from __future__ import annotations

import json
import hashlib
import dataclasses
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator


IDENTITY_A = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")
IDENTITY_B = ClientIdentity("claude", 22, 2, "2026-07-15T00:00:01Z", "B", "other")
HASH_A = "a" * 64
HASH_B = "b" * 64


class AuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.fail_events: set[str] = set()

    def __call__(self, event: dict[str, object]) -> bool:
        self.events.append(event)
        return event.get("event") not in self.fail_events


class FakeLauncher:
    def __init__(self, pid: int = 9001) -> None:
        self.pid = pid
        self.calls: list[tuple[list[str], str, str]] = []
        self.terminated: list[int] = []
        self.confirmed_exit = True

    def __call__(self, argv: list[str], cwd: str, window_style: str):
        self.calls.append((list(argv), cwd, window_style))
        return self

    def terminate(self) -> None:
        self.terminated.append(self.pid)

    def wait(self, timeout: float) -> None:
        if not self.confirmed_exit:
            raise TimeoutError("still running")
        return

    def poll(self):
        return 0 if self.confirmed_exit else None


class FakeGuard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict[str, object]] = {}
        self.snapshot_calls: list[int] = []
        self.terminate_calls: list[ProcessRecord] = []
        self.terminate_results: list[dict[str, object]] = []

    def snapshot(self, pid: int) -> dict[str, object]:
        self.snapshot_calls.append(pid)
        return dict(self.snapshots.get(pid, {"error": "identity_unavailable"}))

    def terminate(self, record: ProcessRecord) -> dict[str, object]:
        self.terminate_calls.append(record)
        if self.terminate_results:
            return self.terminate_results.pop(0)
        return {"terminated": True}


def process(pid: int, role: str = "client") -> ProcessRecord:
    return ProcessRecord(
        pid,
        f"2026-07-15T00:00:{pid % 60:02d}.0000000Z",
        HASH_A,
        HASH_B,
        role,
        identity_scheme="psutil-argv-v2",
    )


def legacy_process(pid: int, role: str = "client") -> ProcessRecord:
    return ProcessRecord(
        pid,
        f"2026-07-15T00:00:{pid % 60:02d}.0000000Z",
        HASH_A,
        HASH_B,
        role,
    )


def snapshot(record: ProcessRecord) -> dict[str, object]:
    return {
        "pid": record.pid,
        "creation_time_utc": record.creation_time_utc,
        "executable_sha256": record.executable_sha256,
        "command_line_sha256": record.command_line_sha256,
        "identity_scheme": record.identity_scheme,
        "identity_complete": True,
    }


class ProcessRecordIdentitySchemeTest(unittest.TestCase):
    def payload(self) -> dict[str, object]:
        return {
            "pid": 123,
            "creation_time_utc": "2026-07-22T00:00:00.000000Z",
            "executable_sha256": HASH_A,
            "command_line_sha256": HASH_B,
            "role": "daemon",
        }

    def test_missing_scheme_loads_as_legacy_without_rewrite(self) -> None:
        record = ProcessRecord.from_payload(self.payload())

        self.assertEqual(record.identity_scheme, "legacy-wmi-v1")

    def test_explicit_v2_round_trips(self) -> None:
        record = ProcessRecord.from_payload(
            self.payload() | {"identity_scheme": "psutil-argv-v2"}
        )

        self.assertEqual(record.identity_scheme, "psutil-argv-v2")

    def test_unknown_scheme_is_persistent_corruption(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_process_record"):
            ProcessRecord.from_payload(
                self.payload() | {"identity_scheme": "unknown-v9"}
            )

    def test_existing_five_argument_constructor_remains_legacy(self) -> None:
        record = ProcessRecord(
            123,
            "2026-07-22T00:00:00.000000Z",
            HASH_A,
            HASH_B,
            "daemon",
        )

        self.assertEqual(record.identity_scheme, "legacy-wmi-v1")

    def test_record_from_snapshot_requires_explicit_v2_scheme(self) -> None:
        value = self.payload() | {"identity_complete": True}

        self.assertIsNone(ProcessLifecycle._record_from_snapshot(value, "daemon"))
        self.assertIsNone(
            ProcessLifecycle._record_from_snapshot(
                value | {"identity_scheme": "legacy-wmi-v1"},
                "daemon",
            )
        )
        record = ProcessLifecycle._record_from_snapshot(
            value | {"identity_scheme": "psutil-argv-v2"},
            "daemon",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.identity_scheme, "psutil-argv-v2")

    def test_identity_match_includes_scheme(self) -> None:
        record = ProcessRecord.from_payload(
            self.payload() | {"identity_scheme": "psutil-argv-v2"}
        )
        actual = self.payload() | {
            "identity_complete": True,
            "identity_scheme": "psutil-argv-v2",
        }

        self.assertTrue(ProcessLifecycle._identity_matches(record, actual))
        self.assertFalse(
            ProcessLifecycle._identity_matches(
                record,
                actual | {"identity_scheme": "legacy-wmi-v1"},
            )
        )


class ProcessLifecycleStatusPruneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        runtime = self.root / "runtime"
        self.paths = RuntimePaths(
            runtime,
            runtime / "audit",
            runtime / "coordination.json",
            runtime / "runs.json",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, run_id: str, state: str, pid: int | None = None) -> RunRecord:
        owned = state in {"STARTING", "RUNNING", "STOPPING"}
        return RunRecord(
            run_id,
            "A" if owned else None,
            "lease-A" if owned else None,
            state,
            "fixture",
            "@Fixture",
            "profiles",
            "mission",
            [process(pid)] if pid is not None else [],
        )

    def _write_manifest(self, runs: list[RunRecord]) -> bytes:
        self.paths.runs_path.parent.mkdir(parents=True, exist_ok=True)
        raw = (
            json.dumps(
                {
                    "version": 1,
                    "runs": [dataclasses.asdict(run) for run in runs],
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            + b"\n"
        )
        self.paths.runs_path.write_bytes(raw)
        return raw

    def _lifecycle(self, store: RunManifestStore) -> ProcessLifecycle:
        audit = AuditSink()
        coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=audit,
        )
        return ProcessLifecycle(
            coordinator=coordinator,
            manifest=store,
            audit=audit,
            guard=FakeGuard(),
            retail_probe=lambda: {"known": True, "processes": []},
            game_path=self.root,
        )

    def test_public_status_omits_exited_runs_and_counts_them(self) -> None:
        store = RunManifestStore(self.paths)
        for index in range(3):
            store.add(self._run(f"exited-{index}", "EXITED"))
        store.add(self._run("idle", "RUNNING_IDLE", 501))

        result = self._lifecycle(store).public_status()

        self.assertEqual([run["run_id"] for run in result["runs"]], ["idle"])
        self.assertEqual(result["runs_retired"], 3)

    def test_public_status_keeps_starting_and_stopping_runs(self) -> None:
        store = RunManifestStore(self.paths)
        store.add(self._run("starting", "STARTING"))
        store.add(self._run("stopping", "STOPPING", 502))

        result = self._lifecycle(store).public_status()

        self.assertEqual(
            {run["run_id"] for run in result["runs"]},
            {"starting", "stopping"},
        )
        self.assertEqual(result["runs_retired"], 0)

    def test_lifecycle_status_remains_unfiltered(self) -> None:
        store = RunManifestStore(self.paths)
        store.add(self._run("exited", "EXITED"))
        store.add(self._run("idle", "RUNNING_IDLE", 503))

        result = self._lifecycle(store).status(IDENTITY_A)

        self.assertEqual(
            {run["run_id"] for run in result["runs"]},
            {"exited", "idle"},
        )
        self.assertNotIn("runs_retired", result)

    def test_load_prunes_exited_after_backup_and_checkpoints_new_raw(self) -> None:
        original = self._write_manifest(
            [
                self._run("exited", "EXITED"),
                self._run("idle", "RUNNING_IDLE", 504),
            ]
        )
        checkpoints: list[bytes] = []

        store = RunManifestStore(self.paths, checkpoint=checkpoints.append)

        backup = self.paths.runs_path.with_name("runs.json.bak-preprune")
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual([run.run_id for run in store.list_runs()], ["idle"])
        persisted = self.paths.runs_path.read_bytes()
        self.assertEqual(
            [run["run_id"] for run in json.loads(persisted)["runs"]],
            ["idle"],
        )
        self.assertEqual(checkpoints, [persisted])

    def test_second_load_does_not_overwrite_preprune_backup(self) -> None:
        original = self._write_manifest(
            [
                self._run("exited", "EXITED"),
                self._run("idle", "RUNNING_IDLE", 505),
            ]
        )
        RunManifestStore(self.paths)
        backup = self.paths.runs_path.with_name("runs.json.bak-preprune")
        pruned = self.paths.runs_path.read_bytes()

        RunManifestStore(self.paths)

        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(self.paths.runs_path.read_bytes(), pruned)

    def test_backup_failure_leaves_manifest_unpruned_and_startup_available(self) -> None:
        original = self._write_manifest(
            [
                self._run("exited", "EXITED"),
                self._run("idle", "RUNNING_IDLE", 506),
            ]
        )
        checkpoints: list[bytes] = []

        with patch(
            "dayz_mcp.process_lifecycle.os.open",
            side_effect=OSError("backup blocked"),
        ):
            store = RunManifestStore(self.paths, checkpoint=checkpoints.append)

        self.assertEqual(
            [run.run_id for run in store.list_runs()],
            ["exited", "idle"],
        )
        self.assertEqual(self.paths.runs_path.read_bytes(), original)
        self.assertFalse(
            self.paths.runs_path.with_name("runs.json.bak-preprune").exists()
        )
        self.assertEqual(checkpoints, [original])

    def test_read_only_store_leaves_exited_manifest_bytes_intact(self) -> None:
        original = self._write_manifest(
            [
                self._run("exited", "EXITED"),
                self._run("idle", "RUNNING_IDLE", 507),
            ]
        )

        store = RunManifestStore(self.paths, read_only=True)

        self.assertEqual(
            {run.run_id for run in store.list_runs()},
            {"exited", "idle"},
        )
        self.assertEqual(self.paths.runs_path.read_bytes(), original)
        self.assertFalse(
            self.paths.runs_path.with_name("runs.json.bak-preprune").exists()
        )

    def test_read_only_store_rejects_mutations(self) -> None:
        self._write_manifest([self._run("idle", "RUNNING_IDLE", 508)])
        store = RunManifestStore(self.paths, read_only=True)
        before = self.paths.runs_path.read_bytes()

        with self.assertRaisesRegex(RuntimeError, "run_manifest_store_read_only"):
            store.add(self._run("new", "EXITED"))
        with self.assertRaisesRegex(RuntimeError, "run_manifest_store_read_only"):
            store.release_owner("A", "lease-A")
        with self.assertRaisesRegex(RuntimeError, "run_manifest_store_read_only"):
            store._persist_locked()

        self.assertEqual(self.paths.runs_path.read_bytes(), before)

    def test_second_prune_writes_numbered_backup_without_touching_first(self) -> None:
        first_original = self._write_manifest(
            [
                self._run("exited-1", "EXITED"),
                self._run("idle", "RUNNING_IDLE", 509),
            ]
        )
        RunManifestStore(self.paths)
        first_backup = self.paths.runs_path.with_name("runs.json.bak-preprune")
        self.assertEqual(first_backup.read_bytes(), first_original)
        pruned_once = self.paths.runs_path.read_bytes()

        second_original = self._write_manifest(
            [
                self._run("exited-2", "EXITED"),
                self._run("idle", "RUNNING_IDLE", 509),
            ]
        )
        RunManifestStore(self.paths)
        second_backup = self.paths.runs_path.with_name("runs.json.bak-preprune.2")

        self.assertEqual(first_backup.read_bytes(), first_original)
        self.assertEqual(second_backup.read_bytes(), second_original)
        self.assertNotEqual(self.paths.runs_path.read_bytes(), second_original)
        self.assertNotEqual(pruned_once, second_original)

    def test_preprune_backup_slots_exhausted_leaves_manifest_unpruned(self) -> None:
        original = self._write_manifest(
            [
                self._run("exited", "EXITED"),
                self._run("idle", "RUNNING_IDLE", 510),
            ]
        )
        base = self.paths.runs_path.with_name("runs.json.bak-preprune")
        base.write_bytes(b"slot-1\n")
        for index in range(2, 11):
            self.paths.runs_path.with_name(f"runs.json.bak-preprune.{index}").write_bytes(
                f"slot-{index}\n".encode("utf-8")
            )

        store = RunManifestStore(self.paths)

        self.assertEqual(
            [run.run_id for run in store.list_runs()],
            ["exited", "idle"],
        )
        self.assertEqual(self.paths.runs_path.read_bytes(), original)
        self.assertEqual(base.read_bytes(), b"slot-1\n")
        for index in range(2, 11):
            self.assertEqual(
                self.paths.runs_path.with_name(f"runs.json.bak-preprune.{index}").read_bytes(),
                f"slot-{index}\n".encode("utf-8"),
            )


class ProcessLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe", "DayZServer_x64.exe"):
            (self.game / name).write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=self.audit,
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.probe_result: dict[str, object] = {"known": True, "processes": []}
        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: self.probe_result,
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, executable: Path | None = None) -> dict[str, object]:
        exe = executable or (self.game / "DayZDiag_x64.exe")
        return {
            "argv": [str(exe), "-mission=test"],
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
        }

    def recoverable_request(self) -> dict[str, object]:
        request = self.request() | {
            "new_run_id": "11111111-1111-4111-8111-111111111111",
            "launch_operation_id": "22222222-2222-4222-8222-222222222222",
        }
        digest = hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return request | {
            "launch_request_sha256": digest,
        }

    def add_run(self, record: ProcessRecord, *, owner: str | None = "A", state: str = "RUNNING") -> RunRecord:
        run = RunRecord(
            "run-existing",
            owner,
            "lease-A" if owner else None,
            state,
            "same",
            "@SameMod",
            "profiles",
            "mission",
            [record],
        )
        self.store.add(run)
        return run

    def test_legacy_active_run_is_audited_and_durably_quarantined_once(self) -> None:
        self.add_run(legacy_process(77))

        changed = self.store.quarantine_legacy_active(self.audit)
        second = self.store.quarantine_legacy_active(self.audit)

        self.assertEqual(changed, ["run-existing"])
        self.assertEqual(second, [])
        stored = RunManifestStore(self.store.paths).get("run-existing")
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertIsNone(stored.owner_session_id)
        self.assertEqual(
            [event["event"] for event in self.audit.events].count(
                "legacy_identity_quarantined"
            ),
            1,
        )

    def test_legacy_audit_or_persist_failure_leaves_active_run_unchanged(self) -> None:
        self.add_run(legacy_process(78))
        before = self.store.paths.runs_path.read_bytes()
        self.audit.fail_events.add("legacy_identity_quarantined")

        with self.assertRaises(RuntimeError):
            self.store.quarantine_legacy_active(self.audit)

        self.assertEqual(self.store.get("run-existing").state, "RUNNING")
        self.assertEqual(self.store.paths.runs_path.read_bytes(), before)

        self.audit.fail_events.clear()
        with (
            patch.object(self.store, "_persist_locked", side_effect=OSError("disk")),
            self.assertRaises(OSError),
        ):
            self.store.quarantine_legacy_active(self.audit)
        self.assertEqual(self.store.get("run-existing").state, "RUNNING")
        self.assertEqual(self.store.paths.runs_path.read_bytes(), before)

    def test_first_lifecycle_operation_quarantines_legacy_before_guard_access(self) -> None:
        self.add_run(legacy_process(79))

        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")

        self.assertEqual(result["error"], "run_not_adopted")
        self.assertEqual(self.store.get("run-existing").state, "UNRECONCILED")
        self.assertEqual(self.guard.snapshot_calls, [])
        self.assertEqual(self.guard.terminate_calls, [])

    def test_manifest_restart_roundtrip_is_atomic_and_has_no_raw_commandline_or_token(self) -> None:
        self.add_run(process(100))
        reloaded = RunManifestStore(self.store.paths)
        self.assertEqual(reloaded.get("run-existing").processes, [process(100)])
        wire = self.store.paths.runs_path.read_text(encoding="utf-8")
        self.assertNotIn("command_line\"", wire)
        self.assertNotIn("lease_token", wire)
        self.assertFalse(self.store.paths.runs_path.with_name("runs.json.tmp").exists())

    def test_retail_start_is_rejected_before_launch_or_manifest(self) -> None:
        for name in ("DayZ_BE.exe", "dayz_x64.EXE"):
            result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(self.game / name))
            self.assertEqual(result["error"], "retail_manual_lifecycle_required")
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.store.list_runs(), [])
        self.assertEqual(
            [event["event"] for event in self.audit.events].count("lifecycle_start_rejected"),
            2,
        )

    def test_same_basename_outside_game_path_and_server_are_not_allowed(self) -> None:
        outside = self.root / "copy" / "DayZ_BE.exe"
        outside.parent.mkdir()
        outside.write_bytes(b"")
        self.assertEqual(
            self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(outside))["error"],
            "executable_not_allowed",
        )
        self.assertEqual(
            self.lifecycle.start_run(
                IDENTITY_A, self.token_a, self.request(self.game / "DayZServer_x64.exe")
            )["error"],
            "executable_not_allowed",
        )
        self.assertEqual(self.launcher.calls, [])

    def test_rejected_start_audit_failure_is_fail_closed(self) -> None:
        self.audit.fail_events.add("lifecycle_start_rejected")
        result = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.request(self.game / "DayZ_BE.exe")
        )
        self.assertEqual(result["error"], "audit_failed")
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.store.list_runs(), [])

    def test_start_rejects_foreign_diag_with_wire_compatible_error(self) -> None:
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": 777, "name": "DayZDiag_x64.exe"}],
        }

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "active_run_exists")
        rejected = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]
        self.assertEqual(rejected[-1]["reason"], "foreign_diag_process")
        self.assertEqual(self.store.list_runs(), [])

    def test_start_allows_registered_pid_missing_from_diag_snapshot(self) -> None:
        registered = process(701)
        self.add_run(registered)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)

        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing"},
        )

        self.assertEqual(
            result,
            {"ok": True, "run_id": "run-existing", "state": "RUNNING"},
        )
        self.assertEqual(
            [record.pid for record in self.store.get("run-existing").processes],
            [registered.pid, launched.pid],
        )

    def test_foreign_diag_reason_allows_exact_registered_snapshot(self) -> None:
        registered = process(702)
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [
                {"pid": registered.pid, "name": "DayZDiag_x64.exe"},
            ],
        }
        reason = getattr(self.lifecycle, "_foreign_diag_reason", None)

        self.assertIsNotNone(reason)
        self.assertIsNone(reason({registered.pid}))

    def test_start_rejects_when_diag_probe_is_absent(self) -> None:
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        self.lifecycle.diag_probe = None

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "active_run_exists")
        rejected = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]
        self.assertEqual(rejected[-1]["reason"], "diag_snapshot_unknown")
        self.assertEqual(self.store.list_runs(), [])

    def test_start_rejects_when_diag_probe_raises(self) -> None:
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)

        def failing_probe() -> dict[str, object]:
            raise OSError("toolhelp failed")

        self.lifecycle.diag_probe = failing_probe

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "active_run_exists")
        rejected = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]
        self.assertEqual(rejected[-1]["reason"], "diag_snapshot_unknown")
        self.assertEqual(self.store.list_runs(), [])

    def test_start_rejects_diag_snapshot_without_known(self) -> None:
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        self.lifecycle.diag_probe = lambda: {"processes": []}

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "active_run_exists")
        rejected = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]
        self.assertEqual(rejected[-1]["reason"], "diag_snapshot_unknown")
        self.assertEqual(self.store.list_runs(), [])

    def test_start_rejects_diag_snapshot_with_bool_pid(self) -> None:
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": True, "name": "DayZDiag_x64.exe"}],
        }

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "active_run_exists")
        rejected = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]
        self.assertEqual(rejected[-1]["reason"], "diag_snapshot_unknown")
        self.assertEqual(self.store.list_runs(), [])

    def test_supplied_run_extension_allows_its_registered_diag(self) -> None:
        registered = process(703)
        self.add_run(registered)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [
                {"pid": registered.pid, "name": "DayZDiag_x64.exe"},
            ],
        }

        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing"},
        )

        self.assertEqual(
            result,
            {"ok": True, "run_id": "run-existing", "state": "RUNNING"},
        )
        self.assertEqual(
            [record.pid for record in self.store.get("run-existing").processes],
            [registered.pid, launched.pid],
        )

    def test_start_rejection_without_audit_reason_preserves_reason(self) -> None:
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request(self.game / "DayZ_BE.exe"),
        )

        self.assertEqual(result["error"], "retail_manual_lifecycle_required")
        rejected = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]
        self.assertEqual(
            rejected[-1]["reason"],
            "retail_manual_lifecycle_required",
        )

    def test_diag_start_records_only_complete_strong_identity(self) -> None:
        expected = process(self.launcher.pid)
        self.guard.snapshots[self.launcher.pid] = snapshot(expected)
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        self.assertEqual(result, {"ok": True, "run_id": "run-1", "state": "RUNNING"})
        self.assertEqual(self.store.get("run-1").processes, [expected])
        self.assertEqual(len(self.launcher.calls), 1)

    def test_recoverable_start_is_exactly_idempotent_and_ack_is_persisted(self) -> None:
        expected = process(self.launcher.pid)
        self.guard.snapshots[self.launcher.pid] = snapshot(expected)
        request = self.recoverable_request()

        first = self.lifecycle.start_run(IDENTITY_A, self.token_a, request)
        second = self.lifecycle.start_run(IDENTITY_A, self.token_a, request)
        acknowledged = self.lifecycle.ack_run(
            IDENTITY_A,
            self.token_a,
            request["new_run_id"],
            request["launch_operation_id"],
        )
        repeated_ack = self.lifecycle.ack_run(
            IDENTITY_A,
            self.token_a,
            request["new_run_id"],
            request["launch_operation_id"],
        )

        self.assertEqual(first, second)
        self.assertEqual(len(self.launcher.calls), 1)
        self.assertTrue(acknowledged["ok"])
        self.assertTrue(repeated_ack["ok"])
        stored = self.store.get(str(request["new_run_id"]))
        self.assertEqual(stored.launch_operation_id, request["launch_operation_id"])
        self.assertEqual(stored.launch_request_sha256, request["launch_request_sha256"])
        self.assertTrue(stored.launch_acknowledged)

    def test_recoverable_start_rejects_collision_or_hash_drift_without_spawn(self) -> None:
        expected = process(self.launcher.pid)
        self.guard.snapshots[self.launcher.pid] = snapshot(expected)
        request = self.recoverable_request()
        self.assertTrue(self.lifecycle.start_run(IDENTITY_A, self.token_a, request)["ok"])

        drift = request | {"launch_request_sha256": "f" * 64}
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, drift)
        ack = self.lifecycle.ack_run(
            IDENTITY_A,
            self.token_a,
            request["new_run_id"],
            "33333333-3333-4333-8333-333333333333",
        )

        self.assertEqual(result["error"], "launch_request_hash_mismatch")
        self.assertEqual(ack["error"], "launch_identity_conflict")
        self.assertEqual(len(self.launcher.calls), 1)

    def test_legacy_run_fields_default_to_null_null_true(self) -> None:
        value = {
            "run_id": "legacy",
            "owner_session_id": None,
            "owner_lease_id": None,
            "state": "EXITED",
            "label": "",
            "mod": "",
            "profiles": "",
            "mission": "",
            "processes": [],
        }
        run = RunRecord.from_payload(value)
        self.assertIsNone(run.launch_operation_id)
        self.assertIsNone(run.launch_request_sha256)
        self.assertTrue(run.launch_acknowledged)

    def test_unacknowledged_owner_cleanup_is_guarded_and_terminal(self) -> None:
        record = process(808)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.store.add(
            RunRecord(
                "11111111-1111-4111-8111-111111111111",
                "A",
                "lease-A",
                "RUNNING",
                "recoverable",
                "@mod",
                "profiles",
                "mission",
                [record],
                "22222222-2222-4222-8222-222222222222",
                "a" * 64,
                False,
            )
        )

        disposition = self.lifecycle.begin_release_owner("A", "lease-A")
        self.assertTrue(disposition.fence_required)
        self.assertTrue(disposition.terminal_event.wait(1.0))
        self.assertTrue(disposition.terminal_result["terminal_safe"])
        self.assertEqual(self.guard.terminate_calls, [record])
        self.assertEqual(
            self.store.get("11111111-1111-4111-8111-111111111111").state,
            "EXITED",
        )

    def test_unacknowledged_cleanup_identity_ambiguity_is_fail_closed(self) -> None:
        record = process(809)
        self.guard.snapshots[record.pid] = snapshot(record) | {
            "creation_time_utc": "different"
        }
        self.store.add(
            RunRecord(
                "11111111-1111-4111-8111-111111111111",
                "A",
                "lease-A",
                "RUNNING",
                "recoverable",
                "@mod",
                "profiles",
                "mission",
                [record],
                "22222222-2222-4222-8222-222222222222",
                "a" * 64,
                False,
            )
        )

        armed: list[tuple[str, str]] = []
        self.lifecycle.recovery_fault_arm = (
            lambda run, reason: armed.append((run.run_id, reason))
        )
        disposition = self.lifecycle.begin_release_owner("A", "lease-A")
        self.assertTrue(disposition.terminal_event.wait(1.0))
        self.assertFalse(disposition.terminal_result["terminal_safe"])
        self.assertEqual(disposition.terminal_result["error"], "identity_ambiguous")
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(
            armed,
            [("11111111-1111-4111-8111-111111111111", "identity_ambiguous")],
        )
        self.assertNotEqual(
            self.store.get("11111111-1111-4111-8111-111111111111").state,
            "EXITED",
        )

    def test_recovery_repair_revalidates_full_record_and_exits_run(self) -> None:
        record = process(810)
        run = RunRecord(
            "11111111-1111-4111-8111-111111111111",
            None,
            None,
            "UNRECONCILED",
            "recoverable",
            "@mod",
            "profiles",
            "mission",
            [record],
            "22222222-2222-4222-8222-222222222222",
            "a" * 64,
            False,
        )
        self.store.add(run)
        self.guard.snapshots[record.pid] = snapshot(record)
        run_hash = hashlib.sha256(
            json.dumps(dataclasses.asdict(run), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        result = self.lifecycle.repair_recovery_fault(
            {
                "scope": "run",
                "run_id": run.run_id,
                "launch_operation_id": run.launch_operation_id,
                "run_record_sha256": run_hash,
            }
        )
        self.assertTrue(result["terminal_safe"])
        self.assertEqual(self.store.get(run.run_id).state, "EXITED")
        self.assertEqual(self.guard.terminate_calls, [record])

    def test_manifest_recovery_restores_valid_backup_then_reconciles_unacknowledged(self) -> None:
        record = process(811)
        self.guard.snapshots[record.pid] = snapshot(record)
        run = RunRecord(
            "11111111-1111-4111-8111-111111111111",
            "A",
            "lease-A",
            "RUNNING",
            "recoverable",
            "@mod",
            "profiles",
            "mission",
            [record],
            "22222222-2222-4222-8222-222222222222",
            "a" * 64,
            False,
        )
        raw = (
            json.dumps(
                {"version": 1, "runs": [dataclasses.asdict(run)]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        result = self.lifecycle.repair_manifest_recovery(raw)

        self.assertTrue(result["terminal_safe"])
        persisted = json.loads(
            self.store.paths.runs_path.read_text(encoding="utf-8")
        )
        persisted_run = next(
            item for item in persisted["runs"] if item["run_id"] == run.run_id
        )
        self.assertEqual(persisted_run["state"], "EXITED")
        self.assertEqual(persisted_run["processes"], [])
        self.assertEqual(self.guard.terminate_calls, [record])
        # Load-time pruning is deliberate; the incident trail remains in the audit log.
        reloaded = RunManifestStore(self.store.paths)
        self.assertIsNone(reloaded.get(run.run_id))

    def test_manifest_recovery_rejects_invalid_backup_without_overwrite(self) -> None:
        self.store.paths.runs_path.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"version":1,"runs":[]}\n'
        self.store.paths.runs_path.write_bytes(original)

        result = self.lifecycle.repair_manifest_recovery(b'{"version":1,"runs":"bad"}\n')

        self.assertFalse(result["terminal_safe"])
        self.assertEqual(result["error"], "manifest_drift")
        self.assertEqual(self.store.paths.runs_path.read_bytes(), original)

    def test_allowed_executable_is_launched_by_its_canonical_path(self) -> None:
        expected = process(self.launcher.pid)
        self.guard.snapshots[self.launcher.pid] = snapshot(expected)
        alias = self.game / "unused" / ".." / "DayZDiag_x64.exe"
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(alias))
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.launcher.calls[0][0][0],
            str((self.game / "DayZDiag_x64.exe").resolve()),
        )

    def test_snapshot_marked_complete_but_missing_strong_hash_is_identity_unavailable(self) -> None:
        expected = process(self.launcher.pid)
        self.guard.snapshots[self.launcher.pid] = snapshot(expected) | {
            "command_line_sha256": ""
        }
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        self.assertEqual(result["error"], "identity_unavailable")
        self.assertEqual(self.launcher.terminated, [self.launcher.pid])
        self.assertEqual(self.store.get("run-1").state, "EXITED")

    def test_allowed_start_audit_failure_happens_before_launch(self) -> None:
        self.audit.fail_events.add("lifecycle_start")
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        self.assertEqual(result["error"], "audit_failed")
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.store.list_runs(), [])

    # --- Dead-run reaper (2026-07-16 ghost-run fix; spec plans/2026-07-16-daemon-dead-run-reaper-spec.md) ---

    def _dead(self, pid: int) -> None:
        # Guard reports a confirmed-dead PID exactly as process-guard.ps1 does (exit 4).
        self.guard.snapshots[pid] = {"error": "process_not_found", "exit_code": 4}

    def test_reaper_retires_run_with_all_dead_processes(self) -> None:  # SC-001, SC-004
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["run-existing"])
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "EXITED")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)
        self.assertEqual(stored.processes, [])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertIn("run_reaped", [event["event"] for event in self.audit.events])

    def test_reaper_retires_running_run_and_leaves_lease_intact(self) -> None:  # SC-001, H1
        # The delicate branch: a RUNNING run owned by a live lease whose game crashed.
        # Reap retires the run (manifest only) and must NOT touch the coordinator lease.
        self.add_run(process(48976), owner="A", state="RUNNING")
        self._dead(48976)
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["run-existing"])
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "EXITED")
        self.assertIsNone(stored.owner_session_id)
        self.assertEqual(self.guard.terminate_calls, [])
        # A's lease survived the reap: it can still authorise a mutation.
        self.assertTrue(
            self.coordinator.authorize(IDENTITY_A, self.token_a, "world_spawn").allowed
        )

    def test_reaper_skips_pid_reuse_mismatch(self) -> None:  # SC-002
        rec = process(48976)
        self.add_run(rec, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[48976] = snapshot(rec) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_reaper_skips_live_run(self) -> None:  # SC-003
        rec = process(48976)
        self.add_run(rec, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[48976] = snapshot(rec)
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_reaper_skips_when_diag_shows_unexpected_process(self) -> None:  # safety
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.lifecycle.diag_probe = lambda: {"known": True, "processes": [{"pid": 777}]}
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_reaper_skips_under_retail_quarantine(self) -> None:  # SC-005 (retail)
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.probe_result = {"known": True, "processes": [{"pid": 5, "name": "DayZ_x64.exe"}]}
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_reaper_audit_before_act_is_fail_closed(self) -> None:  # SC-007
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.audit.fail_events.add("run_reaped")
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_reap_dead_run_agent_callable_retires_all_dead(self) -> None:  # SC-005
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        result = self.lifecycle.reap_dead_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result, {"ok": True, "run_id": "run-existing", "state": "EXITED"})
        self.assertEqual(self.store.get("run-existing").state, "EXITED")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_reap_dead_run_rejects_live_run(self) -> None:  # SC-005 (dangerous case stays gated)
        rec = process(48976)
        self.add_run(rec, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[48976] = snapshot(rec)
        result = self.lifecycle.reap_dead_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result["error"], "run_not_reapable")
        self.assertEqual(result["_http_status"], 409)
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_reap_dead_run_reports_manifest_failure_distinctly(self) -> None:  # R9 H4/H1 fix
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.store.replace = lambda _run: (_ for _ in ()).throw(OSError("disk full"))  # type: ignore[method-assign]
        result = self.lifecycle.reap_dead_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual((result["error"], result["_http_status"]), ("manifest_failed", 503))

    def test_reap_dead_run_requires_lease(self) -> None:  # SC-005 (lease gate)
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        result = self.lifecycle.reap_dead_run(IDENTITY_B, None, "run-existing")
        self.assertIn(result["error"], {"lease_required", "lease_invalid"})
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_stop_mismatch_to_unreconciled_clears_owner(self) -> None:  # SC-009 / F-07
        rec = process(48976, "server")
        self.add_run(rec, owner="A", state="RUNNING")
        self.guard.snapshots[48976] = snapshot(rec) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result.get("error"), "process_identity_mismatch")
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)

    def test_identity_unavailable_uses_only_open_launcher_handle_and_marks_unreconciled(self) -> None:
        self.launcher.confirmed_exit = False
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        self.assertEqual(result["error"], "manual_cleanup_required")
        self.assertEqual(self.launcher.terminated, [self.launcher.pid])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get("run-1").state, "UNRECONCILED")

    def test_same_mod_does_not_grant_ownership(self) -> None:
        record = process(101)
        run = self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.coordinator.release(IDENTITY_A, self.token_a)
        status, acquired = self.coordinator.acquire(IDENTITY_B, "other")
        self.assertEqual(status, 200)
        result = self.lifecycle.stop_run(IDENTITY_B, acquired["lease_token"], run.run_id)
        self.assertEqual(result["error"], "run_not_adopted")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_pid_reuse_or_fingerprint_mismatch_preserves_every_process(self) -> None:
        first, second = process(102), process(103)
        run = self.add_run(first)
        run.processes.append(second)
        self.store.replace(run)
        self.guard.snapshots[first.pid] = snapshot(first) | {"creation_time_utc": "different"}
        self.guard.snapshots[second.pid] = snapshot(second)
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertEqual(result["error"], "process_identity_mismatch")
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get(run.run_id).state, "UNRECONCILED")

    def test_fully_matching_registered_stop_exits_only_that_run(self) -> None:
        record = process(112)
        run = self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertEqual(
            result,
            {
                "ok": True,
                "run_id": "run-existing",
                "state": "EXITED",
                "terminated": 1,
            },
        )
        exited = self.store.get(run.run_id)
        self.assertEqual((exited.state, exited.owner_session_id), ("EXITED", None))
        self.assertEqual([item.pid for item in self.guard.terminate_calls], [112])

    def test_phase_two_race_stops_further_termination_and_marks_unreconciled(self) -> None:
        first, second = process(104), process(105)
        run = self.add_run(first)
        run.processes.append(second)
        self.store.replace(run)
        self.guard.snapshots = {first.pid: snapshot(first), second.pid: snapshot(second)}
        self.guard.terminate_results = [
            {"terminated": True},
            {"terminated": False, "error": "process_identity_mismatch"},
        ]
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertEqual(result["error"], "partial_cleanup")
        self.assertEqual(result["terminated"], 1)
        stored = self.store.get(run.run_id)
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertIsNone(stored.owner_session_id)  # F-07 (site 3: terminate-fail)
        self.assertIsNone(stored.owner_lease_id)
        self.assertEqual([item.pid for item in self.guard.terminate_calls], [104, 105])

    def test_stop_quarantine_midloop_to_unreconciled_clears_owner(self) -> None:  # SC-009 / F-07 (site 2)
        record = process(48976)
        self.add_run(record, owner="A", state="RUNNING")
        self.guard.snapshots[record.pid] = snapshot(record)  # phase-1 passes (alive, matching)
        calls = {"n": 0}

        def probe() -> dict[str, object]:
            calls["n"] += 1  # clean through 877/942/949; quarantine appears at the phase-2 loop
            return {"known": True, "processes": [] if calls["n"] < 4 else [{"pid": 5}]}

        self.lifecycle.retail_probe = probe
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result["error"], "partial_cleanup")
        self.assertEqual(result.get("reason"), "retail_quarantine")
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)
        self.assertEqual(self.guard.terminate_calls, [])  # quarantine fired before any terminate

    def test_stop_manifest_failure_before_kill_is_explicit_and_preserves_process(self) -> None:
        record = process(111)
        run = self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.store.replace = lambda _run: (_ for _ in ()).throw(OSError("disk full"))  # type: ignore[method-assign]
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertEqual((result["error"], result["_http_status"]), ("manifest_failed", 503))
        self.assertEqual(self.guard.terminate_calls, [])

    def test_release_changes_running_to_idle_without_guard_or_terminate(self) -> None:
        record = process(106)
        self.add_run(record)
        changed = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(changed, ["run-existing"])
        idle = self.store.get("run-existing")
        self.assertEqual((idle.state, idle.owner_session_id, idle.owner_lease_id), ("RUNNING_IDLE", None, None))
        self.assertEqual(self.guard.snapshot_calls, [])
        self.assertEqual(self.guard.terminate_calls, [])

    def test_adopt_requires_idle_complete_match_and_clean_quarantine(self) -> None:
        record = process(107)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[record.pid] = snapshot(record)
        result = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result, {"ok": True, "run_id": "run-existing", "state": "RUNNING"})
        self.assertEqual(self.store.get("run-existing").owner_session_id, "A")

    def test_quarantine_blocks_lifecycle_before_manifest_or_guard(self) -> None:
        record = process(108)
        self.add_run(record)
        self.probe_result = {"known": False, "processes": []}
        start = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        stop = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual((start["error"], stop["error"]), ("retail_quarantine", "retail_quarantine"))
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.guard.snapshot_calls, [])

    def test_quarantine_blocks_adopt_and_admin_reconcile_but_status_remains_available(self) -> None:
        idle_record = process(109)
        self.add_run(idle_record, owner=None, state="RUNNING_IDLE")
        before = self.store.get("run-existing")
        self.probe_result = {
            "known": True,
            "processes": [{"pid": 55, "name": "DayZ_BE.exe"}],
        }
        adopt = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        reconcile = self.lifecycle.admin_reconcile("run-existing", 109, "incident")
        status = self.lifecycle.status(IDENTITY_A)
        self.assertEqual((adopt["error"], reconcile["error"]), ("retail_quarantine", "retail_quarantine"))
        self.assertTrue(status["retail_quarantine"])
        self.assertEqual(self.store.get("run-existing"), before)
        self.assertEqual(self.guard.snapshot_calls, [])
        self.assertEqual(self.guard.terminate_calls, [])

        def fail_probe():
            raise OSError("toolhelp failed")

        self.lifecycle.retail_probe = fail_probe
        self.assertEqual(
            self.lifecycle.admin_reconcile("run-existing", 109, "incident")["error"],
            "retail_quarantine",
        )
        self.assertEqual(self.guard.snapshot_calls, [])

    def test_admin_reconcile_cannot_orphan_a_running_owned_run(self) -> None:
        record = process(110)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        result = self.lifecycle.admin_reconcile("run-existing", 110, "incident")
        self.assertEqual(result["error"], "run_not_reconcilable")
        current = self.store.get("run-existing")
        self.assertEqual((current.state, current.owner_session_id), ("RUNNING", "A"))

    def test_admin_reconcile_ownerless_idle_run_with_gone_process_exits(self) -> None:
        record = process(111)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }

        result = self.lifecycle.admin_reconcile("run-existing", 111, "incident")

        self.assertEqual(
            result,
            {
                "reconciled": True,
                "run_id": "run-existing",
                "pid": 111,
                "state": "EXITED",
            },
        )
        current = self.store.get("run-existing")
        self.assertEqual((current.state, current.owner_session_id, current.processes), ("EXITED", None, []))
        self.assertEqual(self.guard.terminate_calls, [])

    def test_admin_reconcile_registered_unreconciled_run_is_non_destructive_and_audited(self) -> None:
        record = process(113)
        self.add_run(record, state="UNRECONCILED")
        self.guard.snapshots[record.pid] = snapshot(record)
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": record.pid, "name": "DayZDiag_x64.exe"}],
        }
        result = self.lifecycle.admin_reconcile("run-existing", 113, "incident")
        self.assertEqual(
            result,
            {
                "reconciled": True,
                "run_id": "run-existing",
                "pid": 113,
                "state": "RUNNING_IDLE",
            },
        )
        current = self.store.get("run-existing")
        self.assertEqual((current.state, current.owner_session_id), ("RUNNING_IDLE", None))
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertIn("admin_reconcile", [event["event"] for event in self.audit.events])

    def test_release_owner_serializes_with_lifecycle_operations(self) -> None:
        entered = threading.Event()
        original = self.store.release_owner

        def observed(session_id: str, lease_id: str):
            entered.set()
            return original(session_id, lease_id)

        self.store.release_owner = observed  # type: ignore[method-assign]
        self.lifecycle._operation_lock.acquire()
        try:
            thread = threading.Thread(
                target=lambda: self.lifecycle.release_owner("A", "lease-A")
            )
            thread.start()
            time.sleep(0.05)
            self.assertFalse(entered.is_set())
        finally:
            self.lifecycle._operation_lock.release()
        thread.join(timeout=1.0)
        self.assertTrue(entered.is_set())

    def test_retail_appearing_after_preflight_still_blocks_before_launch(self) -> None:
        calls = 0

        def changing_probe():
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"known": True, "processes": []}
            return {
                "known": True,
                "processes": [{"pid": 77, "name": "DayZ_x64.exe"}],
            }

        self.lifecycle.retail_probe = changing_probe
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        self.assertEqual(result["error"], "retail_quarantine")
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.store.list_runs(), [])

    def test_guard_terminate_revalidates_and_kills_the_same_process_object(self) -> None:
        guard_text = (_TOOLS_DIR / "process-guard.ps1").read_text(encoding="utf-8")
        terminate = guard_text.split('if ($request.operation -eq "terminate")', 1)[1]
        terminate = terminate.split('Write-Result ([ordered]@{ error = "unsupported_operation"', 1)[0]
        self.assertEqual(terminate.count("Get-Process -Id"), 1)
        self.assertIn("Get-IdentityFromProcess $proc", terminate)
        self.assertNotIn("Get-Identity ([int]$expected.pid)", terminate)
        self.assertLess(terminate.index("Get-IdentityFromProcess $proc"), terminate.index("$proc.Kill()"))


if __name__ == "__main__":
    unittest.main()
