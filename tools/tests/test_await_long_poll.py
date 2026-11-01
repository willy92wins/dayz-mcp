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
