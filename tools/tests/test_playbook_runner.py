"""Offline tests for the generic playbook runner."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path


def playbooks_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [here.parents[3] / "playbooks", here.parents[2] / "playbooks"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "playbooks directory not found; tried: " + ", ".join(str(item) for item in candidates)
    )


PLAYBOOKS = playbooks_dir()
if str(PLAYBOOKS) not in sys.path:
    sys.path.insert(0, str(PLAYBOOKS))

import runner  # noqa: E402


PLACE = PLAYBOOKS / "place_safely.toml"
FIXTURES = PLAYBOOKS / "fixtures" / "place_safely"


def load_place() -> dict:
    return runner.load_playbook(PLACE)


def load_named_fixture(name: str) -> dict:
    return runner.load_fixture(FIXTURES / name)


def run_named(name: str, playbook: dict | None = None) -> tuple[dict, dict]:
    fixture = load_named_fixture(name)
    verdict = runner.run_fixture(playbook or load_place(), fixture)
    return verdict, fixture


class LocatePlaybooksTest(unittest.TestCase):
    def test_playbooks_dir_exists_and_holds_runner(self) -> None:
        self.assertTrue(PLAYBOOKS.is_dir())
        self.assertTrue((PLAYBOOKS / "runner.py").is_file())
        self.assertTrue(PLACE.is_file())


class TomlParseTest(unittest.TestCase):
    def test_place_safely_loads_and_validates(self) -> None:
        playbook = load_place()
        self.assertEqual(playbook["id"], "place_safely")
        runner.validate_schema(playbook)


class FixtureReplayTest(unittest.TestCase):
    def _assert_expect(self, name: str) -> None:
        verdict, fixture = run_named(name)
        self.assertEqual(verdict["overall"], fixture["expect_overall"])
        self.assertEqual(verdict["stopped_at"], fixture["expect_stopped_at"])
        self.assertEqual(verdict["reason"], fixture["expect_reason"])

    def test_pass_fixture(self) -> None:
        self._assert_expect("pass.json")

    def test_red_no_surface_fixture(self) -> None:
        self._assert_expect("red_no_surface.json")

    def test_red_canopy_fixture(self) -> None:
        self._assert_expect("red_canopy.json")

    def test_red_canopy_low_fixture(self) -> None:
        self._assert_expect("red_canopy_low.json")

    def test_red_player_near_fixture(self) -> None:
        self._assert_expect("red_player_near.json")

    def test_red_crowded_fixture(self) -> None:
        self._assert_expect("red_crowded.json")


class AllPlaybooksFixtureContractTest(unittest.TestCase):
    def test_every_toml_loads_and_has_red_per_on_fail(self) -> None:
        tomls = sorted(PLAYBOOKS.glob("*.toml"))
        self.assertGreaterEqual(len(tomls), 3)
        for path in tomls:
            with self.subTest(playbook=path.stem):
                playbook = runner.load_playbook(path)
                runner.validate_schema(playbook)
                fixture_dir = PLAYBOOKS / "fixtures" / path.stem
                self.assertTrue(fixture_dir.is_dir(), path.stem)
                report = runner.run_fixture_dir(playbook, fixture_dir)
                self.assertEqual(report["overall"], "PASS", path.stem)
                seen = {
                    item["verdict"]["reason"]
                    for item in report["results"]
                    if item["verdict"]["overall"] in {"FAIL", "PASS_WITH_WARNINGS"}
                }
                cals = runner.calibrations_by_name(playbook)
                for step in playbook["steps"]:
                    reason = step["on_fail"]["reason"]
                    uses_uncal = any(
                        exp.get("tol_ref")
                        and cals.get(exp.get("tol_ref"), {}).get("state")
                        == "uncalibrated"
                        for exp in (step.get("expect") or [])
                    )
                    if uses_uncal:
                        self.assertTrue(
                            reason in seen
                            or runner.UNCALIBRATED_REASON in seen,
                            f"{path.stem} missing RED for {reason}",
                        )
                    else:
                        self.assertIn(
                            reason,
                            seen,
                            f"{path.stem} missing RED for {reason}",
                        )


class SchemaBiteTest(unittest.TestCase):
    def test_unknown_op_names_step(self) -> None:
        playbook = load_place()
        playbook["steps"][0]["expect"][0]["op"] = "bogus"
        calls: list[str] = []

        def invoke(step_id: str, tool: str, arguments: object) -> dict:
            del tool, arguments
            calls.append(step_id)
            return {"ok": 1, "y": 1.0}

        with self.assertRaises(runner.SchemaError) as ctx:
            runner.run_playbook(playbook, invoke=invoke)
        self.assertIn("S1", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_unresolvable_ref_names_step(self) -> None:
        playbook = load_place()
        playbook["steps"][0]["args"] = {"x": "$nope", "z": "$z"}
        with self.assertRaises(runner.SchemaError) as ctx:
            runner.validate_schema(playbook)
        self.assertIn("S1", str(ctx.exception))

    def test_tool_outside_requires_names_step(self) -> None:
        playbook = load_place()
        playbook["steps"][0]["tool"] = "camera_set"
        with self.assertRaises(runner.SchemaError) as ctx:
            runner.validate_schema(playbook)
        self.assertIn("S1", str(ctx.exception))
        self.assertIn("tool", str(ctx.exception))

    def test_duplicate_id_names_step(self) -> None:
        playbook = load_place()
        clone = copy.deepcopy(playbook["steps"][0])
        playbook["steps"].append(clone)
        with self.assertRaises(runner.SchemaError) as ctx:
            runner.validate_schema(playbook)
        self.assertIn("S1", str(ctx.exception))
        self.assertIn("id", str(ctx.exception))


class NearTolRefTest(unittest.TestCase):
    def _playbook(self) -> dict:
        playbook = {
            "id": "near_probe",
            "version": "0",
            "status": "DRAFT",
            "requires_tools": ["surface_query"],
            "params": {},
            "calibration": [
                {
                    "name": "canopy_dy",
                    "threshold": 0.05,
                    "state": "calibrated",
                }
            ],
            "steps": [
                {
                    "id": "S1",
                    "tool": "surface_query",
                    "args": {},
                    "expect": [
                        {
                            "field": "y",
                            "op": "near",
                            "ref": 315.5601,
                            "tol_ref": "canopy_dy",
                        }
                    ],
                    "on_fail": {"action": "STOP", "reason": "not_near"},
                }
            ],
        }
        return playbook

    def test_delta_0002_passes_when_calibrated(self) -> None:
        playbook = self._playbook()

        def invoke(step_id: str, tool: str, arguments: object) -> dict:
            del step_id, tool, arguments
            return {"y": 315.5603}

        verdict = runner.run_playbook(playbook, invoke=invoke)
        self.assertEqual(verdict["overall"], "PASS")

    def test_delta_006_fails_when_calibrated(self) -> None:
        playbook = self._playbook()

        def invoke(step_id: str, tool: str, arguments: object) -> dict:
            del step_id, tool, arguments
            return {"y": 315.5601 + 0.06}

        verdict = runner.run_playbook(playbook, invoke=invoke)
        self.assertEqual(verdict["overall"], "FAIL")
        self.assertEqual(verdict["stopped_at"], "S1")
        self.assertEqual(verdict["reason"], "not_near")


class MinDistXzTest(unittest.TestCase):
    def _playbook(self) -> dict:
        return {
            "id": "dist_probe",
            "version": "0",
            "status": "DRAFT",
            "requires_tools": ["query_all_players"],
            "params": {"clear_r": 15.0},
            "steps": [
                {
                    "id": "S1",
                    "tool": "query_all_players",
                    "args": {},
                    "expect": [
                        {
                            "field": "players",
                            "op": "min_dist_xz_gte",
                            "origin": [0.0, 0.0],
                            "value": "$clear_r",
                        }
                    ],
                    "on_fail": {"action": "STOP", "reason": "player_near"},
                }
            ],
        }

    def _run(self, players: list) -> dict:
        playbook = self._playbook()

        def invoke(step_id: str, tool: str, arguments: object) -> dict:
            del step_id, tool, arguments
            return {"players": players}

        return runner.run_playbook(playbook, invoke=invoke)

    def test_empty_list_passes(self) -> None:
        self.assertEqual(self._run([])["overall"], "PASS")

    def test_player_at_3m_fails(self) -> None:
        verdict = self._run([{"pos": [3.0, 0.0, 0.0]}])
        self.assertEqual(verdict["overall"], "FAIL")
        self.assertEqual(verdict["reason"], "player_near")

    def test_player_at_20m_passes(self) -> None:
        self.assertEqual(self._run([{"pos": [20.0, 0.0, 0.0]}])["overall"], "PASS")


class UncalibratedDowngradeTest(unittest.TestCase):
    def test_uncalibrated_warns_calibrated_fails(self) -> None:
        fixture = load_named_fixture("red_canopy.json")
        # Both states are forced. Reading whatever place_safely happens to declare
        # today makes this test fail the day the threshold gets calibrated, which
        # is a change in the data, not in the downgrade semantics under test.
        uncalibrated = load_place()
        uncalibrated["calibration"][0]["state"] = "uncalibrated"
        uncal = runner.run_fixture(uncalibrated, fixture)
        self.assertEqual(uncal["overall"], "PASS_WITH_WARNINGS")
        self.assertIsNone(uncal["stopped_at"])
        self.assertEqual(uncal["reason"], "uncalibrated_gate_downgraded")

        calibrated = load_place()
        calibrated["calibration"][0]["state"] = "calibrated"
        failed = runner.run_fixture(calibrated, fixture)
        self.assertEqual(failed["overall"], "FAIL")
        self.assertEqual(failed["stopped_at"], "S2")
        self.assertEqual(failed["reason"], "canopy_or_roof")


class CliExitCodeTest(unittest.TestCase):
    def test_exit_0_on_pass_fixture_file(self) -> None:
        code = runner.main([str(PLACE), "--fixtures", str(FIXTURES / "pass.json")])
        self.assertEqual(code, 0)

    def test_exit_0_on_pass_with_warnings_fixture_file(self) -> None:
        """PASS_WITH_WARNINGS is a pass, and playbooks/README.md promises exit 0 for it.

        Nothing defended that promise. The exit code comes from PASS_OVERALL
        (runner.py:60); dropping PASS_WITH_WARNINGS out of that frozenset left every
        test in this module green -- measured by the mutation run of 2026-08-21. The
        overall assertion below is the precondition: if this fixture ever stops warning,
        the test says so instead of quietly re-testing the plain PASS path.
        """
        verdict = runner.run_fixture(load_place(), load_named_fixture("red_crowded.json"))
        self.assertEqual(verdict["overall"], "PASS_WITH_WARNINGS")
        code = runner.main([str(PLACE), "--fixtures", str(FIXTURES / "red_crowded.json")])
        self.assertEqual(code, 0)

    def test_exit_1_on_fail_fixture_file(self) -> None:
        code = runner.main([str(PLACE), "--fixtures", str(FIXTURES / "red_no_surface.json")])
        self.assertEqual(code, 1)

    def test_exit_0_on_fixture_directory_when_expects_match(self) -> None:
        code = runner.main([str(PLACE), "--fixtures", str(FIXTURES)])
        self.assertEqual(code, 0)

    def test_exit_2_on_schema_error(self) -> None:
        text = PLACE.read_text(encoding="utf-8").replace('op = "eq"', 'op = "nope"', 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.toml"
            path.write_text(text, encoding="utf-8")
            code = runner.main([str(path), "--fixtures", str(FIXTURES / "pass.json")])
        self.assertEqual(code, 2)

    def test_exit_2_on_usage_error(self) -> None:
        code = runner.main([])
        self.assertEqual(code, 2)


class ImportHasNoSideEffectsTest(unittest.TestCase):
    def test_module_exposes_pure_functions(self) -> None:
        self.assertTrue(callable(runner.run_playbook))
        self.assertTrue(callable(runner.validate_schema))
        self.assertTrue(callable(runner.main))


class CalibratedMeasuredSitesTest(unittest.TestCase):
    """n_sites_required is a shipped-playbook invariant, not a runtime near() input.

    README says the runner reads only threshold and state; this test is the
    reader for 'no measured data means the calibration stays uncalibrated'.
    """

    def test_calibrated_entries_meet_n_sites_required(self) -> None:
        tomls = sorted(PLAYBOOKS.glob("*.toml"))
        self.assertGreaterEqual(len(tomls), 3)
        for path in tomls:
            with self.subTest(playbook=path.name):
                playbook = runner.load_playbook(path)
                for cal in playbook.get("calibration") or []:
                    if not isinstance(cal, dict):
                        continue
                    if cal.get("state") == "uncalibrated":
                        continue
                    n_req = cal.get("n_sites_required")
                    measured = cal.get("measured") or []
                    self.assertIsInstance(n_req, int)
                    self.assertNotIsInstance(n_req, bool)
                    self.assertGreaterEqual(n_req, 1, path.name)
                    self.assertIsInstance(measured, list)
                    self.assertGreaterEqual(
                        len(measured),
                        n_req,
                        f"{path.name} calibration {cal.get('name')!r} has "
                        f"{len(measured)} measured sites, needs {n_req}",
                    )


class FixtureReadyMatchesProducerTest(unittest.TestCase):
    def test_bridge_status_ready_matches_compute_bridge_ready(self) -> None:
        tools_dir = Path(__file__).resolve().parents[1]
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        from dayz_mcp.server import compute_bridge_ready

        checked = 0
        for path in sorted((PLAYBOOKS / "fixtures").glob("*/*.json")):
            fixture = runner.load_fixture(path)
            for step_id, payload in (fixture.get("responses") or {}).items():
                if not isinstance(payload, dict):
                    continue
                if "server_peer" not in payload or "client_peer" not in payload:
                    continue
                if "ready" not in payload:
                    continue
                checked += 1
                self.assertEqual(
                    payload["ready"],
                    compute_bridge_ready(payload),
                    f"{path.parent.name}/{path.name} {step_id}",
                )
        self.assertGreaterEqual(checked, 1)


class SessionStatusFixtureShapeTest(unittest.TestCase):
    def _session_status_payloads(self):
        for path in sorted((PLAYBOOKS / "fixtures").glob("*/*.json")):
            fixture = runner.load_fixture(path)
            for step_id, payload in (fixture.get("responses") or {}).items():
                if not isinstance(payload, dict):
                    continue
                if "self" not in payload or "box" not in payload:
                    continue
                yield path, step_id, payload

    def test_operation_tombstones_has_producer_keys(self) -> None:
        seen = 0
        for path, step_id, payload in self._session_status_payloads():
            tombs = payload.get("operation_tombstones")
            self.assertIsInstance(tombs, dict, f"{path.name} {step_id}")
            self.assertEqual(
                set(tombs),
                {"count", "capacity", "saturated"},
                f"{path.name} {step_id}",
            )
            seen += 1
        self.assertGreaterEqual(seen, 1)

    def test_require_version_true_never_pairs_with_legacy(self) -> None:
        seen = 0
        for path in sorted((PLAYBOOKS / "fixtures").glob("*/*.json")):
            fixture = runner.load_fixture(path)
            for step_id, payload in (fixture.get("responses") or {}).items():
                if not isinstance(payload, dict) or "server_peer" not in payload:
                    continue
                if payload.get("require_version") is not True:
                    continue
                seen += 1
                for peer_key in ("server_peer", "client_peer"):
                    peer = payload.get(peer_key) or {}
                    if peer.get("version") is None:
                        self.assertEqual(
                            peer.get("version_state"),
                            "legacy_blocked",
                            f"{path.name} {step_id} {peer_key}",
                        )
        self.assertGreaterEqual(seen, 1)


class RequiresBridgeAbsentTest(unittest.TestCase):
    """requires_bridge is not a gate. Re-adding it to a shipped TOML fails this test."""

    def test_requires_bridge_is_not_a_gate(self) -> None:
        tomls = sorted(PLAYBOOKS.glob("*.toml"))
        self.assertGreaterEqual(len(tomls), 3)
        for path in tomls:
            with self.subTest(playbook=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(
                    text,
                    r"(?m)^\s*requires_bridge\b",
                    f"{path.name} declares requires_bridge; the runner never checks it",
                )
        playbook = {
            "id": "probe",
            "version": "0",
            "status": "DRAFT",
            "requires_tools": ["surface_query"],
            "steps": [
                {
                    "id": "S1",
                    "tool": "surface_query",
                    "args": {},
                    "expect": [{"field": "ok", "op": "eq", "value": 1}],
                    "on_fail": {"action": "STOP", "reason": "x"},
                }
            ],
        }
        runner.validate_schema(playbook)


if __name__ == "__main__":
    unittest.main()
