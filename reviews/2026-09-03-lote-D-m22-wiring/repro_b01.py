# -*- coding: utf-8 -*-
"""Codex's B-01 repro, verbatim in intent: drive the PUBLIC tool through FastMCP.

This observes one layer above my own oracle -- the tool as a consumer sees it -- so it is
the arbiter, not a restatement of my gate.
"""
import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import dayz_test_tool, server
from tests.test_client_mode import _fixture_client_runtime
from tests.test_dayz_test_tool import RUN_ID, _Bundle, _Opened, _policy, _sealed
from tests.test_mcp_tools import _content_json


class Repro(unittest.IsolatedAsyncioTestCase):
    async def test_structured_failure_when_steam_evaluator_raises(self):
        config = server.ServerConfig(mode="client", key="k", port=12345,
                                     client_platform="codex", log_sink=lambda _m: None)
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _ = server.build_app(config)
        lifecycle = {"runs": [{"run_id": RUN_ID, "state": "RUNNING_IDLE",
                               "mod": "@ExampleMod", "processes": []}]}
        with patch.object(dayz_test_tool, "_require_idle_session", new=AsyncMock()), \
             patch.object(runtime, "lifecycle_status", new=AsyncMock(return_value=lifecycle)), \
             patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), \
             patch.object(dayz_test_tool.secure_launcher, "load_verified_bundle",
                          return_value=_Bundle(_sealed(_policy()))), \
             patch.object(dayz_test_tool.secure_launcher,
                          "execute_secure_launcher_request", new=AsyncMock()), \
             patch.object(dayz_test_tool, "evaluate_steam_session",
                          side_effect=RuntimeError("probe")):
            raw = await app.call_tool("dayz_test_run", {
                "project": "ExampleMod", "mode": "client",
                "run_id": RUN_ID, "extra_mods": ["@DayZ_MCP"]})
        result = _content_json(raw)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "steam_session_stale")


if __name__ == "__main__":
    unittest.main(verbosity=2)
