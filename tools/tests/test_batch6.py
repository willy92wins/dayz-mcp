from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import core, loopback, server
from tests._addon_paths import addon_root


_GOOD_POS = [100.0, 10.0, 200.0]


def _function_source(text: str, name: str) -> str:
    needle = f"protected bool {name}("
    start = text.find(needle)
    if start < 0:
        return ""
    nxt = text.find("\n\tprotected ", start + len(needle))
    if nxt < 0:
        nxt = len(text)
    return text[start:nxt]


class Batch6Test(unittest.TestCase):
    def test_expected_bridge_version_is_10(self) -> None:
        # Bumped 9 -> 10 on 2026-08-29 with the T13/T14 client verbs; the
        # equality gate in core.py forces a matched daemon/PBO pair.
        self.assertEqual(core.EXPECTED_BRIDGE_VERSION, "10")

    def test_mcp_bridge_version_const_is_10(self) -> None:
        messages = (
            addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
        ).read_text(encoding="utf-8")
        self.assertIn('const string MCP_BRIDGE_VERSION = "10";', messages)
        self.assertNotIn('const string MCP_BRIDGE_VERSION = "9";', messages)

    def test_entities_query_in_server_commands(self) -> None:
        self.assertIn("entities_query", loopback.SERVER_COMMANDS)

    def test_entities_query_accepts_good_args(self) -> None:
        ok, err = loopback.validate_command_args(
            "entities_query",
            {"pos": list(_GOOD_POS), "radius": 25.0, "limit": 32},
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        ok_default, err_default = loopback.validate_command_args(
            "entities_query",
            {"pos": list(_GOOD_POS), "radius": 25.0},
        )
        self.assertTrue(ok_default)
        self.assertIsNone(err_default)
        ok_max, err_max = loopback.validate_command_args(
            "entities_query",
            {"pos": list(_GOOD_POS), "radius": 200.0, "limit": 128},
        )
        self.assertTrue(ok_max)
        self.assertIsNone(err_max)

    def test_entities_query_rejects_radius_non_positive(self) -> None:
        for radius in (0, 0.0, -1.0):
            ok, err = loopback.validate_command_args(
                "entities_query",
                {"pos": list(_GOOD_POS), "radius": radius, "limit": 32},
            )
            self.assertFalse(ok, msg=f"radius={radius!r} should fail")
            self.assertEqual(err, "bad_args")

    def test_entities_query_rejects_radius_over_200(self) -> None:
        ok, err = loopback.validate_command_args(
            "entities_query",
            {"pos": list(_GOOD_POS), "radius": 200.1, "limit": 32},
        )
        self.assertFalse(ok)
        self.assertEqual(err, "bad_args")

    def test_entities_query_rejects_limit_over_128(self) -> None:
        ok, err = loopback.validate_command_args(
            "entities_query",
            {"pos": list(_GOOD_POS), "radius": 10.0, "limit": 129},
        )
        self.assertFalse(ok)
        self.assertEqual(err, "bad_args")

    def test_entities_query_rejects_invalid_pos(self) -> None:
        cases = (
            {"pos": [1.0, 2.0], "radius": 10.0, "limit": 32},
            {"pos": "nope", "radius": 10.0, "limit": 32},
            {"pos": [1.0, float("nan"), 3.0], "radius": 10.0, "limit": 32},
            {"pos": [1.0, float("inf"), 3.0], "radius": 10.0, "limit": 32},
        )
        for args in cases:
            ok, err = loopback.validate_command_args("entities_query", args)
            self.assertFalse(ok, msg=f"args={args!r} should fail")
            self.assertEqual(err, "bad_args")

    def test_player_teleport_uid_is_optional(self) -> None:
        ok_legacy, err_legacy = loopback.validate_command_args(
            "player_teleport", {"pos": list(_GOOD_POS)}
        )
        self.assertTrue(ok_legacy)
        self.assertIsNone(err_legacy)
        ok_uid, err_uid = loopback.validate_command_args(
            "player_teleport", {"pos": list(_GOOD_POS), "uid": "76561198000000000"}
        )
        self.assertTrue(ok_uid)
        self.assertIsNone(err_uid)
        ok_bad, err_bad = loopback.validate_command_args(
            "player_teleport", {"pos": list(_GOOD_POS), "uid": 123}
        )
        self.assertFalse(ok_bad)
        self.assertEqual(err_bad, "bad_args")

    def test_inventory_give_uid_is_optional(self) -> None:
        ok_legacy, err_legacy = loopback.validate_command_args(
            "inventory_give", {"classname": "Apple", "dest": "inventory"}
        )
        self.assertTrue(ok_legacy)
        self.assertIsNone(err_legacy)
        ok_uid, err_uid = loopback.validate_command_args(
            "inventory_give",
            {"classname": "Apple", "dest": "inventory", "uid": "76561198000000000"},
        )
        self.assertTrue(ok_uid)
        self.assertIsNone(err_uid)

    def test_entities_query_tool_registered(self) -> None:
        app, _runtime = server.build_app(server.ServerConfig())
        tool = app._tool_manager.get_tool("entities_query")
        self.assertIsNotNone(tool)
        params = inspect.signature(tool.fn).parameters
        self.assertIn("pos", params)
        self.assertIn("radius", params)
        self.assertIn("limit", params)
        self.assertEqual(params["limit"].default, 32)
        properties = tool.parameters.get("properties", {})
        self.assertIn("pos", properties)
        self.assertIn("radius", properties)
        self.assertIn("limit", properties)
        required = tool.parameters.get("required") or []
        self.assertIn("pos", required)
        self.assertIn("radius", required)
        self.assertNotIn("limit", required)

    def test_uid_params_on_existing_tools(self) -> None:
        app, _runtime = server.build_app(server.ServerConfig())
        for name in ("player_teleport", "inventory_give", "notify_players"):
            tool = app._tool_manager.get_tool(name)
            self.assertIsNotNone(tool, msg=name)
            params = inspect.signature(tool.fn).parameters
            self.assertIn("uid", params, msg=name)
            self.assertEqual(params["uid"].default, "", msg=name)
            required = tool.parameters.get("required") or []
            self.assertNotIn("uid", required, msg=name)
            properties = tool.parameters.get("properties", {})
            self.assertIn("uid", properties, msg=name)

    def test_teleport_and_give_have_no_plugin_developer(self) -> None:
        bridge = (
            addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
        ).read_text(encoding="utf-8")
        teleport = _function_source(bridge, "DispatchPlayerTeleport")
        give = _function_source(bridge, "DispatchInventoryGive")
        self.assertTrue(teleport)
        self.assertTrue(give)
        self.assertNotIn("PluginDeveloper", teleport)
        self.assertNotIn("PluginDeveloper", give)
        self.assertIn("SetPosition", teleport)
        self.assertIn("CreateInInventory", give)
        flags_fn = _function_source(bridge, "IsAllowedSpawnFlags")
        self.assertIn(
            "ECE_INITAI | ECE_EQUIP_ATTACHMENTS | ECE_NOPERSISTENCY_WORLD | ECE_CREATEPHYSICS",
            flags_fn,
        )
        self.assertIn("DispatchEntitiesQuery", bridge)
