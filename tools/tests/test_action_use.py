from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import command_requires_lease


COMMAND = "action_use"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
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


def _squash(text: str) -> str:
    return " ".join(text.split())


def _mutate(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise AssertionError(f"mutant anchor must be unique: {old!r}")
    return source.replace(old, new, 1)


# The 1.30 guard, whitespace-squashed and without its comment. Its words and
# their order let three mutants live (#249 K1): the guard only in the
# action_use_door branch, the negative with ok true, and `return true` outside
# the `if`, which also stops every permitted start.
CAN_BE_STARTED_GUARD = """
if (!action.CanBeStarted())
{
    result.ok = false;
    result.error = "condition_failed";
    return true;
}
"""


def verify_can_be_started_guard(body: str) -> None:
    if body.count("action.CanBeStarted()") != 1:
        raise AssertionError("one CanBeStarted() check")
    guarded = body.split("#ifndef DAYZ_1_29\n")
    if len(guarded) != 2:
        raise AssertionError("one 1.29 guard: 1.29 has no CanBeStarted()")
    before, block = guarded[0], guarded[1].split("#endif\n")[0]
    code = "\n".join(
        line for line in block.splitlines() if not line.strip().startswith("//")
    )
    if _squash(code) != _squash(CAN_BE_STARTED_GUARD):
        raise AssertionError(f"not the negative of Can(): {block!r}")
    # At method level: the five commands share DispatchActionUse.
    if before.count("{") != before.count("}"):
        raise AssertionError("the guard is inside a branch")
    if "if (!action.Can(player, actionTarget, heldItem))" not in before:
        raise AssertionError("CanBeStarted() is asked before Can()")
    if "amc.PerformActionStart(" in before + block:
        raise AssertionError("an action starts before CanBeStarted() is asked")


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
        body = _method_body(source, "protected bool DispatchActionUse(")
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
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.body = _method_body(source, "protected bool DispatchActionUse(")

    def test_can_be_started_is_asked_after_can_and_before_any_start(self) -> None:
        verify_can_be_started_guard(self.body)

    def test_can_be_started_mutants_are_red(self) -> None:
        body = self.body
        mutants = {
            "door_only": _mutate(
                _mutate(
                    body,
                    "#ifndef DAYZ_1_29\n",
                    '\t\tif (command.cmd == "action_use_door")\n\t\t{\n#ifndef DAYZ_1_29\n',
                ),
                "#endif\n",
                "#endif\n\t\t}\n",
            ),
            "ok_true": _mutate(
                body,
                "if (!action.CanBeStarted())\n\t\t{\n\t\t\tresult.ok = false;\n",
                "if (!action.CanBeStarted())\n\t\t{\n\t\t\tresult.ok = true;\n",
            ),
            "return_outside_if": _mutate(
                body,
                "\t\t\treturn true;\n\t\t}\n#endif\n",
                "\t\t}\n\t\treturn true;\n#endif\n",
            ),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                with self.assertRaises(AssertionError):
                    verify_can_be_started_guard(mutant)


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
