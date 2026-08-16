from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from vehicle_trace_artifact import main as artifact_cli_main

try:
    from dayz_mcp import vehicle_trace
except ImportError as exc:
    vehicle_trace = None
    VEHICLE_TRACE_IMPORT_ERROR: Exception | None = exc
else:
    VEHICLE_TRACE_IMPORT_ERROR = None


TOOLS_DIR = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "vehicle_trace"
SCHEMA_PATH = TOOLS_DIR / "schemas" / "vehicle-trace-v1.json"
COURSE_PATH = TOOLS_DIR / "fixtures" / "vehicle-trace-civilian-sedan-control-v1.json"


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object fixture: {path}")
    return value


def _wire_result() -> dict:
    sample = {field: 1 for field in vehicle_trace._BOOL_SAMPLE_FIELDS}
    sample["forced"] = 0
    sample["is_authority_owner"] = 0
    sample["wheel_loss_event"] = 0
    return {
        "ok": 1,
        "trace": {
            "active": 0,
            "complete": 1,
            "overflow": 0,
            "eof": 1,
            "count": 7,
            "samples": [sample],
        },
    }


def _positive_trace(
    *,
    period_s: float | None = None,
    sample_count: int | None = None,
    sample_hz: int | None = None,
) -> dict:
    fixture = _load_json(FIXTURE_DIR / "positive_20hz.json")
    trace = copy.deepcopy(fixture["trace"])
    defaults = fixture["sample_defaults"]
    count = int(sample_count if sample_count is not None else fixture["sample_count"])
    period = float(period_s if period_s is not None else fixture["sample_period_s"])
    overrides = fixture["overrides"]
    samples = []
    for index in range(count):
        sample = copy.deepcopy(defaults)
        sample["sequence"] = index
        sample["monotonic_s"] = round(float(trace["start_monotonic_s"]) + index * period, 6)
        sample["sample_dt_s"] = 0.0 if index == 0 else period
        sample["position_z"] = round(float(sample["position_z"]) + index * 0.5, 6)
        sample.update(copy.deepcopy(overrides.get(str(index), {})))
        samples.append(sample)
    trace["samples"] = samples
    trace["count"] = count
    trace["capacity"] = max(int(trace["capacity"]), count)
    trace["next_cursor"] = count
    if sample_hz is not None:
        trace["sample_hz"] = sample_hz
    return trace


def _course_trace(*, sample_count: int = 61) -> dict:
    trace = _positive_trace(
        period_s=1.0 / 20.0,
        sample_count=sample_count,
        sample_hz=20,
    )
    course = _load_json(COURSE_PATH)
    controls = course["controls"]
    first_time = float(trace["samples"][0]["monotonic_s"])
    for sample in trace["samples"]:
        elapsed = float(sample["monotonic_s"]) - first_time
        active_control = controls[0]
        for control in controls:
            if float(control["at_s"]) <= elapsed + 1e-6:
                active_control = control
        for channel in ("throttle", "steer", "brake", "handbrake"):
            sample[f"{channel}_requested"] = active_control[channel]
            sample[f"{channel}_applied"] = active_control[channel]
    return trace


def _set_path(root: object, path: list[object], value: object, *, delete: bool = False) -> None:
    current = root
    for component in path[:-1]:
        current = current[component]  # type: ignore[index]
    final = path[-1]
    if delete:
        del current[final]  # type: ignore[index]
    else:
        current[final] = value  # type: ignore[index]


class VehicleTracePresenceTest(unittest.TestCase):
    def test_feature_module_exists(self) -> None:
        self.assertIsNone(VEHICLE_TRACE_IMPORT_ERROR, str(VEHICLE_TRACE_IMPORT_ERROR))
        self.assertIsNotNone(vehicle_trace)


@unittest.skipIf(vehicle_trace is None, "vehicle_trace module not implemented yet")
class VehicleTraceValidationTest(unittest.TestCase):
    def test_bridge_zero_one_booleans_are_exact_and_fail_closed(self) -> None:
        result = vehicle_trace.normalize_bridge_result(_wire_result())
        trace = result["trace"]
        self.assertIs(trace["active"], False)
        self.assertIs(trace["complete"], True)
        self.assertIs(trace["overflow"], False)
        self.assertIs(trace["eof"], True)
        self.assertEqual(trace["count"], 7)
        for field in vehicle_trace._BOOL_SAMPLE_FIELDS:
            self.assertIsInstance(trace["samples"][0][field], bool)

        for path, value in [
            (("trace", "complete"), 2),
            (("trace", "eof"), "1"),
            (("sample", "is_owner"), -1),
            (("sample", "control_active"), None),
        ]:
            broken = _wire_result()
            if path[0] == "trace":
                broken["trace"][path[1]] = value
            else:
                broken["trace"]["samples"][0][path[1]] = value
            with self.subTest(path=path, value=value):
                with self.assertRaisesRegex(ValueError, "bad_bridge_trace_boolean"):
                    vehicle_trace.normalize_bridge_result(broken)

    def test_live_course_uses_20hz_and_generic_30hz_gate_stays_strict(self) -> None:
        course = _load_json(COURSE_PATH)
        self.assertEqual(course["requested_sample_hz"], 20)

        trace = _positive_trace(
            period_s=1.0 / 22.5,
            sample_count=64,
            sample_hz=30,
        )
        strict_30 = vehicle_trace.validate_trace(trace)
        strict_stop_ids = {
            check["id"] for check in strict_30["checks"] if check["status"] == "STOP"
        }
        self.assertIn("effective_hz", strict_stop_ids)

        trace["sample_hz"] = 20
        self.assertEqual(vehicle_trace.validate_trace(trace)["status"], "PASS")

    def test_positive_20hz_control_passes_and_derives_raw_events(self) -> None:
        result = vehicle_trace.validate_trace(_positive_trace())
        self.assertEqual(result["status"], "PASS", result)
        self.assertTrue(result["derived"]["grounding"])
        self.assertEqual(result["derived"]["wheel_contact_false_pulses_s"]["0"], [0.1])
        self.assertFalse(result["derived"]["spinout"])
        self.assertFalse(result["derived"]["rollover"])
        self.assertGreaterEqual(result["derived"]["effective_hz"], 20.0)

    def test_sustained_subthreshold_dt_bias_fails_aggregate_balance(self) -> None:
        trace = _positive_trace()
        template = copy.deepcopy(trace["samples"][0])
        trace["capacity"] = 256
        trace["count"] = 201
        trace["samples"] = []
        for index in range(201):
            sample = copy.deepcopy(template)
            sample["sequence"] = index
            sample["monotonic_s"] = round(
                float(trace["start_monotonic_s"]) + index * 0.05,
                6,
            )
            sample["sample_dt_s"] = 0.0 if index == 0 else 0.0509
            sample["position_z"] = round(7500.0 + index * 0.5, 6)
            trace["samples"].append(sample)

        result = vehicle_trace.validate_trace(trace)

        self.assertEqual(result["status"], "STOP", result)
        self.assertTrue(
            any(check["id"] == "time_balance" for check in result["checks"]),
            result,
        )

    def test_temporal_epsilon_accepts_inside_dt_edge_and_rejects_outside(self) -> None:
        inside = _positive_trace()
        inside["samples"][4]["sample_dt_s"] = 0.0510005
        self.assertEqual(vehicle_trace.validate_trace(inside)["status"], "PASS")

        outside = _positive_trace()
        outside["samples"][4]["sample_dt_s"] = 0.051002
        self.assertEqual(vehicle_trace.validate_trace(outside)["status"], "STOP")

    def test_temporal_epsilon_accepts_inside_max_gap_and_rejects_outside(self) -> None:
        def trace_with_gap(gap_s: float) -> dict:
            trace = _positive_trace()
            template = copy.deepcopy(trace["samples"][0])
            trace["capacity"] = 256
            trace["count"] = 201
            trace["next_cursor"] = 201
            trace["samples"] = []
            for index in range(201):
                sample = copy.deepcopy(template)
                sample["sequence"] = index
                sample["monotonic_s"] = round(
                    float(trace["start_monotonic_s"]) + index * 0.05,
                    7,
                )
                sample["sample_dt_s"] = 0.0 if index == 0 else 0.05
                sample["position_z"] = round(7500.0 + index * 0.5, 6)
                trace["samples"].append(sample)
            trace["samples"][100]["monotonic_s"] = round(
                trace["samples"][99]["monotonic_s"] + gap_s,
                7,
            )
            trace["samples"][100]["sample_dt_s"] = gap_s
            next_gap = (
                trace["samples"][101]["monotonic_s"]
                - trace["samples"][100]["monotonic_s"]
            )
            trace["samples"][101]["sample_dt_s"] = next_gap
            return trace

        self.assertEqual(
            vehicle_trace.validate_trace(trace_with_gap(0.0750005))["status"],
            "PASS",
        )
        self.assertEqual(
            vehicle_trace.validate_trace(trace_with_gap(0.075002))["status"],
            "STOP",
        )

    def test_every_required_trace_and_sample_field_is_fail_closed(self) -> None:
        schema = _load_json(SCHEMA_PATH)
        for field in schema["required"]:
            broken = _positive_trace()
            del broken[field]
            with self.subTest(kind="trace", field=field):
                self.assertEqual(vehicle_trace.validate_trace(broken)["status"], "STOP")
        for field in schema["$defs"]["sample"]["required"]:
            broken = _positive_trace()
            del broken["samples"][3][field]
            with self.subTest(kind="sample", field=field):
                self.assertEqual(vehicle_trace.validate_trace(broken)["status"], "STOP")

    def test_named_negative_mutations_are_not_false_green(self) -> None:
        fixture = _load_json(FIXTURE_DIR / "negative_mutations.json")
        for mutation in fixture["mutations"]:
            trace = _positive_trace(
                period_s=float(mutation["value"])
                if mutation["kind"] == "set_period"
                else None
            )
            if mutation["kind"] != "set_period":
                _set_path(
                    trace,
                    mutation["path"],
                    mutation.get("value"),
                    delete=mutation["kind"] == "delete",
                )
            with self.subTest(mutation=mutation["id"]):
                self.assertEqual(
                    vehicle_trace.validate_trace(trace)["status"],
                    mutation["expected_status"],
                )

    def test_invalid_sample_types_stop_without_raising(self) -> None:
        schema = _load_json(SCHEMA_PATH)
        properties = schema["$defs"]["sample"]["properties"]
        invalid_by_type = {
            "boolean": 1,
            "integer": "invalid",
            "number": "invalid",
            "string": 1,
        }
        for field, contract in properties.items():
            broken = _positive_trace()
            broken["samples"][3][field] = invalid_by_type[contract["type"]]
            with self.subTest(field=field):
                result = vehicle_trace.validate_trace(broken)
                self.assertEqual(result["status"], "STOP", result)

    def test_request_contract_generates_uuid_and_rejects_near_matches(self) -> None:
        start = vehicle_trace.normalize_request(
            "start", "", 0, 64, 20, 4096
        )
        self.assertRegex(start["trace_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(start["mode"], "start")
        for args in [
            ("START", "", 0, 64, 20, 4096),
            ("read", "", 0, 64, 20, 4096),
            ("read", "A" * 32, 0, 64, 20, 4096),
            ("read", "a" * 32, -1, 64, 20, 4096),
            ("read", "a" * 32, 0, 65, 20, 4096),
            ("start", "", 0, 64, 19, 4096),
            ("start", "", 0, 64, 20, 8193),
        ]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    vehicle_trace.normalize_request(*args)

    def test_artifact_hashes_inputs_and_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            trace_path = temp / "trace.json"
            schema_path = temp / "schema.json"
            course_path = temp / "course.json"
            pbo_path = temp / "DayZ_MCP.pbo"
            rpt_path = temp / "client.RPT"
            script_log_path = temp / "script.log"
            lifecycle_path = temp / "lifecycle.json"

            trace_path.write_text(
                json.dumps(_course_trace(), sort_keys=True),
                encoding="utf-8",
            )
            schema_path.write_bytes(SCHEMA_PATH.read_bytes())
            course_path.write_bytes(COURSE_PATH.read_bytes())
            pbo_path.write_bytes(b"PACKONLY-CONTROL")
            rpt_path.write_text("clean rpt\n", encoding="utf-8")
            script_log_path.write_text("clean script log\n", encoding="utf-8")
            lifecycle_path.write_text(
                json.dumps(
                    {
                        "run_id": "run-control-1",
                        "processes": [
                            {
                                "pid": 101,
                                "creation_time_utc": "2026-07-25T00:00:00Z",
                                "executable_sha256": "1" * 64,
                                "command_line_sha256": "2" * 64,
                                "role": "server",
                                "identity_scheme": "h11-v1"
                            },
                            {
                                "pid": 102,
                                "creation_time_utc": "2026-07-25T00:00:01Z",
                                "executable_sha256": "3" * 64,
                                "command_line_sha256": "4" * 64,
                                "role": "client",
                                "identity_scheme": "h11-v1"
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            kwargs = {
                "trace_path": trace_path,
                "schema_path": schema_path,
                "course_path": course_path,
                "pbo_path": pbo_path,
                "rpt_path": rpt_path,
                "script_log_path": script_log_path,
                "lifecycle_path": lifecycle_path,
            }
            first = vehicle_trace.build_artifact(**kwargs)
            second = vehicle_trace.build_artifact(**kwargs)
            first_bytes = vehicle_trace.canonical_artifact_bytes(first)
            second_bytes = vehicle_trace.canonical_artifact_bytes(second)
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(first["status"], "PASS")
            self.assertTrue(
                all(
                    set(check)
                    == {"id", "status", "measured", "expected", "evidence"}
                    for check in first["checks"]
                ),
                first["checks"],
            )
            self.assertEqual(
                first["hashes"]["pbo_sha256"],
                hashlib.sha256(pbo_path.read_bytes()).hexdigest(),
            )
            body = dict(first)
            artifact_sha = body.pop("artifact_sha256")
            self.assertEqual(
                artifact_sha,
                hashlib.sha256(vehicle_trace.canonical_json_bytes(body)).hexdigest(),
            )

            short_trace = _course_trace(sample_count=31)
            trace_path.write_text(json.dumps(short_trace, sort_keys=True), encoding="utf-8")
            short = vehicle_trace.build_artifact(**kwargs)
            self.assertEqual(short["status"], "STOP", short)
            self.assertTrue(
                any(check["id"] == "course_minimum_duration_s" for check in short["checks"]),
                short,
            )

            long_trace = _course_trace()
            trace_path.write_text(json.dumps(long_trace, sort_keys=True), encoding="utf-8")
            course = _load_json(COURSE_PATH)
            for field, value, check_id in [
                ("course_id", "wrong-course", "course_id"),
                ("vehicle_type", "Hatchback_02", "course_vehicle_type"),
                ("requested_sample_hz", 30, "course_requested_sample_hz"),
            ]:
                broken_course = copy.deepcopy(course)
                broken_course[field] = value
                course_path.write_text(
                    json.dumps(broken_course, sort_keys=True),
                    encoding="utf-8",
                )
                with self.subTest(course_field=field):
                    result = vehicle_trace.build_artifact(**kwargs)
                    self.assertEqual(result["status"], "STOP", result)
                    self.assertTrue(
                        any(check["id"] == check_id for check in result["checks"]),
                        result,
                    )

            broken_course = copy.deepcopy(course)
            broken_course["controls"] = [{"at_s": 2.0}]
            course_path.write_text(
                json.dumps(broken_course, sort_keys=True),
                encoding="utf-8",
            )
            malformed_controls = vehicle_trace.build_artifact(**kwargs)
            self.assertEqual(malformed_controls["status"], "STOP", malformed_controls)
            self.assertTrue(
                any(check["id"] == "course_controls" for check in malformed_controls["checks"]),
                malformed_controls,
            )

            substituted_course = copy.deepcopy(course)
            for control in substituted_course["controls"]:
                for channel in ("throttle", "steer", "brake", "handbrake"):
                    control[channel] = 0.0
            course_path.write_text(
                json.dumps(substituted_course, sort_keys=True),
                encoding="utf-8",
            )
            substituted_trace = copy.deepcopy(long_trace)
            for sample in substituted_trace["samples"]:
                for channel in ("throttle", "steer", "brake", "handbrake"):
                    sample[f"{channel}_requested"] = 0.0
                    sample[f"{channel}_applied"] = 0.0
            trace_path.write_text(
                json.dumps(substituted_trace, sort_keys=True),
                encoding="utf-8",
            )
            substituted_schedule = vehicle_trace.build_artifact(**kwargs)
            self.assertEqual(substituted_schedule["status"], "STOP", substituted_schedule)
            self.assertTrue(
                any(
                    check["id"] == "course_controls_canonical"
                    and check["status"] == "STOP"
                    for check in substituted_schedule["checks"]
                ),
                substituted_schedule,
            )

            course_path.write_bytes(COURSE_PATH.read_bytes())
            no_scheduled_control = copy.deepcopy(long_trace)
            for sample in no_scheduled_control["samples"]:
                for channel in ("throttle", "steer", "brake", "handbrake"):
                    sample[f"{channel}_requested"] = 0.0
                    sample[f"{channel}_applied"] = 0.0
            trace_path.write_text(
                json.dumps(no_scheduled_control, sort_keys=True),
                encoding="utf-8",
            )
            missing_schedule = vehicle_trace.build_artifact(**kwargs)
            self.assertEqual(missing_schedule["status"], "STOP", missing_schedule)
            self.assertTrue(
                any(
                    check["id"].startswith("course_control_")
                    and check["status"] == "STOP"
                    for check in missing_schedule["checks"]
                ),
                missing_schedule,
            )

            no_contact = copy.deepcopy(long_trace)
            for sample in no_contact["samples"]:
                sample["contact_count"] = 0
                sample["body_contact_count"] = 0
            trace_path.write_text(json.dumps(no_contact, sort_keys=True), encoding="utf-8")
            missing_observation = vehicle_trace.build_artifact(**kwargs)
            self.assertEqual(missing_observation["status"], "STOP", missing_observation)
            self.assertTrue(
                any(
                    check["id"] == "course_observation_body_contact_owner_client"
                    for check in missing_observation["checks"]
                ),
                missing_observation,
            )

            invalid_type = copy.deepcopy(long_trace)
            invalid_type["samples"][3]["wheel_count"] = "invalid"
            trace_path.write_text(json.dumps(invalid_type, sort_keys=True), encoding="utf-8")
            self.assertEqual(
                artifact_cli_main(
                    [
                        "--trace",
                        str(trace_path),
                        "--schema",
                        str(schema_path),
                        "--course",
                        str(course_path),
                        "--pbo",
                        str(pbo_path),
                        "--rpt",
                        str(rpt_path),
                        "--script-log",
                        str(script_log_path),
                        "--lifecycle",
                        str(lifecycle_path),
                        "--output",
                        str(temp / "artifact.json"),
                    ]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
