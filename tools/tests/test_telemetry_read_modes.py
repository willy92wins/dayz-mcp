"""telemetry_read publishes a closed mode enum and names each mode's contract.

fb-20260908-231330-62e3: callers could not tell which args each mode consumes,
what it returns, or whether object_at could read a modded entity's synchronized
script members. Guessing costs a shared-box launch. The schema enum and the
tool description are the contract; the handler still only forwards the two
instrumented bridge modes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from dayz_mcp import server  # noqa: E402
from dayz_mcp.server import ServerConfig, build_app  # noqa: E402

from tests.test_client_mode import _fixture_client_runtime  # noqa: E402


class TelemetryReadModesContractTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            self.app, self.runtime = build_app(config)

    def _tool(self):
        return self.app._tool_manager.get_tool("telemetry_read")

    def test_schema_enum_is_exactly_the_two_implemented_modes(self) -> None:
        enum = self._tool().parameters["properties"]["mode"]["enum"]
        self.assertEqual(set(enum), set(server.TELEMETRY_READ_MODES))
        self.assertEqual({"object_at", "fixture_jsonl"}, set(enum))

    async def test_list_tools_schema_matches_the_manager_enum(self) -> None:
        listed = {tool.name: tool for tool in await self.app.list_tools()}
        listed_enum = listed["telemetry_read"].inputSchema["properties"]["mode"]["enum"]
        manager_enum = self._tool().parameters["properties"]["mode"]["enum"]
        self.assertEqual(set(listed_enum), set(manager_enum))
        self.assertEqual(set(listed_enum), {"object_at", "fixture_jsonl"})

    def test_description_has_one_consume_clause_per_mode(self) -> None:
        description = self._tool().description or ""
        self.assertIn("object_at consumes", description)
        self.assertIn("fixture_jsonl consumes", description)
        self.assertIn("type (exact GetType match)", description)
        self.assertIn("path and max_lines", description)
        self.assertIn("Returns {ok,telemetry:{mode,found,type", description)
        self.assertIn(
            "Returns {ok,telemetry:{mode,path,found,line_count_read",
            description,
        )
        self.assertIn("raise ToolError(ambiguous_fixture)", description)
        self.assertIn("raises ToolError(fixture_not_found)", description)
        self.assertIn("raises ToolError(parse_error)", description)
        self.assertIn("MCP tool error, not a returned dict", description)
        self.assertNotIn("ok:false", description)

    def test_description_says_object_at_does_not_reach_mod_sync_state(self) -> None:
        description = self._tool().description or ""
        self.assertIn("does not reach arbitrary script members", description)
        self.assertIn("synchronized variables of a modded entity", description)

    async def test_unknown_mode_never_reaches_the_bridge(self) -> None:
        async def unexpected(*_args, **_kwargs):
            raise AssertionError("unknown mode must not call the bridge")

        self.runtime.call_bridge = unexpected
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("telemetry_read", {"mode": "nonsense"})
        message = str(ctx.exception)
        self.assertIn("mode", message)
        self.assertIn("object_at", message)
        self.assertIn("fixture_jsonl", message)

    async def test_each_published_mode_forwards_only_its_args(self) -> None:
        calls: list[tuple] = []

        async def capture(cmd, args, peer, timeout_s):
            calls.append((cmd, args, peer, timeout_s))
            return {"ok": True, "telemetry": {"mode": args["mode"]}}

        self.runtime.call_bridge = capture
        await self.app.call_tool(
            "telemetry_read",
            {
                "mode": "object_at",
                "type": "WoodenCrate",
                "pos": [1.0, 2.0, 3.0],
                "radius": 5.0,
                "path": "ignored",
                "max_lines": 3,
            },
        )
        await self.app.call_tool(
            "telemetry_read",
            {
                "mode": "fixture_jsonl",
                "path": "$mission:dayz_mcp/sample.jsonl",
                "max_lines": 8,
                "type": "ignored",
                "pos": [9.0, 9.0, 9.0],
                "radius": 4.0,
            },
        )
        self.assertEqual(calls[0][0], "telemetry_read")
        self.assertEqual(calls[0][1], {
            "mode": "object_at",
            "type": "WoodenCrate",
            "pos": [1.0, 2.0, 3.0],
            "radius": 5.0,
        })
        self.assertEqual(calls[0][2], "server")
        self.assertEqual(calls[1][1], {
            "mode": "fixture_jsonl",
            "path": "$mission:dayz_mcp/sample.jsonl",
            "max_lines": 8,
        })


def _fake_bridge_state(result: dict) -> SimpleNamespace:
    return SimpleNamespace(
        enqueue_command=lambda *args, **kwargs: (200, {"id": 41}),
        take_result=lambda command_id, remove=False: dict(result),
        abandon_command=lambda *args, **kwargs: None,
    )


class TelemetryReadPublicErrorsAreToolErrorsTest(unittest.IsolatedAsyncioTestCase):
    """A falsy bridge ok is a ToolError, not a returned {ok:false,...} envelope.

    Both runtimes convert in wait_for_result. Tests that replace call_bridge
    with a success stub cannot see that; this one keeps the real conversion.
    """

    async def _call_through_real_conversion(self, arguments: dict, result: dict):
        app, runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        with patch.object(runtime, "ensure_peer_allowed", return_value=None), patch.object(
            type(runtime), "state", new_callable=PropertyMock, return_value=_fake_bridge_state(result)
        ):
            return await app.call_tool("telemetry_read", arguments)

    async def test_bridge_codes_raise_tool_error_not_ok_false_dict(self) -> None:
        cases = (
            (
                "ambiguous_fixture",
                {
                    "mode": "object_at",
                    "type": "WoodenCrate",
                    "pos": [1.0, 2.0, 3.0],
                    "radius": 5.0,
                },
            ),
            (
                "fixture_not_found",
                {"mode": "fixture_jsonl", "path": "$mission:dayz_mcp/sample.jsonl"},
            ),
            (
                "parse_error",
                {"mode": "fixture_jsonl", "path": "$mission:dayz_mcp/sample.jsonl"},
            ),
        )
        for code, arguments in cases:
            with self.subTest(code=code):
                with self.assertRaises(ToolError) as ctx:
                    payload = await self._call_through_real_conversion(
                        arguments, {"ok": 0, "error": code}
                    )
                    self.fail(f"expected ToolError, got envelope {payload!r}")
                self.assertEqual(type(ctx.exception).__name__, "ToolError")
                message = str(ctx.exception)
                self.assertIn(code, message)
                self.assertIn("Error executing tool telemetry_read:", message)
                self.assertNotIn("ok:false", message)


if __name__ == "__main__":
    unittest.main()
