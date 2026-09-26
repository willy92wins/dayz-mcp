from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon


class InstallRunReaperTest(unittest.TestCase):
    """The daemon-side periodic reaper thread (daemon.install_run_reaper): reaps
    first then waits (immediate first pass), stops promptly on the shared event, and
    survives a failing pass. It calls only ProcessLifecycle.reap_dead_runs — never
    terminate — so it is safe by construction."""

    def test_reaps_immediately_and_stops_on_event(self) -> None:
        stop = threading.Event()
        calls: list[int] = []
        logs: list[str] = []

        class FakeLifecycle:
            def reap_dead_runs(self_inner) -> list[str]:
                calls.append(1)
                stop.set()
                return ["run-x"]

        thread = daemon.install_run_reaper(
            FakeLifecycle(), interval_s=0.01, log=logs.append, stop=stop
        )
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(len(calls), 1)
        self.assertTrue(any("run-x" in message for message in logs))

    def test_a_failing_pass_does_not_kill_the_thread(self) -> None:
        stop = threading.Event()
        state = {"passes": 0}
        logs: list[str] = []

        class FakeLifecycle:
            def reap_dead_runs(self_inner) -> list[str]:
                state["passes"] += 1
                if state["passes"] == 1:
                    raise RuntimeError("boom")
                stop.set()
                return []

        thread = daemon.install_run_reaper(
            FakeLifecycle(), interval_s=0.01, log=logs.append, stop=stop
        )
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(state["passes"], 2)
        self.assertTrue(any("pass failed" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
