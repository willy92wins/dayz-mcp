"""Long-poll of GET /poll (wait_ms).

Fast cases return immediately. Cases that wait on a real clock are @slow_test.
The benchmark drives checks/poll_long_poll_bench.py against an ephemeral
loopback port: it does not launch DayZ and does not bind port 8765.
"""

from __future__ import annotations

import ast
import http.client
import inspect
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dayz_mcp import loopback
from tests._addon_paths import addon_root
from tests._tiers import slow_test


_EMPTY = b'{"commands":[]}'
_MISSION = addon_root() / "scripts" / "5_Mission"
_INST = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_PID = 4242
_CTIME = "2026-08-18T00:00:00.000000Z"
_APPEND_SITES = {
    "_publish_enqueue_locked",
    "_enqueue_exec_enforce",
    "_publish_close_locked",
    "_publish_release_locked",
}


def _commands_body(commands: object) -> bytes:
    return json.dumps({"commands": commands}, separators=(",", ":")).encode("utf-8")


def _brace_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0 or source.find(signature, start + 1) >= 0:
        raise AssertionError(f"signature not found exactly once: {signature}")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block: {signature}")


class _Daemon:
    def __init__(self) -> None:
        self.key = "poll-long-poll-key"
        self.state = loopback.ServerState(self.key)
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
        self.base = f"http://{host}:{port}"

    def close(self) -> None:
        self.state.cancel_pending()
        self.httpd.shutdown()
        self.thread.join(timeout=3.0)
        self.httpd.server_close()

    def open(self, query: dict[str, str], timeout: float = 1.0) -> tuple[int, bytes]:
        params = dict(query)
        params["key"] = self.key
        url = self.base + "/poll?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            try:
                return int(exc.code), exc.read()
            finally:
                exc.close()

    def enqueue(self, cmd: str = "camera_get", peer: str = "client") -> dict:
        status, payload = self.state.enqueue_command(
            cmd, {}, peer=peer, operation_timeout_s=30
        )
        if status != 200 or not isinstance(payload, dict):
            raise AssertionError(f"enqueue failed: {status} {payload!r}")
        return payload

    def post_json(self, path: str, payload: dict, timeout: float = 2.0) -> tuple[int, bytes]:
        url = self.base + path + "?" + urllib.parse.urlencode({"key": self.key})
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            try:
                return int(exc.code), exc.read()
            finally:
                exc.close()


def _on_wait(state: loopback.ServerState, peer: str, callback) -> threading.Event:
    """Fire once the peer's /poll has released the lock inside Condition.wait."""

    cv = state._command_cv[peer]
    entered = threading.Event()
    real_release = cv._release_save

    def release_save():
        saved = real_release()
        if not entered.is_set():
            callback()
            entered.set()
        return saved

    cv._release_save = release_save
    return entered


class PollLongPollTest(unittest.TestCase):
    def setUp(self) -> None:
        self.daemon = _Daemon()
        self.addCleanup(self.daemon.close)

    def _read(
        self, query: dict[str, str] | None = None, timeout: float = 1.0
    ) -> tuple[float, int, bytes]:
        started = time.monotonic()
        status, raw = self.daemon.open(query or {"peer": "client"}, timeout)
        return time.monotonic() - started, status, raw

    def _twin_commands(self, cmd: str, peer: str) -> bytes:
        twin = loopback.ServerState(self.daemon.key)
        status, payload = twin.enqueue_command(
            cmd, {}, peer=peer, operation_timeout_s=30
        )
        self.assertEqual(status, 200, payload)
        code, polled = twin.record_poll(peer)
        self.assertEqual(code, 200, polled)
        return _commands_body(polled["commands"])

    def test_absent_wait_ms_is_the_old_empty_body(self) -> None:
        elapsed, status, raw = self._read()
        self.assertEqual(status, 200)
        self.assertEqual(raw, _EMPTY)
        self.assertLess(elapsed, 0.2)

    def test_unusable_and_zero_wait_ms_do_not_wait(self) -> None:
        for raw_wait in ("", "-1", "1.5", "abc", "0"):
            with self.subTest(wait_ms=raw_wait):
                elapsed, status, raw = self._read(
                    {"peer": "client", "wait_ms": raw_wait}
                )
                self.assertEqual(status, 200)
                self.assertEqual(raw, _EMPTY)
                self.assertLess(elapsed, 0.2)

    def test_absent_wait_ms_does_not_see_a_command_enqueued_after_it(self) -> None:
        errors: list[BaseException] = []

        def late() -> None:
            try:
                time.sleep(0.05)
                self.daemon.enqueue()
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=late)
        worker.start()
        self.addCleanup(lambda: worker.join(timeout=2))
        elapsed, status, raw = self._read({"peer": "client"})
        worker.join(timeout=2)
        self.assertEqual(errors, [])
        self.assertEqual(status, 200)
        self.assertEqual(raw, _EMPTY)
        self.assertLess(elapsed, 0.2)
        code, polled = self.daemon.state.record_poll("client")
        self.assertEqual(code, 200)
        self.assertEqual(polled["commands"][0]["cmd"], "camera_get")

    def test_queued_command_returns_without_waiting_out_the_cap(self) -> None:
        self.daemon.enqueue()
        expected = self._twin_commands("camera_get", "client")
        elapsed, status, raw = self._read(
            {"peer": "client", "wait_ms": "800"}, timeout=2.0
        )
        self.assertEqual(status, 200)
        self.assertEqual(raw, expected)
        self.assertIn(b'"cmd":"camera_get"', raw)
        self.assertLess(elapsed, 0.2)

    def test_delay_ms_still_sleeps_after_delivery(self) -> None:
        status, payload = self.daemon.state.set_poll_delay(200)
        self.assertEqual(status, 200, payload)
        self.daemon.enqueue()
        expected = self._twin_commands("camera_get", "client")
        elapsed, http_status, raw = self._read(
            {"peer": "client", "wait_ms": "800"}, timeout=2.0
        )
        self.assertEqual(http_status, 200)
        self.assertEqual(raw, expected)
        self.assertGreater(elapsed, 0.15)
        self.assertLess(elapsed, 0.5)

    def test_conditions_are_per_peer_and_not_the_await_condition(self) -> None:
        state = self.daemon.state
        self.assertIsNot(state._command_cv["server"], state._command_cv["client"])
        self.assertIs(state._command_cv["server"]._lock, state._lock)
        self.assertIs(state._command_cv["client"]._lock, state._lock)
        self.assertIsNot(state._command_cv["server"], state._result_cv)
        self.assertEqual(loopback.POLL_LONG_POLL_MAX_MS, 800)
        self.assertEqual(
            loopback.POLL_LONG_POLL_MAX_S, loopback.POLL_LONG_POLL_MAX_MS / 1000.0
        )
        self.assertEqual(loopback.Handler.timeout, 35.0)
        self.assertGreater(loopback.Handler.timeout, loopback.POLL_LONG_POLL_MAX_S + 5.0)

    def test_handle_poll_does_not_look_up_creation_time(self) -> None:
        source = inspect.getsource(loopback.Handler._handle_poll)
        self.assertNotIn("_lookup_creation_time", source)
        self.assertLess(source.index("resolve_poll_pid"), source.index("record_poll"))
        self.assertIn("wait_s=wait_s", source)
        body = inspect.getsource(loopback.ServerState.record_poll)
        self.assertEqual(body.count("_wait_for_peer_command("), 1)
        self.assertLess(
            body.index("_wait_for_peer_command("), body.index("_lookup_creation_time")
        )

    def test_every_queue_append_wakes_that_peers_poll(self) -> None:
        source = Path(loopback.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if "queue.append" not in segment:
                continue
            self.assertIn("_wake_command_waiters_locked", segment, node.name)
            names.add(node.name)
        self.assertTrue(_APPEND_SITES <= names)

    def test_bridges_send_the_cap_and_keep_lf(self) -> None:
        self.assertEqual(loopback.POLL_LONG_POLL_MAX_MS, 800)
        self.assertLessEqual(800, loopback.POLL_LONG_POLL_MAX_MS)
        for name, get_line in (
            ("MCPBridge.c", "m_Ctx.GET(cb, request);"),
            ("MCPClientBridge.c", "m_PollCtx.GET(m_PollCallback, request);"),
        ):
            raw = (_MISSION / name).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), name)
            self.assertNotIn(b"\r", raw, name)
            source = raw.decode("utf-8")
            self.assertNotIn("RestApi.SetOption", source)
            self.assertNotIn("ERESTOPTION", source)
            start = _brace_body(source, "protected void StartPoll()")
            literal = 'request = request + "&wait_ms=800";'
            self.assertEqual(start.count(literal), 1, name)
            self.assertLess(start.index('"&caps="'), start.index(literal), name)
            self.assertLess(start.index(literal), start.index(get_line), name)
            self.assertIn('m_PeerInstance != ""', start)
            self.assertIn("&inst=", start)
            self.assertNotIn('&inst=""', start)
            on_tick = _brace_body(source, "void OnTick(float timeslice)")
            self.assertLess(
                on_tick.index("if (m_PollInFlight)"), on_tick.index("if (m_RepollNow)")
            )
            self.assertLess(
                on_tick.index("if (m_RepollNow)"),
                on_tick.index("m_Accum = m_Accum + timeslice;"),
            )
            success = _brace_body(source, "void OnPollSuccess(string data, int dataSize)")
            self.assertIn("if (count == 0 && m_SincePollS >= 0.4)", success)
        server = (_MISSION / "MCPBridge.c").read_text(encoding="utf-8")
        self.assertIn("m_RepollNow = false;", _brace_body(server, "void MCPBridge()"))
        client = (_MISSION / "MCPClientBridge.c").read_text(encoding="utf-8")
        self.assertIn("m_RepollNow = false;", _brace_body(client, "void MCPClientBridge()"))
        self.assertIn("#ifndef DAYZ_1_29", client)

    @slow_test
    def test_enqueue_during_the_wait_is_delivered(self) -> None:
        entered = _on_wait(self.daemon.state, "client", lambda: None)
        errors: list[BaseException] = []

        def publish() -> None:
            try:
                if not entered.wait(2.0):
                    raise AssertionError("poll did not enter the wait")
                time.sleep(0.12)
                self.daemon.enqueue()
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=publish)
        worker.start()
        self.addCleanup(lambda: worker.join(timeout=2))
        elapsed, status, raw = self._read(
            {"peer": "client", "wait_ms": "800"}, timeout=3.0
        )
        worker.join(timeout=2)
        self.assertEqual(errors, [])
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertEqual([command["cmd"] for command in body["commands"]], ["camera_get"])
        self.assertGreater(elapsed, 0.08)
        self.assertLess(elapsed, 0.5)

    @slow_test
    def test_no_command_returns_empty_near_wait_ms_and_counts_one_poll(self) -> None:
        state = self.daemon.state
        snap: dict[str, object] = {}

        def grab() -> None:
            snap["count"] = state._unaccredited_poll_counts.get(
                loopback.BINDING_LEGACY, 0
            )
            snap["last"] = state._last_poll_at.get("client")
            snap["polls"] = state._peer_load["client"]["polls"]

        entered = _on_wait(state, "client", grab)
        elapsed, status, raw = self._read(
            {"peer": "client", "wait_ms": "150", "ia": "1", "pc": "2", "lpims": "300"},
            timeout=2.0,
        )
        self.assertTrue(entered.is_set())
        self.assertEqual(status, 200, raw)
        self.assertEqual(raw, _EMPTY)
        self.assertGreater(elapsed, 0.10)
        self.assertLess(elapsed, 0.45)
        self.assertEqual(snap["count"], 0)
        self.assertIsNone(snap["last"])
        self.assertIsNone(snap["polls"])
        self.assertEqual(
            state._unaccredited_poll_counts.get(loopback.BINDING_LEGACY), 1
        )
        self.assertIsInstance(state._last_poll_at["client"], float)
        with state._lock:
            applies = state._load_reading_applies_locked("client", None, False)
        self.assertTrue(applies)
        self.assertEqual(state._peer_load["client"]["polls"], 2)
        self.assertEqual(state._peer_load["client"]["init_attempts"], 1)
        self.assertAlmostEqual(state._peer_load["client"]["last_poll_interval_s"], 0.3)

    @slow_test
    def test_wait_ms_above_the_cap_is_clamped(self) -> None:
        elapsed, status, raw = self._read(
            {"peer": "client", "wait_ms": "60000"}, timeout=5.0
        )
        self.assertEqual(status, 200, raw)
        self.assertEqual(raw, _EMPTY)
        self.assertGreater(elapsed, 0.6)
        self.assertLess(elapsed, 1.4)

    @slow_test
    def test_delay_ms_applies_when_the_command_arrives_during_the_wait(self) -> None:
        status, payload = self.daemon.state.set_poll_delay(200)
        self.assertEqual(status, 200, payload)
        entered = _on_wait(self.daemon.state, "client", lambda: None)
        errors: list[BaseException] = []

        def publish() -> None:
            try:
                if not entered.wait(2.0):
                    raise AssertionError("poll did not enter the wait")
                time.sleep(0.12)
                self.daemon.enqueue()
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=publish)
        worker.start()
        self.addCleanup(lambda: worker.join(timeout=2))
        elapsed, http_status, raw = self._read(
            {"peer": "client", "wait_ms": "800"}, timeout=3.0
        )
        worker.join(timeout=2)
        self.assertEqual(errors, [])
        self.assertEqual(http_status, 200, raw)
        body = json.loads(raw)
        self.assertEqual([command["cmd"] for command in body["commands"]], ["camera_get"])
        self.assertGreater(elapsed, 0.25)
        self.assertLess(elapsed, 0.7)

    @slow_test
    def test_one_retained_get_attributes_once_on_wake(self) -> None:
        state = self.daemon.state
        state.install_bound_peer(
            instance=_INST,
            role="client",
            pid=_PID,
            run_id="poll-lp",
            creation_time_utc=_CTIME,
        )
        lookups: list[int] = []
        real_lookup = state._lookup_creation_time

        def wrapped(pid: int) -> str | None:
            lookups.append(pid)
            return real_lookup(pid)

        state._lookup_creation_time = wrapped
        binding = state._bindings[_INST]
        snap: dict[str, object] = {}

        def grab() -> None:
            snap["lookups"] = list(lookups)
            snap["presented"] = _PID in binding.presented_pids
            snap["bound_at"] = state._bound_last_poll_at.get("client")
            snap["last"] = state._last_poll_at.get("client")
            snap["polls"] = state._peer_load["client"]["polls"]
            snap["unaccredited"] = dict(state._unaccredited_poll_counts)

        entered = _on_wait(state, "client", grab)
        errors: list[BaseException] = []

        def publish() -> None:
            try:
                if not entered.wait(2.0):
                    raise AssertionError("poll did not enter the wait")
                time.sleep(0.05)
                self.daemon.enqueue()
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=publish)
        worker.start()
        self.addCleanup(lambda: worker.join(timeout=2))
        elapsed, status, raw = self._read(
            {
                "peer": "client",
                "inst": _INST,
                "wait_ms": "800",
                "ia": "1",
                "pc": "4",
                "lpims": "250",
            },
            timeout=3.0,
        )
        worker.join(timeout=2)
        self.assertEqual(errors, [])
        self.assertEqual(status, 200, raw)
        self.assertLess(elapsed, 0.5)
        body = json.loads(raw)
        self.assertEqual([command["cmd"] for command in body["commands"]], ["camera_get"])
        self.assertEqual(snap["lookups"], [])
        self.assertIs(snap["presented"], False)
        self.assertIsNone(snap["bound_at"])
        self.assertIsNone(snap["last"])
        self.assertIsNone(snap["polls"])
        self.assertEqual(lookups, [_PID])
        self.assertIn(_PID, binding.presented_pids)
        self.assertIsInstance(state._bound_last_poll_at["client"], float)
        self.assertIsInstance(state._last_poll_at["client"], float)
        self.assertEqual(state._unaccredited_poll_counts, snap["unaccredited"])
        self.assertEqual(state._unaccredited_poll_counts, {})
        with state._lock:
            applies = state._load_reading_applies_locked("client", _INST, True)
        self.assertTrue(applies)
        self.assertEqual(state._peer_load["client"]["polls"], 4)

    @slow_test
    def test_a_server_command_does_not_wake_a_client_poll(self) -> None:
        entered = _on_wait(self.daemon.state, "client", lambda: None)
        errors: list[BaseException] = []

        def publish() -> None:
            try:
                if not entered.wait(2.0):
                    raise AssertionError("poll did not enter the wait")
                time.sleep(0.05)
                self.daemon.enqueue("query_all_players", "server")
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=publish)
        worker.start()
        self.addCleanup(lambda: worker.join(timeout=2))
        elapsed, status, raw = self._read(
            {"peer": "client", "wait_ms": "200"}, timeout=2.0
        )
        worker.join(timeout=2)
        self.assertEqual(errors, [])
        self.assertEqual(status, 200, raw)
        self.assertEqual(raw, _EMPTY)
        self.assertGreater(elapsed, 0.15)
        self.assertLess(elapsed, 0.45)
        code, polled = self.daemon.state.record_poll("server")
        self.assertEqual(code, 200)
        self.assertEqual(
            [command["cmd"] for command in polled["commands"]], ["query_all_players"]
        )

    @slow_test
    def test_shutdown_releases_the_retained_poll(self) -> None:
        entered = _on_wait(self.daemon.state, "client", lambda: None)
        box: dict[str, object] = {}

        def run() -> None:
            try:
                status, raw = self.daemon.open(
                    {"peer": "client", "wait_ms": "800"}, timeout=3.0
                )
                box["result"] = (status, raw)
            except BaseException as exc:
                box["error"] = exc

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(2.0), "poll did not enter the wait")
        released = time.monotonic()
        self.daemon.state.cancel_pending()
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", box)
        status, raw = box["result"]
        self.assertEqual(status, 200)
        self.assertEqual(raw, _EMPTY)
        self.assertLess(time.monotonic() - released, 0.4)


def _poll_call(
    daemon: _Daemon, peer: str, sink: list, lock: threading.Lock
) -> None:
    started = time.monotonic()
    try:
        status, raw = daemon.open({"peer": peer, "wait_ms": "800"}, timeout=3.0)
        error: BaseException | None = None
    except BaseException as exc:
        status, raw, error = 0, b"", exc
    elapsed = time.monotonic() - started
    with lock:
        sink.append((peer, status, raw, elapsed, error))


class PollWaitMsParseTest(unittest.TestCase):
    """F2/F3. ASCII digits only; the numeric value is the wait, not the length."""

    def setUp(self) -> None:
        self.daemon = _Daemon()
        self.addCleanup(self.daemon.close)

    def test_zero_padded_wait_ms_is_the_numeric_value(self) -> None:
        self.assertEqual(loopback._parse_poll_wait_s({"wait_ms": ["00000000"]}), 0.0)
        self.assertEqual(
            loopback._parse_poll_wait_s({"wait_ms": ["00000001"]}), 0.001
        )
        for raw_wait in ("00000000", "00000001"):
            with self.subTest(wait_ms=raw_wait):
                elapsed, status, raw = self._read(raw_wait)
                self.assertEqual(status, 200, raw)
                self.assertEqual(raw, _EMPTY)
                self.assertLess(elapsed, 0.2)

    def test_superscript_wait_ms_does_not_raise(self) -> None:
        # "²" passes str.isdigit and int() raises. It is not a wait.
        self.assertEqual(loopback._parse_poll_wait_s({"wait_ms": ["²"]}), 0.0)
        elapsed, status, raw = self._read("²")
        self.assertEqual(status, 200, raw)
        self.assertEqual(raw, _EMPTY)
        self.assertLess(elapsed, 0.2)

    def _read(self, raw_wait: str) -> tuple[float, int, bytes]:
        started = time.monotonic()
        status, raw = self.daemon.open(
            {"peer": "client", "wait_ms": raw_wait}, timeout=2.0
        )
        return time.monotonic() - started, status, raw


class PollQuotaTest(unittest.TestCase):
    """F1. One retained GET /poll wait per peer. The rest answer immediately."""

    def setUp(self) -> None:
        self.daemon = _Daemon()
        self.addCleanup(self.daemon.close)

    def _delivered_result_id(self) -> int:
        payload = self.daemon.enqueue()
        command_id = payload.get("id")
        self.assertIsInstance(command_id, int)
        assert isinstance(command_id, int)
        code, polled = self.daemon.state.record_poll("client")
        self.assertEqual(code, 200, polled)
        self.assertEqual(polled["commands"][0]["cmd"], "camera_get")
        return command_id

    def _watch(self, peer: str) -> tuple[threading.Event, dict[str, int]]:
        """Count entries into this peer's Condition.wait. One retained poll."""

        cv = self.daemon.state._command_cv[peer]
        real_release = cv._release_save
        entered = threading.Event()
        count = {"n": 0}

        def release_save():
            saved = real_release()
            count["n"] += 1
            entered.set()
            return saved

        cv._release_save = release_save
        return entered, count

    def _start_poll(
        self,
        peer: str,
        done: list[tuple],
        lock: threading.Lock,
        threads: list[threading.Thread],
    ) -> threading.Thread:
        thread = threading.Thread(
            target=_poll_call, args=(self.daemon, peer, done, lock)
        )
        threads.append(thread)
        thread.start()
        return thread

    def _rows(self, done: list[tuple], lock: threading.Lock, peer: str) -> list[tuple]:
        with lock:
            return [row for row in done if row[0] == peer]

    def _assert_immediate(self, rows: list[tuple], expected: int) -> None:
        self.assertEqual(len(rows), expected, rows)
        for row in rows:
            self.assertIsNone(row[4], row)
            self.assertEqual(row[1], 200, row)
            self.assertEqual(row[2], _EMPTY)
            # Under the 0.8s cap, so a poll that actually waited still fails.
            self.assertLess(row[3], 0.5, row)

    def test_one_retained_poll_per_peer_and_result_still_answers(self) -> None:
        # The holder is inside Condition.wait before the extras connect.
        # Firing them all at once counts not-yet-accepted sockets as retained.
        command_id = self._delivered_result_id()
        client_entered, client_waits = self._watch("client")
        server_entered, server_waits = self._watch("server")
        lock = threading.Lock()
        done: list[tuple] = []
        threads: list[threading.Thread] = []
        self.addCleanup(self._join, threads)

        client_holder = self._start_poll("client", done, lock, threads)
        self.assertTrue(client_entered.wait(2.0), "client poll did not wait")
        self.assertEqual(client_waits["n"], 1)
        extra_n = 8
        for _ in range(extra_n):
            self._start_poll("client", done, lock, threads)
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline and len(self._rows(done, lock, "client")) < extra_n:
            time.sleep(0.01)
        self._assert_immediate(self._rows(done, lock, "client"), extra_n)
        self.assertEqual(client_waits["n"], 1, "more than one client /poll waited")
        self.assertTrue(client_holder.is_alive())

        server_holder = self._start_poll("server", done, lock, threads)
        self.assertTrue(server_entered.wait(2.0), "server poll did not wait")
        self._start_poll("server", done, lock, threads)
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline and len(self._rows(done, lock, "server")) < 1:
            time.sleep(0.01)
        self._assert_immediate(self._rows(done, lock, "server"), 1)
        self.assertEqual(server_waits["n"], 1, "server /poll was not its own slot")
        self.assertTrue(server_holder.is_alive())

        status, raw = self.daemon.post_json("/result", {"id": command_id, "ok": 1})
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertTrue(body.get("ok"), body)
        self.assertFalse(body.get("discarded", False), body)
        stored = self.daemon.state.take_result(command_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.get("ok"), 1)

        self.daemon.enqueue()
        client_holder.join(timeout=2.0)
        self.assertFalse(client_holder.is_alive())
        again_entered, _again_waits = self._watch("client")
        self._start_poll("client", done, lock, threads)
        self.assertTrue(again_entered.wait(1.0), "poll slot was not released after the wait")
        self.daemon.state.cancel_pending()
        self._join(threads)
        self.assertFalse(any(thread.is_alive() for thread in threads))

    def test_slot_is_released_when_the_wait_raises(self) -> None:
        cv = self.daemon.state._command_cv["client"]

        def boom(timeout: float | None = None) -> None:
            raise RuntimeError("injected wait failure")

        cv.wait = boom  # type: ignore[method-assign]
        try:
            # Python 3.14 raises RemoteDisconnected from getresponse(); urllib
            # does not wrap that call as URLError.
            with self.assertRaises(
                (http.client.RemoteDisconnected, urllib.error.URLError)
            ):
                self.daemon.open({"peer": "client", "wait_ms": "200"}, timeout=2.0)
        finally:
            del cv.wait
        entered, waits = self._watch("client")
        lock = threading.Lock()
        done: list[tuple] = []
        threads: list[threading.Thread] = []
        self.addCleanup(self._join, threads)
        holder = self._start_poll("client", done, lock, threads)
        self.assertTrue(entered.wait(1.0), "a later /poll could not take the slot")
        self.assertEqual(waits["n"], 1)
        self._start_poll("client", done, lock, threads)
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline and len(self._rows(done, lock, "client")) < 1:
            time.sleep(0.01)
        self._assert_immediate(self._rows(done, lock, "client"), 1)
        self.assertEqual(waits["n"], 1)
        self.assertTrue(holder.is_alive())
        self.daemon.state.cancel_pending()
        self._join(threads)
        self.assertFalse(any(thread.is_alive() for thread in threads))

    @slow_test
    def test_session_waits_await_and_two_polls_leave_result_and_enqueue(self) -> None:
        from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator

        # 64 session waits + 8 /await + one /poll per peer. The two routes
        # still need a worker after that.
        free = (
            loopback.MAX_HTTP_WORKERS
            - loopback.MAX_SESSION_QUEUE
            - loopback.AWAIT_LONG_POLL_MAX_IN_FLIGHT
            - 2
        )
        self.assertGreaterEqual(free, 2, free)
        command_id = self._delivered_result_id()
        ids = {"n": 0}

        def next_id() -> str:
            ids["n"] += 1
            return f"poll-quota-ticket-{ids['n']}"

        coordinator = SessionCoordinator(
            token_fn=lambda: "poll-quota-holder-token",
            id_fn=next_id,
            audit=lambda _event: True,
            cleanup=lambda _session_id, _lease_id, _reason, _vehicle_active: {},
        )
        self.daemon.state.coordination = coordinator
        holder_payload = {
            "platform": "codex",
            "pid": 1,
            "ppid": 0,
            "started_at_utc": "2026-11-01T00:00:00Z",
            "session_id": "poll-quota-holder",
            "task_label": "holder",
        }
        holder = ClientIdentity.from_payload(holder_payload)
        status, active = coordinator.acquire(holder, "hold the lease")
        self.assertEqual(status, 200, active)
        lease_token = active.get("lease_token")
        self.assertIsInstance(lease_token, str)
        tickets: list[tuple[dict, str]] = []
        session_waits = loopback.MAX_SESSION_QUEUE
        for index in range(session_waits):
            client = ClientIdentity.from_payload(
                {
                    "platform": "codex",
                    "pid": 1000 + index,
                    "ppid": 1,
                    "started_at_utc": "2026-11-01T00:00:00Z",
                    "session_id": f"poll-quota-waiter-{index}",
                    "task_label": "waiter",
                }
            )
            queued_status, queued = coordinator.enqueue(
                client, "queued", f"op-{index}"
            )
            self.assertEqual(queued_status, 202, queued)
            ticket = queued.get("ticket")
            self.assertIsInstance(ticket, str)
            assert isinstance(ticket, str)
            tickets.append((client.to_payload(), ticket))

        entered = threading.Event()
        entered_count = {"n": 0}
        entered_lock = threading.Lock()
        original_wait = coordinator.wait

        def counting_wait(client, ticket_id, timeout_s):
            with entered_lock:
                entered_count["n"] += 1
                if entered_count["n"] >= session_waits:
                    entered.set()
            return original_wait(client, ticket_id, timeout_s)

        coordinator.wait = counting_wait  # type: ignore[method-assign]
        wait_timeout_s = 4.0
        wait_threads: list[threading.Thread] = []

        def session_wait(identity: dict, ticket: str) -> None:
            url = (
                self.daemon.base
                + "/session/wait?"
                + urllib.parse.urlencode({"key": self.daemon.key})
            )
            request = urllib.request.Request(
                url,
                data=json.dumps(
                    {
                        "identity": identity,
                        "ticket": ticket,
                        "timeout_s": wait_timeout_s,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=wait_timeout_s + 3.0):
                    return
            except Exception:
                return

        for identity, ticket in tickets:
            thread = threading.Thread(target=session_wait, args=(identity, ticket))
            wait_threads.append(thread)
            thread.start()
        self.assertTrue(entered.wait(5.0), "session waits did not enter")

        await_held = loopback.AWAIT_LONG_POLL_MAX_IN_FLIGHT
        await_entered = threading.Event()
        await_count = {"n": 0}
        original_await = self.daemon.state.await_stored_result

        def counting_await(command_id_arg, remove=False, wait_s=0.0, cancel=None):
            with entered_lock:
                await_count["n"] += 1
                if await_count["n"] >= await_held:
                    await_entered.set()
            return original_await(command_id_arg, remove, wait_s, cancel)

        self.daemon.state.await_stored_result = counting_await  # type: ignore[method-assign]
        await_threads = [
            threading.Thread(
                target=self._await_held, name=f"await-{index}"
            )
            for index in range(await_held)
        ]
        for thread in await_threads:
            thread.start()
        self.assertTrue(await_entered.wait(2.0), "await waits did not enter")

        # One retained /poll per peer, then a few more of the same peer.
        # Those must answer at once. 64 + 8 + 2 stay held, inside the 96
        # workers, so /result and /enqueue still have one. Launching every
        # spare worker at once is rejected or still sitting in accept(); that
        # is not a retained /poll.
        client_entered, client_waits = self._watch("client")
        server_entered, server_waits = self._watch("server")
        lock = threading.Lock()
        done: list[tuple] = []
        poll_threads: list[threading.Thread] = []

        def _join_holders() -> None:
            self.daemon.state.cancel_pending()
            for thread in poll_threads:
                thread.join(timeout=2.0)
            for thread in await_threads:
                thread.join(timeout=3.0)
            for thread in wait_threads:
                thread.join(timeout=wait_timeout_s + 2.0)

        self.addCleanup(_join_holders)
        client_holder = self._start_poll("client", done, lock, poll_threads)
        self.assertTrue(client_entered.wait(2.0), "client poll did not wait")
        server_holder = self._start_poll("server", done, lock, poll_threads)
        self.assertTrue(server_entered.wait(2.0), "server poll did not wait")
        self.assertEqual(client_waits["n"], 1)
        self.assertEqual(server_waits["n"], 1)
        extra_n = 4
        for _ in range(extra_n):
            self._start_poll("client", done, lock, poll_threads)
        self._start_poll("server", done, lock, poll_threads)
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            client_done = len(self._rows(done, lock, "client"))
            server_done = len(self._rows(done, lock, "server"))
            if client_done >= extra_n and server_done >= 1:
                break
            time.sleep(0.01)
        self._assert_immediate(self._rows(done, lock, "client"), extra_n)
        self._assert_immediate(self._rows(done, lock, "server"), 1)
        self.assertEqual(client_waits["n"], 1)
        self.assertEqual(server_waits["n"], 1)
        self.assertTrue(client_holder.is_alive())
        self.assertTrue(server_holder.is_alive())

        result_status, result_raw = self.daemon.post_json(
            "/result", {"id": command_id, "ok": 1}, timeout=2.0
        )
        self.assertEqual(result_status, 200, result_raw)
        result_body = json.loads(result_raw)
        self.assertTrue(result_body.get("ok"), result_body)
        enqueue_status, enqueue_raw = self.daemon.post_json(
            "/enqueue",
            {
                "cmd": "query_all_players",
                "args": {},
                "peer": "server",
                "identity": holder_payload,
                "lease_token": lease_token,
                "operation_timeout_s": 30,
            },
            timeout=2.0,
        )
        self.assertEqual(enqueue_status, 200, enqueue_raw)
        enqueue_body = json.loads(enqueue_raw)
        self.assertIsInstance(enqueue_body.get("id"), int)
        self.daemon.state.cancel_pending()
        self._join(poll_threads)
        for thread in await_threads:
            thread.join(timeout=3.0)
        for thread in wait_threads:
            thread.join(timeout=wait_timeout_s + 2.0)
        self.assertFalse(any(thread.is_alive() for thread in poll_threads))
        self.assertFalse(any(thread.is_alive() for thread in await_threads))
        self.assertFalse(any(thread.is_alive() for thread in wait_threads))

    def _await_held(self) -> None:
        url = (
            self.daemon.base
            + "/await?"
            + urllib.parse.urlencode(
                {"key": self.daemon.key, "id": "999999", "wait_ms": "2000"}
            )
        )
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5.0):
                return
        except Exception:
            return

    @staticmethod
    def _join(threads: list[threading.Thread]) -> None:
        for thread in threads:
            thread.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
