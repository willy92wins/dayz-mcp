"""Tool descriptions name the real vehicle_trace / world_spawn preconditions.

fb-20260909-190503-ba0e-B: weak agents skip seated/clear/prepare because the
catalog did not name the bridge errors. Descriptions must track the codes the
bridge already returns; this file fails if those phrases drop out.

Sol1 P1: substring tokens alone are not enough. The checker has to reject
negated polarity ("does not require seated", "attaches wheels") and the
inverted lease cycle (acquire -> run -> release).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._addon_paths import addon_root
from dayz_mcp.server import LEASE_TOOL_LINE, ServerConfig, build_app
from tests.test_client_mode import _fixture_client_runtime
from tests.test_vehicle_trace_contract import _method_body


MOD_SCRIPTS = addon_root() / "scripts"
CAR_SCRIPT = MOD_SCRIPTS / "4_World" / "MCP_CarScript.c"
CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"
SERVER_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPBridge.c"

_TRACE_SEATED_CLAUSE = "mode=start requires the local player seated"
_TRACE_NOT_SEATED_CLAUSE = "otherwise the bridge returns not_seated"
_TRACE_EXISTS_CLAUSE = "already exists returns trace_exists"
_TRACE_CLEAR_CLAUSE = "mode=clear before reuse"
_SPAWN_NO_ATTACH_CLAUSE = "Does not attach wheels, battery, or spark plug"
_SPAWN_PREPARE_CLAUSE = "follow with vehicle_prepare_fixture"
_RUN_RELEASE_FIRST = "Release any held session lease before calling"


def _assert_vehicle_trace_copy(test: unittest.TestCase, description: str) -> None:
    test.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
    test.assertIn(_TRACE_SEATED_CLAUSE, description)
    test.assertNotIn("does not require the local player seated", description)
    test.assertIn(_TRACE_NOT_SEATED_CLAUSE, description)
    test.assertIn(_TRACE_EXISTS_CLAUSE, description)
    test.assertIn(_TRACE_CLEAR_CLAUSE, description)


def _assert_world_spawn_copy(test: unittest.TestCase, description: str) -> None:
    test.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
    test.assertIn(_SPAWN_NO_ATTACH_CLAUSE, description)
    test.assertNotIn("attaches wheels", description)
    test.assertIn(_SPAWN_PREPARE_CLAUSE, description)


def _assert_dayz_test_run_copy(test: unittest.TestCase, description: str) -> None:
    test.assertIn(_RUN_RELEASE_FIRST, description)
    release_at = description.index("session_release")
    run_at = description.index("dayz_test_run")
    acquire_at = description.index("session_acquire_wait")
    test.assertLess(release_at, run_at, description)
    test.assertLess(run_at, acquire_at, description)
    test.assertIn("later mutating tools", description)


def _tool_description(app, name: str) -> str:
    tool = app._tool_manager.get_tool(name)
    return tool.description or ""


class PreconditionDocsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch("dayz_mcp.server.ClientRuntime", return_value=runtime):
            self.app, _runtime = build_app(config)

    def test_vehicle_trace_names_seated_and_clear_before_reuse(self) -> None:
        description = _tool_description(self.app, "vehicle_trace")
        _assert_vehicle_trace_copy(self, description)

        dispatch = _method_body(
            CLIENT_BRIDGE.read_text(encoding="utf-8"),
            "protected bool DispatchVehicleTrace(",
        )
        start = _method_body(
            CAR_SCRIPT.read_text(encoding="utf-8"),
            "static bool Start(CarScript car, string traceId, int sampleHz, int maxSamples)",
        )
        self.assertIn('result.error = "not_seated"', dispatch)
        self.assertIn('args.mode == "clear"', dispatch)
        self.assertIn('s_LastError = "trace_exists"', start)

    def test_world_spawn_does_not_claim_fixture_prep(self) -> None:
        description = _tool_description(self.app, "world_spawn")
        _assert_world_spawn_copy(self, description)

        spawn = _method_body(
            SERVER_BRIDGE.read_text(encoding="utf-8"),
            "protected bool DispatchWorldSpawn(",
        )
        ready = _method_body(
            SERVER_BRIDGE.read_text(encoding="utf-8"),
            "protected bool IsVehicleFixtureReady(",
        )
        self.assertIn("CreateObjectEx", spawn)
        self.assertNotIn("OnDebugSpawn", spawn)
        self.assertIn("WheelCountPresent", ready)
        self.assertIn("GetBattery", ready)
        self.assertIn("SparkPlug", ready)

    def test_dayz_test_run_names_lease_release_run_lease_cycle(self) -> None:
        description = _tool_description(self.app, "dayz_test_run")
        _assert_dayz_test_run_copy(self, description)

    def test_copy_checker_rejects_negated_polarity_and_inverted_cycle(self) -> None:
        with self.assertRaises(AssertionError):
            _assert_vehicle_trace_copy(
                self,
                f"{LEASE_TOOL_LINE} mode=start does not require the local "
                "player seated; otherwise the bridge returns not_seated. "
                "already exists returns trace_exists; call mode=clear before reuse.",
            )
        with self.assertRaises(AssertionError):
            _assert_world_spawn_copy(
                self,
                f"{LEASE_TOOL_LINE} world_spawn attaches wheels, battery, or "
                "spark plug; follow with vehicle_prepare_fixture.",
            )
        with self.assertRaises(AssertionError):
            _assert_dayz_test_run_copy(
                self,
                "Release any held session lease before calling. "
                "Cycle: session_acquire_wait -> dayz_test_run -> session_release "
                "for later mutating tools.",
            )


if __name__ == "__main__":
    unittest.main()
