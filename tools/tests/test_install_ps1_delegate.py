"""install-mcp.ps1 -Register delegates to install_mcp.py --register.

The registration path is exercised through -ValidateRegisterInvocation, which
leaves before the venv, pip, or any host write. The Python process is a seam
command, never the installer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import install_mcp
from install_mcp import InstallerContractError
from tests.pe_helpers import write_fake_x64_pe

INSTALLER = TOOLS_DIR / "install-mcp.ps1"
# The interpreter running the tests: a CI runner creates its venv inside the checkout.
VENV_PYTHON = Path(sys.executable)

_SEAM = """@echo off
:loop
if "%~1"=="" goto done
>>"%DAYZ_MCP_SEAM_LOG%" echo %~1
shift
goto loop
:done
if "%DAYZ_MCP_SEAM_CODE%"=="" exit /b 0
echo {"status":"error","error":"%DAYZ_MCP_SEAM_TOKEN%"} 1>&2
exit /b %DAYZ_MCP_SEAM_CODE%
"""


def _powershell_env(root: Path, *, code: str = "", token: str = "") -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "DAYZ_MCP_INSTANCE",
        "DAYZ_MCP_PORT",
        "DAYZ_MCP_GAME_PATH",
        "DAYZ_MCP_SEAM_CODE",
        "DAYZ_MCP_SEAM_TOKEN",
    ):
        env.pop(name, None)
    bindir = root / "bin"
    bindir.mkdir()
    write_fake_x64_pe(bindir / "claude.exe")
    write_fake_x64_pe(bindir / "codex.exe")
    # The registration probe still talks to the shim. The pin must not.
    (bindir / "codex.cmd").write_text("@echo off\r\n", encoding="ascii")
    seam = root / "seam.cmd"
    seam.write_text(_SEAM, encoding="ascii", newline="\r\n")
    env["PATH"] = str(bindir)
    env["PATHEXT"] = ".CMD;.EXE;.BAT"
    env["DAYZ_MCP_REGISTER_SEAM"] = str(seam)
    env["DAYZ_MCP_SEAM_LOG"] = str(root / "seam.log")
    if code:
        env["DAYZ_MCP_SEAM_CODE"] = code
        env["DAYZ_MCP_SEAM_TOKEN"] = token
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(INSTALLER),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


def _seam_args(root: Path) -> list[str]:
    log = root / "seam.log"
    if not log.is_file():
        return []
    return [line.strip() for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


class InstallPs1DelegateTest(unittest.TestCase):
    def test_script_has_no_direct_mcp_add_or_remove_call(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("&") and ("mcp add" in stripped or "mcp remove" in stripped):
                self.fail(stripped)
        self.assertNotIn("function Undo-DayZMcpClaudeRegistration", source)
        self.assertIn("Submit-DayZMcpRegistration -Python $VenvPython", source)
        self.assertIn(
            "-AllowOptionRemoval:([bool]$registrationDecision.AllowOptionRemoval)",
            source,
        )
        self.assertIn(
            "default is registration print-only; rerun with -Register",
            source,
        )

    def test_validate_only_exits_before_any_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles = root / "server-profiles"
            env = os.environ.copy()
            for name in ("DAYZ_MCP_INSTANCE", "DAYZ_MCP_PORT", "DAYZ_MCP_GAME_PATH"):
                env.pop(name, None)
            completed = _run(
                ["-ValidateOnly", "-ServerProfiles", str(profiles), "-Register"],
                env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(profiles.exists())
            self.assertFalse((root / "seam.log").exists())

    def test_default_register_invocation_uses_install_mcp_register(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _powershell_env(root)
            completed = _run(["-ValidateRegisterInvocation"], env)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            args = _seam_args(root)
        self.assertIn("-B", args)
        installer = args[args.index("-B") + 1]
        self.assertTrue(installer.endswith("install_mcp.py"))
        self.assertEqual(args[args.index("-B") + 2], "--register")
        self.assertNotIn("--instance", args)
        self.assertNotIn("--game-path", args)
        self.assertNotIn("--allow-option-removal", args)
        self.assertIn("--claude-exe", args)
        self.assertIn("--codex-exe", args)
        self.assertTrue(args[args.index("--claude-exe") + 1].lower().endswith("claude.exe"))
        self.assertTrue(args[args.index("--codex-exe") + 1].lower().endswith("codex.exe"))

    def test_named_instance_register_invocation_forwards_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _powershell_env(root)
            completed = _run(
                [
                    "-ValidateRegisterInvocation",
                    "-Instance",
                    "130",
                    "-GamePath",
                    r"C:\DayZGames\DayZ",
                ],
                env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            args = _seam_args(root)
        self.assertEqual(args[args.index("--register") - 1].split("\\")[-1], "install_mcp.py")
        self.assertEqual(args[args.index("--instance") + 1], "130")
        self.assertEqual(args[args.index("--game-path") + 1], r"C:\DayZGames\DayZ")
        self.assertNotIn("--allow-option-removal", args)

    def test_python_nonzero_exit_propagates_with_its_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _powershell_env(root, code="2", token="registration_busy")
            completed = _run(["-ValidateRegisterInvocation"], env)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("registration_busy", completed.stderr.splitlines())

    def test_replace_switch_on_the_invocation_entry_passes_option_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _powershell_env(root)
            completed = _run(
                ["-ValidateRegisterInvocation", "-ReplaceExistingRegistration"],
                env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            args = _seam_args(root)
        self.assertIn("--allow-option-removal", args)
        self.assertIn("--register", args)

    def test_delegate_codex_argument_satisfies_the_pin_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _powershell_env(root)
            completed = _run(["-ValidateRegisterInvocation"], env)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            args = _seam_args(root)
            codex = Path(args[args.index("--codex-exe") + 1])
            claude = Path(args[args.index("--claude-exe") + 1])
            self.assertEqual(
                install_mcp._resolve_pin_cli("CODEX", codex).name.casefold(),
                "codex.exe",
            )
            self.assertEqual(
                install_mcp._resolve_pin_cli("CLAUDE", claude).name.casefold(),
                "claude.exe",
            )
            shim = codex.with_name("codex.cmd")
            with self.assertRaises(InstallerContractError) as raised:
                install_mcp._resolve_pin_cli("CODEX", shim)
            self.assertEqual(raised.exception.code, "installer_cli_not_native_exe")

    def test_trailing_backslash_reaches_native_python_argv(self) -> None:
        game_path = "C:\\DayZGames\\DayZ\\"
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "argv_consumer.ps1"
            probe.write_text(
                r'''
param([string]$SourcePath, [string]$Python, [string]$GamePath)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'PowerShell source did not parse' }
foreach ($name in @('Format-DayZMcpNativeCommandLine', 'Invoke-NativeRegistrationCommand')) {
  $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
  if ($null -eq $functionAst) { throw "Missing function $name" }
  . ([scriptblock]::Create($functionAst.Extent.Text))
}
$captured = Invoke-NativeRegistrationCommand -CommandPath $Python -Arguments @(
  '-c',
  'import json,sys; print(json.dumps(sys.argv[1:]))',
  '--game-path',
  $GamePath,
  '--no-supervised'
)
if ($captured.ExitCode -ne 0) { throw $captured.Stderr }
Write-Output $captured.Stdout.Trim()
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-File",
                    str(probe),
                    "-SourcePath",
                    str(INSTALLER),
                    "-Python",
                    str(VENV_PYTHON),
                    "-GamePath",
                    game_path,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        received = json.loads(completed.stdout.strip())
        self.assertEqual(
            received,
            ["--game-path", game_path, "--no-supervised"],
        )
        options = install_mcp.parse_args(["--register", *received])
        self.assertEqual(options.game_path, os.path.normpath(game_path))
        self.assertFalse(options.supervised)

    def test_removed_rollback_switch_is_refused_by_name(self) -> None:
        env = os.environ.copy()
        for name in ("DAYZ_MCP_INSTANCE", "DAYZ_MCP_PORT", "DAYZ_MCP_GAME_PATH"):
            env.pop(name, None)
        completed = _run(["-ValidateRegistrationRollback"], env)
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("registration_rollback_seam_removed", completed.stderr.splitlines())


if __name__ == "__main__":
    unittest.main()
