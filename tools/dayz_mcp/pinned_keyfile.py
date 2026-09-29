"""Bounded, local-only reading of the daemon authentication key."""

from __future__ import annotations

import ctypes
import ntpath
import os
import unicodedata
from ctypes import wintypes
from pathlib import Path

from dayz_mcp.win32_fileinfo import FILE_STANDARD_INFO as _FILE_STANDARD_INFO
from dayz_mcp.win32_fileinfo import bind_common_kernel32


_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_TYPE_DISK = 0x0001
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_FILE_STANDARD_INFO_CLASS = 1
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_DRIVE_REMOTE = 4
_MAX_RAW_BYTES = 4096
_MAX_KEY_CHARS = 1024


class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", wintypes.DWORD),
        ("ReparseTag", wintypes.DWORD),
    ]


_kernel32 = bind_common_kernel32()
_kernel32.GetFileAttributesW.argtypes = (wintypes.LPCWSTR,)
_kernel32.GetFileAttributesW.restype = wintypes.DWORD
_kernel32.GetLongPathNameW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
)
_kernel32.GetLongPathNameW.restype = wintypes.DWORD
_kernel32.GetShortPathNameW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
)
_kernel32.GetShortPathNameW.restype = wintypes.DWORD
_kernel32.ReadFile.argtypes = (
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
)
_kernel32.ReadFile.restype = wintypes.BOOL


def _local_canonical_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 520
        or "\0" in value
        or not unicodedata.is_normalized("NFC", value)
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        or ntpath.normpath(value) != value
    ):
        raise ValueError("invalid_daemon_keyfile")
    drive, tail = ntpath.splitdrive(value)
    if (
        len(drive) != 2
        or drive[1] != ":"
        or not drive[0].isascii()
        or not drive[0].isalpha()
        or not tail.startswith("\\")
        or ":" in tail
    ):
        raise ValueError("invalid_daemon_keyfile")
    root = drive + "\\"
    if _kernel32.GetDriveTypeW(root) == _DRIVE_REMOTE:
        raise ValueError("invalid_daemon_keyfile")
    return value


def _assert_no_reparse_parents(path: str) -> None:
    candidate = Path(path)
    for parent in reversed(candidate.parents):
        attributes = int(_kernel32.GetFileAttributesW(str(parent)))
        if (
            attributes == _INVALID_FILE_ATTRIBUTES
            or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("invalid_daemon_keyfile")


class CanonicalPathError(Exception):
    """GetLongPathNameW could not return a long form. Callers refuse."""


def _dos_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def canonical_long_path(path: str) -> str:
    """Long form of an existing path.

    Expands 8.3 names and leaves reparse points in the spelling. A failure
    raises. Callers try this only after the raw spelling compare missed:
    the call needs list access on every ancestor.
    """
    if not isinstance(path, str) or not path or "\0" in path:
        raise CanonicalPathError()
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(_kernel32.GetLongPathNameW(path, buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        raise CanonicalPathError()
    value = _dos_path(buffer.value)
    if not value:
        raise CanonicalPathError()
    return value


def _spelling(value: str, *, collapse: bool) -> str:
    if collapse:
        return os.path.normcase(os.path.normpath(value))
    return os.path.normcase(value)


def _short_path_name(path: str) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(_kernel32.GetShortPathNameW(path, buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        raise CanonicalPathError()
    value = _dos_path(buffer.value)
    if not value:
        raise CanonicalPathError()
    return value


def _differs_only_by_short_names(requested: str, expanded: str) -> bool:
    """True when each changed component is the 8.3 name of that long prefix.

    A trailing dot or space is not an alias. GetShortPathNameW failing
    refuses, so the caller keeps the mismatch main already refused.
    """
    requested_parts = requested.split("\\")
    expanded_parts = expanded.split("\\")
    if len(requested_parts) != len(expanded_parts) or not requested_parts:
        return False
    for index, (left, right) in enumerate(zip(requested_parts, expanded_parts)):
        if os.path.normcase(left) == os.path.normcase(right):
            continue
        if not left or left.endswith(".") or left.endswith(" "):
            return False
        try:
            short_prefix = _short_path_name("\\".join(expanded_parts[: index + 1]))
        except CanonicalPathError:
            return False
        short_leaf = short_prefix.split("\\")[-1]
        if not short_leaf or os.path.normcase(left) != os.path.normcase(short_leaf):
            return False
    return True


def _expanded_matches(candidate: str, observed: str, *, collapse: bool) -> bool:
    try:
        expanded = canonical_long_path(candidate)
    except CanonicalPathError:
        return False
    if _spelling(expanded, collapse=collapse) != _spelling(
        observed, collapse=collapse
    ):
        return False
    return _differs_only_by_short_names(candidate, expanded)


def same_requested_path(left: str, right: str, *, collapse: bool) -> bool:
    """True when main's spelling compare matches, or one side is its 8.3 form.

    The raw compare runs first and does not call GetLongPathNameW. A long
    path main already accepts stays accepted even when listing the parents
    is denied. Expansion is attempted only after a mismatch. It counts only
    when every changed component is that prefix's 8.3 name, with no trailing
    dot or space. A failure to expand or to read the short name leaves the
    mismatch: callers refuse.
    collapse is False for the installer, which must keep ".." and junctions.
    """
    if _spelling(left, collapse=collapse) == _spelling(right, collapse=collapse):
        return True
    if os.name != "nt":
        return False
    return _expanded_matches(left, right, collapse=collapse) or _expanded_matches(
        right, left, collapse=collapse
    )


def _final_handle_path(handle: object) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(
        _kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    )
    if length <= 0 or length >= len(buffer):
        raise ValueError("invalid_daemon_keyfile")
    return _dos_path(buffer.value)


def _read_bounded(handle: object) -> bytes:
    buffer = ctypes.create_string_buffer(_MAX_RAW_BYTES + 1)
    received = wintypes.DWORD()
    if not _kernel32.ReadFile(
        handle,
        buffer,
        len(buffer),
        ctypes.byref(received),
        None,
    ):
        raise ValueError("invalid_daemon_keyfile")
    raw = buffer.raw[: received.value]
    if not 1 <= len(raw) <= _MAX_RAW_BYTES:
        raise ValueError("invalid_daemon_keyfile")
    return raw


def read_pinned_keyfile(path: str) -> str:
    canonical = _local_canonical_path(path)
    _assert_no_reparse_parents(canonical)
    handle = _kernel32.CreateFileW(
        canonical,
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise ValueError("invalid_daemon_keyfile")
    try:
        if _kernel32.GetFileType(handle) != _FILE_TYPE_DISK:
            raise ValueError("invalid_daemon_keyfile")
        attributes = _FILE_ATTRIBUTE_TAG_INFO()
        standard = _FILE_STANDARD_INFO()
        if not _kernel32.GetFileInformationByHandleEx(
            handle,
            _FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(attributes),
            ctypes.sizeof(attributes),
        ) or not _kernel32.GetFileInformationByHandleEx(
            handle,
            _FILE_STANDARD_INFO_CLASS,
            ctypes.byref(standard),
            ctypes.sizeof(standard),
        ):
            raise ValueError("invalid_daemon_keyfile")
        if (
            attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or standard.Directory
            or standard.DeletePending
            or standard.NumberOfLinks != 1
            or standard.EndOfFile > _MAX_RAW_BYTES
        ):
            raise ValueError("invalid_daemon_keyfile")
        final_path = _final_handle_path(handle)
        if not same_requested_path(canonical, final_path, collapse=True):
            raise ValueError("invalid_daemon_keyfile")
        raw = _read_bounded(handle)
    finally:
        _kernel32.CloseHandle(handle)

    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("invalid_daemon_keyfile")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid_daemon_keyfile") from None
    key = text.strip()
    if (
        not key
        or len(key) > _MAX_KEY_CHARS
        or "\0" in key
        or "\r" in key
        or "\n" in key
    ):
        raise ValueError("invalid_daemon_keyfile")
    return key
