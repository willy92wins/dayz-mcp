"""actor_emote (inbox 36fa): catalog, wait, admission and the client bridge contract."""

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

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.bridge_errors import _bridge_error
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import _method_body
from tests.enforce_subset_helpers import EnforceString, method_body, translate
from tests.fence_helpers import (
    INST_CLIENT,
    PID_CLIENT,
    bind_both_peers,
    bound_queue,
    poll_census_query,
)


COMMAND = "actor_emote"
CLIENT_BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
EMOTE_HELPER = addon_root() / "scripts" / "4_World" / "MCP_Emote.c"
OLDER_CLIENT_CAPS = "camera_get,key_press,player_respawn,ui_dialog"
WIRE = {"emote": "greeting", "wait_s": 10.0}


def _tool_error_text(exc: BaseException) -> str:
    text = str(exc)
    nested = getattr(exc, "exceptions", None)
    if nested:
        text = text + " " + str(nested)
    return text


def _strip_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


def _in_order(test: unittest.TestCase, body: str, *tokens: str) -> None:
    positions = []
    for token in tokens:
        test.assertIn(token, body)
        positions.append(body.index(token))
    test.assertEqual(positions, sorted(positions), tokens)


def _client_state(caps: str | None) -> loopback.ServerState:
    state = loopback.ServerState("test-key")
    bind_both_peers(state)
    if caps is not None:
        state.record_poll(
            "client", "12~1.29", instance=INST_CLIENT, source_pid=PID_CLIENT, caps=caps
        )
    return state


class ActorEmoteToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tool = self.app._tool_manager.get_tool(COMMAND)

    async def _call(self, arguments: dict, reply: dict | None = None) -> tuple[tuple, dict]:
        answer = dict(reply or {"ok": 1, "emote_state": "finished"})
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=answer)) as call:
            result = await self.app.call_tool(COMMAND, arguments)
        call.assert_awaited_once()
        return call.await_args.args, result

    async def _refused(self, arguments: dict) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(COMMAND, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    def test_closed_schema_with_one_required_name(self) -> None:
        self.assertIsNotNone(self.tool)
        parameters = self.tool.parameters
        self.assertEqual(set(parameters["properties"]), {"emote", "wait_s", "timeout_s"})
        self.assertEqual(parameters.get("required"), ["emote"])
        self.assertIs(parameters.get("additionalProperties"), False)
        self.assertIn(COMMAND, server._CLOSED_SCHEMA_TOOLS)

    def test_lease_verb_and_not_read_only(self) -> None:
        self.assertTrue(command_requires_lease(COMMAND))
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(self.tool.description.startswith(server.LEASE_TOOL_LINE))
        for code in (
            "no_player",
            "no_emote_manager",
            "emote_rejected",
            "no_emote_playing",
            "emote_timed_out",
            "bridge_capability_missing",
        ):
            self.assertIn(code, self.tool.description)
        for part in (
            "emote_rejected when an emote is already playing, still requested or the player "
            "surrendered (IsEmotePlaying(); end a playing one first), when another actor_emote "
            "is still watching its own, end included",
            "watches EmoteManager.m_bEmoteIsPlaying, which a request still pending does not set",
            "A looping emote (greeting, sita, salute...) answers started and plays until "
            "emote='end'; a one-time one (nod, clap, shrug...) answers finished.",
            "or for end the emote still plays (the end may not have been sent:",
            "no_emote_playing for end while no emote plays",
            "rps_s, nod, shake, shrug, or end,",
        ):
            self.assertIn(part, self.tool.description)
        for name in ("surrender", "vomit"):
            self.assertNotIn(f", {name},", self.tool.description)

    async def test_wire_goes_to_the_client_with_wait_s(self) -> None:
        args, _result = await self._call({"emote": "greeting"})
        self.assertEqual(args, (COMMAND, {"emote": "greeting", "wait_s": 10.0}, "client", 15.0))
        args, _result = await self._call({"emote": "end", "wait_s": 2})
        self.assertEqual(args[1], {"emote": "end", "wait_s": 2.0})
        self.assertIsInstance(args[1]["wait_s"], float)

    async def test_transport_wait_is_extended_by_wait_s(self) -> None:
        for arguments, expected in (
            ({"emote": "sita", "wait_s": 30.0}, 30.0 + loopback.ACTOR_EMOTE_WAIT_SLACK_S),
            ({"emote": "sita", "wait_s": 12.0, "timeout_s": 15.0}, 17.0),
            ({"emote": "sita", "wait_s": 5.0, "timeout_s": 60.0}, 60.0),
            ({"emote": "sita", "wait_s": 0.5}, 15.0),
        ):
            with self.subTest(arguments=arguments):
                args, _result = await self._call(arguments)
                self.assertEqual(args[3], expected)
        self.assertEqual(loopback.ACTOR_EMOTE_WAIT_SLACK_S, 5.0)

    async def test_unknown_emote_is_bad_args_listing_every_allowed_name(self) -> None:
        for emote in ("nope", "Greeting", "ID_EMOTE_GREETING", "suicide", "debug", "surrender", "vomit", ""):
            with self.subTest(emote=emote):
                text = await self._refused({"emote": emote})
                self.assertIn(f"bad_args: emote {emote!r} must be one of", text)
                listed = text.split("must be one of ", 1)[1]
                for name in (*loopback.ACTOR_EMOTES, loopback.ACTOR_EMOTE_END):
                    self.assertRegex(listed, rf"(^|, ){re.escape(name)}(,|$|\s)")

    async def test_wait_s_out_of_range_or_not_finite_is_bad_args(self) -> None:
        for wait_s in (0.0, 0.49, 30.01, 31, -1.0, math.nan, math.inf):
            with self.subTest(wait_s=wait_s):
                text = await self._refused({"emote": "greeting", "wait_s": wait_s})
                self.assertIn("bad_args: wait_s", text)
                self.assertIn("[0.5, 30]", text)

    async def test_extra_keys_and_wrong_types_never_reach_the_bridge(self) -> None:
        for arguments, part in (
            ({"emote": "greeting", "object_id": 4}, "unexpected arguments: object_id"),
            ({"emote": "greeting", "timeout": 4}, "unexpected arguments: timeout"),
            ({"emote": "greeting", "wait_s": True}, "wait_s"),
            ({"emote": 1}, "emote"),
            ({}, "emote"),
        ):
            with self.subTest(arguments=arguments):
                self.assertIn(part, await self._refused(arguments))

    async def test_times_are_kept_only_where_the_state_reached_them(self) -> None:
        wire = {"ok": 1, "started_after_s": 0.25, "finished_after_s": 2.5}
        for emote, state, kept, dropped in (
            ("nod", "finished", ("started_after_s", "finished_after_s"), ()),
            ("sita", "started", ("started_after_s",), ("finished_after_s",)),
            ("end", "finished", ("finished_after_s",), ("started_after_s",)),
        ):
            with self.subTest(emote=emote, state=state):
                _args, result = await self._call(
                    {"emote": emote}, {**wire, "emote_state": state}
                )
                text = str(result)
                self.assertIn(f"'emote_state': '{state}'", text)
                for field in kept:
                    self.assertIn(f"'{field}'", text)
                for field in dropped:
                    self.assertNotIn(f"'{field}'", text)

    def test_error_codes_carry_the_observed_state_only(self) -> None:
        for code, state in (
            ("emote_rejected", "rejected"),
            ("no_emote_playing", "rejected"),
            ("emote_timed_out", "timed_out"),
        ):
            with self.subTest(code=code):
                error = _bridge_error({"ok": 0, "error": code, "emote_state": state}, COMMAND)
                self.assertEqual(str(error), f"{code}; observed=emote_state={state}")
        # An unknown state, another code or another verb keeps the bare code.
        self.assertEqual(
            str(_bridge_error({"ok": 0, "error": "emote_rejected", "emote_state": "x y"}, COMMAND)),
            "emote_rejected",
        )
        self.assertEqual(
            str(_bridge_error({"ok": 0, "error": "no_player", "emote_state": "rejected"}, COMMAND)),
            "no_player",
        )
        self.assertEqual(
            str(_bridge_error({"ok": 0, "error": "emote_rejected", "emote_state": "rejected"}, "ui_tree")),
            "emote_rejected",
        )

    def test_the_watch_scalars_are_owned_by_actor_emote(self) -> None:
        for field in ("emote_state", "started_after_s", "finished_after_s"):
            with self.subTest(field=field):
                self.assertEqual(result_prune.OWNED_SCALAR_FIELDS[field], frozenset({COMMAND}))
                kept = result_prune.prune_unfilled_fields(COMMAND, {"ok": 1, field: 0.0})
                self.assertIn(field, kept)
                dropped = result_prune.prune_unfilled_fields("player_move", {"ok": 1, field: 0.0})
                self.assertNotIn(field, dropped)


class ActorEmoteAdmissionTest(unittest.TestCase):
    def test_lists_name_the_client_verb(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertIn(COMMAND, loopback._CLIENT_ANNOUNCED_COMMANDS)
        self.assertNotIn(COMMAND, loopback._CAPABILITY_ADMISSION_COMMANDS)
        self.assertEqual(server._BRIDGE_COMMAND_TOOLS["client"][COMMAND], COMMAND)
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)

    def test_older_client_census_queues_nothing(self) -> None:
        for caps in (None, OLDER_CLIENT_CAPS):
            with self.subTest(caps=caps):
                state = _client_state(caps)
                before = state._next_id
                status, body = state.enqueue_command(COMMAND, dict(WIRE), peer="client")
                self.assertEqual((status, body.get("error")), (409, "bridge_capability_missing"))
                self.assertEqual(state._next_id, before)
                self.assertEqual(bound_queue(state, "client"), [])

    def test_current_client_census_admits_and_delivers(self) -> None:
        caps = poll_census_query("client")["caps"]
        self.assertIn(COMMAND, caps.split(","))
        state = _client_state(caps)
        status, body = state.enqueue_command(COMMAND, dict(WIRE), peer="client")
        self.assertEqual(status, 200, body)
        status, poll = state.record_poll(
            "client", "12~1.29", instance=INST_CLIENT, source_pid=PID_CLIENT, caps=caps
        )
        self.assertEqual(status, 200)
        self.assertEqual(poll["commands"], [{"id": body["id"], "cmd": COMMAND, "args": WIRE}])

    def test_a_destination_that_lost_the_verb_is_not_handed_it(self) -> None:
        state = _client_state(poll_census_query("client")["caps"])
        status, body = state.enqueue_command(COMMAND, dict(WIRE), peer="client")
        self.assertEqual(status, 200, body)
        status, poll = state.record_poll(
            "client", "12~1.29", instance=INST_CLIENT, source_pid=PID_CLIENT, caps=OLDER_CLIENT_CAPS
        )
        self.assertEqual(status, 200)
        self.assertEqual(poll["commands"], [])
        result = state.take_result(body["id"], remove=False)
        self.assertEqual(result.get("error"), "bridge_capability_missing")

    def test_action_hold_keeps_its_own_refusal(self) -> None:
        state = _client_state(OLDER_CLIENT_CAPS)
        status, body = state.enqueue_command(
            "action_hold",
            {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": 1.0},
            peer="client",
        )
        self.assertEqual((status, body.get("error")), (409, "hold_not_supported"))


class ActorEmoteEnforceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        cls.code = _strip_comments(cls.source)

    def test_bridge_catalog_is_the_python_catalog(self) -> None:
        body = _method_body(self.code, "protected int ActorEmoteId(string emote)")
        pairs = re.findall(
            r'if \(emote == "([a-z_]+)"\)\s*\{\s*return EmoteConstants\.(ID_EMOTE_[A-Z_]+);\s*\}',
            body,
        )
        self.assertEqual(tuple(name for name, _constant in pairs), loopback.ACTOR_EMOTES)
        for name, constant in pairs:
            self.assertEqual(constant, "ID_EMOTE_" + name.upper())
        self.assertEqual(body.count("return EmoteConstants."), len(loopback.ACTOR_EMOTES))
        self.assertTrue(body.rstrip().endswith("return 0;"))
        self.assertEqual(len(loopback.ACTOR_EMOTES), 37)
        self.assertEqual(len(set(loopback.ACTOR_EMOTES)), 37)
        # surrender and vomit create no gesture callback (emoteclasses.c
        # EmoteStartOverride, emotemanager.c:667-671): out of the catalog.
        for excluded in ("SUICIDE", "DEBUG", "SURRENDER", "VOMIT"):
            self.assertNotIn(excluded, body)
            self.assertNotIn(excluded.lower(), loopback.ACTOR_EMOTES)
        self.assertNotIn(loopback.ACTOR_EMOTE_END, loopback.ACTOR_EMOTES)

    def test_wait_bounds_match_the_ingress(self) -> None:
        for name, value in (
            ("ACTOR_EMOTE_WAIT_MIN_S", loopback.ACTOR_EMOTE_WAIT_MIN_S),
            ("ACTOR_EMOTE_WAIT_MAX_S", loopback.ACTOR_EMOTE_WAIT_MAX_S),
        ):
            match = re.search(rf"protected const float {name} = ([0-9.]+);", self.code)
            self.assertIsNotNone(match, name)
            self.assertEqual(float(match.group(1)), value)
        self.assertEqual((loopback.ACTOR_EMOTE_WAIT_MIN_S, loopback.ACTOR_EMOTE_WAIT_MAX_S), (0.5, 30.0))

    def test_dispatch_refuses_before_it_asks_and_then_queues_the_watch(self) -> None:
        dispatch = _method_body(self.code, "protected void Dispatch(MCPCommand command)")
        self.assertIn(
            'else if (command.cmd == "actor_emote")\n\t\t{\n\t\t\tpostNow = DispatchActorEmote(command, result);',
            dispatch,
        )
        body = _method_body(self.code, "protected bool DispatchActorEmote(MCPCommand command, MCPResult result)")
        _in_order(
            self,
            body,
            'isEnd = command.args.emote == "end";',
            "emoteId = ActorEmoteId(command.args.emote);",
            "if (!isEnd && emoteId <= 0)",
            "if (!IsStrictFinite(waitS) || waitS < ACTOR_EMOTE_WAIT_MIN_S || waitS > ACTOR_EMOTE_WAIT_MAX_S)",
            'if (m_JobRunner.CountOfKind("actor_emote") > 0)',
            "player = PlayerBase.Cast(GetGame().GetPlayer());",
            'result.error = "no_player";',
            "emotes = player.GetEmoteManager();",
            'result.error = "no_emote_manager";',
            "if (!emotes.m_bEmoteIsPlaying)",
            'result.error = "no_emote_playing";',
            "if (!emotes.MCP_RequestEmoteEnd())",
            "if (emotes.IsEmotePlaying() || !emotes.CanPlayEmote(emoteId) || !emotes.CanPlayEmoteClientCheck(emoteId))",
            "emotes.CreateEmoteCBFromMenu(emoteId);",
            'job.kind = "actor_emote";',
            "job.emote_request_s = GetGame().GetTickTime();",
            "job.deadline_s = m_JobRunner.GetElapsedS() + waitS;",
            "m_JobRunner.AddJob(job);",
        )
        self.assertEqual(body.count('result.error = "emote_rejected";'), 3)
        self.assertEqual(body.count('result.emote_state = "rejected";'), 4)
        self.assertEqual(body.count("CreateEmoteCBFromMenu("), 1)
        self.assertNotIn("PlayEmote(", body.replace("CanPlayEmote(", ""))

    def test_a_second_actor_emote_while_one_is_watched_is_rejected(self) -> None:
        # The first emote may still be on its way (IsEmotePlaying() reads false
        # until Update sends the request), so only the job count tells; end is
        # refused too, and nothing is sent.
        body = _method_body(self.code, "protected bool DispatchActorEmote(MCPCommand command, MCPResult result)")
        guard = 'if (m_JobRunner.CountOfKind("actor_emote") > 0)'
        self.assertEqual(body.count(guard), 1)
        block = body[body.index(guard) :]
        block = block[: block.index("}") + 1]
        for line in (
            "result.ok = false;",
            'result.error = "emote_rejected";',
            'result.emote_state = "rejected";',
            "return true;",
        ):
            self.assertIn(line, block)
        for later in (
            "if (isEnd)",
            "emotes.MCP_RequestEmoteEnd()",
            "emotes.CreateEmoteCBFromMenu(emoteId);",
            "m_JobRunner.AddJob(job);",
        ):
            self.assertLess(body.index(guard), body.index(later), later)

    def test_the_job_watches_the_playing_flag_and_answers_once(self) -> None:
        process = _method_body(self.code, "override bool MCP_ProcessJob(MCPJob job)")
        self.assertIn(
            'else if (job.kind == "actor_emote")\n\t\t{\n\t\t\treturn ProcessActorEmoteJob(job);',
            process,
        )
        watch = _method_body(self.code, "protected bool ProcessActorEmoteJob(MCPJob job)")
        self.assertIn("playing = emotes.m_bEmoteIsPlaying;", watch)
        self.assertNotIn("IsEmotePlaying", watch)
        self.assertIn("nowS = GetGame().GetTickTime();", watch)
        self.assertIn("job.emote_started_s = nowS;", watch)
        self.assertEqual(watch.count("job.emote_finished_s = nowS;"), 2)
        success = _method_body(self.code, "override void MCP_PostJobSuccess(MCPJob job)")
        self.assertIn("PostActorEmoteJob(job, true);", success)
        timeout = _method_body(self.code, "override void MCP_PostJobTimeout(MCPJob job)")
        self.assertIn("PostActorEmoteJob(job, false);", timeout)
        post = _method_body(self.code, "protected void PostActorEmoteJob(MCPJob job, bool finished)")
        _in_order(
            self,
            post,
            "result.started_after_s = job.emote_started_s - job.emote_request_s;",
            'result.emote_state = "finished";',
            "result.finished_after_s = job.emote_finished_s - job.emote_request_s;",
            'result.emote_state = "started";',
            'result.error = "emote_timed_out";',
            'result.emote_state = "timed_out";',
            "PostResult(result);",
        )
        states = set(re.findall(r'emote_state = "([a-z_]+)";', self.code))
        self.assertEqual(states, {"finished", "started", "timed_out", "rejected"})

    def test_the_watch_does_not_make_camera_set_busy(self) -> None:
        exclusive = _method_body(self.code, "protected bool HasExclusiveJob()")
        self.assertIn('emoteJobs = m_JobRunner.CountOfKind("actor_emote");', exclusive)
        self.assertIn("blocking = blocking - emoteJobs;", exclusive)

    def test_end_is_the_class_own_request(self) -> None:
        helper = _strip_comments(EMOTE_HELPER.read_text(encoding="utf-8"))
        self.assertIn("modded class EmoteManager", helper)
        body = _method_body(helper, "bool MCP_RequestEmoteEnd()")
        _in_order(
            self,
            body,
            "if (!CanPlayEmote(CALLBACK_CMD_END) || !CanPlayEmoteClientCheck(CALLBACK_CMD_END))",
            "return false;",
            "SendEmoteRequestSync(CALLBACK_CMD_END);",
            "return true;",
        )
        self.assertNotIn("override", helper)
        self.assertNotIn("MCPResult", helper)


DISPATCH = "protected bool DispatchActorEmote(MCPCommand command, MCPResult result)"
CATALOG = "protected int ActorEmoteId(string emote)"
WATCH = "protected bool ProcessActorEmoteJob(MCPJob job)"
ANSWER = "protected void PostActorEmoteJob(MCPJob job, bool finished)"
# The translator has no `new`: these two become factory calls, nothing else changes.
_NEW = (("new MCPJob()", "NewJob()"), ("new MCPResult()", "NewResult()"))
_TICK_S = 0.1


class _Emotes:
    """EmoteManager as the bridge reads it. IsEmotePlaying() is vanilla's
    m_bEmoteIsPlaying || IsSurrendered() || m_bEmoteIsRequestPending
    (emotemanager.c:1328-1331); `timeline` gives (playing, pending) for the
    seconds since the request."""

    def __init__(self, *, playing: bool = False, pending: bool = False, surrendered: bool = False) -> None:
        self.m_bEmoteIsPlaying = playing
        self.m_bEmoteIsRequestPending = pending
        self.surrendered = surrendered
        self.sent: list[object] = []

    def IsEmotePlaying(self) -> bool:
        return self.m_bEmoteIsPlaying or self.surrendered or self.m_bEmoteIsRequestPending

    def CanPlayEmote(self, _emote_id: int) -> bool:
        return True

    def CanPlayEmoteClientCheck(self, _emote_id: int) -> bool:
        return True

    def CreateEmoteCBFromMenu(self, emote_id: int) -> None:
        self.sent.append(emote_id)

    def MCP_RequestEmoteEnd(self) -> bool:
        self.sent.append("end")
        return True


class _ClientModel:
    """DispatchActorEmote, ActorEmoteId, ProcessActorEmoteJob and
    PostActorEmoteJob translated from MCPClientBridge.c and run as the job
    runner runs them (MCPJobRunner.c ProcessJobs): each tick the job's own
    check first (true: MCP_PostJobSuccess, PostActorEmoteJob(job, true)), then
    its deadline (MCP_PostJobTimeout, PostActorEmoteJob(job, false))."""

    def __init__(self, source: str, emotes: _Emotes, *, watching: int = 0) -> None:
        self.emotes = emotes
        self.watching = watching
        self.now = 100.0
        self.elapsed = 40.0
        self.jobs: list[SimpleNamespace] = []
        self.posted: list[SimpleNamespace] = []
        player = SimpleNamespace(GetEmoteManager=lambda: emotes)
        game = SimpleNamespace(GetPlayer=lambda: player, GetTickTime=lambda: self.now)
        names = (*loopback.ACTOR_EMOTES, "surrender", "vomit", "suicide")
        self.namespace: dict[str, object] = {
            "GetGame": lambda: game,
            "PlayerBase": SimpleNamespace(Cast=lambda item: item),
            "EmoteConstants": SimpleNamespace(
                **{f"ID_EMOTE_{name.upper()}": index + 1 for index, name in enumerate(names)}
            ),
            "m_JobRunner": SimpleNamespace(
                CountOfKind=self._count_of_kind,
                GetElapsedS=lambda: self.elapsed,
                AddJob=self.jobs.append,
            ),
            "IsStrictFinite": math.isfinite,
            "ACTOR_EMOTE_WAIT_MIN_S": loopback.ACTOR_EMOTE_WAIT_MIN_S,
            "ACTOR_EMOTE_WAIT_MAX_S": loopback.ACTOR_EMOTE_WAIT_MAX_S,
            "NewJob": self._new_job,
            "NewResult": self._new_result,
            "PostResult": self.posted.append,
        }
        for signature in (CATALOG, DISPATCH, WATCH, ANSWER):
            body = method_body(source, signature)
            for old, new in _NEW:
                body = body.replace(old, new)
            translate(
                signature + "\n{" + body + "}\n",
                signature,
                self.namespace,
                class_types=("PlayerBase", "EmoteManager", "MCPJob", "MCPResult"),
            )

    def _count_of_kind(self, kind: EnforceString) -> int:
        return self.watching + len(self.jobs) if kind == EnforceString.of("actor_emote") else 0

    @staticmethod
    def _new_job() -> SimpleNamespace:
        return SimpleNamespace(
            id=0, kind=EnforceString.of(""), error=EnforceString.of(""), emote_end=False,
            emote_started=False, emote_request_s=0.0, emote_started_s=0.0, emote_finished_s=0.0,
            deadline_s=0.0, tick_poll_sent=0, tick_poll_callback=0, tick_dispatch=0,
        )

    @staticmethod
    def _new_result() -> SimpleNamespace:
        return SimpleNamespace(
            id=0, ok=False, error=EnforceString.of(""), emote_state=EnforceString.of(""),
            started_after_s=0.0, finished_after_s=0.0, tick_poll_sent=0, tick_poll_callback=0, tick_dispatch=0,
        )

    def dispatch(self, emote: str, wait_s: float = 2.0) -> SimpleNamespace | None:
        """The immediate answer, or None when the watch was queued."""
        command = SimpleNamespace(id=7, args=SimpleNamespace(emote=EnforceString.of(emote), wait_s=wait_s))
        result = self._new_result()
        if self.namespace["DispatchActorEmote"](command, result):
            return result
        return None

    def run(self, timeline) -> SimpleNamespace:
        """Ticks until the job answers; timeline(seconds) -> (playing, pending)."""
        start = self.now
        for _tick in range(1000):
            self.now += _TICK_S
            self.elapsed += _TICK_S
            self.emotes.m_bEmoteIsPlaying, self.emotes.m_bEmoteIsRequestPending = timeline(self.now - start)
            job = self.jobs[0]
            if self.namespace["ProcessActorEmoteJob"](job):
                if job.error:
                    raise AssertionError("the watch set job.error: the runner would post a failure")
                self.namespace["PostActorEmoteJob"](job, True)
                self.jobs.remove(job)
            elif self.elapsed > job.deadline_s:
                self.namespace["PostActorEmoteJob"](job, False)
                self.jobs.remove(job)
            if self.posted:
                return self.posted[-1]
        raise AssertionError("the job never answered")


def _state(result: SimpleNamespace) -> tuple[bool, str, str]:
    return bool(result.ok), result.error.text, result.emote_state.text


class ActorEmoteWatchModelTest(unittest.TestCase):
    """The real dispatch, watch and answer, run on a fake EmoteManager. A
    pending request is not a started emote: the watch reads
    m_bEmoteIsPlaying, and IsEmotePlaying() (which also counts the pending
    request and the surrender state) only refuses a new emote."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CLIENT_BRIDGE.read_text(encoding="utf-8")

    def _model(self, emotes: _Emotes, **kwargs: int) -> _ClientModel:
        return _ClientModel(self.source, emotes, **kwargs)

    def test_a_request_that_stays_pending_times_out_and_never_reads_as_started(self) -> None:
        model = self._model(_Emotes())
        self.assertIsNone(model.dispatch("greeting", wait_s=2.0))
        self.assertEqual(len(model.emotes.sent), 1)
        result = model.run(lambda _s: (False, True))
        self.assertEqual(_state(result), (False, "emote_timed_out", "timed_out"))
        self.assertEqual(result.started_after_s, 0.0)
        self.assertEqual(result.finished_after_s, 0.0)

    def test_a_request_the_server_refuses_times_out_and_never_finishes(self) -> None:
        # The server answers a refused request with an INSTACANCEL
        # (emotemanager.c:537-552) whose hard cancel clears the pending flag
        # with no callback (:304-312): pending, then nothing, m_bEmoteIsPlaying
        # never true. Read through IsEmotePlaying() that was started, then
        # finished, for an emote that never played (the ticket's NEG).
        model = self._model(_Emotes())
        self.assertIsNone(model.dispatch("nod", wait_s=2.0))
        result = model.run(lambda s: (False, s <= 0.35))
        self.assertEqual(_state(result), (False, "emote_timed_out", "timed_out"))
        self.assertEqual((result.started_after_s, result.finished_after_s), (0.0, 0.0))

    def test_a_one_time_emote_finishes_and_a_looping_one_is_started_at_wait_s(self) -> None:
        # Pending for 0.35 s, then playing: the start is the first playing tick.
        model = self._model(_Emotes())
        self.assertIsNone(model.dispatch("nod", wait_s=2.0))
        result = model.run(lambda s: (0.35 < s < 1.25, s <= 0.35))
        self.assertEqual(_state(result), (True, "", "finished"))
        self.assertAlmostEqual(result.started_after_s, 0.4)
        self.assertAlmostEqual(result.finished_after_s, 1.3)
        model = self._model(_Emotes())
        self.assertIsNone(model.dispatch("greeting", wait_s=2.0))
        result = model.run(lambda s: (s > 0.35, s <= 0.35))
        self.assertEqual(_state(result), (True, "", "started"))
        self.assertAlmostEqual(result.started_after_s, 0.4)
        self.assertEqual(result.finished_after_s, 0.0)

    def test_end_finishes_when_the_playing_flag_drops_and_times_out_while_it_stays(self) -> None:
        model = self._model(_Emotes(playing=True))
        self.assertIsNone(model.dispatch("end", wait_s=2.0))
        self.assertEqual(model.emotes.sent, ["end"])
        result = model.run(lambda s: (s < 0.55, False))
        self.assertEqual(_state(result), (True, "", "finished"))
        self.assertAlmostEqual(result.finished_after_s, 0.6)
        self.assertEqual(result.started_after_s, 0.0)
        # SendEmoteRequestSync's own checks (CanStoreInputUserData, canProceed)
        # can drop the end: the emote plays on and the job times out.
        model = self._model(_Emotes(playing=True))
        self.assertIsNone(model.dispatch("end", wait_s=2.0))
        result = model.run(lambda _s: (True, False))
        self.assertEqual(_state(result), (False, "emote_timed_out", "timed_out"))

    def test_end_needs_the_playing_flag_not_a_pending_request(self) -> None:
        for emotes in (_Emotes(pending=True), _Emotes(surrendered=True), _Emotes()):
            with self.subTest(pending=emotes.m_bEmoteIsRequestPending, surrendered=emotes.surrendered):
                model = self._model(emotes)
                result = model.dispatch("end")
                self.assertIsNotNone(result)
                self.assertEqual(_state(result), (False, "no_emote_playing", "rejected"))
                self.assertEqual((emotes.sent, model.jobs), ([], []))

    def test_a_new_emote_is_refused_while_one_plays_is_pending_or_the_player_surrenders(self) -> None:
        for emotes in (_Emotes(playing=True), _Emotes(pending=True), _Emotes(surrendered=True)):
            with self.subTest(
                playing=emotes.m_bEmoteIsPlaying,
                pending=emotes.m_bEmoteIsRequestPending,
                surrendered=emotes.surrendered,
            ):
                model = self._model(emotes)
                result = model.dispatch("nod")
                self.assertIsNotNone(result)
                self.assertEqual(_state(result), (False, "emote_rejected", "rejected"))
                self.assertEqual((emotes.sent, model.jobs), ([], []))

    def test_a_watch_in_progress_refuses_every_emote_end_included(self) -> None:
        for emote, emotes in (("nod", _Emotes()), ("end", _Emotes(playing=True))):
            with self.subTest(emote=emote):
                model = self._model(emotes, watching=1)
                result = model.dispatch(emote)
                self.assertIsNotNone(result)
                self.assertEqual(_state(result), (False, "emote_rejected", "rejected"))
                self.assertEqual((emotes.sent, model.jobs), ([], []))

    def test_surrender_vomit_and_suicide_are_bad_args_in_the_bridge_too(self) -> None:
        for emote in ("surrender", "vomit", "suicide", "debug"):
            with self.subTest(emote=emote):
                model = self._model(_Emotes())
                result = model.dispatch(emote)
                self.assertIsNotNone(result)
                self.assertEqual(result.error.text, "bad_args")
                self.assertEqual(model.emotes.sent, [])
        for emote in loopback.ACTOR_EMOTES:
            with self.subTest(emote=emote):
                model = self._model(_Emotes())
                self.assertIsNone(model.dispatch(emote))
                self.assertEqual(len(model.emotes.sent), 1)


if __name__ == "__main__":
    unittest.main()
