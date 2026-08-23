"""Startup keyfile reads must use the pinned local-disk contract.

The mutation this file exists to catch: restore a raw unbounded text
read in ``loopback.read_key`` and ``test_oversized_keyfile_is_rejected``
returns 200000 bytes instead of raising.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from dayz_mcp import daemon, loopback, server


_OVERSIZE_BYTES = 200000
_SECRET_MARKER = b"SECRET-MARKER"


class StartupKeyfileHardeningTest(unittest.TestCase):
    def test_short_regular_keyfile_still_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_bytes(b"test-key\n")
            self.assertEqual(loopback.read_key(str(keyfile)), "test-key")

    def test_oversized_keyfile_is_rejected(self) -> None:
        payload = _SECRET_MARKER + (b"k" * (_OVERSIZE_BYTES - len(_SECRET_MARKER)))
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_bytes(payload)
            with self.assertRaises(ValueError) as raised:
                loopback.read_key(str(keyfile))
        message = str(raised.exception)
        self.assertIn("invalid_daemon_keyfile", message)
        self.assertNotIn(_SECRET_MARKER.decode("ascii"), message)
        self.assertNotIn("k" * 32, message)

    def test_read_key_delegates_to_the_pinned_reader(self) -> None:
        source = inspect.getsource(loopback.read_key)
        self.assertIn("read_pinned_keyfile", source)
        self.assertNotIn("open(", source)

    def test_both_startup_paths_load_the_key_through_read_key(self) -> None:
        self.assertIn(
            "read_key(_required_keyfile(config))",
            inspect.getsource(daemon.run_daemon),
        )
        self.assertIn(
            "read_key(required_keyfile(self.config))",
            inspect.getsource(server.Runtime.start_loopback),
        )


if __name__ == "__main__":
    unittest.main()
