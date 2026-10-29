"""B1 fix3: harness profile=test must not leak past instance release.

prepare() writes profile=test while the instance lives; retiring it
restores <profiles>/dayz_mcp.json byte-exactly, or removes it when the
harness created it. No game needed: ServerState on tmp profiles dirs.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import loopback

INSTALLER = b'{"url":"http://127.0.0.1:8765/","key":"k","pollHz":5}\n'


def _profiles(tmp: str) -> Path:
    profiles = Path(tmp) / "profiles"
    profiles.mkdir()
    return profiles


def _state() -> loopback.ServerState:
    return loopback.ServerState("k", config_port=8765)


class HarnessProfileRestoreTest(unittest.TestCase):
    def test_a_no_prior_config_release_leaves_no_profile_test(self) -> None:
        with TemporaryDirectory() as tmp:
            profiles = _profiles(tmp)
            state = _state()
            state.prepare("run-a", "server", str(profiles))
            config = profiles / "dayz_mcp.json"
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["profile"], "test")
            state.retire_run("run-a", "stopped")
            if config.exists():
                payload = json.loads(config.read_text(encoding="utf-8"))
                self.assertNotEqual(payload.get("profile"), "test")
            self.assertFalse((profiles / "dayz_mcp.json.harness-backup").exists())

    def test_b_installer_config_restored_byte_exact(self) -> None:
        with TemporaryDirectory() as tmp:
            profiles = _profiles(tmp)
            config = profiles / "dayz_mcp.json"
            config.write_bytes(INSTALLER)
            state = _state()
            state.prepare("run-b", "server", str(profiles))
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["profile"], "test")
            state.retire_role("run-b", "server", "stopped")
            self.assertEqual(config.read_bytes(), INSTALLER)

    def test_c_double_release_does_not_break(self) -> None:
        with TemporaryDirectory() as tmp:
            profiles = _profiles(tmp)
            config = profiles / "dayz_mcp.json"
            config.write_bytes(INSTALLER)
            state = _state()
            state.prepare("run-c", "server", str(profiles))
            state.retire_run("run-c", "stopped")
            state.retire_run("run-c", "stopped")
            state.retire_role("run-c", "server", "stopped")
            state.restore_harness_config(config)
            state.restore_harness_config(config)
            self.assertEqual(config.read_bytes(), INSTALLER)

    def test_d_crash_sidecar_restored_on_next_acquire_and_release(self) -> None:
        with TemporaryDirectory() as tmp:
            profiles = _profiles(tmp)
            config = profiles / "dayz_mcp.json"
            config.write_bytes(INSTALLER)
            crashed = _state()
            crashed.prepare("run-d", "server", str(profiles))
            backup = profiles / "dayz_mcp.json.harness-backup"
            self.assertEqual(backup.read_bytes(), INSTALLER)
            # Crash: no retire. Next acquisition must keep the good backup.
            state = _state()
            state.prepare("run-d2", "server", str(profiles))
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["profile"], "test")
            self.assertEqual(backup.read_bytes(), INSTALLER)
            state.retire_run("run-d2", "stopped")
            self.assertEqual(config.read_bytes(), INSTALLER)
            self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
