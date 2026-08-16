"""Read-only accreditation of registered native launcher executables."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from .native_pe import validate_x64_pe_stream

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes


NAME_SURROGATE_BIT = 0x20000000
_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_FILE_ID_RE = re.compile(r"^[0-9A-F]{32}$")
_CANONICAL_REGISTRY = Path(__file__).resolve().parents[1] / "approved-launchers.json"

_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_O_BINARY = os.O_BINARY if os.name == "nt" else 0

if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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


@dataclass(frozen=True)
class _FileIdentity:
    volume_serial_number: int
    file_id: str

    def to_payload(self) -> dict[str, object]:
        return {
            "volume_serial_number": self.volume_serial_number,
            "file_id": self.file_id,
        }


@dataclass
class _OpenedLauncher:
    launcher_id: str
    root: Path
    path: Path
    sha256: str
    root_identity: _FileIdentity
    file_identity: _FileIdentity
    _stream: BinaryIO
    _registry_lock: object | None = None

    def revalidate(self) -> None:
        _reject_name_surrogates(self.root, self.path)
        if _identity_from_stat(os.stat(self.root, follow_symlinks=False)) != (
            self.root_identity
        ):
            raise ValueError("launcher_root_identity_drift")
        if _identity_from_stat(os.fstat(self._stream.fileno())) != self.file_identity:
            raise ValueError("launcher_file_identity_drift")
        if not _same_path(self.path.resolve(strict=True), self.path):
            raise ValueError("launcher_handle_path_mismatch")
        if _sha256_stream(self._stream) != self.sha256:
            raise ValueError("launcher_hash_drift")

    def validate_native_pe(self) -> None:
        self.revalidate()
        validate_x64_pe_stream(self._stream)
        self.revalidate()

    def approve_root_debug_image(self, file_handle: int) -> bool:
        if type(file_handle) is not int or file_handle <= 0:
            return False
        try:
            from dayz_mcp.request_path_authority import (
                _file_identity,
                _final_handle_path,
            )

            self.revalidate()
            observed_identity = _file_identity(file_handle)
            observed_path = Path(_final_handle_path(file_handle))
            observed_stat_file_id = bytes.fromhex(
                observed_identity.file_id
            )[::-1].hex().upper()
            return (
                observed_identity.volume_serial_number
                == self.file_identity.volume_serial_number
                and observed_stat_file_id == self.file_identity.file_id
                and _same_path(observed_path, self.path)
            )
        except (OSError, OverflowError, ValueError):
            return False

    def require_unique_embedded_marker(self, marker: bytes) -> None:
        if type(marker) is not bytes or not 1 <= len(marker) <= 256:
            raise ValueError("launcher_embedded_marker_mismatch")
        self.revalidate()
        overlap = len(marker) - 1
        tail = b""
        occurrences = 0
        try:
            self._stream.seek(0)
            while True:
                chunk = self._stream.read(64 * 1024)
                if not chunk:
                    break
                data = tail + chunk
                scan_limit = len(data) - overlap
                start = 0
                while True:
                    offset = data.find(marker, start)
                    if offset < 0 or offset >= scan_limit:
                        break
                    occurrences += 1
                    if occurrences > 1:
                        raise ValueError("launcher_embedded_marker_mismatch")
                    start = offset + len(marker)
                tail = data[-overlap:] if overlap else b""
        finally:
            self._stream.seek(0)
        if occurrences != 1:
            raise ValueError("launcher_embedded_marker_mismatch")
        self.revalidate()

    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            if self._registry_lock is not None:
                self._registry_lock.close()  # type: ignore[attr-defined]
                self._registry_lock = None

    def __enter__(self) -> "_OpenedLauncher":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _identity_from_stat(info: os.stat_result) -> _FileIdentity:
    volume = int(info.st_dev)
    file_number = int(info.st_ino)
    if volume < 0 or file_number < 0 or file_number >= (1 << 128):
        raise ValueError("invalid_launcher_file_identity")
    return _FileIdentity(volume, f"{file_number:032X}")


def _sha256_stream(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest().upper()


def _open_pinned_read(path: Path) -> BinaryIO:
    if os.name != "nt":
        return path.open("rb")
    handle = _kernel32.CreateFileW(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "launcher_open_failed", str(path))
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | _O_BINARY)
    except BaseException:
        _kernel32.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _create_registry_entry_for_test(
    launcher_id: str, root: Path, relative_path: str
) -> dict[str, object]:
    canonical_root = root.resolve(strict=True)
    target = _resolve_relative(canonical_root, relative_path)
    _reject_name_surrogates(canonical_root, target)
    root_identity = _identity_from_stat(os.stat(canonical_root, follow_symlinks=False))
    with target.open("rb") as stream:
        digest = _sha256_stream(stream)
    return {
        "id": _validate_id(launcher_id),
        "root": str(canonical_root),
        "root_file_id": root_identity.to_payload(),
        "relative_path": str(PureWindowsPath(relative_path)),
        "sha256": digest,
    }


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("invalid_launcher_registry")
        value[key] = item
    return value


def _parse_launcher_registry(document: str) -> list[dict[str, object]]:
    try:
        payload = json.loads(document, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError("invalid_launcher_registry") from error
    return _validate_launcher_registry_payload(payload)


def _validate_launcher_registry_payload(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or set(payload) != {
        "format_version",
        "launchers",
    }:
        raise ValueError("invalid_launcher_registry")
    launchers = payload.get("launchers")
    version = payload.get("format_version")
    if type(version) is not int or version != 1 or not isinstance(launchers, list):
        raise ValueError("invalid_launcher_registry")
    validated = [_validate_entry(item) for item in launchers]
    ids = [str(item["id"]) for item in validated]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_launcher_id")
    return validated


def _read_canonical_registry() -> list[dict[str, object]]:
    path = _CANONICAL_REGISTRY
    try:
        _reject_path_name_surrogates(
            path, error_code="launcher_registry_name_surrogate"
        )
        if not _same_path(path.resolve(strict=True), path):
            raise ValueError("launcher_registry_name_surrogate")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            _reject_path_name_surrogates(
                path, error_code="launcher_registry_name_surrogate"
            )
            if not _same_path(path.resolve(strict=True), path):
                raise ValueError("launcher_registry_name_surrogate")
            lexical = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("invalid_launcher_registry")
            if _identity_from_stat(before) != _identity_from_stat(lexical):
                raise ValueError("launcher_registry_name_surrogate")
            document = stream.read()
            after = os.fstat(stream.fileno())
            if _identity_from_stat(before) != _identity_from_stat(after):
                raise ValueError("launcher_registry_identity_drift")
    except ValueError:
        raise
    except (OSError, UnicodeError) as error:
        raise ValueError("invalid_launcher_registry") from error
    try:
        text = document.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("invalid_launcher_registry") from error
    return _parse_launcher_registry(text)


def open_approved_launcher(launcher_id: str) -> _OpenedLauncher:
    from dayz_mcp.registry_lock import acquire_registry_lock

    registry_lock = acquire_registry_lock(exclusive=False)
    try:
        approved = _read_canonical_registry()
        validated_id = _validate_id(launcher_id)
        matches = [item for item in approved if item["id"] == validated_id]
        if len(matches) != 1:
            raise ValueError("launcher_not_approved")
        opened = _open_validated_entry(matches[0])
        opened._registry_lock = registry_lock
        return opened
    except BaseException:
        registry_lock.close()
        raise


def _open_registry_entry_for_test(approved: object) -> _OpenedLauncher:
    return _open_validated_entry(_validate_entry(approved))


def _open_validated_entry(entry: dict[str, object]) -> _OpenedLauncher:
    root = Path(str(entry["root"])).resolve(strict=True)
    if not _same_path(root, Path(str(entry["root"]))):
        raise ValueError("launcher_root_path_drift")
    target = _resolve_relative(root, str(entry["relative_path"]))
    _reject_name_surrogates(root, target)
    expected_root = _identity_from_payload(entry["root_file_id"])
    if _identity_from_stat(os.stat(root, follow_symlinks=False)) != expected_root:
        raise ValueError("launcher_root_identity_drift")
    stream = _open_pinned_read(target)
    try:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("launcher_requires_native_executable")
        file_identity = _identity_from_stat(os.fstat(stream.fileno()))
        if not _same_path(target.resolve(strict=True), target):
            raise ValueError("launcher_handle_path_mismatch")
        digest = _sha256_stream(stream)
        if digest != entry["sha256"]:
            raise ValueError("launcher_hash_drift")
        return _OpenedLauncher(
            str(entry["id"]),
            root,
            target,
            digest,
            expected_root,
            file_identity,
            stream,
        )
    except BaseException:
        stream.close()
        raise


def _validate_entry(value: object) -> dict[str, object]:
    keys = {"id", "root", "root_file_id", "relative_path", "sha256"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid_launcher_entry")
    launcher_id = _validate_id(value.get("id"))
    root = value.get("root")
    relative = value.get("relative_path")
    sha256 = value.get("sha256")
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise ValueError("invalid_launcher_root")
    if not isinstance(relative, str):
        raise ValueError("invalid_launcher_relative_path")
    _validate_relative(relative)
    if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None:
        raise ValueError("invalid_launcher_sha256")
    identity = _identity_from_payload(value.get("root_file_id"))
    return {
        "id": launcher_id,
        "root": root,
        "root_file_id": identity.to_payload(),
        "relative_path": relative,
        "sha256": sha256,
    }


def _validate_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 120
        or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value) is None
    ):
        raise ValueError("invalid_launcher_id")
    return value


def _identity_from_payload(value: object) -> _FileIdentity:
    if not isinstance(value, dict) or set(value) != {
        "volume_serial_number",
        "file_id",
    }:
        raise ValueError("invalid_launcher_file_identity")
    volume = value.get("volume_serial_number")
    file_id = value.get("file_id")
    if (
        type(volume) is not int
        or volume < 0
        or not isinstance(file_id, str)
        or _FILE_ID_RE.fullmatch(file_id) is None
    ):
        raise ValueError("invalid_launcher_file_identity")
    return _FileIdentity(volume, file_id)


def _resolve_relative(root: Path, relative_path: str) -> Path:
    relative = _validate_relative(relative_path)
    lexical = root.joinpath(*relative.parts)
    _reject_name_surrogates(root, lexical)
    candidate = lexical.resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("launcher_path_escape") from error
    return candidate


def _validate_relative(value: str) -> PureWindowsPath:
    relative = PureWindowsPath(value)
    if (
        not value
        or relative.is_absolute()
        or relative.drive
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("invalid_launcher_relative_path")
    if relative.suffix.casefold() != ".exe":
        raise ValueError("launcher_requires_native_executable")
    return relative


def _reject_path_name_surrogates(path: Path, *, error_code: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        info = os.lstat(current)
        tag = int(info.st_reparse_tag if hasattr(info, "st_reparse_tag") else 0)
        if tag & NAME_SURROGATE_BIT:
            raise ValueError(error_code)


def _reject_name_surrogates(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("launcher_path_escape") from error
    _reject_path_name_surrogates(root, error_code="launcher_name_surrogate_rejected")
    relative = target.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        info = os.lstat(current)
        tag = int(info.st_reparse_tag if hasattr(info, "st_reparse_tag") else 0)
        if tag & NAME_SURROGATE_BIT:
            raise ValueError("launcher_name_surrogate_rejected")


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
        os.path.abspath(second)
    )
