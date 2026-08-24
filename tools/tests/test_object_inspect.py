from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease


COMMAND = "object_inspect"
VALID_ARGS = {
    "type": "CivilianSedan",
    "pos": [7500.0, 0.0, 7500.0],
    "want": ["bounding_center", "usti hlavne", "mcp_invented_point"],
}
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


class ObjectInspectIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")

    def test_happy_path_server_read_only(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease(COMMAND))

        by_id = {"object_id": 5, "want": ["bounding_center"]}
        for args in (dict(VALID_ARGS), by_id):
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)
                self.assertEqual(status, 200)
                self.assertEqual(body["peer"], "server")

    def test_rejects_bad_args(self) -> None:
        invalid = [
            {},
            {**VALID_ARGS, "want": []},
            {**VALID_ARGS, "want": [""]},
            {**VALID_ARGS, "want": [1]},
            {**VALID_ARGS, "type": ""},
            {**VALID_ARGS, "pos": [1.0, 2.0]},
            {**VALID_ARGS, "extra": True},
            {"object_id": 0, "want": ["bounding_center"]},
            {"object_id": 5},
            {"object_id": 5, "type": "X", "want": ["bounding_center"]},
        ]
        for args in invalid:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})


class ObjectInspectEnforceContractTest(unittest.TestCase):
    def test_missing_memory_point_is_exists_false_with_ok_true(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchObjectInspect(")
        required = [
            "MemoryPointExists(wantName)",
            "GetMemoryPointPos(wantName)",
            "GetBoundingCenter()",
            "point.exists = false",
            "point.exists = true",
            "result.inspect = inspect",
            "result.ok = true",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, body)

        # Contract: inventing a memory point must not become a tool error path.
        self.assertNotIn('result.error = "memory_point_missing"', body)
        self.assertNotIn('result.error = "unknown_memory_point"', body)

        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn("class MCPMemoryPoint", messages)
        self.assertIn("class MCPObjectInspect", messages)
        self.assertIn("ref MCPObjectInspect inspect;", messages)


class ObjectInspectAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_app_tool_registered_and_forwards(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        payload = {
            "ok": 1,
            "inspect": {
                "type": "CivilianSedan",
                "memory_points": [
                    {"name": "mcp_invented_point", "exists": False, "pos": []},
                ],
            },
        }
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value=payload)
        ) as call:
            await app.call_tool(
                COMMAND,
                {
                    "type": VALID_ARGS["type"],
                    "pos": VALID_ARGS["pos"],
                    "want": VALID_ARGS["want"],
                    "timeout_s": 1.0,
                },
            )

        call.assert_awaited_once_with(COMMAND, VALID_ARGS, "server", 1.0)


if __name__ == "__main__":
    unittest.main()
