"""Phase-after kill check in the fence canary polls until the intruder is gone.

Ficha fb-20260915-014753-ba11: one wmic snapshot immediately after taskkill
reported the intruder still running, and the pid was gone twenty seconds later.
These tests load reviews/2026-08-19-lane-fence/canario/canary_fence.py by path
(it is not a package) and patch subprocess.run, time.sleep and time.monotonic.
No wmic, no sleep, no process.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class _PollDidNotStop(BaseException):
    """The fake wmic was called past its bound, so the poll did not stop.

    BaseException on purpose: _intruder_still_running catches Exception and
    would count an AssertionError as "still running", so a poll without an end
    would hang the test instead of failing it (R2 mutants M5 and M6).
    """


_CANARY_PATH = (
    Path(__file__).resolve().parents[2]
    / "reviews"
    / "2026-08-19-lane-fence"
    / "canario"
    / "canary_fence.py"
)


def _load_canary():
    """Execute the review script from this worktree.

    Import inserts a hardcoded tools directory at canary_fence.py (sys.path),
    so the previous path is restored before the rest of the suite continues.
    """
    spec = importlib.util.spec_from_file_location(
        "canary_fence_ba11_under_test", _CANARY_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load %s" % _CANARY_PATH)
    module = importlib.util.module_from_spec(spec)
    saved_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved_path
    return module


def _listing(evidence: str, *, running: bool) -> str:
    if not running:
        return 'CommandLine\n"DayZDiag_x64.exe" -profiles=C:\\registered\\client\n'
    marker = os.path.join(evidence, "intruder_profiles")
    return 'CommandLine\n"DayZDiag_x64.exe" -profiles=%s\n' % marker


def _completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["wmic"], returncode=0, stdout=stdout, stderr=""
    )


# Measured by the R1 wmic probe: a bad query and a real "none" share a blank
# stdout. Only rc and stderr tell them apart. rc 2147749911, stdout newlines.
_WMIC_FAILED = subprocess.CompletedProcess(
    args=["wmic"],
    returncode=2147749911,
    stdout="\n\n\n\n",
    stderr="Node - WILLY\n\nERROR:\n\nDescription = Consulta no valida\n\n",
)
_WMIC_NONE = subprocess.CompletedProcess(
    args=["wmic"],
    returncode=0,
    stdout="\n\n\n\n",
    stderr="No Instance(s) Available.\n\n",
)


class _Clock:
    """Fake monotonic clock. Sleep advances it; wmic time must advance it too."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


class CanaryKillVerifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.canary = _load_canary()

    def _run_after(self, evidence: str, on_wmic, clock: _Clock | None = None):
        canary = self.canary
        if clock is None:
            clock = _Clock()
        commands: list[str] = []

        def fake_run(cmd, *args, **kwargs):
            exe = cmd[0]
            commands.append(exe)
            if exe == "taskkill":
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if exe == "wmic":
                if commands.count("wmic") > 20:
                    raise _PollDidNotStop("poll did not stop")
                outcome = on_wmic()
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
            raise AssertionError("unexpected subprocess.run: %r" % (cmd,))

        argv = [
            "canary_fence.py",
            "--phase", "after",
            "--client-profiles", os.path.join(evidence, "client_profiles"),
            "--evidence", evidence,
            "--port", "9",
            "--key", "not-a-real-key",
            "--intruder-pid", "33788",
        ]
        sleep = mock.Mock(side_effect=clock.sleep)
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch.object(canary, "capture", return_value={"patched": True}), \
                mock.patch.object(canary, "daemon_status", return_value={"fence": {}}), \
                mock.patch.object(canary.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(canary.time, "sleep", sleep), \
                mock.patch.object(canary.time, "monotonic", clock.monotonic):
            rc = canary.main()
        self.assertEqual(rc, 0)
        written = json.loads(
            (Path(evidence) / "canary_after.json").read_text(encoding="utf-8")
        )
        self.assertEqual(commands[0], "taskkill")
        self.assertEqual(commands.count("taskkill"), 1)
        self.assertTrue(all(name == "wmic" for name in commands[1:]))
        return written, sleep, commands, clock

    def test_window_is_fifteen_seconds_stepped_once_a_second(self) -> None:
        self.assertEqual(self.canary.KILL_VERIFY_WINDOW_S, 15)
        self.assertEqual(self.canary.KILL_VERIFY_INTERVAL_S, 1)

    def test_gone_on_third_query_is_verified_after_three_queries(self) -> None:
        queries = {"n": 0}
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                queries["n"] += 1
                return _completed(_listing(evidence, running=queries["n"] < 3))

            written, sleep, commands, clock = self._run_after(evidence, on_wmic)
        self.assertIs(written["killed_verified"], True)
        self.assertEqual(written["kill_verify_queries"], 3)
        self.assertEqual(
            written["kill_verify_waited_s"],
            2 * self.canary.KILL_VERIFY_INTERVAL_S,
        )
        self.assertEqual(written["kill_verify_elapsed_s"], clock.now)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(commands.count("wmic"), 3)

    def test_still_running_through_the_window_is_not_verified(self) -> None:
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                return _completed(_listing(evidence, running=True))

            written, sleep, commands, clock = self._run_after(evidence, on_wmic)
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_waited_s"], window)
        self.assertEqual(written["kill_verify_queries"], window // interval + 1)
        self.assertEqual(written["kill_verify_elapsed_s"], clock.now)
        self.assertEqual(written["kill_verify_elapsed_s"], window)
        slept = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(slept, [interval] * (window // interval))
        self.assertEqual(commands.count("wmic"), window // interval + 1)

    def test_query_raising_every_time_is_not_verified(self) -> None:
        def on_wmic():
            return OSError("wmic failed")

        with tempfile.TemporaryDirectory() as evidence:
            written, sleep, commands, clock = self._run_after(evidence, on_wmic)
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_waited_s"], window)
        self.assertEqual(written["kill_verify_queries"], window // interval + 1)
        self.assertEqual(written["kill_verify_elapsed_s"], clock.now)
        self.assertEqual(sleep.call_count, window // interval)
        self.assertEqual(commands.count("wmic"), window // interval + 1)

    def test_one_nonzero_wmic_while_intruder_alive_is_not_verified(self) -> None:
        # Reviewer S2: the intruder is alive, and query 5 is the measured
        # failure (rc 2147749911, blank stdout, error on stderr).
        queries = {"n": 0}
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                queries["n"] += 1
                if queries["n"] == 5:
                    return _WMIC_FAILED
                return _completed(_listing(evidence, running=True))

            written, sleep, commands, clock = self._run_after(evidence, on_wmic)
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_queries"], window // interval + 1)
        self.assertGreater(written["kill_verify_queries"], 5)
        self.assertEqual(written["kill_verify_waited_s"], window)
        self.assertEqual(written["kill_verify_elapsed_s"], clock.now)
        self.assertEqual(commands.count("wmic"), window // interval + 1)

    def test_nonzero_wmic_with_header_and_client_only_is_not_verified(self) -> None:
        # Reviewer S7, not measured: the intruder is alive, and query 3 fails
        # (rc != 0) yet prints the CommandLine header with only the registered
        # client. That stdout alone reads as gone; only the rc vetoes it.
        queries = {"n": 0}
        with tempfile.TemporaryDirectory() as evidence:
            client_only = _listing(evidence, running=False)
            failed_with_listing = subprocess.CompletedProcess(
                args=["wmic"],
                returncode=2147749911,
                stdout=client_only,
                stderr="ERROR:\n",
            )
            # Control: the same stdout with rc 0 is a clean kill.
            with mock.patch.object(
                self.canary.subprocess, "run", return_value=_completed(client_only)
            ):
                self.assertIs(self.canary._intruder_still_running(evidence), False)

            def on_wmic():
                queries["n"] += 1
                if queries["n"] == 3:
                    return failed_with_listing
                return _completed(_listing(evidence, running=True))

            written, sleep, commands, clock = self._run_after(evidence, on_wmic)
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_queries"], window // interval + 1)
        self.assertEqual(written["kill_verify_waited_s"], window)
        self.assertEqual(written["kill_verify_elapsed_s"], clock.now)
        self.assertEqual(written["kill_verify_elapsed_s"], window)
        self.assertEqual(commands.count("wmic"), window // interval + 1)

    def test_no_instances_available_with_rc_0_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                return _WMIC_NONE

            written, sleep, commands, clock = self._run_after(evidence, on_wmic)
        self.assertIs(written["killed_verified"], True)
        self.assertEqual(written["kill_verify_queries"], 1)
        self.assertEqual(written["kill_verify_waited_s"], 0)
        self.assertEqual(written["kill_verify_elapsed_s"], 0)
        self.assertEqual(clock.now, 0)
        self.assertEqual(sleep.call_count, 0)
        self.assertEqual(commands.count("wmic"), 1)

    def test_blank_wmic_output_without_positive_evidence_is_not_verified(self) -> None:
        blank = subprocess.CompletedProcess(
            args=["wmic"], returncode=0, stdout="\n\n\n\n", stderr=""
        )
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                return blank

            written, sleep, commands, clock = self._run_after(evidence, on_wmic)
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_queries"], window // interval + 1)
        self.assertEqual(written["kill_verify_elapsed_s"], clock.now)
        self.assertEqual(commands.count("wmic"), window // interval + 1)

    def test_wmic_time_counts_against_the_monotonic_deadline(self) -> None:
        # Two queries of (window - interval) / 2 each, plus one spacing sleep,
        # land on the deadline. waited_s stays the single sleep.
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        query_cost = (window - interval) // 2
        clock = _Clock()
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                clock.now += query_cost
                return _completed(_listing(evidence, running=True))

            written, sleep, commands, same_clock = self._run_after(
                evidence, on_wmic, clock
            )
        self.assertIs(same_clock, clock)
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_queries"], 2)
        self.assertEqual(written["kill_verify_waited_s"], interval)
        self.assertEqual(written["kill_verify_elapsed_s"], window)
        self.assertEqual(clock.now, window)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [interval])
        self.assertEqual(commands.count("wmic"), 2)

    def test_last_sleep_is_shortened_to_end_on_the_deadline(self) -> None:
        # Reviewer S6: each query costs 0.3 s and the intruder stays listed.
        # Query 12 ends at 14.6 s, so the last sleep is cut to 0.4 s, query 13
        # starts on the deadline and the poll ends at 15.3 s. An uncut 1 s
        # sleep would end it at 15.9 s.
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        query_cost = 0.3
        clock = _Clock()
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                clock.now += query_cost
                return _completed(_listing(evidence, running=True))

            written, sleep, commands, same_clock = self._run_after(
                evidence, on_wmic, clock
            )
        self.assertIs(same_clock, clock)
        slept = [call.args[0] for call in sleep.call_args_list]
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_queries"], 13)
        self.assertEqual(commands.count("wmic"), 13)
        self.assertEqual(slept[:-1], [interval] * 11)
        self.assertAlmostEqual(slept[-1], 0.4)
        self.assertAlmostEqual(written["kill_verify_waited_s"], 11.4)
        self.assertAlmostEqual(written["kill_verify_elapsed_s"], window + query_cost)
        self.assertEqual(written["kill_verify_elapsed_s"], clock.now)

    def test_a_wmic_past_the_deadline_does_not_start_another_query(self) -> None:
        clock = _Clock()
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                clock.now += 20
                raise subprocess.TimeoutExpired(cmd=["wmic"], timeout=20)

            written, sleep, commands, same_clock = self._run_after(
                evidence, on_wmic, clock
            )
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_queries"], 1)
        self.assertEqual(written["kill_verify_waited_s"], 0)
        self.assertEqual(written["kill_verify_elapsed_s"], 20)
        self.assertEqual(same_clock.now, 20)
        self.assertEqual(sleep.call_count, 0)
        self.assertEqual(commands.count("wmic"), 1)

    def test_poll_docstring_says_the_window_is_monotonic(self) -> None:
        doc = self.canary._poll_intruder_exit.__doc__ or ""
        self.assertIn("monotonic", doc)
        self.assertIn("KILL_VERIFY_WINDOW_S", doc)

    def test_docstring_says_not_to_reschedule_on_one_steam_account(self) -> None:
        doc = self.canary.__doc__ or ""
        self.assertIn("0x000400B3", doc)
        self.assertIn("e4cf", doc)
        self.assertIn("Steam", doc)


if __name__ == "__main__":
    unittest.main()
