from __future__ import annotations

import unittest

from tests._addon_paths import addon_root


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"


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


class UiClickScriptViewSourceContractTest(unittest.TestCase):
    """The lookup behind InvokeUiClick is the walk it shares with
    mode="complete" (backlog 20be): InvokeUiHandler visits each widget's script
    and user-data slots, then the menu, and DeliverUiEvent makes the call. The
    pins follow the walk there; InvokeUiClick hands it OnClick at (0, 0)
    without bubbling, which keeps direct mode's first-handler return."""

    def setUp(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.direct = _method_body(source, "protected bool InvokeUiClick(")
        self.walk = _method_body(source, "protected void InvokeUiHandler(")
        self.deliver = _method_body(source, "protected bool DeliverUiEvent(")
        self.compact = " ".join(self.walk.split())
        self.deliver_compact = " ".join(self.deliver.split()).replace(
            "CallFunctionParams( ", "CallFunctionParams("
        )

    def test_direct_mode_hands_the_walk_onclick_at_origin_without_bubbling(self) -> None:
        self.assertEqual(
            " ".join(self.direct.split()),
            "MCPUiClickPhase click = new MCPUiClickPhase(); "
            'InvokeUiHandler(target, "OnClick", 0, 0, mouseButton, false, click); '
            "handlerName = click.handler; return click.consumed;",
        )

    def test_sweh_fast_paths_remain_first(self) -> None:
        # Script slot before user-data slot, then the menu; in each slot the
        # ScriptedWidgetEventHandler cast comes before the reflective call.
        order = [
            self.compact.index("cursor.GetScript(scriptInst);"),
            self.compact.index("DeliverUiEvent(scriptInst, method, target, x, y, mouseButton, consumed)"),
            self.compact.index("cursor.GetUserData(userInst);"),
            self.compact.index("DeliverUiEvent(userInst, method, target, x, y, mouseButton, consumed)"),
            self.compact.index("UIManager ui = GetGame().GetUIManager();"),
        ]
        self.assertEqual(order, sorted(order))
        cast = "ScriptedWidgetEventHandler handler = ScriptedWidgetEventHandler.Cast(inst);"
        self.assertIn(cast, self.deliver)
        self.assertLess(self.deliver.index(cast), self.deliver.index("CallFunctionParams("))

    def test_failed_casts_fall_back_to_reflective_onclick_with_bool_result(self) -> None:
        reflective = (
            "int called = g_Game.GameScript.CallFunctionParams(inst, method, "
            "reflected, new Param4<Widget, int, int, int>(target, x, y, mouseButton));"
        )
        self.assertIn(reflective, self.deliver_compact)
        self.assertEqual(self.deliver_compact.count("CallFunctionParams("), 1)
        # A missing method is an empty slot; a found one hands back its bool.
        self.assertIn(
            "if (!called) { return false; } consumed = reflected; return true;",
            self.deliver_compact,
        )
        # Each slot names its handler from its own instance and stops the
        # direct walk (no bubble) at the first one found, whatever it returned.
        for expected in (
            "string scriptName = scriptInst.ClassName(); "
            "if (DeliverUiEvent(scriptInst, method, target, x, y, mouseButton, consumed)) { "
            "NoteUiHandler(walk, scriptName, consumed); if (consumed || !bubble) { return; }",
            "string userName = userInst.ClassName(); "
            "if (DeliverUiEvent(userInst, method, target, x, y, mouseButton, consumed)) { "
            "NoteUiHandler(walk, userName, consumed); if (consumed || !bubble) { return; }",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.compact)

    def test_reader_has_no_dabs_compile_dependency_or_dead_define(self) -> None:
        for body in (self.direct, self.walk, self.deliver):
            self.assertNotIn("ScriptedViewBase", body)
            self.assertNotIn("DabsFramework", body)
            self.assertNotIn("Relay_Command", body)


class UiClickDirectModeContractTest(unittest.TestCase):
    """mode="direct" (and an omitted mode) keeps the legacy lookup and its return.

    What changed is the resolver in front of it: a ScriptView that is not the
    active menu is in scope because the named path is resolved over the whole
    workspace, and a homonym is refused (ambiguous_path) before InvokeUiClick
    instead of the first one being clicked. These are source gates only; the
    handler actually reached and the effect on LFPG TEST are measured live.
    """

    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.dispatch = _method_body(self.source, "protected bool DispatchUiClick(")
        self.resolver = _method_body(self.source, "protected Widget ResolveUiRoot(")

    def test_direct_is_the_normalised_default_and_invokes_the_legacy_lookup_once(self) -> None:
        compact = " ".join(self.dispatch.split())
        self.assertIn('string mode = command.args.mode; if (mode == "") { mode = "direct"; }', compact)
        self.assertEqual(self.dispatch.count("InvokeUiClick("), 1)
        self.assertIn(
            "bool didClick = InvokeUiClick(target, mouseButton, handlerName);", self.dispatch
        )
        self.assertIn("result.handler = handlerName;", self.dispatch)
        self.assertIn("result.clicked = didClick;", self.dispatch)

    def test_unique_resolution_and_mode_gate_precede_the_handler_lookup(self) -> None:
        order = [
            self.dispatch.index("ResolveUiRoot(command.args, error)"),
            self.dispatch.index("if (!target)"),
            self.dispatch.index("FillUiMatchedPath(target, result);"),
            self.dispatch.index('if (mode == "complete")'),
            self.dispatch.index('if (mode != "direct")'),
            self.dispatch.index("InvokeUiClick("),
        ]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("bubble", self.dispatch)

    def test_named_path_is_resolved_over_the_whole_workspace_before_the_menu_fallback(self) -> None:
        self.assertNotIn("FindAnyWidget(", self.source)
        self.assertNotIn("FindWidgetByNameWalk", self.source)
        self.assertLess(
            self.resolver.index("ResolveUniqueUiWidget(scope, pathName, pathError)"),
            self.resolver.index("ui.GetMenu()"),
        )
        unique = _method_body(self.source, "protected Widget ResolveUniqueUiWidget(")
        self.assertIn('error = "ambiguous_path";', unique)
        self.assertIn('error = "widget_not_found";', unique)
        self.assertLess(unique.index('"ambiguous_path"'), unique.index("return match.first;"))


if __name__ == "__main__":
    unittest.main()
