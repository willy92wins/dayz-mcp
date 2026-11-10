"""Who holds a DayZ that another dayz-mcp instance launched on this box.

fb-20261028-001246-6330 and fb-20261028-001458-95d7. Two daemons (the default
instance and an ``--instance`` one) can share one Windows box. Each sees the
other's DayZ only as a foreign process, so its refusal could not say whose run
holds the box or why. While a run of this daemon lives, one small record next
to ``box-admission.lock`` names it: instance, holder session, purpose, run id,
start time, pids and daemon pid. The end of the run removes it.

A daemon that refuses or queues because of a foreign DayZ names a holder only
when exactly one record of another instance accounts for every foreign pid it
sees, with creation times that agree wherever both sides know them. Anything
else (no record, unreadable, oversize, stale, other pids, its own instance)
names nobody and the answer stays what it was: a wrong name is worse than
none. Holder and purpose are text from another session. They lose control and
format characters, are capped, and travel only as JSON fields, never spliced
into an instruction.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable, Mapping

from dayz_mcp.runtime_state import atomic_write_json
from dayz_mcp.server_cli import (
    bound_instance_token,
    registration_name,
    shared_root,
    validate_instance_token,
)


RECORD_VERSION = 1
RECORD_PREFIX = "box-holder."
RECORD_SUFFIX = ".json"
MAX_RECORD_BYTES = 4096
MAX_TEXT_CHARS = 120
MAX_PROCESSES = 8
MAX_RECORDS = 16
CREATION_TOLERANCE_S = 1.0
PUBLIC_FIELDS = ("instance", "holder", "purpose", "run_id", "since")

_RECORD_KEYS = frozenset(
    {"version", "daemon_pid", "processes"} | set(PUBLIC_FIELDS)
)
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SINCE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
# One daemon writes and clears its own record; publish and clear never race.
_write_lock = threading.Lock()
# A record written by this very process is never another instance's run.
_process_id = os.getpid


def clean_text(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    """Untrusted text as one capped line, without control or format characters."""
    if not isinstance(value, str):
        return ""
    kept: list[str] = []
    for char in value:
        if char.isspace():
            kept.append(" ")
        elif not unicodedata.category(char).startswith("C"):
            kept.append(char)
    return " ".join("".join(kept).split())[:limit]


def own_name() -> str:
    """This daemon's registration name, from the instance it was bound to."""
    return registration_name(bound_instance_token())


def valid_name(value: object) -> bool:
    """A registration name some instance can have, by the server_cli rules."""
    if not isinstance(value, str):
        return False
    base = registration_name(None)
    if value == base:
        return True
    if not value.startswith(base) or len(value) <= len(base) + 1:
        return False
    token = value[len(base) + 1 :]
    try:
        canonical = validate_instance_token(token)
    except ValueError:
        return False
    return canonical is not None and registration_name(canonical) == value


def record_path(root: Path, name: str) -> Path:
    return root / f"{RECORD_PREFIX}{name}{RECORD_SUFFIX}"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_pid(value: object) -> bool:
    return _is_int(value) and value > 0


def _valid_epoch(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def publish(
    *,
    run_id: str,
    holder: str,
    purpose: str,
    processes: Mapping[int, float | None],
) -> bool:
    """Write this instance's record for a live run. Best effort: False on failure."""
    try:
        name = own_name()
        if not valid_name(name) or not isinstance(run_id, str):
            return False
        if not _RUN_ID.fullmatch(run_id):
            return False
        rows: list[dict[str, object]] = []
        for pid, created in processes.items():
            if len(rows) >= MAX_PROCESSES:
                break
            if _valid_pid(pid):
                stamp = float(created) if _valid_epoch(created) else None
                rows.append({"pid": pid, "created": stamp})
        holder_text = clean_text(holder)
        if not rows or not holder_text:
            return False
        stamps = [row["created"] for row in rows if row["created"] is not None]
        started = min(stamps) if stamps else time.time()
        record = {
            "version": RECORD_VERSION,
            "instance": name,
            "holder": holder_text,
            "purpose": clean_text(purpose),
            "run_id": run_id,
            "processes": rows,
            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "daemon_pid": _process_id(),
        }
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) + 1 > MAX_RECORD_BYTES:
            return False
        path = record_path(shared_root(), name)
        with _write_lock:
            atomic_write_json(path, record)
        return True
    except Exception:
        return False


def clear(run_id: str) -> bool:
    """Remove this instance's record while it still names run_id."""
    try:
        path = record_path(shared_root(), own_name())
        with _write_lock:
            data = _read_json(path)
            if data is None or data.get("run_id") != run_id:
                return False
            path.unlink()
        return True
    except Exception:
        return False


def _read_json(path: Path) -> dict | None:
    """A small regular file holding one JSON object, or None."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if not stat.S_ISREG(info.st_mode) or reparse or info.st_size > MAX_RECORD_BYTES:
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_RECORD_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_RECORD_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record(data: dict, name: str) -> dict | None:
    """The record a file named for ``name`` holds, validated, or None."""
    if set(data) != _RECORD_KEYS:
        return None
    version = data["version"]
    if not _is_int(version) or version != RECORD_VERSION:
        return None
    if data["instance"] != name or not _valid_pid(data["daemon_pid"]):
        return None
    run_id, since = data["run_id"], data["since"]
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        return None
    if not isinstance(since, str) or not _SINCE.fullmatch(since):
        return None
    holder, purpose = data["holder"], data["purpose"]
    if not isinstance(holder, str) or not isinstance(purpose, str):
        return None
    holder, purpose = clean_text(holder), clean_text(purpose)
    if not holder:
        return None
    rows = data["processes"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PROCESSES:
        return None
    created: dict[int, float | None] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"pid", "created"}:
            return None
        pid, stamp = row["pid"], row["created"]
        if not _valid_pid(pid) or pid in created:
            return None
        if stamp is not None and not _valid_epoch(stamp):
            return None
        created[pid] = float(stamp) if stamp is not None else None
    return {
        "instance": name,
        "holder": holder,
        "purpose": purpose,
        "run_id": run_id,
        "since": since,
        "daemon_pid": data["daemon_pid"],
        "processes": created,
    }


def attribute(
    observed: Mapping[int, float | None],
    *,
    created_of: Callable[[int], float | None] | None = None,
) -> dict[str, str] | None:
    """The one other instance's run that accounts for these foreign pids, or None.

    ``observed`` maps each foreign pid this daemon sees to its creation time
    (epoch seconds) when the scan gave one. ``created_of`` reads a creation
    time the scan did not give; it is asked only for pids a record claims.
    """
    pids = set(observed)
    if not pids:
        return None
    try:
        own = own_name()
        paths = sorted(shared_root().glob(f"{RECORD_PREFIX}*{RECORD_SUFFIX}"))
    except Exception:
        return None
    if len(paths) > MAX_RECORDS:
        return None
    matches: list[dict] = []
    for path in paths:
        name = path.name[len(RECORD_PREFIX) : -len(RECORD_SUFFIX)]
        if name == own or not valid_name(name):
            continue
        data = _read_json(path)
        record = _record(data, name) if data is not None else None
        if record is None or record["daemon_pid"] == _process_id():
            continue
        if pids <= set(record["processes"]):
            matches.append(record)
    if len(matches) != 1:
        return None
    record = matches[0]
    for pid in sorted(pids):
        published = record["processes"][pid]
        if published is None:
            continue
        seen = observed.get(pid)
        if seen is None and created_of is not None:
            try:
                seen = created_of(pid)
            except Exception:
                seen = None
        if seen is None:
            continue
        if not _valid_epoch(seen) or abs(float(seen) - published) > CREATION_TOLERANCE_S:
            return None
    return {key: record[key] for key in PUBLIC_FIELDS}


def public_holder(value: object) -> dict[str, str] | None:
    """A foreign_holder fit to pass on, or None: the MCP side checks it again."""
    if not isinstance(value, Mapping) or set(value) != set(PUBLIC_FIELDS):
        return None
    instance, run_id, since = value["instance"], value["run_id"], value["since"]
    holder, purpose = value["holder"], value["purpose"]
    if not valid_name(instance):
        return None
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        return None
    if not isinstance(since, str) or not _SINCE.fullmatch(since):
        return None
    if not isinstance(holder, str) or not isinstance(purpose, str):
        return None
    holder = clean_text(holder)
    if not holder:
        return None
    return {
        "instance": instance,
        "holder": holder,
        "purpose": clean_text(purpose),
        "run_id": run_id,
        "since": since,
    }
