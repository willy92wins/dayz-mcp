"""ui_click mode="complete" (backlog 20be, product-spec B4).

complete synthesizes in script the three handler calls a real click delivers:
OnMouseButtonDown, OnMouseButtonUp and OnClick, in that order, each at the
target's measured screen centre and each through the walk direct mode uses.
Every phase runs whatever the one before returned. bubble decides whether a
phase a handler declined moves on up the chain. The reply adds click_sequence,
a reference member no other verb fills.

These tests pin the Enforce source the game compiles and re-run each verifier
over mutants that must go red, the reply classes and their prune contract, and
the Python tool that forwards mode and bubble and publishes the contract. They
do not execute Enforce or launch DayZ: which handler a live panel exposes, and
what it does with the three phases, is checked in game after promotion.
"""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune
from dayz_mcp.server import LEASE_TOOL_LINE, ServerConfig, ToolError, build_app
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean, method_body
from tests.mcp_helpers import _content_json


MISSION = addon_root() / "scripts" / "5_Mission"
CLIENT_BRIDGE = MISSION / "MCPClientBridge.c"
SERVER_BRIDGE = MISSION / "MCPBridge.c"
MESSAGES = MISSION / "MCPMessages.c"

COMMAND = "ui_click"
FIELD = "click_sequence"

DISPATCH = "protected bool DispatchUiClick(MCPCommand command, MCPResult result)"
COMPLETE = (
    "protected bool DispatchUiClickComplete(MCPCommand command, MCPResult result, "
    "Widget target, int mouseButton)"
)
PHASE = (
    "protected MCPUiClickPhase RunUiClickPhase(Widget target, string phaseName, "
    "string method, int mouseButton, MCPUiClickSequence sequence)"
)
DIRECT = "protected bool InvokeUiClick(Widget target, int mouseButton, out string handlerName)"
WALK = (
    "protected void InvokeUiHandler(Widget target, string method, int x, int y, "
    "int mouseButton, bool bubble, MCPUiClickPhase walk)"
)
DELIVER = (
    "protected bool DeliverUiEvent(Class inst, string method, Widget target, int x, "
    "int y, int mouseButton, out bool consumed)"
)
DELIVER_MENU = (
    "protected bool DeliverUiMenuEvent(UIScriptedMenu menu, string method, Widget target, "
    "int x, int y, int mouseButton, out bool consumed)"
)
NOTE = "protected void NoteUiHandler(MCPUiClickPhase walk, string handlerName, bool consumed)"
NEW_METHODS = (COMPLETE, PHASE, DIRECT, WALK, DELIVER, DELIVER_MENU, NOTE)

PHASE_CALLS = (
    'MCPUiClickPhase down = RunUiClickPhase(target, "down", "OnMouseButtonDown", mouseButton, sequence);',
    'MCPUiClickPhase up = RunUiClickPhase(target, "up", "OnMouseButtonUp", mouseButton, sequence);',
    'MCPUiClickPhase click = RunUiClickPhase(target, "click", "OnClick", mouseButton, sequence);',
)
BUBBLE_GATE = "if (consumed || !bubble) { return; }"

# click_sequence as a complete click on the ui_dialog confirm's BtnYes would
# fill it (bools travel as 0/1), and the request echo beside it.
SEQUENCE = {
    "x": 961,
    "y": 541,
    "bubble": 0,
    "phases": [
        {"phase": "down", "handler": "MCPDialogController", "consumed": 0, "received": 1},
        {"phase": "up", "handler": "MCPDialogController", "consumed": 0, "received": 1},
        {"phase": "click", "handler": "MCPDialogController", "consumed": 1, "received": 1},
    ],
}
ECHO = {
    "requested_path": "BtnYes",
    "requested_root": "",
    "requested_text": "",
    "matched_path": "/Workspace@0/MCPDialogRoot@0/MCPDialogPanel@0/BtnYes@0",
}


def _compact(text: str) -> str:
    return " ".join(clean(text).split())


def _body(source: str, signature: str) -> str:
    return _compact(method_body(source, signature))


def _ordered(haystack: str, *needles: str) -> None:
    """Each needle must occur after the previous one (sequential search)."""
    last = -1
    for needle in needles:
        index = haystack.find(needle, last + 1)
        if index < 0:
            raise AssertionError(f"missing or out of order: {needle!r}")
        last = index


def _mutate(source: str, old: str, new: str, count: int = 1) -> str:
    if source.count(old) != count:
        raise AssertionError(
            f"mutant anchor must occur {count} time(s), found {source.count(old)}: {old!r}"
        )
    return source.replace(old, new)


# --- Verifiers over the Enforce source ----------------------------------------


def verify_complete_dispatch(source: str) -> None:
    """DispatchUiClick hands complete over; DispatchUiClickComplete measures the
    centre, runs the three phases in order and derives ok and the codes from all
    three."""
    if "mode_not_implemented" in source:
        raise AssertionError("complete is implemented: no mode_not_implemented may remain")
    _ordered(
        _body(source, DISPATCH),
        "FillUiMatchedPath(target, result);",
        'if (mode == "complete") { return DispatchUiClickComplete(command, result, target, mouseButton); }',
        'if (mode != "direct")',
    )
    body = _body(source, COMPLETE)
    _ordered(
        body,
        "target.GetScreenPos(screenX, screenY);",
        "target.GetScreenSize(screenW, screenH);",
        "int centerX = Math.Round(screenX + screenW / 2.0);",
        "int centerY = Math.Round(screenY + screenH / 2.0);",
        "MCPUiClickSequence sequence = new MCPUiClickSequence();",
        "sequence.x = centerX;",
        "sequence.y = centerY;",
        "sequence.bubble = command.args.bubble;",
        "result.click_sequence = sequence;",
        "result.user_id = target.GetUserID();",
        *PHASE_CALLS,
        "result.handler = click.handler;",
        "result.clicked = click.consumed;",
        "if (down.consumed || up.consumed || click.consumed) { result.ok = true; return true; }",
        "result.ok = false;",
        "if (down.received == 0 && up.received == 0 && click.received == 0) "
        '{ result.error = "no_handler"; } else { result.error = "not_handled"; } return true;',
    )
    if body.count("RunUiClickPhase(") != 3:
        raise AssertionError("complete runs exactly three phases")
    between = body[body.index(PHASE_CALLS[0]) : body.index(PHASE_CALLS[2])]
    if "return" in between or "if (" in between:
        raise AssertionError("every phase runs, whatever the phase before it returned")
    if "GetPos(" in body or "GetSize(" in body:
        raise AssertionError("the centre is measured in screen pixels, not local coordinates")
    if body.count("command.args") != 1:
        raise AssertionError("complete reads bubble once, into the reported sequence")


def verify_phase_runner(source: str) -> None:
    expected = (
        "MCPUiClickPhase phase = new MCPUiClickPhase(); phase.phase = phaseName; "
        "sequence.phases.Insert(phase); "
        "InvokeUiHandler(target, method, sequence.x, sequence.y, mouseButton, sequence.bubble, phase); "
        "return phase;"
    )
    if _body(source, PHASE) != expected:
        raise AssertionError(
            "a phase is one fresh walk at the reported centre and bubble, booked in run order"
        )


def verify_direct_unchanged(source: str) -> None:
    expected = (
        "MCPUiClickPhase click = new MCPUiClickPhase(); "
        'InvokeUiHandler(target, "OnClick", 0, 0, mouseButton, false, click); '
        "handlerName = click.handler; return click.consumed;"
    )
    if _body(source, DIRECT) != expected:
        raise AssertionError("direct mode walks OnClick at (0, 0) without bubbling")
    tail = (
        'string handlerName = ""; bool didClick = InvokeUiClick(target, mouseButton, handlerName); '
        "result.user_id = target.GetUserID(); result.handler = handlerName; "
        "result.clicked = didClick; if (!didClick) { result.ok = false; "
        'if (handlerName == "") { result.error = "no_handler"; } '
        'else { result.error = "not_handled"; } return true; } result.ok = true; return true;'
    )
    if not _body(source, DISPATCH).endswith(tail):
        raise AssertionError("the direct branch of DispatchUiClick changed")


def verify_walk(source: str) -> None:
    walk = _body(source, WALK)
    _ordered(
        walk,
        "if (!GetGame()) { return; }",
        "if (!target) { return; }",
        "bool consumed = false;",
        "Widget cursor = target;",
        "while (cursor) {",
        "Class scriptInst; cursor.GetScript(scriptInst); if (scriptInst) { "
        "string scriptName = scriptInst.ClassName(); "
        "if (DeliverUiEvent(scriptInst, method, target, x, y, mouseButton, consumed)) { "
        f"NoteUiHandler(walk, scriptName, consumed); {BUBBLE_GATE} }} }}",
        "if (!target) { return; }",
        "Class userInst; cursor.GetUserData(userInst); if (userInst) { "
        "string userName = userInst.ClassName(); "
        "if (DeliverUiEvent(userInst, method, target, x, y, mouseButton, consumed)) { "
        f"NoteUiHandler(walk, userName, consumed); {BUBBLE_GATE} }} }}",
        "if (!target) { return; } cursor = cursor.GetParent(); }",
        "UIManager ui = GetGame().GetUIManager(); if (ui) { "
        "UIScriptedMenu menu = ui.GetMenu(); if (menu) { string menuName = menu.ClassName(); "
        "if (DeliverUiMenuEvent(menu, method, target, x, y, mouseButton, consumed)) { "
        "NoteUiHandler(walk, menuName, consumed); } } }",
    )
    if walk.count(BUBBLE_GATE) != 2:
        raise AssertionError("both widget slots stop on a consume, and without bubble on any receiver")
    if walk.count("NoteUiHandler(") != 3:
        raise AssertionError("every receiver is booked: script, user data and menu")
    if walk.count("if (!target) { return; }") != 3:
        raise AssertionError("the walk ends when a handler has unlinked the target")


def _typed_calls(owner: str) -> str:
    """The three typed calls, each storing the handler's return, in compact form."""
    return " ".join(
        f'if (method == "{method}") {{ consumed = {owner}.{method}(target, x, y, mouseButton); '
        "return true; }"
        for method in ("OnMouseButtonDown", "OnMouseButtonUp", "OnClick")
    )


def verify_delivery(source: str) -> None:
    expected = (
        "consumed = false; "
        "ScriptedWidgetEventHandler handler = ScriptedWidgetEventHandler.Cast(inst); "
        f"if (handler) {{ {_typed_calls('handler')} return false; }} "
        "bool reflected = false; "
        "int called = g_Game.GameScript.CallFunctionParams(inst, method, reflected, "
        "new Param4<Widget, int, int, int>(target, x, y, mouseButton)); "
        "if (!called) { return false; } "
        "consumed = reflected; return true;"
    )
    if _body(source, DELIVER) != expected:
        raise AssertionError(
            "a ScriptedWidgetEventHandler gets the typed call, any other instance "
            "CallFunctionParams with the same method and (target, x, y, button)"
        )
    menu = f"consumed = false; {_typed_calls('menu')} return false;"
    if _body(source, DELIVER_MENU) != menu:
        raise AssertionError("the active menu gets the typed call of the same method")


def verify_note(source: str) -> None:
    expected = (
        "walk.received = walk.received + 1; "
        "if (walk.received == 1 || consumed) { walk.handler = handlerName; } "
        "if (consumed) { walk.consumed = true; }"
    )
    if _body(source, NOTE) != expected:
        raise AssertionError(
            "the first receiver names a phase until one consumes it; received counts them"
        )


_LOCAL = re.compile(
    r"(?:^|[;{}]\s*)(?:bool|int|float|string|Class|Widget|UIManager|UIScriptedMenu|"
    r"ScriptedWidgetEventHandler|MCPUiClickPhase|MCPUiClickSequence)\s+(\w+)\s*(?==|;)"
)


def verify_compile_hazards(source: str) -> None:
    """Enforce rejects a local declared twice in one method and has no ternary."""
    for signature in NEW_METHODS:
        body = _body(source, signature)
        names = _LOCAL.findall(body)
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise AssertionError(f"local declared twice in {signature}: {duplicated}")
        if re.search(r"\?[^;]*:", re.sub(r'"(?:\\.|[^"\\])*"', '""', body)):
            raise AssertionError(f"ternary in {signature}")


def verify_reply_classes(messages: str) -> None:
    phase = _class_members(messages, "MCPUiClickPhase")
    if phase != [("string", "phase"), ("string", "handler"), ("bool", "consumed"), ("int", "received")]:
        raise AssertionError(f"MCPUiClickPhase members: {phase}")
    sequence = _class_members(messages, "MCPUiClickSequence")
    if sequence != [
        ("int", "x"),
        ("int", "y"),
        ("bool", "bubble"),
        ("ref array<ref MCPUiClickPhase>", "phases"),
    ]:
        raise AssertionError(f"MCPUiClickSequence members: {sequence}")
    constructor = _compact(method_body(messages, "void MCPUiClickSequence()"))
    if constructor != "phases = new array<ref MCPUiClickPhase>();":
        raise AssertionError("the sequence owns an empty phase list from construction")
    if _class_members(messages, "MCPResult").count(("ref MCPUiClickSequence", FIELD)) != 1:
        raise AssertionError("MCPResult carries exactly one ref click_sequence")
    order = [
        messages.index("class MCPUiClickPhase"),
        messages.index("class MCPUiClickSequence"),
        messages.index("class MCPResult"),
    ]
    if order != sorted(order):
        raise AssertionError("the reply classes are declared before they are used")


_MEMBER = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    text = clean(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", text)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    brace = text.index("{", match.end())
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                body = text[brace + 1 : index]
                return [(m.group("type"), m.group("name")) for m in _MEMBER.finditer(body)]
    raise AssertionError(f"class {class_name} is unterminated")


# --- Tests --------------------------------------------------------------------


class CompleteEnforceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CLIENT_BRIDGE.read_text(encoding="utf-8")

    def test_complete_dispatch_measures_then_runs_all_three_phases(self) -> None:
        verify_complete_dispatch(self.source)

    def test_each_phase_is_a_fresh_walk_at_the_reported_centre(self) -> None:
        verify_phase_runner(self.source)

    def test_direct_mode_walks_onclick_at_origin_without_bubbling(self) -> None:
        verify_direct_unchanged(self.source)

    def test_walk_order_and_bubble_branches(self) -> None:
        verify_walk(self.source)

    def test_delivery_is_typed_then_reflective(self) -> None:
        verify_delivery(self.source)

    def test_consumer_names_the_phase(self) -> None:
        verify_note(self.source)

    def test_compile_hazards(self) -> None:
        verify_compile_hazards(self.source)
        # The helper can fail: a repeated local and a ternary are caught.
        doubled = _mutate(
            self.source, "\t\t\t\tstring userName = userInst.ClassName();\n",
            "\t\t\t\tstring scriptName = userInst.ClassName();\n",
        )
        with self.assertRaisesRegex(AssertionError, "declared twice"):
            verify_compile_hazards(doubled)
        ternary = _mutate(
            self.source,
            "\t\tint centerX = Math.Round(screenX + screenW / 2.0);\n",
            "\t\tint centerX = screenW > 0 ? Math.Round(screenX + screenW / 2.0) : 0;\n",
        )
        with self.assertRaisesRegex(AssertionError, "ternary"):
            verify_compile_hazards(ternary)

    def test_mutants_are_red_for_what_they_do(self) -> None:
        source = self.source
        down_call = f"\t\t{PHASE_CALLS[0]}\n"
        up_call = f"\t\t{PHASE_CALLS[1]}\n"
        gate = "\t\t\t\t\tif (consumed || !bubble)\n"
        delegation = "\t\t\treturn DispatchUiClickComplete(command, result, target, mouseButton);\n"
        direct_call = '\t\tInvokeUiHandler(target, "OnClick", 0, 0, mouseButton, false, click);\n'
        phase_call = (
            "\t\tInvokeUiHandler(target, method, sequence.x, sequence.y, mouseButton, "
            "sequence.bubble, phase);\n"
        )
        menu_tail = (
            "\t\t}\n\n\t\tUIManager ui = GetGame().GetUIManager();\n\t\tif (ui)\n\t\t{\n"
            "\t\t\tUIScriptedMenu menu = ui.GetMenu();\n\t\t\tif (menu)\n\t\t\t{\n"
            "\t\t\t\tstring menuName"
        )
        # label: (verifier, mutant, the reason the verifier must give)
        mutants = {
            "complete_refused_again": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    delegation,
                    '\t\t\tresult.ok = false;\n\t\t\tresult.error = "mode_not_implemented";\n'
                    "\t\t\treturn true;\n",
                ),
                "no mode_not_implemented may remain",
            ),
            "complete_degrades_to_direct": (
                verify_complete_dispatch,
                _mutate(source, delegation, '\t\t\tmode = "direct";\n'),
                r"missing or out of order: 'if \(mode == \"complete\"\) \{ return DispatchUiClickComplete",
            ),
            "up_before_down": (
                verify_complete_dispatch,
                _mutate(source, down_call + up_call, up_call + down_call),
                "missing or out of order: 'MCPUiClickPhase up = ",
            ),
            "later_phases_skipped_after_a_consume": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    down_call,
                    down_call + "\t\tif (down.consumed)\n\t\t{\n\t\t\tresult.ok = true;\n"
                    "\t\t\treturn true;\n\t\t}\n",
                ),
                "every phase runs",
            ),
            "centre_in_local_coordinates": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    "\t\ttarget.GetScreenPos(screenX, screenY);\n",
                    "\t\ttarget.GetPos(screenX, screenY);\n",
                ),
                r"missing or out of order: 'target\.GetScreenPos",
            ),
            "centre_is_the_corner": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    "\t\tint centerX = Math.Round(screenX + screenW / 2.0);\n",
                    "\t\tint centerX = Math.Round(screenX);\n",
                ),
                "missing or out of order: 'int centerX = ",
            ),
            "centre_truncated_not_rounded": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    "\t\tint centerY = Math.Round(screenY + screenH / 2.0);\n",
                    "\t\tint centerY = screenY + screenH / 2.0;\n",
                ),
                "missing or out of order: 'int centerY = ",
            ),
            "bubble_never_read": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    "\t\tsequence.bubble = command.args.bubble;\n",
                    "\t\tsequence.bubble = false;\n",
                ),
                r"missing or out of order: 'sequence\.bubble = command\.args\.bubble",
            ),
            "user_id_read_after_the_phases": (
                verify_complete_dispatch,
                _mutate(
                    _mutate(
                        source,
                        "\t\tresult.user_id = target.GetUserID();\n\n\t\tMCPUiClickPhase down",
                        "\n\t\tMCPUiClickPhase down",
                    ),
                    "\t\tresult.handler = click.handler;\n",
                    "\t\tresult.user_id = target.GetUserID();\n\t\tresult.handler = click.handler;\n",
                ),
                "missing or out of order: 'MCPUiClickPhase down = ",
            ),
            "handler_from_the_down_phase": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    "\t\tresult.handler = click.handler;\n",
                    "\t\tresult.handler = down.handler;\n",
                ),
                r"missing or out of order: 'result\.handler = click\.handler",
            ),
            "ok_from_the_click_phase_only": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    "\t\tif (down.consumed || up.consumed || click.consumed)\n",
                    "\t\tif (click.consumed)\n",
                ),
                r"missing or out of order: 'if \(down\.consumed \|\| up\.consumed",
            ),
            "no_handler_from_the_click_phase_only": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    "\t\tif (down.received == 0 && up.received == 0 && click.received == 0)\n",
                    "\t\tif (click.received == 0)\n",
                ),
                r"missing or out of order: 'if \(down\.received == 0",
            ),
            "codes_swapped": (
                verify_complete_dispatch,
                _mutate(
                    source,
                    '\t\t\tresult.error = "no_handler";\n\t\t}\n\t\telse\n\t\t{\n'
                    '\t\t\tresult.error = "not_handled";\n',
                    '\t\t\tresult.error = "not_handled";\n\t\t}\n\t\telse\n\t\t{\n'
                    '\t\t\tresult.error = "no_handler";\n',
                ),
                r"missing or out of order: 'if \(down\.received == 0",
            ),
            "reported_centre_is_not_the_one_used": (
                verify_phase_runner,
                _mutate(
                    source,
                    phase_call,
                    "\t\tInvokeUiHandler(target, method, 0, 0, mouseButton, sequence.bubble, phase);\n",
                ),
                "at the reported centre and bubble",
            ),
            "phase_booked_out_of_run_order": (
                verify_phase_runner,
                _mutate(
                    source,
                    "\t\tsequence.phases.Insert(phase);\n",
                    "\t\tsequence.phases.InsertAt(phase, 0);\n",
                ),
                "booked in run order",
            ),
            "direct_takes_a_centre": (
                verify_direct_unchanged,
                _mutate(
                    source,
                    direct_call,
                    '\t\tInvokeUiHandler(target, "OnClick", 1, 1, mouseButton, false, click);\n',
                ),
                r"direct mode walks OnClick at \(0, 0\) without bubbling",
            ),
            "direct_bubbles": (
                verify_direct_unchanged,
                _mutate(
                    source,
                    direct_call,
                    '\t\tInvokeUiHandler(target, "OnClick", 0, 0, mouseButton, true, click);\n',
                ),
                r"direct mode walks OnClick at \(0, 0\) without bubbling",
            ),
            "bubble_inverted": (
                verify_walk,
                _mutate(source, gate, "\t\t\t\t\tif (consumed || bubble)\n", count=2),
                "missing or out of order: 'Class scriptInst; ",
            ),
            "a_consume_does_not_end_the_phase": (
                verify_walk,
                _mutate(source, gate, "\t\t\t\t\tif (!bubble)\n", count=2),
                "missing or out of order: 'Class scriptInst; ",
            ),
            "bubble_ignored": (
                verify_walk,
                _mutate(source, gate, "\t\t\t\t\tif (consumed)\n", count=2),
                "missing or out of order: 'Class scriptInst; ",
            ),
            "user_data_before_script": (
                verify_walk,
                _mutate(
                    _mutate(
                        source,
                        "\t\t\tcursor.GetScript(scriptInst);\n",
                        "\t\t\tcursor.GetUserData(scriptInst);\n",
                    ),
                    "\t\t\tcursor.GetUserData(userInst);\n",
                    "\t\t\tcursor.GetScript(userInst);\n",
                ),
                r"missing or out of order: 'Class scriptInst; cursor\.GetScript\(scriptInst\)",
            ),
            "walk_stops_at_the_target": (
                verify_walk,
                _mutate(
                    source,
                    "\t\t\tcursor = cursor.GetParent();\n" + menu_tail,
                    "\t\t\tcursor = null;\n" + menu_tail,
                ),
                r"missing or out of order: 'if \(!target\) \{ return; \} cursor = cursor\.GetParent",
            ),
            "menu_never_asked": (
                verify_walk,
                _mutate(
                    source,
                    "\t\t\t\tif (DeliverUiMenuEvent(menu, method, target, x, y, mouseButton, consumed))\n",
                    "\t\t\t\tif (false)\n",
                ),
                "missing or out of order: 'UIManager ui = ",
            ),
            "walk_goes_on_after_the_target_is_gone": (
                verify_walk,
                _mutate(
                    source,
                    "\t\t\tif (!target)\n\t\t\t{\n\t\t\t\treturn;\n\t\t\t}\n\n\t\t\tClass userInst;\n",
                    "\n\t\t\tClass userInst;\n",
                ),
                "missing or out of order: 'Class userInst; ",
            ),
            "reflection_instead_of_the_typed_call": (
                verify_delivery,
                _mutate(
                    source,
                    "\t\tScriptedWidgetEventHandler handler = ScriptedWidgetEventHandler.Cast(inst);\n",
                    "\t\tScriptedWidgetEventHandler handler = null;\n",
                ),
                "a ScriptedWidgetEventHandler gets the typed call",
            ),
            "unknown_method_is_clicked": (
                verify_delivery,
                _mutate(
                    source,
                    '\t\t\tif (method == "OnClick")\n\t\t\t{\n'
                    "\t\t\t\tconsumed = handler.OnClick(target, x, y, mouseButton);\n"
                    "\t\t\t\treturn true;\n\t\t\t}\n\t\t\treturn false;\n",
                    "\t\t\tconsumed = handler.OnClick(target, x, y, mouseButton);\n\t\t\treturn true;\n",
                ),
                "a ScriptedWidgetEventHandler gets the typed call",
            ),
            "coordinates_swapped": (
                verify_delivery,
                _mutate(
                    source,
                    "consumed = handler.OnMouseButtonDown(target, x, y, mouseButton);",
                    "consumed = handler.OnMouseButtonDown(target, y, x, mouseButton);",
                ),
                "a ScriptedWidgetEventHandler gets the typed call",
            ),
            "reflection_always_calls_onclick": (
                verify_delivery,
                _mutate(
                    source,
                    "CallFunctionParams(inst, method, reflected,",
                    'CallFunctionParams(inst, "OnClick", reflected,',
                ),
                "a ScriptedWidgetEventHandler gets the typed call",
            ),
            "missing_method_counted_as_received": (
                verify_delivery,
                _mutate(
                    source,
                    "\t\tif (!called)\n\t\t{\n\t\t\treturn false;\n\t\t}\n\n\t\tconsumed = reflected;\n",
                    "\t\tconsumed = reflected;\n",
                ),
                "a ScriptedWidgetEventHandler gets the typed call",
            ),
            "menu_clicked_on_every_phase": (
                verify_delivery,
                _mutate(
                    source,
                    "consumed = menu.OnMouseButtonDown(target, x, y, mouseButton);",
                    "consumed = menu.OnClick(target, x, y, mouseButton);",
                ),
                "the active menu gets the typed call",
            ),
            "first_receiver_kept_over_the_consumer": (
                verify_note,
                _mutate(
                    source,
                    "\t\tif (walk.received == 1 || consumed)\n",
                    "\t\tif (walk.received == 1)\n",
                ),
                "the first receiver names a phase until one consumes it",
            ),
            "consume_never_booked": (
                verify_note,
                _mutate(source, "\t\tif (consumed)\n\t\t{\n\t\t\twalk.consumed = true;\n\t\t}\n", ""),
                "the first receiver names a phase until one consumes it",
            ),
        }
        for label, (verifier, mutant, reason) in mutants.items():
            with self.subTest(mutant=label):
                self.assertNotEqual(mutant, source)
                with self.assertRaisesRegex(AssertionError, reason):
                    verifier(mutant)


class CompleteReplyClassesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.messages = MESSAGES.read_text(encoding="utf-8")

    def test_reply_classes_and_the_result_member(self) -> None:
        verify_reply_classes(self.messages)

    def test_reply_class_mutants_are_red(self) -> None:
        messages = self.messages
        mutants = {
            "phases_not_owned": _mutate(
                messages,
                "\tref array<ref MCPUiClickPhase> phases;\n",
                "\tarray<ref MCPUiClickPhase> phases;\n",
            ),
            "phases_never_built": _mutate(
                messages, "\t\tphases = new array<ref MCPUiClickPhase>();\n", ""
            ),
            "received_is_a_flag": _mutate(messages, "\tint received;\n", "\tbool received;\n"),
            "result_member_missing": _mutate(
                messages, "\tref MCPUiClickSequence click_sequence;\n", ""
            ),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                with self.assertRaises(AssertionError):
                    verify_reply_classes(mutant)

    def test_only_the_complete_dispatch_fills_the_member_and_the_version_stays(self) -> None:
        client = clean(CLIENT_BRIDGE.read_text(encoding="utf-8"))
        server = clean(SERVER_BRIDGE.read_text(encoding="utf-8"))
        self.assertEqual(client.count(f".{FIELD} ="), 1)
        self.assertEqual(server.count(f".{FIELD}"), 0)
        self.assertIn(
            f"result.{FIELD} = sequence;",
            method_body(CLIENT_BRIDGE.read_text(encoding="utf-8"), COMPLETE),
        )
        # A reply member and a bridge-internal walk change neither the wire
        # version nor the arguments MCPArgs already carried (mode, bubble).
        self.assertIn('const string MCP_BRIDGE_VERSION = "10";', self.messages)


class CompletePruneTest(unittest.TestCase):
    def test_the_member_is_a_prunable_reference_owned_by_no_scalar_rule(self) -> None:
        self.assertIn(FIELD, result_prune.PRUNABLE_FIELDS)
        self.assertNotIn(FIELD, result_prune.OWNED_SCALAR_FIELDS)
        self.assertNotIn(FIELD, result_prune.NEVER_FILLED_SCALAR_FIELDS)
        self.assertEqual(
            [pair for pair in result_prune.SEMANTIC_EMPTY_FIELDS if pair[1] == FIELD], []
        )

    def test_every_other_verb_and_direct_mode_lose_the_unfilled_member(self) -> None:
        commands = loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS | loopback.EXEC_COMMANDS
        self.assertIn(COMMAND, commands)
        for command in sorted(commands):
            for unfilled in ({}, None):
                with self.subTest(command=command, unfilled=unfilled):
                    pruned = result_prune.prune_unfilled_fields(
                        command, {"ok": 1, FIELD: unfilled}
                    )
                    self.assertNotIn(FIELD, pruned)
                    self.assertEqual(pruned["ok"], 1)
        # A direct reply keeps its own answer as before.
        direct = {"ok": 1, "handler": "H", "clicked": 1, "user_id": 5, FIELD: {}}
        self.assertEqual(
            result_prune.prune_unfilled_fields(COMMAND, direct),
            {"ok": 1, "handler": "H", "clicked": 1, "user_id": 5},
        )

    def test_a_filled_sequence_travels_on_ui_click(self) -> None:
        pruned = result_prune.prune_unfilled_fields(
            COMMAND, {"ok": 1, FIELD: SEQUENCE, "dialog": {}, "ui": {}}
        )
        self.assertEqual(pruned[FIELD], SEQUENCE)
        self.assertNotIn("dialog", pruned)
        self.assertNotIn("ui", pruned)


class CompleteToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _description(self) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[COMMAND].description or ""

    async def test_description_states_the_sequence_and_what_it_is_not(self) -> None:
        description = await self._description()
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        self.assertNotIn("mode_not_implemented", description)
        for fragment in (
            "mode='direct' (default) calls OnClick at x=y=0 on the first handler found",
            "mode='complete' calls OnMouseButtonDown, OnMouseButtonUp and OnClick in "
            "that order at the widget's screen center (rounded)",
            "runs all three even when an earlier one was consumed",
            "with bubble=false each phase stops at the first handler found, with "
            "bubble=true a handler that declines passes the phase up the chain and "
            "to the menu (direct ignores bubble)",
            "click_sequence {x, y, bubble, phases: [{phase, handler, consumed, received}]}",
            "a phase's handler is the one that consumed it, else the first one that "
            "got it, and received counts the handlers that got it",
            "handler and clicked describe the click phase",
            "ok means some phase was consumed, no_handler that no phase found a "
            "handler, not_handled that handlers ran and none consumed",
            "a script-level synthesis of handler calls, not an engine mouse event",
            "no OS input, no cursor move, no window focus, no hit test",
            "engine-side widget state that only a real mouse drives (a button's "
            "pressed look) does not change",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)

    async def test_complete_and_bubble_reach_the_bridge_as_given(self) -> None:
        for bubble in (False, True):
            with self.subTest(bubble=bubble):
                call = AsyncMock(return_value={"ok": 1, FIELD: SEQUENCE})
                with patch.object(self.runtime, "call_bridge", new=call):
                    result = _content_json(
                        await self.app.call_tool(
                            COMMAND,
                            {"path": "BtnYes", "mode": "complete", "bubble": bubble, "timeout_s": 1.0},
                        )
                    )
                call.assert_awaited_once_with(
                    COMMAND,
                    {"path": "BtnYes", "button": 0, "mode": "complete", "bubble": bubble},
                    "client",
                    1.0,
                )
                self.assertEqual(result[FIELD], SEQUENCE)

    def _ready_runtime(self, bridge_answer: dict[str, object]):
        """The embedded runtime's own call_bridge, wait_for_result and prune,
        with only the loopback state and the readiness snapshot faked."""
        ready = {
            "server_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
            "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
        }
        enqueued: list[tuple[str, dict[str, object], object]] = []

        def enqueue(cmd: str, args: dict[str, object], peer: object = None, **_kwargs: object):
            enqueued.append((cmd, dict(args), peer))
            return 200, {"id": 7}

        state = SimpleNamespace(
            enqueue_command=enqueue,
            take_result=lambda *_args, **_kwargs: dict(bridge_answer),
        )
        patches = (
            patch.object(self.runtime, "status", return_value=ready),
            patch.object(self.runtime, "loopback", SimpleNamespace(state=state)),
        )
        return patches, enqueued

    async def test_a_complete_reply_arrives_with_the_sequence_and_without_empty_refs(self) -> None:
        answer = {
            "id": 7,
            "ok": 1,
            "handler": "MCPDialogController",
            "clicked": 1,
            "user_id": 0,
            "ui_request": dict(ECHO),
            FIELD: SEQUENCE,
            "ui": {},
            "dialog": {},
            "input_trigger": {},
        }
        (status_patch, loopback_patch), enqueued = self._ready_runtime(answer)
        with status_patch, loopback_patch:
            result = _content_json(
                await self.app.call_tool(
                    COMMAND, {"path": "BtnYes", "mode": "complete", "timeout_s": 1.0}
                )
            )
        self.assertEqual(
            enqueued,
            [(COMMAND, {"path": "BtnYes", "button": 0, "mode": "complete", "bubble": False}, "client")],
        )
        self.assertEqual(result[FIELD], SEQUENCE)
        self.assertEqual(result["handler"], "MCPDialogController")
        self.assertEqual(result["clicked"], 1)
        for empty in ("ui", "dialog", "input_trigger"):
            self.assertNotIn(empty, result)

    async def test_a_complete_refusal_keeps_the_click_phase_diagnostics(self) -> None:
        answer = {
            "id": 7,
            "ok": 0,
            "error": "not_handled",
            "handler": "MCPDialogController",
            "clicked": 0,
            "user_id": 0,
            "ui_request": dict(ECHO, requested_path="Title", requested_root="MCPDialogRoot"),
            FIELD: {
                "x": 960,
                "y": 300,
                "bubble": 0,
                "phases": [
                    {"phase": name, "handler": "MCPDialogController", "consumed": 0, "received": 1}
                    for name in ("down", "up", "click")
                ],
            },
        }
        (status_patch, loopback_patch), _enqueued = self._ready_runtime(answer)
        with status_patch, loopback_patch:
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(
                    COMMAND,
                    {"path": "Title", "root": "MCPDialogRoot", "mode": "complete", "timeout_s": 1.0},
                )
        self.assertIn(
            "not_handled; handler='MCPDialogController' user_id=0 clicked=0; "
            "requested_path='Title' requested_root='MCPDialogRoot'",
            str(raised.exception),
        )


if __name__ == "__main__":
    unittest.main()
