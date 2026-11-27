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


@unittest.skipUnless(POSIX, "POSIX host_config durability and rename-swap detection")
class HostConfigPosixDurabilityAndSwapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))
        self.claude = self.root / ".claude.json"
        self.codex = self.root / ".codex" / "config.toml"
        self.codex.parent.mkdir()
        self.journal = self.root / "state" / "host-config-transaction"
        self.original_claude = (
            '{\n  "mcpServers": {\n    "dayz-mcp": {\n'
            '      "type": "stdio",\n'
            '      "command": "/opt/venv/bin/python",\n'
            '      "args": ["-m", "dayz_mcp", "--client"]\n'
            "    }\n  }\n}\n"
        ).encode("utf-8")
        self.original_codex = (
            "[mcp_servers.dayz-mcp]\n"
            'command = "/opt/venv/bin/python"\n'
            'args = ["-m", "dayz_mcp", "--client"]\n'
        ).encode("utf-8")
        self.claude.write_bytes(self.original_claude)
        self.codex.write_bytes(self.original_codex)

    def test_apply_host_timeouts_fails_closed_when_claude_replaced_by_rename(
        self,
    ) -> None:
        from dayz_mcp import host_config

        for phase in ("after_prepare", "after_write_claude"):
            with self.subTest(phase=phase):
                self.claude.write_bytes(self.original_claude)
                self.codex.write_bytes(self.original_codex)
                replacement = b'{"external_writer": true}\n'

                def swap(current_phase: str) -> None:
                    if current_phase == phase:
                        staged = self.root / f"replacement-{phase}.json"
                        staged.write_bytes(replacement)
                        os.replace(staged, self.claude)

                with self.assertRaisesRegex(
                    host_config.HostConfigError,
                    "^registration_config_transaction_failed$",
                ):
                    host_config.apply_host_timeouts(
                        self.claude,
                        self.codex,
                        journal_root=self.journal,
                        fault_injector=swap,
                    )
                self.assertEqual(self.claude.read_bytes(), replacement)
                self.assertEqual(self.codex.read_bytes(), self.original_codex)
                self.assertFalse(self.journal.exists())

    def test_apply_host_timeouts_fails_closed_when_claude_unlinked_before_commit(
        self,
    ) -> None:
        from dayz_mcp import host_config

        def remove_claude(current_phase: str) -> None:
            if current_phase == "after_write_claude":
                self.claude.unlink()

        with self.assertRaisesRegex(
            host_config.HostConfigError,
            "^registration_config_transaction_failed$",
        ):
            host_config.apply_host_timeouts(
                self.claude,
                self.codex,
                journal_root=self.journal,
                fault_injector=remove_claude,
            )
        self.assertFalse(self.claude.exists())
        self.assertEqual(self.codex.read_bytes(), self.original_codex)
        self.assertFalse(self.journal.exists())

    def test_posix_file_verify_path_identity_raises_drift_on_rename(self) -> None:
        from dayz_mcp import host_config

        with host_config._PosixFile(self.claude) as handle:
            handle.verify_path_identity()
            staged = self.root / "swapped.json"
            staged.write_bytes(self.original_claude)
            os.replace(staged, self.claude)
            with self.assertRaisesRegex(
                host_config.HostConfigError,
                "^registration_config_drift$",
            ):
                handle.verify_path_identity()

    def test_mkdir_restricted_and_persist_manifest_fsync_directories(self) -> None:
        from dayz_mcp import host_config

        events: list[tuple[object, ...]] = []
        fd_paths: dict[int, tuple[Path, int]] = {}
        real_open = os.open
        real_fsync = os.fsync
        real_close = os.close
        real_replace = os.replace

        def spy_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            fd_paths[fd] = (Path(os.fsdecode(path)), int(flags))
            return fd

        def spy_fsync(fd):
            info = fd_paths.get(fd)
            if info is not None:
                path, flags = info
                events.append(("fsync", path, flags))
            return real_fsync(fd)

        def spy_close(fd):
            fd_paths.pop(fd, None)
            return real_close(fd)

        def spy_replace(src, dst):
            events.append(
                ("replace", Path(os.fsdecode(src)).name, Path(os.fsdecode(dst)).name)
            )
            return real_replace(src, dst)

        expected_dir_flag = getattr(os, "O_DIRECTORY", 0)
        with (
            patch.object(host_config.os, "open", side_effect=spy_open),
            patch.object(host_config.os, "fsync", side_effect=spy_fsync),
            patch.object(host_config.os, "close", side_effect=spy_close),
            patch.object(host_config.os, "replace", side_effect=spy_replace),
        ):
            host_config._mkdir_restricted(self.journal)
            mkdir_dir_fsyncs = [
                (path, flags) for kind, path, flags in events if kind == "fsync"
            ]
            self.assertIn(self.journal.parent, [path for path, _ in mkdir_dir_fsyncs])
            self.assertIn(self.journal, [path for path, _ in mkdir_dir_fsyncs])
            for _path, flags in mkdir_dir_fsyncs:
                self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR), 0)
                if expected_dir_flag:
                    self.assertTrue(bool(flags & expected_dir_flag))

            events.clear()
            host_config._persist_manifest(
                self.journal,
                {"schema": 1, "status": "prepared", "files": {}},
            )

        manifest_dir_events = [
            event
            for event in events
            if event[0] == "replace"
            or (event[0] == "fsync" and event[1] == self.journal)
        ]
        self.assertEqual(len(manifest_dir_events), 4)
        self.assertEqual(
            manifest_dir_events[0], ("replace", "manifest.staging", "manifest.next")
        )
        self.assertEqual(manifest_dir_events[1][:2], ("fsync", self.journal))
        self.assertEqual(
            manifest_dir_events[2], ("replace", "manifest.next", "manifest.json")
        )
        self.assertEqual(manifest_dir_events[3][:2], ("fsync", self.journal))
        for idx in (1, 3):
            flags = manifest_dir_events[idx][2]
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR), 0)
            if expected_dir_flag:
                self.assertTrue(bool(flags & expected_dir_flag))


@unittest.skipUnless(POSIX, "real POSIX filesystem validation for install_mcp")
class PosixInstallerValidationRealFsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))

    @staticmethod
    def _import_installer():
        import sys

        tools_dir = str(Path(__file__).resolve().parents[1])
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import install_mcp as installer

        return installer

    @staticmethod
    def _write_elf(path: Path, mode: int) -> Path:
        payload = bytearray(64)
        payload[0:4] = b"\x7fELF"
        payload[4] = 2
        payload[5] = 1
        payload[6] = 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(mode)
        return path

    def test_validate_python_executable_base_requires_elf_and_exec_bit(self) -> None:
        installer = self._import_installer()

        bin_dir = self.root / "usr" / "bin"
        good = self._write_elf(bin_dir / "python3.13", 0o755)
        good_link = bin_dir / "python3"
        good_link.symlink_to(good)

        noexec = self._write_elf(bin_dir / "python3.12", 0o644)
        noexec_link = bin_dir / "python"
        noexec_link.symlink_to(noexec)

        script = bin_dir / "python3.14"
        script.write_bytes(b"#!/bin/sh\necho hi\n")
        script.chmod(0o755)
        script_link = self.root / "alt" / "bin" / "python3"
        script_link.parent.mkdir(parents=True)
        script_link.symlink_to(script)

        self.assertEqual(
            installer._validate_python_executable(good, allow_symlink=True), good
        )
        self.assertEqual(
            installer._validate_python_executable(good_link, allow_symlink=True), good
        )
        for bad in (noexec, noexec_link):
            with self.subTest(noexec=str(bad)):
                with self.assertRaises(installer.InstallerContractError) as err:
                    installer._validate_python_executable(bad, allow_symlink=True)
                self.assertEqual(err.exception.code, "python_executable_not_executable")
        for bad in (script, script_link):
            with self.subTest(non_elf=str(bad)):
                with self.assertRaises(installer.InstallerContractError) as err:
                    installer._validate_python_executable(bad, allow_symlink=True)
                self.assertEqual(err.exception.code, "installer_cli_not_elf")

    def test_validate_cli_entry_requires_exec_bit_on_posix(self) -> None:
        installer = self._import_installer()

        good_claude = self._write_elf(self.root / "ok" / "claude", 0o755)
        noexec_claude = self._write_elf(self.root / "noexec" / "claude", 0o644)

        payload = installer.build_installer_cli_entry_payload("CLAUDE", good_claude)
        entry = installer._validate_cli_entry("CLAUDE", payload)
        self.assertEqual(entry.path, good_claude)

        with self.assertRaises(installer.InstallerContractError) as err:
            installer.build_installer_cli_entry_payload("CLAUDE", noexec_claude)
        self.assertEqual(err.exception.code, "installer_cli_not_executable")

        good_claude.chmod(0o644)
        with self.assertRaises(installer.InstallerContractError) as reval_err:
            entry.revalidate()
        self.assertEqual(reval_err.exception.code, "installer_cli_not_executable")


if __name__ == "__main__":
    unittest.main()
