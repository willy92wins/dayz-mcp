from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


COORDINATION_DIR = Path(__file__).resolve().parents[1] / "_session_coordination"
if str(COORDINATION_DIR) not in sys.path:
    sys.path.insert(0, str(COORDINATION_DIR))

import h8_distributed_codex_gate as gate


class H8DistributedGateTests(unittest.TestCase):
    def _write_roster(self, gate_dir: Path) -> dict:
        roster = {
            "schema": "dayz-mcp-h8-task-roster-v1",
            "source": "collaboration.spawn_agent",
            "nonce": "fixture-roster",
            "roles": {
                "A": {"task_path": "/root", "task_id": "root-thread"},
                "B": {"task_path": "/root/h8-b", "task_id": "agent-b"},
                "C": {"task_path": "/root/h8-c", "task_id": "agent-c"},
                "D": {"task_path": "/root/h8-d", "task_id": "agent-d"},
            },
        }
        (gate_dir / "roster.json").write_text(
            json.dumps(roster, separators=(",", ":")),
            encoding="utf-8",
        )
        return roster

    def test_external_roster_binds_exact_task_and_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gate_dir = Path(temp)
            self._write_roster(gate_dir)
            task = gate._task_from_roster(gate_dir, "B", "/root/h8-b")
            self.assertEqual(task["task_id"], "agent-b")
            self.assertTrue(task["roster_sha256"])
            with self.assertRaises(gate.GateFailure):
                gate._task_from_roster(gate_dir, "B", "/root/forged")

            roster = json.loads((gate_dir / "roster.json").read_text(encoding="utf-8"))
            roster["roles"]["C"]["task_id"] = "agent-b"
            (gate_dir / "roster.json").write_text(
                json.dumps(roster, separators=(",", ":")),
                encoding="utf-8",
            )
            with self.assertRaises(gate.GateFailure):
                gate._task_from_roster(gate_dir, "B", "/root/h8-b")

    def test_status_evidence_does_not_persist_ticket_or_lease_handles(self) -> None:
        evidence = gate._status_evidence(
            {
                "owner": {
                    "state": "active",
                    "lease_id": "lease-secret",
                    "purpose": "fixture",
                    "client": {"session": "abc123"},
                },
                "queue": [
                    {
                        "ticket": "ticket-secret",
                        "position": 1,
                        "purpose": "queued",
                        "client": {"session": "def456"},
                    }
                ],
                "self": {
                    "state": "active",
                    "lease_id": "lease-secret",
                    "ticket": "ticket-secret",
                    "position": 0,
                },
                "cleanup_degraded": [],
                "daemon_generation": "generation",
                "pending_commands": 0,
            }
        )
        wire = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("lease-secret", wire)
        self.assertNotIn("ticket-secret", wire)
        self.assertNotIn("lease_id", wire)
        self.assertNotIn('"ticket"', wire)

    def test_lease_context_uses_observed_generation_when_acquire_omits_it(self) -> None:
        identity = {"json": json.dumps({"session_id": "session-A"})}
        acquired = {
            "lease_token": "token-A",
            "lease_id": "lease-A",
            "status": "active",
        }
        context = gate._distributed_context(
            acquired,
            identity,
            "generation-observed",
        )
        self.assertEqual(context["daemon_generation"], "generation-observed")

        acquired["daemon_generation"] = "generation-other"
        with self.assertRaises(gate.GateFailure):
            gate._distributed_context(
                acquired,
                identity,
                "generation-observed",
            )

    def test_roles_bind_context_to_observed_generation_not_acquire_payload(self) -> None:
        source_a = inspect.getsource(gate._run_a)
        status_index = source_a.index('initial_status = _tool_result(')
        acquire_index = source_a.index('"session_acquire"')
        launch_index = source_a.index("_start_fixture(")
        ready_index = source_a.index("ready0 = _wait_bridge_ready(proxy)")
        self.assertLess(status_index, acquire_index)
        self.assertLess(acquire_index, launch_index)
        self.assertLess(launch_index, ready_index)
        self.assertIn("_clean_status(initial_status)", source_a)
        self.assertIn(
            "_distributed_context(acquired, identity, generation)",
            source_a,
        )

        source_queued = inspect.getsource(gate._run_queued)
        context_slice = source_queued[
            source_queued.index("active, wait_slices") :
            source_queued.index("active_at_utc")
        ]
        self.assertIn("_distributed_context(", context_slice)
        self.assertIn('read["daemon_generation"]', context_slice)

    def test_cleanup_gate_observes_generation_before_building_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gate_dir = Path(temp)
            stderr_path = gate_dir / "role-cleanup.stderr.log"
            stderr_path.write_text("", encoding="utf-8")
            proxy = SimpleNamespace(process=SimpleNamespace(pid=12345))
            identity = {
                "json": json.dumps({"session_id": "cleanup-session"}),
                "session_id": "cleanup-session",
            }
            active = {
                "status": "active",
                "lease_token": "cleanup-token",
                "lease_id": "cleanup-lease",
            }
            observed_status = {
                "owner": {"state": "active"},
                "queue": [],
                "self": {"state": "active", "position": 0},
                "cleanup_degraded": [],
                "daemon_generation": "generation-live",
                "pending_commands": 0,
            }
            final_status = {
                "owner": None,
                "queue": [],
                "self": {"state": "none", "position": None},
                "cleanup_degraded": [],
                "daemon_generation": "generation-live",
                "pending_commands": 0,
            }
            tool_results = iter(
                [
                    active,
                    observed_status,
                    {"released": True, "cleanup_degraded": []},
                    final_status,
                ]
            )

            with (
                patch.object(
                    gate,
                    "_new_proxy",
                    return_value=(proxy, stderr_path, datetime.now(timezone.utc)),
                ),
                patch.object(gate, "_identity_from", return_value=identity),
                patch.object(gate, "_tool_result", side_effect=lambda *_: next(tool_results)),
                patch.object(
                    gate,
                    "_cleanup_registered_runs",
                    return_value={"registered": 0, "owner_discovered": 0, "actions": []},
                ) as cleanup_runs,
                patch.object(gate, "_close_proxy"),
                patch.object(gate, "_wait_process_counts_zero", return_value={}),
                patch.object(gate, "_udp_owners", return_value=[]),
                patch.object(
                    gate,
                    "_doctor",
                    return_value={"exit_code": 0, "ok": True, "finding_codes": []},
                ),
            ):
                result = gate._cleanup_gate(gate_dir)

            self.assertTrue(result["overall_pass"])
            context = cleanup_runs.call_args.args[0]
            self.assertEqual(context["daemon_generation"], "generation-live")

    def test_expiry_evidence_is_bound_to_c_and_rejects_release(self) -> None:
        heartbeat = datetime.now(timezone.utc)
        expired_at = heartbeat + timedelta(seconds=120.2)
        events = [
            {
                "timestamp_utc": expired_at.isoformat().replace("+00:00", "Z"),
                "event": "session_expired",
                "client": {"session": "cccccccccccc"},
                "reason": "lease_ttl",
            },
            {
                "timestamp_utc": (
                    expired_at + timedelta(milliseconds=50)
                ).isoformat().replace("+00:00", "Z"),
                "event": "session_release_finished",
                "client": {"session": "cccccccccccc"},
                "reason": "lease_ttl",
            }
        ]
        evidence = gate._expiry_audit_evidence(
            events,
            "cccccccccccc",
            heartbeat.isoformat().replace("+00:00", "Z"),
        )
        self.assertAlmostEqual(evidence["heartbeat_to_expiry_s"], 120.2, places=1)
        self.assertEqual(evidence["release_started_events"], 0)
        self.assertEqual(evidence["expiry_cleanup_finished_events"], 1)

        events.append(
            {
                "timestamp_utc": expired_at.isoformat().replace("+00:00", "Z"),
                "event": "session_release_started",
                "client": {"session": "cccccccccccc"},
            }
        )
        with self.assertRaises(gate.GateFailure):
            gate._expiry_audit_evidence(
                events,
                "cccccccccccc",
                heartbeat.isoformat().replace("+00:00", "Z"),
            )

    def test_c_adopts_before_abrupt_exit_and_failure_paths_cleanup(self) -> None:
        source = inspect.getsource(gate._run_queued)
        role_c = source[source.index('if role == "C"') :]
        self.assertLess(role_c.index('"adopt"'), role_c.index("_kill_own_proxy"))
        self.assertIn("_cleanup_registered_runs", source)
        self.assertIn("_cleanup_registered_runs", inspect.getsource(gate._run_a))
        self.assertIn("runner_pids_not_unique", inspect.getsource(gate._finalize))

    def test_cleanup_discovers_unregistered_run_owned_by_current_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gate_dir = Path(temp)
            self._write_roster(gate_dir)
            context = {
                "identity_json": json.dumps({"session_id": "session-A"}),
                "lease_id": "lease-A",
                "lease_token": "token-A",
                "daemon_generation": "generation-A",
            }
            lifecycle_status = {
                "runs": [
                    {
                        "run_id": "run-unregistered",
                        "owner_session_id": "session-A",
                        "owner_lease_id": "lease-A",
                        "state": "RUNNING",
                        "label": "fixture",
                        "mod": "@MilitaryBunker",
                    },
                    {
                        "run_id": "run-foreign",
                        "owner_session_id": "session-other",
                        "owner_lease_id": "lease-other",
                        "state": "RUNNING",
                        "label": "foreign",
                        "mod": "@MilitaryBunker",
                    },
                ]
            }
            calls = [
                (0, lifecycle_status),
                (0, {"state": "EXITED"}),
                (
                    0,
                    {
                        "runs": [
                            {
                                "run_id": "run-unregistered",
                                "owner_session_id": None,
                                "owner_lease_id": None,
                                "state": "EXITED",
                                "label": "fixture",
                                "mod": "@MilitaryBunker",
                            },
                            lifecycle_status["runs"][1],
                        ]
                    },
                ),
            ]
            with patch.object(gate, "_lifecycle_call", side_effect=calls) as lifecycle:
                result = gate._cleanup_registered_runs(context, gate_dir)
            self.assertEqual(result["registered"], 0)
            self.assertEqual(result["owner_discovered"], 1)
            self.assertEqual(result["actions"][0]["run_id"], "run-unregistered")
            self.assertEqual(result["actions"][0]["discovery"], "owner_manifest")
            stop_calls = [
                call
                for call in lifecycle.call_args_list
                if len(call.args) >= 2 and call.args[1] == "stop"
            ]
            self.assertEqual(len(stop_calls), 1)
            self.assertEqual(stop_calls[0].kwargs["run_id"], "run-unregistered")

    def test_cleanup_rejects_stale_marker_before_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gate_dir = Path(temp)
            self._write_roster(gate_dir)
            context = {
                "identity_json": json.dumps({"session_id": "session-cleanup"}),
                "lease_id": "lease-cleanup",
                "lease_token": "token-cleanup",
                "daemon_generation": "generation-current",
            }
            (gate_dir / "registered-R0.json").write_text(
                json.dumps(
                    {
                        "schema": "dayz-mcp-h8-registered-run-v2",
                        "label": "R0",
                        "run_id": "run-stale",
                        "gate_nonce": "old-run",
                        "roster_sha256": "0" * 64,
                        "daemon_generation": "generation-old",
                        "owner_session_sha256_12": "1" * 12,
                        "owner_lease_sha256_12": "2" * 12,
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            with patch.object(gate, "_lifecycle_call") as lifecycle:
                with self.assertRaises(gate.GateFailure):
                    gate._cleanup_registered_runs(context, gate_dir)
            lifecycle.assert_not_called()

    def test_failure_stderr_is_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "role-A.stderr.log"
            path.write_text("token-A\nidentity-A\n", encoding="utf-8")
            gate._delete_failure_stderr(path)
            self.assertFalse(path.exists())

    def test_cleanup_success_stderr_is_scanned_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "role-cleanup.stderr.log"
            path.write_text("DAEMON: healthy\n", encoding="utf-8")
            result = gate._scan_and_delete_stderr(path, ["secret-value"])
            self.assertEqual(result["values_found"], 0)
            self.assertFalse(path.exists())

            path.write_text("leaked secret-value\n", encoding="utf-8")
            with self.assertRaises(gate.GateFailure):
                gate._scan_and_delete_stderr(path, ["secret-value"])
            self.assertFalse(path.exists())

    def test_full_audit_scan_allows_process_metadata_but_rejects_secret_handles(self) -> None:
        process_audit = [{"event": "admin_reconcile", "pid": 12345}]
        self.assertTrue(gate._forbidden_key(process_audit))
        self.assertFalse(gate._has_forbidden_role_key(process_audit))
        self.assertTrue(
            gate._has_forbidden_role_key(
                [{"event": "bad", "lease_token": "secret"}]
            )
        )
        source = inspect.getsource(gate._finalize)
        self.assertIn(
            "_has_forbidden_role_key(_all_audit_documents",
            source,
        )


if __name__ == "__main__":
    unittest.main()
