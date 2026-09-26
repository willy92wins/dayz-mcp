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
from dayz_mcp.server import (
    LEASE_TOOL_LINE,
    _INITIAL_DESCRIPTION_LIMIT,
    ServerConfig,
    build_app,
)
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
_TRACE_STOP_BEFORE_RELEASE = "Call mode=stop before vehicle_release"
_RELEASE_STOP_FIRST = "Call vehicle_trace mode=stop before vehicle_release"
_SPAWN_NO_ATTACH_CLAUSE = "Does not attach wheels, battery, or spark plug"
_SPAWN_PREPARE_CLAUSE = "follow with vehicle_prepare_fixture"
_SPAWN_FLAGS_MASK_CLAUSE = (
    "Allowed non-zero values are the exact pair ECE_CREATEPHYSICS|ECE_TRACE"
)
_SPAWN_KEEPHEIGHT_EXCLUDED = "ECE_KEEPHEIGHT (524288)"
_SPAWN_NOLIFETIME_EXCLUDED = "ECE_NOLIFETIME (4194304)"
_SPAWN_COMBO_EXCLUDED = "flags=4718592"
_SPAWN_BAD_FLAGS_CLAUSE = "return bad_flags"
_RUN_RELEASE_FIRST = "Release any held session lease before calling"
_RUN_ACQUIRE_BEFORE_VERBS = "required before any bridge verb or wait_for"


def _assert_vehicle_trace_copy(test: unittest.TestCase, description: str) -> None:
    test.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
    test.assertIn(_TRACE_SEATED_CLAUSE, description)
    test.assertNotIn("does not require the local player seated", description)
    test.assertIn(_TRACE_NOT_SEATED_CLAUSE, description)
    test.assertIn(_TRACE_EXISTS_CLAUSE, description)
    test.assertIn(_TRACE_CLEAR_CLAUSE, description)
    test.assertIn(_TRACE_STOP_BEFORE_RELEASE, description)


def _assert_world_spawn_copy(test: unittest.TestCase, description: str) -> None:
    test.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
    test.assertIn(_SPAWN_NO_ATTACH_CLAUSE, description)
    test.assertNotIn("attaches wheels", description)
    test.assertIn(_SPAWN_PREPARE_CLAUSE, description)
    test.assertIn(_SPAWN_FLAGS_MASK_CLAUSE, description)
    test.assertIn(_SPAWN_KEEPHEIGHT_EXCLUDED, description)
    test.assertIn(_SPAWN_NOLIFETIME_EXCLUDED, description)
    test.assertIn(_SPAWN_COMBO_EXCLUDED, description)
    test.assertIn(_SPAWN_BAD_FLAGS_CLAUSE, description)
    test.assertNotIn("accepts ECE_KEEPHEIGHT", description)
    test.assertNotIn("accepts ECE_NOLIFETIME", description)


def _assert_dayz_test_run_copy(test: unittest.TestCase, description: str) -> None:
    test.assertIn(_RUN_RELEASE_FIRST, description)
    release_at = description.index("session_release")
    run_at = description.index("dayz_test_run")
    acquire_at = description.index("session_acquire_wait")
    test.assertLess(release_at, run_at, description)
    test.assertLess(run_at, acquire_at, description)
    test.assertIn("later mutating tools", description)
    test.assertIn(_RUN_ACQUIRE_BEFORE_VERBS, description)
    test.assertIn("mode=all plus wait_for(players_at_least, 1)", description)
    test.assertIn("viable night session", description)


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

    def test_vehicle_release_names_stop_then_release_and_abort_dumps(self) -> None:
        description = _tool_description(self.app, "vehicle_release")
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        self.assertIn(_RELEASE_STOP_FIRST, description)
        abort = _method_body(
            CAR_SCRIPT.read_text(encoding="utf-8"),
            "static void Abort(string reason)",
        )
        self.assertIn("Dump(s_TraceId)", abort)
        self.assertLess(abort.index("Dump("), abort.index("ClearState("))

    def test_player_teleport_names_occupant_client_seated_and_one_car_limit(self) -> None:
        description = _tool_description(self.app, "player_teleport")
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        self.assertIn("occupant_client_seated", description)
        self.assertIn("One car per run", description)
        self.assertIn("no get-out", description)
        body = _method_body(
            SERVER_BRIDGE.read_text(encoding="utf-8"),
            "protected bool DispatchPlayerTeleport(",
        )
        self.assertIn('result.error = "occupant_client_seated"', body)
        get_in = _tool_description(self.app, "vehicle_get_in_client")
        self.assertIn("occupant_client_seated", get_in)
        self.assertIn("One car per run", get_in)
        self.assertIn("does not survive the run", description)
        self.assertIn("does not survive the run", get_in)
        delete = _tool_description(self.app, "object_delete")
        self.assertIn("does not survive the run", delete)
        self.assertIn("needs care", delete)

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

    def test_dayz_test_run_cycle_survives_the_initial_catalog_cut(self) -> None:
        # fb-20260925-233943-9ccc: before a lease the catalog keeps only the
        # first _INITIAL_DESCRIPTION_LIMIT characters; the cycle must be there.
        head = _tool_description(self.app, "dayz_test_run")[:_INITIAL_DESCRIPTION_LIMIT]
        for token in ("session_release", "dayz_test_run", "session_acquire_wait"):
            self.assertIn(token, head)
        self.assertLess(head.index("session_release"), head.index("dayz_test_run"), head)
        self.assertLess(head.index("dayz_test_run"), head.index("session_acquire_wait"), head)

    def test_instructions_name_the_dayz_test_run_cycle(self) -> None:
        instructions = self.app.instructions or ""
        self.assertIn("call dayz_test_run without a lease", instructions)
        self.assertIn("then session_acquire_wait", instructions)
        self.assertLess(
            instructions.index("call dayz_test_run without a lease"),
            instructions.index("then session_acquire_wait"),
            instructions,
        )

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
            _assert_world_spawn_copy(
                self,
                f"{LEASE_TOOL_LINE} {_SPAWN_NO_ATTACH_CLAUSE}; "
                f"{_SPAWN_PREPARE_CLAUSE}. accepts ECE_KEEPHEIGHT (524288) "
                "and accepts ECE_NOLIFETIME (4194304).",
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
