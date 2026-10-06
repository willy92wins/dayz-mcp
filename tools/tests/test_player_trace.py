"""Inbox fb-20261001-013706-4ae5: player_trace, a per-frame trace of the local player.

MCPPlayerTrace (addon/scripts/4_World/MCP_PlayerTrace.c) samples the local
player on the owner client from MCPClientBridge.OnTick on every frame, never
from a job, a vehicle or CommandHandler, so it runs seated or not and needs no
window focus. Its lifecycle is vehicle_trace's: start, status, stop with an
autodump, dump, read by cursor and limit, clear. These tests pin the wire
contract, the census, the prune contract, the Python normalization and the
Enforce source the game will compile. They do not launch DayZ; the verb is
tested in game after the promotion.
"""
from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, player_trace, result_prune, server, vehicle_trace
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census
from tests.fence_helpers import bind_both_peers
from tests.mcp_helpers import _content_json


COMMAND = "player_trace"
FIELD = "player_trace"
SCRIPTS = addon_root() / "scripts"
TRACE_PATH = SCRIPTS / "4_World" / "MCP_PlayerTrace.c"
CAR_PATH = SCRIPTS / "4_World" / "MCP_CarScript.c"
CLIENT_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
VANILLA = Path(r"P:\scripts")
TRACE_ID = "0123456789abcdef0123456789abcdef"
WIRE_KEYS = {"mode", "trace_id", "cursor", "limit", "sample_hz", "max_samples"}

EXPECTED_ENTITY_MEMBERS = [
    ("string", "type"),
    ("string", "class_name"),
    ("int", "net_id_low"),
    ("int", "net_id_high"),
    ("ref array<float>", "pos"),
]
EXPECTED_SAMPLE_MEMBERS = [
    ("int", "sequence"),
    ("float", "monotonic_s"),
    ("float", "sample_dt_s"),
    ("int", "tick"),
    ("ref array<float>", "pos"),
    ("ref array<float>", "vel"),
    ("float", "heading_deg"),
    ("float", "yaw_deg"),
    ("bool", "falling"),
    ("ref MCPPlayerTraceEntity", "floor"),
    ("ref MCPPlayerTraceEntity", "linked"),
    ("bool", "sliding_off_linked"),
    ("ref MCPPlayerTraceEntity", "parent"),
    ("int", "command_type_id"),
    ("string", "command"),
    ("int", "stance_idx"),
    ("int", "movement_idx"),
]
EXPECTED_READ_MEMBERS = [
    ("string", "schema"),
    ("string", "mode"),
    ("string", "trace_id"),
    ("bool", "active"),
    ("bool", "complete"),
    ("bool", "overflow"),
    ("string", "stop_reason"),
    ("int", "sample_hz"),
    ("int", "capacity"),
    ("int", "count"),
    ("float", "start_monotonic_s"),
    ("string", "player_type"),
    ("int", "net_id_low"),
    ("int", "net_id_high"),
    ("int", "cursor"),
    ("int", "next_cursor"),
    ("bool", "eof"),
    ("string", "path"),
    ("int", "rows"),
    ("ref array<ref MCPPlayerTraceSample>", "samples"),
]
# The engine's command ids (dayzplayer.c:695-708), each named in lower case.
COMMAND_IDS = (
    "NONE", "MOVE", "ACTION", "MELEE", "MELEE2", "FALL", "DEATH", "DAMAGE",
    "LADDER", "UNCONSCIOUS", "SWIM", "VEHICLE", "CLIMB", "SCRIPT",
)

# Signatures, by unique text.
DISPATCH = "protected bool DispatchPlayerTrace(MCPCommand command, MCPResult result)"
DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
ON_TICK = "void OnTick(float timeslice)"
SHUTDOWN = "void Shutdown()"
START = "static bool Start(PlayerBase player, string traceId, int sampleHz, int maxSamples)"
TICK = "static void Tick(int bridgeTick)"
CHECK = "static bool CheckPlayer()"
STOP = "static bool Stop(string traceId)"
CLEAR = "static bool Clear(string traceId)"
ABORT = "static void Abort(string reason)"
FAIL = "static void Fail(string reason)"
MATCHES = "static bool Matches(string traceId)"
DUMP = "static bool Dump(string traceId)"
VIEW = "static MCPPlayerTraceRead View(string mode, string traceId, int cursor, int limit)"
CAPTURE = "protected static void CaptureNow(float nowS, int bridgeTick)"
COMPASS = "protected static float CompassDeg(float headingRad)"
DESCRIBE = "protected static MCPPlayerTraceEntity DescribeEntity(IEntity entity)"
COMMAND_NAME = "protected static string CommandName(int commandId)"
CLEAR_STATE = "protected static void ClearState()"
TRACE_METHODS = (
    START, TICK, CHECK, STOP, CLEAR, ABORT, FAIL, MATCHES, DUMP, VIEW, CAPTURE,
    COMPASS, DESCRIBE, COMMAND_NAME, CLEAR_STATE,
)
VEHICLE_CAPTURE = "static void Capture(CarScript car, float dt)"

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
_LOCAL_TYPES = (
    "string", "int", "float", "bool", "vector", "PlayerBase", "HumanInputController",
    "FileHandle", "JsonSerializer", "Object", "MCPArgs", "MCPPlayerTraceSample",
    "MCPPlayerTraceRead", "MCPPlayerTraceEntity",
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:" + "|".join(_LOCAL_TYPES) + r")\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)


def _block_after(source: str, start: int) -> str:
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block at offset {start}")


def _method_body(source: str, signature: str) -> str:
    if source.count(signature) != 1:
        raise AssertionError(f"{signature!r} occurs {source.count(signature)} times")
    return _block_after(source, source.index(signature))


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    if source.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {source.count(needle)} times")
    return _block_after(source, source.index(needle))


def _flat(text: str) -> str:
    return " ".join(text.split())


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    clean = _without_comments(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", clean)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    body = _block_after(clean, match.end())
    return [(m.group("type"), m.group("name")) for m in _MEMBER_RE.finditer(body)]


def _locals_declared_at_top(body: str) -> list[str]:
    """Local names, failing when one follows a statement or is declared twice."""
    names: list[str] = []
    statement_seen = False
    for raw in _without_comments(body).splitlines():
        line = raw.strip()
        if not line or line in ("{", "}"):
            continue
        match = _LOCAL_DECLARATION.match(raw)
        if match is None:
            statement_seen = True
            continue
        if statement_seen:
            raise AssertionError(f"declaration after a statement: {line}")
        if match.group(1) in names:
            raise AssertionError(f"local declared twice: {match.group(1)}")
        names.append(match.group(1))
    return names


def _in_order(test: unittest.TestCase, body: str, *tokens: str) -> None:
    positions = []
    for token in tokens:
        test.assertIn(token, body)
        positions.append(body.index(token))
    test.assertEqual(positions, sorted(positions), tokens)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


def _wire(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "mode": "read",
        "trace_id": TRACE_ID,
        "cursor": 0,
        "limit": 64,
        "sample_hz": 20,
        "max_samples": 4096,
    }
    args.update(overrides)
    return args


def _entity(**fields: object) -> dict[str, object]:
    entity: dict[str, object] = {
        "type": "Boat_01_Orange",
        "class_name": "Boat_01_Orange",
        "net_id_low": 1234,
        "net_id_high": 5,
        "pos": [7000.0, 0.5, 2500.0],
    }
    entity.update(fields)
    return entity


def _sample(sequence: int = 0, **fields: object) -> dict[str, object]:
    sample: dict[str, object] = {
        "sequence": sequence,
        "monotonic_s": 100.0 + sequence * 0.05,
        "sample_dt_s": 0.0 if sequence == 0 else 0.05,
        "tick": 500 + sequence,
        "pos": [1000.0, 10.0, 2000.0],
        "vel": [0.0, 0.0, 1.4],
        "heading_deg": 90.0,
        "yaw_deg": 91.0,
        "falling": 0,
        "floor": {},
        "linked": {},
        "sliding_off_linked": 0,
        "parent": {},
        "command_type_id": 1,
        "command": "move",
        "stance_idx": 0,
        "movement_idx": 1,
    }
    sample.update(fields)
    return sample


def _trace(mode: str = "read", samples: list[object] | None = None, **fields: object) -> dict[str, object]:
    """An MCPPlayerTraceRead as View answers it: the page is samples cursor..next_cursor."""
    page = [_sample(0), _sample(1)] if samples is None else samples
    cursor = fields.get("cursor", 0)
    end = cursor + len(page) if isinstance(cursor, int) else len(page)
    trace: dict[str, object] = {
        "schema": "dayz-mcp-player-trace-v1",
        "mode": mode,
        "trace_id": TRACE_ID,
        "active": 1,
        "complete": 0,
        "overflow": 0,
        "stop_reason": "",
        "sample_hz": 20,
        "capacity": 4096,
        "count": max(2, end),
        "start_monotonic_s": 99.9,
        "player_type": "SurvivorM_Mirek",
        "net_id_low": 11,
        "net_id_high": 22,
        "cursor": 0,
        "next_cursor": end,
        "eof": 0,
        "path": "",
        "rows": 0,
        "samples": page,
    }
    trace.update(fields)
    return trace


def _dump_trace(**fields: object) -> dict[str, object]:
    """A dump answer: View("dump", id, 0, 1) after Dump, so no samples and rows == count."""
    dumped: dict[str, object] = {
        "active": 0,
        "complete": 1,
        "stop_reason": "requested",
        "path": player_trace.dump_profile_path(TRACE_ID),
        "rows": 2,
    }
    dumped.update(fields)
    return _trace("dump", samples=[], **dumped)


def _bridge_codes() -> set[str]:
    """Every error code the client bridge can answer for this verb, from the sources."""
    dispatch = _method_body(_source(CLIENT_PATH), DISPATCH)
    codes = set(re.findall(r'result\.error = "(\w+)"', dispatch))
    codes |= set(re.findall(r's_LastError = "(\w+)"', _without_comments(_source(TRACE_PATH))))
    codes.add("client_not_in_game")  # the Dispatch gate before every verb
    return codes


class _Return(Exception):
    """A `return;` reached while walking an Enforce body."""


def _block_span(source: str, start: int) -> tuple[str, int]:
    """The brace block at or after `start`: its content and the index after it."""
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index], index + 1
    raise AssertionError(f"unterminated block at offset {start}")


def _parse_statements(code: str) -> list[tuple]:
    """Comment-free Enforce of statements and if/else blocks, as walkable nodes:
    ("do", statement) and ("if", condition, then_nodes, else_nodes)."""
    nodes: list[tuple] = []
    index = 0
    while index < len(code):
        if code[index].isspace():
            index += 1
            continue
        if code.startswith("if (", index):
            depth = 0
            close = index + 3
            while True:
                if code[close] == "(":
                    depth += 1
                elif code[close] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                close += 1
            condition = " ".join(code[index + 4 : close].split())
            then_code, index = _block_span(code, close)
            else_nodes: list[tuple] = []
            rest = code[index:].lstrip()
            if re.match(r"else\b", rest):
                if re.match(r"else\s+if\b", rest):
                    raise AssertionError("else if is not walked")
                else_code, index = _block_span(code, len(code) - len(rest) + len("else"))
                else_nodes = _parse_statements(else_code)
            nodes.append(("if", condition, _parse_statements(then_code), else_nodes))
            continue
        end = code.index(";", index)
        nodes.append(("do", " ".join(code[index : end + 1].split())))
        index = end + 1
    return nodes


class _DecimationWalk:
    """Runs the per-frame decimation branches of a trace's Enforce body.

    Every statement and condition the body has must be transcribed below, so an
    edit to those branches fails here until the walk learns it. A frame moves
    the clock by its length: GetTickTime() is the running sum, the body's
    timeslice or dt is the frame length, and CaptureNow records one sample at
    that time (CaptureNow sets s_LastSampleS and counts it). The game, the
    local player and the car owner stay valid. Python doubles, not float32.
    """

    def __init__(self, sample_hz: int) -> None:
        self.sample_hz = sample_hz
        self.statics = {"s_Active": True, "s_Count": 0, "s_LastSampleS": 0.0, "s_AccumS": 0.0, "s_NextDueS": 0.0}
        self.clock = 0.0
        self.frame = 0.0
        self.locals: dict[str, float] = {}
        self.samples: list[float] = []

    def run(self, nodes: list[tuple], frames: list[float]) -> list[float]:
        for frame in frames:
            self.clock += frame
            self.frame = frame
            self.locals = {}
            try:
                self._run(nodes)
            except _Return:
                pass
        return self.samples

    def _run(self, nodes: list[tuple]) -> None:
        for node in nodes:
            if node[0] == "do":
                self._do(node[1])
            elif self._holds(node[1]):
                self._run(node[2])
            else:
                self._run(node[3])

    def _do(self, statement: str) -> None:
        state = self.statics
        local = self.locals
        if re.fullmatch(r"(?:float|int) \w+;", statement):
            return
        statement = re.sub(r"^(?:float|int) (\w+ =)", r"\1", statement)
        if statement == "return;":
            raise _Return()
        if statement.startswith("Fail("):
            raise AssertionError(f"the walk failed the trace: {statement}")
        if statement == "nowS = GetGame().GetTickTime();":
            local["nowS"] = self.clock
        elif statement == "intervalS = 1.0 / s_SampleHz;":
            local["intervalS"] = 1.0 / self.sample_hz
        elif statement in ("s_AccumS = s_AccumS + timeslice;", "s_AccumS = s_AccumS + dt;"):
            state["s_AccumS"] += self.frame
        elif statement == "s_AccumS = s_AccumS - intervalS;":
            state["s_AccumS"] -= local["intervalS"]
        elif statement == "s_AccumS = 0.0;":
            state["s_AccumS"] = 0.0
        elif statement == "s_NextDueS = nowS + intervalS;":
            state["s_NextDueS"] = local["nowS"] + local["intervalS"]
        elif statement == "s_NextDueS = s_NextDueS + intervalS;":
            state["s_NextDueS"] += local["intervalS"]
        elif statement in ("CaptureNow(nowS, bridgeTick);", "CaptureNow(car, false);"):
            self.samples.append(local["nowS"])
            state["s_LastSampleS"] = local["nowS"]
            state["s_Count"] += 1
        else:
            raise AssertionError(f"the walk has no transcription for: {statement}")

    def _holds(self, condition: str) -> bool:
        state = self.statics
        local = self.locals
        if condition in ("!s_Active", "!s_Active || car != s_Car"):
            return not state["s_Active"]
        if condition in ("!GetGame()", "!CheckPlayer()", "!car.IsOwner()"):
            return False
        if condition == "s_Count > 0":
            return state["s_Count"] > 0
        if condition == "s_Count > 0 && s_AccumS < intervalS":
            return state["s_Count"] > 0 and state["s_AccumS"] < local["intervalS"]
        if condition == "s_Count > 0 && nowS < s_LastSampleS":
            return state["s_Count"] > 0 and local["nowS"] < state["s_LastSampleS"]
        if condition == "s_Count > 0 && nowS == s_LastSampleS":
            return state["s_Count"] > 0 and local["nowS"] == state["s_LastSampleS"]
        if condition == "s_Count > 0 && nowS < s_NextDueS":
            return state["s_Count"] > 0 and local["nowS"] < state["s_NextDueS"]
        if condition == "s_Count == 0 || nowS - s_NextDueS >= intervalS":
            return state["s_Count"] == 0 or local["nowS"] - state["s_NextDueS"] >= local["intervalS"]
        raise AssertionError(f"the walk has no transcription for the condition: {condition}")


def _most_in_one_second(samples: list[float]) -> int:
    """The most samples any one-second window [t, t + 1) holds."""
    most = 0
    start = 0
    for end, time in enumerate(samples):
        while time - samples[start] >= 1.0:
            start += 1
        most = max(most, end - start + 1)
    return most


class PlayerTraceIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)

    def test_client_command_that_needs_a_lease(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))
        status, body = self.state.enqueue_command(COMMAND, _wire())
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "client", COMMAND))
        self.assertEqual(
            self.state.enqueue_command(COMMAND, _wire(), peer="server"),
            (400, {"error": "bad_peer"}),
        )

    def test_one_exact_variant_carries_every_key_in_every_mode(self) -> None:
        variants, delegated = loopback._COMMAND_ARG_SCHEMAS[COMMAND]
        self.assertIsNone(delegated)
        self.assertEqual(len(variants), 1)
        required, optional, validators = variants[0]
        self.assertEqual(set(required), WIRE_KEYS)
        self.assertEqual(optional, frozenset())
        self.assertEqual(set(validators), WIRE_KEYS)
        for mode in sorted(player_trace.TRACE_MODES):
            with self.subTest(mode=mode):
                status, body = self.state.enqueue_command(COMMAND, _wire(mode=mode))
                self.assertEqual((status, body["peer"]), (200, "client"))

    def test_bad_payloads_are_refused_at_the_ingress(self) -> None:
        refused = [
            _wire(mode="START"),
            _wire(mode="begin"),
            _wire(trace_id=""),
            _wire(trace_id=TRACE_ID.upper()),
            _wire(trace_id=TRACE_ID[:-1]),
            _wire(trace_id=TRACE_ID + "0"),
            _wire(cursor=-1),
            _wire(cursor=True),
            _wire(cursor=1.5),
            _wire(limit=0),
            _wire(limit=65),
            _wire(limit=True),
            _wire(sample_hz=19),
            _wire(sample_hz=61),
            _wire(sample_hz=20.0),
            _wire(max_samples=1),
            _wire(max_samples=8193),
            _wire(path="C:\\trace.jsonl"),
            _wire(samples=[]),
            {key: value for key, value in _wire().items() if key != "limit"},
            {key: value for key, value in _wire().items() if key != "trace_id"},
        ]
        for args in refused:
            with self.subTest(args=args):
                self.assertEqual(
                    self.state.enqueue_command(COMMAND, args), (400, {"error": "bad_args"})
                )


class PlayerTraceCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)

    def test_client_caps_and_dispatch_name_the_command(self) -> None:
        caps = announced_caps(self.source)
        self.assertIn(COMMAND, caps)
        names = dispatch_census(self.source)
        self.assertIn(COMMAND, names)
        self.assertEqual(names[-1], "ui_dialog")
        dispatch = _method_body(self.source, DISPATCH_CHAIN)
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), "postNow = DispatchPlayerTrace(command, result);")
        _in_order(
            self,
            dispatch,
            'result.error = "client_not_in_game";',
            f'command.cmd == "{COMMAND}"',
            'command.cmd == "ui_dialog"',
        )

    def test_the_server_bridge_neither_implements_nor_announces_it(self) -> None:
        self.assertNotIn(COMMAND, _source(SERVER_BRIDGE_PATH))
        self.assertNotIn("MCPPlayerTrace", _source(SERVER_BRIDGE_PATH))
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertNotIn(COMMAND, fixture["peers"]["server"])
        self.assertEqual(fixture["peers"]["client"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["server"])
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["client"].get(COMMAND), COMMAND)

    def test_the_version_and_the_server_arg_contract_hash_stay(self) -> None:
        # A client command: the hash covers the server's arguments only.
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "e5a0ed288dbae72f")
        self.assertIn('const string MCP_BRIDGE_VERSION = "11";', _source(MESSAGES_PATH))

    def test_a_stale_client_census_names_the_missing_tool(self) -> None:
        registered = frozenset(tool for tool in _BRIDGE_COMMAND_TOOLS["client"].values() if tool)
        announced = sorted(_BRIDGE_COMMAND_TOOLS["client"])
        announced.remove(COMMAND)
        result = server._compare_bridge_capabilities(
            "client",
            {"state": "announced", "reason": "ok", "announced_commands": announced},
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])


class PlayerTraceLifecycleContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(TRACE_PATH)
        self.code = _without_comments(self.source)
        self.car = _source(CAR_PATH)

    def test_bounds_and_names_are_one_python_copy_equal_to_the_enforce_one(self) -> None:
        constants = dict(re.findall(r"static const int (\w+) = (\d+);", self.code))
        self.assertEqual(
            {name: int(value) for name, value in constants.items()},
            {
                "SAMPLE_HZ_MIN": player_trace.SAMPLE_HZ_MIN,
                "SAMPLE_HZ_MAX": player_trace.SAMPLE_HZ_MAX,
                "MAX_SAMPLES_MIN": player_trace.MAX_SAMPLES_MIN,
                "MAX_SAMPLES_MAX": player_trace.MAX_SAMPLES_MAX,
                "LIMIT_MAX": player_trace.LIMIT_MAX,
            },
        )
        # vehicle_trace's bounds (MCPVehicleTrace.Start, DispatchVehicleTrace).
        self.assertEqual(
            (
                player_trace.SAMPLE_HZ_MIN,
                player_trace.SAMPLE_HZ_MAX,
                player_trace.MAX_SAMPLES_MIN,
                player_trace.MAX_SAMPLES_MAX,
                player_trace.LIMIT_MIN,
                player_trace.LIMIT_MAX,
            ),
            (20, 60, 2, 8192, 1, 64),
        )
        self.assertEqual(player_trace.TRACE_MODES, vehicle_trace.TRACE_MODES)
        self.assertEqual(player_trace.TRACE_ID_PATTERN.pattern, vehicle_trace.TRACE_ID_PATTERN.pattern)
        self.assertIn(f'static const string SCHEMA = "{player_trace.TRACE_SCHEMA}";', self.source)
        prefix = player_trace.DUMP_PROFILE_PREFIX + player_trace.DUMP_FILENAME_PREFIX
        self.assertIn(f'static const string DUMP_PREFIX = "{prefix}";', self.source)
        # Its own file, never vehicle_trace's.
        self.assertNotEqual(player_trace.DUMP_FILENAME_PREFIX, vehicle_trace.DUMP_FILENAME_PREFIX)

    def test_start_refuses_then_preallocates_and_records_the_player(self) -> None:
        start = _method_body(self.source, START)
        for condition, code in (
            ("!player", "no_player"),
            ("!player.IsAlive()", "player_dead"),
            ('s_TraceId != ""', "trace_exists"),
            (
                "sampleHz < SAMPLE_HZ_MIN || sampleHz > SAMPLE_HZ_MAX || "
                "maxSamples < MAX_SAMPLES_MIN || maxSamples > MAX_SAMPLES_MAX",
                "bad_args",
            ),
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    _flat(_if_body(start, condition)), f's_LastError = "{code}"; return false;'
                )
        _in_order(
            self,
            start,
            'if (!player)',
            "if (!player.IsAlive())",
            'if (s_TraceId != "")',
            "s_Samples = new array<ref MCPPlayerTraceSample>();",
            "s_Samples.Resize(maxSamples);",
            "sample = new MCPPlayerTraceSample();",
            "sample.pos.Resize(3);",
            "sample.vel.Resize(3);",
            "s_Samples.Set(index, sample);",
            "s_TraceId = traceId;",
            "s_StartMonotonicS = GetGame().GetTickTime();",
            "s_Player = player;",
            "s_PlayerType = player.GetType();",
            "player.GetNetworkID(s_NetIdLow, s_NetIdHigh);",
            "s_Active = true;",
            "return true;",
        )

    def test_tick_is_idle_first_then_checks_the_player_then_keeps_a_due_time(self) -> None:
        # Review F2 (round 1): this sampler departs on purpose from
        # MCPVehicleTrace.Capture's accumulator, which keeps the time slow
        # frames owe and then samples every frame above sample_hz. The test
        # that pinned the two decimations as identical now pins the departure,
        # and that vehicle_trace's own decimation is left as it was.
        tick = _method_body(self.source, TICK)
        self.assertEqual(_flat(_if_body(tick, "!s_Active")), "return;")
        self.assertLess(tick.index("if (!s_Active)"), tick.index("GetGame()"))
        self.assertEqual(_flat(_if_body(tick, "!CheckPlayer()")), "return;")
        self.assertEqual(
            _flat(_if_body(tick, "s_Count > 0 && nowS < s_LastSampleS")),
            'Fail("clock_not_monotonic"); return;',
        )
        self.assertEqual(_flat(_if_body(tick, "s_Count > 0 && nowS < s_NextDueS")), "return;")
        _in_order(
            self,
            tick,
            "if (!s_Active)",
            "if (!GetGame())",
            "if (!CheckPlayer())",
            "nowS = GetGame().GetTickTime();",
            "if (s_Count > 0 && nowS < s_LastSampleS)",
            "if (s_Count > 0 && nowS < s_NextDueS)",
            "intervalS = 1.0 / s_SampleHz;",
            "if (s_Count == 0 || nowS - s_NextDueS >= intervalS)",
            "s_NextDueS = nowS + intervalS;",
            "else",
            "s_NextDueS = s_NextDueS + intervalS;",
            "CaptureNow(nowS, bridgeTick);",
        )
        self.assertNotIn("s_AccumS", self.code)
        self.assertNotIn("timeslice", _without_comments(tick))
        # vehicle_trace keeps its accumulator, unchanged by this PR.
        _in_order(
            self,
            _method_body(self.car, VEHICLE_CAPTURE),
            "s_AccumS = s_AccumS + dt;",
            "if (s_Count > 0 && s_AccumS < intervalS)",
            "if (s_Count > 0 && nowS < s_LastSampleS)",
            "if (s_Count > 0 && nowS == s_LastSampleS)",
            "s_AccumS = s_AccumS - intervalS;",
            "s_AccumS = 0.0;",
            "CaptureNow(car, false);",
        )

    def test_every_static_the_sampler_uses_is_declared(self) -> None:
        # The offline linter does not resolve identifiers, so a static renamed
        # in one place only would pass it and not compile in game.
        declared = set(
            re.findall(r"\bstatic\s+(?:const\s+)?(?:ref\s+)?[\w<> ]+?\s+(s_\w+)\s*;", self.code)
        )
        used = set(re.findall(r"\b(s_\w+)\b", self.code))
        self.assertIn("s_NextDueS", declared)
        self.assertEqual(used - declared, set())

    def test_check_player_stops_on_a_missing_changed_or_dead_player(self) -> None:
        check = _method_body(self.source, CHECK)
        self.assertEqual(_flat(_if_body(check, "!s_Active")), "return false;")
        for condition, reason in (
            ("!live", "player_changed"),
            ("live != s_Player", "player_changed"),
            ("!live.IsAlive()", "player_dead"),
        ):
            with self.subTest(condition=condition):
                self.assertEqual(
                    _flat(_if_body(check, condition)), f'Fail("{reason}"); return false;'
                )
        _in_order(
            self,
            check,
            "if (!s_Active)",
            "live = PlayerBase.Cast(GetGame().GetPlayer());",
            "if (!live)",
            "if (live != s_Player)",
            "if (!live.IsAlive())",
            "return true;",
        )

    def test_clear_abort_fail_and_matches_are_vehicle_traces(self) -> None:
        car_code = _without_comments(self.car)
        for signature in (CLEAR, ABORT, FAIL, MATCHES):
            with self.subTest(method=signature):
                self.assertEqual(
                    _flat(_method_body(self.code, signature)),
                    _flat(_method_body(car_code, signature)),
                )
        self.assertEqual(
            _flat(_if_body(_method_body(self.source, CLEAR), "s_Active")),
            's_LastError = "trace_active"; return false;',
        )
        abort = _method_body(self.source, ABORT)
        _in_order(self, abort, "if (s_Count > 0)", "Dump(s_TraceId);", "ClearState();")

    def test_stop_autodumps_an_active_trace_and_answers_a_stopped_one(self) -> None:
        stop = _method_body(self.source, STOP)
        self.assertEqual(
            _flat(_if_body(stop, "!Matches(traceId)")),
            's_LastError = "trace_not_found"; return false;',
        )
        self.assertEqual(_flat(_if_body(stop, "!s_Active")), "return true;")
        _in_order(
            self,
            stop,
            "if (!s_Active)",
            "s_Active = false;",
            "s_Complete = true;",
            's_StopReason = "requested";',
            "if (!Dump(traceId))",
        )

    def test_dump_writes_the_header_then_every_sample_to_the_canonical_path(self) -> None:
        dump = _method_body(self.source, DUMP)
        _in_order(
            self,
            dump,
            "if (!Matches(traceId))",
            'filePath = DUMP_PREFIX + traceId + ".jsonl";',
            "handle = OpenFile(filePath, FileMode.WRITE);",
            "if (handle == 0)",
            'header = View("dump", traceId, 0, 1);',
            "header.path = filePath;",
            "header.rows = s_Count;",
            "header.samples.Clear();",
            "wrote = serializer.WriteToString(header, false, line);",
            "FPrintln(handle, line);",
            "while (index < s_Count)",
            "wrote = serializer.WriteToString(s_Samples.Get(index), false, line);",
            "s_DumpPath = filePath;",
            "s_DumpRows = s_Count;",
        )
        # Every failure after the open closes the file.
        self.assertEqual(dump.count('s_LastError = "dump_failed";'), 4)
        self.assertEqual(dump.count("CloseFile(handle);"), 4)
        self.assertEqual(
            player_trace.dump_profile_path(TRACE_ID),
            "$profile:dayz_mcp_player_trace_" + TRACE_ID + ".jsonl",
        )

    def test_view_pages_by_cursor_and_limit_and_fills_every_header_field(self) -> None:
        view = _method_body(self.source, VIEW)
        self.assertEqual(
            _flat(_if_body(view, "!Matches(traceId)")),
            's_LastError = "trace_not_found"; return null;',
        )
        self.assertEqual(
            _flat(_if_body(view, "cursor < 0 || cursor > s_Count || limit < 1 || limit > LIMIT_MAX")),
            's_LastError = "bad_args"; return null;',
        )
        read = _if_body(view, 'mode == "read"')
        _in_order(
            self,
            read,
            "end = cursor + limit;",
            "if (end > s_Count)",
            "end = s_Count;",
            "while (index < end)",
            "view.samples.Insert(s_Samples.Get(index));",
        )
        _in_order(self, view, "view.next_cursor = end;", "view.eof = !s_Active && end == s_Count;")
        assigned = set(re.findall(r"\bview\.(\w+) =", view))
        self.assertEqual(assigned, {name for _type, name in EXPECTED_READ_MEMBERS} - {"samples"})


class PlayerTraceDecimationTest(unittest.TestCase):
    """Review F2 (round 1): frame-rate changes walked through the Tick branches."""

    # 30 s unfocused at 20 fps, then 2 s focused at 120 fps: the review's model.
    DROP_THEN_RECOVERY = [1.0 / 20.0] * 600 + [1.0 / 120.0] * 240

    def setUp(self) -> None:
        self.tick = _parse_statements(_without_comments(_method_body(_source(TRACE_PATH), TICK)))
        self.vehicle = _parse_statements(
            _without_comments(_method_body(_source(CAR_PATH), VEHICLE_CAPTURE))
        )

    @staticmethod
    def _between(samples: list[float], start: float, end: float) -> list[float]:
        return [time for time in samples if start + 1e-9 < time <= end + 1e-9]

    def test_a_frame_rate_drop_then_recovery_never_catches_up_above_sample_hz(self) -> None:
        samples = _DecimationWalk(60).run(self.tick, self.DROP_THEN_RECOVERY)
        slow = self._between(samples, -1.0, 30.0)
        first_second = self._between(samples, 30.0, 31.0)
        self.assertEqual(len(slow), 600)  # one sample on every 20 fps frame
        self.assertLessEqual(len(first_second), 60)
        self.assertGreaterEqual(first_second[0] - slow[-1], 1.0 / 60 - 1e-9)
        self.assertLessEqual(_most_in_one_second(samples), 61)
        # Recovered, it keeps 60 Hz: the dropped debt costs no samples after it.
        self.assertIn(len(samples) - len(slow), range(119, 122))
        # The same frames through vehicle_trace's decimation, which keeps the
        # debt: it samples every 120 fps frame, the burst review F2 measured.
        burst = _DecimationWalk(60).run(self.vehicle, self.DROP_THEN_RECOVERY)
        self.assertEqual(len(self._between(burst, 30.0, 31.0)), 120)

    def test_steady_frame_rates_keep_sample_hz(self) -> None:
        for fps, sample_hz, seconds in ((60, 20, 30), (144, 20, 30), (120, 60, 10), (60, 60, 10)):
            with self.subTest(fps=fps, sample_hz=sample_hz):
                frames = [1.0 / fps] * (fps * seconds)
                samples = _DecimationWalk(sample_hz).run(self.tick, frames)
                self.assertIn(len(samples), range(sample_hz * seconds - 1, sample_hz * seconds + 2))
                self.assertLessEqual(_most_in_one_second(samples), sample_hz + 1)
        # Frames slower than sample_hz: one sample on every frame, and no more.
        self.assertEqual(len(_DecimationWalk(60).run(self.tick, [1.0 / 20.0] * 200)), 200)

    def test_the_walk_refuses_a_branch_it_does_not_know(self) -> None:
        for body in ("s_Unknown = 1;", "if (s_Other) { return; }"):
            with self.subTest(body=body):
                with self.assertRaises(AssertionError):
                    _DecimationWalk(20).run(_parse_statements(body), [0.05])


class PlayerTraceSampleContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(TRACE_PATH)
        self.code = _without_comments(self.source)
        self.capture = _method_body(self.source, CAPTURE)

    def test_a_full_trace_stops_before_any_read(self) -> None:
        self.assertEqual(_flat(_if_body(self.capture, "!s_Active || !s_Player")), "return;")
        self.assertEqual(
            _flat(_if_body(self.capture, "s_Count >= s_Capacity")),
            's_Overflow = true; Fail("overflow"); return;',
        )
        self.assertEqual(
            _flat(_if_body(self.capture, "s_Count > 0 && nowS <= s_LastSampleS")),
            'Fail("clock_not_monotonic"); return;',
        )
        _in_order(
            self,
            self.capture,
            "if (s_Count >= s_Capacity)",
            "if (s_Count > 0 && nowS <= s_LastSampleS)",
            "sample = s_Samples.Get(s_Count);",
            "s_Player.PhysicsGetPositionWS()",
        )

    def test_every_field_is_read_from_its_engine_getter(self) -> None:
        _in_order(
            self,
            self.capture,
            "sample.sequence = s_Count;",
            "sample.monotonic_s = nowS;",
            "sample.tick = bridgeTick;",
            "position = s_Player.PhysicsGetPositionWS();",
            "sample.pos.Set(0, position[0]);",
            "sample.pos.Set(2, position[2]);",
            "s_Player.PhysicsGetVelocity(velocity);",
            "sample.vel.Set(0, velocity[0]);",
            "sample.vel.Set(2, velocity[2]);",
            "sample.heading_deg = -1.0;",
            "hic = s_Player.GetInputController();",
            "sample.heading_deg = CompassDeg(hic.GetHeadingAngle());",
            "orientation = s_Player.GetOrientation();",
            "sample.yaw_deg = orientation[0];",
            "sample.falling = s_Player.PhysicsIsFalling(false);",
            "sample.floor = DescribeEntity(s_Player.PhysicsGetFloorEntity());",
            "sample.linked = DescribeEntity(s_Player.PhysicsGetLinkedEntity());",
            "sample.sliding_off_linked = s_Player.PhysicsWasSlidingOffLinkedEntity();",
            "sample.parent = DescribeEntity(s_Player.GetParent());",
            "s_Player.GetMovementState(s_MovementState);",
            "sample.command_type_id = s_MovementState.m_CommandTypeId;",
            "sample.command = CommandName(s_MovementState.m_CommandTypeId);",
            "sample.stance_idx = s_MovementState.m_iStanceIdx;",
            "sample.movement_idx = s_MovementState.m_iMovement;",
            "s_LastSampleS = nowS;",
            "s_Count = s_Count + 1;",
        )
        self.assertEqual(
            _flat(_if_body(self.capture, "hic")),
            "sample.heading_deg = CompassDeg(hic.GetHeadingAngle());",
        )
        assigned = set(re.findall(r"\bsample\.(\w+) =", self.capture))
        self.assertEqual(assigned, {name for _type, name in EXPECTED_SAMPLE_MEMBERS} - {"pos", "vel"})
        # pValidate false: vanilla's per-tick fall start (dayzplayerimplement.c:2540).
        self.assertNotIn("PhysicsIsFalling(true)", self.code)
        self.assertEqual(self.code.count("PhysicsIsFalling("), 1)

    def test_the_compass_matches_vanillas_facing_vector(self) -> None:
        compass = _method_body(self.source, COMPASS)
        self.assertEqual(
            _flat(_without_comments(compass)),
            "float compass; compass = 0.0 - headingRad * Math.RAD2DEG; "
            "if (compass < 0.0) { compass = compass + 360.0; } "
            "if (compass >= 360.0) { compass = compass - 360.0; } return compass;",
        )

        def transcribed(heading: float) -> float:
            value = 0.0 - heading * (180.0 / math.pi)
            if value < 0.0:
                value = value + 360.0
            if value >= 360.0:
                value = value - 360.0
            return value

        # miscgameplayfunctions.c:726-733 turns a heading h into the facing
        # x = cos(h + PI/2), z = sin(h + PI/2); its compass is atan2(x, z).
        for heading in (-math.pi, -2.0, -math.pi / 2, -0.3, 0.0, 0.7, math.pi / 2, 3.0, math.pi):
            with self.subTest(heading=heading):
                facing_x = math.cos(heading + math.pi / 2)
                facing_z = math.sin(heading + math.pi / 2)
                expected = math.degrees(math.atan2(facing_x, facing_z)) % 360.0
                got = transcribed(heading)
                self.assertGreaterEqual(got, 0.0)
                self.assertLess(got, 360.0)
                diff = (got - expected + 180.0) % 360.0 - 180.0
                self.assertAlmostEqual(diff, 0.0, places=9)
        self.assertEqual(math.copysign(1.0, transcribed(0.0)), 1.0)
        self.assertAlmostEqual(transcribed(-math.pi / 2), 90.0)  # facing +X is east

    def test_entities_are_null_or_type_class_net_id_and_origin(self) -> None:
        describe = _method_body(self.source, DESCRIBE)
        self.assertEqual(_flat(_if_body(describe, "!entity")), "return null;")
        self.assertEqual(_flat(_if_body(describe, 'configType != ""')), "described.type = configType;")
        _in_order(
            self,
            describe,
            "if (!entity)",
            "described = new MCPPlayerTraceEntity();",
            "described.class_name = entity.ClassName();",
            "described.type = described.class_name;",
            "asObject = Object.Cast(entity);",
            "configType = asObject.GetType();",
            "asObject.GetNetworkID(lowBits, highBits);",
            "described.net_id_low = lowBits;",
            "described.net_id_high = highBits;",
            "origin = entity.GetOrigin();",
            "described.pos.Insert(origin[0]);",
            "described.pos.Insert(origin[1]);",
            "described.pos.Insert(origin[2]);",
            "return described;",
        )
        # The net id travels as the two ints MCP results already carry.
        self.assertIn(("int", "net_id_low"), _class_members(_source(MESSAGES_PATH), "MCPResult"))

    def test_command_name_names_every_engine_command_id(self) -> None:
        name = _method_body(self.source, COMMAND_NAME)
        pairs = re.findall(
            r'if \(commandId == DayZPlayerConstants\.COMMANDID_(\w+)\)\s*\{\s*return "(\w+)";', name
        )
        self.assertEqual(sorted(pairs), sorted((cid, cid.lower()) for cid in COMMAND_IDS))
        self.assertTrue(name.rstrip().endswith('return "other";'))

    def test_the_sampler_reads_no_health_and_names_no_other_verb(self) -> None:
        for banned in (
            "GetHealth",
            "MCPResult",
            "MCPVehicleTrace",
            "OverrideMovement",
            "ref PlayerBase",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.code)
        self.assertIn("\tstatic PlayerBase s_Player;\n", self.source)
        self.assertNotIn("MCPPlayerTrace", _source(CAR_PATH))

    def test_compile_hazards_locals_on_top_no_local_keyword_no_ternary(self) -> None:
        type_names = set(re.findall(r"\bclass\s+(\w+)", "".join(
            path.read_text(encoding="utf-8") for path in SCRIPTS.rglob("*.c")
        )))
        type_names |= set(_LOCAL_TYPES) | {"IEntity", "Math", "HumanMovementState", "array"}
        for text, signatures in (
            (self.source, TRACE_METHODS),
            (_source(CLIENT_PATH), (DISPATCH,)),
        ):
            for signature in signatures:
                with self.subTest(method=signature):
                    body = _method_body(text, signature)
                    names = _locals_declared_at_top(body)
                    self.assertFalse(set(names) & type_names, names)
                    code = _without_comments(body)
                    self.assertIsNone(re.search(r"\blocal\b", code))
                    self.assertNotIn("?", code)
        for class_name in ("MCPPlayerTraceEntity", "MCPPlayerTraceSample", "MCPPlayerTraceRead"):
            with self.subTest(fields_of=class_name):
                fields = {field for _type, field in _class_members(self.source, class_name)}
                self.assertFalse(fields & type_names, fields & type_names)
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a = 0;\n\t\ta = 1;\n\t\tint b = 0;\n")
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a;\n\t\tint a;\n")


class PlayerTraceDispatchContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)
        self.dispatch = _method_body(self.source, DISPATCH)

    def test_no_seat_is_needed_and_the_bounds_are_the_samplers(self) -> None:
        for banned in ("not_seated", "not_driver", "GetCommand_Vehicle", "IsOwner", "MCPVehicleTrace"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.dispatch)
        _in_order(
            self,
            self.dispatch,
            "if (!command.args)",
            'args.mode != "start" && args.mode != "status" && args.mode != "stop" && '
            'args.mode != "read" && args.mode != "clear" && args.mode != "dump"',
            "if (!IsValidTraceId(args.trace_id) || args.cursor < 0 || args.limit < 1 || "
            "args.limit > MCPPlayerTrace.LIMIT_MAX)",
            "if (args.sample_hz < MCPPlayerTrace.SAMPLE_HZ_MIN || args.sample_hz > "
            "MCPPlayerTrace.SAMPLE_HZ_MAX || args.max_samples < MCPPlayerTrace.MAX_SAMPLES_MIN "
            "|| args.max_samples > MCPPlayerTrace.MAX_SAMPLES_MAX)",
            'if (args.mode == "start")',
        )
        self.assertEqual(
            set(re.findall(r'result\.error = "(\w+)"', self.dispatch)),
            {"bad_args", "bad_mode", "trace_not_found"},
        )

    def test_the_lifecycle_branches_follow_vehicle_trace(self) -> None:
        start = _if_body(self.dispatch, 'args.mode == "start"')
        _in_order(
            self,
            start,
            "player = PlayerBase.Cast(GetGame().GetPlayer());",
            "if (!MCPPlayerTrace.Start(player, args.trace_id, args.sample_hz, args.max_samples))",
            "result.error = MCPPlayerTrace.GetLastError();",
            'result.player_trace = MCPPlayerTrace.View("start", args.trace_id, 0, 1);',
            "result.ok = result.player_trace != null;",
        )
        self.assertEqual(
            _flat(_if_body(self.dispatch, "MCPPlayerTrace.IsActive()")),
            "MCPPlayerTrace.CheckPlayer();",
        )
        _in_order(
            self,
            self.dispatch,
            "if (!MCPPlayerTrace.Matches(args.trace_id))",
            "if (MCPPlayerTrace.IsActive())",
            'if (args.mode == "stop")',
            "if (!MCPPlayerTrace.Stop(args.trace_id))",
            'result.player_trace = MCPPlayerTrace.View("stop", args.trace_id, 0, 1);',
            'else if (args.mode == "clear")',
            'clearView = MCPPlayerTrace.View("clear", args.trace_id, 0, 1);',
            "if (!clearView || !MCPPlayerTrace.Clear(args.trace_id))",
            "result.player_trace = clearView;",
            'else if (args.mode == "dump")',
            "if (!MCPPlayerTrace.Dump(args.trace_id))",
            'result.player_trace = MCPPlayerTrace.View("dump", args.trace_id, 0, 1);',
            "result.player_trace = MCPPlayerTrace.View(args.mode, args.trace_id, args.cursor, args.limit);",
            "if (!result.player_trace)",
            "result.ok = true;",
        )

    def test_on_tick_is_the_only_sampler_and_shutdown_autodumps(self) -> None:
        on_tick = _method_body(self.source, ON_TICK)
        _in_order(
            self,
            on_tick,
            "m_Tick = m_Tick + 1;",
            "MCPAnimTimeline.Tick(timeslice);",
            "MCPPlayerTrace.Tick(m_Tick);",
            "m_JobRunner.Tick(timeslice, this);",
            "if (!m_Configured)",
        )
        every_script = "".join(
            _without_comments(path.read_text(encoding="utf-8")) for path in SCRIPTS.rglob("*.c")
        )
        # One caller, with the one argument the signature takes.
        self.assertEqual(every_script.count("MCPPlayerTrace.Tick("), 1)
        self.assertEqual(_source(TRACE_PATH).count(TICK), 1)
        self.assertNotIn("MCPPlayerTrace.Start(", _without_comments(_source(SERVER_BRIDGE_PATH)))
        shutdown = _method_body(self.source, SHUTDOWN)
        _in_order(
            self,
            shutdown,
            'MCPVehicleTrace.Abort("shutdown");',
            'MCPAnimTimeline.Abort("shutdown");',
            'MCPPlayerTrace.Abort("shutdown");',
        )
        # A vehicle_release aborts the vehicle trace only.
        release = _method_body(self.source, "protected bool DispatchVehicleRelease(MCPCommand command, MCPResult result)")
        self.assertNotIn("MCPPlayerTrace", release)

    def test_the_bridge_answers_these_error_codes(self) -> None:
        self.assertEqual(
            _bridge_codes(),
            {
                "bad_args", "bad_mode", "client_not_in_game", "no_player", "player_dead",
                "trace_exists", "trace_not_found", "trace_active", "dump_failed",
            },
        )


class PlayerTraceMessagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = _source(MESSAGES_PATH)
        self.trace = _source(TRACE_PATH)

    def test_the_result_owns_one_reference_and_the_args_are_reused(self) -> None:
        result_members = _class_members(self.messages, "MCPResult")
        self.assertEqual(result_members.count(("ref MCPPlayerTraceRead", FIELD)), 1)
        self.assertEqual(result_members[-1], ("ref MCPPlayerTraceRead", FIELD))
        self.assertNotIn(FIELD, {name for _type, name in _class_members(self.messages, "MCPJob")})
        args = _class_members(self.messages, "MCPArgs")
        for member in (
            ("string", "mode"),
            ("string", "trace_id"),
            ("int", "cursor"),
            ("int", "limit"),
            ("int", "sample_hz"),
            ("int", "max_samples"),
        ):
            with self.subTest(member=member):
                self.assertEqual(args.count(member), 1)

    def test_the_data_classes_carry_the_contract_members(self) -> None:
        self.assertEqual(_class_members(self.trace, "MCPPlayerTraceEntity"), EXPECTED_ENTITY_MEMBERS)
        self.assertEqual(_class_members(self.trace, "MCPPlayerTraceSample"), EXPECTED_SAMPLE_MEMBERS)
        self.assertEqual(_class_members(self.trace, "MCPPlayerTraceRead"), EXPECTED_READ_MEMBERS)
        code = _without_comments(self.trace)
        self.assertEqual(
            _flat(_method_body(code, "void MCPPlayerTraceSample()")),
            "pos = new array<float>(); vel = new array<float>();",
        )
        self.assertEqual(
            _flat(_method_body(code, "void MCPPlayerTraceRead()")),
            "samples = new array<ref MCPPlayerTraceSample>();",
        )
        # The fixtures below are the wire shape of these classes.
        self.assertEqual(set(_sample()), {name for _type, name in EXPECTED_SAMPLE_MEMBERS})
        self.assertEqual(set(_trace()), {name for _type, name in EXPECTED_READ_MEMBERS})
        self.assertEqual(set(_entity()), {name for _type, name in EXPECTED_ENTITY_MEMBERS})

    def test_the_reply_is_prunable_and_kept_for_its_verb(self) -> None:
        self.assertEqual(result_prune.PRUNABLE_FIELDS[-1], FIELD)
        self.assertNotIn(FIELD, result_prune.OWNED_SCALAR_FIELDS)
        unfilled = result_prune.prune_unfilled_fields("vehicle_trace", {"ok": 1, FIELD: {}})
        self.assertNotIn(FIELD, unfilled)
        kept = result_prune.prune_unfilled_fields(COMMAND, {"ok": 1, FIELD: _trace("status", samples=[])})
        self.assertEqual(kept[FIELD]["samples"], [])
        cleared = result_prune.prune_unfilled_fields(COMMAND, {"ok": 1, FIELD: _trace(), "trace": {}})
        self.assertNotIn("trace", cleared)


class PlayerTraceModuleTest(unittest.TestCase):
    def test_start_generates_the_id_and_every_request_passes_the_ingress(self) -> None:
        first = player_trace.normalize_request("start", "", 0, 64, 20, 4096)
        second = player_trace.normalize_request("start", "", 0, 64, 20, 4096)
        self.assertEqual(set(first), WIRE_KEYS)
        self.assertRegex(first["trace_id"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(first["trace_id"], second["trace_id"])
        self.assertEqual(
            player_trace.normalize_request("read", TRACE_ID, 5, 1, 60, 8192),
            {"mode": "read", "trace_id": TRACE_ID, "cursor": 5, "limit": 1, "sample_hz": 60, "max_samples": 8192},
        )
        for mode in sorted(player_trace.TRACE_MODES):
            with self.subTest(mode=mode):
                trace_id = "" if mode == "start" else TRACE_ID
                args = player_trace.normalize_request(mode, trace_id, 0, 64, 20, 4096)
                self.assertEqual(loopback.validate_command_args(COMMAND, args), (True, None))

    def test_bad_requests_name_their_argument(self) -> None:
        cases = (
            (("begin", "", 0, 64, 20, 4096), "bad_mode"),
            ((1, "", 0, 64, 20, 4096), "bad_mode"),
            (("start", TRACE_ID, 0, 64, 20, 4096), "bad_trace_id"),
            (("read", "", 0, 64, 20, 4096), "bad_trace_id"),
            (("read", TRACE_ID.upper(), 0, 64, 20, 4096), "bad_trace_id"),
            (("read", TRACE_ID[:-1], 0, 64, 20, 4096), "bad_trace_id"),
            (("read", None, 0, 64, 20, 4096), "bad_trace_id"),
            (("read", TRACE_ID, -1, 64, 20, 4096), "bad_cursor"),
            (("read", TRACE_ID, True, 64, 20, 4096), "bad_cursor"),
            (("read", TRACE_ID, 0.0, 64, 20, 4096), "bad_cursor"),
            (("read", TRACE_ID, 0, 0, 20, 4096), "bad_limit"),
            (("read", TRACE_ID, 0, 65, 20, 4096), "bad_limit"),
            (("read", TRACE_ID, 0, True, 20, 4096), "bad_limit"),
            (("read", TRACE_ID, 0, 64, 19, 4096), "bad_sample_hz"),
            (("read", TRACE_ID, 0, 64, 61, 4096), "bad_sample_hz"),
            (("read", TRACE_ID, 0, 64, 20.0, 4096), "bad_sample_hz"),
            (("read", TRACE_ID, 0, 64, 20, 1), "bad_max_samples"),
            (("read", TRACE_ID, 0, 64, 20, 8193), "bad_max_samples"),
            (("read", TRACE_ID, 0, 64, 20, False), "bad_max_samples"),
        )
        for arguments, code in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_request(*arguments)
                self.assertEqual(str(raised.exception), code)
        for trace_id in ("", TRACE_ID.upper(), None):
            with self.subTest(trace_id=trace_id):
                with self.assertRaises(ValueError):
                    player_trace.dump_relpath(trace_id)

    def test_bools_are_typed_and_anything_else_is_refused(self) -> None:
        result = player_trace.normalize_bridge_result(
            {"ok": 1, FIELD: _trace(samples=[_sample(0, falling=1, sliding_off_linked=True)])}
        )
        trace = result[FIELD]
        self.assertEqual(
            [trace[name] for name in ("active", "complete", "overflow", "eof")],
            [True, False, False, False],
        )
        self.assertIs(trace["samples"][0]["falling"], True)
        self.assertIs(trace["samples"][0]["sliding_off_linked"], True)
        for bad in (2, "1", None, 0.5, -1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_bridge_result({"ok": 1, FIELD: _trace(active=bad)})
                self.assertEqual(str(raised.exception), "bad_bridge_trace_boolean: player_trace.active")
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_bridge_result(
                        {"ok": 1, FIELD: _trace(samples=[_sample(0, falling=bad)])}
                    )
                self.assertEqual(
                    str(raised.exception), "bad_bridge_trace_boolean: player_trace.samples[0].falling"
                )
        missing_trace_bool = _trace()
        del missing_trace_bool["eof"]
        missing_sample_bool = _sample()
        del missing_sample_bool["sliding_off_linked"]
        for trace, member in (
            (missing_trace_bool, "player_trace.eof"),
            (_trace(samples=[missing_sample_bool]), "player_trace.samples[0].sliding_off_linked"),
        ):
            with self.subTest(member=member):
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_bridge_result({"ok": 1, FIELD: trace})
                self.assertEqual(str(raised.exception), f"bad_bridge_trace_boolean: {member}")

    def test_an_unset_entity_reads_null_and_a_malformed_one_is_refused(self) -> None:
        boat = _entity()
        result = player_trace.normalize_bridge_result(
            {"ok": 1, FIELD: _trace(samples=[_sample(0, floor=None, linked=dict(boat), parent={})])}
        )
        sample = result[FIELD]["samples"][0]
        self.assertIsNone(sample["floor"])
        self.assertEqual(sample["linked"], boat)
        self.assertIsNone(sample["parent"])
        for bad in ([], "Boat_01_Orange", 0, [1, 2]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_bridge_result(
                        {"ok": 1, FIELD: _trace(samples=[_sample(0, linked=bad)])}
                    )
                self.assertEqual(
                    str(raised.exception), "bad_bridge_trace_result: player_trace.samples[0].linked"
                )
        for field in player_trace.ENTITY_SAMPLE_FIELDS:
            with self.subTest(missing=field):
                sample = _sample()
                del sample[field]
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_bridge_result({"ok": 1, FIELD: _trace(samples=[sample])})
                self.assertEqual(
                    str(raised.exception), f"bad_bridge_trace_result: player_trace.samples[0].{field}"
                )

    def test_a_result_of_the_wrong_shape_is_refused(self) -> None:
        for result, member in (
            (None, "result"),
            ([], "result"),
            ({"ok": 1}, "player_trace"),
            ({"ok": 1, FIELD: []}, "player_trace"),
            ({"ok": 1, FIELD: {k: v for k, v in _trace().items() if k != "samples"}}, "player_trace.samples"),
            ({"ok": 1, FIELD: _trace(samples={})}, "player_trace.samples"),
            ({"ok": 1, FIELD: _trace(samples=[[]])}, "player_trace.samples[0]"),
        ):
            with self.subTest(result=result):
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_bridge_result(result)
                self.assertEqual(str(raised.exception), f"bad_bridge_trace_result: {member}")

    def test_an_incomplete_or_inconsistent_answer_is_refused_with_its_member(self) -> None:
        # Review F1 (round 1): the first three answers came back as valid.
        def without(record: dict[str, object], key: str) -> dict[str, object]:
            return {name: value for name, value in record.items() if name != key}

        def one(**fields: object) -> dict[str, object]:
            return _trace(samples=[_sample(0, **fields)])

        nan = float("nan")
        other_path = player_trace.dump_profile_path("f" * 32)
        cases = (
            ("sample_without_pos", _trace(samples=[without(_sample(0), "pos")]), "result", "samples[0].pos"),
            ("linked_with_only_its_type", one(linked={"type": "Boat_01_Orange"}), "result", "samples[0].linked.class_name"),
            ("dump_count_1_rows_0", _dump_trace(count=1, rows=0), "dump", "rows"),
            ("schema_of_another_version", _trace(schema="dayz-mcp-player-trace-v2"), "result", "schema"),
            ("schema_missing", without(_trace(), "schema"), "result", "schema"),
            ("mode_unknown", _trace(mode="begin"), "result", "mode"),
            ("trace_id_upper_case", _trace(trace_id=TRACE_ID.upper()), "result", "trace_id"),
            ("stop_reason_unknown", _trace(stop_reason="crashed"), "result", "stop_reason"),
            ("sample_hz_out_of_range", _trace(sample_hz=0), "result", "sample_hz"),
            ("capacity_a_float", _trace(capacity=4096.0), "result", "capacity"),
            ("count_above_capacity", _trace(count=5000), "result", "count"),
            ("start_monotonic_s_nan", _trace(start_monotonic_s=nan), "result", "start_monotonic_s"),
            ("player_type_null", _trace(player_type=None), "result", "player_type"),
            ("net_id_low_a_string", _trace(net_id_low="11"), "result", "net_id_low"),
            ("cursor_beyond_count", _trace(samples=[], cursor=3, next_cursor=3, count=2), "result", "cursor"),
            ("next_cursor_before_cursor", _trace(samples=[], cursor=1, next_cursor=0), "result", "next_cursor"),
            ("page_shorter_than_its_cursors", _trace(samples=[_sample(0)], next_cursor=2), "result", "samples"),
            ("rows_above_count", _trace(rows=3), "result", "rows"),
            ("path_of_another_trace", _trace(path=other_path), "result", "path"),
            ("sequence_gap", _trace(samples=[_sample(0), _sample(2)]), "result", "samples[1].sequence"),
            ("monotonic_s_infinite", one(monotonic_s=float("inf")), "result", "samples[0].monotonic_s"),
            ("sample_dt_s_negative", one(sample_dt_s=-0.05), "result", "samples[0].sample_dt_s"),
            ("tick_a_float", one(tick=500.0), "result", "samples[0].tick"),
            ("pos_two_numbers", one(pos=[1.0, 2.0]), "result", "samples[0].pos"),
            ("pos_with_a_string", one(pos=[1.0, "2", 3.0]), "result", "samples[0].pos"),
            ("vel_nan", one(vel=[0.0, nan, 1.4]), "result", "samples[0].vel"),
            ("heading_deg_null", one(heading_deg=None), "result", "samples[0].heading_deg"),
            ("yaw_deg_a_string", one(yaw_deg="91"), "result", "samples[0].yaw_deg"),
            ("command_unknown", one(command="walk"), "result", "samples[0].command"),
            ("command_type_id_a_bool", one(command_type_id=True), "result", "samples[0].command_type_id"),
            ("movement_idx_missing", _trace(samples=[without(_sample(0), "movement_idx")]), "result", "samples[0].movement_idx"),
            ("linked_type_empty", one(linked=_entity(type="")), "result", "samples[0].linked.type"),
            ("floor_net_id_a_string", one(floor=_entity(net_id_low="1234")), "result", "samples[0].floor.net_id_low"),
            ("parent_pos_four_numbers", one(parent=_entity(pos=[1.0, 2.0, 3.0, 4.0])), "result", "samples[0].parent.pos"),
            ("linked_pos_nan", one(linked=_entity(pos=[nan, 0.5, 2500.0])), "result", "samples[0].linked.pos"),
        )
        for label, trace, kind, member in cases:
            with self.subTest(label):
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_bridge_result({"ok": 1, FIELD: trace})
                self.assertEqual(
                    str(raised.exception), f"bad_bridge_trace_{kind}: player_trace.{member}"
                )

    def test_a_complete_answer_passes_as_the_engine_may_write_it(self) -> None:
        # Integral floats as JSON ints, a page that starts past 0, a non-Object
        # entity with network id 0 0, a status read after a dump, every mode.
        integral = _sample(5, pos=[1000, 10, 2000], vel=[0, 0, 1], monotonic_s=100, heading_deg=90, yaw_deg=-91)
        plain = _entity(type="SomeEntity", class_name="SomeEntity", net_id_low=0, net_id_high=0, pos=[1, 2, 3])
        answers = [
            _trace(samples=[integral, _sample(6, parent=plain)], cursor=5, count=9),
            _trace("status", samples=[], path=player_trace.dump_profile_path(TRACE_ID), rows=2),
            _trace(net_id_low=-1, net_id_high=2**31 - 1, start_monotonic_s=0),
            _dump_trace(),
            _dump_trace(count=0, rows=0),
        ]
        answers += [_trace(mode, samples=[]) for mode in sorted(player_trace.TRACE_MODES - {"read", "dump"})]
        for trace in answers:
            with self.subTest(mode=trace["mode"], cursor=trace["cursor"]):
                expected = json.loads(json.dumps(trace))
                result = player_trace.normalize_bridge_result({"ok": 1, FIELD: trace})
                self.assertEqual(len(result[FIELD]["samples"]), len(expected["samples"]))
                self.assertEqual(result[FIELD]["count"], expected["count"])

    def test_the_names_it_accepts_are_the_enforce_ones(self) -> None:
        code = _without_comments(_source(TRACE_PATH))
        reasons = set(re.findall(r'Fail\("(\w+)"\)', code))
        reasons |= set(re.findall(r's_StopReason = "(\w+)"', code))
        reasons |= set(re.findall(r'MCPPlayerTrace\.Abort\("(\w+)"\)', _source(CLIENT_PATH)))
        self.assertEqual(player_trace.STOP_REASONS, reasons | {""})
        names = set(re.findall(r'return "(\w+)";', _method_body(_source(TRACE_PATH), COMMAND_NAME)))
        self.assertEqual(player_trace.COMMAND_NAMES, names)

    def test_a_dump_answer_names_its_canonical_file_and_row_count(self) -> None:
        result = player_trace.normalize_bridge_result({"ok": 1, FIELD: _dump_trace()})
        self.assertEqual(result[FIELD]["path"], "$profile:dayz_mcp_player_trace_" + TRACE_ID + ".jsonl")
        self.assertEqual(result[FIELD]["rows"], 2)
        for fields, member in (
            ({"samples": [_sample()]}, "samples"),
            ({"path": ""}, "path"),
            ({"path": "$profile:dayz_mcp_trace_" + TRACE_ID + ".jsonl"}, "path"),
            ({"path": "C:\\dayz_mcp_player_trace_" + TRACE_ID + ".jsonl"}, "path"),
            ({"path": player_trace.dump_profile_path("f" * 32)}, "path"),
            ({"rows": -1}, "rows"),
            ({"rows": 8193}, "rows"),
            ({"rows": True}, "rows"),
            ({"rows": "2"}, "rows"),
            ({"rows": 1}, "rows"),
            ({"rows": 3, "count": 2}, "rows"),
        ):
            with self.subTest(fields=fields):
                trace = _dump_trace()
                trace.update(fields)
                with self.assertRaises(ValueError) as raised:
                    player_trace.normalize_bridge_result({"ok": 1, FIELD: trace})
                self.assertEqual(str(raised.exception), f"bad_bridge_trace_dump: player_trace.{member}")

    def test_nothing_is_derived_dropped_or_reordered(self) -> None:
        samples = [_sample(index, tick=900 - index) for index in range(5)]
        result = player_trace.normalize_bridge_result({"ok": 1, FIELD: _trace(samples=samples, count=5)})
        out = result[FIELD]["samples"]
        self.assertEqual([sample["tick"] for sample in out], [900, 899, 898, 897, 896])
        self.assertEqual(
            {key: value for key, value in out[3].items() if key not in ("falling", "sliding_off_linked", "floor", "linked", "parent")},
            {
                key: value
                for key, value in _sample(3, tick=897).items()
                if key not in ("falling", "sliding_off_linked", "floor", "linked", "parent")
            },
        )
        self.assertEqual(set(result[FIELD]), {name for _type, name in EXPECTED_READ_MEMBERS})


class PlayerTraceToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _description(self) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[COMMAND].description or ""

    async def _call(self, arguments: dict[str, object], answer: object = None):
        bridge_answer = {"ok": 1, FIELD: _trace()} if answer is None else answer
        call = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(await self.app.call_tool(COMMAND, dict(arguments)))
        return result, call

    async def test_description_states_the_contract_and_its_limits(self) -> None:
        description = await self._description()
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        for fragment in (
            "per-frame trace of the local player on the owner client",
            "It runs seated or not, with no window focus, beside vehicle_trace.",
            "mode=start (no trace_id) returns a new trace_id",
            "mode=start while a trace exists returns trace_exists, so call mode=clear before reuse",
            "clear of an active trace is trace_active (stop first)",
            "mode=read pages samples by cursor and limit (1..64)",
            "$profile:dayz_mcp_player_trace_<trace_id>.jsonl and stop autodumps the same file",
            "Samples come at most at sample_hz (20..60) and at most one per client frame",
            "an unfocused client renders at about 20 fps",
            "each is due one 1/sample_hz interval after the previous due time",
            "a frame a full interval or more late restarts that schedule",
            "the trace never catches up above sample_hz (vehicle_trace's decimation does catch up)",
            "max_samples (2..8192); a full trace stops with overflow=true",
            "monotonic_s (client GetTickTime seconds)",
            "tick (client bridge tick, the counter tick_dispatch reports)",
            "pos (PhysicsGetPositionWS), vel (PhysicsGetVelocity)",
            "heading_deg (the input controller's GetHeadingAngle as a compass heading, "
            "0 north = +Z, 90 east = +X; -1 without an input controller)",
            "yaw_deg (GetOrientation yaw in degrees",
            "falling (PhysicsIsFalling(false)",
            "floor (PhysicsGetFloorEntity)",
            "linked (PhysicsGetLinkedEntity: the boat a player stands on; not valid while parent is set)",
            "parent (GetParent)",
            "each null or {type, class_name, net_id_low, net_id_high, pos}",
            "sliding_off_linked",
            "command_type_id with its name in command, stance_idx and movement_idx (HumanMovementState)",
            "Health is not sampled: in multiplayer the owner client has no synced value.",
            "read answers null",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        # vehicle_trace's seat refusal is not this verb's.
        self.assertNotIn("not_seated", description)

    async def test_description_names_every_stop_reason_and_error(self) -> None:
        description = await self._description()
        code = _without_comments(_source(TRACE_PATH))
        reasons = set(re.findall(r'Fail\("(\w+)"\)', code))
        reasons |= set(re.findall(r's_StopReason = "(\w+)"', code))
        reasons |= set(re.findall(r'MCPPlayerTrace\.Abort\("(\w+)"\)', _source(CLIENT_PATH)))
        self.assertEqual(
            reasons,
            {"requested", "overflow", "clock_not_monotonic", "player_dead", "player_changed", "shutdown"},
        )
        stop_text = description[description.index("stop_reason") : description.index("Errors: ")]
        for reason in sorted(reasons):
            with self.subTest(reason=reason):
                self.assertIn(reason, stop_text)
        errors = description[description.index("Errors: ") :]
        python_codes = {"bad_mode", "bad_trace_id", "bad_cursor", "bad_limit", "bad_sample_hz", "bad_max_samples"}
        # normalize_bridge_result's refusals, each followed by the member that failed.
        python_codes |= {"bad_bridge_trace_result", "bad_bridge_trace_boolean", "bad_bridge_trace_dump"}
        for error in sorted(_bridge_codes() | python_codes):
            with self.subTest(error=error):
                self.assertIn(error, errors)
        self.assertIn("followed by the member that failed", errors)
        self.assertIn("a dump's rows must equal count", errors)

    async def test_start_forwards_a_generated_id_and_every_key(self) -> None:
        _result, call = await self._call({"mode": "start", "timeout_s": 1.0})
        call.assert_awaited_once()
        command, args, peer, timeout = call.await_args.args
        self.assertEqual((command, peer, timeout), (COMMAND, "client", 1.0))
        self.assertEqual(set(args), WIRE_KEYS)
        self.assertRegex(args["trace_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(
            {key: value for key, value in args.items() if key != "trace_id"},
            {"mode": "start", "cursor": 0, "limit": 64, "sample_hz": 20, "max_samples": 4096},
        )
        self.assertEqual(loopback.validate_command_args(COMMAND, args), (True, None))

    async def test_read_forwards_exactly_its_arguments_with_the_default_timeout(self) -> None:
        _result, call = await self._call(
            {"mode": "read", "trace_id": TRACE_ID, "cursor": 5, "limit": 10, "sample_hz": 60, "max_samples": 2}
        )
        expected = {"mode": "read", "trace_id": TRACE_ID, "cursor": 5, "limit": 10, "sample_hz": 60, "max_samples": 2}
        call.assert_awaited_once_with(COMMAND, expected, "client", DEFAULT_TOOL_TIMEOUT_S)
        self.assertEqual(loopback.validate_command_args(COMMAND, expected), (True, None))

    async def test_bad_arguments_raise_before_the_bridge(self) -> None:
        for arguments, code in (
            ({"mode": "begin"}, "bad_mode"),
            ({"mode": "start", "trace_id": TRACE_ID}, "bad_trace_id"),
            ({"mode": "read"}, "bad_trace_id"),
            ({"mode": "read", "trace_id": TRACE_ID.upper()}, "bad_trace_id"),
            ({"mode": "read", "trace_id": TRACE_ID, "cursor": -1}, "bad_cursor"),
            ({"mode": "read", "trace_id": TRACE_ID, "limit": 65}, "bad_limit"),
            ({"mode": "start", "sample_hz": 61}, "bad_sample_hz"),
            ({"mode": "start", "max_samples": 1}, "bad_max_samples"),
        ):
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1, FIELD: _trace()})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, {**arguments, "timeout_s": 1.0})
                self.assertEqual(_tool_error_text(raised.exception), code)
                call.assert_not_awaited()
        # Schema-level refusals never reach the handler either.
        for arguments in (
            {"mode": 1},
            {"mode": "read", "trace_id": TRACE_ID, "limit": True},
            {"mode": "read", "trace_id": TRACE_ID, "cursor": 1.5},
            {"mode": "start", "sample_hz": "20"},
            {},
        ):
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError):
                        await self.app.call_tool(COMMAND, arguments)
                call.assert_not_awaited()

    async def test_the_answer_is_normalized(self) -> None:
        boat = _entity()
        answer = {
            "ok": 1,
            FIELD: _trace(samples=[_sample(0, falling=1, linked=dict(boat), floor=dict(boat), parent={})]),
        }
        result, _call = await self._call({"mode": "read", "trace_id": TRACE_ID}, answer)
        trace = result[FIELD]
        self.assertIs(trace["active"], True)
        self.assertIs(trace["eof"], False)
        sample = trace["samples"][0]
        self.assertIs(sample["falling"], True)
        self.assertIs(sample["sliding_off_linked"], False)
        self.assertEqual(sample["linked"], boat)
        self.assertIsNone(sample["parent"])
        self.assertEqual(sample["yaw_deg"], 91.0)
        for broken, code in (
            ({"ok": 1}, "bad_bridge_trace_result: player_trace"),
            ({"ok": 1, FIELD: _trace(overflow=2)}, "bad_bridge_trace_boolean: player_trace.overflow"),
            ({"ok": 1, FIELD: _dump_trace(rows=9000)}, "bad_bridge_trace_dump: player_trace.rows"),
            (
                {"ok": 1, FIELD: _trace(samples=[_sample(0, linked={"type": "Boat_01_Orange"})])},
                "bad_bridge_trace_result: player_trace.samples[0].linked.class_name",
            ),
        ):
            with self.subTest(code=code):
                call = AsyncMock(return_value=broken)
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, {"mode": "read", "trace_id": TRACE_ID})
                self.assertEqual(_tool_error_text(raised.exception), code)

    def _ready_runtime(self, bridge_answer: dict[str, object]):
        """The embedded runtime's own call_bridge, wait_for_result and prune,
        with only the loopback state and the readiness snapshot faked."""
        ready = {
            "server_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
            "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
        }
        enqueued: list[tuple[str, dict[str, object], object]] = []

        def enqueue(cmd: str, args: dict[str, object], peer: object = None, **_kwargs: object):
            enqueued.append((cmd, dict(args), peer))
            return 200, {"id": 7}

        state = SimpleNamespace(
            enqueue_command=enqueue,
            take_result=lambda *_args, **_kwargs: json.loads(json.dumps(bridge_answer)),
        )
        patches = (
            patch.object(self.runtime, "status", return_value=ready),
            patch.object(self.runtime, "loopback", SimpleNamespace(state=state)),
        )
        return patches, enqueued

    async def test_a_read_through_the_runtime_arrives_normalized_without_empty_refs(self) -> None:
        answer = {
            "id": 7,
            "ok": 1,
            FIELD: _trace(samples=[_sample(0, linked={})]),
            "trace": {},
            "timeline": {},
            "input_trigger": {},
        }
        (status_patch, loopback_patch), enqueued = self._ready_runtime(answer)
        with status_patch, loopback_patch:
            result = _content_json(
                await self.app.call_tool(COMMAND, {"mode": "read", "trace_id": TRACE_ID, "timeout_s": 1.0})
            )
        self.assertEqual(enqueued, [(COMMAND, _wire(), "client")])
        self.assertIsNone(result[FIELD]["samples"][0]["linked"])
        self.assertIs(result[FIELD]["active"], True)
        for empty in ("trace", "timeline", "input_trigger"):
            self.assertNotIn(empty, result)

    async def test_a_bridge_refusal_reaches_the_caller(self) -> None:
        for code in ("trace_exists", "player_dead", "trace_active"):
            with self.subTest(code=code):
                (status_patch, loopback_patch), _enqueued = self._ready_runtime(
                    {"id": 7, "ok": 0, "error": code}
                )
                with status_patch, loopback_patch:
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, {"mode": "start", "timeout_s": 1.0})
                self.assertTrue(str(raised.exception).endswith(f": {code}"), str(raised.exception))


class VanillaPremiseTest(unittest.TestCase):
    """The premises, read from the vanilla scripts: they hold with or without this verb."""

    def _vanilla(self, relative: str) -> str:
        path = VANILLA / relative
        if not path.is_file():
            self.skipTest(f"vanilla {relative} not mounted")
        return path.read_text(encoding="utf-8", errors="replace")

    def test_the_human_getters_the_sampler_calls(self) -> None:
        human = self._vanilla(r"3_game\human.c")
        for pattern in (
            r"proto native\s+float\s+GetHeadingAngle\(\);",
            r"proto native\s+vector\s+PhysicsGetPositionWS\(\);",
            r"proto native\s+bool\s+PhysicsIsFalling\(bool pValidate\);",
            r"proto native\s+IEntity\s+PhysicsGetFloorEntity\(\);",
            r"proto native\s+IEntity\s+PhysicsGetLinkedEntity\(\);",
            r"proto native\s+bool\s+PhysicsWasSlidingOffLinkedEntity\(\);",
            r"proto native\s+void\s+PhysicsGetVelocity\(out vector pVelocity\);",
            r"proto native\s+void\s+GetMovementState\(HumanMovementState pState\);",
        ):
            with self.subTest(pattern=pattern):
                self.assertRegex(human, pattern)
        state = human[human.index("class HumanMovementState") :][:600]
        for member in ("m_CommandTypeId;", "m_iStanceIdx;", "m_iMovement;"):
            with self.subTest(member=member):
                self.assertIn(member, state)

    def test_health_is_read_only_offline(self) -> None:
        hud = self._vanilla(r"5_mission\gui\ingamehud.c")
        offline = hud.index("if (!g_Game.IsMultiplayer())")
        self.assertLess(offline, hud.index('player.GetHealth("","");'))

    def test_vanilla_reads_a_boat_deck_as_the_linked_entity(self) -> None:
        entity = self._vanilla(r"3_game\entities\entityai.c")
        self.assertIn("player.PhysicsGetLinkedEntity() == boat", entity)


if __name__ == "__main__":
    unittest.main()
