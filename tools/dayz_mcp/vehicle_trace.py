from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any


TRACE_SCHEMA = "dayz-mcp-vehicle-trace-v1"
ARTIFACT_SCHEMA = "dayz-mcp-vehicle-trace-artifact-v1"
COURSE_SCHEMA = "dayz-mcp-vehicle-trace-course-v1"
CONTROL_COURSE_ID = "civilian-sedan-control-v1"
CONTROL_VEHICLE_TYPE = "CivilianSedan"
CONTROL_SAMPLE_HZ = 20
TRACE_MODES = frozenset({"start", "status", "stop", "read", "clear"})
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
TIME_EPS_S = 1e-6
REQUIRED_CONTROL_OBSERVATIONS = frozenset(
    {
        "owner_stable",
        "net_id_stable",
        "control_readback",
        "body_contact_owner_client",
    }
)

REQUIRED_TRACE_FIELDS = frozenset(
    {
        "schema",
        "mode",
        "trace_id",
        "active",
        "complete",
        "overflow",
        "stop_reason",
        "sample_hz",
        "capacity",
        "count",
        "start_monotonic_s",
        "owner_identity",
        "car_type",
        "net_id_low",
        "net_id_high",
        "cursor",
        "next_cursor",
        "eof",
        "samples",
    }
)
REQUIRED_SAMPLE_FIELDS = frozenset(
    {
        "sequence",
        "monotonic_s",
        "sample_dt_s",
        "forced",
        "position_x",
        "position_y",
        "position_z",
        "velocity_x",
        "velocity_y",
        "velocity_z",
        "direction_x",
        "direction_y",
        "direction_z",
        "yaw_deg",
        "pitch_deg",
        "roll_deg",
        "control_active",
        "throttle_requested",
        "steer_requested",
        "brake_requested",
        "handbrake_requested",
        "throttle_applied",
        "steer_applied",
        "brake_applied",
        "handbrake_applied",
        "wheel_contact_0",
        "wheel_contact_1",
        "wheel_contact_2",
        "wheel_contact_3",
        "wheel_angular_velocity_0",
        "wheel_angular_velocity_1",
        "wheel_angular_velocity_2",
        "wheel_angular_velocity_3",
        "wheel_count",
        "wheels_present",
        "engine_on",
        "gear",
        "is_owner",
        "is_authority_owner",
        "net_id_low",
        "net_id_high",
        "wheel_loss_event",
        "wheel_loss_count",
        "contact_count",
        "body_contact_count",
        "body_contact_zone",
        "body_contact_impulse",
        "body_contact_local_x",
        "body_contact_local_y",
        "body_contact_local_z",
        "body_contact_normal_x",
        "body_contact_normal_y",
        "body_contact_normal_z",
        "body_contact_penetration_depth",
    }
)

_BOOL_SAMPLE_FIELDS = frozenset(
    {
        "forced",
        "control_active",
        "wheel_contact_0",
        "wheel_contact_1",
        "wheel_contact_2",
        "wheel_contact_3",
        "engine_on",
        "is_owner",
        "is_authority_owner",
        "wheel_loss_event",
    }
)
_BOOL_TRACE_FIELDS = ("active", "complete", "overflow", "eof")
_INT_SAMPLE_FIELDS = frozenset(
    {
        "sequence",
        "wheel_count",
        "wheels_present",
        "gear",
        "net_id_low",
        "net_id_high",
        "wheel_loss_count",
        "contact_count",
        "body_contact_count",
    }
)
_STRING_SAMPLE_FIELDS = frozenset({"body_contact_zone"})
_CONTROL_CHANNELS = ("throttle", "steer", "brake", "handbrake")
_CANONICAL_CONTROL_SCHEDULE = (
    (0.0, 0.5, 0.0, 0.0, 0.0),
    (1.0, 0.2, 0.25, 0.0, 0.0),
    (2.0, 0.0, 0.0, 1.0, 0.0),
)


class ArtifactInputError(ValueError):
    pass


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def normalize_request(
    mode: str,
    trace_id: str,
    cursor: int,
    limit: int,
    sample_hz: int,
    max_samples: int,
) -> dict[str, object]:
    if mode not in TRACE_MODES:
        raise ValueError("bad_mode")
    if not _is_int(cursor) or cursor < 0:
        raise ValueError("bad_cursor")
    if not _is_int(limit) or limit < 1 or limit > 64:
        raise ValueError("bad_limit")
    if not _is_int(sample_hz) or sample_hz < 20 or sample_hz > 60:
        raise ValueError("bad_sample_hz")
    if not _is_int(max_samples) or max_samples < 2 or max_samples > 8192:
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


def _normalize_bridge_boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise ValueError("bad_bridge_trace_boolean")


def normalize_bridge_result(result: object) -> dict[str, object]:
    if not isinstance(result, dict):
        raise ValueError("bad_bridge_trace_result")
    trace = result.get("trace")
    if not isinstance(trace, dict):
        raise ValueError("bad_bridge_trace_result")
    samples = trace.get("samples")
    if not isinstance(samples, list):
        raise ValueError("bad_bridge_trace_result")

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
    return result


def _empty_derived() -> dict[str, object]:
    return {
        "effective_hz": 0.0,
        "max_gap_s": 0.0,
        "wheel_contact_false_pulses_s": {
            "0": [],
            "1": [],
            "2": [],
            "3": [],
        },
        "spinout": False,
        "spinout_max_duration_s": 0.0,
        "rollover": False,
        "rollover_max_duration_s": 0.0,
        "grounding": False,
        "body_contact_count": 0,
    }


def _check(
    checks: list[dict[str, object]],
    check_id: str,
    status: str,
    measured: object,
    expected: object,
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": status,
            "measured": measured,
            "expected": expected,
            "evidence": f"validator:{check_id}",
        }
    )


def _final_status(checks: list[dict[str, object]]) -> str:
    statuses = {item["status"] for item in checks}
    if "STOP" in statuses:
        return "STOP"
    if "FAIL" in statuses:
        return "FAIL"
    return "PASS"


def validate_trace(trace: object) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    derived = _empty_derived()
    if not isinstance(trace, dict):
        _check(checks, "trace_type", "STOP", type(trace).__name__, "object")
        return {"status": "STOP", "checks": checks, "derived": derived}

    missing_trace = sorted(REQUIRED_TRACE_FIELDS - set(trace))
    if missing_trace:
        _check(checks, "trace_required_fields", "STOP", missing_trace, [])
        return {"status": "STOP", "checks": checks, "derived": derived}

    if trace.get("schema") != TRACE_SCHEMA:
        _check(checks, "schema", "STOP", trace.get("schema"), TRACE_SCHEMA)
    if trace.get("mode") != "read":
        _check(checks, "mode", "STOP", trace.get("mode"), "read")
    trace_id = trace.get("trace_id")
    if not isinstance(trace_id, str) or TRACE_ID_PATTERN.fullmatch(trace_id) is None:
        _check(checks, "trace_id", "STOP", trace_id, "32 lowercase hex")
    for field in ("active", "complete", "overflow"):
        if not isinstance(trace.get(field), bool):
            _check(checks, f"{field}_type", "STOP", trace.get(field), "boolean")
    if trace.get("active") is not False:
        _check(checks, "trace_inactive", "STOP", trace.get("active"), False)
    if trace.get("complete") is not True:
        _check(checks, "trace_complete", "STOP", trace.get("complete"), True)
    if trace.get("overflow") is not False:
        _check(checks, "trace_overflow", "STOP", trace.get("overflow"), False)
    if not isinstance(trace.get("stop_reason"), str):
        _check(checks, "stop_reason_type", "STOP", trace.get("stop_reason"), "string")

    sample_hz = trace.get("sample_hz")
    if not _is_int(sample_hz) or not 20 <= sample_hz <= 60:
        _check(checks, "sample_hz", "STOP", sample_hz, "integer 20..60")
        safe_sample_hz = 20
    else:
        safe_sample_hz = sample_hz
    capacity = trace.get("capacity")
    if not _is_int(capacity) or not 2 <= capacity <= 8192:
        _check(checks, "capacity", "STOP", capacity, "integer 2..8192")
    count = trace.get("count")
    samples = trace.get("samples")
    if not _is_int(count) or count < 0 or count > 8192:
        _check(checks, "count", "STOP", count, "integer 0..8192")
        safe_count = 0
    else:
        safe_count = count
    if not isinstance(samples, list):
        _check(checks, "samples_type", "STOP", type(samples).__name__, "array")
        return {"status": "STOP", "checks": checks, "derived": derived}
    if safe_count != len(samples):
        _check(checks, "count_matches_samples", "STOP", len(samples), safe_count)
    if _is_int(capacity) and len(samples) > capacity:
        _check(checks, "capacity_not_exceeded", "STOP", len(samples), capacity)
    if len(samples) < 2:
        _check(checks, "minimum_samples", "STOP", len(samples), ">=2")
    cursor = trace.get("cursor")
    next_cursor = trace.get("next_cursor")
    eof = trace.get("eof")
    if not _is_int(cursor):
        _check(checks, "cursor_type", "STOP", cursor, "integer")
    elif cursor != 0:
        _check(checks, "cursor", "STOP", cursor, 0)
    if not _is_int(next_cursor):
        _check(checks, "next_cursor_type", "STOP", next_cursor, "integer")
    elif next_cursor != safe_count:
        _check(checks, "next_cursor", "STOP", next_cursor, safe_count)
    if not isinstance(eof, bool):
        _check(checks, "eof_type", "STOP", eof, "boolean")
    elif eof is not True:
        _check(checks, "eof", "STOP", eof, True)

    start_monotonic = trace.get("start_monotonic_s")
    if not _is_number(start_monotonic):
        _check(
            checks,
            "start_monotonic_s",
            "STOP",
            start_monotonic,
            "finite number",
        )
    if not isinstance(trace.get("owner_identity"), str):
        _check(
            checks,
            "owner_identity_type",
            "STOP",
            trace.get("owner_identity"),
            "string",
        )
    if not isinstance(trace.get("car_type"), str) or trace.get("car_type") == "":
        _check(checks, "car_type", "STOP", trace.get("car_type"), "non-empty string")
    for field in ("net_id_low", "net_id_high"):
        if not _is_int(trace.get(field)):
            _check(checks, f"{field}_type", "STOP", trace.get(field), "integer")

    previous_time: float | None = None
    gaps: list[float] = []
    sample_dt_sum = 0.0
    previous_wheels_present: int | None = None
    expected_wheel_loss = 0
    false_pulse_running = [0.0, 0.0, 0.0, 0.0]
    false_pulses: list[list[float]] = [[], [], [], []]
    spinout_duration = 0.0
    spinout_max = 0.0
    rollover_duration = 0.0
    rollover_max = 0.0
    body_contact_total = 0

    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            _check(checks, f"sample_{index}_type", "STOP", type(sample).__name__, "object")
            continue
        missing_sample = sorted(REQUIRED_SAMPLE_FIELDS - set(sample))
        if missing_sample:
            _check(
                checks,
                f"sample_{index}_required_fields",
                "STOP",
                missing_sample,
                [],
            )
            continue

        sample_types_valid = True
        for field in _BOOL_SAMPLE_FIELDS:
            if not isinstance(sample.get(field), bool):
                sample_types_valid = False
                _check(
                    checks,
                    f"sample_{index}_{field}_type",
                    "STOP",
                    sample.get(field),
                    "boolean",
                )
        for field in _INT_SAMPLE_FIELDS:
            if not _is_int(sample.get(field)):
                sample_types_valid = False
                _check(
                    checks,
                    f"sample_{index}_{field}_type",
                    "STOP",
                    sample.get(field),
                    "integer",
                )
        for field in _STRING_SAMPLE_FIELDS:
            if not isinstance(sample.get(field), str):
                sample_types_valid = False
                _check(
                    checks,
                    f"sample_{index}_{field}_type",
                    "STOP",
                    sample.get(field),
                    "string",
                )
        if not sample_types_valid:
            continue
        numeric_fields = (
            REQUIRED_SAMPLE_FIELDS
            - _BOOL_SAMPLE_FIELDS
            - _INT_SAMPLE_FIELDS
            - _STRING_SAMPLE_FIELDS
        )
        bad_numbers = sorted(
            field for field in numeric_fields if not _is_number(sample.get(field))
        )
        if bad_numbers:
            _check(
                checks,
                f"sample_{index}_finite_numbers",
                "STOP",
                bad_numbers,
                [],
            )
            continue

        if sample["sequence"] != index:
            _check(
                checks,
                f"sample_{index}_sequence",
                "STOP",
                sample["sequence"],
                index,
            )
        timestamp = float(sample["monotonic_s"])
        sample_dt = float(sample["sample_dt_s"])
        if index == 0:
            if abs(sample_dt) > 0.001 + TIME_EPS_S:
                _check(checks, "first_sample_dt", "STOP", sample_dt, 0.0)
            if _is_number(start_monotonic) and timestamp < float(start_monotonic):
                _check(
                    checks,
                    "first_sample_clock",
                    "STOP",
                    timestamp,
                    f">={start_monotonic}",
                )
        elif previous_time is not None:
            gap = timestamp - previous_time
            gaps.append(gap)
            sample_dt_sum += sample_dt
            if gap <= 0.0:
                _check(
                    checks,
                    f"sample_{index}_clock_monotonic",
                    "STOP",
                    gap,
                    ">0",
                )
            if abs(sample_dt - gap) > 0.001 + TIME_EPS_S:
                _check(
                    checks,
                    f"sample_{index}_dt",
                    "STOP",
                    sample_dt,
                    gap,
                )
        previous_time = timestamp

        if sample["is_owner"] is not True:
            _check(
                checks,
                f"sample_{index}_owner",
                "STOP",
                sample["is_owner"],
                True,
            )
        for field in ("net_id_low", "net_id_high"):
            if sample[field] != trace[field]:
                _check(
                    checks,
                    f"sample_{index}_{field}_stable",
                    "STOP",
                    sample[field],
                    trace[field],
                )

        if sample["control_active"]:
            for channel in _CONTROL_CHANNELS:
                requested = float(sample[f"{channel}_requested"])
                applied = float(sample[f"{channel}_applied"])
                if abs(requested - applied) > 0.001:
                    _check(
                        checks,
                        f"sample_{index}_{channel}_readback",
                        "STOP",
                        applied,
                        requested,
                    )

        wheel_count = sample["wheel_count"]
        wheels_present = sample["wheels_present"]
        if wheel_count < 0 or wheels_present < 0 or wheels_present > wheel_count:
            _check(
                checks,
                f"sample_{index}_wheel_counts",
                "STOP",
                [wheel_count, wheels_present],
                "0<=present<=count",
            )
        wheel_drop = (
            max(0, previous_wheels_present - wheels_present)
            if previous_wheels_present is not None
            else 0
        )
        expected_wheel_loss += wheel_drop
        if sample["wheel_loss_event"] != (wheel_drop > 0):
            _check(
                checks,
                f"sample_{index}_wheel_loss_event",
                "STOP",
                sample["wheel_loss_event"],
                wheel_drop > 0,
            )
        if sample["wheel_loss_count"] != expected_wheel_loss:
            _check(
                checks,
                f"sample_{index}_wheel_loss_count",
                "STOP",
                sample["wheel_loss_count"],
                expected_wheel_loss,
            )
        previous_wheels_present = wheels_present

        for wheel in range(4):
            if sample[f"wheel_contact_{wheel}"]:
                if false_pulse_running[wheel] > 0.0:
                    false_pulses[wheel].append(round(false_pulse_running[wheel], 6))
                    false_pulse_running[wheel] = 0.0
            else:
                false_pulse_running[wheel] += sample_dt

        contact_count = sample["contact_count"]
        body_contact_count = sample["body_contact_count"]
        if contact_count < 0 or body_contact_count < 0 or body_contact_count > contact_count:
            _check(
                checks,
                f"sample_{index}_contact_counts",
                "STOP",
                [contact_count, body_contact_count],
                "0<=body<=all",
            )
        body_contact_total += max(0, body_contact_count)

        vx = float(sample["velocity_x"])
        vz = float(sample["velocity_z"])
        dx = float(sample["direction_x"])
        dz = float(sample["direction_z"])
        speed_horizontal = math.hypot(vx, vz)
        direction_length = math.hypot(dx, dz)
        spinout_sample = False
        if speed_horizontal >= (30.0 / 3.6) and direction_length > 0.0:
            cosine = (vx * dx + vz * dz) / (speed_horizontal * direction_length)
            cosine = max(-1.0, min(1.0, cosine))
            spinout_sample = math.degrees(math.acos(cosine)) >= 45.0
        if spinout_sample:
            spinout_duration += sample_dt
            spinout_max = max(spinout_max, spinout_duration)
        else:
            spinout_duration = 0.0

        if abs(float(sample["roll_deg"])) >= 60.0:
            rollover_duration += sample_dt
            rollover_max = max(rollover_max, rollover_duration)
        else:
            rollover_duration = 0.0

    for wheel in range(4):
        if false_pulse_running[wheel] > 0.0:
            false_pulses[wheel].append(round(false_pulse_running[wheel], 6))

    max_gap = max(gaps, default=0.0)
    effective_hz = 0.0
    duration = 0.0
    if len(samples) >= 2:
        first = samples[0]
        last = samples[-1]
        if (
            isinstance(first, dict)
            and isinstance(last, dict)
            and _is_number(first.get("monotonic_s"))
            and _is_number(last.get("monotonic_s"))
        ):
            duration = float(last["monotonic_s"]) - float(first["monotonic_s"])
            if duration > 0.0:
                effective_hz = (len(samples) - 1) / duration
    allowed_gap = 1.5 / safe_sample_hz
    required_hz = max(20.0, 0.9 * safe_sample_hz)
    if max_gap > allowed_gap + TIME_EPS_S:
        _check(checks, "max_gap", "STOP", max_gap, f"<={allowed_gap}")
    if effective_hz + TIME_EPS_S < required_hz:
        _check(checks, "effective_hz", "STOP", effective_hz, f">={required_hz}")
    if duration > 0.0:
        time_balance_tolerance = max(0.01 * duration, 0.1)
        time_balance_error = abs(duration - sample_dt_sum)
        if time_balance_error > time_balance_tolerance + TIME_EPS_S:
            _check(
                checks,
                "time_balance",
                "STOP",
                time_balance_error,
                f"<={time_balance_tolerance}",
            )

    derived = {
        "effective_hz": round(effective_hz, 6),
        "max_gap_s": round(max_gap, 6),
        "wheel_contact_false_pulses_s": {
            str(wheel): false_pulses[wheel] for wheel in range(4)
        },
        "spinout": spinout_max + TIME_EPS_S >= 0.25,
        "spinout_max_duration_s": round(spinout_max, 6),
        "rollover": rollover_max + TIME_EPS_S >= 0.5,
        "rollover_max_duration_s": round(rollover_max, 6),
        "grounding": body_contact_total > 0,
        "body_contact_count": body_contact_total,
    }

    declared = trace.get("declared_derived")
    if declared is not None:
        if not isinstance(declared, dict):
            _check(checks, "declared_derived_type", "FAIL", type(declared).__name__, "object")
        else:
            for key, value in declared.items():
                if key not in derived or derived[key] != value:
                    _check(
                        checks,
                        f"declared_derived_{key}",
                        "FAIL",
                        value,
                        derived.get(key),
                    )

    if not checks:
        _check(checks, "trace_contract", "PASS", "valid", "valid")
    return {
        "status": _final_status(checks),
        "checks": checks,
        "derived": derived,
    }


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_artifact_bytes(artifact: dict[str, object]) -> bytes:
    return canonical_json_bytes(artifact)


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ArtifactInputError(f"{label}_unreadable") from exc


def _read_json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactInputError(f"{label}_invalid_json") from exc
    if not isinstance(value, dict):
        raise ArtifactInputError(f"{label}_invalid_type")
    return value


def _validate_lifecycle(lifecycle: dict[str, object]) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    run_id = lifecycle.get("run_id")
    processes = lifecycle.get("processes")
    if not isinstance(run_id, str) or run_id == "":
        _check(checks, "lifecycle_run_id", "STOP", run_id, "non-empty string")
    if not isinstance(processes, list):
        _check(checks, "lifecycle_processes", "STOP", type(processes).__name__, "array")
        return checks

    roles: set[str] = set()
    required = {
        "pid",
        "creation_time_utc",
        "executable_sha256",
        "command_line_sha256",
        "role",
        "identity_scheme",
    }
    for index, process in enumerate(processes):
        if not isinstance(process, dict):
            _check(checks, f"lifecycle_process_{index}", "STOP", type(process).__name__, "object")
            continue
        missing = sorted(required - set(process))
        if missing:
            _check(checks, f"lifecycle_process_{index}_fields", "STOP", missing, [])
            continue
        if not _is_int(process["pid"]) or process["pid"] <= 0:
            _check(checks, f"lifecycle_process_{index}_pid", "STOP", process["pid"], ">0 integer")
        for field in ("creation_time_utc", "role", "identity_scheme"):
            if not isinstance(process[field], str) or process[field] == "":
                _check(
                    checks,
                    f"lifecycle_process_{index}_{field}",
                    "STOP",
                    process[field],
                    "non-empty string",
                )
        for field in ("executable_sha256", "command_line_sha256"):
            value = process[field]
            if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
                _check(
                    checks,
                    f"lifecycle_process_{index}_{field}",
                    "STOP",
                    value,
                    "64 hex",
                )
        if isinstance(process["role"], str):
            roles.add(process["role"])
    if not {"server", "client"}.issubset(roles):
        _check(checks, "lifecycle_roles", "STOP", sorted(roles), ["client", "server"])
    return checks


def _trace_duration_s(trace: dict[str, object]) -> float | None:
    samples = trace.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        return None
    first = samples[0]
    last = samples[-1]
    if (
        not isinstance(first, dict)
        or not isinstance(last, dict)
        or not _is_number(first.get("monotonic_s"))
        or not _is_number(last.get("monotonic_s"))
    ):
        return None
    duration = float(last["monotonic_s"]) - float(first["monotonic_s"])
    return duration if duration >= 0.0 else None


def _validate_course(
    course: dict[str, object],
    trace: dict[str, object],
    trace_result: dict[str, object],
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    required = {
        "schema",
        "course_id",
        "vehicle_type",
        "requested_sample_hz",
        "minimum_duration_s",
        "controls",
        "required_observations",
    }
    missing = sorted(required - set(course))
    if missing:
        _check(checks, "course_required_fields", "STOP", missing, [])
        return checks

    _check(
        checks,
        "course_schema",
        "PASS" if course.get("schema") == COURSE_SCHEMA else "STOP",
        course.get("schema"),
        COURSE_SCHEMA,
    )
    _check(
        checks,
        "course_id",
        "PASS" if course.get("course_id") == CONTROL_COURSE_ID else "STOP",
        course.get("course_id"),
        CONTROL_COURSE_ID,
    )
    vehicle_measurement = [course.get("vehicle_type"), trace.get("car_type")]
    _check(
        checks,
        "course_vehicle_type",
        (
            "PASS"
            if vehicle_measurement == [CONTROL_VEHICLE_TYPE, CONTROL_VEHICLE_TYPE]
            else "STOP"
        ),
        vehicle_measurement,
        [CONTROL_VEHICLE_TYPE, CONTROL_VEHICLE_TYPE],
    )
    sample_hz_measurement = [
        course.get("requested_sample_hz"),
        trace.get("sample_hz"),
    ]
    _check(
        checks,
        "course_requested_sample_hz",
        (
            "PASS"
            if sample_hz_measurement == [CONTROL_SAMPLE_HZ, CONTROL_SAMPLE_HZ]
            else "STOP"
        ),
        sample_hz_measurement,
        [CONTROL_SAMPLE_HZ, CONTROL_SAMPLE_HZ],
    )

    minimum_duration = course.get("minimum_duration_s")
    minimum_valid = _is_number(minimum_duration) and float(minimum_duration) >= 2.0
    _check(
        checks,
        "course_minimum_duration_contract",
        "PASS" if minimum_valid else "STOP",
        minimum_duration,
        "finite number >=2.0",
    )
    duration = _trace_duration_s(trace)
    duration_valid = (
        minimum_valid
        and duration is not None
        and duration + TIME_EPS_S >= float(minimum_duration)
    )
    _check(
        checks,
        "course_minimum_duration_s",
        "PASS" if duration_valid else "STOP",
        duration,
        minimum_duration,
    )

    controls = course.get("controls")
    control_fields = {"at_s", *_CONTROL_CHANNELS}
    normalized_controls: list[dict[str, float]] = []
    controls_valid = isinstance(controls, list) and len(controls) > 0
    last_control_s: float | None = None
    if controls_valid:
        for control in controls:
            if (
                not isinstance(control, dict)
                or set(control) != control_fields
                or not all(_is_number(control.get(field)) for field in control_fields)
            ):
                controls_valid = False
                break
            at_s = float(control["at_s"])
            normalized_control = {
                field: float(control[field])
                for field in control_fields
            }
            if (
                at_s < 0.0
                or (last_control_s is None and abs(at_s) > TIME_EPS_S)
                or (last_control_s is not None and at_s <= last_control_s)
                or not 0.0 <= normalized_control["throttle"] <= 1.0
                or not -1.0 <= normalized_control["steer"] <= 1.0
                or not 0.0 <= normalized_control["brake"] <= 1.0
                or normalized_control["handbrake"] not in (0.0, 1.0)
            ):
                controls_valid = False
                break
            normalized_controls.append(normalized_control)
            last_control_s = at_s
    if minimum_valid and (
        last_control_s is None
        or last_control_s + TIME_EPS_S < float(minimum_duration)
    ):
        controls_valid = False
    if (
        duration is not None
        and last_control_s is not None
        and last_control_s > duration + TIME_EPS_S
    ):
        controls_valid = False
    _check(
        checks,
        "course_controls",
        "PASS" if controls_valid else "STOP",
        {
            "count": len(normalized_controls),
            "last_at_s": last_control_s,
        },
        "exact fields, finite/ranged values, start=0, strictly increasing, "
        f"{minimum_duration}<=last_at_s<=trace duration",
    )
    normalized_schedule = tuple(
        (
            control["at_s"],
            *(control[channel] for channel in _CONTROL_CHANNELS),
        )
        for control in normalized_controls
    )
    canonical_controls_valid = (
        controls_valid
        and normalized_schedule == _CANONICAL_CONTROL_SCHEDULE
    )
    _check(
        checks,
        "course_controls_canonical",
        "PASS" if canonical_controls_valid else "STOP",
        normalized_schedule,
        _CANONICAL_CONTROL_SCHEDULE,
    )

    observations = course.get("required_observations")
    observations_valid = (
        isinstance(observations, list)
        and all(isinstance(item, str) for item in observations)
        and len(observations) == len(set(observations))
    )
    observation_set = set(observations) if observations_valid else set()
    expected_observations = set(REQUIRED_CONTROL_OBSERVATIONS)
    _check(
        checks,
        "course_required_observations",
        (
            "PASS"
            if observations_valid and observation_set == expected_observations
            else "STOP"
        ),
        sorted(observation_set),
        sorted(expected_observations),
    )

    samples = trace.get("samples")
    safe_samples = samples if isinstance(samples, list) else []
    owner_stable = bool(safe_samples) and all(
        isinstance(sample, dict) and sample.get("is_owner") is True
        for sample in safe_samples
    )
    net_id_stable = bool(safe_samples) and all(
        isinstance(sample, dict)
        and sample.get("net_id_low") == trace.get("net_id_low")
        and sample.get("net_id_high") == trace.get("net_id_high")
        for sample in safe_samples
    )
    control_samples = [
        sample
        for sample in safe_samples
        if isinstance(sample, dict) and sample.get("control_active") is True
    ]
    control_readback = bool(control_samples) and all(
        all(
            _is_number(sample.get(f"{channel}_requested"))
            and _is_number(sample.get(f"{channel}_applied"))
            and abs(
                float(sample[f"{channel}_requested"])
                - float(sample[f"{channel}_applied"])
            )
            <= 0.001
            for channel in _CONTROL_CHANNELS
        )
        for sample in control_samples
    )
    if canonical_controls_valid:
        first_sample = safe_samples[0] if safe_samples else None
        first_time = (
            float(first_sample["monotonic_s"])
            if isinstance(first_sample, dict)
            and _is_number(first_sample.get("monotonic_s"))
            else None
        )
        transition_tolerance_s = max(1.5 / CONTROL_SAMPLE_HZ, 0.05)
        for index, expected_control in enumerate(normalized_controls):
            at_s = expected_control["at_s"]
            matching_sequences: list[int] = []
            for sample in control_samples:
                if (
                    first_time is None
                    or not _is_number(sample.get("monotonic_s"))
                    or not _is_int(sample.get("sequence"))
                ):
                    continue
                elapsed_s = float(sample["monotonic_s"]) - first_time
                if abs(elapsed_s - at_s) > transition_tolerance_s + TIME_EPS_S:
                    continue
                if all(
                    _is_number(sample.get(f"{channel}_requested"))
                    and _is_number(sample.get(f"{channel}_applied"))
                    and abs(
                        float(sample[f"{channel}_requested"])
                        - expected_control[channel]
                    )
                    <= 0.001
                    and abs(
                        float(sample[f"{channel}_applied"])
                        - expected_control[channel]
                    )
                    <= 0.001
                    for channel in _CONTROL_CHANNELS
                ):
                    matching_sequences.append(int(sample["sequence"]))
            _check(
                checks,
                f"course_control_{index}_observed",
                "PASS" if matching_sequences else "STOP",
                {
                    "at_s": at_s,
                    "matching_sequences": matching_sequences,
                },
                {
                    "transition_tolerance_s": transition_tolerance_s,
                    "requested_and_applied": {
                        channel: expected_control[channel]
                        for channel in _CONTROL_CHANNELS
                    },
                },
            )
    derived = trace_result.get("derived")
    body_contact_owner_client = (
        owner_stable
        and isinstance(derived, dict)
        and derived.get("grounding") is True
    )
    observation_results = {
        "owner_stable": owner_stable,
        "net_id_stable": net_id_stable,
        "control_readback": control_readback,
        "body_contact_owner_client": body_contact_owner_client,
    }
    for observation in sorted(REQUIRED_CONTROL_OBSERVATIONS):
        satisfied = observation_results[observation]
        _check(
            checks,
            f"course_observation_{observation}",
            "PASS" if satisfied else "STOP",
            satisfied,
            True,
        )
    return checks


def build_artifact(
    *,
    trace_path: Path,
    schema_path: Path,
    course_path: Path,
    pbo_path: Path,
    rpt_path: Path,
    script_log_path: Path,
    lifecycle_path: Path,
) -> dict[str, object]:
    trace_raw = _read_bytes(trace_path, "trace")
    schema_raw = _read_bytes(schema_path, "schema")
    course_raw = _read_bytes(course_path, "course")
    pbo_raw = _read_bytes(pbo_path, "pbo")
    rpt_raw = _read_bytes(rpt_path, "rpt")
    script_log_raw = _read_bytes(script_log_path, "script_log")
    lifecycle_raw = _read_bytes(lifecycle_path, "lifecycle")

    trace = _read_json_object(trace_raw, "trace")
    schema = _read_json_object(schema_raw, "schema")
    course = _read_json_object(course_raw, "course")
    lifecycle = _read_json_object(lifecycle_raw, "lifecycle")
    if schema.get("$id") != TRACE_SCHEMA:
        raise ArtifactInputError("schema_id_mismatch")

    trace_result = validate_trace(trace)
    checks = list(trace_result["checks"])
    checks.extend(_validate_course(course, trace, trace_result))
    checks.extend(_validate_lifecycle(lifecycle))
    status = _final_status(checks)
    artifact: dict[str, object] = {
        "schema": ARTIFACT_SCHEMA,
        "status": status,
        "run_id": lifecycle.get("run_id"),
        "processes": lifecycle.get("processes"),
        "trace": {
            "schema": trace.get("schema"),
            "trace_id": trace.get("trace_id"),
            "car_type": trace.get("car_type"),
            "sample_hz": trace.get("sample_hz"),
            "count": trace.get("count"),
            "net_id_low": trace.get("net_id_low"),
            "net_id_high": trace.get("net_id_high"),
        },
        "derived": trace_result["derived"],
        "checks": checks,
        "hashes": {
            "trace_sha256": hashlib.sha256(trace_raw).hexdigest(),
            "schema_sha256": hashlib.sha256(schema_raw).hexdigest(),
            "course_sha256": hashlib.sha256(course_raw).hexdigest(),
            "pbo_sha256": hashlib.sha256(pbo_raw).hexdigest(),
            "rpt_sha256": hashlib.sha256(rpt_raw).hexdigest(),
            "script_log_sha256": hashlib.sha256(script_log_raw).hexdigest(),
        },
    }
    artifact["artifact_sha256"] = hashlib.sha256(canonical_json_bytes(artifact)).hexdigest()
    return artifact
