"""Expansion AI adapter ai_emote (plan 3.5/4, ficha 36fa NPC half).

Offline criteria O8 and E8. Each test names the mutant it kills; the
commit message carries the criterion -> test -> mutant table.
"""

from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import loopback, server
from dayz_mcp.bridge_errors import _bridge_error
from dayz_mcp.bridge_readiness import (
    EXPANSION_AI_VERB_BITS,
    EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH,
    parse_xai,
)
from dayz_mcp.session_coordination import command_requires_lease
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    method_body,
    translate,
)
from tests.fence_helpers import bound_queue
from tests.lease_helpers import _identity
from tests.mcp_helpers import _content_json
from tests.xai_helpers import (
    adapter_census,
    announce as _shared_announce,
    bound_state as _shared_bound_state,
)

EMOTE = "ai_emote"
GOOD_XAI = EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
ADAPTER = (
    addon_root().parent
    / "addon_expansion_ai"
    / "scripts"
    / "5_Mission"
    / "MCPExpansionAI.c"
)
_S = EnforceString.of


def _run(coro):
    while True:
        try:
            yielded = coro.send(None)
        except StopIteration as stop:
            return stop.value
        if yielded is not None:
            raise AssertionError(f"tool call yielded {yielded!r} to a loop")


class AiEmoteToolTest(unittest.TestCase):
    """O8: object_id routes actor_emote to ai_emote on the server."""

    def setUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _m: None)
        )

    def test_o8_without_object_id_goes_to_the_client_as_actor_emote(self) -> None:
        # Mutant killed: the NPC path swallowing the plain client call.
        call = AsyncMock(return_value={"ok": 1, "emote_state": "finished"})
        with patch.object(self.runtime, "call_bridge", new=call):
            _run(self.app.call_tool("actor_emote", {"emote": "nod", "wait_s": 2.0}))
        self.assertEqual(
            [item.args for item in call.await_args_list],
            [("actor_emote", {"emote": "nod", "wait_s": 2.0}, "client", 15.0)],
        )

    def test_o8_with_object_id_goes_to_the_server_as_ai_emote_with_lease(self) -> None:
        # Mutants killed: ai_emote routed to the client, sent without a
        # lease (read-only), or actor_emote gaining the object_id key.
        reply = {
            "ok": 1,
            "emote_state": "finished",
            "emote_pending": False,
            "started_after_s": 1.0,
            "finished_after_s": 3.0,
        }
        call = AsyncMock(return_value=dict(reply))
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(
                _run(
                    self.app.call_tool(
                        "actor_emote",
                        {"emote": "nod", "wait_s": 2.0, "object_id": 11},
                    )
                )
            )
        self.assertEqual(result, reply)
        self.assertEqual(
            [item.args for item in call.await_args_list],
            [
                (
                    "ai_emote",
                    {"object_id": 11, "emote": "nod", "wait_s": 2.0},
                    "server",
                    15.0,
                )
            ],
        )
        self.assertEqual(loopback.peer_for_command(EMOTE), "server")
        self.assertEqual(loopback.peer_for_command("actor_emote"), "client")
        self.assertIn(EMOTE, loopback.SERVER_COMMANDS)
        self.assertNotIn(EMOTE, loopback.CLIENT_COMMANDS)
        self.assertTrue(command_requires_lease(EMOTE))

    def test_o8_object_id_must_be_a_positive_int(self) -> None:
        # Mutant killed: object_id 0/true forwarded (the bridge would hunt
        # a member that cannot exist).
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            for bad in (0, -1):
                with self.subTest(object_id=bad):
                    with self.assertRaises(ToolError) as raised:
                        _run(
                            self.app.call_tool(
                                "actor_emote",
                                {"emote": "nod", "object_id": bad},
                            )
                        )
                    self.assertIn("bad_args", str(raised.exception))
        call.assert_not_awaited()

    def test_ai_emote_pending_crosses_the_tool_error(self) -> None:
        # Mutant killed: emote_pending dropped on ok=false, where only the
        # ToolError text crosses (plan 3.5/4).
        timed_out = {"ok": False, "error": "emote_timed_out", "emote_state": "timed_out", "emote_pending": True}
        self.assertEqual(str(_bridge_error(timed_out, EMOTE)),
                         "emote_timed_out; observed=emote_state=timed_out emote_pending=true")
        self.assertEqual(str(_bridge_error(timed_out, "actor_emote")),
                         "emote_timed_out; observed=emote_state=timed_out")
        rejected = {"ok": False, "error": "emote_rejected", "emote_state": "rejected", "emote_pending": True}
        self.assertEqual(str(_bridge_error(rejected, EMOTE)),
                         "emote_rejected; observed=emote_state=rejected emote_pending=true")
        interrupted = {"ok": False, "error": "emote_interrupted", "emote_state": "interrupted", "emote_pending": False}
        self.assertEqual(str(_bridge_error(interrupted, EMOTE)),
                         "emote_interrupted; observed=emote_pending=false")
        nothing = {"ok": False, "error": "no_emote_playing", "emote_state": "rejected"}
        self.assertEqual(str(_bridge_error(nothing, EMOTE)),
                         "no_emote_playing; observed=emote_state=rejected emote_pending=false")

    def test_o8_server_states_keep_times_only_where_reached(self) -> None:
        # Mutant killed: a cancelled/timed_out answer keeping a start time
        # the watch never reached.
        call = AsyncMock(
            return_value={
                "ok": 1,
                "emote_state": "cancelled",
                "emote_pending": False,
                "started_after_s": 9.0,
                "finished_after_s": 9.0,
            }
        )
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(
                _run(
                    self.app.call_tool(
                        "actor_emote",
                        {"emote": "end", "object_id": 11},
                    )
                )
            )
        self.assertEqual(
            result, {"ok": 1, "emote_state": "cancelled", "emote_pending": False}
        )

    def test_ai_emote_bad_target_unknown_or_idle_reports_emote_pending_false(self) -> None:
        # P2-2 negative: bad_target (and bad_args) when object_id names no
        # existing member — or an existing member with no pending request —
        # carries observed=emote_pending=false without inventing emote_state=,
        # while emote_rejected and client actor_emote keep today's text.
        namespace = _emote_namespace()
        other = _FakeMember()
        other.m_Expansion_EmoteID = _EMOTE_IDS["nod"]
        _setup(namespace, other)
        result = _new_result()
        result.emote_pending = True
        handled = namespace["DispatchAIEmote"](
            SimpleNamespace(
                id=1,
                cmd=_S(EMOTE),
                args=SimpleNamespace(
                    object_id=999, emote=_S("nod"), wait_s=10.0
                ),
            ),
            result,
        )
        self.assertEqual(handled, 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.text, "bad_target")
        self.assertFalse(result.emote_pending)
        wire = _wire_from_result(result)
        self.assertEqual(
            str(_bridge_error(wire, EMOTE)),
            "bad_target; observed=emote_pending=false",
        )
        self.assertEqual(str(_bridge_error(wire, "actor_emote")), "bad_target")

        # Releasing group with gone (null) or idle member -> emote_pending=false.
        for member in (None, _FakeMember()):
            with self.subTest(releasing_member_present=member is not None):
                namespace = _emote_namespace()
                _setup(namespace, member, group_state="releasing")
                handled, rel_result = _dispatch(
                    namespace, emote="nod", object_id=11
                )
                self.assertEqual(handled, 1)
                self.assertEqual(rel_result.error.text, "bad_target")
                self.assertFalse(rel_result.emote_pending)
                self.assertEqual(
                    str(_bridge_error(_wire_from_result(rel_result), EMOTE)),
                    "bad_target; observed=emote_pending=false",
                )

        # Missing args, bad_args with unknown member, and no_emote_playing
        # actively write emote_pending=false even on a dirty result object.
        namespace = _emote_namespace()
        _setup(namespace, _FakeMember())
        for args, expected_code in (
            (None, "bad_args"),
            (
                SimpleNamespace(object_id=999, emote=_S("nope"), wait_s=10.0),
                "bad_args",
            ),
            (
                SimpleNamespace(object_id=999, emote=_S("nod"), wait_s=99.0),
                "bad_args",
            ),
            (
                SimpleNamespace(object_id=11, emote=_S("end"), wait_s=10.0),
                "no_emote_playing",
            ),
        ):
            with self.subTest(expected_code=expected_code, has_args=args is not None):
                dirty = _new_result()
                dirty.emote_pending = True
                handled = namespace["DispatchAIEmote"](
                    SimpleNamespace(id=2, cmd=_S(EMOTE), args=args), dirty
                )
                self.assertEqual(handled, 1)
                self.assertEqual(dirty.error.text, expected_code)
                self.assertFalse(dirty.emote_pending)

        # emote_rejected preserves today's emote_state=rejected pair.
        namespace = _emote_namespace()
        _setup(namespace, _FakeMember(fighting=True))
        _handled, rejected = _dispatch(namespace, emote="nod", object_id=11)
        self.assertEqual(
            str(_bridge_error(_wire_from_result(rejected), EMOTE)),
            "emote_rejected; observed=emote_state=rejected emote_pending=false",
        )


class AiEmoteAdmissionTest(unittest.TestCase):
    """O8/2.2 at the broker: ai_emote is a new adapter mutation."""

    def _enqueue(self, state: loopback.ServerState):
        coord = state.coordination
        client = _identity("emote")
        status, lease = coord.acquire(client, "trace")
        assert status == 200, lease
        payload = {
            "platform": client.platform, "pid": client.pid, "ppid": client.ppid,
            "started_at_utc": client.started_at_utc,
            "session_id": client.session_id, "task_label": client.task_label,
        }
        return state.enqueue_command(
            EMOTE,
            {"object_id": 11, "emote": "nod", "wait_s": 10.0},
            peer="server",
            identity_payload=payload,
            lease_token=lease["lease_token"],
            operation_timeout_s=30.0,
        )

    def _announce(self, state, commands, xai=GOOD_XAI):
        _shared_announce(
            state, commands, server.EXPECTED_SERVER_ARG_CONTRACT_HASH, xai
        )

    def test_ai_emote_admitted_with_adapter_and_close_support(self) -> None:
        # Mutant killed: ai_emote outside the expansion admission set (it
        # would read bridge_capability_missing or skip the xai check).
        state = _shared_bound_state()
        self._announce(state, adapter_census())
        status, body = self._enqueue(state)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(bound_queue(state, "server")), 1)
        self.assertIn(EMOTE, loopback._EXPANSION_NEW_MUTATION_COMMANDS)

    def test_ai_emote_missing_verb_is_adapter_not_loaded(self) -> None:
        state = _shared_bound_state()
        self._announce(
            state, [name for name in adapter_census() if name != EMOTE]
        )
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(
            body, {"error": "expansion_unavailable", "detail": "adapter_not_loaded"}
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_ai_emote_bad_xai_is_adapter_contract_mismatch(self) -> None:
        state = _shared_bound_state()
        self._announce(state, adapter_census(), xai="0" * 16)
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(
            body,
            {"error": "expansion_unavailable", "detail": "adapter_contract_mismatch"},
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_ai_emote_without_close_support_is_refused_before_the_game(self) -> None:
        # Mutant killed: ai_emote admitted as a mutation while no peer
        # announces its real lease close (plan 5.1 gate).
        state = _shared_bound_state()
        commands = [
            name
            for name in adapter_census()
            if name not in ("lease_close", "lease_close_client")
        ]
        self._announce(state, commands)
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(
            body,
            {"error": "expansion_unavailable", "detail": "lease_close_not_supported"},
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_ai_emote_stays_out_of_the_live_profile_and_its_doc_section(self) -> None:
        # PX3 c: the LIVE_ALLOWED_COMMANDS paragraph is about ai_group_state.
        # Under the ai_emote heading it read as if the mutation were a live
        # read. Mutants killed: ai_emote joining LIVE_ALLOWED_COMMANDS, and
        # the paragraph back in the ai_emote section.
        self.assertNotIn(EMOTE, loopback.LIVE_ALLOWED_COMMANDS)
        doc = (addon_root().parent / "docs" / "expansion-ai-adapter.md").read_text(encoding="utf-8")
        paragraph = "As a server read it is also admitted under the live profile:"
        self.assertEqual(doc.count(paragraph), 1)
        reading = doc.index("## Reading a group (`ai_group_state`")
        emote = doc.index("## Playing an emote on a member (`ai_emote`")
        self.assertLess(reading, doc.index(paragraph))
        self.assertLess(doc.index(paragraph), emote)

    def test_mask_bit4_is_ai_emote_append_only(self) -> None:
        # Mutant killed: ai_emote reusing an earlier bit (an old adapter
        # would announce a different verb under the same mask).
        self.assertEqual(EXPANSION_AI_VERB_BITS[4], EMOTE)
        self.assertEqual(
            EXPANSION_AI_VERB_BITS[:4],
            ("ai_group_release", "ai_group_spawn", "ai_group_order", "ai_group_state"),
        )
        source = ADAPTER.read_text(encoding="utf-8")
        verbs = re.search(r'EXPANSION_AI_VERBS = "([0-9]+)"', source)
        self.assertIsNotNone(verbs)
        self.assertEqual(verbs.group(1), "31")
        _hash, names = parse_xai(f"{GOOD_XAI}.{verbs.group(1)}")
        self.assertEqual(names[-1], EMOTE)


# --- E8: translated Enforce bodies ---------------------------------------

_EMOTE_IDS = {
    name: index + 1 for index, name in enumerate(loopback.ACTOR_EMOTES)
}


def _same_scheduled_fn(left, right) -> bool:
    left_self = getattr(left, "__self__", None)
    right_self = getattr(right, "__self__", None)
    if left_self is not None or right_self is not None:
        return (
            left_self is right_self
            and getattr(left, "__func__", None) is getattr(right, "__func__", None)
        )
    return left == right


class _FakeCallQueue:
    """ScriptCallQueue double (2_gamelib/tools.c:52-74): CallLater by time, Remove by fn and instance."""

    def __init__(self, clock: SimpleNamespace) -> None:
        self._clock = clock
        self._entries: list[tuple[float, object]] = []

    def CallLater(self, fn, delay: int = 0, repeat: bool = False, *_args) -> None:
        if repeat:
            raise AssertionError("ai_emote tests do not expect repeat=True")
        self._entries.append((self._clock.now + (float(delay) / 1000.0), fn))

    def Remove(self, fn) -> None:
        self._entries = [
            (due_s, entry_fn)
            for due_s, entry_fn in self._entries
            if not _same_scheduled_fn(entry_fn, fn)
        ]

    def advance_to(self, now_s: float) -> None:
        due = [entry for entry in self._entries if entry[0] <= now_s]
        self._entries = [entry for entry in self._entries if entry[0] > now_s]
        for _due_s, fn in due:
            fn()


class _FakeEmoteManager:
    def __init__(self) -> None:
        self.m_bEmoteIsPlaying = False
        self.m_bEmoteIsRequestPending = False
        self.gesture = 0
        self.cancel_calls = 0

    def IsEmotePlaying(self) -> bool:
        return self.m_bEmoteIsPlaying or self.m_bEmoteIsRequestPending

    def Expansion_GetCurrentGesture(self) -> int:
        return self.gesture

    def ServerRequestEmoteCancel(self) -> None:
        self.cancel_calls += 1
        self.m_bEmoteIsPlaying = False
        self.gesture = 0


class _FakeGroup:
    def __init__(self, in_combat: bool = False) -> None:
        self._in_combat = in_combat

    def IsInCombat(self) -> bool:
        return self._in_combat


class _FakeMember:
    """An eAIBase double with both emote layers; every write is counted."""

    def __init__(self, *, threat: float = 0.0, fighting: bool = False) -> None:
        self.alive = True
        self.m_eAI_IsFightingFSM = fighting
        self.m_Expansion_EmoteID = 0
        self.m_Expansion_EmoteAutoCancel = False
        self.m_Expansion_EmoteAutoCancelDelay = 0
        self._threat = threat
        self.emotes = _FakeEmoteManager()
        self.set_emote_calls: list[tuple] = []
        self.play_calls = 0
        self.setter_calls = 0

    def IsAlive(self) -> bool:
        return self.alive

    def GetEmoteManager(self):
        return self.emotes

    def GetThreatToSelf(self, ignore_los: bool = False) -> float:
        return self._threat

    def Expansion_SetEmote(self, emote_id, auto_cancel=False, delay=0) -> None:
        self.set_emote_calls.append((emote_id, auto_cancel, delay))
        self.m_Expansion_EmoteID = emote_id
        self.m_Expansion_EmoteAutoCancel = bool(auto_cancel)
        self.m_Expansion_EmoteAutoCancelDelay = int(delay)

    def Expansion_PlayEmote(self) -> None:
        self.play_calls += 1

    def SetMovementSpeedLimits(self, *_a) -> None:
        self.setter_calls += 1

    def eAI_Despawn(self) -> None:
        self.setter_calls += 1


def _new_job() -> SimpleNamespace:
    return SimpleNamespace(
        id=0, kind=_S(""), args=None, subject=None, actor=None,
        deadline_s=0.0, emote_end=False, emote_started=False,
        emote_request_s=0.0, emote_started_s=0.0, emote_finished_s=0.0,
        wait_s=0.0, emote_gesture_id=0, emote_member_id=0,
        emote_pending=False, emote_interrupted=False,
        tick_poll_sent=0, tick_poll_callback=0, tick_dispatch=0,
    )


def _new_result() -> SimpleNamespace:
    return SimpleNamespace(
        id=0, ok=False, error=_S(""), emote_state=_S(""),
        started_after_s=0.0, finished_after_s=0.0, emote_pending=False,
        tick_poll_sent=0, tick_poll_callback=0, tick_dispatch=0,
    )


def _wire_from_result(result: SimpleNamespace) -> dict[str, object]:
    return {
        "id": result.id,
        "ok": result.ok,
        "error": result.error.text,
        "emote_state": result.emote_state.text,
        "started_after_s": result.started_after_s,
        "finished_after_s": result.finished_after_s,
        "emote_pending": result.emote_pending,
    }


def _emote_namespace() -> dict:
    adapter = ADAPTER.read_text(encoding="utf-8")
    bridge = BRIDGE.read_text(encoding="utf-8")
    clock = SimpleNamespace(now=100.0)
    posted: list = []
    system_queue = _FakeCallQueue(clock)
    game = SimpleNamespace(
        GetTickTime=lambda: clock.now,
        GetCallQueue=lambda category: system_queue if category == 0 else None,
    )
    namespace: dict[str, object] = {
        "_S": EnforceString,
        "m_AIGroups": EnforceArray(),
        "m_Jobs": EnforceMap(),
        "m_ElapsedS": 100.0,
        "CALL_CATEGORY_SYSTEM": 0,
        "GetGame": lambda: game,
        "PostResult": posted.append,
        "Math": SimpleNamespace(Floor=lambda value: float(math.floor(value))),
        "IsFiniteFloat": lambda value: isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value),
        "EmoteConstants": SimpleNamespace(
            **{f"ID_EMOTE_{name.upper()}": ident for name, ident in _EMOTE_IDS.items()}
        ),
        "eAIBase": SimpleNamespace(Cast=lambda value: value),
        "MCPJob": _new_job,
        "MCPResult": _new_result,
        "_clock": clock,
        "_posted": posted,
        "_system_queue": system_queue,
        "AI_EMOTE_WAIT_MIN_S": 0.5,
        "AI_EMOTE_WAIT_MAX_S": 30.0,
    }
    class_types = (
        "MCPAIGroupRecord", "MCPAIGroupMemberRecord", "eAIBase",
        "EmoteManager", "ScriptCallQueue", "MCPJob", "MCPResult",
    )
    if "protected void AIClearEmoteAutoCancel(EmoteManager emotes)" in adapter:
        translate(
            adapter,
            "protected void AIClearEmoteAutoCancel(EmoteManager emotes)",
            namespace,
            members=(),
            class_types=class_types,
        )
    for signature, members in (
        ("protected void EnsureAIRegistry()", ("m_AIGroups",)),
        (
            "protected MCPAIGroupRecord AIFindEmoteGroup(int objectId)",
            ("m_AIGroups",),
        ),
        (
            "protected MCPAIGroupMemberRecord AIFindEmoteMember(MCPAIGroupRecord record, int objectId)",
            (),
        ),
        ("protected bool AIEmoteJobOpen(int objectId)", ("m_Jobs",)),
        (
            "protected bool AIEmotePending(eAIBase member, EmoteManager emotes)",
            (),
        ),
        ("protected int AIEmoteId(string emote)", ()),
        (
            "protected int DispatchAIEmote(MCPCommand command, MCPResult result)",
            ("m_AIGroups", "m_Jobs", "m_ElapsedS", "AI_EMOTE_WAIT_MIN_S", "AI_EMOTE_WAIT_MAX_S"),
        ),
        ("protected override bool IsJobReady(MCPJob job)", ()),
        ("protected override void PostJobSuccess(MCPJob job)", ()),
        ("protected override void PostJobTimeout(MCPJob job)", ()),
    ):
        translate(adapter, signature, namespace, members=members, class_types=class_types)
    if "protected bool AIEmotePendingByObjectId(int objectId)" in adapter:
        translate(
            adapter,
            "protected bool AIEmotePendingByObjectId(int objectId)",
            namespace,
            members=("m_AIGroups",),
            class_types=class_types,
        )
    translate(
        bridge,
        "protected void ProcessJobs()",
        namespace,
        members=("m_Jobs", "m_ElapsedS"),
        class_types=("MCPJob",),
    )
    return namespace


def _setup(namespace, member: _FakeMember, *, group_state: str = "active", in_combat: bool = False):
    record = SimpleNamespace(
        group_state=_S(group_state),
        group=_FakeGroup(in_combat),
        members=EnforceArray(
            [SimpleNamespace(object_id=11, member=member)]
        ),
    )
    namespace["m_AIGroups"] = EnforceArray([record])
    return record


def _dispatch(namespace, *, emote: str = "nod", object_id: int = 11, wait_s: float = 10.0, cmd_id: int = 1):
    command = SimpleNamespace(
        id=cmd_id,
        cmd=_S(EMOTE),
        args=SimpleNamespace(
            object_id=object_id, emote=_S(emote), wait_s=wait_s
        ),
    )
    result = _new_result()
    handled = namespace["DispatchAIEmote"](command, result)
    return handled, result


def _set_clock(namespace, at: float) -> None:
    namespace["_clock"].now = at
    namespace["m_ElapsedS"] = at


def _tick(namespace, at: float) -> None:
    _set_clock(namespace, at)
    namespace["_system_queue"].advance_to(at)
    namespace["ProcessJobs"]()


def _start_static_pose_in_fsm(namespace, member: _FakeMember, at: float) -> None:
    """Run one FSM transition into a static pose and tick at `at` (eAIBase.c:10914)."""
    _set_clock(namespace, at)
    namespace["_system_queue"].advance_to(at)
    emote_id = member.m_Expansion_EmoteID
    assert emote_id > 0 and not member.emotes.IsEmotePlaying()
    member.emotes.m_bEmoteIsPlaying = True
    member.emotes.gesture = emote_id
    if member.m_Expansion_EmoteAutoCancel:
        delay_ms = member.m_Expansion_EmoteAutoCancelDelay or 2000
        namespace["_system_queue"].CallLater(
            member.emotes.ServerRequestEmoteCancel, delay_ms
        )
        member.m_Expansion_EmoteID = 0
    namespace["ProcessJobs"]()


class AiEmoteEnforceTest(unittest.TestCase):
    """E8: emote through the translated dispatch and the real job tick."""

    def test_e8_combat_or_threat_rejects_with_zero_set_emote_calls(self) -> None:
        # Mutant killed: the combat guard read after the write, or with a
        # higher threshold than eAIState_PlayEmote's 0.2.
        for member, in_combat in (
            (_FakeMember(fighting=True), False),
            (_FakeMember(), True),
            (_FakeMember(threat=0.21), False),
        ):
            with self.subTest(threat=member._threat, fighting=member.m_eAI_IsFightingFSM, in_combat=in_combat):
                namespace = _emote_namespace()
                _setup(namespace, member, in_combat=in_combat)
                handled, result = _dispatch(namespace)
                self.assertEqual(handled, 1)
                self.assertEqual(result.error.text, "emote_rejected")
                self.assertEqual(member.set_emote_calls, [])
                self.assertEqual(namespace["m_Jobs"].Count(), 0)

    def test_e8_second_job_same_member_rejects_end_included(self) -> None:
        # Mutant killed: the one-job guard counting only non-end jobs.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        handled, _result = _dispatch(namespace)
        self.assertEqual(handled, 2)
        for emote in ("nod", "end"):
            with self.subTest(emote=emote):
                handled, result = _dispatch(namespace, emote=emote, cmd_id=2)
                self.assertEqual(handled, 1)
                self.assertEqual(result.error.text, "emote_rejected")
        self.assertEqual(len(member.set_emote_calls), 1)

    def test_e8_playing_or_pending_in_either_layer_rejects(self) -> None:
        # Mutants killed: started accepting the gesture already sounding
        # with the same id (no dispatch rejection), and a pending request
        # in one layer read as free.
        cases = []
        playing = _FakeMember()
        playing.emotes.m_bEmoteIsPlaying = True
        playing.emotes.gesture = _EMOTE_IDS["nod"]
        cases.append((playing, False))
        expansion_pending = _FakeMember()
        expansion_pending.m_Expansion_EmoteID = _EMOTE_IDS["nod"]
        cases.append((expansion_pending, True))
        vanilla_pending = _FakeMember()
        vanilla_pending.emotes.m_bEmoteIsRequestPending = True
        cases.append((vanilla_pending, True))
        for member, expected_pending in cases:
            with self.subTest(member=id(member)):
                namespace = _emote_namespace()
                _setup(namespace, member)
                handled, result = _dispatch(namespace)
                self.assertEqual(handled, 1)
                self.assertEqual(result.error.text, "emote_rejected")
                self.assertEqual(result.emote_pending, expected_pending)
                self.assertEqual(member.set_emote_calls, [])
                self.assertEqual(namespace["m_Jobs"].Count(), 0)

    def test_e8_at_rest_one_set_emote_call_in_the_dispatch(self) -> None:
        # Mutants killed: the job (not the dispatch) calling
        # Expansion_SetEmote, autoCancel false, and Expansion_PlayEmote
        # anywhere (it would skip the FSM guard).
        namespace = _emote_namespace()
        member = _FakeMember(threat=0.2)
        _setup(namespace, member)
        handled, _result = _dispatch(namespace)
        self.assertEqual(handled, 2)
        self.assertEqual(
            member.set_emote_calls, [(_EMOTE_IDS["nod"], True, 10000.0)]
        )
        self.assertEqual(member.play_calls, 0)
        self.assertEqual(namespace["m_Jobs"].Count(), 1)

    def test_e8_started_then_finished_with_both_times(self) -> None:
        # Mutants killed: started on the pending flag alone, finished
        # without a seen start, and times counted from the first tick.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.m_Expansion_EmoteID = 0  # Expansion consumed its attempt.
        member.emotes.m_bEmoteIsPlaying = True
        member.emotes.gesture = _EMOTE_IDS["nod"]
        _tick(namespace, 101.0)
        self.assertEqual(namespace["_posted"], [])
        member.emotes.m_bEmoteIsPlaying = False
        _tick(namespace, 103.0)
        self.assertEqual(len(namespace["_posted"]), 1)
        result = namespace["_posted"][0]
        self.assertTrue(result.ok)
        self.assertEqual(result.emote_state.text, "finished")
        self.assertEqual(result.started_after_s, 1.0)
        self.assertEqual(result.finished_after_s, 3.0)
        self.assertFalse(result.emote_pending)

    def test_e8_finished_with_unconsumed_request_reports_pending_true(self) -> None:
        # Mutant killed: finished always answering emote_pending false (a
        # request Expansion never consumed can replay the gesture).
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.emotes.m_bEmoteIsPlaying = True
        member.emotes.gesture = _EMOTE_IDS["nod"]
        _tick(namespace, 101.0)
        member.emotes.m_bEmoteIsPlaying = False
        _tick(namespace, 102.0)
        result = namespace["_posted"][0]
        self.assertEqual(result.emote_state.text, "finished")
        self.assertTrue(result.emote_pending)

    def test_e8_started_at_wait_s_and_timed_out_without_a_start(self) -> None:
        # Mutant killed: the timeout answering finished, or a still-pending
        # request read as started.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.m_Expansion_EmoteID = 0
        member.emotes.m_bEmoteIsPlaying = True
        member.emotes.gesture = _EMOTE_IDS["nod"]
        _tick(namespace, 105.0)
        _tick(namespace, 111.0)
        result = namespace["_posted"][0]
        self.assertTrue(result.ok)
        self.assertEqual(result.emote_state.text, "started")
        self.assertEqual(result.started_after_s, 5.0)

        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.emotes.m_bEmoteIsRequestPending = True
        _tick(namespace, 105.0)
        _tick(namespace, 111.0)
        result = namespace["_posted"][0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error.text, "emote_timed_out")
        self.assertTrue(result.emote_pending)

    def test_e8_started_requires_the_requested_gesture(self) -> None:
        # Mutant killed: started without comparing the gesture id (an
        # ambient Expansion gesture would read as the requested one).
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.m_Expansion_EmoteID = 0
        member.emotes.m_bEmoteIsPlaying = True
        member.emotes.gesture = _EMOTE_IDS["shrug"]
        _tick(namespace, 101.0)
        member.emotes.m_bEmoteIsPlaying = False
        member.emotes.gesture = 0
        _tick(namespace, 102.0)
        self.assertEqual(namespace["_posted"], [])
        _tick(namespace, 111.0)
        self.assertEqual(namespace["_posted"][0].error.text, "emote_timed_out")

    def test_e8_timeout_reads_pending_at_the_timeout(self) -> None:
        # Mutant killed: PostJobTimeout reusing the last IsJobReady read
        # (stale past wait_s, and absent on the lease-close path).
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace, wait_s=0.5)
        _tick(namespace, 100.6)  # first tick already past wait_s
        self.assertTrue(namespace["_posted"][0].emote_pending)

        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        _tick(namespace, 109.0)
        member.m_Expansion_EmoteID = 0  # consumed after the last read
        _tick(namespace, 110.5)
        self.assertFalse(namespace["_posted"][0].emote_pending)

    def test_e8_death_seen_at_the_first_tick_past_wait_s_interrupts(self) -> None:
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        namespace["m_Jobs"].GetElement(0).subject = None
        _tick(namespace, 110.5)
        self.assertEqual(namespace["_posted"][0].error.text, "emote_interrupted")

    def test_e8_timeout_with_the_member_gone_still_answers(self) -> None:
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        job = namespace["m_Jobs"].GetElement(0)
        job.subject = None
        namespace["PostJobTimeout"](job)
        self.assertEqual(namespace["_posted"][0].error.text, "emote_timed_out")
        self.assertFalse(namespace["_posted"][0].emote_pending)

    def test_e8_a_start_past_wait_s_is_not_started(self) -> None:
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.m_Expansion_EmoteID = 0
        member.emotes.m_bEmoteIsPlaying = True
        member.emotes.gesture = _EMOTE_IDS["nod"]
        _tick(namespace, 110.5)
        self.assertEqual(namespace["_posted"][0].error.text, "emote_timed_out")

    def test_e8_the_job_tick_writes_no_field_of_either_layer(self) -> None:
        # Mutant killed: the job tick assigning a field of either layer
        # (e.g. m_bEmoteIsRequestPending = false) instead of only reading.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.emotes.m_bEmoteIsRequestPending = True
        before = (dict(vars(member)), dict(vars(member.emotes)))
        _tick(namespace, 101.0)
        self.assertEqual((dict(vars(member)), dict(vars(member.emotes))), before)
        member.emotes.m_bEmoteIsRequestPending = False
        member.emotes.m_bEmoteIsPlaying = True
        member.emotes.gesture = _EMOTE_IDS["nod"]
        _tick(namespace, 102.0)
        member.emotes.m_bEmoteIsPlaying = False
        before = (dict(vars(member)), dict(vars(member.emotes)))
        _tick(namespace, 103.0)  # publishes through PostJobSuccess
        self.assertEqual(len(namespace["_posted"]), 1)
        self.assertEqual((dict(vars(member)), dict(vars(member.emotes))), before)

    def test_e8_member_death_or_disappearance_interrupts(self) -> None:
        # Mutant killed: a dead member's job waiting out wait_s as a
        # timeout instead of emote_interrupted.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.alive = False
        _tick(namespace, 101.0)
        result = namespace["_posted"][0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error.text, "emote_interrupted")

        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        namespace["m_Jobs"].Get(1).subject = None
        _tick(namespace, 101.0)
        result = namespace["_posted"][0]
        self.assertEqual(result.error.text, "emote_interrupted")

    def test_e8_bad_target_for_unknown_dead_or_releasing_member(self) -> None:
        # Mutant killed: the target check accepting a releasing group or
        # a dead member (the close's stop covers members, not emotes).
        namespace = _emote_namespace()
        _setup(namespace, _FakeMember())
        handled, result = _dispatch(namespace, object_id=99)
        self.assertEqual((handled, result.error.text), (1, "bad_target"))

        namespace = _emote_namespace()
        dead = _FakeMember()
        dead.alive = False
        _setup(namespace, dead)
        handled, result = _dispatch(namespace)
        self.assertEqual((handled, result.error.text), (1, "bad_target"))

        namespace = _emote_namespace()
        _setup(namespace, _FakeMember(), group_state="releasing")
        handled, result = _dispatch(namespace)
        self.assertEqual((handled, result.error.text), (1, "bad_target"))

    def test_e8_end_with_only_an_expansion_request_cancels_in_dispatch(self) -> None:
        # Mutant killed: end with a pending Expansion request queuing a
        # job instead of answering cancelled after clearing it.
        namespace = _emote_namespace()
        member = _FakeMember()
        member.m_Expansion_EmoteID = _EMOTE_IDS["sita"]
        _setup(namespace, member)
        handled, result = _dispatch(namespace, emote="end")
        self.assertEqual(handled, 1)
        self.assertTrue(result.ok)
        self.assertEqual(result.emote_state.text, "cancelled")
        self.assertFalse(result.emote_pending)
        self.assertEqual(member.set_emote_calls, [(0, False, 0)])
        self.assertEqual(namespace["m_Jobs"].Count(), 0)

    def test_e8_end_playing_requests_cancel_and_finishes_at_the_stop(self) -> None:
        # Mutants killed: end answering finished in the dispatch (the
        # cancel is a request, not a stop), and end skipping the
        # Expansion_SetEmote(0) clearing.
        namespace = _emote_namespace()
        member = _FakeMember()
        member.m_Expansion_EmoteID = _EMOTE_IDS["sita"]
        member.emotes.m_bEmoteIsPlaying = True
        member.emotes.gesture = _EMOTE_IDS["sita"]
        _setup(namespace, member)
        handled, _result = _dispatch(namespace, emote="end")
        self.assertEqual(handled, 2)
        self.assertEqual(member.set_emote_calls, [(0, False, 0)])
        self.assertEqual(member.emotes.cancel_calls, 1)
        self.assertEqual(namespace["_posted"], [])
        member.emotes.m_bEmoteIsPlaying = False
        _tick(namespace, 104.0)
        result = namespace["_posted"][0]
        self.assertTrue(result.ok)
        self.assertEqual(result.emote_state.text, "finished")
        self.assertEqual(result.finished_after_s, 4.0)

    def test_e8_end_with_only_a_vanilla_request_or_nothing(self) -> None:
        # Mutants killed: end clearing a vanilla request it cannot clear,
        # and end with nothing playing answering cancelled.
        namespace = _emote_namespace()
        member = _FakeMember()
        member.emotes.m_bEmoteIsRequestPending = True
        _setup(namespace, member)
        handled, result = _dispatch(namespace, emote="end")
        self.assertEqual(handled, 1)
        self.assertEqual(result.error.text, "emote_rejected")
        self.assertTrue(result.emote_pending)
        self.assertEqual(member.set_emote_calls, [])
        self.assertEqual(member.emotes.cancel_calls, 0)

        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        handled, result = _dispatch(namespace, emote="end")
        self.assertEqual(handled, 1)
        self.assertEqual(result.error.text, "no_emote_playing")

    def test_e8_the_job_calls_nothing_that_changes_the_world(self) -> None:
        # Mutant killed: the job calling Expansion_SetEmote (or any
        # setter) to consume the request itself.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _dispatch(namespace)
        member.set_emote_calls.clear()
        member.emotes.m_bEmoteIsPlaying = True
        member.emotes.gesture = _EMOTE_IDS["nod"]
        _tick(namespace, 101.0)
        member.emotes.m_bEmoteIsPlaying = False
        _tick(namespace, 102.0)
        self.assertEqual(member.set_emote_calls, [])
        self.assertEqual(member.play_calls, 0)
        self.assertEqual(member.setter_calls, 0)
        self.assertEqual(member.emotes.cancel_calls, 0)
        source = ADAPTER.read_text(encoding="utf-8")
        for signature in (
            "protected override bool IsJobReady(MCPJob job)",
            "protected override void PostJobSuccess(MCPJob job)",
            "protected override void PostJobTimeout(MCPJob job)",
        ):
            body = method_body(source, signature)
            for forbidden in (
                "Expansion_SetEmote", "Expansion_PlayEmote",
                "ServerRequestEmoteCancel", ".Set", "eAI_Despawn",
            ):
                with self.subTest(signature=signature, forbidden=forbidden):
                    self.assertNotIn(forbidden, body)

    def test_e8_catalog_is_actor_emotes_catalog(self) -> None:
        # Mutant killed: an adapter-only emote name (suicide/vomit would
        # reach Expansion_SetEmote through the bridge).
        namespace = _emote_namespace()
        for name in loopback.ACTOR_EMOTES:
            with self.subTest(emote=name):
                self.assertGreater(namespace["AIEmoteId"](_S(name)), 0)
        for name in ("suicide", "vomit", "surrender", "debug", "nope", ""):
            with self.subTest(emote=name):
                self.assertEqual(namespace["AIEmoteId"](_S(name)), 0)

    def test_e8_autocancel_removed_before_end_and_before_new_emote(self) -> None:
        # P1-1 positive: greeting (wait_s=10) started by FSM at t=9 queues a
        # cancel at t=19 (eAIBase.c:10914); end at t=11 and sita (wait_s=30)
        # at t=12 must not let greeting's t=19 expiry cut sita. Sub-checks
        # also isolate the end removal and the step-5 removal independently.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _set_clock(namespace, 0.0)
        handled, _result = _dispatch(
            namespace, emote="greeting", wait_s=10.0, cmd_id=1
        )
        self.assertEqual(handled, 2)
        _start_static_pose_in_fsm(namespace, member, 9.0)
        _tick(namespace, 10.5)
        self.assertEqual(namespace["_posted"][-1].emote_state.text, "started")

        _set_clock(namespace, 11.0)
        handled, _result = _dispatch(
            namespace, emote="end", wait_s=10.0, cmd_id=2
        )
        self.assertEqual(handled, 2)
        _tick(namespace, 11.5)
        self.assertEqual(namespace["_posted"][-1].emote_state.text, "finished")

        _set_clock(namespace, 12.0)
        handled, _result = _dispatch(
            namespace, emote="sita", wait_s=30.0, cmd_id=3
        )
        self.assertEqual(handled, 2)
        _start_static_pose_in_fsm(namespace, member, 12.5)

        _tick(namespace, 19.0)
        self.assertTrue(member.emotes.m_bEmoteIsPlaying)
        self.assertEqual(member.emotes.gesture, _EMOTE_IDS["sita"])
        self.assertEqual(member.emotes.cancel_calls, 1)
        self.assertEqual(len(namespace["_posted"]), 2)

        _tick(namespace, 42.1)
        self.assertEqual(len(namespace["_posted"]), 3)
        self.assertEqual(namespace["_posted"][-1].emote_state.text, "started")

        # Mutant killed: Remove called only in step 5 and omitted in isEnd.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _set_clock(namespace, 0.0)
        _dispatch(namespace, emote="greeting", wait_s=10.0, cmd_id=1)
        _start_static_pose_in_fsm(namespace, member, 9.0)
        _tick(namespace, 10.5)
        _set_clock(namespace, 11.0)
        _dispatch(namespace, emote="end", wait_s=10.0, cmd_id=2)
        _tick(namespace, 11.5)
        _tick(namespace, 19.0)
        self.assertEqual(member.emotes.cancel_calls, 1)

        # Mutant killed: Remove called only in isEnd and omitted in step 5.
        namespace = _emote_namespace()
        member = _FakeMember()
        _setup(namespace, member)
        _set_clock(namespace, 0.0)
        _dispatch(namespace, emote="greeting", wait_s=10.0, cmd_id=1)
        _start_static_pose_in_fsm(namespace, member, 9.0)
        member.emotes.m_bEmoteIsPlaying = False
        member.emotes.gesture = 0
        _tick(namespace, 9.5)
        self.assertEqual(namespace["_posted"][-1].emote_state.text, "finished")
        _set_clock(namespace, 12.0)
        _dispatch(namespace, emote="sita", wait_s=30.0, cmd_id=2)
        _start_static_pose_in_fsm(namespace, member, 12.5)
        _tick(namespace, 19.0)
        self.assertTrue(member.emotes.m_bEmoteIsPlaying)
        self.assertEqual(member.emotes.gesture, _EMOTE_IDS["sita"])
        self.assertEqual(member.emotes.cancel_calls, 0)

    def test_e8_autocancel_remove_keeps_other_member_scheduled_cancel(self) -> None:
        # P1-1 negative: clearing member 11's queued cancel (2_gamelib/tools.c:65)
        # targets only member 11's EmoteManager instance; member 12's queued
        # cancel stays in CALL_CATEGORY_SYSTEM and fires at t=19.
        namespace = _emote_namespace()
        member_a = _FakeMember()
        member_b = _FakeMember()
        namespace["m_AIGroups"] = EnforceArray(
            [
                SimpleNamespace(
                    group_state=_S("active"),
                    group=_FakeGroup(False),
                    members=EnforceArray(
                        [
                            SimpleNamespace(object_id=11, member=member_a),
                            SimpleNamespace(object_id=12, member=member_b),
                        ]
                    ),
                )
            ]
        )
        _set_clock(namespace, 0.0)
        _dispatch(
            namespace, emote="greeting", object_id=11, wait_s=10.0, cmd_id=1
        )
        _dispatch(
            namespace, emote="greeting", object_id=12, wait_s=10.0, cmd_id=2
        )
        _start_static_pose_in_fsm(namespace, member_a, 9.0)
        _start_static_pose_in_fsm(namespace, member_b, 9.0)
        _tick(namespace, 10.5)

        _set_clock(namespace, 11.0)
        _dispatch(namespace, emote="end", object_id=11, wait_s=10.0, cmd_id=3)
        _tick(namespace, 11.5)

        _set_clock(namespace, 12.0)
        _dispatch(
            namespace, emote="sita", object_id=11, wait_s=30.0, cmd_id=4
        )
        _start_static_pose_in_fsm(namespace, member_a, 12.5)

        _tick(namespace, 19.0)
        self.assertFalse(member_b.emotes.m_bEmoteIsPlaying)
        self.assertEqual(member_b.emotes.cancel_calls, 1)
        self.assertTrue(member_a.emotes.m_bEmoteIsPlaying)
        self.assertEqual(member_a.emotes.gesture, _EMOTE_IDS["sita"])
        self.assertEqual(member_a.emotes.cancel_calls, 1)

    def test_e8_releasing_or_dead_member_pending_reports_true_on_refusals(self) -> None:
        # P2-2 positive: when object_id names a still-existing member of a
        # releasing (or active) group whose request is pending in either layer,
        # bad_target and bad_args read AIEmotePending on that member and carry
        # observed=emote_pending=true across _bridge_error, while emote_rejected
        # keeps today's text.
        for layer in ("expansion", "vanilla"):
            with self.subTest(group_state="releasing", layer=layer):
                namespace = _emote_namespace()
                member = _FakeMember()
                if layer == "expansion":
                    member.m_Expansion_EmoteID = _EMOTE_IDS["nod"]
                else:
                    member.emotes.m_bEmoteIsRequestPending = True
                _setup(namespace, member, group_state="releasing")
                handled, result = _dispatch(
                    namespace, emote="sita", object_id=11
                )
                self.assertEqual(handled, 1)
                self.assertFalse(result.ok)
                self.assertEqual(result.error.text, "bad_target")
                self.assertTrue(result.emote_pending)
                self.assertEqual(
                    str(_bridge_error(_wire_from_result(result), EMOTE)),
                    "bad_target; observed=emote_pending=true",
                )

                for bad_emote, bad_wait in (("nope", 10.0), ("sita", 99.0)):
                    with self.subTest(bad_emote=bad_emote, bad_wait=bad_wait):
                        handled, bad = _dispatch(
                            namespace,
                            emote=bad_emote,
                            object_id=11,
                            wait_s=bad_wait,
                        )
                        self.assertEqual(handled, 1)
                        self.assertEqual(bad.error.text, "bad_args")
                        self.assertTrue(bad.emote_pending)
                        self.assertEqual(
                            str(_bridge_error(_wire_from_result(bad), EMOTE)),
                            "bad_args; observed=emote_pending=true",
                        )

        # Dead but still-existing member in an active group also reports pending.
        namespace = _emote_namespace()
        dead = _FakeMember()
        dead.alive = False
        dead.emotes.m_bEmoteIsRequestPending = True
        _setup(namespace, dead, group_state="active")
        handled, dead_result = _dispatch(namespace, emote="sita", object_id=11)
        self.assertEqual(handled, 1)
        self.assertEqual(dead_result.error.text, "bad_target")
        self.assertTrue(dead_result.emote_pending)
        self.assertEqual(
            str(_bridge_error(_wire_from_result(dead_result), EMOTE)),
            "bad_target; observed=emote_pending=true",
        )

        # emote_rejected preserves today's text with emote_state=rejected.
        namespace = _emote_namespace()
        active_pending = _FakeMember()
        active_pending.m_Expansion_EmoteID = _EMOTE_IDS["nod"]
        _setup(namespace, active_pending, group_state="active")
        handled, rejected = _dispatch(namespace, emote="sita", object_id=11)
        self.assertEqual(handled, 1)
        self.assertEqual(rejected.error.text, "emote_rejected")
        self.assertEqual(
            str(_bridge_error(_wire_from_result(rejected), EMOTE)),
            "emote_rejected; observed=emote_state=rejected emote_pending=true",
        )


if __name__ == "__main__":
    unittest.main()
