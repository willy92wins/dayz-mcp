#!/usr/bin/env python3
"""Experimental, fail-closed Job Object viability probe for retail DayZ.

This is deliberately not production lifecycle code.  It creates one suspended
DayZ_BE process, assigns it to a new named job before resuming it, observes only
that job's members, and requests normal window closure.  It has no forced-stop
path and never enumerates the machine's process tree.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable, Mapping, Sequence


DEFAULT_OBSERVE_SECONDS = 20.0
DEFAULT_CLOSE_TIMEOUT = 30.0
POLL_SECONDS = 0.25

CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_FLAGS = (
    CREATE_SUSPENDED
    | CREATE_UNICODE_ENVIRONMENT
    | EXTENDED_STARTUPINFO_PRESENT
)

# Windows SDK 10.0.26100 winbase.h: ProcThreadAttributeJobList=13 and
# PROC_THREAD_ATTRIBUTE_INPUT=0x00020000.
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_MORE_DATA = 234
WM_CLOSE = 0x0010

JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
UNSAFE_JOB_LIMIT_FLAGS = (
    JOB_OBJECT_LIMIT_BREAKAWAY_OK
    | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
    | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
)

JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

ULONG_PTR = ctypes.c_size_t
SIZE_T = ctypes.c_size_t
LPBYTE = ctypes.POINTER(wintypes.BYTE)


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", LPBYTE),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ULONG_PTR),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class WinApiFailure(RuntimeError):
    def __init__(self, operation: str, error_code: int | None = None) -> None:
        super().__init__(operation)
        self.operation = operation
        self.error_code = int(ctypes.get_last_error() if error_code is None else error_code)


class WinApi:
    """Small ctypes surface pinned to the documented Win32 signatures."""

    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

        # https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-createjobobjectw
        self.kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel32.CreateJobObjectW.restype = wintypes.HANDLE

        # https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw
        self.kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        self.kernel32.CreateProcessW.restype = wintypes.BOOL

        # https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-initializeprocthreadattributelist
        self.kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(SIZE_T),
        ]
        self.kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        # https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
        self.kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ULONG_PTR,
            ctypes.c_void_p,
            SIZE_T,
            ctypes.c_void_p,
            ctypes.POINTER(SIZE_T),
        ]
        self.kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        self.kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        self.kernel32.DeleteProcThreadAttributeList.restype = None

        # https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject
        self.kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryInformationJobObject.restype = wintypes.BOOL

        # https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-isprocessinjob
        self.kernel32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        self.kernel32.IsProcessInJob.restype = wintypes.BOOL
        self.kernel32.GetCurrentProcess.argtypes = []
        self.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel32.GetCurrentProcessId.argtypes = []
        self.kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        # https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-processidtosessionid
        self.kernel32.ProcessIdToSessionId.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

        self.kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel32.ResumeThread.restype = wintypes.DWORD
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

        # https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE

        # https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-queryfullprocessimagenamew
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

        # https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes
        self.kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        self.kernel32.GetProcessTimes.restype = wintypes.BOOL

        # EnumWindows returns top-level windows; PostMessageW only queues WM_CLOSE.
        # https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-enumwindows
        self.enum_windows_callback = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        self.user32.EnumWindows.argtypes = [self.enum_windows_callback, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        # https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-postmessagew
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostMessageW.restype = wintypes.BOOL


def non_negative_seconds(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a finite non-negative number") from exc
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("expected a finite non-negative number")
    return value


def select_close_target_pids(
    window_owner_pids: Iterable[int], current_job_member_pids: Iterable[int]
) -> tuple[int, ...]:
    members = {int(pid) for pid in current_job_member_pids if int(pid) > 0}
    return tuple(
        sorted({int(pid) for pid in window_owner_pids if int(pid) in members})
    )


def parent_job_preflight(parent_in_any_job: bool) -> dict[str, object]:
    return {
        "allowed": not parent_in_any_job,
        "reason": "parent_already_in_job" if parent_in_any_job else None,
    }


def manual_cleanup_after_error(
    process_created: bool, all_members_exited: bool
) -> bool:
    return bool(process_created and not all_members_exited)


def reconcile_process_creation(
    result: dict[str, object],
    process: PROCESS_INFORMATION,
    process_created: bool,
) -> bool:
    created = bool(
        process_created or process.hProcess or int(process.dwProcessId)
    )
    if created and result.get("launch") is None:
        result["launch"] = {
            "wrapper_pid": int(process.dwProcessId),
            "created_suspended": True,
            "assigned_at_creation": True,
            "membership_verified_before_resume": False,
            "resumed": False,
        }
    return created


def apply_caught_failure(
    result: dict[str, object],
    exc: BaseException,
    process_created: bool,
    all_members_exited: bool,
) -> tuple[dict[str, object], int]:
    if isinstance(exc, SystemExit):
        raise exc
    if isinstance(exc, KeyboardInterrupt):
        result["status"] = "INTERRUPTED"
        exit_code = 130
    elif isinstance(exc, Exception):
        result["status"] = "UNEXPECTED_ERROR"
        exit_code = 5
    else:
        raise exc
    result["error"] = {"type": type(exc).__name__}
    result["manual_cleanup_required"] = manual_cleanup_after_error(
        process_created, all_members_exited
    )
    return result, exit_code


def classify_observed_members(
    wrapper_pid: int, members: Sequence[Mapping[str, object]]
) -> dict[str, bool]:
    named_children = [
        member
        for member in members
        if int(member.get("pid") or 0) != int(wrapper_pid)
        and str(member.get("image_name") or "").casefold() == "dayz_x64.exe"
    ]
    opaque_descendant_seen = any(
        int(member.get("pid") or 0) != int(wrapper_pid)
        and not member.get("image_name")
        for member in members
    )
    return {
        "named_child_seen": bool(named_children),
        "opaque_descendant_seen": opaque_descendant_seen,
        "basic_identity_complete": any(
            bool(member.get("basic_identity_complete"))
            for member in named_children
        ),
    }


def redact_identity(
    *,
    pid: int,
    creation_filetime: int | None,
    image_path: str | None,
    query_errors: Mapping[str, object],
) -> dict[str, object]:
    path_sha256 = None
    image_name = None
    if image_path:
        image_name = PureWindowsPath(image_path).name
        path_sha256 = hashlib.sha256(
            image_path.casefold().encode("utf-8")
        ).hexdigest()
    allowed_error_keys = {"open_process", "image_path", "process_times"}
    error_codes = {
        str(key): int(value)
        for key, value in query_errors.items()
        if key in allowed_error_keys and isinstance(value, int) and not isinstance(value, bool)
    }
    return {
        "pid": int(pid),
        "creation_filetime": creation_filetime,
        "image_name": image_name,
        "image_path_sha256": path_sha256,
        "basic_identity_complete": bool(
            image_path and creation_filetime is not None
        ),
        "query_error_codes": error_codes,
    }


def _filetime_to_int(value: wintypes.FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _close_handle(api: WinApi, handle: wintypes.HANDLE | None) -> None:
    if handle:
        api.kernel32.CloseHandle(handle)


def current_process_in_any_job(api: WinApi) -> bool:
    in_job = wintypes.BOOL()
    if not api.kernel32.IsProcessInJob(
        api.kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)
    ):
        raise WinApiFailure("IsProcessInJob")
    return bool(in_job.value)


def current_process_context(api: WinApi) -> dict[str, object]:
    process_id = int(api.kernel32.GetCurrentProcessId())
    session_id = wintypes.DWORD()
    if not api.kernel32.ProcessIdToSessionId(
        process_id, ctypes.byref(session_id)
    ):
        raise WinApiFailure("ProcessIdToSessionId")
    return {
        "current_process_id": process_id,
        "current_session_id": int(session_id.value),
        "parent_in_any_job": current_process_in_any_job(api),
    }


def create_named_job(api: WinApi, job_name: str) -> wintypes.HANDLE:
    handle = api.kernel32.CreateJobObjectW(None, job_name)
    if not handle:
        raise WinApiFailure("CreateJobObjectW")
    return handle


def query_job_limit_flags(api: WinApi, job_handle: wintypes.HANDLE) -> int:
    value = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    returned = wintypes.DWORD()
    if not api.kernel32.QueryInformationJobObject(
        job_handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(value),
        ctypes.sizeof(value),
        ctypes.byref(returned),
    ):
        raise WinApiFailure("QueryInformationJobObject.limit_flags")
    return int(value.BasicLimitInformation.LimitFlags)


def query_job_active_processes(api: WinApi, job_handle: wintypes.HANDLE) -> int:
    value = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = wintypes.DWORD()
    if not api.kernel32.QueryInformationJobObject(
        job_handle,
        JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(value),
        ctypes.sizeof(value),
        ctypes.byref(returned),
    ):
        raise WinApiFailure("QueryInformationJobObject.accounting")
    return int(value.ActiveProcesses)


def query_job_member_pids(
    api: WinApi, job_handle: wintypes.HANDLE
) -> tuple[int, ...]:
    capacity = 16
    pointer_size = ctypes.sizeof(ULONG_PTR)
    while capacity <= 4096:
        size = 8 + capacity * pointer_size
        buffer = ctypes.create_string_buffer(size)
        returned = wintypes.DWORD()
        if api.kernel32.QueryInformationJobObject(
            job_handle,
            JOB_OBJECT_BASIC_PROCESS_ID_LIST,
            buffer,
            size,
            ctypes.byref(returned),
        ):
            count = int(wintypes.DWORD.from_buffer(buffer, 4).value)
            if count > capacity:
                capacity = count
                continue
            array_type = ULONG_PTR * count
            values = array_type.from_address(ctypes.addressof(buffer) + 8)
            return tuple(sorted(int(pid) for pid in values if int(pid) > 0))
        error_code = ctypes.get_last_error()
        if error_code == ERROR_MORE_DATA:
            capacity *= 2
            continue
        raise WinApiFailure("QueryInformationJobObject.members", error_code)
    raise WinApiFailure("QueryInformationJobObject.members_capacity", ERROR_MORE_DATA)


def query_process_identity(api: WinApi, pid: int) -> dict[str, object]:
    errors: dict[str, int] = {}
    image_path: str | None = None
    creation_filetime: int | None = None
    handle = api.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not handle:
        errors["open_process"] = int(ctypes.get_last_error())
        return redact_identity(
            pid=pid,
            creation_filetime=None,
            image_path=None,
            query_errors=errors,
        )
    try:
        capacity = wintypes.DWORD(32768)
        path_buffer = ctypes.create_unicode_buffer(capacity.value)
        if api.kernel32.QueryFullProcessImageNameW(
            handle, 0, path_buffer, ctypes.byref(capacity)
        ):
            image_path = path_buffer.value
        else:
            errors["image_path"] = int(ctypes.get_last_error())

        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if api.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            creation_filetime = _filetime_to_int(creation)
        else:
            errors["process_times"] = int(ctypes.get_last_error())
    finally:
        _close_handle(api, handle)
    return redact_identity(
        pid=pid,
        creation_filetime=creation_filetime,
        image_path=image_path,
        query_errors=errors,
    )


def create_suspended_process(
    api: WinApi,
    executable: Path,
    job_handle: wintypes.HANDLE,
    process: PROCESS_INFORMATION,
) -> PROCESS_INFORMATION:
    attribute_size = SIZE_T()
    api.kernel32.InitializeProcThreadAttributeList(
        None, 1, 0, ctypes.byref(attribute_size)
    )
    if attribute_size.value <= 0:
        raise WinApiFailure("InitializeProcThreadAttributeList.size")

    attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
    attribute_pointer = ctypes.cast(attribute_buffer, ctypes.c_void_p)
    initialized = False
    try:
        if not api.kernel32.InitializeProcThreadAttributeList(
            attribute_pointer, 1, 0, ctypes.byref(attribute_size)
        ):
            raise WinApiFailure("InitializeProcThreadAttributeList")
        initialized = True

        job_handles = (wintypes.HANDLE * 1)(job_handle)
        if not api.kernel32.UpdateProcThreadAttribute(
            attribute_pointer,
            0,
            PROC_THREAD_ATTRIBUTE_JOB_LIST,
            ctypes.cast(job_handles, ctypes.c_void_p),
            ctypes.sizeof(job_handles),
            None,
            None,
        ):
            raise WinApiFailure("UpdateProcThreadAttribute.job_list")

        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = attribute_pointer
        command_line = ctypes.create_unicode_buffer(f'"{executable}"')
        if not api.kernel32.CreateProcessW(
            str(executable),
            command_line,
            None,
            None,
            False,
            CREATE_FLAGS,
            None,
            str(executable.parent),
            ctypes.byref(startup),
            ctypes.byref(process),
        ):
            raise WinApiFailure("CreateProcessW.atomic_job")
        return process
    finally:
        if initialized:
            api.kernel32.DeleteProcThreadAttributeList(attribute_pointer)


def verify_membership_then_resume(
    api: WinApi,
    job_handle: wintypes.HANDLE,
    process: PROCESS_INFORMATION,
    *,
    on_verified: Callable[[], None] | None = None,
) -> bool:
    members = query_job_member_pids(api, job_handle)
    if int(process.dwProcessId) not in members:
        raise WinApiFailure("job_membership_before_resume", 0)
    if on_verified is not None:
        on_verified()
    if api.kernel32.ResumeThread(process.hThread) == 0xFFFFFFFF:
        raise WinApiFailure("ResumeThread")
    return True


def _redacted_members(api: WinApi, pids: Sequence[int]) -> list[dict[str, object]]:
    return [query_process_identity(api, pid) for pid in pids]


def observe_members(
    api: WinApi,
    job_handle: wintypes.HANDLE,
    wrapper_pid: int,
    seconds: float,
) -> tuple[list[dict[str, object]], dict[str, bool]]:
    started = time.monotonic()
    deadline = started + seconds
    observations: list[dict[str, object]] = []
    previous_signature: str | None = None
    signals = {
        "named_child_seen": False,
        "opaque_descendant_seen": False,
        "basic_identity_complete": False,
    }
    while True:
        pids = query_job_member_pids(api, job_handle)
        members = _redacted_members(api, pids)
        active = query_job_active_processes(api, job_handle)
        current_signals = classify_observed_members(wrapper_pid, members)
        for key, value in current_signals.items():
            signals[key] = signals[key] or value
        signature = json.dumps(
            {"active_processes": active, "members": members}, sort_keys=True
        )
        if signature != previous_signature:
            observations.append(
                {
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "active_processes": active,
                    "members": members,
                }
            )
            previous_signature = signature
        if time.monotonic() >= deadline:
            break
        time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    return observations, signals


def enumerate_visible_job_windows(
    api: WinApi, current_members: Sequence[int]
) -> list[tuple[int, int]]:
    allowed = set(current_members)
    found: list[tuple[int, int]] = []

    @api.enum_windows_callback
    def callback(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        api.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in allowed and api.user32.IsWindowVisible(hwnd):
            found.append((int(hwnd), int(pid.value)))
        return True

    if not api.user32.EnumWindows(callback, 0):
        raise WinApiFailure("EnumWindows")
    return found


def post_close_to_current_job_windows(
    api: WinApi, job_handle: wintypes.HANDLE
) -> list[dict[str, object]]:
    initial_members = query_job_member_pids(api, job_handle)
    candidates = enumerate_visible_job_windows(api, initial_members)
    selected_pids = set(
        select_close_target_pids((pid for _hwnd, pid in candidates), initial_members)
    )
    results: list[dict[str, object]] = []
    for hwnd, expected_pid in candidates:
        current_members = set(query_job_member_pids(api, job_handle))
        current_pid = wintypes.DWORD()
        api.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(current_pid))
        still_exact_member = (
            expected_pid in selected_pids
            and int(current_pid.value) == expected_pid
            and expected_pid in current_members
        )
        posted = False
        error_code = None
        if still_exact_member:
            posted = bool(api.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))
            if not posted:
                error_code = int(ctypes.get_last_error())
        results.append(
            {
                "pid": expected_pid,
                "still_exact_job_member": still_exact_member,
                "wm_close_posted": posted,
                "error_code": error_code,
            }
        )
    return results


def wait_for_empty_job(
    api: WinApi, job_handle: wintypes.HANDLE, timeout: float
) -> tuple[bool, int, list[dict[str, object]]]:
    deadline = time.monotonic() + timeout
    while True:
        active = query_job_active_processes(api, job_handle)
        if active == 0:
            return True, 0, []
        if time.monotonic() >= deadline:
            pids = query_job_member_pids(api, job_handle)
            return False, active, _redacted_members(api, pids)
        time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def _path_sha256(path: Path) -> str:
    return hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()


def emit_result_json(
    result: Mapping[str, object], output_path: str | Path | None
) -> str:
    serialized = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if output_path is not None:
        target = Path(output_path).expanduser().resolve()
        temp_path = target.with_name(
            f".{target.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temp_path.write_text(serialized, encoding="utf-8")
            os.replace(temp_path, target)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
    print(serialized)
    return serialized


def run_spike(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    executable = Path(args.dayz_be).expanduser().resolve()
    result: dict[str, object] = {
        "schema_version": 1,
        "experimental": True,
        "status": "ERROR",
        "configuration": {
            "executable_name": executable.name,
            "executable_path_sha256": _path_sha256(executable),
            "observe_seconds": args.observe_seconds,
            "close_timeout": args.close_timeout,
            "create_flags": CREATE_FLAGS,
        },
        "preflight": {"parent_in_any_job": None},
        "job": None,
        "launch": None,
        "observations": [],
        "named_child_seen": False,
        "opaque_descendant_seen": False,
        "job_containment_observed": False,
        "basic_identity_complete": False,
        "close_requests": [],
        "final": None,
        "forced_stop_available": False,
        "manual_cleanup_required": False,
    }
    if os.name != "nt":
        result["status"] = "UNSUPPORTED_PLATFORM"
        return result, 2
    if executable.name.casefold() != "dayz_be.exe" or not executable.is_file():
        result["status"] = "INVALID_EXECUTABLE"
        return result, 2

    api = WinApi()
    job_handle: wintypes.HANDLE | None = None
    process = PROCESS_INFORMATION()
    process_created = False
    all_members_exited = False
    try:
        context = current_process_context(api)
        preflight = parent_job_preflight(bool(context["parent_in_any_job"]))
        result["preflight"] = {
            **context,
            **preflight,
        }
        if getattr(args, "preflight_only", False):
            if preflight["allowed"]:
                result["status"] = "PREFLIGHT_OK"
                return result, 0
            result["status"] = "REFUSED_PARENT_ALREADY_IN_JOB"
            return result, 2
        if not preflight["allowed"]:
            result["status"] = "REFUSED_PARENT_ALREADY_IN_JOB"
            return result, 2

        job_name = f"Local\\DayZMCP-JobSpike-{uuid.uuid4()}"
        job_handle = create_named_job(api, job_name)
        limit_flags = query_job_limit_flags(api, job_handle)
        safety_flags_clear = (limit_flags & UNSAFE_JOB_LIMIT_FLAGS) == 0
        result["job"] = {
            "name_sha256": hashlib.sha256(job_name.encode("utf-8")).hexdigest(),
            "limit_flags": limit_flags,
            "no_kill_or_breakaway_flags": safety_flags_clear,
        }
        if not safety_flags_clear:
            result["status"] = "REFUSED_UNSAFE_JOB_FLAGS"
            return result, 2

        create_suspended_process(api, executable, job_handle, process)
        process_created = True
        result["launch"] = {
            "wrapper_pid": int(process.dwProcessId),
            "created_suspended": True,
            "assigned_at_creation": True,
            "membership_verified_before_resume": False,
            "resumed": False,
        }
        def mark_containment_verified() -> None:
            result["launch"]["membership_verified_before_resume"] = True
            result["job_containment_observed"] = True

        verify_membership_then_resume(
            api,
            job_handle,
            process,
            on_verified=mark_containment_verified,
        )
        result["launch"]["resumed"] = True

        observations, signals = observe_members(
            api,
            job_handle,
            int(process.dwProcessId),
            args.observe_seconds,
        )
        result["observations"] = observations
        result.update(signals)

        result["close_requests"] = post_close_to_current_job_windows(api, job_handle)
        empty, active, members = wait_for_empty_job(
            api, job_handle, args.close_timeout
        )
        all_members_exited = empty
        result["final"] = {
            "active_processes": active,
            "members": members,
            "all_members_exited": empty,
        }
        if not empty:
            result["status"] = "CLOSE_TIMEOUT"
            result["manual_cleanup_required"] = True
            return result, 4
        if not signals["named_child_seen"]:
            if signals["opaque_descendant_seen"]:
                result["status"] = "OPAQUE_DESCENDANT_OBSERVED"
                return result, 3
            result["status"] = "DAYZ_CHILD_NOT_OBSERVED"
            return result, 3
        if not signals["basic_identity_complete"]:
            result["status"] = "DAYZ_CHILD_BASIC_IDENTITY_INCOMPLETE"
            return result, 3
        result["status"] = "PASS"
        return result, 0
    except WinApiFailure as exc:
        process_created = reconcile_process_creation(
            result, process, process_created
        )
        result["status"] = "WINAPI_ERROR"
        result["error"] = {
            "operation": exc.operation,
            "error_code": exc.error_code,
        }
        result["manual_cleanup_required"] = manual_cleanup_after_error(
            process_created, all_members_exited
        )
        return result, 5
    except KeyboardInterrupt as exc:
        process_created = reconcile_process_creation(
            result, process, process_created
        )
        return apply_caught_failure(
            result, exc, process_created, all_members_exited
        )
    except Exception as exc:
        process_created = reconcile_process_creation(
            result, process, process_created
        )
        return apply_caught_failure(
            result, exc, process_created, all_members_exited
        )
    finally:
        _close_handle(api, process.hThread)
        _close_handle(api, process.hProcess)
        _close_handle(api, job_handle)


def build_parser() -> argparse.ArgumentParser:
    default_game = os.environ.get(
        "DAYZ_GAME_PATH",
        r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dayz-be",
        default=str(Path(default_game) / "DayZ_BE.exe"),
        help="Path to DayZ_BE.exe (never emitted verbatim in JSON).",
    )
    parser.add_argument(
        "--observe-seconds",
        type=non_negative_seconds,
        default=DEFAULT_OBSERVE_SECONDS,
    )
    parser.add_argument(
        "--close-timeout",
        type=non_negative_seconds,
        default=DEFAULT_CLOSE_TIMEOUT,
    )
    parser.add_argument(
        "--output-json",
        help="Write the same redacted stdout JSON atomically to this path.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate executable and launcher process/session without creating a job or process.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result, exit_code = run_spike(args)
    emit_result_json(result, args.output_json)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
