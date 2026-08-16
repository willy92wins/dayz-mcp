"""Fixed-cadence lease heartbeat and terminal cleanup for the native launcher."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Protocol


HEARTBEAT_INTERVAL_S = 45.0
SESSION_TTL_S = 120.0


class _SessionClient(Protocol):
    async def session_heartbeat(self, lease_token: str) -> dict[str, object]: ...

    async def session_release(self, lease_token: str) -> dict[str, object]: ...

    async def session_status(self) -> dict[str, object]: ...


class LeaseHeartbeatError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LeaseCleanupError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("session_close_degraded")
        self.code = "session_close_degraded"


async def _wait_for_stop(stop: asyncio.Event, delay: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        return False
    return True


class LeaseHeartbeatSupervisor:
    def __init__(
        self,
        client: _SessionClient,
        *,
        lease_token: str,
        lease_id: str,
        monotonic: Callable[[], float] = time.monotonic,
        wait_fn: Callable[[asyncio.Event, float], Awaitable[bool]] = _wait_for_stop,
    ) -> None:
        if (
            not isinstance(lease_token, str)
            or not lease_token
            or not isinstance(lease_id, str)
            or not lease_id
            or not callable(monotonic)
            or not callable(wait_fn)
        ):
            raise ValueError("invalid_lease_supervisor")
        self._client = client
        self._lease_token = lease_token
        self._lease_id = lease_id
        self._monotonic = monotonic
        self._wait_fn = wait_fn
        self._stop = asyncio.Event()
        self._failed = asyncio.Event()
        self._failure: LeaseHeartbeatError | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("lease_supervisor_already_started")
        self._task = asyncio.create_task(self._run())

    def _fail(self, code: str) -> None:
        if self._failure is None:
            self._failure = LeaseHeartbeatError(code)
            self._failed.set()

    def _validate_heartbeat(self, response: object) -> None:
        if not isinstance(response, dict):
            self._fail("lease_heartbeat_failed")
            return
        expires_in_s = response.get("expires_in_s")
        if (
            response.get("status") != "active"
            or response.get("lease_token") != self._lease_token
            or response.get("lease_id") != self._lease_id
            or not isinstance(expires_in_s, (int, float))
            or isinstance(expires_in_s, bool)
            or not math.isfinite(float(expires_in_s))
            or not 0.0 < float(expires_in_s) <= SESSION_TTL_S
        ):
            self._fail("lease_heartbeat_failed")

    async def _run(self) -> None:
        next_due = self._monotonic() + HEARTBEAT_INTERVAL_S
        try:
            while not self._stop.is_set():
                delay = max(0.0, next_due - self._monotonic())
                if await self._wait_fn(self._stop, delay):
                    return
                now = self._monotonic()
                if now < next_due:
                    continue
                if now >= next_due + HEARTBEAT_INTERVAL_S:
                    self._fail("lease_heartbeat_tardy")
                    return
                try:
                    response = await self._client.session_heartbeat(
                        self._lease_token
                    )
                except Exception:
                    self._fail("lease_heartbeat_failed")
                    return
                self._validate_heartbeat(response)
                if self._failure is not None:
                    return
                next_due += HEARTBEAT_INTERVAL_S
                if self._monotonic() >= next_due:
                    self._fail("lease_heartbeat_tardy")
                    return
        except asyncio.CancelledError:
            self._fail("lease_heartbeat_failed")
            raise
        except Exception:
            self._fail("lease_heartbeat_failed")

    async def wait_failed(self) -> LeaseHeartbeatError:
        await self._failed.wait()
        if self._failure is None:
            raise RuntimeError("lease_supervisor_failure_missing")
        return self._failure

    def ensure_healthy(self) -> None:
        if self._failure is not None:
            raise self._failure

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task


def validate_terminal_session_status(status: object) -> None:
    if not isinstance(status, dict):
        raise LeaseCleanupError()
    own = status.get("self")
    pending = status.get("pending_commands")
    if (
        not isinstance(own, dict)
        or own.get("state") != "none"
        or "ticket" in own
        or type(pending) is not int
        or pending != 0
    ):
        raise LeaseCleanupError()


async def _release_and_verify(
    client: _SessionClient, lease_token: str
) -> dict[str, object]:
    release_failed = False
    try:
        await client.session_release(lease_token)
    except Exception:
        release_failed = True
    try:
        status = await client.session_status()
    except Exception:
        raise LeaseCleanupError() from None
    if release_failed:
        raise LeaseCleanupError()
    validate_terminal_session_status(status)
    return status


async def protected_release_and_verify(
    client: _SessionClient, lease_token: str
) -> dict[str, object]:
    if not isinstance(lease_token, str) or not lease_token:
        raise ValueError("invalid_session_lease")
    cleanup = asyncio.create_task(_release_and_verify(client, lease_token))
    delayed_cancellation: asyncio.CancelledError | None = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as error:
            delayed_cancellation = error
    cleanup_error: BaseException | None = None
    result: dict[str, object] | None = None
    try:
        result = cleanup.result()
    except BaseException as error:
        cleanup_error = error
    if delayed_cancellation is not None:
        if cleanup_error is not None:
            delayed_cancellation.add_note(
                f"lease cleanup degraded: {type(cleanup_error).__name__}"
            )
        raise delayed_cancellation
    if cleanup_error is not None:
        raise cleanup_error
    if result is None:
        raise LeaseCleanupError()
    return result
