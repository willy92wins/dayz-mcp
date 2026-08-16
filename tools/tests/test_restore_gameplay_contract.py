from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS
from tests._addon_paths import addon_root


MOD_SCRIPTS = addon_root() / "scripts"

CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"


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


class RestoreGameplayIngressContractTest(unittest.TestCase):
    def test_command_is_client_only_mutating_and_accepts_only_empty_args(self) -> None:
        self.assertIn("restore_gameplay", loopback.CLIENT_COMMANDS)
        self.assertNotIn("restore_gameplay", READ_ONLY_COMMANDS)
        self.assertEqual(loopback.peer_for_command("restore_gameplay"), "client")

        state = loopback.ServerState("test-key")
        status, body = state.enqueue_command("restore_gameplay", {})
        self.assertEqual((status, body["peer"]), (200, "client"))
        self.assertEqual(
            state.enqueue_command("restore_gameplay", {"extra": 1}),
            (400, {"error": "bad_args"}),
        )
        self.assertEqual(
            state.enqueue_command("restore_gameplay", {}, peer="server"),
            (400, {"error": "bad_peer"}),
        )


class RestoreGameplayFastMCPContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_tool_forwards_exact_empty_client_command(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn("restore_gameplay", tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1}),
        ) as call:
            await app.call_tool("restore_gameplay", {"timeout_s": 1.0})

        call.assert_awaited_once_with("restore_gameplay", {}, "client", 1.0)


class RestoreGameplayEnforceSourceContractTest(unittest.TestCase):
    def test_dispatch_restores_gameplay_and_reports_success(self) -> None:
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        branch = 'else if (command.cmd == "restore_gameplay")'
        self.assertIn(branch, dispatch)
        branch_start = dispatch.index(branch)
        branch_end = dispatch.index("}", branch_start)
        branch_body = dispatch[branch_start:branch_end]
        self.assertIn("RestoreGameplay();", branch_body)
        self.assertIn("result.ok = true;", branch_body)

    def test_vehicle_get_in_restores_once_before_reading_vehicle_command(self) -> None:
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        prep = _method_body(source, "protected bool ProcessVehicleGetInClientPrep(MCPJob job)")
        guard = "if (!job.sim_restored)"
        self.assertIn(guard, prep)
        self.assertIn("RestoreGameplay();", prep)
        self.assertIn("job.sim_restored = true;", prep)
        self.assertLess(prep.index(guard), prep.index("player.GetCommand_Vehicle()"))


if __name__ == "__main__":
    unittest.main()
