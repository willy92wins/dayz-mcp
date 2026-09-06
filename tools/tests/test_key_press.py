from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import command_requires_lease
from tests._addon_paths import addon_root


COMMAND = "key_press"
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


class KeyPressIngressContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_command_is_client_only_mutating(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertTrue(command_requires_lease(COMMAND))

        status, body = self.state.enqueue_command(COMMAND, {"dik": 1})
        self.assertEqual((status, body["peer"]), (200, "client"))
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"dik": 1}, peer="server"),
            (400, {"error": "bad_peer"}),
        )

    def test_schema_accepts_esc_and_rejects_ambiguous_payloads(self) -> None:
        self.assertEqual(
            loopback.validate_command_args(COMMAND, {"dik": 1}), (True, None)
        )
        for args in ({}, {"dik": -1}, {"dik": True}, {"dik": 1, "extra": 0}):
            with self.subTest(args=args):
                self.assertEqual(
                    loopback.validate_command_args(COMMAND, args),
                    (False, "bad_args"),
                )


class KeyPressFastMCPContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_tool_forwards_exact_dik_to_client(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "delivered": True, "dik": 1}),
        ) as call:
            await app.call_tool(COMMAND, {"dik": 1, "timeout_s": 1.0})

        call.assert_awaited_once_with(COMMAND, {"dik": 1}, "client", 1.0)

    async def test_public_tool_rejects_bool_and_negative_dik_before_bridge(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        with patch.object(runtime, "call_bridge", new=AsyncMock()) as call:
            for dik in (True, -1):
                with self.subTest(dik=dik):
                    with self.assertRaises(server.ToolError):
                        await app.call_tool(COMMAND, {"dik": dik, "timeout_s": 1.0})
        call.assert_not_awaited()


class KeyPressEnforceSourceContractTest(unittest.TestCase):
    def test_wire_has_dedicated_dik_with_missing_value_sentinel(self) -> None:
        source = MESSAGES_PATH.read_text(encoding="utf-8")
        args_body = _method_body(source, "class MCPArgs")
        self.assertIn("int dik;", args_body)
        constructor = _method_body(source, "void MCPArgs()")
        self.assertIn("dik = -1;", constructor)

    def test_dispatch_delivers_once_to_mission_callback_only(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        self.assertIn('else if (command.cmd == "key_press")', dispatch)

        body = _method_body(source, "protected bool DispatchKeyPress(")
        exact_call = "GetGame().GetMission().OnKeyPress(dik);"
        self.assertEqual(body.count(exact_call), 1)
        self.assertIn("int dik = command.args.dik;", body)
        self.assertIn("result.delivered = true;", body)
        self.assertIn("result.dik = dik;", body)
        self.assertIn("result.ok = true;", body)

        for forbidden in (
            "SendInput",
            "keybd_event",
            "UAInput",
            "LocalPress",
            "DayZGame.OnKeyPress",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)


if __name__ == "__main__":
    unittest.main()
