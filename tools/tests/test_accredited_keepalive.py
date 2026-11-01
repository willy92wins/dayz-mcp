"""Client keepalive transport and the daemon opt-in that makes it safe."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import accredited_daemon_transport, accredited_keepalive, loopback, server
from dayz_mcp.daemon_credential import RefreshingDaemonCredential
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy
from dayz_mcp.native_process_guard import identity_hashes
from tests.fence_helpers import INST_SERVER, bind_both_peers


EXECUTABLE = r"C:\Runtime\python.exe"
CWD = r"C:\DayZ_MCP_dev\tools"
PORT = 54321
_FORBIDDEN_PORT = 8765


def _argv(port: int) -> list[str]:
    return [EXECUTABLE, "-m", "dayz_mcp", "--daemon", "--port", str(port)]


def _policy(keyfile: Path, port: int = PORT) -> AccreditedDaemonPolicy:
    argv = _argv(port)
    authority = {
        "argv": argv,
        "cwd": CWD,
        "host": "127.0.0.1",
        "keyfile": str(keyfile),
        "kind": "normal",
        "native_executable": EXECUTABLE,
        "port": port,
        "security_build_id": None,
    }
    digest = hashlib.sha256(
        json.dumps(
            authority,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return AccreditedDaemonPolicy(
        kind="normal",
        host="127.0.0.1",
        port=port,
        keyfile=str(keyfile),
        native_executable=EXECUTABLE,
        argv=tuple(argv),
        cwd=CWD,
        security_build_id=None,
        authority_sha256=digest,
    )


class _Headers:
    def __init__(self, connection: str) -> None:
        self._connection = connection

    def get(self, name: str, default: str = "") -> str:
        if str(name).lower() == "connection":
            return self._connection
        return default


class _Response:
    def __init__(
        self,
        *,
        will_close: bool,
        connection: str,
        body: bytes = b'{"ok":true}',
    ) -> None:
        self.status = 200
        self.will_close = will_close
        self.headers = _Headers(connection)
        self._body = body

    def read(self, size: int) -> bytes:
        return self._body


class _Socket:
    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 51000)

    def getpeername(self) -> tuple[str, int]:
        return ("127.0.0.1", PORT)

    def settimeout(self, timeout: float) -> None:
        return None


class _Connection:
    def __init__(self, response: _Response) -> None:
        self.sock = _Socket()
        self._response = response
        self.requests = 0
        self.closed = False
        self.on_request = None

    def connect(self) -> None:
        return None

    def request(self, method: str, target: str, *, body: bytes | None, headers: dict) -> None:
        self.requests += 1
        self.last = (method, target, body, headers)
        if self.on_request is not None:
            self.on_request(self)

    def getresponse(self) -> _Response:
        waiter = getattr(self, "wait_for_response", None)
        if waiter is not None:
            waiter(self)
        return self._response

    def close(self) -> None:
        self.closed = True


def _established() -> SimpleNamespace:
    return SimpleNamespace(
        status=accredited_daemon_transport.psutil.CONN_ESTABLISHED,
        laddr=("127.0.0.1", PORT),
        raddr=("127.0.0.1", 51000),
        pid=42,
    )


def _guard(port: int):
    hashes = identity_hashes(EXECUTABLE, _argv(port))

    class Guard:
        def snapshot(self, pid: int) -> dict[str, object]:
            return {
                "pid": pid,
                "creation_time_utc": "2026-07-22T00:00:00Z",
                "executable_sha256": hashes["executable_sha256"],
                "command_line_sha256": hashes["command_line_sha256"],
                "identity_scheme": "psutil-argv-v2",
                "identity_complete": True,
            }

    return Guard()


class KeepaliveTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        accredited_keepalive.clear_idle_connections()
        self.walks = 0
        self.created: list[_Connection] = []
        self.clock = [100.0]

    def tearDown(self) -> None:
        accredited_keepalive.clear_idle_connections()

    def _connections(self) -> list[SimpleNamespace]:
        self.walks += 1
        return [_established()]

    def _factory(self, response: _Response | None = None):
        def factory(_host: str, _port: int, _timeout: float) -> _Connection:
            connection = _Connection(
                response
                or _Response(will_close=False, connection="keep-alive")
            )
            self.created.append(connection)
            return connection

        return factory

    def _request(self, **overrides: object) -> tuple[int, bytes]:
        port = int(overrides.pop("port", PORT))
        kwargs = dict(
            host="127.0.0.1",
            port=port,
            key="top-secret",
            method="GET",
            path="/status",
            query=None,
            body=None,
            headers=None,
            deadline=1000.0,
            expected_executable=EXECUTABLE,
            expected_argv=_argv(port),
            expected_cwd=CWD,
            connection_factory=self._factory(),
            connections_fn=self._connections,
            get_executable=lambda _pid: EXECUTABLE,
            get_argv=lambda _pid: _argv(port),
            get_cwd=lambda _pid: CWD,
            guard=_guard(port),
            time_fn=lambda: self.clock[0],
            peer_pending_fn=lambda _sock: False,
        )
        kwargs.update(overrides)
        return accredited_keepalive.keepalive_daemon_http_request(**kwargs)

    def test_eight_requests_accredit_once(self) -> None:
        for _ in range(8):
            status, body = self._request()
            self.assertEqual((status, body), (200, b'{"ok":true}'))
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.walks, 3)
        sent = self.created[0].last[3]
        self.assertEqual(sent.get("Connection"), "keep-alive")
        self.assertFalse(self.created[0].closed)

    def test_idle_cap_is_strictly_over_five_seconds(self) -> None:
        self._request()
        self.clock[0] = 105.0
        self._request()
        self.assertEqual(self.walks, 3)
        self.assertEqual(len(self.created), 1)
        self.clock[0] = 110.01
        self._request()
        self.assertEqual(self.walks, 6)
        self.assertEqual(len(self.created), 2)

    def test_readable_peer_is_not_reused(self) -> None:
        pending = {"calls": 0}

        def peer_pending(_sock: object) -> bool:
            pending["calls"] += 1
            return True

        self._request(peer_pending_fn=peer_pending)
        self._request(peer_pending_fn=peer_pending)
        self.assertEqual(pending["calls"], 1)
        self.assertEqual(self.walks, 6)
        self.assertEqual(len(self.created), 2)

    def test_socket_without_fileno_is_not_reused(self) -> None:
        self._request(peer_pending_fn=None)
        self._request(peer_pending_fn=None)
        self.assertEqual(self.walks, 6)
        self.assertEqual(len(self.created), 2)

    def test_close_response_is_not_reused(self) -> None:
        for will_close, connection in (
            (True, "keep-alive"),
            (False, "close"),
        ):
            with self.subTest(will_close=will_close, connection=connection):
                accredited_keepalive.clear_idle_connections()
                self.walks = 0
                self.created.clear()
                response = _Response(will_close=will_close, connection=connection)
                self._request(connection_factory=self._factory(response))
                self._request(connection_factory=self._factory(response))
                self.assertEqual(self.walks, 6)
                self.assertEqual(len(self.created), 2)
                self.assertTrue(self.created[0].closed)

    def test_busy_slot_opens_a_fresh_accredited_connection(self) -> None:
        release = threading.Event()
        entered = threading.Event()
        blocker = _Connection(_Response(will_close=False, connection="keep-alive"))

        def wait_for_response(_connection: _Connection) -> None:
            entered.set()
            self.assertTrue(release.wait(2.0))

        blocker.wait_for_response = wait_for_response
        fast = _Response(will_close=False, connection="keep-alive")

        def factory(_host: str, _port: int, _timeout: float) -> _Connection:
            if not self.created:
                self.created.append(blocker)
                return blocker
            connection = _Connection(fast)
            self.created.append(connection)
            return connection

        errors: list[BaseException] = []
        first: list[tuple[int, bytes]] = []

        def lead() -> None:
            try:
                first.append(self._request(connection_factory=factory))
            except BaseException as exc:
                errors.append(exc)

        leader = threading.Thread(target=lead, daemon=True)
        leader.start()
        self.assertTrue(entered.wait(2.0))
        done = threading.Event()
        second: list[tuple[int, bytes]] = []

        def follow() -> None:
            try:
                second.append(self._request(connection_factory=factory))
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        follower = threading.Thread(target=follow, daemon=True)
        try:
            follower.start()
            self.assertTrue(
                done.wait(2.0),
                "the request that found the slot busy waited on it",
            )
        finally:
            release.set()
            leader.join(2.0)
            follower.join(2.0)
        self.assertEqual(errors, [])
        self.assertEqual(first, [(200, b'{"ok":true}')])
        self.assertEqual(second, [(200, b'{"ok":true}')])
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.walks, 6)
        self.assertFalse(leader.is_alive())
        self.assertFalse(follower.is_alive())

    def test_reset_on_reused_socket_is_post_request_and_dropped(self) -> None:
        def reject_second(connection: _Connection) -> None:
            if connection.requests >= 2:
                raise ConnectionResetError("reset")

        status, _body = self._request()
        self.assertEqual(status, 200)
        self.created[0].on_request = reject_second
        with self.assertRaises(accredited_daemon_transport.AccreditedTransportError) as raised:
            self._request()
        self.assertEqual(raised.exception.request_stage, "post_request")
        self.assertEqual(raised.exception.http_bytes_sent, 1)
        self.assertEqual(raised.exception.code, "daemon_transport_failure")
        self._request()
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.walks, 6)

    def test_oversized_body_is_not_pooled(self) -> None:
        response = _Response(
            will_close=False,
            connection="keep-alive",
            body=b'{"ok":true}',
        )
        with self.assertRaises(accredited_daemon_transport.AccreditedTransportError) as raised:
            self._request(
                connection_factory=self._factory(response),
                max_response_bytes=4,
            )
        self.assertEqual(raised.exception.code, "daemon_response_too_large")
        self.assertEqual(raised.exception.request_stage, "post_request")
        self.assertEqual(raised.exception.http_bytes_sent, 1)
        with self.assertRaises(accredited_daemon_transport.AccreditedTransportError):
            self._request(
                connection_factory=self._factory(response),
                max_response_bytes=4,
            )
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.walks, 6)

    def test_patched_sealed_function_is_called_and_not_pooled(self) -> None:
        calls: list[str] = []

        def fake(**kwargs: object) -> tuple[int, bytes]:
            calls.append(str(kwargs["path"]))
            return 200, b"{}"

        with patch.object(
            accredited_daemon_transport,
            "verified_daemon_http_request",
            fake,
        ):
            self._request()
            self._request()
        self.assertEqual(calls, ["/status", "/status"])
        self.assertEqual(self.created, [])
        self.assertEqual(self.walks, 0)

    def test_send_default_is_the_keepalive_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            provider = RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=None,
            )
            seen: list[str] = []

            def fake(**kwargs: object) -> tuple[int, bytes]:
                seen.append(str(kwargs["path"]))
                return 200, b"{}"

            with patch.object(
                accredited_keepalive,
                "keepalive_daemon_http_request",
                fake,
            ):
                status, body = provider._send(
                    secret="fixture-credential",
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=10.0,
                )
        self.assertEqual((status, body), (200, b"{}"))
        self.assertEqual(seen, ["/status"])

    def test_client_runtime_uses_a_patched_orphan_guard_binding(self) -> None:
        calls: list[str] = []

        def fake(**kwargs: object) -> tuple[int, bytes]:
            calls.append(str(kwargs["path"]))
            return 200, b"{}"

        with patch.object(server.orphan_guard, "verified_daemon_http_request", fake):
            status, body = server._client_keepalive_request(
                host="127.0.0.1",
                port=PORT,
                key="top-secret",
                method="GET",
                path="/status",
                query={},
                body=None,
                headers={},
                deadline=10.0,
                expected_executable=EXECUTABLE,
                expected_argv=_argv(PORT),
                expected_cwd=CWD,
            )
        self.assertEqual((status, body), (200, b"{}"))
        self.assertEqual(calls, ["/status"])

    def test_reused_socket_reset_does_not_duplicate_enqueue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            calls: list[str] = []

            def reject_second(connection: _Connection) -> None:
                if connection.requests >= 2:
                    raise ConnectionResetError("reset")

            def request_fn(**kwargs: object) -> tuple[int, bytes]:
                calls.append(str(kwargs["path"]))
                return self._request(**kwargs)

            provider = RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request_fn,
            )
            runtime = object.__new__(server.ClientRuntime)
            runtime._credential_provider = provider
            ensured: list[bool] = []
            runtime._ensure_daemon = lambda _deadline: ensured.append(True) or True
            status, body = runtime._call(
                "POST", "/enqueue", {"cmd": "noop"}, timeout=5.0
            )
            self.assertEqual(status, 200)
            self.assertEqual(body, {"ok": True})
            self.created[0].on_request = reject_second
            with self.assertRaises(server.ToolError) as raised:
                runtime._call("POST", "/enqueue", {"cmd": "noop"}, timeout=5.0)
        self.assertEqual(str(raised.exception), "daemon_response_ambiguous")
        self.assertEqual(calls, ["/enqueue", "/enqueue"])
        self.assertEqual(ensured, [])
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.walks, 3)

    def test_pre_request_refusal_is_still_retried_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            attempts = {"n": 0}

            def factory(_host: str, _port: int, _timeout: float) -> _Connection:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise ConnectionRefusedError("refused")
                connection = _Connection(
                    _Response(will_close=False, connection="keep-alive")
                )
                self.created.append(connection)
                return connection

            def request_fn(**kwargs: object) -> tuple[int, bytes]:
                return self._request(connection_factory=factory, **kwargs)

            provider = RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request_fn,
            )
            runtime = object.__new__(server.ClientRuntime)
            runtime._credential_provider = provider
            ensured: list[bool] = []
            runtime._ensure_daemon = lambda _deadline: ensured.append(True) or True
            status, body = runtime._call("GET", "/status", timeout=5.0)
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(ensured, [True])
        self.assertEqual(self.walks, 3)

    def test_old_http10_daemon_opens_one_connection_per_request(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0.2)
        port = int(listener.getsockname()[1])
        self.assertNotEqual(port, _FORBIDDEN_PORT)
        stop = threading.Event()
        accepts: list[int] = []

        def serve() -> None:
            while not stop.is_set():
                try:
                    peer, _addr = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    return
                accepts.append(1)
                try:
                    peer.settimeout(2.0)
                    data = b""
                    while b"\r\n\r\n" not in data:
                        chunk = peer.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    body = b'{"status":"ok"}'
                    peer.sendall(
                        b"HTTP/1.0 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
                        b"\r\n" + body
                    )
                finally:
                    peer.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        live: list[http.client.HTTPConnection] = []

        class Tracking(http.client.HTTPConnection):
            def connect(self) -> None:
                super().connect()
                live.append(self)

        def connections_fn() -> list[SimpleNamespace]:
            self.walks += 1
            sock = live[-1].sock
            assert sock is not None
            return [
                SimpleNamespace(
                    status=accredited_daemon_transport.psutil.CONN_ESTABLISHED,
                    laddr=sock.getpeername(),
                    raddr=sock.getsockname(),
                    pid=42,
                )
            ]

        try:
            for _ in range(2):
                status, body = self._request(
                    port=port,
                    connection_factory=Tracking,
                    connections_fn=connections_fn,
                    peer_pending_fn=None,
                    time_fn=time.monotonic,
                    deadline=time.monotonic() + 5.0,
                )
                self.assertEqual(status, 200)
                self.assertEqual(body, b'{"status":"ok"}')
        finally:
            accredited_keepalive.clear_idle_connections()
            stop.set()
            listener.close()
            thread.join(2.0)
        self.assertEqual(len(accepts), 2)
        self.assertEqual(len(live), 2)
        self.assertEqual(self.walks, 6)


def _read_http(sock: socket.socket, buffered: bytes = b"") -> tuple[bytes, dict[str, str], bytes, bytes]:
    data = buffered
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(8192)
        if not chunk:
            break
        data += chunk
    head, sep, rest = data.partition(b"\r\n\r\n")
    if not sep:
        raise AssertionError(f"no HTTP header in {data!r}")
    lines = head.split(b"\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        headers[name.decode("latin-1").lower()] = value.decode("latin-1").strip()
    length = int(headers.get("content-length", "0"))
    while len(rest) < length:
        chunk = sock.recv(8192)
        if not chunk:
            break
        rest += chunk
    return lines[0], headers, rest[:length], rest[length:]


class DaemonKeepaliveTests(unittest.TestCase):
    def setUp(self) -> None:
        accredited_keepalive.clear_idle_connections()
        self.key = "test-key"
        self.state = loopback.ServerState(self.key)
        bind_both_peers(self.state)
        self.httpd = loopback.create_http_server(
            0,
            self.state,
            log_sink=lambda _message: None,
            reclaim_orphans=False,
        )
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        host, port = self.httpd.server_address
        self.host = str(host)
        self.port = int(port)
        self.assertNotEqual(self.port, _FORBIDDEN_PORT)

    def tearDown(self) -> None:
        accredited_keepalive.clear_idle_connections()
        self.httpd.shutdown()
        self.thread.join(timeout=2.0)
        self.httpd.server_close()

    def _raw(
        self,
        method: str,
        path: str,
        *,
        protocol: str,
        connection: str | None,
        query: dict[str, str] | None = None,
        body: bytes = b"",
        content_length: int | None = None,
    ) -> bytes:
        params = dict(query or {})
        params["key"] = self.key
        target = path + "?" + urllib.parse.urlencode(params)
        lines = [f"{method} {target} {protocol}", "Host: 127.0.0.1"]
        if connection is not None:
            lines.append(f"Connection: {connection}")
        if content_length is not None:
            lines.append(f"Content-Length: {content_length}")
        elif body:
            lines.append(f"Content-Length: {len(body)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body

    def _exchange(self, payload: bytes) -> tuple[bytes, dict[str, str], bytes]:
        sock = socket.create_connection((self.host, self.port), timeout=2.0)
        try:
            sock.settimeout(2.0)
            sock.sendall(payload)
            status, headers, body, _rest = _read_http(sock)
            try:
                extra = sock.recv(1)
            except TimeoutError:
                self.fail("server left the connection open")
            self.assertEqual(extra, b"")
            return status, headers, body
        finally:
            sock.close()

    def test_idle_bound_sits_between_client_cap_and_handler_timeout(self) -> None:
        self.assertGreater(
            loopback.KEEPALIVE_IDLE_S,
            accredited_keepalive.IDLE_REUSE_LIMIT_S,
        )
        self.assertLess(loopback.KEEPALIVE_IDLE_S, loopback.Handler.timeout)

    def test_bridge_routes_close_when_the_client_asks_to_keep_alive(self) -> None:
        polls = []
        for protocol, connection in (
            ("HTTP/1.0", None),
            ("HTTP/1.1", "keep-alive"),
        ):
            status, headers, body = self._exchange(
                self._raw(
                    "GET",
                    "/poll",
                    protocol=protocol,
                    connection=connection,
                    query={"inst": INST_SERVER},
                )
            )
            self.assertTrue(status.startswith(b"HTTP/1.0 "))
            self.assertNotIn("keep-alive", headers.get("connection", "").lower())
            polls.append((status.split()[1], headers, body))
        self.assertEqual(polls[0][0], polls[1][0])
        self.assertEqual(polls[0][2], polls[1][2])
        self.assertNotIn("connection", polls[1][1])
        result_body = json.dumps(
            {"id": 1, "ok": 1, "value": 7}, separators=(",", ":")
        ).encode("ascii")
        status, headers, body = self._exchange(
            self._raw(
                "POST",
                "/result",
                protocol="HTTP/1.1",
                connection="keep-alive",
                query={"inst": INST_SERVER},
                body=result_body,
            )
        )
        self.assertTrue(status.startswith(b"HTTP/1.0 "))
        self.assertNotIn("connection", headers)
        self.assertIsInstance(json.loads(body.decode("utf-8")), dict)
        delay = b'{"ms":0}'
        status, headers, _body = self._exchange(
            self._raw(
                "POST",
                "/set_poll_delay",
                protocol="HTTP/1.1",
                connection="keep-alive",
                body=delay,
            )
        )
        self.assertTrue(status.startswith(b"HTTP/1.0 "))
        self.assertNotIn("connection", headers)

    def test_http10_status_still_closes(self) -> None:
        status, headers, body = self._exchange(
            self._raw("GET", "/status", protocol="HTTP/1.0", connection=None)
        )
        self.assertTrue(status.startswith(b"HTTP/1.0 200"))
        self.assertNotIn("connection", headers)
        self.assertIsInstance(json.loads(body.decode("utf-8")), dict)

    def test_authenticated_status_stays_open_for_a_second_request(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=2.0)
        try:
            sock.settimeout(2.0)
            request = self._raw(
                "GET", "/status", protocol="HTTP/1.1", connection="keep-alive"
            )
            sock.sendall(request)
            status, headers, body, rest = _read_http(sock)
            self.assertTrue(status.startswith(b"HTTP/1.0 200"))
            self.assertIn("keep-alive", headers.get("connection", "").lower())
            self.assertNotIn("close", headers.get("connection", "").lower())
            self.assertIsInstance(json.loads(body.decode("utf-8")), dict)
            sock.sendall(request)
            status, headers, body, _rest = _read_http(sock, rest)
            self.assertTrue(status.startswith(b"HTTP/1.0 200"))
            self.assertIn("keep-alive", headers.get("connection", "").lower())
            self.assertIsInstance(json.loads(body.decode("utf-8")), dict)
        finally:
            sock.close()

    def test_unread_and_invalid_bodies_close(self) -> None:
        too_big = loopback.MAX_BODY_BYTES + 1
        cases = (
            ("body_too_large", 413, too_big, b""),
            ("bad_content_length", 400, -1, b""),
            ("bad_json", 400, 1, b"{"),
            ("bad_json_type", 400, 2, b"[]"),
        )
        for error, code, length, body in cases:
            with self.subTest(error=error):
                status, headers, payload = self._exchange(
                    self._raw(
                        "POST",
                        "/enqueue",
                        protocol="HTTP/1.1",
                        connection="keep-alive",
                        body=body,
                        content_length=length,
                    )
                )
                self.assertEqual(status.split()[1], str(code).encode("ascii"))
                self.assertEqual(json.loads(payload.decode("utf-8")), {"error": error})
                self.assertNotIn("connection", headers)

    def test_idle_keepalive_closes_before_handler_timeout(self) -> None:
        with patch.object(loopback, "KEEPALIVE_IDLE_S", 0.2):
            sock = socket.create_connection((self.host, self.port), timeout=2.0)
            try:
                sock.settimeout(2.0)
                sock.sendall(
                    self._raw(
                        "GET",
                        "/status",
                        protocol="HTTP/1.1",
                        connection="keep-alive",
                    )
                )
                status, headers, _body, _rest = _read_http(sock)
                self.assertTrue(status.startswith(b"HTTP/1.0 200"))
                self.assertIn("keep-alive", headers.get("connection", "").lower())
                sock.settimeout(0.2)
                deadline = time.monotonic() + 2.0
                extra = b"pending"
                while time.monotonic() < deadline:
                    try:
                        extra = sock.recv(1)
                    except TimeoutError:
                        continue
                    break
                else:
                    self.fail("idle keep-alive was not closed within 2s")
                self.assertEqual(extra, b"")
            finally:
                sock.close()

    def test_client_reuses_one_accredited_connection_for_status(self) -> None:
        walks: list[int] = []
        live: list[http.client.HTTPConnection] = []

        class Tracking(http.client.HTTPConnection):
            def connect(self) -> None:
                super().connect()
                live.append(self)

        def connections_fn() -> list[SimpleNamespace]:
            walks.append(1)
            sock = live[-1].sock
            assert sock is not None
            return [
                SimpleNamespace(
                    status=accredited_daemon_transport.psutil.CONN_ESTABLISHED,
                    laddr=sock.getpeername(),
                    raddr=sock.getsockname(),
                    pid=42,
                )
            ]

        for _ in range(4):
            status, body = accredited_keepalive.keepalive_daemon_http_request(
                host="127.0.0.1",
                port=self.port,
                key=self.key,
                method="GET",
                path="/status",
                query=None,
                body=None,
                headers=None,
                deadline=time.monotonic() + 5.0,
                expected_executable=EXECUTABLE,
                expected_argv=_argv(self.port),
                expected_cwd=CWD,
                connection_factory=Tracking,
                connections_fn=connections_fn,
                get_executable=lambda _pid: EXECUTABLE,
                get_argv=lambda _pid: _argv(self.port),
                get_cwd=lambda _pid: CWD,
                guard=_guard(self.port),
                time_fn=time.monotonic,
            )
            self.assertEqual(status, 200)
            self.assertIsInstance(json.loads(body.decode("utf-8")), dict)
        self.assertEqual(len(live), 1)
        self.assertEqual(len(walks), 3)
