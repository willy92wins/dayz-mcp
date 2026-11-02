"""Long-poll of GET and POST /await (wait_ms).

Fast cases return immediately. Cases that wait on a real clock are @slow_test.
The benchmark drives checks/await_long_poll_bench.py against an ephemeral
loopback port: it does not launch DayZ and does not bind port 8765.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dayz_mcp import loopback, server
from tests._tiers import slow_test

_PENDING = b'{"status":"pending"}'
_HOLDER = {
    "identity": {
        "platform": "codex",
        "pid": 1,
        "ppid": 0,
        "started_at_utc": "2026-11-01T00:00:00Z",
        "session_id": "await-long-poll",
        "task_label": "",
    },
    "lease_token": "await-token",
}


class _Daemon:
    def __init__(self) -> None:
        self.key = "await-long-poll-key"
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

    def open(
        self,
        method: str,
        query: dict[str, str],
        payload: dict | None = None,
        timeout: float = 1.0,
    ) -> tuple[int, bytes]:
        params = dict(query)
        params["key"] = self.key
        url = self.base + "/await?" + urllib.parse.urlencode(params)
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            try:
                return int(exc.code), exc.read()
            finally:
                exc.close()

    def enqueue(self) -> int:
        status, payload = self.state.enqueue_command(
            "camera_get", {}, peer="client", operation_timeout_s=30
        )
        if status != 200 or not isinstance(payload, dict):
            raise AssertionError(f"enqueue failed: {status} {payload!r}")
        command_id = payload.get("id")
        if not isinstance(command_id, int) or isinstance(command_id, bool):
            raise AssertionError(f"enqueue id missing: {payload!r}")
        return command_id


class AwaitHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.daemon = _Daemon()
        self.addCleanup(self.daemon.close)

    def _read(
        self,
        method: str = "GET",
        query: dict[str, str] | None = None,
        payload: dict | None = None,
        timeout: float = 1.0,
    ) -> tuple[float, int, bytes]:
        started = time.monotonic()
        status, raw = self.daemon.open(method, query or {"id": "999999"}, payload, timeout)
        return time.monotonic() - started, status, raw

    def test_absent_wait_ms_is_the_old_pending_body(self) -> None:
        elapsed, status, raw = self._read()
        self.assertEqual(status, 200)
        self.assertEqual(raw, _PENDING)
        self.assertLess(elapsed, 0.2)

    def test_unusable_wait_ms_does_not_wait(self) -> None:
        for raw_wait in ("-1", "1.5", "abc", ""):
            with self.subTest(wait_ms=raw_wait):
                elapsed, status, raw = self._read(query={"id": "999999", "wait_ms": raw_wait})
                self.assertEqual(status, 200)
                self.assertEqual(raw, _PENDING)
                self.assertLess(elapsed, 0.2)

    def test_zero_wait_ms_stays_the_old_pending_body(self) -> None:
        elapsed, status, raw = self._read(query={"id": "999999", "wait_ms": "0"})
        self.assertEqual(status, 200)
        self.assertEqual(raw, _PENDING)
        self.assertLess(elapsed, 0.2)

    def test_superscript_and_zero_padded_wait_ms(self) -> None:
        # "²" is a digit to str.isdigit and makes int() raise. Zero-padded
        # ASCII is the numeric value, not the 2s length shortcut.
        self.assertEqual(loopback._parse_await_wait_s({"wait_ms": ["²"]}), (0.0, False))
        self.assertEqual(
            loopback._parse_await_wait_s({"wait_ms": ["00000000"]}), (0.0, True)
        )
        self.assertEqual(
            loopback._parse_await_wait_s({"wait_ms": ["00000001"]}), (0.001, True)
        )
        cases = (("²", False), ("00000000", False), ("00000001", True))
        for raw_wait, expect_field in cases:
            with self.subTest(wait_ms=raw_wait):
                elapsed, status, raw = self._read(
                    query={"id": "999999", "wait_ms": raw_wait},
                    timeout=1.0,
                )
                self.assertEqual(status, 200, raw)
                body = json.loads(raw)
                self.assertEqual(body.get("status"), "pending", body)
                self.assertLess(elapsed, 0.2)
                if expect_field:
                    self.assertIs(body.get(loopback.AWAIT_LONG_POLL_FIELD), True)
                else:
                    self.assertEqual(raw, _PENDING)

    def test_stored_result_returns_without_waiting_out_the_cap(self) -> None:
        command_id = self.daemon.enqueue()
        code, stored = self.daemon.state.store_result({"id": command_id, "ok": 1})
        self.assertEqual(code, 200, stored)
        self.assertFalse(stored.get("discarded", False), stored)
        elapsed, status, raw = self._read(
            query={"id": str(command_id), "remove": "1", "wait_ms": "2000"}
        )
        self.assertEqual(status, 200)
        body = json.loads(raw)
        self.assertEqual(body["status"], "done")
        self.assertNotIn(loopback.AWAIT_LONG_POLL_FIELD, body)
        self.assertLess(elapsed, 0.2)
        self.assertIsNone(self.daemon.state.take_result(command_id))

    @slow_test
    def test_result_during_the_wait_is_delivered(self) -> None:
        command_id = self.daemon.enqueue()

        def publish() -> None:
            time.sleep(0.12)
            code, stored = self.daemon.state.store_result({"id": command_id, "ok": 1})
            if code != 200 or stored.get("discarded"):
                raise AssertionError(f"store_result failed: {code} {stored!r}")

        worker = threading.Thread(target=publish)
        worker.start()
        self.addCleanup(lambda: worker.join(timeout=2))
        elapsed, status, raw = self._read(
            query={"id": str(command_id), "remove": "1", "wait_ms": "2000"},
            timeout=5.0,
        )
        worker.join(timeout=2)
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["result"]["ok"], 1)
        self.assertNotIn(loopback.AWAIT_LONG_POLL_FIELD, body)
        self.assertGreater(elapsed, 0.08)
        self.assertLess(elapsed, 0.8)

    @slow_test
    def test_no_result_returns_pending_after_wait_ms(self) -> None:
        for method, payload in (("GET", None), ("POST", _HOLDER)):
            with self.subTest(method=method):
                elapsed, status, raw = self._read(
                    method,
                    {"id": "999999", "wait_ms": "150"},
                    payload,
                    timeout=2.0,
                )
                self.assertEqual(status, 200, raw)
                body = json.loads(raw)
                self.assertEqual(body["status"], "pending")
                self.assertIs(body.get(loopback.AWAIT_LONG_POLL_FIELD), True)
                self.assertGreater(elapsed, 0.10)
                self.assertLess(elapsed, 0.45)

    @slow_test
    def test_wait_ms_above_the_cap_is_clamped(self) -> None:
        elapsed, status, raw = self._read(
            query={"id": "999999", "wait_ms": "60000"},
            timeout=5.0,
        )
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertEqual(body["status"], "pending")
        self.assertIs(body.get(loopback.AWAIT_LONG_POLL_FIELD), True)
        self.assertGreater(elapsed, loopback.AWAIT_LONG_POLL_MAX_S - 0.25)
        self.assertLess(elapsed, loopback.AWAIT_LONG_POLL_MAX_S + 0.8)

    @slow_test
    def test_remove_once_with_several_waiters(self) -> None:
        command_id = self.daemon.enqueue()
        found: list[dict] = []
        errors: list[BaseException] = []

        def wait_one() -> None:
            try:
                status, raw = self.daemon.open(
                    "GET",
                    {"id": str(command_id), "remove": "1", "wait_ms": "350"},
                    timeout=5.0,
                )
                if status != 200:
                    raise AssertionError(f"{status} {raw!r}")
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise AssertionError(body)
                found.append(body)
            except BaseException as exc:
                errors.append(exc)

        workers = [threading.Thread(target=wait_one) for _index in range(3)]
        for worker in workers:
            worker.start()
        try:
            time.sleep(0.05)
            code, stored = self.daemon.state.store_result({"id": command_id, "ok": 1})
            self.assertEqual(code, 200, stored)
            self.assertFalse(stored.get("discarded", False), stored)
        finally:
            for worker in workers:
                worker.join(timeout=5)
        self.assertEqual(errors, [])
        done = [body for body in found if body.get("status") == "done"]
        pending = [body for body in found if body.get("status") == "pending"]
        self.assertEqual(len(done), 1, found)
        self.assertEqual(len(pending), 2, found)
        self.assertTrue(
            all(body.get(loopback.AWAIT_LONG_POLL_FIELD) is True for body in pending),
            pending,
        )
        self.assertIsNone(self.daemon.state.take_result(command_id))

    @slow_test
    def test_discard_during_the_wait_wakes_with_the_synthetic_result(self) -> None:
        command_id = self.daemon.enqueue()
        entered = threading.Event()
        original = self.daemon.state.await_stored_result

        def wrapped(command_id_arg: int, remove: bool = False, wait_s: float = 0.0, cancel=None):
            entered.set()
            return original(command_id_arg, remove, wait_s, cancel)

        self.daemon.state.await_stored_result = wrapped  # type: ignore[method-assign]
        outcome: dict[str, object] = {}

        def request() -> None:
            started = time.monotonic()
            status, raw = self.daemon.open(
                "GET",
                {"id": str(command_id), "remove": "1", "wait_ms": "2000"},
                timeout=5.0,
            )
            outcome["status"] = status
            outcome["raw"] = raw
            outcome["elapsed"] = time.monotonic() - started

        worker = threading.Thread(target=request)
        worker.start()
        self.assertTrue(entered.wait(2.0))
        self.daemon.state.cancel_pending("stale_discarded")
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome["status"], 200)
        body = json.loads(outcome["raw"])
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["result"]["error"], "stale_discarded")
        self.assertLess(outcome["elapsed"], 1.0)

    @slow_test
    def test_discard_before_the_wait_is_already_stored(self) -> None:
        command_id = self.daemon.enqueue()
        self.daemon.state.cancel_pending("stale_discarded")
        elapsed, status, raw = self._read(
            query={"id": str(command_id), "remove": "1", "wait_ms": "2000"},
            timeout=2.0,
        )
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertEqual(body["result"]["error"], "stale_discarded")
        self.assertLess(elapsed, 1.0)

    @slow_test
    def test_shutdown_of_an_unknown_id_is_pending_without_the_field(self) -> None:
        entered = threading.Event()
        original = self.daemon.state.await_stored_result

        def wrapped(command_id_arg: int, remove: bool = False, wait_s: float = 0.0, cancel=None):
            entered.set()
            return original(command_id_arg, remove, wait_s, cancel)

        self.daemon.state.await_stored_result = wrapped  # type: ignore[method-assign]
        outcome: dict[str, object] = {}

        def request() -> None:
            started = time.monotonic()
            status, raw = self.daemon.open(
                "GET",
                {"id": "999999", "wait_ms": "2000"},
                timeout=5.0,
            )
            outcome["status"] = status
            outcome["raw"] = raw
            outcome["elapsed"] = time.monotonic() - started

        worker = threading.Thread(target=request)
        worker.start()
        self.assertTrue(entered.wait(2.0))
        self.daemon.state.cancel_pending()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome["status"], 200)
        self.assertEqual(outcome["raw"], _PENDING)
        self.assertLess(outcome["elapsed"], 1.0)


class AwaitQuotaTest(unittest.TestCase):
    @slow_test
    def test_result_is_stored_while_session_waits_and_long_polls_fill_the_pool(self) -> None:
        from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator

        session_waits = loopback.MAX_SESSION_QUEUE
        await_waits = loopback.MAX_HTTP_WORKERS - loopback.MAX_SESSION_QUEUE
        self.assertGreater(
            await_waits, loopback.AWAIT_LONG_POLL_MAX_IN_FLIGHT
        )
        ids = {"n": 0}

        def next_id() -> str:
            ids["n"] += 1
            return f"quota-ticket-{ids['n']}"

        daemon = _Daemon()
        self.addCleanup(daemon.close)
        # camera_get needs no lease. Enqueue before coordination is attached:
        # with a coordinator, enqueue_command requires an identity.
        command_id = daemon.enqueue()
        coordinator = SessionCoordinator(
            token_fn=lambda: "quota-holder-token",
            id_fn=next_id,
            audit=lambda _event: True,
            cleanup=lambda _session_id, _lease_id, _reason, _vehicle_active: {},
        )
        daemon.state.coordination = coordinator
        holder = ClientIdentity.from_payload(
            {
                "platform": "codex",
                "pid": 1,
                "ppid": 0,
                "started_at_utc": "2026-11-01T00:00:00Z",
                "session_id": "quota-holder",
                "task_label": "holder",
            }
        )
        status, _active = coordinator.acquire(holder, "hold the lease")
        self.assertEqual(status, 200, _active)
        tickets: list[tuple[dict, str]] = []
        for index in range(session_waits):
            client = ClientIdentity.from_payload(
                {
                    "platform": "codex",
                    "pid": 1000 + index,
                    "ppid": 1,
                    "started_at_utc": "2026-11-01T00:00:00Z",
                    "session_id": f"quota-waiter-{index}",
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
                daemon.base
                + "/session/wait?"
                + urllib.parse.urlencode({"key": daemon.key})
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

        await_bodies: list[tuple[int, dict]] = []
        await_lock = threading.Lock()

        def one_await() -> None:
            try:
                status_code, raw = daemon.open(
                    "GET",
                    {"id": "999999", "wait_ms": "2000"},
                    timeout=5.0,
                )
                parsed = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                status_code = 0
                parsed = {"error": repr(exc)}
            if not isinstance(parsed, dict):
                parsed = {"error": repr(parsed)}
            with await_lock:
                await_bodies.append((status_code, parsed))

        await_threads = [
            threading.Thread(target=one_await) for _ in range(await_waits)
        ]
        for thread in await_threads:
            thread.start()

        def _join_holders() -> None:
            daemon.state.cancel_pending()
            for thread in await_threads:
                thread.join(timeout=3.0)
            for thread in wait_threads:
                thread.join(timeout=wait_timeout_s + 2.0)

        self.addCleanup(_join_holders)
        overflow = await_waits - loopback.AWAIT_LONG_POLL_MAX_IN_FLIGHT
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            with await_lock:
                immediate = sum(
                    1
                    for status_code, body in await_bodies
                    if status_code == 200
                    and loopback.AWAIT_LONG_POLL_FIELD not in body
                )
            if immediate >= overflow:
                break
            time.sleep(0.02)

        self.assertTrue(
            all(thread.is_alive() for thread in wait_threads),
            "a session wait returned before POST /result",
        )
        result_url = (
            daemon.base + "/result?" + urllib.parse.urlencode({"key": daemon.key})
        )
        result_request = urllib.request.Request(
            result_url,
            data=json.dumps({"id": command_id, "ok": 1}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(result_request, timeout=2.0) as response:
                result_status = int(response.status)
                result_body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                result_status = int(exc.code)
                result_body = json.loads(exc.read().decode("utf-8") or "{}")
            finally:
                exc.close()
        self.assertEqual(result_status, 200, result_body)
        self.assertTrue(result_body.get("ok"), result_body)
        stored = daemon.state.take_result(command_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.get("ok"), 1)
        with await_lock:
            immediate = sum(
                1
                for status_code, body in await_bodies
                if status_code == 200 and loopback.AWAIT_LONG_POLL_FIELD not in body
            )
        self.assertGreaterEqual(immediate, overflow, await_bodies)
        daemon.state.cancel_pending()
        for thread in await_threads:
            thread.join(timeout=3.0)
        for thread in wait_threads:
            thread.join(timeout=wait_timeout_s + 2.0)
        self.assertFalse(any(thread.is_alive() for thread in await_threads))
        self.assertFalse(any(thread.is_alive() for thread in wait_threads))


def _query_ok(case: unittest.TestCase, query: object, command_id: str) -> None:
    case.assertIsInstance(query, dict)
    assert isinstance(query, dict)
    case.assertEqual(query.get("id"), command_id)
    case.assertEqual(query.get("remove"), "1")
    wait_ms = query.get("wait_ms")
    case.assertIsInstance(wait_ms, str)
    assert isinstance(wait_ms, str)
    case.assertTrue(wait_ms.isdigit(), wait_ms)
    case.assertGreaterEqual(int(wait_ms), 1)
    case.assertLessEqual(int(wait_ms), 2000)


class ClientPollTest(unittest.IsolatedAsyncioTestCase):
    def _runtime(self, call):
        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._call = call
        return runtime

    async def test_old_daemon_still_sleeps_and_new_daemon_does_not(self) -> None:
        slept: list[float] = []

        async def fake_sleep(delay: float, *_args, **_kwargs) -> None:
            slept.append(delay)

        calls: list[object] = []
        replies = [
            (200, {"status": "pending"}),
            (200, {"status": "done", "result": {"ok": 1, "y": 1}}),
        ]

        def _call(method, path, payload=None, query=None, *_args):
            del method, path, payload
            calls.append(query)
            if not replies:
                raise AssertionError(f"extra await: {calls}")
            return replies.pop(0)

        runtime = self._runtime(_call)
        with mock.patch.object(server.asyncio, "sleep", fake_sleep):
            waited = await runtime._await_result("surface_query", 7, "client", 3.5)
        self.assertEqual(waited["y"], 1)
        self.assertEqual(len(calls), 2)
        _query_ok(self, calls[0], "7")
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], server.POLL_INTERVAL_S, places=2)

        slept.clear()
        calls.clear()
        replies[:] = [
            (200, {"status": "pending", "await_long_poll": True}),
            (200, {"status": "done", "result": {"ok": 1, "y": 2}}),
        ]
        with mock.patch.object(server.asyncio, "sleep", fake_sleep):
            waited = await runtime._await_result("surface_query", 8, "client", 3.5)
        self.assertEqual(waited["y"], 2)
        self.assertEqual(len(calls), 2)
        _query_ok(self, calls[0], "8")
        self.assertEqual(slept, [])

    async def test_post_404_falls_back_to_get_without_sleeping(self) -> None:
        from dayz_mcp.session_coordination import ClientIdentity

        slept: list[float] = []

        async def fake_sleep(delay: float, *_args, **_kwargs) -> None:
            slept.append(delay)

        runtime = self._runtime(None)
        runtime.identity = ClientIdentity.from_payload(_HOLDER["identity"])
        runtime._broker_admits = {4: {"generation": "", "lease_token": "await-token"}}
        calls: list[tuple] = []

        def _call(method, path, payload=None, query=None, *_args):
            calls.append((method, path, payload, query))
            if method == "POST":
                return 404, {"error": "not_found"}
            return 200, {"status": "done", "result": {"ok": 1, "y": 4}}

        runtime._call = _call
        with mock.patch.object(server.asyncio, "sleep", fake_sleep):
            waited = await runtime._await_result("surface_query", 4, "client", 3.5)
        self.assertEqual(waited["y"], 4)
        self.assertEqual(slept, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[1][0], "GET")
        _query_ok(self, calls[0][3], "4")
        _query_ok(self, calls[1][3], "4")

    async def test_last_second_of_the_budget_does_not_send_wait_ms(self) -> None:
        calls: list[object] = []

        def _call(method, path, payload=None, query=None, *_args):
            del method, path, payload
            calls.append(query)
            return 200, {"status": "done", "result": {"ok": 1, "y": 1}}

        runtime = self._runtime(_call)
        waited = await runtime._await_result("surface_query", 9, "client", 0.48)
        self.assertEqual(waited["y"], 1)
        self.assertIsInstance(calls[0], dict)
        assert isinstance(calls[0], dict)
        self.assertNotIn("wait_ms", calls[0])


class SlowAccreditationTest(unittest.IsolatedAsyncioTestCase):
    @slow_test
    async def test_budget_expiry_stays_timeout_waiting(self) -> None:
        from dayz_mcp import accredited_daemon_transport as transport

        daemon = _Daemon()
        self.addCleanup(daemon.close)
        _host, port = daemon.httpd.server_address

        def slow_verify(*_args, **_kwargs) -> bool:
            time.sleep(0.2)
            return True

        def request_with_refresh(
            *,
            method,
            path,
            query,
            body,
            headers,
            deadline,
            **_kwargs,
        ):
            return transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=int(port),
                key=daemon.key,
                method=method,
                path=path,
                query=dict(query),
                body=body,
                headers=dict(headers),
                deadline=float(deadline),
                expected_executable="dayz-mcp-test",
                expected_argv=["dayz-mcp-test"],
                expected_cwd="C:\\dayz-mcp-test",
            )

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._allow_stale_policy = False
        runtime._credential_provider = SimpleNamespace(
            request_with_refresh=request_with_refresh
        )
        runtime._ensure_daemon = lambda _deadline: False

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        with mock.patch.object(
            transport, "_connected_daemon_identity_verified", slow_verify
        ):
            with self.assertRaises(server.ToolError) as caught:
                await runtime._await_result("camera_get", 7, "client", 0.48)
        message = str(caught.exception)
        self.assertTrue(message.startswith("timeout waiting"), message)
        self.assertNotIn("daemon_response_ambiguous", message)

    @slow_test
    async def test_saturated_executor_with_slow_accreditation_stays_timeout_waiting(
        self,
    ) -> None:
        # Sol r2 F-03: budget 1.5s, the default executor held for 1.1s,
        # accreditation 200ms. On e2e345d, wait_ms baked before to_thread was
        # 499 and the call ended as daemon_response_ambiguous. The worker
        # must choose wait_ms from the deadline it still has, and the error
        # must stay "timeout waiting…".
        from dayz_mcp import accredited_daemon_transport as transport

        daemon = _Daemon()
        self.addCleanup(daemon.close)
        _host, port = daemon.httpd.server_address
        hold = threading.Event()
        entered: list[int] = []
        entered_lock = threading.Lock()

        def block() -> None:
            with entered_lock:
                entered.append(1)
            hold.wait(timeout=5.0)

        def release() -> None:
            hold.set()

        self.addCleanup(release)

        async def occupy(count: int) -> tuple[int, list[asyncio.Task]]:
            tasks = [
                asyncio.create_task(asyncio.to_thread(block)) for _ in range(count)
            ]
            last = -1
            stable = 0.0
            deadline = time.monotonic() + 2.0
            occupied = 0
            while time.monotonic() < deadline:
                await asyncio.sleep(0.02)
                with entered_lock:
                    occupied = len(entered)
                if occupied > 0 and occupied == last:
                    stable += 0.02
                    if stable >= 0.12:
                        break
                else:
                    stable = 0.0
                    last = occupied
            else:
                release()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.fail(f"default executor did not fill; entered={occupied}")
            return occupied, tasks

        pool, probes = await occupy(40)
        self.assertGreaterEqual(pool, 2)
        release()
        await asyncio.gather(*probes, return_exceptions=True)
        hold.clear()
        with entered_lock:
            entered.clear()
        occupied, blockers = await occupy(pool)
        self.assertGreaterEqual(occupied, pool)

        def slow_verify(*_args, **_kwargs) -> bool:
            time.sleep(0.2)
            return True

        seen: list[dict[str, str]] = []

        def request_with_refresh(
            *,
            method,
            path,
            query,
            body,
            headers,
            deadline,
            **_kwargs,
        ):
            seen.append(dict(query))
            return transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=int(port),
                key=daemon.key,
                method=method,
                path=path,
                query=dict(query),
                body=body,
                headers=dict(headers),
                deadline=float(deadline),
                expected_executable="dayz-mcp-test",
                expected_argv=["dayz-mcp-test"],
                expected_cwd="C:\\dayz-mcp-test",
            )

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._allow_stale_policy = False
        runtime._credential_provider = SimpleNamespace(
            request_with_refresh=request_with_refresh
        )
        runtime._ensure_daemon = lambda _deadline: False

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        try:
            with mock.patch.object(
                transport, "_connected_daemon_identity_verified", slow_verify
            ):
                with self.assertRaises(server.ToolError) as caught:
                    task = asyncio.create_task(
                        runtime._await_result("camera_get", 7, "client", 1.5)
                    )
                    await asyncio.sleep(1.1)
                    release()
                    await task
            message = str(caught.exception)
            self.assertTrue(message.startswith("timeout waiting"), (message, seen))
            self.assertNotIn("daemon_response_ambiguous", message)
        finally:
            release()

    @slow_test
    async def test_stale_wait_ms_on_recovered_retry_stays_timeout_waiting(
        self,
    ) -> None:
        # Sol r3 F-06: budget 1.5s, the first /await fails before any byte,
        # recovery takes 1.1s, accreditation 200ms. On 7d4cc4e the retry
        # resends the original wait_ms (about 497) with about 397ms left
        # before accreditation, so the socket dies as daemon_response_ambiguous
        # and call_bridge skips the tool_timeout cleanup.
        from dayz_mcp import accredited_daemon_transport as transport
        from dayz_mcp.session_coordination import ClientIdentity

        daemon = _Daemon()
        self.addCleanup(daemon.close)
        _host, port = daemon.httpd.server_address
        seen: list[dict[str, object]] = []
        cleanups: list[tuple[object, object]] = []
        await_attempts = {"n": 0}
        recoveries = {"n": 0}

        def ensure_daemon(deadline=None) -> bool:
            recoveries["n"] += 1
            if recoveries["n"] == 1:
                time.sleep(1.1)
            if deadline is not None and time.monotonic() >= float(deadline):
                return False
            return True

        def slow_verify(*_args, **_kwargs) -> bool:
            time.sleep(0.2)
            return True

        def request_with_refresh(
            *,
            method,
            path,
            query,
            body,
            headers,
            deadline,
            **_kwargs,
        ):
            del method, body
            copied = dict(query or {})
            if path == "/enqueue":
                seen.append({"path": path, "query": copied, "http": False})
                return 200, b'{"id": 7}'
            if path == "/abandon":
                seen.append({"path": path, "query": copied, "http": False})
                return 200, b'{"status":"abandoned"}'
            if path != "/await":
                seen.append({"path": path, "query": copied, "http": False})
                return 200, b"{}"
            await_attempts["n"] += 1
            if await_attempts["n"] == 1:
                seen.append({"path": path, "query": copied, "http": False})
                raise transport.AccreditedTransportError(
                    "daemon_transport_failure",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                )
            seen.append({"path": path, "query": copied, "http": True})
            return transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=int(port),
                key=daemon.key,
                method="GET",
                path=path,
                query=copied,
                body=None,
                headers=dict(headers or {}),
                deadline=float(deadline),
                expected_executable="dayz-mcp-test",
                expected_argv=["dayz-mcp-test"],
                expected_cwd="C:\\dayz-mcp-test",
            )

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._allow_stale_policy = False
        runtime._control = SimpleNamespace(
            active_lease_token=None, active_ticket=None
        )
        runtime.identity = ClientIdentity.from_payload(_HOLDER["identity"])
        runtime._credential_provider = SimpleNamespace(
            request_with_refresh=request_with_refresh
        )
        runtime._ensure_daemon = ensure_daemon

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        async def no_status(*_args, **_kwargs):
            return None

        runtime._liveness_message = liveness
        runtime.bridge_status_payload = no_status
        real_cleanup = server._shielded_broker_cleanup

        async def spy_cleanup(bound, command_id, reason, budget_deadline=None):
            cleanups.append((command_id, reason))
            return await real_cleanup(
                bound, command_id, reason, budget_deadline
            )

        with mock.patch.object(server, "_shielded_broker_cleanup", spy_cleanup):
            with mock.patch.object(
                transport, "_connected_daemon_identity_verified", slow_verify
            ):
                with self.assertRaises(server.ToolError) as caught:
                    await runtime.call_bridge("camera_get", {}, "client", 1.5)
        message = str(caught.exception)
        detail = (message, seen, cleanups, recoveries["n"])
        self.assertTrue(message.startswith("timeout waiting"), detail)
        self.assertNotIn("daemon_response_ambiguous", message)
        sent = [item for item in seen if item["http"]]
        self.assertTrue(sent, detail)
        self.assertTrue(
            all("wait_ms" not in item["query"] for item in sent), detail
        )
        self.assertEqual(cleanups, [(7, "tool_timeout")], detail)
        self.assertTrue(any(item["path"] == "/abandon" for item in seen), detail)

    async def _credential_replay(
        self, mode: str
    ) -> tuple[str, list[dict[str, object]], list[tuple[object, object]]]:
        # Sol r4 F-07: budget 1.5s, the refresh lock is held for 1.1s, then
        # accreditation takes 200ms. The real provider resends the original
        # wait_ms. On 3bfd5fc that replay outlives the socket.
        import tempfile

        from dayz_mcp import accredited_daemon_transport as transport
        from dayz_mcp import daemon_credential
        from dayz_mcp.daemon_policy import AccreditedDaemonPolicy
        from dayz_mcp.daemon_policy_contract import (
            authority_payload,
            authority_sha256,
        )
        from dayz_mcp.session_coordination import ClientIdentity

        daemon = _Daemon()
        self.addCleanup(daemon.close)
        _host, port = daemon.httpd.server_address
        seen: list[dict[str, object]] = []
        cleanups: list[tuple[object, object]] = []
        verified = {"n": 0}

        def verify(*_args, **_kwargs) -> bool:
            verified["n"] += 1
            if mode == "reaccreditation" and verified["n"] == 1:
                return False
            time.sleep(0.2)
            return True

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        keyfile = (Path(directory.name) / "daemon.key").resolve()
        stale = "stale-await-credential"
        keyfile.write_text(
            (stale if mode == "401" else daemon.key) + "\n", encoding="utf-8"
        )
        argv = (
            r"P:\Runtime\python.exe",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            str(int(port)),
        )
        payload = authority_payload(
            kind="normal",
            host="127.0.0.1",
            port=int(port),
            keyfile=str(keyfile),
            native_executable=r"P:\Runtime\python.exe",
            argv=argv,
            cwd=r"P:\DayZ_MCP_dev\tools",
            security_build_id=None,
        )
        provider = daemon_credential.RefreshingDaemonCredential(
            policy=AccreditedDaemonPolicy(
                kind="normal",
                host="127.0.0.1",
                port=int(port),
                keyfile=str(keyfile),
                native_executable=r"P:\Runtime\python.exe",
                argv=argv,
                cwd=r"P:\DayZ_MCP_dev\tools",
                security_build_id=None,
                authority_sha256=authority_sha256(**payload),
            )
        )
        if mode == "401":
            keyfile.write_text(daemon.key + "\n", encoding="utf-8")

        def request_fn(**kwargs: object) -> tuple[int, bytes]:
            path = str(kwargs.get("path"))
            query = dict(kwargs.get("query") or {})
            if path == "/enqueue":
                seen.append({"path": path, "query": query})
                return 200, b'{"id": 7}'
            if path == "/abandon":
                seen.append({"path": path, "query": query})
                return 200, b'{"status":"abandoned"}'
            if path != "/await":
                seen.append({"path": path, "query": query})
                return 200, b"{}"
            seen.append({"path": path, "query": query})
            return transport.verified_daemon_http_request(**kwargs)

        provider._request_fn = request_fn
        held = threading.Event()

        def hold_refresh() -> None:
            provider._refresh_lock.acquire()
            held.set()
            time.sleep(1.1)
            provider._refresh_lock.release()

        locker = threading.Thread(target=hold_refresh, daemon=True)
        locker.start()
        self.addCleanup(held.set)
        self.assertTrue(held.wait(timeout=2.0), "refresh lock was not taken")

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._allow_stale_policy = False
        runtime._control = SimpleNamespace(
            active_lease_token=None, active_ticket=None
        )
        runtime.identity = ClientIdentity.from_payload(_HOLDER["identity"])
        runtime._credential_provider = provider
        runtime._ensure_daemon = lambda _deadline: False

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        real_cleanup = server._shielded_broker_cleanup

        async def spy_cleanup(bound, command_id, reason, budget_deadline=None):
            cleanups.append((command_id, reason))
            return await real_cleanup(
                bound, command_id, reason, budget_deadline
            )

        with mock.patch.object(server, "_shielded_broker_cleanup", spy_cleanup):
            with mock.patch.object(
                transport, "_connected_daemon_identity_verified", verify
            ):
                with self.assertRaises(server.ToolError) as caught:
                    # camera_set is mutating, so the 401 path re-reads the key.
                    await runtime.call_bridge("camera_set", {}, "client", 1.5)
        locker.join(timeout=2.0)
        return str(caught.exception), seen, cleanups

    def _assert_replay_is_a_timeout(
        self,
        message: str,
        seen: list[dict[str, object]],
        cleanups: list[tuple[object, object]],
    ) -> None:
        detail = (message, seen, cleanups)
        self.assertTrue(message.startswith("timeout waiting"), detail)
        self.assertNotIn("daemon_response_ambiguous", message)
        self.assertNotIn("stale_client_credential", message)
        self.assertNotIn("reaccreditation", message)
        awaits = [item for item in seen if item["path"] == "/await"]
        self.assertGreaterEqual(len(awaits), 2, detail)
        self.assertIn("wait_ms", awaits[0]["query"])
        self.assertNotIn("wait_ms", awaits[1]["query"], detail)
        self.assertEqual(cleanups, [(7, "tool_timeout")], detail)
        self.assertTrue(any(item["path"] == "/abandon" for item in seen), detail)

    @slow_test
    async def test_credential_401_retry_drops_stale_wait_ms_and_cleans_up(
        self,
    ) -> None:
        message, seen, cleanups = await self._credential_replay("401")
        self._assert_replay_is_a_timeout(message, seen, cleanups)

    @slow_test
    async def test_reaccreditation_replay_drops_stale_wait_ms_and_cleans_up(
        self,
    ) -> None:
        message, seen, cleanups = await self._credential_replay("reaccreditation")
        self._assert_replay_is_a_timeout(message, seen, cleanups)

    @slow_test
    async def test_eight_long_polls_do_not_block_the_ninth_result(self) -> None:
        # Sol r4 F-08: the default executor is fixed at 8 threads here, so the
        # result does not depend on the host CPU count. Eight long-polls fill
        # that executor on 3bfd5fc and the ninth, whose result is already
        # stored, does not return inside its 400ms budget.
        import concurrent.futures

        from dayz_mcp import accredited_daemon_transport as transport

        daemon = _Daemon()
        closed = {"done": False}

        def close_once() -> None:
            if closed["done"]:
                return
            closed["done"] = True
            daemon.close()

        self.addCleanup(close_once)
        _host, port = daemon.httpd.server_address
        ids = [daemon.enqueue() for _ in range(9)]
        started = {"n": 0}
        started_lock = threading.Lock()

        def request_with_refresh(
            *,
            method,
            path,
            query,
            body,
            headers,
            deadline,
            **_kwargs,
        ):
            if path == "/await":
                with started_lock:
                    started["n"] += 1
            return transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=int(port),
                key=daemon.key,
                method=method,
                path=path,
                query=dict(query),
                body=body,
                headers=dict(headers),
                deadline=float(deadline),
                expected_executable="dayz-mcp-test",
                expected_argv=["dayz-mcp-test"],
                expected_cwd="C:\\dayz-mcp-test",
            )

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._allow_stale_policy = False
        runtime._credential_provider = SimpleNamespace(
            request_with_refresh=request_with_refresh
        )
        runtime._ensure_daemon = lambda _deadline: False

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        loop = asyncio.get_running_loop()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        loop.set_default_executor(pool)
        self.addCleanup(pool.shutdown, wait=False, cancel_futures=True)

        async def await_id(command_id: int, timeout_s: float) -> dict:
            return await runtime._await_result(
                "camera_get", command_id, "client", timeout_s
            )

        def verify(*_args, **_kwargs) -> bool:
            return True

        blockers: list[asyncio.Task] = []
        try:
            with mock.patch.object(
                transport, "_connected_daemon_identity_verified", verify
            ):
                blockers = [
                    asyncio.create_task(await_id(command_id, 3.0))
                    for command_id in ids[:8]
                ]
                limit = time.monotonic() + 2.0
                while time.monotonic() < limit and started["n"] < 8:
                    await asyncio.sleep(0.02)
                self.assertGreaterEqual(started["n"], 8, started["n"])
                await asyncio.sleep(0.05)
                code, stored = daemon.state.store_result(
                    {"id": ids[8], "ok": 1, "marker": 9}
                )
                self.assertEqual(code, 200, stored)
                started_at = time.monotonic()
                result = await asyncio.wait_for(
                    await_id(ids[8], 0.4), timeout=0.5
                )
            # y belongs to surface_query, so camera_get's prune drops it.
            self.assertEqual(result.get("marker"), 9, result)
            self.assertLess(time.monotonic() - started_at, 0.4)
        finally:
            close_once()
            for task in blockers:
                task.cancel()
            if blockers:
                await asyncio.gather(*blockers, return_exceptions=True)

    @slow_test
    async def test_raised_long_poll_releases_its_slot(self) -> None:
        # Eight long-polls sit on an 8-thread default executor. A ninth call
        # must still start at once. After one of those waits raises, a later
        # call must be able to send wait_ms again.
        import concurrent.futures

        seen: list[dict[str, str]] = []
        seen_lock = threading.Lock()
        hold = threading.Event()
        boom = threading.Event()
        self.addCleanup(hold.set)
        self.addCleanup(boom.set)

        def _call(method, path, payload=None, query=None, *_args):
            del method, path, payload
            copied = dict(query or {})
            with seen_lock:
                seen.append(copied)
            command_id = copied.get("id")
            if command_id == "9":
                raise RuntimeError("boom")
            if "wait_ms" not in copied:
                return 200, {"status": "done", "result": {"ok": 1, "y": 1}}
            if command_id == "10":
                return 200, {"status": "done", "result": {"ok": 1, "y": 10}}
            if command_id == "1":
                if not boom.wait(timeout=3.0):
                    raise AssertionError("id 1 was not signalled")
                raise RuntimeError("slot")
            hold.wait(timeout=3.0)
            return 200, {"status": "pending"}

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._call = _call

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        loop = asyncio.get_running_loop()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        loop.set_default_executor(pool)
        self.addCleanup(pool.shutdown, wait=False, cancel_futures=True)
        blockers: list[asyncio.Task] = []
        try:
            blockers = [
                asyncio.create_task(
                    runtime._await_result("camera_get", command_id, "client", 3.0)
                )
                for command_id in range(1, 9)
            ]
            limit = time.monotonic() + 2.0
            while time.monotonic() < limit:
                with seen_lock:
                    entered = sum(1 for query in seen if "wait_ms" in query)
                if entered >= 8:
                    break
                await asyncio.sleep(0.02)
            self.assertGreaterEqual(entered, 8, seen)
            with self.assertRaises(RuntimeError) as caught:
                await asyncio.wait_for(
                    runtime._await_result("camera_get", 9, "client", 3.0),
                    timeout=0.3,
                )
            self.assertEqual(str(caught.exception), "boom")
            boom.set()
            with self.assertRaises(RuntimeError) as slot_error:
                await asyncio.wait_for(blockers[0], timeout=1.0)
            self.assertEqual(str(slot_error.exception), "slot")
            await runtime._await_result("camera_get", 10, "client", 3.0)
            with seen_lock:
                follower = [query for query in seen if query.get("id") == "10"]
            self.assertTrue(follower, seen)
            self.assertIn("wait_ms", follower[0], follower)
        finally:
            hold.set()
            boom.set()
            for task in blockers:
                task.cancel()
            if blockers:
                await asyncio.gather(*blockers, return_exceptions=True)

    @slow_test
    async def test_accreditation_longer_than_the_margin_stays_timeout_waiting(
        self,
    ) -> None:
        # Sol r5 F-03: budget 1.5s, accreditation 1.2s on the first /await.
        # HEAD sends wait_ms around 497, the socket dies after accreditation,
        # and the call ends as daemon_response_ambiguous with no tool_timeout.
        from dayz_mcp import accredited_daemon_transport as transport
        from dayz_mcp.session_coordination import ClientIdentity

        daemon = _Daemon()
        self.addCleanup(daemon.close)
        _host, port = daemon.httpd.server_address
        seen: list[dict[str, object]] = []
        cleanups: list[tuple[object, object]] = []

        def slow_verify(*_args, **_kwargs) -> bool:
            time.sleep(1.2)
            return True

        def request_with_refresh(
            *,
            method,
            path,
            query,
            body,
            headers,
            deadline,
            **_kwargs,
        ):
            copied = dict(query or {})
            if path == "/enqueue":
                seen.append({"path": path, "query": copied})
                return 200, b'{"id": 7}'
            if path == "/abandon":
                seen.append({"path": path, "query": copied})
                return 200, b'{"status":"abandoned"}'
            if path != "/await":
                seen.append({"path": path, "query": copied})
                return 200, b"{}"
            seen.append({"path": path, "query": copied})
            return transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=int(port),
                key=daemon.key,
                method=method,
                path=path,
                query=copied,
                body=body,
                headers=dict(headers or {}),
                deadline=float(deadline),
                expected_executable="dayz-mcp-test",
                expected_argv=["dayz-mcp-test"],
                expected_cwd="C:\\dayz-mcp-test",
            )

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._allow_stale_policy = False
        runtime._control = SimpleNamespace(
            active_lease_token=None, active_ticket=None
        )
        runtime.identity = ClientIdentity.from_payload(_HOLDER["identity"])
        runtime._credential_provider = SimpleNamespace(
            request_with_refresh=request_with_refresh
        )
        runtime._ensure_daemon = lambda _deadline: False

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        async def no_status(*_args, **_kwargs):
            return None

        runtime._liveness_message = liveness
        runtime.bridge_status_payload = no_status
        real_cleanup = server._shielded_broker_cleanup

        async def spy_cleanup(bound, command_id, reason, budget_deadline=None):
            cleanups.append((command_id, reason))
            return await real_cleanup(
                bound, command_id, reason, budget_deadline
            )

        with mock.patch.object(server, "_shielded_broker_cleanup", spy_cleanup):
            with mock.patch.object(
                transport, "_connected_daemon_identity_verified", slow_verify
            ):
                with self.assertRaises(server.ToolError) as caught:
                    await runtime.call_bridge("camera_get", {}, "client", 1.5)
        message = str(caught.exception)
        detail = (message, seen, cleanups)
        self.assertTrue(message.startswith("timeout waiting"), detail)
        self.assertNotIn("daemon_response_ambiguous", message)
        awaits = [item for item in seen if item["path"] == "/await"]
        self.assertTrue(awaits, detail)
        self.assertIn("wait_ms", awaits[0]["query"], detail)
        self.assertEqual(cleanups, [(7, "tool_timeout")], detail)
        self.assertTrue(any(item["path"] == "/abandon" for item in seen), detail)

    @slow_test
    async def test_cancelled_long_polls_do_not_block_the_next_result(self) -> None:
        # Sol r5 F-09: cancelling the coroutine frees the slot while the
        # private-pool thread is still in HTTP. The next call, budget 1.4s,
        # takes that slot, queues, and never issues the query.
        import concurrent.futures

        from dayz_mcp import accredited_daemon_transport as transport

        daemon = _Daemon()
        closed = {"done": False}

        def close_once() -> None:
            if closed["done"]:
                return
            closed["done"] = True
            daemon.close()

        self.addCleanup(close_once)
        _host, port = daemon.httpd.server_address
        ids = [daemon.enqueue() for _ in range(9)]
        started = {"n": 0}
        started_lock = threading.Lock()

        def request_with_refresh(
            *,
            method,
            path,
            query,
            body,
            headers,
            deadline,
            **_kwargs,
        ):
            if path == "/await":
                with started_lock:
                    started["n"] += 1
            return transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=int(port),
                key=daemon.key,
                method=method,
                path=path,
                query=dict(query),
                body=body,
                headers=dict(headers),
                deadline=float(deadline),
                expected_executable="dayz-mcp-test",
                expected_argv=["dayz-mcp-test"],
                expected_cwd="C:\\dayz-mcp-test",
            )

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._allow_stale_policy = False
        runtime._credential_provider = SimpleNamespace(
            request_with_refresh=request_with_refresh
        )
        runtime._ensure_daemon = lambda _deadline: False

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        loop = asyncio.get_running_loop()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        loop.set_default_executor(pool)
        self.addCleanup(pool.shutdown, wait=False, cancel_futures=True)

        async def await_id(command_id: int, timeout_s: float) -> dict:
            return await runtime._await_result(
                "camera_get", command_id, "client", timeout_s
            )

        def verify(*_args, **_kwargs) -> bool:
            return True

        blockers: list[asyncio.Task] = []
        try:
            with mock.patch.object(
                transport, "_connected_daemon_identity_verified", verify
            ):
                blockers = [
                    asyncio.create_task(await_id(command_id, 3.0))
                    for command_id in ids[:8]
                ]
                limit = time.monotonic() + 2.0
                while time.monotonic() < limit and started["n"] < 8:
                    await asyncio.sleep(0.02)
                self.assertGreaterEqual(started["n"], 8, started["n"])
                for task in blockers:
                    task.cancel()
                # The coroutines must finish (and, on 80940d3, free the slot)
                # while the private-pool threads are still blocked in HTTP.
                await asyncio.gather(*blockers, return_exceptions=True)
                code, stored = daemon.state.store_result(
                    {"id": ids[8], "ok": 1, "marker": 9}
                )
                self.assertEqual(code, 200, stored)
                started_at = time.monotonic()
                result = await asyncio.wait_for(
                    await_id(ids[8], 1.4), timeout=1.6
                )
            self.assertEqual(result.get("marker"), 9, result)
            self.assertLess(time.monotonic() - started_at, 1.4)
        finally:
            close_once()
            for task in blockers:
                task.cancel()
            if blockers:
                await asyncio.gather(*blockers, return_exceptions=True)

    @slow_test
    async def test_cancelling_queued_work_releases_the_slot(self) -> None:
        # Sol r6 F-11: one cancelled hop still leaves seven slots, so a later
        # wait_ms does not prove that hop gave its slot back. Eight queued
        # hops take every slot. Cancelling them before they start must restore
        # the semaphore to its initial value, and eight later calls must all
        # send wait_ms. The cancelled hops must not send a query.
        seen: list[dict[str, str]] = []
        seen_lock = threading.Lock()
        hold = threading.Event()
        running = {"n": 0}
        self.addCleanup(hold.set)

        def block() -> None:
            with seen_lock:
                running["n"] += 1
            hold.wait(timeout=3.0)

        executor = server._client_long_poll_executor_handle()
        _executor, slots = server._client_long_poll_resources()
        self.assertEqual(slots._value, slots._initial_value, slots)
        parked = [executor.submit(block) for _ in range(8)]
        limit = time.monotonic() + 2.0
        while time.monotonic() < limit and running["n"] < 8:
            await asyncio.sleep(0.02)
        self.assertGreaterEqual(running["n"], 8, running["n"])

        def _call(method, path, payload=None, query=None, *_args):
            del method, path, payload
            copied = dict(query or {})
            with seen_lock:
                seen.append(copied)
            command_id = int(copied.get("id", "0"))
            if 9 <= command_id <= 16:
                return 200, {
                    "status": "done",
                    "result": {"ok": 1, "marker": command_id},
                }
            hold.wait(timeout=3.0)
            return 200, {"status": "pending"}

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._call = _call

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        queued = [
            asyncio.create_task(
                runtime._await_result("camera_get", command_id, "client", 3.0)
            )
            for command_id in range(1, 9)
        ]
        limit = time.monotonic() + 2.0
        while time.monotonic() < limit and slots._value != 0:
            await asyncio.sleep(0.02)
        self.assertEqual(slots._value, 0, slots)
        with seen_lock:
            self.assertEqual(seen, [], seen)
        for task in queued:
            task.cancel()
        await asyncio.gather(*queued, return_exceptions=True)
        self.assertEqual(slots._value, slots._initial_value, slots)
        hold.set()
        for future in parked:
            future.result(timeout=2.0)
        followers = [
            asyncio.create_task(
                runtime._await_result("camera_get", command_id, "client", 3.0)
            )
            for command_id in range(9, 17)
        ]
        await asyncio.gather(*followers)
        with seen_lock:
            by_id: dict[str, list[dict[str, str]]] = {}
            for query in seen:
                by_id.setdefault(str(query.get("id")), []).append(query)
        cancelled = [str(command_id) for command_id in range(1, 9) if str(command_id) in by_id]
        self.assertEqual(cancelled, [], seen)
        for command_id in range(9, 17):
            queries = by_id.get(str(command_id), [])
            self.assertTrue(queries, seen)
            self.assertIn("wait_ms", queries[0], queries)
        self.assertEqual(slots._value, slots._initial_value, slots)

    @slow_test
    async def test_submit_failure_returns_the_slot_and_drops_the_queued_hop(
        self,
    ) -> None:
        # Sol r6 F-10: submit queues the hop and then raises (the private
        # thread fails to start). The slot must come back, that queued hop
        # must not send /await, and the call must still deliver the result
        # on the shared executor without wait_ms.
        import concurrent.futures

        seen: list[dict[str, str]] = []
        seen_lock = threading.Lock()
        hold = threading.Event()
        running = {"n": 0}
        submits = {"n": 0}
        self.addCleanup(hold.set)

        def block() -> None:
            with seen_lock:
                running["n"] += 1
            hold.wait(timeout=3.0)

        executor = server._client_long_poll_executor_handle()
        _executor, slots = server._client_long_poll_resources()
        self.assertEqual(slots._value, slots._initial_value, slots)
        before = slots._value
        parked = [executor.submit(block) for _ in range(8)]
        limit = time.monotonic() + 2.0
        while time.monotonic() < limit and running["n"] < 8:
            await asyncio.sleep(0.02)
        self.assertGreaterEqual(running["n"], 8, running["n"])

        loop = asyncio.get_running_loop()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        warmed = [pool.submit(lambda: None) for _ in range(8)]
        for item in warmed:
            item.result(timeout=2.0)
        loop.set_default_executor(pool)
        self.addCleanup(pool.shutdown, wait=False, cancel_futures=True)

        def _call(method, path, payload=None, query=None, *_args):
            del method, path, payload
            copied = dict(query or {})
            with seen_lock:
                seen.append(copied)
            if copied.get("id") == "1":
                return 200, {"status": "done", "result": {"ok": 1, "marker": 1}}
            hold.wait(timeout=3.0)
            return 200, {"status": "pending"}

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._call = _call

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        original_submit = executor.submit

        def broken_submit(fn, /, *args, **kwargs):
            submits["n"] += 1
            original_submit(fn, *args, **kwargs)
            raise RuntimeError("private thread failed to start")

        executor.submit = broken_submit
        try:
            result = await runtime._await_result("camera_get", 1, "client", 3.0)
            self.assertEqual(submits["n"], 1, submits["n"])
            self.assertEqual(result.get("marker"), 1, result)
            self.assertEqual(slots._value, before, slots)
        finally:
            executor.submit = original_submit
            hold.set()
            for future in parked:
                future.result(timeout=2.0)
            executor.submit(lambda: None).result(timeout=2.0)
        with seen_lock:
            detail = list(seen)
        self.assertEqual(len(detail), 1, detail)
        self.assertEqual(detail[0].get("id"), "1", detail)
        self.assertNotIn("wait_ms", detail[0], detail)
        self.assertEqual(slots._value, before, slots)

    def _park_private_pool(self) -> tuple[object, list, threading.Event, dict[str, int]]:
        hold = threading.Event()
        running = {"n": 0}
        seen_lock = threading.Lock()
        self.addCleanup(hold.set)

        def block() -> None:
            with seen_lock:
                running["n"] += 1
            hold.wait(timeout=3.0)

        executor = server._client_long_poll_executor_handle()
        parked = [executor.submit(block) for _ in range(8)]
        return executor, parked, hold, running

    async def _wait_until_parked(self, running: dict[str, int], lock: threading.Lock) -> None:
        limit = time.monotonic() + 2.0
        while time.monotonic() < limit:
            with lock:
                count = running["n"]
            if count >= 8:
                return
            await asyncio.sleep(0.02)
        self.fail(running["n"])

    @slow_test
    async def test_submit_failure_pending_fallback_does_not_let_the_orphan_consume(
        self,
    ) -> None:
        # Sol r7 F-10: submit queues the hop and raises. The fallback returns
        # pending, the next iteration rebinds the abandon flag, and the orphan
        # then consumes the result with remove=1. Publishing at 150ms must
        # still reach this call, and only one /await may take it.
        import concurrent.futures

        seen: list[dict[str, str]] = []
        seen_lock = threading.Lock()
        box = {"ready": False, "taken": 0}
        submits = {"n": 0}
        executor, parked, hold, running = self._park_private_pool()
        await self._wait_until_parked(running, seen_lock)

        loop = asyncio.get_running_loop()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        warmed = [pool.submit(lambda: None) for _ in range(8)]
        for item in warmed:
            item.result(timeout=2.0)
        loop.set_default_executor(pool)
        self.addCleanup(pool.shutdown, wait=False, cancel_futures=True)

        def publish() -> None:
            time.sleep(0.15)
            with seen_lock:
                box["ready"] = True

        threading.Thread(target=publish, daemon=True).start()

        def release_after_rebind() -> None:
            limit = time.monotonic() + 2.0
            while time.monotonic() < limit:
                with seen_lock:
                    rebound = submits["n"] >= 2 and box["ready"]
                if rebound:
                    time.sleep(0.02)
                    hold.set()
                    return
                time.sleep(0.01)
            hold.set()

        threading.Thread(target=release_after_rebind, daemon=True).start()

        def _call(method, path, payload=None, query=None, *_args):
            del method, path, payload
            copied = dict(query or {})
            with seen_lock:
                seen.append(copied)
                if (
                    copied.get("id") == "1"
                    and box["ready"]
                    and box["taken"] == 0
                ):
                    box["taken"] = 1
                    return 200, {
                        "status": "done",
                        "result": {"ok": 1, "marker": 1},
                    }
            return 200, {"status": "pending"}

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime._broker_admits = {}
        runtime._call = _call

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        original_submit = executor.submit

        def broken_submit(fn, /, *args, **kwargs):
            future = original_submit(fn, *args, **kwargs)
            with seen_lock:
                submits["n"] += 1
                first = submits["n"] == 1
            if first:
                raise RuntimeError("private thread failed to start")
            return future

        executor.submit = broken_submit
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                runtime._await_result("camera_get", 1, "client", 1.5),
                timeout=3.0,
            )
        finally:
            executor.submit = original_submit
            hold.set()
            for future in parked:
                future.result(timeout=2.0)
        self.assertEqual(result.get("marker"), 1, result)
        self.assertLess(time.monotonic() - started, 1.5)
        with seen_lock:
            detail = list(seen)
            taken = box["taken"]
        waited = [query for query in detail if "wait_ms" in query]
        self.assertEqual(taken, 1, detail)
        self.assertEqual(len(waited), 1, detail)

    @slow_test
    async def test_submit_failure_last_second_does_not_stick_the_orphan(
        self,
    ) -> None:
        # Same failed submit and pending fallback, but the next iteration is
        # already inside the last second, so it does not long-poll. The
        # orphan must finish without sending, and no private-pool thread may
        # stay blocked on an event after the call returns.
        import sys

        seen: list[dict[str, str]] = []
        seen_lock = threading.Lock()
        clock = {"t": 10.0}
        executor, parked, hold, running = self._park_private_pool()
        await self._wait_until_parked(running, seen_lock)

        def _call(method, path, payload=None, query=None, *_args):
            del method, path, payload
            copied = dict(query or {})
            with seen_lock:
                seen.append(dict(copied))
                first = len(seen) == 1
            if first:
                # 1.2s budget, 0.4s left: the next loop is not a long-poll.
                clock["t"] = 10.8
                return 200, {"status": "pending"}
            if "wait_ms" in copied:
                return 200, {"status": "pending"}
            return 200, {"status": "done", "result": {"ok": 1, "marker": 1}}

        runtime = object.__new__(server.ClientRuntime)
        runtime._time_fn = lambda: clock["t"]
        runtime._broker_admits = {}
        runtime._call = _call

        async def liveness(_peer: str) -> str:
            return "status unavailable"

        runtime._liveness_message = liveness
        original_submit = executor.submit

        def broken_submit(fn, /, *args, **kwargs):
            original_submit(fn, *args, **kwargs)
            raise RuntimeError("private thread failed to start")

        executor.submit = broken_submit
        stuck: list[threading.Event] = []
        try:
            result = await asyncio.wait_for(
                runtime._await_result("camera_get", 1, "client", 1.2),
                timeout=2.0,
            )
            self.assertEqual(result.get("marker"), 1, result)
        finally:
            executor.submit = original_submit
            hold.set()
            for future in parked:
                future.result(timeout=2.0)
            await asyncio.sleep(0.15)
            frames = sys._current_frames()
            for thread in threading.enumerate():
                if not thread.name.startswith("await-long-poll"):
                    continue
                frame = frames.get(thread.ident)
                while frame is not None:
                    if frame.f_code.co_name == "wait":
                        candidate = frame.f_locals.get("self")
                        if isinstance(candidate, threading.Event):
                            stuck.append(candidate)
                            break
                    frame = frame.f_back
            for event in stuck:
                event.set()
        self.assertEqual(stuck, [], stuck)
        with seen_lock:
            detail = list(seen)
        self.assertTrue(detail, detail)
        self.assertFalse(any("wait_ms" in query for query in detail), detail)


class EmbeddedWaitTest(unittest.IsolatedAsyncioTestCase):
    def _runtime(self, state: loopback.ServerState):
        runtime = object.__new__(server.Runtime)
        runtime.loopback = SimpleNamespace(state=state)
        return runtime

    @slow_test
    async def test_embedded_wait_returns_when_the_result_is_stored(self) -> None:
        state = loopback.ServerState("embedded-key")
        status, payload = state.enqueue_command(
            "camera_get", {}, peer="client", operation_timeout_s=30
        )
        self.assertEqual(status, 200, payload)
        command_id = int(payload["id"])
        runtime = self._runtime(state)

        async def publish() -> None:
            await asyncio.sleep(0.1)
            code, stored = state.store_result({"id": command_id, "ok": 1})
            self.assertEqual(code, 200, stored)
            self.assertFalse(stored.get("discarded", False), stored)

        task = asyncio.create_task(publish())
        started = time.monotonic()
        result = await runtime.wait_for_result("camera_get", command_id, "client", 5.0)
        elapsed = time.monotonic() - started
        await task
        self.assertEqual(result["ok"], 1)
        self.assertGreater(elapsed, 0.05)
        self.assertLess(elapsed, 0.8)

    @slow_test
    async def test_embedded_cancel_abandons_without_consuming(self) -> None:
        state = loopback.ServerState("embedded-key")
        status, payload = state.enqueue_command(
            "camera_get", {}, peer="client", operation_timeout_s=30
        )
        self.assertEqual(status, 200, payload)
        command_id = int(payload["id"])
        runtime = self._runtime(state)
        task = asyncio.create_task(
            runtime.wait_for_result("camera_get", command_id, "client", 5.0)
        )
        await asyncio.sleep(0.05)
        started = time.monotonic()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertLess(time.monotonic() - started, 1.0)
        queued = [command.get("id") for command in state._legacy_queues["client"]]
        self.assertNotIn(command_id, queued)
        self.assertIsNone(state.take_result(command_id))

    @slow_test
    async def test_result_arrives_while_the_default_executor_is_full(self) -> None:
        hold = threading.Event()
        entered: list[int] = []
        entered_lock = threading.Lock()

        def block() -> None:
            with entered_lock:
                entered.append(1)
            hold.wait(timeout=3.0)

        blockers = [
            asyncio.create_task(asyncio.to_thread(block)) for _ in range(40)
        ]
        last = -1
        stable = 0.0
        fill_deadline = time.monotonic() + 2.0
        filled = False
        while time.monotonic() < fill_deadline:
            await asyncio.sleep(0.02)
            with entered_lock:
                count = len(entered)
            if count > 0 and count == last:
                stable += 0.02
                if stable >= 0.15:
                    filled = True
                    break
            else:
                stable = 0.0
                last = count
        if not filled:
            hold.set()
            await asyncio.gather(*blockers, return_exceptions=True)
            self.fail(f"default executor did not fill; entered={len(entered)}")

        state = loopback.ServerState("embedded-key")
        status, payload = state.enqueue_command(
            "camera_get", {}, peer="client", operation_timeout_s=30
        )
        self.assertEqual(status, 200, payload)
        command_id = int(payload["id"])
        runtime = self._runtime(state)
        started = time.monotonic()
        task = asyncio.create_task(
            runtime.wait_for_result("camera_get", command_id, "client", 0.4)
        )
        await asyncio.sleep(0.05)
        code, stored = state.store_result({"id": command_id, "ok": 1})
        self.assertEqual(code, 200, stored)
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=0.35)
        except TimeoutError:
            self.fail(
                "embedded wait did not deliver a result published at 50ms "
                f"before its 400ms timeout (elapsed={time.monotonic() - started:.3f})"
            )
        finally:
            hold.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, *blockers, return_exceptions=True)
        self.assertEqual(result["ok"], 1)
        self.assertLess(time.monotonic() - started, 0.4)


class BenchTest(unittest.TestCase):
    def _load(self):
        path = Path(__file__).resolve().parents[1] / "checks" / "await_long_poll_bench.py"
        spec = importlib.util.spec_from_file_location("await_long_poll_bench", path)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @slow_test
    def test_deferred_result_drops_from_about_twenty_requests_to_one_or_two(self) -> None:
        module = self._load()
        report = asyncio.run(module.run_bench(delay_s=1.0, samples=7))
        print("BENCH " + json.dumps(report), flush=True)
        before = report["before"]["request_counts"]
        after = report["after"]["request_counts"]
        self.assertTrue(all(count >= 8 for count in before), before)
        self.assertTrue(all(count <= 2 for count in after), after)

    @slow_test
    def test_thirty_three_samples_do_not_fill_the_command_queue(self) -> None:
        module = self._load()
        report = asyncio.run(module.run_bench(delay_s=0.02, samples=33))
        self.assertEqual(report["samples"], 33)
        self.assertEqual(len(report["before"]["request_counts"]), 33)
        self.assertEqual(len(report["after"]["request_counts"]), 33)


if __name__ == "__main__":
    unittest.main()
