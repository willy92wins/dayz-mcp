from __future__ import annotations

import functools
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from tests._tiers import slow_test
from tests.enforce_subset_helpers import clean, method_body
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import command_requires_lease


COMMAND = "action_use"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
HOLD_PATH = addon_root() / "scripts" / "4_World" / "MCP_ActionHold.c"
DISPATCH = "protected bool DispatchActionUse(MCPCommand command, MCPResult result)"

# The 1.30 guard, compared without the whitespace outside its string literal
# once the comments are blank. Its words and their order let three mutants live
# (#249 K1), a brace count three more (K2) and spaces in the error code one
# (K6); test_can_be_started_mutants_are_red has them.
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
_STRING = re.compile(r'("(?:\\.|[^"\\])*")')
# The calls that start an action without asking CanBeStarted()
# (actionmanagerclient.c:792, 1055-1090 and 1172-1190; #249 K4).
_START = re.compile(
    r"\b(?:ActionStart|PerformActionStart|PerformActionFromQuickbar"
    r"|PerformActionFromInventory|OnInstantAction)\b"
)
# Calls by name or through a queue: this reading cannot follow them (#251 Q5).
_BY_NAME = re.compile(r"\b(?:CallFunction\w*|CallByName|CallLater\w*|Call|Invoke)\s*\(")
_METHOD = re.compile(
    r"\b(?P<name>[A-Za-z_]\w*)\s*\([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*\)\s*\{"
)
_NAME = re.compile(r"\b[A-Za-z_]\w*")


def _bare(text: str) -> str:
    parts = _STRING.split(text)
    return "".join(
        part if index % 2 else re.sub(r"\s+", "", part) for index, part in enumerate(parts)
    )


def _code(source: str) -> str:
    # Comments and the insides of string literals blank, at the same offsets: a
    # name inside a string literal is not a call (#249 K7).
    return _STRING.sub(lambda match: '"' + " " * (len(match.group()) - 2) + '"', clean(source))


def _methods(code: str) -> list[tuple[str, int, int, int]]:
    """(name, offset of the name, body start, body end) of each method in `code`."""
    pairs: dict[int, int] = {}
    opened: list[int] = []
    for brace in re.finditer(r"[{}]", code):
        if brace.group() == "{":
            opened.append(brace.start())
        elif opened:
            pairs[opened.pop()] = brace.start()
    return [
        (match.group("name"), match.start("name"), match.end(), pairs[match.end() - 1])
        for match in _METHOD.finditer(code)
        if match.group("name") not in ("if", "while", "foreach", "switch")
        and match.end() - 1 in pairs
    ]


def _dispatch(code: str) -> tuple[list[tuple[str, int, int, int]], int, int, int, re.Match[str]]:
    """The methods of `code`, and DispatchActionUse's name offset, body and guard."""
    methods = _methods(code)
    found = [method for method in methods if method[0] == "DispatchActionUse"]
    if len(found) != 1:
        raise AssertionError("one DispatchActionUse")
    _name, at, start, end = found[0]
    guards = list(_GUARD.finditer(code, start, end))
    if len(guards) != 1:
        raise AssertionError("one 1.29 guard: 1.29 has no CanBeStarted()")
    return methods, at, start, end, guards[0]


def verify_can_be_started_guard(source: str, others: tuple[str, ...] | None = None) -> None:
    cleaned, code = clean(source), _code(source)
    methods, _at, start, end, guard = _dispatch(code)
    if len(re.findall(r"\bCanBeStarted\b", code[start:end])) != 1:
        raise AssertionError("one CanBeStarted() check")
    block = cleaned[guard.start("block") : guard.end("block")]
    if _bare(block) != _bare(CAN_BE_STARTED_GUARD):
        raise AssertionError(f"not the negative of Can(): {block!r}")
    # A statement of the method itself, so the five commands all ask it: no
    # brace or #if open before it, and not the body of an if, else or loop
    # without braces. Comments and string literals are blank.
    before = code[start : guard.start()]
    if before.count("{") != before.count("}"):
        raise AssertionError("the guard is inside a branch")
    if len(re.findall(r"^[ \t]*#if", before, re.M)) != len(re.findall(r"^[ \t]*#endif\b", before, re.M)):
        raise AssertionError("the guard is inside another #if")
    last = re.sub(r"^[ \t]*#.*$", "", before, flags=re.M).rstrip()
    if last and last[-1] not in ";}":
        raise AssertionError("the guard is the body of a statement without braces")
    if _bare("if (!action.Can(player, actionTarget, heldItem))") not in _bare(before):
        raise AssertionError("CanBeStarted() is asked before Can()")
    _verify_starts(code, methods, start, guard.start(), guard.end(), end)
    _verify_no_call_by_name_first(
        code, methods, start, guard.start(), _other_sources() if others is None else others
    )


def _verify_starts(
    code: str,
    methods: list[tuple[str, int, int, int]],
    start: int,
    guard_start: int,
    guard_end: int,
    end: int,
) -> None:
    # Every start in the file runs after the guard: in DispatchActionUse, or in a
    # method that only code after the guard reaches (#249 K4, K5; #251 Q2).
    # Methods are told apart by name, so one name counts for every method that
    # has it.
    bodies: dict[str, list[tuple[int, int]]] = {}
    definitions = set()
    for name, at, body_start, body_end in methods:
        definitions.add(at)
        if body_start != start:
            bodies.setdefault(name, []).append((body_start, body_end))
    mentions: dict[str, set[str]] = {}

    def mentioned(name: str) -> set[str]:
        if name not in mentions:
            words = {word for a, b in bodies[name] for word in _NAME.findall(code, a, b)}
            mentions[name] = words & bodies.keys()
        return mentions[name]

    starting = {
        name for name, spans in bodies.items() if any(_START.search(code, a, b) for a, b in spans)
    }
    grown = bool(starting)
    while grown:
        more = {name for name in bodies.keys() - starting if mentioned(name) & starting}
        starting |= more
        grown = bool(more)
    allowed = [(guard_end, end)] + [span for name in starting for span in bodies[name]]
    pattern = _START.pattern
    if starting:
        pattern += r"|\b(?:" + "|".join(map(re.escape, sorted(starting))) + r")\b"
    for match in re.finditer(pattern, code):
        if match.start() not in definitions and not any(a <= match.start() < b for a, b in allowed):
            raise AssertionError("an action starts before CanBeStarted() is asked")
    reached = set(_NAME.findall(code, guard_end, end)) & starting
    frontier = list(reached)
    while frontier:
        for name in (mentioned(frontier.pop()) & starting) - reached:
            reached.add(name)
            frontier.append(name)
    if starting - reached:
        raise AssertionError(f"{sorted(starting - reached)} start an action that the guard does not cover")


@functools.lru_cache(maxsize=1)
def _other_sources() -> tuple[str, ...]:
    return tuple(
        path.read_text(encoding="utf-8")
        for path in sorted(addon_root().glob("scripts/**/*.c"))
        if path != BRIDGE_PATH
    )


@functools.lru_cache(maxsize=2)
def _other_methods(sources: tuple[str, ...]) -> dict[str, list[tuple[str, int, int]]]:
    found: dict[str, list[tuple[str, int, int]]] = {}
    for source in sources:
        code = _code(source)
        for name, _at, start, end in _methods(code):
            found.setdefault(name, []).append((code, start, end))
    return found


def _verify_no_call_by_name_first(
    code: str,
    methods: list[tuple[str, int, int, int]],
    start: int,
    guard_start: int,
    others: tuple[str, ...],
) -> None:
    # A call by name hides what it runs, so none runs before the guard: in
    # DispatchActionUse, or in a method of the addon that it names, however
    # deep (#251 Q5).
    bodies: dict[str, list[tuple[str, int, int]]] = {}
    for name, _at, body_start, body_end in methods:
        if body_start != start:
            bodies.setdefault(name, []).append((code, body_start, body_end))
    for name, spans in _other_methods(others).items():
        bodies.setdefault(name, []).extend(spans)
    seen: set[str] = set()
    spans = [(code, start, guard_start)]
    while spans:
        text, a, b = spans.pop()
        if _BY_NAME.search(text, a, b):
            raise AssertionError("a call by name runs before CanBeStarted() is asked")
        for name in (set(_NAME.findall(text, a, b)) & bodies.keys()) - seen:
            seen.add(name)
            spans.extend(bodies[name])


def _hold_override(code: str) -> tuple[int, int, int]:
    """Name offset, body start and body end of the override of PerformActionStart."""
    overrides = [
        method
        for method in _methods(code)
        if method[0] == "PerformActionStart"
        and re.search(r"\boverride\s+void\s+$", code[max(0, method[1] - 64) : method[1]])
    ]
    if len(overrides) != 1:
        raise AssertionError("one override of PerformActionStart")
    return overrides[0][1:]


def verify_no_start_outside_the_bridge(path: Path, source: str) -> None:
    # #249 K5: a start anywhere else in the addon could run before the guard.
    # MCP_ActionHold's override of PerformActionStart only passes the bridge's
    # call on to super; it is found by its name, whatever the spacing of its
    # parameters (#251 Q4).
    code = _code(source)
    starts = [match.start() for match in _START.finditer(code)]
    if path != HOLD_PATH:
        if starts:
            raise AssertionError(f"{path.name} starts an action")
        return
    at, start, end = _hold_override(code)
    inside = [offset for offset in starts if start <= offset < end]
    if [offset for offset in starts if offset != at and offset not in inside]:
        raise AssertionError("MCP_ActionHold starts an action outside its override")
    if (
        len(inside) != 1
        or not code.startswith("PerformActionStart", inside[0])
        or not re.search(r"\bsuper\s*\.\s*$", code[max(0, inside[0] - 32) : inside[0]])
    ):
        raise AssertionError("the override does more than call super.PerformActionStart")


def _around_guard(source: str, before: str, after: str) -> str:
    guard = _dispatch(_code(source))[4]
    return (
        source[: guard.start()] + before + source[guard.start() : guard.end()] + after
        + source[guard.end() :]
    )


def _in_guard(source: str, pattern: str, replacement: str) -> str:
    # Matched without the comments and spliced into the source with the text
    # each group has there, so a comment in the guard stays (#251 Q3). The
    # replacement is literal text in which \1 to \9 stand for the groups.
    start, end = _dispatch(_code(source))[4].span("block")
    matches = list(re.finditer(pattern, clean(source)[start:end]))
    if len(matches) != 1:
        raise AssertionError(f"pattern must match once in the guard: {pattern!r}")
    match = matches[0]

    def group(number: re.Match[str]) -> str:
        index = int(number.group(1))
        return source[start + match.start(index) : start + match.end(index)]

    return (
        source[: start + match.start()] + re.sub(r"\\(\d)", group, replacement)
        + source[start + match.end() :]
    )


def _before_dispatch(source: str, text: str) -> str:
    at = _dispatch(_code(source))[1]
    line = source.rfind("\n", 0, at) + 1
    return source[:line] + text + source[line:]


def _helper(name: str, body: str) -> str:
    return (
        f"protected void {name}(ActionManagerClient amc, ActionBase action, "
        f"ActionTarget target, ItemBase item)\n{{\n{body}\n}}\n\n"
    )


def _variants(source: str) -> dict[str, str]:
    # #249 K3, K7 and #251 Q2, Q3: layout, comments, braces or names in a
    # comment or a string, and a start moved into a helper called after the
    # guard are not the guard's business. The last start is found by position,
    # as one statement of the code after the guard.
    code = _code(source)
    end, guard = _dispatch(code)[3:]
    last = list(_START.finditer(code, guard.end(), end))[-1].start()
    first = re.compile(r"\s*").match(code, max(code.rfind(mark, 0, last) for mark in ";{}") + 1).end()
    after = code.index(";", last) + 1
    return {
        "as_is": source,
        "tabs_and_spaces_swapped": (
            source.replace("\t", "    ") if "\t" in source else source.replace("    ", "\t")
        ),
        "comment_in_the_if": _in_guard(source, r"(result\s*\.\s*ok\s*=)", "// why\n\\1"),
        "trailing_comment": _in_guard(source, r"(return\s+true\s*;)", r"\1 // done"),
        "block_comment": _in_guard(source, r"(return\s+true\s*;)", r"\1 /* done */"),
        "comment_line_before_the_brace": _in_guard(source, r"(return\s+true\s*;)", "\\1\n// done"),
        "braces_in_a_comment_and_a_string": _around_guard(
            source, '// } {\nstring unused = "}";\n', ""
        ),
        "names_in_a_string": _around_guard(
            source, 'string unused = "CanBeStarted() before PerformActionStart()";\n', ""
        ),
        "start_in_a_helper_after_the_guard": _before_dispatch(
            source[:first] + "StartChosenAction(amc, action, actionTarget, heldItem);" + source[after:],
            _helper("StartChosenAction", "amc.PerformActionStart(action, target, item, NULL);"),
        ),
    }


_DOOR = 'if (command.cmd == "action_use_door")\n'


def _door_first(source: str, call: str) -> str:
    return _around_guard(source, _DOOR + "{\n" + call + "\nreturn true;\n}\n", "")


def _mutants(source: str) -> dict[str, str]:
    return {
        # The guard for action_use_door alone leaves the other four commands
        # starting what 1.30 would not.
        "door_only": _around_guard(source, _DOOR + "{\n", "\n}"),
        "door_only_unbraced": _around_guard(source, _DOOR, ""),
        "door_only_comment_brace": _around_guard(source, _DOOR + "{ // }\n", "\n} // {"),
        "door_only_string_brace": _around_guard(source, _DOOR + '{ string unused = "}";\n', "\n}"),
        "inside_another_if": _around_guard(source, "#ifdef DAYZ_MCP_NEVER\n", "\n#endif"),
        "ok_true": _in_guard(source, r"result\s*\.\s*ok\s*=\s*false\s*;", "result.ok = true;"),
        "error_code_with_a_space": _in_guard(source, r'"condition_failed"', '"condition_ failed"'),
        # Also stops every allowed start.
        "return_outside_if": _in_guard(source, r"return\s+true\s*;(\s*)\}", r"}\1return true;"),
        # Starts that skip the guard for action_use_door (#249 K4, K5; #251 Q5).
        "door_quickbar": _door_first(source, "amc.PerformActionFromQuickbar(heldItem, null);"),
        "door_inventory": _door_first(source, "amc.PerformActionFromInventory(heldItem, null);"),
        "door_instant": _door_first(source, "amc.OnInstantAction(ActionOpenDoors);"),
        "door_helper": _door_first(
            _before_dispatch(
                source, _helper("StartDoor", "amc.PerformActionStart(action, target, item, NULL);")
            ),
            "StartDoor(amc, action, actionTarget, heldItem);",
        ),
        "door_by_name": _door_first(
            source,
            'g_Game.GameScript.CallFunctionParams(amc, "PerformActionStart", null, new '
            "Param4<ActionBase, ActionTarget, ItemBase, Param>(action, actionTarget, heldItem, NULL));",
        ),
        "door_helper_by_name": _door_first(
            _before_dispatch(
                source,
                _helper(
                    "StartDoorLater",
                    'GetGame().GetCallQueue(CALL_CATEGORY_GUI).CallByName(amc, "PerformActionStart", null);',
                ),
            ),
            "StartDoorLater(amc, action, actionTarget, heldItem);",
        ),
        # A start that nothing after the guard reaches.
        "start_elsewhere": _before_dispatch(
            source, _helper("StartAnyway", "amc.PerformActionStart(action, target, item, NULL);")
        ),
    }


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

    def test_harmless_edits_keep_the_guard_green(self) -> None:
        for label, variant in _variants(self.source).items():
            with self.subTest(variant=label):
                if label != "as_is":
                    self.assertNotEqual(variant, self.source)
                verify_can_be_started_guard(variant)

    @slow_test
    def test_can_be_started_mutants_are_red(self) -> None:
        # Built on each harmless edit too, so a layout the builders do not
        # expect fails here rather than hiding a mutant (#251 Q3).
        for label, variant in _variants(self.source).items():
            for name, mutant in _mutants(variant).items():
                with self.subTest(variant=label, mutant=name):
                    with self.assertRaises(AssertionError):
                        verify_can_be_started_guard(mutant)
        # A call by name in another addon file, which the code before the
        # guard reaches.
        later = (
            "class MCPDoorLater\n{\n\tstatic void StartDoorElsewhere(ActionManagerClient amc)\n\t{\n"
            '\t\tGetGame().GetCallQueue(CALL_CATEGORY_GUI).CallByName(amc, "PerformActionStart", null);\n'
            "\t}\n};\n"
        )
        mutant = _door_first(self.source, "MCPDoorLater.StartDoorElsewhere(amc);")
        verify_can_be_started_guard(self.source, _other_sources() + (later,))
        with self.subTest(mutant="door_helper_by_name_in_another_file"):
            with self.assertRaises(AssertionError):
                verify_can_be_started_guard(mutant, _other_sources() + (later,))

    def test_no_other_addon_code_starts_an_action(self) -> None:
        for path in sorted(addon_root().glob("scripts/**/*.c")):
            if path != BRIDGE_PATH:
                with self.subTest(file=path.name):
                    verify_no_start_outside_the_bridge(path, path.read_text(encoding="utf-8"))

    def test_the_hold_override_is_read_whatever_its_spacing(self) -> None:
        # #251 Q4. The variants and the mutants are built from positions in the
        # code, so the layout of the file itself cannot break them.
        source = HOLD_PATH.read_text(encoding="utf-8")
        code = _code(source)
        at, start, end = _hold_override(code)
        squeezed, spread = source, source
        for run in reversed(list(re.finditer(r"\s*([(),=])\s*", code[at:start]))):
            squeezed = squeezed[: at + run.start()] + run.group(1) + squeezed[at + run.end() :]
            mark = at + run.start(1)
            spread = spread[:mark] + "\n\t " + spread[mark:]
        self.assertNotEqual(spread, source)
        for label, variant in (("squeezed", squeezed), ("spread", spread)):
            with self.subTest(variant=label):
                verify_no_start_outside_the_bridge(HOLD_PATH, variant)
        call = _START.search(code, start, end)
        head = start + re.search(r"\bsuper\s*\.\s*$", code[start : call.start()]).start()
        mutants = {
            "not_super": source[: call.start()] + "PerformActionFromQuickbar" + source[call.end() :],
            "another_start": source[:head] + "OnInstantAction(ActionOpenDoors);\n\t\t" + source[head:],
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                with self.assertRaises(AssertionError):
                    verify_no_start_outside_the_bridge(HOLD_PATH, mutant)


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
