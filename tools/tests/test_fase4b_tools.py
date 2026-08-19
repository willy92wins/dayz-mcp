from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from dayz_mcp.server import ServerConfig, Runtime, build_app
from tests.test_mcp_tools import FakePeer, _assert_tool_error, _content_json


class Fase4BToolsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.key = "test-key"
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.audit_path = self.tmp_path / "exec_enforce.jsonl"
        self.peers: list[FakePeer] = []
        self.runtimes: list[Runtime] = []

    async def asyncTearDown(self) -> None:
        await asyncio.gather(*(asyncio.to_thread(peer.stop) for peer in self.peers))
        await asyncio.gather(*(asyncio.to_thread(runtime.stop_loopback) for runtime in self.runtimes))
        self.tmp.cleanup()

    def build_started(self, **config_kwargs: Any):
        app, runtime = build_app(
            ServerConfig(
                key=self.key,
                port=0,
                log_sink=lambda _message: None,
                exec_audit_path=str(self.audit_path),
                **config_kwargs,
            )
        )
        runtime.start_loopback()
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(runtime.state)
        self.runtimes.append(runtime)
        return app, runtime

    def start_peer(
        self,
        runtime: Runtime,
        peer: str,
        responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> FakePeer:
        fake = FakePeer(runtime, self.key, peer, responder=responder)
        fake.start()
        self.peers.append(fake)
        return fake

    async def test_world_tools_happy_path_enqueue_to_server_peer(self) -> None:
        app, runtime = self.build_started()
        fake = self.start_peer(runtime, "server")

        time_result = _content_json(
            await app.call_tool(
                "world_time_set",
                {"year": 2026, "month": 6, "day": 10, "hour": 22, "minute": 15, "time_multiplier": -1, "timeout_s": 1.0},
            )
        )
        self.assertTrue(time_result["ok"])

        weather_result = _content_json(await app.call_tool("world_weather_set", {"overcast": 0.5, "rain": 0.0, "timeout_s": 1.0}))
        self.assertTrue(weather_result["ok"])

        commands = [(command["cmd"], command["args"]) for command in fake.commands_seen]
        self.assertEqual(commands[0][0], "world_time_set")
        self.assertEqual(commands[0][1]["time_multiplier"], -1.0)
        self.assertEqual(commands[1][0], "world_weather_set")
        self.assertEqual(commands[1][1]["overcast"], 0.5)
        self.assertEqual(commands[1][1]["rain"], 0.0)

    async def test_world_tool_negatives_raise_before_enqueue(self) -> None:
        app, runtime = self.build_started()
        fake = self.start_peer(runtime, "server")

        bad_calls = [
            ("world_time_set", {"year": 2026, "month": 13, "day": 10, "hour": 22, "minute": 15}),
            ("world_time_set", {"year": 2026, "month": 6, "day": 10, "hour": 24, "minute": 15}),
            ("world_time_set", {"year": 2026, "month": 6, "day": 10, "hour": 22, "minute": 15, "time_multiplier": 65}),
            ("world_weather_set", {"overcast": -0.1}),
            ("world_weather_set", {"rain": 1.5}),
            ("world_weather_set", {}),
        ]

        for tool_name, payload in bad_calls:
            with self.assertRaises(Exception) as err:
                await app.call_tool(tool_name, payload)
            _assert_tool_error(self, err.exception)

        self.assertEqual(fake.commands_seen, [])

    async def test_exec_tool_absent_without_flag(self) -> None:
        app, _runtime = self.build_started()
        tools = await app.list_tools()
        names = {tool.name for tool in tools}
        self.assertNotIn("exec_enforce", names)

    async def test_exec_allowlist_denied_and_allowed_are_audited(self) -> None:
        expr = "void main() { Print(\"ok\"); }"
        allowlist = self.tmp_path / "allowlist.json"
        allowlist.write_text(json.dumps([expr]), encoding="utf-8")
        app, runtime = self.build_started(enable_exec_enforce=True, exec_allowlist=str(allowlist))
        fake = self.start_peer(runtime, "server")

        tools = await app.list_tools()
        names = {tool.name for tool in tools}
        self.assertIn("exec_enforce", names)

        with self.assertRaises(Exception) as denied:
            await app.call_tool("exec_enforce", {"expr": "Print(\"denied\")", "timeout_s": 1.0})
        _assert_tool_error(self, denied.exception)
        self.assertIn("exec_not_allowed", str(denied.exception))

        allowed = _content_json(await app.call_tool("exec_enforce", {"expr": expr, "main_fn": "main", "timeout_s": 1.0}))
        self.assertTrue(allowed["ok"])
        self.assertEqual(fake.commands_seen[-1]["cmd"], "exec_enforce")
        self.assertEqual(fake.commands_seen[-1]["args"]["expr"], expr)

        lines = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[0]["verdict"], "denied")
        self.assertEqual(lines[0]["expr"], "Print(\"denied\")")
        self.assertEqual(lines[1]["verdict"], "allowed")
        self.assertEqual(lines[1]["expr"], expr)
        self.assertIn("command_id", lines[1])


if __name__ == "__main__":
    unittest.main()
