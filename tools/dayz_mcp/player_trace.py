"""player_trace request and result contract (inbox 4ae5).

A per-frame trace of the local player on the owner client: position, velocity,
heading, falling, the floor, linked and parent entities and the movement state.
The sampler is MCPPlayerTrace in addon/scripts/4_World/MCP_PlayerTrace.c and the
bridge handler is DispatchPlayerTrace in addon/scripts/5_Mission/MCPClientBridge.c;
every bound below mirrors theirs, so a request this module builds is one the
bridge accepts. The shape is vehicle_trace's normalize_request /
normalize_bridge_result, dump included, without the course validation; unlike
vehicle_trace's, normalize_bridge_result checks every member of the answer.
"""
from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable
from typing import Any


TRACE_SCHEMA = "dayz-mcp-player-trace-v1"
TRACE_MODES = frozenset({"start", "status", "stop", "read", "clear", "dump"})
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SAMPLE_HZ_MIN = 20
SAMPLE_HZ_MAX = 60
MAX_SAMPLES_MIN = 2
MAX_SAMPLES_MAX = 8192
LIMIT_MIN = 1
LIMIT_MAX = 64
DUMP_FILENAME_PREFIX = "dayz_mcp_player_trace_"
DUMP_FILENAME_SUFFIX = ".jsonl"
DUMP_PROFILE_PREFIX = "$profile:"

# Enforce bool members (MCPPlayerTraceRead, MCPPlayerTraceSample). The bridge
# may serialize them as 0/1; anything else is refused, never guessed.
_BOOL_TRACE_FIELDS = ("active", "complete", "overflow", "eof")
_BOOL_SAMPLE_FIELDS = ("falling", "sliding_off_linked")
# Entity members of a sample. An unset Enforce reference reaches the wire as {}
# or null; the read answers null for both. A missing key is refused, like a
# missing bool: the serializer writes every member.
ENTITY_SAMPLE_FIELDS = ("floor", "linked", "parent")
# The stop reasons MCPPlayerTrace records (Fail, Stop and Abort), "" while the
# trace runs, and the names MCPPlayerTrace.CommandName gives a command id.
# tools/tests keep both sets equal to the Enforce source.
STOP_REASONS = frozenset(
    {
        "",
        "requested",
        "overflow",
        "clock_not_monotonic",
        "player_dead",
        "player_changed",
        "shutdown",
    }
)
COMMAND_NAMES = frozenset(
    {
        "move",
        "action",
        "melee",
        "melee2",
        "fall",
        "death",
        "damage",
        "ladder",
        "unconscious",
        "swim",
        "vehicle",
        "climb",
        "script",
        "none",
        "other",
    }
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    # A finite JSON number. An integral Enforce float may arrive as an int.
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_vector3(value: object) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_is_number(item) for item in value)


def _is_text(value: object) -> bool:
    return isinstance(value, str)


def _is_name(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _is_trace_id(value: object) -> bool:
    return isinstance(value, str) and TRACE_ID_PATTERN.fullmatch(value) is not None


def _is_one_of(names: frozenset[str]) -> Callable[[object], bool]:
    return lambda value: isinstance(value, str) and value in names


def _is_int_in(low: int, high: int) -> Callable[[object], bool]:
    return lambda value: _is_int(value) and low <= value <= high


_FieldChecks = tuple[tuple[str, Callable[[object], bool]], ...]

# Every member of MCPPlayerTraceRead but the bools and the samples. count,
# cursor, next_cursor, rows and path are also checked against each other.
_TRACE_FIELD_CHECKS: _FieldChecks = (
    ("schema", lambda value: value == TRACE_SCHEMA),
    ("mode", _is_one_of(TRACE_MODES)),
    ("trace_id", _is_trace_id),
    ("stop_reason", _is_one_of(STOP_REASONS)),
    ("sample_hz", _is_int_in(SAMPLE_HZ_MIN, SAMPLE_HZ_MAX)),
    ("capacity", _is_int_in(MAX_SAMPLES_MIN, MAX_SAMPLES_MAX)),
    ("count", _is_int),
    ("start_monotonic_s", _is_number),
    ("player_type", _is_text),
    ("net_id_low", _is_int),
    ("net_id_high", _is_int),
    ("cursor", _is_int),
    ("next_cursor", _is_int),
    ("path", _is_text),
    ("rows", _is_int),
)
# Every member of MCPPlayerTraceSample but the bools and the entities.
_SAMPLE_FIELD_CHECKS: _FieldChecks = (
    ("sequence", _is_int),
    ("monotonic_s", _is_number),
    ("sample_dt_s", lambda value: _is_number(value) and value >= 0),
    ("tick", _is_int),
    ("pos", _is_vector3),
    ("vel", _is_vector3),
    ("heading_deg", _is_number),
    ("yaw_deg", _is_number),
    ("command_type_id", _is_int),
    ("command", _is_one_of(COMMAND_NAMES)),
    ("stance_idx", _is_int),
    ("movement_idx", _is_int),
)
# Every member of a present MCPPlayerTraceEntity.
_ENTITY_FIELD_CHECKS: _FieldChecks = (
    ("type", _is_name),
    ("class_name", _is_name),
    ("net_id_low", _is_int),
    ("net_id_high", _is_int),
    ("pos", _is_vector3),
)


def normalize_request(
    mode: str,
    trace_id: str,
    cursor: int,
    limit: int,
    sample_hz: int,
    max_samples: int,
) -> dict[str, object]:
    if not isinstance(mode, str) or mode not in TRACE_MODES:
        raise ValueError("bad_mode")
    if not _is_int(cursor) or cursor < 0:
        raise ValueError("bad_cursor")
    if not _is_int(limit) or limit < LIMIT_MIN or limit > LIMIT_MAX:
        raise ValueError("bad_limit")
    if not _is_int(sample_hz) or sample_hz < SAMPLE_HZ_MIN or sample_hz > SAMPLE_HZ_MAX:
        raise ValueError("bad_sample_hz")
    if not _is_int(max_samples) or max_samples < MAX_SAMPLES_MIN or max_samples > MAX_SAMPLES_MAX:
        raise ValueError("bad_max_samples")

    if mode == "start":
        if trace_id:
            raise ValueError("bad_trace_id")
        normalized_id = uuid.uuid4().hex
    else:
        if not isinstance(trace_id, str) or TRACE_ID_PATTERN.fullmatch(trace_id) is None:
            raise ValueError("bad_trace_id")
        normalized_id = trace_id

    return {
        "mode": mode,
        "trace_id": normalized_id,
        "cursor": cursor,
        "limit": limit,
        "sample_hz": sample_hz,
        "max_samples": max_samples,
    }


def dump_relpath(trace_id: str) -> str:
    if not isinstance(trace_id, str) or TRACE_ID_PATTERN.fullmatch(trace_id) is None:
        raise ValueError("bad_trace_id")
    return DUMP_FILENAME_PREFIX + trace_id + DUMP_FILENAME_SUFFIX


def dump_profile_path(trace_id: str) -> str:
    return DUMP_PROFILE_PREFIX + dump_relpath(trace_id)


def _is_canonical_dump_path(path: object, trace_id: object) -> bool:
    if not isinstance(path, str) or not isinstance(trace_id, str):
        return False
    try:
        expected = dump_profile_path(trace_id)
    except ValueError:
        return False
    return path == expected


def _refused(code: str, where: str) -> ValueError:
    # "<code>: <member>", so the caller sees which member of the answer failed.
    return ValueError(f"{code}: {where}")


def _check_fields(record: dict[str, Any], checks: _FieldChecks, where: str) -> None:
    for name, check in checks:
        if name not in record or not check(record[name]):
            raise _refused("bad_bridge_trace_result", f"{where}.{name}")


def _normalize_bridge_boolean(value: object, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise _refused("bad_bridge_trace_boolean", where)


def _normalize_booleans(record: dict[str, Any], fields: tuple[str, ...], where: str) -> None:
    for field in fields:
        if field not in record:
            raise _refused("bad_bridge_trace_boolean", f"{where}.{field}")
        record[field] = _normalize_bridge_boolean(record[field], f"{where}.{field}")


def _normalize_entity(value: object, where: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _refused("bad_bridge_trace_result", where)
    if not value:
        return None
    _check_fields(value, _ENTITY_FIELD_CHECKS, where)
    return value


def _check_dump(trace: dict[str, Any], samples: list[Any]) -> None:
    # Dump writes the file, then answers its header (MCPPlayerTrace.Dump, then
    # View): no samples, the canonical path of this trace, and rows equal to
    # count, both read in the same dispatch.
    if samples:
        raise _refused("bad_bridge_trace_dump", "player_trace.samples")
    if not _is_canonical_dump_path(trace.get("path"), trace.get("trace_id")):
        raise _refused("bad_bridge_trace_dump", "player_trace.path")
    rows = trace.get("rows")
    count = trace.get("count")
    if not _is_int(rows) or not _is_int(count) or rows != count or not 0 <= rows <= MAX_SAMPLES_MAX:
        raise _refused("bad_bridge_trace_dump", "player_trace.rows")


def _check_page(trace: dict[str, Any], samples: list[Any]) -> None:
    # MCPPlayerTrace.View's invariants: count within capacity, the page
    # cursor..next_cursor within count with exactly that many samples, the row
    # count of the last dump no larger than count, and no path but its own.
    count = trace["count"]
    cursor = trace["cursor"]
    next_cursor = trace["next_cursor"]
    if not 0 <= count <= trace["capacity"]:
        raise _refused("bad_bridge_trace_result", "player_trace.count")
    if not 0 <= cursor <= count:
        raise _refused("bad_bridge_trace_result", "player_trace.cursor")
    if not cursor <= next_cursor <= count:
        raise _refused("bad_bridge_trace_result", "player_trace.next_cursor")
    if len(samples) != next_cursor - cursor:
        raise _refused("bad_bridge_trace_result", "player_trace.samples")
    if not 0 <= trace["rows"] <= count:
        raise _refused("bad_bridge_trace_result", "player_trace.rows")
    if trace["path"] not in ("", dump_profile_path(trace["trace_id"])):
        raise _refused("bad_bridge_trace_result", "player_trace.path")


def normalize_bridge_result(result: object) -> dict[str, object]:
    """Check the whole answer, type the bools and answer null for an unset entity.

    Every member of the header, of each sample and of each present entity must
    be there with its type: finite numbers, three-number vectors, known names.
    The page must be the samples cursor..next_cursor, in order, and a dump
    answer carries no samples, its canonical path and rows equal to count. An
    answer that fails any check is refused with the member it failed on; nothing
    is derived here: no sample is dropped, reordered or interpolated.
    """
    if not isinstance(result, dict):
        raise _refused("bad_bridge_trace_result", "result")
    trace = result.get("player_trace")
    if not isinstance(trace, dict):
        raise _refused("bad_bridge_trace_result", "player_trace")
    samples = trace.get("samples")
    if not isinstance(samples, list):
        raise _refused("bad_bridge_trace_result", "player_trace.samples")
    if trace.get("mode") == "dump":
        _check_dump(trace, samples)
    _normalize_booleans(trace, _BOOL_TRACE_FIELDS, "player_trace")
    _check_fields(trace, _TRACE_FIELD_CHECKS, "player_trace")
    _check_page(trace, samples)
    for index, sample in enumerate(samples):
        where = f"player_trace.samples[{index}]"
        if not isinstance(sample, dict):
            raise _refused("bad_bridge_trace_result", where)
        _normalize_booleans(sample, _BOOL_SAMPLE_FIELDS, where)
        _check_fields(sample, _SAMPLE_FIELD_CHECKS, where)
        if sample["sequence"] != trace["cursor"] + index:
            raise _refused("bad_bridge_trace_result", f"{where}.sequence")
        for field in ENTITY_SAMPLE_FIELDS:
            if field not in sample:
                raise _refused("bad_bridge_trace_result", f"{where}.{field}")
            sample[field] = _normalize_entity(sample[field], f"{where}.{field}")
    return result
