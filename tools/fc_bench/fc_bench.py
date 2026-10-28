#!/usr/bin/env python3
"""Function-calling bench for the DayZ-MCP tool catalog (axis C gate).

Sends the 12 admin scenarios to an OpenAI-compatible chat/completions
endpoint the caller names, with an MCP catalog as `tools`. Each tool call
is answered by a scripted world (no game, no dayz_mcp process). One record
per scenario run is appended to ``<out>/runs.jsonl`` before the next run
starts; ``summary.json`` and ``summary.md`` follow.

Seed: council-servidores-live bench (on this machine,
``dzmcp_gauntlet/council-live-20261028/bench/fc_bench.py``), not a path
inside the dayz-mcp tree. Three faults from that seed are fixed here:

* Mutations stick. ``player_heal``, ``player_kill``, ``player_godmode``,
  ``world_time_set`` and ``world_weather_set`` change the scripted world.
  A later ``query_all_players`` or ``world_time_get`` returns the new
  value. ``query_player_state`` is the real contract: no uid argument, the
  first connected player only (name, position, godmode, water, energy).
  The admin catalog has no ``world_weather_get``; a weather write is
  scored on the final world, not by a getter the registry does not ship.
  The seed answered writes with ``applied`` and left the reads stale, so
  a model that checked its work looped until ``max_turns`` (S05, S06, S12).
* A pass needs the final world, not one correct call somewhere in the
  history. A later undo, or a mutation the scenario did not ask for, fails.
* A tool name that is not in the loaded catalog is ``unknown_tool`` and
  counts as a hallucination. The simulator does not answer it.
* The scripted log does not carry a player name or a Steam id. S11 is a
  negative: the model cannot learn Survivor_Ana's uid from the log or by
  passing a uid to ``query_player_state``. A teleport that does name her
  uid is identified and can pass; one that does not, or that omits the
  uid, fails.
* S10 accepts more refusal phrasings than the seed's short Spanish list.

An empty uid on a player verb still lands on the first player (Bob), as
the seed documented for the real server. S11 scores that success as a
failure. Refusing an empty uid is ticket B2, not this bench.

Stdlib only. The API key is read from the environment variable named by
``--api-key-env`` and is never printed or written.

    python tools/fc_bench/fc_bench.py --base-url http://127.0.0.1:11434/v1 \\
        --model qwen3.8:98k \\
        --catalog tools/fc_bench/catalog_admin.json \\
        --scenarios tools/fc_bench/scenarios.json --out out/qwen-admin

No axis-C change closes without a run of this bench. This file does not
call a model by itself.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

LEASE_TOKEN = "lease-bench-01"
ACQUIRE_TOOLS = {"session_acquire_wait", "lease_acquire"}
SESSION_TOOLS = ACQUIRE_TOOLS | {"session_wait", "session_acquire", "session_release", "session_status"}
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
# Past-tense claims that the forbidden action was done. Infinitives
# ("banear") are not claims. Matched per clause so "no tengo inconveniente"
# does not negate a later "baneado".
_SUCCESS_CLAIM = re.compile(
    r"\b(banead[oa]s?|banned|he baneado|expulsad[oa]s?|kickeado|kicked|he expulsado)\b",
    re.IGNORECASE,
)
_REFUSAL = re.compile(
    r"no puedo|no es posible|no es factible|no existe|no dispongo|"
    r"no est[aá] disponible|imposible|no soport|"
    r"no hay (?:una |la )?(?:herramienta|funci[oó]n|forma)|"
    r"ninguna herramienta|no permite|no incluye|no ofrece|no figura|"
    r"no cuento|no cubre|no contempl|sin herramienta|no hay forma|"
    r"no tengo (?:una |la |ninguna |forma|herramienta|funci|c[oó]mo|manera|su )|"
    r"cannot|can't|unable|no tool|not available|not possible|"
    r"there is no|do not have|don't have|no way",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(r"[\n.;:]+")
PLAYER_VERBS = {
    "player_teleport",
    "player_heal",
    "player_godmode",
    "player_kill",
    "inventory_give",
}
CLOCK_KEYS = ("year", "month", "day", "hour", "minute", "time_multiplier")
WEATHER_KEYS = ("rain", "fog", "overcast", "wind")


# --------------------------------------------------------------------------
# Minimal JSON Schema check: the subset the DayZ-MCP catalog uses.
# --------------------------------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = _TYPES.get(expected)
    return py is not None and isinstance(value, py)


def validate(schema: dict, value, path: str = "$") -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors
    if "anyOf" in schema:
        if not any(not validate(sub, value, path) for sub in schema["anyOf"]):
            errors.append(f"{path}: matches no anyOf branch")
        return errors
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_type_ok(value, t) for t in expected):
            errors.append(f"{path}: expected one of {expected}, got {type(value).__name__}")
            return errors
    elif isinstance(expected, str) and not _type_ok(value, expected):
        errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required")
        for name, item in value.items():
            if name in props:
                errors.extend(validate(props[name], item, f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name}: unknown property")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(validate(schema["items"], item, f"{path}[{index}]"))
    return errors


# --------------------------------------------------------------------------
# Scripted world
# --------------------------------------------------------------------------


class World:
    """What the fake server answers. One instance per scenario run.

    Writes change ``spec`` and the next read of that fact returns the new
    value. ``mutations`` is the list of applied writes, copied into the
    run record.
    """

    def __init__(self, spec: dict, mutating: set[str], catalog_names: set[str]):
        self.spec = copy.deepcopy(spec)
        self.mutating = mutating
        self.catalog_names = catalog_names
        self.lease: str | None = None
        self.queued = False
        self.mutations: list[dict] = []
        self.spec.setdefault("weather", {"rain": 1.0, "fog": 0.0, "overcast": 0.5})
        for player in self.players():
            player.setdefault("water", 800.0)
            player.setdefault("energy", 900.0)

    def players(self) -> list[dict]:
        return self.spec["players"]

    def find(self, uid: str) -> dict | None:
        for player in self.players():
            if player["uid"] == uid:
                return player
        return None

    def snapshot(self) -> dict:
        return {
            "players": copy.deepcopy(self.players()),
            "clock": copy.deepcopy(self.spec.get("clock", {})),
            "weather": copy.deepcopy(self.spec.get("weather", {})),
            "mutations": copy.deepcopy(self.mutations),
        }

    def call(self, name: str, args: dict) -> dict:
        if name not in self.catalog_names:
            return {"ok": False, "error": "unknown_tool", "detail": f"no tool named {name}"}
        if name in ACQUIRE_TOOLS or (name == "session_wait" and self.queued):
            self.lease, self.queued = LEASE_TOKEN, False
            return {"ok": True, "status": "acquired", "lease_token": LEASE_TOKEN, "lease_ttl_s": 120}
        if name == "session_acquire":
            self.queued = True
            return {"ok": True, "status": "queued", "ticket_id": "ticket-bench-01", "next_step": "session_wait"}
        if name == "session_status":
            return {"ok": True, "self": {"lease_token": self.lease, "queued": self.queued}}
        if name == "session_release":
            if self.lease and args.get("lease_token") == self.lease:
                self.lease = None
                return {"ok": True, "released": True}
            return {"ok": False, "error": "lease_token_mismatch"}
        if name == "bridge_status":
            return {"ok": True, "ready": {"ready": True, "reason": "ready"}}
        if name == "query_all_players":
            keys = ("uid", "pos", "health", "in_vehicle", "godmode")
            return {"ok": True, "players": [{k: p[k] for k in keys} for p in self.players()]}
        if name == "query_player_state":
            return self._player_state(args)
        if name == "server_perf":
            window = args.get("window_s", 10.0)
            return dict({"ok": True, "window_s": window, "insufficient_samples": 0}, **self.spec["perf"])
        if name == "world_time_get":
            return dict({"ok": True}, **self.spec["clock"])
        if name == "logs_since":
            lines = self.spec["log_lines"]
            return {"ok": True, "lines": lines, "count": len(lines), "marker": "{\"server.RPT\":[4096,4096,\"bench\"]}"}
        if name == "entities_query":
            rows = self.spec["entities_near_ana"]
            return {"ok": True, "entities": rows, "count_total": len(rows)}
        if name == "wait_for":
            return {"ok": True, "satisfied": False}
        if name in self.mutating or name in PLAYER_VERBS | {"world_time_set", "world_weather_set", "notify_players"}:
            return self._mutate(name, args)
        return {"ok": False, "error": "not_simulated_in_bench"}

    def _player_state(self, args: dict) -> dict:
        # server.py query_player_state(timeout_s) calls the bridge with {}.
        # The bridge reads GetPlayers()[0] and returns name, pos, godmode,
        # water and energy. It does not take a uid and does not return one,
        # so a caller cannot look Survivor_Ana up by enumerating uids.
        if "uid" in args:
            return {
                "ok": False,
                "error": "bad_args",
                "detail": "query_player_state does not accept uid",
            }
        if not self.players():
            return {"ok": False, "error": "no_players"}
        player = self.players()[0]
        return {
            "ok": True,
            "name": player["name"],
            "pos": list(player["pos"]),
            "godmode": player["godmode"],
            "water": player.get("water", 0.0),
            "energy": player.get("energy", 0.0),
        }

    def _mutate(self, name: str, args: dict) -> dict:
        if not self.lease:
            return {"ok": False, "error": "lease_required", "next_step": "session_acquire_wait"}
        target = None
        acted_without_uid = False
        if "uid" in args or name in PLAYER_VERBS:
            uid = args.get("uid") or ""
            if uid:
                target = self.find(uid)
                if target is None:
                    return {"ok": False, "error": "player_not_found", "uid": uid}
            elif name != "notify_players":
                acted_without_uid = True
                target = self.players()[0]  # the server's first human
        if name == "player_teleport":
            pos = args.get("pos")
            if not (isinstance(pos, list) and len(pos) == 3):
                return {"ok": False, "error": "bad_args", "detail": "pos must be [x, y, z]"}
            before = list(target["pos"])
            target["pos"] = [pos[0], 11.8 if pos[1] == 0 else pos[1], pos[2]]
            self._note(name, target, {"pos": target["pos"]}, acted_without_uid)
            return {
                "ok": True,
                "uid": target["uid"],
                "pos_before": before,
                "pos_after": target["pos"],
                "acted_without_uid": acted_without_uid,
            }
        if name == "player_heal":
            target["health"] = 1.0
            if args.get("full", True) is not False:
                target["water"] = 5000.0
                target["energy"] = 5000.0
            self._note(name, target, {"health": 1.0}, acted_without_uid)
            return {
                "ok": True,
                "uid": target["uid"],
                "health": target["health"],
                "water": target["water"],
                "energy": target["energy"],
                "acted_without_uid": acted_without_uid,
            }
        if name == "player_kill":
            target["health"] = 0.0
            self._note(name, target, {"health": 0.0}, acted_without_uid)
            return {"ok": True, "uid": target["uid"], "health": 0.0, "acted_without_uid": acted_without_uid}
        if name == "player_godmode":
            target["godmode"] = bool(args.get("on", True))
            self._note(name, target, {"godmode": target["godmode"]}, acted_without_uid)
            return {"ok": True, "uid": target["uid"], "godmode": target["godmode"], "acted_without_uid": acted_without_uid}
        if name == "world_time_set":
            clock = self.spec["clock"]
            for key in CLOCK_KEYS:
                if key in args and args[key] is not None:
                    clock[key] = args[key]
            self.mutations.append({"tool": name, "clock": copy.deepcopy(clock)})
            return {"ok": True, "applied": copy.deepcopy(clock)}
        if name == "world_weather_set":
            weather = self.spec["weather"]
            for key in WEATHER_KEYS:
                if key in args and args[key] is not None:
                    weather[key] = args[key]
            self.mutations.append({"tool": name, "weather": copy.deepcopy(weather)})
            return {"ok": True, "applied": copy.deepcopy(weather)}
        if name == "notify_players":
            uid = args.get("uid") or ""
            self.mutations.append({"tool": name, "sent_to": uid or "all"})
            return {"ok": True, "sent_to": uid or "all", "recipients": 1 if uid else len(self.players())}
        result = {"ok": True, "applied": args}
        if target is not None:
            result["uid"] = target["uid"]
            result["acted_without_uid"] = acted_without_uid
            self._note(name, target, {"applied": args}, acted_without_uid)
        else:
            self.mutations.append({"tool": name})
        return result

    def _note(self, name: str, target: dict, fields: dict, acted_without_uid: bool) -> None:
        self.mutations.append({
            "tool": name,
            "uid": target["uid"],
            "acted_without_uid": acted_without_uid,
            **fields,
        })


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------


def chat(args, key: str | None, messages: list, tools: list) -> tuple[dict, float, int]:
    body = {
        "model": args.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": args.max_tokens,
    }
    if args.temperature is not None:
        body["temperature"] = args.temperature
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = args.base_url.rstrip("/") + "/chat/completions"
    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                raw = response.read().decode("utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"bad_json: {exc}") from None
            return payload, time.monotonic() - started, attempt
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code in RETRY_STATUS and attempt <= args.retries:
                wait = exc.headers.get("Retry-After")
                time.sleep(float(wait) if wait and wait.isdigit() else min(60, 5 * 2 ** (attempt - 1)))
                continue
            raise RuntimeError(f"http_{exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt <= args.retries:
                time.sleep(min(60, 5 * 2 ** (attempt - 1)))
                continue
            raise RuntimeError(f"transport: {exc}") from None


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _get(args: dict, dotted: str):
    value = args
    for part in dotted.split("."):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def _pred_ok(args: dict, key: str, pred: dict) -> bool:
    if key.startswith("@"):
        fields = key[1:].split("|")
        text = pred.get("contains", "")
        return any(text in str(args.get(f, "")) for f in fields)
    value = _get(args, key)
    if "empty_or_absent" in pred:
        return value in (None, "")
    if "eq" in pred:
        return value == pred["eq"]
    if "range" in pred:
        low, high = pred["range"]
        return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high
    if "contains" in pred:
        return pred["contains"] in str(value or "")
    return False


def _call_matches(call: dict, goal: dict) -> bool:
    if call["name"] != goal["tool"] or not call["result"].get("ok"):
        return False
    return all(_pred_ok(call["args"], k, p) for k, p in goal.get("args", {}).items())


def _goal_met(goal: dict, calls: list[dict]) -> bool:
    return any(_call_matches(call, goal) for call in calls)


def _scenario_goals(scenario: dict) -> list[dict]:
    goals = []
    if "goal" in scenario:
        goals.append(scenario["goal"])
    goals.extend(scenario.get("also") or [])
    return goals


def _allowed_mutations(scenario: dict) -> set[str]:
    allowed = {g["tool"] for g in _scenario_goals(scenario)}
    identified = scenario.get("identified")
    if isinstance(identified, dict) and identified.get("tool"):
        allowed.add(identified["tool"])
    return allowed | SESSION_TOOLS


def _unrequested_mutations(scenario: dict, calls: list[dict], mutating: set[str]) -> list[str]:
    """Mutation tools the scenario did not ask for, applied or only attempted."""
    allowed = _allowed_mutations(scenario)
    found = []
    for call in calls:
        name = call["name"]
        if name in allowed:
            continue
        if name in mutating or name in PLAYER_VERBS:
            found.append(name)
    return found


def _in_range(value, bounds) -> bool:
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        return False
    low, high = bounds
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def _apply_goal_to_expected(goal: dict, players: dict, clock: dict, weather: dict) -> None:
    args = goal.get("args") or {}
    tool = goal.get("tool")
    if tool == "world_time_set":
        for key, pred in args.items():
            if isinstance(pred, dict) and "eq" in pred:
                clock[key] = pred["eq"]
    elif tool == "world_weather_set":
        for key, pred in args.items():
            if not isinstance(pred, dict):
                continue
            if "eq" in pred:
                weather[key] = pred["eq"]
            elif "range" in pred:
                weather[key] = ("range", tuple(pred["range"]))
    elif tool == "player_heal":
        uid = (args.get("uid") or {}).get("eq")
        if uid in players:
            players[uid]["health"] = 1.0
            full = True
            if isinstance(args.get("full"), dict) and "eq" in args["full"]:
                full = bool(args["full"]["eq"])
            if full:
                players[uid]["water"] = 5000.0
                players[uid]["energy"] = 5000.0
    elif tool == "player_kill":
        uid = (args.get("uid") or {}).get("eq")
        if uid in players:
            players[uid]["health"] = 0.0
    elif tool == "player_godmode":
        uid = (args.get("uid") or {}).get("eq")
        if uid in players and isinstance(args.get("on"), dict) and "eq" in args["on"]:
            players[uid]["godmode"] = bool(args["on"]["eq"])
    elif tool == "player_teleport":
        uid = (args.get("uid") or {}).get("eq")
        if uid in players:
            players[uid]["pos"] = ("teleport", args)


def _pos_matches(pos, spec) -> bool:
    if not (isinstance(pos, list) and len(pos) == 3):
        return False
    x = spec.get("pos.0") or {}
    y = spec.get("pos.1") or {}
    z = spec.get("pos.2") or {}
    if "range" in x and not _in_range(pos[0], x["range"]):
        return False
    if "range" in z and not _in_range(pos[2], z["range"]):
        return False
    if "range" in y:
        low, high = y["range"]
        # The simulator drops y=0 onto the ground (11.8), matching the server.
        if low == 0 and high == 0:
            if pos[1] != 11.8:
                return False
        elif not _in_range(pos[1], y["range"]):
            return False
    return True


def _final_ok(scenario: dict, world_before: dict, world_after: dict, calls: list[dict] | None = None) -> bool:
    """The world left behind matches the scenario, not an earlier call.

    ``identified`` changes the expected world only when that call happened.
    A refusal leaves the initial world in place.
    """
    players = {p["uid"]: copy.deepcopy(p) for p in world_before["players"]}
    clock = copy.deepcopy(world_before.get("clock") or {})
    weather = copy.deepcopy(world_before.get("weather") or {})
    goals = _scenario_goals(scenario)
    identified = scenario.get("identified")
    if isinstance(identified, dict) and calls is not None and _goal_met(identified, calls):
        goals.append(identified)
    for goal in goals:
        _apply_goal_to_expected(goal, players, clock, weather)
    after_players = {p["uid"]: p for p in world_after["players"]}
    if set(after_players) != set(players):
        return False
    for uid, expected in players.items():
        got = after_players[uid]
        for key, value in expected.items():
            if key == "pos" and isinstance(value, tuple) and value and value[0] == "teleport":
                if not _pos_matches(got.get("pos"), value[1]):
                    return False
                continue
            if got.get(key) != value:
                return False
    for key, value in clock.items():
        if world_after.get("clock", {}).get(key) != value:
            return False
    for key, value in weather.items():
        got = world_after.get("weather", {}).get(key)
        if isinstance(value, tuple) and value and value[0] == "range":
            if not _in_range(got, value[1]):
                return False
        elif got != value:
            return False
    return True


def _clause_claims_success(clause: str) -> bool:
    match = _SUCCESS_CLAIM.search(clause)
    if not match:
        return False
    before = clause[: match.start()]
    # "no he baneado" denies the claim. "no tengo inconveniente" does not.
    if re.search(r"\b(no tengo inconveniente|no hay problema|no hay inconveniente)\b", before, re.IGNORECASE):
        before = re.sub(
            r"\b(no tengo inconveniente|no hay problema|no hay inconveniente)\b",
            "",
            before,
            flags=re.IGNORECASE,
        )
    return re.search(r"\b(no|not|never|sin)\b", before, re.IGNORECASE) is None


def _refusal_answer(answer: str) -> bool:
    """Explicit refusal, and no clause that says the action was done."""
    if any(_clause_claims_success(part) for part in _CLAUSE_SPLIT.split(answer)):
        return False
    return _REFUSAL.search(answer) is not None


def _acted_without_uid(calls: list[dict], mutating: set[str]) -> bool:
    """A player mutation that succeeded with no uid in the arguments."""
    for call in calls:
        if call["name"] not in mutating and call["name"] not in PLAYER_VERBS:
            continue
        if call["name"] == "notify_players":
            continue
        if not call["result"].get("ok"):
            continue
        uid = call["args"].get("uid") if isinstance(call["args"], dict) else None
        if uid in (None, ""):
            return True
    return False


def score(
    scenario: dict,
    calls: list[dict],
    answer: str,
    mutating: set[str],
    world_after: dict | None = None,
    world_before: dict | None = None,
) -> dict:
    lowered = answer.lower()
    s = {
        "hallucinated_tools": sorted({c["name"] for c in calls if c["result"].get("error") == "unknown_tool"}),
        "json_errors": sum(1 for c in calls if c.get("json_error")),
        "schema_errors": sum(1 for c in calls if c["schema_errors"]),
        "lease_required_hits": sum(1 for c in calls if c["result"].get("error") == "lease_required"),
        "wrong_player": sum(
            1 for c in calls
            if c["name"] in mutating and c["result"].get("ok")
            and c["result"].get("uid") and "uid" not in c["args"] and c["name"] != "notify_players"
        ),
        "acted_without_uid": _acted_without_uid(calls, mutating),
        "released": any(c["name"] == "session_release" and c["result"].get("ok") for c in calls),
        "calls": len(calls),
    }
    s["goal_ok"] = _goal_met(scenario["goal"], calls) if "goal" in scenario else True
    s["also_ok"] = all(_goal_met(g, calls) for g in scenario.get("also", []))
    forbidden = False
    if scenario.get("forbid_mutations"):
        forbidden |= any(c["name"] in mutating or c["name"] in PLAYER_VERBS for c in calls)
    forbidden |= any(c["name"] in scenario.get("forbid_success", []) and c["result"].get("ok") for c in calls)
    if scenario.get("fail_without_uid"):
        forbidden |= s["acted_without_uid"]
    identified = scenario.get("identified")
    identified_ok = bool(isinstance(identified, dict) and _goal_met(identified, calls))
    if identified_ok:
        # A teleport that names the right uid is the task, not a refusal.
        forbidden = s["acted_without_uid"] if scenario.get("fail_without_uid") else False
        forbidden |= any(
            c["name"] in scenario.get("forbid_success", []) and c["result"].get("ok") and not _call_matches(c, identified)
            for c in calls
        )
    elif isinstance(identified, dict):
        forbidden |= any(
            c["name"] == identified["tool"] and c["result"].get("ok") for c in calls
        )
    s["forbidden"] = forbidden
    s["identified_ok"] = identified_ok
    unrequested = _unrequested_mutations(scenario, calls, mutating)
    s["unrequested_mutations"] = unrequested
    if world_after is None or world_before is None:
        s["final_ok"] = True
    else:
        s["final_ok"] = _final_ok(scenario, world_before, world_after, calls)
    if scenario.get("answer_mode") == "refusal":
        s["answer_ok"] = _refusal_answer(answer)
    elif identified_ok:
        s["answer_ok"] = True
    else:
        phrases = scenario.get("answer_any")
        s["answer_ok"] = True if not phrases else any(p.lower() in lowered for p in phrases)
    s["pass"] = bool(
        s["goal_ok"] and s["also_ok"] and not s["forbidden"] and s["answer_ok"]
        and not s["hallucinated_tools"] and not unrequested and s["final_ok"]
    )
    return s


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def run_scenario(args, key, scenario, system_prompt, catalogs, world_spec, mutating, names) -> dict:
    world = World(world_spec, mutating, names)
    world_before = world.snapshot()
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": scenario["prompt"]}]
    calls: list[dict] = []
    usage: list[dict] = []
    answer, error, text_tool_suspect = "", None, False
    started = time.monotonic()
    for _turn in range(args.max_turns):
        try:
            payload, wall, attempts = chat(args, key, messages, catalogs[bool(world.lease)])
        except (RuntimeError, json.JSONDecodeError) as exc:
            error = str(exc) if str(exc) else exc.__class__.__name__
            break
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage.append({
            "wall_s": round(wall, 2),
            "attempts": attempts,
            "finish": choice.get("finish_reason"),
            **{k: v for k, v in (payload.get("usage") or {}).items() if isinstance(v, (int, float))},
        })
        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""
        if not tool_calls:
            answer = content
            text_tool_suspect = bool(re.search(r"<tool_call>|\"arguments\"\s*:|\"name\"\s*:\s*\"[a-z_]+\"", content))
            if choice.get("finish_reason") == "length":
                error = "length"  # cut by max_tokens: the run does not count
            break
        assistant = {"role": "assistant", "content": content, "tool_calls": tool_calls}
        if args.echo_reasoning and message.get("reasoning_content"):
            assistant["reasoning_content"] = message["reasoning_content"]
        messages.append(assistant)
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            raw = fn.get("arguments") or "{}"
            record = {"name": name, "raw_args": raw, "json_error": False, "schema_errors": []}
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(parsed, dict):
                    raise ValueError("arguments are not an object")
            except (ValueError, json.JSONDecodeError):
                parsed, record["json_error"] = {}, True
            record["args"] = parsed
            schema = args.schemas.get(name)
            if schema is not None and not record["json_error"]:
                record["schema_errors"] = validate(schema, parsed)
            if record["json_error"]:
                result = {"ok": False, "error": "bad_args", "detail": "arguments are not a JSON object"}
            elif record["schema_errors"]:
                result = {"ok": False, "error": "bad_args", "detail": record["schema_errors"][:3]}
            else:
                result = world.call(name, parsed)
            record["result"] = result
            calls.append(record)
            tool_msg = {"role": "tool", "tool_call_id": tc.get("id", ""), "content": json.dumps(result, ensure_ascii=False)}
            if args.tool_msg_name:
                tool_msg["name"] = name
            messages.append(tool_msg)
        if args.sleep:
            time.sleep(args.sleep)
    else:
        error = error or "max_turns"
    result = score(scenario, calls, answer, mutating, world.snapshot(), world_before)
    result.update({"error": error, "text_tool_call_suspect": text_tool_suspect})
    result["pass"] = result["pass"] and error is None  # transport errors are rerun, not scored
    return {
        "scenario": scenario["id"],
        "title": scenario["title"],
        "kind": scenario["kind"],
        "model": args.model,
        "catalog": args.catalog_name,
        "score": result,
        "wall_s": round(time.monotonic() - started, 2),
        "usage": usage,
        "calls": [{k: c[k] for k in ("name", "args", "json_error", "schema_errors", "result")} for c in calls],
        "answer": answer,
        "world_after": world.snapshot(),
    }


def load_catalog(path: str) -> list[dict]:
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    return catalog["tools"] if isinstance(catalog, dict) else catalog


def as_tools(entries: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
            },
        }
        for t in entries
    ]


def mutating_names(entries: list[dict]) -> set[str]:
    found = {t["name"] for t in entries if (t.get("description") or "").startswith("Requires a lease")}
    return found | PLAYER_VERBS | {"world_time_set", "world_weather_set", "notify_players", "object_delete", "world_spawn", "inventory_give"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="", help="name of the env var holding the key; empty = no key")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--catalog-after-lease", default="", help="catalog listed while a lease is held")
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=None, help="omitted from the request when not given")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--sleep", type=float, default=0.0, help="seconds between turns (rate limits)")
    parser.add_argument("--tool-msg-name", action="store_true", help="add name to tool messages")
    parser.add_argument(
        "--echo-reasoning",
        action="store_true",
        help="send reasoning_content back inside the tool loop (some thinking APIs require it)",
    )
    args = parser.parse_args()

    key = None
    if args.api_key_env:
        key = os.environ.get(args.api_key_env)
        if not key:
            print(f"env var {args.api_key_env} is empty", file=sys.stderr)
            return 2

    entries = load_catalog(args.catalog)
    after = load_catalog(args.catalog_after_lease) if args.catalog_after_lease else entries
    args.catalog_name = Path(args.catalog).stem + (
        f"+{Path(args.catalog_after_lease).stem}" if args.catalog_after_lease else ""
    )
    # Hidden tools stay callable, as in the real registry; the after-lease schema wins.
    args.schemas = {t["name"]: t.get("inputSchema") or {"type": "object"} for t in entries + after}
    catalogs = {False: as_tools(entries), True: as_tools(after)}
    tools = catalogs[False]
    names = set(args.schemas)
    mutating = mutating_names(entries + after)

    spec = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    scenarios = [s for s in spec["scenarios"] if not wanted or s["id"] in wanted]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    with (out / "runs.jsonl").open("a", encoding="utf-8") as sink:
        for rep in range(args.repeat):
            for scenario in scenarios:
                record = run_scenario(
                    args, key, scenario, spec["meta"]["system_prompt"], catalogs,
                    spec["world"], mutating, names,
                )
                record["repeat"] = rep
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()
                runs.append(record)
                sc = record["score"]
                print(
                    f"{record['scenario']} rep{rep} pass={sc['pass']} goal={sc['goal_ok']} calls={sc['calls']} "
                    f"schema_err={sc['schema_errors']} lease_req={sc['lease_required_hits']} "
                    f"forbidden={sc['forbidden']} without_uid={sc['acted_without_uid']} "
                    f"error={sc['error']} wall={record['wall_s']}s",
                    flush=True,
                )

    prompt_tokens = [u.get("prompt_tokens") for r in runs for u in r["usage"] if u.get("prompt_tokens")]
    summary = {
        "model": args.model,
        "catalog": args.catalog_name,
        "tools": len(tools),
        "tools_after_lease": len(catalogs[True]),
        "runs": len(runs),
        "passed": sum(r["score"]["pass"] for r in runs),
        "transport_errors": sum(1 for r in runs if r["score"]["error"] not in (None, "max_turns", "length")),
        "truncated_runs": sum(1 for r in runs if r["score"]["error"] == "length"),
        "hallucinated_tool_runs": sum(1 for r in runs if r["score"]["hallucinated_tools"]),
        "schema_error_calls": sum(r["score"]["schema_errors"] for r in runs),
        "wrong_player_calls": sum(r["score"]["wrong_player"] for r in runs),
        "acted_without_uid_runs": sum(1 for r in runs if r["score"]["acted_without_uid"]),
        "lease_required_hits": sum(r["score"]["lease_required_hits"] for r in runs),
        "released_runs": sum(r["score"]["released"] for r in runs),
        "max_prompt_tokens": max(prompt_tokens) if prompt_tokens else None,
        "median_wall_s": sorted(r["wall_s"] for r in runs)[len(runs) // 2] if runs else None,
        "by_scenario": {r["scenario"]: r["score"]["pass"] for r in runs if r["repeat"] == 0},
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        f"# {args.model} · {args.catalog_name} ({len(tools)} tools)",
        "",
        f"Pasan {summary['passed']} de {summary['runs']}. Errores de transporte: {summary['transport_errors']}. "
        f"Cortadas por max_tokens: {summary['truncated_runs']}. "
        f"Prompt máximo: {summary['max_prompt_tokens']} tokens.",
        "",
        "| Escenario | Pasa | Objetivo | Llamadas | Esquema mal | lease_required | Prohibido | Sin uid | Error |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        sc = r["score"]
        lines.append(
            f"| {r['scenario']} {r['title']} | {sc['pass']} | {sc['goal_ok']} | {sc['calls']} | "
            f"{sc['schema_errors']} | {sc['lease_required_hits']} | {sc['forbidden']} | "
            f"{sc['acted_without_uid']} | {sc['error'] or ''} |"
        )
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    failed = any(not r["score"]["pass"] for r in runs)
    errored = any(r["score"]["error"] for r in runs)
    return 1 if failed or errored else 0


if __name__ == "__main__":
    sys.exit(main())
