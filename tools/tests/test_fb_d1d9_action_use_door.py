"""action_use door_index: one Building door, gated like action_use_target."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback
from dayz_mcp.server import (
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census


COMMAND = "action_use"
DOOR_COMMAND = "action_use_door"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
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


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    start = source.index(needle)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated if ({condition})")


def _content_json(content: Any) -> dict[str, Any]:
    if isinstance(content, tuple):
        _blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = _blocks
    parsed = json.loads(content[0].text)
    if not isinstance(parsed, dict):
        raise AssertionError(f"expected dict content, got {parsed!r}")
    return parsed


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    if message.startswith(wrapper):
        return message[len(wrapper) :]
    return message


def _status(commands: list[str]) -> dict[str, Any]:
    return {
        "client_peer": {
            "capabilities": {
                "state": "announced",
                "announced_commands": list(commands),
            }
        }
    }


class ActionUseDoorToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_description_names_door_index_reach_and_the_follow_up_read(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        description = tools[COMMAND].description or ""
        for fragment in (
            "door_index",
            "0 <= door_index < 64",
            "action_use_door",
            "door_not_supported",
            "within 2 m",
            "object_doors",
            "player_teleport",
            "started still does not prove the door moved",
            "component=-1",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)

    async def test_without_door_index_the_command_and_args_stay_action_use(self) -> None:
        status = AsyncMock(side_effect=RuntimeError("status must not be read"))
        with patch.object(self.runtime, "bridge_status_payload", new=status):
            with patch.object(
                self.runtime,
                "call_bridge",
                new=AsyncMock(return_value={"ok": 1, "started": True}),
            ) as call:
                await self.app.call_tool(
                    COMMAND,
                    {
                        "action": "ActionOpenDoors",
                        "classname": "Land_House_2W03",
                        "timeout_s": 1.0,
                    },
                )
        status.assert_not_awaited()
        call.assert_awaited_once()
        forwarded = call.await_args.args
        self.assertEqual(forwarded[0], COMMAND)
        self.assertNotIn("door_index", forwarded[1])
        self.assertNotIn("target", forwarded[1])
        self.assertEqual(forwarded[1]["classname"], "Land_House_2W03")
        self.assertEqual(forwarded[2], "client")

    async def test_door_index_validation_is_bad_args_and_does_not_call(self) -> None:
        cases = (
            (
                {"action": "ActionOpenDoors", "door_index": 0, "timeout_s": 1.0},
                "classname",
                "non-empty string when door_index is set",
            ),
            (
                {
                    "action": "ActionOpenDoors",
                    "classname": "Land_House_2W03",
                    "target": "hands",
                    "door_index": 1,
                    "timeout_s": 1.0,
                },
                "door_index",
                "omitted unless target is world",
            ),
            (
                {
                    "action": "ActionOpenDoors",
                    "classname": "Land_House_2W03",
                    "door_index": 64,
                    "timeout_s": 1.0,
                },
                "door_index",
                "int from 0 to 63",
            ),
            (
                {
                    "action": "ActionOpenDoors",
                    "classname": "Land_House_2W03",
                    "door_index": -1,
                    "timeout_s": 1.0,
                },
                "door_index",
                "int from 0 to 63",
            ),
        )
        for arguments, field, expectation in cases:
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, arguments)
                message = _tool_error_text(raised.exception)
                self.assertTrue(message.startswith("bad_args: "), message)
                self.assertIn(field, message)
                self.assertIn(expectation, message)
                call.assert_not_awaited()

    async def test_unannounced_door_is_refused_before_the_bridge(self) -> None:
        statuses = (
            _status(["action_use", "action_use_target"]),
            _status([]),
        )
        for status_payload in statuses:
            with self.subTest(status=status_payload):
                call = AsyncMock(return_value={"ok": 1, "door_index": 1})
                with patch.object(
                    self.runtime,
                    "bridge_status_payload",
                    new=AsyncMock(return_value=status_payload),
                ):
                    with patch.object(self.runtime, "call_bridge", new=call):
                        with self.assertRaises(ToolError) as raised:
                            await self.app.call_tool(
                                COMMAND,
                                {
                                    "action": "ActionOpenDoors",
                                    "classname": "Land_House_2W03",
                                    "door_index": 1,
                                    "timeout_s": 1.0,
                                },
                            )
                self.assertIn("door_not_supported", _tool_error_text(raised.exception))
                call.assert_not_awaited()

    async def test_status_failure_is_door_not_supported(self) -> None:
        call = AsyncMock(return_value={"ok": 1, "door_index": 0})
        with patch.object(
            self.runtime,
            "bridge_status_payload",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ):
            with patch.object(self.runtime, "call_bridge", new=call):
                with self.assertRaises(ToolError) as raised:
                    await self.app.call_tool(
                        COMMAND,
                        {
                            "action": "ActionOpenDoors",
                            "classname": "Land_Shed_M1",
                            "door_index": 0,
                            "timeout_s": 1.0,
                        },
                    )
        self.assertIn("door_not_supported", _tool_error_text(raised.exception))
        call.assert_not_awaited()

    async def test_announced_door_sends_action_use_door_and_requires_the_echo(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        async def fake_bridge(
            cmd: str,
            args: dict[str, Any],
            peer: str,
            timeout_s: float,
        ) -> dict[str, Any]:
            calls.append((cmd, dict(args)))
            self.assertEqual(peer, "client")
            self.assertEqual(timeout_s, 1.0)
            if args["door_index"] == 0:
                return {"ok": 1, "started": True, "door_index": 0, "component_index": 4}
            return {"ok": 1, "started": True, "door_index": 0}

        with patch.object(
            self.runtime,
            "bridge_status_payload",
            new=AsyncMock(return_value=_status([DOOR_COMMAND])),
        ):
            with patch.object(self.runtime, "call_bridge", new=fake_bridge):
                opened = _content_json(
                    await self.app.call_tool(
                        COMMAND,
                        {
                            "action": "ActionOpenDoors",
                            "classname": "Land_House_2W03",
                            "pos": [1.0, 2.0, 3.0],
                            "radius": 8.0,
                            "door_index": 0,
                            "timeout_s": 1.0,
                        },
                    )
                )
                with self.assertRaises(ToolError) as raised:
                    await self.app.call_tool(
                        COMMAND,
                        {
                            "action": "ActionCloseDoors",
                            "classname": "Land_House_2W03",
                            "door_index": 2,
                            "timeout_s": 1.0,
                        },
                    )
        self.assertEqual(opened["door_index"], 0)
        self.assertEqual(opened["component_index"], 4)
        self.assertIn("door_not_supported", _tool_error_text(raised.exception))
        self.assertEqual(len(calls), 2)
        zero_cmd, zero_args = calls[0]
        other_cmd, other_args = calls[1]
        self.assertEqual(zero_cmd, DOOR_COMMAND)
        self.assertEqual(other_cmd, DOOR_COMMAND)
        self.assertEqual(zero_args["door_index"], 0)
        self.assertEqual(zero_args["classname"], "Land_House_2W03")
        self.assertEqual(zero_args["action"], "ActionOpenDoors")
        self.assertEqual(zero_args["pos"], [1.0, 2.0, 3.0])
        self.assertEqual(zero_args["radius"], 8.0)
        self.assertNotIn("target", zero_args)
        self.assertEqual(other_args["door_index"], 2)
        self.assertNotIn("pos", other_args)


class ActionUseDoorIngressTest(unittest.TestCase):
    def test_door_command_is_a_client_command_with_its_own_schema(self) -> None:
        self.assertIn(DOOR_COMMAND, loopback.CLIENT_COMMANDS)
        self.assertIn(DOOR_COMMAND, loopback.WHITELISTED_COMMANDS)
        self.assertEqual(loopback.peer_for_command(DOOR_COMMAND), "client")
        self.assertEqual(
            loopback.validate_command_args(
                DOOR_COMMAND,
                {
                    "action": "ActionOpenDoors",
                    "classname": "Land_House_2W03",
                    "door_index": 0,
                },
            ),
            (True, None),
        )
        self.assertEqual(
            loopback.validate_command_args(
                COMMAND,
                {"action": "ActionOpenDoors", "door_index": 0},
            ),
            (False, "bad_args"),
        )
        self.assertEqual(
            _BRIDGE_COMMAND_TOOLS["client"].get(DOOR_COMMAND), COMMAND
        )
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["peers"]["client"].get(DOOR_COMMAND), COMMAND)


class ActionUseDoorEnforceContractTest(unittest.TestCase):
    def test_caps_and_dispatch_name_the_command(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn(DOOR_COMMAND, announced_caps(source))
        self.assertIn(DOOR_COMMAND, dispatch_census(source))
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        branch = _if_body(dispatch, 'command.cmd == "action_use_door"')
        self.assertIn("DispatchActionUse(command, result)", branch)

    def test_door_branch_builds_the_component_target_and_names_the_errors(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchActionUse(")
        door = _if_body(body, 'command.cmd == "action_use_door"')
        self.assertIn("ACTION_USE_DOOR_COMPONENT_CAP", source)
        self.assertIn("ACTION_USE_DOOR_COMPONENT_CAP", door)
        for token in (
            "Building.Cast(targetObj)",
            "building.GetDoorCount()" if False else "doorBuilding.GetDoorCount()",
            "doorBuilding.GetDoorIndex(componentScan)",
            'doorComponentName.Contains("doorstwin")',
            "doorBuilding.GetActionComponentNameList(doorComponent, doorComponentNames)",
            "doorBuilding.GetSelectionPositionMS(doorSelection)",
            "doorBuilding.ModelToWorld(doorModelPos)",
            "new ActionTarget(doorBuilding, null, doorComponent, cursorHitPos, 0)",
            "result.door_index = wantedDoor",
            "result.component_index = doorComponent",
            'result.error = "not_a_building"',
            'result.error = "door_out_of_range"',
            'result.error = "door_component_not_found"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, door)
        self.assertLess(
            door.index("result.door_index = wantedDoor"),
            door.index('result.error = "not_a_building"'),
        )
        self.assertLess(
            door.index("doorBuilding.GetDoorIndex(componentScan)"),
            door.index('doorComponentName.Contains("doorstwin")'),
        )
        self.assertLess(
            door.index('doorComponentName.Contains("doorstwin")'),
            door.index("doorBuilding.GetSelectionPositionMS(doorSelection)"),
        )
        self.assertLess(
            door.index("doorBuilding.GetSelectionPositionMS(doorSelection)"),
            door.index("doorBuilding.ModelToWorld(doorModelPos)"),
        )
        self.assertLess(
            door.index("doorBuilding.ModelToWorld(doorModelPos)"),
            door.index(
                "new ActionTarget(doorBuilding, null, doorComponent, cursorHitPos, 0)"
            ),
        )
        self.assertIn(
            "new ActionTarget(targetObj, null, -1, cursorHitPos, 0)",
            body,
        )
        self.assertNotIn(
            "new ActionTarget(targetObj, null, -1, cursorHitPos, 0)",
            door,
        )

    def test_messages_keep_door_mode_on_the_command_name(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        args = _method_body(messages, "class MCPArgs")
        result = _method_body(messages, "class MCPResult")
        self.assertIn("int door_index;", args)
        self.assertIn("the command name, not this field, turns door mode on", args)
        self.assertIn("int door_index;", result)
        self.assertIn("int component_index;", result)
        self.assertIn("the command name, not these fields, is", result)


if __name__ == "__main__":
    unittest.main()
