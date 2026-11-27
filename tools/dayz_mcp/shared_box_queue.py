"""One shared FIFO for the physical box.

The document ``box-queue.json`` under ``server_cli.shared_root`` is the only
queue. Daemons keep their own ticket ids in RAM and mutate this file under
``box-queue.lock``. An unreadable document occupies the box and is not
rewritten.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Callable

from dayz_mcp import byte_lock
from dayz_mcp.runtime_state import atomic_write_json

# Bound at import: the lock deadline runs on the real clock even where a
# caller patches time.monotonic for its own deadline.
_monotonic = time.monotonic


WAITING_TTL_S = 120.0
CLAIM_TTL_S = 600.0
LOCK_WAIT_S = 5.0
DOCUMENT_NAME = "box-queue.json"
LOCK_NAME = "box-queue.lock"
VERSION = 1

_ENTRY_KEYS = frozenset(
    {
        "ticket",
        "kind",
        "instance",
        "holder",
        "purpose",
        "session_id",
        "owner_pid",
        "owner_created",
        "state",
        "run_id",
        "created_at",
        "touched_at",
        "claimed_at",
        "ttl_s",
    }
)


def _process_is_test() -> bool:
    """True when this process is a test runner.

    ``server_cli`` is sealed, so the queue does not ask it. Production does
    not import pytest or unittest and does not launch via ``python -m unittest``.
    """

    if os.environ.get("PYTEST_CURRENT_TEST", "").strip():
        return True
    if "pytest" in sys.modules or "unittest" in sys.modules:
        return True
    if sys.argv:
        head = sys.argv[0].replace("\\", "/").casefold()
        if " -m unittest" in head:
            return True
    return False


def _refuse_real_profile(root: Path) -> None:
    """A test process must not write the real ``DayZ_MCP_shared`` profile."""

    if not _process_is_test():
        return
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        return
    real = Path(local) / "DayZ_MCP_shared"
    try:
        same = os.path.normcase(str(Path(root).resolve())) == os.path.normcase(
            str(real.resolve())
        )
    except OSError:
        same = os.path.normcase(str(root)) == os.path.normcase(str(real))
    if same:
        raise OSError("shared_root_uses_real_profile")


def queue_root() -> Path:
    """Directory of the shared document. ``server_cli.shared_root`` decides it."""

    from dayz_mcp.server_cli import shared_root

    root = shared_root()
    _refuse_real_profile(root)
    return root


def document_path(root: Path | None = None) -> Path:
    return (queue_root() if root is None else root) / DOCUMENT_NAME


def lock_path(root: Path | None = None) -> Path:
    return (queue_root() if root is None else root) / LOCK_NAME


def empty_document() -> dict:
    return {"version": VERSION, "entries": []}


def parse_document(text: str) -> dict | None:
    """Return the document, or None when it must not be rewritten."""

    try:
        data = json.loads(
            text,
            parse_constant=lambda _name: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "entries"}
        or data.get("version") != VERSION
        or not isinstance(data.get("entries"), list)
    ):
        return None
    entries: list[dict] = []
    for item in data["entries"]:
        if not isinstance(item, dict) or set(item) != _ENTRY_KEYS:
            return None
        if not _entry_sane(item):
            return None
        entries.append(dict(item))
    tickets = [item["ticket"] for item in entries]
    if len(tickets) != len(set(tickets)):
        return None
    for index, item in enumerate(entries):
        if item.get("state") == "claimed" and (
            index != 0 or item.get("claimed_at") is None
        ):
            return None
    return {"version": VERSION, "entries": entries}


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _entry_sane(item: dict) -> bool:
    if not isinstance(item.get("ticket"), str) or not item["ticket"]:
        return False
    if item.get("kind") not in {"mcp", "external"}:
        return False
    if not isinstance(item.get("instance"), str) or not item["instance"]:
        return False
    if not isinstance(item.get("holder"), str) or not item["holder"]:
        return False
    if not isinstance(item.get("purpose"), str) or not item["purpose"].strip():
        return False
    if not isinstance(item.get("session_id"), str):
        return False
    if not isinstance(item.get("owner_pid"), int) or isinstance(item.get("owner_pid"), bool):
        return False
    if item["owner_pid"] <= 0:
        return False
    created = item.get("owner_created")
    if not isinstance(created, str) or not created:
        return False
    if item.get("state") not in {"waiting", "claimed"}:
        return False
    run_id = item.get("run_id")
    if run_id is not None and not isinstance(run_id, str):
        return False
    if not _number(item.get("created_at")) or not _number(item.get("touched_at")):
        return False
    claimed_at = item.get("claimed_at")
    if claimed_at is not None and not _number(claimed_at):
        return False
    ttl = item.get("ttl_s")
    if item["state"] == "waiting" and ttl != WAITING_TTL_S:
        return False
    if item["state"] == "claimed" and ttl != CLAIM_TTL_S:
        return False
    return True


def default_snapshot(pid: int) -> dict:
    from dayz_mcp.native_process_guard import NativeProcessGuard

    return NativeProcessGuard().snapshot(pid)


def owner_departed(entry: dict, snapshot: dict) -> bool:
    """True when the owner pid is gone or is a different process creation."""

    error = snapshot.get("error") if isinstance(snapshot, dict) else None
    if error == "identity_unavailable":
        return False
    if error == "process_not_found":
        return True
    if not isinstance(snapshot, dict) or snapshot.get("identity_complete") is not True:
        return False
    created = snapshot.get("creation_time_utc")
    if not isinstance(created, str) or not created:
        return False
    return created != entry.get("owner_created")


def purge_entries(
    entries: list[dict],
    now: float,
    snapshot_fn: Callable[[int], dict],
) -> tuple[list[dict], list[tuple[str, str]]]:
    kept: list[dict] = []
    removed: list[tuple[str, str]] = []
    for entry in entries:
        ttl = float(entry["ttl_s"])
        if now - float(entry["touched_at"]) >= ttl:
            removed.append((str(entry["session_id"]), str(entry["ticket"])))
            continue
        try:
            snap = snapshot_fn(int(entry["owner_pid"]))
        except Exception:
            snap = {"error": "identity_unavailable"}
        if owner_departed(entry, snap):
            removed.append((str(entry["session_id"]), str(entry["ticket"])))
            continue
        kept.append(entry)
    return kept, removed


def project_queue(entries: list[dict], now: float) -> list[dict]:
    rows: list[dict] = []
    for entry in entries:
        session = entry.get("session_id") or ""
        public = session[:12] if isinstance(session, str) else ""
        rows.append(
            {
                "session": public,
                "instance": entry.get("instance"),
                "holder": entry.get("holder"),
                "purpose": entry.get("purpose"),
                "waiting_s": max(0.0, now - float(entry["created_at"])),
            }
        )
    return rows


def admission(
    entries: list[dict],
    session_id: str,
    ticket: object,
    now: float,
) -> str | None:
    """None when the start may proceed. Otherwise an audit reason.

    A start with a ticket must be the unexpired claimed head. A start without
    a ticket is an immediate start: it passes only while the document is empty.
    The daemon binds a claimed ticket onto the session before the sealed
    worker runs, and passes that ticket here. The same session is not a bypass.
    """

    supplied = ticket if isinstance(ticket, str) and ticket else None
    if supplied is not None:
        if not entries or entries[0].get("ticket") != supplied:
            return "box_queue_ahead"
        head = entries[0]
        if head.get("session_id") != session_id or head.get("state") != "claimed":
            return "box_queue_ahead"
        if now - float(head["touched_at"]) >= CLAIM_TTL_S:
            return "box_claim_lost"
        return None
    if not entries:
        return None
    head = entries[0]
    if head.get("state") == "claimed" and head.get("session_id") != session_id:
        return "box_claimed"
    return "box_queue_ahead"


def queue_occupies_caller(entries: list[dict], session_id: str) -> bool:
    """Plan rule for ``box.occupied`` from the queue alone."""

    if not entries:
        return False
    for entry in entries:
        if entry.get("state") == "claimed" and entry.get("session_id") != session_id:
            return True
    head = entries[0]
    return head.get("session_id") != session_id


def external_holder_payload(entries: list[dict]) -> dict | None:
    for entry in entries:
        if entry.get("kind") != "external" or entry.get("state") != "claimed":
            continue
        created = float(entry["created_at"])
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created))
        return {
            "instance": "external",
            "holder": entry.get("holder") or "",
            "purpose": entry.get("purpose") or "",
            "run_id": entry.get("run_id"),
            "since": since,
        }
    return None


class QueueUnreadable(OSError):
    def __init__(self) -> None:
        super().__init__("box_queue_unreadable")


def _read(path: Path) -> dict | None:
    """Missing file is an empty document. Any other read failure is unreadable."""

    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return empty_document()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return parse_document(text)


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, document)


class _Lock:
    def __init__(self, root: Path, wait_s: float) -> None:
        self.root = root
        self.wait_s = wait_s
        self.descriptor: int | None = None

    def __enter__(self) -> _Lock:
        from dayz_mcp.server_cli import open_lock_file

        _refuse_real_profile(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / LOCK_NAME
        descriptor = open_lock_file(path)
        self.descriptor = descriptor
        deadline = _monotonic() + self.wait_s
        try:
            while True:
                try:
                    if os.fstat(descriptor).st_size == 0:
                        os.write(descriptor, b"\0")
                    byte_lock.try_lock(descriptor, 0)
                    return self
                except OSError:
                    if _monotonic() >= deadline:
                        raise TimeoutError("box_queue_lock_timeout")
                    time.sleep(0.01)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self.descriptor = None
            raise

    def __exit__(self, *_) -> None:
        descriptor = self.descriptor
        self.descriptor = None
        if descriptor is None:
            return
        try:
            byte_lock.unlock(descriptor, 0)
        except OSError:
            pass
        finally:
            os.close(descriptor)


def locked_update(
    mutator: Callable[[dict], tuple[dict, dict]],
    *,
    now: float,
    snapshot_fn: Callable[[int], dict] | None = None,
    root: Path | None = None,
    wait_s: float = LOCK_WAIT_S,
) -> dict:
    """Purge, then let ``mutator`` rewrite the document under the lock.

    ``mutator`` receives a readable document and returns ``(document, result)``.
    A false ``result['write']`` skips the replace. Unreadable input returns
    ``{'unreadable': True}`` and does not write.
    """

    snap = snapshot_fn or default_snapshot
    base = queue_root() if root is None else root
    with _Lock(base, wait_s):
        path = base / DOCUMENT_NAME
        document = _read(path)
        if document is None:
            return {"unreadable": True, "entries": [], "removed": []}
        entries, removed = purge_entries(list(document["entries"]), now, snap)
        document = {"version": VERSION, "entries": entries}
        updated, result = mutator(document)
        result = dict(result)
        # ``left`` is what this call removed. ``removed`` also carries the
        # TTL and dead-PID purges, which belong to other sessions.
        left = list(result.get("removed") or [])
        result["left"] = left
        result["removed"] = list(removed) + left
        if result.get("write", True):
            _write(path, updated)
        elif removed:
            _write(path, document)
        result["entries"] = list(updated["entries"])
        result["unreadable"] = False
        return result


def _self_owner(snapshot_fn: Callable[[int], dict]) -> tuple[int, str]:
    pid = os.getpid()
    snap = snapshot_fn(pid)
    created = snap.get("creation_time_utc") if isinstance(snap, dict) else None
    if not isinstance(created, str) or not created:
        raise QueueUnreadable()
    return pid, created


def read_view(
    *,
    now: float,
    session_id: str = "",
    snapshot_fn: Callable[[int], dict] | None = None,
    root: Path | None = None,
) -> dict:
    def mutator(document: dict) -> tuple[dict, dict]:
        entries = document["entries"]
        claimed = next((item for item in entries if item.get("state") == "claimed"), None)
        claimed_s = None
        if claimed is not None and _number(claimed.get("claimed_at")):
            claimed_s = max(0.0, now - float(claimed["claimed_at"]))
        return document, {
            "write": False,
            "queue": project_queue(entries, now),
            "claimed": claimed is not None,
            "claimed_s": claimed_s,
            "occupies": queue_occupies_caller(entries, session_id),
            "external_holder": external_holder_payload(entries),
            "blocked": bool(entries)
            and admission(entries, session_id, None, now) is not None,
        }

    try:
        return locked_update(mutator, now=now, snapshot_fn=snapshot_fn, root=root)
    except (TimeoutError, OSError):
        return {"unreadable": True, "entries": [], "removed": [], "queue": []}


def touch(
    *,
    now: float,
    session_id: str,
    holder: str,
    purpose: str,
    instance: str,
    ticket: str | None,
    new_ticket: str | None,
    done: bool,
    claim: bool,
    kind: str = "mcp",
    run_id: str | None = None,
    capacity: int = 64,
    snapshot_fn: Callable[[int], dict] | None = None,
    root: Path | None = None,
) -> dict:
    snap = snapshot_fn or default_snapshot

    def mutator(document: dict) -> tuple[dict, dict]:
        entries = list(document["entries"])
        removed: list[tuple[str, str]] = []
        if done:
            entries, gone = _leave(entries, session_id, ticket)
            removed.extend(gone)
            return {"version": VERSION, "entries": entries}, {
                "removed": removed,
                "response": {"box_ticket": None},
            }
        existing = None
        if ticket:
            for item in entries:
                if item["ticket"] == ticket and item["session_id"] == session_id:
                    existing = item
                    break
            if existing is None:
                return {"version": VERSION, "entries": entries}, {
                    "write": False,
                    "response": {
                        "box_ticket": None,
                        "box_claimed": False,
                        "box_wait_error": "box_ticket_invalid",
                    },
                }
        if existing is None:
            if len(entries) >= capacity:
                return {"version": VERSION, "entries": entries}, {
                    "write": False,
                    "response": {"box_ticket": None, "box_wait_error": "queue_full"},
                }
            try:
                pid, created = _self_owner(snap)
            except QueueUnreadable:
                return {"version": VERSION, "entries": entries}, {
                    "write": False,
                    "response": {
                        "box_ticket": None,
                        "box_wait_error": "box_queue_unreadable",
                    },
                }
            minted = new_ticket or ""
            existing = {
                "ticket": minted,
                "kind": kind,
                "instance": instance,
                "holder": holder,
                "purpose": purpose,
                "session_id": session_id,
                "owner_pid": pid,
                "owner_created": created,
                "state": "waiting",
                "run_id": run_id,
                "created_at": now,
                "touched_at": now,
                "claimed_at": None,
                "ttl_s": WAITING_TTL_S,
            }
            entries.append(existing)
        else:
            existing["touched_at"] = now
        if claim and entries and entries[0] is existing:
            if existing.get("state") != "claimed":
                existing["claimed_at"] = now
            existing["state"] = "claimed"
            existing["ttl_s"] = CLAIM_TTL_S
        position = entries.index(existing) + 1
        claimed = (
            existing.get("state") == "claimed" and entries[0] is existing
        )
        return {"version": VERSION, "entries": entries}, {
            "removed": removed,
            "response": {
                "box_ticket": existing["ticket"],
                "box_position": position,
                "box_claimed": claimed,
            },
        }

    try:
        result = locked_update(mutator, now=now, snapshot_fn=snap, root=root)
    except TimeoutError as error:
        raise QueueUnreadable() from error
    if result.get("unreadable"):
        return {
            "unreadable": True,
            "removed": [],
            "response": {"box_ticket": None, "box_wait_error": "box_queue_unreadable"},
        }
    return result


def _leave(
    entries: list[dict], session_id: str, ticket: object
) -> tuple[list[dict], list[tuple[str, str]]]:
    removed: list[tuple[str, str]] = []
    if ticket in (None, ""):
        live = [item for item in entries if item["session_id"] == session_id]
        claiming = any(
            item["session_id"] == session_id and item["state"] == "claimed"
            for item in entries
        )
        if len(live) > 1 or (live and claiming):
            return entries, []
        kept = []
        for item in entries:
            if item["session_id"] == session_id:
                removed.append((session_id, item["ticket"]))
                continue
            kept.append(item)
        return kept, removed
    kept = []
    for item in entries:
        if item["ticket"] == ticket and item["session_id"] == session_id:
            removed.append((session_id, item["ticket"]))
            continue
        kept.append(item)
    return kept, removed


def leave_session(
    session_id: str,
    *,
    now: float,
    snapshot_fn: Callable[[int], dict] | None = None,
    root: Path | None = None,
) -> dict:
    def mutator(document: dict) -> tuple[dict, dict]:
        kept = []
        removed = []
        for item in document["entries"]:
            if item["session_id"] == session_id:
                removed.append((session_id, item["ticket"]))
                continue
            kept.append(item)
        return {"version": VERSION, "entries": kept}, {"removed": removed}

    try:
        return locked_update(mutator, now=now, snapshot_fn=snapshot_fn, root=root)
    except (TimeoutError, OSError):
        return {"unreadable": True, "removed": []}


def bind_run(
    session_id: str,
    ticket: str | None,
    run_id: str,
    *,
    now: float,
    snapshot_fn: Callable[[int], dict] | None = None,
    root: Path | None = None,
) -> str | None:
    """Attach ``run_id`` to the admitted head. Return an audit reason or None."""

    def mutator(document: dict) -> tuple[dict, dict]:
        entries = document["entries"]
        reason = admission(entries, session_id, ticket, now)
        if reason is not None:
            return document, {"write": False, "reason": reason}
        if not entries:
            return document, {"write": False, "reason": None}
        head = entries[0]
        if ticket and head.get("ticket") != ticket:
            return document, {"write": False, "reason": "box_queue_ahead"}
        if head.get("session_id") != session_id or head.get("state") != "claimed":
            return document, {"write": False, "reason": "box_claim_lost"}
        head["run_id"] = run_id
        head["touched_at"] = now
        return {"version": VERSION, "entries": entries}, {"reason": None}

    try:
        result = locked_update(mutator, now=now, snapshot_fn=snapshot_fn, root=root)
    except (TimeoutError, OSError):
        return "box_queue_unreadable"
    if result.get("unreadable"):
        return "box_queue_unreadable"
    return result.get("reason")


def admit(
    session_id: str,
    ticket: object,
    *,
    now: float,
    snapshot_fn: Callable[[int], dict] | None = None,
    root: Path | None = None,
) -> str | None:
    def mutator(document: dict) -> tuple[dict, dict]:
        reason = admission(document["entries"], session_id, ticket, now)
        return document, {"write": False, "reason": reason}

    try:
        result = locked_update(mutator, now=now, snapshot_fn=snapshot_fn, root=root)
    except (TimeoutError, OSError):
        return "box_queue_unreadable"
    if result.get("unreadable"):
        return "box_queue_unreadable"
    return result.get("reason")


def claim_refresh_keeps(payload: object) -> bool:
    """The claim heartbeat keeps the launch only while the answer says so."""

    if not isinstance(payload, dict):
        return True
    error = payload.get("box_wait_error")
    if error in {"box_ticket_invalid", "box_wait_cancelled"}:
        return False
    if payload.get("box_ticket") is None and error:
        return False
    if "box_claimed" not in payload:
        return True
    return payload.get("box_claimed") is True


def reset_empty(reason: str, *, root: Path | None = None) -> None:
    """Replace an unreadable document with an empty queue and leave an audit."""

    base = queue_root() if root is None else root
    with _Lock(base, LOCK_WAIT_S):
        _write(base / DOCUMENT_NAME, empty_document())
        audit = {
            "event": "box_queue_reset",
            "reason": reason,
            "document": DOCUMENT_NAME,
        }
        atomic_write_json(base / "box-queue-reset.json", audit)
