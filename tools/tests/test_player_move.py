"""Inbox fb-20261001-013706-c1cb: player_move, the on-foot local player walked without OS input.

The design is the one measured in game (spike key 106, after inbox 4485 showed
the owner's movement overrides alone are corrected back): the owner client
applies HumanInputController.OverrideMovementSpeed and OverrideMovementAngle
ENABLED from the local player's CommandHandler every tick, and sends a
ScriptInputUserData request on which the server applies the same two overrides
to its copy of the player in every ConsumeMove (after super) and in its
CommandHandler, with its own checks, deadman and arrival check, and DISABLED
only what each side enabled. These tests pin the wire contract, the census,
the prune contract, the Python normalization and the Enforce source the game
will compile. They do not launch DayZ; the verb is tested in game after the
promotion.
"""
from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, player_move, result_prune, server
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


COMMAND = "player_move"
FIELD = "player_move"
SCRIPTS = addon_root() / "scripts"
MOVE_PATH = SCRIPTS / "4_World" / "MCP_PlayerMove.c"
WEAPON_PATH = SCRIPTS / "4_World" / "MCP_Weapon.c"
CLIENT_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
VANILLA = Path(r"P:\scripts")

RELEASED_BY = {"hold", "phase", "ttl", "arrived", "restore", "player_changed", "shutdown"}
BRIDGE_CODES = {
    "bad_args", "bad_speed", "bad_angle_deg", "bad_heading_deg", "bad_to",
    "bad_arrive_radius_m", "bad_hold_s", "bad_ttl_s", "client_not_in_game",
    "no_player", "player_dead", "player_unconscious", "player_restrained",
    "seated", "no_input_controller", "not_in_move_command", "player_move_busy",
    "input_busy", "not_held", "aborted",
}
EXPECTED_REQUIRED = {
    frozenset({"mode"}),
    frozenset({"mode", "speed", "direction", "hold_s", "angle_deg"}),
    frozenset({"mode", "speed", "direction", "hold_s", "heading"}),
    frozenset({"mode", "speed", "direction", "hold_s", "to", "radius"}),
    frozenset({"mode", "speed", "direction", "hold_ttl_s", "angle_deg"}),
    frozenset({"mode", "speed", "direction", "hold_ttl_s", "heading"}),
    frozenset({"mode", "speed", "direction", "hold_ttl_s", "to", "radius"}),
}
EXPECTED_REPLY_MEMBERS = [
    ("string", "phase"),
    ("string", "speed"),
    ("float", "speed_value"),
    ("string", "direction"),
    ("float", "angle_deg"),
    ("float", "heading_deg"),
    ("ref array<float>", "to"),
    ("float", "arrive_radius_m"),
    ("int", "move_id"),
    ("ref array<float>", "start_pos"),
    ("ref array<float>", "end_pos"),
    ("float", "distance_m"),
    ("bool", "arrived"),
    ("string", "released_by"),
    ("int", "start_tick"),
    ("int", "release_tick"),
    ("float", "start_tick_time_s"),
    ("float", "release_tick_time_s"),
    ("float", "release_due_s"),
    ("string", "server_request"),
    ("float", "applied_angle_deg"),
    ("int", "command_ticks"),
]
EXPECTED_REQUEST_MEMBERS = [
    ("int", "move_id"),
    ("string", "phase"),
    ("float", "speed"),
    ("string", "direction"),
    ("float", "angle_deg"),
    ("float", "heading_deg"),
    ("vector", "target"),
    ("float", "radius"),
    ("float", "seconds"),
]

# Signatures this verb adds, by unique text.
DISPATCH = "protected bool DispatchPlayerMove(MCPCommand command, MCPResult result)"
DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
RELEASE = "protected bool ReleasePlayerMove(MCPResult result, MCPPlayerMove reply)"
FILL_REQUEST = "protected void FillPlayerMoveRequest(MCPPlayerMove reply, MCPPlayerMoveRequest request)"
FILL_RECORD = "protected void FillPlayerMoveRecord(MCPPlayerMove reply)"
FILL_RELEASE = "protected void FillPlayerMoveRelease(MCPPlayerMove reply)"
PROCESS = "protected bool ProcessPlayerMoveJob(MCPJob job)"
POST = "protected void PostPlayerMoveJob(MCPJob job)"
TIMEOUT = "protected void PostPlayerMoveTimeout(MCPJob job)"
CLIENT_METHODS = (DISPATCH, RELEASE, FILL_REQUEST, FILL_RECORD, FILL_RELEASE, PROCESS, POST, TIMEOUT)
REFUSAL = "static string RequestRefusal(MCPPlayerMoveRequest request)"
SECONDS = "static string SecondsRefusal(string phase, float seconds)"
ACTOR = "static string ActorRefusal(PlayerBase player)"
START_REFUSAL = "static string StartRefusal(PlayerBase player)"
DISTANCE = "static string DistanceRefusal(PlayerBase player, MCPPlayerMoveRequest request)"
RELATIVE = "static float RelativeAngleDeg(PlayerBase player, MCPPlayerMoveRequest request)"
ARRIVED = "static bool HasArrived(PlayerBase player, MCPPlayerMoveRequest request)"
HORIZONTAL = "static float HorizontalDistance(vector from, vector target)"
FINITE = "static bool IsFiniteValue(float value)"
ENABLE = "static void Enable(HumanInputController hic, float speed, float angleDeg)"
DISABLE = "static void Disable(HumanInputController hic)"
NEXT_ID = "static int NextMoveId()"
BEGIN = "static int Begin(PlayerBase player, MCPPlayerMoveRequest request, float nowS, string serverRequest)"
APPLY = "static void Apply(PlayerBase player)"
RELEASE_ALL = "static void ReleaseAll(string why)"
RELEASE_DUE = "static void ReleaseDue()"
ON_COMMAND = "static void OnCommandHandler(PlayerBase player)"
MAINTAIN = "static void MaintainFromTick(int bridgeTick)"
SEND_START = "static string SendStart(MCPPlayerMoveRequest request)"
FLUSH = "static void FlushServerRelease()"
READ_REQUEST = "static bool ReadRequest(ParamsReadContext ctx, MCPPlayerMoveRequest request)"
WRAP = "static float WrapDeg180(float angle)"
COMPASS = "static float CompassFromHeading(float headingRad)"
BEARING = "static float BearingDeg(vector from, vector target)"
SERVER_INPUT = "override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)"
SERVER_READ = "protected void MCPMoveReadRequest(ParamsReadContext ctx)"
SERVER_READ_RELEASE = "protected void MCPMoveReadRelease(int moveId)"
SERVER_ACCEPT = "protected void MCPMoveAccept(MCPPlayerMoveRequest request)"
SERVER_TICK = "protected void MCPMoveServerTick(bool fromConsume)"
SERVER_RELEASE = "protected void MCPMoveServerRelease(string why)"
SERVER_VERDICT = "protected void MCPMoveVerdict(bool accepted, int moveId, string reason)"
SERVER_NOTE = "protected void MCPMoveNote(int moveId, string reason)"
COMMAND_HANDLER = (
    "override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)"
)
CONSUME = "protected override event void ConsumeMove(PawnMove pMove)"
MOVE_METHODS = (
    REFUSAL, SECONDS, ACTOR, START_REFUSAL, DISTANCE, RELATIVE, ARRIVED, HORIZONTAL,
    FINITE, ENABLE, DISABLE, NEXT_ID, BEGIN, APPLY, RELEASE_ALL, RELEASE_DUE, ON_COMMAND,
    MAINTAIN, SEND_START, FLUSH, READ_REQUEST, WRAP, COMPASS, BEARING, SERVER_INPUT,
    SERVER_READ, SERVER_READ_RELEASE, SERVER_ACCEPT, SERVER_TICK, SERVER_RELEASE,
    SERVER_VERDICT, SERVER_NOTE, COMMAND_HANDLER, CONSUME,
)

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
_LOCAL_TYPES = (
    "string", "int", "float", "bool", "vector", "PlayerBase", "HumanInputController",
    "MCPArgs", "MCPJob", "MCPResult", "MCPPlayerMove", "MCPPlayerMoveRequest",
    "ScriptInputUserData",
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


def _wire(phase: str = "hold", direction: str = "angle", **overrides: object) -> dict[str, object]:
    args: dict[str, object] = {"mode": phase, "speed": 1.0, "direction": direction}
    args["hold_s" if phase == "hold" else "hold_ttl_s"] = 5.0
    if direction == "angle":
        args["angle_deg"] = 30.0
    elif direction == "heading":
        args["heading"] = 270.0
    else:
        args.update(to=[10.0, 0.0, 20.0], radius=0.5)
    args.update(overrides)
    return args


def _reply(**fields: object) -> dict[str, object]:
    reply: dict[str, object] = {
        "phase": "hold",
        "speed": "walk",
        "speed_value": 1.0,
        "direction": "angle",
        "angle_deg": 30.0,
        "heading_deg": 0.0,
        "to": [],
        "arrive_radius_m": 0.0,
        "move_id": 3,
        "start_pos": [1.0, 2.0, 3.0],
        "end_pos": [1.0, 2.0, 8.0],
        "distance_m": 5.0,
        "arrived": 0,
        "released_by": "hold",
        "start_tick": 10,
        "release_tick": 110,
        "start_tick_time_s": 100.0,
        "release_tick_time_s": 105.01,
        "release_due_s": 105.0,
        "server_request": "sent",
        "applied_angle_deg": 30.0,
        "command_ticks": 300,
    }
    reply.update(fields)
    return reply


def _bridge_codes() -> set[str]:
    """Every error code the client bridge can answer for this verb, from the sources."""
    client = _source(CLIENT_PATH)
    move = _source(MOVE_PATH)
    codes: set[str] = {"client_not_in_game"}  # the Dispatch gate before every verb
    for signature in (DISPATCH, RELEASE, PROCESS, TIMEOUT):
        codes |= set(re.findall(r'(?:result|job)\.error = "(\w+)"', _method_body(client, signature)))
    for signature in (REFUSAL, SECONDS, ACTOR, START_REFUSAL, DISTANCE):
        codes |= set(re.findall(r'return "(\w+)";', _method_body(move, signature)))
    codes |= set(re.findall(r'code = "(\w+)";', _method_body(move, SECONDS)))
    codes.discard("")
    return codes


class PlayerMoveIngressTest(unittest.TestCase):
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

    def test_seven_exact_variants_and_no_optional_key(self) -> None:
        variants, delegated = loopback._COMMAND_ARG_SCHEMAS[COMMAND]
        self.assertIsNone(delegated)
        self.assertEqual(len(variants), 7)
        for required, optional, validators in variants:
            with self.subTest(required=sorted(required)):
                self.assertEqual(optional, frozenset())
                self.assertEqual(set(validators), set(required))
        self.assertEqual({required for required, _o, _v in variants}, EXPECTED_REQUIRED)

    def test_each_phase_and_direction_enqueues_on_the_client(self) -> None:
        self.assertEqual(self.state.enqueue_command(COMMAND, {"mode": "release"})[0], 200)
        for phase in ("hold", "press"):
            for direction in ("angle", "heading", "to"):
                with self.subTest(phase=phase, direction=direction):
                    status, body = self.state.enqueue_command(COMMAND, _wire(phase, direction))
                    self.assertEqual((status, body["peer"]), (200, "client"))

    def test_no_value_travels_without_its_phase_or_direction(self) -> None:
        # fb-20260930-065425-8779: an absent key reaches Enforce as 0, so each
        # phase and direction requires its own value and refuses the others'.
        refused = [
            {key: value for key, value in _wire().items() if key != "hold_s"},
            {key: value for key, value in _wire("press").items() if key != "hold_ttl_s"},
            {key: value for key, value in _wire().items() if key != "angle_deg"},
            {key: value for key, value in _wire("hold", "heading").items() if key != "heading"},
            {key: value for key, value in _wire("hold", "to").items() if key != "radius"},
            {key: value for key, value in _wire().items() if key != "speed"},
            _wire(hold_ttl_s=1.0),
            _wire("press", hold_s=1.0),
            _wire(heading=10.0),
            _wire(to=[1.0, 2.0, 3.0]),
            _wire("hold", "heading", angle_deg=0.0),
            _wire("hold", "to", heading=0.0),
            _wire(mirror_to_server=True),
            {"mode": "release", "speed": 1.0},
            {"mode": "release", "hold_ttl_s": 1.0},
            _wire(speed=0.0),
            _wire(speed=True),
            _wire(angle_deg=float("nan")),
            _wire(angle_deg=float("inf")),
            _wire("hold", "heading", heading=360.0),
            _wire("hold", "to", radius=0.1),
            _wire("hold", "to", to=[1.0, float("nan"), 2.0]),
            _wire(hold_s=30.001),
            _wire("press", hold_ttl_s=0.0),
            _wire(direction="north"),
            _wire(mode="walk"),
        ]
        for args in refused:
            with self.subTest(args=args):
                self.assertEqual(
                    self.state.enqueue_command(COMMAND, args), (400, {"error": "bad_args"})
                )


class PlayerMoveCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)

    def test_client_caps_and_dispatch_name_the_command(self) -> None:
        self.assertIn(COMMAND, announced_caps(self.source))
        names = dispatch_census(self.source)
        self.assertIn(COMMAND, names)
        self.assertEqual(names[-1], "ui_dialog")
        dispatch = _method_body(self.source, DISPATCH_CHAIN)
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), "postNow = DispatchPlayerMove(command, result);")
        _in_order(
            self,
            dispatch,
            'result.error = "client_not_in_game";',
            f'command.cmd == "{COMMAND}"',
            'command.cmd == "player_trace"',
            'command.cmd == "ui_dialog"',
        )

    def test_the_server_bridge_neither_implements_nor_announces_it(self) -> None:
        self.assertNotIn(COMMAND, _source(SERVER_BRIDGE_PATH))
        self.assertNotIn("MCPPlayerMove", _source(SERVER_BRIDGE_PATH))
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertNotIn(COMMAND, fixture["peers"]["server"])
        self.assertEqual(fixture["peers"]["client"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["server"])
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["client"].get(COMMAND), COMMAND)

    def test_the_version_and_the_server_arg_contract_hash_stay(self) -> None:
        # A client command: the hash covers the server's arguments only, and a
        # bridge without this verb answers unknown_command and fails the census.
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "3c77a99c95fd05a4")
        self.assertIn('const string MCP_BRIDGE_VERSION = "10";', _source(MESSAGES_PATH))

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


class PlayerMoveOverrideContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(MOVE_PATH)
        self.code = _without_comments(self.source)

    def test_enable_and_disable_touch_exactly_the_two_movement_overrides(self) -> None:
        self.assertEqual(
            _flat(_method_body(self.source, ENABLE)),
            "hic.OverrideMovementSpeed(HumanInputControllerOverrideType.ENABLED, speed); "
            "hic.OverrideMovementAngle(HumanInputControllerOverrideType.ENABLED, angleDeg);",
        )
        self.assertEqual(
            _flat(_method_body(self.source, DISABLE)),
            "hic.OverrideMovementSpeed(HumanInputControllerOverrideType.DISABLED, 0.0); "
            "hic.OverrideMovementAngle(HumanInputControllerOverrideType.DISABLED, 0.0);",
        )
        # The overrides are set nowhere else, never as ONE_FRAME, and the
        # client bridge only reaches them through the control class.
        self.assertEqual(self.code.count("OverrideMovementSpeed("), 2)
        self.assertEqual(self.code.count("OverrideMovementAngle("), 2)
        self.assertNotIn("OverrideMovement", _without_comments(_source(CLIENT_PATH)))

    def test_nothing_is_teleported_aligned_turned_or_carried_in_the_move(self) -> None:
        # Spike rounds 1 and 2: align and SetPosition move without the walk,
        # AlignDirectionWS handles no replay (human.c:1376-1377), and script
        # fields of PlayerBaseMove never reach the server (run e1dd38d2).
        for banned in (
            "ONE_FRAME", "AlignDirectionWS", "AlignPositionWS", "AlignTranslationWS",
            "AlignTranslationLS", "SetPosition", "SetOrientation", "SetDirection",
            "OverrideAimChange", "PlayerBaseMove", "ObtainMove", "ReplayMove", "camera",
            "Camera",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.code)

    def test_steering_recomputes_the_angle_from_each_sides_heading(self) -> None:
        relative = _method_body(self.source, RELATIVE)
        self.assertEqual(
            _if_body(relative, 'request.direction == "angle"').strip(), "return request.angle_deg;"
        )
        _in_order(
            self,
            relative,
            "hic = player.GetInputController();",
            "compass = CompassFromHeading(hic.GetHeadingAngle());",
            "wanted = request.heading_deg;",
            'if (request.direction == "to")',
            "wanted = BearingDeg(player.PhysicsGetPositionWS(), request.target);",
            "return WrapDeg180(wanted - compass);",
        )
        self.assertIn("return -headingRad * Math.RAD2DEG;", _method_body(self.source, COMPASS))
        bearing = _method_body(self.source, BEARING)
        self.assertIn("return Math.Atan2(dx, dz) * Math.RAD2DEG;", bearing)
        self.assertIn("dx = target[0] - from[0];", bearing)
        self.assertIn("dz = target[2] - from[2];", bearing)
        _in_order(
            self,
            _method_body(self.source, APPLY),
            "angle = RelativeAngleDeg(player, s_Request);",
            "Enable(hic, s_Request.speed, angle);",
            "s_Armed = true;",
            "s_AppliedAngleDeg = angle;",
        )
        # The server steers from its own player, not from the owner's numbers.
        tick = _method_body(self.source, SERVER_TICK)
        self.assertIn("angle = MCPPlayerMoveControl.RelativeAngleDeg(this, m_MCPMoveActive);", tick)

    def test_the_compass_sign_matches_vanillas_facing_vector(self) -> None:
        # miscgameplayfunctions.c:726-733 turns a heading h into the facing
        # x = cos(h + PI/2), z = sin(h + PI/2). The compass of that facing
        # (BearingDeg's atan2(x, z)) must be CompassFromHeading's -h.
        for heading in (-math.pi, -2.0, -math.pi / 2, -0.3, 0.0, 0.7, math.pi / 2, 3.0):
            with self.subTest(heading=heading):
                facing_x = math.cos(heading + math.pi / 2)
                facing_z = math.sin(heading + math.pi / 2)
                compass_of_facing = math.degrees(math.atan2(facing_x, facing_z))
                diff = (compass_of_facing - (-heading * 180.0 / math.pi) + 180.0) % 360.0 - 180.0
                self.assertAlmostEqual(diff, 0.0, places=9)
        # Facing north (h = 0), a target due east is 90 degrees to the right.
        self.assertAlmostEqual(math.degrees(math.atan2(1.0, 0.0)), 90.0)

    def test_wrap_is_minus_180_to_180_over_the_reachable_range(self) -> None:
        wrap = _method_body(self.source, WRAP)
        self.assertEqual(wrap.count("wrapped = wrapped - 360.0;"), 2)
        self.assertEqual(wrap.count("wrapped = wrapped + 360.0;"), 2)
        self.assertNotIn("while", wrap)

        def transcribed(angle: float) -> float:
            wrapped = angle
            for _ in range(2):
                if wrapped > 180.0:
                    wrapped -= 360.0
            for _ in range(2):
                if wrapped < -180.0:
                    wrapped += 360.0
            return wrapped

        # Callers pass a wanted direction (0..360 or -180..180) minus a compass
        # heading (-180..180): -360..540 at most.
        for angle in range(-540, 541, 15):
            with self.subTest(angle=angle):
                wrapped = transcribed(float(angle))
                self.assertGreaterEqual(wrapped, -180.0)
                self.assertLessEqual(wrapped, 180.0)
                self.assertAlmostEqual((wrapped - angle) % 360.0, 0.0)

    def test_arrival_is_horizontal_and_within_the_radius(self) -> None:
        arrived = _method_body(self.source, ARRIVED)
        self.assertEqual(_if_body(arrived, 'request.direction != "to"').strip(), "return false;")
        self.assertIn(
            "return HorizontalDistance(player.PhysicsGetPositionWS(), request.target) <= request.radius;",
            arrived,
        )
        distance = _method_body(self.source, HORIZONTAL)
        self.assertIn("return Math.Sqrt(dx * dx + dz * dz);", distance)
        self.assertNotIn("[1]", distance)


class PlayerMoveOwnerLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(MOVE_PATH)
        self.client = _source(CLIENT_PATH)

    def test_the_owner_command_tick_releases_first_then_applies(self) -> None:
        body = _method_body(self.source, ON_COMMAND)
        _in_order(
            self,
            body,
            "if (!s_Active)",
            "if (player != s_Player)",
            'if (ActorRefusal(player) != "")',
            "if (GetGame().GetTickTime() >= s_DueS)",
            "if (HasArrived(player, s_Request))",
            "Apply(player);",
            "s_CommandTicks = s_CommandTicks + 1;",
        )
        self.assertIn('ReleaseAll("player_changed");', _if_body(body, 'ActorRefusal(player) != ""'))
        self.assertIn("ReleaseDue();", _if_body(body, "GetGame().GetTickTime() >= s_DueS"))
        self.assertIn('ReleaseAll("arrived");', _if_body(body, "HasArrived(player, s_Request)"))

    def test_command_handler_runs_the_server_copy_then_the_local_player(self) -> None:
        hook = _method_body(self.source, COMMAND_HANDLER)
        _in_order(
            self,
            hook,
            "super.CommandHandler(pDt, pCurrentCommandID, pCurrentCommandFinished);",
            "if (m_MCPMoveActive)",
            "MCPMoveServerTick(false);",
            "if (!MCPPlayerMoveControl.IsActive())",
            "live = PlayerBase.Cast(GetGame().GetPlayer());",
            "if (this != live)",
            "MCPPlayerMoveControl.OnCommandHandler(this);",
        )
        self.assertEqual(_if_body(hook, "m_MCPMoveActive").strip(), "MCPMoveServerTick(false);")

    def test_maintain_from_tick_releases_on_player_due_time_and_arrival(self) -> None:
        maintain = _method_body(self.source, MAINTAIN)
        _in_order(
            self,
            maintain,
            "s_Tick = bridgeTick;",
            "FlushServerRelease();",
            "if (!s_Active)",
            "live = PlayerBase.Cast(GetGame().GetPlayer());",
            "if (!live)",
            "if (live != s_Player)",
            'if (ActorRefusal(live) != "")',
            "if (GetGame().GetTickTime() >= s_DueS)",
            "if (HasArrived(live, s_Request))",
        )
        for guard in ("!live", "live != s_Player", 'ActorRefusal(live) != ""'):
            with self.subTest(guard=guard):
                self.assertIn('ReleaseAll("player_changed");', _if_body(maintain, guard))
        # Idle costs no engine call: the flush returns on a bool first.
        self.assertLess(maintain.index("if (!s_Active)"), maintain.index("GetGame()"))
        flush = _method_body(self.source, FLUSH)
        self.assertTrue(
            flush.split("if (!s_ServerReleasePending)")[0].strip().endswith("ScriptInputUserData message;")
        )
        due = _method_body(self.source, RELEASE_DUE)
        self.assertIn('ReleaseAll("hold");', _if_body(due, 's_Request && s_Request.phase == "hold"'))
        self.assertTrue(due.rstrip().endswith('ReleaseAll("ttl");'))
        on_tick = _method_body(self.client, "void OnTick(float timeslice)")
        _in_order(
            self,
            on_tick,
            "m_Tick = m_Tick + 1;",
            "MCPInputTriggerControl.MaintainFromTick();",
            "MCPPlayerMoveControl.MaintainFromTick(m_Tick);",
            "MCPPlayerTrace.Tick(m_Tick);",
            "m_JobRunner.Tick(timeslice, this);",
            "if (!m_Configured)",
        )

    def test_a_fall_or_climb_does_not_end_the_move_only_the_start_needs_the_move_command(
        self,
    ) -> None:
        start = _method_body(self.source, START_REFUSAL)
        _in_order(
            self,
            start,
            "refusal = ActorRefusal(player);",
            "if (player.GetCurrentCommandID() != DayZPlayerConstants.COMMANDID_MOVE)",
            'return "not_in_move_command";',
        )
        for signature in (ACTOR, ON_COMMAND, MAINTAIN, SERVER_TICK):
            with self.subTest(method=signature):
                self.assertNotIn("GetCurrentCommandID", _method_body(self.source, signature))
                self.assertNotIn("StartRefusal", _method_body(self.source, signature))
        self.assertIn(
            "refusal = MCPPlayerMoveControl.StartRefusal(player);", _method_body(self.client, DISPATCH)
        )
        self.assertIn(
            "refusal = MCPPlayerMoveControl.StartRefusal(this);", _method_body(self.source, SERVER_ACCEPT)
        )

    def test_release_disables_only_what_this_code_enabled_and_tells_the_server(self) -> None:
        release = _method_body(self.source, RELEASE_ALL)
        self.assertEqual(_if_body(release, "!s_Active").strip(), "return;")
        _in_order(
            self,
            release,
            "if (!s_Active)",
            "s_Active = false;",
            "s_ReleasedGen = s_Gen;",
            "s_ReleasedBy = why;",
            "s_ReleaseTick = s_Tick;",
            "s_ReleaseTimeS = GetGame().GetTickTime();",
            "s_ReleasePos = s_Player.PhysicsGetPositionWS();",
            "if (s_Armed)",
            "Disable(hic);",
            "s_Armed = false;",
            "s_Player = null;",
            'if (why == "shutdown")',
            'if (s_ServerRequest == "sent")',
        )
        self.assertIn("s_ReleaseArrived = true;", _if_body(release, 'why == "arrived"'))
        # A sent move sends its release, naming its move, except at shutdown.
        self.assertIn("s_ServerReleasePending = false;", _if_body(release, 'why == "shutdown"'))
        _in_order(
            self,
            _if_body(release, 's_ServerRequest == "sent"'),
            "s_ServerReleasePending = true;",
            "s_ServerReleaseId = s_Gen;",
            "FlushServerRelease();",
        )

    def test_every_release_names_one_of_the_seven_causes(self) -> None:
        causes = set(re.findall(r'ReleaseAll\("(\w+)"\)', _without_comments(self.source)))
        causes |= set(re.findall(r'MCPPlayerMoveControl\.ReleaseAll\("(\w+)"\)', self.client))
        self.assertEqual(causes, RELEASED_BY)
        self.assertEqual(player_move.RELEASED_BY, frozenset(RELEASED_BY))
        restore = _method_body(self.client, "protected void RestoreGameplay()")
        _in_order(
            self,
            restore,
            'MCPInputTriggerControl.ReleaseAll("restore");',
            'MCPPlayerMoveControl.ReleaseAll("restore");',
            "if (!GetGame())",
        )
        shutdown = _method_body(self.client, "void Shutdown()")
        _in_order(
            self,
            shutdown,
            'MCPInputTriggerControl.ReleaseAll("shutdown");',
            'MCPPlayerMoveControl.ReleaseAll("shutdown");',
            "RestoreGameplay();",
        )

    def test_begin_records_the_start_and_applies_once(self) -> None:
        _in_order(
            self,
            _method_body(self.source, BEGIN),
            "s_Gen = request.move_id;",
            "s_Active = true;",
            "s_Armed = false;",
            "s_Player = player;",
            "s_Request = request;",
            "s_StartS = nowS;",
            "s_DueS = nowS + request.seconds;",
            "s_ServerRequest = serverRequest;",
            "s_StartPos = player.PhysicsGetPositionWS();",
            "s_StartTick = s_Tick;",
            "Apply(player);",
            "return s_Gen;",
        )
        self.assertEqual(_method_body(self.source, NEXT_ID).strip(), "return s_Gen + 1;")


class PlayerMoveServerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(MOVE_PATH)
        self.code = _without_comments(self.source)

    def test_the_input_type_is_new_unique_and_far_above_vanilla(self) -> None:
        self.assertIn("const int MCP_INPUT_UDT_PLAYER_MOVE = 78541066;", self.source)
        weapon = _source(WEAPON_PATH)
        for taken in (
            "const int MCP_RPC_HANDS_TAKE = 78541063;",
            "const int MCP_INPUT_UDT_WEAPON_FIRE = 78541064;",
            "const int MCP_INPUT_UDT_WEAPON_RAISE = 78541065;",
        ):
            with self.subTest(taken=taken):
                self.assertIn(taken, weapon)
        values = []
        for path in sorted(SCRIPTS.rglob("*.c")):
            values += [int(v) for v in re.findall(r"(?m)^\s*const int \w+ = (-?\d+);", _source(path))]
        self.assertEqual(values.count(78541066), 1)
        # Vanilla INPUT_UDT_* end at 16 (_constants.c:2-19); the spike's id stays out.
        self.assertNotIn("78541091", "".join(_source(path) for path in SCRIPTS.rglob("*.c")))

    def test_the_request_is_sent_before_the_owner_moves_and_read_in_the_same_order(self) -> None:
        send = _method_body(self.source, SEND_START)
        _in_order(
            self,
            send,
            "if (!GetGame().IsMultiplayer())",
            "if (!GetGame().IsClient())",
            "if (!ScriptInputUserData.CanStoreInputUserData())",
            "message = new ScriptInputUserData();",
            "message.Write(MCP_INPUT_UDT_PLAYER_MOVE);",
            "message.Write(true);",
            "message.Write(request.move_id);",
            "message.Send();",
            "s_ServerReleasePending = false;",
            'return "sent";',
        )
        self.assertEqual(
            _if_body(send, "!ScriptInputUserData.CanStoreInputUserData()").strip(),
            'return "input_busy";',
        )
        self.assertEqual(_if_body(send, "!GetGame().IsMultiplayer()").strip(), 'return "unavailable";')
        self.assertEqual(_if_body(send, "!GetGame().IsClient()").strip(), 'return "unavailable";')
        written = re.findall(r"message\.Write\(request\.(\w+)\);", send)
        self.assertEqual(
            written,
            ["move_id", "phase", "speed", "direction", "angle_deg", "heading_deg", "target", "radius", "seconds"],
        )
        # The server reads the flag and the id first, then ReadRequest the rest.
        server_read = _method_body(self.source, SERVER_READ)
        _in_order(
            self,
            server_read,
            "if (!ctx.Read(isStart))",
            "if (!ctx.Read(moveId))",
            "if (!isStart)",
            "request.move_id = moveId;",
            "if (!MCPPlayerMoveControl.ReadRequest(ctx, request))",
            "MCPMoveAccept(request);",
        )
        read = _method_body(self.source, READ_REQUEST)
        local_of = {
            "phase": "phase", "speed": "speed", "direction": "direction", "angle_deg": "angleDeg",
            "heading_deg": "headingDeg", "target": "target", "radius": "radius", "seconds": "seconds",
        }
        self.assertEqual(
            re.findall(r"if \(!ctx\.Read\((\w+)\)\)", read), [local_of[name] for name in written[1:]]
        )
        for name, local in local_of.items():
            with self.subTest(field=name):
                self.assertIn(f"request.{name} = {local};", read)
        # Each read's type is the written member's type.
        members = dict((name, kind) for kind, name in _class_members(self.source, "MCPPlayerMoveRequest"))
        for name, local in local_of.items():
            with self.subTest(local=local):
                self.assertIn(f"\t\t{members[name]} {local};\n", read)

    def test_the_release_names_its_move_and_waits_for_a_free_channel(self) -> None:
        flush = _method_body(self.source, FLUSH)
        _in_order(
            self,
            flush,
            "if (!s_ServerReleasePending)",
            "if (!GetGame().IsMultiplayer() || !GetGame().IsClient())",
            "if (!GetGame().GetPlayer())",
            "if (!ScriptInputUserData.CanStoreInputUserData())",
            "message.Write(MCP_INPUT_UDT_PLAYER_MOVE);",
            "message.Write(false);",
            "message.Write(s_ServerReleaseId);",
            "message.Send();",
        )
        self.assertTrue(flush.rstrip().endswith("message.Send();\n\t\ts_ServerReleasePending = false;"))
        self.assertEqual(_if_body(flush, "!GetGame().GetPlayer()").strip(), "return;")
        self.assertEqual(_if_body(flush, "!ScriptInputUserData.CanStoreInputUserData()").strip(), "return;")
        release = _method_body(self.source, SERVER_READ_RELEASE)
        self.assertIn('MCPMoveNote(moveId, "release_without_move");', _if_body(release, "!m_MCPMoveActive"))
        self.assertIn(
            'MCPMoveNote(moveId, "release_of_another_move");',
            _if_body(release, "m_MCPMoveActive.move_id != moveId"),
        )
        self.assertTrue(release.rstrip().endswith('MCPMoveServerRelease("client");'))

    def test_the_server_consumes_its_type_and_stops_there(self) -> None:
        process = _method_body(self.source, SERVER_INPUT)
        branch = _if_body(process, "userDataType == MCP_INPUT_UDT_PLAYER_MOVE")
        self.assertEqual(_flat(branch), "MCPMoveReadRequest(ctx); return true;")
        self.assertTrue(
            process.rstrip().endswith("return super.OnInputUserDataProcess(userDataType, ctx);")
        )

    def test_the_server_rechecks_on_its_own_copy_before_it_accepts(self) -> None:
        accept = _method_body(self.source, SERVER_ACCEPT)
        _in_order(
            self,
            accept,
            'MCPMoveServerRelease("superseded");',
            "if (GetInstanceType() != DayZPlayerInstanceType.INSTANCETYPE_SERVER)",
            "refusal = MCPPlayerMoveControl.RequestRefusal(request);",
            "refusal = MCPPlayerMoveControl.StartRefusal(this);",
            "refusal = MCPPlayerMoveControl.DistanceRefusal(this, request);",
            'if (refusal != "")',
            "m_MCPMoveActive = request;",
            "m_MCPMoveDeadlineS = GetGame().GetTickTime() + request.seconds + "
            "MCPPlayerMoveControl.SERVER_DEADMAN_MARGIN_S;",
            "m_MCPMoveApplied = false;",
            'MCPMoveVerdict(true, request.move_id, "");',
        )
        self.assertIn(
            'MCPMoveVerdict(false, request.move_id, "not_server");',
            _if_body(accept, "GetInstanceType() != DayZPlayerInstanceType.INSTANCETYPE_SERVER"),
        )
        self.assertIn("MCPMoveVerdict(false, request.move_id, refusal);", _if_body(accept, 'refusal != ""'))
        # Accepting changes no override: the ticks apply them.
        self.assertNotIn("Enable(", accept)

    def test_the_server_applies_in_every_consumed_move_after_super(self) -> None:
        start = self.code.index(CONSUME)
        guard = self.code.rindex("#ifdef FEATURE_NETWORK_RECONCILIATION", 0, start)
        self.assertNotIn("#endif", self.code[guard:start])
        self.assertIn("#endif", self.code[start:])
        consume = _method_body(self.source, CONSUME)
        _in_order(
            self,
            consume,
            "super.ConsumeMove(pMove);",
            "if (m_MCPMoveActive)",
            "MCPMoveServerTick(true);",
        )
        self.assertEqual(self.code.count("ConsumeMove("), 2)

    def test_every_server_tick_checks_its_end_then_applies_both_overrides(self) -> None:
        tick = _method_body(self.source, SERVER_TICK)
        _in_order(
            self,
            tick,
            "if (!m_MCPMoveActive)",
            "refusal = MCPPlayerMoveControl.ActorRefusal(this);",
            "if (GetGame().GetTickTime() > m_MCPMoveDeadlineS)",
            "if (MCPPlayerMoveControl.HasArrived(this, m_MCPMoveActive))",
            "hic = GetInputController();",
            "angle = MCPPlayerMoveControl.RelativeAngleDeg(this, m_MCPMoveActive);",
            "MCPPlayerMoveControl.Enable(hic, m_MCPMoveActive.speed, angle);",
            "m_MCPMoveApplied = true;",
        )
        self.assertIn("MCPMoveServerRelease(refusal);", _if_body(tick, 'refusal != ""'))
        self.assertIn(
            'MCPMoveServerRelease("expired");', _if_body(tick, "GetGame().GetTickTime() > m_MCPMoveDeadlineS")
        )
        self.assertIn(
            'MCPMoveServerRelease("arrived");',
            _if_body(tick, "MCPPlayerMoveControl.HasArrived(this, m_MCPMoveActive)"),
        )
        self.assertIn("m_MCPMoveConsumed = m_MCPMoveConsumed + 1;", _if_body(tick, "fromConsume"))

    def test_the_server_disables_only_what_it_enabled_and_logs_once(self) -> None:
        release = _method_body(self.source, SERVER_RELEASE)
        self.assertEqual(_if_body(release, "!m_MCPMoveActive").strip(), "return;")
        _in_order(
            self,
            release,
            "moveId = m_MCPMoveActive.move_id;",
            "m_MCPMoveActive = null;",
            "if (m_MCPMoveApplied)",
            "MCPPlayerMoveControl.Disable(hic);",
            "m_MCPMoveApplied = false;",
            '"[DayZ_MCP] player_move server released move=" + moveId.ToString() + " reason=" + why;',
            "Print(line);",
        )
        verdict = _method_body(self.source, SERVER_VERDICT)
        self.assertIn('"[DayZ_MCP] player_move server accepted=0"', verdict)
        self.assertIn('"[DayZ_MCP] player_move server accepted=1"', verdict)
        self.assertIn('" move=" + moveId.ToString() + " reason=" + reason', verdict)

    def test_the_new_modded_player_base_keeps_entity_refs_out(self) -> None:
        self.assertNotIn("ref PlayerBase", self.code)
        self.assertIn("\tstatic PlayerBase s_Player;\n", self.source)
        # The owned-scalar census reads the bridges and MCPMessages.c only.
        self.assertNotIn("MCPResult", self.code)
        self.assertNotIn("MCPPlayerMove ", self.code.replace("MCPPlayerMoveRequest", ""))
        self.assertEqual(self.code.count("modded class PlayerBase"), 1)


class PlayerMoveRefusalContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(MOVE_PATH)

    def test_the_actor_refusals_and_their_order(self) -> None:
        actor = _method_body(self.source, ACTOR)
        _in_order(
            self,
            actor,
            'return "no_player";',
            'return "player_dead";',
            'return "player_unconscious";',
            'return "player_restrained";',
            'return "seated";',
            'return "no_input_controller";',
        )
        for condition, code in (
            ("!player", "no_player"),
            ("!player.IsAlive()", "player_dead"),
            ("player.IsUnconscious()", "player_unconscious"),
            ("player.IsRestrained()", "player_restrained"),
            ("player.IsInVehicle()", "seated"),
            ("!player.GetInputController()", "no_input_controller"),
        ):
            with self.subTest(code=code):
                self.assertEqual(_if_body(actor, condition).strip(), f'return "{code}";')

    def test_the_value_refusals_match_the_python_bounds(self) -> None:
        refusal = _method_body(self.source, REFUSAL)
        speed = "request.speed != 1.0 && request.speed != 2.0 && request.speed != 3.0"
        self.assertEqual(_if_body(refusal, speed).strip(), 'return "bad_speed";')
        self.assertEqual(sorted(player_move.SPEEDS.values()), [1.0, 2.0, 3.0])
        self.assertEqual(
            _if_body(refusal, 'request.phase != "hold" && request.phase != "press"').strip(),
            'return "bad_args";',
        )
        for condition, code in (
            ("!IsFiniteValue(request.angle_deg)", "bad_angle_deg"),
            ("request.angle_deg < -ANGLE_ABS_MAX_DEG", "bad_angle_deg"),
            ("request.angle_deg > ANGLE_ABS_MAX_DEG", "bad_angle_deg"),
            ("!IsFiniteValue(request.heading_deg)", "bad_heading_deg"),
            ("request.heading_deg < 0.0", "bad_heading_deg"),
            ("request.heading_deg >= HEADING_MAX_DEG", "bad_heading_deg"),
            ("!IsFiniteVector(request.target)", "bad_to"),
            ("!IsFiniteValue(request.radius)", "bad_arrive_radius_m"),
            ("request.radius < ARRIVE_RADIUS_MIN_M", "bad_arrive_radius_m"),
            ("request.radius > ARRIVE_RADIUS_MAX_M", "bad_arrive_radius_m"),
        ):
            with self.subTest(condition=condition):
                self.assertEqual(_if_body(refusal, condition).strip(), f'return "{code}";')
        self.assertTrue(refusal.rstrip().endswith("return SecondsRefusal(request.phase, request.seconds);"))
        seconds = _method_body(self.source, SECONDS)
        hold = _if_body(seconds, 'phase == "hold"')
        self.assertIn('code = "bad_hold_s";', hold)
        self.assertIn("maximum = HOLD_MAX_S;", hold)
        self.assertIn('code = "bad_ttl_s";', seconds)
        for condition in ("!IsFiniteValue(seconds)", "seconds <= 0.0", "seconds > maximum"):
            with self.subTest(condition=condition):
                self.assertEqual(_if_body(seconds, condition).strip(), "return code;")
        distance = _method_body(self.source, DISTANCE)
        self.assertIn(
            "if (HorizontalDistance(player.PhysicsGetPositionWS(), request.target) > TO_MAX_DISTANCE_M)",
            distance,
        )
        finite = _method_body(self.source, FINITE)
        for condition in ("value != value", "value >= float.MAX", "value <= -float.MAX"):
            with self.subTest(condition=condition):
                self.assertEqual(_if_body(finite, condition).strip(), "return false;")

    def test_bounds_are_one_python_copy_equal_to_the_enforce_one(self) -> None:
        found = dict(re.findall(r"static const float (\w+) = ([0-9.]+);", self.source))
        self.assertEqual(
            {name: float(value) for name, value in found.items()},
            {
                "HOLD_MAX_S": player_move.HOLD_MAX_S,
                "PRESS_MAX_TTL_S": player_move.PRESS_MAX_TTL_S,
                "ANGLE_ABS_MAX_DEG": player_move.ANGLE_ABS_MAX_DEG,
                "HEADING_MAX_DEG": player_move.HEADING_MAX_DEG,
                "ARRIVE_RADIUS_MIN_M": player_move.ARRIVE_RADIUS_MIN_M,
                "ARRIVE_RADIUS_MAX_M": player_move.ARRIVE_RADIUS_MAX_M,
                "TO_MAX_DISTANCE_M": player_move.TO_MAX_DISTANCE_M,
                "SERVER_DEADMAN_MARGIN_S": player_move.SERVER_DEADMAN_MARGIN_S,
            },
        )
        self.assertEqual(
            (player_move.HOLD_MAX_S, player_move.PRESS_MAX_TTL_S, player_move.TO_MAX_DISTANCE_M),
            (30.0, 30.0, 200.0),
        )
        self.assertEqual((player_move.ARRIVE_RADIUS_MIN_M, player_move.ARRIVE_RADIUS_MAX_M), (0.2, 5.0))
        self.assertIn("protected const float PLAYER_MOVE_JOB_SLACK_S = 5.0;", _source(CLIENT_PATH))
        self.assertEqual(player_move.HOLD_SLACK_S, 5.0)


class PlayerMoveDispatchContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)
        self.dispatch = _method_body(self.source, DISPATCH)

    def test_every_refusal_comes_before_anything_moves(self) -> None:
        _in_order(
            self,
            self.dispatch,
            "if (!m_JobRunner)",
            "if (GetGame().IsDedicatedServer())",
            "if (!command.args)",
            'if (args.mode != "hold" && args.mode != "press" && args.mode != "release")',
            "result.player_move = reply;",
            'if (args.mode == "release")',
            "if (!ArrayToVector(args.to, target))",
            "refusal = MCPPlayerMoveControl.RequestRefusal(request);",
            "FillPlayerMoveRequest(reply, request);",
            'if (MCPPlayerMoveControl.IsActive() || m_JobRunner.CountOfKind("player_move") > 0)',
            "refusal = MCPPlayerMoveControl.StartRefusal(player);",
            "refusal = MCPPlayerMoveControl.DistanceRefusal(player, request);",
            "request.move_id = MCPPlayerMoveControl.NextMoveId();",
            "serverRequest = MCPPlayerMoveControl.SendStart(request);",
            'if (serverRequest == "input_busy")',
            "nowS = GetGame().GetTickTime();",
            "generation = MCPPlayerMoveControl.Begin(player, request, nowS, serverRequest);",
            "FillPlayerMoveRecord(reply);",
            'if (args.mode == "press")',
            'job.kind = "player_move";',
            "m_JobRunner.AddJob(job);",
            "return false;",
        )
        self.assertIn(
            'result.error = "player_move_busy";',
            _if_body(self.dispatch, 'MCPPlayerMoveControl.IsActive() || m_JobRunner.CountOfKind("player_move") > 0'),
        )
        self.assertIn('result.error = "bad_to";', _if_body(self.dispatch, "!ArrayToVector(args.to, target)"))
        self.assertIn('result.error = "input_busy";', _if_body(self.dispatch, 'serverRequest == "input_busy"'))
        self.assertEqual(_flat(_if_body(self.dispatch, 'args.mode == "press"')), "result.ok = true; return true;")
        self.assertIn(
            "job.deadline_s = m_JobRunner.GetElapsedS() + PLAYER_MOVE_JOB_SLACK_S + request.seconds;",
            self.dispatch,
        )
        self.assertIn("request.seconds = args.hold_s;", _if_body(self.dispatch, 'args.mode == "hold"'))
        for reused in (
            "request.speed = args.speed;",
            "request.direction = args.direction;",
            "request.angle_deg = args.angle_deg;",
            "request.heading_deg = args.heading;",
            "request.radius = args.radius;",
            "request.seconds = args.hold_ttl_s;",
        ):
            with self.subTest(reused=reused):
                self.assertIn(reused, self.dispatch)

    def test_release_ends_the_active_move_or_answers_not_held(self) -> None:
        release = _method_body(self.source, RELEASE)
        _in_order(
            self,
            _if_body(release, "MCPPlayerMoveControl.IsActive()"),
            'MCPPlayerMoveControl.ReleaseAll("phase");',
            "FillPlayerMoveRelease(reply);",
            "result.ok = true;",
        )
        self.assertIn("FillPlayerMoveRelease(reply);", _if_body(release, "MCPPlayerMoveControl.HasReleased()"))
        self.assertTrue(release.rstrip().endswith('result.error = "not_held";\n\t\treturn true;'))

    def test_a_hold_answers_after_its_release_and_otherwise_aborts(self) -> None:
        process_job = _method_body(self.source, "override bool MCP_ProcessJob(MCPJob job)")
        self.assertEqual(
            _if_body(process_job, 'job.kind == "player_move"').strip(), "return ProcessPlayerMoveJob(job);"
        )
        process = _method_body(self.source, PROCESS)
        self.assertEqual(
            _if_body(process, "!MCPPlayerMoveControl.WasReleased(job.generation)").strip(), "return false;"
        )
        self.assertIn(
            'job.error = "aborted";', _if_body(process, 'releasedBy != "hold" && releasedBy != "arrived"')
        )
        success = _method_body(self.source, "override void MCP_PostJobSuccess(MCPJob job)")
        self.assertIn("PostPlayerMoveJob(job);", _if_body(success, 'job.kind == "player_move"'))
        failure = _method_body(self.source, "override void MCP_PostJobFailure(MCPJob job)")
        self.assertIn("PostPlayerMoveJob(job);", _if_body(failure, 'job && job.kind == "player_move"'))
        timeout = _method_body(self.source, "override void MCP_PostJobTimeout(MCPJob job)")
        self.assertIn("PostPlayerMoveTimeout(job);", _if_body(timeout, 'job.kind == "player_move"'))
        late = _method_body(self.source, TIMEOUT)
        stuck = "MCPPlayerMoveControl.IsActive() && MCPPlayerMoveControl.Generation() == job.generation"
        self.assertEqual(_if_body(late, stuck).strip(), 'MCPPlayerMoveControl.ReleaseAll("ttl");')
        _in_order(self, late, f"if ({stuck})", 'job.error = "aborted";', "PostPlayerMoveJob(job);")
        self.assertIn("result.player_move = job.player_move;", _method_body(self.source, POST))
        # A hold waits on its release; it does not make camera_set busy.
        exclusive = _method_body(self.source, "protected bool HasExclusiveJob()")
        self.assertIn('moveJobs = m_JobRunner.CountOfKind("player_move");', exclusive)
        self.assertIn("blocking = blocking - moveJobs;", exclusive)

    def test_the_answer_reports_start_release_and_horizontal_distance(self) -> None:
        record = _method_body(self.source, FILL_RECORD)
        for line in (
            "reply.move_id = MCPPlayerMoveControl.Generation();",
            "VectorToArray(MCPPlayerMoveControl.StartPos(), reply.start_pos);",
            "reply.start_tick = MCPPlayerMoveControl.StartTick();",
            "reply.start_tick_time_s = MCPPlayerMoveControl.StartS();",
            "reply.release_due_s = MCPPlayerMoveControl.DueS();",
            "reply.server_request = MCPPlayerMoveControl.ServerRequest();",
        ):
            with self.subTest(line=line):
                self.assertIn(line, record)
        _in_order(
            self,
            _method_body(self.source, FILL_RELEASE),
            "reply.released_by = MCPPlayerMoveControl.ReleasedBy();",
            "reply.release_tick = MCPPlayerMoveControl.ReleaseTick();",
            "reply.release_tick_time_s = MCPPlayerMoveControl.ReleaseTimeS();",
            "reply.arrived = MCPPlayerMoveControl.ReleaseArrived();",
            "if (!MCPPlayerMoveControl.ReleasePosKnown())",
            "VectorToArray(endPos, reply.end_pos);",
            "reply.distance_m = MCPPlayerMoveControl.HorizontalDistance(startPos, endPos);",
        )
        request = _method_body(self.source, FILL_REQUEST)
        self.assertIn("reply.speed = MCPPlayerMoveControl.SpeedName(request.speed);", request)
        self.assertIn("VectorToArray(request.target, reply.to);", _if_body(request, 'request.direction == "to"'))

    def test_the_bridge_answers_these_error_codes(self) -> None:
        self.assertEqual(_bridge_codes(), BRIDGE_CODES)

    def test_compile_hazards_locals_on_top_no_local_keyword_no_ternary(self) -> None:
        every_script = "".join(_source(path) for path in SCRIPTS.rglob("*.c"))
        type_names = set(re.findall(r"\bclass\s+(\w+)", every_script)) | set(_LOCAL_TYPES)
        type_names |= {"Math", "ParamsReadContext", "PawnMove", "DayZPlayerConstants"}
        for text, signatures in ((self.source, CLIENT_METHODS), (_source(MOVE_PATH), MOVE_METHODS)):
            for signature in signatures:
                with self.subTest(method=signature):
                    body = _method_body(text, signature)
                    names = _locals_declared_at_top(body)
                    self.assertFalse(set(names) & type_names, names)
                    code = _without_comments(body)
                    self.assertIsNone(re.search(r"\blocal\b", code))
                    self.assertNotIn("?", code)
                    self.assertNotIn("GetGame().GetPlayer() ==", body)
                    self.assertNotIn("GetGame().GetPlayer() !=", body)
        for class_name in ("MCPPlayerMoveRequest",):
            fields = {field for _type, field in _class_members(_source(MOVE_PATH), class_name)}
            self.assertFalse(fields & type_names, fields & type_names)
        fields = {field for _type, field in _class_members(_source(MESSAGES_PATH), "MCPPlayerMove")}
        self.assertFalse(fields & type_names, fields & type_names)
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a = 0;\n\t\ta = 1;\n\t\tint b = 0;\n")
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a;\n\t\tint a;\n")


class PlayerMovePayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = _source(MESSAGES_PATH)

    def test_args_add_two_fields_and_reuse_the_rest(self) -> None:
        members = _class_members(self.messages, "MCPArgs")
        for member in (
            ("string", "direction"),
            ("float", "angle_deg"),
            ("string", "mode"),
            ("float", "speed"),
            ("float", "heading"),
            ("ref array<float>", "to"),
            ("float", "radius"),
            ("float", "hold_s"),
            ("float", "hold_ttl_s"),
        ):
            with self.subTest(member=member):
                self.assertEqual(members.count(member), 1)
        constructor = _method_body(_without_comments(self.messages), "void MCPArgs()")
        for field in ("direction", "angle_deg"):
            with self.subTest(field=field):
                self.assertIsNone(re.search(rf"\b{field}\s*=", constructor))
        # No other MCPArgs field travels for this verb: nothing new beside these two.
        self.assertNotIn(("bool", "mirror_to_server"), members)

    def test_the_request_and_reply_classes_carry_the_contract_members(self) -> None:
        self.assertEqual(_class_members(_source(MOVE_PATH), "MCPPlayerMoveRequest"), EXPECTED_REQUEST_MEMBERS)
        self.assertEqual(_class_members(self.messages, "MCPPlayerMove"), EXPECTED_REPLY_MEMBERS)
        self.assertEqual(set(_reply()), {name for _type, name in EXPECTED_REPLY_MEMBERS})
        constructor = _flat(_method_body(_without_comments(self.messages), "void MCPPlayerMove()"))
        self.assertEqual(
            constructor,
            "to = new array<float>(); start_pos = new array<float>(); end_pos = new array<float>(); "
            "move_id = -1; distance_m = -1.0; start_tick = -1; release_tick = -1; "
            "start_tick_time_s = -1.0; release_tick_time_s = -1.0; release_due_s = -1.0;",
        )
        for owner in ("MCPResult", "MCPJob"):
            with self.subTest(owner=owner):
                self.assertEqual(_class_members(self.messages, owner).count(("ref MCPPlayerMove", FIELD)), 1)
        # player_trace stays the last MCPResult member; this one sits just before it.
        result_members = _class_members(self.messages, "MCPResult")
        self.assertEqual(
            result_members[-2:],
            [("ref MCPPlayerMove", FIELD), ("ref MCPPlayerTraceRead", "player_trace")],
        )
        self.assertLess(self.messages.index("class MCPPlayerMove"), self.messages.index("class MCPResult"))

    def test_the_reply_is_prunable_and_a_refusal_keeps_its_zero_facts(self) -> None:
        self.assertIn(FIELD, result_prune.PRUNABLE_FIELDS)
        self.assertEqual(
            result_prune.PRUNABLE_FIELDS[-2:], (FIELD, "player_trace")
        )
        self.assertNotIn(FIELD, result_prune.OWNED_SCALAR_FIELDS)
        unfilled = result_prune.prune_unfilled_fields("world_spawn", {"ok": 1, FIELD: {}})
        self.assertNotIn(FIELD, unfilled)
        self.assertEqual(unfilled["ok"], 1)
        filled = {"phase": "hold", "distance_m": 0.0, "arrived": 0, "command_ticks": 0}
        kept = result_prune.prune_unfilled_fields(COMMAND, {"ok": 1, FIELD: dict(filled)})
        self.assertEqual(kept[FIELD], filled)


class PlayerMoveModuleTest(unittest.TestCase):
    def _normalize(self, **kwargs: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "speed": "walk",
            "phase": "hold",
            "hold_s": None,
            "ttl_s": None,
            "angle_deg": None,
            "heading_deg": None,
            "to": None,
            "arrive_radius_m": player_move.ARRIVE_RADIUS_DEFAULT_M,
        }
        arguments.update(kwargs)
        return player_move.normalize_request(**arguments)

    def test_each_phase_and_direction_builds_exactly_its_variant(self) -> None:
        cases = (
            ({"phase": "release"}, {"mode": "release"}),
            ({"hold_s": 5.0}, {"mode": "hold", "speed": 1.0, "hold_s": 5.0, "direction": "angle", "angle_deg": 0.0}),
            (
                {"hold_s": 1, "angle_deg": -45, "speed": "sprint"},
                {"mode": "hold", "speed": 3.0, "hold_s": 1.0, "direction": "angle", "angle_deg": -45.0},
            ),
            (
                {"phase": "press", "ttl_s": 2.5, "heading_deg": 90, "speed": "jog"},
                {"mode": "press", "speed": 2.0, "hold_ttl_s": 2.5, "direction": "heading", "heading": 90.0},
            ),
            (
                {"hold_s": 30.0, "to": [1, 2, 3], "arrive_radius_m": 1.5},
                {"mode": "hold", "speed": 1.0, "hold_s": 30.0, "direction": "to", "to": [1.0, 2.0, 3.0], "radius": 1.5},
            ),
        )
        for kwargs, wire in cases:
            with self.subTest(kwargs=kwargs):
                built = self._normalize(**kwargs)
                self.assertEqual(built, wire)
                self.assertEqual(loopback.validate_command_args(COMMAND, built), (True, None))

    def test_bad_values_name_their_code_and_argument(self) -> None:
        nan = float("nan")
        inf = float("inf")
        cases = (
            ({"hold_s": 1.0, "speed": "run"}, "bad_speed: speed"),
            ({"hold_s": 1.0, "speed": 1}, "bad_speed: speed"),
            ({"hold_s": 1.0, "angle_deg": 180.5}, "bad_angle_deg: angle_deg"),
            ({"hold_s": 1.0, "angle_deg": nan}, "bad_angle_deg: angle_deg"),
            ({"hold_s": 1.0, "angle_deg": -inf}, "bad_angle_deg: angle_deg"),
            ({"hold_s": 1.0, "angle_deg": True}, "bad_angle_deg: angle_deg"),
            ({"hold_s": 1.0, "heading_deg": 360.0}, "bad_heading_deg: heading_deg"),
            ({"hold_s": 1.0, "heading_deg": nan}, "bad_heading_deg: heading_deg"),
            ({"hold_s": 1.0, "heading_deg": inf}, "bad_heading_deg: heading_deg"),
            ({"hold_s": 1.0, "to": [1.0, 2.0]}, "bad_to: to"),
            ({"hold_s": 1.0, "to": [1.0, nan, 2.0]}, "bad_to: to"),
            ({"hold_s": 1.0, "to": [inf, 0.0, 2.0]}, "bad_to: to"),
            ({"hold_s": 1.0, "to": (1.0, 2.0, 3.0)}, "bad_to: to"),
            ({"hold_s": 1.0, "arrive_radius_m": nan}, "bad_arrive_radius_m: arrive_radius_m"),
            ({"hold_s": 1.0, "arrive_radius_m": inf}, "bad_arrive_radius_m: arrive_radius_m"),
            ({"hold_s": 1.0, "arrive_radius_m": 0.19}, "bad_arrive_radius_m: arrive_radius_m"),
            ({}, "bad_hold_s: hold_s"),
            ({"hold_s": nan}, "bad_hold_s: hold_s"),
            ({"hold_s": inf}, "bad_hold_s: hold_s"),
            ({"hold_s": 0.0}, "bad_hold_s: hold_s"),
            ({"hold_s": 30.01}, "bad_hold_s: hold_s"),
            ({"phase": "press"}, "bad_ttl_s: ttl_s"),
            ({"phase": "press", "ttl_s": nan}, "bad_ttl_s: ttl_s"),
            ({"phase": "press", "ttl_s": -inf}, "bad_ttl_s: ttl_s"),
            ({"phase": "press", "ttl_s": 31}, "bad_ttl_s: ttl_s"),
            ({"phase": "walk", "hold_s": 1.0}, "bad_args: phase"),
            ({"hold_s": 1.0, "angle_deg": 0.0, "heading_deg": 0.0}, "bad_args: direction"),
            ({"hold_s": 1.0, "heading_deg": 0.0, "to": [0.0, 0.0, 0.0]}, "bad_args: direction"),
            ({"hold_s": 1.0, "angle_deg": nan, "to": [0.0, 0.0, 0.0]}, "bad_args: direction"),
            ({"hold_s": 1.0, "ttl_s": 1.0}, "bad_args: ttl_s"),
            ({"phase": "press", "ttl_s": 1.0, "hold_s": 1.0}, "bad_args: hold_s"),
            ({"phase": "release", "heading_deg": 1.0}, "bad_args: heading_deg"),
        )
        for kwargs, prefix in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError) as raised:
                    self._normalize(**kwargs)
                self.assertTrue(str(raised.exception).startswith(prefix), str(raised.exception))

    def test_the_answer_keeps_only_its_direction_and_types_arrived(self) -> None:
        for direction, kept, dropped in (
            ("angle", {"angle_deg"}, {"heading_deg", "to", "arrive_radius_m"}),
            ("heading", {"heading_deg"}, {"angle_deg", "to", "arrive_radius_m"}),
            ("to", {"to", "arrive_radius_m"}, {"angle_deg", "heading_deg"}),
        ):
            with self.subTest(direction=direction):
                answer = player_move.normalize_bridge_result(
                    {"ok": 1, FIELD: _reply(direction=direction, arrived=1, to=[1.0, 2.0, 3.0])}
                )[FIELD]
                self.assertTrue(kept <= set(answer), answer)
                self.assertFalse(dropped & set(answer), answer)
                self.assertIs(answer["arrived"], True)
                self.assertEqual(answer["server_request"], "sent")
        for bad in (2, "1", None, 0.5):
            with self.subTest(arrived=bad):
                with self.assertRaises(ValueError) as raised:
                    player_move.normalize_bridge_result({"ok": 1, FIELD: _reply(arrived=bad)})
                self.assertEqual(str(raised.exception), "bad_bridge_move_boolean")
        for broken in (None, [], {"ok": 1}, {"ok": 1, FIELD: []}):
            with self.subTest(broken=broken):
                with self.assertRaises(ValueError) as raised:
                    player_move.normalize_bridge_result(broken)
                self.assertEqual(str(raised.exception), "bad_bridge_move_result")


class PlayerMoveToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _description(self) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[COMMAND].description or ""

    async def test_description_states_the_contract_and_its_limits(self) -> None:
        description = await self._description()
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        for fragment in (
            "with no OS input and no window focus, through the engine's own move command",
            "OverrideMovementSpeed and OverrideMovementAngle ENABLED from the local player's "
            "CommandHandler every tick",
            "the server applies the same two overrides to its copy of the player in every move "
            "it consumes (ConsumeMove) and in its CommandHandler",
            "Each side sets DISABLED only what it enabled",
            "The camera is not turned and nothing is teleported or aligned.",
            "speed is walk, jog or sprint (OverrideMovementSpeed 1, 2, 3)",
            "angle_deg in [-180, 180], degrees relative to the current heading, 0 straight ahead",
            "heading_deg in [0, 360), a compass heading (0 north = +Z, 90 east = +X)",
            "arrive_radius_m (0.2..5, default 0.5, used only with to) and at most 200 m away (bad_to)",
            "None means angle_deg=0; more than one is bad_args",
            "on each side from its own heading and position",
            "taken as degrees, positive to the right, from HumanCommandMove's movement angles "
            "(human.c:439-445): not yet measured in game",
            "can circle the point",
            "phase=hold (default) moves until hold_s (required, 0 < hold_s <= 30)",
            "the tool waits at least hold_s + 5 s",
            "phase=press starts and answers at once with release_due_s",
            "its ttl_s (required, 0 < ttl_s <= 30)",
            "One move at a time",
            "not_in_move_command",
            "its own deadman (the move's hold_s or ttl_s plus 0.5 s)",
            "[DayZ_MCP] player_move server accepted=0|1 move=<move_id> reason=...",
            "server_request=sent means the request left the client, not that the server accepted it",
            "A full input channel is input_busy and moves neither side.",
            "player_trace's monotonic_s",
            "player_trace's tick",
            "query_all_players",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        released = description[description.index("released_by (") :]
        for cause in sorted(RELEASED_BY):
            with self.subTest(cause=cause):
                self.assertIn(cause, released)

    async def test_description_names_every_error_the_bridge_can_return(self) -> None:
        description = await self._description()
        listed = description[description.index("Errors: ") :]
        for code in sorted(_bridge_codes()):
            with self.subTest(code=code):
                self.assertIn(code, listed)

    async def test_the_tool_sends_exactly_its_variant_on_the_client(self) -> None:
        cases = (
            (
                {"hold_s": 5.0},
                {"mode": "hold", "speed": 1.0, "hold_s": 5.0, "direction": "angle", "angle_deg": 0.0},
                10.0,
            ),
            (
                {"hold_s": 2, "angle_deg": -45, "speed": "sprint"},
                {"mode": "hold", "speed": 3.0, "hold_s": 2.0, "direction": "angle", "angle_deg": -45.0},
                7.0,
            ),
            (
                {"phase": "press", "ttl_s": 30, "heading_deg": 90.0, "speed": "jog"},
                {"mode": "press", "speed": 2.0, "hold_ttl_s": 30.0, "direction": "heading", "heading": 90.0},
                1.0,
            ),
            (
                {"hold_s": 30.0, "to": [100.0, 5.0, 200.0], "arrive_radius_m": 1.5},
                {"mode": "hold", "speed": 1.0, "hold_s": 30.0, "direction": "to",
                 "to": [100.0, 5.0, 200.0], "radius": 1.5},
                35.0,
            ),
            ({"phase": "release"}, {"mode": "release"}, 1.0),
        )
        for arguments, wire, timeout in cases:
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1, FIELD: _reply()})
                with patch.object(self.runtime, "call_bridge", new=call):
                    await self.app.call_tool(COMMAND, {**arguments, "timeout_s": 1.0})
                call.assert_awaited_once_with(COMMAND, wire, "client", timeout)
                self.assertEqual(loopback.validate_command_args(COMMAND, wire), (True, None))
        # A longer timeout than hold_s + 5 is kept, and the default is the tool default.
        call = AsyncMock(return_value={"ok": 1, FIELD: _reply()})
        with patch.object(self.runtime, "call_bridge", new=call):
            await self.app.call_tool(COMMAND, {"hold_s": 1.0, "timeout_s": 20.0})
        self.assertEqual(call.await_args.args[3], 20.0)
        call = AsyncMock(return_value={"ok": 1, FIELD: _reply()})
        with patch.object(self.runtime, "call_bridge", new=call):
            await self.app.call_tool(COMMAND, {"phase": "press", "ttl_s": 1.0})
        self.assertEqual(call.await_args.args[3], DEFAULT_TOOL_TIMEOUT_S)

    async def test_bad_arguments_raise_before_the_bridge(self) -> None:
        nan = float("nan")
        inf = float("inf")
        cases = (
            ({"hold_s": 1.0, "speed": "run"}, "bad_speed: speed"),
            ({"hold_s": 1.0, "speed": "WALK"}, "bad_speed: speed"),
            ({"hold_s": 1.0, "angle_deg": 180.5}, "bad_angle_deg: angle_deg"),
            ({"hold_s": 1.0, "angle_deg": nan}, "bad_angle_deg: angle_deg"),
            ({"hold_s": 1.0, "angle_deg": inf}, "bad_angle_deg: angle_deg"),
            ({"hold_s": 1.0, "heading_deg": -0.1}, "bad_heading_deg: heading_deg"),
            ({"hold_s": 1.0, "heading_deg": nan}, "bad_heading_deg: heading_deg"),
            ({"hold_s": 1.0, "heading_deg": -inf}, "bad_heading_deg: heading_deg"),
            ({"hold_s": 1.0, "to": [1.0, 2.0]}, "bad_to: to"),
            ({"hold_s": 1.0, "to": [1.0, nan, 2.0]}, "bad_to: to"),
            ({"hold_s": 1.0, "to": [inf, 0.0, 2.0]}, "bad_to: to"),
            ({"hold_s": 1.0, "arrive_radius_m": 5.5}, "bad_arrive_radius_m: arrive_radius_m"),
            ({"hold_s": 1.0, "arrive_radius_m": nan}, "bad_arrive_radius_m: arrive_radius_m"),
            ({"hold_s": 1.0, "to": [1.0, 2.0, 3.0], "arrive_radius_m": inf}, "bad_arrive_radius_m: arrive_radius_m"),
            ({}, "bad_hold_s: hold_s"),
            ({"hold_s": 30.5}, "bad_hold_s: hold_s"),
            ({"hold_s": nan}, "bad_hold_s: hold_s"),
            ({"hold_s": inf}, "bad_hold_s: hold_s"),
            ({"phase": "press"}, "bad_ttl_s: ttl_s"),
            ({"phase": "press", "ttl_s": -1.0}, "bad_ttl_s: ttl_s"),
            ({"phase": "press", "ttl_s": nan}, "bad_ttl_s: ttl_s"),
            ({"phase": "press", "ttl_s": inf}, "bad_ttl_s: ttl_s"),
            ({"phase": "walk", "hold_s": 1.0}, "bad_args: phase"),
            ({"hold_s": 1.0, "angle_deg": 10.0, "heading_deg": 90.0}, "bad_args: direction"),
            ({"hold_s": 1.0, "angle_deg": 10.0, "to": [1.0, 2.0, 3.0]}, "bad_args: direction"),
            ({"hold_s": 1.0, "heading_deg": 10.0, "to": [1.0, 2.0, 3.0]}, "bad_args: direction"),
            ({"hold_s": 1.0, "ttl_s": 1.0}, "bad_args: ttl_s"),
            ({"phase": "press", "ttl_s": 1.0, "hold_s": 1.0}, "bad_args: hold_s"),
            ({"phase": "release", "hold_s": 1.0}, "bad_args: hold_s"),
            ({"phase": "release", "to": [1.0, 2.0, 3.0]}, "bad_args: to"),
            # A mistyped direction must not walk straight ahead: the schema is closed.
            ({"hold_s": 1.0, "heading": 90.0}, "bad_args: unexpected arguments: heading"),
            ({"hold_s": 1.0, "mirror_to_server": False}, "bad_args: unexpected arguments: mirror_to_server"),
        )
        for arguments, prefix in cases:
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1, FIELD: _reply()})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, {**arguments, "timeout_s": 1.0})
                self.assertTrue(
                    _tool_error_text(raised.exception).startswith(prefix), _tool_error_text(raised.exception)
                )
                call.assert_not_awaited()
        # Schema-level refusals never reach the handler either.
        for arguments in (
            {"hold_s": True},
            {"hold_s": 1.0, "speed": 1},
            {"hold_s": 1.0, "to": [1.0, True, 3.0]},
            {"hold_s": 1.0, "angle_deg": "0"},
            {"hold_s": "1"},
        ):
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError):
                        await self.app.call_tool(COMMAND, {**arguments, "timeout_s": 1.0})
                call.assert_not_awaited()

    async def test_the_answer_keeps_only_its_direction_and_types_arrived(self) -> None:
        call = AsyncMock(return_value={"ok": 1, FIELD: _reply(direction="heading", heading_deg=90.0, arrived=0)})
        with patch.object(self.runtime, "call_bridge", new=call):
            _blocks, structured = await self.app.call_tool(COMMAND, {"hold_s": 1.0, "heading_deg": 90.0})
        answer = structured[FIELD]
        self.assertEqual(answer["heading_deg"], 90.0)
        self.assertNotIn("angle_deg", answer)
        self.assertNotIn("to", answer)
        self.assertIs(answer["arrived"], False)
        self.assertEqual(answer["release_tick_time_s"], 105.01)
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(COMMAND, {"hold_s": 1.0})
        self.assertIn("bad_bridge_move_result", str(raised.exception))


class PlayerMoveErrorDetailTest(unittest.TestCase):
    def test_not_held_and_aborted_carry_released_by(self) -> None:
        for code, cause in (
            ("not_held", "ttl"),
            ("not_held", "arrived"),
            ("aborted", "player_changed"),
            ("aborted", "phase"),
        ):
            with self.subTest(code=code, cause=cause):
                wire = {"ok": 0, "error": code, FIELD: _reply(released_by=cause)}
                self.assertEqual(str(server._bridge_error(wire, COMMAND)), f"{code}; observed=released_by={cause}")
        never = {"ok": 0, "error": "not_held", FIELD: _reply(released_by="")}
        self.assertEqual(str(server._bridge_error(never, COMMAND)), "not_held")

    def test_detail_is_gated_on_verb_and_code_and_leaks_nothing(self) -> None:
        busy = {"ok": 0, "error": "player_move_busy", FIELD: _reply(released_by="ttl")}
        self.assertEqual(str(server._bridge_error(busy, COMMAND)), "player_move_busy")
        seated = {"ok": 0, "error": "seated", FIELD: _reply()}
        self.assertEqual(str(server._bridge_error(seated, COMMAND)), "seated")
        forged = {"ok": 0, "error": "aborted", FIELD: _reply(released_by="rm -rf C:\\")}
        self.assertEqual(str(server._bridge_error(forged, COMMAND)), "aborted")
        other_verb = {"ok": 0, "error": "not_held", FIELD: _reply(released_by="ttl")}
        self.assertEqual(str(server._bridge_error(other_verb, "input_trigger")), "not_held")
        self.assertEqual(str(server._bridge_error({"ok": 0, "error": "not_held"}, COMMAND)), "not_held")


class VanillaPremiseTest(unittest.TestCase):
    """The premises, read from the vanilla scripts: they hold with or without this verb."""

    def _vanilla(self, relative: str) -> str:
        path = VANILLA / relative
        if not path.is_file():
            self.skipTest(f"vanilla {relative} not mounted")
        return path.read_text(encoding="utf-8", errors="replace")

    def test_the_overrides_and_their_types(self) -> None:
        human = self._vanilla(r"3_game\human.c")
        for pattern in (
            r"proto native\s+void\s+OverrideMovementSpeed\(HumanInputControllerOverrideType overrideType, float value\);",
            r"proto native\s+void\s+OverrideMovementAngle\(HumanInputControllerOverrideType overrideType, float value\);",
            r"proto native\s+float\s+GetHeadingAngle\(\);",
            r"proto native\s+int\s+GetCurrentCommandID\(\);",
            r"proto native\s+vector\s+PhysicsGetPositionWS\(\);",
            r"proto native\s+HumanInputController\s+GetInputController\(\);",
        ):
            with self.subTest(pattern=pattern):
                self.assertRegex(human, pattern)
        enum = human[human.index("enum HumanInputControllerOverrideType") :][:300]
        self.assertIn("ENABLED,\t\t//! Permenantly active until DISABLED is passed", enum)
        self.assertIn("returns main heading angle (in radians)  -PI .. PI", human)
        self.assertIn("-180 -90 0 90 180 angles of input movement", human)

    def test_consume_move_is_the_authoritys_event_and_the_feature_is_on(self) -> None:
        pawn = self._vanilla(r"3_game\entities\pawn.c")
        self.assertTrue(pawn.startswith("#ifdef FEATURE_NETWORK_RECONCILIATION"))
        self.assertIn("protected event void ConsumeMove(PawnMove pMove)", pawn)
        self.assertIn("Apply the moves inputs on the server", pawn)
        defines = self._vanilla(r"1_core\defines.c")
        self.assertRegex(defines, r"(?m)^#define FEATURE_NETWORK_RECONCILIATION\s*$")
        player = self._vanilla(r"4_world\entities\manbase\playerbase.c")
        implement = self._vanilla(r"4_world\entities\dayzplayerimplement.c")
        self.assertNotIn("ConsumeMove", player)
        self.assertNotIn("ConsumeMove", implement)

    def test_the_request_channel_and_its_types(self) -> None:
        gameplay = self._vanilla(r"3_game\gameplay.c")
        self.assertIn("proto native static bool CanStoreInputUserData ();", gameplay)
        serializer = self._vanilla(r"1_core\proto\serializer.c")
        self.assertIn("primitive types: int, float, string, bool, vector", serializer)

    def test_the_heading_convention(self) -> None:
        misc = self._vanilla(r"4_world\static\miscgameplayfunctions.c")
        self.assertIn("dir[0] = Math.Cos(headingAngle + Math.PI_HALF);", misc)
        self.assertIn("dir[2] = Math.Sin(headingAngle + Math.PI_HALF);", misc)
        crosshair = self._vanilla(r"5_mission\gui\crosshairselector.c")
        self.assertIn("hic.GetHeadingAngle() * -Math.RAD2DEG", crosshair)

    def test_seated_is_the_vehicle_command_or_a_transport_parent(self) -> None:
        implement = self._vanilla(r"4_world\entities\dayzplayerimplement.c")
        self.assertIn(
            "return m_MovementState.m_CommandTypeId == DayZPlayerConstants.COMMANDID_VEHICLE "
            "|| (GetParent() != null && GetParent().IsInherited(Transport));",
            implement,
        )


if __name__ == "__main__":
    unittest.main()
