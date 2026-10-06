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

from dayz_mcp.authenticode import is_valve_signed, is_valve_signed_handle
from dayz_mcp.dayz_test_request import RequestProjectPolicy
from dayz_mcp.native_broker_protocol import BrokerKind
from dayz_mcp.dayz_tools_paths import (
    ADDON_BUILDER_RELATIVE,
    ADDON_HELPER_RELATIVE,
    external_file_paths,
    resolved_layout,
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
    _kernel32 as _path_kernel32,
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
        "dayz_test_modes.py",
        "dayz_test_readiness.py",
        "dayz_test_request.py",
        "dayz_test_storage.py",
        "dayz_test_worker.py",
        "host_config.py",
        "native_broker_protocol.py",
        "native_process_guard.py",
        "native_process_snapshot.py",
        "normal_daemon_policy.py",
        "pack_only.py",
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
# The broker launches and announces the AddonBuilder sealed in the bundle
# (closure-manifest.json, kind "external"). resolved_layout() is how the builder
# chose that layout — env, registry, then the C: default — and the registry
# spelling can differ in case, so comparisons use ntpath.normcase
# (fb-20260928-124739-a2d5).
def _addon_builder_path() -> str:
    return str(resolved_layout().tools.joinpath(*ADDON_BUILDER_RELATIVE))


def _addon_helper_paths() -> frozenset[str]:
    tools = resolved_layout().tools
    return frozenset(
        ntpath.normcase(str(tools.joinpath(*parts))) for parts in ADDON_HELPER_RELATIVE
    )


def _external_paths() -> frozenset[str]:
    # The builder seals what require_dayz_layout finds, registry included; this check
    # has to resolve the same way (fb-20260927-170437-19b5).
    return frozenset(
        ntpath.normcase(str(path)) for path in external_file_paths(resolved_layout())
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


# Basenames loaded from the Steam install directory itself (no subdirectory)
# by AddonBuilder.exe (32-bit), FileBank.exe (32-bit) and binarize.exe (64-bit)
# on the pack-only LF_VStorage run. Measured 2026-09-29, and again on
# 2026-09-30 when steam.dll appeared beside the same five. Compared
# case-insensitively.
_ADDON_TREE_STEAM_CLIENT_DLLS = frozenset(
    {
        "cserhelper.dll",
        "gameoverlayrenderer.dll",
        "gameoverlayrenderer64.dll",
        "steam.dll",
        "steamclient.dll",
        "tier0_s.dll",
        "vstdlib_s.dll",
    }
)
# The 2026-09-30 run also starts this one child. Exactly one subdirectory of
# the pinned Steam directory, this basename. Its DLL loads are not this list.
_ADDON_TREE_STEAM_LAUNCHER_RELATIVE = ("bin", "x64launcher.exe")
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_TYPE_DISK = 0x0001
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_GENERIC_READ = 0x80000000
_FILE_READ_DATA = 0x00000001
_EXTENDED_FILE_ID_TYPE = 2
_INVALID_HANDLE_VALUES = frozenset({0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF})


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


def _is_trusted_gac_resource_satellite(path: str, windows_directory: str) -> bool:
    # Localized .NET resource satellites (*.resources) load into the
    # AddonBuilder toolchain when an exception is formatted under a
    # non-English OS UI culture. Observed 2026-10-01 (gate #61, culture es):
    #   GAC_MSIL\mscorlib.resources\v4.0_4.0.0.0_es_b77a5c561934e089\
    #   GAC_MSIL\Microsoft.VisualBasic.resources\v4.0_10.0.0.0_es_b03f5f7f11d50a3a\
    # The host machine carries ~180 *.resources assemblies in GAC_MSIL across
    # three official token families (b77a5c561934e089, b03f5f7f11d50a3a,
    # 31bf3856ad364e35), so a per-assembly allowlist would chase loads one
    # rejection at a time. This rule instead trusts the whole
    # admin-protected satellite subtree with structural validation — the
    # same trust model already applied to System32, WinSxS and NativeImages.
    # Required shape, exactly four levels under the pinned root:
    #   GAC_MSIL\<X.resources>\<v4.0_version_culture_token>\<X.resources.dll>
    # where the basename must equal the assembly directory + ".dll"
    # (self-consistency: a foreign basename inside a trusted directory is
    # still rejected), the version directory must be exactly
    # v4.0_<digits.quad>_<BCP-47 ASCII subtags>_<16 hex chars>, and only
    # ".resources" directories qualify (code assemblies like
    # System.Resources.Reader stay out). Evil sibling roots, foreign
    # basenames, deeper nesting and malformed fields stay rejected.
    # The whole path must be ASCII, as every legitimate satellite path is:
    # casefold() and lower() equate some non-ASCII letters with ASCII ones
    # (U+017F LONG S casefolds to "s", U+212A KELVIN SIGN lowers to "k"),
    # while Windows compares names with its own uppercase table and does not,
    # so "C:\Window<U+017F>\..." is a separate tree any authenticated user
    # can create.
    if not path.isascii():
        return False
    expected = ntpath.normpath(
        ntpath.join(
            windows_directory, "Microsoft.NET", "assembly", "GAC_MSIL"
        )
    )
    normalized = ntpath.normpath(path)
    parent, basename = ntpath.split(normalized)
    version_directory = ntpath.basename(parent)
    assembly_directory = ntpath.basename(ntpath.dirname(parent))
    # parent = <GAC_MSIL>\<assembly>\<version dir>; one more dirname gets the
    # assembly directory, one more the pinned GAC_MSIL root.
    gac_directory = ntpath.dirname(ntpath.dirname(parent))
    if ntpath.normcase(gac_directory) != ntpath.normcase(expected):
        return False
    if not assembly_directory.casefold().endswith(".resources"):
        return False
    if basename.casefold() != assembly_directory.casefold() + ".dll":
        return False
    fields = version_directory.split("_")
    if len(fields) != 4 or fields[0] != "v4.0":
        return False
    version_parts = fields[1].split(".")
    if len(version_parts) != 4 or not all(
        part.isascii() and part.isdigit() and len(part) > 0
        for part in version_parts
    ):
        return False
    if len(fields[3]) != 16 or not all(
        character in "0123456789abcdefABCDEF" for character in fields[3]
    ):
        return False
    subtags = fields[2].split("-")
    return all(
        subtag.isascii() and subtag.isalnum() and 1 <= len(subtag) <= 8
        for subtag in subtags
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
    steam_install_directory: str | None = None
    steam_client_dll_names: frozenset[str] = frozenset()
    steam_install_identity: PathIdentity | None = None

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
            or _is_trusted_gac_resource_satellite(path, str(windows_directory))
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
            # The broker announces addon_entry->path from the sealed closure
            # table, the same value stored on this descriptor. A case-only
            # difference is still tolerated because Windows compares paths
            # case-insensitively. Identity, hash and final path pin the file.
            and ntpath.normcase(descriptor.announced_path)
            == ntpath.normcase(announcement.announced_path)
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

    def approve_addon_tree_module(self, file_handle: int) -> bool:
        if type(file_handle) is not int or file_handle <= 0:
            return False
        directory = self.steam_install_directory
        pinned = self.steam_install_identity
        names = self.steam_client_dll_names
        if (
            type(directory) is not str
            or not ntpath.isabs(directory)
            or ntpath.normpath(directory) != directory
            or type(pinned) is not PathIdentity
            or type(names) is not frozenset
            or not names
        ):
            return False
        try:
            before = _file_identity(file_handle)
            path = _final_handle_path(file_handle)
        except (OSError, ValueError):
            return False
        if ntpath.normpath(path) != path:
            return False
        pure = PureWindowsPath(path)
        if pure.suffix.lower() != ".dll":
            return False
        allowed = {name.casefold() for name in names if type(name) is str}
        if len(allowed) != len(names) or pure.name.casefold() not in allowed:
            return False
        parent = ntpath.dirname(path)
        if ntpath.normcase(parent) != ntpath.normcase(directory):
            return False
        try:
            if _directory_identity(parent) != pinned:
                return False
            # WinVerifyTrust reads WINTRUST_FILE_INFO.hFile. The path is only
            # the required pcwszFilePath; a swap of that name does not approve.
            if _valve_signature_of_handle(file_handle, path) is not True:
                return False
            after = _file_identity(file_handle)
            reopened = _path_identity(path)
        except (OSError, ValueError):
            return False
        return before == after == reopened and type(reopened) is PathIdentity

    def approve_addon_tree_steam_launcher(self, file_handle: int) -> bool:
        if type(file_handle) is not int or file_handle <= 0:
            return False
        directory = self.steam_install_directory
        pinned = self.steam_install_identity
        names = self.steam_client_dll_names
        if (
            type(directory) is not str
            or not ntpath.isabs(directory)
            or ntpath.normpath(directory) != directory
            or type(pinned) is not PathIdentity
            or type(names) is not frozenset
            or not names
            or _ADDON_TREE_STEAM_LAUNCHER_RELATIVE
            != ("bin", "x64launcher.exe")
        ):
            return False
        subdirectory, base_name = _ADDON_TREE_STEAM_LAUNCHER_RELATIVE
        try:
            before = _file_identity(file_handle)
            path = _final_handle_path(file_handle)
        except (OSError, ValueError):
            return False
        if ntpath.normpath(path) != path:
            return False
        pure = PureWindowsPath(path)
        if pure.suffix.lower() != ".exe" or pure.name.casefold() != base_name:
            return False
        parent = ntpath.dirname(path)
        install = ntpath.dirname(parent)
        expected = ntpath.join(directory, subdirectory, base_name)
        if (
            PureWindowsPath(parent).name.casefold() != subdirectory
            or ntpath.normcase(install) != ntpath.normcase(directory)
            or ntpath.normcase(path) != ntpath.normcase(expected)
        ):
            return False
        try:
            if _directory_identity(install) != pinned:
                return False
            # WinVerifyTrust reads WINTRUST_FILE_INFO.hFile. The path is only
            # the required pcwszFilePath; a swap of that name does not approve.
            if _valve_signature_of_handle(file_handle, path) is not True:
                return False
            after = _file_identity(file_handle)
            reopened = _path_identity(path)
        except (OSError, ValueError):
            return False
        return before == after == reopened and type(reopened) is PathIdentity


def _path_has_dot_segment(path: str) -> bool:
    parts = PureWindowsPath(path.replace("/", "\\")).parts
    return "." in parts or ".." in parts


class _SteamProcessEntry32W(ctypes.Structure):
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


def _steam_probe_kernel32() -> ctypes.WinDLL:
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
    kernel32.Process32FirstW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_SteamProcessEntry32W),
    )
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_SteamProcessEntry32W),
    )
    kernel32.Process32NextW.restype = wintypes.BOOL
    return kernel32


class _LiveSteamProcessReader:
    """Same reads as WindowsSteamPreflightProvider.steam_process_pids and
    process_image_path (steam_preflight.py:215, steam_preflight.py:199).
    """

    def steam_process_pids(self) -> tuple[int, ...]:
        kernel32 = _steam_probe_kernel32()
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        try:
            entry = _SteamProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                raise OSError(ctypes.get_last_error(), "Process32FirstW failed")
            pids: list[int] = []
            while True:
                if entry.szExeFile.casefold() == "steam.exe":
                    pids.append(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    error = ctypes.get_last_error()
                    if error == 18:
                        break
                    raise OSError(error, "Process32NextW failed")
            return tuple(pids)
        finally:
            kernel32.CloseHandle(snapshot)

    def process_image_path(self, pid: int) -> str:
        kernel32 = _steam_probe_kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                raise OSError(
                    ctypes.get_last_error(), "QueryFullProcessImageNameW failed"
                )
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)


class _RegistrySteamExecutable:
    """HKCU Software\\Valve\\Steam SteamExe, the read in
    WindowsSteamRemediationHost.steam_executable (steam_preflight.py:517).
    """

    def steam_executable(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value, _ = winreg.QueryValueEx(key, "SteamExe")
        except OSError:
            return None
        if isinstance(value, str) and value:
            return value
        return None


@dataclass(frozen=True)
class _PinnedSteamInstall:
    directory: str
    identity: PathIdentity


def _open_path_identity(path: str, *, directory: bool) -> PathIdentity | None:
    if type(path) is not str or not path or "\0" in path:
        return None
    flags = _FILE_FLAG_BACKUP_SEMANTICS if directory else _FILE_ATTRIBUTE_NORMAL
    handle = _path_kernel32.CreateFileW(
        path,
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        return None
    numeric = int(handle)
    try:
        if int(_path_kernel32.GetFileType(numeric)) != _FILE_TYPE_DISK:
            return None
        return _file_identity(numeric)
    except (OSError, ValueError):
        return None
    finally:
        _path_kernel32.CloseHandle(numeric)


def _directory_identity(path: str) -> PathIdentity | None:
    return _open_path_identity(path, directory=True)


def _path_identity(path: str) -> PathIdentity | None:
    return _open_path_identity(path, directory=False)


class _FileId128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class _FileIdDescriptor(ctypes.Structure):
    class _Union(ctypes.Union):
        _fields_ = [
            ("FileId", ctypes.c_longlong),
            ("ObjectId", ctypes.c_ubyte * 16),
            ("ExtendedFileId", _FileId128),
        ]

    _anonymous_ = ("union",)
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("union", _Union),
    ]


def _usable_handle(handle: object) -> int | None:
    if not handle:
        return None
    value = int(handle)
    if value in _INVALID_HANDLE_VALUES or value == _INVALID_HANDLE_VALUE:
        return None
    return value


def _close_kernel_handle(value: int | None) -> None:
    if type(value) is int and value > 0:
        _path_kernel32.CloseHandle(value)


def _bind_handle_reopen() -> None:
    # Same WinDLL as native_launcher_backend. Leave a signature that is already
    # bound; setting it again would fight DuplicateHandle's prototype there.
    kernel32 = _path_kernel32
    if kernel32.GetCurrentProcess.argtypes is None:
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    if kernel32.DuplicateHandle.argtypes is None:
        kernel32.DuplicateHandle.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.DuplicateHandle.restype = wintypes.BOOL
    if kernel32.OpenFileById.argtypes is None:
        kernel32.OpenFileById.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_FileIdDescriptor),
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.OpenFileById.restype = wintypes.HANDLE


def _duplicate_read(file_handle: int) -> int | None:
    _bind_handle_reopen()
    duplicated = wintypes.HANDLE()
    current = _path_kernel32.GetCurrentProcess()
    if not _path_kernel32.DuplicateHandle(
        current,
        wintypes.HANDLE(file_handle),
        current,
        ctypes.byref(duplicated),
        _FILE_READ_DATA,
        False,
        0,
    ):
        return None
    return _usable_handle(duplicated.value)


def _reopen_by_file_id(path: str, identity: PathIdentity) -> int | None:
    """Open the same file id. The path only selects the volume."""
    if type(identity) is not PathIdentity or type(identity.file_id) is not str:
        return None
    file_id = identity.file_id
    if len(file_id) != 32 or any(character not in "0123456789ABCDEF" for character in file_id):
        return None
    drive, _tail = ntpath.splitdrive(ntpath.normpath(path))
    if (
        len(drive) != 2
        or drive[1] != ":"
        or not drive[0].isascii()
        or not drive[0].isalpha()
    ):
        return None
    _bind_handle_reopen()
    volume = _path_kernel32.CreateFileW(
        "\\\\.\\" + drive,
        0,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    volume_handle = _usable_handle(volume)
    if volume_handle is None:
        return None
    try:
        extended = _FileId128()
        extended.Identifier[:] = bytes.fromhex(file_id)
        descriptor = _FileIdDescriptor()
        descriptor.dwSize = ctypes.sizeof(descriptor)
        descriptor.Type = _EXTENDED_FILE_ID_TYPE
        descriptor.ExtendedFileId = extended
        opened = _path_kernel32.OpenFileById(
            wintypes.HANDLE(volume_handle),
            ctypes.byref(descriptor),
            _GENERIC_READ,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            0,
        )
        numeric = _usable_handle(opened)
        if numeric is None:
            return None
        try:
            if int(_path_kernel32.GetFileType(numeric)) != _FILE_TYPE_DISK:
                raise OSError("reopened file is not a disk file")
            if _file_identity(numeric) != identity:
                raise OSError("reopened file id differs")
        except (OSError, ValueError):
            _close_kernel_handle(numeric)
            return None
        return numeric
    finally:
        _close_kernel_handle(volume_handle)


def _valve_signature_of_handle(file_handle: int, path: str) -> bool:
    """Valve subject of this open file, read through its handle.

    FILE_READ_ATTRIBUTES is not enough for WinVerifyTrust. Duplicate the handle
    for FILE_READ_DATA when it already allows that. Otherwise reopen by file id
    and require that reopen to be the same PathIdentity. No path fallback.
    """
    if type(file_handle) is not int or file_handle <= 0 or type(path) is not str or not path:
        return False
    try:
        before = _file_identity(file_handle)
    except (OSError, ValueError):
        return False
    if type(before) is not PathIdentity:
        return False
    opened = _duplicate_read(file_handle)
    if opened is None:
        opened = _reopen_by_file_id(path, before)
    if opened is None:
        return False
    try:
        if _file_identity(opened) != before or _file_identity(file_handle) != before:
            return False
        if is_valve_signed_handle(opened, path=path) is not True:
            return False
        return (
            _file_identity(opened) == before and _file_identity(file_handle) == before
        )
    except (OSError, ValueError):
        return False
    finally:
        _close_kernel_handle(opened)


def _is_steam_executable_path(path: object) -> bool:
    return (
        type(path) is str
        and bool(path)
        and "\0" not in path
        and '"' not in path
        and ntpath.isabs(path)
        and not _path_has_dot_segment(path)
        and ntpath.basename(path).casefold() == "steam.exe"
    )


def _open_share_read(path: str) -> int | None:
    if (
        type(path) is not str
        or not path
        or "\0" in path
        or '"' in path
        or not ntpath.isabs(path)
        or _path_has_dot_segment(path)
    ):
        return None
    handle = _path_kernel32.CreateFileW(
        ntpath.normpath(path),
        _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    numeric = _usable_handle(handle)
    if numeric is None:
        return None
    try:
        if int(_path_kernel32.GetFileType(numeric)) != _FILE_TYPE_DISK:
            raise OSError("opened file is not a disk file")
    except (OSError, ValueError):
        _close_kernel_handle(numeric)
        return None
    return numeric


def _open_exclusive_read(path: str) -> int | None:
    """GENERIC_READ that denies write and delete for as long as the handle lives.

    FILE_SHARE_READ is the only share. os.replace and a writer both fail with a
    sharing violation until the handle is closed.
    """
    if not _is_steam_executable_path(path):
        return None
    handle = _path_kernel32.CreateFileW(
        ntpath.normpath(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    numeric = _usable_handle(handle)
    if numeric is None:
        return None
    try:
        if int(_path_kernel32.GetFileType(numeric)) != _FILE_TYPE_DISK:
            raise OSError("opened file is not a disk file")
    except (OSError, ValueError):
        _close_kernel_handle(numeric)
        return None
    return numeric


def _steam_image_pin(
    file_handle: int, directory: str
) -> tuple[PathIdentity, str] | None:
    try:
        identity = _file_identity(file_handle)
        final = _final_handle_path(file_handle)
    except (OSError, ValueError):
        return None
    if type(identity) is not PathIdentity:
        return None
    if PureWindowsPath(final).name.casefold() != "steam.exe":
        return None
    parent = ntpath.dirname(final)
    if (
        not parent
        or ntpath.normpath(parent) != parent
        or ntpath.normcase(parent) != ntpath.normcase(directory)
    ):
        return None
    return identity, final


def _install_directory_of_steam_executable(path: str) -> str | None:
    if (
        type(path) is not str
        or not path
        or "\0" in path
        or '"' in path
        or not ntpath.isabs(path)
        or _path_has_dot_segment(path)
        or ntpath.basename(path).casefold() != "steam.exe"
    ):
        return None
    normalized = ntpath.normpath(path)
    handle = _path_kernel32.CreateFileW(
        normalized,
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        return None
    numeric = int(handle)
    try:
        if int(_path_kernel32.GetFileType(numeric)) != _FILE_TYPE_DISK:
            return None
        _file_identity(numeric)
        final = _final_handle_path(numeric)
    except (OSError, ValueError):
        return None
    finally:
        _path_kernel32.CloseHandle(numeric)
    if PureWindowsPath(final).name.casefold() != "steam.exe":
        return None
    parent = ntpath.dirname(final)
    if not parent or ntpath.normpath(parent) != parent or not os.path.isdir(parent):
        return None
    return parent


def _resolve_addon_tree_steam_directory(
    provider: object | None = None,
    host: object | None = None,
) -> _PinnedSteamInstall | None:
    """Choose the Steam directory, or leave the DLL rule off.

    One live steam.exe and HKCU Software\\Valve\\Steam SteamExe have to be the
    same file as a GENERIC_READ handle opened with FILE_SHARE_READ only. While
    that handle is held, the live image path is queried again and both paths
    are opened; the held handle, the re-query and the registry path must share
    one PathIdentity. WinVerifyTrust then runs on the held handle.

    Residual: the running image can already have been renamed away before this
    open. Binding that image section to a handle would need an undocumented
    API, which this does not call. A rename that lands before the open is
    visible to the re-query (the process path follows the file); a rename
    after the open fails because the handle shares neither delete nor write.
    steam.exe only chooses the directory. A LOAD_DLL is approved only when
    that DLL's own event handle is Valve-signed and its parent is this
    directory.

    The readers are copied from steam_preflight (steam_process_pids,
    process_image_path, steam_executable). Importing that module would put
    invoke_steam's subprocess.Popen (steam_preflight.py:530) in the audited
    process-creation closure.
    """
    try:
        selected_provider = _LiveSteamProcessReader() if provider is None else provider
        selected_host = _RegistrySteamExecutable() if host is None else host
        try:
            raw_pids = selected_provider.steam_process_pids()
        except Exception:
            return None
        if type(raw_pids) is not tuple or len(raw_pids) != 1:
            return None
        pid = raw_pids[0]
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            return None
        try:
            image = selected_provider.process_image_path(pid)
        except Exception:
            return None
        if not _is_steam_executable_path(image):
            return None
        image_handle = _open_exclusive_read(image)
        if image_handle is None:
            return None
        requery_handle = None
        registry_handle = None
        try:
            try:
                held_identity = _file_identity(image_handle)
                image_final = _final_handle_path(image_handle)
            except (OSError, ValueError):
                return None
            if type(held_identity) is not PathIdentity:
                return None
            if PureWindowsPath(image_final).name.casefold() != "steam.exe":
                return None
            parent = ntpath.dirname(image_final)
            if not parent or ntpath.normpath(parent) != parent:
                return None
            try:
                requery = selected_provider.process_image_path(pid)
            except Exception:
                return None
            if not _is_steam_executable_path(requery):
                return None
            requery_handle = _open_exclusive_read(requery)
            if requery_handle is None:
                return None
            try:
                if _file_identity(requery_handle) != held_identity:
                    return None
            except (OSError, ValueError):
                return None
            try:
                registry_value = selected_host.steam_executable()
            except Exception:
                return None
            if not _is_steam_executable_path(registry_value):
                return None
            registry_handle = _open_exclusive_read(registry_value)
            if registry_handle is None:
                return None
            try:
                if _file_identity(registry_handle) != held_identity:
                    return None
            except (OSError, ValueError):
                return None
            if _valve_signature_of_handle(image_handle, image_final) is not True:
                return None
            try:
                if _file_identity(image_handle) != held_identity:
                    return None
            except (OSError, ValueError):
                return None
            identity = _directory_identity(parent)
            if type(identity) is not PathIdentity:
                return None
            return _PinnedSteamInstall(parent, identity)
        finally:
            _close_kernel_handle(image_handle)
            _close_kernel_handle(requery_handle)
            _close_kernel_handle(registry_handle)
    except Exception:
        return None


def _addon_tree_steam_rule() -> tuple[str | None, frozenset[str], PathIdentity | None]:
    pinned = _resolve_addon_tree_steam_directory()
    if pinned is None:
        return None, frozenset(), None
    return pinned.directory, _ADDON_TREE_STEAM_CLIENT_DLLS, pinned.identity


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


def _read_source_pins(
    declared: dict[str, object], source_root: Path
) -> tuple[dict[str, bytes], list[dict[str, str]]]:
    sources: dict[str, bytes] = {}
    failures: list[dict[str, str]] = []
    for key, module in _HASHED_MODULES.items():
        expected = declared.get(key)
        if not isinstance(expected, str) or not _valid_hash(expected.upper()):
            failures.append({"key": key, "file": module, "reason": "invalid_pin"})
            continue
        try:
            raw = (source_root / module).read_bytes()
        except OSError:
            failures.append({"key": key, "file": module, "reason": "source_unreadable"})
            continue
        sources[module] = raw
        if hashlib.sha256(raw).hexdigest().upper() != expected.upper():
            failures.append({"key": key, "file": module, "reason": "sha256_mismatch"})
    return sources, failures


def source_pin_status(
    manifest: dict[str, object], *, source_root: Path | None = None
) -> dict[str, object]:
    """Cheap diagnostic only: four source pins, never a full bundle attestation.

    Compare working-tree bytes, as the builder does; do not rebuild or normalize
    line endings. Digest case is immaterial. Missing pins/sources are failures.
    """
    _, failures = _read_source_pins(manifest, source_root or Path(__file__).resolve().parent)
    return {"status": "stale" if failures else "fresh", "failures": failures}


def installed_source_pin_status(launcher_id: str = "dayz-test-v1") -> dict[str, object]:
    """Inspect the approved launcher's manifest without opening its whole closure."""
    from dayz_mcp.launcher_registry import open_approved_launcher

    try:
        with open_approved_launcher(launcher_id) as opened:
            with _open_pinned_read(opened.root / "closure-manifest.json") as stream:
                manifest = _canonical_json(
                    _read_bounded(stream, _MAX_MANIFEST_BYTES), maximum=_MAX_MANIFEST_BYTES
                )
            return source_pin_status(manifest)
    except (OSError, ValueError, RuntimeError):
        return {"status": "unavailable", "failures": [], "reason": "manifest_unreadable"}


def _require_source_pins(declared: dict[str, object]) -> dict[str, bytes]:
    sources, failures = _read_source_pins(declared, Path(__file__).resolve().parent)
    if failures:
        # The MCP adapter deliberately strips prose/path-bearing ValueErrors.
        # Keep this an identifier made only from our constants so the failing
        # key survives that boundary. Doctor/startup list every key and filename.
        first = failures[0]
        raise ValueError(f"invalid_native_launcher_bundle__{first['key']}")
    return sources


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
        # Diagnose stale source seals before a missing/changed closure artifact can
        # mask the actionable pin name. Full closure verification still follows.
        source_bytes = _require_source_pins(declared_hashes)

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

        _verify_app(app_stream, source_bytes)

        marker = b"DAYZ_MCP_MANIFEST_SHA256=" + manifest_sha256.encode("ascii")
        try:
            opened_launcher.require_unique_embedded_marker(marker)
        except ValueError as error:
            raise ValueError("invalid_native_launcher_bundle") from error

        opened_launcher.revalidate()
        (
            steam_install_directory,
            steam_client_dll_names,
            steam_install_identity,
        ) = _addon_tree_steam_rule()
        authority = DebugImageAuthority(
            process_identities=frozenset(process_identities),
            module_identities=frozenset(module_identities),
            system_directory=_system_directory(),
            process_descriptors=tuple(process_descriptors),
            addon_helper_descriptors=tuple(addon_helper_descriptors),
            steam_install_directory=steam_install_directory,
            steam_client_dll_names=steam_client_dll_names,
            steam_install_identity=steam_install_identity,
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
