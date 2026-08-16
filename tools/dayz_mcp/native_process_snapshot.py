"""Read-only native process identity helpers for daemon accreditation."""

from __future__ import annotations

import ntpath
import os
import sys

try:
    import psutil
except ImportError:  # pragma: no cover - fail-closed on unsupported installs.
    psutil = None  # type: ignore[assignment]


_IS_WINDOWS = sys.platform == "win32"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def full_image_path_of(pid: int) -> str | None:
    """Return the process image path using a query-only Windows handle."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if not _IS_WINDOWS:  # pragma: no cover - Windows is the supported target.
        return None
    handle = _kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return buffer.value
        return None
    finally:
        _kernel32.CloseHandle(handle)


def same_path(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return False
    return os.path.normcase(os.path.normpath(first)) == os.path.normcase(
        os.path.normpath(second)
    )


def command_argv_of(pid: int) -> list[str] | None:
    """Return one PID's structured argv, or None for partial identity."""
    if (
        psutil is None
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
    ):
        return None
    try:
        process = psutil.Process(pid)
        if getattr(process, "pid", None) != pid:
            return None
        with process.oneshot():
            argv = process.cmdline()
    except Exception:
        return None
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(argument, str) or not argument for argument in argv)
    ):
        return None
    return list(argv)


def working_directory_of(pid: int) -> str | None:
    """Return one PID's absolute cwd, or None for partial identity."""
    if (
        psutil is None
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
    ):
        return None
    try:
        process = psutil.Process(pid)
        if getattr(process, "pid", None) != pid:
            return None
        cwd = process.cwd()
    except Exception:
        return None
    if not isinstance(cwd, str) or not cwd or not ntpath.isabs(cwd):
        return None
    return cwd
