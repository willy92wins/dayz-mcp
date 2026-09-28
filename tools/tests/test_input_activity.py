"""250f, plan v2.1 §3.1: the input signal, without a desktop.

Every Win32 read is a fake, so these tests never depend on who is at the
machine. The attribution rule and its declared residue (C1) are pinned here.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon
from dayz_mcp.input_activity import (
    InputAttributor,
    InputSample,
    Win32InputFns,
    take_sample,
    tick_advanced,
)


DAYZ_PID = 4242
OTHER_PID = 777


def _fns(last=1000, tick=1500, pid=DAYZ_PID) -> Win32InputFns:
    return Win32InputFns(
        last_input_tick=lambda: last,
        tick_count=lambda: tick,
        foreground_pid=lambda: pid,
    )


def _sample(at: float, last_tick: int, pid: int | None, *, idle_ms: int = 0) -> InputSample:
    return InputSample(
        at=at, ok=True, last_input_tick=last_tick, idle_ms=idle_ms, foreground_pid=pid
    )


def _owner(pid: int) -> tuple[str, bool] | None:
    return ("run-1", True) if pid == DAYZ_PID else None


def _runs(attributions) -> list[str]:
    return [run_id for run_id, _epoch, _verified in attributions]


class TakeSampleTest(unittest.TestCase):
    def test_reads_idle_ms_and_foreground_pid(self) -> None:
        sample = take_sample(_fns(last=1000, tick=1500), 50.0)
        self.assertTrue(sample.ok)
        self.assertEqual(sample.idle_ms, 500)
        self.assertEqual(sample.foreground_pid, DAYZ_PID)
        self.assertAlmostEqual(sample.last_input_epoch, 49.5)

    def test_tick_wraparound_keeps_a_small_idle(self) -> None:
        sample = take_sample(_fns(last=0xFFFFFF00, tick=0x00000010), 10.0)
        self.assertTrue(sample.ok)
        self.assertEqual(sample.idle_ms, 0x110)

    def test_last_input_in_the_future_is_not_ok(self) -> None:
        self.assertFalse(take_sample(_fns(last=2000, tick=1000), 1.0).ok)

    def test_failed_read_is_not_ok(self) -> None:
        self.assertFalse(take_sample(_fns(last=None), 1.0).ok)
        broken = Win32InputFns(
            last_input_tick=lambda: (_ for _ in ()).throw(OSError("user32")),
            tick_count=lambda: 1,
            foreground_pid=lambda: 1,
        )
        self.assertFalse(take_sample(broken, 1.0).ok)

    def test_no_foreground_window_is_ok_without_pid(self) -> None:
        sample = take_sample(_fns(pid=None), 1.0)
        self.assertTrue(sample.ok)
        self.assertIsNone(sample.foreground_pid)

    def test_a_foreground_owner_that_cannot_be_read_is_not_ok(self) -> None:
        # Review #125 F3: the real binding raises when the window exists but
        # its owner cannot be read; that must never pass for "no run in front".
        def unreadable() -> int | None:
            raise OSError(5, "GetWindowThreadProcessId failed")

        fns = Win32InputFns(
            last_input_tick=lambda: 1000,
            tick_count=lambda: 1500,
            foreground_pid=unreadable,
        )
        self.assertFalse(take_sample(fns, 1.0).ok)


class TickAdvancedTest(unittest.TestCase):
    def test_forward_equal_backward_and_wrap(self) -> None:
        self.assertTrue(tick_advanced(10, 11))
        self.assertFalse(tick_advanced(10, 10))
        self.assertFalse(tick_advanced(11, 10))
        self.assertTrue(tick_advanced(0xFFFFFFFF, 0))


class InputAttributorTest(unittest.TestCase):
    def test_first_sample_attributes_nothing(self) -> None:
        self.assertEqual(InputAttributor().observe(_sample(1.0, 100, DAYZ_PID), _owner), [])

    def test_new_input_with_the_run_in_front_is_counted(self) -> None:
        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, DAYZ_PID), _owner)
        [(run_id, epoch, verified)] = attributor.observe(
            _sample(1.25, 180, DAYZ_PID, idle_ms=50), _owner
        )
        self.assertEqual((run_id, verified), ("run-1", True))
        self.assertAlmostEqual(epoch, 1.2)

    def test_focus_leaving_the_run_between_samples_still_counts(self) -> None:
        # DayZ had the focus at the previous sample, then input, then another
        # window: a doubt counts, it can only delay a cut.
        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, DAYZ_PID), _owner)
        self.assertEqual(_runs(attributor.observe(_sample(1.25, 180, OTHER_PID), _owner)), ["run-1"])

    def test_focus_reaching_the_run_between_samples_counts(self) -> None:
        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, OTHER_PID), _owner)
        self.assertEqual(_runs(attributor.observe(_sample(1.25, 180, DAYZ_PID), _owner)), ["run-1"])

    def test_focus_moving_between_two_runs_credits_both(self) -> None:
        # Review #125 F5: the input may belong to either run.
        def owner(pid: int) -> tuple[str, bool] | None:
            return {DAYZ_PID: ("run-1", True), OTHER_PID: ("run-2", True)}.get(pid)

        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, OTHER_PID), owner)
        self.assertEqual(
            sorted(_runs(attributor.observe(_sample(1.25, 180, DAYZ_PID), owner))),
            ["run-1", "run-2"],
        )

    def test_an_unverified_identity_comes_back_as_a_doubt(self) -> None:
        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, DAYZ_PID), lambda pid: ("run-1", False))
        self.assertEqual(
            [(run_id, verified) for run_id, _epoch, verified in attributor.observe(
                _sample(1.25, 180, DAYZ_PID), lambda pid: ("run-1", False)
            )],
            [("run-1", False)],
        )

    def test_input_in_another_window_is_not_counted(self) -> None:
        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, OTHER_PID), _owner)
        self.assertEqual(attributor.observe(_sample(1.25, 180, OTHER_PID), _owner), [])

    def test_declared_residue_c1_full_excursion_between_samples(self) -> None:
        # Plan v2.1 §3.1, residue C1: DayZ gains the focus, gets input and loses
        # it between two samples that both show another window. Not counted.
        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, OTHER_PID), _owner)
        self.assertEqual(attributor.observe(_sample(1.25, 180, OTHER_PID), _owner), [])

    def test_the_run_in_front_without_input_is_not_counted(self) -> None:
        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, DAYZ_PID), _owner)
        self.assertEqual(attributor.observe(_sample(1.25, 100, DAYZ_PID), _owner), [])

    def test_a_failed_sample_is_bridged_to_the_last_good_one(self) -> None:
        # Review #125 F4: the input after a failed sample is compared with the
        # last good sample, which had DayZ in front.
        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, DAYZ_PID), _owner)
        self.assertEqual(attributor.observe(InputSample(at=1.25, ok=False), _owner), [])
        self.assertEqual(_runs(attributor.observe(_sample(1.5, 300, OTHER_PID), _owner)), ["run-1"])

    def test_a_gap_longer_than_the_bridge_attributes_nothing(self) -> None:
        # The lifecycle restarts the clocks from the recovery instead.
        attributor = InputAttributor(bridge_s=2.0)
        attributor.observe(_sample(1.0, 100, DAYZ_PID), _owner)
        self.assertEqual(attributor.observe(_sample(3.5, 300, DAYZ_PID), _owner), [])
        self.assertEqual(_runs(attributor.observe(_sample(3.75, 400, DAYZ_PID), _owner)), ["run-1"])

    def test_owner_is_only_asked_when_there_is_new_input(self) -> None:
        asked: list[int] = []

        def owner(pid: int) -> tuple[str, bool] | None:
            asked.append(pid)
            return None

        attributor = InputAttributor()
        attributor.observe(_sample(1.0, 100, DAYZ_PID), owner)
        attributor.observe(_sample(1.25, 100, DAYZ_PID), owner)
        self.assertEqual(asked, [])


class _RecordingLifecycle:
    def __init__(self, stop: threading.Event, after: int) -> None:
        self.samples: list[InputSample] = []
        self._stop = stop
        self._after = after

    def record_input_sample(self, sample: InputSample) -> None:
        self.samples.append(sample)
        if len(self.samples) >= self._after:
            self._stop.set()


class InstallInputSamplerTest(unittest.TestCase):
    def test_samples_until_stopped(self) -> None:
        stop = threading.Event()
        lifecycle = _RecordingLifecycle(stop, after=3)
        thread = daemon.install_input_sampler(
            lifecycle, _fns(), interval_s=0.001, stop=stop, clock=lambda: 5.0
        )
        self.assertIsNotNone(thread)
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(len(lifecycle.samples), 3)
        self.assertTrue(all(sample.ok for sample in lifecycle.samples))

    def test_without_win32_reads_nothing_is_armed(self) -> None:
        logged: list[str] = []
        self.assertIsNone(
            daemon.install_input_sampler(object(), None, log=logged.append)
        )
        self.assertEqual(len(logged), 1)

    def test_a_failing_lifecycle_does_not_kill_the_sampler(self) -> None:
        stop = threading.Event()
        calls: list[int] = []

        class Failing:
            def record_input_sample(self, sample: InputSample) -> None:
                calls.append(1)
                if len(calls) >= 3:
                    stop.set()
                raise RuntimeError("boom")

        logged: list[str] = []
        thread = daemon.install_input_sampler(
            Failing(), _fns(), interval_s=0.001, stop=stop, log=logged.append
        )
        thread.join(timeout=5.0)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(len(logged), 1)


if __name__ == "__main__":
    unittest.main()
