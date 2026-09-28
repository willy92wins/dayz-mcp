from __future__ import annotations

import ctypes
import os
import stat
import struct
import tempfile
import unittest
from pathlib import Path

from dayz_mcp import launcher_registry, registry_lock


def _is_junction(path: Path) -> bool:
    # Path.is_junction arrived in 3.12 and the floor is 3.11 (pyproject.toml).
    # This is what it does: an lstat whose reparse tag is a mount point.
    try:
        return os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    except OSError:
        return False


def _set_junction_in_place(directory: Path, target: Path) -> None:
    """Turn the existing empty ``directory`` into a junction to ``target``, without renaming it.

    Opened with FILE_WRITE_ATTRIBUTES only, which share modes do not restrict,
    so a directory held by another handle can still be converted.
    """
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.DeviceIoControl.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    )
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    file_write_attributes, share_all, open_existing = 0x100, 0x7, 3
    backup_semantics_open_reparse_point = 0x02000000 | 0x00200000
    handle = kernel32.CreateFileW(
        str(directory), file_write_attributes, share_all, None, open_existing,
        backup_semantics_open_reparse_point, None,
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        substitute = ("\\??\\" + str(target)).encode("utf-16-le")
        printable = str(target).encode("utf-16-le")
        body = struct.pack(
            "<HHHH", 0, len(substitute), len(substitute) + 2, len(printable)
        ) + substitute + b"\0\0" + printable + b"\0\0"
        mount_point, set_reparse_point = 0xA0000003, 0x000900A4
        data = struct.pack("<IHH", mount_point, len(body), 0) + body
        buffer = ctypes.create_string_buffer(data)
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(
            handle, set_reparse_point, buffer, len(data), None, 0,
            ctypes.byref(returned), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


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
    # Any acquire now creates a missing lock (#93), so a clone can have the lock
    # without the registry it guards; the productive open needs both.
    if not launcher_registry._CANONICAL_REGISTRY.is_file():
        return "canonical launcher registry is absent"
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

    def test_a_lock_name_outside_the_bmp_is_created_whole(self) -> None:
        # Review of #114, round 3: the UNICODE_STRING length counted characters,
        # not UTF-16 code units, and truncated this name to "lock-\U0001F512.tx".
        lock = self.lock_path.with_name("lock-\U0001F512.txt")

        with registry_lock.acquire_registry_lock(exclusive=True, path=lock):
            pass

        self.assertTrue(lock.is_file())
        self.assertFalse(lock.with_name("lock-\U0001F512.tx").exists())

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

        # A junction higher up: the parent itself is a plain directory reached
        # through it, so only the check of the whole chain refuses it.
        (target / "sub").mkdir()
        with self.assertRaisesRegex(RuntimeError, "invalid_launcher_registry_lock"):
            registry_lock.acquire_registry_lock(
                exclusive=True, path=junction / "sub" / "approved-launchers.lock"
            )
        self.assertEqual(list((target / "sub").iterdir()), [])

    def test_the_parent_chain_cannot_move_while_the_lock_is_created(self) -> None:
        # Review of #114, F1: the parent was checked, then swapped for a junction
        # before the create. The chain is now frozen while the file is created.
        import _winapi
        from unittest.mock import patch

        root = self.lock_path.with_name("chain")
        parent, moved, redirected = root / "parent", root / "moved", root / "redirected"
        parent.mkdir(parents=True)
        redirected.mkdir()
        lock = parent / "approved-launchers.lock"
        original = registry_lock._create_missing_lock
        attempts: list[str] = []

        def swap_then_create(handle: int, name: str) -> None:
            try:
                parent.rename(moved)
                _winapi.CreateJunction(str(redirected), str(parent))
                attempts.append("swapped")
            except OSError as error:
                attempts.append(f"blocked: {error}")
            original(handle, name)

        self.addCleanup(lambda: _is_junction(parent) and os.rmdir(parent))
        with patch.object(registry_lock, "_create_missing_lock", swap_then_create):
            with registry_lock.acquire_registry_lock(exclusive=True, path=lock):
                pass

        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0].startswith("blocked"), attempts)
        self.assertTrue(lock.is_file())
        self.assertFalse(moved.exists())
        self.assertEqual(list(redirected.iterdir()), [])

    def test_a_parent_turned_into_a_junction_in_place_gets_nothing_created(self) -> None:
        # Review of #114, round 2: an empty parent can become a junction without
        # being renamed, through a FILE_WRITE_ATTRIBUTES handle that share modes
        # do not stop. The lock is created relative to the held parent, so that
        # create stops at the reparse point instead of going through it.
        from unittest.mock import patch

        root = self.lock_path.with_name("in-place")
        parent, target = root / "parent", root / "target"
        parent.mkdir(parents=True)
        target.mkdir()
        original = registry_lock._create_missing_lock
        converted: list[bool] = []

        def convert_then_create(handle: int, name: str) -> None:
            _set_junction_in_place(parent, target)
            converted.append(_is_junction(parent))
            original(handle, name)

        self.addCleanup(lambda: _is_junction(parent) and os.rmdir(parent))
        with patch.object(registry_lock, "_create_missing_lock", convert_then_create):
            with self.assertRaisesRegex(RuntimeError, "invalid_launcher_registry_lock"):
                registry_lock.acquire_registry_lock(
                    exclusive=True, path=parent / "approved-launchers.lock"
                )

        self.assertEqual(converted, [True])
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
