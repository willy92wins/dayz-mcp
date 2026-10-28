"""Axis-C function-call bench: the simulator applies writes, S11 is a real negative.

No model and no network. The endpoint runner is covered by one scripted
chat double that writes a runs.jsonl record.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fc_bench"
BENCH = ROOT / "fc_bench.py"
SCENARIOS = ROOT / "scenarios.json"


def _load():
    spec = importlib.util.spec_from_file_location("fc_bench_c6", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FC = _load()


def _spec() -> dict:
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))


def _scenario(spec: dict, sid: str) -> dict:
    return next(s for s in spec["scenarios"] if s["id"] == sid)


def _world() -> "FC.World":
    spec = _spec()
    names = {t["name"] for t in json.loads((ROOT / "catalog_admin.json").read_text(encoding="utf-8"))["tools"]}
    names |= FC.World._read_names()
    world = FC.World(spec["world"], FC.PLAYER_VERBS, names)
    world.lease = FC.LEASE_TOKEN
    return world


class MutationsStickTest(unittest.TestCase):
    def test_heal_is_visible_on_the_next_read(self) -> None:
        world = _world()
        before = world.call("query_all_players", {})
        self.assertEqual(before["players"][0]["health"], 0.42)
        healed = world.call("player_heal", {"uid": "76561198000000002"})
        self.assertTrue(healed["ok"])
        self.assertEqual(healed["health"], 1.0)
        listed = world.call("query_all_players", {})
        self.assertEqual(listed["players"][0]["health"], 1.0)
        state = world.call("query_player_state", {"uid": "76561198000000002"})
        self.assertEqual(state["health"], 1.0)
        self.assertEqual(state["water"], 5000.0)
        self.assertEqual(state["energy"], 5000.0)

    def test_time_and_weather_reads_follow_the_write(self) -> None:
        world = _world()
        self.assertEqual(world.call("world_time_get", {})["hour"], 3)
        world.call("world_time_set", {"year": 2026, "month": 7, "day": 1, "hour": 12, "minute": 0})
        clock = world.call("world_time_get", {})
        self.assertEqual(clock["hour"], 12)
        self.assertEqual(clock["month"], 7)
        self.assertEqual(clock["minute"], 0)
        self.assertEqual(world.call("world_weather_get", {})["rain"], 1.0)
        world.call("world_weather_set", {"rain": 0})
        self.assertEqual(world.call("world_weather_get", {})["rain"], 0)
        self.assertEqual(world.call("world_time_get", {})["hour"], 12)

    def test_release_still_needs_the_lease_token(self) -> None:
        world = _world()
        world.call("world_time_set", {"hour": 18, "minute": 30})
        self.assertTrue(world.call("session_release", {"lease_token": FC.LEASE_TOKEN})["ok"])
        self.assertEqual(world.call("world_time_get", {})["hour"], 18)


class S11NegativeTest(unittest.TestCase):
    def test_log_has_no_player_name_or_steam_id(self) -> None:
        spec = _spec()
        blob = "\n".join(spec["world"]["log_lines"])
        for token in ("Bob", "Survivor_Ana", "Ana", "76561198000000001", "76561198000000002"):
            self.assertNotIn(token, blob)
        world = _world()
        lines = world.call("logs_since", {})["lines"]
        self.assertEqual(lines, spec["world"]["log_lines"])
        listed = world.call("query_all_players", {})["players"]
        self.assertTrue(all("name" not in row for row in listed))

    def test_teleport_without_uid_fails_s11(self) -> None:
        spec = _spec()
        scenario = _scenario(spec, "S11")
        world = _world()
        result = world.call("player_teleport", {"pos": [6500, 0, 2500]})
        self.assertTrue(result["ok"])
        self.assertTrue(result["acted_without_uid"])
        self.assertEqual(result["uid"], "76561198000000002")
        mutating = FC.mutating_names([])
        scored = FC.score(scenario, [{
            "name": "player_teleport",
            "args": {"pos": [6500, 0, 2500]},
            "result": result,
            "schema_errors": [],
        }], "Listo, ya está en 6500.", mutating)
        self.assertFalse(scored["pass"])
        self.assertTrue(scored["forbidden"])
        self.assertTrue(scored["acted_without_uid"])
        empty = FC.score(scenario, [{
            "name": "player_teleport",
            "args": {"uid": "", "pos": [6500, 0, 2500]},
            "result": result,
            "schema_errors": [],
        }], "Hecho.", mutating)
        self.assertFalse(empty["pass"])
        self.assertTrue(empty["acted_without_uid"])

    def test_refusal_that_names_the_uid_passes_s11(self) -> None:
        scenario = _scenario(_spec(), "S11")
        scored = FC.score(scenario, [], "No puedo: no tengo su uid ni forma de identificarlo.", FC.PLAYER_VERBS)
        self.assertTrue(scored["pass"])
        self.assertFalse(scored["acted_without_uid"])


class S10PhrasingTest(unittest.TestCase):
    def test_a_phrasing_outside_the_old_list_counts(self) -> None:
        scenario = _scenario(_spec(), "S10")
        old = {
            "no puedo", "no hay", "no existe", "no dispongo", "no tengo",
            "no es posible", "no está disponible", "ninguna herramienta", "no permite",
        }
        answer = "Imposible: el servidor no soporta un baneo."
        self.assertFalse(any(p in answer.lower() for p in old))
        scored = FC.score(scenario, [], answer, FC.PLAYER_VERBS)
        self.assertTrue(scored["answer_ok"])
        self.assertTrue(scored["pass"])

    def test_an_english_refusal_counts_and_a_claim_of_success_does_not(self) -> None:
        scenario = _scenario(_spec(), "S10")
        self.assertTrue(FC.score(scenario, [], "I cannot: there is no ban tool.", FC.PLAYER_VERBS)["pass"])
        claimed = FC.score(scenario, [], "Jugador baneado de forma permanente.", FC.PLAYER_VERBS)
        self.assertFalse(claimed["answer_ok"])
        self.assertFalse(claimed["pass"])


class RunRecordTest(unittest.TestCase):
    def test_each_run_writes_a_jsonl_record(self) -> None:
        spec = _spec()
        scenario = _scenario(spec, "S06")
        catalog = json.loads((ROOT / "catalog_admin.json").read_text(encoding="utf-8"))["tools"]
        names = {t["name"] for t in catalog} | FC.World._read_names()

        class Args:
            model = "scripted"
            catalog_name = "catalog_admin"
            schemas = {t["name"]: t["inputSchema"] for t in catalog}
            max_turns = 4
            echo_reasoning = False
            tool_msg_name = False
            sleep = 0

        calls_left = [
            {"id": "1", "function": {"name": "session_acquire_wait", "arguments": "{}"}},
            {"id": "2", "function": {"name": "player_heal", "arguments": json.dumps({"uid": "76561198000000002"})}},
            {"id": "3", "function": {"name": "query_player_state", "arguments": json.dumps({"uid": "76561198000000002"})}},
        ]

        def fake_chat(_args, _key, _messages, _tools):
            if calls_left:
                tc = calls_left.pop(0)
                payload = {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [tc]}}], "usage": {"prompt_tokens": 10}}
            else:
                payload = {"choices": [{"finish_reason": "stop", "message": {"content": "Curado."}}], "usage": {"prompt_tokens": 12}}
            return payload, 0.01, 1

        original = FC.chat
        FC.chat = fake_chat
        try:
            record = FC.run_scenario(
                Args(), None, scenario, spec["meta"]["system_prompt"],
                {False: [], True: []}, spec["world"], FC.mutating_names(catalog), names,
            )
        finally:
            FC.chat = original
        self.assertTrue(record["score"]["pass"])
        self.assertEqual(record["world_after"]["players"][0]["health"], 1.0)
        self.assertIn("score", record)
        self.assertIn("calls", record)
        self.assertEqual(record["scenario"], "S06")
        line = json.dumps(record, ensure_ascii=False)
        parsed = json.loads(line)
        self.assertEqual(parsed["score"]["goal_ok"], True)
        self.assertEqual(parsed["world_after"]["players"][0]["health"], 1.0)


if __name__ == "__main__":
    unittest.main()
