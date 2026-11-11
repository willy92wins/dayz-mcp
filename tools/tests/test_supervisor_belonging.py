import sys
import time
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from supervisor.adapters import FakeLifecycle, make_process
from supervisor.belonging import index_owned, reject_foreign_pid
from supervisor.types import RunSnapshot


class BelongingTests(unittest.TestCase):
    def test_exact_pid_creation_accepted(self):
        snap = RunSnapshot(
            run_id="r1",
            state="RUNNING",
            processes=(make_process(10, "2026-11-09T01:00:00Z"),),
            observed_at_monotonic=time.monotonic(),
        )
        owned = index_owned(snap)
        self.assertIsNone(reject_foreign_pid(10, "2026-11-09T01:00:00Z", owned))

    def test_foreign_pid_rejected(self):
        snap = RunSnapshot(
            run_id="r1",
            state="RUNNING",
            processes=(make_process(10, "2026-11-09T01:00:00Z"),),
            observed_at_monotonic=time.monotonic(),
        )
        owned = index_owned(snap)
        self.assertEqual(
            reject_foreign_pid(99, "2026-11-09T01:00:00Z", owned), "foreign_pid"
        )

    def test_reused_pid_different_creation_rejected(self):
        snap = RunSnapshot(
            run_id="r1",
            state="RUNNING",
            processes=(make_process(10, "2026-11-09T01:00:00Z"),),
            observed_at_monotonic=time.monotonic(),
        )
        owned = index_owned(snap)
        self.assertEqual(
            reject_foreign_pid(10, "2026-11-09T02:00:00Z", owned),
            "pid_reused_different_creation",
        )

    def test_missing_creation_rejected(self):
        owned = {}
        self.assertEqual(
            reject_foreign_pid(1, None, owned), "no_creation_time_attribution"
        )


if __name__ == "__main__":
    unittest.main()
