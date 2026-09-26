from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import tempfile
import types as python_types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp import types
from mcp.server.fastmcp import Image
from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import server
from dayz_mcp.server_freshness import (
    MARKER,
    ServerSourceWatch,
    loaded_source_files,
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
        self.assertEqual(json.loads(result.content[-1].text), {MARKER: marker})
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

    async def test_same_size_and_mtime_edit_is_stale(self) -> None:
        stat = self.path.stat()
        self.path.write_text(_NEW, encoding="utf-8")
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.path.stat().st_size, stat.st_size)
        self.assertEqual(self.path.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(self.marker(await self.call())["stale"], [_MODULE])

    async def test_deleted_source_is_unknown_and_call_still_succeeds(self) -> None:
        self.path.unlink()
        result = await self.call()
        self.assertFalse(result.isError)
        marker = self.marker(result)
        self.assertEqual(marker["status"], "unknown")
        self.assertEqual(marker["unreadable"], [_MODULE])
        status = (await self.call("bridge_status", {})).structuredContent
        self.assertEqual(status["tool_registry_source_stale"], {
            "status": "unknown", "reason": "unverifiable_server_sources",
            "modules": {_MODULE: "source_unreadable_now"},
        })

    async def test_read_denied_is_unknown_without_failing_tool(self) -> None:
        with self.deny_source_read():
            result = await self.call()
        self.assertFalse(result.isError)
        self.assertEqual(self.marker(result)["unreadable"], [_MODULE])

    async def test_unreadable_initial_snapshot_never_becomes_false_fresh(self) -> None:
        with self.deny_source_read():
            watch = ServerSourceWatch(loaded_source_files())
        snapshot = watch.snapshot()
        self.assertEqual(snapshot["unreadable_reasons"][_MODULE],
                         "source_unreadable_at_server_snapshot")
        self.assertEqual(source_stale(snapshot)["status"], "unknown")

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
            "dayz_mcp.server_freshness", "mcp_capture",
        ):
            self.assertIn(name, files)
        for tool in self.app._tool_manager.list_tools():
            self.assertIn(tool.fn.__module__, files)

    async def test_empty_watch_is_unknown(self) -> None:
        with patch("dayz_mcp.server_freshness.loaded_source_files", return_value={}):
            snapshot = ServerSourceWatch({}).snapshot()
        self.assertEqual(source_stale(snapshot)["status"], "unknown")


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


if __name__ == "__main__":
    unittest.main()
