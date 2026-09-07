from __future__ import annotations

import json
import hashlib
import dataclasses
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_storage, loopback
from dayz_mcp.instance_fence import BINDING_STARTING, Binding
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
    _ADOPT_NOT_DISPATCHABLE_HINT,
)
from dayz_mcp.runtime_state import JsonlAuditWriter, RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.fence_helpers import INST_CLIENT, INST_SERVER, accredited_poll, bind_both_peers


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


class FakeBridgeBindings:
    """The loopback ServerState status_snapshot the daemon wires as bridge_probe.

    fb-20260904-200816-79e2: superseding a live client needs the witness of the
    gate that authorised it AND a bridge row that still agrees at T1. The default
    row is a client that has not polled for a long time -- the state the gate
    acts on -- so the tests written before the witness keep measuring what they
    measured. The refusal branches have their own tests.
    """

    def __init__(
        self,
        last_poll_age_s: object = 999.0,
        bound_last_poll_age_s: object = None,
        binding_state: object = None,
        raise_on_read: bool = False,
    ) -> None:
        self.last_poll_age_s = last_poll_age_s
        self.bound_last_poll_age_s = bound_last_poll_age_s
        self.binding_state = binding_state
        self.raise_on_read = raise_on_read
        self.reads = 0

    def status_snapshot(self, now: object = None) -> dict[str, object]:
        self.reads += 1
        if self.raise_on_read:
            raise RuntimeError("bridge unavailable")
        return {
            "peers": {
                "client": {
                    "last_poll_age_s": self.last_poll_age_s,
                    "bound_last_poll_age_s": self.bound_last_poll_age_s,
                    "binding_state": self.binding_state,
                }
            }
        }


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
        self.bridge = FakeBridgeBindings()
        self.lifecycle.bridge_probe = self.bridge.status_snapshot

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
            # 79e2: the witness of the gate that authorised superseding a live
            # client, stamped now so the revalidation in start_run can compare.
            "replace_if_not_polling_since": int(time.time() * 1000),
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

    def _bind_run(self, run_id: str = "run-existing") -> loopback.ServerState:
        state = loopback.ServerState("k")
        bind_both_peers(state, run_id=run_id)
        state.lifecycle = self.lifecycle
        self.lifecycle.bindings = state
        return state

    def _enqueue_after_durable(
        self,
        state: loopback.ServerState,
        trigger_states: frozenset[str],
    ):
        seen: dict[str, object] = {}
        real = RunManifestStore.replace

        def _wrapped(store: RunManifestStore, run: RunRecord) -> None:
            real(store, run)
            name = getattr(run, "state", None)
            if name in trigger_states and "st" not in seen:
                seen["st"], seen["payload"] = state.enqueue_command(
                    "camera_get", {}, peer="client"
                )
                seen["state"] = name

        return seen, _wrapped

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

    def test_extending_inherited_run_does_not_import_pre_daemon_activity(self) -> None:
        from dayz_mcp.process_lifecycle import _utc_epoch

        viejo_utc = "2026-06-01T00:00:00.0000000Z"
        nuevo_utc = "2026-09-04T12:00:00.0000000Z"
        inherited = ProcessRecord(
            701,
            viejo_utc,
            HASH_A,
            HASH_B,
            "client",
            identity_scheme="psutil-argv-v2",
        )
        self.add_run(inherited)
        self.lifecycle.daemon_generation = "gen-B"
        launched = ProcessRecord(
            self.launcher.pid,
            nuevo_utc,
            HASH_A,
            HASH_B,
            "server",
            identity_scheme="psutil-argv-v2",
        )
        self.guard.snapshots[launched.pid] = snapshot(launched)
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing", "role": "server"},
        )
        self.assertTrue(result.get("ok"), result)
        now = _utc_epoch(nuevo_utc) + 10.0
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=now)["runs"]
            if item["run_id"] == "run-existing"
        )
        edad = row.get("last_activity_age_s")
        viejo = _utc_epoch(viejo_utc)
        importado = (
            edad is not None
            and viejo is not None
            and abs((now - float(edad)) - viejo) < 2.0
        )
        self.assertFalse(importado, row)
        self.assertEqual(row["activity_state"], "recent")
        self.assertAlmostEqual(row["last_activity_age_s"], 10.0, places=2)

    def test_failed_extend_does_not_leave_freshness_on_inherited_run(self) -> None:
        from dayz_mcp.process_lifecycle import _utc_epoch

        inherited = process(701)
        self.add_run(inherited)
        self.lifecycle.daemon_generation = "gen-B"
        self.guard.snapshots[inherited.pid] = snapshot(inherited)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        original_replace = self.store.replace

        def _replace(run: RunRecord) -> None:
            if run.state == "RUNNING" and len(run.processes) > 1:
                raise OSError("disk")
            original_replace(run)

        self.store.replace = _replace  # type: ignore[method-assign]
        before = self.lifecycle.box_occupancy(now=time.time())
        self.assertEqual(
            next(
                item["activity_state"]
                for item in before["runs"]
                if item["run_id"] == "run-existing"
            ),
            "unknown",
        )
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing", "role": "server"},
        )
        self.assertIn("error", result)
        restored = self.store.get("run-existing")
        self.assertEqual(restored.state, "RUNNING")
        self.assertEqual(len(restored.processes), 1)
        stamp = _utc_epoch(launched.creation_time_utc)
        self.assertIsNotNone(stamp)
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=float(stamp) + 10.0)["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_failed_extend_discards_activity_credited_in_the_confirm_window(self) -> None:
        profiles = self.root / "client_profiles"
        profiles.mkdir()
        (profiles / "dayz_mcp.json").write_text(
            '{"url":"http://127.0.0.1:1/","key":"k","pollHz":5}',
            encoding="utf-8",
        )
        state = loopback.ServerState("k")
        self.lifecycle.bindings = state
        state.lifecycle = self.lifecycle
        inherited = process(701)
        self.add_run(inherited)
        self.lifecycle.daemon_generation = "gen-B"
        self.guard.snapshots[inherited.pid] = snapshot(inherited)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        before = self.lifecycle.box_occupancy(now=time.time())
        self.assertEqual(
            next(
                item["activity_state"]
                for item in before["runs"]
                if item["run_id"] == "run-existing"
            ),
            "unknown",
        )
        window: dict[str, object] = {}
        original_replace = self.store.replace

        def _replace(run: RunRecord) -> None:
            if run.state == "RUNNING" and len(run.processes) > 1:
                window["st"], window["payload"] = state.enqueue_command(
                    "query_all_players", {}, peer="server"
                )
                raise OSError("disk")
            original_replace(run)

        self.store.replace = _replace  # type: ignore[method-assign]
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request()
            | {
                "run_id": "run-existing",
                "role": "server",
                "profiles": str(profiles),
            },
        )
        self.assertIn("error", result)
        self.assertEqual(window.get("st"), 200, window)
        credited = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
            and event.get("run_id") == "run-existing"
        ]
        self.assertTrue(
            credited,
            "the confirm-to-replace window did not credit the inherited run",
        )
        restored = self.store.get("run-existing")
        self.assertEqual(restored.state, "RUNNING")
        self.assertEqual(len(restored.processes), 1)
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

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

    def test_repair_recovery_fault_invalidates_occupancy_cache(self) -> None:
        record = process(8101)
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
            json.dumps(
                dataclasses.asdict(run),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        before = self.lifecycle.box_occupancy()
        self.assertTrue(before["occupied"])
        result = self.lifecycle.repair_recovery_fault(
            {
                "scope": "run",
                "run_id": run.run_id,
                "launch_operation_id": run.launch_operation_id,
                "run_record_sha256": run_hash,
            }
        )
        self.assertTrue(result["terminal_safe"])
        cached = self.lifecycle.box_occupancy()
        self.assertFalse(cached["occupied"])
        self.assertEqual(cached["runs"], [])

    def test_begin_release_owner_invalidates_occupancy_cache(self) -> None:
        record = process(8081)
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
        before = self.lifecycle.box_occupancy()
        self.assertTrue(before["occupied"])
        disposition = self.lifecycle.begin_release_owner("A", "lease-A")
        self.assertTrue(disposition.terminal_event.wait(1.0))
        self.assertTrue(disposition.terminal_result["terminal_safe"])
        cached = self.lifecycle.box_occupancy()
        self.assertFalse(cached["occupied"])
        self.assertEqual(cached["runs"], [])

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

    def test_reaper_retires_run_with_all_dead_processes(self) -> None:  # all-dead PIDs retire the run; never terminate
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

    def test_reaper_retires_running_run_and_leaves_lease_intact(self) -> None:  # crash of a RUNNING run must not touch the live lease
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

    def test_reaper_retires_foreign_identity_without_terminate(self) -> None:
        rec = process(48976)
        self.add_run(rec, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[48976] = snapshot(rec) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["run-existing"])
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "EXITED")
        self.assertEqual(stored.processes, [])
        self.assertEqual(self.guard.terminate_calls, [])

    def test_reaper_skips_live_run(self) -> None:
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

    def test_reaper_retires_under_retail_quarantine(self) -> None:  # reaper stays a no-terminate path under quarantine
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.probe_result = {"known": True, "processes": [{"pid": 5, "name": "DayZ_x64.exe"}]}
        self.assertTrue(self.lifecycle._quarantined())
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["run-existing"])
        self.assertEqual(self.store.get("run-existing").state, "EXITED")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_reaper_audit_before_act_is_fail_closed(self) -> None:
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.audit.fail_events.add("run_reaped")
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_reap_dead_run_agent_callable_retires_all_dead(self) -> None:
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        result = self.lifecycle.reap_dead_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result, {"ok": True, "run_id": "run-existing", "state": "EXITED"})
        self.assertEqual(self.store.get("run-existing").state, "EXITED")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_reap_dead_run_rejects_live_run(self) -> None:  # dangerous case stays gated
        rec = process(48976)
        self.add_run(rec, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[48976] = snapshot(rec)
        result = self.lifecycle.reap_dead_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result["error"], "run_not_reapable")
        self.assertEqual(result["_http_status"], 409)
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_reap_dead_run_reports_manifest_failure_distinctly(self) -> None:  # manifest_failed stays a distinct 503
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.store.replace = lambda _run: (_ for _ in ()).throw(OSError("disk full"))  # type: ignore[method-assign]
        result = self.lifecycle.reap_dead_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual((result["error"], result["_http_status"]), ("manifest_failed", 503))

    def test_reap_dead_run_requires_lease(self) -> None:  # lease gate
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        result = self.lifecycle.reap_dead_run(IDENTITY_B, None, "run-existing")
        self.assertIn(result["error"], {"lease_required", "lease_invalid"})
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")

    def test_stop_unknown_guard_unavailable_to_unreconciled_clears_owner(self) -> None:
        # Unknown guard snapshot stays fail-closed; owner cleared.
        rec = process(48101, "server")
        self.add_run(rec, owner="A", state="RUNNING")
        self.guard.snapshots[rec.pid] = {
            "error": "identity_unavailable",
            "exit_code": 3,
        }
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result.get("error"), "guard_unavailable")
        self.assertEqual(result.get("_http_status"), 503)
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)
        persisted = RunManifestStore(self.store.paths).get("run-existing")
        self.assertEqual(persisted.state, "UNRECONCILED")
        self.assertIsNone(persisted.owner_session_id)
        self.assertIsNone(persisted.owner_lease_id)
        self.assertEqual(self.guard.terminate_calls, [])

    def test_stop_unknown_incomplete_identity_to_unreconciled_clears_owner(self) -> None:
        # Incomplete identity is unknown, not foreign; owner cleared.
        rec = process(48102, "server")
        self.add_run(rec, owner="A", state="RUNNING")
        self.guard.snapshots[rec.pid] = {
            "pid": rec.pid,
            "identity_complete": False,
            "exit_code": 1,
        }
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(result.get("error"), "process_identity_mismatch")
        self.assertEqual(result.get("_http_status"), 409)
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)
        persisted = RunManifestStore(self.store.paths).get("run-existing")
        self.assertEqual(persisted.state, "UNRECONCILED")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_stop_and_reaper_audit_payload_classifies_owned_gone_and_foreign(self) -> None:
        live, gone, foreign = (
            process(48201, "server"),
            process(48202, "client"),
            process(48203, "client"),
        )
        run = self.add_run(live, owner="A", state="RUNNING")
        run.processes.extend([gone, foreign])
        self.store.replace(run)
        self.guard.snapshots[live.pid] = snapshot(live)
        self._dead(gone.pid)
        self.guard.snapshots[foreign.pid] = snapshot(foreign) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertTrue(result.get("ok"))
        stop_audit = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_stop"
        ]
        self.assertEqual(stop_audit[-1]["owned_pids"], [live.pid])
        self.assertEqual(stop_audit[-1]["gone_pids"], [gone.pid])
        self.assertEqual(stop_audit[-1]["foreign_pids"], [foreign.pid])

        gone_idle, foreign_idle = process(48221), process(48222)
        self.store.add(
            RunRecord(
                "run-reap-audit",
                None,
                None,
                "RUNNING_IDLE",
                "same",
                "@SameMod",
                "profiles",
                "mission",
                [gone_idle, foreign_idle],
            )
        )
        self._dead(gone_idle.pid)
        self.guard.snapshots[foreign_idle.pid] = snapshot(foreign_idle) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        self.audit.events.clear()
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["run-reap-audit"])
        reaped = [
            event
            for event in self.audit.events
            if event.get("event") == "run_reaped"
        ]
        self.assertEqual(reaped[-1]["reason"], "all_processes_gone_or_foreign")
        self.assertEqual(reaped[-1]["owned_pids"], [])
        self.assertEqual(reaped[-1]["gone_pids"], [gone_idle.pid])
        self.assertEqual(reaped[-1]["foreign_pids"], [foreign_idle.pid])

    def test_stop_foreign_identity_exits_without_terminate(self) -> None:
        rec = process(48976, "server")
        self.add_run(rec, owner="A", state="RUNNING")
        self.guard.snapshots[48976] = snapshot(rec) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(
            result,
            {
                "ok": True,
                "run_id": "run-existing",
                "state": "EXITED",
                "terminated": 0,
            },
        )
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "EXITED")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)
        self.assertEqual(self.guard.terminate_calls, [])

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

    def test_pid_reuse_skips_foreign_pid_and_stops_owned_survivor(self) -> None:
        first, second = process(102), process(103)
        run = self.add_run(first)
        run.processes.append(second)
        self.store.replace(run)
        self.guard.snapshots[first.pid] = snapshot(first) | {"creation_time_utc": "different"}
        self.guard.snapshots[second.pid] = snapshot(second)
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
        self.assertEqual([item.pid for item in self.guard.terminate_calls], [second.pid])
        stored = self.store.get(run.run_id)
        self.assertEqual((stored.state, stored.owner_session_id), ("EXITED", None))

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
        self.assertIsNone(stored.owner_session_id)  # owner cleared on terminate-fail
        self.assertIsNone(stored.owner_lease_id)
        self.assertEqual([item.pid for item in self.guard.terminate_calls], [104, 105])

    def test_stop_quarantine_midloop_to_unreconciled_clears_owner(self) -> None:  # owner cleared on mid-loop quarantine
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
        self.assertEqual(
            result,
            {
                "ok": True,
                "run_id": "run-existing",
                "state": "RUNNING",
                "dispatchable": False,
                "hint": _ADOPT_NOT_DISPATCHABLE_HINT,
            },
        )
        self.assertEqual(self.store.get("run-existing").owner_session_id, "A")

    def test_adopt_is_idempotent_for_the_same_owner(self) -> None:
        record = process(1072)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[record.pid] = snapshot(record)
        first = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        stored_after_first = self.store.get("run-existing")
        owner = stored_after_first.owner_session_id
        lease = stored_after_first.owner_lease_id
        state = stored_after_first.state
        audit_before = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_adopt"
        ]
        second = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(second, first)
        stored = self.store.get("run-existing")
        self.assertEqual(
            (stored.state, stored.owner_session_id, stored.owner_lease_id),
            (state, owner, lease),
        )
        audit_after = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_adopt"
        ]
        self.assertEqual(len(audit_after), len(audit_before) + 1)
        self.assertEqual(audit_after[-1].get("decision"), "allowed")
        self.assertEqual(audit_after[-1].get("run_id"), "run-existing")

    def test_adopt_from_another_session_stays_rejected(self) -> None:
        record = process(1073)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[record.pid] = snapshot(record)
        first = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(first.get("ok"), True, first)
        missing = self.lifecycle.adopt_run(IDENTITY_B, None, "run-existing")
        stolen = self.lifecycle.adopt_run(IDENTITY_B, self.token_a, "run-existing")
        self.assertNotEqual(missing.get("ok"), True, missing)
        self.assertNotEqual(stolen.get("ok"), True, stolen)
        stored = self.store.get("run-existing")
        self.assertEqual(stored.owner_session_id, "A")
        self.assertEqual(stored.state, "RUNNING")

    def test_adopt_run_dependency_failure_after_authorize_closes_reservation(
        self,
    ) -> None:
        record = process(1099)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        original_get = self.store.get

        def _boom(_run_id: str):
            raise OSError("manifest read failed")

        self.store.get = _boom  # type: ignore[method-assign]
        try:
            result = self.lifecycle.adopt_run(
                IDENTITY_A, self.token_a, "run-existing"
            )
        except Exception as exc:  # noqa: BLE001 — propagation is the failure
            self.fail(f"adopt_run propagated {type(exc).__name__}: {exc}")
        finally:
            self.store.get = original_get  # type: ignore[method-assign]
        self.assertEqual(result.get("error"), "adopt_failed")
        self.assertEqual(result.get("_http_status"), 503)
        self.assertNotEqual(result.get("ok"), True)
        active = self.coordinator._active
        self.assertIsNotNone(active)
        self.assertEqual(active.pending_authorizations, [])  # type: ignore[union-attr]
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)

    def test_adopt_run_abort_degradations_reach_cleanup_degraded(self) -> None:
        record = process(1100)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        original_get = self.store.get
        original_abort = self.coordinator.abort_reservation

        def _boom(_run_id: str):
            raise OSError("manifest read failed")

        def _degraded_abort(*args, **kwargs):
            base = original_abort(*args, **kwargs)
            return tuple(dict.fromkeys([*base, "audit_failed"]))

        self.store.get = _boom  # type: ignore[method-assign]
        self.coordinator.abort_reservation = _degraded_abort  # type: ignore[method-assign]
        try:
            result = self.lifecycle.adopt_run(
                IDENTITY_A, self.token_a, "run-existing"
            )
        except Exception as exc:  # noqa: BLE001 — propagation is the failure
            self.fail(f"adopt_run propagated {type(exc).__name__}: {exc}")
        finally:
            self.store.get = original_get  # type: ignore[method-assign]
            self.coordinator.abort_reservation = original_abort  # type: ignore[method-assign]
        self.assertEqual(result.get("error"), "adopt_failed")
        self.assertEqual(result.get("_http_status"), 503)
        self.assertIn("audit_failed", result.get("cleanup_degraded") or [])
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)

    def test_adopt_run_abort_raise_closes_via_reject_reservation(self) -> None:
        record = process(1101)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        original_get = self.store.get
        original_abort = self.coordinator.abort_reservation

        def _boom(_run_id: str):
            raise OSError("manifest read failed")

        def _abort_boom(*_a, **_k):
            raise OSError("abort boom")

        self.store.get = _boom  # type: ignore[method-assign]
        self.coordinator.abort_reservation = _abort_boom  # type: ignore[method-assign]
        try:
            result = self.lifecycle.adopt_run(
                IDENTITY_A, self.token_a, "run-existing"
            )
        except Exception as exc:  # noqa: BLE001 — propagation is the failure
            self.fail(f"adopt_run propagated {type(exc).__name__}: {exc}")
        finally:
            self.store.get = original_get  # type: ignore[method-assign]
            self.coordinator.abort_reservation = original_abort  # type: ignore[method-assign]
        self.assertEqual(result.get("error"), "adopt_failed")
        self.assertEqual(result.get("_http_status"), 503)
        active = self.coordinator._active
        self.assertIsNotNone(active)
        self.assertEqual(active.pending_authorizations, [])  # type: ignore[union-attr]
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)

    def test_adopt_run_abort_and_reject_raise_declares_reservation_abort_failed(
        self,
    ) -> None:
        record = process(1102)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        original_get = self.store.get
        original_abort = self.coordinator.abort_reservation
        original_reject = self.coordinator.reject_reservation

        def _boom(_run_id: str):
            raise OSError("manifest read failed")

        def _abort_boom(*_a, **_k):
            raise OSError("abort boom")

        def _reject_boom(*_a, **_k):
            raise OSError("reject boom")

        self.store.get = _boom  # type: ignore[method-assign]
        self.coordinator.abort_reservation = _abort_boom  # type: ignore[method-assign]
        self.coordinator.reject_reservation = _reject_boom  # type: ignore[method-assign]
        try:
            result = self.lifecycle.adopt_run(
                IDENTITY_A, self.token_a, "run-existing"
            )
        except Exception as exc:  # noqa: BLE001 — propagation is the failure
            self.fail(f"adopt_run propagated {type(exc).__name__}: {exc}")
        finally:
            self.store.get = original_get  # type: ignore[method-assign]
            self.coordinator.abort_reservation = original_abort  # type: ignore[method-assign]
            self.coordinator.reject_reservation = original_reject  # type: ignore[method-assign]
        self.assertEqual(result.get("error"), "adopt_failed")
        self.assertEqual(result.get("_http_status"), 503)
        self.assertIn(
            "reservation_abort_failed", result.get("cleanup_degraded") or []
        )
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)

    def test_exempt_delivery_does_not_credit_activity(self) -> None:
        record = process(1074)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        cleanup = state.cleanup_owner("A", "lease-A", "owner_release", True)
        self.assertEqual(cleanup["vehicle_release_enqueued"], 1)
        released = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(released, ["run-existing"])
        _, poll = accredited_poll(state, "client")
        self.assertEqual(
            [command.get("cmd") for command in poll.get("commands", [])],
            ["vehicle_release"],
        )
        row = self.lifecycle.box_occupancy()["runs"][0]
        self.assertEqual(row["state"], "RUNNING_IDLE")
        self.assertNotEqual(row.get("activity_state"), "recent")

    def test_adopt_invalidates_occupancy_cache(self) -> None:
        record = process(1071)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[record.pid] = snapshot(record)
        before = self.lifecycle.box_occupancy()
        self.assertEqual(before["runs"][0]["state"], "RUNNING_IDLE")
        result = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(
            result,
            {
                "ok": True,
                "run_id": "run-existing",
                "state": "RUNNING",
                "dispatchable": False,
                "hint": _ADOPT_NOT_DISPATCHABLE_HINT,
            },
        )
        cached = self.lifecycle.box_occupancy()
        self.assertEqual(cached["runs"][0]["state"], "RUNNING")
        self.assertEqual(cached["runs"][0]["owner_session"], "A")

    def test_release_owner_invalidates_occupancy_cache(self) -> None:
        record = process(1061)
        self.add_run(record)
        before = self.lifecycle.box_occupancy()
        self.assertEqual(before["runs"][0]["state"], "RUNNING")
        changed = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(changed, ["run-existing"])
        cached = self.lifecycle.box_occupancy()
        self.assertEqual(cached["runs"][0]["state"], "RUNNING_IDLE")
        self.assertIsNone(cached["runs"][0]["owner_session"])

    def test_stop_run_releases_when_one_registered_pid_is_already_dead(self) -> None:
        live, dead = process(48011, "server"), process(48012, "client")
        run = self.add_run(live, owner="A", state="RUNNING")
        run.processes.append(dead)
        self.store.replace(run)
        self.guard.snapshots[live.pid] = snapshot(live)
        self._dead(dead.pid)
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
        stored = self.store.get(run.run_id)
        self.assertEqual((stored.state, stored.owner_session_id), ("EXITED", None))
        self.assertEqual([item.pid for item in self.guard.terminate_calls], [live.pid])

    def test_reaper_retires_incoherent_all_dead_and_unblocks_start(self) -> None:
        first, second = process(48001), process(48002)
        run = self.add_run(first, owner=None, state="RUNNING_IDLE")
        run.processes.append(second)
        self.store.replace(run)
        self._dead(first.pid)
        self.guard.snapshots[second.pid] = snapshot(second) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["run-existing"])
        self.assertEqual(self.store.get("run-existing").state, "EXITED")
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        self.assertNotEqual(result.get("error"), "active_run_exists")
        self.assertTrue(result.get("ok"))

    def test_adopt_allows_absent_registered_process(self) -> None:
        live, dead = process(48021, "server"), process(48022, "client")
        run = self.add_run(live, owner=None, state="RUNNING_IDLE")
        run.processes.append(dead)
        self.store.replace(run)
        self.guard.snapshots[live.pid] = snapshot(live)
        self._dead(dead.pid)
        result = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(
            result,
            {
                "ok": True,
                "run_id": "run-existing",
                "state": "RUNNING",
                "dispatchable": False,
                "hint": _ADOPT_NOT_DISPATCHABLE_HINT,
            },
        )
        stop = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertEqual(stop.get("state"), "EXITED")
        self.assertEqual([item.pid for item in self.guard.terminate_calls], [live.pid])

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

    def test_admin_reconcile_invalidates_occupancy_cache(self) -> None:
        record = process(1131)
        self.add_run(record, owner=None, state="UNRECONCILED")
        self.guard.snapshots[record.pid] = snapshot(record)
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": record.pid, "name": "DayZDiag_x64.exe"}],
        }
        before = self.lifecycle.box_occupancy()
        self.assertEqual(before["runs"][0]["state"], "UNRECONCILED")
        result = self.lifecycle.admin_reconcile("run-existing", 1131, "incident")
        self.assertEqual(result.get("state"), "RUNNING_IDLE")
        cached = self.lifecycle.box_occupancy()
        self.assertEqual(cached["runs"][0]["state"], "RUNNING_IDLE")

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

    def _never_started_run(
        self, run_id: str = "11111111-1111-4111-8111-111111111111"
    ) -> RunRecord:
        run = RunRecord(
            run_id,
            None,
            None,
            "EXITED",
            "same",
            "@SameMod",
            "profiles",
            "mission",
            [],
            "22222222-2222-4222-8222-222222222222",
            HASH_A,
            False,
        )
        self.store.add(run)
        return run

    def test_stop_never_started_unacked_exited_run_succeeds_without_killing(self) -> None:
        run = self._never_started_run()

        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("run_id"), run.run_id)
        self.assertEqual(result.get("state"), "EXITED")
        self.assertEqual(self.guard.terminate_calls, [])
        stored = self.store.get(run.run_id)
        self.assertEqual(stored.state, "EXITED")
        self.assertIsNone(stored.owner_session_id)
        self.assertFalse(stored.launch_acknowledged)

    def test_stop_never_started_does_not_open_foreign_running_runs(self) -> None:
        ghost = self._never_started_run()
        live = process(201)
        owned = self.add_run(live)
        self.guard.snapshots[live.pid] = snapshot(live)
        self.coordinator.release(IDENTITY_A, self.token_a)
        status, acquired = self.coordinator.acquire(IDENTITY_B, "other")
        self.assertEqual(status, 200)

        ghost_stop = self.lifecycle.stop_run(
            IDENTITY_B, acquired["lease_token"], ghost.run_id
        )
        live_stop = self.lifecycle.stop_run(
            IDENTITY_B, acquired["lease_token"], owned.run_id
        )

        self.assertEqual(ghost_stop.get("ok"), True)
        self.assertEqual(live_stop.get("error"), "run_not_adopted")
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get(owned.run_id).state, "RUNNING")
        self.assertEqual(self.store.get(owned.run_id).owner_session_id, "A")

    def test_acked_exited_run_is_not_stoppable_as_a_ghost(self) -> None:
        run = RunRecord(
            "33333333-3333-4333-8333-333333333333",
            None,
            None,
            "EXITED",
            "same",
            "@SameMod",
            "profiles",
            "mission",
            [],
        )
        self.store.add(run)

        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)

        self.assertEqual(result.get("error"), "run_not_adopted")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_failed_prepare_exposes_lifecycle_reason_on_status(self) -> None:
        class _EmptyMint:
            def prepare(self, *_args: object, **_kwargs: object) -> str:
                return ""

            def retire_role(self, *_args: object, **_kwargs: object) -> None:
                return None

            def confirm(self, *_args: object, **_kwargs: object) -> None:
                return None

            def retire_run(self, *_args: object, **_kwargs: object) -> None:
                return None

        self.lifecycle.bindings = _EmptyMint()
        request = self.recoverable_request()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, request)

        self.assertEqual(result.get("error"), "instance_config_missing")
        self.assertEqual(result.get("state"), "EXITED")
        status = self.lifecycle.status(IDENTITY_A)
        self.assertEqual(status.get("last_start_error"), "instance_config_missing")
        stored = self.store.get(str(request["new_run_id"]))
        self.assertEqual(stored.state, "EXITED")
        self.assertFalse(stored.launch_acknowledged)
        self.assertEqual(stored.processes, [])
        stop = self.lifecycle.stop_run(
            IDENTITY_A, self.token_a, str(request["new_run_id"])
        )
        self.assertEqual(stop.get("ok"), True)

    def test_stop_run_rejects_enqueue_once_stopping_is_durable(self) -> None:
        record = process(808)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        seen, wrapped = self._enqueue_after_durable(state, frozenset({"STOPPING"}))
        with patch.object(RunManifestStore, "replace", wrapped):
            result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(result.get("ok"), result)
        self.assertIn("st", seen)
        self.assertNotEqual(seen["st"], 200, seen)

    def test_stop_run_rejects_enqueue_once_exited_is_durable(self) -> None:
        record = process(809)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        seen, wrapped = self._enqueue_after_durable(state, frozenset({"EXITED"}))
        with patch.object(RunManifestStore, "replace", wrapped):
            result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(result.get("ok"), result)
        self.assertIn("st", seen)
        self.assertNotEqual(seen["st"], 200, seen)

    def test_begin_release_owner_rejects_enqueue_once_exited_is_durable(self) -> None:
        record = process(8082)
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
        state = self._bind_run("11111111-1111-4111-8111-111111111111")
        seen, wrapped = self._enqueue_after_durable(state, frozenset({"EXITED"}))
        with patch.object(RunManifestStore, "replace", wrapped):
            disposition = self.lifecycle.begin_release_owner("A", "lease-A")
            self.assertTrue(disposition.terminal_event.wait(2.0))
        self.assertTrue(disposition.terminal_result.get("terminal_safe"))
        self.assertIn("st", seen)
        self.assertNotEqual(seen["st"], 200, seen)

    def test_repair_recovery_fault_rejects_enqueue_once_exited_is_durable(self) -> None:
        record = process(8102)
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
            json.dumps(
                dataclasses.asdict(run),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        state = self._bind_run(run.run_id)
        seen, wrapped = self._enqueue_after_durable(state, frozenset({"EXITED"}))
        with patch.object(RunManifestStore, "replace", wrapped):
            result = self.lifecycle.repair_recovery_fault(
                {
                    "scope": "run",
                    "run_id": run.run_id,
                    "launch_operation_id": run.launch_operation_id,
                    "run_record_sha256": run_hash,
                }
            )
        self.assertTrue(result.get("terminal_safe"), result)
        self.assertIn("st", seen)
        self.assertNotEqual(seen["st"], 200, seen)

    def test_repair_manifest_recovery_rejects_enqueue_once_exited_is_durable(
        self,
    ) -> None:
        record = process(8112)
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
        state = self._bind_run(run.run_id)
        seen, wrapped = self._enqueue_after_durable(state, frozenset({"EXITED"}))
        with patch.object(RunManifestStore, "replace", wrapped):
            result = self.lifecycle.repair_manifest_recovery(raw)
        self.assertTrue(result.get("terminal_safe"), result)
        self.assertIn("st", seen)
        self.assertNotEqual(seen["st"], 200, seen)

    def test_reap_run_rejects_enqueue_once_exited_is_durable(self) -> None:
        rec = process(48977)
        self.add_run(rec, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[rec.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        state = self._bind_run()
        seen, wrapped = self._enqueue_after_durable(state, frozenset({"EXITED"}))
        with patch.object(RunManifestStore, "replace", wrapped):
            reaped = self.lifecycle.reap_dead_runs()
        self.assertEqual(reaped, ["run-existing"])
        self.assertIn("st", seen)
        self.assertNotEqual(seen["st"], 200, seen)

    def test_admin_reconcile_rejects_enqueue_once_exited_is_durable(self) -> None:
        record = process(1132)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        state = self._bind_run()
        seen, wrapped = self._enqueue_after_durable(state, frozenset({"EXITED"}))
        with patch.object(RunManifestStore, "replace", wrapped):
            result = self.lifecycle.admin_reconcile(
                "run-existing", record.pid, "incident", empty=False
            )
        self.assertEqual(result.get("state"), "EXITED", result)
        self.assertIn("st", seen)
        self.assertNotEqual(seen["st"], 200, seen)

    def _world_spawn(self, state: loopback.ServerState):
        return state.enqueue_command(
            "world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server"
        )

    def test_acknowledged_begin_release_owner_rejects_world_spawn(self) -> None:
        record = process(8083)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        before, _ = self._world_spawn(state)
        self.assertEqual(before, 200)
        disposition = self.lifecycle.begin_release_owner("A", "lease-A")
        self.assertFalse(disposition.fence_required)
        self.assertTrue(disposition.terminal_result.get("terminal_safe"))
        durable = self.store.get("run-existing")
        self.assertEqual(durable.state, "RUNNING_IDLE")
        self.assertIsNone(durable.owner_session_id)
        status, payload = self._world_spawn(state)
        self.assertNotEqual(status, 200, payload)
        self.assertEqual(payload.get("error"), "run_not_owned")
        hint = payload.get("hint")
        self.assertIsInstance(hint, str)
        self.assertIn("Adopt", hint)
        self.assertNotIn("dayz_test_run mode=client", hint)
        self.assertNotIn("mode=client run_id=", hint)
        self.assertNotEqual(payload.get("error"), "binding_retired")

    def test_admin_reconcile_with_survivors_rejects_world_spawn(self) -> None:
        record = process(1133)
        self.add_run(record, owner=None, state="UNRECONCILED")
        self.guard.snapshots[record.pid] = snapshot(record)
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": record.pid, "name": "DayZDiag_x64.exe"}],
        }
        state = self._bind_run()
        result = self.lifecycle.admin_reconcile(
            "run-existing", record.pid, "incident"
        )
        self.assertEqual(result.get("state"), "RUNNING_IDLE", result)
        status, payload = self._world_spawn(state)
        self.assertNotEqual(status, 200, payload)
        self.assertEqual(payload.get("error"), "run_not_owned")

    def test_release_owner_drains_pending_queue_and_adopt_rehabilitates(self) -> None:
        record = process(8084)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        queued, payload = self._world_spawn(state)
        self.assertEqual(queued, 200, payload)
        self.assertTrue(state._bound_queues.get(INST_SERVER))
        changed = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(changed, ["run-existing"])
        durable = self.store.get("run-existing")
        self.assertEqual(durable.state, "RUNNING_IDLE")
        self.assertIn(INST_SERVER, state._bindings)
        self.assertEqual(state._bindings[INST_SERVER].state, "BOUND")
        self.assertEqual(state._bound_queues.get(INST_SERVER), [])
        poll_status, poll = accredited_poll(state, "server")
        self.assertEqual(poll_status, 200, poll)
        self.assertEqual(poll.get("commands"), [])
        idle_status, idle_payload = self._world_spawn(state)
        self.assertEqual(idle_payload.get("error"), "run_not_owned")
        self.assertNotEqual(idle_status, 200)
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(adopted.get("ok"), adopted)
        self.assertIs(adopted.get("dispatchable"), True, adopted)
        after, after_payload = self._world_spawn(state)
        self.assertEqual(after, 200, after_payload)
        self.assertIn(INST_SERVER, state._bindings)

    def test_starting_run_still_dispatches_mutations(self) -> None:
        record = process(8085)
        self.add_run(record, state="STARTING")
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        status, payload = self._world_spawn(state)
        self.assertEqual(status, 200, payload)

    def test_credit_during_failed_launch_wait_is_tombstoned_post_rollback_kept(
        self,
    ) -> None:
        inherited = process(701)
        self.add_run(inherited)
        self.guard.snapshots[inherited.pid] = snapshot(inherited)
        seeded_at = time.time()
        self.lifecycle.record_command_activity("run-existing", now=seeded_at)
        credited: dict[str, object] = {}
        original_wait = self.launcher.wait

        def _wait(timeout: float) -> None:
            credited["ok"] = self.lifecycle.record_command_activity(
                "run-existing", now=time.time()
            )
            return original_wait(timeout)

        self.launcher.wait = _wait  # type: ignore[method-assign]
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing", "role": "server"},
        )
        self.assertIn("error", result, result)
        self.assertTrue(credited.get("ok"), credited)
        restored = self.store.get("run-existing")
        self.assertEqual(restored.state, "RUNNING")
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])
        after_at = time.time()
        self.assertTrue(
            self.lifecycle.record_command_activity("run-existing", now=after_at)
        )
        kept = next(
            item
            for item in self.lifecycle.box_occupancy(now=after_at + 1.0)["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(kept["activity_state"], "recent")

    def test_credit_during_rollback_replace_is_discarded_post_lift_lands(self) -> None:
        inherited = process(701)
        self.add_run(inherited)
        self.guard.snapshots[inherited.pid] = snapshot(inherited)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        original_replace = self.store.replace
        during: dict[str, object] = {}

        def _replace(run: RunRecord) -> None:
            if run.state == "RUNNING" and len(run.processes) > 1:
                raise OSError("disk")
            if run.state == "RUNNING" and len(run.processes) == 1:
                original_replace(run)
                during["ok"] = self.lifecycle.record_command_activity(
                    "run-existing", now=time.time()
                )
                key = self.lifecycle._activity_key("run-existing")
                with self.lifecycle._activity_lock:
                    during["stamp"] = self.lifecycle._last_activity.get(key)
                return
            original_replace(run)

        self.store.replace = _replace  # type: ignore[method-assign]
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing", "role": "server"},
        )
        self.assertIn("error", result, result)
        self.assertTrue(during.get("ok"), during)
        self.assertIsNone(during.get("stamp"), during)
        restored = self.store.get("run-existing")
        self.assertEqual(restored.state, "RUNNING")
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])
        after_at = time.time()
        self.assertTrue(
            self.lifecycle.record_command_activity("run-existing", now=after_at)
        )
        kept = next(
            item
            for item in self.lifecycle.box_occupancy(now=after_at + 1.0)["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(kept["activity_state"], "recent")

    def test_failed_rollback_replace_lifts_compensating_fence(self) -> None:
        inherited = process(702)
        self.add_run(inherited)
        self.guard.snapshots[inherited.pid] = snapshot(inherited)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        original_replace = self.store.replace

        def _replace(run: RunRecord) -> None:
            if run.state == "RUNNING":
                raise OSError("disk")
            original_replace(run)

        self.store.replace = _replace  # type: ignore[method-assign]
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing", "role": "server"},
        )
        self.assertIn("error", result, result)
        after_at = time.time()
        self.assertTrue(
            self.lifecycle.record_command_activity("run-existing", now=after_at)
        )
        kept = next(
            item
            for item in self.lifecycle.box_occupancy(now=after_at + 1.0)["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(kept["activity_state"], "recent")

    def test_acknowledged_begin_release_owner_poll_during_persist_is_empty(
        self,
    ) -> None:
        record = process(8086)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        queued, payload = self._world_spawn(state)
        self.assertEqual(queued, 200, payload)
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien: dict[str, object] = {}
        release_real = self.store.release_owner

        def _release_lento(session_id: str, lease_id: str):
            salida = release_real(session_id, lease_id)
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return salida

        self.store.release_owner = _release_lento  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _liberador() -> None:
            returned["d"] = self.lifecycle.begin_release_owner("A", "lease-A")

        thread = threading.Thread(target=_liberador, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertEqual(quien.get("ident"), thread.ident)
        self.assertNotIn("d", returned)
        durable = self.store.get("run-existing").state
        self.assertEqual(durable, "RUNNING_IDLE")
        poll_status, poll = accredited_poll(state, "server")
        puerta.set()
        thread.join(timeout=5)
        self.store.release_owner = release_real  # type: ignore[method-assign]
        self.assertEqual(poll_status, 200, poll)
        self.assertEqual(poll.get("commands"), [])
        disposition = returned.get("d")
        self.assertIsNotNone(disposition)
        self.assertFalse(disposition.fence_required)

    def test_admin_reconcile_with_survivors_poll_during_persist_is_empty(self) -> None:
        record = process(1134)
        self.add_run(record, owner=None, state="UNRECONCILED")
        self.guard.snapshots[record.pid] = snapshot(record)
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": record.pid, "name": "DayZDiag_x64.exe"}],
        }
        state = self._bind_run()
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        replace_real = self.store.replace

        def _replace_lento(run: RunRecord) -> None:
            replace_real(run)
            if (
                armado.is_set()
                and not dentro.is_set()
                and getattr(run, "state", None) == "RUNNING_IDLE"
            ):
                dentro.set()
                puerta.wait(5.0)

        self.store.replace = _replace_lento  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _reconciler() -> None:
            returned["r"] = self.lifecycle.admin_reconcile(
                "run-existing", record.pid, "incident"
            )

        thread = threading.Thread(target=_reconciler, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertNotIn("r", returned)
        st_cmd, _ = state.enqueue_command("camera_get", {}, peer="client")
        poll_status, poll = accredited_poll(state, "client")
        puerta.set()
        thread.join(timeout=5)
        self.store.replace = replace_real  # type: ignore[method-assign]
        self.assertNotEqual(st_cmd, 200)
        self.assertEqual(poll_status, 200, poll)
        self.assertEqual(poll.get("commands"), [])
        self.assertEqual((returned.get("r") or {}).get("state"), "RUNNING_IDLE")

    def test_release_owner_persist_failure_reverts_fence_and_still_dispatches(
        self,
    ) -> None:
        record = process(8087)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        before, _ = self._world_spawn(state)
        self.assertEqual(before, 200)

        def _fail(_session_id: str, _lease_id: str):
            raise OSError("disk")

        original = self.store.release_owner
        self.store.release_owner = _fail  # type: ignore[method-assign]
        with self.assertRaises(OSError):
            self.lifecycle.release_owner("A", "lease-A")
        self.store.release_owner = original  # type: ignore[method-assign]
        durable = self.store.get("run-existing")
        self.assertEqual(durable.state, "RUNNING")
        self.assertEqual(durable.owner_session_id, "A")
        status, payload = self._world_spawn(state)
        self.assertEqual(status, 200, payload)

    def test_internal_cleanup_enqueued_before_release_survives_when_idle_published(
        self,
    ) -> None:
        record = process(8088)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        queued, payload = state.enqueue_command(
            "vehicle_release", {}, peer="client", internal=True
        )
        self.assertEqual(queued, 200, payload)
        state._fire_and_forget_ids.add(payload["id"])
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        seen: dict[str, object] = {}
        release_real = self.store.release_owner

        def _release_lento(session_id: str, lease_id: str):
            salida = release_real(session_id, lease_id)
            if armado.is_set() and not dentro.is_set():
                dentro.set()
                seen["queue"] = list(state._bound_queues.get(INST_CLIENT) or [])
                seen["poll"] = accredited_poll(state, "client")
                puerta.wait(5.0)
            return salida

        self.store.release_owner = _release_lento  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _liberador() -> None:
            returned["changed"] = self.lifecycle.release_owner("A", "lease-A")

        thread = threading.Thread(target=_liberador, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        durable = self.store.get("run-existing").state
        self.assertEqual(durable, "RUNNING_IDLE")
        puerta.set()
        thread.join(timeout=5)
        self.store.release_owner = release_real  # type: ignore[method-assign]
        self.assertEqual(returned.get("changed"), ["run-existing"])
        queued = seen.get("queue") or []
        self.assertEqual([command.get("cmd") for command in queued], ["vehicle_release"])
        poll = seen.get("poll")
        self.assertIsInstance(poll, tuple)
        self.assertEqual(
            [command.get("cmd") for command in poll[1].get("commands", [])],
            ["vehicle_release"],
        )

    def test_poll_revalidates_after_lock_when_store_releases_without_fence(
        self,
    ) -> None:
        record = process(8090)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        queued, payload = self._world_spawn(state)
        self.assertEqual(queued, 200, payload)
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        crl_real = loopback.command_requires_lease

        def _crl_lento(cmd: str) -> bool:
            if armado.is_set() and not dentro.is_set():
                dentro.set()
                puerta.wait(5.0)
            return crl_real(cmd)

        loopback.command_requires_lease = _crl_lento  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _poll() -> None:
            returned["p"] = accredited_poll(state, "server")

        thread = threading.Thread(target=_poll, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertNotIn("p", returned)
        changed = self.store.release_owner("A", "lease-A")
        durable = self.store.get("run-existing").state
        puerta.set()
        thread.join(timeout=5)
        loopback.command_requires_lease = crl_real  # type: ignore[method-assign]
        self.assertEqual(changed, ["run-existing"])
        self.assertEqual(durable, "RUNNING_IDLE")
        st_poll, poll = returned.get("p", (None, None))
        self.assertEqual(st_poll, 200, poll)
        self.assertEqual(poll.get("commands"), [])

    def test_poll_in_repair_window_does_not_deliver(self) -> None:
        record = process(8091)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        queued, payload = self._world_spawn(state)
        self.assertEqual(queued, 200, payload)
        raw = self.store.paths.runs_path.read_bytes()
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        crl_real = loopback.command_requires_lease

        def _crl_lento(cmd: str) -> bool:
            if armado.is_set() and not dentro.is_set():
                dentro.set()
                puerta.wait(5.0)
            return crl_real(cmd)

        loopback.command_requires_lease = _crl_lento  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _poll() -> None:
            returned["p"] = accredited_poll(state, "server")

        thread = threading.Thread(target=_poll, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertNotIn("p", returned)
        repaired = self.lifecycle.repair_manifest_recovery(raw)
        durable = self.lifecycle.manifest.get("run-existing").state
        puerta.set()
        thread.join(timeout=5)
        loopback.command_requires_lease = crl_real  # type: ignore[method-assign]
        self.assertTrue(repaired.get("terminal_safe"), repaired)
        self.assertEqual(durable, "RUNNING_IDLE")
        st_poll, poll = returned.get("p", (None, None))
        self.assertEqual(st_poll, 200, poll)
        self.assertEqual(poll.get("commands"), [])
        self.assertIn("run-existing", state._fenced_runs)

    def test_release_all_running_owners_poll_during_persist_is_empty(self) -> None:
        record = process(8092)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        queued, payload = self._world_spawn(state)
        self.assertEqual(queued, 200, payload)
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        release_real = self.store.release_all_running_owners

        def _release_lento() -> list[str]:
            salida = release_real()
            if armado.is_set() and not dentro.is_set():
                dentro.set()
                puerta.wait(5.0)
            return salida

        self.store.release_all_running_owners = _release_lento  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _liberador() -> None:
            returned["changed"] = self.lifecycle.release_all_running_owners()

        thread = threading.Thread(target=_liberador, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertNotIn("changed", returned)
        durable = self.store.get("run-existing").state
        self.assertEqual(durable, "RUNNING_IDLE")
        poll_status, poll = accredited_poll(state, "server")
        puerta.set()
        thread.join(timeout=5)
        self.store.release_all_running_owners = release_real  # type: ignore[method-assign]
        self.assertEqual(returned.get("changed"), ["run-existing"])
        self.assertEqual(poll_status, 200, poll)
        self.assertEqual(poll.get("commands"), [])
        self.assertIn("run-existing", state._fenced_runs)

    def test_reader_during_rollback_replace_publishes_unknown(self) -> None:
        inherited = process(703)
        self.add_run(inherited)
        self.guard.snapshots[inherited.pid] = snapshot(inherited)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        original_wait = self.launcher.wait
        original_replace = self.store.replace
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()

        def _wait(timeout: float) -> None:
            self.lifecycle.record_command_activity("run-existing", now=time.time())
            return original_wait(timeout)

        def _replace(run: RunRecord) -> None:
            if run.state == "RUNNING" and len(run.processes) > 1:
                raise OSError("disk")
            if run.state == "RUNNING" and len(run.processes) == 1:
                original_replace(run)
                if armado.is_set() and not dentro.is_set():
                    dentro.set()
                    puerta.wait(5.0)
                return
            original_replace(run)

        self.launcher.wait = _wait  # type: ignore[method-assign]
        self.store.replace = _replace  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _starter() -> None:
            returned["start"] = self.lifecycle.start_run(
                IDENTITY_A,
                self.token_a,
                self.request() | {"run_id": "run-existing", "role": "server"},
            )

        def _reader() -> None:
            returned["box"] = self.lifecycle.box_occupancy(now=time.time())

        starter = threading.Thread(target=_starter, daemon=True)
        armado.set()
        starter.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertNotIn("start", returned)
        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        reader.join(timeout=5)
        puerta.set()
        starter.join(timeout=5)
        self.launcher.wait = original_wait  # type: ignore[method-assign]
        self.store.replace = original_replace  # type: ignore[method-assign]
        self.assertIn("error", returned.get("start") or {}, returned)
        box = returned.get("box")
        self.assertIsInstance(box, dict)
        row = next(
            item
            for item in box["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_release_then_reap_drops_fence(self) -> None:
        record = process(8093)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        changed = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(changed, ["run-existing"])
        self.assertIn("run-existing", state._fenced_runs)
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        reaped = self.lifecycle.reap_dead_runs()
        self.assertEqual(reaped, ["run-existing"])
        self.assertEqual(self.store.get("run-existing").state, "EXITED")
        self.assertNotIn("run-existing", state._fenced_runs)

    def test_failed_rollback_replace_forgets_attempt_credit(self) -> None:
        inherited = process(701)
        self.add_run(inherited)
        self.guard.snapshots[inherited.pid] = snapshot(inherited)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        original_replace = self.store.replace
        during: dict[str, object] = {}

        def _replace(run: RunRecord) -> None:
            if run.state == "RUNNING" and len(run.processes) > 1:
                during["ok"] = self.lifecycle.record_command_activity(
                    "run-existing", now=time.time()
                )
                raise OSError("disk")
            if run.state == "RUNNING" and len(run.processes) == 1:
                raise OSError("disk")
            original_replace(run)

        self.store.replace = _replace  # type: ignore[method-assign]
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing", "role": "server"},
        )
        self.assertIn("error", result, result)
        self.assertTrue(during.get("ok"), during)
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_begin_release_owner_failed_replace_arms_durable_hash(self) -> None:
        record = process(812)
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
        self.store.add(run)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.guard.terminate_results = [{"terminated": False}]
        durable_hash = hashlib.sha256(
            json.dumps(
                dataclasses.asdict(self.store.get(run.run_id)),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        armed: list[tuple[str, str]] = []

        def _arm(armed_run: RunRecord, reason: str) -> None:
            armed.append(
                (
                    hashlib.sha256(
                        json.dumps(
                            dataclasses.asdict(armed_run),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                    reason,
                )
            )

        self.lifecycle.recovery_fault_arm = _arm
        original_replace = self.store.replace

        def _replace(_run: RunRecord) -> None:
            raise OSError("disk")

        self.store.replace = _replace  # type: ignore[method-assign]
        disposition = self.lifecycle.begin_release_owner("A", "lease-A")
        self.assertTrue(disposition.terminal_event.wait(2.0))
        self.assertFalse(disposition.terminal_result.get("terminal_safe"))
        self.assertTrue(armed, armed)
        self.assertEqual(armed[0][0], durable_hash)
        self.store.replace = original_replace  # type: ignore[method-assign]
        repaired = self.lifecycle.repair_recovery_fault(
            {
                "scope": "run",
                "run_id": run.run_id,
                "launch_operation_id": run.launch_operation_id,
                "run_record_sha256": armed[0][0],
            }
        )
        self.assertTrue(repaired.get("terminal_safe"), repaired)
        self.assertEqual(self.store.get(run.run_id).state, "EXITED")

    def test_admin_reconcile_starting_does_not_publish_unaccredited_idle(self) -> None:
        record = process(1134)
        run = RunRecord(
            "run-existing",
            "A",
            "lease-A",
            "STARTING",
            "same",
            "@SameMod",
            "profiles",
            "mission",
            [record],
            "22222222-2222-4222-8222-222222222222",
            "a" * 64,
            False,
        )
        self.store.add(run)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": record.pid, "name": "DayZDiag_x64.exe"}],
        }
        result = self.lifecycle.admin_reconcile(
            "run-existing", record.pid, "incident"
        )
        current = self.store.get("run-existing")
        idle_unaccredited = (
            current.state == "RUNNING_IDLE"
            and current.launch_acknowledged is False
            and current.owner_session_id is None
        )
        self.assertFalse(idle_unaccredited, result)
        recovered = self.store.recover_after_restart()
        after = self.store.get("run-existing")
        self.assertIsNotNone(after)
        self.assertNotEqual(after.state, "STARTING")
        _ = recovered

    def test_run_not_owned_hint_does_not_order_client_launch(self) -> None:
        record = process(8087)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        self.lifecycle.release_owner("A", "lease-A")
        status, payload = self._world_spawn(state)
        self.assertEqual(payload.get("error"), "run_not_owned")
        self.assertNotEqual(status, 200)
        hint = str(payload.get("hint") or "")
        self.assertNotIn("dayz_test_run mode=client", hint)
        self.assertNotIn("mode=client run_id=", hint)

    def test_extension_refreshes_basal_as_max_with_prior_stamp(self) -> None:
        inherited = process(701)
        self.add_run(inherited)
        self.guard.snapshots[inherited.pid] = snapshot(inherited)
        old_stamp = time.time() - 5000.0
        self.lifecycle.record_command_activity("run-existing", now=old_stamp)
        stale = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(stale["activity_state"], "stale")
        launched_utc = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000000Z"
        launched = ProcessRecord(
            self.launcher.pid,
            launched_utc,
            HASH_A,
            HASH_B,
            "server",
            identity_scheme="psutil-argv-v2",
        )
        self.guard.snapshots[launched.pid] = snapshot(launched)
        result = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            self.request() | {"run_id": "run-existing", "role": "server"},
        )
        self.assertTrue(result.get("ok"), result)
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(row["activity_state"], "recent")

    def test_exited_clears_residues_so_reused_run_id_is_unknown(self) -> None:
        expected = process(self.launcher.pid)
        self.guard.snapshots[self.launcher.pid] = snapshot(expected)
        started =         self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.request()
        )
        self.assertTrue(started.get("ok"), started)
        run_id = str(started["run_id"])
        old_stamp = time.time() - 100.0
        self.lifecycle.record_command_activity(run_id, now=old_stamp)
        recent = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == run_id
        )
        self.assertEqual(recent["activity_state"], "recent")
        self.lifecycle.stop_run(IDENTITY_A, self.token_a, run_id)
        self.assertEqual(self.store.get(run_id).state, "EXITED")
        key = self.lifecycle._activity_key(run_id)
        with self.lifecycle._activity_lock:
            self.assertNotIn(key, self.lifecycle._last_activity)
            self.assertIn(key, self.lifecycle._activity_tombstone)
            self.assertNotIn(key, self.lifecycle._compensating_runs)
        self.lifecycle.manifest = RunManifestStore(self.store.paths)
        self.store = self.lifecycle.manifest
        second = process(self.launcher.pid + 1)
        self.launcher.pid = second.pid
        self.guard.snapshots[second.pid] = snapshot(second)
        restarted = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.request()
        )
        self.assertTrue(restarted.get("ok"), restarted)
        self.assertEqual(restarted.get("run_id"), run_id)
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == run_id
        )
        age = row.get("last_activity_age_s")
        inherited_old_credit = (
            row["activity_state"] == "recent"
            and isinstance(age, (int, float))
            and abs(float(age) - 100.0) < 20.0
        )
        self.assertFalse(inherited_old_credit, row)
        with self.lifecycle._activity_lock:
            stamp = self.lifecycle._last_activity.get(key)
        if stamp is not None:
            self.assertNotAlmostEqual(float(stamp), old_stamp, delta=1.0)
        if row["activity_state"] == "recent":
            self.assertLess(float(age), 30.0)
        else:
            self.assertIn(row["activity_state"], ("unknown", "stale"))

    def test_in_flight_credit_does_not_land_after_fence(self) -> None:
        record = process(8088)
        run = self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.lifecycle._capture_start_activity(run)
        state = self._bind_run()
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        credito_real = self.lifecycle.record_command_activity

        def _credito_lento(run_id: str, *, now: float | None = None) -> bool:
            if armado.is_set() and not dentro.is_set():
                dentro.set()
                puerta.wait(5.0)
            return credito_real(run_id, now=now)

        self.lifecycle.record_command_activity = _credito_lento  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _encolador() -> None:
            returned["r"] = state.enqueue_command(
                "world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server"
            )

        thread = threading.Thread(target=_encolador, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.lifecycle.release_owner("A", "lease-A")
        puerta.set()
        thread.join(timeout=5)
        self.lifecycle.record_command_activity = credito_real  # type: ignore[method-assign]
        self.assertEqual((returned.get("r") or (None, None))[0], 200)
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")
        row = next(
            item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
            if item["run_id"] == "run-existing"
        )
        self.assertEqual(row["activity_state"], "unknown")

    def test_stop_discards_queued_exec_with_audit(self) -> None:
        record = process(8089)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        registros: list[tuple[object, object]] = []

        def _exec_audit(expr, decision, main_fn, command_id) -> None:
            registros.append((decision, command_id))

        state = loopback.ServerState(
            "k",
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=_exec_audit,
        )
        bind_both_peers(state, run_id="run-existing")
        state.lifecycle = self.lifecycle
        self.lifecycle.bindings = state
        st, payload = state.enqueue_command(
            "exec_enforce", {"expr": "probe()", "main_fn": "Main"}, peer="server"
        )
        self.assertEqual(st, 200, payload)
        cid = payload.get("id")
        parado = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(isinstance(parado, dict) and parado.get("ok") is True, parado)
        self.assertIn(("discarded", cid), registros)

    def test_unreadable_manifest_rejects_enqueue_without_credit(self) -> None:
        record = process(8090)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        get_real = self.store.get
        before = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]

        def _get_roto(run_id: str):
            raise RuntimeError("manifest_unreadable")

        self.store.get = _get_roto  # type: ignore[method-assign]
        try:
            st, payload = state.enqueue_command("camera_get", {}, peer="client")
        finally:
            self.store.get = get_real  # type: ignore[method-assign]
        after = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        self.assertNotEqual(st, 200, payload)
        self.assertEqual(payload.get("error"), "run_state_unavailable")
        self.assertEqual(len(after), len(before))

    def test_store_result_without_instance_after_retire_is_rejected(self) -> None:
        record = process(8091)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        st_enq, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(st_enq, 200, payload)
        cid = payload.get("id")
        st_poll, entregado = accredited_poll(state, "client")
        self.assertEqual(st_poll, 200, entregado)
        ids = [
            command.get("id")
            for command in (entregado.get("commands") or [])
        ]
        self.assertIn(cid, ids)
        parado = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(isinstance(parado, dict) and parado.get("ok") is True, parado)
        st_res, res = state.store_result(
            {"id": cid, "ok": True, "result": {"late": True}}, instance=None
        )
        self.assertNotEqual(st_res, 200, res)

    def _dispatch_client_command(self, state: loopback.ServerState) -> int:
        st_enq, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(st_enq, 200, payload)
        cid = payload.get("id")
        self.assertIsInstance(cid, int)
        st_poll, delivered = accredited_poll(state, "client")
        self.assertEqual(st_poll, 200, delivered)
        ids = [
            command.get("id")
            for command in (delivered.get("commands") or [])
        ]
        self.assertIn(cid, ids)
        return int(cid)

    def _assert_late_result_rejected_without_diagnostic(
        self, state: loopback.ServerState, cid: int, run_id: str
    ) -> None:
        st_res, res = state.store_result(
            {"id": cid, "ok": True, "result": {"late": True}}, instance=None
        )
        self.assertNotEqual(st_res, 200, res)
        self.assertFalse(
            any(item.run_id == run_id for item in self.lifecycle._retired_diagnostics)
        )

    def _replace_failing_exited(self):
        real = self.lifecycle.manifest.replace

        def _wrapped(run: RunRecord) -> None:
            if getattr(run, "state", None) == "EXITED":
                raise OSError("disco lleno")
            return real(run)

        return real, _wrapped

    def test_persist_failure_on_reap_retires_binding_and_rejects_late_result(
        self,
    ) -> None:
        record = process(9401)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        cid = self._dispatch_client_command(state)
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        real, wrapped = self._replace_failing_exited()
        self.lifecycle.manifest.replace = wrapped  # type: ignore[method-assign]
        try:
            reaped = self.lifecycle.reap_dead_runs()
        finally:
            self.lifecycle.manifest.replace = real  # type: ignore[method-assign]
        self.assertEqual(reaped, [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING")
        self._assert_late_result_rejected_without_diagnostic(
            state, cid, "run-existing"
        )

    def test_persist_failure_on_begin_release_retires_binding_and_rejects_late_result(
        self,
    ) -> None:
        record = process(9402)
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
        self.store.add(run)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run(run.run_id)
        cid = self._dispatch_client_command(state)
        real, wrapped = self._replace_failing_exited()
        self.lifecycle.manifest.replace = wrapped  # type: ignore[method-assign]
        try:
            disposition = self.lifecycle.begin_release_owner("A", "lease-A")
            self.assertTrue(disposition.terminal_event.wait(1.0))
        finally:
            self.lifecycle.manifest.replace = real  # type: ignore[method-assign]
        self.assertFalse(disposition.terminal_result.get("terminal_safe"))
        self.assertEqual(self.store.get(run.run_id).state, "RUNNING")
        self._assert_late_result_rejected_without_diagnostic(state, cid, run.run_id)

    def test_persist_failure_on_repair_recovery_retires_binding_and_rejects_late_result(
        self,
    ) -> None:
        record = process(9403)
        run = RunRecord(
            "11111111-1111-4111-8111-333333333333",
            "A",
            "lease-A",
            "RUNNING",
            "recoverable",
            "@mod",
            "profiles",
            "mission",
            [record],
            "22222222-2222-4222-8222-444444444444",
            "a" * 64,
            False,
        )
        self.store.add(run)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run(run.run_id)
        cid = self._dispatch_client_command(state)
        run = self.store.get(run.run_id)
        assert run is not None
        run.state = "UNRECONCILED"
        self.store.replace(run)
        run_hash = hashlib.sha256(
            json.dumps(
                dataclasses.asdict(run),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        real, wrapped = self._replace_failing_exited()
        self.lifecycle.manifest.replace = wrapped  # type: ignore[method-assign]
        try:
            result = self.lifecycle.repair_recovery_fault(
                {
                    "scope": "run",
                    "run_id": run.run_id,
                    "launch_operation_id": run.launch_operation_id,
                    "run_record_sha256": run_hash,
                }
            )
        finally:
            self.lifecycle.manifest.replace = real  # type: ignore[method-assign]
        self.assertFalse(result.get("terminal_safe"), result)
        self.assertEqual(self.store.get(run.run_id).state, "UNRECONCILED")
        self._assert_late_result_rejected_without_diagnostic(state, cid, run.run_id)

    def test_persist_failure_on_admin_reconcile_retires_binding_and_rejects_late_result(
        self,
    ) -> None:
        record = process(9404)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        cid = self._dispatch_client_command(state)
        run = self.store.get("run-existing")
        assert run is not None
        run.state = "UNRECONCILED"
        run.processes = []
        self.store.replace(run)
        real, wrapped = self._replace_failing_exited()
        self.lifecycle.manifest.replace = wrapped  # type: ignore[method-assign]
        try:
            result = self.lifecycle.admin_reconcile(
                "run-existing", None, "operator cleanup", empty=True
            )
        finally:
            self.lifecycle.manifest.replace = real  # type: ignore[method-assign]
        self.assertEqual(result.get("error"), "manifest_failed")
        self.assertEqual(self.store.get("run-existing").state, "UNRECONCILED")
        self._assert_late_result_rejected_without_diagnostic(
            state, cid, "run-existing"
        )

    def test_persist_failure_on_manifest_recovery_retires_binding_and_rejects_late_result(
        self,
    ) -> None:
        record = process(9405)
        run = RunRecord(
            "11111111-1111-4111-8111-555555555555",
            "A",
            "lease-A",
            "RUNNING",
            "recoverable",
            "@mod",
            "profiles",
            "mission",
            [record],
            "22222222-2222-4222-8222-666666666666",
            "a" * 64,
            False,
        )
        self.store.add(run)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run(run.run_id)
        cid = self._dispatch_client_command(state)
        raw = (
            json.dumps(
                {"version": 1, "runs": [dataclasses.asdict(run)]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        original = RunManifestStore.replace

        def _wrapped(store, target):
            if getattr(target, "state", None) == "EXITED":
                raise OSError("disco lleno")
            return original(store, target)

        RunManifestStore.replace = _wrapped  # type: ignore[method-assign]
        try:
            result = self.lifecycle.repair_manifest_recovery(raw)
        finally:
            RunManifestStore.replace = original  # type: ignore[method-assign]
        self.assertFalse(result.get("terminal_safe"), result)
        self._assert_late_result_rejected_without_diagnostic(state, cid, run.run_id)

    def test_persist_failure_on_stop_retires_binding_and_rejects_late_result(
        self,
    ) -> None:
        record = process(9406)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        cid = self._dispatch_client_command(state)
        real, wrapped = self._replace_failing_exited()
        self.lifecycle.manifest.replace = wrapped  # type: ignore[method-assign]
        try:
            result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, "run-existing")
        finally:
            self.lifecycle.manifest.replace = real  # type: ignore[method-assign]
        self.assertIn("error", result)
        self.assertNotEqual(self.store.get("run-existing").state, "EXITED")
        self._assert_late_result_rejected_without_diagnostic(
            state, cid, "run-existing"
        )

    def test_terminal_cleanup_keeps_frontier_so_late_credit_does_not_reappear(
        self,
    ) -> None:
        record = process(9407)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        credito_real = self.lifecycle.record_command_activity

        def _credito_lento(run_id: str, *, now: float | None = None) -> bool:
            if armado.is_set() and not dentro.is_set():
                dentro.set()
                puerta.wait(5.0)
            return credito_real(run_id, now=now)

        self.lifecycle.record_command_activity = _credito_lento  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _encolador() -> None:
            returned["r"] = state.enqueue_command("camera_get", {}, peer="client")

        thread = threading.Thread(target=_encolador, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        reaped = self.lifecycle.reap_dead_runs()
        puerta.set()
        thread.join(timeout=5)
        self.lifecycle.record_command_activity = credito_real  # type: ignore[method-assign]
        self.assertEqual(reaped, ["run-existing"])
        self.assertEqual(self.store.get("run-existing").state, "EXITED")
        key = self.lifecycle._activity_key("run-existing")
        with self.lifecycle._activity_lock:
            self.assertNotIn(key, self.lifecycle._last_activity)
            self.assertIn(key, self.lifecycle._activity_tombstone)

    def test_tombstone_raise_does_not_fail_open_idle_transition(self) -> None:
        record = process(9408)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        real_tomb = self.lifecycle.tombstone_run_activity

        def _boom(run_id: str, *, now: float | None = None) -> None:
            raise RuntimeError("tombstone_failed")

        self.lifecycle.tombstone_run_activity = _boom  # type: ignore[method-assign]
        try:
            with self.assertRaises(RuntimeError):
                self.lifecycle.release_owner("A", "lease-A")
        finally:
            self.lifecycle.tombstone_run_activity = real_tomb  # type: ignore[method-assign]
        self.assertEqual(self.store.get("run-existing").state, "RUNNING")
        self.assertNotIn("run-existing", state._fenced_runs)

    def test_malformed_durable_state_rejects_enqueue_without_credit(self) -> None:
        record = process(9409)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        get_real = self.store.get
        before = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]

        def _get_malformado(run_id: str):
            return types.SimpleNamespace(run_id=run_id, state=None)

        self.store.get = _get_malformado  # type: ignore[method-assign]
        try:
            st, payload = state.enqueue_command("camera_get", {}, peer="client")
        finally:
            self.store.get = get_real  # type: ignore[method-assign]
        after = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        self.assertNotEqual(st, 200, payload)
        self.assertEqual(payload.get("error"), "run_state_unavailable")
        self.assertEqual(len(after), len(before))

    def test_missing_manifest_getter_rejects_enqueue_without_credit(self) -> None:
        record = process(9410)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        before = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        original = self.lifecycle.manifest
        self.lifecycle.manifest = object()  # type: ignore[assignment]
        try:
            st, payload = state.enqueue_command("camera_get", {}, peer="client")
        finally:
            self.lifecycle.manifest = original
        after = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        self.assertNotEqual(st, 200, payload)
        self.assertEqual(payload.get("error"), "run_state_unavailable")
        self.assertEqual(len(after), len(before))

    def test_exec_enforce_without_lifecycle_is_run_state_unavailable(self) -> None:
        record = process(9411)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = loopback.ServerState(
            "k",
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=lambda *_args: None,
        )
        bind_both_peers(state, run_id="run-existing")
        state.lifecycle = None
        st, payload = state.enqueue_command(
            "exec_enforce",
            {"expr": "probe()", "main_fn": "Main"},
            peer="server",
        )
        self.assertNotEqual(st, 200, payload)
        self.assertEqual(st, 503, payload)
        self.assertEqual(payload.get("error"), "run_state_unavailable")

    def test_poll_without_lifecycle_holds_bound_commands(self) -> None:
        record = process(9412)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        st, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(st, 200, payload)
        state.lifecycle = None
        st_poll, delivered = accredited_poll(state, "client")
        self.assertEqual(st_poll, 200, delivered)
        self.assertEqual(delivered.get("commands"), [])

    def test_failed_audit_after_terminal_frontier_does_not_recreate_unknown(
        self,
    ) -> None:
        record = process(9413)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien: dict[str, object] = {}
        audit_real = self.lifecycle.audit

        def _audit_lento_fallido(payload: dict[str, object]) -> bool:
            if (
                armado.is_set()
                and not dentro.is_set()
                and isinstance(payload, dict)
                and payload.get("event") == "run_command_activity"
            ):
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
                return False
            return audit_real(payload)

        self.lifecycle.audit = _audit_lento_fallido  # type: ignore[method-assign]
        returned: dict[str, object] = {}

        def _encolador() -> None:
            returned["r"] = state.enqueue_command("camera_get", {}, peer="client")

        thread = threading.Thread(target=_encolador, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertEqual(quien.get("ident"), thread.ident)
        self.assertNotIn("r", returned)
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        reaped = self.lifecycle.reap_dead_runs()
        puerta.set()
        thread.join(timeout=5)
        self.lifecycle.audit = audit_real  # type: ignore[method-assign]
        self.assertEqual(reaped, ["run-existing"])
        self.assertEqual(self.store.get("run-existing").state, "EXITED")
        key = self.lifecycle._activity_key("run-existing")
        with self.lifecycle._activity_lock:
            self.assertNotIn(key, self.lifecycle._activity_unknown)

    def test_failed_audit_after_frontier_still_leaves_sticky(self) -> None:
        record = process(9414)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.lifecycle.tombstone_run_activity("run-existing", now=100.0)
        self.audit.fail_events.add("run_command_activity")
        recorded = self.lifecycle.record_command_activity("run-existing", now=200.0)
        self.assertFalse(recorded)
        key = self.lifecycle._activity_key("run-existing")
        with self.lifecycle._activity_lock:
            self.assertIn(key, self.lifecycle._activity_unknown)

    def test_adopt_after_failed_terminal_persist_directs_to_reap(self) -> None:
        record = process(9415)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        released = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(released, ["run-existing"])
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        real = self.lifecycle.manifest.replace
        fallos = {"n": 0}

        def _replace_roto(run: RunRecord) -> None:
            if getattr(run, "state", None) == "EXITED" and fallos["n"] == 0:
                fallos["n"] += 1
                raise OSError("disco lleno")
            return real(run)

        self.lifecycle.manifest.replace = _replace_roto  # type: ignore[method-assign]
        try:
            reaped = self.lifecycle.reap_dead_runs()
        finally:
            self.lifecycle.manifest.replace = real  # type: ignore[method-assign]
        self.assertEqual(reaped, [])
        self.assertEqual(fallos["n"], 1)
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertNotEqual(adopted.get("ok"), True, adopted)
        self.assertEqual(adopted.get("error"), "run_processes_gone")
        hint = adopted.get("hint")
        self.assertIsInstance(hint, str)
        self.assertIn("reap", str(hint).lower())
        st, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertNotEqual(st, 200, payload)
        reaped_again = self.lifecycle.reap_dead_runs()
        self.assertEqual(reaped_again, ["run-existing"])
        self.assertEqual(self.store.get("run-existing").state, "EXITED")

    def _fresh_lifecycle_after_restart(self, generation: str = "gen-B"):
        manifest2 = RunManifestStore(self.store.paths)
        manifest2.recover_after_restart()
        state2 = loopback.ServerState("k")
        life2 = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=manifest2,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: self.probe_result,
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            bindings=state2,
            daemon_generation=generation,
        )
        state2.lifecycle = life2
        return life2, manifest2, state2

    def test_adopt_rejects_when_all_registered_processes_are_foreign(self) -> None:
        rec = process(9422)
        self.add_run(rec, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[rec.pid] = snapshot(rec) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertNotEqual(adopted.get("ok"), True, adopted)
        self.assertEqual(adopted.get("error"), "run_processes_gone")
        self.assertEqual(adopted.get("_http_status"), 409)
        hint = adopted.get("hint")
        self.assertIsInstance(hint, str)
        self.assertIn("reap", str(hint).lower())
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_adopt_declares_dispatchable_true_after_release(self) -> None:
        record = process(9423)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        state = self._bind_run()
        released = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(released, ["run-existing"])
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(adopted.get("ok"), adopted)
        self.assertIs(adopted.get("dispatchable"), True, adopted)
        self.assertNotIn("hint", adopted)
        after, after_payload = self._world_spawn(state)
        self.assertEqual(after, 200, after_payload)

    def test_adopt_declares_dispatchable_false_without_bindings(self) -> None:
        record = process(9424)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[record.pid] = snapshot(record)
        self.assertIsNone(self.lifecycle.bindings)
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(adopted.get("ok"), adopted)
        self.assertIs(adopted.get("dispatchable"), False, adopted)
        hint = adopted.get("hint")
        self.assertIsInstance(hint, str)
        self.assertIn("restart", str(hint).lower())
        self.assertIn("stop_run", str(hint))

    def test_adopt_starting_binding_is_not_dispatchable(self) -> None:
        record = process(9420)
        self.add_run(record, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[record.pid] = snapshot(record)
        state = loopback.ServerState("k")
        with state._lock:
            state._bindings["starting"] = Binding(
                instance="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                run_id="run-existing",
                role="client",
                epoch=1,
                pid=None,
                creation_time_utc=None,
                state=BINDING_STARTING,
            )
        state.lifecycle = self.lifecycle
        self.lifecycle.bindings = state
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(adopted.get("ok"), adopted)
        self.assertIs(adopted.get("dispatchable"), False, adopted)
        self.assertIsInstance(adopted.get("hint"), str)

    def test_adopt_after_restart_rejects_gone_process(self) -> None:
        record = process(9421)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        released = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(released, ["run-existing"])
        self._dead(record.pid)
        real = self.lifecycle.manifest.replace
        fallos = {"n": 0}

        def _replace_roto(run: RunRecord) -> None:
            if getattr(run, "state", None) == "EXITED" and fallos["n"] == 0:
                fallos["n"] += 1
                raise OSError("disco lleno")
            return real(run)

        self.lifecycle.manifest.replace = _replace_roto  # type: ignore[method-assign]
        try:
            reaped = self.lifecycle.reap_dead_runs()
        finally:
            self.lifecycle.manifest.replace = real  # type: ignore[method-assign]
        self.assertEqual(reaped, [])
        self.assertEqual(fallos["n"], 1)
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")
        life2, manifest2, _state2 = self._fresh_lifecycle_after_restart()
        adopted = life2.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertNotEqual(adopted.get("ok"), True, adopted)
        self.assertEqual(adopted.get("error"), "run_processes_gone")
        self.assertEqual(adopted.get("_http_status"), 409)
        hint = adopted.get("hint")
        self.assertIsInstance(hint, str)
        self.assertIn("reap", str(hint).lower())
        self.assertEqual(manifest2.get("run-existing").state, "RUNNING_IDLE")

    def test_adopt_after_restart_live_process_declares_not_dispatchable(self) -> None:
        record = process(9425)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self._bind_run()
        released = self.lifecycle.release_owner("A", "lease-A")
        self.assertEqual(released, ["run-existing"])
        life2, _manifest2, state2 = self._fresh_lifecycle_after_restart()
        adopted = life2.adopt_run(IDENTITY_A, self.token_a, "run-existing")
        self.assertTrue(adopted.get("ok"), adopted)
        self.assertIs(adopted.get("dispatchable"), False, adopted)
        hint = adopted.get("hint")
        self.assertIsInstance(hint, str)
        self.assertIn("restart", str(hint).lower())
        self.assertIn("stop_run", str(hint))
        st, payload = state2.enqueue_command(
            "world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server"
        )
        self.assertNotEqual(st, 200, payload)


GEN_LAUNCH = "daemon_generation_at_launch"
GEN_CURRENT = "daemon_generation_current"
GEN_CHANGED = "generation_changed"
GEN_FIELDS = (GEN_LAUNCH, GEN_CURRENT, GEN_CHANGED)
DIAG_KEY = "retired_run_diagnostics"
DIAG_CAP = 32
DIAG_KEYS = frozenset(
    {
        "run_id",
        GEN_LAUNCH,
        GEN_CURRENT,
        GEN_CHANGED,
        "event",
        "reason",
        "decision",
        "state",
    }
)


class RetiredRunDiagnosticsAndGenerationTest(unittest.TestCase):
    """Lote H: generation projection, in-memory retired-run ring, six feed paths."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.paths = RuntimePaths(
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
        self.store = RunManifestStore(self.paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.generation = "gen-lote-h-test"
        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            daemon_generation=self.generation,
        )
        self.bridge = FakeBridgeBindings()
        self.lifecycle.bridge_probe = self.bridge.status_snapshot

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _request(self) -> dict[str, object]:
        return {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
        }

    def _start(self) -> str:
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request())
        self.assertTrue(result.get("ok"), result)
        return str(result["run_id"])

    def _prune_exited(self) -> None:
        self.lifecycle.manifest = RunManifestStore(self.paths)
        self.lifecycle._invalidate_box_cache()

    def _row(self, payload: dict[str, object], run_id: str) -> dict[str, object]:
        for item in payload.get("runs") or []:
            if isinstance(item, dict) and item.get("run_id") == run_id:
                return item
        self.fail(f"run {run_id} missing from {payload.get('runs')}")

    def _diags(self, payload: dict[str, object], run_id: str) -> list[dict[str, object]]:
        raw = payload.get(DIAG_KEY)
        self.assertIsInstance(raw, list, f"{DIAG_KEY} missing or not a list: {raw!r}")
        return [
            item
            for item in raw
            if isinstance(item, dict) and item.get("run_id") == run_id
        ]

    def test_status_and_public_status_project_generation_on_present_run(self) -> None:
        run_id = self._start()
        status = self.lifecycle.status(IDENTITY_A)
        public = self.lifecycle.public_status()
        status_row = self._row(status, run_id)
        public_row = self._row(public, run_id)
        for row in (status_row, public_row):
            self.assertEqual(row[GEN_LAUNCH], self.generation)
            self.assertEqual(row[GEN_CURRENT], self.generation)
            self.assertIs(row[GEN_CHANGED], False)
        self.assertEqual(
            {field: status_row[field] for field in GEN_FIELDS},
            {field: public_row[field] for field in GEN_FIELDS},
        )
        self.assertIsInstance(status.get(DIAG_KEY), list)
        self.assertIsInstance(public.get(DIAG_KEY), list)

    def test_generation_changed_is_null_never_false_without_launch_generation(self) -> None:
        record = process(9101)
        self.store.add(
            RunRecord(
                "legacy-run",
                "A",
                "lease-A",
                "RUNNING",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [record],
            )
        )
        row = self._row(self.lifecycle.status(IDENTITY_A), "legacy-run")
        self.assertIsNone(row[GEN_LAUNCH])
        self.assertEqual(row[GEN_CURRENT], self.generation)
        self.assertIsNone(row[GEN_CHANGED])

    def test_from_payload_round_trip_keeps_launch_generation(self) -> None:
        run_id = self._start()
        stored = self.lifecycle.manifest.get(run_id)
        self.assertEqual(getattr(stored, GEN_LAUNCH), self.generation)
        reloaded = RunManifestStore(self.paths).get(run_id)
        self.assertEqual(getattr(reloaded, GEN_LAUNCH), self.generation)

    def test_legacy_runs_json_without_field_loads_and_survives_get_list_replace(
        self,
    ) -> None:
        """§2(D): a real on-disk manifest written by the previous tree."""
        record = process(9102)
        payload = {
            "version": 1,
            "runs": [
                {
                    "run_id": "legacy-disk",
                    "owner_session_id": "A",
                    "owner_lease_id": "lease-A",
                    "state": "RUNNING",
                    "label": "gate",
                    "mod": "@SameMod",
                    "profiles": "profiles",
                    "mission": "mission",
                    "processes": [dataclasses.asdict(record)],
                    "launch_operation_id": None,
                    "launch_request_sha256": None,
                    "launch_acknowledged": True,
                }
            ],
        }
        self.assertNotIn(GEN_LAUNCH, payload["runs"][0])
        self.paths.runs_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.runs_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        store = RunManifestStore(self.paths)
        loaded = store.get("legacy-disk")
        self.assertIsNotNone(loaded)
        self.assertIsNone(getattr(loaded, GEN_LAUNCH, "missing"))
        listed = store.list_runs()
        self.assertEqual(len(listed), 1)
        self.assertIsNone(getattr(listed[0], GEN_LAUNCH, "missing"))
        loaded.label = "relabeled"
        store.replace(loaded)
        again = store.get("legacy-disk")
        self.assertEqual(again.label, "relabeled")
        self.assertIsNone(getattr(again, GEN_LAUNCH, "missing"))
        on_disk = json.loads(self.paths.runs_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["runs"][0]["label"], "relabeled")

    def test_stop_run_feeds_the_retired_diagnostic_ring(self) -> None:
        run_id = self._start()
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run_id)
        self.assertTrue(stopped.get("ok"), stopped)
        self._prune_exited()
        hits = self._diags(self.lifecycle.status(IDENTITY_A), run_id)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["event"], "lifecycle_stop_outcome")
        self.assertEqual(hits[0]["reason"], "stopped")
        self.assertEqual(hits[0]["decision"], "stopped")
        self.assertEqual(hits[0]["state"], "EXITED")
        self.assertEqual(set(hits[0]), DIAG_KEYS)

    def test_reap_run_feeds_the_retired_diagnostic_ring(self) -> None:
        record = process(9201)
        self.store.add(
            RunRecord(
                "reap-run",
                None,
                None,
                "RUNNING_IDLE",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [record],
            )
        )
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["reap-run"])
        self._prune_exited()
        hits = self._diags(self.lifecycle.status(IDENTITY_A), "reap-run")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["event"], "run_reaped")
        self.assertEqual(hits[0]["decision"], "reaped")
        self.assertEqual(hits[0]["state"], "EXITED")
        self.assertEqual(set(hits[0]), DIAG_KEYS)

    def test_begin_release_owner_feeds_the_retired_diagnostic_ring(self) -> None:
        record = process(9202)
        self.guard.snapshots[record.pid] = snapshot(record)
        run_id = "11111111-1111-4111-8111-111111111111"
        self.store.add(
            RunRecord(
                run_id,
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
        self.assertTrue(disposition.terminal_event.wait(1.0))
        self.assertTrue(disposition.terminal_result.get("terminal_safe"))
        self._prune_exited()
        hits = self._diags(self.lifecycle.status(IDENTITY_A), run_id)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["decision"], "released")
        self.assertEqual(hits[0]["state"], "EXITED")
        self.assertEqual(set(hits[0]), DIAG_KEYS)

    def test_repair_recovery_fault_feeds_the_retired_diagnostic_ring(self) -> None:
        record = process(9203)
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
            json.dumps(
                dataclasses.asdict(run),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        result = self.lifecycle.repair_recovery_fault(
            {
                "scope": "run",
                "run_id": run.run_id,
                "launch_operation_id": run.launch_operation_id,
                "run_record_sha256": run_hash,
            }
        )
        self.assertTrue(result.get("terminal_safe"), result)
        self._prune_exited()
        hits = self._diags(self.lifecycle.status(IDENTITY_A), run.run_id)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["event"], "lifecycle_recovery_repair")
        self.assertEqual(hits[0]["state"], "EXITED")
        self.assertEqual(set(hits[0]), DIAG_KEYS)

    def test_repair_manifest_recovery_feeds_the_retired_diagnostic_ring(self) -> None:
        record = process(9204)
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
        self.assertTrue(result.get("terminal_safe"), result)
        hits = self._diags(self.lifecycle.status(IDENTITY_A), run.run_id)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["event"], "lifecycle_manifest_recovery")
        self.assertEqual(hits[0]["state"], "EXITED")
        self.assertEqual(set(hits[0]), DIAG_KEYS)

    def test_admin_reconcile_feeds_the_retired_diagnostic_ring(self) -> None:
        record = process(9205)
        self.store.add(
            RunRecord(
                "reconcile-run",
                None,
                None,
                "RUNNING_IDLE",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [record],
            )
        )
        self.guard.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        result = self.lifecycle.admin_reconcile("reconcile-run", 9205, "incident")
        self.assertEqual(result.get("state"), "EXITED", result)
        self._prune_exited()
        hits = self._diags(self.lifecycle.status(IDENTITY_A), "reconcile-run")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["event"], "admin_reconcile")
        self.assertEqual(hits[0]["state"], "EXITED")
        self.assertEqual(set(hits[0]), DIAG_KEYS)

    def test_ring_caps_at_32_keeping_the_most_recent(self) -> None:
        total = 40
        ids = [f"seed-{index:02d}" for index in range(total)]
        for index, run_id in enumerate(ids):
            pid = 30000 + index
            record = process(pid)
            self.store.add(
                RunRecord(
                    run_id,
                    None,
                    None,
                    "RUNNING_IDLE",
                    "gate",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [record],
                )
            )
            self.guard.snapshots[pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
        reaped = self.lifecycle.reap_dead_runs()
        self.assertEqual(len(reaped), total)
        raw = self.lifecycle.status(IDENTITY_A).get(DIAG_KEY)
        self.assertIsInstance(raw, list)
        published = [item.get("run_id") for item in raw if isinstance(item, dict)]
        self.assertLessEqual(len(raw), DIAG_CAP)
        self.assertEqual(set(published), set(ids[-DIAG_CAP:]))
        self.assertFalse(set(published) & set(ids[:-DIAG_CAP]))

    def test_diagnostic_has_no_timestamp_or_path_keys(self) -> None:
        run_id = self._start()
        self.lifecycle.stop_run(IDENTITY_A, self.token_a, run_id)
        hits = self._diags(self.lifecycle.status(IDENTITY_A), run_id)
        self.assertEqual(len(hits), 1)
        self.assertEqual(set(hits[0]), DIAG_KEYS)
        forbidden = ("time", "stamp", "path", "dir", "file", "utc", "epoch")
        for key, value in hits[0].items():
            lowered = key.casefold()
            if lowered in {GEN_LAUNCH, GEN_CURRENT}:
                continue
            for token in forbidden:
                self.assertNotIn(token, lowered, key)
            if isinstance(value, str):
                self.assertNotIn("\\", value)
                self.assertFalse(value.startswith("/"))

    def test_failed_stop_does_not_enter_the_ring(self) -> None:
        run_id = self._start()
        self.guard.snapshots[self.launcher.pid] = {
            "error": "guard_unavailable",
            "exit_code": 3,
        }
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run_id)
        row = self._row(self.lifecycle.status(IDENTITY_A), run_id)
        self.assertEqual(row["state"], "UNRECONCILED")
        self.assertEqual(self._diags(self.lifecycle.status(IDENTITY_A), run_id), [])
        self.assertIn("error", result)

    def test_new_lifecycle_publishes_an_empty_ring(self) -> None:
        status = self.lifecycle.status(IDENTITY_A)
        public = self.lifecycle.public_status()
        self.assertEqual(status.get(DIAG_KEY), [])
        self.assertEqual(public.get(DIAG_KEY), [])

class RetiredRunRingCommitAndSnapshotTest(RetiredRunDiagnosticsAndGenerationTest):
    """Lote H ronda 2: persistir then publicar, dedup, coherent read, closed reason."""

    def _ring_raw(self) -> list:
        return list(self.lifecycle._retired_diagnostics)

    def _wrap_replace_fail(self):
        real = self.lifecycle.manifest.replace
        calls = {"n": 0}

        def _wrapped(run):
            calls["n"] += 1
            ring_ids = [item.run_id for item in self._ring_raw()]
            calls.setdefault("ring_at_replace", []).append(list(ring_ids))
            raise OSError("disco lleno")

        return real, _wrapped, calls

    def test_a_persist_failure_on_reap_publishes_nothing_and_keeps_the_run(self) -> None:
        record = process(9301)
        self.store.add(
            RunRecord(
                "reap-fail",
                None,
                None,
                "RUNNING_IDLE",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [record],
            )
        )
        self.guard.snapshots[record.pid] = {"error": "process_not_found", "exit_code": 4}
        real, wrapped, calls = self._wrap_replace_fail()
        self.lifecycle.manifest.replace = wrapped
        try:
            reaped = self.lifecycle.reap_dead_runs()
        finally:
            self.lifecycle.manifest.replace = real
        self.assertEqual(reaped, [])
        self.assertGreaterEqual(calls["n"], 1)
        self.assertEqual(self.lifecycle.manifest.get("reap-fail").state, "RUNNING_IDLE")
        self.assertEqual(self._diags(self.lifecycle.status(IDENTITY_A), "reap-fail"), [])
        self.assertEqual(self._ring_raw(), [])

    def test_a_persist_failure_on_stop_publishes_nothing_and_keeps_the_run(self) -> None:
        run_id = self._start()
        real, wrapped, calls = self._wrap_replace_fail()
        self.lifecycle.manifest.replace = wrapped
        try:
            result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run_id)
        finally:
            self.lifecycle.manifest.replace = real
        self.assertGreaterEqual(calls["n"], 1)
        self.assertIn("error", result)
        row = self._row(self.lifecycle.status(IDENTITY_A), run_id)
        self.assertNotEqual(row["state"], "EXITED")
        self.assertEqual(self._diags(self.lifecycle.status(IDENTITY_A), run_id), [])
        self.assertFalse(any(item.run_id == run_id for item in self._ring_raw()))

    def test_a_persist_failure_on_repair_recovery_publishes_nothing(self) -> None:
        record = process(9302)
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
            json.dumps(
                dataclasses.asdict(run),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        real, wrapped, calls = self._wrap_replace_fail()
        self.lifecycle.manifest.replace = wrapped
        try:
            result = self.lifecycle.repair_recovery_fault(
                {
                    "scope": "run",
                    "run_id": run.run_id,
                    "launch_operation_id": run.launch_operation_id,
                    "run_record_sha256": run_hash,
                }
            )
        finally:
            self.lifecycle.manifest.replace = real
        self.assertGreaterEqual(calls["n"], 1)
        self.assertFalse(result.get("terminal_safe"))
        self.assertEqual(self.lifecycle.manifest.get(run.run_id).state, "UNRECONCILED")
        self.assertEqual(self._diags(self.lifecycle.status(IDENTITY_A), run.run_id), [])
        self.assertEqual(self._ring_raw(), [])

    def test_a_persist_failure_on_admin_reconcile_publishes_nothing(self) -> None:
        self.store.add(
            RunRecord(
                "reconcile-fail",
                None,
                None,
                "UNRECONCILED",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [],
            )
        )
        real, wrapped, calls = self._wrap_replace_fail()
        self.lifecycle.manifest.replace = wrapped
        try:
            result = self.lifecycle.admin_reconcile(
                "reconcile-fail", None, "operator cleanup", empty=True
            )
        finally:
            self.lifecycle.manifest.replace = real
        self.assertGreaterEqual(calls["n"], 1)
        self.assertEqual(result.get("error"), "manifest_failed")
        self.assertEqual(self.lifecycle.manifest.get("reconcile-fail").state, "UNRECONCILED")
        self.assertEqual(self._diags(self.lifecycle.status(IDENTITY_A), "reconcile-fail"), [])
        self.assertEqual(self._ring_raw(), [])

    def test_a_persist_failure_on_begin_release_owner_publishes_nothing(self) -> None:
        record = process(9303)
        self.guard.snapshots[record.pid] = snapshot(record)
        run_id = "11111111-1111-4111-8111-aaaaaaaaaaaa"
        self.store.add(
            RunRecord(
                run_id,
                "A",
                "lease-A",
                "RUNNING",
                "recoverable",
                "@mod",
                "profiles",
                "mission",
                [record],
                "22222222-2222-4222-8222-bbbbbbbbbbbb",
                "a" * 64,
                False,
            )
        )
        real, wrapped, calls = self._wrap_replace_fail()
        self.lifecycle.manifest.replace = wrapped
        try:
            disposition = self.lifecycle.begin_release_owner("A", "lease-A")
            self.assertTrue(disposition.terminal_event.wait(1.0))
        finally:
            self.lifecycle.manifest.replace = real
        self.assertGreaterEqual(calls["n"], 1)
        self.assertFalse(disposition.terminal_result.get("terminal_safe"))
        self.assertEqual(self.lifecycle.manifest.get(run_id).state, "RUNNING")
        self.assertEqual(self._diags(self.lifecycle.status(IDENTITY_A), run_id), [])
        self.assertEqual(self._ring_raw(), [])

    def test_a_persist_failure_on_repair_manifest_recovery_publishes_nothing(self) -> None:
        record = process(9304)
        self.guard.snapshots[record.pid] = snapshot(record)
        run = RunRecord(
            "11111111-1111-4111-8111-cccccccccccc",
            "A",
            "lease-A",
            "RUNNING",
            "recoverable",
            "@mod",
            "profiles",
            "mission",
            [record],
            "22222222-2222-4222-8222-dddddddddddd",
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
        original = RunManifestStore.replace
        calls = {"n": 0}

        def _wrapped(store, target):
            calls["n"] += 1
            if target.state == "EXITED":
                raise OSError("disco lleno")
            return original(store, target)

        RunManifestStore.replace = _wrapped
        try:
            result = self.lifecycle.repair_manifest_recovery(raw)
        finally:
            RunManifestStore.replace = original
        self.assertGreaterEqual(calls["n"], 1)
        self.assertFalse(result.get("terminal_safe"))
        self.assertEqual(self._diags(self.lifecycle.status(IDENTITY_A), run.run_id), [])
        self.assertFalse(any(item.run_id == run.run_id for item in self._ring_raw()))

    def test_a_order_ring_still_empty_at_the_replace_of_a_healthy_reap(self) -> None:
        record = process(9305)
        self.store.add(
            RunRecord(
                "reap-order",
                None,
                None,
                "RUNNING_IDLE",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [record],
            )
        )
        self.guard.snapshots[record.pid] = {"error": "process_not_found", "exit_code": 4}
        seen: list[list[str]] = []
        real = self.lifecycle.manifest.replace

        def _wrapped(run):
            seen.append([item.run_id for item in self._ring_raw()])
            return real(run)

        self.lifecycle.manifest.replace = _wrapped
        try:
            self.assertEqual(self.lifecycle.reap_dead_runs(), ["reap-order"])
        finally:
            self.lifecycle.manifest.replace = real
        self.assertTrue(seen)
        self.assertFalse(any("reap-order" in frame for frame in seen))
        self.assertEqual(len(self._diags(self.lifecycle.status(IDENTITY_A), "reap-order")), 1)

    def test_b_failed_reaps_do_not_evict_a_prior_legitimate_diagnostic(self) -> None:
        prior = process(9310)
        self.store.add(
            RunRecord(
                "real-prior",
                None,
                None,
                "RUNNING_IDLE",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [prior],
            )
        )
        self.guard.snapshots[prior.pid] = {"error": "process_not_found", "exit_code": 4}
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["real-prior"])
        self.assertEqual(len(self._diags(self.lifecycle.status(IDENTITY_A), "real-prior")), 1)
        stuck = process(9311)
        self.store.add(
            RunRecord(
                "stuck-run",
                None,
                None,
                "RUNNING_IDLE",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [stuck],
            )
        )
        self.guard.snapshots[stuck.pid] = {"error": "process_not_found", "exit_code": 4}
        real = self.lifecycle.manifest.replace

        def _broken(run):
            raise OSError("disco lleno")

        self.lifecycle.manifest.replace = _broken
        try:
            for _ in range(33):
                self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        finally:
            self.lifecycle.manifest.replace = real
        status = self.lifecycle.status(IDENTITY_A)
        self.assertEqual(len(self._diags(status, "real-prior")), 1)
        self.assertEqual(self._diags(status, "stuck-run"), [])
        self.assertEqual(self._row(status, "stuck-run")["state"], "RUNNING_IDLE")
        self.assertEqual(len(status.get(DIAG_KEY) or []), 1)

    def test_c_repeated_repair_recovery_fault_is_rejected_with_one_diagnostic(self) -> None:
        record = process(9320)
        run = RunRecord(
            "11111111-1111-4111-8111-eeeeeeeeeeee",
            None,
            None,
            "UNRECONCILED",
            "recoverable",
            "@mod",
            "profiles",
            "mission",
            [record],
            "22222222-2222-4222-8222-eeeeeeeeeeee",
            "a" * 64,
            False,
        )
        self.store.add(run)
        self.guard.snapshots[record.pid] = {"error": "process_not_found", "exit_code": 4}
        fault = {
            "scope": "run",
            "run_id": run.run_id,
            "launch_operation_id": run.launch_operation_id,
            "run_record_sha256": hashlib.sha256(
                json.dumps(
                    dataclasses.asdict(run),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        first = self.lifecycle.repair_recovery_fault(fault)
        self.assertTrue(first.get("terminal_safe"), first)
        self.assertEqual(len(self._diags(self.lifecycle.status(IDENTITY_A), run.run_id)), 1)
        current = self.lifecycle.manifest.get(run.run_id)
        second_fault = {
            "scope": "run",
            "run_id": current.run_id,
            "launch_operation_id": current.launch_operation_id,
            "run_record_sha256": hashlib.sha256(
                json.dumps(
                    dataclasses.asdict(current),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        second = self.lifecycle.repair_recovery_fault(second_fault)
        self.assertIsNot(second.get("terminal_safe"), True)
        self.assertEqual(len(self._diags(self.lifecycle.status(IDENTITY_A), run.run_id)), 1)

    def test_d_torn_status_never_pairs_a_live_row_with_its_diagnostic(self) -> None:
        self._start()
        self.guard.snapshots[self.launcher.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        real = self.lifecycle.manifest.list_runs
        armed = {"on": True}

        def _wrapped():
            rows = real()
            if armed["on"]:
                armed["on"] = False
                self.lifecycle.reap_dead_runs()
            return rows

        self.lifecycle.manifest.list_runs = _wrapped
        try:
            payload = self.lifecycle.status(IDENTITY_A)
        finally:
            self.lifecycle.manifest.list_runs = real
        self.assertFalse(armed["on"])
        live = {
            row.get("run_id")
            for row in payload.get("runs") or []
            if isinstance(row, dict) and row.get("state") != "EXITED"
        }
        ring = {
            item.get("run_id")
            for item in payload.get(DIAG_KEY) or []
            if isinstance(item, dict)
        }
        self.assertFalse(live & ring)

    def test_d_torn_public_status_never_pairs_a_live_row_with_its_diagnostic(self) -> None:
        self._start()
        self.guard.snapshots[self.launcher.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        real = self.lifecycle.manifest.list_runs
        armed = {"on": True}

        def _wrapped():
            rows = real()
            if armed["on"]:
                armed["on"] = False
                self.lifecycle.reap_dead_runs()
            return rows

        self.lifecycle.manifest.list_runs = _wrapped
        try:
            payload = self.lifecycle.public_status()
        finally:
            self.lifecycle.manifest.list_runs = real
        self.assertFalse(armed["on"])
        live = {
            row.get("run_id")
            for row in payload.get("runs") or []
            if isinstance(row, dict) and row.get("state") != "EXITED"
        }
        ring = {
            item.get("run_id")
            for item in payload.get(DIAG_KEY) or []
            if isinstance(item, dict)
        }
        self.assertFalse(live & ring)

    def test_d_stop_then_status_without_reload_keeps_exited_row_and_diagnostic(self) -> None:
        run_id = self._start()
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run_id)
        self.assertTrue(stopped.get("ok"), stopped)
        payload = self.lifecycle.status(IDENTITY_A)
        self.assertEqual(self._row(payload, run_id)["state"], "EXITED")
        self.assertEqual(len(self._diags(payload, run_id)), 1)

    def test_e_admin_reconcile_publishes_closed_reason_and_keeps_operator_text_in_audit(
        self,
    ) -> None:
        dirty = "C:\\Users\\alice\\secret\\x.mdmp @ 2026-09-04T05:00:00Z"
        self.store.add(
            RunRecord(
                "reconcile-dirty",
                None,
                None,
                "UNRECONCILED",
                "gate",
                "@SameMod",
                "profiles",
                "mission",
                [],
            )
        )
        result = self.lifecycle.admin_reconcile(
            "reconcile-dirty", None, dirty, empty=True
        )
        self.assertTrue(result.get("reconciled"), result)
        hits = self._diags(self.lifecycle.status(IDENTITY_A), "reconcile-dirty")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["reason"], "admin_reconciled")
        self.assertEqual(hits[0]["event"], "admin_reconcile")
        self.assertNotIn("\\", hits[0]["reason"])
        self.assertNotIn("2026", hits[0]["reason"])
        audit_reasons = [
            event.get("reason")
            for event in self.audit.events
            if event.get("event") == "admin_reconcile"
        ]
        self.assertIn(dirty, audit_reasons)


class StorageRotationAuditTest(unittest.TestCase):
    """The rotation row has to land in the durable jsonl, not only in AuditSink."""

    STORAGE_SEAL_A = "a" * 64
    STORAGE_SEAL_B = "b" * 64

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.paths.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=self.audit,
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.store = RunManifestStore(self.paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
        )
        self.lifecycle.bridge_probe = FakeBridgeBindings().status_snapshot

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mission(self) -> Path:
        directory = Path(self.temporary.name) / "mpmissions" / "dayzOffline"
        if not directory.exists():
            tree = directory / "storage_1"
            (tree / "players").mkdir(parents=True)
            (tree / "data.bin").write_bytes(b"world-and-characters")
            (tree / "players" / "p1.bin").write_bytes(b"survivor")
        return directory

    def server_request(self, seal: str | None = STORAGE_SEAL_A) -> dict[str, object]:
        payload: dict[str, object] = {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "server",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": str(self.mission()),
        }
        if seal is not None:
            payload["storage_seal"] = seal
        return payload

    def _rotation_rows(self, events: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            event
            for event in events
            if event.get("event") == "lifecycle_storage_rotated"
        ]

    def _jsonl_rows(self, writer: JsonlAuditWriter) -> list[dict[str, object]]:
        if not writer.current_path.exists():
            return []
        return [
            json.loads(line)
            for line in writer.current_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def _assert_jsonl_carries_the_rotation_paths(
        self,
        row: dict[str, object],
        mission: Path,
        *,
        original_world: bytes,
        original_player: bytes,
        original_marker: dict[str, object] | None,
    ) -> None:
        """The JSONL row, not the producer object: that is the F-02 joint."""
        self.assertIn("storage_backup", row)
        backup_name = row["storage_backup"]
        self.assertIsInstance(backup_name, str)
        self.assertTrue(backup_name)
        backup = mission / backup_name
        self.assertTrue(backup.is_dir(), backup)
        self.assertEqual((backup / "data.bin").read_bytes(), original_world)
        self.assertEqual(
            (backup / "players" / "p1.bin").read_bytes(), original_player
        )
        self.assertIn("storage_marker_backup", row)
        marker_name = row["storage_marker_backup"]
        if original_marker is None:
            self.assertIsNone(marker_name)
            return
        self.assertIsInstance(marker_name, str)
        self.assertTrue(marker_name)
        kept = mission / marker_name
        self.assertTrue(kept.is_file(), kept)
        self.assertEqual(
            json.loads(kept.read_text(encoding="utf-8")), original_marker
        )

    def test_a_real_rotation_writes_lifecycle_storage_rotated_to_jsonl(self) -> None:
        """JsonlAuditWriter is the production sink; AuditSink would accept anything."""
        writer = JsonlAuditWriter(self.paths, "generation-rotation")
        self.lifecycle.audit = writer.write
        mission = self.mission()
        original_world = (mission / dayz_test_storage.STORAGE_NAME / "data.bin").read_bytes()
        original_player = (
            mission / dayz_test_storage.STORAGE_NAME / "players" / "p1.bin"
        ).read_bytes()

        error = self.lifecycle._rotate_storage_for_launch(
            self.server_request(), "run-real-audit"
        )

        self.assertIsNone(error)
        rotated = self._rotation_rows(self._jsonl_rows(writer))
        self.assertEqual(len(rotated), 1, rotated)
        self.assertEqual(rotated[0].get("run_id"), "run-real-audit")
        self.assertTrue(str(rotated[0].get("reason") or "").strip())
        self.assertIsInstance(rotated[0].get("duration_s"), (int, float))
        self._assert_jsonl_carries_the_rotation_paths(
            rotated[0],
            mission,
            original_world=original_world,
            original_player=original_player,
            original_marker=None,
        )

    def test_a_rotation_with_a_prior_marker_copies_the_marker_backup_into_jsonl(
        self,
    ) -> None:
        writer = JsonlAuditWriter(self.paths, "generation-rotation")
        self.lifecycle.audit = writer.write
        mission = self.mission()
        original_world = (mission / dayz_test_storage.STORAGE_NAME / "data.bin").read_bytes()
        original_player = (
            mission / dayz_test_storage.STORAGE_NAME / "players" / "p1.bin"
        ).read_bytes()
        original_marker = {
            "schema_version": dayz_test_storage.MARKER_SCHEMA_VERSION,
            "algorithm": dayz_test_storage.MARKER_ALGORITHM,
            "seal": self.STORAGE_SEAL_B,
            "project": "SameMod",
        }
        (mission / dayz_test_storage.MARKER_NAME).write_text(
            json.dumps(original_marker), encoding="utf-8"
        )

        error = self.lifecycle._rotate_storage_for_launch(
            self.server_request(), "run-marker-backup"
        )

        self.assertIsNone(error)
        rotated = self._rotation_rows(self._jsonl_rows(writer))
        self.assertEqual(len(rotated), 1, rotated)
        self.assertEqual(rotated[0].get("run_id"), "run-marker-backup")
        self._assert_jsonl_carries_the_rotation_paths(
            rotated[0],
            mission,
            original_world=original_world,
            original_player=original_player,
            original_marker=original_marker,
        )

    def test_an_admitted_server_launch_rotates_and_leaves_the_row(self) -> None:
        launched = process(self.launcher.pid, "server")
        self.guard.snapshots[self.launcher.pid] = snapshot(launched)
        self.mission()

        result = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.server_request()
        )

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(len(self.launcher.calls), 1)
        rows = self._rotation_rows(self.audit.events)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("run_id"), "run-1")

    def test_a_launch_that_does_not_rotate_writes_no_rotation_row(self) -> None:
        writer = JsonlAuditWriter(self.paths, "generation-rotation")
        self.lifecycle.audit = writer.write
        request = self.server_request()
        first = self.lifecycle._rotate_storage_for_launch(request, "run-one")
        self.assertIsNone(first)
        after_first = self._rotation_rows(self._jsonl_rows(writer))
        self.assertEqual(len(after_first), 1, after_first)

        second = self.lifecycle._rotate_storage_for_launch(request, "run-two")

        self.assertIsNone(second)
        self.assertEqual(
            self._rotation_rows(self._jsonl_rows(writer)),
            after_first,
        )

    def test_a_client_start_never_writes_a_rotation_row(self) -> None:
        launched = process(self.launcher.pid, "client")
        self.guard.snapshots[self.launcher.pid] = snapshot(launched)
        self.mission()
        request = self.server_request()
        request["role"] = "client"
        request["replace_if_not_polling_since"] = int(time.time() * 1000)

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, request)

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self._rotation_rows(self.audit.events), [])

    def test_a_writer_that_raises_does_not_block_the_launch(self) -> None:
        def boom(_event: dict[str, object]) -> bool:
            raise ValueError("invalid_audit_event")

        self.lifecycle.audit = boom
        self.mission()
        before = int(self.lifecycle.public_status().get("audit_rows_dropped") or 0)

        error = self.lifecycle._rotate_storage_for_launch(
            self.server_request(), "run-dropped-audit"
        )

        self.assertIsNone(error)
        dropped = self.lifecycle.public_status().get("audit_rows_dropped")
        self.assertIsInstance(dropped, int)
        self.assertGreaterEqual(dropped, before + 1)

    def test_a_missing_writer_still_counts_the_dropped_row(self) -> None:
        self.lifecycle.audit = None
        self.mission()
        before = int(self.lifecycle.public_status().get("audit_rows_dropped") or 0)

        error = self.lifecycle._rotate_storage_for_launch(
            self.server_request(), "run-no-writer"
        )

        self.assertIsNone(error)
        dropped = self.lifecycle.public_status().get("audit_rows_dropped")
        self.assertIsInstance(dropped, int)
        self.assertGreaterEqual(dropped, before + 1)


if __name__ == "__main__":
    unittest.main()
