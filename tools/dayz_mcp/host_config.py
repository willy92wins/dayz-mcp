from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator

from dayz_mcp.server_cli import parse_server_tail_silent


CLAUDE_TIMEOUT_MS = 604_800_000
CODEX_TIMEOUT_SECONDS = 604_800
_JOURNAL_SCHEMA = 1
_HEADER_RE = re.compile(r"(?m)^\s*\[mcp_servers\.dayz-mcp\]\s*(?:#.*)?$")
_ANY_HEADER_RE = re.compile(r"(?m)^\s*\[")
_TIMEOUT_RE = re.compile(r"(?m)^\s*tool_timeout_sec\s*=.*(?:\r?\n|$)")
_FaultInjector = Callable[[str], None]


class HostConfigError(RuntimeError):
    pass


class HostConfigCrash(BaseException):
    """Test-only abrupt-stop surrogate; deliberately bypasses normal rollback."""


@dataclass(frozen=True)
class DaemonProvenance:
    launch_executable: str
    native_executable: str
    argv: tuple[str, ...]
    cwd: str
    port: int
    keyfile: str
    auto_spawn_daemon: bool


@dataclass(frozen=True)
class _ClientRegistration:
    launch_executable: str
    port: int
    keyfile: str
    expected_game_version: str | None
    require_version: bool
    idle_timeout_s: float
    enable_exec_enforce: bool
    exec_allowlist: str | None
    exec_audit_path: str | None
    auto_spawn_daemon: bool


_ENTRY_KEYS = {
    "claude": frozenset({"type", "command", "args", "timeout"}),
    "codex": frozenset({"command", "args", "tool_timeout_sec"}),
}
_VALUE_OPTIONS = frozenset(
    {
        "--port",
        "--keyfile",
        "--expected-game-version",
        "--idle-timeout",
        "--exec-allowlist",
        "--exec-audit-path",
        "--client-platform",
        "--task-label",
    }
)
_BOOLEAN_OPTIONS = frozenset(
    {
        "--client",
        "--require-version",
        "--enable-exec-enforce",
        "--no-daemon-autospawn",
    }
)
_REQUIRED_OPTIONS = frozenset(
    {"--client", "--port", "--keyfile", "--idle-timeout", "--client-platform"}
)


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HostConfigError("invalid_claude_config")
        result[key] = value
    return result


def _canonical_existing_file(value: object) -> str:
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise HostConfigError("daemon_provenance_conflict")
    candidate = Path(value)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise HostConfigError("daemon_provenance_conflict") from None
    if os.path.normcase(os.path.normpath(str(candidate))) != os.path.normcase(
        os.path.normpath(str(resolved))
    ):
        raise HostConfigError("daemon_provenance_conflict")
    if not resolved.is_file():
        raise HostConfigError("daemon_provenance_conflict")
    return str(resolved)


def require_matching_keyfile(value: object, expected: str) -> str:
    actual = _canonical_existing_file(value)
    if os.path.normcase(os.path.normpath(actual)) != os.path.normcase(
        os.path.normpath(expected)
    ):
        raise HostConfigError("daemon_provenance_conflict")
    return actual


def _local_launch_executable() -> str:
    try:
        resolved = Path(sys.executable).resolve(strict=True)
    except OSError:
        raise HostConfigError("daemon_provenance_conflict") from None
    if not resolved.is_file():
        raise HostConfigError("daemon_provenance_conflict")
    return str(resolved)


def _local_native_executable() -> str:
    from dayz_mcp import native_process_snapshot

    image = native_process_snapshot.full_image_path_of(os.getpid())
    if not isinstance(image, str) or not image or not os.path.isabs(image):
        raise HostConfigError("daemon_provenance_conflict")
    native = Path(os.path.abspath(image))
    if not native.is_file():
        raise HostConfigError("daemon_provenance_conflict")
    return str(native)


def _scan_raw_options(args: list[str]) -> dict[str, int]:
    counts = {option: 0 for option in _VALUE_OPTIONS | _BOOLEAN_OPTIONS}
    index = 2
    while index < len(args):
        token = args[index]
        if not token.startswith("--") or token == "--":
            raise HostConfigError("daemon_provenance_conflict")
        name, separator, inline_value = token.partition("=")
        if name in _BOOLEAN_OPTIONS:
            if separator:
                raise HostConfigError("daemon_provenance_conflict")
            counts[name] += 1
            index += 1
            continue
        if name not in _VALUE_OPTIONS:
            raise HostConfigError("daemon_provenance_conflict")
        counts[name] += 1
        if separator:
            if not inline_value:
                raise HostConfigError("daemon_provenance_conflict")
            index += 1
            continue
        if index + 1 >= len(args):
            raise HostConfigError("daemon_provenance_conflict")
        index += 2
    if any(counts[option] != 1 for option in _REQUIRED_OPTIONS):
        raise HostConfigError("daemon_provenance_conflict")
    if any(count > 1 for count in counts.values()):
        raise HostConfigError("daemon_provenance_conflict")
    return counts


def _registration_from_entry(
    entry: object,
    *,
    platform: str,
) -> _ClientRegistration:
    if not isinstance(entry, dict):
        raise HostConfigError("daemon_provenance_conflict")
    entry_keys = set(entry)
    expected_keys = _ENTRY_KEYS[platform]
    claude_default_env = (
        platform == "claude"
        and entry_keys == expected_keys | {"env"}
        and entry.get("env") == {}
    )
    if entry_keys != expected_keys and not claude_default_env:
        raise HostConfigError("daemon_provenance_conflict")
    if platform == "claude":
        timeout = entry.get("timeout")
        if (
            entry.get("type") != "stdio"
            or isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout != CLAUDE_TIMEOUT_MS
        ):
            raise HostConfigError("daemon_provenance_conflict")
    else:
        timeout = entry.get("tool_timeout_sec")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout != CODEX_TIMEOUT_SECONDS
        ):
            raise HostConfigError("daemon_provenance_conflict")
    command = _canonical_existing_file(entry.get("command"))
    if os.path.normcase(os.path.normpath(command)) != os.path.normcase(
        os.path.normpath(_local_launch_executable())
    ):
        raise HostConfigError("daemon_provenance_conflict")
    args = entry.get("args")
    if (
        not isinstance(args, list)
        or any(not isinstance(value, str) for value in args)
        or args[:2] != ["-m", "dayz_mcp"]
    ):
        raise HostConfigError("daemon_provenance_conflict")
    option_counts = _scan_raw_options(args)
    parsed = parse_server_tail_silent(list(args[2:]))
    namespace = parsed.namespace
    if (
        parsed.status != "parsed"
        or namespace is None
        or namespace.mode != "client"
        or namespace.client_platform != platform
        or not isinstance(namespace.port, int)
        or isinstance(namespace.port, bool)
        or not 1 <= namespace.port <= 65535
        or not isinstance(namespace.keyfile, str)
        or not math.isfinite(float(namespace.idle_timeout))
        or float(namespace.idle_timeout) < 0.0
    ):
        raise HostConfigError("daemon_provenance_conflict")
    if (
        (option_counts["--expected-game-version"] == 0 and namespace.expected_game_version is not None)
        or (option_counts["--require-version"] == 0 and namespace.require_version is not False)
        or (option_counts["--enable-exec-enforce"] == 0 and namespace.enable_exec_enforce is not False)
        or (option_counts["--exec-allowlist"] == 0 and namespace.exec_allowlist is not None)
        or (option_counts["--exec-audit-path"] == 0 and namespace.exec_audit_path is not None)
        or (option_counts["--task-label"] == 0 and namespace.task_label != "")
        or (option_counts["--no-daemon-autospawn"] == 0 and namespace.auto_spawn_daemon is not True)
    ):
        raise HostConfigError("daemon_provenance_conflict")
    keyfile = _canonical_existing_file(namespace.keyfile)
    exec_allowlist = namespace.exec_allowlist
    if exec_allowlist is not None:
        exec_allowlist = _canonical_existing_file(exec_allowlist)
    return _ClientRegistration(
        launch_executable=command,
        port=namespace.port,
        keyfile=keyfile,
        expected_game_version=namespace.expected_game_version,
        require_version=bool(namespace.require_version),
        idle_timeout_s=float(namespace.idle_timeout),
        enable_exec_enforce=bool(namespace.enable_exec_enforce),
        exec_allowlist=exec_allowlist,
        exec_audit_path=namespace.exec_audit_path,
        auto_spawn_daemon=bool(namespace.auto_spawn_daemon),
    )


def _reject_json_constant(_value: str) -> object:
    raise HostConfigError("daemon_provenance_conflict")


def _registration_from_raw(raw: bytes, *, platform: str) -> _ClientRegistration | None:
    try:
        if platform == "claude":
            parsed = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_pairs,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(parsed, dict):
                raise HostConfigError("daemon_provenance_conflict")
            servers = parsed.get("mcpServers")
        else:
            parsed = tomllib.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise HostConfigError("daemon_provenance_conflict")
            servers = parsed.get("mcp_servers")
    except (UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, HostConfigError):
        raise HostConfigError("daemon_provenance_conflict") from None
    if servers is None:
        return None
    if not isinstance(servers, dict):
        raise HostConfigError("daemon_provenance_conflict")
    if "dayz-mcp" not in servers:
        return None
    entry = servers["dayz-mcp"]
    return _registration_from_entry(entry, platform=platform)


def resolve_daemon_provenance(
    *,
    claude_path: Path | None = None,
    codex_path: Path | None = None,
) -> DaemonProvenance:
    """Resolve the daemon policy only from both canonical local registrations."""
    paths = {
        "claude": claude_path or (Path.home() / ".claude.json"),
        "codex": codex_path or (Path.home() / ".codex" / "config.toml"),
    }
    with _open_pinned_configs(paths) as handles:
        snapshots: dict[str, tuple[tuple[object, ...], bytes] | None] = {}
        registrations: dict[str, _ClientRegistration | None] = {}
        for platform in ("claude", "codex"):
            handle = handles[platform]
            if handle is None:
                snapshots[platform] = None
                registrations[platform] = None
                continue
            raw = handle.read()
            snapshots[platform] = (handle.identity(), raw)
            registrations[platform] = _registration_from_raw(raw, platform=platform)

        present = sum(registration is not None for registration in registrations.values())
        if present == 0:
            raise HostConfigError("daemon_provenance_unavailable")
        if present == 1:
            raise HostConfigError("daemon_provenance_incomplete")
        claude = registrations["claude"]
        codex = registrations["codex"]
        if claude is None or codex is None or claude != codex:
            raise HostConfigError("daemon_provenance_conflict")

        # Local import keeps the timeout/config transaction module independently
        # importable while reusing the one canonical daemon argv/cwd builders.
        from dayz_mcp import daemon_contract

        config = SimpleNamespace(
            port=claude.port,
            keyfile=claude.keyfile,
            expected_game_version=claude.expected_game_version,
            require_version=claude.require_version,
            idle_timeout_s=claude.idle_timeout_s,
            enable_exec_enforce=claude.enable_exec_enforce,
            exec_allowlist=claude.exec_allowlist,
            exec_audit_path=claude.exec_audit_path,
        )
        argv = daemon_contract.build_daemon_argv(
            config, python=claude.launch_executable
        )
        provenance = DaemonProvenance(
            launch_executable=claude.launch_executable,
            native_executable=_local_native_executable(),
            argv=tuple(argv),
            cwd=daemon_contract.daemon_runtime_cwd(),
            port=claude.port,
            keyfile=claude.keyfile,
            auto_spawn_daemon=claude.auto_spawn_daemon,
        )
        for platform in ("claude", "codex"):
            handle = handles[platform]
            snapshot = snapshots[platform]
            if handle is None:
                if os.path.lexists(paths[platform]):
                    raise HostConfigError("daemon_provenance_conflict")
                continue
            if snapshot is None:
                raise HostConfigError("daemon_provenance_conflict")
            identity, raw = snapshot
            if handle.identity() != identity or handle.read() != raw:
                raise HostConfigError("daemon_provenance_conflict")
            try:
                reopened = _PinnedConfigFile(handle.path)
            except _PinnedConfigMissing:
                raise HostConfigError("daemon_provenance_conflict") from None
            try:
                if reopened.identity() != identity or reopened.read() != raw:
                    raise HostConfigError("daemon_provenance_conflict")
            finally:
                reopened.close()
        return provenance


def build_claude_target(raw: bytes) -> bytes:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, HostConfigError):
        raise HostConfigError("invalid_claude_config") from None
    if not isinstance(value, dict):
        raise HostConfigError("invalid_claude_config")
    servers = value.get("mcpServers")
    entry = servers.get("dayz-mcp") if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        raise HostConfigError("missing_claude_dayz_mcp")
    entry["timeout"] = CLAUDE_TIMEOUT_MS
    target = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        verified = json.loads(target)
    except json.JSONDecodeError:
        raise HostConfigError("invalid_claude_target") from None
    timeout = verified.get("mcpServers", {}).get("dayz-mcp", {}).get("timeout")
    if isinstance(timeout, bool) or timeout != CLAUDE_TIMEOUT_MS:
        raise HostConfigError("invalid_claude_target")
    return target


def build_codex_target(raw: bytes) -> bytes:
    try:
        text = raw.decode("utf-8")
        parsed = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise HostConfigError("invalid_codex_config") from None
    entry = parsed.get("mcp_servers", {}).get("dayz-mcp")
    if not isinstance(entry, dict):
        raise HostConfigError("missing_codex_dayz_mcp")
    matches = list(_HEADER_RE.finditer(text))
    if len(matches) != 1:
        raise HostConfigError("ambiguous_codex_dayz_mcp")
    start = matches[0].end()
    next_header = _ANY_HEADER_RE.search(text, start)
    end = next_header.start() if next_header is not None else len(text)
    body = text[start:end]
    timeout_matches = list(_TIMEOUT_RE.finditer(body))
    if "tool_timeout_sec" in entry and len(timeout_matches) != 1:
        raise HostConfigError("ambiguous_codex_timeout")
    if len(timeout_matches) > 1:
        raise HostConfigError("ambiguous_codex_timeout")
    newline = "\r\n" if "\r\n" in text else "\n"
    body_without_timeout = _TIMEOUT_RE.sub("", body)
    if body_without_timeout.startswith("\r\n"):
        body_without_timeout = body_without_timeout[2:]
    elif body_without_timeout.startswith("\n"):
        body_without_timeout = body_without_timeout[1:]
    insertion = f"{newline}tool_timeout_sec = {CODEX_TIMEOUT_SECONDS}{newline}"
    target_text = text[:start] + insertion + body_without_timeout + text[end:]
    try:
        verified = tomllib.loads(target_text)
    except tomllib.TOMLDecodeError:
        raise HostConfigError("invalid_codex_target") from None
    timeout = verified.get("mcp_servers", {}).get("dayz-mcp", {}).get("tool_timeout_sec")
    if isinstance(timeout, bool) or timeout != CODEX_TIMEOUT_SECONDS:
        raise HostConfigError("invalid_codex_target")
    return target_text.encode("utf-8")


if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _FILE_BEGIN = 0
    _FILE_ID_INFO_CLASS = 18
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", ctypes.c_ubyte * 16),
        ]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

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
    _kernel32.WriteFile.argtypes = _kernel32.ReadFile.argtypes
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.SetFilePointerEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    )
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _kernel32.SetEndOfFile.argtypes = (wintypes.HANDLE,)
    _kernel32.SetEndOfFile.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.CreateDirectoryW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(_SECURITY_ATTRIBUTES))
    _kernel32.CreateDirectoryW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    _kernel32.LocalFree.restype = wintypes.HLOCAL


class _WinFile:
    def __init__(self, path: Path) -> None:
        if os.name != "nt":
            raise HostConfigError("windows_required")
        self.path = Path(path).resolve(strict=True)
        handle = _kernel32.CreateFileW(
            str(self.path),
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            raise HostConfigError("registration_config_busy")
        self.handle = handle

    def close(self) -> None:
        handle = getattr(self, "handle", None)
        if handle is not None:
            self.handle = None
            _kernel32.CloseHandle(handle)

    def __enter__(self) -> "_WinFile":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _seek_start(self) -> None:
        if not _kernel32.SetFilePointerEx(self.handle, 0, None, _FILE_BEGIN):
            raise HostConfigError("registration_config_io_failed")

    def read(self) -> bytes:
        self._seek_start()
        chunks: list[bytes] = []
        while True:
            buffer = ctypes.create_string_buffer(64 * 1024)
            received = wintypes.DWORD()
            if not _kernel32.ReadFile(
                self.handle, buffer, len(buffer), ctypes.byref(received), None
            ):
                raise HostConfigError("registration_config_io_failed")
            if received.value == 0:
                return b"".join(chunks)
            chunks.append(buffer.raw[: received.value])

    def write(self, value: bytes) -> None:
        self._seek_start()
        offset = 0
        while offset < len(value):
            chunk = value[offset : offset + 64 * 1024]
            written = wintypes.DWORD()
            buffer = ctypes.create_string_buffer(chunk)
            if not _kernel32.WriteFile(
                self.handle, buffer, len(chunk), ctypes.byref(written), None
            ):
                raise HostConfigError("registration_config_io_failed")
            if written.value <= 0 or written.value > len(chunk):
                raise HostConfigError("registration_config_io_failed")
            offset += written.value
        if not _kernel32.SetEndOfFile(self.handle):
            raise HostConfigError("registration_config_io_failed")
        if not _kernel32.FlushFileBuffers(self.handle):
            raise HostConfigError("registration_config_io_failed")

    def identity(self) -> dict[str, object]:
        info = _FILE_ID_INFO()
        if not _kernel32.GetFileInformationByHandleEx(
            self.handle,
            _FILE_ID_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise HostConfigError("registration_config_io_failed")
        return {
            "volume_serial": int(info.VolumeSerialNumber),
            "file_id": bytes(info.FileId).hex().upper(),
        }


class _PinnedConfigMissing(Exception):
    pass


if os.name == "nt":
    _FILE_SHARE_READ = 0x00000001
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    _INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    _kernel32.GetFileAttributesW.argtypes = (wintypes.LPCWSTR,)
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _kernel32.GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD


def _assert_no_reparse_parents(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    if os.name != "nt":
        if any(parent.is_symlink() for parent in absolute.parents):
            raise HostConfigError("daemon_provenance_conflict")
        return
    for parent in reversed(absolute.parents):
        attributes = int(_kernel32.GetFileAttributesW(str(parent)))
        if attributes == _INVALID_FILE_ATTRIBUTES:
            raise _PinnedConfigMissing()
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise HostConfigError("daemon_provenance_conflict")


def _final_handle_path(handle: object) -> str:
    if os.name != "nt":
        raise HostConfigError("daemon_provenance_conflict")
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(
        _kernel32.GetFinalPathNameByHandleW(
            handle, buffer, len(buffer), 0
        )
    )
    if length <= 0 or length >= len(buffer):
        raise HostConfigError("daemon_provenance_conflict")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


class _PinnedConfigFile:
    def __init__(self, path: Path) -> None:
        self.path = Path(os.path.abspath(path))
        _assert_no_reparse_parents(self.path)
        if os.name == "nt":
            handle = _kernel32.CreateFileW(
                str(self.path),
                _GENERIC_READ,
                _FILE_SHARE_READ,
                None,
                _OPEN_EXISTING,
                _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
            if handle == _INVALID_HANDLE_VALUE:
                error = ctypes.get_last_error()
                if error in {2, 3}:
                    raise _PinnedConfigMissing()
                raise HostConfigError("daemon_provenance_conflict")
            self.handle = handle
            try:
                attributes = _FILE_ATTRIBUTE_TAG_INFO()
                if not _kernel32.GetFileInformationByHandleEx(
                    self.handle,
                    _FILE_ATTRIBUTE_TAG_INFO_CLASS,
                    ctypes.byref(attributes),
                    ctypes.sizeof(attributes),
                ):
                    raise HostConfigError("daemon_provenance_conflict")
                if attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                    raise HostConfigError("daemon_provenance_conflict")
                final_path = _final_handle_path(self.handle)
                if os.path.normcase(os.path.normpath(final_path)) != os.path.normcase(
                    os.path.normpath(str(self.path))
                ):
                    raise HostConfigError("daemon_provenance_conflict")
            except BaseException:
                self.close()
                raise
        else:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                self.handle = os.open(self.path, flags)
            except FileNotFoundError:
                raise _PinnedConfigMissing() from None
            except OSError:
                raise HostConfigError("daemon_provenance_conflict") from None
            if not os.path.isfile(self.path):
                self.close()
                raise HostConfigError("daemon_provenance_conflict")

    def close(self) -> None:
        handle = getattr(self, "handle", None)
        if handle is None:
            return
        self.handle = None
        if os.name == "nt":
            _kernel32.CloseHandle(handle)
        else:
            os.close(handle)

    def read(self) -> bytes:
        if os.name != "nt":
            os.lseek(self.handle, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(self.handle, 64 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        if not _kernel32.SetFilePointerEx(self.handle, 0, None, _FILE_BEGIN):
            raise HostConfigError("daemon_provenance_conflict")
        chunks = []
        while True:
            buffer = ctypes.create_string_buffer(64 * 1024)
            received = wintypes.DWORD()
            if not _kernel32.ReadFile(
                self.handle, buffer, len(buffer), ctypes.byref(received), None
            ):
                raise HostConfigError("daemon_provenance_conflict")
            if received.value == 0:
                return b"".join(chunks)
            chunks.append(buffer.raw[: received.value])

    def identity(self) -> tuple[object, ...]:
        if os.name != "nt":
            stat = os.fstat(self.handle)
            return (stat.st_dev, stat.st_ino, stat.st_mode)
        info = _FILE_ID_INFO()
        if not _kernel32.GetFileInformationByHandleEx(
            self.handle,
            _FILE_ID_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise HostConfigError("daemon_provenance_conflict")
        return (int(info.VolumeSerialNumber), bytes(info.FileId))


@contextmanager
def _open_pinned_configs(
    paths: dict[str, Path],
) -> Iterator[dict[str, _PinnedConfigFile | None]]:
    handles: dict[str, _PinnedConfigFile | None] = {}
    try:
        for platform in ("claude", "codex"):
            try:
                handles[platform] = _PinnedConfigFile(paths[platform])
            except _PinnedConfigMissing:
                handles[platform] = None
        yield handles
    finally:
        for handle in handles.values():
            if handle is not None:
                handle.close()


@contextmanager
def open_exclusive_for_test(path: Path) -> Iterator[None]:
    handle = _WinFile(path)
    try:
        yield
    finally:
        handle.close()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _default_journal_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise HostConfigError("local_app_data_missing")
    return Path(local) / "DayZ_MCP" / "host-config-transaction"


def _mkdir_restricted(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_dir() or path.is_symlink():
            raise HostConfigError("registration_journal_invalid")
        return
    if os.name != "nt":
        path.mkdir(mode=0o700)
        return
    descriptor = wintypes.LPVOID()
    # Protected DACL: local SYSTEM and the object's owner only.
    sddl = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;OW)"
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise HostConfigError("registration_journal_acl_failed")
    try:
        attributes = _SECURITY_ATTRIBUTES(
            ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
        )
        if not _kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            raise HostConfigError("registration_journal_acl_failed")
    finally:
        _kernel32.LocalFree(descriptor)


def _write_private(path: Path, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(value):
            count = os.write(descriptor, value[offset:])
            if count <= 0:
                raise HostConfigError("registration_journal_write_failed")
            offset += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _manifest_path(journal: Path) -> Path:
    return journal / "manifest.json"


def _persist_manifest(journal: Path, manifest: dict[str, object]) -> None:
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = journal / "manifest.next"
    _write_private(temporary, payload)
    os.replace(temporary, _manifest_path(journal))


def _journal_payload(
    paths: dict[str, Path],
    identities: dict[str, dict[str, object]],
    originals: dict[str, bytes],
    targets: dict[str, bytes],
) -> dict[str, object]:
    return {
        "schema": _JOURNAL_SCHEMA,
        "status": "prepared",
        "files": {
            role: {
                "path": str(paths[role]),
                "identity": identities[role],
                "original_sha256": _sha256(originals[role]),
                "target_sha256": _sha256(targets[role]),
                "original": base64.b64encode(originals[role]).decode("ascii"),
                "target": base64.b64encode(targets[role]).decode("ascii"),
            }
            for role in ("claude", "codex")
        },
    }


def _load_manifest(journal: Path) -> dict[str, object]:
    try:
        raw = _manifest_path(journal).read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        raise HostConfigError("registration_journal_invalid") from None
    if (
        not isinstance(value, dict)
        or value.get("schema") != _JOURNAL_SCHEMA
        or value.get("status")
        not in {"prepared", "writing", "committed", "restoring_original", "restored"}
        or not isinstance(value.get("files"), dict)
        or set(value["files"]) != {"claude", "codex"}
    ):
        raise HostConfigError("registration_journal_invalid")
    expected_keys = {"schema", "status", "files"}
    if value["status"] in {"restoring_original", "restored"}:
        expected_keys.add("recovery_source")
        _decode_recovery_sources(value.get("recovery_source"))
    if set(value) != expected_keys:
        raise HostConfigError("registration_journal_invalid")
    return value


def _decode_manifest_file(value: object) -> tuple[Path, dict[str, object], bytes, bytes]:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "identity",
        "original_sha256",
        "target_sha256",
        "original",
        "target",
    }:
        raise HostConfigError("registration_journal_invalid")
    try:
        path = Path(value["path"]).resolve(strict=True)
        original = base64.b64decode(value["original"], validate=True)
        target = base64.b64decode(value["target"], validate=True)
    except (TypeError, ValueError, OSError):
        raise HostConfigError("registration_journal_invalid") from None
    identity = value.get("identity")
    if (
        not isinstance(identity, dict)
        or set(identity) != {"volume_serial", "file_id"}
        or not isinstance(identity.get("volume_serial"), int)
        or not isinstance(identity.get("file_id"), str)
        or value.get("original_sha256") != _sha256(original)
        or value.get("target_sha256") != _sha256(target)
    ):
        raise HostConfigError("registration_journal_invalid")
    return path, identity, original, target


def _encode_recovery_sources(values: dict[str, bytes]) -> dict[str, dict[str, str]]:
    return {
        role: {
            "bytes": base64.b64encode(values[role]).decode("ascii"),
            "sha256": _sha256(values[role]),
        }
        for role in ("claude", "codex")
    }


def _decode_recovery_sources(value: object) -> dict[str, bytes]:
    if not isinstance(value, dict) or set(value) != {"claude", "codex"}:
        raise HostConfigError("registration_journal_invalid")
    decoded: dict[str, bytes] = {}
    for role in ("claude", "codex"):
        item = value[role]
        if (
            not isinstance(item, dict)
            or set(item) != {"bytes", "sha256"}
            or not isinstance(item.get("bytes"), str)
            or not isinstance(item.get("sha256"), str)
        ):
            raise HostConfigError("registration_journal_invalid")
        try:
            payload = base64.b64decode(item["bytes"], validate=True)
        except (ValueError, TypeError):
            raise HostConfigError("registration_journal_invalid") from None
        if item["sha256"] != _sha256(payload):
            raise HostConfigError("registration_journal_invalid")
        decoded[role] = payload
    return decoded


def _open_both(paths: dict[str, Path]) -> dict[str, _WinFile]:
    opened: dict[str, _WinFile] = {}
    roles = sorted(paths, key=lambda role: os.path.normcase(str(paths[role].resolve())))
    try:
        for role in roles:
            opened[role] = _WinFile(paths[role])
        return opened
    except Exception:
        for handle in opened.values():
            handle.close()
        raise HostConfigError("registration_config_busy") from None


def _close_all(handles: dict[str, _WinFile]) -> None:
    for handle in handles.values():
        handle.close()


def _cleanup_journal(journal: Path) -> None:
    if journal.exists():
        shutil.rmtree(journal)


def _matches_in_place_overwrite(
    current: bytes,
    desired: bytes,
    source: bytes,
) -> bool:
    shared = min(len(desired), len(source))
    if len(current) == len(source):
        prefix = 0
        while prefix < shared and current[prefix] == desired[prefix]:
            prefix += 1
        suffix_start = len(source)
        while suffix_start > 0 and current[suffix_start - 1] == source[suffix_start - 1]:
            suffix_start -= 1
        return suffix_start <= prefix
    return (
        len(desired) > len(source)
        and len(source) < len(current) <= len(desired)
        and current == desired[: len(current)]
    )


def _is_own_write(current: bytes, original: bytes, target: bytes) -> bool:
    return _matches_in_place_overwrite(current, target, original)


def _is_restore_progress(current: bytes, original: bytes, source: bytes) -> bool:
    return _matches_in_place_overwrite(current, original, source)


def _classify(current: bytes, original: bytes, target: bytes) -> str:
    if current == original:
        return "original"
    if current == target:
        return "target"
    if _is_own_write(current, original, target):
        return "own_torn"
    return "external"


def _recover_if_needed(
    claude_path: Path,
    codex_path: Path,
    journal: Path,
    *,
    fault_injector: _FaultInjector | None = None,
) -> None:
    if not journal.exists():
        return
    manifest = _load_manifest(journal)
    files = manifest["files"]
    decoded = {
        role: _decode_manifest_file(files[role]) for role in ("claude", "codex")
    }
    expected_paths = {
        "claude": claude_path.resolve(strict=True),
        "codex": codex_path.resolve(strict=True),
    }
    if any(decoded[role][0] != expected_paths[role] for role in decoded):
        raise HostConfigError("registration_recovery_conflict")
    handles = _open_both(expected_paths)
    try:
        if any(handles[role].identity() != decoded[role][1] for role in decoded):
            raise HostConfigError("registration_recovery_conflict")
        currents = {role: handles[role].read() for role in decoded}
        status = manifest["status"]
        if status == "committed":
            if any(currents[role] != decoded[role][3] for role in decoded):
                raise HostConfigError("registration_recovery_conflict")
            _cleanup_journal(journal)
            return
        if status in {"restoring_original", "restored"}:
            sources = _decode_recovery_sources(manifest["recovery_source"])
            if status == "restored":
                if any(currents[role] != decoded[role][2] for role in decoded):
                    raise HostConfigError("registration_recovery_conflict")
                _cleanup_journal(journal)
                return
            if any(
                not _is_restore_progress(
                    currents[role], decoded[role][2], sources[role]
                )
                for role in decoded
            ):
                raise HostConfigError("registration_recovery_conflict")
        else:
            classes = {
                role: _classify(currents[role], decoded[role][2], decoded[role][3])
                for role in decoded
            }
            if "external" in classes.values():
                raise HostConfigError("registration_recovery_conflict")
            if all(value == "target" for value in classes.values()):
                _cleanup_journal(journal)
                return
            manifest["status"] = "restoring_original"
            manifest["recovery_source"] = _encode_recovery_sources(currents)
            _persist_manifest(journal, manifest)
            if fault_injector is not None:
                fault_injector("after_recovery_prepare")
        for role in ("claude", "codex"):
            handles[role].write(decoded[role][2])
            if fault_injector is not None:
                fault_injector(f"after_recovery_write_{role}")
        if any(handles[role].read() != decoded[role][2] for role in decoded):
            raise HostConfigError("registration_recovery_restore_failed")
        manifest["status"] = "restored"
        _persist_manifest(journal, manifest)
        _cleanup_journal(journal)
    finally:
        _close_all(handles)


def apply_host_timeouts(
    claude_path: Path,
    codex_path: Path,
    *,
    journal_root: Path | None = None,
    fault_injector: _FaultInjector | None = None,
) -> dict[str, str]:
    claude_path = Path(claude_path).resolve(strict=True)
    codex_path = Path(codex_path).resolve(strict=True)
    if claude_path == codex_path:
        raise HostConfigError("registration_config_paths_conflict")
    journal = Path(journal_root) if journal_root is not None else _default_journal_root()
    _recover_if_needed(
        claude_path,
        codex_path,
        journal,
        fault_injector=fault_injector,
    )

    try:
        originals = {
            "claude": claude_path.read_bytes(),
            "codex": codex_path.read_bytes(),
        }
    except OSError:
        raise HostConfigError("registration_config_busy") from None
    try:
        targets = {
            "claude": build_claude_target(originals["claude"]),
            "codex": build_codex_target(originals["codex"]),
        }
    except HostConfigError:
        raise
    except OSError:
        raise HostConfigError("registration_config_read_failed") from None

    paths = {"claude": claude_path, "codex": codex_path}
    handles = _open_both(paths)
    journal_started = False
    journal_prepared = False
    try:
        if any(handles[role].read() != originals[role] for role in paths):
            raise HostConfigError("registration_config_drift")
        identities = {role: handles[role].identity() for role in paths}
        _mkdir_restricted(journal)
        journal_started = True
        manifest = _journal_payload(paths, identities, originals, targets)
        _persist_manifest(journal, manifest)
        journal_prepared = True
        if fault_injector is not None:
            fault_injector("after_prepare")
        manifest["status"] = "writing"
        _persist_manifest(journal, manifest)
        for role in ("claude", "codex"):
            handles[role].write(targets[role])
            if fault_injector is not None:
                fault_injector(f"after_write_{role}")
        if any(handles[role].read() != targets[role] for role in paths):
            raise HostConfigError("registration_config_verify_failed")
        build_claude_target(handles["claude"].read())
        build_codex_target(handles["codex"].read())
        manifest["status"] = "committed"
        _persist_manifest(journal, manifest)
        if fault_injector is not None:
            fault_injector("after_commit")
        _cleanup_journal(journal)
        return {"status": "committed"}
    except HostConfigCrash:
        raise
    except Exception:
        if journal_prepared:
            try:
                manifest["status"] = "restoring_original"
                manifest["recovery_source"] = _encode_recovery_sources(
                    {role: handles[role].read() for role in paths}
                )
                _persist_manifest(journal, manifest)
                for role in ("claude", "codex"):
                    handles[role].write(originals[role])
                if any(handles[role].read() != originals[role] for role in paths):
                    raise HostConfigError("registration_config_rollback_failed")
                manifest["status"] = "restored"
                _persist_manifest(journal, manifest)
                _cleanup_journal(journal)
            except Exception:
                raise HostConfigError("registration_config_rollback_failed") from None
        elif journal_started:
            try:
                _cleanup_journal(journal)
            except Exception:
                raise HostConfigError("registration_journal_cleanup_failed") from None
        raise HostConfigError("registration_config_transaction_failed") from None
    finally:
        _close_all(handles)
