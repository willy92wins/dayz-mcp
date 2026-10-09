"""action_hold: one continuous action, observed until it ends.

The public tool accepts the same target selectors as action_use. The client
command is always action_hold, which an older PBO does not announce. A valid
reply is an observation (ok true) even when the action does not finish.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError
from dayz_mcp.tool_args import _bad_args, _finite_float, _require_vec3, _timeout


HOLD_PROTOCOL = 1
HOLD_TIMEOUT_DEFAULT_S = 30.0
HOLD_TIMEOUT_MAX_S = 120.0
HOLD_CALL_TIMEOUT_DEFAULT_S = 45.0
HOLD_CALL_MARGIN_S = 15.0
HOLD_END_STATES = frozenset({"finished", "cancel", "timeout", "rejected", "unknown"})
CYCLES_SCOPE = "client_progress"


@dataclass(frozen=True)
class ParsedActionSelector:
    """Target resolution shared with action_use. command is the legacy verb."""

    mode: str
    command: str
    args: dict[str, Any]
    announce: str | None
    echo_key: str | None
    echo_value: Any
    unsupported: str | None


def parse_action_selector(
    action: str,
    classname: str,
    pos: list[float] | None,
    radius: float,
    target: str,
    door_index: int | None,
    component_index: int | None,
    cursor_pos: list[float] | None,
) -> ParsedActionSelector:
    """Validate one action_use / action_hold target. Raises ToolError."""

    if not isinstance(action, str) or action == "":
        raise ToolError(_bad_args("action", action, "be a non-empty string"))
    if not isinstance(classname, str):
        raise ToolError(_bad_args("classname", classname, "be a string"))
    if not isinstance(target, str) or target not in {"world", "hands", "self"}:
        raise ToolError(_bad_args("target", target, "be 'world', 'hands' or 'self'"))
    if pos is not None and target != "world":
        raise ToolError(_bad_args("pos", pos, "be omitted unless target is world"))
    if classname != "" and target == "self":
        raise ToolError(
            _bad_args("classname", classname, "be omitted when target is self")
        )
    if door_index is not None:
        if (
            isinstance(door_index, bool)
            or not isinstance(door_index, int)
            or door_index < 0
            or door_index >= 64
        ):
            raise ToolError(_bad_args("door_index", door_index, "be an int from 0 to 63"))
        if target != "world":
            raise ToolError(
                _bad_args("door_index", door_index, "be omitted unless target is world")
            )
        if classname == "":
            raise ToolError(
                _bad_args(
                    "classname",
                    classname,
                    "be a non-empty string when door_index is set",
                )
            )
    radius_error = _bad_args(
        "radius",
        radius,
        "be a finite number greater than 0 and at most 200",
    )
    radius_value = _finite_float(radius, radius_error)
    if radius_value <= 0.0 or radius_value > 200.0:
        raise ToolError(radius_error)
    component_mode = component_index is not None or cursor_pos is not None
    if component_mode:
        if component_index is None or cursor_pos is None:
            raise ToolError(
                _bad_args(
                    "component_index",
                    component_index,
                    "be set together with cursor_pos",
                )
            )
        if (
            isinstance(component_index, bool)
            or not isinstance(component_index, int)
            or component_index < 0
            or component_index > 2_147_483_647
        ):
            raise ToolError(
                _bad_args(
                    "component_index",
                    component_index,
                    "be an int from 0 to 2147483647",
                )
            )
        if door_index is not None:
            raise ToolError(
                _bad_args(
                    "component_index",
                    component_index,
                    "be omitted when door_index is set",
                )
            )
        if target != "world":
            raise ToolError(
                _bad_args(
                    "component_index",
                    component_index,
                    "be omitted unless target is world",
                )
            )
        if classname == "":
            raise ToolError(
                _bad_args(
                    "classname",
                    classname,
                    "be a non-empty string when component_index is set",
                )
            )
    args: dict[str, Any] = {"action": action, "radius": radius_value}
    if classname != "":
        args["classname"] = classname
    if pos is not None:
        args["pos"] = _require_vec3(pos, "pos")
    if component_mode:
        args["component_index"] = component_index
        args["cursor_pos"] = _require_vec3(cursor_pos, "cursor_pos")
        return ParsedActionSelector(
            "component",
            "action_use_component",
            args,
            "action_use_component",
            "component_index",
            component_index,
            "component_not_supported",
        )
    if door_index is not None:
        args["door_index"] = door_index
        return ParsedActionSelector(
            "door",
            "action_use_door",
            args,
            "action_use_door",
            "door_index",
            door_index,
            "door_not_supported",
        )
    if target == "world":
        return ParsedActionSelector("world", "action_use", args, None, None, None, None)
    args["target"] = target
    return ParsedActionSelector(
        target,
        "action_use_target",
        args,
        "action_use_target",
        "target",
        target,
        "target_not_supported",
    )


def validate_hold_budget(hold_timeout_s: object, timeout_s: object) -> tuple[float, float]:
    """Finite hold budget and the wider Python budget around it."""

    hold_error = _bad_args(
        "hold_timeout_s",
        hold_timeout_s,
        "be a finite number greater than 0 and at most 120",
    )
    if isinstance(hold_timeout_s, bool) or not isinstance(hold_timeout_s, (int, float)):
        raise ToolError(hold_error)
    hold_value = _finite_float(hold_timeout_s, hold_error)
    if hold_value <= 0.0 or hold_value > HOLD_TIMEOUT_MAX_S:
        raise ToolError(hold_error)
    timeout_error = _bad_args(
        "timeout_s",
        timeout_s,
        "be a finite number, at least hold_timeout_s + 15 and at most 300",
    )
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise ToolError(timeout_error)
    timeout_value = _finite_float(timeout_s, timeout_error)
    if (
        timeout_value < hold_value + HOLD_CALL_MARGIN_S
        or timeout_value > 300.0
    ):
        raise ToolError(timeout_error)
    # Same ceiling action_use uses, after the hold margin has been checked.
    _timeout(timeout_value)
    return hold_value, timeout_value


def hold_command_args(parsed: ParsedActionSelector, hold_timeout_s: float) -> dict[str, Any]:
    """Wire args for the single action_hold command. Selector is closed."""

    args = dict(parsed.args)
    args["selector"] = parsed.mode
    args["hold_timeout_s"] = hold_timeout_s
    if parsed.mode == "door":
        args["door_index_set"] = True
    if parsed.mode == "component":
        args["component_index_set"] = True
    return args


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, 1) and not isinstance(value, bool):
        return bool(value)
    return None


def normalize_hold_observation(result: dict[str, Any], minted_hold_id: str) -> dict[str, Any]:
    """Public observation. Zero cycles and already_placed false stay present.

    A reply that is not protocol 1, or whose hold_id is not the one the daemon
    assigned to this call, is not evidence of a sustained action.
    """

    if not isinstance(result, dict):
        raise ToolError("hold_result_incompatible")
    protocol = result.get("hold_protocol")
    if isinstance(protocol, bool) or protocol != HOLD_PROTOCOL:
        raise ToolError("hold_result_incompatible")
    hold_id = result.get("hold_id")
    if not isinstance(hold_id, str) or hold_id == "" or hold_id != minted_hold_id:
        raise ToolError("hold_result_incompatible")
    end_state = result.get("end_state")
    if end_state not in HOLD_END_STATES:
        raise ToolError("hold_result_incompatible")
    reason = result.get("reason")
    if not isinstance(reason, str) or reason == "":
        raise ToolError("hold_result_incompatible")
    started = _as_bool(result.get("started"))
    flag_restored = _as_bool(result.get("flag_restored"))
    cleanup_complete = _as_bool(result.get("cleanup_complete"))
    if started is None or flag_restored is None or cleanup_complete is None:
        raise ToolError("hold_result_incompatible")
    duration = result.get("duration_s")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ToolError("hold_result_incompatible")
    if not math.isfinite(float(duration)) or float(duration) < 0.0:
        raise ToolError("hold_result_incompatible")
    cycles = result.get("completed_cycles")
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 0:
        raise ToolError("hold_result_incompatible")
    state_known = _as_bool(result.get("action_state_known"))
    action_state: int | None
    if state_known is True:
        raw_state = result.get("action_state")
        if isinstance(raw_state, bool) or not isinstance(raw_state, int):
            raise ToolError("hold_result_incompatible")
        action_state = raw_state
    elif state_known is False or result.get("action_state") is None:
        action_state = None
    else:
        raise ToolError("hold_result_incompatible")
    public: dict[str, Any] = {
        "ok": True,
        "hold_protocol": HOLD_PROTOCOL,
        "hold_id": hold_id,
        "started": started,
        "end_state": end_state,
        "reason": reason,
        "action_state": action_state,
        "duration_s": float(duration),
        "completed_cycles": cycles,
        "cycles_scope": CYCLES_SCOPE,
        "flag_restored": flag_restored,
        "cleanup_complete": cleanup_complete,
    }
    placed_known = _as_bool(result.get("placed_known"))
    if placed_known is True:
        placed = _as_bool(result.get("already_placed"))
        if placed is None:
            raise ToolError("hold_result_incompatible")
        public["already_placed"] = placed
    return public
