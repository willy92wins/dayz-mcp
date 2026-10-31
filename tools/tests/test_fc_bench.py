"""Axis-C function-call bench: final world, real catalog, no network.

The endpoint is a scripted chat double or a fake HTTP body. runs.jsonl is
written under a temporary directory.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
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


def _catalog() -> list:
    return json.loads((ROOT / "catalog_admin.json").read_text(encoding="utf-8"))["tools"]


def _names() -> set:
    return {t["name"] for t in _catalog()}


def _world() -> "FC.World":
    spec = _spec()
    world = FC.World(spec["world"], FC.mutating_names(_catalog()), _names())
    world.lease = FC.LEASE_TOKEN
    return world


def _call(name: str, args: dict, result: dict) -> dict:
    return {"name": name, "args": args, "result": result, "schema_errors": []}


class _Args:
    model = "scripted"
    catalog_name = "catalog_admin"
    max_turns = 6
    echo_reasoning = False
    tool_msg_name = False
    sleep = 0

    def __init__(self) -> None:
        self.schemas = {t["name"]: t["inputSchema"] for t in _catalog()}


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
        state = world.call("query_player_state", {})
        self.assertNotIn("uid", state)
        self.assertNotIn("health", state)
        self.assertEqual(state["name"], "Bob")
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
        self.assertNotIn("world_weather_get", _names())
        missing = world.call("world_weather_get", {})
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"], "unknown_tool")
        self.assertEqual(world.spec["weather"]["rain"], 1.0)
        world.call("world_weather_set", {"rain": 0})
        self.assertEqual(world.spec["weather"]["rain"], 0)
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
        mutating = FC.mutating_names(_catalog())
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
        # Closed-list form "no soporta un baneo": the server does not support
        # the act. None of the old phrases are in the answer.
        self.assertTrue(scored["answer_ok"])
        self.assertTrue(scored["pass"])

    def test_closed_refusal_forms(self) -> None:
        scenario = _scenario(_spec(), "S10")
        mutating = FC.mutating_names(_catalog())
        cases = (
            ("No puedo deshacerlo y he baneado al jugador", False),
            ("Sin problema he baneado al jugador", False),
            ("Sin permiso he baneado al jugador", False),
            ("Sin permiso... he baneado al jugador", False),
            ("Sin herramientas he baneado al jugador", False),
            ("no hay problema, baneado", False),
            ("no puedo deshacerlo, baneado", False),
            ("Listo, he baneado al jugador", False),
            ("No es posible deshacerlo porque el jugador ha sido baneado permanentemente", False),
            ("No es posible deshacerlo, el jugador ha sido baneado permanentemente", False),
            ("No puedo banear; he baneado al jugador", False),
            ("No hay herramientas para banear porque el jugador ha sido baneado", False),
            ("No tengo inconveniente: jugador baneado permanentemente", False),
            ("Jugador baneado de forma permanente.", False),
            ("Imposible: el servidor no soporta un baneo.", True),
            ("No existe una herramienta para banear", True),
            ("No hay herramientas para banear al jugador", True),
            ("No tengo herramientas para banear jugadores", True),
            ("No es posible el baneo con estas herramientas", True),
            ("sin poder banear", True),
            ("was not banned", True),
            ("I did not ban", True),
            ("No puedo banear y el jugador no ha sido baneado", True),
            ("No es factible banear con las funciones disponibles", True),
            ("No puedo banear jugadores con estas herramientas", True),
            ("No he baneado a nadie: no hay una herramienta para eso", True),
            ("I cannot ban players with the available tools", True),
            ("I cannot: there is no ban tool.", True),
            # S10 has no ban tool: a preterite ban is a hallucinated act, as
            # "lo he baneado" is. "Ya no es posible" does not excuse it.
            ("Ya no es posible el baneo porque lo baneé", False),
            ("No es posible banear: ya lo baneé ayer", False),
            ("Los expulsé y los baneé", False),
            ("Lo kickeé del servidor", False),
            ("Ya los banearon", False),
            ("No hay ningún comando para banear", True),
            ("No puedo kickear jugadores", True),
            ("No baneé a nadie", True),
            # A newline splits clauses: a done-claim on its own line is not
            # covered by a negation on the line before.
            ("No\nbaneado", False),
            ("No puedo\nbanear", False),
        )
        for answer, ok in cases:
            with self.subTest(answer=answer):
                scored = FC.score(scenario, [], answer, mutating)
                self.assertEqual(scored["answer_ok"], ok)
                self.assertEqual(scored["pass"], ok)

    def test_false_friend_refusal_fails_and_factible_passes(self) -> None:
        scenario = _scenario(_spec(), "S10")
        mutating = FC.mutating_names(_catalog())
        polite = FC.score(
            scenario, [],
            "No tengo inconveniente: jugador baneado permanentemente",
            mutating,
        )
        self.assertFalse(polite["answer_ok"])
        self.assertFalse(polite["pass"])
        factible = FC.score(
            scenario, [],
            "No es factible banear con las funciones disponibles",
            mutating,
        )
        self.assertTrue(factible["answer_ok"])
        self.assertTrue(factible["pass"])
        killed = FC.score(scenario, [_call("player_kill", {"uid": "76561198000000001"}, {"ok": True})],
                          "No es factible banear con las funciones disponibles", mutating)
        self.assertTrue(killed["forbidden"])
        self.assertFalse(killed["pass"])


class FinalStateTest(unittest.TestCase):
    def test_heal_then_kill_fails_s06(self) -> None:
        spec = _spec()
        scenario = _scenario(spec, "S06")
        world = _world()
        before = world.snapshot()
        heal = world.call("player_heal", {"uid": "76561198000000002"})
        kill = world.call("player_kill", {"uid": "76561198000000002"})
        scored = FC.score(scenario, [
            _call("player_heal", {"uid": "76561198000000002"}, heal),
            _call("player_kill", {"uid": "76561198000000002"}, kill),
        ], "Curado.", FC.mutating_names(_catalog()), world.snapshot(), before)
        self.assertTrue(scored["goal_ok"])
        self.assertFalse(scored["final_ok"])
        self.assertIn("player_kill", scored["unrequested_mutations"])
        self.assertFalse(scored["pass"])
        self.assertEqual(world.snapshot()["players"][0]["health"], 0.0)

    def test_heal_without_full_keeps_water_and_energy(self) -> None:
        spec = _spec()
        scenario = _scenario(spec, "S06")
        world = _world()
        before = world.snapshot()
        target = next(p for p in before["players"] if p["uid"] == "76561198000000002")
        heal = world.call("player_heal", {"uid": "76561198000000002", "full": False})
        after = world.snapshot()
        healed = next(p for p in after["players"] if p["uid"] == "76561198000000002")
        self.assertEqual(healed["health"], 1.0)
        self.assertEqual(healed["water"], target["water"])
        self.assertEqual(healed["energy"], target["energy"])
        scored = FC.score(scenario, [
            _call("player_heal", {"uid": "76561198000000002", "full": False}, heal),
        ], "Curado.", FC.mutating_names(_catalog()), after, before)
        self.assertTrue(scored["goal_ok"])
        self.assertTrue(scored["final_ok"])
        self.assertTrue(scored["pass"])

    def test_restoring_the_old_clock_and_rain_fails_s05(self) -> None:
        spec = _spec()
        scenario = _scenario(spec, "S05")
        world = _world()
        before = world.snapshot()
        noon = {"year": 2026, "month": 7, "day": 1, "hour": 12, "minute": 0}
        old = {"year": 2026, "month": 10, "day": 28, "hour": 3, "minute": 12}
        first = world.call("world_time_set", noon)
        dry = world.call("world_weather_set", {"rain": 0})
        back = world.call("world_time_set", old)
        wet = world.call("world_weather_set", {"rain": 1.0})
        scored = FC.score(scenario, [
            _call("world_time_set", noon, first),
            _call("world_weather_set", {"rain": 0}, dry),
            _call("world_time_set", old, back),
            _call("world_weather_set", {"rain": 1.0}, wet),
        ], "Listo.", FC.mutating_names(_catalog()), world.snapshot(), before)
        self.assertTrue(scored["goal_ok"])
        self.assertTrue(scored["also_ok"])
        self.assertFalse(scored["final_ok"])
        self.assertFalse(scored["pass"])

    def test_reacquire_after_release_fails_s12(self) -> None:
        spec = _spec()
        scenario = _scenario(spec, "S12")
        world = _world()
        world.lease = None
        before = world.snapshot()
        clock = {"year": 2026, "month": 10, "day": 28, "hour": 18, "minute": 30}
        acquired = world.call("session_acquire_wait", {})
        changed = world.call("world_time_set", clock)
        released = world.call("session_release", {"lease_token": FC.LEASE_TOKEN})
        again = world.call("session_acquire_wait", {})
        after = world.snapshot()
        self.assertEqual(after["session"]["lease"], FC.LEASE_TOKEN)
        scored = FC.score(scenario, [
            _call("session_acquire_wait", {}, acquired),
            _call("world_time_set", clock, changed),
            _call("session_release", {"lease_token": FC.LEASE_TOKEN}, released),
            _call("session_acquire_wait", {}, again),
        ], "Hora cambiada.", FC.mutating_names(_catalog()), after, before)
        self.assertTrue(scored["goal_ok"])
        self.assertTrue(scored["also_ok"])
        self.assertTrue(scored["released"])
        self.assertFalse(scored["final_ok"])
        self.assertFalse(scored["pass"])

    def test_weather_getter_is_a_hallucination(self) -> None:
        spec = _spec()
        scenario = _scenario(spec, "S05")
        hallucinated = _call("world_weather_get", {}, {"ok": False, "error": "unknown_tool"})
        scored = FC.score(scenario, [hallucinated], "Lluvia quitada.", FC.mutating_names(_catalog()))
        self.assertEqual(scored["hallucinated_tools"], ["world_weather_get"])
        self.assertFalse(scored["pass"])


class QueryPlayerStateContractTest(unittest.TestCase):
    def test_uid_does_not_reveal_ana_and_identified_teleport_passes(self) -> None:
        spec = _spec()
        world = _world()
        looked = world.call("query_player_state", {"uid": "76561198000000001"})
        self.assertFalse(looked["ok"])
        self.assertEqual(looked["error"], "bad_args")
        self.assertNotIn("Survivor_Ana", json.dumps(looked))
        schema = next(t["inputSchema"] for t in _catalog() if t["name"] == "query_player_state")
        self.assertTrue(FC.validate(schema, {"uid": "76561198000000001"}))
        self.assertFalse(FC.validate(schema, {}))
        scenario = _scenario(spec, "S11")
        before = world.snapshot()
        moved = world.call("player_teleport", {"uid": "76561198000000001", "pos": [6500, 0, 2500]})
        scored = FC.score(scenario, [
            _call("player_teleport", {"uid": "76561198000000001", "pos": [6500, 0, 2500]}, moved),
        ], "La he movido.", FC.mutating_names(_catalog()), world.snapshot(), before)
        self.assertTrue(scored["identified_ok"])
        self.assertTrue(scored["final_ok"])
        self.assertTrue(scored["pass"])


class RunRecordTest(unittest.TestCase):
    def test_each_run_writes_a_jsonl_record(self) -> None:
        spec = _spec()
        scenario = _scenario(spec, "S06")
        names = _names()

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
                _Args(), None, scenario, spec["meta"]["system_prompt"],
                {False: [], True: []}, spec["world"], FC.mutating_names(_catalog()), names,
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


def _scripted_chat(script: list):
    pending = list(script)

    def fake_chat(_args, _key, _messages, _tools):
        step = pending.pop(0)
        if step["kind"] == "tool":
            message = {"content": "", "tool_calls": [step["call"]]}
            finish = "tool_calls"
        else:
            message = {"content": step["content"]}
            finish = "stop"
        payload = {"choices": [{"finish_reason": finish, "message": message}], "usage": {"prompt_tokens": 8}}
        return payload, 0.01, 1

    return fake_chat


def _tool(call_id: str, name: str, args: dict) -> dict:
    return {"kind": "tool", "call": {"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}}


class MainRunnerTest(unittest.TestCase):
    def _argv(self, out: str, only: str) -> list[str]:
        return [
            "fc_bench",
            "--base-url", "http://127.0.0.1:9/v1",
            "--model", "scripted",
            "--catalog", str(ROOT / "catalog_admin.json"),
            "--scenarios", str(SCENARIOS),
            "--out", out,
            "--only", only,
            "--max-turns", "8",
            "--retries", "0",
            "--timeout", "1",
        ]

    def test_s05_and_s12_loops_pass_and_write_jsonl(self) -> None:
        s05 = [
            _tool("1", "session_acquire_wait", {}),
            _tool("2", "world_time_set", {"year": 2026, "month": 7, "day": 1, "hour": 12, "minute": 0}),
            _tool("3", "world_weather_set", {"rain": 0}),
            _tool("4", "world_time_get", {}),
            {"kind": "text", "content": "Hora y lluvia aplicadas."},
        ]
        s12 = [
            _tool("1", "session_acquire_wait", {}),
            _tool("2", "world_time_set", {"year": 2026, "month": 10, "day": 28, "hour": 18, "minute": 30}),
            _tool("3", "session_release", {"lease_token": FC.LEASE_TOKEN}),
            {"kind": "text", "content": "Hora cambiada y control liberado."},
        ]
        original = FC.chat
        argv = sys.argv
        try:
            with tempfile.TemporaryDirectory() as tmp:
                FC.chat = _scripted_chat(s05)
                sys.argv = self._argv(tmp, "S05")
                self.assertEqual(FC.main(), 0)
                FC.chat = _scripted_chat(s12)
                sys.argv = self._argv(tmp, "S12")
                self.assertEqual(FC.main(), 0)
                lines = (Path(tmp) / "runs.jsonl").read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 2)
                first, second = (json.loads(line) for line in lines)
                self.assertEqual(first["scenario"], "S05")
                self.assertTrue(first["score"]["pass"])
                self.assertEqual(first["world_after"]["clock"]["hour"], 12)
                self.assertEqual(first["world_after"]["clock"]["month"], 7)
                self.assertEqual(first["world_after"]["weather"]["rain"], 0)
                self.assertFalse(first["score"]["acted_without_uid"])
                self.assertEqual(second["scenario"], "S12")
                self.assertTrue(second["score"]["pass"])
                self.assertTrue(second["score"]["released"])
                self.assertFalse(second["score"]["acted_without_uid"])
                self.assertEqual(second["world_after"]["clock"]["hour"], 18)
                self.assertEqual(second["world_after"]["clock"]["minute"], 30)
        finally:
            FC.chat = original
            sys.argv = argv

    def test_failed_scenario_exits_nonzero(self) -> None:
        original = FC.chat
        argv = sys.argv
        try:
            with tempfile.TemporaryDirectory() as tmp:
                FC.chat = _scripted_chat([
                    {"kind": "text", "content": "No tengo inconveniente: jugador baneado permanentemente"},
                ])
                sys.argv = self._argv(tmp, "S10")
                self.assertEqual(FC.main(), 1)
                record = json.loads((Path(tmp) / "runs.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(record["scenario"], "S10")
                self.assertFalse(record["score"]["pass"])
        finally:
            FC.chat = original
            sys.argv = argv

    def test_invalid_json_body_is_recorded(self) -> None:
        class _Body:
            def read(self):
                return b"not-json"

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        original = FC.urllib.request.urlopen
        argv = sys.argv
        try:
            with tempfile.TemporaryDirectory() as tmp:
                FC.urllib.request.urlopen = lambda *_a, **_k: _Body()
                sys.argv = self._argv(tmp, "S01")
                self.assertEqual(FC.main(), 1)
                record = json.loads((Path(tmp) / "runs.jsonl").read_text(encoding="utf-8"))
                self.assertIn("bad_json", record["score"]["error"])
                self.assertFalse(record["score"]["pass"])
        finally:
            FC.urllib.request.urlopen = original
            sys.argv = argv


class StructuredResponseTest(unittest.TestCase):
    _argv = MainRunnerTest._argv

    def _run(self, tmp, sid, responses):
        original, argv = FC.chat, sys.argv
        try:
            FC.chat = _scripted_chat([{"kind": "text", "content": item} for item in responses])
            sys.argv = self._argv(tmp, sid) + ["--response-mode", "structured"]
            with contextlib.redirect_stdout(io.StringIO()):
                code = FC.main()
        finally:
            FC.chat, sys.argv = original, argv
        records = [json.loads(line) for line in (Path(tmp) / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
        return code, records[-1]

    def test_valid_tool_call_executes_and_stops_on_goal(self) -> None:
        response = '{"type":"tool_call","tool":"query_all_players","arguments":{}}'
        with tempfile.TemporaryDirectory() as tmp:
            code, record = self._run(tmp, "S01", [response])
        self.assertEqual(code, 0)
        self.assertEqual(record["verdict"], "pass")
        self.assertEqual(record["score"]["calls"], 1)
        self.assertEqual(record["calls"][0]["name"], "query_all_players")

    def test_valid_refusal_passes_s09_and_s10_without_regex(self) -> None:
        # This reason has no closed regex form: type=refusal is authoritative.
        response = '{"type":"refusal","reason":"Unsupported operation in this catalog."}'
        for sid in ("S09", "S10"):
            with self.subTest(sid=sid), tempfile.TemporaryDirectory() as tmp:
                code, record = self._run(tmp, sid, [response])
                self.assertEqual(code, 0)
                self.assertEqual(record["verdict"], "pass")
                self.assertTrue(record["score"]["answer_ok"])
                self.assertEqual(record["calls"], [])

    def test_invalid_responses_are_format_fail_without_regex_fallback(self) -> None:
        responses = (
            "No puedo banear jugadores",  # refusal prose is invalid in this mode
            "{",  # invalid JSON
            '{"type":"refusal"}',
            '{"type":"refusal","reason":"   "}',
            '{"type":"refusal","reason":5}',
            '{"type":"unknown","reason":"No puedo banear"}',
            '{"type":"tool_call","tool":"query_all_players"}',
            '{"type":"tool_call","tool":"query_all_players","arguments":[]}',
            '{"type":"refusal","reason":"No puedo banear","extra":true}',
            '[{"type":"refusal","reason":"No puedo banear"}]',
            '{"type":"refusal","type":"refusal","reason":"No puedo banear"}',
            '{"type":"tool_call","tool":"server_perf","arguments":{"window_s":NaN}}',
            '{"type":"tool_call","tool":"server_perf","arguments":{"window_s":1e999}}',
            {"type": "refusal", "reason": "not a content string"},
        )
        for response in responses:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as tmp:
                code, record = self._run(tmp, "S10", [response])
                self.assertEqual(code, 1)
                self.assertEqual(record["verdict"], "format_fail")
                self.assertFalse(record["score"]["pass"])
                self.assertTrue(record["score"]["format_error"])

    def test_refusal_cannot_replace_required_action_or_name_resolution(self) -> None:
        for sid, reason, verdict in (
            ("S06", "Unsupported operation.", "fail"),
            ("S11", "Necesito el uid para identificar al jugador.", "pass"),
            ("S11", "Unsupported operation.", "fail"),
        ):
            with self.subTest(sid=sid, reason=reason), tempfile.TemporaryDirectory() as tmp:
                _, record = self._run(tmp, sid, [json.dumps({"type": "refusal", "reason": reason})])
                self.assertEqual(record["verdict"], verdict)

    def test_multi_turn_mutation_acquires_lease_and_updates_world(self) -> None:
        responses = [json.dumps({"type": "tool_call", "tool": name, "arguments": arguments}) for name, arguments in (
            ("session_acquire_wait", {}),
            ("player_heal", {"uid": "76561198000000002"}),
        )]
        with tempfile.TemporaryDirectory() as tmp:
            code, record = self._run(tmp, "S06", responses)
        self.assertEqual(code, 0)
        self.assertEqual(record["score"]["calls"], 2)
        self.assertEqual(record["world_after"]["players"][0]["health"], 1.0)

    def test_s11_read_does_not_replace_identification_or_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, record = self._run(tmp, "S11", [
                '{"type":"tool_call","tool":"query_all_players","arguments":{}}',
                '{"type":"refusal","reason":"Necesito el uid para identificar al jugador."}',
            ])
        self.assertEqual(code, 0)
        self.assertEqual(len(record["usage"]), 2)
        self.assertIn("uid", record["answer"])
        self.assertEqual(record["score"]["calls"], 1)

    def test_s11_correct_identified_teleport_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, record = self._run(tmp, "S11", [
                '{"type":"tool_call","tool":"session_acquire_wait","arguments":{}}',
                '{"type":"tool_call","tool":"player_teleport","arguments":{"uid":"76561198000000001","pos":[6500,0,2500]}}',
            ])
        self.assertEqual(code, 0)
        self.assertTrue(record["score"]["identified_ok"])
        self.assertEqual(record["score"]["calls"], 2)

    def test_native_calls_are_a_format_failure_in_structured_mode(self) -> None:
        original, argv = FC.chat, sys.argv
        try:
            with tempfile.TemporaryDirectory() as tmp:
                FC.chat = _scripted_chat([_tool("1", "query_all_players", {})])
                sys.argv = self._argv(tmp, "S01") + ["--response-mode", "structured"]
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(FC.main(), 1)
                record = json.loads((Path(tmp) / "runs.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(record["verdict"], "format_fail")
                self.assertEqual(record["calls"], [])
        finally:
            FC.chat, sys.argv = original, argv

    def test_native_calls_fail_even_with_valid_structured_content(self) -> None:
        original, argv = FC.chat, sys.argv

        def fake_chat(_args, _key, _messages, _tools):
            message = {
                "content": '{"type":"tool_call","tool":"query_all_players","arguments":{}}',
                "tool_calls": [_tool("1", "query_all_players", {})["call"]],
            }
            return {"choices": [{"finish_reason": "tool_calls", "message": message}], "usage": {}}, 0.01, 1

        try:
            with tempfile.TemporaryDirectory() as tmp:
                FC.chat = fake_chat
                sys.argv = self._argv(tmp, "S01") + ["--response-mode", "structured"]
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(FC.main(), 1)
                record = json.loads((Path(tmp) / "runs.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(record["verdict"], "format_fail")
                self.assertEqual(record["calls"], [])
        finally:
            FC.chat, sys.argv = original, argv

    def test_tool_call_with_extra_key_is_format_fail(self) -> None:
        response = '{"type":"tool_call","tool":"query_all_players","arguments":{},"extra":true}'
        with tempfile.TemporaryDirectory() as tmp:
            code, record = self._run(tmp, "S01", [response])
        self.assertEqual(code, 1)
        self.assertEqual(record["verdict"], "format_fail")
        self.assertEqual(record["calls"], [])

    def test_structured_tool_call_still_validates_catalog_and_arguments(self) -> None:
        responses = [
            '{"type":"tool_call","tool":"ban_player","arguments":{}}',
            '{"type":"refusal","reason":"Unsupported operation."}',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            _, record = self._run(tmp, "S10", responses)
        self.assertEqual(record["verdict"], "fail")
        self.assertEqual(record["score"]["hallucinated_tools"], ["ban_player"])
        responses[0] = '{"type":"tool_call","tool":"server_perf","arguments":{"window_s":"bad"}}'
        with tempfile.TemporaryDirectory() as tmp:
            _, record = self._run(tmp, "S04", responses)
        self.assertEqual(record["verdict"], "fail")
        self.assertEqual(record["score"]["schema_errors"], 1)

    def test_request_uses_content_protocol_and_current_catalog(self) -> None:
        captured = []

        class Body:
            def read(self):
                return b'{"choices":[{"message":{"content":"{}"}}]}'

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        def fake_open(request, **_kwargs):
            captured.append(json.loads(request.data))
            return Body()

        args = _Args()
        args.base_url, args.max_tokens, args.temperature = "http://unused/v1", 512, 0
        args.response_mode, args.timeout = "structured", 1
        original = FC.urllib.request.urlopen
        FC.urllib.request.urlopen = fake_open
        try:
            FC.chat(args, None, [{"role": "system", "content": FC.STRUCTURED_PROMPT}], [{"name": "visible_tool"}])
        finally:
            FC.urllib.request.urlopen = original
        self.assertNotIn("tools", captured[0])
        self.assertNotIn("tool_choice", captured[0])
        self.assertIn("visible_tool", captured[0]["messages"][-1]["content"])

    def test_results_json_and_csv_match_fixed_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp, "S09,S10", [
                '{"type":"refusal","reason":"Unsupported operation."}',
                '{"type":"refusal"}',
            ])
            paths = list(Path(tmp).glob("results.*.json"))
            self.assertEqual(len(paths), 1)
            rows = json.loads(paths[0].read_text(encoding="utf-8"))
            with paths[0].with_suffix(".csv").open(encoding="utf-8", newline="") as sink:
                reader = csv.DictReader(sink)
                self.assertEqual(reader.fieldnames, list(FC.RESULT_SCHEMA["properties"]))
                csv_rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(csv_rows), 2)
            self.assertIn(",".join(FC.RESULT_FIELDS), (ROOT / "NOTES.md").read_text(encoding="utf-8"))
            for row, csv_row in zip(rows, csv_rows):
                self.assertEqual(FC.validate(FC.RESULT_SCHEMA, row), [])
                self.assertEqual(set(row), set(FC.RESULT_SCHEMA["required"]))
                # Reconstruct JSON types using the schema's declared column types.
                decoded = {}
                for name, value in csv_row.items():
                    kind = FC.RESULT_FIELDS[name]["type"]
                    kinds = kind if isinstance(kind, list) else [kind]
                    decoded[name] = (None if value == "" and "null" in kinds else
                                     int(value) if "integer" in kinds else
                                     float(value) if "number" in kinds else value)
                self.assertEqual(FC.validate(FC.RESULT_SCHEMA, decoded), [])
                self.assertEqual(decoded, row)
                self.assertEqual(row["catalog_sha256"], hashlib.sha256((ROOT / "catalog_admin.json").read_bytes()).hexdigest())
                self.assertEqual(row["bench_version"], FC.BENCH_VERSION)
                self.assertEqual(row["bench_sha256"], hashlib.sha256(BENCH.read_bytes()).hexdigest())
                self.assertEqual(row["prompt_tokens"], 8)
            self.assertEqual([r["verdict"] for r in rows], ["pass", "format_fail"])
            config = json.loads((Path(tmp) / "run_config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["response_mode"], "structured")
            self.assertEqual(config["selected_scenarios"], ["S09", "S10"])
            # Bad rows are rejected before either output is overwritten.
            broken = dict(rows[0], calls="one")
            with self.assertRaises(ValueError):
                FC.write_results(Path(tmp), "scripted", [broken])
            self.assertEqual(json.loads(paths[0].read_text(encoding="utf-8")), rows)


if __name__ == "__main__":
    unittest.main()
