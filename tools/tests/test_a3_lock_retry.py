"""A3 (964e): registry lock retry with bounded backoff and deadline."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dayz_mcp import registry_lock


@unittest.skipUnless(os.name == "nt", "LockFileEx is Windows-only")
class RegistryLockRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.lock_path = Path(directory.name) / "approved-launchers.lock"
        self.lock_path.touch()

    def test_busy_lock_is_acquired_after_release(self) -> None:
        holder = registry_lock.acquire_registry_lock(exclusive=True, path=self.lock_path)
        timer = threading.Timer(0.3, holder.close)
        timer.start()
        started = time.monotonic()
        try:
            with registry_lock.acquire_registry_lock_with_retry(
                exclusive=True, path=self.lock_path, deadline_s=5.0,
                initial_backoff_s=0.02, max_backoff_s=0.1,
            ):
                pass
        finally:
            timer.cancel()
        self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_busy_past_deadline_fails_clearly_without_hanging(self) -> None:
        with registry_lock.acquire_registry_lock(exclusive=True, path=self.lock_path):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "launcher_registry_busy_timeout"):
                registry_lock.acquire_registry_lock_with_retry(
                    exclusive=True, path=self.lock_path, deadline_s=0.3,
                    initial_backoff_s=0.02, max_backoff_s=0.05,
                )
            self.assertLess(time.monotonic() - started, 2.0)

    def test_non_busy_error_is_not_retried(self) -> None:
        missing = self.lock_path.with_name("no-such-dir") / "approved-launchers.lock"
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "invalid_launcher_registry_lock"):
            registry_lock.acquire_registry_lock_with_retry(
                exclusive=True, path=missing, deadline_s=5.0,
                initial_backoff_s=0.02, max_backoff_s=0.05,
            )
        self.assertLess(time.monotonic() - started, 1.0)

    def test_fail_fast_acquire_is_unchanged(self) -> None:
        with registry_lock.acquire_registry_lock(exclusive=True, path=self.lock_path):
            with self.assertRaisesRegex(RuntimeError, "^launcher_registry_busy$"):
                registry_lock.acquire_registry_lock(exclusive=True, path=self.lock_path)


if __name__ == "__main__":
    unittest.main()
