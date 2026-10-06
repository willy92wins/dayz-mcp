"""Door component cap (6d21) and action_use_component (fde3)."""
from __future__ import annotations

import re
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback
from dayz_mcp.bridge_readiness import _BRIDGE_COMMAND_TOOLS
from dayz_mcp.server import ServerConfig, ToolError, build_app
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census


COMMAND = "action_use"
COMPONENT_COMMAND = "action_use_component"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
ENFORCE_INT_MAX = 2_147_483_647


def _content_json(content: Any) -> dict[str, Any]:
    if isinstance(content, tuple):
        _blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = _blocks
    import json

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


def _component_lookup_accepted(component_state: int) -> bool:
    """DispatchActionUse keeps GetActionComponentNameList results of 0 and 1.

    -1 is not found. 0 is a valid default component. 1 is a valid named
    component (object.c:197-198).
    """
    return component_state >= 0


def _first_door_component(door_at: dict[int, int], wanted: int, cap: int) -> int:
    """The scan in DispatchActionUse: first index whose door matches, else -1.

    Stops at the cap. A later match is ignored once one is found. Indices the
    map omits are not that door, the same way GetDoorIndex returns -1.
    """
    door_component = -1
    component_scan = 0
    while component_scan < cap and door_component < 0:
        if door_at.get(component_scan, -1) == wanted:
            door_component = component_scan
        component_scan = component_scan + 1
    return door_component


class DoorComponentCapTest(unittest.TestCase):
    def test_scan_finds_876_a_low_index_and_no_match(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"protected const int ACTION_USE_DOOR_COMPONENT_CAP = (\d+);", source
        )
        self.assertIsNotNone(match)
        cap = int(match.group(1))
        self.assertEqual(cap, 2048)
        self.assertIn(
            "while (componentScan < ACTION_USE_DOOR_COMPONENT_CAP && doorComponent < 0)",
            source,
        )
        self.assertIn('doorComponentName.Contains("doorstwin")', source)

        sole_high = {876: 5}
        self.assertEqual(_first_door_component(sole_high, 5, cap), 876)
        # The old cap of 512 cannot see component 876.
        self.assertEqual(_first_door_component(sole_high, 5, 512), -1)

        low = {0: 1, 4: 5, 90: 5}
        self.assertEqual(_first_door_component(low, 5, cap), 4)

        self.assertEqual(_first_door_component({3: 1}, 5, cap), -1)
        # First match wins: a higher index that also matches is not selected.
        self.assertEqual(_first_door_component({10: 2, 900: 2}, 2, cap), 10)


class ActionUseComponentToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    def _patch(self, commands: list[str], bridge: Any):
        return (
            patch.object(
                self.runtime,
                "bridge_status_payload",
                new=AsyncMock(return_value=_status(commands)),
            ),
            patch.object(self.runtime, "call_bridge", new=bridge),
        )

    async def test_index_zero_and_876_forward_on_the_component_command(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        async def fake_bridge(cmd: str, args: dict[str, Any], peer: str, timeout_s: float):
            calls.append((cmd, dict(args)))
            self.assertEqual(peer, "client")
            return {
                "ok": 1,
                "started": True,
                "component_index": args["component_index"],
                "target": "world",
            }

        status, bridge = self._patch([COMPONENT_COMMAND], fake_bridge)
        with status, bridge:
            for index in (0, 876):
                opened = _content_json(
                    await self.app.call_tool(
                        COMMAND,
                        {
                            "action": "ActionWorldLiquidActionSwitch",
                            "classname": "Land_Misc_Well",
                            "component_index": index,
                            "cursor_pos": [10.0, 1.5, 20.0],
                            "timeout_s": 1.0,
                        },
                    )
                )
                self.assertEqual(opened["component_index"], index)
        self.assertEqual([cmd for cmd, _args in calls], [COMPONENT_COMMAND, COMPONENT_COMMAND])
        self.assertEqual(calls[0][1]["component_index"], 0)
        self.assertEqual(calls[1][1]["component_index"], 876)
        self.assertEqual(calls[0][1]["cursor_pos"], [10.0, 1.5, 20.0])
        self.assertNotIn("door_index", calls[0][1])
        self.assertNotIn("target", calls[0][1])

    async def test_omitted_component_stays_on_action_use(self) -> None:
        async def fake_bridge(cmd: str, args: dict[str, Any], peer: str, timeout_s: float):
            self.assertEqual(cmd, COMMAND)
            self.assertNotIn("component_index", args)
            return {"ok": 1, "started": True, "target": "world"}

        _status_patch, bridge = self._patch([COMPONENT_COMMAND], fake_bridge)
        with bridge:
            await self.app.call_tool(
                COMMAND, {"action": "ActionOpenDoors", "timeout_s": 1.0}
            )

    async def _refused(self, arguments: dict[str, Any]) -> str:
        with self.assertRaises(ToolError) as raised:
            await self.app.call_tool(COMMAND, arguments)
        return _tool_error_text(raised.exception)

    async def test_bool_negative_cursor_target_and_door_are_refused(self) -> None:
        call = AsyncMock(return_value={"ok": 1, "component_index": 0})
        _status_patch, bridge = self._patch([COMPONENT_COMMAND], call)
        base = {
            "action": "ActionOpenDoors",
            "classname": "Land_Misc_Well",
            "timeout_s": 1.0,
        }
        with _status_patch, bridge:
            bool_error = await self._refused(
                {**base, "component_index": True, "cursor_pos": [0, 0, 0]}
            )
            self.assertTrue(
                "bad_args" in bool_error or "int_type" in bool_error, bool_error
            )
            self.assertIn("bad_args", await self._refused({**base, "component_index": -1, "cursor_pos": [0, 0, 0]}))
            self.assertIn(
                "bad_args",
                await self._refused({**base, "component_index": ENFORCE_INT_MAX + 1, "cursor_pos": [0, 0, 0]}),
            )
            self.assertIn("bad_args", await self._refused({**base, "component_index": 1}))
            self.assertIn("bad_args", await self._refused({**base, "cursor_pos": [1.0, 2.0, 3.0]}))
            self.assertIn(
                "bad_args",
                await self._refused({**base, "component_index": 1, "cursor_pos": [1.0, float("nan"), 0.0]}),
            )
            self.assertIn(
                "bad_args",
                await self._refused(
                    {
                        **base,
                        "component_index": 1,
                        "cursor_pos": [0, 0, 0],
                        "target": "hands",
                    }
                ),
            )
            self.assertIn(
                "bad_args",
                await self._refused(
                    {
                        **base,
                        "component_index": 1,
                        "cursor_pos": [0, 0, 0],
                        "door_index": 0,
                    }
                ),
            )
        call.assert_not_awaited()

    async def test_old_pbo_is_component_not_supported_without_a_call(self) -> None:
        call = AsyncMock(return_value={"ok": 1, "component_index": 2})
        status, bridge = self._patch(["action_use", "action_use_door"], call)
        with status, bridge:
            text = await self._refused(
                {
                    "action": "ActionOpenDoors",
                    "classname": "Land_Misc_Well",
                    "component_index": 2,
                    "cursor_pos": [1.0, 2.0, 3.0],
                    "timeout_s": 1.0,
                }
            )
        self.assertIn("component_not_supported", text)
        call.assert_not_awaited()

    async def test_echo_mismatch_is_component_not_supported(self) -> None:
        async def fake_bridge(cmd: str, args: dict[str, Any], peer: str, timeout_s: float):
            return {"ok": 1, "started": True, "component_index": 0}

        status, bridge = self._patch([COMPONENT_COMMAND], fake_bridge)
        with status, bridge:
            text = await self._refused(
                {
                    "action": "ActionOpenDoors",
                    "classname": "Land_Misc_Well",
                    "component_index": 876,
                    "cursor_pos": [1.0, 2.0, 3.0],
                    "timeout_s": 1.0,
                }
            )
        self.assertIn("component_not_supported", text)

    async def test_bridge_component_not_found_is_not_rewritten(self) -> None:
        # call_bridge raises business errors before the echo check. A lookup
        # refusal must stay component_not_found.
        async def fake_bridge(cmd: str, args: dict[str, Any], peer: str, timeout_s: float):
            raise ToolError("component_not_found")

        status, bridge = self._patch([COMPONENT_COMMAND], fake_bridge)
        with status, bridge:
            text = await self._refused(
                {
                    "action": "ActionOpenDoors",
                    "classname": "Land_Misc_Well",
                    "component_index": 876,
                    "cursor_pos": [1.0, 2.0, 3.0],
                    "timeout_s": 1.0,
                }
            )
        self.assertIn("component_not_found", text)
        self.assertNotIn("component_not_supported", text)


class ActionUseComponentContractTest(unittest.TestCase):
    def test_command_is_advertised_on_the_client_wire_and_the_map(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn(COMPONENT_COMMAND, announced_caps(source))
        self.assertIn(COMPONENT_COMMAND, dispatch_census(source))
        self.assertEqual(announced_caps(source), sorted(announced_caps(source)))
        self.assertIn(COMPONENT_COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMPONENT_COMMAND), "client")
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["client"][COMPONENT_COMMAND], COMMAND)
        ok, err = loopback.validate_command_args(
            COMPONENT_COMMAND,
            {
                "action": "ActionOpenDoors",
                "classname": "Land_Misc_Well",
                "component_index": 876,
                "cursor_pos": [1.0, 2.0, 3.0],
            },
        )
        self.assertEqual((ok, err), (True, None))
        self.assertEqual(
            loopback.validate_command_args(
                "action_use",
                {"action": "ActionOpenDoors", "component_index": 2},
            ),
            (False, "bad_args"),
        )

    def test_bridge_looks_up_one_component_and_uses_the_supplied_cursor(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        start = source.index('command.cmd == "action_use_component"')
        # The second occurrence is the world-target branch, not the dispatcher.
        start = source.index('command.cmd == "action_use_component"', start + 1)
        end = source.index("actionTarget = new ActionTarget(targetObj, null, -1", start)
        branch = source[start:end]
        self.assertEqual(
            branch.count("GetActionComponentNameList(wantedComponent, componentNames)"),
            1,
        )
        self.assertIn("if (componentState < 0)", branch)
        self.assertNotIn("componentState <= 0", branch)
        self.assertIn('result.error = "component_not_found"', branch)
        self.assertIn("cursorHitPos = suppliedCursor", branch)
        self.assertNotIn("GetDoorIndex", branch)
        self.assertNotIn("componentScan", branch)

    def test_lookup_keeps_default_zero_and_named_one_and_rejects_minus_one(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        start = source.index('if (componentState < 0)')
        window = source[start:start + 180]
        self.assertIn('result.error = "component_not_found"', window)
        # The refusal is the negative result only, so 0 proceeds to the target.
        self.assertLess(
            source.index("if (componentState < 0)"),
            source.index("cursorHitPos = suppliedCursor"),
        )
        self.assertEqual(
            [_component_lookup_accepted(state) for state in (-1, 0, 1)],
            [False, True, True],
        )


if __name__ == "__main__":
    unittest.main()
