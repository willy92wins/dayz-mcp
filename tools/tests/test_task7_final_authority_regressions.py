from __future__ import annotations

import json
import threading
import time
import unittest
from tempfile import TemporaryDirectory

from dayz_mcp import loopback, server, session_coordination as coordination_module
from dayz_mcp.runtime_state import CoordinationSnapshotStore, RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.test_task7_review_regressions import (
    Clock,
    IDENTITY,
    IDENTITY_PAYLOAD,
    Sequence,
)
from tests.test_task7_rereview_regressions import IDENTITY_B
from tests.fence_helpers import INST_SERVER, accredited_poll, bind_both_peers, bound_queue


def wait_until(predicate, timeout_s: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class ReleasingBudgetTest(unittest.TestCase):
    def test_stuck_release_audit_bounds_direct_followup_grant(self) -> None:
        audit_entered = threading.Event()
        resume_audit = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if event.get("event") == "session_release_started":
                audit_entered.set()
                resume_audit.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
            cleanup_timeout_s=0.03,
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        release_status, release_payload = coordinator.release(
            IDENTITY, l1["lease_token"]
        )
        self.assertTrue(audit_entered.is_set())
        self.assertEqual(release_status, 200)
        self.assertEqual(release_payload["cleanup_degraded"], [])
        self.assertNotIn("audit_failed", release_payload["cleanup_degraded"])

        progress = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        during = coordinator.snapshot_payload()
        self.assertEqual((progress[0], progress[1]["status"]), (202, "queued"))
        self.assertIsNone(during["active"])
        self.assertIsNone(during["releasing"])
        self.assertIsNone(during["granting"])
        self.assertTrue(during["handoff_pending"])

        resume_audit.set()
        with coordinator._condition:
            terminalized = coordinator._condition.wait_for(
                lambda: not coordinator._handoff_pending, timeout=1.0
            )
        self.assertTrue(terminalized)
        granted = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        self.assertEqual((granted[0], granted[1]["status"]), (200, "active"))

    def test_handoff_clear_advances_revision_and_persists_false(self) -> None:
        entered = threading.Event()
        resume = threading.Event()
        release_done = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_release_started":
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
            cleanup_timeout_s=0.03,
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        release_thread = threading.Thread(
            target=lambda: (
                coordinator.release(IDENTITY, l1["lease_token"]),
                release_done.set(),
            )
        )

        with TemporaryDirectory() as temp_dir:
            store = CoordinationSnapshotStore(
                RuntimePaths.from_env({"LOCALAPPDATA": temp_dir}), "generation-a"
            )
            release_thread.start()
            self.assertTrue(entered.wait(1.0))
            pending = coordinator.snapshot_payload()
            self.assertTrue(pending["handoff_pending"])
            self.assertTrue(store.write_coordination(pending))
            self.assertTrue(release_done.wait(0.20))
            still_pending = coordinator.snapshot_payload()
            self.assertTrue(still_pending["handoff_pending"])

            resume.set()
            with coordinator._condition:
                terminalized = coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=1.0
                )
            self.assertTrue(terminalized)
            release_thread.join(2.0)
            self.assertFalse(release_thread.is_alive())
            cleared = coordinator.snapshot_payload()
            self.assertFalse(cleared["handoff_pending"])
            self.assertGreater(cleared["revision"], pending["revision"])
            self.assertTrue(store.write_coordination(cleared))
            persisted = json.loads(
                store.coordination_path.read_text(encoding="utf-8")
            )
            self.assertFalse(persisted["handoff_pending"])

    def test_post_cleanup_ticket_expiry_audit_is_bounded(self) -> None:
        clock = Clock()
        audit_entered = threading.Event()
        resume_audit = threading.Event()
        release_done = threading.Event()
        release_result: list[tuple[int, dict]] = []

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "ticket_cancelled":
                audit_entered.set()
                resume_audit.wait(2.0)
            return True

        def cleanup(*_args) -> dict:
            clock.advance(2.0)
            return {}

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=cleanup,
            cleanup_timeout_s=0.03,
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        clock.advance(119.0)

        release_thread = threading.Thread(
            target=lambda: (
                release_result.append(
                    coordinator.release(IDENTITY, l1["lease_token"])
                ),
                release_done.set(),
            )
        )
        release_thread.start()
        self.assertTrue(audit_entered.wait(1.0))
        self.assertTrue(release_done.wait(0.20), "release_held_by_ticket_audit")
        self.assertEqual(release_result[0][1]["cleanup_degraded"], [])
        self.assertNotIn("audit_failed", release_result[0][1]["cleanup_degraded"])
        during = coordinator.snapshot_payload()
        self.assertIsNone(during["active"])
        self.assertIsNone(during["releasing"])
        self.assertTrue(during["handoff_pending"])
        self.assertEqual(during["queue"], [])
        self.assertNotEqual(ticket["ticket"], "")

        resume_audit.set()
        with coordinator._condition:
            terminalized = coordinator._condition.wait_for(
                lambda: not coordinator._handoff_pending, timeout=1.0
            )
        self.assertTrue(terminalized)
        release_thread.join(2.0)
        self.assertFalse(release_thread.is_alive())

    def test_stuck_release_audit_cannot_pin_handoff_or_followup_calls(self) -> None:
        audit_entered = threading.Event()
        resume_audit = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_release_started":
                audit_entered.set()
                resume_audit.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
            cleanup_timeout_s=0.03,
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        queued_status, ticket = coordinator.acquire(IDENTITY_B, "l2")
        self.assertEqual(queued_status, 202)

        release_status, release_payload = coordinator.release(
            IDENTITY, l1["lease_token"]
        )
        self.assertTrue(audit_entered.is_set())
        self.assertEqual(release_status, 200)
        self.assertEqual(release_payload["cleanup_degraded"], [])
        self.assertNotIn("audit_failed", release_payload["cleanup_degraded"])

        status_payload = coordinator.status(IDENTITY_B)
        progress = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        snapshot = coordinator.snapshot_payload()
        self.assertEqual(status_payload["self"]["state"], "queued")
        self.assertEqual(status_payload["cleanup_degraded"], [])
        self.assertEqual((progress[0], progress[1]["status"]), (202, "queued"))
        self.assertTrue(snapshot["handoff_pending"])
        self.assertIsNone(snapshot["active"])
        self.assertIsNone(snapshot["granting"])

        resume_audit.set()
        with coordinator._condition:
            terminalized = coordinator._condition.wait_for(
                lambda: not coordinator._handoff_pending, timeout=1.0
            )
        self.assertTrue(terminalized)
        granted = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        self.assertEqual((granted[0], granted[1]["status"]), (200, "active"))

    def test_started_timeout_and_finished_audits_cannot_hold_fifo_past_budget(self) -> None:
        for blocked_event in (
            "session_release_started",
            "session_cleanup_timeout",
            "session_release_finished",
        ):
            with self.subTest(blocked_event=blocked_event):
                audit_entered = threading.Event()
                resume_audit = threading.Event()
                resume_cleanup = threading.Event()

                def audit(event: dict[str, object]) -> bool:
                    if event.get("event") == blocked_event:
                        audit_entered.set()
                        resume_audit.wait(2.0)
                    return True

                def cleanup(*_args):
                    if blocked_event == "session_cleanup_timeout":
                        resume_cleanup.wait(2.0)
                    return {"cancelled": 0}

                coordinator = SessionCoordinator(
                    token_fn=Sequence("token"),
                    id_fn=Sequence("lease"),
                    audit=audit,
                    cleanup=cleanup,
                    cleanup_timeout_s=0.02,
                )
                _, l1 = coordinator.acquire(IDENTITY, "l1")
                _, ticket = coordinator.acquire(IDENTITY_B, "l2")
                result: list[tuple[int, dict[str, object]]] = []
                release_done = threading.Event()

                def release() -> None:
                    result.append(
                        coordinator.release(IDENTITY, l1["lease_token"])
                    )
                    release_done.set()

                thread = threading.Thread(
                    target=release
                )
                thread.start()
                self.assertTrue(
                    audit_entered.wait(1.0), f"audit_not_reached:{blocked_event}"
                )
                self.assertTrue(
                    release_done.wait(0.20),
                    f"release_not_bounded:{blocked_event}",
                )
                during = coordinator.snapshot_payload()
                progress = coordinator.wait(
                    IDENTITY_B, ticket["ticket"], 0.0
                )
                self.assertIsNone(during["active"])
                self.assertTrue(during["handoff_pending"])
                self.assertEqual(
                    (progress[0], progress[1]["status"]), (202, "queued")
                )

                resume_audit.set()
                resume_cleanup.set()
                with coordinator._condition:
                    terminalized = coordinator._condition.wait_for(
                        lambda: not coordinator._handoff_pending, timeout=1.0
                    )
                thread.join(2.0)
                self.assertTrue(
                    terminalized, f"release_fence_not_terminal:{blocked_event}"
                )
                self.assertFalse(thread.is_alive())
                granted = coordinator.wait(
                    IDENTITY_B, ticket["ticket"], 0.0
                )
                self.assertEqual(
                    (granted[0], granted[1]["status"]), (200, "active")
                )
                self.assertEqual(result[0][0], 200)
class CleanupWorkerCapacityTest(unittest.TestCase):
    def test_timed_out_workers_are_capped_and_saturation_advances_fifo(self) -> None:
        expected_capacity = 4
        resume_cleanup = threading.Event()
        starts = 0
        starts_lock = threading.Lock()

        def cleanup(*_args):
            nonlocal starts
            with starts_lock:
                starts += 1
            resume_cleanup.wait(2.0)
            return {"cancelled": 0}

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda _event: True,
            cleanup=cleanup,
            cleanup_timeout_s=0.01,
        )
        try:
            for index in range(expected_capacity):
                status, lease = coordinator.acquire(IDENTITY, f"fill-{index}")
                self.assertEqual(status, 200)
                released = coordinator.release(IDENTITY, lease["lease_token"])[1]
                self.assertIn("cleanup_timeout", released["cleanup_degraded"])

            _, saturated_lease = coordinator.acquire(IDENTITY, "saturated")
            coordinator.acquire(IDENTITY_B, "fifo")
            saturated = coordinator.release(
                IDENTITY, saturated_lease["lease_token"]
            )[1]
            snapshot = coordinator.snapshot_payload()

            self.assertEqual(starts, expected_capacity)
            self.assertIn(
                "cleanup_worker_saturated", saturated["cleanup_degraded"]
            )
            self.assertIsNone(snapshot["active"])
            self.assertEqual(snapshot["queue"][0]["session"], IDENTITY_B.session_id[:12])
            granted = coordinator.wait(
                IDENTITY_B, snapshot["queue"][0]["ticket"], 0.0
            )
            self.assertEqual((granted[0], granted[1]["status"]), (200, "active"))
            self.assertEqual(
                snapshot.get("cleanup_workers"),
                {
                    "capacity": expected_capacity,
                    "active": expected_capacity,
                    "saturated": 1,
                },
            )
        finally:
            resume_cleanup.set()
            wait_until(
                lambda: (
                    coordinator.snapshot_payload().get("cleanup_workers") or {}
                ).get("active")
                == 0,
                1.0,
            )


class GrantLedgerLinearizationTest(unittest.TestCase):
    def test_wait_zero_revalidates_fifo_grant_after_audit_before_publish(self) -> None:
        commit_entered = threading.Event()
        allow_commit = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                commit_entered.set()
                allow_commit.wait(1.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        coordinator.release(IDENTITY, l1["lease_token"])
        result: list[tuple[int, dict]] = []
        wait_thread = threading.Thread(
            target=lambda: result.append(
                coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
            )
        )
        wait_thread.start()
        self.assertTrue(commit_entered.wait(1.0))
        during = coordinator.snapshot_payload()

        self.assertIsNone(during["active"])
        self.assertEqual(during["granting"]["ticket"], ticket["ticket"])
        self.assertEqual(during["queue"][0]["ticket"], ticket["ticket"])
        allow_commit.set()
        wait_thread.join(2.0)
        self.assertFalse(wait_thread.is_alive())
        self.assertEqual((result[0][0], result[0][1]["status"]), (200, "active"))
        commit = next(
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        )
        self.assertEqual(
            coordinator.snapshot_payload()["active"]["lease_id"],
            commit["lease_id"],
        )

    def test_wait_response_matches_granting_audit_if_publish_wins_callback(self) -> None:
        commit_entered = threading.Event()
        allow_commit = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                commit_entered.set()
                allow_commit.wait(1.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        coordinator.release(IDENTITY, l1["lease_token"])
        owner_result: list[tuple[int, dict]] = []
        owner_wait = threading.Thread(
            target=lambda: owner_result.append(
                coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
            )
        )
        owner_wait.start()
        self.assertTrue(commit_entered.wait(1.0))

        progress = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        during = coordinator.snapshot_payload()
        self.assertEqual((progress[0], progress[1]["status"]), (202, "queued"))
        self.assertNotIn("lease_token", progress[1])
        self.assertEqual(during["granting"]["ticket"], ticket["ticket"])
        runtime = object.__new__(server.ClientRuntime)
        runtime._control = object.__new__(server.ControlClient)
        runtime._control._state_lock = threading.Lock()
        runtime._control._remember_acquire_or_wait(progress[1], None)
        self.assertEqual(runtime.active_ticket, ticket["ticket"])

        allow_commit.set()
        owner_wait.join(2.0)
        self.assertFalse(owner_wait.is_alive())
        self.assertEqual((owner_result[0][0], owner_result[0][1]["status"]), (200, "active"))

    def test_wait_response_stays_queued_if_grant_requeues_during_callback(self) -> None:
        commit_entered = threading.Event()
        allow_commit = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                commit_entered.set()
                allow_commit.wait(1.0)
                return False
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        coordinator.release(IDENTITY, l1["lease_token"])
        owner_result: list[tuple[int, dict]] = []
        owner_wait = threading.Thread(
            target=lambda: owner_result.append(
                coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
            )
        )
        owner_wait.start()
        self.assertTrue(commit_entered.wait(1.0))

        progress = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        self.assertEqual((progress[0], progress[1]["status"]), (202, "queued"))
        self.assertEqual(progress[1]["ticket"], ticket["ticket"])
        allow_commit.set()
        owner_wait.join(2.0)
        self.assertFalse(owner_wait.is_alive())

        self.assertEqual(
            (owner_result[0][0], owner_result[0][1]["error"]),
            (503, "audit_failed"),
        )
        snapshot = coordinator.snapshot_payload()
        self.assertIsNone(snapshot["active"])
        self.assertIsNone(snapshot["granting"])
        self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event.get("event") == "session_grant_revoked"
                ]
            ),
            1,
        )

    def test_concurrent_same_client_queue_acquire_is_one_durable_ticket(self) -> None:
        first_audit_entered = threading.Event()
        resume_first_audit = threading.Event()
        queued_events: list[str] = []
        blocked_once = False

        def audit(event: dict[str, object]) -> bool:
            nonlocal blocked_once
            client = event.get("client") or {}
            if (
                event.get("event") == "session_acquire"
                and client.get("session") == IDENTITY_B.session_id[:12]
                and not blocked_once
            ):
                blocked_once = True
                first_audit_entered.set()
                resume_first_audit.wait(1.0)
            if event.get("event") == "session_queued":
                queued_events.append(str(event.get("ticket")))
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        coordinator.acquire(IDENTITY, "owner")
        results: list[tuple[int, dict]] = []
        threads = (
            threading.Thread(
                name="same-a",
                target=lambda: results.append(
                    coordinator.acquire(IDENTITY_B, "same")
                ),
            ),
            threading.Thread(
                name="same-b",
                target=lambda: results.append(
                    coordinator.acquire(IDENTITY_B, "same")
                ),
            ),
        )
        threads[0].start()
        self.assertTrue(first_audit_entered.wait(1.0))
        threads[1].start()
        threads[1].join(0.02)
        self.assertTrue(threads[1].is_alive())
        resume_first_audit.set()
        for thread in threads:
            thread.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([status for status, _payload in results], [202, 202])
        self.assertEqual(len({payload["ticket"] for _status, payload in results}), 1)
        self.assertEqual(len(queued_events), 1)
        self.assertEqual(len(coordinator.snapshot_payload()["queue"]), 1)

    def test_same_client_retry_never_gets_ticket_when_first_audit_fails(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            client = event.get("client") or {}
            if (
                event.get("event") == "session_acquire"
                and client.get("session") == IDENTITY_B.session_id[:12]
            ):
                entered.set()
                resume.wait(1.0)
                return False
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        coordinator.acquire(IDENTITY, "owner")
        results: list[tuple[int, dict]] = []
        first = threading.Thread(
            target=lambda: results.append(coordinator.acquire(IDENTITY_B, "same"))
        )
        second = threading.Thread(
            target=lambda: results.append(coordinator.acquire(IDENTITY_B, "same"))
        )
        first.start()
        self.assertTrue(entered.wait(1.0))
        second.start()
        resume.set()
        first.join(2.0)
        second.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in (first, second)))
        self.assertEqual([status for status, _payload in results], [503, 503])
        self.assertEqual(coordinator.snapshot_payload()["queue"], [])

    def test_same_client_retry_is_bounded_while_first_audit_is_stuck(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            client = event.get("client") or {}
            if (
                event.get("event") == "session_acquire"
                and client.get("session") == IDENTITY_B.session_id[:12]
            ):
                entered.set()
                resume.wait(1.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        coordinator.acquire(IDENTITY, "owner")
        first_result: list[tuple[int, dict]] = []
        first = threading.Thread(
            target=lambda: first_result.append(
                coordinator.acquire(IDENTITY_B, "same")
            )
        )
        first.start()
        self.assertTrue(entered.wait(1.0))
        started_at = time.monotonic()
        try:
            retry_status, retry_payload = coordinator.acquire(IDENTITY_B, "same")
            elapsed = time.monotonic() - started_at
        finally:
            resume.set()
            first.join(2.0)

        self.assertLess(elapsed, 0.20)
        self.assertEqual(retry_status, 503)
        self.assertEqual(retry_payload["error"], "audit_failed")
        self.assertEqual(first_result[0][0], 202)

    def test_queue_fifo_follows_ticket_creation_not_audit_completion(self) -> None:
        c_second_acquired = threading.Event()

        class CreationOrderGate:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.counts: dict[str, int] = {}

            def acquire(self, blocking: bool = True) -> bool:
                name = threading.current_thread().name
                self.counts[name] = self.counts.get(name, 0) + 1
                acquired = self.lock.acquire(blocking=blocking)
                if acquired and name == "fifo-c" and self.counts[name] == 2:
                    c_second_acquired.set()
                return acquired

            def release(self) -> None:
                name = threading.current_thread().name
                first_b = name == "fifo-b" and self.counts.get(name) == 1
                self.lock.release()
                if first_b:
                    c_second_acquired.wait(1.0)

        gate = CreationOrderGate()
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda _event: True,
        )
        coordinator.acquire(IDENTITY, "owner")
        coordinator._audit_gate = gate
        client_b = ClientIdentity(
            "codex", 811, 1, "2026-07-15T00:00:05Z", "fifo-b", "fifo-b"
        )
        client_c = ClientIdentity(
            "claude", 812, 1, "2026-07-15T00:00:06Z", "fifo-c", "fifo-c"
        )
        results: list[tuple[int, dict]] = []
        b_thread = threading.Thread(
            name="fifo-b",
            target=lambda: results.append(coordinator.acquire(client_b, "fifo-b")),
        )
        c_thread = threading.Thread(
            name="fifo-c",
            target=lambda: results.append(coordinator.acquire(client_c, "fifo-c")),
        )
        b_thread.start()
        deadline = time.monotonic() + 1.0
        while gate.counts.get("fifo-b", 0) < 1 and time.monotonic() < deadline:
            time.sleep(0.001)
        c_thread.start()
        b_thread.join(2.0)
        c_thread.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in (b_thread, c_thread)))
        self.assertEqual(sorted(status for status, _payload in results), [202, 202])
        queue = coordinator.snapshot_payload()["queue"]
        self.assertEqual(
            [item["session"] for item in queue],
            [client_b.session_id[:12], client_c.session_id[:12]],
        )

    def test_queued_ticket_ttl_starts_when_audit_commits(self) -> None:
        clock = Clock()
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_queued":
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
        )
        coordinator.acquire(IDENTITY, "owner")
        result: list[tuple[int, dict]] = []
        thread = threading.Thread(
            target=lambda: result.append(coordinator.acquire(IDENTITY_B, "queued"))
        )
        thread.start()
        self.assertTrue(entered.wait(1.0))
        clock.advance(121.0)
        resume.set()
        thread.join(2.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0][0], 202)
        self.assertEqual(result[0][1]["expires_in_s"], 120.0)

    @staticmethod
    def _fill_queue_to_one_free_slot(coordinator: SessionCoordinator) -> None:
        with coordinator._condition:
            now = coordinator._time_fn()
            for index in range(coordination_module.MAX_SESSION_QUEUE - 1):
                coordinator._queue.append(
                    coordination_module._Ticket(
                        f"prefill-{index}",
                        ClientIdentity(
                            "prefill",
                            1000 + index,
                            1,
                            "2026-07-15T00:00:00Z",
                            f"prefill-session-{index}",
                            "prefill",
                        ),
                        "prefill",
                        now,
                        now,
                    )
                )

    def test_last_session_queue_slot_has_one_durable_queued_event(self) -> None:
        candidate_a = ClientIdentity(
            "codex", 801, 1, "2026-07-15T00:00:00Z", "candidate-a", "queue-a"
        )
        candidate_b = ClientIdentity(
            "claude", 802, 1, "2026-07-15T00:00:01Z", "candidate-b", "queue-b"
        )
        a_queued_entered = threading.Event()
        queued_events: list[str] = []

        class HandoffGate:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.a_release_thread: int | None = None
                self.b_acquires = 0
                self.b_second_acquired = threading.Event()

            def acquire(self, blocking: bool = True) -> bool:
                acquired = self.lock.acquire(blocking=blocking)
                if acquired and threading.current_thread().name == "queue-b":
                    self.b_acquires += 1
                    if self.b_acquires == 2:
                        self.b_second_acquired.set()
                return acquired

            def release(self) -> None:
                current = threading.get_ident()
                handoff = current == self.a_release_thread
                if handoff:
                    self.a_release_thread = None
                self.lock.release()
                if handoff:
                    self.b_second_acquired.wait(1.0)

        gate = HandoffGate()

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_queued":
                session = str((event.get("client") or {}).get("session"))
                queued_events.append(session)
                if session == candidate_a.session_id[:12]:
                    gate.a_release_thread = threading.get_ident()
                    a_queued_entered.set()
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        coordinator.acquire(IDENTITY, "owner")
        self._fill_queue_to_one_free_slot(coordinator)
        coordinator._audit_gate = gate
        results: dict[str, tuple[int, dict]] = {}
        a_thread = threading.Thread(
            name="queue-a",
            target=lambda: results.setdefault(
                "a", coordinator.acquire(candidate_a, "candidate-a")
            ),
        )
        b_thread = threading.Thread(
            name="queue-b",
            target=lambda: results.setdefault(
                "b", coordinator.acquire(candidate_b, "candidate-b")
            ),
        )
        a_thread.start()
        self.assertTrue(a_queued_entered.wait(1.0))
        b_thread.start()
        a_thread.join(2.0)
        b_thread.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in (a_thread, b_thread)))
        self.assertEqual(sorted(result[0] for result in results.values()), [202, 429])
        self.assertEqual(len(queued_events), 1)

    def test_queue_capacity_reservation_is_released_on_audit_failure(self) -> None:
        fail_queued = True

        def audit(event: dict[str, object]) -> bool:
            return not (fail_queued and event.get("event") == "session_queued")

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        coordinator.acquire(IDENTITY, "owner")
        self._fill_queue_to_one_free_slot(coordinator)
        rejected = coordinator.acquire(
            ClientIdentity(
                "codex", 803, 1, "2026-07-15T00:00:02Z", "candidate-c", "queue-c"
            ),
            "candidate-c",
        )
        self.assertEqual((rejected[0], rejected[1]["error"]), (503, "audit_failed"))
        fail_queued = False
        accepted = coordinator.acquire(
            ClientIdentity(
                "claude", 804, 1, "2026-07-15T00:00:03Z", "candidate-d", "queue-d"
            ),
            "candidate-d",
        )
        self.assertEqual(accepted[0], 202)

    def test_concurrent_ticket_expiry_writes_one_terminal_event(self) -> None:
        clock = Clock()
        entered = threading.Event()
        resume = threading.Event()
        cancelled: list[str] = []

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "ticket_cancelled":
                cancelled.append(str(event.get("ticket")))
                if len(cancelled) == 1:
                    entered.set()
                    resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
        )
        coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        with coordinator._condition:
            coordinator._active.expires_at = 999.0
        clock.advance(121.0)
        threads = [
            threading.Thread(target=lambda: coordinator.status(IDENTITY)),
            threading.Thread(target=lambda: coordinator.status(IDENTITY_B)),
        ]
        threads[0].start()
        self.assertTrue(entered.wait(1.0))
        threads[1].start()
        try:
            threads[1].join(0.05)
        finally:
            resume.set()
            for thread in threads:
                thread.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(cancelled, [ticket["ticket"]])
        self.assertEqual(coordinator.snapshot_payload()["queue"], [])

    def test_initial_grant_rebases_full_ttl_at_publication(self) -> None:
        clock = Clock()
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "request"
            ):
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
        )
        result: list[tuple[int, dict]] = []
        thread = threading.Thread(
            target=lambda: result.append(coordinator.acquire(IDENTITY, "ttl"))
        )
        thread.start()
        self.assertTrue(entered.wait(1.0))
        clock.advance(121.0)
        resume.set()
        thread.join(2.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0][0], 200)
        self.assertEqual(result[0][1]["expires_in_s"], 120.0)
        heartbeat_status, heartbeat = coordinator.heartbeat(
            IDENTITY, result[0][1]["lease_token"]
        )
        self.assertEqual(heartbeat_status, 200)
        self.assertEqual(heartbeat["lease_id"], result[0][1]["lease_id"])
        self.assertEqual(heartbeat["expires_in_s"], 120.0)

    def test_initial_grant_marker_advances_revision_on_publish_and_failure(self) -> None:
        for audit_allowed in (True, False):
            with self.subTest(audit_allowed=audit_allowed):
                entered = threading.Event()
                resume = threading.Event()

                def audit(event: dict[str, object]) -> bool:
                    if (
                        event.get("event") == "session_granted"
                        and event.get("reason") == "request"
                    ):
                        entered.set()
                        resume.wait(2.0)
                        return audit_allowed
                    return True

                coordinator = SessionCoordinator(
                    token_fn=Sequence("token"),
                    id_fn=Sequence("lease"),
                    audit=audit,
                )
                initial_revision = coordinator.snapshot_payload()["revision"]
                result: list[tuple[int, dict]] = []
                thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.acquire(IDENTITY, "revision")
                    )
                )
                thread.start()
                self.assertTrue(entered.wait(1.0))
                granting = coordinator.snapshot_payload()
                self.assertEqual(granting["revision"], initial_revision + 1)
                self.assertIsNotNone(granting["granting"])
                resume.set()
                thread.join(2.0)

                self.assertFalse(thread.is_alive())
                settled = coordinator.snapshot_payload()
                expected_revision = initial_revision + (3 if audit_allowed else 2)
                self.assertEqual(settled["revision"], expected_revision)
                self.assertIsNone(settled["granting"])
                self.assertEqual(result[0][0], 200 if audit_allowed else 503)
                self.assertEqual(settled["active"] is not None, audit_allowed)

    def test_initial_grant_inflight_rejects_same_session_identity_collision(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "request"
            ):
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        first: list[tuple[int, dict]] = []
        thread = threading.Thread(
            target=lambda: first.append(coordinator.acquire(IDENTITY, "first"))
        )
        thread.start()
        self.assertTrue(entered.wait(1.0))
        collision = type(IDENTITY)(
            IDENTITY.platform,
            IDENTITY.pid + 1,
            IDENTITY.ppid,
            IDENTITY.started_at_utc,
            IDENTITY.session_id,
            "different metadata",
        )
        status, payload = coordinator.acquire(collision, "collision")
        resume.set()
        thread.join(2.0)

        self.assertEqual((status, payload.get("error")), (403, "identity_mismatch"))
        self.assertEqual(coordinator.snapshot_payload()["queue"], [])

    def test_fifo_ticket_is_reserved_against_ttl_during_grant_audit(self) -> None:
        clock = Clock()
        entered = threading.Event()
        resume = threading.Event()
        fifo_grants: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                fifo_grants.append(dict(event))
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        coordinator.release(IDENTITY, l1["lease_token"])
        result: list[tuple[int, dict]] = []
        wait_thread = threading.Thread(
            target=lambda: result.append(
                coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
            )
        )
        wait_thread.start()
        self.assertTrue(entered.wait(1.0))
        clock.advance(121.0)
        during = coordinator.status(IDENTITY_B)
        self.assertEqual(during["self"]["ticket"], ticket["ticket"])
        self.assertEqual(during["self"]["state"], "queued")
        resume.set()
        wait_thread.join(2.0)

        self.assertFalse(wait_thread.is_alive())
        snapshot = coordinator.snapshot_payload()
        self.assertEqual(len(fifo_grants), 1)
        self.assertEqual(fifo_grants[0].get("lease_id"), snapshot["active"]["lease_id"])
        self.assertEqual(snapshot["active"]["session"], IDENTITY_B.session_id[:12])
        self.assertEqual(result[0][0], 200)

    def test_fifo_inflight_ticket_waits_without_false_terminal_status(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
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
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        coordinator.release(IDENTITY, l1["lease_token"])
        first_result: list[tuple[int, dict]] = []
        first_wait = threading.Thread(
            target=lambda: first_result.append(
                coordinator.wait(IDENTITY_B, ticket["ticket"], 0.50)
            )
        )
        first_wait.start()
        self.assertTrue(entered.wait(1.0))
        wait_result: list[tuple[int, dict]] = []
        wait_thread = threading.Thread(
            target=lambda: wait_result.append(
                coordinator.wait(IDENTITY_B, ticket["ticket"], 0.50)
            )
        )
        wait_thread.start()
        wait_thread.join(0.05)
        self.assertTrue(wait_thread.is_alive(), "wait_returned_false_terminal")
        acquire_status, acquire_payload = coordinator.acquire(IDENTITY_B, "l2")
        self.assertEqual((acquire_status, acquire_payload["status"]), (202, "queued"))
        self.assertEqual(coordinator.status(IDENTITY_B)["self"]["position"], 1)

        resume.set()
        first_wait.join(2.0)
        wait_thread.join(2.0)
        self.assertFalse(first_wait.is_alive())
        self.assertFalse(wait_thread.is_alive())
        self.assertEqual(first_result[0][0], 200)
        self.assertEqual(wait_result[0][0], 200)
        self.assertEqual(
            wait_result[0][1]["lease_id"],
            coordinator.snapshot_payload()["active"]["lease_id"],
        )

    def test_fifo_inflight_zero_timeout_fails_audit_fast_without_terminal(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
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
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        coordinator.release(IDENTITY, l1["lease_token"])
        claim_result: list[tuple[int, dict]] = []
        claim_thread = threading.Thread(
            target=lambda: claim_result.append(
                coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
            )
        )
        claim_thread.start()
        self.assertTrue(entered.wait(1.0))

        progress = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        during = coordinator.snapshot_payload()
        self.assertEqual((progress[0], progress[1]["status"]), (202, "queued"))
        self.assertNotIn("cleanup_degraded", progress[1])
        self.assertNotIn("lease_token", progress[1])
        self.assertIsNone(during["active"])
        self.assertEqual(during["granting"]["ticket"], ticket["ticket"])

        resume.set()
        claim_thread.join(2.0)
        self.assertFalse(claim_thread.is_alive())
        self.assertEqual(claim_result[0][0], 200)

    def test_concurrent_initial_grant_has_one_allowed_event_matching_active(self) -> None:
        entered = threading.Event()
        resume = threading.Event()
        granted: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "request"
            ):
                granted.append(dict(event))
                client = event.get("client")
                if (
                    isinstance(client, dict)
                    and client.get("session") == IDENTITY.session_id[:12]
                ):
                    entered.set()
                    resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        first: list[tuple[int, dict]] = []
        thread = threading.Thread(
            target=lambda: first.append(coordinator.acquire(IDENTITY, "first"))
        )
        thread.start()
        self.assertTrue(entered.wait(1.0))
        second = coordinator.acquire(IDENTITY_B, "second")
        resume.set()
        thread.join(2.0)

        self.assertFalse(thread.is_alive())
        snapshot = coordinator.snapshot_payload()
        matching = [
            event
            for event in granted
            if event.get("lease_id") == snapshot["active"]["lease_id"]
        ]
        self.assertEqual(len(granted), 1, granted)
        self.assertEqual(len(matching), 1, (granted, snapshot, first, second))

    def test_concurrent_fifo_grant_has_one_allowed_event_matching_active(self) -> None:
        entered = threading.Event()
        resume = threading.Event()
        fifo_grants: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                fifo_grants.append(dict(event))
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
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        coordinator.release(IDENTITY, l1["lease_token"])
        first: list[tuple[int, dict]] = []
        first_wait = threading.Thread(
            target=lambda: first.append(
                coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
            )
        )
        first_wait.start()
        self.assertTrue(entered.wait(1.0))
        second = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        self.assertEqual((second[0], second[1]["status"]), (202, "queued"))
        resume.set()
        first_wait.join(2.0)

        self.assertFalse(first_wait.is_alive())
        snapshot = coordinator.snapshot_payload()
        self.assertEqual(len(fifo_grants), 1, fifo_grants)
        self.assertEqual(fifo_grants[0].get("lease_id"), snapshot["active"]["lease_id"])
        self.assertEqual(first[0][0], 200)

    def test_release_terminal_events_precede_fifo_grant_in_durable_ledger(self) -> None:
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")
        coordinator.release(IDENTITY, l1["lease_token"])
        self.assertEqual(
            coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)[0],
            200,
        )

        names = [str(event.get("event")) for event in events]
        started = names.index("session_release_started")
        finished = names.index("session_release_finished")
        prepared = names.index("session_grant_prepared")
        fifo = next(
            index
            for index, event in enumerate(events)
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        )
        self.assertLess(started, finished)
        self.assertLess(finished, prepared)
        self.assertLess(prepared, fifo)

    def test_late_release_audit_cannot_physically_follow_next_grant(self) -> None:
        entered = threading.Event()
        resume = threading.Event()
        release_done = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_release_started":
                entered.set()
                resume.wait(2.0)
            events.append(dict(event))
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {},
            cleanup_timeout_s=0.02,
        )
        _, l1 = coordinator.acquire(IDENTITY, "l1")
        _, ticket = coordinator.acquire(IDENTITY_B, "l2")

        def release() -> None:
            coordinator.release(IDENTITY, l1["lease_token"])
            release_done.set()

        thread = threading.Thread(target=release)
        thread.start()
        self.assertTrue(entered.wait(1.0))
        self.assertTrue(release_done.wait(0.20), "release_held_by_late_audit")
        during = coordinator.snapshot_payload()
        self.assertIsNone(during["releasing"])
        self.assertIsNone(during["active"])
        self.assertTrue(during["handoff_pending"])
        self.assertEqual(during["queue"][0]["session"], IDENTITY_B.session_id[:12])
        blocked = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        self.assertEqual((blocked[0], blocked[1]["status"]), (202, "queued"))

        resume.set()
        with coordinator._condition:
            terminalized = coordinator._condition.wait_for(
                lambda: not coordinator._handoff_pending, timeout=1.0
            )
        self.assertTrue(terminalized)
        granted = coordinator.wait(IDENTITY_B, ticket["ticket"], 0.0)
        thread.join(2.0)
        self.assertEqual((granted[0], granted[1]["status"]), (200, "active"))

        names = [str(event.get("event")) for event in events]
        finished = names.index("session_release_finished")
        prepared = names.index("session_grant_prepared")
        fifo = next(
            index
            for index, event in enumerate(events)
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        )
        self.assertLess(finished, prepared)
        self.assertLess(prepared, fifo)

    def test_snapshot_exposes_public_grant_marker_without_token(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "request"
            ):
                entered.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=lambda: "secret-token-shaped-value",
            id_fn=Sequence("lease"),
            audit=audit,
        )
        thread = threading.Thread(
            target=lambda: coordinator.acquire(IDENTITY, "marker")
        )
        thread.start()
        self.assertTrue(entered.wait(1.0))
        snapshot = coordinator.snapshot_payload()
        resume.set()
        thread.join(2.0)

        self.assertEqual(snapshot["granting"]["session"], IDENTITY.session_id[:12])
        self.assertFalse(snapshot["handoff_pending"])
        self.assertNotIn("secret-token-shaped-value", str(snapshot))


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
            cleanup_timeout_s=0.05,
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
