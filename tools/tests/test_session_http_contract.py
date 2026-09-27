"""The session HTTP contract of the loopback daemon.

Moved verbatim from test_task7_review_regressions.py, test_session_http.py
(review 2026-09-25, W4d step 3).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback
from dayz_mcp.process_lifecycle import RunRecord
from dayz_mcp.runtime_state import CoordinationSnapshotStore, RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.fence_helpers import bind_both_peers, INST_CLIENT, INST_SERVER
from tests.lease_helpers import SnapshotStore
from tests.lifecycle_helpers import (
    IDENTITY,
    identity,
    IDENTITY_PAYLOAD,
    LifecycleFixture,
    record,
)


# --- helpers from test_task7_review_regressions.py ---
def http_post(base: str, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    url = base + path + "?" + urllib.parse.urlencode({"key": "key"})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        finally:
            exc.close()


# --- helpers from test_session_http.py ---
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


# --- from test_task7_review_regressions.py ---


class RealLifecycleHttpQuarantineTest(unittest.TestCase):
    def test_real_lifecycle_quarantine_blocks_mutations_but_status_and_release_work(self) -> None:
        fixture = LifecycleFixture()
        state = loopback.ServerState("key", coordination=fixture.coordinator)
        bind_both_peers(state)
        state.lifecycle = fixture.lifecycle
        state.retail_probe = lambda: {"known": False, "processes": []}
        fixture.probe = {"known": False, "processes": []}
        server = loopback.create_http_server(
            0, state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base = f"http://{host}:{port}"
        try:
            requests = (
                ("/lifecycle/start", {"identity": IDENTITY_PAYLOAD, "lease_token": fixture.token, "request": fixture.request()}),
                ("/lifecycle/stop", {"identity": IDENTITY_PAYLOAD, "lease_token": fixture.token, "run_id": "missing"}),
                ("/lifecycle/adopt", {"identity": IDENTITY_PAYLOAD, "lease_token": fixture.token, "run_id": "missing"}),
            )
            for path, payload in requests:
                with self.subTest(path=path):
                    status, result = http_post(base, path, payload)
                    self.assertEqual((status, result["error"]), (409, "retail_quarantine"))
            status, result = http_post(base, "/lifecycle/status", {"identity": IDENTITY_PAYLOAD})
            self.assertEqual((status, result["retail_quarantine"]), (200, True))
            status, result = http_post(
                base,
                "/admin/reconcile",
                {"run_id": "missing", "pid": 9, "reason": "incident", "confirmation": "FORCE missing 9"},
            )
            self.assertEqual((status, result["error"]), (409, "retail_quarantine"))
            status, result = http_post(
                base,
                "/admin/release",
                {
                    "lease_id": fixture.lease_id,
                    "reason": "incident",
                    "confirmation": f"FORCE {fixture.lease_id}",
                },
            )
            self.assertEqual((status, result["released"]), (200, True))
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.guard.snapshot_calls, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2.0)
            fixture.close()

    def test_real_lifecycle_clean_routes_cover_start_stop_adopt_reconcile_and_empty(self) -> None:
        fixture = LifecycleFixture()
        state = loopback.ServerState("key", coordination=fixture.coordinator)
        bind_both_peers(state)
        state.lifecycle = fixture.lifecycle
        state.retail_probe = lambda: {"known": True, "processes": []}
        fixture.probe = {"known": True, "processes": []}
        server = loopback.create_http_server(
            0, state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base = f"http://{host}:{port}"
        try:
            launched = record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[launched.pid] = identity(launched)
            status, started = http_post(
                base,
                "/lifecycle/start",
                {
                    "identity": IDENTITY_PAYLOAD,
                    "lease_token": fixture.token,
                    "request": fixture.request(),
                },
            )
            self.assertEqual((status, started["state"]), (200, "RUNNING"))
            status, state_payload = http_post(
                base, "/lifecycle/status", {"identity": IDENTITY_PAYLOAD}
            )
            self.assertEqual((status, state_payload["retail_quarantine"]), (200, False))
            status, stopped = http_post(
                base,
                "/lifecycle/stop",
                {
                    "identity": IDENTITY_PAYLOAD,
                    "lease_token": fixture.token,
                    "run_id": started["run_id"],
                },
            )
            self.assertEqual((status, stopped["state"]), (200, "EXITED"))

            idle_process = record(7601)
            fixture.add_run(
                idle_process,
                run_id="idle",
                state="RUNNING_IDLE",
                owned=False,
            )
            fixture.guard.snapshots[idle_process.pid] = identity(idle_process)
            status, adopted = http_post(
                base,
                "/lifecycle/adopt",
                {
                    "identity": IDENTITY_PAYLOAD,
                    "lease_token": fixture.token,
                    "run_id": "idle",
                },
            )
            self.assertEqual((status, adopted["state"]), (200, "RUNNING"))

            survivor = record(7602)
            fixture.add_run(survivor, run_id="repair", state="UNRECONCILED")
            fixture.guard.snapshots[survivor.pid] = identity(survivor)
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [
                    {"pid": survivor.pid, "name": "DayZDiag_x64.exe"}
                ],
            }
            status, reconciled = http_post(
                base,
                "/admin/reconcile",
                {
                    "run_id": "repair",
                    "pid": survivor.pid,
                    "reason": "repair",
                    "confirmation": f"FORCE repair {survivor.pid}",
                },
            )
            self.assertEqual((status, reconciled["state"]), (200, "RUNNING_IDLE"))

            fixture.store.add(
                RunRecord(
                    "empty-http",
                    IDENTITY.session_id,
                    fixture.lease_id,
                    "UNRECONCILED",
                    "red",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [],
                )
            )
            fixture.lifecycle.diag_probe = lambda: {"known": True, "processes": []}
            status, emptied = http_post(
                base,
                "/admin/reconcile",
                {
                    "run_id": "empty-http",
                    "empty": True,
                    "reason": "confirmed empty",
                    "confirmation": "FORCE empty-http EMPTY",
                },
            )
            self.assertEqual((status, emptied["state"]), (200, "EXITED"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2.0)
            fixture.close()


# --- from test_session_http.py ---


class SessionHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = "session-key"
        self.audit_fails = False
        self.audit_events: list[dict[str, object]] = []
        self.store = SnapshotStore()
        self.state = loopback.ServerState(self.key)
        bind_both_peers(self.state)

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
            self.base, "GET", "/poll", self.key, query={"peer": "server", "inst": INST_SERVER}
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
            self.base, "GET", "/poll", self.key, query={"peer": "client", "inst": INST_CLIENT}
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

    def test_box_wait_fifo_over_http(self) -> None:
        # RED if /session/status ignores box_wait and does not persist FIFO order.
        status, first = self.request(
            "/session/status",
            {"identity": IDENTITY_A, "box_wait": True},
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(first.get("box_ticket"), str)
        self.assertEqual(first.get("box_position"), 1)
        self.assertEqual(first["box"]["queue"][0]["session"], "A")

        status, second = self.request(
            "/session/status",
            {"identity": IDENTITY_B, "box_wait": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(second.get("box_position"), 2)
        self.assertFalse(second.get("box_claimed"))

        status, stolen = self.request(
            "/session/status",
            {
                "identity": IDENTITY_B,
                "box_wait": True,
                "box_ticket": second["box_ticket"],
                "box_wait_claim": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(stolen.get("box_claimed"))

        status, done = self.request(
            "/session/status",
            {
                "identity": IDENTITY_A,
                "box_wait_done": True,
                "box_ticket": first["box_ticket"],
            },
        )
        self.assertEqual(status, 200)
        self.assertIsNone(done.get("box_ticket"))
        self.assertEqual(len(done["box"]["queue"]), 1)
        self.assertEqual(done["box"]["queue"][0]["session"], "B")

    def test_get_status_does_not_attach_box(self) -> None:
        # RED if GET /status grows a box key (health-probe budget).
        status, body = _http(self.base, "GET", "/status", self.key)
        self.assertEqual(status, 200)
        self.assertNotIn("box", body)

    def test_session_status_without_revision_change_does_not_write_coordination(self) -> None:
        self.acquire()
        after_acquire = len(self.store.payloads)
        self.assertGreater(after_acquire, 0)
        for _ in range(5):
            status, body = self.request("/session/status", {"identity": IDENTITY_A})
            self.assertEqual(status, 200)
            self.assertNotEqual(body.get("cleanup_degraded"), ["snapshot_failed"])
        self.assertEqual(len(self.store.payloads), after_acquire)

    def test_box_wait_refresh_does_not_write_coordination(self) -> None:
        status, first = self.request(
            "/session/status",
            {"identity": IDENTITY_A, "box_wait": True},
        )
        self.assertEqual(status, 200)
        ticket = first.get("box_ticket")
        self.assertIsInstance(ticket, str)
        after_ticket = len(self.store.payloads)
        self.assertGreater(after_ticket, 0)
        for _ in range(5):
            status, body = self.request(
                "/session/status",
                {
                    "identity": IDENTITY_A,
                    "box_wait": True,
                    "box_ticket": ticket,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(body.get("box_ticket"), ticket)
            self.assertNotEqual(body.get("cleanup_degraded"), ["snapshot_failed"])
        self.assertEqual(len(self.store.payloads), after_ticket)

    def test_acquire_that_grants_lease_writes_coordination(self) -> None:
        before = len(self.store.payloads)
        self.acquire()
        after_grant = len(self.store.payloads)
        self.assertGreater(after_grant, before)
        status, queued = self.request(
            "/session/acquire", {"identity": IDENTITY_B, "purpose": "queued"}
        )
        self.assertEqual(status, 202)
        self.assertGreater(len(self.store.payloads), after_grant)


class ProductionCoordinationStorePersistSkipTest(unittest.TestCase):
    """Skip of `_persist_coordination` must bind to CoordinationSnapshotStore,
    not to the SnapshotStore test double."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.key = "session-key"
        self.state = loopback.ServerState(self.key)
        bind_both_peers(self.state)
        self.coordinator = SessionCoordinator()
        self.state.coordination = self.coordinator  # type: ignore[attr-defined]
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        paths = RuntimePaths.from_env({"LOCALAPPDATA": self.temporary.name})
        store = CoordinationSnapshotStore(paths, "generation-test")
        self.write_calls: list[dict[str, object]] = []
        original_write = store.write_coordination

        def counting_write(payload: dict[str, object]) -> bool:
            self.write_calls.append(payload)
            return original_write(payload)

        store.write_coordination = counting_write  # type: ignore[method-assign]
        self.store = store
        self.state.coordination_store = store  # type: ignore[attr-defined]
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
        self.temporary.cleanup()

    def request(self, path: str, payload: dict) -> tuple[int, dict]:
        return _http(self.base, "POST", path, self.key, payload)

    def test_status_poll_skip_uses_production_persisted_revision(self) -> None:
        self.assertIs(type(self.store), CoordinationSnapshotStore)
        with patch("dayz_mcp.runtime_state.os.replace", wraps=os.replace) as replace_call:
            status, body = self.request(
                "/session/acquire", {"identity": IDENTITY_A, "purpose": "test"}
            )
            self.assertEqual(status, 200)
            self.assertNotEqual(body.get("cleanup_degraded"), ["snapshot_failed"])
            self.assertEqual(len(self.write_calls), 1)
            self.assertEqual(replace_call.call_count, 1)
            disk_after_grant = self.store.coordination_path.read_bytes()
            self.assertTrue(disk_after_grant)
            for _ in range(3):
                status, body = self.request(
                    "/session/status", {"identity": IDENTITY_A}
                )
                self.assertEqual(status, 200)
                self.assertNotEqual(body.get("cleanup_degraded"), ["snapshot_failed"])
            self.assertEqual(len(self.write_calls), 1)
            self.assertEqual(replace_call.call_count, 1)
            self.assertEqual(self.store.coordination_path.read_bytes(), disk_after_grant)
            persisted = self.store.persisted_revision()
            self.assertIsInstance(persisted, int)
            self.assertEqual(persisted, self.coordinator.durable_revision())


if __name__ == "__main__":
    unittest.main()
