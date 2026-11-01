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
            waited = await runtime._await_result("surface_query", 7, "client", 1.0)
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
            waited = await runtime._await_result("surface_query", 8, "client", 1.0)
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
            waited = await runtime._await_result("surface_query", 4, "client", 1.0)
        self.assertEqual(waited["y"], 4)
        self.assertEqual(slept, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[1][0], "GET")
        _query_ok(self, calls[0][3], "4")
        _query_ok(self, calls[1][3], "4")


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


class BenchTest(unittest.TestCase):
    @slow_test
    def test_deferred_result_drops_from_about_twenty_requests_to_one_or_two(self) -> None:
        path = Path(__file__).resolve().parents[1] / "checks" / "await_long_poll_bench.py"
        spec = importlib.util.spec_from_file_location("await_long_poll_bench", path)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = asyncio.run(module.run_bench(delay_s=1.0, samples=7))
        print("BENCH " + json.dumps(report), flush=True)
        before = report["before"]["request_counts"]
        after = report["after"]["request_counts"]
        self.assertTrue(all(count >= 8 for count in before), before)
        self.assertTrue(all(count <= 2 for count in after), after)


if __name__ == "__main__":
    unittest.main()
