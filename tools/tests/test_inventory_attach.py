from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.fence_helpers import bind_both_peers


COMMAND = "inventory_attach"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"


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


class InventoryAttachIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)

    def test_all_target_and_destination_variants_are_server_mutations(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))

        valid = (
            {
                "object_id": 7,
                "classname": "SparkPlug",
                "dest": "attachment",
                "slot": "SparkPlug",
            },
            {
                "type": "CivilianSedan",
                "pos": [7500.0, 0.0, 7500.0],
                "classname": "SparkPlug",
                "dest": "attachment",
                "slot": "SparkPlug",
            },
            {"object_id": 7, "classname": "Apple", "dest": "cargo"},
            {
                "type": "WoodenCrate",
                "pos": [7500.0, 0.0, 7500.0],
                "classname": "Apple",
                "dest": "cargo",
            },
        )
        for args in valid:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)
                self.assertEqual(status, 200)
                self.assertEqual(body["peer"], "server")

    def test_ingress_rejects_ambiguous_or_incomplete_shapes(self) -> None:
        invalid = (
            {},
            {"object_id": 7, "classname": "Apple"},
            {"object_id": 7, "classname": "", "dest": "cargo"},
            {"object_id": 0, "classname": "Apple", "dest": "cargo"},
            {"object_id": True, "classname": "Apple", "dest": "cargo"},
            {"object_id": 7, "classname": "Apple", "dest": "ground"},
            {
                "object_id": 7,
                "classname": "SparkPlug",
                "dest": "attachment",
            },
            {
                "object_id": 7,
                "classname": "SparkPlug",
                "dest": "attachment",
                "slot": "",
            },
            {
                "object_id": 7,
                "classname": "Apple",
                "dest": "cargo",
                "slot": "",
            },
            {
                "object_id": 7,
                "classname": "Apple",
                "dest": "cargo",
                "extra": 1,
            },
            {
                "object_id": 7,
                "type": "WoodenCrate",
                "classname": "Apple",
                "dest": "cargo",
            },
            {
                "type": "WoodenCrate",
                "pos": [1.0, 2.0],
                "classname": "Apple",
                "dest": "cargo",
            },
        )
        for args in invalid:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})


class InventoryAttachEnforceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = BRIDGE_PATH.read_text(encoding="utf-8")
        cls.body = _method_body(cls.source, "protected bool DispatchInventoryAttach(")

    def test_target_and_classname_fail_closed_before_creation(self) -> None:
        required = (
            "ResolveCommandObject(command.args, attachResolveError)",
            "EntityAI.Cast(attachTarget)",
            'result.error = "not_entity_ai"',
            "attachOwner.GetInventory()",
            'result.error = "inventory_unavailable"',
            "IsKnownInventoryClassname(command.args.classname)",
            'result.error = "invalid_classname"',
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.body)
        self.assertLess(
            self.body.index("ResolveCommandObject("),
            self.body.index("EntityAI.Cast("),
        )
        self.assertLess(
            self.body.index("IsKnownInventoryClassname("),
            self.body.index("CreateAttachmentEx("),
        )
        self.assertLess(
            self.body.index("IsKnownInventoryClassname("),
            self.body.index("CreateEntityInCargo("),
        )

    def test_attachment_validates_slot_and_observes_postcondition(self) -> None:
        mutation = self.body[self.body.index("EntityAI attachedItem") :]
        branch = _method_body(mutation, 'if (command.args.dest == "attachment")')
        required = (
            "InventorySlots.GetSlotIdFromString(command.args.slot)",
            "InventorySlots.IsSlotIdValid(attachSlotId)",
            "attachInventory.HasAttachmentSlot(attachSlotId)",
            "attachInventory.FindAttachment(attachSlotId)",
            'result.error = "slot_not_found"',
            'result.error = "slot_occupied"',
            "attachInventory.CreateAttachmentEx(command.args.classname, attachSlotId)",
            'result.error = "attachment_create_failed"',
            "attachInventory.FindAttachment(attachSlotId) != attachedItem",
            'result.error = "attachment_postcondition_failed"',
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, branch)
        self.assertLess(branch.index("FindAttachment("), branch.index("CreateAttachmentEx("))
        self.assertLess(branch.index('result.error = "slot_occupied"'), branch.index("CreateAttachmentEx("))

    def test_cargo_requires_capacity_and_observes_membership(self) -> None:
        cargo_start = self.body.index("\t\telse\n\t\t{", self.body.index("EntityAI attachedItem"))
        cargo = self.body[cargo_start : self.body.index("\n\t\tresult.classname", cargo_start)]
        required = (
            "attachInventory.GetCargo()",
            'result.error = "cargo_unavailable"',
            "attachInventory.CreateEntityInCargo(command.args.classname)",
            'result.error = "cargo_create_failed"',
            "attachInventory.HasEntityInCargo(attachedItem)",
            'result.error = "cargo_postcondition_failed"',
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, cargo)
        self.assertLess(cargo.index("GetCargo()"), cargo.index("CreateEntityInCargo("))
        self.assertLess(cargo.index("CreateEntityInCargo("), cargo.index("HasEntityInCargo("))

    def test_success_returns_receipt_and_inventory_snapshot(self) -> None:
        for token in (
            "result.classname = command.args.classname",
            "result.object_id = command.args.object_id",
            "attachReceipt.dest = command.args.dest",
            "attachReceipt.slot = command.args.slot",
            "result.inventory_attach = attachReceipt",
            'attachTelemetry.mode = "inventory_attach"',
            "PopulateTelemetryObject(attachOwner, attachTelemetry)",
            "result.telemetry = attachTelemetry",
            "result.ok = true",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.body)
        self.assertLess(
            self.body.index("PopulateTelemetryObject(attachOwner, attachTelemetry)"),
            self.body.rindex("result.ok = true"),
        )
        inspect = _method_body(self.source, "protected bool DispatchObjectInspect(")
        self.assertIn('wantName == "inventory"', inspect)
        self.assertIn("PopulateTelemetryObject(match, inventoryTelemetry)", inspect)

    def test_wire_has_slot_and_result_receipt_fields(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        args = _method_body(messages, "class MCPArgs")
        result = _method_body(messages, "class MCPResult")
        receipt = _method_body(messages, "class MCPInventoryAttachReceipt")
        self.assertIn("string slot;", args)
        self.assertIn("string classname;", result)
        self.assertIn("ref MCPInventoryAttachReceipt inventory_attach;", result)
        self.assertIn("string dest;", receipt)
        self.assertIn("string slot;", receipt)


class InventoryAttachAppToolTest(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        return server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_registered_schema_declares_destination_enum(self) -> None:
        app, _runtime = self._app()
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)
        schema = tools[COMMAND].inputSchema or {}
        props = schema.get("properties", {})
        self.assertEqual(props["dest"].get("enum"), ["attachment", "cargo"])
        self.assertIn("classname", schema.get("required", []))
        self.assertIn("dest", schema.get("required", []))

    async def test_forwards_attachment_by_object_id(self) -> None:
        app, runtime = self._app()
        expected = {
            "object_id": 7,
            "classname": "SparkPlug",
            "dest": "attachment",
            "slot": "SparkPlug",
        }
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "dest": "attachment"}),
        ) as call:
            await app.call_tool(COMMAND, {**expected, "timeout_s": 1.0})
        call.assert_awaited_once_with(COMMAND, expected, "server", 1.0)

    async def test_forwards_cargo_by_type_and_omits_slot(self) -> None:
        app, runtime = self._app()
        expected = {
            "type": "WoodenCrate",
            "pos": [1.0, 2.0, 3.0],
            "classname": "Apple",
            "dest": "cargo",
        }
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "dest": "cargo"}),
        ) as call:
            await app.call_tool(COMMAND, {**expected, "timeout_s": 1.0})
        call.assert_awaited_once_with(COMMAND, expected, "server", 1.0)

    async def test_rejects_cross_field_and_target_errors_before_enqueue(self) -> None:
        cases = (
            {"object_id": 7, "classname": "SparkPlug", "dest": "attachment"},
            {
                "object_id": 7,
                "classname": "Apple",
                "dest": "cargo",
                "slot": "Cargo",
            },
            {"object_id": 0, "classname": "Apple", "dest": "cargo"},
            {"object_id": True, "classname": "Apple", "dest": "cargo"},
        )
        for args in cases:
            with self.subTest(args=args):
                app, runtime = self._app()
                with patch.object(runtime, "call_bridge", new=AsyncMock()) as call:
                    with self.assertRaises(Exception):
                        await app.call_tool(COMMAND, args)
                call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
