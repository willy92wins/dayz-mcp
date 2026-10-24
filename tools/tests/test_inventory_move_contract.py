"""inventory_move public tool and daemon agree on per-op arguments."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import loopback, server


class InventoryMoveDaemonArgsTest(unittest.TestCase):
    def test_ops_that_need_a_destination_or_a_slot(self):
        accepted = (
            {"item_object_id": 1, "op": "to_hands"},
            {"item_object_id": 1, "op": "to_inventory"},
            {"item_object_id": 1, "op": "split_half"},
            {"item_object_id": 1, "op": "split_to_hands"},
            {"item_object_id": 1, "op": "to_cargo", "target_object_id": 4},
            {"item_object_id": 1, "op": "combine", "target_object_id": 4, "authority": "server"},
            {
                "item_object_id": 1,
                "op": "to_attachment",
                "target_object_id": 4,
                "slot": "Vest",
            },
        )
        for args in accepted:
            with self.subTest(args=args):
                self.assertEqual(loopback.validate_command_args("inventory_move", args), (True, None))
        rejected = (
            {"item_object_id": 1, "op": "to_cargo"},
            {"item_object_id": 1, "op": "to_cargo", "target_object_id": 0},
            {"item_object_id": 1, "op": "combine"},
            {"item_object_id": 1, "op": "to_attachment", "target_object_id": 4},
            {"item_object_id": 1, "op": "to_attachment", "target_object_id": 4, "slot": ""},
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
                {"item_object_id": 1, "op": "to_cargo", "target_object_id": 3, "timeout_s": 1.0},
            )
            call.assert_awaited()
        with patch.object(runtime, "call_bridge", new=AsyncMock()) as call:
            for arguments in (
                {"item_object_id": 1, "op": "to_cargo", "timeout_s": 1.0},
                {"item_object_id": 1, "op": "combine", "timeout_s": 1.0},
                {
                    "item_object_id": 1,
                    "op": "to_attachment",
                    "target_object_id": 3,
                    "slot": "",
                    "timeout_s": 1.0,
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
