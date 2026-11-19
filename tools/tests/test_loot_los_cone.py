"""Contract, translated-Enforce, and tool tests for loot_los_cone.

Covers T1-T16 and kills mutants M1-M11:
- Request validation, daemon ingress, lease, and live-profile refusal (T1-T2; kills M10, M11)
- Server capability census, bitmask, arg-contract hash (T3-T4; kills M5)
- EvidenceKind contract, published envelope, masking, passthrough, header and
  cross-row invariants (T5-T9; kills M4, M6, M7)
- Translated real Enforce bodies for LootConeCandidate and DispatchLootLosCone
  plus source-level text and no-write checks (T10-T11; kills M1, M2, M3, M8, M9)
- Message/prune, tool, size-budget, mirror, and compile-hazard checks (T12-T16).
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
    loot_los_cone,
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
from dayz_mcp.session_coordination import (
    ClientIdentity,
    READ_ONLY_COMMANDS,
    SessionCoordinator,
    command_requires_lease,
)
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
    python_source,
    translate,
)
from tests.fence_helpers import INST_SERVER, bind_both_peers, bound_queue
from tests.mcp_helpers import _content_json


COMMAND = "loot_los_cone"
FIELD = "loot_los_cone"
SCRIPTS = addon_root() / "scripts"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
CLIENT_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
PREVIOUS_ACH = "2ec297a794473200"
NEW_ACH = "fe4cd4a4597078cf"
WIRE_KEYS = {"uids", "radius", "half_yaw_deg"}

PREVIOUS_51_SERVER_BITS: tuple[str, ...] = (
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
    "players_los_cone",
)

EXPECTED_ROW_MEMBERS: list[tuple[str, str]] = [
    ("string", "uid"),
    ("int", "tick_ms"),
    ("string", "gap_kind"),
    ("int", "net_low"),
    ("int", "net_high"),
    ("string", "item_type"),
    ("ref array<float>", "item_pos"),
    ("ref array<float>", "observer_head"),
    ("bool", "observer_head_bone"),
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
    ("int", "scanned"),
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
    ("ref array<ref MCPLootLosConeObserver>", "observers"),
    ("ref array<ref MCPLootLosConeRow>", "rows"),
]

SIG_YAW_OFF = "protected float LosConeYawOffDeg(float bearingDeg, float yawDeg)"
SIG_IN_CONE = (
    "protected bool LosConeInCone(float distanceM, float radiusM, "
    "float yawOffDeg, float halfYawDeg)"
)
SIG_NEAREST = "protected int LosConeNearest(array<float> distances, array<bool> taken)"
SIG_GAP_KIND = "protected string LosConeGapKind(int position, int raysMax)"
SIG_CANDIDATE = "protected InventoryItem LootConeCandidate(Object found)"
SIG_DISPATCH = (
    "protected bool DispatchLootLosCone(MCPCommand command, MCPResult result)"
)
SIG_DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"

NEW_BRIDGE_METHODS = (
    SIG_CANDIDATE,
    SIG_DISPATCH,
)

_LOCAL_TYPES = (
    "string",
    "int",
    "float",
    "bool",
    "vector",
    "Object",
    "EntityAI",
    "InventoryItem",
    "PlayerBase",
    "PlayerIdentity",
    "HumanInputController",
    "HumanCommandWeapons",
    "MCPArgs",
    "MCPLootLosConeRead",
    "MCPLootLosConeObserver",
    "MCPLootLosConeRow",
    "MCPRaycastHit",
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:ref\s+)?(?:array\s*<[^;>]+>|"
    + "|".join(_LOCAL_TYPES)
    + r")\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)
_IDENTITY: dict[str, object] = {
    "platform": "codex",
    "pid": 12,
    "ppid": 1,
    "started_at_utc": "2026-10-24T00:00:00Z",
    "session_id": "loot-cone-session",
    "task_label": "loot_los_cone",
}
_NET_ID_CALL_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<recv>[A-Za-z_]\w*)\.GetNetworkID\("
    r"(?P<low>[A-Za-z_]\w*)\s*,\s*(?P<high>[A-Za-z_]\w*)\)$",
    re.MULTILINE,
)


class _LootConeString(EnforceString):
    """EnforceString plus Trim() for DispatchLootLosCone translation."""

    __slots__ = ()

    def Trim(self) -> "_LootConeString":
        return _LootConeString(self.data.strip())


class _LootConeArray(EnforceArray):
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


def _block_after(source: str, start: int) -> str:
    brace = source.index("{", start)
    if source[start:brace].strip():
        raise AssertionError(
            f"unexpected text before block: {source[start:brace]!r}"
        )
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block at offset {start}")


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    if source.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {source.count(needle)} times")
    return _block_after(source, source.index(needle) + len(needle))


def _announce_server(
    state: loopback.ServerState, commands: list[str], ach: str | None
) -> None:
    state.daemon_generation = "gen-loot-cone"
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _coordinated_state() -> loopback.ServerState:
    state = loopback.ServerState("test-key")
    bind_both_peers(state)
    _announce_server(
        state,
        sorted(_BRIDGE_COMMAND_TOOLS["server"]),
        EXPECTED_SERVER_ARG_CONTRACT_HASH,
    )
    state.coordination = SessionCoordinator(
        token_fn=lambda: "token",
        id_fn=lambda: "lease",
        audit=lambda event: True,
        cleanup=lambda session, lease, reason, active: state.cleanup_owner(
            session, lease, reason, active
        ),
    )
    return state


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
        "radius": 8.0,
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
        "scanned": 5,
        "in_radius": 2,
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
        "net_low": 1001,
        "net_high": 2,
        "item_type": "BakedBeansCan",
        "item_pos": [103.535, 5.0, 203.535],
        "observer_head": [100.0, 6.7, 200.0],
        "observer_head_bone": 1,
        "distance_m": 5.28,
        "yaw_deg": 45.0,
        "yaw_off_deg": 0.0,
        "pitch_deg": -18.5,
        "pitch_deg_available": 1,
        "ray_hit": 1,
        "hit_pos": [102.0, 5.8, 202.0],
        "hit_pos_available": 1,
        "hit_distance_m": 2.97,
        "hit_distance_m_available": 1,
        "clearance_m": 2.31,
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
        "schema": loot_los_cone.SCHEMA,
        "tick_ms": 12000,
        "radius_m": 8.0,
        "half_yaw_deg": 10.0,
        "pitch_filter_applied": 0,
        "observers_max": loot_los_cone.OBSERVERS_MAX,
        "rays_per_observer_max": loot_los_cone.RAYS_PER_OBSERVER_MAX,
        "rows_per_observer_max": loot_los_cone.ROWS_PER_OBSERVER_MAX,
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
        default_req = loot_los_cone.normalize_request(["76561198000000001"])
        self.assertEqual(
            default_req,
            {
                "uids": ["76561198000000001"],
                "radius": 8.0,
                "half_yaw_deg": 10.0,
            },
        )
        self.assertEqual(set(default_req), WIRE_KEYS)
        self.assertEqual(
            loopback.validate_command_args(COMMAND, default_req), (True, None)
        )

        uid64 = "a" * loot_los_cone.UID_MAX_CHARS
        four_uids = [uid64, "u2", "u3", "u4"]
        max_req = loot_los_cone.normalize_request(four_uids, 15.0, 30.0)
        self.assertEqual(
            max_req,
            {"uids": four_uids, "radius": 15.0, "half_yaw_deg": 30.0},
        )
        self.assertEqual(loopback.validate_command_args(COMMAND, max_req), (True, None))

        # Kills M10: 65-char uid must be refused as bad_uids.
        uid65 = "a" * (loot_los_cone.UID_MAX_CHARS + 1)
        cases = (
            (("u1", 8.0, 10.0), "bad_uids"),
            ((("u1",), 8.0, 10.0), "bad_uids"),
            ((123, 8.0, 10.0), "bad_uids"),
            ((None, 8.0, 10.0), "bad_uids"),
            (([], 8.0, 10.0), "bad_uids"),
            ((["u1", "u2", "u3", "u4", "u5"], 8.0, 10.0), "bad_uids"),
            (([""], 8.0, 10.0), "bad_uids"),
            ((["   "], 8.0, 10.0), "bad_uids"),
            ((["u1", "u1"], 8.0, 10.0), "bad_uids"),
            (([123], 8.0, 10.0), "bad_uids"),
            (([uid65], 8.0, 10.0), "bad_uids"),
            ((["u1"], 0, 10.0), "bad_radius_m"),
            ((["u1"], 0.0, 10.0), "bad_radius_m"),
            ((["u1"], -1.0, 10.0), "bad_radius_m"),
            ((["u1"], 15.01, 10.0), "bad_radius_m"),
            ((["u1"], math.nan, 10.0), "bad_radius_m"),
            ((["u1"], math.inf, 10.0), "bad_radius_m"),
            ((["u1"], -math.inf, 10.0), "bad_radius_m"),
            ((["u1"], True, 10.0), "bad_radius_m"),
            ((["u1"], False, 10.0), "bad_radius_m"),
            ((["u1"], 8.0, 0), "bad_half_yaw_deg"),
            ((["u1"], 8.0, 0.0), "bad_half_yaw_deg"),
            ((["u1"], 8.0, -0.5), "bad_half_yaw_deg"),
            ((["u1"], 8.0, 30.01), "bad_half_yaw_deg"),
            ((["u1"], 8.0, math.nan), "bad_half_yaw_deg"),
            ((["u1"], 8.0, math.inf), "bad_half_yaw_deg"),
            ((["u1"], 8.0, True), "bad_half_yaw_deg"),
            ((["u1"], 8.0, False), "bad_half_yaw_deg"),
        )
        for arguments, expected_code in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError) as raised:
                    loot_los_cone.normalize_request(*arguments)  # type: ignore[arg-type]
                self.assertEqual(str(raised.exception), expected_code)

    def test_t2_ingress_lease_admission_and_live_profile(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        # Kills M11: loot_los_cone must NOT be in READ_ONLY_COMMANDS and must require a lease.
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

        # Schema rejects empty uids, 5 uids, duplicate uid, 65-char uid (64 passes), and radius 15.01.
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
            loopback.validate_command_args(COMMAND, _wire_args(radius=15.01)),
            (False, "bad_args"),
        )

        self.assertEqual(
            self.state.enqueue_command(COMMAND, _wire_args(), peer="client"),
            (400, {"error": "bad_peer"}),
        )

        # Live profile refuses with and without admin (D10; also kills M11).
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

    def test_t2_coordinator_refuses_missing_and_invalid_lease_without_queueing(self) -> None:
        state = _coordinated_state()
        before_id = state._next_id

        # Missing lease token: 423 lease_required, nothing queued, id untouched.
        self.assertEqual(
            state.enqueue_command(
                COMMAND, _wire_args(), peer="server", identity_payload=_IDENTITY
            ),
            (423, {"error": "lease_required"}),
        )
        self.assertEqual(bound_queue(state, "server"), [])
        self.assertEqual(state._next_id, before_id)

        # Invalid lease token before any lease is active: 403 lease_invalid, nothing queued.
        self.assertEqual(
            state.enqueue_command(
                COMMAND,
                _wire_args(),
                peer="server",
                identity_payload=_IDENTITY,
                lease_token="wrong-token",
            ),
            (403, {"error": "lease_invalid"}),
        )
        self.assertEqual(bound_queue(state, "server"), [])
        self.assertEqual(state._next_id, before_id)

        # Invalid token while a lease is active is still refused without queueing.
        client = ClientIdentity.from_payload(_IDENTITY)
        acq_status, grant = state.coordination.acquire(client, "loot_los_cone")  # type: ignore[union-attr]
        self.assertEqual(acq_status, 200)
        self.assertEqual(
            state.enqueue_command(
                COMMAND,
                _wire_args(),
                peer="server",
                identity_payload=_IDENTITY,
                lease_token="wrong-token",
            ),
            (403, {"error": "lease_invalid"}),
        )
        self.assertEqual(bound_queue(state, "server"), [])
        self.assertEqual(state._next_id, before_id)

        # Valid lease token enqueues the command on the server peer.
        status, body = state.enqueue_command(
            COMMAND,
            _wire_args(),
            peer="server",
            identity_payload=_IDENTITY,
            lease_token=grant["lease_token"],
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(len(bound_queue(state, "server")), 1)
        self.assertEqual(state._next_id, before_id + 1)

    def test_t2_enqueue_refuses_invalid_half_yaw_deg_with_valid_lease(self) -> None:
        state = _coordinated_state()
        client = ClientIdentity.from_payload(_IDENTITY)
        acq_status, grant = state.coordination.acquire(client, "loot_los_cone")  # type: ignore[union-attr]
        self.assertEqual(acq_status, 200)
        lease_token = grant["lease_token"]
        before_id = state._next_id

        for bad_half_yaw in (
            0,
            0.0,
            -0.5,
            30.01,
            30.5,
            math.nan,
            math.inf,
            -math.inf,
            True,
            False,
            "10.0",
        ):
            with self.subTest(half_yaw_deg=bad_half_yaw):
                self.assertEqual(
                    state.enqueue_command(
                        COMMAND,
                        _wire_args(half_yaw_deg=bad_half_yaw),
                        peer="server",
                        identity_payload=_IDENTITY,
                        lease_token=lease_token,
                    ),
                    (400, {"error": "bad_args"}),
                )
                self.assertEqual(bound_queue(state, "server"), [])
                self.assertEqual(state._next_id, before_id)

        # Exact cap (30.0) passes ingress with the same valid lease.
        status, body = state.enqueue_command(
            COMMAND,
            _wire_args(half_yaw_deg=loot_los_cone.HALF_YAW_MAX_DEG),
            peer="server",
            identity_payload=_IDENTITY,
            lease_token=lease_token,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(len(bound_queue(state, "server")), 1)
        self.assertEqual(state._next_id, before_id + 1)


class CensusAndContractHashTest(unittest.TestCase):
    """T3 (server capability census & mask) and T4 (SERVER_ARG_CONTRACT & ach)."""

    def test_t3_server_census_mask_and_client_absence(self) -> None:
        server_src = _source(SERVER_BRIDGE_PATH)
        census = _server_const_census(server_src)
        self.assertIn(COMMAND, census)
        self.assertEqual(census, sorted(census))
        self.assertLess(census.index("loadout_get"), census.index(COMMAND))
        self.assertLess(census.index(COMMAND), census.index("nav_path_query"))

        branches = _server_dispatch_branches(server_src)
        self.assertIn(COMMAND, branches)
        dispatch = method_body(server_src, SIG_DISPATCH_CHAIN)
        self.assertEqual(
            dispatch.count(f'else if (command.cmd == "{COMMAND}")'), 1
        )
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(
            branch.strip(), "postNow = DispatchLootLosCone(command, result);"
        )
        _in_order(
            self,
            dispatch,
            f'else if (command.cmd == "{COMMAND}")',
            "postNow = DispatchLootLosCone(command, result);",
            'result.error = "unknown_command";',
        )

        # Bit table append-only: prior 51 bits unchanged, bit 51 is loot_los_cone.
        self.assertEqual(SERVER_CAPABILITY_BITS[:51], PREVIOUS_51_SERVER_BITS)
        self.assertEqual(SERVER_CAPABILITY_BITS[51:], (COMMAND,))

        mask_matches = re.findall(r'SERVER_CAPABILITIES_MASK\s*=\s*"([^"]+)"', server_src)
        self.assertEqual(mask_matches, ["1.fffffffffffff"])
        self.assertEqual(encode_capability_mask("server", census), "1.fffffffffffff")
        decoded, reason = parse_capability_mask("server", mask_matches[0])
        self.assertEqual(reason, "ok")
        self.assertEqual(set(decoded or ()), set(census))
        self.assertEqual(set(decoded or ()), set(branches))

        # Client bridge neither implements nor announces loot_los_cone.
        client_src = _source(CLIENT_BRIDGE_PATH)
        self.assertNotIn(COMMAND, client_src)
        self.assertNotIn("MCPLootLosCone", client_src)
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
        # Kills M5: MCPBridge.c must declare the updated ach hash, not #432's.
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
        kind = loot_los_cone.KIND
        self.assertEqual(kind.name, "loot_los_cone")
        self.assertEqual(kind.gap_kinds, loot_los_cone.GAP_KINDS)
        expected_fields = [
            ("net_low", evidence.EvidenceField("int")),
            ("net_high", evidence.EvidenceField("int")),
            ("item_type", evidence.EvidenceField("token")),
            ("item_pos", evidence.EvidenceField("vec3", "m")),
            ("observer_head", evidence.EvidenceField("vec3", "m")),
            ("observer_head_bone", evidence.EvidenceField("flag")),
            ("distance_m", evidence.EvidenceField("number", "m")),
            ("yaw_deg", evidence.EvidenceField("number", "deg")),
            ("yaw_off_deg", evidence.EvidenceField("number", "deg")),
            ("pitch_deg", evidence.EvidenceField("number", "deg", optional=True)),
            # Kills M6: ray_hit must declare absent_with ray_budget_exceeded.
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
            net_low=101,
            net_high=1,
            item_type="BakedBeansCan",
            item_pos=[10.0, 0.0, 23.0],
            observer_head=[10.0, 1.7, 20.0],
            observer_head_bone=1,
            distance_m=3.45,
            yaw_deg=0.0,
            yaw_off_deg=0.0,
            pitch_deg=-25.0,
            pitch_deg_available=1,
            ray_hit=1,
            hit_pos=[10.0, 0.85, 21.5],
            hit_pos_available=1,
            hit_distance_m=1.72,
            hit_distance_m_available=1,
            clearance_m=1.72,
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
            net_low=102,
            net_high=1,
            item_type="SodaCan_Cola",
            item_pos=[10.2, 0.0, 25.0],
            observer_head=[10.0, 1.7, 20.0],
            observer_head_bone=1,
            distance_m=5.28,
            yaw_deg=0.0,
            yaw_off_deg=2.29,
            pitch_deg=-25.0,
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
            net_low=103,
            net_high=1,
            item_type="Barrel_Red",
            item_pos=[10.4, 0.0, 26.5],
            observer_head=[10.0, 1.7, 20.0],
            observer_head_bone=1,
            distance_m=6.73,
            yaw_deg=0.0,
            yaw_off_deg=3.52,
            pitch_deg=-25.0,
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
                        scanned=12,
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
                        scanned=0,
                        in_radius=0,
                        in_cone=0,
                        rays=0,
                        rows=0,
                    ),
                ],
                rows=[row_hit, row_miss, row_gap],
            ),
        }
        published = loot_los_cone.normalize_bridge_result(
            wire, include_unverified=False
        )
        expected_header = {
            "schema": loot_los_cone.SCHEMA,
            "tick_ms": 15000,
            "radius_m": 8.0,
            "half_yaw_deg": 10.0,
            "pitch_filter_applied": False,
            "observers_max": 4,
            "rays_per_observer_max": 4,
            "rows_per_observer_max": 16,
            "truncated": False,
            "observers": [
                {
                    "uid": "obs-a",
                    "found": True,
                    "yaw_deg_available": True,
                    "yaw_deg": 0.0,
                    "scanned": 12,
                    "in_radius": 5,
                    "in_cone": 3,
                    "rays": 2,
                    "rows": 3,
                },
                {
                    "uid": "obs-missing",
                    "found": False,
                    "yaw_deg_available": False,
                    "scanned": 0,
                    "in_radius": 0,
                    "in_cone": 0,
                    "rays": 0,
                    "rows": 0,
                },
            ],
        }
        expected_evidence = {
            "schema": evidence.EVIDENCE_SCHEMA,
            "kind": "loot_los_cone",
            "units": {
                "tick_ms": "ms",
                "item_pos": "m",
                "observer_head": "m",
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
                    "net_low": 101,
                    "net_high": 1,
                    "item_type": "BakedBeansCan",
                    "item_pos": [10.0, 0.0, 23.0],
                    "observer_head": [10.0, 1.7, 20.0],
                    "observer_head_bone": True,
                    "distance_m": 3.45,
                    "yaw_deg": 0.0,
                    "yaw_off_deg": 0.0,
                    "pitch_deg_available": False,
                    "ray_hit": True,
                    "hit_pos": [10.0, 0.85, 21.5],
                    "hit_pos_available": True,
                    "hit_distance_m": 1.72,
                    "hit_distance_m_available": True,
                    "clearance_m": 1.72,
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
                    "net_low": 102,
                    "net_high": 1,
                    "item_type": "SodaCan_Cola",
                    "item_pos": [10.2, 0.0, 25.0],
                    "observer_head": [10.0, 1.7, 20.0],
                    "observer_head_bone": True,
                    "distance_m": 5.28,
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
                    "net_low": 103,
                    "net_high": 1,
                    "item_type": "Barrel_Red",
                    "item_pos": [10.4, 0.0, 26.5],
                    "observer_head": [10.0, 1.7, 20.0],
                    "observer_head_bone": True,
                    "distance_m": 6.73,
                    "yaw_deg": 0.0,
                    "yaw_off_deg": 3.52,
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
        self.assertEqual(published["loot_los_cone"], expected_header)
        self.assertEqual(published["evidence"], expected_evidence)

    def test_t6_truncated_propagates_to_evidence_envelope(self) -> None:
        # Kills M7: evidence["truncated"] must reflect header["truncated"] == True.
        wire = {
            "ok": 1,
            FIELD: _wire_read(
                truncated=1,
                observers=[
                    _wire_observer(scanned=10, in_radius=5, in_cone=2, rays=1, rows=1),
                ],
                rows=[_wire_row()],
            ),
        }
        published = loot_los_cone.normalize_bridge_result(wire)
        self.assertIs(published["loot_los_cone"]["truncated"], True)
        self.assertIs(published["evidence"]["truncated"], True)

    def test_t7_unverified_field_masking_and_input_immutability(self) -> None:
        wire = {
            "ok": 1,
            FIELD: _wire_read(rows=[_wire_row(pitch_deg=14.25, pitch_deg_available=1)]),
        }
        snapshot = copy.deepcopy(wire)

        # Kills M4: default masks every field in UNVERIFIED_FIELDS.
        masked = loot_los_cone.normalize_bridge_result(wire)
        row_masked = masked["evidence"]["rows"][0]
        for field_name in loot_los_cone.UNVERIFIED_FIELDS:
            self.assertIs(row_masked[f"{field_name}_available"], False)
            self.assertNotIn(field_name, row_masked)
        self.assertEqual(wire, snapshot)

        unmasked = loot_los_cone.normalize_bridge_result(wire, include_unverified=True)
        row_unmasked = unmasked["evidence"]["rows"][0]
        self.assertIs(row_unmasked["pitch_deg_available"], True)
        self.assertEqual(row_unmasked["pitch_deg"], 14.25)
        self.assertEqual(wire, snapshot)

    def test_t8_no_filtering_plausibility_or_verdict_keys(self) -> None:
        rows = [
            _wire_row(
                net_low=-42,
                net_high=-999,
                item_type="EdgePosCan",
                item_pos=[-99984.5001, -500.0, 123456.75],
                observer_head=[-99999.5, -500.0, 123456.75],
                distance_m=14.9999,
                yaw_off_deg=10.0,
                clearance_m=0.0,
            ),
            _wire_row(
                net_low=-1,
                net_high=-2147483648,
                item_type="EdgeNegCan",
                item_pos=[0.0, 0.0, 15.0],
                observer_head=[0.0, 0.0, 0.0],
                distance_m=15.0,
                yaw_off_deg=-10.0,
                clearance_m=0.0,
            ),
        ]
        wire = {
            "ok": 1,
            FIELD: _wire_read(
                radius_m=15.0,
                observers=[
                    _wire_observer(scanned=4, in_radius=2, in_cone=2, rays=2, rows=2)
                ],
                rows=rows,
            ),
        }
        published = loot_los_cone.normalize_bridge_result(wire)
        out_rows = published["evidence"]["rows"]
        self.assertEqual([r["item_type"] for r in out_rows], ["EdgePosCan", "EdgeNegCan"])
        self.assertEqual(out_rows[0]["net_low"], -42)
        self.assertEqual(out_rows[0]["net_high"], -999)
        self.assertEqual(out_rows[0]["distance_m"], 14.9999)
        self.assertEqual(out_rows[0]["yaw_off_deg"], 10.0)
        self.assertEqual(out_rows[0]["clearance_m"], 0.0)
        self.assertEqual(out_rows[1]["net_low"], -1)
        self.assertEqual(out_rows[1]["net_high"], -2147483648)
        self.assertEqual(out_rows[1]["distance_m"], 15.0)
        self.assertEqual(out_rows[1]["yaw_off_deg"], -10.0)
        self.assertEqual(out_rows[1]["clearance_m"], 0.0)

        allowed_keys = {"uid", "tick_ms", "gap_kind"}
        for field_name, spec in loot_los_cone.KIND.fields.items():
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
            out = loot_los_cone.normalize_bridge_result(wire)
            self.assertIs(out["loot_los_cone"]["pitch_filter_applied"], False)
            self.assertIs(out["loot_los_cone"]["truncated"], False)

        # Found observer without yaw_deg_available: scanned & in_radius allowed, yaw_deg omitted.
        no_yaw = loot_los_cone.normalize_bridge_result(
            {
                "ok": 1,
                FIELD: _wire_read(
                    observers=[
                        _wire_observer(
                            found=1,
                            yaw_deg_available=0,
                            yaw_deg=0.0,
                            scanned=7,
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
        self.assertNotIn("yaw_deg", no_yaw["loot_los_cone"]["observers"][0])
        self.assertEqual(no_yaw["loot_los_cone"]["observers"][0]["scanned"], 7)
        self.assertEqual(no_yaw["loot_los_cone"]["observers"][0]["in_radius"], 3)

        # Bad booleans -> bad_bridge_loot_cone_boolean: <where>.
        with self.assertRaises(ValueError) as raised:
            loot_los_cone.normalize_bridge_result(
                {"ok": 1, FIELD: _wire_read()}, include_unverified=1  # type: ignore[arg-type]
            )
        self.assertEqual(
            str(raised.exception), "bad_bridge_loot_cone_boolean: include_unverified"
        )

        def _without(mapping: dict[str, object], key: str) -> dict[str, object]:
            return {k: v for k, v in mapping.items() if k != key}

        bool_cases = (
            (_wire_read(pitch_filter_applied=2), "loot_los_cone.pitch_filter_applied"),
            (_wire_read(pitch_filter_applied="0"), "loot_los_cone.pitch_filter_applied"),
            (_wire_read(pitch_filter_applied=0.0), "loot_los_cone.pitch_filter_applied"),
            (
                _without(_wire_read(), "pitch_filter_applied"),
                "loot_los_cone.pitch_filter_applied",
            ),
            (_wire_read(truncated="0"), "loot_los_cone.truncated"),
            (_wire_read(truncated=0.0), "loot_los_cone.truncated"),
            (
                _without(_wire_read(), "truncated"),
                "loot_los_cone.truncated",
            ),
            (
                _wire_read(observers=[_wire_observer(found=2)]),
                "loot_los_cone.observers[0].found",
            ),
            (
                _wire_read(observers=[_wire_observer(found="1")]),
                "loot_los_cone.observers[0].found",
            ),
            (
                _wire_read(observers=[_wire_observer(found=1.0)]),
                "loot_los_cone.observers[0].found",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "found")]),
                "loot_los_cone.observers[0].found",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg_available=-1)]),
                "loot_los_cone.observers[0].yaw_deg_available",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg_available="1")]),
                "loot_los_cone.observers[0].yaw_deg_available",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg_available=1.0)]),
                "loot_los_cone.observers[0].yaw_deg_available",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "yaw_deg_available")]),
                "loot_los_cone.observers[0].yaw_deg_available",
            ),
        )
        for idx, (bad_read, where) in enumerate(bool_cases):
            with self.subTest(idx=idx, bool_where=where):
                with self.assertRaises(ValueError) as raised:
                    loot_los_cone.normalize_bridge_result({"ok": 1, FIELD: bad_read})
                self.assertEqual(
                    str(raised.exception), f"bad_bridge_loot_cone_boolean: {where}"
                )

        # Top-level and header member / invariant rejections.
        with self.assertRaises(ValueError) as raised:
            loot_los_cone.normalize_bridge_result("not-a-dict")
        self.assertEqual(str(raised.exception), "bad_bridge_loot_cone_result: result")

        with self.assertRaises(ValueError) as raised:
            loot_los_cone.normalize_bridge_result({"ok": 1, FIELD: None})
        self.assertEqual(
            str(raised.exception), "bad_bridge_loot_cone_result: loot_los_cone"
        )

        result_cases: tuple[tuple[dict[str, object], str], ...] = (
            (_without(_wire_read(), "rows"), "loot_los_cone.rows"),
            (_wire_read(rows="bad"), "loot_los_cone.rows"),
            (_without(_wire_read(), "schema"), "loot_los_cone.schema"),
            (_wire_read(schema=123), "loot_los_cone.schema"),
            (_wire_read(schema="wrong"), "loot_los_cone.schema"),
            (_without(_wire_read(), "tick_ms"), "loot_los_cone.tick_ms"),
            (_wire_read(tick_ms=-1), "loot_los_cone.tick_ms"),
            (_wire_read(tick_ms=True), "loot_los_cone.tick_ms"),
            (_wire_read(tick_ms=12000.0), "loot_los_cone.tick_ms"),
            (_wire_read(tick_ms="12000"), "loot_los_cone.tick_ms"),
            (_without(_wire_read(), "radius_m"), "loot_los_cone.radius_m"),
            (_wire_read(radius_m="8.0"), "loot_los_cone.radius_m"),
            (_wire_read(radius_m=True), "loot_los_cone.radius_m"),
            (_wire_read(radius_m=0.0), "loot_los_cone.radius_m"),
            (_wire_read(radius_m=15.5), "loot_los_cone.radius_m"),
            (_wire_read(radius_m=math.nan), "loot_los_cone.radius_m"),
            (_without(_wire_read(), "half_yaw_deg"), "loot_los_cone.half_yaw_deg"),
            (_wire_read(half_yaw_deg="10.0"), "loot_los_cone.half_yaw_deg"),
            (_wire_read(half_yaw_deg=True), "loot_los_cone.half_yaw_deg"),
            (_wire_read(half_yaw_deg=0.0), "loot_los_cone.half_yaw_deg"),
            (_wire_read(half_yaw_deg=31.0), "loot_los_cone.half_yaw_deg"),
            (_wire_read(pitch_filter_applied=1), "loot_los_cone.pitch_filter_applied"),
            (_without(_wire_read(), "observers_max"), "loot_los_cone.observers_max"),
            (_wire_read(observers_max=5), "loot_los_cone.observers_max"),
            (_wire_read(observers_max=4.0), "loot_los_cone.observers_max"),
            (_wire_read(observers_max="4"), "loot_los_cone.observers_max"),
            (_wire_read(observers_max=True), "loot_los_cone.observers_max"),
            (
                _without(_wire_read(), "rays_per_observer_max"),
                "loot_los_cone.rays_per_observer_max",
            ),
            (_wire_read(rays_per_observer_max=5), "loot_los_cone.rays_per_observer_max"),
            (_wire_read(rays_per_observer_max=4.0), "loot_los_cone.rays_per_observer_max"),
            (_wire_read(rays_per_observer_max="4"), "loot_los_cone.rays_per_observer_max"),
            (_wire_read(rays_per_observer_max=True), "loot_los_cone.rays_per_observer_max"),
            (
                _without(_wire_read(), "rows_per_observer_max"),
                "loot_los_cone.rows_per_observer_max",
            ),
            (_wire_read(rows_per_observer_max=17), "loot_los_cone.rows_per_observer_max"),
            (_wire_read(rows_per_observer_max=16.0), "loot_los_cone.rows_per_observer_max"),
            (_wire_read(rows_per_observer_max="16"), "loot_los_cone.rows_per_observer_max"),
            (_wire_read(rows_per_observer_max=True), "loot_los_cone.rows_per_observer_max"),
            (_without(_wire_read(), "observers"), "loot_los_cone.observers"),
            (_wire_read(observers="bad", rows=[]), "loot_los_cone.observers"),
            (_wire_read(observers=[], rows=[]), "loot_los_cone.observers"),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            uid=f"u{i}", scanned=0, in_radius=0, in_cone=0, rays=0, rows=0
                        )
                        for i in range(5)
                    ],
                    rows=[],
                ),
                "loot_los_cone.observers",
            ),
            (
                _wire_read(observers=["not-a-dict"], rows=[]),
                "loot_los_cone.observers[0]",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "uid")]),
                "loot_los_cone.observers[0].uid",
            ),
            (
                _wire_read(observers=[_wire_observer(uid=123)]),
                "loot_los_cone.observers[0].uid",
            ),
            (
                _wire_read(observers=[_wire_observer(uid="  ")], rows=[]),
                "loot_los_cone.observers[0].uid",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            uid="u1", scanned=0, in_radius=0, in_cone=0, rays=0, rows=0
                        ),
                        _wire_observer(
                            uid="u1", scanned=0, in_radius=0, in_cone=0, rays=0, rows=0
                        ),
                    ],
                    rows=[],
                ),
                "loot_los_cone.observers[1].uid",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "scanned")]),
                "loot_los_cone.observers[0].scanned",
            ),
            (
                _wire_read(observers=[_wire_observer(scanned=-1)]),
                "loot_los_cone.observers[0].scanned",
            ),
            (
                _wire_read(observers=[_wire_observer(scanned=5.0)]),
                "loot_los_cone.observers[0].scanned",
            ),
            (
                _wire_read(observers=[_wire_observer(scanned=True)]),
                "loot_los_cone.observers[0].scanned",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "in_radius")]),
                "loot_los_cone.observers[0].in_radius",
            ),
            (
                _wire_read(observers=[_wire_observer(in_radius=1.0)]),
                "loot_los_cone.observers[0].in_radius",
            ),
            (
                _wire_read(observers=[_wire_observer(in_radius=True)]),
                "loot_los_cone.observers[0].in_radius",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "in_cone")]),
                "loot_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(observers=[_wire_observer(in_cone=1.0)]),
                "loot_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(observers=[_wire_observer(in_cone=True)]),
                "loot_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "rays")]),
                "loot_los_cone.observers[0].rays",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=1.0)]),
                "loot_los_cone.observers[0].rays",
            ),
            (
                _wire_read(observers=[_wire_observer(rays=True)]),
                "loot_los_cone.observers[0].rays",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "rows")]),
                "loot_los_cone.observers[0].rows",
            ),
            (
                _wire_read(observers=[_wire_observer(rows=1.0)]),
                "loot_los_cone.observers[0].rows",
            ),
            (
                _wire_read(observers=[_wire_observer(rows=True)]),
                "loot_los_cone.observers[0].rows",
            ),
            (
                _wire_read(
                    observers=[_wire_observer(scanned=1, in_radius=2, in_cone=1, rays=1, rows=1)]
                ),
                "loot_los_cone.observers[0].in_radius",
            ),
            (
                _wire_read(
                    observers=[_wire_observer(scanned=5, rays=2, rows=1, in_cone=2, in_radius=2)]
                ),
                "loot_los_cone.observers[0].rays",
            ),
            (
                _wire_read(
                    observers=[_wire_observer(scanned=5, rays=5, rows=5, in_cone=5, in_radius=5)]
                ),
                "loot_los_cone.observers[0].rays",
            ),
            (
                _wire_read(
                    observers=[_wire_observer(scanned=5, rays=1, rows=2, in_cone=1, in_radius=2)]
                ),
                "loot_los_cone.observers[0].rows",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(scanned=20, rays=1, rows=17, in_cone=17, in_radius=17)
                    ]
                ),
                "loot_los_cone.observers[0].rows",
            ),
            (
                _wire_read(
                    observers=[_wire_observer(scanned=5, rays=1, rows=1, in_cone=2, in_radius=1)]
                ),
                "loot_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            found=0,
                            yaw_deg_available=0,
                            scanned=1,
                            in_radius=0,
                            in_cone=0,
                            rays=0,
                            rows=0,
                        )
                    ],
                    rows=[],
                ),
                "loot_los_cone.observers[0].scanned",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            found=0,
                            yaw_deg_available=0,
                            scanned=1,
                            in_radius=1,
                            in_cone=0,
                            rays=0,
                            rows=0,
                        )
                    ],
                    rows=[],
                ),
                "loot_los_cone.observers[0].scanned",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            found=0,
                            yaw_deg_available=1,
                            scanned=0,
                            in_radius=0,
                            in_cone=0,
                            rays=0,
                            rows=0,
                        )
                    ],
                    rows=[],
                ),
                "loot_los_cone.observers[0].yaw_deg_available",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            found=1,
                            yaw_deg_available=0,
                            scanned=4,
                            in_radius=2,
                            in_cone=1,
                            rays=0,
                            rows=1,
                        )
                    ],
                    rows=[_wire_row(gap_kind="ray_budget_exceeded")],
                ),
                "loot_los_cone.observers[0].in_cone",
            ),
            (
                _wire_read(observers=[_without(_wire_observer(), "yaw_deg")]),
                "loot_los_cone.observers[0].yaw_deg",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg="45.0")]),
                "loot_los_cone.observers[0].yaw_deg",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg=True)]),
                "loot_los_cone.observers[0].yaw_deg",
            ),
            (
                _wire_read(observers=[_wire_observer(yaw_deg=math.nan)]),
                "loot_los_cone.observers[0].yaw_deg",
            ),
            (
                _wire_read(rows=["not-a-dict"]),
                "loot_los_cone.rows[0]",
            ),
            (
                _wire_read(rows=[_without(_wire_row(), "uid")]),
                "loot_los_cone.rows[0].uid",
            ),
            (
                _wire_read(rows=[_wire_row(uid=123)]),
                "loot_los_cone.rows[0].uid",
            ),
            (
                _wire_read(rows=[_wire_row(uid="unknown-obs")]),
                "loot_los_cone.rows[0].uid",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(
                            uid="obs-dead",
                            found=0,
                            yaw_deg_available=0,
                            scanned=0,
                            in_radius=0,
                            in_cone=0,
                            rays=0,
                            rows=0,
                        )
                    ],
                    rows=[_wire_row(uid="obs-dead")],
                ),
                "loot_los_cone.rows[0].uid",
            ),
            (
                _wire_read(rows=[_without(_wire_row(), "tick_ms")]),
                "loot_los_cone.rows[0].tick_ms",
            ),
            (
                _wire_read(tick_ms=12000, rows=[_wire_row(tick_ms=12000.0)]),
                "loot_los_cone.rows[0].tick_ms",
            ),
            (
                _wire_read(tick_ms=12000, rows=[_wire_row(tick_ms=12001)]),
                "loot_los_cone.rows[0].tick_ms",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(scanned=5, in_radius=2, in_cone=2, rays=1, rows=2)
                    ],
                    rows=[_wire_row()],
                ),
                "loot_los_cone.observers[0].rows",
            ),
            (
                _wire_read(
                    observers=[
                        _wire_observer(scanned=5, in_radius=1, in_cone=1, rays=1, rows=1)
                    ],
                    rows=[_wire_row(gap_kind="ray_budget_exceeded")],
                ),
                "loot_los_cone.observers[0].rays",
            ),
            (
                _wire_read(
                    truncated=0,
                    observers=[
                        _wire_observer(scanned=5, in_radius=5, in_cone=2, rays=1, rows=1)
                    ],
                    rows=[_wire_row()],
                ),
                "loot_los_cone.truncated",
            ),
            (
                _wire_read(
                    truncated=1,
                    observers=[
                        _wire_observer(scanned=5, in_radius=1, in_cone=1, rays=1, rows=1)
                    ],
                    rows=[_wire_row()],
                ),
                "loot_los_cone.truncated",
            ),
        )
        for idx, (bad_read, where) in enumerate(result_cases):
            with self.subTest(idx=idx, where=where):
                with self.assertRaises(ValueError) as raised:
                    loot_los_cone.normalize_bridge_result({"ok": 1, FIELD: bad_read})
                self.assertEqual(
                    str(raised.exception), f"bad_bridge_loot_cone_result: {where}"
                )


class _FakeIdentity:
    def __init__(self, plain_id: str) -> None:
        self._plain_id = _LootConeString.of(plain_id)

    def GetPlainId(self) -> _LootConeString:
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

    def GetHierarchyParent(self) -> object:
        return None


class _FakeNonItem:
    def __init__(
        self,
        pos: tuple[float, float, float] = (0.0, 0.0, 0.0),
        *,
        parent: object = None,
    ) -> None:
        self._pos = EnforceVector(pos)
        self._parent = parent

    def GetHierarchyParent(self) -> object:
        return self._parent

    def GetPosition(self) -> EnforceVector:
        return EnforceVector(self._pos)


class _FakeItem:
    def __init__(
        self,
        item_type: str,
        pos: tuple[float, float, float],
        *,
        class_name: str = "ItemBase",
        parent: object = None,
        net_id_log: list[object] | None = None,
        net_low: int = 0,
        net_high: int = 0,
    ) -> None:
        self._item_type = _LootConeString.of(item_type)
        self._class_name = _LootConeString.of(class_name)
        self._pos = EnforceVector(pos)
        self._parent = parent
        self._net_id_log = net_id_log
        self._net_low = net_low
        self._net_high = net_high

    def GetHierarchyParent(self) -> object:
        return self._parent

    def GetPosition(self) -> EnforceVector:
        return EnforceVector(self._pos)

    def GetNetworkID(self, _low: int, _high: int) -> tuple[int, int]:
        if self._net_id_log is not None:
            self._net_id_log.append(self)
        return self._net_low, self._net_high

    def GetType(self) -> _LootConeString:
        return self._item_type

    def ClassName(self) -> _LootConeString:
        return self._class_name


class LootConeCandidateTest(unittest.TestCase):
    """T10: translated real LootConeCandidate body with doubles (kills M1, M2)."""

    def test_t10_loot_cone_candidate_real_body(self) -> None:
        bridge = _source(SERVER_BRIDGE_PATH)
        ns: dict[str, object] = {
            "InventoryItem": SimpleNamespace(
                Cast=lambda obj: obj if isinstance(obj, _FakeItem) else None
            ),
        }
        fn = translate(
            bridge,
            SIG_CANDIDATE,
            ns,
            class_types=("Object", "InventoryItem"),
        )
        loose = _FakeItem("BakedBeansCan", (10.0, 0.0, 22.0), parent=None)
        owned = _FakeItem("BakedBeansCan", (10.0, 1.0, 22.0), parent=object())
        # Non-InventoryItem with null parent so removing the cast returns it (kills M2).
        non_item = _FakeNonItem((10.0, 0.0, 22.0), parent=None)

        self.assertIs(fn(loose), loose)
        # Kills M1: item with non-null hierarchy parent must be rejected.
        self.assertIsNone(fn(owned))
        # Kills M2: non-InventoryItem must be rejected.
        self.assertIsNone(fn(non_item))
        self.assertIsNone(fn(None))


class DispatchLootLosConeTest(unittest.TestCase):
    """T11: translated real DispatchLootLosCone + source text & no-write checks."""

    def setUp(self) -> None:
        self.bridge = _source(SERVER_BRIDGE_PATH)
        self.messages = _source(MESSAGES_PATH)

    def _build_dispatch(
        self,
        players: list[object],
        world_objects: list[object],
        *,
        tick_ms: int = 42000,
        ray_handler: object = None,
    ) -> tuple[object, dict[str, object]]:
        clock_reads = [0]
        query_calls: list[tuple[EnforceVector, float]] = []
        ray_calls: list[tuple[EnforceVector, EnforceVector, object, object]] = []

        def get_time() -> int:
            clock_reads[0] += 1
            return tick_ms

        def get_objects(
            pos: EnforceVector,
            radius: float,
            out_objects: _LootConeArray,
            out_cargos: _LootConeArray,
        ) -> None:
            # Verify both arrays were cleared right before GetObjectsAtPosition3D.
            if out_objects.Count() != 0 or out_cargos.Count() != 0:
                raise AssertionError("query arrays not cleared before GetObjectsAtPosition3D")
            query_calls.append((EnforceVector(pos), radius))
            for obj in world_objects:
                out_objects.Insert(obj)
            out_cargos.Insert(object())

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

        # Seed both scratch arrays with stale entries to prove Clear() runs before query.
        ready_objects = _LootConeArray([object()])
        ready_cargos = _LootConeArray([object()])

        ns: dict[str, object] = {
            "_S": _LootConeString,
            "array": _LootConeArray,
            "LOOT_CONE_SCHEMA": _LootConeString.of(loot_los_cone.SCHEMA),
            "LOOT_CONE_RADIUS_MAX": loot_los_cone.RADIUS_MAX_M,
            "LOS_CONE_RADIUS_MAX": 50.0,
            "LOS_CONE_HALF_YAW_MAX_DEG": loot_los_cone.HALF_YAW_MAX_DEG,
            "LOOT_CONE_OBSERVERS_MAX": loot_los_cone.OBSERVERS_MAX,
            "LOOT_CONE_RAYS_PER_OBSERVER_MAX": loot_los_cone.RAYS_PER_OBSERVER_MAX,
            "LOS_CONE_RAYS_PER_OBSERVER_MAX": 8,
            "LOOT_CONE_ROWS_PER_OBSERVER_MAX": loot_los_cone.ROWS_PER_OBSERVER_MAX,
            "VISIBILITY_HEAD_BONE": _LootConeString.of("Head"),
            "m_ReadyObjects": ready_objects,
            "m_ReadyProxyCargos": ready_cargos,
            "IsFiniteFloat": math.isfinite,
            "GetGame": lambda: SimpleNamespace(
                GetTime=get_time, GetObjectsAtPosition3D=get_objects
            ),
            "PlayerBase": SimpleNamespace(
                Cast=lambda obj: obj if isinstance(obj, _FakePlayer) else None
            ),
            "InventoryItem": SimpleNamespace(
                Cast=lambda obj: obj if isinstance(obj, _FakeItem) else None
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
            "MCPLootLosConeRow",
            "MCPLootLosConeObserver",
            "MCPLootLosConeRead",
            "MCPRaycastHit",
        ):
            fake_class(self.messages, cls_name, ns)

        translate(self.bridge, SIG_YAW_OFF, ns)
        translate(self.bridge, SIG_IN_CONE, ns)
        translate(self.bridge, SIG_NEAREST, ns, class_types=("array",))
        translate(self.bridge, SIG_GAP_KIND, ns)
        translate(self.bridge, SIG_CANDIDATE, ns, class_types=("Object", "InventoryItem"))
        # enforce_subset_helpers.translate only supports out_functions on bare
        # non-void assignment calls, not void method calls like
        # candItem.GetNetworkID(candNetLow, candNetHigh);. Bind that one
        # translated call so the double returns (low, high) into the locals.
        dispatch_name, dispatch_code = python_source(
            self.bridge,
            SIG_DISPATCH,
            class_types=(
                "MCPCommand",
                "MCPResult",
                "MCPArgs",
                "MCPLootLosConeRead",
                "MCPLootLosConeObserver",
                "MCPLootLosConeRow",
                "MCPRaycastHit",
                "PlayerBase",
                "HumanInputController",
                "HumanCommandWeapons",
                "InventoryItem",
                "array",
            ),
            members=("m_ReadyObjects", "m_ReadyProxyCargos"),
        )
        net_id_matches = list(_NET_ID_CALL_RE.finditer(dispatch_code))
        if len(net_id_matches) != 1:
            raise AssertionError(
                f"expected 1 GetNetworkID call in {dispatch_name}, got {len(net_id_matches)}"
            )
        dispatch_code = _NET_ID_CALL_RE.sub(
            r"\g<indent>\g<low>, \g<high> = \g<recv>.GetNetworkID(\g<low>, \g<high>)",
            dispatch_code,
            count=1,
        )
        exec(compile(dispatch_code, f"<enforce:{dispatch_name}>", "exec"), ns)
        dispatch = ns[dispatch_name]
        return dispatch, {
            "clock_reads": clock_reads,
            "query_calls": query_calls,
            "ray_calls": ray_calls,
        }

    def _run_dispatch(
        self,
        dispatch: object,
        args: object,
    ) -> tuple[bool, SimpleNamespace]:
        command = SimpleNamespace(args=args)
        result = SimpleNamespace(ok=False, error=_LootConeString.of(""), loot_los_cone=None)
        post_now = dispatch(command, result)  # type: ignore[operator]
        return post_now, result

    def test_t11_dispatch_arg_rejections_real_body(self) -> None:
        dispatch, _meta = self._build_dispatch([], [])

        def make_args(
            uids: list[str] | None,
            radius: float = 8.0,
            half_yaw_deg: float = 10.0,
        ) -> SimpleNamespace:
            arr = (
                None
                if uids is None
                else _LootConeArray([_LootConeString.of(u) for u in uids])
            )
            return SimpleNamespace(uids=arr, radius=radius, half_yaw_deg=half_yaw_deg)

        # Kills M8: radius=15.01 must be refused by DispatchLootLosCone.
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
            ("radius_over", make_args(["u1"], radius=15.01)),
            ("radius_nan", make_args(["u1"], radius=math.nan)),
            ("radius_inf", make_args(["u1"], radius=math.inf)),
            ("half_yaw_zero", make_args(["u1"], half_yaw_deg=0.0)),
            ("half_yaw_neg", make_args(["u1"], half_yaw_deg=-1.0)),
            ("half_yaw_over", make_args(["u1"], half_yaw_deg=30.01)),
            ("half_yaw_nan", make_args(["u1"], half_yaw_deg=math.nan)),
            ("half_yaw_inf", make_args(["u1"], half_yaw_deg=math.inf)),
        )
        for label, bad_args in bad_cases:
            with self.subTest(case=label):
                post_now, result = self._run_dispatch(dispatch, bad_args)
                self.assertIs(post_now, True)
                self.assertIs(result.ok, False)
                self.assertEqual(result.error.text, "bad_args")

    def test_t11_dispatch_filtering_ordering_rays_and_budget_real_body(self) -> None:
        obs_a = _FakePlayer("obs-a", (10.0, 1.7, 20.0), heading_rad=0.0, pitch_deg=6.0)
        obs_dead = _FakePlayer("obs-dead", (0.0, 1.7, 0.0), alive=False)
        obs_no_hic = _FakePlayer("obs-nohic", (10.0, 1.7, 20.0), heading_rad=None)

        net_id_log: list[object] = []
        non_item = _FakeNonItem((10.0, 0.0, 23.0), parent=None)
        owned_item = _FakeItem("HeldCan", (10.0, 1.2, 22.0), parent=obs_a, net_id_log=net_id_log)
        side_item = _FakeItem("SideCan", (14.0, 1.7, 20.0), net_id_log=net_id_log)
        edge_radius_item = _FakeItem("EdgeRadiusCan", (10.0, 1.7, 28.0), net_id_log=net_id_log)

        item_1 = _FakeItem(
            "Can_01", (10.0, 0.2, 22.0), net_id_log=net_id_log, net_low=101, net_high=11
        )
        item_2 = _FakeItem(
            "",
            (10.0, 1.7, 23.0),
            class_name="FallbackItem",
            net_id_log=net_id_log,
            net_low=202,
            net_high=-22,
        )
        item_3 = _FakeItem(
            "Can_03", (10.0, 1.7, 24.0), net_id_log=net_id_log, net_low=-303, net_high=33
        )
        item_4 = _FakeItem(
            "Can_04", (10.0, 1.7, 25.0), net_id_log=net_id_log, net_low=404, net_high=44
        )
        item_5 = _FakeItem(
            "Can_05", (10.0, 1.7, 26.0), net_id_log=net_id_log, net_low=505, net_high=-55
        )
        item_6 = _FakeItem(
            "Can_06", (10.0, 1.7, 27.0), net_id_log=net_id_log, net_low=606, net_high=66
        )

        world_objects = [
            obs_a,
            non_item,
            owned_item,
            side_item,
            edge_radius_item,
            item_5,
            item_2,
            item_6,
            item_1,
            item_4,
            item_3,
        ]

        ray_seq = [0]

        def ray_handler(
            _from_pos: EnforceVector,
            _to_pos: EnforceVector,
            _observer: object,
            hit: object,
        ) -> None:
            idx = ray_seq[0]
            ray_seq[0] += 1
            if idx == 0:
                # Object hit on nearest item (Can_01 at (10.0, 0.2, 22.0)): clearance = 1.0 m.
                hit.hit = True  # type: ignore[attr-defined]
                hit.pos.Insert(10.0)  # type: ignore[attr-defined]
                hit.pos.Insert(0.8)  # type: ignore[attr-defined]
                hit.pos.Insert(21.2)  # type: ignore[attr-defined]
                hit.distance = 1.5  # type: ignore[attr-defined]
                hit.object_type = _LootConeString.of("Land_Wall")  # type: ignore[attr-defined]
                hit.object_class = _LootConeString.of("HouseNoDestruct")  # type: ignore[attr-defined]
            elif idx == 1:
                # Terrain hit (no object_type/object_class): must not inherit idx 0's object.
                hit.hit = True  # type: ignore[attr-defined]
                hit.pos.Insert(10.0)  # type: ignore[attr-defined]
                hit.pos.Insert(1.7)  # type: ignore[attr-defined]
                hit.pos.Insert(22.0)  # type: ignore[attr-defined]
                hit.distance = 2.0  # type: ignore[attr-defined]
            else:
                hit.hit = False  # type: ignore[attr-defined]

        dispatch, meta = self._build_dispatch(
            [obs_a, obs_dead, obs_no_hic],
            world_objects,
            tick_ms=55000,
            ray_handler=ray_handler,
        )
        args = SimpleNamespace(
            uids=_LootConeArray(
                [
                    _LootConeString.of("obs-a"),
                    _LootConeString.of("obs-dead"),
                    _LootConeString.of("obs-offline"),
                    _LootConeString.of("obs-nohic"),
                ]
            ),
            radius=8.0,
            half_yaw_deg=10.0,
        )
        post_now, result = self._run_dispatch(dispatch, args)
        self.assertIs(post_now, True)
        self.assertIs(result.ok, True)
        self.assertEqual(meta["clock_reads"], [1])

        # One GetObjectsAtPosition3D per living observer with observer head and radius.
        query_calls = meta["query_calls"]
        self.assertEqual(len(query_calls), 2)  # type: ignore[arg-type]
        for q_pos, q_radius in query_calls:  # type: ignore[attr-defined]
            self.assertEqual(list(q_pos), [10.0, 1.7, 20.0])
            self.assertEqual(q_radius, 8.0)

        read = result.loot_los_cone
        self.assertEqual(read.schema.text, loot_los_cone.SCHEMA)
        self.assertEqual(read.tick_ms, 55000)
        self.assertIs(read.pitch_filter_applied, False)
        self.assertIs(read.truncated, False)

        self.assertEqual(
            [o.uid.text for o in read.observers],
            ["obs-a", "obs-dead", "obs-offline", "obs-nohic"],
        )
        sum_a, sum_dead, sum_off, sum_nohic = list(read.observers)
        self.assertIs(sum_a.found, True)
        self.assertIs(sum_a.yaw_deg_available, True)
        self.assertEqual(sum_a.yaw_deg, 0.0)
        self.assertEqual(sum_a.scanned, 11)
        self.assertEqual(sum_a.in_radius, 7)
        self.assertEqual(sum_a.in_cone, 6)
        # Kills M3: loot ray budget is 4, not players' 8.
        self.assertEqual(sum_a.rays, 4)
        self.assertEqual(sum_a.rows, 6)

        self.assertIs(sum_dead.found, False)
        self.assertEqual(
            (sum_dead.scanned, sum_dead.in_radius, sum_dead.in_cone, sum_dead.rays, sum_dead.rows),
            (0, 0, 0, 0, 0),
        )
        self.assertIs(sum_off.found, False)
        self.assertEqual(
            (sum_off.scanned, sum_off.in_radius, sum_off.in_cone, sum_off.rays, sum_off.rows),
            (0, 0, 0, 0, 0),
        )
        self.assertIs(sum_nohic.found, True)
        self.assertIs(sum_nohic.yaw_deg_available, False)
        self.assertEqual(sum_nohic.scanned, 11)
        self.assertEqual(sum_nohic.in_radius, 7)
        self.assertEqual((sum_nohic.in_cone, sum_nohic.rays, sum_nohic.rows), (0, 0, 0))

        # Nearest-first ordering; GetType() falls back to ClassName(); excluded items absent.
        emitted_types = [r.item_type.text for r in read.rows]
        self.assertEqual(
            emitted_types,
            ["Can_01", "FallbackItem", "Can_03", "Can_04", "Can_05", "Can_06"],
        )
        expected_net_ids = [
            ("Can_01", 101, 11),
            ("FallbackItem", 202, -22),
            ("Can_03", -303, 33),
            ("Can_04", 404, 44),
            ("Can_05", 505, -55),
            ("Can_06", 606, 66),
        ]
        self.assertEqual(
            [(r.item_type.text, r.net_low, r.net_high) for r in read.rows],
            expected_net_ids,
        )
        for excluded in ("HeldCan", "SideCan", "EdgeRadiusCan"):
            self.assertNotIn(excluded, emitted_types)
        self.assertEqual(len(net_id_log), 6)

        # Kills M9: row.item_pos must be the item's position, distinct from observer_head.
        row0 = read.rows.Get(0)
        self.assertEqual(list(row0.observer_head), [10.0, 1.7, 20.0])
        self.assertEqual(list(row0.item_pos), [10.0, 0.2, 22.0])
        self.assertNotEqual(list(row0.item_pos), list(row0.observer_head))
        self.assertAlmostEqual(row0.distance_m, 2.5, places=5)
        self.assertIs(row0.ray_hit, True)
        self.assertIs(row0.hit_pos_available, True)
        self.assertAlmostEqual(row0.clearance_m, 1.0, places=5)
        self.assertIs(row0.hit_type_available, True)
        self.assertEqual(row0.hit_type.text, "Land_Wall")
        self.assertIs(row0.hit_class_available, True)
        self.assertEqual(row0.hit_class.text, "HouseNoDestruct")

        # First 4 rows got rays with fresh MCPRaycastHit instances; rows 4..5 got budget gap (kills M3).
        ray_calls = meta["ray_calls"]
        self.assertEqual(len(ray_calls), 4)  # type: ignore[arg-type]
        self.assertEqual(len({id(call[3]) for call in ray_calls}), 4)  # type: ignore[attr-defined]
        for from_pos, _to_pos, observer, _hit in ray_calls:  # type: ignore[attr-defined]
            self.assertEqual(list(from_pos), [10.0, 1.7, 20.0])
            self.assertIs(observer, obs_a)
        self.assertEqual(list(ray_calls[0][1]), [10.0, 0.2, 22.0])  # type: ignore[index]

        row1 = read.rows.Get(1)
        self.assertIs(row1.ray_hit, True)
        self.assertAlmostEqual(row1.clearance_m, 1.0, places=5)
        self.assertIs(row1.hit_type_available, False)
        self.assertIs(row1.hit_class_available, False)

        for idx, want_gap in ((2, ""), (3, ""), (4, "ray_budget_exceeded"), (5, "ray_budget_exceeded")):
            r = read.rows.Get(idx)
            self.assertEqual(r.gap_kind.text, want_gap)
            self.assertIs(r.hit_pos_available, False)
            self.assertIs(r.hit_distance_m_available, False)
            self.assertIs(r.clearance_m_available, False)
            self.assertIs(r.hit_type_available, False)
            self.assertIs(r.hit_class_available, False)

        # End-to-end: wire payload passes Python normalize_bridge_result cleanly.
        wire_payload = {"ok": 1, FIELD: _to_wire_value(read)}
        normalized = loot_los_cone.normalize_bridge_result(wire_payload)
        self.assertEqual(len(normalized["evidence"]["rows"]), 6)
        self.assertEqual(
            [
                (r["item_type"], r["net_low"], r["net_high"])
                for r in normalized["evidence"]["rows"]
            ],
            expected_net_ids,
        )
        self.assertIs(normalized["evidence"]["truncated"], False)

    def test_t11_dispatch_two_living_observers_reset_rows_and_rays_real_body(self) -> None:
        obs_a = _FakePlayer("obs-a", (10.0, 1.7, 20.0), heading_rad=0.0, pitch_deg=4.0)
        obs_b = _FakePlayer(
            "obs-b", (200.0, 1.7, 200.0), heading_rad=-math.pi / 2.0, pitch_deg=-3.0
        )
        items_a = [
            _FakeItem(
                f"A_{i:02d}",
                (10.0, 1.7, 21.0 + float(i)),
                net_low=1000 + i,
                net_high=10 + i,
            )
            for i in range(1, 7)
        ]
        items_b = [
            _FakeItem(
                f"B_{i:02d}",
                (200.5 + float(i), 1.7, 200.0),
                net_low=2000 + i,
                net_high=20 + i,
            )
            for i in range(1, 7)
        ]
        # Scramble both sets so nearest-first selection runs independently per observer.
        world_objects = [
            items_a[3],
            items_a[1],
            items_a[5],
            items_a[0],
            items_a[4],
            items_a[2],
            items_b[4],
            items_b[1],
            items_b[5],
            items_b[0],
            items_b[3],
            items_b[2],
        ]
        dispatch, meta = self._build_dispatch(
            [obs_a, obs_b], world_objects, tick_ms=90000
        )
        args = SimpleNamespace(
            uids=_LootConeArray(
                [_LootConeString.of("obs-a"), _LootConeString.of("obs-b")]
            ),
            radius=8.0,
            half_yaw_deg=10.0,
        )
        post_now, result = self._run_dispatch(dispatch, args)
        self.assertIs(post_now, True)
        self.assertIs(result.ok, True)
        self.assertEqual(meta["clock_reads"], [1])

        query_calls = meta["query_calls"]
        self.assertEqual(len(query_calls), 2)  # type: ignore[arg-type]
        self.assertEqual(list(query_calls[0][0]), [10.0, 1.7, 20.0])  # type: ignore[index]
        self.assertEqual(list(query_calls[1][0]), [200.0, 1.7, 200.0])  # type: ignore[index]

        read = result.loot_los_cone
        self.assertIs(read.truncated, False)
        self.assertEqual(read.observers.Count(), 2)
        sum_a, sum_b = list(read.observers)
        self.assertEqual(sum_a.uid.text, "obs-a")
        self.assertIs(sum_a.found, True)
        self.assertIs(sum_a.yaw_deg_available, True)
        self.assertAlmostEqual(sum_a.yaw_deg, 0.0, places=5)
        self.assertEqual(
            (sum_a.scanned, sum_a.in_radius, sum_a.in_cone, sum_a.rays, sum_a.rows),
            (12, 6, 6, 4, 6),
        )
        self.assertEqual(sum_b.uid.text, "obs-b")
        self.assertIs(sum_b.found, True)
        self.assertIs(sum_b.yaw_deg_available, True)
        self.assertAlmostEqual(sum_b.yaw_deg, 90.0, places=5)
        self.assertEqual(
            (sum_b.scanned, sum_b.in_radius, sum_b.in_cone, sum_b.rays, sum_b.rows),
            (12, 6, 6, 4, 6),
        )

        self.assertEqual(read.rows.Count(), 12)
        rows_a = [r for r in read.rows if r.uid.text == "obs-a"]
        rows_b = [r for r in read.rows if r.uid.text == "obs-b"]
        self.assertEqual(len(rows_a), 6)
        self.assertEqual(len(rows_b), 6)
        self.assertEqual(
            [(r.item_type.text, r.net_low, r.net_high, r.gap_kind.text) for r in rows_a],
            [
                ("A_01", 1001, 11, ""),
                ("A_02", 1002, 12, ""),
                ("A_03", 1003, 13, ""),
                ("A_04", 1004, 14, ""),
                ("A_05", 1005, 15, "ray_budget_exceeded"),
                ("A_06", 1006, 16, "ray_budget_exceeded"),
            ],
        )
        self.assertEqual(
            [(r.item_type.text, r.net_low, r.net_high, r.gap_kind.text) for r in rows_b],
            [
                ("B_01", 2001, 21, ""),
                ("B_02", 2002, 22, ""),
                ("B_03", 2003, 23, ""),
                ("B_04", 2004, 24, ""),
                ("B_05", 2005, 25, "ray_budget_exceeded"),
                ("B_06", 2006, 26, "ray_budget_exceeded"),
            ],
        )

        ray_calls = meta["ray_calls"]
        self.assertEqual(len(ray_calls), 8)  # type: ignore[arg-type]
        self.assertEqual(len({id(call[3]) for call in ray_calls}), 8)  # type: ignore[attr-defined]
        for call in ray_calls[:4]:  # type: ignore[index]
            self.assertEqual(list(call[0]), [10.0, 1.7, 20.0])
            self.assertIs(call[2], obs_a)
        for call in ray_calls[4:]:  # type: ignore[index]
            self.assertEqual(list(call[0]), [200.0, 1.7, 200.0])
            self.assertIs(call[2], obs_b)

        normalized = loot_los_cone.normalize_bridge_result(
            {"ok": 1, FIELD: _to_wire_value(read)}
        )
        self.assertEqual(len(normalized["evidence"]["rows"]), 12)
        self.assertIs(normalized["loot_los_cone"]["truncated"], False)
        self.assertIs(normalized["evidence"]["truncated"], False)

    def test_t11_dispatch_truncates_past_16_rows_per_observer_real_body(self) -> None:
        obs = _FakePlayer("obs-a", (0.0, 1.7, 0.0), heading_rad=0.0)
        items = [
            _FakeItem(f"Can_{i:02d}", (0.0, 1.7, 1.0 + i * 0.3))
            for i in range(19)
        ]
        dispatch, meta = self._build_dispatch([obs], items, tick_ms=70000)
        args = SimpleNamespace(
            uids=_LootConeArray([_LootConeString.of("obs-a")]),
            radius=8.0,
            half_yaw_deg=10.0,
        )
        post_now, result = self._run_dispatch(dispatch, args)
        self.assertIs(post_now, True)
        self.assertIs(result.ok, True)
        read = result.loot_los_cone
        self.assertIs(read.truncated, True)
        summary = read.observers.Get(0)
        self.assertEqual(summary.scanned, 19)
        self.assertEqual(summary.in_radius, 19)
        self.assertEqual(summary.in_cone, 19)
        self.assertEqual(summary.rays, 4)
        self.assertEqual(summary.rows, 16)
        self.assertEqual(read.rows.Count(), 16)
        self.assertEqual(len(meta["ray_calls"]), 4)  # type: ignore[arg-type]

        normalized = loot_los_cone.normalize_bridge_result(
            {"ok": 1, FIELD: _to_wire_value(read)}
        )
        self.assertIs(normalized["loot_los_cone"]["truncated"], True)
        self.assertIs(normalized["evidence"]["truncated"], True)

    def test_t11_dispatch_cardinal_headings_real_body(self) -> None:
        obs_east = _FakePlayer("obs-east", (100.0, 1.7, 200.0), heading_rad=-math.pi / 2.0)
        item_east = _FakeItem("EastCan", (105.0, 1.7, 200.0))
        item_west = _FakeItem("WestCan", (95.0, 1.7, 200.0))
        dispatch, meta = self._build_dispatch(
            [obs_east], [item_east, item_west], tick_ms=80000
        )
        args = SimpleNamespace(
            uids=_LootConeArray([_LootConeString.of("obs-east")]),
            radius=8.0,
            half_yaw_deg=10.0,
        )
        post_now, result = self._run_dispatch(dispatch, args)
        self.assertIs(post_now, True)
        self.assertIs(result.ok, True)
        read = result.loot_los_cone
        summary = read.observers.Get(0)
        self.assertAlmostEqual(summary.yaw_deg, 90.0, places=5)
        self.assertEqual((summary.scanned, summary.in_radius, summary.in_cone, summary.rays, summary.rows), (2, 2, 1, 1, 1))
        row = read.rows.Get(0)
        self.assertEqual(row.item_type.text, "EastCan")
        self.assertAlmostEqual(row.yaw_off_deg, 0.0, places=4)
        self.assertAlmostEqual(row.distance_m, 5.0, places=4)
        self.assertEqual(len(meta["ray_calls"]), 1)  # type: ignore[arg-type]

        right_offset_deg = 5.0
        cardinal_cases = (
            ("south_180", -math.pi, 180.0, 180.0 + right_offset_deg),
            ("west_270", math.pi / 2.0, 270.0, 270.0 + right_offset_deg),
        )
        for label, heading_rad, expected_yaw_deg, bearing_deg in cardinal_cases:
            with self.subTest(heading=label):
                bearing_rad = math.radians(bearing_deg)
                dx = 5.0 * math.sin(bearing_rad)
                dz = 5.0 * math.cos(bearing_rad)
                obs = _FakePlayer("obs-card", (100.0, 1.7, 200.0), heading_rad=heading_rad)
                cand = _FakeItem("RightCan", (100.0 + dx, 1.7, 200.0 + dz))
                dispatch_c, meta_c = self._build_dispatch([obs], [cand], tick_ms=81000)
                args_c = SimpleNamespace(
                    uids=_LootConeArray([_LootConeString.of("obs-card")]),
                    radius=8.0,
                    half_yaw_deg=10.0,
                )
                post_c, res_c = self._run_dispatch(dispatch_c, args_c)
                self.assertIs(post_c, True)
                self.assertIs(res_c.ok, True)
                row_c = res_c.loot_los_cone.rows.Get(0)
                self.assertAlmostEqual(row_c.yaw_deg, expected_yaw_deg, places=5)
                self.assertAlmostEqual(row_c.yaw_off_deg, right_offset_deg, places=4)
                self.assertEqual(len(meta_c["ray_calls"]), 1)  # type: ignore[arg-type]

    def test_t11_dispatch_source_text_and_no_world_writes(self) -> None:
        dispatch = clean(method_body(self.bridge, SIG_DISPATCH))
        candidate = clean(method_body(self.bridge, SIG_CANDIDATE))

        # Single clock read, single spatial query, single ray helper call; no second cone/FOV/ray.
        self.assertEqual(dispatch.count("GetGame().GetTime()"), 1)
        self.assertEqual(dispatch.count("GetObjectsAtPosition3D("), 1)
        self.assertEqual(dispatch.count("LootConeCandidate("), 1)
        self.assertEqual(dispatch.count("LosConeCastRay("), 1)
        self.assertEqual(dispatch.count("PopulateRaycastRVProxy"), 0)
        self.assertEqual(dispatch.count("MCPRaycastValidation"), 0)
        self.assertEqual(dispatch.count("SceneGetEntitiesInBox"), 0)
        self.assertEqual(dispatch.count("IsInApproxFov"), 0)
        self.assertEqual(dispatch.count("HasLineOfSight"), 0)
        self.assertNotIn("LootConeYawOff", self.bridge)
        self.assertNotIn("LootConeInCone", self.bridge)
        self.assertNotIn("LootConeCastRay", self.bridge)

        # Pin the exact LosConeInCone block so candNetLow/candNetHigh cannot be
        # overwritten after candItem.GetNetworkID(candNetLow, candNetHigh).
        in_cone_body = _if_body(
            dispatch, "LosConeInCone(candDist, radius, yawOffDeg, halfYawDeg)"
        )
        self.assertEqual(
            _flat(in_cone_body),
            "obsSummary.in_cone = obsSummary.in_cone + 1; "
            "candNetLow = 0; candNetHigh = 0; "
            "candItem.GetNetworkID(candNetLow, candNetHigh); "
            "candType = candItem.GetType(); "
            'if (candType == "") { candType = candItem.ClassName(); } '
            "candNetLows.Insert(candNetLow); "
            "candNetHighs.Insert(candNetHigh); "
            "candTypes.Insert(candType); "
            "candPositions.Insert(candPos); "
            "candDistances.Insert(candDist); "
            "candYawOffs.Insert(yawOffDeg); "
            "candTaken.Insert(false);",
        )

        # Pin every step and row member write line by line.
        _in_order(
            self,
            dispatch,
            "obsPlayer = PlayerBase.Cast(FindHumanByUid(obsUid));",
            "if (obsPlayer && obsPlayer.IsAlive())",
            "obsHead = PlayerHeadPosition(obsPlayer);",
            "obsHeadBone = obsPlayer.GetBoneIndexByName(VISIBILITY_HEAD_BONE) >= 0;",
            "obsSummary.yaw_deg = MCPPlayerTrace.CompassDeg(hic.GetHeadingAngle());",
            "obsPitchDeg = weaponCmd.GetBaseAimingAngleUD();",
            "m_ReadyObjects.Clear();",
            "m_ReadyProxyCargos.Clear();",
            "GetGame().GetObjectsAtPosition3D(obsHead, radius, m_ReadyObjects, m_ReadyProxyCargos);",
            "obsSummary.scanned = m_ReadyObjects.Count();",
            "candItem = LootConeCandidate(m_ReadyObjects.Get(candIndex));",
            "candPos = candItem.GetPosition();",
            "candDist = vector.Distance(obsHead, candPos);",
            "if (candDist < radius)",
            "bearingDeg = Math.Atan2(dx, dz) * Math.RAD2DEG;",
            "yawOffDeg = LosConeYawOffDeg(bearingDeg, obsSummary.yaw_deg);",
            "if (LosConeInCone(candDist, radius, yawOffDeg, halfYawDeg))",
            "candItem.GetNetworkID(candNetLow, candNetHigh);",
            "candType = candItem.GetType();",
            "candType = candItem.ClassName();",
            "candNetLows.Insert(candNetLow);",
            "candNetHighs.Insert(candNetHigh);",
            "candTypes.Insert(candType);",
            "candPositions.Insert(candPos);",
            "candDistances.Insert(candDist);",
            "candYawOffs.Insert(yawOffDeg);",
            "bestIndex = LosConeNearest(candDistances, candTaken);",
            "gapKind = LosConeGapKind(rowPosition, LOOT_CONE_RAYS_PER_OBSERVER_MAX);",
            "row.uid = obsUid;",
            "row.tick_ms = tickMs;",
            "row.gap_kind = gapKind;",
            "row.net_low = candNetLows.Get(bestIndex);",
            "row.net_high = candNetHighs.Get(bestIndex);",
            "row.item_type = candTypes.Get(bestIndex);",
            "VectorToArray(rowItemPos, row.item_pos);",
            "VectorToArray(obsHead, row.observer_head);",
            "row.observer_head_bone = obsHeadBone;",
            "row.distance_m = candDistances.Get(bestIndex);",
            "row.yaw_deg = obsSummary.yaw_deg;",
            "row.yaw_off_deg = candYawOffs.Get(bestIndex);",
            "row.pitch_deg = obsPitchDeg;",
            "row.pitch_deg_available = obsPitchAvailable;",
            'if (gapKind == "")',
            "hit = new MCPRaycastHit();",
            "LosConeCastRay(obsHead, rowItemPos, obsPlayer, hit);",
            "row.ray_hit = hit.hit;",
            "VectorToArray(hitPos, row.hit_pos);",
            "row.hit_pos_available = true;",
            "row.hit_distance_m = hit.distance;",
            "row.hit_distance_m_available = true;",
            "row.clearance_m = vector.Distance(hitPos, rowItemPos);",
            "row.clearance_m_available = true;",
            "row.hit_type = hit.object_type;",
            "row.hit_type_available = true;",
            "row.hit_class = hit.object_class;",
            "row.hit_class_available = true;",
        )

        # No world writes or banned reads across new bridge methods.
        new_code = "\n".join((candidate, dispatch))
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
            "SceneGetEntitiesInBox",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, new_code)


class EnforceSourceContractTest(unittest.TestCase):
    """T12 (messages & prune), T15 (mirrors), T16 (compile hazards)."""

    def setUp(self) -> None:
        self.bridge = _source(SERVER_BRIDGE_PATH)
        self.bridge_code = clean(self.bridge)
        self.messages = _source(MESSAGES_PATH)

    def test_t12_messages_members_and_result_prune(self) -> None:
        self.assertEqual(
            class_members(self.messages, "MCPLootLosConeRow"),
            EXPECTED_ROW_MEMBERS,
        )
        self.assertEqual(
            class_members(self.messages, "MCPLootLosConeObserver"),
            EXPECTED_OBSERVER_MEMBERS,
        )
        self.assertEqual(
            class_members(self.messages, "MCPLootLosConeRead"),
            EXPECTED_READ_MEMBERS,
        )
        self.assertEqual(
            _flat(method_body(self.messages, "void MCPLootLosConeRow()")),
            "item_pos = new array<float>(); observer_head = new array<float>(); hit_pos = new array<float>();",
        )
        self.assertEqual(
            _flat(method_body(self.messages, "void MCPLootLosConeRead()")),
            "observers = new array<ref MCPLootLosConeObserver>(); rows = new array<ref MCPLootLosConeRow>();",
        )

        args_members = class_members(self.messages, "MCPArgs")
        for member in (
            ("float", "half_yaw_deg"),
            ("float", "radius"),
            ("ref array<string>", "uids"),
        ):
            with self.subTest(args_member=member):
                self.assertEqual(args_members.count(member), 1)
        for _t, name in args_members:
            self.assertNotIn("loot", name)

        result_members = class_members(self.messages, "MCPResult")
        players_los_member = ("ref MCPPlayersLosConeRead", "players_los_cone")
        loot_los_member = ("ref MCPLootLosConeRead", FIELD)
        self.assertEqual(result_members.count(loot_los_member), 1)
        self.assertEqual(
            result_members.index(loot_los_member),
            result_members.index(players_los_member) + 1,
        )

        self.assertIn(FIELD, result_prune.PRUNABLE_FIELDS)
        self.assertEqual(
            result_prune.PRUNABLE_FIELDS.index(FIELD),
            result_prune.PRUNABLE_FIELDS.index("players_los_cone") + 1,
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

    def test_t15_python_and_enforce_mirrors_match(self) -> None:
        self.assertIn(
            f'protected const string LOOT_CONE_SCHEMA = "{loot_los_cone.SCHEMA}";',
            self.bridge_code,
        )
        floats = {
            name: float(value)
            for name, value in re.findall(
                r"protected const float (LOOT_CONE_\w+) = (-?[0-9.]+);",
                self.bridge_code,
            )
        }
        self.assertEqual(
            floats,
            {
                "LOOT_CONE_RADIUS_MAX": loot_los_cone.RADIUS_MAX_M,
            },
        )
        self.assertIn(
            f"protected const float LOS_CONE_HALF_YAW_MAX_DEG = {loot_los_cone.HALF_YAW_MAX_DEG};",
            self.bridge_code,
        )
        ints = {
            name: int(value)
            for name, value in re.findall(
                r"protected const int (LOOT_CONE_\w+) = (\d+);",
                self.bridge_code,
            )
        }
        self.assertEqual(
            ints,
            {
                "LOOT_CONE_OBSERVERS_MAX": loot_los_cone.OBSERVERS_MAX,
                "LOOT_CONE_RAYS_PER_OBSERVER_MAX": loot_los_cone.RAYS_PER_OBSERVER_MAX,
                "LOOT_CONE_ROWS_PER_OBSERVER_MAX": loot_los_cone.ROWS_PER_OBSERVER_MAX,
            },
        )
        gap_returns = set(
            re.findall(r'return "(\w*)";', method_body(self.bridge, SIG_GAP_KIND))
        )
        self.assertEqual(gap_returns - {""}, loot_los_cone.GAP_KINDS)

    def test_t16_compile_hazards_locals_on_top_no_ternary(self) -> None:
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
    """T14: worst-case 64 wire rows + 16384 B overhead < half MAX_BODY_BYTES."""

    def test_t14_worst_case_payload_and_header_budget(self) -> None:
        num24 = "123456789012345678901234"
        longest_gap = max(loot_los_cone.GAP_KINDS, key=len)
        uid64 = "u" * loot_los_cone.UID_MAX_CHARS
        worst_row = {
            "uid": uid64,
            "tick_ms": num24,
            "gap_kind": longest_gap,
            "net_low": num24,
            "net_high": num24,
            "item_type": "I" * 64,
            "item_pos": [num24, num24, num24],
            "observer_head": [num24, num24, num24],
            "observer_head_bone": False,
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
            loot_los_cone.OBSERVERS_MAX * loot_los_cone.ROWS_PER_OBSERVER_MAX
        )
        self.assertEqual(max_rows, 64)
        total_bytes = (row_bytes + 1) * max_rows + 16384
        self.assertLess(total_bytes, loopback.MAX_BODY_BYTES // 2)

        worst_header = {
            "schema": loot_los_cone.SCHEMA,
            "tick_ms": num24,
            "radius_m": num24,
            "half_yaw_deg": num24,
            "pitch_filter_applied": False,
            "observers_max": loot_los_cone.OBSERVERS_MAX,
            "rays_per_observer_max": loot_los_cone.RAYS_PER_OBSERVER_MAX,
            "rows_per_observer_max": loot_los_cone.ROWS_PER_OBSERVER_MAX,
            "truncated": False,
            "observers": [
                {
                    "uid": uid64,
                    "found": True,
                    "yaw_deg_available": True,
                    "yaw_deg": num24,
                    "scanned": num24,
                    "in_radius": num24,
                    "in_cone": num24,
                    "rays": num24,
                    "rows": num24,
                }
                for _ in range(loot_los_cone.OBSERVERS_MAX)
            ],
        }
        header_bytes = len(
            json.dumps(worst_header, separators=(",", ":")).encode("utf-8")
        )
        self.assertLess(header_bytes, 16384)


class LootLosConeToolTest(unittest.IsolatedAsyncioTestCase):
    """T13: tool registration, closed schema, peer, error conversion, description."""

    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _m: None)
        )

    async def _description(self) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn(COMMAND, tools)
        return tools[COMMAND].description or ""

    async def test_t13_registered_closed_schema_peer_errors_and_description(self) -> None:
        self.assertIn(COMMAND, server._CLOSED_SCHEMA_TOOLS)
        description = await self._description()
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)

        for field_name in loot_los_cone.KIND.fields:
            with self.subTest(field=field_name):
                self.assertIn(field_name, description)
        for gap_name in loot_los_cone.GAP_KINDS:
            with self.subTest(gap=gap_name):
                self.assertIn(gap_name, description)
        for fragment in (
            "include_unverified",
            "server's heading for the player, not the client camera",
            "mouse movement or free look",
            "pitch_filter_applied=false",
            "up to 4 observers",
            "first 4 receive a raycast",
            "up to 16 rows per observer",
            "default 8.0 m",
            "max 15.0 m",
            "GetObjectsAtPosition3D",
            "InventoryItem",
            "GetHierarchyParent()",
            "GetPosition()",
            "scanned",
            "ObjIntersectView",
            "0.05 m radius",
            "no world writes",
            "truncated",
            "found=false",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)

        # Calls server peer with defaults and normalizes bridge result.
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
                "radius": 8.0,
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
                await self.app.call_tool(COMMAND, {"uids": ["u1"], "radius_m": 16.0})
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
            "bad_bridge_loot_cone_boolean: loot_los_cone.truncated",
        )


if __name__ == "__main__":
    unittest.main()
