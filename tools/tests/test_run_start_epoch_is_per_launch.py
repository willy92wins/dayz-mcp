"""`_run_start_epoch` and the launch-log floor it feeds.

`_current_launch_logs` exists so a profiles dir holding months of old RPT and
script files yields only the files of the launch in progress. Its floor comes
from `_run_start_epoch(runs)`, which flattens every process of every run into
one list and takes `min()`.

`min()` is the right aggregation *inside* a run -- `process_lifecycle._run_age_s`
(process_lifecycle.py:171-179) uses exactly that, because a run started when its
earliest process did. Applied *across* runs it answers a different question: it
returns the oldest launch still listed, not the current one. A second live run
would pull the floor back to the first one's start and readmit its logs, and a
`wait_for` needle already printed by that earlier launch would report
`satisfied: true` for a line the current run never wrote.

Measured before writing this file, because a mechanism is not a defect until
something can reach it:

  * `RunRecord.validate` (process_lifecycle.py:397-408) makes the states
    exclusive -- `EXITED` MUST carry an empty `processes`, `RUNNING` and
    `RUNNING_IDLE` MUST carry a non-empty one. Only a live run contributes a
    stamp at all.
  * Across the live store and its six pre-prune backups (126 accumulated runs in
    the largest), the number of runs holding processes was 0, 1, 0, 0, 0, 0, 1.
    Never two.

So the cross-run case is unreached on this host today, and these tests are a
guard, not a repair of an observed failure. The reason to close it anyway is
that both aggregations agree whenever one run is live -- `max` over per-run
starts and `min` over the flattened list return the same float -- so the guard
costs no behaviour change, and the failure it prevents is a silent true.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server


def _run(*stamps: str) -> dict:
    """A run record shaped like the lifecycle payload, carrying process stamps."""

    return {
        "run_id": "run-%s" % (stamps[0] if stamps else "empty"),
        "processes": [
            {"pid": 1000 + index, "creation_time_utc": value}
            for index, value in enumerate(stamps)
        ],
    }


class RunStartEpochTests(unittest.TestCase):
    def test_no_runs_and_no_stamps_yield_none(self) -> None:
        self.assertIsNone(server._run_start_epoch([]))
        self.assertIsNone(server._run_start_epoch([_run()]))

    def test_single_run_starts_at_its_earliest_process(self) -> None:
        """Inside one run the earliest process is the launch. This must not move."""

        epoch = server._run_start_epoch(
            [
                _run(
                    "2026-08-22T11:48:32.375091Z",
                    "2026-08-22T11:48:18.223130Z",
                )
            ]
        )
        self.assertIsNotNone(epoch)
        expected = server.datetime.fromisoformat(
            "2026-08-22T11:48:18.223130+00:00"
        ).timestamp()
        self.assertEqual(epoch, expected)

    def test_two_live_runs_take_the_newer_launch(self) -> None:
        """The floor must track the current launch, not the oldest one listed.

        This is the assertion the flattened `min()` fails: it returns the 09:00
        run and readmits four hours of that launch's logs.
        """

        epoch = server._run_start_epoch(
            [
                _run("2026-08-22T09:00:00.000000Z"),
                _run("2026-08-22T13:00:00.000000Z"),
            ]
        )
        newer = server.datetime.fromisoformat(
            "2026-08-22T13:00:00+00:00"
        ).timestamp()
        self.assertEqual(epoch, newer)

    def test_run_order_does_not_change_the_answer(self) -> None:
        """Runs arrive sorted by run_id, which says nothing about start time."""

        older = _run("2026-08-22T09:00:00.000000Z")
        newer = _run("2026-08-22T13:00:00.000000Z")
        self.assertEqual(
            server._run_start_epoch([older, newer]),
            server._run_start_epoch([newer, older]),
        )

    def test_a_finished_run_cannot_drag_the_floor_back(self) -> None:
        """`EXITED` runs carry no processes (validate), so they contribute nothing.

        Pinning it here means a future payload that keeps stale process records
        on a dead run fails this test instead of silently widening the window.
        """

        epoch = server._run_start_epoch(
            [
                {"run_id": "exited", "processes": []},
                _run("2026-08-22T13:00:00.000000Z"),
            ]
        )
        expected = server.datetime.fromisoformat(
            "2026-08-22T13:00:00+00:00"
        ).timestamp()
        self.assertEqual(epoch, expected)

    def test_malformed_entries_are_skipped_not_fatal(self) -> None:
        """A stamp the daemon could not format must not take down wait_for."""

        epoch = server._run_start_epoch(
            [
                {"run_id": "junk", "processes": ["not-a-dict", 7]},
                {"run_id": "blank", "processes": [{"creation_time_utc": ""}]},
                {"run_id": "absent", "processes": [{"pid": 1}]},
                {"run_id": "bad", "processes": [{"creation_time_utc": "hace un rato"}]},
                {"run_id": "none", "processes": None},
                _run("2026-08-22T13:00:00.000000Z"),
            ]
        )
        expected = server.datetime.fromisoformat(
            "2026-08-22T13:00:00+00:00"
        ).timestamp()
        self.assertEqual(epoch, expected)

    def test_every_stamp_unparseable_is_the_same_as_no_stamp(self) -> None:
        self.assertIsNone(
            server._run_start_epoch(
                [{"run_id": "bad", "processes": [{"creation_time_utc": "ayer"}]}]
            )
        )


class CurrentLaunchLogsTests(unittest.TestCase):
    """The floor in use: which files on disk the epoch admits."""

    def _profiles(self, stack: tempfile.TemporaryDirectory) -> Path:
        return Path(stack.name)

    def _write(self, directory: Path, name: str, mtime: float) -> Path:
        path = directory / name
        path.write_bytes(b"line\n")
        os.utime(path, (mtime, mtime))
        return path

    def test_the_floor_drops_files_from_an_earlier_launch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            start = 1_700_000_000.0
            old = self._write(directory, "old.RPT", start - 3600.0)
            current = self._write(directory, "current.RPT", start + 5.0)
            kept = server._current_launch_logs(str(directory), start)
            self.assertIn(str(current), kept)
            self.assertNotIn(str(old), kept)

    def test_the_two_second_grace_keeps_a_log_opened_just_before_the_stamp(self) -> None:
        """DayZ opens its log slightly before the process stamp is taken."""

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            start = 1_700_000_000.0
            early = self._write(directory, "early.RPT", start - 1.5)
            self.assertIn(str(early), server._current_launch_logs(str(directory), start))

    def test_an_older_floor_readmits_the_earlier_launch(self) -> None:
        """Why the aggregation matters, stated as behaviour rather than arithmetic.

        Same directory, same files; only the floor moves. With the earlier
        launch's start as the floor, its log comes back as "current".
        """

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            first_launch = 1_700_000_000.0
            second_launch = first_launch + 14_400.0
            stale = self._write(directory, "first.RPT", first_launch + 10.0)
            self._write(directory, "second.RPT", second_launch + 10.0)

            self.assertNotIn(
                str(stale), server._current_launch_logs(str(directory), second_launch)
            )
            self.assertIn(
                str(stale), server._current_launch_logs(str(directory), first_launch)
            )

    def test_without_a_floor_it_keeps_newest_rpt_and_newest_script(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            base = 1_700_000_000.0
            self._write(directory, "old.RPT", base)
            new_rpt = self._write(directory, "new.RPT", base + 100.0)
            self._write(directory, "old.log", base + 1.0)
            new_log = self._write(directory, "new.log", base + 50.0)
            kept = server._current_launch_logs(str(directory), None)
            self.assertEqual(sorted(kept), sorted([str(new_rpt), str(new_log)]))


if __name__ == "__main__":
    unittest.main()
