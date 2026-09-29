"""input_describe: client read of a registered UAInput and its selected bind."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.result_prune import prune_unfilled_fields
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root


COMMAND = "input_describe"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
PRESSING_SENTENCE = (
    "Pressing the input is out of scope; use key_press for an OnKeyPress handler."
)


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


class InputDescribeIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")

    def test_client_read_needs_no_lease(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease(COMMAND))

        status, body = self.state.enqueue_command(COMMAND, {"name": "UAMoveForward"})
        self.assertEqual((status, body["peer"]), (200, "client"))
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"name": "UAMoveForward"}, peer="server"),
            (400, {"error": "bad_peer"}),
        )

    def test_unknown_name_is_not_an_ingress_error(self) -> None:
        ok, err = loopback.validate_command_args(
            COMMAND, {"name": "MCP_NoSuchInput_e1ae"}
        )
        self.assertEqual((ok, err), (True, None))

    def test_name_bounds_match_printable_ascii(self) -> None:
        self.assertTrue(loopback.is_printable_input_name(" "))
        self.assertTrue(loopback.is_printable_input_name("a" * 128))
        self.assertFalse(loopback.is_printable_input_name(""))
        self.assertFalse(loopback.is_printable_input_name("a" * 129))
        self.assertFalse(loopback.is_printable_input_name("UA\nMove"))
        self.assertFalse(loopback.is_printable_input_name("UA\tMove"))
        self.assertFalse(loopback.is_printable_input_name("UA\x7f"))
        self.assertFalse(loopback.is_printable_input_name("UA\u00f1"))
        self.assertEqual(loopback.INPUT_NAME_MAX_CHARS, 128)

    def test_exists_false_survives_prune_and_an_empty_ref_does_not(self) -> None:
        described = {
            "exists": False,
            "binding_count": 0,
            "locked": False,
            "conflict_count": 0,
            "keys": [],
        }
        pruned = prune_unfilled_fields(
            COMMAND,
            {"ok": 1, "input_describe": described, "building_doors": {}},
        )
        self.assertEqual(pruned["input_describe"], described)
        self.assertNotIn("building_doors", pruned)


class InputDescribeFastMCPTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_forwards_the_name_and_says_pressing_is_out_of_scope(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)
        description = tools[COMMAND].description or ""
        self.assertIn(PRESSING_SENTENCE, description)
        self.assertNotIn("Requires a lease", description)

        payload = {
            "ok": 1,
            "input_describe": {
                "exists": True,
                "binding_count": 1,
                "locked": False,
                "conflict_count": 0,
                "keys": [{"index": 0, "key_code": 17, "device": 1}],
            },
        }
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value=payload)
        ) as call:
            await app.call_tool(COMMAND, {"name": "UAMoveForward", "timeout_s": 1.0})

        call.assert_awaited_once_with(
            COMMAND, {"name": "UAMoveForward"}, "client", 1.0
        )

    async def test_tool_rejects_a_name_the_bridge_would_refuse(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        with patch.object(runtime, "call_bridge", new=AsyncMock()) as call:
            for name in ("", "a" * 129, "UA\n", "UA\u00f1"):
                with self.subTest(name=name):
                    with self.assertRaises(server.ToolError) as ctx:
                        await app.call_tool(COMMAND, {"name": name, "timeout_s": 1.0})
                    self.assertIn("bad_args", str(ctx.exception))
                    self.assertIn("32..126", str(ctx.exception))
        call.assert_not_awaited()


class InputDescribeEnforceContractTest(unittest.TestCase):
    def test_null_input_is_exists_false_before_any_bind_read(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        gate = dispatch.index("client_not_in_game")
        branch = dispatch.index('command.cmd == "input_describe"')
        dialog = dispatch.index('command.cmd == "ui_dialog"')
        self.assertLess(gate, branch)
        self.assertLess(branch, dialog)
        self.assertIn("input_describe", source[source.index("CLIENT_POLL_CAPS") : source.index("CLIENT_POLL_CAPS") + 400])

        body = _method_body(source, "protected bool DispatchInputDescribe(")
        null_at = body.index("if (!input)")
        bind_at = body.index("BindingCount()")
        null_branch = body[null_at:bind_at]
        self.assertIn("described.exists = false", null_branch)
        self.assertIn("result.ok = true", null_branch)
        self.assertNotIn("BindingCount", null_branch)
        self.assertNotIn("result.error", null_branch)
        self.assertIn('result.error = "input_api_unavailable"', body)
        self.assertIn('result.error = "input_bind_unreadable"', body)
        for token in (
            "GetUApi()",
            "GetInputByName(command.args.name)",
            "BindKeyCount()",
            "GetBindKey(keyIndex)",
            "GetBindDevice(keyIndex)",
            "IsLocked()",
            "ConflictCount()",
            "described.binding_count = bindingCount",
            "result.input_describe = described",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        for forbidden in (
            "SelectAlternative",
            "LocalPress",
            "BindCombo",
            "ClearBinding",
            "input.Lock(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn("class MCPInputKey", messages)
        self.assertIn("class MCPInputDescribe", messages)
        self.assertIn("ref MCPInputDescribe input_describe;", messages)

    def test_stale_client_census_names_this_tool(self) -> None:
        announced = sorted(server._BRIDGE_COMMAND_TOOLS["client"])
        announced.remove(COMMAND)
        registered = frozenset(
            tool for tool in server._BRIDGE_COMMAND_TOOLS["client"].values() if tool
        )
        result = server._compare_bridge_capabilities(
            "client",
            {"state": "announced", "reason": "ok", "announced_commands": announced},
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["reason"], "census_disagrees_with_registered_tools")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])


if __name__ == "__main__":
    unittest.main()
