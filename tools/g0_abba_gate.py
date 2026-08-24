#!/usr/bin/env python3
"""DayZ MCP G0 ABBA diagnostic gate.

Drives an already-running broker daemon through raw authenticated HTTP. It does
not launch, stop, or discover DayZ processes. The module is self-contained and
uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_KEYFILE = SCRIPT_DIR / ".dayz_mcp.key"
DEFAULT_OUT = SCRIPT_DIR / "_g0_abba_verdict.json"

# Sites shifted 40 m south of the G0 diagnosis originals on 2026-08-24: the
# full-throttle ABBA drive (~91 m north) parked the car inside a map-object
# cluster at z~2063 (entities_query: statics at 3.2/7.4/9.3 m around the stop),
# leaving ActionGetOutTransport blocked (exit condition false, consistent with
# an occupied door area) and the site unfinishable. Same line, same surfaces,
# drives now end 40-80 m clear.
CONTROL_SITE = [6063.01416015625, 0.0, 1931.696044921875]
RED_SITE = [6063.095703125, 0.0, 1891.7137451171875]
CONTROL_END = [6062.75634765625, 0.0, 1967.4820556640625]
ABBA_SITES = (
    ("CONTROL", CONTROL_SITE),
    ("RED", RED_SITE),
    ("RED", RED_SITE),
    ("CONTROL", CONTROL_SITE),
)

VEHICLE_TYPE = "CivilianSedan"
TOOL_TIMEOUT_S = 30.0
TRACE_SAMPLE_HZ = 20
TRACE_MAX_SAMPLES = 256
TRACE_PAGE_LIMIT = 64
TRACE_WALL_LIMIT_S = 7.5
CONTROL_HOLD_TTL_S = 8.0
THROTTLE_TOLERANCE = 0.001
SAMPLE_TIME_TOLERANCE_S = 0.075
MIN_EFFECTIVE_HZ = 20.0
DELTA_2S_THRESHOLD_M = 1.0
DIRECTION_DOT_XZ_MIN = 0.99
WHEEL_SPIN_EPS = 1e-6
PEER_STALE_S = 15.0

TREE_VERDICTS = {
    "H2_POSITION_ISOLATED_OBSERVED_MECHANISM": (
        "H2 aislada a nivel de posición. La mecánica concreta sigue abierta "
        "entre obstáculo, suelo y contacto; no tocar drivetrain."
    ),
    "NO_REPRODUCE_G0_PASS": (
        "El rojo antiguo no reproduce con el build actual; H1 histórica/build "
        "o command path anterior queda primera. G0 actual PASS, sin afirmar qué "
        "bytes produjeron el JSON antiguo."
    ),
    "G0_RED_H4_TIMING": (
        "G0 sigue rojo; H4/timing sube. El movimiento tardío refuta bloqueo "
        "físico permanente y no satisface los dos segundos contractuales."
    ),
    "H1_CONTROL_NOT_OBSERVABLE": (
        "H1-holder/target/TTL en el runtime actual. El command fue aceptado pero "
        "el holder no estuvo activo sobre esas entidades."
    ),
    "INCONCLUSIVE_RUNTIME_VARIANCE": (
        "INCONCLUSIVE_RUNTIME_VARIANCE; el actuador/holder es intermitente y la "
        "comparación de posición no es válida."
    ),
    "H1_H4_JOINT": (
        "H1/H4 aún juntas. Si engine_on flapea, H4 sube; si engine_on permanece "
        "true, hace falta RPM o prueba de contenido del PBO. No declarar causa raíz."
    ),
    "H3_NEUTRAL_GEAR": "H3-marcha neutral.",
    "H3_TRANSMISSION_PHYSICS": "H3-transmisión/física.",
    "H2_POSITION_ISOLATED_MECHANISM_UNOBSERVED": (
        "H2 aislada a nivel de posición, mecanismo no observado; inspeccionar "
        "geometría/suelo antes de tocar código."
    ),
    "H5_SETTLE": (
        "H5-settle medido; el delta no es propulsión. Buscar H1/H4 para explicar "
        "por qué no hubo input aplicado."
    ),
    "INCONCLUSIVE_ENTITY_VARIANCE": (
        "INCONCLUSIVE_ENTITY_VARIANCE; el efecto no está aislado por posición."
    ),
    "INCONCLUSIVE_SETUP_FAILED": (
        "INCONCLUSO/SETUP_FAILED, no rojo de drivability."
    ),
}

_FENCE_BLOCK_READY = {
    "AMBIGUOUS": "binding_ambiguous",
    "STARTING": "binding_not_ready",
    "RETIRED": "binding_retired",
    "binding_retired": "binding_retired",
    "instance_unknown": "instance_unknown",
    "unbound_after_restart": "unbound_after_restart",
    "instance_unattributed": "instance_unattributed",
    "instance_role_mismatch": "instance_role_mismatch",
    "instance_malformed": "instance_malformed",
    "instance_peer_collision": "instance_peer_collision",
    "creation_time_unreadable": "creation_time_unreadable",
}


class GateFailure(RuntimeError):
    """Typed driver failure that must not be interpreted as drivability."""

    def __init__(self, code: str, field: str, detail: object):
        super().__init__(f"{code}:{field}:{detail}")
        self.code = code
        self.field = field
        self.detail = detail

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "field": self.field, "detail": self.detail}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _within(value: object, target: object, tolerance: float) -> bool:
    if not _is_number(value) or not _is_number(target):
        return False
    return abs(Decimal(str(value)) - Decimal(str(target))) <= Decimal(str(tolerance))


def _is_true(value: object) -> bool:
    return value is True or (type(value) is int and value == 1)


def _is_false(value: object) -> bool:
    return value is False or (type(value) is int and value == 0)


def _typed_absence(absence_type: str, **detail: object) -> dict[str, object]:
    return {"present": False, "absence_type": absence_type, **detail}


def _is_absence(value: object) -> bool:
    return isinstance(value, dict) and value.get("present") is False


def _sample_time(sample: object) -> float | None:
    if not isinstance(sample, dict) or not _is_number(sample.get("monotonic_s")):
        return None
    return float(sample["monotonic_s"])


def _position(sample: object) -> tuple[float, float, float]:
    if not isinstance(sample, dict):
        raise ValueError("sample must be an object")
    values = (
        sample.get("position_x"),
        sample.get("position_y"),
        sample.get("position_z"),
    )
    if not all(_is_number(value) for value in values):
        raise ValueError("sample position must contain three finite numbers")
    return tuple(float(value) for value in values)


def extract_s0_s2_s5(samples: list[dict]) -> dict[str, object]:
    """Select contractual S0/S2/S5 without transforming the source samples."""

    s0: dict | None = None
    for row in samples:
        if not isinstance(row, dict):
            continue
        requested = row.get("throttle_requested")
        if (
            _is_true(row.get("control_active"))
            and _within(requested, 1.0, THROTTLE_TOLERANCE)
        ):
            s0 = row
            break

    if s0 is None:
        return {
            "status": "H1_CONTROL_NOT_OBSERVABLE",
            "S0": _typed_absence("CONTROL_NOT_OBSERVABLE"),
            "S2": _typed_absence("NO_S0"),
            "S5": _typed_absence("NO_S0"),
        }

    s0_time = _sample_time(s0)
    if s0_time is None:
        return {
            "status": "TRACE_SETUP_FAILED",
            "S0": _typed_absence("S0_BAD_MONOTONIC"),
            "S2": _typed_absence("NO_VALID_S0"),
            "S5": _typed_absence("NO_VALID_S0"),
        }

    selected: dict[str, object] = {"status": "PASS", "S0": s0}
    for label, offset in (("S2", 2.0), ("S5", 5.0)):
        target = s0_time + offset
        reached: dict | None = None
        reached_time: float | None = None
        for row in samples:
            row_time = _sample_time(row)
            if row_time is not None and row_time >= target:
                reached = row
                reached_time = row_time
                break
        if reached is None or reached_time is None:
            selected[label] = _typed_absence(
                f"{label}_MISSING", target_monotonic_s=target
            )
            selected["status"] = "TRACE_SETUP_FAILED"
        elif reached_time - target > SAMPLE_TIME_TOLERANCE_S:
            selected[label] = _typed_absence(
                f"{label}_OUTSIDE_TOLERANCE",
                target_monotonic_s=target,
                observed_monotonic_s=reached_time,
                lateness_s=reached_time - target,
            )
            selected["status"] = "TRACE_SETUP_FAILED"
        else:
            selected[label] = reached
    return selected


def cell_deltas(s0: dict, s2: dict, s5: dict) -> dict[str, float]:
    """Return independent 3D and horizontal displacements from raw samples."""

    p0 = _position(s0)
    p2 = _position(s2)
    p5 = _position(s5)

    def distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float]:
        dx = right[0] - left[0]
        dy = right[1] - left[1]
        dz = right[2] - left[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz), math.hypot(dx, dz)

    d2_3d, d2_xz = distance(p0, p2)
    d5_3d, d5_xz = distance(p0, p5)
    return {
        "delta_2s_3d": d2_3d,
        "delta_2s_xz": d2_xz,
        "delta_5s_3d": d5_3d,
        "delta_5s_xz": d5_xz,
    }


def trace_sample_gate(samples: list[dict], s0: dict, s5: dict) -> dict[str, object]:
    """Check the contractual gap and effective-rate window from S0 through S5."""

    start = _sample_time(s0)
    end = _sample_time(s5)
    if start is None or end is None or end <= start:
        return {
            "status": "TRACE_SETUP_FAILED",
            "field": "sample_window",
            "max_sample_gap_s": None,
            "effective_hz": 0.0,
        }
    window = [
        row
        for row in samples
        if (_sample_time(row) is not None)
        and start <= float(row["monotonic_s"]) <= end
    ]
    if len(window) < 2:
        return {
            "status": "TRACE_SETUP_FAILED",
            "field": "sample_window",
            "max_sample_gap_s": None,
            "effective_hz": 0.0,
        }
    times = [float(row["monotonic_s"]) for row in window]
    gaps = [right - left for left, right in zip(times, times[1:])]
    max_gap = max(gaps)
    duration = times[-1] - times[0]
    effective_hz = (len(times) - 1) / duration if duration > 0.0 else 0.0
    status = "PASS"
    field = None
    if any(gap <= 0.0 for gap in gaps):
        status, field = "TRACE_SETUP_FAILED", "sample_clock"
    elif max_gap > SAMPLE_TIME_TOLERANCE_S:
        status, field = "TRACE_SETUP_FAILED", "max_sample_gap_s"
    elif effective_hz < MIN_EFFECTIVE_HZ:
        status, field = "TRACE_SETUP_FAILED", "effective_hz"
    return {
        "status": status,
        "field": field,
        "sample_count": len(window),
        "max_sample_gap_s": max_gap,
        "effective_hz": effective_hz,
    }


def _initial_sample(cell: dict) -> dict | None:
    s0 = cell.get("S0")
    if isinstance(s0, dict) and not _is_absence(s0):
        return s0
    samples = cell.get("samples")
    if isinstance(samples, list):
        return next((row for row in samples if isinstance(row, dict)), None)
    return None


def _normalized_direction_xz(sample: dict) -> tuple[float, float] | None:
    dx, dz = sample.get("direction_x"), sample.get("direction_z")
    if not _is_number(dx) or not _is_number(dz):
        return None
    length = math.hypot(float(dx), float(dz))
    if length <= 0.0:
        return None
    return float(dx) / length, float(dz) / length


def _result(status: str, field: str | None = None, **detail: object) -> dict[str, object]:
    return {"status": status, "field": field, **detail}


def comparability_gate(cells: list[dict]) -> dict[str, object]:
    """Validate ABBA comparability before any causal verdict is allowed."""

    if len(cells) != 4:
        return _result("SETUP_FAILED", "cell_count", observed=len(cells), expected=4)
    sites = [cell.get("site") for cell in cells]
    if sites != ["CONTROL", "RED", "RED", "CONTROL"]:
        return _result("SETUP_FAILED", "site_sequence", observed=sites)

    for index, cell in enumerate(cells, start=1):
        gates = cell.get("trace_gates")
        if not isinstance(gates, dict) or gates.get("status") != "PASS":
            return _result(
                "SETUP_FAILED",
                "trace_gates",
                cell=index,
                observed=gates,
            )
        owner = cell.get("owner_identity")
        if not isinstance(owner, str) or not owner:
            return _result("SETUP_FAILED", "owner_identity", cell=index)
        if cell.get("trace_owner_identity") != owner:
            return _result("SETUP_FAILED", "trace_owner_identity", cell=index)
        net_id = cell.get("net_id")
        if (
            not isinstance(net_id, list)
            or len(net_id) != 2
            or not all(type(value) is int for value in net_id)
        ):
            return _result("SETUP_FAILED", "net_id", cell=index)
        if cell.get("trace_net_id") != net_id:
            return _result("SETUP_FAILED", "trace_net_id", cell=index)
        initial = _initial_sample(cell)
        if initial is None:
            return _result("SETUP_FAILED", "initial_sample", cell=index)
        if not _is_true(initial.get("is_owner")):
            return _result("SETUP_FAILED", "is_owner", cell=index)
        if [initial.get("net_id_low"), initial.get("net_id_high")] != net_id:
            return _result("SETUP_FAILED", "sample_net_id", cell=index)
        wheel_count = initial.get("wheel_count")
        wheels_present = initial.get("wheels_present")
        if (
            type(wheel_count) is not int
            or wheel_count < 0
            or type(wheels_present) is not int
            or wheels_present < 0
        ):
            return _result(
                "SETUP_FAILED",
                "wheel_counts",
                cell=index,
                observed=[wheel_count, wheels_present],
            )

    owners = {str(cell["owner_identity"]) for cell in cells}
    if len(owners) != 1:
        return _result("SETUP_FAILED", "owner_identity", observed=sorted(owners))

    wheel_counts = {
        (
            _initial_sample(cell).get("wheel_count"),
            _initial_sample(cell).get("wheels_present"),
        )
        for cell in cells
    }
    if len(wheel_counts) != 1:
        return _result(
            "INCONCLUSIVE_ENTITY_VARIANCE",
            "wheel_counts",
            observed=[list(value) for value in sorted(wheel_counts)],
        )

    directions: list[tuple[float, float]] = []
    for index, cell in enumerate(cells, start=1):
        direction = _normalized_direction_xz(_initial_sample(cell))
        if direction is None:
            return _result("SETUP_FAILED", "direction_xz", cell=index)
        directions.append(direction)
    pairwise = [
        {
            "cells": [left + 1, right + 1],
            "dot_xz": directions[left][0] * directions[right][0]
            + directions[left][1] * directions[right][1],
        }
        for left, right in itertools.combinations(range(4), 2)
    ]
    minimum_dot = min(item["dot_xz"] for item in pairwise)
    if minimum_dot < DIRECTION_DOT_XZ_MIN:
        return _result(
            "SETUP_FAILED",
            "direction_xz_dot",
            minimum_dot_xz=minimum_dot,
            required_minimum=DIRECTION_DOT_XZ_MIN,
            pairwise=pairwise,
        )

    s0_present = [
        isinstance(cell.get("S0"), dict) and not _is_absence(cell.get("S0"))
        for cell in cells
    ]
    if not any(s0_present):
        return _result(
            "PASS",
            None,
            minimum_dot_xz=minimum_dot,
            pairwise=pairwise,
            delta_comparison_skipped=True,
            reason="H1_CONTROL_NOT_OBSERVABLE",
        )
    if not all(s0_present):
        return _result(
            "INCONCLUSIVE_RUNTIME_VARIANCE",
            "S0_presence",
            observed=s0_present,
            minimum_dot_xz=minimum_dot,
        )
    if not all(
        isinstance(cell.get("S2"), dict) and not _is_absence(cell.get("S2"))
        for cell in cells
    ):
        return _result("SETUP_FAILED", "S2_presence")

    for site in ("CONTROL", "RED"):
        replicas = [cell for cell in cells if cell.get("site") == site]
        passes = [
            float(cell["deltas"]["delta_2s_3d"]) > DELTA_2S_THRESHOLD_M
            for cell in replicas
        ]
        if len(set(passes)) != 1:
            return _result(
                "INCONCLUSIVE_ENTITY_VARIANCE",
                "replica_delta_verdict",
                site=site,
                observed=passes,
                threshold_m=DELTA_2S_THRESHOLD_M,
            )
    return _result(
        "PASS",
        None,
        minimum_dot_xz=minimum_dot,
        pairwise=pairwise,
        delta_comparison_skipped=False,
    )


def _selected_samples(cell: dict) -> list[dict]:
    return [
        row
        for key in ("S0", "S2", "S5")
        if isinstance((row := cell.get(key)), dict) and not _is_absence(row)
    ]


def _window_samples(cell: dict) -> list[dict]:
    selected = _selected_samples(cell)
    start = _sample_time(cell.get("S0"))
    end = _sample_time(cell.get("S5"))
    samples = cell.get("samples")
    if start is None or end is None or not isinstance(samples, list):
        return selected
    window = [
        row
        for row in samples
        if isinstance(row, dict)
        and (row_time := _sample_time(row)) is not None
        and start <= row_time <= end
    ]
    return window or selected


def _all_selected(cells: list[dict], predicate) -> bool:
    rows = [row for cell in cells for row in _window_samples(cell)]
    return bool(rows) and all(predicate(row) for row in rows)


def _d2_pass(cell: dict) -> bool:
    return float(cell["deltas"]["delta_2s_3d"]) > DELTA_2S_THRESHOLD_M


def _d5_pass(cell: dict) -> bool:
    return float(cell["deltas"]["delta_5s_3d"]) > DELTA_2S_THRESHOLD_M


def _requested_applied_agree(row: dict) -> bool:
    requested, applied = row.get("throttle_requested"), row.get("throttle_applied")
    return _within(applied, requested, THROTTLE_TOLERANCE)


def _observed_contact_or_spin(cell: dict) -> bool:
    for row in _window_samples(cell):
        contacts = row.get("body_contact_count")
        if type(contacts) is int and contacts > 0:
            return True
        for wheel in range(4):
            value = row.get(f"wheel_angular_velocity_{wheel}")
            if _is_number(value) and abs(float(value)) > WHEEL_SPIN_EPS:
                return True
    return False


def _is_settle_cell(cell: dict) -> bool:
    deltas = cell["deltas"]
    d3 = float(deltas["delta_2s_3d"])
    dxz = float(deltas["delta_2s_xz"])
    rows = _window_samples(cell)
    near_zero_xz = bool(rows) and all(
        _is_number(row.get("velocity_x"))
        and _is_number(row.get("velocity_z"))
        and math.hypot(
            float(row["velocity_x"]),
            float(row["velocity_z"]),
        )
        <= 0.1
        for row in rows
    )
    applied_zero = bool(rows) and all(
        _within(row.get("throttle_applied"), 0.0, THROTTLE_TOLERANCE)
        for row in rows
    )
    vertical_dominates = d3 > max(2.0 * dxz, dxz + 0.05)
    return vertical_dominates and near_zero_xz and applied_zero


def _verdict(row: str, comparability: dict[str, object], **detail: object) -> dict[str, object]:
    return {
        "row": row,
        "verdict": TREE_VERDICTS[row],
        "comparability_status": comparability.get("status"),
        **detail,
    }


def verdict_tree(cells: list[dict]) -> dict[str, object]:
    """Apply section 4.5 only after the independent comparability gate."""

    comparable = comparability_gate(cells)
    status = comparable.get("status")
    if status == "INCONCLUSIVE_RUNTIME_VARIANCE":
        return _verdict(
            "INCONCLUSIVE_RUNTIME_VARIANCE",
            comparable,
            field=comparable.get("field"),
        )
    if status == "INCONCLUSIVE_ENTITY_VARIANCE":
        return _verdict(
            "INCONCLUSIVE_ENTITY_VARIANCE",
            comparable,
            field=comparable.get("field"),
        )
    if status != "PASS":
        return _verdict(
            "INCONCLUSIVE_SETUP_FAILED",
            comparable,
            field=comparable.get("field"),
        )

    s0_present = [
        isinstance(cell.get("S0"), dict) and not _is_absence(cell.get("S0"))
        for cell in cells
    ]
    if not any(s0_present):
        return _verdict("H1_CONTROL_NOT_OBSERVABLE", comparable)

    controls = [cell for cell in cells if cell.get("site") == "CONTROL"]
    reds = [cell for cell in cells if cell.get("site") == "RED"]
    signals_agree = _all_selected(cells, _requested_applied_agree)
    h2_shape = (
        all(_d2_pass(cell) for cell in controls)
        and all(not _d2_pass(cell) and not _d5_pass(cell) for cell in reds)
        and signals_agree
    )
    if h2_shape:
        observed = [_observed_contact_or_spin(cell) for cell in reds]
        if all(observed):
            return _verdict("H2_POSITION_ISOLATED_OBSERVED_MECHANISM", comparable)
        if not any(observed):
            return _verdict(
                "H2_POSITION_ISOLATED_MECHANISM_UNOBSERVED", comparable
            )
        return _verdict(
            "INCONCLUSIVE_ENTITY_VARIANCE",
            comparable,
            field="red_contact_or_wheel_spin",
        )

    if all(_d2_pass(cell) for cell in cells):
        return _verdict("NO_REPRODUCE_G0_PASS", comparable)

    if all(not _d2_pass(cell) and _d5_pass(cell) for cell in cells):
        transitioned = all(
            not _requested_applied_agree(cell["S0"])
            and _requested_applied_agree(cell["S5"])
            for cell in cells
        )
        if transitioned:
            return _verdict("G0_RED_H4_TIMING", comparable)

    all_d2_fail = all(not _d2_pass(cell) for cell in cells)
    if all_d2_fail and all(_is_settle_cell(cell) for cell in cells):
        return _verdict("H5_SETTLE", comparable)

    if all_d2_fail and _all_selected(
        cells,
        lambda row: _within(
            row.get("throttle_requested"), 1.0, THROTTLE_TOLERANCE
        )
        and _within(row.get("throttle_applied"), 0.0, THROTTLE_TOLERANCE),
    ):
        return _verdict("H1_H4_JOINT", comparable)

    if all_d2_fail and _all_selected(
        cells,
        lambda row: _requested_applied_agree(row)
        and _is_true(row.get("engine_on"))
        and row.get("gear") == 1,
    ):
        return _verdict("H3_NEUTRAL_GEAR", comparable)

    if all_d2_fail and _all_selected(
        cells,
        lambda row: _requested_applied_agree(row)
        and _is_true(row.get("engine_on"))
        and type(row.get("gear")) is int
        and int(row["gear"]) >= 2
        and row.get("wheel_count") == row.get("wheels_present")
        and all(
            _is_number(row.get(f"wheel_angular_velocity_{wheel}"))
            and abs(float(row[f"wheel_angular_velocity_{wheel}"])) <= WHEEL_SPIN_EPS
            for wheel in range(4)
        ),
    ):
        return _verdict("H3_TRANSMISSION_PHYSICS", comparable)

    return _verdict("INCONCLUSIVE_SETUP_FAILED", comparable, field="tree_fallback")


def _peer_is_live(peer: object) -> bool:
    if not isinstance(peer, dict):
        return False
    binding = peer.get("binding_state")
    if binding == "LEGACY_UNBOUND":
        return False
    if binding in (None, ""):
        age = peer.get("last_poll_age_s")
    elif binding != "BOUND":
        return False
    else:
        age = peer.get("bound_last_poll_age_s")
    return _is_number(age) and float(age) < PEER_STALE_S


def compute_bridge_ready(status: dict[str, object]) -> dict[str, object]:
    """Replicate the public bridge_status additive readiness field."""

    server = status.get("server_peer") if isinstance(status.get("server_peer"), dict) else {}
    client = status.get("client_peer") if isinstance(status.get("client_peer"), dict) else {}
    server_age = server.get("last_poll_age_s")
    client_age = client.get("last_poll_age_s")
    server_live = _peer_is_live(server)
    client_live = _peer_is_live(client)
    server_state = server.get("version_state")
    client_state = client.get("version_state")
    server_binding = server.get("binding_state")
    client_binding = client.get("binding_state")
    server_block = _FENCE_BLOCK_READY.get(server_binding)
    client_block = _FENCE_BLOCK_READY.get(client_binding)
    if server_block:
        return {"ready": False, "reason": server_block}
    if client_block:
        return {"ready": False, "reason": client_block}
    if server_binding == "LEGACY_UNBOUND" and server_age is not None:
        return {"ready": False, "reason": "legacy_unbound"}
    if client_binding == "LEGACY_UNBOUND" and client_age is not None:
        return {"ready": False, "reason": "legacy_unbound"}
    if server_live and client_live and server_state == "ok" and client_state == "ok":
        return {"ready": True, "reason": "ready"}
    if server_age is None and client_age is None:
        return {"ready": False, "reason": "no_run"}
    if not server_live:
        return {"ready": False, "reason": "server_poll_stale"}
    if not client_live:
        return {"ready": False, "reason": "client_not_polling"}
    if client_age is not None and client_state == "legacy_blocked":
        return {"ready": False, "reason": "client_legacy_blocked"}
    if (
        server_age is not None
        and server_state in {"version_mismatch", "legacy_blocked"}
    ) or (client_age is not None and client_state == "version_mismatch"):
        return {"ready": False, "reason": "version_mismatch"}
    if server_state != "ok" or client_state != "ok":
        return {"ready": False, "reason": "version_mismatch"}
    return {"ready": False, "reason": "no_run"}


class Daemon:
    """Raw authenticated daemon client with request-bound session coordination."""

    def __init__(self, port: int, key: str):
        self.base = f"http://127.0.0.1:{port}"
        self.key = key
        self.identity = {
            "platform": "codex",
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "started_at_utc": _utc_now(),
            "session_id": str(uuid.uuid4()),
            "task_label": "G0 ABBA drivability differential",
        }
        self.lease_token: str | None = None

    def _req(
        self,
        method: str,
        path: str,
        query: dict[str, object] | None,
        body: dict[str, object] | None,
        timeout_s: float = 10.0,
    ) -> tuple[int, dict]:
        query_values = dict(query or {})
        query_values["key"] = self.key
        url = self.base + path + "?" + urllib.parse.urlencode(query_values)
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return response.status, payload if isinstance(payload, dict) else {}
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            return error.code, payload if isinstance(payload, dict) else {}
        except (OSError, urllib.error.URLError) as error:
            raise RuntimeError(
                f"daemon request failed: {type(error).__name__}"
            ) from error

    def _session_call(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_s: float = 35.0,
    ) -> dict:
        body = {"identity": self.identity, **(payload or {})}
        status, response = self._req("POST", path, {}, body, timeout_s)
        if status not in (200, 202):
            raise RuntimeError(f"{path} -> {status} {response}")
        return response

    def session_status(self) -> dict:
        return self._session_call("/session/status")

    def session_acquire_wait(self, purpose: str, max_wait_s: float = 300.0) -> dict:
        operation_id = str(uuid.uuid4())
        active = False
        try:
            response = self._session_call(
                "/session/enqueue",
                {"purpose": purpose, "operation_id": operation_id},
            )
            ticket = response.get("ticket")
            if response.get("status") != "queued" or not isinstance(ticket, str) or not ticket:
                raise RuntimeError(f"session_acquire_wait bad enqueue response: {response}")
            deadline = time.monotonic() + max_wait_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise RuntimeError("session_wait_timeout")
                wait_slice = min(30.0, remaining)
                response = self._session_call(
                    "/session/wait",
                    {"ticket": ticket, "timeout_s": wait_slice},
                    timeout_s=max(5.0, wait_slice + 1.0),
                )
                if response.get("operation_id") != operation_id:
                    raise RuntimeError("session_acquire_wait operation_id mismatch")
                if response.get("status") == "active":
                    token = response.get("lease_token")
                    if not isinstance(token, str) or not token:
                        raise RuntimeError("session_acquire_wait missing lease_token")
                    self.lease_token = token
                    active = True
                    return response
                if response.get("status") != "queued":
                    raise RuntimeError(
                        f"session_acquire_wait bad wait response: {response}"
                    )
        finally:
            if not active:
                try:
                    self._session_call(
                        "/session/cancel-operation",
                        {"operation_id": operation_id},
                    )
                except Exception:
                    pass

    def session_heartbeat(self, lease_token: str) -> dict:
        return self._session_call(
            "/session/heartbeat", {"lease_token": lease_token}
        )

    def session_release(self, lease_token: str) -> dict:
        response = self._session_call(
            "/session/release", {"lease_token": lease_token}
        )
        self.lease_token = None
        return response

    def bridge_status(self) -> dict:
        status, payload = self._req("GET", "/status", {}, None)
        if status != 200:
            raise RuntimeError(f"bridge_status -> {status} {payload}")
        result = dict(payload)
        result["ready"] = compute_bridge_ready(result)
        return result

    def enqueue(
        self,
        cmd: str,
        args: dict,
        peer: str | None = None,
        timeout_s: float = TOOL_TIMEOUT_S,
    ) -> int:
        body: dict[str, object] = {
            "cmd": cmd,
            "args": args,
            "identity": self.identity,
            "operation_timeout_s": timeout_s,
        }
        if peer:
            body["peer"] = peer
        if self.lease_token is not None:
            body["lease_token"] = self.lease_token
        status, payload = self._req("POST", "/enqueue", {}, body)
        if status != 200 or type(payload.get("id")) is not int:
            raise RuntimeError(f"enqueue {cmd} -> {status} {payload}")
        return int(payload["id"])

    def await_result(self, command_id: int, timeout_s: float = TOOL_TIMEOUT_S) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status, payload = self._req(
                "GET",
                "/await",
                {"id": command_id, "remove": "1"},
                None,
            )
            if status == 200 and payload.get("status") == "done":
                result = payload.get("result", {})
                return result if isinstance(result, dict) else {}
            if status != 200:
                raise RuntimeError(f"await {command_id} -> {status} {payload}")
            time.sleep(0.4)
        return {"_timeout": True}

    def run(
        self,
        cmd: str,
        args: dict,
        peer: str | None = None,
        timeout_s: float = TOOL_TIMEOUT_S,
    ) -> dict:
        command_id = self.enqueue(cmd, args, peer, timeout_s)
        result = self.await_result(command_id, timeout_s)
        summary = dict(result)
        trace = summary.get("trace")
        if isinstance(trace, dict) and isinstance(trace.get("samples"), list):
            summary["trace"] = {
                **{key: value for key, value in trace.items() if key != "samples"},
                "samples_in_page": len(trace["samples"]),
            }
        print(
            f"  [{cmd}] -> {json.dumps(summary, ensure_ascii=False, default=str)[:800]}",
            flush=True,
        )
        return result


def extract_pos(result: dict) -> list[float] | None:
    for key in ("pos_real", "pos"):
        value = result.get(key)
        if (
            isinstance(value, list)
            and len(value) == 3
            and all(_is_number(component) for component in value)
        ):
            return [float(component) for component in value]
    state = result.get("state")
    if isinstance(state, dict):
        return extract_pos(state)
    return None


def wait_ready(daemon: Daemon, timeout_s: float = 180.0) -> list[float]:
    """Reference-compatible player readiness probe; not part of section 4."""

    print("[gate] waiting for server peer + player...", flush=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            result = daemon.run("query_player_state", {}, "server", 8.0)
        except RuntimeError as error:
            print(f"  enqueue error {error}", flush=True)
            time.sleep(2.0)
            continue
        position = extract_pos(result)
        if not result.get("_timeout") and result.get("ok", True) and position:
            print(f"[gate] player ready @ {position}", flush=True)
            return position
        time.sleep(2.5)
    raise RuntimeError("player never became ready")


def _logical_tool_call(
    daemon: Daemon,
    tool: str,
    args: dict[str, object],
    peer: str,
) -> dict:
    logical = dict(args)
    timeout_s = float(logical.pop("timeout_s", TOOL_TIMEOUT_S))
    if tool == "vehicle_prepare_fixture":
        logical["mode"] = "object_at"
    if tool == "vehicle_trace" and logical.get("mode") == "start":
        logical["trace_id"] = uuid.uuid4().hex
    return daemon.run(tool, logical, peer, timeout_s)


def _require(condition: bool, code: str, field: str, detail: object) -> None:
    if not condition:
        raise GateFailure(code, field, detail)


def _require_ok(payload: object, code: str, field: str) -> dict:
    _require(isinstance(payload, dict), code, field, "not_an_object")
    _require(_is_true(payload.get("ok")), code, field, payload)
    _require(not payload.get("_timeout"), code, field, "timeout")
    return payload


def _safe_tool_call(
    daemon: Daemon,
    tool: str,
    args: dict[str, object],
    peer: str,
) -> dict:
    try:
        return _logical_tool_call(daemon, tool, args, peer)
    except Exception as error:
        return {
            "_driver_error": type(error).__name__,
            "detail": str(error),
        }


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if "token" in key.casefold() or key.casefold() == "key" else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def setup_gate(daemon: Daemon) -> tuple[dict[str, object], str]:
    """Execute section 4.1 and the three global surface probes from 4.2."""

    setup: dict[str, object] = {}
    setup["SESSION0"] = _redact(daemon.session_status())
    setup["B0"] = daemon.bridge_status()
    lease = daemon.session_acquire_wait(
        purpose="G0 ABBA drivability differential",
        max_wait_s=300.0,
    )
    token = daemon.lease_token
    _require(isinstance(token, str) and bool(token), "SETUP_FAILED", "lease", lease)
    setup["lease"] = _redact(lease)
    bridge = daemon.bridge_status()
    setup["B"] = bridge
    version_state = bridge.get("version_state")
    ready = bridge.get("ready")
    _require(
        isinstance(ready, dict) and ready.get("ready") is True,
        "SETUP_FAILED",
        "B.ready.ready",
        ready,
    )
    _require(
        isinstance(version_state, dict) and version_state.get("server") == "ok",
        "SETUP_FAILED",
        "B.version_state.server",
        version_state,
    )
    _require(
        isinstance(version_state, dict) and version_state.get("client") == "ok",
        "SETUP_FAILED",
        "B.version_state.client",
        version_state,
    )

    surfaces = {
        "SURFACE_CONTROL_START": _logical_tool_call(
            daemon,
            "surface_query",
            {"x": CONTROL_SITE[0], "z": CONTROL_SITE[2], "timeout_s": 30.0},
            "server",
        ),
        "SURFACE_CONTROL_END": _logical_tool_call(
            daemon,
            "surface_query",
            {"x": CONTROL_END[0], "z": CONTROL_END[2], "timeout_s": 30.0},
            "server",
        ),
        "SURFACE_RED": _logical_tool_call(
            daemon,
            "surface_query",
            {"x": RED_SITE[0], "z": RED_SITE[2], "timeout_s": 30.0},
            "server",
        ),
    }
    for name, result in surfaces.items():
        _require_ok(result, "SETUP_FAILED", name)
    setup["surfaces"] = surfaces
    return setup, token


def prepare_site(daemon: Daemon, cell: dict[str, object]) -> None:
    """Execute PREPARE_SITE from section 4.2 into a mutable cell record."""

    site = cell["site_pos"]
    spawn = _logical_tool_call(
        daemon,
        "world_spawn",
        {
            "type": VEHICLE_TYPE,
            "pos": site,
            "flags": 0,
            "rotation": 0,
            "timeout_s": 30.0,
        },
        "server",
    )
    cell["S"] = spawn
    _require_ok(spawn, "SETUP_FAILED", "world_spawn.ok")
    _require(spawn.get("type") == VEHICLE_TYPE, "SETUP_FAILED", "world_spawn.type", spawn)
    _require(_is_true(spawn.get("found")), "SETUP_FAILED", "world_spawn.found", spawn)
    object_id = spawn.get("object_id")
    _require(type(object_id) is int and object_id > 0, "SETUP_FAILED", "world_spawn.object_id", object_id)
    pos_real = extract_pos(spawn)
    _require(pos_real is not None, "SETUP_FAILED", "world_spawn.pos_real", spawn)
    cell["object_id"] = object_id
    cell["spawn_pos_real"] = pos_real

    fixture = _logical_tool_call(
        daemon,
        "vehicle_prepare_fixture",
        {
            "type": VEHICLE_TYPE,
            "pos": pos_real,
            "radius": 8.0,
            "timeout_s": 30.0,
        },
        "server",
    )
    cell["F"] = fixture
    cell["prepare_restore"] = _safe_tool_call(
        daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
    )
    cell["prepare_release"] = _safe_tool_call(
        daemon, "vehicle_release", {"timeout_s": 30.0}, "client"
    )
    get_in = _logical_tool_call(
        daemon,
        "vehicle_get_in_client",
        {"pos": pos_real, "timeout_s": 30.0},
        "client",
    )
    cell["G"] = get_in

    _require_ok(fixture, "SETUP_FAILED", "vehicle_prepare_fixture.ok")
    _require(_is_true(get_in.get("seated")), "SETUP_FAILED", "G.seated", get_in)
    _require(get_in.get("seat") == "driver", "SETUP_FAILED", "G.seat", get_in)
    _require(_is_true(get_in.get("vehicle_fixture_ready")), "SETUP_FAILED", "G.vehicle_fixture_ready", get_in)
    _require(_is_true(get_in.get("is_owner")), "SETUP_FAILED", "G.is_owner", get_in)
    _require(_is_false(get_in.get("is_authority_owner")), "SETUP_FAILED", "G.is_authority_owner", get_in)
    _require(get_in.get("net_strategy") == 2, "SETUP_FAILED", "G.net_strategy", get_in)
    owner_identity = get_in.get("owner_identity")
    _require(isinstance(owner_identity, str) and bool(owner_identity), "SETUP_FAILED", "G.owner_identity", get_in)
    net_id = [get_in.get("net_id_low"), get_in.get("net_id_high")]
    _require(all(type(value) is int for value in net_id), "SETUP_FAILED", "G.net_id", net_id)
    cell["owner_identity"] = owner_identity
    cell["net_id"] = net_id

    engine = _logical_tool_call(
        daemon, "engine_set", {"mode": "start", "timeout_s": 30.0}, "client"
    )
    cell["engine_start"] = engine
    _require_ok(engine, "SETUP_FAILED", "engine_set.start")
    base = _logical_tool_call(
        daemon, "vehicle_telemetry", {"timeout_s": 30.0}, "client"
    )
    cell["BASE"] = base
    _require_ok(base, "SETUP_FAILED", "BASE.ok")
    _require(_is_true(base.get("engine_on_server")), "SETUP_FAILED", "BASE.engine_on_server", base)
    _require(_is_true(base.get("is_owner")), "SETUP_FAILED", "BASE.is_owner", base)
    _require(_is_false(base.get("is_authority_owner")), "SETUP_FAILED", "BASE.is_authority_owner", base)
    _require(base.get("net_strategy") == 2, "SETUP_FAILED", "BASE.net_strategy", base)
    _require([base.get("net_id_low"), base.get("net_id_high")] == net_id, "SETUP_FAILED", "BASE.net_id", base)


def _trace_integrity_gate(
    cell: dict[str, object],
    trace: dict,
    trace_ids: list[object],
    samples: list[dict],
    selected: dict[str, object],
) -> dict[str, object]:
    checks: dict[str, object] = {}
    checks["complete"] = _is_true(trace.get("complete"))
    checks["overflow"] = _is_false(trace.get("overflow"))
    checks["stop_reason"] = trace.get("stop_reason") == "requested"
    checks["eof"] = _is_true(trace.get("eof"))
    trace_id = cell.get("trace_id")
    checks["trace_ids_consistent"] = bool(trace_ids) and all(
        value == trace_id for value in trace_ids
    )
    checks["owner_identity"] = trace.get("owner_identity") == cell.get("owner_identity")
    checks["trace_net_id"] = [trace.get("net_id_low"), trace.get("net_id_high")] == cell.get("net_id")
    checks["car_type"] = trace.get("car_type") == VEHICLE_TYPE
    checks["sample_count"] = trace.get("count") == len(samples)
    checks["sample_owner_stable"] = all(_is_true(row.get("is_owner")) for row in samples)
    checks["sample_net_id_stable"] = all(
        [row.get("net_id_low"), row.get("net_id_high")] == cell.get("net_id")
        for row in samples
    )
    failed = [name for name, passed in checks.items() if passed is not True]
    result: dict[str, object] = {
        "status": "PASS" if not failed else "TRACE_SETUP_FAILED",
        "checks": checks,
        "failed": failed,
    }
    if selected.get("status") == "TRACE_SETUP_FAILED":
        result["status"] = "TRACE_SETUP_FAILED"
        result["failed"] = [*failed, "S0_S2_S5"]
    elif selected.get("status") == "PASS":
        quality = trace_sample_gate(samples, selected["S0"], selected["S5"])
        result["sample_quality"] = quality
        if quality.get("status") != "PASS":
            result["status"] = "TRACE_SETUP_FAILED"
            result["failed"] = [*failed, str(quality.get("field"))]
    else:
        quality = (
            trace_sample_gate(samples, samples[0], samples[-1])
            if len(samples) >= 2
            else {
                "status": "TRACE_SETUP_FAILED",
                "field": "sample_window",
                "max_sample_gap_s": None,
                "effective_hz": 0.0,
            }
        )
        result["sample_quality"] = {
            **quality,
            "reason": "H1_CONTROL_NOT_OBSERVABLE",
        }
        if quality.get("status") != "PASS":
            result["status"] = "TRACE_SETUP_FAILED"
            result["failed"] = [*failed, str(quality.get("field"))]
    return result


def run_cell(daemon: Daemon, cell: dict[str, object]) -> None:
    """Execute RUN_CELL from section 4.3, including final raw pagination."""

    trace_id: str | None = None
    trace_ids: list[object] = []
    live_samples: list[dict] = []
    raw_samples: list[dict] = []
    final_trace: dict = {}
    pending_failure: BaseException | None = None
    try:
        start = _logical_tool_call(
            daemon,
            "vehicle_trace",
            {
                "mode": "start",
                "trace_id": "",
                "cursor": 0,
                "limit": 1,
                "sample_hz": 20,
                "max_samples": 256,
                "timeout_s": 30.0,
            },
            "client",
        )
        cell["trace_start"] = start
        _require_ok(start, "TRACE_SETUP_FAILED", "START.ok")
        start_trace = start.get("trace")
        _require(isinstance(start_trace, dict), "TRACE_SETUP_FAILED", "START.trace", start)
        _require(_is_true(start_trace.get("active")), "TRACE_SETUP_FAILED", "START.trace.active", start_trace)
        trace_id_value = start_trace.get("trace_id")
        _require(isinstance(trace_id_value, str) and bool(trace_id_value), "TRACE_SETUP_FAILED", "TRACE_ID", trace_id_value)
        trace_id = trace_id_value
        cell["trace_id"] = trace_id
        trace_ids.append(trace_id)

        control = _logical_tool_call(
            daemon,
            "vehicle_control",
            {
                "throttle": 1.0,
                "steer": 0.0,
                "brake": 0.0,
                "handbrake": 0.0,
                "hold_ttl_s": 8.0,
                "timeout_s": 30.0,
            },
            "client",
        )
        cell["control_call"] = control
        _require_ok(control, "COMMAND_FAILED", "CONTROL_CALL.ok")

        scan_cursor = 0
        found_s0: dict | None = None
        started = time.monotonic()
        while time.monotonic() - started <= TRACE_WALL_LIMIT_S:
            status = _logical_tool_call(
                daemon,
                "vehicle_trace",
                {
                    "mode": "status",
                    "trace_id": trace_id,
                    "cursor": 0,
                    "limit": 1,
                    "sample_hz": 20,
                    "max_samples": 256,
                    "timeout_s": 30.0,
                },
                "client",
            )
            _require_ok(status, "TRACE_SETUP_FAILED", "STATUS.ok")
            status_trace = status.get("trace")
            _require(isinstance(status_trace, dict), "TRACE_SETUP_FAILED", "STATUS.trace", status)
            trace_ids.append(status_trace.get("trace_id"))
            count = status_trace.get("count")
            _require(type(count) is int and count >= scan_cursor, "TRACE_SETUP_FAILED", "STATUS.trace.count", count)
            while scan_cursor < count:
                limit = min(TRACE_PAGE_LIMIT, count - scan_cursor)
                page = _logical_tool_call(
                    daemon,
                    "vehicle_trace",
                    {
                        "mode": "read",
                        "trace_id": trace_id,
                        "cursor": scan_cursor,
                        "limit": limit,
                        "sample_hz": 20,
                        "max_samples": 256,
                        "timeout_s": 30.0,
                    },
                    "client",
                )
                _require_ok(page, "TRACE_SETUP_FAILED", "LIVE_PAGE.ok")
                page_trace = page.get("trace")
                _require(isinstance(page_trace, dict), "TRACE_SETUP_FAILED", "LIVE_PAGE.trace", page)
                trace_ids.append(page_trace.get("trace_id"))
                page_samples = page_trace.get("samples")
                _require(isinstance(page_samples, list) and all(isinstance(row, dict) for row in page_samples), "TRACE_SETUP_FAILED", "LIVE_PAGE.trace.samples", page_trace)
                live_samples.extend(page_samples)
                next_cursor = page_trace.get("next_cursor")
                _require(type(next_cursor) is int and next_cursor > scan_cursor, "TRACE_SETUP_FAILED", "LIVE_PAGE.trace.next_cursor", next_cursor)
                scan_cursor = next_cursor
            selected_live = extract_s0_s2_s5(live_samples)
            if selected_live.get("status") != "H1_CONTROL_NOT_OBSERVABLE":
                candidate = selected_live.get("S0")
                if isinstance(candidate, dict) and not _is_absence(candidate):
                    found_s0 = candidate
            last_time = _sample_time(live_samples[-1]) if live_samples else None
            s0_time = _sample_time(found_s0)
            if (
                s0_time is not None
                and last_time is not None
                and last_time >= s0_time + 5.0
            ):
                break
            time.sleep(0.05)
    except BaseException as error:
        pending_failure = error
    finally:
        if trace_id is not None:
            stop = _safe_tool_call(
                daemon,
                "vehicle_trace",
                {
                    "mode": "stop",
                    "trace_id": trace_id,
                    "cursor": 0,
                    "limit": 1,
                    "sample_hz": 20,
                    "max_samples": 256,
                    "timeout_s": 30.0,
                },
                "client",
            )
            cell["trace_stop"] = stop
            stop_trace = stop.get("trace")
            if isinstance(stop_trace, dict):
                trace_ids.append(stop_trace.get("trace_id"))
        cell["brake_after_trace"] = _safe_tool_call(
            daemon,
            "vehicle_control",
            {
                "throttle": 0.0,
                "steer": 0.0,
                "brake": 1.0,
                "handbrake": 1.0,
                "hold_ttl_s": 8.0,
                "timeout_s": 30.0,
            },
            "client",
        )
        cell["END"] = _safe_tool_call(
            daemon, "vehicle_telemetry", {"timeout_s": 30.0}, "client"
        )
        if trace_id is not None:
            cursor = 0
            eof = False
            while not eof:
                page = _safe_tool_call(
                    daemon,
                    "vehicle_trace",
                    {
                        "mode": "read",
                        "trace_id": trace_id,
                        "cursor": cursor,
                        "limit": 64,
                        "sample_hz": 20,
                        "max_samples": 256,
                        "timeout_s": 30.0,
                    },
                    "client",
                )
                page_trace = page.get("trace")
                if not isinstance(page_trace, dict):
                    pending_failure = pending_failure or GateFailure(
                        "TRACE_SETUP_FAILED", "PAGE.trace", page
                    )
                    break
                final_trace = page_trace
                trace_ids.append(page_trace.get("trace_id"))
                page_samples = page_trace.get("samples")
                if not isinstance(page_samples, list) or not all(
                    isinstance(row, dict) for row in page_samples
                ):
                    pending_failure = pending_failure or GateFailure(
                        "TRACE_SETUP_FAILED", "PAGE.trace.samples", page_trace
                    )
                    break
                raw_samples.extend(page_samples)
                next_cursor = page_trace.get("next_cursor")
                if type(next_cursor) is not int or next_cursor < cursor:
                    pending_failure = pending_failure or GateFailure(
                        "TRACE_SETUP_FAILED", "PAGE.trace.next_cursor", next_cursor
                    )
                    break
                eof = _is_true(page_trace.get("eof"))
                if not eof and next_cursor == cursor:
                    pending_failure = pending_failure or GateFailure(
                        "TRACE_SETUP_FAILED", "PAGE.trace.cursor_stall", cursor
                    )
                    break
                cursor = next_cursor
            cell["trace_clear"] = _safe_tool_call(
                daemon,
                "vehicle_trace",
                {
                    "mode": "clear",
                    "trace_id": trace_id,
                    "cursor": 0,
                    "limit": 1,
                    "sample_hz": 20,
                    "max_samples": 256,
                    "timeout_s": 30.0,
                },
                "client",
            )

    cell["samples"] = raw_samples
    selected = extract_s0_s2_s5(raw_samples)
    cell.update({key: selected[key] for key in ("S0", "S2", "S5")})
    if selected.get("status") == "PASS":
        cell["deltas"] = cell_deltas(
            selected["S0"], selected["S2"], selected["S5"]
        )
    else:
        cell["deltas"] = None
    cell["trace_owner_identity"] = final_trace.get("owner_identity")
    cell["trace_net_id"] = [
        final_trace.get("net_id_low"),
        final_trace.get("net_id_high"),
    ]
    cell["trace_gates"] = _trace_integrity_gate(
        cell, final_trace, trace_ids, raw_samples, selected
    )
    if pending_failure is not None:
        raise pending_failure


def finish_site(daemon: Daemon, cell: dict[str, object]) -> dict[str, object]:
    """Execute FINISH_SITE from section 4.6.

    Prefers a clean get-out before deleting the fixture; when the exit action
    stays blocked, the disposable fixture is force-deleted under the player
    (engine ejects the occupant; the ejection is then verified).
    """

    cleanup: dict[str, object] = {"status": "CLEANUP_DEGRADED"}
    object_id = cell.get("object_id")
    if type(object_id) is not int or object_id <= 0:
        cleanup["reason"] = "no_object_id"
        cleanup["restore"] = _safe_tool_call(
            daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
        )
        cell["finished"] = True
        return cleanup

    check = _safe_tool_call(
        daemon, "vehicle_telemetry", {"timeout_s": 30.0}, "client"
    )
    cleanup["CHECK"] = check
    if _is_false(check.get("ok")) and check.get("error") == "not_seated":
        cleanup["release"] = _safe_tool_call(
            daemon, "vehicle_release", {"timeout_s": 30.0}, "client"
        )
        deleted = _safe_tool_call(
            daemon,
            "object_delete",
            {"object_id": object_id, "timeout_s": 30.0},
            "server",
        )
        cleanup["delete"] = deleted
        cleanup["restore"] = _safe_tool_call(
            daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
        )
        cleanup["status"] = (
            "OK_UNSEATED"
            if _is_true(deleted.get("ok")) and _is_true(deleted.get("deleted"))
            else "CLEANUP_DEGRADED"
        )
        cell["finished"] = True
        return cleanup
    if not _is_true(check.get("ok")):
        # Seat state is unobservable, but the claim and the fixture are
        # still ours to drop: leaving them held would contaminate whatever
        # runs on the box next (the sibling branches all release).
        cleanup["reason"] = "telemetry_failed"
        cleanup["release"] = _safe_tool_call(
            daemon, "vehicle_release", {"timeout_s": 30.0}, "client"
        )
        cleanup["delete"] = _safe_tool_call(
            daemon,
            "object_delete",
            {"object_id": object_id, "timeout_s": 30.0},
            "server",
        )
        cleanup["restore"] = _safe_tool_call(
            daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
        )
        cell["finished"] = True
        return cleanup

    brake_args = {
        "throttle": 0.0,
        "steer": 0.0,
        "brake": 1.0,
        "handbrake": 1.0,
        "hold_ttl_s": 8.0,
        "timeout_s": 30.0,
    }
    cleanup["brake"] = _safe_tool_call(
        daemon, "vehicle_control", brake_args, "client"
    )
    stopped = check
    started = time.monotonic()
    refreshed = False
    while time.monotonic() - started <= 10.0:
        stopped = _safe_tool_call(
            daemon, "vehicle_telemetry", {"timeout_s": 30.0}, "client"
        )
        speed = stopped.get("speedo_max")
        if _is_number(speed) and abs(float(speed)) < 0.1:
            break
        if not refreshed and time.monotonic() - started >= 4.0:
            cleanup["brake_refresh"] = _safe_tool_call(
                daemon, "vehicle_control", brake_args, "client"
            )
            refreshed = True
        time.sleep(0.25)
    cleanup["STOPPED"] = stopped
    cleanup["engine_stop"] = _safe_tool_call(
        daemon, "engine_set", {"mode": "stop", "timeout_s": 30.0}, "client"
    )
    # vehicle_release runs after the get-out attempt (exit first, then drop
    # the claim). Early-return branches release explicitly so no path leaks
    # the claim.

    speed = stopped.get("speedo_max")
    if not _is_number(speed) or abs(float(speed)) >= 0.1:
        cleanup["reason"] = "vehicle_not_stopped"
        cleanup["release"] = _safe_tool_call(
            daemon, "vehicle_release", {"timeout_s": 30.0}, "client"
        )
        cleanup["restore"] = _safe_tool_call(
            daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
        )
        cell["finished"] = True
        return cleanup

    stopped_pos = extract_pos(stopped)
    if stopped_pos is None:
        cleanup["reason"] = "stopped_position_missing"
        cleanup["release"] = _safe_tool_call(
            daemon, "vehicle_release", {"timeout_s": 30.0}, "client"
        )
        cleanup["restore"] = _safe_tool_call(
            daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
        )
        cell["finished"] = True
        return cleanup
    cleanup["OUT"] = _safe_tool_call(
        daemon,
        "action_use",
        {
            "action": "ActionGetOutTransport",
            "classname": VEHICLE_TYPE,
            "pos": stopped_pos,
            "radius": 8.0,
            "timeout_s": 30.0,
        },
        "client",
    )
    seat_check: dict = {}
    started = time.monotonic()
    while time.monotonic() - started <= 5.0:
        seat_check = _safe_tool_call(
            daemon, "vehicle_telemetry", {"timeout_s": 30.0}, "client"
        )
        if _is_false(seat_check.get("ok")) and seat_check.get("error") == "not_seated":
            break
        time.sleep(0.25)
    cleanup["SEAT_CHECK"] = seat_check
    cleanup["release"] = _safe_tool_call(
        daemon, "vehicle_release", {"timeout_s": 30.0}, "client"
    )
    if _is_false(seat_check.get("ok")) and seat_check.get("error") == "not_seated":
        deleted = _safe_tool_call(
            daemon,
            "object_delete",
            {"object_id": object_id, "timeout_s": 30.0},
            "server",
        )
        cleanup["delete"] = deleted
        cleanup["restore"] = _safe_tool_call(
            daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
        )
        cleanup["status"] = (
            "OK"
            if _is_true(deleted.get("ok")) and _is_true(deleted.get("deleted"))
            else "CLEANUP_DEGRADED"
        )
    else:
        # Telemetry ok means the seat is positively confirmed; any other
        # non-not_seated answer leaves the state unobserved.
        if _is_true(seat_check.get("ok")):
            cleanup["reason"] = "still_seated"
        else:
            cleanup["reason"] = "unseating_not_confirmed"
        # ActionGetOutTransport.Can() stayed false on the 2026-08-24 stand at
        # two different sites. The fixture is disposable: delete it under the
        # player and let the engine eject them, then verify the seat cleared.
        forced = _safe_tool_call(
            daemon,
            "object_delete",
            {"object_id": object_id, "timeout_s": 30.0},
            "server",
        )
        cleanup["forced_delete"] = forced
        eject_check: dict = {}
        started = time.monotonic()
        while time.monotonic() - started <= 5.0:
            eject_check = _safe_tool_call(
                daemon, "vehicle_telemetry", {"timeout_s": 30.0}, "client"
            )
            if _is_false(eject_check.get("ok")) and eject_check.get("error") == "not_seated":
                break
            time.sleep(0.25)
        cleanup["EJECT_CHECK"] = eject_check
        cleanup["restore"] = _safe_tool_call(
            daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
        )
        if (
            _is_true(forced.get("ok"))
            and _is_true(forced.get("deleted"))
            and _is_false(eject_check.get("ok"))
            and eject_check.get("error") == "not_seated"
        ):
            cleanup["status"] = "OK_FORCED_DELETE"
    cell["finished"] = True
    return cleanup


def _call_text(tool: str, args: dict[str, object] | None = None) -> str:
    if not args:
        return f"{tool}()"
    rendered = ", ".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in args.items()
    )
    return f"{tool}({rendered})"


def _prepare_plan(site_pos: list[float]) -> list[str]:
    return [
        _call_text(
            "world_spawn",
            {"type": VEHICLE_TYPE, "pos": site_pos, "flags": 0, "rotation": 0, "timeout_s": 30.0},
        ),
        _call_text(
            "vehicle_prepare_fixture",
            {"type": VEHICLE_TYPE, "pos": "S.pos_real", "radius": 8.0, "timeout_s": 30.0},
        ),
        _call_text("restore_gameplay", {"timeout_s": 30.0}),
        _call_text("vehicle_release", {"timeout_s": 30.0}),
        _call_text("vehicle_get_in_client", {"pos": "S.pos_real", "timeout_s": 30.0}),
        _call_text("engine_set", {"mode": "start", "timeout_s": 30.0}),
        _call_text("vehicle_telemetry", {"timeout_s": 30.0}),
    ]


def _run_cell_plan() -> list[str]:
    trace_common = {"trace_id": "TRACE_ID", "sample_hz": 20, "max_samples": 256, "timeout_s": 30.0}
    return [
        _call_text(
            "vehicle_trace",
            {"mode": "start", "trace_id": "", "cursor": 0, "limit": 1, "sample_hz": 20, "max_samples": 256, "timeout_s": 30.0},
        ),
        _call_text(
            "vehicle_control",
            {"throttle": 1.0, "steer": 0.0, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 8.0, "timeout_s": 30.0},
        ),
        "REPEAT <=7.5s: " + _call_text("vehicle_trace", {"mode": "status", "cursor": 0, "limit": 1, **trace_common}),
        "PAGINATE live count: " + _call_text("vehicle_trace", {"mode": "read", "cursor": "scan_cursor", "limit": "min(64, remaining)", **trace_common}),
        _call_text("vehicle_trace", {"mode": "stop", "cursor": 0, "limit": 1, **trace_common}),
        _call_text(
            "vehicle_control",
            {"throttle": 0.0, "steer": 0.0, "brake": 1.0, "handbrake": 1.0, "hold_ttl_s": 8.0, "timeout_s": 30.0},
        ),
        _call_text("vehicle_telemetry", {"timeout_s": 30.0}),
        "PAGINATE until eof: " + _call_text("vehicle_trace", {"mode": "read", "cursor": "cursor", "limit": 64, **trace_common}),
        _call_text("vehicle_trace", {"mode": "clear", "cursor": 0, "limit": 1, **trace_common}),
    ]


def _finish_plan() -> list[str]:
    return [
        _call_text("vehicle_telemetry", {"timeout_s": 30.0}),
        "IF not_seated: " + _call_text("vehicle_release", {"timeout_s": 30.0}),
        "IF not_seated: " + _call_text("object_delete", {"object_id": "S.object_id", "timeout_s": 30.0}),
        "IF not_seated: " + _call_text("restore_gameplay", {"timeout_s": 30.0}),
        _call_text(
            "vehicle_control",
            {"throttle": 0.0, "steer": 0.0, "brake": 1.0, "handbrake": 1.0, "hold_ttl_s": 8.0, "timeout_s": 30.0},
        ),
        "REPEAT <=10s: " + _call_text("vehicle_telemetry", {"timeout_s": 30.0}),
        "REFRESH at 4s: " + _call_text(
            "vehicle_control",
            {"throttle": 0.0, "steer": 0.0, "brake": 1.0, "handbrake": 1.0, "hold_ttl_s": 8.0, "timeout_s": 30.0},
        ),
        _call_text("engine_set", {"mode": "stop", "timeout_s": 30.0}),
        _call_text(
            "action_use",
            {"action": "ActionGetOutTransport", "classname": VEHICLE_TYPE, "pos": "STOPPED.pos_real", "radius": 8.0, "timeout_s": 30.0},
        ),
        "REPEAT <=5s: " + _call_text("vehicle_telemetry", {"timeout_s": 30.0}),
        _call_text("vehicle_release", {"timeout_s": 30.0}),
        "IF not_seated: " + _call_text("object_delete", {"object_id": "S.object_id", "timeout_s": 30.0}),
        "ELSE forced: " + _call_text("object_delete", {"object_id": "S.object_id", "timeout_s": 30.0})
        + " + REPEAT <=5s: " + _call_text("vehicle_telemetry", {"timeout_s": 30.0}),
        _call_text("restore_gameplay", {"timeout_s": 30.0}),
    ]


def build_call_plan(build: str) -> list[str]:
    """Build the complete four-cell call plan without opening a socket."""

    lines = [
        "G0 ABBA DRY RUN -- NO SOCKETS",
        f"BUILD {build}",
        "SETUP 4.1",
        "  " + _call_text("session_status"),
        "  " + _call_text("bridge_status"),
        "  " + _call_text(
            "session_acquire_wait",
            {"purpose": "G0 ABBA drivability differential", "max_wait_s": 300.0},
        ),
        "  " + _call_text("bridge_status"),
        "SURFACES 4.2",
        "  " + _call_text("surface_query", {"x": CONTROL_SITE[0], "z": CONTROL_SITE[2], "timeout_s": 30.0}),
        "  " + _call_text("surface_query", {"x": CONTROL_END[0], "z": CONTROL_END[2], "timeout_s": 30.0}),
        "  " + _call_text("surface_query", {"x": RED_SITE[0], "z": RED_SITE[2], "timeout_s": 30.0}),
    ]
    for index, (site, site_pos) in enumerate(ABBA_SITES, start=1):
        lines.append(f"CELL {index} {site} {json.dumps(site_pos)}")
        lines.append("  PREPARE_SITE 4.2")
        lines.extend(f"    {call}" for call in _prepare_plan(site_pos))
        lines.append("  RUN_CELL 4.3")
        lines.extend(f"    {call}" for call in _run_cell_plan())
        lines.append("  FINISH_SITE 4.6")
        lines.extend(f"    {call}" for call in _finish_plan())
        if index < 4:
            lines.append(
                "  "
                + _call_text("session_heartbeat", {"lease_token": "L"})
            )
    lines.extend(
        [
            "COMPARABILITY GATE 4.4",
            "VERDICT TREE 4.5",
            "FINALIZER 4.6 (always)",
            "  IF active unfinished cell: FINISH_SITE(active_cell)",
            "  " + _call_text("restore_gameplay", {"timeout_s": 30.0}),
            "  " + _call_text("session_release", {"lease_token": "L"}),
            "  " + _call_text("session_status"),
        ]
    )
    return lines


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _sidecar_path(out_path: Path, cell_number: int) -> Path:
    return Path(f"{out_path}.cell{cell_number}.samples.json")


def _public_cell(cell: dict[str, object]) -> dict[str, object]:
    samples_file = cell.get("samples_file")
    if isinstance(samples_file, str):
        samples_file = Path(samples_file).name
    return {
        "site": cell.get("site"),
        "site_pos": cell.get("site_pos"),
        "object_id": cell.get("object_id"),
        "owner_identity": cell.get("owner_identity"),
        "net_id": cell.get("net_id"),
        "trace_id": cell.get("trace_id"),
        "S0": cell.get("S0", _typed_absence("CELL_NOT_RUN")),
        "S2": cell.get("S2", _typed_absence("CELL_NOT_RUN")),
        "S5": cell.get("S5", _typed_absence("CELL_NOT_RUN")),
        "deltas": cell.get("deltas"),
        "trace_gates": cell.get("trace_gates"),
        "samples_file": samples_file,
        "samples_write_error": cell.get("samples_write_error"),
        "finish": cell.get("finish"),
    }


def execute(daemon: Daemon, out_path: Path, build: str) -> tuple[dict[str, object], int]:
    report: dict[str, object] = {
        "schema": "dayz-mcp-g0-abba-verdict-v1",
        "generated_at_utc": _utc_now(),
        "build": build,
        "sequence": [site for site, _ in ABBA_SITES],
        "cells": [],
        "comparability": None,
        "tree": None,
        "cleanup": {},
    }
    cells: list[dict[str, object]] = []
    active_cell: dict[str, object] | None = None
    lease_token: str | None = None
    exit_code = 3
    try:
        setup, lease_token = setup_gate(daemon)
        report["setup"] = setup
        for index, (site, site_pos) in enumerate(ABBA_SITES, start=1):
            print(f"[gate] CELL {index} {site} @ {site_pos}", flush=True)
            cell_record: dict[str, object] = {
                "cell_number": index,
                "site": site,
                "site_pos": list(site_pos),
                "finished": False,
            }
            cells.append(cell_record)
            active_cell = cell_record
            prepare_site(daemon, cell_record)
            sidecar = _sidecar_path(out_path, index)
            try:
                run_cell(daemon, cell_record)
            finally:
                _write_json(sidecar, cell_record.get("samples", []))
                cell_record["samples_file"] = str(sidecar)
            finish = finish_site(daemon, cell_record)
            cell_record["finish"] = finish
            _require(
                finish.get("status") in {"OK", "OK_UNSEATED", "OK_FORCED_DELETE"},
                "SETUP_FAILED",
                "FINISH_SITE",
                finish,
            )
            active_cell = None
            if index < 4:
                heartbeat = daemon.session_heartbeat(lease_token)
                cell_record["heartbeat"] = _redact(heartbeat)

        comparable = comparability_gate(cells)
        tree = verdict_tree(cells)
        report["comparability"] = comparable
        report["tree"] = tree
        exit_code = 0 if tree.get("row") == "NO_REPRODUCE_G0_PASS" else 2
    except GateFailure as error:
        report["failure"] = error.to_dict()
        report["tree"] = {
            "row": "INCONCLUSIVE_SETUP_FAILED",
            "verdict": TREE_VERDICTS["INCONCLUSIVE_SETUP_FAILED"],
            "field": error.field,
        }
        exit_code = 3
    except Exception as error:
        report["failure"] = {
            "code": "DRIVER_FAILED",
            "field": type(error).__name__,
            "detail": str(error),
        }
        report["tree"] = {
            "row": "INCONCLUSIVE_SETUP_FAILED",
            "verdict": TREE_VERDICTS["INCONCLUSIVE_SETUP_FAILED"],
            "field": type(error).__name__,
        }
        exit_code = 3
    finally:
        cleanup = report["cleanup"]
        if (
            active_cell is not None
            and active_cell.get("finished") is not True
            and daemon.lease_token is not None
        ):
            try:
                active_cell["finish"] = finish_site(daemon, active_cell)
            except Exception as error:
                active_cell["finish"] = {
                    "status": "CLEANUP_DEGRADED",
                    "_driver_error": type(error).__name__,
                    "detail": str(error),
                }
                exit_code = 3
        if daemon.lease_token is not None:
            cleanup["restore_gameplay"] = _safe_tool_call(
                daemon, "restore_gameplay", {"timeout_s": 30.0}, "client"
            )
            token = daemon.lease_token
            try:
                cleanup["RELEASE"] = _redact(daemon.session_release(token))
            except Exception as error:
                cleanup["RELEASE"] = {
                    "_driver_error": type(error).__name__,
                    "detail": str(error),
                }
                exit_code = 3
        try:
            cleanup["FINAL_SESSION"] = _redact(daemon.session_status())
        except Exception as error:
            cleanup["FINAL_SESSION"] = {
                "_driver_error": type(error).__name__,
                "detail": str(error),
            }
            exit_code = 3
        if active_cell is not None and "samples_file" not in active_cell:
            sidecar = _sidecar_path(out_path, int(active_cell["cell_number"]))
            try:
                _write_json(sidecar, active_cell.get("samples", []))
                active_cell["samples_file"] = str(sidecar)
            except Exception as error:
                active_cell["samples_write_error"] = {
                    "_driver_error": type(error).__name__,
                    "detail": str(error),
                }
                exit_code = 3
        report["cells"] = [_public_cell(cell) for cell in cells]
        _write_json(out_path, report)
    return report, exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the G0 CONTROL-RED-RED-CONTROL DayZ MCP diagnostic gate."
    )
    parser.add_argument("--keyfile", default=str(DEFAULT_KEYFILE))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--pbo-sha256",
        default="",
        help="Deployed PBO SHA-256 recorded outside MCP.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the four-cell call plan without reading the key or opening sockets.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    build = args.pbo_sha256.strip() or "BUILD_UNVERIFIED"
    if args.dry_run:
        print("\n".join(build_call_plan(build)))
        return 0

    key = Path(args.keyfile).read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("keyfile is empty")
    daemon = Daemon(args.port, key)
    report, exit_code = execute(daemon, Path(args.out), build)
    tree = report.get("tree") or {}
    print(
        f"[gate] verdict written: {args.out} row={tree.get('row')} exit={exit_code}",
        flush=True,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
