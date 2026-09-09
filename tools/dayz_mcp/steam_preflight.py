"""Fail-closed validation of Steam's registered active process.

This module is deliberately independent from launch/readiness orchestration.  Its
provider seam lets callers test the registry and process contract without
reading the local registry or enumerating real processes.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, replace
import ntpath
import subprocess
import time
from collections.abc import Callable
from typing import Protocol
from ctypes import wintypes


STEAM_SESSION_STALE = "steam_session_stale"
REMEDIATION = "restart_steam_and_wait_for_active_process_match"
_ACTIVE_PROCESS_KEY = r"Software\Valve\Steam\ActiveProcess"
_STEAM_KEY = r"Software\Valve\Steam"
_MAX_LIVE_PIDS = 8
_SHUTDOWN_WAIT_S = 15.0
_ACTIVE_WAIT_S = 20.0
_POLL_INTERVAL_S = 0.2
_STEAM_INVOKE_FLAGS = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NO_WINDOW", 0
)


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


@dataclass(frozen=True, slots=True)
class SteamRemediationResult(SteamSessionResult):
    """Verdict and PID-repair diagnostics, separate from the evaluate contract.

    ``steam_left_down`` is true only when the restart fallback shut Steam down
    and could not bring it back. ``steam_remediation_reason`` retains the
    existing restart failure reasons; PID-repair outcomes have their own field.
    """

    steam_left_down: bool = False
    steam_remediation_reason: str | None = None
    steam_pid_repair_reason: str | None = None
    steam_previous_registered_pid: int | None = None
    steam_pid_repair_target_pid: int | None = None
    steam_pid_repair_error: str | None = None
    steam_restart_fallback: bool = False


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


class SteamRemediationHost(Protocol):
    """Mutating PID repair and Steam restart commands. Tests inject doubles."""

    def write_active_process_pid(self, pid: int) -> None: ...

    def steam_executable(self) -> str | None: ...

    def invoke_steam(self, executable: str, extra_args: tuple[str, ...]) -> None: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class WindowsSteamRemediationHost:
    """Write the verified PID or launch Steam detached; never wait on the child."""

    def write_active_process_pid(self, pid: int) -> None:
        """Set only pid as REG_DWORD; the caller must verify the live Steam PID."""
        if not _is_int(pid) or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("Steam PID must be a positive DWORD")
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _ACTIVE_PROCESS_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "pid", 0, winreg.REG_DWORD, pid)

    def steam_executable(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STEAM_KEY) as key:
                value, _ = winreg.QueryValueEx(key, "SteamExe")
        except OSError:
            return None
        if isinstance(value, str) and value:
            return value
        return None

    def invoke_steam(self, executable: str, extra_args: tuple[str, ...]) -> None:
        subprocess.Popen(
            [executable, *extra_args],
            close_fds=True,
            creationflags=_STEAM_INVOKE_FLAGS,
        )

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _steam_executable(
    provider: SteamPreflightProvider, host: SteamRemediationHost
) -> str | None:
    live_pids = _safe_live_pids(provider) or ()
    for pid in live_pids:
        try:
            image_path = provider.process_image_path(pid)
        except Exception:
            continue
        if (
            isinstance(image_path, str)
            and ntpath.basename(image_path).casefold() == "steam.exe"
        ):
            return image_path
    return host.steam_executable()


def _wait_until(
    host: SteamRemediationHost,
    timeout_s: float,
    predicate: Callable[[], bool],
) -> bool:
    deadline = host.monotonic() + timeout_s
    while True:
        passed = predicate()
        now = host.monotonic()
        if passed and now <= deadline:
            return True
        if now >= deadline:
            return False
        host.sleep(min(_POLL_INTERVAL_S, deadline - now))


def _wait_until_steam_down(
    provider: SteamPreflightProvider,
    host: SteamRemediationHost,
) -> str:
    """Distinguish down / still alive / unreadable. Never treat None as down."""

    deadline = host.monotonic() + _SHUTDOWN_WAIT_S
    while True:
        live = _safe_live_pids(provider)
        if live is None:
            return "unknown"
        if live == ():
            return "down"
        now = host.monotonic()
        if now >= deadline:
            return "alive"
        host.sleep(min(_POLL_INTERVAL_S, deadline - now))


def _remediation_result(
    session: SteamSessionResult, *, steam_left_down: bool = False,
    reason: str | None = None,
) -> SteamRemediationResult:
    return SteamRemediationResult(
        error_code=STEAM_SESSION_STALE if reason is not None else session.error_code,
        steam_registered_pid=session.steam_registered_pid,
        steam_live_pids=session.steam_live_pids,
        remediation=session.remediation,
        steam_left_down=steam_left_down,
        steam_remediation_reason=reason,
    )


def _try_repair_steam_pid(
    provider: SteamPreflightProvider, host: SteamRemediationHost,
) -> SteamRemediationResult:
    """One conservative write attempt; uncertainty leaves the restart fallback."""
    snapshot = _read_stable_snapshot(provider)
    previous_pid = None
    target_pid = None
    live_pids: tuple[int, ...] = ()

    def result(
        reason: str, error: Exception | None = None,
        session: SteamSessionResult | None = None,
    ) -> SteamRemediationResult:
        verdict = _stale(previous_pid, live_pids) if session is None else session
        return replace(
            _remediation_result(verdict),
            steam_pid_repair_reason=reason,
            steam_previous_registered_pid=previous_pid,
            steam_pid_repair_target_pid=target_pid,
            steam_pid_repair_error=None if error is None else type(error).__name__,
        )

    if snapshot is None:
        return result("snapshot_unreadable_or_unstable")
    if not _is_int(snapshot.pid) or not 0 <= snapshot.pid <= 0xFFFFFFFF:
        return result("registered_pid_invalid")
    previous_pid = snapshot.pid
    if not _is_int(snapshot.active_user) or not 0 < snapshot.active_user <= 0xFFFFFFFF:
        return result("active_user_invalid")
    candidates = _safe_live_pids(provider)
    if candidates is None:
        return result("process_list_unreadable")
    live_pids = candidates
    if not candidates:
        return result("no_steam_process")
    if len(candidates) != 1:
        return result("multiple_steam_processes")
    if candidates[0] > 0xFFFFFFFF:
        return result("target_pid_invalid")
    target_pid = candidates[0]

    def target_failure() -> SteamRemediationResult | None:
        try:
            if not provider.process_exists(target_pid):
                return result("target_not_running")
            image_path = provider.process_image_path(target_pid)
        except Exception as exc:
            return result("target_probe_failed", exc)
        if not isinstance(image_path, str) or ntpath.basename(image_path).casefold() != "steam.exe":
            return result("target_not_steam")
        return None

    failure = target_failure()
    if failure is not None:
        return failure
    if _safe_live_pids(provider) != candidates:
        return result("process_list_changed")
    if _read_stable_snapshot(provider) != snapshot:
        return result("registry_changed")

    # Legacy hosts only supported a restart; preserve that contract when they
    # have not opted into the PID-repair capability (including its healthy no-op).
    writer = getattr(host, "write_active_process_pid", None)
    if not callable(writer):
        return result("writer_unavailable")

    if previous_pid == target_pid:
        session = evaluate_steam_session(provider)
        if session.error_code is None and session.steam_registered_pid == target_pid:
            return result("already_correct", session=session)
        return result("verification_failed")

    # Recheck liveness AND image immediately before mutation. These independent
    # reads cannot make process/registry changes atomic; verify again afterward.
    failure = target_failure()
    if failure is not None:
        return failure
    try:
        writer(target_pid)
    except Exception as exc:
        return result("write_failed", exc)

    repaired = _read_stable_snapshot(provider)
    if repaired != SteamActiveProcessSnapshot(target_pid, snapshot.active_user):
        return result("verification_failed")
    session = evaluate_steam_session(provider)
    if session.error_code is not None or session.steam_registered_pid != target_pid:
        return result("verification_failed")
    return result("applied", session=session)


def remediate_stale_steam_session(
    provider: SteamPreflightProvider | None = None,
    host: SteamRemediationHost | None = None,
) -> SteamRemediationResult:
    """Repair only the verified live Steam PID first, otherwise use one restart.

    The fast path needs a stable registry snapshot, a valid ActiveUser, and a
    single live steam.exe whose identity is checked before writing. An already
    correct PID is a no-op. Failure retains the bounded shutdown/silent cycle
    and its existing error reasons. Call at most once for the same launch.
    """
    selected_provider = WindowsSteamPreflightProvider() if provider is None else provider
    selected_host = WindowsSteamRemediationHost() if host is None else host
    repair = _try_repair_steam_pid(selected_provider, selected_host)
    if repair.error_code is None:
        return repair
    restarted = _restart_steam_session(selected_provider, selected_host)
    return replace(
        restarted,
        steam_pid_repair_reason=repair.steam_pid_repair_reason,
        steam_previous_registered_pid=repair.steam_previous_registered_pid,
        steam_pid_repair_target_pid=repair.steam_pid_repair_target_pid,
        steam_pid_repair_error=repair.steam_pid_repair_error,
        steam_restart_fallback=True,
    )


def _restart_steam_session(
    selected_provider: SteamPreflightProvider, selected_host: SteamRemediationHost,
) -> SteamRemediationResult:
    """Existing bounded shutdown/relaunch, including its one relaunch retry."""
    executable = _steam_executable(selected_provider, selected_host)
    if executable is None:
        return _remediation_result(
            evaluate_steam_session(selected_provider), reason="steam_executable_unavailable"
        )
    try:
        selected_host.invoke_steam(executable, ("-shutdown",))
    except Exception:
        return _remediation_result(
            evaluate_steam_session(selected_provider), reason="shutdown_failed"
        )
    down_state = _wait_until_steam_down(selected_provider, selected_host)
    if down_state != "down":
        return _remediation_result(
            evaluate_steam_session(selected_provider),
            reason="shutdown_timeout" if down_state == "alive" else "process_list_unreadable",
        )
    try:
        selected_host.invoke_steam(executable, ("-silent",))
    except Exception:
        try:
            selected_host.invoke_steam(executable, ("-silent",))
        except Exception:
            return _remediation_result(
                evaluate_steam_session(selected_provider),
                steam_left_down=True,
                reason="relaunch_failed",
            )
    last_session = _stale(None, ())

    def active_process_matches() -> bool:
        nonlocal last_session
        last_session = evaluate_steam_session(selected_provider)
        return last_session.error_code is None

    matched = _wait_until(
        selected_host,
        _ACTIVE_WAIT_S,
        active_process_matches,
    )
    # Keep the verdict observed inside the budget. A fresh read after timeout
    # must not turn a failed wait into a successful remediation.
    return _remediation_result(
        last_session, reason=None if matched else "active_process_timeout"
    )


__all__ = [
    "REMEDIATION",
    "STEAM_SESSION_STALE",
    "SteamActiveProcessSnapshot",
    "SteamPreflightProvider",
    "SteamRemediationHost",
    "SteamSessionResult",
    "WindowsSteamPreflightProvider",
    "WindowsSteamRemediationHost",
    "evaluate_steam_session",
    "remediate_stale_steam_session",
]
