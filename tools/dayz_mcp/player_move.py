"""player_move request and result contract (inbox c1cb).

Walks the on-foot local player through the engine's own move command: the
owner client sets HumanInputController.OverrideMovementSpeed and
OverrideMovementAngle ENABLED every command tick, and the server applies the
same two overrides to its copy of the player in every move it consumes and in
its CommandHandler (the design measured in game, spike key 106). The owner side
is MCPPlayerMoveControl and the server copy is PlayerBase in
addon/scripts/4_World/MCP_PlayerMove.c; the bridge handler is
DispatchPlayerMove in addon/scripts/5_Mission/MCPClientBridge.c. Every bound
below mirrors MCPPlayerMoveControl, and a test keeps the copies equal, so a
request this module builds is one the bridge accepts.

Each phase and direction sends exactly the keys of its ingress variant
(loopback._player_move_variant): no number travels without its phase or its
direction, so an absent key never reaches Enforce as 0
(fb-20260930-065425-8779).
"""
from __future__ import annotations

import math
from typing import Any


# Mirror MCPPlayerMoveControl (HOLD_MAX_S, PRESS_MAX_TTL_S, ANGLE_ABS_MAX_DEG,
# HEADING_MAX_DEG, ARRIVE_RADIUS_MIN_M, ARRIVE_RADIUS_MAX_M, TO_MAX_DISTANCE_M,
# SERVER_DEADMAN_MARGIN_S). Values outside are refused, never clamped.
# heading_deg excludes 360.
HOLD_MAX_S = 30.0
PRESS_MAX_TTL_S = 30.0
ANGLE_ABS_MAX_DEG = 180.0
HEADING_MAX_DEG = 360.0
ARRIVE_RADIUS_MIN_M = 0.2
ARRIVE_RADIUS_MAX_M = 5.0
ARRIVE_RADIUS_DEFAULT_M = 0.5
TO_MAX_DISTANCE_M = 200.0
SERVER_DEADMAN_MARGIN_S = 0.5
# The OverrideMovementSpeed value of each speed name: 1 walk, 2 run (jog),
# 3 sprint (human.c:24-25).
SPEEDS: dict[str, float] = {"walk": 1.0, "jog": 2.0, "sprint": 3.0}
PHASES = ("hold", "press", "release")
DIRECTIONS = ("angle", "heading", "to")
# A hold answers after its release, hold_s after the start: the tool waits at
# least hold_s plus this, the slack MCPClientBridge.c PLAYER_MOVE_JOB_SLACK_S
# gives the job before it releases the move itself.
HOLD_SLACK_S = 5.0
RELEASED_BY = frozenset(
    {"hold", "phase", "ttl", "arrived", "restore", "player_changed", "shutdown"}
)
SERVER_REQUEST_STATES = frozenset({"sent", "unavailable"})

# The reply fields of each direction; the other directions' are dropped.
_DIRECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "angle": ("angle_deg",),
    "heading": ("heading_deg",),
    "to": ("to", "arrive_radius_m"),
}
_ALL_DIRECTION_FIELDS = frozenset(
    field for fields in _DIRECTION_FIELDS.values() for field in fields
)


def _refusal(code: str, field: str, value: object, requirement: str) -> str:
    return f"{code}: {field} {value!r} must {requirement}"


def _finite_number(value: object) -> float | None:
    """The value as a finite float, or None. Never a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(converted):
        return None
    return converted


def _seconds(code: str, field: str, value: object, maximum: float, when: str) -> float:
    requirement = f"be a finite number in (0, {maximum:g}] with {when}"
    seconds = _finite_number(value)
    if seconds is None or seconds <= 0.0 or seconds > maximum:
        raise ValueError(_refusal(code, field, value, requirement))
    return seconds


def _target(value: object) -> list[float]:
    requirement = (
        "be a list of 3 finite numbers [x, y, z], at most "
        f"{TO_MAX_DISTANCE_M:g} m from the player on the horizontal plane"
    )
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(_refusal("bad_to", "to", value, requirement))
    target = [_finite_number(item) for item in value]
    if any(item is None for item in target):
        raise ValueError(_refusal("bad_to", "to", value, requirement))
    return [float(item) for item in target]  # type: ignore[arg-type]


def normalize_request(
    speed: object,
    phase: object,
    hold_s: object,
    ttl_s: object,
    angle_deg: object,
    heading_deg: object,
    to: object,
    arrive_radius_m: object,
) -> dict[str, Any]:
    """The player_move wire args, or ValueError whose text is the refusal.

    The codes are the bridge's: bad_args (phase, two directions, a value its
    phase or direction does not take), bad_speed, bad_angle_deg,
    bad_heading_deg, bad_to (a target farther than 200 m is the bridge's own
    refusal: only it knows where the player is), bad_arrive_radius_m,
    bad_hold_s and bad_ttl_s. speed and arrive_radius_m are checked on every
    call, because they have defaults; arrive_radius_m is sent only with to.
    """
    if not isinstance(phase, str) or phase not in PHASES:
        raise ValueError(
            _refusal("bad_args", "phase", phase, "be 'hold', 'press' or 'release'")
        )
    if not isinstance(speed, str) or speed not in SPEEDS:
        raise ValueError(
            _refusal("bad_speed", "speed", speed, "be one of 'walk', 'jog' or 'sprint'")
        )
    radius = _finite_number(arrive_radius_m)
    if radius is None or radius < ARRIVE_RADIUS_MIN_M or radius > ARRIVE_RADIUS_MAX_M:
        raise ValueError(
            _refusal(
                "bad_arrive_radius_m",
                "arrive_radius_m",
                arrive_radius_m,
                f"be a finite number from {ARRIVE_RADIUS_MIN_M:g} to "
                f"{ARRIVE_RADIUS_MAX_M:g} metres",
            )
        )
    if phase == "release":
        for name, value in (
            ("hold_s", hold_s),
            ("ttl_s", ttl_s),
            ("angle_deg", angle_deg),
            ("heading_deg", heading_deg),
            ("to", to),
        ):
            if value is not None:
                raise ValueError(
                    _refusal("bad_args", name, value, "be omitted with phase='release'")
                )
        return {"mode": "release"}
    given = [
        name
        for name, value in (("angle_deg", angle_deg), ("heading_deg", heading_deg), ("to", to))
        if value is not None
    ]
    if len(given) > 1:
        raise ValueError(
            _refusal(
                "bad_args",
                "direction",
                given,
                "be at most one of angle_deg, heading_deg or to",
            )
        )
    args: dict[str, Any] = {"mode": phase, "speed": SPEEDS[speed]}
    if phase == "hold":
        args["hold_s"] = _seconds("bad_hold_s", "hold_s", hold_s, HOLD_MAX_S, "phase='hold'")
        if ttl_s is not None:
            raise ValueError(
                _refusal("bad_args", "ttl_s", ttl_s, "be omitted unless phase is 'press'")
            )
    else:
        args["hold_ttl_s"] = _seconds(
            "bad_ttl_s", "ttl_s", ttl_s, PRESS_MAX_TTL_S, "phase='press'"
        )
        if hold_s is not None:
            raise ValueError(
                _refusal("bad_args", "hold_s", hold_s, "be omitted unless phase is 'hold'")
            )
    if heading_deg is not None:
        heading = _finite_number(heading_deg)
        if heading is None or heading < 0.0 or heading >= HEADING_MAX_DEG:
            raise ValueError(
                _refusal(
                    "bad_heading_deg",
                    "heading_deg",
                    heading_deg,
                    "be a finite compass heading in degrees from 0 to below "
                    f"{HEADING_MAX_DEG:g}",
                )
            )
        args["direction"] = "heading"
        args["heading"] = heading
    elif to is not None:
        args["direction"] = "to"
        args["to"] = _target(to)
        args["radius"] = radius
    else:
        angle = 0.0 if angle_deg is None else _finite_number(angle_deg)
        if angle is None or angle < -ANGLE_ABS_MAX_DEG or angle > ANGLE_ABS_MAX_DEG:
            raise ValueError(
                _refusal(
                    "bad_angle_deg",
                    "angle_deg",
                    angle_deg,
                    f"be a finite number of degrees from {-ANGLE_ABS_MAX_DEG:g} to "
                    f"{ANGLE_ABS_MAX_DEG:g}",
                )
            )
        args["direction"] = "angle"
        args["angle_deg"] = angle
    return args


def normalize_bridge_result(result: object) -> dict[str, Any]:
    """Keep the reply's direction fields only and type arrived as a bool.

    MCPPlayerMove is one flat class, so a reply carries angle_deg, heading_deg,
    to and arrive_radius_m whatever the direction; only the fields of its
    direction are its answer. arrived may arrive as 0/1; anything else is
    refused, never guessed. Every other value passes untouched.
    """
    if not isinstance(result, dict):
        raise ValueError("bad_bridge_move_result")
    reply = result.get("player_move")
    if not isinstance(reply, dict):
        raise ValueError("bad_bridge_move_result")
    arrived = reply.get("arrived", False)
    if isinstance(arrived, bool):
        normalized_arrived = arrived
    elif type(arrived) is int and arrived in (0, 1):
        normalized_arrived = bool(arrived)
    else:
        raise ValueError("bad_bridge_move_boolean")
    keep = set(_DIRECTION_FIELDS.get(str(reply.get("direction")), ()))
    cleaned = {
        key: value
        for key, value in reply.items()
        if key not in _ALL_DIRECTION_FIELDS or key in keep
    }
    cleaned["arrived"] = normalized_arrived
    payload = dict(result)
    payload["player_move"] = cleaned
    return payload
