"""camera_path_play request contract (inbox d026).

A keyed camera path the client evaluates every frame on the scripted camera
camera_set uses: MCPCameraPath in addon/scripts/5_Mission/MCP_CameraPath.c,
dispatched by DispatchCameraPathPlay in addon/scripts/5_Mission/MCPClientBridge.c.
Every bound below mirrors theirs, so a request this module builds is one the
bridge accepts, and the loopback ingress checks the same wire shape with
validate_command_args.
"""
from __future__ import annotations

import math
import re
from typing import Any


PATH_MODES = ("play", "state", "stop")
EASINGS = ("linear", "smooth")
PATH_STATES = ("idle", "playing", "finished", "aborted")
KEYS_MIN = 2
KEYS_MAX = 64
# The last key's t, in seconds.
DURATION_MAX_S = 600.0
PATH_ID_MAX_CHARS = 64
PATH_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{0,64}\Z")
_KEY_FIELDS = frozenset({"t", "cam_pos", "cam_orientation", "look_at", "fov"})
_AIM_FIELDS = ("cam_orientation", "look_at")
_PLAY_WIRE_KEYS = frozenset({"mode", "keys", "easing", "path_id"})
_QUERY_WIRE_KEYS = frozenset({"mode", "path_id"})


def _real(value: object) -> float | None:
    """value as a finite float, or None. A bool is not a number here."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _vec3(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    items = [_real(item) for item in value]
    if any(item is None for item in items):
        return None
    return [float(item) for item in items]  # type: ignore[arg-type]


def is_path_id(value: object) -> bool:
    """Up to 64 characters from A-Z a-z 0-9 _ -; empty is no label."""

    return isinstance(value, str) and PATH_ID_PATTERN.fullmatch(value) is not None


def normalize_keys(keys: object) -> list[dict[str, Any]]:
    """The keys of a play as the wire carries them, or ValueError("bad_args: ...").

    t in seconds, the first exactly 0, strictly increasing, the last at most
    600; cam_pos [x, y, z]; cam_orientation [yaw, pitch, roll] in degrees or
    look_at [x, y, z], the same one on every key; fov in radians above 0, on
    every key or on none (camera_set's fov 0 keeps the FOV, which is no value
    to interpolate).
    """

    if not isinstance(keys, list) or not KEYS_MIN <= len(keys) <= KEYS_MAX:
        raise ValueError(
            f"bad_args: keys must be a list of {KEYS_MIN} to {KEYS_MAX} keys"
        )
    aim_field: str | None = None
    with_fov: bool | None = None
    previous_t: float | None = None
    normalized: list[dict[str, Any]] = []
    for index, key in enumerate(keys):
        where = f"keys[{index}]"
        if not isinstance(key, dict):
            raise ValueError(
                f"bad_args: {where} must be an object with t, cam_pos and "
                "cam_orientation or look_at"
            )
        unknown = sorted(str(name) for name in key if name not in _KEY_FIELDS)
        if unknown:
            raise ValueError(
                f"bad_args: {where} has unknown fields {unknown}; a key takes "
                "t, cam_pos, cam_orientation or look_at, and fov"
            )
        t = _real(key.get("t"))
        if t is None:
            raise ValueError(f"bad_args: {where}.t must be a finite number of seconds")
        if index == 0 and t != 0.0:
            raise ValueError("bad_args: keys[0].t must be 0")
        if previous_t is not None and t <= previous_t:
            raise ValueError(
                f"bad_args: {where}.t must be greater than keys[{index - 1}].t "
                "(t is strictly increasing)"
            )
        if t > DURATION_MAX_S:
            raise ValueError(
                f"bad_args: {where}.t must be at most {DURATION_MAX_S:g} seconds"
            )
        pos = _vec3(key.get("cam_pos"))
        if pos is None:
            raise ValueError(f"bad_args: {where}.cam_pos must be a list of 3 finite numbers")
        aims = [name for name in _AIM_FIELDS if name in key]
        if len(aims) != 1:
            raise ValueError(
                f"bad_args: {where} must carry exactly one of cam_orientation or look_at"
            )
        if aim_field is None:
            aim_field = aims[0]
        elif aims[0] != aim_field:
            raise ValueError(
                f"bad_args: {where} carries {aims[0]} and keys[0] carries "
                f"{aim_field}; every key carries the same one"
            )
        aim = _vec3(key[aim_field])
        if aim is None:
            raise ValueError(
                f"bad_args: {where}.{aim_field} must be a list of 3 finite numbers"
            )
        has_fov = "fov" in key
        if with_fov is None:
            with_fov = has_fov
        elif has_fov != with_fov:
            raise ValueError("bad_args: fov goes on every key or on none")
        entry: dict[str, Any] = {"t": t, "cam_pos": pos, aim_field: aim}
        if has_fov:
            fov = _real(key["fov"])
            if fov is None or fov <= 0.0:
                raise ValueError(
                    f"bad_args: {where}.fov must be a finite number of radians above 0"
                )
            entry["fov"] = fov
        normalized.append(entry)
        previous_t = t
    return normalized


def normalize_request(
    mode: object,
    keys: object,
    easing: object,
    path_id: object,
) -> dict[str, Any]:
    """The camera_path_play wire args, or ValueError("bad_args: ...").

    play carries keys, easing and path_id; state and stop carry path_id only
    and take no keys.
    """

    if not isinstance(mode, str) or mode not in PATH_MODES:
        raise ValueError("bad_args: mode must be one of play, state, stop")
    if not isinstance(easing, str) or easing not in EASINGS:
        raise ValueError("bad_args: easing must be linear or smooth")
    if not is_path_id(path_id):
        raise ValueError(
            f"bad_args: path_id must be up to {PATH_ID_MAX_CHARS} characters "
            "from A-Z a-z 0-9 _ - (empty for none)"
        )
    if mode == "play":
        return {
            "mode": mode,
            "keys": normalize_keys(keys),
            "easing": easing,
            "path_id": path_id,
        }
    if keys is not None:
        raise ValueError("bad_args: keys travel with mode=play only")
    return {"mode": mode, "path_id": path_id}


def validate_command_args(args: object) -> tuple[bool, str | None]:
    """Daemon-path check of the shape normalize_request builds.

    Returns the fixed token bad_args, never caller text: the detailed
    message stays on the MCP tool path.
    """

    if not isinstance(args, dict):
        return False, "bad_args"
    mode = args.get("mode")
    expected = _PLAY_WIRE_KEYS if mode == "play" else _QUERY_WIRE_KEYS
    if set(args) != expected:
        return False, "bad_args"
    try:
        normalize_request(
            mode,
            args.get("keys"),
            args.get("easing", EASINGS[1]),
            args.get("path_id"),
        )
    except ValueError:
        return False, "bad_args"
    return True, None
