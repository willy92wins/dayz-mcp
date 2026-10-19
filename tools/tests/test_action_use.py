from __future__ import annotations

import re
import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean, method_body
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import command_requires_lease


COMMAND = "action_use"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
HOLD_PATH = addon_root() / "scripts" / "4_World" / "MCP_ActionHold.c"
DISPATCH = "protected bool DispatchActionUse(MCPCommand command, MCPResult result)"
HOLD_OVERRIDE = (
    "override void PerformActionStart(ActionBase action, ActionTarget target, "
    "ItemBase item, Param extra_data = NULL)"
)

# The 1.30 guard, compared without the whitespace outside its string literal
# once method_body() has blanked the comments. Its words and their order let
# three mutants live (#249 K1), a brace count three more (K2) and spaces in
# the error code one (K6); test_can_be_started_mutants_are_red has them.
CAN_BE_STARTED_GUARD = """
if (!action.CanBeStarted())
{
    result.ok = false;
    result.error = "condition_failed";
    return true;
}
"""
_GUARD = re.compile(
    r"^[ \t]*#ifndef[ \t]+DAYZ_1_29[ \t]*\n(?P<block>.*?)^[ \t]*#endif\b", re.M | re.S
)
_STRING_OR_BRACE = re.compile(r'"(?:\\.|[^"\\])*"|[{}]')
_STRING = re.compile(r'("(?:\\.|[^"\\])*")')
# The calls that start an action without asking CanBeStarted()
# (actionmanagerclient.c:792, 1055-1090 and 1172-1190; #249 K4).
_START = re.compile(
    r"\b(?:ActionStart|PerformActionStart|PerformActionFromQuickbar"
    r"|PerformActionFromInventory|OnInstantAction)\b"
)


def _bare(text: str) -> str:
    parts = _STRING.split(text)
    return "".join(
        part if index % 2 else re.sub(r"\s+", "", part) for index, part in enumerate(parts)
    )


def _code(text: str) -> str:
    # A name inside a string literal is not a call (#249 K7).
    return _STRING.sub('""', text)


def _one_guard(text: str) -> re.Match[str]:
    guards = list(_GUARD.finditer(text))
    if len(guards) != 1:
        raise AssertionError("one 1.29 guard: 1.29 has no CanBeStarted()")
    return guards[0]


def verify_can_be_started_guard(source: str) -> None:
    body = method_body(source, DISPATCH)
    if len(re.findall(r"\bCanBeStarted\b", _code(body))) != 1:
        raise AssertionError("one CanBeStarted() check")
    guard = _one_guard(body)
    before, block = body[: guard.start()], guard.group("block")
    if _bare(block) != _bare(CAN_BE_STARTED_GUARD):
        raise AssertionError(f"not the negative of Can(): {block!r}")
    # A statement of the method itself, so the five commands all ask it: no
    # brace or #if open before it, and not the body of an if, else or loop
    # without braces. Comments are blank and string literals do not count.
    depth = sum({"{": 1, "}": -1}.get(token, 0) for token in _STRING_OR_BRACE.findall(before))
    if depth != 0:
        raise AssertionError("the guard is inside a branch")
    if len(re.findall(r"^[ \t]*#if", before, re.M)) != len(re.findall(r"^[ \t]*#endif\b", before, re.M)):
        raise AssertionError("the guard is inside another #if")
    code = re.sub(r"^[ \t]*#.*$", "", before, flags=re.M).rstrip()
    if code and code[-1] not in ";}":
        raise AssertionError("the guard is the body of a statement without braces")
    if _bare("if (!action.Can(player, actionTarget, heldItem))") not in _bare(before):
        raise AssertionError("CanBeStarted() is asked before Can()")
    # Every start in the file comes after the guard in this method, so neither
    # a helper nor another start call runs first (#249 K4, K5).
    after = body[guard.end() :]
    if len(_START.findall(_code(clean(source)))) != len(_START.findall(_code(after))):
        raise AssertionError("an action starts before CanBeStarted() is asked")


def _around_guard(source: str, before: str, after: str) -> str:
    guard = _one_guard(source)
    return source[: guard.start()] + before + guard.group() + after + source[guard.end() :]


def _in_guard(source: str, pattern: str, replacement: str) -> str:
    guard = _one_guard(source)
    block, count = re.subn(pattern, replacement, guard.group("block"))
    if count != 1:
        raise AssertionError(f"pattern must match once in the guard: {pattern!r}")
    return source[: guard.start("block")] + block + source[guard.end("block") :]


class ActionUseIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_happy_path_client_mutating(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertTrue(command_requires_lease(COMMAND))
        status, body = self.state.enqueue_command(COMMAND, {"action": "ActionOpenDoors"})
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "client")


class ActionUseHeldItemContractTest(unittest.TestCase):
    def test_dispatch_passes_real_item_in_hands(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = method_body(source, DISPATCH)
        self.assertIn("GetItemInHands()", body)
        self.assertIn("ItemBase heldItem", body)
        self.assertIn("heldItem = player.GetItemInHands()", body)
        self.assertIn("action.Can(player, actionTarget, heldItem)", body)
        self.assertIn(
            "amc.PerformActionStart(action, actionTarget, heldItem, NULL)", body
        )
        self.assertNotIn("action.Can(player, actionTarget, null)", body)
        self.assertNotIn(
            "amc.PerformActionStart(action, actionTarget, null, NULL)", body
        )


class ActionUseCanBeStartedTest(unittest.TestCase):
    # Known issue of 1.4: 1.30 starts an input's action only when CanBeStarted()
    # is true (actionmanagerclient.c:336), and PerformActionStart does not ask,
    # so action_use and action_hold started what that path would not.
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")

    def test_can_be_started_is_asked_after_can_and_before_any_start(self) -> None:
        verify_can_be_started_guard(self.source)

    def test_can_be_started_mutants_are_red(self) -> None:
        source = self.source
        door = 'if (command.cmd == "action_use_door")\n'
        helper = (
            "protected bool StartDoor(ActionManagerClient amc, ActionBase action, "
            "ActionTarget target, ItemBase item)\n{\n"
            "amc.PerformActionStart(action, target, item, NULL);\nreturn true;\n}\n\n"
        )

        def door_first(base: str, call: str) -> str:
            return _around_guard(base, door + "{\n" + call + "\nreturn true;\n}\n", "")

        mutants = {
            # The guard for action_use_door alone leaves the other four
            # commands starting what 1.30 would not.
            "door_only": _around_guard(source, door + "{\n", "\n}"),
            "door_only_unbraced": _around_guard(source, door, ""),
            "door_only_comment_brace": _around_guard(source, door + "{ // }\n", "\n} // {"),
            "door_only_string_brace": _around_guard(source, door + '{ string unused = "}";\n', "\n}"),
            "inside_another_if": _around_guard(source, "#ifdef DAYZ_MCP_NEVER\n", "\n#endif"),
            "ok_true": _in_guard(source, r"result\s*\.\s*ok\s*=\s*false\s*;", "result.ok = true;"),
            "error_code_with_a_space": _in_guard(source, r'"condition_failed"', '"condition_ failed"'),
            # Also stops every allowed start.
            "return_outside_if": _in_guard(
                source, r"return\s+true\s*;((?:[ \t]*//[^\n]*)?\s*)\}", r"}\1return true;"
            ),
            # Starts that skip the guard for action_use_door (#249 K4, K5).
            "door_quickbar": door_first(source, "amc.PerformActionFromQuickbar(heldItem, null);"),
            "door_inventory": door_first(source, "amc.PerformActionFromInventory(heldItem, null);"),
            "door_instant": door_first(source, "amc.OnInstantAction(ActionOpenDoors);"),
            "door_helper": door_first(
                source.replace(DISPATCH, helper + DISPATCH, 1),
                "return StartDoor(amc, action, actionTarget, heldItem);",
            ),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                with self.assertRaises(AssertionError):
                    verify_can_be_started_guard(mutant)

    def test_harmless_edits_keep_the_guard_green(self) -> None:
        # #249 K3, K7: layout, comments, and braces or names in a comment or a
        # string are not the guard's business.
        source = self.source
        variants = {
            "tabs_and_spaces_swapped": (
                source.replace("\t", "    ") if "\t" in source else source.replace("    ", "\t")
            ),
            "comment_in_the_if": _in_guard(source, r"(result\s*\.\s*ok\s*=)", r"// why\n\1"),
            "trailing_comment": _in_guard(source, r"(return\s+true\s*;)", r"\1 // done"),
            "braces_in_a_comment_and_a_string": _around_guard(
                source, '// } {\nstring unused = "}";\n', ""
            ),
            "names_in_a_string": _around_guard(
                source, 'string unused = "CanBeStarted() before PerformActionStart()";\n', ""
            ),
        }
        for label, variant in variants.items():
            with self.subTest(variant=label):
                self.assertNotEqual(variant, source)
                verify_can_be_started_guard(variant)

    def test_no_other_addon_code_starts_an_action(self) -> None:
        # #249 K5: a start anywhere else in the addon could run before the
        # guard. MCP_ActionHold's override of PerformActionStart only passes
        # the bridge's call on to super.
        for path in sorted(addon_root().glob("scripts/**/*.c")):
            if path == BRIDGE_PATH:
                continue
            source = path.read_text(encoding="utf-8")
            starts = _START.findall(_code(clean(source)))
            with self.subTest(file=path.name):
                if path == HOLD_PATH:
                    override = _code(method_body(source, HOLD_OVERRIDE))
                    self.assertEqual(_START.findall(override), ["PerformActionStart"])
                    self.assertIn("super.PerformActionStart(", _bare(override))
                    self.assertEqual(len(starts), 2)  # its signature and that call
                else:
                    self.assertEqual(starts, [])


class ActionUseAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_app_tool_registered_and_forwards(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "started": True}),
        ) as call:
            await app.call_tool(
                COMMAND, {"action": "ActionOpenDoors", "timeout_s": 1.0}
            )

        call.assert_awaited_once()
        forwarded = call.await_args.args
        self.assertEqual(forwarded[0], COMMAND)
        self.assertEqual(forwarded[1]["action"], "ActionOpenDoors")
        self.assertNotIn("target", forwarded[1])
        self.assertEqual(forwarded[2], "client")


if __name__ == "__main__":
    unittest.main()
