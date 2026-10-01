"""Reply shaping for world reads: the world_time clock and the entities_query rows.

Moved out of server.py unchanged (backlog 71fc); server.py imports every name
back, so dayz_mcp.server.<name> is the same object.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any


def _is_int_clock_part(value: object) -> bool:
    """True for int or finite integral float (60.0, 24.0). Never bool."""
    if isinstance(value, bool):
        return False
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value) and value == int(value)
    return False


def _add_applied_days(out: dict[str, Any], extra_days: int) -> None:
    if extra_days == 0:
        return
    year, month, day = out.get("year"), out.get("month"), out.get("day")
    if all(_is_int_clock_part(part) for part in (year, month, day)):
        try:
            shifted = datetime(int(year), int(month), int(day)) + timedelta(
                days=extra_days
            )
        except ValueError:
            if _is_int_clock_part(day):
                out["day"] = int(day) + extra_days
            return
        out["year"] = shifted.year
        out["month"] = shifted.month
        out["day"] = shifted.day
        return
    # Incomplete calendar (day without year/month) must not invent a date.


def _overflow_clock_parts(hour: int, minute: int) -> tuple[int, int, int]:
    extra_hours, minute = divmod(minute, 60)
    extra_days, hour = divmod(hour + extra_hours, 24)
    return extra_days, hour, minute


def _clock_was_normalized(raw: dict[str, Any], normalized: dict[str, Any]) -> bool:
    return any(
        raw.get(field) != normalized.get(field)
        for field in ("year", "month", "day", "hour", "minute")
    )


def _normalize_applied_clock(applied: dict[str, Any]) -> dict[str, Any]:
    """Carry minute>=60 into hour, then into the calendar day. Hour stays 0–23.

    GetDate can echo hour=8, minute=60 for a requested 9:00, or hour=23,
    minute=60 for midnight (fb-20260911-230929-311d). Integral floats
    60.0 and 24.0 take the same carry path as int 60/24. Overflow always
    carries into the next calendar day so applied.hour is never 24. The
    request is not consulted; a same-day 23:60 echo becomes 00:00 the
    next day even if the client asked for 00:00 on the echoed day.
    """
    out = dict(applied)
    hour = out.get("hour")
    minute = out.get("minute")
    if not _is_int_clock_part(hour) or not _is_int_clock_part(minute):
        return out
    hour = int(hour)
    minute = int(minute)
    if minute < 60 and 0 <= hour <= 23:
        return out
    extra_days, hour, minute = _overflow_clock_parts(hour, minute)
    out["hour"] = hour
    out["minute"] = minute
    _add_applied_days(out, extra_days)
    return out


# The five World.GetDate fields world_time_get answers in world_time.
WORLD_TIME_FIELDS = ("year", "month", "day", "hour", "minute")


def _world_time_reply(result: dict[str, Any]) -> dict[str, Any]:
    """world_time_get's answer: world_time normalized, the raw read beside it.

    GetDate can report hour=8, minute=60 for 9:00 (fb-20260911-230929-311d),
    so world_time goes through _normalize_applied_clock, as world_time_set's
    applied echo does; world_time_echo keeps the raw values. An answer that
    is not ok (the not-ready envelope a world read returns before enqueue)
    passes through untouched. An ok answer without the five integral fields
    is ok=0 with world_time_incomplete: a clock that did not arrive is not a
    reading.
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    response = dict(result)
    world_time = result.get("world_time")
    if isinstance(world_time, dict):
        echo = dict(world_time)
        normalized = _normalize_applied_clock(echo)
        response["world_time"] = normalized
        response["world_time_echo"] = echo
        response["clock_normalized"] = _clock_was_normalized(echo, normalized)
    complete = isinstance(world_time, dict) and all(
        _is_int_clock_part(world_time.get(field)) for field in WORLD_TIME_FIELDS
    )
    if not complete:
        response["ok"] = 0
        response["warnings"] = ["world_time_incomplete"]
    return response


# entities_query is trustworthy only with a player streaming the area: far from
# every player the engine answers 0 rows or exactly the cap with no error signal
# (fb-20260824-123204-638e, measured 2026-08-24). 300 m is conservative against
# the certified corridor probes (player within ~215 m of every sphere).
ENTITIES_QUERY_BUBBLE_M = 300.0


# entities_query rows carry has_cargo: MCPEntityHit declares the field
# (addon/scripts/5_Mission/MCPMessages.c:360) and DispatchEntitiesQuery fills it with
# HasCargoCapacity (MCPBridge.c:1422, defined :1441-1456 as EntityAI.Cast ->
# GetInventory() -> GetCargo() != null). Two wire facts keep a normalisation on this
# side:
#   - the bridge serialises an Enforce bool as int 0/1, the same way `ok` arrives
#     (wait_for_result above), so a consumer testing `row["has_cargo"] is True` would
#     read every container as false;
#   - a bridge that predates the field omits the key entirely, and absent means "the
#     bridge did not say" (null), never "no cargo" (false).
# A value in any other form is published as null rather than guessed: a truthiness test
# on an unknown shape would fabricate a verdict the bridge never gave.
def _cargo_flag(value: object) -> bool | None:
    """Read one has_cargo cell off the wire as a bool, or None when unstated."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return None


def _normalize_entities_cargo(result: dict[str, Any]) -> dict[str, Any]:
    """Publish has_cargo on every entities_query row as bool | None.

    Additive and total: no row is dropped, reordered or otherwise rewritten, and no
    shape raises. A result without rows, or rows that are not dicts, comes back as it
    arrived -- a missing field must never cost the caller the answer it did get.
    """
    if not isinstance(result, dict):
        return result
    rows = result.get("entities")
    if not isinstance(rows, list):
        return result
    for row in rows:
        if isinstance(row, dict):
            row["has_cargo"] = _cargo_flag(row.get("has_cargo"))
    return result


def _annotate_entities_reliability(
    result: dict[str, Any], players_result: object, pos: list[float]
) -> dict[str, Any]:
    """Stamp nearest_player_m and reliability on an entities_query result."""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    nearest: float | None = None
    players = []
    # The probe's raw list, kept apart from the iteration default: an ok reply
    # WITHOUT a players list is not evidence that nobody is connected.
    players_raw: object = None
    if isinstance(players_result, dict) and players_result.get("ok"):
        players_raw = players_result.get("players")
        players = players_raw if isinstance(players_raw, list) else []
    for player in players:
        ppos = player.get("pos") if isinstance(player, dict) else None
        if not (isinstance(ppos, list) and len(ppos) == 3):
            continue
        try:
            deltas = [float(a) - float(b) for a, b in zip(ppos, pos)]
        except (TypeError, ValueError):
            continue
        distance = (deltas[0] ** 2 + deltas[1] ** 2 + deltas[2] ** 2) ** 0.5
        if nearest is None or distance < nearest:
            nearest = distance
    if nearest is not None:
        result["nearest_player_m"] = round(nearest, 1)
    else:
        result["nearest_player_m"] = None
    if nearest is not None and nearest <= ENTITIES_QUERY_BUBBLE_M:
        result["reliability"] = "player_in_bubble"
    else:
        result["reliability"] = "remote_unverified"
    # Positive evidence only: the probe answered ok AND carried an empty list.
    # A missing or non-list field is reported as remote_unverified without a reason.
    if isinstance(players_raw, list) and not players_raw:
        result["reason"] = "no_player_connected"
    return result
