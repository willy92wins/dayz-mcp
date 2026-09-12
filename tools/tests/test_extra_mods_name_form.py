"""fb-20260909-213257-49a9: extra_mods name-form error and docs contract.

Isolated from the Windows-only import graph so the error text can be checked
on any host. Validation itself is unchanged; this locks the caller-facing
wording.
"""

from __future__ import annotations

import ctypes
import sys
import types
import unittest
from pathlib import Path

if not hasattr(ctypes, "WinDLL"):
    class _FakeDll:
        def __getattr__(self, name: str):
            fn = lambda *_a, **_k: 0
            fn.argtypes = None
            fn.restype = None
            return fn

    ctypes.WinDLL = lambda *_a, **_k: _FakeDll()  # type: ignore[attr-defined]

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp import dayz_test_request, dayz_test_tool


def _policy() -> dayz_test_request.RequestProjectPolicy:
    return dayz_test_request.RequestProjectPolicy(
        mod="ExampleMod",
        dev_root=r"P:\ExampleMod_Suite",
        default_source=r"P:\ExampleMod",
        default_base_mods=("@CF", "@Dabs Framework"),
        mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
        mod_roots=(r"P:\Mods",),
    )


def _sealed() -> tuple[object, ...]:
    return (types.SimpleNamespace(policy=_policy()),)


class ExtraModsNameFormErrorTest(unittest.TestCase):
    def test_relative_path_is_bad_mod_and_names_the_form(self) -> None:
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(),
                project="ExampleMod",
                mode="offline",
                extra_mods=[r"mods\@DayZ_MCP"],
            )
        code = caught.exception.code
        self.assertEqual(code, dayz_test_tool._BAD_MOD)
        self.assertTrue(code.startswith("bad_mod:"))
        self.assertIn("single folder name", code)
        self.assertIn("@DayZ_MCP", code)
        self.assertIn("absolute path inside the project's mod_roots", code)

    def test_missing_bridge_keeps_prefix_and_requires_explicit_folder(self) -> None:
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(),
                project="ExampleMod",
                mode="offline",
            )
        code = caught.exception.code
        self.assertEqual(code, dayz_test_tool._BRIDGE_MOD_MISSING)
        self.assertTrue(code.startswith("bridge_mod_missing:"))
        self.assertIn("extra_mods=['@DayZ_MCP']", code)
        self.assertIn("folder name", code)
        self.assertIn("base_mods and server_mods do not count", code)

    def test_folder_name_is_still_accepted(self) -> None:
        raw, selected = dayz_test_tool.build_run_request(
            _sealed(),
            project="ExampleMod",
            mode="offline",
            extra_mods=["@DayZ_MCP"],
        )
        parsed = dayz_test_request.parse_dayz_test_request(
            raw, policies=(_policy(),)
        )
        self.assertEqual(selected.mod, "ExampleMod")
        self.assertEqual(parsed.payload["extra_mods"], ["@DayZ_MCP"])


class ExtraModsNameFormDocsTest(unittest.TestCase):
    def test_server_prose_names_the_form_and_drops_any_folder(self) -> None:
        text = (_TOOLS / "dayz_mcp" / "server.py").read_text(encoding="utf-8")
        self.assertIn("EXTRA_MODS_DESCRIPTION", text)
        self.assertIn("single folder name", text)
        self.assertIn("@DayZ_MCP", text)
        self.assertIn("bridge_mod_missing", text)
        self.assertNotIn("extra_mods accepts any folder", text)


if __name__ == "__main__":
    unittest.main()
