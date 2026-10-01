"""The DayZ test box as the MCP layer reads it from a status payload.

FIFO position and queue offer, foreign ports, takeover and protection, and the
session_status blocked_on text. Pure reads; the waits that call the daemon stay
in server.py. Moved out of server.py unchanged (backlog 71fc); server.py
imports every name back, so dayz_mcp.server.<name> is the same object.
"""

from __future__ import annotations

import re
from typing import Any

from dayz_mcp.process_lifecycle import (
    ADOPTION_REVERT_PENDING,
    caller_launched_row,
    caller_may_adopt_ownerless,
    empty_box,
    occupancy_error_fields,
    protection_fields,
    takeover_target_run_id,
)


TAKEOVER_REQUIRED = "takeover_required"


def _box_from_status(status: object) -> dict[str, Any]:
    if not isinstance(status, dict):
        return empty_box(occupied=True)
    box = status.get("box")
    if not isinstance(box, dict):
        return empty_box(occupied=True)
    payload = dict(box)
    if not isinstance(payload.get("runs"), list):
        payload["runs"] = []
    if not isinstance(payload.get("foreign"), list):
        payload["foreign"] = []
    if not isinstance(payload.get("ports_in_use"), list):
        payload["ports_in_use"] = []
    if not isinstance(payload.get("queue"), list):
        payload["queue"] = []
    if payload.get("occupied") is not False:
        payload["occupied"] = bool(payload.get("occupied", True))
    return payload


def _box_session_is(session: object, session_id: str) -> bool:
    if not isinstance(session, str) or not isinstance(session_id, str):
        return False
    return session in {session_id, session_id[:12]}


def _box_head_is(box: dict[str, Any], session_id: str) -> bool:
    queue = box.get("queue")
    if not isinstance(queue, list) or not queue:
        return False
    head = queue[0]
    if not isinstance(head, dict):
        return False
    return _box_session_is(head.get("session"), session_id)


def _box_ready_for(box: dict[str, Any], session_id: str) -> bool:
    if box.get("occupied") is True:
        return False
    return _box_head_is(box, session_id)


_BOX_OFFER_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_BOX_OFFER_MOD = re.compile(r"[A-Za-z0-9@ _.+-]{1,64}")
_BOX_OFFER_STATES = frozenset({
    "STARTING",
    "RUNNING",
    "RUNNING_IDLE",
    "STOPPING",
    "UNRECONCILED",
})
_BOX_OFFER_MOD_CAP = 16


def _offer_run_id(value: object) -> str | None:
    if isinstance(value, str) and _BOX_OFFER_RUN_ID.fullmatch(value):
        return value
    return None


def _offer_state(value: object) -> str | None:
    if isinstance(value, str) and value in _BOX_OFFER_STATES:
        return value
    return None


def _offer_port(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
        return value
    return None


def _offer_age_s(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _offer_mods(raw: object) -> list[str]:
    if isinstance(raw, str):
        items: list[object] = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    kept: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            continue
        if item[0] == " " or item[-1] == " ":
            continue
        if _BOX_OFFER_MOD.fullmatch(item) is None:
            continue
        kept.append(item)
        if len(kept) >= _BOX_OFFER_MOD_CAP:
            break
    return kept


def _first_box_dict(items: object) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def _box_run(box: object, run_id: object) -> dict[str, Any] | None:
    if not isinstance(box, dict) or not isinstance(run_id, str) or not run_id:
        return None
    runs = box.get("runs")
    if not isinstance(runs, list):
        return None
    for item in runs:
        if isinstance(item, dict) and item.get("run_id") == run_id:
            return item
    return None


def _row_is_protected(
    row: dict[str, Any] | None, caller_session: str | None
) -> bool:
    """Ownerless RUNNING_IDLE, or a revert that has not landed. Fail closed.

    A RUNNING row is not this refusal unless its adoption revert is still
    pending. The launcher is not refused. The daemon decides with the full
    session id; this layer only has the public prefix.
    """

    if not isinstance(row, dict):
        return False
    if row.get("use_reason") == ADOPTION_REVERT_PENDING:
        return not caller_launched_row(row, caller_session)
    if row.get("state") != "RUNNING_IDLE":
        return False
    owner = row.get("owner_session")
    if isinstance(owner, str) and owner:
        return False
    return not caller_may_adopt_ownerless(row, caller_session)


def _box_wait_cannot_help(
    box: object,
    caller_session: str | None = None,
    port: int | None = None,
) -> str | None:
    """Return a reason when waiting in the FIFO cannot free the box."""

    if not isinstance(box, dict):
        return None
    if box.get("port_scan_known") is False:
        return "port_scan_unknown"
    session_id = caller_session if isinstance(caller_session, str) else ""
    runs = box.get("runs")
    if isinstance(runs, list) and session_id:
        for occupier in runs:
            if (
                isinstance(occupier, dict)
                and occupier.get("state") in {"RUNNING", "RUNNING_IDLE"}
                and _box_session_is(occupier.get("owner_session"), session_id)
            ):
                return "own_run"
    if box_available_for(box, caller_session=session_id or None)["adopt"] is True:
        return "adopt"
    if isinstance(runs, list):
        for item in runs:
            if isinstance(item, dict) and item.get("state") == "UNRECONCILED":
                return "unreconciled"
    if _port_conflict_fields(box, port).get("reason") == "port_in_use_foreign":
        return "port_in_use_foreign"
    return None


def _occupant_from_box(box: dict[str, Any]) -> tuple[dict[str, Any], float | None]:
    run = _first_box_dict(box.get("runs"))
    if run is not None:
        mods_raw: object = run.get("mods")
        if not isinstance(mods_raw, list):
            mods_raw = run.get("mod")
        occupant = {
            "run_id": _offer_run_id(run.get("run_id")),
            "state": _offer_state(run.get("state")),
            "foreign": False,
            "port": _offer_port(run.get("port")),
            "mods": _offer_mods(mods_raw),
        }
        return occupant, _offer_age_s(run.get("age_s"))
    foreign = _first_box_dict(box.get("foreign"))
    if foreign is not None:
        occupant = {
            "run_id": _offer_run_id(foreign.get("run_id")),
            "state": _offer_state(foreign.get("state")),
            "foreign": True,
            "port": _offer_port(foreign.get("port")),
            "mods": _offer_mods(foreign.get("mods")),
        }
        return occupant, _offer_age_s(foreign.get("age_s"))
    occupant = {
        "run_id": None,
        "state": None,
        "foreign": False,
        "port": None,
        "mods": [],
    }
    return occupant, _offer_age_s(box.get("claimed_s"))


def _box_queue_position(box: dict[str, Any], session_id: str) -> int | None:
    queue = box.get("queue")
    if not isinstance(queue, list) or not session_id:
        return None
    for index, item in enumerate(queue):
        if isinstance(item, dict) and _box_session_is(item.get("session"), session_id):
            return index + 1
    return None


def _box_queue_offer(
    box: object,
    *,
    caller_session: str | None = None,
    port: int | None = None,
) -> dict[str, Any] | None:
    """Actionable FIFO offer for a busy-box rejection or session_status."""

    if not isinstance(box, dict):
        return None
    session_id = caller_session if isinstance(caller_session, str) else ""
    wait_reason = _box_wait_cannot_help(
        box, caller_session=session_id or None, port=port
    )
    if wait_reason is not None and wait_reason != "own_run":
        return None
    if _port_conflict_fields(box, port).get("reason") == "port_in_use_foreign":
        return None
    waiters = 0
    queue = box.get("queue")
    if isinstance(queue, list):
        for item in queue:
            session = item.get("session") if isinstance(item, dict) else None
            if _box_session_is(session, session_id):
                continue
            waiters += 1
    occupant, age_s = _occupant_from_box(box)
    retry: dict[str, Any] = {
        "tool": "dayz_test_run",
        "same_args": True,
    }
    if wait_reason != "own_run":
        retry["on_busy"] = "queue"
    return {
        "position_if_joined": waiters + 1,
        "waiters": waiters,
        "occupant": occupant,
        "occupant_age_s": age_s,
        "retry": retry,
    }


def _attach_queue_offer(
    payload: dict[str, Any],
    box: object,
    *,
    caller_session: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    if payload.get("error_code") in {
        "active_run_exists",
        TAKEOVER_REQUIRED,
        "run_protected",
    }:
        payload["queue_offer"] = _box_queue_offer(
            box, caller_session=caller_session, port=port
        )
    return payload


_DAYZ_GAME_PORT_MIN = 2302
_DAYZ_GAME_PORT_MAX = 2999


def _port_is_dayz_relevant(port: object) -> bool:
    return (
        isinstance(port, int)
        and not isinstance(port, bool)
        and _DAYZ_GAME_PORT_MIN <= port <= _DAYZ_GAME_PORT_MAX
    )


def _foreign_port_number(item: object) -> int | None:
    if isinstance(item, int) and not isinstance(item, bool):
        return item
    if isinstance(item, dict):
        port = item.get("port")
        if isinstance(port, int) and not isinstance(port, bool):
            return port
    return None


def _foreign_ports_contain(foreign_ports: object, port: int) -> bool:
    if not isinstance(foreign_ports, list):
        return False
    return any(_foreign_port_number(item) == port for item in foreign_ports)


def _annotate_foreign_port_list(
    ports: object,
    *,
    relevant_ports: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Normalize a foreign_ports* list to [{port, dayz_relevant}, ...].

    When relevant_ports is set (membership of capture's DayZ-related
    foreign_ports), dayz_relevant follows that set so image-classified
    ports outside 2302-2999 stay coherent with foreign_ports_meta.dayz_related.
    Otherwise fall back to the UDP game-port range alone.
    """
    if not isinstance(ports, list):
        return []
    annotated: list[dict[str, Any]] = []
    for item in ports:
        port = _foreign_port_number(item)
        if port is None:
            continue
        extra = dict(item) if isinstance(item, dict) else {}
        extra.pop("port", None)
        extra["port"] = port
        if relevant_ports is not None:
            extra["dayz_relevant"] = port in relevant_ports
        else:
            extra["dayz_relevant"] = _port_is_dayz_relevant(port)
        annotated.append(extra)
    return annotated


def _annotate_box_foreign_ports(box: dict[str, Any]) -> dict[str, Any]:
    """Flag DayZ-relevant listeners; keep full table under foreign_ports_all.

    Capture already filters foreign_ports to image-or-range DayZ-related
    holders. Annotate both lists from that membership so a DayZ image on
    e.g. 1234 stays dayz_relevant:true and meta dayz_relevant matches
    dayz_related (fb-1432 B1). Range-only marking demoted those ports.
    """
    ports = box.get("foreign_ports")
    relevant_ports: set[int] | None = None
    if isinstance(ports, list):
        relevant_ports = {
            port
            for port in (_foreign_port_number(item) for item in ports)
            if port is not None
        }

    annotated: list[dict[str, Any]] = []
    if isinstance(ports, list):
        annotated = _annotate_foreign_port_list(
            ports, relevant_ports=relevant_ports
        )
        box["foreign_ports"] = annotated

    ports_all = box.get("foreign_ports_all")
    annotated_all: list[dict[str, Any]] | None = None
    if isinstance(ports_all, list):
        annotated_all = _annotate_foreign_port_list(
            ports_all, relevant_ports=relevant_ports
        )
        box["foreign_ports_all"] = annotated_all

    meta = dict(box.get("foreign_ports_meta") or {})
    if isinstance(ports, list):
        meta["count"] = len(annotated)
        meta["dayz_relevant"] = sum(
            1 for item in annotated if item.get("dayz_relevant")
        )
    if annotated_all is not None:
        meta["count_all"] = len(annotated_all)
    box["foreign_ports_meta"] = meta
    return box


def _port_conflict_fields(box: object, port: int | None) -> dict[str, Any]:
    """Diagnosis for an active_run_exists that the box alone cannot explain.

    fb-20260904-114520-6927: a launch refused because the requested port is
    held by a process that is not ours (any image) arrives with a box that
    reads free -- only DayZ images occupy the box -- so the generic hint told
    the caller to wait for a box that was never busy. The held port is in
    foreign_ports; say that, and say when the socket table itself could not be
    read (waiting does not repair that either).
    """
    if not isinstance(box, dict):
        return {}
    if box.get("port_scan_known") is False:
        reason = box.get("port_scan_reason")
        return {
            "reason": reason if isinstance(reason, str) and reason else "port_scan_unknown",
            "hint": (
                "the daemon could not read the host UDP socket table "
                "(psutil/netstat): waiting does not help, restore that first"
            ),
        }
    # Prefer the full table when present (option A); fall back to the default
    # DayZ-related list for older payloads that never emitted foreign_ports_all.
    foreign_ports_all = box.get("foreign_ports_all")
    foreign_ports = (
        foreign_ports_all
        if isinstance(foreign_ports_all, list)
        else box.get("foreign_ports")
    )
    runs = box.get("runs")
    if isinstance(port, int) and _foreign_ports_contain(foreign_ports, port):
        # Any requested port, any image: foreign_ports_all is the socket table
        # minus managed runs. When a run also occupies the box, both blockers
        # are named -- freeing the box does not free this port.
        if isinstance(runs, list) and runs:
            hint = (
                f"the box is busy (see occupied_by_run_id) AND port {port} is held "
                "by a process that is not a managed run: after the box frees, pass "
                "another port= or wait for that holder to exit; waiting for the box "
                "alone does not free the port"
            )
        else:
            hint = (
                f"port {port} is held by a process on this host that is not a "
                "managed run (see session_status.box.foreign_ports_all): pass another "
                "port= or wait for its holder to exit; wait_for_box_s does not "
                "help while the box reads free"
            )
        return {"reason": "port_in_use_foreign", "port": port, "hint": hint}
    return {}


def _apply_takeover_required(
    payload: dict[str, Any],
    box: object,
    *,
    caller_session: str | None = None,
) -> dict[str, Any]:
    if payload.get("reason") == "port_in_use_foreign":
        return payload
    target = takeover_target_run_id(box, caller_session=caller_session)
    if target is None:
        return payload
    row = _box_run(box, target)
    if _row_is_protected(row, caller_session):
        payload["error_code"] = "run_protected"
        notice = protection_fields(row)
        payload["hint"] = notice["hint"]
        payload["use_state"] = notice["use_state"]
        payload["use_reason"] = notice["use_reason"]
        if "retry_after_s" in notice:
            payload["retry_after_s"] = notice["retry_after_s"]
        return payload
    payload["error_code"] = TAKEOVER_REQUIRED
    extra = occupancy_error_fields(box, caller_session=caller_session)
    hint = extra.get("hint")
    if isinstance(hint, str) and hint:
        payload["hint"] = hint
    return payload


def _enrich_active_run_result(
    result: dict[str, Any],
    box: dict[str, Any],
    *,
    caller_session: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    extra = occupancy_error_fields(box, caller_session=caller_session)
    payload = dict(result)
    payload.update(extra)
    payload.update(_port_conflict_fields(box, port))
    payload["run_id"] = None
    payload["status"] = "failed"
    payload["error_code"] = "active_run_exists"
    return _attach_queue_offer(
        _apply_takeover_required(payload, box, caller_session=caller_session),
        box,
        caller_session=caller_session,
        port=port,
    )


# inbox 3997 (fb-20261001-000925-3997). A launch refused as active_run_exists
# although the box read free for this call right before it, and the box read
# after the refusal shows who holds it now (_box_held_by_another): the box was
# taken while the call built, or while it waited for the lease.
BOX_TAKEN_DURING_BUILD = "box_taken_during_build"
BOX_TAKEN_BEFORE_LAUNCH = "box_taken_before_launch"
# What dayz_test_run changes to launch again without rebuilding: build or
# clean starts a build, and pack_only needs one.
BOX_TAKEN_NO_REBUILD = {"build": False, "clean": False, "pack_only": False}
_BOX_TAKEN_HINT = {
    BOX_TAKEN_DURING_BUILD: (
        "the box was taken while this call was building: the build completed, "
        "then the launch found the box claimed by a FIFO waiter, or occupied "
        "by another run or a DayZ this daemon does not manage. Call "
        "dayz_test_run again with build=false and on_busy='queue' to wait in "
        "the box FIFO without rebuilding"
    ),
    BOX_TAKEN_BEFORE_LAUNCH: (
        "the box was taken while this call waited for its launch: it read free "
        "when the call started, and the launch found it claimed by a FIFO "
        "waiter, or occupied by another run or a DayZ this daemon does not "
        "manage. Call dayz_test_run again with on_busy='queue' to wait in the "
        "box FIFO"
    ),
}


def _box_was_free_for(box: object, caller_session: str | None) -> bool:
    """Whether the box this call read right before its launch was free for it.

    Free is read free, or occupied only by this caller's own FIFO claim (the
    on_busy="queue" path claims the box before it launches). Anything else is
    not free, unreadable included: a launch refused over a box that was already
    busy when the call started is not a box taken during the call.
    """
    if (
        not isinstance(box, dict)
        or box.get("port_scan_known") is False
        or box.get("scan_known") is False
    ):
        return False
    if box.get("occupied") is False:
        return True
    if box.get("occupied") is not True:
        return False
    runs = box.get("runs")
    foreign = box.get("foreign")
    if not isinstance(runs, list) or runs or not isinstance(foreign, list) or foreign:
        return False
    claimed = box.get("claimed_s")
    if isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
        return False
    return isinstance(caller_session, str) and _box_head_is(box, caller_session)


def _box_held_by_another(box: object, caller_session: str | None) -> bool:
    """Positive proof that someone other than this caller holds the box now.

    A registered run this caller does not own (or, ownerless, did not launch),
    the box claim of another session, or a DayZ this daemon does not manage.
    A box that reads free, holds only this caller's own claim, shows a held
    port and no occupant, or cannot be read proves nothing: what refused the
    launch, a port holder that has exited for one, may not be observable any
    more, and is then not called a lost box.
    """
    if not isinstance(box, dict):
        return False
    foreign = box.get("foreign")
    if isinstance(foreign, list) and any(isinstance(item, dict) for item in foreign):
        return True
    caller = caller_session if isinstance(caller_session, str) and caller_session else None
    runs = box.get("runs")
    if isinstance(runs, list):
        for item in runs:
            if not isinstance(item, dict):
                continue
            run_id = item.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                continue
            owner = item.get("owner_session")
            if isinstance(owner, str) and owner:
                if caller is None or not _box_session_is(owner, caller):
                    return True
            elif caller is None or not caller_launched_row(item, caller):
                return True
    claimed = box.get("claimed_s")
    if isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
        return False
    head = _first_box_dict(box.get("queue"))
    session = head.get("session") if head is not None else None
    if not isinstance(session, str) or not session:
        return False
    return caller is None or not _box_session_is(session, caller)


def _box_holder_session(
    box: object, caller_session: str | None = None
) -> str | None:
    """The public session that holds the box, or None when no session does.

    The run occupancy_error_fields names: its owner, else the session that
    launched it (an ownerless run). With no run registered yet, the FIFO head
    that holds the box claim: the claimant before it launches. The caller's
    own claim (still held while its answer is built) names nobody, and a
    foreign DayZ has no session.
    """
    if not isinstance(box, dict):
        return None
    runs = box.get("runs")
    if isinstance(runs, list):
        for item in runs:
            if not isinstance(item, dict):
                continue
            run_id = item.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                continue
            owner = item.get("owner_session")
            if isinstance(owner, str) and owner:
                return owner[:12]
            launched = item.get("launched_by")
            session = launched.get("session") if isinstance(launched, dict) else None
            return session[:12] if isinstance(session, str) and session else None
    claimed = box.get("claimed_s")
    if isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
        return None
    head = _first_box_dict(box.get("queue"))
    session = head.get("session") if head is not None else None
    if not isinstance(session, str) or not session:
        return None
    if isinstance(caller_session, str) and _box_session_is(session, caller_session):
        return None
    return session[:12]


def _box_taken_result(
    result: dict[str, Any],
    box: dict[str, Any],
    *,
    built: bool,
    caller_session: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    """active_run_exists for a box proven taken after this call read it free.

    Not takeover_required: the run that holds the box now won it while this
    call built or waited for its launch, so the answer offers the FIFO, not
    an eviction (H11). The holder's session and the queue offer come from the
    box read after the refusal. A port held by a process that is not a
    managed run keeps its own reason and hint, since waiting does not free it.
    A run id is kept only when the worker could not confirm its stop, so the
    caller can stop it.
    """
    payload = dict(result)
    payload.update(occupancy_error_fields(box, caller_session=caller_session))
    conflict = _port_conflict_fields(box, port)
    payload.update(conflict)
    if payload.get("cleanup_degraded") is not True:
        payload["run_id"] = None
    payload["status"] = "failed"
    payload["error_code"] = "active_run_exists"
    payload["occupied_by_session"] = _box_holder_session(box, caller_session)
    reason = BOX_TAKEN_DURING_BUILD if built else BOX_TAKEN_BEFORE_LAUNCH
    if not conflict.get("reason"):
        payload["reason"] = reason
        payload["hint"] = _BOX_TAKEN_HINT[reason]
    payload = _attach_queue_offer(
        payload, box, caller_session=caller_session, port=port
    )
    offer = payload.get("queue_offer")
    if built and isinstance(offer, dict) and isinstance(offer.get("retry"), dict):
        offer["retry"].update(BOX_TAKEN_NO_REBUILD)
    return payload


ADOPT_BLOCKED_ON = (
    "DayZ test box has an ownerless RUNNING_IDLE run; next: call "
    "session_acquire_wait(purpose=...) to adopt it. dayz_test_run wait_for_box_s "
    "is for a new launch, not this box."
)


def _ownerless_idle_runs(box: dict[str, Any]) -> list[dict[str, Any]]:
    runs = box.get("runs")
    if not isinstance(runs, list):
        return []
    idle: list[dict[str, Any]] = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        if item.get("state") != "RUNNING_IDLE":
            continue
        owner = item.get("owner_session")
        if owner is None or owner == "":
            idle.append(item)
    return idle


def box_available_for(
    box: object, caller_session: str | None = None
) -> dict[str, bool]:
    """MCP-only: new launch, or adopt for the launcher or an abandoned run.

    caller_session None cannot prove the launcher, so adopt is only abandoned.
    """

    unavailable = {"new_launch": False, "adopt": False}
    if not isinstance(box, dict):
        return unavailable
    if box.get("port_scan_known") is False:
        return unavailable
    if box.get("occupied") is not True:
        return {"new_launch": True, "adopt": False}
    foreign = box.get("foreign")
    if not isinstance(foreign, list) or foreign:
        return unavailable
    idle = _ownerless_idle_runs(box)
    if len(idle) == 1 and caller_may_adopt_ownerless(idle[0], caller_session):
        return {"new_launch": False, "adopt": True}
    return unavailable


def _protection_blocked_on(row: dict[str, Any]) -> str:
    """What protects the run, and until when, for a caller who cannot adopt."""

    notice = protection_fields(row)
    state = notice.get("use_state") or "unclassified"
    reason = notice.get("use_reason")
    text = f"DayZ test box is protected ({state}"
    if isinstance(reason, str) and reason:
        text += f", {reason}"
    text += ")"
    retry = notice.get("retry_after_s")
    if isinstance(retry, (int, float)) and not isinstance(retry, bool):
        text += f"; it can be abandoned in {retry}s"
    else:
        text += "; no time is given for when it can be abandoned"
    text += ". Waiting in the box FIFO does not adopt it."
    return text


def _session_status_blocked_on(
    status: dict[str, Any], caller_session: str | None = None
) -> str | None:
    """Return the next queue a caller should join, if a resource is busy."""

    if isinstance(status.get("owner"), dict):
        return (
            "session lease; next: call session_acquire_wait(purpose=...) "
            "to join the lease FIFO"
        )
    box = status.get("box")
    if isinstance(box, dict) and box.get("port_scan_known") is False:
        # The box reads occupied because the daemon could not read or
        # attribute the host UDP socket table; the FIFO does not repair that.
        reason = box.get("port_scan_reason")
        reason_text = reason if isinstance(reason, str) and reason else "port_scan_unknown"
        return (
            f"DayZ test box, {reason_text}; next: restore the daemon's view of the "
            "host UDP socket table (psutil/netstat, process attribution) -- "
            "wait_for_box_s does not help"
        )
    if isinstance(box, dict):
        runs = box.get("runs")
        if isinstance(runs, list):
            for item in runs:
                if (
                    isinstance(item, dict)
                    and item.get("use_reason") == ADOPTION_REVERT_PENDING
                    and _row_is_protected(item, caller_session)
                ):
                    return _protection_blocked_on(item)
    if isinstance(box, dict) and box_available_for(box, caller_session)["adopt"] is True:
        return ADOPT_BLOCKED_ON
    if isinstance(box, dict):
        idle = _ownerless_idle_runs(box)
        if len(idle) == 1 and _row_is_protected(idle[0], caller_session):
            return _protection_blocked_on(idle[0])
    if isinstance(box, dict) and box.get("occupied") is True:
        return (
            'DayZ test box; next: call dayz_test_run(..., on_busy="queue") '
            "to join the box FIFO"
        )
    return None
