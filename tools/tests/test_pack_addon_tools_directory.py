"""pack-addon.ps1 must always pass -toolsDirectory to AddonBuilder.

On a clean machine reached over SSH (no Steam running in the session),
AddonBuilder without -toolsDirectory logs "[ERROR]: DayZ Tools path is
unknown, Steam must be running or specify -toolsDirectory." and exits 0
without writing the PBO. The pack script must derive the DayZ Tools root
(the folder holding Bin\\AddonBuilder\\AddonBuilder.exe) from whatever
AddonBuilder.exe it resolved -- -ToolsPath, DAYZ_TOOLS_PATH or the default
Steam path -- pass it on every invocation, and name that cause (with what
to do) instead of the generic "reported success but ... is missing".

A fake AddonBuilder.exe (a tiny compiled program that logs its argv)
proves the argument actually reaches the builder.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
PACK_PS1 = TOOLS_DIR / "pack-addon.ps1"
_CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
_TIMEOUT_S = 120
_MOD_NAME = "DayZ_MCP"

_FAKE_SOURCE = r"""
using System;
using System.IO;

public static class FakeAddonBuilder
{
    public static int Main(string[] args)
    {
        string log = Environment.GetEnvironmentVariable("FAKE_AB_LOG");
        if (!String.IsNullOrEmpty(log))
        {
            File.WriteAllLines(log, args);
        }
        string mode = Environment.GetEnvironmentVariable("FAKE_AB_MODE");
        if (mode == "unknown")
        {
            Console.WriteLine(
                "[ERROR]: DayZ Tools path is unknown, Steam must be running " +
                "or specify -toolsDirectory.");
            return 0;
        }
        string dest = args.Length > 1 ? args[1] : "";
        string prefix = _MOD_NAME_;
        string temp = "";
        foreach (string arg in args)
        {
            if (arg.StartsWith("-prefix=")) { prefix = arg.Substring(8); }
            if (arg.StartsWith("-temp=")) { temp = arg.Substring(6); }
        }
        byte[] pbo = System.Text.Encoding.ASCII.GetBytes("fake pbo\n");
        Directory.CreateDirectory(dest);
        File.WriteAllBytes(Path.Combine(dest, prefix + ".pbo"), pbo);
        if (!String.IsNullOrEmpty(temp))
        {
            Directory.CreateDirectory(temp);
            File.WriteAllBytes(Path.Combine(temp, prefix + ".pbo"), pbo);
        }
        return 0;
    }
}
""".replace("_MOD_NAME_", '"' + _MOD_NAME + '"')


def _compile_fake_builder(target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp, "FakeAddonBuilder.cs")
        source.write_text(_FAKE_SOURCE, encoding="utf-8")
        # csc writes scratch files under %TEMP%; point it at the scratch dir
        # so the compile also works where the inherited TEMP is not writable.
        env = os.environ.copy()
        env["TEMP"] = tmp
        env["TMP"] = tmp
        completed = subprocess.run(
            [str(_CSC), "/nologo", "/target:exe", f"/out:{target}", str(source)],
            capture_output=True, text=True, timeout=_TIMEOUT_S, env=env,
        )
        if completed.returncode != 0 or not target.is_file():
            raise AssertionError(
                f"csc failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
            )


@unittest.skipUnless(sys.platform == "win32", "pack-addon.ps1 runs under Windows PowerShell")
@unittest.skipUnless(_CSC.is_file(), "csc.exe ( .NET Framework) not available to build the fake")
class PackAddonToolsDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("powershell.exe") is None:
            self.skipTest("powershell.exe not on PATH")
        if Path("P:\\").exists():
            self.skipTest("P: is already mapped; refusing to touch it")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        # Fake DayZ Tools tree: <tools>\Bin\AddonBuilder\AddonBuilder.exe
        self.tools_root = root / "tools"
        builder = self.tools_root / "Bin" / "AddonBuilder" / "AddonBuilder.exe"
        builder.parent.mkdir(parents=True)
        _compile_fake_builder(builder)
        # Minimal addon source (scripts-only, so the script adds -packonly).
        self.source = root / "src"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "$PBOPREFIX$").write_text("DayZ_MCP\n", encoding="utf-8")
        (self.source / "config.cpp").write_text("class CfgPatches {};\n", encoding="utf-8")
        (self.source / "scripts" / "bridge.c").write_text("// bridge\n", encoding="utf-8")
        self.destination = root / "dest"
        self.log = root / "argv.log"
        # pack-addon.ps1 requires the stage under P:\; map it to a scratch dir.
        self.p_root = root / "pdrive"
        self.p_root.mkdir()
        self.stage_root = self.p_root / "stage"
        subprocess.run(["subst", "P:", str(self.p_root)], check=True, capture_output=True)
        self.addCleanup(self._unmap_p)

    def _unmap_p(self) -> None:
        subprocess.run(["subst", "P:", "/d"], capture_output=True)

    def _run_pack(self, mode: str, *, use_env: bool) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FAKE_AB_LOG"] = str(self.log)
        env["FAKE_AB_MODE"] = mode
        # The script derives its -temp folder from %TEMP%; keep it in scratch.
        scratch_temp = Path(self._tmp.name) / "ps-temp"
        scratch_temp.mkdir(exist_ok=True)
        env["TEMP"] = str(scratch_temp)
        env["TMP"] = str(scratch_temp)
        cmd = [
            "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(PACK_PS1),
            "-ModName", _MOD_NAME,
            "-Source", str(self.source),
            "-StageRoot", "P:\\stage",
            "-Destination", str(self.destination),
        ]
        if use_env:
            env["DAYZ_TOOLS_PATH"] = str(self.tools_root)
        else:
            env.pop("DAYZ_TOOLS_PATH", None)
            cmd += ["-ToolsPath", str(self.tools_root)]
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_TIMEOUT_S, env=env,
        )

    def test_tools_directory_arg_reaches_addonbuilder(self) -> None:
        for use_env in (False, True):
            with self.subTest(via_env=use_env):
                if self.log.exists():
                    self.log.unlink()
                completed = self._run_pack("ok", use_env=use_env)
                self.assertEqual(
                    completed.returncode, 0,
                    f"pack failed:\n{completed.stdout}\n{completed.stderr}",
                )
                argv = self.log.read_text(encoding="utf-8").splitlines()
                self.assertIn(f"-toolsDirectory={self.tools_root}", argv)

    def test_unknown_tools_path_is_named_not_generic_missing(self) -> None:
        completed = self._run_pack("unknown", use_env=False)
        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, output)
        self.assertIn("DayZ Tools path is unknown", output)
        self.assertIn("-toolsDirectory", output)
        self.assertNotIn("reported success but", output)


class PackAddonToolsDirectoryContractTest(unittest.TestCase):
    def test_ps1_derives_tools_root_and_always_passes_it(self) -> None:
        text = PACK_PS1.read_text(encoding="utf-8")
        self.assertIn("-toolsDirectory=", text)
        self.assertLess(
            text.index("-toolsDirectory="),
            text.index("& $builder @args"),
            "-toolsDirectory must be added before AddonBuilder is invoked",
        )
        self.assertIn("DayZ Tools path is unknown", text)


if __name__ == "__main__":
    unittest.main()
