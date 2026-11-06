from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import identity_migration, posix_fileops
from dayz_mcp.identity_migration import RunsBackupGateError, ensure_runs_v1_backup
from dayz_mcp.runtime_state import RuntimePaths

POSIX = os.name != "nt"


class SimulatedCrash(BaseException):
    pass


@unittest.skipUnless(POSIX, "POSIX counterparts of the Win32 primitives")
class ByteLockTest(unittest.TestCase):
    """Windows semantics: the open file owns the lock, not the process."""

    def test_two_descriptors_in_one_process_conflict_and_closing_a_third_keeps_it(self) -> None:
        from dayz_mcp import byte_lock

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "lock")
            first = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            second = os.open(path, os.O_RDWR)
            third = os.open(path, os.O_RDWR)
            self.addCleanup(os.close, first)
            self.addCleanup(os.close, second)
            byte_lock.try_lock(first)
            with self.assertRaises(OSError):
                byte_lock.try_lock(second)
            os.close(third)
            with self.assertRaises(OSError):
                byte_lock.try_lock(second)


@unittest.skipUnless(POSIX, "POSIX counterparts of the Win32 primitives")
class RenameNoReplaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_moves_to_a_free_name(self) -> None:
        (self.root / "a").write_bytes(b"a")
        posix_fileops.rename_noreplace(self.root / "a", self.root / "b")
        self.assertFalse((self.root / "a").exists())
        self.assertEqual((self.root / "b").read_bytes(), b"a")

    def test_refuses_an_existing_target_and_keeps_both(self) -> None:
        (self.root / "a").write_bytes(b"a")
        (self.root / "b").write_bytes(b"b")
        with self.assertRaises(FileExistsError):
            posix_fileops.rename_noreplace(self.root / "a", self.root / "b")
        self.assertEqual((self.root / "a").read_bytes(), b"a")
        self.assertEqual((self.root / "b").read_bytes(), b"b")

    def test_link_fallback_also_refuses_an_existing_target(self) -> None:
        (self.root / "a").write_bytes(b"a")
        (self.root / "b").write_bytes(b"b")
        with patch.object(posix_fileops, "_renameat2", None):
            with self.assertRaises(FileExistsError):
                posix_fileops.rename_noreplace(self.root / "a", self.root / "b")
            posix_fileops.rename_noreplace(self.root / "a", self.root / "c")
        self.assertEqual((self.root / "b").read_bytes(), b"b")
        self.assertEqual((self.root / "c").read_bytes(), b"a")
        self.assertFalse((self.root / "a").exists())

    def test_missing_source_is_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            posix_fileops.rename_noreplace(self.root / "nope", self.root / "b")


@unittest.skipUnless(POSIX, "POSIX counterparts of the Win32 primitives")
class UnlinkOwnedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "marker.json"
        self.owned = b"owned-marker"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def unlink(self, **kwargs: object) -> None:
        posix_fileops.unlink_owned(
            self.path, lambda actual: actual == self.owned, phase_name="marker", **kwargs
        )

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir())

    def test_deletes_the_owned_file(self) -> None:
        self.path.write_bytes(self.owned)
        self.unlink()
        self.assertEqual(self.leftovers(), [])

    def test_missing_file_is_not_an_error(self) -> None:
        self.unlink()

    def test_foreign_bytes_are_a_conflict_and_kept(self) -> None:
        self.path.write_bytes(b"foreign")
        with self.assertRaises(posix_fileops.OwnedUnlinkConflict):
            self.unlink()
        self.assertEqual(self.path.read_bytes(), b"foreign")

    def test_symlink_is_refused_and_its_target_kept(self) -> None:
        target = self.root / "target"
        target.write_bytes(self.owned)
        self.path.symlink_to(target)
        with self.assertRaises(posix_fileops.OwnedUnlinkConflict):
            self.unlink()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_bytes(), self.owned)

    def test_hard_linked_file_is_refused(self) -> None:
        self.path.write_bytes(self.owned)
        os.link(self.path, self.root / "second-name")
        with self.assertRaises(posix_fileops.OwnedUnlinkConflict):
            self.unlink()
        self.assertEqual(self.leftovers(), ["marker.json", "second-name"])

    def test_owned_file_moved_away_while_pinned_is_a_conflict_and_kept(self) -> None:
        self.path.write_bytes(self.owned)
        displaced = self.root / "displaced"

        def swap(phase: str) -> None:
            if phase == "marker_unlink_pinned":
                os.rename(self.path, displaced)

        with self.assertRaises(posix_fileops.OwnedUnlinkConflict):
            self.unlink(fault_injector=swap)
        self.assertEqual(self.leftovers(), ["displaced"])
        self.assertEqual(displaced.read_bytes(), self.owned)

    def test_foreign_file_swapped_in_while_pinned_is_put_back(self) -> None:
        self.path.write_bytes(self.owned)
        displaced = self.root / "displaced"

        def swap(phase: str) -> None:
            if phase == "marker_unlink_pinned":
                os.rename(self.path, displaced)
                self.path.write_bytes(self.owned)  # same bytes, different file

        with self.assertRaises(posix_fileops.OwnedUnlinkConflict):
            self.unlink(fault_injector=swap)
        self.assertEqual(self.leftovers(), ["displaced", "marker.json"])
        self.assertEqual(self.path.read_bytes(), self.owned)

    def test_rewrite_in_place_while_pinned_is_a_conflict(self) -> None:
        self.path.write_bytes(self.owned)

        def rewrite(phase: str) -> None:
            if phase == "marker_unlink_pinned":
                with open(self.path, "r+b") as handle:
                    handle.write(b"X")

        with self.assertRaises(posix_fileops.OwnedUnlinkConflict):
            self.unlink(fault_injector=rewrite)
        self.assertEqual(self.path.read_bytes(), b"Xwned-marker")


@unittest.skipUnless(POSIX, "the runs.json migration through the POSIX primitives")
class RunsBackupGatePosixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        runtime = root / "runtime"
        self.paths = RuntimePaths(
            runtime, runtime / "audit", runtime / "coordination.json", runtime / "runs.json"
        )
        runtime.mkdir()
        self.paths.runs_path.write_bytes(b"source")
        self.migration = root / "migration"
        self.marker = self.migration / "runs-backup-transaction.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_gate(self, **kwargs: object) -> dict[str, object]:
        return ensure_runs_v1_backup(
            self.paths,
            8765,
            migration_dir=self.migration,
            scan_fn=lambda _allowed: (),
            listener_fn=lambda _port: False,
            **kwargs,
        )

    def crash_after_receipt(self) -> None:
        def crash(phase: str) -> None:
            if phase == "after_receipt_publish":
                raise SimulatedCrash()

        with self.assertRaises(SimulatedCrash):
            self.run_gate(fault_injector=crash)
        self.assertTrue(self.marker.exists())

    def test_fresh_migration_leaves_only_backup_receipt_and_lock(self) -> None:
        receipt = self.run_gate()
        self.assertEqual(receipt["source"], receipt["backup"])
        self.assertEqual(
            sorted(p.name for p in self.migration.iterdir()),
            [".runs-v1.lock", "runs-backup-receipt.json", "runs.pre-v2.json"],
        )
        self.assertEqual(self.run_gate(), receipt)

    def test_recovery_removes_the_owned_marker(self) -> None:
        self.crash_after_receipt()
        receipt = self.run_gate()
        self.assertEqual(receipt["source"], receipt["backup"])
        self.assertFalse(self.marker.exists())

    def test_marker_swapped_while_pinned_is_a_conflict_and_nothing_foreign_is_deleted(
        self,
    ) -> None:
        self.crash_after_receipt()
        owned = self.marker.read_bytes()
        displaced = self.migration / "owned-marker.displaced"

        def swap(phase: str) -> None:
            if phase == "marker_unlink_pinned":
                os.rename(self.marker, displaced)
                self.marker.write_bytes(owned)

        with self.assertRaisesRegex(RunsBackupGateError, "runs_backup_recovery_conflict"):
            self.run_gate(fault_injector=swap)
        self.assertEqual(self.marker.read_bytes(), owned)
        self.assertEqual(displaced.read_bytes(), owned)

    def test_receipt_planted_before_publish_is_a_conflict_and_kept(self) -> None:
        receipt_path = self.migration / "runs-backup-receipt.json"

        def plant(phase: str) -> None:
            if phase == "after_marker_receipt_pending":
                receipt_path.write_bytes(b"foreign-receipt")

        with self.assertRaisesRegex(RunsBackupGateError, "runs_backup_receipt_publish_conflict"):
            self.run_gate(fault_injector=plant)
        self.assertEqual(receipt_path.read_bytes(), b"foreign-receipt")

    def test_symlinked_migration_dir_is_refused(self) -> None:
        real = self.migration.parent / "real-migration"
        real.mkdir()
        self.migration.symlink_to(real)
        with self.assertRaises(RunsBackupGateError):
            self.run_gate()
        self.assertEqual(list(real.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
