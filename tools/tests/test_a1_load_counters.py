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

from dayz_mcp import core, loopback
from dayz_mcp.instance_fence import BINDING_BOUND
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceArray, EnforceString, method_body, translate


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


def _int_divide(value: int, divisor: int) -> int:
    if not -2_147_483_648 <= value <= 2_147_483_647:
        raise AssertionError(f"int32 overflow before division: {value}")
    return value // divisor if value >= 0 else -(-value // divisor)


def _bridge_harness(name: str) -> dict:
    """Execute real counter/poll/drain/post bodies with fake dispatch and clock.

    This exercises source control flow, not an Enforce compiler or the clock's
    engine resolution. Only logging is removed (the subset has no string + int).
    JSON out strings are populated by the serializer fake; POST records bytes.
    """
    source = (_MISSION / name).read_text(encoding="utf-8")

    class WireString(EnforceString):
        def __add__(self, other):
            return WireString(self.data + other.data)

    game = SimpleNamespace(ms=100, GetTime=lambda: game.ms)
    clock = SimpleNamespace(ticks=1_000_000)
    posted = []
    polls = []
    transport = SimpleNamespace(
        POST=lambda cb, req, body: posted.append(body.data),
        GET=lambda cb, req: polls.append(req.data),
    )
    ns = dict(
        LOAD_COUNTER_CAP=_CAP, MAX_DISPATCH_PER_TICK=2, _S=WireString,
        TICKS_PER_MS=int(re.search(r"TICKS_PER_MS = (\d+);", source)[1]),
        IntDivide=_int_divide,
        IntRemainder=lambda value, divisor: value - _int_divide(value, divisor) * divisor,
        MAX_CALLBACK_REFS=int(re.search(r"MAX_CALLBACK_REFS = (\d+);", source)[1]),
        MAX_POLL_RESULTS=int(re.search(r"MAX_POLL_RESULTS = (\d+);", source)[1]),
        m_Cmds=0, m_CmdBytes=0, m_BusyMs=0, m_BusyTicksRemainder=0, m_LastCmdsPerPoll=0,
        m_Configured=True, m_Ctx=transport, m_PollCtx=transport,
        m_Pending=EnforceArray(), m_CallbackRefs=EnforceArray(),
        m_Jobs=EnforceArray(), m_PollCallback=object(),
        m_Polls=0, m_SincePollS=0.0, m_LastPollIntervalS=0.0,
        m_Key=EnforceString.of("k"), m_PeerInstance=EnforceString.of(""),
        m_PollCapabilities=EnforceString.of(""), m_PollArgContractHash=EnforceString.of(""),
        m_ResultDropLogged=False, m_Tick=1, m_TickPollCallback=0,
        m_PollInFlight=True, m_Backoff=0.0, GetGame=lambda: game,
        TickCount=lambda prev: clock.ticks - prev,
        GetPollVersion=lambda: EnforceString.of("12~1.30"),
        EncodeQueryValue=lambda value: value,
        LoadCounterQuery=lambda: EnforceString.of(""),
        SERVER_CAPABILITIES=EnforceString.of(""),
        SERVER_ARG_CONTRACT_HASH=EnforceString.of(""),
        CLIENT_POLL_CAPS=EnforceString.of(""),
        OutstandingWork=lambda: sum(len(ns[key]) for key in ("m_CallbackRefs", "m_Pending", "m_Jobs")),
        EnsurePollCallback=lambda: None, HoldPollCallback=lambda: None,
        MCPCommandBatch=lambda: SimpleNamespace(commands=None),
        MCPClientResultCallback=lambda owner: object(),
        AcquireResultCallback=lambda: object(), this=None,
        OnPollFail=lambda reason: None,
        game=game, clock=clock, posted=posted, polls=polls,
        commands=[], parse_ok=True, serialize_ok=True,
    )

    class Pending(EnforceArray):
        def Remove(self, index):
            del self[index]

        def RemoveOrdered(self, index):
            del self[index]

    ns["m_Pending"] = Pending()

    class Serializer:
        def ReadFromString(self, batch, data, error):
            batch.commands = EnforceArray(ns["commands"])
            return ns["parse_ok"]

        def WriteToString(self, result, pretty, body):
            body.data = result.body
            return ns["serialize_ok"]

    ns["JsonSerializer"] = Serializer
    ns["QueuePendingOrFail"] = lambda command: ns["m_Pending"].Insert(command)

    def dispatch(command):
        # Mission time stays fixed within synchronous dispatch; the native
        # performance counter advances independently in 100 ns ticks
        # (ensystem.c:105-106): 30,000 ticks must measure as 3 ms.
        clock.ticks += command.duration_ticks
        ns["PostResult"](command)

    ns["Dispatch"] = dispatch
    signatures = (
        "protected int NextLoadCounter(int value)",
        "protected int AddLoadCounter(int value, int amount)",
        "protected void DispatchMeasured(MCPCommand command)",
        "protected void StartPoll()",
        "void OnPollSuccess(string data, int dataSize)",
        "protected void DrainPending()",
        "protected void PostResult(MCPResult result)",
    )
    for signature in signatures:
        body = method_body(source, signature)
        body = re.sub(r'\bLog\([^;\n]*\);', '', body)
        if signature == "protected void DispatchMeasured(MCPCommand command)":
            # The shared subset refuses / and %. Lower these simple operands
            # with int32 bounds checks and Enforce truncation toward zero.
            for operator, helper in (("/", "IntDivide"), ("%", "IntRemainder")):
                body = re.sub(
                    rf"\b(TickCount\(\w+\)|\w+)\s*{operator}\s*(\w+)",
                    rf"{helper}(\1, \2)", body,
                )
        translate(
            signature + "\n{" + body + "}\n", signature, ns,
            members=set(re.findall(r'\bm_\w+', body)),
            class_types=("MCPCommandBatch", "JsonSerializer", "MCPCommand",
                         "MCPResult", "MCPResultCallback", "MCPClientResultCallback",
                         "MCPPollCallback"),
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

    def test_start_poll_reuses_preencoded_constants_and_keeps_exact_wire_query(self) -> None:
        for name, caps_const in (("MCPBridge.c", "SERVER_CAPABILITIES"), ("MCPClientBridge.c", "CLIENT_POLL_CAPS")):
            with self.subTest(bridge=name):
                source = (_MISSION / name).read_text(encoding="utf-8")
                ns = _bridge_harness(name)
                wire_str = ns["_S"]
                encode_sig = "protected string EncodeQueryValue(string value)"
                encode_body = re.sub(
                    r"\b(\w+)\s*/\s*(\w+)",
                    r"IntDivide(\1, \2)",
                    method_body(source, encode_sig),
                )
                real_encode = translate(encode_sig + "\n{" + encode_body + "}\n", encode_sig, dict(ns))
                caps_decl = re.search(rf"const string {caps_const} = ([^;]+);", source)
                self.assertIsNotNone(caps_decl)
                assert caps_decl is not None
                caps_raw = "".join(re.findall(r'"([^"\n]*)"', caps_decl[1]))
                encoded_caps = real_encode(wire_str.of(caps_raw))
                ns["m_PollCapabilities"] = encoded_caps
                if name == "MCPBridge.c":
                    ach_decl = re.search(r'const string SERVER_ARG_CONTRACT_HASH = "([^"]+)";', source)
                    self.assertIsNotNone(ach_decl)
                    assert ach_decl is not None
                    encoded_ach = real_encode(wire_str.of(ach_decl[1]))
                    ns["m_PollArgContractHash"] = encoded_ach
                load_wire = b"&ia=1&pc=1&lpims=0&cc=0&cb=0&bms=0&lcp=0"
                ns["LoadCounterQuery"] = lambda _lw=load_wire: EnforceString(_lw)
                encode_calls: list[bytes] = []
                ns["EncodeQueryValue"] = lambda value, _enc=real_encode, _calls=encode_calls: (
                    _calls.append(value.data) or _enc(value)
                )

                ns["m_Key"] = EnforceString.of("k1")
                ns["m_PeerInstance"] = EnforceString.of("")
                ns["StartPoll"]()
                self.assertEqual(encode_calls, [])
                if name == "MCPBridge.c":
                    expected_no_inst = (
                        b"poll?key=k1&ver=12~1.30&caps="
                        + encoded_caps.data
                        + b"&ach="
                        + encoded_ach.data
                        + load_wire
                    )
                else:
                    expected_no_inst = (
                        b"poll?peer=client&key=k1&ver=12~1.30&caps="
                        + encoded_caps.data
                        + load_wire
                    )
                self.assertEqual(ns["polls"][-1], expected_no_inst)

                ns["m_Key"] = EnforceString.of("k2")
                ns["m_PeerInstance"] = wire_str.of("inst/1")
                ns["StartPoll"]()
                self.assertEqual(encode_calls, [b"inst/1"])
                if name == "MCPBridge.c":
                    expected_with_inst = (
                        b"poll?key=k2&ver=12~1.30&caps="
                        + encoded_caps.data
                        + b"&ach="
                        + encoded_ach.data
                        + load_wire
                        + b"&inst=inst%2F1"
                    )
                else:
                    expected_with_inst = (
                        b"poll?peer=client&key=k2&ver=12~1.30&inst=inst%2F1&caps="
                        + encoded_caps.data
                        + load_wire
                    )
                self.assertEqual(ns["polls"][-1], expected_with_inst)

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
