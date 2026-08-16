from __future__ import annotations

import errno
import ctypes
import hashlib
import json
import math
import ntpath
import os
import socket
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterator, Mapping

try:
    import msvcrt
except ImportError:  # pragma: no cover - P0.S is Windows-only.
    msvcrt = None  # type: ignore[assignment]

if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _GENERIC_READ = 0x80000000
    _DELETE_ACCESS = 0x00010000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_BEGIN = 0
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    _FILE_ID_INFO_CLASS = 18
    _FILE_DISPOSITION_INFO_CLASS = 4
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", ctypes.c_ubyte * 16),
        ]

    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [("DeleteFile", ctypes.c_ubyte)]

    _kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.SetFilePointerEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    )
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL

try:
    import psutil
except ImportError:  # pragma: no cover - covered by the fail-closed runtime path.
    psutil = None  # type: ignore[assignment]

from dayz_mcp.native_process_guard import NativeProcessGuard, identity_hashes
from dayz_mcp.orphan_guard import image_name_of
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server_cli import parse_server_tail_silent


MIGRATION_DIR = Path(
    r"P:\DayZ_MCP_dev\reports\security\migration\P0S-IDENTITY-V2"
)
BACKUP_NAME = "runs.pre-v2.json"
RECEIPT_NAME = "runs-backup-receipt.json"
_LOCK_NAME = ".runs-v1.lock"
_STARTUP_LOCK_NAME = ".daemon-startup.lock"
_TRANSACTION_NAME = "runs-backup-transaction.json"
_TRANSACTION_NEXT_NAME = "runs-backup-transaction.next"
_PENDING_RECEIPT_NAME = "runs-backup-receipt.pending"
_RECEIPT_KIND = "dayz-mcp-runs-backup-receipt-v1"
_TRANSACTION_KIND = "dayz-mcp-runs-backup-transaction-v1"
_TRANSACTION_PHASES = {"prepared", "backup_written", "receipt_pending"}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_NAME_SURROGATE_BIT = 0x20000000
_IDENTITY_FIELDS = (
    "pid",
    "creation_time_utc",
    "executable_sha256",
    "command_line_sha256",
    "identity_scheme",
)


class RunsBackupGateError(RuntimeError):
    pass


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(str(Path(path))))


def _metadata(payload: bytes) -> dict[str, object]:
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest().upper(),
    }


def _closed_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate_json_key")
        value[name] = item
    return value


def _read_file(path: Path, error_code: str) -> bytes:
    try:
        _assert_regular_path(path, error_code)
        return path.read_bytes()
    except RunsBackupGateError:
        raise
    except OSError as error:
        raise RunsBackupGateError(error_code) from error


def _exclusive_write(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise RunsBackupGateError("runs_backup_create_failed") from error
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short_write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as error:
        raise RunsBackupGateError("runs_backup_write_failed") from error
    finally:
        os.close(descriptor)


def _is_reparse_stat(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_regular_path(path: Path, error_code: str) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as error:
        raise RunsBackupGateError(error_code) from error
    if not stat.S_ISREG(value.st_mode) or _is_reparse_stat(value):
        raise RunsBackupGateError(error_code)
    return value


def _assert_no_name_surrogates(path: Path, error_code: str) -> None:
    absolute = _absolute(path)
    parts = absolute.parts
    if not parts:
        raise RunsBackupGateError(error_code)
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            value = os.stat(current, follow_symlinks=False)
        except OSError as error:
            raise RunsBackupGateError(error_code) from error
        tag = int(getattr(value, "st_reparse_tag", 0) or 0)
        if tag & _NAME_SURROGATE_BIT:
            raise RunsBackupGateError(error_code)


def _read_pinned_handle(handle: object) -> bytes:
    if os.name != "nt":
        raise RunsBackupGateError("runs_backup_recovery_conflict")
    if not _kernel32.SetFilePointerEx(handle, 0, None, _FILE_BEGIN):
        raise RunsBackupGateError("runs_backup_recovery_failed")
    chunks: list[bytes] = []
    while True:
        buffer = ctypes.create_string_buffer(64 * 1024)
        received = wintypes.DWORD()
        if not _kernel32.ReadFile(
            handle, buffer, len(buffer), ctypes.byref(received), None
        ):
            raise RunsBackupGateError("runs_backup_recovery_failed")
        if received.value == 0:
            return b"".join(chunks)
        chunks.append(buffer.raw[: received.value])


def _pinned_identity(handle: object) -> tuple[int, bytes]:
    if os.name != "nt":
        raise RunsBackupGateError("runs_backup_recovery_conflict")
    identity = _FILE_ID_INFO()
    if not _kernel32.GetFileInformationByHandleEx(
        handle,
        _FILE_ID_INFO_CLASS,
        ctypes.byref(identity),
        ctypes.sizeof(identity),
    ):
        raise RunsBackupGateError("runs_backup_recovery_failed")
    return int(identity.VolumeSerialNumber), bytes(identity.FileId)


def _matches_owned_bytes(
    actual: bytes, expected: tuple[bytes, ...], allow_prefix: bool
) -> bool:
    if allow_prefix:
        return any(candidate.startswith(actual) for candidate in expected)
    return actual in expected


def _safe_unlink_owned(
    path: Path,
    expected: bytes | tuple[bytes, ...],
    *,
    allow_prefix: bool = False,
    fault_injector: Callable[[str], None] | None = None,
    phase_name: str,
) -> None:
    if not os.path.lexists(path):
        return
    if fault_injector is not None:
        fault_injector(f"{phase_name}_before_unlink_open")
    if not os.path.lexists(path):
        return
    if os.name != "nt":
        raise RunsBackupGateError("runs_backup_recovery_conflict")
    candidates = expected if isinstance(expected, tuple) else (expected,)
    handle = _kernel32.CreateFileW(
        str(_absolute(path)),
        _GENERIC_READ | _DELETE_ACCESS,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if error in {2, 3} and not os.path.lexists(path):
            return
        raise RunsBackupGateError("runs_backup_recovery_conflict")
    delete_armed = False
    close_failed = False
    try:
        attributes = _FILE_ATTRIBUTE_TAG_INFO()
        if not _kernel32.GetFileInformationByHandleEx(
            handle,
            _FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(attributes),
            ctypes.sizeof(attributes),
        ):
            raise RunsBackupGateError("runs_backup_recovery_failed")
        if int(attributes.FileAttributes) & 0x400:
            raise RunsBackupGateError("runs_backup_recovery_conflict")
        identity = _pinned_identity(handle)
        actual = _read_pinned_handle(handle)
        if not _matches_owned_bytes(actual, candidates, allow_prefix):
            raise RunsBackupGateError("runs_backup_recovery_conflict")
        if fault_injector is not None:
            fault_injector(f"{phase_name}_unlink_pinned")
        if _pinned_identity(handle) != identity:
            raise RunsBackupGateError("runs_backup_recovery_conflict")
        if _read_pinned_handle(handle) != actual:
            raise RunsBackupGateError("runs_backup_recovery_conflict")
        disposition = _FILE_DISPOSITION_INFO(1)
        if not _kernel32.SetFileInformationByHandle(
            handle,
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise RunsBackupGateError("runs_backup_recovery_failed")
        delete_armed = True
    finally:
        if not _kernel32.CloseHandle(handle):
            close_failed = True
    if close_failed or not delete_armed or os.path.lexists(path):
        raise RunsBackupGateError("runs_backup_recovery_failed")


@contextmanager
def daemon_startup_election(paths: RuntimePaths) -> Iterator[bool]:
    """Elect exactly one daemon startup candidate for a runtime root.

    The lock file is intentionally persistent: ownership is the held byte-range
    lock, never the file's existence.  Every candidate must re-probe daemon health
    after entering this gate.
    """
    if not isinstance(paths, RuntimePaths):
        raise RunsBackupGateError("invalid_runtime_paths")
    if msvcrt is None:
        raise RunsBackupGateError("daemon_startup_lock_unavailable")
    try:
        paths.root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RunsBackupGateError("daemon_startup_lock_unavailable") from error
    root = _absolute(paths.root)
    _assert_no_name_surrogates(root, "daemon_startup_lock_unavailable")
    lock_path = root / _STARTUP_LOCK_NAME
    if os.path.lexists(lock_path):
        _assert_regular_path(lock_path, "daemon_startup_lock_unavailable")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor: int | None = None
    acquired = False
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _is_reparse_stat(opened):
            raise RunsBackupGateError("daemon_startup_lock_unavailable")
        named = _assert_regular_path(lock_path, "daemon_startup_lock_unavailable")
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise RunsBackupGateError("daemon_startup_lock_unavailable")
        if opened.st_size == 0:
            if os.write(descriptor, b"\0") != 1:
                raise OSError("short_lock_write")
            os.fsync(descriptor)
            if os.fstat(descriptor).st_size < 1:
                raise OSError("short_lock_write")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            acquired = True
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                yield False
                return
            raise RunsBackupGateError("daemon_startup_lock_unavailable") from error
        named = _assert_regular_path(lock_path, "daemon_startup_lock_unavailable")
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise RunsBackupGateError("daemon_startup_lock_unavailable")
        yield True
    except RunsBackupGateError:
        raise
    except OSError as error:
        raise RunsBackupGateError("daemon_startup_lock_unavailable") from error
    finally:
        if descriptor is not None:
            try:
                if acquired:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)


@contextmanager
def _exclusive_gate_lock(lock_path: Path) -> Iterator[None]:
    if msvcrt is None:
        raise RunsBackupGateError("runs_backup_lock_unavailable")
    _assert_no_name_surrogates(lock_path.parent, "runs_backup_lock_unavailable")
    if os.path.lexists(lock_path):
        _assert_regular_path(lock_path, "runs_backup_lock_unavailable")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor: int | None = None
    acquired = False
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _is_reparse_stat(opened):
            raise RunsBackupGateError("runs_backup_lock_unavailable")
        named = _assert_regular_path(lock_path, "runs_backup_lock_unavailable")
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise RunsBackupGateError("runs_backup_lock_unavailable")
        if opened.st_size == 0:
            if os.write(descriptor, b"\0") != 1:
                raise OSError("short_lock_write")
            os.fsync(descriptor)
            if os.fstat(descriptor).st_size < 1:
                raise OSError("short_lock_write")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        acquired = True
        named = _assert_regular_path(lock_path, "runs_backup_lock_unavailable")
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise RunsBackupGateError("runs_backup_lock_unavailable")
    except RunsBackupGateError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        try:
            if descriptor is not None:
                os.close(descriptor)
        except OSError:
            pass
        raise RunsBackupGateError("runs_backup_lock_unavailable") from error
    try:
        yield
    finally:
        try:
            if acquired:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


def _python_image(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    basename = ntpath.basename(value).casefold()
    return basename.startswith("python") and basename.endswith(".exe")


def _dayz_mcp_tail_is_writer(tail: list[str]) -> bool:
    result = parse_server_tail_silent(tail)
    if result.status == "terminal":
        return False
    if result.status != "parsed" or result.namespace is None:
        return True
    return result.namespace.mode != "client"


def _argv_targets_dayz_mcp(argv: list[str]) -> bool:
    if len(argv) < 2:
        return False
    no_value_options = frozenset("bBdEiIOPqRsSuvx")
    terminal_short_options = frozenset("h?V")
    terminal_long_options = {
        "--help",
        "--help-env",
        "--help-xoptions",
        "--help-all",
        "--version",
    }
    hash_modes = {"always", "default", "never"}
    index = 1
    target_kind: str | None = None
    target_value: str | None = None
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            index += 1
            if index >= len(argv):
                return False
            target_kind = "script"
            target_value = argv[index]
            index += 1
            break
        if argument == "--check-hash-based-pycs":
            if index + 1 >= len(argv) or argv[index + 1] not in hash_modes:
                return False
            index += 2
            continue
        if argument in terminal_long_options:
            return False
        if argument.startswith("--"):
            return False
        if argument.startswith("-") and argument != "-":
            compact = argument[1:]
            position = 0
            consumed_value = False
            while position < len(compact):
                option = compact[position]
                if option in no_value_options:
                    position += 1
                    continue
                if option in terminal_short_options:
                    return False
                if option == "c":
                    if position + 1 == len(compact) and index + 1 >= len(argv):
                        return False
                    return False
                if option == "m":
                    if position + 1 < len(compact):
                        target_value = compact[position + 1 :]
                        index += 1
                    else:
                        if index + 1 >= len(argv):
                            return False
                        target_value = argv[index + 1]
                        index += 2
                    target_kind = "module"
                    break
                if option in {"W", "X"}:
                    if position + 1 == len(compact):
                        if index + 1 >= len(argv):
                            return False
                        index += 2
                    else:
                        index += 1
                    consumed_value = True
                    break
                return False
            if target_kind is not None:
                break
            if consumed_value:
                continue
            index += 1
            continue
        target_kind = "script"
        target_value = argument
        index += 1
        break

    if target_kind == "module" and target_value == "dayz_mcp":
        return _dayz_mcp_tail_is_writer(argv[index:])
    if target_kind == "module" and target_value == "dayz_mcp.__main__":
        return True
    if target_kind != "script" or target_value is None:
        return False
    basename = ntpath.basename(target_value).casefold()
    if basename == "p0s_daemon_bootstrap.py" and ntpath.isabs(target_value):
        return True
    if (
        basename == "__main__.py"
        and ntpath.basename(ntpath.dirname(target_value)).casefold() == "dayz_mcp"
    ):
        return True
    return False


def _valid_allowed_identity(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    return (
        isinstance(value.get("pid"), int)
        and not isinstance(value.get("pid"), bool)
        and value.get("pid", 0) > 0
        and value.get("identity_scheme") == "psutil-argv-v2"
        and value.get("identity_complete") is True
        and all(field in value for field in _IDENTITY_FIELDS)
    )


def _identity_matches(expected: Mapping[str, object], actual: Mapping[str, object]) -> bool:
    return actual.get("identity_complete") is True and all(
        actual.get(field) == expected.get(field) for field in _IDENTITY_FIELDS
    )


def _is_process_error(module: object, error: BaseException, name: str) -> bool:
    error_type = getattr(module, name, None)
    return isinstance(error_type, type) and isinstance(error, error_type)


def capture_launch_ancestor_identity(
    current_identity: Mapping[str, object],
    approved_executable: Path,
    *,
    psutil_module: object | None = None,
    guard: object | None = None,
) -> dict[str, object] | None:
    """Capture the Windows venv redirector that owns the current Python child.

    It is admissible only as the immediate parent, with the exact same argv hash,
    an approved executable path and a complete strong identity.  Ordinary shells,
    unrelated daemons and ambiguous parents are never admitted.
    """
    if not _valid_allowed_identity(current_identity):
        raise RunsBackupGateError("invalid_allowed_process_identity")
    module = psutil if psutil_module is None else psutil_module
    if module is None:
        return None
    native_guard = guard or NativeProcessGuard()
    try:
        current_process = module.Process(os.getpid())
        parent_pid = current_process.ppid()
        if not isinstance(parent_pid, int) or isinstance(parent_pid, bool) or parent_pid <= 0:
            return None
        parent_process = module.Process(parent_pid)
        with parent_process.oneshot():
            observed_pid = parent_process.pid
            creation_time = parent_process.create_time()
            executable = parent_process.exe()
            argv = parent_process.cmdline()
        if (
            observed_pid != parent_pid
            or not isinstance(creation_time, (int, float))
            or isinstance(creation_time, bool)
            or not math.isfinite(creation_time)
            or creation_time <= 0
            or not isinstance(executable, str)
            or not ntpath.isabs(executable)
            or not isinstance(argv, list)
            or not argv
            or any(not isinstance(argument, str) or not argument for argument in argv)
            or not _argv_targets_dayz_mcp(argv)
            or os.path.normcase(str(Path(executable).resolve(strict=True)))
            != os.path.normcase(str(Path(approved_executable).resolve(strict=True)))
        ):
            return None
        parent_created = datetime.fromtimestamp(creation_time, UTC)
        current_created_value = current_identity.get("creation_time_utc")
        if not isinstance(current_created_value, str):
            return None
        current_created = datetime.fromisoformat(
            current_created_value.replace("Z", "+00:00")
        )
        if parent_created >= current_created:
            return None
        hashes = identity_hashes(executable, argv)
        expected_parent_identity: dict[str, object] = {
            "pid": parent_pid,
            "creation_time_utc": parent_created.isoformat(
                timespec="microseconds"
            ).replace("+00:00", "Z"),
            "executable_sha256": hashes["executable_sha256"],
            "command_line_sha256": hashes["command_line_sha256"],
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }
        parent_identity = native_guard.snapshot(parent_pid)
        if module.Process(os.getpid()).ppid() != parent_pid:
            return None
    except Exception:
        return None
    if (
        not isinstance(parent_identity, dict)
        or not _valid_allowed_identity(parent_identity)
        or not _identity_matches(expected_parent_identity, parent_identity)
        or parent_identity.get("command_line_sha256")
        != current_identity.get("command_line_sha256")
    ):
        return None
    return parent_identity


def scan_dayz_mcp_processes(
    allowed_current_identity: Mapping[str, object] | None = None,
    allowed_launch_ancestor_identity: Mapping[str, object] | None = None,
    *,
    psutil_module: object | None = None,
    guard: object | None = None,
    image_name_fn: Callable[[int], str | None] = image_name_of,
) -> tuple[int, ...]:
    module = psutil if psutil_module is None else psutil_module
    if module is None:
        raise RunsBackupGateError("psutil_unavailable")
    if allowed_current_identity is not None and not _valid_allowed_identity(
        allowed_current_identity
    ):
        raise RunsBackupGateError("invalid_allowed_process_identity")
    if allowed_launch_ancestor_identity is not None and (
        not _valid_allowed_identity(allowed_launch_ancestor_identity)
        or allowed_current_identity is None
        or allowed_launch_ancestor_identity.get("pid")
        == allowed_current_identity.get("pid")
        or allowed_launch_ancestor_identity.get("command_line_sha256")
        != allowed_current_identity.get("command_line_sha256")
    ):
        raise RunsBackupGateError("invalid_allowed_process_identity")
    native_guard = guard or NativeProcessGuard()
    allowed_identities = {
        int(identity["pid"]): identity
        for identity in (allowed_current_identity, allowed_launch_ancestor_identity)
        if identity is not None
    }
    allowed_seen: set[int] = set()
    blockers: list[int] = []
    try:
        processes = module.process_iter(["pid", "name"], ad_value=None)
        for process in processes:
            info = getattr(process, "info", None)
            if not isinstance(info, dict):
                raise RunsBackupGateError("process_scan_incomplete")
            pid = info.get("pid")
            name = info.get("name")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                continue
            if not isinstance(name, str) or not name:
                try:
                    name = image_name_fn(pid)
                except Exception as error:
                    raise RunsBackupGateError("process_scan_incomplete") from error
                if not isinstance(name, str) or not name:
                    raise RunsBackupGateError("process_scan_incomplete")
            if not _python_image(name):
                continue
            try:
                with process.oneshot():
                    executable = process.exe()
                    argv = process.cmdline()
            except Exception as error:
                if _is_process_error(module, error, "NoSuchProcess"):
                    continue
                raise RunsBackupGateError("process_scan_incomplete") from error
            if (
                not isinstance(executable, str)
                or not ntpath.isabs(executable)
                or not _python_image(executable)
                or not isinstance(argv, list)
                or not argv
                or any(not isinstance(argument, str) or not argument for argument in argv)
            ):
                raise RunsBackupGateError("process_scan_incomplete")
            if not _argv_targets_dayz_mcp(argv):
                continue
            expected_identity = allowed_identities.get(pid)
            if expected_identity is None:
                blockers.append(pid)
                continue
            actual = native_guard.snapshot(pid)
            if not isinstance(actual, Mapping) or not _identity_matches(
                expected_identity, actual
            ):
                raise RunsBackupGateError("allowed_process_identity_drift")
            allowed_seen.add(pid)
    except RunsBackupGateError:
        raise
    except Exception as error:
        raise RunsBackupGateError("process_scan_failed") from error
    if set(allowed_identities) != allowed_seen:
        raise RunsBackupGateError("allowed_process_not_observed")
    return tuple(sorted(set(blockers)))


def listener_present(port: int) -> bool:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise RunsBackupGateError("invalid_listener_port")
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False
    finally:
        probe.close()


def _validate_receipt(
    receipt_path: Path,
    source_path: Path,
    backup_path: Path,
) -> dict[str, object]:
    try:
        _assert_regular_path(receipt_path, "invalid_runs_backup_receipt")
        payload = json.loads(
            receipt_path.read_text(encoding="utf-8"),
            object_pairs_hook=_closed_json_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RunsBackupGateError("invalid_runs_backup_receipt") from error
    expected_keys = {
        "schema_version",
        "kind",
        "source_path",
        "backup_path",
        "source_absent",
        "source",
        "backup",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema_version") != 1
        or payload.get("kind") != _RECEIPT_KIND
        or payload.get("source_path") != str(source_path)
        or payload.get("backup_path") != str(backup_path)
        or not isinstance(payload.get("source_absent"), bool)
    ):
        raise RunsBackupGateError("invalid_runs_backup_receipt")
    if payload["source_absent"]:
        if payload.get("source") is not None or payload.get("backup") is not None:
            raise RunsBackupGateError("invalid_runs_backup_receipt")
        if os.path.lexists(backup_path):
            raise RunsBackupGateError("unexpected_runs_backup")
        return payload

    source = payload.get("source")
    backup = payload.get("backup")
    if (
        not isinstance(source, dict)
        or set(source) != {"bytes", "sha256"}
        or source != backup
    ):
        raise RunsBackupGateError("invalid_runs_backup_receipt")
    backup_bytes = _read_file(backup_path, "runs_backup_missing")
    if _metadata(backup_bytes) != backup:
        raise RunsBackupGateError("runs_backup_drift")
    return payload


def _assert_quiescent(
    port: int,
    allowed_current_identity: Mapping[str, object] | None,
    allowed_launch_ancestor_identity: Mapping[str, object] | None,
    scan_fn: Callable[..., tuple[int, ...]],
    listener_fn: Callable[[int], bool],
) -> None:
    try:
        has_listener = listener_fn(port)
    except RunsBackupGateError:
        raise
    except Exception as error:
        raise RunsBackupGateError("listener_probe_failed") from error
    if has_listener is not False:
        raise RunsBackupGateError("listener_present")
    blockers = (
        scan_fn(allowed_current_identity, allowed_launch_ancestor_identity)
        if allowed_launch_ancestor_identity is not None
        else scan_fn(allowed_current_identity)
    )
    if not isinstance(blockers, tuple) or any(
        not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 for pid in blockers
    ):
        raise RunsBackupGateError("invalid_process_scan_result")
    if blockers:
        raise RunsBackupGateError("dayz_mcp_process_present")


def _encoded_json(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


def _validate_transaction_marker(
    marker_path: Path,
    source_path: Path,
    backup_path: Path,
    receipt_path: Path,
    pending_receipt_path: Path,
) -> tuple[dict[str, object], bytes]:
    encoded = _read_file(marker_path, "invalid_runs_backup_transaction")
    try:
        payload = json.loads(
            encoded.decode("utf-8"), object_pairs_hook=_closed_json_object
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RunsBackupGateError("invalid_runs_backup_transaction") from error
    expected_keys = {
        "schema_version",
        "kind",
        "revision",
        "previous_sha256",
        "phase",
        "source_path",
        "backup_path",
        "receipt_path",
        "pending_receipt_path",
        "source_absent",
        "source",
    }
    revision = payload.get("revision") if isinstance(payload, dict) else None
    previous = payload.get("previous_sha256") if isinstance(payload, dict) else None
    source_absent = payload.get("source_absent") if isinstance(payload, dict) else None
    source = payload.get("source") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema_version") != 1
        or payload.get("kind") != _TRANSACTION_KIND
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or payload.get("phase") not in _TRANSACTION_PHASES
        or payload.get("source_path") != str(source_path)
        or payload.get("backup_path") != str(backup_path)
        or payload.get("receipt_path") != str(receipt_path)
        or payload.get("pending_receipt_path") != str(pending_receipt_path)
        or not isinstance(source_absent, bool)
    ):
        raise RunsBackupGateError("invalid_runs_backup_transaction")
    expected_revision = {
        "prepared": 1,
        "backup_written": 2,
        "receipt_pending": 3,
    }[str(payload["phase"])]
    if revision != expected_revision or (
        (revision == 1 and previous is not None)
        or (revision > 1 and not _valid_sha256(previous))
    ):
        raise RunsBackupGateError("invalid_runs_backup_transaction")
    if source_absent:
        if source is not None:
            raise RunsBackupGateError("invalid_runs_backup_transaction")
    elif (
        not isinstance(source, dict)
        or set(source) != {"bytes", "sha256"}
        or not isinstance(source.get("bytes"), int)
        or isinstance(source.get("bytes"), bool)
        or source.get("bytes", -1) < 0
        or not _valid_sha256(source.get("sha256"))
    ):
        raise RunsBackupGateError("invalid_runs_backup_transaction")
    return payload, encoded


def _publish_transaction_marker(
    marker_path: Path,
    next_path: Path,
    marker: Mapping[str, object],
    fault_injector: Callable[[str], None] | None,
) -> str:
    if os.name != "nt":
        raise RunsBackupGateError("runs_backup_transaction_publish_failed")
    if os.path.lexists(marker_path):
        raise RunsBackupGateError("runs_backup_transaction_conflict")
    if os.path.lexists(next_path):
        raise RunsBackupGateError("runs_backup_transaction_conflict")
    encoded = _encoded_json(marker)
    _exclusive_write(next_path, encoded)
    _validate_transaction_marker(
        next_path,
        Path(str(marker["source_path"])),
        Path(str(marker["backup_path"])),
        Path(str(marker["receipt_path"])),
        Path(str(marker["pending_receipt_path"])),
    )
    if fault_injector is not None:
        fault_injector("marker_before_rename")
    if _read_file(next_path, "invalid_runs_backup_transaction") != encoded:
        raise RunsBackupGateError("runs_backup_transaction_conflict")
    try:
        os.rename(next_path, marker_path)
    except FileExistsError as error:
        raise RunsBackupGateError("runs_backup_transaction_conflict") from error
    except OSError as error:
        raise RunsBackupGateError("runs_backup_transaction_publish_failed") from error
    if fault_injector is not None:
        fault_injector("marker_after_rename")
    if _read_file(marker_path, "invalid_runs_backup_transaction") != encoded:
        raise RunsBackupGateError("runs_backup_transaction_conflict")
    if fault_injector is not None:
        fault_injector("marker_after_revalidate")
    return _sha256(encoded)


def _source_matches_transaction(
    marker: Mapping[str, object], source_path: Path
) -> bool:
    try:
        if marker["source_absent"]:
            return not source_path.exists()
        current = _read_file(source_path, "runs_source_unavailable")
        return _metadata(current) == marker["source"]
    except (OSError, RunsBackupGateError):
        return False


def _build_transaction_marker(
    source_path: Path,
    backup_path: Path,
    receipt_path: Path,
    pending_receipt_path: Path,
    source_metadata: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": _TRANSACTION_KIND,
        "revision": 1,
        "previous_sha256": None,
        "phase": "prepared",
        "source_path": str(source_path),
        "backup_path": str(backup_path),
        "receipt_path": str(receipt_path),
        "pending_receipt_path": str(pending_receipt_path),
        "source_absent": source_metadata is None,
        "source": source_metadata,
    }


def _build_receipt(
    source_path: Path,
    backup_path: Path,
    source_metadata: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": _RECEIPT_KIND,
        "source_path": str(source_path),
        "backup_path": str(backup_path),
        "source_absent": source_metadata is None,
        "source": source_metadata,
        "backup": source_metadata,
    }


def _assert_owned_prefix(path: Path, expected: bytes | None) -> None:
    if not os.path.lexists(path):
        return
    if expected is None:
        raise RunsBackupGateError("runs_backup_recovery_conflict")
    actual = _read_file(path, "runs_backup_recovery_conflict")
    if not expected.startswith(actual):
        raise RunsBackupGateError("runs_backup_recovery_conflict")


def _next_marker_candidates(
    marker: Mapping[str, object], marker_encoded: bytes
) -> tuple[bytes, ...]:
    phase = str(marker["phase"])
    if phase == "receipt_pending":
        return ()
    candidates: list[bytes] = []
    if phase == "prepared":
        next_phase = "backup_written"
    elif phase == "backup_written":
        next_phase = "receipt_pending"
    else:
        return ()
    legacy = dict(marker)
    legacy["revision"] = int(marker["revision"]) + 1
    legacy["previous_sha256"] = _sha256(marker_encoded)
    legacy["phase"] = next_phase
    candidates.append(_encoded_json(legacy))
    return tuple(candidates)


def _recover_backup_transaction(
    source_path: Path,
    backup_path: Path,
    receipt_path: Path,
    pending_receipt_path: Path,
    marker_path: Path,
    next_path: Path,
    fault_injector: Callable[[str], None] | None,
) -> dict[str, object] | None:
    # A valid final receipt is authoritative for both new and legacy writers.
    if os.path.lexists(receipt_path):
        receipt = _validate_receipt(receipt_path, source_path, backup_path)
        auxiliary_present = any(
            os.path.lexists(path) for path in (pending_receipt_path, next_path)
        )
        if auxiliary_present:
            raise RunsBackupGateError("runs_backup_recovery_conflict")
        if os.path.lexists(marker_path):
            marker, marker_encoded = _validate_transaction_marker(
                marker_path,
                source_path,
                backup_path,
                receipt_path,
                pending_receipt_path,
            )
            if (
                marker.get("phase") not in {"prepared", "receipt_pending"}
                or marker.get("source_absent") != receipt.get("source_absent")
                or marker.get("source") != receipt.get("source")
            ):
                raise RunsBackupGateError("runs_backup_recovery_conflict")
            _safe_unlink_owned(
                marker_path,
                marker_encoded,
                fault_injector=fault_injector,
                phase_name="marker",
            )
        return receipt

    if not os.path.lexists(marker_path):
        # The only safe marker-less recovery is the create of the first marker,
        # before any data artifact can have been written.
        if (
            os.path.lexists(next_path)
            and not os.path.lexists(backup_path)
            and not os.path.lexists(pending_receipt_path)
        ):
            source_bytes = (
                _read_file(source_path, "runs_source_unavailable")
                if source_path.exists()
                else None
            )
            initial = _build_transaction_marker(
                source_path,
                backup_path,
                receipt_path,
                pending_receipt_path,
                _metadata(source_bytes) if source_bytes is not None else None,
            )
            initial_encoded = _encoded_json(initial)
            _assert_owned_prefix(next_path, initial_encoded)
            _safe_unlink_owned(
                next_path,
                initial_encoded,
                allow_prefix=True,
                fault_injector=fault_injector,
                phase_name="marker_next",
            )
            return None
        if any(
            os.path.lexists(path)
            for path in (backup_path, pending_receipt_path, next_path)
        ):
            raise RunsBackupGateError("incomplete_runs_backup_artifacts")
        return None

    marker, marker_encoded = _validate_transaction_marker(
        marker_path,
        source_path,
        backup_path,
        receipt_path,
        pending_receipt_path,
    )
    if not _source_matches_transaction(marker, source_path):
        raise RunsBackupGateError("runs_backup_recovery_conflict")

    source_bytes = (
        None
        if marker["source_absent"]
        else _read_file(source_path, "runs_backup_recovery_conflict")
    )
    receipt = _build_receipt(
        source_path,
        backup_path,
        marker["source"] if isinstance(marker["source"], dict) else None,
    )
    _assert_owned_prefix(backup_path, source_bytes)
    _assert_owned_prefix(pending_receipt_path, _encoded_json(receipt))
    next_candidates: tuple[bytes, ...] = ()
    if os.path.lexists(next_path):
        next_candidates = _next_marker_candidates(marker, marker_encoded)
        if not next_candidates:
            raise RunsBackupGateError("runs_backup_recovery_conflict")
        actual_next = _read_file(next_path, "runs_backup_recovery_conflict")
        if not any(candidate.startswith(actual_next) for candidate in next_candidates):
            raise RunsBackupGateError("runs_backup_recovery_conflict")

    # The marker proves ownership and the unchanged source proves rollback is safe.
    # Delete the marker last so a crash during cleanup remains recoverable.
    receipt_encoded = _encoded_json(receipt)
    if os.path.lexists(pending_receipt_path):
        _safe_unlink_owned(
            pending_receipt_path,
            receipt_encoded,
            allow_prefix=True,
            fault_injector=fault_injector,
            phase_name="pending_receipt",
        )
    if os.path.lexists(next_path):
        _safe_unlink_owned(
            next_path,
            next_candidates,
            allow_prefix=True,
            fault_injector=fault_injector,
            phase_name="marker_next",
        )
    if os.path.lexists(backup_path):
        if source_bytes is None:
            raise RunsBackupGateError("runs_backup_recovery_conflict")
        _safe_unlink_owned(
            backup_path,
            source_bytes,
            allow_prefix=True,
            fault_injector=fault_injector,
            phase_name="backup",
        )
    _safe_unlink_owned(
        marker_path,
        marker_encoded,
        fault_injector=fault_injector,
        phase_name="marker",
    )
    return None


def ensure_runs_v1_backup(
    paths: RuntimePaths,
    port: int,
    *,
    migration_dir: Path = MIGRATION_DIR,
    allowed_current_identity: Mapping[str, object] | None = None,
    allowed_launch_ancestor_identity: Mapping[str, object] | None = None,
    scan_fn: Callable[..., tuple[int, ...]] = scan_dayz_mcp_processes,
    listener_fn: Callable[[int], bool] = listener_present,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, object]:
    if not isinstance(paths, RuntimePaths):
        raise RunsBackupGateError("invalid_runtime_paths")
    if allowed_current_identity is not None and (
        not _valid_allowed_identity(allowed_current_identity)
        or allowed_current_identity.get("pid") != os.getpid()
    ):
        raise RunsBackupGateError("invalid_allowed_process_identity")
    if allowed_launch_ancestor_identity is not None and (
        allowed_current_identity is None
        or not _valid_allowed_identity(allowed_launch_ancestor_identity)
        or allowed_launch_ancestor_identity.get("pid") != os.getppid()
        or allowed_launch_ancestor_identity.get("command_line_sha256")
        != allowed_current_identity.get("command_line_sha256")
    ):
        raise RunsBackupGateError("invalid_allowed_process_identity")
    source_path = _absolute(paths.runs_path)
    destination_dir = _absolute(migration_dir)
    backup_path = destination_dir / BACKUP_NAME
    receipt_path = destination_dir / RECEIPT_NAME
    pending_receipt_path = destination_dir / _PENDING_RECEIPT_NAME
    marker_path = destination_dir / _TRANSACTION_NAME
    next_path = destination_dir / _TRANSACTION_NEXT_NAME
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RunsBackupGateError("runs_backup_directory_unavailable") from error
    _assert_no_name_surrogates(
        destination_dir, "runs_backup_directory_unavailable"
    )

    with _exclusive_gate_lock(destination_dir / _LOCK_NAME):
        _assert_quiescent(
            port,
            allowed_current_identity,
            allowed_launch_ancestor_identity,
            scan_fn,
            listener_fn,
        )
        recovered = _recover_backup_transaction(
            source_path,
            backup_path,
            receipt_path,
            pending_receipt_path,
            marker_path,
            next_path,
            fault_injector,
        )
        if recovered is not None:
            _assert_quiescent(
                port,
                allowed_current_identity,
                allowed_launch_ancestor_identity,
                scan_fn,
                listener_fn,
            )
            return recovered

        try:
            source_exists = source_path.exists()
        except OSError as error:
            raise RunsBackupGateError("runs_source_unavailable") from error
        source_bytes: bytes | None = None
        source_metadata: dict[str, object] | None = None
        if source_exists:
            source_bytes = _read_file(source_path, "runs_source_unavailable")
            source_metadata = _metadata(source_bytes)

        marker = _build_transaction_marker(
            source_path,
            backup_path,
            receipt_path,
            pending_receipt_path,
            source_metadata,
        )
        marker_encoded = _encoded_json(marker)
        _publish_transaction_marker(
            marker_path,
            next_path,
            marker,
            fault_injector,
        )
        if fault_injector is not None:
            fault_injector("after_marker_prepared")

        if source_exists:
            assert source_bytes is not None
            _exclusive_write(backup_path, source_bytes)
        if fault_injector is not None:
            fault_injector("after_backup_write")

        if fault_injector is not None:
            fault_injector("after_marker_backup_written")

        _assert_quiescent(
            port,
            allowed_current_identity,
            allowed_launch_ancestor_identity,
            scan_fn,
            listener_fn,
        )
        if fault_injector is not None:
            fault_injector("after_second_quiescence")
        if source_exists:
            current_source = _read_file(source_path, "runs_source_unavailable")
            backup_bytes = _read_file(backup_path, "runs_backup_missing")
            if (
                _metadata(current_source) != source_metadata
                or _metadata(backup_bytes) != source_metadata
                or current_source != source_bytes
                or backup_bytes != source_bytes
            ):
                raise RunsBackupGateError("runs_source_or_backup_drift")
        elif source_path.exists():
            raise RunsBackupGateError("runs_source_appeared")

        receipt = _build_receipt(source_path, backup_path, source_metadata)
        encoded = _encoded_json(receipt)
        _exclusive_write(pending_receipt_path, encoded)
        if fault_injector is not None:
            fault_injector("after_pending_receipt")

        if fault_injector is not None:
            fault_injector("after_marker_receipt_pending")
        if os.path.lexists(receipt_path):
            raise RunsBackupGateError("runs_backup_receipt_publish_conflict")
        try:
            os.rename(pending_receipt_path, receipt_path)
        except OSError as error:
            raise RunsBackupGateError("runs_backup_receipt_publish_failed") from error
        if fault_injector is not None:
            fault_injector("after_receipt_publish")
        validated = _validate_receipt(receipt_path, source_path, backup_path)
        if os.path.lexists(next_path):
            raise RunsBackupGateError("runs_backup_recovery_conflict")
        _safe_unlink_owned(
            marker_path,
            marker_encoded,
            fault_injector=fault_injector,
            phase_name="marker",
        )
        if fault_injector is not None:
            fault_injector("after_marker_cleanup")
        return validated
