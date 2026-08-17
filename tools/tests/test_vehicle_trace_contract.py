from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS
from tests._addon_paths import addon_root


MOD_SCRIPTS = addon_root() / "scripts"

CAR_SCRIPT = MOD_SCRIPTS / "4_World" / "MCP_CarScript.c"
MESSAGES = MOD_SCRIPTS / "5_Mission" / "MCPMessages.c"
CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


def _content_json(content: object) -> dict:
    if isinstance(content, tuple):
        _blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = _blocks
    parsed = json.loads(content[0].text)  # type: ignore[index,union-attr]
    if not isinstance(parsed, dict):
        raise AssertionError("expected dict")
    return parsed


class VehicleTraceIngressContractTest(unittest.TestCase):
    def test_command_is_client_only_mutating_and_validated(self) -> None:
        self.assertIn("vehicle_trace", loopback.CLIENT_COMMANDS)
        self.assertNotIn("vehicle_trace", READ_ONLY_COMMANDS)
        self.assertEqual(loopback.peer_for_command("vehicle_trace"), "client")

        state = loopback.ServerState("test-key")
        valid = {
            "mode": "start",
            "trace_id": "a" * 32,
            "cursor": 0,
            "limit": 64,
            "sample_hz": 20,
            "max_samples": 4096,
        }
        status, body = state.enqueue_command("vehicle_trace", valid)
        self.assertEqual((status, body["peer"]), (200, "client"))
        for bad in [
            valid | {"mode": "START"},
            valid | {"trace_id": "A" * 32},
            valid | {"cursor": -1},
            valid | {"limit": 65},
            valid | {"sample_hz": 19},
            valid | {"max_samples": 8193},
            valid | {"extra": 1},
        ]:
            with self.subTest(bad=bad):
                self.assertEqual(
                    state.enqueue_command("vehicle_trace", bad),
                    (400, {"error": "bad_args"}),
                )


class VehicleTraceFastMCPContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_tool_generates_id_and_forwards_exact_args(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn("vehicle_trace", tools)

        response = {
            "ok": 1,
            "trace": {
                "trace_id": "unused",
                "active": 1,
                "complete": 0,
                "overflow": 0,
                "eof": 0,
                "samples": [],
            },
        }
        expected_response = {
            "ok": 1,
            "trace": {
                "trace_id": "unused",
                "active": True,
                "complete": False,
                "overflow": False,
                "eof": False,
                "samples": [],
            },
        }
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value=response)) as call:
            result = _content_json(
                await app.call_tool(
                    "vehicle_trace",
                    {
                        "mode": "start",
                        "sample_hz": 30,
                        "max_samples": 128,
                        "timeout_s": 1.0,
                    },
                )
            )
        self.assertEqual(result, expected_response)
        args = call.await_args.args
        self.assertEqual(args[0], "vehicle_trace")
        self.assertEqual(args[2], "client")
        self.assertRegex(args[1]["trace_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(
            set(args[1]),
            {"mode", "trace_id", "cursor", "limit", "sample_hz", "max_samples"},
        )


class VehicleTraceEnforceSourceContractTest(unittest.TestCase):
    def test_bridge_versions_move_together_to_v7(self) -> None:
        self.assertEqual(server.EXPECTED_BRIDGE_VERSION, "7")
        messages = MESSAGES.read_text(encoding="utf-8")
        self.assertIn('const string MCP_BRIDGE_VERSION = "7";', messages)

    def test_hooks_copy_contact_and_do_not_allocate(self) -> None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("class MCPVehicleTraceSample", source)
        self.assertIn("class MCPVehicleTrace", source)
        on_input = _method_body(source, "override void OnInput(float dt)")
        on_contact = _method_body(source, "override void OnContact(")
        self.assertNotRegex(on_input, r"\bnew\b")
        self.assertNotRegex(on_contact, r"\bnew\b")
        self.assertIn("MCPVehicleTrace.Capture(this, dt);", on_input)
        self.assertIn("super.OnContact(zoneName, localPos, other, data);", on_contact)
        self.assertIn("MCPVehicleTrace.CaptureContact(this, zoneName, localPos, data);", on_contact)
        self.assertNotIn("Contact m_", source)

    def test_sampling_accumulator_preserves_fractional_frame_remainder(self) -> None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")
        capture = _method_body(source, "static void Capture(CarScript car, float dt)")
        self.assertIn("s_AccumS = s_AccumS - intervalS;", capture)

        interval_s = 1.0 / 30.0
        frame_dt_s = 0.025
        accumulator_s = 0.0
        sample_count = 1
        for _ in range(400):
            accumulator_s += frame_dt_s
            if accumulator_s < interval_s:
                continue
            accumulator_s -= interval_s
            sample_count += 1
        effective_hz = (sample_count - 1) / (400 * frame_dt_s)
        self.assertGreaterEqual(effective_hz, 27.0)

    def test_equal_tick_defers_without_consuming_fractional_remainder(self) -> None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")
        capture = _method_body(source, "static void Capture(CarScript car, float dt)")
        decrease_guard = "if (s_Count > 0 && nowS < s_LastSampleS)"
        equal_guard = "if (s_Count > 0 && nowS == s_LastSampleS)"
        subtract = "s_AccumS = s_AccumS - intervalS;"
        capture_now = "CaptureNow(car, false);"

        self.assertIn(decrease_guard, capture)
        self.assertIn(equal_guard, capture)
        self.assertIn('Fail("clock_not_monotonic");', capture)
        self.assertLess(capture.index(decrease_guard), capture.index(equal_guard))
        self.assertLess(capture.index(equal_guard), capture.index(subtract))
        self.assertLess(capture.index(subtract), capture.index(capture_now))

        interval_s = 1.0 / 30.0
        accumulated_s = 0.04

        def clock_step(now_s: float, last_s: float) -> tuple[str, float]:
            if now_s < last_s:
                return "fail", accumulated_s
            if now_s == last_s:
                return "defer", accumulated_s
            return "capture", accumulated_s - interval_s

        self.assertEqual(clock_step(10.0, 10.0), ("defer", accumulated_s))
        self.assertEqual(clock_step(9.99, 10.0), ("fail", accumulated_s))
        self.assertEqual(
            clock_step(10.01, 10.0),
            ("capture", accumulated_s - interval_s),
        )

    def test_dispatch_and_all_cleanup_paths_exist(self) -> None:
        messages = MESSAGES.read_text(encoding="utf-8")
        bridge = CLIENT_BRIDGE.read_text(encoding="utf-8")
        self.assertIn("ref MCPVehicleTraceRead trace;", messages)
        self.assertIn('else if (command.cmd == "vehicle_trace")', bridge)
        self.assertIn("DispatchVehicleTrace(command, result)", bridge)
        dispatch = _method_body(bridge, "protected bool DispatchVehicleTrace(")
        self.assertIn("GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER", dispatch)
        self.assertIn("!car.IsOwner()", dispatch)
        release = _method_body(bridge, "protected bool DispatchVehicleRelease(")
        self.assertIn('MCPVehicleTrace.Abort("vehicle_release");', release)
        shutdown = _method_body(bridge, "void Shutdown()")
        self.assertIn('MCPVehicleTrace.Abort("shutdown");', shutdown)


if __name__ == "__main__":
    unittest.main()
