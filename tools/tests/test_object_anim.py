from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease


COMMAND = "object_anim"
VALID_READ = {
    "type": "Land_Garage_Row_Small",
    "pos": [7500.0, 0.0, 7500.0],
    "source": "Doors1",
}
VALID_WRITE = {**VALID_READ, "phase": 1.0}
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


class ObjectAnimIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")

    def test_happy_read_and_write_server(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        # Lease is name-only: dual-mode verb stays mutating fail-closed.
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))

        for args in (VALID_READ, VALID_WRITE):
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, dict(args))
                self.assertEqual(status, 200)
                self.assertEqual(body["peer"], "server")

    def test_rejects_bad_args(self) -> None:
        invalid = [
            {},
            {**VALID_READ, "source": ""},
            {**VALID_READ, "type": ""},
            {**VALID_READ, "pos": [1.0, 2.0]},
            {**VALID_READ, "phase": float("nan")},
            {**VALID_READ, "extra": 1},
            {"type": "X", "pos": [0.0, 0.0, 0.0], "source": "Doors1", "phase": True},
        ]
        for args in invalid:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})


class ObjectAnimEnforceContractTest(unittest.TestCase):
    def test_handler_read_write_and_entity_cast(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchObjectAnim(")
        required = [
            "FindUniqueObjectNearType(",
            "Entity.Cast(match)",
            "SetAnimationPhase(command.args.source, command.args.phase)",
            "GetAnimationPhase(command.args.source)",
            "command.args.phase != MCP_ARG_FLOAT_UNSET",
            "result.error = error",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, body)

        # object_not_found lives in the shared lookup helper, not the dispatch body.
        helper = _method_body(source, "protected Object FindUniqueObjectNearType(")
        self.assertIn('error = "object_not_found"', helper)


class ObjectAnimAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_app_tool_registered_and_forwards_optional_phase(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "phase": 0.0}),
        ) as call:
            await app.call_tool(
                COMMAND,
                {
                    "type": VALID_READ["type"],
                    "pos": VALID_READ["pos"],
                    "source": VALID_READ["source"],
                    "timeout_s": 1.0,
                },
            )
            call.assert_awaited_once_with(COMMAND, VALID_READ, "server", 1.0)

            call.reset_mock()
            await app.call_tool(
                COMMAND,
                {
                    "type": VALID_WRITE["type"],
                    "pos": VALID_WRITE["pos"],
                    "source": VALID_WRITE["source"],
                    "phase": 1.0,
                    "timeout_s": 1.0,
                },
            )
            call.assert_awaited_once_with(COMMAND, VALID_WRITE, "server", 1.0)


if __name__ == "__main__":
    unittest.main()
