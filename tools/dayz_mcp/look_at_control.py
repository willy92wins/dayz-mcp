"""Decide player_look_at pulses from the measured camera direction.

The 2026-10-08 addendum measured OverrideAimChangeX/Y ONE_FRAME as a one-shot
view change of exactly v radians (positive yaw to the right, positive pitch
up). This module does not treat a commanded angle as convergence. Success is
three consecutive ticks at or under 1 degree, then three further ticks with
no pulse, inside the control budget.
"""

from __future__ import annotations

import math
import time
from typing import Any

MAX_CONTROL_S = 3.0
BAND_DEG = 1.0
STABLE_TICKS = 3
HOLD_TICKS = 3
MAX_PULSE_RAD = 0.2


def admit_player_look_at(*, lease_token: object, run_id: object) -> str | None:
    if not isinstance(lease_token, str) or lease_token == "":
        return "lease_required"
    if not isinstance(run_id, str) or run_id == "":
        return "no_active_run"
    return None


def _wrap_pi(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def _clamp(value: float, limit: float) -> float:
    if value > limit:
        return limit
    if value < -limit:
        return -limit
    return value


def _unit(vec: object) -> tuple[float, float, float] | None:
    if not isinstance(vec, (list, tuple)) or len(vec) != 3:
        return None
    nums: list[float] = []
    for item in vec:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        nums.append(number)
    length = math.sqrt(nums[0] ** 2 + nums[1] ** 2 + nums[2] ** 2)
    if length <= 0.0 or not math.isfinite(length):
        return None
    return (nums[0] / length, nums[1] / length, nums[2] / length)


def bearing_pitch_rad(direction: tuple[float, float, float]) -> tuple[float, float]:
    """Compass bearing (0 = +Z, positive toward +X) and pitch (positive up)."""
    x, y, z = direction
    bearing = math.atan2(x, z)
    horizontal = math.sqrt(x * x + z * z)
    pitch = math.atan2(y, horizontal)
    return bearing, pitch


def angular_error_deg(
    current_dir: tuple[float, float, float],
    desired_dir: tuple[float, float, float],
) -> float:
    dot = current_dir[0] * desired_dir[0] + current_dir[1] * desired_dir[1] + current_dir[2] * desired_dir[2]
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


def pulse_radians(
    current_dir: tuple[float, float, float],
    desired_dir: tuple[float, float, float],
) -> tuple[float, float]:
    current_bearing, current_pitch = bearing_pitch_rad(current_dir)
    desired_bearing, desired_pitch = bearing_pitch_rad(desired_dir)
    dx = _clamp(_wrap_pi(desired_bearing - current_bearing), MAX_PULSE_RAD)
    dy = _clamp(_wrap_pi(desired_pitch - current_pitch), MAX_PULSE_RAD)
    return dx, dy


class LookAtController:
    def __init__(self, *, budget_s: float) -> None:
        if not math.isfinite(budget_s) or budget_s <= 0.0:
            raise ValueError("bad_timeout")
        self.budget_s = min(float(budget_s), MAX_CONTROL_S)
        self.elapsed_s = 0.0
        self.ticks = 0
        self.pulses = 0
        self.stable_ticks = 0
        self.hold_ticks = 0
        self.initial_error_deg: float | None = None
        self.final_error_deg: float | None = None
        self.finished = False
        self.converged = False
        self.termination = ""
        self.released = False

    def _stop(self, termination: str, *, converged: bool) -> dict[str, Any]:
        self.finished = True
        self.converged = converged
        self.termination = termination
        self.released = True
        return self.decision(None)

    def decision(self, pulse: tuple[float, float] | None) -> dict[str, Any]:
        return {
            "finished": self.finished,
            "converged": self.converged and self.termination == "converged",
            "pulse": None if pulse is None else [pulse[0], pulse[1]],
            "release_overrides": self.released,
            "ticks": self.ticks,
            "stable_ticks": self.stable_ticks,
            "hold_ticks": self.hold_ticks,
            "pulses": self.pulses,
            "duration_s": self.elapsed_s,
            "error_initial_deg": self.initial_error_deg,
            "error_final_deg": self.final_error_deg,
            "termination": self.termination,
        }

    def step(self, observation: dict[str, Any]) -> dict[str, Any]:
        if self.finished:
            return self.decision(None)
        dt = observation.get("dt_s", 0.0)
        if isinstance(dt, bool) or not isinstance(dt, (int, float)) or not math.isfinite(float(dt)) or float(dt) < 0.0:
            return self._stop("bad_observation", converged=False)
        self.ticks += 1
        self.elapsed_s += float(dt)
        if observation.get("cancelled") is True:
            return self._stop("cancelled", converged=False)
        if observation.get("lease_held") is not True:
            return self._stop("lease_lost", converged=False)
        if observation.get("respawned") is True:
            return self._stop("respawned", converged=False)
        pose_ok = observation.get("pose_ok")
        if pose_ok is not True:
            reason = observation.get("pose_error")
            if not isinstance(reason, str) or reason == "":
                reason = "pose_rejected"
            return self._stop(reason, converged=False)
        if self.elapsed_s > self.budget_s:
            return self._stop("deadline", converged=False)
        current = _unit(observation.get("camera_dir"))
        desired = _unit(observation.get("desired_dir"))
        if current is None or desired is None:
            return self._stop("camera_illegible", converged=False)
        error = angular_error_deg(current, desired)
        self.final_error_deg = error
        if self.initial_error_deg is None:
            self.initial_error_deg = error
        # Commanded error is not an observation. Convergence reads camera_dir.
        if error <= BAND_DEG:
            self.stable_ticks += 1
            if self.stable_ticks > STABLE_TICKS:
                self.hold_ticks += 1
                if self.hold_ticks >= HOLD_TICKS:
                    self.finished = True
                    self.converged = True
                    self.termination = "converged"
                    self.released = True
                    return self.decision(None)
            return self.decision(None)
        self.stable_ticks = 0
        self.hold_ticks = 0
        self._holding = False
        dx, dy = pulse_radians(current, desired)
        self.pulses += 1
        return self.decision((dx, dy))


async def execute_player_look_at(runtime: Any, pos: list[float], timeout_s: float) -> dict[str, Any]:
    from mcp.server.fastmcp.exceptions import ToolError

    from dayz_mcp.action_cursor import (
        binding_fence,
        call_bridge_fenced,
        fence_provenance,
        peer_announces,
    )
    from dayz_mcp.bridge_readiness import _client_peer_announces_command
    from dayz_mcp.tool_args import _require_float_list, _timeout

    point = _require_float_list(pos, 3, "pos")
    timeout = _timeout(timeout_s)
    budget = min(timeout, MAX_CONTROL_S)
    deadline = time.monotonic() + timeout
    snapshot = getattr(runtime, "_session_state_snapshot", None)
    if callable(snapshot):
        lease_token, _ticket = snapshot()
    else:
        lease_token = getattr(runtime, "active_lease_token", None)
    left = deadline - time.monotonic()
    if left <= 0.0:
        raise ToolError("timeout")
    status = await runtime.bridge_status_payload(timeout_s=left)
    if time.monotonic() > deadline:
        raise ToolError("timeout")
    fence_before = binding_fence(status, ("client",))
    if fence_before is None:
        raise ToolError("broker_infrastructure_missing")
    run_id = fence_before["peers"]["client"]["run_id"]
    # Embedded Runtime has no client-side lease snapshot. Its enqueue admits
    # the lease. Client mode fails here, before dispatch.
    if callable(snapshot):
        refusal = admit_player_look_at(lease_token=lease_token, run_id=run_id)
        if refusal is not None:
            raise ToolError(refusal)
    elif run_id is None:
        raise ToolError("no_active_run")
    if not _client_peer_announces_command(status, "player_look_at"):
        raise ToolError("bridge_capability_missing")
    if not peer_announces(status, "client_peer", "player_look_at"):
        raise ToolError("bridge_capability_missing")
    left = deadline - time.monotonic()
    if left <= 0.0:
        raise ToolError("timeout")
    raw = await call_bridge_fenced(
        runtime,
        "player_look_at",
        {"pos": point, "timeout_s": budget},
        "client",
        left,
        fence_before,
    )
    if time.monotonic() > deadline:
        raise ToolError("timeout")
    if isinstance(raw, dict) and raw.get("ok") is False:
        code = raw.get("error")
        if not isinstance(code, str) or code == "":
            code = "view_not_converged"
        raise ToolError(code)
    report = raw.get("look_at") if isinstance(raw, dict) else None
    try:
        accepted = accept_bridge_look_at(report)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    left = deadline - time.monotonic()
    if left <= 0.0:
        raise ToolError("timeout")
    status_after = await runtime.bridge_status_payload(timeout_s=left)
    if time.monotonic() > deadline:
        raise ToolError("timeout")
    if binding_fence(status_after, ("client",)) != fence_before:
        raise ToolError("snapshot_invalidated")
    _run_id, generation, client_instance = fence_provenance(fence_before)
    return {
        "ok": True,
        "schema_version": 1,
        "converged": True,
        "error_initial_deg": accepted.get("error_initial_deg"),
        "error_final_deg": accepted.get("error_final_deg"),
        "ticks": accepted.get("ticks"),
        "stable_ticks": accepted.get("stable_ticks"),
        "hold_ticks": accepted.get("hold_ticks"),
        "duration_s": accepted.get("duration_s"),
        "pose": accepted.get("pose"),
        "termination": "converged",
        "run_id": run_id,
        "client_instance": client_instance,
        "generation": generation,
    }


def _bridge_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise ValueError("view_not_converged")


def _bridge_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("view_not_converged")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("view_not_converged")
    return number


def accept_bridge_look_at(report: object) -> dict[str, Any]:
    """Fail closed unless the bridge report shows measured convergence."""
    required = (
        "converged",
        "termination",
        "error_initial_deg",
        "error_final_deg",
        "ticks",
        "duration_s",
        "pose",
        "stable_ticks",
        "hold_ticks",
    )
    if not isinstance(report, dict) or any(name not in report for name in required):
        raise ValueError("view_not_converged")
    if _bridge_bool(report.get("converged")) is not True or report.get("termination") != "converged":
        raise ValueError(str(report.get("termination") or "view_not_converged"))
    initial = _bridge_number(report.get("error_initial_deg"))
    final = _bridge_number(report.get("error_final_deg"))
    duration = _bridge_number(report.get("duration_s"))
    stable = report.get("stable_ticks")
    hold = report.get("hold_ticks")
    ticks = report.get("ticks")
    pose = report.get("pose")
    if (
        initial < 0.0
        or initial > 180.0
        or final < 0.0
        or final > BAND_DEG
        or duration < 0.0
        or duration > MAX_CONTROL_S
        or pose != "first_idle"
        or not isinstance(stable, int)
        or isinstance(stable, bool)
        or stable < STABLE_TICKS + HOLD_TICKS
        or not isinstance(hold, int)
        or isinstance(hold, bool)
        or hold != stable - STABLE_TICKS
        or not isinstance(ticks, int)
        or isinstance(ticks, bool)
        or ticks < stable
    ):
        raise ValueError("view_not_converged")
    accepted = dict(report)
    accepted["converged"] = True
    accepted["error_initial_deg"] = initial
    accepted["error_final_deg"] = final
    accepted["duration_s"] = duration
    return accepted
