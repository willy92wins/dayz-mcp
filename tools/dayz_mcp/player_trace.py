"""player_trace request and result contract (inbox 4ae5).

A per-frame trace of the local player on the owner client: position, velocity,
heading, falling, the floor, linked and parent entities and the movement state.
The sampler is MCPPlayerTrace in addon/scripts/4_World/MCP_PlayerTrace.c and the
bridge handler is DispatchPlayerTrace in addon/scripts/5_Mission/MCPClientBridge.c;
every bound below mirrors theirs, so a request this module builds is one the
bridge accepts. The shape is vehicle_trace's normalize_request /
normalize_bridge_result, dump included, without the course validation.
"""
from __future__ import annotations

import re
import uuid
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


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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


def _normalize_bridge_boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise ValueError("bad_bridge_trace_boolean")


def _normalize_entity(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("bad_bridge_trace_result")
    if not value:
        return None
    return value


def normalize_bridge_result(result: object) -> dict[str, object]:
    """Check the shape, type the bools and answer null for an unset entity.

    Nothing is derived here: no sample is dropped, reordered or interpolated.
    A dump answer carries no samples, its canonical path and a row count.
    """
    if not isinstance(result, dict):
        raise ValueError("bad_bridge_trace_result")
    trace = result.get("player_trace")
    if not isinstance(trace, dict):
        raise ValueError("bad_bridge_trace_result")
    samples = trace.get("samples")
    if not isinstance(samples, list):
        raise ValueError("bad_bridge_trace_result")
    if trace.get("mode") == "dump":
        if samples:
            raise ValueError("bad_bridge_trace_dump")
        rows = trace.get("rows")
        if not _is_canonical_dump_path(trace.get("path"), trace.get("trace_id")):
            raise ValueError("bad_bridge_trace_dump")
        if not _is_int(rows) or rows < 0 or rows > MAX_SAMPLES_MAX:
            raise ValueError("bad_bridge_trace_dump")
    for field in _BOOL_TRACE_FIELDS:
        if field not in trace:
            raise ValueError("bad_bridge_trace_boolean")
        trace[field] = _normalize_bridge_boolean(trace[field])
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("bad_bridge_trace_result")
        for field in _BOOL_SAMPLE_FIELDS:
            if field not in sample:
                raise ValueError("bad_bridge_trace_boolean")
            sample[field] = _normalize_bridge_boolean(sample[field])
        for field in ENTITY_SAMPLE_FIELDS:
            if field not in sample:
                raise ValueError("bad_bridge_trace_result")
            sample[field] = _normalize_entity(sample[field])
    return result
