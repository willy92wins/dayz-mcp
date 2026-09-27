from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from dayz_mcp import launcher_registry, registry_lock


def _canonical_lock_blocker() -> str | None:
    """Why the canonical lock cannot serve a test right now, or None if it can.

    LockFileEx is process-global: a live launcher holding the in-tree lock is
    indistinguishable from the leak these tests look for. Ruling that out up
    front turns an ambiguous red into a named skip. It only covers a lock held
    *before* the test starts; a holder arriving mid-test still fails, which is
    the honest outcome for a race nobody can observe from here.
    """
    if not registry_lock._CANONICAL_LOCK.is_file():
        return "canonical registry lock file is absent"
    try:
        registry_lock.acquire_registry_lock(exclusive=True).close()
    except RuntimeError as error:
        if str(error) == "launcher_registry_busy":
            return "canonical registry lock is held by another process"
        raise
    return None


@unittest.skipUnless(os.name == "nt", "LockFileEx is Windows-only")
class RegistryLockTest(unittest.TestCase):
    def setUp(self) -> None:
        # Lock semantics need no particular file, so give each run its own
        # instead of contending for the in-tree canonical one.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.lock_path = Path(directory.name) / "approved-launchers.lock"
        self.lock_path.touch()

    def acquire(self, *, exclusive: bool) -> registry_lock.RegistryLock:
        return registry_lock.acquire_registry_lock(
            exclusive=exclusive, path=self.lock_path
        )

    def test_shared_readers_coexist_and_exclusive_is_fail_fast(self) -> None:
        first = self.acquire(exclusive=False)
        second = self.acquire(exclusive=False)
        try:
            with self.assertRaisesRegex(RuntimeError, "launcher_registry_busy"):
                self.acquire(exclusive=True)
        finally:
            second.close()
            first.close()

        with self.acquire(exclusive=True):
            with self.assertRaisesRegex(RuntimeError, "launcher_registry_busy"):
                self.acquire(exclusive=False)

    def test_missing_lock_is_created_once_then_checked_like_any_other(self) -> None:
        # A clone has no lock file: it is gitignored (#93).
        missing = self.lock_path.with_name("fresh-clone.lock")
        self.assertFalse(os.path.lexists(missing))

        with registry_lock.acquire_registry_lock(exclusive=True, path=missing):
            info = os.stat(missing, follow_symlinks=False)
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(info.st_nlink, 1)
            with self.assertRaisesRegex(RuntimeError, "launcher_registry_busy"):
                registry_lock.acquire_registry_lock(exclusive=False, path=missing)
        self.assertEqual(missing.read_bytes(), b"")
        with registry_lock.acquire_registry_lock(exclusive=False, path=missing):
            pass

    def test_lock_is_not_created_in_a_missing_directory_or_through_a_junction(
        self,
    ) -> None:
        orphan = self.lock_path.with_name("no-such-dir") / "approved-launchers.lock"
        with self.assertRaisesRegex(RuntimeError, "invalid_launcher_registry_lock"):
            registry_lock.acquire_registry_lock(exclusive=True, path=orphan)
        self.assertFalse(os.path.lexists(orphan.parent))

        import _winapi

        target = self.lock_path.with_name("junction-target")
        target.mkdir()
        junction = self.lock_path.with_name("junction")
        _winapi.CreateJunction(str(target), str(junction))
        self.addCleanup(os.rmdir, junction)
        with self.assertRaisesRegex(RuntimeError, "invalid_launcher_registry_lock"):
            registry_lock.acquire_registry_lock(
                exclusive=True, path=junction / "approved-launchers.lock"
            )
        self.assertEqual(list(target.iterdir()), [])

    def test_failed_productive_open_releases_its_shared_lock(self) -> None:
        # Stays on the canonical lock deliberately: the claim is about the
        # production call site, and open_approved_launcher is hard-wired to it.
        blocker = _canonical_lock_blocker()
        if blocker is not None:
            self.skipTest(blocker)
        with self.assertRaisesRegex(ValueError, "launcher_not_approved"):
            launcher_registry.open_approved_launcher("not-installed")
        with registry_lock.acquire_registry_lock(exclusive=True):
            pass


if __name__ == "__main__":
    unittest.main()
