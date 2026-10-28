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
  ``world_time_set`` and ``world_weather_set`` change the scripted world,
  and a later read (``query_all_players``, ``query_player_state``,
  ``world_time_get``, ``world_weather_get``) returns the new value. The
  seed answered those writes with ``applied`` and left the reads stale,
  so a model that checked its work looped until ``max_turns`` (S05, S06,
  S12).
* The scripted log does not carry a player name or a Steam id. S11 is a
  negative: the model cannot learn Survivor_Ana's uid from the log.
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
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
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
        if name not in self.catalog_names and name not in self._read_names():
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
        if name == "world_weather_get":
            return dict({"ok": True}, **self.spec["weather"])
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

    @staticmethod
    def _read_names() -> set[str]:
        """Reads the simulator always answers, even when the catalog hid them.

        A model that re-reads weather after ``world_weather_set`` must see
        the value it wrote. Hiding the getter was how S05 looped.
        """
        return {"world_weather_get", "world_time_get", "query_player_state", "query_all_players"}

    def _player_state(self, args: dict) -> dict:
        uid = args.get("uid") or ""
        player = self.find(uid) if uid else self.players()[0]
        if player is None:
            return {"ok": False, "error": "player_not_found", "uid": uid}
        return {
            "ok": True,
            "uid": player["uid"],
            "name": player["name"],
            "pos": player["pos"],
            "health": player["health"],
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
                payload = json.loads(response.read().decode("utf-8"))
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


def _goal_met(goal: dict, calls: list[dict]) -> bool:
    for call in calls:
        if call["name"] != goal["tool"] or not call["result"].get("ok"):
            continue
        if all(_pred_ok(call["args"], k, p) for k, p in goal.get("args", {}).items()):
            return True
    return False


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


def score(scenario: dict, calls: list[dict], answer: str, mutating: set[str]) -> dict:
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
    s["forbidden"] = forbidden
    phrases = scenario.get("answer_any")
    s["answer_ok"] = True if not phrases else any(p.lower() in lowered for p in phrases)
    s["pass"] = bool(
        s["goal_ok"] and s["also_ok"] and not forbidden and s["answer_ok"] and not s["hallucinated_tools"]
    )
    return s


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def run_scenario(args, key, scenario, system_prompt, catalogs, world_spec, mutating, names) -> dict:
    world = World(world_spec, mutating, names)
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": scenario["prompt"]}]
    calls: list[dict] = []
    usage: list[dict] = []
    answer, error, text_tool_suspect = "", None, False
    started = time.monotonic()
    for _turn in range(args.max_turns):
        try:
            payload, wall, attempts = chat(args, key, messages, catalogs[bool(world.lease)])
        except RuntimeError as exc:
            error = str(exc)
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
    result = score(scenario, calls, answer, mutating)
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
    names = set(args.schemas) | World._read_names()
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
