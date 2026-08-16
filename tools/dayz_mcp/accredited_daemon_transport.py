"""Authenticated daemon HTTP after accrediting the owner of the exact socket."""

from __future__ import annotations

import http.client
import math
import ntpath
import time
import urllib.parse
from typing import Callable

from dayz_mcp.daemon_contract import argv_targets_port, classify_dayz_argv
from dayz_mcp.native_process_guard import NativeProcessGuard, identity_hashes
from dayz_mcp.native_process_snapshot import (
    command_argv_of,
    full_image_path_of,
    same_path,
    working_directory_of,
)

try:
    import psutil
except ImportError:  # pragma: no cover - callers fail closed without psutil.
    psutil = None  # type: ignore[assignment]


MAX_AUTHENTICATED_RESPONSE_BYTES = 4 * 1024 * 1024


class AccreditedTransportError(ConnectionError):
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


def _transport_error(error: Exception, *, request_started: bool) -> AccreditedTransportError:
    code = (
        str(error)
        if str(error)
        in {
            "daemon_identity_unverified",
            "daemon_request_deadline_exceeded",
            "daemon_response_too_large",
        }
        else "daemon_transport_failure"
    )
    return AccreditedTransportError(
        code,
        request_stage="post_request" if request_started else "pre_request",
        http_bytes_sent=1 if request_started else 0,
    )


def _endpoint(address: object) -> tuple[str, int] | None:
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
    else:
        host = getattr(address, "ip", None)
        port = getattr(address, "port", None)
    if (
        not isinstance(host, str)
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        return None
    return host, port


def _connected_server_pid(
    sock: object,
    *,
    connections_fn: Callable[[], object],
) -> int | None:
    """Return the unique owner of this exact established loopback connection."""
    if psutil is None:
        return None
    try:
        client_endpoint = _endpoint(sock.getsockname())  # type: ignore[attr-defined]
        server_endpoint = _endpoint(sock.getpeername())  # type: ignore[attr-defined]
        connections = connections_fn()
    except Exception:
        return None
    if (
        client_endpoint is None
        or server_endpoint is None
        or client_endpoint[0] != "127.0.0.1"
        or server_endpoint[0] != "127.0.0.1"
        or not isinstance(connections, (list, tuple))
    ):
        return None
    owners: set[int] = set()
    for connection in connections:
        if getattr(connection, "status", None) != psutil.CONN_ESTABLISHED:
            continue
        if (
            _endpoint(getattr(connection, "laddr", None)) != server_endpoint
            or _endpoint(getattr(connection, "raddr", None)) != client_endpoint
        ):
            continue
        pid = getattr(connection, "pid", None)
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            owners.add(pid)
    return next(iter(owners)) if len(owners) == 1 else None


def _connected_daemon_identity_verified(
    sock: object,
    port: int,
    *,
    expected_executable: str,
    expected_argv: list[str],
    expected_cwd: str,
    connections_fn: Callable[[], object],
    get_executable: Callable[[int], str | None],
    get_argv: Callable[[int], list[str] | None],
    get_cwd: Callable[[int], str | None],
    guard: object,
) -> bool:
    expected_kind = classify_dayz_argv(expected_argv)
    if (
        not isinstance(expected_executable, str)
        or not expected_executable
        or not ntpath.isabs(expected_executable)
        or not isinstance(expected_argv, list)
        or not expected_argv
        or any(not isinstance(value, str) or not value for value in expected_argv)
        or not isinstance(expected_cwd, str)
        or not expected_cwd
        or not ntpath.isabs(expected_cwd)
        or expected_kind not in {"normal_daemon", "p0s_bootstrap_daemon"}
        or not argv_targets_port(expected_argv, port)
    ):
        return False
    pid_a = _connected_server_pid(sock, connections_fn=connections_fn)
    if not isinstance(pid_a, int) or isinstance(pid_a, bool) or pid_a <= 0:
        return False
    executable = get_executable(pid_a)
    argv = get_argv(pid_a)
    cwd = get_cwd(pid_a)
    if (
        not same_path(executable, expected_executable)
        or not isinstance(argv, list)
        or argv != expected_argv
        or not same_path(cwd, expected_cwd)
    ):
        return False
    try:
        expected_hashes = identity_hashes(expected_executable, expected_argv)
        snapshot_a = guard.snapshot(pid_a)  # type: ignore[attr-defined]
    except Exception:
        return False
    identity_fields = (
        "pid",
        "creation_time_utc",
        "executable_sha256",
        "command_line_sha256",
        "identity_scheme",
    )
    if (
        not isinstance(snapshot_a, dict)
        or snapshot_a.get("identity_complete") is not True
        or snapshot_a.get("identity_scheme") != "psutil-argv-v2"
        or snapshot_a.get("pid") != pid_a
        or snapshot_a.get("executable_sha256")
        != expected_hashes["executable_sha256"]
        or snapshot_a.get("command_line_sha256")
        != expected_hashes["command_line_sha256"]
    ):
        return False
    pid_b = _connected_server_pid(sock, connections_fn=connections_fn)
    if pid_b != pid_a:
        return False
    try:
        executable_b = get_executable(pid_b)
        argv_b = get_argv(pid_b)
        cwd_b = get_cwd(pid_b)
        snapshot_b = guard.snapshot(pid_b)  # type: ignore[attr-defined]
    except Exception:
        return False
    return (
        _connected_server_pid(sock, connections_fn=connections_fn) == pid_a
        and same_path(executable_b, expected_executable)
        and argv_b == expected_argv
        and same_path(cwd_b, expected_cwd)
        and isinstance(snapshot_b, dict)
        and snapshot_b.get("identity_complete") is True
        and all(snapshot_b.get(field) == snapshot_a.get(field) for field in identity_fields)
        and snapshot_b.get("executable_sha256")
        == expected_hashes["executable_sha256"]
        and snapshot_b.get("command_line_sha256")
        == expected_hashes["command_line_sha256"]
    )


def verified_daemon_http_request(
    *,
    host: str,
    port: int,
    key: str,
    method: str,
    path: str,
    query: dict[str, str] | None,
    body: bytes | None,
    headers: dict[str, str] | None,
    deadline: float,
    expected_executable: str,
    expected_argv: list[str],
    expected_cwd: str,
    connection_factory: Callable[[str, int, float], object] | None = None,
    connections_fn: Callable[[], object] | None = None,
    get_executable: Callable[[int], str | None] | None = None,
    get_argv: Callable[[int], list[str] | None] | None = None,
    get_cwd: Callable[[int], str | None] | None = None,
    guard: object | None = None,
    time_fn: Callable[[], float] | None = None,
    max_response_bytes: int = MAX_AUTHENTICATED_RESPONSE_BYTES,
) -> tuple[int, bytes]:
    """Send authenticated HTTP only after accrediting this connected socket."""
    now = time_fn or time.monotonic
    if (
        host != "127.0.0.1"
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
        or not isinstance(key, str)
        or not key
        or not isinstance(method, str)
        or not method
        or not isinstance(path, str)
        or not path.startswith("/")
        or "?" in path
        or not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
        or not isinstance(max_response_bytes, int)
        or isinstance(max_response_bytes, bool)
        or max_response_bytes <= 0
    ):
        raise ValueError("invalid_verified_daemon_request")
    remaining = float(deadline) - now()
    if remaining <= 0.0:
        raise TimeoutError("daemon_request_deadline_exceeded")
    factory = connection_factory or http.client.HTTPConnection
    connection: object | None = None
    request_started = False
    caught_error = False
    try:
        connection = factory(host, port, remaining)
        connection.connect()  # type: ignore[attr-defined]
        sock = getattr(connection, "sock", None)
        if sock is None or not _connected_daemon_identity_verified(
            sock,
            port,
            expected_executable=expected_executable,
            expected_argv=expected_argv,
            expected_cwd=expected_cwd,
            connections_fn=connections_fn
            or (lambda: psutil.net_connections(kind="tcp")),  # type: ignore[union-attr]
            get_executable=get_executable or full_image_path_of,
            get_argv=get_argv or command_argv_of,
            get_cwd=get_cwd or working_directory_of,
            guard=guard or NativeProcessGuard(),
        ):
            raise ConnectionError("daemon_identity_unverified")

        remaining = float(deadline) - now()
        if remaining <= 0.0:
            raise TimeoutError("daemon_request_deadline_exceeded")
        settimeout = getattr(sock, "settimeout", None)
        if callable(settimeout):
            settimeout(remaining)
        params = dict(query or {})
        params["key"] = key
        target = path + "?" + urllib.parse.urlencode(params)
        request_started = True
        connection.request(  # type: ignore[attr-defined]
            method, target, body=body, headers=dict(headers or {})
        )
        response = connection.getresponse()  # type: ignore[attr-defined]
        response_body = response.read(max_response_bytes + 1)
        if len(response_body) > max_response_bytes:
            raise ValueError("daemon_response_too_large")
        return int(response.status), response_body
    except AccreditedTransportError:
        caught_error = True
        raise
    except Exception as error:
        caught_error = True
        raise _transport_error(error, request_started=request_started) from error
    finally:
        if connection is not None:
            try:
                connection.close()  # type: ignore[attr-defined]
            except Exception as error:
                if not caught_error:
                    raise _transport_error(
                        error, request_started=request_started
                    ) from error
