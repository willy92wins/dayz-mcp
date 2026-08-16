from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator


IDENTITY_A = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-14T10:00:00Z",
    "session_id": "A",
    "task_label": "test",
}
IDENTITY_B = {
    "platform": "claude",
    "pid": 22,
    "ppid": 2,
    "started_at_utc": "2026-07-14T10:00:01Z",
    "session_id": "B",
    "task_label": "reader",
}


def _http(base, method, path, key, payload=None, query=None, timeout=2.0):
    params = dict(query or {})
    if key is not None:
        params["key"] = key
    url = base + path + "?" + urllib.parse.urlencode(params)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), json.loads(exc.read().decode("utf-8") or "{}")
        finally:
            exc.close()


class SnapshotStore:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self.raise_on_write = False
        self.return_value = True
        self.lock_probe: SessionCoordinator | None = None

    def write_coordination(self, payload: dict[str, object]) -> bool:
        self.payloads.append(payload)
        if self.lock_probe is not None:
            acquired: list[bool] = []

            def probe() -> None:
                condition = self.lock_probe._condition  # type: ignore[attr-defined]
                locked = condition.acquire(timeout=0.5)
                acquired.append(locked)
                if locked:
                    condition.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(timeout=1.0)
            if acquired != [True]:
                raise RuntimeError("snapshot_ran_under_coordinator_condition")
        if self.raise_on_write:
            raise OSError("snapshot write failed")
        return self.return_value


class SessionHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = "session-key"
        self.audit_fails = False
        self.audit_events: list[dict[str, object]] = []
        self.store = SnapshotStore()
        self.state = loopback.ServerState(self.key)

        def audit(event: dict[str, object]) -> bool:
            self.audit_events.append(event)
            return not self.audit_fails

        self.coordinator = SessionCoordinator(
            audit=audit,
            cleanup=lambda session_id, lease_id, reason, vehicle_active: self.state.cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            ),
        )
        # Task 3 adds these as daemon-composition attributes. Assigning them here
        # keeps RED focused on the missing routes instead of constructor TypeError.
        self.state.coordination = self.coordinator  # type: ignore[attr-defined]
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        self.state.coordination_store = self.store  # type: ignore[attr-defined]
        self.state.daemon_generation = "generation-test"  # type: ignore[attr-defined]
        self.httpd = loopback.create_http_server(
            0, self.state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)

    def request(self, path: str, payload: dict, *, key: str | None = None) -> tuple[int, dict]:
        return _http(self.base, "POST", path, self.key if key is None else key, payload)

    def acquire(self, identity: dict = IDENTITY_A, purpose: str = "test") -> tuple[str, dict]:
        status, body = self.request(
            "/session/acquire", {"identity": identity, "purpose": purpose}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            set(body), {"status", "lease_token", "lease_id", "expires_in_s"}
        )
        self.assertEqual(body["status"], "active")
        return body["lease_token"], body

    def test_mutation_requires_lease_but_read_is_admitted(self) -> None:
        status, body = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_A,
                "cmd": "world_spawn",
                "args": {"type": "X", "pos": [1, 2, 3]},
                "peer": "server",
            },
        )
        self.assertEqual(status, 423)
        self.assertEqual(body["error"], "lease_required")

        status, body = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_A,
                "cmd": "query_player_state",
                "args": {},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"id"})

    def test_enqueue_requires_valid_identity_and_unknown_is_mutating_first(self) -> None:
        status, body = self.request(
            "/enqueue", {"cmd": "query_player_state", "args": {}, "peer": "server"}
        )
        self.assertEqual((status, body), (400, {"error": "invalid_identity"}))

        status, body = self.request(
            "/enqueue",
            {"identity": IDENTITY_A, "cmd": "brand_new_tool", "args": {}},
        )
        self.assertEqual((status, body["error"]), (423, "lease_required"))

    def test_unhashable_non_string_commands_are_safe_mutating_sentinels(self) -> None:
        malformed_commands = (
            ["raw-list-command-marker"],
            {"raw-object-command-marker": True},
        )
        for malformed in malformed_commands:
            with self.subTest(kind=type(malformed).__name__, lease=False):
                audit_start = len(self.audit_events)
                status, body = self.request(
                    "/enqueue",
                    {"identity": IDENTITY_A, "cmd": malformed, "args": {}},
                )
                self.assertEqual((status, body), (423, {"error": "lease_required"}))
                audit_wire = json.dumps(self.audit_events[audit_start:])
                self.assertNotIn("raw-list-command-marker", audit_wire)
                self.assertNotIn("raw-object-command-marker", audit_wire)

        token, _ = self.acquire()
        for malformed in malformed_commands:
            with self.subTest(kind=type(malformed).__name__, lease=True):
                audit_start = len(self.audit_events)
                status, body = self.request(
                    "/enqueue",
                    {
                        "identity": IDENTITY_A,
                        "lease_token": token,
                        "cmd": malformed,
                        "args": {},
                    },
                )
                self.assertEqual((status, body), (400, {"error": "not_whitelisted"}))
                active = self.coordinator._active  # type: ignore[attr-defined]
                self.assertIsNotNone(active)
                self.assertEqual(active.pending_authorizations, [])
                self.assertEqual(active.operation_pins, {})
                audit_wire = json.dumps(self.audit_events[audit_start:])
                self.assertNotIn("raw-list-command-marker", audit_wire)
                self.assertNotIn("raw-object-command-marker", audit_wire)
                self.assertTrue(
                    all(
                        event.get("command", "") == ""
                        for event in self.audit_events[audit_start:]
                    )
                )

    def test_total_enqueue_schema_is_lease_first_and_never_audits_raw_values(self) -> None:
        for peer in (["raw-peer-list"], {"raw-peer-object": True}):
            with self.subTest(field="peer", kind=type(peer).__name__, lease=False):
                status, body = self.request(
                    "/enqueue",
                    {
                        "identity": IDENTITY_A,
                        "cmd": "world_spawn",
                        "args": {"type": "X", "pos": [1, 2, 3]},
                        "peer": peer,
                    },
                )
                self.assertEqual((status, body), (423, {"error": "lease_required"}))

        token, _ = self.acquire()
        for peer in (["raw-peer-list"], {"raw-peer-object": True}):
            with self.subTest(field="peer", kind=type(peer).__name__, lease=True):
                status, body = self.request(
                    "/enqueue",
                    {
                        "identity": IDENTITY_A,
                        "lease_token": token,
                        "cmd": "world_spawn",
                        "args": {"type": "X", "pos": [1, 2, 3]},
                        "peer": peer,
                    },
                )
                self.assertEqual((status, body), (400, {"error": "bad_peer"}))
                self._assert_no_pending_reservation()

        for malformed_token in (
            ["raw-token-list"],
            {"raw-token-object": True},
        ):
            with self.subTest(field="lease_token", kind=type(malformed_token).__name__):
                audit_start = len(self.audit_events)
                status, body = self.request(
                    "/enqueue",
                    {
                        "identity": IDENTITY_A,
                        "lease_token": malformed_token,
                        "cmd": "world_spawn",
                        "args": {"type": "X", "pos": [1, 2, 3]},
                    },
                )
                self.assertEqual((status, body), (403, {"error": "lease_invalid"}))
                self._assert_no_pending_reservation()
                self.assertEqual(len(self.audit_events), audit_start)

        malformed_timeouts = (
            "raw-timeout-string",
            float("nan"),
            float("inf"),
            10**400,
        )
        for timeout in malformed_timeouts:
            with self.subTest(field="operation_timeout_s", value=repr(timeout), lease=False):
                status, body = self.request(
                    "/enqueue",
                    {
                        "identity": IDENTITY_A,
                        "cmd": "world_spawn",
                        "args": {"type": "X", "pos": [1, 2, 3]},
                        "operation_timeout_s": timeout,
                    },
                )
                self.assertEqual((status, body), (423, {"error": "lease_required"}))

            with self.subTest(field="operation_timeout_s", value=repr(timeout), lease=True):
                status, body = self.request(
                    "/enqueue",
                    {
                        "identity": IDENTITY_A,
                        "lease_token": token,
                        "cmd": "world_spawn",
                        "args": {"type": "X", "pos": [1, 2, 3]},
                        "operation_timeout_s": timeout,
                    },
                )
                self.assertEqual(
                    (status, body), (400, {"error": "bad_operation_timeout"})
                )
                self._assert_no_pending_reservation()

        audit_wire = json.dumps(self.audit_events, default=str)
        for marker in (
            "raw-peer-list",
            "raw-peer-object",
            "raw-token-list",
            "raw-token-object",
            "raw-timeout-string",
        ):
            self.assertNotIn(marker, audit_wire)

    def _assert_no_pending_reservation(self) -> None:
        active = self.coordinator._active  # type: ignore[attr-defined]
        self.assertIsNotNone(active)
        self.assertEqual(active.pending_authorizations, [])
        self.assertEqual(active.operation_pins, {})

    def test_stolen_token_is_rejected_before_queueing(self) -> None:
        token, _ = self.acquire()
        status, body = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_B,
                "lease_token": token,
                "cmd": "world_spawn",
                "args": {"type": "X", "pos": [1, 2, 3]},
                "peer": "server",
            },
        )
        self.assertEqual((status, body["error"]), (403, "lease_invalid"))
        self.assertEqual(self.state.status_snapshot()["peers"]["server"]["queue_depth"], 0)

    def test_wait_rejects_timeout_outside_http_contract(self) -> None:
        self.acquire()
        status, queued = self.request(
            "/session/acquire", {"identity": IDENTITY_B, "purpose": "next"}
        )
        self.assertEqual(status, 202)
        self.assertEqual(set(queued), {"status", "ticket", "position", "expires_in_s"})

        status, body = self.request(
            "/session/wait",
            {"identity": IDENTITY_B, "ticket": queued["ticket"], "timeout_s": 30.01},
        )
        self.assertEqual((status, body), (400, {"error": "bad_wait_timeout"}))

    def test_high_level_enqueue_always_queues_and_ticket_cancel_is_exact(self) -> None:
        status, queued = self.request(
            "/session/enqueue",
            {
                "identity": IDENTITY_A,
                "purpose": "queued-work",
                "operation_id": "operation-a",
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(queued["status"], "queued")
        self.assertNotIn("lease_token", queued)

        status, cancelled = self.request(
            "/session/cancel",
            {"identity": IDENTITY_A, "ticket": queued["ticket"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(cancelled["cancelled"])

        status, body = self.request(
            "/session/cancel",
            {"identity": IDENTITY_B, "ticket": queued["ticket"]},
        )
        self.assertEqual((status, body), (403, {"error": "ticket_invalid"}))

    def test_cancel_operation_before_enqueue_blocks_late_request(self) -> None:
        status, cancelled = self.request(
            "/session/cancel-operation",
            {"identity": IDENTITY_A, "operation_id": "late-operation"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(cancelled["cancelled"])

        status, body = self.request(
            "/session/enqueue",
            {
                "identity": IDENTITY_A,
                "purpose": "must-not-publish",
                "operation_id": "late-operation",
            },
        )
        self.assertEqual((status, body), (409, {"error": "operation_cancelled"}))

        status, snapshot = self.request(
            "/session/status", {"identity": IDENTITY_A}
        )
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["operation_tombstones"]["count"], 1)
        self.assertIsNone(snapshot["owner"])
        self.assertEqual(snapshot["queue"], [])

    def test_status_is_redacted_and_reports_owner_pending_count(self) -> None:
        token, active = self.acquire()
        status, enqueued = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_A,
                "lease_token": token,
                "cmd": "world_spawn",
                "args": {"type": "X", "pos": [1, 2, 3]},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)

        status, body = self.request("/session/status", {"identity": IDENTITY_A})
        self.assertEqual(status, 200)
        self.assertEqual(body["daemon_generation"], "generation-test")
        self.assertEqual(body["pending_commands"], 1)
        self.assertEqual(body["self"]["lease_id"], active["lease_id"])
        wire = json.dumps(body, separators=(",", ":"))
        self.assertNotIn(token, wire)
        self.assertNotIn('"pid"', wire)
        self.assertNotIn('"ppid"', wire)
        self.assertNotIn(str(enqueued["id"]) + ":" + token, wire)

    def test_owner_release_cancels_only_its_undelivered_commands(self) -> None:
        token, _ = self.acquire()
        status, owned = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_A,
                "lease_token": token,
                "cmd": "world_spawn",
                "args": {"type": "X", "pos": [1, 2, 3]},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)
        status, foreign_read = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_B,
                "cmd": "query_player_state",
                "args": {},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)

        status, released = self.request(
            "/session/release", {"identity": IDENTITY_A, "lease_token": token}
        )
        self.assertEqual(status, 200)
        self.assertTrue(released["released"])
        self.assertEqual(released["cleanup"]["cancelled"], 1)

        status, done = _http(
            self.base,
            "GET",
            "/await",
            self.key,
            query={"id": owned["id"], "remove": 1},
        )
        self.assertEqual(status, 200)
        self.assertEqual(done["result"]["error"], "owner_release")

        status, polled = _http(
            self.base, "GET", "/poll", self.key, query={"peer": "server"}
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in polled["commands"]], [foreign_read["id"]])

    def test_release_enqueues_internal_vehicle_release_without_wire_owner_metadata(self) -> None:
        token, _ = self.acquire()
        status, _ = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_A,
                "lease_token": token,
                "cmd": "vehicle_control",
                "args": {"throttle": 1.0},
                "peer": "client",
            },
        )
        self.assertEqual(status, 200)

        status, released = self.request(
            "/session/release", {"identity": IDENTITY_A, "lease_token": token}
        )
        self.assertEqual(status, 200)
        self.assertEqual(released["cleanup"]["vehicle_release_enqueued"], 1)

        status, polled = _http(
            self.base, "GET", "/poll", self.key, query={"peer": "client"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(polled["commands"]), 1)
        self.assertEqual(polled["commands"][0]["cmd"], "vehicle_release")
        self.assertEqual(
            set(polled["commands"][0]), {"id", "cmd", "args"}
        )

    def test_audit_failure_rejects_acquire_and_mutation_but_release_invalidates(self) -> None:
        self.audit_fails = True
        status, body = self.request(
            "/session/acquire", {"identity": IDENTITY_B, "purpose": "denied"}
        )
        self.assertEqual((status, body["error"]), (503, "audit_failed"))

        self.audit_fails = False
        token, _ = self.acquire()
        self.audit_fails = True
        status, body = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_A,
                "lease_token": token,
                "cmd": "world_spawn",
                "args": {"type": "X", "pos": [1, 2, 3]},
                "peer": "server",
            },
        )
        self.assertEqual((status, body["error"]), (503, "audit_failed"))

        status, released = self.request(
            "/session/release", {"identity": IDENTITY_A, "lease_token": token}
        )
        self.assertEqual(status, 200)
        self.assertTrue(released["released"])
        self.assertIn("audit_failed", released["cleanup_degraded"])

        status, body = self.request(
            "/session/heartbeat", {"identity": IDENTITY_A, "lease_token": token}
        )
        self.assertEqual((status, body["error"]), (403, "lease_invalid"))

    def test_operation_timeout_is_pinned_at_no_more_than_300_seconds(self) -> None:
        token, _ = self.acquire()
        status, _ = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_A,
                "lease_token": token,
                "cmd": "world_spawn",
                "args": {"type": "X", "pos": [1, 2, 3]},
                "peer": "server",
                "operation_timeout_s": 9999,
            },
        )
        self.assertEqual(status, 200)
        status, body = self.request("/session/status", {"identity": IDENTITY_A})
        self.assertEqual(status, 200)
        self.assertGreater(body["owner"]["expires_in_s"], 299.0)
        self.assertLessEqual(body["owner"]["expires_in_s"], 300.0)

    def test_snapshot_false_is_benign_and_io_runs_after_condition_unlock(self) -> None:
        self.store.return_value = False
        self.store.lock_probe = self.coordinator
        _, body = self.acquire()
        self.assertNotIn("cleanup_degraded", body)
        self.assertEqual(len(self.store.payloads), 1)

    def test_snapshot_exception_preserves_applied_acquire_and_enqueue(self) -> None:
        self.store.raise_on_write = True
        status, acquired = self.request(
            "/session/acquire", {"identity": IDENTITY_A, "purpose": "snapshot"}
        )
        self.assertEqual(status, 200)
        self.assertIn("lease_token", acquired)
        self.assertEqual(acquired["cleanup_degraded"], ["snapshot_failed"])

        status, enqueued = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_A,
                "lease_token": acquired["lease_token"],
                "cmd": "world_spawn",
                "args": {"type": "X", "pos": [1, 2, 3]},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("id", enqueued)
        self.assertEqual(enqueued["cleanup_degraded"], ["snapshot_failed"])
        self.assertEqual(len(self.store.payloads), 2)
        self.assertEqual(self.state.status_snapshot()["peers"]["server"]["queue_depth"], 1)

    def test_session_routes_require_api_key_and_count_as_client_activity(self) -> None:
        status, body = _http(
            self.base,
            "POST",
            "/session/status",
            None,
            {"identity": IDENTITY_A},
        )
        self.assertEqual((status, body), (401, {"error": "unauthorized"}))
        self.assertIsNone(self.state.status_snapshot()["last_client_request_at"])

        status, _ = self.request("/session/status", {"identity": IDENTITY_A})
        self.assertEqual(status, 200)
        self.assertIsNotNone(self.state.status_snapshot()["last_client_request_at"])


if __name__ == "__main__":
    unittest.main()
