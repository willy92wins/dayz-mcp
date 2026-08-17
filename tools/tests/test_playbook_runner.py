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

    def test_red_player_near_fixture(self) -> None:
        self._assert_expect("red_player_near.json")


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
        uncal = runner.run_fixture(load_place(), fixture)
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


if __name__ == "__main__":
    unittest.main()
