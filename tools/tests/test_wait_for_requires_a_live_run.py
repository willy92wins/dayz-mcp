"""wait_for(log_matches) must refuse when no run is live.

Without a live process stamp, `_run_start_epoch` returns None and
`_current_launch_logs` falls back to the newest .rpt/.log by mtime, with no
age floor. `wait_for` then scans that historic pair and can report
`satisfied: true` on a line from a run that EXITED hours or days earlier.

Measured on the live store: `lifecycle_status` lists every run
(`process_lifecycle.status` does not filter by `_ACTIVE_STATES`),
`RunRecord.validate` forces EXITED to carry `processes: []`, and with only
EXITED rows `_run_start_epoch` is None. The newest-file fallback is correct
for `logs_since` (read the last launch even after it died) and is pinned by
`test_without_a_floor_it_keeps_newest_rpt_and_script`. It is not correct for
`wait_for`, which is a wait on the launch in progress.

The guard is in `_wait_for_script_log_paths`: None epoch -> ToolError
`no_active_run`, the same named code already used when there is no snapshot.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from tests.test_wait_for_launch_and_contract import (
    _FakeRuntime,
    _live_process,
    _profiles,
)


NEEDLE = "[DayZ-MCP] leftover from a dead run"


def _exited(profiles: Path, run_id: str) -> dict:
    return {
        "run_id": run_id,
        "state": "EXITED",
        "profiles": str(profiles),
        "processes": [],
    }


def _live(profiles: Path, run_id: str = "run-live") -> dict:
    return {
        "run_id": run_id,
        "state": "RUNNING",
        "profiles": str(profiles),
        "processes": [_live_process()],
    }


async def _wait(runtime: _FakeRuntime, pattern: str = NEEDLE) -> dict:
    return await server.execute_wait_for(
        runtime,
        "log_matches",
        pattern=pattern,
        timeout_s=0.6,
        poll_interval_s=0.5,
        lookback_from="launch",
    )


class WaitForRequiresALiveRunTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_exited_runs_raise_no_active_run(self) -> None:
        """EXITED rows still carry `profiles`, so the old path scanned their logs."""

        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text(
                f"SCRIPT : {NEEDLE}\n", encoding="utf-8"
            )
            runtime = _FakeRuntime(
                profiles,
                runs=[
                    _exited(profiles, "dead-1"),
                    _exited(profiles, "dead-2"),
                ],
            )
            with self.assertRaises(server.ToolError) as caught:
                await _wait(runtime)
            self.assertEqual(
                str(caught.exception),
                "no_active_run",
                "the wire code must be the named token, not a sentence",
            )

    async def test_a_live_run_still_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text(
                f"SCRIPT : {NEEDLE}\n", encoding="utf-8"
            )
            result = await _wait(_FakeRuntime(profiles, runs=[_live(profiles)]))

        self.assertTrue(result["satisfied"])
        self.assertIn(NEEDLE, str(result["observed"]))

    async def test_a_live_run_among_exited_ones_still_matches(self) -> None:
        """The store shape: many EXITED rows, one with processes."""

        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text(
                f"SCRIPT : {NEEDLE}\n", encoding="utf-8"
            )
            runtime = _FakeRuntime(
                profiles,
                runs=[
                    _exited(profiles, "dead-1"),
                    _exited(profiles, "dead-2"),
                    _exited(profiles, "dead-3"),
                    _live(profiles),
                    _exited(profiles, "dead-4"),
                ],
            )
            result = await _wait(runtime)

        self.assertTrue(result["satisfied"])
        self.assertIn(NEEDLE, str(result["observed"]))


if __name__ == "__main__":
    unittest.main()
