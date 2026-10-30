"""inventory_move public tool and daemon agree on per-op arguments."""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import loopback, server
from tests.mcp_helpers import _content_json

_ROOT = Path(__file__).resolve().parents[2]
UID = "76561198000000001"


class InventoryMoveDaemonArgsTest(unittest.TestCase):
    def test_ops_that_need_a_destination_or_a_slot(self):
        accepted = (
            {"item_object_id": 1, "op": "to_hands", "uid": UID},
            {"item_object_id": 1, "op": "to_inventory", "uid": UID},
            {"item_object_id": 1, "op": "split_half", "uid": UID},
            {"item_object_id": 1, "op": "split_to_hands", "uid": UID},
            {"item_object_id": 1, "op": "to_cargo", "target_object_id": 4, "uid": UID},
            {"item_object_id": 1, "op": "combine", "target_object_id": 4, "authority": "server", "uid": UID},
            {
                "item_object_id": 1,
                "op": "to_attachment",
                "target_object_id": 4,
                "slot": "Vest",
                "uid": UID,
            },
            # Server world-to-world stays exempt from uid (B2).
            {"item_object_id": 1, "op": "to_cargo", "target_object_id": 4, "authority": "server"},
        )
        for args in accepted:
            with self.subTest(args=args):
                self.assertEqual(loopback.validate_command_args("inventory_move", args), (True, None))
        rejected = (
            {"item_object_id": 1, "op": "to_cargo", "uid": UID},
            {"item_object_id": 1, "op": "to_cargo", "target_object_id": 0, "uid": UID},
            {"item_object_id": 1, "op": "combine", "uid": UID},
            {"item_object_id": 1, "op": "to_attachment", "target_object_id": 4, "uid": UID},
            {"item_object_id": 1, "op": "to_attachment", "target_object_id": 4, "slot": "", "uid": UID},
            {"item_object_id": 1, "op": "to_hands"},
            {"item_object_id": 1, "op": "to_hands", "uid": ""},
            {"item_object_id": 1, "op": "to_inventory", "authority": "client"},
        )
        for args in rejected:
            with self.subTest(args=args):
                self.assertEqual(
                    loopback.validate_command_args("inventory_move", args),
                    (False, "bad_args"),
                )


class InventoryMovePublicArgsTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_tool_rejects_the_same_ops_before_the_bridge(self):
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            await app.call_tool(
                "inventory_move",
                {"item_object_id": 1, "op": "to_cargo", "target_object_id": 3, "timeout_s": 1.0, "uid": UID},
            )
            call.assert_awaited()
        with patch.object(runtime, "call_bridge", new=AsyncMock()) as call:
            for arguments in (
                {"item_object_id": 1, "op": "to_cargo", "timeout_s": 1.0, "uid": UID},
                {"item_object_id": 1, "op": "combine", "timeout_s": 1.0, "uid": UID},
                {
                    "item_object_id": 1,
                    "op": "to_attachment",
                    "target_object_id": 3,
                    "slot": "",
                    "timeout_s": 1.0,
                    "uid": UID,
                },
            ):
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ToolError):
                        await app.call_tool("inventory_move", arguments)
            call.assert_not_awaited()
        text = (await app.list_tools())
        description = next(tool.description or "" for tool in text if tool.name == "inventory_move")
        self.assertIn("juncture", description)
        self.assertIn("ShouldSplitQuantity", description)
        self.assertIn("GetAmmoCount", description)
        self.assertIn("accepted=false", description)

    async def test_a_client_refusal_reaches_the_caller_unchanged(self):
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        bridge = {
            "ok": False,
            "accepted": False,
            "confirmed": False,
            "error": "no_room",
        }
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value=bridge)):
            payload = _content_json(
                await app.call_tool(
                    "inventory_move",
                    {
                        "item_object_id": 1,
                        "op": "to_inventory",
                        "authority": "client",
                        "timeout_s": 1.0,
                        "uid": UID,
                    },
                )
            )
        self.assertFalse(payload["accepted"])
        self.assertFalse(payload["confirmed"])
        self.assertEqual(payload["error"], "no_room")


def _function_body(source: str, name: str) -> str:
    match = re.search(re.escape(name), source)
    if match is None:
        raise AssertionError(f"{name} not found")
    start = source.find("{", match.end())
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"{name} body did not close")


class InventoryMoveRefusalCodesTest(unittest.TestCase):
    def test_every_client_refusal_code_is_on_the_server_whitelist(self):
        client = (_ROOT / "addon" / "scripts" / "4_World" / "MCP_InventoryMove.c").read_text(
            encoding="utf-8"
        )
        bridge = (_ROOT / "addon" / "scripts" / "5_Mission" / "MCPBridge.c").read_text(encoding="utf-8")
        perform = _function_body(client, "MCPPerformClientInventoryMove")
        apply = _function_body(client, "MCPApplyInventoryMoveRpc")
        client_codes = set(re.findall(r'return "([^"]+)"', perform))
        client_codes.discard("")
        client_codes.update(re.findall(r'MCPRefuseInventoryMove\(\s*jobId\s*,\s*"([^"]+)"\s*\)', apply))
        client_codes.update(re.findall(r'MCPDropInventoryMove\(\s*"([^"]+)"\s*\)', apply))
        whitelist = set(
            re.findall(
                r'reason == "([^"]+)"',
                _function_body(bridge, "MCPInventoryMoveClientReason"),
            )
        )
        self.assertEqual(client_codes, whitelist)
        ready = _function_body(bridge, "bool MCPInventoryMoveReady")
        cargo_at = ready.index('if (op == "to_cargo")')
        hands_at = ready.index('if (op == "to_hands")', cargo_at)
        self.assertNotIn("job.actor", ready[cargo_at:hands_at])


class InventoryMoveConfirmationOrderTest(unittest.TestCase):
    def test_the_server_read_is_checked_before_a_client_refusal_is_posted(self):
        bridge = (_ROOT / "addon" / "scripts" / "5_Mission" / "MCPBridge.c").read_text(encoding="utf-8")
        dispatch = _function_body(bridge, "bool DispatchInventoryMove(")
        self.assertLess(
            dispatch.index("if (MCPInventoryMoveReady(job))"),
            dispatch.index("if (clientRpc && job.phase == 2)"),
        )
        jobs = _function_body(bridge, "void ProcessJobs()")
        self.assertLess(
            jobs.index("else if (IsJobReady(job))"),
            jobs.index('job.kind == "inventory_move" && job.phase == 2'),
        )
