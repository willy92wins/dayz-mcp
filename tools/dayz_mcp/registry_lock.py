"""Cross-process shared/exclusive lock for the native launcher registry."""

from __future__ import annotations

import ctypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from dayz_mcp.launcher_registry import (
    _identity_from_stat,
    _reject_path_name_surrogates,
)

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = (
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        )


_CANONICAL_LOCK = Path(__file__).resolve().parents[1] / "approved-launchers.lock"
_LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
_LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
_ERROR_LOCK_VIOLATION = 33

if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    )
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    )
    _kernel32.UnlockFileEx.restype = wintypes.BOOL


@dataclass
class RegistryLock:
    _stream: BinaryIO
    _overlapped: object
    _locked: bool = True

    def close(self) -> None:
        if self._locked:
            if os.name == "nt":
                handle = msvcrt.get_osfhandle(self._stream.fileno())
                _kernel32.UnlockFileEx(
                    handle, 0, 1, 0, ctypes.byref(self._overlapped)
                )
            self._locked = False
        self._stream.close()

    def __enter__(self) -> "RegistryLock":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def acquire_registry_lock(*, exclusive: bool, path: Path = _CANONICAL_LOCK) -> RegistryLock:
    if type(exclusive) is not bool or os.name != "nt":
        raise RuntimeError("launcher_registry_lock_unavailable")
    try:
        _reject_path_name_surrogates(
            path, error_code="invalid_launcher_registry_lock"
        )
        stream = path.open("r+b")
    except (OSError, ValueError) as error:
        raise RuntimeError("invalid_launcher_registry_lock") from error
    try:
        before = os.fstat(stream.fileno())
        lexical = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or int(before.st_nlink) != 1
            or _identity_from_stat(before) != _identity_from_stat(lexical)
        ):
            raise RuntimeError("invalid_launcher_registry_lock")
        overlapped = _OVERLAPPED()
        flags = _LOCKFILE_FAIL_IMMEDIATELY
        if exclusive:
            flags |= _LOCKFILE_EXCLUSIVE_LOCK
        handle = msvcrt.get_osfhandle(stream.fileno())
        if not _kernel32.LockFileEx(
            handle, flags, 0, 1, 0, ctypes.byref(overlapped)
        ):
            code = ctypes.get_last_error()
            if code == _ERROR_LOCK_VIOLATION:
                raise RuntimeError("launcher_registry_busy")
            raise RuntimeError("launcher_registry_lock_failed")
        locked = RegistryLock(stream, overlapped)
        try:
            after = os.fstat(stream.fileno())
            current = os.stat(path, follow_symlinks=False)
            if (
                _identity_from_stat(before) != _identity_from_stat(after)
                or _identity_from_stat(before) != _identity_from_stat(current)
            ):
                raise RuntimeError("launcher_registry_lock_drift")
            return locked
        except BaseException:
            locked.close()
            raise
    except BaseException:
        if not stream.closed:
            stream.close()
        raise
