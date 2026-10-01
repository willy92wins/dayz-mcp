"""player_heal (inbox bef7) and player_godmode, on by default (inbox 3136).

Two server bridge verbs and a new default. player_heal applies DayZ's own full
heal, the Bullet_CupidsBolt branch of PlayerBase.EEHitBy, to the server's
PlayerBase, seated or not. player_godmode switches SetAllowDamage on the player
only and keeps the choice per identity for the mission; MissionServer gives every
body an identity gets that choice, on by default, takes it off a body that leaves
its identity, and a periodic top-up keeps a godmode body's water and energy away
from the level where hunger and thirst cost health.

The Enforce methods that hold the logic are translated and run against fakes
(tests/enforce_subset_helpers.py): the heal sequence and its order, the godmode
memory, switch, release and top-up, and the MissionServer hooks. The dispatches,
the messages and the query fields are text contracts. The Python side is the
ingress schemas, the census and readiness map, the prune, and the two tools.
"""
from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests._tiers import slow_test
from tests.enforce_subset_helpers import EnforceString, method_body, translate
from tests.fence_helpers import announced_capabilities, bind_both_peers

HEAL = "player_heal"
GODMODE = "player_godmode"
COMMANDS = (GODMODE, HEAL)
SCRIPTS = addon_root() / "scripts"
CARE_PATH = SCRIPTS / "4_World" / "MCP_PlayerCare.c"
BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
CLIENT_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
MISSION_PATH = SCRIPTS / "5_Mission" / "MissionServer.c"
CENSUS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
VANILLA = Path(os.environ.get("DAYZ_MCP_VANILLA_SCRIPTS", "P:/scripts"))

HEAL_SERVER = "string MCPHealServer(bool full)"
COUNT_SPLINTS = "protected int MCPCountSplints()"
COUNT_GROUND_SPLINTS = "protected int MCPCountGroundSplints()"
DISPATCH = "protected void Dispatch(MCPCommand command)"
DISPATCH_HEAL = "protected bool DispatchPlayerHeal(MCPCommand command, MCPResult result)"
DISPATCH_GODMODE = "protected bool DispatchPlayerGodmode(MCPCommand command, MCPResult result)"
FILL_VITALS = "protected void FillPlayerVitals(PlayerBase target, MCPPlayerVitals vitals)"
BUILD_ALL = "protected array<ref MCPAllPlayer> BuildAllPlayers()"
BUILD_STATE = "protected MCPPlayerState BuildPlayerState()"
RESET = "static void ResetMission()"
IS_ON = "static bool IsOn(Man body)"
IS_ON_FOR = "static bool IsOnFor(string plainId)"
CHOOSE = "static void Choose(PlayerBase player, string plainId, bool on)"
ON_BODY_READY = "static void OnBodyReady(PlayerBase player, PlayerIdentity identity)"
RELEASE_BODY = "static void ReleaseBody(PlayerBase player, string why)"
TICK = "static void Tick(float timeslice)"
TOP_UP = "static void TopUp(PlayerBase player)"
REMEMBER = "protected static void Remember(string plainId, bool on)"
APPLY = "protected static void Apply(PlayerBase player, bool on)"
NOTE = "protected static void Note(bool on, string plainId, string why)"
INVOKE_ON_CONNECT = "override void InvokeOnConnect(PlayerBase player, PlayerIdentity identity)"
ON_RESPAWN = "override void OnClientRespawnEvent(PlayerIdentity identity, PlayerBase player)"
ON_DISCONNECT = "override void InvokeOnDisconnect(PlayerBase player)"
ON_MISSION_START = "override void OnMissionStart()"
ON_UPDATE = "override void OnUpdate(float timeslice)"

# PlayerConstants.LOW_WATER_THRESHOLD / LOW_ENERGY_THRESHOLD (playerconstants.c:70,
# :72); VanillaPremiseTest checks them against the scripts tree when it is there.
LOW_THRESHOLD = 300.0
STAT_MAX = 5000.0
# A new character's water and energy (playerstatspco.c:297-298).
STAT_INIT = 600.0
# eModifiers.MDF_BROKEN_LEGS, any distinct value for the fakes.
MDF_BROKEN_LEGS = 25
# The heal's one documented inventory change (review R1 F1): vanilla's own splint
# return when legs heal, said in the tool description and in the CHANGELOG.
SPLINT_RULE = (
    "Healing a player with an applied splint removes the splint and gives the "
    "Splint item back as vanilla does, into the inventory or, when it is full, on "
    "the ground at the player; nothing else in the inventory changes."
)
# Review R2 F1: vanilla deletes the applied splint even when it cannot create the
# Splint item anywhere; the answer then names no place.
SPLINT_LOSS = "Vanilla can lose the splint when it cannot place it, and the answer then says so."

EXPECTED_VITALS_MEMBERS = [
    ("float", "health"),
    ("float", "blood"),
    ("float", "shock"),
    ("bool", "broken_legs"),
    ("bool", "unconscious"),
    ("int", "bleeding_sources"),
    ("float", "water"),
    ("float", "energy"),
]
EXPECTED_HEAL_MEMBERS = [
    ("bool", "full"),
    ("bool", "in_vehicle"),
    ("bool", "splint_returned"),
    ("string", "splint_returned_to"),
    ("ref MCPPlayerVitals", "before"),
    ("ref MCPPlayerVitals", "after"),
]
EXPECTED_GODMODE_MEMBERS = [("bool", "before"), ("bool", "after")]

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_ENFORCE_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
# MCPGodmode's statics read through G, its `new` and template locals as calls and
# plain names the translator can read.
_GODMODE_REWRITES = (
    (re.compile(r"(?<![\w.])(s_Off|s_Players|s_TopUpAccumS|TOPUP_INTERVAL_S|TOPUP_MARGIN)\b"), r"G.\1"),
    (re.compile(r"new map<string, bool>\(\)"), "NewMap()"),
    (re.compile(r"new array<Man>\(\)"), "NewArray()"),
    (re.compile(r"PlayerStat<float>"), "PlayerStatF"),
)
_GODMODE_TYPES = ("PlayerBase", "PlayerIdentity", "PlayerStatF", "Man")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


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


def _if_body(body: str, condition: str) -> str:
    needle = f"if ({condition})"
    if body.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {body.count(needle)} times")
    return _block_after(body, body.index(needle))


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    code = _without_comments(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", code)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    return [
        (item.group("type"), item.group("name"))
        for item in _MEMBER_RE.finditer(_block_after(code, match.end()))
    ]


def _in_order(test: unittest.TestCase, body: str, *tokens: str) -> None:
    positions = []
    for token in tokens:
        test.assertEqual(body.count(token), 1, token)
        positions.append(body.index(token))
    test.assertEqual(positions, sorted(positions), tokens)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _rewritten(source: str, signature: str, rewrites) -> str:
    """One method of `source` as a standalone method, its code rewritten (never
    its string literals), for the translator."""
    body = method_body(source, signature)
    pieces: list[str] = []
    last = 0
    for match in _ENFORCE_STRING.finditer(body):
        pieces.append(_rewrite(body[last : match.start()], rewrites))
        pieces.append(match.group(0))
        last = match.end()
    pieces.append(_rewrite(body[last:], rewrites))
    return signature + "\n{" + "".join(pieces) + "}\n"


def _rewrite(code: str, rewrites) -> str:
    for pattern, replacement in rewrites:
        code = pattern.sub(replacement, code)
    return code


def _godmode_constant(name: str) -> float:
    match = re.search(rf"static const float {name} = ([0-9.]+);", _without_comments(_source(CARE_PATH)))
    if match is None:
        raise AssertionError(f"{name} is absent from MCP_PlayerCare.c")
    return float(match.group(1))


def _splint_ground_radius() -> float:
    """How far from the player the heal looks for a Splint on the ground."""
    match = re.search(
        r"protected const float MCP_SPLINT_GROUND_RADIUS_M = ([0-9.]+);", _without_comments(_source(CARE_PATH))
    )
    if match is None:
        raise AssertionError("MCP_SPLINT_GROUND_RADIUS_M is absent from MCP_PlayerCare.c")
    return float(match.group(1))


def _caps(source: str) -> list[str]:
    match = re.search(r"protected const string SERVER_CAPABILITIES = ((?:\"[^\"\n]*\"(?: \+ )?)+);", source)
    if match is None:
        raise AssertionError("SERVER_CAPABILITIES declaration not found")
    return "".join(re.findall(r'"([^"\n]*)"', match.group(1))).split(",")


def _tool_error_text(exc: BaseException) -> str:
    return str(exc)


class _LogString(EnforceString):
    """An Enforce string with the + the log lines use."""

    __slots__ = ()

    def __add__(self, other: object) -> "_LogString":
        if not isinstance(other, EnforceString):
            raise TypeError(f"string + {type(other).__name__}")
        return _LogString(self.data + other.data)


def _s(text: str) -> _LogString:
    return _LogString.of(text)


class _Stat:
    """PlayerStat<float> or <int>: Get, GetMax and a clamped Set (playerstatbase.c:85-152)."""

    def __init__(self, value: float, maximum: float = STAT_MAX) -> None:
        self.value = value
        self.maximum = maximum

    def Get(self) -> float:
        return self.value

    def GetMax(self) -> float:
        return self.maximum

    def Set(self, value: float) -> None:
        self.value = min(value, self.maximum)


class _Map:
    """map<string, bool> keyed by the bytes of an Enforce string."""

    def __init__(self) -> None:
        self.items: dict[bytes, bool] = {}

    def Contains(self, key: EnforceString) -> bool:
        return key.data in self.items

    def Set(self, key: EnforceString, value: bool) -> None:
        self.items[key.data] = value

    def Remove(self, key: EnforceString) -> None:
        self.items.pop(key.data, None)


class _ManArray:
    def __init__(self) -> None:
        self.items: list[object] = []

    def Clear(self) -> None:
        self.items.clear()

    def Count(self) -> int:
        return len(self.items)

    def Get(self, index: int) -> object:
        return self.items[index]


class _Identity:
    def __init__(self, plain_id: str) -> None:
        self.plain_id = plain_id

    def GetPlainId(self) -> _LogString:
        return _s(self.plain_id)


class _Body:
    """A server PlayerBase as the godmode code sees it."""

    def __init__(
        self,
        *,
        allow_damage: bool = True,
        alive: bool = True,
        water: float = STAT_INIT,
        energy: float = STAT_INIT,
        identity: _Identity | None = None,
    ) -> None:
        self.allow_damage = allow_damage
        self.alive = alive
        self.water = _Stat(water)
        self.energy = _Stat(energy)
        self.identity = identity
        self.damage_writes: list[bool] = []

    def GetAllowDamage(self) -> bool:
        return self.allow_damage

    def SetAllowDamage(self, value: bool) -> None:
        self.damage_writes.append(value)
        self.allow_damage = value

    def IsAlive(self) -> bool:
        return self.alive

    def GetStatWater(self) -> _Stat:
        return self.water

    def GetStatEnergy(self) -> _Stat:
        return self.energy

    def GetIdentity(self) -> _Identity | None:
        return self.identity


class _Godmode:
    """MCPGodmode's methods translated from MCP_PlayerCare.c, run on one static state."""

    def __init__(self, *, players: list[object] | None = None, game: bool = True) -> None:
        self.source = _source(CARE_PATH)
        self.printed: list[str] = []
        self.G = SimpleNamespace(
            s_Off=None,
            s_Players=None,
            s_TopUpAccumS=0.0,
            TOPUP_INTERVAL_S=_godmode_constant("TOPUP_INTERVAL_S"),
            TOPUP_MARGIN=_godmode_constant("TOPUP_MARGIN"),
        )
        self.players = list(players or [])
        fake_game = SimpleNamespace(GetPlayers=self._get_players)
        self.namespace: dict[str, Any] = {
            "_S": _LogString,
            "G": self.G,
            "NewMap": _Map,
            "NewArray": _ManArray,
            "Print": lambda line: self.printed.append(line.text),
            "GetGame": (lambda: fake_game) if game else (lambda: None),
            "PlayerBase": SimpleNamespace(Cast=lambda item: item if isinstance(item, _Body) else None),
            "PlayerConstants": SimpleNamespace(
                LOW_WATER_THRESHOLD=LOW_THRESHOLD, LOW_ENERGY_THRESHOLD=LOW_THRESHOLD
            ),
        }
        for signature in (RESET, IS_ON, IS_ON_FOR, REMEMBER, NOTE, TOP_UP, APPLY, CHOOSE, ON_BODY_READY, RELEASE_BODY, TICK):
            translate(
                _rewritten(self.source, signature, _GODMODE_REWRITES),
                signature,
                self.namespace,
                class_types=_GODMODE_TYPES,
            )

    def _get_players(self, out: _ManArray) -> None:
        out.items.extend(self.players)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.namespace[name]
        except KeyError:
            raise AttributeError(name) from None

    def off_ids(self) -> set[bytes]:
        return set(self.G.s_Off.items) if self.G.s_Off is not None else set()


class _HealPlayer:
    """One server PlayerBase as MCPHealServer sees it: every call, in order, with
    whether damage was allowed at the time, its inventory and the ground at it.

    room: vanilla's CreateInInventory finds a place (the hands included).
    ground_room: else its ground creation returns an entity; when it returns
    null too, the Splint item exists nowhere (review R2 F1).
    lying: Splints already loose on the ground near the player."""

    def __init__(
        self,
        *,
        allow_damage: bool = True,
        legs: int = 0,
        legs_after_reset_all: int = 0,
        unconscious: bool = False,
        managers: bool = True,
        stats: bool = True,
        splint_worn: bool = False,
        legs_modifier_active: bool = True,
        room: bool = True,
        ground_room: bool = True,
        lying: int = 0,
    ) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.allow_damage = allow_damage
        self.legs = legs
        self.legs_after_reset_all = legs_after_reset_all
        self.unconscious = unconscious
        self.values = {"blood_type": 6, "water": 900.0, "energy": 1200.0, "heat_buffer": 12.5, "heat_comfort": -0.25}
        self.stats = self._stats(self.values) if stats else None
        self.managers = managers
        self.splint_worn = splint_worn
        self.legs_modifier_active = legs_modifier_active
        self.room = room
        self.ground_room = ground_room
        self.inventory: list[str] = ["Bandage", "Splint_Applied"] if splint_worn else ["Bandage"]
        self.ground: list[str] = ["Splint"] * lying

    @staticmethod
    def _stats(values: dict[str, float]) -> dict[str, _Stat]:
        return {
            "blood_type": _Stat(values["blood_type"], 128),
            "water": _Stat(values["water"]),
            "energy": _Stat(values["energy"]),
            "heat_buffer": _Stat(values["heat_buffer"], 30.0),
            "heat_comfort": _Stat(values["heat_comfort"], 1.0),
        }

    def _record(self, *call: Any) -> None:
        self.calls.append(call + (self.allow_damage,))

    def _set_allow_damage(self, value: bool) -> None:
        self.calls.append(("SetAllowDamage", value))
        self.allow_damage = value

    def _reset_all(self) -> None:
        self._record("ResetAll")
        if not self.legs_modifier_active:
            # Deactivate returns at once for an inactive modifier (modifierbase.c:217-231).
            return
        # BrokenLegsMdfr.OnDeactivate (brokenlegs.c:38-48): RemoveSplint when the
        # splint is worn gives the Splint item back into the inventory (or the
        # hands) when there is room, else on the ground at the player, and queues
        # the applied one's Delete, which stays until the call queue runs
        # (miscgameplayfunctions.c:1636-1687, humaninventory.c:65-71, object.c:82-85).
        # The ground creation can return null as well (playerbase.c:6445-6472):
        # the applied one is deleted all the same and the Splint is lost.
        if self.splint_worn:
            if self.room:
                self.inventory.append("Splint")
            elif self.ground_room:
                self.ground.append("Splint")
        self.legs = self.legs_after_reset_all

    def _count_splints(self) -> int:
        self._record("MCPCountSplints")
        return self.inventory.count("Splint")

    def _count_ground_splints(self) -> int:
        self._record("MCPCountGroundSplints")
        return self.ground.count("Splint")

    def _set_broken_legs(self, state: int) -> None:
        self._record("SetBrokenLegs", state)
        self.legs = state

    def _reset_all_stats(self) -> None:
        self._record("ResetAllStats")
        # PlayerStatsPCO.ResetAllStats rebuilds every stat at its init value
        # (playerstatspco.c:113-117, :297-304); the getters fetch the new ones.
        self.stats = self._stats(
            {"blood_type": 1, "water": STAT_INIT, "energy": STAT_INIT, "heat_buffer": 0.0, "heat_comfort": 0.0}
        )

    def _stat(self, name: str):
        return lambda: self.stats[name]

    def namespace(self) -> dict[str, Any]:
        manager = (lambda **methods: SimpleNamespace(**methods)) if self.managers else (lambda **_m: None)
        return {
            "this": self,
            "GetAllowDamage": lambda: self.allow_damage,
            "SetAllowDamage": self._set_allow_damage,
            "DamageSystem": SimpleNamespace(ResetAllZones=lambda entity: self._record("ResetAllZones", entity is self)),
            "m_ModifiersManager": manager(
                ResetAll=self._reset_all,
                IsModifierActive=lambda modifier: modifier == MDF_BROKEN_LEGS and self.legs_modifier_active,
            ),
            "eModifiers": SimpleNamespace(MDF_BROKEN_LEGS=MDF_BROKEN_LEGS),
            "IsWearingSplint": lambda: self.splint_worn,
            "MCPCountSplints": self._count_splints,
            "MCPCountGroundSplints": self._count_ground_splints,
            "GetBrokenLegs": lambda: self.legs,
            "SetBrokenLegs": self._set_broken_legs,
            "eBrokenLegs": SimpleNamespace(NO_BROKEN_LEGS=0, BROKEN_LEGS=1, BROKEN_LEGS_SPLINT=2),
            "m_BleedingManagerServer": manager(RemoveAllSources=lambda: self._record("RemoveAllSources")),
            "GetPlayerStats": lambda: (SimpleNamespace(ResetAllStats=self._reset_all_stats) if self.stats is not None else None),
            "GetStatBloodType": self._stat("blood_type"),
            "GetStatWater": self._stat("water"),
            "GetStatEnergy": self._stat("energy"),
            "GetStatHeatBuffer": self._stat("heat_buffer"),
            "GetStatHeatComfort": self._stat("heat_comfort"),
            "m_AgentPool": manager(RemoveAllAgents=lambda: self._record("RemoveAllAgents")),
            "m_StaminaHandler": manager(SetStamina=lambda value: self._record("SetStamina", value)),
            "GameConstants": SimpleNamespace(STAMINA_MAX=100.0),
            "IsUnconscious": lambda: self.unconscious,
            "DayZPlayerSyncJunctures": SimpleNamespace(
                SendPlayerUnconsciousness=lambda player, enable: self._record("SendPlayerUnconsciousness", player is self, enable)
            ),
        }

    def heal(self, full: bool) -> str:
        heal = translate(_source(CARE_PATH), HEAL_SERVER, self.namespace())
        return heal(full).text

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


# --- ingress ----------------------------------------------------------------


class HealGodmodeIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)

    def test_both_are_server_commands_that_need_a_lease(self) -> None:
        for command, args in ((HEAL, {"full": True}), (GODMODE, {"godmode": False})):
            with self.subTest(command=command):
                self.assertIn(command, loopback.SERVER_COMMANDS)
                self.assertNotIn(command, loopback.CLIENT_COMMANDS)
                self.assertEqual(loopback.peer_for_command(command), "server")
                self.assertNotIn(command, READ_ONLY_COMMANDS)
                self.assertTrue(command_requires_lease(command))
                status, body = self.state.enqueue_command(command, dict(args))
                self.assertEqual((status, body["peer"], body["cmd"]), (200, "server", command))
                self.assertEqual(
                    self.state.enqueue_command(command, dict(args), peer="client"),
                    (400, {"error": "bad_peer"}),
                )

    def test_one_exact_variant_each_with_an_optional_uid(self) -> None:
        for command, choice in ((HEAL, "full"), (GODMODE, "godmode")):
            with self.subTest(command=command):
                variants, delegated = loopback._COMMAND_ARG_SCHEMAS[command]
                self.assertIsNone(delegated)
                self.assertEqual(len(variants), 1)
                required, optional, validators = variants[0]
                self.assertEqual(required, frozenset({choice}))
                self.assertEqual(optional, frozenset({"uid"}))
                self.assertEqual(set(validators), {choice, "uid"})

    def test_the_choice_travels_as_a_json_boolean_and_nothing_else_travels(self) -> None:
        # fb-20260930-065425-8779: an absent key reaches Enforce as false, so the
        # choice is required, and 1, 0 and "true" are not booleans.
        accepted = {
            HEAL: ({"full": True}, {"full": False}, {"full": True, "uid": "76561198000000001"}, {"full": False, "uid": ""}),
            GODMODE: ({"godmode": True}, {"godmode": False}, {"godmode": False, "uid": "76561198000000001"}),
        }
        refused = {
            HEAL: (
                {},
                {"uid": "76561198000000001"},
                {"full": 1},
                {"full": 0},
                {"full": "true"},
                {"full": None},
                {"full": True, "uid": 7},
                {"full": True, "godmode": True},
                {"full": True, "pos": [0.0, 0.0, 0.0]},
            ),
            GODMODE: (
                {},
                {"uid": "76561198000000001"},
                {"godmode": 1},
                {"godmode": 0},
                {"godmode": "off"},
                {"godmode": None},
                {"godmode": True, "uid": None},
                {"godmode": True, "on": True},
                {"on": False},
            ),
        }
        for command in COMMANDS:
            for args in accepted[command]:
                with self.subTest(command=command, args=args):
                    self.assertEqual(loopback.validate_command_args(command, args), (True, None))
            for args in refused[command]:
                with self.subTest(command=command, args=args):
                    self.assertEqual(self.state.enqueue_command(command, args), (400, {"error": "bad_args"}))


# --- census and readiness -----------------------------------------------------


class HealGodmodeCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = _source(BRIDGE_PATH)
        self.code = _without_comments(self.bridge)

    def test_the_server_bridge_announces_and_dispatches_both(self) -> None:
        caps = _caps(self.bridge)
        self.assertEqual(caps, sorted(caps))
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(caps.count(command), 1)
        dispatch = method_body(self.bridge, DISPATCH)
        for command, handler in ((HEAL, "DispatchPlayerHeal"), (GODMODE, "DispatchPlayerGodmode")):
            with self.subTest(command=command):
                branch = _if_body(dispatch, f'command.cmd == "{command}"')
                self.assertEqual(branch.strip(), f"postNow = {handler}(command, result);")
        self.assertLess(
            dispatch.index('command.cmd == "player_heal"'), dispatch.index('result.error = "unknown_command"')
        )

    def test_the_client_bridge_neither_implements_nor_announces_them(self) -> None:
        client = _source(CLIENT_PATH)
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertNotIn(f'"{command}"', client)
                self.assertNotIn(command, fixture["peers"]["client"])
                self.assertNotIn(command, server._BRIDGE_COMMAND_TOOLS["client"])

    def test_readiness_maps_each_server_command_to_its_tool_in_both_copies(self) -> None:
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(server._BRIDGE_COMMAND_TOOLS["server"].get(command), command)
                self.assertEqual(fixture["peers"]["server"].get(command), command)
                self.assertIn(command, announced_capabilities("server")["announced_commands"])

    def test_a_stale_server_census_names_both_missing_tools(self) -> None:
        registered = frozenset(tool for tool in server._BRIDGE_COMMAND_TOOLS["server"].values() if tool)
        announced = sorted(set(server._BRIDGE_COMMAND_TOOLS["server"]) - set(COMMANDS))
        result = server._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": announced,
                "announced_arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            },
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["reason"], "census_disagrees_with_registered_tools")
        self.assertEqual(result["registered_without_announced_command"], sorted(COMMANDS))
        full = server._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": sorted(server._BRIDGE_COMMAND_TOOLS["server"]),
                "announced_arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            },
            registered,
        )
        self.assertEqual((full["state"], full["reason"]), ("match", "ok"))

    def test_the_version_and_the_server_arg_contract_hash_stay(self) -> None:
        # Two new verbs with their own names: a PBO without them answers
        # unknown_command and fails the census above, so neither moves.
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertNotIn(command, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "3c77a99c95fd05a4")
        self.assertIn('protected const string SERVER_ARG_CONTRACT_HASH = "3c77a99c95fd05a4";', self.bridge)
        self.assertIn('const string MCP_BRIDGE_VERSION = "10";', _source(MESSAGES_PATH))


# --- the heal, run --------------------------------------------------------------


class PlayerHealSequenceTest(unittest.TestCase):
    """MCPHealServer translated and run on a fake server PlayerBase."""

    def test_the_vanilla_sequence_runs_in_the_template_order(self) -> None:
        player = _HealPlayer(legs=1, legs_after_reset_all=1, unconscious=True)
        player.heal(full=False)
        self.assertEqual(
            player.names(),
            [
                "ResetAllZones",
                "ResetAll",
                "SetBrokenLegs",
                "RemoveAllSources",
                "ResetAllStats",
                "RemoveAllAgents",
                "SetStamina",
                "SendPlayerUnconsciousness",
            ],
        )
        calls = {call[0]: call for call in player.calls}
        self.assertEqual(calls["ResetAllZones"][1], True, "ResetAllZones on this player")
        self.assertEqual(calls["SetBrokenLegs"][1], 0)
        self.assertEqual(calls["SetStamina"][1], 100.0)
        self.assertEqual(calls["SendPlayerUnconsciousness"][1:3], (True, False))

    def test_full_fills_water_and_energy_and_otherwise_keeps_them(self) -> None:
        for full, water, energy in ((True, STAT_MAX, STAT_MAX), (False, 900.0, 1200.0)):
            with self.subTest(full=full):
                player = _HealPlayer()
                player.heal(full=full)
                self.assertEqual(player.stats["water"].Get(), water)
                self.assertEqual(player.stats["energy"].Get(), energy)
                # What the template keeps across ResetAllStats (playerbase.c:1315-1327).
                self.assertEqual(player.stats["blood_type"].Get(), 6)
                self.assertEqual(player.stats["heat_buffer"].Get(), 12.5)
                self.assertEqual(player.stats["heat_comfort"].Get(), -0.25)

    def test_a_godmode_body_is_healed_with_damage_allowed_and_keeps_its_godmode(self) -> None:
        player = _HealPlayer(allow_damage=False, legs=1, legs_after_reset_all=1, unconscious=True)
        player.heal(full=True)
        self.assertEqual(player.calls[0], ("SetAllowDamage", True))
        self.assertEqual(player.calls[-1], ("SetAllowDamage", False))
        self.assertFalse(player.allow_damage)
        for call in player.calls[1:-1]:
            with self.subTest(call=call[0]):
                self.assertTrue(call[-1], f"{call[0]} ran with damage off")

    def test_a_body_with_vanilla_damage_never_has_its_damage_switched(self) -> None:
        player = _HealPlayer(allow_damage=True)
        player.heal(full=True)
        self.assertNotIn("SetAllowDamage", player.names())
        self.assertTrue(player.allow_damage)

    def test_broken_legs_and_unconsciousness_are_only_touched_when_still_there(self) -> None:
        # ResetAll's deactivation of an active MDF_BROKEN_LEGS already clears
        # them (brokenlegs.c:38-48); the juncture is only for an unconscious body.
        player = _HealPlayer(legs=1, legs_after_reset_all=0, unconscious=False)
        player.heal(full=False)
        self.assertNotIn("SetBrokenLegs", player.names())
        self.assertNotIn("SendPlayerUnconsciousness", player.names())
        self.assertEqual(player.legs, 0)

    def test_missing_managers_and_stats_are_skipped(self) -> None:
        player = _HealPlayer(managers=False, stats=False, unconscious=True)
        player.heal(full=True)
        self.assertEqual(player.names(), ["ResetAllZones", "SendPlayerUnconsciousness"])

    # Review R1 F1: a worn splint comes off through vanilla's own deactivation,
    # and the heal says whether and where the Splint item went.

    def _counts(self, player: _HealPlayer) -> list[str]:
        return [name for name in player.names() if name in ("MCPCountSplints", "MCPCountGroundSplints", "ResetAll")]

    def test_a_worn_splint_comes_back_into_the_inventory_through_vanillas_deactivation(self) -> None:
        player = _HealPlayer(legs=2, legs_after_reset_all=0, splint_worn=True)
        before = list(player.inventory)
        self.assertEqual(player.heal(full=False), "inventory")
        self.assertEqual(player.inventory, before + ["Splint"], "only the returned Splint is new")
        self.assertEqual(player.ground, [])
        self.assertEqual(
            self._counts(player),
            ["MCPCountSplints", "MCPCountGroundSplints", "ResetAll", "MCPCountSplints"],
        )
        self.assertNotIn("SetBrokenLegs", player.names(), "vanilla's deactivation cleared the legs")

    def test_with_no_room_the_splint_lands_on_the_ground_and_the_inventory_stays(self) -> None:
        for lying in (0, 1):
            with self.subTest(splints_already_lying=lying):
                player = _HealPlayer(legs=2, legs_after_reset_all=0, splint_worn=True, room=False, lying=lying)
                before = list(player.inventory)
                self.assertEqual(player.heal(full=True), "ground")
                self.assertEqual(player.inventory, before)
                self.assertEqual(player.ground, ["Splint"] * (lying + 1))
                self.assertEqual(
                    self._counts(player),
                    ["MCPCountSplints", "MCPCountGroundSplints", "ResetAll", "MCPCountSplints", "MCPCountGroundSplints"],
                )

    def test_when_vanilla_cannot_place_the_splint_the_answer_claims_no_return(self) -> None:
        # Review R2 F1: CreateInInventory and the ground creation both return null;
        # vanilla deletes the applied splint all the same, so no Splint exists.
        # A Splint that was already lying there is not a return.
        for lying in (0, 1):
            with self.subTest(splints_already_lying=lying):
                player = _HealPlayer(
                    legs=2, legs_after_reset_all=0, splint_worn=True, room=False, ground_room=False, lying=lying
                )
                before = list(player.inventory)
                self.assertEqual(player.heal(full=True), "none")
                self.assertEqual(player.inventory, before)
                self.assertEqual(player.ground, ["Splint"] * lying)
                self.assertEqual(
                    self._counts(player),
                    ["MCPCountSplints", "MCPCountGroundSplints", "ResetAll", "MCPCountSplints", "MCPCountGroundSplints"],
                )

    def test_without_a_splint_coming_off_nothing_is_returned_or_counted(self) -> None:
        for case in (
            {},
            {"splint_worn": True, "legs_modifier_active": False},
            {"splint_worn": True, "managers": False},
        ):
            with self.subTest(**case):
                player = _HealPlayer(legs=2, legs_after_reset_all=0, **case)
                before = list(player.inventory)
                self.assertEqual(player.heal(full=False), "")
                self.assertEqual(player.inventory, before)
                self.assertEqual(player.ground, [])
                self.assertNotIn("MCPCountSplints", player.names())
                self.assertNotIn("MCPCountGroundSplints", player.names())


class PlayerHealContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.care = _source(CARE_PATH)
        self.heal = method_body(self.care, HEAL_SERVER)
        self.counter = method_body(self.care, COUNT_SPLINTS)
        self.bridge = _source(BRIDGE_PATH)
        self.dispatch = method_body(self.bridge, DISPATCH_HEAL)

    def test_the_heal_gives_no_immunity_boost_and_never_moves_the_player(self) -> None:
        for forbidden in (
            "MDF_IMMUNITYBOOST",
            "ActivateModifier",
            "SetPosition",
            "SetTransform",
            "SetOrientation",
            "StartCommand",
            "GetCommand_Vehicle",
            "GetTransport",
            "GetParent",
            "SetHealth(",
            "ResetPlayer",
            "FixAllInventoryItems",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.heal)
                self.assertNotIn(forbidden, self.dispatch)

    def test_the_only_inventory_change_is_vanillas_splint_return(self) -> None:
        # Review R1 F1, decided by the orchestrator: the inventory does change in
        # one documented case, a worn splint, and only through vanilla's own path
        # (ResetAll -> BrokenLegsMdfr.OnDeactivate -> RemoveSplint, brokenlegs.c:38-44).
        # Nothing removes the splint by hand, nothing suppresses that path, and
        # the counters that tell where the Splint went only read.
        ground_counter = method_body(self.care, COUNT_GROUND_SPLINTS)
        self.assertEqual(self.heal.count("m_ModifiersManager.ResetAll();"), 1)
        for write in (
            "RemoveSplint",
            "Splint_Applied",
            "Delete(",
            "CreateInInventory",
            "CreateInHands",
            "SpawnEntity",
            "ServerDropEntity",
            "PredictiveDropEntity",
            "LocalDestroyEntity",
            "TakeEntity",
            "GetInventory",
            "GetHumanInventory",
            "DeactivateModifier",
            "SetModifierLock",
            "SetLock(",
        ):
            with self.subTest(write=write):
                self.assertNotIn(write, self.heal)
                self.assertNotIn(write, self.dispatch)
                self.assertNotIn(write, ground_counter)
        self.assertEqual(
            set(re.findall(r"(\w+)\s*\(", self.counter)),
            {"GetInventory", "EnumerateInventory", "while", "Count", "if", "Cast", "Get", "GetItemInHands"},
            "MCPCountSplints may only read the inventory",
        )
        self.assertEqual(
            set(re.findall(r"(\w+)\s*\(", ground_counter)),
            {"GetGame", "GetObjectsAtPosition3D", "GetPosition", "while", "Count", "Cast", "Get", "if", "GetHierarchyParent"},
            "MCPCountGroundSplints may only read the objects around the player",
        )
        self.assertIn("protected int MCPCountSplints()", self.care, "only the heal calls it")
        self.assertIn("protected int MCPCountGroundSplints()", self.care, "only the heal calls it")
        # Vanilla's two conditions, read before anything changes; both counts
        # before ResetAll and again after; a place is named only where a new
        # Splint was found, and "none" when a splint came off and none was
        # (review R2 F1: the ground is confirmed, never inferred).
        _in_order(
            self,
            self.heal,
            "if (m_ModifiersManager && IsWearingSplint())",
            "splintComesOff = m_ModifiersManager.IsModifierActive(eModifiers.MDF_BROKEN_LEGS);",
            "splintsBefore = MCPCountSplints();",
            "groundSplintsBefore = MCPCountGroundSplints();",
            "damageWasAllowed = GetAllowDamage();",
            "DamageSystem.ResetAllZones(this);",
            "m_ModifiersManager.ResetAll();",
            'splintTo = "none";',
            "if (MCPCountSplints() > splintsBefore)",
            'splintTo = "inventory";',
            "else if (MCPCountGroundSplints() > groundSplintsBefore)",
            'splintTo = "ground";',
            "return splintTo;",
        )
        self.assertEqual(set(re.findall(r'splintTo = "(\w*)";', self.heal)), {"", "none", "inventory", "ground"})
        # The applied splint's Delete is deferred (object.c:82-85): it is never re-read.
        self.assertEqual(self.heal.count("IsWearingSplint()"), 1)

    def test_the_heal_is_a_public_player_base_method(self) -> None:
        # MCPBridge (5_Mission) calls it: protected would not compile across
        # modules (ES-PROTECTED-CROSS-MODULE).
        self.assertIn("modded class PlayerBase", self.care)
        self.assertIn("\n\t" + HEAL_SERVER + "\n", self.care)
        self.assertNotIn("protected " + HEAL_SERVER, self.care)

    def test_refusals_come_before_anything_changes(self) -> None:
        _in_order(
            self,
            self.dispatch,
            "if (!command.args)",
            'result.error = "bad_args";',
            "healPlayer = ResolvePlayer(command.args, healResolveError);",
            "result.error = healResolveError;",
            "if (!healPlayer.IsAlive())",
            'result.error = "player_dead";',
            "healReport = new MCPPlayerHeal();",
            "healReport.in_vehicle = healPlayer.IsInVehicle();",
            "FillPlayerVitals(healPlayer, healReport.before);",
            "healSplintTo = healPlayer.MCPHealServer(command.args.full);",
            "FillPlayerVitals(healPlayer, healReport.after);",
            'healReport.splint_returned = healSplintTo == "inventory" || healSplintTo == "ground";',
            "healReport.splint_returned_to = healSplintTo;",
            "result.player_heal = healReport;",
            "result.ok = true;",
        )
        self.assertEqual(set(re.findall(r'result\.error = "(\w+)";', self.dispatch)), {"bad_args", "player_dead"})
        self.assertEqual(self.dispatch.count("result.ok = true;"), 1)
        self.assertIn("healReport.full = command.args.full;", self.dispatch)

    def test_the_answer_claims_a_return_only_where_a_new_splint_was_found(self) -> None:
        # Review R2 F1: the dispatch's own expression for splint_returned, run on
        # each place MCPHealServer can name.
        assignments = re.findall(r"healReport\.splint_returned = ([^;]+);", self.dispatch)
        self.assertEqual(len(assignments), 1, assignments)
        signature = "bool SplintReturned(string healSplintTo)"
        returned = translate(f"{signature}\n{{\n\treturn {assignments[0]};\n}}\n", signature, {})
        for place, expected in (("inventory", True), ("ground", True), ("none", False), ("", False)):
            with self.subTest(place=place):
                self.assertIs(returned(EnforceString.of(place)), expected)

    def test_the_vitals_are_the_contract_reads(self) -> None:
        fill = _flat(method_body(self.bridge, FILL_VITALS))
        for read in (
            'vitals.health = target.GetHealth01("", "");',
            'vitals.blood = target.GetHealth01("", "Blood");',
            'vitals.shock = target.GetHealth01("", "Shock");',
            "vitals.broken_legs = target.GetBrokenLegs() != eBrokenLegs.NO_BROKEN_LEGS;",
            "vitals.unconscious = target.IsUnconscious();",
            "vitals.bleeding_sources = target.GetBleedingSourceCount();",
            "vitalsWater = target.GetStatWater(); if (vitalsWater) { vitals.water = vitalsWater.Get(); }",
            "vitalsEnergy = target.GetStatEnergy(); if (vitalsEnergy) { vitals.energy = vitalsEnergy.Get(); }",
        ):
            with self.subTest(read=read):
                self.assertIn(read, fill)


class _Item:
    def __init__(self, kind: str) -> None:
        self.kind = kind


class PlayerHealSplintCountTest(unittest.TestCase):
    """MCPCountSplints translated and run: the Splint items in the inventory and the hands."""

    def _count(self, enumerated: list[_Item], hands: _Item | None) -> int:
        class _Array:
            def __init__(self) -> None:
                self.items: list[_Item] = []

            def Count(self) -> int:
                return len(self.items)

            def Get(self, index: int) -> _Item:
                return self.items[index]

        def enumerate_inventory(traversal: str, out: _Array) -> bool:
            self.assertEqual(traversal, "PREORDER")
            out.items.extend(enumerated)
            return bool(enumerated)

        namespace: dict[str, Any] = {
            "NewEntityArray": _Array,
            "GetInventory": lambda: SimpleNamespace(EnumerateInventory=enumerate_inventory),
            "InventoryTraversalType": SimpleNamespace(PREORDER="PREORDER"),
            # The script class Splint (splint.c:1); Splint_Applied is a Clothing (splint.c:12).
            "Splint": SimpleNamespace(Cast=lambda item: item if isinstance(item, _Item) and item.kind == "Splint" else None),
            "GetItemInHands": lambda: hands,
        }
        rewrites = (
            (re.compile(r"new array<EntityAI>\(\)"), "NewEntityArray()"),
            (re.compile(r"array<EntityAI>"), "EntityArray"),
        )
        count = translate(
            _rewritten(_source(CARE_PATH), COUNT_SPLINTS, rewrites),
            COUNT_SPLINTS,
            namespace,
            class_types=("EntityArray",),
        )
        return count()

    def test_only_splint_items_count_never_the_applied_one(self) -> None:
        enumerated = [_Item("SurvivorM_Mirek"), _Item("Splint"), _Item("Splint_Applied"), _Item("Bandage"), _Item("Splint")]
        self.assertEqual(self._count(enumerated, None), 2)
        self.assertEqual(self._count([_Item("Splint_Applied")], _Item("Bandage")), 0)

    def test_a_splint_returned_into_the_hands_raises_the_count_either_way(self) -> None:
        # Whether or not the enumeration holds the hands, the count is taken the
        # same way before and after, so a new Splint in the hands always shows.
        before = self._count([_Item("Bandage")], None)
        held = _Item("Splint")
        for enumerated in ([_Item("Bandage")], [_Item("Bandage"), held]):
            with self.subTest(enumeration_holds_the_hands=len(enumerated) == 2):
                self.assertGreater(self._count(enumerated, held), before)


class _Placed:
    """An object GetObjectsAtPosition3D returns, with its hierarchy parent."""

    def __init__(self, kind: str, parent: object | None = None) -> None:
        self.kind = kind
        self.parent = parent

    def GetHierarchyParent(self) -> object | None:
        return self.parent


class PlayerHealGroundSplintCountTest(unittest.TestCase):
    """MCPCountGroundSplints translated and run: the Splint items loose on the
    ground around the player (review R2 F1)."""

    PLAYER_POSITION = "player position"

    def _count(self, placed: list[_Placed]) -> tuple[int, list[tuple[object, float]]]:
        class _Array:
            def __init__(self) -> None:
                self.items: list[object] = []

            def Count(self) -> int:
                return len(self.items)

            def Get(self, index: int) -> object:
                return self.items[index]

        queries: list[tuple[object, float]] = []

        def objects_at(position: object, radius: float, objects: _Array, cargos: _Array) -> None:
            queries.append((position, radius))
            objects.items.extend(placed)

        namespace: dict[str, Any] = {
            "NewObjectArray": _Array,
            "NewCargoArray": _Array,
            "GetGame": lambda: SimpleNamespace(GetObjectsAtPosition3D=objects_at),
            "GetPosition": lambda: self.PLAYER_POSITION,
            "MCP_SPLINT_GROUND_RADIUS_M": _splint_ground_radius(),
            # The script class Splint (splint.c:1); Splint_Applied is a Clothing (splint.c:12).
            "Splint": SimpleNamespace(Cast=lambda obj: obj if isinstance(obj, _Placed) and obj.kind == "Splint" else None),
        }
        rewrites = (
            (re.compile(r"new array<Object>\(\)"), "NewObjectArray()"),
            (re.compile(r"new array<CargoBase>\(\)"), "NewCargoArray()"),
            (re.compile(r"array<Object>"), "ObjectArray"),
            (re.compile(r"array<CargoBase>"), "CargoArray"),
        )
        count = translate(
            _rewritten(_source(CARE_PATH), COUNT_GROUND_SPLINTS, rewrites),
            COUNT_GROUND_SPLINTS,
            namespace,
            class_types=("ObjectArray", "CargoArray", "Splint"),
        )
        return count(), queries

    def test_only_splints_with_no_parent_count(self) -> None:
        # Nothing in an inventory, a container or anyone's hands counts: those
        # have a hierarchy parent (entityai.c:880).
        player = _Placed("SurvivorM_Mirek")
        backpack = _Placed("TaloonBag")
        other = _Placed("SurvivorF_Linda")
        placed = [
            player,
            _Placed("Splint"),
            _Placed("Splint", backpack),
            _Placed("Splint_Applied", player),
            _Placed("Splint", player),
            _Placed("Bandage"),
            _Placed("Splint", other),
            backpack,
            _Placed("Splint"),
        ]
        self.assertEqual(self._count(placed)[0], 2)
        self.assertEqual(self._count([player, _Placed("Splint_Applied", player)])[0], 0)
        self.assertEqual(self._count([])[0], 0)

    def test_one_sphere_around_the_player_that_covers_vanillas_half_metre(self) -> None:
        # RemoveSplint's ground fallback is half a metre in front of the player
        # (miscgameplayfunctions.c:1640, playerbase.c:6480-6484), dropped to the
        # surface below: the radius leaves room for that drop and stays small.
        count, queries = self._count([_Placed("Splint")])
        self.assertEqual(count, 1)
        radius = _splint_ground_radius()
        self.assertEqual(queries, [(self.PLAYER_POSITION, radius)])
        self.assertGreaterEqual(radius, 2.0)
        self.assertLessEqual(radius, 5.0)


# --- godmode, run ---------------------------------------------------------------


class GodmodeMemoryTest(unittest.TestCase):
    """MCPGodmode's per-identity memory: on unless switched off this mission."""

    def test_an_identity_is_on_until_it_is_switched_off_and_on_again(self) -> None:
        godmode = _Godmode()
        self.assertTrue(godmode.IsOnFor(_s("76561198000000001")), "before any mission state")
        godmode.ResetMission()
        self.assertTrue(godmode.IsOnFor(_s("76561198000000001")))
        godmode.Remember(_s("76561198000000001"), False)
        self.assertFalse(godmode.IsOnFor(_s("76561198000000001")))
        self.assertTrue(godmode.IsOnFor(_s("76561198000000002")), "another identity stays on")
        godmode.Remember(_s("76561198000000001"), True)
        self.assertTrue(godmode.IsOnFor(_s("76561198000000001")))
        self.assertEqual(godmode.off_ids(), set())

    def test_remember_creates_the_memory_when_the_mission_has_none(self) -> None:
        godmode = _Godmode()
        godmode.Remember(_s("a"), False)
        self.assertEqual(godmode.off_ids(), {b"a"})

    def test_a_new_mission_forgets_every_choice_and_the_top_up_clock(self) -> None:
        godmode = _Godmode()
        godmode.ResetMission()
        godmode.Remember(_s("a"), False)
        godmode.G.s_TopUpAccumS = 7.5
        godmode.ResetMission()
        self.assertTrue(godmode.IsOnFor(_s("a")))
        self.assertEqual(godmode.G.s_TopUpAccumS, 0.0)
        self.assertIsInstance(godmode.G.s_Players, _ManArray)


class GodmodeSwitchTest(unittest.TestCase):
    def test_is_on_is_damage_off_on_that_body(self) -> None:
        godmode = _Godmode()
        self.assertTrue(godmode.IsOn(_Body(allow_damage=False)))
        self.assertFalse(godmode.IsOn(_Body(allow_damage=True)))
        self.assertFalse(godmode.IsOn(None))

    def test_choose_remembers_for_the_identity_then_switches_the_body(self) -> None:
        godmode = _Godmode()
        godmode.ResetMission()
        body = _Body(allow_damage=False)
        godmode.Choose(body, _s("76561198000000001"), False)
        self.assertEqual(body.damage_writes, [True])
        self.assertFalse(godmode.IsOnFor(_s("76561198000000001")))
        godmode.Choose(body, _s("76561198000000001"), True)
        self.assertEqual(body.damage_writes, [True, False])
        self.assertTrue(godmode.IsOnFor(_s("76561198000000001")))
        self.assertEqual(
            godmode.printed,
            [
                "[DayZ_MCP] godmode on=0 uid=76561198000000001 reason=player_godmode",
                "[DayZ_MCP] godmode on=1 uid=76561198000000001 reason=player_godmode",
            ],
        )

    def test_switching_on_tops_up_at_once_and_switching_off_does_not(self) -> None:
        godmode = _Godmode()
        low = _Body(water=320.0, energy=310.0)
        godmode.Apply(low, False)
        self.assertEqual((low.water.Get(), low.energy.Get()), (320.0, 310.0))
        godmode.Apply(low, True)
        floor = LOW_THRESHOLD + godmode.G.TOPUP_MARGIN
        self.assertEqual((low.water.Get(), low.energy.Get()), (floor, floor))
        godmode.Apply(None, True)

    def test_the_contract_switches_only_the_player(self) -> None:
        care = _without_comments(_source(CARE_PATH))
        godmode_class = _block_after(care, care.index("class MCPGodmode"))
        self.assertEqual(
            re.findall(r"(\w+)\.SetAllowDamage\(([^;]*)\);", godmode_class),
            [("player", "true"), ("player", "!on")],
        )
        for forbidden in ("GetParent", "GetTransport", "GetCommand_Vehicle", "Transport", "CarScript"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, godmode_class)


class GodmodeDefaultOnTest(unittest.TestCase):
    """Every body an identity gets starts with its identity's godmode, on by default."""

    def test_a_body_gets_its_identitys_choice_on_by_default(self) -> None:
        godmode = _Godmode()
        godmode.ResetMission()
        fresh = _Body(identity=_Identity("76561198000000001"))
        godmode.OnBodyReady(fresh, fresh.identity)
        self.assertEqual(fresh.damage_writes, [False])
        godmode.Remember(_s("76561198000000001"), False)
        respawned = _Body(identity=_Identity("76561198000000001"))
        godmode.OnBodyReady(respawned, respawned.identity)
        self.assertEqual(respawned.damage_writes, [True])
        other = _Body(identity=_Identity("76561198000000002"))
        godmode.OnBodyReady(other, other.identity)
        self.assertEqual(other.damage_writes, [False])
        self.assertEqual(
            godmode.printed,
            [
                "[DayZ_MCP] godmode on=1 uid=76561198000000001 reason=connect",
                "[DayZ_MCP] godmode on=0 uid=76561198000000001 reason=connect",
                "[DayZ_MCP] godmode on=1 uid=76561198000000002 reason=connect",
            ],
        )

    def test_a_body_without_identity_gets_the_default_and_none_is_skipped(self) -> None:
        godmode = _Godmode()
        godmode.ResetMission()
        body = _Body()
        godmode.OnBodyReady(body, None)
        self.assertEqual(body.damage_writes, [False])
        godmode.OnBodyReady(None, _Identity("x"))
        self.assertEqual(godmode.printed, ["[DayZ_MCP] godmode on=1 uid= reason=connect"])

    def test_a_body_leaving_its_identity_loses_godmode_and_the_choice_stays(self) -> None:
        godmode = _Godmode()
        godmode.ResetMission()
        leaving = _Body(allow_damage=False, identity=_Identity("76561198000000001"))
        godmode.ReleaseBody(leaving, _s("respawn"))
        self.assertEqual(leaving.damage_writes, [True])
        self.assertTrue(godmode.IsOnFor(_s("76561198000000001")))
        vanilla = _Body(allow_damage=True)
        godmode.ReleaseBody(vanilla, _s("disconnect"))
        godmode.ReleaseBody(None, _s("disconnect"))
        self.assertEqual(vanilla.damage_writes, [])
        self.assertEqual(godmode.printed, ["[DayZ_MCP] godmode released uid=76561198000000001 reason=respawn"])


class GodmodeTopUpTest(unittest.TestCase):
    def test_the_floor_is_above_the_lethal_threshold_and_below_a_new_character(self) -> None:
        margin = _godmode_constant("TOPUP_MARGIN")
        interval = _godmode_constant("TOPUP_INTERVAL_S")
        self.assertEqual((margin, interval), (200.0, 10.0))
        floor = LOW_THRESHOLD + margin
        self.assertLess(floor, STAT_INIT)
        # The fastest drain (sprint 0.6 + basal 0.01 per second, times 1 + value / 5000,
        # thirst.c:36-37) over one interval must stay inside the margin.
        self.assertLess((0.6 + 0.01) * (1.0 + floor / STAT_MAX) * interval, margin)

    def test_top_up_raises_to_the_floor_and_never_lowers(self) -> None:
        godmode = _Godmode()
        floor = LOW_THRESHOLD + godmode.G.TOPUP_MARGIN
        for water, energy, expected in (
            (100.0, 499.0, (floor, floor)),
            (floor, floor, (floor, floor)),
            (4800.0, 650.0, (4800.0, 650.0)),
        ):
            with self.subTest(water=water, energy=energy):
                body = _Body(water=water, energy=energy)
                godmode.TopUp(body)
                self.assertEqual((body.water.Get(), body.energy.Get()), expected)
        godmode.TopUp(None)

    def test_each_interval_tops_up_only_living_godmode_bodies(self) -> None:
        on_low = _Body(allow_damage=False, water=310.0, energy=310.0)
        off_low = _Body(allow_damage=True, water=310.0, energy=310.0)
        dead_low = _Body(allow_damage=False, alive=False, water=310.0, energy=310.0)
        godmode = _Godmode(players=[on_low, off_low, dead_low, object()])
        godmode.ResetMission()
        floor = LOW_THRESHOLD + godmode.G.TOPUP_MARGIN
        godmode.Tick(9.5)
        self.assertEqual(on_low.water.Get(), 310.0, "before the interval")
        godmode.Tick(0.5)
        self.assertEqual((on_low.water.Get(), on_low.energy.Get()), (floor, floor))
        self.assertEqual((off_low.water.Get(), dead_low.water.Get()), (310.0, 310.0))
        self.assertEqual(godmode.G.s_TopUpAccumS, 0.0)
        self.assertEqual(godmode.G.s_Players.Count(), 0, "no body is kept between passes")

    def test_without_a_game_the_pass_does_nothing(self) -> None:
        godmode = _Godmode(players=[_Body(allow_damage=False, water=10.0)], game=False)
        godmode.Tick(60.0)
        self.assertEqual(godmode.G.s_TopUpAccumS, 0.0)


class GodmodeMissionHookTest(unittest.TestCase):
    """The MissionServer overrides translated and run, in their order."""

    def _run(self, signature: str, *args: Any) -> list[tuple[Any, ...]]:
        order: list[tuple[Any, ...]] = []

        def recorder(owner: str):
            class _Recorder:
                def __getattr__(self, name: str):
                    return lambda *values: order.append((owner, name) + values)

            return _Recorder()

        bridge = SimpleNamespace(OnTick=lambda value: order.append(("bridge", "OnTick", value)))
        namespace: dict[str, Any] = {
            "super": recorder("super"),
            "MCPGodmode": recorder("MCPGodmode"),
            "MCPBridge": SimpleNamespace(Get=lambda: bridge),
        }
        hook = translate(
            _source(MISSION_PATH),
            signature,
            namespace,
            class_types=("PlayerBase", "PlayerIdentity", "MCPBridge"),
        )
        hook(*args)
        return order

    def test_a_connecting_body_gets_godmode_after_vanilla(self) -> None:
        player, identity = object(), object()
        self.assertEqual(
            self._run(INVOKE_ON_CONNECT, player, identity),
            [("super", "InvokeOnConnect", player, identity), ("MCPGodmode", "OnBodyReady", player, identity)],
        )

    def test_a_respawning_or_leaving_body_is_released_before_vanilla(self) -> None:
        player, identity = object(), object()
        respawn = self._run(ON_RESPAWN, identity, player)
        self.assertEqual(respawn[1], ("super", "OnClientRespawnEvent", identity, player))
        self.assertEqual(respawn[0][:3], ("MCPGodmode", "ReleaseBody", player))
        self.assertEqual(respawn[0][3].text, "respawn")
        leave = self._run(ON_DISCONNECT, player)
        self.assertEqual(leave[1], ("super", "InvokeOnDisconnect", player))
        self.assertEqual(leave[0][:3], ("MCPGodmode", "ReleaseBody", player))
        self.assertEqual(leave[0][3].text, "disconnect")

    def test_the_mission_resets_the_memory_and_ticks_the_top_up_without_the_bridge(self) -> None:
        self.assertEqual(
            self._run(ON_MISSION_START),
            [("super", "OnMissionStart"), ("MCPGodmode", "ResetMission"), ("bridge", "OnTick", 0.0)],
        )
        self.assertEqual(
            self._run(ON_UPDATE, 0.25),
            [("super", "OnUpdate", 0.25), ("MCPGodmode", "Tick", 0.25), ("bridge", "OnTick", 0.25)],
        )
        update = method_body(_source(MISSION_PATH), ON_UPDATE)
        self.assertLess(update.index("MCPGodmode.Tick(timeslice);"), update.index("if (bridge)"))

    def test_the_overrides_keep_the_vanilla_parameter_names(self) -> None:
        # Enforce binds an override's parameters by name (ES-OVERRIDE-PARAM-NAME-MISMATCH):
        # missionserver.c:422, :429 and :598.
        mission = _source(MISSION_PATH)
        for signature in (INVOKE_ON_CONNECT, ON_RESPAWN, ON_DISCONNECT):
            with self.subTest(signature=signature):
                self.assertEqual(mission.count(signature), 1)


class GodmodeDispatchContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatch = method_body(_source(BRIDGE_PATH), DISPATCH_GODMODE)

    def test_refusals_come_before_the_choice_and_the_reply_brackets_it(self) -> None:
        _in_order(
            self,
            self.dispatch,
            "if (!command.args)",
            'result.error = "bad_args";',
            "godmodePlayer = ResolvePlayer(command.args, godmodeResolveError);",
            "result.error = godmodeResolveError;",
            "if (!godmodePlayer.IsAlive())",
            'result.error = "player_dead";',
            "godmodeIdentity = godmodePlayer.GetIdentity();",
            'result.error = "no_identity";',
            "godmodeReport = new MCPPlayerGodmode();",
            "godmodeReport.before = MCPGodmode.IsOn(godmodePlayer);",
            "MCPGodmode.Choose(godmodePlayer, godmodeIdentity.GetPlainId(), command.args.godmode);",
            "godmodeReport.after = MCPGodmode.IsOn(godmodePlayer);",
            "result.player_godmode = godmodeReport;",
            "result.ok = true;",
        )
        self.assertEqual(
            set(re.findall(r'result\.error = "(\w+)";', self.dispatch)), {"bad_args", "player_dead", "no_identity"}
        )

    def test_the_dispatch_switches_nothing_itself(self) -> None:
        for forbidden in ("SetAllowDamage", "GetParent", "GetTransport", "GetCommand_Vehicle", "SetCanBeDestroyed"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.dispatch)


# --- messages and query fields -------------------------------------------------


class HealGodmodeMessagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = _source(MESSAGES_PATH)
        self.bridge = _source(BRIDGE_PATH)

    def test_args_carry_the_two_choices_without_defaults(self) -> None:
        members = _class_members(self.messages, "MCPArgs")
        for member in (("bool", "full"), ("bool", "godmode"), ("string", "uid")):
            with self.subTest(member=member):
                self.assertEqual(members.count(member), 1)
        constructor = method_body(self.messages, "void MCPArgs()")
        for name in ("full", "godmode"):
            with self.subTest(name=name):
                self.assertIsNone(re.search(rf"\b{name}\s*=", constructor))

    def test_the_replies_are_primitives_declared_before_the_result(self) -> None:
        self.assertEqual(_class_members(self.messages, "MCPPlayerVitals"), EXPECTED_VITALS_MEMBERS)
        self.assertEqual(_class_members(self.messages, "MCPPlayerHeal"), EXPECTED_HEAL_MEMBERS)
        self.assertEqual(_class_members(self.messages, "MCPPlayerGodmode"), EXPECTED_GODMODE_MEMBERS)
        self.assertEqual(
            _flat(method_body(self.messages, "void MCPPlayerHeal()")),
            "before = new MCPPlayerVitals(); after = new MCPPlayerVitals();",
        )
        for name in ("MCPPlayerVitals", "MCPPlayerHeal", "MCPPlayerGodmode"):
            with self.subTest(name=name):
                self.assertLess(self.messages.index(f"class {name}"), self.messages.index("class MCPResult"))

    def test_the_result_owns_one_reference_per_verb_before_player_move(self) -> None:
        members = _class_members(self.messages, "MCPResult")
        self.assertEqual(members.count(("ref MCPPlayerHeal", HEAL)), 1)
        self.assertEqual(members.count(("ref MCPPlayerGodmode", GODMODE)), 1)
        index = members.index(("ref MCPPlayerHeal", HEAL))
        self.assertEqual(
            members[index : index + 3],
            [("ref MCPPlayerHeal", HEAL), ("ref MCPPlayerGodmode", GODMODE), ("ref MCPPlayerMove", "player_move")],
        )

    def test_both_query_rows_add_godmode_after_their_fields(self) -> None:
        self.assertEqual(
            _class_members(self.messages, "MCPAllPlayer"),
            [("string", "uid"), ("ref array<float>", "pos"), ("float", "health"), ("bool", "in_vehicle"), ("bool", "godmode")],
        )
        self.assertEqual(
            _class_members(self.messages, "MCPPlayerState"),
            [("string", "name"), ("ref array<float>", "pos"), ("bool", "godmode")],
        )

    def test_both_reads_fill_godmode_from_the_server_body(self) -> None:
        all_players = method_body(self.bridge, BUILD_ALL)
        _in_order(
            self,
            all_players,
            "player.in_vehicle = p.IsInTransport();",
            "player.godmode = MCPGodmode.IsOn(p);",
            "players.Insert(player);",
        )
        state = method_body(self.bridge, BUILD_STATE)
        _in_order(self, state, "state.pos.Insert(pos[2]);", "state.godmode = MCPGodmode.IsOn(player);", "return state;")


class HealGodmodePruneTest(unittest.TestCase):
    def test_each_reply_is_prunable_and_kept_for_its_verb(self) -> None:
        for field, filled in (
            (
                HEAL,
                {
                    "full": 1,
                    "in_vehicle": 0,
                    "splint_returned": 1,
                    "splint_returned_to": "inventory",
                    "before": {"health": 0.03},
                    "after": {"health": 1.0},
                },
            ),
            (GODMODE, {"before": 1, "after": 0}),
        ):
            with self.subTest(field=field):
                self.assertIn(field, result_prune.PRUNABLE_FIELDS)
                self.assertNotIn(field, result_prune.OWNED_SCALAR_FIELDS)
                self.assertLess(
                    result_prune.PRUNABLE_FIELDS.index(field), result_prune.PRUNABLE_FIELDS.index("player_move")
                )
                unfilled = result_prune.prune_unfilled_fields("world_spawn", {"ok": 1, field: {}})
                self.assertNotIn(field, unfilled)
                kept = result_prune.prune_unfilled_fields(field, {"ok": 1, field: dict(filled), "players": []})
                self.assertEqual(kept[field], filled)
                self.assertNotIn("players", kept)


# --- the tools ---------------------------------------------------------------


class _ResultState:
    """The daemon queue for one bridge answer."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result: dict[str, Any] | None = result
        self.enqueued: list[tuple[str, dict[str, Any], str | None]] = []

    def enqueue_command(self, cmd: str, args: dict, peer: str | None = None, **_kwargs: Any) -> tuple[int, dict]:
        self.enqueued.append((cmd, dict(args), peer))
        return 200, {"id": 7}

    def status_snapshot(self) -> dict[str, Any]:
        version = f"{server.EXPECTED_BRIDGE_VERSION}~1.29.0"
        return {
            "peers": {
                peer: {
                    "last_poll_age_s": 0.1,
                    "queue_depth": 0,
                    "version": version,
                    "binding_state": "BOUND",
                    "instance_prefix": peer,
                    "bound_last_poll_age_s": 0.1,
                    "capabilities": announced_capabilities(peer),
                }
                for peer in ("server", "client")
            },
            "results_pending": 0,
        }

    def take_result(self, command_id: int, remove: bool = True) -> dict[str, Any] | None:
        result, self.result = self.result, None
        return result

    def abandon_command(self, command_id: int, reason: str) -> None:
        raise AssertionError(f"unexpected timeout for {command_id}: {reason}")


class _BridgeRuntime(server.Runtime):
    def __init__(self, config: server.ServerConfig, result: dict[str, Any]) -> None:
        super().__init__(config)
        self.loopback = SimpleNamespace(state=_ResultState(result))

    def ensure_peer_allowed(self, peer: str) -> None:
        return None


class HealGodmodeToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    async def _forwarded(self, tool: str, arguments: dict[str, Any]) -> tuple[Any, ...]:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            await self.app.call_tool(tool, arguments)
        call.assert_awaited_once()
        return call.await_args.args

    async def _refused(self, tool: str, arguments: dict[str, Any]) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(tool, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    def test_both_tools_are_registered_with_closed_schemas(self) -> None:
        self.assertEqual(
            self.tools[HEAL].parameters["properties"].keys(), {"uid", "full", "timeout_s"}
        )
        self.assertEqual(self.tools[HEAL].parameters.get("required"), [])
        self.assertIs(self.tools[HEAL].parameters["properties"]["full"]["default"], True)
        self.assertEqual(
            self.tools[GODMODE].parameters["properties"].keys(), {"on", "uid", "timeout_s"}
        )
        self.assertEqual(self.tools[GODMODE].parameters.get("required"), ["on"])
        for tool in COMMANDS:
            with self.subTest(tool=tool):
                self.assertIs(self.tools[tool].parameters.get("additionalProperties"), False)
                self.assertIn(tool, server._CLOSED_SCHEMA_TOOLS)

    async def test_player_heal_forwards_full_and_a_set_uid_only(self) -> None:
        self.assertEqual(await self._forwarded(HEAL, {}), (HEAL, {"full": True}, "server", 15.0))
        self.assertEqual(
            await self._forwarded(HEAL, {"full": False, "uid": "76561198000000001", "timeout_s": 2.0}),
            (HEAL, {"full": False, "uid": "76561198000000001"}, "server", 2.0),
        )

    async def test_player_godmode_forwards_the_choice_as_godmode(self) -> None:
        self.assertEqual(await self._forwarded(GODMODE, {"on": False}), (GODMODE, {"godmode": False}, "server", 15.0))
        self.assertEqual(
            await self._forwarded(GODMODE, {"on": True, "uid": "76561198000000001"}),
            (GODMODE, {"godmode": True, "uid": "76561198000000001"}, "server", 15.0),
        )

    async def test_bad_values_and_unknown_keys_never_reach_the_bridge(self) -> None:
        for tool, arguments, expected in (
            (GODMODE, {}, ("\non\n", "Field required")),
            (GODMODE, {"on": 1}, ("\non\n", "valid boolean")),
            (GODMODE, {"on": "false"}, ("\non\n", "valid boolean")),
            (GODMODE, {"on": False, "uid": 7}, ("\nuid\n", "valid string")),
            (GODMODE, {"on": False, "id": "76561198000000001"}, ("bad_args: unexpected arguments: id",)),
            (GODMODE, {"enabled": False}, ("bad_args: unexpected arguments: enabled",)),
            (HEAL, {"full": 1}, ("\nfull\n", "valid boolean")),
            (HEAL, {"full": "no"}, ("\nfull\n", "valid boolean")),
            (HEAL, {"uid": 7}, ("\nuid\n", "valid string")),
            (HEAL, {"player": "76561198000000001"}, ("bad_args: unexpected arguments: player",)),
            (HEAL, {"timeout_s": True}, ("\ntimeout_s\n", "valid number")),
            (HEAL, {"timeout_s": 0.0}, ("bad_timeout",)),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                text = await self._refused(tool, arguments)
                for part in expected:
                    self.assertIn(part, text)

    def test_descriptions_state_the_default_and_the_way_back(self) -> None:
        line = server.GODMODE_DEFAULT_LINE
        for phrase in (
            "Godmode is ON by default",
            "every run that loads DayZ_MCP",
            "immune to damage (falls, hits, hunger and thirst)",
            "connects or respawns",
            "player_godmode(on=false)",
            "vanilla damage back",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, line)
        for tool in (GODMODE, HEAL, "query_player_state", "query_all_players", "dayz_test_run"):
            with self.subTest(tool=tool):
                self.assertIn(line, self.tools[tool].description or "")
        self.assertIn(
            "The new character keeps the player identity's godmode: on by default, off when "
            "player_godmode(on=false) switched it off during this mission.",
            self.tools["player_respawn"].description or "",
        )
        self.assertIn("in_vehicle stays 0", self.tools["query_all_players"].description or "")

    def test_the_verb_descriptions_name_the_contract(self) -> None:
        heal = self.tools[HEAL].description or ""
        godmode = self.tools[GODMODE].description or ""
        for description in (heal, godmode):
            self.assertTrue(description.startswith(server.LEASE_TOOL_LINE), description)
        for phrase in (
            "seated or not",
            "never moves the player",
            "never touches its seat or vehicle",
            "full=true (default) also fills water and energy to their maximum",
            "full=false keeps their values",
            "The player's godmode stays as it was",
            "before and after",
            "bleeding_sources",
            "no_players",
            "player_not_found",
            "player_dead (a dead player cannot be healed",
        ):
            with self.subTest(tool=HEAL, phrase=phrase):
                self.assertIn(phrase, heal)
        for phrase in (
            "SetAllowDamage(false) on the player, never on its vehicle",
            "keeps its water and energy above the level where hunger and thirst cost health",
            "until the mission ends, across player_respawn",
            "player_godmode: before and after",
            "query_all_players and query_player_state report godmode",
            "no_identity",
            "player_dead",
        ):
            with self.subTest(tool=GODMODE, phrase=phrase):
                self.assertIn(phrase, godmode)

    def test_the_heal_description_and_the_changelog_state_the_splint_rule(self) -> None:
        # Review R1 F1: the one inventory change is said in plain words, with the
        # two answer fields, and nothing claims the inventory is never touched.
        # Review R2 F1: the loss vanilla allows is said too, with the value that
        # reports it and the radius the heal searches (the Enforce constant).
        radius = f"{_splint_ground_radius():g} m"
        heal = self.tools[HEAL].description or ""
        self.assertIn(SPLINT_RULE, heal)
        self.assertIn(SPLINT_LOSS, heal)
        self.assertIn(
            "splint_returned and splint_returned_to (inventory, the hands included, or "
            "ground, where a new Splint was found after the heal; none, with "
            "splint_returned false, when the splint came off and no new Splint was "
            f"found in the inventory, the hands or on the ground within {radius} of the "
            "player; empty when no splint came off)",
            heal,
        )
        self.assertNotIn("never touches the inventory", heal)
        self.assertNotIn("the inventory changes only when", heal)
        changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
        unreleased = changelog[changelog.index("## [Unreleased]") : changelog.index("## [1.3]")]
        self.assertIn(SPLINT_RULE, unreleased)
        self.assertIn(SPLINT_LOSS, unreleased)
        self.assertIn(
            "`splint_returned` and `splint_returned_to` (`inventory`, the hands "
            "included, or `ground`, where a new Splint was found after the heal; "
            "`none`, with `splint_returned` false, when the splint came off and no new "
            f"Splint was found in the inventory, the hands or on the ground within {radius} "
            "of the player; empty when no splint came off)",
            unreleased,
        )

    def test_the_compact_catalog_never_lists_the_mutating_verbs(self) -> None:
        for tool in COMMANDS:
            with self.subTest(tool=tool):
                self.assertNotIn(tool, server._INITIAL_CATALOG_NAMES)


class HealGodmodeBridgePathTest(unittest.IsolatedAsyncioTestCase):
    """Through the real call_bridge and wait_for_result, with a faked daemon queue."""

    async def _call(self, tool: str, arguments: dict[str, Any], answer: dict[str, Any]):
        config = server.ServerConfig(mode="embedded", key="heal-godmode-test", log_sink=lambda _m: None)
        runtime = _BridgeRuntime(config, answer)
        with patch.object(server, "Runtime", return_value=runtime):
            app, built = server.build_app(config)
        self.assertIs(built, runtime)
        result = await app.call_tool(tool, arguments)
        return result, runtime.loopback.state.enqueued

    @slow_test
    async def test_a_refusal_is_a_tool_error_naming_the_code(self) -> None:
        for tool, arguments, code in (
            (HEAL, {}, "player_dead"),
            (HEAL, {"uid": "x"}, "player_not_found"),
            (GODMODE, {"on": False}, "no_identity"),
            (GODMODE, {"on": True}, "no_players"),
        ):
            with self.subTest(tool=tool, code=code):
                with self.assertRaisesRegex(server.ToolError, code):
                    await self._call(tool, arguments, {"ok": 0, "error": code})

    async def test_a_reply_keeps_its_own_field_and_drops_the_unfilled_ones(self) -> None:
        reply = {"before": 1, "after": 0}
        (_content, structured), enqueued = await self._call(
            GODMODE,
            {"on": False},
            {"ok": 1, "error": "", GODMODE: dict(reply), HEAL: {}, "players": [], "state": {}},
        )
        self.assertEqual(enqueued, [(GODMODE, {"godmode": False}, "server")])
        self.assertEqual(structured[GODMODE], reply)
        for gone in (HEAL, "players", "state"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, structured)
        self.assertEqual(structured.get("next_step"), "session_heartbeat")


# --- vanilla premises ---------------------------------------------------------


@unittest.skipUnless(VANILLA.is_dir(), f"vanilla scripts tree not present: {VANILLA}")
class VanillaPremiseTest(unittest.TestCase):
    """What the design reads from DayZ 1.29's scripts, checked where they live."""

    def _vanilla(self, relative: str) -> str:
        return (VANILLA / relative).read_text(encoding="utf-8", errors="replace")

    def test_the_damage_switch_and_its_vanilla_users(self) -> None:
        obj = self._vanilla("3_game/entities/object.c")
        self.assertIn("proto native bool GetAllowDamage();", obj)
        self.assertIn("proto native void SetAllowDamage(bool val);", obj)
        diag = self._vanilla("4_world/plugins/pluginbase/plugindiagmenu/plugindiagmenu.c")
        self.assertIn("player.SetAllowDamage(false);", diag)
        # Vanilla allows damage before it raises health (repair, base building).
        repairing = _flat(self._vanilla("4_world/plugins/pluginbase/pluginrepairing.c"))
        _in_order(self, repairing, "entity.SetAllowDamage(true);", 'item.SetHealth01(damage_zone,"Health",health_coef);')

    def test_the_heal_template_order(self) -> None:
        player = self._vanilla("4_world/entities/manbase/playerbase.c")
        start = player.index('if (ammo == "Bullet_CupidsBolt" && IsAlive())')
        template = player[start : player.index("m_ShockHandler.CheckValue(true);", start)]
        _in_order(
            self,
            template,
            "DamageSystem.ResetAllZones(this);",
            "m_ModifiersManager.ResetAll();",
            "m_ModifiersManager.ActivateModifier(eModifiers.MDF_IMMUNITYBOOST);",
            "m_BleedingManagerServer.RemoveAllSources();",
            "GetPlayerStats().ResetAllStats();",
            "m_AgentPool.RemoveAllAgents();",
            "m_StaminaHandler.SetStamina(GameConstants.STAMINA_MAX);",
            "DayZPlayerSyncJunctures.SendPlayerUnconsciousness(this, false);",
        )

    def test_the_lethal_thresholds_and_a_new_characters_stats(self) -> None:
        constants = self._vanilla("3_game/playerconstants.c")
        self.assertRegex(constants, r"SL_WATER_LOW\s*=\s*300;")
        self.assertRegex(constants, r"SL_ENERGY_LOW\s*=\s*300;")
        self.assertRegex(constants, r"LOW_WATER_THRESHOLD\s*=\s*SL_WATER_LOW;")
        self.assertRegex(constants, r"LOW_ENERGY_THRESHOLD\s*=\s*SL_ENERGY_LOW;")
        self.assertRegex(constants, r"METABOLIC_SPEED_WATER_SPRINT\s*=\s*0\.6;")
        self.assertIn("water <= PlayerConstants.LOW_WATER_THRESHOLD", self._vanilla("4_world/classes/playermodifiers/modifiers/thirst.c"))
        self.assertIn("energy <= PlayerConstants.LOW_ENERGY_THRESHOLD", self._vanilla("4_world/classes/playermodifiers/modifiers/hunger.c"))
        pco = _flat(self._vanilla("4_world/classes/playerstats/playerstatspco.c"))
        self.assertIn("new PlayerStat<float> (0, PlayerConstants.SL_WATER_MAX, 600,", pco)
        self.assertIn("new PlayerStat<float> (0, PlayerConstants.SL_ENERGY_MAX, 600,", pco)
        self.assertRegex(constants, r"SL_WATER_MAX\s*=\s*5000;")

    def test_vanillas_splint_return_when_legs_heal(self) -> None:
        legs = _flat(self._vanilla("4_world/classes/playermodifiers/modifiers/conditions/brokenlegs.c"))
        deactivate = legs[legs.index("override void OnDeactivate(PlayerBase player)") : legs.index("override bool DeactivateCondition(")]
        _in_order(self, deactivate, "if ( player.IsWearingSplint() )", "MiscGameplayFunctions.RemoveSplint(player);")
        modifier = _flat(self._vanilla("4_world/classes/playermodifiers/modifierbase.c"))
        deactivation = modifier[modifier.index("void Deactivate(bool trigger = true)") : modifier.index("void OnStoreSave(ParamsWriteContext ctx);")]
        _in_order(self, deactivation, "if (!m_IsActive) return;", "OnDeactivate(m_Player);")
        misc = _flat(self._vanilla("4_world/static/miscgameplayfunctions.c"))
        remove = misc[misc.index("static void RemoveSplint( PlayerBase player )") : misc.index("static void TeleportCheck(")]
        _in_order(
            self,
            remove,
            'player.GetInventory().CreateInInventory("Splint");',
            "if (!entity)",
            'player.SpawnEntityOnGroundOnCursorDir("Splint", 0.5);',
            "attachment.Delete();",
        )
        human = _flat(self._vanilla("3_game/systems/inventory/humaninventory.c"))
        _in_order(self, human, "EntityAI newEntity = super.CreateInInventory(type);", "newEntity = CreateInHands(type);")
        splint = _flat(self._vanilla("4_world/entities/itembase/gear/medical/splint.c"))
        self.assertIn("class Splint: Inventory_Base", splint)
        self.assertIn("class Splint_Applied: Clothing", splint)
        self.assertIn("bool IsWearingSplint()", self._vanilla("4_world/entities/manbase/playerbase.c"))
        self.assertIn(
            "void Delete() { g_Game.GetCallQueue(CALL_CATEGORY_SYSTEM).Call(g_Game.ObjectDelete, this); }",
            _flat(self._vanilla("3_game/entities/object.c")),
        )

    def test_vanilla_can_lose_the_splint_and_the_heal_reads_the_ground_around_the_player(self) -> None:
        # Review R2 F1. RemoveSplint deletes the applied splint whether or not a
        # Splint item was created: the Delete is outside `if (new_item)`.
        misc = _flat(self._vanilla("4_world/static/miscgameplayfunctions.c"))
        remove = misc[misc.index("static void RemoveSplint( PlayerBase player )") : misc.index("static void TeleportCheck(")]
        _in_order(
            self,
            remove,
            'if ( attachment && attachment.GetType() == "Splint_Applied" )',
            "if (new_item)",
            "attachment.Delete();",
        )
        self.assertNotIn("attachment.Delete();", _block_after(remove, remove.index("if (new_item)")))
        # The ground creation half a metre in front can return null: CreateObjectEx's
        # own result, or null on a client (playerbase.c:6445-6484).
        player = _flat(self._vanilla("4_world/entities/manbase/playerbase.c"))
        ground = player[
            player.index("override EntityAI SpawnEntityOnGroundPos(string object_name, vector pos)") : player.index(
                "EntityAI SpawnEntityOnGroundRaycastDispersed("
            )
        ]
        _in_order(
            self,
            ground,
            "int flags = ECE_PLACE_ON_SURFACE;",
            "return EntityAI.Cast(g_Game.CreateObjectEx(object_name, inv_loc.GetPos(), flags));",
            "return null;",
        )
        self.assertIn(
            "vector position = GetPosition() + (GetDirection() * distance); "
            "return SpawnEntityOnGroundPos(object_name, position);",
            player,
        )
        # That placement traces down to the surface (ECE_TRACE is in ECE_PLACE_ON_SURFACE).
        economy = self._vanilla("3_game/ce/centraleconomy.c")
        trace = int(re.search(r"const int ECE_TRACE\s*=\s*(\d+);", economy).group(1))
        place = int(re.search(r"const int ECE_PLACE_ON_SURFACE\s*=\s*(\d+);", economy).group(1))
        self.assertEqual(place & trace, trace)
        # The scan: the sphere query vanilla's vicinity uses, and the hierarchy parent.
        self.assertIn(
            "proto native void GetObjectsAtPosition3D(vector pos, float radius, out array<Object> objects, "
            "out array<CargoBase> proxyCargos);",
            _flat(self._vanilla("3_game/global/game.c")),
        )
        self.assertIn(
            "g_Game.GetObjectsAtPosition3D(playerPosition, VICINITY_DISTANCE, objectsInVicinity, proxyCargos);",
            self._vanilla("5_mission/gui/inventorynew/vicinityitemmanager.c"),
        )
        self.assertIn("proto native EntityAI GetHierarchyParent();", self._vanilla("3_game/entities/entityai.c"))
        self.assertIn("proto native vector GetPosition();", self._vanilla("3_game/entities/object.c"))

    def test_the_connect_respawn_and_logout_events(self) -> None:
        mission = _flat(self._vanilla("5_mission/mission/missionserver.c"))
        self.assertIn("void InvokeOnConnect(PlayerBase player, PlayerIdentity identity)", mission)
        self.assertIn("void InvokeOnDisconnect(PlayerBase player)", mission)
        self.assertIn("void OnClientRespawnEvent(PlayerIdentity identity, PlayerBase player)", mission)
        # A new character (also a respawn's) and a loaded one both reach InvokeOnConnect.
        _in_order(self, mission, "player = OnClientNewEvent(", "InvokeOnConnect(player,identity);")
        _in_order(self, mission, "OnClientReadyEvent(identity, player);", "InvokeOnConnect(player, identity);")
        # Vanilla kills the respawning body when unconscious or restrained, and a
        # leaving body in the same states.
        respawn = mission[mission.index("void OnClientRespawnEvent(") :]
        self.assertIn('player.SetHealth("", "", 0.0);', respawn[: respawn.index("void OnClientReconnectEvent(")])
        _in_order(self, mission, "InvokeOnDisconnect(player);", "HandleBody(player);")


if __name__ == "__main__":
    unittest.main()
