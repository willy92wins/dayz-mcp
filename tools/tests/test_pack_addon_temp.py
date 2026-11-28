"""pack-addon.ps1 gives AddonBuilder a new empty temp folder on every build.

A shared dayz-pack-<mod> folder under %TEMP% survived from one build to the
next. AddonBuilder empties it only when -clear is passed, so a later pack of
a different tree could ship files left behind by an earlier one.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
PACK_PS1 = TOOLS_DIR / "pack-addon.ps1"
_CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
_TIMEOUT_S = 120
_MOD_NAME = "DayZ_MCP"
_TEMP_NAME = re.compile(r"^dayz-pack-DayZ_MCP-[0-9a-f]{32}$")
_LEFTOVER = "leftover-from-fake.txt"

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
        string temp = "";
        string prefix = _MOD_NAME_;
        foreach (string arg in args)
        {
            if (arg.StartsWith("-prefix=")) { prefix = arg.Substring(8); }
            if (arg.StartsWith("-temp=")) { temp = arg.Substring(6); }
        }
        string listLog = Environment.GetEnvironmentVariable("FAKE_AB_LIST");
        if (!String.IsNullOrEmpty(listLog))
        {
            var lines = new System.Collections.Generic.List<string>();
            lines.Add("TEMP=" + temp);
            if (!String.IsNullOrEmpty(temp) && Directory.Exists(temp))
            {
                string[] files = Directory.GetFiles(temp);
                if (files.Length == 0)
                {
                    lines.Add("EMPTY=1");
                }
                foreach (string file in files)
                {
                    lines.Add("FILE=" + Path.GetFileName(file));
                }
            }
            else
            {
                lines.Add("MISSING=1");
            }
            File.WriteAllLines(listLog, lines.ToArray());
        }
        if (!String.IsNullOrEmpty(temp))
        {
            Directory.CreateDirectory(temp);
            File.WriteAllText(Path.Combine(temp, _LEFTOVER_), "left by an earlier build");
        }
        string mode = Environment.GetEnvironmentVariable("FAKE_AB_MODE");
        string built = Path.Combine(temp, prefix + ".pbo");
        if (mode == "fail")
        {
            return 2;
        }
        File.WriteAllBytes(built, System.Text.Encoding.ASCII.GetBytes("fresh-build\n"));
        if (mode == "leave_temp")
        {
            // AddonBuilder packed but could not finish its own copy. Leave
            // the fresh PBO in -temp and do not touch the destination.
            return 0;
        }
        string dest = args.Length > 1 ? args[1] : "";
        Directory.CreateDirectory(dest);
        string destinationPbo = Path.Combine(dest, prefix + ".pbo");
        if (File.Exists(destinationPbo))
        {
            File.Delete(destinationPbo);
        }
        File.Move(built, destinationPbo);
        return 0;
    }
}
""".replace("_MOD_NAME_", '"' + _MOD_NAME + '"').replace("_LEFTOVER_", '"' + _LEFTOVER + '"')


def _compile_fake_builder(target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp, "FakeAddonBuilder.cs")
        source.write_text(_FAKE_SOURCE, encoding="utf-8")
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
class PackAddonTempFolderTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("powershell.exe") is None:
            self.skipTest("powershell.exe not on PATH")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tools_root = root / "tools"
        builder = self.tools_root / "Bin" / "AddonBuilder" / "AddonBuilder.exe"
        builder.parent.mkdir(parents=True)
        _compile_fake_builder(builder)
        self.source = root / "src"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "$PBOPREFIX$").write_text("DayZ_MCP\n", encoding="utf-8")
        (self.source / "config.cpp").write_text("class CfgPatches {};\n", encoding="utf-8")
        (self.source / "scripts" / "bridge.c").write_text("// bridge\n", encoding="utf-8")
        self.destination = root / "dest"
        self.destination.mkdir()
        self.log = root / "argv.log"
        self.list_log = root / "temp-list.log"
        self.ps_temp = root / "ps-temp"
        self.ps_temp.mkdir()
        if Path("P:\\").exists():
            self._mapped_p = False
            self.p_root = Path("P:/") / ("ff-486-" + uuid.uuid4().hex)
            self.p_root.mkdir()
            self.addCleanup(self._remove_owned_p_root)
            self.stage_root = self.p_root / "stage"
        else:
            self._mapped_p = True
            self.p_root = root / "pdrive"
            self.p_root.mkdir()
            subprocess.run(["subst", "P:", str(self.p_root)], check=True, capture_output=True)
            self.addCleanup(self._unmap_p)
            self.stage_root = Path(r"P:\stage")

    def _unmap_p(self) -> None:
        subprocess.run(["subst", "P:", "/d"], capture_output=True)

    def _remove_owned_p_root(self) -> None:
        shutil.rmtree(self.p_root, ignore_errors=True)

    def _run_pack(
        self, mode: str, *, clear: bool = False, stage_only: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FAKE_AB_LOG"] = str(self.log)
        env["FAKE_AB_LIST"] = str(self.list_log)
        env["FAKE_AB_MODE"] = mode
        env["TEMP"] = str(self.ps_temp)
        env["TMP"] = str(self.ps_temp)
        env.pop("DAYZ_TOOLS_PATH", None)
        cmd = [
            "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(PACK_PS1),
            "-ModName", _MOD_NAME,
            "-Source", str(self.source),
            "-StageRoot", str(self.stage_root),
            "-Destination", str(self.destination),
            "-ToolsPath", str(self.tools_root),
        ]
        if clear:
            cmd.append("-Clear")
        if stage_only:
            cmd.append("-StageOnly")
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_TIMEOUT_S, env=env,
        )

    def _temp_from_logs(self) -> str:
        argv = self.log.read_text(encoding="utf-8").splitlines()
        temps = [line[6:] for line in argv if line.startswith("-temp=")]
        self.assertEqual(len(temps), 1, argv)
        listed = self.list_log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(listed[0].startswith("TEMP="), listed)
        self.assertEqual(listed[0][5:], temps[0])
        return temps[0]

    def test_two_builds_get_distinct_empty_temp_folders(self) -> None:
        seen: list[str] = []
        for _ in range(2):
            completed = self._run_pack("ok")
            self.assertEqual(
                completed.returncode, 0,
                f"pack failed:\n{completed.stdout}\n{completed.stderr}",
            )
            temp = self._temp_from_logs()
            listing = self.list_log.read_text(encoding="utf-8").splitlines()
            self.assertIn("EMPTY=1", listing)
            self.assertNotIn(f"FILE={_LEFTOVER}", listing)
            self.assertTrue(
                Path(temp).name and _TEMP_NAME.match(Path(temp).name),
                temp,
            )
            self.assertTrue(
                str(Path(temp)).lower().startswith(str(self.ps_temp).lower()),
                temp,
            )
            self.assertFalse(Path(temp).exists(), temp)
            self.assertTrue((self.destination / f"{_MOD_NAME}.pbo").is_file())
            seen.append(temp)
        self.assertNotEqual(seen[0], seen[1])

    def test_failed_builder_still_removes_temp_folder(self) -> None:
        completed = self._run_pack("fail")
        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, output)
        self.assertIn("AddonBuilder failed with exit code", output)
        temp = self._temp_from_logs()
        self.assertFalse(Path(temp).exists(), temp)

    def test_stage_only_does_not_create_the_temp_folder(self) -> None:
        completed = self._run_pack("ok", stage_only=True)
        self.assertEqual(
            completed.returncode, 0,
            f"pack failed:\n{completed.stdout}\n{completed.stderr}",
        )
        self.assertEqual(list(self.ps_temp.iterdir()), [])

    def test_clear_still_passes_clear(self) -> None:
        completed = self._run_pack("ok", clear=True)
        self.assertEqual(
            completed.returncode, 0,
            f"pack failed:\n{completed.stdout}\n{completed.stderr}",
        )
        argv = self.log.read_text(encoding="utf-8").splitlines()
        self.assertIn("-clear", argv)
        temp = self._temp_from_logs()
        self.assertFalse(Path(temp).exists(), temp)


    def test_failed_delivery_keeps_the_built_pbo(self) -> None:
        stale = b"stale-destination\n"
        fresh = b"fresh-build\n"
        dest_pbo = self.destination / f"{_MOD_NAME}.pbo"
        dest_pbo.write_bytes(stale)
        lock = _lock_exclusive(dest_pbo)
        self.addCleanup(lock.close)
        completed = self._run_pack("leave_temp")
        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, output)
        self.assertIn("The PBO was BUILT but never reached the game", output)
        # PowerShell may wrap the path in the ErrorRecord; join until "destination:".
        section = re.search(r"built\s*:\s*(.*?)\s*destination\s*:", output, re.S)
        self.assertIsNotNone(section, output)
        kept = Path(re.sub(r"\s+", "", section.group(1)))
        self.assertTrue(kept.is_file(), f"error path missing: {kept}\n{output}")
        self.assertEqual(hashlib.sha256(kept.read_bytes()).digest(), hashlib.sha256(fresh).digest())
        lock.close()
        self.assertEqual(dest_pbo.read_bytes(), stale)
        temp = self._temp_from_logs()
        self.assertFalse(Path(temp).exists() or (Path(temp) / f"{_MOD_NAME}.pbo").is_file(), temp)
        self.addCleanup(kept.unlink, missing_ok=True)


def _lock_exclusive(path: Path):
    """Hold an exclusive share on *path* so Copy-Item cannot overwrite it."""

    import ctypes

    generic_read = 0x80000000
    open_existing = 3
    invalid = ctypes.c_void_p(-1).value
    handle = ctypes.windll.kernel32.CreateFileW(
        str(path), generic_read, 0, None, open_existing, 0, None
    )
    if handle == invalid or handle is None:
        raise OSError(f"CreateFileW failed for {path}")

    class _Locked:
        def __init__(self, h: int) -> None:
            self._h = h

        def close(self) -> None:
            if self._h:
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._h))
                self._h = 0

    return _Locked(handle)


class PackAddonTempFolderContractTest(unittest.TestCase):
    def test_temp_folder_is_unique_and_removed_after_stage_only(self) -> None:
        text = PACK_PS1.read_text(encoding="utf-8")
        self.assertIn(
            '$temp = Join-Path $env:TEMP ("dayz-pack-$ModName-" + [guid]::NewGuid().ToString(\'N\'))',
            text,
        )
        self.assertIn('$built = Join-Path $temp "$ModName.pbo"', text)
        self.assertIn("Remove-Item -LiteralPath $temp -Recurse -Force", text)
        self.assertIn("if ($Clear) { $args += \"-clear\" }", text)
        self.assertIn('("dayz-pack-$ModName-undelivered-" + [guid]::NewGuid().ToString(\'N\') + ".pbo")', text)
        self.assertIn("Write-Warning -WarningAction Continue", text)
        self.assertIn("$cleanupTemp = $true", text)
        self.assertLess(
            text.index("New-Item -ItemType Directory -Path $temp"),
            text.index("$cleanupTemp = $true"),
        )
        self.assertLess(
            text.index("$cleanupTemp = $true"),
            text.index("if ($cleanupTemp)"),
        )
        self.assertLess(
            text.index("if ($cleanupTemp)"),
            text.index("Remove-Item -LiteralPath $temp -Recurse -Force"),
        )
        self.assertLess(text.index("if ($StageOnly)"), text.index("dayz-pack-$ModName-"))
        self.assertLess(text.index("if ($StageOnly)"), text.index("New-Item -ItemType Directory -Path $temp"))
        self.assertIn("2026-11-19", text)
        self.assertIn("2026-11-27", text)
        self.assertIn("pbo_provenance.py", text)


if __name__ == "__main__":
    unittest.main()
