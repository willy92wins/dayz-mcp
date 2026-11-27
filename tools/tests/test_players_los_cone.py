"""Contract, translated-Enforce, and tool tests for players_los_cone.

Covers T1-T19 (T14 is pinned by test_server_perf_nav_visibility.py without
duplication) and kills mutants M1-M11:
- Request validation, daemon ingress, lease, and live-profile refusal (T1-T2)
- Server capability census, bitmask, arg-contract hash (T3-T4; kills M7)
- EvidenceKind contract, published envelope, masking, passthrough, header and
  cross-row invariants (T5-T9; kills M6, M8, M9)
- Translated real Enforce bodies for LosConeYawOffDeg, LosConeInCone,
  LosConeNearest, LosConeGapKind, and DispatchPlayersLosCone (T10-T13; kills
  M1, M2, M3, M4, M5, M10, M11)
- Source-level dispatch, message/prune, tool, size-budget, mirror, and
  compile-hazard checks (T13, T15-T19).
"""
from __future__ import annotations

import copy
import json
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

from dayz_mcp import (
    evidence,
    loopback,
    players_los_cone,
    result_prune,
    server,
)
from dayz_mcp.bridge_readiness import (
    EXPECTED_SERVER_ARG_CONTRACT_HASH,
    SERVER_ARG_CONTRACT,
    SERVER_CAPABILITY_BITS,
    _BRIDGE_COMMAND_TOOLS,
    _compare_bridge_capabilities,
    encode_capability_mask,
    parse_capability_mask,
    server_arg_contract_hash,
)
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    build_app,
)
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceString,
    EnforceVector,
    class_members,
    clean,
    fake_class,
    method_body,
    translate,
)
from tests.fence_helpers import bind_both_peers
from tests.mcp_helpers import _content_json


COMMAND = "players_los_cone"
FIELD = "players_los_cone"
SCRIPTS = addon_root() / "scripts"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
CLIENT_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
PREVIOUS_ACH = "c282097c3d1855ba"
NEW_ACH = "49cc1290c4293fcb"
WIRE_KEYS = {"uids", "radius", "half_yaw_deg"}

PREVIOUS_50_SERVER_BITS: tuple[str, ...] = (
    "action_cursor_ids",
    "bot_start",
    "bot_stop",
    "entities_query",
    "exec_enforce",
    "hands_attach",
    "hands_take",
    "infected_drive",
    "inventory_attach",
    "inventory_give",
    "inventory_move",
    "lease_close",
    "loadout_apply",
    "loadout_clear",
    "loadout_get",
    "nav_path_query",
    "notify_players",
    "object_anim",
    "object_delete",
    "object_doors",
    "object_inspect",
    "object_resolve",
    "object_set_health",
    "player_godmode",
    "player_heal",
    "player_kill",
    "player_teleport",
    "players_visibility",
    "query_all_players",
    "query_get_in_condition",
    "query_player_state",
    "scene_clear",
    "scene_entities",
    "scene_raycast",
    "server_perf",
    "surface_query",
    "survivor_drive",
    "survivor_drive_release",
    "telemetry_read",
    "vehicle_door",
    "vehicle_enter",
    "vehicle_prepare_fixture",
    "weapon_state",
    "world_spawn",
    "world_spawn_batch",
    "world_time_get",
    "world_time_set",
    "world_weather_set",
    "player_set_stance",
    "players_kinematics",
)

EXPECTED_ROW_MEMBERS: list[tuple[str, str]] = [
    ("string", "uid"),
    ("int", "tick_ms"),
    ("string", "gap_kind"),
    ("string", "target_uid"),
    ("ref array<float>", "observer_head"),
    ("ref array<float>", "target_head"),
    ("bool", "observer_head_bone"),
    ("bool", "target_head_bone"),
    ("float", "distance_m"),
    ("float", "yaw_deg"),
    ("float", "yaw_off_deg"),
    ("float", "pitch_deg"),
    ("bool", "pitch_deg_available"),
    ("bool", "ray_hit"),
    ("ref array<float>", "hit_pos"),
    ("bool", "hit_pos_available"),
    ("float", "hit_distance_m"),
    ("bool", "hit_distance_m_available"),
    ("float", "clearance_m"),
    ("bool", "clearance_m_available"),
    ("string", "hit_type"),
    ("bool", "hit_type_available"),
    ("string", "hit_class"),
    ("bool", "hit_class_available"),
]

EXPECTED_OBSERVER_MEMBERS: list[tuple[str, str]] = [
    ("string", "uid"),
    ("bool", "found"),
    ("bool", "yaw_deg_available"),
    ("float", "yaw_deg"),
    ("int", "in_radius"),
    ("int", "in_cone"),
    ("int", "rays"),
    ("int", "rows"),
]

EXPECTED_READ_MEMBERS: list[tuple[str, str]] = [
    ("string", "schema"),
    ("int", "tick_ms"),
    ("float", "radius_m"),
    ("float", "half_yaw_deg"),
    ("bool", "pitch_filter_applied"),
    ("int", "observers_max"),
    ("int", "rays_per_observer_max"),
    ("int", "rows_per_observer_max"),
    ("bool", "truncated"),
    ("ref array<ref MCPPlayersLosConeObserver>", "observers"),
    ("ref array<ref MCPPlayersLosConeRow>", "rows"),
]

SIG_YAW_OFF = "protected float LosConeYawOffDeg(float bearingDeg, float yawDeg)"
SIG_IN_CONE = (
    "protected bool LosConeInCone(float distanceM, float radiusM, "
    "float yawOffDeg, float halfYawDeg)"
)
SIG_NEAREST = "protected int LosConeNearest(array<float> distances, array<bool> taken)"
SIG_GAP_KIND = "protected string LosConeGapKind(int position, int raysMax)"
SIG_CAST_RAY = (
    "protected void LosConeCastRay(vector fromPos, vector toPos, "
    "PlayerBase observer, MCPRaycastHit hit)"
)
SIG_DISPATCH = (
    "protected bool DispatchPlayersLosCone(MCPCommand command, MCPResult result)"
)
SIG_DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"

NEW_BRIDGE_METHODS = (
    SIG_YAW_OFF,
    SIG_IN_CONE,
    SIG_NEAREST,
    SIG_GAP_KIND,
    SIG_CAST_RAY,
    SIG_DISPATCH,
)

_LOCAL_TYPES = (
    "string",
    "int",
    "float",
    "bool",
    "vector",
    "PlayerBase",
    "PlayerIdentity",
    "HumanInputController",
    "HumanCommandWeapons",
    "MCPArgs",
    "MCPPlayersLosConeRead",
    "MCPPlayersLosConeObserver",
    "MCPPlayersLosConeRow",
    "MCPRaycastValidation",
    "MCPRaycastHit",
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:ref\s+)?(?:array\s*<[^;>]+>|"
    + "|".join(_LOCAL_TYPES)
    + r")\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)


class _LosConeString(EnforceString):
    """EnforceString plus Trim() for DispatchPlayersLosCone translation."""

    __slots__ = ()

    def Trim(self) -> "_LosConeString":
        return _LosConeString(self.data.strip())


class _LosConeArray(EnforceArray):
    """EnforceArray plus Set() and vector-copy Insert() for translated Enforce."""

    def Set(self, index: int, value: object) -> None:
        if type(index) is not int or not 0 <= index < len(self):
            raise AssertionError(f"Set({index!r}) outside an array of {len(self)}")
        self[index] = value

    def Insert(self, value: object) -> int:
        if isinstance(value, EnforceVector):
            value = EnforceVector(value)
        return super().Insert(value)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _in_order(test: unittest.TestCase, body: str, *tokens: str) -> None:
    positions = []
    for token in tokens:
        test.assertIn(token, body)
        positions.append(body.index(token))
    test.assertEqual(positions, sorted(positions), tokens)


def _locals_declared_at_top(body: str) -> list[str]:
    names: list[str] = []
    statement_seen = False
    for raw in clean(body).splitlines():
        line = raw.strip()
        if not line or line in ("{", "}"):
            continue
        match = _LOCAL_DECLARATION.match(raw)
        if match is None:
            statement_seen = True
            continue
        if statement_seen:
            raise AssertionError(f"declaration after a statement: {line}")
        if re.match(r"^\s*ref\s+", raw):
            raise AssertionError(f"ref on a local variable: {line}")
        if match.group(1) in names:
            raise AssertionError(f"local declared twice: {match.group(1)}")
        names.append(match.group(1))
    return names


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


def _wire_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "uids": ["76561198000000001"],
        "radius": 50.0,
        "half_yaw_deg": 10.0,
    }
    args.update(overrides)
    return args


def _wire_observer(**overrides: object) -> dict[str, object]:
    obs: dict[str, object] = {
        "uid": "76561198000000001",
        "found": 1,
        "yaw_deg_available": 1,
        "yaw_deg": 45.0,
        "in_radius": 1,
        "in_cone": 1,
        "rays": 1,
        "rows": 1,
    }
    obs.update(overrides)
    return obs


def _wire_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "uid": "76561198000000001",
        "tick_ms": 12000,
        "gap_kind": "",
        "target_uid": "76561198000000002",
        "observer_head": [100.0, 6.7, 200.0],
        "target_head": [114.142, 6.7, 214.142],
        "observer_head_bone": 1,
        "target_head_bone": 1,
        "distance_m": 20.0,
        "yaw_deg": 45.0,
        "yaw_off_deg": 0.0,
        "pitch_deg": 12.5,
        "pitch_deg_available": 1,
        "ray_hit": 1,
        "hit_pos": [107.071, 6.7, 207.071],
        "hit_pos_available": 1,
        "hit_distance_m": 10.0,
        "hit_distance_m_available": 1,
        "clearance_m": 10.0,
        "clearance_m_available": 1,
        "hit_type": "Land_Wall_Concrete",
        "hit_type_available": 1,
        "hit_class": "HouseNoDestruct",
        "hit_class_available": 1,
    }
    row.update(overrides)
    return row


def _wire_read(**overrides: object) -> dict[str, object]:
    header: dict[str, object] = {
        "schema": players_los_cone.SCHEMA,
        "tick_ms": 12000,
        "radius_m": 50.0,
        "half_yaw_deg": 10.0,
        "pitch_filter_applied": 0,
        "observers_max": players_los_cone.OBSERVERS_MAX,
        "rays_per_observer_max": players_los_cone.RAYS_PER_OBSERVER_MAX,
        "rows_per_observer_max": players_los_cone.ROWS_PER_OBSERVER_MAX,
        "truncated": 0,
        "observers": [_wire_observer()],
        "rows": [_wire_row()],
    }
    header.update(overrides)
    return header


def _server_const_census(source: str) -> list[str]:
    match = re.search(r"\bSERVER_CAPABILITIES\s*=\s*([^;]+);", source)
    if match is None:
        raise AssertionError("SERVER_CAPABILITIES not found")
    return "".join(re.findall(r'"([^"]*)"', match.group(1))).split(",")


def _server_dispatch_branches(source: str) -> list[str]:
    dispatch = method_body(source, SIG_DISPATCH_CHAIN)
    return re.findall(r'command\.cmd\s*==\s*"([a-z0-9_]+)"', dispatch)


def _to_wire_value(value: object) -> object:
    if isinstance(value, EnforceString):
        return value.text
    if isinstance(value, EnforceArray):
        return [_to_wire_value(item) for item in value]
    if hasattr(value, "__slots__"):
        return {
            slot: _to_wire_value(getattr(value, slot))
            for slot in value.__slots__  # type: ignore[attr-defined]
        }
    return value


class RequestAndIngressTest(unittest.TestCase):
    """T1 (request normalization) and T2 (ingress, lease, live profile)."""

    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)

    def test_t1_defaults_bounds_and_bad_request_values(self) -> None:
        default_req = players_los_cone.normalize_request(["76561198000000001"])
        self.assertEqual(
            default_req,
            {
                "uids": ["76561198000000001"],
                "radius": 50.0,
                "half_yaw_deg": 10.0,
            },
        )
        self.assertEqual(set(default_req), WIRE_KEYS)
        self.assertEqual(
            loopback.validate_command_args(COMMAND, default_req), (True, None)
        )

        uid64 = "a" * players_los_cone.UID_MAX_CHARS
        four_uids = [uid64, "u2", "u3", "u4"]
        max_req = players_los_cone.normalize_request(four_uids, 50.0, 30.0)
        self.assertEqual(
            max_req,
            {"uids": four_uids, "radius": 50.0, "half_yaw_deg": 30.0},
        )
        self.assertEqual(loopback.validate_command_args(COMMAND, max_req), (True, None))

        uid65 = "a" * (players_los_cone.UID_MAX_CHARS + 1)
        cases = (
            (("u1", 50.0, 10.0), "bad_uids"),
            ((("u1",), 50.0, 10.0), "bad_uids"),
            ((123, 50.0, 10.0), "bad_uids"),
            ((None, 50.0, 10.0), "bad_uids"),
            (([], 50.0, 10.0), "bad_uids"),
            ((["u1", "u2", "u3", "u4", "u5"], 50.0, 10.0), "bad_uids"),
            (([""], 50.0, 10.0), "bad_uids"),
            ((["   "], 50.0, 10.0), "bad_uids"),
            ((["u1", "u1"], 50.0, 10.0), "bad_uids"),
            (([123], 50.0, 10.0), "bad_uids"),
            (([uid65], 50.0, 10.0), "bad_uids"),
            ((["u1"], 0, 10.0), "bad_radius_m"),
            ((["u1"], 0.0, 10.0), "bad_radius_m"),
            ((["u1"], -1.0, 10.0), "bad_radius_m"),
            ((["u1"], 50.01, 10.0), "bad_radius_m"),
            ((["u1"], math.nan, 10.0), "bad_radius_m"),
            ((["u1"], math.inf, 10.0), "bad_radius_m"),
            ((["u1"], -math.inf, 10.0), "bad_radius_m"),
            ((["u1"], True, 10.0), "bad_radius_m"),
            ((["u1"], False, 10.0), "bad_radius_m"),
            ((["u1"], 50.0, 0), "bad_half_yaw_deg"),
            ((["u1"], 50.0, 0.0), "bad_half_yaw_deg"),
            ((["u1"], 50.0, -0.5), "bad_half_yaw_deg"),
            ((["u1"], 50.0, 30.01), "bad_half_yaw_deg"),
            ((["u1"], 50.0, math.nan), "bad_half_yaw_deg"),
            ((["u1"], 50.0, math.inf), "bad_half_yaw_deg"),
            ((["u1"], 50.0, True), "bad_half_yaw_deg"),
            ((["u1"], 50.0, False), "bad_half_yaw_deg"),
        )
        for arguments, expected_code in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError) as raised:
                    players_los_cone.normalize_request(*arguments)  # type: ignore[arg-type]
                self.assertEqual(str(raised.exception), expected_code)

    def test_t2_ingress_lease_admission_and_live_profile(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))
        self.assertIn(COMMAND, loopback._CAPABILITY_ADMISSION_COMMANDS)
        self.assertNotIn(COMMAND, loopback.LIVE_ALLOWED_COMMANDS)
        self.assertNotIn(COMMAND, loopback.LIVE_ADMIN_COMMANDS)

        # Missing or extra key is bad_args at ingress.
        for key in WIRE_KEYS:
            missing = {k: v for k, v in _wire_args().items() if k != key}
            with self.subTest(missing=key):
                self.assertEqual(
                    loopback.validate_command_args(COMMAND, missing),
                    (False, "bad_args"),
                )
        extra = _wire_args(extra=1)
        self.assertEqual(
            loopback.validate_command_args(COMMAND, extra),
            (False, "bad_args"),
        )
        self.assertEqual(
            self.state.enqueue_command(COMMAND, extra, peer="server"),
            (400, {"error": "bad_args"}),
        )

        # Schema rejects empty uids, 5 uids, duplicate uid, and 65-char uid (64 passes).
        self.assertEqual(
            loopback.validate_command_args(COMMAND, _wire_args(uids=["u" * 64])),
            (True, None),
        )
        for bad_uids in (
            [],
            ["u1", "u2", "u3", "u4", "u5"],
            ["u1", "u1"],
            ["u" * 65],
        ):
            with self.subTest(bad_uids=bad_uids):
                self.assertEqual(
                    loopback.validate_command_args(COMMAND, _wire_args(uids=bad_uids)),
                    (False, "bad_args"),
                )

        self.assertEqual(
            self.state.enqueue_command(COMMAND, _wire_args(), peer="client"),
            (400, {"error": "bad_peer"}),
        )

        # Live profile refuses with and without admin (D11).
        for admin in (False, True):
            with self.subTest(admin=admin):
                self.assertEqual(
                    loopback.live_profile_refusal(
                        COMMAND, enabled=True, admin_enabled=admin
                    ),
                    loopback.LIVE_PROFILE_REFUSED,
                )
                live_state = loopback.ServerState(
                    "test-key", live_profile=True, live_admin_enabled=admin
                )
                bind_both_peers(live_state)
                self.assertEqual(
                    live_state.enqueue_command(COMMAND, _wire_args(), peer="server"),
                    (403, {"error": "live_profile_refused"}),
                )


class CensusAndContractHashTest(unittest.TestCase):
    """T3 (server capability census & mask) and T4 (SERVER_ARG_CONTRACT & ach)."""

    def test_t3_server_census_mask_and_client_absence(self) -> None:
        server_src = _source(SERVER_BRIDGE_PATH)
        census = _server_const_census(server_src)
        self.assertIn(COMMAND, census)
        self.assertEqual(census, sorted(census))
        self.assertLess(census.index("players_kinematics"), census.index(COMMAND))
        self.assertLess(census.index(COMMAND), census.index("players_visibility"))

        branches = _server_dispatch_branches(server_src)
        self.assertIn(COMMAND, branches)
        dispatch = method_body(server_src, SIG_DISPATCH_CHAIN)
        _in_order(
            self,
            dispatch,
            'command.cmd == "players_los_cone"',
            "postNow = DispatchPlayersLosCone(command, result);",
            'result.error = "unknown_command";',
        )

        # Bit table append-only: prior 50 bits unchanged, bit 50 is players_los_cone.
        self.assertEqual(SERVER_CAPABILITY_BITS[:50], PREVIOUS_50_SERVER_BITS)
        self.assertEqual(SERVER_CAPABILITY_BITS[50:], (COMMAND, "loot_los_cone"))

        mask_matches = re.findall(r'SERVER_CAPABILITIES_MASK\s*=\s*"([^"]+)"', server_src)
        self.assertEqual(mask_matches, ["1.fffffffffffff"])
        self.assertEqual(encode_capability_mask("server", census), "1.fffffffffffff")
        decoded, reason = parse_capability_mask("server", mask_matches[0])
        self.assertEqual(reason, "ok")
        self.assertEqual(set(decoded or ()), set(census))
        self.assertEqual(set(decoded or ()), set(branches))

        # Client bridge neither implements nor announces players_los_cone.
        client_src = _source(CLIENT_BRIDGE_PATH)
        self.assertNotIn(COMMAND, client_src)
        self.assertNotIn("MCPPlayersLosCone", client_src)
        self.assertNotIn(COMMAND, announced_caps(client_src))
        self.assertNotIn(COMMAND, dispatch_census(client_src))
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["client"])
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["peers"]["server"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, fixture["peers"]["client"])

    def test_t4_arg_contract_hash_and_stale_peer_mismatch(self) -> None:
        self.assertEqual(
            SERVER_ARG_CONTRACT[COMMAND],
            ("half_yaw_deg", "radius", "uids"),
        )
        self.assertEqual(server_arg_contract_hash(), NEW_ACH)
        self.assertEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, NEW_ACH)
        # Kills M7: MCPBridge.c must declare the updated ach hash, not #431's.
        server_src = _source(SERVER_BRIDGE_PATH)
        self.assertIn(f'SERVER_ARG_CONTRACT_HASH = "{NEW_ACH}"', server_src)
        self.assertNotIn(f'SERVER_ARG_CONTRACT_HASH = "{PREVIOUS_ACH}"', server_src)

        census = sorted(_BRIDGE_COMMAND_TOOLS["server"])
        registered = frozenset(
            tool for tool in _BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        stale = _compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
                "announced_arg_contract_hash": PREVIOUS_ACH,
            },
            registered,
        )
        self.assertEqual(stale["state"], "mismatch")
        self.assertEqual(stale["reason"], "arg_contract_mismatch")
        self.assertEqual(stale["expected_arg_contract_hash"], NEW_ACH)
        self.assertEqual(stale["announced_arg_contract_hash"], PREVIOUS_ACH)


class NormalizationAndEvidenceTest(unittest.TestCase):
    """T5-T9: KIND, published envelope, masking, passthrough, header & invariants."""

    def test_t5_evidence_kind_contract(self) -> None:
        kind = players_los_cone.KIND
        self.assertEqual(kind.name, "players_los_cone")
        self.assertEqual(kind.gap_kinds, players_los_cone.GAP_KINDS)
        expected_fields = [
            ("target_uid", evidence.EvidenceField("uid")),
            ("observer_head", evidence.EvidenceField("vec3", "m")),
            ("target_head", evidence.EvidenceField("vec3", "m")),
            ("observer_head_bone", evidence.EvidenceField("flag")),
            ("target_head_bone", evidence.EvidenceField("flag")),
            ("distance_m", evidence.EvidenceField("number", "m")),
            ("yaw_deg", evidence.EvidenceField("number", "deg")),
            ("yaw_off_deg", evidence.EvidenceField("number", "deg")),
            ("pitch_deg", evidence.EvidenceField("number", "deg", optional=True)),
            # Kills M8: ray_hit must declare absent_with ray_budget_exceeded.
            (
                "ray_hit",
                evidence.EvidenceField(
                    "flag", absent_with=frozenset({"ray_budget_exceeded"})
                ),
            ),
            ("hit_pos", evidence.EvidenceField("vec3", "m", optional=True)),
            ("hit_distance_m", evidence.EvidenceField("number", "m", optional=True)),
            ("clearance_m", evidence.EvidenceField("number", "m", optional=True)),
            ("hit_type", evidence.EvidenceField("token", optional=True)),
            ("hit_class", evidence.EvidenceField("token", optional=True)),
        ]
        self.assertEqual(list(kind.fields.items()), expected_fields)

    def test_t6_published_read_envelope_and_header(self) -> None:
        row_hit = _wire_row(
            uid="obs-a",
            tick_ms=15000,
            gap_kind="",
            target_uid="tgt-1",
            observer_head=[10.0, 1.7, 20.0],
            target_head=[10.0, 1.7, 40.0],
            observer_head_bone=1,
            target_head_bone=1,
            distance_m=20.0,
            yaw_deg=0.0,
            yaw_off_deg=0.0,
            pitch_deg=7.5,
            pitch_deg_available=1,
            ray_hit=1,
            hit_pos=[10.0, 1.7, 30.0],
            hit_pos_available=1,
            hit_distance_m=10.0,
            hit_distance_m_available=1,
            clearance_m=10.0,
            clearance_m_available=1,
            hit_type="Land_Container",
            hit_type_available=1,
            hit_class="HouseNoDestruct",
            hit_class_available=1,
        )
        row_miss = _wire_row(
            uid="obs-a",
            tick_ms=15000,
            gap_kind="",
            target_uid="tgt-2",
            observer_head=[10.0, 1.7, 20.0],
            target_head=[11.0, 1.6, 45.0],
            observer_head_bone=1,
            target_head_bone=0,
            distance_m=25.02,
            yaw_deg=0.0,
            yaw_off_deg=2.29,
            pitch_deg=7.5,
            pitch_deg_available=1,
            ray_hit=0,
            hit_pos=[],
            hit_pos_available=0,
            hit_distance_m=0.0,
            hit_distance_m_available=0,
            clearance_m=0.0,
            clearance_m_available=0,
            hit_type="",
            hit_type_available=0,
            hit_class="",
            hit_class_available=0,
        )
        row_gap = _wire_row(
            uid="obs-a",
            tick_ms=15000,
            gap_kind="ray_budget_exceeded",
            target_uid="tgt-3",
            observer_head=[10.0, 1.7, 20.0],
            target_head=[12.0, 1.7, 50.0],
            observer_head_bone=1,
            target_head_bone=1,
            distance_m=30.07,
            yaw_deg=0.0,
            yaw_off_deg=3.81,
            pitch_deg=7.5,
            pitch_deg_available=1,
            ray_hit=0,
            hit_pos=[],
            hit_pos_available=0,
            hit_distance_m=0.0,
            hit_distance_m_available=0,
            clearance_m=0.0,
            clearance_m_available=0,
            hit_type="",
            hit_type_available=0,
            hit_class="",
            hit_class_available=0,
        )
        wire = {
            "ok": 1,
            FIELD: _wire_read(
                tick_ms=15000,
                truncated=0,
                observers=[
                    _wire_observer(
                        uid="obs-a",
                        found=1,
                        yaw_deg_available=1,
                        yaw_deg=0.0,
                        in_radius=5,
                        in_cone=3,
                        rays=2,
                        rows=3,
                    ),
                    _wire_observer(
                        uid="obs-missing",
                        found=0,
                        yaw_deg_available=0,
                        yaw_deg=0.0,
                        in_radius=0,
                        in_cone=0,
                        rays=0,
                        rows=0,
                    ),
                ],
                rows=[row_hit, row_miss, row_gap],
            ),
        }
        published = players_los_cone.normalize_bridge_result(
            wire, include_unverified=False
        )
        expected_header = {
            "schema": players_los_cone.SCHEMA,
            "tick_ms": 15000,
            "radius_m": 50.0,
            "half_yaw_deg": 10.0,
            "pitch_filter_applied": False,
            "observers_max": 4,
            "rays_per_observer_max": 8,
            "rows_per_observer_max": 32,
            "truncated": False,
            "observers": [
                {
                    "uid": "obs-a",
                    "found": True,
                    "yaw_deg_available": True,
                    "yaw_deg": 0.0,
                    "in_radius": 5,
                    "in_cone": 3,
                    "rays": 2,
                    "rows": 3,
                },
                {
                    "uid": "obs-missing",
                    "found": False,
                    "yaw_deg_available": False,
                    "in_radius": 0,
                    "in_cone": 0,
                    "rays": 0,
                    "rows": 0,
                },
            ],
        }
        expected_evidence = {
            "schema": evidence.EVIDENCE_SCHEMA,
            "kind": "players_los_cone",
            "units": {
                "tick_ms": "ms",
                "observer_head": "m",
                "target_head": "m",
                "distance_m": "m",
                "yaw_deg": "deg",
                "yaw_off_deg": "deg",
                "pitch_deg": "deg",
                "hit_pos": "m",
                "hit_distance_m": "m",
                "clearance_m": "m",
            },
            "rows": [
                {
                    "uid": "obs-a",
                    "tick_ms": 15000,
                    "gap_kind": "",
                    "target_uid": "tgt-1",
                    "observer_head": [10.0, 1.7, 20.0],
                    "target_head": [10.0, 1.7, 40.0],
                    "observer_head_bone": True,
                    "target_head_bone": True,
                    "distance_m": 20.0,
                    "yaw_deg": 0.0,
                    "yaw_off_deg": 0.0,
                    "pitch_deg_available": False,
                    "ray_hit": True,
                    "hit_pos": [10.0, 1.7, 30.0],
                    "hit_pos_available": True,
                    "hit_distance_m": 10.0,
                    "hit_distance_m_available": True,
                    "clearance_m": 10.0,
                    "clearance_m_available": True,
                    "hit_type": "Land_Container",
                    "hit_type_available": True,
                    "hit_class": "HouseNoDestruct",
                    "hit_class_available": True,
                },
                {
                    "uid": "obs-a",
                    "tick_ms": 15000,
                    "gap_kind": "",
                    "target_uid": "tgt-2",
                    "observer_head": [10.0, 1.7, 20.0],
                    "target_head": [11.0, 1.6, 45.0],
                    "observer_head_bone": True,
                    "target_head_bone": False,
                    "distance_m": 25.02,
                    "yaw_deg": 0.0,
                    "yaw_off_deg": 2.29,
                    "pitch_deg_available": False,
                    "ray_hit": False,
                    "hit_pos_available": False,
                    "hit_distance_m_available": False,
                    "clearance_m_available": False,
                    "hit_type_available": False,
                    "hit_class_available": False,
                },
                {
                    "uid": "obs-a",
                    "tick_ms": 15000,
                    "gap_kind": "ray_budget_exceeded",
                    "target_uid": "tgt-3",
                    "observer_head": [10.0, 1.7, 20.0],
                    "target_head": [12.0, 1.7, 50.0],
                    "observer_head_bone": True,
                    "target_head_bone": True,
                    "distance_m": 30.07,
                    "yaw_deg": 0.0,
                    "yaw_off_deg": 3.81,
                    "pitch_deg_available": False,
                    "hit_pos_available": False,
                    "hit_distance_m_available": False,
                    "clearance_m_available": False,
                    "hit_type_available": False,
                    "hit_class_available": False,
                },
            ],
            "truncated": False,
        }
        self.assertEqual(published["players_los_cone"], expected_header)
        self.assertEqual(published["evidence"], expected_evidence)

    def test_t6_truncated_propagates_to_evidence_envelope(self) -> None:
        # Kills M9: evidence["truncated"] must reflect header["truncated"] == True.
        wire = {
            "ok": 1,
            FIELD: _wire_read(
                truncated=1,
                observers=[
                    _wire_observer(in_radius=5, in_cone=2, rays=1, rows=1),
                ],
                rows=[_wire_row()],
            ),
        }
        published = players_los_cone.normalize_bridge_result(wire)
        self.assertIs(published["players_los_cone"]["truncated"], True)
        self.assertIs(published["evidence"]["truncated"], True)

    def test_t7_unverified_field_masking_and_input_immutability(self) -> None:
        wire = {"ok": 1, FIELD: _wire_read(rows=[_wire_row(pitch_deg=14.25, pitch_deg_available=1)])}
        snapshot = copy.deepcopy(wire)

        # Kills M6: default masks every field in UNVERIFIED_FIELDS.
        masked = players_los_cone.normalize_bridge_result(wire)
        row_masked = masked["evidence"]["rows"][0]
        for field_name in players_los_cone.UNVERIFIED_FIELDS:
            self.assertIs(row_masked[f"{field_name}_available"], False)
            self.assertNotIn(field_name, row_masked)
        self.assertEqual(wire, snapshot)

        unmasked = players_los_cone.normalize_bridge_result(wire, include_unverified=True)
        row_unmasked = unmasked["evidence"]["rows"][0]
        self.assertIs(row_unmasked["pitch_deg_available"], True)
        self.assertEqual(row_unmasked["pitch_deg"], 14.25)
        self.assertEqual(wire, snapshot)

    def test_t8_no_filtering_plausibility_or_verdict_keys(self) -> None:
        rows = [
            _wire_row(
                target_uid="edge-pos",
                observer_head=[-99999.5, -500.0, 123456.75],
                target_head=[-99949.5001, -500.0, 123456.75],
                distance_m=49.9999,
                yaw_off_deg=10.0,
                clearance_m=0.0,
            ),
            _wire_row(
                target_uid="edge-neg",
                observer_head=[0.0, 0.0, 0.0],
                target_head=[0.0, 0.0, 50.0],
                distance_m=50.0,
                yaw_off_deg=-10.0,
                clearance_m=0.0,
            ),
        ]
        wire = {
            "ok": 1,
            FIELD: _wire_read(
                observers=[_wire_observer(in_radius=2, in_cone=2, rays=2, rows=2)],
                rows=rows,
            ),
        }
        published = players_los_cone.normalize_bridge_result(wire)
        out_rows = published["evidence"]["rows"]
        self.assertEqual([r["target_uid"] for r in out_rows], ["edge-pos", "edge-neg"])
        self.assertEqual(out_rows[0]["distance_m"], 49.9999)
        self.assertEqual(out_rows[0]["yaw_off_deg"], 10.0)
        self.assertEqual(out_rows[0]["observer_head"], [-99999.5, -500.0, 123456.75])
        self.assertEqual(out_rows[0]["clearance_m"], 0.0)
        self.assertEqual(out_rows[1]["distance_m"], 50.0)
        self.assertEqual(out_rows[1]["yaw_off_deg"], -10.0)

        allowed_keys = {"uid", "tick_ms", "gap_kind"}
        for field_name, spec in players_los_cone.KIND.fields.items():
            allowed_keys.add(field_name)
            if spec.optional:
                allowed_keys.add(f"{field_name}_available")
        forbidden = {"visible", "line_of_sight", "in_fov", "occluded"}
        for row in out_rows:
            self.assertTrue(set(row).issubset(allowed_keys), set(row) - allowed_keys)
            self.assertFalse(set(row) & forbidden)

    def test_t9_header_booleans_and_invariants(self) -> None:
        # Booleans accept 0/1 and True/False.
        for raw_false in (0, False):
            wire = {
                "ok": 1,
                FIELD: _wire_read(
                    pitch_filter_applied=raw_false,
                    truncated=raw_false,
                    observers=[_wire_observer(found=1, yaw_deg_available=True)],
                ),
            }
            out = players_los_cone.normalize_bridge_result(wire)
            self.assertIs(out["players_los_cone"]["pitch_filter_applied"], False)
            self.assertIs(out["players_los_cone"]["truncated"], False)

        # Found observer without yaw_deg_available: in_radius allowed, yaw_deg omitted.
        no_yaw = players_los_cone.normalize_bridge_result(
            {
                "ok": 1,
                FIELD: _wire_read(
                    observers=[
                        _wire_observer(
                            found=1,
                            yaw_deg_available=0,
                            yaw_deg=0.0,
                            in_radius=3,
                            in_cone=0,
                            rays=0,
                            rows=0,
                        )
                    ],
                    rows=[],
                ),
            }
        )
        self.assertNotIn("yaw_deg", no_yaw["players_los_cone"]["observers"][0])
        self.assertEqual(no_yaw["players_los_cone"]["observers"][0]["in_radius"], 3)

        # Bad booleans -> bad_bridge_los_cone_boolean: <where>.
        with self.assertRaises(ValueError) as raised:
            players_los_cone.normalize_bridge_result(
                {"ok": 1, FIELD: _wire_read()}, include_unverified=1  # type: ignore[arg-type]
            )
        self.assertEqual(
            str(raised.exception), "bad_bridge_los_cone_boolean: include_unverified"
        )

        def _without(mapping: dict[str, object], key: str) -> dict[str, object]:
            return {k: v for k, v in mapping.items() if k != key}

        bool_cases = (
            (_wire_read(pitch_filter_applied=2), "players_los_cone.pitch_filter_applied"),
            (_wire_read(pitch_filter_applied="0"), "players_los_cone.pitch_filter_applied"),
            (_wire_read(pitch_filter_applied=0.0), "players_los_cone.pitch_filter_applied"),
            (
                _without(_wire_read(), "pitch_filter_applied"),
                "players_los_cone.pitch_filter_applied",
            ),
            (_wire_read(truncated="0"), "players_los_cone.truncated"),
            (_wire_read(truncated=0.0), "players_los_cone.truncated"),
            (
                _without(_wire_read(), "truncated"),
                "players_los_cone.truncated",
            ),
            (
                _wire_read(observers=[_wire_observer(found=2)]),
                "players_los_cone.observers[0].found",
            ),
            (
                _wire_read(observers=[_wire_observer(found="1")]),
                "players_los_cone.observers[0].found",
            ),
            (
                _wire_read(observers=[_wire_observer(found=1.0)]),
                "players_los_cone.observers[0].found",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "found")]),
                "players_los_cone.observers[0].found",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg_available=-1)]),
                "players_los_cone.observers[0].yaw_deg_available",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg_available="1")]),
                "players_los_cone.observers[0].yaw_deg_available",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg_available=1.0)]),
                "players_los_cone.observers[0].yaw_deg_available",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "yaw_deg_available")]),
                "players_los_cone.observers[0].yaw_deg_available",
            ),
        )
        for idx, (bad_read, where) in enumerate(bool_cases):
            with self.subTest(idx=idx, bool_where=where):
                with self.assertRaises(ValueError) as raised:
                    players_los_cone.normalize_bridge_result({"ok": 1, FIELD: bad_read})
                self.assertEqual(
                    str(raised.exception), f"bad_bridge_los_cone_boolean: {where}"
                )

        # Top-level and header member / invariant rejections.
        with self.assertRaises(ValueError) as raised:
            players_los_cone.normalize_bridge_result("not-a-dict")
        self.assertEqual(str(raised.exception), "bad_bridge_los_cone_result: result")

        with self.assertRaises(ValueError) as raised:
            players_los_cone.normalize_bridge_result({"ok": 1, FIELD: None})
        self.assertEqual(
            str(raised.exception), "bad_bridge_los_cone_result: players_los_cone"
        )

        result_cases: tuple[tuple[dict[str, object], str], ...] = (
            (_without(_wire_read(), "rows"), "players_los_cone.rows"),
            (_wire_read(rows="bad"), "players_los_cone.rows"),
            (_without(_wire_read(), "schema"), "players_los_cone.schema"),
            (_wire_read(schema=123), "players_los_cone.schema"),
            (_wire_read(schema="wrong"), "players_los_cone.schema"),
            (_without(_wire_read(), "tick_ms"), "players_los_cone.tick_ms"),
            (_wire_read(tick_ms=-1), "players_los_cone.tick_ms"),
            (_wire_read(tick_ms=True), "players_los_cone.tick_ms"),
            (_wire_read(tick_ms=12000.0), "players_los_cone.tick_ms"),
            (_wire_read(tick_ms="12000"), "players_los_cone.tick_ms"),
            (_without(_wire_read(), "radius_m"), "players_los_cone.radius_m"),
            (_wire_read(radius_m="50.0"), "players_los_cone.radius_m"),
            (_wire_read(radius_m=True), "players_los_cone.radius_m"),
            (_wire_read(radius_m=0.0), "players_los_cone.radius_m"),
            (_wire_read(radius_m=50.5), "players_los_cone.radius_m"),
            (_wire_read(radius_m=math.nan), "players_los_cone.radius_m"),
            (_without(_wire_read(), "half_yaw_deg"), "players_los_cone.half_yaw_deg"),
            (_wire_read(half_yaw_deg="10.0"), "players_los_cone.half_yaw_deg"),
            (_wire_read(half_yaw_deg=True), "players_los_cone.half_yaw_deg"),
            (_wire_read(half_yaw_deg=0.0), "players_los_cone.half_yaw_deg"),
            (_wire_read(half_yaw_deg=31.0), "players_los_cone.half_yaw_deg"),
            (_wire_read(pitch_filter_applied=1), "players_los_cone.pitch_filter_applied"),
            (_without(_wire_read(), "observers_max"), "players_los_cone.observers_max"),
            (_wire_read(observers_max=5), "players_los_cone.observers_max"),
            (_wire_read(observers_max=4.0), "players_los_cone.observers_max"),
            (_wire_read(observers_max="4"), "players_los_cone.observers_max"),
            (_wire_read(observers_max=True), "players_los_cone.observers_max"),
            (
                _without(_wire_read(), "rays_per_observer_max"),
                "players_los_cone.rays_per_observer_max",
            ),
            (_wire_read(rays_per_observer_max=9), "players_los_cone.rays_per_observer_max"),
            (_wire_read(rays_per_observer_max=8.0), "players_los_cone.rays_per_observer_max"),
            (_wire_read(rays_per_observer_max="8"), "players_los_cone.rays_per_observer_max"),
            (_wire_read(rays_per_observer_max=True), "players_los_cone.rays_per_observer_max"),
            (
                _without(_wire_read(), "rows_per_observer_max"),
                "players_los_cone.rows_per_observer_max",
            ),
            (_wire_read(rows_per_observer_max=33), "players_los_cone.rows_per_observer_max"),
            (_wire_read(rows_per_observer_max=32.0), "players_los_cone.rows_per_observer_max"),
            (_wire_read(rows_per_observer_max="32"), "players_los_cone.rows_per_observer_max"),
            (_wire_read(rows_per_observer_max=True), "players_los_cone.rows_per_observer_max"),
            (_without(_wire_read(), "observers"), "players_los_cone.observers"),
            (_wire_read(observers="bad", rows=[]), "players_los_cone.observers"),
            (_wire_read(observers=[], rows=[]), "players_los_cone.observers"),
            (
                _wire_read(
                    observers=[_wire_observer(uid=f"u{i}", in_radius=0, in_cone=0, rays=0, rows=0) for i in range(5)],
                    rows=[],
                ),
                "players_los_cone.observers",
            ),
            (
                _wire_read(observers=["not-a-dict"], rows=[]),
                "players_los_cone.observers[0]",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "uid")]),
                "players_los_cone.observers[0].uid",
            ),
            (
                _wire_read(observers=[_wire_observer(uid=123)]),
                "players_los_cone.observers[0].uid",
            ),
            (
                _wire_read(observers=[_wire_observer(uid="  ")], rows=[]),
                "players_los_cone.observers[0].uid",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(uid="u1", in_radius=0, in_cone=0, rays=0, rows=0),
                        _wire_observer(uid="u1", in_radius=0, in_cone=0, rays=0, rows=0),
                    ],
                    rows=[],
                ),
                "players_los_cone.observers[1].uid",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "in_radius")]),
                "players_los_cone.observers[0].in_radius",
            ),
            (
                _wire_read(observers=[_wire_observer(in_radius=1.0)]),
                "players_los_cone.observers[0].in_radius",
            ),
            (
                _wire_read(observers=[_wire_observer(in_radius=True)]),
                "players_los_cone.observers[0].in_radius",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "in_cone")]),
                "players_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(observers=[_wire_observer(in_cone=1.0)]),
                "players_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(observers=[_wire_observer(in_cone=True)]),
                "players_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "rays")]),
                "players_los_cone.observers[0].rays",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=1.0)]),
                "players_los_cone.observers[0].rays",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=True)]),
                "players_los_cone.observers[0].rays",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "rows")]),
                "players_los_cone.observers[0].rows",
            ),
            (
                _wire_read(observers=[_wire_observer(rows=1.0)]),
                "players_los_cone.observers[0].rows",
            ),
            (
                _wire_read(observers=[_wire_observer(rows=True)]),
                "players_los_cone.observers[0].rows",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=2, rows=1, in_cone=2, in_radius=2)]),
                "players_los_cone.observers[0].rays",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=9, rows=9, in_cone=9, in_radius=9)]),
                "players_los_cone.observers[0].rays",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=1, rows=2, in_cone=1, in_radius=2)]),
                "players_los_cone.observers[0].rows",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=1, rows=33, in_cone=33, in_radius=33)]),
                "players_los_cone.observers[0].rows",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=1, rows=1, in_cone=2, in_radius=1)]),
                "players_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            found=0,
                            yaw_deg_available=0,
                            in_radius=1,
                            in_cone=0,
                            rays=0,
                            rows=0,
                        )
                    ],
                    rows=[],
                ),
                "players_los_cone.observers[0].in_radius",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            found=0,
                            yaw_deg_available=1,
                            in_radius=0,
                            in_cone=0,
                            rays=0,
                            rows=0,
                        )
                    ],
                    rows=[],
                ),
                "players_los_cone.observers[0].yaw_deg_available",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            found=1,
                            yaw_deg_available=0,
                            in_radius=2,
                            in_cone=1,
                            rays=0,
                            rows=1,
                        )
                    ],
                    rows=[_wire_row(gap_kind="ray_budget_exceeded")],
                ),
                "players_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "yaw_deg")]),
                "players_los_cone.observers[0].yaw_deg",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg="45.0")]),
                "players_los_cone.observers[0].yaw_deg",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg=True)]),
                "players_los_cone.observers[0].yaw_deg",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg=math.nan)]),
                "players_los_cone.observers[0].yaw_deg",
            ),
            (
                _wire_read(rows=["not-a-dict"]),
                "players_los_cone.rows[0]",
            ),
            (
                _wire_read(rows=[_without(_wire_row(), "uid")]),
                "players_los_cone.rows[0].uid",
            ),
            (
                _wire_read(rows=[_wire_row(uid=123)]),
                "players_los_cone.rows[0].uid",
            ),
            (
                _wire_read(rows=[_wire_row(uid="unknown-obs")]),
                "players_los_cone.rows[0].uid",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            uid="obs-dead",
                            found=0,
                            yaw_deg_available=0,
                            in_radius=0,
                            in_cone=0,
                            rays=0,
                            rows=0,
                        )
                    ],
                    rows=[_wire_row(uid="obs-dead")],
                ),
                "players_los_cone.rows[0].uid",
            ),
            (
                _wire_read(rows=[_without(_wire_row(), "tick_ms")]),
                "players_los_cone.rows[0].tick_ms",
            ),
            (
                _wire_read(tick_ms=12000, rows=[_wire_row(tick_ms=12000.0)]),
                "players_los_cone.rows[0].tick_ms",
            ),
            (
                _wire_read(tick_ms=12000, rows=[_wire_row(tick_ms=12001)]),
                "players_los_cone.rows[0].tick_ms",
            ),
            (
                _wire_read(
                    observers=[_wire_observer(in_radius=2, in_cone=2, rays=1, rows=2)],
                    rows=[_wire_row()],
                ),
                "players_los_cone.observers[0].rows",
            ),
            (
                _wire_read(
                    observers=[_wire_observer(in_radius=1, in_cone=1, rays=1, rows=1)],
                    rows=[_wire_row(gap_kind="ray_budget_exceeded")],
                ),
                "players_los_cone.observers[0].rays",
            ),
            (
                _wire_read(
                    truncated=0,
                    observers=[_wire_observer(in_radius=5, in_cone=2, rays=1, rows=1)],
                    rows=[_wire_row()],
                ),
                "players_los_cone.truncated",
            ),
            (
                _wire_read(
                    truncated=1,
                    observers=[_wire_observer(in_radius=1, in_cone=1, rays=1, rows=1)],
                    rows=[_wire_row()],
                ),
                "players_los_cone.truncated",
            ),
        )
        for idx, (bad_read, where) in enumerate(result_cases):
            with self.subTest(idx=idx, where=where):
                with self.assertRaises(ValueError) as raised:
                    players_los_cone.normalize_bridge_result({"ok": 1, FIELD: bad_read})
                self.assertEqual(
                    str(raised.exception), f"bad_bridge_los_cone_result: {where}"
                )


class TranslatedEnforceFunctionsTest(unittest.TestCase):
    """T10, T11, T12: real Enforce bodies translated and executed in Python."""

    def setUp(self) -> None:
        self.bridge = _source(SERVER_BRIDGE_PATH)

    def test_t10_los_cone_yaw_off_deg_real_body(self) -> None:
        # Kills M3: LosConeYawOffDeg must wrap into (-180, 180].
        fn = translate(self.bridge, SIG_YAW_OFF, {})
        cases = (
            ((0.0, 350.0), 10.0),
            ((10.0, 20.0), -10.0),
            ((-170.0, 170.0), 20.0),
            ((170.0, 190.0), -20.0),
            ((179.0, 359.0), 180.0),
            ((3.0, 0.0), 3.0),
            ((-90.0, 270.0), 0.0),
            ((-180.0, 360.0), 180.0),
            ((180.0, 0.0), 180.0),
        )
        for (bearing, yaw), expected in cases:
            with self.subTest(bearing=bearing, yaw=yaw):
                self.assertAlmostEqual(fn(bearing, yaw), expected, places=6)

    def test_t11_los_cone_in_cone_real_body(self) -> None:
        # Kills M1 (radius check) and M2 (yaw check).
        ns = {"Math": SimpleNamespace(AbsFloat=abs)}
        fn = translate(self.bridge, SIG_IN_CONE, ns)
        cases = (
            ((60.0, 50.0, 0.0, 10.0), False),
            ((20.0, 50.0, 30.0, 10.0), False),
            ((20.0, 50.0, -30.0, 10.0), False),
            ((20.0, 50.0, 3.0, 10.0), True),
            ((20.0, 50.0, -3.0, 10.0), True),
            ((20.0, 50.0, 10.0, 10.0), True),
            ((20.0, 50.0, -10.0, 10.0), True),
            ((20.0, 50.0, 10.01, 10.0), False),
            ((50.0, 50.0, 0.0, 10.0), False),
            ((49.99, 50.0, 0.0, 10.0), True),
        )
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertIs(fn(*args), expected)

    def test_t12_los_cone_nearest_and_gap_kind_real_bodies(self) -> None:
        # Kills M5 (LosConeNearest) and M4 (LosConeGapKind).
        nearest = translate(self.bridge, SIG_NEAREST, {}, class_types=("array",))
        distances = EnforceArray([30.0, 10.0, 20.0, 10.0])
        taken = EnforceArray([False, False, False, False])
        order: list[int] = []
        while True:
            idx = nearest(distances, taken)
            if idx < 0:
                break
            order.append(idx)
            taken[idx] = True
        self.assertEqual(order, [1, 3, 2, 0])
        self.assertEqual(nearest(distances, taken), -1)
        self.assertEqual(nearest(EnforceArray(), EnforceArray()), -1)

        gap_kind = translate(self.bridge, SIG_GAP_KIND, {})
        self.assertEqual(gap_kind(0, 8).text, "")
        self.assertEqual(gap_kind(7, 8).text, "")
        self.assertEqual(gap_kind(8, 8).text, "ray_budget_exceeded")
        self.assertEqual(gap_kind(31, 8).text, "ray_budget_exceeded")
        self.assertEqual(gap_kind(3, 4).text, "")
        self.assertEqual(gap_kind(4, 4).text, "ray_budget_exceeded")


class _FakeIdentity:
    def __init__(self, plain_id: str) -> None:
        self._plain_id = _LosConeString.of(plain_id)

    def GetPlainId(self) -> _LosConeString:
        return self._plain_id


class _FakePlayer:
    def __init__(
        self,
        uid: str | None,
        head: tuple[float, float, float],
        *,
        alive: bool = True,
        has_bone: bool = True,
        heading_rad: float | None = 0.0,
        pitch_deg: float | None = 5.5,
    ) -> None:
        self._identity = None if uid is None else _FakeIdentity(uid)
        self._head = EnforceVector(head)
        self._alive = alive
        self._has_bone = has_bone
        self._hic = (
            None
            if heading_rad is None
            else SimpleNamespace(GetHeadingAngle=lambda: heading_rad)
        )
        self._weapon = (
            None
            if pitch_deg is None
            else SimpleNamespace(GetBaseAimingAngleUD=lambda: pitch_deg)
        )

    def IsAlive(self) -> bool:
        return self._alive

    def GetIdentity(self) -> _FakeIdentity | None:
        return self._identity

    def GetBoneIndexByName(self, _name: object) -> int:
        return 12 if self._has_bone else -1

    def GetInputController(self) -> object:
        return self._hic

    def GetCommandModifier_Weapons(self) -> object:
        return self._weapon


class DispatchPlayersLosConeTest(unittest.TestCase):
    """T13: translated real DispatchPlayersLosCone + source text & no-write checks."""

    def setUp(self) -> None:
        self.bridge = _source(SERVER_BRIDGE_PATH)
        self.messages = _source(MESSAGES_PATH)

    def _build_dispatch(
        self,
        players: list[object],
        *,
        tick_ms: int = 42000,
        ray_handler: object = None,
    ) -> tuple[object, dict[str, object]]:
        clock_reads = [0]
        ray_calls: list[tuple[EnforceVector, EnforceVector, object, object]] = []

        def get_time() -> int:
            clock_reads[0] += 1
            return tick_ms

        def get_players(out_list: _LosConeArray) -> None:
            for item in players:
                out_list.Insert(item)

        def find_human(uid: EnforceString) -> object:
            for item in players:
                if isinstance(item, _FakePlayer):
                    ident = item.GetIdentity()
                    if ident is not None and ident.GetPlainId() == uid:
                        return item
            return None

        def cast_ray(
            from_pos: EnforceVector,
            to_pos: EnforceVector,
            observer: object,
            hit: object,
        ) -> None:
            ray_calls.append((EnforceVector(from_pos), EnforceVector(to_pos), observer, hit))
            if callable(ray_handler):
                ray_handler(from_pos, to_pos, observer, hit)

        def compass_deg(heading_rad: float) -> float:
            deg = 0.0 - heading_rad * (180.0 / math.pi)
            while deg < 0.0:
                deg += 360.0
            while deg >= 360.0:
                deg -= 360.0
            return deg

        def vector_distance(a: EnforceVector, b: EnforceVector) -> float:
            return math.sqrt(
                (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
            )

        def vector_to_array(vec: EnforceVector, arr: EnforceArray) -> None:
            arr.Clear()
            arr.Insert(vec[0])
            arr.Insert(vec[1])
            arr.Insert(vec[2])

        ns: dict[str, object] = {
            "_S": _LosConeString,
            "array": _LosConeArray,
            "LOS_CONE_SCHEMA": _LosConeString.of(players_los_cone.SCHEMA),
            "LOS_CONE_RADIUS_MAX": players_los_cone.RADIUS_MAX_M,
            "LOS_CONE_HALF_YAW_MAX_DEG": players_los_cone.HALF_YAW_MAX_DEG,
            "LOS_CONE_OBSERVERS_MAX": players_los_cone.OBSERVERS_MAX,
            "LOS_CONE_RAYS_PER_OBSERVER_MAX": players_los_cone.RAYS_PER_OBSERVER_MAX,
            "LOS_CONE_ROWS_PER_OBSERVER_MAX": players_los_cone.ROWS_PER_OBSERVER_MAX,
            "VISIBILITY_HEAD_BONE": _LosConeString.of("Head"),
            "m_Players": _LosConeArray(),
            "IsFiniteFloat": math.isfinite,
            "GetGame": lambda: SimpleNamespace(
                GetTime=get_time, GetPlayers=get_players
            ),
            "PlayerBase": SimpleNamespace(
                Cast=lambda obj: obj if isinstance(obj, _FakePlayer) else None
            ),
            "FindHumanByUid": find_human,
            "PlayerHeadPosition": lambda player: EnforceVector(player._head),
            "MCPPlayerTrace": SimpleNamespace(CompassDeg=compass_deg),
            "Math": SimpleNamespace(
                Atan2=math.atan2,
                RAD2DEG=180.0 / math.pi,
                AbsFloat=abs,
            ),
            "vector": SimpleNamespace(Distance=vector_distance),
            "Vector": lambda x, y, z: EnforceVector((x, y, z)),
            "VectorToArray": vector_to_array,
            "LosConeCastRay": cast_ray,
        }
        for cls_name in (
            "MCPPlayersLosConeRow",
            "MCPPlayersLosConeObserver",
            "MCPPlayersLosConeRead",
            "MCPRaycastHit",
        ):
            fake_class(self.messages, cls_name, ns)

        translate(self.bridge, SIG_YAW_OFF, ns)
        translate(self.bridge, SIG_IN_CONE, ns)
        translate(self.bridge, SIG_NEAREST, ns, class_types=("array",))
        translate(self.bridge, SIG_GAP_KIND, ns)
        dispatch = translate(
            self.bridge,
            SIG_DISPATCH,
            ns,
            class_types=(
                "MCPCommand",
                "MCPResult",
                "MCPArgs",
                "MCPPlayersLosConeRead",
                "MCPPlayersLosConeObserver",
                "MCPPlayersLosConeRow",
                "MCPRaycastHit",
                "PlayerBase",
                "PlayerIdentity",
                "HumanInputController",
                "HumanCommandWeapons",
                "array",
            ),
            members=("m_Players",),
        )
        return dispatch, {"clock_reads": clock_reads, "ray_calls": ray_calls}

    def _run_dispatch(
        self,
        dispatch: object,
        args: object,
    ) -> tuple[bool, SimpleNamespace]:
        command = SimpleNamespace(args=args)
        result = SimpleNamespace(ok=False, error=_LosConeString.of(""), players_los_cone=None)
        post_now = dispatch(command, result)  # type: ignore[operator]
        return post_now, result

    def test_t13_dispatch_arg_rejections_real_body(self) -> None:
        dispatch, _meta = self._build_dispatch([])

        def make_args(
            uids: list[str] | None,
            radius: float = 50.0,
            half_yaw_deg: float = 10.0,
        ) -> SimpleNamespace:
            arr = (
                None
                if uids is None
                else _LosConeArray([_LosConeString.of(u) for u in uids])
            )
            return SimpleNamespace(uids=arr, radius=radius, half_yaw_deg=half_yaw_deg)

        bad_cases = (
            ("no_args", None),
            ("null_uids", make_args(None)),
            ("empty_uids", make_args([])),
            ("five_uids", make_args(["u1", "u2", "u3", "u4", "u5"])),
            ("empty_uid", make_args([""])),
            ("blank_uid", make_args(["   "])),
            ("dup_uid", make_args(["u1", "u1"])),
            ("radius_zero", make_args(["u1"], radius=0.0)),
            ("radius_neg", make_args(["u1"], radius=-1.0)),
            ("radius_over", make_args(["u1"], radius=50.1)),
            ("radius_nan", make_args(["u1"], radius=math.nan)),
            ("radius_inf", make_args(["u1"], radius=math.inf)),
            ("half_yaw_zero", make_args(["u1"], half_yaw_deg=0.0)),
            ("half_yaw_neg", make_args(["u1"], half_yaw_deg=-1.0)),
            ("half_yaw_over", make_args(["u1"], half_yaw_deg=30.1)),
            ("half_yaw_nan", make_args(["u1"], half_yaw_deg=math.nan)),
            ("half_yaw_inf", make_args(["u1"], half_yaw_deg=math.inf)),
        )
        for label, bad_args in bad_cases:
            with self.subTest(case=label):
                post_now, result = self._run_dispatch(dispatch, bad_args)
                self.assertIs(post_now, True)
                self.assertIs(result.ok, False)
                self.assertEqual(result.error.text, "bad_args")

    def test_t13_dispatch_filtering_ordering_rays_and_budget_real_body(self) -> None:
        obs_a = _FakePlayer("obs-a", (10.0, 1.7, 20.0), heading_rad=0.0, pitch_deg=6.0)
        obs_dead = _FakePlayer("obs-dead", (0.0, 1.7, 0.0), alive=False)
        obs_no_hic = _FakePlayer("obs-nohic", (10.0, 1.7, 20.0), heading_rad=None)

        non_player_man = SimpleNamespace(IsAlive=lambda: True)
        # Dead player directly in front of obs-a (kills M10 if IsAlive is removed).
        dead_in_cone = _FakePlayer("dead-cand", (10.0, 1.7, 30.0), alive=False)
        no_ident = _FakePlayer(None, (10.0, 1.7, 32.0), alive=True)
        empty_uid = _FakePlayer("", (10.0, 1.7, 34.0), alive=True)
        out_of_cone = _FakePlayer("side-cand", (30.0, 1.7, 20.0), alive=True)
        out_of_radius = _FakePlayer("far-cand", (10.0, 1.7, 70.0), alive=True)

        # 10 living candidates in cone at unsorted distances (5..41 m along +Z).
        cone_specs = [
            ("c-30", (10.0, 1.7, 50.0), True),
            ("c-05", (10.0, 1.7, 25.0), True),
            ("c-25", (10.0, 1.7, 45.0), False),
            ("c-10", (10.0, 1.7, 30.0), True),
            ("c-40", (10.0, 1.7, 60.0), True),
            ("c-15", (10.0, 1.7, 35.0), True),
            ("c-35", (10.0, 1.7, 55.0), True),
            ("c-20", (10.0, 1.7, 40.0), True),
            ("c-45", (10.0, 1.7, 65.0), True),
            ("c-42", (10.0, 1.7, 62.0), True),
        ]
        cone_players = [
            _FakePlayer(uid, head, has_bone=bone) for uid, head, bone in cone_specs
        ]
        world = [
            obs_a,
            obs_dead,
            obs_no_hic,
            non_player_man,
            dead_in_cone,
            no_ident,
            empty_uid,
            out_of_cone,
            out_of_radius,
            *cone_players,
        ]

        ray_seq = [0]

        def ray_handler(
            from_pos: EnforceVector,
            to_pos: EnforceVector,
            _observer: object,
            hit: object,
        ) -> None:
            idx = ray_seq[0]
            ray_seq[0] += 1
            if idx == 0:
                # Object hit on nearest candidate (c-05).
                hit.hit = True  # type: ignore[attr-defined]
                hit.pos.Insert(10.0)  # type: ignore[attr-defined]
                hit.pos.Insert(1.7)  # type: ignore[attr-defined]
                hit.pos.Insert(23.0)  # type: ignore[attr-defined]
                hit.distance = 3.0  # type: ignore[attr-defined]
                hit.object_type = _LosConeString.of("Land_Wall")  # type: ignore[attr-defined]
                hit.object_class = _LosConeString.of("HouseNoDestruct")  # type: ignore[attr-defined]
            elif idx == 1:
                # Terrain hit (no object_type/object_class): must NOT inherit idx 0's object!
                hit.hit = True  # type: ignore[attr-defined]
                hit.pos.Insert(10.0)  # type: ignore[attr-defined]
                hit.pos.Insert(1.7)  # type: ignore[attr-defined]
                hit.pos.Insert(26.0)  # type: ignore[attr-defined]
                hit.distance = 6.0  # type: ignore[attr-defined]
            else:
                # Miss.
                hit.hit = False  # type: ignore[attr-defined]

        dispatch, meta = self._build_dispatch(world, tick_ms=55000, ray_handler=ray_handler)
        args = SimpleNamespace(
            uids=_LosConeArray(
                [
                    _LosConeString.of("obs-a"),
                    _LosConeString.of("obs-dead"),
                    _LosConeString.of("obs-offline"),
                    _LosConeString.of("obs-nohic"),
                ]
            ),
            radius=50.0,
            half_yaw_deg=10.0,
        )
        post_now, result = self._run_dispatch(dispatch, args)
        self.assertIs(post_now, True)
        self.assertIs(result.ok, True)
        # Single GetGame().GetTime() read for all 4 observers.
        self.assertEqual(meta["clock_reads"], [1])

        read = result.players_los_cone
        self.assertEqual(read.schema.text, players_los_cone.SCHEMA)
        self.assertEqual(read.tick_ms, 55000)
        self.assertIs(read.pitch_filter_applied, False)
        self.assertIs(read.truncated, False)

        # Observer summaries in requested order.
        self.assertEqual(
            [o.uid.text for o in read.observers],
            ["obs-a", "obs-dead", "obs-offline", "obs-nohic"],
        )
        sum_a, sum_dead, sum_off, sum_nohic = list(read.observers)
        self.assertIs(sum_a.found, True)
        self.assertIs(sum_a.yaw_deg_available, True)
        self.assertEqual(sum_a.yaw_deg, 0.0)
        # in_radius for obs-a: 10 cone candidates + out_of_cone + obs_no_hic (0m away!) = 12
        self.assertEqual(sum_a.in_radius, 12)
        # obs_no_hic is at same coords (0 m, dx=0, dz=0 -> bearing 0 -> yaw_off 0) -> in_cone!
        # So in_cone = 10 + 1 = 11.
        self.assertEqual(sum_a.in_cone, 11)
        self.assertEqual(sum_a.rays, 8)
        self.assertEqual(sum_a.rows, 11)

        self.assertIs(sum_dead.found, False)
        self.assertEqual((sum_dead.in_radius, sum_dead.in_cone, sum_dead.rays, sum_dead.rows), (0, 0, 0, 0))
        self.assertIs(sum_off.found, False)
        self.assertEqual((sum_off.in_radius, sum_off.in_cone, sum_off.rays, sum_off.rows), (0, 0, 0, 0))
        self.assertIs(sum_nohic.found, True)
        self.assertIs(sum_nohic.yaw_deg_available, False)
        self.assertEqual(sum_nohic.in_radius, 12)
        self.assertEqual((sum_nohic.in_cone, sum_nohic.rays, sum_nohic.rows), (0, 0, 0))

        # Verify excluded entities never appear as target_uid (kills M10).
        emitted_targets = [r.target_uid.text for r in read.rows]
        for excluded in ("obs-a", "dead-cand", "side-cand", "far-cand", ""):
            self.assertNotIn(excluded, emitted_targets)
        self.assertEqual(
            emitted_targets,
            [
                "obs-nohic",
                "c-05",
                "c-10",
                "c-15",
                "c-20",
                "c-25",
                "c-30",
                "c-35",
                "c-40",
                "c-42",
                "c-45",
            ],
        )

        # Kills M11: target_head must be candidate's head, distinct from observer_head.
        row_c05 = read.rows.Get(1)
        self.assertEqual(list(row_c05.observer_head), [10.0, 1.7, 20.0])
        self.assertEqual(list(row_c05.target_head), [10.0, 1.7, 25.0])
        self.assertNotEqual(list(row_c05.target_head), list(row_c05.observer_head))

        # First 8 rows got rays with fresh MCPRaycastHit instances; rows 8..10 got budget gap.
        ray_calls = meta["ray_calls"]
        self.assertEqual(len(ray_calls), 8)  # type: ignore[arg-type]
        self.assertEqual(len({id(call[3]) for call in ray_calls}), 8)  # type: ignore[attr-defined]
        for from_pos, _to_pos, observer, _hit in ray_calls:  # type: ignore[attr-defined]
            self.assertEqual(list(from_pos), [10.0, 1.7, 20.0])
            self.assertIs(observer, obs_a)

        # Row 0 (idx 0 object hit): all hit flags True; Row 1 (idx 1 terrain hit): type/class False.
        row0 = read.rows.Get(0)
        self.assertIs(row0.ray_hit, True)
        self.assertIs(row0.hit_pos_available, True)
        self.assertIs(row0.hit_type_available, True)
        self.assertEqual(row0.hit_type.text, "Land_Wall")
        self.assertIs(row0.hit_class_available, True)
        self.assertEqual(row0.hit_class.text, "HouseNoDestruct")

        self.assertIs(row_c05.ray_hit, True)
        self.assertIs(row_c05.hit_pos_available, True)
        self.assertIs(row_c05.hit_distance_m_available, True)
        self.assertIs(row_c05.clearance_m_available, True)
        self.assertAlmostEqual(row_c05.clearance_m, 1.0, places=5)
        self.assertIs(row_c05.hit_type_available, False)
        self.assertIs(row_c05.hit_class_available, False)

        # Row 2 (miss) and Row 8 (ray_budget_exceeded): all 5 hit flags False.
        for idx, want_gap in ((2, ""), (8, "ray_budget_exceeded"), (10, "ray_budget_exceeded")):
            r = read.rows.Get(idx)
            self.assertEqual(r.gap_kind.text, want_gap)
            self.assertIs(r.hit_pos_available, False)
            self.assertIs(r.hit_distance_m_available, False)
            self.assertIs(r.clearance_m_available, False)
            self.assertIs(r.hit_type_available, False)
            self.assertIs(r.hit_class_available, False)

        # End-to-end: wire payload passes Python normalize_bridge_result cleanly.
        wire_payload = {"ok": 1, FIELD: _to_wire_value(read)}
        normalized = players_los_cone.normalize_bridge_result(wire_payload)
        self.assertEqual(len(normalized["evidence"]["rows"]), 11)
        self.assertIs(normalized["evidence"]["truncated"], False)

    def test_t13_dispatch_truncates_past_32_rows_per_observer_real_body(self) -> None:
        obs = _FakePlayer("obs-a", (0.0, 1.7, 0.0), heading_rad=0.0)
        cands = [
            _FakePlayer(f"c-{i:02d}", (0.0, 1.7, 2.0 + i * 1.0))
            for i in range(35)
        ]
        dispatch, meta = self._build_dispatch([obs, *cands], tick_ms=70000)
        args = SimpleNamespace(
            uids=_LosConeArray([_LosConeString.of("obs-a")]),
            radius=50.0,
            half_yaw_deg=10.0,
        )
        post_now, result = self._run_dispatch(dispatch, args)
        self.assertIs(post_now, True)
        self.assertIs(result.ok, True)
        read = result.players_los_cone
        self.assertIs(read.truncated, True)
        summary = read.observers.Get(0)
        self.assertEqual(summary.in_cone, 35)
        self.assertEqual(summary.rays, 8)
        self.assertEqual(summary.rows, 32)
        self.assertEqual(read.rows.Count(), 32)
        self.assertEqual(len(meta["ray_calls"]), 8)  # type: ignore[arg-type]

        normalized = players_los_cone.normalize_bridge_result(
            {"ok": 1, FIELD: _to_wire_value(read)}
        )
        self.assertIs(normalized["players_los_cone"]["truncated"], True)
        self.assertIs(normalized["evidence"]["truncated"], True)

    def test_t13_dispatch_cardinal_headings_real_body(self) -> None:
        # Cardinal headings with nonzero yaw catch sign flips in Math.Atan2(dx, dz):
        # Math.Atan2(-dx, dz) casts west instead of east at yaw 90, flips yaw_off_deg
        # at yaw 180, and misses the right-of-heading target at yaw 270.
        obs_east = _FakePlayer("obs-east", (100.0, 1.7, 200.0), heading_rad=-math.pi / 2.0)
        cand_east = _FakePlayer("cand-east", (120.0, 1.7, 200.0))
        cand_west = _FakePlayer("cand-west", (80.0, 1.7, 200.0))
        dispatch, meta = self._build_dispatch(
            [obs_east, cand_east, cand_west], tick_ms=80000
        )
        args = SimpleNamespace(
            uids=_LosConeArray([_LosConeString.of("obs-east")]),
            radius=50.0,
            half_yaw_deg=10.0,
        )
        post_now, result = self._run_dispatch(dispatch, args)
        self.assertIs(post_now, True)
        self.assertIs(result.ok, True)
        read = result.players_los_cone
        summary = read.observers.Get(0)
        self.assertIs(summary.found, True)
        self.assertIs(summary.yaw_deg_available, True)
        self.assertAlmostEqual(summary.yaw_deg, 90.0, places=5)
        self.assertEqual(summary.in_radius, 2)
        self.assertEqual(summary.in_cone, 1)
        self.assertEqual(summary.rays, 1)
        self.assertEqual(summary.rows, 1)
        self.assertEqual(read.rows.Count(), 1)

        row = read.rows.Get(0)
        self.assertEqual(row.uid.text, "obs-east")
        self.assertEqual(row.target_uid.text, "cand-east")
        self.assertAlmostEqual(row.yaw_deg, 90.0, places=5)
        self.assertAlmostEqual(row.yaw_off_deg, 0.0, places=4)
        self.assertAlmostEqual(row.distance_m, 20.0, places=4)
        self.assertEqual(row.gap_kind.text, "")

        ray_calls = meta["ray_calls"]
        self.assertEqual(len(ray_calls), 1)  # type: ignore[arg-type]
        from_pos, to_pos, observer, _hit = ray_calls[0]  # type: ignore[index]
        self.assertEqual(list(from_pos), [100.0, 1.7, 200.0])
        self.assertEqual(list(to_pos), [120.0, 1.7, 200.0])
        self.assertIs(observer, obs_east)

        # Facing south (180 deg, -Z), right is -X; facing west (270 deg, -X),
        # right is +Z. A candidate 5 deg to the right must produce yaw_off_deg == +5.
        right_offset_deg = 5.0
        cardinal_cases = (
            ("south_180", -math.pi, 180.0, 180.0 + right_offset_deg, -1, -1),
            ("west_270", math.pi / 2.0, 270.0, 270.0 + right_offset_deg, -1, 1),
        )
        for label, heading_rad, expected_yaw_deg, bearing_deg, dx_sign, dz_sign in cardinal_cases:
            with self.subTest(heading=label):
                bearing_rad = math.radians(bearing_deg)
                dx = 20.0 * math.sin(bearing_rad)
                dz = 20.0 * math.cos(bearing_rad)
                self.assertEqual(1 if dx > 0.0 else -1, dx_sign)
                self.assertEqual(1 if dz > 0.0 else -1, dz_sign)

                obs = _FakePlayer("obs-card", (100.0, 1.7, 200.0), heading_rad=heading_rad)
                cand = _FakePlayer("cand-right", (100.0 + dx, 1.7, 200.0 + dz))
                dispatch_c, meta_c = self._build_dispatch([obs, cand], tick_ms=81000)
                args_c = SimpleNamespace(
                    uids=_LosConeArray([_LosConeString.of("obs-card")]),
                    radius=50.0,
                    half_yaw_deg=10.0,
                )
                post_c, res_c = self._run_dispatch(dispatch_c, args_c)
                self.assertIs(post_c, True)
                self.assertIs(res_c.ok, True)
                read_c = res_c.players_los_cone
                sum_c = read_c.observers.Get(0)
                self.assertIs(sum_c.found, True)
                self.assertIs(sum_c.yaw_deg_available, True)
                self.assertAlmostEqual(sum_c.yaw_deg, expected_yaw_deg, places=5)
                self.assertEqual(sum_c.in_radius, 1)
                self.assertEqual(sum_c.in_cone, 1)
                self.assertEqual(sum_c.rays, 1)
                self.assertEqual(sum_c.rows, 1)
                self.assertEqual(read_c.rows.Count(), 1)
                row_c = read_c.rows.Get(0)
                self.assertEqual(row_c.target_uid.text, "cand-right")
                self.assertAlmostEqual(row_c.yaw_deg, expected_yaw_deg, places=5)
                self.assertGreater(row_c.yaw_off_deg, 0.0)
                self.assertAlmostEqual(row_c.yaw_off_deg, right_offset_deg, places=4)
                self.assertEqual(len(meta_c["ray_calls"]), 1)  # type: ignore[arg-type]

    def test_t13_dispatch_source_text_ray_branch_and_no_world_writes(self) -> None:
        dispatch = clean(method_body(self.bridge, SIG_DISPATCH))
        cast_ray = clean(method_body(self.bridge, SIG_CAST_RAY))

        # Single clock read, single ray helper call under gapKind == "", after LosConeInCone.
        self.assertEqual(dispatch.count("GetGame().GetTime()"), 1)
        self.assertEqual(dispatch.count("LosConeCastRay("), 1)
        self.assertEqual(dispatch.count("PopulateRaycastRVProxy("), 0)
        self.assertEqual(cast_ray.count("PopulateRaycastRVProxy("), 1)

        _in_order(
            self,
            dispatch,
            "obsPlayer = PlayerBase.Cast(FindHumanByUid(obsUid));",
            "if (obsPlayer && obsPlayer.IsAlive())",
            "obsHead = PlayerHeadPosition(obsPlayer);",
            "obsHeadBone = obsPlayer.GetBoneIndexByName(VISIBILITY_HEAD_BONE) >= 0;",
            "obsSummary.yaw_deg = MCPPlayerTrace.CompassDeg(hic.GetHeadingAngle());",
            "obsPitchDeg = weaponCmd.GetBaseAimingAngleUD();",
            "GetGame().GetPlayers(m_Players);",
            "candPlayer = PlayerBase.Cast(m_Players.Get(candIndex));",
            "if (candPlayer && candPlayer != obsPlayer && candPlayer.IsAlive())",
            "candHead = PlayerHeadPosition(candPlayer);",
            "if (LosConeInCone(candDist, radius, yawOffDeg, halfYawDeg))",
            "bestIndex = LosConeNearest(candDistances, candTaken);",
            "gapKind = LosConeGapKind(rowPosition, LOS_CONE_RAYS_PER_OBSERVER_MAX);",
            "row.uid = obsUid;",
            "row.tick_ms = tickMs;",
            "row.gap_kind = gapKind;",
            "row.target_uid = candUids.Get(bestIndex);",
            "VectorToArray(obsHead, row.observer_head);",
            "VectorToArray(rowTargetHead, row.target_head);",
            "row.observer_head_bone = obsHeadBone;",
            "row.target_head_bone = candHeadBones.Get(bestIndex);",
            "row.distance_m = candDistances.Get(bestIndex);",
            "row.yaw_deg = obsSummary.yaw_deg;",
            "row.yaw_off_deg = candYawOffs.Get(bestIndex);",
            "row.pitch_deg = obsPitchDeg;",
            "row.pitch_deg_available = obsPitchAvailable;",
            'if (gapKind == "")',
            "hit = new MCPRaycastHit();",
            "LosConeCastRay(obsHead, rowTargetHead, obsPlayer, hit);",
            "row.ray_hit = hit.hit;",
            "VectorToArray(hitPos, row.hit_pos);",
            "row.hit_pos_available = true;",
            "row.hit_distance_m = hit.distance;",
            "row.hit_distance_m_available = true;",
            "row.clearance_m = vector.Distance(hitPos, rowTargetHead);",
            "row.clearance_m_available = true;",
            "row.hit_type = hit.object_type;",
            "row.hit_type_available = true;",
            "row.hit_class = hit.object_class;",
            "row.hit_class_available = true;",
        )

        _in_order(
            self,
            cast_ray,
            "ray = new MCPRaycastValidation();",
            "ray.from = fromPos;",
            "ray.to = toPos;",
            "ray.radius = VISIBILITY_RAY_RADIUS;",
            "ray.intersect_type = ObjIntersectView;",
            "PopulateRaycastRVProxy(ray, observer, hit);",
        )

        # No world writes or banned reads across all new bridge methods.
        new_code = "\n".join(
            clean(method_body(self.bridge, sig)) for sig in NEW_BRIDGE_METHODS
        )
        for banned in (
            "SetPosition",
            "SetTransform",
            "CreateObject",
            "DeleteObject",
            "ObjectDelete",
            "SetHealth",
            "OverrideMovement",
            "OverrideHeading",
            "StartCommand",
            "GetTickTime",
            "GetHealth",
            "OpenFile",
            "RayCastBullet",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, new_code)


class EnforceSourceContractTest(unittest.TestCase):
    """T15 (messages & prune), T18 (mirrors), T19 (compile hazards)."""

    def setUp(self) -> None:
        self.bridge = _source(SERVER_BRIDGE_PATH)
        self.bridge_code = clean(self.bridge)
        self.messages = _source(MESSAGES_PATH)

    def test_t15_messages_members_and_result_prune(self) -> None:
        self.assertEqual(
            class_members(self.messages, "MCPPlayersLosConeRow"),
            EXPECTED_ROW_MEMBERS,
        )
        self.assertEqual(
            class_members(self.messages, "MCPPlayersLosConeObserver"),
            EXPECTED_OBSERVER_MEMBERS,
        )
        self.assertEqual(
            class_members(self.messages, "MCPPlayersLosConeRead"),
            EXPECTED_READ_MEMBERS,
        )
        self.assertEqual(
            _flat(method_body(self.messages, "void MCPPlayersLosConeRow()")),
            "observer_head = new array<float>(); target_head = new array<float>(); hit_pos = new array<float>();",
        )
        self.assertEqual(
            _flat(method_body(self.messages, "void MCPPlayersLosConeRead()")),
            "observers = new array<ref MCPPlayersLosConeObserver>(); rows = new array<ref MCPPlayersLosConeRow>();",
        )

        args_members = class_members(self.messages, "MCPArgs")
        for member in (
            ("float", "half_yaw_deg"),
            ("float", "radius"),
            ("ref array<string>", "uids"),
        ):
            with self.subTest(args_member=member):
                self.assertEqual(args_members.count(member), 1)

        result_members = class_members(self.messages, "MCPResult")
        kin_member = ("ref MCPPlayersKinematicsRead", "players_kinematics")
        los_member = ("ref MCPPlayersLosConeRead", FIELD)
        self.assertEqual(result_members.count(los_member), 1)
        self.assertEqual(
            result_members.index(los_member), result_members.index(kin_member) + 1
        )

        self.assertIn(FIELD, result_prune.PRUNABLE_FIELDS)
        self.assertEqual(
            result_prune.PRUNABLE_FIELDS.index(FIELD),
            result_prune.PRUNABLE_FIELDS.index("players_kinematics") + 1,
        )
        self.assertNotIn(FIELD, result_prune.OWNED_SCALAR_FIELDS)
        pruned_other = result_prune.prune_unfilled_fields(
            "query_all_players", {"ok": 1, FIELD: {}}
        )
        self.assertNotIn(FIELD, pruned_other)
        kept = result_prune.prune_unfilled_fields(
            COMMAND, {"ok": 1, FIELD: _wire_read()}
        )
        self.assertIn(FIELD, kept)

    def test_t18_python_and_enforce_mirrors_match(self) -> None:
        self.assertIn(
            f'protected const string LOS_CONE_SCHEMA = "{players_los_cone.SCHEMA}";',
            self.bridge_code,
        )
        floats = {
            name: float(value)
            for name, value in re.findall(
                r"protected const float (LOS_CONE_\w+) = (-?[0-9.]+);",
                self.bridge_code,
            )
        }
        self.assertEqual(
            floats,
            {
                "LOS_CONE_RADIUS_MAX": players_los_cone.RADIUS_MAX_M,
                "LOS_CONE_HALF_YAW_MAX_DEG": players_los_cone.HALF_YAW_MAX_DEG,
            },
        )
        ints = {
            name: int(value)
            for name, value in re.findall(
                r"protected const int (LOS_CONE_\w+) = (\d+);",
                self.bridge_code,
            )
        }
        self.assertEqual(
            ints,
            {
                "LOS_CONE_OBSERVERS_MAX": players_los_cone.OBSERVERS_MAX,
                "LOS_CONE_RAYS_PER_OBSERVER_MAX": players_los_cone.RAYS_PER_OBSERVER_MAX,
                "LOS_CONE_ROWS_PER_OBSERVER_MAX": players_los_cone.ROWS_PER_OBSERVER_MAX,
            },
        )
        gap_returns = set(
            re.findall(r'return "(\w*)";', method_body(self.bridge, SIG_GAP_KIND))
        )
        self.assertEqual(gap_returns - {""}, players_los_cone.GAP_KINDS)

    def test_t19_compile_hazards_locals_on_top_no_ternary(self) -> None:
        type_names = set(
            re.findall(
                r"\bclass\s+(\w+)",
                "".join(path.read_text(encoding="utf-8") for path in SCRIPTS.rglob("*.c")),
            )
        )
        type_names |= set(_LOCAL_TYPES) | {"IEntity", "Math", "array", "map"}
        for signature in NEW_BRIDGE_METHODS:
            with self.subTest(method=signature):
                body = method_body(self.bridge, signature)
                names = _locals_declared_at_top(body)
                self.assertFalse(set(names) & type_names, names)
                code = clean(body)
                self.assertIsNone(re.search(r"\blocal\b", code))
                self.assertNotIn("?", code)


class SizeBudgetTest(unittest.TestCase):
    """T17: worst-case 128 wire rows + 16384 B overhead < half MAX_BODY_BYTES."""

    def test_t17_worst_case_payload_and_header_budget(self) -> None:
        num24 = "123456789012345678901234"
        longest_gap = max(players_los_cone.GAP_KINDS, key=len)
        uid64 = "u" * players_los_cone.UID_MAX_CHARS
        worst_row = {
            "uid": uid64,
            "tick_ms": num24,
            "gap_kind": longest_gap,
            "target_uid": "t" * players_los_cone.UID_MAX_CHARS,
            "observer_head": [num24, num24, num24],
            "target_head": [num24, num24, num24],
            "observer_head_bone": False,
            "target_head_bone": False,
            "distance_m": num24,
            "yaw_deg": num24,
            "yaw_off_deg": num24,
            "pitch_deg": num24,
            "pitch_deg_available": False,
            "ray_hit": False,
            "hit_pos": [num24, num24, num24],
            "hit_pos_available": False,
            "hit_distance_m": num24,
            "hit_distance_m_available": False,
            "clearance_m": num24,
            "clearance_m_available": False,
            "hit_type": "T" * 64,
            "hit_type_available": False,
            "hit_class": "C" * 64,
            "hit_class_available": False,
        }
        self.assertEqual(set(worst_row), {name for _t, name in EXPECTED_ROW_MEMBERS})
        row_bytes = len(json.dumps(worst_row, separators=(",", ":")).encode("utf-8"))
        max_rows = (
            players_los_cone.OBSERVERS_MAX * players_los_cone.ROWS_PER_OBSERVER_MAX
        )
        self.assertEqual(max_rows, 128)
        total_bytes = (row_bytes + 1) * max_rows + 16384
        self.assertLess(total_bytes, loopback.MAX_BODY_BYTES // 2)

        worst_header = {
            "schema": players_los_cone.SCHEMA,
            "tick_ms": num24,
            "radius_m": num24,
            "half_yaw_deg": num24,
            "pitch_filter_applied": False,
            "observers_max": players_los_cone.OBSERVERS_MAX,
            "rays_per_observer_max": players_los_cone.RAYS_PER_OBSERVER_MAX,
            "rows_per_observer_max": players_los_cone.ROWS_PER_OBSERVER_MAX,
            "truncated": False,
            "observers": [
                {
                    "uid": uid64,
                    "found": True,
                    "yaw_deg_available": True,
                    "yaw_deg": num24,
                    "in_radius": num24,
                    "in_cone": num24,
                    "rays": num24,
                    "rows": num24,
                }
                for _ in range(players_los_cone.OBSERVERS_MAX)
            ],
        }
        header_bytes = len(
            json.dumps(worst_header, separators=(",", ":")).encode("utf-8")
        )
        self.assertLess(header_bytes, 16384)


class PlayersLosConeToolTest(unittest.IsolatedAsyncioTestCase):
    """T16: tool registration, closed schema, peer, error conversion, description."""

    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _m: None)
        )

    async def _description(self) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn(COMMAND, tools)
        return tools[COMMAND].description or ""

    async def test_t16_registered_closed_schema_peer_errors_and_description(self) -> None:
        self.assertIn(COMMAND, server._CLOSED_SCHEMA_TOOLS)
        description = await self._description()
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)

        for field_name in players_los_cone.KIND.fields:
            with self.subTest(field=field_name):
                self.assertIn(field_name, description)
        for gap_name in players_los_cone.GAP_KINDS:
            with self.subTest(gap=gap_name):
                self.assertIn(gap_name, description)
        for fragment in (
            "include_unverified",
            "server's heading for the player, not the client camera",
            "measured in game on 2026-11-27 through players_visibility",
            "free look (holding Alt) does not move it",
            "pitch_filter_applied=false",
            "up to 4 observers",
            "first 8 receive a raycast",
            "up to 32 rows per observer",
            "ObjIntersectView",
            "0.05 m radius",
            "no world writes",
            "players_kinematics",
            "truncated",
            "found=false",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)

        # Calls server peer and normalizes bridge result.
        bridge_answer = {"ok": 1, FIELD: _wire_read()}
        call = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(
                await self.app.call_tool(
                    COMMAND,
                    {"uids": ["76561198000000001"]},
                )
            )
        call.assert_awaited_once_with(
            COMMAND,
            {
                "uids": ["76561198000000001"],
                "radius": 50.0,
                "half_yaw_deg": 10.0,
            },
            "server",
            DEFAULT_TOOL_TIMEOUT_S,
        )
        self.assertIn("evidence", result)
        self.assertIs(result["evidence"]["rows"][0]["pitch_deg_available"], False)

        # Closed schema refuses mistyped uid= instead of uids= before bridge.
        call_extra = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call_extra):
            with self.assertRaises(ToolError):
                await self.app.call_tool(
                    COMMAND, {"uids": ["u1"], "uid": "u1"}
                )
        call_extra.assert_not_awaited()

        # Request ValueError -> ToolError before bridge.
        call_bad_req = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call_bad_req):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(COMMAND, {"uids": ["u1"], "radius_m": 51.0})
        self.assertEqual(_tool_error_text(raised.exception), "bad_radius_m")
        call_bad_req.assert_not_awaited()

        # Normalization ValueError -> ToolError.
        call_bad_res = AsyncMock(
            return_value={"ok": 1, FIELD: _wire_read(truncated=2)}
        )
        with patch.object(self.runtime, "call_bridge", new=call_bad_res):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(COMMAND, {"uids": ["u1"]})
        self.assertEqual(
            _tool_error_text(raised.exception),
            "bad_bridge_los_cone_boolean: players_los_cone.truncated",
        )


if __name__ == "__main__":
    unittest.main()
