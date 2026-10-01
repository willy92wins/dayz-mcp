"""Anti-regression: every whitelisted verb must survive validate_command_args.

The previous D18 test iterated the schemaless set, which by construction could
not detect a verb that was missing from it. This test walks the UNION of all
three command sets (SERVER_COMMANDS | CLIENT_COMMANDS | EXEC_COMMANDS) and, for
each verb, asserts that validate_command_args accepts a minimal valid payload.
Every verb has a schema since fb-20260822-191204-6ce4, so a new verb added to
any of the three sets without one falls through to the final
`return False, "bad_args"` and this test fails naming the verb.
"""
from __future__ import annotations

import unittest

from dayz_mcp import loopback


def _minimal_args(cmd: str) -> dict:
    """Return a minimal payload that validate_command_args must accept for `cmd`.

    This is the contract: a verb is "wired" iff its minimal valid payload passes
    validation. A verb that is whitelisted but has no schema will reject even
    its minimal payload, which is exactly the bug this test guards against.
    """
    # Verbs whose minimal payload is empty.
    if cmd in {
        "query_player_state",
        "query_all_players",
        "vehicle_telemetry",
        "vehicle_release",
        "camera_get",
        "weapon_state",
        "weapon_fire",
        "world_time_get",
    }:
        return {}

    # The rest of the verbs that were schemaless before 6ce4.
    if cmd == "world_spawn":
        return {"type": "Item", "pos": [0.0, 0.0, 0.0], "flags": 0, "rotation": 0}
    if cmd in {"vehicle_enter", "vehicle_get_in_client"}:
        return {"pos": [0.0, 0.0, 0.0]}
    if cmd == "scene_raycast":
        return {
            "from": [0.0, 1.0, 0.0],
            "to": [0.0, 0.0, 0.0],
            "method": "rvproxy",
            "ignore": "",
            "radius": 0.05,
            "intersect": "view",
        }
    if cmd == "telemetry_read":
        return {"mode": "object_at", "type": "CarScript", "pos": [0.0, 0.0, 0.0], "radius": 1.0}
    if cmd == "query_get_in_condition":
        return {"pos": [0.0, 0.0, 0.0], "component": -1}
    if cmd == "world_time_set":
        return {"year": 2026, "month": 1, "day": 1, "hour": 0, "minute": 0}
    if cmd == "world_weather_set":
        # Each level travels with its presence flag (fb-20260930-065425-8779).
        return {"rain": 0.0, "rain_set": True, "time": 0.0, "min_duration": 0.0}
    if cmd == "camera_set":
        return {
            "cam_mode": "orient",
            "cam_pos": [0.0, 0.0, 0.0],
            "cam_orientation": [0.0, 0.0, 0.0],
            "fov": 0.0,
            "settle_ticks": 0,
        }
    if cmd == "engine_set":
        return {"mode": "start"}
    if cmd == "vehicle_control":
        return {"throttle": 0.0, "steer": 0.0, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 0.0}

    if cmd == "restore_gameplay":
        return {}
    if cmd == "player_respawn":
        return {}
    if cmd == "key_press":
        return {"dik": 1}
    if cmd == "input_describe":
        return {"name": "UAMoveForward"}
    if cmd == "input_trigger":
        return {"trigger_kind": "key", "trigger_edge": "click", "trigger_entry": "game", "dik": 1}
    if cmd == "vehicle_trace":
        return {
            "mode": "start",
            "trace_id": "a" * 32,
            "cursor": 0,
            "limit": 1,
            "sample_hz": 20,
            "max_samples": 2,
        }
    if cmd == "anim_timeline":
        return {
            "mode": "start",
            "trace_id": "a" * 32,
            "cursor": 0,
            "limit": 1,
            "sample_hz": 10,
            "max_samples": 2,
            "sources": [],
        }
    if cmd == "player_trace":
        return {
            "mode": "start",
            "trace_id": "a" * 32,
            "cursor": 0,
            "limit": 1,
            "sample_hz": 20,
            "max_samples": 2,
        }
    if cmd == "player_move":
        return {"mode": "release"}
    if cmd == "vehicle_prepare_fixture":
        return {"mode": "object_at", "type": "CarScript", "pos": [0.0, 0.0, 0.0], "radius": 1.0}
    if cmd == "surface_query":
        return {"x": 0.0, "z": 0.0}
    if cmd == "player_teleport":
        return {"pos": [0.0, 0.0, 0.0]}
    if cmd == "player_heal":
        return {"full": True}
    if cmd == "player_godmode":
        return {"godmode": False}
    if cmd == "infected_drive":
        return {
            "type": "Infected",
            "pos": [0.0, 0.0, 0.0],
            "heading": 0.0,
            "heading_set": True,
            "speed": 1.0,
            "speed_set": True,
        }
    if cmd == "object_anim":
        return {"type": "CarScript", "pos": [0.0, 0.0, 0.0], "source": "idle"}
    if cmd == "vehicle_door":
        return {"object_id": 1, "source": "DoorsDriver", "mode": "read"}
    if cmd == "inventory_give":
        return {"classname": "Item", "dest": "hands"}
    if cmd == "inventory_attach":
        return {"object_id": 1, "classname": "Item", "dest": "cargo"}
    if cmd == "object_inspect":
        return {"type": "CarScript", "pos": [0.0, 0.0, 0.0], "want": ["health"]}
    if cmd == "object_doors":
        return {"type": "Land_Garage_Row_Small", "pos": [0.0, 0.0, 0.0]}
    if cmd == "object_delete":
        return {"object_id": 1}
    if cmd == "hands_take":
        return {"object_id": 1}
    if cmd == "weapon_raise":
        return {"raised": True, "hold_ttl_s": 3.0}
    if cmd == "weapon_aim":
        return {"dx": 0.0, "dy": 0.0}
    if cmd == "weapon_sights":
        return {"mode": "ironsights"}
    if cmd == "notify_players":
        return {"show_time": 1.0, "title": "t", "detail": "", "icon": ""}
    if cmd == "entities_query":
        return {"pos": [0.0, 0.0, 0.0], "radius": 1.0}
    if cmd == "ui_tree":
        return {}
    if cmd == "ui_set_text":
        return {"path": "p", "text": "t"}
    if cmd == "ui_click":
        return {"path": "p"}
    if cmd == "ui_focus":
        # Same shape as ui_click: a widget NAME. ui_focus rejects an empty
        # path and any extra key.
        return {"path": "p"}
    if cmd == "ui_reload_layout":
        return {"mode": "close"}
    if cmd == "ui_dialog":
        # Delegates to ui_dialog.validate_command_args, which runs parse_request:
        # kind must be one of ui_dialog.KINDS and title is required. It shares no
        # keys with ui_reload_layout despite both being ui_* verbs.
        return {"kind": "acknowledge", "title": "t", "message": "m"}
    if cmd == "action_use":
        return {"action": "use"}
    if cmd == "action_use_target":
        return {"action": "use", "target": "hands"}
    if cmd == "action_use_door":
        return {"action": "use", "classname": "Land_House_2W03", "door_index": 0}
    if cmd == "exec_enforce":
        # Shape-only gate; allowlist/audit happen in _enqueue_exec_enforce.
        return {"expr": "allowed"}

    # Unknown verb: this should not happen for a whitelisted verb. If it does,
    # return {} so the test fails loudly with a clear message below.
    return {}


class CommandValidationCoverageTest(unittest.TestCase):
    def test_every_whitelisted_verb_survives_validation(self) -> None:
        all_commands = (
            loopback.SERVER_COMMANDS
            | loopback.CLIENT_COMMANDS
            | loopback.EXEC_COMMANDS
        )
        self.assertTrue(all_commands, "no commands declared; test is vacuous")

        failures: list[str] = []
        for cmd in sorted(all_commands):
            args = _minimal_args(cmd)
            ok, err = loopback.validate_command_args(cmd, args)
            if not ok:
                failures.append(f"{cmd!r} (args={args!r}) -> {err!r}")

        self.assertEqual(
            failures,
            [],
            "whitelisted verbs rejected by validate_command_args (missing "
            "schema): " + "; ".join(failures),
        )

    def test_infected_drive_still_passes_validation(self) -> None:
        # Real-world case this test must protect: infected_drive was added to
        # SERVER_COMMANDS by another change and must keep passing validation.
        self.assertIn("infected_drive", loopback.SERVER_COMMANDS)
        ok, err = loopback.validate_command_args(
            "infected_drive",
            {
                "type": "Infected",
                "pos": [0.0, 0.0, 0.0],
                "heading": 0.0,
                "heading_set": True,
                "speed": 1.0,
                "speed_set": True,
            },
        )
        self.assertTrue(ok, f"infected_drive rejected: {err!r}")
        ok_release, err_release = loopback.validate_command_args(
            "infected_drive",
            {"type": "Infected", "pos": [0.0, 0.0, 0.0], "mode": "release"},
        )
        self.assertTrue(ok_release, f"infected_drive (release) rejected: {err_release!r}")

    def test_schemed_verbs_reject_unexpected_keys(self) -> None:
        # F-12: verbs with a schema must fail closed on extra keys. Since 6ce4
        # every verb has one; tests/test_validate_command_args_table.py covers
        # the extra key for each of them.
        ok_delete, err_delete = loopback.validate_command_args(
            "object_delete", {"object_id": 1, "unexpected": "x"}
        )
        self.assertFalse(ok_delete)
        self.assertEqual(err_delete, "bad_args")
        ok_notify, err_notify = loopback.validate_command_args(
            "notify_players",
            {"show_time": 1.0, "title": "t", "unexpected": "x"},
        )
        self.assertFalse(ok_notify)
        self.assertEqual(err_notify, "bad_args")
        ok_clean, err_clean = loopback.validate_command_args(
            "object_delete", {"object_id": 1}
        )
        self.assertTrue(ok_clean, err_clean)
        ok_notify_clean, err_notify_clean = loopback.validate_command_args(
            "notify_players", {"show_time": 1.0, "title": "t"}
        )
        self.assertTrue(ok_notify_clean, err_notify_clean)


if __name__ == "__main__":
    unittest.main()
