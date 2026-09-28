from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback
from dayz_mcp.server import (
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    _compare_bridge_capabilities,
    build_app,
)
from tests._tiers import slow_test


COMMAND = "action_use"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"


@contextmanager
def _exceptions_are_fails(test: unittest.TestCase) -> Iterator[None]:
    try:
        yield
    except test.failureException:
        raise
    except Exception as exc:
        test.fail(str(exc))


def _method_body(source: str, signature: str) -> str:
    try:
        start = source.index(signature)
        brace = source.index("{", start)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc
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
    start = source.find(needle)
    if start < 0:
        raise AssertionError(f"missing if ({condition})")
    try:
        brace = source.index("{", start)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated if ({condition})")


def _brace_block_end(source: str, start: int) -> int:
    try:
        brace = source.index("{", start)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise AssertionError("unterminated brace block")


def _if_else_chain(source: str, first_condition: str) -> tuple[str, str]:
    needle = f"if ({first_condition})"
    start = source.find(needle)
    if start < 0:
        raise AssertionError(f"missing if ({first_condition})")
    cursor = _brace_block_end(source, start)
    while True:
        rest = source[cursor:].lstrip()
        cursor = cursor + (len(source[cursor:]) - len(rest))
        if rest.startswith("else if"):
            cursor = _brace_block_end(source, cursor)
            continue
        if rest.startswith("else"):
            cursor = _brace_block_end(source, cursor)
        break
    return source[start:cursor], source[cursor:]


def _else_body_following(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    start = source.find(needle)
    if start < 0:
        raise AssertionError(f"missing if ({condition})")
    cursor = _brace_block_end(source, start)
    rest = source[cursor:].lstrip()
    cursor = cursor + (len(source[cursor:]) - len(rest))
    if rest.startswith("else if") or not rest.startswith("else"):
        raise AssertionError(f"missing else after if ({condition})")
    try:
        brace = source.index("{", cursor)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError("unterminated else")


def _class_body(source: str, class_name: str) -> str:
    return _method_body(source, f"class {class_name}")


def _content_json(content: Any) -> dict[str, Any]:
    if isinstance(content, tuple):
        _blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = _blocks
    text = content[0].text
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise AssertionError(f"expected dict content, got {parsed!r}")
    return parsed


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    if message.startswith(wrapper):
        return message[len(wrapper) :]
    return message


def _status_payload(state: str, commands: list[str] | None = None) -> dict[str, Any]:
    return {
        "client_peer": {
            "capabilities": {
                "state": state,
                "announced_commands": list(commands or []),
            }
        }
    }


def _announced_status() -> dict[str, Any]:
    return _status_payload("announced", ["action_use", "action_use_target"])


class Fb3fc1ActionUseToolTest(unittest.IsolatedAsyncioTestCase):
    async def _call(
        self,
        arguments: dict[str, Any],
        bridge_result: dict[str, Any] | None = None,
        status: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        app, runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        returned = dict(bridge_result or {"ok": 1, "started": True})
        status_payload = status if status is not None else _announced_status()
        with patch.object(
            runtime,
            "bridge_status_payload",
            new=AsyncMock(return_value=status_payload),
        ):
            with patch.object(
                runtime,
                "call_bridge",
                new=AsyncMock(return_value=returned),
            ) as call:
                result = _content_json(await app.call_tool(COMMAND, arguments))
        forwarded = call.await_args.args[1]
        return result, forwarded

    @slow_test
    async def test_3fc1_default_and_world_omit_target_hands_self_send_it(self) -> None:
        with _exceptions_are_fails(self):
            _result, default_args = await self._call(
                {"action": "ActionDrink", "timeout_s": 1.0}
            )
            self.assertNotIn("target", default_args)
            self.assertEqual(default_args["action"], "ActionDrink")
            self.assertIn("radius", default_args)

            _world_result, world_args = await self._call(
                {"action": "ActionDrink", "target": "world", "timeout_s": 1.0}
            )
            self.assertNotIn("target", world_args)
            self.assertEqual(world_args["action"], "ActionDrink")

            _hands_result, hands_args = await self._call(
                {
                    "action": "ActionDrink",
                    "target": "hands",
                    "timeout_s": 1.0,
                },
                {"ok": 1, "started": True, "target": "hands"},
            )
            self.assertEqual(hands_args["target"], "hands")
            self.assertEqual(hands_args["action"], "ActionDrink")

            _self_result, self_args = await self._call(
                {
                    "action": "ActionDrink",
                    "target": "self",
                    "timeout_s": 1.0,
                },
                {"ok": 1, "started": True, "target": "self"},
            )
            self.assertEqual(self_args["target"], "self")
            self.assertEqual(self_args["action"], "ActionDrink")

    async def test_3fc1_invalid_target_pos_and_self_classname_are_bad_args(self) -> None:
        with _exceptions_are_fails(self):
            app, runtime = build_app(
                ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
            )
            with patch.object(
                runtime,
                "bridge_status_payload",
                new=AsyncMock(return_value=_announced_status()),
            ):
                with patch.object(runtime, "call_bridge", new=AsyncMock()) as call:
                    with self.assertRaises(ToolError) as raised_target:
                        await app.call_tool(
                            COMMAND,
                            {
                                "action": "ActionDrink",
                                "target": "cursor",
                                "timeout_s": 1.0,
                            },
                        )
                    self.assertIn("bad_args", _tool_error_text(raised_target.exception))

                    with self.assertRaises(ToolError) as raised_pos_hands:
                        await app.call_tool(
                            COMMAND,
                            {
                                "action": "ActionDrink",
                                "target": "hands",
                                "pos": [1.0, 2.0, 3.0],
                                "timeout_s": 1.0,
                            },
                        )
                    self.assertIn("bad_args", _tool_error_text(raised_pos_hands.exception))

                    with self.assertRaises(ToolError) as raised_pos_self:
                        await app.call_tool(
                            COMMAND,
                            {
                                "action": "ActionDrink",
                                "target": "self",
                                "pos": [1.0, 2.0, 3.0],
                                "timeout_s": 1.0,
                            },
                        )
                    self.assertIn("bad_args", _tool_error_text(raised_pos_self.exception))

                    with self.assertRaises(ToolError) as raised_classname:
                        await app.call_tool(
                            COMMAND,
                            {
                                "action": "ActionDrink",
                                "target": "self",
                                "classname": "WaterBottle",
                                "timeout_s": 1.0,
                            },
                        )
                    self.assertIn("bad_args", _tool_error_text(raised_classname.exception))
            call.assert_not_awaited()

    async def test_3fc1_hands_without_echo_is_target_not_supported(self) -> None:
        with _exceptions_are_fails(self):
            app, runtime = build_app(
                ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
            )
            with patch.object(
                runtime,
                "bridge_status_payload",
                new=AsyncMock(return_value=_announced_status()),
            ):
                with patch.object(
                    runtime,
                    "call_bridge",
                    new=AsyncMock(return_value={"ok": 1, "started": True}),
                ):
                    with self.assertRaises(ToolError) as raised:
                        await app.call_tool(
                            COMMAND,
                            {
                                "action": "ActionDrink",
                                "target": "hands",
                                "timeout_s": 1.0,
                            },
                        )
            message = _tool_error_text(raised.exception)
            self.assertIn("target_not_supported", message)

    async def test_3fc1_hands_with_echo_returns_the_result_unchanged(self) -> None:
        with _exceptions_are_fails(self):
            echoed = {
                "ok": 1,
                "started": True,
                "target": "hands",
                "classname": "WaterBottle",
            }
            result, forwarded = await self._call(
                {
                    "action": "ActionDrink",
                    "target": "hands",
                    "timeout_s": 1.0,
                },
                echoed,
            )
            self.assertEqual(forwarded["target"], "hands")
            self.assertEqual(result, echoed)

    @slow_test
    async def test_3fc1_r2_old_addon_is_refused_before_any_bridge_call(self) -> None:
        with _exceptions_are_fails(self):
            statuses = (
                _status_payload("announced", ["action_use", "camera_get"]),
                _status_payload("unknown", []),
            )
            for status in statuses:
                for target in ("hands", "self"):
                    app, runtime = build_app(
                        ServerConfig(
                            key="test-key",
                            port=0,
                            log_sink=lambda _message: None,
                        )
                    )
                    call = AsyncMock(return_value={"ok": 1, "started": True})
                    with patch.object(
                        runtime,
                        "bridge_status_payload",
                        new=AsyncMock(return_value=status),
                    ):
                        with patch.object(runtime, "call_bridge", new=call):
                            with self.assertRaises(ToolError) as raised:
                                await app.call_tool(
                                    COMMAND,
                                    {
                                        "action": "ActionDrink",
                                        "target": target,
                                        "timeout_s": 1.0,
                                    },
                                )
                    self.assertIn(
                        "target_not_supported",
                        _tool_error_text(raised.exception),
                    )
                    call.assert_not_awaited()

    @slow_test
    async def test_3fc1_r2_legacy_error_cannot_mask_the_refusal(self) -> None:
        with _exceptions_are_fails(self):
            statuses = (
                _status_payload("announced", ["action_use", "camera_get"]),
                _status_payload("unknown", []),
            )
            for status in statuses:
                for target in ("hands", "self"):
                    app, runtime = build_app(
                        ServerConfig(
                            key="test-key",
                            port=0,
                            log_sink=lambda _message: None,
                        )
                    )
                    call = AsyncMock(side_effect=ToolError("target_not_found"))
                    with patch.object(
                        runtime,
                        "bridge_status_payload",
                        new=AsyncMock(return_value=status),
                    ):
                        with patch.object(runtime, "call_bridge", new=call):
                            with self.assertRaises(ToolError) as raised:
                                await app.call_tool(
                                    COMMAND,
                                    {
                                        "action": "ActionDrink",
                                        "target": target,
                                        "timeout_s": 1.0,
                                    },
                                )
                    self.assertIn(
                        "target_not_supported",
                        _tool_error_text(raised.exception),
                    )
                    call.assert_not_awaited()

    async def test_3fc1_r2_announced_addon_gets_action_use_target(self) -> None:
        with _exceptions_are_fails(self):
            app, runtime = build_app(
                ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
            )
            calls: list[tuple[str, dict[str, Any]]] = []

            async def fake_bridge(
                cmd: str,
                args: dict[str, Any],
                peer: str,
                timeout_s: float,
            ) -> dict[str, Any]:
                forwarded = dict(args)
                calls.append((cmd, forwarded))
                result = dict(forwarded)
                result["ok"] = 1
                result["started"] = True
                return result

            with patch.object(
                runtime,
                "bridge_status_payload",
                new=AsyncMock(return_value=_announced_status()),
            ):
                with patch.object(runtime, "call_bridge", new=fake_bridge):
                    await app.call_tool(
                        COMMAND,
                        {
                            "action": "ActionDrink",
                            "target": "hands",
                            "timeout_s": 1.0,
                        },
                    )
                    await app.call_tool(
                        COMMAND,
                        {
                            "action": "ActionDrink",
                            "target": "self",
                            "timeout_s": 1.0,
                        },
                    )
                    await app.call_tool(
                        COMMAND,
                        {"action": "ActionDrink", "timeout_s": 1.0},
                    )
                    await app.call_tool(
                        COMMAND,
                        {
                            "action": "ActionDrink",
                            "target": "world",
                            "timeout_s": 1.0,
                        },
                    )
            self.assertEqual(len(calls), 4)
            hands_cmd, hands_args = calls[0]
            self_cmd, self_args = calls[1]
            default_cmd, default_args = calls[2]
            world_cmd, world_args = calls[3]
            self.assertEqual(hands_cmd, "action_use_target")
            self.assertEqual(hands_args["target"], "hands")
            self.assertEqual(self_cmd, "action_use_target")
            self.assertEqual(self_args["target"], "self")
            self.assertEqual(default_cmd, "action_use")
            self.assertNotIn("target", default_args)
            self.assertEqual(default_args["action"], "ActionDrink")
            self.assertIn("radius", default_args)
            self.assertEqual(world_cmd, "action_use")
            self.assertNotIn("target", world_args)
            self.assertEqual(world_args["action"], "ActionDrink")
            self.assertIn("radius", world_args)


class Fb3fc1ActionUseWireTest(unittest.TestCase):
    def test_3fc1_loopback_accepts_three_targets_and_rejects_others(self) -> None:
        with _exceptions_are_fails(self):
            self.assertEqual(
                loopback.validate_command_args(COMMAND, {"action": "ActionDrink"}),
                (True, None),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    COMMAND, {"action": "ActionDrink", "target": "world"}
                ),
                (False, "bad_args"),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    COMMAND, {"action": "ActionDrink", "target": "hands"}
                ),
                (False, "bad_args"),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    COMMAND, {"action": "ActionDrink", "target": "self"}
                ),
                (False, "bad_args"),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    COMMAND, {"action": "ActionDrink", "target": "cursor"}
                ),
                (False, "bad_args"),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    COMMAND, {"action": "ActionDrink", "target": 1}
                ),
                (False, "bad_args"),
            )

    def test_3fc1_r2_loopback_routes_and_validates_action_use_target(self) -> None:
        with _exceptions_are_fails(self):
            self.assertIn("action_use_target", loopback.CLIENT_COMMANDS)
            self.assertIn("action_use_target", loopback.WHITELISTED_COMMANDS)
            self.assertEqual(
                loopback.peer_for_command("action_use_target"), "client"
            )
            self.assertEqual(
                loopback.validate_command_args(
                    "action_use_target",
                    {"action": "ActionDrink", "target": "hands"},
                ),
                (True, None),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    "action_use_target",
                    {"action": "ActionDrink", "target": "self"},
                ),
                (True, None),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    "action_use_target",
                    {"action": "ActionDrink", "target": "world"},
                ),
                (False, "bad_args"),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    "action_use_target",
                    {"action": "ActionDrink", "target": "cursor"},
                ),
                (False, "bad_args"),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    "action_use_target",
                    {
                        "action": "ActionDrink",
                        "target": "hands",
                        "pos": [1.0, 2.0, 3.0],
                    },
                ),
                (False, "bad_args"),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    COMMAND, {"action": "ActionDrink", "target": "hands"}
                ),
                (False, "bad_args"),
            )
            self.assertEqual(
                loopback.validate_command_args(
                    COMMAND, {"action": "ActionDrink", "target": "world"}
                ),
                (False, "bad_args"),
            )


class Fb3fc1ActionUseCensusTest(unittest.TestCase):
    def test_3fc1_r2_census_maps_action_use_target_to_action_use(self) -> None:
        with _exceptions_are_fails(self):
            mapping = _BRIDGE_COMMAND_TOOLS["client"]
            self.assertEqual(mapping.get("action_use_target"), "action_use")
            registered = frozenset({"action_use"})
            only_legacy = _compare_bridge_capabilities(
                "client",
                {
                    "state": "announced",
                    "announced_commands": ["action_use"],
                },
                registered,
            )
            self.assertEqual(only_legacy["state"], "match")
            both = _compare_bridge_capabilities(
                "client",
                {
                    "state": "announced",
                    "announced_commands": ["action_use", "action_use_target"],
                },
                registered,
            )
            self.assertEqual(both["state"], "match")
            self.assertEqual(both["unmapped_announced_commands"], [])


class Fb3fc1ActionUseEnforceContractTest(unittest.TestCase):
    def test_3fc1_dispatch_hands_self_world_and_result_target(self) -> None:
        with _exceptions_are_fails(self):
            source = BRIDGE_PATH.read_text(encoding="utf-8")
            body = _method_body(source, "protected bool DispatchActionUse(")
            hands = _if_body(body, 'targetMode == "hands"')
            self_body = _if_body(body, 'targetMode == "self"')
            self.assertIn("GetItemInHands", hands)
            self.assertIn('result.error = "no_item_in_hands"', hands)
            self.assertIn('result.error = "held_item_mismatch"', hands)
            self.assertIn("GetHierarchyParent()", hands)
            self.assertIn(
                "new ActionTarget(targetItem, targetParent, -1, vector.Zero, -1)",
                hands,
            )
            self.assertIn(
                "new ActionTarget(null, null, -1, vector.Zero, -1)",
                self_body,
            )
            self.assertIn("FindNearestObjectNearClient", body)
            self.assertNotIn("FindNearestObjectNearClient", hands)
            self.assertNotIn("FindNearestObjectNearClient", self_body)
            self.assertIn("result.target = targetMode", body)
            self.assertIn('command.cmd == "action_use_target"', source)
            self.assertIn("action_use_target", source)

            messages = MESSAGES_PATH.read_text(encoding="utf-8")
            args_body = _class_body(messages, "MCPArgs")
            result_body = _class_body(messages, "MCPResult")
            self.assertIn("string target", args_body)
            self.assertIn("string target", result_body)

    def test_3fc1_world_path_still_reports_target_not_found(self) -> None:
        with _exceptions_are_fails(self):
            source = BRIDGE_PATH.read_text(encoding="utf-8")
            body = _method_body(source, "protected bool DispatchActionUse(")
            hands = _if_body(body, 'targetMode == "hands"')
            self_body = _if_body(body, 'targetMode == "self"')
            self.assertIn('result.error = "target_not_found"', body)
            self.assertNotIn("target_not_found", hands)
            self.assertNotIn("target_not_found", self_body)
            self.assertIn("FindNearestObjectNearClient", body)

    def test_3fc1_dispatch_common_checks_follow_target_branches(self) -> None:
        with _exceptions_are_fails(self):
            source = BRIDGE_PATH.read_text(encoding="utf-8")
            body = _method_body(source, "protected bool DispatchActionUse(")
            _chain, after = _if_else_chain(body, 'targetMode == "hands"')
            world = _else_body_following(body, 'targetMode == "self"')
            needles = (
                "GetRunningAction",
                "ActionPossibilityCheck",
                "CanStoreInputUserData",
                "action.Can",
                "PerformActionStart",
                "result.started",
            )
            for needle in needles:
                self.assertIn(needle, after)
                self.assertNotIn(needle, world)
            self.assertIn("FindNearestObjectNearClient", world)


if __name__ == "__main__":
    unittest.main()
