"""Characterize command argument validation before its table refactor.

The corpus pins accepted payload variants and representative failures so the
declarative implementation cannot silently widen or narrow the wire contract.
"""
from __future__ import annotations

import unittest

from dayz_mcp import loopback


_Result = tuple[bool, str | None]
_Case = tuple[str, dict[str, object], _Result]


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
    "infected_drive": (
        (
            "valid_drive",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "heading": -360.0,
                "speed": 5.0,
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
                "speed": 1.0,
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
            },
            (False, "bad_args"),
        ),
        (
            "speed_above_range",
            {
                "type": "ZmbM_CitizenASkinny_Base",
                "pos": [1.0, 2.0, 3.0],
                "heading": 0.0,
                "speed": 5.01,
            },
            (False, "bad_args"),
        ),
    ),
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
            },
            (True, None),
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
            },
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
    "object_delete": (
        ("valid", {"object_id": 1}, (True, None)),
        ("extra_key", {"object_id": 1, "extra": None}, (False, "bad_args")),
        ("missing_object_id", {}, (False, "bad_args")),
        ("bool_is_not_int", {"object_id": True}, (False, "bad_args")),
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
        ("extra_key", {"extra": None}, (False, "bad_args")),
        ("path_wrong_type", {"path": 1}, (False, "bad_args")),
        ("limit_below_range", {"limit": 0}, (False, "bad_args")),
    ),
    "ui_set_text": (
        ("valid", {"path": "Root.Label", "text": "Ready"}, (True, None)),
        (
            "extra_key",
            {"path": "Root.Label", "text": "Ready", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_text", {"path": "Root.Label"}, (False, "bad_args")),
        ("empty_path", {"path": "", "text": "Ready"}, (False, "bad_args")),
    ),
    "ui_click": (
        ("valid_without_button", {"path": "Root.Button"}, (True, None)),
        (
            "valid_with_button",
            {"path": "Root.Button", "button": 2},
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
        (
            "extra_key",
            {"path": "Root.EditBox", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_path", {}, (False, "bad_args")),
        ("empty_path", {"path": ""}, (False, "bad_args")),
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
            "extra_key",
            {"action": "open", "extra": None},
            (False, "bad_args"),
        ),
        ("missing_action", {"radius": 1.0}, (False, "bad_args")),
        (
            "radius_not_positive",
            {"action": "open", "radius": 0.0},
            (False, "bad_args"),
        ),
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
}


_SCHEMALESS_COMMANDS = (
    "query_player_state",
    "query_all_players",
    "world_spawn",
    "vehicle_enter",
    "vehicle_drive",
    "scene_raycast",
    "telemetry_read",
    "query_get_in_condition",
    "world_time_set",
    "world_weather_set",
    "camera_set",
    "camera_get",
    "drive_probe_client",
    "vehicle_get_in_client",
    "engine_set",
    "vehicle_control",
    "vehicle_telemetry",
    "vehicle_release",
)


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
    def test_schemaless_commands_preserve_unchecked_payloads(self) -> None:
        for command in _SCHEMALESS_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(
                    loopback.validate_command_args(command, {"unchecked": object()}),
                    (True, None),
                )

    def test_unknown_command_is_rejected(self) -> None:
        self.assertEqual(
            loopback.validate_command_args("not_a_command", {"unchecked": True}),
            (False, "bad_args"),
        )


if __name__ == "__main__":
    unittest.main()
