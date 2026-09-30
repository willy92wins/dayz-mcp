"""anim_timeline request and result contract (ficha 5535).

A client-side timeline of the local player's action states and of the
animation phases of the item in hands. The sampler is MCPAnimTimeline in
addon/scripts/4_World/MCP_AnimTimeline.c and the bridge handler is
DispatchAnimTimeline in addon/scripts/5_Mission/MCPClientBridge.c; every bound
below mirrors theirs, so a request this module builds is one the bridge
accepts. The shape is vehicle_trace's normalize_request /
normalize_bridge_result, without the dump and the course validation.
"""
from __future__ import annotations

import re
import uuid


TIMELINE_SCHEMA = "dayz-mcp-anim-timeline-v1"
TIMELINE_MODES = frozenset({"start", "status", "stop", "read", "clear"})
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SAMPLE_HZ_MIN = 10
SAMPLE_HZ_MAX = 60
MAX_SAMPLES_MIN = 2
MAX_SAMPLES_MAX = 8192
LIMIT_MIN = 1
LIMIT_MAX = 64
SOURCE_COUNT_MAX = 8
SOURCE_NAME_MAX_CHARS = 64

# Enforce bool members (MCPAnimTimelineRead, MCPAnimTimelineSample). The bridge
# may serialize them as 0/1; anything else is refused, never guessed.
_BOOL_TIMELINE_FIELDS = ("active", "complete", "overflow", "eof")
_BOOL_SAMPLE_FIELDS = ("edge", "player_present", "callback_both", "hands_present")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_source_name(value: object) -> bool:
    """Printable ASCII (32..126), 1..64 characters: MCPAnimTimeline.IsSourceName."""

    if not isinstance(value, str) or value == "" or len(value) > SOURCE_NAME_MAX_CHARS:
        return False
    return all(32 <= ord(character) <= 126 for character in value)


def is_source_list(value: object) -> bool:
    """A list of at most 8 source names; empty is valid (MCPAnimTimeline.SourcesOk)."""

    return (
        isinstance(value, list)
        and len(value) <= SOURCE_COUNT_MAX
        and all(is_source_name(item) for item in value)
    )


def normalize_request(
    mode: str,
    trace_id: str,
    cursor: int,
    limit: int,
    sample_hz: int,
    max_samples: int,
    sources: list[str] | None,
) -> dict[str, object]:
    if not isinstance(mode, str) or mode not in TIMELINE_MODES:
        raise ValueError("bad_mode")
    if not _is_int(cursor) or cursor < 0:
        raise ValueError("bad_cursor")
    if not _is_int(limit) or limit < LIMIT_MIN or limit > LIMIT_MAX:
        raise ValueError("bad_limit")
    if not _is_int(sample_hz) or sample_hz < SAMPLE_HZ_MIN or sample_hz > SAMPLE_HZ_MAX:
        raise ValueError("bad_sample_hz")
    if not _is_int(max_samples) or max_samples < MAX_SAMPLES_MIN or max_samples > MAX_SAMPLES_MAX:
        raise ValueError("bad_max_samples")
    normalized_sources: object = [] if sources is None else sources
    if not is_source_list(normalized_sources):
        raise ValueError("bad_sources")

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
        "sources": list(normalized_sources),  # type: ignore[arg-type]
    }


def _normalize_bridge_boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise ValueError("bad_bridge_timeline_boolean")


def normalize_bridge_result(result: object) -> dict[str, object]:
    """Check the shape and type the bools; every other value passes untouched.

    Nothing is derived here: no action is declared ran, no phase is
    interpolated, no sample is dropped or reordered.
    """

    if not isinstance(result, dict):
        raise ValueError("bad_bridge_timeline_result")
    timeline = result.get("timeline")
    if not isinstance(timeline, dict):
        raise ValueError("bad_bridge_timeline_result")
    samples = timeline.get("samples")
    if not isinstance(samples, list):
        raise ValueError("bad_bridge_timeline_result")
    for field in _BOOL_TIMELINE_FIELDS:
        if field not in timeline:
            raise ValueError("bad_bridge_timeline_boolean")
        timeline[field] = _normalize_bridge_boolean(timeline[field])
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("bad_bridge_timeline_result")
        for field in _BOOL_SAMPLE_FIELDS:
            if field not in sample:
                raise ValueError("bad_bridge_timeline_boolean")
            sample[field] = _normalize_bridge_boolean(sample[field])
    return result
