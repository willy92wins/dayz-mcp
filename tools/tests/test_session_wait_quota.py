"""Duplicate POST /session/wait must not exhaust the HTTP worker pool.

One blocking wait per ticket is the pool's budget (MAX_SESSION_QUEUE).
Enough duplicates on one ticket used to leave POST /result with no worker.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
import urllib.parse
import urllib.request

from dayz_mcp import loopback
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests._tiers import slow_test


class _Daemon:
    def __init__(self) -> None:
        self.key = "session-wait-quota-key"
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


class SessionWaitQuotaTest(unittest.TestCase):
    @slow_test
    def test_duplicate_session_waits_leave_a_worker_for_result(self) -> None:
        # Names that already exist on main. The quota constant is new, so
        # this module must import without it: on the old code the failure
        # is /result with no worker, not ImportError.
        # Enough duplicate waits to fill the pool by themselves. With the
        # quota, only one blocks, so the /await quota and one /poll still
        # fit and /result keeps a worker. Without it, /result is refused.
        await_n = loopback.AWAIT_LONG_POLL_MAX_IN_FLIGHT
        session_n = loopback.MAX_HTTP_WORKERS
        self.assertGreater(session_n, loopback.MAX_SESSION_QUEUE)

        daemon = _Daemon()
        self.addCleanup(daemon.close)
        command_id = daemon.enqueue()
        coordinator = SessionCoordinator(
            token_fn=lambda: "quota-holder-token",
            id_fn=lambda: "quota-ticket-1",
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
        status, active = coordinator.acquire(holder, "hold the lease")
        self.assertEqual(status, 200, active)
        lease_token = active.get("lease_token")
        self.assertIsInstance(lease_token, str)
        waiter = ClientIdentity.from_payload(
            {
                "platform": "codex",
                "pid": 2,
                "ppid": 1,
                "started_at_utc": "2026-11-01T00:00:00Z",
                "session_id": "quota-waiter",
                "task_label": "waiter",
            }
        )
        queued_status, queued = coordinator.enqueue(waiter, "queued", "op-1")
        self.assertEqual(queued_status, 202, queued)
        ticket = queued.get("ticket")
        self.assertIsInstance(ticket, str)
        assert isinstance(ticket, str)
        identity = waiter.to_payload()

        held = self._arm_held_count(coordinator._condition)
        outcomes: list[tuple] = []
        outcome_lock = threading.Lock()
        wait_timeout_s = 3.0
        threads: list[threading.Thread] = []

        def session_wait() -> None:
            started = time.monotonic()
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
                with urllib.request.urlopen(request, timeout=wait_timeout_s + 2.0) as response:
                    row = (
                        int(response.status),
                        response.read(),
                        time.monotonic() - started,
                        None,
                    )
            except BaseException as exc:
                row = (0, b"", time.monotonic() - started, exc)
            with outcome_lock:
                outcomes.append(row)

        for index in range(session_n):
            thread = threading.Thread(target=session_wait)
            threads.append(thread)
            thread.start()
            # Accept backlog is 5. A burst gets RST and Windows retries
            # the SYN at 0.5, 1 and 2s. 5ms between launches keeps the
            # reply time on the daemon.
            if index + 1 < session_n:
                time.sleep(0.005)

        def _join() -> None:
            daemon.state.cancel_pending()
            try:
                coordinator.release(holder, lease_token)
            except Exception:
                pass
            for thread in threads:
                thread.join(timeout=wait_timeout_s + 2.0)

        self.addCleanup(_join)

        deadline = time.monotonic() + 8.0
        saturated = False
        admitted = False
        while time.monotonic() < deadline:
            with outcome_lock:
                refused = len(outcomes)
            if held["n"] >= session_n:
                saturated = True
                break
            if held["n"] == 1 and refused >= session_n - 1:
                admitted = True
                break
            time.sleep(0.01)
        self.assertTrue(
            admitted or saturated,
            f"waits did not settle (held={held['n']} refused={len(outcomes)})",
        )
        if admitted:
            # camera_get is still queued. Deliver it first, or the later
            # /poll returns at once and does not hold a worker.
            self._poll(daemon, wait_ms=None)
            await_threads = [
                threading.Thread(target=self._await_unknown, args=(daemon,))
                for _ in range(await_n)
            ]
            poll_thread = threading.Thread(
                target=self._poll, args=(daemon, "800")
            )
            for thread in await_threads:
                threads.append(thread)
                thread.start()
            threads.append(poll_thread)
            poll_thread.start()
            time.sleep(0.3)
            self.assertTrue(
                poll_thread.is_alive(),
                "/poll returned; the queue was not empty",
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
        except (OSError, http.client.HTTPException) as exc:
            # urllib does not wrap RemoteDisconnected or ConnectionAbortedError
            # (Windows 10053) from getresponse(). Both mean the pool refused
            # the socket. self.fail makes that an assertion, not an error.
            self.fail(f"/result had no worker: {exc}")
        self.assertEqual(result_status, 200, result_body)
        self.assertTrue(result_body.get("ok"), result_body)
        self.assertEqual(held["n"], 1)
        with outcome_lock:
            finished = list(outcomes)
        self.assertGreaterEqual(len(finished), session_n - 1)
        for status_code, raw, elapsed, exc in finished:
            self.assertIsNone(exc, exc)
            self.assertEqual(status_code, 202, raw)
            # Launches are staggered so this is the daemon's reply, not a
            # Windows SYN retry (those land at 0.5, 1 and 2s). timeout_s is 3.
            self.assertLess(elapsed, 0.5, raw)
            body = json.loads(raw.decode("utf-8"))
            self.assertEqual(body.get("status"), "queued")
            self.assertEqual(body.get("ticket"), ticket)
            self.assertEqual(body.get("session_wait_quota"), "duplicate")
            self.assertGreater(body.get("retry_after_s"), 0.0)

    def _poll(self, daemon: _Daemon, wait_ms: str | None) -> None:
        query = {"key": daemon.key, "peer": "client"}
        if wait_ms is not None:
            query["wait_ms"] = wait_ms
        url = daemon.base + "/poll?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5.0):
                return
        except Exception:
            return

    def _await_unknown(self, daemon: _Daemon) -> None:
        url = (
            daemon.base
            + "/await?"
            + urllib.parse.urlencode(
                {"key": daemon.key, "id": "999999", "wait_ms": "2000"}
            )
        )
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5.0):
                return
        except Exception:
            return

    @staticmethod
    def _arm_held_count(cv: threading.Condition) -> dict[str, int]:
        held = {"n": 0}
        real_release = cv._release_save
        real_acquire = cv._acquire_restore

        def release_save():
            saved = real_release()
            held["n"] += 1
            return saved

        def acquire_restore(state):
            try:
                return real_acquire(state)
            finally:
                held["n"] -= 1

        cv._release_save = release_save
        cv._acquire_restore = acquire_restore
        return held


def _queued_pair():
    """Holder owns the lease. One other client has a queued ticket."""
    ids = {"n": 0}

    def next_id() -> str:
        ids["n"] += 1
        return f"rel-{ids['n']}"

    coordinator = SessionCoordinator(
        token_fn=lambda: "rel-token",
        id_fn=next_id,
        audit=lambda _event: True,
        cleanup=lambda _session_id, _lease_id, _reason, _vehicle_active: {},
    )
    holder = ClientIdentity.from_payload(
        {
            "platform": "codex",
            "pid": 1,
            "ppid": 0,
            "started_at_utc": "2026-11-01T00:00:00Z",
            "session_id": "rel-holder",
            "task_label": "holder",
        }
    )
    status, active = coordinator.acquire(holder, "hold")
    if status != 200:
        raise AssertionError(active)
    waiter = ClientIdentity.from_payload(
        {
            "platform": "codex",
            "pid": 2,
            "ppid": 1,
            "started_at_utc": "2026-11-01T00:00:00Z",
            "session_id": "rel-waiter",
            "task_label": "waiter",
        }
    )
    queued_status, queued = coordinator.enqueue(waiter, "queued", "op-rel")
    if queued_status != 202:
        raise AssertionError(queued)
    return coordinator, holder, waiter, str(queued["ticket"]), active["lease_token"]


class SessionWaitQuotaReleaseTest(unittest.TestCase):
    """The blocking slot is released on every way out of wait().

    These call the coordinator directly, so they do not open the 96-socket
    pool. On code without the quota map they fail the assertion that the
    map exists, and they do not block: the blocking call is joined after
    release, or it is patched to raise.
    """

    def test_duplicate_does_not_bump_and_cancel_releases(self) -> None:
        coordinator, holder, waiter, ticket, lease_token = _queued_pair()
        outcome: dict[str, object] = {}

        def admitted() -> None:
            # Long on purpose. The duplicate must not be racing a 0.2s timeout.
            outcome["row"] = coordinator.wait(waiter, ticket, 30.0)

        thread = threading.Thread(target=admitted)
        thread.start()
        self.addCleanup(thread.join, 2.0)
        self.addCleanup(coordinator.release, holder, lease_token)
        slots = getattr(coordinator, "_blocking_session_waits", None)
        self.assertIsNotNone(slots, "blocking-wait quota map missing")
        assert slots is not None
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and ticket not in slots:
            time.sleep(0.01)
        self.assertIn(ticket, slots)
        revision = coordinator._revision
        started = time.monotonic()
        status, body = coordinator.wait(waiter, ticket, 1.0)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(status, 202, body)
        self.assertEqual(body.get("session_wait_quota"), "duplicate")
        self.assertGreater(body.get("retry_after_s"), 0.0)
        self.assertEqual(coordinator._revision, revision)
        cancel_status, cancel_body = coordinator.cancel(waiter, ticket)
        self.assertEqual(cancel_status, 200, cancel_body)
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(slots, {})
        row = outcome.get("row")
        self.assertIsInstance(row, tuple)
        assert isinstance(row, tuple)
        self.assertEqual(row[0], 410)

    @slow_test
    def test_timeout_releases_the_slot(self) -> None:
        coordinator, holder, waiter, ticket, lease_token = _queued_pair()
        self.addCleanup(coordinator.release, holder, lease_token)
        slots = getattr(coordinator, "_blocking_session_waits", None)
        self.assertIsNotNone(slots, "blocking-wait quota map missing")
        assert slots is not None
        status, body = coordinator.wait(waiter, ticket, 0.05)
        self.assertEqual(status, 202, body)
        self.assertNotIn("session_wait_quota", body)
        self.assertEqual(slots, {})
        outcome: dict[str, object] = {}

        def again() -> None:
            outcome["row"] = coordinator.wait(waiter, ticket, 2.0)

        thread = threading.Thread(target=again)
        thread.start()
        self.addCleanup(thread.join, 2.0)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and ticket not in slots:
            time.sleep(0.01)
        self.assertIn(ticket, slots)
        coordinator.release(holder, lease_token)
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(slots, {})
        row = outcome.get("row")
        self.assertIsInstance(row, tuple)
        assert isinstance(row, tuple)
        self.assertEqual(row[0], 200)

    def test_injected_wait_error_releases_the_slot(self) -> None:
        coordinator, holder, waiter, ticket, lease_token = _queued_pair()
        self.addCleanup(coordinator.release, holder, lease_token)
        real_wait = coordinator._condition.wait

        def boom(_wait_for: float = 0.0) -> bool:
            raise RuntimeError("injected")

        coordinator._condition.wait = boom  # type: ignore[method-assign]
        try:
            with self.assertRaises(RuntimeError):
                coordinator.wait(waiter, ticket, 1.0)
        finally:
            coordinator._condition.wait = real_wait  # type: ignore[method-assign]
        slots = getattr(coordinator, "_blocking_session_waits", None)
        self.assertIsNotNone(slots, "blocking-wait quota map missing")
        self.assertEqual(slots, {})

    def test_cancel_releases_the_slot(self) -> None:
        coordinator, holder, waiter, ticket, lease_token = _queued_pair()
        outcome: dict[str, object] = {}

        def admitted() -> None:
            outcome["row"] = coordinator.wait(waiter, ticket, 2.0)

        thread = threading.Thread(target=admitted)
        thread.start()
        self.addCleanup(thread.join, 2.0)
        self.addCleanup(coordinator.release, holder, lease_token)
        slots = getattr(coordinator, "_blocking_session_waits", None)
        self.assertIsNotNone(slots, "blocking-wait quota map missing")
        assert slots is not None
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and ticket not in slots:
            time.sleep(0.01)
        self.assertIn(ticket, slots)
        status, body = coordinator.cancel(waiter, ticket)
        self.assertEqual(status, 200, body)
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(slots, {})
        row = outcome.get("row")
        self.assertIsInstance(row, tuple)
        assert isinstance(row, tuple)
        self.assertEqual(row[0], 410)

    def test_full_quota_answers_at_once(self) -> None:
        coordinator, holder, waiter, ticket, lease_token = _queued_pair()
        self.addCleanup(coordinator.release, holder, lease_token)
        slots = getattr(coordinator, "_blocking_session_waits", None)
        self.assertIsNotNone(slots, "blocking-wait quota map missing")
        assert slots is not None
        deadline = time.monotonic() + 30.0
        for index in range(loopback.MAX_SESSION_QUEUE):
            slots[f"pad-{index}"] = deadline
        try:
            started = time.monotonic()
            status, body = coordinator.wait(waiter, ticket, 5.0)
            elapsed = time.monotonic() - started
        finally:
            slots.clear()
        self.assertEqual(status, 202, body)
        self.assertEqual(body.get("session_wait_quota"), "full")
        self.assertEqual(body.get("retry_after_s"), 0.0)
        self.assertLess(elapsed, 0.05)
        self.assertNotIn(ticket, slots)


if __name__ == "__main__":
    unittest.main()
