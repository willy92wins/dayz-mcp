"""query_player_state reports scripted triggers and their events (inbox fa2b).

The Enforce record methods in MCP_PlayerTriggers.c are translated and run
against fakes (tests/enforce_subset_helpers.py); the overrides and the
5_Mission read are pinned where translation cannot reach (super, the deep
copy), and the Python cursor goes through the public tool with a fake bridge.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dayz_mcp import server
from dayz_mcp.effective_schema import resolve_effective_schemas
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceString,
    EnforceVector,
    class_members,
    method_body,
    translate,
)
from tests.mcp_helpers import _content_json

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = addon_root() / "scripts"
TRIG_PATH = SCRIPTS / "4_World" / "MCP_PlayerTriggers.c"
TRIG = TRIG_PATH.read_text(encoding="utf-8")
BRIDGE = (SCRIPTS / "5_Mission" / "MCPBridge.c").read_text(encoding="utf-8")
CLIENT_BRIDGE = (SCRIPTS / "5_Mission" / "MCPClientBridge.c").read_text(encoding="utf-8")
MESSAGES = (SCRIPTS / "5_Mission" / "MCPMessages.c").read_text(encoding="utf-8")
LOOPBACK = (ROOT / "tools" / "dayz_mcp" / "loopback.py").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

SHAPE_NAME = "static string ShapeName(TriggerShape shape)"
RUN_ID = "static int RunId()"
NEXT_SEQ = "static int NextSeq()"
APPEND_EVENT = "protected void AppendEvent(string kind, MCPPlayerTriggerInside snap, float uptime)"
SNAPSHOT = "protected MCPPlayerTriggerInside Snapshot(ScriptedEntity trigger, float now)"
FIND_INSIDE = "protected int FindInside(ScriptedEntity trigger)"
PRUNE = "void PruneGone()"
RECORD_ENTER = "void RecordEnter(ScriptedEntity trigger)"
RECORD_LEAVE = "void RecordLeave(ScriptedEntity trigger)"
RECORD_ENTER_TRIGGER = "void MCPRecordEnterTrigger(ScriptedEntity trigger)"
RECORD_LEAVE_TRIGGER = "void MCPRecordLeaveTrigger(ScriptedEntity trigger)"
FILL = "protected void FillPlayerTriggers(PlayerBase body, MCPPlayerState state)"

EXPECTED_SERVER_CAPS = (
    "action_cursor_ids,bot_start,bot_stop,entities_query,exec_enforce,hands_attach,hands_take,"
    "infected_drive,inventory_attach,inventory_give,inventory_move,lease_close,loadout_apply,"
    "loadout_clear,loadout_get,nav_path_query,notify_players,object_anim,object_delete,object_doors,"
    "object_inspect,object_resolve,object_set_health,player_godmode,player_heal,player_kill,"
    "player_teleport,players_visibility,query_all_players,query_get_in_condition,query_player_state,"
    "scene_clear,scene_entities,scene_raycast,server_perf,surface_query,survivor_drive,"
    "survivor_drive_release,telemetry_read,vehicle_door,vehicle_enter,vehicle_prepare_fixture,"
    "weapon_state,world_spawn,world_spawn_batch,world_time_get,world_time_set,world_weather_set"
)
EXPECTED_CLIENT_CAPS = (
    "action_cursor,action_hold,action_hold_cancel,action_use,action_use_component,action_use_door,"
    "action_use_target,actor_emote,anim_timeline,camera_get,camera_path_play,camera_path_release,"
    "camera_set,camera_set_focus,chat_read,dev_free_camera_set,engine_set,gameplay_state,hud_set,"
    "input_describe,input_trigger,key_press,lease_close_client,player_look_at,player_look_at_release,"
    "player_move,player_respawn,player_trace,player_view_set,restore_gameplay,ui_click,ui_dialog,"
    "ui_focus,ui_pause,ui_reload_layout,ui_set_text,ui_tree,vehicle_control,vehicle_get_in_client,"
    "vehicle_release,vehicle_telemetry,vehicle_trace,weapon_aim,weapon_fire,weapon_raise,weapon_sights"
)


class _Vector(EnforceVector):
    def At(self, index: int) -> float:
        return self[index]


class _Inside:
    def __init__(self) -> None:
        self.type = EnforceString(b"")
        self.net_id_low = 0
        self.net_id_high = 0
        self.pos = EnforceArray()
        self.shape = EnforceString(b"")
        self.enter_uptime_s = 0.0


class _Event:
    def __init__(self) -> None:
        self.seq = 0
        self.kind = EnforceString(b"")
        self.type = EnforceString(b"")
        self.net_id_low = 0
        self.net_id_high = 0
        self.pos = EnforceArray()
        self.shape = EnforceString(b"")
        self.uptime_s = 0.0


class _Live:
    def __init__(self) -> None:
        self.trigger: Any = None
        self.entry: Any = None


class _Trigger:
    def __init__(self, type_name: str, low: int, high: int, pos: tuple[float, float, float], shape: int) -> None:
        self._type = type_name
        self._low = low
        self._high = high
        self._pos = pos
        self._shape = shape

    def GetType(self) -> EnforceString:
        return EnforceString.of(self._type)

    def NetLow(self) -> int:
        return self._low

    def NetHigh(self) -> int:
        return self._high

    def GetPosition(self) -> _Vector:
        return _Vector(self._pos)

    def GetTriggerShape(self) -> int:
        return self._shape


class _Game:
    def __init__(self, server_side: bool = True) -> None:
        self.tick = 10.0
        self._server = server_side

    def GetTickTime(self) -> float:
        return self.tick

    def IsServer(self) -> bool:
        return self._server


_TRIGGER_SHAPE = SimpleNamespace(BOX=0, SPHERE=1, CYLINDER=2)


def _snapshot_source() -> str:
    body = method_body(TRIG, SNAPSHOT)
    if "trigger.GetNetworkID(low, high);" not in body:
        raise AssertionError("Snapshot no longer reads the network id")
    body = body.replace(
        "trigger.GetNetworkID(low, high);",
        "low = trigger.NetLow(); high = trigger.NetHigh();",
    )
    body = re.sub(r"\bpos\[(\d+)\]", r"pos.At(\1)", body)
    return SNAPSHOT + "\n{\n" + body + "\n}\n"


class _Harness:
    """One shared namespace: the static seq, one loaded body at a time."""

    def __init__(self) -> None:
        self.game = _Game()
        ns: dict[str, Any] = {
            "GetGame": lambda: self.game,
            "_vector": _Vector,
            "TriggerShape": _TRIGGER_SHAPE,
            "MCPPlayerTriggerInside": _Inside,
            "MCPPlayerTriggerEvent": _Event,
            "MCPPlayerTriggerLive": _Live,
            "EVENTS_MAX": 32,
            "s_RunId": 0,
            "s_LastSeq": 0,
            "inside": EnforceArray(),
            "events": EnforceArray(),
            "evicted_seq": 0,
        }

        def run_id() -> int:
            if ns["s_RunId"] == 0:
                ns["s_RunId"] = 4242
            return ns["s_RunId"]

        ns["RunId"] = run_id
        self.ns = ns
        translate(TRIG, SHAPE_NAME, ns, class_types=("TriggerShape",))
        ns["MCPPlayerTriggers"] = SimpleNamespace(
            ShapeName=ns["ShapeName"], NextSeq=None, RunId=run_id, LastSeq=lambda: ns["s_LastSeq"]
        )
        translate(TRIG, NEXT_SEQ, ns, class_types=(), members=("s_RunId", "s_LastSeq"))
        ns["MCPPlayerTriggers"].NextSeq = ns["NextSeq"]
        translate(TRIG, APPEND_EVENT, ns, class_types=("MCPPlayerTriggerInside", "MCPPlayerTriggerEvent"), members=("events", "evicted_seq"))
        translate(_snapshot_source(), SNAPSHOT, ns, class_types=("MCPPlayerTriggerInside", "ScriptedEntity", "vector"))
        translate(TRIG, FIND_INSIDE, ns, class_types=("ScriptedEntity",), members=("inside",))
        translate(TRIG, PRUNE, ns, class_types=("MCPPlayerTriggerLive",), members=("inside",))
        translate(TRIG, RECORD_ENTER, ns, class_types=("MCPPlayerTriggerInside", "MCPPlayerTriggerLive", "ScriptedEntity"), members=("inside",))
        translate(TRIG, RECORD_LEAVE, ns, class_types=("MCPPlayerTriggerInside", "ScriptedEntity"), members=("inside",))
        self.new_body()

    def new_body(self) -> dict[str, Any]:
        body = {"inside": EnforceArray(), "events": EnforceArray(), "evicted_seq": 0}
        self.load(body)
        return body

    def load(self, body: dict[str, Any]) -> None:
        self.ns["inside"] = body["inside"]
        self.ns["events"] = body["events"]
        self.ns["evicted_seq"] = body["evicted_seq"]
        self.body = body

    def save(self) -> None:
        self.body["evicted_seq"] = self.ns["evicted_seq"]

    def enter(self, trigger: Any) -> None:
        self.ns["RecordEnter"](trigger)
        self.save()

    def leave(self, trigger: Any) -> None:
        self.ns["RecordLeave"](trigger)
        self.save()

    def prune(self) -> None:
        self.ns["PruneGone"]()
        self.save()


def _event_rows(body: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [
        (e.seq, e.kind.text, e.type.text, e.net_id_low, e.net_id_high, list(e.pos), e.shape.text, e.uptime_s)
        for e in body["events"]
    ]


class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = _Harness()

    def test_super_first_and_client_records_nothing(self) -> None:
        for override, helper in (
            ("override void OnEnterTrigger(ScriptedEntity trigger)", "MCPRecordEnterTrigger(trigger);"),
            ("override void OnLeaveTrigger(ScriptedEntity trigger)", "MCPRecordLeaveTrigger(trigger);"),
        ):
            with self.subTest(override=override):
                body = method_body(TRIG, override)
                super_call = "super.OnEnterTrigger(trigger);" if "Enter" in override else "super.OnLeaveTrigger(trigger);"
                self.assertLess(body.index(super_call), body.index(helper))
        # The server guard, translated: a client allocates and records nothing.
        calls: list[Any] = []
        fake_state = SimpleNamespace(RecordEnter=lambda t: calls.append(t), RecordLeave=lambda t: calls.append(t))
        ns: dict[str, Any] = {
            "GetGame": lambda: _Game(server_side=False),
            "MCPGetTriggerBody": lambda _create: fake_state,
            "m_MCPTriggerBody": None,
        }
        for signature in (RECORD_ENTER_TRIGGER, RECORD_LEAVE_TRIGGER):
            with self.subTest(signature=signature):
                record = translate(TRIG, signature, ns, class_types=("MCPPlayerTriggerBody", "ScriptedEntity"), members=("m_MCPTriggerBody",))
                record(_Trigger("T", 1, 2, (0.0, 0.0, 0.0), 0))
                self.assertEqual(calls, [])
                self.assertIsNone(ns["m_MCPTriggerBody"])

    def test_enter_then_leave(self) -> None:
        trigger = _Trigger("ContaminatedTrigger_Dynamic", 11, 22, (100.0, 5.0, 200.0), 2)
        self.h.enter(trigger)
        inside = self.h.body["inside"]
        self.assertEqual(len(inside), 1)
        entry = inside[0].entry
        self.assertEqual(
            (entry.type.text, entry.net_id_low, entry.net_id_high, list(entry.pos), entry.shape.text, entry.enter_uptime_s),
            ("ContaminatedTrigger_Dynamic", 11, 22, [100.0, 5.0, 200.0], "cylinder", 10.0),
        )
        self.h.leave(trigger)
        self.assertEqual(len(self.h.body["inside"]), 0)
        self.assertEqual(
            _event_rows(self.h.body),
            [
                (1, "enter", "ContaminatedTrigger_Dynamic", 11, 22, [100.0, 5.0, 200.0], "cylinder", 10.0),
                (2, "leave", "ContaminatedTrigger_Dynamic", 11, 22, [100.0, 5.0, 200.0], "cylinder", 10.0),
            ],
        )

    def test_shapes(self) -> None:
        for shape, name in ((0, "box"), (1, "sphere"), (2, "cylinder"), (99, "unknown")):
            with self.subTest(shape=name):
                body = self.h.new_body()
                self.h.enter(_Trigger("T", 1, 1, (0.0, 0.0, 0.0), shape))
                self.assertEqual(body["inside"][0].entry.shape.text, name)

    def test_ring_evicts_oldest_and_seq_is_shared(self) -> None:
        body_a = self.h.body
        for index in range(33):
            self.h.enter(_Trigger(f"T{index}", index, 0, (0.0, 0.0, 0.0), 0))
        self.assertEqual(len(body_a["events"]), 32)
        self.assertEqual(body_a["events"][0].seq, 2)
        self.assertEqual(body_a["events"][-1].seq, 33)
        self.assertEqual(body_a["evicted_seq"], 1)
        body_b = self.h.new_body()
        self.h.load(body_b)
        self.h.enter(_Trigger("Other", 5, 6, (1.0, 2.0, 3.0), 1))
        self.assertEqual(body_b["events"][0].seq, 34)

    def test_fake_remove_would_break_the_ring(self) -> None:
        # EnforceArray mirrors vanilla: Remove swaps the last element into
        # the hole (enscript.c:457-463), RemoveOrdered keeps the order
        # (:464-469), so the fake can no longer hide an unordered eviction.
        swapped = EnforceArray([1, 2, 3])
        swapped.Remove(0)
        self.assertEqual(list(swapped), [3, 2])
        ordered = EnforceArray([1, 2, 3])
        ordered.RemoveOrdered(0)
        self.assertEqual(list(ordered), [2, 3])
        ring = EnforceArray(range(1, 33))
        ring.Insert(33)
        ring.Remove(0)
        self.assertNotEqual(list(ring), list(range(2, 34)))
        self.assertEqual(ring[0], 33)
        ring = EnforceArray(range(1, 33))
        ring.Insert(33)
        ring.RemoveOrdered(0)
        self.assertEqual(list(ring), list(range(2, 34)))

    def test_gone_null_and_many_inside(self) -> None:
        trigger = _Trigger("AreaTrigger", 7, 8, (1.0, 2.0, 3.0), 1)
        self.h.enter(trigger)
        self.h.enter(None)
        self.assertEqual(len(_event_rows(self.h.body)), 1)
        # The weak reference reads null: one gone event, cached values.
        self.h.body["inside"][0].trigger = None
        self.h.game.tick = 25.0
        self.h.prune()
        self.assertEqual(len(self.h.body["inside"]), 0)
        rows = _event_rows(self.h.body)
        self.assertEqual(rows[-1], (2, "gone", "AreaTrigger", 7, 8, [1.0, 2.0, 3.0], "sphere", 25.0))
        body = self.h.new_body()
        self.h.load(body)
        for index in range(17):
            self.h.enter(_Trigger(f"M{index}", index, 0, (0.0, 0.0, 0.0), 0))
        self.assertEqual(len(body["inside"]), 17)


class _FillState:
    def __init__(self) -> None:
        self.triggers = EnforceArray()
        self.trigger_events = EnforceArray()
        self.trigger_run = 0
        self.trigger_seq = 0
        self.trigger_evicted_seq = 0


class _Tracker:
    def __init__(self, harness: _Harness, body: dict[str, Any]) -> None:
        self._h = harness
        self._body = body
        self.inside = body["inside"]
        self.events = body["events"]

    @property
    def evicted_seq(self) -> int:  # type: ignore[override]
        return self._body["evicted_seq"]

    def PruneGone(self) -> None:
        self._h.load(self._body)
        self._h.prune()


class FillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = _Harness()
        ns = self.h.ns
        ns["MCPPlayerTriggerInside"] = _Inside
        ns["MCPPlayerTriggerEvent"] = _Event
        self.fill = translate(
            BRIDGE, FILL, ns,
            class_types=("PlayerBase", "MCPPlayerState", "MCPPlayerTriggerBody", "MCPPlayerTriggerLive", "MCPPlayerTriggerInside", "MCPPlayerTriggerEvent"),
        )

    def _fill(self, tracker: Any) -> _FillState:
        state = _FillState()
        body = SimpleNamespace(MCPGetTriggerBody=lambda _create, _t=tracker: _t)
        self.fill(body, state)
        return state

    def test_empty_body_answers_empty(self) -> None:
        for tracker in (None, _Tracker(self.h, self.h.body)):
            with self.subTest(tracker=tracker):
                state = self._fill(tracker)
                self.assertEqual((list(state.triggers), list(state.trigger_events)), ([], []))
                self.assertEqual(state.trigger_run, 4242)

    def test_read_prunes_gone_and_copies(self) -> None:
        trigger = _Trigger("AreaTrigger", 7, 8, (1.0, 2.0, 3.0), 1)
        self.h.enter(trigger)
        tracker = _Tracker(self.h, self.h.body)
        state = self._fill(tracker)
        self.assertEqual(len(state.triggers), 1)
        self.assertIsNot(state.triggers[0], self.h.body["inside"][0].entry)
        self.assertEqual(state.triggers[0].type.text, "AreaTrigger")
        self.h.body["inside"][0].trigger = None
        state = self._fill(tracker)
        self.assertEqual(list(state.triggers), [])
        self.assertEqual([e.kind.text for e in state.trigger_events], ["enter", "gone"])
        # The result never aliases the live ring.
        self.assertIsNot(state.trigger_events[0], self.h.body["events"][0])


class RunIdTest(unittest.TestCase):
    def test_run_id_drawn_once_and_non_zero(self) -> None:
        # Translate the real RunId (f2): two reads give the same non-zero
        # run, drawn once. A fake run id here would hide a RunId that
        # always returns 0 (the next page would then be bad_args).
        draws: list[tuple[int, int]] = []

        def random_int_inclusive(low: int, high: int) -> int:
            draws.append((low, high))
            return 12345

        ns: dict[str, Any] = {
            "s_RunId": 0,
            "Math": SimpleNamespace(RandomIntInclusive=random_int_inclusive),
            "int": SimpleNamespace(MAX=2147483647),
        }
        run_id = translate(TRIG, RUN_ID, ns, members=("s_RunId",))
        first = run_id()
        second = run_id()
        self.assertNotEqual(first, 0)
        self.assertEqual(first, second)
        self.assertEqual(first, 12345)
        self.assertEqual(draws, [(1, 2147483646)])
        self.assertEqual(ns["s_RunId"], first)

    def test_trigger_body_holder_is_strong_ref(self) -> None:
        # Source pin (f2): the body holder lives in the modded PlayerBase
        # behind a strong ref, so the recorded state has a strong owner.
        # class_members cannot read a `modded class`, so pin the line.
        modded = TRIG[TRIG.index("modded class PlayerBase") :]
        self.assertEqual(
            re.findall(r"protected\s+(?:(ref)\s+)?MCPPlayerTriggerBody\s+m_MCPTriggerBody\s*;", modded),
            ["ref"],
        )


class DtoContractTest(unittest.TestCase):
    def test_dto_members_are_values_only(self) -> None:
        self.assertEqual(
            class_members(TRIG, "MCPPlayerTriggerInside"),
            [("string", "type"), ("int", "net_id_low"), ("int", "net_id_high"), ("ref array<float>", "pos"), ("string", "shape"), ("float", "enter_uptime_s")],
        )
        self.assertEqual(
            class_members(TRIG, "MCPPlayerTriggerEvent"),
            [("int", "seq"), ("string", "kind"), ("string", "type"), ("int", "net_id_low"), ("int", "net_id_high"), ("ref array<float>", "pos"), ("string", "shape"), ("float", "uptime_s")],
        )
        forbidden = {"Object", "ScriptedEntity", "EntityAI", "PlayerBase", "Man", "Trigger", "MCPPlayerTriggerLive", "MCPPlayerTriggerBody"}
        for name in ("MCPPlayerTriggerInside", "MCPPlayerTriggerEvent"):
            for type_name, _member in class_members(TRIG, name):
                self.assertNotIn(type_name.replace("ref ", ""), forbidden)

    def test_player_state_members(self) -> None:
        self.assertEqual(
            class_members(MESSAGES, "MCPPlayerState"),
            [
                ("string", "name"), ("ref array<float>", "pos"), ("bool", "godmode"), ("float", "water"), ("float", "energy"),
                ("ref array<ref MCPPlayerTriggerInside>", "triggers"), ("ref array<ref MCPPlayerTriggerEvent>", "trigger_events"),
                ("int", "trigger_run"), ("int", "trigger_seq"), ("int", "trigger_evicted_seq"),
            ],
        )

    def test_no_new_verb_or_schema_change(self) -> None:
        caps = "".join(re.findall(r'"([^"]*)"', re.search(r"SERVER_CAPABILITIES = ([^;]+);", BRIDGE).group(1)))  # type: ignore[union-attr]
        self.assertEqual(caps, EXPECTED_SERVER_CAPS)
        client = "".join(re.findall(r'"([^"]*)"', re.search(r"CLIENT_POLL_CAPS = ([^;]+);", CLIENT_BRIDGE).group(1)))  # type: ignore[union-attr]
        self.assertEqual(client, EXPECTED_CLIENT_CAPS)
        self.assertIn('"query_player_state": _command_schema(_schema_variant()),', LOOPBACK)
        self.assertIn('protected const string SERVER_ARG_CONTRACT_HASH = "3b800b7c7ee0674c";', BRIDGE)


def _answer(events: list[dict[str, Any]] | None, run: int = 4242, evicted: int = 0) -> dict[str, Any]:
    state: dict[str, Any] = {"name": "Survivor", "pos": [1.0, 2.0, 3.0], "godmode": False}
    if events is not None:
        state.update({"triggers": [], "trigger_events": events, "trigger_run": run, "trigger_seq": events[-1]["seq"] if events else 0, "trigger_evicted_seq": evicted})
    return {"state": state}


class PythonToolTest(unittest.IsolatedAsyncioTestCase):
    async def _call(self, arguments: dict[str, Any], *answers: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Any]]:
        app, runtime = server.build_app(server.ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        runtime.start_loopback()
        self.addCleanup(runtime.stop_loopback)
        real_enqueue = runtime.state.enqueue_command
        planted: dict[str, Any] = {}
        enqueued: list[Any] = []

        def enqueue(cmd, args, peer="server", operation_timeout_s=0.0, **_kw):
            enqueued.append(cmd)
            status, body = real_enqueue(cmd, args, peer=peer, operation_timeout_s=operation_timeout_s)
            if status == 200:
                payload = json.dumps({"id": body["id"], "ok": True, "error": "", "cmd": cmd, **planted})
                runtime.state.store_result(json.loads(payload))
            return status, body

        payloads = []
        with patch("dayz_mcp.server._world_read_not_ready", return_value=None):
            with patch.object(runtime.state, "enqueue_command", enqueue):
                for answer in answers:
                    planted.clear()
                    planted.update(answer)
                    payloads.append(_content_json(await app.call_tool("query_player_state", {**arguments, "timeout_s": 2.0})))
        return payloads, enqueued

    async def test_cursor_filter_next_and_overflow(self) -> None:
        events = [{"seq": n, "kind": "enter"} for n in (1, 2, 3, 4)]
        (payload,), _ = await self._call({"trigger_cursor": 2, "trigger_run": 4242}, _answer(events, evicted=3))
        state = payload["state"]
        self.assertEqual([e["seq"] for e in state["trigger_events"]], [3, 4])
        self.assertEqual(state["trigger_next_cursor"], 4)
        self.assertEqual(state["trigger_events_overflow"], True)
        self.assertEqual(state["trigger_cursor_reset"], False)
        (payload,), _ = await self._call({"trigger_cursor": 3, "trigger_run": 4242}, _answer(events, evicted=3))
        self.assertEqual(payload["state"]["trigger_events_overflow"], False)

    async def test_other_run_resets_and_returns_every_event(self) -> None:
        events = [{"seq": n, "kind": "enter"} for n in (1, 2, 3)]
        (payload,), _ = await self._call({"trigger_cursor": 2, "trigger_run": 999}, _answer(events))
        state = payload["state"]
        self.assertEqual(state["trigger_cursor_reset"], True)
        self.assertEqual([e["seq"] for e in state["trigger_events"]], [1, 2, 3])
        self.assertEqual(state["trigger_next_cursor"], 3)

    async def test_bad_args_refused_without_bridge_call(self) -> None:
        for arguments in ({"trigger_cursor": -1}, {"trigger_run": -1}, {"trigger_cursor": 1, "trigger_run": 0}):
            with self.subTest(arguments=arguments):
                app, runtime = server.build_app(server.ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
                runtime.start_loopback()
                self.addCleanup(runtime.stop_loopback)
                enqueued: list[Any] = []
                with patch.object(runtime.state, "enqueue_command", lambda *a, **k: enqueued.append(a) or (200, {"id": 1})):
                    with self.assertRaises(Exception) as caught:
                        await app.call_tool("query_player_state", {**arguments, "timeout_s": 2.0})
                self.assertIn("bad_args", str(caught.exception))
                self.assertEqual(enqueued, [])

    async def test_older_pbo_passes_through(self) -> None:
        answer = _answer(None)
        (payload,), _ = await self._call({}, answer)
        self.assertEqual(payload["state"], answer["state"])
        self.assertNotIn("trigger_next_cursor", payload["state"])


class DocsTest(unittest.TestCase):
    def test_description_and_changelog(self) -> None:
        description = resolve_effective_schemas()["query_player_state"]["description"]
        for sentence in (
            "triggers is the list of scripted triggers (Trigger subclasses) the server-side body is inside now, empty when none",
            "trigger_events are its enter/leave/gone events with a monotonic seq, paged with trigger_cursor plus trigger_run like player_trace's cursor",
            "A trigger deleted while the player is inside shows up as a gone event when it is next noticed",
            "The ring keeps 32 events per body and trigger_events_overflow says some were lost",
            "A respawned body starts empty; an older PBO leaves these fields out",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, description)
        unreleased = CHANGELOG[CHANGELOG.index("## [Unreleased]") : CHANGELOG.index("## [1.5]")]
        self.assertIn("fb-20261112-103904-fa2b", unreleased)


if __name__ == "__main__":
    unittest.main()
