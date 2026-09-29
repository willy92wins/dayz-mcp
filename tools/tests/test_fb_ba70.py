"""capture grab must not inherit a poisoned Git Bash PSModulePath.

fb-20260915-011312-ba70: launching mcp-grab.ps1 with the parent
PSModulePath makes Windows PowerShell fail to find its cmdlets.

Launch tests stand in for powershell.exe at subprocess.Popen and point
GRAB_SCRIPT at an inert file, so no test starts PowerShell or reaches the
desktop. A powershell.cmd stub on PATH is not a stand-in: CreateProcess only
appends .exe to the bare "powershell" name, so the real grab would run.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import mcp_capture


_POISON = "/git/bash/poisoned/Modules"


class _ExitedGrab:
    def __init__(self, stdout: str, stderr: str, code: int) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = code

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def _fake_grab_child(cmd: list[str], **kwargs: Any) -> _ExitedGrab:
    """powershell.exe running mcp-grab.ps1 with no DayZ window.

    The child sees ``env`` when one is passed and the parent environment
    otherwise, as Popen does. A PSModulePath in any casing fails like the
    poisoned Windows PowerShell, with no JSON on stdout; stderr carries the
    inherited value so a leak into the capture error stays visible.
    """
    env = kwargs.get("env")
    child_env = os.environ if env is None else env
    leaked = [value for key, value in child_env.items() if key.casefold() == "psmodulepath"]
    if leaked:
        return _ExitedGrab("", f"psmodulepath_leaked: {leaked[0]}\n", 1)
    return _ExitedGrab('{"ok":false,"error":"no_window"}\n', "", 0)


class GrabPsModulePathTest(unittest.TestCase):
    def test_grab_env_drops_psmodulepath(self) -> None:
        poisoned = {"PATH": "/bin", "PSModulePath": "/git/poisoned/Modules", "HOME": "/tmp"}
        env = mcp_capture._grab_subprocess_env(poisoned)
        self.assertNotIn("PSModulePath", env)
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["HOME"], "/tmp")

    def test_grab_env_drops_any_psmodulepath_casing(self) -> None:
        env = mcp_capture._grab_subprocess_env({"psmodulepath": "unix-poison", "Keep": "1"})
        self.assertNotIn("psmodulepath", env)
        self.assertEqual(env["Keep"], "1")

    def test_missing_powershell_is_command_not_found(self) -> None:
        with mock.patch.object(mcp_capture, "probe_input_desktop", return_value="unlocked"):
            with mock.patch.object(mcp_capture.os.path, "exists", return_value=True):
                with mock.patch.object(
                    mcp_capture.subprocess,
                    "Popen",
                    side_effect=FileNotFoundError("powershell"),
                ):
                    result = mcp_capture._run_window_capture(
                        "frame.png", "DayZDiag_x64", 8.0
                    )
        self.assertEqual(
            {"ok": False, "error": "capture_backend_failed:command_not_found"},
            result,
        )

    def _launch_with_poisoned_parent(self) -> tuple[dict[str, Any], mock.Mock, str]:
        run = mock.Mock(side_effect=_fake_grab_child)
        with tempfile.TemporaryDirectory() as tmp:
            grab_script = os.path.join(tmp, "mcp-grab.ps1")
            Path(grab_script).write_text("# Inert grab script.\n", encoding="utf-8")
            with mock.patch.object(mcp_capture, "probe_input_desktop", return_value="unlocked"):
                with mock.patch.object(mcp_capture, "GRAB_SCRIPT", grab_script):
                    with mock.patch.dict(
                        os.environ,
                        {"PSModulePath": _POISON, "psmodulepath": _POISON},
                        clear=False,
                    ):
                        with mock.patch.object(mcp_capture.subprocess, "Popen", run):
                            result = mcp_capture._run_window_capture(
                                os.path.join(tmp, "frame.png"), "DayZDiag_x64", 8.0
                            )
        return result, run, grab_script

    def test_launch_grab_with_poisoned_psmodulepath(self) -> None:
        result, run, grab_script = self._launch_with_poisoned_parent()

        self.assertEqual({"ok": False, "error": "no_window"}, result)
        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertEqual("powershell", cmd[0])
        self.assertEqual(grab_script, cmd[cmd.index("-File") + 1])
        env = run.call_args.kwargs["env"]
        self.assertEqual([], [key for key in env if key.casefold() == "psmodulepath"])
        self.assertFalse(any(_POISON in value for value in env.values()))

    def test_launch_grab_detects_grab_env_keeping_psmodulepath(self) -> None:
        # Positive control: a grab env that keeps PSModulePath must reach the
        # capture error, or the launch test above would pass vacuously.
        def keep_psmodulepath(base: dict[str, str] | None = None) -> dict[str, str]:
            return dict(os.environ if base is None else base)

        with mock.patch.object(mcp_capture, "_grab_subprocess_env", keep_psmodulepath):
            result, run, _grab_script = self._launch_with_poisoned_parent()

        run.assert_called_once()
        self.assertEqual(
            {"ok": False, "error": f"capture_backend_failed: psmodulepath_leaked: {_POISON}"},
            result,
        )


if __name__ == "__main__":
    unittest.main()
