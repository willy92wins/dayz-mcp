"""Two authority checks that nothing was defending.

Found by mutation: break either rule below and the whole suite still passes.

`claim_dispatch` is the worse of the two, and the reason it went unnoticed is
visible in the test suite itself -- it appears only as an injection point
(`coordinator.claim_dispatch = paused`, test_task7_review_regressions.py:582),
never as the subject of an assertion. A function that is only ever mocked is a
function whose behaviour nobody checks.

What these defend:

* a queued mutation is dispatched only if it is still the *same command* the
  active lease committed under that id. Without the committed_commands check,
  any id belonging to the active lease dispatches -- including one the lease
  never authorised.
* an authorization commits only against the lease it was issued for. Without the
  lease_id check, a session that reacquired (new lease, same session id) can
  commit a reservation issued to the previous lease.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from tests.test_session_coordination import (  # noqa: E402
    AuditSink,
    CleanupSink,
    FakeClock,
    SequentialIds,
    _identity,
)

from dayz_mcp.session_coordination import SessionCoordinator  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
