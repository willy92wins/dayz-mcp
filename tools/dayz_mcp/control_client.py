"""Non-launching client for the DayZ MCP daemon session-control API."""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from dayz_mcp import accredited_daemon_transport as transport
from dayz_mcp import daemon_credential
from dayz_mcp.daemon_policy_contract import AccreditedDaemonPolicy


_monotonic = time.monotonic


class ControlClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        request_stage: str,
        http_bytes_sent: int,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.request_stage = request_stage
        self.http_bytes_sent = http_bytes_sent


@dataclass(frozen=True)
class ControlIdentity:
    platform: str
    pid: int
    ppid: int
    started_at_utc: str
    session_id: str
    task_label: str = ""

    def __post_init__(self) -> None:
        if self.platform not in {"claude", "codex", "unknown"}:
            raise ValueError("invalid_identity")
        if any(
            type(value) is not int or value < 0 for value in (self.pid, self.ppid)
        ):
            raise ValueError("invalid_identity")
        if (
            not isinstance(self.started_at_utc, str)
            or not self.started_at_utc
            or not isinstance(self.session_id, str)
            or not self.session_id
            or not isinstance(self.task_label, str)
            or len(self.task_label) > 120
        ):
            raise ValueError("invalid_identity")

    def to_payload(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "pid": self.pid,
            "ppid": self.ppid,
            "started_at_utc": self.started_at_utc,
            "session_id": self.session_id,
            "task_label": self.task_label,
        }


def _decode_body(body: bytes) -> dict[str, object]:
    try:
        value = json.loads((body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ControlClientError(
            "daemon_bad_body", request_stage="post_request", http_bytes_sent=1
        ) from None
    if not isinstance(value, dict):
        raise ControlClientError(
            "daemon_bad_body", request_stage="post_request", http_bytes_sent=1
        )
    return value


def _remote_error_code(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error and len(error) <= 120:
        return error
    return "daemon_request_failed"


class ControlClient:
    def __init__(
        self,
        *,
        policy: AccreditedDaemonPolicy,
        identity: ControlIdentity,
        timeout_s: float = 5.0,
        credential_provider: (
            daemon_credential.RefreshingDaemonCredential | None
        ) = None,
    ) -> None:
        if type(policy) is not AccreditedDaemonPolicy:
            raise ValueError("invalid_daemon_policy")
        if type(identity) is not ControlIdentity:
            raise ValueError("invalid_identity")
        if (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(float(timeout_s))
            or not 0.0 < float(timeout_s) <= 3600.0
        ):
            raise ValueError("invalid_control_timeout")
        policy.revalidate()
        self.policy = policy
        self.identity = identity
        self.timeout_s = float(timeout_s)
        if credential_provider is None:
            credential_provider = (
                daemon_credential.RefreshingDaemonCredential(policy=policy)
            )
        elif (
            type(credential_provider)
            is not daemon_credential.RefreshingDaemonCredential
            or credential_provider.policy is not policy
        ):
            raise ValueError("credential_provider_policy_mismatch")
        self._credential_provider = credential_provider
        self.active_lease_token: str | None = None
        self.active_ticket: str | None = None
        self.active_operation_id: str | None = None
        self.state = "NEW"
        self._state_lock = threading.Lock()
        self._transition_lock = asyncio.Lock()

    def _request_once(
        self,
        path: str,
        payload: dict[str, object],
        timeout_s: float,
    ) -> dict[str, object]:
        try:
            self.policy.revalidate()
        except Exception:
            raise ControlClientError(
                "client_policy_untrusted_open_new_session",
                request_stage="pre_request",
                http_bytes_sent=0,
            ) from None
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        deadline = _monotonic() + timeout_s
        try:
            status, response_body = self._credential_provider.request_with_refresh(
                method="POST",
                path=path,
                query={},
                body=body,
                headers={"Content-Type": "application/json"},
                deadline=deadline,
            )
        except daemon_credential.CredentialRefreshError as error:
            raise ControlClientError(
                error.code,
                request_stage=error.request_stage,
                http_bytes_sent=error.http_bytes_sent,
            ) from None
        except transport.AccreditedTransportError as error:
            if error.code == "daemon_identity_unverified":
                code = error.code
            else:
                code = (
                    "daemon_unavailable"
                    if error.request_stage == "pre_request"
                    and error.http_bytes_sent == 0
                    else "daemon_response_ambiguous"
                )
            raise ControlClientError(
                code,
                request_stage=error.request_stage,
                http_bytes_sent=error.http_bytes_sent,
            ) from None
        except TimeoutError:
            raise ControlClientError(
                "daemon_unavailable",
                request_stage="pre_request",
                http_bytes_sent=0,
            ) from None
        except OSError:
            raise ControlClientError(
                "daemon_response_ambiguous",
                request_stage="post_request",
                http_bytes_sent=1,
            ) from None
        response = _decode_body(response_body)
        if status not in (200, 202):
            raise ControlClientError(
                _remote_error_code(response),
                request_stage="post_request",
                http_bytes_sent=1,
            )
        return response

    async def _session_call(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, object]:
        request_payload = {"identity": self.identity.to_payload()}
        request_payload.update(payload or {})
        effective_timeout = self.timeout_s if timeout_s is None else timeout_s
        return await asyncio.to_thread(
            self._request_once, path, request_payload, effective_timeout
        )

    async def session_status(self) -> dict[str, object]:
        return await self._session_call("/session/status")

    async def lifecycle_status(self) -> dict[str, object]:
        return await self._session_call("/lifecycle/status")

    def _bad_session_response(self) -> None:
        raise ControlClientError(
            "daemon_bad_session_response",
            request_stage="post_request",
            http_bytes_sent=1,
        )

    def _remember_acquire_or_wait(
        self, response: dict[str, object], operation_id: str | None
    ) -> None:
        status = response.get("status")
        if status == "active":
            lease_token = response.get("lease_token")
            if not isinstance(lease_token, str) or not lease_token:
                self._bad_session_response()
            with self._state_lock:
                self.active_lease_token = lease_token
                self.active_ticket = None
                self.active_operation_id = operation_id
                self.state = "ACTIVE"
            return
        if status == "queued":
            ticket = response.get("ticket")
            if not isinstance(ticket, str) or not ticket:
                self._bad_session_response()
            with self._state_lock:
                self.active_lease_token = None
                self.active_ticket = ticket
                self.active_operation_id = operation_id
                self.state = "QUEUED"
            return
        self._bad_session_response()

    def _validate_request_bound_wait_response(
        self,
        response: dict[str, object],
        *,
        ticket: str,
        operation_id: str,
    ) -> None:
        if (
            response.get("status") not in {"queued", "active"}
            or response.get("ticket") != ticket
            or response.get("operation_id") != operation_id
        ):
            self._bad_session_response()

    def _with_own_identity(
        self, response: dict[str, object]
    ) -> dict[str, object]:
        result = dict(response)
        result["client_identity_json"] = json.dumps(
            self.identity.to_payload(), separators=(",", ":")
        )
        return result

    def _clear_matching_ticket(self, ticket: str) -> None:
        with self._state_lock:
            if self.active_ticket != ticket:
                return
            self.active_ticket = None
            if self.active_lease_token is None:
                self.active_operation_id = None
                self.state = "CLOSED"

    def _clear_matching_lease(self, lease_token: str) -> None:
        with self._state_lock:
            if self.active_lease_token != lease_token:
                return
            self.active_lease_token = None
            if self.active_ticket is None:
                self.active_operation_id = None
                self.state = "CLOSED"

    def _begin_operation(self, operation_id: str) -> None:
        with self._state_lock:
            if (
                self.state not in {"NEW", "CLOSED"}
                or self.active_operation_id is not None
                or self.active_ticket is not None
                or self.active_lease_token is not None
            ):
                raise ControlClientError(
                    "session_transition_conflict",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                )
            self.active_operation_id = operation_id
            self.state = "NEW"

    async def session_acquire(self, purpose: str) -> dict[str, object]:
        if not isinstance(purpose, str) or not purpose:
            raise ValueError("invalid_session_purpose")
        returned = False
        primary: BaseException | None = None
        async with self._transition_lock:
            with self._state_lock:
                existing_state = self.state
                has_existing_session = (
                    existing_state == "ACTIVE"
                    and self.active_lease_token is not None
                ) or (
                    existing_state == "QUEUED"
                    and self.active_ticket is not None
                )
                existing_operation_id = self.active_operation_id
                existing_ticket = self.active_ticket
            if has_existing_session:
                if (
                    not isinstance(existing_operation_id, str)
                    or not existing_operation_id
                ):
                    raise ControlClientError(
                        "session_transition_conflict",
                        request_stage="pre_request",
                        http_bytes_sent=0,
                    )
                authoritative = await self._session_call("/session/status")
                own = authoritative.get("self")
                if isinstance(own, dict) and own.get("state") == "none":
                    await self._reconcile_idle_session_locked(
                        before=authoritative
                    )
                    has_existing_session = False
                elif not (
                    isinstance(own, dict)
                    and (
                        (
                            existing_state == "ACTIVE"
                            and own.get("state") == "active"
                        )
                        or (
                            existing_state == "QUEUED"
                            and own.get("state") == "queued"
                            and own.get("ticket") == existing_ticket
                        )
                    )
                ):
                    raise ControlClientError(
                        "session_transition_conflict",
                        request_stage="post_request",
                        http_bytes_sent=1,
                    )
            if has_existing_session:
                operation_id = existing_operation_id
            else:
                await self._reconcile_idle_session_locked()
                operation_id = str(uuid.uuid4())
                self._begin_operation(operation_id)
            try:
                response = await self._session_call(
                    "/session/acquire",
                    {"purpose": purpose, "operation_id": operation_id},
                )
                if has_existing_session:
                    with self._state_lock:
                        if self.state == "ACTIVE":
                            response_matches = (
                                response.get("status") == "active"
                                and response.get("lease_token")
                                == self.active_lease_token
                            )
                        else:
                            response_matches = (
                                self.state == "QUEUED"
                                and response.get("status") == "queued"
                                and response.get("ticket") == self.active_ticket
                            )
                    if not response_matches:
                        self._bad_session_response()
                self._remember_acquire_or_wait(response, operation_id)
                returned = True
                return self._with_own_identity(response)
            except BaseException as error:
                primary = error
                raise
            finally:
                if not returned and not has_existing_session:
                    safe_unsent = (
                        isinstance(primary, ControlClientError)
                        and primary.code == "daemon_unavailable"
                        and primary.request_stage == "pre_request"
                        and primary.http_bytes_sent == 0
                    )
                    if safe_unsent:
                        self._clear_operation(operation_id)
                    else:
                        degradation = await self._cleanup_operation_never_raises(
                            operation_id
                        )
                        if degradation:
                            if primary is not None:
                                primary.add_note(degradation)
                            else:
                                raise ControlClientError(
                                    "session_cleanup_failed",
                                    request_stage="post_request",
                                    http_bytes_sent=1,
                                )

    async def session_wait(
        self, ticket: str, timeout_s: float = 30.0
    ) -> dict[str, object]:
        if (
            not isinstance(ticket, str)
            or not ticket
            or not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(float(timeout_s))
            or timeout_s < 0.0
        ):
            raise ValueError("invalid_session_wait")
        async with self._transition_lock:
            with self._state_lock:
                operation_id = self.active_operation_id
                if (
                    self.state != "QUEUED"
                    or self.active_ticket != ticket
                    or not isinstance(operation_id, str)
                    or not operation_id
                ):
                    raise ControlClientError(
                        "session_transition_conflict",
                        request_stage="pre_request",
                        http_bytes_sent=0,
                    )
            try:
                response = await self._session_call(
                    "/session/wait",
                    {"ticket": ticket, "timeout_s": float(timeout_s)},
                    timeout_s=max(5.0, float(timeout_s) + 1.0),
                )
            except ControlClientError as error:
                if error.code in {"ticket_expired", "ticket_invalid"}:
                    self._clear_matching_ticket(ticket)
                raise
            self._remember_acquire_or_wait(response, operation_id)
            return self._with_own_identity(response)

    async def session_cancel(self, ticket: str) -> dict[str, object]:
        if not isinstance(ticket, str) or not ticket:
            raise ValueError("invalid_session_ticket")
        async with self._transition_lock:
            try:
                response = await self._session_call(
                    "/session/cancel", {"ticket": ticket}
                )
            except ControlClientError as error:
                if error.code in {"ticket_expired", "ticket_invalid"}:
                    self._clear_matching_ticket(ticket)
                raise
            with self._state_lock:
                if self.active_ticket == ticket:
                    self.active_ticket = None
                    self.active_operation_id = None
                    self.state = "CLOSED"
            return response

    async def session_heartbeat(self, lease_token: str) -> dict[str, object]:
        if not isinstance(lease_token, str) or not lease_token:
            raise ValueError("invalid_session_lease")
        async with self._transition_lock:
            try:
                return await self._session_call(
                    "/session/heartbeat", {"lease_token": lease_token}
                )
            except ControlClientError as error:
                if error.code in {"lease_expired", "lease_invalid"}:
                    self._clear_matching_lease(lease_token)
                raise

    async def session_release(self, lease_token: str) -> dict[str, object]:
        if not isinstance(lease_token, str) or not lease_token:
            raise ValueError("invalid_session_lease")
        async with self._transition_lock:
            with self._state_lock:
                if self.active_lease_token == lease_token:
                    self.state = "RELEASING"
            try:
                response = await self._session_call(
                    "/session/release", {"lease_token": lease_token}
                )
            except ControlClientError as error:
                if error.code in {"lease_expired", "lease_invalid"}:
                    self._clear_matching_lease(lease_token)
                raise
            with self._state_lock:
                if self.active_lease_token == lease_token:
                    self.active_lease_token = None
                    self.active_ticket = None
                    self.active_operation_id = None
                    self.state = "CLOSED"
            return response

    def _clear_operation(self, operation_id: str) -> None:
        with self._state_lock:
            if self.active_operation_id != operation_id:
                return
            self.active_lease_token = None
            self.active_ticket = None
            self.active_operation_id = None
            self.state = "CLOSED"

    async def _cancel_operation_remote_until_resolved(
        self, operation_id: str
    ) -> None:
        while True:
            response = await self._session_call(
                "/session/cancel-operation",
                {"operation_id": operation_id},
            )
            if response.get("cancelled") is True:
                if response.get("operation_id") != operation_id:
                    self._bad_session_response()
                return
            if response.get("resolving") is not True:
                self._bad_session_response()
            await asyncio.sleep(0.05)

    async def _cancel_operation_until_resolved(self, operation_id: str) -> None:
        await self._cancel_operation_remote_until_resolved(operation_id)
        self._clear_operation(operation_id)

    def _require_recovery_idle_status(
        self, status: dict[str, object]
    ) -> None:
        own = status.get("self")
        generation = status.get("daemon_generation")
        cleanup_degraded = status.get("cleanup_degraded")
        if (
            not isinstance(own, dict)
            or own.get("state") != "none"
            or own.get("position") is not None
            or "ticket" in own
            or "lease_id" in own
            or type(status.get("pending_commands")) is not int
            or status.get("pending_commands") != 0
            or "audit_fault" not in status
            or status.get("audit_fault") is not None
            or "lifecycle_recovery_fault" not in status
            or status.get("lifecycle_recovery_fault") is not None
            or not isinstance(cleanup_degraded, list)
            or cleanup_degraded
            or not isinstance(generation, str)
            or not generation
        ):
            raise ControlClientError(
                "session_transition_conflict",
                request_stage="post_request",
                http_bytes_sent=1,
            )

    async def _reconcile_idle_session_locked(
        self, *, before: dict[str, object] | None = None
    ) -> dict[str, object]:
        with self._state_lock:
            snapshot = (
                self.state,
                self.active_operation_id,
                self.active_ticket,
                self.active_lease_token,
            )
        operation_id = snapshot[1]
        if all(value is None for value in snapshot[1:]):
            return {"reconciled": False}
        if not isinstance(operation_id, str) or not operation_id:
            raise ControlClientError(
                "session_transition_conflict",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        if before is None:
            before = await self._session_call("/session/status")
        self._require_recovery_idle_status(before)
        await self._cancel_operation_remote_until_resolved(operation_id)
        after = await self._session_call("/session/status")
        # Generation equality is deliberately not required: a restarted accredited
        # daemon invalidates old authority, while this final clean status proves the
        # current generation has no state or pending command for this client.
        self._require_recovery_idle_status(after)

        with self._state_lock:
            current = (
                self.state,
                self.active_operation_id,
                self.active_ticket,
                self.active_lease_token,
            )
            if current != snapshot:
                raise ControlClientError(
                    "session_transition_conflict",
                    request_stage="post_request",
                    http_bytes_sent=1,
                )
            self.active_lease_token = None
            self.active_ticket = None
            self.active_operation_id = None
            self.state = "CLOSED"
        return {"reconciled": True}

    async def reconcile_idle_session(self) -> dict[str, object]:
        async with self._transition_lock:
            return await self._reconcile_idle_session_locked()

    async def _cleanup_operation_never_raises(
        self, operation_id: str
    ) -> str | None:
        cleanup_task = asyncio.create_task(
            self._cancel_operation_until_resolved(operation_id)
        )
        delayed_cancellation: asyncio.CancelledError | None = None
        try:
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as error:
                    delayed_cancellation = error
            cleanup_task.result()
        except BaseException as error:
            degradation = f"session cleanup degraded: {type(error).__name__}"
        else:
            degradation = None
        if delayed_cancellation is not None:
            raise delayed_cancellation
        return degradation

    async def session_acquire_wait(
        self,
        purpose: str,
        max_wait_s: float | None = None,
        progress_cb: Callable[[float, float | None, str | None], Awaitable[None]]
        | None = None,
    ) -> dict[str, object]:
        if not isinstance(purpose, str) or not purpose:
            raise ValueError("invalid_session_purpose")
        if max_wait_s is not None and (
            not isinstance(max_wait_s, (int, float))
            or isinstance(max_wait_s, bool)
            or not math.isfinite(float(max_wait_s))
            or max_wait_s <= 0.0
        ):
            raise ValueError("invalid_session_wait")

        operation_id = str(uuid.uuid4())
        returned_active = False
        primary: BaseException | None = None
        async with self._transition_lock:
            await self._reconcile_idle_session_locked()
            self._begin_operation(operation_id)
            try:
                response = await self._session_call(
                    "/session/enqueue",
                    {"purpose": purpose, "operation_id": operation_id},
                )
                ticket = response.get("ticket")
                if (
                    response.get("status") != "queued"
                    or not isinstance(ticket, str)
                    or not ticket
                ):
                    self._bad_session_response()
                self._validate_request_bound_wait_response(
                    response,
                    ticket=ticket,
                    operation_id=operation_id,
                )
                self._remember_acquire_or_wait(response, operation_id)

                position = response.get("position")
                if progress_cb is not None and type(position) is int and position > 0:
                    await progress_cb(
                        0.0,
                        None if max_wait_s is None else float(max_wait_s),
                        f"En cola (posición {position}) para {purpose[:48]}",
                    )

                started = _monotonic()
                deadline = (
                    None
                    if max_wait_s is None
                    else started + float(max_wait_s)
                )
                while True:
                    if deadline is None:
                        wait_slice = 30.0
                    else:
                        remaining = deadline - _monotonic()
                        if remaining <= 0.0:
                            raise ControlClientError(
                                "session_wait_timeout",
                                request_stage="post_request",
                                http_bytes_sent=1,
                            )
                        wait_slice = min(30.0, remaining)
                    response = await self._session_call(
                        "/session/wait",
                        {"ticket": ticket, "timeout_s": wait_slice},
                        timeout_s=max(5.0, wait_slice + 1.0),
                    )
                    self._validate_request_bound_wait_response(
                        response,
                        ticket=ticket,
                        operation_id=operation_id,
                    )
                    self._remember_acquire_or_wait(response, operation_id)
                    if response.get("status") == "active":
                        returned_active = True
                        return self._with_own_identity(response)
                    if response.get("status") != "queued":
                        self._bad_session_response()
                    if progress_cb is not None:
                        elapsed = max(0.0, _monotonic() - started)
                        await progress_cb(
                            elapsed,
                            None if max_wait_s is None else float(max_wait_s),
                            f"En cola (posición {response.get('position')}) para {purpose[:48]}",
                        )
            except BaseException as error:
                primary = error
                raise
            finally:
                if not returned_active:
                    safe_unsent = (
                        isinstance(primary, ControlClientError)
                        and primary.code == "daemon_unavailable"
                        and primary.request_stage == "pre_request"
                        and primary.http_bytes_sent == 0
                    )
                    if safe_unsent:
                        self._clear_operation(operation_id)
                    else:
                        degradation = await self._cleanup_operation_never_raises(
                            operation_id
                        )
                        if degradation:
                            if primary is not None:
                                primary.add_note(degradation)
                            else:
                                raise ControlClientError(
                                    "session_cleanup_failed",
                                    request_stage="post_request",
                                    http_bytes_sent=1,
                                )
