"""Pre-run desktop unlock + brightness gate (fb-20260918-134756-05a0 / c0e5)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, patch

from PIL import Image

import mcp_capture
from dayz_mcp import dayz_test_tool
from dayz_mcp import server
from dayz_mcp import steam_preflight
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)


def _bright(**_kwargs: object) -> dict[str, object]:
    return {"ok": True, "mean_brightness": 80.0, "nonblack_ratio": 0.9}


def _black(**_kwargs: object) -> dict[str, object]:
    return {"ok": True, "mean_brightness": 0.0, "nonblack_ratio": 0.0}


def _ok_desktop() -> mcp_capture.PrerunDesktopResult:
    return mcp_capture.PrerunDesktopResult(
        error_code=None,
        desktop="unlocked",
        mean_brightness=80.0,
        nonblack_ratio=0.9,
        waited_s=0.01,
        remediation="",
    )


def _locked_desktop() -> mcp_capture.PrerunDesktopResult:
    return mcp_capture.PrerunDesktopResult(
        error_code=mcp_capture.SESSION_LOCKED,
        desktop="locked",
        mean_brightness=None,
        nonblack_ratio=None,
        waited_s=30.0,
        remediation=mcp_capture.REMEDIATION_SESSION_LOCKED,
    )


def _failed_desktop() -> mcp_capture.PrerunDesktopResult:
    return mcp_capture.PrerunDesktopResult(
        error_code=mcp_capture.DESKTOP_PROBE_FAILED,
        desktop="unlocked",
        mean_brightness=None,
        nonblack_ratio=None,
        waited_s=30.0,
        remediation=mcp_capture.REMEDIATION_DESKTOP_PROBE_FAILED,
    )


class PrerunDesktopGateTest(unittest.TestCase):
    def test_unlocked_and_bright_passes_without_sleep(self) -> None:
        sleeps: list[float] = []
        result = mcp_capture.run_prerun_desktop_gate(
            timeout_s=30,
            probe_desktop=lambda: "unlocked",
            probe_brightness=_bright,
            sleeper=sleeps.append,
            clock=lambda: 0.0,
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.desktop, "unlocked")
        self.assertEqual(result.mean_brightness, 80.0)
        self.assertEqual(sleeps, [])

    def test_locked_waits_then_aborts_session_locked(self) -> None:
        now = {"t": 0.0}

        def clock() -> float:
            return now["t"]

        def sleeper(delta: float) -> None:
            now["t"] = now["t"] + delta

        brightness = mock.Mock(side_effect=_bright)
        result = mcp_capture.run_prerun_desktop_gate(
            timeout_s=30,
            poll_s=10,
            probe_desktop=lambda: "locked",
            probe_brightness=brightness,
            sleeper=sleeper,
            clock=clock,
        )
        self.assertEqual(result.error_code, "session_locked")
        self.assertIn("Unlock", result.remediation)
        self.assertIn("frame_client_all_black", result.remediation)
        self.assertGreaterEqual(result.waited_s, 30.0)
        brightness.assert_not_called()

    def test_black_waits_then_aborts_desktop_all_black(self) -> None:
        now = {"t": 0.0}

        def clock() -> float:
            return now["t"]

        def sleeper(delta: float) -> None:
            now["t"] = now["t"] + delta

        result = mcp_capture.run_prerun_desktop_gate(
            timeout_s=30,
            poll_s=10,
            probe_desktop=lambda: "unlocked",
            probe_brightness=_black,
            sleeper=sleeper,
            clock=clock,
        )
        self.assertEqual(result.error_code, "desktop_all_black")
        self.assertIn("all-black", result.remediation)
        self.assertIn("frame_client_all_black", result.remediation)
        self.assertEqual(result.mean_brightness, 0.0)
        self.assertGreaterEqual(result.waited_s, 30.0)

    def test_locked_then_unlocks_and_bright_passes(self) -> None:
        now = {"t": 0.0}
        desktops = ["locked", "locked", "unlocked"]

        def clock() -> float:
            return now["t"]

        def sleeper(delta: float) -> None:
            now["t"] = now["t"] + delta

        result = mcp_capture.run_prerun_desktop_gate(
            timeout_s=30,
            poll_s=5,
            probe_desktop=lambda: desktops.pop(0) if desktops else "unlocked",
            probe_brightness=_bright,
            sleeper=sleeper,
            clock=clock,
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.desktop, "unlocked")

    def test_black_then_bright_passes(self) -> None:
        now = {"t": 0.0}
        probes = [_black, _bright]

        def clock() -> float:
            return now["t"]

        def sleeper(delta: float) -> None:
            now["t"] = now["t"] + delta

        result = mcp_capture.run_prerun_desktop_gate(
            timeout_s=30,
            poll_s=5,
            probe_desktop=lambda: "unlocked",
            probe_brightness=lambda **_kwargs: probes.pop(0)(),
            sleeper=sleeper,
            clock=clock,
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.mean_brightness, 80.0)

    def test_unsupported_probe_does_not_block(self) -> None:
        sleeps: list[float] = []
        result = mcp_capture.run_prerun_desktop_gate(
            probe_desktop=lambda: "unknown",
            probe_brightness=lambda **_kwargs: {
                "ok": False,
                "error": "desktop_probe_unsupported",
            },
            sleeper=sleeps.append,
            clock=lambda: 0.0,
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(sleeps, [])

    def test_probe_failed_retries_then_fail_closed(self) -> None:
        now = {"t": 0.0}

        def clock() -> float:
            return now["t"]

        def sleeper(delta: float) -> None:
            now["t"] = now["t"] + delta

        result = mcp_capture.run_prerun_desktop_gate(
            timeout_s=30,
            poll_s=10,
            probe_desktop=lambda: "unlocked",
            probe_brightness=lambda **_kwargs: {
                "ok": False,
                "error": "desktop_probe_failed",
            },
            sleeper=sleeper,
            clock=clock,
        )
        self.assertEqual(result.error_code, "desktop_probe_failed")
        self.assertIn("refused", result.remediation)
        self.assertIn("frame_client_all_black", result.remediation)
        self.assertGreaterEqual(result.waited_s, 30.0)

    def test_wait_false_probe_failed_fail_closed(self) -> None:
        sleeps: list[float] = []
        result = mcp_capture.run_prerun_desktop_gate(
            wait=False,
            probe_desktop=lambda: "unlocked",
            probe_brightness=lambda **_kwargs: {
                "ok": False,
                "error": "desktop_probe_failed",
            },
            sleeper=sleeps.append,
            clock=lambda: 0.0,
        )
        self.assertEqual(result.error_code, "desktop_probe_failed")
        self.assertEqual(sleeps, [])

    def test_bright_probe_after_deadline_is_rejected(self) -> None:
        now = {"t": 0.0}

        def clock() -> float:
            return now["t"]

        def bright_late(**_kwargs: object) -> dict[str, object]:
            now["t"] = now["t"] + 0.10
            return _bright()

        result = mcp_capture.run_prerun_desktop_gate(
            timeout_s=0.05,
            poll_s=1.0,
            probe_desktop=lambda: "unlocked",
            probe_brightness=bright_late,
            sleeper=lambda _delta: None,
            clock=clock,
        )
        self.assertEqual(result.error_code, "desktop_probe_timeout")
        self.assertIsNotNone(result.mean_brightness)
        self.assertGreaterEqual(result.waited_s, 0.05)

    def test_wait_false_aborts_immediately_when_locked(self) -> None:
        sleeps: list[float] = []
        result = mcp_capture.run_prerun_desktop_gate(
            wait=False,
            probe_desktop=lambda: "locked",
            probe_brightness=_bright,
            sleeper=sleeps.append,
            clock=lambda: 0.0,
        )
        self.assertEqual(result.error_code, "session_locked")
        self.assertEqual(sleeps, [])

    def test_brightness_timeout_aborts_with_token(self) -> None:
        now = {"t": 0.0}

        def clock() -> float:
            return now["t"]

        def sleeper(delta: float) -> None:
            now["t"] = now["t"] + delta

        result = mcp_capture.run_prerun_desktop_gate(
            timeout_s=30,
            poll_s=10,
            probe_desktop=lambda: "unlocked",
            probe_brightness=lambda **_kwargs: {
                "ok": False,
                "error": "desktop_probe_timeout",
            },
            sleeper=sleeper,
            clock=clock,
        )
        self.assertEqual(result.error_code, "desktop_probe_timeout")
        self.assertIn("did not finish", result.remediation)
        self.assertGreaterEqual(result.waited_s, 30.0)

    def test_probe_desktop_brightness_off_windows_is_unsupported(self) -> None:
        with mock.patch.object(mcp_capture.sys, "platform", "linux"):
            result = mcp_capture.probe_desktop_brightness()
        self.assertEqual(
            {"ok": False, "error": "desktop_probe_unsupported"}, result
        )

    def test_probe_desktop_brightness_reads_imagegrab(self) -> None:
        image = Image.new("RGB", (64, 64), (80, 80, 80))
        with mock.patch.object(mcp_capture.sys, "platform", "win32"):
            with mock.patch("PIL.ImageGrab.grab", return_value=image):
                result = mcp_capture.probe_desktop_brightness()
        self.assertTrue(result.get("ok"))
        self.assertGreater(float(result["mean_brightness"]), 1.0)
        self.assertGreater(float(result["nonblack_ratio"]), 0.01)

    def test_probe_desktop_brightness_timeout_does_not_wait_for_slow_worker(self) -> None:
        def slow_grab() -> Image.Image:
            time.sleep(0.25)
            return Image.new("RGB", (16, 16), (80, 80, 80))

        with mock.patch.object(mcp_capture.sys, "platform", "win32"):
            with mock.patch("PIL.ImageGrab.grab", side_effect=slow_grab):
                started = time.monotonic()
                result = mcp_capture.probe_desktop_brightness(timeout_s=0.05)
                elapsed = time.monotonic() - started
        self.assertEqual(result, {"ok": False, "error": "desktop_probe_timeout"})
        self.assertLess(elapsed, 0.18)

    def test_probe_desktop_brightness_hung_worker_returns_at_timeout(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def hung_grab() -> Image.Image:
            started.set()
            release.wait(10.0)
            return Image.new("RGB", (16, 16), (80, 80, 80))

        with mock.patch.object(mcp_capture.sys, "platform", "win32"):
            with mock.patch("PIL.ImageGrab.grab", side_effect=hung_grab):
                t0 = time.monotonic()
                result = mcp_capture.probe_desktop_brightness(timeout_s=0.08)
                elapsed = time.monotonic() - t0
        release.set()
        self.assertTrue(started.wait(1.0))
        self.assertEqual(result, {"ok": False, "error": "desktop_probe_timeout"})
        self.assertLess(elapsed, 0.4)
        self.assertGreaterEqual(elapsed, 0.05)

    def test_probe_desktop_brightness_grab_exception_is_failed(self) -> None:
        with mock.patch.object(mcp_capture.sys, "platform", "win32"):
            with mock.patch("PIL.ImageGrab.grab", side_effect=OSError("boom")):
                result = mcp_capture.probe_desktop_brightness()
        self.assertEqual(result, {"ok": False, "error": "desktop_probe_failed"})


class PrerunDesktopDayzTestRunTest(unittest.IsolatedAsyncioTestCase):
    async def test_locked_desktop_refuses_before_launch(self) -> None:
        policy = _policy()
        launch = AsyncMock()
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_prerun_desktop", return_value=_locked_desktop()
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["run_id"])
        self.assertEqual(result["phase"], "validating")
        self.assertEqual(result["error_code"], "session_locked")
        self.assertIn("Unlock", str(result["remediation"]))

    async def test_locked_desktop_refuses_preflight_before_launch(self) -> None:
        policy = _policy()
        launch = AsyncMock()
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_prerun_desktop", return_value=_locked_desktop()
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                preflight=True,
                extra_mods=["@DayZ_MCP"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["error_code"], "session_locked")
        self.assertEqual(result.get("preflight_skipped_checks"), ["steam_session"])

    async def test_server_mode_does_not_consult_desktop_gate(self) -> None:
        policy = _policy()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        desktop = mock.Mock(return_value=_locked_desktop())
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_prerun_desktop", desktop
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="server",
                extra_mods=["@DayZ_MCP"],
            )

        desktop.assert_not_called()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["run_id"], RUN_ID)

    async def test_bright_desktop_reaches_launcher(self) -> None:
        policy = _policy()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_prerun_desktop", return_value=_ok_desktop()
        ), patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            return_value=steam_preflight.SteamSessionResult(
                error_code=None,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation="ok",
            ),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["run_id"], RUN_ID)

    async def test_windows_probe_failed_refuses_before_launch(self) -> None:
        policy = _policy()
        launch = AsyncMock()
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_prerun_desktop", return_value=_failed_desktop()
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["run_id"])
        self.assertEqual(result["phase"], "validating")
        self.assertEqual(result["error_code"], "desktop_probe_failed")
        self.assertIn("refused", str(result["remediation"]))

    async def test_heartbeat_progresses_during_gate_wait(self) -> None:
        ticks = {"n": 0}

        async def heartbeat() -> None:
            while True:
                ticks["n"] = ticks["n"] + 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(heartbeat())
        try:
            result = await asyncio.to_thread(
                mcp_capture.run_prerun_desktop_gate,
                timeout_s=0.12,
                poll_s=0.04,
                probe_desktop=lambda: "locked",
                probe_brightness=_bright,
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.assertEqual(result.error_code, "session_locked")
        self.assertGreaterEqual(ticks["n"], 3)

    async def test_execute_path_keeps_event_loop_alive_during_gate(self) -> None:
        def blocking_gate(*_args: object, **_kwargs: object) -> mcp_capture.PrerunDesktopResult:
            time.sleep(0.12)
            return _locked_desktop()

        ticks = {"n": 0}

        async def heartbeat() -> None:
            while True:
                ticks["n"] = ticks["n"] + 1
                await asyncio.sleep(0.01)

        policy = _policy()
        launch = AsyncMock()
        task = asyncio.create_task(heartbeat())
        try:
            with patch.object(
                dayz_test_tool, "open_approved_launcher", return_value=_Opened()
            ), patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                return_value=_Bundle(_sealed(policy)),
            ), patch.object(
                dayz_test_tool.secure_launcher,
                "execute_secure_launcher_request",
                new=launch,
            ), patch.object(
                dayz_test_tool, "evaluate_prerun_desktop", side_effect=blocking_gate
            ):
                result = await dayz_test_tool.execute_dayz_test_run(
                    _Runtime(),
                    project="ExampleMod",
                    mode="all",
                    extra_mods=["@DayZ_MCP"],
                )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        launch.assert_not_awaited()
        self.assertEqual(result["error_code"], "session_locked")
        self.assertGreaterEqual(ticks["n"], 3)


class PrerunDesktopDescriptionTest(unittest.TestCase):
    def test_readme_names_capture_tandem_step_zero(self) -> None:
        readme = Path(server.__file__).resolve().parents[1] / "README-mcp.md"
        text = readme.read_text(encoding="utf-8")
        self.assertIn("### Capture tandems (step 0)", text)
        self.assertIn("session_locked", text)
        self.assertIn("desktop_all_black", text)
        self.assertIn("desktop_probe_timeout", text)
        self.assertIn("desktop_probe_failed", text)
        self.assertIn("desktop_probe_unsupported", text)
        self.assertIn("frame_client_all_black", text)

    def test_capture_screenshot_description_names_the_prerun_gate(self) -> None:
        text = Path(server.__file__).read_text(encoding="utf-8")
        start = text.index("Capture a screenshot from the DayZDiag window.")
        end = text.index("async def capture_screenshot")
        description = text[start:end]
        self.assertIn("dayz_test_run waits up to 30 s", description)
        self.assertIn("desktop_all_black", description)
        self.assertIn("desktop_probe_timeout", description)
        self.assertIn("desktop_probe_failed", description)
        self.assertIn("frame_client_all_black", description)


if __name__ == "__main__":
    unittest.main()
