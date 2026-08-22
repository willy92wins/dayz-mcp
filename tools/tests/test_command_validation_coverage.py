"""Anti-regression: every whitelisted verb must survive validate_command_args.

The previous D18 test iterated _SCHEMALESS_COMMANDS, which by construction could
not detect a verb that was missing from that set. This test walks the UNION of
all three command sets (SERVER_COMMANDS | CLIENT_COMMANDS | EXEC_COMMANDS) and,
for each verb, asserts that validate_command_args accepts a minimal valid
payload. If a new verb is added to any of the three sets without a matching
`if cmd == ...` branch (or an entry in _SCHEMALESS_COMMANDS), it falls through
to the final `return False, "bad_args"` and this test fails naming the verb.
"""
from __future__ import annotations

import unittest

from dayz_mcp import loopback


def _minimal_args(cmd: str) -> dict:
    """Return a minimal payload that validate_command_args must accept for `cmd`.

    This is the contract: a verb is "wired" iff its minimal valid payload passes
    validation. A verb that is whitelisted but has no validation branch and is
    not in _SCHEMALESS_COMMANDS will reject even its minimal payload, which is
    exactly the bug this test guards against.
    """
    # Schema-less verbs: empty payload is the minimal valid one.
    if cmd in loopback._SCHEMALESS_COMMANDS:
        return {}

    # Verbs with their own `if cmd == ...` branch in validate_command_args.
    if cmd == "restore_gameplay":
        return {}
    if cmd == "vehicle_trace":
        return {
            "mode": "start",
            "trace_id": "a" * 32,
            "cursor": 0,
            "limit": 1,
            "sample_hz": 20,
            "max_samples": 2,
        }
    if cmd == "vehicle_prepare_fixture":
        return {"mode": "object_at", "type": "CarScript", "pos": [0.0, 0.0, 0.0], "radius": 1.0}
    if cmd == "surface_query":
        return {"x": 0.0, "z": 0.0}
    if cmd == "player_teleport":
        return {"pos": [0.0, 0.0, 0.0]}
    if cmd == "infected_drive":
        return {"type": "Infected", "pos": [0.0, 0.0, 0.0], "heading": 0.0, "speed": 1.0}
    if cmd == "object_anim":
        return {"type": "CarScript", "pos": [0.0, 0.0, 0.0], "source": "idle"}
    if cmd == "inventory_give":
        return {"classname": "Item", "dest": "hands"}
    if cmd == "object_inspect":
        return {"type": "CarScript", "pos": [0.0, 0.0, 0.0], "want": ["health"]}
    if cmd == "object_delete":
        return {"object_id": 1}
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
        # Same shape as ui_click: a widget NAME. NOT schemaless -- ui_focus
        # rejects an empty path and any extra key, so an entry in
        # _SCHEMALESS_COMMANDS would silently drop that validation.
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
            "validation branch or _SCHEMALESS_COMMANDS entry): " + "; ".join(failures),
        )

    def test_infected_drive_still_passes_validation(self) -> None:
        # Real-world case this test must protect: infected_drive was added to
        # SERVER_COMMANDS by another change and must keep passing validation.
        self.assertIn("infected_drive", loopback.SERVER_COMMANDS)
        ok, err = loopback.validate_command_args(
            "infected_drive",
            {"type": "Infected", "pos": [0.0, 0.0, 0.0], "heading": 0.0, "speed": 1.0},
        )
        self.assertTrue(ok, f"infected_drive rejected: {err!r}")
        ok_release, err_release = loopback.validate_command_args(
            "infected_drive",
            {"type": "Infected", "pos": [0.0, 0.0, 0.0], "mode": "release"},
        )
        self.assertTrue(ok_release, f"infected_drive (release) rejected: {err_release!r}")

    def test_schemed_verbs_reject_unexpected_keys(self) -> None:
        # F-12: verbs that already have a schema must fail closed on extra keys.
        # _SCHEMALESS_COMMANDS are intentionally not covered here.
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
