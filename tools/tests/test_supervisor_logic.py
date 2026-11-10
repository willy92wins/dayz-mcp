import sys
import time
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from supervisor.adapters import FakeHostMetrics, FakeLifecycle, FakeStop, make_process
from supervisor.loop import run_supervisor
from supervisor.types import MonitorThresholds, RunSnapshot, StopOutcomeKind

GiB = 1 << 30


class SupervisorLogicTests(unittest.TestCase):
    def setUp(self):
        self.life = FakeLifecycle()
        self.metrics = FakeHostMetrics(
            free_physical_bytes=10 * GiB, private_by_pid={11: 1 * GiB}
        )
        self.stop = FakeStop()
        self.thresholds = MonitorThresholds(
            floor_free_physical_bytes=3 * GiB,
            max_private_sum_bytes=4 * GiB,
            max_sample_age_s=5.0,
        )

    def _put(self, state, pid=11, creation="2026-11-09T01:00:00Z"):
        self.life.put(
            RunSnapshot(
                run_id="run-a",
                state=state,
                processes=(make_process(pid, creation),),
                observed_at_monotonic=time.monotonic(),
            )
        )

    def _clear_after_stop(self, run_id, state="EXITED"):
        self.life.put(
            RunSnapshot(
                run_id=run_id,
                state=state,
                processes=(),
                observed_at_monotonic=time.monotonic(),
            )
        )

    def test_no_breach_completes_without_stop(self):
        self._put("RUNNING")
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=3,
        )
        self.assertEqual(self.stop.calls, [])
        self.assertFalse(r.claimed_real_shutdown)

    def test_private_breach_stop_ok_pids_cleared(self):
        self._put("RUNNING")
        self.metrics.private_by_pid[11] = 5 * GiB

        def stop_and_clear(run_id):
            self.stop.calls.append(run_id)
            self._clear_after_stop(run_id)
            return {
                "ok": True,
                "error_code": None,
                "stop_method": "forced_kill",
                "raw_status": "succeeded",
                "state": "EXITED",
                "run_id": run_id,
                "terminated": 1,
                "cleanup_degraded": False,
            }

        self.stop.stop = stop_and_clear  # type: ignore
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.SUCCEEDED_PIDS_ZERO)
        self.assertTrue(r.claimed_real_shutdown)
        self.assertFalse(r.storage_rollback_claimed)
        self.assertFalse(r.hard_os_limit_implemented)
        self.assertIsNotNone(r.t_decision_monotonic)
        self.assertIsNotNone(r.t_stop_return_monotonic)

    def test_stop_fail_closed_does_not_claim_shutdown(self):
        self._put("RUNNING")
        self.metrics.private_by_pid[11] = 9 * GiB
        self.stop.default_ok = False
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.STOP_FAILED)
        self.assertFalse(r.claimed_real_shutdown)
        self.assertEqual(r.stop_error_code, "run_stop_failed")

    def test_sample_fail_closed(self):
        self._put("RUNNING")
        self.metrics.fail_next = True
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=3,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.SAMPLE_FAILED)
        self.assertFalse(r.claimed_real_shutdown)
        self.assertEqual(self.stop.calls, [])

    def test_starting_state_owned_breach_calls_public_stop(self):
        """ba47dbe7: STARTING is stoppable through the owned public cancel."""
        self._put("STARTING")
        self.metrics.private_by_pid[11] = 9 * GiB

        def stop_and_cancel(run_id):
            self.stop.calls.append(run_id)
            self._clear_after_stop(run_id)
            return {
                "ok": True,
                "error_code": None,
                "stop_method": "cancelled_during_start",
                "raw_status": "succeeded",
                "state": "EXITED",
                "run_id": run_id,
                "terminated": 1,
                "cleanup_degraded": False,
            }

        self.stop.stop = stop_and_cancel  # type: ignore
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(self.stop.calls, ["run-a"])
        self.assertEqual(r.outcome, StopOutcomeKind.SUCCEEDED_PIDS_ZERO)
        self.assertTrue(r.claimed_real_shutdown)
        self.assertEqual(r.stop_state, "EXITED")
        self.assertFalse(r.storage_rollback_claimed)
        self.assertTrue(
            any(event.name == "s3_storage_note" for event in r.events)
        )

    def test_product_refusal_in_starting_is_stop_refused_state(self):
        self._put("STARTING")
        self.metrics.private_by_pid[11] = 9 * GiB
        self.stop.responses = [
            {
                "ok": False,
                "error_code": "run_not_active",
                "stop_method": None,
                "raw_status": "failed",
                "state": "STARTING",
                "run_id": "run-a",
                "terminated": None,
                "cleanup_degraded": None,
            }
        ]
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(self.stop.calls, ["run-a"])
        self.assertEqual(r.outcome, StopOutcomeKind.STOP_REFUSED_STATE)
        self.assertFalse(r.claimed_real_shutdown)
        self.assertEqual(r.stop_error_code, "run_not_active")

    def test_stop_ok_but_pids_remain_no_claim(self):
        self._put("RUNNING")
        self.metrics.private_by_pid[11] = 9 * GiB
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.STOP_FAILED)
        self.assertFalse(r.claimed_real_shutdown)

    def test_cadence_measured(self):
        self._put("RUNNING")
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.01,
            max_ticks=4,
        )
        self.assertGreaterEqual(len(r.cadence_samples), 2)
        self.assertIsNotNone(r.mean_sample_interval_s)

    def test_floor_free_physical_breach_stops_owned_run(self):
        self._put("RUNNING")
        self.metrics.free_physical_bytes = 2 * GiB
        self.metrics.private_by_pid[11] = 1 * GiB

        def stop_and_clear(run_id):
            self.stop.calls.append(run_id)
            self._clear_after_stop(run_id)
            return {
                "ok": True,
                "error_code": None,
                "stop_method": "forced_kill",
                "raw_status": "succeeded",
                "state": "EXITED",
                "run_id": run_id,
                "terminated": 1,
                "cleanup_degraded": False,
            }

        self.stop.stop = stop_and_clear  # type: ignore
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(self.stop.calls, ["run-a"])
        self.assertEqual(r.outcome, StopOutcomeKind.SUCCEEDED_PIDS_ZERO)
        self.assertTrue(r.claimed_real_shutdown)
        self.assertTrue(
            any(
                event.name == "decision_threshold"
                and event.detail.get("breach") == "floor_free_physical"
                for event in r.events
            )
        )

    def test_launch_cancel_pending_is_named(self):
        self._put("STARTING")
        self.metrics.private_by_pid[11] = 9 * GiB
        self.stop.responses = [
            {
                "ok": False,
                "error_code": "launch_cancel_pending",
                "stop_method": None,
                "raw_status": "failed",
                "state": "STARTING",
                "run_id": "run-a",
                "terminated": None,
                "cleanup_degraded": None,
            }
        ]
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.LAUNCH_CANCEL_PENDING)
        self.assertFalse(r.claimed_real_shutdown)
        self.assertEqual(r.stop_state, "STARTING")
        self.assertEqual(r.stop_error_code, "launch_cancel_pending")

    def test_launch_cancel_degraded_reports_durable_state(self):
        self._put("STARTING")
        self.metrics.private_by_pid[11] = 9 * GiB

        def stop_degraded(run_id):
            self.stop.calls.append(run_id)
            # A degraded cancel leaves the row UNRECONCILED with a live PID
            # record; the supervisor must report that durable state.
            self._put("UNRECONCILED")
            return {
                "ok": False,
                "error_code": "launch_cancel_degraded",
                "stop_method": None,
                "raw_status": "failed",
                "state": "UNRECONCILED",
                "run_id": run_id,
                "terminated": None,
                "cleanup_degraded": True,
            }

        self.stop.stop = stop_degraded  # type: ignore
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.LAUNCH_CANCEL_DEGRADED)
        self.assertFalse(r.claimed_real_shutdown)
        self.assertEqual(r.stop_state, "UNRECONCILED")
        self.assertIn("durable_state=UNRECONCILED", r.message)
        self.assertNotIn("durable_state=STARTING", r.message)

    def test_stale_sample_fail_closed(self):
        self._put("RUNNING")
        self.metrics.private_by_pid[11] = 9 * GiB
        self.metrics.stale_s = 10.0
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.SAMPLE_STALE)
        self.assertFalse(r.claimed_real_shutdown)
        self.assertEqual(self.stop.calls, [])

    def test_session_busy_is_named(self):
        self._put("RUNNING")
        self.metrics.private_by_pid[11] = 9 * GiB
        self.stop.responses = [
            {
                "ok": False,
                "error_code": "session_busy",
                "stop_method": None,
                "raw_status": "failed",
                "state": "RUNNING",
                "run_id": "run-a",
                "terminated": None,
                "cleanup_degraded": None,
            }
        ]
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.STOP_SESSION_BUSY)
        self.assertFalse(r.claimed_real_shutdown)

    def test_missing_creation_rejected(self):
        self._put("RUNNING", creation="")
        self.metrics.private_by_pid[11] = 9 * GiB
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.SAMPLE_FAILED)
        self.assertFalse(r.claimed_real_shutdown)
        self.assertEqual(self.stop.calls, [])

    def test_private_missing_fail_closed(self):
        self._put("RUNNING")
        self.metrics.omit_pids = (11,)
        self.metrics.private_by_pid[11] = 9 * GiB
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.SAMPLE_FAILED)
        self.assertFalse(r.claimed_real_shutdown)
        self.assertEqual(self.stop.calls, [])

    def test_stop_exception_fail_closed(self):
        self._put("RUNNING")
        self.metrics.private_by_pid[11] = 9 * GiB

        def raising_stop(_run_id):
            raise RuntimeError("transport down")

        self.stop.stop = raising_stop  # type: ignore
        r = run_supervisor(
            run_id="run-a",
            lifecycle=self.life,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=self.thresholds,
            tick_s=0.0,
            max_ticks=5,
        )
        self.assertEqual(r.outcome, StopOutcomeKind.STOP_FAILED)
        self.assertFalse(r.claimed_real_shutdown)


if __name__ == "__main__":
    unittest.main()
