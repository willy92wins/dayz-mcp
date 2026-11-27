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
