"""fb-20260909-190200-546d: vehicle_trace mode=dump JSONL.

Fixtures from docs/plan-546d.md (EXACT).
"""

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server, vehicle_trace
from tests._addon_paths import addon_root
from tests.test_vehicle_trace import _positive_trace
from tests.test_vehicle_trace_contract import _content_json, _method_body


TID = "0123456789abcdef0123456789abcdef"
DUMP_ARGS = {
    "mode": "dump",
    "trace_id": TID,
    "cursor": 0,
    "limit": 64,
    "sample_hz": 20,
    "max_samples": 8192,
}

MOD_SCRIPTS = addon_root() / "scripts"
CAR_SCRIPT = MOD_SCRIPTS / "4_World" / "MCP_CarScript.c"
CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"


def _dump_header(rows: int) -> dict[str, object]:
    return {
        "schema": vehicle_trace.TRACE_SCHEMA,
        "mode": "dump",
        "trace_id": TID,
        "path": vehicle_trace.dump_profile_path(TID),
        "rows": rows,
        "count": rows,
        "active": 0,
        "complete": 1,
        "overflow": 0,
        "eof": 1,
        "sample_hz": 20,
        "capacity": 64,
        "start_monotonic_s": 100.0,
        "owner_identity": "fixture-owner",
        "car_type": "CivilianSedan",
        "net_id_low": 123,
        "net_id_high": 456,
        "cursor": 0,
        "next_cursor": rows,
        "stop_reason": "requested",
        "samples": [],
    }


def _write_jsonl(path: Path, header: dict[str, object], samples: list[dict]) -> None:
    lines = [json.dumps(header, separators=(",", ":"))]
    for sample in samples:
        lines.append(json.dumps(sample, separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class DumpRequestTests(unittest.TestCase):
    def test_p1_dump_is_a_trace_mode(self) -> None:
        self.assertIn("dump", vehicle_trace.TRACE_MODES)
        got = vehicle_trace.normalize_request("dump", TID, 0, 64, 20, 4096)
        self.assertEqual(got["mode"], "dump")
        self.assertEqual(got["trace_id"], TID)

    def test_n1_wrong_case_still_bad_mode(self) -> None:
        with self.assertRaises(ValueError) as caught:
            vehicle_trace.normalize_request("DUMP", TID, 0, 64, 20, 4096)
        self.assertEqual(str(caught.exception), "bad_mode")

    def test_n3_dump_requires_trace_id(self) -> None:
        with self.assertRaises(ValueError) as caught:
            vehicle_trace.normalize_request("dump", "", 0, 64, 20, 4096)
        self.assertEqual(str(caught.exception), "bad_trace_id")

    def test_p2_ingress_accepts_dump(self) -> None:
        ok, error = loopback.validate_command_args("vehicle_trace", DUMP_ARGS)
        self.assertEqual((ok, error), (True, None))

    def test_n2_caller_path_is_bad_args(self) -> None:
        ok, error = loopback.validate_command_args(
            "vehicle_trace", DUMP_ARGS | {"path": r"C:\Windows\x.jsonl"}
        )
        self.assertEqual((ok, error), (False, "bad_args"))


class DumpJsonlTests(unittest.TestCase):
    def test_p3_load_dump_jsonl_two_samples(self) -> None:
        source = _positive_trace(sample_count=2)
        samples = source["samples"]
        self.assertEqual(len(samples), 2)
        with tempfile.TemporaryDirectory() as raw_temp:
            path = Path(raw_temp) / "dump.jsonl"
            _write_jsonl(path, _dump_header(2), samples)
            loaded = vehicle_trace.load_dump_jsonl(path)
        self.assertEqual(loaded["rows"], 2)
        self.assertEqual(loaded["schema"], vehicle_trace.TRACE_SCHEMA)
        trace = loaded["trace"]
        self.assertIsInstance(trace, dict)
        self.assertEqual(trace["mode"], "read")
        self.assertEqual(len(trace["samples"]), 2)
        report = vehicle_trace.validate_trace(trace)
        self.assertFalse(
            any(
                item.get("id") in {"schema", "mode"} and item.get("status") == "STOP"
                for item in report["checks"]
            ),
            report,
        )

    def test_n4_count_mismatch(self) -> None:
        source = _positive_trace(sample_count=2)
        with tempfile.TemporaryDirectory() as raw_temp:
            path = Path(raw_temp) / "dump.jsonl"
            _write_jsonl(path, _dump_header(2), source["samples"][:1])
            with self.assertRaises(ValueError) as caught:
                vehicle_trace.load_dump_jsonl(path)
        self.assertEqual(str(caught.exception), "dump_count_mismatch")


class DumpBridgeResultTests(unittest.TestCase):
    def test_p4_normalizes_dump_wire(self) -> None:
        path = vehicle_trace.dump_profile_path(TID)
        result = vehicle_trace.normalize_bridge_result(
            {
                "ok": 1,
                "trace": {
                    "schema": vehicle_trace.TRACE_SCHEMA,
                    "mode": "dump",
                    "trace_id": TID,
                    "path": path,
                    "rows": 3,
                    "active": 1,
                    "complete": 0,
                    "overflow": 0,
                    "eof": 0,
                    "samples": [],
                },
            }
        )
        trace = result["trace"]
        self.assertIsInstance(trace, dict)
        self.assertEqual(trace["path"], path)
        self.assertEqual(trace["rows"], 3)
        self.assertTrue(trace["active"])
        self.assertFalse(trace["complete"])

    def test_n6_rejects_noncanonical_path_and_rows(self) -> None:
        base = {
            "ok": 1,
            "trace": {
                "mode": "dump",
                "trace_id": TID,
                "path": "$profile:../secret.jsonl",
                "rows": 1,
                "active": 0,
                "complete": 1,
                "overflow": 0,
                "eof": 1,
                "samples": [],
            },
        }
        with self.assertRaises(ValueError) as caught:
            vehicle_trace.normalize_bridge_result(base)
        self.assertEqual(str(caught.exception), "bad_bridge_trace_dump")
        base["trace"]["path"] = vehicle_trace.dump_profile_path(TID)
        base["trace"]["rows"] = -1
        with self.assertRaises(ValueError) as caught:
            vehicle_trace.normalize_bridge_result(base)
        self.assertEqual(str(caught.exception), "bad_bridge_trace_dump")


class DumpToolOnceTests(unittest.IsolatedAsyncioTestCase):
    async def test_p4_tool_one_bridge_call(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        path = vehicle_trace.dump_profile_path(TID)
        response = {
            "ok": 1,
            "trace": {
                "schema": vehicle_trace.TRACE_SCHEMA,
                "mode": "dump",
                "trace_id": TID,
                "path": path,
                "rows": 3,
                "active": 1,
                "complete": 0,
                "overflow": 0,
                "eof": 0,
                "samples": [],
            },
        }
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value=response)) as call:
            result = _content_json(
                await app.call_tool(
                    "vehicle_trace",
                    {
                        "mode": "dump",
                        "trace_id": TID,
                        "timeout_s": 1.0,
                    },
                )
            )
        self.assertEqual(call.await_count, 1)
        self.assertEqual(call.await_args.args[1]["mode"], "dump")
        self.assertEqual(result["trace"]["path"], path)
        self.assertEqual(result["trace"]["rows"], 3)

    def test_n5_vehicle_trace_source_has_one_call_bridge(self) -> None:
        source = Path(inspect.getfile(server)).read_text(encoding="utf-8")
        start = source.index("async def vehicle_trace(")
        end = source.index("async def vehicle_release(")
        self.assertEqual(source[start:end].count("call_bridge"), 1)


class DumpSourceContractTests(unittest.TestCase):
    def test_p5_enforce_dump_writer(self) -> None:
        car = CAR_SCRIPT.read_text(encoding="utf-8")
        bridge = CLIENT_BRIDGE.read_text(encoding="utf-8")
        dispatch = _method_body(bridge, "protected bool DispatchVehicleTrace(")
        stop = _method_body(car, "static bool Stop(string traceId)")
        dump = _method_body(car, "static bool Dump(string traceId)")
        self.assertIn('args.mode != "dump"', dispatch)
        self.assertIn("MCPVehicleTrace.Dump(", dispatch)
        self.assertIn("Dump(traceId)", stop)
        self.assertIn("$profile:dayz_mcp_trace_", dump)
        self.assertIn("FileMode.WRITE", dump)
        self.assertIn("WriteToString(header, false, line)", dump)
        self.assertNotIn("args.path", dump)


if __name__ == "__main__":
    unittest.main()
