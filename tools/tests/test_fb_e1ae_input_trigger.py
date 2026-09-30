"""fb-20260927-171553-e1ae part 2: input_trigger, a key to the script key handlers.

kind=key presses a DIK code through DayZGame.OnKeyPress (entry=game) or
Mission.OnKeyPress (entry=mission) and releases it through the matching
OnKeyRelease: click on the next tick, hold after hold_s, press on phase=release,
its TTL, restore_gameplay, a player change or death, or shutdown. kind=input
resolves a UAInput and always refuses, because DayZ 1.29 has no script setter
for UAInput.Local*. These tests pin the wire contract and the source shape the
game will compile. They do not launch DayZ; the verb is tested in game after
the promotion.
"""
from __future__ import annotations

import json
import re
import typing
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.server import (
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census


COMMAND = "input_trigger"
SCRIPTS = addon_root() / "scripts"
CLIENT_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)

# The eight wire shapes, written out here rather than derived from loopback.
KEY_BASE = {"trigger_kind": "key", "trigger_entry": "game", "dik": 1}
INPUT_BASE = {"trigger_kind": "input", "name": "UAGear"}
EDGE_EXTRA = {
    "click": {},
    "release": {},
    "hold": {"hold_s": 1.5},
    "press": {"hold_ttl_s": 2.0},
}
EXPECTED_REQUIRED = {
    frozenset({"trigger_kind", "trigger_edge", "trigger_entry", "dik"}),
    frozenset({"trigger_kind", "trigger_edge", "trigger_entry", "dik", "hold_s"}),
    frozenset({"trigger_kind", "trigger_edge", "trigger_entry", "dik", "hold_ttl_s"}),
    frozenset({"trigger_kind", "trigger_edge", "name"}),
    frozenset({"trigger_kind", "trigger_edge", "name", "hold_s"}),
    frozenset({"trigger_kind", "trigger_edge", "name", "hold_ttl_s"}),
}

EXPECTED_REPLY_MEMBERS = [
    ("string", "kind"),
    ("string", "entry"),
    ("string", "phase"),
    ("int", "dik"),
    ("string", "name"),
    ("int", "input_id"),
    ("bool", "delivered_press"),
    ("bool", "delivered_release"),
    ("int", "press_tick"),
    ("int", "release_tick"),
    ("float", "release_due_s"),
    ("float", "tick_time_s"),
    ("string", "released_by"),
    ("bool", "menu_open"),
    ("bool", "exists"),
    ("bool", "locked"),
    ("bool", "in_active_inputs"),
    ("string", "reason"),
]
RELEASED_BY = ("phase", "ttl", "restore", "player_changed", "shutdown")

# The methods this verb adds to MCPClientBridge.c, by unique signature.
DISPATCH = "protected bool DispatchInputTrigger(MCPCommand command, MCPResult result)"
TIMES_OK = "protected bool InputTriggerTimesOk(MCPArgs args)"
KEY = "protected bool DispatchInputTriggerKey("
RELEASE = "protected bool ReleaseInputTriggerKey("
FILL = "protected void FillInputTriggerRelease("
INPUT = "protected bool DispatchInputTriggerInput("
DENYLIST = "protected bool IsVanillaForcedInput("
PROCESS = "protected bool ProcessInputTriggerJob("
POST = "protected void PostInputTriggerJob("
TIMEOUT = "protected void PostInputTriggerTimeout("
DELIVER = "static bool Deliver(string entry, int dik, bool press)"
PRESS = "static int Press("
RELEASE_ALL = "static void ReleaseAll(string why)"
MAINTAIN = "static void MaintainFromTick()"
EXIT_COMBO = "static bool IsExitComboKey(int dik)"
NEW_METHODS = (
    DISPATCH,
    TIMES_OK,
    KEY,
    RELEASE,
    FILL,
    INPUT,
    DENYLIST,
    PROCESS,
    POST,
    TIMEOUT,
    DELIVER,
    PRESS,
    RELEASE_ALL,
    MAINTAIN,
    EXIT_COMBO,
)

# The would_request_exit guard, evaluated clause by clause for a scenario. An
# unknown clause is a KeyError, so a new condition cannot pass unread. The
# round-1 clauses are known too, so that guard runs here and fails the physical
# Alt scenarios instead of stopping at a missing method (review R1 F1).
_EXIT_GUARD_CLAUSES = {
    'entry == "game"': lambda state: state["entry"] == "game",
    'edge != "release"': lambda state: state["edge"] != "release",
    "MCPInputTriggerControl.IsExitComboKey(dik)": lambda state: state["dik"] in state["exit_keys"],
    "dik == KeyCode.KC_F4": lambda state: state["dik"] == "KC_F4",
    "MCPInputTriggerControl.HoldsAlt()": lambda state: state["verb_holds_alt"],
}


def _exit_scenario(entry: str, edge: str, dik: str, physical_alt: bool) -> dict[str, object]:
    # The verb holds no Alt in any scenario: a physical Alt is the case the
    # bridge cannot see (DayZGame.m_IsLeftAltHolding is private, dayzgame.c:933).
    return {
        "entry": entry,
        "edge": edge,
        "dik": dik,
        "physical_alt": physical_alt,
        "verb_holds_alt": False,
    }


# (label, scenario, refused as would_request_exit)
EXIT_GUARD_SCENARIOS = (
    ("physical_alt_synthetic_f4_click", _exit_scenario("game", "click", "KC_F4", True), True),
    ("physical_alt_synthetic_f4_hold", _exit_scenario("game", "hold", "KC_F4", True), True),
    ("physical_alt_synthetic_f4_press", _exit_scenario("game", "press", "KC_F4", True), True),
    ("no_alt_synthetic_f4_click", _exit_scenario("game", "click", "KC_F4", False), True),
    # The mirror: a synthetic Alt left down on entry game arms a physical F4.
    ("synthetic_left_alt_press", _exit_scenario("game", "press", "KC_LMENU", False), True),
    ("synthetic_left_alt_hold", _exit_scenario("game", "hold", "KC_LMENU", False), True),
    ("synthetic_right_alt_click", _exit_scenario("game", "click", "KC_RMENU", False), True),
    # entry mission never reaches DayZGame's flags or its exit check.
    ("physical_alt_mission_f4_click", _exit_scenario("mission", "click", "KC_F4", True), False),
    ("mission_left_alt_press", _exit_scenario("mission", "press", "KC_LMENU", False), False),
    # Nothing on entry game can hold F4 or Alt, so a release is not_held.
    ("game_release_of_f4", _exit_scenario("game", "release", "KC_F4", True), False),
    ("physical_alt_game_escape_click", _exit_scenario("game", "click", "KC_ESCAPE", True), False),
)


def _exit_guard_condition(source: str) -> str:
    """The condition of the single if whose body refuses would_request_exit."""
    body = _method_body(source, KEY)
    found = re.findall(
        r'if \(([^\n]+)\)\n\s*\{\n\s*result\.ok = false;\n\s*result\.error = "would_request_exit";',
        body,
    )
    if len(found) != 1:
        raise AssertionError(f"{len(found)} would_request_exit guards in DispatchInputTriggerKey")
    return found[0]


def _exit_combo_keys(source: str) -> set[str]:
    """KeyCode names IsExitComboKey accepts; empty when the helper is absent."""
    if EXIT_COMBO not in source:
        return set()
    return set(re.findall(r"if \(dik == KeyCode\.(KC_\w+)\)", _method_body(source, EXIT_COMBO)))

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
# A local declaration of one of the types these methods use.
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:string|int|float|bool|Mission|PlayerBase|UIManager|MCPArgs|MCPJob|"
    r"MCPResult|MCPInputTrigger|UAInputAPI|UAInput|TIntArray)\s+(\w+)\s*(?:=[^;]*)?;\s*$"
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


def _wire(kind: str, edge: str, **overrides: object) -> dict[str, object]:
    args: dict[str, object] = dict(KEY_BASE if kind == "key" else INPUT_BASE)
    args["trigger_edge"] = edge
    args.update(EDGE_EXTRA[edge])
    args.update(overrides)
    return args


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


def _not_drivable_wire() -> dict[str, object]:
    return {
        "ok": 0,
        "error": "input_not_drivable",
        "input_trigger": {
            "kind": "input",
            "entry": "",
            "phase": "click",
            "dik": -1,
            "name": "UAGear",
            "input_id": 187,
            "delivered_press": 0,
            "delivered_release": 0,
            "press_tick": -1,
            "release_tick": -1,
            "release_due_s": -1.0,
            "tick_time_s": 912.25,
            "released_by": "",
            "menu_open": 0,
            "exists": 1,
            "locked": 0,
            "in_active_inputs": 1,
            "reason": "no_local_setter",
        },
    }


class InputTriggerIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_client_command_that_needs_a_lease(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))
        status, body = self.state.enqueue_command(COMMAND, _wire("key", "click"))
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "client", COMMAND))
        self.assertEqual(
            self.state.enqueue_command(COMMAND, _wire("key", "click"), peer="server"),
            (400, {"error": "bad_peer"}),
        )

    def test_eight_exact_variants_and_no_optional_key(self) -> None:
        variants, delegated = loopback._COMMAND_ARG_SCHEMAS[COMMAND]
        self.assertIsNone(delegated)
        self.assertEqual(len(variants), 8)
        for required, optional, validators in variants:
            with self.subTest(required=sorted(required)):
                self.assertEqual(optional, frozenset())
                self.assertEqual(set(validators), set(required))
        self.assertEqual({required for required, _o, _v in variants}, EXPECTED_REQUIRED)

    def test_each_kind_and_phase_enqueues_on_the_client(self) -> None:
        for kind in ("key", "input"):
            for edge in ("click", "release", "hold", "press"):
                with self.subTest(kind=kind, edge=edge):
                    status, body = self.state.enqueue_command(COMMAND, _wire(kind, edge))
                    self.assertEqual((status, body["peer"]), (200, "client"))

    def test_no_number_travels_without_its_phase_and_zero_is_refused(self) -> None:
        # fb-20260930-065425-8779: an absent key reaches Enforce as 0, so each
        # phase requires its own number and refuses the other's.
        refused = [
            {key: value for key, value in _wire("key", "hold").items() if key != "hold_s"},
            {key: value for key, value in _wire("key", "press").items() if key != "hold_ttl_s"},
            {key: value for key, value in _wire("input", "hold").items() if key != "hold_s"},
            {key: value for key, value in _wire("input", "press").items() if key != "hold_ttl_s"},
            _wire("key", "click", hold_s=1.0),
            _wire("key", "click", hold_ttl_s=1.0),
            _wire("key", "release", hold_s=1.0),
            _wire("key", "hold", hold_ttl_s=1.0),
            _wire("input", "press", hold_s=1.0),
            _wire("key", "hold", hold_s=0),
            _wire("key", "press", hold_ttl_s=0.0),
            _wire("key", "hold", hold_s=10.001),
            _wire("key", "press", hold_ttl_s=30.001),
            {key: value for key, value in _wire("key", "release").items() if key != "dik"},
            _wire("key", "click", dik=256),
            _wire("key", "click", trigger_entry="world"),
            _wire("key", "click", name="UAGear"),
            _wire("input", "click", dik=1),
            _wire("input", "click", trigger_entry="game"),
            _wire("input", "click", name="UA\u00f1"),
            _wire("key", "click", trigger_edge="tap"),
            _wire("key", "click", trigger_kind="mouse"),
        ]
        for args in refused:
            with self.subTest(args=args):
                self.assertEqual(
                    self.state.enqueue_command(COMMAND, args), (400, {"error": "bad_args"})
                )


class InputTriggerCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT_PATH.read_text(encoding="utf-8")

    def test_client_caps_and_dispatch_name_the_command(self) -> None:
        self.assertIn(COMMAND, announced_caps(self.source))
        names = dispatch_census(self.source)
        self.assertIn(COMMAND, names)
        self.assertEqual(names[-1], "ui_dialog")
        dispatch = _method_body(self.source, "protected void Dispatch(MCPCommand command)")
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), "postNow = DispatchInputTrigger(command, result);")
        _in_order(
            self,
            dispatch,
            'result.error = "client_not_in_game";',
            f'command.cmd == "{COMMAND}"',
            'command.cmd == "ui_dialog"',
        )

    def test_server_bridge_neither_implements_nor_announces_it(self) -> None:
        self.assertNotIn(COMMAND, SERVER_BRIDGE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["server"])
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertNotIn(COMMAND, fixture["peers"]["server"])
        self.assertEqual(fixture["peers"]["client"].get(COMMAND), COMMAND)

    def test_daemon_map_names_its_tool_and_the_version_and_hash_stay(self) -> None:
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["client"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "3c77a99c95fd05a4")
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn('const string MCP_BRIDGE_VERSION = "10";', messages)

    def test_stale_client_census_names_this_tool(self) -> None:
        announced = sorted(_BRIDGE_COMMAND_TOOLS["client"])
        announced.remove(COMMAND)
        registered = frozenset(
            tool for tool in _BRIDGE_COMMAND_TOOLS["client"].values() if tool
        )
        result = server._compare_bridge_capabilities(
            "client",
            {"state": "announced", "reason": "ok", "announced_commands": announced},
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])


class InputTriggerKeyContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT_PATH.read_text(encoding="utf-8")
        self.code = _without_comments(self.source)

    def test_deliver_calls_the_two_vanilla_entries_and_key_press_keeps_its_call(self) -> None:
        deliver = _method_body(self.source, DELIVER)
        game = _if_body(deliver, 'entry == "game"')
        for call in ("GetGame().OnKeyPress(dik);", "GetGame().OnKeyRelease(dik);"):
            with self.subTest(call=call):
                self.assertEqual(game.count(call), 1)
        mission_at = deliver.index("mission = GetGame().GetMission();")
        for call in ("mission.OnKeyPress(dik);", "mission.OnKeyRelease(dik);"):
            with self.subTest(call=call):
                self.assertEqual(deliver.count(call), 1)
                self.assertGreater(deliver.index(call), mission_at)
        self.assertLess(deliver.index("if (!mission)"), deliver.index("mission.OnKeyPress(dik);"))
        # Every release in the file is Deliver's; key_press keeps its one call.
        self.assertEqual(self.code.count(".OnKeyRelease("), 2)
        self.assertEqual(self.code.count(".OnKeyPress("), 3)
        key_press = _method_body(self.source, "protected bool DispatchKeyPress(")
        self.assertEqual(key_press.count("GetGame().GetMission().OnKeyPress(dik);"), 1)
        self.assertNotIn("MCPInputTriggerControl", key_press)
        for forbidden in ("SendInput", "keybd_event", "LocalPress", "ForceEnable", "UAInput"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, deliver)

    def test_press_arms_only_after_a_delivered_press(self) -> None:
        press = _method_body(self.source, PRESS)
        held = _if_body(press, "s_Held")
        self.assertEqual(held.strip(), "return -1;")
        undelivered = _if_body(press, "!Deliver(entry, dik, true)")
        self.assertEqual(undelivered.strip(), "return 0;")
        _in_order(
            self,
            press,
            "if (s_Held)",
            "if (!Deliver(entry, dik, true))",
            "s_Gen = s_Gen + 1;",
            "s_Held = true;",
            "s_PressTick = s_Tick;",
            "s_DueS = dueS;",
            "s_Player = player;",
            "return s_Gen;",
        )

    def test_release_clears_the_hold_before_it_delivers(self) -> None:
        release = _method_body(self.source, RELEASE_ALL)
        self.assertEqual(_if_body(release, "!s_Held").strip(), "return;")
        _in_order(
            self,
            release,
            "if (!s_Held)",
            "s_Held = false;",
            "s_ReleasedGen = s_Gen;",
            "s_ReleasedBy = why;",
            "s_ReleaseDelivered = Deliver(s_Entry, s_Dik, false);",
        )

    def test_maintain_from_tick_releases_on_player_phase_and_ttl(self) -> None:
        maintain = _method_body(self.source, MAINTAIN)
        _in_order(
            self,
            maintain,
            "s_Tick = s_Tick + 1;",
            "if (!s_Held)",
            "live = PlayerBase.Cast(GetGame().GetPlayer());",
            "if (!live)",
            "if (live != s_Player)",
            "if (!live.IsAlive())",
            "if (s_Tick <= s_PressTick)",
            'if (s_Edge == "click")',
            "if (GetGame().GetTickTime() < s_DueS)",
            'if (s_Edge == "hold")',
            'ReleaseAll("ttl");',
        )
        # Idle costs no engine call.
        self.assertLess(maintain.index("if (!s_Held)"), maintain.index("GetGame()"))
        for guard in ("!live", "live != s_Player", "!live.IsAlive()"):
            with self.subTest(guard=guard):
                self.assertIn('ReleaseAll("player_changed");', _if_body(maintain, guard))
        for edge in ("click", "hold"):
            with self.subTest(edge=edge):
                self.assertIn('ReleaseAll("phase");', _if_body(maintain, f's_Edge == "{edge}"'))
        self.assertEqual(_if_body(maintain, "s_Tick <= s_PressTick").strip(), "return;")
        on_tick = _method_body(self.source, "void OnTick(float timeslice)")
        _in_order(
            self,
            on_tick,
            "MCPInputTriggerControl.MaintainFromTick();",
            "m_JobRunner.Tick(timeslice, this);",
            "if (!m_Configured)",
        )

    def test_restore_and_shutdown_release_the_key(self) -> None:
        restore = _method_body(self.source, "protected void RestoreGameplay()")
        _in_order(self, restore, 'MCPInputTriggerControl.ReleaseAll("restore");', "if (!GetGame())")
        shutdown = _method_body(self.source, "void Shutdown()")
        _in_order(
            self, shutdown, 'MCPInputTriggerControl.ReleaseAll("shutdown");', "RestoreGameplay();"
        )
        # Every release names one of the five causes the reply lists: the
        # controller's own calls, and the bridge's calls through the class.
        control = self.source[
            self.source.index("class MCPInputTriggerControl") : self.source.index(
                "class MCPClientBridge extends"
            )
        ]
        causes = set(re.findall(r'ReleaseAll\("(\w+)"\)', control))
        causes |= set(re.findall(r'MCPInputTriggerControl\.ReleaseAll\("(\w+)"\)', self.source))
        self.assertEqual(causes, set(RELEASED_BY))

    def test_dispatch_refuses_a_dedicated_server_and_bad_times(self) -> None:
        dispatch = _method_body(self.source, DISPATCH)
        self.assertIn(
            'result.error = "client_not_in_game";', _if_body(dispatch, "GetGame().IsDedicatedServer()")
        )
        _in_order(
            self,
            dispatch,
            "if (GetGame().IsDedicatedServer())",
            "if (!InputTriggerTimesOk(args))",
            'if (args.trigger_kind == "key")',
            'if (args.trigger_kind == "input")',
        )
        times = _method_body(self.source, TIMES_OK)
        for edge, field, bound in (
            ("hold", "hold_s", "HOLD_MAX_S"),
            ("press", "hold_ttl_s", "PRESS_MAX_TTL_S"),
        ):
            block = _if_body(times, f'args.trigger_edge == "{edge}"')
            for condition in (
                f"!IsStrictFinite(args.{field})",
                f"args.{field} <= 0.0",
                f"args.{field} > MCPInputTriggerControl.{bound}",
            ):
                with self.subTest(condition=condition):
                    self.assertEqual(_if_body(block, condition).strip(), "return false;")

    def test_a_physical_alt_with_a_synthetic_f4_is_refused_on_entry_game(self) -> None:
        # Review R1 F1: DayZGame.OnKeyPress sets its private left Alt flag on
        # KC_LMENU, physical or not, and calls RequestExit for F4 while it is set
        # (dayzgame.c:2855-2858, :2876-2881, DEVELOPER builds). The bridge cannot
        # read that flag, so the refusal must not depend on any Alt state: the
        # scenarios carry physical_alt and no clause may read it.
        condition = _exit_guard_condition(self.source)
        clauses = [clause.strip() for clause in condition.split("&&")]
        exit_keys = _exit_combo_keys(self.source)
        for label, scenario, refused in EXIT_GUARD_SCENARIOS:
            with self.subTest(scenario=label):
                state = {**scenario, "exit_keys": exit_keys}
                self.assertEqual(
                    all(_EXIT_GUARD_CLAUSES[clause](state) for clause in clauses),
                    refused,
                    f"{label}: guard `{condition}`",
                )

    def test_entry_game_never_presses_f4_or_an_alt_key_and_says_so_first(self) -> None:
        key = _method_body(self.source, KEY)
        guard = (
            'entry == "game" && edge != "release" '
            "&& MCPInputTriggerControl.IsExitComboKey(dik)"
        )
        self.assertEqual(_exit_guard_condition(self.source), guard)
        self.assertIn('result.error = "would_request_exit";', _if_body(key, guard))
        _in_order(
            self,
            key,
            f"if ({guard})",
            'if (edge == "release")',
            'result.error = "input_trigger_busy";',
            "MCPInputTriggerControl.Press(entry, dik, edge, dueS, player);",
        )
        combo = _method_body(self.source, EXIT_COMBO)
        for key_code in ("KeyCode.KC_F4", "KeyCode.KC_LMENU", "KeyCode.KC_RMENU"):
            with self.subTest(key_code=key_code):
                self.assertEqual(_if_body(combo, f"dik == {key_code}").strip(), "return true;")
        self.assertEqual(_exit_combo_keys(self.source), {"KC_F4", "KC_LMENU", "KC_RMENU"})
        self.assertTrue(combo.rstrip().endswith("return false;"))
        # The Alt this verb holds is no longer the criterion anywhere.
        self.assertNotIn("HoldsAlt", self.source)

    def test_one_key_at_a_time_and_the_press_answers_at_once(self) -> None:
        key = _method_body(self.source, KEY)
        busy = 'MCPInputTriggerControl.IsHeld() || m_JobRunner.CountOfKind("input_trigger") > 0'
        self.assertIn('result.error = "input_trigger_busy";', _if_body(key, busy))
        self.assertIn('result.error = "input_trigger_busy";', _if_body(key, "generation < 0"))
        self.assertIn('result.error = "no_mission";', _if_body(key, "generation == 0"))
        self.assertLess(key.index("if (generation == 0)"), key.index("reply.delivered_press = true;"))
        # After the delivered press: the press answers at once, click and hold
        # queue a job that answers after the release.
        delivered = key[key.index("reply.delivered_press = true;") :]
        self.assertEqual(
            " ".join(_if_body(delivered, 'edge == "press"').split()),
            "result.ok = true; return true;",
        )
        self.assertEqual(
            _if_body(delivered, 'edge == "hold"').strip(),
            "job.deadline_s = job.deadline_s + args.hold_s;",
        )
        _in_order(
            self,
            delivered,
            "reply.press_tick = MCPInputTriggerControl.PressTick();",
            "reply.release_due_s = dueS;",
            'if (edge == "press")',
            'job.kind = "input_trigger";',
            "job.generation = generation;",
            "job.deadline_s = m_JobRunner.GetElapsedS() + INPUT_TRIGGER_JOB_SLACK_S;",
            'if (edge == "hold")',
            "job.input_trigger = reply;",
            "m_JobRunner.AddJob(job);",
            "return false;",
        )
        self.assertIn("protected const float INPUT_TRIGGER_JOB_SLACK_S = 5.0;", self.source)
        self.assertEqual(server.INPUT_TRIGGER_HOLD_SLACK_S, 5.0)

    def test_release_only_ends_a_matching_press(self) -> None:
        release = _method_body(self.source, RELEASE)
        match = _if_body(release, 'heldEdge == "press" && MCPInputTriggerControl.Holds(entry, dik)')
        _in_order(self, match, 'MCPInputTriggerControl.ReleaseAll("phase");', "result.ok = true;")
        owned = _if_body(
            release,
            'heldEdge == "click" || heldEdge == "hold" || m_JobRunner.CountOfKind("input_trigger") > 0',
        )
        self.assertIn('result.error = "input_trigger_busy";', owned)
        self.assertIn(
            "FillInputTriggerRelease(reply);",
            _if_body(release, "MCPInputTriggerControl.LastReleaseWas(entry, dik)"),
        )
        self.assertTrue(release.rstrip().endswith('result.error = "not_held";\n\t\treturn true;'))

    def test_click_and_hold_jobs_answer_after_the_release(self) -> None:
        process_job = _method_body(self.source, "override bool MCP_ProcessJob(MCPJob job)")
        self.assertEqual(
            _if_body(process_job, 'job.kind == "input_trigger"').strip(),
            "return ProcessInputTriggerJob(job);",
        )
        process = _method_body(self.source, PROCESS)
        self.assertEqual(
            _if_body(process, "!MCPInputTriggerControl.WasReleased(job.generation)").strip(),
            "return false;",
        )
        self.assertIn(
            'job.error = "aborted";', _if_body(process, 'job.input_trigger.released_by != "phase"')
        )
        success = _method_body(self.source, "override void MCP_PostJobSuccess(MCPJob job)")
        self.assertIn("PostInputTriggerJob(job);", _if_body(success, 'job.kind == "input_trigger"'))
        failure = _method_body(self.source, "override void MCP_PostJobFailure(MCPJob job)")
        self.assertIn(
            "PostInputTriggerJob(job);", _if_body(failure, 'job && job.kind == "input_trigger"')
        )
        timeout = _method_body(self.source, "override void MCP_PostJobTimeout(MCPJob job)")
        self.assertIn(
            "PostInputTriggerTimeout(job);", _if_body(timeout, 'job.kind == "input_trigger"')
        )
        late = _method_body(self.source, TIMEOUT)
        stuck = "MCPInputTriggerControl.IsHeld() && MCPInputTriggerControl.Generation() == job.generation"
        self.assertEqual(_if_body(late, stuck).strip(), 'MCPInputTriggerControl.ReleaseAll("ttl");')
        _in_order(self, late, f"if ({stuck})", 'job.error = "aborted";', "PostInputTriggerJob(job);")
        post = _method_body(self.source, POST)
        self.assertIn("result.input_trigger = job.input_trigger;", post)
        # A click or hold waits on a key; it does not make camera_set busy.
        exclusive = _method_body(self.source, "protected bool HasExclusiveJob()")
        self.assertIn('triggerJobs = m_JobRunner.CountOfKind("input_trigger");', exclusive)
        self.assertIn("blocking = blocking - weaponJobs - triggerJobs;", exclusive)

    def test_compile_hazards_locals_on_top_and_no_stored_entity_reference(self) -> None:
        for signature in NEW_METHODS:
            with self.subTest(method=signature):
                body = _method_body(self.source, signature)
                _locals_declared_at_top(body)
                self.assertIsNone(re.search(r"\blocal\b", _without_comments(body)))
                self.assertNotIn("GetGame().GetPlayer() ==", body)
                self.assertNotIn("GetGame().GetPlayer() !=", body)
                self.assertNotIn("== GetGame().GetPlayer()", body)
                self.assertNotIn("!= GetGame().GetPlayer()", body)
        self.assertIn("\tstatic PlayerBase s_Player;\n", self.source)
        self.assertNotIn("ref PlayerBase", self.source)
        # The helper can fail: a late or a repeated declaration is caught.
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a = 0;\n\t\ta = 1;\n\t\tint b = 0;\n")
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tPlayerBase a;\n\t\tstring a;\n")


class InputTriggerInputContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT_PATH.read_text(encoding="utf-8")
        self.body = _method_body(self.source, INPUT)

    def test_refusals_in_order_and_always_input_not_drivable(self) -> None:
        _in_order(
            self,
            self.body,
            "if (!IsPrintableInputName(args.name))",
            "api = GetUApi();",
            "input = api.GetInputByName(args.name);",
            "if (!input)",
            "resolvedId = input.ID();",
            "if (resolvedId < 0)",
            "reply.exists = true;",
            "reply.locked = input.IsLocked();",
            "api.GetActiveInputs(activeIds);",
            "if (reply.locked)",
            "if (IsVanillaForcedInput(api, resolvedId))",
            'reply.reason = "no_local_setter";',
            'result.error = "input_not_drivable";',
        )
        for condition, code in (
            ("!input", "input_unknown"),
            ("resolvedId < 0", "input_unknown"),
            ("reply.locked", "input_locked"),
            ("IsVanillaForcedInput(api, resolvedId)", "input_denied"),
        ):
            with self.subTest(condition=condition):
                self.assertIn(f'result.error = "{code}";', _if_body(self.body, condition))
        codes = set(re.findall(r'result\.error = "(\w+)"', self.body))
        self.assertEqual(
            codes,
            {
                "bad_args",
                "input_api_unavailable",
                "input_unknown",
                "input_locked",
                "input_denied",
                "input_not_drivable",
            },
        )
        # No success path: every return of the method is a refusal.
        self.assertNotIn("result.ok = true", self.body)
        self.assertEqual(self.body.count("result.ok = false;"), self.body.count("return true;"))

    def test_nothing_is_forced_overridden_or_locked(self) -> None:
        # The design's guards for a later forcing route hold with no route at
        # all: every ForceEnable(true) would need its ForceEnable(false), and a
        # SyncedOverride would need #ifdef INPUT_OVERRIDE; neither is called.
        code = _without_comments(self.source)
        self.assertEqual(code.count("ForceEnable(true)"), code.count("ForceEnable(false)"))
        for banned in (
            "ForceEnable(",
            "ForceDisable(",
            "SyncedOverride",
            "SyncedRemoveAllOverrides",
            "ActivateAction",
            "LocalPress(",
            "Supress(",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, code)
        for banned in (".Lock()", ".Unlock()", "SelectAlternative", "BindCombo"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.body)

    def test_denylist_resolves_both_vanilla_forced_inputs_by_index(self) -> None:
        denylist = _method_body(self.source, DENYLIST)
        for local, name in (("walkRun", "UAWalkRunForced"), ("tempRaise", "UATempRaiseWeapon")):
            with self.subTest(name=name):
                self.assertIn(f'{local} = api.GetInputByName("{name}");', denylist)
                self.assertEqual(
                    _if_body(denylist, f"{local} && {local}.ID() == inputId").strip(),
                    "return true;",
                )
        self.assertTrue(denylist.rstrip().endswith("return false;"))


class InputTriggerPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = MESSAGES_PATH.read_text(encoding="utf-8")

    def test_args_carry_the_four_new_fields_once_without_defaults(self) -> None:
        members = _class_members(self.messages, "MCPArgs")
        for member in (
            ("string", "trigger_kind"),
            ("string", "trigger_edge"),
            ("string", "trigger_entry"),
            ("float", "hold_s"),
            ("int", "dik"),
            ("string", "name"),
            ("float", "hold_ttl_s"),
        ):
            with self.subTest(member=member):
                self.assertEqual(members.count(member), 1)
        constructor = _method_body(_without_comments(self.messages), "void MCPArgs()")
        for field in ("trigger_kind", "trigger_edge", "trigger_entry", "hold_s"):
            with self.subTest(field=field):
                self.assertIsNone(re.search(rf"\b{field}\s*=", constructor))
        self.assertIn(
            "// input_describe, and input_trigger with trigger_kind input: UAInput name.",
            self.messages,
        )

    def test_reply_class_is_primitives_with_unset_markers(self) -> None:
        members = _class_members(self.messages, "MCPInputTrigger")
        self.assertEqual(members, EXPECTED_REPLY_MEMBERS)
        for member_type, _name in members:
            self.assertIn(member_type, ("string", "int", "float", "bool"))
        constructor = " ".join(
            _method_body(_without_comments(self.messages), "void MCPInputTrigger()").split()
        )
        self.assertEqual(
            constructor,
            "dik = -1; input_id = -1; press_tick = -1; release_tick = -1; "
            "release_due_s = -1.0; tick_time_s = -1.0;",
        )
        for owner in ("MCPResult", "MCPJob"):
            with self.subTest(owner=owner):
                self.assertEqual(
                    _class_members(self.messages, owner).count(
                        ("ref MCPInputTrigger", "input_trigger")
                    ),
                    1,
                )
        self.assertLess(
            self.messages.index("class MCPInputTrigger"), self.messages.index("class MCPResult")
        )

    def test_reply_is_prunable_and_a_refusal_keeps_its_zero_facts(self) -> None:
        self.assertIn(COMMAND, result_prune.PRUNABLE_FIELDS)
        self.assertNotIn(COMMAND, result_prune.OWNED_SCALAR_FIELDS)
        unfilled = result_prune.prune_unfilled_fields("world_spawn", {"ok": 1, COMMAND: {}})
        self.assertNotIn(COMMAND, unfilled)
        filled = {"kind": "key", "dik": 0, "delivered_press": 1, "delivered_release": 0}
        kept = result_prune.prune_unfilled_fields(COMMAND, {"ok": 1, COMMAND: dict(filled)})
        self.assertEqual(kept[COMMAND], filled)


class InputTriggerToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _description(self) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[COMMAND].description or ""

    async def test_description_states_what_is_promised_and_what_is_not(self) -> None:
        description = await self._description()
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        for fragment in (
            "No OS input and no focus.",
            "kind=key sends dik (0..255, ESC is 1)",
            "entry=game (default), DayZGame.OnKeyPress/OnKeyRelease",
            "entry=mission, Mission.OnKeyPress/OnKeyRelease only, what key_press calls",
            "phase=click (default) presses, releases on the next client tick and "
            "answers after the release",
            "phase=hold presses, releases after hold_s (required, 0 < hold_s <= 10)",
            "the tool waits at least hold_s + 5 s",
            "phase=press presses and answers at once with release_due_s",
            "its ttl_s (required, 0 < ttl_s <= 30), restore_gameplay, a change or "
            "death of the local player, or the bridge shutting down",
            "One key at a time",
            "delivered_press and delivered_release mean the bridge called the "
            "handler, not that anything consumed the key",
            "entry=game never presses F4, LMENU or RMENU, whatever this tool or "
            "the physical keyboard holds (would_request_exit)",
            "a physical Alt sets that flag and nothing can read it",
            "an Alt held through entry=game would let a physical F4 exit",
            "entry=mission delivers these keys to the mission handlers only",
            "not_held; observed=released_by=... is added only when the most recent "
            "release this tool made was of that same entry and key (one release is "
            "remembered, not a history per key)",
            "in DayZ 1.29 no script setter feeds UAInput.Local*, so mods that poll "
            "UAInput (LocalPress, LocalValue and the like, Community Framework input "
            "bindings included) cannot be driven by this tool",
            "otherwise always input_not_drivable; reason=no_local_setter; "
            "observed=exists=1 locked=0 in_active_inputs=0|1",
            "input_denied (UAWalkRunForced and UATempRaiseWeapon",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        for cause in RELEASED_BY:
            with self.subTest(cause=cause):
                self.assertIn(cause, description[description.index("released_by (") :])
        # Review R1 F1 retracted the round-1 promise: the Alt this tool holds
        # was never the only Alt that can close the game.
        self.assertNotIn("while this tool holds Alt", description)

    async def test_description_names_every_error_the_bridge_can_return(self) -> None:
        # Read from the Enforce source: a new refusal there fails here until the
        # description names it.
        source = CLIENT_PATH.read_text(encoding="utf-8")
        codes: set[str] = {"client_not_in_game"}  # the Dispatch gate
        for signature in (DISPATCH, KEY, RELEASE, INPUT, PROCESS, TIMEOUT):
            body = _method_body(source, signature)
            codes |= set(re.findall(r'(?:result|job)\.error = "(\w+)"', body))
        self.assertEqual(
            codes,
            {
                "bad_args",
                "client_not_in_game",
                "no_mission",
                "input_trigger_busy",
                "not_held",
                "would_request_exit",
                "aborted",
                "input_api_unavailable",
                "input_unknown",
                "input_locked",
                "input_denied",
                "input_not_drivable",
            },
        )
        description = await self._description()
        listed = description[description.index("Errors: ") :]
        for code in sorted(codes):
            with self.subTest(code=code):
                self.assertIn(code, listed)

    async def test_the_tool_sends_exactly_its_variant_on_the_client(self) -> None:
        cases = (
            ({"kind": "key", "dik": 1}, {"trigger_kind": "key", "trigger_edge": "click", "trigger_entry": "game", "dik": 1}, 1.0),
            (
                {"kind": "key", "dik": 0, "entry": "mission", "phase": "release"},
                {"trigger_kind": "key", "trigger_edge": "release", "trigger_entry": "mission", "dik": 0},
                1.0,
            ),
            (
                {"kind": "key", "dik": 17, "phase": "hold", "hold_s": 2.5},
                {"trigger_kind": "key", "trigger_edge": "hold", "trigger_entry": "game", "dik": 17, "hold_s": 2.5},
                7.5,
            ),
            (
                {"kind": "key", "dik": 42, "phase": "press", "ttl_s": 30},
                {"trigger_kind": "key", "trigger_edge": "press", "trigger_entry": "game", "dik": 42, "hold_ttl_s": 30.0},
                1.0,
            ),
            (
                {"kind": "input", "name": "UAGear"},
                {"trigger_kind": "input", "trigger_edge": "click", "name": "UAGear"},
                1.0,
            ),
            (
                {"kind": "input", "name": "UAGear", "phase": "hold", "hold_s": 10},
                {"trigger_kind": "input", "trigger_edge": "hold", "name": "UAGear", "hold_s": 10.0},
                15.0,
            ),
        )
        for arguments, wire, timeout in cases:
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1, COMMAND: {"kind": arguments["kind"]}})
                with patch.object(self.runtime, "call_bridge", new=call):
                    await self.app.call_tool(COMMAND, {**arguments, "timeout_s": 1.0})
                call.assert_awaited_once_with(COMMAND, wire, "client", timeout)
                self.assertEqual(loopback.validate_command_args(COMMAND, wire), (True, None))
        # A longer timeout than hold_s + 5 is kept.
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            await self.app.call_tool(
                COMMAND, {"kind": "key", "dik": 17, "phase": "hold", "hold_s": 1, "timeout_s": 20.0}
            )
        self.assertEqual(call.await_args.args[3], 20.0)

    async def test_bad_arguments_raise_before_the_bridge(self) -> None:
        cases = (
            ({"kind": "key"}, "bad_args: dik"),
            ({"kind": "key", "dik": -1}, "bad_args: dik"),
            ({"kind": "key", "dik": 256}, "bad_args: dik"),
            ({"kind": "key", "dik": 1, "name": "UAGear"}, "bad_args: name"),
            ({"kind": "input"}, "bad_args: name"),
            ({"kind": "input", "name": "UA\n"}, "bad_args: name"),
            ({"kind": "input", "name": "a" * 129}, "bad_args: name"),
            ({"kind": "input", "name": "UAGear", "dik": 1}, "bad_args: dik"),
            ({"kind": "input", "name": "UAGear", "entry": "game"}, "bad_args: entry"),
            ({"kind": "key", "dik": 1, "phase": "hold"}, "bad_args: hold_s"),
            ({"kind": "key", "dik": 1, "phase": "hold", "hold_s": 0}, "bad_args: hold_s"),
            ({"kind": "key", "dik": 1, "phase": "hold", "hold_s": 10.5}, "bad_args: hold_s"),
            ({"kind": "key", "dik": 1, "phase": "press"}, "bad_args: ttl_s"),
            ({"kind": "key", "dik": 1, "phase": "press", "ttl_s": 30.5}, "bad_args: ttl_s"),
            ({"kind": "key", "dik": 1, "phase": "press", "ttl_s": -1}, "bad_args: ttl_s"),
            ({"kind": "key", "dik": 1, "hold_s": 1.0}, "bad_args: hold_s"),
            ({"kind": "key", "dik": 1, "phase": "release", "ttl_s": 1.0}, "bad_args: ttl_s"),
            ({"kind": "key", "dik": 1, "phase": "hold", "hold_s": 1.0, "ttl_s": 1.0}, "bad_args: ttl_s"),
        )
        for arguments, prefix in cases:
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, {**arguments, "timeout_s": 1.0})
                self.assertTrue(
                    _tool_error_text(raised.exception).startswith(prefix),
                    _tool_error_text(raised.exception),
                )
                call.assert_not_awaited()
        # Schema-level refusals never reach the handler either.
        for arguments in (
            {"kind": "mouse", "dik": 1},
            {"kind": "key", "dik": 1, "phase": "tap"},
            {"kind": "key", "dik": 1, "entry": "world"},
            {"kind": "key", "dik": True},
            {"kind": "key", "dik": 1, "phase": "hold", "hold_s": True},
        ):
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError):
                        await self.app.call_tool(COMMAND, {**arguments, "timeout_s": 1.0})
                call.assert_not_awaited()

    def test_bounds_are_one_python_copy_equal_to_the_enforce_one(self) -> None:
        self.assertIs(server.INPUT_TRIGGER_DIK_MAX, loopback.INPUT_TRIGGER_DIK_MAX)
        self.assertIs(server.INPUT_TRIGGER_HOLD_MAX_S, loopback.INPUT_TRIGGER_HOLD_MAX_S)
        self.assertIs(server.INPUT_TRIGGER_PRESS_MAX_TTL_S, loopback.INPUT_TRIGGER_PRESS_MAX_TTL_S)
        source = CLIENT_PATH.read_text(encoding="utf-8")
        for declaration, value in (
            ("static const int DIK_MAX = ", loopback.INPUT_TRIGGER_DIK_MAX),
            ("static const float HOLD_MAX_S = ", loopback.INPUT_TRIGGER_HOLD_MAX_S),
            ("static const float PRESS_MAX_TTL_S = ", loopback.INPUT_TRIGGER_PRESS_MAX_TTL_S),
        ):
            with self.subTest(declaration=declaration):
                found = re.search(re.escape(declaration) + r"([0-9.]+);", source)
                self.assertIsNotNone(found)
                self.assertEqual(float(found.group(1)), float(value))
        self.assertEqual(
            (loopback.INPUT_TRIGGER_DIK_MAX, loopback.INPUT_TRIGGER_HOLD_MAX_S, loopback.INPUT_TRIGGER_PRESS_MAX_TTL_S),
            (255, 10.0, 30.0),
        )
        self.assertEqual(typing.get_args(server.InputTriggerKind), loopback.INPUT_TRIGGER_KINDS)
        self.assertEqual(typing.get_args(server.InputTriggerEntry), loopback.INPUT_TRIGGER_ENTRIES)
        self.assertEqual(
            set(typing.get_args(server.InputTriggerPhase)), set(loopback.INPUT_TRIGGER_EDGES)
        )


class InputTriggerErrorDetailTest(unittest.IsolatedAsyncioTestCase):
    def test_input_not_drivable_carries_the_reason_and_the_read_facts(self) -> None:
        self.assertEqual(
            str(server._bridge_error(_not_drivable_wire(), COMMAND)),
            "input_not_drivable; reason=no_local_setter; "
            "observed=exists=1 locked=0 in_active_inputs=1",
        )

    def test_not_held_and_aborted_carry_released_by(self) -> None:
        for code, cause in (("not_held", "ttl"), ("not_held", "restore"), ("aborted", "player_changed")):
            with self.subTest(code=code, cause=cause):
                wire = _not_drivable_wire()
                wire["error"] = code
                wire[COMMAND].update(reason="", released_by=cause)  # type: ignore[union-attr]
                self.assertEqual(
                    str(server._bridge_error(wire, COMMAND)), f"{code}; observed=released_by={cause}"
                )
        never_held = _not_drivable_wire()
        never_held["error"] = "not_held"
        never_held[COMMAND].update(reason="", released_by="")  # type: ignore[union-attr]
        self.assertEqual(str(server._bridge_error(never_held, COMMAND)), "not_held")

    def test_detail_is_gated_on_verb_and_code_and_leaks_nothing(self) -> None:
        wire = _not_drivable_wire()
        self.assertEqual(str(server._bridge_error(dict(wire), "input_describe")), "input_not_drivable")
        self.assertEqual(str(server._bridge_error(dict(wire), None)), "input_not_drivable")
        busy = _not_drivable_wire()
        busy["error"] = "input_trigger_busy"
        busy[COMMAND].update(reason="", released_by="ttl")  # type: ignore[union-attr]
        self.assertEqual(str(server._bridge_error(busy, COMMAND)), "input_trigger_busy")
        leaky = _not_drivable_wire()
        leaky[COMMAND].update(  # type: ignore[union-attr]
            name="caller-text-7f3a", reason="C:\\secret path", exists=2, locked="0", in_active_inputs=True
        )
        message = str(server._bridge_error(leaky, COMMAND))
        self.assertEqual(message, "input_not_drivable; observed=in_active_inputs=1")
        self.assertNotIn("caller-text-7f3a", message)
        forged = _not_drivable_wire()
        forged["error"] = "aborted"
        forged[COMMAND].update(reason="", released_by="rm -rf")  # type: ignore[union-attr]
        self.assertEqual(str(server._bridge_error(forged, COMMAND)), "aborted")
        self.assertEqual(str(server._bridge_error({"ok": 0, "error": "not_held"}, COMMAND)), "not_held")

    async def test_the_detail_reaches_the_caller_through_wait_for_result(self) -> None:
        _app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _message: None))
        wire = _not_drivable_wire()
        state = SimpleNamespace(take_result=lambda *_args, **_kwargs: json.loads(json.dumps(wire)))
        with patch.object(runtime, "loopback", SimpleNamespace(state=state)):
            with self.assertRaises(ToolError) as raised:
                await runtime.wait_for_result(COMMAND, 1, "client", 1.0)
        self.assertEqual(
            str(raised.exception),
            "input_not_drivable; reason=no_local_setter; "
            "observed=exists=1 locked=0 in_active_inputs=1",
        )


if __name__ == "__main__":
    unittest.main()
