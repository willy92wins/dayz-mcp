from __future__ import annotations

import json
import hashlib
import sys
import threading
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback
from dayz_mcp.runtime_state import (
    CoordinationFaultStore,
    CoordinationSnapshotStore,
    JsonlAuditWriter,
    LifecycleRecoveryFaultStore,
    RuntimePaths,
)
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator


IDENTITY = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-15T00:00:00Z",
    "session_id": "A",
    "task_label": "http",
}


def request(base: str, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    url = base + path + "?" + urllib.parse.urlencode({"key": "key"})
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        finally:
            exc.close()


class FakeLifecycle:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def start_run(self, client, token, value):
        self.calls.append(("start", value))
        return {"ok": True, "run_id": "R", "state": "RUNNING"}

    def stop_run(self, client, token, value):
        self.calls.append(("stop", value))
        return {"error": "process_identity_mismatch", "_http_status": 409}

    def adopt_run(self, client, token, value):
        self.calls.append(("adopt", value))
        return {"ok": True, "run_id": value, "state": "RUNNING"}

    def reap_dead_run(self, client, token, value):
        self.calls.append(("reap", value))
        return {"ok": True, "run_id": value, "state": "EXITED"}

    def ack_run(self, client, token, run_id, launch_operation_id):
        self.calls.append(("ack", (run_id, launch_operation_id)))
        return {"ok": True, "run_id": run_id, "state": "RUNNING"}

    def status(self, client):
        self.calls.append(("status", client.session_id))
        return {"runs": [], "retail_quarantine": False}

    def admin_reconcile(self, run_id, pid, reason):
        self.calls.append(("reconcile", (run_id, pid, reason)))
        return {"reconciled": True}

    def repair_recovery_fault(self, fault):
        self.calls.append(("repair-recovery", fault["fault_id"]))
        return {
            "terminal_safe": True,
            "run_id": fault["run_id"],
            "state": "EXITED",
        }

    def repair_manifest_recovery(self, raw):
        self.calls.append(("repair-manifest", raw))
        return {
            "terminal_safe": True,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        }


class LifecycleHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("key")
        self.events: list[dict[str, object]] = []
        self.state.coordination = SessionCoordinator(
            token_fn=lambda: "token",
            id_fn=lambda: "lease",
            audit=lambda event: self.events.append(event) or True,
        )
        self.lifecycle = FakeLifecycle()
        self.state.lifecycle = self.lifecycle
        self.httpd = loopback.create_http_server(
            0, self.state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)

    def test_lifecycle_routes_validate_identity_and_transport_http_status(self) -> None:
        status, result = request(self.base, "/lifecycle/start", {"identity": {}, "request": {}})
        self.assertEqual((status, result["error"]), (400, "invalid_identity"))
        status, result = request(
            self.base,
            "/lifecycle/start",
            {"identity": IDENTITY, "lease_token": "token", "request": {"argv": ["diag"]}},
        )
        self.assertEqual((status, result["run_id"]), (200, "R"))
        status, result = request(
            self.base,
            "/lifecycle/stop",
            {"identity": IDENTITY, "lease_token": "token", "run_id": "R"},
        )
        self.assertEqual((status, result["error"]), (409, "process_identity_mismatch"))
        status, result = request(
            self.base,
            "/lifecycle/reap",
            {"identity": IDENTITY, "lease_token": "token", "run_id": "R"},
        )
        self.assertEqual((status, result["state"]), (200, "EXITED"))
        self.assertIn(("reap", "R"), self.lifecycle.calls)
        status, result = request(
            self.base,
            "/lifecycle/ack",
            {
                "identity": IDENTITY,
                "lease_token": "token",
                "run_id": "R",
                "launch_operation_id": "OP",
            },
        )
        self.assertEqual((status, result["state"]), (200, "RUNNING"))
        self.assertIn(("ack", ("R", "OP")), self.lifecycle.calls)
        status, result = request(
            self.base, "/lifecycle/status", {"identity": IDENTITY}
        )
        self.assertEqual((status, result["retail_quarantine"]), (200, False))

    def test_lifecycle_targeted_status_filters_exact_run_and_retains_envelope(
        self,
    ) -> None:
        selected = {"run_id": "selected", "state": "RUNNING"}
        other = {"run_id": "other", "state": "EXITED"}

        def status_for_two_runs(client):
            self.lifecycle.calls.append(("status", client.session_id))
            return {
                "daemon_generation": "fixture-generation",
                "retail_quarantine": False,
                "runs": [other, selected],
            }

        self.lifecycle.status = status_for_two_runs
        status, result = request(
            self.base,
            "/lifecycle/status",
            {"identity": IDENTITY, "run_id": "selected"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(result["runs"], [selected])
        self.assertEqual(result["daemon_generation"], "fixture-generation")
        self.assertIs(result["retail_quarantine"], False)
        self.assertIn(("status", IDENTITY["session_id"]), self.lifecycle.calls)

    def test_admin_release_requires_exact_confirmation_and_reconcile_is_quarantinable(self) -> None:
        client = ClientIdentity.from_payload(IDENTITY)
        _, acquired = self.state.coordination.acquire(client, "admin")
        lease_id = acquired["lease_id"]
        status, result = request(
            self.base,
            "/admin/release",
            {"lease_id": lease_id, "reason": "incident", "confirmation": "wrong"},
        )
        self.assertEqual((status, result["error"]), (403, "confirmation_required"))
        self.state.retail_probe = lambda: {"known": False, "processes": []}
        status, result = request(
            self.base,
            "/admin/release",
            {
                "lease_id": lease_id,
                "reason": "incident",
                "confirmation": f"FORCE {lease_id}",
            },
        )
        self.assertEqual((status, result["released"]), (200, True))
        status, result = request(
            self.base,
            "/admin/reconcile",
            {
                "run_id": "R",
                "pid": 9,
                "reason": "repair",
                "confirmation": "FORCE R 9",
            },
        )
        self.assertEqual((status, result["reconciled"]), (200, True))

    def test_admin_audit_repair_is_confirmed_idempotent_and_unblocks_grants(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = RuntimePaths(
                root,
                root / "audit",
                root / "coordination.json",
                root / "runs.json",
            )
            marker = {
                "format_version": 1,
                "fault_id": "fault-1",
                "daemon_generation": "generation-a",
                "state": "fault",
                "operation": "grant",
                "phase": "audit_failed",
                "lease_id": "lease-1",
                "ticket_id": "ticket-1",
                "client": {
                    "platform": "codex",
                    "session": "session-a",
                    "started_at_utc": "2026-07-22T00:00:00Z",
                    "task_label": "repair",
                },
                "reason": "fifo_head",
                "armed_at_utc": "2026-07-22T00:00:00Z",
                "failure": "audit_failed",
                "expected_snapshot_revision": 1,
                "repair_phase": "none",
            }
            fault_store = CoordinationFaultStore(paths)
            fault_store.arm(marker | {"state": "armed", "failure": None, "phase": "armed"})
            armed, armed_sha = fault_store.load_with_sha()
            fault_store.transition(
                "fault-1",
                armed_sha,
                state="fault",
                phase="audit_failed",
                failure="audit_failed",
            )
            snapshot_store = CoordinationSnapshotStore(paths, "generation-a")
            writer = JsonlAuditWriter(paths, "generation-a")
            self.state.coordination = SessionCoordinator(
                recovered_audit_fault=marker,
                daemon_generation="generation-a",
                fault_arm=fault_store.arm,
                fault_transition=fault_store.transition,
                fault_clear=fault_store.clear,
                persist_snapshot=snapshot_store.write_coordination,
            )
            self.state.coordination_fault_store = fault_store
            self.state.coordination_store = snapshot_store
            self.state.audit_writer = writer
            self.httpd = loopback.create_http_server(
                0, self.state, log_sink=lambda _message: None, reclaim_orphans=False
            )
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
            host, port = self.httpd.server_address
            self.base = f"http://{host}:{port}"

            status, result = request(
                self.base,
                "/admin/audit-repair",
                {"fault_id": "fault-1", "reason": "operator repair", "confirmation": "wrong"},
            )
            self.assertEqual((status, result["error"]), (403, "confirmation_required"))
            status, result = request(
                self.base,
                "/admin/audit-repair",
                {
                    "fault_id": "fault-1",
                    "reason": "operator repair",
                    "confirmation": "REPAIR fault-1",
                },
            )
            self.assertEqual((status, result["repaired"]), (200, True))
            self.assertEqual(fault_store.load_with_sha(), (None, None))
            acquired = self.state.coordination.acquire(
                ClientIdentity.from_payload(IDENTITY), "after repair"
            )
            self.assertEqual((acquired[0], acquired[1]["status"]), (200, "active"))
            event_ids = [
                json.loads(line)["event_id"]
                for line in writer.current_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                event_ids,
                ["fault-1:compensation", "fault-1:repaired"],
            )

    def test_admin_lifecycle_recovery_repair_is_exactly_confirmed_and_cas_bound(self) -> None:
        fault_id = "11111111-1111-4111-8111-111111111111"
        head = "A" * 64
        active = {
            "fault": {
                "fault_id": fault_id,
                "scope": "run",
                "run_id": "R",
                "launch_operation_id": "22222222-2222-4222-8222-222222222222",
                "run_record_sha256": "b" * 64,
            },
            "event": {"state": "armed"},
            "pointer": {"head_event_sha256": head},
        }

        class Store:
            def load_active(self):
                return active

            def transition(self, fault_id_arg, expected, **values):
                if values["state"] == "repairing":
                    active["event"] = {"state": "repairing"}
                    active["pointer"] = {"head_event_sha256": "B" * 64}
                    return "B" * 64
                active["event"] = {"state": "repaired"}
                active["pointer"] = {"head_event_sha256": "C" * 64}
                return "C" * 64

            def create_receipt(self, fault_id_arg, payload):
                return "D" * 64

        self.state.lifecycle_recovery_fault_store = Store()
        self.state.coordination.arm_lifecycle_recovery_fault(active)
        status, result = request(
            self.base,
            "/admin/lifecycle-recovery-repair",
            {
                "fault_id": fault_id,
                "expected_head_sha256": head,
                "reason": "operator reviewed exact process identity",
                "confirmation": f"REPAIR LIFECYCLE {fault_id}",
            },
        )
        self.assertEqual((status, result["repaired"]), (200, True))
        self.assertIsNone(
            self.state.coordination.status(
                ClientIdentity.from_payload(IDENTITY)
            )["lifecycle_recovery_fault"]
        )
        self.assertIn(("repair-recovery", fault_id), self.lifecycle.calls)

    def test_manifest_scope_restores_only_hash_bound_backup_before_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = RuntimePaths(
                root,
                root / "audit",
                root / "coordination.json",
                root / "runs.json",
            )
            store = LifecycleRecoveryFaultStore(
                paths,
                fault_id_fn=lambda: "11111111-1111-4111-8111-111111111111",
                utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            )
            valid_raw = b'{"version":1,"runs":[]}\n'
            backup_receipt = store.create_manifest_backup(valid_raw)
            active = store.arm(
                scope="manifest",
                reason="manifest_corrupt",
                manifest_sha256=hashlib.sha256(b"corrupt").hexdigest(),
                backup_receipt_sha256=backup_receipt,
            )
            self.state.lifecycle_recovery_fault_store = store
            self.state.coordination.arm_lifecycle_recovery_fault(active)

            status, result = request(
                self.base,
                "/admin/lifecycle-recovery-repair",
                {
                    "fault_id": active["fault"]["fault_id"],
                    "expected_head_sha256": active["pointer"]["head_event_sha256"],
                    "reason": "restore exact validated backup",
                    "confirmation": f"REPAIR LIFECYCLE {active['fault']['fault_id']}",
                },
            )

            self.assertEqual((status, result["repaired"]), (200, True))
            self.assertIn(("repair-manifest", valid_raw), self.lifecycle.calls)
            self.assertEqual(store.load_active()["event"]["state"], "repaired")

    def test_manifest_scope_receipt_drift_returns_to_armed_without_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = RuntimePaths(
                root,
                root / "audit",
                root / "coordination.json",
                root / "runs.json",
            )
            store = LifecycleRecoveryFaultStore(
                paths,
                fault_id_fn=lambda: "11111111-1111-4111-8111-111111111111",
                utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            )
            backup_receipt = store.create_manifest_backup(
                b'{"version":1,"runs":[]}\n'
            )
            active = store.arm(
                scope="manifest",
                reason="manifest_corrupt",
                manifest_sha256=hashlib.sha256(b"corrupt").hexdigest(),
                backup_receipt_sha256=backup_receipt,
            )
            backup = (
                paths.lifecycle_recovery_faults_dir
                / "backups"
                / backup_receipt
                / "manifest.bin"
            )
            backup.write_bytes(b"drift")
            self.state.lifecycle_recovery_fault_store = store
            self.state.coordination.arm_lifecycle_recovery_fault(active)

            status, result = request(
                self.base,
                "/admin/lifecycle-recovery-repair",
                {
                    "fault_id": active["fault"]["fault_id"],
                    "expected_head_sha256": active["pointer"]["head_event_sha256"],
                    "reason": "attempt drifted backup",
                    "confirmation": f"REPAIR LIFECYCLE {active['fault']['fault_id']}",
                },
            )

            self.assertEqual((status, result["error"]), (409, "receipt_missing"))
            self.assertEqual(store.load_active()["event"]["state"], "armed")
            self.assertFalse(
                any(call[0] == "repair-manifest" for call in self.lifecycle.calls)
            )

    def test_lifecycle_recovery_repair_resumes_from_repairing(self) -> None:
        # F-02: a failed re-arm used to leave the pointer in repairing, and the
        # gate only admitted repaired/armed, so retry 409'd on both heads.
        attempts = {"n": 0}

        class FlakyLifecycle(FakeLifecycle):
            def repair_recovery_fault(self, fault):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    return {"terminal_safe": False, "error": "cleanup_failed"}
                return super().repair_recovery_fault(fault)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = RuntimePaths(
                root,
                root / "audit",
                root / "coordination.json",
                root / "runs.json",
            )
            store = LifecycleRecoveryFaultStore(
                paths,
                fault_id_fn=lambda: "11111111-1111-4111-8111-111111111111",
                utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            )
            backup_receipt = store.create_manifest_backup(
                b'{"version":1,"runs":[]}\n'
            )
            active = store.arm(
                scope="run",
                reason="cleanup_failed",
                manifest_sha256=hashlib.sha256(b"corrupt").hexdigest(),
                backup_receipt_sha256=backup_receipt,
                run_id="R",
                launch_operation_id="22222222-2222-4222-8222-222222222222",
                run_record_sha256="c" * 64,
            )
            original_transition = store.transition

            def transition_no_rearm(fault_id_arg, expected, **values):
                if values.get("state") == "armed":
                    raise OSError("rearm failed")
                return original_transition(fault_id_arg, expected, **values)

            store.transition = transition_no_rearm  # type: ignore[method-assign]
            self.state.lifecycle = FlakyLifecycle()
            self.state.lifecycle_recovery_fault_store = store
            self.state.coordination.arm_lifecycle_recovery_fault(active)
            fault_id = active["fault"]["fault_id"]
            armed_head = active["pointer"]["head_event_sha256"]

            first_status, first = loopback._repair_lifecycle_recovery_fault(
                self.state, fault_id, armed_head
            )
            self.assertEqual((first_status, first.get("error")), (409, "cleanup_failed"))
            stuck = store.load_active()
            self.assertEqual(stuck["event"]["state"], "repairing")
            repairing_head = stuck["pointer"]["head_event_sha256"]
            self.assertNotEqual(repairing_head, armed_head)

            second_status, second = loopback._repair_lifecycle_recovery_fault(
                self.state, fault_id, repairing_head
            )
            self.assertEqual((second_status, second.get("repaired")), (200, True))
            self.assertEqual(store.load_active()["event"]["state"], "repaired")
            self.assertEqual(attempts["n"], 2)


if __name__ == "__main__":
    unittest.main()
