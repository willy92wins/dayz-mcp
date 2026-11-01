"""Issue 298: public contract for eject-before-delete, without a live game.

The source execution tests cover the engine-facing sequencing separately.
These tests exercise the actual MCP wrappers, argument validator, result
pruning and bridge-error conversion; no DayZ process or PBO is used.
"""

from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import server
from dayz_mcp import loadout as loadout_contract
from dayz_mcp.loopback import peer_for_command, validate_command_args
from dayz_mcp.world_results import _scene_clear_reply
from tests.mcp_helpers import _content_json


class VehicleDeleteContractTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    def test_input_contract_stays_unchanged_and_has_no_eject_bypass(self) -> None:
        for verb, args, peer in (
            ("object_delete", {"object_id": 7}, "server"),
            ("scene_clear", {"scene_id": "vehicles"}, "server"),
            ("vehicle_release", {}, "client"),
        ):
            with self.subTest(verb=verb):
                self.assertEqual(peer_for_command(verb), peer)
                self.assertEqual(validate_command_args(verb, args), (True, None))
                self.assertEqual(
                    set(self.tools[verb].parameters["properties"]),
                    set(args) | {"timeout_s"},
                )
                self.assertEqual(
                    validate_command_args(verb, {**args, "eject": False}),
                    (False, "bad_args"),
                )

    def test_delete_descriptions_explain_all_crew_fail_closed_and_delay(self) -> None:
        for verb in ("object_delete", "scene_clear"):
            with self.subTest(verb=verb):
                description = self.tools[verb].description
                self.assertIn("all crew positions", description)
                self.assertIn("vehicle_eject_failed", description)
                self.assertIn("later server tick", description)
                self.assertIn("5 s", description)
        self.assertIn("registry id", self.tools["object_delete"].description)
        self.assertIn("scene_shared", self.tools["scene_clear"].description)
        self.assertIn("deletes nothing", self.tools["scene_clear"].description)

    def test_release_description_does_not_promise_a_dismount(self) -> None:
        description = self.tools["vehicle_release"].description
        self.assertIn("does not eject", description)
        self.assertIn("object_delete", description)
        self.assertIn("Call vehicle_trace mode=stop before vehicle_release", description)

    async def test_object_delete_success_and_unknown_keep_the_existing_reply(self) -> None:
        for deleted in (0, 1):
            with self.subTest(deleted=deleted):
                reply = {"id": 17, "ok": 1, "object_id": 7, "deleted": deleted}
                call = AsyncMock(return_value=copy.deepcopy(reply))
                with patch.object(self.runtime, "call_bridge", new=call):
                    actual = _content_json(
                        await self.app.call_tool("object_delete", {"object_id": 7, "timeout_s": 8.0})
                    )
                call.assert_awaited_once_with("object_delete", {"object_id": 7}, "server", 8.0)
                self.assertEqual(actual, reply)

    async def test_ejection_failure_is_an_error_and_keeps_the_object_id(self) -> None:
        wire = {"id": 17, "ok": 0, "error": "vehicle_eject_failed", "object_id": 7, "deleted": 0}
        fake_state = SimpleNamespace(take_result=Mock(return_value=wire))
        with patch.object(self.runtime, "loopback", SimpleNamespace(state=fake_state)):
            with self.assertRaises(ToolError) as caught:
                await self.runtime.wait_for_result("object_delete", 17, "server", 8.0)
        self.assertEqual(str(caught.exception), "vehicle_eject_failed")
        self.assertEqual(caught.exception.object_id, 7)
        self.assertEqual(wire["deleted"], 0)

    async def test_result_pruning_keeps_deleted_zero_as_a_real_answer(self) -> None:
        wire = {"id": 17, "ok": 1, "object_id": 7, "deleted": 0, "seated": False}
        fake_state = SimpleNamespace(take_result=Mock(return_value=wire))
        with patch.object(self.runtime, "loopback", SimpleNamespace(state=fake_state)):
            result = await self.runtime.wait_for_result("object_delete", 17, "server", 8.0)
        self.assertEqual(result, {"id": 17, "ok": 1, "object_id": 7, "deleted": 0})

    async def test_scene_ejection_failure_is_not_reported_as_success(self) -> None:
        wire = {
            "id": 18,
            "ok": 0,
            "error": "vehicle_eject_failed",
            "scene_clear": {"deleted": 0, "already_deleted": 1, "errors": 2, "error_ids": [7, 9]},
        }
        fake_state = SimpleNamespace(take_result=Mock(return_value=wire))
        with patch.object(self.runtime, "loopback", SimpleNamespace(state=fake_state)):
            with self.assertRaisesRegex(ToolError, "^vehicle_eject_failed$"):
                await self.runtime.wait_for_result("scene_clear", 18, "server", 8.0)
        self.assertEqual(_scene_clear_reply(copy.deepcopy(wire)), wire)

    async def test_scene_success_keeps_existing_counts_without_new_fields(self) -> None:
        reply = {
            "id": 18,
            "ok": 1,
            "scene_clear": {"deleted": 2, "already_deleted": 1, "errors": 0, "error_ids": []},
        }
        call = AsyncMock(return_value=copy.deepcopy(reply))
        with patch.object(self.runtime, "call_bridge", new=call):
            actual = _content_json(
                await self.app.call_tool("scene_clear", {"scene_id": "vehicles", "timeout_s": 8.0})
            )
        call.assert_awaited_once_with("scene_clear", {"scene_id": "vehicles"}, "server", 8.0)
        self.assertEqual(actual, reply)

    def test_loadout_names_vehicle_cleanup_refusal_in_both_error_surfaces(self) -> None:
        self.assertIn("vehicle_eject_failed", loadout_contract.ITEM_ERRORS)
        description = self.tools["loadout_apply"].description
        self.assertIn("vehicle_eject_failed", description)
        self.assertIn("rollback", description)

    async def test_clear_first_ejection_failure_never_sends_apply(self) -> None:
        call = AsyncMock(side_effect=ToolError("vehicle_eject_failed"))
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaisesRegex(ToolError, "vehicle_eject_failed"):
                await self.app.call_tool(
                    "loadout_apply",
                    {"loadout": {"hands": {"type": "M4A1"}}, "object_id": 7, "clear_first": True},
                )
        call.assert_awaited_once()
        self.assertEqual(call.await_args.args[:3], ("loadout_clear", {"object_id": 7}, "server"))

    async def test_failed_inventory_rollback_keeps_per_item_error(self) -> None:
        reply = {
            "ok": 1,
            "type": "SurvivorM_Mirek",
            "loadout": {
                "items": [],
                "applied": [{"ok": 0, "error": "vehicle_eject_failed"}],
                "too_large": 0,
            },
        }
        call = AsyncMock(return_value=reply)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(await self.app.call_tool(
                "loadout_apply", {"loadout": {"hands": {"type": "M4A1"}}, "object_id": 7}
            ))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["items"], [
            {"path": "loadout.hands", "type": "M4A1", "ok": False, "error": "vehicle_eject_failed"}
        ])


if __name__ == "__main__":
    unittest.main()
