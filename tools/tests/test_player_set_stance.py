"""player_set_stance: test-mode stance change without kill, respawn or health writes."""

from __future__ import annotations

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
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    _bounded,
    method_body as _enforce_method_body,
    python_source,
)
from tests.fence_helpers import INST_CLIENT, INST_SERVER, PID_CLIENT, PID_SERVER, bound_queue
from tests.player_move_helpers import _LogString, _without_comments


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


_STANCE_CONSTS = SimpleNamespace(
    STANCEIDX_ERECT=0,
    STANCEIDX_CROUCH=1,
    STANCEIDX_PRONE=2,
    STANCEIDX_RAISEDERECT=3,
    STANCEIDX_RAISEDCROUCH=4,
    STANCEIDX_RAISEDPRONE=5,
)

_STANCE_SIGNATURES = (
    "protected bool MCPStanceBusyNow()",
    "string MCPStanceBegin(string stance, bool recover)",
    "string MCPSetStanceServer(string stance, bool recover)",
    "protected string MCPStanceHardRefusal(int stanceId)",
    "protected string MCPStanceSoftRefusal(int stanceId)",
    "protected string MCPStanceApplyRefusal()",
    "protected bool MCPStanceSendOwnerRpc(PlayerIdentity identity)",
    "protected void MCPStanceSendApplyAckRpc(PlayerIdentity identity, int requestId, bool releaseNow)",
    "protected bool MCPStanceSendOwnerIud()",
    "override void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx)",
    "override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)",
    "override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)",
    "protected void MCPStanceOnCommandHandler(float pDt)",
    "protected void MCPStanceOwnerCancelStep()",
    "void MCPStanceVerify()",
    "protected void MCPStanceTryUnlock()",
    "protected string MCPStanceLogLine(string observed)",
    "bool MCPStanceActiveNow()",
    "void MCPStanceCancel(string reason)",
    "protected bool MCPStanceSendCancelIud()",
    "protected void MCPStanceSendCancelRpc(PlayerIdentity identity)",
    "bool MCPStanceCloseServer(int connGen)",
    "override void OnReconnect()",
    "string MCPStanceNameFromId(int stanceId)",
)

_ENFORCE_STR_LIT = re.compile(r'"(?:\\.|[^"\\])*"')
_STANCE_CLASS_TYPES = frozenset(
    {
        "PlayerIdentity",
        "EntityAI",
        "HumanCommandMove",
        "HumanMovementState",
        "ScriptRPC",
        "ScriptInputUserData",
        "ParamsReadContext",
    }
)


class _ParamsReadCtx:
    def __init__(self, values: list[object]) -> None:
        self._values = list(values)
        self._index = 0

    def _read(self) -> tuple[bool, object]:
        if self._index >= len(self._values):
            return False, 0
        val = self._values[self._index]
        self._index += 1
        return True, val


def _translate_stance_method(
    source: str, signature: str, ns: dict[str, object], members: set[str]
) -> None:
    body = _enforce_method_body(source, signature)

    def _rewrite(code: str) -> str:
        code = re.sub(r"\b([A-Za-z_]\w*)\s*\+=\s*([^;]+);", r"\1 = \1 + (\2);", code)
        return re.sub(r"\b([A-Za-z_][\w.]*)\.ToString\(\)", r"ToStr(\1)", code)

    pieces: list[str] = []
    last = 0
    for m in _ENFORCE_STR_LIT.finditer(body):
        pieces.append(_rewrite(body[last : m.start()]))
        pieces.append(m.group(0))
        last = m.end()
    pieces.append(_rewrite(body[last:]))
    wrapped = signature + "\n{" + "".join(pieces) + "}\n"
    name, py_code = python_source(wrapped, signature, class_types=_STANCE_CLASS_TYPES, members=members)
    py_code = re.sub(r"ctx\.Read\((\w+)\)", r"(_r := ctx._read(), \1 := _r[1])[0][0]", py_code)
    ns.setdefault("_S", _LogString)
    ns.setdefault(
        "_string_copy",
        lambda v: _LogString(v.data) if isinstance(v, _LogString) else v,
    )
    exec(compile(py_code, f"<enforce:{name}>", "exec"), ns)


class _StancePeer:
    def __init__(
        self,
        stance_source: str,
        care_source: str,
        *,
        is_server: bool,
        is_client: bool,
        is_dedicated: bool,
        conn_gen: int,
        rpc_out: list[tuple[int, list[object]]],
        iud_out: list[list[object]],
    ) -> None:
        self.is_server = is_server
        self.is_client = is_client
        self.is_dedicated = is_dedicated
        self.multiplayer = True
        self.conn_gen = conn_gen
        self.can_store_iud = True
        self.alive = True
        self.unconscious = False
        self.in_vehicle = False
        self.swimming = False
        self.climbing_ladder = False
        self.climbing = False
        self.in_water = False
        self.heavy_item = False
        self.restrained = False
        self.in_action = False
        self.emote_playing = False
        self.inventory_processing = False
        self.weapon_running = False
        self.can_change_stance = True
        self.has_move = True
        self.changing_stance = False
        self.has_identity = True
        self.stance_idx = _STANCE_CONSTS.STANCEIDX_ERECT
        self.forced_stance = -1
        self.force_calls: list[int] = []
        self.hand_stops = 0
        self.uncon_junctures: list[bool] = []
        self.logs: list[str] = []
        peer = self

        class FakeMoveCmd:
            def IsChangingStance(self) -> bool:
                return peer.changing_stance

            def ForceStance(self, stance_id: int) -> None:
                peer.force_calls.append(stance_id)
                peer.forced_stance = stance_id
                if stance_id >= 0:
                    peer.stance_idx = stance_id

        self._move_cmd = FakeMoveCmd()

        class FakeScriptRPC:
            def __init__(self) -> None:
                self.values: list[object] = []

            def Write(self, value: object) -> None:
                self.values.append(value)

            def Send(self, _target: object, rpc_type: int, _reliable: bool, _identity: object) -> None:
                rpc_out.append((rpc_type, list(self.values)))

        class FakeInputUserData:
            def __init__(self) -> None:
                self.values: list[object] = []

            @staticmethod
            def CanStoreInputUserData() -> bool:
                return peer.can_store_iud

            def Write(self, value: object) -> None:
                self.values.append(value)

            def Send(self) -> None:
                iud_out.append(list(self.values))

        constants: dict[str, int | float] = {}
        for ctype, cname, cval in re.findall(r"^const (int|float) (\w+) = (-?[0-9.]+);", stance_source, re.M):
            constants[cname] = int(cval) if ctype == "int" else float(cval)

        defaults = {"bool": False, "int": 0, "float": 0.0, "string": _LogString.of("")}
        members: set[str] = {"m_MovementState"}
        member_init: dict[str, object] = {"m_MovementState": SimpleNamespace(m_iStanceIdx=0)}
        for mtype, mname in re.findall(r"^\tprotected (bool|int|float|string) (m_\w+);", stance_source, re.M):
            members.add(mname)
            member_init[mname] = _LogString.of("") if mtype == "string" else defaults[mtype]

        identity_obj = SimpleNamespace(GetPlainId=lambda: _LogString.of(UID))
        game_obj = SimpleNamespace(
            IsMultiplayer=lambda: peer.multiplayer,
            IsServer=lambda: peer.is_server,
            IsClient=lambda: peer.is_client,
            IsDedicatedServer=lambda: peer.is_dedicated,
        )

        self.ns: dict[str, object] = {
            "_S": _LogString,
            "ToStr": lambda v: _LogString.of(str(v)),
            "this": self,
            "super": SimpleNamespace(
                OnRPC=lambda *_a: None,
                OnInputUserDataProcess=lambda *_a: False,
                CommandHandler=lambda *_a: None,
                OnReconnect=lambda: None,
            ),
            "Print": lambda line: peer.logs.append(line.text),
            "GetGame": lambda: game_obj,
            "GetIdentity": lambda: identity_obj if peer.has_identity else None,
            "MCPConnGenValue": lambda: peer.conn_gen,
            "IsAlive": lambda: peer.alive,
            "IsUnconscious": lambda: peer.unconscious,
            "IsInVehicle": lambda: peer.in_vehicle,
            "IsSwimming": lambda: peer.swimming,
            "IsClimbingLadder": lambda: peer.climbing_ladder,
            "IsClimbing": lambda: peer.climbing,
            "IsInWater": lambda: peer.in_water,
            "GetItemInHands": lambda: SimpleNamespace(IsHeavyBehaviour=lambda: peer.heavy_item) if peer.heavy_item else None,
            "IsRestrained": lambda: peer.restrained,
            "GetCommand_Action": lambda: object() if peer.in_action else None,
            "GetEmoteManager": lambda: SimpleNamespace(IsEmotePlaying=lambda: peer.emote_playing),
            "GetDayZPlayerInventory": lambda: SimpleNamespace(IsProcessing=lambda: peer.inventory_processing),
            "GetWeaponManager": lambda: SimpleNamespace(IsRunning=lambda: peer.weapon_running),
            "GetCommand_Move": lambda: peer._move_cmd if peer.has_move else None,
            "StopHandEvent": lambda: setattr(peer, "hand_stops", peer.hand_stops + 1),
            "GetMovementState": lambda state: setattr(state, "m_iStanceIdx", peer.stance_idx),
            "HumanMovementState": lambda: SimpleNamespace(m_iStanceIdx=0),
            "DayZPlayerConstants": _STANCE_CONSTS,
            "DayZPlayerUtils": SimpleNamespace(PlayerCanChangeStance=lambda _p, _s: peer.can_change_stance),
            "DayZPlayerSyncJunctures": SimpleNamespace(
                SendPlayerUnconsciousness=lambda _p, val: peer.uncon_junctures.append(val)
            ),
            "ScriptRPC": FakeScriptRPC,
            "ScriptInputUserData": FakeInputUserData,
            **constants,
            **member_init,
        }
        for care_sig in ("string MCPStanceName()", "int MCPStanceIdFromName(string stance)"):
            _translate_stance_method(care_source, care_sig, self.ns, members)
        for sig in _STANCE_SIGNATURES:
            _translate_stance_method(stance_source, sig, self.ns, members)

    def command_handler(self, dt: float) -> None:
        self.ns["CommandHandler"](dt, 0, False)  # type: ignore[operator]

    def cancel(self, reason: str) -> None:
        self.ns["MCPStanceCancel"](_LogString.of(reason))  # type: ignore[operator]

    def close_server(self, conn_gen: int) -> bool:
        return bool(self.ns["MCPStanceCloseServer"](conn_gen))  # type: ignore[operator]

    def active_now(self) -> bool:
        return bool(self.ns["MCPStanceActiveNow"]())  # type: ignore[operator]

    def busy_now(self) -> bool:
        return bool(self.ns["MCPStanceBusyNow"]())  # type: ignore[operator]


class _StancePair:
    def __init__(self, stance_source: str, care_source: str, conn_gen: int = 7) -> None:
        self.conn_gen = conn_gen
        self.rpcs: list[tuple[int, list[object]]] = []
        self.iuds: list[list[object]] = []
        self.server = _StancePeer(
            stance_source,
            care_source,
            is_server=True,
            is_client=False,
            is_dedicated=True,
            conn_gen=conn_gen,
            rpc_out=self.rpcs,
            iud_out=[],
        )
        self.owner = _StancePeer(
            stance_source,
            care_source,
            is_server=False,
            is_client=True,
            is_dedicated=False,
            conn_gen=conn_gen,
            rpc_out=[],
            iud_out=self.iuds,
        )

    def begin(self, stance: str, recover: bool = False) -> str:
        res = self.server.ns["MCPSetStanceServer"](_LogString.of(stance), recover)  # type: ignore[operator]
        return res.text

    def deliver_rpcs(self) -> None:
        while self.rpcs:
            rpc_type, payload = self.rpcs.pop(0)
            self.owner.ns["OnRPC"](None, rpc_type, _ParamsReadCtx(payload))  # type: ignore[operator]

    def deliver_iuds(self) -> None:
        while self.iuds:
            payload = self.iuds.pop(0)
            udt = int(payload[0])  # type: ignore[arg-type]
            self.server.ns["OnInputUserDataProcess"](udt, _ParamsReadCtx(payload[1:]))  # type: ignore[operator]

    def step(self, dt: float) -> None:
        self.deliver_rpcs()
        self.owner.command_handler(dt)
        self.deliver_iuds()
        self.server.command_handler(dt)
        self.deliver_rpcs()

    def player_input_stance(self, stance_id: int) -> None:
        if self.owner.forced_stance >= 0:
            return
        self.owner.stance_idx = stance_id
        if self.server.forced_stance < 0:
            self.server.stance_idx = stance_id


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

    def test_old_bound_client_version_refuses_without_a_queue_id(self) -> None:
        state = _bound_state(
            version_validator=lambda version: "ok" if version == "12~1.30" else "version_mismatch"
        )
        state.install_bound_peer(
            instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id="stance-run"
        )
        state.record_poll("server", "12~1.30", instance=INST_SERVER, source_pid=PID_SERVER)
        state.record_poll("client", "11~1.30", instance=INST_CLIENT, source_pid=PID_CLIENT)
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
        # main pins heal, godmode, player_move adjacent in MCPResult and the
        # whole tail from player_move; the prune order mirrors MCPResult. So the
        # stance ref sits between bot and player_heal, before player_move.
        stance_at = result_prune.PRUNABLE_FIELDS.index("player_set_stance")
        self.assertEqual(
            result_prune.PRUNABLE_FIELDS[stance_at - 1 : stance_at + 3],
            ("bot", "player_set_stance", "player_heal", "player_godmode"),
        )
        self.assertLess(stance_at, result_prune.PRUNABLE_FIELDS.index("player_move"))


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

        # Replay applied_after_close + subsequent cancel ACK while server is busy:
        pair = _StancePair(self.stance, CARE.read_text(encoding="utf-8"))
        self.assertEqual(pair.begin("prone", False), "")
        pair.deliver_rpcs()
        pair.owner.command_handler(0.1)
        self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
        # Server closes before start IUD arrives; late start IUD is accepted as applied_after_close.
        self.assertTrue(pair.server.close_server(pair.conn_gen))
        pair.server.inventory_processing = True
        pair.deliver_iuds()
        self.assertTrue(any("reason=applied_after_close" in line for line in pair.server.logs))
        pair.server.command_handler(0.1)
        self.assertEqual(pair.server.force_calls, [])
        # Owner processes cancel RPC and sends cancel ACK; both sides retain lock until server applies.
        pair.deliver_rpcs()
        pair.owner.command_handler(0.1)
        pair.deliver_iuds()
        self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
        self.assertTrue(pair.owner.active_now())
        self.assertTrue(pair.server.ns["m_MCPStancePending"])
        pair.server.inventory_processing = False
        pair.server.command_handler(0.1)
        self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
        pair.deliver_rpcs()
        pair.owner.command_handler(0.1)
        pair.server.command_handler(0.1)
        self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
        self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
        self.assertFalse(pair.owner.active_now())
        self.assertFalse(pair.server.active_now())

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

    def test_p1_1_owner_checks_guards_and_move_before_sending_start_iud(self) -> None:
        # Sol P1 #1a: when the owner is impeded (no_move_command or busy) while
        # the server is ready, the owner must NOT emit a start IUD early; on
        # timeout both sides finish with ForceStance=[] instead of server=[prone]
        # and owner=[].
        for impediment in ("no_move_command", "busy"):
            with self.subTest(impediment=impediment, outcome="timeout"):
                pair = _StancePair(self.stance, CARE.read_text(encoding="utf-8"))
                if impediment == "no_move_command":
                    pair.owner.has_move = False
                else:
                    pair.owner.inventory_processing = True
                self.assertEqual(pair.begin("prone", False), "")
                pair.deliver_rpcs()
                for _ in range(10):
                    pair.step(0.5)
                    self.assertEqual(pair.owner.force_calls, [])
                    self.assertEqual(pair.server.force_calls, [])
                pair.step(0.2)
                self.assertEqual(pair.owner.force_calls, [])
                self.assertEqual(pair.server.force_calls, [])
                self.assertFalse(pair.owner.active_now())
                self.assertFalse(pair.server.active_now())
                self.assertFalse(pair.server.busy_now())

            with self.subTest(impediment=impediment, outcome="recovers_before_timeout"):
                pair = _StancePair(self.stance, CARE.read_text(encoding="utf-8"))
                if impediment == "no_move_command":
                    pair.owner.has_move = False
                else:
                    pair.owner.inventory_processing = True
                self.assertEqual(pair.begin("prone", False), "")
                pair.deliver_rpcs()
                pair.step(0.5)
                self.assertEqual(pair.owner.force_calls, [])
                self.assertEqual(pair.server.force_calls, [])
                pair.owner.has_move = True
                pair.owner.inventory_processing = False
                pair.step(0.5)
                self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                pair.step(1.6)
                self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
                self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])

    def test_p1_1_owner_terminal_refusal_sends_cancel_to_server(self) -> None:
        # Sol P1 #1b: a terminal refusal on the owner (both in SendWanted and in
        # the pending fallback branch) must send a cancel IUD so the server does
        # not remain stuck waiting.
        with self.subTest(phase="send_wanted"):
            pair = _StancePair(self.stance, CARE.read_text(encoding="utf-8"))
            self.assertEqual(pair.begin("prone", False), "")
            pair.deliver_rpcs()
            pair.owner.in_vehicle = True
            pair.step(0.1)
            self.assertEqual(pair.owner.force_calls, [])
            self.assertEqual(pair.server.force_calls, [])
            self.assertFalse(pair.owner.active_now())
            self.assertFalse(pair.server.active_now())
            self.assertFalse(pair.server.busy_now())
            self.assertTrue(any("reason=in_vehicle" in line for line in pair.owner.logs))
            self.assertTrue(any("reason=cancel_ack" in line for line in pair.server.logs))

        with self.subTest(phase="pending_branch_with_delayed_iud_slot"):
            pair = _StancePair(self.stance, CARE.read_text(encoding="utf-8"))
            self.assertEqual(pair.begin("prone", False), "")
            pair.deliver_rpcs()
            # Force the defensive pending path directly with IUD slot initially full.
            pair.owner.ns["m_MCPStanceSendWanted"] = False
            pair.owner.ns["m_MCPStancePending"] = True
            pair.owner.in_vehicle = True
            pair.owner.can_store_iud = False
            pair.owner.command_handler(0.1)
            self.assertFalse(pair.owner.ns["m_MCPStancePending"])
            self.assertTrue(pair.owner.active_now())
            pair.owner.can_store_iud = True
            pair.step(0.1)
            self.assertEqual(pair.owner.force_calls, [])
            self.assertEqual(pair.server.force_calls, [])
            self.assertFalse(pair.owner.active_now())
            self.assertFalse(pair.server.active_now())
            self.assertFalse(pair.server.busy_now())

    def test_p1_2_cancel_ack_after_owner_apply_keeps_server_start_until_applied_then_unlocks(self) -> None:
        # Sol P1 #2: owner applies; server accepts start IUD but waits on
        # busy/no_move_command; lease_close arrives and owner sends cancel ACK;
        # server must NOT drop its pending apply, must apply once the impediment
        # clears, and must then release the ForceStance lock.
        for impediment in ("busy", "no_move_command"):
            for client_first in (False, True):
                with self.subTest(impediment=impediment, client_first=client_first):
                    pair = _StancePair(self.stance, CARE.read_text(encoding="utf-8"))
                    self.assertEqual(pair.begin("prone", False), "")
                    pair.deliver_rpcs()
                    # Owner applies and emits start IUD; server accepts it.
                    pair.owner.command_handler(0.1)
                    self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                    pair.deliver_iuds()
                    self.assertTrue(pair.server.ns["m_MCPStancePending"])
                    self.assertTrue(pair.server.ns["m_MCPStanceFromIud"])

                    # Server is temporarily impeded when its CommandHandler runs.
                    if impediment == "no_move_command":
                        pair.server.has_move = False
                    else:
                        pair.server.inventory_processing = True
                    pair.server.command_handler(0.1)
                    self.assertEqual(pair.server.force_calls, [])
                    self.assertTrue(pair.server.ns["m_MCPStancePending"])

                    # lease_close runs on both sides and owner ACKs cancel.
                    if client_first:
                        pair.owner.cancel("lease_close")
                        pair.owner.command_handler(0.1)
                        pair.deliver_iuds()
                        self.assertTrue(pair.server.close_server(pair.conn_gen))
                        pair.deliver_rpcs()
                    else:
                        self.assertTrue(pair.server.close_server(pair.conn_gen))
                        pair.owner.cancel("lease_close")
                        pair.deliver_rpcs()
                        pair.owner.command_handler(0.1)
                        pair.deliver_iuds()

                    self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                    self.assertTrue(pair.owner.active_now())
                    self.assertFalse(pair.server.ns["m_MCPStanceCancelOutstanding"])
                    self.assertTrue(pair.server.ns["m_MCPStancePending"])
                    self.assertTrue(pair.server.ns["m_MCPStanceFromIud"])
                    self.assertTrue(pair.server.active_now())

                    # Impediment clears: server completes ForceStance(prone), sends apply-ack, then both unlock.
                    pair.server.has_move = True
                    pair.server.inventory_processing = False
                    pair.server.command_handler(0.1)
                    self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                    pair.deliver_rpcs()
                    pair.owner.command_handler(0.1)
                    pair.server.command_handler(0.1)
                    self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
                    self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
                    self.assertFalse(pair.owner.active_now())
                    self.assertFalse(pair.server.active_now())
                    self.assertFalse(pair.server.busy_now())

    def test_r2_p1_1_server_keeps_owner_applied_start_across_server_refusals(self) -> None:
        # Sol r2 P1 #1: once the server accepts a start IUD (the owner already
        # applied ForceStance), server-side hard/terminal refusals while that
        # owner still exists and is alive must NOT drop Pending/FromIud; once
        # the impediment clears, the server applies ForceStance in lockstep.
        care = CARE.read_text(encoding="utf-8")
        for attr in ("in_water", "in_vehicle", "swimming", "climbing", "climbing_ladder", "heavy_item"):
            for with_close in (False, True):
                with self.subTest(server_refusal=attr, with_close=with_close):
                    pair = _StancePair(self.stance, care)
                    self.assertEqual(pair.begin("prone", False), "")
                    pair.deliver_rpcs()
                    pair.owner.command_handler(0.1)
                    self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                    pair.deliver_iuds()
                    self.assertTrue(pair.server.ns["m_MCPStancePending"])
                    self.assertTrue(pair.server.ns["m_MCPStanceFromIud"])

                    setattr(pair.server, attr, True)
                    pair.server.command_handler(0.1)
                    self.assertEqual(pair.server.force_calls, [])
                    self.assertTrue(pair.server.ns["m_MCPStancePending"])
                    self.assertTrue(pair.server.ns["m_MCPStanceFromIud"])

                    if with_close:
                        self.assertTrue(pair.server.close_server(pair.conn_gen))
                        pair.owner.cancel("lease_close")
                        pair.deliver_rpcs()
                        pair.owner.command_handler(0.1)
                        pair.deliver_iuds()
                    else:
                        # Advance owner past unlock and server past timeout while owner still exists.
                        pair.owner.command_handler(1.6)
                        pair.server.command_handler(5.5)

                    self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                    self.assertTrue(pair.owner.active_now())
                    self.assertEqual(pair.server.force_calls, [])
                    self.assertTrue(pair.server.ns["m_MCPStancePending"])
                    self.assertTrue(pair.server.ns["m_MCPStanceFromIud"])
                    self.assertTrue(pair.server.active_now())

                    setattr(pair.server, attr, False)
                    pair.server.command_handler(0.1)
                    self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                    pair.deliver_rpcs()
                    settle_dt = 0.1 if with_close else 1.6
                    pair.owner.command_handler(settle_dt)
                    pair.server.command_handler(settle_dt)
                    self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
                    self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
                    self.assertFalse(pair.owner.active_now())
                    self.assertFalse(pair.server.active_now())
                    self.assertFalse(pair.server.busy_now())

        with self.subTest(terminal="player_dead_clears_corpse"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            pair.deliver_rpcs()
            pair.owner.command_handler(0.1)
            pair.deliver_iuds()
            pair.server.alive = False
            pair.server.command_handler(0.1)
            self.assertEqual(pair.server.force_calls, [])
            self.assertFalse(pair.server.active_now())
            self.assertFalse(pair.server.busy_now())
            self.assertTrue(any("reason=player_dead" in line for line in pair.server.logs))

        with self.subTest(terminal="owner_gone_clears_server"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            pair.deliver_rpcs()
            pair.owner.command_handler(0.1)
            pair.deliver_iuds()
            pair.server.in_water = True
            pair.server.has_identity = False
            pair.server.command_handler(0.1)
            self.assertEqual(pair.server.force_calls, [])
            self.assertFalse(pair.server.active_now())
            self.assertFalse(pair.server.busy_now())

    def test_r2_p1_2_stale_or_duplicate_cancel_never_cancels_next_request_and_close_deduplicates(self) -> None:
        # Sol r2 P1 #2: duplicate/stale cancel RPCs must be idempotent and bound
        # to their request id without cancelling subsequent starts; server close
        # paths (both MCPStanceCancel and MCPBridge.DispatchLeaseClose) must not
        # emit duplicate cancel sends for the same player/request.
        care = CARE.read_text(encoding="utf-8")

        with self.subTest(case="server_cancel_deduplicates_repeated_close"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            self.assertEqual(len(pair.rpcs), 1)
            pair.rpcs.clear()
            self.assertTrue(pair.server.close_server(pair.conn_gen))
            self.assertTrue(pair.server.close_server(pair.conn_gen))
            self.assertEqual(len(pair.rpcs), 1)

        with self.subTest(case="bridge_dispatch_lease_close_deduplicates_floors"):
            bridge_src = BRIDGE.read_text(encoding="utf-8")
            helper_sig = "protected bool IsPlayerInCloseFloors(PlayerBase player, MCPArgs closeArgs)"
            helper_body = _enforce_method_body(bridge_src, helper_sig)
            close_body = _enforce_method_body(
                bridge_src, "protected bool DispatchLeaseClose(MCPCommand command, MCPResult result)"
            )
            self.assertIn("IsPlayerInCloseFloors(stanceClosePlayer, command.args)", close_body)

            ns: dict[str, object] = {
                "_S": _LogString,
                "_bounded": _bounded,
                "_string_copy": lambda v: _LogString(v.data) if isinstance(v, _LogString) else v,
            }
            _, py_code = python_source(
                helper_sig + "\n{" + helper_body + "}\n",
                helper_sig,
                class_types=frozenset({"PlayerBase", "MCPArgs", "MCPLeaseCloseFloor"}),
                members=set(),
            )
            py_code = re.sub(
                r"player\.GetNetworkID\((\w+),\s*(\w+)\)",
                r"\1, \2 = player.GetNetworkID()",
                py_code,
            )
            exec(compile(py_code, "<enforce:IsPlayerInCloseFloors>", "exec"), ns)
            in_floors = ns["IsPlayerInCloseFloors"]  # type: ignore[assignment]

            p1_closes: list[int] = []
            p2_closes: list[int] = []
            p1 = SimpleNamespace(
                GetNetworkID=lambda: (10, 20),
                MCPConnGenValue=lambda: 7,
                MCPStanceCloseServer=lambda cg: p1_closes.append(cg),
            )
            p2 = SimpleNamespace(
                GetNetworkID=lambda: (30, 40),
                MCPConnGenValue=lambda: 8,
                MCPStanceCloseServer=lambda cg: p2_closes.append(cg),
            )
            floors = EnforceArray([SimpleNamespace(net_low=10, net_high=20, conn_gen=7)])
            close_args = SimpleNamespace(close_floors=floors)
            self.assertTrue(in_floors(p1, close_args))  # type: ignore[operator]
            self.assertFalse(in_floors(p2, close_args))  # type: ignore[operator]
            self.assertFalse(in_floors(p1, SimpleNamespace(close_floors=None)))  # type: ignore[operator]
            self.assertFalse(in_floors(p1, None))  # type: ignore[operator]
            # Simulate both loops in DispatchLeaseClose: p1 closed once via floor, p2 once via fallback.
            for carried in floors:
                for pl in (p1, p2):
                    if pl.GetNetworkID() == (carried.net_low, carried.net_high):
                        pl.MCPStanceCloseServer(carried.conn_gen)
            for pl in (p1, p2):
                if not in_floors(pl, close_args):  # type: ignore[operator]
                    pl.MCPStanceCloseServer(pl.MCPConnGenValue())
            self.assertEqual(p1_closes, [7])
            self.assertEqual(p2_closes, [8])

        for dup_before_start2 in (True, False):
            with self.subTest(case="duplicate_cancel1_with_start2", dup_before_start2=dup_before_start2):
                pair = _StancePair(self.stance, care)
                self.assertEqual(pair.begin("prone", False), "")
                pair.deliver_rpcs()
                self.assertTrue(pair.server.close_server(pair.conn_gen))
                self.assertEqual(len(pair.rpcs), 1)
                dup_cancel_1 = (pair.rpcs[0][0], list(pair.rpcs[0][1]))
                # Deliver first cancel_1 and process its ACK on the server.
                pair.deliver_rpcs()
                pair.owner.command_handler(0.1)
                pair.deliver_iuds()
                self.assertFalse(pair.server.busy_now())
                # Admit crouch_2 and deliver duplicate cancel_1 alongside start_2.
                self.assertEqual(pair.begin("crouch", False), "")
                self.assertEqual(len(pair.rpcs), 1)
                start_2 = pair.rpcs.pop(0)
                if dup_before_start2:
                    pair.rpcs.extend([dup_cancel_1, start_2])
                else:
                    pair.rpcs.extend([start_2, dup_cancel_1])
                pair.step(0.1)
                self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_CROUCH])
                self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_CROUCH])
                pair.step(1.6)
                self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_CROUCH, -1])
                self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_CROUCH, -1])
                self.assertFalse(pair.owner.active_now())
                self.assertFalse(pair.server.active_now())
                self.assertFalse(pair.server.busy_now())

        with self.subTest(case="pending_owner_cancel_for_req1_superseded_by_start2"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            pair.step(0.1)
            # Server finishes unlock while owner is still locked; client lease_close arms CancelWanted for req 1.
            pair.server.command_handler(1.6)
            self.assertFalse(pair.server.busy_now())
            pair.owner.cancel("lease_close")
            self.assertTrue(pair.owner.ns["m_MCPStanceCancelWanted"])
            # Server admits crouch_2 and start_2 reaches owner before owner's CommandHandler runs.
            self.assertEqual(pair.begin("crouch", False), "")
            pair.deliver_rpcs()
            self.assertFalse(pair.owner.ns["m_MCPStanceCancelWanted"])
            pair.step(0.1)
            self.assertEqual(
                pair.owner.force_calls,
                [_STANCE_CONSTS.STANCEIDX_PRONE, -1, _STANCE_CONSTS.STANCEIDX_CROUCH],
            )
            self.assertEqual(
                pair.server.force_calls,
                [_STANCE_CONSTS.STANCEIDX_PRONE, -1, _STANCE_CONSTS.STANCEIDX_CROUCH],
            )
            pair.step(1.6)
            self.assertEqual(
                pair.owner.force_calls,
                [_STANCE_CONSTS.STANCEIDX_PRONE, -1, _STANCE_CONSTS.STANCEIDX_CROUCH, -1],
            )
            self.assertEqual(
                pair.server.force_calls,
                [_STANCE_CONSTS.STANCEIDX_PRONE, -1, _STANCE_CONSTS.STANCEIDX_CROUCH, -1],
            )

        with self.subTest(case="stale_cancel1_after_uncancelled_req1_and_start2"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            pair.step(0.1)
            pair.step(1.6)
            self.assertEqual(pair.owner.ns["m_MCPStanceClosedFloor"], 0)
            self.assertEqual(pair.begin("crouch", False), "")
            pair.deliver_rpcs()
            rpc_id = int(pair.server.ns["MCP_RPC_PLAYER_STANCE"])  # type: ignore[arg-type]
            pair.rpcs.append((rpc_id, [1, -1, False]))
            pair.deliver_rpcs()
            pair.step(0.1)
            pair.step(1.6)
            self.assertEqual(
                pair.owner.force_calls,
                [_STANCE_CONSTS.STANCEIDX_PRONE, -1, _STANCE_CONSTS.STANCEIDX_CROUCH, -1],
            )
            self.assertEqual(
                pair.server.force_calls,
                [_STANCE_CONSTS.STANCEIDX_PRONE, -1, _STANCE_CONSTS.STANCEIDX_CROUCH, -1],
            )

    def test_r3_p1_owner_holds_lock_until_server_apply_ack_blocking_premature_input(self) -> None:
        # Sol r3 P1: owner applies prone while server waits on busy inventory.
        # Past the 1.5 s unlock window, the owner must still retain ForceStance
        # until the server applies and sends MCP_STANCE_RPC_APPLIED; player
        # stance input before server unblock cannot desync owner=erect vs
        # server=prone.
        care = CARE.read_text(encoding="utf-8")
        pair = _StancePair(self.stance, care)
        self.assertEqual(pair.begin("prone", False), "")
        pair.server.inventory_processing = True

        pair.step(0.1)
        self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
        self.assertEqual(pair.server.force_calls, [])
        self.assertTrue(pair.owner.ns["m_MCPStanceWaitingApplyAck"])
        self.assertTrue(pair.server.ns["m_MCPStancePending"])

        # Advance well past the 1.5 s unlock window while server remains blocked.
        pair.step(2.0)
        pair.step(2.0)
        self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
        self.assertEqual(pair.owner.forced_stance, _STANCE_CONSTS.STANCEIDX_PRONE)
        self.assertTrue(pair.owner.active_now())

        # Player attempts to stand up before server unblocks: lock blocks it.
        pair.player_input_stance(_STANCE_CONSTS.STANCEIDX_ERECT)
        self.assertEqual(pair.owner.stance_idx, _STANCE_CONSTS.STANCEIDX_PRONE)

        # Unblock server: server applies prone and sends explicit apply-ack RPC.
        pair.server.inventory_processing = False
        pair.step(0.1)
        self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
        self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
        self.assertFalse(pair.owner.ns["m_MCPStanceWaitingApplyAck"])
        self.assertEqual(pair.owner.stance_idx, _STANCE_CONSTS.STANCEIDX_PRONE)
        self.assertEqual(pair.server.stance_idx, _STANCE_CONSTS.STANCEIDX_PRONE)

        # During the synchronized 1.5 s settle window, both remain locked on prone.
        pair.player_input_stance(_STANCE_CONSTS.STANCEIDX_ERECT)
        self.assertEqual(pair.owner.stance_idx, _STANCE_CONSTS.STANCEIDX_PRONE)
        self.assertEqual(pair.server.stance_idx, _STANCE_CONSTS.STANCEIDX_PRONE)

        # Settle window completes: both release ForceStance(-1) in sync.
        pair.step(1.6)
        self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
        self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
        self.assertEqual(pair.owner.forced_stance, -1)
        self.assertEqual(pair.server.forced_stance, -1)
        self.assertFalse(pair.owner.active_now())
        self.assertFalse(pair.server.active_now())
        self.assertFalse(pair.server.busy_now())

        # Subsequent player stance input moves both sides to erect together.
        pair.player_input_stance(_STANCE_CONSTS.STANCEIDX_ERECT)
        self.assertEqual(pair.owner.stance_idx, _STANCE_CONSTS.STANCEIDX_ERECT)
        self.assertEqual(pair.server.stance_idx, _STANCE_CONSTS.STANCEIDX_ERECT)

    def test_r3_p2_1_death_after_apply_clears_unlock_while_living_no_move_keeps_retry(self) -> None:
        # Sol r3 P2 #1: dying after apply (Pending=false, Unlock=true) removes
        # HumanCommandMove and must clear the unlock/close state instead of
        # leaving permanent census residue; living players temporarily without
        # HumanCommandMove must keep retrying until Move returns.
        care = CARE.read_text(encoding="utf-8")

        with self.subTest(case="death_after_apply_with_close_and_ack"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            pair.step(0.1)
            self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
            self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
            self.assertTrue(pair.owner.ns["m_MCPStanceUnlock"])
            self.assertTrue(pair.server.ns["m_MCPStanceUnlock"])

            # Both die without Move before the 1.5 s unlock runs.
            pair.owner.alive = False
            pair.owner.has_move = False
            pair.server.alive = False
            pair.server.has_move = False

            self.assertTrue(pair.server.close_server(pair.conn_gen))
            pair.owner.cancel("lease_close")
            pair.step(0.1)
            self.assertFalse(pair.server.ns["m_MCPStanceCancelOutstanding"])
            self.assertFalse(pair.owner.ns["m_MCPStanceUnlock"])
            self.assertFalse(pair.server.ns["m_MCPStanceUnlock"])
            self.assertFalse(pair.owner.active_now())
            self.assertFalse(pair.server.active_now())
            self.assertFalse(pair.owner.busy_now())
            self.assertFalse(pair.server.busy_now())

            for _ in range(50):
                pair.step(1.0)
            self.assertFalse(pair.owner.active_now())
            self.assertFalse(pair.server.active_now())

        with self.subTest(case="death_after_apply_without_close"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            pair.step(0.1)
            pair.owner.alive = False
            pair.owner.has_move = False
            pair.server.alive = False
            pair.server.has_move = False
            # Even before the next CommandHandler tick, census/busy drop dead-no-move unlock.
            self.assertFalse(pair.owner.active_now())
            self.assertFalse(pair.server.active_now())
            self.assertFalse(pair.owner.busy_now())
            self.assertFalse(pair.server.busy_now())
            pair.step(0.1)
            self.assertFalse(pair.owner.ns["m_MCPStanceUnlock"])
            self.assertFalse(pair.server.ns["m_MCPStanceUnlock"])

        with self.subTest(case="living_player_temporarily_without_move_retries_unlock"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            pair.step(0.1)
            pair.owner.has_move = False
            pair.server.has_move = False
            for _ in range(10):
                pair.step(0.5)
                self.assertTrue(pair.owner.ns["m_MCPStanceUnlock"])
                self.assertTrue(pair.server.ns["m_MCPStanceUnlock"])
                self.assertTrue(pair.owner.active_now())
                self.assertTrue(pair.server.active_now())
            pair.owner.has_move = True
            pair.server.has_move = True
            pair.step(0.1)
            self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
            self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
            self.assertFalse(pair.owner.ns["m_MCPStanceUnlock"])
            self.assertFalse(pair.server.ns["m_MCPStanceUnlock"])
            self.assertFalse(pair.owner.active_now())
            self.assertFalse(pair.server.active_now())

    def test_r3_p2_2_old_pbo_owner_degrades_gracefully_after_no_owner_ack_without_reconnect(self) -> None:
        # Sol r3 P2 #2: an old-PBO owner processes neither start nor cancel RPCs.
        # After no_owner_ack, CancelOutstanding must bound-expire (no_cancel_ack)
        # so the server settles cleanly and subsequent requests do not stay stuck
        # returning busy until reconnect.
        care = CARE.read_text(encoding="utf-8")

        with self.subTest(case="no_owner_ack_settles_without_reconnect"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            self.assertTrue(pair.server.busy_now())
            # Simulate an old-PBO owner: never call deliver_rpcs or owner.command_handler.
            for _ in range(130):
                pair.server.command_handler(0.5)
            self.assertTrue(any("reason=no_owner_ack" in line for line in pair.server.logs))
            self.assertTrue(any("reason=no_cancel_ack" in line for line in pair.server.logs))
            self.assertFalse(pair.server.ns["m_MCPStanceCancelOutstanding"])
            self.assertFalse(pair.server.active_now())
            self.assertFalse(pair.server.busy_now())

            # After 65 s with no reconnect, the next request is not stuck on busy.
            pair.rpcs.clear()
            self.assertEqual(pair.begin("crouch", False), "")
            # If the owner now processes RPCs (e.g. transient stall cleared), it applies cleanly.
            pair.step(0.1)
            self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_CROUCH])
            self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_CROUCH])
            pair.step(1.6)
            self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_CROUCH, -1])
            self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_CROUCH, -1])
            self.assertFalse(pair.server.busy_now())

        with self.subTest(case="lease_close_with_old_pbo_owner_settles_and_fences_late_iud"):
            pair = _StancePair(self.stance, care)
            self.assertEqual(pair.begin("prone", False), "")
            pair.rpcs.clear()
            self.assertTrue(pair.server.close_server(pair.conn_gen))
            self.assertTrue(pair.server.ns["m_MCPStanceCancelOutstanding"])
            pair.rpcs.clear()
            for _ in range(4):
                pair.server.command_handler(0.5)
            self.assertFalse(pair.server.ns["m_MCPStanceCancelOutstanding"])
            self.assertFalse(pair.server.active_now())
            self.assertFalse(pair.server.busy_now())
            # A subsequent lease_close does not re-arm CancelOutstanding for the already-expired id.
            self.assertTrue(pair.server.close_server(pair.conn_gen))
            self.assertFalse(pair.server.ns["m_MCPStanceCancelOutstanding"])
            self.assertFalse(pair.server.active_now())

    def test_r4_p1_cancel_ack_timeout_keeps_late_owner_applied_start(self) -> None:
        # Sol @2b3fa813 P1 (M-1): owner applies prone, server closes the lease,
        # then the owner's start IUD and its cancel-applied IUD are delayed (in
        # order) past MCP_STANCE_CANCEL_TIMEOUT_S. no_cancel_ack must not be
        # treated as "owner did not apply": the late matching start is accepted
        # (applied_after_close), the server applies prone, and both sides end
        # with ForceStance=[prone, -1].
        care = CARE.read_text(encoding="utf-8")
        for delay_s in (0.5, 2.0):
            with self.subTest(delay_s=delay_s):
                pair = _StancePair(self.stance, care)
                cancel_timeout = float(pair.server.ns["MCP_STANCE_CANCEL_TIMEOUT_S"])  # type: ignore[arg-type]
                cancel_applied = pair.server.ns["MCP_STANCE_IUD_CANCEL_APPLIED"]
                self.assertEqual(pair.begin("prone", False), "")
                pair.deliver_rpcs()
                pair.owner.command_handler(0.1)
                self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                self.assertEqual(len(pair.iuds), 1)

                self.assertTrue(pair.server.close_server(pair.conn_gen))
                self.assertTrue(pair.server.ns["m_MCPStanceCancelOutstanding"])

                # Hold the start IUD; owner processes the cancel RPC and emits
                # cancel-applied, which is held behind the start (order kept).
                delayed: list[list[object]] = list(pair.iuds)
                pair.iuds.clear()
                pair.deliver_rpcs()
                pair.owner.command_handler(0.1)
                delayed.extend(pair.iuds)
                pair.iuds.clear()
                self.assertEqual(len(delayed), 2)
                self.assertEqual(delayed[0][-1], _STANCE_CONSTS.STANCEIDX_PRONE)
                self.assertEqual(delayed[1][-1], cancel_applied)

                elapsed = 0.0
                while elapsed < delay_s:
                    pair.step(0.25)
                    elapsed += 0.25
                    self.assertEqual(pair.server.force_calls, [])
                    self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                    self.assertTrue(pair.owner.active_now())
                timed_out = any("reason=no_cancel_ack" in line for line in pair.server.logs)
                self.assertEqual(timed_out, delay_s > cancel_timeout)

                pair.iuds.extend(delayed)
                pair.step(0.1)
                self.assertTrue(any("reason=applied_after_close" in line for line in pair.server.logs))
                self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE])
                pair.step(0.1)
                pair.step(0.1)
                self.assertEqual(pair.owner.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
                self.assertEqual(pair.server.force_calls, [_STANCE_CONSTS.STANCEIDX_PRONE, -1])
                self.assertFalse(pair.owner.active_now())
                self.assertFalse(pair.server.active_now())
                self.assertFalse(pair.server.busy_now())

    def test_enforce_script_no_ternary_and_locals_at_top(self) -> None:
        stripped = _without_comments(self.stance)
        no_strings = re.sub(r'"(?:\\.|[^"\\])*"', '""', stripped)
        self.assertNotIn("?", no_strings)
        local_decl = re.compile(
            r"^\s*(?:string|int|float|bool|PlayerIdentity|EntityAI|HumanCommandMove|"
            r"ScriptRPC|ScriptInputUserData|MCPLeaseCloseFloor)\s+(\w+)\s*(?:=[^;]*)?;\s*$"
        )
        bridge_src = BRIDGE.read_text(encoding="utf-8")
        checked: list[tuple[str, str]] = [(self.stance, sig) for sig in _STANCE_SIGNATURES] + [
            (bridge_src, "protected bool IsPlayerInCloseFloors(PlayerBase player, MCPArgs closeArgs)")
        ]
        for src_text, sig in checked:
            with self.subTest(signature=sig):
                body = _enforce_method_body(src_text, sig)
                self.assertNotIn("?", re.sub(r'"(?:\\.|[^"\\])*"', '""', _without_comments(body)))
                seen_stmt = False
                names: set[str] = set()
                for raw in _without_comments(body).splitlines():
                    line = raw.strip()
                    if not line or line in ("{", "}"):
                        continue
                    m = local_decl.match(raw)
                    if m is None:
                        seen_stmt = True
                        continue
                    self.assertFalse(seen_stmt, f"local declared after statement in {sig}: {line}")
                    self.assertNotIn(m.group(1), names, f"duplicate local in {sig}: {line}")
                    names.add(m.group(1))


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
        messages = MESSAGES.read_text(encoding="utf-8")
        stance_msg_block = messages[
            messages.index("// player_set_stance reply.") : messages.index("class MCPPlayerKill")
        ]
        self.assertNotIn("CallLater", stance_msg_block)
        self.assertIn("CommandHandler", stance_msg_block)


if __name__ == "__main__":
    unittest.main()
