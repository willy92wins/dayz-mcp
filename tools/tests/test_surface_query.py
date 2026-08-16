from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease


COMMAND = "surface_query"
VALID_ARGS = {"x": 7500.0, "z": 7500.0}
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
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


class SurfaceQueryIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")

    def test_happy_path_whitelisted_server_read_only(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease(COMMAND))

        status, body = self.state.enqueue_command(COMMAND, dict(VALID_ARGS))
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "server")

        status, body = self.state.enqueue_command(COMMAND, dict(VALID_ARGS), peer="client")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_peer"})

    def test_rejects_malformed_and_non_finite_args(self) -> None:
        invalid = [
            {},
            {"x": 1.0},
            {"z": 1.0},
            {"x": 1.0, "z": 1.0, "extra": 0},
            {"x": True, "z": 1.0},
            {"x": float("nan"), "z": 1.0},
            {"x": float("inf"), "z": 1.0},
            {"x": 1.0, "z": float("nan")},
            {"x": "1", "z": 1.0},
        ]
        for args in invalid:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})


class SurfaceQueryEnforceContractTest(unittest.TestCase):
    def test_handler_uses_surface_get_type_y_and_bounds_gate(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn('else if (command.cmd == "surface_query")', source)
        body = _method_body(source, "protected bool DispatchSurfaceQuery(")
        required = [
            "SurfaceGetType(",
            "SurfaceGetNormal(",
            'result.error = "out_of_bounds"',
            "IsFiniteWorldCoord(command.args.x, worldSize)",
            "IsFiniteWorldCoord(command.args.z, worldSize)",
            "result.y = surfaceY",
            "result.type = surfaceType",
            "result.normal = new array<float>()",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, body)
        # Published Y is SurfaceGetType's return, not a separate SurfaceY call.
        self.assertNotIn("SurfaceY(", body)

        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn("float y;", messages)
        self.assertIn("ref array<float> normal;", messages)


class SurfaceQueryAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_app_tool_registered_and_forwards(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "y": 12.0, "type": "cp_grass", "normal": [0, 1, 0]}),
        ) as call:
            await app.call_tool(COMMAND, {"x": 7500.0, "z": 7500.0, "timeout_s": 1.0})

        call.assert_awaited_once_with(COMMAND, {"x": 7500.0, "z": 7500.0}, "server", 1.0)


if __name__ == "__main__":
    unittest.main()
