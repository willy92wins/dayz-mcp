"""Authority fences: who may act under a lease, identity checks and the grant ledger.

Moved verbatim from test_bug046_lease_queue_liveness.py, test_bug046_authority_fence.py, test_authority_invariants_are_gated.py, test_task7_review_regressions.py, test_task7_rereview_regressions.py, test_task7_final_authority_regressions.py, test_0ab2_r9.py
(review 2026-09-25, W4d step 3).
"""

from __future__ import annotations

import copy
import inspect
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server, session_coordination as coordination_module
from dayz_mcp.process_lifecycle import (
    occupancy_error_fields,
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
    takeover_target_run_id,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.fence_helpers import accredited_poll, bind_both_peers
from tests.lease_helpers import (
    _coord,
    _identity,
    AuditSink,
    CleanupSink,
    FakeClock,
    MID,
    SequentialIds,
)
from tests.lifecycle_helpers import (
    Clock,
    Guard,
    IDENTITY,
    identity,
    IDENTITY_B,
    IDENTITY_PAYLOAD,
    LifecycleFixture,
    record,
    Sequence,
)
from tests.steam_helpers import FakeSteamGate
from tests._tiers import slow_test


# --- helpers from test_bug046_lease_queue_liveness.py ---
class _BlockingWalBoundary:
    """Deterministic WAL callbacks for grant-fence concurrency tests."""

    def __init__(self, boundary: str | None = None) -> None:
        self.boundary = boundary
        self.entered = threading.Event()
        self.resume = threading.Event()
        self.events: list[dict[str, object]] = []
        self.snapshots: list[dict[str, object]] = []
        self._blocked = False
        self._sha_index = 0

    def _next_sha(self) -> str:
        self._sha_index += 1
        return f"sha-{self._sha_index}"

    def _block_once(self, boundary: str) -> None:
        if self.boundary != boundary or self._blocked:
            return
        self._blocked = True
        self.entered.set()
        if not self.resume.wait(2.0):
            raise TimeoutError(f"boundary_not_released:{boundary}")

    def arm(self, marker: dict[str, object]) -> str:
        self.events.append({"callback": "arm", **dict(marker)})
        return self._next_sha()

    def transition(
        self, _fault_id: str, _expected_sha256: str, **kwargs: object
    ) -> str:
        if kwargs.get("state") == "completed":
            boundary = "completed_transition"
        else:
            boundary = "revision_transition"
        self.events.append({"callback": boundary, **kwargs})
        self._block_once(boundary)
        return self._next_sha()

    def persist(self, snapshot: dict[str, object]) -> bool:
        self.snapshots.append(snapshot)
        self._block_once("snapshot")
        return True

    def clear(self, _fault_id: str, _expected_sha256: str) -> bool:
        self.events.append({"callback": "clear"})
        self._block_once("clear")
        return True


# --- helpers from test_bug046_authority_fence.py ---
class _BlockingWal:
    def __init__(self, boundary: str, *, clear_succeeds: bool = True) -> None:
        self.boundary = boundary
        self.clear_succeeds = clear_succeeds
        self.enabled = True
        self.entered = threading.Event()
        self.resume = threading.Event()
        self.marker: dict[str, object] | None = None
        self.sha = "sha-0"
        self._serial = 0
        self._blocked = False

    def _next_sha(self) -> str:
        self._serial += 1
        self.sha = f"sha-{self._serial}"
        return self.sha

    def _block_once(self, boundary: str) -> None:
        if self.enabled and self.boundary == boundary and not self._blocked:
            self._blocked = True
            self.entered.set()
            if not self.resume.wait(5.0):
                raise TimeoutError(f"test barrier timed out at {boundary}")

    def arm(self, marker: dict[str, object]) -> str:
        self.marker = copy.deepcopy(marker)
        return self._next_sha()

    def transition(
        self, fault_id: str, expected_sha: str, **changes: object
    ) -> str:
        self._block_once("transition")
        if (
            self.marker is None
            or self.marker.get("fault_id") != fault_id
            or self.sha != expected_sha
        ):
            raise ValueError("test_wal_cas_mismatch")
        self.marker.update(changes)
        return self._next_sha()

    def persist(self, _snapshot: dict[str, object]) -> bool:
        self._block_once("snapshot")
        return True

    def clear(self, fault_id: str, expected_sha: str) -> bool:
        self._block_once("clear")
        if (
            self.marker is None
            or self.marker.get("fault_id") != fault_id
            or self.sha != expected_sha
        ):
            return False
        if not self.clear_succeeds:
            return False
        self.marker = None
        return True


# --- from test_bug046_lease_queue_liveness.py ---


class Bug046GrantAuthorityFenceTests(unittest.TestCase):
    _BOUNDARIES = (
        "revision_transition",
        "snapshot",
        "completed_transition",
        "clear",
    )

    @staticmethod
    def _coordinator(
        wal: _BlockingWalBoundary,
        events: list[dict[str, object]],
    ) -> SessionCoordinator:
        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return True

        return SessionCoordinator(
            token_fn=SequentialIds("secret-token"),
            id_fn=SequentialIds("lease"),
            audit=audit,
            fault_id_fn=SequentialIds("fault"),
            fault_arm=wal.arm,
            fault_transition=wal.transition,
            fault_clear=wal.clear,
            persist_snapshot=wal.persist,
        )

    def test_admin_release_never_confirms_a_provisional_grant_at_any_wal_boundary(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal = _BlockingWalBoundary(boundary)
                events: list[dict[str, object]] = []
                coordinator = self._coordinator(wal, events)
                client = _identity("owner")
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.acquire(client, "authority-fence", "operation-owner")
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                during = coordinator.snapshot_payload()
                self.assertIsNone(during["active"])
                self.assertIsNotNone(during["granting"])
                self.assertNotIn("secret-token", repr(during))
                status = coordinator.status(client)
                read = coordinator.authorize(client, None, "telemetry_read")
                self.assertIsNone(status["owner"])
                self.assertIsNone(read.owner_session_id)
                try:
                    released = coordinator.admin_release(
                        str(during["granting"]["lease_id"]), "operator"
                    )
                finally:
                    wal.resume.set()
                    grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertEqual(
                    (released[0], released[1].get("error")),
                    (409, "session_granting"),
                )
                self.assertEqual((result[0][0], result[0][1]["status"]), (200, "active"))
                self.assertFalse(
                    any(event.get("event") == "admin_release" for event in events)
                )

    def test_revision_or_queue_drift_at_any_wal_boundary_revokes_without_token(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal = _BlockingWalBoundary(boundary)
                events: list[dict[str, object]] = []
                coordinator = self._coordinator(wal, events)
                owner, waiter = _identity("owner"), _identity("waiter")
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.acquire(owner, "authority-fence", "operation-owner")
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                queued = coordinator.acquire(waiter, "queued-drift", "operation-waiter")
                self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
                wal.resume.set()
                grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertEqual(
                    (result[0][0], result[0][1].get("error")),
                    (409, "coordination_changed"),
                )
                settled = coordinator.snapshot_payload()
                self.assertIsNone(settled["active"])
                self.assertIsNone(settled["granting"])
                self.assertEqual(settled["queue"][0]["session"], waiter.session_id[:12])
                self.assertNotIn("secret-token", repr((result, settled)))
                revoked = [
                    event
                    for event in events
                    if event.get("event") == "session_grant_revoked"
                ]
                self.assertEqual(len(revoked), 1, events)
                self.assertTrue(
                    any(
                        event.get("failure") == "coordination_changed"
                        for event in wal.events
                        if event.get("callback")
                        in {"revision_transition", "completed_transition"}
                    ),
                    wal.events,
                )

    def test_cancel_at_any_wal_boundary_is_revisioned_and_durably_revoked(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal = _BlockingWalBoundary(boundary)
                events: list[dict[str, object]] = []
                coordinator = self._coordinator(wal, events)
                owner = _identity("owner")
                operation_id = "operation-owner"
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.acquire(
                            owner, "authority-fence", operation_id
                        )
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                revision_before_cancel = coordinator.snapshot_payload()["revision"]
                cancelled = coordinator.cancel_operation(owner, operation_id)
                revision_after_cancel = coordinator.snapshot_payload()["revision"]
                wal.resume.set()
                grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertGreater(revision_after_cancel, revision_before_cancel)
                self.assertIn(cancelled[0], {200, 202})
                self.assertEqual(
                    (result[0][0], result[0][1].get("error")),
                    (409, "operation_cancelled"),
                )
                settled = coordinator.snapshot_payload()
                self.assertIsNone(settled["active"])
                self.assertIsNone(settled["granting"])
                self.assertNotIn("secret-token", repr((result, settled)))
                revoked = [
                    event
                    for event in events
                    if event.get("event") == "session_grant_revoked"
                ]
                self.assertEqual(len(revoked), 1, events)
                self.assertTrue(
                    any(
                        event.get("failure") == "coordination_changed"
                        for event in wal.events
                        if event.get("callback")
                        in {"revision_transition", "completed_transition"}
                    ),
                    wal.events,
                )

    def test_pending_wal_is_not_claimable_while_next_acquire_queues(self) -> None:
        wal = _BlockingWalBoundary()
        events: list[dict[str, object]] = []
        coordinator = self._coordinator(wal, events)
        with coordinator._condition:
            coordinator._wal_marker = {
                "fault_id": "fault-open",
                "state": "armed",
                "phase": "coordination_changed",
            }
            coordinator._wal_sha256 = "sha-open"

        self.assertFalse(coordinator.status(_identity("observer"))["claimable"])
        queued = coordinator.acquire(
            _identity("next"), "wait-for-compensation", "operation-next"
        )
        self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
        self.assertFalse(
            any(
                event.get("event") in {"session_grant_prepared", "session_granted"}
                for event in events
            )
        )

    def _fifo_fixture(
        self, boundary: str
    ) -> tuple[
        _BlockingWalBoundary,
        list[dict[str, object]],
        SessionCoordinator,
        ClientIdentity,
        dict,
    ]:
        wal = _BlockingWalBoundary()
        events: list[dict[str, object]] = []
        coordinator = self._coordinator(wal, events)
        owner, waiter = _identity("owner"), _identity("waiter")
        active = coordinator.acquire(owner, "owner", "operation-owner")[1]
        ticket = coordinator.acquire(waiter, "waiter", "operation-waiter")[1]
        self.assertEqual(coordinator.release(owner, active["lease_token"])[0], 200)
        with coordinator._condition:
            self.assertTrue(
                coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=1.0
                )
            )
        wal.boundary = boundary
        wal.entered.clear()
        wal.resume.clear()
        wal._blocked = False
        return wal, events, coordinator, waiter, ticket

    def test_fifo_admin_release_rejects_each_provisional_wal_boundary(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal, events, coordinator, waiter, ticket = self._fifo_fixture(boundary)
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.wait(waiter, ticket["ticket"], 0.0)
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                granting = coordinator.snapshot_payload()["granting"]
                try:
                    released = coordinator.admin_release(
                        str(granting["lease_id"]), "operator"
                    )
                finally:
                    wal.resume.set()
                    grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertEqual(
                    (released[0], released[1].get("error")),
                    (409, "session_granting"),
                )
                self.assertEqual((result[0][0], result[0][1]["status"]), (200, "active"))
                self.assertFalse(
                    any(event.get("event") == "admin_release" for event in events)
                )

    def test_duplicate_wait_during_each_wal_boundary_does_not_revoke_grant(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal, _events, coordinator, waiter, ticket = self._fifo_fixture(boundary)
                first_result: list[tuple[int, dict]] = []
                first_wait = threading.Thread(
                    target=lambda: first_result.append(
                        coordinator.wait(waiter, ticket["ticket"], 0.0)
                    )
                )
                first_wait.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)

                revision_before_duplicate = coordinator.snapshot_payload()["revision"]
                duplicate = coordinator.wait(waiter, ticket["ticket"], 0.0)
                revision_after_duplicate = coordinator.snapshot_payload()["revision"]
                wal.resume.set()
                first_wait.join(2.0)

                self.assertFalse(first_wait.is_alive(), boundary)
                self.assertEqual(
                    (duplicate[0], duplicate[1]["status"]),
                    (202, "queued"),
                )
                self.assertEqual(revision_after_duplicate, revision_before_duplicate)
                self.assertEqual(
                    (first_result[0][0], first_result[0][1]["status"]),
                    (200, "active"),
                )

    def test_fifo_queue_drift_at_each_wal_boundary_requeues_exact_head(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal, events, coordinator, waiter, ticket = self._fifo_fixture(boundary)
                next_waiter = _identity("next")
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.wait(waiter, ticket["ticket"], 0.0)
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                queued_results: list[tuple[int, dict]] = []
                queue_thread = threading.Thread(
                    target=lambda: queued_results.append(
                        coordinator.acquire(
                            next_waiter, "next", "operation-next"
                        )
                    )
                )
                queue_thread.start()
                with coordinator._condition:
                    self.assertTrue(
                        coordinator._condition.wait_for(
                            lambda: bool(coordinator._queue_reservations),
                            timeout=1.0,
                        )
                    )
                wal.resume.set()
                grant_thread.join(2.0)
                queue_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertFalse(queue_thread.is_alive(), boundary)
                queued = queued_results[0]
                self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
                self.assertEqual(
                    (result[0][0], result[0][1].get("error")),
                    (409, "coordination_changed"),
                )
                settled = coordinator.snapshot_payload()
                self.assertIsNone(settled["active"])
                self.assertIsNone(settled["granting"])
                self.assertEqual(
                    [item["ticket"] for item in settled["queue"]],
                    [ticket["ticket"], queued[1]["ticket"]],
                )
                self.assertEqual(
                    len(
                        [
                            event
                            for event in events
                            if event.get("event") == "session_grant_revoked"
                            and event.get("reason") == "coordination_changed"
                        ]
                    ),
                    1,
                )

    def test_fifo_cancel_at_each_wal_boundary_never_requeues_or_returns_token(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal, events, coordinator, waiter, ticket = self._fifo_fixture(boundary)
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.wait(waiter, ticket["ticket"], 0.0)
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                cancelled = coordinator.cancel_operation(
                    waiter, "operation-waiter"
                )
                wal.resume.set()
                grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertIn(cancelled[0], {200, 202})
                self.assertEqual(
                    (result[0][0], result[0][1].get("error")),
                    (409, "operation_cancelled"),
                )
                settled = coordinator.snapshot_payload()
                self.assertIsNone(settled["active"])
                self.assertIsNone(settled["granting"])
                self.assertEqual(settled["queue"], [])
                self.assertNotIn("secret-token", repr((result, settled)))
                self.assertEqual(
                    len(
                        [
                            event
                            for event in events
                            if event.get("event") == "session_grant_revoked"
                            and event.get("reason") == "operation_cancelled"
                        ]
                    ),
                    1,
                )

    def test_tombstone_create_and_refresh_each_advance_revision(self) -> None:
        coordinator = SessionCoordinator()
        client = _identity("owner")
        revision_0 = coordinator.snapshot_payload()["revision"]
        first = coordinator.cancel_operation(client, "operation-owner")
        revision_1 = coordinator.snapshot_payload()["revision"]
        second = coordinator.cancel_operation(client, "operation-owner")
        revision_2 = coordinator.snapshot_payload()["revision"]

        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 200)
        self.assertGreater(revision_1, revision_0)
        self.assertGreater(revision_2, revision_1)

    def test_snapshots_never_mix_active_and_granting_and_fifo_clear_pending_has_no_token(self) -> None:
        class FailThirdClear(_BlockingWalBoundary):
            def __init__(self) -> None:
                super().__init__()
                self.clear_count = 0

            def clear(self, _fault_id: str, _expected_sha256: str) -> bool:
                self.clear_count += 1
                return self.clear_count < 3

        wal = FailThirdClear()
        events: list[dict[str, object]] = []
        coordinator = self._coordinator(wal, events)
        owner, waiter = _identity("owner"), _identity("waiter")
        active = coordinator.acquire(owner, "owner", "operation-owner")[1]
        ticket = coordinator.acquire(waiter, "waiter", "operation-waiter")[1]
        released = coordinator.release(owner, active["lease_token"])
        self.assertEqual(released[0], 200)
        with coordinator._condition:
            self.assertTrue(
                coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=1.0
                )
            )

        claimed = coordinator.wait(waiter, ticket["ticket"], 0.0)
        public_snapshot = coordinator.snapshot_payload()
        all_snapshots = [*wal.snapshots, public_snapshot]

        self.assertEqual((claimed[0], claimed[1].get("error")), (503, "audit_failed"))
        self.assertTrue(
            all(not (snapshot["active"] and snapshot["granting"]) for snapshot in all_snapshots)
        )
        self.assertIsNone(public_snapshot["active"])
        self.assertNotIn("secret-token", repr((claimed, all_snapshots)))


# --- from test_bug046_authority_fence.py ---


class GrantAuthorityFenceTests(unittest.TestCase):
    def _coordinator(
        self,
        wal: _BlockingWal,
        *,
        tokens: object | None = None,
        ids: object | None = None,
    ) -> SessionCoordinator:
        token_values = tokens or iter(("token-a",))
        id_values = ids or iter(("lease-a",))
        fault_values = iter(
            ("fault-1", "fault-2", "fault-3", "fault-4", "fault-5")
        )
        return SessionCoordinator(
            token_fn=token_values.__next__,
            id_fn=id_values.__next__,
            fault_id_fn=fault_values.__next__,
            daemon_generation="generation-a",
            utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            audit=lambda _event: True,
            fault_arm=wal.arm,
            fault_transition=wal.transition,
            fault_clear=wal.clear,
            persist_snapshot=wal.persist,
        )

    def _start_call(self, call):
        result: dict[str, object] = {}

        def invoke() -> None:
            result["value"] = call()

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        return worker, result

    def _assert_immediate_is_fenced(self, boundary: str) -> None:
        wal = _BlockingWal(boundary)
        coordinator = self._coordinator(wal)
        client = _identity("a")
        worker, result = self._start_call(
            lambda: coordinator.acquire(client, "drive", operation_id="op-a")
        )
        try:
            self.assertTrue(wal.entered.wait(2.0), boundary)
            duplicate = coordinator.acquire(client, "drive", operation_id="op-a")
            decision = coordinator.authorize(client, "token-a", "world_spawn")
            heartbeat = coordinator.heartbeat(client, "token-a")
            release = coordinator.release(client, "token-a")
            status = coordinator.status(client)

            self.assertFalse(decision.allowed)
            self.assertNotEqual(heartbeat[0], 200)
            self.assertNotEqual(release[0], 200)
            self.assertNotEqual(status["self"]["state"], "active")
            self.assertIsNone(status["owner"])
            self.assertNotIn("lease_token", repr((duplicate, status)))
            self.assertFalse("lease_token" in duplicate[1])
        finally:
            wal.resume.set()
            worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result["value"][0], 200)
        self.assertEqual(result["value"][1]["lease_token"], "token-a")
        self.assertTrue(
            coordinator.authorize(client, "token-a", "world_spawn").allowed
        )

    def test_immediate_grant_exposes_no_authority_at_any_wal_boundary(self) -> None:
        for boundary in ("transition", "snapshot", "clear"):
            with self.subTest(boundary=boundary):
                self._assert_immediate_is_fenced(boundary)

    def _assert_fifo_is_fenced(self, boundary: str) -> None:
        wal = _BlockingWal(boundary)
        wal.enabled = False
        coordinator = self._coordinator(
            wal,
            tokens=iter(("token-a", "token-b")),
            ids=iter(("lease-a", "ticket-b", "lease-b")),
        )
        owner = _identity("a")
        waiter = _identity("b")
        active = coordinator.acquire(owner, "drive")[1]
        queued = coordinator.acquire(
            waiter, "camera", operation_id="op-b"
        )[1]
        self.assertEqual(coordinator.release(owner, active["lease_token"])[0], 200)

        wal.enabled = True
        worker, result = self._start_call(
            lambda: coordinator.wait(waiter, queued["ticket"], 0.0)
        )
        try:
            self.assertTrue(wal.entered.wait(2.0), boundary)
            duplicate_acquire = coordinator.acquire(
                waiter, "camera", operation_id="op-b"
            )
            duplicate_wait = coordinator.wait(waiter, queued["ticket"], 0.0)
            decision = coordinator.authorize(waiter, "token-b", "world_spawn")
            heartbeat = coordinator.heartbeat(waiter, "token-b")
            release = coordinator.release(waiter, "token-b")
            status = coordinator.status(waiter)

            self.assertFalse(decision.allowed)
            self.assertNotEqual(heartbeat[0], 200)
            self.assertNotEqual(release[0], 200)
            self.assertNotEqual(status["self"]["state"], "active")
            self.assertIsNone(status["owner"])
            self.assertNotIn(
                "lease_token",
                repr((duplicate_acquire, duplicate_wait, status)),
            )
        finally:
            wal.resume.set()
            worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result["value"][0], 200)
        self.assertEqual(result["value"][1]["lease_token"], "token-b")
        self.assertTrue(
            coordinator.authorize(waiter, "token-b", "world_spawn").allowed
        )

    def test_fifo_grant_exposes_no_authority_at_any_wal_boundary(self) -> None:
        for boundary in ("transition", "snapshot", "clear"):
            with self.subTest(boundary=boundary):
                self._assert_fifo_is_fenced(boundary)

    def test_clear_pending_never_returns_or_authorizes_the_provisional_token(self) -> None:
        wal = _BlockingWal("clear", clear_succeeds=False)
        coordinator = self._coordinator(wal)
        client = _identity("a")
        worker, result = self._start_call(
            lambda: coordinator.acquire(client, "drive", operation_id="op-a")
        )
        try:
            self.assertTrue(wal.entered.wait(2.0))
            self.assertFalse(
                coordinator.authorize(client, "token-a", "world_spawn").allowed
            )
        finally:
            wal.resume.set()
            worker.join(2.0)

        self.assertFalse(worker.is_alive())
        response = result["value"]
        self.assertEqual((response[0], response[1]["error"]), (503, "audit_failed"))
        self.assertNotIn("lease_token", repr(response))
        self.assertFalse(
            coordinator.authorize(client, "token-a", "world_spawn").allowed
        )
        status = coordinator.status(client)
        self.assertNotEqual(status["self"]["state"], "active")
        self.assertIsNone(status["owner"])


# --- from test_authority_invariants_are_gated.py ---
# Two authority checks that nothing was defending.
#
# Found by mutation: break either rule below and the whole suite still passes.
#
# `claim_dispatch` is the worse of the two, and the reason it went unnoticed is
# visible in the test suite itself -- it appears only as an injection point
# (`coordinator.claim_dispatch = paused`, test_loopback_authority_quarantine.py:142),
# never as the subject of an assertion. A function that is only ever mocked is a
# function whose behaviour nobody checks.
#
# What these defend:
#
# * a queued mutation is dispatched only if it is still the *same command* the
#   active lease committed under that id. Without the committed_commands check,
#   any id belonging to the active lease dispatches -- including one the lease
#   never authorised.
# * an authorization commits only against the lease it was issued for. Without the
#   lease_id check, a session that reacquired (new lease, same session id) can
#   commit a reservation issued to the previous lease.


class AuthorityInvariantsAreGatedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = SessionCoordinator(
            time_fn=FakeClock(),
            token_fn=SequentialIds("secret-token"),
            id_fn=SequentialIds("public-id"),
            audit=AuditSink(),
            cleanup=CleanupSink(),
        )
        self.a = _identity("a")
        self.b = _identity("b")

    def _commit(self, command_id: int, command: str = "vehicle_trace") -> str:
        """Acquire, authorize and commit one command. Returns the lease id."""
        token = self.coordinator.acquire(self.a, "trace")[1]["lease_token"]
        decision = self.coordinator.authorize(self.a, token, command)
        self.assertTrue(
            self.coordinator.commit_authorization(
                self.a.session_id,
                decision.lease_id,
                decision.reservation_id,
                command_id,
                command,
                {"mode": "start"},
            )
        )
        return decision.lease_id

    # --- claim_dispatch ----------------------------------------------------

    def test_claim_dispatch_accepts_the_command_that_was_committed(self) -> None:
        lease_id = self._commit(52)
        self.assertTrue(
            self.coordinator.claim_dispatch(
                self.a.session_id, lease_id, 52, "vehicle_trace"
            )
        )

    def test_claim_dispatch_refuses_an_id_the_lease_never_committed(self) -> None:
        # THE mutation that survived: drop the committed_commands check and this
        # returns True, dispatching a mutation the lease never authorised.
        lease_id = self._commit(52)
        self.assertFalse(
            self.coordinator.claim_dispatch(
                self.a.session_id, lease_id, 53, "vehicle_trace"
            ),
            "an id the lease never committed must not dispatch",
        )

    def test_claim_dispatch_refuses_a_different_command_under_a_committed_id(self) -> None:
        # Same id, different verb: the commitment is (id -> command), not id alone.
        lease_id = self._commit(52)
        self.assertFalse(
            self.coordinator.claim_dispatch(
                self.a.session_id, lease_id, 52, "vehicle_control"
            ),
            "a committed id must not dispatch a different command",
        )

    def test_claim_dispatch_refuses_a_stale_lease_id(self) -> None:
        self._commit(52)
        self.assertFalse(
            self.coordinator.claim_dispatch(
                self.a.session_id, "lease-that-never-existed", 52, "vehicle_trace"
            )
        )

    # --- commit_authorization ---------------------------------------------

    def test_commit_authorization_refuses_a_foreign_lease_id(self) -> None:
        # The other surviving mutation: drop the lease_id comparison and a
        # reservation commits against a lease it was not issued for.
        token = self.coordinator.acquire(self.a, "trace")[1]["lease_token"]
        decision = self.coordinator.authorize(self.a, token, "vehicle_trace")
        self.assertFalse(
            self.coordinator.commit_authorization(
                self.a.session_id,
                decision.lease_id + "-not-mine",
                decision.reservation_id,
                60,
                "vehicle_trace",
                {"mode": "start"},
            ),
            "a reservation must not commit against a lease id it was not issued for",
        )

    def test_commit_authorization_refuses_a_foreign_session(self) -> None:
        token = self.coordinator.acquire(self.a, "trace")[1]["lease_token"]
        decision = self.coordinator.authorize(self.a, token, "vehicle_trace")
        self.assertFalse(
            self.coordinator.commit_authorization(
                self.b.session_id,
                decision.lease_id,
                decision.reservation_id,
                61,
                "vehicle_trace",
                {"mode": "start"},
            )
        )


# --- from test_task7_review_regressions.py ---


class ExactLeaseBarrierTest(unittest.TestCase):
    def test_authorization_decision_carries_exact_lease_and_reservation_ids(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("id"), audit=lambda _event: True
        )
        _, acquired = coordinator.acquire(IDENTITY, "exact")
        decision = coordinator.authorize(
            IDENTITY, acquired["lease_token"], "world_time_set"
        )
        self.assertEqual(decision.lease_id, acquired["lease_id"])
        self.assertIsInstance(decision.reservation_id, str)
        self.assertTrue(decision.reservation_id)

    def _run_stale_lifecycle(self, action):
        fixture = LifecycleFixture()
        entered = threading.Event()
        resume = threading.Event()
        original = fixture.lifecycle._authorize

        def paused(client, token, command):
            decision = original(client, token, command)
            entered.set()
            self.assertTrue(resume.wait(2.0))
            return decision

        fixture.lifecycle._authorize = paused  # type: ignore[method-assign]
        results: list[dict[str, object]] = []
        worker = threading.Thread(target=lambda: results.append(action(fixture)))
        worker.start()
        self.assertTrue(entered.wait(1.0), "authorization barrier not reached")
        _, replacement_lease = fixture.release_and_reacquire()
        resume.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        return fixture, results[0], replacement_lease

    def test_stale_l1_start_cannot_launch_or_persist_under_l2(self) -> None:
        fixture, result, replacement_lease = self._run_stale_lifecycle(
            lambda item: item.lifecycle.start_run(IDENTITY, item.token, item.request())
        )
        try:
            self.assertNotIn("ok", result)
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.store.list_runs(), [])
            self.assertNotEqual(fixture.lease_id, replacement_lease)
        finally:
            fixture.close()

    def test_stale_l1_adopt_cannot_snapshot_or_persist_under_l2(self) -> None:
        holder: dict[str, ProcessRecord] = {}

        def action(fixture: LifecycleFixture):
            process = record(7101)
            holder["process"] = process
            fixture.add_run(process, state="RUNNING_IDLE", owned=False)
            fixture.guard.snapshots[process.pid] = identity(process)
            return fixture.lifecycle.adopt_run(IDENTITY, fixture.token, "existing")

        fixture, result, _ = self._run_stale_lifecycle(action)
        try:
            self.assertNotIn("ok", result)
            self.assertEqual(fixture.guard.snapshot_calls, [])
            run = fixture.store.get("existing")
            self.assertEqual((run.state, run.owner_session_id, run.owner_lease_id), ("RUNNING_IDLE", None, None))
        finally:
            fixture.close()

    def test_stale_l1_stop_cannot_snapshot_or_terminate_under_l2(self) -> None:
        def action(fixture: LifecycleFixture):
            process = record(7102)
            fixture.add_run(process, state="RUNNING", owned=True)
            fixture.guard.snapshots[process.pid] = identity(process)
            return fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")

        fixture, result, _ = self._run_stale_lifecycle(action)
        try:
            self.assertNotIn("ok", result)
            self.assertEqual(fixture.guard.snapshot_calls, [])
            self.assertEqual(fixture.guard.terminate_calls, [])
            run = fixture.store.get("existing")
            self.assertEqual(
                (run.state, run.owner_session_id, run.owner_lease_id),
                ("RUNNING", IDENTITY.session_id, fixture.lease_id),
            )
        finally:
            fixture.close()

    def test_stale_l1_enqueue_cannot_be_delivered_after_same_identity_gets_l2(self) -> None:
        state = loopback.ServerState("key")
        bind_both_peers(state)
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda _event: True,
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        _, first = coordinator.acquire(IDENTITY, "first")
        entered = threading.Event()
        resume = threading.Event()
        original = coordinator.authorize

        def paused(*args, **kwargs):
            decision = original(*args, **kwargs)
            entered.set()
            self.assertTrue(resume.wait(2.0))
            return decision

        coordinator.authorize = paused  # type: ignore[method-assign]
        results: list[tuple[int, dict]] = []
        worker = threading.Thread(
            target=lambda: results.append(
                state.enqueue_command(
                    "world_time_set",
                    {"year": 2026, "month": 1, "day": 1, "hour": 0, "minute": 0},
                    "server",
                    identity_payload=IDENTITY_PAYLOAD,
                    lease_token=first["lease_token"],
                )
            )
        )
        worker.start()
        self.assertTrue(entered.wait(1.0))
        self.assertEqual(coordinator.release(IDENTITY, first["lease_token"])[0], 200)
        self.assertEqual(coordinator.acquire(IDENTITY, "second")[0], 200)
        resume.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertNotEqual(results[0][0], 200)
        self.assertEqual(accredited_poll(state, "server")[1]["commands"], [])


# --- from test_task7_rereview_regressions.py ---


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
                steam_gate=FakeSteamGate(),
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
                {"year": 2026, "month": 1, "day": 1, "hour": 0, "minute": 0},
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


# --- from test_task7_final_authority_regressions.py ---


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
        second_waiting = threading.Event()
        queued_events: list[str] = []
        blocked_once = False

        class WaitProbe(threading.Condition):
            def wait(self, timeout: float | None = None) -> bool:
                if threading.current_thread().name == "same-b":
                    second_waiting.set()
                return super().wait(timeout)

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
                resume_first_audit.wait(5.0)
            if event.get("event") == "session_queued":
                queued_events.append(str(event.get("ticket")))
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        coordinator._condition = WaitProbe()
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
        # RELEASE_AUDIT_TIMEOUT_S bounds the same-client reservation wait (pinned by
        # test_same_client_retry_is_bounded_while_first_audit_is_stuck). Widen it so
        # the barriers, not scheduler latency, decide the outcome.
        with patch.object(coordination_module, "RELEASE_AUDIT_TIMEOUT_S", 10.0):
            threads[0].start()
            try:
                self.assertTrue(first_audit_entered.wait(1.0))
                threads[1].start()
                # same-a holds the audit gate: same-b can only wait on its reservation.
                self.assertTrue(second_waiting.wait(2.0))
            finally:
                resume_first_audit.set()
                for thread in threads:
                    if thread.is_alive():
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
        audit_left = threading.Event()
        retry_thread = threading.current_thread()
        retry_wait_timeouts: list[float | None] = []

        class WaitProbe(threading.Condition):
            def wait(self, timeout: float | None = None) -> bool:
                # Once the first audit is stuck, the test thread only runs the retry.
                if entered.is_set() and threading.current_thread() is retry_thread:
                    retry_wait_timeouts.append(timeout)
                return super().wait(timeout)

        def audit(event: dict[str, object]) -> bool:
            client = event.get("client") or {}
            if (
                event.get("event") == "session_acquire"
                and client.get("session") == IDENTITY_B.session_id[:12]
            ):
                entered.set()
                # Stuck until the retry has answered; the timeout only ends a retry
                # that waits for this audit instead of answering.
                resume.wait(5.0)
                audit_left.set()
            return True

        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=audit
        )
        coordinator._condition = WaitProbe()
        coordinator.acquire(IDENTITY, "owner")
        first_result: list[tuple[int, dict]] = []
        first = threading.Thread(
            target=lambda: first_result.append(
                coordinator.acquire(IDENTITY_B, "same")
            )
        )
        first.start()
        self.assertTrue(entered.wait(1.0))
        try:
            retry_status, retry_payload = coordinator.acquire(IDENTITY_B, "same")
            answered_while_audit_stuck = not audit_left.is_set()
        finally:
            resume.set()
            first.join(2.0)

        self.assertTrue(answered_while_audit_stuck)
        self.assertEqual(retry_status, 503)
        self.assertEqual(retry_payload["error"], "audit_failed")
        # The bound is the timeout the reservation wait passes, not wall-clock time:
        # scheduler latency delays the answer but cannot raise it. Every wait (none
        # if the deadline passed before the first) stays within
        # RELEASE_AUDIT_TIMEOUT_S, up to float rounding of deadline - now.
        self.assertNotIn(None, retry_wait_timeouts)
        self.assertLessEqual(
            max(retry_wait_timeouts, default=0.0),
            coordination_module.RELEASE_AUDIT_TIMEOUT_S + 1e-6,
        )
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

    @slow_test
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

    @slow_test
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


# --- from test_0ab2_r9.py ---
# W7a DZ-R9 offline for 0ab2 (state-machine, race, identity, data-loss).
#
# In-game H8 is W7b / I3 — not this module.


class IdentityR9Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")

    def test_grace_uses_full_client_identity_not_session_prefix(self) -> None:
        clock = FakeClock()
        former = _identity("a", session_id="session-abcdefghijklmnop")
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(former, "drive")
        clock.advance(MID)
        prefix = ClientIdentity.from_payload(
            {
                "platform": former.platform,
                "pid": former.pid,
                "ppid": former.ppid,
                "started_at_utc": former.started_at_utc,
                "session_id": former.session_id[:12],
                "task_label": former.task_label,
            }
        )
        self.assertNotEqual(prefix, former)
        status, payload = coordinator.acquire(prefix, "drive")
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(coordinator.acquire(former, "drive")[0], 200)

    def test_same_session_id_different_pid_is_identity_mismatch(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        spoof = ClientIdentity.from_payload(
            {
                "platform": self.a.platform,
                "pid": self.a.pid + 1,
                "ppid": self.a.ppid,
                "started_at_utc": self.a.started_at_utc,
                "session_id": self.a.session_id,
                "task_label": self.a.task_label,
            }
        )
        status, payload = coordinator.acquire(spoof, "drive")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "identity_mismatch")
        self.assertEqual(coordinator.acquire(self.a, "drive")[0], 200)

    def test_owner_prefix_twelve_matches_caller_full_session(self) -> None:
        session = "session-abcdefghijklmnop"
        box = {
            "runs": [
                {
                    "run_id": "abc",
                    "state": "RUNNING",
                    "owner_session": session[:12],
                }
            ]
        }
        self.assertIsNone(takeover_target_run_id(box, caller_session=session))
        fields = occupancy_error_fields(box, caller_session=session)
        self.assertIn("dayz_test_stop", str(fields.get("hint") or ""))

    def test_stranger_full_session_is_takeover_target(self) -> None:
        box = {
            "runs": [
                {
                    "run_id": "abc",
                    "state": "RUNNING",
                    "owner_session": "session-owner",
                }
            ]
        }
        self.assertEqual(
            takeover_target_run_id(box, caller_session="session-other"),
            "abc",
        )


if __name__ == "__main__":
    unittest.main()
