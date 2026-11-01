"""One PowerShell process per multi-frame capture (perf/capture-one-process).

Before, grab_stable_frame launched mcp-grab.ps1 once per frame, paying the
cold powershell.exe + Add-Type start (~0.5 s) N times and spacing frames by
that start instead of DEFAULT_FRAME_INTERVAL_S. Now mcp-grab.ps1 takes
-FrameCount/-FrameIntervalMs/-FramePattern and emits one JSON line per frame
(frameIndex added), and mcp_capture._run_window_captures launches it once.

These tests simulate subprocess.Popen only: no PowerShell, no window.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from PIL import Image

import mcp_capture
from tests._frame_state_isolation import isolate_capture_frame_state
from tests._tiers import slow_test

WINDOW = {"pid": 4242, "class": "DayZ", "title": "fixture", "left": 0, "top": 0, "width": 16, "height": 16}
CLIENT = {"left": 0, "top": 0, "width": 16, "height": 16}


def _arg(cmd: list[str], name: str) -> str:
    return cmd[cmd.index(name) + 1]


class _BatchGrab:
    """Fake powershell child honouring the grab script's wire protocol.

    Writes a solid-grey PNG per frame to the -FramePattern targets (or the
    single -CapturePng) and prints started + one JSON line per frame. When
    fail_at is set, that frame emits a failure line and later frames emit
    nothing, exactly as the script exits on the first failed frame.
    """

    def __init__(self, cmd: list[str], levels: tuple[int, ...], fail_at: int | None = None) -> None:
        self.returncode: int | None = 0
        self.killed = False
        self.wait_timeouts: list[float | None] = []
        frame_count = int(_arg(cmd, "-FrameCount")) if "-FrameCount" in cmd else 1
        pattern = _arg(cmd, "-FramePattern") if "-FramePattern" in cmd else ""
        capture = _arg(cmd, "-CapturePng")
        lines = ['{"phase":"started"}']
        for index in range(frame_count):
            if fail_at is not None and index == fail_at:
                payload: dict[str, object] = {
                    "ok": False, "error": "printwindow_failed", "method": "printwindow",
                    "window": dict(WINDOW), "stats": None, "sha256": "",
                    "client": dict(CLIENT), "clientStats": None,
                }
                if frame_count > 1:
                    payload["frameIndex"] = index
                lines.append(json.dumps(payload))
                break
            level = levels[index] if index < len(levels) else 100
            if pattern:
                # Mirror the fixed script: "{0}" is substituted only in the
                # file name, never in the directory part of the pattern.
                pattern_dir = os.path.dirname(pattern)
                pattern_name = os.path.basename(pattern).replace("{0}", str(index))
                path = os.path.join(pattern_dir, pattern_name) if pattern_dir else pattern_name
            else:
                path = capture
            Image.new("RGB", (16, 16), (level, level, level)).save(path, format="PNG")
            payload = {
                "ok": True, "error": "", "method": "printwindow",
                "window": dict(WINDOW),
                "stats": {"meanBrightness": float(level), "nonBlackRatio": 1.0},
                "sha256": f"{index:064x}",
                "client": dict(CLIENT),
                "clientStats": {"meanBrightness": float(level), "nonBlackRatio": 1.0},
            }
            if frame_count > 1:
                payload["frameIndex"] = index
            lines.append(json.dumps(payload))
        self.stdout = io.StringIO("\n".join(lines) + "\n")
        self.stderr = io.StringIO("")

    def poll(self) -> int | None:
        return -9 if self.killed else self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        return -9 if self.killed else 0

    def kill(self) -> None:
        self.killed = True


class _HangingGrab:
    def __init__(self, stdout: str) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO("")
        self.returncode: int | None = None
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        return -9 if self.killed else self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self.killed:
            self.returncode = -9
            return -9
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=timeout)

    def kill(self) -> None:
        self.killed = True


class CaptureOneProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        isolate_capture_frame_state(self)

    def _patched(self, popen: mock.Mock):
        return (
            mock.patch.object(mcp_capture, "probe_input_desktop", return_value="unlocked"),
            mock.patch.object(mcp_capture.subprocess, "Popen", popen),
        )

    def test_one_process_for_n_frames_and_stable_pick(self) -> None:
        levels = (100, 102, 160, 30)  # calmest adjacent pair is (0, 1)
        popen = mock.Mock(side_effect=lambda cmd, **kw: _BatchGrab(cmd, levels))
        probe, popen_patch = self._patched(popen)
        with probe, popen_patch:
            frame = mcp_capture.grab_stable_frame(frames=4)

        self.assertEqual(1, popen.call_count)
        cmd = popen.call_args.args[0]
        self.assertEqual("4", _arg(cmd, "-FrameCount"))
        self.assertEqual("120", _arg(cmd, "-FrameIntervalMs"))
        self.assertIn("{0}", _arg(cmd, "-FramePattern"))
        self.assertIsInstance(frame, Image.Image)
        assert isinstance(frame, Image.Image)
        self.assertEqual(f"{0:064x}", frame.info.get("sha256"))
        self.assertEqual((100, 100, 100), frame.getpixel((0, 0)))
        frame.close()

    def test_helper_returns_one_payload_per_frame_from_one_process(self) -> None:
        levels = (10, 20, 30)
        popen = mock.Mock(side_effect=lambda cmd, **kw: _BatchGrab(cmd, levels))
        probe, popen_patch = self._patched(popen)
        with tempfile.TemporaryDirectory() as tmp:
            paths = [os.path.join(tmp, f"frame_{i}.png") for i in range(3)]
            with probe, popen_patch:
                results = mcp_capture._run_window_captures(paths, process_name="DayZDiag_x64", timeout_s=8.0)
            self.assertEqual(1, popen.call_count)
            self.assertEqual([True, True, True], [r.get("ok") for r in results])
            self.assertEqual([f"{i:064x}" for i in range(3)], [r.get("sha256") for r in results])
            self.assertNotIn("frameIndex", results[0])
            for path in paths:
                self.assertTrue(os.path.exists(path), path)

    def test_frame_count_one_keeps_legacy_command_and_parse(self) -> None:
        popen = mock.Mock(side_effect=lambda cmd, **kw: _BatchGrab(cmd, (77,)))
        probe, popen_patch = self._patched(popen)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "frame_0.png")
            with probe, popen_patch:
                result = mcp_capture._run_window_capture(path, process_name="DayZDiag_x64", timeout_s=8.0)
            cmd = popen.call_args.args[0]
            self.assertEqual(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    mcp_capture.GRAB_SCRIPT, "-ProcessName", "DayZDiag_x64",
                    "-CapturePng", path, "-Method", mcp_capture.DEFAULT_GRAB_METHOD,
                ],
                cmd,
            )
            self.assertNotIn("-FrameCount", cmd)
            self.assertTrue(os.path.exists(path))
        self.assertEqual(
            {
                "ok": True, "error": "", "method": "printwindow", "window": WINDOW,
                "stats": {"meanBrightness": 77.0, "nonBlackRatio": 1.0},
                "sha256": f"{0:064x}", "client": CLIENT,
                "clientStats": {"meanBrightness": 77.0, "nonBlackRatio": 1.0},
            },
            result,
        )

    def test_locked_session_launches_no_process(self) -> None:
        popen = mock.Mock(side_effect=lambda cmd, **kw: _BatchGrab(cmd, (1, 2)))
        with mock.patch.object(mcp_capture, "probe_input_desktop", return_value="locked"), mock.patch.object(
            mcp_capture.subprocess, "Popen", popen
        ):
            results = mcp_capture._run_window_captures(["a.png", "b.png"], process_name="DayZDiag_x64", timeout_s=8.0)
        self.assertEqual([{"ok": False, "error": "session_locked"}], results)
        popen.assert_not_called()

    def test_start_timeout_is_one_process_and_not_capture_timeout(self) -> None:
        hanging = _HangingGrab("")
        popen = mock.Mock(return_value=hanging)
        probe, popen_patch = self._patched(popen)
        with mock.patch.object(mcp_capture, "CAPTURE_START_BUDGET_S", 0.05), probe, popen_patch:
            results = mcp_capture._run_window_captures(
                ["a.png", "b.png", "c.png"], process_name="DayZDiag_x64", timeout_s=8.0
            )
        self.assertEqual([{"ok": False, "error": "capture_start_timeout"}], results)
        self.assertEqual(1, popen.call_count)
        self.assertTrue(hanging.killed)

    def test_capture_timeout_budget_scales_with_frame_count(self) -> None:
        hanging = _HangingGrab('{"phase":"started"}\n')
        popen = mock.Mock(return_value=hanging)
        probe, popen_patch = self._patched(popen)
        with probe, popen_patch:
            results = mcp_capture._run_window_captures(
                ["a.png", "b.png", "c.png"], process_name="DayZDiag_x64", timeout_s=2.0
            )
        self.assertEqual([{"ok": False, "error": "capture_timeout"}], results)
        # First wait is the capture budget (3 frames x 2.0 s); _kill then reaps.
        self.assertEqual(6.0, hanging.wait_timeouts[0])
        self.assertTrue(hanging.killed)

    def test_failure_at_frame_k_cuts_the_capture(self) -> None:
        levels = (100, 102, 160, 30)
        popen = mock.Mock(side_effect=lambda cmd, **kw: _BatchGrab(cmd, levels, fail_at=2))
        probe, popen_patch = self._patched(popen)
        with tempfile.TemporaryDirectory() as tmp:
            paths = [os.path.join(tmp, f"frame_{i}.png") for i in range(4)]
            with probe, popen_patch:
                results = mcp_capture._run_window_captures(paths, process_name="DayZDiag_x64", timeout_s=8.0)
            self.assertEqual(1, popen.call_count)
            self.assertEqual(3, len(results))
            self.assertEqual("printwindow_failed", results[-1].get("error"))
            self.assertFalse(os.path.exists(paths[3]))

        popen2 = mock.Mock(side_effect=lambda cmd, **kw: _BatchGrab(cmd, levels, fail_at=2))
        probe2, popen_patch2 = self._patched(popen2)
        with probe2, popen_patch2:
            result = mcp_capture.grab_stable_frame(frames=4)
        self.assertEqual({"isError": True, "error": "printwindow_failed"}, result)
        self.assertEqual(1, popen2.call_count)

    def test_rename_failure_with_preexisting_destination_is_an_error(self) -> None:
        popen = mock.Mock(side_effect=lambda cmd, **kw: _BatchGrab(cmd, (100, 120)))
        probe, popen_patch = self._patched(popen)
        with tempfile.TemporaryDirectory() as tmp:
            paths = [os.path.join(tmp, "dest_a.png"), os.path.join(tmp, "dest_b.png")]
            for path in paths:
                with open(path, "wb") as handle:
                    handle.write(b"OLD-CAPTURE")
            with probe, popen_patch, mock.patch.object(
                os, "replace", side_effect=OSError("rename denied")
            ):
                results = mcp_capture._run_window_captures(
                    paths, process_name="DayZDiag_x64", timeout_s=8.0
                )
        self.assertEqual(1, len(results))
        self.assertIsNot(results[0].get("ok"), True)
        self.assertEqual("capture_backend_failed: rename_failed", results[0].get("error"))
        self.assertNotIn(tmp, str(results[0].get("error")))

    def test_brace_directory_keeps_frames_inside_it(self) -> None:
        popen = mock.Mock(side_effect=lambda cmd, **kw: _BatchGrab(cmd, (100, 120)))
        probe, popen_patch = self._patched(popen)
        with tempfile.TemporaryDirectory() as tmp:
            brace_dir = os.path.join(tmp, "literal{0}")
            os.makedirs(brace_dir)
            paths = [os.path.join(brace_dir, f"frame_{i}.png") for i in range(2)]
            pattern, targets = mcp_capture._frame_pattern_for(paths)
            self.assertEqual(paths, targets)
            self.assertEqual(brace_dir, os.path.dirname(pattern))
            self.assertEqual("frame_{0}.png", os.path.basename(pattern))
            with probe, popen_patch:
                results = mcp_capture._run_window_captures(
                    paths, process_name="DayZDiag_x64", timeout_s=8.0
                )
            self.assertEqual([True, True], [r.get("ok") for r in results])
            for path in paths:
                self.assertTrue(os.path.exists(path), path)
            self.assertFalse(os.path.exists(os.path.join(tmp, "literal0")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "literal1")))

    def test_grab_script_emits_started_once_in_source(self) -> None:
        with open(mcp_capture.GRAB_SCRIPT, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count("GetBytes('{\"phase\":\"started\"}'"))
        self.assertIn("[int]$FrameCount = 1", text)
        self.assertIn("[int]$FrameIntervalMs = 120", text)
        self.assertIn("[string]$FramePattern = ''", text)


class RealGrabScriptTest(unittest.TestCase):
    """Regression for the one-process P1: run the REAL mcp-grab.ps1.

    The simulated-Popen tests above cannot see PowerShell pipeline semantics:
    Emit used to write to the success stream inside Capture-OneFrame, so the
    caller either swallowed the result line ([void](...)) or captured it into
    an always-truthy array. Against a real explorer taskbar window the fixed
    script prints started plus exactly one ok:true result line per frame, and
    every reported sha256 matches the PNG on disk.
    """

    def _run_real_grab(self, frame_count: int, tmp: str) -> tuple[list[dict], list[str], str]:
        base = os.path.join(tmp, "tray.png")
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", mcp_capture.GRAB_SCRIPT,
            "-ProcessName", "explorer",
            "-WindowClass", "Shell_TrayWnd",
            "-CapturePng", base,
            "-Method", "printwindow",
        ]
        if frame_count > 1:
            cmd += ["-FrameCount", str(frame_count), "-FrameIntervalMs", "10"]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        parsed: list[dict] = []
        for raw in completed.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                parsed.append(value)
        paths = [base]
        stem, ext = os.path.splitext(base)
        paths += [f"{stem}_{i}{ext}" for i in range(1, frame_count)]
        detail = f"stdout={completed.stdout!r} stderr={completed.stderr!r} rc={completed.returncode}"
        return parsed, paths, detail

    @slow_test
    def test_selftest_emits_pure_ascii_json_bytes_with_original_text(self) -> None:
        """P1 regression with real bytes: the reader decodes stdout as cp1252
        (the host locale), where raw UTF-8 for "Á" raises and loses the
        capture. -SelfTest now emits its non-ASCII sample through the same
        ASCII-escaping emission path as Emit, so the raw stdout bytes must be
        pure ASCII and json.loads must recover the original text."""
        if shutil.which("powershell") is None:
            self.skipTest("powershell not available on this host")
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", mcp_capture.GRAB_SCRIPT, "-SelfTest",
            ],
            capture_output=True,
            timeout=120,
        )
        detail = f"stdout={completed.stdout!r} stderr={completed.stderr!r} rc={completed.returncode}"
        self.assertEqual(0, completed.returncode, detail)
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(1, len(lines), detail)
        self.assertTrue(all(byte < 128 for byte in completed.stdout), detail)
        payload = json.loads(lines[-1].decode("cp1252"))
        self.assertIs(payload.get("ok"), True)
        self.assertEqual("Á ñ", payload.get("sampleTitle"))
        self.assertIs(payload.get("framePathBracesOk"), True)
        self.assertEqual(r"C:\Temp\literal{0}\frame_3.png", payload.get("framePathBraces"))

    @slow_test
    def test_real_script_emits_result_line_per_frame(self) -> None:
        if shutil.which("powershell") is None:
            self.skipTest("powershell not available on this host")
        for frame_count in (1, 3):
            with self.subTest(frame_count=frame_count), tempfile.TemporaryDirectory() as tmp:
                parsed, paths, detail = self._run_real_grab(frame_count, tmp)
                started = [p for p in parsed if p.get("phase") == "started"]
                results = [p for p in parsed if p.get("phase") != "started"]
                if results and all(
                    str(r.get("error", "")).startswith("window_not_found") for r in results
                ):
                    self.skipTest(
                        "no visible Shell_TrayWnd window (desktop-less CI): " + detail
                    )
                self.assertEqual(1, len(started), detail)
                self.assertEqual(frame_count, len(results), detail)
                for index, result in enumerate(results):
                    self.assertTrue(result.get("ok"), detail)
                    if frame_count > 1:
                        self.assertEqual(index, result.get("frameIndex"), detail)
                    else:
                        self.assertNotIn("frameIndex", result, detail)
                    path = paths[index]
                    self.assertTrue(os.path.exists(path), f"{path} missing: {detail}")
                    with open(path, "rb") as handle:
                        digest = hashlib.sha256(handle.read()).hexdigest()
                    self.assertEqual(digest.upper(), str(result.get("sha256", "")).upper(), detail)


if __name__ == "__main__":
    unittest.main()
