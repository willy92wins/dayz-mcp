"""Explicit, immutable authority used to accredit the DayZ MCP daemon."""

from __future__ import annotations

import ctypes
import hashlib
import json
import msvcrt
import ntpath
import os
import re
import unicodedata
import weakref
from dataclasses import dataclass
from ctypes import wintypes
from typing import BinaryIO, MutableMapping

from dayz_mcp import bootstrap_parent, host_config
from dayz_mcp.daemon_contract import classify_dayz_argv
from dayz_mcp.daemon_policy_contract import (
    AccreditedDaemonPolicy as _ContractAccreditedDaemonPolicy,
)


_HEX64 = re.compile(r"[0-9a-f]{64}")
_BOOTSTRAP_KEYS = frozenset(
    {
        "argv",
        "authority_sha256",
        "cwd",
        "format_version",
        "host",
        "keyfile",
        "kind",
        "native_executable",
        "port",
        "security_build_id",
    }
)
_HANDLE_FLAG_INHERIT = 0x00000001
_DUPLICATE_READ_ACCESS = 0x80000000
_DUPLICATE_SAME_ACCESS = 0x00000002
_FILE_TYPE_DISK = 0x0001
_FILE_TYPE_PIPE = 0x0003
_BOOTSTRAP_ENV_NAMES = (
    "DAYZ_MCP_BOOTSTRAP_POLICY_HANDLE",
    "DAYZ_MCP_BOOTSTRAP_LIVENESS_HANDLE",
    "DAYZ_MCP_SECURITY_BUILD_ID",
)


def _kernel32_function(
    name: str, argtypes: list[object], restype: object
) -> object:
    function = getattr(ctypes.WinDLL("kernel32", use_last_error=True), name)
    function.argtypes = argtypes
    function.restype = restype
    return function


_GetCurrentProcess = _kernel32_function("GetCurrentProcess", [], wintypes.HANDLE)
_GetHandleInformation = _kernel32_function(
    "GetHandleInformation",
    [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)],
    wintypes.BOOL,
)
_GetFileType = _kernel32_function("GetFileType", [wintypes.HANDLE], wintypes.DWORD)
_DuplicateHandle = _kernel32_function(
    "DuplicateHandle",
    [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ],
    wintypes.BOOL,
)
_CloseHandle = _kernel32_function(
    "CloseHandle", [wintypes.HANDLE], wintypes.BOOL
)


def _valid_text(value: object, *, maximum: int = 520) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and unicodedata.is_normalized("NFC", value)
        and "\0" not in value
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def _valid_absolute_path(value: object) -> bool:
    if not _valid_text(value) or ntpath.normpath(value) != value:
        return False
    drive, tail = ntpath.splitdrive(value)
    return (
        len(drive) == 2
        and drive[1] == ":"
        and drive[0].isascii()
        and drive[0].isalpha()
        and tail.startswith("\\")
        and ":" not in tail
    )


def _authority_payload(
    *,
    kind: str,
    host: str,
    port: int,
    keyfile: str,
    native_executable: str,
    argv: tuple[str, ...],
    cwd: str,
    security_build_id: str | None,
) -> dict[str, object]:
    return {
        "argv": list(argv),
        "cwd": cwd,
        "host": host,
        "keyfile": keyfile,
        "kind": kind,
        "native_executable": native_executable,
        "port": port,
        "security_build_id": security_build_id,
    }


def _authority_sha256(**fields: object) -> str:
    encoded = json.dumps(
        fields,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_bootstrap_manifest")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("invalid_bootstrap_manifest")


def _read_bootstrap_manifest(handle: BinaryIO) -> bytes:
    try:
        handle.seek(0)
        raw = handle.read(65_537)
    except (OSError, ValueError):
        raise ValueError("invalid_bootstrap_manifest") from None
    if not 1 <= len(raw) <= 65_536 or raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("invalid_bootstrap_manifest")
    return raw


def _parse_bootstrap_manifest(
    raw: bytes, *, expected_security_build_id: str
) -> tuple[AccreditedDaemonPolicy, bytes]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid_bootstrap_manifest") from None
    if not isinstance(value, dict) or set(value) != _BOOTSTRAP_KEYS:
        raise ValueError("invalid_bootstrap_manifest")
    if type(value.get("format_version")) is not int or value["format_version"] != 1:
        raise ValueError("invalid_bootstrap_manifest")
    if (
        not isinstance(expected_security_build_id, str)
        or not _HEX64.fullmatch(expected_security_build_id)
        or value.get("security_build_id") != expected_security_build_id
    ):
        raise ValueError("invalid_bootstrap_manifest")
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if raw != canonical:
        raise ValueError("invalid_bootstrap_manifest")
    argv = value.get("argv")
    if not isinstance(argv, list):
        raise ValueError("invalid_bootstrap_manifest")
    try:
        policy = AccreditedDaemonPolicy(
            kind=value["kind"],
            host=value["host"],
            port=value["port"],
            keyfile=value["keyfile"],
            native_executable=value["native_executable"],
            argv=tuple(argv),
            cwd=value["cwd"],
            security_build_id=value["security_build_id"],
            authority_sha256=value["authority_sha256"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid_bootstrap_manifest") from None
    return policy, canonical


def _duplicate_inherited_manifest_handle(manifest_handle: object) -> BinaryIO:
    if type(manifest_handle) is not int or manifest_handle <= 0:
        raise ValueError("invalid_bootstrap_policy_handle")
    flags = wintypes.DWORD()
    source = wintypes.HANDLE(manifest_handle)
    if not _GetHandleInformation(source, ctypes.byref(flags)):
        raise ValueError("invalid_bootstrap_policy_handle")
    if flags.value & _HANDLE_FLAG_INHERIT == 0:
        raise ValueError("invalid_bootstrap_policy_handle")
    if _GetFileType(source) != _FILE_TYPE_DISK:
        raise ValueError("invalid_bootstrap_policy_handle")

    current_process = _GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not _DuplicateHandle(
        current_process,
        source,
        current_process,
        ctypes.byref(duplicate),
        _DUPLICATE_READ_ACCESS,
        False,
        0,
    ):
        raise ValueError("invalid_bootstrap_policy_handle")
    try:
        descriptor = msvcrt.open_osfhandle(
            int(duplicate.value), os.O_RDONLY | os.O_BINARY
        )
    except OSError:
        _CloseHandle(duplicate)
        raise ValueError("invalid_bootstrap_policy_handle") from None
    return os.fdopen(descriptor, "rb", closefd=True)


def _duplicate_inherited_liveness_handle(liveness_handle: object) -> int:
    if type(liveness_handle) is not int or liveness_handle <= 0:
        raise ValueError("invalid_bootstrap_liveness_handle")
    flags = wintypes.DWORD()
    source = wintypes.HANDLE(liveness_handle)
    if not _GetHandleInformation(source, ctypes.byref(flags)):
        raise ValueError("invalid_bootstrap_liveness_handle")
    if flags.value & _HANDLE_FLAG_INHERIT == 0:
        raise ValueError("invalid_bootstrap_liveness_handle")
    if _GetFileType(source) != _FILE_TYPE_PIPE:
        raise ValueError("invalid_bootstrap_liveness_handle")

    duplicate = wintypes.HANDLE()
    current_process = _GetCurrentProcess()
    if not _DuplicateHandle(
        current_process,
        source,
        current_process,
        ctypes.byref(duplicate),
        0,
        False,
        _DUPLICATE_SAME_ACCESS,
    ):
        raise ValueError("invalid_bootstrap_liveness_handle")
    return int(duplicate.value)


def _parse_inherited_handle(value: object, *, error: str) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError(error)
    parsed = int(value)
    if parsed <= 0 or value != str(parsed):
        raise ValueError(error)
    return parsed


@dataclass(frozen=True)
class AccreditedDaemonPolicy:
    kind: str
    host: str
    port: int
    keyfile: str
    native_executable: str
    argv: tuple[str, ...]
    cwd: str
    security_build_id: str | None
    authority_sha256: str

    def __post_init__(self) -> None:
        if self.kind not in {"normal", "bootstrap"} or self.host != "127.0.0.1":
            raise ValueError("invalid_daemon_policy")
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ValueError("invalid_daemon_policy")
        if not all(
            _valid_absolute_path(value)
            for value in (self.keyfile, self.native_executable, self.cwd)
        ):
            raise ValueError("invalid_daemon_policy")
        if (
            type(self.argv) is not tuple
            or not 1 <= len(self.argv) <= 64
            or any(not _valid_text(value) for value in self.argv)
        ):
            raise ValueError("invalid_daemon_policy")
        expected_argv_kind = (
            "normal_daemon" if self.kind == "normal" else "p0s_bootstrap_daemon"
        )
        if classify_dayz_argv(list(self.argv)) != expected_argv_kind:
            raise ValueError("invalid_daemon_policy")
        if self.kind == "normal":
            if self.security_build_id is not None:
                raise ValueError("invalid_daemon_policy")
        elif not isinstance(self.security_build_id, str) or not _HEX64.fullmatch(
            self.security_build_id
        ):
            raise ValueError("invalid_daemon_policy")

        payload = _authority_payload(
            kind=self.kind,
            host=self.host,
            port=self.port,
            keyfile=self.keyfile,
            native_executable=self.native_executable,
            argv=self.argv,
            cwd=self.cwd,
            security_build_id=self.security_build_id,
        )
        if not isinstance(self.authority_sha256, str) or not _HEX64.fullmatch(
            self.authority_sha256
        ):
            raise ValueError("invalid_daemon_policy")
        if self.authority_sha256 != _authority_sha256(**payload):
            raise ValueError("invalid_daemon_policy")

    def revalidate(self) -> None:
        self.__post_init__()
        hook = getattr(self, "_revalidation_hook", None)
        if hook is not None:
            hook()
        liveness_handle = getattr(self, "_liveness_handle", None)
        if liveness_handle is not None and _GetFileType(
            wintypes.HANDLE(liveness_handle)
        ) != _FILE_TYPE_PIPE:
            raise ValueError("daemon_policy_drift")


# One runtime type is shared by pure clients and the bootstrap/normal loaders.
AccreditedDaemonPolicy = _ContractAccreditedDaemonPolicy


def _normal_policy_from_provenance(provenance: object) -> AccreditedDaemonPolicy:
    port = getattr(provenance, "port")
    keyfile = getattr(provenance, "keyfile")
    native_executable = getattr(provenance, "native_executable")
    argv = getattr(provenance, "argv")
    cwd = getattr(provenance, "cwd")
    payload = _authority_payload(
        kind="normal",
        host="127.0.0.1",
        port=port,
        keyfile=keyfile,
        native_executable=native_executable,
        argv=argv,
        cwd=cwd,
        security_build_id=None,
    )
    return AccreditedDaemonPolicy(
        kind="normal",
        host="127.0.0.1",
        port=port,
        keyfile=keyfile,
        native_executable=native_executable,
        argv=argv,
        cwd=cwd,
        security_build_id=None,
        authority_sha256=_authority_sha256(**payload),
    )


def load_normal_daemon_policy() -> AccreditedDaemonPolicy:
    provenance = host_config.resolve_daemon_provenance()
    policy = _normal_policy_from_provenance(provenance)

    def revalidate_normal() -> None:
        current = _normal_policy_from_provenance(
            host_config.resolve_daemon_provenance()
        )
        if current != policy:
            raise ValueError("daemon_policy_drift")

    object.__setattr__(policy, "_revalidation_hook", revalidate_normal)
    return policy


def load_bootstrap_daemon_policy(
    manifest_handle: int,
    *,
    expected_security_build_id: str,
) -> AccreditedDaemonPolicy:
    pinned = _duplicate_inherited_manifest_handle(manifest_handle)
    try:
        raw = _read_bootstrap_manifest(pinned)
        policy, canonical = _parse_bootstrap_manifest(
            raw,
            expected_security_build_id=expected_security_build_id,
        )
    except BaseException:
        pinned.close()
        raise

    def revalidate_bootstrap() -> None:
        current = _read_bootstrap_manifest(pinned)
        if current != canonical:
            raise ValueError("daemon_policy_drift")
        try:
            candidate, _ = _parse_bootstrap_manifest(
                current,
                expected_security_build_id=expected_security_build_id,
            )
        except ValueError:
            raise ValueError("daemon_policy_drift") from None
        if candidate != policy:
            raise ValueError("daemon_policy_drift")

    object.__setattr__(policy, "_revalidation_hook", revalidate_bootstrap)
    weakref.finalize(policy, pinned.close)
    return policy


def load_daemon_policy(
    kind: str,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> AccreditedDaemonPolicy:
    environment = os.environ if environ is None else environ
    present = {name: environment[name] for name in _BOOTSTRAP_ENV_NAMES if name in environment}
    for name in _BOOTSTRAP_ENV_NAMES:
        environment.pop(name, None)

    if kind not in {"normal", "bootstrap"}:
        raise ValueError("invalid_daemon_policy_kind")
    if kind == "normal":
        if present:
            raise ValueError("invalid_daemon_policy_environment")
        return load_normal_daemon_policy()
    if set(present) != set(_BOOTSTRAP_ENV_NAMES):
        raise ValueError("invalid_daemon_policy_environment")

    bootstrap_parent.accredit_registered_bootstrap_parent()
    manifest_handle = _parse_inherited_handle(
        present["DAYZ_MCP_BOOTSTRAP_POLICY_HANDLE"],
        error="invalid_bootstrap_policy_handle",
    )
    liveness_source = _parse_inherited_handle(
        present["DAYZ_MCP_BOOTSTRAP_LIVENESS_HANDLE"],
        error="invalid_bootstrap_liveness_handle",
    )
    liveness_duplicate = _duplicate_inherited_liveness_handle(liveness_source)
    try:
        policy = load_bootstrap_daemon_policy(
            manifest_handle,
            expected_security_build_id=present["DAYZ_MCP_SECURITY_BUILD_ID"],
        )
        policy.revalidate()
        object.__setattr__(policy, "_liveness_handle", liveness_duplicate)
        previous_hook = getattr(policy, "_revalidation_hook", None)

        def revalidate_with_liveness() -> None:
            if previous_hook is not None:
                previous_hook()
            if _GetFileType(wintypes.HANDLE(liveness_duplicate)) != _FILE_TYPE_PIPE:
                raise ValueError("daemon_policy_drift")

        object.__setattr__(
            policy, "_revalidation_hook", revalidate_with_liveness
        )
        weakref.finalize(
            policy, _CloseHandle, wintypes.HANDLE(liveness_duplicate)
        )
        return policy
    except BaseException:
        _CloseHandle(wintypes.HANDLE(liveness_duplicate))
        raise
