"""player_respawn can pin the survivor class (fb-20261112-154523-5ad8).

DispatchPlayerRespawn is translated with tests/enforce_subset_helpers.py and
run on fakes; the daemon, ingress, capability tables and descriptions are
checked through their public surfaces. Literal tuples below are pins, not
values read back from the code under test.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import bridge_readiness, loopback, result_prune, server
from dayz_mcp.bridge_readiness import (
    CLIENT_CAPABILITY_BITS,
    SERVER_ARG_CONTRACT,
    SERVER_CAPABILITY_BITS,
    _BRIDGE_COMMAND_TOOLS,
    _with_capability_comparison,
    compute_bridge_ready,
    encode_capability_mask,
    parse_capability_mask,
    server_arg_contract_hash,
)
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.result_prune import prune_unfilled_fields
from dayz_mcp.server import ServerConfig, ToolError, build_app
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census
from tests.enforce_subset_helpers import EnforceArray, EnforceString, class_members, method_body, translate
from tests.fence_helpers import INST_CLIENT, INST_SERVER, PID_CLIENT, PID_SERVER, bind_both_peers, bound_queue

COMMAND = "player_respawn_survivor"
TOOL = "player_respawn"
SIGNATURE = "protected bool DispatchPlayerRespawn(MCPCommand command, MCPResult result)"
SCRIPTS = addon_root() / "scripts" / "5_Mission"
BRIDGE_PATH = SCRIPTS / "MCPClientBridge.c"
MESSAGES_PATH = SCRIPTS / "MCPMessages.c"
SERVER_BRIDGE_PATH = SCRIPTS / "MCPBridge.c"
CHANGELOG = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
BRIDGE = BRIDGE_PATH.read_text(encoding="utf-8")
MESSAGES = MESSAGES_PATH.read_text(encoding="utf-8")
VERSION = f"{EXPECTED_BRIDGE_VERSION}~1.30"

VANILLA = (
    "SurvivorM_Mirek", "SurvivorM_Boris", "SurvivorM_Cyril", "SurvivorM_Denis",
    "SurvivorM_Elias", "SurvivorM_Francis", "SurvivorM_Guo", "SurvivorM_Hassan",
    "SurvivorM_Indar", "SurvivorM_Jose", "SurvivorM_Kaito", "SurvivorM_Lewis",
    "SurvivorM_Manua", "SurvivorM_Niki", "SurvivorM_Oliver", "SurvivorM_Peter",
    "SurvivorM_Quinn", "SurvivorM_Rolf", "SurvivorM_Seth", "SurvivorM_Taiki",
    "SurvivorF_Eva", "SurvivorF_Frida", "SurvivorF_Gabi", "SurvivorF_Helga",
    "SurvivorF_Irena", "SurvivorF_Judy", "SurvivorF_Keiko", "SurvivorF_Linda",
    "SurvivorF_Maria", "SurvivorF_Naomi", "SurvivorF_Baty",
)
BASE_CLIENT = (
    "action_cursor", "action_hold", "action_hold_cancel", "action_use",
    "action_use_component", "action_use_door", "action_use_target", "actor_emote",
    "anim_timeline", "camera_get", "camera_path_play", "camera_path_release",
    "camera_set", "camera_set_focus", "chat_read", "dev_free_camera_set",
    "engine_set", "gameplay_state", "hud_set", "input_describe", "input_trigger",
    "key_press", "lease_close_client", "player_look_at", "player_look_at_release",
    "player_move", "player_respawn", "player_trace", "player_view_set",
    "restore_gameplay", "ui_click", "ui_dialog", "ui_focus", "ui_pause",
    "ui_reload_layout", "ui_set_text", "ui_tree", "vehicle_control",
    "vehicle_get_in_client", "vehicle_release", "vehicle_telemetry",
    "vehicle_trace", "weapon_aim", "weapon_fire", "weapon_raise", "weapon_sights",
)
SERVER_BITS = (
    "action_cursor_ids", "bot_start", "bot_stop", "entities_query", "exec_enforce",
    "hands_attach", "hands_take", "infected_drive", "inventory_attach",
    "inventory_give", "inventory_move", "lease_close", "loadout_apply",
    "loadout_clear", "loadout_get", "nav_path_query", "notify_players",
    "object_anim", "object_delete", "object_doors", "object_inspect",
    "object_resolve", "object_set_health", "player_godmode", "player_heal",
    "player_kill", "player_teleport", "players_visibility", "query_all_players",
    "query_get_in_condition", "query_player_state", "scene_clear", "scene_entities",
    "scene_raycast", "server_perf", "surface_query", "survivor_drive",
    "survivor_drive_release", "telemetry_read", "vehicle_door", "vehicle_enter",
    "vehicle_prepare_fixture", "weapon_state", "world_spawn", "world_spawn_batch",
    "world_time_get", "world_time_set", "world_weather_set",
)
SERVER_CONTRACT = {
    "bot_start": ("action", "bot_ttl_s", "object_id"),
    "bot_stop": ("object_id",),
    "hands_attach": ("object_id", "slot", "uid"),
    "loadout_apply": ("loadout_items", "object_id", "uid"),
    "loadout_clear": ("object_id", "uid"),
    "loadout_get": ("object_id", "uid"),
    "nav_path_query": ("from", "snap_radius", "to"),
    "object_resolve": ("pos", "radius", "type"),
    "object_set_health": ("health01", "object_id", "pos", "slot", "type", "zone"),
    "player_kill": ("uid",),
    "players_kinematics": ("cursor", "limit", "max_samples", "mode", "sample_hz", "trace_id", "uids"),
    "players_visibility": ("pos", "radius"),
    "scene_clear": ("scene_id",),
    "scene_entities": ("scene_id",),
    "server_perf": ("window_s",),
    "survivor_drive": ("heading", "heading_set", "mode", "object_id", "speed", "speed_set"),
    "survivor_drive_release": ("command_id", "object_id"),
    "vehicle_enter": ("pos", "uid"),
    "vehicle_prepare_fixture": ("mode", "pos", "radius", "type"),
    "world_spawn": ("flags", "lifetime_s", "lifetime_s_set", "pos", "rotation", "scene_id", "type"),
    "world_spawn_batch": ("items", "scene_id"),
}


class _CharData:
    def __init__(self, log: list) -> None:
        self._log = log

    def SetCharacterType(self, value: Any) -> None:
        self._log.append(("SetCharacterType", value.text))

    def SetRandomCharacterForced(self, value: bool) -> None:
        self._log.append(("SetRandomCharacterForced", value))


class _Player:
    # Dead: the state vanilla respawns. A living, conscious, free player is
    # refused before anything changes (tests/test_player_respawn_alive.py).
    def __init__(self, log: list) -> None:
        self._log = log

    def IsAlive(self) -> bool:
        return False

    def IsUnconscious(self) -> bool:
        return False

    def IsRestrained(self) -> bool:
        return False

    def SimulateDeath(self, flag: bool) -> None:
        self._log.append(("SimulateDeath", flag))

    def ShowDeadScreen(self, *_args: Any) -> None:
        raise AssertionError("ShowDeadScreen is passed to Call, not called directly")


class _CallQueue:
    def __init__(self, log: list) -> None:
        self._log = log

    def Call(self, _fn: Any, *args: Any) -> None:
        self._log.append(("ShowDeadScreen",) + args)


class _Menu:
    def __init__(self, log: list) -> None:
        self._log = log

    def Close(self) -> None:
        self._log.append(("Close",))


class _UI:
    def __init__(self, menu: _Menu) -> None:
        self._menu = menu

    def GetMenu(self) -> _Menu:
        return self._menu


class _Mission:
    def __init__(self, log: list, mode: int) -> None:
        self._log = log
        self._mode = mode

    def GetRespawnModeClient(self) -> int:
        return self._mode

    def DestroyAllMenus(self) -> None:
        self._log.append(("DestroyAllMenus",))

    def SetPlayerRespawning(self, flag: bool) -> None:
        self._log.append(("SetPlayerRespawning", flag))

    def Continue(self) -> None:
        self._log.append(("Continue",))


class _Game:
    def __init__(self, char_data: _CharData, player: _Player, mission: _Mission, ui: _UI, available: Any, queue: _CallQueue) -> None:
        self._char_data = char_data
        self._player = player
        self._mission = mission
        self._ui = ui
        self._available = available
        self._queue = queue

    def GetMission(self) -> _Mission:
        return self._mission

    def GetUIManager(self) -> _UI:
        return self._ui

    def GetMenuDefaultCharacterData(self, _flag: bool) -> _CharData:
        return self._char_data

    def ListAvailableCharacters(self) -> Any:
        return self._available

    def RespawnPlayer(self) -> None:
        self._player._log.append(("RespawnPlayer",))

    def GetPlayer(self) -> _Player:
        return self._player

    def GetCallQueue(self, _category: int) -> _CallQueue:
        return self._queue


class _MissionGameplay:
    @staticmethod
    def Cast(value: Any) -> Any:
        return value


class _PlayerBase:
    @staticmethod
    def Cast(value: Any) -> Any:
        return value


class _Dummy:
    pass


_ARGS_NULL = object()


def _run(cmd: str, survivor: Any, available: Any, mode: int) -> tuple[Any, Any, list]:
    log: list = []
    char_data = _CharData(log)
    player = _Player(log)
    mission = _Mission(log, mode)
    game = _Game(char_data, player, mission, _UI(_Menu(log)), available, _CallQueue(log))
    namespace: dict[str, Any] = {
        "GetGame": lambda: game,
        "MissionGameplay": _MissionGameplay,
        "PlayerBase": _PlayerBase,
        "UIScriptedMenu": _Dummy,
        "UIManager": _Dummy,
        "MenuDefaultCharacterData": _Dummy,
        "GameConstants": SimpleNamespace(RESPAWN_MODE_CUSTOM=0),
        "CALL_CATEGORY_GUI": 1,
    }
    fn = translate(
        BRIDGE,
        SIGNATURE,
        namespace,
        class_types=("MissionGameplay", "array", "UIScriptedMenu", "UIManager", "MenuDefaultCharacterData", "PlayerBase"),
    )
    args = None if survivor is _ARGS_NULL else SimpleNamespace(survivor_type=EnforceString.of(survivor))
    command = SimpleNamespace(cmd=EnforceString.of(cmd), args=args)
    result = SimpleNamespace(ok=False, error=EnforceString(b""), requested=False)
    returned = fn(command, result)
    return returned, result, log


def _available(*names: str) -> EnforceArray:
    return EnforceArray(EnforceString.of(name) for name in names)


_TAIL = (
    ("RespawnPlayer",),
    ("SimulateDeath", True),
    ("ShowDeadScreen", True, 0),
    ("DestroyAllMenus",),
    ("SetPlayerRespawning", True),
    ("Continue",),
    ("Close",),
)


class TranslatedRespawnTest(unittest.TestCase):
    def test_pinned_listed_class_in_custom_mode_runs_todays_recipe(self) -> None:
        returned, result, log = _run(
            COMMAND, "SurvivorM_Manua", _available("SurvivorM_Manua", "SurvivorF_Eva"), 0
        )
        self.assertIs(returned, True)
        self.assertEqual((result.ok, result.requested, result.error.text), (True, True, ""))
        self.assertEqual(
            log,
            [("SetCharacterType", "SurvivorM_Manua"), ("SetRandomCharacterForced", False), *_TAIL],
        )

    def test_pinned_refusals_change_nothing(self) -> None:
        cases = (
            ("unavailable", "SurvivorF_Eva", _available("SurvivorM_Manua"), 0, "survivor_type_unavailable"),
            ("null list", "SurvivorF_Eva", None, 0, "survivor_type_unavailable"),
            ("mode random", "SurvivorM_Manua", _available("SurvivorM_Manua"), 1, "respawn_mode_random"),
            ("mode unknown", "SurvivorM_Manua", _available("SurvivorM_Manua"), -1, "respawn_mode_random"),
            ("empty", "", _available("SurvivorM_Manua"), 0, "bad_args"),
            ("no args", _ARGS_NULL, _available("SurvivorM_Manua"), 0, "bad_args"),
        )
        for label, survivor, available, mode, error in cases:
            with self.subTest(case=label):
                returned, result, log = _run(COMMAND, survivor, available, mode)
                self.assertIs(returned, True)
                self.assertEqual((result.ok, result.requested, result.error.text), (False, False, error))
                self.assertEqual(log, [])

    def test_default_respawn_forces_random_and_never_sets_type_whatever_mode(self) -> None:
        for mode in (0, 1, -1):
            with self.subTest(mode=mode):
                returned, result, log = _run("player_respawn", "", _available("SurvivorM_Manua"), mode)
                self.assertIs(returned, True)
                self.assertEqual((result.ok, result.requested), (True, True))
                self.assertEqual(log, [("SetRandomCharacterForced", True), *_TAIL])
                self.assertNotIn("SetCharacterType", [entry[0] for entry in log])


class EnforceSourceTest(unittest.TestCase):
    def test_dispatch_branch_census_and_mask(self) -> None:
        dispatch = method_body(BRIDGE, "protected void Dispatch(MCPCommand command)")
        plain = 'else if (command.cmd == "player_respawn")'
        pinned = 'else if (command.cmd == "player_respawn_survivor")'
        self.assertIn(plain, dispatch)
        self.assertIn(pinned, dispatch)
        self.assertLess(dispatch.index(plain), dispatch.index(pinned))
        pinned_branch = dispatch[dispatch.index(pinned):]
        self.assertIn("postNow = DispatchPlayerRespawn(command, result);", pinned_branch)
        self.assertIn(COMMAND, dispatch_census(BRIDGE))
        self.assertIn(COMMAND, announced_caps(BRIDGE))
        self.assertEqual(set(announced_caps(BRIDGE)), set(dispatch_census(BRIDGE)))
        found = re.findall(r'CLIENT_POLL_CAPS_MASK\s*=\s*"([^"]*)"', BRIDGE)
        self.assertEqual(found, ["1.1ffffffffffff"])
        names, reason = parse_capability_mask("client", found[0])
        self.assertEqual(reason, "ok")
        self.assertEqual(set(names or ()), set(announced_caps(BRIDGE)))
        self.assertEqual(encode_capability_mask("client", announced_caps(BRIDGE)), found[0])
        body = method_body(BRIDGE, SIGNATURE)
        self.assertIn(
            "MenuDefaultCharacterData charData = GetGame().GetMenuDefaultCharacterData(false);",
            body,
        )
        self.assertIn(
            "array<string> available = GetGame().ListAvailableCharacters();",
            body,
        )
        self.assertEqual(body.count("GetGame().RespawnPlayer();"), 1)
        self.assertIn('bool pinned = command.cmd == "player_respawn_survivor";', body)
        self.assertIn(("string", "survivor_type"), class_members(MESSAGES, "MCPArgs"))

    def test_capability_tables_are_append_only_and_server_tables_unchanged(self) -> None:
        self.assertEqual(CLIENT_CAPABILITY_BITS[:46], BASE_CLIENT)
        self.assertEqual(CLIENT_CAPABILITY_BITS[46], COMMAND)
        self.assertEqual(CLIENT_CAPABILITY_BITS[47:], ("ui_menu_set", "ui_map_set"))
        self.assertEqual(SERVER_CAPABILITY_BITS[:48], SERVER_BITS)
        self.assertEqual(SERVER_CAPABILITY_BITS[48:], ("player_set_stance", "players_kinematics"))
        self.assertEqual(SERVER_ARG_CONTRACT, SERVER_CONTRACT)
        self.assertEqual(server_arg_contract_hash(), "3b7a1b143153de71")
        self.assertEqual(bridge_readiness.EXPECTED_SERVER_ARG_CONTRACT_HASH, "3b7a1b143153de71")
        self.assertIn(
            'SERVER_ARG_CONTRACT_HASH = "3b7a1b143153de71"',
            SERVER_BRIDGE_PATH.read_text(encoding="utf-8"),
        )
        self.assertIsNone(_BRIDGE_COMMAND_TOOLS["client"][COMMAND])


def _state(*, live: bool = False, admin: bool = False) -> loopback.ServerState:
    state = loopback.ServerState("test-key", live_profile=live, live_admin_enabled=admin)
    bind_both_peers(state)
    return state


def _poll_client(state: loopback.ServerState, cm: str) -> None:
    status, body = state.record_poll(
        "client", VERSION, instance=INST_CLIENT, source_pid=PID_CLIENT, cm=cm
    )
    if status != 200:
        raise AssertionError(f"client poll refused: {status} {body}")


class CompatibilityTest(unittest.TestCase):
    def test_base_pbo_mask_decodes_without_new_command_and_still_matches(self) -> None:
        names, reason = parse_capability_mask("client", "1.3fffffffffff")
        self.assertEqual((names, reason), (BASE_CLIENT, "ok"))
        self.assertNotIn(COMMAND, names or ())
        # Bits 47-48 (ui_menu_set, ui_map_set) are required client commands,
        # so the PBO that must still match is one that lacks only bit 46.
        without_pinned = "1.1bfffffffffff"
        names, reason = parse_capability_mask("client", without_pinned)
        self.assertEqual(reason, "ok")
        self.assertEqual(names, tuple(n for n in CLIENT_CAPABILITY_BITS if n != COMMAND))
        state = _state()
        status, body = state.record_poll(
            "server", VERSION, instance=INST_SERVER, source_pid=PID_SERVER,
            cm="1.ffffffffffff", ach="3b7a1b143153de71",
        )
        self.assertEqual(status, 200, body)
        _poll_client(state, without_pinned)
        snapshot = state.status_snapshot()
        payload: dict[str, Any] = {}
        for peer, key in (("server", "server_peer"), ("client", "client_peer")):
            block = dict(snapshot["peers"][peer])
            block["version_state"] = "ok"
            payload[key] = block
        registered = frozenset(
            tool for mapping in _BRIDGE_COMMAND_TOOLS.values() for tool in mapping.values() if tool
        )
        compared = _with_capability_comparison(payload, registered)
        self.assertEqual(compared["client_peer"]["capabilities"]["state"], "match")
        self.assertEqual(compute_bridge_ready(compared), {"ready": True, "reason": "ready"})
        self.assertEqual(
            state.enqueue_command(COMMAND, {"survivor_type": "SurvivorM_Manua"}),
            (409, {"error": "bridge_capability_missing"}),
        )
        self.assertEqual(bound_queue(state, "client"), [])

    def test_variant_without_player_respawn_command_is_a_mismatch(self) -> None:
        announced = [name for name in announced_caps(BRIDGE) if name != "player_respawn"]
        self.assertIn(COMMAND, announced)
        registered = frozenset(
            tool for mapping in _BRIDGE_COMMAND_TOOLS.values() for tool in mapping.values() if tool
        )
        payload: dict[str, Any] = {
            "client_peer": {
                "capabilities": {
                    "state": "announced",
                    "announced_commands": announced,
                }
            }
        }
        compared = _with_capability_comparison(payload, registered)
        capabilities = compared["client_peer"]["capabilities"]
        self.assertEqual(capabilities["state"], "mismatch")
        self.assertEqual(
            capabilities["registered_without_announced_command"], ["player_respawn"]
        )

    def test_current_client_receives_the_pinned_command(self) -> None:
        state = _state()
        _poll_client(state, "1.1ffffffffffff")
        status, body = state.enqueue_command(COMMAND, {"survivor_type": "SurvivorM_Manua"})
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "client", COMMAND))

    def test_a_46_name_table_ignores_bit_46(self) -> None:
        original = bridge_readiness.CAPABILITY_MASK_TABLES["client"][1]
        bridge_readiness.CAPABILITY_MASK_TABLES["client"][1] = BASE_CLIENT
        try:
            self.assertEqual(parse_capability_mask("client", "1.7fffffffffff"), (BASE_CLIENT, "ok"))
        finally:
            bridge_readiness.CAPABILITY_MASK_TABLES["client"][1] = original
        self.assertEqual(bridge_readiness.CAPABILITY_MASK_TABLES["client"][1], CLIENT_CAPABILITY_BITS)
        self.assertEqual(loopback._CLIENT_ANNOUNCED_COMMANDS[COMMAND], "bridge_capability_missing")


class VanillaListAndIngressTest(unittest.TestCase):
    def test_the_vanilla_list_is_the_31_names_of_the_brief(self) -> None:
        self.assertEqual(loopback.VANILLA_SURVIVOR_TYPES, VANILLA)
        self.assertEqual(server.VANILLA_SURVIVOR_TYPES, VANILLA)
        self.assertEqual(len(VANILLA), 31)
        self.assertEqual(len(set(VANILLA)), 31)
        self.assertEqual(sum(name.startswith("SurvivorM_") for name in VANILLA), 20)
        self.assertEqual(sum(name.startswith("SurvivorF_") for name in VANILLA), 11)
        for name in VANILLA:
            with self.subTest(name=name):
                self.assertTrue(loopback.is_vanilla_survivor_type(name))
                self.assertEqual(
                    loopback.validate_command_args(COMMAND, {"survivor_type": name}), (True, None)
                )
        for bad in ("", " ", "SurvivorM_Mod", "survivorm_manua", "SurvivorM_Manua ", "Survivor M", "S" * 65, 1, None, ["SurvivorM_Manua"]):
            with self.subTest(value=bad):
                self.assertFalse(loopback.is_vanilla_survivor_type(bad))
                self.assertEqual(
                    loopback.validate_command_args(COMMAND, {"survivor_type": bad}), (False, "bad_args")
                )

    def test_ingress_refusals_and_server_peer(self) -> None:
        state = _state()
        for args in ({}, {"survivor_type": "SurvivorM_Manua", "extra": 1}, {"survivor_type": 1}, {"survivor_type": "SurvivorM_Mod"}):
            with self.subTest(args=args):
                self.assertEqual(loopback.validate_command_args(COMMAND, args), (False, "bad_args"))
                self.assertEqual(state.enqueue_command(COMMAND, args), (400, {"error": "bad_args"}))
        self.assertEqual(
            state.enqueue_command(COMMAND, {"survivor_type": "SurvivorM_Manua"}, peer="server"),
            (400, {"error": "bad_peer"}),
        )
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertEqual(
            loopback._COMMAND_ARG_SCHEMAS[COMMAND][0][0][0], frozenset({"survivor_type"})
        )


class LiveLeasePruneTest(unittest.TestCase):
    def test_live_profile_refuses_it_admin_on_or_off(self) -> None:
        for admin in (False, True):
            with self.subTest(admin=admin):
                self.assertEqual(
                    loopback.live_profile_refusal(COMMAND, enabled=True, admin_enabled=admin),
                    "live_profile_refused",
                )
                state = _state(live=True, admin=admin)
                self.assertEqual(
                    state.enqueue_command(COMMAND, {"survivor_type": "SurvivorM_Manua"}),
                    (403, {"error": "live_profile_refused"}),
                )

    def test_it_needs_a_lease_and_the_prune_keeps_requested_for_it(self) -> None:
        self.assertTrue(command_requires_lease(COMMAND))
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertEqual(
            result_prune.OWNED_SCALAR_FIELDS["requested"],
            frozenset({"player_respawn", COMMAND}),
        )
        kept = prune_unfilled_fields(COMMAND, {"ok": 1, "requested": True})
        self.assertEqual(kept.get("requested"), True)
        dropped = prune_unfilled_fields("player_trace", {"ok": 1, "requested": True})
        self.assertNotIn("requested", dropped)


def _error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = "Error executing tool player_respawn: "
    return message[len(wrapper):] if message.startswith(wrapper) else message


class ToolTest(unittest.IsolatedAsyncioTestCase):
    async def _app(self):
        app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn(TOOL, tools)
        return app, runtime, tools[TOOL]

    async def test_without_survivor_type_the_default_call_is_unchanged(self) -> None:
        app, runtime, _tool = await self._app()
        call = AsyncMock(return_value={"ok": 1, "requested": True})
        with patch.object(runtime, "call_bridge", new=call):
            await app.call_tool(TOOL, {"timeout_s": 1.0})
        call.assert_awaited_once_with("player_respawn", {}, "client", 1.0)

    async def test_each_vanilla_class_is_forwarded_on_the_variant_command(self) -> None:
        app, runtime, _tool = await self._app()
        for name in VANILLA:
            with self.subTest(name=name):
                call = AsyncMock(return_value={"ok": 1, "requested": True})
                with patch.object(runtime, "call_bridge", new=call):
                    await app.call_tool(TOOL, {"survivor_type": name, "timeout_s": 1.0})
                call.assert_awaited_once_with(COMMAND, {"survivor_type": name}, "client", 1.0)

    async def test_anything_else_is_unknown_survivor_type_and_nothing_is_sent(self) -> None:
        app, runtime, _tool = await self._app()
        for bad in ("", " ", "SurvivorM_Mod", "survivorm_manua", "SurvivorM_Manua ", "Survivor M", "S" * 65):
            with self.subTest(value=bad):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await app.call_tool(TOOL, {"survivor_type": bad, "timeout_s": 1.0})
                call.assert_not_awaited()
                text = _error_text(raised.exception)
                self.assertTrue(text.startswith("unknown_survivor_type"), text)
                for name in VANILLA:
                    self.assertIn(name, text)

    async def test_schema_and_descriptions(self) -> None:
        _app, _runtime, tool = await self._app()
        self.assertEqual(set(tool.inputSchema["properties"]), {"survivor_type", "timeout_s"})
        self.assertNotIn("survivor_type", tool.inputSchema.get("required") or [])
        description = tool.description or ""
        for phrase in (
            "Without survivor_type the respawn is random as before",
            "one of the 31 vanilla survivor classes",
            "respawn mode CUSTOM",
            "disableRespawnDialog false",
            "the client reads the mode it received when it connected",
            "which vanilla does not change during a mission",
            "no spawn-gear preset lists characterTypes",
            "unknown_survivor_type (not a vanilla survivor class; nothing is sent)",
            "survivor_type_unavailable (this game's survivor list lacks it)",
            "respawn_mode_random",
            "bridge_capability_missing (a client bridge older than this release; nothing is sent)",
            "read the body's class with loadout_get (type) after the respawn",
            "A storage reset gives a new character whose class can differ, which survivor_type pins",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)
        for name in VANILLA:
            self.assertIn(name, description)

    async def test_dayz_test_run_description_and_changelog(self) -> None:
        app, _runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        description = tools["dayz_test_run"].description or ""
        self.assertIn(
            "With storage_rotated true (storage_reset_notice mission_world_and_character_reset) "
            "the player's character is new and its survivor class can differ from the previous "
            "run's (loadout_get type says which); pin it with player_respawn(survivor_type=...) "
            "or dress a world_spawn'ed survivor by object_id.",
            description,
        )
        unreleased = CHANGELOG.split("## [Unreleased]", 1)[1].split("## [", 1)[0]
        added = unreleased.split("### Added", 1)[1].split("### ", 1)[0]
        self.assertIn("fb-20261112-154523-5ad8", added)
        self.assertIn("player_respawn(survivor_type", added)


if __name__ == "__main__":
    unittest.main()

