from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types as python_types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp import types
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import server
from dayz_mcp.server_freshness import (
    MARKER,
    ServerSourceWatch,
    loaded_source_files,
    install_result_freshness,
    source_stale,
)
from tests.test_client_mode import _fixture_client_runtime

_MODULE = "dayz_mcp.dayz_test_tool"
_OLD = '''on_call = None

async def execute_dayz_test_run(client, **kwargs):
    if on_call is not None:
        on_call()
    return {"status": "succeeded", "implementation": "old"}
'''
_NEW = _OLD.replace('"old"', '"new"')


class ServerFreshnessTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "dayz_test_tool.py"
        self.path.write_text(_OLD, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(_MODULE, self.path)
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        # Keep the real exception/constants contract, but load a harmless
        # executor from disk. No launcher, daemon, Steam or game is contacted.
        self.module.__dict__.update({
            key: value for key, value in vars(server.dayz_test_tool).items()
            if not key.startswith("__")
        })
        spec.loader.exec_module(self.module)
        modules_patch = patch.dict(sys.modules, {_MODULE: self.module})
        modules_patch.start()
        self.addCleanup(modules_patch.stop)
        module_patch = patch.object(server, "dayz_test_tool", self.module)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        self.watch = ServerSourceWatch(loaded_source_files())
        watch_patch = patch.object(server, "_SERVER_SOURCES", self.watch)
        watch_patch.start()
        self.addCleanup(watch_patch.stop)
        self.app, self.runtime = self.build()

    def build(self):
        config = server.ServerConfig(
            mode="client", key="fixture-key", port=12345,
            client_platform="codex", auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        runtime.bridge_status_payload = AsyncMock(return_value={
            "daemon_modules": {
                "daemon_started_at": 1.0, "watched_count": 2,
                "stale": [], "unreadable": [],
            }
        })
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, built = server.build_app(config)
        self.assertIs(built, runtime)
        return app, runtime

    async def call(self, name="dayz_test_run", arguments=None, app=None):
        if arguments is None:
            arguments = {"project": "Fixture", "mode": "server"}
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments),
        )
        response = await (app or self.app)._mcp_server.request_handlers[
            types.CallToolRequest
        ](request)
        self.assertIsInstance(response.root, types.CallToolResult)
        # Round-trip the actual protocol envelope, including the _meta alias.
        return types.CallToolResult.model_validate_json(
            response.root.model_dump_json(by_alias=True)
        )

    def marker(self, result):
        self.assertIn(MARKER, result.meta or {})
        marker = result.meta[MARKER]
        text = result.content[-1].text
        self.assertTrue(text.startswith(f"SERVER_CODE_FRESHNESS {marker['status']}: "))
        self.assertIn(f"stale={','.join(marker['stale']) or 'none'};", text)
        for name, reason in {**marker["unreadable_reasons"], **marker["observation_errors"]}.items():
            self.assertIn(f"{name}={reason}", text)
        self.assertIn("scope=tools", text)
        self.assertIn("remediation=reopen_mcp_client", text)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(text)
        self.assertEqual(marker["scope"], "loaded_server_modules")
        self.assertEqual(marker["remediation"], "reopen_mcp_client")
        return marker

    def deny_source_read(self):
        original = Path.read_bytes

        def read(path):
            if path == self.path:
                raise PermissionError("fixture access denied")
            return original(path)

        return patch.object(Path, "read_bytes", read)

    async def test_fresh_response_has_no_marker(self) -> None:
        result = await self.call()
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["implementation"], "old")
        self.assertNotIn(MARKER, result.meta or {})
        self.assertEqual(len(result.content), 1)

    async def test_dayz_run_keeps_old_behavior_and_marks_stale_on_wire(self) -> None:
        self.path.write_text(_NEW, encoding="utf-8")
        for _ in range(2):
            result = await self.call()
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent["implementation"], "old")
            self.assertEqual(json.loads(result.content[0].text), result.structuredContent)
            marker = self.marker(result)
            self.assertEqual(marker["status"], "stale")
            self.assertEqual(marker["stale"], [_MODULE])
            self.assertEqual(marker["unreadable"], [])

    async def test_bridge_status_is_live_but_registry_fingerprint_is_frozen(self) -> None:
        fresh = (await self.call("bridge_status", {})).structuredContent
        self.assertIsNone(fresh["tool_registry_remediation"])
        self.assertNotIsInstance(fresh["tool_registry_remediation"], str)
        self.assertIs(fresh["tool_registry_source_stale"], False)
        modules = fresh["server_modules"]
        self.assertGreater(modules["watched_count"], 1)
        self.assertEqual(modules["stale"], [])
        self.assertEqual(modules["unreadable"], [])
        self.assertEqual(modules["server_pid"], os.getpid())
        self.assertEqual(fresh["daemon_modules"]["watched_count"], 2)
        self.path.write_text(_NEW, encoding="utf-8")
        with patch.object(server, "capture_registry_snapshot") as capture:
            stale = (await self.call("bridge_status", {})).structuredContent
        capture.assert_not_called()
        self.assertIs(stale["tool_registry_source_stale"], True)
        self.assertNotIsInstance(stale["tool_registry_remediation"], str)
        self.assertEqual(stale["tool_registry_remediation"]["code"], "reopen_mcp_client")
        self.assertEqual(stale["tool_registry_remediation"]["scope"], "tools")
        self.assertEqual(stale["server_modules"]["stale"], [_MODULE])
        self.assertEqual(stale["tool_registry_fingerprint"], fresh["tool_registry_fingerprint"])
        self.assertEqual(stale["tool_registry_captured_at"], fresh["tool_registry_captured_at"])
        self.assertEqual(stale["daemon_modules"], fresh["daemon_modules"])

    async def test_rebuilding_app_does_not_reanchor_loaded_code(self) -> None:
        self.path.write_text(_NEW, encoding="utf-8")
        app, _runtime = self.build()
        result = await self.call(app=app)
        self.assertEqual(self.marker(result)["stale"], [_MODULE])

    async def test_identical_content_touch_is_fresh(self) -> None:
        stat = self.path.stat()
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        result = await self.call()
        self.assertNotIn(MARKER, result.meta or {})

    async def test_same_stat_edit_is_the_documented_cache_miss(self) -> None:
        stat = self.path.stat()
        self.path.write_text(_NEW, encoding="utf-8")
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.path.stat().st_size, stat.st_size)
        self.assertEqual(self.path.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(self.path.stat().st_ino, stat.st_ino)
        self.assertNotIn(MARKER, (await self.call()).meta or {})

    async def test_deleted_source_is_unknown_and_call_still_succeeds(self) -> None:
        self.path.unlink()
        result = await self.call()
        self.assertFalse(result.isError)
        marker = self.marker(result)
        self.assertEqual(marker["status"], "unknown")
        self.assertEqual(marker["unreadable"], [_MODULE])
        status = (await self.call("bridge_status", {})).structuredContent
        self.assertIs(status["tool_registry_source_stale"], True)
        self.assertEqual(status["server_modules"]["status"], "unknown")
        self.assertEqual(status["server_modules"]["unreadable_reasons"], {
            _MODULE: "source_unreadable_now",
        })

    async def test_read_denied_after_stat_change_is_unknown_and_recovers(self) -> None:
        self.path.write_text(_NEW + "\n", encoding="utf-8")
        with self.deny_source_read():
            result = await self.call()
        self.assertFalse(result.isError)
        self.assertEqual(self.marker(result)["unreadable"], [_MODULE])
        self.assertEqual(self.marker(await self.call())["stale"], [_MODULE])

    async def test_unreadable_initial_snapshot_never_becomes_false_fresh(self) -> None:
        with self.deny_source_read():
            watch = ServerSourceWatch(loaded_source_files())
        snapshot = watch.snapshot()
        self.assertEqual(snapshot["unreadable_reasons"][_MODULE],
                         "source_unreadable_at_server_snapshot")
        self.assertIs(source_stale(snapshot), True)
        self.assertEqual(snapshot["status"], "unknown")

    async def test_late_import_has_explicit_unknown_reason(self) -> None:
        name = "dayz_mcp.s3_late_fixture"
        module = python_types.ModuleType(name)
        module.__file__ = str(self.path)
        with patch.dict(sys.modules, {name: module}):
            for _ in range(2):
                marker = self.marker(await self.call())
                self.assertEqual(marker["unreadable_reasons"][name],
                                 "loaded_after_server_snapshot")

    async def test_changed_module_origin_is_unknown(self) -> None:
        with patch.object(self.module, "__file__", str(self.path.with_name("other.py"))):
            marker = self.marker(await self.call())
        self.assertEqual(marker["unreadable_reasons"][_MODULE],
                         "loaded_module_path_changed")

    async def test_stale_at_entry_is_retained_if_source_is_restored_in_call(self) -> None:
        self.path.write_text(_NEW, encoding="utf-8")
        self.module.on_call = lambda: self.path.write_text(_OLD, encoding="utf-8")
        self.assertEqual(self.marker(await self.call())["stale"], [_MODULE])
        self.assertNotIn(MARKER, (await self.call()).meta or {})

    async def test_source_edit_during_call_is_marked(self) -> None:
        self.module.on_call = lambda: self.path.write_text(_NEW, encoding="utf-8")
        self.assertEqual(self.marker(await self.call())["stale"], [_MODULE])

    async def test_stale_witness_wins_over_another_unreadable_source(self) -> None:
        self.path.write_text(_NEW, encoding="utf-8")
        module = python_types.ModuleType("dayz_mcp.s3_missing_fixture")
        with patch.dict(sys.modules, {module.__name__: module}):
            result = await self.call("bridge_status", {})
        self.assertIs(result.structuredContent["tool_registry_source_stale"], True)
        marker = self.marker(result)
        self.assertEqual(marker["status"], "stale")
        self.assertIn(module.__name__, marker["unreadable"])

    async def test_error_flag_and_original_error_text_are_preserved(self) -> None:
        @self.app.tool()
        async def s3_error() -> dict:
            raise ToolError("fixture_error")

        fresh = await self.call("s3_error", {})
        self.path.write_text(_NEW, encoding="utf-8")
        stale = await self.call("s3_error", {})
        self.assertTrue(stale.isError)
        self.assertEqual(stale.content[:-1], fresh.content)
        self.assertEqual(self.marker(stale)["status"], "stale")

    async def test_closed_argument_validation_is_preserved_and_marked(self) -> None:
        self.path.write_text(_NEW, encoding="utf-8")
        result = await self.call(arguments={
            "project": "Fixture", "mode": "server", "unexpected": True,
        })
        self.assertTrue(result.isError)
        self.assertIn("unexpected arguments", result.content[0].text)
        self.assertEqual(self.marker(result)["status"], "stale")

    async def test_image_and_existing_content_are_preserved(self) -> None:
        @self.app.tool()
        async def s3_picture():
            return [Image(data=b"fixture-image", format="png"), "fixture metadata"]

        fresh = await self.call("s3_picture", {})
        self.path.write_text(_NEW, encoding="utf-8")
        stale = await self.call("s3_picture", {})
        self.assertFalse(stale.isError)
        self.assertEqual(stale.content[:-1], fresh.content)
        self.assertEqual(stale.content[0].data, base64.b64encode(b"fixture-image").decode())
        self.assertIsNone(stale.structuredContent)
        self.marker(stale)

    async def test_list_result_and_output_schema_are_preserved(self) -> None:
        @self.app.tool()
        async def s3_list() -> list[dict[str, str]]:
            return [{"symbol": "fixture"}]

        fresh = await self.call("s3_list", {})
        self.path.write_text(_NEW, encoding="utf-8")
        stale = await self.call("s3_list", {})
        self.assertFalse(stale.isError)
        self.assertEqual(stale.structuredContent, fresh.structuredContent)
        self.assertEqual(stale.content[:-1], fresh.content)
        self.marker(stale)

    async def test_existing_result_metadata_is_preserved(self) -> None:
        @self.app.tool()
        async def s3_metadata() -> types.CallToolResult:
            return types.CallToolResult(
                _meta={"fixture": "retained"},
                content=[types.TextContent(type="text", text="fixture")],
            )

        self.path.write_text(_NEW, encoding="utf-8")
        result = await self.call("s3_metadata", {})
        self.assertEqual(result.meta["fixture"], "retained")
        self.marker(result)

    async def test_loaded_watch_covers_registered_implementations_and_helpers(self) -> None:
        files = loaded_source_files()
        for name in (
            "dayz_mcp.server", "dayz_mcp.dayz_test_tool", "dayz_mcp.knowledge",
            "dayz_mcp.playbook_tool", "dayz_mcp.ui_dialog",
            "dayz_mcp.server_freshness", "mcp_capture", "dayz_playbook_runner",
            "dayz_mcp.registry_lock",
        ):
            self.assertIn(name, files)
        for tool in self.app._tool_manager.list_tools():
            self.assertIn(tool.fn.__module__, files)

    async def test_empty_watch_is_unknown(self) -> None:
        with patch("dayz_mcp.server_freshness.loaded_source_files", return_value={}):
            snapshot = ServerSourceWatch({}).snapshot()
        self.assertIs(source_stale(snapshot), True)
        self.assertEqual(snapshot["status"], "unknown")


    async def test_named_capture_helper_is_watched_through_a_drive_alias(self) -> None:
        import mcp_capture
        with patch.object(mcp_capture, "__file__", r"Q:\alias\tools\mcp_capture.py"):
            files = loaded_source_files()
        self.assertIn("mcp_capture", files)

    async def test_stale_marker_survives_a_real_mcp_client_session(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session
        self.path.write_text(_NEW, encoding="utf-8")
        async with create_connected_server_and_client_session(self.app._mcp_server) as session:
            result = await session.call_tool(
                "dayz_test_run", {"project": "Fixture", "mode": "server"}
            )
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["implementation"], "old")
        self.assertEqual(self.marker(result)["stale"], [_MODULE])


    async def test_unchanged_calls_do_not_read_source_bytes(self) -> None:
        with patch.object(Path, "read_bytes", autospec=True, side_effect=Path.read_bytes) as reads:
            await self.call()
            await self.call("bridge_status", {})
        reads.assert_not_called()

    async def test_changed_source_is_hashed_once_then_stale_is_cached(self) -> None:
        self.path.write_text(_NEW + "\n", encoding="utf-8")
        with patch.object(Path, "read_bytes", autospec=True, side_effect=Path.read_bytes) as reads:
            for _ in range(2):
                self.assertEqual(self.marker(await self.call())["stale"], [_MODULE])
            status = (await self.call("bridge_status", {})).structuredContent
        reads.assert_called_once_with(self.path)
        self.assertIs(status["tool_registry_source_stale"], True)

    async def test_identical_touch_is_hashed_once_then_fresh_is_cached(self) -> None:
        stat = self.path.stat()
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        with patch.object(Path, "read_bytes", autospec=True, side_effect=Path.read_bytes) as reads:
            for _ in range(2):
                self.assertNotIn(MARKER, (await self.call()).meta or {})
        reads.assert_called_once_with(self.path)

    async def test_file_id_change_detects_same_size_and_mtime_replacement(self) -> None:
        stat = self.path.stat()
        replacement = self.path.with_name("replacement.py")
        replacement.write_text(_NEW, encoding="utf-8")
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        replacement.replace(self.path)
        current = self.path.stat()
        self.assertEqual(current.st_size, stat.st_size)
        self.assertEqual(current.st_mtime_ns, stat.st_mtime_ns)
        self.assertNotEqual(current.st_ino, stat.st_ino)
        self.assertEqual(self.marker(await self.call())["stale"], [_MODULE])

    async def test_same_stat_read_deny_is_the_documented_cache_miss(self) -> None:
        with self.deny_source_read():
            self.assertNotIn(MARKER, (await self.call()).meta or {})

    async def test_cached_playbook_runner_stays_old_and_is_marked_on_wire(self) -> None:
        adapter = server.playbook_tool_mod
        runner_path = self.path.with_name("runner.py")
        source = Path(adapter.load_runner().__file__).read_text(encoding="utf-8")
        source += '\nasync def _async_run(*args):\n    return {"overall": "PASS", "implementation": "old"}\n'
        runner_path.write_text(source, encoding="utf-8")
        runner_path.with_name("fixture.toml").write_text('status = "DRAFT"\n', encoding="utf-8")
        with patch.object(adapter, "PLAYBOOKS_DIR", runner_path.parent), patch.object(
            adapter, "_runner", None
        ), patch.object(adapter, "_runner_source", None), patch.dict(sys.modules):
            loaded = adapter.load_runner()  # Real spec_from_file_location/cache path.
            watch = ServerSourceWatch(loaded_source_files())
            with patch.object(server, "_SERVER_SOURCES", watch):
                app, _ = self.build()
            fresh = await self.call("playbook_run", {"name": "fixture"}, app=app)
            self.assertNotIn(MARKER, fresh.meta or {})
            runner_path.write_text(source.replace('"implementation": "old"', '"implementation": "new"') + "\n", encoding="utf-8")
            for _ in range(2):
                result = await self.call("playbook_run", {"name": "fixture"}, app=app)
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["implementation"], "old")
                self.assertIs(adapter.load_runner(), loaded)
                self.assertEqual(self.marker(result)["stale"], ["dayz_playbook_runner"])

    async def test_named_runner_is_watched_through_a_drive_alias(self) -> None:
        runner = server.playbook_tool_mod.load_runner()
        with patch.object(runner, "__file__", r"Q:\alias\playbooks\runner.py"):
            self.assertIn("dayz_playbook_runner", loaded_source_files())

    async def test_other_playbooks_module_is_censused_with_unknown_for_late_import(self) -> None:
        module = python_types.ModuleType("other_playbook_helper")
        module.__file__ = str(server.playbook_tool_mod.PLAYBOOKS_DIR / "helper.py")
        with patch.dict(sys.modules, {module.__name__: module}):
            self.assertIn(module.__name__, loaded_source_files())
            self.assertEqual(self.marker(await self.call())["unreadable_reasons"][module.__name__],
                             "loaded_after_server_snapshot")

    async def test_fresh_process_preloads_production_import_closure(self) -> None:
        # Fresh interpreter: unittest discovery must not accidentally warm the
        # closure and make this gate pass. Import only; never invoke a launcher.
        code = r"""
import ast, json, os, sys
from pathlib import Path
from dayz_mcp import server
from dayz_mcp.server_freshness import loaded_source_files
before = server._SERVER_SOURCES.snapshot()
expected = {'dayz_playbook_runner', 'dayz_mcp.registry_lock'}
if os.name == 'nt':
    expected.update({'dayz_mcp.native_launcher_backend', 'dayz_mcp.native_bundle',
                     'dayz_mcp.native_child_announcement', 'dayz_mcp.native_debug_state',
                     'dayz_mcp.dayz_tools_paths', 'dayz_mcp.request_path_authority'})
assert expected <= set(loaded_source_files()), sorted(expected - set(loaded_source_files()))
missing = []
for name, path in loaded_source_files().items():
    if not name.startswith('dayz_mcp.') or path is None:
        continue
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
        targets = []
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('dayz_mcp'):
            targets = [node.module] if node.module != 'dayz_mcp' else ['dayz_mcp.' + x.name for x in node.names]
        elif isinstance(node, ast.Import):
            targets = [x.name for x in node.names if x.name.startswith('dayz_mcp')]
        missing.extend((name, node.lineno, target) for target in targets
                       if target not in sys.modules and (os.name == 'nt' or target not in
                           {'dayz_mcp.native_launcher_backend', 'dayz_mcp.native_bundle'}))
assert not missing, missing
server.playbook_tool_mod.load_runner()
after = server._SERVER_SOURCES.snapshot()
assert before['status'] == after['status'] == 'fresh', (before, after)
print(json.dumps({'before': before, 'after': after, 'missing_imports': missing}))
"""
        result = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-B", "-c", code],
            cwd=Path(server.__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["missing_imports"], [])
        self.assertEqual(payload["after"]["status"], "fresh")

    async def test_detached_hook_is_unknown_in_real_bridge_status_payload(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session
        lowlevel = self.app._mcp_server
        # Re-register the SDK's real handler, bypassing our wrapper completely.
        lowlevel.call_tool(validate_input=False)(self.app.call_tool)
        async with create_connected_server_and_client_session(lowlevel) as session:
            result = await session.call_tool("bridge_status", {})
        status = result.structuredContent
        self.assertIs(status["tool_registry_source_stale"], True)
        self.assertEqual(status["server_modules"]["status"], "unknown")
        self.assertEqual(status["server_modules"]["observation_errors"], {
            "call_tool_hook": "call_tool_handler_not_wrapper",
        })
        self.assertEqual(json.loads(result.content[0].text)["server_modules"], status["server_modules"])

    async def test_fossil_handler_dictionary_cannot_report_fresh(self) -> None:
        lowlevel = self.app._mcp_server
        fossil = lowlevel.request_handlers
        wrapper = fossil[types.CallToolRequest]
        lowlevel.request_handlers = dict(fossil)
        lowlevel.call_tool(validate_input=False)(self.app.call_tool)
        self.assertIs(fossil[types.CallToolRequest], wrapper)
        self.path.write_text(_NEW, encoding="utf-8")
        status = (await self.call("bridge_status", {})).structuredContent
        self.assertEqual(status["server_modules"]["stale"], [_MODULE])
        self.assertEqual(status["server_modules"]["status"], "unknown")
        self.assertEqual(status["server_modules"]["observation_errors"]["call_tool_hook"],
                         "call_tool_handler_not_wrapper")

    async def test_equal_but_different_live_handler_is_unknown(self) -> None:
        handlers = self.app._mcp_server.request_handlers
        wrapped = handlers[types.CallToolRequest]

        class EqualHandler:
            def __eq__(self, other):
                return True

            async def __call__(self, request):
                return await wrapped(request)

        handlers[types.CallToolRequest] = EqualHandler()
        marker = self.marker(await self.call())
        self.assertEqual(marker["status"], "unknown")
        self.assertEqual(marker["observation_errors"]["call_tool_hook"], "call_tool_handler_not_wrapper")

    async def test_hook_detached_during_call_is_unknown_at_exit(self) -> None:
        handlers = self.app._mcp_server.request_handlers
        self.module.on_call = lambda: handlers.pop(types.CallToolRequest)
        marker = self.marker(await self.call())
        self.assertEqual(marker["status"], "unknown")
        self.assertIn("call_tool_hook", marker["observation_errors"])

    async def test_replaced_lowlevel_server_is_unknown(self) -> None:
        wrapper = self.app._mcp_server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(
            name="dayz_test_run", arguments={"project": "Fixture", "mode": "server"},
        ))
        with patch.object(self.app, "_mcp_server", FastMCP("replacement")._mcp_server):
            result = (await wrapper(request)).root
        marker = self.marker(result)
        self.assertEqual(marker["status"], "unknown")
        self.assertEqual(marker["observation_errors"]["call_tool_hook"], "mcp_server_replaced")

    async def test_unexpected_result_type_is_visible_unknown_not_omitted(self) -> None:
        app = FastMCP("incompatible-result")
        app._mcp_server.request_handlers[types.CallToolRequest] = AsyncMock(
            return_value=types.ServerResult(types.EmptyResult()),
        )
        observe = install_result_freshness(app, self.watch)
        result = await self.call(app=app)
        self.assertTrue(result.isError)
        marker = self.marker(result)
        self.assertEqual(marker["status"], "unknown")
        self.assertEqual(marker["observation_errors"], {
            "call_tool_result": "unexpected_call_tool_result_type",
        })
        self.assertEqual(observe()["status"], "unknown")

    async def test_apps_sharing_source_baseline_have_independent_canaries(self) -> None:
        app, _ = self.build()
        self.assertNotIn(MARKER, (await self.call(app=app)).meta or {})
        self.assertNotIn(MARKER, (await self.call()).meta or {})


if __name__ == "__main__":
    unittest.main()
