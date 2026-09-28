from __future__ import annotations

import hashlib
import os
import stat
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dayz_mcp import runtime_state
from dayz_mcp.runtime_state import LifecycleRecoveryFaultStore, RuntimePaths
from tests._tiers import slow_test


def _raw(seq: int) -> bytes:
    return ('{"version":1,"n":%d}\n' % seq).encode("utf-8")


def _backup_dir(paths: RuntimePaths, receipt: str) -> Path:
    return paths.lifecycle_recovery_faults_dir / "backups" / receipt


def _stamp(path: Path, seq: int) -> None:
    when = 1_000_000_000 + seq * 2
    os.utime(path, (when, when))


def _hex_dirs(paths: RuntimePaths) -> set[str]:
    backups = paths.lifecycle_recovery_faults_dir / "backups"
    if not backups.is_dir():
        return set()
    names: set[str] = set()
    for entry in backups.iterdir():
        if LifecycleRecoveryFaultStore._valid_hex64(entry.name) and entry.is_dir():
            names.add(entry.name)
    return names


def _seed(
    store: LifecycleRecoveryFaultStore,
    paths: RuntimePaths,
    count: int,
    start: int = 1,
) -> list[tuple[bytes, str]]:
    created: list[tuple[bytes, str]] = []
    for seq in range(start, start + count):
        raw = _raw(seq)
        receipt = store.create_manifest_backup(raw)
        _stamp(_backup_dir(paths, receipt), seq)
        created.append((raw, receipt))
    return created


def _assert_readable(
    store: LifecycleRecoveryFaultStore, paths: RuntimePaths
) -> None:
    backups = paths.lifecycle_recovery_faults_dir / "backups"
    if not backups.is_dir():
        return
    for entry in backups.iterdir():
        if not LifecycleRecoveryFaultStore._valid_hex64(entry.name):
            continue
        if not entry.is_dir():
            continue
        names = {child.name for child in entry.iterdir()}
        if "manifest.bin" in names and "receipt.json" in names:
            store.read_manifest_backup(entry.name)


@contextmanager
def _limits(retain: int, batch: int):
    with patch.object(
        runtime_state, "MANIFEST_BACKUP_RETAIN", retain, create=True
    ), patch.object(
        runtime_state, "MANIFEST_BACKUP_PRUNE_BATCH", batch, create=True
    ):
        yield


class Fb160eManifestBackupRetentionTest(unittest.TestCase):
    def test_fb_160e_retention_keeps_newest_retain_directories(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 5)
            with _limits(3, 32):
                new_raw = _raw(100)
                new_receipt = store.checkpoint_manifest(new_raw)
            remaining = _hex_dirs(paths)
            self.assertEqual(len(remaining), 3)
            self.assertEqual(
                remaining,
                {seeded[3][1], seeded[4][1], new_receipt},
            )
            self.assertNotIn(seeded[0][1], remaining)
            self.assertNotIn(seeded[1][1], remaining)
            self.assertNotIn(seeded[2][1], remaining)
            _assert_readable(store, paths)

    def test_fb_160e_fault_named_backup_survives(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 5)
            oldest_receipt = seeded[0][1]
            store.arm(
                scope="manifest",
                reason="manifest_corrupt",
                manifest_sha256=hashlib.sha256(b"corrupt").hexdigest(),
                backup_receipt_sha256=oldest_receipt,
            )
            with _limits(3, 32):
                new_receipt = store.checkpoint_manifest(_raw(100))
            remaining = _hex_dirs(paths)
            self.assertIn(oldest_receipt, remaining)
            self.assertIn(new_receipt, remaining)
            self.assertIn(seeded[4][1], remaining)
            self.assertIn(seeded[3][1], remaining)
            self.assertNotIn(seeded[1][1], remaining)
            _assert_readable(store, paths)

    def test_fb_160e_pointer_backup_survives_when_oldest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 5)
            oldest_raw, oldest_receipt = seeded[0]
            oldest_path = _backup_dir(paths, oldest_receipt)
            before = oldest_path.lstat().st_mtime_ns
            with _limits(3, 32):
                again = store.checkpoint_manifest(oldest_raw)
            self.assertEqual(again, oldest_receipt)
            self.assertGreater(oldest_path.lstat().st_mtime_ns, before)
            remaining = _hex_dirs(paths)
            self.assertIn(oldest_receipt, remaining)
            self.assertNotIn(seeded[1][1], remaining)
            self.assertNotIn(seeded[2][1], remaining)
            self.assertEqual(
                remaining,
                {oldest_receipt, seeded[3][1], seeded[4][1]},
            )
            loaded = store.load_manifest_checkpoint()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[0], oldest_raw)
            self.assertEqual(loaded[1], oldest_receipt)
            _assert_readable(store, paths)

    def test_fb_160e_malformed_fault_json_removes_nothing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 5)
            before = _hex_dirs(paths)
            fault_id = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
            fault_dir = paths.lifecycle_recovery_faults_dir / fault_id
            fault_dir.mkdir()
            (fault_dir / "fault.json").write_text("{not-json", encoding="utf-8")
            with _limits(3, 32):
                new_receipt = store.checkpoint_manifest(_raw(100))
            remaining = _hex_dirs(paths)
            self.assertTrue(before.issubset(remaining))
            self.assertIn(new_receipt, remaining)
            self.assertEqual(remaining, before | {new_receipt})
            _assert_readable(store, paths)

    def test_fb_160e_unknown_child_directory_removes_nothing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 5)
            before = _hex_dirs(paths)
            (paths.lifecycle_recovery_faults_dir / "stray-child").mkdir()
            with _limits(3, 32):
                new_receipt = store.checkpoint_manifest(_raw(100))
            remaining = _hex_dirs(paths)
            self.assertEqual(remaining, before | {new_receipt})
            self.assertTrue((paths.lifecycle_recovery_faults_dir / "stray-child").is_dir())
            _assert_readable(store, paths)

    def test_fb_160e_non_hex_and_stray_file_and_extra_file_never_removed(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 5)
            backups = paths.lifecycle_recovery_faults_dir / "backups"
            notes = backups / "notes"
            notes.mkdir()
            (notes / "readme.txt").write_text("keep", encoding="utf-8")
            stray_file = backups / "stray.bin"
            stray_file.write_bytes(b"file")
            hex_file = backups / ("c" * 64)
            hex_file.write_bytes(b"not-a-dir")
            extra_dir = _backup_dir(paths, seeded[0][1])
            extra_member = extra_dir / "extra.bin"
            extra_member.write_bytes(b"extra")
            with _limits(3, 32):
                new_receipt = store.checkpoint_manifest(_raw(100))
            remaining = _hex_dirs(paths)
            self.assertTrue(notes.is_dir())
            self.assertTrue(stray_file.is_file())
            self.assertTrue(hex_file.is_file())
            self.assertTrue(extra_member.is_file())
            self.assertIn(seeded[0][1], remaining)
            self.assertIn(new_receipt, remaining)
            self.assertNotIn(seeded[1][1], remaining)
            _assert_readable(store, paths)

    def test_fb_160e_batch_limit_and_repeated_checkpoints_converge(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 7)
            oldest_receipt = seeded[0][1]
            store.arm(
                scope="manifest",
                reason="manifest_corrupt",
                manifest_sha256=hashlib.sha256(b"corrupt").hexdigest(),
                backup_receipt_sha256=oldest_receipt,
            )
            new_raw = _raw(100)
            with _limits(2, 2):
                new_receipt = store.checkpoint_manifest(new_raw)
                after_one = _hex_dirs(paths)
                self.assertEqual(len(after_one), 6)
                self.assertLessEqual(8 - len(after_one), 2)
                previous = None
                current = after_one
                for _ in range(8):
                    store.checkpoint_manifest(new_raw)
                    current = _hex_dirs(paths)
                    if current == previous:
                        break
                    previous = current
            expected = {oldest_receipt, seeded[6][1], new_receipt}
            self.assertEqual(current, expected)
            _assert_readable(store, paths)

    def test_fb_160e_oserror_during_remove_does_not_escape_checkpoint(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            _seed(store, paths, 5)
            new_raw = _raw(100)
            with _limits(3, 32):
                with patch.object(
                    runtime_state.os, "unlink", side_effect=OSError("denied")
                ):
                    new_receipt = store.checkpoint_manifest(new_raw)
            loaded = store.load_manifest_checkpoint()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[0], new_raw)
            self.assertEqual(loaded[1], new_receipt)
            self.assertIn(new_receipt, _hex_dirs(paths))
            _assert_readable(store, paths)

    def test_fb_160e_scan_does_not_list_unselected_candidates(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 5)
            backups = paths.lifecycle_recovery_faults_dir / "backups"
            listed_inside: list[str] = []
            real_listdir = os.listdir
            real_scandir = os.scandir

            def tracing_listdir(path):
                path_obj = Path(path)
                try:
                    path_obj.relative_to(backups)
                except ValueError:
                    return real_listdir(path)
                if path_obj != backups:
                    listed_inside.append(path_obj.name)
                return real_listdir(path)

            def tracing_scandir(path):
                path_obj = Path(path)
                try:
                    path_obj.relative_to(backups)
                except ValueError:
                    return real_scandir(path)
                if path_obj != backups:
                    listed_inside.append(path_obj.name)
                return real_scandir(path)

            with _limits(3, 32), patch.object(
                runtime_state.os, "listdir", side_effect=tracing_listdir
            ), patch.object(
                runtime_state.os, "scandir", side_effect=tracing_scandir
            ):
                new_receipt = store.checkpoint_manifest(_raw(100))
            kept = {seeded[3][1], seeded[4][1], new_receipt}
            stale = {item[1] for item in seeded if item[1] not in kept}
            self.assertEqual(sorted(listed_inside), sorted(stale))
            remaining = _hex_dirs(paths)
            self.assertEqual(remaining, kept)
            _assert_readable(store, paths)

    def test_fb_160e_failed_removal_skips_and_continues(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 5)
            fail_dir = _backup_dir(paths, seeded[0][1]).resolve()
            real_unlink = os.unlink

            def selective_unlink(path):
                target = Path(path).resolve()
                if target.parent == fail_dir:
                    raise OSError("denied")
                return real_unlink(path)

            with _limits(3, 32), patch.object(
                runtime_state.os, "unlink", side_effect=selective_unlink
            ):
                new_receipt = store.checkpoint_manifest(_raw(100))
            remaining = _hex_dirs(paths)
            self.assertIn(seeded[0][1], remaining)
            self.assertNotIn(seeded[1][1], remaining)
            self.assertNotIn(seeded[2][1], remaining)
            self.assertIn(new_receipt, remaining)
            _assert_readable(store, paths)

    def test_fb_160e_manifest_backup_retention_status_count_blockers_and_readonly(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            absent = store.manifest_backup_retention_status()
            self.assertEqual(
                absent,
                {"count": 0, "retain": 200, "blocker": None},
            )

            seeded = _seed(store, paths, 3)
            store.checkpoint_manifest(_raw(100))
            healthy = store.manifest_backup_retention_status()
            self.assertEqual(healthy["blocker"], None)
            self.assertEqual(healthy["retain"], 200)
            self.assertEqual(healthy["count"], len(_hex_dirs(paths)))
            self.assertGreaterEqual(healthy["count"], 1)

            def fingerprint() -> dict[str, tuple[int, bytes | None]]:
                found: dict[str, tuple[int, bytes | None]] = {}
                for dirpath, dirnames, filenames in os.walk(paths.root):
                    for name in dirnames + filenames:
                        path = Path(dirpath) / name
                        try:
                            info = path.lstat()
                        except OSError:
                            continue
                        data = None
                        if stat.S_ISREG(info.st_mode):
                            data = path.read_bytes()
                        found[str(path)] = (info.st_mtime_ns, data)
                return found

            before = fingerprint()
            again = store.manifest_backup_retention_status()
            self.assertEqual(again, healthy)
            self.assertEqual(fingerprint(), before)

            pointer = paths.lifecycle_manifest_checkpoint_path
            pointer.write_text("{not-json", encoding="utf-8")
            unreadable = store.manifest_backup_retention_status()
            self.assertEqual(unreadable["blocker"], "pointer_unreadable")
            self.assertEqual(unreadable["count"], healthy["count"])
            pointer.unlink()

            stray = paths.lifecycle_recovery_faults_dir / "stray-child"
            stray.mkdir()
            unexpected = store.manifest_backup_retention_status()
            self.assertEqual(unexpected["blocker"], "unexpected_child")
            stray.rmdir()

            fault_id = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
            fault_dir = paths.lifecycle_recovery_faults_dir / fault_id
            fault_dir.mkdir()
            (fault_dir / "fault.json").write_text("{not-json", encoding="utf-8")
            fault_status = store.manifest_backup_retention_status()
            self.assertEqual(fault_status["blocker"], "fault_unreadable")
            self.assertEqual(fingerprint()[str(fault_dir / "fault.json")][1], b"{not-json")

    def test_fb_160e_reused_backup_refreshes_directory_mtime(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            raw = _raw(1)
            receipt = store.create_manifest_backup(raw)
            directory = _backup_dir(paths, receipt)
            os.utime(directory, (1_000_000_000, 1_000_000_000))
            before = directory.lstat().st_mtime_ns
            again = store.create_manifest_backup(raw)
            self.assertEqual(again, receipt)
            self.assertGreater(directory.lstat().st_mtime_ns, before)

    @slow_test
    def test_fb_160e_checkpoint_holds_lock_across_pointer_write(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            in_write = threading.Event()
            hold = threading.Event()
            real_write = runtime_state.atomic_write_json

            def blocking_write(path, payload):
                in_write.set()
                self.assertTrue(hold.wait(timeout=5))
                return real_write(path, payload)

            other = threading.Thread(
                target=store.create_manifest_backup, args=(_raw(200),)
            )
            with patch.object(
                runtime_state, "atomic_write_json", side_effect=blocking_write
            ):
                checkpoint = threading.Thread(
                    target=store.checkpoint_manifest, args=(_raw(100),)
                )
                checkpoint.start()
                self.assertTrue(in_write.wait(timeout=5))
                other.start()
                other.join(timeout=0.4)
                self.assertTrue(other.is_alive())
                hold.set()
                checkpoint.join(timeout=5)
                other.join(timeout=5)
            self.assertFalse(checkpoint.is_alive())
            self.assertFalse(other.is_alive())

    def test_fb_160e_mtime_refresh_failure_does_not_fail_checkpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            raw = _raw(1)
            receipt = store.create_manifest_backup(raw)
            with patch.object(
                runtime_state.os, "utime", side_effect=OSError("denied")
            ):
                again = store.checkpoint_manifest(raw)
            self.assertEqual(again, receipt)
            self.assertEqual(store.load_manifest_checkpoint(), (raw, receipt))

    @unittest.skipUnless(os.name == "nt", "directory junctions are a Windows feature")
    def test_fb_160e_directory_swapped_for_junction_is_not_followed(self) -> None:
        """A swap made while the directory is listed is caught by the identity
        re-check. A swap between that re-check and the unlink is not exercised:
        path-based removal cannot close that window."""
        import _winapi

        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            _stale_raw, stale_receipt = _seed(store, paths, 1)[0]
            protected_raw = _raw(50)
            protected_receipt = store.checkpoint_manifest(protected_raw)
            protected_dir = _backup_dir(paths, protected_receipt)
            stale_dir = _backup_dir(paths, stale_receipt)
            members = ("manifest.bin", "receipt.json")
            before = {name: (protected_dir / name).read_bytes() for name in members}
            real_listdir = os.listdir
            swapped: list[Path] = []

            def swapping_listdir(path):
                if Path(path) == stale_dir and not swapped:
                    moved = stale_dir.with_name("moved-aside")
                    os.rename(stale_dir, moved)
                    _winapi.CreateJunction(str(protected_dir), str(stale_dir))
                    swapped.append(moved)
                return real_listdir(path)

            try:
                with _limits(1, 8), patch.object(
                    runtime_state.os, "listdir", side_effect=swapping_listdir
                ):
                    store.checkpoint_manifest(protected_raw)
                self.assertTrue(swapped, "the stale directory was never selected")
                after = {name: (protected_dir / name).read_bytes() for name in members}
                self.assertEqual(after, before)
            finally:
                if swapped:
                    os.rmdir(stale_dir)

    def test_fb_160e_unreadable_backup_entry_blocks_prune_and_reports(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            store = LifecycleRecoveryFaultStore(paths)
            seeded = _seed(store, paths, 4)
            unreadable = seeded[1][1]
            real_scandir = os.scandir

            class _Entry:
                def __init__(self, entry) -> None:
                    self._entry = entry
                    self.name = entry.name
                    self.path = entry.path

                def is_dir(self, follow_symlinks=True):
                    if self.name == unreadable:
                        raise PermissionError("denied")
                    return self._entry.is_dir(follow_symlinks=follow_symlinks)

                def stat(self, follow_symlinks=True):
                    return self._entry.stat(follow_symlinks=follow_symlinks)

            @contextmanager
            def wrapped(path):
                with real_scandir(path) as entries:
                    yield [_Entry(entry) for entry in entries]

            before = _hex_dirs(paths)
            with _limits(1, 8), patch.object(
                runtime_state.os, "scandir", side_effect=wrapped
            ):
                status = store.manifest_backup_retention_status()
                new_receipt = store.checkpoint_manifest(_raw(100))
            self.assertEqual(status["blocker"], "backup_unreadable")
            self.assertEqual(_hex_dirs(paths), before | {new_receipt})

            with patch.object(runtime_state.os, "scandir", side_effect=OSError("denied")):
                failing = store.manifest_backup_retention_status()
            self.assertEqual(failing["blocker"], "backup_unreadable")
