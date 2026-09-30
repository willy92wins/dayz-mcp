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


# A valid world_spawn payload without the optional lifetime_s pair.
_SPAWN: dict[str, object] = {
    "type": "CivilianSedan",
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
        ("valid_without_uid", {"pos": [1.0, 2.0, 3.0]}, (True, None)),
        (
            "valid_with_uid",
            {"pos": [1.0, 2.0, 3.0], "uid": "player-1"},
            (True, None),
        ),
        (
            "extra_key",
            {"pos": [1.0, 2.0, 3.0], "extra": None},
            (False, "bad_args"),
        ),
        ("missing_pos", {"uid": "player-1"}, (False, "bad_args")),
        (
            "uid_wrong_type",
            {"pos": [1.0, 2.0, 3.0], "uid": 1},
            (False, "bad_args"),
        ),
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
            "valid_without_uid",
            {"classname": "BandageDressing", "dest": "hands"},
            (True, None),
        ),
        (
            "valid_with_uid",
            {"classname": "BandageDressing", "dest": "inventory", "uid": "player-1"},
            (True, None),
        ),
        (
            "extra_key",
            {"classname": "BandageDressing", "dest": "hands", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_dest", {"classname": "BandageDressing"}, (False, "bad_args")),
        (
            "bad_destination",
            {"classname": "BandageDressing", "dest": "ground"},
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
        ("valid", {"object_id": 1}, (True, None)),
        ("valid_with_uid", {"object_id": 7, "uid": "player-1"}, (True, None)),
        ("extra_key", {"object_id": 1, "extra": None}, (False, "bad_args")),
        ("missing_object_id", {}, (False, "bad_args")),
        ("bool_is_not_int", {"object_id": True}, (False, "bad_args")),
        ("zero_object_id", {"object_id": 0}, (False, "bad_args")),
    ),
    "weapon_state": (
        ("empty", {}, (True, None)),
        ("valid_with_uid", {"uid": "player-1"}, (True, None)),
        ("extra_key", {"extra": None}, (False, "bad_args")),
        ("uid_not_string", {"uid": 1}, (False, "bad_args")),
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
    "weapon_sights": (
        ("valid_ironsights", {"mode": "ironsights"}, (True, None)),
        ("valid_optics", {"mode": "optics"}, (True, None)),
        ("valid_none", {"mode": "none"}, (True, None)),
        ("bad_mode", {"mode": "red_dot"}, (False, "bad_args")),
        ("unhashable_mode", {"mode": ["ironsights"]}, (False, "bad_args")),
        ("missing_mode", {}, (False, "bad_args")),
        ("extra_key", {"mode": "none", "extra": None}, (False, "bad_args")),
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
