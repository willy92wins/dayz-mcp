from __future__ import annotations

import ntpath
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import dayz_tools_paths
from dayz_mcp.dayz_tools_paths import (
    DEFAULT_DAYZ_ROOT,
    DEFAULT_STEAM_ROOT,
    DEFAULT_TOOLS_ROOT,
    DIAG_NAME,
    STEAM_RELATIVE_FILES,
    TOOLS_ENV,
    TOOLS_RELATIVE_FILES,
    addon_builder_exe,
    dayz_diag_exe,
    external_file_paths,
    require_dayz_layout,
    selected_layout,
    tools_root_candidates,
)


# The 29 absolute paths build_native_launcher.py used to hardcode. Fallback must
# keep this exact list; only the root is allowed to move.
_FALLBACK_EXTERNAL = (
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe.config",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\log4net.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\NDesk.Options.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\SharedResources.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\SteamHelper.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\SteamLayerWrap.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\steam_api.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\Utils.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\en-US\SharedResources.resources.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\logger.xml",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\steam_appid.txt",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize\binarize.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize\steam_api64.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize\bin.txt",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize\bin\config.cpp",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\CfgConvert\CfgConvert.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\FileBank.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\NativeMethods.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\log4net.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\LibCommon.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\exclude.lst",
    r"C:\Program Files (x86)\Steam\steamclient.dll",
    r"C:\Program Files (x86)\Steam\Steam.dll",
    r"C:\Program Files (x86)\Steam\CSERHelper.dll",
    r"C:\Program Files (x86)\Steam\GameOverlayRenderer.dll",
    r"C:\Program Files (x86)\Steam\tier0_s.dll",
    r"C:\Program Files (x86)\Steam\vstdlib_s.dll",
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
)


def _markers(*paths: Path) -> set[str]:
    return {ntpath.normcase(str(path)) for path in paths}


def _is_file(present: set[str]) -> object:
    return lambda path: ntpath.normcase(str(path)) in present


class DayZToolsPathsTest(unittest.TestCase):
    def test_unset_env_keeps_the_historical_absolute_file_list(self) -> None:
        layout = selected_layout(environ={})
        self.assertEqual(layout.tools, DEFAULT_TOOLS_ROOT)
        self.assertEqual(layout.steam, DEFAULT_STEAM_ROOT)
        self.assertEqual(layout.dayz, DEFAULT_DAYZ_ROOT)
        self.assertEqual(
            tuple(str(path) for path in external_file_paths(layout)),
            _FALLBACK_EXTERNAL,
        )
        self.assertEqual(len(_FALLBACK_EXTERNAL), 29)
        self.assertEqual(len(TOOLS_RELATIVE_FILES), 22)
        self.assertEqual(len(STEAM_RELATIVE_FILES), 6)

    def test_env_relocates_tools_steam_dlls_and_diag_when_layout_is_standard(self) -> None:
        tools = Path(r"D:\SteamLibrary\steamapps\common\DayZ Tools")
        layout = selected_layout(environ={TOOLS_ENV: str(tools)})
        self.assertEqual(layout.tools, tools)
        self.assertEqual(layout.steam, Path(r"D:\SteamLibrary"))
        self.assertEqual(layout.dayz, Path(r"D:\SteamLibrary\steamapps\common\DayZ"))
        files = tuple(str(path) for path in external_file_paths(layout))
        self.assertEqual(
            files[0],
            str(tools / "Bin" / "AddonBuilder" / "AddonBuilder.exe"),
        )
        self.assertEqual(files[22], r"D:\SteamLibrary\steamclient.dll")
        self.assertEqual(
            files[-1],
            r"D:\SteamLibrary\steamapps\common\DayZ\DayZDiag_x64.exe",
        )
        self.assertEqual(len(files), 29)
        self.assertEqual(
            addon_builder_exe(environ={TOOLS_ENV: str(tools)}),
            files[0],
        )
        self.assertEqual(
            dayz_diag_exe(environ={TOOLS_ENV: str(tools)}),
            files[-1],
        )

    def test_blank_env_is_treated_as_unset(self) -> None:
        self.assertEqual(
            tools_root_candidates(environ={TOOLS_ENV: "   "}),
            [DEFAULT_TOOLS_ROOT],
        )
        self.assertEqual(selected_layout(environ={TOOLS_ENV: ""}).tools, DEFAULT_TOOLS_ROOT)

    def test_require_prefers_env_when_its_marker_exists(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "steamapps" / "common" / "DayZ Tools"
            addon = tools.joinpath("Bin", "AddonBuilder", "AddonBuilder.exe")
            steam_dll = root / "steamclient.dll"
            diag = root / "steamapps" / "common" / "DayZ" / DIAG_NAME
            present = _markers(addon, steam_dll, diag)
            layout = require_dayz_layout(
                environ={TOOLS_ENV: str(tools)},
                is_file=_is_file(present),
            )
            self.assertEqual(layout.tools, tools)
            self.assertEqual(layout.steam, root)
            self.assertEqual(layout.dayz, root / "steamapps" / "common" / "DayZ")

    def test_require_falls_through_to_default_when_env_marker_is_missing(self) -> None:
        env_tools = Path(r"E:\Missing\DayZ Tools")
        present = _markers(
            DEFAULT_TOOLS_ROOT.joinpath("Bin", "AddonBuilder", "AddonBuilder.exe"),
            DEFAULT_STEAM_ROOT / "steamclient.dll",
            DEFAULT_DAYZ_ROOT / DIAG_NAME,
        )
        layout = require_dayz_layout(
            environ={TOOLS_ENV: str(env_tools)},
            is_file=_is_file(present),
        )
        self.assertEqual(layout.tools, DEFAULT_TOOLS_ROOT)
        self.assertEqual(layout.steam, DEFAULT_STEAM_ROOT)
        self.assertEqual(layout.dayz, DEFAULT_DAYZ_ROOT)

    def test_require_names_every_tried_tools_root_when_none_exist(self) -> None:
        env_tools = Path(r"E:\Missing\DayZ Tools")
        with self.assertRaises(ValueError) as ctx:
            require_dayz_layout(
                environ={TOOLS_ENV: str(env_tools)},
                is_file=lambda _path: False,
            )
        message = str(ctx.exception)
        self.assertIn("dayz_tools_not_found", message)
        self.assertIn(str(env_tools), message)
        self.assertIn(str(DEFAULT_TOOLS_ROOT), message)
        self.assertIn(TOOLS_ENV, message)

    def test_require_names_tried_diag_paths_when_tools_exist_but_diag_does_not(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "steamapps" / "common" / "DayZ Tools"
            addon = tools.joinpath("Bin", "AddonBuilder", "AddonBuilder.exe")
            steam_dll = root / "steamclient.dll"
            present = _markers(addon, steam_dll)
            with self.assertRaises(ValueError) as ctx:
                require_dayz_layout(
                    environ={TOOLS_ENV: str(tools)},
                    is_file=_is_file(present),
                )
            message = str(ctx.exception)
            self.assertIn("dayz_diag_not_found", message)
            self.assertIn(str(root / "steamapps" / "common" / "DayZ" / DIAG_NAME), message)

    def test_addon_builder_exe_follows_selected_layout_without_filesystem(self) -> None:
        self.assertEqual(
            addon_builder_exe(environ={}),
            _FALLBACK_EXTERNAL[0],
        )


class RegistryStepTests(unittest.TestCase):
    """The registry sits between DAYZ_TOOLS_PATH and the hardcoded default.

    The point of the step is a Steam library on another drive, so these tests inject
    a reader instead of touching the real hive: a test that only passed on a machine
    where the keys happen to exist would prove nothing about the machine where they
    do not, which is the whole case being defended.
    """

    @staticmethod
    def _reader(answers):
        def read(hive, subkey, value):
            return answers.get((hive, subkey, value))
        return read

    def test_registry_is_tried_after_the_env_var_and_before_the_default(self) -> None:
        reader = self._reader({
            ("HKEY_CURRENT_USER", r"Software\Bohemia Interactive\DayZ Tools", "path"):
                r"D:\SteamLibrary\steamapps\common\DayZ Tools",
        })
        found = dayz_tools_paths.tools_root_candidates(
            environ={"DAYZ_TOOLS_PATH": r"E:\explicit"}, registry=reader
        )
        self.assertEqual(found[0], Path(r"E:\explicit"))
        self.assertEqual(found[1], Path(r"D:\SteamLibrary\steamapps\common\DayZ Tools"))
        self.assertEqual(found[-1], dayz_tools_paths.DEFAULT_TOOLS_ROOT)

    def test_a_silent_registry_leaves_the_chain_exactly_as_it_was(self) -> None:
        """No winreg, no key, wrong type -- all the same thing: contribute nothing."""
        self.assertEqual(
            dayz_tools_paths.tools_root_candidates(environ={}, registry=self._reader({})),
            [dayz_tools_paths.DEFAULT_TOOLS_ROOT],
        )

    def test_a_steam_root_is_completed_into_the_tools_subpath(self) -> None:
        """SteamPath names Steam, not the Tools, and comes back with forward slashes."""
        reader = self._reader({
            ("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath"): "d:/steamlibrary",
        })
        found = dayz_tools_paths.tools_root_candidates(environ={}, registry=reader)
        self.assertEqual(
            found[0], Path("d:/steamlibrary") / "steamapps" / "common" / "DayZ Tools"
        )

    def test_the_bohemia_key_outranks_the_steam_keys(self) -> None:
        """It names the Tools directly, so it stays right when the library moves."""
        reader = self._reader({
            ("HKEY_CURRENT_USER", r"Software\Bohemia Interactive\DayZ Tools", "path"):
                r"F:\Tools",
            ("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath"): r"C:\Steam",
        })
        found = dayz_tools_paths.tools_root_candidates(environ={}, registry=reader)
        self.assertEqual(found[0], Path(r"F:\Tools"))

    def test_read_registry_string_swallows_every_bad_input(self) -> None:
        read = dayz_tools_paths.read_registry_string
        self.assertIsNone(read("HKEY_NOT_A_HIVE", "Software", "x"))
        self.assertIsNone(read("HKEY_CURRENT_USER", r"Software\Nope\Nope\Nope", "x"))
        self.assertIsNone(read("HKEY_CURRENT_USER", r"Software\Valve\Steam", "NoSuchValue"))


if __name__ == "__main__":
    unittest.main()
