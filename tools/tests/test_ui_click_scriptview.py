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


if __name__ == "__main__":
    unittest.main()
