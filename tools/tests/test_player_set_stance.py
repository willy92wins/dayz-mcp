"""player_set_stance: test-mode stance change without kill, respawn or health writes."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from tests._addon_paths import addon_root
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue


REPO = _TOOLS_DIR.parent
CHANGELOG = REPO / "CHANGELOG.md"
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
CARE = addon_root() / "scripts" / "4_World" / "MCP_PlayerCare.c"
STANCE = addon_root() / "scripts" / "4_World" / "MCP_PlayerStance.c"
UID = "76561198000000001"
CMD = "player_set_stance"
VALID = {"uid": UID, "stance": "erect", "recover": False}


def _tool_error_text(exc: BaseException) -> str:
    text = str(exc)
    for attr in ("exceptions", "error"):
        nested = getattr(exc, attr, None)
        if nested:
            text = text + " " + str(nested)
    return text


def _identity() -> dict[str, object]:
    return {
        "platform": "codex",
        "pid": 101,
        "ppid": 10,
        "started_at_utc": "2026-07-14T20:00:00Z",
        "session_id": "stance-owner",
        "task_label": "player set stance",
    }


def _bound_state(**kwargs: object) -> loopback.ServerState:
    state = loopback.ServerState(key="k", **kwargs)  # type: ignore[arg-type]
    state.daemon_generation = "gen-stance"
    state.install_bound_peer(instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="stance-run")
    return state


def _announce(state: loopback.ServerState, commands: list[str], ach: str | None, generation: str | None = None) -> None:
    state._peer_caps["server"] = {
        "generation": state.daemon_generation if generation is None else generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _full_commands() -> list[str]:
    return sorted(server._BRIDGE_COMMAND_TOOLS["server"])


def _method_body(source: str, signature: str) -> str:
    match = re.search(
        re.escape(signature) + r"\s*\{(?P<body>.*?)\n\t\}",
        source,
        re.S,
    )
    if match is None:
        raise AssertionError(f"method not found: {signature}")
    return match.group("body")


class PlayerSetStanceSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    async def _forwarded(self, arguments: dict) -> tuple:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            await self.app.call_tool(CMD, arguments)
        call.assert_awaited_once()
        return call.await_args.args

    async def _refused(self, arguments: dict) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(CMD, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    def test_uid_stance_and_recover_are_the_closed_schema(self) -> None:
        tool = self.tools[CMD]
        self.assertEqual(set(tool.parameters["properties"]), {"uid", "stance", "recover", "timeout_s"})
        self.assertEqual(tool.parameters.get("required"), ["uid", "stance"])
        self.assertIs(tool.parameters.get("additionalProperties"), False)
        self.assertIn(CMD, server._CLOSED_SCHEMA_TOOLS)
        self.assertIn(CMD, loopback.SERVER_COMMANDS)
        self.assertNotIn(CMD, loopback.LIVE_ADMIN_COMMANDS)

    async def test_wire_always_sends_uid_stance_and_recover(self) -> None:
        self.assertEqual(
            await self._forwarded({"uid": UID, "stance": "erect"}),
            (CMD, {"uid": UID, "stance": "erect", "recover": False}, "server", 15.0),
        )
        self.assertEqual(
            await self._forwarded({"uid": UID, "stance": "crouch", "recover": True, "timeout_s": 4}),
            (CMD, {"uid": UID, "stance": "crouch", "recover": True}, "server", 4.0),
        )
        self.assertEqual(
            await self._forwarded({"uid": UID, "stance": "prone", "recover": False}),
            (CMD, {"uid": UID, "stance": "prone", "recover": False}, "server", 15.0),
        )

    async def test_empty_uid_unknown_stance_and_unknown_keys_are_rejected(self) -> None:
        for arguments, part in (
            ({}, "uid"),
            ({"uid": "", "stance": "erect"}, "non-empty"),
            ({"uid": UID}, "stance"),
            ({"uid": UID, "stance": "flying"}, "'erect', 'crouch' or 'prone'"),
            ({"uid": UID, "stance": "erect", "id": UID}, "unexpected arguments: id"),
            ({"player": UID, "stance": "erect"}, "unexpected arguments: player"),
        ):
            with self.subTest(arguments=arguments):
                self.assertIn(part, await self._refused(arguments))


class PlayerSetStanceAdmissionTest(unittest.TestCase):
    def _enqueue(self, state: loopback.ServerState, args: dict | None = None, **kwargs: object):
        before_id = state._next_id
        before_q = len(bound_queue(state, "server"))
        status, body = state.enqueue_command(
            CMD,
            dict(VALID) if args is None else args,
            peer="server",
            **kwargs,
        )
        self.assertEqual(len(bound_queue(state, "server")), before_q)
        self.assertEqual(state._next_id, before_id)
        self.assertNotEqual(status, 200)
        return status, body

    def test_old_version_refuses_without_a_queue_id(self) -> None:
        state = _bound_state(version_validator=lambda _version: "version_mismatch")
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = self._enqueue(state)
        self.assertEqual(body["error"], "version_blocked")
        self.assertEqual(status, 409)

    def test_missing_verb_wrong_hash_and_stale_census_refuse(self) -> None:
        full = _full_commands()
        cases = {
            "missing_verb": (["entities_query"], EXPECTED_SERVER_ARG_CONTRACT_HASH, None, "bridge_capability_missing"),
            "absent_hash": (full, None, None, "arg_contract_mismatch"),
            "wrong_hash": (full, "0" * 16, None, "arg_contract_mismatch"),
            "stale": (full, EXPECTED_SERVER_ARG_CONTRACT_HASH, "gen-old", "bridge_capability_missing"),
        }
        for name, (commands, ach, generation, error) in cases.items():
            with self.subTest(name=name):
                state = _bound_state()
                _announce(state, commands, ach, generation)
                _status, body = self._enqueue(state)
                self.assertEqual(body["error"], error)

    def test_accredited_census_queues_one_command(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command(CMD, dict(VALID), peer="server")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["cmd"], CMD)
        queued = [
            {key: value for key, value in command.items() if key != "_broker_pin"}
            for command in bound_queue(state, "server")
        ]
        self.assertEqual(queued, [{"id": 1, "cmd": CMD, "args": dict(VALID)}])
        status, bad = state.enqueue_command(
            CMD, {"uid": "", "stance": "erect", "recover": False}, peer="server"
        )
        self.assertEqual((status, bad), (400, {"error": "bad_args"}))
        self.assertEqual(len(bound_queue(state, "server")), 1)

    def test_live_profile_refuses_with_or_without_admin(self) -> None:
        self.assertEqual(
            loopback.live_profile_refusal(CMD, enabled=True, admin_enabled=False),
            loopback.LIVE_PROFILE_REFUSED,
        )
        self.assertEqual(
            loopback.live_profile_refusal(CMD, enabled=True, admin_enabled=True),
            loopback.LIVE_PROFILE_REFUSED,
        )
        self.assertIsNone(loopback.live_profile_refusal(CMD, enabled=False, admin_enabled=False))


class PlayerSetStanceSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = BRIDGE.read_text(encoding="utf-8")
        self.messages = MESSAGES.read_text(encoding="utf-8")
        self.care = CARE.read_text(encoding="utf-8")
        self.stance = STANCE.read_text(encoding="utf-8")
        self.dispatch = _method_body(
            self.bridge, "protected bool DispatchPlayerSetStance(MCPCommand command, MCPResult result)"
        )
        self.method = _method_body(
            self.stance, "string MCPSetStanceServer(string stance, bool recover)"
        )
        self.begin = _method_body(
            self.stance, "string MCPStanceBegin(string stance, bool recover)"
        )
        self.apply = _method_body(
            self.stance, "protected void MCPStanceOnCommandHandler(float pDt)"
        )
        self.verify = _method_body(
            self.stance, "void MCPStanceVerify()"
        )

    def test_handler_uses_uid_and_vanilla_primitives_without_health_writes(self) -> None:
        self.assertLess(self.dispatch.index('stanceUid == ""'), self.dispatch.index("FindHumanByUid"))
        self.assertLess(self.dispatch.index("FindHumanByUid"), self.dispatch.index("GetIdentity"))
        self.assertLess(self.dispatch.index("GetPlainId() != stanceUid"), self.dispatch.index("IsAlive()"))
        self.assertIn('result.error = "player_dead"', self.dispatch)
        self.assertNotIn("GetFirstHuman", self.dispatch)
        self.assertNotIn("ResolvePlayer", self.dispatch)
        self.assertNotIn("SetHealth", self.dispatch)
        self.assertNotIn("MCPHealServer", self.dispatch)
        self.assertNotIn("player_kill", self.dispatch)
        self.assertNotIn("player_respawn", self.dispatch)
        self.assertNotIn("SetHealth", self.method)
        self.assertNotIn("MCPHealServer", self.method)
        self.assertNotIn("ResetAllZones", self.method)
        self.assertIn("return MCPStanceBegin(stance, recover);", self.method)
        self.assertNotIn("StartCommand_Move()", self.method)
        self.assertNotIn("StartCommand_Move()", self.stance)
        self.assertNotIn("StartCommand_Move()", self.dispatch)
        self.assertIn("DayZPlayerSyncJunctures.SendPlayerUnconsciousness(this, false)", self.begin)
        self.assertNotIn("MCPSetStanceServer", re.sub(r"//[^\n]*", "", self.care))
        self.assertNotIn("MCPStanceBegin(", re.sub(r"//[^\n]*", "", self.care))
        self.assertIn("GetMovementState(state)", self.care)
        self.assertIn("m_iStanceIdx", self.care)
        self.assertIn("DayZPlayerConstants.STANCEIDX_ERECT", self.care)
        self.assertIn("DayZPlayerConstants.STANCEIDX_CROUCH", self.care)
        self.assertIn("DayZPlayerConstants.STANCEIDX_PRONE", self.care)
        self.assertIn('return "still_unconscious"', self.begin)
        self.assertIn('return "in_vehicle"', self.stance)
        self.assertIn('return "no_move_command"', self.begin)
        self.assertIn('return "swimming"', self.stance)
        self.assertIn('return "climbing"', self.stance)
        self.assertIn('return "in_water"', self.stance)
        self.assertIn('return "heavy_item"', self.stance)
        self.assertIn('return "cannot_change_stance"', self.stance)
        self.assertIn('return "busy"', self.stance)
        self.assertIn("IsSwimming()", self.stance)
        self.assertIn("IsClimbingLadder()", self.stance)
        self.assertIn("IsClimbing()", self.stance)
        self.assertIn("IsInWater()", self.stance)
        self.assertIn("DayZPlayerUtils.PlayerCanChangeStance(this, stanceId)", self.stance)
        self.assertIn("IsHeavyBehaviour()", self.stance)
        self.assertIn("const int MCP_INPUT_UDT_PLAYER_STANCE = 78541080", self.stance)
        self.assertIn("const int MCP_RPC_PLAYER_STANCE = 78541081", self.stance)
        self.assertIn("rpc.Send(this, MCP_RPC_PLAYER_STANCE, true, identity)", self.stance)
        self.assertIn("override void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx)", self.stance)
        self.assertIn(
            "override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)",
            self.stance,
        )
        self.assertIn(
            "override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)",
            self.stance,
        )
        self.assertIn("new ScriptInputUserData()", self.stance)
        self.assertIn("message.Write(MCP_INPUT_UDT_PLAYER_STANCE)", self.stance)
        self.assertIn("moveCmd.ForceStance(m_MCPStanceId)", self.apply)
        self.assertIn("moveCmd.ForceStance(-1)", self.stance)
        self.assertIn("m_MCPStanceWaitingOwner", self.stance)
        self.assertIn("requestId != m_MCPStanceRequestId", self.stance)
        self.assertIn("bool MCPStanceCloseServer(int connGen)", self.stance)
        self.assertIn("void MCPStanceCancel(string reason)", self.stance)
        self.assertIn("requestId <= m_MCPStanceClosedFloor", self.stance)
        self.assertIn("MCPStanceCancel(\"no_owner_ack\")", self.stance)
        self.assertIn("MCPStanceCancel(\"timeout\")", self.stance)
        self.assertIn("m_MCPStanceCancelOutstanding", self.stance)
        self.assertNotIn('MCPStanceCancel("expired")', self.stance)
        self.assertIn("m_MCPStanceCancelRequestId", self.stance)
        self.assertIn("if (MCPStanceBusyNow())", self.begin)
        self.assertLess(self.begin.index("if (MCPStanceBusyNow())"), self.begin.index("SendPlayerUnconsciousness"))
        self.assertIn("if (hadUnlock)", self.stance)
        self.assertIn("if (!hadRequest)", self.stance)
        self.assertIn("override void OnReconnect()", self.stance)
        self.assertIn("m_MCPStanceRequestId <= m_MCPStanceOwnerClosedFloor", self.stance)
        self.assertIn("m_MCPStanceVerifyArmed = true", self.apply)
        self.assertIn(" observed=", self.stance)
        self.assertIn("stanceReport.requested = stanceWanted", self.dispatch)
        self.assertIn("stanceReport.applied = stanceError == """, self.dispatch)
        self.assertIn("stanceReport.recover_requested = command.args.recover", self.dispatch)
        self.assertNotIn("stanceReport.after", self.dispatch)
        self.assertNotIn("stanceReport.recovered", self.dispatch)
        self.assertIn("string requested", self.messages)
        self.assertIn("bool applied", self.messages)
        self.assertIn("bool recover_requested", self.messages)
        self.assertIn("class MCPPlayerSetStance", self.messages)
        self.assertIn("ref MCPPlayerSetStance player_set_stance", self.messages)
        self.assertIn("player_set_stance: erect, crouch or prone", self.messages)
        self.assertIn('command.cmd == "player_set_stance"', self.bridge)
        self.assertIn("player_godmode,player_heal,player_kill,player_set_stance,player_teleport,", self.bridge)
        self.assertIn(CMD, result_prune.PRUNABLE_FIELDS)
        self.assertIn(
            EXPECTED_SERVER_ARG_CONTRACT_HASH,
            self.bridge,
        )
        self.assertGreater(
            result_prune.PRUNABLE_FIELDS.index("player_set_stance"),
            result_prune.PRUNABLE_FIELDS.index("player_godmode"),
        )
        self.assertLess(
            result_prune.PRUNABLE_FIELDS.index("player_set_stance"),
            result_prune.PRUNABLE_FIELDS.index("player_move"),
        )


class PlayerSetStanceM1RegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stance = STANCE.read_text(encoding="utf-8")

    def test_case_a_begin_busy_while_request_in_flight(self) -> None:
        begin = _method_body(self.stance, "string MCPStanceBegin(string stance, bool recover)")
        busy = _method_body(self.stance, "protected bool MCPStanceBusyNow()")
        self.assertLess(begin.index('return "bad_args"'), begin.index("MCPStanceBusyNow()"))
        self.assertLess(begin.index("MCPStanceBusyNow()"), begin.index("IsAlive()"))
        self.assertLess(begin.index("MCPStanceBusyNow()"), begin.index("SendPlayerUnconsciousness"))
        for token in (
            "m_MCPStancePending",
            "m_MCPStanceWaitingOwner",
            "m_MCPStanceCancelOutstanding",
            "m_MCPStanceUnlock",
            "m_MCPStanceSendWanted",
            "m_MCPStanceCancelWanted",
        ):
            with self.subTest(token=token):
                self.assertIn(token, busy)

    def test_case_b_server_close_never_drops_an_owner_applied_start(self) -> None:
        iud = _method_body(
            self.stance,
            "override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)",
        )
        cancel = _method_body(self.stance, "void MCPStanceCancel(string reason)")
        close_srv = _method_body(self.stance, "bool MCPStanceCloseServer(int connGen)")
        handler = _method_body(self.stance, "protected void MCPStanceOnCommandHandler(float pDt)")

        self.assertNotIn('MCPStanceCancel("expired")', iud)
        self.assertNotIn("GetTickTime", iud)
        self.assertIn("reason=applied_after_close", iud)
        self.assertIn("if (requestId <= m_MCPStanceClosedFloor)", iud)
        self.assertLess(
            iud.index("if (requestId <= m_MCPStanceOwnerClosedFloor)"),
            iud.index("if (requestId != m_MCPStanceRequestId || stanceId != m_MCPStanceId)"),
        )
        self.assertLess(
            iud.index("if (requestId != m_MCPStanceRequestId || stanceId != m_MCPStanceId)"),
            iud.index("if (requestId == m_MCPStanceAcceptedId)"),
        )
        self.assertLess(
            iud.index("if (requestId == m_MCPStanceAcceptedId)"),
            iud.index("reason=applied_after_close"),
        )
        self.assertLess(
            iud.index("reason=applied_after_close"),
            iud.index("m_MCPStanceFromIud = true"),
        )

        guard = "if (GetGame() && GetGame().IsMultiplayer() && m_MCPStanceFromIud && GetIdentity())"
        self.assertIn(guard, cancel)
        self.assertLess(cancel.index(guard), cancel.index("m_MCPStancePending = false"))
        self.assertIn('MCPStanceCancel("no_owner_ack")', handler)
        self.assertIn('MCPStanceCancel("lease_close")', close_srv)

    def test_case_c_owner_decides_timeouts_on_sim_time(self) -> None:
        self.assertNotIn("GetTickTime", self.stance)
        self.assertNotIn("CallLater", self.stance)
        cmd = _method_body(
            self.stance,
            "override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)",
        )
        handler = _method_body(self.stance, "protected void MCPStanceOnCommandHandler(float pDt)")
        self.assertIn("MCPStanceOnCommandHandler(pDt);", cmd)
        self.assertIn("m_MCPStanceElapsedS += pDt;", handler)
        self.assertIn("m_MCPStanceUnlockElapsedS += pDt;", handler)
        self.assertIn("m_MCPStanceVerifyElapsedS += pDt;", handler)
        pending_tail = handler[handler.index("if (!m_MCPStancePending)") :]
        self.assertIn("IsMultiplayer()", pending_tail)
        self.assertIn("GetIdentity()", pending_tail)
        self.assertIn("m_MCPStanceFromIud", pending_tail)
        self.assertIn('MCPStanceCancel("owner_gone")', pending_tail)
        self.assertIn('MCPStanceCancel("timeout")', pending_tail)
        self.assertNotIn("m_MCPStanceRequestId <= m_MCPStanceClosedFloor", pending_tail)


class PlayerSetStanceShouldTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stance = STANCE.read_text(encoding="utf-8")

    def test_s1_owner_sends_and_pends_only_in_command_handler(self) -> None:
        on_rpc = _method_body(
            self.stance,
            "override void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx)",
        )
        send_iud = _method_body(self.stance, "protected bool MCPStanceSendOwnerIud()")
        handler = _method_body(self.stance, "protected void MCPStanceOnCommandHandler(float pDt)")
        cancel = _method_body(self.stance, "void MCPStanceCancel(string reason)")

        self.assertNotIn("m_MCPStancePending = true", on_rpc)
        self.assertNotIn("MCPStanceSendOwnerIud()", on_rpc)
        self.assertIn("m_MCPStanceSendWanted = true;", on_rpc)
        self.assertIn("m_MCPStancePending = false;", on_rpc)
        self.assertNotIn("m_MCPStancePending", send_iud)
        self.assertLess(send_iud.index("message.Send();"), send_iud.index("return true;"))
        self.assertIn("else if (MCPStanceSendOwnerIud())", handler)
        self.assertLess(
            handler.index("else if (MCPStanceSendOwnerIud())"),
            handler.index("m_MCPStancePending = true;"),
        )
        self.assertLess(
            handler.index("m_MCPStancePending = true;"),
            handler.index("moveCmd.ForceStance(m_MCPStanceId);"),
        )
        self.assertNotIn("MCPStanceSendCancelIud()", cancel)
        self.assertIn("m_MCPStanceCancelWanted = true;", cancel)
        self.assertIn("MCPStanceOwnerCancelStep();", handler)

    def test_s2_verify_logs_only_and_cancel_has_no_force_stance(self) -> None:
        verify = _method_body(self.stance, "void MCPStanceVerify()")
        cancel = _method_body(self.stance, "void MCPStanceCancel(string reason)")
        for forbidden in ("ForceStance", "TryUnlock", "CallLater"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, verify)
        self.assertIn("GetGame().IsServer()", verify)
        self.assertNotIn("ForceStance", cancel)

    def test_s3_guards_order_and_stop_hand_event(self) -> None:
        soft = _method_body(self.stance, "protected string MCPStanceSoftRefusal(int stanceId)")
        apply_refusal = _method_body(self.stance, "protected string MCPStanceApplyRefusal()")
        handler = _method_body(self.stance, "protected void MCPStanceOnCommandHandler(float pDt)")

        self.assertIn("IsProcessing()", soft)
        self.assertIn("IsRunning()", soft)
        self.assertLess(apply_refusal.index("IsAlive()"), apply_refusal.index("IsUnconscious()"))
        self.assertLess(
            apply_refusal.index("IsUnconscious()"),
            apply_refusal.index("MCPStanceHardRefusal"),
        )
        self.assertLess(
            apply_refusal.index("MCPStanceHardRefusal"),
            apply_refusal.index("MCPStanceSoftRefusal"),
        )
        self.assertIn("StopHandEvent();", handler)
        self.assertLess(
            handler.index("StopHandEvent();"),
            handler.index("moveCmd.ForceStance(m_MCPStanceId);"),
        )

    def test_s4_try_unlock_keeps_flag_without_move_command(self) -> None:
        try_unlock = _method_body(self.stance, "protected void MCPStanceTryUnlock()")
        handler = _method_body(self.stance, "protected void MCPStanceOnCommandHandler(float pDt)")
        self.assertIn("if (!moveCmd)", try_unlock)
        self.assertLess(
            try_unlock.index("if (!moveCmd)"),
            try_unlock.index("moveCmd.ForceStance(-1);"),
        )
        self.assertLess(
            try_unlock.index("if (!moveCmd)"),
            try_unlock.index("m_MCPStanceUnlock = false;"),
        )
        self.assertEqual(
            self.stance.count("MCPStanceTryUnlock()"),
            handler.count("MCPStanceTryUnlock()") + 1,
        )

    def test_s5_changelog_has_no_control_bytes(self) -> None:
        raw = CHANGELOG.read_bytes()
        bad = [byte for byte in raw if byte < 0x20 and byte not in (0x09, 0x0A, 0x0D)]
        self.assertEqual(bad, [])
        text = raw.decode("utf-8")
        self.assertIn("refused as `busy`", text)

    def test_s6_tool_description_matches_two_sided_contract(self) -> None:
        app, _runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in app._tool_manager.list_tools()}
        desc = tools[CMD].description
        self.assertNotIn("CallLater", desc)
        self.assertNotIn("This is a server command", desc)
        self.assertIn("StartCommand_Move is never called", desc)
        self.assertIn("applied=true", desc)
        self.assertIn("before", desc)
        self.assertIn("observed=", desc)
        self.assertIn("busy", desc)
        self.assertIn("rpc_failed", desc)
        self.assertIn(server.GODMODE_DEFAULT_LINE, desc)


if __name__ == "__main__":
    unittest.main()
