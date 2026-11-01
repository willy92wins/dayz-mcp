"""A1: bridge_status load counters (fb-20261028-131233-a07f).

Pins the poll query the PBO sends, the daemon's reading of it, and the
four-state record shape. The four states themselves are not measured here.
Review round 1: the reading is per instance, a junk /poll still answers,
and the bridge counters saturate.
"""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from dayz_mcp import core, loopback, server as server_module
from dayz_mcp.instance_fence import BINDING_BOUND
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    clean,
    method_body,
    translate,
)


_MISSION = addon_root() / "scripts" / "5_Mission"
_QUERY = '&ia=" + m_InitAttempts + "&pc=" + m_Polls + "&lpims=" + ms'
_CAP = 2000000000
_INST_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_INST_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_PID_A = 41001
_PID_B = 41002
_CTIME = "2026-08-18T00:00:00.000000Z"
_WORK_FIELDS = ("cmds", "cmd_bytes", "busy_ms", "last_cmds_per_poll")
_WIRE_FIELDS = ("cc", "cb", "bms", "lcp")
_CONST_STRING_DECL = re.compile(r"\bconst\s+string\s+([A-Za-z_]\w*)\s*=\s*([^;]+);")
_CONST_INT_DECL = re.compile(r"\bconst\s+int\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;")
_CONST_FLOAT_DECL = re.compile(r"\bconst\s+float\s+([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?)\s*;")
_CONST_STRING_CHUNK = re.compile(r'"((?:\\.|[^"\\\n])*)"')
_ESCAPE_SEQ = re.compile(r"\\(.)")
_ESCAPE_MAP = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\\n])*"')
_DECLARED_MEMBER = re.compile(
    r"\b(?:protected|private)\s+(?:static\s+)?(?P<ref>ref\s+)?"
    r"(?P<type>[A-Za-z_]\w*(?:\s*<[^;{}()=]*>)?)\s+(?P<name>m_\w+)\s*;"
)
_RFC3986_UNRESERVED = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_GLOBAL_CONST_STRINGS: dict[str, str] | None = None


def _parse_const_string_expr(name: str, expr: str) -> str:
    chunks = [
        _ESCAPE_SEQ.sub(lambda m: _ESCAPE_MAP.get(m.group(1), m.group(1)), raw)
        for raw in _CONST_STRING_CHUNK.findall(expr)
    ]
    glue = re.sub(r"\s+", "", _CONST_STRING_CHUNK.sub("", expr))
    if not chunks or glue != "+" * (len(chunks) - 1):
        raise AssertionError(f"unsupported const string initializer for {name}: {expr!r}")
    return "".join(chunks)


def _global_const_strings() -> dict[str, str]:
    global _GLOBAL_CONST_STRINGS
    if _GLOBAL_CONST_STRINGS is None:
        found: dict[str, str] = {}
        cleaned = clean((_MISSION / "MCPMessages.c").read_text(encoding="utf-8"))
        masked = _STRING_LITERAL.sub(lambda m: '"' + (" " * (len(m.group(0)) - 2)) + '"', cleaned)
        for match in _CONST_STRING_DECL.finditer(cleaned):
            prefix = masked[: match.start()]
            if prefix.count("{") == prefix.count("}"):
                found[match.group(1)] = _parse_const_string_expr(match.group(1), match.group(2))
        if "MCP_BRIDGE_VERSION" not in found:
            raise AssertionError("MCP_BRIDGE_VERSION global const string not found in MCPMessages.c")
        _GLOBAL_CONST_STRINGS = found
    return _GLOBAL_CONST_STRINGS


def _rfc3986_encode(raw: bytes) -> bytes:
    return b"".join(
        bytes((byte,)) if byte in _RFC3986_UNRESERVED else ("%%%02X" % byte).encode("ascii")
        for byte in raw
    )


def _dispatch_branches(source: str) -> list[str]:
    body = method_body(source, "protected void Dispatch(MCPCommand command)")
    first = re.search(r'\bif\s*\(\s*command\.cmd\s*==\s*"', body)
    if first is None:
        raise AssertionError("Dispatch has no command.cmd chain")
    pos = first.start()
    head_re = re.compile(r'(?:if|else\s+if)\s*\(\s*command\.cmd\s*==\s*"([^"]+)"\s*\)')
    token_re = re.compile(r'"(?:\\.|[^"\\])*"|[{}]')
    names: list[str] = []
    while True:
        head = head_re.match(body, pos)
        if head is None:
            raise AssertionError(f"broken Dispatch chain at {body[pos:pos + 40]!r}")
        names.append(head.group(1))
        brace = body.index("{", head.end())
        depth = 0
        end = brace
        for tok in token_re.finditer(body, brace):
            if tok.group() == "{":
                depth += 1
            elif tok.group() == "}":
                depth -= 1
                if depth == 0:
                    end = tok.end()
                    break
        pos = end
        while pos < len(body) and body[pos].isspace():
            pos += 1
        if not body.startswith("else", pos):
            raise AssertionError("Dispatch chain lacks terminal else")
        after_else = pos + len("else")
        while after_else < len(body) and body[after_else].isspace():
            after_else += 1
        if re.match(r"if\b", body[after_else:]):
            continue
        if 'result.error = "unknown_command";' not in body[after_else:]:
            raise AssertionError("Dispatch terminal else lacks unknown_command")
        return sorted(names)


def _int_divide(value: int, divisor: int) -> int:
    if not -2_147_483_648 <= value <= 2_147_483_647:
        raise AssertionError(f"int32 overflow before division: {value}")
    return value // divisor if value >= 0 else -(-value // divisor)


def _bridge_harness(name: str, *, source: str | None = None) -> dict:
    """Execute real constructor/encode/counter/poll/drain/post bodies with fake dispatch and clock.

    This exercises source control flow, not an Enforce compiler or the clock's
    engine resolution. Only logging, integer division, float-to-int truncation
    and g_Game.GetVersion's out parameter are lowered. Cached poll fields start
    at Enforce's empty-string default and are populated solely by running the
    translated bridge constructor.
    """
    if source is None:
        source = (_MISSION / name).read_text(encoding="utf-8")
    cleaned = clean(source)
    ctor_sig = "void MCPBridge()" if name == "MCPBridge.c" else "void MCPClientBridge()"
    ctor_name = "MCPBridge" if name == "MCPBridge.c" else "MCPClientBridge"

    class WireString(EnforceString):
        def __add__(self, other):
            if isinstance(other, int):
                return WireString(self.data + str(other).encode("ascii"))
            if isinstance(other, EnforceString):
                return WireString(self.data + other.data)
            return NotImplemented

        def __radd__(self, other):
            if isinstance(other, int):
                return WireString(str(other).encode("ascii") + self.data)
            if isinstance(other, EnforceString):
                return WireString(other.data + self.data)
            return NotImplemented

        def ToUpper(self) -> int:
            self.data = self.data.upper()
            return len(self.data)

        def ToLower(self) -> int:
            self.data = self.data.lower()
            return len(self.data)

        def Replace(self, sample: EnforceString, replace: EnforceString) -> int:
            if not sample.data:
                return 0
            count = self.data.count(sample.data)
            self.data = self.data.replace(sample.data, replace.data)
            return count

        def TrimInPlace(self) -> None:
            self.data = self.data.strip(b" \t\r\n")

        def Set(self, index: int, value: EnforceString) -> None:
            self.data = self.data[:index] + value.data[:1] + self.data[index + 1 :]

        def Insert(self, index: int, value: EnforceString) -> None:
            self.data = self.data[:index] + value.data + self.data[index:]

    class Pending(EnforceArray):
        def Remove(self, index):
            del self[index]

        def RemoveOrdered(self, index):
            del self[index]

    game = SimpleNamespace(ms=100, version="1.30", GetTime=lambda: game.ms)
    clock = SimpleNamespace(ticks=1_000_000)
    posted: list[bytes] = []
    polls: list[bytes] = []
    encode_calls: list[bytes] = []
    transport = SimpleNamespace(
        POST=lambda cb, req, body: posted.append(body.data),
        GET=lambda cb, req: polls.append(req.data),
    )
    masked = _STRING_LITERAL.sub(lambda m: '"' + (" " * (len(m.group(0)) - 2)) + '"', cleaned)
    ns: dict = {}
    for cname, cval in _global_const_strings().items():
        ns[cname] = WireString.of(cval)
    for match in _CONST_INT_DECL.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = int(match.group(2))
    for match in _CONST_FLOAT_DECL.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = float(match.group(2))
    for match in _CONST_STRING_DECL.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = WireString.of(
                _parse_const_string_expr(match.group(1), match.group(2))
            )
    for match in _DECLARED_MEMBER.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") != 1:
            continue
        mtype = match.group("type").strip()
        mname = match.group("name")
        if match.group("ref") or "<" in mtype:
            ns[mname] = None
        elif mtype == "int":
            ns[mname] = 0
        elif mtype == "float":
            ns[mname] = 0.0
        elif mtype == "bool":
            ns[mname] = False
        elif mtype == "string":
            ns[mname] = WireString(b"")
        else:
            ns[mname] = None

    class Serializer:
        def ReadFromString(self, batch, data, error):
            batch.commands = EnforceArray(ns["commands"])
            return ns["parse_ok"]

        def WriteToString(self, result, pretty, body):
            body.data = result.body
            return ns["serialize_ok"]

    def dispatch(command):
        # Mission time stays fixed within synchronous dispatch; the native
        # performance counter advances independently in 100 ns ticks
        # (ensystem.c:105-106): 30,000 ticks must measure as 3 ms.
        clock.ticks += command.duration_ticks
        ns["PostResult"](command)

    ns.update(
        _S=WireString,
        array=Pending,
        map=EnforceMap,
        IntDivide=_int_divide,
        IntRemainder=lambda value, divisor: value - _int_divide(value, divisor) * divisor,
        FloatToInt=int,
        GameGetVersion=lambda: WireString.of(game.version),
        g_Game=None,
        GetGame=lambda: game,
        TickCount=lambda prev: clock.ticks - prev,
        MCPTelemetryFixtureLine=object,
        MCPOnInventoryMoveRefused=object(),
        MCPCameraPath=object,
        MCPJobRunner=lambda: SimpleNamespace(Count=lambda: 0),
        MCPCommandBatch=lambda: SimpleNamespace(commands=None),
        MCPPollCallback=lambda owner: object(),
        MCPClientPollCallback=lambda owner: object(),
        MCPClientResultCallback=lambda owner: object(),
        AcquireResultCallback=lambda: object(),
        JsonSerializer=Serializer,
        QueuePendingOrFail=lambda command: ns["m_Pending"].Insert(command),
        Dispatch=dispatch,
        OnPollFail=lambda reason: None,
        this=object(),
        game=game,
        clock=clock,
        posted=posted,
        polls=polls,
        encode_calls=encode_calls,
        commands=[],
        parse_ok=True,
        serialize_ok=True,
    )

    encode_sig = "protected string EncodeQueryValue(string value)"
    encode_body = re.sub(
        r"\b(\w+)\s*/\s*(\w+)",
        r"IntDivide(\1, \2)",
        method_body(source, encode_sig),
    )
    real_encode = translate(encode_sig + "\n{" + encode_body + "}\n", encode_sig, dict(ns))
    ns["real_encode"] = real_encode

    def tracked_encode(value):
        encode_calls.append(value.data)
        return real_encode(value)

    ns["EncodeQueryValue"] = tracked_encode

    signatures = [
        ctor_sig,
        "protected int NextLoadCounter(int value)",
        "protected int AddLoadCounter(int value, int amount)",
        "protected string GetPollVersion()",
        "protected string LoadCounterQuery()",
        "protected void DispatchMeasured(MCPCommand command)",
        "protected void StartPoll()",
        "void OnPollSuccess(string data, int dataSize)",
        "protected void DrainPending()",
        "protected void PostResult(MCPResult result)",
    ]
    if name == "MCPClientBridge.c":
        signatures.extend((
            "protected int OutstandingWork()",
            "protected void EnsurePollCallback()",
            "protected void HoldPollCallback()",
        ))
    for signature in signatures:
        body = method_body(source, signature)
        body = re.sub(r"\bLog\([^;\n]*\);", "", body)
        if signature == "protected string GetPollVersion()":
            body = re.sub(
                r"\bg_Game\.GetVersion\(\s*gameVersion\s*\)\s*;",
                "gameVersion = GameGetVersion();",
                body,
            )
        elif signature == "protected string LoadCounterQuery()":
            body = re.sub(r"\bint\s+ms\s*=\s*([^;]+);", r"int ms = FloatToInt(\1);", body)
        elif signature == "protected void DispatchMeasured(MCPCommand command)":
            # The shared subset refuses / and %. Lower these simple operands
            # with int32 bounds checks and Enforce truncation toward zero.
            for operator, helper in (("/", "IntDivide"), ("%", "IntRemainder")):
                body = re.sub(
                    rf"\b(TickCount\(\w+\)|\w+)\s*{operator}\s*(\w+)",
                    rf"{helper}(\1, \2)",
                    body,
                )
        translate(
            signature + "\n{" + body + "}\n",
            signature,
            ns,
            members=set(re.findall(r"\bm_\w+", body)),
            class_types=(
                "MCPCommandBatch",
                "JsonSerializer",
                "MCPCommand",
                "MCPResult",
                "MCPResultCallback",
                "MCPClientResultCallback",
                "MCPPollCallback",
                "MCPClientPollCallback",
            ),
        )

    # Execute the real bridge constructor to populate m_PollCapabilities (and
    # m_PollArgContractHash on the server) from the class constants.
    ns[ctor_name]()
    ns["ctor_encode_calls"] = list(encode_calls)
    encode_calls.clear()

    # Post-TryInit runtime state; cached poll fields are never touched here.
    ns.update(
        MAX_DISPATCH_PER_TICK=2,
        m_Configured=True,
        m_Ctx=transport,
        m_PollCtx=transport,
        m_Key=WireString.of("k"),
        m_Tick=1,
        m_PollInFlight=True,
    )
    return ns


def _command(index: int, *, kind: str = "query_all_players", duration_ticks: int = 30_000):
    return SimpleNamespace(id=index, cmd=EnforceString.of(kind), duration_ticks=duration_ticks,
                           body='{"ok":true,"text":"ñ"}'.encode("utf-8"), ok=True)


def _null_load(load: dict) -> None:
    if not isinstance(load, dict):
        raise AssertionError(load)
    for key in ("init_attempts", "polls", "last_poll_interval_s", *_WORK_FIELDS):
        if load[key] is not None:
            raise AssertionError(load)


class LoadCountersTest(unittest.TestCase):
    def test_real_dispatch_paths_measure_work_and_idle_stays_flat(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            with self.subTest(bridge=name):
                ns = _bridge_harness(name)

                def poll():
                    ns["StartPoll"]()
                    ns["OnPollSuccess"](EnforceString.of("{}"), 2)

                poll()
                self.assertEqual([ns[key] for key in ("m_Cmds", "m_CmdBytes", "m_BusyMs")], [0, 0, 0])
                ns["commands"] = [_command(i) for i in range(5)]
                poll()
                self.assertEqual(ns["m_Cmds"], 2)
                self.assertEqual(ns["m_LastCmdsPerPoll"], 5)
                ns["DrainPending"]()
                ns["DrainPending"]()
                self.assertEqual(ns["m_Cmds"], 5)
                self.assertEqual(ns["m_BusyMs"], 15)
                self.assertEqual(ns["game"].GetTime(), 100)
                self.assertEqual(ns["m_CmdBytes"], sum(map(len, ns["posted"])))
                self.assertEqual(ns["m_CmdBytes"], 5 * len(_command(0).body))
                before = [ns[key] for key in ("m_Cmds", "m_CmdBytes", "m_BusyMs", "m_LastCmdsPerPoll")]
                self.assertEqual(ns["m_Polls"], 2)
                ns["commands"] = []
                for _ in range(30):
                    ns["game"].ms += 300
                    ns["clock"].ticks += 3_000_000
                    ns["m_SincePollS"] += 0.3
                    poll()
                    ns["DrainPending"]()
                self.assertEqual(ns["m_Polls"], 32)
                self.assertEqual(len(ns["polls"]), 32)
                self.assertAlmostEqual(ns["m_LastPollIntervalS"], 0.3)
                self.assertEqual([ns[key] for key in ("m_Cmds", "m_CmdBytes", "m_BusyMs", "m_LastCmdsPerPoll")], before)
                ns["commands"] = [_command(6)]
                poll()
                self.assertEqual(ns["m_LastCmdsPerPoll"], 1)
                self.assertEqual(ns["m_Cmds"], 6)

    def test_dispatch_tick_deltas_preserve_fractional_milliseconds(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            for ticks, milliseconds in ((-2_147_483_648, 0), (-30_000, 0), (-1, 0),
                    (0, 0), (9_999, 0), (10_000, 1), (19_999, 1), (30_000, 3),
                    (2_147_483_647, 214_748)):
                with self.subTest(bridge=name, ticks=ticks):
                    ns = _bridge_harness(name)
                    ns["m_BusyMs"] = 7
                    ns["DispatchMeasured"](_command(0, duration_ticks=ticks))
                    self.assertEqual(ns["m_BusyMs"], 7 + milliseconds)
                    self.assertIs(type(ns["m_BusyMs"]), int)
                    self.assertEqual(ns["m_BusyTicksRemainder"], max(ticks, 0) % 10_000)

    def test_fast_dispatches_accumulate_and_carry_remainder(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            with self.subTest(bridge=name):
                ns = _bridge_harness(name)
                for index in range(50):
                    ns["DispatchMeasured"](_command(index, duration_ticks=2_500))
                self.assertEqual(ns["m_BusyMs"], 12)
                self.assertEqual(ns["m_BusyTicksRemainder"], 5_000)
                for ticks, milliseconds, remainder in (
                    (4_999, 12, 9_999), (-2_147_483_648, 12, 9_999),
                    (-1, 12, 9_999), (0, 12, 9_999), (1, 13, 0),
                    (12_500, 14, 2_500), (7_500, 15, 0),
                ):
                    ns["DispatchMeasured"](_command(50, duration_ticks=ticks))
                    self.assertEqual(ns["m_BusyMs"], milliseconds)
                    self.assertEqual(ns["m_BusyTicksRemainder"], remainder)

    def test_max_tick_delta_with_remainder_and_saturated_counter(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            for busy_ms in (0, _CAP - 1, _CAP):
                with self.subTest(bridge=name, busy_ms=busy_ms):
                    ns = _bridge_harness(name)
                    ns.update(m_BusyMs=busy_ms, m_BusyTicksRemainder=9_999)
                    total_ticks = 9_999
                    for index in range(3):
                        ns["DispatchMeasured"](_command(index, duration_ticks=2_147_483_647))
                        total_ticks += 2_147_483_647
                        milliseconds, remainder = divmod(total_ticks, 10_000)
                        self.assertEqual(ns["m_BusyMs"], min(_CAP, busy_ms + milliseconds))
                        self.assertEqual(ns["m_BusyTicksRemainder"], remainder)

    def test_world_spawn_counts_when_drained_not_when_queued(self) -> None:
        ns = _bridge_harness("MCPBridge.c")
        ns["commands"] = [_command(0, kind="world_spawn"), _command(1)]
        ns["OnPollSuccess"](EnforceString.of("{}"), 2)
        self.assertEqual(ns["m_Cmds"], 0)
        self.assertEqual(ns["m_LastCmdsPerPoll"], 2)
        ns["DrainPending"]()
        self.assertEqual(ns["m_Cmds"], 2)

    def test_counter_arithmetic_saturates_without_overflow(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            ns = _bridge_harness(name)
            for value, amount, expected in ((0, 0, 0), (2, -1, 2), (2, 3, 5),
                    (_CAP - 1, 1, _CAP), (_CAP - 1, 2_147_483_647, _CAP),
                    (_CAP, 1, _CAP), (-1, 1, _CAP)):
                self.assertEqual(ns["AddLoadCounter"](value, amount), expected, name)
            ns.update(m_Cmds=_CAP - 1, m_CmdBytes=_CAP - 1, m_BusyMs=_CAP - 1)
            ns["DispatchMeasured"](_command(0))
            ns["DispatchMeasured"](_command(1))
            self.assertEqual([ns[k] for k in ("m_Cmds", "m_CmdBytes", "m_BusyMs")], [_CAP] * 3)

    def test_null_failed_parse_failed_serialization_and_clock_regression(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            ns = _bridge_harness(name)
            ns["DispatchMeasured"](None)
            ns.update(commands=[_command(0)], parse_ok=False)
            ns["OnPollSuccess"](EnforceString.of("bad"), 3)
            self.assertEqual(ns["m_Cmds"], 0)
            self.assertEqual(ns["m_LastCmdsPerPoll"], 0)
            ns["serialize_ok"] = False
            ns["DispatchMeasured"](_command(1, duration_ticks=-20_000))
            self.assertEqual(ns["m_Cmds"], 1)
            self.assertEqual(ns["m_CmdBytes"], 0)
            self.assertEqual(ns["m_BusyMs"], 0)
            self.assertEqual(ns["posted"], [])

    def test_work_query_reaches_both_peers_and_old_pbo_stays_compatible(self) -> None:
        state = loopback.ServerState("k")
        query = {"ia": ["1"], "pc": ["2"], "lpims": ["300"]}
        for wire, value in zip(_WIRE_FIELDS, (9, 678, 12, 3)):
            query[wire] = [str(value)]
        for peer in ("server", "client"):
            state.record_poll(peer, load=loopback._load_from_poll_query(query))
        status = core.build_status(state.status_snapshot(), require_version=False, expected_game_version=None)
        for peer in ("server_peer", "client_peer"):
            self.assertEqual([status[peer]["load"][key] for key in _WORK_FIELDS], [9, 678, 12, 3])
        old = loopback.ServerState("k")
        old.record_poll("server", load=loopback._load_from_poll_query({k: query[k] for k in ("ia", "pc", "lpims")}))
        load = old.status_snapshot()["peers"]["server"]["load"]
        self.assertEqual(load["polls"], 2)
        self.assertTrue(all(load[key] is None for key in _WORK_FIELDS))

    def test_work_query_rejects_garbage_and_preserves_previous_reading(self) -> None:
        base = {"ia": ["1"], "pc": ["2"], "lpims": ["300"], **{key: ["7"] for key in _WIRE_FIELDS}}
        for wire, field in zip(_WIRE_FIELDS, _WORK_FIELDS):
            for bad in (["nope"], ["-1"], ["1.0"], ["²"], ["١"], [""], [str(_CAP + 1)], ["9" * 400], [], ["1", "2"], [True], "7", None):
                with self.subTest(wire=wire, bad=bad):
                    state = loopback.ServerState("k")
                    state.record_poll("server", load=loopback._load_from_poll_query(base))
                    parsed = loopback._load_from_poll_query({**base, wire: bad})
                    self.assertNotIn(field, parsed)
                    state.record_poll("server", load=parsed)
                    self.assertEqual(state.status_snapshot()["peers"]["server"]["load"][field], 7)
            for good in (0, _CAP):
                self.assertEqual(loopback._load_from_poll_query({**base, wire: [str(good)]})[field], good)

    def test_status_rejects_invalid_typed_work_fields(self) -> None:
        for field in _WORK_FIELDS:
            for bad in (True, False, -1, 1.5, "3", float("nan"), float("inf"), _CAP + 1, [], {}):
                with self.subTest(field=field, bad=bad):
                    snapshot = loopback.ServerState("k").status_snapshot()
                    snapshot["peers"]["server"]["load"] = {field: bad}
                    status = core.build_status(snapshot, require_version=False, expected_game_version=None)
                    self.assertIsNone(status["server_peer"]["load"][field])
                    state = loopback.ServerState("k")
                    state.record_poll("server", load={"init_attempts": 1, "polls": 1, "last_poll_interval_s": 0.3, field: bad})
                    self.assertIsNone(state.status_snapshot()["peers"]["server"]["load"][field])

    def test_invalid_direct_legacy_reading_does_not_replace_work_counters(self) -> None:
        state = loopback.ServerState("k")
        good = {"init_attempts": 1, "polls": 2, "last_poll_interval_s": 0.3, "cmds": 5}
        state.record_poll("server", load=good)
        for bad in (-1, True, "0.3", float("nan"), float("inf"), 10 ** 400):
            state.record_poll("server", load={**good, "last_poll_interval_s": bad, "cmds": 6})
            self.assertEqual(state.status_snapshot()["peers"]["server"]["load"]["cmds"], 5)

    def test_both_bridges_announce_the_same_query(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            text = (_MISSION / name).read_text(encoding="utf-8")
            self.assertIn(_QUERY, text, name)
            self.assertIn('&cc=" + m_Cmds + "&cb=" + m_CmdBytes + "&bms=" + m_BusyMs + "&lcp=" + m_LastCmdsPerPoll', text, name)
            self.assertIn("m_InitAttempts = NextLoadCounter(m_InitAttempts);", text, name)
            self.assertIn("m_Polls = NextLoadCounter(m_Polls);", text, name)

    def _expected_poll_query(
        self,
        name: str,
        *,
        key: str,
        game_version: str,
        inst: str,
        caps_raw: bytes,
        ia: int,
        pc: int,
        lpims: int,
        cc: int,
        cb: int,
        bms: int,
        lcp: int,
    ) -> bytes:
        ver_raw = (core.EXPECTED_BRIDGE_VERSION + "~" + (game_version or "unknown")).encode("utf-8")
        ver_enc = _rfc3986_encode(ver_raw)
        caps_enc = _rfc3986_encode(caps_raw)
        load_enc = (
            f"&ia={ia}&pc={pc}&lpims={lpims}&cc={cc}&cb={cb}&bms={bms}&lcp={lcp}"
        ).encode("ascii")
        inst_enc = (b"&inst=" + _rfc3986_encode(inst.encode("utf-8"))) if inst else b""
        if name == "MCPBridge.c":
            ach_enc = _rfc3986_encode(
                server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH.encode("ascii")
            )
            return (
                b"poll?key="
                + key.encode("utf-8")
                + b"&ver="
                + ver_enc
                + b"&caps="
                + caps_enc
                + b"&ach="
                + ach_enc
                + load_enc
                + inst_enc
            )
        return (
            b"poll?peer=client&key="
            + key.encode("utf-8")
            + b"&ver="
            + ver_enc
            + inst_enc
            + b"&caps="
            + caps_enc
            + load_enc
        )

    def _run_translated_constructor_and_polls(
        self, name: str, source: str, expected_caps_raw: bytes
    ) -> None:
        ns = _bridge_harness(name, source=source)
        wire_str = ns["_S"]
        ns["game"].version = "1.30.164200"
        ns["m_Key"] = wire_str.of("k1")
        ns["m_PeerInstance"] = wire_str.of("inst/1")
        ns["StartPoll"]()
        expected_first = self._expected_poll_query(
            name,
            key="k1",
            game_version="1.30.164200",
            inst="inst/1",
            caps_raw=expected_caps_raw,
            ia=0,
            pc=1,
            lpims=0,
            cc=0,
            cb=0,
            bms=0,
            lcp=0,
        )
        self.assertEqual(ns["polls"][-1], expected_first)

        ns["m_CallbackRefs"].Clear()
        ns["encode_calls"].clear()
        ns["m_SincePollS"] = 0.25
        ns["m_Key"] = wire_str.of("k2")
        ns["m_PeerInstance"] = wire_str.of("")
        ns["StartPoll"]()
        expected_second = self._expected_poll_query(
            name,
            key="k2",
            game_version="1.30.164200",
            inst="",
            caps_raw=expected_caps_raw,
            ia=0,
            pc=2,
            lpims=250,
            cc=0,
            cb=0,
            bms=0,
            lcp=0,
        )
        self.assertEqual(ns["polls"][-1], expected_second)
        self.assertEqual(ns["encode_calls"], [])
        expected_ctor_calls = (
            [expected_caps_raw, server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH.encode("ascii")]
            if name == "MCPBridge.c"
            else [expected_caps_raw]
        )
        self.assertEqual(ns["ctor_encode_calls"], expected_ctor_calls)

    def test_start_poll_reuses_preencoded_constants_and_keeps_exact_wire_query(self) -> None:
        query_count = 0
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            source = (_MISSION / name).read_text(encoding="utf-8")
            expected_caps_raw = ",".join(_dispatch_branches(source)).encode("ascii")
            expected_ctor_calls = (
                [
                    expected_caps_raw,
                    server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH.encode("ascii"),
                ]
                if name == "MCPBridge.c"
                else [expected_caps_raw]
            )
            for game_version in ("", "1.30.164200"):
                for key in ("key_initial", "key_reloaded"):
                    for inst in ("", "aaaa-1111", "inst/1~a b?x=1&y=2"):
                        for ia, gap_s, cc, cb, bms, lcp in (
                            (0, 0.0, 0, 0, 0, 0),
                            (3, 0.25, 9, 678, 12, 2),
                        ):
                            with self.subTest(
                                bridge=name, ver=game_version, key=key, inst=inst, ia=ia
                            ):
                                ns = _bridge_harness(name, source=source)
                                self.assertEqual(ns["ctor_encode_calls"], expected_ctor_calls)
                                wire_str = ns["_S"]
                                ns["game"].version = game_version
                                ns["m_Key"] = wire_str.of(key)
                                ns["m_PeerInstance"] = wire_str.of(inst)
                                ns["m_InitAttempts"] = ia
                                ns["m_Cmds"] = cc
                                ns["m_CmdBytes"] = cb
                                ns["m_BusyMs"] = bms
                                ns["m_LastCmdsPerPoll"] = lcp

                                for poll_index in (1, 2):
                                    ns["m_CallbackRefs"].Clear()
                                    ns["encode_calls"].clear()
                                    ns["m_SincePollS"] = gap_s
                                    version_uncached = ns["m_PollVersion"].data == b""
                                    ns["StartPoll"]()
                                    expected_calls: list[bytes] = []
                                    if version_uncached:
                                        expected_calls.append(
                                            (
                                                core.EXPECTED_BRIDGE_VERSION
                                                + "~"
                                                + (game_version or "unknown")
                                            ).encode("utf-8")
                                        )
                                    if inst:
                                        expected_calls.append(inst.encode("utf-8"))
                                    self.assertEqual(ns["encode_calls"], expected_calls)
                                    lpims = 0 if poll_index == 1 else int(gap_s * 1000.0)
                                    expected_query = self._expected_poll_query(
                                        name,
                                        key=key,
                                        game_version=game_version,
                                        inst=inst,
                                        caps_raw=expected_caps_raw,
                                        ia=ia,
                                        pc=poll_index,
                                        lpims=lpims,
                                        cc=cc,
                                        cb=cb,
                                        bms=bms,
                                        lcp=lcp,
                                    )
                                    self.assertEqual(ns["polls"][-1], expected_query)
                                    query_count += 1
        self.assertEqual(query_count, 96)

    def test_constructor_cache_mutants_fail_translated_poll_gate(self) -> None:
        server_source = (_MISSION / "MCPBridge.c").read_text(encoding="utf-8")
        client_source = (_MISSION / "MCPClientBridge.c").read_text(encoding="utf-8")
        server_caps_raw = ",".join(_dispatch_branches(server_source)).encode("ascii")
        client_caps_raw = ",".join(_dispatch_branches(client_source)).encode("ascii")

        server_caps_init = "\t\tm_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);\n"
        server_ach_init = "\t\tm_PollArgContractHash = EncodeQueryValue(SERVER_ARG_CONTRACT_HASH);\n"
        client_caps_init = "\t\tm_PollCapabilities = EncodeQueryValue(CLIENT_POLL_CAPS);\n"
        server_caps_wire = '\t\trequest = request + "&caps=" + m_PollCapabilities;\n'
        server_ach_wire = '\t\trequest = request + "&ach=" + m_PollArgContractHash;\n'
        client_caps_wire = '\t\trequest = request + "&caps=" + m_PollCapabilities;\n'
        try_init_head = "\tprotected void TryInit()\n\t{\n"

        for needle in (server_caps_init, server_ach_init, server_caps_wire, server_ach_wire, try_init_head):
            self.assertEqual(server_source.count(needle), 1, needle)
        for needle in (client_caps_init, client_caps_wire, try_init_head):
            self.assertEqual(client_source.count(needle), 1, needle)

        self._run_translated_constructor_and_polls("MCPBridge.c", server_source, server_caps_raw)
        self._run_translated_constructor_and_polls("MCPClientBridge.c", client_source, client_caps_raw)

        cases = (
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_caps_to_upper_after_init",
                server_source.replace(
                    server_caps_init, server_caps_init + "\t\tm_PollCapabilities.ToUpper();\n", 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_ach_to_upper_after_init",
                server_source.replace(
                    server_ach_init, server_ach_init + "\t\tm_PollArgContractHash.ToUpper();\n", 1
                ),
            ),
            (
                "MCPClientBridge.c",
                client_caps_raw,
                "client_caps_to_upper_after_init",
                client_source.replace(
                    client_caps_init, client_caps_init + "\t\tm_PollCapabilities.ToUpper();\n", 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_caps_plus_equals_after_init",
                server_source.replace(
                    server_caps_init, server_caps_init + '\t\tm_PollCapabilities += "%2Cextra";\n', 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_ach_plus_equals_after_init",
                server_source.replace(
                    server_ach_init, server_ach_init + '\t\tm_PollArgContractHash += "0";\n', 1
                ),
            ),
            (
                "MCPClientBridge.c",
                client_caps_raw,
                "client_caps_plus_equals_after_init",
                client_source.replace(
                    client_caps_init, client_caps_init + '\t\tm_PollCapabilities += "%2Cextra";\n', 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_caps_init_from_other_const",
                server_source.replace(
                    server_caps_init, "\t\tm_PollCapabilities = EncodeQueryValue(VISIBILITY_HEAD_BONE);\n", 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_ach_init_from_other_const",
                server_source.replace(
                    server_ach_init, "\t\tm_PollArgContractHash = EncodeQueryValue(VISIBILITY_HEAD_BONE);\n", 1
                ),
            ),
            (
                "MCPClientBridge.c",
                client_caps_raw,
                "client_caps_init_from_other_const",
                client_source.replace(
                    client_caps_init, "\t\tm_PollCapabilities = EncodeQueryValue(MCP_BRIDGE_VERSION);\n", 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_caps_init_from_literal",
                server_source.replace(
                    server_caps_init, '\t\tm_PollCapabilities = EncodeQueryValue("entities_query");\n', 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_caps_init_from_preencoded_literal",
                server_source.replace(
                    server_caps_init,
                    f'\t\tm_PollCapabilities = "{_rfc3986_encode(server_caps_raw).decode("ascii")}";\n',
                    1,
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_ach_init_from_literal",
                server_source.replace(
                    server_ach_init, '\t\tm_PollArgContractHash = EncodeQueryValue("0000000000000000");\n', 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_ach_init_from_preencoded_literal",
                server_source.replace(
                    server_ach_init,
                    f'\t\tm_PollArgContractHash = "{server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH}";\n',
                    1,
                ),
            ),
            (
                "MCPClientBridge.c",
                client_caps_raw,
                "client_caps_init_from_literal",
                client_source.replace(
                    client_caps_init, '\t\tm_PollCapabilities = EncodeQueryValue("ui_dialog");\n', 1
                ),
            ),
            (
                "MCPClientBridge.c",
                client_caps_raw,
                "client_caps_init_from_preencoded_literal",
                client_source.replace(
                    client_caps_init,
                    f'\t\tm_PollCapabilities = "{_rfc3986_encode(client_caps_raw).decode("ascii")}";\n',
                    1,
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_caps_init_conditional",
                server_source.replace(
                    server_caps_init,
                    "\t\tif (m_Configured)\n\t\t{\n\t" + server_caps_init + "\t\t}\n",
                    1,
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_ach_init_conditional",
                server_source.replace(
                    server_ach_init,
                    "\t\tif (g_Game)\n\t\t{\n\t" + server_ach_init + "\t\t}\n",
                    1,
                ),
            ),
            (
                "MCPClientBridge.c",
                client_caps_raw,
                "client_caps_init_conditional",
                client_source.replace(
                    client_caps_init,
                    "\t\tif (m_Configured)\n\t\t{\n\t" + client_caps_init + "\t\t}\n",
                    1,
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_caps_init_moved_to_try_init",
                server_source.replace(server_caps_init, "", 1).replace(
                    try_init_head, try_init_head + server_caps_init, 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_ach_init_moved_to_try_init",
                server_source.replace(server_ach_init, "", 1).replace(
                    try_init_head, try_init_head + server_ach_init, 1
                ),
            ),
            (
                "MCPClientBridge.c",
                client_caps_raw,
                "client_caps_init_moved_to_try_init",
                client_source.replace(client_caps_init, "", 1).replace(
                    try_init_head, try_init_head + client_caps_init, 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_caps_init_moved_to_start_poll",
                server_source.replace(server_caps_init, "", 1).replace(
                    server_caps_wire, server_caps_init + server_caps_wire, 1
                ),
            ),
            (
                "MCPBridge.c",
                server_caps_raw,
                "server_ach_init_moved_to_start_poll",
                server_source.replace(server_ach_init, "", 1).replace(
                    server_ach_wire, server_ach_init + server_ach_wire, 1
                ),
            ),
            (
                "MCPClientBridge.c",
                client_caps_raw,
                "client_caps_init_moved_to_start_poll",
                client_source.replace(client_caps_init, "", 1).replace(
                    client_caps_wire, client_caps_init + client_caps_wire, 1
                ),
            ),
        )
        for name, expected_caps_raw, label, mutant in cases:
            with self.subTest(mutant=label):
                base = server_source if name == "MCPBridge.c" else client_source
                self.assertNotEqual(mutant, base, label)
                with self.assertRaises(AssertionError):
                    self._run_translated_constructor_and_polls(name, mutant, expected_caps_raw)

    def test_bridge_counters_saturate_at_a_fixed_cap(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            text = (_MISSION / name).read_text(encoding="utf-8")
            self.assertIn(f"LOAD_COUNTER_CAP = {_CAP};", text, name)
            self.assertIn("if (value < 0 || value >= LOAD_COUNTER_CAP)", text, name)
            self.assertIn("return LOAD_COUNTER_CAP;", text, name)
            self.assertNotIn("m_InitAttempts = m_InitAttempts + 1;", text, name)
            self.assertNotIn("m_Polls = m_Polls + 1;", text, name)

    def test_status_load_is_null_until_a_complete_poll(self) -> None:
        state = loopback.ServerState("k")
        code, _payload = state.record_poll("server", "12~1.30")
        self.assertEqual(code, 200)
        load = state.status_snapshot()["peers"]["server"]["load"]
        self.assertEqual(load["init_attempts"], None)
        self.assertEqual(load["polls"], None)
        self.assertEqual(load["last_poll_interval_s"], None)

    def test_poll_query_lands_on_bridge_status(self) -> None:
        state = loopback.ServerState("k")
        code, _payload = state.record_poll(
            "server",
            "12~1.30",
            load=loopback._load_from_poll_query(
                {"ia": ["7"], "pc": ["2"], "lpims": ["250"]}
            ),
        )
        self.assertEqual(code, 200)
        status = core.build_status(
            state.status_snapshot(),
            require_version=False,
            expected_game_version=None,
        )
        load = status["server_peer"]["load"]
        self.assertEqual(load["init_attempts"], 7)
        self.assertEqual(load["polls"], 2)
        self.assertEqual(load["last_poll_interval_s"], 0.25)
        client = status["client_peer"]["load"]
        self.assertEqual(client["init_attempts"], None)
        self.assertEqual(client["polls"], None)
        self.assertIsNone(client["last_poll_interval_s"])

    def test_partial_or_junk_query_does_not_replace_a_reading(self) -> None:
        self.assertIsNone(loopback._load_from_poll_query({"ia": ["1"], "pc": ["1"]}))
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["-1"], "pc": ["1"], "lpims": ["1"]}
            )
        )
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["1"], "pc": ["nope"], "lpims": ["1"]}
            )
        )
        state = loopback.ServerState("k")
        state.record_poll(
            "client",
            load={
                "init_attempts": 3,
                "polls": 3,
                "last_poll_interval_s": 0.2,
            },
        )
        state.record_poll("client", load=None)
        load = state.status_snapshot()["peers"]["client"]["load"]
        self.assertEqual(load["init_attempts"], 3)
        self.assertEqual(load["polls"], 3)
        self.assertEqual(load["last_poll_interval_s"], 0.2)

    def test_legacy_work_counters_do_not_leak_to_bound_instance(self) -> None:
        for peer in ("server", "client"):
            with self.subTest(peer=peer):
                state = loopback.ServerState("k")
                state.record_poll(
                    peer,
                    load=loopback._load_from_poll_query({
                        "ia": ["4"], "pc": ["9"], "lpims": ["200"],
                        "cc": ["8"], "cb": ["900"], "bms": ["12"], "lcp": ["2"],
                    }),
                )
                legacy = state.status_snapshot()["peers"][peer]["load"]
                self.assertEqual([legacy[key] for key in _WORK_FIELDS], [8, 900, 12, 2])

                state.install_bound_peer(
                    instance=_INST_A, role=peer, pid=_PID_A, run_id="run-a"
                )
                code, payload = state.record_poll(
                    peer,
                    "12~1.30",
                    instance=_INST_A,
                    source_pid=_PID_A,
                    source_creation_time=_CTIME,
                    load=loopback._load_from_poll_query(
                        {"ia": ["1"], "pc": ["2"], "lpims": ["300"]}
                    ),
                )
                self.assertEqual(code, 200)
                self.assertEqual(payload["bind"], BINDING_BOUND)
                status = core.build_status(
                    state.status_snapshot(),
                    require_version=False,
                    expected_game_version=None,
                )
                self.assertEqual(status[f"{peer}_peer"]["load"], {
                    "init_attempts": 1, "polls": 2, "last_poll_interval_s": 0.3,
                    "cmds": None, "cmd_bytes": None, "busy_ms": None,
                    "last_cmds_per_poll": None,
                })

    def test_retired_instance_does_not_lend_counters_to_an_old_pbo(self) -> None:
        state = loopback.ServerState("k")
        state.install_bound_peer(
            instance=_INST_A, role="server", pid=_PID_A, run_id="run-a"
        )
        code, payload = state.record_poll(
            "server",
            "12~1.30",
            instance=_INST_A,
            source_pid=_PID_A,
            source_creation_time=_CTIME,
            load={"init_attempts": 4, "polls": 9, "last_poll_interval_s": 0.2,
                  "cmds": 8, "cmd_bytes": 900, "busy_ms": 12, "last_cmds_per_poll": 2},
        )
        self.assertEqual(code, 200)
        self.assertEqual(payload["bind"], BINDING_BOUND)
        owned = state.status_snapshot()["peers"]["server"]["load"]
        self.assertEqual(owned["init_attempts"], 4)
        self.assertEqual(owned["polls"], 9)
        self.assertEqual([owned[key] for key in _WORK_FIELDS], [8, 900, 12, 2])

        state.retire_role("run-a", "server", "test")
        state.install_bound_peer(
            instance=_INST_B, role="server", pid=_PID_B, run_id="run-b"
        )
        code, payload = state.record_poll(
            "server",
            "12~1.30",
            instance=_INST_B,
            source_pid=_PID_B,
            source_creation_time=_CTIME,
            load=None,
        )
        self.assertEqual(code, 200)
        self.assertEqual(payload["bind"], BINDING_BOUND)
        peer = state.status_snapshot()["peers"]["server"]
        self.assertEqual(peer["binding_state"], BINDING_BOUND)
        _null_load(peer["load"])

    def test_non_ascii_and_huge_poll_query_still_answers(self) -> None:
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["\u00b2"], "pc": ["1"], "lpims": ["1"]}
            )
        )
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["1"], "pc": ["1"], "lpims": ["9" * 400]}
            )
        )
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["1"], "pc": ["1"], "lpims": ["9" * (loopback._POLL_COUNTER_MAX_DIGITS + 1)]}
            )
        )
        kept = loopback._load_from_poll_query(
            {"ia": [str(_CAP)], "pc": ["1"], "lpims": ["250"]}
        )
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertEqual(kept["init_attempts"], _CAP)

        state = loopback.ServerState("k")
        state.record_poll(
            "server",
            load={
                "init_attempts": 3,
                "polls": 3,
                "last_poll_interval_s": 0.2,
            },
        )
        handler = loopback.Handler.__new__(loopback.Handler)
        handler.server = type("Srv", (), {"state": state, "log_sink": lambda _m: None})()
        handler.connection = None
        answered: list[tuple[int, dict]] = []

        def _json(code: int, body: dict) -> None:
            answered.append((code, body))

        handler._json = _json  # type: ignore[method-assign]
        handler._log = lambda _message: None  # type: ignore[method-assign]
        for query in (
            {"ia": ["\u00b2"], "pc": ["1"], "lpims": ["1"]},
            {"ia": ["1"], "pc": ["1"], "lpims": ["9" * 400]},
        ):
            answered.clear()
            handler._handle_poll(query)
            self.assertEqual(len(answered), 1)
            self.assertEqual(answered[0][0], 200)
            self.assertIn("commands", answered[0][1])
        load = state.status_snapshot()["peers"]["server"]["load"]
        self.assertEqual(load["init_attempts"], 3)
        self.assertEqual(load["polls"], 3)

    def test_baseline_record_names_four_unmeasured_states(self) -> None:
        record = core.empty_load_baseline_record()
        self.assertEqual(record["observer"], "server_process_cpu")
        self.assertFalse(record["measured"])
        self.assertEqual(
            tuple(record["states"]),
            (
                "no_mod",
                "loaded_unconfigured",
                "configured_idle",
                "admin_load",
            ),
        )
        self.assertTrue(all(value is None for value in record["states"].values()))


if __name__ == "__main__":
    unittest.main()
