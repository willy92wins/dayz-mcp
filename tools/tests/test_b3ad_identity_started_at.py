"""b3ad: an identity the WAL would refuse is refused at admission.

ClientIdentity.from_payload applies the same predicates
CoordinationFaultStore.validate applies to the WAL marker, so a
"2026-11-12T10:00:00.000000+00:00" identity gets invalid_identity at
acquire instead of being queued and failing wal_arm at grant.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.runtime_state import (
    CoordinationFaultStore,
    CoordinationSnapshotStore,
    RuntimePaths,
)
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.lease_helpers import _identity
from tests.session_http_helpers import SessionLoopback


BASE_PAYLOAD = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-11-12T10:00:00Z",
    "session_id": "session-a",
    "task_label": "b3ad",
}

PLUS_OFFSET_PAYLOAD = BASE_PAYLOAD | {
    "started_at_utc": "2026-11-12T10:00:00.000000+00:00",
    "session_id": "session-plus-offset",
}

# (started_at_utc, accepted) -- the accepted column is what
# CoordinationFaultStore.validate does with the same value, guarded below.
STARTED_CASES = [
    ("2026-11-12T10:00:00.000000+00:00", False),
    ("2026-11-12T10:00:00", False),
    ("2026-11-12T10:00:00+01:00", False),
    # A space separator still ends in Z and fromisoformat parses it once the
    # Z is rewritten, so the WAL (and therefore admission) accepts it.
    ("2026-11-12 10:00:00Z", True),
    ("2026-11-12T10:00:00." + "0" * 44 + "Z", False),  # 65 characters
    ("2026-11-12T10:00:00\x01Z", False),
    ("", False),
    ("2026-11-12T10:00:00Z", True),
    ("2026-11-12T10:00:00.000000Z", True),
]

SESSION_CASES = [
    # The WAL receives session_id[:12] (public_payload), so length past 12
    # and characters past 12 do not reach its bounded_text(session, 200).
    ("s" * 300, True),
    ("s" * 201, True),
    ("s" * 200, True),
    (" " * 12 + "x", False),  # first 12 all blank: the WAL would refuse it
    ("session-\x01id", False),  # control character within the first 12
    ("s" * 12 + "\x01" + "x", True),  # control character after position 12
    ("session-a", True),
]


def _payload(**changes: object) -> dict[str, object]:
    payload = dict(BASE_PAYLOAD)
    payload.update(changes)
    return payload


def _marker(
    *, started_at_utc: str = "2026-07-22T00:00:00Z", session: str = "session-a"
) -> dict[str, object]:
    return {
        "format_version": 1,
        "fault_id": "fault-1",
        "daemon_generation": "generation-a",
        "state": "armed",
        "operation": "grant",
        "phase": "armed",
        "lease_id": "lease-1",
        "ticket_id": "ticket-1",
        "client": {
            "platform": "codex",
            "session": session,
            "started_at_utc": started_at_utc,
            "task_label": "queue wait",
        },
        "reason": "fifo_head",
        "armed_at_utc": "2026-07-22T00:00:00Z",
        "failure": None,
        "expected_snapshot_revision": 7,
        "repair_phase": "none",
    }


def _wal_accepts(marker: dict[str, object]) -> bool:
    try:
        CoordinationFaultStore.validate(marker)
    except ValueError:
        return False
    return True


def _wal_accepts_for(candidate: ClientIdentity) -> bool:
    # Build the marker's client from the plain constructor's public_payload()
    # (bypassing from_payload), exactly what _arm_wal_locked puts in the WAL.
    marker = _marker()
    marker["client"] = candidate.public_payload()
    return _wal_accepts(marker)


def _identity_accepts(payload: dict[str, object]) -> bool:
    try:
        ClientIdentity.from_payload(payload)
    except ValueError as exc:
        if str(exc) != "invalid_identity":
            raise
        return False
    return True


class IdentityAdmissionTests(unittest.TestCase):
    def test_plus_offset_started_at_raises_invalid_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "^invalid_identity$"):
            ClientIdentity.from_payload(PLUS_OFFSET_PAYLOAD)

    def test_started_at_table(self) -> None:
        for value, accepted in STARTED_CASES:
            with self.subTest(started_at_utc=value):
                if accepted:
                    identity = ClientIdentity.from_payload(
                        _payload(started_at_utc=value)
                    )
                    # Nothing is rewritten: the value survives as sent.
                    self.assertEqual(identity.started_at_utc, value)
                else:
                    with self.assertRaisesRegex(ValueError, "^invalid_identity$"):
                        ClientIdentity.from_payload(_payload(started_at_utc=value))

    def test_session_id_bounds(self) -> None:
        for value, accepted in SESSION_CASES:
            with self.subTest(session_id=value):
                if accepted:
                    identity = ClientIdentity.from_payload(
                        _payload(session_id=value)
                    )
                    self.assertEqual(identity.session_id, value)
                else:
                    with self.assertRaisesRegex(ValueError, "^invalid_identity$"):
                        ClientIdentity.from_payload(_payload(session_id=value))

    def test_identity_predicate_matches_wal_for_started_at(self) -> None:
        for value, _accepted in STARTED_CASES:
            with self.subTest(started_at_utc=value):
                candidate = ClientIdentity(
                    "codex", 11, 1, value, "session-a", "b3ad"
                )
                self.assertEqual(
                    _identity_accepts(_payload(started_at_utc=value)),
                    _wal_accepts_for(candidate),
                )

    def test_identity_predicate_matches_wal_for_session(self) -> None:
        for value, _accepted in SESSION_CASES:
            with self.subTest(session_id=value):
                candidate = ClientIdentity(
                    "codex", 11, 1, "2026-11-12T10:00:00Z", value, "b3ad"
                )
                self.assertEqual(
                    _identity_accepts(_payload(session_id=value)),
                    _wal_accepts_for(candidate),
                )


class CoordinatorAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.paths = RuntimePaths(
            root=root,
            audit_dir=root / "audit",
            coordination_path=root / "coordination.json",
            runs_path=root / "runs.json",
        )
        self.fault_store = CoordinationFaultStore(self.paths)
        self.snapshot_store = CoordinationSnapshotStore(self.paths, "generation-a")
        self.events: list[dict[str, object]] = []
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "secret-token",
            id_fn=lambda: "lease-1",
            fault_id_fn=lambda: "fault-1",
            daemon_generation="generation-a",
            utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            audit=lambda event: self.events.append(dict(event)) or True,
            fault_arm=self.fault_store.arm,
            fault_transition=self.fault_store.transition,
            fault_clear=self.fault_store.clear,
            persist_snapshot=self.snapshot_store.write_coordination,
        )

    def test_plus_offset_identity_never_reaches_the_queue(self) -> None:
        status, payload = self.coordinator.acquire(_identity("a"), "drive")
        self.assertEqual((status, payload["status"]), (200, "active"))

        # The loopback builds the identity through from_payload before it
        # calls the coordinator, so the refusal leaves no ticket behind.
        with self.assertRaisesRegex(ValueError, "^invalid_identity$"):
            ClientIdentity.from_payload(PLUS_OFFSET_PAYLOAD)

        snapshot = self.coordinator.snapshot_payload()
        self.assertEqual(snapshot["queue"], [])
        self.assertEqual(self.fault_store.load_with_sha(), (None, None))


class B3adHttpTests(SessionLoopback, unittest.TestCase):
    def test_acquire_plus_offset_is_400_and_queue_stays_empty(self) -> None:
        status, body = self.request(
            "/session/acquire", {"identity": PLUS_OFFSET_PAYLOAD, "purpose": "drive"}
        )
        self.assertEqual((status, body), (400, {"error": "invalid_identity"}))

        status, snapshot = self.request("/session/status", {"identity": BASE_PAYLOAD})
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["queue"], [])
        self.assertIsNone(snapshot["owner"])

        # The control with a Z identity is still admitted.
        status, body = self.request(
            "/session/acquire", {"identity": BASE_PAYLOAD, "purpose": "drive"}
        )
        self.assertEqual((status, body["status"]), (200, "active"))

        queued_payload = _payload(session_id="session-queued", pid=22)
        status, body = self.request(
            "/session/acquire", {"identity": queued_payload, "purpose": "next"}
        )
        self.assertEqual((status, body["status"]), (202, "queued"))

    def test_abandon_plus_offset_is_400_bad_abandon_and_queue_stays_empty(
        self,
    ) -> None:
        # /abandon does not surface invalid_identity: request_abandon maps a
        # from_payload failure to its own bad_abandon (loopback.py:6393-6396).
        status, body = self.request(
            "/abandon",
            {
                "identity": PLUS_OFFSET_PAYLOAD,
                "generation": "g",
                "id": 1,
                "reason": "cancelled",
            },
        )
        self.assertEqual((status, body), (400, {"error": "bad_abandon"}))

        status, snapshot = self.request("/session/status", {"identity": BASE_PAYLOAD})
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["queue"], [])
        self.assertIsNone(snapshot["owner"])

    def test_wait_cancel_and_release_plus_offset_are_400(self) -> None:
        for path, extra in (
            ("/session/wait", {"ticket": "ticket-1", "timeout_s": 1}),
            ("/session/cancel", {"ticket": "ticket-1"}),
            ("/session/release", {"lease_token": "token"}),
        ):
            with self.subTest(path=path):
                status, body = self.request(
                    path, {"identity": PLUS_OFFSET_PAYLOAD, **extra}
                )
                self.assertEqual((status, body), (400, {"error": "invalid_identity"}))


if __name__ == "__main__":
    unittest.main()
