"""One idle accredited connection per daemon identity, for the MCP client only.

The sealed ``accredited_daemon_transport.verified_daemon_http_request`` still
opens, accredits and closes a socket on every call. This module is the client
path (``daemon_credential._send``): the first use of a socket is accredited
with the sealed check, and later requests on that same TCP connection are not.
A connected socket cannot be handed to another process, so skipping the
psutil walks is the same peer the first check accepted.

Reuse is refused when the socket has been idle longer than
``IDLE_REUSE_LIMIT_S``, when the peer has already written or closed, when the
response is not an explicit keep-alive, or when another thread holds the idle
socket. That last request uses the sealed one-shot path.

A failure on a socket we already decided to reuse is reported as
``post_request`` with ``http_bytes_sent`` 1, including a reset from a server
that closed between the idle check and the write. ``ClientRuntime._call``
retries only ``pre_request`` with zero bytes sent, so that failure does not
send a second ``/enqueue``.
"""

from __future__ import annotations

import http.client
import math
import select
import threading
import time
import urllib.parse
from typing import Callable

from dayz_mcp import accredited_daemon_transport as _sealed_transport
from dayz_mcp.accredited_daemon_transport import (
    MAX_AUTHENTICATED_RESPONSE_BYTES,
    AccreditedTransportError,
    _connected_daemon_identity_verified,
    _transport_error,
)
from dayz_mcp.native_process_guard import NativeProcessGuard
from dayz_mcp.native_process_snapshot import (
    command_argv_of,
    full_image_path_of,
    working_directory_of,
)

try:
    import psutil
except ImportError:  # pragma: no cover - callers fail closed without psutil.
    psutil = None  # type: ignore[assignment]


# Discard an idle socket after this much silence. A tool burst fits; an idle
# client does not keep the accredited socket, and the daemon's inter-request
# wait is longer than this (loopback.KEEPALIVE_IDLE_S) so a request the client
# still considers fresh is not answered by a server that already hung up.
IDLE_REUSE_LIMIT_S = 5.0

# Captured at import. A later patch of the module attribute is a test double:
# keepalive_daemon_http_request calls that double and does not pool.
_SEALED_REQUEST = _sealed_transport.verified_daemon_http_request
_PoolKey = tuple[str, int, str, tuple[str, ...], str]


class _IdleSlot:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.connection: object | None = None
        self.idle_since: float | None = None
        self.busy = False


_registry_lock = threading.Lock()
_slots: dict[_PoolKey, _IdleSlot] = {}


def clear_idle_connections() -> None:
    """Drop every pooled socket. Tests use this; a live request keeps its slot."""
    with _registry_lock:
        slots = list(_slots.values())
        _slots.clear()
    for slot in slots:
        with slot.lock:
            connection = slot.connection
            slot.connection = None
            slot.idle_since = None
            slot.busy = False
        _close_quiet(connection)


def _slot_for(key: _PoolKey) -> _IdleSlot:
    with _registry_lock:
        slot = _slots.get(key)
        if slot is None:
            slot = _IdleSlot()
            _slots[key] = slot
        return slot


def _close_quiet(connection: object | None) -> None:
    if connection is None:
        return
    close = getattr(connection, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return


def _park(slot: _IdleSlot, connection: object, idle_since: float) -> None:
    with slot.lock:
        slot.connection = connection
        slot.idle_since = idle_since
        slot.busy = False


def _discard(slot: _IdleSlot, connection: object | None) -> None:
    _close_quiet(connection)
    with slot.lock:
        slot.connection = None
        slot.idle_since = None
        slot.busy = False


def _validate_request(
    *,
    host: str,
    port: int,
    key: str,
    method: str,
    path: str,
    deadline: float,
    max_response_bytes: int,
) -> None:
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


def _deadline_error() -> AccreditedTransportError:
    return AccreditedTransportError(
        "daemon_request_deadline_exceeded",
        request_stage="pre_request",
        http_bytes_sent=0,
    )


def _socket_already_readable(sock: object) -> bool:
    """True when the peer has written or hung up, or the socket cannot be checked.

    A readable idle socket is either a close or bytes we did not ask for.
    Sending on it would ride a dead connection or desynchronise the stream.
    """
    fileno = getattr(sock, "fileno", None)
    if not callable(fileno):
        return True
    try:
        descriptor = fileno()
    except Exception:
        return True
    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 0:
        return True
    try:
        readable, _writable, _errors = select.select([sock], [], [], 0)
    except (OSError, ValueError, TypeError):
        return True
    return bool(readable)


def _candidate_unusable(
    connection: object,
    idle_since: float | None,
    *,
    now: float,
    peer_pending_fn: Callable[[object], bool] | None,
) -> bool:
    if idle_since is None or (now - idle_since) > IDLE_REUSE_LIMIT_S:
        return True
    sock = getattr(connection, "sock", None)
    if sock is None:
        return True
    if peer_pending_fn is not None:
        try:
            return bool(peer_pending_fn(sock))
        except Exception:
            return True
    return _socket_already_readable(sock)


def _response_connection_tokens(headers: object) -> set[str] | None:
    """Union of every Connection token. None if the headers cannot be read.

    Tokens are comma-separated and compared without regard to case.
    A message without get_all stores one value; HTTPMessage stores them all.
    """
    try:
        if hasattr(headers, "get_all"):
            values = headers.get_all("Connection")  # type: ignore[attr-defined]
            if values is None:
                values = []
        else:
            raw = headers.get("Connection", "")  # type: ignore[attr-defined]
            values = [] if raw is None else [raw]
    except Exception:
        return None
    tokens: set[str] = set()
    for value in values:
        text = value if isinstance(value, str) else str(value)
        for part in text.split(","):
            token = part.strip().lower()
            if token:
                tokens.add(token)
    return tokens


def _response_framing_allows_reuse(headers: object) -> bool:
    """True only for one non-negative Content-Length and no Transfer-Encoding.

    Messages without get_all cannot tell a missing field from a default
    and are not on-wire responses. The production parser has get_all, and
    a present Transfer-Encoding counts even when its value is blank.
    """
    if not hasattr(headers, "get_all"):
        return True
    try:
        transfer = headers.get_all("Transfer-Encoding")  # type: ignore[attr-defined]
        lengths = headers.get_all("Content-Length")  # type: ignore[attr-defined]
    except Exception:
        return False
    if transfer is not None and len(transfer) > 0:
        return False
    if lengths is None or len(lengths) != 1:
        return False
    try:
        length = int(str(lengths[0]).strip())
    except (TypeError, ValueError):
        return False
    return length >= 0


def _response_is_reusable(response: object) -> bool:
    """True only for an explicit keep-alive response with one framed body.

    A missing attribute is not reusable: the caller drops the socket.
    Connection is every instance; any close token wins. Keep-alive counts
    only when it was asked for and close was not.
    """
    try:
        will_close = response.will_close  # type: ignore[attr-defined]
        headers = response.headers  # type: ignore[attr-defined]
    except Exception:
        return False
    if will_close is not False:
        return False
    tokens = _response_connection_tokens(headers)
    if tokens is None or "keep-alive" not in tokens or "close" in tokens:
        return False
    return _response_framing_allows_reuse(headers)


def _open_accredited(
    *,
    host: str,
    port: int,
    timeout: float,
    expected_executable: str,
    expected_argv: list[str],
    expected_cwd: str,
    connection_factory: Callable[[str, int, float], object] | None,
    connections_fn: Callable[[], object] | None,
    get_executable: Callable[[int], str | None] | None,
    get_argv: Callable[[int], list[str] | None] | None,
    get_cwd: Callable[[int], str | None] | None,
    guard: object | None,
) -> object:
    """Connect and run the sealed socket-owner check. No request bytes."""
    factory = connection_factory or http.client.HTTPConnection
    connection: object | None = None
    try:
        connection = factory(host, port, timeout)
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
        return connection
    except Exception:
        _close_quiet(connection)
        raise


def _transmit(
    connection: object,
    *,
    reused: bool,
    started: list[bool],
    key: str,
    method: str,
    path: str,
    query: dict[str, str] | None,
    body: bytes | None,
    headers: dict[str, str] | None,
    deadline: float,
    now: Callable[[], float],
    max_response_bytes: int,
) -> tuple[int, bytes, bool]:
    """Write one request. ``started[0]`` is set before the first byte may leave."""
    remaining = float(deadline) - now()
    if remaining <= 0.0:
        raise TimeoutError("daemon_request_deadline_exceeded")
    # On a reused socket any later error may mean the server already closed
    # and a write was ambiguous. Mark that before touching the socket.
    if reused:
        started[0] = True
    sock = getattr(connection, "sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if callable(settimeout):
        settimeout(remaining)
    if not reused:
        started[0] = True
    params = dict(query or {})
    params["key"] = key
    target = path + "?" + urllib.parse.urlencode(params)
    outgoing = dict(headers or {})
    if not any(name.lower() == "connection" for name in outgoing):
        outgoing["Connection"] = "keep-alive"
    connection.request(  # type: ignore[attr-defined]
        method, target, body=body, headers=outgoing
    )
    response = connection.getresponse()  # type: ignore[attr-defined]
    payload = response.read(max_response_bytes + 1)
    if len(payload) > max_response_bytes:
        raise ValueError("daemon_response_too_large")
    return int(response.status), payload, _response_is_reusable(response)


def keepalive_daemon_http_request(
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
    peer_pending_fn: Callable[[object], bool] | None = None,
) -> tuple[int, bytes]:
    """Accredited HTTP, reusing one idle socket for this daemon identity."""
    _validate_request(
        host=host,
        port=port,
        key=key,
        method=method,
        path=path,
        deadline=deadline,
        max_response_bytes=max_response_bytes,
    )
    sealed_kwargs = dict(
        host=host,
        port=port,
        key=key,
        method=method,
        path=path,
        query=query,
        body=body,
        headers=headers,
        deadline=deadline,
        expected_executable=expected_executable,
        expected_argv=expected_argv,
        expected_cwd=expected_cwd,
        connection_factory=connection_factory,
        connections_fn=connections_fn,
        get_executable=get_executable,
        get_argv=get_argv,
        get_cwd=get_cwd,
        guard=guard,
        time_fn=time_fn,
        max_response_bytes=max_response_bytes,
    )
    # Tests replace the sealed function on its module. Honour that replacement
    # and do not pool: the double is the whole transport for that test.
    current = _sealed_transport.verified_daemon_http_request
    if current is not _SEALED_REQUEST:
        return current(**sealed_kwargs)

    now = time_fn or time.monotonic
    if float(deadline) - now() <= 0.0:
        raise _deadline_error()

    pool_key: _PoolKey = (
        host,
        port,
        expected_executable,
        tuple(expected_argv),
        expected_cwd,
    )
    slot = _slot_for(pool_key)
    with slot.lock:
        if slot.busy:
            other_thread_holds_it = True
            candidate: object | None = None
            idle_since: float | None = None
        else:
            other_thread_holds_it = False
            slot.busy = True
            candidate = slot.connection
            idle_since = slot.idle_since
            slot.connection = None
            slot.idle_since = None
    if other_thread_holds_it:
        return _sealed_transport.verified_daemon_http_request(**sealed_kwargs)

    connection: object | None = None
    reused = False
    started = [False]
    park = False
    try:
        if candidate is not None and not _candidate_unusable(
            candidate,
            idle_since,
            now=now(),
            peer_pending_fn=peer_pending_fn,
        ):
            connection = candidate
            reused = True
        else:
            _close_quiet(candidate)
            remaining = float(deadline) - now()
            if remaining <= 0.0:
                raise _deadline_error()
            connection = _open_accredited(
                host=host,
                port=port,
                timeout=remaining,
                expected_executable=expected_executable,
                expected_argv=expected_argv,
                expected_cwd=expected_cwd,
                connection_factory=connection_factory,
                connections_fn=connections_fn,
                get_executable=get_executable,
                get_argv=get_argv,
                get_cwd=get_cwd,
                guard=guard,
            )
        status, payload, reusable = _transmit(
            connection,
            reused=reused,
            started=started,
            key=key,
            method=method,
            path=path,
            query=query,
            body=body,
            headers=headers,
            deadline=deadline,
            now=now,
            max_response_bytes=max_response_bytes,
        )
        park = reusable
        return status, payload
    except AccreditedTransportError as error:
        if (
            reused
            and error.request_stage == "pre_request"
            and error.http_bytes_sent == 0
            and connection is not None
        ):
            park = True
        raise
    except Exception as error:
        wrapped = _transport_error(error, request_started=started[0])
        if (
            reused
            and wrapped.request_stage == "pre_request"
            and wrapped.http_bytes_sent == 0
            and connection is not None
        ):
            park = True
        raise wrapped from error
    finally:
        if park and connection is not None:
            _park(slot, connection, now())
        else:
            _discard(slot, connection)
