import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from dayz_mcp.runtime_state import (
    AUDIT_BACKUPS,
    CoordinationSnapshotStore,
    JsonlAuditWriter,
    LifecycleRecoveryFaultStore,
    RuntimePaths,
)
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator


class FakeClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SequentialIds:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> str:
        return next(self._values)


class BlockingSnapshot(dict[str, object]):
    def __init__(
        self,
        payload: dict[str, object],
        entered: threading.Event,
        resume: threading.Event,
    ) -> None:
        super().__init__(payload)
        self._entered = entered
        self._resume = resume
        self._blocked = False

    def get(self, key: str, default: object = None) -> object:
        if key == "captured_at_monotonic" and not self._blocked:
            self._blocked = True
            self._entered.set()
            if not self._resume.wait(2.0):
                raise RuntimeError("test_snapshot_resume_timeout")
        return super().get(key, default)


class RuntimeStateTest(unittest.TestCase):
    def test_lifecycle_recovery_fault_events_are_create_only_and_cas_chained(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(
                paths,
                fault_id_fn=lambda: "11111111-1111-4111-8111-111111111111",
                utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            )
            active = store.arm(
                scope="run",
                reason="identity_ambiguous",
                manifest_sha256="a" * 64,
                backup_receipt_sha256="b" * 64,
                run_id="run-1",
                launch_operation_id="22222222-2222-4222-8222-222222222222",
                run_record_sha256="c" * 64,
            )
            first_head = active["pointer"]["head_event_sha256"]
            with self.assertRaisesRegex(RuntimeError, "lifecycle_recovery_cas_conflict"):
                store.transition(
                    active["fault"]["fault_id"],
                    "0" * 64,
                    state="repairing",
                    expected_manifest_sha256="a" * 64,
                )

            second_head = store.transition(
                active["fault"]["fault_id"],
                first_head,
                state="repairing",
                expected_manifest_sha256="a" * 64,
            )
            loaded = store.load_active()
            self.assertEqual(loaded["event"]["state"], "repairing")
            self.assertEqual(loaded["event"]["previous_event_sha256"], first_head)
            self.assertEqual(loaded["pointer"]["head_event_sha256"], second_head)
            event_zero = paths.lifecycle_recovery_faults_dir / active["fault"]["fault_id"] / "events" / "00000000.json"
            before = event_zero.read_bytes()
            self.assertEqual(store.load_active()["fault"], active["fault"])
            self.assertEqual(event_zero.read_bytes(), before)

    def test_lifecycle_recovery_repaired_requires_create_only_receipt(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(
                paths,
                fault_id_fn=lambda: "11111111-1111-4111-8111-111111111111",
                utc_now_fn=lambda: "2026-07-22T00:00:00Z",
            )
            active = store.arm(
                scope="manifest",
                reason="manifest_corrupt",
                manifest_sha256="a" * 64,
                backup_receipt_sha256="b" * 64,
            )
            head = store.transition(
                active["fault"]["fault_id"],
                active["pointer"]["head_event_sha256"],
                state="repairing",
                expected_manifest_sha256="a" * 64,
            )
            with self.assertRaisesRegex(ValueError, "invalid_lifecycle_recovery_transition"):
                store.transition(
                    active["fault"]["fault_id"],
                    head,
                    state="repaired",
                    expected_manifest_sha256="a" * 64,
                    evidence_sha256="d" * 64,
                )
            receipt_sha = store.create_receipt(
                active["fault"]["fault_id"],
                {"manifest_sha256": "a" * 64, "all_relevant_runs_terminal": True},
            )
            repaired_head = store.transition(
                active["fault"]["fault_id"],
                head,
                state="repaired",
                expected_manifest_sha256="a" * 64,
                evidence_sha256=receipt_sha,
            )
            self.assertEqual(store.load_active()["event"]["state"], "repaired")
            self.assertEqual(store.load_active()["pointer"]["head_event_sha256"], repaired_head)

    def test_lifecycle_manifest_backup_is_byte_exact_and_hash_bound(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            raw = b'{"version":1,"runs":[]}\n'

            receipt_sha = store.create_manifest_backup(raw)
            self.assertEqual(store.read_manifest_backup(receipt_sha), raw)
            receipt = paths.lifecycle_recovery_faults_dir / "backups" / receipt_sha / "receipt.json"
            self.assertTrue(receipt.exists())
            (receipt.parent / "manifest.bin").write_bytes(b"drift")
            with self.assertRaisesRegex(ValueError, "invalid_lifecycle_manifest_backup"):
                store.read_manifest_backup(receipt_sha)
    def test_runtime_path_uses_localappdata_not_onedrive(self) -> None:
        paths = RuntimePaths.from_env(
            {"LOCALAPPDATA": r"C:\Local", "OneDrive": r"C:\Cloud"}
        )

        self.assertEqual(paths.root, Path(r"C:\Local\DayZ_MCP"))

    def test_runtime_path_requires_localappdata(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "^localappdata_unavailable$"):
            RuntimePaths.from_env({"OneDrive": r"C:\Cloud"})

    def test_stores_require_nonempty_generation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            with self.assertRaisesRegex(ValueError, "^invalid_daemon_generation$"):
                JsonlAuditWriter(paths, "")
            with self.assertRaisesRegex(ValueError, "^invalid_daemon_generation$"):
                CoordinationSnapshotStore(paths, "")

    def test_audit_rejects_missing_empty_or_invalid_reason_and_duration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            writer = JsonlAuditWriter(
                paths,
                "generation-a",
                utc_now_fn=lambda: "2026-07-15T00:00:00Z",
            )
            valid = {
                "event": "session_acquire",
                "decision": "allowed",
                "reason": "request",
                "duration_s": 0.0,
            }
            invalid_events = [
                {key: value for key, value in valid.items() if key != "reason"},
                {**valid, "reason": ""},
                {**valid, "reason": "   "},
                {key: value for key, value in valid.items() if key != "duration_s"},
                {**valid, "duration_s": None},
                {**valid, "duration_s": True},
                {**valid, "duration_s": -0.001},
                {**valid, "duration_s": float("nan")},
                {**valid, "duration_s": float("inf")},
            ]

            for invalid in invalid_events:
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(
                        ValueError, "^invalid_audit_event$"
                    ):
                        writer.write(invalid)

            self.assertFalse(writer.current_path.exists())
            self.assertTrue(writer.write(valid))

    def test_audit_redacts_recursive_secret_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            writer = JsonlAuditWriter(
                paths,
                "generation-a",
                utc_now_fn=lambda: "2026-07-15T00:00:00Z",
            )

            writer.write(
                {
                    "event": "grant",
                    "decision": "allowed",
                    "reason": "request",
                    "duration_s": 0.0,
                    "lease_token": "secret",
                    "nested": {"key": "secret"},
                }
            )

            text = writer.current_path.read_text(encoding="utf-8")
            self.assertNotIn("secret", text)
            self.assertIn('"lease_token":"[REDACTED]"', text)

            event = json.loads(text)
            self.assertEqual(event["daemon_generation"], "generation-a")
            self.assertEqual(event["timestamp_utc"], "2026-07-15T00:00:00Z")
            self.assertEqual(event["duration_s"], 0.0)

    def test_audit_rotation_happens_before_append_and_retains_five_backups(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            writer = JsonlAuditWriter(
                paths,
                "generation-a",
                utc_now_fn=lambda: "2026-07-15T00:00:00Z",
            )
            writer.current_path.parent.mkdir(parents=True)
            writer.current_path.write_text("x" * 450, encoding="utf-8")
            for index in range(1, AUDIT_BACKUPS + 1):
                Path(f"{writer.current_path}.{index}").write_text(
                    f"backup-{index}", encoding="utf-8"
                )

            with patch("dayz_mcp.runtime_state.AUDIT_MAX_BYTES", 512):
                writer.write(
                    {
                        "event": "session_acquire",
                        "decision": "allowed",
                        "reason": "request",
                        "duration_s": 0.0,
                    }
                )

            current = writer.current_path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(current)["event"], "session_acquire")
            self.assertEqual(
                Path(f"{writer.current_path}.1").read_text(encoding="utf-8"),
                "x" * 450,
            )
            self.assertEqual(
                Path(f"{writer.current_path}.2").read_text(encoding="utf-8"),
                "backup-1",
            )
            self.assertEqual(
                Path(f"{writer.current_path}.5").read_text(encoding="utf-8"),
                "backup-4",
            )

    def test_snapshot_write_uses_sibling_tmp_fsync_and_replace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = CoordinationSnapshotStore(paths, "generation-a")
            with patch(
                "dayz_mcp.runtime_state.os.open", wraps=os.open
            ) as open_call, patch(
                "dayz_mcp.runtime_state.os.fsync", wraps=os.fsync
            ) as fsync_call, patch(
                "dayz_mcp.runtime_state.os.replace", wraps=os.replace
            ) as replace_call:
                store.write_coordination(
                    {"revision": 0, "active": None, "queue": []}
                )

            fsync_call.assert_called_once()
            replace_call.assert_called_once()
            temporary_path, target_path = replace_call.call_args.args
            temporary_path = Path(temporary_path)
            self.assertEqual(target_path, store.coordination_path)
            self.assertEqual(temporary_path.parent, store.coordination_path.parent)
            self.assertTrue(
                temporary_path.name.startswith(store.coordination_path.name + ".tmp.")
            )
            self.assertFalse(temporary_path.exists())
            matching_open = [
                call
                for call in open_call.call_args_list
                if Path(call.args[0]) == temporary_path
            ]
            self.assertEqual(len(matching_open), 1)
            flags = matching_open[0].args[1]
            self.assertTrue(flags & os.O_CREAT)
            self.assertTrue(flags & os.O_EXCL)

    def test_snapshot_write_never_reuses_preexisting_fixed_temporary(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = CoordinationSnapshotStore(paths, "generation-a")
            fixed_temporary = store.coordination_path.with_name(
                store.coordination_path.name + ".tmp"
            )
            fixed_temporary.parent.mkdir(parents=True)
            fixed_temporary.write_bytes(b"preexisting-evidence")

            store.write_coordination(
                {"revision": 0, "active": None, "queue": []}
            )

            self.assertEqual(fixed_temporary.read_bytes(), b"preexisting-evidence")
            self.assertEqual(
                list(store.coordination_path.parent.glob("coordination.json.tmp.*")),
                [],
            )

    def test_snapshot_write_rejects_target_changed_before_replace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = CoordinationSnapshotStore(paths, "generation-a")
            store.write_coordination(
                {"revision": 0, "active": None, "queue": []}
            )
            real_fsync = os.fsync
            changed = False

            def fsync_then_replace_target(descriptor: int) -> None:
                nonlocal changed
                real_fsync(descriptor)
                if not changed:
                    changed = True
                    store.coordination_path.write_bytes(b"replacement")

            with patch(
                "dayz_mcp.runtime_state.os.fsync",
                side_effect=fsync_then_replace_target,
            ):
                with self.assertRaisesRegex(RuntimeError, "^atomic_target_changed$"):
                    store.write_coordination(
                        {"revision": 1, "active": None, "queue": []}
                    )

            self.assertEqual(store.coordination_path.read_bytes(), b"replacement")
            self.assertEqual(
                list(store.coordination_path.parent.glob("coordination.json.tmp.*")),
                [],
            )

    def test_snapshot_write_fsync_failure_preserves_target_and_removes_unique_temp(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = CoordinationSnapshotStore(paths, "generation-a")
            store.write_coordination(
                {"revision": 0, "active": None, "queue": []}
            )
            before = store.coordination_path.read_bytes()

            with patch(
                "dayz_mcp.runtime_state.os.fsync",
                side_effect=OSError("simulated fsync failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated fsync failure"):
                    store.write_coordination(
                        {"revision": 1, "active": None, "queue": []}
                    )

            self.assertEqual(store.coordination_path.read_bytes(), before)
            self.assertEqual(
                list(store.coordination_path.parent.glob("coordination.json.tmp.*")),
                [],
            )

    def test_snapshot_write_rejects_reparse_target_when_symlinks_are_available(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = CoordinationSnapshotStore(paths, "generation-a")
            store.coordination_path.parent.mkdir(parents=True)
            outside = Path(temp_dir) / "outside.json"
            outside.write_bytes(b"outside-evidence")
            try:
                os.symlink(outside, store.coordination_path)
            except OSError as exc:
                from dayz_mcp import runtime_state

                store.coordination_path.write_bytes(b"existing-evidence")
                real_lstat = Path.lstat

                class ReparseStat:
                    def __init__(self, wrapped: os.stat_result) -> None:
                        self._wrapped = wrapped
                        self.st_file_attributes = getattr(
                            wrapped, "st_file_attributes", 0
                        ) | 0x400

                    def __getattr__(self, name: str) -> object:
                        return getattr(self._wrapped, name)

                def lstat_with_reparse(path: Path) -> os.stat_result | ReparseStat:
                    value = real_lstat(path)
                    if path == store.coordination_path:
                        return ReparseStat(value)
                    return value

                with patch.object(
                    Path,
                    "lstat",
                    autospec=True,
                    side_effect=lstat_with_reparse,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "^unsafe_runtime_path$"
                    ):
                        runtime_state._atomic_write_text(
                            store.coordination_path, "replacement\n"
                        )
                self.assertEqual(
                    store.coordination_path.read_bytes(), b"existing-evidence"
                )
                self.assertIn("1314", str(exc))
                return

            with self.assertRaisesRegex(RuntimeError, "^unsafe_runtime_path$"):
                store.write_coordination(
                    {"revision": 0, "active": None, "queue": []}
                )

            self.assertEqual(outside.read_bytes(), b"outside-evidence")

    def test_snapshot_persists_only_public_coordination_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = CoordinationSnapshotStore(paths, "generation-a")
            store.write_coordination(
                {
                    "revision": 7,
                    "captured_at_monotonic": 10.0,
                    "active": {
                        "lease_id": "lease-a",
                        "session": "public-a",
                        "granted_at_monotonic": 1.0,
                        "expires_at_monotonic": 121.0,
                        "lease_token": "token-shaped-value",
                        "pid": 123,
                        "cmdline": "python --key secret",
                    },
                    "granting": {
                        "lease_id": "lease-g",
                        "session": "public-g",
                        "granted_at_monotonic": 4.0,
                        "expires_at_monotonic": 124.0,
                        "ticket": "ticket-g",
                        "lease_token": "grant-token-shaped-value",
                    },
                    "handoff_pending": True,
                    "audit_fault": None,
                    "queue": [
                        {
                            "ticket": "ticket-b",
                            "session": "public-b",
                            "created_at_monotonic": 2.0,
                            "touched_at_monotonic": 3.0,
                            "purpose": "must-not-persist",
                        }
                    ],
                    "args": {"password": "secret"},
                }
            )

            payload = json.loads(store.coordination_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload,
                {
                    "daemon_generation": "generation-a",
                    "revision": 7,
                    "captured_at_monotonic": 10.0,
                    "active": {
                        "lease_id": "lease-a",
                        "session": "public-a",
                        "granted_at_monotonic": 1.0,
                        "expires_at_monotonic": 121.0,
                    },
                    "releasing": None,
                    "granting": {
                        "lease_id": "lease-g",
                        "session": "public-g",
                        "granted_at_monotonic": 4.0,
                        "expires_at_monotonic": 124.0,
                        "ticket": "ticket-g",
                    },
                    "handoff_pending": True,
                    "audit_fault": None,
                    "queue": [
                        {
                            "ticket": "ticket-b",
                            "session": "public-b",
                            "created_at_monotonic": 2.0,
                            "touched_at_monotonic": 3.0,
                        }
                    ],
                },
            )

    def test_restart_granting_preserves_public_client_lease_and_ticket(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            CoordinationSnapshotStore(paths, "old").write_coordination(
                {
                    "revision": 4,
                    "active": None,
                    "releasing": None,
                    "granting": {
                        "lease_id": "lease-g",
                        "session": "public-g",
                        "ticket": "ticket-g",
                        "lease_token": "secret",
                    },
                    "handoff_pending": True,
                    "queue": [],
                }
            )

            event = CoordinationSnapshotStore(
                paths, "new"
            ).consume_previous_generation("new")

            self.assertEqual(event["clients"], [{"session": "public-g"}])
            self.assertEqual(event["lease_ids"], ["lease-g"])
            self.assertEqual(event["ticket_ids"], ["ticket-g"])
            persisted = json.loads(paths.coordination_path.read_text(encoding="utf-8"))
            self.assertIsNone(persisted["granting"])
            self.assertFalse(persisted["handoff_pending"])
            self.assertNotIn("secret", json.dumps(persisted))

    def test_snapshot_store_accepts_only_strictly_newer_integer_revision(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = CoordinationSnapshotStore(paths, "generation-a")

            self.assertTrue(
                store.write_coordination(
                    {"revision": 1, "active": None, "queue": []}
                )
            )
            self.assertFalse(
                store.write_coordination(
                    {"revision": 1, "active": None, "queue": []}
                )
            )
            self.assertFalse(
                store.write_coordination(
                    {"revision": 0, "active": None, "queue": []}
                )
            )
            self.assertTrue(
                store.write_coordination(
                    {
                        "revision": 2,
                        "active": {"lease_id": "new", "session": "public-new"},
                        "queue": [],
                    }
                )
            )
            persisted = json.loads(
                store.coordination_path.read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["revision"], 2)
            self.assertEqual(persisted["active"]["lease_id"], "new")

            self.assertTrue(
                store.ensure_coordination(
                    {
                        "revision": 2,
                        "active": {"lease_id": "new", "session": "public-new"},
                        "queue": [],
                    }
                )
            )
            self.assertTrue(
                store.ensure_coordination(
                    {
                        "revision": 2,
                        "active": {"lease_id": "drift", "session": "public-new"},
                        "queue": [],
                    }
                )
            )
            touch_store = CoordinationSnapshotStore(
                RuntimePaths.from_env(
                    {"LOCALAPPDATA": str(Path(temp_dir) / "touch")}
                ),
                "generation-a",
            )
            self.assertTrue(
                touch_store.write_coordination(
                    {
                        "revision": 1,
                        "active": None,
                        "queue": [
                            {
                                "ticket": "T",
                                "session": "public",
                                "created_at_monotonic": 1.0,
                                "touched_at_monotonic": 2.0,
                            }
                        ],
                    }
                )
            )
            self.assertTrue(
                touch_store.ensure_coordination(
                    {
                        "revision": 1,
                        "active": None,
                        "queue": [
                            {
                                "ticket": "T",
                                "session": "public",
                                "created_at_monotonic": 1.0,
                                "touched_at_monotonic": 3.0,
                            }
                        ],
                    }
                )
            )
            self.assertTrue(
                store.write_coordination(
                    {"revision": 3, "active": None, "queue": []}
                )
            )
            self.assertTrue(
                store.ensure_coordination(
                    {"revision": 2, "active": None, "queue": []}
                )
            )

            for invalid in ({}, {"revision": True}, {"revision": -1}, {"revision": 1.5}):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(
                        ValueError, "^invalid_coordination_revision$"
                    ):
                        CoordinationSnapshotStore(
                            RuntimePaths.from_env(
                                {"LOCALAPPDATA": str(Path(temp_dir) / "invalid")}
                            ),
                            "generation-a",
                        ).write_coordination(invalid)

    def test_concurrent_older_snapshot_finishes_last_without_overwriting_newer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = CoordinationSnapshotStore(paths, "generation-a")
            entered = threading.Event()
            resume = threading.Event()
            finish_order: list[str] = []
            results: dict[str, bool] = {}
            old_payload = BlockingSnapshot(
                {
                    "revision": 1,
                    "captured_at_monotonic": 1.0,
                    "active": {"lease_id": "old", "session": "public-old"},
                    "queue": [],
                },
                entered,
                resume,
            )

            def write_old() -> None:
                results["old"] = store.write_coordination(old_payload)
                finish_order.append("old")

            old_thread = threading.Thread(target=write_old)
            old_thread.start()
            self.assertTrue(entered.wait(1.0), "older write did not overlap")
            results["new"] = store.write_coordination(
                {
                    "revision": 2,
                    "captured_at_monotonic": 2.0,
                    "active": {"lease_id": "new", "session": "public-new"},
                    "queue": [],
                }
            )
            finish_order.append("new")
            resume.set()
            old_thread.join(2.0)
            self.assertFalse(old_thread.is_alive())

            self.assertEqual(finish_order, ["new", "old"])
            self.assertEqual(results, {"new": True, "old": False})
            persisted = json.loads(
                store.coordination_path.read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["revision"], 2)
            self.assertEqual(persisted["active"]["lease_id"], "new")

    def test_restart_invalidates_without_persisting_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            old_store = CoordinationSnapshotStore(paths, "old")
            old_store.write_coordination(
                {
                    "revision": 3,
                    "active": {
                        "lease_id": "L",
                        "lease_token": "secret",
                    }
                }
            )
            new_store = CoordinationSnapshotStore(paths, "new")

            loaded = new_store.consume_previous_generation("new")

            self.assertEqual(loaded["previous_generation"], "old")
            self.assertEqual(loaded["event"], "daemon_restart_invalidated")
            text = new_store.coordination_path.read_text(encoding="utf-8")
            self.assertNotIn("secret", text)
            self.assertEqual(json.loads(text)["revision"], 0)

    def test_restart_releasing_only_preserves_public_client_and_lease_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            CoordinationSnapshotStore(paths, "old").write_coordination(
                {
                    "revision": 8,
                    "active": None,
                    "releasing": {
                        "lease_id": "lease-r",
                        "session": "public-r",
                        "lease_token": "secret",
                    },
                    "queue": [],
                }
            )

            event = CoordinationSnapshotStore(
                paths, "new"
            ).consume_previous_generation("new")

            self.assertEqual(event["clients"], [{"session": "public-r"}])
            self.assertEqual(event["lease_ids"], ["lease-r"])
            self.assertEqual(event["ticket_ids"], [])
            persisted = json.loads(paths.coordination_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["revision"], 0)
            self.assertIsNone(persisted["active"])
            self.assertIsNone(persisted["releasing"])
            self.assertEqual(persisted["queue"], [])

    def test_restart_mixed_state_preserves_every_public_client_and_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            CoordinationSnapshotStore(paths, "old").write_coordination(
                {
                    "revision": 9,
                    "active": {"lease_id": "lease-a", "session": "public-a"},
                    "releasing": {"lease_id": "lease-r", "session": "public-r"},
                    "queue": [
                        {"ticket": "ticket-b", "session": "public-b"},
                        {"ticket": "ticket-c", "session": "public-c"},
                    ],
                }
            )

            event = CoordinationSnapshotStore(
                paths, "new"
            ).consume_previous_generation("new")

            self.assertEqual(
                event["clients"],
                [
                    {"session": "public-a"},
                    {"session": "public-r"},
                    {"session": "public-b"},
                    {"session": "public-c"},
                ],
            )
            self.assertEqual(event["lease_ids"], ["lease-a", "lease-r"])
            self.assertEqual(event["ticket_ids"], ["ticket-b", "ticket-c"])

    def test_raw_runtime_files_never_contain_secrets_tokens_cmdline_or_args(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            writer = JsonlAuditWriter(
                paths,
                "generation-a",
                utc_now_fn=lambda: "2026-07-15T00:00:00Z",
            )
            token = "tok_live_0123456789abcdefghijklmnopqrstuvwxyz"
            writer.write(
                {
                    "event": "session_authorized",
                    "decision": "allowed",
                    "reason": "lease_valid",
                    "duration_s": 0.0,
                    "client": {"session": "public-a"},
                    "nested": [
                        {"TOKEN": token},
                        {"cmdline": f"python --token {token}"},
                        {"request_args": {"key": "secret"}},
                    ],
                }
            )
            CoordinationSnapshotStore(paths, "generation-a").write_coordination(
                {
                    "revision": 1,
                    "active": {
                        "lease_id": "lease-a",
                        "session": "public-a",
                        "lease_token": token,
                    },
                    "queue": [],
                }
            )

            runtime_files = list(paths.root.rglob("*.json")) + list(
                paths.root.rglob("*.jsonl")
            )
            self.assertTrue(runtime_files)
            for runtime_file in runtime_files:
                text = runtime_file.read_text(encoding="utf-8")
                self.assertNotIn("secret", text, runtime_file)
                self.assertNotIn(token, text, runtime_file)
                self.assertNotIn("python --token", text, runtime_file)


class CoordinatorSnapshotTest(unittest.TestCase):
    def test_revision_increments_only_when_observable_state_changes(self) -> None:
        clock = FakeClock()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=lambda: "token-shaped-value",
            id_fn=SequentialIds("lease-a", "ticket-b"),
            audit=lambda _event: None,
        )
        owner = ClientIdentity(
            "codex", 101, 10, "2026-07-15T00:00:00Z", "session-a", "owner"
        )
        queued = ClientIdentity(
            "claude", 202, 20, "2026-07-15T00:01:00Z", "session-b", "queued"
        )

        self.assertEqual(coordinator.snapshot_payload()["revision"], 0)
        _, active = coordinator.acquire(owner, "drive")
        self.assertEqual(coordinator.snapshot_payload()["revision"], 3)
        coordinator.acquire(owner, "drive")
        coordinator.release(queued, active["lease_token"])
        self.assertEqual(coordinator.snapshot_payload()["revision"], 3)

        _, ticket = coordinator.acquire(queued, "weather")
        self.assertEqual(coordinator.snapshot_payload()["revision"], 4)
        coordinator.acquire(queued, "weather")
        self.assertEqual(coordinator.snapshot_payload()["revision"], 4)

        clock.advance(1.0)
        with patch(
            "dayz_mcp.session_coordination.time.monotonic",
            side_effect=[10.0, 10.0, 10.0],
        ):
            coordinator.wait(queued, ticket["ticket"], 0.0)
        self.assertEqual(coordinator.snapshot_payload()["revision"], 5)

        clock.advance(1.0)
        coordinator.heartbeat(owner, active["lease_token"])
        self.assertEqual(coordinator.snapshot_payload()["revision"], 6)
        coordinator.authorize(queued, None, "camera_get")
        self.assertEqual(coordinator.snapshot_payload()["revision"], 6)
        coordinator.snapshot_payload()
        self.assertEqual(coordinator.snapshot_payload()["revision"], 6)

    def test_snapshot_is_pure_independent_and_fully_public(self) -> None:
        clock = FakeClock()
        audit_events: list[dict[str, object]] = []
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=lambda: "token-shaped-value",
            id_fn=SequentialIds("lease-a", "ticket-b"),
            audit=audit_events.append,
        )
        owner = ClientIdentity(
            "codex",
            101,
            10,
            "2026-07-15T00:00:00Z",
            "session-a-1234567890",
            "owner task",
        )
        queued = ClientIdentity(
            "claude",
            202,
            20,
            "2026-07-15T00:01:00Z",
            "session-b-1234567890",
            "queued task",
        )
        coordinator.acquire(owner, "drive")
        coordinator.acquire(queued, "weather")
        audit_events.clear()

        snapshot = coordinator.snapshot_payload()

        self.assertEqual(
            snapshot,
            {
                "revision": 4,
                "captured_at_monotonic": 10.0,
                "active": {
                    "lease_id": "lease-a",
                    "session": "session-a-12",
                    "granted_at_monotonic": 10.0,
                    "expires_at_monotonic": 130.0,
                },
                "releasing": None,
                "granting": None,
                "handoff_pending": False,
                "claimable": True,
                "audit_fault": None,
                "operation_tombstones": {
                    "count": 0,
                    "capacity": 128,
                    "saturated": False,
                },
                "queue": [
                    {
                        "ticket": "ticket-b",
                        "session": "session-b-12",
                        "created_at_monotonic": 10.0,
                        "touched_at_monotonic": 10.0,
                    }
                ],
                "cleanup_workers": {
                    "capacity": 4,
                    "active": 0,
                    "saturated": 0,
                },
            },
        )
        self.assertEqual(audit_events, [])
        serialized = json.dumps(snapshot)
        self.assertNotIn("token-shaped-value", serialized)
        self.assertNotIn("101", serialized)
        self.assertNotIn("202", serialized)
        self.assertNotIn("owner task", serialized)
        self.assertNotIn("queued task", serialized)

        snapshot["active"] = None
        self.assertIsNotNone(coordinator.snapshot_payload()["active"])

        clock.advance(120.0)
        still_unexpired = coordinator.snapshot_payload()
        self.assertIsNotNone(still_unexpired["active"])
        self.assertEqual(audit_events, [])

    def test_wait_and_release_emit_exact_reason_and_real_duration(self) -> None:
        clock = FakeClock()
        events: list[dict[str, object]] = []
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=lambda: "token-shaped-value",
            id_fn=SequentialIds("lease-a", "ticket-b", "lease-b"),
            audit=events.append,
        )
        owner = ClientIdentity(
            "codex", 101, 10, "2026-07-15T00:00:00Z", "session-a", "owner"
        )
        queued = ClientIdentity(
            "claude", 202, 20, "2026-07-15T00:01:00Z", "session-b", "queued"
        )
        _, active = coordinator.acquire(owner, "drive")
        _, waiting = coordinator.acquire(queued, "weather")

        with patch(
            "dayz_mcp.session_coordination.time.monotonic",
            side_effect=[10.0, 12.0, 12.0],
        ):
            coordinator.wait(queued, waiting["ticket"], 0.0)

        wait_event = [event for event in events if event["event"] == "session_wait"][-1]
        self.assertEqual(wait_event["reason"], "queued")
        self.assertEqual(wait_event["duration_s"], 2.0)

        with patch(
            "dayz_mcp.session_coordination.time.monotonic",
            side_effect=[20.0, 22.5],
        ):
            coordinator.release(owner, active["lease_token"], "owner_release")

        release_started = [
            event for event in events if event["event"] == "session_release_started"
        ][-1]
        release_finished = [
            event for event in events if event["event"] == "session_release_finished"
        ][-1]
        self.assertEqual(
            (release_started["reason"], release_started["duration_s"]),
            ("owner_release", 0.0),
        )
        self.assertEqual(
            (release_finished["reason"], release_finished["duration_s"]),
            ("owner_release", 2.5),
        )

    def test_audit_reconstructs_every_exact_event_with_common_evidence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            writer = JsonlAuditWriter(
                paths,
                "generation-a",
                utc_now_fn=lambda: "2026-07-15T00:00:00Z",
            )
            clock = FakeClock()
            coordinator = SessionCoordinator(
                time_fn=clock,
                token_fn=lambda: "token-shaped-value",
                id_fn=SequentialIds("lease-a", "ticket-b", "lease-b", "ticket-c"),
                audit=writer.write,
            )
            owner = ClientIdentity(
                "codex", 101, 10, "2026-07-15T00:00:00Z", "session-a", "owner"
            )
            queued = ClientIdentity(
                "claude", 202, 20, "2026-07-15T00:01:00Z", "session-b", "queued"
            )
            abandoned = ClientIdentity(
                "codex", 303, 30, "2026-07-15T00:02:00Z", "session-c", "abandoned"
            )

            _, active = coordinator.acquire(owner, "drive")
            _, waiting = coordinator.acquire(queued, "weather")
            coordinator.wait(queued, waiting["ticket"], 0.0)
            coordinator.heartbeat(owner, active["lease_token"])
            coordinator.authorize(
                owner, active["lease_token"], "world_spawn", operation_timeout_s=1.0
            )
            coordinator.authorize(abandoned, None, "camera_get")
            coordinator.note_command(
                owner.session_id,
                77,
                "world_spawn",
                {"token": "secret", "args": "must-not-persist"},
            )
            coordinator.authorize(queued, None, "world_spawn")
            coordinator.release(owner, active["lease_token"])
            coordinator.acquire(abandoned, "camera")
            queued_token = coordinator.wait(queued, waiting["ticket"], 0.0)[1][
                "lease_token"
            ]
            clock.advance(119.0)
            coordinator.heartbeat(queued, queued_token)
            clock.advance(1.0)
            coordinator.status(queued)
            clock.advance(119.0)
            coordinator.status(queued)

            CoordinationSnapshotStore(paths, "old").write_coordination(
                {
                    "revision": 4,
                    "active": {
                        "lease_id": "old-lease",
                        "session": "old-session",
                    },
                    "queue": [{"ticket": "old-ticket", "session": "old-session"}],
                }
            )
            restart_event = CoordinationSnapshotStore(
                paths, "generation-a"
            ).consume_previous_generation("generation-a")
            writer.write(restart_event)

            raw = writer.current_path.read_text(encoding="utf-8")
            events = [json.loads(line) for line in raw.splitlines()]
            expected_names = {
                "session_acquire",
                "session_queued",
                "session_grant_prepared",
                "session_granted",
                "session_wait",
                "session_heartbeat",
                "session_authorized",
                "session_rejected",
                "session_release_started",
                "session_release_finished",
                "session_expired",
                "ticket_cancelled",
                "daemon_restart_invalidated",
            }
            self.assertEqual({event["event"] for event in events}, expected_names)
            for event in events:
                self.assertEqual(event["timestamp_utc"], "2026-07-15T00:00:00Z")
                self.assertEqual(event["daemon_generation"], "generation-a")
                self.assertIn("decision", event)
                self.assertIn("reason", event)
                self.assertIsInstance(event["duration_s"], (int, float))
                self.assertGreaterEqual(event["duration_s"], 0.0)
                if event["event"] != "daemon_restart_invalidated":
                    self.assertIn("client", event)
                    self.assertNotIn("pid", event["client"])
                    self.assertNotIn("ppid", event["client"])

            reasons_by_event: dict[str, set[str]] = {}
            for event in events:
                reasons_by_event.setdefault(event["event"], set()).add(event["reason"])
            self.assertEqual(reasons_by_event["session_acquire"], {"request"})
            self.assertEqual(reasons_by_event["session_queued"], {"lease_busy"})
            self.assertEqual(
                reasons_by_event["session_granted"], {"request", "fifo_head"}
            )
            self.assertEqual(reasons_by_event["session_wait"], {"queued"})
            self.assertEqual(
                reasons_by_event["session_heartbeat"], {"owner_heartbeat"}
            )
            self.assertEqual(
                reasons_by_event["session_authorized"],
                {"lease_valid", "read_only"},
            )
            self.assertEqual(reasons_by_event["session_rejected"], {"lease_required"})
            self.assertEqual(
                reasons_by_event["session_release_started"], {"owner_release"}
            )
            self.assertEqual(
                reasons_by_event["session_release_finished"],
                {"owner_release", "lease_ttl"},
            )
            self.assertEqual(reasons_by_event["session_expired"], {"lease_ttl"})
            self.assertEqual(reasons_by_event["ticket_cancelled"], {"ticket_ttl"})
            self.assertEqual(
                reasons_by_event["daemon_restart_invalidated"], {"daemon_restart"}
            )

            acquire_grant = next(
                event
                for event in events
                if event["event"] == "session_acquire"
                and event["decision"] == "grant"
            )
            acquire_queue = next(
                event
                for event in events
                if event["event"] == "session_acquire"
                and event["decision"] == "queue"
            )
            self.assertEqual(acquire_grant["lease_id"], "lease-a")
            self.assertEqual(acquire_queue["ticket"], "ticket-b")
            self.assertNotIn("token-shaped-value", raw)
            self.assertNotIn("secret", raw)
            self.assertNotIn('"args"', raw)


if __name__ == "__main__":
    unittest.main()
