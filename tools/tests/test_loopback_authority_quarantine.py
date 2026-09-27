"""Loopback dispatch under lease authority, quarantine and I/O boundaries.

Moved verbatim from test_task7_review_regressions.py, test_task7_rereview_regressions.py, test_task7_final_authority_regressions.py
(review 2026-09-25, W4d step 3).
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, session_coordination as coordination_module
from dayz_mcp.session_coordination import SessionCoordinator
from tests.fence_helpers import accredited_poll, bind_both_peers, bound_queue
from tests.lifecycle_helpers import (
    Clock,
    IDENTITY,
    IDENTITY_B,
    IDENTITY_PAYLOAD,
    Sequence,
)


# --- from test_task7_review_regressions.py ---


class DispatchAuthorityAndQuarantineTest(unittest.TestCase):
    def _state(self, *, clock: Clock | None = None):
        state = loopback.ServerState("key", time_fn=clock)
        bind_both_peers(state)
        events: list[dict[str, object]] = []
        coordinator = SessionCoordinator(
            time_fn=clock or __import__("time").monotonic,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda event: events.append(dict(event)) or True,
            cleanup=lambda session, lease, reason, active: state.cleanup_owner(
                session, lease, reason, active
            ),
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        _, acquired = coordinator.acquire(IDENTITY, "dispatch")
        return state, coordinator, acquired, events

    def test_committed_unpolled_mutation_does_not_prevent_expiry_or_dispatch(self) -> None:
        clock = Clock()
        state, coordinator, acquired, _ = self._state(clock=clock)
        status, queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
            operation_timeout_s=0.0,
        )
        self.assertEqual(status, 200)
        clock.advance(121.0)
        with patch.object(loopback, "COMMAND_TTL_S", 1000.0):
            polled = accredited_poll(state, "server")[1]
        self.assertEqual(polled["commands"], [])
        self.assertEqual(coordinator.status(IDENTITY)["self"]["state"], "none")
        self.assertIn(
            state.take_result(queued["id"])["error"],
            {"lease_expired", "authority_expired", "owner_release", "lease_inactive"},
        )

    def test_retail_appearing_before_poll_discards_mutation_but_delivers_read(self) -> None:
        state, _coordinator, acquired, events = self._state()
        mutation = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )[1]
        read = state.enqueue_command(
            "query_player_state", {}, "server", identity_payload=IDENTITY_PAYLOAD
        )[1]
        state.retail_probe = lambda: {
            "known": True,
            "processes": [{"pid": 42, "name": "DayZ_x64.exe"}],
        }
        commands = accredited_poll(state, "server")[1]["commands"]
        self.assertEqual([item["id"] for item in commands], [read["id"]])
        self.assertEqual(state.take_result(mutation["id"])["error"], "retail_quarantine")
        self.assertIn("session_rejected", [event.get("event") for event in events])

    def test_unknown_snapshot_before_poll_discards_mutation(self) -> None:
        state, _coordinator, acquired, _events = self._state()
        queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )[1]
        state.retail_probe = lambda: {"known": False, "processes": []}
        self.assertEqual(accredited_poll(state, "server")[1]["commands"], [])
        self.assertEqual(state.take_result(queued["id"])["error"], "retail_quarantine")

    def test_missing_probe_with_coordination_is_unknown_and_fail_closed(self) -> None:
        state, _coordinator, acquired, _events = self._state()
        state.retail_probe = None
        status, result = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )
        self.assertEqual((status, result["error"]), (409, "retail_quarantine"))
        self.assertEqual(accredited_poll(state, "server")[1]["commands"], [])

    def test_dispatch_claim_wins_before_release_and_delivers_exactly_once(self) -> None:
        state, coordinator, acquired, _events = self._state()
        queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )[1]
        claimed = threading.Event()
        resume = threading.Event()
        original = coordinator.claim_dispatch

        def paused(*args, **kwargs):
            result = original(*args, **kwargs)
            claimed.set()
            self.assertTrue(resume.wait(2.0))
            return result

        coordinator.claim_dispatch = paused  # type: ignore[method-assign]
        poll_result: list[tuple[int, dict]] = []
        release_result: list[tuple[int, dict]] = []
        poll = threading.Thread(target=lambda: poll_result.append(accredited_poll(state, "server")))
        poll.start()
        self.assertTrue(claimed.wait(1.0))
        release = threading.Thread(
            target=lambda: release_result.append(
                coordinator.release(IDENTITY, acquired["lease_token"])
            )
        )
        release.start()
        resume.set()
        poll.join(2.0)
        release.join(2.0)
        self.assertFalse(poll.is_alive() or release.is_alive())
        self.assertEqual([item["id"] for item in poll_result[0][1]["commands"]], [queued["id"]])
        self.assertEqual(release_result[0][1]["cleanup"]["cancelled"], 0)

    def test_release_wins_before_dispatch_claim_and_delivers_nothing(self) -> None:
        state, coordinator, acquired, _events = self._state()
        queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )[1]
        before_claim = threading.Event()
        resume = threading.Event()
        original = coordinator.claim_dispatch

        def paused(*args, **kwargs):
            before_claim.set()
            self.assertTrue(resume.wait(2.0))
            return original(*args, **kwargs)

        coordinator.claim_dispatch = paused  # type: ignore[method-assign]
        poll_result: list[tuple[int, dict]] = []
        release_result: list[tuple[int, dict]] = []
        poll = threading.Thread(target=lambda: poll_result.append(accredited_poll(state, "server")))
        poll.start()
        self.assertTrue(before_claim.wait(1.0))
        release = threading.Thread(
            target=lambda: release_result.append(
                coordinator.release(IDENTITY, acquired["lease_token"])
            )
        )
        release.start()
        deadline = __import__("time").monotonic() + 1.0
        while coordinator.snapshot_payload()["active"] is not None:
            if __import__("time").monotonic() >= deadline:
                self.fail("release did not invalidate before claim")
        resume.set()
        poll.join(2.0)
        release.join(2.0)
        self.assertFalse(poll.is_alive() or release.is_alive())
        self.assertEqual(poll_result[0][1]["commands"], [])
        self.assertEqual(state.take_result(queued["id"])["error"], "lease_inactive")


# --- from test_task7_rereview_regressions.py ---


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


# --- from test_task7_final_authority_regressions.py ---


class StateLockIoBoundaryTest(unittest.TestCase):
    @staticmethod
    def _lock_observer(state: loopback.ServerState, observations: list[bool]) -> None:
        acquired: list[bool] = []

        def probe() -> None:
            locked = state._lock.acquire(timeout=0.10)
            acquired.append(locked)
            if locked:
                state._lock.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(0.25)
        observations.append(acquired == [True])

    def test_expiry_discovered_before_commit_never_audits_under_state_lock(self) -> None:
        clock = Clock()
        observations: list[bool] = []
        state_holder: list[loopback.ServerState] = []

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_expired":
                self._lock_observer(state_holder[0], observations)
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
            cleanup_timeout_s=5.0,
        )
        state = loopback.ServerState(
            "key", time_fn=clock, coordination=coordinator
        )
        bind_both_peers(state)
        state_holder.append(state)
        advanced = False

        def retail_probe():
            nonlocal advanced
            if not advanced:
                advanced = True
                clock.advance(121.0)
            return {"known": True, "processes": []}

        state.retail_probe = retail_probe
        _, lease = coordinator.acquire(IDENTITY, "commit-expiry")
        # Release audits are awaited for min(RELEASE_AUDIT_TIMEOUT_S, cleanup budget
        # left) and may land after the call; widen both so session_expired is written
        # while enqueue_command is still inside expire_due(), where the probe looks.
        with patch.object(coordination_module, "RELEASE_AUDIT_TIMEOUT_S", 5.0):
            status, payload = state.enqueue_command(
                "world_time_set",
                {},
                "server",
                identity_payload=IDENTITY_PAYLOAD,
                lease_token=lease["lease_token"],
            )
        self.assertEqual((status, payload["error"]), (409, "lease_invalid"))
        self.assertEqual(observations, [True])

    def test_expiry_discovered_before_claim_never_audits_under_state_lock(self) -> None:
        clock = Clock()
        observations: list[bool] = []
        state_holder: list[loopback.ServerState] = []

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_expired":
                self._lock_observer(state_holder[0], observations)
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
            cleanup_timeout_s=0.05,
        )
        state = loopback.ServerState(
            "key", time_fn=clock, coordination=coordinator
        )
        bind_both_peers(state)
        state_holder.append(state)
        state.retail_probe = lambda: {"known": True, "processes": []}
        _, lease = coordinator.acquire(IDENTITY, "claim-expiry")
        queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=lease["lease_token"],
        )[1]
        clock.advance(121.0)
        # Isolate the lease-claim boundary from the independent 30 s queue TTL.
        with state._lock:
            state._enqueued_at[queued["id"]] = clock.now
        _, polled = accredited_poll(state, "server")
        self.assertEqual(polled["commands"], [])
        self.assertEqual(state.take_result(queued["id"])["error"], "lease_inactive")
        self.assertEqual(observations, [True])

    def test_read_only_poll_skips_retail_probe_entirely(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda _event: True,
        )
        state = loopback.ServerState("key", coordination=coordinator)
        bind_both_peers(state)
        status, queued = state.enqueue_command(
            "query_player_state",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
        )
        self.assertEqual(status, 200)
        probe_called = threading.Event()
        resume_probe = threading.Event()

        def blocking_probe():
            probe_called.set()
            resume_probe.wait(2.0)
            return {"known": True, "processes": []}

        state.retail_probe = blocking_probe
        polled: list[tuple[int, dict]] = []
        thread = threading.Thread(target=lambda: polled.append(accredited_poll(state, "server")))
        thread.start()
        thread.join(0.20)
        progressed = not thread.is_alive()
        try:
            self.assertTrue(progressed, "read_poll_blocked_by_retail_probe")
            self.assertFalse(probe_called.is_set())
        finally:
            resume_probe.set()
            thread.join(2.0)
        self.assertEqual([item["id"] for item in polled[0][1]["commands"]], [queued["id"]])

    def test_mutation_probe_runs_outside_state_lock_and_read_enqueue_progresses(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda _event: True,
        )
        state = loopback.ServerState("key", coordination=coordinator)
        bind_both_peers(state)
        state.retail_probe = lambda: {"known": True, "processes": []}
        _, lease = coordinator.acquire(IDENTITY, "poll-probe")
        state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=lease["lease_token"],
        )

        probe_entered = threading.Event()
        resume_probe = threading.Event()
        lock_observations: list[bool] = []

        def blocking_probe():
            self._lock_observer(state, lock_observations)
            probe_entered.set()
            resume_probe.wait(2.0)
            return {"known": True, "processes": []}

        state.retail_probe = blocking_probe
        poll_thread = threading.Thread(target=lambda: accredited_poll(state, "server"))
        poll_thread.start()
        self.assertTrue(probe_entered.wait(1.0))

        read_result: list[tuple[int, dict]] = []
        read_thread = threading.Thread(
            target=lambda: read_result.append(
                state.enqueue_command(
                    "query_player_state",
                    {},
                    "server",
                    identity_payload=IDENTITY_PAYLOAD,
                )
            )
        )
        read_thread.start()
        read_progressed = read_thread.join(0.20) is None and not read_thread.is_alive()
        try:
            self.assertEqual(lock_observations, [True])
            self.assertTrue(read_progressed, "read_enqueue_blocked_by_poll_probe")
        finally:
            resume_probe.set()
            poll_thread.join(2.0)
            read_thread.join(2.0)
        self.assertEqual(read_result[0][0], 200)


class ExecEnforceCapacityReservationTest(unittest.TestCase):
    def test_cancel_pending_fences_exec_allowed_before_publish(self) -> None:
        audit_entered = threading.Event()
        resume_audit = threading.Event()
        audit_events: list[tuple[str, int | None]] = []

        def audit(_expr: str, decision: str, _main_fn: str, command_id) -> None:
            audit_events.append((decision, command_id))
            if decision == "allowed":
                audit_entered.set()
                resume_audit.wait(2.0)

        state = loopback.ServerState(
            "key",
            enable_exec_enforce=True,
            exec_allowlist={"allowed_expr"},
            exec_audit=audit,
        )
        bind_both_peers(state)
        result: list[tuple[int, dict]] = []
        enqueue_thread = threading.Thread(
            target=lambda: result.append(
                state.enqueue_command(
                    "exec_enforce",
                    {"expr": "allowed_expr", "main_fn": "main"},
                    "server",
                )
            )
        )
        enqueue_thread.start()
        self.assertTrue(audit_entered.wait(1.0))
        state.cancel_pending()
        resume_audit.set()
        enqueue_thread.join(2.0)

        self.assertFalse(enqueue_thread.is_alive())
        self.assertEqual(result[0][0], 409)
        self.assertEqual(result[0][1]["error"], "enqueue_cancelled")
        decisions = [decision for decision, _command_id in audit_events]
        self.assertEqual(decisions, ["allowed", "discarded"])
        with state._lock:
            self.assertEqual(state._queues["server"], [])
            self.assertEqual(bound_queue(state, "server"), [])
            self.assertEqual(state._exec_capacity_reserved["server"], 0)

    def test_last_queue_slot_cannot_emit_phantom_allowed_audit(self) -> None:
        audit_entered = threading.Event()
        resume_audit = threading.Event()
        audit_events: list[tuple[str, int | None]] = []

        def audit(_expr: str, decision: str, _main_fn: str, command_id) -> None:
            audit_events.append((decision, command_id))
            if decision == "allowed" and len(audit_events) == 1:
                audit_entered.set()
                resume_audit.wait(2.0)

        state = loopback.ServerState(
            "key",
            enable_exec_enforce=True,
            exec_allowlist={"allowed_expr"},
            exec_audit=audit,
        )
        bind_both_peers(state)
        with state._lock:
            state._queues["server"] = [
                {"id": -(index + 1), "cmd": "noop", "args": {}}
                for index in range(loopback.MAX_QUEUE - 1)
            ]

        first: list[tuple[int, dict]] = []
        first_thread = threading.Thread(
            target=lambda: first.append(
                state.enqueue_command(
                    "exec_enforce",
                    {"expr": "allowed_expr", "main_fn": "main"},
                    "server",
                )
            )
        )
        first_thread.start()
        self.assertTrue(audit_entered.wait(1.0))
        second = state.enqueue_command(
            "exec_enforce",
            {"expr": "allowed_expr", "main_fn": "main"},
            "server",
        )
        resume_audit.set()
        first_thread.join(2.0)

        self.assertFalse(first_thread.is_alive())
        self.assertEqual(sorted((first[0][0], second[0])), [200, 429])
        allowed_ids = [
            command_id for decision, command_id in audit_events if decision == "allowed"
        ]
        self.assertEqual(len(allowed_ids), 1)
        with state._lock:
            legacy_ids = {command["id"] for command in state._queues["server"]}
            published_ids = set(legacy_ids) | {
                command["id"] for command in bound_queue(state, "server")
            }
        self.assertIn(allowed_ids[0], published_ids)
        self.assertNotIn(allowed_ids[0], legacy_ids)


class OwnerReadRevalidationTest(unittest.TestCase):
    def test_stale_owner_read_is_published_unowned_after_l2_wins(self) -> None:
        audit_entered = threading.Event()
        resume_audit = threading.Event()
        blocked = False

        def audit(event: dict[str, object]) -> bool:
            nonlocal blocked
            if (
                event.get("event") == "session_authorized"
                and event.get("decision") == "owner_read"
                and not blocked
            ):
                blocked = True
                audit_entered.set()
                resume_audit.wait(2.0)
            return True

        state = loopback.ServerState("key", coordination=None)
        bind_both_peers(state)
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda session, lease, reason, active: state.cleanup_owner(
                session, lease, reason, active
            ),
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        result: list[tuple[int, dict]] = []
        thread = threading.Thread(
            target=lambda: result.append(
                state.enqueue_command(
                    "query_player_state",
                    {},
                    "server",
                    identity_payload=IDENTITY_PAYLOAD,
                    lease_token=l1["lease_token"],
                )
            )
        )
        thread.start()
        self.assertTrue(audit_entered.wait(1.0))
        coordinator.release(IDENTITY, l1["lease_token"])
        acquire_started = threading.Event()
        acquire_result: list[tuple[int, dict]] = []

        def acquire_l2() -> None:
            acquire_started.set()
            acquire_result.append(coordinator.acquire(IDENTITY, "l2"))

        acquire_thread = threading.Thread(target=acquire_l2)
        acquire_thread.start()
        self.assertTrue(acquire_started.wait(1.0))
        resume_audit.set()
        thread.join(2.0)
        acquire_thread.join(2.0)
        self.assertFalse(thread.is_alive())
        self.assertFalse(acquire_thread.is_alive())
        acquire_status, acquire_payload = acquire_result[0]
        self.assertIn(acquire_status, (200, 202, 503))
        if acquire_status == 503:
            self.assertEqual(acquire_payload["error"], "audit_failed")
            deadline = time.monotonic() + 0.50
            while acquire_status == 503 and time.monotonic() < deadline:
                time.sleep(0.005)
                acquire_status, acquire_payload = coordinator.acquire(IDENTITY, "l2")
        if acquire_status == 202:
            status, l2 = coordinator.wait(
                IDENTITY, acquire_payload["ticket"], 0.50
            )
            self.assertEqual(status, 200)
        else:
            self.assertEqual(acquire_status, 200)
            l2 = acquire_payload
        with coordinator._condition:
            l2_expiry = coordinator._active.expires_at

        self.assertEqual(result[0][0], 200)
        command_id = result[0][1]["id"]
        self.assertNotIn(command_id, state._command_owner)
        self.assertEqual(state.pending_for_owner(IDENTITY.session_id), 0)
        with coordinator._condition:
            self.assertEqual(coordinator._active.expires_at, l2_expiry)
        released = coordinator.release(IDENTITY, l2["lease_token"])[1]
        self.assertEqual(released["cleanup"].get("cancelled"), 0)
        _, polled = accredited_poll(state, "server")
        self.assertEqual([item["id"] for item in polled["commands"]], [command_id])


if __name__ == "__main__":
    unittest.main()
