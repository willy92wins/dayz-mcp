from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from dayz_mcp import admin_cli
from dayz_mcp.process_lifecycle import RunRecord
from tests.test_lifecycle_cli import TtyInput
from tests.test_task7_review_regressions import (
    IDENTITY,
    LifecycleFixture,
    identity,
    record,
)


class PostProvisionalRetailFenceTest(unittest.TestCase):
    def test_new_start_rechecks_retail_after_starting_write_before_launch(self) -> None:
        fixture = LifecycleFixture()
        try:
            original_add = fixture.store.add

            def add_then_quarantine(run: RunRecord) -> None:
                original_add(run)
                fixture.probe = {
                    "known": True,
                    "processes": [{"pid": 99001, "name": "DayZ_x64.exe"}],
                }

            fixture.store.add = add_then_quarantine  # type: ignore[method-assign]
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )

            self.assertEqual(result.get("error"), "retail_quarantine")
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.guard.snapshot_calls, [])
            run = fixture.store.get(str(result["run_id"]))
            self.assertIsNotNone(run)
            self.assertEqual(
                (run.state, run.owner_session_id, run.owner_lease_id, run.processes),
                ("EXITED", None, None, []),
            )
            terminal = [
                event
                for event in fixture.audit.events
                if event.get("event") == "lifecycle_start_outcome"
            ]
            self.assertEqual(terminal[-1].get("decision"), "retail_quarantine")
        finally:
            fixture.close()

    def test_extension_rechecks_retail_and_restores_previous_bytes_before_launch(self) -> None:
        fixture = LifecycleFixture()
        try:
            existing = record(99002)
            fixture.add_run(existing)
            before = fixture.paths.runs_path.read_bytes()
            original_replace = fixture.store.replace

            def replace_then_quarantine(run: RunRecord) -> None:
                original_replace(run)
                if run.state == "STARTING":
                    fixture.probe = {
                        "known": True,
                        "processes": [{"pid": 99003, "name": "DayZ_BE.exe"}],
                    }

            fixture.store.replace = replace_then_quarantine  # type: ignore[method-assign]
            result = fixture.lifecycle.start_run(
                IDENTITY,
                fixture.token,
                fixture.request(run_id="existing"),
            )

            self.assertEqual(result.get("error"), "retail_quarantine")
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.guard.snapshot_calls, [])
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
            terminal = [
                event
                for event in fixture.audit.events
                if event.get("event") == "lifecycle_start_outcome"
            ]
            self.assertEqual(terminal[-1].get("decision"), "retail_quarantine")
        finally:
            fixture.close()


class ReconcileExactSurvivorSetTest(unittest.TestCase):
    def test_reused_pruned_pid_is_extra_diag_and_preserves_manifest_bytes(self) -> None:
        fixture = LifecycleFixture()
        try:
            gone = record(99004)
            fixture.add_run(gone, state="UNRECONCILED")
            fixture.guard.snapshots[gone.pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [{"pid": gone.pid, "name": "DayZDiag_x64.exe"}],
            }
            before = fixture.paths.runs_path.read_bytes()

            result = fixture.lifecycle.admin_reconcile(
                "existing", gone.pid, "pid reused"
            )

            self.assertEqual(result.get("error"), "manual_cleanup_required")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
        finally:
            fixture.close()

    def test_survivor_disappearing_before_second_diag_preserves_manifest_bytes(self) -> None:
        fixture = LifecycleFixture()
        try:
            survivor = record(99005)
            fixture.add_run(survivor, state="UNRECONCILED")
            fixture.guard.snapshots[survivor.pid] = identity(survivor)
            diag_calls = 0

            def diag_probe() -> dict[str, object]:
                nonlocal diag_calls
                diag_calls += 1
                processes = (
                    [{"pid": survivor.pid, "name": "DayZDiag_x64.exe"}]
                    if diag_calls == 1
                    else []
                )
                return {"known": True, "processes": processes}

            fixture.lifecycle.diag_probe = diag_probe
            before = fixture.paths.runs_path.read_bytes()

            result = fixture.lifecycle.admin_reconcile(
                "existing", survivor.pid, "survivor disappeared"
            )

            self.assertEqual(result.get("error"), "manual_cleanup_required")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
        finally:
            fixture.close()


class AdminCliReconcileStateMatrixTest(unittest.TestCase):
    @staticmethod
    def _run(state: str, *, owned: bool = False) -> tuple[int, int]:
        run = {
            "run_id": "target",
            "state": state,
            "processes": [{"pid": 99006}],
        }
        if owned:
            run["owner_session_id"] = "session-owned"
            run["owner_lease_id"] = "lease-owned"
        status_payload = {
            "lifecycle": {
                "runs": [run]
            }
        }
        post_calls = 0

        policy = object()

        def request(actual_policy, method, _path, payload=None):
            nonlocal post_calls
            if actual_policy is not policy:
                raise AssertionError("unexpected_daemon_policy")
            if method == "GET":
                return 200, status_payload
            post_calls += 1
            return 200, {"reconciled": True}

        with (
            patch("sys.stdin", TtyInput("FORCE target 99006\n")),
            patch.object(
                admin_cli.daemon_policy,
                "load_daemon_policy",
                return_value=policy,
            ),
            patch.object(admin_cli, "_request", request),
            patch("sys.stdout", io.StringIO()),
            patch("sys.stderr", io.StringIO()),
        ):
            code = admin_cli.main(
                [
                    "--daemon-policy",
                    "normal",
                    "reconcile",
                    "--reason",
                    "incident",
                    "--run-id",
                    "target",
                    "--pid",
                    "99006",
                ]
            )
        return code, post_calls

    def test_cli_accepts_only_backend_reconcilable_states(self) -> None:
        for state in ("UNRECONCILED", "STARTING", "STOPPING", "RUNNING_IDLE"):
            with self.subTest(accepted=state):
                self.assertEqual(self._run(state), (0, 1))
        for state in ("RUNNING", "EXITED"):
            with self.subTest(rejected=state):
                self.assertEqual(self._run(state), (2, 0))
        self.assertEqual(self._run("RUNNING_IDLE", owned=True), (2, 0))


class StopPreflightReservationRollbackTest(unittest.TestCase):
    def test_manifest_failure_aborts_exact_reservation_and_surfaces_audit_failure(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(99007)
            fixture.add_run(expected)
            fixture.guard.snapshots[expected.pid] = {
                "error": "identity_unavailable",
                "exit_code": 3,
            }
            before = fixture.paths.runs_path.read_bytes()
            fixture.store.replace = (  # type: ignore[method-assign]
                lambda _run: (_ for _ in ()).throw(OSError("disk full"))
            )
            fixture.audit.fail_events.add("session_rejected")

            result = fixture.lifecycle.stop_run(
                IDENTITY, fixture.token, "existing"
            )

            self.assertEqual(result.get("error"), "manifest_failed")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
            self.assertEqual(fixture.guard.terminate_calls, [])
            self.assertEqual(fixture.coordinator._active.pending_authorizations, [])
            self.assertIn("manifest_failed", result.get("cleanup_degraded", []))
            self.assertIn("audit_failed", result.get("cleanup_degraded", []))
        finally:
            fixture.close()


class AdoptPrecommitRollbackTest(unittest.TestCase):
    def test_guard_exception_rejects_exact_reservation_without_manifest_change(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(99009)
            fixture.add_run(expected, state="RUNNING_IDLE", owned=False)
            before = fixture.paths.runs_path.read_bytes()

            def fail_snapshot(_pid: int) -> dict[str, object]:
                raise TimeoutError("guard timeout")

            fixture.guard.snapshot = fail_snapshot  # type: ignore[method-assign]
            result = fixture.lifecycle.adopt_run(
                IDENTITY, fixture.token, "existing"
            )

            self.assertEqual(result.get("error"), "guard_unavailable")
            self.assertEqual(result.get("_http_status"), 503)
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
            self.assertEqual(fixture.coordinator._active.pending_authorizations, [])
        finally:
            fixture.close()


class RunRecordStateInvariantTest(unittest.TestCase):
    def test_validate_enforces_terminal_running_and_idle_shapes(self) -> None:
        process = record(99008)
        valid = (
            RunRecord("exited", None, None, "EXITED", "", "", "", "", []),
            RunRecord(
                "running", "session", "lease", "RUNNING", "", "", "", "", [process]
            ),
            RunRecord(
                "idle", None, None, "RUNNING_IDLE", "", "", "", "", [process]
            ),
        )
        for run in valid:
            with self.subTest(valid=run.state):
                run.validate()

        invalid = (
            RunRecord(
                "null-label", None, None, "EXITED", None, "", "", "", []
            ),
            RunRecord(
                "exited-owned",
                "session",
                "lease",
                "EXITED",
                "",
                "",
                "",
                "",
                [],
            ),
            RunRecord(
                "exited-process", None, None, "EXITED", "", "", "", "", [process]
            ),
            RunRecord(
                "running-empty",
                "session",
                "lease",
                "RUNNING",
                "",
                "",
                "",
                "",
                [],
            ),
            RunRecord(
                "running-empty-owner",
                "",
                "",
                "RUNNING",
                "",
                "",
                "",
                "",
                [process],
            ),
            RunRecord(
                "idle-owned",
                "session",
                "lease",
                "RUNNING_IDLE",
                "",
                "",
                "",
                "",
                [process],
            ),
            RunRecord(
                "idle-empty", None, None, "RUNNING_IDLE", "", "", "", "", []
            ),
        )
        for run in invalid:
            with self.subTest(invalid=run.run_id):
                with self.assertRaisesRegex(ValueError, "invalid_run_record"):
                    run.validate()


if __name__ == "__main__":
    unittest.main()
