"""tools/dev/swap_pbo.ps1 swaps a live PBO only when both hashes are the expected ones.

fb-20260819-024951-e307: deploying a PBO prepared from an older tree would have
silently reverted another session's work. The swap refuses unless the build is
exactly -WantNew and the live file is still exactly -WantOld, refuses while a DayZ
game process runs, and verifies the live file after the swap.

Review R1 (Codex) found two holes, both covered here:
  F1  the live PBO was hashed, then overwritten later by path: another promotion
      could install its build in between and be destroyed, and the build could
      change after its hash. The swap now holds the live PBO with share mode none
      from the hash to the move, installs the bytes it hashed, and moves files by
      renames that never replace one.
  F2  a -Backup that was a hard link to the live PBO passed a path comparison and
      was overwritten with it. The backup is now compared by file id, held against
      writers during the swap and hashed again at the end.
The interference cases dot-source the script with Get-Process shadowed: it runs at
the DayZ check, while the swap holds its locks, and returns no process so the lock
is what the case measures.

Every run targets a temporary <Destination>\\DayZ_MCP.pbo, never the game's, under
the PSModulePath a Git Bash parent leaves behind. While a DayZ game runs on this
machine the plain swap must refuse, so the success case asserts that refusal then.
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
_TIMEOUT_S = 120

# Dot-sourced, the swap runs in this script's scope and calls this Get-Process at its
# DayZ check: the statement runs while the swap holds its locks.
_INTERFERENCE_WRAPPER = """\
$ErrorActionPreference = 'Stop'
$script:attempt = 'not attempted'
function Get-Process {{
  param([string[]]$Name, $ErrorAction)
  $ErrorActionPreference = 'Continue'
  try {{
    {statement}
    $script:attempt = 'succeeded'
  }} catch {{
    $script:attempt = 'failed: ' + $_.Exception.Message
  }}
  @()
}}
try {{
  . {swap} {arguments}
  'SWAP-RESULT ok'
}} catch {{
  'SWAP-RESULT failed: ' + $_.Exception.Message
}}
'INTERFERENCE ' + $script:attempt
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _ps(value: object) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _flat(text: str) -> str:
    """PowerShell wraps long error lines; compare on single spaces."""
    return " ".join(text.split())


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
        self.old_bytes = self.live.read_bytes()
        self.new_bytes = self.build.read_bytes()
        self.old = _sha(self.live)
        self.new = _sha(self.build)
        self.base = ("-Build", str(self.build), "-WantNew", self.new, "-WantOld", self.old)

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PSModulePath"] = _POISONED_PSMODULEPATH
        return env

    def swap(self, *args: str) -> tuple[int, str]:
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
            env=self.environment(),
        )
        return done.returncode, _flat(done.stdout + done.stderr)

    def interfere(self, statement: str, *args: str) -> dict[str, str]:
        """Run the swap dot-sourced, with ``statement`` run at its DayZ check."""
        arguments = " ".join(
            [f"-Destination {_ps(self.addons)}"]
            + [(item if item.startswith("-") else _ps(item)) for item in args]
        )
        wrapper = self.root / "interfere.ps1"
        wrapper.write_text(
            _INTERFERENCE_WRAPPER.format(statement=statement, swap=_ps(SWAP_PS1), arguments=arguments),
            encoding="utf-8",
        )
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_TIMEOUT_S,
            env=self.environment(),
        )
        output = done.stdout + done.stderr
        result = {"output": _flat(output)}
        for line in output.splitlines():
            for key in ("SWAP-RESULT", "INTERFERENCE"):
                if line.startswith(key + " "):
                    result[key] = line[len(key) + 1:]
        self.assertIn("SWAP-RESULT", result, output)
        self.assertIn("INTERFERENCE", result, output)
        return result

    def sidecars(self) -> list[Path]:
        return sorted(self.addons.glob("DayZ_MCP.pbo.swapped_out_*"))

    def assert_no_temp_left(self) -> None:
        self.assertEqual(sorted(self.addons.glob("*.swap_new_*")), [], "a staged copy was left behind")

    def assert_refused(self, fragment: str, *args: str) -> str:
        code, text = self.swap(*args)
        self.assertNotEqual(code, 0, text)
        self.assertIn(fragment, text)
        self.assertNotIn("PBO SWAPPED", text)
        self.assertEqual(self.live.read_bytes(), self.old_bytes, "a refused swap must leave the live PBO alone")
        self.assertEqual(self.sidecars(), [])
        self.assert_no_temp_left()
        return text

    def assert_swapped(self) -> None:
        self.assertEqual(self.live.read_bytes(), self.new_bytes)
        sidecars = self.sidecars()
        self.assertEqual(len(sidecars), 1, sidecars)
        self.assertEqual(sidecars[0].read_bytes(), self.old_bytes, "the previous PBO must survive the swap")
        self.assertTrue(sidecars[0].name.endswith("_" + self.old[:16]), sidecars[0].name)
        self.assert_no_temp_left()

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
            self.assertEqual(self.live.read_bytes(), self.old_bytes)
            self.assertEqual(self.sidecars(), [])
            self.assert_no_temp_left()
            return
        self.assertEqual(code, 0, text)
        self.assert_swapped()
        self.assertIn("live before: {0}".format(self.old), text)
        self.assertIn("live after : {0}".format(self.new), text)
        self.assertIn("previous PBO kept as: {0}".format(self.sidecars()[0]), text)
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
        self.assert_no_temp_left()

    @slow_test
    def test_r1_f1_a_writer_during_the_swap_is_locked_out(self) -> None:
        # The reviewer's repro: another promotion installs C between the hash and the swap.
        result = self.interfere(
            f"[IO.File]::WriteAllText({_ps(self.live)}, 'intervening live build C')", *self.base
        )

        self.assertTrue(result["INTERFERENCE"].startswith("failed:"), result["output"])
        self.assertEqual(result["SWAP-RESULT"], "ok", result["output"])
        self.assert_swapped()

    @slow_test
    def test_r1_f1_a_build_changed_after_its_hash_is_not_installed(self) -> None:
        result = self.interfere(
            f"[IO.File]::WriteAllText({_ps(self.build)}, 'changed after its hash')", *self.base
        )

        self.assertEqual(result["INTERFERENCE"], "succeeded", result["output"])
        self.assertEqual(result["SWAP-RESULT"], "ok", result["output"])
        self.new_bytes = b"new build of commit B\n"
        self.assert_swapped()

    @slow_test
    def test_r1_f1_a_second_swap_during_the_first_is_refused(self) -> None:
        other = self.root / "other" / "DayZ_MCP.pbo"
        other.parent.mkdir()
        other.write_bytes(b"another session's build D\n")
        statement = (
            f"$childOut = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
            f"-File {_ps(SWAP_PS1)} -Destination {_ps(self.addons)} -Build {_ps(other)} "
            f"-WantNew {_ps(_sha(other))} -WantOld {_ps(self.old)} 2>&1 | Out-String\n"
            f"    if ($LASTEXITCODE -eq 0) {{ throw ('second swap went through: ' + $childOut) }}\n"
            f"    throw ('second swap refused: ' + $childOut)"
        )

        result = self.interfere(statement, *self.base)

        self.assertIn("second swap refused", result["INTERFERENCE"], result["output"])
        self.assertIn("The live PBO is in use", _flat(result["INTERFERENCE"]), result["output"])
        self.assertEqual(result["SWAP-RESULT"], "ok", result["output"])
        self.assert_swapped()

    @slow_test
    def test_r1_f2_a_hard_link_backup_is_refused(self) -> None:
        # The reviewer's repro: the "backup" is another name for the live PBO.
        self.backup.unlink()
        os.link(self.live, self.backup)

        self.assert_refused(
            "The backup is the live PBO itself",
            *self.base, "-Backup", str(self.backup), "-WantBackup", self.old,
        )
        self.assertEqual(self.backup.read_bytes(), self.old_bytes)

    @slow_test
    def test_r1_f2_the_backup_is_held_against_writers_during_the_swap(self) -> None:
        result = self.interfere(
            f"[IO.File]::WriteAllText({_ps(self.backup)}, 'rollback overwritten mid-swap')",
            *self.base, "-Backup", str(self.backup), "-WantBackup", self.old,
        )

        self.assertTrue(result["INTERFERENCE"].startswith("failed:"), result["output"])
        self.assertEqual(result["SWAP-RESULT"], "ok", result["output"])
        self.assert_swapped()
        self.assertEqual(self.backup.read_bytes(), self.old_bytes)

    @slow_test
    def test_a_live_pbo_held_by_another_process_is_refused_as_in_use(self) -> None:
        import _winapi

        # Share mode none, as a running game or another swap would hold it.
        handle = _winapi.CreateFile(str(self.live), _winapi.GENERIC_READ, 0, 0, _winapi.OPEN_EXISTING, 0, 0)
        try:
            code, text = self.swap(*self.base)
        finally:
            _winapi.CloseHandle(handle)

        self.assertNotEqual(code, 0, text)
        self.assertIn("The live PBO is in use", text)
        self.assertEqual(self.live.read_bytes(), self.old_bytes)
        self.assertEqual(self.sidecars(), [])
        self.assert_no_temp_left()


class SwapPboContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SWAP_PS1.read_text(encoding="utf-8")

    def test_every_check_runs_before_the_move_and_the_dayz_check_runs_last(self) -> None:
        move_aside = self.text.index("[DayZMcpDevTools.SwapPboFile]::Rename($old, $sidecar)")
        move_in = self.text.index("[DayZMcpDevTools.SwapPboFile]::Rename($new, $liveFull)")
        dayz = self.text.index("Get-Process -Name")
        for check in (
            "Assert-Sha256Text 'WantNew' $WantNew",
            "if ($buildHash -ne $wantNewUpper)",
            "if ($backupId -eq $liveId)",
            "$opened = Open-SwapFile $liveFull $false $true 0 $false",
            "if ($before -ne $wantOldUpper)",
            "if ($backupHash -ne $WantBackup.ToUpperInvariant())",
        ):
            with self.subTest(check=check):
                self.assertLess(self.text.index(check), dayz)
        self.assertLess(dayz, move_aside)
        self.assertLess(move_aside, move_in)
        self.assertGreater(self.text.index("if ($viaHandle -ne $wantNewUpper"), move_in)
        self.assertNotIn("Copy-Item", self.text)
        self.assertNotIn("Move-Item", self.text)

    def test_renames_never_replace_a_file(self) -> None:
        rename = self.text[self.text.index("public static int Rename("):]
        rename = rename[:rename.index("public static int MarkForDeletion(")]
        # FILE_RENAME_INFO is zeroed: ReplaceIfExists stays FALSE.
        self.assertIn("Marshal.Copy(new byte[size], 0, buffer, size);", rename)
        self.assertNotIn("ReplaceIfExists = true", rename)
        self.assertNotIn("WriteByte(buffer, 0, 1)", rename)

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
