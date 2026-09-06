from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import command_requires_lease
from tests._addon_paths import addon_root


COMMAND = "player_respawn"
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


class PlayerRespawnIngressContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_command_is_client_only_mutating_and_accepts_only_empty_args(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertTrue(command_requires_lease(COMMAND))

        status, body = self.state.enqueue_command(COMMAND, {})
        self.assertEqual((status, body["peer"]), (200, "client"))
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"random": True}),
            (400, {"error": "bad_args"}),
        )
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {}, peer="server"),
            (400, {"error": "bad_peer"}),
        )


class PlayerRespawnFastMCPContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_tool_forwards_exact_empty_client_command(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "requested": True}),
        ) as call:
            await app.call_tool(COMMAND, {"timeout_s": 1.0})

        call.assert_awaited_once_with(COMMAND, {}, "client", 1.0)


class PlayerRespawnEnforceSourceContractTest(unittest.TestCase):
    def test_result_names_request_without_claiming_completion(self) -> None:
        source = MESSAGES_PATH.read_text(encoding="utf-8")
        result_body = _method_body(source, "class MCPResult")
        self.assertIn("bool requested;", result_body)
        self.assertNotIn("bool respawned;", result_body)

    def test_dispatch_mirrors_complete_vanilla_game_respawn_recipe(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        self.assertIn('else if (command.cmd == "player_respawn")', dispatch)

        body = _method_body(source, "protected bool DispatchPlayerRespawn(")
        steps = (
            "GetGame().GetMenuDefaultCharacterData(false).SetRandomCharacterForced(true);",
            "GetGame().RespawnPlayer();",
            "player.SimulateDeath(true);",
            "GetGame().GetCallQueue(CALL_CATEGORY_GUI).Call(player.ShowDeadScreen, true, 0);",
            "missionGP.DestroyAllMenus();",
            "missionGP.SetPlayerRespawning(true);",
            "missionGP.Continue();",
            "respawnMenu.Close();",
            "result.requested = true;",
            "result.ok = true;",
        )
        positions = []
        for step in steps:
            with self.subTest(step=step):
                self.assertIn(step, body)
                positions.append(body.index(step))
        self.assertEqual(positions, sorted(positions), "respawn recipe order drifted")
        self.assertEqual(body.count("GetGame().RespawnPlayer();"), 1)
        self.assertNotIn("OnKeyPress", body)


if __name__ == "__main__":
    unittest.main()
