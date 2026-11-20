"""players_kinematics request and result contract.

A server-side per-player kinematic sampler driven from MissionServer.OnUpdate
at 5..10 Hz against GetGame().GetTime() (integer milliseconds). The sampler is
MCPPlayersKinematics in addon/scripts/4_World/MCP_PlayersKinematics.c and the
bridge handler is DispatchPlayersKinematics in
addon/scripts/5_Mission/MCPBridge.c; every bound below mirrors theirs.

Rows are published inside the dayz-mcp-evidence-v1 envelope (evidence.py).
Nothing is filtered, reordered, or checked for plausibility here; unverified
server reads (UNVERIFIED_FIELDS) are masked as unavailable unless the caller
explicitly sets include_unverified=True.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from dayz_mcp import evidence, player_trace


SCHEMA = "dayz-mcp-players-kinematics-v1"
TRACE_MODES = frozenset({"start", "status", "stop", "read", "clear"})
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SAMPLE_HZ_MIN = 5
SAMPLE_HZ_MAX = 10
MAX_SAMPLES_MIN = 1
MAX_SAMPLES_MAX = 8192
LIMIT_MIN = 1
# Wire payload budget for POST /result against loopback.MAX_BODY_BYTES (1 MiB):
# one worst-case MCPPlayersKinematicsRow JSON (~960 B: 64-char uid and
# vehicle_type, longest command "unconscious" and vehicle_class "aircraft",
# 24-char numbers, all 24 keys) * LIMIT_MAX (256) ~= 246 KB, plus 16384 B for
# the rest of the MCPResult body and MCPPlayersKinematicsRead header ~= 262 KB,
# staying well below half of MAX_BODY_BYTES (524288 B). The header's echoed
# uids list is bounded by UIDS_MAX and UID_MAX_CHARS.
LIMIT_MAX = 256
UIDS_MAX = 16
UID_MAX_CHARS = 64
PASS_BUDGET_TICKS = 5000

# Closed vocabularies mirrored in addon/scripts/4_World/MCP_PlayersKinematics.c.
STOP_REASONS = frozenset({"", "requested", "overflow"})
GAP_KINDS = frozenset(
    {
        "first_sample",
        "clock_not_monotonic",
        "subject_absent",
        "pass_late",
    }
)
VEHICLE_CLASSES = frozenset({"none", "ground", "water", "aircraft", "unknown"})
COMMAND_NAMES = player_trace.COMMAND_NAMES
# Server reads not yet verified against human mouse/keyboard input: masked as
# unavailable by default until include_unverified=True is passed.
UNVERIFIED_FIELDS = ("pitch_deg", "jump")

KIND = evidence.EvidenceKind(
    name="players_kinematics",
    fields={
        "pos": evidence.EvidenceField("vec3", "m"),
        "expected_dt_s": evidence.EvidenceField("number", "s"),
        "observed_dt_s": evidence.EvidenceField(
            "number", "s", absent_with=frozenset({"first_sample"})
        ),
        "vel": evidence.EvidenceField(
            "vec3",
            "m/s",
            absent_with=frozenset({"first_sample", "clock_not_monotonic"}),
        ),
        "vel_engine": evidence.EvidenceField("vec3", "m/s", optional=True),
        "yaw_deg": evidence.EvidenceField("number", "deg", optional=True),
        "pitch_deg": evidence.EvidenceField("number", "deg", optional=True),
        "stance_idx": evidence.EvidenceField("int"),
        "command": evidence.EvidenceField("token"),
        "falling": evidence.EvidenceField("flag", optional=True),
        "jump": evidence.EvidenceField("flag", optional=True),
        "parachute": evidence.EvidenceField("flag", optional=True),
        "in_vehicle": evidence.EvidenceField("flag"),
        "vehicle_type": evidence.EvidenceField("token"),
        "vehicle_class": evidence.EvidenceField("token"),
    },
    gap_kinds=GAP_KINDS,
)

# Enforce bool members on MCPPlayersKinematicsRead. The bridge may serialize
# them as 0/1; anything else is refused, never guessed.
_BOOL_HEADER_FIELDS = ("active", "overflow", "eof")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_negative_int(value: object) -> bool:
    return _is_int(value) and value >= 0


def _is_int_in(low: int, high: int) -> Callable[[object], bool]:
    return lambda value: _is_int(value) and low <= value <= high


def _is_trace_id(value: object) -> bool:
    return isinstance(value, str) and TRACE_ID_PATTERN.fullmatch(value) is not None


def _is_one_of(names: frozenset[str]) -> Callable[[object], bool]:
    return lambda value: isinstance(value, str) and value in names


def is_uids_list(value: object) -> bool:
    """List of 0..UIDS_MAX unique non-blank uid strings of at most UID_MAX_CHARS chars."""
    if not isinstance(value, list) or len(value) > UIDS_MAX:
        return False
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or item.strip() == "" or len(item) > UID_MAX_CHARS:
            return False
        if item in seen:
            return False
        seen.add(item)
    return True


_FieldChecks = tuple[tuple[str, Callable[[object], bool]], ...]

_HEADER_FIELD_CHECKS: _FieldChecks = (
    ("schema", lambda value: value == SCHEMA),
    ("mode", _is_one_of(TRACE_MODES)),
    ("trace_id", _is_trace_id),
    ("stop_reason", _is_one_of(STOP_REASONS)),
    ("sample_hz", _is_int_in(SAMPLE_HZ_MIN, SAMPLE_HZ_MAX)),
    ("interval_ms", _is_non_negative_int),
    ("late_after_ms", _is_non_negative_int),
    ("capacity", _is_int_in(MAX_SAMPLES_MIN, MAX_SAMPLES_MAX)),
    ("count", _is_int),
    ("passes", _is_non_negative_int),
    ("budget_skips", _is_non_negative_int),
    ("pass_cost_last_ticks", _is_non_negative_int),
    ("pass_cost_max_ticks", _is_non_negative_int),
    ("pass_cost_budget_ticks", lambda value: _is_int(value) and value == PASS_BUDGET_TICKS),
    ("start_tick_ms", _is_non_negative_int),
    ("now_tick_ms", _is_non_negative_int),
    ("uids", is_uids_list),
    ("cursor", _is_int),
    ("next_cursor", _is_int),
)


def normalize_request(
    mode: str,
    trace_id: str,
    cursor: int,
    limit: int,
    sample_hz: int,
    max_samples: int,
    uids: list[str] | None,
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

    normalized_uids: object = [] if uids is None else uids
    if not is_uids_list(normalized_uids):
        raise ValueError("bad_uids")

    if mode == "start":
        if not isinstance(trace_id, str) or trace_id != "":
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
        "uids": list(normalized_uids),  # type: ignore[arg-type]
    }


def _refused(code: str, where: str) -> ValueError:
    return ValueError(f"{code}: {where}")


def _is_wire_flag(value: object) -> bool:
    return isinstance(value, bool) or (type(value) is int and value in (0, 1))


def _normalize_bridge_boolean(value: object, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise _refused("bad_bridge_kinematics_boolean", where)


def _check_header(header: dict[str, Any], rows: list[Any]) -> None:
    for field in _BOOL_HEADER_FIELDS:
        if field not in header:
            raise _refused("bad_bridge_kinematics_boolean", f"players_kinematics.{field}")
        header[field] = _normalize_bridge_boolean(header[field], f"players_kinematics.{field}")

    for name, check in _HEADER_FIELD_CHECKS:
        if name not in header or not check(header[name]):
            raise _refused("bad_bridge_kinematics_result", f"players_kinematics.{name}")

    expected_interval_ms = 1000 // header["sample_hz"]
    if header["interval_ms"] != expected_interval_ms:
        raise _refused("bad_bridge_kinematics_result", "players_kinematics.interval_ms")
    expected_late_after_ms = expected_interval_ms + expected_interval_ms // 2
    if header["late_after_ms"] != expected_late_after_ms:
        raise _refused("bad_bridge_kinematics_result", "players_kinematics.late_after_ms")

    count = header["count"]
    cursor = header["cursor"]
    next_cursor = header["next_cursor"]
    if not 0 <= count <= header["capacity"]:
        raise _refused("bad_bridge_kinematics_result", "players_kinematics.count")
    if not 0 <= cursor <= count:
        raise _refused("bad_bridge_kinematics_result", "players_kinematics.cursor")
    if not cursor <= next_cursor <= count:
        raise _refused("bad_bridge_kinematics_result", "players_kinematics.next_cursor")
    if header["mode"] != "read":
        if rows:
            raise _refused("bad_bridge_kinematics_result", "players_kinematics.rows")
        if next_cursor != cursor:
            raise _refused("bad_bridge_kinematics_result", "players_kinematics.next_cursor")
    elif len(rows) != next_cursor - cursor:
        raise _refused("bad_bridge_kinematics_result", "players_kinematics.rows")


def _prepare_rows(
    rows: list[Any], *, include_unverified: bool
) -> list[dict[str, Any]]:
    prepared_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        where = f"players_kinematics.rows[{index}]"
        if not isinstance(row, dict):
            raise _refused("bad_bridge_kinematics_result", where)
        command = row.get("command")
        if not isinstance(command, str) or command not in COMMAND_NAMES:
            raise _refused("bad_bridge_kinematics_result", f"{where}.command")
        vehicle_class = row.get("vehicle_class")
        if not isinstance(vehicle_class, str) or vehicle_class not in VEHICLE_CLASSES:
            raise _refused("bad_bridge_kinematics_result", f"{where}.vehicle_class")
        copy_row = dict(row)
        if not include_unverified:
            for field in UNVERIFIED_FIELDS:
                avail_key = f"{field}_available"
                if avail_key in copy_row and _is_wire_flag(copy_row[avail_key]):
                    copy_row[avail_key] = False
        prepared_rows.append(copy_row)
    return prepared_rows


def normalize_bridge_result(
    result: object, *, include_unverified: bool = False
) -> dict[str, Any]:
    """Validate header and rows, mask unverified fields, and build the evidence envelope.

    Publishes ``players_kinematics`` without raw wire ``rows`` and, in ``mode == "read"``
    only, ``evidence = evidence.evidence_result(KIND, rows, truncated=next_cursor < count)``.
    Neither the input dict nor any wire row is mutated.
    """
    if not isinstance(include_unverified, bool):
        raise _refused("bad_bridge_kinematics_boolean", "include_unverified")
    if not isinstance(result, dict):
        raise _refused("bad_bridge_kinematics_result", "result")
    raw_header = result.get("players_kinematics")
    if not isinstance(raw_header, dict):
        raise _refused("bad_bridge_kinematics_result", "players_kinematics")
    rows = raw_header.get("rows")
    if not isinstance(rows, list):
        raise _refused("bad_bridge_kinematics_result", "players_kinematics.rows")

    header = {key: value for key, value in raw_header.items() if key != "rows"}
    _check_header(header, rows)
    header["uids"] = list(header["uids"])

    out = dict(result)
    out["players_kinematics"] = header
    if header["mode"] == "read":
        prepared = _prepare_rows(rows, include_unverified=include_unverified)
        out["evidence"] = evidence.evidence_result(
            KIND,
            prepared,
            truncated=header["next_cursor"] < header["count"],
        )
    else:
        out.pop("evidence", None)
    return out
