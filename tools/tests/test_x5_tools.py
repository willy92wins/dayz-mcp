from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from dayz_mcp.server import Runtime, ServerConfig, build_app
from tests.test_mcp_tools import FakePeer, _assert_tool_error, _content_json


class X5ToolsTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_exec_audit_failure_does_not_enqueue(self) -> None:
        expr = "void main() { Print(\"ok\"); }"
        allowlist = self.tmp_path / "allowlist.json"
        allowlist.write_text(json.dumps([expr]), encoding="utf-8")
        app, runtime = self.build_started(enable_exec_enforce=True, exec_allowlist=str(allowlist))
        fake = self.start_peer(runtime, "server")

        def failing_audit(_expr: str, _verdict: str, _main_fn: str, _command_id: int | None) -> None:
            raise OSError("audit locked")

        runtime.state.exec_audit = failing_audit
        with self.assertRaises(Exception) as err:
            await app.call_tool("exec_enforce", {"expr": expr, "main_fn": "main", "timeout_s": 1.0})
        _assert_tool_error(self, err.exception)
        self.assertIn("audit_failed", str(err.exception))
        self.assertEqual(fake.commands_seen, [])

    async def test_exec_audit_entries_include_main_fn_and_bom_allowlist_loads(self) -> None:
        expr = "void main() { Print(\"ok\"); }"
        allowlist = self.tmp_path / "allowlist-bom.json"
        allowlist.write_text(json.dumps([expr]), encoding="utf-8-sig")
        app, runtime = self.build_started(enable_exec_enforce=True, exec_allowlist=str(allowlist))
        fake = self.start_peer(runtime, "server")

        with self.assertRaises(Exception) as denied:
            await app.call_tool("exec_enforce", {"expr": "Print(\"denied\")", "main_fn": "DeniedMain", "timeout_s": 1.0})
        _assert_tool_error(self, denied.exception)
        self.assertIn("exec_not_allowed", str(denied.exception))

        allowed = _content_json(await app.call_tool("exec_enforce", {"expr": expr, "main_fn": "AllowedMain", "timeout_s": 1.0}))
        self.assertTrue(allowed["ok"])
        self.assertEqual(fake.commands_seen[-1]["args"]["main_fn"], "AllowedMain")

        lines = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[0]["verdict"], "denied")
        self.assertEqual(lines[0]["main_fn"], "DeniedMain")
        self.assertEqual(lines[1]["verdict"], "allowed")
        self.assertEqual(lines[1]["main_fn"], "AllowedMain")

    async def test_exec_empty_expr_is_denied_and_audited(self) -> None:
        allowlist = self.tmp_path / "allowlist.json"
        allowlist.write_text(json.dumps(["allowed"]), encoding="utf-8")
        app, _runtime = self.build_started(enable_exec_enforce=True, exec_allowlist=str(allowlist))

        with self.assertRaises(Exception) as err:
            await app.call_tool("exec_enforce", {"expr": "", "main_fn": "main", "timeout_s": 1.0})
        _assert_tool_error(self, err.exception)
        self.assertIn("exec_not_allowed", str(err.exception))

        lines = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[0]["verdict"], "denied")
        self.assertEqual(lines[0]["expr"], "")
        self.assertEqual(lines[0]["main_fn"], "main")

    async def test_world_time_year_range(self) -> None:
        app, runtime = self.build_started()
        fake = self.start_peer(runtime, "server")

        bad_calls = [
            {"year": 1969, "month": 1, "day": 1, "hour": 0, "minute": 0},
            {"year": 2101, "month": 1, "day": 1, "hour": 0, "minute": 0},
        ]
        for payload in bad_calls:
            with self.assertRaises(Exception) as err:
                await app.call_tool("world_time_set", payload)
            _assert_tool_error(self, err.exception)
            self.assertIn("bad_year", str(err.exception))

        first = _content_json(await app.call_tool("world_time_set", {"year": 1970, "month": 1, "day": 1, "hour": 0, "minute": 0, "timeout_s": 1.0}))
        second = _content_json(await app.call_tool("world_time_set", {"year": 2100, "month": 12, "day": 31, "hour": 23, "minute": 59, "timeout_s": 1.0}))
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual([command["args"]["year"] for command in fake.commands_seen], [1970, 2100])


if __name__ == "__main__":
    unittest.main()
