from __future__ import annotations

import ctypes
import dataclasses
import inspect
import io
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from _session_coordination.process_guard_gate import validate_result_shape
from dayz_mcp import loopback, native_process_guard, orphan_guard
from dayz_mcp.native_process_guard import NativeProcessGuard
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.fence_helpers import accredited_poll, bind_both_peers
from tests.test_task7_review_regressions import (
    Audit,
    Guard,
    IDENTITY,
    IDENTITY_PAYLOAD,
    LifecycleFixture,
    Sequence,
    identity,
    record,
)


IDENTITY_B = ClientIdentity(
    "claude", 22, 1, "2026-07-15T00:00:01Z", "other-session", "fifo"
)


class AuthorityIoBoundaryTest(unittest.TestCase):
    def _blocked_authorize(self):
        entered = threading.Event()
        resume = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if event.get("event") == "session_authorized":
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, acquired = coordinator.acquire(IDENTITY, "authority")
        state = loopback.ServerState("key", coordination=coordinator)
        bind_both_peers(state)
        state.retail_probe = lambda: {"known": True, "processes": []}
        state._enqueue_command(
            "query_player_state",
            {},
            "server",
            owner_client=None,
            owner_lease_id=None,
        )
        result: list[tuple[int, dict]] = []
        worker = threading.Thread(
            target=lambda: result.append(
                state.enqueue_command(
                    "world_time_set",
                    {},
                    "server",
                    identity_payload=IDENTITY_PAYLOAD,
                    lease_token=acquired["lease_token"],
                )
            )
        )
        worker.start()
        self.assertTrue(entered.wait(1.0), "session_authorized audit not reached")
        return state, coordinator, acquired, resume, worker, result

    def test_poll_of_existing_read_progresses_while_authorization_audit_is_blocked(self) -> None:
        state, _coordinator, _acquired, resume, worker, _result = self._blocked_authorize()
        polled: list[tuple[int, dict]] = []
        poller = threading.Thread(target=lambda: polled.append(accredited_poll(state, "server")))
        try:
            poller.start()
            self.assertTrue(
                poller.join(0.25) is None and not poller.is_alive(),
                "poll_blocked_by_authorization_audit",
            )
            self.assertEqual(
                [item["cmd"] for item in polled[0][1]["commands"]],
                ["query_player_state"],
            )
        finally:
            resume.set()
            worker.join(2.0)
            poller.join(2.0)

    def test_release_invalidates_during_blocked_audit_and_old_reservation_rolls_back(self) -> None:
        state, coordinator, acquired, resume, worker, result = self._blocked_authorize()
        released: list[tuple[int, dict[str, object]]] = []
        release_done = threading.Event()

        def release() -> None:
            released.append(coordinator.release(IDENTITY, acquired["lease_token"]))
            release_done.set()

        releaser = threading.Thread(target=release)
        try:
            releaser.start()
            self.assertTrue(
                release_done.wait(0.25),
                "release_blocked_by_session_authorized_audit",
            )
            resume.set()
            worker.join(2.0)
            releaser.join(2.0)
            self.assertEqual(released[0][0], 200)
            self.assertEqual(result[0], (409, {"error": "lease_invalid"}))
            self.assertEqual(state.status_snapshot()["peers"]["server"]["queue_depth"], 1)
        finally:
            resume.set()
            worker.join(2.0)
            releaser.join(2.0)

    def test_audit_callback_never_observes_condition_owned_by_another_thread(self) -> None:
        holder: dict[str, SessionCoordinator] = {}
        observations: list[tuple[str, bool]] = []

        def audit(event: dict[str, object]) -> bool:
            coordinator = holder.get("coordinator")
            if coordinator is None:
                return True
            acquired: list[bool] = []

            def probe() -> None:
                locked = coordinator._condition.acquire(timeout=0.2)
                acquired.append(locked)
                if locked:
                    coordinator._condition.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(0.5)
            observations.append((str(event.get("event")), acquired == [True]))
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        holder["coordinator"] = coordinator
        _, acquired = coordinator.acquire(IDENTITY, "audit-lock")
        coordinator.authorize(IDENTITY, acquired["lease_token"], "world_time_set")
        coordinator.release(IDENTITY, acquired["lease_token"])
        self.assertTrue(observations)
        self.assertEqual(
            [event for event, free in observations if not free],
            [],
            observations,
        )

    def test_stale_admin_release_cannot_release_the_next_lease(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "admin_release":
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        queued_status, ticket = coordinator.acquire(IDENTITY_B, "l2")
        self.assertEqual((queued_status, ticket["status"]), (202, "queued"))
        admin_result: list[tuple[int, dict[str, object]]] = []
        thread = threading.Thread(
            target=lambda: admin_result.append(
                coordinator.admin_release(l1["lease_id"], "operator")
            )
        )
        thread.start()
        self.assertTrue(entered.wait(1.0))
        coordinator.release(IDENTITY, l1["lease_token"])
        resume.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertNotEqual(admin_result[0][0], 200)
        claimed = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))
        snapshot = coordinator.snapshot_payload()
        self.assertEqual(
            snapshot["active"]["session"], IDENTITY_B.session_id[:12]
        )

    def test_stale_heartbeat_cannot_report_renewal_after_release_wins(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_heartbeat":
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        queued_status, ticket = coordinator.acquire(IDENTITY_B, "l2")
        self.assertEqual((queued_status, ticket["status"]), (202, "queued"))
        heartbeat_result: list[tuple[int, dict[str, object]]] = []
        thread = threading.Thread(
            target=lambda: heartbeat_result.append(
                coordinator.heartbeat(IDENTITY, l1["lease_token"])
            )
        )
        thread.start()
        self.assertTrue(entered.wait(1.0))
        coordinator.release(IDENTITY, l1["lease_token"])
        resume.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertNotEqual(heartbeat_result[0][0], 200)
        claimed = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))
        snapshot = coordinator.snapshot_payload()
        self.assertEqual(
            snapshot["active"]["session"],
            IDENTITY_B.session_id[:12],
        )

    def test_concurrent_initial_grant_cannot_overwrite_another_lease(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            client = event.get("client")
            if (
                event.get("event") == "session_acquire"
                and event.get("decision") == "grant"
                and isinstance(client, dict)
                and client.get("session") == IDENTITY.session_id[:12]
            ):
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        first: list[tuple[int, dict[str, object]]] = []
        thread = threading.Thread(
            target=lambda: first.append(coordinator.acquire(IDENTITY, "first"))
        )
        thread.start()
        self.assertTrue(entered.wait(1.0))
        second: list[tuple[int, dict[str, object]]] = []
        second_thread = threading.Thread(
            target=lambda: second.append(coordinator.acquire(IDENTITY_B, "second"))
        )
        second_thread.start()
        resume.set()
        thread.join(1.0)
        second_thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(second[0][0], 202)
        self.assertEqual(first[0][0], 200)
        snapshot = coordinator.snapshot_payload()
        self.assertEqual(snapshot["active"]["session"], IDENTITY.session_id[:12])
        self.assertEqual(snapshot["queue"][0]["session"], IDENTITY_B.session_id[:12])

    def test_concurrent_fifo_grant_cannot_pop_or_overwrite_a_granted_ticket(self) -> None:
        entered = threading.Event()
        resume = threading.Event()
        blocked = False

        def audit(event: dict[str, object]) -> bool:
            nonlocal blocked
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
                and not blocked
            ):
                blocked = True
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        queued_status, ticket = coordinator.acquire(IDENTITY_B, "l2")
        self.assertEqual((queued_status, ticket["status"]), (202, "queued"))
        release_status, _ = coordinator.release(IDENTITY, l1["lease_token"])
        self.assertEqual(release_status, 200)
        claimed: list[tuple[int, dict[str, object]]] = []
        claim_error: list[Exception] = []

        def claim() -> None:
            try:
                claimed.append(
                    coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
                )
            except Exception as exc:  # pragma: no cover - RED diagnostic
                claim_error.append(exc)

        thread = threading.Thread(target=claim)
        thread.start()
        self.assertTrue(entered.wait(1.0))
        coordinator.status(IDENTITY_B)
        resume.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(claim_error, [])
        self.assertEqual((claimed[0][0], claimed[0][1]["status"]), (200, "active"))
        snapshot = coordinator.snapshot_payload()
        self.assertEqual(snapshot["active"]["session"], IDENTITY_B.session_id[:12])
        self.assertEqual(snapshot["queue"], [])


class CleanupBudgetAndFencingTest(unittest.TestCase):
    def test_releasing_has_injectable_budget_timeout_audit_and_fifo_progress(self) -> None:
        if "cleanup_timeout_s" not in inspect.signature(SessionCoordinator).parameters:
            self.fail("cleanup_timeout_s_missing")
        started = threading.Event()
        resume = threading.Event()
        events: list[dict[str, object]] = []

        def cleanup(*_args):
            started.set()
            resume.wait(2.0)
            return {"cancelled": 0}

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda event: events.append(dict(event)) or True,
            cleanup=cleanup,
            cleanup_timeout_s=0.05,
        )
        _, first = coordinator.acquire(IDENTITY, "first")
        status, ticket = coordinator.acquire(IDENTITY_B, "second")
        self.assertEqual((status, ticket["status"]), (202, "queued"))
        try:
            began = time.monotonic()
            status, released = coordinator.release(IDENTITY, first["lease_token"])
            elapsed = time.monotonic() - began
            self.assertTrue(started.is_set())
            self.assertLess(elapsed, 0.5)
            self.assertEqual(status, 200)
            self.assertIn("cleanup_timeout", released["cleanup_degraded"])
            claimed = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
            self.assertEqual(
                (claimed[0], claimed[1]["status"]), (200, "active")
            )
            snapshot = coordinator.snapshot_payload()
            self.assertEqual(
                snapshot["active"]["session"], IDENTITY_B.session_id[:12]
            )
            timeout_events = [
                item for item in events if item.get("event") == "session_cleanup_timeout"
            ]
            self.assertEqual(len(timeout_events), 1)
            self.assertEqual(timeout_events[0]["lease_id"], first["lease_id"])
        finally:
            resume.set()

    def test_late_l1_cleanup_is_fenced_from_same_identity_l2_queue_and_run(self) -> None:
        coordinator_params = inspect.signature(SessionCoordinator).parameters
        state_params = inspect.signature(loopback.ServerState.cleanup_owner).parameters
        lifecycle_params = inspect.signature(ProcessLifecycle.release_owner).parameters
        if "cleanup_timeout_s" not in coordinator_params:
            self.fail("cleanup_timeout_s_missing")
        if "lease_id" not in state_params or "lease_id" not in lifecycle_params:
            self.fail("exact_cleanup_fencing_missing")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            game = root / "DayZ"
            game.mkdir()
            (game / "DayZDiag_x64.exe").write_bytes(b"")
            state = loopback.ServerState("key", coordination=None)
            bind_both_peers(state)
            started = threading.Event()
            resume = threading.Event()
            finished = threading.Event()
            lifecycle_holder: dict[str, ProcessLifecycle] = {}

            def cleanup(session_id, lease_id, reason, vehicle_active):
                started.set()
                resume.wait(2.0)
                result = state.cleanup_owner(
                    session_id, lease_id, reason, vehicle_active
                )
                result["runs_released"] = lifecycle_holder["service"].release_owner(
                    session_id, lease_id
                )
                finished.set()
                return result

            coordinator = SessionCoordinator(
                token_fn=Sequence("token"),
                id_fn=Sequence("lease"),
                audit=lambda _event: True,
                cleanup=cleanup,
                cleanup_timeout_s=0.05,
            )
            state.coordination = coordinator
            state.retail_probe = lambda: {"known": True, "processes": []}
            lifecycle = ProcessLifecycle(
                coordinator=coordinator,
                manifest=RunManifestStore(paths),
                audit=lambda _event: True,
                guard=Guard(),
                retail_probe=lambda: {"known": True, "processes": []},
                diag_probe=lambda: {"known": True, "processes": []},
                game_path=game,
            )
            lifecycle_holder["service"] = lifecycle

            _, l1 = coordinator.acquire(IDENTITY, "l1")
            status, released = coordinator.release(IDENTITY, l1["lease_token"])
            self.assertEqual(status, 200)
            self.assertIn("cleanup_timeout", released["cleanup_degraded"])
            _, l2 = coordinator.acquire(IDENTITY, "l2")
            lifecycle.manifest.add(
                RunRecord(
                    "l2-run",
                    IDENTITY.session_id,
                    l2["lease_id"],
                    "RUNNING",
                    "red",
                    "@M",
                    "p",
                    "m",
                    [record(8201)],
                )
            )
            queued_status, queued = state.enqueue_command(
                "world_time_set",
                {},
                "server",
                identity_payload=IDENTITY_PAYLOAD,
                lease_token=l2["lease_token"],
            )
            self.assertEqual(queued_status, 200)
            resume.set()
            self.assertTrue(finished.wait(1.0))
            self.assertEqual(state.pending_for_owner(IDENTITY.session_id), 1)
            self.assertIsNone(state.take_result(queued["id"]))
            run = lifecycle.manifest.get("l2-run")
            self.assertEqual(
                (run.state, run.owner_lease_id), ("RUNNING", l2["lease_id"])
            )

    def test_late_retail_probe_cannot_enqueue_vehicle_release_after_fence_closes(self) -> None:
        probe_started = threading.Event()
        resume_probe = threading.Event()
        cleanup_finished = threading.Event()
        state = loopback.ServerState("key", coordination=None)
        bind_both_peers(state)

        def cleanup(session_id, lease_id, reason, vehicle_active):
            result = state.cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            )
            cleanup_finished.set()
            return result

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda _event: True,
            cleanup=cleanup,
            cleanup_timeout_s=0.05,
        )
        state.coordination = coordinator

        def blocked_probe():
            probe_started.set()
            resume_probe.wait(2.0)
            return {"known": True, "processes": []}

        state.retail_probe = blocked_probe
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        with coordinator._condition:
            coordinator._active.vehicle_active = True
        coordinator.acquire(IDENTITY_B, "l2")
        released = coordinator.release(IDENTITY, l1["lease_token"])[1]
        self.assertTrue(probe_started.is_set())
        self.assertIn("cleanup_timeout", released["cleanup_degraded"])
        resume_probe.set()
        self.assertTrue(cleanup_finished.wait(1.0))
        _, polled = accredited_poll(state, "client")
        self.assertEqual(polled["commands"], [])


class DispatchAuditDegradationTest(unittest.TestCase):
    def test_dispatch_rejection_audit_failure_is_attached_to_command_result(self) -> None:
        def audit(event: dict[str, object]) -> bool:
            return event.get("event") != "session_rejected"

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        _, acquired = coordinator.acquire(IDENTITY, "dispatch")
        state = loopback.ServerState("key", coordination=coordinator)
        bind_both_peers(state)
        state.retail_probe = lambda: {"known": True, "processes": []}
        status, queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )
        self.assertEqual(status, 200)
        state.retail_probe = lambda: {
            "known": True,
            "processes": [{"pid": 44, "name": "DayZ_x64.exe"}],
        }
        _, polled = accredited_poll(state, "server")
        self.assertEqual(polled["commands"], [])
        result = state.take_result(queued["id"])
        self.assertEqual(result["error"], "retail_quarantine")
        self.assertEqual(result.get("cleanup_degraded"), ["audit_failed"])


class FailedLaunchSettlementTest(unittest.TestCase):
    def test_failed_extension_with_confirmed_exit_restores_previous_bytes_and_processes(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            first, second = record(8301), record(8302)
            run = fixture.add_run(first)
            run.processes.append(second)
            fixture.store.replace(run)
            before = fixture.paths.runs_path.read_bytes()
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request(run_id="existing")
            )
            self.assertEqual(result["error"], "identity_unavailable")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
            restored = fixture.store.get("existing")
            self.assertEqual(
                (restored.state, restored.owner_session_id, restored.owner_lease_id),
                ("RUNNING", IDENTITY.session_id, fixture.lease_id),
            )
            self.assertEqual(restored.processes, [first, second])
        finally:
            fixture.close()

    def test_guard_exception_and_unconfirmed_handle_leave_durable_manual_cleanup(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=False)
        try:
            def fail_snapshot(_pid):
                raise TimeoutError("guard timeout")

            fixture.guard.snapshot = fail_snapshot  # type: ignore[method-assign]
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )
            self.assertEqual(result["error"], "manual_cleanup_required")
            self.assertIn(result["state"], {"STARTING", "UNRECONCILED"})
            run = fixture.store.get(result["run_id"])
            self.assertIsNotNone(run)
            self.assertIn(run.state, {"STARTING", "UNRECONCILED"})
        finally:
            fixture.close()

    def test_final_new_run_write_failure_with_strong_identity_keeps_recoverable_record(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=False)
        try:
            strong = record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[strong.pid] = identity(strong)
            original_replace = fixture.store.replace
            calls = 0

            def fail_once(run):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("disk full")
                return original_replace(run)

            fixture.store.replace = fail_once  # type: ignore[method-assign]
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )
            self.assertEqual(result.get("error"), "manual_cleanup_required")
            recovered = fixture.store.get(result.get("run_id", ""))
            self.assertEqual(recovered.state, "UNRECONCILED")
            self.assertEqual(recovered.processes, [strong])
            self.assertIn("manifest_failed", result["cleanup_degraded"])
        finally:
            fixture.close()

    def test_extension_final_write_failure_and_confirmed_exit_restores_exact_previous_run(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            existing = record(8303)
            fixture.add_run(existing)
            before = fixture.paths.runs_path.read_bytes()
            strong = record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[strong.pid] = identity(strong)
            original_replace = fixture.store.replace
            calls = 0

            def fail_second(run):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disk full")
                return original_replace(run)

            fixture.store.replace = fail_second  # type: ignore[method-assign]
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request(run_id="existing")
            )
            self.assertEqual(result.get("error"), "lifecycle_start_failed")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
            self.assertEqual(fixture.store.get("existing").processes, [existing])
        finally:
            fixture.close()

    def _assert_failed_extension_restores_previous(self, request, first, second, before, fixture) -> None:
        result = fixture.lifecycle.start_run(IDENTITY, fixture.token, request)
        self.assertEqual(result["error"], "identity_unavailable", result)
        self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
        restored = fixture.store.get("existing")
        self.assertEqual(
            (restored.state, restored.owner_session_id, restored.owner_lease_id),
            ("RUNNING", IDENTITY.session_id, fixture.lease_id),
        )
        self.assertEqual(restored.processes, [first, second])

    def _assert_extension_final_write_restores_previous(self, request, existing, before, fixture) -> None:
        result = fixture.lifecycle.start_run(IDENTITY, fixture.token, request)
        self.assertEqual(result.get("error"), "lifecycle_start_failed", result)
        self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
        self.assertEqual(fixture.store.get("existing").processes, [existing])

    def test_failed_extension_settlement_survives_wall_clock_jitter(self) -> None:
        # time.time() is not monotonic. A 1ms step back is already
        # replace_witness_stale (negative decision age); 61s past the stamp
        # trips the published bound. Either way the gate runs instead of
        # settlement, so the clock both sides of the comparison must be pinned.
        for label, delta in (("step_back", -0.001), ("past_bound", 61.0)):
            with self.subTest(clock=label):
                fixture = LifecycleFixture(confirmed_exit=True)
                try:
                    first, second = record(8301), record(8302)
                    run = fixture.add_run(first)
                    run.processes.append(second)
                    fixture.store.replace(run)
                    before = fixture.paths.runs_path.read_bytes()
                    request = fixture.request(run_id="existing")
                    jumped = request["replace_if_not_polling_since"] / 1000.0 + delta
                    with patch("time.time", return_value=jumped):
                        self._assert_failed_extension_restores_previous(
                            request, first, second, before, fixture
                        )
                finally:
                    fixture.close()

    def test_extension_final_write_failure_survives_wall_clock_jitter(self) -> None:
        # Same jitter as the sibling: fail_second turns a stale-witness
        # rollback into manual_cleanup_required, which is how this test
        # reported the flake even though the gate fired first.
        for label, delta in (("step_back", -0.001), ("past_bound", 61.0)):
            with self.subTest(clock=label):
                fixture = LifecycleFixture(confirmed_exit=True)
                try:
                    existing = record(8303)
                    fixture.add_run(existing)
                    before = fixture.paths.runs_path.read_bytes()
                    strong = record(fixture.launcher.handle.pid)
                    fixture.guard.snapshots[strong.pid] = identity(strong)
                    original_replace = fixture.store.replace
                    calls = 0

                    def fail_second(run):
                        nonlocal calls
                        calls += 1
                        if calls == 2:
                            raise OSError("disk full")
                        return original_replace(run)

                    fixture.store.replace = fail_second  # type: ignore[method-assign]
                    request = fixture.request(run_id="existing")
                    jumped = request["replace_if_not_polling_since"] / 1000.0 + delta
                    with patch("time.time", return_value=jumped):
                        self._assert_extension_final_write_restores_previous(
                            request, existing, before, fixture
                        )
                finally:
                    fixture.close()

    def test_new_run_terminal_settlement_still_manual_when_wall_clock_jumps(self) -> None:
        # Negative control: a brand-new run never stamps a witness. Clock
        # jitter must not change the durable STARTING / manual cleanup path.
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            fixture.store.replace = (  # type: ignore[method-assign]
                lambda _run: (_ for _ in ()).throw(OSError("disk full"))
            )
            request = fixture.request()
            jumped = time.time() - 0.001
            with patch("time.time", return_value=jumped):
                result = fixture.lifecycle.start_run(
                    IDENTITY, fixture.token, request
                )
            self.assertEqual(result["error"], "manual_cleanup_required")
            self.assertEqual(result["state"], "STARTING")
            self.assertIn("manifest_failed", result["cleanup_degraded"])
            self.assertEqual(fixture.store.get(result["run_id"]).state, "STARTING")
        finally:
            fixture.close()

    def test_extension_true_witness_is_refused_without_launch(self) -> None:
        # Clock freeze still has to call the real validator. The positives
        # stamp an admissible witness; a new run never enters this gate.
        # True is not an int (type(True) is bool): the product must refuse
        # with replace_witness_missing and never launch or terminate. If the
        # frozen wrapper is stubbed to return None, start_run proceeds and
        # this test fails (identity_unavailable + a launcher call).
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            existing = record(8310)
            fixture.add_run(existing)
            request = fixture.request(run_id="existing")
            request["replace_if_not_polling_since"] = True
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, request
            )
            self.assertEqual(result["error"], "replace_witness_missing", result)
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.guard.terminate_calls, [])
            self.assertEqual(fixture.store.get("existing").processes, [existing])
        finally:
            fixture.close()

    def test_failed_terminal_settlement_leaves_starting_and_requires_manual_cleanup(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            fixture.store.replace = (  # type: ignore[method-assign]
                lambda _run: (_ for _ in ()).throw(OSError("disk full"))
            )
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )
            self.assertEqual(result["error"], "manual_cleanup_required")
            self.assertEqual(result["state"], "STARTING")
            self.assertIn("manifest_failed", result["cleanup_degraded"])
            self.assertEqual(fixture.store.get(result["run_id"]).state, "STARTING")
        finally:
            fixture.close()


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


class LifecycleRecoveryAndOutcomeTest(unittest.TestCase):
    def test_stop_preflight_process_not_found_exits_without_terminate(self) -> None:
        fixture = LifecycleFixture()
        try:
            gone = record(8401)
            fixture.add_run(gone)
            fixture.guard.snapshots[gone.pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
            result = fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(
                result,
                {
                    "ok": True,
                    "run_id": "existing",
                    "state": "EXITED",
                    "terminated": 0,
                },
            )
            self.assertEqual(fixture.guard.terminate_calls, [])
            self.assertEqual(fixture.store.get("existing").state, "EXITED")
        finally:
            fixture.close()

    def test_stop_guard_exception_becomes_unreconciled_and_audited(self) -> None:
        fixture = LifecycleFixture()
        try:
            process = record(8402)
            fixture.add_run(process)
            fixture.guard.snapshots[process.pid] = identity(process)

            def fail_terminate(_record):
                raise TimeoutError("guard timeout")

            fixture.guard.terminate = fail_terminate  # type: ignore[method-assign]
            try:
                result = fixture.lifecycle.stop_run(
                    IDENTITY, fixture.token, "existing"
                )
            except Exception as exc:  # pragma: no cover - RED diagnostic
                self.fail(f"guard_exception_escaped:{type(exc).__name__}")
            self.assertEqual(result["error"], "partial_cleanup")
            self.assertEqual(fixture.store.get("existing").state, "UNRECONCILED")
            terminal = [
                event
                for event in fixture.audit.events
                if event.get("event") == "lifecycle_stop_outcome"
            ]
            self.assertEqual(terminal[-1]["decision"], "partial_cleanup")
        finally:
            fixture.close()

    def test_stopping_after_final_write_failure_remains_admin_reconcilable(self) -> None:
        fixture = LifecycleFixture()
        try:
            process = record(8403)
            fixture.add_run(process)
            fixture.guard.snapshots[process.pid] = identity(process)
            original_replace = fixture.store.replace
            calls = 0

            def fail_second(run):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disk full")
                return original_replace(run)

            fixture.store.replace = fail_second  # type: ignore[method-assign]
            result = fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(result["error"], "partial_cleanup")
            self.assertEqual(fixture.store.get("existing").state, "STOPPING")
            fixture.store.replace = original_replace  # type: ignore[method-assign]
            fixture.guard.snapshots[process.pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
            fixture.lifecycle.diag_probe = lambda: {"known": True, "processes": []}
            reconciled = fixture.lifecycle.admin_reconcile(
                "existing", process.pid, "write recovered"
            )
            self.assertEqual(reconciled.get("state"), "EXITED")
        finally:
            fixture.close()

    def test_restart_batch_recovers_transient_states_and_releases_running(self) -> None:
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            store.add(
                RunRecord("run", "s", "l", "RUNNING", "", "", "", "", [record(8501)])
            )
            store.add(
                RunRecord("start", "s", "l", "STARTING", "", "", "", "", [record(8502)])
            )
            store.add(
                RunRecord("stop", "s", "l", "STOPPING", "", "", "", "", [record(8503)])
            )
            if not hasattr(store, "recover_after_restart"):
                self.fail("recover_after_restart_missing")
            changed = store.recover_after_restart()
            self.assertEqual(changed["released"], ["run"])
            self.assertEqual(changed["unreconciled"], ["start", "stop"])
            self.assertEqual(store.get("run").state, "RUNNING_IDLE")
            self.assertEqual(store.get("start").state, "UNRECONCILED")
            self.assertEqual(store.get("stop").state, "UNRECONCILED")
            self.assertIsNone(store.get("start").owner_session_id)
            self.assertIsNone(store.get("stop").owner_lease_id)

    def test_nonempty_reconcile_rejects_unregistered_diag_pid_without_manifest_change(self) -> None:
        fixture = LifecycleFixture()
        try:
            survivor = record(8504)
            fixture.add_run(survivor, state="UNRECONCILED")
            fixture.guard.snapshots[survivor.pid] = identity(survivor)
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [
                    {"pid": survivor.pid, "name": "DayZDiag_x64.exe"},
                    {"pid": 99991, "name": "DayZDiag_x64.exe"},
                ],
            }
            before = fixture.paths.runs_path.read_bytes()
            result = fixture.lifecycle.admin_reconcile(
                "existing", survivor.pid, "manual"
            )
            self.assertEqual(result.get("error"), "manual_cleanup_required")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
        finally:
            fixture.close()

    def test_manual_cleanup_terminal_audit_is_redacted_and_failure_is_surfaced(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=False)
        try:
            fixture.audit.fail_events.add("lifecycle_start_outcome")
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )
            self.assertEqual(result["error"], "manual_cleanup_required")
            self.assertIn("audit_failed", result.get("cleanup_degraded", []))
            terminal = [
                event
                for event in fixture.audit.events
                if event.get("event") == "lifecycle_start_outcome"
            ][-1]
            self.assertEqual(terminal["decision"], "manual_cleanup_required")
            self.assertNotIn("argv", terminal)
            self.assertNotIn("lease_token", terminal)
            self.assertNotIn("command_line", str(terminal).casefold())
        finally:
            fixture.close()


class GuardFailureNormalizationTest(unittest.TestCase):
    def test_native_guard_timeout_and_spawn_error_are_normalized_fail_closed(self) -> None:
        def unavailable_process(_pid: int) -> object:
            raise OSError("native provider unavailable")

        with patch.object(
            native_process_guard,
            "psutil",
            SimpleNamespace(Process=unavailable_process),
        ):
            try:
                result = NativeProcessGuard().snapshot(123)
            except Exception as exc:  # pragma: no cover - RED diagnostic
                self.fail(f"guard_exception_escaped:{type(exc).__name__}")
        self.assertEqual(result["error"], "identity_unavailable")
        self.assertEqual(result["exit_code"], 3)


class GateEvidenceContractTest(unittest.TestCase):
    @staticmethod
    def _payload() -> dict[str, object]:
        missing = {
            "terminated": False,
            "error": "invalid_expected_identity",
            "exit_code": 3,
            "pid": None,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": False,
        }
        mismatch = {
            "terminated": False,
            "error": "process_identity_mismatch",
            "exit_code": 4,
            "pid": 102,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }
        return {
            "schema_version": 1,
            "gate": "task7_process_guard_registered_vs_foreign",
            "passed": True,
            "steps": {
                "registered_snapshot": {
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "foreign_snapshot": {
                    "pid": 102,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "missing_field_rejections": {
                    field: dict(missing)
                    for field in (
                        "pid",
                        "creation_time_utc",
                        "executable_sha256",
                        "command_line_sha256",
                    )
                },
                "forged_identity_rejections": {
                    field: dict(mismatch)
                    for field in (
                        "pid",
                        "creation_time_utc",
                        "executable_sha256",
                        "command_line_sha256",
                    )
                },
                "foreign_alive_after_rejections": True,
                "registered_termination": {
                    "terminated": True,
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "terminated_identity_recheck": {
                    "terminated": False,
                    "error": "process_not_found",
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": False,
                    "exit_code": 4,
                },
                "foreign_exact_cleanup": {
                    "terminated": True,
                    "pid": 102,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
            },
            "children": [
                {"slot": "registered", "pid": 101, "final_state": "exited"},
                {"slot": "foreign", "pid": 102, "final_state": "exited"},
            ],
            "errors": [],
        }

    def test_validator_requires_snapshots_forged_pid_and_cross_step_pid_consistency(self) -> None:
        payload = self._payload()
        self.assertEqual(validate_result_shape(payload), [])
        payload["steps"]["registered_snapshot"]["pid"] = 999
        self.assertIn("registered_pid_consistency", validate_result_shape(payload))

    def test_validator_rejects_legacy_or_absent_snapshot_identity_scheme(self) -> None:
        payload = self._payload()
        payload["steps"]["registered_snapshot"].pop("identity_scheme")
        self.assertIn("registered_snapshot", validate_result_shape(payload))

    def test_validator_rejects_snapshot_and_child_pid_or_identity_drift(self) -> None:
        payload = self._payload()
        payload["steps"]["registered_snapshot"]["identity_scheme"] = "legacy-wmi-v1"
        self.assertIn("registered_snapshot", validate_result_shape(payload))

        payload = self._payload()
        payload["steps"]["registered_snapshot"]["identity_complete"] = False
        self.assertIn("registered_snapshot", validate_result_shape(payload))

        payload = self._payload()
        payload["children"][0]["pid"] = True
        self.assertIn("child_final_state", validate_result_shape(payload))

        payload = self._payload()
        payload["children"][0]["pid"] = 0
        self.assertIn("child_final_state", validate_result_shape(payload))

    def test_validator_rejects_cross_child_pid_alias_after_coherent_foreign_rewrite(self) -> None:
        payload = self._payload()
        registered_pid = payload["children"][0]["pid"]
        payload["children"][1]["pid"] = registered_pid
        payload["steps"]["foreign_snapshot"]["pid"] = registered_pid
        for result in payload["steps"]["forged_identity_rejections"].values():
            result["pid"] = registered_pid
        payload["steps"]["foreign_exact_cleanup"]["pid"] = registered_pid

        self.assertEqual(validate_result_shape(payload), ["child_pid_alias"])

    def test_validator_rejects_missing_rejection_native_contract_drift(self) -> None:
        for field, value in (
            ("identity_scheme", "legacy-wmi-v1"),
            ("identity_complete", True),
            ("pid", 102),
        ):
            with self.subTest(field=field):
                payload = self._payload()
                payload["steps"]["missing_field_rejections"]["pid"][field] = value
                self.assertIn("missing_field_contract", validate_result_shape(payload))

    def test_validator_rejects_forged_rejection_native_contract_drift(self) -> None:
        for field, value in (
            ("identity_scheme", "legacy-wmi-v1"),
            ("identity_complete", False),
            ("pid", 101),
        ):
            with self.subTest(field=field):
                payload = self._payload()
                payload["steps"]["forged_identity_rejections"]["pid"][field] = value
                self.assertIn("forged_identity_contract", validate_result_shape(payload))

    def test_validator_rejects_termination_native_contract_drift(self) -> None:
        for step, mutations in (
            (
                "registered_termination",
                (
                    ("identity_scheme", "legacy-wmi-v1", "registered_termination"),
                    ("identity_complete", False, "registered_termination"),
                    ("pid", 102, "registered_termination_pid"),
                ),
            ),
            (
                "foreign_exact_cleanup",
                (
                    ("identity_scheme", "legacy-wmi-v1", "foreign_exact_cleanup"),
                    ("identity_complete", False, "foreign_exact_cleanup"),
                    ("pid", 101, "foreign_exact_cleanup_pid"),
                ),
            ),
            (
                "terminated_identity_recheck",
                (
                    ("identity_scheme", "legacy-wmi-v1", "terminated_identity_recheck"),
                    ("identity_complete", True, "terminated_identity_recheck"),
                    ("pid", 102, "terminated_identity_recheck"),
                ),
            ),
        ):
            for field, value, expected_error in mutations:
                with self.subTest(step=step, field=field):
                    payload = self._payload()
                    payload["steps"][step][field] = value
                    self.assertIn(expected_error, validate_result_shape(payload))


class ToolHelpCloseFailureTest(unittest.TestCase):
    def test_invalid_handle_and_close_failure_are_unknown_fail_closed(self) -> None:
        class Kernel32:
            def __init__(self, mode: str) -> None:
                self.mode = mode

            def CreateToolhelp32Snapshot(self, _flags, _pid):
                if self.mode == "invalid":
                    return ctypes.c_void_p(-1).value
                return 123

            def Process32First(self, _snapshot, pointer):
                entry = pointer._obj
                entry.th32ProcessID = 1
                entry.szExeFile = b"python.exe"
                return 1

            def Process32Next(self, _snapshot, _pointer):
                ctypes.set_last_error(orphan_guard._ERROR_NO_MORE_FILES)
                return 0

            def CloseHandle(self, _handle):
                return 0 if self.mode == "close" else 1

        for mode in ("invalid", "close"):
            with self.subTest(mode=mode), patch.object(
                orphan_guard, "_IS_WINDOWS", True
            ), patch.object(orphan_guard, "_k32", Kernel32(mode)):
                self.assertEqual(
                    orphan_guard.snapshot_processes_by_name(["DayZDiag_x64.exe"]),
                    {"known": False, "processes": []},
                )


if __name__ == "__main__":
    unittest.main()
