"""SessionCoordinator contract smoke tests.

The GATES R1 list names this module; the coordination behaviour it covers is
the lease/operation authority every lifecycle test in this tree depends on.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator

IDENTITY_A = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")
IDENTITY_B = ClientIdentity("claude", 22, 2, "2026-07-15T00:00:01Z", "B", "other")
OPERATION_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"


class SessionCoordinationTest(unittest.TestCase):
    def test_acquire_and_release(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=lambda: "token-1", id_fn=lambda: "lease-1"
        )
        status, acquired = coordinator.acquire(IDENTITY_A, "dayz-test")
        self.assertEqual(status, 200, acquired)
        self.assertEqual(acquired["status"], "active")
        token = acquired["lease_token"]
        self.assertEqual(token, "token-1")
        status, released = coordinator.release(IDENTITY_A, token)
        self.assertEqual(status, 200, released)

    def test_cancel_operation_releases_the_active_lease(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=lambda: "token-1", id_fn=lambda: "lease-1"
        )
        status, acquired = coordinator.acquire(
            IDENTITY_A, "dayz-test", operation_id=OPERATION_ID
        )
        self.assertEqual(status, 200, acquired)
        self.assertEqual(acquired["status"], "active")
        status, cancelled = coordinator.cancel_operation(
            IDENTITY_A, OPERATION_ID
        )
        self.assertEqual(status, 200, cancelled)
        self.assertIs(cancelled.get("cancelled"), True)
        self.assertEqual(cancelled.get("operation_id"), OPERATION_ID)

    def test_another_session_queues_while_a_lease_is_active(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=lambda: "token-1", id_fn=lambda: "lease-1"
        )
        status, acquired = coordinator.acquire(IDENTITY_A, "dayz-test")
        self.assertEqual(status, 200, acquired)
        status, queued = coordinator.acquire(IDENTITY_B, "other")
        self.assertEqual(status, 202, queued)
        self.assertEqual(queued["status"], "queued")
        self.assertIn("ticket", queued)
        self.assertIn("position", queued)


if __name__ == "__main__":
    unittest.main()
