"""actor_emote (inbox 36fa): catalog, wait, admission and the client bridge contract."""

from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.bridge_errors import _bridge_error
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import _method_body
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
        self.assertIn(
            "emote_rejected when an emote is already playing (end it first), "
            "when another actor_emote is still watching its own, end included",
            self.tool.description,
        )

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
        for emote in ("nope", "Greeting", "ID_EMOTE_GREETING", "suicide", "debug", ""):
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
            ("greeting", "finished", ("started_after_s", "finished_after_s"), ()),
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
        self.assertEqual(len(loopback.ACTOR_EMOTES), 39)
        self.assertEqual(len(set(loopback.ACTOR_EMOTES)), 39)
        for excluded in ("SUICIDE", "DEBUG"):
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
            "if (!emotes.IsEmotePlaying())",
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
        # The first emote may still be on its way (IsEmotePlaying() false), so
        # only the job count tells; end is refused too, and nothing is sent.
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

    def test_the_job_watches_is_emote_playing_and_answers_once(self) -> None:
        process = _method_body(self.code, "override bool MCP_ProcessJob(MCPJob job)")
        self.assertIn(
            'else if (job.kind == "actor_emote")\n\t\t{\n\t\t\treturn ProcessActorEmoteJob(job);',
            process,
        )
        watch = _method_body(self.code, "protected bool ProcessActorEmoteJob(MCPJob job)")
        self.assertIn("playing = emotes.IsEmotePlaying();", watch)
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


if __name__ == "__main__":
    unittest.main()
