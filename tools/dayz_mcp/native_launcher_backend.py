from __future__ import annotations

import asyncio
import ctypes
import tempfile
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol

from dayz_mcp.native_debug_state import (
    DBG_CONTINUE,
    DBG_EXCEPTION_NOT_HANDLED,
    EXCEPTION_BREAKPOINT,
    NativeDebugDecision,
    NativeDebugEvent,
    NativeDebugState,
)
from dayz_mcp.native_broker_protocol import BrokerKind
from dayz_mcp.native_child_announcement import ChildAnnouncement, ChildAnnouncementDecoder


_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_SUSPENDED = 0x00000004
_MAX_ADDON_HELPER_LAUNCHES = 64
_DEBUG_PROCESS = 0x00000001
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_DUPLICATE_SAME_ACCESS = 0x00000002
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_JOB_OBJECT_ASSOCIATE_COMPLETION_PORT_INFORMATION = 7
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

CREATE_UNICODE_ENVIRONMENT = _CREATE_UNICODE_ENVIRONMENT
CREATE_SUSPENDED = _CREATE_SUSPENDED
DEBUG_PROCESS = _DEBUG_PROCESS
EXTENDED_STARTUPINFO_PRESENT = _EXTENDED_STARTUPINFO_PRESENT
CREATE_FLAGS = (
    CREATE_UNICODE_ENVIRONMENT
    | DEBUG_PROCESS
    | EXTENDED_STARTUPINFO_PRESENT
)
STARTF_USESTDHANDLES = _STARTF_USESTDHANDLES
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = _PROC_THREAD_ATTRIBUTE_HANDLE_LIST
PROC_THREAD_ATTRIBUTE_JOB_LIST = _PROC_THREAD_ATTRIBUTE_JOB_LIST
JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO = 4
JOB_OBJECT_MSG_NEW_PROCESS = 6
_ERROR_SEM_TIMEOUT = 121
_DEBUG_DRAIN_SECONDS = 5.0
_CANCEL_GRACE_SECONDS = 25.0
_REQUEST_WRITE_SECONDS = 5.0
_LAUNCHER_START_SECONDS = 20.0
_DEBUG_POLL_MS = 100
_CHILD_CORRELATION_SECONDS = 1.0

_SIZE_T = ctypes.c_size_t
_ULONG_PTR = ctypes.c_size_t
_LPBYTE = ctypes.POINTER(wintypes.BYTE)


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
        ("lpReserved2", _LPBYTE),
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
        ("MinimumWorkingSetSize", _SIZE_T),
        ("MaximumWorkingSetSize", _SIZE_T),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", _ULONG_PTR),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", _SIZE_T),
        ("JobMemoryLimit", _SIZE_T),
        ("PeakProcessMemoryUsed", _SIZE_T),
        ("PeakJobMemoryUsed", _SIZE_T),
    ]


class JOBOBJECT_ASSOCIATE_COMPLETION_PORT(ctypes.Structure):
    _fields_ = [
        ("CompletionKey", ctypes.c_void_p),
        ("CompletionPort", wintypes.HANDLE),
    ]


class _PinnedLauncher(Protocol):
    path: Path

    def validate_native_pe(self) -> None: ...

    def approve_root_debug_image(self, file_handle: int) -> bool: ...


class _DebugImageAuthority(Protocol):
    def approve_debug_image(self, file_handle: int, *, event_kind: str) -> bool: ...

    def approve_announced_process(
        self,
        file_handle: int,
        announcement: ChildAnnouncement,
    ) -> bool: ...

    def approve_addon_helper_process(self, file_handle: int) -> bool: ...


class NativeLauncherBackendError(RuntimeError):
    """Stable-code launcher failure; optional local-only diagnostic detail.

    ``str(self)`` always starts with ``code``. Structured fields are for local
    tracebacks/logs only — they must not be forwarded over the MCP wire.
    """

    def __init__(
        self,
        code: str,
        detail: str | None = None,
        *,
        fine_code: str | None = None,
        event_kind: str | None = None,
        pid: int | None = None,
        image_path: str | None = None,
    ) -> None:
        self.code = code
        self.detail = detail
        self.fine_code = fine_code
        self.event_kind = event_kind
        self.pid = pid
        self.image_path = image_path
        if detail is None:
            super().__init__(code)
        else:
            super().__init__(f"{code}:{detail}")


@dataclass(frozen=True, slots=True)
class LauncherInheritedHandles:
    stdin_read: int
    stdout_write: int
    stderr_write: int
    cancel_read: int
    worker_cancel_read: int

    def __post_init__(self) -> None:
        values = self.as_tuple()
        if (
            any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                for value in values
            )
            or len(set(values)) != len(values)
        ):
            raise NativeLauncherBackendError("invalid_native_launcher_handles")

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return (
            self.stdin_read,
            self.stdout_write,
            self.stderr_write,
            self.cancel_read,
            self.worker_cancel_read,
        )


@dataclass(slots=True)
class CreatedRegisteredLauncher:
    job_handle: int
    completion_port_handle: int
    process_information: PROCESS_INFORMATION
    _root_image_authority: _PinnedLauncher
    private_directory: Path
    _private_directory_owner: object
    _closed: bool = False

    def close_job(self) -> None:
        if self.job_handle:
            _close_handle(self.job_handle)
            self.job_handle = 0

    def close_tree(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.close_job()
        _close_handle(self.process_information.hThread)
        _close_handle(self.process_information.hProcess)
        _close_handle(self.completion_port_handle)
        self.process_information.hThread = None
        self.process_information.hProcess = None
        self.completion_port_handle = 0
        self._private_directory_owner.cleanup()


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_kernel32.SetInformationJobObject.restype = wintypes.BOOL
_kernel32.CreateIoCompletionPort.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    _ULONG_PTR,
    wintypes.DWORD,
]
_kernel32.CreateIoCompletionPort.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.DuplicateHandle.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
_kernel32.DuplicateHandle.restype = wintypes.BOOL
_kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(_SIZE_T),
]
_kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
_kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    _ULONG_PTR,
    ctypes.c_void_p,
    _SIZE_T,
    ctypes.c_void_p,
    ctypes.POINTER(_SIZE_T),
]
_kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
_kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
_kernel32.DeleteProcThreadAttributeList.restype = None
_kernel32.CreateProcessW.argtypes = [
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
_kernel32.CreateProcessW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CreatePipe.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.c_void_p,
    wintypes.DWORD,
]
_kernel32.CreatePipe.restype = wintypes.BOOL
_kernel32.ContinueDebugEvent.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
]
_kernel32.ContinueDebugEvent.restype = wintypes.BOOL
_kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
_kernel32.WriteFile.restype = wintypes.BOOL
_kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_kernel32.CancelIoEx.restype = wintypes.BOOL
_kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
_kernel32.ReadFile.restype = wintypes.BOOL
_kernel32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.PeekNamedPipe.restype = wintypes.BOOL
_kernel32.GetQueuedCompletionStatus.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(_ULONG_PTR),
    ctypes.POINTER(wintypes.LPVOID),
    wintypes.DWORD,
]
_kernel32.GetQueuedCompletionStatus.restype = wintypes.BOOL


def _handle_value(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, ctypes.c_void_p):
        return int(value.value or 0)
    raise NativeLauncherBackendError("invalid_native_launcher_handle_value")


def _close_handle(handle: object) -> None:
    value = _handle_value(handle)
    if value:
        _kernel32.CloseHandle(wintypes.HANDLE(value))


@dataclass(slots=True)
class NativeRuntimePipes:
    stdin_read: int
    stdin_write: int
    stdout_read: int
    stdout_write: int
    stderr_read: int
    stderr_write: int
    cancel_read: int
    cancel_write: int
    worker_cancel_read: int = 0
    worker_cancel_write: int = 0
    _closed: bool = False

    def child_handles(self) -> LauncherInheritedHandles:
        return LauncherInheritedHandles(
            self.stdin_read,
            self.stdout_write,
            self.stderr_write,
            self.cancel_read,
            self.worker_cancel_read,
        )

    def parent_handles(self) -> tuple[int, int, int, int, int]:
        return (
            self.stdin_write,
            self.stdout_read,
            self.stderr_read,
            self.cancel_write,
            self.worker_cancel_write,
        )

    def owned_handles(self) -> tuple[int, ...]:
        return (
            self.stdin_read,
            self.stdin_write,
            self.stdout_read,
            self.stdout_write,
            self.stderr_read,
            self.stderr_write,
            self.cancel_read,
            self.cancel_write,
            self.worker_cancel_read,
            self.worker_cancel_write,
        )

    def close_cancel_writer(self) -> None:
        handles = (self.cancel_write, self.worker_cancel_write)
        self.cancel_write = 0
        self.worker_cancel_write = 0
        for handle in handles:
            _close_handle(handle)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in reversed(self.owned_handles()):
            _close_handle(handle)


def _create_runtime_pipes() -> NativeRuntimePipes:
    owned: list[int] = []
    try:
        for _index in range(5):
            read_handle = wintypes.HANDLE()
            write_handle = wintypes.HANDLE()
            if not _kernel32.CreatePipe(
                ctypes.byref(read_handle),
                ctypes.byref(write_handle),
                None,
                0,
            ):
                raise NativeLauncherBackendError("native_launcher_pipe_failed")
            pair = (_handle_value(read_handle), _handle_value(write_handle))
            if (
                any(value <= 0 for value in pair)
                or pair[0] == pair[1]
                or any(value in owned for value in pair)
            ):
                for value in pair:
                    if value > 0 and value not in owned:
                        _close_handle(value)
                raise NativeLauncherBackendError("native_launcher_pipe_failed")
            owned.extend(pair)
        return NativeRuntimePipes(*owned)
    except BaseException:
        for handle in reversed(owned):
            _close_handle(handle)
        raise


def _validate_closed_inputs(
    opened_launcher: _PinnedLauncher,
    handles: LauncherInheritedHandles,
    identity_json: str,
    lease_token: str,
    daemon_policy_json: str,
) -> None:
    if not isinstance(handles, LauncherInheritedHandles):
        raise NativeLauncherBackendError("invalid_native_launcher_handles")
    path = Path(opened_launcher.path)
    if not path.is_absolute() or path.suffix.casefold() != ".exe":
        raise NativeLauncherBackendError("invalid_registered_launcher")
    if (
        not isinstance(identity_json, str)
        or not identity_json
        or len(identity_json) > 8192
        or "\0" in identity_json
        or not isinstance(lease_token, str)
        or not lease_token
        or len(lease_token) > 4096
        or "\0" in lease_token
        or not isinstance(daemon_policy_json, str)
        or not 1 <= len(daemon_policy_json) <= 8191
        or "\0" in daemon_policy_json
    ):
        raise NativeLauncherBackendError("invalid_native_launcher_environment")


def _duplicate_inheritable_handles(
    handles: LauncherInheritedHandles,
    owned_duplicates: list[int],
) -> None:
    process = _kernel32.GetCurrentProcess()
    for source in handles.as_tuple():
        target = wintypes.HANDLE()
        if not _kernel32.DuplicateHandle(
            process,
            wintypes.HANDLE(source),
            process,
            ctypes.byref(target),
            0,
            True,
            _DUPLICATE_SAME_ACCESS,
        ):
            raise NativeLauncherBackendError("native_launcher_create_failed")
        value = _handle_value(target)
        if not value or value in owned_duplicates:
            _close_handle(target)
            raise NativeLauncherBackendError("native_launcher_create_failed")
        owned_duplicates.append(value)


def _environment_buffer(
    *, identity_json: str, lease_token: str, cancel_handle: int,
    worker_cancel_handle: int, daemon_policy_json: str
) -> ctypes.Array[ctypes.c_wchar]:
    user_profile = str(Path.home().resolve(strict=True))
    if not Path(user_profile).is_absolute() or "\0" in user_profile:
        raise NativeLauncherBackendError("invalid_native_launcher_environment")
    entries = (
        f"DAYZ_MCP_CANCEL_HANDLE={cancel_handle}",
        f"DAYZ_MCP_WORKER_CANCEL_HANDLE={worker_cancel_handle}",
        f"DAYZ_MCP_CLIENT_ID_JSON={identity_json}",
        f"DAYZ_MCP_LEASE_TOKEN={lease_token}",
        f"DAYZ_MCP_NORMAL_POLICY_JSON={daemon_policy_json}",
        f"USERPROFILE={user_profile}",
    )
    return ctypes.create_unicode_buffer("\0".join(entries) + "\0")


def _create_registered_launcher(
    opened_launcher: _PinnedLauncher,
    *,
    handles: LauncherInheritedHandles,
    identity_json: str,
    lease_token: str,
    daemon_policy_json: str,
) -> CreatedRegisteredLauncher:
    _validate_closed_inputs(
        opened_launcher,
        handles,
        identity_json,
        lease_token,
        daemon_policy_json,
    )
    opened_launcher.validate_native_pe()

    job_handle = 0
    completion_port_handle = 0
    duplicate_handles: list[int] = []
    attribute_pointer = ctypes.c_void_p()
    attribute_initialized = False
    process_info = PROCESS_INFORMATION()
    private_directory_owner: tempfile.TemporaryDirectory[str] | None = None
    current_directory = ""
    transferred = False
    try:
        private_directory_owner = tempfile.TemporaryDirectory(
            prefix="dayz-mcp-native-"
        )
        current_directory = str(
            Path(private_directory_owner.name).resolve(strict=True)
        )
        if not Path(current_directory).is_dir():
            raise NativeLauncherBackendError("native_launcher_create_failed")

        job_handle = _handle_value(_kernel32.CreateJobObjectW(None, None))
        if not job_handle:
            raise NativeLauncherBackendError("native_launcher_create_failed")

        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not _kernel32.SetInformationJobObject(
            wintypes.HANDLE(job_handle),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise NativeLauncherBackendError("native_launcher_create_failed")

        invalid_handle = wintypes.HANDLE(-1)
        completion_port_handle = _handle_value(
            _kernel32.CreateIoCompletionPort(invalid_handle, None, 0, 1)
        )
        if not completion_port_handle:
            raise NativeLauncherBackendError("native_launcher_create_failed")
        association = JOBOBJECT_ASSOCIATE_COMPLETION_PORT()
        association.CompletionKey = ctypes.c_void_p(job_handle)
        association.CompletionPort = wintypes.HANDLE(completion_port_handle)
        if not _kernel32.SetInformationJobObject(
            wintypes.HANDLE(job_handle),
            _JOB_OBJECT_ASSOCIATE_COMPLETION_PORT_INFORMATION,
            ctypes.byref(association),
            ctypes.sizeof(association),
        ):
            raise NativeLauncherBackendError("native_launcher_create_failed")

        _duplicate_inheritable_handles(handles, duplicate_handles)
        attribute_size = _SIZE_T()
        _kernel32.InitializeProcThreadAttributeList(
            None, 2, 0, ctypes.byref(attribute_size)
        )
        if attribute_size.value <= 0:
            raise NativeLauncherBackendError("native_launcher_create_failed")
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        attribute_pointer = ctypes.cast(attribute_buffer, ctypes.c_void_p)
        if not _kernel32.InitializeProcThreadAttributeList(
            attribute_pointer, 2, 0, ctypes.byref(attribute_size)
        ):
            raise NativeLauncherBackendError("native_launcher_create_failed")
        attribute_initialized = True

        job_handles = (wintypes.HANDLE * 1)(job_handle)
        if not _kernel32.UpdateProcThreadAttribute(
            attribute_pointer,
            0,
            _PROC_THREAD_ATTRIBUTE_JOB_LIST,
            ctypes.cast(job_handles, ctypes.c_void_p),
            ctypes.sizeof(job_handles),
            None,
            None,
        ):
            raise NativeLauncherBackendError("native_launcher_create_failed")
        inherited_handles = (wintypes.HANDLE * 5)(*duplicate_handles)
        if not _kernel32.UpdateProcThreadAttribute(
            attribute_pointer,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(inherited_handles, ctypes.c_void_p),
            ctypes.sizeof(inherited_handles),
            None,
            None,
        ):
            raise NativeLauncherBackendError("native_launcher_create_failed")

        startup_info = STARTUPINFOEXW()
        startup_info.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        startup_info.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup_info.StartupInfo.hStdInput = wintypes.HANDLE(duplicate_handles[0])
        startup_info.StartupInfo.hStdOutput = wintypes.HANDLE(duplicate_handles[1])
        startup_info.StartupInfo.hStdError = wintypes.HANDLE(duplicate_handles[2])
        startup_info.lpAttributeList = attribute_pointer
        application_name = str(opened_launcher.path)
        command_line = ctypes.create_unicode_buffer(f'"{application_name}"')
        environment_block = _environment_buffer(
            identity_json=identity_json,
            lease_token=lease_token,
            cancel_handle=duplicate_handles[3],
            worker_cancel_handle=duplicate_handles[4],
            daemon_policy_json=daemon_policy_json,
        )
        try:
            created_process = _kernel32.CreateProcessW(
                application_name,
                command_line,
                None,
                None,
                True,
                CREATE_FLAGS,
                environment_block,
                current_directory,
                ctypes.byref(startup_info),
                ctypes.byref(process_info),
            )
        finally:
            ctypes.memset(
                ctypes.addressof(environment_block),
                0,
                ctypes.sizeof(environment_block),
            )
        if not created_process:
            raise NativeLauncherBackendError("native_launcher_create_failed")
        if (
            not _handle_value(process_info.hProcess)
            or not _handle_value(process_info.hThread)
            or int(process_info.dwProcessId) <= 0
            or int(process_info.dwThreadId) <= 0
        ):
            raise NativeLauncherBackendError("native_launcher_create_failed")

        created = CreatedRegisteredLauncher(
            job_handle,
            completion_port_handle,
            process_info,
            opened_launcher,
            Path(current_directory),
            private_directory_owner,
        )
        transferred = True
        return created
    except NativeLauncherBackendError:
        raise
    except BaseException as error:
        raise NativeLauncherBackendError("native_launcher_create_failed") from error
    finally:
        if attribute_initialized:
            _kernel32.DeleteProcThreadAttributeList(attribute_pointer)
        for duplicate in reversed(duplicate_handles):
            _close_handle(duplicate)
        if not transferred:
            _close_handle(job_handle)
            _close_handle(process_info.hThread)
            _close_handle(process_info.hProcess)
            _close_handle(completion_port_handle)
            if private_directory_owner is not None:
                private_directory_owner.cleanup()


class _EXCEPTION_RECORD(ctypes.Structure):
    pass


_EXCEPTION_RECORD._fields_ = [
    ("ExceptionCode", wintypes.DWORD),
    ("ExceptionFlags", wintypes.DWORD),
    ("ExceptionRecord", ctypes.POINTER(_EXCEPTION_RECORD)),
    ("ExceptionAddress", wintypes.LPVOID),
    ("NumberParameters", wintypes.DWORD),
    ("ExceptionInformation", _ULONG_PTR * 15),
]


class _EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("ExceptionRecord", _EXCEPTION_RECORD),
        ("dwFirstChance", wintypes.DWORD),
    ]


class _CREATE_THREAD_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("hThread", wintypes.HANDLE),
        ("lpThreadLocalBase", wintypes.LPVOID),
        ("lpStartAddress", wintypes.LPVOID),
    ]


class _CREATE_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("hFile", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("lpBaseOfImage", wintypes.LPVOID),
        ("dwDebugInfoFileOffset", wintypes.DWORD),
        ("nDebugInfoSize", wintypes.DWORD),
        ("lpThreadLocalBase", wintypes.LPVOID),
        ("lpStartAddress", wintypes.LPVOID),
        ("lpImageName", wintypes.LPVOID),
        ("fUnicode", wintypes.WORD),
    ]


class _EXIT_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("dwExitCode", wintypes.DWORD)]


class _LOAD_DLL_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("hFile", wintypes.HANDLE),
        ("lpBaseOfDll", wintypes.LPVOID),
        ("dwDebugInfoFileOffset", wintypes.DWORD),
        ("nDebugInfoSize", wintypes.DWORD),
        ("lpImageName", wintypes.LPVOID),
        ("fUnicode", wintypes.WORD),
    ]


class _DEBUG_EVENT_DATA(ctypes.Union):
    _fields_ = [
        ("Exception", _EXCEPTION_DEBUG_INFO),
        ("CreateThread", _CREATE_THREAD_DEBUG_INFO),
        ("CreateProcessInfo", _CREATE_PROCESS_DEBUG_INFO),
        ("ExitProcess", _EXIT_PROCESS_DEBUG_INFO),
        ("LoadDll", _LOAD_DLL_DEBUG_INFO),
        ("reserved", ctypes.c_ubyte * 164),
    ]


class _DEBUG_EVENT(ctypes.Structure):
    _fields_ = [
        ("dwDebugEventCode", wintypes.DWORD),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
        ("u", _DEBUG_EVENT_DATA),
    ]


_kernel32.WaitForDebugEvent.argtypes = [
    ctypes.POINTER(_DEBUG_EVENT),
    wintypes.DWORD,
]
_kernel32.WaitForDebugEvent.restype = wintypes.BOOL
_kernel32.GetFinalPathNameByHandleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
]
_kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD


def _decode_debug_event(raw: _DEBUG_EVENT) -> NativeDebugEvent:
    code = int(raw.dwDebugEventCode)
    pid = int(raw.dwProcessId)
    tid = int(raw.dwThreadId)
    if code == 1:
        item = raw.u.Exception
        return NativeDebugEvent(
            "EXCEPTION",
            pid=pid,
            tid=tid,
            exception_code=int(item.ExceptionRecord.ExceptionCode),
            first_chance=bool(item.dwFirstChance),
        )
    if code == 2:
        return NativeDebugEvent(
            "CREATE_THREAD",
            pid=pid,
            tid=tid,
            thread_handle=_handle_value(raw.u.CreateThread.hThread),
        )
    if code == 3:
        item = raw.u.CreateProcessInfo
        return NativeDebugEvent(
            "CREATE_PROCESS",
            pid=pid,
            tid=tid,
            process_handle=_handle_value(item.hProcess),
            thread_handle=_handle_value(item.hThread),
            file_handle=_handle_value(item.hFile),
        )
    if code == 4:
        return NativeDebugEvent("EXIT_THREAD", pid=pid, tid=tid)
    if code == 5:
        return NativeDebugEvent(
            "EXIT_PROCESS",
            pid=pid,
            tid=tid,
            exit_code=int(raw.u.ExitProcess.dwExitCode),
        )
    if code == 6:
        return NativeDebugEvent(
            "LOAD_DLL",
            pid=pid,
            tid=tid,
            file_handle=_handle_value(raw.u.LoadDll.hFile),
        )
    if code == 7:
        return NativeDebugEvent("UNLOAD_DLL", pid=pid, tid=tid)
    if code == 8:
        return NativeDebugEvent("OUTPUT_DEBUG_STRING", pid=pid, tid=tid)
    if code == 9:
        return NativeDebugEvent("RIP_EVENT", pid=pid, tid=tid)
    return NativeDebugEvent("UNKNOWN", pid=pid, tid=tid)


def _wait_debug_event() -> NativeDebugEvent | None:
    raw = _DEBUG_EVENT()
    outcome = _kernel32.WaitForDebugEvent(ctypes.byref(raw), _DEBUG_POLL_MS)
    if isinstance(outcome, NativeDebugEvent):
        return outcome
    if outcome:
        return _decode_debug_event(raw)
    if ctypes.get_last_error() == _ERROR_SEM_TIMEOUT:
        return None
    raise NativeLauncherBackendError("native_debug_wait_failed")


def _debug_image_path_from_handle(file_handle: int) -> str | None:
    """Best-effort final path for a debug event file handle.

    Returns None when the handle is missing or the host API cannot resolve a
    path reliably. Never invents a path.
    """
    if (
        not isinstance(file_handle, int)
        or isinstance(file_handle, bool)
        or file_handle <= 0
    ):
        return None
    # Resolved statically, NOT via getattr: security_runtime_audit flags any
    # dynamically-resolved call in the productive runtime closure as
    # `dynamic_http` (audit_runtime_http), because such a call cannot be checked
    # statically. The except below already covers the test fakes, which simply
    # do not carry this symbol and raise AttributeError.
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(
            _kernel32.GetFinalPathNameByHandleW(
                wintypes.HANDLE(file_handle), buffer, len(buffer), 0
            )
        )
        if length <= 0 or length >= len(buffer):
            return None
        value = buffer.value
        if not value:
            return None
        if value.startswith("\\\\?\\UNC\\"):
            return "\\\\" + value[8:]
        if value.startswith("\\\\?\\"):
            return value[4:]
        return value
    except Exception:
        return None


def _native_debug_gate_rejection(
    decision: NativeDebugDecision,
    event: NativeDebugEvent,
) -> NativeLauncherBackendError:
    """Build a gate rejection that keeps the stable code and local detail."""
    fine_code = decision.failure_code or "unknown"
    event_kind = event.kind
    pid = int(event.pid)
    image_path = _debug_image_path_from_handle(event.file_handle)
    parts = [fine_code, f"kind={event_kind}", f"pid={pid}"]
    if image_path is not None:
        parts.append(f"image={image_path}")
    return NativeLauncherBackendError(
        "native_debug_gate_rejected",
        " ".join(parts),
        fine_code=fine_code,
        event_kind=event_kind,
        pid=pid,
        image_path=image_path,
    )


class _PublicRequestWriter:
    def __init__(self, source_handle: int, request: bytes) -> None:
        if not isinstance(request, bytes) or len(request) > 64 * 1024:
            raise NativeLauncherBackendError("invalid_native_launcher_request")
        process = _kernel32.GetCurrentProcess()
        duplicate = wintypes.HANDLE()
        if not _kernel32.DuplicateHandle(
            process,
            wintypes.HANDLE(source_handle),
            process,
            ctypes.byref(duplicate),
            0,
            False,
            _DUPLICATE_SAME_ACCESS,
        ):
            raise NativeLauncherBackendError("native_launcher_request_write_failed")
        self._handle = _handle_value(duplicate)
        if not self._handle:
            raise NativeLauncherBackendError("native_launcher_request_write_failed")
        self._request = request
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._error: NativeLauncherBackendError | None = None
        self.deadline = time.monotonic() + _REQUEST_WRITE_SECONDS
        self._thread = threading.Thread(
            target=self._run,
            name="dayz-native-request-writer",
            daemon=True,
        )
        try:
            self._thread.start()
        except BaseException:
            _close_handle(self._handle)
            self._handle = 0
            raise NativeLauncherBackendError(
                "native_launcher_request_write_failed"
            ) from None

    def _run(self) -> None:
        error: NativeLauncherBackendError | None = None
        try:
            buffer = ctypes.create_string_buffer(self._request)
            written = wintypes.DWORD()
            if not _kernel32.WriteFile(
                wintypes.HANDLE(self._handle),
                buffer,
                len(self._request),
                ctypes.byref(written),
                None,
            ) or int(written.value) != len(self._request):
                error = NativeLauncherBackendError(
                    "native_launcher_request_write_failed"
                )
        except BaseException:
            error = NativeLauncherBackendError(
                "native_launcher_request_write_failed"
            )
        finally:
            with self._lock:
                self._error = error
                handle = self._handle
                self._handle = 0
                _close_handle(handle)
                self._done.set()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def error(self) -> NativeLauncherBackendError | None:
        with self._lock:
            return self._error

    def cancel(self) -> None:
        with self._lock:
            if self._handle and not self._done.is_set():
                _kernel32.CancelIoEx(wintypes.HANDLE(self._handle), None)

    def join(self, timeout: float) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()


def _drain_output(
    handle: int,
    *,
    channel: str,
    output_sink: Callable[[str, bytes], None] | None,
) -> None:
    available = wintypes.DWORD()
    if not _kernel32.PeekNamedPipe(
        wintypes.HANDLE(handle),
        None,
        0,
        None,
        ctypes.byref(available),
        None,
    ):
        return
    remaining = int(available.value)
    while remaining:
        size = min(remaining, 16 * 1024)
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if not _kernel32.ReadFile(
            wintypes.HANDLE(handle),
            buffer,
            size,
            ctypes.byref(read),
            None,
        ) or not read.value:
            return
        if output_sink is not None:
            output_sink(channel, bytes(buffer.raw[: int(read.value)]))
        remaining -= int(read.value)


def _read_job_completion(
    completion_port: int,
    *,
    timeout_ms: int,
) -> tuple[int, int, int] | None:
    code = wintypes.DWORD()
    key = _ULONG_PTR()
    overlapped = wintypes.LPVOID()
    outcome = _kernel32.GetQueuedCompletionStatus(
        wintypes.HANDLE(completion_port),
        ctypes.byref(code),
        ctypes.byref(key),
        ctypes.byref(overlapped),
        timeout_ms,
    )
    if isinstance(outcome, tuple):
        if len(outcome) not in {3, 4} or not bool(outcome[0]):
            return None
        pid = int(outcome[3]) if len(outcome) == 4 else 0
        return int(outcome[1]), int(outcome[2]), pid
    if not outcome:
        return None
    return int(code.value), int(key.value), int(overlapped.value or 0)


def _wait_job_new_process(
    completion_port: int,
    *,
    expected_job_handle: int,
    expected_pid: int,
    deadline: float,
) -> bool:
    while time.monotonic() < deadline:
        remaining_ms = max(0, min(_DEBUG_POLL_MS, int((deadline - time.monotonic()) * 1000)))
        completion = _read_job_completion(completion_port, timeout_ms=remaining_ms)
        if completion is None:
            continue
        message, completion_key, pid = completion
        if message == JOB_OBJECT_MSG_NEW_PROCESS:
            return completion_key == expected_job_handle and pid == expected_pid
        if message == JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO:
            return False
    return False


def _wait_active_zero(
    completion_port: int,
    *,
    expected_job_handle: int,
    deadline: float,
) -> bool:
    while time.monotonic() < deadline:
        completion = _read_job_completion(
            completion_port,
            timeout_ms=_DEBUG_POLL_MS,
        )
        if completion is None:
            continue
        message, completion_key, _pid = completion
        if (
            message == JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO
            and completion_key == expected_job_handle
        ):
            return True
    return False


def _complete_debug_event(
    state: NativeDebugState,
    event: NativeDebugEvent,
    decision: NativeDebugDecision,
    *,
    creator_thread_id: int,
) -> None:
    for owned_handle in decision.close_handles:
        _close_handle(owned_handle)
    if decision.close_handles:
        state.acknowledge_closed_handles(decision.close_handles)
    continued = bool(
        _kernel32.ContinueDebugEvent(
            event.pid,
            event.tid,
            decision.continue_status,
        )
    )
    state.complete_continue(
        current_thread_id=creator_thread_id,
        succeeded=continued,
    )
    if not continued:
        raise NativeLauncherBackendError("native_debug_continue_failed")


def _drain_after_job_close(
    state: NativeDebugState,
    *,
    deadline: float,
    creator_thread_id: int,
) -> None:
    while not state.active_zero and time.monotonic() < deadline:
        event = _wait_debug_event()
        if event is None:
            continue
        decision = state.begin_event(
            event,
            current_thread_id=creator_thread_id,
        )
        _complete_debug_event(
            state,
            event,
            decision,
            creator_thread_id=creator_thread_id,
        )


def _supervise_created_launcher(
    created: CreatedRegisteredLauncher,
    *,
    canonical_request: bytes,
    runtime_pipes: NativeRuntimePipes,
    image_authority: _DebugImageAuthority,
    cancel_signal: threading.Event,
    output_sink: Callable[[str, bytes], None] | None = None,
) -> int:
    start_deadline = time.monotonic() + _LAUNCHER_START_SECONDS
    request_write_handle = runtime_pipes.stdin_write
    output_read_handles = (runtime_pipes.stdout_read, runtime_pipes.stderr_read)
    root_pid = int(created.process_information.dwProcessId)
    job_completion_key = created.job_handle
    creator_thread_id = threading.get_ident()
    state = NativeDebugState(creator_thread_id=creator_thread_id)
    announcement_decoder = ChildAnnouncementDecoder()
    announcements: deque[tuple[ChildAnnouncement, float]] = deque()
    root_exit_code: int | None = None
    started = False
    request_written = False
    request_writer: _PublicRequestWriter | None = None
    request_writer_checked = False
    failure: str | None = None
    gate_rejection: NativeLauncherBackendError | None = None
    drain_deadline: float | None = None
    descendant_started = False
    addon_builder_pids: set[int] = set()
    addon_helper_pids: set[int] = set()
    addon_helper_launches = 0
    cleanup_complete = False

    def receive_announcement(_channel: str, chunk: bytes) -> None:
        received_at = time.monotonic()
        announcements.extend(
            (announcement, received_at)
            for announcement in announcement_decoder.feed(chunk)
        )

    def drain_announcements() -> None:
        _drain_output(
            output_read_handles[1],
            channel="announcement",
            output_sink=receive_announcement,
        )

    def drain_outputs() -> None:
        _drain_output(
            output_read_handles[0],
            channel="stdout",
            output_sink=output_sink,
        )
        drain_announcements()

    def check_request_writer(now: float) -> None:
        nonlocal failure, request_written, request_writer_checked, drain_deadline
        if request_writer is None or request_writer_checked:
            return
        if not request_writer.done:
            if failure is None and now >= request_writer.deadline:
                failure = "native_launcher_request_write_timeout"
                request_writer.cancel()
                created.close_job()
                drain_deadline = now + _DEBUG_DRAIN_SECONDS
            return
        request_writer_checked = True
        if request_writer.error is None:
            request_written = True
        elif failure is None:
            failure = "native_launcher_request_write_failed"
            created.close_job()
            drain_deadline = now + _DEBUG_DRAIN_SECONDS

    def check_start_watchdog(now: float) -> None:
        nonlocal failure, drain_deadline
        if (
            failure is None
            and not descendant_started
            and now >= start_deadline
        ):
            failure = "native_launcher_start_timeout"
            created.close_job()
            drain_deadline = now + _DEBUG_DRAIN_SECONDS

    def finish_request_writer() -> None:
        nonlocal failure
        if request_writer is None:
            return
        if not request_writer.done:
            request_writer.cancel()
        if not request_writer.join(_DEBUG_DRAIN_SECONDS):
            created.close_job()
            request_writer.cancel()
            if not request_writer.join(_DEBUG_DRAIN_SECONDS):
                failure = "native_launcher_request_writer_stuck"
                return
        check_request_writer(time.monotonic())

    def take_announcement(
        *, event_received_at: float, deadline: float
    ) -> tuple[ChildAnnouncement | None, bool]:
        while not announcements and time.monotonic() < deadline:
            drain_announcements()
            if not announcements:
                time.sleep(0.001)
        if not announcements:
            return None, not announcement_decoder.has_pending_frame
        announcement, received_at = announcements[0]
        if received_at < event_received_at - _CHILD_CORRELATION_SECONDS:
            return None, False
        announcements.popleft()
        return announcement, True

    try:
        while True:
            if cancel_signal.is_set() and failure is None:
                failure = "native_launcher_cancelled"
                runtime_pipes.close_cancel_writer()
                if request_writer is not None:
                    request_writer.cancel()
                if started:
                    drain_deadline = time.monotonic() + _CANCEL_GRACE_SECONDS
                else:
                    created.close_job()
                    drain_deadline = time.monotonic() + _DEBUG_DRAIN_SECONDS
            now = time.monotonic()
            check_request_writer(now)
            event = _wait_debug_event()
            if event is None:
                drain_outputs()
                now = time.monotonic()
                check_request_writer(now)
                check_start_watchdog(now)
                if (descendant_started or failure is not None) and state.active_zero:
                    break
                if drain_deadline is not None and time.monotonic() >= drain_deadline:
                    if failure == "native_launcher_cancelled" and created.job_handle:
                        created.close_job()
                        drain_deadline = time.monotonic() + _DEBUG_DRAIN_SECONDS
                        continue
                    break
                continue
            event_received_at = time.monotonic()
            if not (event.kind == "EXIT_PROCESS" and event.pid == root_pid):
                check_start_watchdog(event_received_at)
            if event.kind == "CREATE_PROCESS" and event.pid != root_pid:
                descendant_started = True
            drain_announcements()
            accepted_direct_kind: BrokerKind | None = None
            accepted_addon_helper = False
            if (
                event.kind == "CREATE_PROCESS"
                and event.pid == root_pid
                and not started
            ):
                job_approved = _wait_job_new_process(
                    created.completion_port_handle,
                    expected_job_handle=job_completion_key,
                    expected_pid=event.pid,
                    deadline=event_received_at + _CHILD_CORRELATION_SECONDS,
                )
                try:
                    image_approved = created._root_image_authority.approve_root_debug_image(
                        event.file_handle
                    )
                except BaseException:
                    image_approved = False
                event = replace(
                    event,
                    image_approved=job_approved is True and image_approved is True,
                )
            elif event.kind == "LOAD_DLL":
                try:
                    approved = image_authority.approve_debug_image(
                        event.file_handle,
                        event_kind=event.kind,
                    )
                except BaseException:
                    approved = False
                event = replace(event, image_approved=approved is True)
            elif event.kind == "CREATE_PROCESS":
                deadline = event_received_at + _CHILD_CORRELATION_SECONDS
                job_approved = _wait_job_new_process(
                    created.completion_port_handle,
                    expected_job_handle=job_completion_key,
                    expected_pid=event.pid,
                    deadline=deadline,
                )
                announcement, announcement_clear = take_announcement(
                    event_received_at=event_received_at,
                    deadline=deadline,
                )
                try:
                    if not announcement_clear:
                        approved = False
                    elif announcement is not None:
                        approved = (
                            job_approved
                            and not (
                                announcement.kind == BrokerKind.ADDON_BUILDER
                                and addon_builder_pids
                            )
                            and image_authority.approve_announced_process(
                                event.file_handle,
                                announcement,
                            )
                        )
                        if approved:
                            accepted_direct_kind = announcement.kind
                    else:
                        approved = (
                            job_approved
                            and len(addon_builder_pids) == 1
                            and not addon_helper_pids
                            and addon_helper_launches < _MAX_ADDON_HELPER_LAUNCHES
                            and image_authority.approve_addon_helper_process(
                                event.file_handle
                            )
                        )
                        accepted_addon_helper = approved is True
                except BaseException:
                    approved = False
                event = replace(event, image_approved=approved is True)
            decision = state.begin_event(
                event,
                current_thread_id=creator_thread_id,
            )
            if decision.close_job_first:
                if failure is None:
                    failure = "native_debug_gate_rejected"
                    # Resolve image path while the event file handle is still open
                    # (handles close inside _complete_debug_event below).
                    # Guarded: this is observability, and it runs BEFORE
                    # close_job(). Letting it raise would skip closing the job and
                    # strand the process tree -- worse than losing the detail.
                    try:
                        gate_rejection = _native_debug_gate_rejection(decision, event)
                    except Exception:
                        gate_rejection = None
                created.close_job()
                drain_deadline = time.monotonic() + _DEBUG_DRAIN_SECONDS
            _complete_debug_event(
                state,
                event,
                decision,
                creator_thread_id=creator_thread_id,
            )
            if not decision.close_job_first and decision.failure_code is None:
                if accepted_direct_kind == BrokerKind.ADDON_BUILDER:
                    addon_builder_pids.add(event.pid)
                elif accepted_addon_helper:
                    addon_helper_pids.add(event.pid)
                    addon_helper_launches += 1
            drain_outputs()
            if event.kind == "EXIT_PROCESS" and event.pid == root_pid:
                root_exit_code = event.exit_code
            if event.kind == "EXIT_PROCESS":
                addon_builder_pids.discard(event.pid)
                addon_helper_pids.discard(event.pid)
            if (
                event.kind == "CREATE_PROCESS"
                and event.pid == root_pid
                and not started
                and failure is None
            ):
                if int(_kernel32.ResumeThread(created.process_information.hThread)) == 0xFFFFFFFF:
                    raise NativeLauncherBackendError("native_launcher_resume_failed")
                started = True
                request_writer = _PublicRequestWriter(
                    request_write_handle, canonical_request
                )
            check_request_writer(time.monotonic())
            if (
                state.active_zero
                and started
                and not descendant_started
                and failure is None
                and root_exit_code is not None
            ):
                failure = (
                    "native_launcher_root_exit_before_descendant:"
                    f"{int(root_exit_code)}"
                )
            if (descendant_started or failure is not None) and state.active_zero:
                break
        finish_request_writer()
        try:
            announcement_decoder.finish()
        except ValueError:
            if failure is None:
                failure = "native_child_announcement_incomplete"
                created.close_job()
        if announcements and failure is None:
            failure = "native_child_announcement_unmatched"
            created.close_job()
        active_zero_completed = False
        if state.active_zero:
            active_zero_completed = _wait_active_zero(
                created.completion_port_handle,
                expected_job_handle=job_completion_key,
                deadline=time.monotonic() + _DEBUG_DRAIN_SECONDS,
            )
        created.close_job()
        _drain_after_job_close(
            state,
            deadline=time.monotonic() + _DEBUG_DRAIN_SECONDS,
            creator_thread_id=creator_thread_id,
        )
        if not active_zero_completed:
            active_zero_completed = _wait_active_zero(
                created.completion_port_handle,
                expected_job_handle=job_completion_key,
                deadline=time.monotonic() + _DEBUG_DRAIN_SECONDS,
            )
        cleanup_complete = state.active_zero and active_zero_completed
        if not cleanup_complete:
            failure = "native_job_cleanup_incomplete"
        if failure == "native_launcher_cancelled":
            return 130
        if failure is not None:
            if (
                failure == "native_debug_gate_rejected"
                and gate_rejection is not None
            ):
                raise gate_rejection
            raise NativeLauncherBackendError(failure)
        if not request_written or root_exit_code is None:
            raise NativeLauncherBackendError("native_debug_incomplete")
        return int(root_exit_code) & 0xFF
    except BaseException:
        if not cleanup_complete:
            created.close_job()
            deadline = time.monotonic() + _DEBUG_DRAIN_SECONDS
            _drain_after_job_close(
                state,
                deadline=deadline,
                creator_thread_id=creator_thread_id,
            )
            _wait_active_zero(
                created.completion_port_handle,
                expected_job_handle=job_completion_key,
                deadline=deadline,
            )
        raise
    finally:
        if request_writer is not None and not request_writer.done:
            request_writer.cancel()
            request_writer.join(_DEBUG_DRAIN_SECONDS)
        created.close_tree()
        runtime_pipes.close()


@dataclass(slots=True)
class _NativeThreadOutcome:
    value: int | None = None
    error: BaseException | None = None


async def _await_native_thread_completion(
    thread: threading.Thread,
    *,
    cancel_signal: threading.Event,
    outcome: _NativeThreadOutcome,
    external_cancel_event: asyncio.Event,
) -> int:
    delayed_cancel: asyncio.CancelledError | None = None
    while thread.is_alive():
        if external_cancel_event.is_set():
            cancel_signal.set()
        try:
            await asyncio.sleep(0.01)
        except asyncio.CancelledError as error:
            delayed_cancel = error
            cancel_signal.set()
    thread.join()
    if delayed_cancel is not None:
        raise delayed_cancel
    if outcome.error is not None:
        raise NativeLauncherBackendError("native_launcher_thread_failed")
    if type(outcome.value) is not int or not 0 <= outcome.value <= 255:
        raise NativeLauncherBackendError("native_launcher_thread_failed")
    return outcome.value


async def launch_registered_native(
    opened_launcher: _PinnedLauncher,
    *,
    verified_bundle: object,
    identity_json: str,
    lease_token: str,
    daemon_policy_json: str,
    canonical_request: bytes,
    cancel_event: asyncio.Event,
    output_sink: Callable[[str, bytes], None] | None = None,
) -> int:
    """Run Create/Wait/Continue on one thread and join it before returning."""
    image_authority = getattr(verified_bundle, "debug_image_authority", None)
    if (
        not isinstance(cancel_event, asyncio.Event)
        or not isinstance(canonical_request, bytes)
        or len(canonical_request) > 64 * 1024
        or not isinstance(lease_token, str)
        or not isinstance(identity_json, str)
        or not isinstance(daemon_policy_json, str)
        or output_sink is not None
        and not callable(output_sink)
        or lease_token.encode("utf-8") in canonical_request
        or identity_json.encode("utf-8") in canonical_request
        or not callable(getattr(image_authority, "approve_debug_image", None))
        or not callable(getattr(image_authority, "approve_announced_process", None))
        or not callable(getattr(image_authority, "approve_addon_helper_process", None))
    ):
        raise NativeLauncherBackendError("invalid_native_launcher_runtime")
    loop = asyncio.get_running_loop()
    result: asyncio.Future[int] = loop.create_future()
    cancel_signal = threading.Event()

    def worker() -> None:
        pipes: NativeRuntimePipes | None = None
        try:
            pipes = _create_runtime_pipes()
            created = _create_registered_launcher(
                opened_launcher,
                handles=pipes.child_handles(),
                identity_json=identity_json,
                lease_token=lease_token,
                daemon_policy_json=daemon_policy_json,
            )
            value = _supervise_created_launcher(
                created,
                canonical_request=canonical_request,
                runtime_pipes=pipes,
                image_authority=image_authority,
                cancel_signal=cancel_signal,
                output_sink=output_sink,
            )
        except NativeLauncherBackendError as error:
            completion = (_set_future_error, error)
        except BaseException:
            completion = (
                _set_future_error,
                NativeLauncherBackendError("native_launcher_thread_failed"),
            )
        else:
            completion = (_set_future_value, value)
        finally:
            cleanup_failed = False
            try:
                if pipes is not None:
                    pipes.close()
            except BaseException:
                cleanup_failed = True
            if cleanup_failed and completion[0] is _set_future_value:
                completion = (
                    _set_future_error,
                    NativeLauncherBackendError("native_launcher_cleanup_failed"),
                )
        loop.call_soon_threadsafe(completion[0], result, completion[1])

    thread = threading.Thread(
        target=worker, name="dayz-native-launcher", daemon=False
    )
    thread.start()
    watcher = asyncio.create_task(cancel_event.wait())
    delayed_cancel: asyncio.CancelledError | None = None
    try:
        done, _pending = await asyncio.wait(
            (result, watcher), return_when=asyncio.FIRST_COMPLETED
        )
        if watcher in done and watcher.result() and not result.done():
            cancel_signal.set()
        while not result.done():
            try:
                await asyncio.shield(result)
            except asyncio.CancelledError as error:
                delayed_cancel = error
                cancel_signal.set()
        value = result.result()
    except asyncio.CancelledError as error:
        delayed_cancel = error
        cancel_signal.set()
        while not result.done():
            try:
                await asyncio.shield(result)
            except asyncio.CancelledError:
                continue
        value = result.result()
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        thread.join()
    if delayed_cancel is not None:
        raise delayed_cancel
    return value


def _set_future_value(future: asyncio.Future[int], value: int) -> None:
    if not future.done():
        future.set_result(value)


def _set_future_error(
    future: asyncio.Future[int], error: BaseException
) -> None:
    if not future.done():
        future.set_exception(error)
