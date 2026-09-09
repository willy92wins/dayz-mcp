"""Offline consumer tests for the one-runner reload contract; no live services."""
from __future__ import annotations

import asyncio
import os
import py_compile
import sys
import tempfile
import threading
import types as python_types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp import types
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

from dayz_mcp import playbook_tool as adapter, server, server_freshness as freshness
from tests.test_client_mode import _fixture_client_runtime

MODULE = "dayz_playbook_runner"
TAIL = '''
_IMPLEMENTATION = "old"
_original_async_run = _async_run

async def _async_run(*args, **kwargs):
    verdict = await _original_async_run(*args, **kwargs)
    return {**verdict, "implementation": _IMPLEMENTATION}
'''
PLAYBOOK = '''id = "fixture"
version = "1"
status = "DRAFT"
requires_tools = ["query_all_players"]

[[steps]]
id = "S1"
tool = "query_all_players"
args = {}
expect = []
on_fail = { action = "STOP", reason = "fixture_failed" }
'''


class PlaybookReloadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.root / "runner.py"
        real_source = (adapter.PLAYBOOKS_DIR / "runner.py").read_text(encoding="utf-8")
        self.old_source = real_source + TAIL
        self.new_source = self.old_source.replace('_IMPLEMENTATION = "old"', '_IMPLEMENTATION = "new"')
        self.path.write_text(self.old_source, encoding="utf-8")
        (self.root / "fixture.toml").write_text(PLAYBOOK, encoding="utf-8")
        self.stack.enter_context(patch.dict(sys.modules))
        for attr, value in (
            ("PLAYBOOKS_DIR", self.root), ("_runner", None),
            ("_runner_source", None), ("_active_runs", 0), ("_runner_loading", False),
        ):
            self.stack.enter_context(patch.object(adapter, attr, value))
        self.old_runner = adapter.load_runner()
        self.old_receipt = adapter.runner_source_snapshot()
        self.watch = freshness.ServerSourceWatch(freshness.loaded_source_files())
        self.stack.enter_context(patch.object(server, "_SERVER_SOURCES", self.watch))
        self.app, self.runtime = self.build()
        self.boot_hashes = dict(self.watch._hashes)

    def build(self):
        config = server.ServerConfig(
            mode="client", key="fixture-key", port=12345,
            client_platform="codex", auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        runtime.bridge_status_payload = AsyncMock(return_value={"ready": True})
        runtime.call_bridge = AsyncMock(return_value={"ok": 1, "players": []})
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, built = server.build_app(config)
        self.assertIs(built, runtime)
        return app, runtime

    async def call(self, name, arguments=None, app=None):
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments or {}),
        )
        response = await (app or self.app)._mcp_server.request_handlers[types.CallToolRequest](request)
        return types.CallToolResult.model_validate_json(response.root.model_dump_json(by_alias=True))

    async def reload(self, app=None):
        return await self.call("playbook_reload", {"module": MODULE}, app=app)

    async def run_book(self, app=None):
        return await self.call("playbook_run", {"name": "fixture"}, app=app)

    def assert_preserved(self):
        self.assertIs(adapter.load_runner(), self.old_runner)
        self.assertIs(sys.modules[MODULE], self.old_runner)
        self.assertIs(adapter.runner_source_snapshot(), self.old_receipt)
        self.assertEqual(self.old_runner._IMPLEMENTATION, "old")
        self.assertEqual(self.watch._hashes, self.boot_hashes)

    async def test_wire_reload_changes_behavior_and_normal_status_without_reanchoring(self):
        app2, _ = self.build()
        handler = self.app._mcp_server.request_handlers[types.CallToolRequest]
        runtime_identity = self.runtime.identity
        schemas = await self.app.list_tools()
        async with create_connected_server_and_client_session(self.app._mcp_server) as session:
            first = await session.call_tool("playbook_run", {"name": "fixture"})
            self.assertEqual(first.structuredContent["implementation"], "old")
            self.path.write_text(self.new_source + "\n", encoding="utf-8")
            stale = await session.call_tool("playbook_run", {"name": "fixture"})
            self.assertEqual(stale.structuredContent["implementation"], "old")
            self.assertEqual(stale.meta[freshness.MARKER]["stale"], [MODULE])
            result = await session.call_tool("playbook_reload", {"module": MODULE})
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent, {
                "status": "reloaded", "module": MODULE, "scope": "current_mcp_process",
            })
            # The call started stale: preserving its entry warning is intentional.
            self.assertEqual(result.meta[freshness.MARKER]["stale"], [MODULE])
            status = await session.call_tool("bridge_status", {})
            self.assertEqual(status.structuredContent["server_modules"]["status"], "fresh")
            self.assertIs(status.structuredContent["tool_registry_source_stale"], False)
            self.assertNotIn(freshness.MARKER, status.meta or {})
            changed = await session.call_tool("playbook_run", {"name": "fixture"})
            self.assertEqual(changed.structuredContent["implementation"], "new")
            self.assertEqual(changed.structuredContent["overall"], "PASS")
        self.assertEqual((await self.run_book(app2)).structuredContent["implementation"], "new")
        self.assertEqual(schemas, await self.app.list_tools())
        self.assertIs(self.app._mcp_server.request_handlers[types.CallToolRequest], handler)
        self.assertIs(self.runtime.identity, runtime_identity)
        self.assertIs(server._SERVER_SOURCES, self.watch)
        self.assertEqual(self.watch._hashes, self.boot_hashes)

    async def test_allowlist_rejects_all_other_targets_before_loading(self):
        with patch.object(adapter, "_load_runner_locked", side_effect=AssertionError("must not load")):
            for target in ("runner", "dayz_mcp.server", "dayz_mcp.playbook_tool",
                           "dayz_mcp.control_client", "../runner.py", MODULE + " ", "", None, 1):
                with self.subTest(target=target), self.assertRaisesRegex(ToolError, "module_not_allowed"):
                    adapter.reload_runner(target)
        self.assert_preserved()

    async def test_wire_requires_exact_explicit_module(self):
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        schema = tools["playbook_reload"].inputSchema
        self.assertIn("module", schema["required"])
        self.assertEqual(schema["properties"]["module"]["const"], MODULE)
        for arguments in ({}, {"module": "dayz_mcp.server"}, {"module": "runner"},
                          {"module": True}, {"module": None}, {"module": "../runner.py"}):
            with self.subTest(arguments=arguments):
                result = await self.call("playbook_reload", arguments)
                self.assertTrue(result.isError)
                self.assert_preserved()

    async def test_in_flight_runs_across_apps_refuse_until_last_run_finishes(self):
        app2, runtime2 = self.build()
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]

        def hold(index):
            async def callback(*_args, **_kwargs):
                entered[index].set()
                await release[index].wait()
                return {"ok": 1, "players": []}
            return callback

        self.runtime.call_bridge.side_effect = hold(0)
        runtime2.call_bridge.side_effect = hold(1)
        tasks = [asyncio.create_task(self.run_book()), asyncio.create_task(self.run_book(app2))]
        try:
            for event in entered:
                await asyncio.wait_for(event.wait(), 3)
            self.path.write_text(self.new_source, encoding="utf-8")
            refused = await asyncio.wait_for(self.reload(), 3)
            self.assertTrue(refused.isError)
            self.assertIn("playbook_in_flight; active_runs=2", refused.content[0].text)
            self.assert_preserved()
            release[0].set()
            self.assertEqual((await tasks[0]).structuredContent["implementation"], "old")
            refused = await self.reload(app2)
            self.assertTrue(refused.isError)
            self.assertIn("active_runs=1", refused.content[0].text)
            release[1].set()
            self.assertEqual((await tasks[1]).structuredContent["implementation"], "old")
            self.assertFalse((await self.reload()).isError)
        finally:
            for event in release:
                event.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_cancellation_releases_execution_admission(self):
        entered = asyncio.Event()

        async def hold(*_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()

        self.runtime.call_bridge.side_effect = hold
        task = asyncio.create_task(self.run_book())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            self.assertTrue((await self.reload()).isError)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(adapter._active_runs, 0)
        self.assertFalse((await self.reload()).isError)

    async def test_schema_error_releases_execution_admission(self):
        (self.root / "fixture.toml").write_text('status = "INVALID"\n', encoding="utf-8")
        self.assertTrue((await self.run_book()).isError)
        self.assertEqual(adapter._active_runs, 0)
        self.assertFalse((await self.reload()).isError)

    async def test_admission_covers_synchronous_playbook_loading(self):
        load = self.old_runner.load_playbook

        def during_load(path):
            with self.assertRaisesRegex(ToolError, "playbook_in_flight; active_runs=1"):
                adapter.reload_runner(MODULE)
            return load(path)

        with patch.object(self.old_runner, "load_playbook", side_effect=during_load):
            self.assertFalse((await self.run_book()).isError)
        self.assertEqual(adapter._active_runs, 0)

    async def test_playbook_cannot_invoke_reload_as_a_step(self):
        book = PLAYBOOK.replace("query_all_players", "playbook_reload").replace(
            "args = {}", 'args = { module = "dayz_playbook_runner" }')
        (self.root / "fixture.toml").write_text(book, encoding="utf-8")
        result = await self.run_book()
        self.assertEqual(result.structuredContent["overall"], "FAIL")
        self.assertIn("not allowed inside a playbook", result.structuredContent["reason"])
        self.assert_preserved()

    async def test_mid_execution_failure_restores_module_and_allows_recovery(self):
        self.path.write_text(self.new_source + '\n_IMPLEMENTATION = "partial"\nraise RuntimeError("fixture")\n',
                             encoding="utf-8")
        failed = await self.reload()
        self.assertTrue(failed.isError)
        self.assertIn("playbook_runner_load_failed: RuntimeError", failed.content[0].text)
        self.assert_preserved()
        self.assertEqual((await self.run_book()).structuredContent["implementation"], "old")
        self.assertIn(MODULE, self.watch.snapshot()["stale"])
        self.path.write_text(self.new_source, encoding="utf-8")
        self.assertFalse((await self.reload()).isError)
        self.assertEqual((await self.run_book()).structuredContent["implementation"], "new")

    async def test_syntax_failure_preserves_old_runner(self):
        self.path.write_text("def broken(:\n", encoding="utf-8")
        result = await self.reload()
        self.assertTrue(result.isError)
        self.assertIn("SyntaxError", result.content[0].text)
        self.assert_preserved()

    async def test_missing_source_preserves_old_runner_and_reports_unknown(self):
        self.path.unlink()
        result = await self.reload()
        self.assertTrue(result.isError)
        self.assertIn("playbook_runner_missing", result.content[0].text)
        self.assert_preserved()
        self.assertEqual(self.watch.snapshot()["unreadable_reasons"][MODULE], "source_unreadable_now")

    async def test_module_level_exit_is_an_error_and_keeps_old_runner(self):
        self.path.write_text(self.new_source + "\nraise SystemExit(7)\n", encoding="utf-8")
        result = await self.reload()
        self.assertTrue(result.isError)
        self.assertIn("SystemExit", result.content[0].text)
        self.assert_preserved()

    async def test_invalid_runner_api_is_not_published(self):
        for suffix in ("_async_run = None", "SchemaError = lambda *args: None",
                       "def _async_run(*args): return {}"):
            with self.subTest(suffix=suffix):
                self.path.write_text(self.new_source + "\n" + suffix + "\n", encoding="utf-8")
                result = await self.reload()
                self.assertTrue(result.isError)
                self.assertIn("required_api_missing", result.content[0].text)
                self.assert_preserved()

    async def test_failed_first_load_cleans_import_slot_and_can_retry(self):
        adapter._runner = None
        adapter._runner_source = None
        sys.modules.pop(MODULE)
        self.path.write_text(self.old_source + "\nraise RuntimeError('fixture')\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "RuntimeError"):
            adapter.load_runner()
        self.assertIsNone(adapter._runner)
        self.assertNotIn(MODULE, sys.modules)
        self.path.write_text(self.new_source, encoding="utf-8")
        self.assertEqual(adapter.load_runner()._IMPLEMENTATION, "new")

    async def test_reload_bypasses_valid_old_pyc_and_refreshes_same_stat_cache(self):
        py_compile.compile(str(self.path), doraise=True)
        before = self.path.stat()
        self.path.write_text(self.new_source, encoding="utf-8")
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        # Independent control: the default loader really does select the old pyc.
        control = python_types.ModuleType("pyc_control")
        control.__file__ = str(self.path)
        adapter.importlib.machinery.SourceFileLoader(control.__name__, str(self.path)).exec_module(control)
        self.assertEqual(control._IMPLEMENTATION, "old")
        self.assertFalse((await self.reload()).isError)
        self.assertEqual((await self.run_book()).structuredContent["implementation"], "new")
        self.assertEqual(self.watch.snapshot()["status"], "fresh")
        self.assertEqual(self.watch._hashes, self.boot_hashes)

    async def test_source_change_during_exec_rejects_candidate(self):
        execute = adapter._RunnerSourceLoader.exec_module

        def edit_after_exec(loader, module):
            execute(loader, module)
            self.path.write_text(self.new_source + "\n# later edit\n", encoding="utf-8")

        with patch.object(adapter._RunnerSourceLoader, "exec_module", edit_after_exec):
            result = await self.reload()
        self.assertTrue(result.isError)
        self.assertIn("source_changed_during_load", result.content[0].text)
        self.assert_preserved()

    async def test_concurrent_reload_and_run_refuse_during_module_construction(self):
        entered, release = threading.Event(), threading.Event()
        execute = adapter._RunnerSourceLoader.exec_module

        def paused(loader, module):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture barrier timeout")
            execute(loader, module)

        with patch.object(adapter._RunnerSourceLoader, "exec_module", paused):
            task = asyncio.create_task(asyncio.to_thread(adapter.reload_runner, MODULE))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                status = await asyncio.wait_for(asyncio.to_thread(self.watch.snapshot), 1)
                self.assertEqual(status["status"], "unknown")
                self.assertEqual(status["unreadable_reasons"][MODULE], "runner_load_unverified")
                refused = await asyncio.wait_for(self.reload(), 1)
                self.assertTrue(refused.isError)
                self.assertIn("load_in_progress", refused.content[0].text)
                refused = await asyncio.wait_for(self.run_book(), 1)
                self.assertTrue(refused.isError)
                self.assertIn("load_in_progress", refused.content[0].text)
            finally:
                release.set()
                result = await task
        self.assertEqual(result["status"], "reloaded")
        self.assertEqual(adapter._active_runs, 0)

    async def test_generation_change_during_observation_is_unknown_then_fresh(self):
        self.path.write_text(self.new_source + "\n", encoding="utf-8")
        digest = freshness._digest
        changed = False

        def reload_at_read(path):
            nonlocal changed
            if path == self.path and not changed:
                changed = True
                adapter.reload_runner(MODULE)
            return digest(path)

        with patch.object(freshness, "_digest", reload_at_read):
            status = self.watch.snapshot()
        self.assertEqual(status["status"], "unknown")
        self.assertEqual(status["unreadable_reasons"][MODULE], "runner_changed_during_observation")
        self.assertEqual(self.watch.snapshot()["status"], "fresh")
        self.assertEqual(self.watch._hashes, self.boot_hashes)

    async def test_unaccredited_import_slot_replacement_cannot_turn_fresh(self):
        replacement = python_types.ModuleType(MODULE)
        replacement.__file__ = str(self.path)
        sys.modules[MODULE] = replacement
        status = self.watch.snapshot()
        self.assertEqual(status["status"], "unknown")
        self.assertEqual(status["unreadable_reasons"][MODULE], "runner_load_unverified")
        sys.modules[MODULE] = self.old_runner
        self.assertEqual(self.watch.snapshot()["status"], "fresh")

    async def test_reload_does_not_clear_other_modules_drift(self):
        path = self.root / "unrelated.py"
        path.write_text("value = 1\n", encoding="utf-8")
        module = python_types.ModuleType("dayz_mcp.unrelated_reload_fixture")
        module.__file__ = str(path)
        sys.modules[module.__name__] = module
        watch = freshness.ServerSourceWatch(freshness.loaded_source_files())
        path.write_text("value = 222\n", encoding="utf-8")
        self.path.write_text(self.new_source + "\n", encoding="utf-8")
        self.assertFalse((await self.reload()).isError)
        self.assertEqual(watch.snapshot()["stale"], [module.__name__])

    async def test_subsequent_edit_is_stale_and_second_reload_uses_latest_bytes(self):
        self.path.write_text(self.new_source, encoding="utf-8")
        self.assertFalse((await self.reload()).isError)
        new_runner = adapter.load_runner()
        self.path.write_text(self.new_source.replace('"new"', '"third"'), encoding="utf-8")
        result = await self.run_book()
        self.assertEqual(result.structuredContent["implementation"], "new")
        self.assertEqual(result.meta[freshness.MARKER]["stale"], [MODULE])
        self.assertFalse((await self.reload()).isError)
        self.assertIsNot(adapter.load_runner(), new_runner)
        self.assertEqual((await self.run_book()).structuredContent["implementation"], "third")
        self.assertEqual(self.watch.snapshot()["status"], "fresh")

    async def test_reload_supports_module_level_dataclasses(self):
        self.path.write_text(
            self.new_source + "\nfrom dataclasses import dataclass\n"
            "@dataclass\nclass LoadRecord:\n    value: str = 'new'\n",
            encoding="utf-8",
        )
        self.assertFalse((await self.reload()).isError)
        runner = adapter.load_runner()
        self.assertEqual(runner.LoadRecord().value, "new")
        self.assertIs(sys.modules[runner.LoadRecord.__module__], runner)
        self.assertEqual(self.watch.snapshot()["status"], "fresh")


if __name__ == "__main__":
    unittest.main()
