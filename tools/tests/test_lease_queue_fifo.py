"""Lease queue, TTL, grace and FIFO grant order of the session coordinator.

Moved verbatim from test_session_coordination.py, test_bug046_lease_queue_liveness.py, test_task7_final_authority_regressions.py, test_0ab2_grace.py, test_0ab2_r9.py, test_session_acquire_wait.py
(review 2026-09-25, W4d step 3).
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import sys
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import session_coordination as session_coordination_mod
from dayz_mcp.runtime_state import CoordinationSnapshotStore, RuntimePaths
from dayz_mcp.session_coordination import (
    AuthorizationDecision,
    CleanupDisposition,
    ClientIdentity,
    command_requires_lease,
    LEASE_GRACE_S,
    MAX_OPERATION_PIN_S,
    MAX_OPERATION_TOMBSTONES,
    MAX_PREF_RENEWALS,
    MAX_SESSION_QUEUE,
    OPERATION_TOMBSTONE_TTL_S,
    READ_ONLY_COMMANDS,
    SESSION_TTL_S,
    SessionCoordinator,
    WAIT_MAX_S,
)
from tests.lease_helpers import (
    _identity,
    AuditSink,
    CleanupSink,
    FakeClock,
    parse_dpf_table,
    SequentialIds,
)
from tests.lifecycle_helpers import Clock, IDENTITY, IDENTITY_B, Sequence


# --- helpers from test_session_coordination.py ---
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
    "entities_query",
    "ui_tree",
}


# --- helpers from test_bug046_lease_queue_liveness.py ---
_REPO_ROOT = Path(__file__).resolve().parents[2]


_PRODUCT_SPEC = _REPO_ROOT / "product-spec.md"


_H4_HEADING = "### H — Coordinación segura de sesiones de agentes"


# Literals from product-spec H4 ("gracia post-TTL de 90 s", "inválido a 120 s").
# Do not alias LEASE_GRACE_S / SESSION_TTL_S here: a memory mutation of those
# names must still turn the runtime pins red (W3-P2-01).
_H4_SPEC_GRACE_S = 90.0


_H4_SPEC_TTL_S = 120.0


def extract_markdown_segment(markdown: str, start_marker: str, end_marker: str) -> str:
    """Extract one exact Markdown block delimited by structural markers."""

    start = markdown.find(start_marker)
    if start < 0:
        raise AssertionError(f"missing start marker: {start_marker}")
    end = markdown.find(end_marker, start)
    if end < 0:
        raise AssertionError(f"missing end marker: {end_marker}")
    return markdown[start:end].strip()


_H8_HISTORY = """**Excepción de ejecución aprobada 2026-07-16:** mientras no haya créditos Claude, el usuario
autoriza sustituir el reparto 2+2 de H8 por **4 sesiones Codex fresh**. Para acreditar el gate
funcional deben conservar cuatro `session_id` y PID distintos, una misma `daemon_generation`,
la secuencia completa y el cierre limpio. La evidencia se etiqueta `4-Codex`; esta excepción no
convierte en verificación in-game la mitad Claude de H1.

Estado de cierre 2026-07-16: H8 funcional pasó en una ejecución real de 4 sesiones Codex fresh,
con una generación compartida, FIFO/TTL/lifecycle completos y cierre limpio. El doctor final quedó
sin findings y no quedaron procesos DayZ ni UDP 2302. Evidencia:
`reviews/2026-07-16-h8-real-4-codex.json` (SHA256
`E49A4A224EE782C99C86C5D71F8DEE8EB502213B94683D619AF3BF2F26CD873A`). La configuración
efectiva mixta 2 Claude + 2 Codex de H1 sigue sin verificación in-game."""


def _h4_grace_coordinator(clock: FakeClock) -> SessionCoordinator:
    return SessionCoordinator(
        time_fn=clock,
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("id"),
        audit=AuditSink(),
        cleanup=CleanupSink(),
        attached_run_probe=lambda _session, _lease: True,
    )


# --- helpers from test_0ab2_grace.py ---
TTL = SESSION_TTL_S


G = LEASE_GRACE_S


MID = TTL + (G / 2.0)


AFTER = TTL + G + 0.001


def _coord(clock: FakeClock, *, attached: bool) -> SessionCoordinator:
    return SessionCoordinator(
        time_fn=clock,
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("id"),
        audit=AuditSink(),
        cleanup=CleanupSink(),
        attached_run_probe=lambda _session, _lease: attached,
    )


# --- helpers from test_0ab2_r9.py ---
def _coord_0ab2_r9(clock: FakeClock, *, attached: bool, cleanup=None) -> SessionCoordinator:
    return SessionCoordinator(
        time_fn=clock,
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("id"),
        audit=AuditSink(),
        cleanup=cleanup or CleanupSink(),
        attached_run_probe=lambda _session, _lease: attached,
    )


@dataclass
class _AttachedRun:
    owner_session_id: str
    owner_lease_id: str
    state: str = "RUNNING"


class _DaemonShapedBox:
    """Same predicate as daemon.attached_run_probe + cleanup → RUNNING_IDLE."""

    def __init__(self) -> None:
        self.runs: list[_AttachedRun] = []
        self.probe_saw_released = False
        self.cleanup_calls = 0

    def probe(self, session_id: str, lease_id: str) -> bool:
        if any(run.state != "RUNNING" for run in self.runs):
            self.probe_saw_released = True
        for run in self.runs:
            if (
                run.owner_session_id == session_id
                and run.owner_lease_id == lease_id
                and run.state == "RUNNING"
            ):
                return True
        return False

    def cleanup(
        self, session_id: str, lease_id: str, _reason: str, _vehicle_active: bool
    ) -> dict[str, object]:
        self.cleanup_calls += 1
        for run in self.runs:
            if run.owner_session_id == session_id and run.owner_lease_id == lease_id:
                run.state = "RUNNING_IDLE"
                run.owner_session_id = None  # type: ignore[assignment]
                run.owner_lease_id = None  # type: ignore[assignment]
        return {"cancelled": 0, "runs_released": [session_id]}


# --- from test_session_coordination.py ---


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
        self.assertEqual(LEASE_GRACE_S, 90.0)
        self.assertEqual(MAX_PREF_RENEWALS, 1)
        self.assertEqual(WAIT_MAX_S, 30.0)
        self.assertEqual(MAX_SESSION_QUEUE, 64)
        self.assertEqual(MAX_OPERATION_PIN_S, 300.0)
        self.assertEqual(READ_ONLY_COMMANDS, frozenset(READ_ONLY))

    def test_unknown_command_is_mutating_by_default(self) -> None:
        self.assertFalse(command_requires_lease("camera_get"))
        self.assertTrue(command_requires_lease("brand_new_tool"))

    def test_query_all_players_is_a_pure_read(self) -> None:
        # It only reads authoritative server state, so it must not gate on
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

    def test_saturated_release_audit_slots_return_not_started_and_degrade(self) -> None:
        token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
        slots = self.coordinator._release_audit_worker_slots
        held = 0
        while slots.acquire(blocking=False):
            held += 1
        self.assertGreater(held, 0)
        try:
            with self.coordinator._condition:
                lease = self.coordinator._active
                self.assertIsNotNone(lease)
                outcome = self.coordinator._write_release_audits_bounded_locked(
                    lease=lease,
                    started_event="session_release_started",
                    audit_reason="owner_release",
                    cleanup_timed_out=False,
                    cleanup_duration_s=0.0,
                    cleanup_summary={},
                    cleanup_degraded=[],
                    expired_tickets=[],
                    wait_s=0.0,
                )
            status, body = self.coordinator.release(self.a, token)
            degraded = body.get("cleanup_degraded") or []
            handoff_failed = self.coordinator._handoff_audit_failed
        finally:
            for _ in range(held):
                slots.release()
        self.assertEqual(
            (type(outcome).__name__, outcome, status, degraded, handoff_failed),
            ("str", "not_started", 200, ["audit_failed"], True),
        )

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


class BoxNoneReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 0.0
        self.seq = 0

        def next_id() -> str:
            self.seq += 1
            return f"ticket-{self.seq}"

        self.coordinator = SessionCoordinator(
            time_fn=lambda: self.now,
            id_fn=next_id,
            token_fn=lambda: "unused",
        )

    def test_2223_r3b_none_release_keeps_a_sibling_ticket_and_claim(self) -> None:
        owner = _identity("L")
        try:
            first = self.coordinator.box_wait_touch(owner)
            second = self.coordinator.box_wait_touch(owner)
            claimed = self.coordinator.box_wait_touch(
                owner, first["box_ticket"], claim=True
            )
        except Exception as exc:
            self.fail(f"product raised {type(exc).__name__}: {exc}")
        self.assertTrue(claimed.get("box_claimed"))
        self.assertEqual(first.get("box_ticket"), "ticket-1")
        self.assertEqual(second.get("box_ticket"), "ticket-2")
        self.assertEqual(len(self.coordinator.box_queue_public()), 2)
        try:
            self.coordinator.box_wait_touch(owner, done=True)
        except Exception as exc:
            self.fail(f"product raised {type(exc).__name__}: {exc}")
        queue = self.coordinator.box_queue_public()
        self.assertEqual(len(queue), 2)
        self.assertTrue(self.coordinator.box_is_claimed())
        try:
            kept_first = self.coordinator.box_wait_touch(owner, "ticket-1")
            kept_second = self.coordinator.box_wait_touch(owner, "ticket-2")
        except Exception as exc:
            self.fail(f"product raised {type(exc).__name__}: {exc}")
        self.assertEqual(kept_first.get("box_ticket"), "ticket-1")
        self.assertEqual(kept_second.get("box_ticket"), "ticket-2")
        self.assertTrue(kept_first.get("box_claimed"))

    def test_none_release_leaves_session_when_it_holds_no_other_ticket(self) -> None:
        owner = _identity("L")
        try:
            joined = self.coordinator.box_wait_touch(owner)
            self.coordinator.box_wait_touch(owner, done=True)
        except Exception as exc:
            self.fail(f"product raised {type(exc).__name__}: {exc}")
        self.assertTrue(joined.get("box_ticket"))
        self.assertEqual(self.coordinator.box_queue_public(), [])
        self.assertFalse(self.coordinator.box_is_claimed())

    def test_2223_r4_box_claimed_is_true_only_for_the_claiming_ticket(self) -> None:
        owner = _identity("L")
        try:
            first = self.coordinator.box_wait_touch(owner)
            second = self.coordinator.box_wait_touch(owner)
            claimed = self.coordinator.box_wait_touch(
                owner, first["box_ticket"], claim=True
            )
            kept_second = self.coordinator.box_wait_touch(
                owner, second["box_ticket"]
            )
        except Exception as exc:
            self.fail(f"product raised {type(exc).__name__}: {exc}")
        self.assertTrue(claimed.get("box_claimed"))
        self.assertFalse(kept_second.get("box_claimed"))


# --- from test_bug046_lease_queue_liveness.py ---


class Bug046DpfContractTests(unittest.TestCase):
    def test_h4_h9_contract_and_h8_history_are_structurally_preserved(self) -> None:
        markdown = _PRODUCT_SPEC.read_text(encoding="utf-8")
        rows = parse_dpf_table(markdown, _H4_HEADING)

        self.assertEqual(
            rows["H4"],
            {
                "criterion": "Cola FIFO estricta salvo ventana de gracia post-TTL de 90 s en la que solo la identidad titular a t=TTL puede acquire/wait-claim si a t=TTL tenía un RUNNING propio (snapshot antes de release_owner); el token sigue inválido a 120 s; extraños solo promocionan con session_wait vivo después de la gracia; como máximo 1 reacquire preferente consecutivo cuando la cola no está vacía; `acquire` idempotente",
                "verification": "A→B→C, ticket duplicado no duplica posición, reloj inyectable 119/120/121 s; release/expiry sin `session_wait` vivo no conceden leases; gracia 90 s con probe de RUNNING; B no 200 durante la ventana",
                "state": "✓ offline",
            },
        )
        self.assertEqual(
            rows["H9"],
            {
                "criterion": "Adquisición en espera request-bound y liveness de cola",
                "verification": "`session_acquire_wait` request-bound nunca devuelve `queued`; timeout/cancel no deja ticket/lease oculto; sólo `session_wait` vivo promueve cabeza; release/expiry no hacen grant ciego; launcher nativo/neutral respecto al consumidor, registrado (path+SHA, sin identidad/token), no autoriza PowerShell ni `.ps1`; gate local `fifo_grants_without_live_wait=0`; gate real 2 Claude+2 Codex abierto hasta proveniencia externa",
                "state": "[verify] offline ✓; falta gate real 2 Claude + 2 Codex",
            },
        )
        self.assertEqual(
            rows["H8"],
            {
                "criterion": "Gate combinado real sobre una caja y un juego",
                "verification": "2 Claude + 2 Codex: lecturas paralelas, mutaciones FIFO, release, expiry por caída, adopción/reemplazo seguro y estado final limpio",
                "state": "✓ in-game — sustitución 4-Codex aprobada",
            },
        )
        self.assertEqual(
            extract_markdown_segment(
                markdown,
                "**Excepción de ejecución aprobada 2026-07-16:**",
                "Aceptación detallada:",
            ),
            _H8_HISTORY,
        )

    def test_parse_dpf_table_rejects_duplicate_ids(self) -> None:
        duplicate_table = "\n".join(
            (
                "### Test",
                "| # | Criterio | Cómo se verifica | Estado |",
                "|---|---|---|---|",
                "| H4 | first | check | ❌ |",
                "| H4 | second | check | ❌ |",
            )
        )
        with self.assertRaisesRegex(AssertionError, "^duplicate DPF criterion: H4$"):
            parse_dpf_table(duplicate_table, "### Test")

    def test_h4_spec_grace_and_ttl_bind_runtime_constants(self) -> None:
        """Prose equality on rows['H4'] is not the runtime gate (W3-P2-01)."""

        markdown = _PRODUCT_SPEC.read_text(encoding="utf-8")
        rows = parse_dpf_table(markdown, _H4_HEADING)
        criterion = rows["H4"]["criterion"]
        verification = rows["H4"]["verification"]
        grace_match = re.search(r"gracia post-TTL de (\d+) s", criterion)
        ttl_match = re.search(r"inválido a (\d+) s", criterion)
        self.assertIsNotNone(grace_match)
        self.assertIsNotNone(ttl_match)
        spec_grace = float(grace_match.group(1))
        spec_ttl = float(ttl_match.group(1))
        self.assertEqual(spec_grace, _H4_SPEC_GRACE_S)
        self.assertEqual(spec_ttl, _H4_SPEC_TTL_S)
        self.assertEqual(LEASE_GRACE_S, spec_grace)
        self.assertEqual(SESSION_TTL_S, spec_ttl)
        self.assertIn("90 s", verification)
        self.assertEqual(session_coordination_mod.LEASE_GRACE_S, _H4_SPEC_GRACE_S)
        self.assertEqual(session_coordination_mod.SESSION_TTL_S, _H4_SPEC_TTL_S)


class Bug046H4GraceRuntimeTests(unittest.TestCase):
    """Coordinator window follows LEASE_GRACE_S, not the H4 markdown pin."""

    def test_stranger_stays_queued_inside_spec_grace_window(self) -> None:
        clock = FakeClock()
        coordinator = _h4_grace_coordinator(clock)
        holder, stranger = _identity("a"), _identity("b")
        coordinator.acquire(holder, "drive")
        clock.advance(_H4_SPEC_TTL_S + _H4_SPEC_GRACE_S - 0.001)
        status, payload = coordinator.acquire(stranger, "drive")
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "queued")

    def test_stranger_grants_after_spec_grace_window(self) -> None:
        clock = FakeClock()
        coordinator = _h4_grace_coordinator(clock)
        holder, stranger = _identity("a"), _identity("b")
        coordinator.acquire(holder, "drive")
        clock.advance(_H4_SPEC_TTL_S + _H4_SPEC_GRACE_S + 0.001)
        status, payload = coordinator.acquire(stranger, "drive")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")

    def test_mutating_lease_grace_s_keeps_stranger_queued_at_spec_90(self) -> None:
        original = session_coordination_mod.LEASE_GRACE_S
        clock = FakeClock()
        holder, stranger = _identity("a"), _identity("b")
        try:
            session_coordination_mod.LEASE_GRACE_S = original + 1.0
            coordinator = _h4_grace_coordinator(clock)
            coordinator.acquire(holder, "drive")
            clock.advance(_H4_SPEC_TTL_S + _H4_SPEC_GRACE_S + 0.001)
            status, payload = coordinator.acquire(stranger, "drive")
            self.assertEqual(status, 202)
            self.assertEqual(payload["status"], "queued")
        finally:
            session_coordination_mod.LEASE_GRACE_S = original


class Bug046QueueLivenessRedTests(unittest.TestCase):
    def test_v1_release_without_live_wait_keeps_fifo_head_queued_and_grants_nothing(self) -> None:
        clock = FakeClock()
        audit = AuditSink()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b, c = _identity("a"), _identity("b"), _identity("c")

        active = coordinator.acquire(a, "drive")
        b_ticket = coordinator.acquire(b, "camera")
        c_ticket = coordinator.acquire(c, "weather")
        self.assertEqual(
            (
                active[0],
                active[1]["status"],
                b_ticket[0],
                b_ticket[1]["status"],
                b_ticket[1]["position"],
                c_ticket[0],
                c_ticket[1]["status"],
                c_ticket[1]["position"],
            ),
            (200, "active", 202, "queued", 1, 202, "queued", 2),
        )
        grants_before_release = len(
            [event for event in audit.events if event["event"] == "session_granted"]
        )

        self.assertEqual(coordinator.release(a, active[1]["lease_token"])[0], 200)
        snapshot = coordinator.snapshot_payload()
        observed = {
            "active": snapshot["active"],
            "b_position": coordinator.status(b)["self"]["position"],
            "new_grants": len(
                [event for event in audit.events if event["event"] == "session_granted"]
            )
            - grants_before_release,
        }
        self.assertEqual(
            observed,
            {"active": None, "b_position": 1, "new_grants": 0},
            "V1 requires release to leave the FIFO head queued until a live session_wait promotes it",
        )
        self.assertEqual(b_ticket[1]["position"], 1)

    def test_v2_live_head_wait_claims_once_and_leaves_next_ticket_at_position_one(self) -> None:
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b, c = _identity("a"), _identity("b"), _identity("c")
        active = coordinator.acquire(a, "drive")[1]
        b_ticket = coordinator.acquire(b, "camera")[1]
        coordinator.acquire(c, "weather")

        coordinator.release(a, active["lease_token"])
        claimed = coordinator.wait(b, b_ticket["ticket"], 0.0)
        repeated = coordinator.wait(b, b_ticket["ticket"], 0.0)

        fifo_prepared = [
            event
            for event in events
            if event.get("event") == "session_grant_prepared"
            and event.get("reason") == "fifo_head"
        ]
        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        self.assertEqual((claimed[0], claimed[1].get("status")), (200, "active"))
        self.assertEqual(repeated[1]["lease_token"], claimed[1]["lease_token"])
        self.assertEqual(len(fifo_prepared), 1)
        self.assertEqual(len(fifo_commits), 1)
        self.assertLess(events.index(fifo_prepared[0]), events.index(fifo_commits[0]))
        self.assertEqual(coordinator.status(c)["self"]["position"], 1)
        self.assertNotIn(b_ticket["ticket"], [item["ticket"] for item in coordinator.status(c)["queue"]])

    def test_v3_expired_head_never_becomes_lease_and_next_client_claims_only_from_live_wait(self) -> None:
        clock = FakeClock()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b, c = _identity("a"), _identity("b"), _identity("c")
        active = coordinator.acquire(a, "drive")[1]
        b_ticket = coordinator.acquire(b, "camera")[1]
        c_ticket = coordinator.acquire(c, "weather")[1]
        coordinator.release(a, active["lease_token"])

        clock.advance(120.0)
        claimed = coordinator.wait(c, c_ticket["ticket"], 0.0)
        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        cancelled = [
            event
            for event in events
            if event.get("event") == "ticket_cancelled"
        ]

        self.assertEqual((claimed[0], claimed[1].get("status")), (200, "active"))
        self.assertEqual(len(fifo_commits), 1)
        self.assertEqual(fifo_commits[0]["ticket"], c_ticket["ticket"])
        self.assertEqual([event["ticket"] for event in cancelled], [b_ticket["ticket"]])
        self.assertFalse(
            any(event.get("ticket") == b_ticket["ticket"] for event in fifo_commits)
        )

    def test_v4_two_concurrent_waits_publish_at_most_one_fifo_lease_and_commit(self) -> None:
        prepared_entered = threading.Event()
        allow_prepared = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_grant_prepared"
                and event.get("reason") == "fifo_head"
            ):
                prepared_entered.set()
                allow_prepared.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])
        results: list[tuple[int, dict]] = []
        first = threading.Thread(
            target=lambda: results.append(coordinator.wait(b, ticket["ticket"], 0.0))
        )
        second = threading.Thread(
            target=lambda: results.append(coordinator.wait(b, ticket["ticket"], 0.0))
        )

        first.start()
        self.assertTrue(prepared_entered.wait(1.0))
        second.start()
        second.join(1.0)
        self.assertFalse(second.is_alive())
        allow_prepared.set()
        first.join(2.0)
        self.assertFalse(first.is_alive())

        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        active_results = [body for status, body in results if status == 200]
        self.assertEqual(sorted(status for status, _body in results), [200, 202])
        self.assertEqual(len(active_results), 1)
        self.assertEqual(len({body["lease_token"] for body in active_results}), 1)
        self.assertEqual(len(fifo_commits), 1)
        self.assertEqual(
            coordinator.snapshot_payload()["active"]["lease_id"],
            fifo_commits[0]["lease_id"],
        )

    def test_v5_prepared_audit_false_or_exception_keeps_exact_head_without_token(self) -> None:
        for failure_mode in ("false", "exception"):
            with self.subTest(failure_mode=failure_mode):
                events: list[dict[str, object]] = []

                def audit(event: dict[str, object]) -> bool:
                    events.append(dict(event))
                    if (
                        event.get("event") == "session_grant_prepared"
                        and event.get("reason") == "fifo_head"
                    ):
                        if failure_mode == "exception":
                            raise OSError("prepared unavailable")
                        return False
                    return True

                coordinator = SessionCoordinator(
                    token_fn=SequentialIds("secret-token"),
                    id_fn=SequentialIds("id"),
                    audit=audit,
                )
                a, b = _identity("a"), _identity("b")
                active = coordinator.acquire(a, "drive")[1]
                ticket = coordinator.acquire(b, "camera")[1]
                coordinator.release(a, active["lease_token"])

                result = coordinator.wait(b, ticket["ticket"], 0.0)
                snapshot = coordinator.snapshot_payload()
                fifo_commits = [
                    event
                    for event in events
                    if event.get("event") == "session_granted"
                    and event.get("reason") == "fifo_head"
                ]
                self.assertEqual((result[0], result[1].get("error")), (503, "audit_failed"))
                self.assertIsNone(snapshot["active"])
                self.assertIsNone(snapshot["granting"])
                self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])
                self.assertEqual(fifo_commits, [])
                self.assertNotIn("secret-token", repr((events, result, snapshot)))

    def test_v5_busy_audit_gate_returns_queued_progress_without_audit_failure(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=lambda _event: True,
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])

        self.assertTrue(coordinator._audit_gate.acquire(blocking=False))
        busy_results: list[tuple[int, dict]] = []
        busy_thread = threading.Thread(
            target=lambda: busy_results.append(
                coordinator.wait(b, ticket["ticket"], 0.0)
            )
        )
        try:
            busy_thread.start()
            busy_thread.join(0.20)
            bounded = not busy_thread.is_alive()
        finally:
            coordinator._audit_gate.release()
            busy_thread.join(1.0)
        self.assertTrue(bounded, "claim_wait_blocked_behind_busy_audit_gate")
        busy = busy_results[0]
        claimed = coordinator.wait(b, ticket["ticket"], 0.0)

        self.assertEqual((busy[0], busy[1]["status"]), (202, "queued"))
        self.assertNotIn("cleanup_degraded", busy[1])
        self.assertNotIn("lease_token", busy[1])
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))

    def test_v5b_commit_failure_or_uncertain_append_is_compensated_without_hidden_active(self) -> None:
        for failure_mode, expected_commits in (("false", 0), ("append_then_raise", 1)):
            with self.subTest(failure_mode=failure_mode):
                events: list[dict[str, object]] = []

                def audit(event: dict[str, object]) -> bool:
                    is_commit = (
                        event.get("event") == "session_granted"
                        and event.get("reason") == "fifo_head"
                    )
                    if is_commit and failure_mode == "false":
                        return False
                    events.append(dict(event))
                    if is_commit and failure_mode == "append_then_raise":
                        raise OSError("append outcome uncertain")
                    return True

                coordinator = SessionCoordinator(
                    token_fn=SequentialIds("secret-token"),
                    id_fn=SequentialIds("id"),
                    audit=audit,
                )
                a, b = _identity("a"), _identity("b")
                active = coordinator.acquire(a, "drive")[1]
                ticket = coordinator.acquire(b, "camera")[1]
                coordinator.release(a, active["lease_token"])

                result = coordinator.wait(b, ticket["ticket"], 0.0)
                snapshot = coordinator.snapshot_payload()
                commits = [
                    event
                    for event in events
                    if event.get("event") == "session_granted"
                    and event.get("reason") == "fifo_head"
                ]
                revoked = [
                    event
                    for event in events
                    if event.get("event") == "session_grant_revoked"
                ]
                self.assertEqual((result[0], result[1].get("error")), (503, "audit_failed"))
                self.assertEqual(len(commits), expected_commits)
                self.assertEqual(len(revoked), 1)
                self.assertIsNone(snapshot["active"])
                self.assertIsNone(snapshot["granting"])
                self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])
                self.assertNotIn("secret-token", repr((events, result, snapshot)))

    def test_v5e_nonconfirmable_fifo_compensation_latches_after_last_ticket_expires(self) -> None:
        clock = FakeClock()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                raise OSError("commit append outcome uncertain")
            if event.get("event") == "session_grant_revoked":
                raise OSError("revocation append outcome uncertain")
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("secret-token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b, c = _identity("a"), _identity("b"), _identity("c")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])

        failed_claim = coordinator.wait(b, ticket["ticket"], 0.0)
        clock.advance(120.0)
        expired_status = coordinator.status(b)
        retried_acquire = coordinator.acquire(c, "inspect")
        snapshot = coordinator.snapshot_payload()

        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        revocations = [
            event
            for event in events
            if event.get("event") == "session_grant_revoked"
        ]
        self.assertEqual(
            (failed_claim[0], failed_claim[1].get("error")),
            (503, "audit_failed"),
        )
        self.assertEqual(len(fifo_commits), 1)
        self.assertEqual(len(revocations), 1)
        self.assertEqual(expired_status["queue"], [])
        self.assertEqual(
            (retried_acquire[0], retried_acquire[1].get("error")),
            (503, "audit_failed"),
        )
        self.assertFalse(snapshot["claimable"])
        self.assertIsNone(snapshot["active"])
        self.assertIsNone(snapshot["granting"])
        self.assertNotIn("secret-token", repr((failed_claim, retried_acquire, snapshot)))

    def test_v5c_coordination_change_after_prepared_never_writes_authoritative_commit(self) -> None:
        events: list[dict[str, object]] = []
        coordinator: SessionCoordinator

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if event.get("event") == "session_grant_prepared":
                with coordinator._condition:
                    coordinator._handoff_pending = True
                    coordinator._bump_revision_locked()
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"), id_fn=SequentialIds("id"), audit=audit
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])

        result = coordinator.wait(b, ticket["ticket"], 0.0)
        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        self.assertEqual((result[0], result[1].get("error")), (409, "coordination_changed"))
        self.assertEqual(fifo_commits, [])
        self.assertIsNone(coordinator.snapshot_payload()["active"])
        self.assertEqual(coordinator.snapshot_payload()["queue"][0]["ticket"], ticket["ticket"])

    def test_v5c_coordination_change_after_commit_writes_one_revocation_and_publishes_nothing(self) -> None:
        events: list[dict[str, object]] = []
        coordinator: SessionCoordinator

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                with coordinator._condition:
                    coordinator._handoff_pending = True
                    coordinator._bump_revision_locked()
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"), id_fn=SequentialIds("id"), audit=audit
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])

        result = coordinator.wait(b, ticket["ticket"], 0.0)
        commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        revoked = [event for event in events if event.get("event") == "session_grant_revoked"]
        snapshot = coordinator.snapshot_payload()
        self.assertEqual((result[0], result[1].get("error")), (409, "coordination_changed"))
        self.assertEqual(len(commits), 1)
        self.assertEqual(len(revoked), 1)
        self.assertEqual(commits[0]["lease_id"], revoked[0]["lease_id"])
        self.assertIsNone(snapshot["active"])
        self.assertIsNone(snapshot["granting"])
        self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])

    def test_v20_stalled_release_audit_keeps_fence_until_late_success_then_live_wait_claims(self) -> None:
        release_started = threading.Event()
        allow_release_audit = threading.Event()
        release_returned = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if event.get("event") == "session_release_started":
                release_started.set()
                allow_release_audit.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
            cleanup=lambda *_args: {},
            cleanup_timeout_s=0.02,
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        release_result: list[tuple[int, dict]] = []
        release_thread = threading.Thread(
            target=lambda: (
                release_result.append(coordinator.release(a, active["lease_token"])),
                release_returned.set(),
            )
        )
        release_thread.start()
        self.assertTrue(release_started.wait(1.0))
        self.assertTrue(release_returned.wait(1.0))

        during = coordinator.wait(b, ticket["ticket"], 0.0)
        during_snapshot = coordinator.snapshot_payload()
        self.assertEqual((during[0], during[1].get("status")), (202, "queued"))
        self.assertTrue(during_snapshot["handoff_pending"])
        self.assertIsNone(during_snapshot["active"])
        self.assertFalse(
            any(event.get("event") == "session_grant_prepared" for event in events)
        )

        allow_release_audit.set()
        with coordinator._condition:
            terminalized = coordinator._condition.wait_for(
                lambda: not coordinator._handoff_pending, timeout=1.0
            )
        release_thread.join(1.0)
        self.assertTrue(terminalized)
        claimed = coordinator.wait(b, ticket["ticket"], 0.0)
        names = [str(event.get("event")) for event in events]
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))
        self.assertLess(names.index("session_release_finished"), names.index("session_grant_prepared"))

    def test_v20_failed_release_terminal_audit_remains_visible_and_blocks_live_wait(self) -> None:
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return event.get("event") != "session_release_finished"

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]

        release = coordinator.release(a, active["lease_token"])
        blocked = coordinator.wait(b, ticket["ticket"], 0.0)
        snapshot = coordinator.snapshot_payload()

        self.assertIn("audit_failed", release[1]["cleanup_degraded"])
        self.assertEqual((blocked[0], blocked[1].get("error")), (503, "audit_failed"))
        self.assertTrue(snapshot["handoff_pending"])
        self.assertIsNone(snapshot["active"])
        self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])
        self.assertFalse(
            any(
                event.get("event") in {"session_grant_prepared", "session_granted"}
                and event.get("reason") == "fifo_head"
                for event in events
            )
        )


# --- from test_task7_final_authority_regressions.py ---


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


# --- from test_0ab2_grace.py ---
# fb-20260909-213002-0ab2: lease grace S1 + takeover_required.
#
# Fixtures from docs/plan-0ab2.md (EXACT).


class GraceS1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")
        self.assertEqual(LEASE_GRACE_S, 90.0)
        self.assertEqual(MAX_PREF_RENEWALS, 1)

    def test_p1_former_reacquires_at_ttl_plus_half_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        old = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(MID)
        status, payload = coordinator.acquire(self.a, "drive")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")
        self.assertNotEqual(payload["lease_token"], old)
        self.assertIsNone(coordinator.status(self.a)["grace"])

    def test_p1_neg_old_token_stays_expired(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        old = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(MID)
        coordinator.acquire(self.a, "drive")
        decision = coordinator.authorize(self.a, old, "world_spawn")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error, "lease_expired")

    def test_p1w_enqueue_wait_promotes_former(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        queued = coordinator.acquire(self.b, "next")
        self.assertEqual(queued[0], 202)
        clock.advance(119.0)
        self.assertEqual(
            coordinator.wait(self.b, queued[1]["ticket"], 0.0)[0],
            202,
        )
        clock.advance(MID - 119.0)
        enq = coordinator.enqueue(self.a, "back", "operation-a")
        self.assertEqual(enq[0], 202)
        claimed = coordinator.wait(self.a, enq[1]["ticket"], 0.0)
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))

    def test_p2_n1_stranger_is_queued_during_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "queued")
        self.assertNotEqual(payload.get("status"), "active")

    def test_p3_n4_one_pref_renewal_with_queue(self) -> None:
        self.assertEqual(MAX_PREF_RENEWALS, 1)
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        ticket = coordinator.acquire(self.b, "next")[1]["ticket"]
        clock.advance(119.0)
        self.assertEqual(coordinator.wait(self.b, ticket, 0.0)[0], 202)
        clock.advance(MID - 119.0)
        blocked = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((blocked[0], blocked[1]["status"]), (202, "queued"))
        first = coordinator.acquire(self.a, "drive")
        self.assertEqual(first[0], 200)
        queued = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
        clock.advance(TTL - 1.0)
        queued = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
        clock.advance(1.0)
        second = coordinator.acquire(self.a, "again")
        self.assertNotEqual(second[0], 200)
        claimed = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))

    def test_p4_no_attached_run_no_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=False)
        coordinator.acquire(self.a, "drive")
        clock.advance(TTL + 0.001)
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")
        self.assertIsNone(coordinator.status(self.a)["grace"])

    def test_n2_authorize_at_ttl_is_lease_expired(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(TTL)
        decision = coordinator.authorize(self.a, token, "world_spawn")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error, "lease_expired")
        beat = coordinator.heartbeat(self.a, token)
        self.assertNotEqual(beat[0], 200)

    def test_n3_stranger_wins_after_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(AFTER)
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")

    def test_grace_status_shape_during_window(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        grace = coordinator.status(self.a)["grace"]
        self.assertIsInstance(grace, dict)
        self.assertEqual(grace["attached_required"], True)
        self.assertEqual(grace["pref_remaining"], 1)
        self.assertAlmostEqual(grace["remaining_s"], G / 2.0, places=3)

    def test_stranger_wait_does_not_claim_during_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        ticket = coordinator.acquire(self.b, "next")[1]["ticket"]
        clock.advance(119.0)
        self.assertEqual(coordinator.wait(self.b, ticket, 0.0)[0], 202)
        clock.advance(MID - 119.0)
        waited = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual(waited[0], 202)
        self.assertEqual(waited[1]["status"], "queued")

    def test_default_probe_keeps_h4_stranger_grant(self) -> None:
        clock = FakeClock()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=CleanupSink(),
        )
        coordinator.acquire(self.a, "drive")
        clock.advance(TTL + 0.001)
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual((status, payload["status"]), (200, "active"))
        self.assertIsNone(coordinator.status(self.a)["grace"])


# --- from test_0ab2_r9.py ---
# W7a DZ-R9 offline for 0ab2 (state-machine, race, identity, data-loss).
#
# In-game H8 is W7b / I3 — not this module.


class StateMachineR9Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")
        self.c = _identity("c")

    def test_grace_is_null_while_token_is_live(self) -> None:
        clock = FakeClock()
        coordinator = _coord_0ab2_r9(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(TTL - 0.001)
        self.assertIsNone(coordinator.status(self.a)["grace"])
        self.assertEqual(coordinator.acquire(self.a, "drive")[0], 200)

    def test_grace_arms_at_ttl_and_is_consumed_once(self) -> None:
        clock = FakeClock()
        coordinator = _coord_0ab2_r9(clock, attached=True)
        old = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(TTL)
        self.assertIsNotNone(coordinator.status(self.a)["grace"])
        first = coordinator.acquire(self.a, "drive")
        self.assertEqual(first[0], 200)
        self.assertNotEqual(first[1]["lease_token"], old)
        self.assertIsNone(coordinator.status(self.a)["grace"])
        clock.advance(1.0)
        second = coordinator.acquire(self.a, "drive")
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["lease_token"], first[1]["lease_token"])

    def test_voluntary_release_does_not_arm_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord_0ab2_r9(clock, attached=True)
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        released = coordinator.release(self.a, token)
        self.assertEqual(released[0], 200)
        self.assertIsNone(coordinator.status(self.a)["grace"])
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual((status, payload["status"]), (200, "active"))

    def test_third_identity_is_queued_during_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord_0ab2_r9(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        self.assertEqual(coordinator.acquire(self.b, "drive")[0], 202)
        self.assertEqual(coordinator.acquire(self.c, "drive")[0], 202)
        self.assertEqual(coordinator.acquire(self.a, "drive")[0], 200)

    def test_grace_expires_exactly_at_until(self) -> None:
        clock = FakeClock()
        coordinator = _coord_0ab2_r9(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(TTL + G)
        self.assertIsNone(coordinator.status(self.a)["grace"])
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual((status, payload["status"]), (200, "active"))

    def test_release_active_probes_before_cleanup_in_source(self) -> None:
        source = inspect.getsource(SessionCoordinator._release_active_locked)
        probe_at = source.find("_probe_attached_run_locked")
        cleanup_at = source.find("self._cleanup(")
        self.assertGreater(probe_at, 0)
        self.assertGreater(cleanup_at, probe_at)


class RaceR9Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")

    def test_probe_sees_running_before_cleanup_mutates_idle(self) -> None:
        clock = FakeClock()
        box = _DaemonShapedBox()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=box.cleanup,
            attached_run_probe=box.probe,
        )
        payload = coordinator.acquire(self.a, "drive")[1]
        box.runs.append(
            _AttachedRun(
                owner_session_id=self.a.session_id,
                owner_lease_id=payload["lease_id"],
            )
        )
        clock.advance(MID)
        status, granted = coordinator.acquire(self.a, "drive")
        self.assertEqual((status, granted["status"]), (200, "active"))
        self.assertGreaterEqual(box.cleanup_calls, 1)
        self.assertFalse(box.probe_saw_released)
        self.assertEqual(box.runs[0].state, "RUNNING_IDLE")
        self.assertIsNone(box.runs[0].owner_session_id)

    def test_probe_negative_after_idle_transition(self) -> None:
        box = _DaemonShapedBox()
        box.runs.append(_AttachedRun("session-a", "lease-1"))
        self.assertTrue(box.probe("session-a", "lease-1"))
        box.cleanup("session-a", "lease-1", "lease_expired", False)
        self.assertFalse(box.probe("session-a", "lease-1"))

    def test_concurrent_former_and_stranger_acquire_one_active(self) -> None:
        for _ in range(8):
            clock = FakeClock()
            cleanup_entered = threading.Event()
            allow_cleanup = threading.Event()
            former_invoked = threading.Event()

            def blocking_cleanup(
                _session_id: str,
                _lease_id: str,
                _reason: str,
                vehicle_active: bool,
            ) -> dict[str, object]:
                cleanup_entered.set()
                allow_cleanup.wait(5.0)
                return {"cancelled": 0, "vehicle_release": vehicle_active}

            coordinator = _coord_0ab2_r9(clock, attached=True, cleanup=blocking_cleanup)
            old = coordinator.acquire(self.a, "drive")[1]["lease_token"]
            clock.advance(MID)
            barrier = threading.Barrier(2)
            results: dict[str, tuple[int, dict]] = {}
            errors: dict[str, BaseException] = {}

            def worker_former() -> None:
                try:
                    barrier.wait(timeout=2.0)
                    self.assertTrue(
                        cleanup_entered.wait(2.0),
                        "expiry cleanup did not publish _releasing",
                    )
                    former_invoked.set()
                    results["a"] = coordinator.acquire(self.a, "drive")
                except BaseException as exc:  # noqa: BLE001 — capture for the parent
                    errors["a"] = exc

            def worker_stranger() -> None:
                try:
                    barrier.wait(timeout=2.0)
                    results["b"] = coordinator.acquire(self.b, "drive")
                except BaseException as exc:  # noqa: BLE001 — capture for the parent
                    errors["b"] = exc

            threads = [
                threading.Thread(target=worker_former),
                threading.Thread(target=worker_stranger),
            ]
            try:
                for thread in threads:
                    thread.start()
                self.assertTrue(
                    cleanup_entered.wait(2.0),
                    "expiry cleanup did not start",
                )
                self.assertTrue(
                    former_invoked.wait(2.0),
                    "former acquire did not start during cleanup",
                )
                time.sleep(0.05)
                allow_cleanup.set()
            finally:
                allow_cleanup.set()
                for thread in threads:
                    thread.join(timeout=5.0)
            self.assertEqual(errors, {})
            for thread in threads:
                self.assertFalse(thread.is_alive())
            self.assertEqual(results["a"][0], 200)
            self.assertEqual(results["a"][1]["status"], "active")
            self.assertNotEqual(results["a"][1]["lease_token"], old)
            self.assertNotEqual(results["a"][1].get("error"), "session_releasing")
            self.assertEqual(results["b"][0], 202)
            self.assertEqual(results["b"][1]["status"], "queued")
            statuses = {results["a"][1]["status"], results["b"][1]["status"]}
            self.assertEqual(statuses, {"active", "queued"})

    def test_stranger_wait_loses_to_former_wait_claim(self) -> None:
        clock = FakeClock()
        coordinator = _coord_0ab2_r9(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        ticket_b = coordinator.acquire(self.b, "next")[1]["ticket"]
        clock.advance(119.0)
        self.assertEqual(coordinator.wait(self.b, ticket_b, 0.0)[0], 202)
        clock.advance(MID - 119.0)
        enq = coordinator.enqueue(self.a, "back", "operation-a")
        barrier = threading.Barrier(2)
        results: dict[str, tuple[int, dict]] = {}

        def wait_a() -> None:
            barrier.wait(timeout=2.0)
            results["a"] = coordinator.wait(self.a, enq[1]["ticket"], 0.0)

        def wait_b() -> None:
            barrier.wait(timeout=2.0)
            results["b"] = coordinator.wait(self.b, ticket_b, 0.0)

        threads = [
            threading.Thread(target=wait_a),
            threading.Thread(target=wait_b),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)
        self.assertEqual(results["a"][0], 200)
        self.assertEqual(results["b"][0], 202)


# --- from test_session_acquire_wait.py ---


class OperationQueueCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=self.audit,
        )

    def test_enqueue_always_returns_ticket_even_when_authority_is_free(self) -> None:
        status, queued = self.coordinator.enqueue(
            _identity("a"), "build", "operation-a"
        )
        before = self.coordinator.snapshot_payload()
        claimed = self.coordinator.wait(_identity("a"), queued["ticket"], 0.0)

        self.assertEqual((status, queued["status"], queued["position"]), (202, "queued", 1))
        self.assertIsNone(before["active"])
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))

    def test_cancel_before_late_enqueue_installs_tombstone_first(self) -> None:
        cancelled = self.coordinator.cancel_operation(_identity("a"), "operation-a")
        late = self.coordinator.enqueue(_identity("a"), "build", "operation-a")
        snapshot = self.coordinator.snapshot_payload()

        self.assertEqual((cancelled[0], cancelled[1]["cancelled"]), (200, True))
        self.assertEqual((late[0], late[1]["error"]), (409, "operation_cancelled"))
        self.assertEqual(snapshot["queue"], [])
        self.assertIsNone(snapshot["active"])

    def test_cancel_during_prepared_prevents_publish_and_next_waiter_progresses(self) -> None:
        prepared = threading.Event()
        resume = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if event.get("event") == "session_grant_prepared":
                prepared.set()
                resume.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        first = coordinator.enqueue(_identity("a"), "first", "operation-a")[1]
        second = coordinator.enqueue(_identity("b"), "second", "operation-b")[1]
        result: list[tuple[int, dict]] = []
        waiter = threading.Thread(
            target=lambda: result.append(
                coordinator.wait(_identity("a"), first["ticket"], 0.0)
            )
        )
        waiter.start()
        self.assertTrue(prepared.wait(1.0))
        cancelled = coordinator.cancel_operation(_identity("a"), "operation-a")
        resume.set()
        waiter.join(2.0)

        claimed = coordinator.wait(_identity("b"), second["ticket"], 0.0)
        self.assertIn(cancelled[0], {200, 202})
        self.assertNotEqual(result[0][1].get("status"), "active")
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))
        self.assertFalse(
            any(
                event.get("event") == "session_granted"
                and event.get("operation_id") == "operation-a"
                for event in events
            )
        )

    def test_tombstone_capacity_fences_unseen_operations_and_recovers_after_ttl(self) -> None:
        for index in range(MAX_OPERATION_TOMBSTONES):
            status, _payload = self.coordinator.cancel_operation(
                _identity("a"), f"operation-{index}"
            )
            self.assertEqual(status, 200)
        blocked = self.coordinator.enqueue(_identity("b"), "blocked", "unseen")
        saturated = self.coordinator.snapshot_payload()["operation_tombstones"]

        self.assertEqual((blocked[0], blocked[1]["error"]), (503, "operation_tombstones_saturated"))
        self.assertEqual(saturated["count"], MAX_OPERATION_TOMBSTONES)
        self.assertTrue(saturated["saturated"])
        self.clock.advance(120.0)
        admitted = self.coordinator.enqueue(_identity("b"), "after ttl", "unseen")
        self.assertEqual((admitted[0], admitted[1]["status"]), (202, "queued"))

    def test_tombstone_saturation_does_not_block_cleanup_of_admitted_operation(self) -> None:
        queued = self.coordinator.enqueue(
            _identity("b"), "admitted", "operation-admitted"
        )[1]
        active = self.coordinator.wait(_identity("b"), queued["ticket"], 0.0)
        self.assertEqual((active[0], active[1]["status"]), (200, "active"))
        for index in range(MAX_OPERATION_TOMBSTONES):
            status, _payload = self.coordinator.cancel_operation(
                _identity("a"), f"operation-{index}"
            )
            self.assertEqual(status, 200)

        cancelled = self.coordinator.cancel_operation(
            _identity("b"), "operation-admitted"
        )
        snapshot = self.coordinator.snapshot_payload()

        self.assertEqual((cancelled[0], cancelled[1]["cancelled"]), (200, True))
        self.assertIsNone(snapshot["active"])
        blocked = self.coordinator.enqueue(_identity("c"), "new", "operation-new")
        self.assertEqual(
            (blocked[0], blocked[1]["error"]),
            (503, "operation_tombstones_saturated"),
        )

    def test_box_claimer_enqueue_is_not_fenced_when_tombstones_saturated(self) -> None:
        for index in range(MAX_OPERATION_TOMBSTONES):
            status, _payload = self.coordinator.cancel_operation(
                _identity("a"), f"operation-{index}"
            )
            self.assertEqual(status, 200)
        owner = _identity("L")
        claimed = self.coordinator.box_wait_touch(owner, claim=True)
        self.assertTrue(claimed.get("box_claimed"))
        waiter = _identity("W")
        waiting = self.coordinator.box_wait_touch(waiter, claim=False)
        self.assertFalse(waiting.get("box_claimed"))
        status, payload = self.coordinator.enqueue(owner, "build", "op-L")
        self.assertNotEqual(status, 503)
        self.assertNotEqual(payload.get("error"), "operation_tombstones_saturated")
        self.assertEqual((status, payload.get("status")), (202, "queued"))
        blocked_waiter = self.coordinator.enqueue(waiter, "build", "op-W")
        self.assertEqual(
            (blocked_waiter[0], blocked_waiter[1]["error"]),
            (503, "operation_tombstones_saturated"),
        )

    def test_box_claimer_acquire_with_new_operation_id_is_not_fenced_when_saturated(
        self,
    ) -> None:
        for index in range(MAX_OPERATION_TOMBSTONES):
            status, _payload = self.coordinator.cancel_operation(
                _identity("a"), f"operation-{index}"
            )
            self.assertEqual(status, 200)
        owner = _identity("L")
        claimed = self.coordinator.box_wait_touch(owner, claim=True)
        self.assertTrue(claimed.get("box_claimed"))
        self.assertIsNone(self.coordinator.snapshot_payload()["active"])
        status, payload = self.coordinator.acquire(owner, "build", "op-L")
        self.assertNotEqual(status, 503)
        self.assertNotEqual(payload.get("error"), "operation_tombstones_saturated")
        self.assertEqual((status, payload.get("status")), (200, "active"))
        blocked = self.coordinator.acquire(_identity("b"), "blocked", "op-stranger")
        self.assertEqual(
            (blocked[0], blocked[1]["error"]),
            (503, "operation_tombstones_saturated"),
        )

    def test_saturated_error_tells_stranger_to_wait_then_retry(self) -> None:
        for index in range(MAX_OPERATION_TOMBSTONES):
            status, _payload = self.coordinator.cancel_operation(
                _identity("a"), f"operation-{index}"
            )
            self.assertEqual(status, 200)
        status, payload = self.coordinator.enqueue(_identity("b"), "blocked", "unseen")
        self.assertEqual((status, payload.get("error")), (503, "operation_tombstones_saturated"))
        hint = payload.get("hint")
        self.assertIsInstance(hint, str)
        self.assertIn("session_acquire_wait", hint)
        self.assertIn("do not spin", hint)
        retry_after_s = payload.get("retry_after_s")
        self.assertIsInstance(retry_after_s, float)
        self.assertGreaterEqual(retry_after_s, 0.0)
        self.assertLessEqual(retry_after_s, float(OPERATION_TOMBSTONE_TTL_S))

    def test_box_claimer_cancel_of_unseen_operation_is_not_fenced_when_saturated(
        self,
    ) -> None:
        for index in range(MAX_OPERATION_TOMBSTONES):
            status, _payload = self.coordinator.cancel_operation(
                _identity("a"), f"operation-{index}"
            )
            self.assertEqual(status, 200)
        owner = _identity("L")
        claimed = self.coordinator.box_wait_touch(owner, claim=True)
        self.assertTrue(claimed.get("box_claimed"))
        cancelled = self.coordinator.cancel_operation(owner, "op-L-unseen")
        self.assertNotEqual(cancelled[0], 503)
        self.assertNotEqual(
            cancelled[1].get("error"), "operation_tombstones_saturated"
        )
        self.assertEqual((cancelled[0], cancelled[1].get("cancelled")), (200, True))
        snapshot = self.coordinator.snapshot_payload()["operation_tombstones"]
        self.assertEqual(snapshot["count"], MAX_OPERATION_TOMBSTONES + 1)
        blocked = self.coordinator.cancel_operation(_identity("b"), "op-stranger")
        self.assertEqual(
            (blocked[0], blocked[1]["error"]),
            (503, "operation_tombstones_saturated"),
        )

    def test_lease_holder_is_not_fenced_when_tombstones_saturated(self) -> None:
        owner = _identity("L")
        queued = self.coordinator.enqueue(owner, "build", "operation-lease")[1]
        active = self.coordinator.wait(owner, queued["ticket"], 0.0)
        self.assertEqual((active[0], active[1]["status"]), (200, "active"))
        self.assertFalse(self.coordinator.box_claim_public()["claimed"])
        for index in range(MAX_OPERATION_TOMBSTONES):
            status, _payload = self.coordinator.cancel_operation(
                _identity("a"), f"operation-{index}"
            )
            self.assertEqual(status, 200)
        enqueued = self.coordinator.enqueue(owner, "build", "op-L-other")
        self.assertNotEqual(enqueued[0], 503)
        self.assertNotEqual(
            enqueued[1].get("error"), "operation_tombstones_saturated"
        )
        self.assertEqual(
            (enqueued[0], enqueued[1].get("error")),
            (409, "operation_conflict"),
        )
        acquired = self.coordinator.acquire(owner, "build", "op-L-other-2")
        self.assertNotEqual(acquired[0], 503)
        self.assertNotEqual(
            acquired[1].get("error"), "operation_tombstones_saturated"
        )
        self.assertEqual(
            (acquired[0], acquired[1].get("error")),
            (409, "operation_conflict"),
        )
        blocked = self.coordinator.enqueue(_identity("b"), "blocked", "unseen")
        self.assertEqual(
            (blocked[0], blocked[1]["error"]),
            (503, "operation_tombstones_saturated"),
        )

    def test_cancel_exact_active_operation_releases_without_token(self) -> None:
        queued = self.coordinator.enqueue(
            _identity("a"), "build", "operation-a"
        )[1]
        active = self.coordinator.wait(_identity("a"), queued["ticket"], 0.0)
        self.assertEqual(active[1]["status"], "active")

        cancelled = self.coordinator.cancel_operation(_identity("a"), "operation-a")

        self.assertEqual((cancelled[0], cancelled[1]["cancelled"]), (200, True))
        self.assertIsNone(self.coordinator.snapshot_payload()["active"])


if __name__ == "__main__":
    unittest.main()
