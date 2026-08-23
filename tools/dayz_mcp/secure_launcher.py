from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Iterable, Sequence

from dayz_mcp.control_client import ControlClient, ControlIdentity
from dayz_mcp.launcher_registry import open_approved_launcher
from dayz_mcp.native_broker_protocol import BrokerKind, encode_request
from dayz_mcp.native_launcher_transaction import execute_native_launcher_transaction
from dayz_mcp.daemon_policy_contract import AccreditedDaemonPolicy
from dayz_mcp.normal_daemon_policy import (
    load_normal_daemon_policy,
    serialize_normal_daemon_policy,
)


DEFAULT_MAX_WAIT_S: None = None
MAX_PUBLIC_REQUEST_BYTES = 65_536


class _IncrementalRedactor:
    def __init__(self, secrets: Iterable[bytes]) -> None:
        self._secrets = tuple(
            sorted({value for value in secrets if value}, key=len, reverse=True)
        )
        self._pending = bytearray()
        self._max_secret = max((len(value) for value in self._secrets), default=1)

    def feed(self, chunk: bytes) -> bytes:
        self._pending.extend(chunk)
        return self._consume(final=False)

    def flush(self) -> bytes:
        return self._consume(final=True)

    def _consume(self, *, final: bool) -> bytes:
        # pop(0) memmoves the unread tail on every unmatched byte.
        # find() on each miss scans the remaining buffer, so a repeated
        # secret plus an absent one is O(n^2). Advance one byte on
        # mismatch and drop the consumed prefix once. The suffix shorter
        # than the longest secret stays in pending so a secret split
        # across feed() calls still matches.
        pending = self._pending
        if not pending:
            return b""
        output = bytearray()
        index = 0
        length = len(pending)
        secrets = self._secrets
        max_secret = self._max_secret
        limit = length if final else length - max_secret + 1
        while index < limit:
            matched = next(
                (
                    secret
                    for secret in secrets
                    if pending.startswith(secret, index)
                ),
                None,
            )
            if matched is not None:
                index += len(matched)
                output.extend(b"[REDACTED]")
            else:
                output.append(pending[index])
                index += 1
        if index:
            del pending[:index]
        return bytes(output)


async def execute_secure_launcher_request(
    raw_request: bytes,
    *,
    opened_launcher: object,
    verified_bundle: object,
    control_client: object,
    daemon_policy: AccreditedDaemonPolicy,
    output_sink: Callable[[str, bytes], None],
    max_wait_s: float | None = DEFAULT_MAX_WAIT_S,
    queue_progress_cb: Callable[
        [float, float | None, str | None], Awaitable[None]
    ]
    | None = None,
    execution_started_cb: Callable[[], Awaitable[None]] | None = None,
) -> int:
    sealed_policies = getattr(verified_bundle, "sealed_policies", None)
    if (
        type(raw_request) is not bytes
        or type(sealed_policies) is not tuple
        or not callable(output_sink)
        or queue_progress_cb is not None
        and not callable(queue_progress_cb)
        or execution_started_cb is not None
        and not callable(execution_started_cb)
    ):
        raise ValueError("invalid_secure_launcher_request")

    async def consume_registered_launcher(
        *,
        canonical_request: bytes,
        request_sha256: str,
        client_identity_json: str,
        lease_token: str,
        cancel_event: asyncio.Event,
        accredited_paths: object,
        heartbeat_supervisor: object,
    ) -> int:
        del accredited_paths, heartbeat_supervisor
        from dayz_mcp import native_launcher_backend

        broker_frame = encode_request(
            BrokerKind.PRIVATE_WORKER,
            {"request_sha256": request_sha256},
            stdin=canonical_request,
        )
        encoded_secrets = (
            client_identity_json.encode("utf-8"),
            client_identity_json.encode("utf-16le"),
            lease_token.encode("utf-8"),
            lease_token.encode("utf-16le"),
        )
        redactors = {
            "stdout": _IncrementalRedactor(encoded_secrets),
            "stderr": _IncrementalRedactor(encoded_secrets),
        }

        def sanitized_sink(channel: str, chunk: bytes) -> None:
            if channel not in redactors or type(chunk) is not bytes:
                raise ValueError("invalid_native_launcher_output")
            sanitized = redactors[channel].feed(chunk)
            if sanitized:
                output_sink(channel, sanitized)

        if execution_started_cb is not None:
            await execution_started_cb()
        try:
            daemon_policy_json = serialize_normal_daemon_policy(daemon_policy)
            return await native_launcher_backend.launch_registered_native(
                opened_launcher,
                verified_bundle=verified_bundle,
                canonical_request=broker_frame,
                lease_token=lease_token,
                identity_json=client_identity_json,
                daemon_policy_json=daemon_policy_json,
                cancel_event=cancel_event,
                output_sink=sanitized_sink,
            )
        finally:
            for channel, redactor in redactors.items():
                sanitized = redactor.flush()
                if sanitized:
                    output_sink(channel, sanitized)

    return await execute_native_launcher_transaction(
        raw_request,
        sealed_policies=sealed_policies,
        control_client=control_client,
        consumer=consume_registered_launcher,
        max_wait_s=None if max_wait_s is None else float(max_wait_s),
        queue_progress_cb=queue_progress_cb,
    )


def run_secure_launcher(
    launcher_id: str,
    *,
    max_wait_s: float | None = DEFAULT_MAX_WAIT_S,
) -> int:
    if max_wait_s is not None and (
        isinstance(max_wait_s, bool)
        or not isinstance(max_wait_s, (int, float))
        or not math.isfinite(float(max_wait_s))
        or float(max_wait_s) <= 0.0
    ):
        raise ValueError("invalid_launcher_wait")
    raw_request = _read_public_request()
    with open_approved_launcher(launcher_id) as opened:
        opened.validate_native_pe()
        with _load_verified_bundle(opened) as bundle:
            identity = ControlIdentity(
                platform="unknown",
                pid=os.getpid(),
                ppid=os.getppid(),
                started_at_utc=datetime.now(timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                session_id=str(uuid.uuid4()),
                task_label=f"secure-launcher:{launcher_id}"[:120],
            )
            daemon_policy = load_normal_daemon_policy()
            control_client = ControlClient(
                policy=daemon_policy,
                identity=identity,
            )
            targets = {"stdout": sys.stdout.buffer, "stderr": sys.stderr.buffer}

            def stream_output(channel: str, chunk: bytes) -> None:
                if channel not in targets or type(chunk) is not bytes:
                    raise ValueError("invalid_native_launcher_output")
                targets[channel].write(chunk)
                targets[channel].flush()

            return asyncio.run(
                execute_secure_launcher_request(
                    raw_request,
                    opened_launcher=opened,
                    verified_bundle=bundle,
                    control_client=control_client,
                    daemon_policy=daemon_policy,
                    output_sink=stream_output,
                    max_wait_s=max_wait_s,
                )
            )


def _read_public_request() -> bytes:
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        raise ValueError("invalid_dayz_test_request")
    raw = stream.read(MAX_PUBLIC_REQUEST_BYTES + 1)
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PUBLIC_REQUEST_BYTES:
        raise ValueError("invalid_dayz_test_request")
    return raw


def _load_verified_bundle(opened_launcher: object) -> object:
    from dayz_mcp.native_bundle import load_verified_bundle

    return load_verified_bundle(opened_launcher)


def load_verified_bundle(opened_launcher: object) -> object:
    return _load_verified_bundle(opened_launcher)


class _GenericParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        print("secure launcher: invalid arguments", file=sys.stderr, flush=True)
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _GenericParser(
        description="Run one registered native DayZ consumer after a durable lease wait"
    )
    parser.add_argument("launcher_id")
    parser.add_argument("--max-wait-s", type=float, default=DEFAULT_MAX_WAIT_S)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run_secure_launcher(
            args.launcher_id,
            max_wait_s=args.max_wait_s,
        )
    except KeyboardInterrupt:
        return 130
    except BaseException as error:
        print(
            f"secure launcher failed: {type(error).__name__}",
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
