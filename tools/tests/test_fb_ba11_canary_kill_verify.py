"""Phase-after kill check in the fence canary polls until the intruder is gone.

Ficha fb-20260915-014753-ba11: one wmic snapshot immediately after taskkill
reported the intruder still running, and the pid was gone twenty seconds later.
These tests load reviews/2026-08-19-lane-fence/canario/canary_fence.py by path
(it is not a package) and patch subprocess.run and time.sleep. No wmic, no
sleep, no process.
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


class CanaryKillVerifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.canary = _load_canary()

    def _run_after(self, evidence: str, on_wmic):
        canary = self.canary
        commands: list[str] = []

        def fake_run(cmd, *args, **kwargs):
            exe = cmd[0]
            commands.append(exe)
            if exe == "taskkill":
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if exe == "wmic":
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
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch.object(canary, "capture", return_value={"patched": True}), \
                mock.patch.object(canary, "daemon_status", return_value={"fence": {}}), \
                mock.patch.object(canary.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(canary.time, "sleep") as sleep:
            rc = canary.main()
        self.assertEqual(rc, 0)
        written = json.loads(
            (Path(evidence) / "canary_after.json").read_text(encoding="utf-8")
        )
        self.assertEqual(commands[0], "taskkill")
        self.assertEqual(commands.count("taskkill"), 1)
        self.assertTrue(all(name == "wmic" for name in commands[1:]))
        return written, sleep, commands

    def test_window_is_fifteen_seconds_stepped_once_a_second(self) -> None:
        self.assertEqual(self.canary.KILL_VERIFY_WINDOW_S, 15)
        self.assertEqual(self.canary.KILL_VERIFY_INTERVAL_S, 1)

    def test_gone_on_third_query_is_verified_after_three_queries(self) -> None:
        queries = {"n": 0}
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                queries["n"] += 1
                return _completed(_listing(evidence, running=queries["n"] < 3))

            written, sleep, commands = self._run_after(evidence, on_wmic)
        self.assertIs(written["killed_verified"], True)
        self.assertEqual(written["kill_verify_queries"], 3)
        self.assertEqual(
            written["kill_verify_waited_s"],
            2 * self.canary.KILL_VERIFY_INTERVAL_S,
        )
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(commands.count("wmic"), 3)

    def test_still_running_through_the_window_is_not_verified(self) -> None:
        with tempfile.TemporaryDirectory() as evidence:
            def on_wmic():
                return _completed(_listing(evidence, running=True))

            written, sleep, commands = self._run_after(evidence, on_wmic)
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_waited_s"], window)
        self.assertEqual(written["kill_verify_queries"], window // interval + 1)
        slept = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(slept, [interval] * (window // interval))
        self.assertEqual(commands.count("wmic"), window // interval + 1)

    def test_query_raising_every_time_is_not_verified(self) -> None:
        def on_wmic():
            return OSError("wmic failed")

        with tempfile.TemporaryDirectory() as evidence:
            written, sleep, commands = self._run_after(evidence, on_wmic)
        window = self.canary.KILL_VERIFY_WINDOW_S
        interval = self.canary.KILL_VERIFY_INTERVAL_S
        self.assertIs(written["killed_verified"], False)
        self.assertEqual(written["kill_verify_waited_s"], window)
        self.assertEqual(written["kill_verify_queries"], window // interval + 1)
        self.assertEqual(sleep.call_count, window // interval)
        self.assertEqual(commands.count("wmic"), window // interval + 1)

    def test_docstring_says_not_to_reschedule_on_one_steam_account(self) -> None:
        doc = self.canary.__doc__ or ""
        self.assertIn("0x000400B3", doc)
        self.assertIn("e4cf", doc)
        self.assertIn("Steam", doc)


if __name__ == "__main__":
    unittest.main()
