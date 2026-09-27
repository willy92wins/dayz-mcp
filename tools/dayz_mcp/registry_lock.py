"""Cross-process shared/exclusive lock for the native launcher registry."""

from __future__ import annotations

import ctypes
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from dayz_mcp.launcher_registry import (
    _identity_from_stat,
    _reject_path_name_surrogates,
)

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes

    from dayz_mcp.win32_fileinfo import FILE_STANDARD_INFO, bind_common_kernel32

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = (
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        )

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = (
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        )

    _files = bind_common_kernel32()


_CANONICAL_LOCK = Path(__file__).resolve().parents[1] / "approved-launchers.lock"
_LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
_LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
_ERROR_LOCK_VIOLATION = 33
_FILE_LIST_DIRECTORY = 0x00000001
_FILE_TRAVERSE = 0x00000020
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_STANDARD_INFO_CLASS = 1
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

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


@contextmanager
def _frozen_directory(directory: Path) -> Iterator[None]:
    """Hold ``directory`` so that neither it nor an ancestor can be renamed or replaced.

    A handle with data access (list/traverse) and no FILE_SHARE_DELETE makes a
    rename of the directory fail with a sharing violation, and a rename of any
    ancestor with access denied; an attributes-only handle does not block the
    directory itself (measured 2026-09-27). The directory is opened without
    following a reparse point and must be a plain directory.
    """
    handle = _files.CreateFileW(
        str(directory),
        _FILE_LIST_DIRECTORY | _FILE_TRAVERSE | _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle is None or handle == _INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "launcher registry lock directory unavailable")
    try:
        tag = _FILE_ATTRIBUTE_TAG_INFO()
        standard = FILE_STANDARD_INFO()
        if (
            not _files.GetFileInformationByHandleEx(
                handle, _FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(tag), ctypes.sizeof(tag)
            )
            or not _files.GetFileInformationByHandleEx(
                handle, _FILE_STANDARD_INFO_CLASS, ctypes.byref(standard), ctypes.sizeof(standard)
            )
            or tag.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or not standard.Directory
            or standard.DeletePending
        ):
            raise ValueError("invalid_launcher_registry_lock")
        yield
    finally:
        _files.CloseHandle(handle)


def _create_missing_lock(path: Path) -> None:
    """Create an empty lock file where nothing exists yet, create-only.

    The lock is gitignored, so a fresh clone has none and every acquire failed
    with invalid_launcher_registry_lock (#93). O_EXCL never reuses or follows
    whatever is already at the path; the new file is then opened and checked
    exactly like one that already existed. Callers hold the parent frozen.
    """
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
        )
    except FileExistsError:
        return  # created concurrently: the checks below decide
    os.close(descriptor)


def acquire_registry_lock(*, exclusive: bool, path: Path = _CANONICAL_LOCK) -> RegistryLock:
    if type(exclusive) is not bool or os.name != "nt":
        raise RuntimeError("launcher_registry_lock_unavailable")
    try:
        if not os.path.lexists(path):
            # Freeze the parent chain, check it is free of links and junctions,
            # and create inside it: nothing can swap a directory in between
            # (review of #114, F1).
            with _frozen_directory(path.parent):
                _reject_path_name_surrogates(
                    path.parent, error_code="invalid_launcher_registry_lock"
                )
                _create_missing_lock(path)
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
