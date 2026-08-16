"""Request-bound native launcher transaction without any process creation surface."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from dayz_mcp import dayz_test_request, lease_supervisor, request_path_authority


class NativeLauncherTransactionError(RuntimeError):
    pass


class _ControlClient(Protocol):
    async def session_acquire_wait(
        self,
        purpose: str,
        max_wait_s: float | None = None,
        progress_cb: Callable[[float, float | None, str | None], Awaitable[None]]
        | None = None,
    ) -> dict[str, object]: ...

    async def session_heartbeat(self, lease_token: str) -> dict[str, object]: ...

    async def session_release(self, lease_token: str) -> dict[str, object]: ...

    async def session_status(self) -> dict[str, object]: ...


def _validate_grant(response: object) -> tuple[str, str, str]:
    if not isinstance(response, dict):
        raise NativeLauncherTransactionError("invalid_session_grant")
    lease_token = response.get("lease_token")
    lease_id = response.get("lease_id")
    client_identity_json = response.get("client_identity_json")
    if (
        response.get("status") != "active"
        or not isinstance(lease_token, str)
        or not lease_token
        or not isinstance(lease_id, str)
        or not lease_id
        or not isinstance(client_identity_json, str)
        or not client_identity_json
    ):
        raise NativeLauncherTransactionError("invalid_session_grant")
    return lease_token, lease_id, client_identity_json


async def _finish_task(task: asyncio.Task[object] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:
        return


async def _cleanup_transaction(
    *,
    consumer_task: asyncio.Task[int] | None,
    failure_task: asyncio.Task[lease_supervisor.LeaseHeartbeatError] | None,
    cancel_event: asyncio.Event,
    supervisor: lease_supervisor.LeaseHeartbeatSupervisor,
    control_client: _ControlClient,
    lease_token: str,
) -> BaseException | None:
    cancel_event.set()
    await _finish_task(failure_task)
    await _finish_task(consumer_task)
    cleanup_error: BaseException | None = None
    try:
        await supervisor.stop()
    except BaseException as error:
        cleanup_error = error
    try:
        await lease_supervisor.protected_release_and_verify(
            control_client, lease_token
        )
    except BaseException as error:
        if cleanup_error is None:
            cleanup_error = error
    return cleanup_error


async def execute_native_launcher_transaction(
    raw_request: bytes,
    *,
    sealed_policies: tuple[
        request_path_authority.SealedRequestProjectPolicy, ...
    ],
    control_client: _ControlClient,
    consumer: Callable[..., Awaitable[int]],
    max_wait_s: float | None = None,
    queue_progress_cb: Callable[
        [float, float | None, str | None], Awaitable[None]
    ]
    | None = None,
) -> int:
    if type(sealed_policies) is not tuple or not callable(consumer):
        raise ValueError("invalid_native_launcher_transaction")
    semantic_policies = tuple(item.policy for item in sealed_policies)
    parsed = dayz_test_request.parse_dayz_test_request(
        raw_request, policies=semantic_policies
    )

    with request_path_authority.accredit_request_paths(
        parsed, policies=sealed_policies
    ) as accredited_paths:
        response = await control_client.session_acquire_wait(
            "dayz-test",
            max_wait_s=max_wait_s,
            progress_cb=queue_progress_cb,
        )
        try:
            lease_token, lease_id, client_identity_json = _validate_grant(response)
        except NativeLauncherTransactionError as error:
            recoverable_token = (
                response.get("lease_token")
                if isinstance(response, dict)
                and response.get("status") == "active"
                else None
            )
            if isinstance(recoverable_token, str) and recoverable_token:
                try:
                    await lease_supervisor.protected_release_and_verify(
                        control_client, recoverable_token
                    )
                except BaseException as cleanup_error:
                    error.add_note(
                        "launcher cleanup degraded: "
                        f"{type(cleanup_error).__name__}"
                    )
            raise
        supervisor = lease_supervisor.LeaseHeartbeatSupervisor(
            control_client,
            lease_token=lease_token,
            lease_id=lease_id,
        )
        cancel_event = asyncio.Event()
        consumer_task: asyncio.Task[int] | None = None
        failure_task: asyncio.Task[lease_supervisor.LeaseHeartbeatError] | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        result: int | None = None
        supervisor.start()
        try:
            consumer_task = asyncio.create_task(
                consumer(
                    canonical_request=parsed.canonical_bytes,
                    request_sha256=parsed.sha256,
                    client_identity_json=client_identity_json,
                    lease_token=lease_token,
                    cancel_event=cancel_event,
                    accredited_paths=accredited_paths,
                    heartbeat_supervisor=supervisor,
                )
            )
            failure_task = asyncio.create_task(supervisor.wait_failed())
            done, _pending = await asyncio.wait(
                (consumer_task, failure_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if failure_task in done:
                cancel_event.set()
                await _finish_task(consumer_task)
                raise failure_task.result()
            result = consumer_task.result()
            if type(result) is not int or not 0 <= result <= 255:
                raise NativeLauncherTransactionError("invalid_consumer_exit")
            supervisor.ensure_healthy()
        except BaseException as error:
            primary = error
        finally:
            cleanup_task = asyncio.create_task(
                _cleanup_transaction(
                    consumer_task=consumer_task,
                    failure_task=failure_task,
                    cancel_event=cancel_event,
                    supervisor=supervisor,
                    control_client=control_client,
                    lease_token=lease_token,
                )
            )
            delayed_cancellation: asyncio.CancelledError | None = None
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as error:
                    delayed_cancellation = error
            cleanup_error = cleanup_task.result()
            if delayed_cancellation is not None and primary is None:
                primary = delayed_cancellation

        if primary is not None:
            if cleanup_error is not None:
                primary.add_note(
                    f"launcher cleanup degraded: {type(cleanup_error).__name__}"
                )
            raise primary
        if cleanup_error is not None:
            raise cleanup_error
        if result is None:
            raise NativeLauncherTransactionError("invalid_consumer_exit")
        return result
