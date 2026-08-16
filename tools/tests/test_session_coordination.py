from __future__ import annotations

import dataclasses
import json
import sys
import threading
import time
import unittest
from pathlib import Path


# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.session_coordination import (
    MAX_OPERATION_PIN_S,
    MAX_SESSION_QUEUE,
    READ_ONLY_COMMANDS,
    SESSION_TTL_S,
    WAIT_MAX_S,
    AuthorizationDecision,
    CleanupDisposition,
    ClientIdentity,
    SessionCoordinator,
    command_requires_lease,
)


READ_ONLY = {
    "query_player_state",
    "query_all_players",
    "scene_raycast",
    "telemetry_read",
    "query_get_in_condition",
    "camera_get",
    "vehicle_telemetry",
    "logs_since",
    "surface_query",
    "object_inspect",
}


class IdentityAndClassificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.identity_payload = {
            "platform": "codex",
            "pid": 101,
            "ppid": 10,
            "started_at_utc": "2026-07-14T20:00:00Z",
            "session_id": "session-abcdefghijklmnop",
            "task_label": "state machine",
        }

    def test_constants_and_read_only_set_are_exact(self) -> None:
        self.assertEqual(SESSION_TTL_S, 120.0)
        self.assertEqual(WAIT_MAX_S, 30.0)
        self.assertEqual(MAX_SESSION_QUEUE, 64)
        self.assertEqual(MAX_OPERATION_PIN_S, 300.0)
        self.assertEqual(READ_ONLY_COMMANDS, frozenset(READ_ONLY))

    def test_unknown_command_is_mutating_by_default(self) -> None:
        self.assertFalse(command_requires_lease("camera_get"))
        self.assertTrue(command_requires_lease("brand_new_tool"))

    def test_query_all_players_is_a_pure_read(self) -> None:
        # F1.1: it only reads authoritative server state, so it must not gate on
        # the lease. Negative control below: mutating verbs still require it.
        self.assertFalse(command_requires_lease("query_all_players"))
        for mutating in ("world_spawn", "object_delete"):
            self.assertTrue(command_requires_lease(mutating), mutating)

    def test_identity_rejects_missing_or_invalid_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "^invalid_identity$"):
            ClientIdentity.from_payload({"platform": "codex"})
        with self.assertRaisesRegex(ValueError, "^invalid_identity$"):
            ClientIdentity.from_payload(self.identity_payload | {"pid": True})

        invalid_values = [
            None,
            [],
            self.identity_payload | {"platform": "other"},
            self.identity_payload | {"pid": -1},
            self.identity_payload | {"ppid": False},
            self.identity_payload | {"started_at_utc": ""},
            self.identity_payload | {"session_id": ""},
            self.identity_payload | {"task_label": 1},
            self.identity_payload | {"task_label": "x" * 121},
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "^invalid_identity$"):
                    ClientIdentity.from_payload(value)

    def test_identity_is_immutable_and_payloads_are_redacted_as_required(self) -> None:
        identity = ClientIdentity.from_payload(self.identity_payload)
        self.assertEqual(identity.to_payload(), self.identity_payload)
        self.assertEqual(
            identity.public_payload(),
            {
                "platform": "codex",
                "session": "session-abcd",
                "started_at_utc": "2026-07-14T20:00:00Z",
                "task_label": "state machine",
            },
        )
        self.assertNotIn("pid", identity.public_payload())
        self.assertNotIn("ppid", identity.public_payload())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            identity.pid = 999  # type: ignore[misc]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SequentialIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next_value = 1

    def __call__(self) -> str:
        value = f"{self.prefix}-{self.next_value}"
        self.next_value += 1
        return value


class AuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.fail_events: set[str] = set()

    def __call__(self, event: dict[str, object]) -> None:
        if event.get("event") in self.fail_events:
            raise OSError("audit unavailable")
        self.events.append(dict(event))


class CleanupSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(
        self, session_id: str, _lease_id: str, reason: str, vehicle_active: bool
    ) -> dict[str, object]:
        self.calls.append((session_id, reason, vehicle_active))
        return {"cancelled": 0, "vehicle_release": vehicle_active}


def _identity(name: str, *, session_id: str | None = None) -> ClientIdentity:
    return ClientIdentity.from_payload(
        {
            "platform": "codex" if name != "b" else "claude",
            "pid": 100 + ord(name[0]),
            "ppid": 10,
            "started_at_utc": f"2026-07-14T20:00:0{len(name)}Z",
            "session_id": session_id or f"session-{name}",
            "task_label": name,
        }
    )


class SessionCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.audit = AuditSink()
        self.cleanup = CleanupSink()
        self.tokens = SequentialIds("secret-token")
        self.ids = SequentialIds("public-id")
        self.coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            cleanup=self.cleanup,
        )
        self.a = _identity("a")
        self.b = _identity("b")
        self.c = _identity("c")

    def _fresh(self) -> tuple[FakeClock, SessionCoordinator]:
        clock = FakeClock()
        return clock, SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=CleanupSink(),
        )

    def test_fenced_cleanup_timeout_keeps_fifo_until_terminal_callback(self) -> None:
        terminal = threading.Event()
        terminal_result: dict[str, object] = {}

        def cleanup(*_args: object) -> CleanupDisposition:
            return CleanupDisposition(
                fence_required=True,
                terminal_event=terminal,
                terminal_result=terminal_result,
            )

        coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            cleanup=cleanup,
            cleanup_timeout_s=0.01,
        )
        _, active = coordinator.acquire(self.a, "owner")
        status, queued = coordinator.acquire(self.b, "next")
        self.assertEqual(status, 202)

        release_status, release = coordinator.release(
            self.a, active["lease_token"]
        )
        self.assertEqual(release_status, 202)
        self.assertEqual(release["error"], "lifecycle_cleanup_pending")
        self.assertEqual(coordinator.wait(self.b, queued["ticket"], 0.0)[0], 202)
        self.assertEqual(coordinator.status(self.b)["self"]["position"], 1)

        terminal_result.update({"terminal_safe": True, "runs_released": ["R"]})
        terminal.set()
        deadline = time.monotonic() + 1.0
        while True:
            snapshot = coordinator.status(self.a)
            if (
                snapshot["self"]["state"] != "releasing"
                and snapshot["claimable"] is True
            ):
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)

        granted_status, granted = coordinator.wait(
            self.b, queued["ticket"], 0.0
        )
        self.assertEqual(granted_status, 200)
        self.assertEqual(granted["status"], "active")

    def test_ambiguous_fenced_cleanup_never_unfences_queue(self) -> None:
        terminal = threading.Event()
        terminal_result: dict[str, object] = {}
        coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            cleanup=lambda *_args: CleanupDisposition(
                True, terminal, terminal_result
            ),
            cleanup_timeout_s=0.01,
        )
        _, active = coordinator.acquire(self.a, "owner")
        _, queued = coordinator.acquire(self.b, "next")
        self.assertEqual(coordinator.release(self.a, active["lease_token"])[0], 202)
        terminal_result.update(
            {"terminal_safe": False, "error": "identity_ambiguous"}
        )
        terminal.set()
        time.sleep(0.02)

        self.assertEqual(coordinator.wait(self.b, queued["ticket"], 0.0)[0], 202)
        self.assertEqual(coordinator.status(self.a)["self"]["state"], "releasing")

    def test_repaired_lifecycle_fault_retires_release_fence_and_grants_once(self) -> None:
        terminal = threading.Event()
        terminal.set()
        coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            cleanup=lambda *_args: CleanupDisposition(
                True,
                terminal,
                {"terminal_safe": False, "error": "cleanup_failed"},
            ),
        )
        _, active = coordinator.acquire(self.a, "owner")
        _, queued = coordinator.acquire(self.b, "next")
        self.assertEqual(coordinator.release(self.a, active["lease_token"])[0], 202)
        coordinator.arm_lifecycle_recovery_fault(
            {"fault_id": "fault-release", "state": "armed"}
        )

        self.assertTrue(coordinator.complete_lifecycle_recovery_fault("fault-release"))
        self.assertFalse(coordinator.complete_lifecycle_recovery_fault("fault-release"))
        granted_status, granted = coordinator.wait(self.b, queued["ticket"], 0.0)

        self.assertEqual((granted_status, granted["status"]), (200, "active"))
        self.assertIsNone(coordinator.status(self.a)["lifecycle_recovery_fault"])

    def test_recovered_lifecycle_fault_accepts_fifo_but_grants_nothing(self) -> None:
        fault = {
            "fault_id": "11111111-1111-4111-8111-111111111111",
            "state": "armed",
        }
        coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            recovered_lifecycle_fault=fault,
        )

        status, queued = coordinator.acquire(self.a, "blocked-recovery")
        self.assertEqual(status, 202)
        self.assertEqual(coordinator.wait(self.a, queued["ticket"], 0.0)[0], 202)
        snapshot = coordinator.status(self.a)
        self.assertFalse(snapshot["claimable"])
        self.assertEqual(snapshot["lifecycle_recovery_fault"], fault)

        self.assertFalse(coordinator.clear_lifecycle_recovery_fault("wrong"))
        self.assertTrue(
            coordinator.clear_lifecycle_recovery_fault(fault["fault_id"])
        )
        granted_status, granted = coordinator.wait(
            self.a, queued["ticket"], 0.0
        )
        self.assertEqual((granted_status, granted["status"]), (200, "active"))

    def test_lifecycle_fault_arm_is_idempotent_but_rejects_drift(self) -> None:
        coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
        )
        fault = {"fault_id": "F", "state": "armed"}
        coordinator.arm_lifecycle_recovery_fault(fault)
        coordinator.arm_lifecycle_recovery_fault(dict(fault))
        with self.assertRaisesRegex(RuntimeError, "lifecycle_recovery_fault_exists"):
            coordinator.arm_lifecycle_recovery_fault(
                {"fault_id": "G", "state": "armed"}
            )

    def test_authorization_decision_is_immutable(self) -> None:
        decision = AuthorizationDecision(True, 200, "", "session-a")
        self.assertEqual(
            (decision.allowed, decision.http_status, decision.error, decision.owner_session_id),
            (True, 200, "", "session-a"),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.allowed = False  # type: ignore[misc]

    def test_authorize_transports_expiry_audit_degradation(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.audit.fail_events.add("session_expired")
        self.clock.advance(120.0)
        decision = self.coordinator.authorize(self.a, token, "world_spawn")
        self.assertEqual(
            (decision.allowed, decision.error),
            (False, "lease_expired"),
        )
        self.assertEqual(
            getattr(decision, "cleanup_degraded", None),
            ("audit_failed",),
        )

    def test_acquire_immediate_and_duplicate_are_idempotent(self) -> None:
        first = self.coordinator.acquire(self.a, "drive")
        duplicate = self.coordinator.acquire(self.a, "ignored-new-purpose")
        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]["status"], "active")
        self.assertEqual(first[1]["lease_token"], duplicate[1]["lease_token"])
        self.assertEqual(first[1]["lease_id"], duplicate[1]["lease_id"])
        self.assertEqual(self.coordinator.status(self.a)["queue"], [])

    def test_fifo_idempotent_and_abandoned_middle_ticket(self) -> None:
        active = self.coordinator.acquire(self.a, "drive")
        b1 = self.coordinator.acquire(self.b, "camera")
        b2 = self.coordinator.acquire(self.b, "camera")
        c = self.coordinator.acquire(self.c, "weather")
        self.assertEqual(b1[1]["ticket"], b2[1]["ticket"])
        self.assertEqual(c[1]["position"], 2)
        self.clock.advance(119.0)
        token = active[1]["lease_token"]
        self.assertTrue(self.coordinator.authorize(self.a, token, "world_spawn").allowed)
        self.clock.advance(1.0)
        self.coordinator.wait(self.c, c[1]["ticket"], 0.0)
        self.assertEqual(self.coordinator.status(self.c)["self"]["position"], 1)

    def test_release_grants_next_ticket_in_strict_fifo_order(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        b = self.coordinator.acquire(self.b, "camera")
        c = self.coordinator.acquire(self.c, "weather")
        status, body = self.coordinator.release(self.a, token)
        self.assertEqual((status, body["released"]), (200, True))
        b_wait = self.coordinator.wait(self.b, b[1]["ticket"], 0.0)
        self.assertEqual((b_wait[0], b_wait[1]["status"]), (200, "active"))
        self.assertEqual(self.coordinator.status(self.c)["self"]["position"], 1)
        self.assertEqual(self.cleanup.calls, [("session-a", "owner_release", False)])

    def test_queue_capacity_rejects_without_evicting_existing_tickets(self) -> None:
        self.coordinator.acquire(self.a, "active")
        queued = []
        for index in range(MAX_SESSION_QUEUE):
            queued.append(self.coordinator.acquire(_identity(f"q{index}"), "queued"))
        rejected = self.coordinator.acquire(_identity("overflow"), "queued")
        self.assertEqual((rejected[0], rejected[1]["error"]), (429, "queue_full"))
        self.assertEqual(len(self.coordinator.status(self.a)["queue"]), MAX_SESSION_QUEUE)
        self.assertEqual(queued[0][1]["position"], 1)
        self.assertEqual(queued[-1][1]["position"], MAX_SESSION_QUEUE)

    def test_lease_boundaries_are_independent_at_119_120_and_121(self) -> None:
        for elapsed, expected in ((119.0, True), (120.0, False), (121.0, False)):
            with self.subTest(elapsed=elapsed):
                clock, coordinator = self._fresh()
                token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
                clock.advance(elapsed)
                decision = coordinator.authorize(self.a, token, "world_spawn")
                self.assertEqual(decision.allowed, expected)
                if not expected:
                    self.assertEqual(decision.error, "lease_expired")

    def test_lease_renewal_boundary_is_exact(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.clock.advance(119.0)
        self.assertTrue(self.coordinator.authorize(self.a, token, "world_spawn").allowed)
        self.clock.advance(120.0)
        self.assertEqual(
            self.coordinator.authorize(self.a, token, "world_spawn").error,
            "lease_expired",
        )

    def test_ticket_boundaries_are_independent_at_119_120_and_121(self) -> None:
        for elapsed, expected_state in ((119.0, "queued"), (120.0, "none"), (121.0, "none")):
            with self.subTest(elapsed=elapsed):
                clock, coordinator = self._fresh()
                owner_token = coordinator.acquire(self.a, "active")[1]["lease_token"]
                ticket = coordinator.acquire(self.b, "queued")[1]["ticket"]
                if elapsed >= 120.0:
                    clock.advance(119.0)
                    coordinator.heartbeat(self.a, owner_token)
                    clock.advance(elapsed - 119.0)
                else:
                    clock.advance(elapsed)
                state = coordinator.status(self.b)["self"]["state"]
                self.assertEqual(state, expected_state)
                if expected_state == "queued":
                    self.assertEqual(coordinator.wait(self.b, ticket, 0.0)[1]["status"], "queued")

    def test_ticket_and_token_are_bound_to_full_identity(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        decision = self.coordinator.authorize(self.b, token, "world_spawn")
        self.assertEqual((decision.allowed, decision.error), (False, "lease_invalid"))

        ticket = self.coordinator.acquire(self.b, "camera")[1]["ticket"]
        stolen_wait = self.coordinator.wait(self.c, ticket, 0.0)
        self.assertEqual((stolen_wait[0], stolen_wait[1]["error"]), (403, "ticket_invalid"))

        colliding = _identity("collision", session_id=self.b.session_id)
        collision = self.coordinator.acquire(colliding, "other")
        self.assertEqual((collision[0], collision[1]["error"]), (403, "identity_mismatch"))

    def test_heartbeat_renews_owner_but_rejects_stolen_token(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.clock.advance(119.0)
        self.assertEqual(self.coordinator.heartbeat(self.a, token)[0], 200)
        self.clock.advance(119.0)
        self.assertTrue(self.coordinator.authorize(self.a, token, "world_spawn").allowed)
        stolen = self.coordinator.heartbeat(self.b, token)
        self.assertEqual((stolen[0], stolen[1]["error"]), (403, "lease_invalid"))

    def test_heartbeat_after_expiry_transports_audit_degradation(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.audit.fail_events.add("session_expired")
        self.clock.advance(120.0)
        status, body = self.coordinator.heartbeat(self.a, token)
        self.assertEqual((status, body["error"]), (409, "lease_expired"))
        self.assertEqual(body.get("cleanup_degraded"), ["audit_failed"])

    def test_release_after_expiry_transports_audit_degradation(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.audit.fail_events.add("session_expired")
        self.clock.advance(120.0)
        status, body = self.coordinator.release(self.a, token)
        self.assertEqual((status, body["error"]), (409, "lease_expired"))
        self.assertEqual(body.get("cleanup_degraded"), ["audit_failed"])

    def test_acquire_after_expiry_audit_failure_blocks_queued_grant(self) -> None:
        self.coordinator.acquire(self.a, "drive")
        self.clock.advance(1.0)
        self.coordinator.acquire(self.b, "camera")
        self.audit.fail_events.add("session_expired")
        self.clock.advance(119.0)
        status, body = self.coordinator.acquire(self.b, "camera")
        self.assertEqual((status, body["error"]), (503, "audit_failed"))
        self.assertEqual(body.get("cleanup_degraded"), ["audit_failed"])

    def test_invalid_wait_after_expiry_transports_audit_degradation(self) -> None:
        self.coordinator.acquire(self.a, "drive")
        self.audit.fail_events.add("session_expired")
        self.clock.advance(120.0)
        status, body = self.coordinator.wait(self.b, "not-a-ticket", 0.0)
        self.assertEqual((status, body["error"]), (403, "ticket_invalid"))
        self.assertEqual(body.get("cleanup_degraded"), ["audit_failed"])

    def test_read_without_token_does_not_renew_but_valid_token_does(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.clock.advance(119.0)
        self.assertTrue(self.coordinator.authorize(self.b, None, "camera_get").allowed)
        self.clock.advance(1.0)
        self.assertEqual(
            self.coordinator.authorize(self.a, token, "world_spawn").error,
            "lease_expired",
        )

        clock, coordinator = self._fresh()
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(119.0)
        self.assertTrue(coordinator.authorize(self.a, token, "camera_get").allowed)
        clock.advance(119.0)
        self.assertTrue(coordinator.authorize(self.a, token, "world_spawn").allowed)

    def test_read_with_invalid_optional_token_is_allowed_without_renewal(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.clock.advance(119.0)
        stolen = self.coordinator.authorize(self.b, token, "camera_get")
        unknown = self.coordinator.authorize(self.b, "not-a-lease", "camera_get")
        self.assertEqual(
            (stolen.allowed, stolen.error, stolen.owner_session_id),
            (True, "", None),
        )
        self.assertEqual(
            (unknown.allowed, unknown.error, unknown.owner_session_id),
            (True, "", None),
        )
        self.clock.advance(1.0)
        self.assertEqual(
            self.coordinator.authorize(self.a, token, "world_spawn").error,
            "lease_expired",
        )

    def test_read_with_expired_optional_token_is_still_allowed(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.clock.advance(120.0)
        decision = self.coordinator.authorize(self.a, token, "camera_get")
        self.assertEqual(
            (decision.allowed, decision.error, decision.owner_session_id),
            (True, "", None),
        )

    def test_mutating_authorization_pins_at_most_300_and_finish_removes_pin(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        decision = self.coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=9999.0
        )
        self.assertTrue(decision.allowed)
        self.coordinator.note_command(self.a.session_id, 42, "world_spawn", {})
        self.clock.advance(299.0)
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "active")
        self.coordinator.finish_operation(self.a.session_id, 42)
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "none")

        clock, coordinator = self._fresh()
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        coordinator.authorize(self.a, token, "world_spawn", operation_timeout_s=9999.0)
        coordinator.note_command(self.a.session_id, 43, "world_spawn", {})
        clock.advance(300.0)
        self.assertEqual(coordinator.status(self.a)["self"]["state"], "none")

    def test_vehicle_active_tracks_only_accepted_nonzero_control_and_release(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.coordinator.authorize(self.a, token, "vehicle_control")
        self.coordinator.note_command(
            self.a.session_id,
            1,
            "vehicle_control",
            {"throttle": 0.0, "steer": 0.0, "brake": 0.0},
        )
        self.coordinator.release(self.a, token)
        self.assertFalse(self.cleanup.calls[-1][2])

        clock, coordinator = self._fresh()
        cleanup = CleanupSink()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=cleanup,
        )
        acquire = coordinator.acquire(self.a, "drive")[1]
        token = acquire["lease_token"]
        lease_id = acquire["lease_id"]
        coordinator.authorize(self.a, token, "vehicle_control")
        coordinator.note_command(self.a.session_id, 2, "vehicle_control", {"throttle": 1.0})
        coordinator.authorize(self.a, token, "vehicle_release")
        coordinator.note_command(self.a.session_id, 3, "vehicle_release", {})
        coordinator.release(self.a, token)
        self.assertTrue(cleanup.calls[-1][2])

        clock, coordinator = self._fresh()
        cleanup = CleanupSink()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=cleanup,
        )
        acquire = coordinator.acquire(self.a, "drive")[1]
        token = acquire["lease_token"]
        lease_id = acquire["lease_id"]
        coordinator.authorize(self.a, token, "vehicle_control")
        coordinator.note_command(self.a.session_id, 2, "vehicle_control", {"throttle": 1.0})
        coordinator.authorize(self.a, token, "vehicle_release")
        coordinator.note_command(self.a.session_id, 3, "vehicle_release", {})
        coordinator.finish_operation_exact(
            self.a.session_id,
            lease_id,
            3,
            command_succeeded=True,
        )
        coordinator.release(self.a, token)
        # The legacy note_command route has no committed command metadata.
        # It therefore remains conservatively active until lease cleanup.
        self.assertTrue(cleanup.calls[-1][2])

        clock, coordinator = self._fresh()
        cleanup = CleanupSink()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=cleanup,
        )
        acquire = coordinator.acquire(self.a, "drive")[1]
        token = acquire["lease_token"]
        lease_id = acquire["lease_id"]
        coordinator.authorize(self.a, token, "vehicle_control")
        coordinator.note_command(self.a.session_id, 2, "vehicle_control", {"throttle": 1.0})
        coordinator.authorize(self.a, token, "vehicle_release")
        coordinator.note_command(self.a.session_id, 3, "vehicle_release", {})
        coordinator.discard_committed(
            self.a,
            lease_id,
            3,
            "vehicle_release",
            "stale_discarded",
        )
        coordinator.release(self.a, token)
        self.assertTrue(cleanup.calls[-1][2])

        clock, coordinator = self._fresh()
        cleanup = CleanupSink()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=cleanup,
        )
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        coordinator.authorize(self.a, token, "vehicle_control")
        coordinator.note_command(self.a.session_id, 4, "vehicle_control", {"brake": 0.25})
        coordinator.release(self.a, token)
        self.assertTrue(cleanup.calls[-1][2])

    def test_vehicle_trace_start_marks_vehicle_active_in_both_commit_paths(self) -> None:
        token = self.coordinator.acquire(self.a, "trace")[1]["lease_token"]
        self.coordinator.authorize(self.a, token, "vehicle_trace")
        self.coordinator.note_command(
            self.a.session_id,
            51,
            "vehicle_trace",
            {"mode": "start"},
        )
        self.coordinator.release(self.a, token)
        self.assertTrue(self.cleanup.calls[-1][2])

        clock, coordinator = self._fresh()
        cleanup = CleanupSink()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=cleanup,
        )
        token = coordinator.acquire(self.a, "trace")[1]["lease_token"]
        decision = coordinator.authorize(self.a, token, "vehicle_trace")
        self.assertTrue(
            coordinator.commit_authorization(
                self.a.session_id,
                decision.lease_id,
                decision.reservation_id,
                52,
                "vehicle_trace",
                {"mode": "start"},
            )
        )
        coordinator.release(self.a, token)
        self.assertTrue(cleanup.calls[-1][2])

    def test_unaccepted_vehicle_command_cannot_change_vehicle_active(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.coordinator.note_command(
            self.a.session_id, 99, "vehicle_control", {"throttle": 1.0}
        )
        self.coordinator.release(self.a, token)
        self.assertFalse(self.cleanup.calls[-1][2])

    def test_abort_authorization_removes_exactly_one_duplicate_and_its_pin(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.coordinator.authorize(self.a, token, "vehicle_control")
        self.coordinator.note_command(
            self.a.session_id, 1, "vehicle_control", {"throttle": 1.0}
        )
        self.coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=150.0
        )
        self.coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=250.0
        )
        revision_before_abort = self.coordinator.snapshot_payload()["revision"]

        degraded = self.coordinator.abort_authorization(
            self.a.session_id, "world_spawn", "enqueue_failed"
        )

        self.assertEqual(degraded, ())
        self.assertEqual(
            self.coordinator.snapshot_payload()["revision"],
            revision_before_abort + 1,
        )
        rejected = [
            event for event in self.audit.events if event["event"] == "session_rejected"
        ][-1]
        self.assertEqual(rejected["client"], self.a.public_payload())
        self.assertEqual(rejected["command"], "world_spawn")
        self.assertEqual(rejected["reason"], "enqueue_failed")
        self.assertEqual(rejected["duration_s"], 0.0)

        revision_before_accept = self.coordinator.snapshot_payload()["revision"]
        self.coordinator.note_command(self.a.session_id, 2, "world_spawn", {})
        self.assertEqual(
            self.coordinator.snapshot_payload()["revision"],
            revision_before_accept + 1,
        )
        self.coordinator.note_command(self.a.session_id, 3, "world_spawn", {})
        self.assertEqual(
            self.coordinator.snapshot_payload()["revision"],
            revision_before_accept + 1,
        )

        self.clock.advance(200.0)
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "active")
        self.coordinator.release(self.a, token)
        self.assertTrue(self.cleanup.calls[-1][2])

    def test_abort_unpinned_duplicate_preserves_surviving_pinned_reservation(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=0.0
        )
        self.coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=250.0
        )

        self.assertEqual(
            self.coordinator.abort_authorization(
                self.a.session_id, "world_spawn", "enqueue_failed"
            ),
            (),
        )
        self.coordinator.note_command(self.a.session_id, 1, "world_spawn", {})

        self.clock.advance(200.0)
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "active")

    def test_abort_pinned_duplicate_leaves_surviving_unpinned_reservation(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=250.0
        )
        self.coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=0.0
        )

        self.assertEqual(
            self.coordinator.abort_authorization(
                self.a.session_id, "world_spawn", "enqueue_failed"
            ),
            (),
        )
        self.coordinator.note_command(self.a.session_id, 1, "world_spawn", {})

        self.clock.advance(120.0)
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "none")

    def test_abort_authorization_removes_pending_state_from_releasing_owner(self) -> None:
        cleanup_entered = threading.Event()
        allow_cleanup = threading.Event()
        audit = AuditSink()

        def blocking_cleanup(
            session_id: str,
            _lease_id: str,
            reason: str,
            vehicle_active: bool,
        ) -> dict[str, object]:
            cleanup_entered.set()
            allow_cleanup.wait(2.0)
            return {"cancelled": 0, "vehicle_release": vehicle_active}

        coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
            cleanup=blocking_cleanup,
        )
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=60.0
        )
        release_result: list[tuple[int, dict]] = []
        release_thread = threading.Thread(
            target=lambda: release_result.append(coordinator.release(self.a, token))
        )
        release_thread.start()
        self.assertTrue(cleanup_entered.wait(1.0), "release did not enter cleanup")

        try:
            revision_before_abort = coordinator.snapshot_payload()["revision"]
            self.assertEqual(
                coordinator.abort_authorization(
                    self.a.session_id, "world_spawn", "dispatch_cancelled"
                ),
                (),
            )
            self.assertEqual(
                coordinator.snapshot_payload()["revision"],
                revision_before_abort + 1,
            )
            with coordinator._condition:
                self.assertIsNotNone(coordinator._releasing)
                self.assertEqual(coordinator._releasing.pending_authorizations, [])
                self.assertFalse(coordinator._releasing.vehicle_active)
        finally:
            allow_cleanup.set()
            release_thread.join(2.0)

        self.assertFalse(release_thread.is_alive())
        self.assertEqual(release_result[0][0], 200)

    def test_abort_authorization_unknown_owner_or_command_is_revision_noop(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.coordinator.authorize(self.a, token, "world_spawn")
        revision_before = self.coordinator.snapshot_payload()["revision"]

        self.assertEqual(
            self.coordinator.abort_authorization(
                "session-missing", "world_spawn", "enqueue_failed"
            ),
            (),
        )
        self.assertEqual(
            self.coordinator.abort_authorization(
                self.a.session_id, "vehicle_control", "enqueue_failed"
            ),
            (),
        )
        self.assertEqual(self.coordinator.snapshot_payload()["revision"], revision_before)

    def test_abort_authorization_rejects_empty_reason_without_mutation(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.coordinator.authorize(self.a, token, "world_spawn")
        revision_before = self.coordinator.snapshot_payload()["revision"]

        with self.assertRaisesRegex(ValueError, "^invalid_abort_reason$"):
            self.coordinator.abort_authorization(self.a.session_id, "world_spawn", "")

        self.assertEqual(self.coordinator.snapshot_payload()["revision"], revision_before)
        self.coordinator.note_command(self.a.session_id, 1, "world_spawn", {})
        self.assertEqual(
            self.coordinator.snapshot_payload()["revision"], revision_before + 1
        )

    def test_abort_authorization_audit_failure_still_cleans_pending_state(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.coordinator.authorize(
            self.a, token, "world_spawn", operation_timeout_s=60.0
        )
        revision_before_abort = self.coordinator.snapshot_payload()["revision"]
        self.audit.fail_events.add("session_rejected")

        self.assertEqual(
            self.coordinator.abort_authorization(
                self.a.session_id, "world_spawn", "enqueue_failed"
            ),
            ("audit_failed",),
        )
        self.assertEqual(
            self.coordinator.snapshot_payload()["revision"],
            revision_before_abort + 1,
        )
        with self.coordinator._condition:
            self.assertEqual(self.coordinator._active.pending_authorizations, [])

        self.coordinator.note_command(self.a.session_id, 1, "world_spawn", {})
        self.assertEqual(
            self.coordinator.snapshot_payload()["revision"],
            revision_before_abort + 1,
        )

    def test_non_owner_cannot_release_or_evict_owner(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        status, body = self.coordinator.release(self.b, token)
        self.assertEqual((status, body["error"]), (403, "lease_invalid"))
        self.assertEqual(self.cleanup.calls, [])
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "active")

    def test_acquire_heartbeat_and_mutation_audit_fail_closed_without_renewal(self) -> None:
        self.audit.fail_events.add("session_acquire")
        failed = self.coordinator.acquire(self.a, "drive")
        self.assertEqual((failed[0], failed[1]["error"]), (503, "audit_failed"))
        self.audit.fail_events.clear()
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "none")

        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.clock.advance(100.0)
        self.audit.fail_events.add("session_heartbeat")
        self.assertEqual(self.coordinator.heartbeat(self.a, token)[0], 503)
        self.audit.fail_events = {"session_authorized"}
        self.assertEqual(
            self.coordinator.authorize(self.a, token, "world_spawn").error,
            "audit_failed",
        )
        self.audit.fail_events.clear()
        self.clock.advance(20.0)
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "none")

    def test_release_and_expiry_invalidate_even_when_audit_fails(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        self.audit.fail_events.add("session_release_started")
        status, body = self.coordinator.release(self.a, token)
        self.assertEqual(status, 200)
        self.assertIn("audit_failed", body["cleanup_degraded"])
        self.assertEqual(self.coordinator.status(self.a)["self"]["state"], "none")

        clock = FakeClock()
        audit = AuditSink()
        cleanup = CleanupSink()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
            cleanup=cleanup,
        )
        coordinator.acquire(self.a, "drive")
        audit.fail_events.add("session_expired")
        clock.advance(120.0)
        state = coordinator.status(self.a)
        self.assertEqual(state["self"]["state"], "none")
        self.assertIn("audit_failed", state["cleanup_degraded"])
        self.assertEqual(cleanup.calls, [("session-a", "lease_expired", False)])

    def test_failed_grant_leaves_ticket_queued_and_owner_invalidated(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        ticket = self.coordinator.acquire(self.b, "queued")[1]["ticket"]
        self.audit.fail_events.add("session_granted")
        _, release_body = self.coordinator.release(self.a, token)
        self.assertEqual(release_body["cleanup_degraded"], [])
        status_code, body = self.coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((status_code, body["error"]), (503, "audit_failed"))
        status = self.coordinator.status(self.b)
        self.assertIsNone(status["owner"])
        self.assertEqual(status["self"]["state"], "queued")
        self.assertEqual(status["self"]["position"], 1)

    def test_cleanup_callback_runs_without_condition_lock_held(self) -> None:
        callback_observed_unlocked = threading.Event()
        coordinator: SessionCoordinator

        def cleanup(
            _session_id: str,
            _lease_id: str,
            _reason: str,
            _vehicle_active: bool,
        ) -> dict[str, object]:
            probe_done = threading.Event()

            def probe() -> None:
                coordinator.status(self.b)
                probe_done.set()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(timeout=1.0)
            if probe_done.is_set():
                callback_observed_unlocked.set()
            return {}

        coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            cleanup=cleanup,
        )
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        coordinator.release(self.a, token)
        self.assertTrue(callback_observed_unlocked.is_set())

    def test_status_never_contains_tokens_or_raw_pids(self) -> None:
        active = self.coordinator.acquire(self.a, "drive")[1]
        queued = self.coordinator.acquire(self.b, "queued")[1]
        payload = self.coordinator.status(self.b)
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn(active["lease_token"], serialized)
        self.assertNotIn(str(self.a.pid), serialized)
        self.assertNotIn(str(self.a.ppid), serialized)
        self.assertNotIn(str(self.b.pid), serialized)
        self.assertNotIn(str(self.b.ppid), serialized)
        self.assertIn(active["lease_id"], serialized)
        self.assertIn(queued["ticket"], serialized)


if __name__ == "__main__":
    unittest.main()
