"""Contract, translated-Enforce, and tool tests for players_kinematics.

Covers T1-T23 of the players_kinematics specification and kills mutants M1-M8:
- Request validation, daemon ingress, lease and live-profile refusal (T1-T2)
- Server capability census, bitmask, arg-contract hash (T3-T4)
- EvidenceKind contract, wire-to-published envelope, masking, passthrough,
  paging, header and vocabulary checks (T5-T11)
- Translated real Enforce bodies for GapKind, Tick, VehicleClass, Pass
  (T12, T13, T15, T16)
- Source-level engine read, lifecycle, hook, message/prune, tool, size-budget,
  mirror, and compile-hazard checks (T14, T17-T23).
"""
from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import (
    bridge_readiness,
    evidence,
    loopback,
    players_kinematics,
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
    class_members,
    clean,
    method_body,
    translate,
)
from tests.fence_helpers import bind_both_peers
from tests.mcp_helpers import _content_json


COMMAND = "players_kinematics"
FIELD = "players_kinematics"
SCRIPTS = addon_root() / "scripts"
SAMPLER_PATH = SCRIPTS / "4_World" / "MCP_PlayersKinematics.c"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
CLIENT_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
MISSION_SERVER_PATH = SCRIPTS / "5_Mission" / "MissionServer.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
TRACE_ID = "0123456789abcdef0123456789abcdef"
PREVIOUS_ACH = "3b800b7c7ee0674c"
NEW_ACH = "fe4cd4a4597078cf"
WIRE_KEYS = {"mode", "trace_id", "cursor", "limit", "sample_hz", "max_samples", "uids"}

PREVIOUS_49_SERVER_BITS: tuple[str, ...] = (
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
)

EXPECTED_ROW_MEMBERS: list[tuple[str, str]] = [
    ("string", "uid"),
    ("int", "tick_ms"),
    ("string", "gap_kind"),
    ("ref array<float>", "pos"),
    ("float", "expected_dt_s"),
    ("float", "observed_dt_s"),
    ("ref array<float>", "vel"),
    ("ref array<float>", "vel_engine"),
    ("bool", "vel_engine_available"),
    ("float", "yaw_deg"),
    ("bool", "yaw_deg_available"),
    ("float", "pitch_deg"),
    ("bool", "pitch_deg_available"),
    ("int", "stance_idx"),
    ("string", "command"),
    ("bool", "falling"),
    ("bool", "falling_available"),
    ("bool", "jump"),
    ("bool", "jump_available"),
    ("bool", "parachute"),
    ("bool", "parachute_available"),
    ("bool", "in_vehicle"),
    ("string", "vehicle_type"),
    ("string", "vehicle_class"),
]

EXPECTED_READ_MEMBERS: list[tuple[str, str]] = [
    ("string", "schema"),
    ("string", "mode"),
    ("string", "trace_id"),
    ("bool", "active"),
    ("bool", "overflow"),
    ("string", "stop_reason"),
    ("int", "sample_hz"),
    ("int", "interval_ms"),
    ("int", "late_after_ms"),
    ("int", "capacity"),
    ("int", "count"),
    ("int", "passes"),
    ("int", "budget_skips"),
    ("int", "pass_cost_last_ticks"),
    ("int", "pass_cost_max_ticks"),
    ("int", "pass_cost_budget_ticks"),
    ("int", "start_tick_ms"),
    ("int", "now_tick_ms"),
    ("ref array<string>", "uids"),
    ("int", "cursor"),
    ("int", "next_cursor"),
    ("bool", "eof"),
    ("ref array<ref MCPPlayersKinematicsRow>", "rows"),
]

SIG_START = (
    "static bool Start(string traceId, int sampleHz, int maxSamples, array<string> uids)"
)
SIG_TICK = "static void Tick()"
SIG_STOP = "static bool Stop(string traceId)"
SIG_CLEAR = "static bool Clear(string traceId)"
SIG_ABORT = "static void Abort(string reason)"
SIG_MATCHES = "static bool Matches(string traceId)"
SIG_IS_ACTIVE = "static bool IsActive()"
SIG_GET_LAST_ERROR = "static string GetLastError()"
SIG_VIEW = (
    "static MCPPlayersKinematicsRead View(string mode, string traceId, int cursor, int limit)"
)
SIG_GAP_KIND = (
    "static string GapKind(bool hasPrevious, int previousTickMs, int nowTickMs, "
    "int previousPass, int nowPass, int lateAfterMs)"
)
SIG_VEHICLE_CLASS = "static string VehicleClass(Transport transport)"
SIG_PASS = "protected static void Pass(int nowMs)"
SIG_CAPTURE = "protected static void Capture(PlayerBase player, string uid, int nowMs)"
SIG_CLEAR_STATE = "protected static void ClearState()"
SIG_DISPATCH = (
    "protected bool DispatchPlayersKinematics(MCPCommand command, MCPResult result)"
)
SIG_DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
SIG_LEASE_CLOSE = (
    "protected bool DispatchLeaseClose(MCPCommand command, MCPResult result)"
)
SIG_LEASE_CENSUS = (
    "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)"
)
SIG_SHUTDOWN = "void Shutdown()"
SIG_ON_UPDATE = "override void OnUpdate(float timeslice)"

SAMPLER_METHODS = (
    SIG_START,
    SIG_TICK,
    SIG_STOP,
    SIG_CLEAR,
    SIG_ABORT,
    SIG_MATCHES,
    SIG_IS_ACTIVE,
    SIG_GET_LAST_ERROR,
    SIG_VIEW,
    SIG_GAP_KIND,
    SIG_VEHICLE_CLASS,
    SIG_PASS,
    SIG_CAPTURE,
    SIG_CLEAR_STATE,
)

_LOCAL_TYPES = (
    "string",
    "int",
    "float",
    "bool",
    "vector",
    "PlayerBase",
    "PlayerIdentity",
    "HumanCommandVehicle",
    "Transport",
    "HumanInputController",
    "HumanCommandWeapons",
    "MCPArgs",
    "MCPPlayersKinematicsRow",
    "MCPPlayersKinematicsRead",
    "MCPPlayersKinematicsPrev",
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:ref\s+)?(?:" + "|".join(_LOCAL_TYPES) + r")\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)


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
        "mode": "read",
        "trace_id": TRACE_ID,
        "cursor": 0,
        "limit": 64,
        "sample_hz": 10,
        "max_samples": 4096,
        "uids": [],
    }
    args.update(overrides)
    return args


def _wire_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "uid": "76561198000000001",
        "tick_ms": 10000,
        "gap_kind": "",
        "pos": [100.0, 5.0, 200.0],
        "expected_dt_s": 0.1,
        "observed_dt_s": 0.1,
        "vel": [1.0, 0.0, 2.0],
        "vel_engine": [1.0, 0.0, 2.0],
        "vel_engine_available": 1,
        "yaw_deg": 90.0,
        "yaw_deg_available": 1,
        "pitch_deg": 12.5,
        "pitch_deg_available": 1,
        "stance_idx": 0,
        "command": "move",
        "falling": 0,
        "falling_available": 1,
        "jump": 0,
        "jump_available": 1,
        "parachute": 0,
        "parachute_available": 0,
        "in_vehicle": 0,
        "vehicle_type": "-",
        "vehicle_class": "none",
    }
    row.update(overrides)
    return row


_UNSET = object()


def _wire_read(
    mode: str = "read",
    rows: object = _UNSET,
    **overrides: object,
) -> dict[str, object]:
    if rows is _UNSET:
        page: object = [_wire_row()] if mode == "read" else []
    else:
        page = rows
    page_len = len(page) if isinstance(page, list) else 0
    cursor = overrides.get("cursor", 0)
    end = cursor + page_len if isinstance(cursor, int) and not isinstance(cursor, bool) else page_len
    header: dict[str, object] = {
        "schema": players_kinematics.SCHEMA,
        "mode": mode,
        "trace_id": TRACE_ID,
        "active": 1,
        "overflow": 0,
        "stop_reason": "",
        "sample_hz": 10,
        "interval_ms": 100,
        "late_after_ms": 150,
        "capacity": 4096,
        "count": max(page_len, end),
        "passes": 1,
        "budget_skips": 0,
        "pass_cost_last_ticks": 420,
        "pass_cost_max_ticks": 420,
        "pass_cost_budget_ticks": players_kinematics.PASS_BUDGET_TICKS,
        "start_tick_ms": 9900,
        "now_tick_ms": 10050,
        "uids": [],
        "cursor": 0,
        "next_cursor": end,
        "eof": 0,
        "rows": page,
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


class RequestAndIngressTest(unittest.TestCase):
    """T1 (request normalization) and T2 (ingress, lease, live profile)."""

    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)

    def test_t1_start_generates_id_and_every_mode_passes_ingress(self) -> None:
        first = players_kinematics.normalize_request("start", "", 0, 64, 10, 4096, None)
        second = players_kinematics.normalize_request("start", "", 0, 64, 10, 4096, [])
        self.assertEqual(set(first), WIRE_KEYS)
        self.assertRegex(str(first["trace_id"]), r"^[0-9a-f]{32}$")
        self.assertNotEqual(first["trace_id"], second["trace_id"])
        self.assertEqual(first["uids"], [])

        custom_uids = [f"765611980000000{i:02d}" for i in range(16)]
        normalized = players_kinematics.normalize_request(
            "read", TRACE_ID, 5, 256, 5, 1, custom_uids
        )
        self.assertEqual(
            normalized,
            {
                "mode": "read",
                "trace_id": TRACE_ID,
                "cursor": 5,
                "limit": 256,
                "sample_hz": 5,
                "max_samples": 1,
                "uids": custom_uids,
            },
        )
        for mode in sorted(players_kinematics.TRACE_MODES):
            with self.subTest(mode=mode):
                tid = "" if mode == "start" else TRACE_ID
                args = players_kinematics.normalize_request(
                    mode, tid, 0, 64, 10, 4096, ["76561198000000001"]
                )
                self.assertEqual(loopback.validate_command_args(COMMAND, args), (True, None))

    def test_t1_bad_request_values_name_their_argument(self) -> None:
        seventeen_uids = [f"uid-{i}" for i in range(17)]
        cases = (
            (("begin", "", 0, 64, 10, 4096, None), "bad_mode"),
            (("dump", TRACE_ID, 0, 64, 10, 4096, None), "bad_mode"),
            ((1, "", 0, 64, 10, 4096, None), "bad_mode"),
            (("start", TRACE_ID, 0, 64, 10, 4096, None), "bad_trace_id"),
            (("start", None, 0, 64, 10, 4096, None), "bad_trace_id"),
            (("read", "", 0, 64, 10, 4096, None), "bad_trace_id"),
            (("read", TRACE_ID.upper(), 0, 64, 10, 4096, None), "bad_trace_id"),
            (("read", TRACE_ID[:-1], 0, 64, 10, 4096, None), "bad_trace_id"),
            (("read", None, 0, 64, 10, 4096, None), "bad_trace_id"),
            (("read", TRACE_ID, -1, 64, 10, 4096, None), "bad_cursor"),
            (("read", TRACE_ID, True, 64, 10, 4096, None), "bad_cursor"),
            (("read", TRACE_ID, 0.0, 64, 10, 4096, None), "bad_cursor"),
            (("read", TRACE_ID, 0, 0, 10, 4096, None), "bad_limit"),
            (("read", TRACE_ID, 0, 257, 10, 4096, None), "bad_limit"),
            (("read", TRACE_ID, 0, True, 10, 4096, None), "bad_limit"),
            (("start", "", 0, 64, 4, 4096, None), "bad_sample_hz"),
            (("start", "", 0, 64, 11, 4096, None), "bad_sample_hz"),
            (("start", "", 0, 64, 10.0, 4096, None), "bad_sample_hz"),
            (("start", "", 0, 64, 10, 0, None), "bad_max_samples"),
            (("start", "", 0, 64, 10, 8193, None), "bad_max_samples"),
            (("start", "", 0, 64, 10, False, None), "bad_max_samples"),
            (("start", "", 0, 64, 10, 4096, "uid-1"), "bad_uids"),
            (("start", "", 0, 64, 10, 4096, ("uid-1",)), "bad_uids"),
            (("start", "", 0, 64, 10, 4096, 123), "bad_uids"),
            (("start", "", 0, 64, 10, 4096, seventeen_uids), "bad_uids"),
            (("start", "", 0, 64, 10, 4096, [""]), "bad_uids"),
            (("start", "", 0, 64, 10, 4096, ["   "]), "bad_uids"),
            (("start", "", 0, 64, 10, 4096, ["u1", "u1"]), "bad_uids"),
            (("start", "", 0, 64, 10, 4096, [123]), "bad_uids"),
        )
        for arguments, expected_code in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError) as raised:
                    players_kinematics.normalize_request(*arguments)
                self.assertEqual(str(raised.exception), expected_code)

    def test_t1_uid_max_chars_cap(self) -> None:
        # Kills M9: removing the length comparison from is_uids_list accepts > UID_MAX_CHARS.
        self.assertEqual(players_kinematics.UID_MAX_CHARS, 64)
        at_cap = "u" * players_kinematics.UID_MAX_CHARS
        over_cap = "u" * (players_kinematics.UID_MAX_CHARS + 1)

        self.assertTrue(players_kinematics.is_uids_list([at_cap]))
        normalized = players_kinematics.normalize_request(
            "start", "", 0, 64, 10, 4096, [at_cap]
        )
        self.assertEqual(normalized["uids"], [at_cap])
        self.assertEqual(
            loopback.validate_command_args(COMMAND, _wire_args(uids=[at_cap])),
            (True, None),
        )

        self.assertFalse(players_kinematics.is_uids_list([over_cap]))
        with self.assertRaises(ValueError) as raised:
            players_kinematics.normalize_request(
                "start", "", 0, 64, 10, 4096, [over_cap]
            )
        self.assertEqual(str(raised.exception), "bad_uids")
        self.assertEqual(
            loopback.validate_command_args(COMMAND, _wire_args(uids=[over_cap])),
            (False, "bad_args"),
        )

    def test_t2_ingress_lease_admission_and_live_profile(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))
        self.assertIn(COMMAND, loopback._CAPABILITY_ADMISSION_COMMANDS)

        # Missing or extra key is bad_args at ingress.
        for key in WIRE_KEYS:
            missing = {k: v for k, v in _wire_args().items() if k != key}
            with self.subTest(missing=key):
                self.assertEqual(
                    loopback.validate_command_args(COMMAND, missing),
                    (False, "bad_args"),
                )
        extra = _wire_args(uid="76561198000000001")
        self.assertEqual(
            loopback.validate_command_args(COMMAND, extra),
            (False, "bad_args"),
        )
        self.assertEqual(
            self.state.enqueue_command(COMMAND, extra, peer="server"),
            (400, {"error": "bad_args"}),
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
        self.assertLess(census.index("player_teleport"), census.index(COMMAND))
        self.assertLess(census.index(COMMAND), census.index("players_visibility"))

        branches = _server_dispatch_branches(server_src)
        self.assertIn(COMMAND, branches)
        dispatch = method_body(server_src, SIG_DISPATCH_CHAIN)
        _in_order(
            self,
            dispatch,
            'command.cmd == "players_kinematics"',
            "postNow = DispatchPlayersKinematics(command, result);",
            'result.error = "unknown_command";',
        )

        # Bit table append-only: prior 49 bits unchanged, bit 49 is players_kinematics.
        self.assertEqual(SERVER_CAPABILITY_BITS[:49], PREVIOUS_49_SERVER_BITS)
        self.assertEqual(SERVER_CAPABILITY_BITS[49:], (COMMAND, "players_los_cone", "loot_los_cone"))

        mask_matches = re.findall(r'SERVER_CAPABILITIES_MASK\s*=\s*"([^"]+)"', server_src)
        self.assertEqual(mask_matches, ["1.fffffffffffff"])
        self.assertEqual(encode_capability_mask("server", census), "1.fffffffffffff")
        decoded, reason = parse_capability_mask("server", mask_matches[0])
        self.assertEqual(reason, "ok")
        self.assertEqual(set(decoded or ()), set(census))
        self.assertEqual(set(decoded or ()), set(branches))

        # Client bridge neither implements nor announces players_kinematics.
        client_src = _source(CLIENT_BRIDGE_PATH)
        self.assertNotIn(COMMAND, client_src)
        self.assertNotIn("MCPPlayersKinematics", client_src)
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
            ("cursor", "limit", "max_samples", "mode", "sample_hz", "trace_id", "uids"),
        )
        self.assertEqual(server_arg_contract_hash(), NEW_ACH)
        self.assertEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, NEW_ACH)
        # Kills M5: MCPBridge.c must declare the updated ach hash.
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
    """T5-T11: KIND, publication envelope, masking, passthrough, page, header, vocab."""

    def test_t5_evidence_kind_contract(self) -> None:
        kind = players_kinematics.KIND
        self.assertEqual(kind.name, "players_kinematics")
        self.assertEqual(kind.gap_kinds, players_kinematics.GAP_KINDS)
        expected_fields = [
            ("pos", evidence.EvidenceField("vec3", "m")),
            ("expected_dt_s", evidence.EvidenceField("number", "s")),
            (
                "observed_dt_s",
                evidence.EvidenceField("number", "s", absent_with=frozenset({"first_sample"})),
            ),
            # Kills M6: vel must declare absent_with for first_sample and clock_not_monotonic.
            (
                "vel",
                evidence.EvidenceField(
                    "vec3",
                    "m/s",
                    absent_with=frozenset({"first_sample", "clock_not_monotonic"}),
                ),
            ),
            ("vel_engine", evidence.EvidenceField("vec3", "m/s", optional=True)),
            ("yaw_deg", evidence.EvidenceField("number", "deg", optional=True)),
            ("pitch_deg", evidence.EvidenceField("number", "deg", optional=True)),
            ("stance_idx", evidence.EvidenceField("int")),
            ("command", evidence.EvidenceField("token")),
            ("falling", evidence.EvidenceField("flag", optional=True)),
            ("jump", evidence.EvidenceField("flag", optional=True)),
            ("parachute", evidence.EvidenceField("flag", optional=True)),
            ("in_vehicle", evidence.EvidenceField("flag")),
            ("vehicle_type", evidence.EvidenceField("token")),
            ("vehicle_class", evidence.EvidenceField("token")),
        ]
        self.assertEqual(list(kind.fields.items()), expected_fields)

    def test_t6_published_read_envelope_and_header(self) -> None:
        row0 = _wire_row(
            uid="76561198000000001",
            tick_ms=10000,
            gap_kind="first_sample",
            pos=[10.0, 2.0, 30.0],
            expected_dt_s=0.1,
            observed_dt_s=0.0,
            vel=[0.0, 0.0, 0.0],
            vel_engine=[0.0, 0.0, 0.0],
            vel_engine_available=1,
            yaw_deg=45.0,
            yaw_deg_available=1,
            pitch_deg=15.0,
            pitch_deg_available=1,
            stance_idx=0,
            command="move",
            falling=0,
            falling_available=1,
            jump=1,
            jump_available=1,
            parachute=0,
            parachute_available=0,
            in_vehicle=0,
            vehicle_type="-",
            vehicle_class="none",
        )
        row1 = _wire_row(
            uid="76561198000000001",
            tick_ms=10100,
            gap_kind="",
            pos=[10.2, 2.0, 30.0],
            expected_dt_s=0.1,
            observed_dt_s=0.1,
            vel=[2.0, 0.0, 0.0],
            vel_engine=[1.9, 0.0, 0.0],
            vel_engine_available=1,
            yaw_deg=90.0,
            yaw_deg_available=1,
            pitch_deg=-5.0,
            pitch_deg_available=1,
            stance_idx=1,
            command="move",
            falling=0,
            falling_available=1,
            jump=0,
            jump_available=1,
            parachute=0,
            parachute_available=0,
            in_vehicle=0,
            vehicle_type="-",
            vehicle_class="none",
        )
        row2 = _wire_row(
            uid="76561198000000002",
            tick_ms=10100,
            gap_kind="first_sample",
            pos=[500.0, 10.0, 600.0],
            expected_dt_s=0.1,
            observed_dt_s=0.0,
            vel=[0.0, 0.0, 0.0],
            vel_engine=[99.0, 0.0, 0.0],
            vel_engine_available=0,
            yaw_deg=180.0,
            yaw_deg_available=1,
            pitch_deg=3.0,
            pitch_deg_available=1,
            stance_idx=0,
            command="vehicle",
            falling=1,
            falling_available=0,
            jump=1,
            jump_available=1,
            parachute=0,
            parachute_available=0,
            in_vehicle=1,
            vehicle_type="OffroadHatchback",
            vehicle_class="ground",
        )
        wire = {
            "ok": 1,
            FIELD: _wire_read(
                mode="read",
                rows=[row0, row1, row2],
                count=5,
                cursor=0,
                next_cursor=3,
                passes=2,
                uids=["76561198000000001", "76561198000000002"],
            ),
        }
        published = players_kinematics.normalize_bridge_result(wire, include_unverified=False)
        expected_header = {
            "schema": players_kinematics.SCHEMA,
            "mode": "read",
            "trace_id": TRACE_ID,
            "active": True,
            "overflow": False,
            "stop_reason": "",
            "sample_hz": 10,
            "interval_ms": 100,
            "late_after_ms": 150,
            "capacity": 4096,
            "count": 5,
            "passes": 2,
            "budget_skips": 0,
            "pass_cost_last_ticks": 420,
            "pass_cost_max_ticks": 420,
            "pass_cost_budget_ticks": 5000,
            "start_tick_ms": 9900,
            "now_tick_ms": 10050,
            "uids": ["76561198000000001", "76561198000000002"],
            "cursor": 0,
            "next_cursor": 3,
            "eof": False,
        }
        expected_evidence = {
            "schema": evidence.EVIDENCE_SCHEMA,
            "kind": "players_kinematics",
            "units": {
                "tick_ms": "ms",
                "pos": "m",
                "expected_dt_s": "s",
                "observed_dt_s": "s",
                "vel": "m/s",
                "vel_engine": "m/s",
                "yaw_deg": "deg",
                "pitch_deg": "deg",
            },
            "rows": [
                {
                    "uid": "76561198000000001",
                    "tick_ms": 10000,
                    "gap_kind": "first_sample",
                    "pos": [10.0, 2.0, 30.0],
                    "expected_dt_s": 0.1,
                    "vel_engine": [0.0, 0.0, 0.0],
                    "vel_engine_available": True,
                    "yaw_deg": 45.0,
                    "yaw_deg_available": True,
                    "pitch_deg_available": False,
                    "stance_idx": 0,
                    "command": "move",
                    "falling": False,
                    "falling_available": True,
                    "jump_available": False,
                    "parachute_available": False,
                    "in_vehicle": False,
                    "vehicle_type": "-",
                    "vehicle_class": "none",
                },
                {
                    "uid": "76561198000000001",
                    "tick_ms": 10100,
                    "gap_kind": "",
                    "pos": [10.2, 2.0, 30.0],
                    "expected_dt_s": 0.1,
                    "observed_dt_s": 0.1,
                    "vel": [2.0, 0.0, 0.0],
                    "vel_engine": [1.9, 0.0, 0.0],
                    "vel_engine_available": True,
                    "yaw_deg": 90.0,
                    "yaw_deg_available": True,
                    "pitch_deg_available": False,
                    "stance_idx": 1,
                    "command": "move",
                    "falling": False,
                    "falling_available": True,
                    "jump_available": False,
                    "parachute_available": False,
                    "in_vehicle": False,
                    "vehicle_type": "-",
                    "vehicle_class": "none",
                },
                {
                    "uid": "76561198000000002",
                    "tick_ms": 10100,
                    "gap_kind": "first_sample",
                    "pos": [500.0, 10.0, 600.0],
                    "expected_dt_s": 0.1,
                    "vel_engine_available": False,
                    "yaw_deg": 180.0,
                    "yaw_deg_available": True,
                    "pitch_deg_available": False,
                    "stance_idx": 0,
                    "command": "vehicle",
                    "falling_available": False,
                    "jump_available": False,
                    "parachute_available": False,
                    "in_vehicle": True,
                    "vehicle_type": "OffroadHatchback",
                    "vehicle_class": "ground",
                },
            ],
            "truncated": True,
        }
        self.assertEqual(published[FIELD], expected_header)
        self.assertEqual(published["evidence"], expected_evidence)

    def test_t7_unverified_fields_masked_by_default_and_preserved_with_flag(self) -> None:
        # Kills M2: removing UNVERIFIED_FIELDS masking exposes pitch_deg / jump.
        self.assertEqual(players_kinematics.UNVERIFIED_FIELDS, ("pitch_deg", "jump"))
        wire_row = _wire_row(
            pitch_deg=-22.5,
            pitch_deg_available=1,
            jump=1,
            jump_available=1,
        )
        wire = {"ok": 1, FIELD: _wire_read(rows=[wire_row])}
        snapshot = copy.deepcopy(wire)

        masked = players_kinematics.normalize_bridge_result(wire)
        row_masked = masked["evidence"]["rows"][0]
        for field in players_kinematics.UNVERIFIED_FIELDS:
            self.assertIs(row_masked[f"{field}_available"], False)
            self.assertNotIn(field, row_masked)
        # Wire input and wire row are never mutated.
        self.assertEqual(wire, snapshot)

        unmasked = players_kinematics.normalize_bridge_result(
            wire, include_unverified=True
        )
        row_unmasked = unmasked["evidence"]["rows"][0]
        self.assertIs(row_unmasked["pitch_deg_available"], True)
        self.assertEqual(row_unmasked["pitch_deg"], -22.5)
        self.assertIs(row_unmasked["jump_available"], True)
        self.assertIs(row_unmasked["jump"], True)
        self.assertEqual(wire, snapshot)

    def test_t8_nothing_filtered_or_reordered_across_extremes_and_gaps(self) -> None:
        rows = [
            _wire_row(
                uid="u1",
                tick_ms=0,
                gap_kind="first_sample",
                pos=[-1e9, -500.0, 1e9],
                expected_dt_s=0.1,
            ),
            _wire_row(
                uid="u1",
                tick_ms=0,
                gap_kind="clock_not_monotonic",
                pos=[0.0, 0.0, 0.0],
                observed_dt_s=-5.0,
            ),
            _wire_row(
                uid="u1",
                tick_ms=500,
                gap_kind="subject_absent",
                pos=[1e6, 99999.0, -1e6],
                observed_dt_s=0.5,
                vel=[2e6, 2e5, -2e6],
            ),
            _wire_row(
                uid="u1",
                tick_ms=900,
                gap_kind="pass_late",
                pos=[0.0001, -0.0001, 0.0],
                observed_dt_s=0.4,
                vel=[-99999.0, 0.0, 99999.0],
            ),
            _wire_row(
                uid="u1",
                tick_ms=1000,
                gap_kind="",
                pos=[50000.0, 50000.0, 50000.0],
                observed_dt_s=0.1,
                vel=[500000.0, 500000.0, 500000.0],
            ),
        ]
        out = players_kinematics.normalize_bridge_result(
            {"ok": 1, FIELD: _wire_read(rows=rows, count=5, next_cursor=5)}
        )
        published = out["evidence"]["rows"]
        self.assertEqual(len(published), 5)
        self.assertEqual(
            [r["gap_kind"] for r in published],
            ["first_sample", "clock_not_monotonic", "subject_absent", "pass_late", ""],
        )
        self.assertEqual(published[0]["pos"], [-1e9, -500.0, 1e9])
        self.assertNotIn("observed_dt_s", published[0])
        self.assertNotIn("vel", published[0])
        self.assertEqual(published[1]["observed_dt_s"], -5.0)
        self.assertNotIn("vel", published[1])
        self.assertEqual(published[2]["vel"], [2e6, 2e5, -2e6])
        self.assertEqual(published[3]["pos"], [0.0001, -0.0001, 0.0])
        self.assertEqual(published[4]["vel"], [500000.0, 500000.0, 500000.0])

    def test_t9_page_invariants_and_truncated(self) -> None:
        # Kills M3: truncated must be True when next_cursor < count, False at end.
        partial = players_kinematics.normalize_bridge_result(
            {"ok": 1, FIELD: _wire_read(rows=[_wire_row()], count=3, cursor=0, next_cursor=1)}
        )
        self.assertIs(partial["evidence"]["truncated"], True)

        final = players_kinematics.normalize_bridge_result(
            {"ok": 1, FIELD: _wire_read(rows=[_wire_row()], count=3, cursor=2, next_cursor=3)}
        )
        self.assertIs(final["evidence"]["truncated"], False)

        # Outside read, no evidence member is published and rows are omitted.
        for mode in sorted(players_kinematics.TRACE_MODES - {"read"}):
            with self.subTest(mode=mode):
                out = players_kinematics.normalize_bridge_result(
                    {"ok": 1, FIELD: _wire_read(mode=mode, rows=[], count=3, cursor=0, next_cursor=0)}
                )
                self.assertNotIn("evidence", out)
                self.assertNotIn("rows", out[FIELD])

        # Broken page invariants rejected with their member.
        broken = (
            ("count_negative", _wire_read(rows=[], count=-1, cursor=0, next_cursor=0), "count"),
            ("count_above_capacity", _wire_read(rows=[], capacity=2, count=3, cursor=0, next_cursor=0), "count"),
            ("cursor_negative", _wire_read(rows=[], count=2, cursor=-1, next_cursor=0), "cursor"),
            ("cursor_above_count", _wire_read(rows=[], count=2, cursor=3, next_cursor=3), "cursor"),
            ("next_cursor_before_cursor", _wire_read(rows=[], count=2, cursor=2, next_cursor=1), "next_cursor"),
            ("next_cursor_above_count", _wire_read(rows=[_wire_row()], count=1, cursor=0, next_cursor=2), "next_cursor"),
            ("rows_len_mismatch", _wire_read(rows=[_wire_row()], count=3, cursor=0, next_cursor=2), "rows"),
            ("rows_present_in_status", _wire_read(mode="status", rows=[_wire_row()], count=1, cursor=0, next_cursor=1), "rows"),
            ("next_cursor_moved_in_status", _wire_read(mode="status", rows=[], count=2, cursor=0, next_cursor=1), "next_cursor"),
        )
        for label, header, member in broken:
            with self.subTest(case=label):
                with self.assertRaises(ValueError) as raised:
                    players_kinematics.normalize_bridge_result({"ok": 1, FIELD: header})
                self.assertEqual(
                    str(raised.exception),
                    f"bad_bridge_kinematics_result: players_kinematics.{member}",
                )

    def test_t10_header_validation_and_booleans(self) -> None:
        # Container errors.
        for bad_result, where in (
            (None, "result"),
            ([], "result"),
            ({"ok": 1}, "players_kinematics"),
            ({"ok": 1, FIELD: []}, "players_kinematics"),
            ({"ok": 1, FIELD: {k: v for k, v in _wire_read().items() if k != "rows"}}, "players_kinematics.rows"),
            ({"ok": 1, FIELD: _wire_read(rows={})}, "players_kinematics.rows"),
            ({"ok": 1, FIELD: _wire_read(rows=None)}, "players_kinematics.rows"),
        ):
            with self.subTest(where=where):
                with self.assertRaises(ValueError) as raised:
                    players_kinematics.normalize_bridge_result(bad_result)
                self.assertEqual(
                    str(raised.exception), f"bad_bridge_kinematics_result: {where}"
                )

        # Header booleans accept 0/1 and bool; anything else or missing is bad_bridge_kinematics_boolean.
        typed = players_kinematics.normalize_bridge_result(
            {"ok": 1, FIELD: _wire_read(active=0, overflow=1, eof=True)}
        )
        self.assertIs(typed[FIELD]["active"], False)
        self.assertIs(typed[FIELD]["overflow"], True)
        self.assertIs(typed[FIELD]["eof"], True)
        for bool_field in ("active", "overflow", "eof"):
            missing = _wire_read()
            del missing[bool_field]
            with self.subTest(missing=bool_field):
                with self.assertRaises(ValueError) as raised:
                    players_kinematics.normalize_bridge_result({"ok": 1, FIELD: missing})
                self.assertEqual(
                    str(raised.exception),
                    f"bad_bridge_kinematics_boolean: players_kinematics.{bool_field}",
                )
            for bad_bool in (2, -1, "1", None, 0.5):
                with self.subTest(field=bool_field, bad=bad_bool):
                    with self.assertRaises(ValueError) as raised:
                        players_kinematics.normalize_bridge_result(
                            {"ok": 1, FIELD: _wire_read(**{bool_field: bad_bool})}
                        )
                    self.assertEqual(
                        str(raised.exception),
                        f"bad_bridge_kinematics_boolean: players_kinematics.{bool_field}",
                    )

        # Every non-boolean header member missing or bad value names its member.
        header_cases = (
            ("schema", "dayz-mcp-players-kinematics-v2"),
            ("mode", "dump"),
            ("trace_id", TRACE_ID.upper()),
            ("stop_reason", "crashed"),
            ("sample_hz", 4),
            ("interval_ms", 99),
            ("late_after_ms", 151),
            ("capacity", 0),
            ("count", "1"),
            ("passes", -1),
            ("budget_skips", -1),
            ("pass_cost_last_ticks", -1),
            ("pass_cost_max_ticks", "0"),
            ("pass_cost_budget_ticks", 4999),
            ("start_tick_ms", -1),
            ("now_tick_ms", 10.5),
            ("uids", [""]),
            ("cursor", True),
            ("next_cursor", 1.5),
        )
        for member, bad_val in header_cases:
            with self.subTest(bad_member=member):
                with self.assertRaises(ValueError) as raised:
                    players_kinematics.normalize_bridge_result(
                        {"ok": 1, FIELD: _wire_read(**{member: bad_val})}
                    )
                self.assertEqual(
                    str(raised.exception),
                    f"bad_bridge_kinematics_result: players_kinematics.{member}",
                )
            missing_hdr = _wire_read()
            del missing_hdr[member]
            with self.subTest(missing_member=member):
                with self.assertRaises(ValueError) as raised:
                    players_kinematics.normalize_bridge_result({"ok": 1, FIELD: missing_hdr})
                self.assertEqual(
                    str(raised.exception),
                    f"bad_bridge_kinematics_result: players_kinematics.{member}",
                )

    def test_t11_vocabulary_checks(self) -> None:
        with self.assertRaises(ValueError) as raised:
            players_kinematics.normalize_bridge_result(
                {"ok": 1, FIELD: _wire_read(rows=[_wire_row(command="fly")])}
            )
        self.assertEqual(
            str(raised.exception),
            "bad_bridge_kinematics_result: players_kinematics.rows[0].command",
        )

        with self.assertRaises(ValueError) as raised:
            players_kinematics.normalize_bridge_result(
                {"ok": 1, FIELD: _wire_read(rows=[_wire_row(vehicle_class="tank")])}
            )
        self.assertEqual(
            str(raised.exception),
            "bad_bridge_kinematics_result: players_kinematics.rows[0].vehicle_class",
        )

        with self.assertRaises(ValueError) as raised:
            players_kinematics.normalize_bridge_result(
                {"ok": 1, FIELD: _wire_read(rows=[_wire_row(gap_kind="teleport")])}
            )
        self.assertEqual(str(raised.exception), "bad_evidence_row: rows[0].gap_kind")


class TranslatedEnforceBodyTest(unittest.TestCase):
    """T12 (GapKind), T13 (Tick), T15 (VehicleClass), T16 (Pass) on real Enforce bodies."""

    def setUp(self) -> None:
        self.source = _source(SAMPLER_PATH)

    def test_t12_gap_kind_translated(self) -> None:
        # Signature has no position parameter.
        self.assertNotIn("pos", SIG_GAP_KIND.lower())
        ns: dict[str, object] = {}
        gap_kind = translate(self.source, SIG_GAP_KIND, ns)

        # No previous sample -> first_sample (wins over every other condition).
        self.assertEqual(gap_kind(False, 5000, 1000, 1, 9, 150).text, "first_sample")
        # Equal or backward tick -> clock_not_monotonic (wins over subject_absent and pass_late).
        self.assertEqual(gap_kind(True, 1000, 1000, 1, 5, 150).text, "clock_not_monotonic")
        self.assertEqual(gap_kind(True, 1000, 999, 1, 5, 150).text, "clock_not_monotonic")
        # Missed pass -> subject_absent (wins over pass_late; kills M1).
        self.assertEqual(gap_kind(True, 1000, 1100, 1, 3, 150).text, "subject_absent")
        self.assertEqual(gap_kind(True, 1000, 1500, 1, 3, 150).text, "subject_absent")
        # dt == lateAfterMs -> no gap; dt == lateAfterMs + 1 -> pass_late.
        self.assertEqual(gap_kind(True, 1000, 1150, 1, 2, 150).text, "")
        self.assertEqual(gap_kind(True, 1000, 1151, 1, 2, 150).text, "pass_late")
        # Normal cadence -> "".
        self.assertEqual(gap_kind(True, 1000, 1100, 1, 2, 150).text, "")

    def test_t13_tick_schedule_and_budget_translated(self) -> None:
        game_calls = 0
        clock_ms = [1000]
        pass_calls: list[int] = []
        tick_cost = [400]

        class _FakeGame:
            def GetTime(self) -> int:
                return clock_ms[0]

        def fake_get_game():
            nonlocal game_calls
            game_calls += 1
            return _FakeGame()

        def fake_tick_count(prev: int) -> int:
            if prev == 0:
                return 100
            return tick_cost[0]

        def fake_pass(now_ms: int) -> None:
            pass_calls.append(now_ms)

        ns: dict[str, object] = {
            "GetGame": fake_get_game,
            "TickCount": fake_tick_count,
            "Pass": fake_pass,
            "PASS_BUDGET_TICKS": players_kinematics.PASS_BUDGET_TICKS,
            "s_Active": False,
            "s_HasGrid": False,
            "s_LastTickMs": 0,
            "s_NextDueMs": 0,
            "s_IntervalMs": 100,
            "s_SkipNextPass": False,
            "s_BudgetSkips": 0,
            "s_PassCostLastTicks": 0,
            "s_PassCostMaxTicks": 0,
        }
        tick = translate(
            self.source,
            SIG_TICK,
            ns,
            members=(
                "s_Active",
                "s_HasGrid",
                "s_LastTickMs",
                "s_NextDueMs",
                "s_IntervalMs",
                "s_SkipNextPass",
                "s_BudgetSkips",
                "s_PassCostLastTicks",
                "s_PassCostMaxTicks",
            ),
        )

        # Idle: must not call GetGame().
        tick()
        self.assertEqual(game_calls, 0)
        self.assertEqual(pass_calls, [])

        # Activate: first Tick samples immediately and sets grid to 1100.
        ns["s_Active"] = True
        clock_ms[0] = 1000
        tick()
        self.assertEqual(pass_calls, [1000])
        self.assertEqual(ns["s_NextDueMs"], 1100)
        self.assertEqual(ns["s_PassCostLastTicks"], 400)
        self.assertEqual(ns["s_PassCostMaxTicks"], 400)

        # Before due time: no pass.
        clock_ms[0] = 1050
        tick()
        clock_ms[0] = 1099
        tick()
        self.assertEqual(pass_calls, [1000])

        # Slightly late (1120, < 1100 + 100): samples and keeps grid at 1200.
        clock_ms[0] = 1120
        tick()
        self.assertEqual(pass_calls, [1000, 1120])
        self.assertEqual(ns["s_NextDueMs"], 1200)

        # A full interval or more late (1350 >= 1200 + 100): restarts grid at 1450.
        clock_ms[0] = 1350
        tick()
        self.assertEqual(pass_calls, [1000, 1120, 1350])
        self.assertEqual(ns["s_NextDueMs"], 1450)

        # Backward clock (500 < 1350): resets grid and samples immediately.
        clock_ms[0] = 500
        tick()
        self.assertEqual(pass_calls, [1000, 1120, 1350, 500])
        self.assertEqual(ns["s_NextDueMs"], 600)

        # Pass exceeding PASS_BUDGET_TICKS marks skip for next due pass (kills M8).
        tick_cost[0] = 6000
        clock_ms[0] = 600
        tick()
        self.assertEqual(pass_calls, [1000, 1120, 1350, 500, 600])
        self.assertIs(ns["s_SkipNextPass"], True)
        self.assertEqual(ns["s_PassCostLastTicks"], 6000)
        self.assertEqual(ns["s_PassCostMaxTicks"], 6000)
        self.assertEqual(ns["s_BudgetSkips"], 0)

        # Non-due frame does not consume the skip.
        clock_ms[0] = 650
        tick()
        self.assertIs(ns["s_SkipNextPass"], True)
        self.assertEqual(ns["s_BudgetSkips"], 0)

        # Next due pass (700) is skipped and counted.
        tick_cost[0] = 300
        clock_ms[0] = 700
        tick()
        self.assertEqual(pass_calls, [1000, 1120, 1350, 500, 600])
        self.assertIs(ns["s_SkipNextPass"], False)
        self.assertEqual(ns["s_BudgetSkips"], 1)
        self.assertEqual(ns["s_NextDueMs"], 800)

        # Following due pass (800) runs normally.
        clock_ms[0] = 800
        tick()
        self.assertEqual(pass_calls, [1000, 1120, 1350, 500, 600, 800])
        self.assertEqual(ns["s_PassCostLastTicks"], 300)
        self.assertEqual(ns["s_PassCostMaxTicks"], 6000)

        # Negative TickCount is discarded.
        tick_cost[0] = -10
        clock_ms[0] = 900
        tick()
        self.assertEqual(pass_calls, [1000, 1120, 1350, 500, 600, 800, 900])
        self.assertEqual(ns["s_PassCostLastTicks"], 300)
        self.assertIs(ns["s_SkipNextPass"], False)

    def test_t15_vehicle_class_translated(self) -> None:
        class _Transport:
            pass

        class _Helicopter(_Transport):
            pass

        class _Car(_Transport):
            pass

        class _Motorbike(_Transport):
            pass

        class _Boat(_Transport):
            pass

        class _OtherTransport(_Transport):
            pass

        def _caster(cls: type):
            return SimpleNamespace(Cast=lambda obj: obj if isinstance(obj, cls) else None)

        ns = {
            "Helicopter": _caster(_Helicopter),
            "Car": _caster(_Car),
            "Motorbike": _caster(_Motorbike),
            "Boat": _caster(_Boat),
        }
        vehicle_class = translate(self.source, SIG_VEHICLE_CLASS, ns)
        self.assertEqual(vehicle_class(None).text, "none")
        self.assertEqual(vehicle_class(_Helicopter()).text, "aircraft")
        self.assertEqual(vehicle_class(_Car()).text, "ground")
        self.assertEqual(vehicle_class(_Motorbike()).text, "ground")
        self.assertEqual(vehicle_class(_Boat()).text, "water")
        self.assertEqual(vehicle_class(_OtherTransport()).text, "unknown")

    def test_t16_pass_filters_and_overflow_translated(self) -> None:
        class _Identity:
            def __init__(self, plain_id: str) -> None:
                self._id = plain_id

            def GetPlainId(self) -> EnforceString:
                return EnforceString.of(self._id)

        class _Player:
            def __init__(self, alive: bool, identity: _Identity | None) -> None:
                self._alive = alive
                self._identity = identity

            def IsAlive(self) -> bool:
                return self._alive

            def GetIdentity(self) -> _Identity | None:
                return self._identity

        non_player = object()
        dead_player = _Player(False, _Identity("uid-dead"))
        no_identity_player = _Player(True, None)
        empty_uid_player = _Player(True, _Identity(""))
        unlisted_player = _Player(True, _Identity("uid-other"))
        wanted_a = _Player(True, _Identity("uid-a"))
        wanted_b = _Player(True, _Identity("uid-b"))

        world_players: list[object] = [
            non_player,
            dead_player,
            no_identity_player,
            empty_uid_player,
            unlisted_player,
            wanted_a,
            wanted_b,
        ]
        captured: list[tuple[object, str, int]] = []

        class _Game:
            def GetPlayers(self, out_arr: EnforceArray) -> None:
                for p in world_players:
                    out_arr.Insert(p)

        def fake_capture(player: object, uid: EnforceString, now_ms: int) -> None:
            captured.append((player, uid.text, now_ms))
            ns["s_Count"] = int(ns["s_Count"]) + 1  # type: ignore[arg-type]

        ns: dict[str, object] = {
            "GetGame": lambda: _Game(),
            "PlayerBase": SimpleNamespace(
                Cast=lambda obj: obj if isinstance(obj, _Player) else None
            ),
            "Capture": fake_capture,
            "s_Active": True,
            "s_Overflow": False,
            "s_StopReason": EnforceString.of(""),
            "s_Passes": 0,
            "s_Count": 0,
            "s_Capacity": 10,
            "s_Players": EnforceArray(),
            "s_Uids": EnforceArray([EnforceString.of("uid-a"), EnforceString.of("uid-b")]),
        }
        run_pass = translate(
            self.source,
            SIG_PASS,
            ns,
            class_types=("PlayerBase", "PlayerIdentity"),
            members=(
                "s_Active",
                "s_Overflow",
                "s_StopReason",
                "s_Passes",
                "s_Count",
                "s_Capacity",
                "s_Players",
                "s_Uids",
            ),
        )

        # Kills M4: dead_player is skipped along with non-PlayerBase, no-identity, empty-uid, unlisted.
        run_pass(1000)
        self.assertEqual(ns["s_Passes"], 1)
        self.assertEqual([(u, t) for _p, u, t in captured], [("uid-a", 1000), ("uid-b", 1000)])

        # Empty s_Uids samples every living PlayerBase with a non-empty plain id.
        captured.clear()
        ns["s_Uids"] = EnforceArray()
        ns["s_Count"] = 0
        run_pass(1050)
        self.assertEqual(ns["s_Passes"], 2)
        self.assertEqual(
            [(u, t) for _p, u, t in captured],
            [("uid-other", 1050), ("uid-a", 1050), ("uid-b", 1050)],
        )

        # Full buffer stops with overflow without writing (kills M7).
        captured.clear()
        ns["s_Uids"] = EnforceArray([EnforceString.of("uid-a"), EnforceString.of("uid-b")])
        ns["s_Capacity"] = 1
        ns["s_Count"] = 0
        ns["s_Active"] = True
        ns["s_Overflow"] = False
        ns["s_StopReason"] = EnforceString.of("")
        run_pass(1100)
        self.assertEqual([(u, t) for _p, u, t in captured], [("uid-a", 1100)])
        self.assertIs(ns["s_Overflow"], True)
        self.assertIs(ns["s_Active"], False)
        self.assertEqual(ns["s_StopReason"].text, "overflow")  # type: ignore[union-attr]
        self.assertEqual(ns["s_Count"], 1)

        # Already full before first player: writes nothing and overflows immediately.
        captured.clear()
        ns["s_Active"] = True
        ns["s_Overflow"] = False
        ns["s_StopReason"] = EnforceString.of("")
        run_pass(1200)
        self.assertEqual(captured, [])
        self.assertIs(ns["s_Overflow"], True)
        self.assertIs(ns["s_Active"], False)
        self.assertEqual(ns["s_StopReason"].text, "overflow")  # type: ignore[union-attr]


class EnforceSourceContractTest(unittest.TestCase):
    """T14, T17, T18, T19, T22, T23: reads, lifecycle, hooks, messages, mirrors, hazards."""

    def setUp(self) -> None:
        self.sampler = _source(SAMPLER_PATH)
        self.sampler_code = clean(self.sampler)
        self.bridge = _source(SERVER_BRIDGE_PATH)
        self.mission = _source(MISSION_SERVER_PATH)
        self.messages = _source(MESSAGES_PATH)

    def test_t14_engine_reads_on_foot_guard_and_no_world_writes(self) -> None:
        capture = clean(method_body(self.sampler, SIG_CAPTURE))
        _in_order(
            self,
            capture,
            "row = s_Rows.Get(s_Count);",
            "row.uid = uid;",
            "row.tick_ms = nowMs;",
            "position = player.GetPosition();",
            "row.pos.Set(0, position[0]);",
            "row.pos.Set(1, position[1]);",
            "row.pos.Set(2, position[2]);",
            "row.expected_dt_s = s_IntervalMs * 0.001;",
            "prev = s_PrevByUid.Get(uid);",
            "prevTickMs = prev.tick_ms;",
            "prevPass = prev.pass_index;",
            "prevPos = prev.pos;",
            "row.gap_kind = GapKind(hasPrev, prevTickMs, nowMs, prevPass, s_Passes, s_LateAfterMs);",
            "dtS = (nowMs - prevTickMs) * 0.001;",
            "row.observed_dt_s = dtS;",
            "if (dtS > 0.0)",
            "row.vel.Set(0, (position[0] - prevPos[0]) / dtS);",
            "row.vel.Set(1, (position[1] - prevPos[1]) / dtS);",
            "row.vel.Set(2, (position[2] - prevPos[2]) / dtS);",
            "vehicleCmd = player.GetCommand_Vehicle();",
            "transport = vehicleCmd.GetTransport();",
            "transport = Transport.Cast(player.GetParent());",
            "row.vel_engine_available = false;",
            "row.falling_available = false;",
            "if (transport)",
            "row.in_vehicle = true;",
            "vehType = transport.GetType();",
            "vehType = transport.ClassName();",
            "row.vehicle_type = vehType;",
            "row.vehicle_class = VehicleClass(transport);",
            "else",
            "row.in_vehicle = false;",
            'row.vehicle_type = "-";',
            "row.vehicle_class = VehicleClass(null);",
            "player.PhysicsGetVelocity(velocity);",
            "row.vel_engine.Set(0, velocity[0]);",
            "row.vel_engine.Set(1, velocity[1]);",
            "row.vel_engine.Set(2, velocity[2]);",
            "row.vel_engine_available = true;",
            "row.falling = player.PhysicsIsFalling(false);",
            "row.falling_available = true;",
            "hic = player.GetInputController();",
            "row.yaw_deg = MCPPlayerTrace.CompassDeg(hic.GetHeadingAngle());",
            "row.yaw_deg_available = true;",
            "weaponCmd = player.GetCommandModifier_Weapons();",
            "row.pitch_deg = weaponCmd.GetBaseAimingAngleUD();",
            "row.pitch_deg_available = true;",
            "player.GetMovementState(s_MovementState);",
            "row.stance_idx = s_MovementState.m_iStanceIdx;",
            "row.command = MCPPlayerTrace.CommandName(s_MovementState.m_CommandTypeId);",
            "row.jump = player.IsJumpInProgress();",
            "row.jump_available = true;",
            "row.parachute = false;",
            "row.parachute_available = false;",
            "prev.tick_ms = nowMs;",
            "prev.pos = position;",
            "prev.pass_index = s_Passes;",
            "s_Count = s_Count + 1;",
        )
        # Each vector copy writes its three components and nothing overrides them later.
        self.assertEqual(capture.count("row.pos.Set("), 3)
        self.assertEqual(capture.count("row.vel.Set("), 6)
        self.assertEqual(capture.count("row.vel_engine.Set("), 6)

        # Kills M10: pin exact assignment counts for every scalar row member in Capture
        # so an extra line clobbering a scalar after its engine read fails T14.
        expected_scalar_assignments = {
            "uid": 1,
            "tick_ms": 1,
            "gap_kind": 1,
            "expected_dt_s": 1,
            "observed_dt_s": 2,
            "vel_engine_available": 2,
            "yaw_deg": 2,
            "yaw_deg_available": 2,
            "pitch_deg": 2,
            "pitch_deg_available": 2,
            "stance_idx": 1,
            "command": 1,
            "falling": 2,
            "falling_available": 2,
            "jump": 1,
            "jump_available": 1,
            "parachute": 1,
            "parachute_available": 1,
            "in_vehicle": 2,
            "vehicle_type": 2,
            "vehicle_class": 2,
        }
        self.assertEqual(
            set(expected_scalar_assignments),
            {
                name
                for type_, name in EXPECTED_ROW_MEMBERS
                if not type_.startswith("ref array")
            },
        )
        actual_scalar_assignments = dict(
            Counter(re.findall(r"\brow\.(\w+)\s*(?:[+\-*/]?=(?!=)|\+\+|--)", capture))
        )
        self.assertEqual(actual_scalar_assignments, expected_scalar_assignments)

        # PhysicsIsFalling appears once and only with false (never true).
        self.assertEqual(self.sampler_code.count("PhysicsIsFalling("), 1)
        self.assertNotIn("PhysicsIsFalling(true)", self.sampler_code)
        # On-foot reads live strictly in the else (no transport) branch.
        in_veh_branch = capture[capture.index("if (transport)") : capture.index("else")]
        self.assertNotIn("PhysicsGetVelocity", in_veh_branch)
        self.assertNotIn("PhysicsIsFalling", in_veh_branch)

        # No raycasts, no world writes, no GetTickTime, no GetHealth.
        for banned in (
            "Raycast",
            "RaycastRV",
            "RaycastRVProxy",
            "SetPosition",
            "SetTransform",
            "CreateObject",
            "DeleteObject",
            "SetHealth",
            "OverrideMovement",
            "OverrideHeading",
            "StartCommand",
            "GetTickTime",
            "GetHealth",
            "OpenFile",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.sampler_code)

    def test_t17_lifecycle_methods_and_declared_statics(self) -> None:
        start = method_body(self.sampler, SIG_START)
        _in_order(
            self,
            start,
            'if (s_TraceId != "")',
            's_LastError = "trace_exists";',
            "if (traceId == \"\" || sampleHz < SAMPLE_HZ_MIN || sampleHz > SAMPLE_HZ_MAX || maxSamples < MAX_SAMPLES_MIN || maxSamples > MAX_SAMPLES_MAX)",
            "if (uids.Count() > UIDS_MAX)",
            "s_Rows = new array<ref MCPPlayersKinematicsRow>();",
            "s_Rows.Resize(maxSamples);",
            "row.pos.Resize(3);",
            "row.vel.Resize(3);",
            "row.vel_engine.Resize(3);",
            "s_IntervalMs = 1000 / sampleHz;",
            "s_LateAfterMs = s_IntervalMs + (s_IntervalMs / 2);",
            "s_Active = true;",
        )

        stop = method_body(self.sampler, SIG_STOP)
        _in_order(
            self,
            stop,
            "if (!Matches(traceId))",
            's_LastError = "trace_not_found";',
            "if (!s_Active)",
            "return true;",
            "s_Active = false;",
            's_StopReason = "requested";',
        )

        clear = method_body(self.sampler, SIG_CLEAR)
        _in_order(
            self,
            clear,
            "if (!Matches(traceId))",
            "if (s_Active)",
            's_LastError = "trace_active";',
            "ClearState();",
        )

        abort = method_body(self.sampler, SIG_ABORT)
        _in_order(self, abort, 'if (s_TraceId == "")', "ClearState();", "s_LastError = reason;")

        view = method_body(self.sampler, SIG_VIEW)
        _in_order(
            self,
            view,
            "if (!Matches(traceId))",
            "if (cursor < 0 || cursor > s_Count || limit < 1 || limit > LIMIT_MAX)",
            "view.now_tick_ms = GetGame().GetTime();",
            'if (mode == "read")',
            "end = cursor + limit;",
            "if (end > s_Count)",
            "view.rows.Insert(s_Rows.Get(index));",
            "view.next_cursor = end;",
            "view.eof = !s_Active && end == s_Count;",
        )
        assigned = set(re.findall(r"\bview\.(\w+)\s*=", view))
        self.assertEqual(
            assigned,
            {name for _type, name in EXPECTED_READ_MEMBERS} - {"uids", "rows"},
        )

        # Every s_* static used in the sampler is declared.
        declared = set(
            re.findall(
                r"\bstatic\s+(?:const\s+)?(?:ref\s+)?[\w<>, ]+?\s+(s_\w+)\s*;",
                self.sampler_code,
            )
        )
        used = set(re.findall(r"\b(s_\w+)\b", self.sampler_code))
        self.assertEqual(used - declared, set())

    def test_t18_mission_and_bridge_hooks(self) -> None:
        on_update = method_body(self.mission, SIG_ON_UPDATE)
        _in_order(
            self,
            on_update,
            "MCPGodmode.Tick(timeslice);",
            "MCPPlayersKinematics.Tick();",
        )
        every_script = "".join(
            clean(path.read_text(encoding="utf-8")) for path in SCRIPTS.rglob("*.c")
        )
        self.assertEqual(every_script.count("MCPPlayersKinematics.Tick()"), 1)

        shutdown = method_body(self.bridge, SIG_SHUTDOWN)
        self.assertIn('MCPPlayersKinematics.Abort("shutdown");', shutdown)

        lease_close = method_body(self.bridge, SIG_LEASE_CLOSE)
        _in_order(
            self,
            lease_close,
            'MCPPlayersKinematics.Abort("lease_close");',
            "BuildLeaseCloseCensus(command.args);",
        )

        census = method_body(self.bridge, SIG_LEASE_CENSUS)
        self.assertIn(
            'AddCloseEntry(census, "samplers", MCPPlayersKinematics.IsActive(), "samplers");',
            census,
        )

        dispatch = method_body(self.bridge, SIG_DISPATCH)
        self.assertEqual(
            set(re.findall(r'result\.error = "(\w+)"', dispatch)),
            {"bad_args", "bad_mode", "trace_not_found"},
        )
        self.assertNotIn("return false;", dispatch)
        _in_order(
            self,
            dispatch,
            "if (!command.args)",
            'args.mode != "start" && args.mode != "status" && args.mode != "stop" && args.mode != "read" && args.mode != "clear"',
            "MCPPlayersKinematics.LIMIT_MAX",
            "MCPPlayersKinematics.SAMPLE_HZ_MIN",
            "MCPPlayersKinematics.UIDS_MAX",
            'if (args.mode == "start")',
            "MCPPlayersKinematics.Start(args.trace_id, args.sample_hz, args.max_samples, args.uids)",
            "if (!MCPPlayersKinematics.Matches(args.trace_id))",
            'if (args.mode == "stop")',
            'else if (args.mode == "clear")',
            "MCPPlayersKinematics.View(args.mode, args.trace_id, args.cursor, args.limit)",
        )

    def test_t19_messages_members_and_result_prune(self) -> None:
        self.assertEqual(
            class_members(self.sampler, "MCPPlayersKinematicsRow"),
            EXPECTED_ROW_MEMBERS,
        )
        self.assertEqual(
            class_members(self.sampler, "MCPPlayersKinematicsRead"),
            EXPECTED_READ_MEMBERS,
        )
        self.assertEqual(
            _flat(method_body(self.sampler, "void MCPPlayersKinematicsRow()")),
            "pos = new array<float>(); vel = new array<float>(); vel_engine = new array<float>();",
        )
        self.assertEqual(
            _flat(method_body(self.sampler, "void MCPPlayersKinematicsRead()")),
            "uids = new array<string>(); rows = new array<ref MCPPlayersKinematicsRow>();",
        )

        args_members = class_members(self.messages, "MCPArgs")
        for member in (
            ("string", "mode"),
            ("string", "trace_id"),
            ("int", "cursor"),
            ("int", "limit"),
            ("int", "sample_hz"),
            ("int", "max_samples"),
            ("ref array<string>", "uids"),
        ):
            with self.subTest(args_member=member):
                self.assertEqual(args_members.count(member), 1)
        args_ctor = method_body(self.messages, "void MCPArgs()")
        self.assertIn("uids = new array<string>();", args_ctor)

        result_members = class_members(self.messages, "MCPResult")
        self.assertEqual(
            result_members.count(("ref MCPPlayersKinematicsRead", FIELD)), 1
        )

        self.assertIn(FIELD, result_prune.PRUNABLE_FIELDS)
        self.assertNotIn(FIELD, result_prune.OWNED_SCALAR_FIELDS)
        pruned_other = result_prune.prune_unfilled_fields(
            "query_all_players", {"ok": 1, FIELD: {}}
        )
        self.assertNotIn(FIELD, pruned_other)
        kept = result_prune.prune_unfilled_fields(
            COMMAND, {"ok": 1, FIELD: _wire_read(mode="status", rows=[])}
        )
        self.assertIn(FIELD, kept)

    def test_t22_python_and_enforce_mirrors_match(self) -> None:
        ints = {
            name: int(value)
            for name, value in re.findall(
                r"static const int (\w+) = (\d+);", self.sampler_code
            )
        }
        self.assertEqual(
            ints,
            {
                "SAMPLE_HZ_MIN": players_kinematics.SAMPLE_HZ_MIN,
                "SAMPLE_HZ_MAX": players_kinematics.SAMPLE_HZ_MAX,
                "MAX_SAMPLES_MIN": players_kinematics.MAX_SAMPLES_MIN,
                "MAX_SAMPLES_MAX": players_kinematics.MAX_SAMPLES_MAX,
                "LIMIT_MAX": players_kinematics.LIMIT_MAX,
                "UIDS_MAX": players_kinematics.UIDS_MAX,
                "PASS_BUDGET_TICKS": players_kinematics.PASS_BUDGET_TICKS,
            },
        )
        self.assertIn(
            f'static const string SCHEMA = "{players_kinematics.SCHEMA}";',
            self.sampler_code,
        )

        gap_returns = set(
            re.findall(r'return "(\w*)";', method_body(self.sampler, SIG_GAP_KIND))
        )
        self.assertEqual(gap_returns - {""}, players_kinematics.GAP_KINDS)

        veh_returns = set(
            re.findall(r'return "(\w+)";', method_body(self.sampler, SIG_VEHICLE_CLASS))
        )
        self.assertEqual(veh_returns, players_kinematics.VEHICLE_CLASSES)

        stop_reasons = set(
            re.findall(r's_StopReason = "(\w*)";', self.sampler_code)
        )
        self.assertEqual(stop_reasons, players_kinematics.STOP_REASONS)

    def test_t23_compile_hazards_locals_on_top_no_ternary(self) -> None:
        type_names = set(
            re.findall(
                r"\bclass\s+(\w+)",
                "".join(path.read_text(encoding="utf-8") for path in SCRIPTS.rglob("*.c")),
            )
        )
        type_names |= set(_LOCAL_TYPES) | {"IEntity", "Math", "HumanMovementState", "array", "map"}
        for text, signatures in (
            (self.sampler, SAMPLER_METHODS),
            (self.bridge, (SIG_DISPATCH,)),
        ):
            for signature in signatures:
                with self.subTest(method=signature):
                    body = method_body(text, signature)
                    names = _locals_declared_at_top(body)
                    self.assertFalse(set(names) & type_names, names)
                    code = clean(body)
                    self.assertIsNone(re.search(r"\blocal\b", code))
                    self.assertNotIn("?", code)


class SizeBudgetTest(unittest.TestCase):
    """T21: worst-case wire page + 16384 B overhead stays below half MAX_BODY_BYTES."""

    def test_t21_worst_case_page_stays_under_half_max_body_bytes(self) -> None:
        longest_command = max(players_kinematics.COMMAND_NAMES, key=len)
        longest_vehicle_class = max(players_kinematics.VEHICLE_CLASSES, key=len)
        longest_gap_kind = max(players_kinematics.GAP_KINDS, key=len)
        # 24-char numeric stand-in (serialized JSON string of 24 chars is 26 bytes,
        # strictly larger than any 24-char JSON number token).
        num24 = "123456789012345678901234"
        worst_row = {
            "uid": "u" * 64,
            "tick_ms": num24,
            "gap_kind": longest_gap_kind,
            "pos": [num24, num24, num24],
            "expected_dt_s": num24,
            "observed_dt_s": num24,
            "vel": [num24, num24, num24],
            "vel_engine": [num24, num24, num24],
            "vel_engine_available": False,
            "yaw_deg": num24,
            "yaw_deg_available": False,
            "pitch_deg": num24,
            "pitch_deg_available": False,
            "stance_idx": num24,
            "command": longest_command,
            "falling": False,
            "falling_available": False,
            "jump": False,
            "jump_available": False,
            "parachute": False,
            "parachute_available": False,
            "in_vehicle": False,
            "vehicle_type": "V" * 64,
            "vehicle_class": longest_vehicle_class,
        }
        self.assertEqual(set(worst_row), {name for _t, name in EXPECTED_ROW_MEMBERS})
        row_bytes = len(json.dumps(worst_row, separators=(",", ":")).encode("utf-8"))
        # Add 1 byte per row for array comma separator, plus 16384 B for MCPResult + header.
        total_bytes = (row_bytes + 1) * players_kinematics.LIMIT_MAX + 16384
        self.assertLess(total_bytes, loopback.MAX_BODY_BYTES // 2)


class PlayersKinematicsToolTest(unittest.IsolatedAsyncioTestCase):
    """T20: tool registration, closed schema, peer, error conversion, description."""

    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _m: None)
        )

    async def _description(self) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn(COMMAND, tools)
        return tools[COMMAND].description or ""

    async def test_t20_registered_closed_schema_peer_and_errors(self) -> None:
        self.assertIn(COMMAND, server._CLOSED_SCHEMA_TOOLS)
        description = await self._description()
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)

        # Every field of KIND and every gap_kind named in description.
        for field_name in players_kinematics.KIND.fields:
            with self.subTest(field=field_name):
                self.assertIn(field_name, description)
        for gap_name in players_kinematics.GAP_KINDS:
            with self.subTest(gap=gap_name):
                self.assertIn(gap_name, description)
        for vclass in players_kinematics.VEHICLE_CLASSES:
            with self.subTest(vehicle_class=vclass):
                self.assertIn(vclass, description)
        # Unverified fields note + Amendment E1 warning for yaw_deg + falling/parachute/budget/no-dump notes.
        for fragment in (
            "include_unverified",
            "server's heading for the player, not the client camera",
            "mouse movement or free look",
            "PhysicsIsFalling(false)",
            "not grounded",
            "vanilla DayZ 1.30 has no parachute",
            "10 000 ticks = 1 ms",
            "budget_skips",
            "a later row carries pass_late only when GapKind's rule holds",
            "more than late_after_ms since that uid's previous sample, after the earlier gap rules",
            "no disk dump",
            "lease close",
            "truncated",
            "cursor",
            "Dead bodies are never sampled",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        self.assertNotIn("and subsequent rows report pass_late", description)

        # Calls server peer and normalizes bridge result.
        bridge_answer = {"ok": 1, FIELD: _wire_read(rows=[_wire_row()])}
        call = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(
                await self.app.call_tool(
                    COMMAND,
                    {"mode": "read", "trace_id": TRACE_ID, "limit": 10},
                )
            )
        call.assert_awaited_once_with(
            COMMAND,
            {
                "mode": "read",
                "trace_id": TRACE_ID,
                "cursor": 0,
                "limit": 10,
                "sample_hz": 10,
                "max_samples": 4096,
                "uids": [],
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
                    COMMAND, {"mode": "start", "uid": "76561198000000001"}
                )
        call_extra.assert_not_awaited()

        # Request ValueError -> ToolError before bridge.
        call_bad_req = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call_bad_req):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(COMMAND, {"mode": "start", "sample_hz": 4})
        self.assertEqual(_tool_error_text(raised.exception), "bad_sample_hz")
        call_bad_req.assert_not_awaited()

        # Normalization ValueError -> ToolError.
        call_bad_res = AsyncMock(return_value={"ok": 1, FIELD: _wire_read(active=2)})
        with patch.object(self.runtime, "call_bridge", new=call_bad_res):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(COMMAND, {"mode": "read", "trace_id": TRACE_ID})
        self.assertEqual(
            _tool_error_text(raised.exception),
            "bad_bridge_kinematics_boolean: players_kinematics.active",
        )


if __name__ == "__main__":
    unittest.main()
