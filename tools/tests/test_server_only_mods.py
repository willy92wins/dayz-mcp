"""Public server-only routing contract; fixtures never start DayZ."""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import dayz_test_tool, native_launcher_transaction, server
from tests.client_helpers import _fixture_client_runtime
from tests.dayz_test_tool_helpers import RUN_ID, _Bundle, _Opened, _Runtime, _policy, _sealed


class ServerOnlyPublicTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_schema_and_forwarding(self) -> None:
        config = server.ServerConfig(mode="client", key="test-key", port=12345,
                                     client_platform="codex", log_sink=lambda _: None)
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _ = server.build_app(config)
        tool = app._tool_manager.get_tool("dayz_test_run")
        prop = tool.parameters["properties"]["server_only_mods"]
        self.assertIn({"type": "array", "items": {"type": "string"}}, prop["anyOf"])
        self.assertIsNone(prop["default"])
        self.assertIn("-serverMod=", prop["description"])
        with patch.object(runtime, "session_status", new=AsyncMock(
            return_value={"self": {"state": "none"}}
        )), patch.object(dayz_test_tool, "execute_dayz_test_run", new=AsyncMock(
            return_value={"status": "succeeded", "run_id": RUN_ID}
        )) as execute:
            for mods in ([], ["@DayZ_MCP", "@Audit"]):
                await app.call_tool("dayz_test_run", {
                    "project": "ExampleMod", "mode": "all", "server_only_mods": mods,
                })
                self.assertEqual(execute.call_args.kwargs["server_only_mods"], mods)

    async def test_preflight_seals_server_only_mods_without_launch(self) -> None:
        requests: list[bytes] = []
        real_build = dayz_test_tool.build_run_request

        def capture(*args, **kwargs):
            result = real_build(*args, **kwargs)
            requests.append(result[0])
            return result

        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), \
             patch.object(dayz_test_tool.secure_launcher, "load_verified_bundle",
                          return_value=_Bundle(_sealed(_policy()))), \
             patch.object(dayz_test_tool, "build_run_request", side_effect=capture), \
             patch.object(dayz_test_tool, "preflight_vpp_request", return_value=
                          native_launcher_transaction.VppPreflightResult(
                              error_code=None, missing=(), warnings=(), hint="fixture")), \
             patch.object(dayz_test_tool.secure_launcher, "execute_secure_launcher_request",
                          new=AsyncMock()) as launch:
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(), project="ExampleMod", mode="server", preflight=True,
                server_only_mods=["@DayZ_MCP", "@Audit"],
            )
        launch.assert_not_awaited()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(requests), 1)
        payload = json.loads(requests[0])
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["server_only_mods"], ["@DayZ_MCP", "@Audit"])
        self.assertEqual(payload["extra_mods"], [])
        self.assertNotIn("extra_mods_defaulted", result)
