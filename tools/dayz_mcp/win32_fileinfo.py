"""Shared Win32 FILE_STANDARD_INFO and the kernel32 prototypes both
file-authority modules bind identically.

FILE_STANDARD_INFO.DeletePending and .Directory are BOOLEAN (1 byte),
not BOOL (4). The kernel writes 24 bytes; a BOOL layout is 32 bytes and
reads Directory past that buffer.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    ]


class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", wintypes.DWORD),
        ("ReparseTag", wintypes.DWORD),
    ]


# FileInformationClass values and CreateFile flags the namespace reader shares
# with the native launcher. Not a second layout of FILE_STANDARD_INFO.
FILE_STANDARD_INFO_CLASS = 1
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
ERROR_FILE_NOT_FOUND = 2
GENERIC_READ = 0x80000000
FILE_READ_ATTRIBUTES = 0x80
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_TYPE_DISK = 0x0001
PBO_PREFIX_MAX_BYTES = 512


def bind_common_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetFileType.argtypes = (wintypes.HANDLE,)
    kernel32.GetFileType.restype = wintypes.DWORD
    kernel32.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.ReadFile.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    kernel32.ReadFile.restype = wintypes.BOOL
    return kernel32


def last_win32_error() -> int:
    return ctypes.get_last_error()


def invalid_handle(value: object) -> bool:
    if value is None:
        return True
    return int(value) == int(wintypes.HANDLE(-1).value)  # type: ignore[arg-type]


def standard_info(kernel32: ctypes.WinDLL, handle: object) -> FILE_STANDARD_INFO | None:
    info = FILE_STANDARD_INFO()
    ok = kernel32.GetFileInformationByHandleEx(
        handle, FILE_STANDARD_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
    )
    return info if ok else None


def attribute_tag_info(
    kernel32: ctypes.WinDLL, handle: object
) -> FILE_ATTRIBUTE_TAG_INFO | None:
    info = FILE_ATTRIBUTE_TAG_INFO()
    ok = kernel32.GetFileInformationByHandleEx(
        handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
    )
    return info if ok else None


def read_handle(kernel32: ctypes.WinDLL, handle: object, size: int) -> bytes | None:
    buffer = ctypes.create_string_buffer(size)
    read = wintypes.DWORD(0)
    ok = kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None)
    if not ok or read.value != size:
        return None
    return buffer.raw
