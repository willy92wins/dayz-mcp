"""Long-poll of GET /poll (wait_ms).

Fast cases return immediately. Cases that wait on a real clock are @slow_test.
The benchmark drives checks/poll_long_poll_bench.py against an ephemeral
loopback port: it does not launch DayZ and does not bind port 8765.
"""

from __future__ import annotations

import ast
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


if __name__ == "__main__":
    unittest.main()
