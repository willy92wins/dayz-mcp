"""The security replacements the Linux port installs, and the loopback's rebind rule.

`linux_compat` swaps in POSIX versions of sealed checks: the pinned keyfile reader,
the path validators, the drive-aware path dispatch and the interpreter-image rule
that decides which processes may be reclaimed. Off Windows the loopback sets
SO_REUSEADDR, which on Linux never admits a second listener.
"""

from __future__ import annotations

import errno
import os
import socket
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path

POSIX = os.name != "nt"
if POSIX:
    from dayz_mcp import linux_compat

    linux_compat.install()
    from dayz_mcp import loopback


@unittest.skipUnless(POSIX, "the POSIX keyfile reader")
class PinnedKeyfilePosixTest(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(os.path.realpath(holder.name))

    def keyfile(self, name: str, content: bytes = b"secret-key\n", mode: int = 0o600) -> Path:
        path = self.root / name
        path.write_bytes(content)
        path.chmod(mode)
        return path

    def assert_refused(self, path: object) -> None:
        with self.assertRaisesRegex(ValueError, "^invalid_daemon_keyfile$"):
            linux_compat.read_pinned_keyfile_posix(path)

    def test_a_private_single_link_file_is_read(self) -> None:
        self.assertEqual(linux_compat.read_pinned_keyfile_posix(str(self.keyfile("k"))), "secret-key")

    def test_path_shape_is_refused(self) -> None:
        key = self.keyfile("k")
        for path in ("k", str(self.root) + "/./k", "", None, str(key) + "\0"):
            with self.subTest(path=path):
                self.assert_refused(path)

    def test_symbolic_links_are_refused_at_the_leaf_and_above(self) -> None:
        key = self.keyfile("k")
        (self.root / "leaf").symlink_to(key)
        real = self.root / "real"
        real.mkdir()
        self.keyfile("real/k")
        (self.root / "parent").symlink_to(real)
        for path in (self.root / "leaf", self.root / "parent" / "k"):
            with self.subTest(path=path):
                self.assert_refused(str(path))

    def test_group_or_world_writable_and_hard_linked_files_are_refused(self) -> None:
        self.assert_refused(str(self.keyfile("group", mode=0o620)))
        self.assert_refused(str(self.keyfile("world", mode=0o602)))
        linked = self.keyfile("linked")
        os.link(linked, self.root / "second-name")
        self.assert_refused(str(linked))

    def test_content_is_refused_like_the_win32_reader(self) -> None:
        cases = {
            "empty": b"",
            "blank": b" \n",
            "bom": b"\xef\xbb\xbfsecret\n",
            "two_lines": b"a\nb\n",
            "not_utf8": b"\xff\xfe",
            "too_big": b"k" * 4097,
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                self.assert_refused(str(self.keyfile(name, content)))


@unittest.skipUnless(POSIX, "the POSIX path validators")
class PosixPathRulesTest(unittest.TestCase):
    def test_valid_posix_local_path(self) -> None:
        for value in ("/abc", "/home/user/DayZ", "/" + "a" * 519):
            with self.subTest(value=value[:40]):
                self.assertTrue(linux_compat.valid_posix_local_path(value))
        for value in ("/a", "home/user", "//host/share", "/a/../b", "/a/", "/a//b", "/a\0b",
                      "/" + "a" * 520, "C:\\Users", None, b"/abc"):
            with self.subTest(value=str(value)[:40]):
                self.assertFalse(linux_compat.valid_posix_local_path(value))

    def test_drive_aware_path_keeps_ntpath_for_windows_shaped_values(self) -> None:
        aware = linux_compat.DRIVE_PATH_AWARE
        self.assertEqual(aware.join("C:\\mods", "@M"), "C:\\mods\\@M")
        self.assertEqual(aware.basename("C:\\mods\\@M"), "@M")
        self.assertEqual(aware.normcase("C:\\Mods"), "c:\\mods")
        self.assertTrue(aware.isabs("\\\\server\\share\\x"))
        self.assertEqual(aware.join("/mods", "@M"), "/mods/@M")
        self.assertEqual(aware.basename("/mods/@M"), "@M")
        self.assertEqual(aware.normcase("/Mods"), "/Mods")
        self.assertFalse(aware.isabs("mods/@M"))
        with self.assertRaises(AttributeError):
            aware.exists("/mods")

    def test_python_image_rule(self) -> None:
        for value in ("python", "python3", "python3.14", "/usr/bin/python3.11"):
            with self.subTest(value=value):
                self.assertTrue(linux_compat.is_python_image(value))
        for value in ("ipython", "python3-config", "pythonw", "python3.exe", "/usr/bin/sh", "", None):
            with self.subTest(value=value):
                self.assertFalse(linux_compat.is_python_image(value))


@unittest.skipUnless(POSIX, "what install() puts in place")
class InstallTest(unittest.TestCase):
    def test_the_sealed_modules_use_the_posix_replacements(self) -> None:
        from dayz_mcp import (
            daemon_policy_contract,
            dayz_test_attestation,
            dayz_test_request,
            dayz_test_storage,
            dayz_test_worker,
            pinned_keyfile,
        )

        self.assertIs(pinned_keyfile.read_pinned_keyfile, linux_compat.read_pinned_keyfile_posix)
        for module in (dayz_test_request, dayz_test_worker, dayz_test_attestation, dayz_test_storage):
            with self.subTest(module=module.__name__):
                self.assertIs(module.ntpath, linux_compat.DRIVE_PATH_AWARE)
        rules = {
            "daemon_policy_contract.valid_absolute_path": daemon_policy_contract.valid_absolute_path,
            "dayz_test_request._valid_local_absolute_path": dayz_test_request._valid_local_absolute_path,
            "dayz_test_worker._local_path": dayz_test_worker._local_path,
        }
        for name, rule in rules.items():
            with self.subTest(rule=name):
                self.assertTrue(rule("/home/user/DayZ"))


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass


@unittest.skipUnless(POSIX, "the loopback's SO_REUSEADDR off Windows")
class LoopbackRebindTest(unittest.TestCase):
    def server(self, port: int):
        server = loopback.ExclusiveThreadingHTTPServer(("127.0.0.1", port), _Quiet)
        self.addCleanup(server.server_close)
        return server

    def test_a_second_listener_on_the_same_port_is_refused(self) -> None:
        port = self.server(0).server_address[1]
        with self.assertRaises(OSError) as raised:
            self.server(port)
        self.assertEqual(raised.exception.errno, errno.EADDRINUSE)

    def test_a_restarted_daemon_binds_while_a_connection_sits_in_time_wait(self) -> None:
        first = self.server(0)
        port = first.server_address[1]
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _ = first.socket.accept()
        accepted.close()  # the server side closes first, so TIME_WAIT is on its port
        client.recv(1)
        client.close()
        first.server_close()
        self.assertEqual(self.server(port).server_address[1], port)


if __name__ == "__main__":
    unittest.main()
