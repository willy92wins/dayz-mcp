"""players_los_cone request and result contract.

A stateless, on-demand server-side head-to-head line-of-sight cone read for 1..4
named living observers. The bridge handler is DispatchPlayersLosCone in
addon/scripts/5_Mission/MCPBridge.c and the wire classes live in
addon/scripts/5_Mission/MCPMessages.c; every bound below mirrors theirs.

Rows are published inside the dayz-mcp-evidence-v1 envelope (evidence.py).
Nothing is filtered, reordered, or checked for plausibility here; unverified
server reads (UNVERIFIED_FIELDS) are masked as unavailable unless the caller
explicitly sets include_unverified=True.
"""
from __future__ import annotations

import math
from typing import Any

from dayz_mcp import evidence


SCHEMA = "dayz-mcp-players-los-cone-v1"
RADIUS_MAX_M = 50.0
RADIUS_DEFAULT_M = 50.0
HALF_YAW_MAX_DEG = 30.0
HALF_YAW_DEFAULT_DEG = 10.0
OBSERVERS_MAX = 4
UID_MAX_CHARS = 64
RAYS_PER_OBSERVER_MAX = 8
# Wire payload budget for POST /result against loopback.MAX_BODY_BYTES (1 MiB):
# one worst-case MCPPlayersLosConeRow JSON (~1120 B: 64-char uid, target_uid,
# hit_type and hit_class, longest gap_kind "ray_budget_exceeded", 24-char
# numeric tokens, all 24 wire keys) * OBSERVERS_MAX (4) * ROWS_PER_OBSERVER_MAX
# (32) = 128 rows (~144 KB), plus 16384 B for the rest of the POST /result body
# (the full MCPResult and MCPPlayersLosConeRead header with 4 worst-case
# observer summaries of ~180 B each) ~= 160 KB, well below half of
# loopback.MAX_BODY_BYTES (524288 B).
ROWS_PER_OBSERVER_MAX = 32

GAP_KINDS = frozenset({"ray_budget_exceeded"})
# Server reads not yet verified against human input: masked as unavailable by
# default until include_unverified=True is passed.
UNVERIFIED_FIELDS = ("pitch_deg",)

KIND = evidence.EvidenceKind(
    name="players_los_cone",
    fields={
        "target_uid": evidence.EvidenceField("uid"),
        "observer_head": evidence.EvidenceField("vec3", "m"),
        "target_head": evidence.EvidenceField("vec3", "m"),
        "observer_head_bone": evidence.EvidenceField("flag"),
        "target_head_bone": evidence.EvidenceField("flag"),
        "distance_m": evidence.EvidenceField("number", "m"),
        "yaw_deg": evidence.EvidenceField("number", "deg"),
        "yaw_off_deg": evidence.EvidenceField("number", "deg"),
        "pitch_deg": evidence.EvidenceField("number", "deg", optional=True),
        "ray_hit": evidence.EvidenceField(
            "flag", absent_with=frozenset({"ray_budget_exceeded"})
        ),
        "hit_pos": evidence.EvidenceField("vec3", "m", optional=True),
        "hit_distance_m": evidence.EvidenceField("number", "m", optional=True),
        "clearance_m": evidence.EvidenceField("number", "m", optional=True),
        "hit_type": evidence.EvidenceField("token", optional=True),
        "hit_class": evidence.EvidenceField("token", optional=True),
    },
    gap_kinds=GAP_KINDS,
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_negative_int(value: object) -> bool:
    return _is_int(value) and value >= 0


def _is_finite_real(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def is_uids_list(value: object) -> bool:
    """List of 1..OBSERVERS_MAX unique non-blank uid strings up to UID_MAX_CHARS."""
    if not isinstance(value, list) or not 1 <= len(value) <= OBSERVERS_MAX:
        return False
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or item.strip() == "" or len(item) > UID_MAX_CHARS:
            return False
        if item in seen:
            return False
        seen.add(item)
    return True


def normalize_request(
    uids: list[str],
    radius_m: float = RADIUS_DEFAULT_M,
    half_yaw_deg: float = HALF_YAW_DEFAULT_DEG,
) -> dict[str, object]:
    if not is_uids_list(uids):
        raise ValueError("bad_uids")
    if not _is_finite_real(radius_m) or radius_m <= 0.0 or radius_m > RADIUS_MAX_M:
        raise ValueError("bad_radius_m")
    if (
        not _is_finite_real(half_yaw_deg)
        or half_yaw_deg <= 0.0
        or half_yaw_deg > HALF_YAW_MAX_DEG
    ):
        raise ValueError("bad_half_yaw_deg")
    return {
        "uids": list(uids),
        "radius": float(radius_m),
        "half_yaw_deg": float(half_yaw_deg),
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
    raise _refused("bad_bridge_los_cone_boolean", where)


def _normalize_observers(raw_observers: object) -> list[dict[str, Any]]:
    if not isinstance(raw_observers, list) or not 1 <= len(raw_observers) <= OBSERVERS_MAX:
        raise _refused("bad_bridge_los_cone_result", "players_los_cone.observers")

    seen_uids: set[str] = set()
    published: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_observers):
        where = f"players_los_cone.observers[{index}]"
        if not isinstance(entry, dict):
            raise _refused("bad_bridge_los_cone_result", where)

        uid = entry.get("uid")
        if not isinstance(uid, str) or uid.strip() == "" or uid in seen_uids:
            raise _refused("bad_bridge_los_cone_result", f"{where}.uid")
        seen_uids.add(uid)

        if "found" not in entry:
            raise _refused("bad_bridge_los_cone_boolean", f"{where}.found")
        found = _normalize_bridge_boolean(entry["found"], f"{where}.found")

        if "yaw_deg_available" not in entry:
            raise _refused("bad_bridge_los_cone_boolean", f"{where}.yaw_deg_available")
        yaw_deg_available = _normalize_bridge_boolean(
            entry["yaw_deg_available"], f"{where}.yaw_deg_available"
        )

        counts: dict[str, int] = {}
        for count_name in ("in_radius", "in_cone", "rays", "rows"):
            value = entry.get(count_name)
            if not _is_non_negative_int(value):
                raise _refused("bad_bridge_los_cone_result", f"{where}.{count_name}")
            counts[count_name] = value  # type: ignore[assignment]

        in_radius = counts["in_radius"]
        in_cone = counts["in_cone"]
        rays = counts["rays"]
        rows = counts["rows"]

        if rays > RAYS_PER_OBSERVER_MAX or rays > rows:
            raise _refused("bad_bridge_los_cone_result", f"{where}.rays")
        if rows > ROWS_PER_OBSERVER_MAX or rows > in_cone:
            raise _refused("bad_bridge_los_cone_result", f"{where}.rows")
        if in_cone > in_radius:
            raise _refused("bad_bridge_los_cone_result", f"{where}.in_cone")

        if not found:
            if yaw_deg_available:
                raise _refused("bad_bridge_los_cone_result", f"{where}.yaw_deg_available")
            if in_radius != 0:
                raise _refused("bad_bridge_los_cone_result", f"{where}.in_radius")
            if in_cone != 0:
                raise _refused("bad_bridge_los_cone_result", f"{where}.in_cone")
            if rays != 0:
                raise _refused("bad_bridge_los_cone_result", f"{where}.rays")
            if rows != 0:
                raise _refused("bad_bridge_los_cone_result", f"{where}.rows")

        if not yaw_deg_available:
            if in_cone != 0:
                raise _refused("bad_bridge_los_cone_result", f"{where}.in_cone")
            if rays != 0:
                raise _refused("bad_bridge_los_cone_result", f"{where}.rays")
            if rows != 0:
                raise _refused("bad_bridge_los_cone_result", f"{where}.rows")

        summary: dict[str, Any] = {
            "uid": uid,
            "found": found,
            "yaw_deg_available": yaw_deg_available,
        }
        if yaw_deg_available:
            yaw_deg = entry.get("yaw_deg")
            if not _is_finite_real(yaw_deg):
                raise _refused("bad_bridge_los_cone_result", f"{where}.yaw_deg")
            summary["yaw_deg"] = yaw_deg
        summary["in_radius"] = in_radius
        summary["in_cone"] = in_cone
        summary["rays"] = rays
        summary["rows"] = rows
        published.append(summary)

    return published


def _prepare_rows(
    raw_rows: list[Any],
    *,
    header_tick_ms: int,
    observers: list[dict[str, Any]],
    include_unverified: bool,
) -> list[dict[str, Any]]:
    found_uids = {obs["uid"] for obs in observers if obs["found"]}
    row_counts = {obs["uid"]: 0 for obs in observers}
    ray_counts = {obs["uid"]: 0 for obs in observers}
    prepared: list[dict[str, Any]] = []

    for index, row in enumerate(raw_rows):
        where = f"players_los_cone.rows[{index}]"
        if not isinstance(row, dict):
            raise _refused("bad_bridge_los_cone_result", where)
        uid = row.get("uid")
        if not isinstance(uid, str) or uid not in found_uids:
            raise _refused("bad_bridge_los_cone_result", f"{where}.uid")
        tick_ms = row.get("tick_ms")
        if not _is_int(tick_ms) or tick_ms != header_tick_ms:
            raise _refused("bad_bridge_los_cone_result", f"{where}.tick_ms")

        row_counts[uid] += 1
        if row.get("gap_kind") == "":
            ray_counts[uid] += 1

        copy_row = dict(row)
        if not include_unverified:
            for field in UNVERIFIED_FIELDS:
                avail_key = f"{field}_available"
                if avail_key in copy_row and _is_wire_flag(copy_row[avail_key]):
                    copy_row[avail_key] = False
        prepared.append(copy_row)

    for index, obs in enumerate(observers):
        where = f"players_los_cone.observers[{index}]"
        if row_counts[obs["uid"]] != obs["rows"]:
            raise _refused("bad_bridge_los_cone_result", f"{where}.rows")
        if ray_counts[obs["uid"]] != obs["rays"]:
            raise _refused("bad_bridge_los_cone_result", f"{where}.rays")

    return prepared


def normalize_bridge_result(
    result: object, *, include_unverified: bool = False
) -> dict[str, Any]:
    """Validate header, observer summaries, and rows; build the evidence envelope.

    Publishes ``players_los_cone`` without wire ``rows`` and with each observer
    summary omitting ``yaw_deg`` when unavailable, alongside ``evidence =
    evidence.evidence_result(KIND, rows, truncated=header["truncated"])``.
    Neither the input dict nor any wire row is mutated.
    """
    if not isinstance(include_unverified, bool):
        raise _refused("bad_bridge_los_cone_boolean", "include_unverified")
    if not isinstance(result, dict):
        raise _refused("bad_bridge_los_cone_result", "result")
    raw_header = result.get("players_los_cone")
    if not isinstance(raw_header, dict):
        raise _refused("bad_bridge_los_cone_result", "players_los_cone")
    raw_rows = raw_header.get("rows")
    if not isinstance(raw_rows, list):
        raise _refused("bad_bridge_los_cone_result", "players_los_cone.rows")

    header = {key: value for key, value in raw_header.items() if key != "rows"}

    for field in ("pitch_filter_applied", "truncated"):
        if field not in header:
            raise _refused("bad_bridge_los_cone_boolean", f"players_los_cone.{field}")
        header[field] = _normalize_bridge_boolean(
            header[field], f"players_los_cone.{field}"
        )

    if header.get("schema") != SCHEMA:
        raise _refused("bad_bridge_los_cone_result", "players_los_cone.schema")
    if not _is_non_negative_int(header.get("tick_ms")):
        raise _refused("bad_bridge_los_cone_result", "players_los_cone.tick_ms")
    radius_m = header.get("radius_m")
    if not _is_finite_real(radius_m) or radius_m <= 0.0 or radius_m > RADIUS_MAX_M:
        raise _refused("bad_bridge_los_cone_result", "players_los_cone.radius_m")
    half_yaw_deg = header.get("half_yaw_deg")
    if (
        not _is_finite_real(half_yaw_deg)
        or half_yaw_deg <= 0.0
        or half_yaw_deg > HALF_YAW_MAX_DEG
    ):
        raise _refused("bad_bridge_los_cone_result", "players_los_cone.half_yaw_deg")
    if header["pitch_filter_applied"] is not False:
        raise _refused(
            "bad_bridge_los_cone_result", "players_los_cone.pitch_filter_applied"
        )
    for cap_name, expected in (
        ("observers_max", OBSERVERS_MAX),
        ("rays_per_observer_max", RAYS_PER_OBSERVER_MAX),
        ("rows_per_observer_max", ROWS_PER_OBSERVER_MAX),
    ):
        value = header.get(cap_name)
        if not _is_int(value) or value != expected:
            raise _refused("bad_bridge_los_cone_result", f"players_los_cone.{cap_name}")

    observers = _normalize_observers(header.get("observers"))
    prepared_rows = _prepare_rows(
        raw_rows,
        header_tick_ms=header["tick_ms"],
        observers=observers,
        include_unverified=include_unverified,
    )

    expected_truncated = any(obs["in_cone"] > obs["rows"] for obs in observers)
    if header["truncated"] != expected_truncated:
        raise _refused("bad_bridge_los_cone_result", "players_los_cone.truncated")

    header["observers"] = observers
    out = dict(result)
    out["players_los_cone"] = header
    out["evidence"] = evidence.evidence_result(
        KIND,
        prepared_rows,
        truncated=header["truncated"],
    )
    return out
