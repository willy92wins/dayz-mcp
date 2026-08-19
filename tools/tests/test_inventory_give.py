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
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

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
    def test_handler_resolves_player_and_honors_dest(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchInventoryGive(")
        required = [
            'command.args.dest != "hands"',
            'command.args.dest != "inventory"',
            "ResolvePlayer(",
            'command.args.dest == "hands"',
            "GetItemInHands()",
            'result.error = "hands_occupied"',
            "GetHumanInventory()",
            "CreateInHands(",
            "CreateInInventory(",
            'result.error = "create_failed"',
            "result.classname = command.args.classname",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, body)

        forbidden = [
            "PluginDeveloper",
            "FindInventoryLocationType",
            "SpawnEntityInInventory",
            "SpawnEntityInPlayerInventory",
            "handsOccupied",
            "result.deferred = true",
            "DropEntity",
            "CallLater",
        ]
        for token in forbidden:
            with self.subTest(forbidden=token):
                self.assertNotIn(token, body)

    def test_hands_occupied_fails_closed_before_create(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchInventoryGive(")

        hands_block = _method_body(body, 'if (command.args.dest == "hands")')
        self.assertIn("GetItemInHands()", hands_block)
        self.assertIn('result.error = "hands_occupied"', hands_block)
        self.assertIn("CreateInHands(", hands_block)
        self.assertNotIn("CreateInInventory(", hands_block)
        self.assertLess(
            hands_block.index("GetItemInHands()"),
            hands_block.index("CreateInHands("),
        )
        self.assertLess(
            hands_block.index('result.error = "hands_occupied"'),
            hands_block.index("CreateInHands("),
        )

        self.assertLess(body.index("CreateInHands("), body.index("CreateInInventory("))
        inv_block = body[body.index("CreateInInventory(") :]
        self.assertNotIn("CreateInHands(", inv_block)

        null_block = body[body.index("if (!spawned)") :]
        self.assertIn('result.error = "create_failed"', null_block)
        self.assertNotIn("hands_occupied", null_block)


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
