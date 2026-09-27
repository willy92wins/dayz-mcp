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

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        )

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        )

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = (
            ("Status", ctypes.c_void_p),
            ("Information", ctypes.c_size_t),
        )

    _files = bind_common_kernel32()
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtCreateFile.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_OBJECT_ATTRIBUTES),
        ctypes.POINTER(_IO_STATUS_BLOCK),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    _ntdll.NtCreateFile.restype = ctypes.c_long


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
_GENERIC_WRITE = 0x40000000
_SYNCHRONIZE = 0x00100000
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_CREATE = 2
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_FILE_NON_DIRECTORY_FILE = 0x00000040
_OBJ_CASE_INSENSITIVE = 0x00000040
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035

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
def _frozen_directory(directory: Path) -> Iterator[int]:
    """Hold ``directory`` so that neither it nor an ancestor can be renamed or replaced.

    A handle with data access (list/traverse) and no FILE_SHARE_DELETE makes a
    rename of the directory fail with a sharing violation, and a rename of any
    ancestor with access denied; an attributes-only handle does not block the
    directory itself (measured 2026-09-27). The directory is opened without
    following a reparse point and must be a plain directory. Yields the handle,
    for creating inside the directory without resolving its path again.
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
        yield int(handle)
    finally:
        _files.CloseHandle(handle)


def _create_missing_lock(directory_handle: int, name: str) -> None:
    """Create an empty lock file named ``name`` inside the held directory, create-only.

    The lock is gitignored, so a fresh clone has none and every acquire failed
    with invalid_launcher_registry_lock (#93). The file is created relative to
    the directory handle (NtCreateFile with RootDirectory), so the parent's path
    is never resolved again: a junction set on that directory in place after it
    was opened makes the create fail with STATUS_REPARSE_POINT_ENCOUNTERED and
    nothing is created (review of #114, F1; measured 2026-09-27). FILE_CREATE
    never reuses an existing file; that one is then checked like any other.
    """
    if not name or name in {".", ".."} or any(mark in name for mark in "\\/:"):
        raise ValueError("invalid_launcher_registry_lock")
    buffer = ctypes.create_unicode_buffer(name)
    # UNICODE_STRING lengths are in bytes of UTF-16: a character outside the
    # BMP takes two code units (review of #114, round 3).
    size = len(name.encode("utf-16-le"))
    object_name = _UNICODE_STRING(size, size + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = _OBJECT_ATTRIBUTES(
        ctypes.sizeof(_OBJECT_ATTRIBUTES),
        directory_handle,
        ctypes.pointer(object_name),
        _OBJ_CASE_INSENSITIVE,
        None,
        None,
    )
    status_block = _IO_STATUS_BLOCK()
    created = wintypes.HANDLE()
    status = _ntdll.NtCreateFile(
        ctypes.byref(created),
        _GENERIC_WRITE | _SYNCHRONIZE,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        _FILE_ATTRIBUTE_NORMAL,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        _FILE_CREATE,
        _FILE_NON_DIRECTORY_FILE | _FILE_SYNCHRONOUS_IO_NONALERT,
        None,
        0,
    ) & 0xFFFFFFFF
    if status == _STATUS_OBJECT_NAME_COLLISION:
        return  # created concurrently: the checks below decide
    if status != 0:
        raise OSError(0, f"launcher registry lock not created (NTSTATUS {status:#010x})")
    _files.CloseHandle(created)


def acquire_registry_lock(*, exclusive: bool, path: Path = _CANONICAL_LOCK) -> RegistryLock:
    if type(exclusive) is not bool or os.name != "nt":
        raise RuntimeError("launcher_registry_lock_unavailable")
    try:
        if not os.path.lexists(path):
            # Freeze the parent chain, check it is free of links and junctions,
            # and create relative to the held parent: nothing can swap or convert
            # a directory in between (review of #114, F1).
            with _frozen_directory(path.parent) as parent:
                _reject_path_name_surrogates(
                    path.parent, error_code="invalid_launcher_registry_lock"
                )
                _create_missing_lock(parent, path.name)
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
