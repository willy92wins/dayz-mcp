from __future__ import annotations

import copy
import threading
import unittest

from dayz_mcp.session_coordination import SessionCoordinator
from tests.test_session_coordination import _identity


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


if __name__ == "__main__":
    unittest.main()
