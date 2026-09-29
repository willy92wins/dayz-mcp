"""fb-20260927-171545-0063 part 1: weapon_state and hands_take.

Server read of the weapon in hands, plus a leased take-to-hands. The shot
counter and the predictive take live in Enforce; these tests pin the wire
contract and the source shape the game will compile. They do not launch DayZ.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.server import EXPECTED_SERVER_ARG_CONTRACT_HASH, LEASE_TOOL_LINE
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
WEAPON = addon_root() / "scripts" / "4_World" / "MCP_Weapon.c"
CLIENT = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


class WeaponStateIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")

    def test_read_is_server_and_not_leased(self) -> None:
        self.assertIn("weapon_state", loopback.SERVER_COMMANDS)
        self.assertNotIn("weapon_state", loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command("weapon_state"), "server")
        self.assertIn("weapon_state", READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease("weapon_state"))
        status, body = self.state.enqueue_command("weapon_state", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "server")
        status_uid, body_uid = self.state.enqueue_command(
            "weapon_state", {"uid": "76561198000000000"}
        )
        self.assertEqual(status_uid, 200)
        self.assertEqual(body_uid["cmd"], "weapon_state")

    def test_read_rejects_extra_keys(self) -> None:
        status, body = self.state.enqueue_command("weapon_state", {"object_id": 1})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_args"})


class HandsTakeIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_mutation_is_server_and_leased(self) -> None:
        self.assertIn("hands_take", loopback.SERVER_COMMANDS)
        self.assertNotIn("hands_take", loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command("hands_take"), "server")
        self.assertNotIn("hands_take", READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease("hands_take"))
        status, body = self.state.enqueue_command("hands_take", {"object_id": 3})
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "server")
        self.assertEqual(body["cmd"], "hands_take")

    def test_rejects_missing_and_non_positive_object_id(self) -> None:
        for args in ({}, {"object_id": 0}, {"object_id": True}, {"object_id": 1, "extra": 1}):
            with self.subTest(args=args):
                status, body = self.state.enqueue_command("hands_take", args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})


class WeaponHandsEnforceContractTest(unittest.TestCase):
    def test_shot_counter_calls_super_then_counts_on_the_server(self) -> None:
        source = WEAPON.read_text(encoding="utf-8")
        body = _method_body(source, "override void EEFired(")
        self.assertLess(body.index("super.EEFired("), body.index("m_MCPShotCount = m_MCPShotCount + 1"))
        self.assertIn("GetGame().IsServer()", body)
        self.assertNotIn("new ", body)
        self.assertNotIn("%", body)
        self.assertNotIn("SendInput", source)
        self.assertIn("PredictiveTakeEntityToHands(", source)
        self.assertIn("MCP_RPC_HANDS_TAKE", source)
        self.assertIn("rpc.Send(this, MCP_RPC_HANDS_TAKE, true, identity)", source)

    def test_server_dispatch_refuses_and_does_not_confirm(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        take_at = source.index("protected bool DispatchHandsTake(")
        state_at = source.index("protected bool DispatchWeaponState(")
        take = source[take_at:state_at]
        for token in (
            "object_id_unknown",
            "object_id_stale",
            "not_an_item",
            "already_in_hands",
            "not_reachable",
            "not_takeable",
            "cannot_take",
            "hands_blocked",
            "not_networked",
            "no_identity",
            "input_busy",
            "PredictiveTakeEntityToHands(",
            "MCPRequestTakeToHands(",
            "result.accepted = true",
            "result.confirmed = false",
            "UAMaxDistances.DEFAULT",
        ):
            with self.subTest(token=token):
                self.assertIn(token, take)
        self.assertNotIn("SendInput", take)
        self.assertNotIn("WeaponManager", take)
        self.assertNotIn("OverrideAim", take)

        state = _method_body(source, "protected bool DispatchWeaponState(")
        self.assertIn("ResolvePlayer(", state)
        self.assertIn('result.error = "no_weapon_in_hands"', state)
        self.assertIn("BuildWeaponState(", state)
        built = _method_body(source, "protected MCPWeaponState BuildWeaponState(")
        for token in (
            "GetCurrentMuzzle()",
            "IsChamberEmpty(",
            "IsChamberFiredOut(",
            "GetMagazine(",
            "GetAmmoCount()",
            "GetInternalMagazineCartridgeCount(",
            "IsJammed()",
            "GetCurrentMode(",
            "GetCurrentModeName(",
            "MCPShotCount()",
        ):
            with self.subTest(token=token):
                self.assertIn(token, built)

    def test_capabilities_and_arg_contract_hash(self) -> None:
        bridge = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("hands_take", bridge)
        self.assertIn("weapon_state", bridge)
        self.assertIn('SERVER_ARG_CONTRACT_HASH = "3c77a99c95fd05a4"', bridge)
        self.assertEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, "3c77a99c95fd05a4")
        client = CLIENT.read_text(encoding="utf-8")
        self.assertNotIn('"hands_take"', client)
        self.assertNotIn('"weapon_state"', client)
        messages = MESSAGES.read_text(encoding="utf-8")
        self.assertIn("class MCPWeaponState", messages)
        self.assertIn("bool accepted;", messages)
        self.assertIn("bool confirmed;", messages)


class WeaponHandsAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_tools_forward_on_the_server_peer(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn("hands_take", tools)
        self.assertIn("weapon_state", tools)
        self.assertIn(LEASE_TOOL_LINE, tools["hands_take"].description or "")
        self.assertNotIn("Requires a lease", tools["weapon_state"].description or "")
        self.assertIn("confirmed=false", tools["hands_take"].description or "")
        self.assertIn("no_weapon_in_hands", tools["weapon_state"].description or "")

        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})
        ) as call:
            await app.call_tool("hands_take", {"object_id": 9, "timeout_s": 1.0})
            call.assert_awaited_once_with("hands_take", {"object_id": 9}, "server", 1.0)

        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value={"ok": 0})
        ) as call:
            await app.call_tool("weapon_state", {"timeout_s": 1.0})
            call.assert_awaited_once_with("weapon_state", {}, "server", 1.0)

        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})
        ) as call:
            await app.call_tool(
                "hands_take",
                {"object_id": 9, "uid": "76561198000000000", "timeout_s": 2.0},
            )
            call.assert_awaited_once_with(
                "hands_take",
                {"object_id": 9, "uid": "76561198000000000"},
                "server",
                2.0,
            )


if __name__ == "__main__":
    unittest.main()
