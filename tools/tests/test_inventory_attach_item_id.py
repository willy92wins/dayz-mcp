"""inventory_attach registers the created item and returns its id (aa01).

The top-level object_id stays the destination owner. object_delete is unchanged
and removes a registered child the same way it removes a world_spawn.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from tests._addon_paths import addon_root


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


class InventoryAttachItemIdContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = BRIDGE_PATH.read_text(encoding="utf-8")
        cls.body = _method_body(cls.source, "protected bool DispatchInventoryAttach(")
        cls.delete_body = _method_body(cls.source, "protected bool DispatchObjectDelete(")
        cls.messages = MESSAGES_PATH.read_text(encoding="utf-8")

    def test_created_item_is_registered_after_both_postconditions(self) -> None:
        register = "m_RuntimeObjects.Insert(command.id, attachedItem)"
        self.assertIn(register, self.body)
        self.assertLess(self.body.index('result.error = "attachment_postcondition_failed"'), self.body.index(register))
        self.assertLess(self.body.index('result.error = "cargo_postcondition_failed"'), self.body.index(register))
        self.assertLess(self.body.index(register), self.body.index("result.ok = true"))
        # Owner identity is the caller's object_id, not the new command id.
        self.assertIn("result.object_id = command.args.object_id", self.body)
        self.assertNotIn("result.object_id = command.id", self.body)

    def test_receipt_carries_child_id_separately_from_owner(self) -> None:
        receipt = _method_body(self.messages, "class MCPInventoryAttachReceipt")
        self.assertIn("int item_object_id;", receipt)
        self.assertIn("string dest;", receipt)
        self.assertIn("string slot;", receipt)
        self.assertIn("attachReceipt.item_object_id = command.id", self.body)
        self.assertLess(
            self.body.index("attachReceipt.item_object_id = command.id"),
            self.body.index("result.inventory_attach = attachReceipt"),
        )
        self.assertIn("same-run", receipt.lower() + self.messages[self.messages.index("class MCPInventoryAttachReceipt") - 400 : self.messages.index("class MCPInventoryAttachReceipt")].lower())

    def test_object_delete_is_unchanged_registry_delete(self) -> None:
        self.assertIn("GetGame().ObjectDelete(target)", self.delete_body)
        self.assertNotIn("inventory_attach", self.delete_body)
        self.assertNotIn("DeleteAttachment", self.delete_body)
        self.assertIn("result.deleted = 0", self.delete_body)

    def test_registry_wording_includes_the_attached_item(self) -> None:
        resolve = self.source[self.source.index("protected Object ResolveCommandObject") - 500 : self.source.index("protected Object ResolveCommandObject")]
        self.assertIn("inventory_attach", resolve)
        runtime = self.source[self.source.index("protected int RuntimeObjectId") - 300 : self.source.index("protected int RuntimeObjectId")]
        self.assertIn("inventory_attach", runtime)


class InventoryAttachItemIdToolTest(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        return server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _description(self, name: str) -> str:
        app, _runtime = self._app()
        tools = {tool.name: tool for tool in await app.list_tools()}
        return tools[name].description or ""

    async def test_descriptions_distinguish_owner_and_child(self) -> None:
        attach = await self._description(COMMAND)
        self.assertIn("item_object_id", attach)
        self.assertIn("destination owner", attach)
        self.assertIn("same-run", attach)
        delete = await self._description("object_delete")
        self.assertIn("inventory_attach.item_object_id", delete)
        self.assertIn("destination owner", delete)
        inspect = await self._description("object_inspect")
        self.assertIn("inventory_attach.item_object_id", inspect)
        hands = await self._description("hands_take")
        self.assertIn("inventory_attach.item_object_id", hands)
        weapon = await self._description("weapon_state")
        self.assertIn("inventory_attach", weapon)

    async def test_success_returns_distinct_child_id(self) -> None:
        app, runtime = self._app()
        payload = {
            "ok": 1,
            "object_id": 7,
            "inventory_attach": {"dest": "attachment", "slot": "Armband", "item_object_id": 44},
        }
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value=payload)):
            _content, result = await app.call_tool(
                COMMAND,
                {
                    "object_id": 7,
                    "classname": "Armband_Yellow",
                    "dest": "attachment",
                    "slot": "Armband",
                    "timeout_s": 1.0,
                },
            )
        self.assertEqual(result["object_id"], 7)
        self.assertEqual(result["inventory_attach"]["item_object_id"], 44)

    async def test_legacy_receipt_is_kept_so_a_retry_is_not_forced(self) -> None:
        # Base PBO: creation succeeds and the reply has the owner id only.
        # Raising after that reply hid the mutation; a caller retried and
        # the crate gained a second Apple, or the slot came back slot_occupied.
        app, runtime = self._app()
        payload = {
            "ok": 1,
            "object_id": 7,
            "inventory_attach": {"dest": "cargo", "slot": ""},
        }
        bridge = AsyncMock(return_value=payload)
        args = {
            "object_id": 7,
            "classname": "Apple",
            "dest": "cargo",
            "timeout_s": 1.0,
        }
        with patch.object(runtime, "call_bridge", new=bridge):
            _content, result = await app.call_tool(COMMAND, args)
            # Retry only when the tool raises. The legacy receipt is a
            # completed create, so this policy does not call the bridge again.
            self.assertTrue(result.get("ok"))
            self.assertEqual(result["object_id"], 7)
            self.assertEqual(result["inventory_attach"]["dest"], "cargo")
            self.assertNotIn("item_object_id", result["inventory_attach"])
            self.assertIs(result["item_object_id_unavailable"], True)
            self.assertIn("was created", result["detail"])
            self.assertIn("Do not retry", result["detail"])
        self.assertEqual(bridge.await_count, 1)

    async def test_child_id_equal_to_owner_keeps_the_receipt(self) -> None:
        app, runtime = self._app()
        payload = {
            "ok": 1,
            "object_id": 7,
            "inventory_attach": {"dest": "cargo", "slot": "", "item_object_id": 7},
        }
        bridge = AsyncMock(return_value=payload)
        with patch.object(runtime, "call_bridge", new=bridge):
            _content, result = await app.call_tool(
                COMMAND,
                {"object_id": 7, "classname": "Apple", "dest": "cargo", "timeout_s": 1.0},
            )
        self.assertEqual(bridge.await_count, 1)
        self.assertEqual(result["object_id"], 7)
        self.assertEqual(result["inventory_attach"]["item_object_id"], 7)
        self.assertIs(result["item_object_id_unavailable"], True)


if __name__ == "__main__":
    unittest.main()
