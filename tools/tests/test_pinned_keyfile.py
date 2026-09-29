from __future__ import annotations

import ctypes
import hashlib
import importlib
import os
import subprocess
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from tests.pe_helpers import write_fake_x64_pe


class PinnedKeyfileTests(unittest.TestCase):
    def test_reads_one_local_regular_file_with_a_bounded_contract(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_bytes(b"test-key\n")
            self.assertEqual(module.read_pinned_keyfile(str(keyfile)), "test-key")

            keyfile.write_bytes(b"k" * 4097)
            with self.assertRaisesRegex(ValueError, "invalid_daemon_keyfile"):
                module.read_pinned_keyfile(str(keyfile))

            keyfile.write_bytes(b"k" * 1025)
            with self.assertRaisesRegex(ValueError, "invalid_daemon_keyfile"):
                module.read_pinned_keyfile(str(keyfile))

    def test_rejects_unc_device_ads_and_noncanonical_paths_before_open(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        invalid = (
            r"\\server\share\daemon.key",
            r"\\?\C:\keys\daemon.key",
            r"\\.\pipe\daemon.key",
            r"C:\keys\daemon.key:stream",
            r"C:\keys\..\keys\daemon.key",
            r"keys\daemon.key",
        )
        for path in invalid:
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, "invalid_daemon_keyfile"
            ):
                module.read_pinned_keyfile(path)

    def test_rejects_leaf_reparse_points_when_windows_can_create_one(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.key"
            link = root / "link.key"
            target.write_text("secret", encoding="utf-8")
            try:
                os.symlink(target, link)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error.winerror}")
            with self.assertRaisesRegex(ValueError, "invalid_daemon_keyfile"):
                module.read_pinned_keyfile(str(link))

    def test_trailing_dot_component_is_refused(self) -> None:
        # GetLongPathNameW strips one trailing dot from a short component
        # (foo. -> foo). A long component keeps the dot and never matches.
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        host_config = importlib.import_module("dayz_mcp.host_config")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "foo"
            folder.mkdir()
            keyfile = folder / "daemon.key"
            keyfile.write_bytes(b"test-key\n")
            final = str(keyfile.resolve())
            parent, name = final.rsplit("\\", 1)
            head, leaf = parent.rsplit("\\", 1)
            spelling = head + "\\" + leaf + ".\\" + name
            self.assertNotEqual(os.path.normcase(spelling), os.path.normcase(final))
            self.assertFalse(
                module.same_requested_path(spelling, final, collapse=True)
            )
            self.assertFalse(
                module.same_requested_path(spelling, final, collapse=False)
            )
            with self.assertRaisesRegex(ValueError, "invalid_daemon_keyfile"):
                module.read_pinned_keyfile(spelling)
            with self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_conflict$"
            ):
                host_config.require_matching_keyfile(spelling, final)

    def test_trailing_space_component_is_refused_when_expansion_strips_it(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "foo"
            folder.mkdir()
            keyfile = folder / "daemon.key"
            keyfile.write_bytes(b"test-key\n")
            final = str(keyfile.resolve())
            parent, name = final.rsplit("\\", 1)
            head, leaf = parent.rsplit("\\", 1)
            spelling = head + "\\" + leaf + " \\" + name

            def strip_to_final(path: str, buffer: ctypes.Array, size: int) -> int:
                del path, size
                buffer.value = final
                return len(final)

            with patch.object(
                module._kernel32, "GetLongPathNameW", side_effect=strip_to_final
            ):
                self.assertFalse(
                    module.same_requested_path(spelling, final, collapse=True)
                )
                self.assertFalse(
                    module.same_requested_path(spelling, final, collapse=False)
                )

    def test_mixed_short_directory_and_long_filename_is_read(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        host_config = importlib.import_module("dayz_mcp.host_config")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ALongDirectoryNameForShortPaths"
            folder.mkdir()
            keyfile = folder / "daemon-keyfile-name.key"
            keyfile.write_bytes(b"test-key\n")
            short_dir = _short_path_or_skip(self, folder)
            short_leaf = Path(short_dir).name
            final = str(keyfile.resolve())
            mixed = final.replace("\\" + folder.name + "\\", "\\" + short_leaf + "\\")
            if os.path.normcase(mixed) == os.path.normcase(final):
                self.skipTest(
                    "volume did not return an 8.3 short name distinct from the long path"
                )
            self.assertEqual(Path(mixed).name, keyfile.name)
            self.assertEqual(module.read_pinned_keyfile(mixed), "test-key")
            self.assertEqual(
                host_config.require_matching_keyfile(mixed, final), final
            )

    def test_short_path_is_refused_when_its_short_name_cannot_be_read(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ALongDirectoryNameForShortPaths"
            folder.mkdir()
            keyfile = folder / "daemon-keyfile-name.key"
            keyfile.write_bytes(b"test-key\n")
            short = _short_path_or_skip(self, keyfile)
            with patch.object(
                module._kernel32, "GetShortPathNameW", return_value=0
            ), self.assertRaisesRegex(ValueError, "invalid_daemon_keyfile"):
                module.read_pinned_keyfile(short)

    def test_short_path_of_the_same_file_is_read(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ALongDirectoryNameForShortPaths"
            folder.mkdir()
            keyfile = folder / "daemon-keyfile-name.key"
            keyfile.write_bytes(b"test-key\n")
            short = _short_path_or_skip(self, keyfile)
            self.assertEqual(module.read_pinned_keyfile(short), "test-key")

    def test_short_path_of_a_different_file_is_refused(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ALongDirectoryNameForShortPaths"
            folder.mkdir()
            keyfile = folder / "daemon-keyfile-name.key"
            other = folder / "another-keyfile-name.key"
            keyfile.write_bytes(b"test-key\n")
            other.write_bytes(b"other-key\n")
            short = _short_path_or_skip(self, keyfile)
            with patch.object(
                module, "_final_handle_path", return_value=str(other.resolve())
            ), self.assertRaisesRegex(ValueError, "invalid_daemon_keyfile"):
                module.read_pinned_keyfile(short)

    def test_short_path_of_a_hard_link_is_refused(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ALongDirectoryNameForShortPaths"
            folder.mkdir()
            keyfile = folder / "daemon-keyfile-name.key"
            link = folder / "hard-link-keyfile-name.key"
            keyfile.write_bytes(b"test-key\n")
            try:
                os.link(keyfile, link)
            except OSError as error:
                self.skipTest(f"hard link unavailable: {error}")
            short = _short_path_or_skip(self, link)
            with self.assertRaisesRegex(ValueError, "invalid_daemon_keyfile"):
                module.read_pinned_keyfile(short)

    def test_matching_long_path_is_read_when_expansion_fails(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_bytes(b"test-key\n")
            with patch.object(
                module._kernel32, "GetLongPathNameW", return_value=0
            ) as expanded:
                self.assertEqual(
                    module.read_pinned_keyfile(str(keyfile.resolve())), "test-key"
                )
            self.assertEqual(expanded.call_count, 0)

    def test_short_path_is_refused_when_expansion_fails(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ALongDirectoryNameForShortPaths"
            folder.mkdir()
            keyfile = folder / "daemon-keyfile-name.key"
            keyfile.write_bytes(b"test-key\n")
            short = _short_path_or_skip(self, keyfile)
            with patch.object(
                module._kernel32, "GetLongPathNameW", return_value=0
            ) as expanded, self.assertRaisesRegex(
                ValueError, "invalid_daemon_keyfile"
            ):
                module.read_pinned_keyfile(short)
            self.assertGreater(expanded.call_count, 0)

    def test_long_path_without_list_folder_is_accepted(self) -> None:
        module = importlib.import_module("dayz_mcp.pinned_keyfile")
        host_config = importlib.import_module("dayz_mcp.host_config")
        install_mcp = importlib.import_module("install_mcp")

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "PrivateParent"
            parent.mkdir()
            keyfile = parent / "daemon.key"
            keyfile.write_bytes(b"test-key\n")
            cli = parent / "claude.exe"
            write_fake_x64_pe(cli)
            account = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
            denied = subprocess.run(
                ["icacls", str(parent), "/deny", f"{account}:(RD)"],
                capture_output=True,
            )

            def restore() -> None:
                subprocess.run(
                    ["icacls", str(parent), "/remove:d", account],
                    capture_output=True,
                )

            try:
                if denied.returncode != 0:
                    self.skipTest("icacls could not deny list-folder")
                try:
                    readable = keyfile.read_bytes() == b"test-key\n"
                except OSError:
                    readable = False
                buffer = ctypes.create_unicode_buffer(32768)
                length = int(
                    module._kernel32.GetLongPathNameW(
                        str(keyfile), buffer, len(buffer)
                    )
                )
                if not readable or length != 0:
                    self.skipTest(
                        "could not remove list-folder permission while keeping the file readable"
                    )
                spelling = str(keyfile.resolve())
                self.assertEqual(module.read_pinned_keyfile(spelling), "test-key")
                self.assertEqual(
                    host_config.require_matching_keyfile(spelling, spelling),
                    spelling,
                )
                opened = host_config._PinnedConfigFile(keyfile)
                opened.close()
                payload = cli.read_bytes()
                entry = install_mcp._validate_cli_entry(
                    "CLAUDE",
                    {
                        "path": str(cli.resolve()),
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest().upper(),
                    },
                )
                self.assertEqual(entry.path, cli.resolve())
            finally:
                restore()


class ShortPathProvenanceTests(unittest.TestCase):
    def test_provenance_accepts_the_short_spelling_and_refuses_another_file(self) -> None:
        host_config = importlib.import_module("dayz_mcp.host_config")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ALongDirectoryNameForShortPaths"
            folder.mkdir()
            keyfile = folder / "daemon-keyfile-name.key"
            other = folder / "another-keyfile-name.key"
            keyfile.write_bytes(b"test-key\n")
            other.write_bytes(b"other-key\n")
            short = _short_path_or_skip(self, keyfile)
            short_other = _short_path_or_skip(self, other)
            expected = str(keyfile.resolve())
            self.assertEqual(
                host_config.require_matching_keyfile(short, expected), expected
            )
            opened = host_config._PinnedConfigFile(short)
            opened.close()
            with self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_conflict$"
            ):
                host_config.require_matching_keyfile(short_other, expected)

    def test_matching_long_path_is_accepted_when_expansion_fails(self) -> None:
        host_config = importlib.import_module("dayz_mcp.host_config")
        pinned = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_bytes(b"test-key\n")
            spelling = str(keyfile.resolve())
            with patch.object(
                pinned._kernel32, "GetLongPathNameW", return_value=0
            ) as expanded:
                self.assertEqual(
                    host_config.require_matching_keyfile(spelling, spelling),
                    spelling,
                )
                opened = host_config._PinnedConfigFile(keyfile)
                opened.close()
            self.assertEqual(expanded.call_count, 0)

    def test_short_path_is_refused_when_expansion_fails(self) -> None:
        host_config = importlib.import_module("dayz_mcp.host_config")
        pinned = importlib.import_module("dayz_mcp.pinned_keyfile")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "ALongDirectoryNameForShortPaths"
            folder.mkdir()
            keyfile = folder / "daemon-keyfile-name.key"
            keyfile.write_bytes(b"test-key\n")
            short = _short_path_or_skip(self, keyfile)
            expected = str(keyfile.resolve())
            with patch.object(
                pinned._kernel32, "GetLongPathNameW", return_value=0
            ) as expanded, self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_conflict$"
            ):
                host_config.require_matching_keyfile(short, expected)
            self.assertGreater(expanded.call_count, 0)


def _short_path_name(path: Path) -> str | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetShortPathNameW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    kernel32.GetShortPathNameW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(kernel32.GetShortPathNameW(str(path), buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        return None
    return buffer.value


def _short_path_or_skip(test: unittest.TestCase, path: Path) -> str:
    short = _short_path_name(path)
    long_form = str(path.resolve())
    if short is None or os.path.normcase(short) == os.path.normcase(long_form):
        test.skipTest(
            "volume did not return an 8.3 short name distinct from the long path"
        )
    return short


if __name__ == "__main__":
    unittest.main()
