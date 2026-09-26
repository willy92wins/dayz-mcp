from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
