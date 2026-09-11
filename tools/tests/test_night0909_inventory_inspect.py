"""b256 offline structural/consumer contract; no Enforce execution is claimed."""
from __future__ import annotations

import asyncio
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.session_coordination import command_requires_lease

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = Path(os.environ.get("NIGHT0909_BRIDGE_SOURCE", str(ROOT / "addon/scripts/5_Mission/MCPBridge.c")))


def method(source, signature):
    start = source.index(signature)
    start = source.index("{", start)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if not depth:
                return source[start + 1:i]
    raise AssertionError("unterminated method " + signature)


class InventoryInspectStructureTest(unittest.TestCase):
    def setUp(self):
        self.source = BRIDGE.read_text(encoding="utf-8-sig")
        self.body = method(self.source, "protected bool DispatchObjectInspect(")

    def test_inventory_snapshot_uses_same_resolved_target_before_success(self):
        self.assertIn('wantName == "inventory"', self.body)
        branch = method(self.body, 'if (wantName == "inventory"')
        self.assertIn("PopulateTelemetryObject(match, inventoryTelemetry);", branch)
        self.assertIn("result.telemetry = inventoryTelemetry;", branch)
        self.assertLess(self.body.index("ResolveCommandObject(command.args, error)"), self.body.index("PopulateTelemetryObject"))
        self.assertLess(self.body.index("PopulateTelemetryObject"), self.body.index("result.ok = true"))
        self.assertNotIn("GetObjectsAtPosition3D", branch)

    def test_missing_target_fails_before_inventory_read(self):
        before = self.body[:self.body.index("MCPObjectInspect inspect")]
        self.assertIn("if (!match)", before)
        self.assertIn("result.ok = false", before)
        self.assertIn("result.error = error", before)
        self.assertIn("return true", before)

    def test_requesting_inventory_preserves_existing_memory_point_behavior(self):
        self.assertIn('wantName == "inventory" && !result.telemetry', self.body)
        # Two independent if statements: inventory adds data without stealing
        # the old memory-point selector named inventory, even when it exists.
        self.assertRegex(self.body, r'result\.telemetry = inventoryTelemetry;\s*}\s*if \(wantName == "bounding_center"\)')
        self.assertIn("match.MemoryPointExists(wantName)", self.body)
        self.assertIn("point.name = wantName", self.body)
        self.assertIn("inspect.memory_points.Insert(point)", self.body)

    def test_reused_reader_reports_immediate_counts_and_separate_lists(self):
        populate = method(self.source, "protected void PopulateTelemetryObject(")
        self.assertIn("PopulateTelemetryInventory(entity, telemetry)", populate)
        inventory = method(self.source, "protected void PopulateTelemetryInventory(")
        for expected in ["inventory.GetAttachmentSlotsCount()", "inventory.GetAttachmentSlotId(i)", "inventory.AttachmentCount()", "inventory.GetAttachmentFromIndex(i)", "inventory.GetCargo()", "cargo.GetItemCount()", "cargo.GetItem(i)", "telemetry.attachment_items.Insert", "telemetry.cargo_items.Insert", "telemetry.items_truncated = true"]:
            self.assertIn(expected, inventory)

    def test_ingress_and_lease_contract_remain_read_only(self):
        self.assertFalse(command_requires_lease("object_inspect"))
        self.assertEqual(loopback.peer_for_command("object_inspect"), "server")
        state = loopback.ServerState("offline-test")
        for args in [{"object_id": 1, "want": ["inventory"]}, {"type": "WoodenCrate", "pos": [1.0, 2.0, 3.0], "want": ["inventory", "bounding_center"]}]:
            status, _ = state.enqueue_command("object_inspect", args)
            self.assertEqual(status, 200)
        for args in [{"object_id": 0, "want": ["inventory"]}, {"object_id": 1, "want": []}, {"object_id": 1, "want": ["inventory"], "extra": True}]:
            status, _ = state.enqueue_command("object_inspect", args)
            self.assertEqual(status, 400)

    def test_python_consumer_retains_empty_inventory_snapshot(self):
        snapshot = {"mode": "object_inspect", "found": 1, "type": "WoodenCrate", "attachment_count": 0, "cargo_count": 0, "attachment_items": [], "cargo_items": [], "declared_slots": []}
        raw = {"ok": 1, "inspect": {"type": "WoodenCrate"}, "telemetry": snapshot, "entities": []}
        result = result_prune.prune_unfilled_fields("object_inspect", raw)
        self.assertEqual(result["telemetry"], snapshot)
        self.assertNotIn("entities", result)


class InventoryInspectPublicTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_public_tool_forwards_inventory_and_keeps_both_payloads(self):
        class Runtime:
            def __init__(self):
                self.tool_lock = asyncio.Lock()
                self.calls = []

            async def call_bridge(self, cmd, args, peer, timeout_s):
                self.calls.append((cmd, args, peer))
                return {"ok": 1, "object_id": 7, "inspect": {"type": "WoodenCrate"}, "telemetry": {"found": 1, "cargo_count": 2, "cargo_items": ["Rag", "Battery9V"]}}

        runtime = Runtime()
        config = server.ServerConfig(mode="client", key="offline-test", client_platform="codex", log_sink=lambda _: None)
        with patch.object(server, "ClientRuntime", return_value=runtime), patch.object(server, "_frozen_tool_registry_overlay", return_value={}):
            app, _ = server.build_app(config)
        tool = app._tool_manager.get_tool("object_inspect")
        result = await tool.run({"object_id": 7, "want": ["inventory"]})
        self.assertEqual(runtime.calls, [("object_inspect", {"object_id": 7, "want": ["inventory"]}, "server")])
        self.assertEqual(result["telemetry"]["cargo_count"], 2)
        self.assertEqual(result["inspect"]["type"], "WoodenCrate")


if __name__ == "__main__":
    unittest.main()
