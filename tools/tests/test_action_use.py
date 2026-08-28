from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import command_requires_lease


COMMAND = "action_use"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"


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


class ActionUseIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_happy_path_client_mutating(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertTrue(command_requires_lease(COMMAND))
        status, body = self.state.enqueue_command(COMMAND, {"action": "ActionOpenDoors"})
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "client")


class ActionUseHeldItemContractTest(unittest.TestCase):
    def test_dispatch_passes_real_item_in_hands(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchActionUse(")
        self.assertIn("GetItemInHands()", body)
        self.assertIn("ItemBase heldItem", body)
        self.assertIn("heldItem = player.GetItemInHands()", body)
        self.assertIn("action.Can(player, actionTarget, heldItem)", body)
        self.assertIn(
            "amc.PerformActionStart(action, actionTarget, heldItem, NULL)", body
        )
        self.assertNotIn("action.Can(player, actionTarget, null)", body)
        self.assertNotIn(
            "amc.PerformActionStart(action, actionTarget, null, NULL)", body
        )


class ActionUseAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_app_tool_registered_and_forwards(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "started": True}),
        ) as call:
            await app.call_tool(
                COMMAND, {"action": "ActionOpenDoors", "timeout_s": 1.0}
            )

        call.assert_awaited_once()
        forwarded = call.await_args.args
        self.assertEqual(forwarded[0], COMMAND)
        self.assertEqual(forwarded[1]["action"], "ActionOpenDoors")
        self.assertEqual(forwarded[2], "client")


if __name__ == "__main__":
    unittest.main()
