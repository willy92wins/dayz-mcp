from __future__ import annotations

import json
import io
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from dayz_mcp import admin_cli, daemon, loopback, runtime_state
from dayz_mcp.runtime_state import (
    CoordinationFaultStore,
    CoordinationSnapshotStore,
    JsonlAuditWriter,
    RuntimePaths,
    recover_coordination_fault,
    recover_coordination_startup,
)
from dayz_mcp.session_coordination import SessionCoordinator
from tests.test_session_coordination import _identity


def _paths(root: Path) -> RuntimePaths:
    return RuntimePaths(
        root=root,
        audit_dir=root / "audit",
        coordination_path=root / "coordination.json",
        runs_path=root / "runs.json",
    )


def _marker(**changes: object) -> dict[str, object]:
    marker: dict[str, object] = {
        "format_version": 1,
        "fault_id": "fault-1",
        "daemon_generation": "generation-a",
        "state": "armed",
        "operation": "grant",
        "phase": "armed",
        "lease_id": "lease-1",
        "ticket_id": "ticket-1",
        "client": {
            "platform": "codex",
            "session": "session-a",
            "started_at_utc": "2026-07-22T00:00:00Z",
            "task_label": "queue wait",
        },
        "reason": "fifo_head",
        "armed_at_utc": "2026-07-22T00:00:00Z",
        "failure": None,
        "expected_snapshot_revision": 7,
        "repair_phase": "none",
    }
    marker.update(changes)
    return marker


class CoordinationFaultStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = _paths(self.root)
        self.store = CoordinationFaultStore(self.paths)

    def test_runtime_path_derives_fault_path_without_new_constructor_argument(self) -> None:
        self.assertEqual(
            self.paths.coordination_fault_path,
            self.root / "coordination-fault.json",
        )

    def test_arm_transition_completed_and_clear_use_exact_cas(self) -> None:
        armed_sha = self.store.arm(_marker())
        loaded, observed_sha = self.store.load_with_sha()
        self.assertEqual(loaded, _marker())
        self.assertEqual(observed_sha, armed_sha)

        completed_sha = self.store.transition(
            "fault-1",
            armed_sha,
            state="completed",
            phase="snapshot_persisted",
        )
        completed, observed_completed_sha = self.store.load_with_sha()
        self.assertEqual(observed_completed_sha, completed_sha)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["phase"], "snapshot_persisted")
        self.assertTrue(self.store.clear("fault-1", completed_sha))
        self.assertEqual(self.store.load_with_sha(), (None, None))

    def test_fault_repair_state_machine_is_closed(self) -> None:
        armed_sha = self.store.arm(_marker())
        fault_sha = self.store.transition(
            "fault-1",
            armed_sha,
            state="fault",
            phase="audit_failed",
            failure="audit_failed",
        )
        repairing_sha = self.store.transition(
            "fault-1",
            fault_sha,
            state="repairing",
            phase="repairing",
            repair_phase="compensation",
        )
        repaired_sha = self.store.transition(
            "fault-1",
            repairing_sha,
            state="repaired",
            phase="repaired",
            repair_phase="repair_event",
        )
        self.assertTrue(self.store.clear("fault-1", repaired_sha))

    def test_schema_secret_extra_corrupt_and_illegal_transition_fail_closed(self) -> None:
        invalid = (
            _marker(extra="no"),
            _marker(lease_token="secret"),
            _marker(state="unknown"),
            _marker(ticket_id=3),
            _marker(client={"platform": "codex", "session": "session-a"}),
        )
        for marker in invalid:
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(ValueError, "^invalid_coordination_fault$"):
                    CoordinationFaultStore.validate(marker)

        armed_sha = self.store.arm(_marker())
        with self.assertRaisesRegex(RuntimeError, "^coordination_fault_exists$"):
            self.store.arm(_marker(fault_id="fault-2"))
        with self.assertRaisesRegex(RuntimeError, "^coordination_fault_cas_conflict$"):
            self.store.transition(
                "fault-1", "0" * 64, state="completed", phase="snapshot_persisted"
            )
        with self.assertRaisesRegex(ValueError, "^invalid_coordination_fault_transition$"):
            self.store.transition(
                "fault-1", armed_sha, state="repaired", phase="repaired"
            )

        self.paths.coordination_fault_path.write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "^invalid_coordination_fault$"):
            self.store.load_with_sha()

    def test_state_phase_failure_and_repair_phase_form_a_closed_semantic_table(self) -> None:
        invalid = (
            _marker(state="completed", phase="armed"),
            _marker(
                state="completed",
                phase="coordination_changed",
                failure="coordination_changed",
            ),
            _marker(
                state="completed",
                phase="snapshot_persisted",
                failure="audit_failed",
            ),
            _marker(
                state="completed",
                phase="snapshot_persisted",
                repair_phase="repair_event",
            ),
            _marker(state="fault", phase="audit_failed", failure=None),
            _marker(state="fault", phase="armed", failure="audit_failed"),
            _marker(
                state="armed",
                phase="armed",
                failure="coordination_changed",
            ),
            _marker(
                state="armed",
                phase="coordination_changed",
                failure=None,
            ),
            _marker(
                state="repairing",
                phase="repairing",
                failure=None,
                repair_phase="compensation",
            ),
            _marker(
                state="repaired",
                phase="repaired",
                failure="audit_failed",
                repair_phase="none",
            ),
            _marker(state=[]),
            _marker(phase=[]),
            _marker(failure=[]),
            _marker(repair_phase=[]),
        )
        for marker in invalid:
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(
                    ValueError, "^invalid_coordination_fault$"
                ):
                    CoordinationFaultStore.validate(marker)

        legacy_valid = (
            _marker(),
            _marker(phase="prepared"),
            _marker(phase="committed"),
            _marker(phase="published"),
            _marker(state="completed", phase="snapshot_persisted"),
            _marker(
                state="completed",
                phase="snapshot_persisted",
                failure="coordination_changed",
            ),
            _marker(
                state="fault",
                phase="audit_failed",
                failure="audit_failed",
            ),
            _marker(
                state="repairing",
                phase="repairing",
                failure="audit_failed",
                repair_phase="compensation",
            ),
            _marker(
                state="repairing",
                phase="repairing",
                failure="audit_failed",
                repair_phase="repair_event",
            ),
            _marker(
                state="repaired",
                phase="repaired",
                failure="audit_failed",
                repair_phase="repair_event",
            ),
        )
        for marker in legacy_valid:
            with self.subTest(marker=marker):
                self.assertEqual(CoordinationFaultStore.validate(marker), marker)

    def test_transition_rejects_semantically_impossible_terminal_tuple(self) -> None:
        armed_sha = self.store.arm(_marker())

        with self.assertRaisesRegex(ValueError, "^invalid_coordination_fault$"):
            self.store.transition(
                "fault-1",
                armed_sha,
                state="completed",
                phase="coordination_changed",
                failure="coordination_changed",
            )

        self.assertEqual(self.store.load(), _marker())

    def test_clear_rejects_nonterminal_and_wrong_identity_without_modifying_bytes(self) -> None:
        armed_sha = self.store.arm(_marker())
        before = self.paths.coordination_fault_path.read_bytes()
        for fault_id, sha in (("fault-x", armed_sha), ("fault-1", "F" * 64)):
            with self.subTest(fault_id=fault_id, sha=sha):
                with self.assertRaisesRegex(RuntimeError, "^coordination_fault_cas_conflict$"):
                    self.store.clear(fault_id, sha)
                self.assertEqual(self.paths.coordination_fault_path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "^invalid_coordination_fault_transition$"):
            self.store.clear("fault-1", armed_sha)
        self.assertEqual(self.paths.coordination_fault_path.read_bytes(), before)

    def test_materialize_fault_is_create_only_and_exactly_idempotent(self) -> None:
        fault = _marker(state="fault", phase="audit_failed", failure="fault_marker_missing")
        first_sha = self.store.materialize_fault(fault)
        before = self.paths.coordination_fault_path.read_bytes()

        self.assertEqual(self.store.materialize_fault(fault), first_sha)
        self.assertEqual(self.paths.coordination_fault_path.read_bytes(), before)
        with self.assertRaisesRegex(RuntimeError, "^coordination_fault_exists$"):
            self.store.materialize_fault(fault | {"reason": "different"})
        self.assertEqual(self.paths.coordination_fault_path.read_bytes(), before)

    def test_materialize_serializes_two_stores_before_create_replace(self) -> None:
        first_store = CoordinationFaultStore(self.paths)
        second_store = CoordinationFaultStore(self.paths)
        first_fault = _marker(
            state="fault", phase="audit_failed", failure="fault_marker_missing"
        )
        second_fault = first_fault | {"fault_id": "fault-2"}
        entered = threading.Event()
        resume = threading.Event()
        second_done = threading.Event()
        first_errors: list[BaseException] = []
        second_errors: list[BaseException] = []
        real_replace = os.replace

        def replace_with_cutpoint(source: object, target: object) -> None:
            if (
                threading.current_thread().name == "fault-first"
                and Path(target) == self.paths.coordination_fault_path
            ):
                entered.set()
                if not resume.wait(2.0):
                    raise RuntimeError("test_resume_timeout")
            real_replace(source, target)

        def materialize_first() -> None:
            try:
                first_store.materialize_fault(first_fault)
            except BaseException as exc:
                first_errors.append(exc)

        def materialize_second() -> None:
            try:
                second_store.materialize_fault(second_fault)
            except BaseException as exc:
                second_errors.append(exc)
            finally:
                second_done.set()

        first = threading.Thread(target=materialize_first, name="fault-first")
        second = threading.Thread(target=materialize_second, name="fault-second")
        with mock.patch.object(
            runtime_state.os, "replace", side_effect=replace_with_cutpoint
        ):
            first.start()
            self.assertTrue(entered.wait(2.0))
            second.start()
            second_finished_while_first_was_open = second_done.wait(0.1)
            resume.set()
            first.join(2.0)
            second.join(2.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertFalse(second_finished_while_first_was_open)
        self.assertEqual(first_errors, [])
        self.assertEqual(
            [str(error) for error in second_errors], ["coordination_fault_exists"]
        )
        self.assertEqual(first_store.load(), first_fault)
        self.assertEqual(
            list(self.paths.root.glob("coordination-fault.json.tmp.*")), []
        )

    def test_transition_serializes_two_stores_and_rechecks_observed_sha(self) -> None:
        first_store = CoordinationFaultStore(self.paths)
        second_store = CoordinationFaultStore(self.paths)
        armed_sha = first_store.arm(_marker())
        entered = threading.Event()
        resume = threading.Event()
        second_done = threading.Event()
        first_errors: list[BaseException] = []
        second_errors: list[BaseException] = []
        real_replace = os.replace

        def replace_with_cutpoint(source: object, target: object) -> None:
            if (
                threading.current_thread().name == "fault-first"
                and Path(target) == self.paths.coordination_fault_path
            ):
                entered.set()
                if not resume.wait(2.0):
                    raise RuntimeError("test_resume_timeout")
            real_replace(source, target)

        def transition_first() -> None:
            try:
                first_store.transition(
                    "fault-1",
                    armed_sha,
                    state="fault",
                    phase="audit_failed",
                    failure="audit_failed",
                )
            except BaseException as exc:
                first_errors.append(exc)

        def transition_second() -> None:
            try:
                second_store.transition(
                    "fault-1",
                    armed_sha,
                    state="completed",
                    phase="snapshot_persisted",
                )
            except BaseException as exc:
                second_errors.append(exc)
            finally:
                second_done.set()

        first = threading.Thread(target=transition_first, name="fault-first")
        second = threading.Thread(target=transition_second, name="fault-second")
        with mock.patch.object(
            runtime_state.os, "replace", side_effect=replace_with_cutpoint
        ):
            first.start()
            self.assertTrue(entered.wait(2.0))
            second.start()
            second_finished_while_first_was_open = second_done.wait(0.1)
            resume.set()
            first.join(2.0)
            second.join(2.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertFalse(second_finished_while_first_was_open)
        self.assertEqual(first_errors, [])
        self.assertEqual(
            [str(error) for error in second_errors],
            ["coordination_fault_cas_conflict"],
        )
        persisted = first_store.load()
        self.assertEqual((persisted["state"], persisted["failure"]), ("fault", "audit_failed"))

    def test_clear_serializes_two_stores_before_exact_unlink(self) -> None:
        first_store = CoordinationFaultStore(self.paths)
        second_store = CoordinationFaultStore(self.paths)
        armed_sha = first_store.arm(_marker())
        completed_sha = first_store.transition(
            "fault-1",
            armed_sha,
            state="completed",
            phase="snapshot_persisted",
        )
        entered = threading.Event()
        resume = threading.Event()
        second_done = threading.Event()
        first_errors: list[BaseException] = []
        second_errors: list[BaseException] = []
        real_unlink = Path.unlink

        def unlink_with_cutpoint(target: Path, *args: object, **kwargs: object) -> None:
            if (
                threading.current_thread().name == "fault-first"
                and target == self.paths.coordination_fault_path
            ):
                entered.set()
                if not resume.wait(2.0):
                    raise RuntimeError("test_resume_timeout")
            real_unlink(target, *args, **kwargs)

        def clear_first() -> None:
            try:
                first_store.clear("fault-1", completed_sha)
            except BaseException as exc:
                first_errors.append(exc)

        def replace_second() -> None:
            try:
                second_store.transition(
                    "fault-1",
                    completed_sha,
                    state="fault",
                    phase="snapshot_failed",
                    failure="wal_completion_mismatch",
                )
            except BaseException as exc:
                second_errors.append(exc)
            finally:
                second_done.set()

        first = threading.Thread(target=clear_first, name="fault-first")
        second = threading.Thread(target=replace_second, name="fault-second")
        with mock.patch.object(Path, "unlink", autospec=True, side_effect=unlink_with_cutpoint):
            first.start()
            self.assertTrue(entered.wait(2.0))
            second.start()
            second_finished_while_first_was_open = second_done.wait(0.1)
            resume.set()
            first.join(2.0)
            second.join(2.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertFalse(second_finished_while_first_was_open)
        self.assertEqual(first_errors, [])
        self.assertEqual(
            [str(error) for error in second_errors],
            ["coordination_fault_cas_conflict"],
        )
        self.assertEqual(first_store.load_with_sha(), (None, None))

    def test_failed_fault_write_removes_only_its_unique_temporary(self) -> None:
        foreign = self.paths.coordination_fault_path.with_name(
            "coordination-fault.json.tmp.foreign"
        )
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"foreign-evidence")
        real_fsync = os.fsync
        fsync_calls = 0

        def fail_temporary_fsync(descriptor: int) -> None:
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 2:
                raise OSError("simulated fault fsync failure")
            real_fsync(descriptor)

        with mock.patch.object(
            runtime_state.os,
            "fsync",
            side_effect=fail_temporary_fsync,
        ):
            with self.assertRaisesRegex(OSError, "simulated fault fsync failure"):
                self.store.materialize_fault(
                    _marker(
                        state="fault",
                        phase="audit_failed",
                        failure="fault_marker_missing",
                    )
                )

        self.assertEqual(foreign.read_bytes(), b"foreign-evidence")
        self.assertEqual(
            list(self.paths.root.glob("coordination-fault.json.tmp.*")), [foreign]
        )

    def test_fault_cleanup_never_unlinks_foreign_replacement_at_temp_name(self) -> None:
        real_target_identity = runtime_state._target_identity
        moved_owned = self.paths.root / "writer-owned-moved-aside"
        substituted: list[Path] = []

        def substitute_unique_temporary(path: Path):
            if (
                path.name.startswith("coordination-fault.json.tmp.")
                and not substituted
            ):
                os.replace(path, moved_owned)
                path.write_bytes(b"foreign-replacement")
                substituted.append(path)
            return real_target_identity(path)

        with mock.patch.object(
            runtime_state,
            "_target_identity",
            side_effect=substitute_unique_temporary,
        ):
            with self.assertRaisesRegex(RuntimeError, "^atomic_temporary_changed$"):
                self.store.materialize_fault(
                    _marker(
                        state="fault",
                        phase="audit_failed",
                        failure="fault_marker_missing",
                    )
                )

        self.assertEqual(len(substituted), 1)
        self.assertEqual(substituted[0].read_bytes(), b"foreign-replacement")
        self.assertTrue(moved_owned.exists())
        self.assertFalse(self.paths.coordination_fault_path.exists())


class CoordinationFaultStartupRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = _paths(Path(self.temporary.name))
        self.store = CoordinationFaultStore(self.paths)

    def _snapshot(self, **changes: object) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "daemon_generation": "generation-a",
            "revision": 7,
            "active": {"lease_id": "lease-1"},
            "releasing": None,
            "granting": None,
            "audit_fault": None,
            "queue": [],
        }
        snapshot.update(changes)
        self.paths.coordination_path.write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        return snapshot

    def test_armed_marker_becomes_durable_fault_on_restart(self) -> None:
        self._snapshot()
        self.store.arm(_marker())

        recovered = recover_coordination_fault(
            self.paths,
            self.store,
            "generation-b",
            utc_now_fn=lambda: "2026-07-22T01:00:00Z",
        )

        persisted = self.store.load()
        self.assertEqual(recovered["state"], "fault")
        self.assertEqual(persisted["state"], "fault")
        self.assertEqual(recovered["fault_id"], "fault-1")

    def test_completed_matching_grant_is_cleared_idempotently(self) -> None:
        self._snapshot()
        armed_sha = self.store.arm(_marker())
        self.store.transition(
            "fault-1", armed_sha, state="completed", phase="snapshot_persisted"
        )

        recovered = recover_coordination_fault(
            self.paths, self.store, "generation-b"
        )

        self.assertIsNone(recovered)
        self.assertEqual(self.store.load_with_sha(), (None, None))

    def test_impossible_completed_tuple_never_recovers_as_clean(self) -> None:
        self._snapshot()
        impossible = _marker(state="completed", phase="armed")
        original = (json.dumps(impossible, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        self.paths.coordination_fault_path.write_bytes(original)

        recovery = recover_coordination_startup(
            self.paths,
            self.store,
            "generation-b",
            utc_now_fn=lambda: "2026-07-22T01:00:00Z",
        )

        self.assertIsNotNone(recovery.audit_fault)
        self.assertEqual(recovery.audit_fault["failure"], "fault_store_unreadable")
        self.assertFalse(recovery.fault_materialized)
        self.assertEqual(self.paths.coordination_fault_path.read_bytes(), original)

    def test_completed_snapshot_mismatch_becomes_fault_and_is_not_cleared(self) -> None:
        self._snapshot(active=None)
        armed_sha = self.store.arm(_marker())
        self.store.transition(
            "fault-1", armed_sha, state="completed", phase="snapshot_persisted"
        )

        recovered = recover_coordination_fault(
            self.paths, self.store, "generation-b"
        )

        self.assertEqual(recovered["state"], "fault")
        self.assertEqual(recovered["failure"], "wal_completion_mismatch")
        self.assertTrue(self.paths.coordination_fault_path.exists())

    def test_completed_compensation_recovers_after_crash_before_clear(self) -> None:
        for reason in ("coordination_changed", "operation_cancelled"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as root:
                paths = _paths(Path(root))
                paths.root.mkdir(parents=True, exist_ok=True)
                paths.coordination_path.write_text(
                    json.dumps(
                        {
                            "daemon_generation": "generation-a",
                            "revision": 7,
                            "active": None,
                            "releasing": None,
                            "granting": None,
                            "audit_fault": None,
                            "queue": [],
                        }
                    ),
                    encoding="utf-8",
                )
                store = CoordinationFaultStore(paths)
                armed_sha = store.arm(_marker(reason=reason))
                store.transition(
                    "fault-1",
                    armed_sha,
                    state="completed",
                    phase="snapshot_persisted",
                    failure="coordination_changed",
                )

                recovered = recover_coordination_fault(
                    paths, store, "generation-b"
                )

                self.assertIsNone(recovered)
                self.assertEqual(store.load_with_sha(), (None, None))

    def test_snapshot_fault_without_marker_is_synthetic_fail_closed(self) -> None:
        snapshot_fault = _marker(
            state="fault", phase="audit_failed", failure="audit_failed"
        )
        self._snapshot(active=None, audit_fault=snapshot_fault)

        recovered = recover_coordination_fault(
            self.paths,
            self.store,
            "generation-b",
            utc_now_fn=lambda: "2026-07-22T01:00:00Z",
        )

        self.assertEqual(recovered["failure"], "fault_marker_missing")
        self.assertEqual(recovered["fault_id"], "fault-1")
        self.assertEqual(self.store.load(), recovered)

    def test_corrupt_marker_is_reported_without_overwrite(self) -> None:
        self._snapshot(active=None)
        corrupt = b"{not-json"
        self.paths.coordination_fault_path.write_bytes(corrupt)

        recovered = recover_coordination_fault(
            self.paths,
            self.store,
            "generation-b",
            utc_now_fn=lambda: "2026-07-22T01:00:00Z",
        )

        self.assertEqual(recovered["failure"], "fault_store_unreadable")
        self.assertEqual(self.paths.coordination_fault_path.read_bytes(), corrupt)

    def test_snapshot_disappearance_after_observation_is_never_missing_or_clean(self) -> None:
        snapshot_fault = _marker(
            state="fault", phase="audit_failed", failure="audit_failed"
        )

        for cutpoint in ("before_open", "before_final_lstat"):
            with self.subTest(cutpoint=cutpoint), tempfile.TemporaryDirectory() as root:
                paths = _paths(Path(root))
                paths.root.mkdir(parents=True, exist_ok=True)
                snapshot = {
                    "daemon_generation": "generation-a",
                    "revision": 7,
                    "active": None,
                    "releasing": None,
                    "granting": None,
                    "audit_fault": snapshot_fault,
                    "queue": [],
                }
                paths.coordination_path.write_text(
                    json.dumps(snapshot), encoding="utf-8"
                )
                store = CoordinationFaultStore(paths)

                if cutpoint == "before_open":
                    real_open = os.open
                    triggered = False

                    def open_after_removal(
                        path: object, flags: int, *args: object, **kwargs: object
                    ) -> int:
                        nonlocal triggered
                        if Path(path) == paths.coordination_path and not triggered:
                            triggered = True
                            paths.coordination_path.unlink()
                        return real_open(path, flags, *args, **kwargs)

                    cutpoint_patch = mock.patch.object(
                        runtime_state.os, "open", side_effect=open_after_removal
                    )
                else:
                    real_lstat = Path.lstat
                    observed_lstats = 0

                    def lstat_after_removal(path: Path) -> os.stat_result:
                        nonlocal observed_lstats
                        if path == paths.coordination_path:
                            observed_lstats += 1
                            if observed_lstats == 2:
                                raise FileNotFoundError(str(path))
                        return real_lstat(path)

                    cutpoint_patch = mock.patch.object(
                        Path,
                        "lstat",
                        autospec=True,
                        side_effect=lstat_after_removal,
                    )

                with cutpoint_patch:
                    recovery_b = recover_coordination_startup(
                        paths,
                        store,
                        "generation-b",
                        utc_now_fn=lambda: "2026-07-22T01:00:00Z",
                    )
                if cutpoint == "before_final_lstat":
                    paths.coordination_path.unlink()

                self.assertEqual(recovery_b.snapshot_status, "runtime_path_changed")
                self.assertIsNotNone(recovery_b.audit_fault)
                self.assertEqual(recovery_b.audit_fault["failure"], "snapshot_failed")
                self.assertFalse(recovery_b.can_consume_snapshot)
                persisted_b = store.load()
                self.assertEqual(
                    persisted_b["fault_id"], recovery_b.audit_fault["fault_id"]
                )

                recovery_c = recover_coordination_startup(
                    paths,
                    CoordinationFaultStore(paths),
                    "generation-c",
                    utc_now_fn=lambda: "2026-07-22T02:00:00Z",
                )
                self.assertIsNotNone(recovery_c.audit_fault)
                self.assertEqual(
                    recovery_c.audit_fault["fault_id"],
                    recovery_b.audit_fault["fault_id"],
                )

    def test_marker_disappearance_after_observation_is_unreadable_then_b_to_c_faulted(self) -> None:
        snapshot_fault = _marker(
            state="fault", phase="audit_failed", failure="audit_failed"
        )

        for cutpoint in ("before_open", "before_final_lstat"):
            with self.subTest(cutpoint=cutpoint), tempfile.TemporaryDirectory() as root:
                paths = _paths(Path(root))
                paths.root.mkdir(parents=True, exist_ok=True)
                paths.coordination_path.write_text(
                    json.dumps(
                        {
                            "daemon_generation": "generation-a",
                            "revision": 7,
                            "active": None,
                            "releasing": None,
                            "granting": None,
                            "audit_fault": snapshot_fault,
                            "queue": [],
                        }
                    ),
                    encoding="utf-8",
                )
                store = CoordinationFaultStore(paths)
                store.materialize_fault(snapshot_fault)

                if cutpoint == "before_open":
                    real_open = os.open
                    triggered = False

                    def open_after_removal(
                        path: object, flags: int, *args: object, **kwargs: object
                    ) -> int:
                        nonlocal triggered
                        if Path(path) == paths.coordination_fault_path and not triggered:
                            triggered = True
                            paths.coordination_fault_path.unlink()
                        return real_open(path, flags, *args, **kwargs)

                    cutpoint_patch = mock.patch.object(
                        runtime_state.os, "open", side_effect=open_after_removal
                    )
                else:
                    real_lstat = Path.lstat
                    observed_lstats = 0

                    def lstat_after_removal(path: Path) -> os.stat_result:
                        nonlocal observed_lstats
                        if path == paths.coordination_fault_path:
                            observed_lstats += 1
                            if observed_lstats == 2:
                                raise FileNotFoundError(str(path))
                        return real_lstat(path)

                    cutpoint_patch = mock.patch.object(
                        Path,
                        "lstat",
                        autospec=True,
                        side_effect=lstat_after_removal,
                    )

                with cutpoint_patch:
                    recovery_b = recover_coordination_startup(
                        paths,
                        store,
                        "generation-b",
                        utc_now_fn=lambda: "2026-07-22T01:00:00Z",
                    )
                if cutpoint == "before_final_lstat":
                    paths.coordination_fault_path.unlink()

                self.assertIsNotNone(recovery_b.audit_fault)
                self.assertEqual(
                    recovery_b.audit_fault["failure"], "fault_store_unreadable"
                )
                self.assertFalse(recovery_b.fault_materialized)

                recovery_c = recover_coordination_startup(
                    paths,
                    CoordinationFaultStore(paths),
                    "generation-c",
                    utc_now_fn=lambda: "2026-07-22T02:00:00Z",
                )
                self.assertIsNotNone(recovery_c.audit_fault)
                self.assertEqual(
                    recovery_c.audit_fault["failure"], "fault_marker_missing"
                )
                self.assertEqual(
                    CoordinationFaultStore(paths).load()["fault_id"],
                    recovery_c.audit_fault["fault_id"],
                )


class DurableStartupRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = _paths(Path(self.temporary.name))

    def _activate(self, generation: str) -> loopback.ServerState:
        state = loopback.ServerState("key")
        with mock.patch.object(
            daemon.RuntimePaths, "from_env", return_value=self.paths
        ):
            daemon._activate_server_coordination(state, generation)
        return state

    def _assert_blocked(self, state: loopback.ServerState, fault_id: str) -> None:
        status, payload = state.coordination.acquire(_identity("blocked"), "drive")
        self.assertEqual((status, payload.get("error")), (503, "coordination_audit_fault"))
        self.assertEqual(payload.get("fault_id"), fault_id)

    def test_terminal_transition_failure_snapshot_is_valid_and_repairable_after_restart(self) -> None:
        for failure_mode in ("cas_conflict_exception", "false_return"):
            with self.subTest(failure_mode=failure_mode), tempfile.TemporaryDirectory() as root:
                paths = _paths(Path(root))
                store = CoordinationFaultStore(paths)
                snapshots = CoordinationSnapshotStore(paths, "generation-a")

                def fail_transition(*_args: object, **_kwargs: object) -> object:
                    if failure_mode == "cas_conflict_exception":
                        raise RuntimeError("coordination_fault_cas_conflict")
                    return False

                coordinator = SessionCoordinator(
                    token_fn=lambda: "secret-token",
                    id_fn=lambda: "lease-1",
                    fault_id_fn=lambda: "fault-terminal",
                    daemon_generation="generation-a",
                    utc_now_fn=lambda: "2026-07-22T00:00:00Z",
                    audit=lambda _event: False,
                    fault_arm=store.arm,
                    fault_transition=fail_transition,
                    fault_clear=store.clear,
                    persist_snapshot=snapshots.write_coordination,
                )

                status, payload = coordinator.acquire(_identity("owner"), "drive")
                fault = coordinator.snapshot_payload()["audit_fault"]

                self.assertEqual((status, payload.get("error")), (503, "audit_failed"))
                armed = store.load()
                expected_fault = dict(armed)
                expected_fault.update(
                    state="fault",
                    phase="terminal_transition_failed",
                    failure="terminal_transition_failed",
                )
                self.assertEqual(fault, expected_fault)
                self.assertEqual(fault["fault_id"], "fault-terminal")
                self.assertEqual(fault["state"], "fault")
                self.assertEqual(fault["phase"], "terminal_transition_failed")
                self.assertEqual(fault["failure"], "terminal_transition_failed")
                self.assertEqual(CoordinationFaultStore.validate(fault), fault)
                persisted = json.loads(paths.coordination_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["audit_fault"], fault)
                self.assertEqual(list(paths.root.glob("coordination.json.corrupt.*")), [])

                state_b = loopback.ServerState("key")
                with mock.patch.object(
                    daemon.RuntimePaths, "from_env", return_value=paths
                ):
                    daemon._activate_server_coordination(state_b, "generation-b")

                recovered = state_b.coordination.snapshot_payload()["audit_fault"]
                self.assertEqual(recovered["fault_id"], fault["fault_id"])
                self.assertEqual(list(paths.root.glob("coordination.json.corrupt.*")), [])
                self._assert_blocked(state_b, "fault-terminal")
                repair_status, repair_payload = loopback._repair_coordination_audit_fault(
                    state_b, "fault-terminal"
                )
                self.assertEqual(
                    (repair_status, repair_payload.get("repaired")), (200, True)
                )
                self.assertEqual(store.load_with_sha(), (None, None))

    def test_snapshot_only_fault_survives_restart_b_then_c_until_exact_repair(self) -> None:
        snapshot_fault = _marker(
            state="fault", phase="audit_failed", failure="audit_failed"
        )
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.coordination_path.write_text(
            json.dumps(
                {
                    "daemon_generation": "generation-a",
                    "revision": 7,
                    "active": None,
                    "releasing": None,
                    "granting": None,
                    "audit_fault": snapshot_fault,
                    "queue": [],
                }
            ),
            encoding="utf-8",
        )

        state_b = self._activate("generation-b")
        public_b = state_b.coordination.snapshot_payload()["audit_fault"]
        marker_b = state_b.coordination_fault_store.load()
        self.assertEqual(public_b["fault_id"], "fault-1")
        self.assertEqual(marker_b["fault_id"], "fault-1")
        self.assertEqual(marker_b["failure"], "fault_marker_missing")
        self._assert_blocked(state_b, "fault-1")

        state_c = self._activate("generation-c")
        public_c = state_c.coordination.snapshot_payload()["audit_fault"]
        marker_c = state_c.coordination_fault_store.load()
        self.assertEqual(public_c, marker_c)
        self.assertEqual(public_c["fault_id"], public_b["fault_id"])
        self._assert_blocked(state_c, "fault-1")

        repair_status, repair_payload = loopback._repair_coordination_audit_fault(
            state_c, "fault-1"
        )
        self.assertEqual((repair_status, repair_payload.get("repaired")), (200, True))
        self.assertEqual(state_c.coordination_fault_store.load_with_sha(), (None, None))
        granted_status, granted = state_c.coordination.acquire(
            _identity("repaired"), "drive"
        )
        self.assertEqual((granted_status, granted.get("status")), (200, "active"))

    def test_corrupt_snapshot_is_quarantined_once_and_repairable_after_two_restarts(self) -> None:
        corrupt = b"{broken-snapshot"
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.coordination_path.write_bytes(corrupt)

        state_b = self._activate("generation-b")
        public_b = state_b.coordination.snapshot_payload()["audit_fault"]
        fault_id = public_b["fault_id"]
        self.assertEqual(public_b["failure"], "snapshot_failed")
        self.assertEqual(state_b.coordination_fault_store.load()["fault_id"], fault_id)
        quarantined = sorted(self.paths.root.glob("coordination.json.corrupt.*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), corrupt)
        self._assert_blocked(state_b, fault_id)

        state_c = self._activate("generation-c")
        public_c = state_c.coordination.snapshot_payload()["audit_fault"]
        self.assertEqual(public_c["fault_id"], fault_id)
        self.assertEqual(len(list(self.paths.root.glob("coordination.json.corrupt.*"))), 1)
        self._assert_blocked(state_c, fault_id)

        repair_status, repair_payload = loopback._repair_coordination_audit_fault(
            state_c, fault_id
        )
        self.assertEqual((repair_status, repair_payload.get("repaired")), (200, True))
        self.assertEqual(state_c.coordination_fault_store.load_with_sha(), (None, None))
        persisted = json.loads(self.paths.coordination_path.read_text(encoding="utf-8"))
        self.assertIsNone(persisted["audit_fault"])
        granted_status, granted = state_c.coordination.acquire(
            _identity("repaired"), "drive"
        )
        self.assertEqual((granted_status, granted.get("status")), (200, "active"))

    def test_materialize_failure_keeps_snapshot_unconsumed_and_restart_c_faulted(self) -> None:
        corrupt = b"{materialize-cutpoint"
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.coordination_path.write_bytes(corrupt)

        with mock.patch.object(
            CoordinationFaultStore,
            "materialize_fault",
            side_effect=OSError("simulated durable write failure"),
        ), mock.patch.object(
            CoordinationSnapshotStore,
            "consume_previous_generation",
            side_effect=AssertionError("unmaterialized recovery must not be consumed"),
        ):
            state_b = self._activate("generation-b")

        fault_b = state_b.coordination.snapshot_payload()["audit_fault"]
        self.assertEqual(fault_b["failure"], "snapshot_failed")
        self.assertEqual(self.paths.coordination_path.read_bytes(), corrupt)
        self.assertEqual(state_b.coordination_fault_store.load_with_sha(), (None, None))
        self._assert_blocked(state_b, fault_b["fault_id"])

        state_c = self._activate("generation-c")
        fault_c = state_c.coordination.snapshot_payload()["audit_fault"]
        self.assertEqual(fault_c["failure"], "snapshot_failed")
        self.assertEqual(
            state_c.coordination_fault_store.load()["fault_id"],
            fault_c["fault_id"],
        )
        self._assert_blocked(state_c, fault_c["fault_id"])

    def test_corrupt_snapshot_is_quarantined_only_after_marker_is_verified_durable(self) -> None:
        corrupt = b"{post-marker-pre-quarantine"
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.coordination_path.write_bytes(corrupt)
        from dayz_mcp import runtime_state

        real_quarantine = runtime_state._quarantine_coordination_snapshot
        observed: list[str] = []

        def quarantine_after_marker(*args: object, **kwargs: object) -> Path | None:
            marker, marker_sha = CoordinationFaultStore(self.paths).load_with_sha()
            self.assertIsNotNone(marker)
            self.assertIsInstance(marker_sha, str)
            self.assertEqual(marker["state"], "fault")
            observed.append(str(marker["fault_id"]))
            return real_quarantine(*args, **kwargs)

        with mock.patch.object(
            runtime_state,
            "_quarantine_coordination_snapshot",
            side_effect=quarantine_after_marker,
        ):
            state_b = self._activate("generation-b")

        self.assertEqual(observed, [state_b.coordination.snapshot_payload()["audit_fault"]["fault_id"]])
        quarantined = list(self.paths.root.glob("coordination.json.corrupt.*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), corrupt)

    def test_invalid_audit_fault_snapshot_is_preserved_until_marker_is_durable(self) -> None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        invalid_snapshot = {
            "daemon_generation": "generation-a",
            "revision": 7,
            "active": None,
            "releasing": None,
            "granting": None,
            "audit_fault": {"fault_id": "invalid-shape"},
            "queue": [],
        }
        raw = json.dumps(invalid_snapshot, separators=(",", ":")).encode("utf-8")
        self.paths.coordination_path.write_bytes(raw)
        from dayz_mcp import runtime_state

        real_quarantine = runtime_state._quarantine_coordination_snapshot
        marker_seen = False

        def quarantine_after_marker(*args: object, **kwargs: object) -> Path | None:
            nonlocal marker_seen
            marker, marker_sha = CoordinationFaultStore(self.paths).load_with_sha()
            marker_seen = marker is not None and isinstance(marker_sha, str)
            return real_quarantine(*args, **kwargs)

        with mock.patch.object(
            runtime_state,
            "_quarantine_coordination_snapshot",
            side_effect=quarantine_after_marker,
        ):
            state_b = self._activate("generation-b")

        self.assertTrue(marker_seen)
        quarantined = list(self.paths.root.glob("coordination.json.corrupt.*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), raw)
        fault = state_b.coordination.snapshot_payload()["audit_fault"]
        self.assertEqual(fault["failure"], "snapshot_failed")
        self._assert_blocked(state_b, fault["fault_id"])

    def test_quarantine_failure_preserves_source_and_restart_retries_fail_closed(self) -> None:
        corrupt = b"{quarantine-cutpoint"
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.coordination_path.write_bytes(corrupt)
        from dayz_mcp import runtime_state

        with mock.patch.object(
            runtime_state,
            "_quarantine_coordination_snapshot",
            side_effect=OSError("simulated quarantine failure"),
        ), mock.patch.object(
            CoordinationSnapshotStore,
            "consume_previous_generation",
            side_effect=AssertionError("unquarantined evidence must not be overwritten"),
        ):
            state_b = self._activate("generation-b")

        fault_b = state_b.coordination.snapshot_payload()["audit_fault"]
        self.assertEqual(self.paths.coordination_path.read_bytes(), corrupt)
        self.assertEqual(
            state_b.coordination_fault_store.load()["fault_id"], fault_b["fault_id"]
        )
        self._assert_blocked(state_b, fault_b["fault_id"])

        state_c = self._activate("generation-c")
        fault_c = state_c.coordination.snapshot_payload()["audit_fault"]
        self.assertEqual(fault_c["fault_id"], fault_b["fault_id"])
        self.assertEqual(len(list(self.paths.root.glob("coordination.json.corrupt.*"))), 1)
        self._assert_blocked(state_c, fault_c["fault_id"])

    def test_snapshot_substitution_between_read_and_quarantine_is_not_moved_or_consumed(self) -> None:
        corrupt = b"{pinned-source"
        replacement = b"{replacement-source"
        moved_original = self.paths.root / "moved-original.json"
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.coordination_path.write_bytes(corrupt)
        real_materialize = CoordinationFaultStore.materialize_fault

        def materialize_then_substitute(
            store: CoordinationFaultStore, payload: dict[str, object]
        ) -> str:
            marker_sha = real_materialize(store, payload)
            os.replace(self.paths.coordination_path, moved_original)
            self.paths.coordination_path.write_bytes(replacement)
            return marker_sha

        with mock.patch.object(
            CoordinationFaultStore,
            "materialize_fault",
            autospec=True,
            side_effect=materialize_then_substitute,
        ):
            recovery = recover_coordination_startup(
                self.paths,
                CoordinationFaultStore(self.paths),
                "generation-b",
                utc_now_fn=lambda: "2026-07-22T01:00:00Z",
            )

        self.assertFalse(recovery.can_consume_snapshot)
        self.assertEqual(self.paths.coordination_path.read_bytes(), replacement)
        self.assertEqual(moved_original.read_bytes(), corrupt)
        self.assertEqual(list(self.paths.root.glob("coordination.json.corrupt.*")), [])
        self.assertEqual(
            CoordinationFaultStore(self.paths).load()["fault_id"],
            recovery.audit_fault["fault_id"],
        )


class AuditWriteOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = _paths(self.root)
        self.event = {
            "event": "session_grant_revoked",
            "reason": "audit_failed",
            "duration_s": 0.0,
            "decision": "revoked",
            "fault_id": "fault-1",
            "lease_id": "lease-1",
            "ticket": "ticket-1",
            "operation_id": "operation-1",
            "client": {"platform": "codex", "session": "session-a"},
        }

    def test_same_semantic_core_across_generation_is_success_without_append(self) -> None:
        first = JsonlAuditWriter(
            self.paths, "generation-a", utc_now_fn=lambda: "2026-07-22T00:00:00Z"
        )
        second = JsonlAuditWriter(
            self.paths, "generation-b", utc_now_fn=lambda: "2026-07-22T00:01:00Z"
        )
        self.assertTrue(first.write_once("fault-1:compensation", self.event))
        self.assertTrue(second.write_once("fault-1:compensation", self.event))
        lines = first.current_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        persisted = json.loads(lines[0])
        self.assertEqual(persisted["event_id"], "fault-1:compensation")
        self.assertEqual(persisted["daemon_generation"], "generation-a")

    def test_same_event_id_with_different_core_fails_closed(self) -> None:
        writer = JsonlAuditWriter(self.paths, "generation-a")
        writer.write_once("fault-1:compensation", self.event)
        with self.assertRaisesRegex(ValueError, "^audit_event_id_conflict$"):
            writer.write_once(
                "fault-1:compensation", self.event | {"reason": "different"}
            )
        self.assertEqual(
            len(writer.current_path.read_text(encoding="utf-8").splitlines()), 1
        )

    def test_scan_includes_backups_and_invalid_json_fails_closed(self) -> None:
        writer = JsonlAuditWriter(self.paths, "generation-a")
        writer.write_once("fault-1:compensation", self.event)
        backup = Path(f"{writer.current_path}.1")
        writer.current_path.replace(backup)
        self.assertTrue(writer.write_once("fault-1:compensation", self.event))
        self.assertFalse(writer.current_path.exists())

        writer.current_path.parent.mkdir(parents=True, exist_ok=True)
        writer.current_path.write_text("not-json\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "^invalid_audit_jsonl$"):
            writer.write_once("fault-2:repaired", self.event)


class AuditFaultSnapshotTests(unittest.TestCase):
    def test_snapshot_persists_mandatory_public_fault_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(Path(temporary))
            store = CoordinationSnapshotStore(paths, "generation-a")
            payload = {
                "revision": 1,
                "active": None,
                "releasing": None,
                "granting": None,
                "handoff_pending": False,
                "queue": [],
                "audit_fault": _marker(state="fault", phase="audit_failed", failure="audit_failed"),
            }
            self.assertTrue(store.write_coordination(payload))
            persisted = json.loads(paths.coordination_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["audit_fault"]["fault_id"], "fault-1")
            self.assertNotIn("lease_token", json.dumps(persisted))

            self.assertTrue(
                store.write_coordination(payload | {"revision": 2, "audit_fault": None})
            )
            persisted = json.loads(paths.coordination_path.read_text(encoding="utf-8"))
            self.assertIn("audit_fault", persisted)
            self.assertIsNone(persisted["audit_fault"])

    def test_recovered_fault_is_public_and_blocks_new_authority(self) -> None:
        fault = _marker(state="fault", phase="audit_failed", failure="audit_failed")
        coordinator = SessionCoordinator(recovered_audit_fault=fault)
        status, payload = coordinator.acquire(_identity("a"), "blocked")
        snapshot = coordinator.snapshot_payload()

        self.assertEqual((status, payload["error"]), (503, "coordination_audit_fault"))
        self.assertEqual(payload["fault_id"], "fault-1")
        self.assertFalse(snapshot["claimable"])
        self.assertEqual(snapshot["audit_fault"]["fault_id"], "fault-1")
        self.assertNotIn("lease_token", repr((payload, snapshot)))


class CoordinatorWalIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = _paths(Path(self.temporary.name))
        self.fault_store = CoordinationFaultStore(self.paths)
        self.snapshot_store = CoordinationSnapshotStore(self.paths, "generation-a")
        self.events: list[dict[str, object]] = []

    def _coordinator(self, *, persist=None) -> SessionCoordinator:
        return SessionCoordinator(
            token_fn=lambda: "secret-token",
            id_fn=lambda: "lease-1",
            fault_id_fn=lambda: "fault-1",
            daemon_generation="generation-a",
            utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            audit=lambda event: self.events.append(dict(event)) or True,
            fault_arm=self.fault_store.arm,
            fault_transition=self.fault_store.transition,
            fault_clear=self.fault_store.clear,
            persist_snapshot=persist or self.snapshot_store.write_coordination,
        )

    def test_immediate_grant_arms_before_audit_and_clears_only_after_snapshot(self) -> None:
        coordinator = self._coordinator()
        status, payload = coordinator.acquire(_identity("a"), "drive")

        self.assertEqual((status, payload["status"]), (200, "active"))
        self.assertEqual(self.fault_store.load_with_sha(), (None, None))
        snapshot = json.loads(self.paths.coordination_path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["active"]["lease_id"], "lease-1")
        self.assertIsNone(snapshot["audit_fault"])
        self.assertEqual(
            [event["event"] for event in self.events],
            ["session_acquire", "session_granted"],
        )

    def test_immediate_prepublish_aborts_are_durable_and_restart_cleanly(self) -> None:
        for boundary, mutation in (
            ("session_acquire", "coordination_changed"),
            ("session_granted", "operation_cancelled"),
        ):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as root:
                paths = _paths(Path(root))
                store = CoordinationFaultStore(paths)
                snapshots = CoordinationSnapshotStore(paths, "generation-a")
                events: list[dict[str, object]] = []
                owner = _identity("immediate-owner")
                operation_id = "operation-immediate"
                coordinator: SessionCoordinator
                triggered = False

                class CrashBeforeClear(BaseException):
                    pass

                def audit(event: dict[str, object]) -> bool:
                    nonlocal triggered
                    events.append(dict(event))
                    if (
                        not triggered
                        and event.get("event") == boundary
                        and event.get("reason") == "request"
                    ):
                        triggered = True
                        with coordinator._condition:
                            if mutation == "operation_cancelled":
                                coordinator._operation_tombstones[
                                    (owner, operation_id)
                                ] = coordinator._time_fn() + 300.0
                            else:
                                coordinator._handoff_pending = True
                            coordinator._bump_revision_locked()
                            coordinator._condition.notify_all()
                    return True

                def crash_before_clear(fault_id: str, sha256: str) -> bool:
                    marker = store.load()
                    if (
                        marker is not None
                        and marker.get("lease_id") == "lease-immediate"
                        and marker.get("state") == "completed"
                    ):
                        raise CrashBeforeClear()
                    return store.clear(fault_id, sha256)

                coordinator = SessionCoordinator(
                    token_fn=lambda: "secret-token",
                    id_fn=lambda: "lease-immediate",
                    fault_id_fn=lambda: "fault-immediate",
                    daemon_generation="generation-a",
                    utc_now_fn=lambda: "2026-07-22T00:00:00Z",
                    audit=audit,
                    fault_arm=store.arm,
                    fault_transition=store.transition,
                    fault_clear=crash_before_clear,
                    persist_snapshot=snapshots.write_coordination,
                )

                with self.assertRaises(CrashBeforeClear):
                    coordinator.acquire(owner, "drive", operation_id)

                marker = store.load()
                persisted = json.loads(paths.coordination_path.read_text(encoding="utf-8"))
                self.assertEqual(marker["state"], "completed")
                self.assertEqual(marker["phase"], "snapshot_persisted")
                self.assertEqual(marker["failure"], "coordination_changed")
                self.assertTrue(
                    all(
                        persisted.get(field) is None
                        for field in ("active", "releasing", "granting")
                    )
                )
                self.assertEqual(
                    len(
                        [
                            event
                            for event in events
                            if event.get("event") == "session_grant_revoked"
                            and event.get("lease_id") == "lease-immediate"
                        ]
                    ),
                    1,
                )

                recovery = recover_coordination_startup(
                    paths, store, "generation-b"
                )
                self.assertIsNone(recovery.audit_fault)
                self.assertEqual(store.load_with_sha(), (None, None))

    def test_fifo_prepublish_aborts_are_durable_and_restart_cleanly(self) -> None:
        for boundary, mutation, expected_revocations in (
            ("session_grant_prepared", "coordination_changed", 0),
            ("session_granted", "operation_cancelled", 1),
        ):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as root:
                paths = _paths(Path(root))
                store = CoordinationFaultStore(paths)
                snapshots = CoordinationSnapshotStore(paths, "generation-a")
                events: list[dict[str, object]] = []
                owner = _identity("fifo-owner")
                waiter = _identity("fifo-waiter")
                ids = iter(("lease-owner", "ticket-waiter", "lease-waiter"))
                fault_ids = iter(("fault-owner", "fault-release", "fault-waiter"))
                operation_id = "operation-waiter"
                coordinator: SessionCoordinator
                armed_cutpoint = False
                triggered = False

                class CrashBeforeClear(BaseException):
                    pass

                def audit(event: dict[str, object]) -> bool:
                    nonlocal triggered
                    events.append(dict(event))
                    if (
                        armed_cutpoint
                        and not triggered
                        and event.get("event") == boundary
                        and event.get("reason") == "fifo_head"
                    ):
                        triggered = True
                        with coordinator._condition:
                            if mutation == "operation_cancelled":
                                coordinator._operation_tombstones[
                                    (waiter, operation_id)
                                ] = coordinator._time_fn() + 300.0
                                for ticket in tuple(coordinator._queue):
                                    if ticket.client == waiter:
                                        coordinator._queue.remove(ticket)
                            else:
                                coordinator._handoff_pending = True
                            coordinator._bump_revision_locked()
                            coordinator._condition.notify_all()
                    return True

                def crash_before_clear(fault_id: str, sha256: str) -> bool:
                    marker = store.load()
                    if (
                        marker is not None
                        and marker.get("lease_id") == "lease-waiter"
                        and marker.get("state") == "completed"
                    ):
                        raise CrashBeforeClear()
                    return store.clear(fault_id, sha256)

                coordinator = SessionCoordinator(
                    token_fn=lambda: "secret-token",
                    id_fn=lambda: next(ids),
                    fault_id_fn=lambda: next(fault_ids),
                    daemon_generation="generation-a",
                    utc_now_fn=lambda: "2026-07-22T00:00:00Z",
                    audit=audit,
                    fault_arm=store.arm,
                    fault_transition=store.transition,
                    fault_clear=crash_before_clear,
                    persist_snapshot=snapshots.write_coordination,
                )
                active = coordinator.acquire(owner, "drive", "operation-owner")[1]
                queued = coordinator.acquire(waiter, "camera", operation_id)[1]
                self.assertEqual(
                    coordinator.release(owner, active["lease_token"])[0], 200
                )
                with coordinator._condition:
                    self.assertTrue(
                        coordinator._condition.wait_for(
                            lambda: not coordinator._handoff_pending
                            and coordinator._wal_marker is None,
                            timeout=1.0,
                        )
                    )
                armed_cutpoint = True

                with self.assertRaises(CrashBeforeClear):
                    coordinator.wait(waiter, queued["ticket"], 0.0)

                marker = store.load()
                persisted = json.loads(paths.coordination_path.read_text(encoding="utf-8"))
                self.assertEqual(marker["state"], "completed")
                self.assertEqual(marker["phase"], "snapshot_persisted")
                self.assertEqual(marker["failure"], "coordination_changed")
                self.assertTrue(
                    all(
                        persisted.get(field) is None
                        for field in ("active", "releasing", "granting")
                    )
                )
                self.assertEqual(
                    len(
                        [
                            event
                            for event in events
                            if event.get("event") == "session_grant_revoked"
                            and event.get("lease_id") == "lease-waiter"
                        ]
                    ),
                    expected_revocations,
                )

                recovery = recover_coordination_startup(
                    paths, store, "generation-b"
                )
                self.assertIsNone(recovery.audit_fault)
                self.assertEqual(store.load_with_sha(), (None, None))

    def test_arm_failure_writes_no_grant_or_token(self) -> None:
        def fail_arm(_marker: dict[str, object]) -> str:
            raise OSError("disk unavailable")

        coordinator = SessionCoordinator(
            token_fn=lambda: "secret-token",
            id_fn=lambda: "lease-1",
            fault_id_fn=lambda: "fault-1",
            daemon_generation="generation-a",
            utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            audit=lambda event: self.events.append(dict(event)) or True,
            fault_arm=fail_arm,
            fault_transition=self.fault_store.transition,
            fault_clear=self.fault_store.clear,
            persist_snapshot=self.snapshot_store.write_coordination,
        )
        result = coordinator.acquire(_identity("a"), "drive")

        self.assertEqual((result[0], result[1]["error"]), (503, "audit_failed"))
        self.assertEqual(self.events, [])
        self.assertIsNone(coordinator.snapshot_payload()["active"])
        self.assertNotIn("secret-token", repr(result))

    def test_snapshot_failure_compensates_and_latches_fault_without_token(self) -> None:
        coordinator = self._coordinator(persist=lambda _payload: False)
        result = coordinator.acquire(_identity("a"), "drive")
        marker, _sha = self.fault_store.load_with_sha()

        self.assertEqual((result[0], result[1]["error"]), (503, "audit_failed"))
        self.assertIsNone(coordinator.snapshot_payload()["active"])
        self.assertEqual(marker["state"], "fault")
        self.assertEqual(marker["failure"], "snapshot_failed")
        self.assertEqual(
            [event["event"] for event in self.events],
            ["session_acquire", "session_granted", "session_grant_revoked"],
        )
        self.assertNotIn("secret-token", repr((result, self.events, marker)))

    def test_fifo_live_wait_uses_same_wal_and_snapshot_protocol(self) -> None:
        ids = iter(("lease-a", "ticket-b", "lease-b"))
        faults = iter(("fault-grant-a", "fault-release-a", "fault-grant-b"))
        coordinator = SessionCoordinator(
            token_fn=lambda: "secret-token",
            id_fn=lambda: next(ids),
            fault_id_fn=lambda: next(faults),
            daemon_generation="generation-a",
            utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            audit=lambda event: self.events.append(dict(event)) or True,
            fault_arm=self.fault_store.arm,
            fault_transition=self.fault_store.transition,
            fault_clear=self.fault_store.clear,
            persist_snapshot=self.snapshot_store.write_coordination,
        )
        active = coordinator.acquire(_identity("a"), "drive")[1]
        queued = coordinator.acquire(_identity("b"), "camera")[1]
        coordinator.release(_identity("a"), active["lease_token"])

        claimed = coordinator.wait(_identity("b"), queued["ticket"], 0.0)

        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))
        self.assertEqual(self.fault_store.load_with_sha(), (None, None))
        persisted = json.loads(self.paths.coordination_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["active"]["lease_id"], "lease-b")
        self.assertEqual(
            [
                event["event"]
                for event in self.events
                if event.get("reason") == "fifo_head"
            ],
            ["session_grant_prepared", "session_granted"],
        )

    def test_release_arms_before_invalidation_and_clears_after_terminal_snapshot(self) -> None:
        coordinator = self._coordinator()
        active = coordinator.acquire(_identity("a"), "drive")[1]

        released = coordinator.release(_identity("a"), active["lease_token"])

        self.assertEqual((released[0], released[1]["released"]), (200, True))
        self.assertNotIn("audit_failed", released[1].get("cleanup_degraded") or [])
        with coordinator._condition:
            self.assertTrue(
                coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=1.0
                )
            )
        self.assertEqual(self.fault_store.load_with_sha(), (None, None))
        persisted = json.loads(self.paths.coordination_path.read_text(encoding="utf-8"))
        self.assertIsNone(persisted["active"])
        self.assertIsNone(persisted["releasing"])
        self.assertFalse(persisted["handoff_pending"])
        names = [event["event"] for event in self.events]
        self.assertLess(names.index("session_release_started"), names.index("session_release_finished"))

    def test_slow_successful_release_audit_is_not_reported_failed(self) -> None:
        finished = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            self.events.append(dict(event))
            if event.get("event") == "session_release_finished":
                time.sleep(0.2)
                finished.set()
            return True

        coordinator = SessionCoordinator(
            token_fn=lambda: "secret-token",
            id_fn=lambda: "lease-1",
            fault_id_fn=lambda: "fault-1",
            daemon_generation="generation-a",
            utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            audit=audit,
            fault_arm=self.fault_store.arm,
            fault_transition=self.fault_store.transition,
            fault_clear=self.fault_store.clear,
            persist_snapshot=self.snapshot_store.write_coordination,
        )
        active = coordinator.acquire(_identity("a"), "drive")[1]
        released = coordinator.release(_identity("a"), active["lease_token"])

        self.assertEqual((released[0], released[1]["released"]), (200, True))
        self.assertEqual(released[1]["cleanup_degraded"], [])
        self.assertTrue(coordinator.snapshot_payload()["handoff_pending"])
        with coordinator._condition:
            self.assertFalse(coordinator._handoff_audit_failed)

        self.assertTrue(finished.wait(1.0))
        with coordinator._condition:
            self.assertTrue(
                coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=1.0
                )
            )
        for thread in threading.enumerate():
            if thread.name.startswith("dayz-mcp-release-audit-"):
                thread.join(1.0)
        snapshot = coordinator.snapshot_payload()
        self.assertFalse(snapshot["handoff_pending"])
        self.assertIsNone(snapshot["audit_fault"])
        self.assertIn(
            "session_release_finished",
            [event["event"] for event in self.events],
        )

    def test_release_terminal_audit_failure_latches_durable_fault(self) -> None:
        def audit(event: dict[str, object]) -> bool:
            self.events.append(dict(event))
            return event.get("event") != "session_release_finished"

        coordinator = SessionCoordinator(
            token_fn=lambda: "secret-token",
            id_fn=lambda: "lease-1",
            fault_id_fn=iter(("fault-grant", "fault-release")).__next__,
            daemon_generation="generation-a",
            utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            audit=audit,
            fault_arm=self.fault_store.arm,
            fault_transition=self.fault_store.transition,
            fault_clear=self.fault_store.clear,
            persist_snapshot=self.snapshot_store.write_coordination,
        )
        active = coordinator.acquire(_identity("a"), "drive")[1]
        released = coordinator.release(_identity("a"), active["lease_token"])
        marker, _sha = self.fault_store.load_with_sha()

        self.assertIn("audit_failed", released[1]["cleanup_degraded"])
        self.assertEqual(marker["fault_id"], "fault-release")
        self.assertEqual(marker["state"], "fault")
        self.assertEqual(marker["operation"], "release")
        snapshot = coordinator.snapshot_payload()
        self.assertFalse(snapshot["claimable"])
        self.assertEqual(snapshot["audit_fault"]["fault_id"], "fault-release")


class AuditRepairCliTests(unittest.TestCase):
    def test_cli_uses_status_fault_id_and_exact_repair_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "key"
            keyfile.write_text("secret-key", encoding="utf-8")
            calls: list[tuple[str, str, dict[str, object] | None]] = []

            def request(
                actual_policy: object,
                method: str,
                path: str,
                payload: dict[str, object] | None = None,
            ) -> tuple[int, dict[str, object]]:
                calls.append((method, path, payload))
                if method == "GET":
                    return 200, {
                        "coordination": {"audit_fault": {"fault_id": "fault-1"}}
                    }
                return 200, {"repaired": True, "fault_id": "fault-1"}

            with (
                mock.patch.object(
                    admin_cli.daemon_policy,
                    "load_daemon_policy",
                    return_value=object(),
                ),
                mock.patch.object(admin_cli, "_request", side_effect=request),
                mock.patch.object(admin_cli.sys.stdin, "isatty", return_value=True),
                mock.patch("builtins.input", return_value="REPAIR fault-1"),
                redirect_stdout(io.StringIO()) as output,
            ):
                exit_code = admin_cli.main(
                    [
                        "--daemon-policy",
                        "normal",
                        "audit-repair",
                        "--reason",
                        "operator repair",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(calls[1][0:2], ("POST", "/admin/audit-repair"))
            self.assertEqual(
                calls[1][2],
                {
                    "fault_id": "fault-1",
                    "reason": "operator repair",
                    "confirmation": "REPAIR fault-1",
                },
            )
            self.assertNotIn("secret-key", output.getvalue())


if __name__ == "__main__":
    unittest.main()
