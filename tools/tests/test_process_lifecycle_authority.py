"""Process lifecycle under lease authority: runs, manifests, guards and settlement.

Moved verbatim from test_task7_review_regressions.py, test_task7_rereview_regressions.py
(review 2026-09-25, W4d step 3).
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from _session_coordination.process_guard_gate import validate_result_shape
from dayz_mcp import daemon, loopback, native_process_guard, orphan_guard
from dayz_mcp.native_process_guard import NativeProcessGuard
from dayz_mcp.process_lifecycle import RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import ServerConfig
from tests.fence_helpers import bind_both_peers
from tests.lifecycle_helpers import (
    _wait_for_dayz_mcp_background_workers,
    Guard,
    HASH_B,
    IDENTITY,
    identity,
    LifecycleFixture,
    LifecycleFixtureContext,
    record,
)
from tests._tiers import slow_test


# --- from test_task7_review_regressions.py ---


class ProcessGuardGateContractTest(unittest.TestCase):
    def test_durable_gate_result_shape_is_regression_checked_without_running_processes(self) -> None:
        missing = {
            "terminated": False,
            "error": "invalid_expected_identity",
            "exit_code": 3,
            "pid": None,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": False,
        }
        mismatch = {
            "terminated": False,
            "error": "process_identity_mismatch",
            "exit_code": 4,
            "pid": 102,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }
        payload = {
            "schema_version": 1,
            "gate": "task7_process_guard_registered_vs_foreign",
            "passed": True,
            "steps": {
                "registered_snapshot": {
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "foreign_snapshot": {
                    "pid": 102,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "missing_field_rejections": {
                    field: dict(missing)
                    for field in (
                        "pid",
                        "creation_time_utc",
                        "executable_sha256",
                        "command_line_sha256",
                    )
                },
                "forged_identity_rejections": {
                    field: dict(mismatch)
                    for field in (
                        "pid",
                        "creation_time_utc",
                        "executable_sha256",
                        "command_line_sha256",
                    )
                },
                "registered_termination": {
                    "terminated": True,
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "terminated_identity_recheck": {
                    "terminated": False,
                    "error": "process_not_found",
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": False,
                    "exit_code": 4,
                },
                "foreign_alive_after_rejections": True,
                "foreign_exact_cleanup": {
                    "terminated": True,
                    "pid": 102,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
            },
            "children": [
                {"slot": "registered", "pid": 101, "final_state": "exited"},
                {"slot": "foreign", "pid": 102, "final_state": "exited"},
            ],
            "errors": [],
        }
        self.assertEqual(validate_result_shape(payload), [])
        payload["steps"]["forged_identity_rejections"]["command_line_sha256"] = {
            "terminated": True,
            "exit_code": 0,
        }
        self.assertIn("forged_identity_contract", validate_result_shape(payload))

    def test_guard_source_closes_process_objects_and_gate_has_no_generic_kill_primitive(self) -> None:
        guard_source = (_TOOLS_DIR / "process-guard.ps1").read_text(encoding="utf-8")
        gate_source = (
            _TOOLS_DIR / "_session_coordination" / "process_guard_gate.py"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(guard_source.count("$proc.Dispose()"), 2)
        self.assertNotIn("taskkill", gate_source.casefold())
        self.assertNotIn("stop-process", gate_source.casefold())
        self.assertNotIn(".kill(", gate_source.casefold())


class LifecyclePostAuditQuarantineTest(unittest.TestCase):
    def _switch_probe_on(self, fixture: LifecycleFixture, event_name: str) -> None:
        def change(event):
            if event.get("event") == event_name:
                fixture.probe = {
                    "known": True,
                    "processes": [{"pid": 77, "name": "DayZ_x64.exe"}],
                }

        fixture.audit.on_event = change

    def test_retail_appearing_during_start_audit_blocks_popen_and_manifest(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[expected.pid] = identity(expected)
            self._switch_probe_on(fixture, "lifecycle_start")
            result = fixture.lifecycle.start_run(IDENTITY, fixture.token, fixture.request())
            self.assertEqual(result["error"], "retail_quarantine")
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.store.list_runs(), [])
        finally:
            fixture.close()

    def test_retail_appearing_during_stop_audit_blocks_manifest_and_guard(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(7201)
            fixture.add_run(expected)
            fixture.guard.snapshots[expected.pid] = identity(expected)
            self._switch_probe_on(fixture, "lifecycle_stop")
            result = fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(result["error"], "retail_quarantine")
            self.assertEqual(fixture.guard.terminate_calls, [])
            self.assertEqual(fixture.store.get("existing").state, "RUNNING")
        finally:
            fixture.close()

    def test_retail_appearing_during_adopt_audit_blocks_manifest(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(7202)
            fixture.add_run(expected, state="RUNNING_IDLE", owned=False)
            fixture.guard.snapshots[expected.pid] = identity(expected)
            self._switch_probe_on(fixture, "lifecycle_adopt")
            result = fixture.lifecycle.adopt_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(result["error"], "retail_quarantine")
            self.assertEqual(fixture.store.get("existing").state, "RUNNING_IDLE")
        finally:
            fixture.close()

    def test_retail_appearing_during_admin_audit_blocks_manifest(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(7203)
            fixture.add_run(expected, state="UNRECONCILED")
            fixture.guard.snapshots[expected.pid] = identity(expected)
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [
                    {"pid": expected.pid, "name": "DayZDiag_x64.exe"}
                ],
            }
            self._switch_probe_on(fixture, "admin_reconcile")
            result = fixture.lifecycle.admin_reconcile("existing", expected.pid, "incident")
            self.assertEqual(result["error"], "retail_quarantine")
            self.assertEqual(fixture.store.get("existing").state, "UNRECONCILED")
        finally:
            fixture.close()


class RunStateRecoveryTest(unittest.TestCase):
    def test_supplied_unknown_or_exited_run_id_never_launches(self) -> None:
        for mode in ("unknown", "exited"):
            with self.subTest(mode=mode):
                fixture = LifecycleFixture()
                try:
                    supplied = "missing"
                    if mode == "exited":
                        supplied = "exited"
                        fixture.store.add(
                            RunRecord(
                                supplied,
                                None,
                                None,
                                "EXITED",
                                "red",
                                "@SameMod",
                                "profiles",
                                "mission",
                                [],
                            )
                        )
                    expected = record(fixture.launcher.handle.pid)
                    fixture.guard.snapshots[expected.pid] = identity(expected)
                    result = fixture.lifecycle.start_run(
                        IDENTITY, fixture.token, fixture.request(run_id=supplied)
                    )
                    self.assertNotIn("ok", result)
                    self.assertEqual(fixture.launcher.calls, [])
                finally:
                    fixture.close()

    def test_confirmed_handle_exit_after_snapshot_failure_persists_exited(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            result = fixture.lifecycle.start_run(IDENTITY, fixture.token, fixture.request())
            self.assertIn(result["error"], {"identity_unavailable", "manual_cleanup_required"})
            run = fixture.store.list_runs()[0]
            self.assertEqual((run.state, run.owner_session_id, run.owner_lease_id), ("EXITED", None, None))
        finally:
            fixture.close()

    def test_unconfirmed_handle_exit_requires_manual_cleanup(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=False)
        try:
            result = fixture.lifecycle.start_run(IDENTITY, fixture.token, fixture.request())
            self.assertEqual(result["error"], "manual_cleanup_required")
            run = fixture.store.list_runs()[0]
            self.assertEqual((run.state, run.processes), ("UNRECONCILED", []))
        finally:
            fixture.close()

    def test_partial_stop_persists_only_survivors(self) -> None:
        fixture = LifecycleFixture()
        try:
            first, second = record(7302), record(7303)
            run = fixture.add_run(first)
            run.processes.append(second)
            fixture.store.replace(run)
            fixture.guard.snapshots = {first.pid: identity(first), second.pid: identity(second)}
            fixture.guard.terminate_results = [
                {"terminated": True, "exit_code": 0},
                {"terminated": False, "error": "process_identity_mismatch", "exit_code": 4},
            ]
            result = fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(result["error"], "partial_cleanup")
            self.assertEqual(fixture.store.get("existing").processes, [second])
        finally:
            fixture.close()

    def test_reconcile_prunes_only_process_not_found_and_keeps_exact_survivor(self) -> None:
        fixture = LifecycleFixture()
        try:
            gone, survivor = record(7304), record(7305)
            run = fixture.add_run(gone, state="UNRECONCILED")
            run.processes.append(survivor)
            fixture.store.replace(run)
            fixture.guard.snapshots[gone.pid] = {"error": "process_not_found", "exit_code": 4}
            fixture.guard.snapshots[survivor.pid] = identity(survivor)
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [
                    {"pid": survivor.pid, "name": "DayZDiag_x64.exe"}
                ],
            }
            result = fixture.lifecycle.admin_reconcile("existing", survivor.pid, "incident")
            self.assertEqual(result["state"], "RUNNING_IDLE")
            self.assertEqual(fixture.store.get("existing").processes, [survivor])
        finally:
            fixture.close()

    def test_reconcile_all_registered_processes_not_found_exits_run(self) -> None:
        fixture = LifecycleFixture()
        try:
            gone = record(7307)
            fixture.add_run(gone, state="UNRECONCILED")
            fixture.guard.snapshots[gone.pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
            result = fixture.lifecycle.admin_reconcile("existing", gone.pid, "incident")
            self.assertEqual(result["state"], "EXITED")
            run = fixture.store.get("existing")
            self.assertEqual((run.state, run.processes), ("EXITED", []))
        finally:
            fixture.close()

    def test_empty_unreconciled_requires_explicit_empty_mode_and_known_zero_diag(self) -> None:
        fixture = LifecycleFixture()
        try:
            empty = RunRecord(
                "empty",
                IDENTITY.session_id,
                fixture.lease_id,
                "UNRECONCILED",
                "red",
                "@SameMod",
                "profiles",
                "mission",
                [],
            )
            fixture.store.add(empty)
            fixture.lifecycle.diag_probe = lambda: {"known": True, "processes": []}
            result = fixture.lifecycle.admin_reconcile(
                "empty", None, "confirmed empty", empty=True
            )
            self.assertEqual(result["state"], "EXITED")
            run = fixture.store.get("empty")
            self.assertEqual((run.state, run.owner_session_id, run.owner_lease_id), ("EXITED", None, None))
        finally:
            fixture.close()

    def test_empty_reconcile_unknown_or_present_diag_preserves_bytes(self) -> None:
        for probe in (
            {"known": False, "processes": []},
            {"known": True, "processes": [{"pid": 88, "name": "DayZDiag_x64.exe"}]},
        ):
            with self.subTest(probe=probe), LifecycleFixtureContext() as fixture:
                empty = RunRecord(
                    "empty",
                    IDENTITY.session_id,
                    fixture.lease_id,
                    "UNRECONCILED",
                    "red",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [],
                )
                fixture.store.add(empty)
                before = fixture.paths.runs_path.read_bytes()
                fixture.lifecycle.diag_probe = lambda probe=probe: probe
                result = fixture.lifecycle.admin_reconcile(
                    "empty", None, "confirmed empty", empty=True
                )
                self.assertNotIn("reconciled", result)
                self.assertEqual(fixture.paths.runs_path.read_bytes(), before)

    def test_reconcile_unknown_or_mismatch_preserves_manifest_bytes(self) -> None:
        for response in (
            {"error": "guard_unavailable", "exit_code": 3},
            {"error": "process_not_found", "exit_code": 3},
            identity(record(7306)) | {"creation_time_utc": "different"},
        ):
            with self.subTest(response=response.get("error", "mismatch")):
                fixture = LifecycleFixture()
                try:
                    expected = record(7306)
                    fixture.add_run(expected, state="UNRECONCILED")
                    before = fixture.paths.runs_path.read_bytes()
                    fixture.guard.snapshots[expected.pid] = response
                    result = fixture.lifecycle.admin_reconcile("existing", expected.pid, "incident")
                    self.assertNotIn("reconciled", result)
                    self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
                finally:
                    fixture.close()


class RestartAndManifestTest(unittest.TestCase):
    def test_activation_releases_previous_generation_running_owner_without_guard(self) -> None:
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            store.add(
                RunRecord(
                    "persisted",
                    "old-session",
                    "old-lease",
                    "RUNNING",
                    "red",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [record(7401)],
                )
            )
            state = loopback.ServerState("key")
            bind_both_peers(state)
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                daemon._activate_server_coordination(state, "new-generation")
            run = state.lifecycle.manifest.get("persisted")
            self.assertEqual((run.state, run.owner_session_id, run.owner_lease_id), ("RUNNING_IDLE", None, None))

    def test_restart_release_audit_failure_aborts_activation_without_manifest_change(self) -> None:
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            store.add(
                RunRecord(
                    "persisted",
                    "old-session",
                    "old-lease",
                    "RUNNING",
                    "red",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [record(7403)],
                )
            )
            before = paths.runs_path.read_bytes()
            state = loopback.ServerState("key")
            bind_both_peers(state)
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": temporary}),
                patch.object(daemon.JsonlAuditWriter, "write", return_value=False),
                self.assertRaisesRegex(RuntimeError, "restart.*audit"),
            ):
                daemon._activate_server_coordination(state, "new-generation")
            self.assertEqual(paths.runs_path.read_bytes(), before)

    def test_restart_recovery_touches_only_owned_running_and_interrupted_runs(self) -> None:
        """fb-20260820-110945-8625: the scope rule of recover_after_restart.

        daemon.py calls it at every start. It releases a RUNNING run that has
        an owner and quarantines STARTING/STOPPING, and nothing else. An
        UNRECONCILED run can keep its owner (_settle_failed_launch when the
        launch handle could not be confirmed closed); widening the release
        branch would turn it into an adoptable RUNNING_IDLE, widening the
        quarantine branch would strip a run that is idle or already exited.
        Every other row must leave the restart as it entered, on disk too.
        """
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            rows = [
                RunRecord("owned-running", "s", "l", "RUNNING", "", "", "", "", [record(8601)]),
                RunRecord("idle", None, None, "RUNNING_IDLE", "", "", "", "", [record(8602)]),
                RunRecord(
                    "unreconciled-owned", "s", "l", "UNRECONCILED", "", "", "", "", [record(8603)]
                ),
                RunRecord("unreconciled", None, None, "UNRECONCILED", "", "", "", "", [record(8604)]),
                RunRecord("exited", None, None, "EXITED", "", "", "", "", []),
                RunRecord("starting", "s", "l", "STARTING", "", "", "", "", [record(8605)]),
                RunRecord("stopping", None, None, "STOPPING", "", "", "", "", [record(8606)]),
            ]
            for row in rows:
                store.add(row)
            untouched = {
                row.run_id: row
                for row in rows
                if row.run_id in {"idle", "unreconciled-owned", "unreconciled", "exited"}
            }

            def durable_rows() -> dict[str, object]:
                payload = json.loads(paths.runs_path.read_text(encoding="utf-8"))
                return {item["run_id"]: item for item in payload["runs"]}

            durable_before = durable_rows()

            changed = store.recover_after_restart()

            self.assertEqual(
                changed,
                {"released": ["owned-running"], "unreconciled": ["starting", "stopping"]},
            )
            durable_after = durable_rows()
            for run_id, original in untouched.items():
                with self.subTest(run_id=run_id):
                    self.assertEqual(store.get(run_id), original)
                    self.assertEqual(durable_after[run_id], durable_before[run_id])
            released = store.get("owned-running")
            self.assertEqual(
                (released.state, released.owner_session_id, released.owner_lease_id),
                ("RUNNING_IDLE", None, None),
            )
            for run_id in ("starting", "stopping"):
                with self.subTest(run_id=run_id):
                    quarantined = store.get(run_id)
                    self.assertEqual(
                        (
                            quarantined.state,
                            quarantined.owner_session_id,
                            quarantined.owner_lease_id,
                        ),
                        ("UNRECONCILED", None, None),
                    )

    def test_corrupt_version_duplicate_and_invalid_strong_record_are_rejected(self) -> None:
        valid = {
            "run_id": "r",
            "owner_session_id": None,
            "owner_lease_id": None,
            "state": "EXITED",
            "label": "red",
            "mod": "@M",
            "profiles": "p",
            "mission": "m",
            "processes": [],
        }
        payloads: list[object] = [
            "{",
            {"version": 2, "runs": []},
            {"version": 1, "runs": [valid, valid]},
            {
                "version": 1,
                "runs": [valid | {"processes": [{
                    "pid": 1,
                    "creation_time_utc": "now",
                    "executable_sha256": "short",
                    "command_line_sha256": HASH_B,
                    "role": "client",
                }]}],
            },
        ]
        for payload in payloads:
            with self.subTest(payload=type(payload).__name__), TemporaryDirectory() as temporary:
                paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
                paths.runs_path.parent.mkdir(parents=True)
                if isinstance(payload, str):
                    paths.runs_path.write_text(payload, encoding="utf-8")
                else:
                    paths.runs_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "invalid_run_manifest"):
                    RunManifestStore(paths)

    def test_add_replace_and_release_owner_roll_back_memory_and_disk(self) -> None:
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            original = RunRecord(
                "original", "owner", "lease", "RUNNING", "red", "@M", "p", "m", [record(7402)]
            )
            store.add(original)
            before_bytes = paths.runs_path.read_bytes()
            before_runs = store.list_runs()
            with patch.object(store, "_persist_locked", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store.add(RunRecord("new", None, None, "EXITED", "", "", "", "", []))
                replacement = store.get("original")
                replacement.label = "changed"
                with self.assertRaises(OSError):
                    store.replace(replacement)
                with self.assertRaises(OSError):
                    store.release_owner("owner", "lease")
            self.assertEqual(store.list_runs(), before_runs)
            self.assertEqual(paths.runs_path.read_bytes(), before_bytes)


class DaemonReleaseWiringTest(unittest.TestCase):
    @slow_test
    def test_owner_admin_and_expiry_release_runs_idle_without_guard_in_clean_or_quarantine(self) -> None:
        for trigger in ("owner", "admin", "expiry"):
            for quarantined in (False, True):
                with self.subTest(trigger=trigger, quarantined=quarantined), TemporaryDirectory() as temporary:
                    config = ServerConfig(
                        mode="daemon",
                        key="key",
                        port=0,
                        log_sink=lambda _message: None,
                    )
                    with patch.dict(os.environ, {"LOCALAPPDATA": temporary}), patch.object(
                        daemon, "_ensure_identity_migration", return_value=None
                    ):
                        state = daemon.build_server_state(
                            config,
                            "key",
                            daemon_generation=f"{trigger}-{quarantined}",
                            activate_coordination=True,
                        )
                    probe = (
                        {"known": False, "processes": []}
                        if quarantined
                        else {"known": True, "processes": []}
                    )
                    state.retail_probe = lambda probe=probe: probe
                    state.lifecycle.retail_probe = lambda probe=probe: probe
                    guard = Guard()
                    state.lifecycle.guard = guard
                    status, acquired = state.coordination.acquire(IDENTITY, trigger)
                    self.assertEqual(status, 200)
                    state.lifecycle.manifest.add(
                        RunRecord(
                            "managed",
                            IDENTITY.session_id,
                            acquired["lease_id"],
                            "RUNNING",
                            "red",
                            "@SameMod",
                            "profiles",
                            "mission",
                            [record(7701)],
                        )
                    )
                    with state.coordination._condition:
                        state.coordination._active.vehicle_active = True
                    if trigger == "owner":
                        _, response = state.coordination.release(
                            IDENTITY, acquired["lease_token"]
                        )
                    elif trigger == "admin":
                        _, response = state.coordination.admin_release(
                            acquired["lease_id"], "incident"
                        )
                    else:
                        with state.coordination._condition:
                            state.coordination._active.expires_at = -1.0
                        response = state.coordination.status(IDENTITY)
                    idle = state.lifecycle.manifest.get("managed")
                    self.assertEqual(
                        (idle.state, idle.owner_session_id, idle.owner_lease_id),
                        ("RUNNING_IDLE", None, None),
                    )
                    self.assertEqual(guard.snapshot_calls, [])
                    self.assertEqual(guard.terminate_calls, [])
                    degraded = response.get("cleanup_degraded", [])
                    self.assertEqual(
                        "retail_quarantine" in degraded,
                        quarantined,
                    )
                    with state.coordination._condition:
                        terminalized = state.coordination._condition.wait_for(
                            lambda: (
                                not state.coordination._handoff_pending
                                and state.coordination._cleanup_worker_active == 0
                            ),
                            timeout=2.0,
                        )
                    self.assertTrue(terminalized)
                    # _persist_snapshot_locked releases the condition lock while
                    # writing coordination.json (atomic .tmp). handoff_pending can
                    # therefore clear mid-write; join release-audit/cleanup workers
                    # so TemporaryDirectory does not hit WinError 32/145 on the tmp.
                    # If workers outlive the deadline, fail explicitly — never let
                    # tempfile.WinError 145 be the first signal.
                    _wait_for_dayz_mcp_background_workers(timeout_s=2.0)
                    state.root_writer_lease.release()


# --- from test_task7_rereview_regressions.py ---


class FailedLaunchSettlementTest(unittest.TestCase):
    def test_failed_extension_with_confirmed_exit_restores_previous_bytes_and_processes(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            first, second = record(8301), record(8302)
            run = fixture.add_run(first)
            run.processes.append(second)
            fixture.store.replace(run)
            before = fixture.paths.runs_path.read_bytes()
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request(run_id="existing")
            )
            self.assertEqual(result["error"], "identity_unavailable")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
            restored = fixture.store.get("existing")
            self.assertEqual(
                (restored.state, restored.owner_session_id, restored.owner_lease_id),
                ("RUNNING", IDENTITY.session_id, fixture.lease_id),
            )
            self.assertEqual(restored.processes, [first, second])
        finally:
            fixture.close()

    def test_guard_exception_and_unconfirmed_handle_leave_durable_manual_cleanup(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=False)
        try:
            def fail_snapshot(_pid):
                raise TimeoutError("guard timeout")

            fixture.guard.snapshot = fail_snapshot  # type: ignore[method-assign]
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )
            self.assertEqual(result["error"], "manual_cleanup_required")
            self.assertIn(result["state"], {"STARTING", "UNRECONCILED"})
            run = fixture.store.get(result["run_id"])
            self.assertIsNotNone(run)
            self.assertIn(run.state, {"STARTING", "UNRECONCILED"})
        finally:
            fixture.close()

    def test_final_new_run_write_failure_with_strong_identity_keeps_recoverable_record(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=False)
        try:
            strong = record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[strong.pid] = identity(strong)
            original_replace = fixture.store.replace
            calls = 0

            def fail_once(run):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("disk full")
                return original_replace(run)

            fixture.store.replace = fail_once  # type: ignore[method-assign]
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )
            self.assertEqual(result.get("error"), "manual_cleanup_required")
            recovered = fixture.store.get(result.get("run_id", ""))
            self.assertEqual(recovered.state, "UNRECONCILED")
            self.assertEqual(recovered.processes, [strong])
            self.assertIn("manifest_failed", result["cleanup_degraded"])
        finally:
            fixture.close()

    def test_extension_final_write_failure_and_confirmed_exit_restores_exact_previous_run(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            existing = record(8303)
            fixture.add_run(existing)
            before = fixture.paths.runs_path.read_bytes()
            strong = record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[strong.pid] = identity(strong)
            original_replace = fixture.store.replace
            calls = 0

            def fail_second(run):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disk full")
                return original_replace(run)

            fixture.store.replace = fail_second  # type: ignore[method-assign]
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request(run_id="existing")
            )
            self.assertEqual(result.get("error"), "lifecycle_start_failed")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
            self.assertEqual(fixture.store.get("existing").processes, [existing])
        finally:
            fixture.close()

    def _assert_failed_extension_restores_previous(self, request, first, second, before, fixture) -> None:
        result = fixture.lifecycle.start_run(IDENTITY, fixture.token, request)
        self.assertEqual(result["error"], "identity_unavailable", result)
        self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
        restored = fixture.store.get("existing")
        self.assertEqual(
            (restored.state, restored.owner_session_id, restored.owner_lease_id),
            ("RUNNING", IDENTITY.session_id, fixture.lease_id),
        )
        self.assertEqual(restored.processes, [first, second])

    def _assert_extension_final_write_restores_previous(self, request, existing, before, fixture) -> None:
        result = fixture.lifecycle.start_run(IDENTITY, fixture.token, request)
        self.assertEqual(result.get("error"), "lifecycle_start_failed", result)
        self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
        self.assertEqual(fixture.store.get("existing").processes, [existing])

    def test_failed_extension_settlement_survives_wall_clock_jitter(self) -> None:
        # time.time() is not monotonic. A 1ms step back is already
        # replace_witness_stale (negative decision age); 61s past the stamp
        # trips the published bound. Either way the gate runs instead of
        # settlement, so the clock both sides of the comparison must be pinned.
        for label, delta in (("step_back", -0.001), ("past_bound", 61.0)):
            with self.subTest(clock=label):
                fixture = LifecycleFixture(confirmed_exit=True)
                try:
                    first, second = record(8301), record(8302)
                    run = fixture.add_run(first)
                    run.processes.append(second)
                    fixture.store.replace(run)
                    before = fixture.paths.runs_path.read_bytes()
                    request = fixture.request(run_id="existing")
                    jumped = request["replace_if_not_polling_since"] / 1000.0 + delta
                    with patch("time.time", return_value=jumped):
                        self._assert_failed_extension_restores_previous(
                            request, first, second, before, fixture
                        )
                finally:
                    fixture.close()

    def test_extension_final_write_failure_survives_wall_clock_jitter(self) -> None:
        # Same jitter as the sibling: fail_second turns a stale-witness
        # rollback into manual_cleanup_required, which is how this test
        # reported the flake even though the gate fired first.
        for label, delta in (("step_back", -0.001), ("past_bound", 61.0)):
            with self.subTest(clock=label):
                fixture = LifecycleFixture(confirmed_exit=True)
                try:
                    existing = record(8303)
                    fixture.add_run(existing)
                    before = fixture.paths.runs_path.read_bytes()
                    strong = record(fixture.launcher.handle.pid)
                    fixture.guard.snapshots[strong.pid] = identity(strong)
                    original_replace = fixture.store.replace
                    calls = 0

                    def fail_second(run):
                        nonlocal calls
                        calls += 1
                        if calls == 2:
                            raise OSError("disk full")
                        return original_replace(run)

                    fixture.store.replace = fail_second  # type: ignore[method-assign]
                    request = fixture.request(run_id="existing")
                    jumped = request["replace_if_not_polling_since"] / 1000.0 + delta
                    with patch("time.time", return_value=jumped):
                        self._assert_extension_final_write_restores_previous(
                            request, existing, before, fixture
                        )
                finally:
                    fixture.close()

    def test_new_run_terminal_settlement_still_manual_when_wall_clock_jumps(self) -> None:
        # Negative control: a brand-new run never stamps a witness. Clock
        # jitter must not change the durable STARTING / manual cleanup path.
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            fixture.store.replace = (  # type: ignore[method-assign]
                lambda _run: (_ for _ in ()).throw(OSError("disk full"))
            )
            request = fixture.request()
            jumped = time.time() - 0.001
            with patch("time.time", return_value=jumped):
                result = fixture.lifecycle.start_run(
                    IDENTITY, fixture.token, request
                )
            self.assertEqual(result["error"], "manual_cleanup_required")
            self.assertEqual(result["state"], "STARTING")
            self.assertIn("manifest_failed", result["cleanup_degraded"])
            self.assertEqual(fixture.store.get(result["run_id"]).state, "STARTING")
        finally:
            fixture.close()

    def test_extension_true_witness_is_refused_without_launch(self) -> None:
        # Clock freeze still has to call the real validator. The positives
        # stamp an admissible witness; a new run never enters this gate.
        # True is not an int (type(True) is bool): the product must refuse
        # with replace_witness_missing and never launch or terminate. If the
        # frozen wrapper is stubbed to return None, start_run proceeds and
        # this test fails (identity_unavailable + a launcher call).
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            existing = record(8310)
            fixture.add_run(existing)
            request = fixture.request(run_id="existing")
            request["replace_if_not_polling_since"] = True
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, request
            )
            self.assertEqual(result["error"], "replace_witness_missing", result)
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.guard.terminate_calls, [])
            self.assertEqual(fixture.store.get("existing").processes, [existing])
        finally:
            fixture.close()

    def test_failed_terminal_settlement_leaves_starting_and_requires_manual_cleanup(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            fixture.store.replace = (  # type: ignore[method-assign]
                lambda _run: (_ for _ in ()).throw(OSError("disk full"))
            )
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )
            self.assertEqual(result["error"], "manual_cleanup_required")
            self.assertEqual(result["state"], "STARTING")
            self.assertIn("manifest_failed", result["cleanup_degraded"])
            self.assertEqual(fixture.store.get(result["run_id"]).state, "STARTING")
        finally:
            fixture.close()


class LifecycleRecoveryAndOutcomeTest(unittest.TestCase):
    def test_stop_preflight_process_not_found_exits_without_terminate(self) -> None:
        fixture = LifecycleFixture()
        try:
            gone = record(8401)
            fixture.add_run(gone)
            fixture.guard.snapshots[gone.pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
            result = fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(
                result,
                {
                    "ok": True,
                    "run_id": "existing",
                    "state": "EXITED",
                    "terminated": 0,
                    "stop_method": "no_live_owned",
                    "exit_metrics_valid": False,
                },
            )
            self.assertEqual(fixture.guard.terminate_calls, [])
            self.assertEqual(fixture.store.get("existing").state, "EXITED")
        finally:
            fixture.close()

    def test_stop_guard_exception_becomes_unreconciled_and_audited(self) -> None:
        fixture = LifecycleFixture()
        try:
            process = record(8402)
            fixture.add_run(process)
            fixture.guard.snapshots[process.pid] = identity(process)

            def fail_terminate(_record):
                raise TimeoutError("guard timeout")

            fixture.guard.terminate = fail_terminate  # type: ignore[method-assign]
            try:
                result = fixture.lifecycle.stop_run(
                    IDENTITY, fixture.token, "existing"
                )
            except Exception as exc:  # pragma: no cover - RED diagnostic
                self.fail(f"guard_exception_escaped:{type(exc).__name__}")
            self.assertEqual(result["error"], "partial_cleanup")
            self.assertEqual(fixture.store.get("existing").state, "UNRECONCILED")
            terminal = [
                event
                for event in fixture.audit.events
                if event.get("event") == "lifecycle_stop_outcome"
            ]
            self.assertEqual(terminal[-1]["decision"], "partial_cleanup")
        finally:
            fixture.close()

    def test_stopping_after_final_write_failure_remains_admin_reconcilable(self) -> None:
        fixture = LifecycleFixture()
        try:
            process = record(8403)
            fixture.add_run(process)
            fixture.guard.snapshots[process.pid] = identity(process)
            original_replace = fixture.store.replace
            calls = 0

            def fail_second(run):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disk full")
                return original_replace(run)

            fixture.store.replace = fail_second  # type: ignore[method-assign]
            result = fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(result["error"], "partial_cleanup")
            self.assertEqual(fixture.store.get("existing").state, "STOPPING")
            fixture.store.replace = original_replace  # type: ignore[method-assign]
            fixture.guard.snapshots[process.pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
            fixture.lifecycle.diag_probe = lambda: {"known": True, "processes": []}
            reconciled = fixture.lifecycle.admin_reconcile(
                "existing", process.pid, "write recovered"
            )
            self.assertEqual(reconciled.get("state"), "EXITED")
        finally:
            fixture.close()

    def test_restart_batch_recovers_transient_states_and_releases_running(self) -> None:
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            store.add(
                RunRecord("run", "s", "l", "RUNNING", "", "", "", "", [record(8501)])
            )
            store.add(
                RunRecord("start", "s", "l", "STARTING", "", "", "", "", [record(8502)])
            )
            store.add(
                RunRecord("stop", "s", "l", "STOPPING", "", "", "", "", [record(8503)])
            )
            if not hasattr(store, "recover_after_restart"):
                self.fail("recover_after_restart_missing")
            changed = store.recover_after_restart()
            self.assertEqual(changed["released"], ["run"])
            self.assertEqual(changed["unreconciled"], ["start", "stop"])
            self.assertEqual(store.get("run").state, "RUNNING_IDLE")
            self.assertEqual(store.get("start").state, "UNRECONCILED")
            self.assertEqual(store.get("stop").state, "UNRECONCILED")
            self.assertIsNone(store.get("start").owner_session_id)
            self.assertIsNone(store.get("stop").owner_lease_id)

    def test_nonempty_reconcile_rejects_unregistered_diag_pid_without_manifest_change(self) -> None:
        fixture = LifecycleFixture()
        try:
            survivor = record(8504)
            fixture.add_run(survivor, state="UNRECONCILED")
            fixture.guard.snapshots[survivor.pid] = identity(survivor)
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [
                    {"pid": survivor.pid, "name": "DayZDiag_x64.exe"},
                    {"pid": 99991, "name": "DayZDiag_x64.exe"},
                ],
            }
            before = fixture.paths.runs_path.read_bytes()
            result = fixture.lifecycle.admin_reconcile(
                "existing", survivor.pid, "manual"
            )
            self.assertEqual(result.get("error"), "manual_cleanup_required")
            self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
        finally:
            fixture.close()

    def test_manual_cleanup_terminal_audit_is_redacted_and_failure_is_surfaced(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=False)
        try:
            fixture.audit.fail_events.add("lifecycle_start_outcome")
            result = fixture.lifecycle.start_run(
                IDENTITY, fixture.token, fixture.request()
            )
            self.assertEqual(result["error"], "manual_cleanup_required")
            self.assertIn("audit_failed", result.get("cleanup_degraded", []))
            terminal = [
                event
                for event in fixture.audit.events
                if event.get("event") == "lifecycle_start_outcome"
            ][-1]
            self.assertEqual(terminal["decision"], "manual_cleanup_required")
            self.assertNotIn("argv", terminal)
            self.assertNotIn("lease_token", terminal)
            self.assertNotIn("command_line", str(terminal).casefold())
        finally:
            fixture.close()


class GuardFailureNormalizationTest(unittest.TestCase):
    def test_native_guard_timeout_and_spawn_error_are_normalized_fail_closed(self) -> None:
        def unavailable_process(_pid: int) -> object:
            raise OSError("native provider unavailable")

        with patch.object(
            native_process_guard,
            "psutil",
            SimpleNamespace(Process=unavailable_process),
        ):
            try:
                result = NativeProcessGuard().snapshot(123)
            except Exception as exc:  # pragma: no cover - RED diagnostic
                self.fail(f"guard_exception_escaped:{type(exc).__name__}")
        self.assertEqual(result["error"], "identity_unavailable")
        self.assertEqual(result["exit_code"], 3)


class GateEvidenceContractTest(unittest.TestCase):
    @staticmethod
    def _payload() -> dict[str, object]:
        missing = {
            "terminated": False,
            "error": "invalid_expected_identity",
            "exit_code": 3,
            "pid": None,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": False,
        }
        mismatch = {
            "terminated": False,
            "error": "process_identity_mismatch",
            "exit_code": 4,
            "pid": 102,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }
        return {
            "schema_version": 1,
            "gate": "task7_process_guard_registered_vs_foreign",
            "passed": True,
            "steps": {
                "registered_snapshot": {
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "foreign_snapshot": {
                    "pid": 102,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "missing_field_rejections": {
                    field: dict(missing)
                    for field in (
                        "pid",
                        "creation_time_utc",
                        "executable_sha256",
                        "command_line_sha256",
                    )
                },
                "forged_identity_rejections": {
                    field: dict(mismatch)
                    for field in (
                        "pid",
                        "creation_time_utc",
                        "executable_sha256",
                        "command_line_sha256",
                    )
                },
                "foreign_alive_after_rejections": True,
                "registered_termination": {
                    "terminated": True,
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "terminated_identity_recheck": {
                    "terminated": False,
                    "error": "process_not_found",
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": False,
                    "exit_code": 4,
                },
                "foreign_exact_cleanup": {
                    "terminated": True,
                    "pid": 102,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
            },
            "children": [
                {"slot": "registered", "pid": 101, "final_state": "exited"},
                {"slot": "foreign", "pid": 102, "final_state": "exited"},
            ],
            "errors": [],
        }

    def test_validator_requires_snapshots_forged_pid_and_cross_step_pid_consistency(self) -> None:
        payload = self._payload()
        self.assertEqual(validate_result_shape(payload), [])
        payload["steps"]["registered_snapshot"]["pid"] = 999
        self.assertIn("registered_pid_consistency", validate_result_shape(payload))

    def test_validator_rejects_legacy_or_absent_snapshot_identity_scheme(self) -> None:
        payload = self._payload()
        payload["steps"]["registered_snapshot"].pop("identity_scheme")
        self.assertIn("registered_snapshot", validate_result_shape(payload))

    def test_validator_rejects_snapshot_and_child_pid_or_identity_drift(self) -> None:
        payload = self._payload()
        payload["steps"]["registered_snapshot"]["identity_scheme"] = "legacy-wmi-v1"
        self.assertIn("registered_snapshot", validate_result_shape(payload))

        payload = self._payload()
        payload["steps"]["registered_snapshot"]["identity_complete"] = False
        self.assertIn("registered_snapshot", validate_result_shape(payload))

        payload = self._payload()
        payload["children"][0]["pid"] = True
        self.assertIn("child_final_state", validate_result_shape(payload))

        payload = self._payload()
        payload["children"][0]["pid"] = 0
        self.assertIn("child_final_state", validate_result_shape(payload))

    def test_validator_rejects_cross_child_pid_alias_after_coherent_foreign_rewrite(self) -> None:
        payload = self._payload()
        registered_pid = payload["children"][0]["pid"]
        payload["children"][1]["pid"] = registered_pid
        payload["steps"]["foreign_snapshot"]["pid"] = registered_pid
        for result in payload["steps"]["forged_identity_rejections"].values():
            result["pid"] = registered_pid
        payload["steps"]["foreign_exact_cleanup"]["pid"] = registered_pid

        self.assertEqual(validate_result_shape(payload), ["child_pid_alias"])

    def test_validator_rejects_missing_rejection_native_contract_drift(self) -> None:
        for field, value in (
            ("identity_scheme", "legacy-wmi-v1"),
            ("identity_complete", True),
            ("pid", 102),
        ):
            with self.subTest(field=field):
                payload = self._payload()
                payload["steps"]["missing_field_rejections"]["pid"][field] = value
                self.assertIn("missing_field_contract", validate_result_shape(payload))

    def test_validator_rejects_forged_rejection_native_contract_drift(self) -> None:
        for field, value in (
            ("identity_scheme", "legacy-wmi-v1"),
            ("identity_complete", False),
            ("pid", 101),
        ):
            with self.subTest(field=field):
                payload = self._payload()
                payload["steps"]["forged_identity_rejections"]["pid"][field] = value
                self.assertIn("forged_identity_contract", validate_result_shape(payload))

    def test_validator_rejects_termination_native_contract_drift(self) -> None:
        for step, mutations in (
            (
                "registered_termination",
                (
                    ("identity_scheme", "legacy-wmi-v1", "registered_termination"),
                    ("identity_complete", False, "registered_termination"),
                    ("pid", 102, "registered_termination_pid"),
                ),
            ),
            (
                "foreign_exact_cleanup",
                (
                    ("identity_scheme", "legacy-wmi-v1", "foreign_exact_cleanup"),
                    ("identity_complete", False, "foreign_exact_cleanup"),
                    ("pid", 101, "foreign_exact_cleanup_pid"),
                ),
            ),
            (
                "terminated_identity_recheck",
                (
                    ("identity_scheme", "legacy-wmi-v1", "terminated_identity_recheck"),
                    ("identity_complete", True, "terminated_identity_recheck"),
                    ("pid", 102, "terminated_identity_recheck"),
                ),
            ),
        ):
            for field, value, expected_error in mutations:
                with self.subTest(step=step, field=field):
                    payload = self._payload()
                    payload["steps"][step][field] = value
                    self.assertIn(expected_error, validate_result_shape(payload))


class ToolHelpCloseFailureTest(unittest.TestCase):
    def test_invalid_handle_and_close_failure_are_unknown_fail_closed(self) -> None:
        class Kernel32:
            def __init__(self, mode: str) -> None:
                self.mode = mode

            def CreateToolhelp32Snapshot(self, _flags, _pid):
                if self.mode == "invalid":
                    return ctypes.c_void_p(-1).value
                return 123

            def Process32First(self, _snapshot, pointer):
                entry = pointer._obj
                entry.th32ProcessID = 1
                entry.szExeFile = b"python.exe"
                return 1

            def Process32Next(self, _snapshot, _pointer):
                ctypes.set_last_error(orphan_guard._ERROR_NO_MORE_FILES)
                return 0

            def CloseHandle(self, _handle):
                return 0 if self.mode == "close" else 1

        for mode in ("invalid", "close"):
            with self.subTest(mode=mode), patch.object(
                orphan_guard, "_IS_WINDOWS", True
            ), patch.object(orphan_guard, "_k32", Kernel32(mode)):
                self.assertEqual(
                    orphan_guard.snapshot_processes_by_name(["DayZDiag_x64.exe"]),
                    {"known": False, "processes": []},
                )


if __name__ == "__main__":
    unittest.main()
