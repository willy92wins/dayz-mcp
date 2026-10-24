"""server_adminlog: sealed request, public admission, and .adm discovery."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import dayz_test_request, launch_logs, log_tail, server
from tests.client_helpers import _fixture_client_runtime
from tests.dayz_test_tool_helpers import RUN_ID, _policy


class AdminlogRequestTest(unittest.TestCase):
    def parse(self, **changes):
        policy = _policy()
        doc = {
            "version": 1,
            "dev_root": policy.dev_root,
            "mod": policy.mod,
            "mode": "server",
            **changes,
        }
        return dayz_test_request.parse_dayz_test_request(
            json.dumps(doc).encode(), policies=(policy,)
        )

    def test_default_false_and_server_and_all_roundtrip(self):
        self.assertIs(self.parse().payload["server_adminlog"], False)
        for mode in ("server", "all"):
            parsed = self.parse(mode=mode, server_adminlog=True)
            self.assertIs(parsed.payload["server_adminlog"], True)
            self.assertEqual(parsed.payload["mode"], mode)
            again = dayz_test_request.parse_dayz_test_request(
                parsed.canonical_bytes, policies=(_policy(),)
            )
            self.assertEqual(again.sha256, parsed.sha256)

    def test_client_and_offline_rejected(self):
        for changes in (
            {"mode": "client", "run_id": RUN_ID},
            {"mode": "offline"},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                ValueError, "server_adminlog_requires_server"
            ):
                self.parse(server_adminlog=True, **changes)

    def test_omitted_field_stays_canonical(self):
        original = self.parse().payload
        doc = dict(original)
        doc.pop("server_adminlog")
        parsed = dayz_test_request.parse_dayz_test_request(
            json.dumps(doc).encode(), policies=(_policy(),)
        )
        self.assertIs(parsed.payload["server_adminlog"], False)
        self.assertEqual(parsed.payload, original)

    def test_flag_is_strict_boolean(self):
        for value in (None, 0, 1, "true"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "flag_not_boolean"
            ):
                self.parse(server_adminlog=value)


class AdminlogPublicTest(unittest.IsolatedAsyncioTestCase):
    def app(self):
        config = server.ServerConfig(
            mode="client",
            key="test-key",
            port=12345,
            client_platform="codex",
            log_sink=lambda _: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _ = server.build_app(config)
        return app, runtime

    async def test_public_schema_rejects_client_before_queue(self):
        app, runtime = self.app()
        tool = app._tool_manager.get_tool("dayz_test_run")
        prop = tool.parameters["properties"]["server_adminlog"]
        self.assertEqual(prop["type"], "boolean")
        self.assertIs(prop["default"], False)
        with patch.object(runtime, "session_status", new=AsyncMock()) as status:
            with self.assertRaisesRegex(ToolError, "server_adminlog_requires_server"):
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "client",
                        "run_id": RUN_ID,
                        "server_adminlog": True,
                    },
                )
            status.assert_not_awaited()


class AdminLogDiscoveryTest(unittest.TestCase):
    def test_any_adm_name_is_a_log_and_a_missing_one_is_not_listed(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            rpt = directory / "DayZDiag_x64_2026-10-24_21-00-00.RPT"
            script = directory / "script_2026-10-24_21-00-00.log"
            adm = directory / "anything_the_game_picks.ADM"
            for path in (rpt, script):
                path.write_bytes(b"script line\n")
            bare = launch_logs._current_launch_logs(str(directory), None)
            self.assertEqual(
                sorted(Path(item).suffix.casefold() for item in bare),
                [".log", ".rpt"],
            )
            adm.write_bytes(b"AdminLog: [X] probe\n")
            # Touch order: adm is newest.
            now = adm.stat().st_mtime
            os.utime(rpt, (now - 5, now - 5))
            os.utime(script, (now - 4, now - 4))
            kept = launch_logs._current_launch_logs(str(directory), None)
            self.assertIn(str(adm), kept)
            resolved = log_tail.resolve_log_files(str(directory))
            self.assertIn(str(adm), resolved)
            read = log_tail.read_since(str(adm), None)
            self.assertEqual(read["lines"], ["AdminLog: [X] probe"])

    def test_floor_drops_an_adm_from_before_the_launch(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            start = 1_700_000_000.0
            old = directory / "previous.adm"
            old.write_bytes(b"old\n")
            os.utime(old, (start - 3600.0, start - 3600.0))
            kept = launch_logs._current_launch_logs(str(directory), start)
            self.assertNotIn(str(old), kept)
