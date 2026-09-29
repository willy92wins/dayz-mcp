"""NavMeshGenerator opt-in: sealed request, public admission and launch adapter."""
from __future__ import annotations

import itertools
import json
import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import dayz_test_request, dayz_test_tool, server
from tests.client_helpers import _fixture_client_runtime
from tests.dayz_test_tool_helpers import (
    RUN_ID, _Bundle, _Opened, _Runtime, _policy, _sealed, _terminal,
)


class NavmeshRequestTest(unittest.TestCase):
    def parse(self, **changes):
        policy = _policy()
        doc = {"version": 1, "dev_root": policy.dev_root, "mod": policy.mod,
               "mode": "server", **changes}
        return dayz_test_request.parse_dayz_test_request(
            json.dumps(doc).encode(), policies=(policy,)
        )

    def test_opt_in_and_preflight_roundtrip(self):
        self.assertIs(self.parse().payload["navmesh_data_server"], False)
        for preflight in (False, True):
            parsed = self.parse(navmesh_data_server=True, preflight=preflight)
            self.assertIs(parsed.payload["navmesh_data_server"], True)
            again = dayz_test_request.parse_dayz_test_request(
                parsed.canonical_bytes, policies=(_policy(),)
            )
            self.assertEqual(again.sha256, parsed.sha256)

    def test_flag_is_strict_boolean(self):
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "invalid_dayz_test_request:flag_not_boolean"
            ):
                self.parse(navmesh_data_server=value)

    def test_non_server_and_launchless_requests_rejected(self):
        for changes in (
            {"mode": "all"}, {"mode": "client", "run_id": RUN_ID},
            {"mode": "offline"}, {"build": True, "pack_only": True},
            {"kill": True, "run_id": RUN_ID},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                ValueError, "navmesh_data_server_requires_server_launch"
            ):
                self.parse(navmesh_data_server=True, **changes)

    def test_legacy_canonical_requests_keep_default_source_without_build(self):
        original = self.parse().payload
        fields = ("navmesh_data_server", "auto_remediate_steam",
                  "replace_if_not_polling_since")
        for missing in itertools.product((False, True), repeat=3):
            doc = dict(original)
            for field, omit in zip(fields, missing):
                if omit:
                    doc.pop(field)
            with self.subTest(missing=missing):
                parsed = dayz_test_request.parse_dayz_test_request(
                    json.dumps(doc).encode(), policies=(_policy(),)
                )
                self.assertIs(parsed.payload["navmesh_data_server"], False)
                self.assertEqual(parsed.payload, original)

    def test_option_does_not_open_arbitrary_arguments(self):
        with self.assertRaisesRegex(ValueError, "unknown_key"):
            self.parse(navmesh_data_server=True, extra_args=["-anything"])


class NavmeshPublicTest(unittest.IsolatedAsyncioTestCase):
    def app(self):
        config = server.ServerConfig(mode="client", key="test-key", port=12345,
                                     client_platform="codex", log_sink=lambda _: None)
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _ = server.build_app(config)
        return app, runtime

    async def test_public_schema_and_forwarding(self):
        app, runtime = self.app()
        tool = app._tool_manager.get_tool("dayz_test_run")
        prop = tool.parameters["properties"]["navmesh_data_server"]
        self.assertEqual(prop["type"], "boolean")
        self.assertIs(prop["default"], False)
        self.assertIn("mode=server", prop["description"])
        with patch.object(runtime, "session_status", new=AsyncMock(
            return_value={"self": {"state": "none"}}
        )), patch.object(dayz_test_tool, "execute_dayz_test_run", new=AsyncMock(
            return_value={"status": "succeeded", "run_id": RUN_ID}
        )) as execute:
            for flag in (False, True):
                await app.call_tool("dayz_test_run", {
                    "project": "ExampleMod", "mode": "server",
                    "navmesh_data_server": flag,
                })
                self.assertIs(execute.call_args.kwargs["navmesh_data_server"], flag)

    async def test_invalid_request_cannot_queue_or_take_over(self):
        app, runtime = self.app()
        with patch.object(runtime, "session_status", new=AsyncMock()) as status, \
             patch.object(dayz_test_tool, "execute_dayz_test_run", new=AsyncMock()) as run, \
             patch.object(dayz_test_tool, "execute_dayz_test_stop", new=AsyncMock()) as stop:
            for changes in (
                {"mode": "all"}, {"mode": "client", "run_id": RUN_ID},
                {"mode": "server", "pack_only": True, "build": True},
            ):
                with self.subTest(changes=changes), self.assertRaisesRegex(
                    ToolError, "navmesh_data_server_requires_server_launch"
                ):
                    await app.call_tool("dayz_test_run", {
                        "project": "ExampleMod", "navmesh_data_server": True,
                        "takeover": True, "on_busy": "queue", **changes,
                    })
            status.assert_not_awaited()
            run.assert_not_awaited()
            stop.assert_not_awaited()

    async def test_public_api_rejects_coerced_booleans(self):
        app, runtime = self.app()
        with patch.object(runtime, "session_status", new=AsyncMock()) as status:
            for value in (0, 1, "true", None):
                with self.subTest(value=value), self.assertRaises(ToolError):
                    await app.call_tool("dayz_test_run", {
                        "project": "ExampleMod", "mode": "server",
                        "navmesh_data_server": value,
                    })
            status.assert_not_awaited()

    async def test_adapter_seals_option_without_claiming_generator_ready(self):
        async def launch(raw_request, **kwargs):
            self.assertIs(json.loads(raw_request)["navmesh_data_server"], True)
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"]("stdout", _terminal({
                "cleanup_degraded": False, "error_code": None, "exit_code": 0,
                "ok": True, "run_id": RUN_ID,
            }))
            return 0

        runtime = _Runtime()
        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), \
             patch.object(dayz_test_tool.secure_launcher, "load_verified_bundle",
                          return_value=_Bundle(_sealed(_policy()))), \
             patch.object(dayz_test_tool.secure_launcher, "execute_secure_launcher_request",
                          side_effect=launch):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime, project="ExampleMod", mode="server",
                extra_mods=["@DayZ_MCP"], navmesh_data_server=True,
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(runtime.bridge_calls, 0)
        self.assertIsNone(result["bridge_ready"])


if __name__ == "__main__":
    unittest.main()
