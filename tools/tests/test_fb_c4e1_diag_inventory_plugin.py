"""fb-20260927-213700-c4e1: DayZDiag registers vanilla's PluginInventoryDebug.

DayZDiag defines DIAG_DEVELOPER but IsDebug() is false. PluginManager.Init
registers PluginInventoryDebug through RegisterPluginDebug, whose
reg_on_release=false makes RegisterPlugin return before the insert unless
IsDebug(), so the plugin never exists there, while DayZPlayerInventory reads it
without a null check under DIAG_DEVELOPER. addon/scripts/4_World/
MCP_DiagPlugins.c registers it. These tests pin the source shape the game will
compile and the vanilla declarations it relies on. They do not launch DayZ.
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import _method_body


DIAG = addon_root() / "scripts" / "4_World" / "MCP_DiagPlugins.c"
README_MCP = Path(__file__).resolve().parents[1] / "README-mcp.md"
VANILLA = Path(os.environ.get("DAYZ_MCP_VANILLA_SCRIPTS", "P:/scripts"))

GUARD = "#ifdef DIAG_DEVELOPER"
NOT_INTERNAL = "if (!g_Game.IsDebug())"
REGISTER = 'RegisterPlugin("PluginInventoryDebug", true, true, true);'
FOUND = "if (GetPluginByType(PluginInventoryDebug))"
VERDICT_1 = "[DayZ_MCP] diag PluginInventoryDebug registered=1"
VERDICT_0 = "[DayZ_MCP] diag PluginInventoryDebug registered=0"
MODDED_MANAGER_RE = re.compile(r"^\s*modded\s+class\s+PluginManager\b", re.MULTILINE)


def _code(source: str) -> str:
    """The source without its whole-line // comments."""
    return "\n".join(
        line for line in source.split("\n") if not line.lstrip().startswith("//")
    )


class DiagInventoryPluginContractTest(unittest.TestCase):
    def _source(self) -> str:
        self.assertTrue(DIAG.is_file(), f"{DIAG} is missing")
        return DIAG.read_text(encoding="utf-8")

    def test_file_exists_in_the_world_module(self) -> None:
        # config.cpp compiles every file under DayZ_MCP/scripts/4_World, the
        # module vanilla's PluginManager is declared in.
        self.assertTrue(DIAG.is_file(), f"{DIAG} is missing")

    def test_everything_is_inside_one_diag_developer_guard(self) -> None:
        # PluginInventoryDebug is declared only under DIAG_DEVELOPER
        # (plugininventorydebug.c:4-55): a retail compile must see none of this.
        lines = self._source().split("\n")
        filled = [line for line in lines if line.strip()]
        self.assertEqual(lines[0], GUARD)
        self.assertEqual(filled[-1], "#endif")
        directives = [line.strip() for line in lines if line.lstrip().startswith("#")]
        self.assertEqual(directives, [GUARD, "#endif"])
        # No other addon file names the DIAG-only type.
        for path in sorted(addon_root().rglob("*.c")):
            if path == DIAG:
                continue
            with self.subTest(path=path.name):
                self.assertNotIn("PluginInventoryDebug", path.read_text(encoding="utf-8"))

    def test_exactly_one_modded_plugin_manager_in_the_addon(self) -> None:
        found = []
        for path in sorted(addon_root().rglob("*.c")):
            code = _code(path.read_text(encoding="utf-8"))
            found.extend(path.name for _ in MODDED_MANAGER_RE.finditer(code))
        self.assertEqual(found, [DIAG.name])

    def test_init_calls_super_then_registers_outside_internal_builds(self) -> None:
        code = _code(self._source())
        self.assertEqual(code.count("override void Init()"), 1)
        init = _method_body(code, "override void Init()")
        self.assertIn("super.Init();", init)
        self.assertIn(NOT_INTERNAL, init)
        self.assertLess(init.index("super.Init();"), init.index(NOT_INTERNAL))
        # The call is the whole body of the guard and the only registration in
        # the file: an internal build (IsDebug) already has vanilla's.
        self.assertEqual(_method_body(init, NOT_INTERNAL).strip(), REGISTER)
        self.assertEqual(code.count("RegisterPlugin"), 1)

    def test_plugins_init_calls_super_then_prints_one_verdict(self) -> None:
        code = _code(self._source())
        self.assertEqual(code.count("override void PluginsInit()"), 1)
        body = _method_body(code, "override void PluginsInit()")
        self.assertIn("super.PluginsInit();", body)
        self.assertIn(FOUND, body)
        self.assertLess(body.index("super.PluginsInit();"), body.index(FOUND))
        # Found: registered=1, then return. Not found: registered=0, last.
        # One Print per call either way.
        found = [line.strip() for line in _method_body(body, FOUND).strip().split("\n")]
        self.assertEqual(found, [f'Print("{VERDICT_1}");', "return;"])
        self.assertTrue(body.rstrip().endswith(f'Print("{VERDICT_0}");'))
        self.assertEqual(body.count("Print("), 2)
        self.assertNotIn("RegisterPlugin", body)

    def test_readme_names_the_line_the_addon_prints(self) -> None:
        code = _code(self._source())
        for verdict in (VERDICT_1, VERDICT_0):
            with self.subTest(verdict=verdict):
                self.assertIn(f'Print("{verdict}");', code)
        readme = README_MCP.read_text(encoding="utf-8")
        self.assertIn(f"`{VERDICT_1}`", readme)
        self.assertIn("(`registered=0` when it is still missing)", readme)


class DiagInventoryPluginVanillaTest(unittest.TestCase):
    """The vanilla declarations MCP_DiagPlugins.c relies on.

    The offline linter does not compile, so a wrong override signature would
    only show in game. Skipped without the vanilla scripts tree
    (DAYZ_MCP_VANILLA_SCRIPTS, default P:/scripts).
    """

    def test_overrides_and_registration_match_vanilla(self) -> None:
        manager_path = VANILLA / "4_world" / "plugins" / "pluginmanager.c"
        plugin_path = VANILLA / "4_world" / "plugins" / "pluginbase" / "plugininventorydebug.c"
        if not manager_path.is_file() or not plugin_path.is_file():
            self.skipTest(f"vanilla scripts tree not present: {VANILLA}")
        self.assertTrue(DIAG.is_file(), f"{DIAG} is missing")
        ours = _code(DIAG.read_text(encoding="utf-8"))
        manager = manager_path.read_text(encoding="utf-8", errors="replace")
        plugin = plugin_path.read_text(encoding="utf-8", errors="replace")
        # Public, no parameters (pluginmanager.c:39, :109).
        for name in ("Init", "PluginsInit"):
            with self.subTest(name=name):
                self.assertIn(f"override void {name}()", ours)
                self.assertRegex(manager, rf"(?m)^\tvoid {name}\(\)\s*$")
        # The fourth parameter is the switch the call sets to true
        # (pluginmanager.c:192). RegisterPluginDebug passes false (:240-243),
        # and false returns before the insert unless IsDebug() (:213-218).
        self.assertRegex(
            manager,
            r"protected void RegisterPlugin\(\s*string plugin_class_name,\s*bool reg_on_client,"
            r"\s*bool reg_on_server,\s*bool reg_on_release = true\s*\)",
        )
        self.assertRegex(
            manager,
            r"protected void RegisterPluginDebug\([^)]*\)\s*\{\s*"
            r"RegisterPlugin\(\s*plugin_class_name,\s*reg_on_client,\s*reg_on_server,\s*false\s*\);",
        )
        self.assertRegex(
            manager,
            r"if\s*\(\s*!reg_on_release\s*\)\s*\{\s*if\s*\(\s*!g_Game\.IsDebug\(\)\s*\)\s*\{\s*return;",
        )
        self.assertRegex(
            manager,
            r"#ifdef DIAG_DEVELOPER\s*RegisterPluginDebug\(\s*\"PluginInventoryDebug\"\s*,"
            r"\s*true\s*,\s*true\s*\);\s*#endif",
        )
        # The plugin exists only under the same guard (plugininventorydebug.c:4-55),
        # and its defaults are the retail branch (:17-22).
        self.assertLess(
            plugin.index(GUARD), plugin.index("class PluginInventoryDebug extends PluginBase")
        )
        self.assertTrue(plugin.rstrip().endswith("#endif"))
        defaults = _method_body(plugin, "void PluginInventoryDebug()")
        self.assertIn("m_IsOnlyLocalMoveEnable = false;", defaults)
        self.assertIn("m_IsDesyncRepairEnable = true;", defaults)


if __name__ == "__main__":
    unittest.main()
