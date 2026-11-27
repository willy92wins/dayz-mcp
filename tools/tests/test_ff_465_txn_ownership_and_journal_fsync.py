"""Fix-forward of #465 (1e105d10).

P1-1: the POSIX host-config transaction must not lose a concurrent edit made
on the same inode and still report ``committed``. flock is advisory, so the
transaction re-checks inode identity and that the bytes are still the ones it
expects before every write and again before ``committed``, and it restores
only files it touched whose bytes are still its own.

P1-2: the registration journal is published with a directory fsync after each
os.replace, and that barrier precedes the first provider.remove()/add().
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

POSIX = os.name != "nt"
FOREIGN = "foreign-server"


@unittest.skipUnless(POSIX, "the POSIX host-config transaction")
class ConcurrentSameInodeEditTest(unittest.TestCase):
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

    def _add_foreign_server(self, path: Path) -> bytes:
        # In place on the same inode, ignoring the advisory lock.
        document = json.loads(path.read_bytes())
        document["mcpServers"][FOREIGN] = {"type": "stdio", "command": "/bin/true"}
        edited = (json.dumps(document, indent=2) + "\n").encode("utf-8")
        path.write_bytes(edited)
        return edited

    def _apply(self, injector):
        from dayz_mcp import host_config

        return host_config.apply_host_timeouts(
            self.claude, self.codex, journal_root=self.journal, fault_injector=injector
        )

    def test_an_edit_after_prepare_is_kept_and_nothing_commits(self) -> None:
        from dayz_mcp import host_config

        inode = self.claude.stat().st_ino
        edited: dict[str, bytes] = {}

        def edit(phase: str) -> None:
            if phase == "after_prepare":
                edited["claude"] = self._add_foreign_server(self.claude)

        with self.assertRaises(host_config.HostConfigError):
            self._apply(edit)
        self.assertEqual(self.claude.stat().st_ino, inode)
        self.assertEqual(self.claude.read_bytes(), edited["claude"])
        self.assertIn(FOREIGN, json.loads(self.claude.read_bytes())["mcpServers"])
        self.assertEqual(self.codex.read_bytes(), self.original_codex)
        self.assertFalse(self.journal.exists())

    def test_an_edit_to_a_written_file_is_never_overwritten_by_rollback(self) -> None:
        from dayz_mcp import host_config

        edited: dict[str, bytes] = {}

        def edit(phase: str) -> None:
            if phase == "after_write_claude":
                edited["claude"] = self._add_foreign_server(self.claude)

        with self.assertRaises(host_config.HostConfigError):
            self._apply(edit)
        # Neither committed over it nor rolled back over it.
        self.assertEqual(self.claude.read_bytes(), edited["claude"])
        self.assertIn(FOREIGN, json.loads(self.claude.read_bytes())["mcpServers"])
        # Codex was never written: the check ran before its write.
        self.assertEqual(self.codex.read_bytes(), self.original_codex)

    def test_an_edit_to_the_other_file_restores_only_the_touched_one(self) -> None:
        from dayz_mcp import host_config

        edited: dict[str, bytes] = {}

        def edit(phase: str) -> None:
            if phase == "after_write_claude":
                text = self.codex.read_bytes() + b'\n[mcp_servers.other]\ncommand = "/bin/true"\n'
                self.codex.write_bytes(text)
                edited["codex"] = text

        with self.assertRaises(host_config.HostConfigError):
            self._apply(edit)
        self.assertEqual(self.codex.read_bytes(), edited["codex"])
        self.assertEqual(self.claude.read_bytes(), self.original_claude)
        self.assertFalse(self.journal.exists())

    def test_an_edit_after_the_last_write_blocks_committed(self) -> None:
        from dayz_mcp import host_config

        edited: dict[str, bytes] = {}

        def edit(phase: str) -> None:
            if phase == "after_write_codex":
                edited["claude"] = self._add_foreign_server(self.claude)

        with self.assertRaises(host_config.HostConfigError):
            self._apply(edit)
        self.assertEqual(self.claude.read_bytes(), edited["claude"])

    def test_a_rename_after_the_last_write_blocks_committed(self) -> None:
        from dayz_mcp import host_config

        replacement = b'{"external_writer": true}\n'

        def swap(phase: str) -> None:
            if phase == "after_write_codex":
                staged = self.root / "replacement.json"
                staged.write_bytes(replacement)
                os.replace(staged, self.claude)

        with self.assertRaises(host_config.HostConfigError):
            self._apply(swap)
        self.assertEqual(self.claude.read_bytes(), replacement)

    def test_without_a_concurrent_writer_it_still_commits(self) -> None:
        self.assertEqual(self._apply(None), {"status": "committed"})
        self.assertFalse(self.journal.exists())


@unittest.skipUnless(POSIX, "directory fsync is a POSIX barrier")
class RegistrationJournalDurabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))

    def test_the_directory_fsync_after_the_last_rename_precedes_remove_and_add(self) -> None:
        import install_mcp as installer
        from install_mcp import RegistrationSpec, register_transaction

        journal_root = self.root / "reg-journal"
        events: list[tuple[str, str]] = []
        fd_paths: dict[int, Path] = {}
        real_open, real_fsync, real_close, real_replace = os.open, os.fsync, os.close, os.replace

        def spy_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            fd_paths[fd] = Path(os.fsdecode(path))
            return fd

        def spy_fsync(fd):
            path = fd_paths.get(fd)
            if path is not None and path.is_dir():
                events.append(("dir_fsync", str(path)))
            return real_fsync(fd)

        def spy_close(fd):
            fd_paths.pop(fd, None)
            return real_close(fd)

        def spy_replace(src, dst):
            result = real_replace(src, dst)
            events.append(("replace", str(Path(os.fsdecode(dst)))))
            return result

        old = RegistrationSpec(Path("/opt/old/bin/python"), ("-m", "dayz_mcp", "--client"))
        new = RegistrationSpec(Path("/opt/new/bin/python"), ("-m", "dayz_mcp", "--client"))

        class _Provider:
            def __init__(self) -> None:
                self.current: RegistrationSpec | None = old

            def get(self, role: str) -> RegistrationSpec | None:
                return self.current

            def remove(self, role: str) -> None:
                events.append(("remove", role))
                self.current = None

            def add(self, role: str, value: RegistrationSpec) -> None:
                events.append(("add", role))
                self.current = value

        with (
            patch.object(os, "open", side_effect=spy_open),
            patch.object(os, "fsync", side_effect=spy_fsync),
            patch.object(os, "close", side_effect=spy_close),
            patch.object(os, "replace", side_effect=spy_replace),
        ):
            register_transaction(_Provider(), {"CLAUDE": new}, journal_root=journal_root)

        first_mutation = next(
            index for index, event in enumerate(events) if event[0] in {"remove", "add"}
        )
        self.assertEqual(events[first_mutation], ("remove", "CLAUDE"))
        before = events[:first_mutation]
        published = [
            index
            for index, event in enumerate(before)
            if event[0] == "replace" and event[1].endswith("manifest.json")
        ]
        self.assertTrue(published, events)
        last_rename = published[-1]
        journal_dir = str(Path(before[last_rename][1]).parent)
        self.assertIn(
            ("dir_fsync", journal_dir),
            before[last_rename + 1 :],
            "no directory fsync between the journal publication and provider.remove()",
        )
        # Every rename of the journal is followed by a directory fsync.
        for index, event in enumerate(before):
            if event[0] == "replace" and Path(event[1]).parent == Path(journal_dir):
                self.assertIn(("dir_fsync", journal_dir), before[index + 1 :][:1], events)
        self.assertIsNotNone(installer)


@unittest.skipUnless(POSIX, "the POSIX host-config transaction")
class RollbackRound2Test(ConcurrentSameInodeEditTest):
    """Round 2 of the #465 fix-forward (Sol P1-1, P1-2, P1-3 on 0e2fd35d)."""

    # The inherited tests already ran in the parent class.
    test_an_edit_after_prepare_is_kept_and_nothing_commits = None
    test_an_edit_to_a_written_file_is_never_overwritten_by_rollback = None
    test_an_edit_to_the_other_file_restores_only_the_touched_one = None
    test_an_edit_after_the_last_write_blocks_committed = None
    test_a_rename_after_the_last_write_blocks_committed = None
    test_without_a_concurrent_writer_it_still_commits = None

    def test_p1_1_a_short_write_then_eio_is_restored_not_left_torn(self) -> None:
        from dayz_mcp import host_config

        real_write = os.write
        state = {"claude_fd": None, "calls": 0}
        real_init = host_config._PosixFile.__init__

        def tracking_init(handle, path):
            real_init(handle, path)
            if Path(path).resolve() == self.claude:
                state["claude_fd"] = handle.fd

        torn: dict[str, list[bool]] = {"seen": []}

        def short_then_eio(fd, data):
            if fd == state["claude_fd"] and state["calls"] == 1:
                torn["seen"].append(self.claude.read_bytes() in (self.original_claude,))
            if fd == state["claude_fd"] and state["calls"] < 2:
                state["calls"] += 1
                if state["calls"] == 1:
                    # Past the prefix target and original share, so the
                    # inode really holds torn bytes.
                    payload = bytes(data)
                    shared = 0
                    while payload[shared] == self.original_claude[shared]:
                        shared += 1
                    return real_write(fd, payload[: shared + 8])
                raise OSError(5, "injected EIO")
            return real_write(fd, data)

        with patch.object(host_config._PosixFile, "__init__", tracking_init), \
                patch.object(host_config.os, "write", side_effect=short_then_eio):
            with self.assertRaises(host_config.HostConfigError):
                self._apply(None)
        self.assertEqual(state["calls"], 2)
        self.assertEqual(torn["seen"][:1], [False])
        self.assertEqual(self.claude.read_bytes(), self.original_claude)
        self.assertEqual(self.codex.read_bytes(), self.original_codex)
        self.assertFalse(self.journal.exists())

    def test_p1_1_a_foreign_edit_on_a_written_file_keeps_the_journal_unrestored(self) -> None:
        from dayz_mcp import host_config

        edited: dict[str, bytes] = {}

        def edit(phase: str) -> None:
            if phase == "after_write_claude":
                edited["claude"] = self._add_foreign_server(self.claude)

        with self.assertRaisesRegex(
            host_config.HostConfigError, "^registration_config_rollback_conflict$"
        ):
            self._apply(edit)
        self.assertEqual(self.claude.read_bytes(), edited["claude"])
        manifest = json.loads((self.journal / "manifest.json").read_bytes())
        self.assertEqual(manifest["status"], "restore_conflict")
        self.assertEqual(manifest["conflict_roles"], ["claude"])
        # The next run refuses instead of overwriting the kept edit.
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^registration_recovery_conflict$"
        ):
            self._apply(None)
        self.assertEqual(self.claude.read_bytes(), edited["claude"])
        self.assertTrue(self.journal.exists())

    def test_p1_2_a_resumed_restore_never_overwrites_a_foreign_codex_edit(self) -> None:
        from dayz_mcp import host_config

        edited_codex = self.original_codex + b'\n[mcp_servers.other]\ncommand = "/bin/true"\n'

        def edit(phase: str) -> None:
            if phase == "after_write_claude":
                self.codex.write_bytes(edited_codex)

        real_persist = host_config._persist_manifest

        def persist_then_crash(journal, manifest):
            real_persist(journal, manifest)
            if manifest.get("status") == "restoring_original":
                raise host_config.HostConfigCrash("cut after restoring_original")

        with patch.object(host_config, "_persist_manifest", persist_then_crash):
            with self.assertRaises(host_config.HostConfigCrash):
                self._apply(edit)
        self.assertEqual(self.codex.read_bytes(), edited_codex)
        # The retry resumes the restore: Claude goes back, Codex keeps the
        # other writer's server and then gets its timeouts on top of it.
        self._apply(None)
        after_codex = self.codex.read_bytes()
        self.assertIn(b"[mcp_servers.other]", after_codex)
        self.assertNotEqual(after_codex, self.original_codex)
        self.assertFalse(self.journal.exists())


@unittest.skipUnless(POSIX, "the POSIX host-config transaction")
class RollbackRound3Test(RollbackRound2Test):
    """Round 3 of the #465 fix-forward (Sol P1-1 and P1-2 on 33ff52c4)."""

    test_p1_1_a_short_write_then_eio_is_restored_not_left_torn = None
    test_p1_1_a_foreign_edit_on_a_written_file_keeps_the_journal_unrestored = None
    test_p1_2_a_resumed_restore_never_overwrites_a_foreign_codex_edit = None

    def _conflict(self) -> bytes:
        from dayz_mcp import host_config

        edited: dict[str, bytes] = {}

        def edit(phase: str) -> None:
            if phase == "after_write_claude":
                edited["claude"] = self._add_foreign_server(self.claude)

        with self.assertRaisesRegex(
            host_config.HostConfigError, "^registration_config_rollback_conflict$"
        ):
            self._apply(edit)
        return edited["claude"]

    def test_p1_2_an_original_put_back_by_atomic_replace_releases_the_journal(self) -> None:
        self._conflict()
        old_inode = self.claude.stat().st_ino
        staged = self.root / "editor-save.tmp"
        staged.write_bytes(self.original_claude)
        os.replace(staged, self.claude)
        self.assertNotEqual(self.claude.stat().st_ino, old_inode)
        self.assertEqual(self._apply(None), {"status": "committed"})
        self.assertFalse(self.journal.exists())

    def test_p1_2_a_foreign_file_on_a_new_inode_still_refuses(self) -> None:
        from dayz_mcp import host_config

        kept = self._conflict()
        staged = self.root / "editor-save.tmp"
        staged.write_bytes(kept)
        os.replace(staged, self.claude)
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^registration_recovery_conflict$"
        ):
            self._apply(None)
        self.assertEqual(self.claude.read_bytes(), kept)
        self.assertTrue(self.journal.exists())

    def test_p1_1_a_torn_shaped_foreign_edit_after_a_finished_write_is_kept(self) -> None:
        from dayz_mcp import host_config

        target = host_config.build_claude_target(self.original_claude, "dayz-mcp")
        shared = 0
        while target[shared] == self.original_claude[shared]:
            shared += 1
        # Same shape a short write of our target would leave, but written by
        # someone else after our write() to Claude completed.
        foreign = target[: shared + 4] + self.original_claude[shared + 4 :]
        self.assertNotIn(foreign, (target, self.original_claude))
        self.assertTrue(host_config._is_own_write(foreign, self.original_claude, target))

        def edit(phase: str) -> None:
            if phase == "after_write_claude":
                self.claude.write_bytes(foreign)

        with self.assertRaisesRegex(
            host_config.HostConfigError, "^registration_config_rollback_conflict$"
        ):
            self._apply(edit)
        self.assertEqual(self.claude.read_bytes(), foreign)
        self.assertTrue(self.journal.exists())

    def test_p3_a_terminal_journal_refuses_a_path_swapped_for_a_symlink(self) -> None:
        from dayz_mcp import host_config

        kept = self._conflict()
        moved = self.root / "original-moved.json"
        other = self.root / "other.json"
        other.write_bytes(self.original_claude)
        self.claude.rename(moved)
        self.claude.symlink_to(other)
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^registration_recovery_conflict$"
        ):
            self._apply(None)
        self.assertEqual(other.read_bytes(), self.original_claude)
        self.assertEqual(moved.read_bytes(), kept)
        self.assertTrue(self.journal.exists())


@unittest.skipUnless(POSIX, "the POSIX host-config transaction")
class InstallerOuterRollbackRound2Test(unittest.TestCase):
    """Sol P1-3: register_transaction's rollback keeps an edit timeouts kept."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))
        self.claude = self.root / ".claude.json"
        self.claude.write_bytes(
            (
                json.dumps(
                    {
                        "mcpServers": {
                            "dayz-mcp": {
                                "type": "stdio",
                                "command": "/opt/venv/bin/python",
                                "args": ["-m", "dayz_mcp", "--client"],
                            }
                        }
                    },
                    indent=2,
                )
                + "\n"
            ).encode()
        )

    def _provider(self):
        from install_mcp import RegistrationSpec

        path = self.claude

        class SameFileProvider:
            def get(self, role):
                entry = json.loads(path.read_bytes()).get("mcpServers", {}).get("dayz-mcp")
                if not isinstance(entry, dict):
                    return None
                return RegistrationSpec(Path(entry["command"]), tuple(entry["args"]))

            def _store(self, document):
                path.write_bytes((json.dumps(document, indent=2) + "\n").encode())

            def remove(self, role):
                document = json.loads(path.read_bytes())
                document["mcpServers"].pop("dayz-mcp", None)
                self._store(document)

            def add(self, role, spec):
                document = json.loads(path.read_bytes())
                document.setdefault("mcpServers", {})["dayz-mcp"] = {
                    "type": "stdio",
                    "command": str(spec.command),
                    "args": list(spec.arguments),
                }
                self._store(document)

        return SameFileProvider()

    def _command(self) -> str:
        return json.loads(self.claude.read_bytes())["mcpServers"]["dayz-mcp"]["command"]

    def _edit_command(self, value: str) -> None:
        document = json.loads(self.claude.read_bytes())
        document["mcpServers"]["dayz-mcp"]["command"] = value
        self.claude.write_bytes((json.dumps(document, indent=2) + "\n").encode())

    def test_p1_3_an_edit_kept_by_apply_host_timeouts_survives_the_outer_rollback(self) -> None:
        import install_mcp
        from install_mcp import RegistrationSpec

        desired = {"CLAUDE": RegistrationSpec(Path("/opt/new/bin/python"), ("-m", "dayz_mcp", "--client"))}

        def edit(phase: str) -> None:
            if phase == "after_prepare":
                self._edit_command("/bin/edited-command")

        with self.assertRaises(install_mcp.InstallerExecutionError):
            install_mcp.register_transaction(
                self._provider(),
                desired,
                host_configs=(self.claude,),
                journal_root=self.root / "reg-journal",
                host_journal_root=self.root / "host-journal",
                fault_injector=edit,
            )
        self.assertEqual(self._command(), "/bin/edited-command")
        # Round 3 (Sol P1-6): the journal keeps the preserved decision.
        self.assertTrue((self.root / "reg-journal" / "dayz-mcp").exists())

    def test_p1_3_recovery_after_a_crash_keeps_a_foreign_entry(self) -> None:
        import install_mcp
        from install_mcp import RegistrationCrash, RegistrationSpec

        desired = {"CLAUDE": RegistrationSpec(Path("/opt/new/bin/python"), ("-m", "dayz_mcp", "--client"))}

        def crash(phase: str) -> None:
            if phase == "after_add_CLAUDE":
                raise RegistrationCrash("cut after add")

        with self.assertRaises(RegistrationCrash):
            install_mcp.register_transaction(
                self._provider(), desired, journal_root=self.root / "reg-journal",
                fault_injector=crash,
            )
        self._edit_command("/bin/foreign-command")
        with self.assertRaises(install_mcp.InstallerExecutionError):
            install_mcp.register_transaction(
                self._provider(), desired, journal_root=self.root / "reg-journal",
            )
        self.assertEqual(self._command(), "/bin/foreign-command")
        self.assertTrue((self.root / "reg-journal" / "dayz-mcp").exists())

    def _register(self, **kwargs):
        import install_mcp
        from install_mcp import RegistrationSpec

        desired = {"CLAUDE": RegistrationSpec(Path("/opt/new/bin/python"), ("-m", "dayz_mcp", "--client"))}
        return install_mcp.register_transaction(
            self._provider(),
            desired,
            host_configs=(self.claude,),
            journal_root=self.root / "reg-journal",
            host_journal_root=self.root / "host-journal",
            **kwargs,
        )

    def test_p1_6_a_cut_after_preserved_never_lets_the_next_run_overwrite(self) -> None:
        import install_mcp

        def edit(phase: str) -> None:
            if phase == "after_prepare":
                self._edit_command("/bin/edited-command")

        real_write = install_mcp._write_registration_manifest

        def write_then_cut(journal, manifest):
            real_write(journal, manifest)
            if manifest.get("preserved"):
                raise install_mcp.RegistrationCrash("cut after preserved")

        with patch.object(install_mcp, "_write_registration_manifest", write_then_cut):
            with self.assertRaises(install_mcp.RegistrationCrash):
                self._register(fault_injector=edit)
        self.assertEqual(self._command(), "/bin/edited-command")
        for _attempt in range(2):
            with self.assertRaisesRegex(
                install_mcp.InstallerExecutionError, "^registration_preserved_foreign_edit$"
            ):
                self._register()
            self.assertEqual(self._command(), "/bin/edited-command")
            self.assertTrue((self.root / "reg-journal" / "dayz-mcp").exists())

    def test_p1_6_an_explicit_removal_of_the_preserved_entry_releases_it(self) -> None:
        import install_mcp

        def edit(phase: str) -> None:
            if phase == "after_prepare":
                self._edit_command("/bin/edited-command")

        with self.assertRaises(install_mcp.InstallerExecutionError):
            self._register(fault_injector=edit)
        with self.assertRaisesRegex(
            install_mcp.InstallerExecutionError, "^registration_preserved_foreign_edit$"
        ):
            self._register()
        document = json.loads(self.claude.read_bytes())
        document["mcpServers"].pop("dayz-mcp")
        self.claude.write_bytes((json.dumps(document, indent=2) + "\n").encode())
        self._register()
        self.assertEqual(self._command(), "/opt/new/bin/python")
        self.assertFalse((self.root / "reg-journal" / "dayz-mcp").exists())

    # Round 4 (Sol r3 P1-2): an edit in after_write_claude leaves two journals,
    # the timeouts one in restore_conflict and the registration one with
    # preserved=["CLAUDE"]. The documented explicit actions must release both.

    def _both_journals(self) -> dict[str, bytes]:
        import install_mcp

        captured: dict[str, bytes] = {"before": self.claude.read_bytes()}

        def edit(phase: str) -> None:
            if phase == "after_prepare":
                captured["intermediate"] = self.claude.read_bytes()
            if phase == "after_write_claude":
                self._edit_command("/bin/edited-command")

        with self.assertRaisesRegex(
            install_mcp.InstallerExecutionError,
            "^registration_rollback_preserved_foreign_edit$",
        ):
            self._register(fault_injector=edit)
        self.assertEqual(self._command(), "/bin/edited-command")
        self.assertTrue((self.root / "host-journal").exists())
        self.assertTrue((self.root / "reg-journal" / "dayz-mcp").exists())
        return captured

    def _assert_registered_and_released(self) -> None:
        entry = json.loads(self.claude.read_bytes())["mcpServers"]["dayz-mcp"]
        self.assertEqual(entry["command"], "/opt/new/bin/python")
        self.assertIn("timeout", entry)
        self.assertFalse((self.root / "host-journal").exists())
        self.assertFalse((self.root / "reg-journal" / "dayz-mcp").exists())

    def test_p1_r4_both_journals_refuse_while_the_foreign_entry_is_live(self) -> None:
        import install_mcp

        self._both_journals()
        for _attempt in range(2):
            with self.assertRaisesRegex(
                install_mcp.InstallerExecutionError, "^registration_preserved_foreign_edit$"
            ) as raised:
                self._register()
            self.assertIn("remove", raised.exception.remedy)
            self.assertEqual(self._command(), "/bin/edited-command")
            self.assertTrue((self.root / "host-journal").exists())
            self.assertTrue((self.root / "reg-journal" / "dayz-mcp").exists())

    def test_p1_r4_removing_the_entry_after_after_write_claude_releases_both(self) -> None:
        self._both_journals()
        self._provider().remove("CLAUDE")
        self._register()
        self._assert_registered_and_released()

    def test_p1_r4_putting_back_the_pre_install_file_releases_both(self) -> None:
        captured = self._both_journals()
        self.claude.write_bytes(captured["before"])
        self._register()
        self._assert_registered_and_released()

    def test_p1_r4_putting_back_the_intermediate_state_still_releases(self) -> None:
        captured = self._both_journals()
        self.claude.write_bytes(captured["intermediate"])
        self._register()
        self._assert_registered_and_released()

    def test_p1_r4_other_edits_in_the_file_survive_the_release(self) -> None:
        import install_mcp

        self._both_journals()
        document = json.loads(self.claude.read_bytes())
        document["mcpServers"].pop("dayz-mcp")
        document["mcpServers"]["other"] = {"command": "/bin/other", "args": []}
        self.claude.write_bytes((json.dumps(document, indent=2) + "\n").encode())
        # The entry is gone, so the preserved registration is resolved, and
        # the timeouts journal releases on it: the other server is kept.
        self._register()
        self._assert_registered_and_released()
        self.assertEqual(
            json.loads(self.claude.read_bytes())["mcpServers"]["other"]["command"], "/bin/other"
        )

    def test_recovery_after_a_crash_still_restores_our_own_entry(self) -> None:
        import install_mcp
        from install_mcp import RegistrationCrash, RegistrationSpec

        desired = {"CLAUDE": RegistrationSpec(Path("/opt/new/bin/python"), ("-m", "dayz_mcp", "--client"))}

        def crash(phase: str) -> None:
            if phase == "after_add_CLAUDE":
                raise RegistrationCrash("cut after add")

        with self.assertRaises(RegistrationCrash):
            install_mcp.register_transaction(
                self._provider(), desired, journal_root=self.root / "reg-journal",
                fault_injector=crash,
            )
        install_mcp.register_transaction(
            self._provider(), desired, journal_root=self.root / "reg-journal",
        )
        self.assertEqual(self._command(), "/opt/new/bin/python")
        self.assertFalse((self.root / "reg-journal" / "dayz-mcp").exists())


@unittest.skipUnless(POSIX, "the POSIX host-config transaction")
class TwoJournalsRound5Test(unittest.TestCase):
    """Round 5 (Sol r4 P1-1/P1-2 on 502ce52e): two clients, two server names."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))
        self.claude = self.root / "claude.json"
        self.codex = self.root / "codex.toml"
        self.claude.write_text('{"mcpServers": {}}')
        self.codex.write_text("")

    def _spec(self, name: str):
        from install_mcp import RegistrationSpec

        return RegistrationSpec(Path("/opt") / name, ("--client",))

    def _provider(self, name: str = "dayz-mcp"):
        import tomllib

        from install_mcp import RegistrationSpec

        claude, codex = self.claude, self.codex

        class TwoFileProvider:
            def get(self, role):
                if role == "CLAUDE":
                    entry = json.loads(claude.read_text())["mcpServers"].get(name)
                else:
                    entry = tomllib.loads(codex.read_text()).get("mcp_servers", {}).get(name)
                if not entry:
                    return None
                return RegistrationSpec(Path(entry["command"]), tuple(entry["args"]))

            def add(self, role, value):
                if role == "CLAUDE":
                    entries = json.loads(claude.read_text())["mcpServers"]
                    entries.pop(name, None)
                    if value is not None:
                        entries[name] = {"command": str(value.command), "args": list(value.arguments)}
                    claude.write_text(json.dumps({"mcpServers": entries}))
                    return
                text = ""
                if value is not None:
                    text = (
                        f"[mcp_servers.{name}]\n"
                        f"command = {json.dumps(str(value.command))}\n"
                        f"args = {json.dumps(list(value.arguments))}\n"
                    )
                codex.write_text(text)

            def remove(self, role):
                self.add(role, None)

        return TwoFileProvider()

    def _install(self, provider, *, dual: bool = True, edit_at: str | None = None, name="dayz-mcp"):
        import install_mcp

        desired = {"CLAUDE": self._spec("new")}
        paths: tuple[Path, ...] = (self.claude,)
        if dual:
            desired["CODEX"] = self._spec("new")
            paths += (self.codex,)

        def edit(phase: str) -> None:
            if phase == edit_at:
                provider.add("CLAUDE", self._spec("foreign"))

        return install_mcp.register_transaction(
            provider,
            desired,
            host_configs=paths,
            server_name=name,
            journal_root=self.root / "reg",
            host_journal_root=self.root / "host",
            fault_injector=edit,
        )

    def _fails(self, code: str, *args, **kwargs):
        import install_mcp

        with self.assertRaisesRegex(install_mcp.InstallerExecutionError, f"^{code}$") as raised:
            self._install(*args, **kwargs)
        return raised.exception

    def _edit_after_write_codex(self):
        client = self._provider()
        client.add("CODEX", self._spec("old"))
        self._fails(
            "registration_rollback_preserved_foreign_edit",
            client,
            edit_at="after_write_codex",
        )
        self.assertEqual(client.get("CLAUDE"), self._spec("foreign"))
        self.assertEqual(client.get("CODEX"), self._spec("old"))
        self.assertTrue((self.root / "host").exists())
        self.assertTrue((self.root / "reg" / "dayz-mcp").exists())
        return client

    def test_p1_a_removing_claude_after_after_write_codex_releases_both_journals(self) -> None:
        client = self._edit_after_write_codex()
        client.remove("CLAUDE")
        self._install(client)
        self.assertEqual(client.get("CLAUDE"), self._spec("new"))
        self.assertEqual(client.get("CODEX"), self._spec("new"))
        self.assertIn("tool_timeout_sec", self.codex.read_text())
        self.assertFalse((self.root / "host").exists())
        self.assertFalse((self.root / "reg" / "dayz-mcp").exists())

    def test_p1_a_the_live_foreign_claude_entry_still_refuses(self) -> None:
        client = self._edit_after_write_codex()
        self._fails("registration_preserved_foreign_edit", client)
        self.assertEqual(client.get("CLAUDE"), self._spec("foreign"))
        self.assertTrue((self.root / "host").exists())
        self.assertTrue((self.root / "reg" / "dayz-mcp").exists())

    def test_p1_a_a_foreign_codex_entry_after_the_rollback_is_never_released(self) -> None:
        client = self._edit_after_write_codex()
        client.remove("CLAUDE")
        client.add("CODEX", self._spec("other-writer"))
        self._fails("registration_recovery_conflict", client)
        self.assertEqual(client.get("CODEX"), self._spec("other-writer"))
        self.assertTrue((self.root / "host").exists())
        self.assertTrue((self.root / "reg" / "dayz-mcp").exists())

    def test_p1_b_resolving_one_server_name_never_releases_another_names_journal(self) -> None:
        a, b = self._provider("dayz-mcp"), self._provider("dayz-mcp-b")
        self._fails(
            "registration_rollback_preserved_foreign_edit", a, dual=False, edit_at="after_prepare"
        )
        self.assertFalse((self.root / "host").exists())
        self._fails(
            "registration_rollback_preserved_foreign_edit",
            b, dual=False, edit_at="after_write_claude", name="dayz-mcp-b",
        )
        self.assertTrue((self.root / "host").exists())
        a.remove("CLAUDE")
        error = self._fails("registration_recovery_conflict", a, dual=False)
        self.assertIn("another", error.remedy)
        self.assertTrue((self.root / "host").exists())
        self.assertTrue((self.root / "reg" / "dayz-mcp-b").exists())
        self.assertEqual(b.get("CLAUDE"), self._spec("foreign"))
        # Resolving B (its own explicit action) releases B, and then A.
        b.remove("CLAUDE")
        self._install(b, dual=False, name="dayz-mcp-b")
        self.assertEqual(b.get("CLAUDE"), self._spec("new"))
        self._install(a, dual=False)
        self.assertEqual(a.get("CLAUDE"), self._spec("new"))
        self.assertFalse((self.root / "host").exists())
        self.assertFalse((self.root / "reg" / "dayz-mcp").exists())
        self.assertFalse((self.root / "reg" / "dayz-mcp-b").exists())

    def test_p1_b_a_timeouts_journal_without_an_attempt_id_is_refused(self) -> None:
        client = self._provider()
        self._fails(
            "registration_rollback_preserved_foreign_edit",
            client, dual=False, edit_at="after_write_claude",
        )
        manifest_path = self.root / "host" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("attempt", None)
        manifest_path.write_text(json.dumps(manifest))
        client.remove("CLAUDE")
        self._fails("registration_recovery_conflict", client, dual=False)
        self.assertTrue((self.root / "host").exists())
        self.assertTrue((self.root / "reg" / "dayz-mcp").exists())


@unittest.skipUnless(POSIX, "the POSIX host-config transaction")
class TwoJournalsFollowUpTest(TwoJournalsRound5Test):
    """Follow-up to #475 (Sol r5, counted as P2): a cut inside the outer rollback."""

    def _cut_before_codex_previous_is_back(self):
        import install_mcp

        client = self._provider()
        old = self._spec("old")
        client.add("CODEX", old)
        real_add = client.add

        def cut(role, value):
            if role == "CODEX" and value == old:
                raise install_mcp.RegistrationCrash("cut before restoring previous Codex")
            real_add(role, value)

        client.add = cut
        with self.assertRaises(install_mcp.RegistrationCrash):
            self._install(client, edit_at="after_write_codex")
        client.add = real_add
        self.assertIsNone(client.get("CODEX"))
        self.assertEqual(client.get("CLAUDE"), self._spec("foreign"))
        return client

    def test_a1_an_empty_codex_left_by_a_cut_rollback_is_accepted(self) -> None:
        client = self._cut_before_codex_previous_is_back()
        client.remove("CLAUDE")
        self._install(client)
        self.assertEqual(client.get("CLAUDE"), self._spec("new"))
        self.assertEqual(client.get("CODEX"), self._spec("new"))
        self.assertFalse((self.root / "host").exists())
        self.assertFalse((self.root / "reg" / "dayz-mcp").exists())

    def test_a1_an_empty_codex_without_the_recorded_removal_still_refuses(self) -> None:
        import install_mcp

        client = self._cut_before_codex_previous_is_back()
        manifest_path = self.root / "reg" / "dayz-mcp" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["rollback_removed"], ["CODEX"])
        manifest.pop("rollback_removed")
        manifest_path.write_text(json.dumps(manifest))
        client.remove("CLAUDE")
        error = self._fails("registration_recovery_conflict", client)
        self.assertEqual(error.remedy, install_mcp._RELEASE_REFUSED_REMEDY)
        self.assertTrue((self.root / "host").exists())
        self.assertTrue((self.root / "reg" / "dayz-mcp").exists())

    def test_a2_an_old_journal_remedy_never_promises_that_remove_releases(self) -> None:
        import install_mcp

        client = self._provider()
        self._fails(
            "registration_rollback_preserved_foreign_edit",
            client, dual=False, edit_at="after_write_claude",
        )
        manifest_path = self.root / "host" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("attempt")
        manifest_path.write_text(json.dumps(manifest))
        client.remove("CLAUDE")
        error = self._fails("registration_recovery_conflict", client, dual=False)
        self.assertEqual(error.remedy, install_mcp._OTHER_JOURNAL_REMEDY)
        self.assertIn("does not release", error.remedy)
        self.assertIn(
            "put back the previous registration in the other client",
            install_mcp._RELEASE_REFUSED_REMEDY,
        )

    def test_a3_a_probe_failure_during_the_retry_carries_the_remedy(self) -> None:
        import install_mcp

        for code, kind in (
            ("unsupported_codex_registration", install_mcp.InstallerContractError),
            ("registration_probe_failed", install_mcp.InstallerExecutionError),
        ):
            with self.subTest(code=code):
                self.tearDown_journals()
                client = self._edit_after_write_codex()
                client.remove("CLAUDE")
                real_get = client.get

                def get(role, real_get=real_get, code=code, kind=kind):
                    if role == "CODEX":
                        raise kind(code)
                    return real_get(role)

                client.get = get
                with self.assertRaises((install_mcp.InstallerContractError,
                                        install_mcp.InstallerExecutionError)) as raised:
                    self._install(client)
                self.assertEqual(getattr(raised.exception, "code", str(raised.exception)), code)
                self.assertEqual(raised.exception.remedy, install_mcp._RETRY_PROBE_REMEDY)

    def tearDown_journals(self) -> None:
        import shutil

        for name in ("host", "reg"):
            shutil.rmtree(self.root / name, ignore_errors=True)
        self.claude.write_text('{"mcpServers": {}}')
        self.codex.write_text("")


# Reuse the fixtures only; the round 5 tests run once, in their own class.
for _name in [n for n in vars(TwoJournalsRound5Test) if n.startswith("test_")]:
    setattr(TwoJournalsFollowUpTest, _name, None)
del _name


class PreservedWayOutDocsTest(unittest.TestCase):
    """Round 4 (Sol r3 P2): the CLI and QUICKSTART name the way out."""

    def test_the_remedies_and_quickstart_name_the_explicit_actions(self) -> None:
        import install_mcp

        quickstart = (Path(__file__).resolve().parents[2] / "QUICKSTART.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("**Interrupted install.**", quickstart)
        for code in (
            "registration_rollback_preserved_foreign_edit",
            "registration_preserved_foreign_edit",
            "registration_recovery_conflict",
        ):
            self.assertIn(f"`{code}`", quickstart)
        # Round 5 (Sol r4 P2): the user-scope entry needs -s user to be removed.
        self.assertIn("claude mcp remove -s user dayz-mcp", quickstart)
        self.assertIn("codex mcp remove dayz-mcp", quickstart)
        for remedy in (install_mcp._PRESERVED_REMEDY, install_mcp._RECOVERY_CONFLICT_REMEDY):
            self.assertIn("'Interrupted install'", remedy)
        # Follow-up (Sol r5 P2): the remedy that names remove is the one for a
        # releasable journal; the generic one promises only a full put-back.
        for remedy in (install_mcp._PRESERVED_REMEDY, install_mcp._RELEASE_REFUSED_REMEDY):
            self.assertIn("claude mcp remove -s user dayz-mcp", remedy)
            self.assertIn("codex mcp remove dayz-mcp", remedy)

    def test_main_prints_the_remedy_of_a_preserved_registration(self) -> None:
        import contextlib
        import io

        import install_mcp

        error = install_mcp._with_remedy(
            install_mcp.RegistrationTransactionError("registration_preserved_foreign_edit"),
            install_mcp._PRESERVED_REMEDY,
        )
        stderr = io.StringIO()
        with patch.object(install_mcp, "parse_args", side_effect=error), patch.object(
            install_mcp, "_ensure_posix_localappdata"
        ), contextlib.redirect_stderr(stderr):
            self.assertEqual(install_mcp.main([]), 2)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"], "registration_preserved_foreign_edit")
        self.assertEqual(payload["remedy"], install_mcp._PRESERVED_REMEDY)


@unittest.skipUnless(POSIX, "directory fsync is a POSIX barrier")
class DurableAncestorsTest(unittest.TestCase):
    """Residual: every directory mkdir creates is fsynced in its parent."""

    def test_each_new_ancestor_is_fsynced_in_its_parent(self) -> None:
        from dayz_mcp import host_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            synced: list[Path] = []
            with patch.object(
                host_config, "_fsync_dir_posix", side_effect=lambda p: synced.append(Path(p))
            ):
                host_config._mkdir_restricted(root / "a" / "b" / "journal")
            self.assertEqual(synced[:2], [root, root / "a"])
            self.assertIn(root / "a" / "b", synced)


class ElfHeaderTest(unittest.TestCase):
    """P2 of #465: the ELF check reads a whole ELF64 little-endian header."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))

    def _elf(self, name: str, payload: bytes) -> Path:
        path = self.root / name
        path.write_bytes(payload)
        return path

    def test_short_or_foreign_class_images_are_refused(self) -> None:
        import install_mcp as installer

        good = bytearray(64)
        good[0:4] = b"\x7fELF"
        good[4], good[5], good[6] = 2, 1, 1
        installer._validate_elf(self._elf("good", bytes(good)))
        cases = {
            "magic_only": b"\x7fELF",
            "short_header": bytes(good[:32]),
            "elf32": bytes(good[:4]) + b"\x01" + bytes(good[5:]),
            "big_endian": bytes(good[:5]) + b"\x02" + bytes(good[6:]),
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(installer.InstallerContractError) as raised:
                    installer._validate_elf(self._elf(name, payload))
                self.assertEqual(raised.exception.code, "installer_cli_not_elf")


if __name__ == "__main__":
    unittest.main()
