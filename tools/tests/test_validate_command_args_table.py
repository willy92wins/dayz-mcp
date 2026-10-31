"""Characterize command argument validation before its table refactor.

The corpus pins accepted payload variants and representative failures so the
declarative implementation cannot silently widen or narrow the wire contract.
"""
from __future__ import annotations

import unittest

from dayz_mcp import loopback


_Result = tuple[bool, str | None]
_Case = tuple[str, dict[str, object], _Result]


def _anim_args(*, drop: str = "", **overrides: object) -> dict[str, object]:
    """A valid anim_timeline payload with `overrides` applied and `drop` removed."""
    args: dict[str, object] = {
        "mode": "start",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "cursor": 0,
        "limit": 64,
        "sample_hz": 20,
        "max_samples": 4096,
        "sources": [],
    }
    args.update(overrides)
    if drop:
        del args[drop]
    return args


def _trigger_args(
    kind: str = "key", edge: str = "click", *, drop: str = "", **overrides: object
) -> dict[str, object]:
    """A valid input_trigger payload for kind and edge, `overrides` applied, `drop` removed."""
    args: dict[str, object] = {"trigger_kind": kind, "trigger_edge": edge}
    if kind == "key":
        args.update(trigger_entry="game", dik=1)
    else:
        args["name"] = "UAGear"
    if edge == "hold":
        args["hold_s"] = 1.0
    if edge == "press":
        args["hold_ttl_s"] = 2.0
    args.update(overrides)
    if drop:
        del args[drop]
    return args


def _move_args(
    phase: str = "hold", direction: str = "angle", *, drop: str = "", **overrides: object
) -> dict[str, object]:
    """A valid player_move payload for phase and direction, `overrides` applied, `drop` removed."""
    args: dict[str, object] = {"mode": phase, "speed": 1.0, "direction": direction}
    if phase == "hold":
        args["hold_s"] = 5.0
    else:
        args["hold_ttl_s"] = 5.0
    if direction == "angle":
        args["angle_deg"] = 0.0
    elif direction == "heading":
        args["heading"] = 90.0
    else:
        args.update(to=[1.0, 2.0, 3.0], radius=0.5)
    args.update(overrides)
    if drop:
        del args[drop]
    return args


def _player_trace_args(*, drop: str = "", **overrides: object) -> dict[str, object]:
    """A valid player_trace payload with `overrides` applied and `drop` removed."""
    args: dict[str, object] = {
        "mode": "start",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "cursor": 0,
        "limit": 64,
        "sample_hz": 20,
        "max_samples": 4096,
    }
    args.update(overrides)
    if drop:
        del args[drop]
    return args


def _focus_args(
    cam_mode: str = "orient", aim: str = "cam_orientation", *, drop: str = "", **overrides: object
) -> dict[str, object]:
    """A valid camera_set_focus payload for cam_mode and aim, `overrides` applied, `drop` removed."""
    args: dict[str, object] = {"cam_mode": cam_mode, "fov": 0.0, "settle_ticks": 3}
    if cam_mode == "matrix":
        args["cam_matrix"] = [0.0] * 12
    else:
        args["cam_pos"] = [0.0, 2.0, 0.0]
        args[aim] = [5.0, 1.0, 5.0] if aim == "look_at" else [90.0, 0.0, 0.0]
    args.update(focus_distance=2.0, focus_blur=1.5)
    args.update(overrides)
    if drop:
        del args[drop]
    return args


def _path_key(t: float, **overrides: object) -> dict[str, object]:
    """One camera_path_play key at t: cam_pos on a line, cam_orientation level."""
    key: dict[str, object] = {"t": t, "cam_pos": [t, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]}
    key.update(overrides)
    return key


def _path_args(*, drop: str = "", **overrides: object) -> dict[str, object]:
    """A valid camera_path_play play payload with `overrides` applied and `drop` removed."""
    args: dict[str, object] = {
        "mode": "play",
        "keys": [_path_key(0.0), _path_key(3.0), _path_key(6.0)],
        "easing": "linear",
        "path_id": "dolly_1",
    }
    args.update(overrides)
    if drop:
        del args[drop]
    return args


def _look_key(t: float, **overrides: object) -> dict[str, object]:
    """One look_at key with a FOV."""
    key: dict[str, object] = {"t": t, "cam_pos": [t, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0], "fov": 0.9}
    key.update(overrides)
    return key


# A valid world_spawn payload without the optional lifetime_s pair.
_SPAWN: dict[str, object] = {
    "type": "CivilianSedan",
    "pos": [1.0, 2.0, 3.0],
    "flags": 0,
    "rotation": 0,
}
# One world_spawn_batch item as the tool sends it (7f5d).
_BATCH_ITEM: dict[str, object] = {
    "type": "Barrel_Green",
    "pos": [1.0, 2.0, 3.0],
    "flags": 0,
    "rotation": 0,
}


_COMMAND_CASES: dict[str, tuple[_Case, ...]] = {
    "restore_gameplay": (
        ("valid_empty", {}, (True, None)),
        ("extra_mode", {"mode": "restore"}, (False, "bad_args")),
        ("extra_flag", {"force": False}, (False, "bad_args")),
    ),
    "player_respawn": (
        ("valid_empty", {}, (True, None)),
        ("extra_random", {"random": True}, (False, "bad_args")),
    ),
    "key_press": (
        ("valid_esc", {"dik": 1}, (True, None)),
        ("missing_dik", {}, (False, "bad_args")),
        ("negative_dik", {"dik": -1}, (False, "bad_args")),
        ("bool_dik", {"dik": True}, (False, "bad_args")),
        ("extra_key", {"dik": 1, "extra": None}, (False, "bad_args")),
    ),
    "input_describe": (
        ("valid", {"name": "UAMoveForward"}, (True, None)),
        ("space_is_printable", {"name": "UA Move"}, (True, None)),
        ("length_128", {"name": "a" * 128}, (True, None)),
        ("missing_name", {}, (False, "bad_args")),
        ("empty_name", {"name": ""}, (False, "bad_args")),
        ("length_129", {"name": "a" * 129}, (False, "bad_args")),
        ("newline", {"name": "UA\n"}, (False, "bad_args")),
        ("tab", {"name": "UA\t"}, (False, "bad_args")),
        ("del", {"name": "UA\x7f"}, (False, "bad_args")),
        ("non_ascii", {"name": "UA\u00f1"}, (False, "bad_args")),
        ("extra_key", {"name": "UAMoveForward", "extra": None}, (False, "bad_args")),
    ),
    "input_trigger": (
        ("valid_key_click", _trigger_args("key", "click"), (True, None)),
        ("valid_key_release", _trigger_args("key", "release"), (True, None)),
        ("valid_key_hold", _trigger_args("key", "hold"), (True, None)),
        ("valid_key_press", _trigger_args("key", "press"), (True, None)),
        ("valid_input_click", _trigger_args("input", "click"), (True, None)),
        ("valid_input_release", _trigger_args("input", "release"), (True, None)),
        ("valid_input_hold", _trigger_args("input", "hold"), (True, None)),
        ("valid_input_press", _trigger_args("input", "press"), (True, None)),
        ("valid_mission_entry", _trigger_args(trigger_entry="mission"), (True, None)),
        ("valid_dik_0", _trigger_args(dik=0), (True, None)),
        ("valid_dik_255", _trigger_args(dik=255), (True, None)),
        ("valid_hold_max", _trigger_args(edge="hold", hold_s=10.0), (True, None)),
        ("valid_ttl_max", _trigger_args(edge="press", hold_ttl_s=30), (True, None)),
        ("valid_name_128", _trigger_args("input", name="a" * 128), (True, None)),
        ("missing_kind", _trigger_args(drop="trigger_kind"), (False, "bad_args")),
        ("missing_edge", _trigger_args(drop="trigger_edge"), (False, "bad_args")),
        ("missing_entry", _trigger_args(drop="trigger_entry"), (False, "bad_args")),
        ("missing_dik", _trigger_args(drop="dik"), (False, "bad_args")),
        ("release_missing_dik", _trigger_args(edge="release", drop="dik"), (False, "bad_args")),
        ("missing_name", _trigger_args("input", drop="name"), (False, "bad_args")),
        ("hold_missing_hold_s", _trigger_args(edge="hold", drop="hold_s"), (False, "bad_args")),
        ("press_missing_ttl", _trigger_args(edge="press", drop="hold_ttl_s"), (False, "bad_args")),
        ("unknown_kind", _trigger_args(trigger_kind="mouse"), (False, "bad_args")),
        ("unknown_edge", _trigger_args(trigger_edge="tap"), (False, "bad_args")),
        ("unhashable_edge", _trigger_args(trigger_edge=["click"]), (False, "bad_args")),
        ("unknown_entry", _trigger_args(trigger_entry="world"), (False, "bad_args")),
        ("dik_negative", _trigger_args(dik=-1), (False, "bad_args")),
        ("dik_256", _trigger_args(dik=256), (False, "bad_args")),
        ("dik_bool", _trigger_args(dik=True), (False, "bad_args")),
        ("dik_float", _trigger_args(dik=1.0), (False, "bad_args")),
        ("dik_string", _trigger_args(dik="1"), (False, "bad_args")),
        ("name_empty", _trigger_args("input", name=""), (False, "bad_args")),
        ("name_129", _trigger_args("input", name="a" * 129), (False, "bad_args")),
        ("name_newline", _trigger_args("input", name="UA\n"), (False, "bad_args")),
        ("name_non_ascii", _trigger_args("input", name="UA\u00f1"), (False, "bad_args")),
        ("hold_zero", _trigger_args(edge="hold", hold_s=0.0), (False, "bad_args")),
        ("hold_negative", _trigger_args(edge="hold", hold_s=-1.0), (False, "bad_args")),
        ("hold_over_max", _trigger_args(edge="hold", hold_s=10.5), (False, "bad_args")),
        ("hold_nan", _trigger_args(edge="hold", hold_s=float("nan")), (False, "bad_args")),
        ("hold_inf", _trigger_args(edge="hold", hold_s=float("inf")), (False, "bad_args")),
        ("hold_bool", _trigger_args(edge="hold", hold_s=True), (False, "bad_args")),
        ("ttl_zero", _trigger_args(edge="press", hold_ttl_s=0), (False, "bad_args")),
        ("ttl_over_max", _trigger_args(edge="press", hold_ttl_s=30.5), (False, "bad_args")),
        ("ttl_nan", _trigger_args(edge="press", hold_ttl_s=float("nan")), (False, "bad_args")),
        ("click_with_hold_s", _trigger_args(hold_s=1.0), (False, "bad_args")),
        ("click_with_ttl", _trigger_args(hold_ttl_s=1.0), (False, "bad_args")),
        ("release_with_ttl", _trigger_args(edge="release", hold_ttl_s=1.0), (False, "bad_args")),
        ("hold_with_ttl", _trigger_args(edge="hold", hold_ttl_s=1.0), (False, "bad_args")),
        ("press_with_hold_s", _trigger_args(edge="press", hold_s=1.0), (False, "bad_args")),
        ("key_with_name", _trigger_args(name="UAGear"), (False, "bad_args")),
        ("input_with_dik", _trigger_args("input", dik=1), (False, "bad_args")),
        ("input_with_entry", _trigger_args("input", trigger_entry="game"), (False, "bad_args")),
        ("extra_key", _trigger_args(extra=None), (False, "bad_args")),
    ),
    "player_move": (
        ("valid_release", {"mode": "release"}, (True, None)),
        ("valid_hold_angle", _move_args("hold", "angle"), (True, None)),
        ("valid_hold_heading", _move_args("hold", "heading"), (True, None)),
        ("valid_hold_to", _move_args("hold", "to"), (True, None)),
        ("valid_press_angle", _move_args("press", "angle"), (True, None)),
        ("valid_press_heading", _move_args("press", "heading"), (True, None)),
        ("valid_press_to", _move_args("press", "to"), (True, None)),
        ("valid_jog", _move_args(speed=2.0), (True, None)),
        ("valid_sprint_int", _move_args(speed=3), (True, None)),
        ("valid_angle_min", _move_args(angle_deg=-180.0), (True, None)),
        ("valid_angle_max", _move_args(angle_deg=180), (True, None)),
        ("valid_heading_zero", _move_args(direction="heading", heading=0.0), (True, None)),
        ("valid_heading_below_360", _move_args(direction="heading", heading=359.99), (True, None)),
        ("valid_hold_max", _move_args(hold_s=30.0), (True, None)),
        ("valid_ttl_max", _move_args("press", hold_ttl_s=30), (True, None)),
        ("valid_radius_min", _move_args(direction="to", radius=0.2), (True, None)),
        ("valid_radius_max", _move_args(direction="to", radius=5.0), (True, None)),
        ("release_with_speed", {"mode": "release", "speed": 1.0}, (False, "bad_args")),
        ("release_with_hold_s", {"mode": "release", "hold_s": 1.0}, (False, "bad_args")),
        ("unknown_mode", _move_args(mode="walk"), (False, "bad_args")),
        ("missing_mode", _move_args(drop="mode"), (False, "bad_args")),
        ("missing_speed", _move_args(drop="speed"), (False, "bad_args")),
        ("missing_direction", _move_args(drop="direction"), (False, "bad_args")),
        ("missing_hold_s", _move_args(drop="hold_s"), (False, "bad_args")),
        ("missing_ttl", _move_args("press", drop="hold_ttl_s"), (False, "bad_args")),
        ("missing_angle", _move_args(drop="angle_deg"), (False, "bad_args")),
        ("missing_heading", _move_args(direction="heading", drop="heading"), (False, "bad_args")),
        ("missing_to", _move_args(direction="to", drop="to"), (False, "bad_args")),
        ("missing_radius", _move_args(direction="to", drop="radius"), (False, "bad_args")),
        ("speed_zero", _move_args(speed=0.0), (False, "bad_args")),
        ("speed_four", _move_args(speed=4.0), (False, "bad_args")),
        ("speed_half", _move_args(speed=1.5), (False, "bad_args")),
        ("speed_bool", _move_args(speed=True), (False, "bad_args")),
        ("speed_nan", _move_args(speed=float("nan")), (False, "bad_args")),
        ("speed_name", _move_args(speed="walk"), (False, "bad_args")),
        ("unknown_direction", _move_args(direction="north"), (False, "bad_args")),
        ("angle_over", _move_args(angle_deg=180.5), (False, "bad_args")),
        ("angle_under", _move_args(angle_deg=-181.0), (False, "bad_args")),
        ("angle_nan", _move_args(angle_deg=float("nan")), (False, "bad_args")),
        ("angle_inf", _move_args(angle_deg=float("inf")), (False, "bad_args")),
        ("heading_360", _move_args(direction="heading", heading=360.0), (False, "bad_args")),
        ("heading_negative", _move_args(direction="heading", heading=-0.5), (False, "bad_args")),
        ("heading_nan", _move_args(direction="heading", heading=float("nan")), (False, "bad_args")),
        ("heading_bool", _move_args(direction="heading", heading=True), (False, "bad_args")),
        ("to_two_values", _move_args(direction="to", to=[1.0, 2.0]), (False, "bad_args")),
        ("to_nan", _move_args(direction="to", to=[1.0, float("nan"), 3.0]), (False, "bad_args")),
        ("radius_under", _move_args(direction="to", radius=0.19), (False, "bad_args")),
        ("radius_over", _move_args(direction="to", radius=5.01), (False, "bad_args")),
        ("radius_inf", _move_args(direction="to", radius=float("inf")), (False, "bad_args")),
        ("hold_zero", _move_args(hold_s=0.0), (False, "bad_args")),
        ("hold_over", _move_args(hold_s=30.5), (False, "bad_args")),
        ("hold_nan", _move_args(hold_s=float("nan")), (False, "bad_args")),
        ("ttl_zero", _move_args("press", hold_ttl_s=0), (False, "bad_args")),
        ("ttl_over", _move_args("press", hold_ttl_s=30.01), (False, "bad_args")),
        ("hold_with_ttl", _move_args(hold_ttl_s=1.0), (False, "bad_args")),
        ("press_with_hold_s", _move_args("press", hold_s=1.0), (False, "bad_args")),
        ("angle_with_heading", _move_args(heading=90.0), (False, "bad_args")),
        ("angle_with_to", _move_args(to=[1.0, 2.0, 3.0]), (False, "bad_args")),
        ("heading_with_angle", _move_args(direction="heading", angle_deg=0.0), (False, "bad_args")),
        ("to_with_heading", _move_args(direction="to", heading=0.0), (False, "bad_args")),
        ("angle_with_radius", _move_args(radius=0.5), (False, "bad_args")),
        ("extra_key", _move_args(extra=None), (False, "bad_args")),
    ),
    "player_trace": (
        ("valid", _player_trace_args(), (True, None)),
        ("valid_dump", _player_trace_args(mode="dump"), (True, None)),
        ("valid_bounds_low", _player_trace_args(limit=1, sample_hz=20, max_samples=2), (True, None)),
        ("valid_bounds_high", _player_trace_args(limit=64, sample_hz=60, max_samples=8192), (True, None)),
        ("unknown_mode", _player_trace_args(mode="START"), (False, "bad_args")),
        ("bad_trace_id", _player_trace_args(trace_id="A" * 32), (False, "bad_args")),
        ("short_trace_id", _player_trace_args(trace_id="a" * 31), (False, "bad_args")),
        ("negative_cursor", _player_trace_args(cursor=-1), (False, "bad_args")),
        ("limit_zero", _player_trace_args(limit=0), (False, "bad_args")),
        ("limit_65", _player_trace_args(limit=65), (False, "bad_args")),
        ("sample_hz_19", _player_trace_args(sample_hz=19), (False, "bad_args")),
        ("sample_hz_61", _player_trace_args(sample_hz=61), (False, "bad_args")),
        ("max_samples_1", _player_trace_args(max_samples=1), (False, "bad_args")),
        ("max_samples_8193", _player_trace_args(max_samples=8193), (False, "bad_args")),
        ("limit_bool", _player_trace_args(limit=True), (False, "bad_args")),
        ("missing_max_samples", _player_trace_args(drop="max_samples"), (False, "bad_args")),
        ("caller_path", _player_trace_args(path="C:\\x.jsonl"), (False, "bad_args")),
        ("extra_key", _player_trace_args(extra=None), (False, "bad_args")),
    ),
    "vehicle_trace": (
        (
            "valid",
            {
                "mode": "start",
                "trace_id": "0123456789abcdef0123456789abcdef",
                "cursor": 0,
                "limit": 64,
                "sample_hz": 20,
                "max_samples": 8192,
            },
            (True, None),
        ),
        (
            "extra_key",
            {
                "mode": "start",
                "trace_id": "0123456789abcdef0123456789abcdef",
                "cursor": 0,
                "limit": 64,
                "sample_hz": 20,
                "max_samples": 8192,
                "extra": None,
            },
            (False, "bad_args"),
        ),
        (
            "missing_max_samples",
            {
                "mode": "start",
                "trace_id": "0123456789abcdef0123456789abcdef",
                "cursor": 0,
                "limit": 64,
                "sample_hz": 20,
            },
            (False, "bad_args"),
        ),
        (
            "bad_trace_id",
            {
                "mode": "start",
                "trace_id": "0000000000000000000000000000000g",
                "cursor": 0,
                "limit": 64,
                "sample_hz": 20,
                "max_samples": 8192,
            },
            (False, "bad_args"),
        ),
        (
            "valid_dump",
            {
                "mode": "dump",
                "trace_id": "0123456789abcdef0123456789abcdef",
                "cursor": 0,
                "limit": 64,
                "sample_hz": 20,
                "max_samples": 8192,
            },
            (True, None),
        ),
        (
            "dump_caller_path",
            {
                "mode": "dump",
                "trace_id": "0123456789abcdef0123456789abcdef",
                "cursor": 0,
                "limit": 64,
                "sample_hz": 20,
                "max_samples": 8192,
                "path": r"C:\Windows\x.jsonl",
            },
            (False, "bad_args"),
        ),
    ),
    "vehicle_prepare_fixture": (
        (
            "valid",
            {
                "mode": "object_at",
                "type": "CivilianSedan",
                "pos": [1.0, 2.0, 3.0],
                "radius": 10.0,
            },
            (True, None),
        ),
        (
            "extra_key",
            {
                "mode": "object_at",
                "type": "CivilianSedan",
                "pos": [1.0, 2.0, 3.0],
                "radius": 10.0,
                "extra": None,
            },
            (False, "bad_args"),
        ),
        (
            "missing_radius",
            {
                "mode": "object_at",
                "type": "CivilianSedan",
                "pos": [1.0, 2.0, 3.0],
            },
            (False, "bad_args"),
        ),
        (
            "radius_not_positive",
            {
                "mode": "object_at",
                "type": "CivilianSedan",
                "pos": [1.0, 2.0, 3.0],
                "radius": 0.0,
            },
            (False, "bad_args"),
        ),
    ),
    "surface_query": (
        ("valid", {"x": 1.0, "z": 2.0}, (True, None)),
        ("extra_key", {"x": 1.0, "z": 2.0, "y": 3.0}, (False, "bad_args")),
        ("missing_z", {"x": 1.0}, (False, "bad_args")),
        ("bool_is_not_real", {"x": True, "z": 2.0}, (False, "bad_args")),
    ),
    "player_teleport": (
        ("without_uid", {"pos": [1.0, 2.0, 3.0]}, (False, "bad_args")),
        ("empty_uid", {"pos": [1.0, 2.0, 3.0], "uid": ""}, (False, "bad_args")),
        (
            "valid_with_uid",
            {"pos": [1.0, 2.0, 3.0], "uid": "player-1"},
            (True, None),
        ),
        (
            "extra_key",
            {"pos": [1.0, 2.0, 3.0], "uid": "player-1", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_pos", {"uid": "player-1"}, (False, "bad_args")),
        (
            "uid_wrong_type",
            {"pos": [1.0, 2.0, 3.0], "uid": 1},
            (False, "bad_args"),
        ),
    ),
    # full and godmode always travel as JSON booleans: the bridge reads an
    # absent key as false (fb-20260930-065425-8779).
    "player_heal": (
        ("valid_full", {"full": True, "uid": "player-1"}, (True, None)),
        ("valid_not_full_with_uid", {"full": False, "uid": "player-1"}, (True, None)),
        ("without_uid", {"full": True}, (False, "bad_args")),
        ("empty_uid", {"full": True, "uid": ""}, (False, "bad_args")),
        ("missing_full", {"uid": "player-1"}, (False, "bad_args")),
        ("full_int_is_not_bool", {"full": 1, "uid": "player-1"}, (False, "bad_args")),
        ("uid_wrong_type", {"full": True, "uid": 1}, (False, "bad_args")),
        ("extra_key", {"full": True, "uid": "player-1", "extra": None}, (False, "bad_args")),
    ),
    "player_kill": (
        ("valid_uid", {"uid": "player-1"}, (True, None)),
        ("empty_uid", {"uid": ""}, (False, "bad_args")),
        ("missing_uid", {}, (False, "bad_args")),
        ("uid_wrong_type", {"uid": 1}, (False, "bad_args")),
        ("extra_key", {"uid": "player-1", "id": "player-1"}, (False, "bad_args")),
    ),
    "bot_start": (
        (
            "valid",
            {
                "object_id": 1,
                "action": "PLAYER_BOT_RANDOMIZE_MOVEMENT",
                "bot_ttl_s": 5.0,
            },
            (True, None),
        ),
        ("ttl_zero", {"object_id": 1, "action": "PLAYER_BOT_RANDOMIZE_MOVEMENT", "bot_ttl_s": 0}, (False, "bad_args")),
        ("stop_action", {"object_id": 1, "action": "PLAYER_BOT_STOP_CURRENT", "bot_ttl_s": 5}, (False, "bad_args")),
        ("missing_ttl", {"object_id": 1, "action": "PLAYER_BOT_RANDOMIZE_MOVEMENT"}, (False, "bad_args")),
    ),
    "bot_stop": (
        ("valid", {"object_id": 1}, (True, None)),
        ("zero_id", {"object_id": 0}, (False, "bad_args")),
        ("missing", {}, (False, "bad_args")),
    ),
    "player_godmode": (
        ("valid_off", {"godmode": False, "uid": "player-1"}, (True, None)),
        ("valid_on_with_uid", {"godmode": True, "uid": "player-1"}, (True, None)),
        ("without_uid", {"godmode": False}, (False, "bad_args")),
        ("empty_uid", {"godmode": False, "uid": ""}, (False, "bad_args")),
        ("missing_godmode", {"uid": "player-1"}, (False, "bad_args")),
        ("godmode_int_is_not_bool", {"godmode": 0, "uid": "player-1"}, (False, "bad_args")),
        ("uid_wrong_type", {"godmode": True, "uid": 1}, (False, "bad_args")),
        ("extra_key", {"godmode": True, "uid": "player-1", "on": True}, (False, "bad_args")),
    ),
    # heading and speed travel with their _set flags (fb-20260930-065425-8779).
    "infected_drive": (
        (
            "valid_drive",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "heading": -360.0,
                "heading_set": True,
                "speed": 5.0,
                "speed_set": True,
            },
            (True, None),
        ),
        (
            "valid_release",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "mode": "release",
            },
            (True, None),
        ),
        (
            "extra_key",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "heading": 0.0,
                "heading_set": True,
                "speed": 1.0,
                "speed_set": True,
                "extra": None,
            },
            (False, "bad_args"),
        ),
        (
            "missing_speed",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "heading": 0.0,
                "heading_set": True,
            },
            (False, "bad_args"),
        ),
        (
            "speed_above_range",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "heading": 0.0,
                "heading_set": True,
                "speed": 5.01,
                "speed_set": True,
            },
            (False, "bad_args"),
        ),
        (
            "values_without_flags",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "heading": 0.0,
                "speed": 1.0,
            },
            (False, "bad_args"),
        ),
        (
            "heading_flag_false",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "heading": 0.0,
                "heading_set": False,
                "speed": 1.0,
                "speed_set": True,
            },
            (False, "bad_args"),
        ),
        (
            "release_with_flag",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "mode": "release",
                "heading_set": True,
            },
            (False, "bad_args"),
        ),
    ),
    # A write carries phase_set true beside phase (fb-20260930-065425-8779).
    "object_anim": (
        (
            "valid_read",
            {"type": "House", "pos": [1.0, 2.0, 3.0], "source": "door"},
            (True, None),
        ),
        (
            "valid_write",
            {
                "type": "House",
                "pos": [1.0, 2.0, 3.0],
                "source": "door",
                "phase": 0.5,
                "phase_set": True,
            },
            (True, None),
        ),
        (
            "valid_object_id_write",
            {"object_id": 1, "source": "door", "phase": 0.0, "phase_set": True},
            (True, None),
        ),
        (
            "phase_without_flag",
            {"type": "House", "pos": [1.0, 2.0, 3.0], "source": "door", "phase": 0.5},
            (False, "bad_args"),
        ),
        (
            "object_id_phase_without_flag",
            {"object_id": 1, "source": "door", "phase": 0.5},
            (False, "bad_args"),
        ),
        (
            "flag_without_phase",
            {"type": "House", "pos": [1.0, 2.0, 3.0], "source": "door", "phase_set": True},
            (False, "bad_args"),
        ),
        (
            "flag_false",
            {
                "type": "House",
                "pos": [1.0, 2.0, 3.0],
                "source": "door",
                "phase": 0.5,
                "phase_set": False,
            },
            (False, "bad_args"),
        ),
        (
            "flag_int",
            {"object_id": 1, "source": "door", "phase": 0.5, "phase_set": 1},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {
                "type": "House",
                "pos": [1.0, 2.0, 3.0],
                "source": "door",
                "extra": None,
            },
            (False, "bad_args"),
        ),
        (
            "missing_source",
            {"type": "House", "pos": [1.0, 2.0, 3.0]},
            (False, "bad_args"),
        ),
        (
            "phase_wrong_type",
            {
                "type": "House",
                "pos": [1.0, 2.0, 3.0],
                "source": "door",
                "phase": "half",
                "phase_set": True,
            },
            (False, "bad_args"),
        ),
    ),
    "vehicle_door": (
        (
            "valid_object_id_read",
            {"object_id": 1, "source": "DoorsDriver", "mode": "read"},
            (True, None),
        ),
        (
            "valid_object_id_open",
            {"object_id": 1, "source": "DoorsDriver", "mode": "open"},
            (True, None),
        ),
        (
            "valid_type_pos_close",
            {
                "type": "CivilianSedan",
                "pos": [1.0, 2.0, 3.0],
                "source": "DoorsTrunk",
                "mode": "close",
            },
            (True, None),
        ),
        (
            "missing_mode",
            {"object_id": 1, "source": "DoorsDriver"},
            (False, "bad_args"),
        ),
        (
            "bad_mode",
            {"object_id": 1, "source": "DoorsDriver", "mode": "toggle"},
            (False, "bad_args"),
        ),
        (
            "unhashable_mode",
            {"object_id": 1, "source": "DoorsDriver", "mode": ["open"]},
            (False, "bad_args"),
        ),
        (
            "empty_source",
            {"object_id": 1, "source": "", "mode": "read"},
            (False, "bad_args"),
        ),
        ("missing_source", {"object_id": 1, "mode": "read"}, (False, "bad_args")),
        (
            "phase_is_not_this_verb",
            {"object_id": 1, "source": "DoorsDriver", "mode": "open", "phase": 1.0},
            (False, "bad_args"),
        ),
        (
            "object_id_zero",
            {"object_id": 0, "source": "DoorsDriver", "mode": "read"},
            (False, "bad_args"),
        ),
        (
            "type_without_pos",
            {"type": "CivilianSedan", "source": "DoorsDriver", "mode": "read"},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {"object_id": 1, "source": "DoorsDriver", "mode": "read", "extra": None},
            (False, "bad_args"),
        ),
    ),
    "inventory_give": (
        (
            "without_uid",
            {"classname": "BandageDressing", "dest": "hands"},
            (False, "bad_args"),
        ),
        (
            "empty_uid",
            {"classname": "BandageDressing", "dest": "hands", "uid": ""},
            (False, "bad_args"),
        ),
        (
            "valid_with_uid",
            {"classname": "BandageDressing", "dest": "inventory", "uid": "player-1"},
            (True, None),
        ),
        (
            "extra_key",
            {"classname": "BandageDressing", "dest": "hands", "uid": "player-1", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_dest", {"classname": "BandageDressing", "uid": "player-1"}, (False, "bad_args")),
        (
            "bad_destination",
            {"classname": "BandageDressing", "dest": "ground", "uid": "player-1"},
            (False, "bad_args"),
        ),
    ),
    "object_inspect": (
        (
            "valid",
            {"type": "House", "pos": [1.0, 2.0, 3.0], "want": ["health"]},
            (True, None),
        ),
        (
            "extra_key",
            {
                "type": "House",
                "pos": [1.0, 2.0, 3.0],
                "want": ["health"],
                "extra": None,
            },
            (False, "bad_args"),
        ),
        (
            "missing_want",
            {"type": "House", "pos": [1.0, 2.0, 3.0]},
            (False, "bad_args"),
        ),
        (
            "empty_want",
            {"type": "House", "pos": [1.0, 2.0, 3.0], "want": []},
            (False, "bad_args"),
        ),
    ),
    "object_resolve": (
        (
            "valid",
            {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": 2.0},
            (True, None),
        ),
        (
            "radius_at_cap",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "radius": 50.0},
            (True, None),
        ),
        ("missing_radius", {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0]}, (False, "bad_args")),
        (
            "radius_zero",
            {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": 0.0},
            (False, "bad_args"),
        ),
        (
            "radius_over_cap",
            {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": 50.1},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {
                "type": "CivilianSedan",
                "pos": [0.0, 0.0, 0.0],
                "radius": 2.0,
                "nearest": True,
            },
            (False, "bad_args"),
        ),
    ),
    "object_set_health": (
        (
            "valid_object_id",
            {"object_id": 7, "health01": 0.0},
            (True, None),
        ),
        (
            "valid_type_pos",
            {"type": "LFQuad_Wheel_Front", "pos": [1.0, 2.0, 3.0], "health01": 0.5},
            (True, None),
        ),
        (
            "valid_slot_zone",
            {"object_id": 7, "health01": 0.0, "slot": "Wheel_1_1", "zone": "Engine"},
            (True, None),
        ),
        (
            "extra_key",
            {"object_id": 7, "health01": 0.0, "extra": None},
            (False, "bad_args"),
        ),
        (
            "health01_over",
            {"object_id": 7, "health01": 1.01},
            (False, "bad_args"),
        ),
        (
            "object_id_zero",
            {"object_id": 0, "health01": 0.0},
            (False, "bad_args"),
        ),
        (
            "empty_slot",
            {"object_id": 7, "health01": 0.0, "slot": ""},
            (False, "bad_args"),
        ),
        (
            "empty_zone",
            {"object_id": 7, "health01": 0.0, "zone": ""},
            (False, "bad_args"),
        ),
    ),
    "object_doors": (
        (
            "valid_type_pos",
            {"type": "Land_Garage_Row_Small", "pos": [1.0, 2.0, 3.0]},
            (True, None),
        ),
        ("valid_object_id", {"object_id": 1}, (True, None)),
        ("missing_target", {}, (False, "bad_args")),
        ("object_id_zero", {"object_id": 0}, (False, "bad_args")),
        ("bool_object_id", {"object_id": True}, (False, "bad_args")),
        (
            "type_without_pos",
            {"type": "Land_Garage_Row_Small"},
            (False, "bad_args"),
        ),
        (
            "phase_is_not_this_verb",
            {
                "type": "Land_Garage_Row_Small",
                "pos": [1.0, 2.0, 3.0],
                "phase": 1.0,
            },
            (False, "bad_args"),
        ),
        (
            "both_target_shapes",
            {
                "object_id": 1,
                "type": "Land_Garage_Row_Small",
                "pos": [1.0, 2.0, 3.0],
            },
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {
                "type": "Land_Garage_Row_Small",
                "pos": [1.0, 2.0, 3.0],
                "extra": None,
            },
            (False, "bad_args"),
        ),
    ),
    "object_delete": (
        ("valid", {"object_id": 1}, (True, None)),
        ("extra_key", {"object_id": 1, "extra": None}, (False, "bad_args")),
        ("missing_object_id", {}, (False, "bad_args")),
        ("bool_is_not_int", {"object_id": True}, (False, "bad_args")),
    ),
    "hands_take": (
        ("without_uid", {"object_id": 1}, (False, "bad_args")),
        ("empty_uid", {"object_id": 1, "uid": ""}, (False, "bad_args")),
        ("valid_with_uid", {"object_id": 7, "uid": "player-1"}, (True, None)),
        ("extra_key", {"object_id": 1, "uid": "player-1", "extra": None}, (False, "bad_args")),
        ("missing_object_id", {"uid": "player-1"}, (False, "bad_args")),
        ("bool_is_not_int", {"object_id": True, "uid": "player-1"}, (False, "bad_args")),
        ("zero_object_id", {"object_id": 0, "uid": "player-1"}, (False, "bad_args")),
    ),
    "inventory_move": (
        ("valid_hands", {"item_object_id": 1, "op": "to_hands", "uid": "player-1"}, (True, None)),
        ("hands_without_uid", {"item_object_id": 1, "op": "to_hands"}, (False, "bad_args")),
        ("hands_empty_uid", {"item_object_id": 1, "op": "to_hands", "uid": ""}, (False, "bad_args")),
        (
            "valid_cargo",
            {"item_object_id": 1, "op": "to_cargo", "target_object_id": 2, "authority": "server"},
            (True, None),
        ),
        ("cargo_without_target", {"item_object_id": 1, "op": "to_cargo", "uid": "player-1"}, (False, "bad_args")),
        ("cargo_target_zero", {"item_object_id": 1, "op": "to_cargo", "target_object_id": 0, "uid": "player-1"}, (False, "bad_args")),
        ("combine_without_target", {"item_object_id": 1, "op": "combine", "uid": "player-1"}, (False, "bad_args")),
        (
            "attachment_empty_slot",
            {"item_object_id": 1, "op": "to_attachment", "target_object_id": 2, "slot": "", "uid": "player-1"},
            (False, "bad_args"),
        ),
        (
            "valid_attachment",
            {"item_object_id": 1, "op": "to_attachment", "target_object_id": 2, "slot": "Vest", "uid": "player-1"},
            (True, None),
        ),
        ("unknown_op", {"item_object_id": 1, "op": "drop", "uid": "player-1"}, (False, "bad_args")),
        ("missing_op", {"item_object_id": 1, "uid": "player-1"}, (False, "bad_args")),
        ("zero_item", {"item_object_id": 0, "op": "to_hands", "uid": "player-1"}, (False, "bad_args")),
        ("bad_authority", {"item_object_id": 1, "op": "to_hands", "authority": "both", "uid": "player-1"}, (False, "bad_args")),
        ("extra_key", {"item_object_id": 1, "op": "to_hands", "uid": "player-1", "extra": None}, (False, "bad_args")),
    ),
    "hands_attach": (
        ("without_uid", {"object_id": 1, "slot": "LargeBattery"}, (False, "bad_args")),
        ("empty_uid", {"object_id": 1, "slot": "LargeBattery", "uid": ""}, (False, "bad_args")),
        (
            "valid_with_uid",
            {"object_id": 7, "slot": "LargeBattery", "uid": "player-1"},
            (True, None),
        ),
        ("extra_key", {"object_id": 1, "slot": "LargeBattery", "uid": "player-1", "extra": None}, (False, "bad_args")),
        ("missing_slot", {"object_id": 1, "uid": "player-1"}, (False, "bad_args")),
        ("empty_slot", {"object_id": 1, "slot": "", "uid": "player-1"}, (False, "bad_args")),
        ("missing_object_id", {"slot": "LargeBattery", "uid": "player-1"}, (False, "bad_args")),
        ("bool_is_not_int", {"object_id": True, "slot": "LargeBattery", "uid": "player-1"}, (False, "bad_args")),
        ("zero_object_id", {"object_id": 0, "slot": "LargeBattery", "uid": "player-1"}, (False, "bad_args")),
    ),
    "weapon_state": (
        ("empty", {}, (True, None)),
        ("valid_with_uid", {"uid": "player-1"}, (True, None)),
        ("extra_key", {"extra": None}, (False, "bad_args")),
        ("uid_not_string", {"uid": 1}, (False, "bad_args")),
    ),
    # loadout_get / loadout_apply / loadout_clear (fb99, 220f): loadout_get
    # takes uid or object_id or neither; loadout_apply / loadout_clear take a
    # non-empty uid or an object_id, never both and never neither (B2).
    # clear_first is not a wire key: the tool sends loadout_clear for it.
    "loadout_get": (
        ("empty", {}, (True, None)),
        ("valid_with_uid", {"uid": "player-1"}, (True, None)),
        ("valid_with_object_id", {"object_id": 3}, (True, None)),
        ("both_targets", {"object_id": 3, "uid": "player-1"}, (False, "bad_args")),
        ("zero_object_id", {"object_id": 0}, (False, "bad_args")),
        ("bool_is_not_int", {"object_id": True}, (False, "bad_args")),
        ("uid_not_string", {"uid": 1}, (False, "bad_args")),
        ("rows_on_a_read", {"loadout_items": []}, (False, "bad_args")),
        ("extra_key", {"extra": None}, (False, "bad_args")),
    ),
    "loadout_apply": (
        ("empty_without_target", {"loadout_items": []}, (False, "bad_args")),
        ("empty_uid_without_object_id", {"loadout_items": [], "uid": ""}, (False, "bad_args")),
        ("valid_empty_with_uid", {"loadout_items": [], "uid": "player-1"}, (True, None)),
        (
            "valid_uid_tree",
            {
                "uid": "player-1",
                "loadout_items": [
                    {"type": "AssaultBag_Black", "slot": "Back", "dest": "attachment", "parent": -1},
                    {"type": "Rag", "slot": "", "dest": "cargo", "parent": 0},
                    {"type": "M4A1", "slot": "", "dest": "hands", "parent": -1},
                    {"type": "Mag_STANAG_30Rnd", "slot": "magazine", "dest": "attachment", "parent": 2},
                ],
            },
            (True, None),
        ),
        (
            "valid_object_id",
            {"object_id": 4, "loadout_items": []},
            (True, None),
        ),
        (
            "both_targets",
            {"object_id": 4, "uid": "player-1", "loadout_items": []},
            (False, "bad_args"),
        ),
        ("missing_rows", {"uid": "player-1"}, (False, "bad_args")),
        (
            "clear_first_is_not_a_wire_key",
            {"clear_first": True, "loadout_items": [], "uid": "player-1"},
            (False, "bad_args"),
        ),
        ("rows_not_a_list", {"loadout_items": {}, "uid": "player-1"}, (False, "bad_args")),
        (
            "row_missing_parent",
            {
                "uid": "player-1",
                "loadout_items": [{"type": "Apple", "slot": "Body", "dest": "attachment"}],
            },
            (False, "bad_args"),
        ),
        (
            "row_extra_key",
            {
                "uid": "player-1",
                "loadout_items": [
                    {"type": "Apple", "slot": "Body", "dest": "attachment", "parent": -1, "qty": 1}
                ],
            },
            (False, "bad_args"),
        ),
        (
            "parent_not_earlier",
            {
                "uid": "player-1",
                "loadout_items": [{"type": "Apple", "slot": "", "dest": "cargo", "parent": 0}],
            },
            (False, "bad_args"),
        ),
        (
            "cargo_on_the_target",
            {
                "uid": "player-1",
                "loadout_items": [{"type": "Apple", "slot": "", "dest": "cargo", "parent": -1}],
            },
            (False, "bad_args"),
        ),
        (
            "attachment_without_slot",
            {
                "uid": "player-1",
                "loadout_items": [{"type": "Apple", "slot": "", "dest": "attachment", "parent": -1}],
            },
            (False, "bad_args"),
        ),
        (
            "hands_inside_an_item",
            {
                "uid": "player-1",
                "loadout_items": [
                    {"type": "AssaultBag_Black", "slot": "Back", "dest": "attachment", "parent": -1},
                    {"type": "M4A1", "slot": "", "dest": "hands", "parent": 0},
                ],
            },
            (False, "bad_args"),
        ),
        (
            "two_hands",
            {
                "uid": "player-1",
                "loadout_items": [
                    {"type": "M4A1", "slot": "", "dest": "hands", "parent": -1},
                    {"type": "M4A1", "slot": "", "dest": "hands", "parent": -1},
                ],
            },
            (False, "bad_args"),
        ),
        (
            "unknown_dest",
            {
                "uid": "player-1",
                "loadout_items": [{"type": "Apple", "slot": "", "dest": "ground", "parent": -1}],
            },
            (False, "bad_args"),
        ),
        (
            "bool_parent",
            {
                "uid": "player-1",
                "loadout_items": [{"type": "Apple", "slot": "Body", "dest": "attachment", "parent": True}],
            },
            (False, "bad_args"),
        ),
        (
            "depth_five",
            {
                "uid": "player-1",
                "loadout_items": [
                    {"type": "AssaultBag_Black", "slot": "Back", "dest": "attachment", "parent": -1}
                ]
                + [
                    {"type": "Rag", "slot": "", "dest": "cargo", "parent": index}
                    for index in range(4)
                ],
            },
            (False, "bad_args"),
        ),
        (
            "sixty_five_rows",
            {
                "uid": "player-1",
                "loadout_items": [
                    {"type": "Apple", "slot": "Body", "dest": "attachment", "parent": -1}
                ]
                * 65,
            },
            (False, "bad_args"),
        ),
        ("extra_key", {"loadout_items": [], "uid": "player-1", "extra": None}, (False, "bad_args")),
    ),
    "loadout_clear": (
        ("empty", {}, (False, "bad_args")),
        ("empty_uid", {"uid": ""}, (False, "bad_args")),
        ("valid_with_uid", {"uid": "player-1"}, (True, None)),
        ("valid_with_object_id", {"object_id": 3}, (True, None)),
        ("both_targets", {"object_id": 3, "uid": "player-1"}, (False, "bad_args")),
        ("zero_object_id", {"object_id": 0, "uid": "player-1"}, (False, "bad_args")),
        ("bool_is_not_int", {"object_id": True, "uid": "player-1"}, (False, "bad_args")),
        ("uid_not_string", {"uid": 1}, (False, "bad_args")),
        ("rows_on_a_clear", {"loadout_items": [], "uid": "player-1"}, (False, "bad_args")),
        ("extra_key", {"extra": None, "uid": "player-1"}, (False, "bad_args")),
    ),
    "notify_players": (
        (
            "valid_required_only",
            {"show_time": 1.0, "title": "Notice"},
            (True, None),
        ),
        (
            "valid_with_optional",
            {
                "show_time": 1.0,
                "title": "Notice",
                "detail": "Details",
                "icon": "set:dayz_gui image:icon_info",
                "uid": "player-1",
            },
            (True, None),
        ),
        (
            "extra_key",
            {"show_time": 1.0, "title": "Notice", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_title", {"show_time": 1.0}, (False, "bad_args")),
        (
            "show_time_not_positive",
            {"show_time": 0.0, "title": "Notice"},
            (False, "bad_args"),
        ),
    ),
    "entities_query": (
        (
            "valid_without_limit",
            {"pos": [1.0, 2.0, 3.0], "radius": 1.0},
            (True, None),
        ),
        (
            "valid_with_limit",
            {"pos": [1.0, 2.0, 3.0], "radius": 200.0, "limit": 128},
            (True, None),
        ),
        (
            "extra_key",
            {"pos": [1.0, 2.0, 3.0], "radius": 1.0, "extra": None},
            (False, "bad_args"),
        ),
        ("missing_radius", {"pos": [1.0, 2.0, 3.0]}, (False, "bad_args")),
        (
            "limit_below_range",
            {"pos": [1.0, 2.0, 3.0], "radius": 1.0, "limit": 0},
            (False, "bad_args"),
        ),
    ),
    "ui_tree": (
        ("valid_empty", {}, (True, None)),
        ("valid_with_optional", {"path": "Root", "limit": 512}, (True, None)),
        ("valid_root", {"root": "MyMenu"}, (True, None)),
        ("valid_root_and_path", {"root": "MyMenu", "path": "a/b"}, (True, None)),
        ("extra_key", {"extra": None}, (False, "bad_args")),
        ("path_wrong_type", {"path": 1}, (False, "bad_args")),
        ("limit_below_range", {"limit": 0}, (False, "bad_args")),
        ("root_empty", {"root": ""}, (False, "bad_args")),
        ("root_wrong_type", {"root": 5}, (False, "bad_args")),
        ("mode_leaks", {"mode": "direct"}, (False, "bad_args")),
        ("bubble_leaks", {"bubble": True}, (False, "bad_args")),
    ),
    "ui_set_text": (
        ("valid", {"path": "Root.Label", "text": "Ready"}, (True, None)),
        ("valid_empty_text", {"path": "Root.Label", "text": ""}, (True, None)),
        (
            "valid_root",
            {"path": "Root.Label", "text": "Ready", "root": "MyMenu"},
            (True, None),
        ),
        (
            "extra_key",
            {"path": "Root.Label", "text": "Ready", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_text", {"path": "Root.Label"}, (False, "bad_args")),
        ("empty_path", {"path": "", "text": "Ready"}, (False, "bad_args")),
        (
            "root_empty",
            {"path": "Root.Label", "text": "Ready", "root": ""},
            (False, "bad_args"),
        ),
        (
            "mode_leaks",
            {"path": "Root.Label", "text": "Ready", "mode": "direct"},
            (False, "bad_args"),
        ),
        (
            "bubble_leaks",
            {"path": "Root.Label", "text": "Ready", "bubble": True},
            (False, "bad_args"),
        ),
    ),
    "ui_click": (
        ("valid_without_button", {"path": "Root.Button"}, (True, None)),
        (
            "valid_with_button",
            {"path": "Root.Button", "button": 2},
            (True, None),
        ),
        ("valid_mode_direct", {"path": "Root.Button", "mode": "direct"}, (True, None)),
        (
            "valid_mode_complete",
            {"path": "Root.Button", "mode": "complete"},
            (True, None),
        ),
        ("valid_bubble_true", {"path": "Root.Button", "bubble": True}, (True, None)),
        ("valid_bubble_false", {"path": "Root.Button", "bubble": False}, (True, None)),
        ("valid_root", {"path": "Root.Button", "root": "MyMenu"}, (True, None)),
        (
            "valid_full",
            {
                "path": "Root.Button",
                "root": "MyMenu",
                "mode": "direct",
                "bubble": True,
                "button": 0,
            },
            (True, None),
        ),
        (
            "extra_key",
            {"path": "Root.Button", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_path", {"button": 0}, (False, "bad_args")),
        (
            "button_above_range",
            {"path": "Root.Button", "button": 3},
            (False, "bad_args"),
        ),
        (
            "mode_outside_enum",
            {"path": "Root.Button", "mode": "sideways"},
            (False, "bad_args"),
        ),
        ("mode_empty", {"path": "Root.Button", "mode": ""}, (False, "bad_args")),
        (
            "bubble_string",
            {"path": "Root.Button", "bubble": "true"},
            (False, "bad_args"),
        ),
        ("bubble_int", {"path": "Root.Button", "bubble": 1}, (False, "bad_args")),
        ("root_empty", {"path": "Root.Button", "root": ""}, (False, "bad_args")),
        ("root_wrong_type", {"path": "Root.Button", "root": 123}, (False, "bad_args")),
    ),
    "ui_reload_layout": (
        ("valid_implicit_reload", {"path": "layout.gui"}, (True, None)),
        (
            "valid_explicit_reload",
            {"mode": "reload", "path": "layout.gui", "limit": 512},
            (True, None),
        ),
        ("valid_close", {"mode": "close", "path": ""}, (True, None)),
        (
            "extra_key",
            {"mode": "reload", "path": "layout.gui", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_reload_path", {"mode": "reload"}, (False, "bad_args")),
        (
            "close_with_path",
            {"mode": "close", "path": "layout.gui"},
            (False, "bad_args"),
        ),
    ),
    "ui_focus": (
        ("valid", {"path": "Root.EditBox"}, (True, None)),
        ("valid_root", {"root": "MyMenu", "path": "Root.EditBox"}, (True, None)),
        (
            "extra_key",
            {"path": "Root.EditBox", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_path", {}, (False, "bad_args")),
        ("empty_path", {"path": ""}, (False, "bad_args")),
        ("root_empty", {"path": "Root.EditBox", "root": ""}, (False, "bad_args")),
        ("bubble_leaks", {"path": "Root.EditBox", "bubble": True}, (False, "bad_args")),
        ("mode_leaks", {"path": "Root.EditBox", "mode": "direct"}, (False, "bad_args")),
        ("button_leaks", {"path": "Root.EditBox", "button": 1}, (False, "bad_args")),
    ),
    "ui_dialog": (
        (
            "valid",
            {"kind": "acknowledge", "title": "Notice", "message": "Ready"},
            (True, None),
        ),
        (
            "extra_key",
            {
                "kind": "acknowledge",
                "title": "Notice",
                "message": "Ready",
                "extra": None,
            },
            (False, "bad_args"),
        ),
        (
            "missing_title",
            {"kind": "acknowledge", "message": "Ready"},
            (False, "bad_args"),
        ),
        (
            "bad_kind",
            {"kind": "unknown", "title": "Notice", "message": "Ready"},
            (False, "bad_args"),
        ),
    ),
    "action_use": (
        ("valid_minimal", {"action": "open"}, (True, None)),
        (
            "valid_with_optional",
            {
                "action": "open",
                "classname": "House",
                "pos": [1.0, 2.0, 3.0],
                "radius": 200.0,
            },
            (True, None),
        ),
        (
            "target_hands_rejected",
            {"action": "open", "target": "hands"},
            (False, "bad_args"),
        ),
        (
            "target_self_rejected",
            {"action": "open", "target": "self"},
            (False, "bad_args"),
        ),
        (
            "target_world_rejected",
            {"action": "open", "target": "world"},
            (False, "bad_args"),
        ),
        (
            "bad_target",
            {"action": "open", "target": "cursor"},
            (False, "bad_args"),
        ),
        (
            "target_not_string",
            {"action": "open", "target": 1},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {"action": "open", "extra": None},
            (False, "bad_args"),
        ),
        (
            "door_index_rejected",
            {"action": "open", "door_index": 0},
            (False, "bad_args"),
        ),
        ("missing_action", {"radius": 1.0}, (False, "bad_args")),
        (
            "radius_not_positive",
            {"action": "open", "radius": 0.0},
            (False, "bad_args"),
        ),
    ),
    "action_use_target": (
        (
            "valid_target_hands",
            {"action": "open", "target": "hands"},
            (True, None),
        ),
        (
            "valid_target_self",
            {"action": "open", "target": "self"},
            (True, None),
        ),
        (
            "valid_with_optional",
            {
                "action": "open",
                "target": "hands",
                "classname": "House",
                "radius": 200.0,
            },
            (True, None),
        ),
        (
            "world_rejected",
            {"action": "open", "target": "world"},
            (False, "bad_args"),
        ),
        (
            "bad_target",
            {"action": "open", "target": "cursor"},
            (False, "bad_args"),
        ),
        (
            "pos_rejected",
            {"action": "open", "target": "hands", "pos": [1.0, 2.0, 3.0]},
            (False, "bad_args"),
        ),
        ("missing_target", {"action": "open"}, (False, "bad_args")),
        ("missing_action", {"target": "hands"}, (False, "bad_args")),
        (
            "extra_key",
            {"action": "open", "target": "hands", "extra": None},
            (False, "bad_args"),
        ),
    ),
    "action_use_component": (
        (
            "valid_component_zero",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "component_index": 0,
                "cursor_pos": [1.0, 2.0, 3.0],
            },
            (True, None),
        ),
        (
            "valid_component_876",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "component_index": 876,
                "cursor_pos": [1.0, 2.0, 3.0],
                "pos": [4.0, 5.0, 6.0],
                "radius": 200.0,
            },
            (True, None),
        ),
        (
            "component_above_511",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "component_index": 512,
                "cursor_pos": [0.0, 0.0, 0.0],
            },
            (True, None),
        ),
        (
            "negative_component",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "component_index": -1,
                "cursor_pos": [0.0, 0.0, 0.0],
            },
            (False, "bad_args"),
        ),
        (
            "bool_component",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "component_index": True,
                "cursor_pos": [0.0, 0.0, 0.0],
            },
            (False, "bad_args"),
        ),
        (
            "short_cursor",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "component_index": 0,
                "cursor_pos": [1.0, 2.0],
            },
            (False, "bad_args"),
        ),
        (
            "missing_cursor",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "component_index": 0,
            },
            (False, "bad_args"),
        ),
        (
            "missing_component",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "cursor_pos": [1.0, 2.0, 3.0],
            },
            (False, "bad_args"),
        ),
    ),
    "action_use_door": (
        (
            "valid_door_zero",
            {"action": "ActionOpenDoors", "classname": "Land_House_2W03", "door_index": 0},
            (True, None),
        ),
        (
            "valid_last_door_with_optional",
            {
                "action": "ActionCloseDoors",
                "classname": "Land_House_2W03",
                "door_index": 63,
                "pos": [1.0, 2.0, 3.0],
                "radius": 200.0,
            },
            (True, None),
        ),
        (
            "missing_classname",
            {"action": "ActionOpenDoors", "door_index": 0},
            (False, "bad_args"),
        ),
        (
            "empty_classname",
            {"action": "ActionOpenDoors", "classname": "", "door_index": 0},
            (False, "bad_args"),
        ),
        (
            "missing_door_index",
            {"action": "ActionOpenDoors", "classname": "Land_House_2W03"},
            (False, "bad_args"),
        ),
        (
            "door_index_64",
            {"action": "ActionOpenDoors", "classname": "Land_House_2W03", "door_index": 64},
            (False, "bad_args"),
        ),
        (
            "door_index_negative",
            {"action": "ActionOpenDoors", "classname": "Land_House_2W03", "door_index": -1},
            (False, "bad_args"),
        ),
        (
            "door_index_bool",
            {"action": "ActionOpenDoors", "classname": "Land_House_2W03", "door_index": True},
            (False, "bad_args"),
        ),
        (
            "target_rejected",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "door_index": 0,
                "target": "world",
            },
            (False, "bad_args"),
        ),
        ("missing_action", {"classname": "Land_House_2W03", "door_index": 0}, (False, "bad_args")),
        (
            "extra_key",
            {
                "action": "ActionOpenDoors",
                "classname": "Land_House_2W03",
                "door_index": 0,
                "extra": None,
            },
            (False, "bad_args"),
        ),
    ),
    "anim_timeline": (
        ("valid_empty_sources", _anim_args(), (True, None)),
        (
            "valid_eight_sources_of_64",
            _anim_args(sources=[chr(ord("a") + i) * 64 for i in range(8)]),
            (True, None),
        ),
        ("valid_printable_edges", _anim_args(sources=[" ~"]), (True, None)),
        ("valid_hz_10", _anim_args(sample_hz=10), (True, None)),
        ("valid_hz_60", _anim_args(sample_hz=60), (True, None)),
        ("valid_read_bounds", _anim_args(mode="read", limit=64, max_samples=8192), (True, None)),
        ("valid_limit_1", _anim_args(mode="read", limit=1), (True, None)),
        ("valid_max_samples_2", _anim_args(max_samples=2), (True, None)),
        ("valid_clear", _anim_args(mode="clear"), (True, None)),
        ("dump_is_not_a_mode", _anim_args(mode="dump"), (False, "bad_args")),
        ("upper_mode", _anim_args(mode="START"), (False, "bad_args")),
        ("unhashable_mode", _anim_args(mode=["start"]), (False, "bad_args")),
        ("upper_trace_id", _anim_args(trace_id="A" * 32), (False, "bad_args")),
        ("short_trace_id", _anim_args(trace_id="a" * 31), (False, "bad_args")),
        ("negative_cursor", _anim_args(cursor=-1), (False, "bad_args")),
        ("limit_0", _anim_args(limit=0), (False, "bad_args")),
        ("limit_65", _anim_args(limit=65), (False, "bad_args")),
        ("hz_9", _anim_args(sample_hz=9), (False, "bad_args")),
        ("hz_61", _anim_args(sample_hz=61), (False, "bad_args")),
        ("hz_bool", _anim_args(sample_hz=True), (False, "bad_args")),
        ("max_samples_1", _anim_args(max_samples=1), (False, "bad_args")),
        ("max_samples_8193", _anim_args(max_samples=8193), (False, "bad_args")),
        ("nine_sources", _anim_args(sources=["a"] * 9), (False, "bad_args")),
        ("sources_string", _anim_args(sources="lid"), (False, "bad_args")),
        ("sources_null", _anim_args(sources=None), (False, "bad_args")),
        ("source_empty", _anim_args(sources=[""]), (False, "bad_args")),
        ("source_65", _anim_args(sources=["a" * 65]), (False, "bad_args")),
        ("source_int", _anim_args(sources=[1]), (False, "bad_args")),
        ("source_newline", _anim_args(sources=["lid\n"]), (False, "bad_args")),
        ("source_del", _anim_args(sources=["lid\x7f"]), (False, "bad_args")),
        ("source_non_ascii", _anim_args(sources=["tapañ"]), (False, "bad_args")),
        ("missing_sources", _anim_args(drop="sources"), (False, "bad_args")),
        ("missing_trace_id", _anim_args(drop="trace_id"), (False, "bad_args")),
        ("extra_key", _anim_args(extra=None), (False, "bad_args")),
    ),
    "exec_enforce": (
        ("valid_empty", {}, (True, None)),
        (
            "valid_with_optional",
            {"expr": "GetGame()", "main_fn": "main", "timeout_s": 0.5},
            (True, None),
        ),
        ("extra_key", {"extra": None}, (False, "bad_args")),
        ("expr_wrong_type", {"expr": 1}, (False, "bad_args")),
        ("timeout_not_positive", {"timeout_s": 0.0}, (False, "bad_args")),
    ),
    # inventory_attach had a schema but no cases; the coverage test below
    # found the gap.
    "inventory_attach": (
        (
            "valid_attachment_by_id",
            {"object_id": 1, "classname": "Headtorch_Black", "dest": "attachment", "slot": "Eyewear"},
            (True, None),
        ),
        (
            "valid_attachment_by_pos",
            {
                "type": "CivilianSedan",
                "pos": [0.0, 0.0, 0.0],
                "classname": "CarBattery",
                "dest": "attachment",
                "slot": "CarBattery",
            },
            (True, None),
        ),
        (
            "valid_cargo_by_id",
            {"object_id": 1, "classname": "Apple", "dest": "cargo"},
            (True, None),
        ),
        (
            "valid_cargo_by_pos",
            {"type": "SeaChest", "pos": [0.0, 0.0, 0.0], "classname": "Apple", "dest": "cargo"},
            (True, None),
        ),
        (
            "cargo_with_slot",
            {"object_id": 1, "classname": "Apple", "dest": "cargo", "slot": "Eyewear"},
            (False, "bad_args"),
        ),
        (
            "attachment_without_slot",
            {"object_id": 1, "classname": "Apple", "dest": "attachment"},
            (False, "bad_args"),
        ),
        (
            "id_and_pos_together",
            {
                "object_id": 1,
                "type": "SeaChest",
                "pos": [0.0, 0.0, 0.0],
                "classname": "Apple",
                "dest": "cargo",
            },
            (False, "bad_args"),
        ),
        (
            "zero_object_id",
            {"object_id": 0, "classname": "Apple", "dest": "cargo"},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {"object_id": 1, "classname": "Apple", "dest": "cargo", "extra": None},
            (False, "bad_args"),
        ),
    ),
    # The sixteen verbs that had no schema until fb-20260822-191204-6ce4.
    "query_player_state": (
        ("valid_empty", {}, (True, None)),
        ("extra_key", {"unchecked": True}, (False, "bad_args")),
    ),
    "query_all_players": (
        ("valid_empty", {}, (True, None)),
        ("extra_key", {"unchecked": True}, (False, "bad_args")),
    ),
    "vehicle_telemetry": (
        ("valid_empty", {}, (True, None)),
        ("extra_key", {"unchecked": True}, (False, "bad_args")),
        ("valid_pos", {"pos": [1.0, 2.0, 3.0]}, (True, None)),
        ("pos_nan", {"pos": [1.0, float("nan"), 3.0]}, (False, "bad_args")),
        ("pos_inf", {"pos": [1.0, float("inf"), 3.0]}, (False, "bad_args")),
        ("pos_two_components", {"pos": [1.0, 2.0]}, (False, "bad_args")),
        ("pos_four_components", {"pos": [1.0, 2.0, 3.0, 4.0]}, (False, "bad_args")),
        ("pos_non_numeric", {"pos": [1.0, "2", 3.0]}, (False, "bad_args")),
        ("pos_bool", {"pos": [True, 2.0, 3.0]}, (False, "bad_args")),
        ("pos_empty", {"pos": []}, (False, "bad_args")),
        ("pos_not_list", {"pos": "1,2,3"}, (False, "bad_args")),
    ),
    "vehicle_release": (
        ("valid_empty", {}, (True, None)),
        ("extra_key", {"unchecked": True}, (False, "bad_args")),
    ),
    "world_spawn": (
        (
            "valid",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": 0, "rotation": 0},
            (True, None),
        ),
        (
            "valid_empty_type_like_the_tool",
            {"type": "", "pos": [1.0, 2.0, 3.0], "flags": 0, "rotation": 0},
            (True, None),
        ),
        (
            "valid_flags_createphysics_trace",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": 1028, "rotation": 0},
            (True, None),
        ),
        (
            "valid_flags_surface_initai_physics",
            {"type": "ZmbM_CitizenASkinny", "pos": [1.0, 2.0, 3.0], "flags": 3108, "rotation": 0},
            (True, None),
        ),
        (
            "flags_the_tool_refuses",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": 1, "rotation": 0},
            (False, "bad_args"),
        ),
        (
            "flags_keepheight_not_allowed",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": 524288, "rotation": 0},
            (False, "bad_args"),
        ),
        (
            "missing_rotation",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": 0},
            (False, "bad_args"),
        ),
        (
            "negative_flags",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": -1, "rotation": 0},
            (False, "bad_args"),
        ),
        (
            "bool_flags",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": True, "rotation": 0},
            (False, "bad_args"),
        ),
        (
            "non_finite_pos",
            {"type": "CivilianSedan", "pos": [1.0, float("inf"), 3.0], "flags": 0, "rotation": 0},
            (False, "bad_args"),
        ),
        (
            "type_wrong_type",
            {"type": 1, "pos": [1.0, 2.0, 3.0], "flags": 0, "rotation": 0},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": 0, "rotation": 0, "extra": None},
            (False, "bad_args"),
        ),
        # lifetime_s travels with lifetime_s_set true, in (0, 3888000]
        # (fb-20260930-080543-bd28).
        (
            "valid_lifetime",
            {**_SPAWN, "lifetime_s": 600.0, "lifetime_s_set": True},
            (True, None),
        ),
        (
            "valid_lifetime_max",
            {**_SPAWN, "lifetime_s": 3888000.0, "lifetime_s_set": True},
            (True, None),
        ),
        (
            "valid_lifetime_int",
            {**_SPAWN, "lifetime_s": 600, "lifetime_s_set": True},
            (True, None),
        ),
        ("lifetime_without_flag", {**_SPAWN, "lifetime_s": 600.0}, (False, "bad_args")),
        ("flag_without_lifetime", {**_SPAWN, "lifetime_s_set": True}, (False, "bad_args")),
        (
            "lifetime_flag_false",
            {**_SPAWN, "lifetime_s": 600.0, "lifetime_s_set": False},
            (False, "bad_args"),
        ),
        (
            "lifetime_flag_int",
            {**_SPAWN, "lifetime_s": 600.0, "lifetime_s_set": 1},
            (False, "bad_args"),
        ),
        (
            "lifetime_zero",
            {**_SPAWN, "lifetime_s": 0.0, "lifetime_s_set": True},
            (False, "bad_args"),
        ),
        (
            "lifetime_negative",
            {**_SPAWN, "lifetime_s": -1.0, "lifetime_s_set": True},
            (False, "bad_args"),
        ),
        (
            "lifetime_over_max",
            {**_SPAWN, "lifetime_s": 3888000.5, "lifetime_s_set": True},
            (False, "bad_args"),
        ),
        (
            "lifetime_nan",
            {**_SPAWN, "lifetime_s": float("nan"), "lifetime_s_set": True},
            (False, "bad_args"),
        ),
        (
            "lifetime_inf",
            {**_SPAWN, "lifetime_s": float("inf"), "lifetime_s_set": True},
            (False, "bad_args"),
        ),
        (
            "lifetime_bool",
            {**_SPAWN, "lifetime_s": True, "lifetime_s_set": True},
            (False, "bad_args"),
        ),
        (
            "lifetime_huge_int",
            {**_SPAWN, "lifetime_s": 10**400, "lifetime_s_set": True},
            (False, "bad_args"),
        ),
        # scene_id travels only when the caller named a scene, 1 to 64
        # characters from A-Z a-z 0-9 _ - (165a).
        ("valid_scene_id", {**_SPAWN, "scene_id": "take_01-A"}, (True, None)),
        ("valid_scene_id_64", {**_SPAWN, "scene_id": "a" * 64}, (True, None)),
        (
            "valid_scene_id_with_lifetime",
            {**_SPAWN, "lifetime_s": 600.0, "lifetime_s_set": True, "scene_id": "a"},
            (True, None),
        ),
        ("scene_id_empty", {**_SPAWN, "scene_id": ""}, (False, "bad_args")),
        ("scene_id_65", {**_SPAWN, "scene_id": "a" * 65}, (False, "bad_args")),
        ("scene_id_space", {**_SPAWN, "scene_id": "take 1"}, (False, "bad_args")),
        ("scene_id_dot", {**_SPAWN, "scene_id": "take.1"}, (False, "bad_args")),
        ("scene_id_accent", {**_SPAWN, "scene_id": "toma\u00f1"}, (False, "bad_args")),
        ("scene_id_newline", {**_SPAWN, "scene_id": "a\n"}, (False, "bad_args")),
        ("scene_id_wrong_type", {**_SPAWN, "scene_id": 1}, (False, "bad_args")),
    ),
    "vehicle_enter": (
        ("valid", {"pos": [0.0, 0.0, 0.0]}, (True, None)),
        ("missing_pos", {}, (False, "bad_args")),
        ("pos_wrong_type", {"pos": "0,0,0"}, (False, "bad_args")),
        ("extra_key", {"pos": [0.0, 0.0, 0.0], "extra": None}, (False, "bad_args")),
    ),
    "vehicle_get_in_client": (
        ("valid", {"pos": [0.0, 0.0, 0.0]}, (True, None)),
        ("missing_pos", {}, (False, "bad_args")),
        ("pos_two_components", {"pos": [0.0, 0.0]}, (False, "bad_args")),
        ("extra_key", {"pos": [0.0, 0.0, 0.0], "extra": None}, (False, "bad_args")),
    ),
    "scene_raycast": (
        (
            "valid_tool_defaults",
            {
                "from": [0.0, 10.0, 0.0],
                "to": [0.0, -5.0, 0.0],
                "method": "rvproxy",
                "ignore": "",
                "radius": 0.05,
                "intersect": "view",
            },
            (True, None),
        ),
        (
            "valid_bullet_fire_ignore_player",
            {
                "from": [0.0, 10.0, 0.0],
                "to": [0.0, -5.0, 0.0],
                "method": "bullet",
                "ignore": "player",
                "radius": 0.0,
                "intersect": "fire",
            },
            (True, None),
        ),
        (
            "bad_method",
            {
                "from": [0.0, 10.0, 0.0],
                "to": [0.0, -5.0, 0.0],
                "method": "raycast",
                "ignore": "",
                "radius": 0.05,
                "intersect": "view",
            },
            (False, "bad_args"),
        ),
        (
            "bad_ignore",
            {
                "from": [0.0, 10.0, 0.0],
                "to": [0.0, -5.0, 0.0],
                "method": "rvproxy",
                "ignore": "self",
                "radius": 0.05,
                "intersect": "view",
            },
            (False, "bad_args"),
        ),
        (
            "negative_radius",
            {
                "from": [0.0, 10.0, 0.0],
                "to": [0.0, -5.0, 0.0],
                "method": "rvproxy",
                "ignore": "",
                "radius": -0.1,
                "intersect": "view",
            },
            (False, "bad_args"),
        ),
        (
            "bad_intersect",
            {
                "from": [0.0, 10.0, 0.0],
                "to": [0.0, -5.0, 0.0],
                "method": "rvproxy",
                "ignore": "",
                "radius": 0.05,
                "intersect": "all",
            },
            (False, "bad_args"),
        ),
        (
            "missing_to",
            {
                "from": [0.0, 10.0, 0.0],
                "method": "rvproxy",
                "ignore": "",
                "radius": 0.05,
                "intersect": "view",
            },
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {
                "from": [0.0, 10.0, 0.0],
                "to": [0.0, -5.0, 0.0],
                "method": "rvproxy",
                "ignore": "",
                "radius": 0.05,
                "intersect": "view",
                "extra": None,
            },
            (False, "bad_args"),
        ),
    ),
    "telemetry_read": (
        (
            "valid_object_at",
            {"mode": "object_at", "type": "CarScript", "pos": [0.0, 0.0, 0.0], "radius": 5.0},
            (True, None),
        ),
        (
            "valid_fixture_jsonl",
            {"mode": "fixture_jsonl", "path": "fixture.jsonl", "max_lines": 0},
            (True, None),
        ),
        (
            "object_at_empty_type",
            {"mode": "object_at", "type": "", "pos": [0.0, 0.0, 0.0], "radius": 5.0},
            (False, "bad_args"),
        ),
        (
            "object_at_zero_radius",
            {"mode": "object_at", "type": "CarScript", "pos": [0.0, 0.0, 0.0], "radius": 0.0},
            (False, "bad_args"),
        ),
        (
            "mixed_variants",
            {"mode": "object_at", "path": "fixture.jsonl", "max_lines": 1},
            (False, "bad_args"),
        ),
        (
            "unknown_mode",
            {"mode": "nearest", "type": "CarScript", "pos": [0.0, 0.0, 0.0], "radius": 5.0},
            (False, "bad_args"),
        ),
        (
            "bool_max_lines",
            {"mode": "fixture_jsonl", "path": "fixture.jsonl", "max_lines": True},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {
                "mode": "object_at",
                "type": "CarScript",
                "pos": [0.0, 0.0, 0.0],
                "radius": 5.0,
                "extra": None,
            },
            (False, "bad_args"),
        ),
    ),
    "query_get_in_condition": (
        ("valid", {"pos": [0.0, 0.0, 0.0], "component": -1}, (True, None)),
        ("missing_component", {"pos": [0.0, 0.0, 0.0]}, (False, "bad_args")),
        ("bool_component", {"pos": [0.0, 0.0, 0.0], "component": False}, (False, "bad_args")),
        (
            "extra_key",
            {"pos": [0.0, 0.0, 0.0], "component": -1, "extra": None},
            (False, "bad_args"),
        ),
    ),
    # A read with no argument: timeout_s stays on the tool side, never on the wire.
    "world_time_get": (
        ("valid_empty", {}, (True, None)),
        ("timeout_on_the_wire", {"timeout_s": 1.0}, (False, "bad_args")),
        ("set_shape", {"year": 2026, "month": 9, "day": 28, "hour": 9, "minute": 0}, (False, "bad_args")),
        ("extra_key", {"extra": None}, (False, "bad_args")),
    ),
    # 2427: window_s is required on the wire (the bridge reads an absent key as
    # 0) and finite in [1, 60].
    "server_perf": (
        ("valid_default", {"window_s": 10.0}, (True, None)),
        ("window_at_min", {"window_s": 1.0}, (True, None)),
        ("window_at_max", {"window_s": 60.0}, (True, None)),
        ("window_json_int", {"window_s": 5}, (True, None)),
        ("missing_window", {}, (False, "bad_args")),
        ("window_zero", {"window_s": 0.0}, (False, "bad_args")),
        ("window_below_min", {"window_s": 0.999}, (False, "bad_args")),
        ("window_over_max", {"window_s": 61.0}, (False, "bad_args")),
        ("window_nan", {"window_s": float("nan")}, (False, "bad_args")),
        ("window_inf", {"window_s": float("inf")}, (False, "bad_args")),
        ("window_bool", {"window_s": True}, (False, "bad_args")),
        ("window_string", {"window_s": "10"}, (False, "bad_args")),
        ("timeout_on_the_wire", {"window_s": 10.0, "timeout_s": 1.0}, (False, "bad_args")),
        ("extra_key", {"window_s": 10.0, "extra": None}, (False, "bad_args")),
    ),
    # 0131: three finite numbers for each end and snap_radius in (0, 50], all
    # required on the wire. from is the wire name; from_pos is the tool's.
    "nav_path_query": (
        (
            "valid",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": 2.0},
            (True, None),
        ),
        (
            "snap_radius_at_max",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": 50.0},
            (True, None),
        ),
        ("missing_from", {"to": [4.0, 5.0, 6.0], "snap_radius": 2.0}, (False, "bad_args")),
        ("missing_to", {"from": [1.0, 2.0, 3.0], "snap_radius": 2.0}, (False, "bad_args")),
        (
            "missing_snap_radius",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0]},
            (False, "bad_args"),
        ),
        (
            "from_two_components",
            {"from": [1.0, 2.0], "to": [4.0, 5.0, 6.0], "snap_radius": 2.0},
            (False, "bad_args"),
        ),
        (
            "from_nan_component",
            {"from": [1.0, float("nan"), 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": 2.0},
            (False, "bad_args"),
        ),
        (
            "to_inf_component",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, float("inf"), 6.0], "snap_radius": 2.0},
            (False, "bad_args"),
        ),
        (
            "to_bool_component",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, True, 6.0], "snap_radius": 2.0},
            (False, "bad_args"),
        ),
        (
            "snap_radius_zero",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": 0.0},
            (False, "bad_args"),
        ),
        (
            "snap_radius_negative",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": -1.0},
            (False, "bad_args"),
        ),
        (
            "snap_radius_over_max",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": 50.1},
            (False, "bad_args"),
        ),
        (
            "snap_radius_nan",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": float("nan")},
            (False, "bad_args"),
        ),
        (
            "tool_name_from_pos",
            {"from_pos": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": 2.0},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {"from": [1.0, 2.0, 3.0], "to": [4.0, 5.0, 6.0], "snap_radius": 2.0, "extra": None},
            (False, "bad_args"),
        ),
    ),
    # 33c5: pos is three finite numbers and radius is in [0, 100], 0 a point.
    "players_visibility": (
        ("valid_point", {"pos": [1.0, 2.0, 3.0], "radius": 0.0}, (True, None)),
        ("radius_at_max", {"pos": [1.0, 2.0, 3.0], "radius": 100.0}, (True, None)),
        ("missing_pos", {"radius": 0.0}, (False, "bad_args")),
        ("missing_radius", {"pos": [1.0, 2.0, 3.0]}, (False, "bad_args")),
        ("pos_two_components", {"pos": [1.0, 2.0], "radius": 0.0}, (False, "bad_args")),
        (
            "pos_nan_component",
            {"pos": [1.0, float("nan"), 3.0], "radius": 0.0},
            (False, "bad_args"),
        ),
        ("radius_negative", {"pos": [1.0, 2.0, 3.0], "radius": -0.1}, (False, "bad_args")),
        ("radius_over_max", {"pos": [1.0, 2.0, 3.0], "radius": 100.5}, (False, "bad_args")),
        (
            "radius_inf",
            {"pos": [1.0, 2.0, 3.0], "radius": float("inf")},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {"pos": [1.0, 2.0, 3.0], "radius": 0.0, "extra": None},
            (False, "bad_args"),
        ),
    ),
    # time_multiplier travels with time_multiplier_set true (fb-20260930-065425-8779).
    "world_time_set": (
        (
            "valid_without_multiplier",
            {"year": 2026, "month": 9, "day": 28, "hour": 9, "minute": 0},
            (True, None),
        ),
        (
            "valid_keep_multiplier",
            {
                "year": 2026,
                "month": 9,
                "day": 28,
                "hour": 9,
                "minute": 0,
                "time_multiplier": -1.0,
                "time_multiplier_set": True,
            },
            (True, None),
        ),
        (
            "valid_multiplier_64",
            {
                "year": 2026,
                "month": 9,
                "day": 28,
                "hour": 9,
                "minute": 0,
                "time_multiplier": 64.0,
                "time_multiplier_set": True,
            },
            (True, None),
        ),
        (
            "multiplier_over_64",
            {
                "year": 2026,
                "month": 9,
                "day": 28,
                "hour": 9,
                "minute": 0,
                "time_multiplier": 64.5,
                "time_multiplier_set": True,
            },
            (False, "bad_args"),
        ),
        (
            "multiplier_negative_not_minus_one",
            {
                "year": 2026,
                "month": 9,
                "day": 28,
                "hour": 9,
                "minute": 0,
                "time_multiplier": -0.5,
                "time_multiplier_set": True,
            },
            (False, "bad_args"),
        ),
        (
            "multiplier_without_flag",
            {"year": 2026, "month": 9, "day": 28, "hour": 9, "minute": 0, "time_multiplier": 4.0},
            (False, "bad_args"),
        ),
        (
            "flag_without_multiplier",
            {"year": 2026, "month": 9, "day": 28, "hour": 9, "minute": 0, "time_multiplier_set": True},
            (False, "bad_args"),
        ),
        (
            "flag_false",
            {
                "year": 2026,
                "month": 9,
                "day": 28,
                "hour": 9,
                "minute": 0,
                "time_multiplier": 4.0,
                "time_multiplier_set": False,
            },
            (False, "bad_args"),
        ),
        (
            "month_13",
            {"year": 2026, "month": 13, "day": 28, "hour": 9, "minute": 0},
            (False, "bad_args"),
        ),
        (
            "year_1969",
            {"year": 1969, "month": 9, "day": 28, "hour": 9, "minute": 0},
            (False, "bad_args"),
        ),
        (
            "minute_60",
            {"year": 2026, "month": 9, "day": 28, "hour": 9, "minute": 60},
            (False, "bad_args"),
        ),
        (
            "float_hour",
            {"year": 2026, "month": 9, "day": 28, "hour": 9.0, "minute": 0},
            (False, "bad_args"),
        ),
        (
            "missing_minute",
            {"year": 2026, "month": 9, "day": 28, "hour": 9},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {"year": 2026, "month": 9, "day": 28, "hour": 9, "minute": 0, "extra": None},
            (False, "bad_args"),
        ),
    ),
    # Each level travels with its <level>_set flag (fb-20260930-065425-8779).
    "world_weather_set": (
        (
            "valid_rain_only",
            {"rain": 0.5, "rain_set": True, "time": 0.0, "min_duration": 0.0},
            (True, None),
        ),
        (
            "valid_all_levels",
            {
                "overcast": 1.0,
                "overcast_set": True,
                "rain": 0.0,
                "rain_set": True,
                "fog": 0.25,
                "fog_set": True,
                "time": 30.0,
                "min_duration": 60.0,
            },
            (True, None),
        ),
        ("no_levels", {"time": 0.0, "min_duration": 0.0}, (False, "bad_args")),
        (
            "missing_time",
            {"rain": 0.5, "rain_set": True, "min_duration": 0.0},
            (False, "bad_args"),
        ),
        (
            "rain_above_one",
            {"rain": 1.5, "rain_set": True, "time": 0.0, "min_duration": 0.0},
            (False, "bad_args"),
        ),
        (
            "negative_min_duration",
            {"fog": 0.5, "fog_set": True, "time": 0.0, "min_duration": -1.0},
            (False, "bad_args"),
        ),
        (
            "level_wrong_type",
            {"rain": "0.5", "rain_set": True, "time": 0.0, "min_duration": 0.0},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {"rain": 0.5, "rain_set": True, "time": 0.0, "min_duration": 0.0, "extra": None},
            (False, "bad_args"),
        ),
        (
            "level_without_flag",
            {"rain": 0.5, "time": 0.0, "min_duration": 0.0},
            (False, "bad_args"),
        ),
        (
            "flag_without_level",
            {"rain": 0.5, "rain_set": True, "fog_set": True, "time": 0.0, "min_duration": 0.0},
            (False, "bad_args"),
        ),
        (
            "only_a_flag",
            {"rain_set": True, "time": 0.0, "min_duration": 0.0},
            (False, "bad_args"),
        ),
        (
            "flag_false",
            {"rain": 0.5, "rain_set": False, "time": 0.0, "min_duration": 0.0},
            (False, "bad_args"),
        ),
    ),
    "camera_set": (
        (
            "valid_orient",
            {
                "cam_mode": "orient",
                "cam_pos": [0.0, 2.0, 0.0],
                "cam_orientation": [90.0, 0.0, 0.0],
                "fov": 0.0,
                "settle_ticks": 3,
            },
            (True, None),
        ),
        (
            "valid_lookat",
            {
                "cam_mode": "lookat",
                "cam_pos": [0.0, 2.0, 0.0],
                "look_at": [5.0, 1.0, 5.0],
                "fov": 0.9,
                "settle_ticks": 3,
            },
            (True, None),
        ),
        (
            "valid_matrix",
            {"cam_mode": "matrix", "cam_matrix": [0.0] * 12, "fov": 0.0, "settle_ticks": 0},
            (True, None),
        ),
        (
            "valid_free_look_at",
            {
                "cam_mode": "free",
                "cam_pos": [0.0, 2.0, 0.0],
                "look_at": [5.0, 1.0, 5.0],
                "fov": 0.0,
                "settle_ticks": 3,
            },
            (True, None),
        ),
        (
            "valid_free_orientation",
            {
                "cam_mode": "free",
                "cam_pos": [0.0, 2.0, 0.0],
                "cam_orientation": [90.0, 0.0, 0.0],
                "fov": 0.0,
                "settle_ticks": 3,
            },
            (True, None),
        ),
        (
            "alias_never_on_the_wire",
            {
                "cam_mode": "look_at",
                "cam_pos": [0.0, 2.0, 0.0],
                "look_at": [5.0, 1.0, 5.0],
                "fov": 0.0,
                "settle_ticks": 3,
            },
            (False, "bad_args"),
        ),
        (
            "free_with_both_targets",
            {
                "cam_mode": "free",
                "cam_pos": [0.0, 2.0, 0.0],
                "look_at": [5.0, 1.0, 5.0],
                "cam_orientation": [90.0, 0.0, 0.0],
                "fov": 0.0,
                "settle_ticks": 3,
            },
            (False, "bad_args"),
        ),
        (
            "orient_missing_orientation",
            {"cam_mode": "orient", "cam_pos": [0.0, 2.0, 0.0], "fov": 0.0, "settle_ticks": 3},
            (False, "bad_args"),
        ),
        (
            "matrix_11_numbers",
            {"cam_mode": "matrix", "cam_matrix": [0.0] * 11, "fov": 0.0, "settle_ticks": 0},
            (False, "bad_args"),
        ),
        (
            "negative_fov",
            {
                "cam_mode": "orient",
                "cam_pos": [0.0, 2.0, 0.0],
                "cam_orientation": [90.0, 0.0, 0.0],
                "fov": -0.1,
                "settle_ticks": 3,
            },
            (False, "bad_args"),
        ),
        (
            "bool_settle_ticks",
            {
                "cam_mode": "orient",
                "cam_pos": [0.0, 2.0, 0.0],
                "cam_orientation": [90.0, 0.0, 0.0],
                "fov": 0.0,
                "settle_ticks": True,
            },
            (False, "bad_args"),
        ),
        # 702f: focus travels on camera_set_focus only, so camera_set never
        # carries it to a bridge that would drop it.
        ("focus_is_camera_set_focus_only", _focus_args(), (False, "bad_args")),
        (
            "extra_key",
            {
                "cam_mode": "orient",
                "cam_pos": [0.0, 2.0, 0.0],
                "cam_orientation": [90.0, 0.0, 0.0],
                "fov": 0.0,
                "settle_ticks": 3,
                "extra": None,
            },
            (False, "bad_args"),
        ),
    ),
    "camera_get": (
        ("valid_empty", {}, (True, None)),
        ("valid_get", {"cam_mode": "get"}, (True, None)),
        ("empty_cam_mode", {"cam_mode": ""}, (False, "bad_args")),
        ("extra_key", {"cam_mode": "get", "extra": None}, (False, "bad_args")),
    ),
    # 702f: camera_set's five shapes, each with both focus arguments.
    "camera_set_focus": (
        ("valid_orient", _focus_args(), (True, None)),
        ("valid_lookat", _focus_args("lookat", "look_at"), (True, None)),
        ("valid_matrix", _focus_args("matrix"), (True, None)),
        ("valid_free_look_at", _focus_args("free", "look_at"), (True, None)),
        ("valid_free_orientation", _focus_args("free", "cam_orientation"), (True, None)),
        ("valid_blur_zero", _focus_args(focus_blur=0.0), (True, None)),
        ("valid_integer_distance", _focus_args(focus_distance=50), (True, None)),
        ("missing_blur", _focus_args(drop="focus_blur"), (False, "bad_args")),
        ("missing_distance", _focus_args(drop="focus_distance"), (False, "bad_args")),
        (
            "camera_set_shape_without_focus",
            {
                "cam_mode": "orient",
                "cam_pos": [0.0, 2.0, 0.0],
                "cam_orientation": [90.0, 0.0, 0.0],
                "fov": 0.0,
                "settle_ticks": 3,
            },
            (False, "bad_args"),
        ),
        ("blur_null", _focus_args(focus_blur=None), (False, "bad_args")),
        ("distance_zero", _focus_args(focus_distance=0.0), (False, "bad_args")),
        ("distance_negative", _focus_args(focus_distance=-2.0), (False, "bad_args")),
        ("distance_inf", _focus_args(focus_distance=float("inf")), (False, "bad_args")),
        ("distance_nan", _focus_args(focus_distance=float("nan")), (False, "bad_args")),
        ("distance_bool", _focus_args(focus_distance=True), (False, "bad_args")),
        ("distance_string", _focus_args(focus_distance="2"), (False, "bad_args")),
        ("blur_negative", _focus_args(focus_blur=-0.1), (False, "bad_args")),
        ("blur_inf", _focus_args(focus_blur=float("inf")), (False, "bad_args")),
        ("blur_bool", _focus_args(focus_blur=False), (False, "bad_args")),
        # The tool's bounds (camera_path.focus_distance and focus_blur): at most
        # 1e9, and above 0 still as the bridge's 32-bit float.
        ("valid_distance_at_the_bound", _focus_args(focus_distance=1e9), (True, None)),
        ("valid_distance_least_float", _focus_args(focus_distance=1e-45), (True, None)),
        ("valid_blur_least_float", _focus_args(focus_blur=1e-45), (True, None)),
        ("distance_1e100", _focus_args(focus_distance=1e100), (False, "bad_args")),
        ("distance_1e-46", _focus_args(focus_distance=1e-46), (False, "bad_args")),
        ("blur_1e100", _focus_args(focus_blur=1e100), (False, "bad_args")),
        ("blur_1e-46", _focus_args(focus_blur=1e-46), (False, "bad_args")),
        ("alias_never_on_the_wire", _focus_args("look_at", "look_at"), (False, "bad_args")),
        ("extra_key", _focus_args(extra=None), (False, "bad_args")),
    ),
    # d026: the bounds mirror MCPCameraPath through camera_path.py.
    "camera_path_play": (
        ("valid_play_linear", _path_args(), (True, None)),
        ("valid_play_smooth", _path_args(easing="smooth"), (True, None)),
        ("valid_play_two_keys", _path_args(keys=[_path_key(0.0), _path_key(0.5)]), (True, None)),
        (
            "valid_play_64_keys",
            _path_args(keys=[_path_key(float(index)) for index in range(64)]),
            (True, None),
        ),
        ("valid_play_last_t_600", _path_args(keys=[_path_key(0.0), _path_key(600.0)]), (True, None)),
        (
            "valid_play_look_at_with_fov",
            _path_args(keys=[_look_key(0.0), _look_key(2.0, fov=0.5)]),
            (True, None),
        ),
        ("valid_play_integer_t", _path_args(keys=[_path_key(0), _path_key(2)]), (True, None)),
        ("valid_play_no_path_id", _path_args(path_id=""), (True, None)),
        ("valid_play_path_id_64", _path_args(path_id="a-Z_9" * 12 + "abcd"), (True, None)),
        ("valid_state", {"mode": "state", "path_id": ""}, (True, None)),
        ("valid_state_named", {"mode": "state", "path_id": "dolly_1"}, (True, None)),
        ("valid_stop", {"mode": "stop", "path_id": ""}, (True, None)),
        ("one_key", _path_args(keys=[_path_key(0.0)]), (False, "bad_args")),
        ("no_keys", _path_args(keys=[]), (False, "bad_args")),
        (
            "65_keys",
            _path_args(keys=[_path_key(float(index)) for index in range(65)]),
            (False, "bad_args"),
        ),
        ("keys_not_a_list", _path_args(keys={"t": 0.0}), (False, "bad_args")),
        ("keys_null", _path_args(keys=None), (False, "bad_args")),
        ("key_not_an_object", _path_args(keys=[_path_key(0.0), [1.0, 2.0]]), (False, "bad_args")),
        ("first_t_not_zero", _path_args(keys=[_path_key(0.5), _path_key(1.0)]), (False, "bad_args")),
        ("t_repeated", _path_args(keys=[_path_key(0.0), _path_key(2.0), _path_key(2.0)]), (False, "bad_args")),
        ("t_decreasing", _path_args(keys=[_path_key(0.0), _path_key(3.0), _path_key(2.0)]), (False, "bad_args")),
        ("last_t_above_600", _path_args(keys=[_path_key(0.0), _path_key(600.5)]), (False, "bad_args")),
        ("t_missing", _path_args(keys=[_path_key(0.0), {"cam_pos": [1.0, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]}]), (False, "bad_args")),
        ("t_bool", _path_args(keys=[_path_key(0.0), _path_key(True)]), (False, "bad_args")),
        ("t_nan", _path_args(keys=[_path_key(0.0), _path_key(float("nan"))]), (False, "bad_args")),
        ("t_string", _path_args(keys=[_path_key(0.0), _path_key("1")]), (False, "bad_args")),
        ("cam_pos_two_numbers", _path_args(keys=[_path_key(0.0), _path_key(1.0, cam_pos=[1.0, 2.0])]), (False, "bad_args")),
        ("cam_pos_bool", _path_args(keys=[_path_key(0.0), _path_key(1.0, cam_pos=[1.0, True, 0.0])]), (False, "bad_args")),
        ("cam_pos_inf", _path_args(keys=[_path_key(0.0), _path_key(1.0, cam_pos=[1.0, float("inf"), 0.0])]), (False, "bad_args")),
        ("cam_pos_missing", _path_args(keys=[_path_key(0.0), {"t": 1.0, "cam_orientation": [0.0, 0.0, 0.0]}]), (False, "bad_args")),
        ("orientation_two_numbers", _path_args(keys=[_path_key(0.0), _path_key(1.0, cam_orientation=[0.0, 0.0])]), (False, "bad_args")),
        ("both_aims", _path_args(keys=[_path_key(0.0), _path_key(1.0, look_at=[0.0, 0.0, 9.0])]), (False, "bad_args")),
        ("no_aim", _path_args(keys=[_path_key(0.0), {"t": 1.0, "cam_pos": [1.0, 2.0, 0.0]}]), (False, "bad_args")),
        (
            "mixed_aims",
            _path_args(keys=[_path_key(0.0), {"t": 1.0, "cam_pos": [1.0, 2.0, 0.0], "look_at": [0.0, 0.0, 9.0]}]),
            (False, "bad_args"),
        ),
        ("fov_on_some_keys", _path_args(keys=[_look_key(0.0), _look_key(1.0), {"t": 2.0, "cam_pos": [2.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0]}]), (False, "bad_args")),
        ("fov_zero", _path_args(keys=[_look_key(0.0), _look_key(1.0, fov=0.0)]), (False, "bad_args")),
        ("fov_negative", _path_args(keys=[_look_key(0.0), _look_key(1.0, fov=-0.5)]), (False, "bad_args")),
        ("fov_bool", _path_args(keys=[_look_key(0.0), _look_key(1.0, fov=True)]), (False, "bad_args")),
        # The bridge reads each number as a 32-bit float and bounds it to 1e9.
        ("t_collapses_as_float32", _path_args(keys=[_path_key(0.0), _path_key(1.0), _path_key(1.00000001)]), (False, "bad_args")),
        ("valid_t_distinct_as_float32", _path_args(keys=[_path_key(0.0), _path_key(1.0), _path_key(1.0000002)]), (True, None)),
        ("t_huge_int", _path_args(keys=[_path_key(0.0), _path_key(10**400)]), (False, "bad_args")),
        ("valid_cam_pos_at_the_bound", _path_args(keys=[_path_key(0.0, cam_pos=[1e9, -1e9, 0.0]), _path_key(1.0)]), (True, None)),
        ("cam_pos_past_the_bound", _path_args(keys=[_path_key(0.0, cam_pos=[1.5e9, 0.0, 0.0]), _path_key(1.0)]), (False, "bad_args")),
        ("cam_pos_1e100", _path_args(keys=[_path_key(0.0), _path_key(1.0, cam_pos=[1e100, 2.0, 0.0])]), (False, "bad_args")),
        ("orientation_1e100", _path_args(keys=[_path_key(0.0), _path_key(1.0, cam_orientation=[1e100, 0.0, 0.0])]), (False, "bad_args")),
        ("look_at_1e100", _path_args(keys=[_look_key(0.0), _look_key(1.0, look_at=[0.0, 1e100, 9.0])]), (False, "bad_args")),
        ("fov_1e100", _path_args(keys=[_look_key(0.0), _look_key(1.0, fov=1e100)]), (False, "bad_args")),
        ("fov_zero_as_float32", _path_args(keys=[_look_key(0.0), _look_key(1.0, fov=1e-46)]), (False, "bad_args")),
        ("key_unknown_field", _path_args(keys=[_path_key(0.0), _path_key(1.0, focus_distance=2.0)]), (False, "bad_args")),
        ("bad_easing", _path_args(easing="ease_in"), (False, "bad_args")),
        ("easing_not_a_string", _path_args(easing=1), (False, "bad_args")),
        ("bad_mode", _path_args(mode="pause"), (False, "bad_args")),
        ("upper_mode", _path_args(mode="PLAY"), (False, "bad_args")),
        ("unhashable_mode", _path_args(mode=["play"]), (False, "bad_args")),
        ("path_id_65", _path_args(path_id="a" * 65), (False, "bad_args")),
        ("path_id_space", _path_args(path_id="dolly 1"), (False, "bad_args")),
        ("path_id_non_ascii", _path_args(path_id="grúa"), (False, "bad_args")),
        ("path_id_newline", _path_args(path_id="dolly\n"), (False, "bad_args")),
        ("path_id_int", _path_args(path_id=1), (False, "bad_args")),
        ("play_missing_path_id", _path_args(drop="path_id"), (False, "bad_args")),
        ("play_missing_easing", _path_args(drop="easing"), (False, "bad_args")),
        ("play_missing_keys", _path_args(drop="keys"), (False, "bad_args")),
        ("state_with_keys", {"mode": "state", "path_id": "", "keys": [_path_key(0.0), _path_key(1.0)]}, (False, "bad_args")),
        ("state_with_easing", {"mode": "state", "path_id": "", "easing": "linear"}, (False, "bad_args")),
        ("stop_bad_path_id", {"mode": "stop", "path_id": "a/b"}, (False, "bad_args")),
        ("stop_missing_path_id", {"mode": "stop"}, (False, "bad_args")),
        ("missing_mode", {"path_id": ""}, (False, "bad_args")),
        ("extra_key", _path_args(extra=None), (False, "bad_args")),
    ),
    # f427: visible is a JSON bool and the only key.
    "hud_set": (
        ("valid_hidden", {"visible": False}, (True, None)),
        ("valid_shown", {"visible": True}, (True, None)),
        ("int_one", {"visible": 1}, (False, "bad_args")),
        ("int_zero", {"visible": 0}, (False, "bad_args")),
        ("string_true", {"visible": "true"}, (False, "bad_args")),
        ("null", {"visible": None}, (False, "bad_args")),
        ("missing_visible", {}, (False, "bad_args")),
        ("extra_key", {"visible": True, "extra": None}, (False, "bad_args")),
    ),
    "engine_set": (
        ("valid_start", {"mode": "start"}, (True, None)),
        ("valid_stop", {"mode": "stop"}, (True, None)),
        ("bad_mode", {"mode": "idle"}, (False, "bad_args")),
        ("unhashable_mode", {"mode": ["start"]}, (False, "bad_args")),
        ("missing_mode", {}, (False, "bad_args")),
        ("extra_key", {"mode": "start", "extra": None}, (False, "bad_args")),
    ),
    "vehicle_control": (
        (
            "valid_neutral",
            {"throttle": 0.0, "steer": 0.0, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 0.0},
            (True, None),
        ),
        (
            "valid_limits",
            {"throttle": 1.0, "steer": -1.0, "brake": 1.0, "handbrake": 1.0, "hold_ttl_s": 30.0},
            (True, None),
        ),
        (
            "throttle_above_one",
            {"throttle": 1.5, "steer": 0.0, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 0.0},
            (False, "bad_args"),
        ),
        (
            "steer_below_minus_one",
            {"throttle": 0.0, "steer": -1.5, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 0.0},
            (False, "bad_args"),
        ),
        (
            "handbrake_half",
            {"throttle": 0.0, "steer": 0.0, "brake": 0.0, "handbrake": 0.5, "hold_ttl_s": 0.0},
            (False, "bad_args"),
        ),
        (
            "handbrake_bool",
            {"throttle": 0.0, "steer": 0.0, "brake": 0.0, "handbrake": True, "hold_ttl_s": 0.0},
            (False, "bad_args"),
        ),
        (
            "ttl_over_max",
            {"throttle": 0.0, "steer": 0.0, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 30.5},
            (False, "bad_args"),
        ),
        (
            "huge_int_throttle",
            {"throttle": 10**400, "steer": 0.0, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 0.0},
            (False, "bad_args"),
        ),
        (
            "missing_brake",
            {"throttle": 0.0, "steer": 0.0, "handbrake": 0.0, "hold_ttl_s": 0.0},
            (False, "bad_args"),
        ),
        (
            "extra_key",
            {
                "throttle": 0.0,
                "steer": 0.0,
                "brake": 0.0,
                "handbrake": 0.0,
                "hold_ttl_s": 0.0,
                "extra": None,
            },
            (False, "bad_args"),
        ),
    ),
    "weapon_raise": (
        ("valid_raise", {"raised": True, "hold_ttl_s": 3.0}, (True, None)),
        ("valid_release_still_needs_ttl", {"raised": False, "hold_ttl_s": 0.1}, (True, None)),
        ("valid_max", {"raised": True, "hold_ttl_s": 30.0}, (True, None)),
        ("ttl_zero", {"raised": True, "hold_ttl_s": 0.0}, (False, "bad_args")),
        ("ttl_negative", {"raised": True, "hold_ttl_s": -1.0}, (False, "bad_args")),
        ("ttl_over_max", {"raised": True, "hold_ttl_s": 30.5}, (False, "bad_args")),
        ("ttl_nan", {"raised": True, "hold_ttl_s": float("nan")}, (False, "bad_args")),
        ("ttl_inf", {"raised": True, "hold_ttl_s": float("inf")}, (False, "bad_args")),
        ("raised_int", {"raised": 1, "hold_ttl_s": 3.0}, (False, "bad_args")),
        ("missing_raised", {"hold_ttl_s": 3.0}, (False, "bad_args")),
        ("missing_ttl", {"raised": True}, (False, "bad_args")),
        (
            "extra_key",
            {"raised": True, "hold_ttl_s": 3.0, "extra": None},
            (False, "bad_args"),
        ),
    ),
    "weapon_aim": (
        ("valid_zero", {"dx": 0.0, "dy": 0.0}, (True, None)),
        ("valid_cap", {"dx": 3.141593, "dy": -3.141593}, (True, None)),
        ("dx_over_cap", {"dx": 3.141594, "dy": 0.0}, (False, "bad_args")),
        ("dy_under_cap", {"dx": 0.0, "dy": -3.141594}, (False, "bad_args")),
        ("dx_bool", {"dx": True, "dy": 0.0}, (False, "bad_args")),
        ("dx_nan", {"dx": float("nan"), "dy": 0.0}, (False, "bad_args")),
        ("missing_dy", {"dx": 0.0}, (False, "bad_args")),
        ("extra_key", {"dx": 0.0, "dy": 0.0, "extra": None}, (False, "bad_args")),
    ),
    "weapon_fire": (
        ("valid_empty", {}, (True, None)),
        ("extra_key", {"extra": None}, (False, "bad_args")),
    ),
    "action_hold_cancel": (
        ("valid", {"hold_id": "abc"}, (True, None)),
        ("empty_id", {"hold_id": ""}, (False, "bad_args")),
        ("missing_id", {}, (False, "bad_args")),
        ("extra", {"hold_id": "abc", "extra": 1}, (False, "bad_args")),
    ),
    "action_hold": (
        (
            "world",
            {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": 1.0},
            (True, None),
        ),
        (
            "hands",
            {
                "action": "ActionDeployObject",
                "selector": "hands",
                "target": "hands",
                "classname": "FenceKit",
                "hold_timeout_s": 1.0,
            },
            (True, None),
        ),
        (
            "self",
            {
                "action": "ActionBandageSelf",
                "selector": "self",
                "target": "self",
                "hold_timeout_s": 1.0,
            },
            (True, None),
        ),
        (
            "hands_without_target",
            {
                "action": "ActionDeployObject",
                "selector": "hands",
                "hold_timeout_s": 1.0,
            },
            (False, "bad_args"),
        ),
        (
            "missing_selector",
            {"action": "ActionDeployObject", "hold_timeout_s": 1.0},
            (False, "bad_args"),
        ),
        (
            "bool_timeout",
            {
                "action": "ActionDeployObject",
                "selector": "world",
                "hold_timeout_s": True,
            },
            (False, "bad_args"),
        ),
    ),
    "weapon_sights": (
        ("valid_ironsights", {"mode": "ironsights"}, (True, None)),
        ("valid_optics", {"mode": "optics"}, (True, None)),
        ("valid_none", {"mode": "none"}, (True, None)),
        ("bad_mode", {"mode": "red_dot"}, (False, "bad_args")),
        ("unhashable_mode", {"mode": ["ironsights"]}, (False, "bad_args")),
        ("missing_mode", {}, (False, "bad_args")),
        ("extra_key", {"mode": "none", "extra": None}, (False, "bad_args")),
    ),
    "action_cursor": (
        ("valid_empty", {}, (True, None)),
        ("extra_key", {"target": "Car"}, (False, "bad_args")),
    ),
    "player_look_at": (
        ("valid", {"pos": [0.0, 1.0, 10.0], "timeout_s": 0.1}, (True, None)),
        ("missing_timeout", {"pos": [0.0, 1.0, 10.0]}, (False, "bad_args")),
        ("timeout_zero", {"pos": [0.0, 1.0, 10.0], "timeout_s": 0.0}, (False, "bad_args")),
        ("timeout_nan", {"pos": [0.0, 1.0, 10.0], "timeout_s": float("nan")}, (False, "bad_args")),
        ("timeout_bool", {"pos": [0.0, 1.0, 10.0], "timeout_s": True}, (False, "bad_args")),
        ("bad_pos", {"pos": [0.0, 1.0], "timeout_s": 0.1}, (False, "bad_args")),
        (
            "extra_key",
            {"pos": [0.0, 1.0, 10.0], "timeout_s": 0.1, "extra": None},
            (False, "bad_args"),
        ),
    ),
    "player_look_at_release": (
        ("valid", {"command_id": 1}, (True, None)),
        ("zero", {"command_id": 0}, (False, "bad_args")),
        ("bool", {"command_id": True}, (False, "bad_args")),
        ("missing", {}, (False, "bad_args")),
        ("extra_key", {"command_id": 1, "extra": None}, (False, "bad_args")),
    ),
    # 854c: first or third, nothing else, before anything reaches the client.
    "player_view_set": (
        ("valid_first", {"view": "first"}, (True, None)),
        ("valid_third", {"view": "third"}, (True, None)),
        ("missing_view", {}, (False, "bad_args")),
        ("bad_view", {"view": "3rd"}, (False, "bad_args")),
        ("empty_view", {"view": ""}, (False, "bad_args")),
        ("cased_view", {"view": "Third"}, (False, "bad_args")),
        ("number_view", {"view": 3}, (False, "bad_args")),
        ("bool_view", {"view": True}, (False, "bad_args")),
        ("null_view", {"view": None}, (False, "bad_args")),
        ("unhashable_view", {"view": ["third"]}, (False, "bad_args")),
        ("extra_key", {"view": "third", "extra": None}, (False, "bad_args")),
    ),
    # d026, internal: the loopback's release of the path a play started.
    "camera_path_release": (
        ("valid", {"command_id": 1}, (True, None)),
        ("zero", {"command_id": 0}, (False, "bad_args")),
        ("bool", {"command_id": True}, (False, "bad_args")),
        ("missing", {}, (False, "bad_args")),
        ("extra_key", {"command_id": 1, "extra": None}, (False, "bad_args")),
    ),
    "action_cursor_ids": (
        (
            "valid",
            {"net_low": [1], "net_high": [2], "run_token": "run-a"},
            (True, None),
        ),
        (
            "length_mismatch",
            {"net_low": [1], "net_high": [2, 3], "run_token": "run-a"},
            (False, "bad_args"),
        ),
        ("missing_token", {"net_low": [1], "net_high": [2]}, (False, "bad_args")),
        (
            "extra_key",
            {"net_low": [1], "net_high": [2], "run_token": "run-a", "extra": None},
            (False, "bad_args"),
        ),
    ),
    # 7f5d: one group of the items the world_spawn_batch tool sends, at most
    # SPAWN_BATCH_PER_TICK_MAX per command. per_tick stays in the tool.
    "world_spawn_batch": (
        ("valid", {"items": [_BATCH_ITEM]}, (True, None)),
        (
            "valid_scene_ground_ypr",
            {
                "items": [
                    _BATCH_ITEM,
                    {**_BATCH_ITEM, "ground": True},
                    {**_BATCH_ITEM, "ypr": [90.0, 0.0, 0.0], "ypr_set": True},
                ],
                "scene_id": "a",
            },
            (True, None),
        ),
        ("valid_50_items", {"items": [_BATCH_ITEM] * 50}, (True, None)),
        (
            "valid_rotation_extremes",
            {
                "items": [
                    {**_BATCH_ITEM, "rotation": -2147483648},
                    {**_BATCH_ITEM, "rotation": 2147483647},
                ]
            },
            (True, None),
        ),
        ("no_items", {"items": []}, (False, "bad_args")),
        ("51_items", {"items": [_BATCH_ITEM] * 51}, (False, "bad_args")),
        ("200_items", {"items": [_BATCH_ITEM] * 200}, (False, "bad_args")),
        ("items_not_a_list", {"items": _BATCH_ITEM}, (False, "bad_args")),
        ("items_missing", {"scene_id": "a"}, (False, "bad_args")),
        ("per_tick_is_not_a_wire_key", {"items": [_BATCH_ITEM], "per_tick": 10}, (False, "bad_args")),
        ("scene_id_bad", {"items": [_BATCH_ITEM], "scene_id": "a b"}, (False, "bad_args")),
        ("scene_id_empty", {"items": [_BATCH_ITEM], "scene_id": ""}, (False, "bad_args")),
        ("scene_id_not_a_string", {"items": [_BATCH_ITEM], "scene_id": 1}, (False, "bad_args")),
        ("item_empty_type", {"items": [{**_BATCH_ITEM, "type": ""}]}, (False, "bad_args")),
        ("item_type_not_a_string", {"items": [{**_BATCH_ITEM, "type": 5}]}, (False, "bad_args")),
        (
            "item_missing_rotation",
            {"items": [{"type": "Barrel_Green", "pos": [1.0, 0.0, 3.0], "flags": 0}]},
            (False, "bad_args"),
        ),
        ("item_null_y", {"items": [{**_BATCH_ITEM, "pos": [1.0, None, 3.0]}]}, (False, "bad_args")),
        ("item_bool_in_pos", {"items": [{**_BATCH_ITEM, "pos": [1.0, True, 3.0]}]}, (False, "bad_args")),
        ("item_pos_not_a_list", {"items": [{**_BATCH_ITEM, "pos": "1,2,3"}]}, (False, "bad_args")),
        ("item_bad_flags", {"items": [{**_BATCH_ITEM, "flags": 524288}]}, (False, "bad_args")),
        ("item_bool_flags", {"items": [{**_BATCH_ITEM, "flags": True}]}, (False, "bad_args")),
        ("item_bool_rotation", {"items": [{**_BATCH_ITEM, "rotation": True}]}, (False, "bad_args")),
        ("item_float_rotation", {"items": [{**_BATCH_ITEM, "rotation": 1.0}]}, (False, "bad_args")),
        ("item_rotation_above_int32", {"items": [{**_BATCH_ITEM, "rotation": 2147483648}]}, (False, "bad_args")),
        ("item_rotation_below_int32", {"items": [{**_BATCH_ITEM, "rotation": -2147483649}]}, (False, "bad_args")),
        ("item_ground_false", {"items": [{**_BATCH_ITEM, "ground": False}]}, (False, "bad_args")),
        ("item_ground_one", {"items": [{**_BATCH_ITEM, "ground": 1}]}, (False, "bad_args")),
        ("item_ypr_without_flag", {"items": [{**_BATCH_ITEM, "ypr": [0.0, 0.0, 0.0]}]}, (False, "bad_args")),
        (
            "item_ypr_flag_one",
            {"items": [{**_BATCH_ITEM, "ypr": [0.0, 0.0, 0.0], "ypr_set": 1}]},
            (False, "bad_args"),
        ),
        (
            "item_bool_in_ypr",
            {"items": [{**_BATCH_ITEM, "ypr": [0.0, True, 0.0], "ypr_set": True}]},
            (False, "bad_args"),
        ),
        (
            "item_ypr_with_rotation",
            {"items": [{**_BATCH_ITEM, "rotation": 1, "ypr": [0.0, 0.0, 0.0], "ypr_set": True}]},
            (False, "bad_args"),
        ),
        (
            "item_ypr_two_components",
            {"items": [{**_BATCH_ITEM, "ypr": [0.0, 0.0], "ypr_set": True}]},
            (False, "bad_args"),
        ),
        ("item_extra_key", {"items": [{**_BATCH_ITEM, "lifetime_s": 60.0}]}, (False, "bad_args")),
        ("item_not_an_object", {"items": ["Barrel_Green"]}, (False, "bad_args")),
        ("item_a_list", {"items": [["Barrel_Green", [1.0, 2.0, 3.0], 0, 0]]}, (False, "bad_args")),
        ("extra_key", {"items": [_BATCH_ITEM], "extra": None}, (False, "bad_args")),
    ),
    # 165a / 5a21: the scene a read or a clear names.
    "scene_entities": (
        ("valid", {"scene_id": "a"}, (True, None)),
        ("valid_64", {"scene_id": "Z9_-" * 16}, (True, None)),
        ("missing", {}, (False, "bad_args")),
        ("empty", {"scene_id": ""}, (False, "bad_args")),
        ("too_long", {"scene_id": "a" * 65}, (False, "bad_args")),
        ("bad_character", {"scene_id": "a/b"}, (False, "bad_args")),
        ("wrong_type", {"scene_id": ["a"]}, (False, "bad_args")),
        ("extra_key", {"scene_id": "a", "extra": None}, (False, "bad_args")),
    ),
    "scene_clear": (
        ("valid", {"scene_id": "a"}, (True, None)),
        ("missing", {}, (False, "bad_args")),
        ("empty", {"scene_id": ""}, (False, "bad_args")),
        ("too_long", {"scene_id": "a" * 65}, (False, "bad_args")),
        ("bad_character", {"scene_id": "a*"}, (False, "bad_args")),
        ("extra_key", {"scene_id": "a", "timeout_s": 5.0}, (False, "bad_args")),
    ),
}


class ValidateCommandArgsTableTest(unittest.TestCase):
    def _assert_command_cases(self, command: str) -> None:
        for label, args, expected in _COMMAND_CASES[command]:
            with self.subTest(command=command, case=label):
                self.assertEqual(loopback.validate_command_args(command, args), expected)


def _install_command_test(command: str) -> None:
    def test_command(self: ValidateCommandArgsTableTest) -> None:
        self._assert_command_cases(command)

    test_command.__name__ = f"test_{command}"
    setattr(ValidateCommandArgsTableTest, test_command.__name__, test_command)


for _command in _COMMAND_CASES:
    _install_command_test(_command)


class ValidateCommandArgsFallbackTest(unittest.TestCase):
    def test_every_whitelisted_verb_has_a_schema(self) -> None:
        # No verb reaches the ingress unchecked any more: a verb added to a
        # command set without a schema fails here, not in production.
        verbs = (
            loopback.SERVER_COMMANDS
            | loopback.CLIENT_COMMANDS
            | loopback.EXEC_COMMANDS
        )
        self.assertTrue(verbs, "no commands declared; test is vacuous")
        self.assertEqual(sorted(verbs - set(loopback._COMMAND_ARG_SCHEMAS)), [])

    def test_every_schema_has_table_cases(self) -> None:
        self.assertEqual(
            sorted(set(loopback._COMMAND_ARG_SCHEMAS) - set(_COMMAND_CASES)), []
        )

    def test_unknown_command_is_rejected(self) -> None:
        self.assertEqual(
            loopback.validate_command_args("not_a_command", {"unchecked": True}),
            (False, "bad_args"),
        )


if __name__ == "__main__":
    unittest.main()
