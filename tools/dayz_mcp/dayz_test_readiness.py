"""Exact managed-run UDP readiness check for the private dayz-test worker."""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from dayz_mcp import native_broker_protocol


READINESS_ERROR_CODES: frozenset[str] = frozenset(
    {
        "readiness_status_invalid",
        "readiness_run_not_running",
        "readiness_server_process_invalid",
        "readiness_server_pid_dead",
        "readiness_udp_snapshot_invalid",
        "readiness_udp_foreign_owner",
        "readiness_probe_failed",
        "readiness_timeout",
    }
)


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    error_code: str | None

    def __post_init__(self) -> None:
        if (
            type(self.ready) is not bool
            or (self.ready and self.error_code is not None)
            or (
                not self.ready
                and (
                    type(self.error_code) is not str
                    or self.error_code not in READINESS_ERROR_CODES
                )
            )
        ):
            raise ValueError("invalid_readiness_result")


class _Broker(Protocol):
    async def invoke(self, frame: bytes) -> dict[str, object]: ...


def _valid_uuid4(value: object) -> bool:
    if not isinstance(value, str) or value != value.casefold():
        return False
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _server_pid(
    response: object,
    run_id: str,
) -> tuple[int | None, str | None]:
    if not isinstance(response, dict):
        return None, "readiness_status_invalid"
    if response.get("error") is not None or response.get("ok") is False:
        return None, "readiness_status_invalid"
    runs = response.get("runs")
    if not isinstance(runs, list):
        return None, "readiness_status_invalid"
    matching = [
        item for item in runs if isinstance(item, dict) and item.get("run_id") == run_id
    ]
    if len(matching) != 1 or matching[0].get("state") != "RUNNING":
        return None, "readiness_run_not_running"
    processes = matching[0].get("processes")
    if not isinstance(processes, list):
        return None, "readiness_server_process_invalid"
    servers = [
        item
        for item in processes
        if isinstance(item, dict) and item.get("role") == "server"
    ]
    if len(servers) != 1:
        return None, "readiness_server_process_invalid"
    pid = servers[0].get("pid")
    if type(pid) is not int or pid <= 0:
        return None, "readiness_server_process_invalid"
    return pid, None


def _endpoint_port(connection: object) -> int | None:
    address = getattr(connection, "laddr", None)
    port = getattr(address, "port", None)
    if type(port) is int:
        return port
    if isinstance(address, tuple) and len(address) >= 2 and type(address[1]) is int:
        return address[1]
    return None


async def wait_for_owned_udp(
    broker: _Broker,
    run_id: str,
    port: int,
    timeout_s: int | float,
    *,
    psutil_module: object,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ReadinessResult:
    if (
        not _valid_uuid4(run_id)
        or type(port) is not int
        or not 1024 <= port <= 65530
        or isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(float(timeout_s))
        or not 0.0 < float(timeout_s) <= 3600.0
        or not callable(getattr(psutil_module, "pid_exists", None))
        or not callable(getattr(psutil_module, "net_connections", None))
    ):
        raise ValueError("invalid_dayz_test_readiness")
    try:
        deadline = monotonic() + float(timeout_s)
        while monotonic() < deadline:
            frame = native_broker_protocol.encode_request(
                native_broker_protocol.BrokerKind.LIFECYCLE_CLI,
                {"command": "status", "launch_operation_id": None, "run_id": run_id},
            )
            response = await broker.invoke(frame)
            pid, error_code = _server_pid(response, run_id)
            if error_code is not None:
                return ReadinessResult(False, error_code)
            if pid is None:
                return ReadinessResult(False, "readiness_probe_failed")
            if not psutil_module.pid_exists(pid):
                return ReadinessResult(False, "readiness_server_pid_dead")
            connections = psutil_module.net_connections(kind="udp")
            if not isinstance(connections, list):
                return ReadinessResult(False, "readiness_udp_snapshot_invalid")
            bound = [item for item in connections if _endpoint_port(item) == port]
            owners = [getattr(item, "pid", None) for item in bound]
            if any(
                type(owner) is int and owner > 0 and owner != pid
                for owner in owners
            ):
                return ReadinessResult(False, "readiness_udp_foreign_owner")
            if bound and all(owner == pid for owner in owners):
                return ReadinessResult(True, None)
            remaining = deadline - monotonic()
            if remaining <= 0.0:
                break
            await sleep(min(2.0, remaining))
    except asyncio.CancelledError:
        raise
    except Exception:
        return ReadinessResult(False, "readiness_probe_failed")
    return ReadinessResult(False, "readiness_timeout")
