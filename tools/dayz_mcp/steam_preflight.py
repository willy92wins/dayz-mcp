"""Fail-closed validation of Steam's registered active process.

This module is deliberately independent from launch/readiness orchestration.  Its
provider seam lets callers test the registry and process contract without
reading the local registry or enumerating real processes.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import ntpath
from typing import Protocol
from ctypes import wintypes


STEAM_SESSION_STALE = "steam_session_stale"
REMEDIATION = "restart_steam_and_wait_for_active_process_match"
_ACTIVE_PROCESS_KEY = r"Software\Valve\Steam\ActiveProcess"
_MAX_LIVE_PIDS = 8


@dataclass(frozen=True, slots=True)
class SteamActiveProcessSnapshot:
    """The two registry values needed to establish a Steam session."""

    pid: object
    active_user: object


@dataclass(frozen=True, slots=True)
class SteamSessionResult:
    """Closed, non-sensitive result contract exposed to future callers."""

    error_code: str | None
    steam_registered_pid: int | None
    steam_live_pids: tuple[int, ...]
    remediation: str


class SteamPreflightProvider(Protocol):
    """Host operations needed to evaluate a Steam session."""

    def read_active_process(self) -> SteamActiveProcessSnapshot: ...

    def process_exists(self, pid: int) -> bool: ...

    def process_image_path(self, pid: int) -> str: ...

    def steam_process_pids(self) -> tuple[int, ...]: ...


class WindowsSteamPreflightProvider:
    """Windows implementation that reads only Steam's ActiveProcess key."""

    def read_active_process(self) -> SteamActiveProcessSnapshot:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ACTIVE_PROCESS_KEY) as key:
            pid, _ = winreg.QueryValueEx(key, "pid")
            active_user, _ = winreg.QueryValueEx(key, "ActiveUser")
        return SteamActiveProcessSnapshot(pid=pid, active_user=active_user)

    def process_exists(self, pid: int) -> bool:
        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return False
        raise OSError(error, "OpenProcess failed")

    def process_image_path(self, pid: int) -> str:
        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            raise OSError(error, "OpenProcess failed")
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                error = ctypes.get_last_error()
                raise OSError(error, "QueryFullProcessImageNameW failed")
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)

    def steam_process_pids(self) -> tuple[int, ...]:
        kernel32 = _kernel32()
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                raise OSError(ctypes.get_last_error(), "Process32FirstW failed")
            pids: list[int] = []
            while True:
                if entry.szExeFile.casefold() == "steam.exe":
                    pids.append(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    error = ctypes.get_last_error()
                    if error == 18:  # ERROR_NO_MORE_FILES
                        break
                    raise OSError(error, "Process32NextW failed")
            return tuple(pids)
        finally:
            kernel32.CloseHandle(snapshot)


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _kernel32() -> ctypes.WinDLL:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    return kernel32


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _stale(registered_pid: int | None, live_pids: tuple[int, ...]) -> SteamSessionResult:
    return SteamSessionResult(
        error_code=STEAM_SESSION_STALE,
        steam_registered_pid=registered_pid,
        steam_live_pids=live_pids,
        remediation=REMEDIATION,
    )


def _read_stable_snapshot(provider: SteamPreflightProvider) -> SteamActiveProcessSnapshot | None:
    try:
        first = provider.read_active_process()
        second = provider.read_active_process()
    except Exception:
        return None
    if not isinstance(first, SteamActiveProcessSnapshot) or first != second:
        return None
    return first


def _safe_live_pids(provider: SteamPreflightProvider) -> tuple[int, ...] | None:
    try:
        raw_pids = provider.steam_process_pids()
        pids = set()
        for pid in raw_pids:
            if not _is_int(pid) or pid <= 0:
                return None
            pids.add(pid)
    except Exception:
        return None
    return tuple(sorted(pids)[:_MAX_LIVE_PIDS])


def evaluate_steam_session(
    provider: SteamPreflightProvider | None = None,
) -> SteamSessionResult:
    """Return PASS internally only for the exact registered Steam process.

    Any inability to obtain two identical registry snapshots, enumerate processes,
    or resolve the registered process image fails closed as ``steam_session_stale``.
    """

    selected_provider = WindowsSteamPreflightProvider() if provider is None else provider
    snapshot = _read_stable_snapshot(selected_provider)
    if snapshot is None:
        return _stale(None, ())

    registered_pid = snapshot.pid if _is_int(snapshot.pid) and snapshot.pid > 0 else None
    live_pids = _safe_live_pids(selected_provider)
    if live_pids is None:
        return _stale(registered_pid, ())
    if registered_pid is None or not _is_int(snapshot.active_user) or snapshot.active_user == 0:
        return _stale(registered_pid, live_pids)

    try:
        if not selected_provider.process_exists(registered_pid):
            return _stale(registered_pid, live_pids)
        image_path = selected_provider.process_image_path(registered_pid)
    except Exception:
        return _stale(registered_pid, live_pids)
    if not isinstance(image_path, str) or ntpath.basename(image_path).casefold() != "steam.exe":
        return _stale(registered_pid, live_pids)

    return SteamSessionResult(
        error_code=None,
        steam_registered_pid=registered_pid,
        steam_live_pids=live_pids,
        remediation=REMEDIATION,
    )


__all__ = [
    "REMEDIATION",
    "STEAM_SESSION_STALE",
    "SteamActiveProcessSnapshot",
    "SteamPreflightProvider",
    "SteamSessionResult",
    "WindowsSteamPreflightProvider",
    "evaluate_steam_session",
]
