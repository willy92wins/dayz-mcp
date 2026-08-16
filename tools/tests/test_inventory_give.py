from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease


COMMAND = "inventory_give"
VALID_ARGS = {"classname": "AKM", "dest": "hands"}
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


class InventoryGiveIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")

    def test_happy_path_server_mutating(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))

        for dest in ("hands", "inventory"):
            with self.subTest(dest=dest):
                status, body = self.state.enqueue_command(
                    COMMAND, {"classname": "AKM", "dest": dest}
                )
                self.assertEqual(status, 200)
                self.assertEqual(body["peer"], "server")

    def test_rejects_invalid_dest_and_empty_classname(self) -> None:
        invalid = [
            {},
            {"classname": "AKM"},
            {"classname": "", "dest": "hands"},
            {"classname": "AKM", "dest": "ground"},
            {"classname": "AKM", "dest": "HANDS"},
            {"classname": "AKM", "dest": "hands", "extra": 1},
        ]
        for args in invalid:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})


class InventoryGiveEnforceContractTest(unittest.TestCase):
    def test_handler_uses_plugin_developer_and_no_player_error(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchInventoryGive(")
        required = [
            'command.args.dest != "hands"',
            'command.args.dest != "inventory"',
            "GetFirstHuman()",
            'result.error = "no_players"',
            "PluginDeveloper.GetInstance()",
            "SpawnEntityInInventory(",
            "FindInventoryLocationType.HANDS",
            "FindInventoryLocationType.ANY",
            'result.error = "spawn_failed"',
            "result.classname = command.args.classname",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_hands_occupied_is_deferred_success_not_spawn_failed(self) -> None:
        """P1: vanilla returns null after Drop+CallLater when HANDS are full.

        Must stay red if the handler again maps that null to spawn_failed only.
        """
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchInventoryGive(")

        # Precondition observed before the PluginDeveloper call.
        self.assertIn("GetItemInHands()", body)
        self.assertLess(body.index("GetItemInHands()"), body.index("SpawnEntityInInventory("))
        self.assertIn("handsOccupied", body)

        # Explicit deferred success contract (scalar; not pruned).
        self.assertIn("result.deferred = true", body)
        self.assertIn("result.ok = true", body)

        # The deferred branch must sit inside the !spawned path, before spawn_failed.
        null_block_start = body.index("if (!spawned)")
        null_block = body[null_block_start:]
        self.assertIn("if (handsOccupied)", null_block)
        self.assertLess(
            null_block.index("if (handsOccupied)"),
            null_block.index('result.error = "spawn_failed"'),
        )
        deferred_ok = null_block[
            null_block.index("if (handsOccupied)") : null_block.index(
                'result.error = "spawn_failed"'
            )
        ]
        self.assertIn("result.deferred = true", deferred_ok)
        self.assertIn("result.ok = true", deferred_ok)
        self.assertNotIn('result.error = "spawn_failed"', deferred_ok)

        # Citation must name the player path that actually runs, not :572 non-player.
        self.assertIn("SpawnEntityInPlayerInventory", body)
        self.assertNotIn("plugindeveloper.c:572", body)

        messages = (
            addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
        ).read_text(encoding="utf-8")
        self.assertIn("bool deferred;", messages)


class InventoryGiveAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_app_tool_registered_and_forwards(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "classname": "AKM", "found": True}),
        ) as call:
            await app.call_tool(
                COMMAND, {"classname": "AKM", "dest": "hands", "timeout_s": 1.0}
            )

        call.assert_awaited_once_with(COMMAND, VALID_ARGS, "server", 1.0)


if __name__ == "__main__":
    unittest.main()
