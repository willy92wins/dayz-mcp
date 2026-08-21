"""Pin and accredit filesystem objects selected by a canonical dayz-test request."""

from __future__ import annotations

import ctypes
import ntpath
import os
import re
import unicodedata
from ctypes import wintypes
from dataclasses import dataclass
from typing import Mapping

from dayz_mcp.dayz_test_request import ParsedDayzTestRequest, RequestProjectPolicy
from dayz_mcp.win32_fileinfo import FILE_STANDARD_INFO as _FILE_STANDARD_INFO
from dayz_mcp.win32_fileinfo import bind_common_kernel32


_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_TYPE_DISK = 0x0001
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_FILE_STANDARD_INFO_CLASS = 1
_FILE_ID_INFO_CLASS = 18
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_DRIVE_REMOTE = 4
_FILE_ID_RE = re.compile(r"^[0-9A-F]{32}$")
_MISSION_ALIASES = frozenset({"chernarus", "livonia", "sakhal"})


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


_kernel32 = bind_common_kernel32()


@dataclass(frozen=True)
class PathIdentity:
    volume_serial_number: int
    file_id: str


@dataclass(frozen=True)
class SealedPathRoot:
    path: str
    identity: PathIdentity
    handle_path: str
    resolved_path: str
    resolved_identity: PathIdentity
    root_reparse_tag: int
    allow_root_junction: bool = False


@dataclass(frozen=True)
class SealedRequestProjectPolicy:
    policy: RequestProjectPolicy
    dev_root: SealedPathRoot
    default_source: SealedPathRoot
    mission_roots: tuple[SealedPathRoot, ...]
    mod_roots: tuple[SealedPathRoot, ...]


@dataclass
class _PinnedDirectory:
    path: str
    final_path: str
    identity: PathIdentity
    reparse_tag: int
    handle: int

    def close(self) -> None:
        if self.handle:
            _kernel32.CloseHandle(self.handle)
            self.handle = 0


class AccreditedRequestPaths:
    def __init__(
        self,
        identities: Mapping[str, tuple[PathIdentity, ...]],
        handles: list[_PinnedDirectory],
    ) -> None:
        self.identities = dict(identities)
        self._handles = handles
        self.closed = False

    @property
    def handle_count(self) -> int:
        return sum(item.handle != 0 for item in self._handles)

    def close(self) -> None:
        if self.closed:
            return
        for item in reversed(self._handles):
            item.close()
        self.closed = True

    def __enter__(self) -> "AccreditedRequestPaths":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _invalid() -> None:
    raise ValueError("invalid_dayz_test_path_authority")


def _normal_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value))


def _same_path(first: str, second: str) -> bool:
    return _normal_path(first) == _normal_path(second)


def _local_canonical_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 3 <= len(value) <= 520
        or "\0" in value
        or not unicodedata.is_normalized("NFC", value)
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        or ntpath.normpath(value) != value
    ):
        _invalid()
    drive, tail = ntpath.splitdrive(value)
    if (
        len(drive) != 2
        or drive[1] != ":"
        or not drive[0].isascii()
        or not drive[0].isalpha()
        or not tail.startswith("\\")
        or ":" in tail
        or _kernel32.GetDriveTypeW(drive + "\\") == _DRIVE_REMOTE
    ):
        _invalid()
    return value


def _final_handle_path(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(
        _kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    )
    if length <= 0 or length >= len(buffer):
        _invalid()
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        _invalid()
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return _local_canonical_path(ntpath.normpath(value))


def _file_identity(handle: int) -> PathIdentity:
    info = _FILE_ID_INFO()
    if not _kernel32.GetFileInformationByHandleEx(
        handle,
        _FILE_ID_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        _invalid()
    return PathIdentity(
        volume_serial_number=int(info.VolumeSerialNumber),
        file_id=bytes(info.FileId).hex().upper(),
    )


def _open_directory(path: str, *, follow_root_reparse: bool) -> _PinnedDirectory:
    canonical = _local_canonical_path(path)
    flags = _FILE_FLAG_BACKUP_SEMANTICS
    if not follow_root_reparse:
        flags |= _FILE_FLAG_OPEN_REPARSE_POINT
    handle = _kernel32.CreateFileW(
        canonical,
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        _invalid()
    numeric = int(handle)
    try:
        if _kernel32.GetFileType(numeric) != _FILE_TYPE_DISK:
            _invalid()
        attributes = _FILE_ATTRIBUTE_TAG_INFO()
        standard = _FILE_STANDARD_INFO()
        if not _kernel32.GetFileInformationByHandleEx(
            numeric,
            _FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(attributes),
            ctypes.sizeof(attributes),
        ) or not _kernel32.GetFileInformationByHandleEx(
            numeric,
            _FILE_STANDARD_INFO_CLASS,
            ctypes.byref(standard),
            ctypes.sizeof(standard),
        ):
            _invalid()
        if not standard.Directory or standard.DeletePending:
            _invalid()
        reparse_tag = (
            int(attributes.ReparseTag)
            if attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
            else 0
        )
        return _PinnedDirectory(
            path=canonical,
            final_path=_final_handle_path(numeric),
            identity=_file_identity(numeric),
            reparse_tag=reparse_tag,
            handle=numeric,
        )
    except BaseException:
        _kernel32.CloseHandle(numeric)
        raise


def _prefixes(path: str) -> tuple[str, ...]:
    drive, tail = ntpath.splitdrive(path)
    current = drive + "\\"
    values: list[str] = []
    for part in (item for item in tail.split("\\") if item):
        current = ntpath.join(current, part)
        values.append(current)
    if not values:
        _invalid()
    return tuple(values)


def _validate_identity(value: object) -> PathIdentity:
    if (
        type(value) is not PathIdentity
        or type(value.volume_serial_number) is not int
        or value.volume_serial_number < 0
        or _FILE_ID_RE.fullmatch(value.file_id) is None
    ):
        _invalid()
    return value


def _validate_sealed_root(root: object) -> SealedPathRoot:
    if type(root) is not SealedPathRoot:
        _invalid()
    _local_canonical_path(root.path)
    _local_canonical_path(root.handle_path)
    _local_canonical_path(root.resolved_path)
    _validate_identity(root.identity)
    _validate_identity(root.resolved_identity)
    if type(root.allow_root_junction) is not bool or type(root.root_reparse_tag) is not int:
        _invalid()
    if root.allow_root_junction:
        if root.root_reparse_tag != _IO_REPARSE_TAG_MOUNT_POINT:
            _invalid()
    elif root.root_reparse_tag != 0 or not _same_path(
        root.handle_path, root.resolved_path
    ):
        _invalid()
    return root


def _open_sealed_root(root: SealedPathRoot) -> list[_PinnedDirectory]:
    root = _validate_sealed_root(root)
    opened: list[_PinnedDirectory] = []
    try:
        prefixes = _prefixes(root.path)
        for index, prefix in enumerate(prefixes):
            item = _open_directory(prefix, follow_root_reparse=False)
            opened.append(item)
            is_leaf = index == len(prefixes) - 1
            if not is_leaf and item.reparse_tag != 0:
                _invalid()
            if is_leaf:
                if item.reparse_tag != root.root_reparse_tag:
                    _invalid()
                if item.identity != root.identity or not _same_path(
                    item.final_path, root.handle_path
                ):
                    _invalid()
        resolved = _open_directory(root.path, follow_root_reparse=True)
        opened.append(resolved)
        if (
            resolved.identity != root.resolved_identity
            or not _same_path(resolved.final_path, root.resolved_path)
        ):
            _invalid()
        return opened
    except BaseException:
        for item in reversed(opened):
            item.close()
        raise


def _capture_root_for_test(path: str, *, allow_root_junction: bool) -> SealedPathRoot:
    canonical = _local_canonical_path(path)
    opened: list[_PinnedDirectory] = []
    try:
        prefixes = _prefixes(canonical)
        leaf: _PinnedDirectory | None = None
        for index, prefix in enumerate(prefixes):
            item = _open_directory(prefix, follow_root_reparse=False)
            opened.append(item)
            is_leaf = index == len(prefixes) - 1
            if not is_leaf and item.reparse_tag != 0:
                _invalid()
            if is_leaf:
                leaf = item
        if leaf is None:
            _invalid()
        if allow_root_junction:
            if leaf.reparse_tag != _IO_REPARSE_TAG_MOUNT_POINT:
                _invalid()
        elif leaf.reparse_tag != 0:
            _invalid()
        resolved = _open_directory(canonical, follow_root_reparse=True)
        opened.append(resolved)
        return SealedPathRoot(
            path=canonical,
            identity=leaf.identity,
            handle_path=leaf.final_path,
            resolved_path=resolved.final_path,
            resolved_identity=resolved.identity,
            root_reparse_tag=leaf.reparse_tag,
            allow_root_junction=allow_root_junction,
        )
    finally:
        for item in reversed(opened):
            item.close()


def _seal_project_policy_for_test(
    policy: RequestProjectPolicy,
    *,
    allow_mod_root_junctions: tuple[str, ...] = (),
) -> SealedRequestProjectPolicy:
    if type(policy) is not RequestProjectPolicy or type(allow_mod_root_junctions) is not tuple:
        _invalid()
    allowed = {_normal_path(_local_canonical_path(path)) for path in allow_mod_root_junctions}
    mod_roots = tuple(
        _capture_root_for_test(
            path,
            allow_root_junction=_normal_path(path) in allowed,
        )
        for path in policy.mod_roots
    )
    if allowed != {
        _normal_path(item.path) for item in mod_roots if item.allow_root_junction
    }:
        _invalid()
    return SealedRequestProjectPolicy(
        policy=policy,
        dev_root=_capture_root_for_test(policy.dev_root, allow_root_junction=False),
        default_source=_capture_root_for_test(
            policy.default_source, allow_root_junction=False
        ),
        mission_roots=tuple(
            _capture_root_for_test(path, allow_root_junction=False)
            for path in policy.mission_roots
        ),
        mod_roots=mod_roots,
    )


def _validate_sealed_policy(value: object) -> SealedRequestProjectPolicy:
    if type(value) is not SealedRequestProjectPolicy or type(value.policy) is not RequestProjectPolicy:
        _invalid()
    if value.dev_root.path != value.policy.dev_root:
        _invalid()
    if value.default_source.path != value.policy.default_source:
        _invalid()
    if tuple(item.path for item in value.mission_roots) != value.policy.mission_roots:
        _invalid()
    if tuple(item.path for item in value.mod_roots) != value.policy.mod_roots:
        _invalid()
    _validate_sealed_root(value.dev_root)
    _validate_sealed_root(value.default_source)
    for roots in (value.mission_roots, value.mod_roots):
        if type(roots) is not tuple or not roots:
            _invalid()
        for root in roots:
            _validate_sealed_root(root)
    return value


def _contains(path: str, root: str) -> bool:
    try:
        return ntpath.commonpath((_normal_path(path), _normal_path(root))) == _normal_path(root)
    except ValueError:
        return False


def _open_descendant(
    path: str,
    root: SealedPathRoot,
) -> tuple[PathIdentity, list[_PinnedDirectory]]:
    canonical = _local_canonical_path(path)
    if not _contains(canonical, root.path):
        _invalid()
    relative = ntpath.relpath(canonical, root.path)
    if relative == ".":
        return root.resolved_identity, []
    if relative.startswith("..\\") or relative == "..":
        _invalid()
    opened: list[_PinnedDirectory] = []
    current = root.path
    expected = root.resolved_path
    try:
        parts = relative.split("\\")
        for index, part in enumerate(parts):
            if part in {"", ".", ".."}:
                _invalid()
            current = ntpath.join(current, part)
            expected = ntpath.join(expected, part)
            item = _open_directory(current, follow_root_reparse=False)
            opened.append(item)
            is_leaf = index == len(parts) - 1
            if not _same_path(item.final_path, expected):
                _invalid()
            if not is_leaf:
                if item.reparse_tag != 0:
                    _invalid()
                continue
            # Leaf mount-point is allowed only when the sealed root itself is a
            # junction. Reopen without OPEN_REPARSE_POINT to pin the target.
            if root.allow_root_junction:
                if item.reparse_tag not in {0, _IO_REPARSE_TAG_MOUNT_POINT}:
                    _invalid()
            elif item.reparse_tag != 0:
                _invalid()
            if item.reparse_tag == _IO_REPARSE_TAG_MOUNT_POINT:
                resolved = _open_directory(current, follow_root_reparse=True)
                opened.append(resolved)
                return resolved.identity, opened
        return opened[-1].identity, opened
    except BaseException:
        for item in reversed(opened):
            item.close()
        raise


def _root_candidates(path: str, roots: tuple[SealedPathRoot, ...]) -> tuple[SealedPathRoot, ...]:
    return tuple(root for root in roots if _contains(path, root.path))


def _accredit_absolute(
    path: str,
    roots: tuple[SealedPathRoot, ...],
) -> tuple[PathIdentity, list[_PinnedDirectory]]:
    candidates = _root_candidates(path, roots)
    if len(candidates) != 1:
        _invalid()
    return _open_descendant(path, candidates[0])


def _accredit_mod(
    value: str,
    roots: tuple[SealedPathRoot, ...],
) -> tuple[PathIdentity, list[_PinnedDirectory]]:
    if ntpath.isabs(value):
        return _accredit_absolute(value, roots)
    matches: list[tuple[PathIdentity, list[_PinnedDirectory]]] = []
    for root in roots:
        try:
            matches.append(_open_descendant(ntpath.join(root.path, value), root))
        except ValueError:
            continue
    if len(matches) != 1:
        for _identity, handles in matches:
            for item in reversed(handles):
                item.close()
        _invalid()
    return matches[0]


def _accredit_mod_list(
    values: object,
    roots: tuple[SealedPathRoot, ...],
) -> tuple[tuple[PathIdentity, ...], list[_PinnedDirectory]]:
    if not isinstance(values, list):
        _invalid()
    identities: list[PathIdentity] = []
    handles: list[_PinnedDirectory] = []
    try:
        for value in values:
            if not isinstance(value, str):
                _invalid()
            identity, opened = _accredit_mod(value, roots)
            if identity in identities:
                for item in reversed(opened):
                    item.close()
                _invalid()
            identities.append(identity)
            handles.extend(opened)
        return tuple(identities), handles
    except BaseException:
        for item in reversed(handles):
            item.close()
        raise


def accredit_request_paths(
    parsed: ParsedDayzTestRequest,
    *,
    policies: tuple[SealedRequestProjectPolicy, ...],
) -> AccreditedRequestPaths:
    handles: list[_PinnedDirectory] = []
    try:
        if type(parsed) is not ParsedDayzTestRequest or type(policies) is not tuple:
            _invalid()
        validated = tuple(_validate_sealed_policy(item) for item in policies)
        if not 1 <= len(validated) <= 128:
            _invalid()
        selected = tuple(
            item
            for item in validated
            if item.policy.mod == parsed.payload.get("mod")
            and item.policy.dev_root == parsed.payload.get("dev_root")
        )
        if len(selected) != 1:
            _invalid()
        policy = selected[0]

        all_roots = (
            policy.dev_root,
            policy.default_source,
            *policy.mission_roots,
            *policy.mod_roots,
        )
        for root in all_roots:
            handles.extend(_open_sealed_root(root))

        identities: dict[str, tuple[PathIdentity, ...]] = {
            "dev_root": (policy.dev_root.resolved_identity,)
        }
        source_identity, opened = _accredit_absolute(
            str(parsed.payload.get("source")), (policy.default_source,)
        )
        handles.extend(opened)
        identities["source"] = (source_identity,)

        mission = parsed.payload.get("mission")
        if not isinstance(mission, str):
            _invalid()
        if mission in _MISSION_ALIASES:
            identities["mission"] = ()
        else:
            mission_identity, opened = _accredit_absolute(
                mission, policy.mission_roots
            )
            handles.extend(opened)
            identities["mission"] = (mission_identity,)

        for field in ("base_mods", "extra_mods", "server_mods"):
            field_identities, opened = _accredit_mod_list(
                parsed.payload.get(field), policy.mod_roots
            )
            handles.extend(opened)
            identities[field] = field_identities
        return AccreditedRequestPaths(identities, handles)
    except (OSError, TypeError, ValueError):
        for item in reversed(handles):
            item.close()
        raise ValueError("invalid_dayz_test_path_authority") from None
