from __future__ import annotations

import copy
import dataclasses
import math
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


SESSION_TTL_S = 120.0
BOX_CLAIM_TTL_S = 600.0
WAIT_MAX_S = 30.0
MAX_SESSION_QUEUE = 64
READ_ONLY_COMMANDS = frozenset(
    {
        "query_player_state",
        "query_all_players",
        "scene_raycast",
        "telemetry_read",
        "query_get_in_condition",
        "camera_get",
        "vehicle_telemetry",
        # F2.1: not a bridge command -- it reads host log files and never
        # reaches the game. Listed so it stays read-only if it is ever routed
        # through authorize(), since anything absent here is treated as mutating.
        "logs_since",
        # F3 pure reads (lease is command-name only; object_anim stays mutating
        # because a phase write shares the same command name).
        "surface_query",
        "object_inspect",
        "entities_query",
        "ui_tree",
    }
)
MAX_OPERATION_PIN_S = 300.0
MAX_CLEANUP_WORKERS = 4
MAX_RELEASE_AUDIT_WORKERS = 1
RELEASE_AUDIT_TIMEOUT_S = 0.05
MAX_OPERATION_TOMBSTONES = 128
OPERATION_TOMBSTONE_TTL_S = 120.0


def command_requires_lease(command: str) -> bool:
    return command not in READ_ONLY_COMMANDS


@dataclass(frozen=True)
class ClientIdentity:
    platform: str
    pid: int
    ppid: int
    started_at_utc: str
    session_id: str
    task_label: str = ""

    @classmethod
    def from_payload(cls, value: object) -> "ClientIdentity":
        if not isinstance(value, dict):
            raise ValueError("invalid_identity")
        platform = value.get("platform")
        pid, ppid = value.get("pid"), value.get("ppid")
        started = value.get("started_at_utc")
        session_id = value.get("session_id")
        label = value.get("task_label", "")
        if platform not in {"claude", "codex", "unknown"}:
            raise ValueError("invalid_identity")
        if any(
            not isinstance(number, int)
            or isinstance(number, bool)
            or number < 0
            for number in (pid, ppid)
        ):
            raise ValueError("invalid_identity")
        if (
            not isinstance(started, str)
            or not started
            or not isinstance(session_id, str)
            or not session_id
        ):
            raise ValueError("invalid_identity")
        if not isinstance(label, str) or len(label) > 120:
            raise ValueError("invalid_identity")
        return cls(platform, pid, ppid, started, session_id, label)

    def to_payload(self) -> dict[str, object]:
        return dataclasses.asdict(self)

    def public_payload(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "session": self.session_id[:12],
            "started_at_utc": self.started_at_utc,
            "task_label": self.task_label,
        }


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    http_status: int
    error: str
    owner_session_id: str | None
    lease_id: str | None = None
    reservation_id: str | None = None
    cleanup_degraded: tuple[str, ...] = ()


@dataclass(frozen=True)
class CleanupDisposition:
    fence_required: bool
    terminal_event: threading.Event
    terminal_result: dict[str, object]


@dataclass
class _Ticket:
    ticket_id: str
    client: ClientIdentity
    purpose: str
    created_at: float
    touched_at: float
    operation_id: str | None = None


@dataclass(frozen=True)
class _PendingAuthorization:
    reservation_id: str
    command: str
    deadline: float | None


@dataclass
class _Lease:
    client: ClientIdentity
    purpose: str
    lease_token: str
    lease_id: str
    granted_at: float
    expires_at: float
    source_ticket_id: str | None = None
    source_operation_id: str | None = None
    vehicle_active: bool = False
    pending_authorizations: list[_PendingAuthorization] = dataclasses.field(
        default_factory=list
    )
    committed_commands: dict[int, str] = dataclasses.field(default_factory=dict)
    operation_pins: dict[int, float] = dataclasses.field(default_factory=dict)

    def pinned_until(self) -> float:
        deadlines = [
            pending.deadline
            for pending in self.pending_authorizations
            if pending.deadline is not None
        ]
        deadlines.extend(self.operation_pins.values())
        return max(deadlines, default=0.0)

    def effective_expiry(self) -> float:
        return max(self.expires_at, self.pinned_until())


@dataclass
class _GrantInFlight:
    lease: _Lease
    ticket: _Ticket | None


AuditCallback = Callable[[dict[str, object]], object]
CleanupCallback = Callable[..., object]
FaultArmCallback = Callable[[dict[str, object]], str]
FaultTransitionCallback = Callable[..., str]
FaultClearCallback = Callable[[str, str], bool]
PersistSnapshotCallback = Callable[[dict[str, object]], object]


class SessionCoordinator:
    def __init__(
        self,
        *,
        time_fn: Callable[[], float] = time.monotonic,
        token_fn: Callable[[], str] | None = None,
        id_fn: Callable[[], str] | None = None,
        audit: AuditCallback | None = None,
        cleanup: CleanupCallback | None = None,
        cleanup_timeout_s: float = 30.0,
        recovered_audit_fault: dict[str, object] | None = None,
        recovered_lifecycle_fault: dict[str, object] | None = None,
        fault_id_fn: Callable[[], str] | None = None,
        daemon_generation: str = "local",
        utc_now_fn: Callable[[], str] | None = None,
        fault_arm: FaultArmCallback | None = None,
        fault_transition: FaultTransitionCallback | None = None,
        fault_clear: FaultClearCallback | None = None,
        persist_snapshot: PersistSnapshotCallback | None = None,
    ) -> None:
        self._time_fn = time_fn
        self._token_fn = token_fn or (lambda: secrets.token_urlsafe(32))
        self._id_fn = id_fn or (lambda: uuid.uuid4().hex)
        self._fault_id_fn = fault_id_fn or (lambda: uuid.uuid4().hex)
        if not isinstance(daemon_generation, str) or not daemon_generation:
            raise ValueError("invalid_daemon_generation")
        self._daemon_generation = daemon_generation
        self._utc_now_fn = utc_now_fn or (
            lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        callbacks = (fault_arm, fault_transition, fault_clear, persist_snapshot)
        if any(callback is not None for callback in callbacks) and not all(
            callback is not None for callback in callbacks
        ):
            raise ValueError("incomplete_coordination_fault_callbacks")
        self._fault_arm = fault_arm
        self._fault_transition = fault_transition
        self._fault_clear = fault_clear
        self._persist_snapshot = persist_snapshot
        self._audit = audit or (lambda _event: None)
        self._cleanup = cleanup or (
            lambda _session_id, _lease_id, _reason, _vehicle_active: {}
        )
        if (
            isinstance(cleanup_timeout_s, bool)
            or not isinstance(cleanup_timeout_s, (int, float))
            or not math.isfinite(float(cleanup_timeout_s))
            or float(cleanup_timeout_s) <= 0.0
        ):
            raise ValueError("invalid_cleanup_timeout")
        self._cleanup_timeout_s = float(cleanup_timeout_s)
        self._condition = threading.Condition()
        self._active: _Lease | None = None
        self._releasing: _Lease | None = None
        self._grant_inflight: _GrantInFlight | None = None
        self._handoff_pending = False
        self._handoff_audit_failed = False
        self._grant_audit_failed = False
        self._audit_fault = copy.deepcopy(recovered_audit_fault)
        self._lifecycle_recovery_fault = copy.deepcopy(
            recovered_lifecycle_fault
        )
        self._wal_marker: dict[str, object] | None = None
        self._wal_sha256: str | None = None
        self._repair_fence = False
        self._queue: list[_Ticket] = []
        self._queue_reservations: list[_Ticket] = []
        self._operation_tombstones: dict[tuple[ClientIdentity, str], float] = {}
        self._box_queue: list[_Ticket] = []
        self._box_tombstones: dict[tuple[str, str], float] = {}
        self._box_claiming: str | None = None
        self._box_claimed_at: float | None = None
        self._invalid_tokens: dict[str, tuple[ClientIdentity, str]] = {}
        self._revision = 0
        self._cleanup_worker_slots = threading.BoundedSemaphore(
            MAX_CLEANUP_WORKERS
        )
        self._cleanup_worker_active = 0
        self._cleanup_worker_saturated = 0
        self._release_audit_worker_slots = threading.BoundedSemaphore(
            MAX_RELEASE_AUDIT_WORKERS
        )
        self._audit_gate = threading.Lock()

    def acquire(
        self,
        client: ClientIdentity,
        purpose: str,
        operation_id: str | None = None,
    ) -> tuple[int, dict]:
        if operation_id is not None and not self._valid_operation_id(operation_id):
            return 400, {"error": "bad_operation_id"}
        with self._condition:
            self._purge_operation_tombstones_locked()
            degraded = self._expire_due()
            if self._repair_fence:
                return 503, {"error": "coordination_repairing"}
            if self._audit_fault is not None:
                return 503, self._audit_fault_error_locked()
            if self._queued_grant_audit_failed_locked(degraded):
                return 503, self._payload_with_degradation(
                    {"error": "audit_failed"}, degraded
                )
            collision = self._identity_collision_locked(client)
            if collision:
                return 403, self._payload_with_degradation(
                    {"error": "identity_mismatch"}, degraded
                )
            if operation_id is not None:
                existing = self._operation_state_locked(client, operation_id)
                if existing is not None:
                    return existing
                if self._operation_tombstoned_locked(client, operation_id):
                    return 409, {"error": "operation_cancelled"}
                if (
                    len(self._operation_tombstones) >= MAX_OPERATION_TOMBSTONES
                    and not self._admission_privileged_locked(client)
                ):
                    return 503, self._tombstone_saturated_error_locked()
                if self._client_has_other_operation_locked(client, operation_id):
                    return 409, {"error": "operation_conflict"}
            if (
                self._grant_inflight is not None
                and self._grant_inflight.lease.client == client
                and self._active is not self._grant_inflight.lease
            ):
                if self._grant_inflight.ticket is not None:
                    return 202, self._payload_with_degradation(
                        self._ticket_payload_locked(
                            self._grant_inflight.ticket, 1
                        ),
                        degraded,
                    )
                return 409, self._payload_with_degradation(
                    {"error": "session_granting"}, degraded
                )
            reservation = next(
                (
                    ticket
                    for ticket in self._queue_reservations
                    if ticket.client == client
                ),
                None,
            )
            if reservation is not None:
                deadline = time.monotonic() + RELEASE_AUDIT_TIMEOUT_S
                while reservation in self._queue_reservations:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, degraded
                        )
                    self._condition.wait(remaining)
                if reservation in self._queue:
                    return 202, self._payload_with_degradation(
                        self._ticket_payload_locked(
                            reservation, self._queue.index(reservation) + 1
                        ),
                        degraded,
                    )
                return 503, self._payload_with_degradation(
                    {"error": "audit_failed"}, degraded
                )

            if self._active is not None and self._active.client == client:
                lease = self._active
                if (
                    self._grant_inflight is not None
                    and self._grant_inflight.lease is lease
                ):
                    return 409, self._payload_with_degradation(
                        {"error": "session_granting"}, degraded
                    )
                if not self._write_audit_locked(
                    "session_acquire",
                    client=client,
                    reason="request",
                    duration_s=0.0,
                    lease_id=lease.lease_id,
                    decision="idempotent_active",
                ):
                    return 503, self._payload_with_degradation(
                        {"error": "audit_failed"}, degraded
                    )
                if self._active is not lease:
                    return 409, self._payload_with_degradation(
                        {"error": "coordination_changed"}, degraded
                    )
                return 200, self._payload_with_degradation(
                    self._active_payload_locked(lease), degraded
                )

            for position, ticket in enumerate(self._queue, start=1):
                if ticket.client == client:
                    if not self._write_audit_locked(
                        "session_acquire",
                        client=client,
                        reason="request",
                        duration_s=0.0,
                        ticket=ticket.ticket_id,
                        decision="idempotent_queued",
                    ):
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, degraded
                        )
                    if ticket not in self._queue:
                        return 409, self._payload_with_degradation(
                            {"error": "coordination_changed"}, degraded
                        )
                    return 202, self._payload_with_degradation(
                        self._ticket_payload_locked(
                            ticket, self._queue.index(ticket) + 1
                        ),
                        degraded,
                    )

            if self._releasing is not None and self._releasing.client == client:
                return 409, self._payload_with_degradation(
                    {"error": "session_releasing"}, degraded
                )

            if (
                self._active is None
                and self._releasing is None
                and self._grant_inflight is None
                and self._wal_marker is None
                and not self._handoff_pending
                and not self._queue
                and not self._queue_reservations
                and self._lifecycle_recovery_fault is None
            ):
                lease = self._new_lease_locked(
                    client,
                    purpose,
                    source_ticket_id=None,
                    source_operation_id=operation_id,
                )
                grant = _GrantInFlight(lease, None)
                self._grant_inflight = grant
                self._bump_revision_locked()
                self._condition.notify_all()
                grant_fence = self._wal_finish_fence_locked()
                grant_audit_outcome = self._write_initial_grant_audits_locked(lease)
                if grant_audit_outcome != "ok":
                    if self._grant_inflight is grant:
                        self._grant_inflight = None
                        self._bump_revision_locked()
                        self._condition.notify_all()
                    if grant_audit_outcome != "busy":
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, degraded
                        )
                elif (
                    (
                        self._fault_arm is not None
                        and not self._wal_finish_fence_matches_locked(grant_fence)
                    )
                    or self._grant_inflight is not grant
                    or self._active is not None
                    or self._releasing is not None
                    or self._operation_tombstoned_locked(client, operation_id)
                ):
                    cancelled = self._operation_tombstoned_locked(
                        client, operation_id
                    )
                    revoke_reason = (
                        "operation_cancelled" if cancelled else "coordination_changed"
                    )
                    compensated = self._compensate_provisional_grant_locked(
                        grant,
                        reason=revoke_reason,
                        write_revocation=lambda: self._write_audit_locked(
                            "session_grant_revoked",
                            client=lease.client,
                            reason=revoke_reason,
                            duration_s=0.0,
                            lease_id=lease.lease_id,
                            decision="revoked",
                            **(
                                {"operation_id": operation_id}
                                if operation_id is not None
                                else {}
                            ),
                        ),
                        requeue_ticket=False,
                    )
                    if not compensated:
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, ["audit_failed"]
                        )
                    if cancelled:
                        return 409, {"error": "operation_cancelled"}
                    return 409, self._payload_with_degradation(
                        {"error": "coordination_changed"}, degraded
                    )
                else:
                    granted_at = self._time_fn()
                    lease.granted_at = granted_at
                    lease.expires_at = granted_at + SESSION_TTL_S
                    self._active = lease
                    self._bump_revision_locked()
                    wal_outcome = self._finish_wal_after_publish_locked()
                    if wal_outcome == "coordination_changed":
                        cancelled = self._operation_tombstoned_locked(
                            client, operation_id
                        )
                        revoke_reason = (
                            "operation_cancelled" if cancelled else "coordination_changed"
                        )
                        compensated = self._compensate_provisional_grant_locked(
                            grant,
                            reason=revoke_reason,
                            write_revocation=lambda: self._write_audit_locked(
                                "session_grant_revoked",
                                client=lease.client,
                                reason=revoke_reason,
                                duration_s=0.0,
                                lease_id=lease.lease_id,
                                decision="revoked",
                                **(
                                    {"operation_id": operation_id}
                                    if operation_id is not None
                                    else {}
                                ),
                            ),
                            requeue_ticket=False,
                        )
                        if not compensated:
                            return 503, self._payload_with_degradation(
                                {"error": "audit_failed"}, ["audit_failed"]
                            )
                        if cancelled:
                            return 409, {"error": "operation_cancelled"}
                        return 409, self._payload_with_degradation(
                            {"error": "coordination_changed"}, degraded
                        )
                    if wal_outcome != "ok":
                        if wal_outcome != "clear_pending":
                            revoked = self._write_audit_locked(
                                "session_grant_revoked",
                                client=lease.client,
                                reason=wal_outcome,
                                duration_s=0.0,
                                lease_id=lease.lease_id,
                                decision="revoked",
                            )
                            if self._active is lease:
                                self._active = None
                                self._remember_invalid_token_locked(
                                    lease, "lease_invalid"
                                )
                                if self._grant_inflight is grant:
                                    self._grant_inflight = None
                                self._bump_revision_locked()
                                self._condition.notify_all()
                            failure = (
                                wal_outcome if revoked else "compensation_failed"
                            )
                            self._latch_wal_fault_locked(failure, failure)
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, ["audit_failed"]
                        )
                    if (
                        self._grant_inflight is not grant
                        or self._active is not lease
                        or self._releasing is not None
                        or self._audit_fault is not None
                        or self._lifecycle_recovery_fault is not None
                        or self._repair_fence
                        or self._wal_marker is not None
                    ):
                        compensated = self._compensate_provisional_grant_locked(
                            grant,
                            reason="coordination_changed",
                            write_revocation=lambda: self._write_audit_locked(
                                "session_grant_revoked",
                                client=lease.client,
                                reason="coordination_changed",
                                duration_s=0.0,
                                lease_id=lease.lease_id,
                                decision="revoked",
                            ),
                            requeue_ticket=False,
                        )
                        if not compensated:
                            return 503, self._payload_with_degradation(
                                {"error": "audit_failed"}, ["audit_failed"]
                            )
                        return 409, self._payload_with_degradation(
                            {"error": "coordination_changed"}, degraded
                        )
                    if self._operation_tombstoned_locked(client, operation_id):
                        compensated = self._compensate_provisional_grant_locked(
                            grant,
                            reason="operation_cancelled",
                            write_revocation=lambda: self._write_audit_locked(
                                "session_grant_revoked",
                                client=lease.client,
                                reason="operation_cancelled",
                                duration_s=0.0,
                                lease_id=lease.lease_id,
                                decision="revoked",
                                **(
                                    {"operation_id": operation_id}
                                    if operation_id is not None
                                    else {}
                                ),
                            ),
                            requeue_ticket=False,
                        )
                        if not compensated:
                            return 503, self._payload_with_degradation(
                                {"error": "audit_failed"}, ["audit_failed"]
                            )
                        return 409, {"error": "operation_cancelled"}
                    self._grant_inflight = None
                    self._bump_revision_locked()
                    self._condition.notify_all()
                    payload = self._active_payload_locked(lease)
                    if degraded:
                        payload["cleanup_degraded"] = degraded
                    return 200, payload

            if self._queue_capacity_locked() >= MAX_SESSION_QUEUE:
                return 429, self._payload_with_degradation(
                    {"error": "queue_full", "capacity": MAX_SESSION_QUEUE}, degraded
                )

            now = self._time_fn()
            ticket = _Ticket(
                self._id_fn(), client, purpose, now, now, operation_id
            )
            self._queue_reservations.append(ticket)
            if not self._write_audit_locked(
                "session_acquire",
                client=client,
                reason="request",
                duration_s=0.0,
                ticket=ticket.ticket_id,
                decision="queue",
                **(
                    {"operation_id": operation_id}
                    if operation_id is not None
                    else {}
                ),
            ):
                if ticket in self._queue_reservations:
                    self._queue_reservations.remove(ticket)
                    self._condition.notify_all()
                return 503, self._payload_with_degradation(
                    {"error": "audit_failed"}, degraded
                )
            if (
                ticket not in self._queue_reservations
                or self._identity_collision_locked(client)
                or any(
                queued.client == client for queued in self._queue
                )
            ):
                if ticket in self._queue_reservations:
                    self._queue_reservations.remove(ticket)
                    self._condition.notify_all()
                return 409, self._payload_with_degradation(
                    {"error": "coordination_changed"}, degraded
                )
            if not self._write_audit_locked(
                "session_queued",
                client=client,
                reason="lease_busy",
                duration_s=0.0,
                ticket=ticket.ticket_id,
                purpose=purpose,
                position=len(self._queue) + self._queue_reservations.index(ticket) + 1,
                decision="queued",
                **(
                    {"operation_id": operation_id}
                    if operation_id is not None
                    else {}
                ),
            ):
                if ticket in self._queue_reservations:
                    self._queue_reservations.remove(ticket)
                    self._condition.notify_all()
                return 503, self._payload_with_degradation(
                    {"error": "audit_failed"}, degraded
                )
            while (
                ticket in self._queue_reservations
                and self._queue_reservations[0] is not ticket
            ):
                self._condition.wait()
            if ticket not in self._queue_reservations:
                return 409, self._payload_with_degradation(
                    {"error": "coordination_changed"}, degraded
                )
            self._queue_reservations.remove(ticket)
            self._condition.notify_all()
            if self._identity_collision_locked(client) or any(
                queued.client == client for queued in self._queue
            ):
                return 409, self._payload_with_degradation(
                    {"error": "coordination_changed"}, degraded
                )
            if len(self._queue) >= MAX_SESSION_QUEUE:
                return 429, self._payload_with_degradation(
                    {"error": "queue_full", "capacity": MAX_SESSION_QUEUE}, degraded
                )
            ticket.touched_at = self._time_fn()
            self._queue.append(ticket)
            self._bump_revision_locked()
            self._condition.notify_all()
            payload = self._ticket_payload_locked(ticket, len(self._queue))
            if degraded:
                payload["cleanup_degraded"] = degraded
            return 202, payload

    def enqueue(
        self, client: ClientIdentity, purpose: str, operation_id: str
    ) -> tuple[int, dict]:
        if not isinstance(purpose, str) or not purpose.strip():
            return 400, {"error": "bad_purpose"}
        if not self._valid_operation_id(operation_id):
            return 400, {"error": "bad_operation_id"}
        with self._condition:
            self._purge_operation_tombstones_locked()
            degraded = self._expire_due()
            if self._repair_fence:
                return 503, {"error": "coordination_repairing"}
            if self._audit_fault is not None:
                return 503, self._audit_fault_error_locked()
            if self._identity_collision_locked(client):
                return 403, {"error": "identity_mismatch"}
            existing = self._operation_state_locked(client, operation_id)
            if existing is not None:
                return existing
            if self._operation_tombstoned_locked(client, operation_id):
                return 409, {"error": "operation_cancelled"}
            if (
                len(self._operation_tombstones) >= MAX_OPERATION_TOMBSTONES
                and not self._admission_privileged_locked(client)
            ):
                return 503, self._tombstone_saturated_error_locked()
            if self._client_has_other_operation_locked(client, operation_id):
                return 409, {"error": "operation_conflict"}
            if self._queue_capacity_locked() >= MAX_SESSION_QUEUE:
                return 429, {"error": "queue_full", "capacity": MAX_SESSION_QUEUE}

            now = self._time_fn()
            ticket = _Ticket(
                self._id_fn(), client, purpose.strip(), now, now, operation_id
            )
            self._queue_reservations.append(ticket)
            if not self._write_audit_locked(
                "session_acquire",
                client=client,
                reason="request",
                duration_s=0.0,
                ticket=ticket.ticket_id,
                operation_id=operation_id,
                decision="queue",
            ):
                if ticket in self._queue_reservations:
                    self._queue_reservations.remove(ticket)
                    self._condition.notify_all()
                return 503, {"error": "audit_failed"}
            if self._operation_tombstoned_locked(client, operation_id):
                if ticket in self._queue_reservations:
                    self._queue_reservations.remove(ticket)
                    self._condition.notify_all()
                return 409, {"error": "operation_cancelled"}
            if not self._write_audit_locked(
                "session_queued",
                client=client,
                reason="lease_busy",
                duration_s=0.0,
                ticket=ticket.ticket_id,
                operation_id=operation_id,
                purpose=purpose.strip(),
                position=len(self._queue) + self._queue_reservations.index(ticket) + 1,
                decision="queued",
            ):
                if ticket in self._queue_reservations:
                    self._queue_reservations.remove(ticket)
                    self._condition.notify_all()
                return 503, {"error": "audit_failed"}
            while (
                ticket in self._queue_reservations
                and self._queue_reservations[0] is not ticket
            ):
                self._condition.wait()
            if ticket not in self._queue_reservations or self._operation_tombstoned_locked(
                client, operation_id
            ):
                if ticket in self._queue_reservations:
                    self._queue_reservations.remove(ticket)
                    self._condition.notify_all()
                return 409, {"error": "operation_cancelled"}
            self._queue_reservations.remove(ticket)
            self._queue.append(ticket)
            self._bump_revision_locked()
            self._condition.notify_all()
            return 202, self._payload_with_degradation(
                self._ticket_payload_locked(ticket, len(self._queue)), degraded
            )

    def cancel_operation(
        self, client: ClientIdentity, operation_id: str
    ) -> tuple[int, dict]:
        if not self._valid_operation_id(operation_id):
            return 400, {"error": "bad_operation_id"}
        with self._condition:
            self._purge_operation_tombstones_locked()
            key = (client, operation_id)
            operation_admitted = (
                self._operation_state_locked(client, operation_id) is not None
            )
            if (
                key not in self._operation_tombstones
                and len(self._operation_tombstones) >= MAX_OPERATION_TOMBSTONES
                and not operation_admitted
                and not self._admission_privileged_locked(client)
            ):
                return 503, self._tombstone_saturated_error_locked()
            # Saturation fences only unseen admission. Cleanup of an operation
            # already admitted must retain its cancellation marker so it cannot
            # publish late or block the queue until lease/ticket expiry.
            self._operation_tombstones[key] = (
                self._time_fn() + OPERATION_TOMBSTONE_TTL_S
            )
            self._bump_revision_locked()
            self._condition.notify_all()
            removed: _Ticket | None = None
            for collection in (self._queue_reservations, self._queue):
                removed = next(
                    (
                        ticket
                        for ticket in collection
                        if ticket.client == client
                        and ticket.operation_id == operation_id
                    ),
                    None,
                )
                if removed is not None:
                    collection.remove(removed)
                    self._bump_revision_locked()
                    self._condition.notify_all()
                    break
            if removed is not None:
                self._write_audit_locked(
                    "ticket_cancelled",
                    client=client,
                    reason="client_cancel",
                    duration_s=0.0,
                    ticket=removed.ticket_id,
                    operation_id=operation_id,
                    decision="cancelled",
                )
            inflight = self._grant_inflight
            if (
                inflight is not None
                and inflight.lease.client == client
                and inflight.lease.source_operation_id == operation_id
            ):
                return 202, {"cancelled": False, "resolving": True}
            active = self._active
            if (
                active is not None
                and active.client == client
                and active.source_operation_id == operation_id
            ):
                self._release_active_locked("operation_cancelled")
                if self._active is active:
                    return 202, {"cancelled": False, "resolving": True}
            return 200, {"cancelled": True, "operation_id": operation_id}

    def cancel(self, client: ClientIdentity, ticket_id: str) -> tuple[int, dict]:
        if not isinstance(ticket_id, str) or not ticket_id:
            return 403, {"error": "ticket_invalid"}
        operation_id: str | None = None
        with self._condition:
            ticket = next(
                (
                    item
                    for item in (*self._queue, *self._queue_reservations)
                    if item.ticket_id == ticket_id and item.client == client
                ),
                None,
            )
            if ticket is None:
                return 403, {"error": "ticket_invalid"}
            if ticket.operation_id is not None:
                operation_id = ticket.operation_id
            else:
                for collection in (self._queue, self._queue_reservations):
                    if ticket in collection:
                        collection.remove(ticket)
                self._bump_revision_locked()
                self._condition.notify_all()
                if not self._write_audit_locked(
                    "ticket_cancelled",
                    client=client,
                    reason="client_cancel",
                    duration_s=0.0,
                    ticket=ticket_id,
                    decision="cancelled",
                ):
                    return 503, {"error": "audit_failed"}
                return 200, {"cancelled": True, "ticket": ticket_id}
        return self.cancel_operation(client, operation_id)

    def wait(
        self, client: ClientIdentity, ticket_id: str, timeout_s: float
    ) -> tuple[int, dict]:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            return 400, {"error": "bad_wait_timeout"}
        bounded_timeout = max(0.0, min(float(timeout_s), WAIT_MAX_S))
        if not math.isfinite(bounded_timeout):
            return 400, {"error": "bad_wait_timeout"}

        wait_started_at = time.monotonic()
        real_deadline = wait_started_at + bounded_timeout
        with self._condition:
            initial = self._find_ticket_locked(ticket_id)
            protect_ticket = None
            now = self._time_fn()
            if (
                initial is not None
                and initial.client == client
                and now - initial.touched_at <= SESSION_TTL_S
            ):
                protect_ticket = ticket_id
            degraded = self._expire_due(protect_ticket_id=protect_ticket)
            if self._repair_fence:
                return 503, {"error": "coordination_repairing"}
            if self._audit_fault is not None:
                return 503, self._audit_fault_error_locked()
            if self._queued_grant_audit_failed_locked(degraded):
                return 503, self._payload_with_degradation(
                    {"error": "audit_failed"}, degraded
                )

            active_payload = self._wait_active_payload_locked(client, ticket_id)
            if active_payload is not None:
                active_lease = self._active
                if not self._write_wait_audit_locked(
                    "session_wait",
                    client=client,
                    ticket=ticket_id,
                    lease_id=self._active.lease_id if self._active is not None else None,
                    decision="active",
                    reason="granted",
                    wait_started_at=wait_started_at,
                ):
                    return 503, self._payload_with_degradation(
                        {"error": "audit_failed"}, degraded
                    )
                if self._active is not active_lease:
                    return 409, self._payload_with_degradation(
                        {"error": "coordination_changed"}, degraded
                    )
                if degraded:
                    active_payload["cleanup_degraded"] = degraded
                return 200, active_payload

            ticket = self._find_ticket_locked(ticket_id)
            if ticket is None or ticket.client != client:
                return 403, self._payload_with_degradation(
                    {"error": "ticket_invalid"}, degraded
                )
            touched_at = self._time_fn()
            protected_by_grant = (
                self._grant_inflight is not None
                and self._grant_inflight.ticket is ticket
            )
            if not protected_by_grant and ticket.touched_at != touched_at:
                ticket.touched_at = touched_at
                # A live wait may refresh its TTL while a release/grant WAL is
                # completing. That liveness-only refresh is included in the WAL's
                # terminal snapshot, but must not invalidate its revision fence.
                if self._wal_marker is None:
                    self._bump_revision_locked()

            while True:
                degraded.extend(self._expire_due())
                if self._queued_grant_audit_failed_locked(degraded):
                    return 503, self._payload_with_degradation(
                        {"error": "audit_failed"}, degraded
                    )
                active_payload = self._wait_active_payload_locked(client, ticket_id)
                if active_payload is not None:
                    active_lease = self._active
                    if not self._write_wait_audit_locked(
                        "session_wait",
                        client=client,
                        ticket=ticket_id,
                        lease_id=(
                            self._active.lease_id
                            if self._active is not None
                            else None
                        ),
                        decision="active",
                        reason="granted",
                        wait_started_at=wait_started_at,
                    ):
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, degraded
                        )
                    if self._active is not active_lease:
                        return 409, self._payload_with_degradation(
                            {"error": "coordination_changed"}, degraded
                        )
                    if degraded:
                        active_payload["cleanup_degraded"] = self._unique(degraded)
                    return 200, active_payload

                ticket = self._find_ticket_locked(ticket_id)
                if ticket is None or ticket.client != client:
                    if not self._write_wait_audit_locked(
                        "session_wait",
                        client=client,
                        ticket=ticket_id,
                        decision="expired",
                        reason="ticket_expired",
                        wait_started_at=wait_started_at,
                    ):
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, degraded
                        )
                    body: dict[str, object] = {"error": "ticket_expired"}
                    if degraded:
                        body["cleanup_degraded"] = self._unique(degraded)
                    return 410, body

                if (
                    self._queue
                    and self._queue[0] is ticket
                    and self._active is None
                    and self._releasing is None
                    and self._grant_inflight is None
                    and self._wal_marker is None
                    and not self._handoff_pending
                    and not self._grant_audit_failed
                    and self._lifecycle_recovery_fault is None
                ):
                    claim_outcome, claim_payload = self._claim_head_from_wait_locked(
                        ticket
                    )
                    if claim_outcome == "granted" and claim_payload is not None:
                        if degraded:
                            claim_payload["cleanup_degraded"] = self._unique(degraded)
                        return 200, claim_payload
                    if claim_outcome == "audit_failed":
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, degraded
                        )
                    if claim_outcome == "coordination_changed":
                        return 409, self._payload_with_degradation(
                            {"error": "coordination_changed"}, degraded
                        )
                    if claim_outcome == "operation_cancelled":
                        return 409, self._payload_with_degradation(
                            {"error": "operation_cancelled"}, degraded
                        )
                    if claim_outcome == "busy":
                        payload = self._ticket_payload_locked(ticket, 1)
                        if degraded:
                            payload["cleanup_degraded"] = self._unique(degraded)
                        return 202, payload

                real_remaining = real_deadline - time.monotonic()
                if real_remaining <= 0.0:
                    audit_outcome = self._write_wait_audit_outcome_locked(
                        "session_wait",
                        client=client,
                        ticket=ticket_id,
                        decision="queued",
                        reason="queued",
                        wait_started_at=wait_started_at,
                        blocking=False,
                    )
                    if audit_outcome == "failed":
                        return 503, self._payload_with_degradation(
                            {"error": "audit_failed"}, degraded
                        )
                    active_payload = self._wait_active_payload_locked(client, ticket_id)
                    still_granting = (
                        self._grant_inflight is not None
                        and self._grant_inflight.ticket is ticket
                    )
                    if ticket in self._queue:
                        position = self._queue.index(ticket) + 1
                    elif still_granting or active_payload is not None:
                        position = 1
                    else:
                        return 409, self._payload_with_degradation(
                            {"error": "coordination_changed"}, degraded
                        )
                    payload = self._ticket_payload_locked(ticket, position)
                    if degraded:
                        payload["cleanup_degraded"] = self._unique(degraded)
                    return 202, payload

                now = self._time_fn()
                ticket_remaining = max(0.0, ticket.touched_at + SESSION_TTL_S - now)
                owner_remaining = real_remaining
                if self._active is not None:
                    owner_remaining = max(0.0, self._active.effective_expiry() - now)
                wait_for = min(real_remaining, ticket_remaining, owner_remaining)
                if wait_for <= 0.0:
                    continue
                self._condition.wait(wait_for)

    def heartbeat(
        self, client: ClientIdentity, lease_token: str | None
    ) -> tuple[int, dict]:
        with self._condition:
            degraded = self._expire_due()
            lease, error = self._validate_token_locked(client, lease_token)
            if lease is None:
                return self._token_error_tuple(error, degraded)
            if not self._write_audit_locked(
                "session_heartbeat",
                client=client,
                reason="owner_heartbeat",
                duration_s=0.0,
                lease_id=lease.lease_id,
                decision="renewed",
            ):
                return 503, self._payload_with_degradation(
                    {"error": "audit_failed"}, degraded
                )
            if self._active is not lease:
                return 409, self._payload_with_degradation(
                    {"error": "lease_invalid"}, degraded
                )
            expires_at = self._time_fn() + SESSION_TTL_S
            if lease.expires_at != expires_at:
                lease.expires_at = expires_at
                self._bump_revision_locked()
            payload = self._active_payload_locked(lease)
            if degraded:
                payload["cleanup_degraded"] = degraded
            return 200, payload

    def release(
        self,
        client: ClientIdentity,
        lease_token: str | None,
        reason: str = "owner_release",
    ) -> tuple[int, dict]:
        with self._condition:
            degraded = self._expire_due()
            lease, error = self._validate_token_locked(client, lease_token)
            if lease is None:
                return self._token_error_tuple(error, degraded)
            cleanup_result, release_degraded = self._release_active_locked(reason)
            degraded.extend(release_degraded)
            if self._active is lease:
                return 503, {
                    "error": "audit_failed",
                    "cleanup_degraded": self._unique(degraded),
                }
            if cleanup_result.get("error") == "lifecycle_cleanup_pending":
                return 202, {
                    "error": "lifecycle_cleanup_pending",
                    "released": False,
                    "cleanup_degraded": self._unique(degraded),
                }
            return 200, {
                "released": True,
                "cleanup": cleanup_result,
                "cleanup_degraded": self._unique(degraded),
            }

    def authorize(
        self,
        client: ClientIdentity,
        lease_token: str | None,
        command: str,
        operation_timeout_s: float = 0.0,
    ) -> AuthorizationDecision:
        with self._condition:
            degraded = tuple(self._expire_due())
            requires_lease = command_requires_lease(command)
            if not requires_lease:
                lease = None
                if lease_token is not None:
                    lease, _ = self._validate_token_locked(client, lease_token)
                if lease is not None:
                    audit_ok = self._write_audit_locked(
                        "session_authorized",
                        client=client,
                        reason="lease_valid",
                        duration_s=0.0,
                        lease_id=lease.lease_id,
                        command=command,
                        decision="owner_read",
                    )
                    if self._active is lease and audit_ok:
                        expires_at = self._time_fn() + SESSION_TTL_S
                        if lease.expires_at != expires_at:
                            lease.expires_at = expires_at
                            self._bump_revision_locked()
                        return AuthorizationDecision(
                            True,
                            200,
                            "",
                            client.session_id,
                            lease_id=lease.lease_id,
                            cleanup_degraded=degraded,
                        )
                    return AuthorizationDecision(
                        True, 200, "", None, cleanup_degraded=degraded
                    )
                self._write_audit_locked(
                    "session_authorized",
                    client=client,
                    reason="read_only",
                    duration_s=0.0,
                    command=command,
                    decision="read",
                )
                return AuthorizationDecision(
                    True, 200, "", None, cleanup_degraded=degraded
                )

            lease, error = self._validate_token_locked(client, lease_token)
            if lease is None:
                if lease_token is None:
                    error = "lease_required"
                status = self._token_error_status(error)
                self._write_audit_locked(
                    "session_rejected",
                    client=client,
                    reason=error,
                    duration_s=0.0,
                    command=command,
                    decision=error,
                    blocking=error != "session_granting",
                )
                return AuthorizationDecision(
                    False, status, error, None, cleanup_degraded=degraded
                )

            if not self._write_audit_locked(
                "session_authorized",
                client=client,
                reason="lease_valid",
                duration_s=0.0,
                lease_id=lease.lease_id,
                command=command,
                decision="mutation",
            ):
                return AuthorizationDecision(
                    False, 503, "audit_failed", None, cleanup_degraded=degraded
                )
            if self._active is not lease:
                return AuthorizationDecision(
                    False, 409, "lease_invalid", None, cleanup_degraded=degraded
                )
            now = self._time_fn()
            lease.expires_at = now + SESSION_TTL_S
            pin_seconds = self._bounded_operation_timeout(operation_timeout_s)
            deadline = now + pin_seconds if pin_seconds > 0.0 else None
            reservation_id = uuid.uuid4().hex
            lease.pending_authorizations.append(
                _PendingAuthorization(
                    reservation_id=reservation_id,
                    command=command,
                    deadline=deadline,
                )
            )
            self._bump_revision_locked()
            return AuthorizationDecision(
                True,
                200,
                "",
                client.session_id,
                lease_id=lease.lease_id,
                reservation_id=reservation_id,
                cleanup_degraded=degraded,
            )

    def commit_authorization(
        self,
        owner_session_id: str,
        lease_id: str,
        reservation_id: str,
        command_id: int,
        command: str,
        args: object,
    ) -> bool:
        """Consume one exact reservation and register a claimable command.

        Committed commands without an operation deadline remain attributable but do
        not extend the lease TTL.  Callers hold their state/operation lock first, so
        this condition acquisition is the authority linearization point.
        """

        with self._condition:
            lease = self._active
            if (
                lease is None
                or lease.client.session_id != owner_session_id
                or lease.lease_id != lease_id
            ):
                return False
            authorization_index = next(
                (
                    index
                    for index, pending in enumerate(lease.pending_authorizations)
                    if pending.reservation_id == reservation_id
                    and pending.command == command
                ),
                None,
            )
            if authorization_index is None or command_id in lease.committed_commands:
                return False
            pending = lease.pending_authorizations.pop(authorization_index)
            lease.committed_commands[command_id] = command
            if pending.deadline is not None and pending.deadline > self._time_fn():
                lease.operation_pins[command_id] = pending.deadline
            if (
                command == "vehicle_trace"
                and isinstance(args, dict)
                and args.get("mode") == "start"
            ):
                lease.vehicle_active = True
            elif command == "vehicle_control" and self._has_nonzero_control(args):
                lease.vehicle_active = True
            self._bump_revision_locked()
            self._condition.notify_all()
            return True

    def claim_dispatch(
        self,
        owner_session_id: str,
        lease_id: str,
        command_id: int,
        command: str,
    ) -> bool:
        """Atomically prove that a queued mutation still belongs to the active lease."""

        with self._condition:
            lease = self._active
            return bool(
                lease is not None
                and lease.client.session_id == owner_session_id
                and lease.lease_id == lease_id
                and lease.committed_commands.get(command_id) == command
            )

    def reservation_active(
        self,
        owner_session_id: str,
        lease_id: str,
        reservation_id: str,
        command: str,
    ) -> bool:
        """Read-only exact authority check used before lifecycle preflight I/O."""

        with self._condition:
            self._expire_due()
            lease = self._active
            return bool(
                lease is not None
                and lease.client.session_id == owner_session_id
                and lease.lease_id == lease_id
                and any(
                    pending.reservation_id == reservation_id
                    and pending.command == command
                    for pending in lease.pending_authorizations
                )
            )

    def cleanup_authority_active(
        self, owner_session_id: str, lease_id: str
    ) -> bool:
        """Fence late cleanup work to the exact lease still in RELEASING."""

        with self._condition:
            lease = self._releasing
            return bool(
                lease is not None
                and lease.client.session_id == owner_session_id
                and lease.lease_id == lease_id
            )

    def abort_reservation(
        self,
        owner_session_id: str,
        lease_id: str,
        reservation_id: str,
        reason: str,
    ) -> tuple[str, ...]:
        if not isinstance(reason, str) or not reason:
            raise ValueError("invalid_abort_reason")
        with self._condition:
            degraded = self._expire_due()
            lease = self._exact_lease_locked(owner_session_id, lease_id)
            if lease is None:
                return tuple(self._unique(degraded))
            authorization_index = next(
                (
                    index
                    for index, pending in enumerate(lease.pending_authorizations)
                    if pending.reservation_id == reservation_id
                ),
                None,
            )
            if authorization_index is None:
                return tuple(self._unique(degraded))
            pending = lease.pending_authorizations.pop(authorization_index)
            self._bump_revision_locked()
            if not self._write_audit_locked(
                "session_rejected",
                client=lease.client,
                reason=reason,
                duration_s=0.0,
                lease_id=lease.lease_id,
                command=pending.command,
                decision="authorization_aborted",
            ):
                degraded.append("audit_failed")
            self._condition.notify_all()
            degraded.extend(self._expire_due())
            return tuple(self._unique(degraded))

    def reject_reservation(
        self,
        owner_session_id: str,
        lease_id: str,
        reservation_id: str,
        reason: str,
        http_status: int = 409,
    ) -> AuthorizationDecision:
        if not isinstance(reason, str) or not reason:
            raise ValueError("invalid_rejection_reason")
        if not isinstance(http_status, int) or isinstance(http_status, bool):
            raise ValueError("invalid_http_status")
        with self._condition:
            degraded = self._expire_due()
            lease = self._exact_lease_locked(owner_session_id, lease_id)
            if lease is None:
                return AuthorizationDecision(
                    False,
                    http_status,
                    reason,
                    None,
                    cleanup_degraded=tuple(self._unique(degraded)),
                )
            authorization_index = next(
                (
                    index
                    for index, pending in enumerate(lease.pending_authorizations)
                    if pending.reservation_id == reservation_id
                ),
                None,
            )
            pending = (
                lease.pending_authorizations.pop(authorization_index)
                if authorization_index is not None
                else None
            )
            if pending is not None:
                self._bump_revision_locked()
            audit_ok = self._write_audit_locked(
                "session_rejected",
                client=lease.client,
                reason=reason,
                duration_s=0.0,
                lease_id=lease.lease_id,
                command=pending.command if pending is not None else "",
                decision="authorization_rejected",
            )
            self._condition.notify_all()
            degraded.extend(self._expire_due())
            if not audit_ok:
                return AuthorizationDecision(
                    False,
                    503,
                    "audit_failed",
                    None,
                    cleanup_degraded=tuple(self._unique([*degraded, "audit_failed"])),
                )
            return AuthorizationDecision(
                False,
                http_status,
                reason,
                None,
                cleanup_degraded=tuple(self._unique(degraded)),
            )

    def discard_committed(
        self,
        client: ClientIdentity,
        lease_id: str,
        command_id: int,
        command: str,
        reason: str,
    ) -> tuple[str, ...]:
        """Close committed metadata and leave a durable rejection event."""

        with self._condition:
            degraded = self._expire_due()
            lease = self._exact_lease_locked(client.session_id, lease_id)
            if lease is not None:
                lease.committed_commands.pop(command_id, None)
                lease.operation_pins.pop(command_id, None)
                self._bump_revision_locked()
            if not self._write_audit_locked(
                "session_rejected",
                client=client,
                reason=reason,
                duration_s=0.0,
                lease_id=lease_id,
                command=command,
                command_id=command_id,
                decision="dispatch_discarded",
            ):
                degraded.append("audit_failed")
            self._condition.notify_all()
            degraded.extend(self._expire_due())
            return tuple(self._unique(degraded))

    def finish_operation_exact(
        self,
        owner_session_id: str,
        lease_id: str,
        command_id: int,
        *,
        command_succeeded: bool = False,
    ) -> None:
        with self._condition:
            self._expire_due()
            lease = self._exact_lease_locked(owner_session_id, lease_id)
            if lease is None:
                return
            removed_command = lease.committed_commands.pop(command_id, None)
            removed_pin = lease.operation_pins.pop(command_id, None)
            if removed_command == "vehicle_release" and command_succeeded:
                lease.vehicle_active = False
            if removed_command is not None or removed_pin is not None:
                self._bump_revision_locked()
            self._condition.notify_all()
            self._expire_due()

    def abort_authorization(
        self, owner_session_id: str, command: str, reason: str
    ) -> tuple[str, ...]:
        if not isinstance(reason, str) or not reason:
            raise ValueError("invalid_abort_reason")

        with self._condition:
            degraded = self._expire_due()
            lease = self._active
            if lease is None or lease.client.session_id != owner_session_id:
                lease = self._releasing
            if lease is None or lease.client.session_id != owner_session_id:
                return tuple(self._unique(degraded))

            authorization_index = next(
                (
                    index
                    for index, pending in enumerate(lease.pending_authorizations)
                    if pending.command == command
                ),
                None,
            )
            if authorization_index is None:
                return tuple(self._unique(degraded))
            lease.pending_authorizations.pop(authorization_index)

            self._bump_revision_locked()
            if not self._write_audit_locked(
                "session_rejected",
                client=lease.client,
                reason=reason,
                duration_s=0.0,
                lease_id=lease.lease_id,
                command=command,
                decision="authorization_aborted",
            ):
                degraded.append("audit_failed")
            self._condition.notify_all()
            degraded.extend(self._expire_due())
            return tuple(self._unique(degraded))

    def reject_authorization(
        self,
        owner_session_id: str,
        command: str,
        reason: str,
        http_status: int = 409,
    ) -> AuthorizationDecision:
        if not isinstance(reason, str) or not reason:
            raise ValueError("invalid_rejection_reason")
        if not isinstance(http_status, int) or isinstance(http_status, bool):
            raise ValueError("invalid_http_status")

        with self._condition:
            degraded = self._expire_due()
            lease = self._active
            if lease is None or lease.client.session_id != owner_session_id:
                lease = self._releasing
            if lease is None or lease.client.session_id != owner_session_id:
                return AuthorizationDecision(
                    False,
                    http_status,
                    reason,
                    None,
                    cleanup_degraded=tuple(self._unique(degraded)),
                )

            authorization_index = next(
                (
                    index
                    for index, pending in enumerate(lease.pending_authorizations)
                    if pending.command == command
                ),
                None,
            )
            if authorization_index is not None:
                lease.pending_authorizations.pop(authorization_index)
                self._bump_revision_locked()

            audit_ok = self._write_audit_locked(
                "session_rejected",
                client=lease.client,
                reason=reason,
                duration_s=0.0,
                lease_id=lease.lease_id,
                command=command,
                decision="authorization_rejected",
            )
            self._condition.notify_all()
            degraded.extend(self._expire_due())
            if not audit_ok:
                return AuthorizationDecision(
                    False,
                    503,
                    "audit_failed",
                    None,
                    cleanup_degraded=tuple(self._unique([*degraded, "audit_failed"])),
                )
            return AuthorizationDecision(
                False,
                http_status,
                reason,
                None,
                cleanup_degraded=tuple(self._unique(degraded)),
            )

    def admin_release(self, lease_id: str, reason: str) -> tuple[int, dict[str, object]]:
        if not isinstance(lease_id, str) or not lease_id:
            return 400, {"error": "invalid_lease_id"}
        if not isinstance(reason, str) or not reason.strip():
            return 400, {"error": "invalid_reason"}
        with self._condition:
            degraded = self._expire_due()
            lease = self._active
            if lease is None or lease.lease_id != lease_id:
                return 404, {"error": "lease_not_found"}
            if (
                self._grant_inflight is not None
                and self._grant_inflight.lease is lease
            ):
                return 409, {"error": "session_granting"}
            if not self._write_audit_locked(
                "admin_release",
                client=lease.client,
                reason=reason.strip(),
                duration_s=0.0,
                lease_id=lease.lease_id,
                decision="confirmed",
            ):
                return 503, {"error": "audit_failed"}
            if self._active is not lease:
                return 409, {"error": "lease_invalid"}
            cleanup, release_degraded = self._release_active_locked("admin_release")
            degraded.extend(release_degraded)
            if self._active is lease:
                return 503, {
                    "error": "audit_failed",
                    "cleanup_degraded": self._unique(degraded),
                }
            if cleanup.get("error") == "lifecycle_cleanup_pending":
                return 202, {
                    "error": "lifecycle_cleanup_pending",
                    "released": False,
                    "lease_id": lease_id,
                    "cleanup_degraded": self._unique(degraded),
                }
            return 200, {
                "released": True,
                "lease_id": lease_id,
                "cleanup": cleanup,
                "cleanup_degraded": self._unique(degraded),
            }

    def finish_operation(self, owner_session_id: str, command_id: int) -> None:
        with self._condition:
            self._expire_due()
            if self._active is None or self._active.client.session_id != owner_session_id:
                return
            removed = self._active.operation_pins.pop(command_id, None)
            if removed is not None:
                self._bump_revision_locked()
            self._condition.notify_all()
            self._expire_due()

    def note_command(
        self,
        owner_session_id: str,
        command_id: int,
        command: str,
        args: object,
    ) -> None:
        with self._condition:
            self._expire_due()
            lease = self._active
            if lease is None or lease.client.session_id != owner_session_id:
                return

            authorization_index = next(
                (
                    index
                    for index, pending in enumerate(lease.pending_authorizations)
                    if pending.command == command
                ),
                None,
            )
            if authorization_index is None:
                return
            pending = lease.pending_authorizations.pop(authorization_index)
            if pending.deadline is not None and pending.deadline > self._time_fn():
                lease.operation_pins[command_id] = pending.deadline

            if (
                command == "vehicle_trace"
                and isinstance(args, dict)
                and args.get("mode") == "start"
            ):
                lease.vehicle_active = True
            elif command == "vehicle_control" and self._has_nonzero_control(args):
                lease.vehicle_active = True
            self._bump_revision_locked()

    def status(self, client: ClientIdentity) -> dict:
        with self._condition:
            degraded = self._expire_due()
            provisional = (
                self._grant_inflight.lease
                if self._grant_inflight is not None
                and self._active is self._grant_inflight.lease
                else None
            )
            visible_active = self._active if self._active is not provisional else None
            owner = (
                self._public_lease_locked(visible_active)
                if visible_active is not None
                else None
            )
            if owner is None and self._releasing is not None:
                owner = self._public_lease_locked(self._releasing)
                owner["state"] = "releasing"

            queue = []
            self_payload: dict[str, object] = {"state": "none", "position": None}
            for position, ticket in enumerate(self._queue, start=1):
                item = {
                    "ticket": ticket.ticket_id,
                    "purpose": ticket.purpose,
                    "position": position,
                    "client": ticket.client.public_payload(),
                    "age_s": max(0.0, self._time_fn() - ticket.created_at),
                }
                queue.append(item)
                if ticket.client == client:
                    self_payload = {
                        "state": "queued",
                        "ticket": ticket.ticket_id,
                        "position": position,
                    }

            if (
                self._grant_inflight is not None
                and self._grant_inflight.lease.client == client
            ):
                self_payload = {
                    "state": "queued",
                    "lease_id": self._grant_inflight.lease.lease_id,
                    "position": 1,
                }
                if self._grant_inflight.ticket is not None:
                    self_payload["ticket"] = self._grant_inflight.ticket.ticket_id
            elif visible_active is not None and visible_active.client == client:
                self_payload = {
                    "state": "active",
                    "lease_id": visible_active.lease_id,
                    "position": 0,
                }
            elif self._releasing is not None and self._releasing.client == client:
                self_payload = {
                    "state": "releasing",
                    "lease_id": self._releasing.lease_id,
                    "position": 0,
                }
            return {
                "owner": owner,
                "queue": queue,
                "self": self_payload,
                "claimable": self._claimable_locked(),
                "audit_fault": copy.deepcopy(self._audit_fault),
                "lifecycle_recovery_fault": copy.deepcopy(
                    self._lifecycle_recovery_fault
                ),
                "operation_tombstones": self._operation_tombstone_status_locked(),
                "cleanup_degraded": self._unique(degraded),
            }

    def box_queue_public(self) -> list[dict[str, object]]:
        with self._condition:
            self._purge_box_locked()
            now = self._time_fn()
            return [
                {
                    "session": ticket.client.public_payload()["session"],
                    "waiting_s": max(0.0, now - ticket.created_at),
                }
                for ticket in self._box_queue
            ]

    def box_is_claimed(self) -> bool:
        with self._condition:
            self._purge_box_locked()
            return self._box_claiming is not None

    def box_blocks_start(self, client: ClientIdentity) -> bool:
        with self._condition:
            self._purge_box_locked()
            return (
                self._box_claiming is not None
                and self._box_claiming != client.session_id
            )

    def box_claim_public(self) -> dict[str, object]:
        with self._condition:
            self._purge_box_locked()
            if self._box_claiming is None:
                return {"claimed": False, "claimed_s": None}
            now = self._time_fn()
            started = self._box_claimed_at
            claimed_s = (
                max(0.0, now - started) if isinstance(started, (int, float)) else 0.0
            )
            return {"claimed": True, "claimed_s": claimed_s}

    def box_wait_touch(
        self,
        client: ClientIdentity,
        ticket_id: object = None,
        *,
        done: bool = False,
        claim: bool = False,
    ) -> dict[str, object]:
        """Join, refresh, claim, or leave the box FIFO.

        Same TTL, capacity and tombstone constants as the lease queue. Tickets
        are request-bound and are not written into the durable snapshot.
        """

        with self._condition:
            self._purge_box_locked()
            if done:
                self._box_leave_locked(client, ticket_id)
                return {"box_ticket": None}
            existing = self._box_ticket_for_client_locked(client)
            supplied = ticket_id if isinstance(ticket_id, str) and ticket_id else None
            if supplied is not None and self._box_tombstoned_locked(
                client.session_id, supplied
            ):
                return {"box_ticket": None, "box_wait_error": "box_wait_cancelled"}
            if existing is None:
                if supplied is not None:
                    return {"box_ticket": None, "box_wait_error": "box_ticket_invalid"}
                if len(self._box_tombstones) >= MAX_OPERATION_TOMBSTONES:
                    return {
                        "box_ticket": None,
                        "box_wait_error": "box_queue_saturated",
                        "hint": "retry with wait_for_box_s=<n>",
                    }
                if len(self._box_queue) >= MAX_SESSION_QUEUE:
                    return {"box_ticket": None, "box_wait_error": "queue_full"}
                now = self._time_fn()
                ticket = _Ticket(
                    self._id_fn(),
                    client,
                    "box_wait",
                    now,
                    now,
                )
                self._box_queue.append(ticket)
                self._bump_revision_locked()
                self._condition.notify_all()
                existing = ticket
            else:
                existing.touched_at = self._time_fn()
            if claim and self._box_queue and self._box_queue[0] is existing:
                if self._box_claiming != client.session_id:
                    self._box_claimed_at = self._time_fn()
                self._box_claiming = client.session_id
            return {
                "box_ticket": existing.ticket_id,
                "box_position": self._box_queue.index(existing) + 1,
                "box_claimed": self._box_claiming == client.session_id,
            }

    def durable_revision(self) -> int:
        with self._condition:
            return self._revision

    def note_run_reaped(self, owner_session_id: str | None) -> None:
        with self._condition:
            before = self._revision
            if isinstance(owner_session_id, str) and owner_session_id:
                self._box_leave_session_locked(owner_session_id)
            self._expire_due()
            if self._revision == before:
                self._bump_revision_locked()
            self._condition.notify_all()
        try:
            self._audit(
                {
                    "event": "run_reaped_wake",
                    "owner_session": owner_session_id,
                    "decision": "woke",
                }
            )
        except Exception:
            pass

    def snapshot_payload(self) -> dict[str, object]:
        with self._condition:
            return self._snapshot_payload_locked()

    def arm_lifecycle_recovery_fault(
        self, fault: dict[str, object]
    ) -> None:
        if not isinstance(fault, dict) or self._lifecycle_fault_id(fault) is None:
            raise ValueError("invalid_lifecycle_recovery_fault")
        with self._condition:
            if self._lifecycle_recovery_fault is not None:
                if self._lifecycle_recovery_fault == fault:
                    return
                raise RuntimeError("lifecycle_recovery_fault_exists")
            self._lifecycle_recovery_fault = copy.deepcopy(fault)
            self._bump_revision_locked()
            self._condition.notify_all()

    def clear_lifecycle_recovery_fault(self, fault_id: str) -> bool:
        with self._condition:
            current = self._lifecycle_recovery_fault
            if current is None or self._lifecycle_fault_id(current) != fault_id:
                return False
            self._lifecycle_recovery_fault = None
            self._bump_revision_locked()
            self._condition.notify_all()
            return True

    def complete_lifecycle_recovery_fault(self, fault_id: str) -> bool:
        """Atomically retire a repaired lifecycle fault and its live release fence."""

        with self._condition:
            current = self._lifecycle_recovery_fault
            if current is None or self._lifecycle_fault_id(current) != fault_id:
                return False
            lease = self._releasing
            previous = copy.deepcopy(current)
            self._lifecycle_recovery_fault = None
            if lease is None:
                self._bump_revision_locked()
                if not self._persist_snapshot_locked():
                    self._lifecycle_recovery_fault = previous
                    self._bump_revision_locked()
                    self._condition.notify_all()
                    return False
                self._condition.notify_all()
                return True

            marker_reason = (
                self._wal_marker.get("reason")
                if isinstance(self._wal_marker, dict)
                else None
            )
            audit_reason = (
                marker_reason if isinstance(marker_reason, str) and marker_reason else "owner_release"
            )
            started_event = (
                "session_expired"
                if audit_reason == "lease_ttl"
                else "session_release_started"
            )
            self._releasing = None
            self._handoff_pending = True
            self._bump_revision_locked()
            expired_tickets = self._remove_expired_tickets_locked(None)
            self._condition.notify_all()
            audit_ok, audit_worker_started = self._write_release_audits_bounded_locked(
                lease=lease,
                started_event=started_event,
                audit_reason=audit_reason,
                cleanup_timed_out=True,
                cleanup_duration_s=0.0,
                cleanup_summary={},
                cleanup_degraded=["lifecycle_recovery_repaired"],
                expired_tickets=expired_tickets,
                wait_s=RELEASE_AUDIT_TIMEOUT_S,
            )
            if not audit_ok and not audit_worker_started:
                self._handoff_audit_failed = True
                self._latch_wal_fault_locked("audit_failed", "audit_failed")
                return False
            return True

    @staticmethod
    def _lifecycle_fault_id(fault: dict[str, object]) -> str | None:
        direct = fault.get("fault_id")
        if isinstance(direct, str) and direct:
            return direct
        header = fault.get("fault")
        nested = header.get("fault_id") if isinstance(header, dict) else None
        return nested if isinstance(nested, str) and nested else None

    def expire_due(self) -> tuple[str, ...]:
        """Run expiry side effects before callers enter their own state lock."""

        with self._condition:
            return tuple(self._expire_due())

    def expected_repair_snapshot_revision(self, fault_id: str) -> int | None:
        with self._condition:
            if (
                self._audit_fault is None
                or self._audit_fault.get("fault_id") != fault_id
            ):
                return None
            return self._revision + 1

    def finalize_audit_repair(
        self,
        marker: dict[str, object],
        marker_sha256: str,
    ) -> bool:
        with self._condition:
            fault_id = marker.get("fault_id")
            if (
                marker.get("state") != "repaired"
                or not isinstance(fault_id, str)
                or self._audit_fault is None
                or self._audit_fault.get("fault_id") != fault_id
            ):
                return False
            previous_fault = copy.deepcopy(self._audit_fault)
            previous_handoff = self._handoff_pending
            previous_handoff_failed = self._handoff_audit_failed
            previous_grant_failed = self._grant_audit_failed
            self._wal_marker = copy.deepcopy(marker)
            self._wal_sha256 = marker_sha256
            self._repair_fence = True
            self._audit_fault = None
            self._handoff_pending = False
            self._handoff_audit_failed = False
            self._grant_audit_failed = False
            self._bump_revision_locked()
            if not self._persist_snapshot_locked():
                self._audit_fault = previous_fault
                self._handoff_pending = previous_handoff
                self._handoff_audit_failed = previous_handoff_failed
                self._grant_audit_failed = previous_grant_failed
                self._repair_fence = False
                self._bump_revision_locked()
                self._condition.notify_all()
                return False
            if not self._clear_wal_locked():
                self._audit_fault = copy.deepcopy(marker)
                if marker.get("operation") == "release":
                    self._handoff_audit_failed = True
                else:
                    self._grant_audit_failed = True
                self._repair_fence = False
                self._bump_revision_locked()
                self._persist_snapshot_locked()
                self._condition.notify_all()
                return False
            self._repair_fence = False
            self._condition.notify_all()
            return True

    def _expire_due(self, protect_ticket_id: str | None = None) -> list[str]:
        self._purge_operation_tombstones_locked()
        self._purge_box_locked()
        degraded = self._cancel_expired_tickets_locked(protect_ticket_id)
        if (
            self._active is not None
            and self._time_fn() >= self._active.effective_expiry()
        ):
            _, release_degraded = self._release_active_locked("lease_expired")
            degraded.extend(release_degraded)
        if self._handoff_audit_failed or self._grant_audit_failed:
            degraded.append("audit_failed")
        return self._unique(degraded)

    def _cancel_expired_tickets_locked(self, protect_ticket_id: str | None) -> list[str]:
        degraded: list[str] = []
        expired = self._remove_expired_tickets_locked(protect_ticket_id)
        for ticket in expired:
            if not self._write_audit_locked(
                "ticket_cancelled",
                client=ticket.client,
                reason="ticket_ttl",
                duration_s=0.0,
                ticket=ticket.ticket_id,
                decision="ticket_expired",
            ):
                degraded.append("audit_failed")
        return self._unique(degraded)

    def _remove_expired_tickets_locked(
        self, protect_ticket_id: str | None
    ) -> list[_Ticket]:
        now = self._time_fn()
        removed: list[_Ticket] = []
        for ticket in list(self._queue):
            if (
                ticket.ticket_id != protect_ticket_id
                and not (
                    self._grant_inflight is not None
                    and self._grant_inflight.ticket is ticket
                )
                and now - ticket.touched_at >= SESSION_TTL_S
            ):
                self._queue.remove(ticket)
                removed.append(ticket)
        if removed:
            self._bump_revision_locked()
            self._condition.notify_all()
        return removed

    def _release_active_locked(self, reason: str) -> tuple[dict[str, object], list[str]]:
        lease = self._active
        if lease is None:
            return {}, []
        if not self._arm_wal_locked(
            operation="release",
            lease=lease,
            ticket=None,
            reason="lease_ttl" if reason == "lease_expired" else reason,
        ):
            return {}, ["audit_failed", "wal_arm_failed"]
        self._active = None
        self._releasing = lease
        self._bump_revision_locked()
        self._remember_invalid_token_locked(
            lease, "lease_expired" if reason == "lease_expired" else "lease_invalid"
        )
        degraded: list[str] = []
        audit_reason = "lease_ttl" if reason == "lease_expired" else reason
        started_event = "session_expired" if reason == "lease_expired" else "session_release_started"
        self._condition.notify_all()

        cleanup_result: dict[str, object] = {}
        cleanup_started_at = time.monotonic()
        cleanup_done = threading.Event()
        cleanup_box: dict[str, object] = {}
        cleanup_timed_out = False

        def invoke_cleanup() -> None:
            try:
                cleanup_box["result"] = self._cleanup(
                    lease.client.session_id,
                    lease.lease_id,
                    reason,
                    lease.vehicle_active,
                )
            except Exception as exc:
                cleanup_box["error"] = exc
            finally:
                with self._condition:
                    self._cleanup_worker_active -= 1
                    self._cleanup_worker_slots.release()
                    self._condition.notify_all()
                cleanup_done.set()

        cleanup_finished = False
        cleanup_slot = self._cleanup_worker_slots.acquire(blocking=False)
        if not cleanup_slot:
            self._cleanup_worker_saturated += 1
            degraded.append("cleanup_worker_saturated")
        else:
            self._cleanup_worker_active += 1
            cleanup_thread = threading.Thread(
                target=invoke_cleanup,
                name=f"dayz-mcp-cleanup-{lease.lease_id[:12]}",
                daemon=True,
            )
            start_error: Exception | None = None
            self._condition.release()
            try:
                try:
                    cleanup_thread.start()
                except Exception as exc:
                    start_error = exc
                else:
                    cleanup_finished = cleanup_done.wait(self._cleanup_timeout_s)
            finally:
                self._condition.acquire()
            if start_error is not None:
                cleanup_box["error"] = start_error
                cleanup_finished = True
                self._cleanup_worker_active -= 1
                self._cleanup_worker_slots.release()

        if cleanup_slot and not cleanup_finished:
            cleanup_timed_out = True
            degraded.append("cleanup_timeout")
        elif "error" in cleanup_box:
            degraded.append("cleanup_failed")
        else:
            raw_result = cleanup_box.get("result")
            if isinstance(raw_result, CleanupDisposition):
                if not raw_result.fence_required:
                    cleanup_result = dict(raw_result.terminal_result)
                elif not raw_result.terminal_event.wait(
                    max(
                        0.0,
                        self._cleanup_timeout_s
                        - (time.monotonic() - cleanup_started_at),
                    )
                ):
                    cleanup_timed_out = True
                    self._watch_fenced_cleanup_locked(
                        lease=lease,
                        disposition=raw_result,
                        started_event=started_event,
                        audit_reason=audit_reason,
                        cleanup_started_at=cleanup_started_at,
                    )
                    return {"error": "lifecycle_cleanup_pending"}, self._unique(
                        degraded
                    )
                elif raw_result.terminal_result.get("terminal_safe") is not True:
                    return {"error": "lifecycle_cleanup_pending"}, self._unique(
                        [*degraded, "lifecycle_cleanup_ambiguous"]
                    )
                else:
                    cleanup_result = dict(raw_result.terminal_result)
            elif isinstance(raw_result, dict):
                cleanup_result = dict(raw_result)
            raw_degraded = cleanup_result.pop("cleanup_degraded", [])
            if isinstance(raw_degraded, list):
                degraded.extend(str(item) for item in raw_degraded)

        cleanup_duration_s = max(0.0, time.monotonic() - cleanup_started_at)
        cleanup_summary = {
            key: cleanup_result[key]
            for key in ("cancelled", "vehicle_release_enqueued", "runs_released")
            if key in cleanup_result
            and isinstance(cleanup_result[key], (int, list))
            and not isinstance(cleanup_result[key], bool)
        }

        # Close the exact RELEASING fence before terminal audit I/O. A handoff
        # marker prevents a concurrent status/acquire from auditing the FIFO head
        # out of causal order while the bounded release ledger batch runs.
        self._releasing = None
        self._handoff_pending = True
        self._bump_revision_locked()
        expired_tickets = self._remove_expired_tickets_locked(None)
        self._condition.notify_all()

        durable_degraded = self._unique(degraded)
        audit_wait_s = max(
            0.0,
            min(
                RELEASE_AUDIT_TIMEOUT_S,
                self._cleanup_timeout_s - cleanup_duration_s,
            ),
        )
        audit_ok, audit_worker_started = self._write_release_audits_bounded_locked(
            lease=lease,
            started_event=started_event,
            audit_reason=audit_reason,
            cleanup_timed_out=cleanup_timed_out,
            cleanup_duration_s=cleanup_duration_s,
            cleanup_summary=cleanup_summary,
            cleanup_degraded=durable_degraded,
            expired_tickets=expired_tickets,
            wait_s=audit_wait_s,
        )
        if not audit_ok:
            degraded.append("audit_failed")
            if not audit_worker_started:
                self._handoff_audit_failed = True
        self._condition.notify_all()
        return cleanup_result, self._unique(degraded)

    def _watch_fenced_cleanup_locked(
        self,
        *,
        lease: _Lease,
        disposition: CleanupDisposition,
        started_event: str,
        audit_reason: str,
        cleanup_started_at: float,
    ) -> None:
        def finish() -> None:
            disposition.terminal_event.wait()
            with self._condition:
                if self._releasing is not lease:
                    return
                result = dict(disposition.terminal_result)
                if result.get("terminal_safe") is not True:
                    self._condition.notify_all()
                    return
                degraded = result.pop("cleanup_degraded", [])
                if not isinstance(degraded, list):
                    degraded = []
                cleanup_duration_s = max(
                    0.0, time.monotonic() - cleanup_started_at
                )
                cleanup_summary = {
                    key: result[key]
                    for key in ("cancelled", "vehicle_release_enqueued", "runs_released")
                    if key in result
                    and isinstance(result[key], (int, list))
                    and not isinstance(result[key], bool)
                }
                self._releasing = None
                self._handoff_pending = True
                self._bump_revision_locked()
                expired_tickets = self._remove_expired_tickets_locked(None)
                self._condition.notify_all()
                audit_ok, audit_worker_started = (
                    self._write_release_audits_bounded_locked(
                        lease=lease,
                        started_event=started_event,
                        audit_reason=audit_reason,
                        cleanup_timed_out=True,
                        cleanup_duration_s=cleanup_duration_s,
                        cleanup_summary=cleanup_summary,
                        cleanup_degraded=self._unique(
                            [str(item) for item in degraded]
                        ),
                        expired_tickets=expired_tickets,
                        wait_s=RELEASE_AUDIT_TIMEOUT_S,
                    )
                )
                if not audit_ok and not audit_worker_started:
                    self._handoff_audit_failed = True
                self._condition.notify_all()

        threading.Thread(
            target=finish,
            name=f"dayz-mcp-fenced-cleanup-{lease.lease_id[:12]}",
            daemon=True,
        ).start()

    def _write_release_audits_bounded_locked(
        self,
        *,
        lease: _Lease,
        started_event: str,
        audit_reason: str,
        cleanup_timed_out: bool,
        cleanup_duration_s: float,
        cleanup_summary: dict[str, object],
        cleanup_degraded: list[str],
        expired_tickets: list[_Ticket],
        wait_s: float,
    ) -> tuple[bool, bool]:
        if not self._release_audit_worker_slots.acquire(blocking=False):
            return False, False

        done = threading.Event()
        result: dict[str, bool] = {}

        def write(
            event_name: str,
            *,
            event_client: ClientIdentity | None = None,
            event_reason: str | None = None,
            **details: object,
        ) -> bool:
            event: dict[str, object] = {
                "event": event_name,
                "timestamp_monotonic": self._time_fn(),
                "client": (event_client or lease.client).public_payload(),
                "reason": event_reason or audit_reason,
                "duration_s": details.pop("duration_s", 0.0),
            }
            event.update(details)
            try:
                return self._audit(event) is not False
            except Exception:
                return False

        def invoke() -> None:
            try:
                with self._audit_gate:
                    ok = True
                    for ticket in expired_tickets:
                        ok = write(
                            "ticket_cancelled",
                            event_client=ticket.client,
                            event_reason="ticket_ttl",
                            ticket=ticket.ticket_id,
                            decision="ticket_expired",
                        ) and ok
                    ok = write(
                        started_event,
                        lease_id=lease.lease_id,
                        decision="invalidated",
                    ) and ok
                    if cleanup_timed_out:
                        ok = write(
                            "session_cleanup_timeout",
                            lease_id=lease.lease_id,
                            duration_s=cleanup_duration_s,
                            decision="cleanup_timeout",
                        ) and ok
                    ok = write(
                        "session_release_finished",
                        lease_id=lease.lease_id,
                        duration_s=cleanup_duration_s,
                        decision="released",
                        cleanup=cleanup_summary,
                        cleanup_degraded=cleanup_degraded,
                    ) and ok
                result["ok"] = ok
            finally:
                self._release_audit_worker_slots.release()
                with self._condition:
                    if result.get("ok") is True:
                        wal_outcome = self._finish_wal_after_publish_locked()
                        if wal_outcome == "ok":
                            self._clear_handoff_pending_locked()
                            if not self._persist_snapshot_locked():
                                self._handoff_audit_failed = True
                        elif wal_outcome != "clear_pending":
                            self._latch_wal_fault_locked(
                                wal_outcome, wal_outcome
                            )
                    else:
                        if self._fault_arm is None:
                            self._handoff_audit_failed = True
                        else:
                            self._latch_wal_fault_locked(
                                "audit_failed", "audit_failed"
                            )
                    self._condition.notify_all()
                done.set()

        worker = threading.Thread(
            target=invoke,
            name=f"dayz-mcp-release-audit-{lease.lease_id[:12]}",
            daemon=True,
        )
        if wait_s == 0.0:
            wait_s = min(0.005, self._cleanup_timeout_s)
        start_error = False
        self._condition.release()
        try:
            try:
                worker.start()
            except Exception:
                start_error = True
            else:
                done.wait(wait_s)
        finally:
            self._condition.acquire()
        if start_error:
            self._release_audit_worker_slots.release()
            return False, False
        return done.is_set() and result.get("ok") is True, True

    def _claim_head_from_wait_locked(
        self, ticket: _Ticket
    ) -> tuple[str, dict[str, object] | None]:
        lease = self._new_lease_locked(
            ticket.client,
            ticket.purpose,
            source_ticket_id=ticket.ticket_id,
            source_operation_id=ticket.operation_id,
        )
        grant = _GrantInFlight(lease, ticket)
        self._grant_inflight = grant
        self._bump_revision_locked()
        self._condition.notify_all()

        def clear_grant() -> None:
            if self._grant_inflight is grant:
                self._grant_inflight = None
                self._bump_revision_locked()
                self._condition.notify_all()

        def state_is_exact() -> bool:
            return (
                self._grant_inflight is grant
                and self._active is None
                and self._releasing is None
                and not self._handoff_pending
                and not self._grant_audit_failed
                and not self._operation_tombstoned_locked(
                    ticket.client, ticket.operation_id
                )
                and bool(self._queue)
                and self._queue[0] is ticket
            )

        def event(
            event_name: str, *, reason: str, decision: str
        ) -> dict[str, object]:
            payload = {
                "event": event_name,
                "timestamp_monotonic": self._time_fn(),
                "client": ticket.client.public_payload(),
                "reason": reason,
                "duration_s": 0.0,
                "lease_id": lease.lease_id,
                "ticket": ticket.ticket_id,
                "purpose": ticket.purpose,
                "source": "live_wait",
                "decision": decision,
            }
            if ticket.operation_id is not None:
                payload["operation_id"] = ticket.operation_id
            return payload

        def write_with_gate(audit_event: dict[str, object]) -> bool:
            self._condition.release()
            try:
                try:
                    return self._audit(audit_event) is not False
                except Exception:
                    return False
            finally:
                self._condition.acquire()

        self._condition.release()
        try:
            try:
                gate_acquired = self._audit_gate.acquire(blocking=False)
                gate_failed = False
            except Exception:
                gate_acquired = False
                gate_failed = True
        finally:
            self._condition.acquire()
        if not gate_acquired:
            clear_grant()
            return ("audit_failed" if gate_failed else "busy"), None

        try:
            if not self._arm_wal_locked(
                operation="grant",
                lease=lease,
                ticket=ticket,
                reason="fifo_head",
            ):
                clear_grant()
                return "audit_failed", None
            prepared_ok = write_with_gate(
                event(
                    "session_grant_prepared",
                    reason="fifo_head",
                    decision="prepared",
                )
            )
            if not prepared_ok:
                clear_grant()
                self._latch_wal_fault_locked("audit_failed", "audit_failed")
                return "audit_failed", None
            if not state_is_exact():
                cancelled = self._operation_tombstoned_locked(
                    ticket.client, ticket.operation_id
                )
                compensated = self._compensate_provisional_grant_locked(
                    grant,
                    reason=(
                        "operation_cancelled" if cancelled else "coordination_changed"
                    ),
                    write_revocation=lambda: True,
                    requeue_ticket=not cancelled,
                )
                if not compensated:
                    return "audit_failed", None
                return (
                    "operation_cancelled" if cancelled else "coordination_changed"
                ), None

            committed = write_with_gate(
                event(
                    "session_granted",
                    reason="fifo_head",
                    decision="allowed",
                )
            )
            if not committed:
                revoked = write_with_gate(
                    event(
                        "session_grant_revoked",
                        reason="audit_failed",
                        decision="revoked",
                    )
                )
                if not revoked:
                    failure = "compensation_failed"
                else:
                    failure = "audit_failed"
                clear_grant()
                self._latch_wal_fault_locked(failure, failure)
                return "audit_failed", None

            if not state_is_exact():
                cancelled = self._operation_tombstoned_locked(
                    ticket.client, ticket.operation_id
                )
                revoke_reason = (
                    "operation_cancelled" if cancelled else "coordination_changed"
                )
                compensated = self._compensate_provisional_grant_locked(
                    grant,
                    reason=revoke_reason,
                    write_revocation=lambda: write_with_gate(
                        event(
                            "session_grant_revoked",
                            reason=revoke_reason,
                            decision="revoked",
                        )
                    ),
                    requeue_ticket=not cancelled,
                )
                if not compensated:
                    return "audit_failed", None
                return (
                    "operation_cancelled" if cancelled else "coordination_changed"
                ), None

            self._queue.pop(0)
            granted_at = self._time_fn()
            lease.granted_at = granted_at
            lease.expires_at = granted_at + SESSION_TTL_S
            self._active = lease
            self._bump_revision_locked()
            wal_outcome = self._finish_wal_after_publish_locked()
            if wal_outcome == "coordination_changed":
                cancelled = self._operation_tombstoned_locked(
                    ticket.client, ticket.operation_id
                )
                revoke_reason = (
                    "operation_cancelled" if cancelled else "coordination_changed"
                )
                compensated = self._compensate_provisional_grant_locked(
                    grant,
                    reason=revoke_reason,
                    write_revocation=lambda: write_with_gate(
                        event(
                            "session_grant_revoked",
                            reason=revoke_reason,
                            decision="revoked",
                        )
                    ),
                    requeue_ticket=not cancelled,
                )
                if not compensated:
                    return "audit_failed", None
                return (
                    "operation_cancelled" if cancelled else "coordination_changed"
                ), None
            if wal_outcome != "ok":
                if wal_outcome != "clear_pending":
                    revoked = write_with_gate(
                        event(
                            "session_grant_revoked",
                            reason=wal_outcome,
                            decision="revoked",
                        )
                    )
                    if self._active is lease:
                        self._active = None
                        self._remember_invalid_token_locked(lease, "lease_invalid")
                        if ticket not in self._queue:
                            self._queue.insert(0, ticket)
                        if self._grant_inflight is grant:
                            self._grant_inflight = None
                        self._bump_revision_locked()
                        self._condition.notify_all()
                    failure = wal_outcome if revoked else "compensation_failed"
                    self._latch_wal_fault_locked(failure, failure)
                return "audit_failed", None
            if (
                self._grant_inflight is not grant
                or self._active is not lease
                or self._releasing is not None
                or self._audit_fault is not None
                or self._repair_fence
                or self._wal_marker is not None
            ):
                compensated = self._compensate_provisional_grant_locked(
                    grant,
                    reason="coordination_changed",
                    write_revocation=lambda: write_with_gate(
                        event(
                            "session_grant_revoked",
                            reason="coordination_changed",
                            decision="revoked",
                        )
                    ),
                    requeue_ticket=True,
                )
                if not compensated:
                    return "audit_failed", None
                return "coordination_changed", None
            if self._operation_tombstoned_locked(
                ticket.client, ticket.operation_id
            ):
                compensated = self._compensate_provisional_grant_locked(
                    grant,
                    reason="operation_cancelled",
                    write_revocation=lambda: write_with_gate(
                        event(
                            "session_grant_revoked",
                            reason="operation_cancelled",
                            decision="revoked",
                        )
                    ),
                    requeue_ticket=False,
                )
                if not compensated:
                    return "audit_failed", None
                return "operation_cancelled", None
            self._grant_inflight = None
            self._bump_revision_locked()
            self._condition.notify_all()
            return "granted", self._active_payload_locked(lease)
        finally:
            self._audit_gate.release()

    def _new_lease_locked(
        self,
        client: ClientIdentity,
        purpose: str,
        *,
        source_ticket_id: str | None,
        source_operation_id: str | None = None,
    ) -> _Lease:
        now = self._time_fn()
        token = self._token_fn()
        lease_id = self._id_fn()
        return _Lease(
            client,
            purpose,
            token,
            lease_id,
            now,
            now + SESSION_TTL_S,
            source_ticket_id=source_ticket_id,
            source_operation_id=source_operation_id,
        )

    def _identity_collision_locked(self, client: ClientIdentity) -> bool:
        identities = [
            ticket.client for ticket in (*self._queue, *self._queue_reservations)
        ]
        if self._active is not None:
            identities.append(self._active.client)
        if self._releasing is not None:
            identities.append(self._releasing.client)
        if self._grant_inflight is not None:
            identities.append(self._grant_inflight.lease.client)
        return any(
            existing.session_id == client.session_id and existing != client
            for existing in identities
        )

    @staticmethod
    def _valid_operation_id(operation_id: object) -> bool:
        return (
            isinstance(operation_id, str)
            and bool(operation_id)
            and len(operation_id) <= 200
            and all(ord(character) >= 32 for character in operation_id)
        )

    def _admission_privileged_locked(self, client: ClientIdentity) -> bool:
        if self._active is not None and self._active.client == client:
            return True
        if self._box_claiming == client.session_id:
            return True
        return False

    def _oldest_tombstone_retry_after_locked(self) -> float:
        if not self._operation_tombstones:
            return 0.0
        oldest_expires_at = min(self._operation_tombstones.values())
        remaining = float(oldest_expires_at) - float(self._time_fn())
        if remaining < 0.0:
            return 0.0
        if remaining > OPERATION_TOMBSTONE_TTL_S:
            return float(OPERATION_TOMBSTONE_TTL_S)
        return remaining

    def _tombstone_saturated_error_locked(self) -> dict:
        retry_after_s = self._oldest_tombstone_retry_after_locked()
        return {
            "error": "operation_tombstones_saturated",
            "count": len(self._operation_tombstones),
            "capacity": MAX_OPERATION_TOMBSTONES,
            "retry_after_s": retry_after_s,
            "hint": (
                f"wait {retry_after_s:.0f}s then retry session_acquire_wait; "
                "do not spin"
            ),
        }

    def _purge_operation_tombstones_locked(self) -> None:
        now = self._time_fn()
        expired = [
            key
            for key, expires_at in self._operation_tombstones.items()
            if expires_at <= now
        ]
        for key in expired:
            self._operation_tombstones.pop(key, None)
        if expired:
            self._bump_revision_locked()
            self._condition.notify_all()

    def _purge_box_locked(self) -> None:
        now = self._time_fn()
        changed = False
        expired_tombstones = [
            key
            for key, expires_at in self._box_tombstones.items()
            if expires_at <= now
        ]
        for key in expired_tombstones:
            self._box_tombstones.pop(key, None)
            changed = True
        kept: list[_Ticket] = []
        for ticket in self._box_queue:
            claimed = self._box_claiming == ticket.client.session_id
            ttl = BOX_CLAIM_TTL_S if claimed else SESSION_TTL_S
            if now - ticket.touched_at >= ttl:
                self._box_tombstones[(ticket.client.session_id, ticket.ticket_id)] = (
                    now + OPERATION_TOMBSTONE_TTL_S
                )
                if claimed:
                    self._box_claiming = None
                    self._box_claimed_at = None
                changed = True
                continue
            kept.append(ticket)
        if len(kept) != len(self._box_queue):
            self._box_queue = kept
            changed = True
        if changed:
            self._bump_revision_locked()
            self._condition.notify_all()

    def _box_tombstoned_locked(self, session_id: str, ticket_id: str) -> bool:
        return (session_id, ticket_id) in self._box_tombstones

    def _box_ticket_for_client_locked(self, client: ClientIdentity) -> _Ticket | None:
        for ticket in self._box_queue:
            if ticket.client.session_id == client.session_id:
                return ticket
        return None

    def _box_leave_session_locked(self, session_id: str) -> None:
        now = self._time_fn()
        remaining: list[_Ticket] = []
        removed = False
        for ticket in self._box_queue:
            if ticket.client.session_id == session_id:
                self._box_tombstones[(ticket.client.session_id, ticket.ticket_id)] = (
                    now + OPERATION_TOMBSTONE_TTL_S
                )
                removed = True
                continue
            remaining.append(ticket)
        if self._box_claiming == session_id:
            self._box_claiming = None
            self._box_claimed_at = None
            removed = True
        if removed:
            self._box_queue = remaining
            self._bump_revision_locked()
            self._condition.notify_all()

    def _box_leave_locked(self, client: ClientIdentity, ticket_id: object) -> None:
        if ticket_id in (None, ""):
            self._box_leave_session_locked(client.session_id)
            return
        now = self._time_fn()
        remaining: list[_Ticket] = []
        removed = False
        for ticket in self._box_queue:
            match_ticket = (
                isinstance(ticket_id, str)
                and ticket_id
                and ticket.ticket_id == ticket_id
                and ticket.client.session_id == client.session_id
            )
            if match_ticket:
                self._box_tombstones[(ticket.client.session_id, ticket.ticket_id)] = (
                    now + OPERATION_TOMBSTONE_TTL_S
                )
                removed = True
                continue
            remaining.append(ticket)
        if self._box_claiming == client.session_id:
            self._box_claiming = None
            self._box_claimed_at = None
            removed = True
        if removed:
            self._box_queue = remaining
            self._bump_revision_locked()
            self._condition.notify_all()

    def _operation_tombstoned_locked(
        self, client: ClientIdentity, operation_id: str | None
    ) -> bool:
        return operation_id is not None and (
            client, operation_id
        ) in self._operation_tombstones

    def _operation_state_locked(
        self, client: ClientIdentity, operation_id: str
    ) -> tuple[int, dict] | None:
        inflight = self._grant_inflight
        if (
            inflight is not None
            and inflight.lease.client == client
            and inflight.lease.source_operation_id == operation_id
        ):
            if inflight.ticket is None:
                return 409, {"error": "session_granting"}
            return 202, self._ticket_payload_locked(inflight.ticket, 1)
        if (
            self._active is not None
            and self._active.client == client
            and self._active.source_operation_id == operation_id
        ):
            return 200, self._active_payload_locked(self._active)
        for position, ticket in enumerate(self._queue, start=1):
            if ticket.client == client and ticket.operation_id == operation_id:
                return 202, self._ticket_payload_locked(ticket, position)
        for index, ticket in enumerate(self._queue_reservations, start=1):
            if ticket.client == client and ticket.operation_id == operation_id:
                return 202, self._ticket_payload_locked(
                    ticket, len(self._queue) + index
                )
        return None

    def _client_has_other_operation_locked(
        self, client: ClientIdentity, operation_id: str
    ) -> bool:
        operation_ids: list[str | None] = [
            ticket.operation_id
            for ticket in (*self._queue, *self._queue_reservations)
            if ticket.client == client
        ]
        for lease in (self._active, self._releasing):
            if lease is not None and lease.client == client:
                operation_ids.append(lease.source_operation_id)
        if self._grant_inflight is not None and self._grant_inflight.lease.client == client:
            operation_ids.append(self._grant_inflight.lease.source_operation_id)
        return any(value != operation_id for value in operation_ids)

    def _operation_tombstone_status_locked(
        self, *, purge: bool = True
    ) -> dict[str, object]:
        if purge:
            self._purge_operation_tombstones_locked()
        count = len(self._operation_tombstones)
        return {
            "count": count,
            "capacity": MAX_OPERATION_TOMBSTONES,
            "saturated": count >= MAX_OPERATION_TOMBSTONES,
        }

    def _queue_capacity_locked(self) -> int:
        return len(self._queue) + len(self._queue_reservations)

    def _queued_grant_audit_failed_locked(self, degraded: list[str]) -> bool:
        if self._audit_fault is not None:
            return True
        if self._grant_audit_failed:
            return (
                self._active is None
                and self._releasing is None
            )
        if self._handoff_audit_failed:
            return (
                self._active is None
                and self._releasing is None
                and bool(self._queue)
            )
        return (
            "audit_failed" in degraded
            and self._active is None
            and self._releasing is None
            and self._grant_inflight is None
            and not self._handoff_pending
            and bool(self._queue)
        )

    def _clear_handoff_pending_locked(self) -> None:
        if self._handoff_pending:
            self._handoff_pending = False
            self._bump_revision_locked()
            self._condition.notify_all()

    def _claimable_locked(self) -> bool:
        return not (
            self._audit_fault is not None
            or self._lifecycle_recovery_fault is not None
            or self._repair_fence
            or self._handoff_pending
            or self._handoff_audit_failed
            or self._grant_audit_failed
            or self._releasing is not None
            or self._grant_inflight is not None
            or self._wal_marker is not None
        )

    def _wal_finish_fence_locked(
        self, operation: str | None = None
    ) -> tuple[object, ...]:
        """Capture all observable coordination state across WAL I/O callbacks."""

        if operation is None and self._wal_marker is not None:
            operation = str(self._wal_marker.get("operation"))
        if operation == "release":
            # A release WAL owns only the retiring lease. Queue liveness and
            # cancellation remain valid while its filesystem transitions run;
            # the terminal snapshots capture their latest public state.
            return (
                "release",
                id(self._active),
                id(self._releasing),
                id(self._grant_inflight),
                self._handoff_pending,
                self._handoff_audit_failed,
                self._grant_audit_failed,
                self._repair_fence,
                copy.deepcopy(self._audit_fault),
            )
        return (
            "grant",
            self._revision,
            id(self._active),
            id(self._releasing),
            id(self._grant_inflight),
            tuple(id(ticket) for ticket in self._queue),
            tuple(id(ticket) for ticket in self._queue_reservations),
            self._handoff_pending,
            self._handoff_audit_failed,
            self._grant_audit_failed,
            self._repair_fence,
            copy.deepcopy(self._audit_fault),
            tuple(
                sorted(
                    (
                        client.platform,
                        client.pid,
                        client.ppid,
                        client.started_at_utc,
                        client.session_id,
                        operation_id,
                        expires_at,
                    )
                    for (
                        client,
                        operation_id,
                    ), expires_at in self._operation_tombstones.items()
                )
            ),
        )

    def _wal_finish_fence_matches_locked(self, fence: tuple[object, ...]) -> bool:
        operation = str(fence[0]) if fence else None
        return self._wal_finish_fence_locked(operation) == fence

    def _snapshot_payload_locked(
        self, *, persist_provisional_grant: bool = False
    ) -> dict[str, object]:
        self._purge_operation_tombstones_locked()
        provisional = (
            self._grant_inflight.lease
            if self._grant_inflight is not None
            and self._active is self._grant_inflight.lease
            else None
        )
        snapshot_active = self._active
        snapshot_granting = (
            self._grant_inflight.lease if self._grant_inflight is not None else None
        )
        if provisional is not None:
            if persist_provisional_grant:
                snapshot_granting = None
            else:
                snapshot_active = None
        return {
            "revision": self._revision,
            "captured_at_monotonic": self._time_fn(),
            "active": self._snapshot_lease_locked(snapshot_active),
            "releasing": self._snapshot_lease_locked(self._releasing),
            "granting": self._snapshot_lease_locked(snapshot_granting),
            "handoff_pending": self._handoff_pending,
            "claimable": self._claimable_locked(),
            "audit_fault": copy.deepcopy(self._audit_fault),
            "operation_tombstones": self._operation_tombstone_status_locked(
                purge=False
            ),
            "queue": [self._snapshot_ticket_locked(ticket) for ticket in self._queue],
            "cleanup_workers": {
                "capacity": MAX_CLEANUP_WORKERS,
                "active": self._cleanup_worker_active,
                "saturated": self._cleanup_worker_saturated,
            },
        }

    def _arm_wal_locked(
        self,
        *,
        operation: str,
        lease: _Lease,
        ticket: _Ticket | None,
        reason: str,
    ) -> bool:
        if self._fault_arm is None:
            return True
        if self._wal_marker is not None or self._audit_fault is not None:
            return False
        marker: dict[str, object] = {
            "format_version": 1,
            "fault_id": self._fault_id_fn(),
            "daemon_generation": self._daemon_generation,
            "state": "armed",
            "operation": operation,
            "phase": "armed",
            "lease_id": lease.lease_id,
            "ticket_id": ticket.ticket_id if ticket is not None else None,
            "client": lease.client.public_payload(),
            "reason": reason,
            "armed_at_utc": self._utc_now_fn(),
            "failure": None,
            "expected_snapshot_revision": self._revision + 1,
            "repair_phase": "none",
        }
        self._condition.release()
        try:
            try:
                sha256 = self._fault_arm(copy.deepcopy(marker))
            except Exception:
                return False
        finally:
            self._condition.acquire()
        if not isinstance(sha256, str) or not sha256:
            return False
        self._wal_marker = marker
        self._wal_sha256 = sha256
        return True

    def _transition_wal_locked(
        self,
        *,
        state: str,
        phase: str,
        failure: str | None = None,
        repair_phase: str | None = None,
        expected_snapshot_revision: int | None = None,
    ) -> bool:
        if self._fault_transition is None:
            return True
        marker, sha256 = self._wal_marker, self._wal_sha256
        if marker is None or sha256 is None:
            return False
        kwargs: dict[str, object] = {"state": state, "phase": phase}
        if failure is not None:
            kwargs["failure"] = failure
        if repair_phase is not None:
            kwargs["repair_phase"] = repair_phase
        if expected_snapshot_revision is not None:
            kwargs["expected_snapshot_revision"] = expected_snapshot_revision
        self._condition.release()
        try:
            try:
                next_sha = self._fault_transition(
                    str(marker["fault_id"]), sha256, **kwargs
                )
            except Exception:
                return False
        finally:
            self._condition.acquire()
        if not isinstance(next_sha, str) or not next_sha:
            return False
        marker = dict(marker)
        marker.update(kwargs)
        self._wal_marker = marker
        self._wal_sha256 = next_sha
        return True

    def _persist_snapshot_locked(self) -> bool:
        if self._persist_snapshot is None:
            return True
        snapshot = self._snapshot_payload_locked(persist_provisional_grant=True)
        self._condition.release()
        try:
            try:
                return self._persist_snapshot(snapshot) is not False
            except Exception:
                return False
        finally:
            self._condition.acquire()

    def _clear_wal_locked(self) -> bool:
        if self._fault_clear is None:
            self._wal_marker = None
            self._wal_sha256 = None
            return True
        marker, sha256 = self._wal_marker, self._wal_sha256
        if marker is None or sha256 is None:
            return False
        self._condition.release()
        try:
            try:
                cleared = self._fault_clear(str(marker["fault_id"]), sha256) is True
            except Exception:
                cleared = False
        finally:
            self._condition.acquire()
        if cleared:
            self._wal_marker = None
            self._wal_sha256 = None
        return cleared

    def _latch_wal_fault_locked(self, failure: str, phase: str) -> None:
        if self._fault_arm is None:
            if failure == "compensation_failed":
                self._grant_audit_failed = True
                self._bump_revision_locked()
                self._condition.notify_all()
            return
        transitioned = self._transition_wal_locked(
            state="fault", phase=phase, failure=failure
        )
        marker = copy.deepcopy(self._wal_marker)
        if marker is None:
            marker = {
                "fault_id": "wal-unavailable",
                "operation": "grant",
                "phase": phase,
                "failure": failure,
            }
        elif not transitioned:
            marker["state"] = "fault"
            marker["phase"] = "terminal_transition_failed"
            marker["failure"] = "terminal_transition_failed"
        self._audit_fault = marker
        if marker.get("operation") == "release":
            self._handoff_audit_failed = True
        else:
            self._grant_audit_failed = True
        self._bump_revision_locked()
        self._persist_snapshot_locked()
        self._condition.notify_all()

    def _compensate_provisional_grant_locked(
        self,
        grant: _GrantInFlight,
        *,
        reason: str,
        write_revocation: Callable[[], bool],
        requeue_ticket: bool,
    ) -> bool:
        """Revoke an audited grant without ever exposing its provisional token."""

        lease = grant.lease
        compensation_armed = True
        if self._fault_arm is not None and self._wal_marker is None:
            compensation_armed = self._arm_wal_locked(
                operation="grant",
                lease=lease,
                ticket=grant.ticket,
                reason=reason,
            )
        compensation_marked = compensation_armed
        if self._fault_arm is not None and self._wal_marker is not None:
            compensation_marked = self._transition_wal_locked(
                state=str(self._wal_marker["state"]),
                phase="coordination_changed",
                failure="coordination_changed",
            )

        revoked = write_revocation()
        changed = False
        if self._active is lease:
            self._active = None
            self._remember_invalid_token_locked(lease, "lease_invalid")
            changed = True
        if self._grant_inflight is grant:
            self._grant_inflight = None
            changed = True
        ticket = grant.ticket
        if (
            requeue_ticket
            and ticket is not None
            and ticket not in self._queue
            and not self._operation_tombstoned_locked(
                ticket.client, ticket.operation_id
            )
        ):
            self._queue.insert(0, ticket)
            changed = True
        if changed:
            self._bump_revision_locked()
            self._condition.notify_all()

        if not compensation_marked or not revoked:
            failure = "compensation_failed"
            self._latch_wal_fault_locked(failure, failure)
            return False
        wal_outcome = self._finish_wal_after_publish_locked()
        if wal_outcome != "ok":
            failure = (
                "terminal_transition_failed"
                if wal_outcome == "clear_pending"
                else wal_outcome
            )
            self._latch_wal_fault_locked(failure, failure)
            return False
        return True

    def _finish_wal_after_publish_locked(self) -> str:
        if self._fault_arm is None:
            return "ok"
        self._purge_operation_tombstones_locked()
        finish_fence = self._wal_finish_fence_locked()
        marker = self._wal_marker
        if marker is None or not self._transition_wal_locked(
            state=str(marker["state"]),
            phase=str(marker["phase"]),
            expected_snapshot_revision=self._revision,
        ):
            return "terminal_transition_failed"
        if not self._wal_finish_fence_matches_locked(finish_fence):
            return "coordination_changed"
        if not self._persist_snapshot_locked():
            return "snapshot_failed"
        if not self._wal_finish_fence_matches_locked(finish_fence):
            return "coordination_changed"
        if not self._transition_wal_locked(
            state="completed", phase="snapshot_persisted"
        ):
            return "terminal_transition_failed"
        if not self._wal_finish_fence_matches_locked(finish_fence):
            return "coordination_changed"
        cleared = self._clear_wal_locked()
        if not self._wal_finish_fence_matches_locked(finish_fence):
            return "coordination_changed"
        if not cleared:
            self._audit_fault = copy.deepcopy(self._wal_marker)
            self._grant_audit_failed = True
            self._bump_revision_locked()
            self._persist_snapshot_locked()
            self._condition.notify_all()
            return "clear_pending"
        return "ok"

    def _audit_fault_error_locked(self) -> dict[str, object]:
        payload: dict[str, object] = {"error": "coordination_audit_fault"}
        fault_id = self._audit_fault.get("fault_id") if self._audit_fault else None
        if isinstance(fault_id, str):
            payload["fault_id"] = fault_id
        return payload

    def _find_ticket_locked(self, ticket_id: str) -> _Ticket | None:
        for ticket in self._queue:
            if ticket.ticket_id == ticket_id:
                return ticket
        if (
            self._grant_inflight is not None
            and self._grant_inflight.ticket is not None
            and self._grant_inflight.ticket.ticket_id == ticket_id
        ):
            return self._grant_inflight.ticket
        return None

    def _wait_active_payload_locked(
        self, client: ClientIdentity, ticket_id: str
    ) -> dict[str, object] | None:
        if (
            self._active is not None
            and not (
                self._grant_inflight is not None
                and self._grant_inflight.lease is self._active
            )
            and self._active.client == client
            and self._active.source_ticket_id == ticket_id
        ):
            return self._active_payload_locked(self._active)
        return None

    def _validate_token_locked(
        self, client: ClientIdentity, lease_token: str | None
    ) -> tuple[_Lease | None, str]:
        if lease_token is None:
            return None, "lease_required"
        inflight = self._grant_inflight
        if (
            inflight is not None
            and inflight.lease.lease_token == lease_token
            and inflight.lease.client == client
        ):
            return None, "session_granting"
        if (
            self._audit_fault is not None
            or self._repair_fence
            or self._wal_marker is not None
        ):
            return None, "lease_invalid"
        if (
            self._active is not None
            and self._active.lease_token == lease_token
            and self._active.client == client
        ):
            return self._active, ""
        invalid = self._invalid_tokens.get(lease_token)
        if invalid is not None and invalid[0] == client:
            return None, invalid[1]
        return None, "lease_invalid"

    def _exact_lease_locked(
        self, owner_session_id: str, lease_id: str
    ) -> _Lease | None:
        lease = self._active
        if (
            lease is not None
            and lease.client.session_id == owner_session_id
            and lease.lease_id == lease_id
        ):
            return lease
        lease = self._releasing
        if (
            lease is not None
            and lease.client.session_id == owner_session_id
            and lease.lease_id == lease_id
        ):
            return lease
        return None

    def _remember_invalid_token_locked(self, lease: _Lease, reason: str) -> None:
        self._invalid_tokens[lease.lease_token] = (lease.client, reason)
        while len(self._invalid_tokens) > MAX_SESSION_QUEUE * 2:
            self._invalid_tokens.pop(next(iter(self._invalid_tokens)))

    def _write_initial_grant_audits_locked(self, lease: _Lease) -> str:
        events = (
            {
                "event": "session_acquire",
                "timestamp_monotonic": self._time_fn(),
                "client": lease.client.public_payload(),
                "reason": "request",
                "duration_s": 0.0,
                "lease_id": lease.lease_id,
                "decision": "grant",
            },
            {
                "event": "session_granted",
                "timestamp_monotonic": self._time_fn(),
                "client": lease.client.public_payload(),
                "reason": "request",
                "duration_s": 0.0,
                "lease_id": lease.lease_id,
                "purpose": lease.purpose,
                "decision": "allowed",
            },
        )
        if lease.source_operation_id is not None:
            for event in events:
                event["operation_id"] = lease.source_operation_id
        self._condition.release()
        try:
            if not self._audit_gate.acquire(blocking=False):
                return "busy"
            try:
                self._condition.acquire()
                try:
                    armed = self._arm_wal_locked(
                        operation="grant",
                        lease=lease,
                        ticket=None,
                        reason="request",
                    )
                finally:
                    self._condition.release()
                if not armed:
                    return "failed"
                for event in events:
                    try:
                        if self._audit(event) is False:
                            raise OSError("audit_failed")
                    except Exception:
                        revoked = True
                        if event.get("event") == "session_granted":
                            try:
                                revoked = self._audit(
                                    {
                                        "event": "session_grant_revoked",
                                        "timestamp_monotonic": self._time_fn(),
                                        "client": lease.client.public_payload(),
                                        "reason": "audit_failed",
                                        "duration_s": 0.0,
                                        "lease_id": lease.lease_id,
                                        "decision": "revoked",
                                    }
                                ) is not False
                            except Exception:
                                revoked = False
                        self._condition.acquire()
                        try:
                            failure = "audit_failed" if revoked else "compensation_failed"
                            self._latch_wal_fault_locked(failure, failure)
                        finally:
                            self._condition.release()
                        return "failed"
                return "ok"
            finally:
                self._audit_gate.release()
        finally:
            self._condition.acquire()

    def _write_audit_locked(
        self,
        event_name: str,
        *,
        client: ClientIdentity,
        reason: str,
        duration_s: float,
        blocking: bool = True,
        **details: object,
    ) -> bool:
        return self._write_audit_outcome_locked(
            event_name,
            client=client,
            reason=reason,
            duration_s=duration_s,
            blocking=blocking,
            **details,
        ) == "ok"

    def _write_audit_outcome_locked(
        self,
        event_name: str,
        *,
        client: ClientIdentity,
        reason: str,
        duration_s: float,
        blocking: bool = True,
        **details: object,
    ) -> str:
        event = {
            "event": event_name,
            "timestamp_monotonic": self._time_fn(),
            "client": client.public_payload(),
            "reason": reason,
            "duration_s": duration_s,
        }
        event.update(details)
        # Durable audit may fsync/replace a multi-megabyte JSONL. Never retain the
        # coordination condition while external I/O runs; every authorization that
        # gates a mutation revalidates its exact lease after this call returns.
        self._condition.release()
        try:
            acquired = self._audit_gate.acquire(blocking=blocking)
            if not acquired:
                return "busy"
            try:
                try:
                    return "ok" if self._audit(event) is not False else "failed"
                except Exception:
                    return "failed"
            finally:
                self._audit_gate.release()
        finally:
            self._condition.acquire()

    def _write_wait_audit_outcome_locked(
        self,
        event_name: str,
        *,
        client: ClientIdentity,
        reason: str,
        wait_started_at: float,
        blocking: bool = True,
        **details: object,
    ) -> str:
        duration_s = max(0.0, time.monotonic() - wait_started_at)
        return self._write_audit_outcome_locked(
            event_name,
            client=client,
            reason=reason,
            duration_s=duration_s,
            blocking=blocking,
            **details,
        )

    def _write_wait_audit_locked(
        self,
        event_name: str,
        *,
        client: ClientIdentity,
        reason: str,
        wait_started_at: float,
        blocking: bool = True,
        **details: object,
    ) -> bool:
        return self._write_wait_audit_outcome_locked(
            event_name,
            client=client,
            reason=reason,
            wait_started_at=wait_started_at,
            blocking=blocking,
            **details,
        ) == "ok"

    def _bump_revision_locked(self) -> None:
        self._revision += 1

    def _active_payload_locked(self, lease: _Lease) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": "active",
            "lease_token": lease.lease_token,
            "lease_id": lease.lease_id,
            "expires_in_s": max(0.0, lease.effective_expiry() - self._time_fn()),
        }
        if lease.source_ticket_id is not None:
            payload["ticket"] = lease.source_ticket_id
        if lease.source_operation_id is not None:
            payload["operation_id"] = lease.source_operation_id
        return payload

    def _ticket_payload_locked(
        self, ticket: _Ticket, position: int
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": "queued",
            "ticket": ticket.ticket_id,
            "position": position,
            "expires_in_s": max(
                0.0, ticket.touched_at + SESSION_TTL_S - self._time_fn()
            ),
        }
        if ticket.operation_id is not None:
            payload["operation_id"] = ticket.operation_id
        return payload

    def _public_lease_locked(self, lease: _Lease) -> dict[str, object]:
        return {
            "state": "active",
            "lease_id": lease.lease_id,
            "purpose": lease.purpose,
            "client": lease.client.public_payload(),
            "expires_in_s": max(0.0, lease.effective_expiry() - self._time_fn()),
        }

    @staticmethod
    def _snapshot_lease_locked(lease: _Lease | None) -> dict[str, object] | None:
        if lease is None:
            return None
        payload: dict[str, object] = {
            "lease_id": lease.lease_id,
            "session": lease.client.session_id[:12],
            "granted_at_monotonic": lease.granted_at,
            "expires_at_monotonic": lease.effective_expiry(),
        }
        if lease.source_ticket_id is not None:
            payload["ticket"] = lease.source_ticket_id
        if lease.source_operation_id is not None:
            payload["operation_id"] = lease.source_operation_id
        return payload

    @staticmethod
    def _snapshot_ticket_locked(ticket: _Ticket) -> dict[str, object]:
        payload: dict[str, object] = {
            "ticket": ticket.ticket_id,
            "session": ticket.client.session_id[:12],
            "created_at_monotonic": ticket.created_at,
            "touched_at_monotonic": ticket.touched_at,
        }
        if ticket.operation_id is not None:
            payload["operation_id"] = ticket.operation_id
        return payload

    @staticmethod
    def _token_error_status(error: str) -> int:
        if error == "lease_required":
            return 423
        if error in {"lease_expired", "session_granting"}:
            return 409
        return 403

    def _token_error_tuple(
        self, error: str, degraded: list[str] | tuple[str, ...] = ()
    ) -> tuple[int, dict]:
        return self._token_error_status(error), self._payload_with_degradation(
            {"error": error}, degraded
        )

    def _payload_with_degradation(
        self,
        payload: dict[str, object],
        degraded: list[str] | tuple[str, ...],
    ) -> dict[str, object]:
        if not degraded:
            return payload
        result = dict(payload)
        result["cleanup_degraded"] = self._unique(list(degraded))
        return result

    @staticmethod
    def _bounded_operation_timeout(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        seconds = float(value)
        if not math.isfinite(seconds):
            return 0.0
        return max(0.0, min(seconds, MAX_OPERATION_PIN_S))

    @staticmethod
    def _has_nonzero_control(args: object) -> bool:
        if not isinstance(args, dict):
            return False
        return any(
            isinstance(value, (int, float)) and value != 0
            for value in args.values()
        )

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))
