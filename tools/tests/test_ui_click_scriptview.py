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
    def setUp(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.body = _method_body(source, "protected bool InvokeUiClick(")
        self.compact = " ".join(self.body.split()).replace(
            "CallFunctionParams( ", "CallFunctionParams("
        )

    def test_sweh_fast_paths_remain_first(self) -> None:
        script_cast = "ScriptedWidgetEventHandler.Cast(scriptInst)"
        user_cast = "ScriptedWidgetEventHandler.Cast(userInst)"
        self.assertIn(script_cast, self.body)
        self.assertIn(user_cast, self.body)
        self.assertLess(self.body.index(script_cast), self.body.index(user_cast))

    def test_failed_casts_fall_back_to_reflective_onclick_with_bool_result(self) -> None:
        script_call = (
            'int scriptCalled = g_Game.GameScript.CallFunctionParams(scriptInst, "OnClick", '
            "scriptConsumed, new Param4<Widget, int, int, int>(target, 0, 0, mouseButton));"
        )
        user_call = (
            'int userCalled = g_Game.GameScript.CallFunctionParams(userInst, "OnClick", '
            "userConsumed, new Param4<Widget, int, int, int>(target, 0, 0, mouseButton));"
        )
        self.assertIn(script_call, self.compact)
        self.assertIn(user_call, self.compact)
        self.assertEqual(self.compact.count('CallFunctionParams('), 2)

        script_cast = self.compact.index("ScriptedWidgetEventHandler.Cast(scriptInst)")
        script_reflect = self.compact.index(script_call)
        user_data = self.compact.index("cursor.GetUserData(userInst)")
        user_cast = self.compact.index("ScriptedWidgetEventHandler.Cast(userInst)")
        user_reflect = self.compact.index(user_call)
        menu_fallback = self.compact.index("UIManager ui = GetGame().GetUIManager()")
        self.assertLess(script_cast, script_reflect)
        self.assertLess(script_reflect, user_data)
        self.assertLess(user_cast, user_reflect)
        self.assertLess(user_reflect, menu_fallback)

        for expected in (
            "handlerName = scriptInst.ClassName(); return scriptConsumed;",
            "handlerName = userInst.ClassName(); return userConsumed;",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.compact)

    def test_reader_has_no_dabs_compile_dependency_or_dead_define(self) -> None:
        self.assertNotIn("ScriptedViewBase", self.body)
        self.assertNotIn("DabsFramework", self.body)
        self.assertNotIn("Relay_Command", self.body)


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
