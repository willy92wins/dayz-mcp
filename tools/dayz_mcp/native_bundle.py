"""Pinned, fail-closed verification of a registered native launcher bundle."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import stat
import zipfile
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

from dayz_mcp.dayz_test_request import RequestProjectPolicy
from dayz_mcp.native_broker_protocol import BrokerKind
from dayz_mcp.dayz_tools_paths import (
    addon_builder_exe,
    addon_helper_exes,
    external_file_paths,
    selected_layout,
)
from dayz_mcp.native_child_announcement import ChildAnnouncement
from dayz_mcp.launcher_registry import (
    _OpenedLauncher,
    _open_pinned_read,
    _reject_name_surrogates,
    _reject_path_name_surrogates,
    _same_path,
)
from dayz_mcp.request_path_authority import (
    PathIdentity,
    SealedPathRoot,
    SealedRequestProjectPolicy,
    _file_identity,
    _final_handle_path,
    _validate_sealed_policy,
)


_MAX_MANIFEST_BYTES = 1_048_576
_MAX_POLICY_BYTES = 65_536
_HEX = frozenset("0123456789ABCDEF")
_MANIFEST_KEYS = frozenset(
    {
        "bundle_id",
        "dayz_test_readiness_sha256",
        "dayz_test_request_sha256",
        "dayz_test_worker_sha256",
        "entries",
        "format_version",
        "native_broker_protocol_sha256",
        "request_policy_sha256",
        "worker_runtime_sha256",
    }
)
_HASHED_MODULES = {
    "dayz_test_readiness_sha256": "dayz_test_readiness.py",
    "dayz_test_request_sha256": "dayz_test_request.py",
    "dayz_test_worker_sha256": "dayz_test_worker.py",
    "native_broker_protocol_sha256": "native_broker_protocol.py",
}
_APP_PACKAGED_MODULES = frozenset(
    {
        "accredited_daemon_transport.py",
        "daemon_contract.py",
        "daemon_policy_contract.py",
        "dayz_test_readiness.py",
        "dayz_test_request.py",
        "dayz_test_worker.py",
        "host_config.py",
        "native_broker_protocol.py",
        "native_process_guard.py",
        "native_process_snapshot.py",
        "normal_daemon_policy.py",
        "pinned_keyfile.py",
        "server_cli.py",
        "win32_fileinfo.py",
    }
)
_APP_MEMBERS = frozenset(
    {
        "__main__.py",
        "dayz_mcp/__init__.py",
        *(f"dayz_mcp/{name}" for name in _APP_PACKAGED_MODULES),
    }
)
_SPECIAL_BUNDLE_FILES = frozenset(
    {
        "closure-manifest.json",
        "dayz-test-launcher.exe",
        "reproducibility.json",
    }
)
_FINGERPRINT_KEYS = frozenset(
    {
        "app_pyz_sha256",
        "manifest_sha256",
        "pe_sha256",
        "request_policy_sha256",
    }
)
_REPRODUCIBILITY_MODES = ("clean-1", "clean-2", "offline")
def _addon_builder_path() -> str:
    return addon_builder_exe()


def _addon_helper_paths() -> frozenset[str]:
    return frozenset(ntpath.normcase(path) for path in addon_helper_exes())


def _external_paths() -> frozenset[str]:
    return frozenset(
        ntpath.normcase(str(path)) for path in external_file_paths(selected_layout())
    )

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetSystemDirectoryW.argtypes = (
    wintypes.LPWSTR,
    wintypes.UINT,
)
_kernel32.GetSystemDirectoryW.restype = wintypes.UINT


def _system_directory() -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(_kernel32.GetSystemDirectoryW(buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        _invalid()
    return ntpath.normcase(ntpath.normpath(buffer.value))


def _inside_directory(path: str, root: str) -> bool:
    normalized_path = ntpath.normcase(ntpath.normpath(path))
    normalized_root = ntpath.normcase(ntpath.normpath(root))
    try:
        return ntpath.commonpath((normalized_path, normalized_root)) == normalized_root
    except ValueError:
        return False


def _is_trusted_winsxs_common_controls(path: str, windows_directory: str) -> bool:
    winsxs_root = ntpath.join(windows_directory, "WinSxS")
    if not _inside_directory(path, winsxs_root):
        return False
    relative = ntpath.relpath(ntpath.normpath(path), ntpath.normpath(winsxs_root))
    parts = PureWindowsPath(relative).parts
    if len(parts) != 2 or parts[1].casefold() != "comctl32.dll":
        return False
    assembly = parts[0].casefold()
    prefix = "x86_microsoft.windows.common-controls_"
    if not assembly.startswith(prefix):
        return False
    version_identity = assembly[len(prefix) :]
    return bool(version_identity) and all(
        character.isascii() and (character.isalnum() or character in "._-")
        for character in version_identity
    )


def _is_trusted_gac_microsoft_visual_basic(path: str, windows_directory: str) -> bool:
    expected = ntpath.join(
        windows_directory,
        "Microsoft.NET",
        "assembly",
        "GAC_MSIL",
        "Microsoft.VisualBasic",
        "v4.0_10.0.0.0__b03f5f7f11d50a3a",
        "Microsoft.VisualBasic.dll",
    )
    return ntpath.normcase(ntpath.normpath(path)) == ntpath.normcase(
        ntpath.normpath(expected)
    )


@dataclass(frozen=True)
class DebugProcessDescriptor:
    kind: BrokerKind
    announced_path: str
    final_path: str
    image_sha256: str
    identity: PathIdentity


@dataclass(frozen=True)
class DebugAddonHelperDescriptor:
    final_path: str
    identity: PathIdentity


@dataclass(frozen=True)
class DebugImageAuthority:
    process_identities: frozenset[PathIdentity]
    module_identities: frozenset[PathIdentity]
    system_directory: str
    process_descriptors: tuple[DebugProcessDescriptor, ...] = ()
    addon_helper_descriptors: tuple[DebugAddonHelperDescriptor, ...] = ()

    def approve_debug_image(self, file_handle: int, *, event_kind: str) -> bool:
        if type(file_handle) is not int or file_handle <= 0:
            return False
        try:
            identity = _file_identity(file_handle)
            path = _final_handle_path(file_handle)
        except (OSError, ValueError):
            return False
        suffix = PureWindowsPath(path).suffix.lower()
        if event_kind == "CREATE_PROCESS":
            return suffix == ".exe" and identity in self.process_identities
        if event_kind != "LOAD_DLL" or suffix not in {".dll", ".pyd"}:
            return False
        windows_directory = PureWindowsPath(self.system_directory).parent
        trusted_system_directories = (
            self.system_directory,
            str(windows_directory / "SysWOW64"),
            str(windows_directory / "Microsoft.NET" / "Framework" / "v4.0.30319"),
            str(windows_directory / "Microsoft.NET" / "Framework64" / "v4.0.30319"),
            str(windows_directory / "assembly" / "NativeImages_v4.0.30319_32"),
            str(windows_directory / "assembly" / "NativeImages_v4.0.30319_64"),
        )
        return (
            identity in self.module_identities
            or any(
                _inside_directory(path, directory)
                for directory in trusted_system_directories
            )
            or _is_trusted_winsxs_common_controls(path, str(windows_directory))
            or _is_trusted_gac_microsoft_visual_basic(path, str(windows_directory))
        )

    def approve_announced_process(
        self,
        file_handle: int,
        announcement: object,
    ) -> bool:
        if type(file_handle) is not int or file_handle <= 0 or type(announcement) is not ChildAnnouncement:
            return False
        try:
            identity = _file_identity(file_handle)
            final_path = _final_handle_path(file_handle)
        except (OSError, ValueError):
            return False
        if identity != announcement.identity:
            return False
        normalized_final = ntpath.normcase(ntpath.normpath(final_path))
        return any(
            descriptor.kind is announcement.kind
            and descriptor.announced_path == announcement.announced_path
            and descriptor.image_sha256 == announcement.image_sha256
            and descriptor.identity == identity
            and ntpath.normcase(ntpath.normpath(descriptor.final_path)) == normalized_final
            for descriptor in self.process_descriptors
        )

    def approve_addon_helper_process(self, file_handle: int) -> bool:
        if type(file_handle) is not int or file_handle <= 0:
            return False
        try:
            identity = _file_identity(file_handle)
            final_path = _final_handle_path(file_handle)
        except (OSError, ValueError):
            return False
        normalized_final = ntpath.normcase(ntpath.normpath(final_path))
        return any(
            descriptor.identity == identity
            and ntpath.normcase(ntpath.normpath(descriptor.final_path))
            == normalized_final
            for descriptor in self.addon_helper_descriptors
        )


@dataclass
class VerifiedNativeBundle:
    sealed_policies: tuple[SealedRequestProjectPolicy, ...]
    manifest_sha256: str
    debug_image_authority: DebugImageAuthority
    _streams: list[BinaryIO]

    def close(self) -> None:
        while self._streams:
            self._streams.pop().close()

    def __enter__(self) -> "VerifiedNativeBundle":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _invalid() -> None:
    raise ValueError("invalid_native_launcher_bundle")


def _closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _canonical_json(raw: bytes, *, maximum: int) -> dict[str, object]:
    if not 1 <= len(raw) <= maximum or raw.startswith(b"\xef\xbb\xbf"):
        _invalid()
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_closed_object,
            parse_constant=lambda _value: _invalid(),
        )
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError("invalid_native_launcher_bundle") from error
    if type(value) is not dict:
        _invalid()
    canonical = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    if raw != canonical:
        _invalid()
    return value


def _valid_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _valid_file_id(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 32
        and all(character in _HEX for character in value)
    )


def _sha256_stream(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest().upper()


def _read_bounded(stream: BinaryIO, maximum: int) -> bytes:
    stream.seek(0)
    raw = stream.read(maximum + 1)
    stream.seek(0)
    if type(raw) is not bytes or len(raw) > maximum:
        _invalid()
    return raw


def _manifest_file_id(info: os.stat_result) -> str:
    file_number = int(info.st_ino)
    if file_number < 0 or file_number >= (1 << 128):
        _invalid()
    return file_number.to_bytes(16, "little").hex().upper()


def _validate_identity(value: object, info: os.stat_result) -> None:
    if (
        type(value) is not dict
        or set(value) != {"file_id", "volume_serial_number"}
        or type(value.get("volume_serial_number")) is not int
        or value.get("volume_serial_number") != int(info.st_dev)
        or value.get("file_id") != _manifest_file_id(info)
    ):
        _invalid()


def _validate_bundle_relative(value: object) -> str:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        _invalid()
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _invalid()
    canonical = path.as_posix()
    if canonical != value or canonical in _SPECIAL_BUNDLE_FILES:
        _invalid()
    return canonical


def _validate_external_path(value: object) -> Path:
    if type(value) is not str or not value or value != ntpath.normpath(value):
        _invalid()
    pure = PureWindowsPath(value)
    if (
        not pure.is_absolute()
        or len(pure.drive) != 2
        or value.startswith(("\\\\", "\\?\\", "\\.\\"))
        or ntpath.normcase(value) not in _external_paths()
    ):
        _invalid()
    return Path(value)


def _open_verified_file(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    root: Path | None,
    identity: object | None,
) -> BinaryIO:
    if root is None:
        _reject_path_name_surrogates(path, error_code="invalid_native_launcher_bundle")
    else:
        _reject_name_surrogates(root, path)
    stream = _open_pinned_read(path)
    try:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or int(info.st_nlink) != 1
            or int(info.st_size) != expected_size
            or not _same_path(path.resolve(strict=True), path)
            or _sha256_stream(stream) != expected_sha256
        ):
            _invalid()
        if identity is not None:
            _validate_identity(identity, info)
        return stream
    except BaseException:
        stream.close()
        raise


def _parse_manifest(value: object) -> tuple[list[dict[str, object]], dict[str, str]]:
    if (
        type(value) is not dict
        or set(value) != _MANIFEST_KEYS
        or value.get("format_version") != 1
        or value.get("bundle_id") != "dayz-test-v1"
    ):
        _invalid()
    hashes = {
        key: value.get(key)  # type: ignore[dict-item]
        for key in (
            *_HASHED_MODULES,
            "request_policy_sha256",
            "worker_runtime_sha256",
        )
    }
    if any(not _valid_hash(item) for item in hashes.values()):
        _invalid()
    entries = value.get("entries")
    if type(entries) is not list or not entries:
        _invalid()
    validated: list[dict[str, object]] = []
    identities: list[tuple[str, str]] = []
    for item in entries:
        if type(item) is not dict or item.get("kind") not in {"bundle", "external"}:
            _invalid()
        kind = item["kind"]
        expected_keys = {"kind", "path", "sha256", "size"}
        if kind == "external":
            expected_keys.add("identity")
        if (
            set(item) != expected_keys
            or not _valid_hash(item.get("sha256"))
            or type(item.get("size")) is not int
            or not 0 <= item["size"] <= (1 << 34)
        ):
            _invalid()
        path = (
            _validate_bundle_relative(item.get("path"))
            if kind == "bundle"
            else str(_validate_external_path(item.get("path")))
        )
        identities.append((kind, path))
        validated.append({**item, "path": path})
    if (
        identities != sorted(identities, key=lambda item: (item[0], item[1].casefold()))
        or len({(kind, path.casefold()) for kind, path in identities}) != len(identities)
        or {ntpath.normcase(path) for kind, path in identities if kind == "external"}
        != _external_paths()
    ):
        _invalid()
    return validated, hashes


def _root_from_payload(value: object) -> SealedPathRoot:
    if type(value) is not dict or set(value) != {
        "allow_root_junction",
        "handle_path",
        "identity",
        "path",
        "resolved_identity",
        "resolved_path",
        "root_reparse_tag",
    }:
        _invalid()

    def identity(raw: object) -> PathIdentity:
        if (
            type(raw) is not dict
            or set(raw) != {"file_id", "volume_serial_number"}
            or not _valid_file_id(raw.get("file_id"))
            or type(raw.get("volume_serial_number")) is not int
            or raw["volume_serial_number"] < 0
        ):
            _invalid()
        return PathIdentity(raw["volume_serial_number"], raw["file_id"])

    root = SealedPathRoot(
        path=value["path"],
        identity=identity(value["identity"]),
        handle_path=value["handle_path"],
        resolved_path=value["resolved_path"],
        resolved_identity=identity(value["resolved_identity"]),
        root_reparse_tag=value["root_reparse_tag"],
        allow_root_junction=value["allow_root_junction"],
    )
    return root


def _parse_policy(value: object) -> tuple[SealedRequestProjectPolicy, ...]:
    if (
        type(value) is not dict
        or set(value) != {"format_version", "projects"}
        or value.get("format_version") != 1
        or type(value.get("projects")) is not list
        or not 1 <= len(value["projects"]) <= 128
    ):
        _invalid()
    policies: list[SealedRequestProjectPolicy] = []
    identities: set[tuple[str, str]] = set()
    for project in value["projects"]:
        if type(project) is not dict or set(project) != {
            "default_base_mods",
            "default_source",
            "dev_root",
            "mission_roots",
            "mod",
            "mod_roots",
        }:
            _invalid()
        if (
            type(project["mod"]) is not str
            or type(project["default_base_mods"]) is not list
            or any(type(item) is not str or not item for item in project["default_base_mods"])
            or any(
                type(project[key]) is not list or not project[key]
                for key in ("mission_roots", "mod_roots")
            )
        ):
            _invalid()
        dev_root = _root_from_payload(project["dev_root"])
        default_source = _root_from_payload(project["default_source"])
        mission_roots = tuple(_root_from_payload(item) for item in project["mission_roots"])
        mod_roots = tuple(_root_from_payload(item) for item in project["mod_roots"])
        public = RequestProjectPolicy(
            mod=project["mod"],
            dev_root=dev_root.path,
            default_source=default_source.path,
            default_base_mods=tuple(project["default_base_mods"]),
            mission_roots=tuple(item.path for item in mission_roots),
            mod_roots=tuple(item.path for item in mod_roots),
        )
        sealed = SealedRequestProjectPolicy(
            policy=public,
            dev_root=dev_root,
            default_source=default_source,
            mission_roots=mission_roots,
            mod_roots=mod_roots,
        )
        try:
            _validate_sealed_policy(sealed)
        except ValueError as error:
            raise ValueError("invalid_native_launcher_bundle") from error
        key = (public.mod.casefold(), ntpath.normcase(public.dev_root))
        if key in identities:
            _invalid()
        identities.add(key)
        policies.append(sealed)
    return tuple(policies)


def _verify_app(stream: BinaryIO, source_bytes: dict[str, bytes]) -> None:
    try:
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if (
                names != sorted(names)
                or frozenset(names) != _APP_MEMBERS
                or len(names) != len(set(names))
                or any(item.date_time != (1980, 1, 1, 0, 0, 0) for item in infos)
            ):
                _invalid()
            for module, raw in source_bytes.items():
                if archive.read(f"dayz_mcp/{module}") != raw:
                    _invalid()
    except (KeyError, OSError, zipfile.BadZipFile, RuntimeError, ValueError) as error:
        raise ValueError("invalid_native_launcher_bundle") from error
    finally:
        stream.seek(0)


def load_verified_bundle(opened_launcher: object) -> VerifiedNativeBundle:
    if type(opened_launcher) is not _OpenedLauncher:
        _invalid()
    opened_launcher.revalidate()
    root = opened_launcher.root
    streams: list[BinaryIO] = []
    try:
        manifest_path = root / "closure-manifest.json"
        _reject_name_surrogates(root, manifest_path)
        manifest_stream = _open_pinned_read(manifest_path)
        streams.append(manifest_stream)
        manifest_info = os.fstat(manifest_stream.fileno())
        if (
            not stat.S_ISREG(manifest_info.st_mode)
            or int(manifest_info.st_nlink) != 1
            or not _same_path(manifest_path.resolve(strict=True), manifest_path)
        ):
            _invalid()
        manifest_raw = _read_bounded(manifest_stream, _MAX_MANIFEST_BYTES)
        manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest().upper()
        manifest = _canonical_json(manifest_raw, maximum=_MAX_MANIFEST_BYTES)
        entries, declared_hashes = _parse_manifest(manifest)

        opened_by_relative: dict[str, BinaryIO] = {}
        bundle_hashes: dict[str, str] = {}
        expected_bundle: set[str] = set()
        process_identities: set[PathIdentity] = set()
        module_identities: set[PathIdentity] = set()
        process_descriptors: list[DebugProcessDescriptor] = []
        addon_helper_descriptors: list[DebugAddonHelperDescriptor] = []
        for item in entries:
            if item["kind"] == "bundle":
                relative = str(item["path"])
                path = root.joinpath(*PurePosixPath(relative).parts)
                expected_bundle.add(relative)
                stream = _open_verified_file(
                    path,
                    expected_size=item["size"],
                    expected_sha256=item["sha256"],
                    root=root,
                    identity=None,
                )
                opened_by_relative[relative] = stream
                bundle_hashes[relative] = str(item["sha256"])
            else:
                path = Path(str(item["path"]))
                stream = _open_verified_file(
                    path,
                    expected_size=item["size"],
                    expected_sha256=item["sha256"],
                    root=None,
                    identity=item["identity"],
                )
            streams.append(stream)
            info = os.fstat(stream.fileno())
            identity = PathIdentity(
                volume_serial_number=int(info.st_dev),
                file_id=_manifest_file_id(info),
            )
            suffix = PureWindowsPath(str(item["path"])).suffix.lower()
            if suffix == ".exe":
                announced_path = str(PureWindowsPath(str(item["path"])))
                kinds: tuple[BrokerKind, ...] = ()
                if item["kind"] == "bundle" and announced_path == r"runtime\python.exe":
                    kinds = (
                        BrokerKind.PRIVATE_WORKER,
                        BrokerKind.LIFECYCLE_CLI,
                    )
                elif (
                    item["kind"] == "external"
                    and ntpath.normcase(announced_path)
                    == ntpath.normcase(_addon_builder_path())
                ):
                    kinds = (BrokerKind.ADDON_BUILDER,)
                for kind in kinds:
                    process_descriptors.append(
                        DebugProcessDescriptor(
                            kind=kind,
                            announced_path=announced_path,
                            final_path=str(path.resolve(strict=True)),
                            image_sha256=str(item["sha256"]),
                            identity=identity,
                        )
                    )
                if kinds:
                    process_identities.add(identity)
                elif (
                    item["kind"] == "external"
                    and ntpath.normcase(announced_path) in _addon_helper_paths()
                ):
                    addon_helper_descriptors.append(
                        DebugAddonHelperDescriptor(
                            final_path=str(path.resolve(strict=True)),
                            identity=identity,
                        )
                    )
            elif suffix in {".dll", ".pyd"}:
                module_identities.add(identity)

        live_bundle: set[str] = set()
        for path in root.rglob("*"):
            _reject_name_surrogates(root, path)
            if path.is_file():
                live_bundle.add(path.relative_to(root).as_posix())
        if live_bundle != expected_bundle | _SPECIAL_BUNDLE_FILES:
            _invalid()

        policy_stream = opened_by_relative.get("request-policy.json")
        worker_runtime_stream = opened_by_relative.get("worker-runtime.json")
        app_stream = opened_by_relative.get("app.pyz")
        build_contract_stream = opened_by_relative.get("build-contract.json")
        if (
            policy_stream is None
            or worker_runtime_stream is None
            or app_stream is None
            or build_contract_stream is None
        ):
            _invalid()
        policy_raw = _read_bounded(policy_stream, _MAX_POLICY_BYTES)
        if hashlib.sha256(policy_raw).hexdigest().upper() != declared_hashes["request_policy_sha256"]:
            _invalid()
        policy = _canonical_json(policy_raw, maximum=_MAX_POLICY_BYTES)
        sealed_policies = _parse_policy(policy)
        worker_runtime_raw = _read_bounded(
            worker_runtime_stream,
            _MAX_POLICY_BYTES,
        )
        if (
            hashlib.sha256(worker_runtime_raw).hexdigest().upper()
            != declared_hashes["worker_runtime_sha256"]
        ):
            _invalid()
        _canonical_json(worker_runtime_raw, maximum=_MAX_POLICY_BYTES)

        build_contract_raw = _read_bounded(
            build_contract_stream,
            _MAX_POLICY_BYTES,
        )
        build_contract = _canonical_json(
            build_contract_raw,
            maximum=_MAX_POLICY_BYTES,
        )
        if (
            set(build_contract) != {
                "builder_sha256",
                "dependency_lock_sha256",
                "format_version",
                "sources",
            }
            or build_contract.get("format_version") != 1
            or not _valid_hash(build_contract.get("builder_sha256"))
            or not _valid_hash(build_contract.get("dependency_lock_sha256"))
            or build_contract.get("sources")
            != {
                "app_main.py": bundle_hashes.get("src/app_main.py"),
                "launcher.cpp": bundle_hashes.get("src/launcher.cpp"),
            }
        ):
            _invalid()

        receipt_path = root / "reproducibility.json"
        _reject_name_surrogates(root, receipt_path)
        receipt_stream = _open_pinned_read(receipt_path)
        streams.append(receipt_stream)
        receipt_info = os.fstat(receipt_stream.fileno())
        if (
            not stat.S_ISREG(receipt_info.st_mode)
            or int(receipt_info.st_nlink) != 1
            or not _same_path(receipt_path.resolve(strict=True), receipt_path)
        ):
            _invalid()
        receipt_raw = _read_bounded(receipt_stream, _MAX_POLICY_BYTES)
        receipt = _canonical_json(receipt_raw, maximum=_MAX_POLICY_BYTES)
        builds = receipt.get("builds")
        expected_fingerprint = {
            "app_pyz_sha256": bundle_hashes.get("app.pyz"),
            "manifest_sha256": manifest_sha256,
            "pe_sha256": opened_launcher.sha256,
            "request_policy_sha256": bundle_hashes.get("request-policy.json"),
        }
        if (
            set(receipt) != {
                "build_contract_sha256",
                "builds",
                "format_version",
                "reproducible",
            }
            or receipt.get("format_version") != 2
            or receipt.get("reproducible") is not True
            or receipt.get("build_contract_sha256")
            != bundle_hashes.get("build-contract.json")
            or type(builds) is not list
            or len(builds) != len(_REPRODUCIBILITY_MODES)
        ):
            _invalid()
        for index, mode in enumerate(_REPRODUCIBILITY_MODES):
            item = builds[index]
            if (
                type(item) is not dict
                or set(item) != _FINGERPRINT_KEYS | {"mode"}
                or item.get("mode") != mode
                or {key: item.get(key) for key in _FINGERPRINT_KEYS}
                != expected_fingerprint
            ):
                _invalid()

        source_root = Path(__file__).resolve().parent
        source_bytes: dict[str, bytes] = {}
        for field, module in _HASHED_MODULES.items():
            raw = (source_root / module).read_bytes()
            if hashlib.sha256(raw).hexdigest().upper() != declared_hashes[field]:
                _invalid()
            source_bytes[module] = raw
        _verify_app(app_stream, source_bytes)

        marker = b"DAYZ_MCP_MANIFEST_SHA256=" + manifest_sha256.encode("ascii")
        try:
            opened_launcher.require_unique_embedded_marker(marker)
        except ValueError as error:
            raise ValueError("invalid_native_launcher_bundle") from error

        opened_launcher.revalidate()
        authority = DebugImageAuthority(
            process_identities=frozenset(process_identities),
            module_identities=frozenset(module_identities),
            system_directory=_system_directory(),
            process_descriptors=tuple(process_descriptors),
            addon_helper_descriptors=tuple(addon_helper_descriptors),
        )
        return VerifiedNativeBundle(
            sealed_policies,
            manifest_sha256,
            authority,
            streams,
        )
    except BaseException:
        while streams:
            streams.pop().close()
        raise
