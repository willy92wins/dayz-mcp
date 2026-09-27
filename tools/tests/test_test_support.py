"""Tests of test-support harnesses used by the lease family.

Moved verbatim from test_task7_review_regressions.py, test_task7_rereview_regressions.py
(review 2026-09-25, W4d step 3).
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from tests.lifecycle_helpers import _wait_for_dayz_mcp_background_workers


# --- from test_task7_review_regressions.py ---


class DayzMcpWorkerWaitTest(unittest.TestCase):
    def test_wait_fails_explicitly_when_workers_outlive_deadline(self) -> None:
        class _StuckThread:
            name = "dayz-mcp-release-audit-deadbeef"

            def join(self, timeout: float | None = None) -> None:
                return None

        stuck = _StuckThread()
        ticks = {"n": 0}

        def mono() -> float:
            # 0.0 -> deadline=2.0; 0.5 -> still waiting; 2.0 -> fail
            ticks["n"] += 1
            return {1: 0.0, 2: 0.5, 3: 2.0}.get(ticks["n"], 2.0)

        with self.assertRaisesRegex(
            AssertionError,
            r"los workers dayz-mcp-release-audit-deadbeef no terminaron en 2\.0 s",
        ):
            _wait_for_dayz_mcp_background_workers(
                timeout_s=2.0,
                enumerate_fn=lambda: [stuck],
                monotonic_fn=mono,
            )

    def test_wait_returns_when_no_workers(self) -> None:
        _wait_for_dayz_mcp_background_workers(
            timeout_s=2.0,
            enumerate_fn=lambda: [],
            monotonic_fn=lambda: 0.0,
        )


# --- from test_task7_rereview_regressions.py ---


class SettlementFlakeProbeExitTest(unittest.TestCase):
    def test_setup_import_failure_exits_nonzero_and_keeps_fail_counts(self) -> None:
        # Reviewer: CLASS -> tests.__missing_codex_review__, one loop,
        # unittest FAILED (errors=1), loops=1 fails=1, but harness exit 0.
        # A measuring tool that cannot go red is an ornament.
        import tests._settlement_flake_probe as probe

        stdout = io.StringIO()
        with patch.object(probe, "CLASS", "tests.__missing_codex_review__"):
            with patch.object(sys, "argv", ["_settlement_flake_probe.py", "1"]):
                with patch.object(sys, "stdout", stdout):
                    with self.assertRaises(SystemExit) as raised:
                        probe.main()
        self.assertNotIn(raised.exception.code, (0, None), raised.exception)
        text = stdout.getvalue()
        self.assertIn("loops=1", text)
        self.assertIn("fails=1", text)

    def test_all_green_loops_leave_the_harness_exit_at_zero(self) -> None:
        import tests._settlement_flake_probe as probe

        stdout = io.StringIO()
        with patch.object(probe, "_run_class", return_value=(0, "OK")):
            with patch.object(sys, "argv", ["_settlement_flake_probe.py", "3"]):
                with patch.object(sys, "stdout", stdout):
                    try:
                        probe.main()
                        code = 0
                    except SystemExit as exc:
                        code = 0 if exc.code in (0, None) else exc.code
        self.assertEqual(code, 0)
        text = stdout.getvalue()
        self.assertIn("loops=3", text)
        self.assertIn("fails=0", text)


if __name__ == "__main__":
    unittest.main()
