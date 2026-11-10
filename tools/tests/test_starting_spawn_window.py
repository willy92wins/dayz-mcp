"""ah-starting-spawn-20261110: a stop anywhere in the STARTING window is a cancel.

Offline only: no DayZ launch, no daemon process, no network, no PBO.

The daemon row leaves STARTING right after Popen and the identity snapshot,
while the launcher acknowledges the start later. On Willy (E2E :8785, head
ec006d27) the row was RUNNING about 40 ms after DayZDiag was created and the
ack came 0.7-1.1 s later, so:

- run01 attempt 3 (2b6d0197): the stop reached the daemon 19 ms after the row
  became RUNNING, with the launcher not yet acknowledged, and answered
  forced_kill; the launcher's ack was then refused and the run said
  worker_failed.
- run02 (real DayZDiag): the same row shape, so the same forced_kill.

These tests drive ProcessLifecycle, the worker and the public stop route with
the fixtures of tests.test_starting_cancel and require, at both points of the
window, stop_method=cancelled_during_start and a launcher answer of
launch_cancelled, never forced_kill. A stop after the ack stays a stop.
"""
from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import dayz_test_tool, dayz_test_worker
from tests.dayz_test_tool_helpers import RUN_ID, _Runtime
from tests.process_lifecycle_helpers import IDENTITY_A
from tests import test_dayz_test_worker as _worker_tests
from tests.test_dayz_test_worker import _Broker, _raw
from tests.test_starting_cancel import _OP_ID, _Fixture


class _SpawnWindowCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _Fixture()
        self.addCleanup(self.fixture.close)

    def _join(self, thread: threading.Thread, timeout: float = 3.0) -> None:
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), "thread deadlocked")

    def _launch_to_running(self) -> None:
        """Let the launch win: the row is RUNNING and not acknowledged."""
        start_thread = self.fixture.start_in_thread()
        self.fixture.launcher.release.set()
        self._join(start_thread)
        started = self.fixture.start_result["value"]
        self.assertIs(started.get("ok"), True, started)
        self.assertEqual(started.get("state"), "RUNNING")
        stored = self.fixture.store.get(RUN_ID)
        assert stored is not None
        self.assertEqual((stored.state, stored.launch_acknowledged), ("RUNNING", False))

    def _ack(self) -> dict[str, object]:
        return self.fixture.lifecycle.ack_run(
            IDENTITY_A, self.fixture.token, RUN_ID, _OP_ID
        )

    def _assert_cancel(self, stop: dict[str, object]) -> None:
        self.assertIs(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("stop_method"), "cancelled_during_start", stop)
        self.assertNotEqual(stop.get("stop_method"), "forced_kill")
        self.assertEqual(stop.get("state"), "EXITED")
        self.assertEqual(stop.get("terminated"), 1)
        self.assertEqual(
            [item.pid for item in self.fixture.guard.terminate_calls],
            [self.fixture.launcher.pid],
        )
        stored = self.fixture.store.get(RUN_ID)
        assert stored is not None
        self.assertEqual(
            (stored.state, stored.owner_session_id, stored.owner_lease_id, stored.processes),
            ("EXITED", None, None, []),
        )
        # The in-flight launcher learns the start was cancelled, once.
        ack = self._ack()
        self.assertIsNot(ack.get("ok"), True, ack)
        self.assertEqual(ack.get("error"), "launch_cancelled", ack)
        again = self._ack()
        self.assertEqual(again.get("error"), "launch_identity_conflict", again)


class StopAfterSpawnBeforeAckTest(_SpawnWindowCase):
    """Point 2 (run02, real DayZDiag) and run01 attempt 3's 19 ms late stop."""

    def test_stop_after_spawn_before_ack_cancels_the_start(self) -> None:
        self._launch_to_running()
        stop = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, RUN_ID
        )
        self._assert_cancel(stop)
        outcomes = [
            event for event in self.fixture.audit.events
            if event.get("event") == "lifecycle_stop_outcome"
        ]
        self.assertEqual(
            [(event.get("reason"), event.get("decision")) for event in outcomes],
            [("cancelled_during_start", "cancelled")],
        )


class CancelAfterLastBoundaryCheckTest(_SpawnWindowCase):
    """Point 1: the cancel request lands after the launch's last check.

    The lock-free branch of stop_run signals the launch; the launch has
    already passed its post-snapshot check, commits RUNNING and settles with
    no outcome. Before this delta the stop then took the ordinary path and
    answered forced_kill.
    """

    def test_late_cancel_request_still_answers_cancelled(self) -> None:
        fixture = self.fixture
        original_confirm = fixture.lifecycle._confirm_instance
        stop_threads: list[threading.Thread] = []

        def confirm_then_stop(minted, record):
            # Past the last cancel.requested check: start the stop now and
            # wait until it has signalled the launch, then let it continue.
            stop_threads.append(fixture.stop_in_thread())
            cancel = fixture.lifecycle._launch_cancel_for(RUN_ID)
            assert cancel is not None
            assert cancel.requested.wait(2.0), "stop never signalled the launch"
            return original_confirm(minted, record)

        with patch.object(fixture.lifecycle, "_confirm_instance", confirm_then_stop):
            start_thread = fixture.start_in_thread()
            fixture.launcher.release.set()
            self._join(start_thread)
        self.assertEqual(len(stop_threads), 1)
        self._join(stop_threads[0])
        started = fixture.start_result["value"]
        # The launch won the race to RUNNING ...
        self.assertIs(started.get("ok"), True, started)
        self.assertEqual(started.get("state"), "RUNNING")
        # ... and the stop still answers the cancel of that start.
        self._assert_cancel(fixture.stop_result["value"])


class AckLosesToPendingStopTest(_SpawnWindowCase):
    """Review 5dff34ab SHOULD-1: the ack must not overtake a waiting stop.

    The stop is registered before it waits for _operation_lock. The test holds
    that lock (an RLock) so the stop is parked exactly there, then sends the
    launcher's ack from the lock owner: the ack loses with launch_cancelled
    and the row stays RUNNING and unacknowledged for the stop to cancel.
    """

    def _park_stop(self) -> threading.Thread:
        thread = self.fixture.stop_in_thread()
        lifecycle = self.fixture.lifecycle
        for _ in range(400):
            if lifecycle._start_stop_pending(RUN_ID, _OP_ID):
                return thread
            threading.Event().wait(0.005)
        self.fail("stop never registered")

    def test_ack_while_same_session_stop_waits_for_the_lock(self) -> None:
        self._launch_to_running()
        lock = self.fixture.lifecycle._operation_lock
        lock.acquire()
        try:
            stop_thread = self._park_stop()
            ack = self._ack()
            self.assertEqual(ack.get("error"), "launch_cancelled", ack)
            stored = self.fixture.store.get(RUN_ID)
            assert stored is not None
            self.assertEqual(
                (stored.state, stored.launch_acknowledged), ("RUNNING", False)
            )
        finally:
            lock.release()
        self._join(stop_thread)
        stop = self.fixture.stop_result["value"]
        self.assertIs(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("stop_method"), "cancelled_during_start", stop)
        self.assertEqual(stop.get("terminated"), 1)
        self.assertEqual(
            [item.pid for item in self.fixture.guard.terminate_calls],
            [self.fixture.launcher.pid],
        )
        self.assertFalse(
            self.fixture.lifecycle._start_stop_pending(RUN_ID, _OP_ID)
        )

    def test_launcher_cleanup_first_then_the_waiting_stop_still_says_cancel(self) -> None:
        self._launch_to_running()
        lock = self.fixture.lifecycle._operation_lock
        lock.acquire()
        try:
            stop_thread = self._park_stop()
            self.assertEqual(self._ack().get("error"), "launch_cancelled")
            # The launcher's own cleanup stop (same session and lease) wins
            # the lock and cancels the start.
            cleanup = self.fixture.lifecycle.stop_run(
                IDENTITY_A, self.fixture.token, RUN_ID
            )
            self.assertEqual(cleanup.get("stop_method"), "cancelled_during_start", cleanup)
            self.assertEqual(cleanup.get("terminated"), 1)
        finally:
            lock.release()
        self._join(stop_thread)
        stop = self.fixture.stop_result["value"]
        self.assertIs(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("stop_method"), "cancelled_during_start", stop)
        self.assertEqual(stop.get("terminated"), 0)
        self.assertEqual(len(self.fixture.guard.terminate_calls), 1)

    def test_unauthorized_stop_never_registers(self) -> None:
        # Review 028c3425 SHOULD-1: registration happens only after the stop
        # is authorized, so a stop with no valid token cannot make the ack
        # lose, not even while its authorization is being decided.
        self._launch_to_running()
        lifecycle = self.fixture.lifecycle
        original = lifecycle._authorize
        seen: list[bool] = []

        def authorize(client, token, command):
            if command == "lifecycle_stop":
                seen.append(lifecycle._start_stop_pending(RUN_ID, _OP_ID))
            return original(client, token, command)

        with patch.object(lifecycle, "_authorize", authorize):
            refused = lifecycle.stop_run(IDENTITY_A, "not-a-token", RUN_ID)
        self.assertIsNot(refused.get("ok"), True, refused)
        self.assertEqual(seen, [False])
        self.assertFalse(lifecycle._start_stop_pending(RUN_ID, _OP_ID))
        self.assertIs(self._ack().get("ok"), True)
        self.assertEqual(self.fixture.guard.terminate_calls, [])

    def test_no_pending_stop_lets_the_ack_through(self) -> None:
        self._launch_to_running()
        self.assertFalse(self.fixture.lifecycle._start_stop_pending(RUN_ID, _OP_ID))
        self.assertIs(self._ack().get("ok"), True)


class StopAfterAckStaysAStopTest(_SpawnWindowCase):
    def test_acknowledged_run_is_not_relabelled(self) -> None:
        self._launch_to_running()
        ack = self._ack()
        self.assertIs(ack.get("ok"), True, ack)
        stop = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, RUN_ID
        )
        self.assertIs(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("stop_method"), "forced_kill")
        self.assertEqual(self._ack().get("error"), "launch_identity_conflict")


class WorkerAckCancelledTest(unittest.TestCase):
    def test_ack_refused_as_launch_cancelled_is_named(self) -> None:
        class _CancelledAckBroker(_Broker):
            async def invoke(self, frame: bytes) -> dict[str, object]:
                from dayz_mcp import native_broker_protocol

                request = native_broker_protocol.decode_request(frame)
                if request.payload.get("command") == "ack":
                    self.requests.append(request)
                    return {"ok": False, "error": "launch_cancelled"}
                return await super().invoke(frame)

        broker = _CancelledAckBroker()
        runner = _worker_tests.DayzTestWorkerTests("test_failed_cleanup_retains_exact_new_run_and_marks_degraded")
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            runner._run(_raw(), broker)
        self.assertEqual(raised.exception.code, "launch_cancelled")
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests],
            ["start", "ack", "stop"],
        )

    def test_other_ack_refusals_still_collapse(self) -> None:
        class _ConflictAckBroker(_Broker):
            async def invoke(self, frame: bytes) -> dict[str, object]:
                from dayz_mcp import native_broker_protocol

                request = native_broker_protocol.decode_request(frame)
                if request.payload.get("command") == "ack":
                    self.requests.append(request)
                    return {"ok": False, "error": "launch_identity_conflict"}
                return await super().invoke(frame)

        runner = _worker_tests.DayzTestWorkerTests("test_failed_cleanup_retains_exact_new_run_and_marks_degraded")
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            runner._run(_raw(), _ConflictAckBroker())
        self.assertEqual(raised.exception.code, "worker_failed")


class DirectRouteSpawnWindowTest(unittest.IsolatedAsyncioTestCase):
    def _runtime(self, row: dict[str, object]) -> tuple[_Runtime, list[str]]:
        runtime = _Runtime({"runs": [row]})
        runtime.active_lease_token = "lease-1"
        runtime.identity = SimpleNamespace(session_id="sess-1")
        calls: list[str] = []

        async def lifecycle_stop(run_id: str) -> dict[str, object]:
            calls.append(run_id)
            return {
                "ok": True,
                "run_id": run_id,
                "state": "EXITED",
                "terminated": 1,
                "stop_method": "cancelled_during_start",
            }

        runtime.lifecycle_stop = lifecycle_stop  # type: ignore[attr-defined]
        return runtime, calls

    def _row(self, **extra: object) -> dict[str, object]:
        row: dict[str, object] = {
            "run_id": RUN_ID,
            "state": "RUNNING",
            "owner_session_id": "sess-1",
            "owner_lease_id": "lease-1",
            "mod": "@DayZ_MCP",
            "launch_operation_id": _OP_ID,
        }
        row.update(extra)
        return row

    async def _never_ordinary(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("ordinary stop must not run")

    async def test_unacknowledged_running_row_takes_the_direct_route(self) -> None:
        for named in (RUN_ID, None):
            with self.subTest(run_id=named):
                runtime, calls = self._runtime(self._row(launch_acknowledged=False))
                with patch.object(dayz_test_tool, "_require_idle_session", self._never_ordinary):
                    result = await dayz_test_tool.execute_dayz_test_stop(
                        runtime, named, direct_inflight=True
                    )
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(calls, [RUN_ID])
                self.assertEqual(result.get("stop_method"), "cancelled_during_start")
                self.assertEqual(result.get("status"), "succeeded")

    async def test_acknowledged_or_foreign_row_does_not(self) -> None:
        for row in (
            self._row(launch_acknowledged=True),
            self._row(launch_acknowledged=False, launch_operation_id=None),
            self._row(launch_acknowledged=False, owner_session_id="sess-2"),
        ):
            with self.subTest(row=row):
                runtime, calls = self._runtime(row)
                with patch.object(dayz_test_tool, "_require_idle_session", self._never_ordinary):
                    result = await dayz_test_tool.execute_dayz_test_stop(
                        runtime, RUN_ID, direct_inflight=True
                    )
                self.assertIsNone(result)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
