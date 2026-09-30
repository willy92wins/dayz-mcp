"""tools/dev/swap_pbo.ps1 swaps a live PBO only when both hashes are the expected ones.

fb-20260819-024951-e307: deploying a PBO prepared from an older tree would have
silently reverted another session's work. The swap refuses unless the build is
exactly -WantNew and the live file is still exactly -WantOld, refuses while a DayZ
game process runs, and verifies the live file after the copy.

Every run here targets a temporary <Destination>\\DayZ_MCP.pbo, never the game's,
under the PSModulePath a Git Bash parent leaves behind. While a DayZ game runs on
this machine the swap must refuse, so the success case asserts that refusal then.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import psutil

from dayz_mcp import orphan_guard
from tests._tiers import slow_test

TOOLS_DIR = Path(__file__).resolve().parents[1]
DEV_DIR = TOOLS_DIR / "dev"
SWAP_PS1 = DEV_DIR / "swap_pbo.ps1"
_POISONED_PSMODULEPATH = "/git/bash/poisoned/Modules"
_TIMEOUT_S = 90


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _dayz_games_running() -> list[str]:
    running = []
    for process in psutil.process_iter(["name"]):
        name = process.info.get("name") or ""
        if orphan_guard.is_dayz_image_name(name):
            running.append(name)
    return running


@unittest.skipUnless(sys.platform == "win32", "swap_pbo.ps1 runs under Windows PowerShell")
class SwapPboTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addons = self.root / "Addons"
        self.addons.mkdir()
        self.live = self.addons / "DayZ_MCP.pbo"
        self.live.write_bytes(b"live build of commit A\n")
        self.build = self.root / "build" / "DayZ_MCP.pbo"
        self.build.parent.mkdir()
        self.build.write_bytes(b"new build of commit B\n")
        self.backup = self.addons / "DayZ_MCP.pbo.bak_pre_swap"
        self.backup.write_bytes(self.live.read_bytes())
        self.old = _sha(self.live)
        self.new = _sha(self.build)
        self.base = ("-Build", str(self.build), "-WantNew", self.new, "-WantOld", self.old)

    def swap(self, *args: str) -> tuple[int, str]:
        env = os.environ.copy()
        env["PSModulePath"] = _POISONED_PSMODULEPATH
        done = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SWAP_PS1),
                "-Destination",
                str(self.addons),
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_TIMEOUT_S,
            env=env,
        )
        # PowerShell wraps long error lines; compare on single spaces.
        return done.returncode, " ".join((done.stdout + done.stderr).split())

    def assert_refused(self, fragment: str, *args: str) -> str:
        code, text = self.swap(*args)
        self.assertNotEqual(code, 0, text)
        self.assertIn(fragment, text)
        self.assertNotIn("PBO SWAPPED", text)
        self.assertEqual(_sha(self.live), self.old, "a refused swap must leave the live PBO alone")
        return text

    @slow_test
    def test_swaps_when_both_hashes_match(self) -> None:
        running = _dayz_games_running()
        code, text = self.swap(
            "-Build", str(self.build), "-WantNew", self.new.lower(), "-WantOld", self.old,
            "-Backup", str(self.backup), "-WantBackup", self.old,
        )
        if running:
            # A game on this machine may hold its PBO mapped: the swap must refuse.
            self.assertNotEqual(code, 0, text)
            self.assertIn("DayZ is running", text)
            self.assertEqual(_sha(self.live), self.old)
            return
        self.assertEqual(code, 0, text)
        self.assertEqual(self.live.read_bytes(), self.build.read_bytes())
        self.assertIn("live before: {0}".format(self.old), text)
        self.assertIn("live after : {0}".format(self.new), text)
        self.assertTrue(text.endswith("PBO SWAPPED"), text)
        self.assertEqual(_sha(self.backup), self.old, "the rollback copy must stay untouched")

    @slow_test
    def test_refuses_when_the_live_pbo_changed_since_the_caller_looked(self) -> None:
        # Another session swapped in its own build: -WantOld no longer matches.
        text = self.assert_refused(
            "The live PBO is not the expected one",
            "-Build", str(self.build), "-WantNew", self.new, "-WantOld", "A" * 64,
        )
        self.assertIn(self.old, text)

    @slow_test
    def test_refuses_a_build_that_is_not_want_new(self) -> None:
        self.assert_refused(
            "The build is not the expected one",
            "-Build", str(self.build), "-WantNew", self.old, "-WantOld", self.old,
        )

    @slow_test
    def test_refuses_a_rollback_copy_that_is_not_intact(self) -> None:
        self.backup.write_bytes(b"truncated")
        self.assert_refused(
            "The backup is not intact",
            *self.base, "-Backup", str(self.backup), "-WantBackup", self.old,
        )

    @slow_test
    def test_refuses_half_of_the_backup_pair(self) -> None:
        for extra in (("-Backup", str(self.backup)), ("-WantBackup", self.old)):
            with self.subTest(given=extra[0]):
                self.assert_refused("-Backup and -WantBackup go together", *self.base, *extra)

    @slow_test
    def test_refuses_a_backup_that_is_the_live_pbo(self) -> None:
        self.assert_refused(
            "The backup is the live PBO itself",
            *self.base, "-Backup", str(self.live), "-WantBackup", self.old,
        )

    @slow_test
    def test_refuses_a_hash_that_is_not_a_full_sha256(self) -> None:
        self.assert_refused(
            "must be a full SHA-256",
            "-Build", str(self.build), "-WantNew", self.new[:16], "-WantOld", self.old,
        )

    @slow_test
    def test_refuses_when_there_is_no_live_pbo(self) -> None:
        self.live.unlink()

        code, text = self.swap(*self.base)

        self.assertNotEqual(code, 0, text)
        self.assertIn("No live PBO to swap", text)
        self.assertFalse(self.live.exists())


class SwapPboContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SWAP_PS1.read_text(encoding="utf-8")

    def test_every_check_runs_before_the_copy_and_the_dayz_check_runs_last(self) -> None:
        copy = self.text.index("Copy-Item -LiteralPath $Build -Destination $live -Force")
        dayz = self.text.index("Get-Process -Name")
        for check in (
            "Assert-Sha256Text 'WantNew' $WantNew",
            "if ($buildHash -ne $wantNewUpper)",
            "if ($backupHash -ne $WantBackup.ToUpperInvariant())",
            "if ($before -ne $wantOldUpper)",
        ):
            with self.subTest(check=check):
                self.assertLess(self.text.index(check), dayz)
        self.assertLess(dayz, copy)
        self.assertGreater(self.text.index("$after = Get-Sha256OrNull $live"), copy)
        self.assertGreater(self.text.index("if ($after -ne $wantNewUpper)"), copy)

    def test_the_dayz_check_names_the_images_orphan_guard_calls_dayz(self) -> None:
        match = re.search(r"Get-Process -Name ([A-Za-z0-9_, ]+?) -ErrorAction", self.text)
        self.assertIsNotNone(match)
        listed = {name.strip().casefold() + ".exe" for name in match.group(1).split(",")}
        self.assertEqual(listed, set(orphan_guard._DAYZ_IMAGE_NAMES))


class DevToolsHygieneTest(unittest.TestCase):
    """tools/dev is public: parameters, never one machine's paths or one backup's hash."""

    def test_no_user_paths_and_no_fixed_hashes(self) -> None:
        tools = sorted(path for path in DEV_DIR.iterdir() if path.is_file())
        self.assertLessEqual(
            {"pbo_provenance.py", "setup_worktree.sh", "swap_pbo.ps1"},
            {path.name for path in tools},
        )
        for path in tools:
            text = path.read_text(encoding="utf-8")
            with self.subTest(tool=path.name):
                self.assertIsNone(re.search(r"(?i)[a-z]:[\\/]users[\\/]|/[a-z]/users/", text))
                self.assertNotIn("OneDrive", text)
                self.assertIsNone(re.search(r"\b[0-9A-Fa-f]{64}\b", text))
                self.assertIsNone(re.search(r"\b[0-9a-f]{40}\b", text))


if __name__ == "__main__":
    unittest.main()
