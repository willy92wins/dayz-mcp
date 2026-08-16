#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import hmac
import json
import math
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from dayz_mcp import daemon_credential, orphan_guard
from dayz_mcp.core import BLOCKED_VERSION_STATES
from dayz_mcp.session_coordination import (
    ClientIdentity,
    SessionCoordinator,
    command_requires_lease,
)


SERVER_COMMANDS = {
    "query_player_state",
    "query_all_players",
    "world_spawn",
    "object_delete",
    "notify_players",
    "vehicle_enter",
    "vehicle_drive",
    "scene_raycast",
    "telemetry_read",
    "query_get_in_condition",
    "world_time_set",
    "world_weather_set",
    "vehicle_prepare_fixture",
    # F3: server-side world/object/player verbs (MissionServer bridge).
    "surface_query",
    "player_teleport",
    "object_anim",
    "inventory_give",
    "object_inspect",
}
CLIENT_COMMANDS = {
    "camera_set",
    "camera_get",
    "restore_gameplay",
    "drive_probe_client",
    "vehicle_get_in_client",
    "engine_set",
    "vehicle_control",
    "vehicle_telemetry",
    "vehicle_trace",
    "vehicle_release",
}

CREDENTIAL_RECOVERY_TTL_S = 300.0
CREDENTIAL_RECOVERY_COUNT_MAX = 2_147_483_647
EXEC_COMMANDS = {"exec_enforce"}
WHITELISTED_COMMANDS = SERVER_COMMANDS | CLIENT_COMMANDS
VALID_PEERS = {"server", "client"}
SESSION_ROUTES = {
    "/session/acquire": "acquire",
    "/session/enqueue": "enqueue",
    "/session/wait": "wait",
    "/session/cancel": "cancel",
    "/session/cancel-operation": "cancel-operation",
    "/session/heartbeat": "heartbeat",
    "/session/release": "release",
    "/session/status": "status",
}
LIFECYCLE_ROUTES = {
    "/lifecycle/start": "start",
    "/lifecycle/ack": "ack",
    "/lifecycle/stop": "stop",
    "/lifecycle/adopt": "adopt",
    "/lifecycle/reap": "reap",
    "/lifecycle/status": "status",
}
ADMIN_ROUTES = {
    "/admin/release": "release",
    "/admin/reconcile": "reconcile",
    "/admin/audit-repair": "audit-repair",
    "/admin/lifecycle-recovery-repair": "lifecycle-recovery-repair",
}
MAX_QUEUE = 64
# Stale-command hygiene (BUG-041): a client that crashed/relaunched must not
# inherit commands queued for the previous session. record_poll drops commands
# older than COMMAND_TTL_S, and flushes the peer's whole queue when the gap since
# its last poll exceeds PEER_RECONNECT_GAP_S (the previous game/session is gone).
COMMAND_TTL_S = 30.0
PEER_RECONNECT_GAP_S = 10.0

LogSink = Callable[[str], None]
VersionValidator = Callable[[str | None], str]
ExecAudit = Callable[[str, str, str, int | None], None]


def peer_for_command(cmd: str) -> str:
    if cmd in CLIENT_COMMANDS:
        return "client"
    return "server"


def _default_log_sink(message: str) -> None:
    print(message, flush=True)


def _is_real_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _safe_operation_timeout(value: object) -> tuple[float, bool]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0, False
    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        return 0.0, False
    if not math.isfinite(seconds):
        return 0.0, False
    return seconds, True


def validate_command_args(cmd: str, args: dict) -> tuple[bool, str | None]:
    if cmd == "restore_gameplay":
        if args:
            return False, "bad_args"
        return True, None

    if cmd == "vehicle_trace":
        if set(args) != {
            "mode",
            "trace_id",
            "cursor",
            "limit",
            "sample_hz",
            "max_samples",
        }:
            return False, "bad_args"
        mode = args.get("mode")
        trace_id = args.get("trace_id")
        cursor = args.get("cursor")
        limit = args.get("limit")
        sample_hz = args.get("sample_hz")
        max_samples = args.get("max_samples")
        if mode not in {"start", "status", "stop", "read", "clear"}:
            return False, "bad_args"
        if (
            not isinstance(trace_id, str)
            or len(trace_id) != 32
            or any(character not in "0123456789abcdef" for character in trace_id)
        ):
            return False, "bad_args"
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            return False, "bad_args"
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 64
        ):
            return False, "bad_args"
        if (
            not isinstance(sample_hz, int)
            or isinstance(sample_hz, bool)
            or sample_hz < 20
            or sample_hz > 60
        ):
            return False, "bad_args"
        if (
            not isinstance(max_samples, int)
            or isinstance(max_samples, bool)
            or max_samples < 2
            or max_samples > 8192
        ):
            return False, "bad_args"
        return True, None

    if cmd == "vehicle_prepare_fixture":
        # F3.2: any CarScript classname is accepted; non-vehicles fail in the
        # bridge with fixture_not_vehicle after CarScript.Cast.
        if set(args) != {"mode", "type", "pos", "radius"}:
            return False, "bad_args"
        if args.get("mode") != "object_at":
            return False, "bad_args"
        type_name = args.get("type")
        if not isinstance(type_name, str) or type_name == "":
            return False, "bad_args"
        pos = args.get("pos")
        radius = args.get("radius")
        if not isinstance(pos, list) or len(pos) != 3:
            return False, "bad_args"
        try:
            if any(not _is_real_number(component) for component in pos):
                return False, "bad_args"
            if not _is_real_number(radius) or float(radius) <= 0.0:
                return False, "bad_args"
        except (OverflowError, ValueError):
            return False, "bad_args"
        return True, None

    if cmd == "surface_query":
        # Fail closed: exact keys, finite x/z. World bounds are enforced in the
        # bridge (needs GetWorldSize); non-finite is rejected here so the
        # consumer never enqueues NaN/Inf.
        if set(args) != {"x", "z"}:
            return False, "bad_args"
        try:
            if not _is_real_number(args.get("x")) or not _is_real_number(args.get("z")):
                return False, "bad_args"
        except (OverflowError, ValueError):
            return False, "bad_args"
        return True, None

    if cmd == "player_teleport":
        if set(args) != {"pos"}:
            return False, "bad_args"
        pos = args.get("pos")
        if not isinstance(pos, list) or len(pos) != 3:
            return False, "bad_args"
        try:
            if any(not _is_real_number(component) for component in pos):
                return False, "bad_args"
        except (OverflowError, ValueError):
            return False, "bad_args"
        return True, None

    if cmd == "object_anim":
        # phase optional: absent = read, present = SetAnimationPhase.
        keys = set(args)
        if keys != {"type", "pos", "source"} and keys != {"type", "pos", "source", "phase"}:
            return False, "bad_args"
        type_name = args.get("type")
        source = args.get("source")
        if not isinstance(type_name, str) or type_name == "":
            return False, "bad_args"
        if not isinstance(source, str) or source == "":
            return False, "bad_args"
        pos = args.get("pos")
        if not isinstance(pos, list) or len(pos) != 3:
            return False, "bad_args"
        try:
            if any(not _is_real_number(component) for component in pos):
                return False, "bad_args"
            if "phase" in args and not _is_real_number(args.get("phase")):
                return False, "bad_args"
        except (OverflowError, ValueError):
            return False, "bad_args"
        return True, None

    if cmd == "inventory_give":
        if set(args) != {"classname", "dest"}:
            return False, "bad_args"
        classname = args.get("classname")
        dest = args.get("dest")
        if not isinstance(classname, str) or classname == "":
            return False, "bad_args"
        if dest not in {"hands", "inventory"}:
            return False, "bad_args"
        return True, None

    if cmd == "object_inspect":
        if set(args) != {"type", "pos", "want"}:
            return False, "bad_args"
        type_name = args.get("type")
        want = args.get("want")
        if not isinstance(type_name, str) or type_name == "":
            return False, "bad_args"
        if not isinstance(want, list) or len(want) == 0:
            return False, "bad_args"
        if any(not isinstance(item, str) or item == "" for item in want):
            return False, "bad_args"
        pos = args.get("pos")
        if not isinstance(pos, list) or len(pos) != 3:
            return False, "bad_args"
        try:
            if any(not _is_real_number(component) for component in pos):
                return False, "bad_args"
        except (OverflowError, ValueError):
            return False, "bad_args"
        return True, None

    if cmd == "object_delete":
        object_id = args.get("object_id")
        if not isinstance(object_id, int) or isinstance(object_id, bool):
            return False, "bad_args"
        if object_id <= 0:
            return False, "bad_args"
        return True, None

    if cmd == "notify_players":
        if not _is_real_number(args.get("show_time")) or float(args["show_time"]) <= 0.0:
            return False, "bad_args"
        title = args.get("title")
        if not isinstance(title, str) or title == "":
            return False, "bad_args"
        detail = args.get("detail", "")
        icon = args.get("icon", "")
        if not isinstance(detail, str) or not isinstance(icon, str):
            return False, "bad_args"
        return True, None

    return True, None


class ServerState:
    def __init__(
        self,
        key: str,
        enable_exec_enforce: bool = False,
        version_validator: VersionValidator | None = None,
        exec_allowlist: set[str] | None = None,
        exec_audit: ExecAudit | None = None,
        time_fn: Callable[[], float] | None = None,
        coordination: SessionCoordinator | None = None,
    ) -> None:
        self.key = key
        self.enable_exec_enforce = enable_exec_enforce
        self.version_validator = version_validator
        self.exec_allowlist = set(exec_allowlist) if exec_allowlist is not None else None
        self.exec_audit = exec_audit
        self.coordination = coordination
        self.coordination_store: object | None = None
        self.coordination_fault_store: object | None = None
        self.lifecycle_recovery_fault_store: object | None = None
        self.audit_writer: object | None = None
        self.lifecycle: object | None = None
        self.retail_probe: Callable[[], dict[str, object]] | None = None
        self.daemon_generation: str | None = None
        self._lock = threading.RLock()
        self._next_id = 1
        self._queues: dict[str, list[dict]] = {"server": [], "client": []}
        self._exec_capacity_reserved: dict[str, int] = {"server": 0, "client": 0}
        self._enqueue_generation = 0
        self._stopping = False
        self._results: dict[int, dict] = {}
        self._enqueued_at: dict[int, float] = {}
        self._operation_deadlines: dict[int, float] = {}
        self._command_owner: dict[int, tuple[ClientIdentity, str]] = {}
        self._fire_and_forget_ids: set[int] = set()
        self._audit_degraded_count = 0
        self._poll_delay_ms = 0
        self._last_poll_at: dict[str, float | None] = {"server": None, "client": None}
        self._poll_versions: dict[str, str | None] = {"server": None, "client": None}
        # Last session-driven (client) HTTP request; feeds the daemon idle watchdog
        # so a broker daemon with no game and no client traffic can reap itself.
        self._last_client_request_at: float | None = None
        self._credential_recovery_count = 0
        self._last_credential_recovery_at: float | None = None
        # Injectable monotonic clock. Defaults to time.monotonic; tests inject a
        # fake so the BUG-041 TTL / reconnect-flush logic is deterministic.
        self._time_fn = time_fn or time.monotonic

    def _now(self) -> float:
        return self._time_fn()

    def touch_client(self) -> None:
        """Mark session-side (client) HTTP activity for the daemon idle watchdog."""
        with self._lock:
            self._last_client_request_at = self._now()

    def record_credential_recovery(self) -> None:
        with self._lock:
            self._credential_recovery_count = min(
                CREDENTIAL_RECOVERY_COUNT_MAX,
                self._credential_recovery_count + 1,
            )
            self._last_credential_recovery_at = self._now()

    def credential_recovery_snapshot(
        self,
        now: float | None = None,
    ) -> dict[str, object]:
        if now is None:
            now = self._now()
        with self._lock:
            count = self._credential_recovery_count
            recovered_at = self._last_credential_recovery_at
        age = (
            None
            if recovered_at is None
            else max(0.0, float(now) - recovered_at)
        )
        recent = age is not None and age <= CREDENTIAL_RECOVERY_TTL_S
        return {
            "recovered_count": count,
            "recent": recent,
            "last_recovered_age_s": age if recent else None,
        }

    def enqueue_command(
        self,
        cmd: str,
        args: dict,
        peer: str | None = None,
        *,
        identity_payload: object = None,
        lease_token: str | None = None,
        operation_timeout_s: float = 0.0,
        internal: bool = False,
    ) -> tuple[int, dict]:
        safe_operation_timeout_s, timeout_valid = _safe_operation_timeout(
            operation_timeout_s
        )
        # Timeout validation stays after authorize to preserve lease-first ordering.
        if self.coordination is None or internal:
            return self._enqueue_command(
                cmd,
                args,
                peer,
                owner_client=None,
                owner_lease_id=None,
                operation_timeout_s=safe_operation_timeout_s,
            )

        try:
            client = ClientIdentity.from_payload(identity_payload)
        except ValueError:
            return 400, {"error": "invalid_identity"}
        safe_cmd = cmd if isinstance(cmd, str) else ""
        safe_peer = peer if peer is None or isinstance(peer, str) else ""
        if lease_token is not None and not isinstance(lease_token, str):
            return 403, {"error": "lease_invalid"}
        safe_lease_token = lease_token

        # Authorization may fsync the durable audit. It deliberately happens before
        # taking the queue lock. Exact commit is the only authority linearization
        # point and runs under the queue lock immediately before publication.
        decision = self.coordination.authorize(
            client, safe_lease_token, safe_cmd, safe_operation_timeout_s
        )
        if not decision.allowed:
            payload: dict[str, object] = {"error": decision.error}
            if decision.cleanup_degraded:
                payload["cleanup_degraded"] = list(decision.cleanup_degraded)
            return decision.http_status, payload

        if command_requires_lease(safe_cmd) and self._retail_quarantined():
            owner_session_id = decision.owner_session_id
            if (
                owner_session_id is None
                or decision.lease_id is None
                or decision.reservation_id is None
            ):
                return 409, {"error": "retail_quarantine"}
            rejected = self.coordination.reject_reservation(
                owner_session_id,
                decision.lease_id,
                decision.reservation_id,
                "retail_quarantine",
            )
            rejected_payload: dict[str, object] = {"error": rejected.error}
            if rejected.cleanup_degraded:
                rejected_payload["cleanup_degraded"] = list(
                    rejected.cleanup_degraded
                )
            return rejected.http_status, rejected_payload

        payload = {}
        cleanup_degraded = list(decision.cleanup_degraded)
        reservation_owner = (
            (
                decision.owner_session_id,
                decision.lease_id,
                decision.reservation_id,
            )
            if decision.owner_session_id is not None
            and decision.lease_id is not None
            and decision.reservation_id is not None
            and command_requires_lease(safe_cmd)
            else None
        )
        command_accepted = False
        abort_reason = "enqueue_exception"

        def commit(command_id: int) -> bool:
            if reservation_owner is None:
                return True
            return self.coordination.commit_authorization(
                reservation_owner[0],
                reservation_owner[1],
                reservation_owner[2],
                command_id,
                safe_cmd,
                args,
            )

        try:
            if not timeout_valid:
                status, payload = 400, {"error": "bad_operation_timeout"}
            else:
                # Expiry may audit and run cleanup. Keep every side effect outside
                # the queue lock; exact commit below is memory-only and revalidates
                # the lease immediately before publication.
                cleanup_degraded.extend(self.coordination.expire_due())
                status, payload = self._enqueue_command(
                    safe_cmd,
                    args,
                    safe_peer,
                    owner_client=(
                        client if decision.owner_session_id is not None else None
                    ),
                    owner_lease_id=decision.lease_id,
                    operation_timeout_s=safe_operation_timeout_s,
                    commit=commit if reservation_owner is not None else None,
                )
            if status != 200:
                error = payload.get("error")
                if isinstance(error, str):
                    abort_reason = error
                return status, payload
            command_accepted = True
            return status, payload
        finally:
            if reservation_owner is not None and not command_accepted:
                cleanup_degraded.extend(
                    self.coordination.abort_reservation(
                        reservation_owner[0],
                        reservation_owner[1],
                        reservation_owner[2],
                        abort_reason,
                    )
                )
            if cleanup_degraded:
                existing_degradation = payload.get("cleanup_degraded", [])
                payload["cleanup_degraded"] = list(
                    dict.fromkeys([*existing_degradation, *cleanup_degraded])
                )

    def _enqueue_command(
        self,
        cmd: str,
        args: dict,
        peer: str | None,
        *,
        owner_client: ClientIdentity | None,
        owner_lease_id: str | None,
        operation_timeout_s: float = 0.0,
        commit: Callable[[int], bool] | None = None,
    ) -> tuple[int, dict]:
        if cmd not in self.whitelisted_commands():
            return 400, {"error": "not_whitelisted"}
        if not isinstance(args, dict):
            return 400, {"error": "bad_args"}
        args_ok, args_error = validate_command_args(cmd, args)
        if not args_ok:
            return 400, {"error": args_error or "bad_args"}

        command_peer = peer_for_command(cmd)
        if peer is None:
            peer = command_peer
        if peer not in VALID_PEERS or peer != command_peer:
            return 400, {"error": "bad_peer"}

        # Version gate at the enqueue ingress (daemon authority, d): reject before
        # queueing when the target peer is on a blocked version. Active only when a
        # validator is wired (MCP/daemon path); the bare harness shim passes none,
        # so its back-compat behavior is unchanged. record_poll keeps the delivery
        # gate independently.
        if self.version_validator is not None:
            with self._lock:
                poll_version = self._poll_versions.get(peer)
            state = self.version_validator(poll_version)
            if state in BLOCKED_VERSION_STATES:
                return 409, {"error": "version_blocked", "state": state}

        if cmd == "exec_enforce":
            return self._enqueue_exec_enforce(
                args,
                peer,
                owner_client,
                owner_lease_id,
                operation_timeout_s,
                commit,
            )

        with self._lock:
            if self._stopping:
                return 409, {"error": "enqueue_cancelled"}
            queue = self._queues[peer]
            if len(queue) + self._exec_capacity_reserved[peer] >= MAX_QUEUE:
                return 429, {"error": "queue_full"}

            command_id = self._next_id
            self._next_id += 1
            command = {"id": command_id, "cmd": cmd, "args": args}
            if commit is not None and not commit(command_id):
                return 409, {"error": "lease_invalid"}
            if owner_client is not None and owner_lease_id is not None:
                self._command_owner[command_id] = (owner_client, owner_lease_id)
            queue.append(command)
            enqueued_at = self._now()
            self._enqueued_at[command_id] = enqueued_at
            if operation_timeout_s > 0.0:
                self._operation_deadlines[command_id] = (
                    enqueued_at + operation_timeout_s
                )

        return 200, {"id": command_id, "peer": peer, "cmd": cmd}

    def _enqueue_exec_enforce(
        self,
        args: dict,
        peer: str,
        owner_client: ClientIdentity | None,
        owner_lease_id: str | None,
        operation_timeout_s: float,
        commit: Callable[[int], bool] | None = None,
    ) -> tuple[int, dict]:
        expr = args.get("expr", "")
        main_fn = args.get("main_fn", "")
        expr_text = expr if isinstance(expr, str) else ""
        main_fn_text = main_fn if isinstance(main_fn, str) else ""

        if self.exec_allowlist is None or self.exec_audit is None:
            return 403, {"error": "exec_not_allowed"}
        if expr_text == "" or expr_text not in self.exec_allowlist:
            try:
                self.exec_audit(expr_text, "denied", main_fn_text, None)
            except Exception:
                return 503, {"error": "audit_failed"}
            return 403, {"error": "exec_not_allowed"}

        # Reserve both capacity and id before the durable "allowed" audit. Every
        # enqueue path counts this reservation, so no concurrent command can consume
        # the promised slot while audit I/O runs outside the state lock.
        with self._lock:
            if self._stopping:
                return 409, {"error": "enqueue_cancelled"}
            if (
                len(self._queues[peer]) + self._exec_capacity_reserved[peer]
                >= MAX_QUEUE
            ):
                return 429, {"error": "queue_full"}
            command_id = self._next_id
            self._next_id += 1
            self._exec_capacity_reserved[peer] += 1
            enqueue_generation = self._enqueue_generation

        try:
            self.exec_audit(expr_text, "allowed", main_fn_text, command_id)
        except Exception:
            with self._lock:
                self._exec_capacity_reserved[peer] -= 1
            return 503, {"error": "audit_failed"}

        command_args = dict(args)
        command_args["expr"] = expr_text
        command_args["main_fn"] = main_fn_text
        command = {"id": command_id, "cmd": "exec_enforce", "args": command_args}
        commit_failed = False
        capacity_lost = False
        fence_lost = False
        with self._lock:
            queue = self._queues[peer]
            self._exec_capacity_reserved[peer] -= 1
            if self._stopping or self._enqueue_generation != enqueue_generation:
                fence_lost = True
            elif len(queue) >= MAX_QUEUE:
                capacity_lost = True
            elif commit is not None and not commit(command_id):
                commit_failed = True
            else:
                queue.append(command)
                enqueued_at = self._now()
                self._enqueued_at[command_id] = enqueued_at
                if operation_timeout_s > 0.0:
                    self._operation_deadlines[command_id] = (
                        enqueued_at + operation_timeout_s
                    )
                if owner_client is not None and owner_lease_id is not None:
                    self._command_owner[command_id] = (owner_client, owner_lease_id)

        if commit_failed or capacity_lost or fence_lost:
            try:
                self.exec_audit(expr_text, "discarded", main_fn_text, command_id)
            except Exception:
                pass
        if capacity_lost:
            return 429, {"error": "queue_full"}
        if fence_lost:
            return 409, {"error": "enqueue_cancelled"}
        if commit_failed:
            return 409, {"error": "lease_invalid"}

        return 200, {"id": command_id, "peer": peer, "cmd": "exec_enforce"}

    def _rollback_enqueued(self, command_id: int) -> None:
        for peer, queue in self._queues.items():
            self._queues[peer] = [
                command for command in queue if command.get("id") != command_id
            ]
        self._enqueued_at.pop(command_id, None)
        self._operation_deadlines.pop(command_id, None)
        self._command_owner.pop(command_id, None)

    def abandon_command(
        self, command_id: int, reason: str = "tool_timeout"
    ) -> bool:
        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[
            tuple[ClientIdentity, str, int, str, str]
        ] = []
        owner_to_finish: tuple[ClientIdentity, str, int] | None = None
        with self._lock:
            existed = (
                command_id in self._enqueued_at
                or command_id in self._results
                or command_id in self._command_owner
                or command_id in self._fire_and_forget_ids
            )
            found_queued = False
            for peer, queue in self._queues.items():
                survivors: list[dict] = []
                for command in queue:
                    if command.get("id") == command_id:
                        self._mark_discarded(
                            command,
                            reason,
                            discarded_exec,
                            finished_operations,
                        )
                        found_queued = True
                    else:
                        survivors.append(command)
                self._queues[peer] = survivors
            had_result = command_id in self._results
            owner = self._command_owner.pop(command_id, None)
            self._results.pop(command_id, None)
            self._enqueued_at.pop(command_id, None)
            self._operation_deadlines.pop(command_id, None)
            self._fire_and_forget_ids.discard(command_id)
            if owner is not None and not found_queued and not had_result:
                owner_to_finish = (owner[0], owner[1], command_id)

        for expr, main_fn, discarded_id in discarded_exec:
            try:
                if self.exec_audit is not None:
                    self.exec_audit(expr, "discarded", main_fn, discarded_id)
            except Exception:
                pass
        self._finish_operations(finished_operations)
        if owner_to_finish is not None and self.coordination is not None:
            self.coordination.finish_operation_exact(
                owner_to_finish[0].session_id,
                owner_to_finish[1],
                owner_to_finish[2],
                command_succeeded=False,
            )
        return existed or found_queued

    def reap_expired_commands(self, now: float | None = None) -> int:
        if now is None:
            now = self._now()
        with self._lock:
            expired = [
                command_id
                for command_id, deadline in self._operation_deadlines.items()
                if now >= deadline
            ]
        return sum(
            1
            for command_id in expired
            if self.abandon_command(command_id, "tool_timeout")
        )

    def whitelisted_commands(self) -> set[str]:
        commands = set(WHITELISTED_COMMANDS)
        if self.enable_exec_enforce:
            commands.update(EXEC_COMMANDS)
        return commands

    def record_poll(self, peer: str, version: str | None = None) -> tuple[int, dict]:
        if peer not in VALID_PEERS:
            return 400, {"error": "bad_peer"}

        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[
            tuple[ClientIdentity, str, int, str, str]
        ] = []
        coordination = self.coordination
        version_state = (
            self.version_validator(version)
            if self.version_validator is not None
            else None
        )
        self.reap_expired_commands(self._now())

        with self._lock:
            now = self._now()
            prev_poll = self._last_poll_at.get(peer)
            self._poll_versions[peer] = version
            self._last_poll_at[peer] = now
            if version_state in {"legacy_blocked", "version_mismatch"}:
                return 200, {"commands": [], "delay_ms": 0}

            queue = self._queues[peer]

            # Reconnect flush (BUG-041): a poll gap wider than PEER_RECONNECT_GAP_S
            # means the game/session that owned this queue is gone; its commands
            # are stale and must not reach the fresh peer. The first poll
            # (prev_poll is None) has no measurable gap and never flushes.
            if prev_poll is not None and (now - prev_poll) > PEER_RECONNECT_GAP_S:
                self._discard_queue(
                    queue,
                    "peer_reconnect_flush",
                    discarded_exec,
                    finished_operations,
                )

            # TTL: drop commands older than COMMAND_TTL_S even without a reconnect,
            # so an abandoned queue never delivers ancient commands.
            self._expire_stale_commands(
                queue, now, discarded_exec, finished_operations
            )

        while True:
            with self._lock:
                queue_ref = self._queues[peer]
                snapshot = list(queue_ref)
            has_mutation = any(
                isinstance(command.get("cmd"), str)
                and command_requires_lease(command["cmd"])
                for command in snapshot
            )
            if coordination is not None and has_mutation:
                # Expiry may audit and invoke cleanup. Reads skip the entire
                # authority/probe path and remain independently dispatchable.
                coordination.expire_due()
            retail_quarantined = (
                self._retail_quarantined()
                if coordination is not None and has_mutation
                else False
            )

            with self._lock:
                queue = self._queues[peer]
                if (
                    queue is not queue_ref
                    or len(queue) < len(snapshot)
                    or any(
                        queue[index] is not command
                        for index, command in enumerate(snapshot)
                    )
                ):
                    continue

                commands = []
                for command in snapshot:
                    command_id = command.get("id")
                    command_name = command.get("cmd")
                    if (
                        isinstance(command_id, int)
                        and isinstance(command_name, str)
                        and command_requires_lease(command_name)
                        and coordination is not None
                    ):
                        owner = self._command_owner.get(command_id)
                        discard_reason = ""
                        if retail_quarantined:
                            discard_reason = "retail_quarantine"
                        elif (
                            command_name == "vehicle_release"
                            and command_id in self._fire_and_forget_ids
                        ):
                            # Owner release deliberately invalidates the lease before
                            # cleanup. Only this atomically registered internal command
                            # may dispatch without an active owner mapping.
                            pass
                        elif owner is None:
                            discard_reason = "authority_missing"
                        elif not coordination.claim_dispatch(
                            owner[0].session_id,
                            owner[1],
                            command_id,
                            command_name,
                        ):
                            discard_reason = "lease_inactive"
                        if discard_reason:
                            self._mark_discarded(
                                command,
                                discard_reason,
                                discarded_exec,
                                finished_operations,
                            )
                            continue
                    wire_command = dict(command)
                    wire_command.pop("owner_session_id", None)
                    wire_command.pop("owner_lease_id", None)
                    commands.append(wire_command)
                del queue[: len(snapshot)]
                delay_ms = 0
                if commands and self._poll_delay_ms > 0:
                    delay_ms = self._poll_delay_ms
                    self._poll_delay_ms = 0
                break

        # Keep the exec audit ledger consistent (an 'allowed' entry must equal a
        # command that reached the game): an exec_enforce dropped before delivery
        # is recorded as 'discarded'. Done outside the lock — exec_audit may do I/O.
        for expr, main_fn, command_id in discarded_exec:
            try:
                self.exec_audit(expr, "discarded", main_fn, command_id)
            except Exception:
                pass
        self._finish_operations(finished_operations)

        return 200, {"commands": commands, "delay_ms": delay_ms}

    def _discard_queue(
        self,
        queue: list[dict],
        reason: str,
        discarded_exec: list[tuple[str, str, int]],
        finished_operations: list[tuple[ClientIdentity, str, int, str, str]],
    ) -> None:
        # Caller holds self._lock. Empties the queue, recording a failed result
        # per command so its enqueuer's /await resolves instead of hanging.
        for command in queue:
            self._mark_discarded(
                command, reason, discarded_exec, finished_operations
            )
        queue.clear()

    def _expire_stale_commands(
        self,
        queue: list[dict],
        now: float,
        discarded_exec: list[tuple[str, str, int]],
        finished_operations: list[tuple[ClientIdentity, str, int, str, str]],
    ) -> None:
        # Caller holds self._lock. Drops commands older than COMMAND_TTL_S in place.
        survivors: list[dict] = []
        for command in queue:
            command_id = command.get("id")
            enqueued_at = self._enqueued_at.get(command_id)
            if enqueued_at is not None and (now - enqueued_at) > COMMAND_TTL_S:
                self._mark_discarded(
                    command,
                    "stale_discarded",
                    discarded_exec,
                    finished_operations,
                )
            else:
                survivors.append(command)
        queue[:] = survivors

    def _mark_discarded(
        self,
        command: dict,
        reason: str,
        discarded_exec: list[tuple[str, str, int]],
        finished_operations: list[tuple[ClientIdentity, str, int, str, str]],
    ) -> None:
        # Caller holds self._lock. Records the failed result and, for exec_enforce,
        # collects an audit tuple to be written by the caller outside the lock.
        command_id = command.get("id")
        if command_id is None:
            return
        fire_and_forget = command_id in self._fire_and_forget_ids
        if fire_and_forget:
            self._fire_and_forget_ids.discard(command_id)
            self._results.pop(command_id, None)
        elif command_id not in self._results:
            self._results[command_id] = {"id": command_id, "ok": False, "error": reason}
        self._enqueued_at.pop(command_id, None)
        self._operation_deadlines.pop(command_id, None)
        owner = self._command_owner.get(command_id)
        command_name = command.get("cmd")
        if owner is not None and isinstance(command_name, str):
            finished_operations.append(
                (owner[0], owner[1], command_id, command_name, reason)
            )
        if command.get("cmd") == "exec_enforce" and self.exec_audit is not None:
            args = command.get("args", {})
            if isinstance(args, dict):
                expr = args.get("expr", "")
                main_fn = args.get("main_fn", "")
            else:
                expr = ""
                main_fn = ""
            discarded_exec.append((expr, main_fn, command_id))

    def _finish_operations(
        self,
        operations: list[tuple[ClientIdentity, str, int, str, str]],
    ) -> None:
        coordination = self.coordination
        if coordination is None:
            return
        for client, lease_id, command_id, command, reason in operations:
            degraded = coordination.discard_committed(
                client, lease_id, command_id, command, reason
            )
            if not degraded:
                continue
            with self._lock:
                result = self._results.get(command_id)
                if result is not None:
                    current = result.get("cleanup_degraded")
                    values = list(current) if isinstance(current, list) else []
                    for item in degraded:
                        if item not in values:
                            values.append(item)
                    result["cleanup_degraded"] = values
                self._audit_degraded_count += 1

    def store_result(self, body: dict) -> tuple[int, dict]:
        try:
            command_id = int(body.get("id"))
        except (TypeError, ValueError):
            return 400, {"error": "bad_id"}

        t_result = self._now()
        stored = dict(body)
        meta: dict[str, float] = {"t_result": t_result}

        owner_operation: tuple[ClientIdentity, str, int] | None = None
        discarded = False
        with self._lock:
            deadline = self._operation_deadlines.get(command_id)
            known = (
                command_id in self._enqueued_at
                or command_id in self._fire_and_forget_ids
            )
            if not known or (deadline is not None and t_result >= deadline):
                discarded = True
                self._fire_and_forget_ids.discard(command_id)
                self._results.pop(command_id, None)
                self._enqueued_at.pop(command_id, None)
                self._operation_deadlines.pop(command_id, None)
                owner = self._command_owner.pop(command_id, None)
                if owner is not None:
                    owner_operation = (owner[0], owner[1], command_id)
            elif command_id in self._fire_and_forget_ids:
                self._fire_and_forget_ids.discard(command_id)
                self._results.pop(command_id, None)
                self._enqueued_at.pop(command_id, None)
                self._operation_deadlines.pop(command_id, None)
            else:
                t_enqueue = self._enqueued_at.get(command_id)
                if t_enqueue is not None:
                    meta["t_enqueue"] = t_enqueue
                    meta["rtt_s"] = t_result - t_enqueue
                stored["_server"] = meta
                self._results[command_id] = stored
                owner = self._command_owner.get(command_id)
                if owner is not None:
                    owner_operation = (owner[0], owner[1], command_id)

        if owner_operation is not None and self.coordination is not None:
            ok_value = stored.get("ok")
            self.coordination.finish_operation_exact(
                owner_operation[0].session_id,
                owner_operation[1],
                owner_operation[2],
                command_succeeded=(
                    ok_value is True
                    or (
                        isinstance(ok_value, int)
                        and not isinstance(ok_value, bool)
                        and ok_value == 1
                    )
                ),
            )

        response = {"ok": True, "id": command_id, "ok_value": stored.get("ok")}
        if discarded:
            response["discarded"] = True
        return 200, response

    def take_result(self, command_id: int, remove: bool = False) -> dict | None:
        self.reap_expired_commands()
        owner_operation: tuple[ClientIdentity, str, int] | None = None
        with self._lock:
            if remove:
                result = self._results.pop(command_id, None)
                # A polling /await?remove=1 must consume ownership/pin metadata
                # only when it actually consumes a result.  While pending, the
                # command still owns its operation pin and remains attributable
                # for owner-scoped cleanup/status.
                if result is not None:
                    self._enqueued_at.pop(command_id, None)
                    self._operation_deadlines.pop(command_id, None)
                    owner = self._command_owner.pop(command_id, None)
                    if owner is not None:
                        owner_operation = (owner[0], owner[1], command_id)
            else:
                result = self._results.get(command_id)
        if owner_operation is not None:
            self.coordination.finish_operation_exact(
                owner_operation[0].session_id,
                owner_operation[1],
                owner_operation[2],
            )
        return result

    def cancel_owner_pending(
        self, session_id: str, reason: str, lease_id: str | None = None
    ) -> dict[str, int]:
        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[
            tuple[ClientIdentity, str, int, str, str]
        ] = []
        cancelled = 0
        with self._lock:
            self._enqueue_generation += 1
            for peer, queue in self._queues.items():
                survivors: list[dict] = []
                for command in queue:
                    command_id = command.get("id")
                    owner = self._command_owner.get(command_id)
                    if (
                        owner is not None
                        and owner[0].session_id == session_id
                        and (lease_id is None or owner[1] == lease_id)
                    ):
                        self._mark_discarded(
                            command, reason, discarded_exec, finished_operations
                        )
                        cancelled += 1
                    else:
                        survivors.append(command)
                self._queues[peer] = survivors

        for expr, main_fn, command_id in discarded_exec:
            try:
                if self.exec_audit is not None:
                    self.exec_audit(expr, "discarded", main_fn, command_id)
            except Exception:
                pass
        self._finish_operations(finished_operations)
        return {"cancelled": cancelled}

    def pending_for_owner(self, session_id: str) -> int:
        with self._lock:
            return sum(
                1
                for owner in self._command_owner.values()
                if owner[0].session_id == session_id
            )

    def cleanup_owner(
        self,
        session_id: str,
        lease_id: str,
        reason: str,
        vehicle_active: bool,
    ) -> dict[str, object]:
        coordination = self.coordination
        if not isinstance(reason, str) or not isinstance(vehicle_active, bool):
            return {
                "cancelled": 0,
                "vehicle_release_enqueued": 0,
                "cleanup_degraded": ["cleanup_invalid"],
            }
        if (
            coordination is not None
            and not coordination.cleanup_authority_active(session_id, lease_id)
        ):
            return {
                "cancelled": 0,
                "vehicle_release_enqueued": 0,
                "cleanup_degraded": ["cleanup_fenced"],
            }
        cleanup: dict[str, object] = self.cancel_owner_pending(
            session_id, reason, lease_id
        )
        cleanup["vehicle_release_enqueued"] = 0
        if not vehicle_active:
            return cleanup

        if self._retail_quarantined():
            cleanup["cleanup_degraded"] = ["retail_quarantine"]
            return cleanup

        # Keep append + fire-and-forget tracking atomic with /poll. This command is
        # internal and deliberately has no owner mapping or externally awaited result.
        with self._lock:
            if (
                coordination is not None
                and not coordination.cleanup_authority_active(session_id, lease_id)
            ):
                cleanup["cleanup_degraded"] = ["cleanup_fenced"]
                return cleanup
            status, payload = self.enqueue_command(
                "vehicle_release", {}, peer="client", internal=True
            )
            if status == 200:
                command_id = payload["id"]
                self._fire_and_forget_ids.add(command_id)
                cleanup["vehicle_release_enqueued"] = 1
            else:
                cleanup["cleanup_degraded"] = ["vehicle_release_failed"]
        return cleanup

    def _retail_quarantined(self) -> bool:
        probe = self.retail_probe
        if probe is None:
            return self.coordination is not None
        try:
            result = probe()
        except Exception:
            return True
        if not isinstance(result, dict) or result.get("known") is not True:
            return True
        processes = result.get("processes")
        return not isinstance(processes, list) or bool(processes)

    def set_poll_delay(self, delay_ms: int) -> tuple[int, dict]:
        if delay_ms < 0 or delay_ms > 5000:
            return 400, {"error": "bad_ms_range"}

        with self._lock:
            self._poll_delay_ms = delay_ms

        return 200, {"ok": True, "ms": delay_ms}

    def status_snapshot(self, now: float | None = None) -> dict:
        if now is None:
            now = self._now()
        self.reap_expired_commands(now)
        with self._lock:
            last_poll_at = dict(self._last_poll_at)
            versions = dict(self._poll_versions)
            queue_depth = {peer: len(queue) for peer, queue in self._queues.items()}
            results_pending = len(self._results)
            last_client_request_at = self._last_client_request_at
            audit_degraded_count = self._audit_degraded_count

        peers = {}
        for peer in sorted(VALID_PEERS):
            poll_at = last_poll_at.get(peer)
            peers[peer] = {
                "last_poll_at": poll_at,
                "last_poll_age_s": None if poll_at is None else max(0.0, now - poll_at),
                "queue_depth": queue_depth.get(peer, 0),
                "version": versions.get(peer),
            }
        return {
            "peers": peers,
            "results_pending": results_pending,
            "last_client_request_at": last_client_request_at,
            "audit_degraded_count": audit_degraded_count,
        }

    def cancel_pending(self, reason: str = "server_stopping") -> None:
        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[
            tuple[ClientIdentity, str, int, str, str]
        ] = []
        with self._lock:
            self._enqueue_generation += 1
            self._stopping = True
            for queue in self._queues.values():
                self._discard_queue(
                    queue, reason, discarded_exec, finished_operations
                )
        for expr, main_fn, command_id in discarded_exec:
            try:
                if self.exec_audit is not None:
                    self.exec_audit(expr, "discarded", main_fn, command_id)
            except Exception:
                pass
        self._finish_operations(finished_operations)

    def resume_accepting(self) -> None:
        with self._lock:
            if self._stopping:
                self._enqueue_generation += 1
                self._stopping = False


class Handler(BaseHTTPRequestHandler):
    server_version = "DayZMCPPOC/0.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    @property
    def state(self) -> ServerState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if not self._authorized(qs):
            return

        if parsed.path == "/poll":
            self._handle_poll(qs)
            return

        if parsed.path == "/await":
            self.state.touch_client()
            self._handle_await(qs)
            return

        if parsed.path == "/status":
            self.state.touch_client()
            self._handle_status()
            return

        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if not self._authorized(qs):
            return

        if parsed.path == "/enqueue":
            self.state.touch_client()
            self._handle_enqueue()
            return

        session_action = SESSION_ROUTES.get(parsed.path)
        if session_action is not None:
            self.state.touch_client()
            self._handle_session(session_action)
            return


        lifecycle_action = LIFECYCLE_ROUTES.get(parsed.path)
        if lifecycle_action is not None:
            self.state.touch_client()
            self._handle_lifecycle(lifecycle_action)
            return

        admin_action = ADMIN_ROUTES.get(parsed.path)
        if admin_action is not None:
            self.state.touch_client()
            self._handle_admin(admin_action)
            return

        if parsed.path == "/result":
            self._handle_result()
            return

        if parsed.path == "/set_poll_delay":
            self._handle_set_poll_delay()
            return

        self._json(404, {"error": "not_found"})

    def _authorized(self, qs: dict[str, list[str]]) -> bool:
        supplied = qs.get("key", [""])[0]
        if not hmac.compare_digest(supplied, self.state.key):
            self._json(401, {"error": "unauthorized"})
            return False
        if (
            self.headers.get(daemon_credential.RETRY_HEADER_NAME)
            == daemon_credential.RETRY_HEADER_VALUE
        ):
            self.state.record_credential_recovery()
        return True

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "bad_content_length"})
            return None

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return None

        if not isinstance(body, dict):
            self._json(400, {"error": "bad_json_type"})
            return None

        return body

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _log(self, message: str) -> None:
        sink = getattr(self.server, "log_sink", _default_log_sink)  # type: ignore[attr-defined]
        sink(message)

    def _handle_enqueue(self) -> None:
        body = self._read_json()
        if body is None:
            return

        cmd = body.get("cmd")
        args = body.get("args", {})
        peer = body.get("peer")
        status, payload = self.state.enqueue_command(
            cmd,
            args,
            peer,
            identity_payload=body.get("identity"),
            lease_token=body.get("lease_token"),
            operation_timeout_s=body.get("operation_timeout_s", 0.0),
        )
        payload = self._persist_coordination(payload)
        if status != 200:
            self._json(status, payload)
            return

        self._log(f"ENQUEUE id={payload['id']} peer={payload['peer']} cmd={payload['cmd']}")
        response = {"id": payload["id"]}
        if "cleanup_degraded" in payload:
            response["cleanup_degraded"] = payload["cleanup_degraded"]
        self._json(200, response)

    def _handle_session(self, action: str) -> None:
        body = self._read_json()
        if body is None:
            return
        coordination = self.state.coordination
        if coordination is None:
            self._json(404, {"error": "not_found"})
            return
        try:
            client = ClientIdentity.from_payload(body.get("identity"))
        except ValueError:
            self._json(400, {"error": "invalid_identity"})
            return

        if action == "acquire":
            purpose = body.get("purpose")
            if not isinstance(purpose, str) or not purpose.strip():
                self._json(400, {"error": "bad_purpose"})
                return
            operation_id = body.get("operation_id")
            if operation_id is not None and not isinstance(operation_id, str):
                self._json(400, {"error": "bad_operation_id"})
                return
            status, payload = coordination.acquire(
                client, purpose.strip(), operation_id
            )
        elif action == "enqueue":
            purpose = body.get("purpose")
            if not isinstance(purpose, str) or not purpose.strip():
                self._json(400, {"error": "bad_purpose"})
                return
            status, payload = coordination.enqueue(
                client, purpose.strip(), body.get("operation_id")
            )
        elif action == "wait":
            timeout_s = body.get("timeout_s", 30.0)
            if (
                not _is_real_number(timeout_s)
                or float(timeout_s) < 0.0
                or float(timeout_s) > 30.0
            ):
                self._json(400, {"error": "bad_wait_timeout"})
                return
            ticket = body.get("ticket")
            if not isinstance(ticket, str) or not ticket:
                self._json(403, {"error": "ticket_invalid"})
                return
            status, payload = coordination.wait(client, ticket, float(timeout_s))
        elif action == "cancel":
            status, payload = coordination.cancel(client, body.get("ticket"))
        elif action == "cancel-operation":
            status, payload = coordination.cancel_operation(
                client, body.get("operation_id")
            )
        elif action == "heartbeat":
            status, payload = coordination.heartbeat(client, body.get("lease_token"))
        elif action == "release":
            status, payload = coordination.release(client, body.get("lease_token"))
        else:
            status = 200
            payload = coordination.status(client)
            payload["daemon_generation"] = self.state.daemon_generation
            payload["pending_commands"] = self.state.pending_for_owner(
                client.session_id
            )

        payload = self._persist_coordination(payload)
        self._log(
            "SESSION "
            f"event={action} status={status} "
            f"lease_id={payload.get('lease_id', '')} "
            f"ticket={payload.get('ticket', '')}"
        )
        self._json(status, payload)

    def _persist_coordination(self, payload: dict) -> dict:
        coordination = self.state.coordination
        store = self.state.coordination_store
        if coordination is None or store is None:
            return payload
        try:
            snapshot = coordination.snapshot_payload()
            writer = getattr(store, "write_coordination")
            writer(snapshot)
            return payload
        except Exception:
            result = dict(payload)
            degraded = result.get("cleanup_degraded", [])
            if not isinstance(degraded, list):
                degraded = []
            result["cleanup_degraded"] = list(dict.fromkeys([*degraded, "snapshot_failed"]))
            return result

    def _handle_lifecycle(self, action: str) -> None:
        body = self._read_json()
        if body is None:
            return
        lifecycle = self.state.lifecycle
        if lifecycle is None:
            self._json(404, {"error": "not_found"})
            return
        try:
            client = ClientIdentity.from_payload(body.get("identity"))
        except ValueError:
            self._json(400, {"error": "invalid_identity"})
            return
        token = body.get("lease_token")
        if token is not None and not isinstance(token, str):
            self._json(403, {"error": "lease_invalid"})
            return
        if action == "start":
            result = lifecycle.start_run(client, token, body.get("request"))
        elif action == "ack":
            result = lifecycle.ack_run(
                client,
                token,
                body.get("run_id"),
                body.get("launch_operation_id"),
            )
        elif action == "stop":
            result = lifecycle.stop_run(client, token, body.get("run_id"))
        elif action == "adopt":
            result = lifecycle.adopt_run(client, token, body.get("run_id"))
        elif action == "reap":
            result = lifecycle.reap_dead_run(client, token, body.get("run_id"))
        else:
            result = lifecycle.status(client)
            requested_run_id = body.get("run_id")
            if requested_run_id is not None:
                if not isinstance(requested_run_id, str) or not requested_run_id:
                    self._json(400, {"error": "invalid_run_id"})
                    return
                result = dict(result)
                result["runs"] = [
                    run
                    for run in result.get("runs", [])
                    if isinstance(run, dict) and run.get("run_id") == requested_run_id
                ]
        result = dict(result)
        status = int(result.pop("_http_status", 200))
        result = self._persist_coordination(result)
        self._json(status, result)

    def _handle_admin(self, action: str) -> None:
        body = self._read_json()
        if body is None:
            return
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            self._json(400, {"error": "invalid_reason"})
            return
        if action == "audit-repair":
            fault_id = body.get("fault_id")
            if not isinstance(fault_id, str) or not fault_id:
                self._json(400, {"error": "invalid_fault_id"})
                return
            if body.get("confirmation") != f"REPAIR {fault_id}":
                self._json(403, {"error": "confirmation_required"})
                return
            status, result = _repair_coordination_audit_fault(
                self.state, fault_id
            )
            self._json(status, result)
            return
        if action == "lifecycle-recovery-repair":
            fault_id = body.get("fault_id")
            expected_head = body.get("expected_head_sha256")
            if (
                not isinstance(fault_id, str)
                or not fault_id
                or not isinstance(expected_head, str)
                or len(expected_head) != 64
                or len(reason.strip()) > 160
            ):
                self._json(400, {"error": "invalid_lifecycle_recovery_repair"})
                return
            if body.get("confirmation") != f"REPAIR LIFECYCLE {fault_id}":
                self._json(403, {"error": "confirmation_required"})
                return
            status, result = _repair_lifecycle_recovery_fault(
                self.state, fault_id, expected_head
            )
            self._json(status, result)
            return
        if action == "release":
            lease_id = body.get("lease_id")
            if body.get("confirmation") != f"FORCE {lease_id}":
                self._json(403, {"error": "confirmation_required"})
                return
            coordination = self.state.coordination
            if coordination is None:
                self._json(404, {"error": "not_found"})
                return
            status, result = coordination.admin_release(lease_id, reason)
            self._json(status, self._persist_coordination(result))
            return

        lifecycle = self.state.lifecycle
        if lifecycle is None:
            self._json(404, {"error": "not_found"})
            return
        run_id, pid = body.get("run_id"), body.get("pid")
        empty = body.get("empty", False)
        if not isinstance(empty, bool):
            self._json(400, {"error": "invalid_reconcile_request"})
            return
        expected_confirmation = (
            f"FORCE {run_id} EMPTY" if empty else f"FORCE {run_id} {pid}"
        )
        if body.get("confirmation") != expected_confirmation:
            self._json(403, {"error": "confirmation_required"})
            return
        if empty:
            result = dict(
                lifecycle.admin_reconcile(run_id, pid, reason, empty=True)
            )
        else:
            result = dict(lifecycle.admin_reconcile(run_id, pid, reason))
        status = int(result.pop("_http_status", 200))
        self._json(status, result)


    def _handle_await(self, qs: dict[str, list[str]]) -> None:
        try:
            command_id = int(qs.get("id", [""])[0])
        except ValueError:
            self._json(400, {"error": "bad_id"})
            return

        # remove=1 consumes the result on delivery so client-mode proxies do not
        # leak one _results entry per command in the shared daemon. Default keeps
        # the non-destructive read for back-compat with the harness.
        remove = qs.get("remove", ["0"])[0] == "1"
        result = self.state.take_result(command_id, remove=remove)
        if result is None:
            self._json(200, {"status": "pending"})
            return

        self._json(200, {"status": "done", "result": result})

    def _handle_status(self) -> None:
        # Authenticated health/status read used by client bridge_status and by the
        # client/daemon discovery health-probe. With a status_provider wired (daemon)
        # it returns the rich version-aware status; bare loopback returns the raw
        # snapshot so the endpoint still answers for discovery.
        provider = getattr(self.server, "status_provider", None)  # type: ignore[attr-defined]
        if provider is None:
            payload = self.state.status_snapshot()
        else:
            payload = provider()
        payload = dict(payload)
        payload["credential_recovery"] = (
            self.state.credential_recovery_snapshot()
        )
        self._json(200, payload)

    def _handle_poll(self, qs: dict[str, list[str]]) -> None:
        peer = qs.get("peer", ["server"])[0] or "server"
        version = qs["ver"][0] if "ver" in qs else None
        status, payload = self.state.record_poll(peer, version)
        if status != 200:
            self._json(status, payload)
            return

        delay_ms = int(payload.get("delay_ms", 0))
        commands = payload["commands"]
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        self._log(f"HIT GET /poll peer={peer} commands={len(commands)} delay_ms={delay_ms}")
        self._json(200, {"commands": commands})

    def _handle_result(self) -> None:
        body = self._read_json()
        if body is None:
            return

        status, payload = self.state.store_result(body)
        if status != 200:
            self._json(status, payload)
            return

        self._log(f"RESULT id={payload['id']} ok={payload.get('ok_value')}")
        self._json(200, {"ok": True})

    def _handle_set_poll_delay(self) -> None:
        body = self._read_json()
        if body is None:
            return

        try:
            delay_ms = int(body.get("ms"))
        except (TypeError, ValueError):
            self._json(400, {"error": "bad_ms"})
            return

        status, payload = self.state.set_poll_delay(delay_ms)
        if status != 200:
            self._json(status, payload)
            return

        self._log(f"SET_POLL_DELAY ms={delay_ms}")
        self._json(200, {"ok": True})


def _repair_lifecycle_recovery_fault(
    state: ServerState, fault_id: str, expected_head_sha256: str
) -> tuple[int, dict[str, object]]:
    coordinator = state.coordination
    store = state.lifecycle_recovery_fault_store
    lifecycle = state.lifecycle
    if coordinator is None or store is None or lifecycle is None:
        return 404, {"error": "not_found"}
    try:
        active = store.load_active()
    except (OSError, RuntimeError, ValueError):
        return 409, {"error": "lifecycle_recovery_unreadable"}
    if not isinstance(active, dict):
        return 409, {"error": "lifecycle_recovery_fault_mismatch"}
    fault = active.get("fault")
    event = active.get("event")
    pointer = active.get("pointer")
    if (
        not isinstance(fault, dict)
        or not isinstance(event, dict)
        or not isinstance(pointer, dict)
        or fault.get("fault_id") != fault_id
        or pointer.get("head_event_sha256") != expected_head_sha256
    ):
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    if event.get("state") == "repaired":
        coordinator.clear_lifecycle_recovery_fault(fault_id)
        return 200, {"repaired": True, "fault_id": fault_id}
    if event.get("state") != "armed":
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    manifest_sha = fault.get("manifest_sha256")
    try:
        repairing_head = store.transition(
            fault_id,
            expected_head_sha256,
            state="repairing",
            expected_manifest_sha256=manifest_sha,
        )
    except (OSError, RuntimeError, ValueError):
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    try:
        if fault.get("scope") == "manifest":
            try:
                backup = store.read_manifest_backup(
                    fault.get("backup_receipt_sha256")
                )
            except (OSError, RuntimeError, ValueError):
                outcome = {
                    "terminal_safe": False,
                    "error": "receipt_missing",
                    "manifest_sha256": manifest_sha,
                }
            else:
                outcome = lifecycle.repair_manifest_recovery(backup)
        else:
            outcome = lifecycle.repair_recovery_fault(fault)
    except Exception:
        outcome = {"terminal_safe": False, "error": "cleanup_failed"}
    if not isinstance(outcome, dict) or outcome.get("terminal_safe") is not True:
        error = (
            str(outcome.get("error", "cleanup_failed"))
            if isinstance(outcome, dict)
            else "cleanup_failed"
        )
        error_code = (
            error
            if error
            in {
                "manifest_drift",
                "identity_ambiguous",
                "cleanup_failed",
                "receipt_missing",
            }
            else "cleanup_failed"
        )
        failure_manifest_sha = (
            outcome.get("manifest_sha256", manifest_sha)
            if isinstance(outcome, dict)
            else manifest_sha
        )
        try:
            store.transition(
                fault_id,
                repairing_head,
                state="armed",
                expected_manifest_sha256=failure_manifest_sha,
                error_code=error_code,
            )
        except Exception:
            pass
        return 409, {"error": error_code}
    current_manifest_sha = outcome.get("manifest_sha256", manifest_sha)
    try:
        receipt_sha = store.create_receipt(
            fault_id,
            {
                "manifest_sha256": current_manifest_sha,
                "all_relevant_runs_terminal": True,
            },
        )
        store.transition(
            fault_id,
            repairing_head,
            state="repaired",
            expected_manifest_sha256=current_manifest_sha,
            evidence_sha256=receipt_sha,
        )
    except (OSError, RuntimeError, ValueError):
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    if not coordinator.complete_lifecycle_recovery_fault(fault_id):
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    return 200, {"repaired": True, "fault_id": fault_id}


def _repair_coordination_audit_fault(
    state: ServerState, fault_id: str
) -> tuple[int, dict[str, object]]:
    coordinator = state.coordination
    store = state.coordination_fault_store
    writer = state.audit_writer
    if coordinator is None or store is None or writer is None:
        return 404, {"error": "not_found"}
    public_fault = coordinator.snapshot_payload().get("audit_fault")
    if not isinstance(public_fault, dict) or public_fault.get("fault_id") != fault_id:
        return 409, {"error": "audit_fault_mismatch"}
    expected_revision = coordinator.expected_repair_snapshot_revision(fault_id)
    if expected_revision is None:
        return 409, {"error": "audit_fault_mismatch"}
    try:
        marker, marker_sha = store.load_with_sha()
        if (
            not isinstance(marker, dict)
            or not isinstance(marker_sha, str)
            or marker.get("fault_id") != fault_id
        ):
            return 409, {"error": "audit_fault_unrepairable"}
        state_name = marker.get("state")
        if state_name == "fault":
            marker_sha = store.transition(
                fault_id,
                marker_sha,
                state="repairing",
                phase="repairing",
                repair_phase="compensation",
                expected_snapshot_revision=expected_revision,
            )
            marker, marker_sha = store.load_with_sha()
            state_name = marker.get("state")
        if state_name == "repairing":
            if marker.get("repair_phase") in {"none", "compensation"}:
                writer.write_once(
                    f"{fault_id}:compensation",
                    {
                        "event": (
                            "session_grant_revoked"
                            if marker.get("operation") == "grant"
                            else "session_release_reconciled"
                        ),
                        "reason": "admin_audit_repair",
                        "duration_s": 0.0,
                        "decision": "reconciled",
                        "fault_id": fault_id,
                        "lease_id": marker.get("lease_id"),
                        "ticket": marker.get("ticket_id"),
                        "operation": marker.get("operation"),
                        "client": marker.get("client"),
                    },
                )
                marker_sha = store.transition(
                    fault_id,
                    marker_sha,
                    state="repairing",
                    phase="repairing",
                    repair_phase="repair_event",
                    expected_snapshot_revision=expected_revision,
                )
                marker, marker_sha = store.load_with_sha()
            writer.write_once(
                f"{fault_id}:repaired",
                {
                    "event": "coordination_audit_repaired",
                    "reason": "admin_audit_repair",
                    "duration_s": 0.0,
                    "decision": "repaired",
                    "fault_id": fault_id,
                    "lease_id": marker.get("lease_id"),
                    "operation": marker.get("operation"),
                    "client": marker.get("client"),
                },
            )
            marker_sha = store.transition(
                fault_id,
                marker_sha,
                state="repaired",
                phase="repaired",
                repair_phase="repair_event",
                expected_snapshot_revision=expected_revision,
            )
            marker, marker_sha = store.load_with_sha()
            state_name = marker.get("state")
        if state_name == "repaired":
            current_expected = coordinator.expected_repair_snapshot_revision(fault_id)
            if current_expected is None:
                return 409, {"error": "audit_fault_mismatch"}
            if marker.get("expected_snapshot_revision") != current_expected:
                marker_sha = store.transition(
                    fault_id,
                    marker_sha,
                    state="repaired",
                    phase="repaired",
                    repair_phase="repair_event",
                    expected_snapshot_revision=current_expected,
                )
                marker, marker_sha = store.load_with_sha()
            if coordinator.finalize_audit_repair(marker, marker_sha):
                return 200, {"repaired": True, "fault_id": fault_id}
            return 503, {
                "error": "audit_repair_cleanup_pending",
                "fault_id": fault_id,
            }
        return 409, {"error": "audit_fault_unrepairable"}
    except Exception:
        return 503, {"error": "audit_repair_failed", "fault_id": fault_id}


def read_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        key = handle.read().strip()
    if not key:
        raise ValueError("empty keyfile")
    return key


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    # HTTPServer defaults allow_reuse_address to 1. On Windows, SO_REUSEADDR lets a
    # second process LISTEN on an already-bound port, which defeats the E4 single
    # instance lock (two loopbacks would silently split bridge polls). Exclusive
    # bind makes the second bind fail with EADDRINUSE (GATE4A-002).
    allow_reuse_address = False


def _is_address_in_use(exc: OSError) -> bool:
    # Windows surfaces EADDRINUSE as errno/winerror 10048 (WSAEADDRINUSE).
    return exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048


def _bind_exclusive(port: int, log_sink: LogSink, reclaim_orphans: bool) -> "ExclusiveThreadingHTTPServer":
    try:
        return ExclusiveThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        # Only EADDRINUSE is a reclaim candidate; any other bind error propagates.
        if not reclaim_orphans or not _is_address_in_use(exc):
            raise
        original_argv = getattr(sys, "orig_argv", None)
        expected_argv = list(original_argv) if isinstance(original_argv, list) else None
        if not orphan_guard.try_reclaim_port(
            port,
            log=log_sink,
            expected_executable=sys.executable,
            expected_argv=expected_argv,
        ):
            raise
        # A confirmed-dead-parent dayz_mcp orphan was terminated and the port came
        # free; retry the exclusive bind exactly once. E4 stays intact — the bind is
        # still ExclusiveThreadingHTTPServer with allow_reuse_address False.
        return ExclusiveThreadingHTTPServer(("127.0.0.1", port), Handler)


def create_http_server(
    port: int,
    state: ServerState,
    log_sink: LogSink | None = None,
    reclaim_orphans: bool = True,
    status_provider: Callable[[], dict] | None = None,
) -> ThreadingHTTPServer:
    sink = log_sink or _default_log_sink
    httpd = _bind_exclusive(port, sink, reclaim_orphans)
    if httpd.server_address[0] != "127.0.0.1":
        httpd.server_close()
        raise RuntimeError("refusing non-loopback bind")
    httpd.state = state  # type: ignore[attr-defined]
    httpd.log_sink = sink  # type: ignore[attr-defined]
    httpd.status_provider = status_provider  # type: ignore[attr-defined]
    return httpd


class LoopbackServer:
    def __init__(
        self,
        port: int,
        key: str,
        log_sink: LogSink | None = None,
        poll_interval: float = 0.1,
        enable_exec_enforce: bool = False,
        version_validator: VersionValidator | None = None,
        exec_allowlist: set[str] | None = None,
        exec_audit: ExecAudit | None = None,
        status_provider: Callable[[], dict] | None = None,
        reclaim_orphans: bool = True,
    ) -> None:
        self.port = port
        self.key = key
        self.log_sink = log_sink or _default_log_sink
        self.poll_interval = poll_interval
        self.status_provider = status_provider
        self.reclaim_orphans = reclaim_orphans
        self.state = ServerState(
            key,
            enable_exec_enforce=enable_exec_enforce,
            version_validator=version_validator,
            exec_allowlist=exec_allowlist,
            exec_audit=exec_audit,
        )
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.httpd is not None:
            return
        self.state.resume_accepting()
        self.httpd = create_http_server(
            self.port,
            self.state,
            self.log_sink,
            reclaim_orphans=self.reclaim_orphans,
            status_provider=self.status_provider,
        )
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": self.poll_interval},
            daemon=True,
        )
        self.thread.start()
        host, port = self.httpd.server_address
        self.log_sink(f"LISTEN host={host} port={port}")

    def stop(self, timeout: float = 2.0) -> None:
        if self.httpd is None:
            return
        self.state.cancel_pending()
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=timeout)
        self.httpd = None
        self.thread = None


def main(argv: list[str] | None = None, log_sink: LogSink | None = None) -> int:
    parser = argparse.ArgumentParser(description="DayZ MCP POC loopback server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keyfile", required=True)
    args = parser.parse_args(argv)

    key = read_key(args.keyfile)
    httpd = create_http_server(args.port, ServerState(key), log_sink=log_sink or _default_log_sink)
    host, port = httpd.server_address
    (log_sink or _default_log_sink)(f"LISTEN host={host} port={port}")
    try:
        httpd.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        return 130
    finally:
        httpd.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
