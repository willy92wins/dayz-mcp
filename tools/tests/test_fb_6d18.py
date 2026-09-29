"""Locked Windows sessions fail capture as session_locked, not a generic backend error.

Window-grab backends need the interactive desktop. Tests patch the desktop
probe, ctypes.WinDLL and subprocess.Popen so they never call OpenInputDesktop.
"""

from __future__ import annotations

import ctypes
import io
import unittest
from pathlib import Path
from unittest import mock

import mcp_capture
from dayz_mcp import server


class _SinkRuntime:
    """Only what _wire_safe_error touches: a local log sink."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._log = self.lines.append


class _ExitedGrab:
    def __init__(self, stdout: str = "", stderr: str = "", code: int = 1) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = code

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def _patched_popen() -> _ExitedGrab:
    return _ExitedGrab(stdout="", stderr="patched", code=1)


class Fb6d18CaptureSessionLockedTest(unittest.TestCase):
    def test_fb_6d18_locked_probe_returns_session_locked_without_subprocess(self) -> None:
        popen = mock.Mock(return_value=_patched_popen())
        with mock.patch.object(mcp_capture, "probe_input_desktop", return_value="locked"):
            with mock.patch.object(mcp_capture.os.path, "exists", return_value=True):
                with mock.patch.object(mcp_capture.subprocess, "Popen", popen):
                    result = mcp_capture._run_window_capture("frame.png", "DayZDiag_x64", 8.0)

        self.assertEqual({"ok": False, "error": "session_locked"}, result)
        popen.assert_not_called()

    def test_fb_6d18_unknown_probe_reaches_existing_path(self) -> None:
        popen = mock.Mock(return_value=_patched_popen())
        with mock.patch.object(mcp_capture, "probe_input_desktop", return_value="unknown"):
            with mock.patch.object(mcp_capture.os.path, "exists", return_value=True):
                with mock.patch.object(mcp_capture.subprocess, "Popen", popen):
                    result = mcp_capture._run_window_capture("frame.png", "DayZDiag_x64", 8.0)

        popen.assert_called_once()
        self.assertNotEqual("session_locked", result.get("error"))
        self.assertTrue(str(result.get("error") or "").startswith("capture_backend_failed"))

    def test_fb_6d18_unlocked_probe_reaches_existing_path(self) -> None:
        popen = mock.Mock(return_value=_patched_popen())
        with mock.patch.object(mcp_capture, "probe_input_desktop", return_value="unlocked"):
            with mock.patch.object(mcp_capture.os.path, "exists", return_value=True):
                with mock.patch.object(mcp_capture.subprocess, "Popen", popen):
                    result = mcp_capture._run_window_capture("frame.png", "DayZDiag_x64", 8.0)

        popen.assert_called_once()
        self.assertNotEqual("session_locked", result.get("error"))
        self.assertTrue(str(result.get("error") or "").startswith("capture_backend_failed"))

    def test_fb_6d18_wire_safe_error_keeps_session_locked_intact(self) -> None:
        runtime = _SinkRuntime()
        wire = server._wire_safe_error(runtime, "capture_screenshot", "session_locked")
        self.assertEqual("session_locked", wire)
        self.assertEqual([], runtime.lines)

    def test_fb_6d18_capture_screenshot_description_names_session_locked_and_unfocused_fps(self) -> None:
        text = Path(server.__file__).read_text(encoding="utf-8")
        start = text.index("Capture a screenshot from the DayZDiag window.")
        end = text.index("async def capture_screenshot")
        description = text[start:end]
        self.assertIn("session_locked", description)
        self.assertIn("Windows session is locked", description)
        self.assertIn("both window-grab backends need the interactive desktop", description)
        self.assertIn("retrying does not help until the session is unlocked", description)
        self.assertIn("unattended runs must keep it unlocked", description)
        self.assertIn("An unfocused DayZDiag client renders at about 20 fps", description)
        self.assertIn("client-side timing depends on which window owns the foreground", description)


class Fb6d18ProbeInputDesktopTest(unittest.TestCase):
    """probe_input_desktop against a fake user32: the real OpenInputDesktop is never reached."""

    def _probe(self, user32: mock.Mock, platform: str = "win32") -> tuple[str, mock.Mock]:
        windll = mock.Mock(return_value=user32)
        with mock.patch.object(mcp_capture.sys, "platform", platform):
            with mock.patch("ctypes.WinDLL", windll, create=True):
                verdict = mcp_capture.probe_input_desktop()
        return verdict, windll

    def test_fb_6d18_probe_null_handle_is_locked_and_closes_nothing(self) -> None:
        user32 = mock.Mock()
        user32.OpenInputDesktop.return_value = None
        verdict, windll = self._probe(user32)

        self.assertEqual("locked", verdict)
        windll.assert_called_once_with("user32", use_last_error=True)
        user32.OpenInputDesktop.assert_called_once_with(0, False, 0x0100)
        user32.CloseDesktop.assert_not_called()

    def test_fb_6d18_probe_live_handle_is_unlocked_and_closed(self) -> None:
        handle = 0x7FFF00001234
        user32 = mock.Mock()
        user32.OpenInputDesktop.return_value = handle
        verdict, _windll = self._probe(user32)

        self.assertEqual("unlocked", verdict)
        user32.CloseDesktop.assert_called_once_with(handle)

    def test_fb_6d18_probe_declares_pointer_sized_desktop_handle(self) -> None:
        user32 = mock.Mock()
        user32.OpenInputDesktop.return_value = 0x7FFF00001234
        self._probe(user32)

        self.assertIs(ctypes.c_void_p, user32.OpenInputDesktop.restype)
        self.assertEqual([ctypes.c_void_p], user32.CloseDesktop.argtypes)

    def test_fb_6d18_probe_exception_is_unknown(self) -> None:
        user32 = mock.Mock()
        user32.OpenInputDesktop.side_effect = OSError("access denied")
        verdict, _windll = self._probe(user32)

        self.assertEqual("unknown", verdict)
        user32.CloseDesktop.assert_not_called()

    def test_fb_6d18_probe_off_windows_is_unknown_without_user32(self) -> None:
        user32 = mock.Mock()
        verdict, windll = self._probe(user32, platform="linux")

        self.assertEqual("unknown", verdict)
        windll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
