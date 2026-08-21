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


def _final_handle_path(handle: object) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(
        _kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    )
    if length <= 0 or length >= len(buffer):
        raise ValueError("invalid_daemon_keyfile")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


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
        if os.path.normcase(os.path.normpath(final_path)) != os.path.normcase(
            os.path.normpath(canonical)
        ):
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
