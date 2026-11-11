"""33c5, c616, cc75: player_look_at's server copy, sent the way player_move sends its move.

In the 9/11 in-game round the owner's ONE_FRAME aim pulses turned the client
only: MCPLookAtControl returns on the dedicated server and nothing told the
server about the look. players_visibility's in_fov then followed a body
heading that never turned (33c5), and every look of 90 degrees or more that
ended in deadline (c616, cc75) had `NwkMovement Process Correction` lines
inside its window: the server's reconciliation pulled the camera back to the
server's heading. The owner now sends a ScriptInputUserData start
(MCP_INPUT_UDT_PLAYER_LOOK) and, when the look ends without converging, its
release; the server pulses its own aim from its own heading and position
until within BAND_DEG, the release or its deadman, as player_move's copy does
(test_player_move.py). These tests pin the wire, the guards and the server
copy's behaviour on the translated bodies. They do not launch DayZ: the
in-game check is a look at +90 degrees whose players_visibility row reads
in_fov 1 and heading_deg the target's bearing, with no corrections.
"""
from __future__ import annotations

import math
import re
import unittest
from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import method_body, translate
from tests.player_move_helpers import (
    INSTANCE_TYPES,
    _LogString,
    _block_after,
    _flat,
    _free_function,
    _if_body,
    _in_order,
    _locals_declared_at_top,
    _method_body,
    _without_comments,
)

SCRIPTS = addon_root() / "scripts"
LOOK_PATH = SCRIPTS / "4_World" / "MCP_LookAt.c"
MOVE_PATH = SCRIPTS / "4_World" / "MCP_PlayerMove.c"
WEAPON_PATH = SCRIPTS / "4_World" / "MCP_Weapon.c"
CLIENT_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"

UDT = "MCP_INPUT_UDT_PLAYER_LOOK"
BEGIN = "static string Begin(PlayerBase player, vector target, float budgetS, int commandId)"
RESET = "static void Reset()"
RELEASE = "static void Release(string why)"
RELEASE_OWNED = "static void ReleaseOwned(int commandId, string why)"
FINISH = "static void Finish(string termination, bool converged)"
END_SERVER = "static void EndServerLook(bool converged)"
SEND_START = "static string SendServerStart(int lookId, vector target, float budgetS, int turnSign)"
FLUSH = "static void FlushServerRelease()"
REQUEST_REFUSAL = "static string ServerRequestRefusal(vector target, float budgetS)"
WITHIN_BAND = "static bool WithinBand(float errorDeg)"
PULSE_X = "static float ServerPulseX(float errorDeg)"
CLAMP = "protected static float ClampPulse(float value)"
ON_COMMAND = "static void OnCommandHandler(PlayerBase player, float pDt)"
SERVER_INPUT = "override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)"
SERVER_READ = "protected void MCPLookReadRequest(ParamsReadContext ctx)"
SERVER_READ_RELEASE = "protected void MCPLookReadRelease(int lookId)"
SERVER_ACCEPT = "protected void MCPLookAccept(int lookId, vector target, float budgetS, int turnSign)"
SERVER_FRESHNESS = "protected string MCPLookFreshnessRefusal(int lookId)"
SERVER_OWNER = "protected string MCPLookOwnerRefusal()"
SERVER_TICK = "protected void MCPLookServerTick(bool fromConsume)"
SERVER_RELEASE = "protected void MCPLookServerRelease(string why)"
SERVER_VERDICT = "protected void MCPLookVerdict(bool accepted, int lookId, string reason)"
SERVER_NOTE = "protected void MCPLookNote(int lookId, string reason)"
SERVER_RECONNECT = "override void OnReconnect()"
COMMAND_HANDLER = "override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)"
CONSUME = "protected override event void ConsumeMove(PawnMove pMove)"
DISPATCH = "protected bool DispatchPlayerLookAt(MCPCommand command, MCPResult result)"
DISPATCH_RELEASE = "protected bool DispatchPlayerLookAtRelease(MCPCommand command, MCPResult result)"
COMPASS = "static float CompassFromHeading(float headingRad)"
BEARING = "static float BearingDeg(vector from, vector target)"
WRAP = "static float WrapDeg180(float angle)"
TIE_BREAK = "static float TieBreakDeg(float errorDeg)"
TURN_SIGN_FOR = "static int TurnSignFor(float yawDeg)"
START_TURN = "protected static int StartTurnSign(PlayerBase player, vector target)"
LATCH = "static float LatchDeg(float errorDeg, int turnSign)"
TURN_REFUSAL = "static string TurnRefusal(int turnSign)"
SERVER_ALLOWED = "static bool ServerAllowed()"
SET_SERVER_ALLOWED = "static void SetServerAllowed(bool allowed)"
RELEASE_SERVER_COPY = "static void ReleaseServerCopy()"
MISSION_SERVER_PATH = SCRIPTS / "5_Mission" / "MissionServer.c"
MOVE_ACCEPT = "protected void MCPMoveAccept(MCPPlayerMoveRequest request)"
WEAPON_AIM = "protected bool DispatchWeaponAim(MCPCommand command, MCPResult result)"

SERVER_MEMBERS = (
    "m_MCPLookId|m_MCPLookTarget|m_MCPLookDeadlineS|m_MCPLookPulsed|m_MCPLookConsumed|"
    "m_MCPLookHandled|m_MCPLookLastId|m_MCPLookLastStartS|m_MCPLookLastRefusal|m_MCPLookTurn|MCPLookVerdict|MCPLookNote|MCPLookServerRelease|"
    "MCPLookFreshnessRefusal|MCPLookOwnerRefusal|MCPLookReadRelease|MCPLookAccept|"
    "MCPLookServerTick|GetInstanceType|GetIdentity|IsPlayerDisconnected|GetInputController|"
    "PhysicsGetPositionWS"
)
SERVER_METHODS = (
    SERVER_VERDICT, SERVER_NOTE, SERVER_RELEASE, SERVER_FRESHNESS, SERVER_OWNER,
    SERVER_READ_RELEASE, SERVER_ACCEPT, SERVER_TICK, SERVER_RECONNECT,
)
OWNER_METHODS = (RESET, BEGIN, RELEASE, RELEASE_OWNED, FINISH, END_SERVER, SEND_START, FLUSH)


def _source(path) -> str:
    return path.read_text(encoding="utf-8")


def _constants(source: str) -> dict[str, float]:
    return {
        name: float(value)
        for name, value in re.findall(r"static const (?:float|int) (\w+) = ([0-9.]+);", source)
    }


class _EnforceMath:
    RAD2DEG = 180.0 / math.pi
    DEG2RAD = math.pi / 180.0
    Atan2 = staticmethod(math.atan2)


def _bearing_source(move: str) -> tuple[str, str]:
    """BearingDeg with its parameter `from`, a Python keyword, renamed; the
    body is otherwise the real one."""
    signature = "static float BearingDeg(vector origin, vector target)"
    body = re.sub(r"\bfrom\b", "origin", method_body(move, BEARING))
    return signature + "\n{" + body + "}\n", signature


def _wire_type(source: str) -> int:
    return int(re.search(r"const int MCP_INPUT_UDT_PLAYER_LOOK = (\d+);", source).group(1))


class _Channel:
    """ScriptInputUserData as a fake: what was sent, and whether the channel takes more."""

    def __init__(self) -> None:
        self.sent: list[list[object]] = []
        self.free = True
        channel = self

        class InputUserData:
            def __init__(self) -> None:
                self.values: list[object] = []

            @staticmethod
            def CanStoreInputUserData() -> bool:
                return channel.free

            def Write(self, value: object) -> None:
                self.values.append(value)

            def Send(self) -> None:
                channel.sent.append(self.values)

        self.InputUserData = InputUserData


class _OwnerModel:
    """MCPLookAtControl's Begin, Release, ReleaseOwned, Finish and the server
    messages, translated over one namespace, the class statics read through S,
    and the bridge's cancel (DispatchPlayerLookAtRelease) calling them."""

    def __init__(self, source: str) -> None:
        self.channel = _Channel()
        self.multiplayer = True
        self.local_player = True
        self.disabled = 0
        self.turn = 1
        self.now = 10.0
        statics = {}
        defaults = {"bool": False, "int": 0, "float": 0.0, "vector": None, "PlayerBase": None}
        for kind, name in re.findall(r"protected static (\w+) (s_\w+);", source):
            statics[name] = _LogString.of("") if kind == "string" else defaults[kind]
        self.state = SimpleNamespace(**statics)
        game = SimpleNamespace(
            IsMultiplayer=lambda: self.multiplayer,
            IsClient=lambda: True,
            GetPlayer=lambda: object() if self.local_player else None,
            GetTickTime=lambda: self.now,
        )
        self.namespace: dict[str, object] = {
            "_S": _LogString,
            "S": self.state,
            "GetGame": lambda: game,
            "ScriptInputUserData": self.channel.InputUserData,
            UDT: _wire_type(source),
            "Disable": self._disable,
            # S-1: the owner's camera read at Begin is a fake here: the way
            # it answers (LookMirrorLatchTest runs the real TurnSignFor).
            "StartTurnSign": lambda _player, _target: self.turn,
            **_constants(source),
        }
        for signature in OWNER_METHODS + ((RELEASE_SERVER_COPY,) if RELEASE_SERVER_COPY in source else ()):
            free = _free_function(source, signature, r"s_\w+", "S")
            translate(free, signature, self.namespace, class_types={"PlayerBase", "ScriptInputUserData"})
        self.namespace["MCPLookAtControl"] = SimpleNamespace(ReleaseOwned=self.namespace["ReleaseOwned"])
        translate(_source(CLIENT_BRIDGE_PATH), DISPATCH_RELEASE, self.namespace)
        self.udt = _wire_type(source)

    def _disable(self, _player: object) -> None:
        self.disabled += 1

    def begin(self, target=(10.0, 0.0, 0.0), budget: float = 3.0) -> int:
        """The look's id, which the dispatch reads from Generation() once Begin
        answers ""; 0 when Begin refuses, its code then in self.refusal."""
        self.refusal = self.namespace["Begin"](object(), target, budget, 7).text
        look = 0 if self.refusal else self.state.s_Generation
        # The clock moves on by the owner's minimum start interval, so the
        # tests' successive looks are not refused; LookMirrorStartIntervalTest
        # sets the clock itself.
        self.now += 0.5
        return look

    def finish(self, termination: str, converged: bool) -> None:
        self.namespace["Finish"](_LogString.of(termination), converged)

    def release(self, why: str) -> None:
        self.namespace["Release"](_LogString.of(why))

    def cancel(self, command_id: int = 7) -> SimpleNamespace:
        """The broker's player_look_at_release for `command_id`, through the
        bridge's dispatch; the result it answers."""
        result = SimpleNamespace(ok=False, error=None)
        command = SimpleNamespace(args=SimpleNamespace(command_id=command_id))
        self.answered = self.namespace["DispatchPlayerLookAtRelease"](command, result)
        return result

    def flush(self) -> None:
        self.namespace["FlushServerRelease"]()


class _ServerPlayer:
    """The server instance of one PlayerBase: the look members and the engine calls the copy makes.

    A ONE_FRAME aim pulse of v radians turns the compass heading by v (positive
    X to the right), as measured for the owner on 2026-10-08; turns=False keeps
    the heading where it is, as the round's server did."""

    def __init__(self) -> None:
        self.m_MCPLookId = 0
        self.m_MCPLookTarget = None
        self.m_MCPLookDeadlineS = 0.0
        self.m_MCPLookPulsed = False
        self.m_MCPLookConsumed = 0
        self.m_MCPLookHandled = 0
        self.m_MCPLookLastId = 0
        self.m_MCPLookLastStartS = -1.0
        self.m_MCPLookLastRefusal = _LogString.of("")
        self.m_MCPLookTurn = 0
        self.identity: object | None = object()
        self.disconnected = False
        self.alive = True
        self.position = (0.0, 0.0, 0.0)
        self.heading_rad = 0.0
        self.turns = True
        self.overrides: list[tuple[str, float]] = []
        player = self

        class Controller:
            @staticmethod
            def GetHeadingAngle() -> float:
                return player.heading_rad

            @staticmethod
            def OverrideAimChangeX(kind: str, value: float) -> None:
                player.overrides.append((kind, value))
                if kind == "ONE_FRAME" and player.turns:
                    # GetHeadingAngle is -compass (MCP_PlayerMove.c CompassFromHeading).
                    player.heading_rad -= value

        self.controller = Controller()

    def GetInstanceType(self) -> int:
        return INSTANCE_TYPES.INSTANCETYPE_SERVER

    def GetIdentity(self) -> object | None:
        return self.identity

    def IsPlayerDisconnected(self) -> bool:
        return self.disconnected

    def GetInputController(self) -> object:
        return self.controller

    def PhysicsGetPositionWS(self) -> tuple[float, float, float]:
        return self.position

    @property
    def compass_deg(self) -> float:
        return -math.degrees(self.heading_rad)


class _ServerLookModel:
    """The server copy's methods of MCP_LookAt.c, translated and run on one fake
    player. The angle helpers (CompassFromHeading, BearingDeg, WrapDeg180) and
    the look's bounds, band and pulse are the real ones; ActorRefusal is a fake
    that answers player_dead for a dead body."""

    def __init__(self, source: str) -> None:
        move = _source(MOVE_PATH)
        self.now = 100.0
        self.log: list[str] = []
        self.supers: list[str] = []
        self.allowed = True
        self.player = _ServerPlayer()
        helpers: dict[str, object] = {"Math": _EnforceMath}
        for signature in (COMPASS, WRAP):
            translate(move, signature, helpers)
        translate(*_bearing_source(move), helpers)
        move_control = SimpleNamespace(
            ActorRefusal=lambda player: _LogString.of("" if player.alive else "player_dead"),
            IsFiniteValue=math.isfinite,
            IsFiniteVector=lambda value: all(math.isfinite(axis) for axis in value),
            CompassFromHeading=helpers["CompassFromHeading"],
            BearingDeg=helpers["BearingDeg"],
            WrapDeg180=helpers["WrapDeg180"],
        )
        look: dict[str, object] = {
            "_S": _LogString,
            "Math": _EnforceMath,
            "MCPPlayerMoveControl": move_control,
            **_constants(source),
        }
        for signature in (CLAMP, REQUEST_REFUSAL, WITHIN_BAND, PULSE_X):
            translate(source, signature, look)
        # #363 follow-up: the 180-degree tie-break (S2) and the server's gate
        # (L2), a fake the test sets; the gate's real statics are pinned in
        # LookMirrorGateTest. Before the fix the source has neither.
        for signature in (TIE_BREAK, TURN_SIGN_FOR, LATCH, TURN_REFUSAL):
            if signature in source:
                translate(source, signature, look)
        look["ServerAllowed"] = lambda: self.allowed

        class Statics:
            def __getattr__(self, name: str) -> object:
                return look[name]

        namespace: dict[str, object] = {
            "_S": _LogString,
            "ToStr": lambda value: _LogString.of(str(value)),
            "this": self.player,
            "super": SimpleNamespace(OnReconnect=lambda: self.supers.append("OnReconnect")),
            "GetGame": lambda: SimpleNamespace(GetTickTime=lambda: self.now),
            "Print": lambda line: self.log.append(line.text),
            "DayZPlayerInstanceType": INSTANCE_TYPES,
            "HumanInputControllerOverrideType": SimpleNamespace(ONE_FRAME="ONE_FRAME", DISABLED="DISABLED"),
            "MCPPlayerMoveControl": move_control,
            "MCPLookAtControl": Statics(),
        }
        for signature in SERVER_METHODS:
            name = signature[: signature.index("(")].split()[-1]
            free = _free_function(source, signature, SERVER_MEMBERS, "this")
            setattr(self.player, name, translate(free, signature, namespace, class_types={"HumanInputController"}))
        self.look = look

    def start(self, look_id: int, target=(10.0, 0.0, 0.0), budget: float = 3.0, turn: int = 0) -> None:
        """One start, then the clock moves on by the server's minimum start
        interval, so successive starts in a test are never throttled unless
        the test sets the clock itself."""
        self.player.MCPLookAccept(look_id, target, budget, turn)
        self.now += 0.25

    def release(self, look_id: int) -> None:
        self.player.MCPLookReadRelease(look_id)

    def tick(self, from_consume: bool = True) -> None:
        self.player.MCPLookServerTick(from_consume)

    @property
    def active(self) -> int | None:
        return self.player.m_MCPLookId or None


def _verdict(accepted: int, look_id: int, reason: str) -> str:
    return f"[DayZ_MCP] player_look_at server accepted={accepted} look={look_id} reason={reason}"


def _released(look_id: int, why: str, consumed: int = 0, handled: int = 0) -> str:
    return (
        f"[DayZ_MCP] player_look_at server released look={look_id} reason={why} "
        f"consumed={consumed} handled={handled}"
    )


class LookMirrorWireTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(LOOK_PATH)
        self.code = _without_comments(self.source)

    def test_the_input_type_is_the_next_free_id_and_unique(self) -> None:
        self.assertIn("const int MCP_INPUT_UDT_PLAYER_LOOK = 78541068;", self.source)
        for path, taken in (
            (WEAPON_PATH, "const int MCP_INPUT_UDT_WEAPON_FIRE = 78541064;"),
            (WEAPON_PATH, "const int MCP_INPUT_UDT_WEAPON_RAISE = 78541065;"),
            (MOVE_PATH, "const int MCP_INPUT_UDT_PLAYER_MOVE = 78541066;"),
            (WEAPON_PATH, "const int MCP_RPC_HANDS_ATTACH = 78541067;"),
        ):
            with self.subTest(taken=taken):
                self.assertIn(taken, _source(path))
        values = []
        for path in sorted(SCRIPTS.rglob("*.c")):
            values += [int(v) for v in re.findall(r"(?m)^\s*const int \w+ = (-?\d+);", _source(path))]
        self.assertEqual(values.count(78541068), 1)
        self.assertNotIn("78541091", "".join(_source(path) for path in SCRIPTS.rglob("*.c")))

    def test_the_start_is_sent_with_the_move_guards_and_read_in_the_same_order(self) -> None:
        send = _method_body(self.source, SEND_START)
        _in_order(
            self,
            send,
            "if (!GetGame().IsMultiplayer())",
            "if (!GetGame().IsClient())",
            "FlushServerRelease();",
            "if (s_ServerReleasePending)",
            "if (!ScriptInputUserData.CanStoreInputUserData())",
            "message = new ScriptInputUserData();",
            "message.Write(MCP_INPUT_UDT_PLAYER_LOOK);",
            "message.Write(true);",
            "message.Write(lookId);",
            "message.Write(target);",
            "message.Write(budgetS);",
            "message.Write(turnSign);",
            "message.Send();",
            'return "sent";',
        )
        self.assertEqual(_if_body(send, "!GetGame().IsMultiplayer()").strip(), 'return "unavailable";')
        self.assertEqual(_if_body(send, "!GetGame().IsClient()").strip(), 'return "unavailable";')
        self.assertEqual(_if_body(send, "s_ServerReleasePending").strip(), 'return "input_busy";')
        self.assertEqual(_if_body(send, "!ScriptInputUserData.CanStoreInputUserData()").strip(), 'return "input_busy";')
        self.assertNotIn("s_ServerReleasePending = false;", send)
        read = _method_body(self.source, SERVER_READ)
        _in_order(
            self,
            read,
            "if (!ctx.Read(isStart))",
            "if (!ctx.Read(lookId))",
            "if (!isStart)",
            "MCPLookReadRelease(lookId);",
            "if (!ctx.Read(target))",
            "if (!ctx.Read(budgetS))",
            "if (!ctx.Read(turnSign))",
            "MCPLookAccept(lookId, target, budgetS, turnSign);",
        )
        for local in ("bool isStart;", "int lookId;", "vector target;", "float budgetS;", "int turnSign;"):
            with self.subTest(local=local):
                self.assertIn(f"\t\t{local}\n", read)

    def test_the_release_names_its_look_and_waits_for_a_free_channel(self) -> None:
        flush = _method_body(self.source, FLUSH)
        _in_order(
            self,
            flush,
            "if (!s_ServerReleasePending)",
            "if (!GetGame().IsMultiplayer() || !GetGame().IsClient())",
            "if (!GetGame().GetPlayer())",
            "if (!ScriptInputUserData.CanStoreInputUserData())",
            "message.Write(MCP_INPUT_UDT_PLAYER_LOOK);",
            "message.Write(false);",
            "message.Write(s_ServerReleaseId);",
            "message.Send();",
        )
        self.assertTrue(flush.rstrip().endswith("message.Send();\n\t\ts_ServerReleasePending = false;"))
        self.assertEqual(_if_body(flush, "!GetGame().GetPlayer()").strip(), "return;")
        self.assertEqual(_if_body(flush, "!ScriptInputUserData.CanStoreInputUserData()").strip(), "return;")
        release = _method_body(self.source, SERVER_READ_RELEASE)
        _in_order(self, release, "if (lookId > m_MCPLookLastId)", "if (m_MCPLookId <= 0)",
                  "if (m_MCPLookId != lookId)", 'MCPLookServerRelease("client");')
        self.assertIn('MCPLookNote(lookId, "release_without_look");', _if_body(release, "m_MCPLookId <= 0"))
        self.assertIn('MCPLookNote(lookId, "release_of_another_look");', _if_body(release, "m_MCPLookId != lookId"))

    def test_the_server_consumes_its_type_and_stops_there(self) -> None:
        process = _method_body(self.source, SERVER_INPUT)
        self.assertEqual(_flat(_if_body(process, f"userDataType == {UDT}")), "MCPLookReadRequest(ctx); return true;")
        self.assertTrue(process.rstrip().endswith("return super.OnInputUserDataProcess(userDataType, ctx);"))

    def test_the_server_rechecks_on_its_own_copy_before_it_accepts(self) -> None:
        accept = _method_body(self.source, SERVER_ACCEPT)
        _in_order(
            self,
            accept,
            "if (GetInstanceType() != DayZPlayerInstanceType.INSTANCETYPE_SERVER)",
            "if (!GetGame())",
            "if (!MCPLookAtControl.ServerAllowed())",
            "nowS = GetGame().GetTickTime();",
            "refusal = MCPLookFreshnessRefusal(lookId);",
            "m_MCPLookLastId = lookId;",
            "if (m_MCPLookLastStartS >= 0.0 && nowS - m_MCPLookLastStartS < MCPLookAtControl.SERVER_MIN_START_INTERVAL_S)",
            'refusal = "throttled";',
            "m_MCPLookLastStartS = nowS;",
            "refusal = MCPLookOwnerRefusal();",
            "refusal = MCPLookAtControl.ServerRequestRefusal(target, budgetS);",
            "refusal = MCPLookAtControl.TurnRefusal(turnSign);",
            "refusal = MCPPlayerMoveControl.ActorRefusal(this);",
            'if (refusal != "")',
            'MCPLookServerRelease("superseded");',
            "m_MCPLookId = lookId;",
            "m_MCPLookTarget = target;",
            "m_MCPLookTurn = turnSign;",
            "m_MCPLookDeadlineS = nowS + budgetS + MCPLookAtControl.SERVER_DEADMAN_MARGIN_S;",
            "m_MCPLookPulsed = false;",
            'MCPLookVerdict(true, lookId, "");',
        )
        gate = _without_comments(accept[: accept.index('if (refusal != "")')])
        self.assertNotIn("MCPLookServerRelease(", gate)
        self.assertEqual(
            re.findall(r"(\w+) = [^=]", gate),
            ["nowS", "refusal", "m_MCPLookLastId", "refusal", "m_MCPLookLastStartS"] + ["refusal"] * 4,
        )
        self.assertEqual(
            _flat(_if_body(accept, "!MCPLookAtControl.ServerAllowed()")),
            'MCPLookVerdict(false, lookId, "not_allowed"); return;',
        )
        # Accepting pulses nothing: the ticks do.
        self.assertNotIn("OverrideAim", accept)

    def test_every_server_tick_checks_its_end_then_pulses_from_its_own_heading(self) -> None:
        tick = _method_body(self.source, SERVER_TICK)
        _in_order(
            self,
            tick,
            "if (m_MCPLookId <= 0)",
            "refusal = MCPLookOwnerRefusal();",
            "refusal = MCPPlayerMoveControl.ActorRefusal(this);",
            "MCPLookServerRelease(refusal);",
            "if (GetGame().GetTickTime() > m_MCPLookDeadlineS)",
            "hic = GetInputController();",
            "compass = MCPPlayerMoveControl.CompassFromHeading(hic.GetHeadingAngle());",
            "bearing = MCPPlayerMoveControl.BearingDeg(PhysicsGetPositionWS(), m_MCPLookTarget);",
            "errorDeg = MCPLookAtControl.LatchDeg(MCPPlayerMoveControl.WrapDeg180(bearing - compass), m_MCPLookTurn);",
            "if (MCPLookAtControl.WithinBand(errorDeg))",
            'MCPLookServerRelease("converged");',
            "hic.OverrideAimChangeX(HumanInputControllerOverrideType.ONE_FRAME, MCPLookAtControl.ServerPulseX(errorDeg));",
            "m_MCPLookPulsed = true;",
        )
        self.assertEqual(_flat(_if_body(tick, 'refusal != ""')), "MCPLookServerRelease(refusal); return;")
        self.assertIn('MCPLookServerRelease("expired");', _if_body(tick, "GetGame().GetTickTime() > m_MCPLookDeadlineS"))
        # Yaw only: the server has no camera to aim a pitch from.
        self.assertNotIn("OverrideAimChangeY", tick)
        # The pulse is the owner's: GAIN of the error, clamped to MAX_PULSE, in radians.
        self.assertEqual(_method_body(self.source, PULSE_X).strip(), "return ClampPulse(GAIN * errorDeg * Math.DEG2RAD);")

    def test_the_server_disables_only_what_it_pulsed_and_logs_once(self) -> None:
        release = _method_body(self.source, SERVER_RELEASE)
        self.assertEqual(_if_body(release, "m_MCPLookId <= 0").strip(), "return;")
        _in_order(
            self,
            release,
            "lookId = m_MCPLookId;",
            "m_MCPLookId = 0;",
            "if (m_MCPLookPulsed)",
            "hic.OverrideAimChangeX(HumanInputControllerOverrideType.DISABLED, 0.0);",
            "m_MCPLookPulsed = false;",
            '"[DayZ_MCP] player_look_at server released look=" + lookId.ToString() + " reason=" + why;',
            "Print(line);",
        )

    def test_the_hooks_run_after_super_on_both_ticks_of_the_server(self) -> None:
        handler = _method_body(self.source, COMMAND_HANDLER)
        _in_order(self, handler, "super.CommandHandler(pDt, pCurrentCommandID, pCurrentCommandFinished);",
                  "if (m_MCPLookId > 0)", "MCPLookServerTick(false);")
        start = self.code.index(CONSUME)
        guard = self.code.rindex("#ifdef FEATURE_NETWORK_RECONCILIATION", 0, start)
        self.assertNotIn("#endif", self.code[guard:start])
        self.assertIn("#endif", self.code[start:])
        _in_order(self, _method_body(self.source, CONSUME), "super.ConsumeMove(pMove);",
                  "if (m_MCPLookId > 0)", "MCPLookServerTick(true);")
        self.assertEqual(
            _flat(_method_body(self.source, SERVER_RECONNECT)),
            'super.OnReconnect(); MCPLookServerRelease("reconnect"); m_MCPLookLastId = 0;',
        )

    def test_the_new_modded_player_base_keeps_entity_refs_out(self) -> None:
        self.assertEqual(self.code.count("modded class PlayerBase"), 1)
        player = self.code[self.code.index("modded class PlayerBase") :]
        self.assertNotIn("ref ", player)
        self.assertNotIn("MCPResult", self.code)
        # Methods of the other files' modded PlayerBase are not called from here.
        self.assertNotIn("MCPMove", player.replace("MCPPlayerMoveControl", ""))

    def test_the_owner_sends_on_begin_and_ends_on_every_end(self) -> None:
        begin = _method_body(self.source, BEGIN)
        _in_order(self, begin, "lookId = s_Generation + 1;",
                  "nowS = GetGame().GetTickTime();",
                  "if (s_StartSent && nowS - s_StartSentS < OWNER_MIN_START_INTERVAL_S)",
                  "turn = StartTurnSign(player, target);",
                  "serverStart = SendServerStart(lookId, target, budget, turn);",
                  'if (serverStart == "input_busy")', 'Release("replaced");', "Reset();",
                  "s_Active = true;", "s_CommandId = commandId;", "s_TurnSign = turn;", "s_ServerLookId = lookId;",
                  'return "";')
        self.assertEqual(_flat(_if_body(begin, 'serverStart == "input_busy"')), "return serverStart;")
        # The start goes first: nothing of the look changes before it is sent
        # or known to be unavailable.
        head = _without_comments(begin[: begin.index('if (serverStart == "input_busy")')])
        self.assertIsNone(re.search(r"\bs_\w+ = ", head), head)
        self.assertNotIn("Release(", head)
        end = _method_body(self.source, END_SERVER)
        _in_order(self, end, "if (s_ServerLookId <= 0)", "if (converged)", "s_ServerReleasePending = true;",
                  "s_ServerReleaseId = s_ServerLookId;", "s_ServerLookId = 0;", "FlushServerRelease();")
        # A converged look keeps its id, so a later Release still ends its copy.
        self.assertEqual(_if_body(end, "converged").strip(), "return;")
        _in_order(self, _method_body(self.source, RELEASE), "Disable(s_Player);", "EndServerLook(false);",
                  "if (s_Active && !s_Finished)")
        _in_order(self, _method_body(self.source, FINISH), "Disable(s_Player);", "EndServerLook(converged);",
                  "s_Finished = true;")
        # A release that could not leave is retried on every command tick of the
        # local player, before the look's own early return.
        _in_order(self, _method_body(self.source, ON_COMMAND), "FlushServerRelease();",
                  "if (!s_Active || s_Finished)")
        # The local player's own look is driven from MCP_Weapon.c only.
        self.assertEqual(_source(WEAPON_PATH).count("MCPLookAtControl.OnCommandHandler(live, pDt);"), 1)
        self.assertNotIn("MCPLookAtControl.OnCommandHandler(", self.code)

    def test_the_dispatch_answers_a_refused_begin_before_any_job(self) -> None:
        dispatch = _method_body(_source(CLIENT_BRIDGE_PATH), DISPATCH)
        tail = dispatch[dispatch.index("refusal = MCPLookAtControl.Begin(player, target, budget, command.id);") :]
        _in_order(self, tail, 'if (refusal != "")', "generation = MCPLookAtControl.Generation();",
                  "job = new MCPJob();", "job.generation = generation;", "m_JobRunner.AddJob(job);")
        self.assertEqual(
            _flat(_if_body(tail, 'refusal != ""')),
            "result.ok = false; result.error = refusal; return true;",
        )


    def test_compile_hazards_locals_on_top_no_local_keyword_no_ternary(self) -> None:
        every_script = "".join(_source(path) for path in SCRIPTS.rglob("*.c"))
        type_names = set(re.findall(r"\bclass\s+(\w+)", every_script))
        type_names |= {"Math", "ParamsReadContext", "PawnMove", "ScriptInputUserData", "HumanInputController"}
        for signature in (
            BEGIN, RELEASE, RELEASE_OWNED, FINISH, END_SERVER, SEND_START, FLUSH, REQUEST_REFUSAL, WITHIN_BAND,
            PULSE_X, ON_COMMAND, SERVER_INPUT, SERVER_READ, SERVER_READ_RELEASE, SERVER_ACCEPT,
            SERVER_FRESHNESS, SERVER_OWNER, SERVER_TICK, SERVER_RELEASE, SERVER_VERDICT,
            SERVER_NOTE, SERVER_RECONNECT, COMMAND_HANDLER, CONSUME, TIE_BREAK, SERVER_ALLOWED,
            SET_SERVER_ALLOWED, RELEASE_SERVER_COPY, TURN_SIGN_FOR, START_TURN, LATCH, TURN_REFUSAL,
        ):
            with self.subTest(method=signature):
                body = _method_body(self.source, signature)
                names = _locals_declared_at_top(body)
                self.assertFalse(set(names) & type_names, names)
                code = _without_comments(body)
                self.assertIsNone(re.search(r"\blocal\b", code))
                self.assertNotIn("?", code)


class LookMirrorOwnerModelTest(unittest.TestCase):
    """The owner's Begin, Release, Finish and the two messages, translated and run."""

    def setUp(self) -> None:
        self.model = _OwnerModel(_source(LOOK_PATH))

    def test_a_converged_look_is_left_to_the_server_and_the_next_start_supersedes_it(self) -> None:
        model = self.model
        first = model.begin(target=(10.0, 1.5, 0.0), budget=3.0)
        self.assertEqual(model.channel.sent, [[model.udt, True, first, (10.0, 1.5, 0.0), 3.0, 1]])
        self.assertEqual(model.state.s_ServerLookId, first)
        model.finish("converged", True)
        # The server's copy reaches its own band: no release, nothing pending,
        # and the id kept for a later Release.
        self.assertEqual(len(model.channel.sent), 1)
        self.assertEqual((model.state.s_ServerLookId, model.state.s_ServerReleasePending), (first, False))
        second = model.begin()
        self.assertGreater(second, first)
        # The new start replaces the copy there (MCPLookAccept): no release first.
        self.assertEqual(model.channel.sent[1:], [[model.udt, True, second, (10.0, 0.0, 0.0), 3.0, 1]])
        model.finish("deadline", False)
        self.assertEqual(model.channel.sent[-1], [model.udt, False, second])
        # A later release finds nothing left to send.
        model.release("restore")
        self.assertEqual(len(model.channel.sent), 3)

    def test_a_restore_after_a_converged_look_releases_its_copy(self) -> None:
        model = self.model
        first = model.begin()
        model.finish("converged", True)
        model.release("restore")
        self.assertEqual(model.channel.sent[-1], [model.udt, False, first])
        self.assertEqual((model.state.s_ServerLookId, model.state.s_ServerReleasePending), (0, False))
        model.release("restore")
        self.assertEqual(len(model.channel.sent), 2)

    def test_a_cancel_after_a_converged_look_releases_its_copy_once(self) -> None:
        model = self.model
        look = model.begin()
        model.finish("converged", True)
        # A cancel naming another command leaves the copy alone.
        self.assertTrue(model.cancel(command_id=8).ok)
        self.assertEqual(len(model.channel.sent), 1)
        result = model.cancel()
        self.assertEqual((model.answered, result.ok), (True, True))
        self.assertEqual(model.channel.sent[-1], [model.udt, False, look])
        self.assertEqual((model.state.s_ServerLookId, model.state.s_ServerReleasePending), (0, False))
        # The look's report and generation stay as they were, so its job still
        # answers converged.
        self.assertEqual((model.state.s_Generation, model.state.s_Converged), (look, True))
        self.assertEqual(model.state.s_Termination.text, "converged")
        model.cancel()
        self.assertEqual(len(model.channel.sent), 2)

    def test_a_running_look_cancelled_sends_its_release(self) -> None:
        model = self.model
        first = model.begin()
        second = model.begin()
        self.assertGreater(second, first)
        self.assertTrue(model.cancel().ok)
        self.assertEqual(model.channel.sent[-1], [model.udt, False, second])
        self.assertEqual(len(model.channel.sent), 3)
        self.assertGreaterEqual(model.disabled, 3)

    def test_a_start_the_channel_cannot_take_is_refused_and_changes_nothing(self) -> None:
        model = self.model
        model.channel.free = False
        self.assertEqual(model.begin(), 0)
        self.assertEqual(model.refusal, "input_busy")
        self.assertEqual(model.channel.sent, [])
        # No local look either, and the next id is still free.
        self.assertEqual((model.state.s_Active, model.state.s_Generation, model.disabled), (False, 0, 0))
        model.channel.free = True
        look = model.begin()
        self.assertEqual((look, model.refusal), (1, ""))
        self.assertEqual(model.channel.sent, [[model.udt, True, 1, (10.0, 0.0, 0.0), 3.0, 1]])
        self.assertEqual((model.state.s_Active, model.state.s_ServerLookId), (True, 1))

    def test_offline_the_look_runs_on_this_side_only(self) -> None:
        model = self.model
        model.multiplayer = False
        look = model.begin()
        self.assertEqual((model.refusal, model.state.s_Active, model.state.s_ServerLookId), ("", True, 0))
        self.assertGreater(look, 0)
        model.finish("deadline", False)
        self.assertEqual(model.channel.sent, [])

    def test_a_release_waits_for_a_local_player_and_holds_the_next_start(self) -> None:
        model = self.model
        first = model.begin()
        model.local_player = False
        model.finish("player_changed", False)
        self.assertEqual(len(model.channel.sent), 1)
        self.assertTrue(model.state.s_ServerReleasePending)
        generation = model.state.s_Generation
        # The pending release cannot leave, so the start is refused and the
        # look does not start.
        self.assertEqual((model.begin(), model.refusal), (0, "input_busy"))
        self.assertEqual(len(model.channel.sent), 1)
        self.assertEqual((model.state.s_Active, model.state.s_Generation), (False, generation))
        model.local_player = True
        model.flush()
        self.assertEqual(model.channel.sent[-1], [model.udt, False, first])
        self.assertFalse(model.state.s_ServerReleasePending)
        self.assertGreater(model.begin(), first)


class LookMirrorServerModelTest(unittest.TestCase):
    """The server copy's translated methods, run on one fake player."""

    def setUp(self) -> None:
        self.source = _source(LOOK_PATH)
        self.model = _ServerLookModel(self.source)

    def _run_until_released(self, model: _ServerLookModel, limit: int = 200) -> int:
        ticks = 0
        while model.active is not None and ticks < limit:
            model.tick(from_consume=ticks % 2 == 0)
            ticks += 1
        return ticks

    def test_the_server_turns_its_own_heading_to_the_bearing_and_stops_in_the_band(self) -> None:
        for bearing in (90.0, -90.0, 170.0, 180.0, 45.0):
            with self.subTest(bearing=bearing):
                model = _ServerLookModel(self.source)
                rad = math.radians(bearing)
                target = (10.0 * math.sin(rad), 0.0, 10.0 * math.cos(rad))
                model.start(3, target=target)
                self.assertEqual((model.active, model.log), (3, []))
                model.tick()
                first = model.player.overrides[0]
                self.assertEqual(first[0], "ONE_FRAME")
                # Right of the heading is a positive pulse, left a negative one,
                # never more than MAX_PULSE.
                self.assertLessEqual(abs(first[1]), 0.2 + 1e-9)
                if 0.0 < bearing < 180.0:
                    self.assertGreater(first[1], 0.0)
                if bearing < 0.0:
                    self.assertLess(first[1], 0.0)
                self._run_until_released(model)
                self.assertIsNone(model.active)
                error = (bearing - model.player.compass_deg + 180.0) % 360.0 - 180.0
                self.assertLessEqual(abs(error), 1.0)
                self.assertRegex(model.log[-1], r"released look=3 reason=converged consumed=\d+ handled=\d+$")
                self.assertEqual(model.player.overrides[-1], ("DISABLED", 0.0))

    def test_a_server_that_does_not_turn_stops_at_its_deadman(self) -> None:
        model = self.model
        model.player.turns = False
        model.start(5, budget=2.0)
        model.now = 100.0
        model.tick()
        model.now = 102.5
        model.tick(from_consume=False)
        self.assertEqual(model.active, 5)
        model.now = 102.51
        model.tick()
        self.assertEqual((model.active, model.log[-1]), (None, _released(5, "expired", consumed=1, handled=1)))

    def test_a_replayed_or_older_start_cannot_re_arm_a_released_look(self) -> None:
        model = self.model
        model.start(7)
        model.release(7)
        self.assertEqual((model.active, model.log[-1]), (None, _released(7, "client")))
        for look_id, reason in ((7, "stale_look_id"), (0, "bad_look_id")):
            with self.subTest(look_id=look_id):
                model.start(look_id)
                self.assertEqual((model.active, model.log[-1]), (None, _verdict(0, look_id, reason)))
        # The same refusal again in a row is not logged again.
        logged = len(model.log)
        model.start(0)
        model.start(-3)
        self.assertEqual((model.active, len(model.log)), (None, logged))
        model.release(9)
        self.assertEqual(model.log[-1], "[DayZ_MCP] player_look_at server ignored look=9 reason=release_without_look")
        model.start(9)
        self.assertEqual((model.active, model.log[-1]), (None, _verdict(0, 9, "stale_look_id")))
        model.start(10)
        model.release(11)
        self.assertEqual(model.active, 10)
        self.assertEqual(
            model.log[-1], "[DayZ_MCP] player_look_at server ignored look=11 reason=release_of_another_look"
        )
        self.assertEqual(model.player.overrides, [])

    def test_a_refused_start_leaves_the_running_look_and_bad_values_are_refused(self) -> None:
        model = self.model
        model.start(4)
        model.tick()
        armed = (model.player.m_MCPLookDeadlineS, list(model.player.overrides))
        for look_id, target, budget, reason in (
            # Alternated: the same refusal twice in a row is logged once.
            (5, (float("nan"), 0.0, 0.0), 3.0, "bad_pos"),
            (6, (10.0, 0.0, 0.0), 0.0, "bad_timeout"),
            (7, (float("inf"), 0.0, 0.0), 3.0, "bad_pos"),
            (8, (10.0, 0.0, 0.0), 3.5, "bad_timeout"),
            (10, (10.0, float("nan"), 0.0), 3.0, "bad_pos"),
            (11, (10.0, 0.0, 0.0), float("inf"), "bad_timeout"),
        ):
            with self.subTest(reason=reason, look_id=look_id):
                model.start(look_id, target=target, budget=budget)
                self.assertEqual(model.log[-1], _verdict(0, look_id, reason))
                self.assertEqual(model.active, 4)
                self.assertEqual((model.player.m_MCPLookDeadlineS, model.player.overrides), armed)
        # An accepted start replaces it, disabling once what the old one pulsed.
        model.start(12, target=(-10.0, 0.0, 0.0))
        self.assertEqual(model.active, 12)
        self.assertEqual(model.log[-1], _released(4, "superseded", consumed=1))
        self.assertEqual(model.player.overrides[-1], ("DISABLED", 0.0))

    def test_the_server_copy_stops_when_its_owner_is_gone_or_dead(self) -> None:
        for cause, end in (
            ("no_identity", lambda player: setattr(player, "identity", None)),
            ("disconnected", lambda player: setattr(player, "disconnected", True)),
            ("player_dead", lambda player: setattr(player, "alive", False)),
        ):
            with self.subTest(cause=cause):
                model = _ServerLookModel(self.source)
                model.start(2)
                model.tick()
                end(model.player)
                model.tick(from_consume=False)
                self.assertEqual((model.active, model.log[-1]), (None, _released(2, cause, consumed=1)))
                model.start(3)
                self.assertEqual((model.active, model.log[-1]), (None, _verdict(0, 3, cause)))

    def test_a_reconnect_into_the_body_stops_the_look_and_frees_the_ids(self) -> None:
        model = self.model
        model.start(12)
        model.player.OnReconnect()
        self.assertEqual(model.supers, ["OnReconnect"])
        self.assertEqual((model.active, model.log[-1]), (None, _released(12, "reconnect")))
        model.start(1)
        self.assertEqual((model.active, model.log[-1]), (1, _released(12, "reconnect")))


class LookMirrorRoundTripTest(unittest.TestCase):
    """The owner's messages delivered to the server copy late, in the order sent."""

    def setUp(self) -> None:
        self.source = _source(LOOK_PATH)

    def _deliver(self, owner: _OwnerModel, server: _ServerLookModel) -> None:
        for message in owner.channel.sent:
            self.assertEqual(message[0], owner.udt)
            if message[1]:
                server.start(message[2], target=message[3], budget=message[4], turn=message[5])
            else:
                server.release(message[2])
        owner.channel.sent = []

    def test_a_start_that_arrives_after_converged_and_restore_is_released_there(self) -> None:
        owner = _OwnerModel(self.source)
        server = _ServerLookModel(self.source)
        look = owner.begin()
        owner.finish("converged", True)
        owner.release("restore")
        self._deliver(owner, server)
        self.assertIsNone(server.active)
        self.assertEqual(server.log, [_released(look, "client")])
        server.tick()
        self.assertEqual(server.player.overrides, [])

    def test_a_start_that_arrives_after_converged_and_a_cancel_is_released_there(self) -> None:
        owner = _OwnerModel(self.source)
        server = _ServerLookModel(self.source)
        look = owner.begin()
        owner.finish("converged", True)
        self.assertTrue(owner.cancel().ok)
        self._deliver(owner, server)
        self.assertIsNone(server.active)
        self.assertEqual(server.log, [_released(look, "client")])
        server.tick()
        self.assertEqual(server.player.overrides, [])

    def test_the_next_look_supersedes_a_converged_copy_still_running(self) -> None:
        owner = _OwnerModel(self.source)
        server = _ServerLookModel(self.source)
        server.player.turns = False
        first = owner.begin()
        self._deliver(owner, server)
        server.tick()
        owner.finish("converged", True)
        second = owner.begin(target=(-10.0, 0.0, 0.0))
        self._deliver(owner, server)
        self.assertEqual(server.active, second)
        self.assertEqual(server.log[-1], _released(first, "superseded", consumed=1))


def _target_at(bearing_deg: float, distance: float = 10.0) -> tuple[float, float, float]:
    rad = math.radians(bearing_deg)
    return (distance * math.sin(rad), 0.0, distance * math.cos(rad))


class LookMirrorTieBreakTest(unittest.TestCase):
    """S2 (#363 follow-up): a target behind turns both sides the same way."""

    def setUp(self) -> None:
        self.source = _source(LOOK_PATH)

    def _first_server_pulse(self, bearing: float, heading_deg: float = 0.0) -> float:
        model = _ServerLookModel(self.source)
        model.player.heading_rad = -math.radians(heading_deg)
        model.start(3, target=_target_at(bearing))
        model.tick()
        self.assertEqual(model.player.overrides[0][0], "ONE_FRAME")
        return model.player.overrides[0][1]

    def test_a_target_behind_turns_the_server_right_either_side_of_180(self) -> None:
        # -180 exactly: atan2 of a target a hair left of straight behind; the
        # wrapped error is then -180, and without the tie-break the server
        # turned left where +180 turned it right.
        for bearing, heading in ((180.0, 0.0), (-180.0, 0.0), (179.5, 0.0), (-179.5, 0.0), (-178.5, 0.0),
                                 (90.0, -90.5), (90.0, 269.5)):
            with self.subTest(bearing=bearing, heading=heading):
                self.assertGreater(self._first_server_pulse(bearing, heading), 0.0)

    def test_outside_the_tie_band_the_short_way_is_kept(self) -> None:
        self.assertLess(self._first_server_pulse(-177.0), 0.0)
        self.assertGreater(self._first_server_pulse(177.0), 0.0)
        self.assertLess(self._first_server_pulse(-90.0), 0.0)

    def test_a_look_behind_still_converges_in_the_band(self) -> None:
        for bearing in (-180.0, -179.5, 180.0):
            with self.subTest(bearing=bearing):
                model = _ServerLookModel(self.source)
                model.start(3, target=_target_at(bearing))
                ticks = 0
                while model.active is not None and ticks < 200:
                    model.tick()
                    ticks += 1
                self.assertIsNone(model.active)
                self.assertRegex(model.log[-1], r"reason=converged")
                # Every pulse of the turn went right.
                pulses = [value for kind, value in model.player.overrides if kind == "ONE_FRAME"]
                self.assertTrue(pulses and all(value > 0.0 for value in pulses), pulses)

    def test_the_owner_and_the_server_share_the_tie_break(self) -> None:
        look: dict[str, object] = {"_S": _LogString, "Math": _EnforceMath, **_constants(self.source)}
        translate(self.source, TIE_BREAK, look)
        translate(self.source, CLAMP, look)
        tie = look["TieBreakDeg"]
        for given, expected in ((-180.0, 180.0), (-179.5, 180.5), (-178.5, 181.5), (-177.0, -177.0),
                                (179.5, 179.5), (180.0, 180.0), (90.0, 90.0), (-90.0, -90.0), (0.0, 0.0)):
            with self.subTest(given=given):
                self.assertAlmostEqual(tie(given), expected)
        handler = _without_comments(_method_body(self.source, ON_COMMAND))
        _in_order(
            self,
            handler,
            "yawErrorDeg = LatchDeg(WrapPi(desiredBearing - currentBearing) * Math.RAD2DEG, s_TurnSign);",
            "pulseX = ClampPulse(GAIN * yawErrorDeg * Math.DEG2RAD);",
        )
        self.assertNotIn("ClampPulse(GAIN * WrapPi(desiredBearing - currentBearing))", handler)


class LookMirrorLatchTest(unittest.TestCase):
    """S-1 (Auditor on #378): the way to turn is picked once, by the owner at
    Begin, and sent in the start; both sides hold it while the target is
    more than LATCH_FROM_DEG off. A fixed band alone only moved the
    disagreement: owner -177.9 (left) against server -178.1 (right)."""

    def setUp(self) -> None:
        self.source = _source(LOOK_PATH)
        self.look: dict[str, object] = {"_S": _LogString, "Math": _EnforceMath, **_constants(self.source)}
        for signature in (CLAMP, TIE_BREAK, TURN_SIGN_FOR, LATCH):
            translate(self.source, signature, self.look)

    def _owner_pulse(self, owner_error: float) -> tuple[int, float]:
        turn = self.look["TurnSignFor"](owner_error)
        error = self.look["LatchDeg"](owner_error, turn)
        return turn, self.look["ClampPulse"](self.look["GAIN"] * error * _EnforceMath.DEG2RAD)

    def _server_pulse(self, server_error: float, turn: int) -> float:
        model = _ServerLookModel(self.source)
        model.start(3, target=_target_at(server_error), turn=turn)
        model.tick()
        return model.player.overrides[0][1]

    def test_owner_and_server_turn_the_same_way_when_they_read_a_little_apart(self) -> None:
        for owner_error, server_error in (
            (-177.9, -178.1), (-178.1, -177.9), (-179.9, 179.9), (179.9, -179.9),
            (177.9, -179.0), (-176.0, 179.0), (178.5, 177.0), (-177.0, -178.5),
        ):
            with self.subTest(owner=owner_error, server=server_error):
                turn, owner = self._owner_pulse(owner_error)
                server = self._server_pulse(server_error, turn)
                self.assertGreater(owner * server, 0.0, (turn, owner, server))

    def test_the_latch_holds_for_the_whole_turn_and_still_converges(self) -> None:
        for turn, bearing in ((1, -178.1), (-1, 178.1), (-1, -177.9), (1, 177.9)):
            with self.subTest(turn=turn, bearing=bearing):
                model = _ServerLookModel(self.source)
                model.start(3, target=_target_at(bearing), turn=turn)
                ticks = 0
                while model.active is not None and ticks < 300:
                    model.tick()
                    ticks += 1
                self.assertRegex(model.log[-1], r"reason=converged")
                pulses = [value for kind, value in model.player.overrides if kind == "ONE_FRAME"]
                self.assertTrue(pulses and all(value * turn > 0.0 for value in pulses), pulses[:5])

    def test_latch_values_and_the_short_way_near_the_target(self) -> None:
        latch = self.look["LatchDeg"]
        for error, turn, expected in (
            (-178.1, 1, 181.9), (-178.1, -1, -178.1), (179.0, -1, -181.0), (179.0, 1, 179.0),
            (-91.0, 1, 269.0), (-89.0, 1, -89.0), (89.0, -1, 89.0), (-180.0, 0, 180.0), (-177.0, 0, -177.0),
        ):
            with self.subTest(error=error, turn=turn):
                self.assertAlmostEqual(latch(error, turn), expected)
        self.assertEqual([self.look["TurnSignFor"](v) for v in (-177.9, -178.1, 0.0, 90.0, -90.0, -180.0)],
                         [-1, 1, 1, 1, -1, 1])

    def test_a_turn_outside_minus_one_to_one_is_refused(self) -> None:
        model = _ServerLookModel(self.source)
        model.start(3, turn=2)
        self.assertEqual((model.active, model.log[-1]), (None, _verdict(0, 3, "bad_turn")))
        model.start(4, turn=-1)
        self.assertEqual(model.active, 4)

    def test_the_owner_picks_the_turn_from_its_camera_and_latches_it(self) -> None:
        start = _without_comments(_method_body(self.source, START_TURN))
        _in_order(self, start, "if (!ReadCamera(player, camPos, camDir))", "return 0;",
                  "desired = target - camPos;",
                  "yawDeg = WrapPi(Math.Atan2(desired[0], desired[2]) - Math.Atan2(camDir[0], camDir[2])) * Math.RAD2DEG;",
                  "return TurnSignFor(yawDeg);")
        model = _OwnerModel(self.source)
        model.turn = -1
        look = model.begin()
        self.assertEqual(model.channel.sent[-1], [model.udt, True, look, (10.0, 0.0, 0.0), 3.0, -1])
        self.assertEqual(model.state.s_TurnSign, -1)


class LookMirrorStartIntervalTest(unittest.TestCase):
    """Sol on #378 @ 3df2d865: a start the server throttles turned the owner
    only. The owner now sends no start sooner than OWNER_MIN_START_INTERVAL_S
    (twice the server's) after its last one; Begin answers input_busy and
    neither side turns. The clock is set by hand here, never advanced by the
    helpers between the two looks under test."""

    def setUp(self) -> None:
        self.source = _source(LOOK_PATH)
        constants = _constants(self.source)
        self.assertEqual(constants["OWNER_MIN_START_INTERVAL_S"], 2 * constants["SERVER_MIN_START_INTERVAL_S"])

    def _begin_at(self, owner: _OwnerModel, now: float, target=(10.0, 0.0, 0.0)) -> int:
        owner.now = now
        refusal = owner.namespace["Begin"](object(), target, 3.0, 7).text
        owner.refusal = refusal
        return 0 if refusal else owner.state.s_Generation

    def _deliver_at(self, owner: _OwnerModel, server: _ServerLookModel, now: float) -> None:
        for message in owner.channel.sent:
            server.now = now
            if message[1]:
                server.player.MCPLookAccept(message[2], message[3], message[4], message[5])
            else:
                server.player.MCPLookReadRelease(message[2])
        owner.channel.sent = []

    def test_a_second_look_sooner_than_the_interval_turns_neither_side(self) -> None:
        owner = _OwnerModel(self.source)
        server = _ServerLookModel(self.source)
        first = self._begin_at(owner, 10.0)
        owner.finish("converged", True)
        self._deliver_at(owner, server, 100.0)
        self.assertEqual(server.active, first)
        second = self._begin_at(owner, 10.1, target=(-10.0, 0.0, 0.0))
        self.assertEqual((second, owner.refusal), (0, "input_busy"))
        self.assertEqual(owner.channel.sent, [])
        # No local look either: nothing turns on this side alone.
        self.assertFalse(owner.state.s_Active and not owner.state.s_Finished)
        self.assertEqual(owner.state.s_Generation, first)

    def test_a_start_the_owner_sends_is_not_throttled_by_a_late_first_start(self) -> None:
        # The first start reaches the server late, the second early: up to
        # SERVER_MIN_START_INTERVAL_S of delay between the two arrivals.
        owner = _OwnerModel(self.source)
        server = _ServerLookModel(self.source)
        first = self._begin_at(owner, 10.0)
        owner.finish("converged", True)
        self._deliver_at(owner, server, 100.24)
        second = self._begin_at(owner, 10.5, target=(-10.0, 0.0, 0.0))
        self.assertGreater(second, first)
        self._deliver_at(owner, server, 100.49)
        self.assertEqual(server.active, second)
        self.assertNotIn("throttled", " ".join(server.log))

    def test_offline_looks_are_not_held(self) -> None:
        owner = _OwnerModel(self.source)
        owner.multiplayer = False
        self.assertGreater(self._begin_at(owner, 10.0), 0)
        self.assertGreater(self._begin_at(owner, 10.1), 0)


class LookMirrorGateTest(unittest.TestCase):
    """L2 (#363 follow-up): the server copy runs on a profile=test server only,
    at most one start per sender per SERVER_MIN_START_INTERVAL_S, and an
    accepted start logs nothing."""

    def setUp(self) -> None:
        self.source = _source(LOOK_PATH)
        self.model = _ServerLookModel(self.source)

    def test_a_server_without_the_test_profile_refuses_every_start(self) -> None:
        model = self.model
        model.allowed = False
        model.start(1)
        self.assertEqual((model.active, model.log), (None, [_verdict(0, 1, "not_allowed")]))
        model.start(2)
        model.tick()
        self.assertEqual((model.active, len(model.log), model.player.overrides), (None, 1, []))
        # The gate comes before the id is used up.
        self.assertEqual(model.player.m_MCPLookLastId, 0)
        model.allowed = True
        model.start(1)
        self.assertEqual(model.active, 1)

    def test_a_start_faster_than_the_interval_is_throttled(self) -> None:
        model = self.model
        self.assertEqual(_constants(self.source)["SERVER_MIN_START_INTERVAL_S"], 0.25)
        model.start(1)
        model.now = 100.1
        model.start(2)
        self.assertEqual((model.active, model.log[-1]), (1, _verdict(0, 2, "throttled")))
        model.now = 100.2
        model.start(3)
        self.assertEqual((model.active, model.log.count(_verdict(0, 2, "throttled"))), (1, 1))
        model.now = 100.25
        model.start(4)
        self.assertEqual(model.active, 4)
        self.assertEqual(model.log[-1], _released(1, "superseded"))

    def test_an_accepted_start_prints_nothing(self) -> None:
        model = self.model
        for look_id in (1, 2, 3):
            model.start(look_id)
        self.assertEqual(model.active, 3)
        self.assertFalse([line for line in model.log if "accepted=1" in line], model.log)

    def test_the_gate_is_set_from_the_test_profile_at_mission_start(self) -> None:
        self.assertIn("protected static bool s_ServerAllowed;", self.source)
        self.assertEqual(_flat(_method_body(self.source, SERVER_ALLOWED)), "return s_ServerAllowed;")
        self.assertEqual(_flat(_method_body(self.source, SET_SERVER_ALLOWED)), "s_ServerAllowed = allowed;")
        mission = _source(MISSION_SERVER_PATH)
        _in_order(
            self,
            mission,
            "MCPGodmode.ResetMission();",
            "MCPGodmode.SetDefaultOn(MCPBridge.ConfigIsTestProfile());",
            "MCPLookAtControl.SetServerAllowed(MCPGodmode.DefaultOn());",
            "MCPBridge bridge = MCPBridge.Get();",
        )
        # No Print of an accepted start is left in the verdict.
        verdict = _method_body(self.source, SERVER_VERDICT)
        self.assertNotIn("accepted=1", verdict)
        self.assertEqual(_without_comments(verdict).count("Print("), 1)


class LookMirrorMoveAndAimReleaseTest(unittest.TestCase):
    """L1 (#363 follow-up): a move or an aim after a converged look ends the
    server copy, which otherwise kept turning the heading up to its band or
    its deadman (budget + 0.5 s)."""

    def setUp(self) -> None:
        self.source = _source(LOOK_PATH)

    def test_an_accepted_move_releases_the_look_copy_on_the_server(self) -> None:
        accept = _method_body(_source(MOVE_PATH), MOVE_ACCEPT)
        _in_order(self, accept, "m_MCPMoveActive = request;", 'MCPLookServerRelease("move_started");',
                  'MCPMoveVerdict(true, request.move_id, "");')
        model = _ServerLookModel(self.source)
        model.player.turns = False
        model.start(3, target=_target_at(170.0))
        model.tick()
        model.player.MCPLookServerRelease(_LogString.of("move_started"))
        self.assertEqual((model.active, model.log[-1]), (None, _released(3, "move_started", consumed=1)))
        self.assertEqual(model.player.overrides[-1], ("DISABLED", 0.0))
        pulses = len(model.player.overrides)
        model.tick()
        self.assertEqual(len(model.player.overrides), pulses)

    def test_weapon_aim_ends_a_converged_look_copy_and_leaves_a_running_look(self) -> None:
        dispatch = _method_body(_source(CLIENT_BRIDGE_PATH), WEAPON_AIM)
        _in_order(self, dispatch, "MCPLookAtControl.ReleaseServerCopy();",
                  "generation = MCPWeaponControl.BeginAim(player, dx, dy);")
        model = _OwnerModel(self.source)
        look = model.begin()
        # A look still running is left to its own end.
        model.namespace["ReleaseServerCopy"]()
        self.assertEqual(len(model.channel.sent), 1)
        model.finish("converged", True)
        model.namespace["ReleaseServerCopy"]()
        self.assertEqual(model.channel.sent[-1], [model.udt, False, look])
        self.assertEqual((model.state.s_ServerLookId, model.state.s_ServerReleasePending), (0, False))
        model.namespace["ReleaseServerCopy"]()
        self.assertEqual(len(model.channel.sent), 2)


class LookMirrorDescriptionTest(unittest.TestCase):
    def test_player_look_at_says_the_server_turns_and_what_a_miss_answers(self) -> None:
        from dayz_mcp import server

        app, _runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        description = " ".join(app._tool_manager.get_tool("player_look_at").description.split())
        for part in (
            "The bridge sends the look to the server too, which turns the player's heading "
            "with it (yaw only)",
            "players_visibility's heading_deg and in_fov follow the look",
            "A full input channel, or a previous look's release that cannot be sent yet, is "
            "input_busy and turns neither side",
            "answers its termination with observed=error_final_deg=, ticks= and pulses=",
        ):
            with self.subTest(part=part):
                self.assertIn(part, description)


if __name__ == "__main__":
    unittest.main()
