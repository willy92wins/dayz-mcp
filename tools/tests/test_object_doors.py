"""object_doors: server read of Building door predicates, not animation phase."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.result_prune import prune_unfilled_fields
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root


COMMAND = "object_doors"
VALID_ARGS = {"type": "Land_Garage_Row_Small", "pos": [7500.0, 0.0, 7500.0]}
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
RAYCAST_SENTENCE = (
    "Whether a raycast passes through an open door leaf is out of scope."
)


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


class ObjectDoorsIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")

    def test_server_read_needs_no_lease(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease(COMMAND))
        self.assertIn(COMMAND, server._BRIDGE_WORLD_READ_COMMANDS)

        by_id = {"object_id": 5}
        for args in (dict(VALID_ARGS), by_id):
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)
                self.assertEqual(status, 200)
                self.assertEqual(body["peer"], "server")

    def test_phase_cannot_ride_along(self) -> None:
        status, body = self.state.enqueue_command(
            COMMAND, {**VALID_ARGS, "phase": 1.0, "source": "Doors1"}
        )
        self.assertEqual((status, body), (400, {"error": "bad_args"}))

    def test_door_payload_survives_prune_and_not_a_building_drops_the_empty_ref(self) -> None:
        doors = {
            "door_count": 1,
            "doors": [{"index": 0, "open": True, "locked": False}],
        }
        kept = prune_unfilled_fields(
            COMMAND, {"ok": 1, "building_doors": doors, "input_describe": {}}
        )
        self.assertEqual(kept["building_doors"], doors)
        self.assertNotIn("input_describe", kept)
        refused = prune_unfilled_fields(
            COMMAND,
            {
                "ok": 0,
                "error": "not_a_building",
                "type": "CivilianSedan",
                "building_doors": {},
            },
        )
        self.assertEqual(refused["error"], "not_a_building")
        self.assertEqual(refused["type"], "CivilianSedan")
        self.assertNotIn("building_doors", refused)


class ObjectDoorsFastMCPTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_target_names_both_forms(self) -> None:
        app, _runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        with self.assertRaises(server.ToolError) as ctx:
            await app.call_tool(COMMAND, {"pos": [7500.0, 0.0, 7500.0]})
        message = str(ctx.exception)
        self.assertIn("bad_args", message)
        self.assertIn("type", message)
        self.assertIn("object_id", message)
        self.assertIn("type+pos", message)
        self.assertNotIn("''", message)

    async def test_tool_forwards_and_keeps_object_anim_unchanged(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)
        description = tools[COMMAND].description or ""
        self.assertIn(RAYCAST_SENTENCE, description)
        self.assertIn("not_a_building", description)
        self.assertIn("object_anim is unchanged", description)
        self.assertIn("GetDoorSoundPos", description)
        self.assertIn("3-float array", description)
        self.assertNotIn("Requires a lease", description)

        payload = {
            "ok": 1,
            "type": "Land_Garage_Row_Small",
            "building_doors": {
                "door_count": 1,
                "doors": [
                    {
                        "index": 0,
                        "open": True,
                        "opening": False,
                        "opening_ajar": False,
                        "opened": True,
                        "ajar": False,
                        "closing": False,
                        "closed": False,
                        "locked": False,
                    }
                ],
            },
        }
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value=payload)
        ) as call:
            await app.call_tool(
                COMMAND,
                {
                    "type": VALID_ARGS["type"],
                    "pos": VALID_ARGS["pos"],
                    "timeout_s": 1.0,
                },
            )
        call.assert_awaited_once_with(COMMAND, VALID_ARGS, "server", 1.0)

        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value=payload)
        ) as call_id:
            await app.call_tool(COMMAND, {"object_id": 5, "timeout_s": 1.0})
        call_id.assert_awaited_once_with(COMMAND, {"object_id": 5}, "server", 1.0)


class ObjectDoorsEnforceContractTest(unittest.TestCase):
    def test_predicates_are_read_and_object_anim_is_untouched(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        doors_at = dispatch.index('command.cmd == "object_doors"')
        query_at = dispatch.index('command.cmd == "entities_query"')
        self.assertLess(doors_at, query_at)

        body = _method_body(source, "protected bool DispatchObjectDoors(")
        for token in (
            "Building.Cast(match)",
            'result.error = "not_a_building"',
            "building.GetDoorCount()",
            "report.door_count = doorCount",
            'result.error = "door_count_unsupported"',
            "building.IsDoorOpen(doorIndex)",
            "building.IsDoorOpening(doorIndex)",
            "building.IsDoorOpeningAjar(doorIndex)",
            "building.IsDoorOpened(doorIndex)",
            "building.IsDoorOpenedAjar(doorIndex)",
            "building.IsDoorClosing(doorIndex)",
            "building.IsDoorClosed(doorIndex)",
            "building.IsDoorLocked(doorIndex)",
            "building.GetDoorSoundPos(doorIndex)",
            "VectorToArray(doorSoundPos, row.pos)",
            "result.building_doors = report",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        self.assertLess(
            body.index("report.door_count = doorCount"),
            body.index("door_count_unsupported"),
        )
        for forbidden in (
            "OpenDoor",
            "CloseDoor",
            "LockDoor",
            "UnlockDoor",
            "GetAnimationPhase",
            "GetDoorIndex",
            "PlayDoorSound",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

        anim = _method_body(source, "protected bool DispatchObjectAnim(")
        self.assertNotIn("IsDoorOpen", anim)
        self.assertNotIn("GetDoorCount", anim)
        self.assertIn('SERVER_ARG_CONTRACT_HASH = "3c77a99c95fd05a4"', source)

        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn("class MCPDoorState", messages)
        self.assertIn("class MCPBuildingDoors", messages)
        self.assertIn("ref MCPBuildingDoors building_doors;", messages)
        door_state = _method_body(messages, "class MCPDoorState")
        self.assertIn("ref array<float> pos;", door_state)
        self.assertIn("GetDoorSoundPos", door_state)

    def test_stale_server_census_names_this_tool_without_flipping_the_hash(self) -> None:
        announced = sorted(server._BRIDGE_COMMAND_TOOLS["server"])
        announced.remove(COMMAND)
        registered = frozenset(
            tool for tool in server._BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        result = server._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": announced,
                "announced_arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            },
            registered,
        )
        self.assertEqual(result["reason"], "census_disagrees_with_registered_tools")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "3c77a99c95fd05a4")
        self.assertEqual(
            server.server_arg_contract_canonical(),
            "vehicle_prepare_fixture=mode,pos,radius,type",
        )


if __name__ == "__main__":
    unittest.main()
