"""B2 min (fb-20261028-131048-091a): empty/missing uid is refused before the bridge.

Each player-mutating tool, called with an empty or missing uid, must get a
named refusal (bad_args naming uid) and nothing must reach the bridge; the
daemon (loopback.validate_command_args / enqueue) refuses the same wire
shapes so a client that bypasses the tool layer cannot queue them either.
Calls with a uid behave as before. player_kill already had this contract
(test_player_kill.py) and is included here for completeness.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server

UID = "76561198000000001"

# tool name -> (arguments without uid, arguments with uid, bridge command)
TOOL_CASES = {
    "player_teleport": ({"pos": [1.0, 2.0, 3.0], "skip_clearance_check": True}, {"pos": [1.0, 2.0, 3.0], "skip_clearance_check": True, "uid": UID}, "player_teleport"),
    "player_heal": ({}, {"uid": UID}, "player_heal"),
    "player_godmode": ({"on": True}, {"on": True, "uid": UID}, "player_godmode"),
    "player_kill": ({}, {"uid": UID}, "player_kill"),
    "inventory_give": ({"classname": "Apple"}, {"classname": "Apple", "uid": UID}, "inventory_give"),
    "hands_take": ({"object_id": 1}, {"object_id": 1, "uid": UID}, "hands_take"),
    "hands_attach": ({"object_id": 1, "slot": "Body"}, {"object_id": 1, "slot": "Body", "uid": UID}, "hands_attach"),
    "inventory_move": ({"item_object_id": 1, "op": "to_hands"}, {"item_object_id": 1, "op": "to_hands", "uid": UID}, "inventory_move"),
    "loadout_apply": ({"loadout": {}}, {"loadout": {}, "uid": UID}, "loadout_apply"),
}


def _error_text(exc: BaseException) -> str:
    text = str(exc)
    for attr in ("exceptions", "error"):
        nested = getattr(exc, attr, None)
        if nested:
            text += " " + str(nested)
    return text


class EmptyUidToolRefusalTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_empty_and_missing_uid_refused_before_bridge(self) -> None:
        for tool, (without_uid, _with_uid, _cmd) in TOOL_CASES.items():
            for arguments in (without_uid, {**without_uid, "uid": ""}):
                with self.subTest(tool=tool, arguments=arguments):
                    with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
                        with self.assertRaises(Exception) as caught:
                            await self.app.call_tool(tool, arguments)
                    call.assert_not_awaited()
                    self.assertIn("uid", _error_text(caught.exception))

    async def test_with_uid_reaches_bridge(self) -> None:
        for tool, (_without_uid, with_uid, cmd) in TOOL_CASES.items():
            with self.subTest(tool=tool):
                with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
                    await self.app.call_tool(tool, with_uid)
                call.assert_awaited()
                self.assertEqual(call.await_args.args[0], cmd)
                self.assertEqual(call.await_args.args[1].get("uid"), UID)


class EmptyUidDaemonRefusalTest(unittest.TestCase):
    # Wire shapes the tools send; the daemon must refuse the uid-less ones
    # with bad_args before queueing (player_kill's shape is the precedent).
    WIRE_CASES = {
        "player_teleport": ({"pos": [1.0, 2.0, 3.0]}, {"pos": [1.0, 2.0, 3.0], "uid": UID}),
        "player_heal": ({"full": True}, {"full": True, "uid": UID}),
        "player_godmode": ({"godmode": True}, {"godmode": True, "uid": UID}),
        "player_kill": ({}, {"uid": UID}),
        "inventory_give": ({"classname": "Apple", "dest": "hands"}, {"classname": "Apple", "dest": "hands", "uid": UID}),
        "hands_take": ({"object_id": 1}, {"object_id": 1, "uid": UID}),
        "hands_attach": ({"object_id": 1, "slot": "Body"}, {"object_id": 1, "slot": "Body", "uid": UID}),
        "inventory_move": ({"item_object_id": 1, "op": "to_hands"}, {"item_object_id": 1, "op": "to_hands", "uid": UID}),
        "loadout_apply": ({"loadout_items": []}, {"loadout_items": [], "uid": UID}),
        "loadout_clear": ({}, {"uid": UID}),
    }

    def test_validate_command_args(self) -> None:
        for cmd, (without_uid, with_uid) in self.WIRE_CASES.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(loopback.validate_command_args(cmd, without_uid), (False, "bad_args"))
                self.assertEqual(loopback.validate_command_args(cmd, {**without_uid, "uid": ""}), (False, "bad_args"))
                ok, error = loopback.validate_command_args(cmd, with_uid)
                self.assertTrue(ok, f"{cmd} with uid refused: {error}")

    def test_object_id_target_and_server_world_move_keep_their_shape(self) -> None:
        # A survivor dummy by object_id names no connected player.
        ok, _ = loopback.validate_command_args("loadout_apply", {"loadout_items": [], "object_id": 3})
        self.assertTrue(ok)
        # authority=server world-to-world move names no player.
        ok, _ = loopback.validate_command_args(
            "inventory_move",
            {"item_object_id": 1, "op": "to_cargo", "target_object_id": 2, "authority": "server"},
        )
        self.assertTrue(ok)

    def test_reads_and_broadcast_are_not_refused(self) -> None:
        ok, _ = loopback.validate_command_args("weapon_state", {})
        self.assertTrue(ok)
        ok, _ = loopback.validate_command_args("loadout_get", {})
        self.assertTrue(ok)
        ok, _ = loopback.validate_command_args("notify_players", {"show_time": 1.0, "title": "hi"})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
