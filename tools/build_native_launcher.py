from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import ntpath
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import urllib.request
import zipfile
from ctypes import wintypes
from pathlib import Path, PurePosixPath

from dayz_mcp.dayz_test_request import (
    _path_is_within,
    _valid_local_absolute_path,
    _valid_mod_entry,
    _valid_string_list,
)


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
LOCK_PATH = TOOLS_DIR / "dependency-lock.json"
CANONICAL_BUNDLE = TOOLS_DIR / "native-launchers" / "dayz-test-v1"
BUNDLE_ID = "dayz-test-v1"
CPYTHON_NAME = "python-3.14.3-embed-amd64.zip"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 256
SPECIAL_BUNDLE_FILES = frozenset(
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
PACKAGED_MODULES = (
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
    # Pulled in on 2026-08-21 by pinned_keyfile: the duplicated FILE_STANDARD_INFO
    # moved here, and a packaged module importing an unpackaged one is a
    # ModuleNotFoundError inside app.pyz, not a build error.
    "win32_fileinfo.py",
)
EXTERNAL_FILES = (
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe.config"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\log4net.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\NDesk.Options.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\SharedResources.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\SteamHelper.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\SteamLayerWrap.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\steam_api.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\Utils.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\en-US\SharedResources.resources.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\logger.xml"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\steam_appid.txt"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize\binarize.exe"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize\steam_api64.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize\bin.txt"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize\bin\config.cpp"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\CfgConvert\CfgConvert.exe"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\FileBank.exe"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\NativeMethods.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\log4net.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\LibCommon.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils\exclude.lst"),
    Path(r"C:\Program Files (x86)\Steam\steamclient.dll"),
    Path(r"C:\Program Files (x86)\Steam\Steam.dll"),
    Path(r"C:\Program Files (x86)\Steam\CSERHelper.dll"),
    Path(r"C:\Program Files (x86)\Steam\GameOverlayRenderer.dll"),
    Path(r"C:\Program Files (x86)\Steam\tier0_s.dll"),
    Path(r"C:\Program Files (x86)\Steam\vstdlib_s.dll"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe"),
)


class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


class _FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    ]


class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", ctypes.c_ubyte * 16)]


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
_kernel32.GetFileInformationByHandleEx.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
)
_kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
_kernel32.GetFinalPathNameByHandleW.argtypes = (
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
)
_kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD

_GENERIC_READ = 0x80000000
_FILE_READ_ATTRIBUTES = 0x80
_FILE_SHARE_READ = 0x1
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_FILE_STANDARD_INFO_CLASS = 1
_FILE_ID_INFO_CLASS = 18
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and value == value.upper()
        and all(character in "0123456789ABCDEF" for character in value)
    )


def _closed_load(path: Path) -> dict[str, object]:
    def closed(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=closed)
    if type(value) is not dict:
        raise ValueError("invalid_json_root")
    return value


def _require_file(path: Path, expected: dict[str, object]) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"locked_file_missing:{path}")
    if path.stat().st_size != expected["size"] or _sha256(path) != expected["sha256"]:
        raise ValueError(f"locked_file_drift:{path}")


def _load_verified_lock() -> dict[str, object]:
    lock = _closed_load(LOCK_PATH)
    if set(lock) != {"artifacts", "format_version", "remote_artifacts", "toolchains"} or lock["format_version"] != 1:
        raise ValueError("dependency_lock_invalid")
    artifacts = lock["artifacts"]
    if type(artifacts) is not dict:
        raise ValueError("dependency_lock_invalid")
    wheel = artifacts.get("psutil_wheel")
    if type(wheel) is not dict:
        raise ValueError("dependency_lock_invalid")
    _require_file(PROJECT_ROOT / str(wheel["path"]), wheel)
    for toolchain in lock["toolchains"].values():
        for item in toolchain["files"].values():
            _require_file(Path(item["path"]), item)
    return lock


def _cache_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ValueError("local_cache_unavailable")
    return Path(local) / "DayZ_MCP" / "dependency-cache" / CPYTHON_NAME


def _acquire_cpython(lock: dict[str, object], *, offline: bool) -> Path:
    expected = lock["remote_artifacts"]["cpython_embed"]
    cache = _cache_path()
    if cache.is_file() and cache.stat().st_size == expected["size"] and _sha256(cache) == expected["sha256"]:
        return cache
    if offline:
        raise ValueError("cpython_cache_missing_or_drifted")
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_name(cache.name + ".partial")
    if temporary.exists():
        temporary.unlink()
    request = urllib.request.Request(str(expected["url"]), headers={"User-Agent": "DayZ-MCP-reproducible-builder/1"})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("xb") as output:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            output.write(block)
            if output.tell() > int(expected["size"]):
                raise ValueError("cpython_download_size_drift")
        output.flush()
        os.fsync(output.fileno())
    if temporary.stat().st_size != expected["size"] or _sha256(temporary) != expected["sha256"]:
        temporary.unlink(missing_ok=True)
        raise ValueError("cpython_download_integrity_failed")
    os.replace(temporary, cache)
    return cache


def _safe_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if not 1 <= len(members) <= MAX_ARCHIVE_ENTRIES:
        raise ValueError("archive_entry_count")
    total = 0
    names: set[str] = set()
    for item in members:
        pure = PurePosixPath(item.filename)
        normalized = pure.as_posix()
        if pure.is_absolute() or ".." in pure.parts or "\\" in item.filename or ":" in item.filename:
            raise ValueError("archive_path")
        folded = normalized.casefold()
        if folded in names:
            raise ValueError("archive_duplicate")
        names.add(folded)
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError("archive_symlink")
        total += item.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise ValueError("archive_expanded_size")
    return members


def _extract_cpython(archive_path: Path, runtime: Path) -> None:
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_archive_members(archive)
        for item in members:
            if item.is_dir():
                continue
            destination = runtime / PurePosixPath(item.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)
    (runtime / "python314._pth").write_text(
        ".\npython314.zip\n..\\app.pyz\n..\\vendor\n",
        encoding="ascii",
        newline="\n",
    )


def _extract_psutil(lock: dict[str, object], vendor: Path) -> None:
    wheel_item = lock["artifacts"]["psutil_wheel"]
    wheel_path = PROJECT_ROOT / str(wheel_item["path"])
    vendor.mkdir(parents=True)
    with zipfile.ZipFile(wheel_path) as archive:
        _safe_archive_members(archive)
        for item in archive.infolist():
            if item.is_dir() or not (item.filename.startswith("psutil/") or item.filename == "psutil-7.2.2.dist-info/LICENSE"):
                continue
            relative = "PSUTIL-LICENSE.txt" if item.filename.endswith("/LICENSE") else item.filename
            destination = vendor / PurePosixPath(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)


def _zip_entry(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, data


def _build_app_pyz(destination: Path) -> None:
    files: dict[str, bytes] = {
        "__main__.py": (CANONICAL_BUNDLE / "src" / "app_main.py").read_bytes(),
        "dayz_mcp/__init__.py": (TOOLS_DIR / "dayz_mcp" / "__init__.py").read_bytes(),
    }
    for name in PACKAGED_MODULES:
        files[f"dayz_mcp/{name}"] = (TOOLS_DIR / "dayz_mcp" / name).read_bytes()
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info, data = _zip_entry(name, files[name])
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _file_details(path: Path, *, directory: bool, open_reparse: bool) -> dict[str, object]:
    flags = _FILE_FLAG_BACKUP_SEMANTICS if directory else 0
    if open_reparse:
        flags |= _FILE_FLAG_OPEN_REPARSE_POINT
    handle = _kernel32.CreateFileW(
        str(path),
        _FILE_READ_ATTRIBUTES | (0 if directory else _GENERIC_READ),
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise ValueError(f"identity_open_failed:{path}")
    numeric = int(handle)
    try:
        tag = _FILE_ATTRIBUTE_TAG_INFO()
        standard = _FILE_STANDARD_INFO()
        identity = _FILE_ID_INFO()
        if not all(
            (
                _kernel32.GetFileInformationByHandleEx(numeric, _FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(tag), ctypes.sizeof(tag)),
                _kernel32.GetFileInformationByHandleEx(numeric, _FILE_STANDARD_INFO_CLASS, ctypes.byref(standard), ctypes.sizeof(standard)),
                _kernel32.GetFileInformationByHandleEx(numeric, _FILE_ID_INFO_CLASS, ctypes.byref(identity), ctypes.sizeof(identity)),
            )
        ):
            raise ValueError(f"identity_query_failed:{path}")
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(_kernel32.GetFinalPathNameByHandleW(numeric, buffer, len(buffer), 0))
        if length <= 0 or length >= len(buffer):
            raise ValueError(f"identity_final_path_failed:{path}")
        final = buffer.value
        if final.startswith("\\\\?\\"):
            final = final[4:]
        reparse_tag = int(tag.ReparseTag) if int(tag.FileAttributes) & _FILE_ATTRIBUTE_REPARSE_POINT else 0
        return {
            "file_id": bytes(identity.FileId).hex().upper(),
            "final_path": str(Path(final)),
            "links": int(standard.NumberOfLinks),
            "reparse_tag": reparse_tag,
            "size": int(standard.EndOfFile),
            "volume_serial_number": int(identity.VolumeSerialNumber),
        }
    finally:
        _kernel32.CloseHandle(numeric)


def _sealed_root(path: str, *, allow_root_junction: bool) -> dict[str, object]:
    lexical = _file_details(Path(path), directory=True, open_reparse=True)
    resolved = _file_details(Path(path), directory=True, open_reparse=False)
    if allow_root_junction:
        if lexical["reparse_tag"] != _IO_REPARSE_TAG_MOUNT_POINT:
            raise ValueError(f"required_junction_missing:{path}")
    elif lexical["reparse_tag"] != 0:
        raise ValueError(f"unexpected_root_reparse:{path}")
    identity = {"file_id": lexical["file_id"], "volume_serial_number": lexical["volume_serial_number"]}
    resolved_identity = {"file_id": resolved["file_id"], "volume_serial_number": resolved["volume_serial_number"]}
    return {
        "allow_root_junction": allow_root_junction,
        "handle_path": lexical["final_path"],
        "identity": identity,
        "path": path,
        "resolved_identity": resolved_identity,
        "resolved_path": resolved["final_path"],
        "root_reparse_tag": lexical["reparse_tag"],
    }


_POLICY_ENV = "DAYZ_MCP_LAUNCHER_POLICY"
_MAX_POLICY_BYTES = 65_536
_MOD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_BUILD_SOURCE_BASENAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
_MISSION_ALIAS_KEYS = frozenset({"chernarus", "livonia", "sakhal"})
_PROJECT_INTENT_KEYS = frozenset(
    {
        "build_source_basename",
        "build_temp_root",
        "default_base_mods",
        "default_source",
        "dev_root",
        "diag_executable",
        "game_directory",
        "mission_aliases",
        "mission_roots",
        "mod",
        "mod_roots",
        "mods_root",
    }
)
_WORKER_PROJECT_KEYS = frozenset(
    {
        "build_source_basename",
        "build_temp_root",
        "dev_root",
        "diag_executable",
        "game_directory",
        "mission_aliases",
        "mod",
        "mods_root",
    }
)


def resolve_launcher_policy_path(cli_path: Path | str | None = None) -> Path:
    # One winner: --policy, then DAYZ_MCP_LAUNCHER_POLICY, then LOCALAPPDATA.
    # The published example is never part of this chain.
    if cli_path is not None:
        return Path(cli_path)
    if _POLICY_ENV in os.environ:
        value = os.environ[_POLICY_ENV]
        if type(value) is not str or not value.strip():
            raise ValueError("policy_source_unselected")
        return Path(value)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ValueError("local_cache_unavailable")
    return Path(local) / "DayZ_MCP" / "launcher-policy.json"


def _validate_root_intent(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != {"allow_root_junction", "path"}:
        raise ValueError("policy_source_schema")
    if type(value["allow_root_junction"]) is not bool:
        raise ValueError("policy_source_schema")
    if not _valid_local_absolute_path(value["path"]):
        raise ValueError("policy_source_schema")
    return value


def _validate_root_intent_list(value: object) -> list[dict[str, object]]:
    if type(value) is not list or not 1 <= len(value) <= 64:
        raise ValueError("policy_source_schema")
    roots = [_validate_root_intent(item) for item in value]
    folded = [ntpath.normcase(item["path"]) for item in roots]
    if len(folded) != len(set(folded)):
        raise ValueError("policy_source_schema")
    return roots


def validate_launcher_policy_source(value: object) -> None:
    if (
        type(value) is not dict
        or set(value) != {"format_version", "projects"}
        or type(value.get("format_version")) is not int
        or value["format_version"] != 1
    ):
        raise ValueError("policy_source_schema")
    projects = value["projects"]
    if type(projects) is not list or not 1 <= len(projects) <= 128:
        raise ValueError("policy_source_schema")
    seen: set[tuple[str, str]] = set()
    for project in projects:
        if type(project) is not dict or set(project) != _PROJECT_INTENT_KEYS:
            raise ValueError("policy_source_schema")
        if type(project["mod"]) is not str or _MOD_NAME.fullmatch(project["mod"]) is None:
            raise ValueError("policy_source_schema")
        _validate_root_intent(project["default_source"])
        dev_root = _validate_root_intent(project["dev_root"])
        mission_roots = _validate_root_intent_list(project["mission_roots"])
        mod_roots = _validate_root_intent_list(project["mod_roots"])
        mod_root_paths = tuple(item["path"] for item in mod_roots)
        mission_root_paths = tuple(item["path"] for item in mission_roots)
        if not _valid_string_list(project["default_base_mods"]) or not all(
            _valid_mod_entry(item, mod_root_paths) for item in project["default_base_mods"]
        ):
            raise ValueError("policy_source_schema")
        for key in ("build_temp_root", "diag_executable", "game_directory", "mods_root"):
            if not _valid_local_absolute_path(project[key]):
                raise ValueError("policy_source_schema")
        basename = project["build_source_basename"]
        if basename is not None and (
            type(basename) is not str
            or _BUILD_SOURCE_BASENAME.fullmatch(basename) is None
        ):
            raise ValueError("policy_source_schema")
        aliases = project["mission_aliases"]
        if (
            type(aliases) is not dict
            or set(aliases) != _MISSION_ALIAS_KEYS
            or not all(_valid_local_absolute_path(item) for item in aliases.values())
            or not all(_path_is_within(item, mission_root_paths) for item in aliases.values())
        ):
            raise ValueError("policy_source_schema")
        if ntpath.normcase(project["mods_root"]) not in {ntpath.normcase(path) for path in mod_root_paths}:
            raise ValueError("policy_source_schema")
        identity = (project["mod"].casefold(), dev_root["path"].casefold())
        if identity in seen:
            raise ValueError("policy_source_schema")
        seen.add(identity)


def load_launcher_policy_source(path: Path) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink():
        raise ValueError("policy_source_not_regular")
    if not path.exists():
        raise ValueError("policy_source_missing")
    if not path.is_file():
        raise ValueError("policy_source_not_regular")
    details = _file_details(path, directory=False, open_reparse=True)
    if details["reparse_tag"] != 0:
        raise ValueError("policy_source_not_regular")
    raw = _closed_load(path)
    validate_launcher_policy_source(raw)
    return raw


def seal_request_policy(source: dict[str, object]) -> dict[str, object]:
    validate_launcher_policy_source(source)
    projects: list[dict[str, object]] = []
    for project in source["projects"]:
        projects.append(
            {
                "default_base_mods": list(project["default_base_mods"]),
                "default_source": _sealed_root(
                    project["default_source"]["path"],
                    allow_root_junction=project["default_source"]["allow_root_junction"],
                ),
                "dev_root": _sealed_root(
                    project["dev_root"]["path"],
                    allow_root_junction=project["dev_root"]["allow_root_junction"],
                ),
                "mission_roots": [
                    _sealed_root(item["path"], allow_root_junction=item["allow_root_junction"])
                    for item in project["mission_roots"]
                ],
                "mod": project["mod"],
                "mod_roots": [
                    _sealed_root(item["path"], allow_root_junction=item["allow_root_junction"])
                    for item in project["mod_roots"]
                ],
            }
        )
    return {"format_version": 1, "projects": projects}


def derive_worker_runtime(source: dict[str, object]) -> dict[str, object]:
    validate_launcher_policy_source(source)
    projects: list[dict[str, object]] = []
    for project in source["projects"]:
        projects.append(
            {
                "build_source_basename": project["build_source_basename"],
                "build_temp_root": project["build_temp_root"],
                "dev_root": project["dev_root"]["path"],
                "diag_executable": project["diag_executable"],
                "game_directory": project["game_directory"],
                "mission_aliases": dict(project["mission_aliases"]),
                "mod": project["mod"],
                "mods_root": project["mods_root"],
            }
        )
    return {"format_version": 1, "projects": projects}


def validate_worker_runtime_document(value: object) -> None:
    if (
        type(value) is not dict
        or set(value) != {"format_version", "projects"}
        or type(value.get("format_version")) is not int
        or value["format_version"] != 1
    ):
        raise ValueError("worker_runtime_schema")
    projects = value["projects"]
    if type(projects) is not list or not 1 <= len(projects) <= 128:
        raise ValueError("worker_runtime_schema")
    seen: set[tuple[str, str]] = set()
    for project in projects:
        if type(project) is not dict or set(project) != _WORKER_PROJECT_KEYS:
            raise ValueError("worker_runtime_schema")
        if type(project["mod"]) is not str or not project["mod"]:
            raise ValueError("worker_runtime_schema")
        aliases = project["mission_aliases"]
        if type(aliases) is not dict or set(aliases) != _MISSION_ALIAS_KEYS:
            raise ValueError("worker_runtime_schema")
        basename = project["build_source_basename"]
        if basename is not None and (
            type(basename) is not str
            or _BUILD_SOURCE_BASENAME.fullmatch(basename) is None
        ):
            raise ValueError("worker_runtime_schema")
        paths = (
            project["build_temp_root"],
            project["dev_root"],
            project["diag_executable"],
            project["game_directory"],
            project["mods_root"],
            *aliases.values(),
        )
        if not all(_valid_local_absolute_path(item) for item in paths):
            raise ValueError("worker_runtime_schema")
        identity = (project["mod"].casefold(), project["dev_root"].casefold())
        if identity in seen:
            raise ValueError("worker_runtime_schema")
        seen.add(identity)


def _emit_policy_documents(source: dict[str, object]) -> tuple[bytes, bytes]:
    sealed = seal_request_policy(source)
    validate_request_policy_document(sealed)
    runtime = derive_worker_runtime(source)
    validate_worker_runtime_document(runtime)
    policy_bytes = canonical_json_bytes(sealed)
    runtime_bytes = canonical_json_bytes(runtime)
    if len(policy_bytes) > _MAX_POLICY_BYTES or len(runtime_bytes) > _MAX_POLICY_BYTES:
        raise ValueError("policy_document_too_large")
    return policy_bytes, runtime_bytes


def _valid_identity(value: object) -> bool:
    return (
        type(value) is dict
        and set(value) == {"file_id", "volume_serial_number"}
        and type(value["volume_serial_number"]) is int
        and value["volume_serial_number"] >= 0
        and type(value["file_id"]) is str
        and len(value["file_id"]) == 32
        and all(character in "0123456789ABCDEF" for character in value["file_id"])
    )


def _valid_root(value: object) -> bool:
    return (
        type(value) is dict
        and set(value) == {
            "allow_root_junction",
            "handle_path",
            "identity",
            "path",
            "resolved_identity",
            "resolved_path",
            "root_reparse_tag",
        }
        and type(value["allow_root_junction"]) is bool
        and type(value["root_reparse_tag"]) is int
        and all(type(value[key]) is str and len(value[key]) >= 3 for key in ("path", "handle_path", "resolved_path"))
        and _valid_identity(value["identity"])
        and _valid_identity(value["resolved_identity"])
        and ((value["allow_root_junction"] and value["root_reparse_tag"] == _IO_REPARSE_TAG_MOUNT_POINT) or (not value["allow_root_junction"] and value["root_reparse_tag"] == 0))
    )


def validate_request_policy_document(value: object) -> None:
    if type(value) is not dict or set(value) != {"format_version", "projects"} or value["format_version"] != 1:
        raise ValueError("request_policy_schema")
    projects = value["projects"]
    if type(projects) is not list or not 1 <= len(projects) <= 128:
        raise ValueError("request_policy_projects")
    seen: set[tuple[str, str]] = set()
    for project in projects:
        if type(project) is not dict or set(project) != {
            "default_base_mods", "default_source", "dev_root", "mission_roots", "mod", "mod_roots"
        }:
            raise ValueError("request_policy_project")
        if type(project["mod"]) is not str or not project["mod"]:
            raise ValueError("request_policy_mod")
        if not _valid_root(project["dev_root"]) or not _valid_root(project["default_source"]):
            raise ValueError("request_policy_root")
        if any(type(project[key]) is not list or not project[key] or not all(_valid_root(item) for item in project[key]) for key in ("mission_roots", "mod_roots")):
            raise ValueError("request_policy_roots")
        if type(project["default_base_mods"]) is not list or not all(type(item) is str and item for item in project["default_base_mods"]):
            raise ValueError("request_policy_base_mods")
        identity = (project["mod"].casefold(), project["dev_root"]["path"].casefold())
        if identity in seen:
            raise ValueError("request_policy_duplicate")
        seen.add(identity)


def _bundle_entry(path: Path, root: Path) -> dict[str, object]:
    details = _file_details(path, directory=False, open_reparse=True)
    if details["reparse_tag"] != 0 or details["links"] != 1:
        raise ValueError(f"bundle_input_not_regular:{path}")
    return {
        "kind": "bundle",
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _external_entry(path: Path) -> dict[str, object]:
    details = _file_details(path, directory=False, open_reparse=True)
    if details["reparse_tag"] != 0 or details["links"] != 1:
        raise ValueError(f"external_not_regular:{path}")
    return {
        "identity": {"file_id": details["file_id"], "volume_serial_number": details["volume_serial_number"]},
        "kind": "external",
        "path": str(path),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _build_contract(source_files: dict[str, bytes]) -> dict[str, object]:
    if set(source_files) != {"app_main.py", "launcher.cpp"}:
        raise ValueError("build_contract_sources")
    return {
        "builder_sha256": _sha256(Path(__file__).resolve()),
        "dependency_lock_sha256": _sha256(LOCK_PATH),
        "format_version": 1,
        "sources": {
            name: _sha256_bytes(source_files[name])
            for name in sorted(source_files)
        },
    }


def _manifest(staging: Path) -> dict[str, object]:
    bundle_files = sorted(
        (
            path for path in staging.rglob("*")
            if path.is_file() and path.relative_to(staging).as_posix() not in SPECIAL_BUNDLE_FILES
        ),
        key=lambda path: path.relative_to(staging).as_posix().casefold(),
    )
    entries = [_bundle_entry(path, staging) for path in bundle_files]
    entries.extend(_external_entry(path) for path in EXTERNAL_FILES)
    entries.sort(key=lambda item: (item["kind"], str(item["path"]).casefold()))
    hashes = {name: _sha256(TOOLS_DIR / "dayz_mcp" / name) for name in PACKAGED_MODULES}
    return {
        "bundle_id": BUNDLE_ID,
        "dayz_test_readiness_sha256": hashes["dayz_test_readiness.py"],
        "dayz_test_request_sha256": hashes["dayz_test_request.py"],
        "dayz_test_worker_sha256": hashes["dayz_test_worker.py"],
        "entries": entries,
        "format_version": 1,
        "native_broker_protocol_sha256": hashes["native_broker_protocol.py"],
        "request_policy_sha256": _sha256(staging / "request-policy.json"),
        "worker_runtime_sha256": _sha256(staging / "worker-runtime.json"),
    }


def _cpp_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _hex_bytes(value: str) -> str:
    return ",".join(f"0x{value[index:index + 2]}" for index in range(0, len(value), 2))


def _write_header(staging: Path, manifest: dict[str, object]) -> None:
    lines = [
        "#pragma once",
        "#include <windows.h>",
        "#include <stdint.h>",
        "enum class ClosureKind : BYTE { BUNDLE = 1, EXTERNAL = 2 };",
        "struct ClosureEntry { ClosureKind kind; const wchar_t* path; uint64_t size; BYTE sha256[32]; bool require_identity; uint64_t volume_serial_number; BYTE file_id[16]; };",
        f'#define DAYZ_MCP_MANIFEST_SHA256_HEX "{hashlib.sha256(canonical_json_bytes(manifest)).hexdigest().upper()}"',
        "inline constexpr ClosureEntry kClosureEntries[] = {",
    ]
    for item in manifest["entries"]:
        external = item["kind"] == "external"
        identity = item.get("identity", {"file_id": "0" * 32, "volume_serial_number": 0})
        lines.append(
            "    {ClosureKind::%s, L\"%s\", %dULL, {%s}, %s, %dULL, {%s}},"
            % (
                "EXTERNAL" if external else "BUNDLE",
                _cpp_escape(str(item["path"]).replace("/", "\\") if not external else str(item["path"])),
                item["size"],
                _hex_bytes(item["sha256"]),
                "true" if external else "false",
                identity["volume_serial_number"],
                _hex_bytes(identity["file_id"]),
            )
        )
    lines.extend(["};", "inline constexpr DWORD kClosureEntryCount = ARRAYSIZE(kClosureEntries);", ""])
    generated = staging / "generated"
    generated.mkdir()
    (generated / "closure_manifest.h").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _addon_target_root(mods_root: str) -> str:
    # ExpectedAddonPath concatenates root + prefix + "\\Addons".
    return mods_root.rstrip("\\") + "\\@"


def _addon_temp_root(build_temp_root: str) -> str:
    # ExpectedAddonPath concatenates root + prefix.
    return build_temp_root.rstrip("\\") + "\\"


def _addon_root_rows(source: dict[str, object]) -> list[tuple[str, str, str]]:
    validate_launcher_policy_source(source)
    rows: list[tuple[str, str, str]] = []
    for project in source["projects"]:
        rows.append(
            (
                str(project["mod"]),
                _addon_target_root(str(project["mods_root"])),
                _addon_temp_root(str(project["build_temp_root"])),
            )
        )
    return rows


def _write_addon_roots_header(staging: Path, source: dict[str, object]) -> None:
    lines = [
        "#pragma once",
        "#include <windows.h>",
        "struct AddonRootEntry { const wchar_t* prefix; const wchar_t* target_root; const wchar_t* temp_root; };",
        "inline constexpr AddonRootEntry kAddonRoots[] = {",
    ]
    for prefix, target_root, temp_root in _addon_root_rows(source):
        lines.append(
            '    {L"%s", L"%s", L"%s"},'
            % (_cpp_escape(prefix), _cpp_escape(target_root), _cpp_escape(temp_root))
        )
    lines.extend(
        [
            "};",
            "inline constexpr DWORD kAddonRootCount = ARRAYSIZE(kAddonRoots);",
            "",
        ]
    )
    generated = staging / "generated"
    generated.mkdir(exist_ok=True)
    (generated / "addon_roots.h").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _compile(staging: Path, lock: dict[str, object]) -> None:
    msvc = lock["toolchains"]["msvc"]
    sdk = lock["toolchains"]["windows_sdk"]
    cl = Path(msvc["files"]["cl"]["path"])
    root = cl.parents[3]
    sdk_version = str(sdk["version"])
    sdk_root = Path(r"C:\Program Files (x86)\Windows Kits\10")
    output = staging / "dayz-test-launcher.exe"
    obj = staging / "launcher.obj"
    source = staging / "src" / "launcher.cpp"
    compile_command = [
        str(cl), "/nologo", "/c", "/O2", "/Oi", "/GS", "/guard:cf", "/std:c++17",
        "/EHs-c-", "/GR-", "/Zl", "/Brepro", "/DUNICODE", "/D_UNICODE",
        f"/I{root / 'include'}", f"/I{sdk_root / 'Include' / sdk_version / 'um'}",
        f"/I{sdk_root / 'Include' / sdk_version / 'shared'}",
        f"/I{sdk_root / 'Include' / sdk_version / 'ucrt'}", f"/Fo{obj}", str(source),
    ]
    link_command = [
        str(Path(msvc["files"]["link"]["path"])), "/NOLOGO", "/NODEFAULTLIB",
        "/SUBSYSTEM:CONSOLE", "/ENTRY:wWinMainCRTStartup",
        "/DYNAMICBASE", "/NXCOMPAT", "/HIGHENTROPYVA", "/guard:cf", "/CETCOMPAT", "/Brepro",
        "/INCREMENTAL:NO", "/MANIFEST:NO", f"/OUT:{output}",
        f"/LIBPATH:{root / 'lib' / 'x64'}",
        f"/LIBPATH:{sdk_root / 'Lib' / sdk_version / 'um' / 'x64'}",
        str(obj), "kernel32.lib", "bcrypt.lib", "BufferOverflowU.lib",
        "libvcruntime.lib", "libcmt.lib",
    ]
    environment = {
        "SystemRoot": os.environ["SystemRoot"],
        "TEMP": tempfile.gettempdir(),
        "TMP": tempfile.gettempdir(),
        "PATH": str(cl.parent) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
    }
    for label, command in (("compile", compile_command), ("link", link_command)):
        completed = subprocess.run(command, cwd=staging, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
        if completed.returncode != 0:
            raise RuntimeError(f"native_{label}_failed\n" + completed.stdout)
    obj.unlink(missing_ok=True)


def _pe_contract(path: Path) -> None:
    raw = path.read_bytes()
    if raw[:2] != b"MZ" or len(raw) < 0x40:
        raise ValueError("pe_dos_header")
    offset = struct.unpack_from("<I", raw, 0x3C)[0]
    if raw[offset:offset + 4] != b"PE\0\0" or struct.unpack_from("<H", raw, offset + 4)[0] != 0x8664 or struct.unpack_from("<H", raw, offset + 24)[0] != 0x20B:
        raise ValueError("pe_machine")


def verify_bundle(
    bundle: Path,
    *,
    require_pe: bool = True,
    require_receipt: bool = True,
) -> None:
    manifest_path = bundle / "closure-manifest.json"
    manifest = _closed_load(manifest_path)
    if manifest_path.read_bytes() != canonical_json_bytes(manifest):
        raise ValueError("closure_noncanonical")
    expected_keys = {
        "bundle_id", "dayz_test_readiness_sha256", "dayz_test_request_sha256",
        "dayz_test_worker_sha256", "entries", "format_version",
        "native_broker_protocol_sha256", "request_policy_sha256",
        "worker_runtime_sha256",
    }
    if set(manifest) != expected_keys or manifest["format_version"] != 1 or manifest["bundle_id"] != BUNDLE_ID:
        raise ValueError("closure_schema")
    expected_bundle: set[str] = set()
    identities: list[tuple[str, str]] = []
    for item in manifest["entries"]:
        if type(item) is not dict or item.get("kind") not in {"bundle", "external"}:
            raise ValueError("closure_entry")
        expected = {"kind", "path", "sha256", "size"} | ({"identity"} if item["kind"] == "external" else set())
        if set(item) != expected:
            raise ValueError("closure_entry")
        identities.append((item["kind"], item["path"]))
        path = bundle / PurePosixPath(item["path"]) if item["kind"] == "bundle" else Path(item["path"])
        if item["kind"] == "bundle":
            expected_bundle.add(item["path"])
        if not path.exists():
            raise ValueError("closure_missing")
        if path.is_symlink():
            raise ValueError("closure_reparse")
        details = _file_details(path, directory=False, open_reparse=True)
        if details["reparse_tag"] != 0:
            raise ValueError("closure_reparse")
        if details["links"] != 1:
            raise ValueError("closure_hardlink")
        if path.stat().st_size != item["size"]:
            raise ValueError("closure_size")
        if _sha256(path) != item["sha256"]:
            raise ValueError("closure_hash")
        if item["kind"] == "external":
            identity = item["identity"]
            if not _valid_identity(identity) or identity["file_id"] != details["file_id"] or identity["volume_serial_number"] != details["volume_serial_number"]:
                raise ValueError("closure_identity")
    if identities != sorted(identities, key=lambda item: (item[0], item[1].casefold())) or len(identities) != len(set(identities)):
        raise ValueError("closure_order")
    live_bundle: set[str] = set()
    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise ValueError("closure_reparse")
        if path.is_file():
            live_bundle.add(path.relative_to(bundle).as_posix())
    allowed = expected_bundle | SPECIAL_BUNDLE_FILES
    missing = expected_bundle - live_bundle
    extra = live_bundle - allowed
    if missing:
        raise ValueError("closure_missing")
    if extra:
        raise ValueError("closure_extra")
    policy = _closed_load(bundle / "request-policy.json")
    if (bundle / "request-policy.json").read_bytes() != canonical_json_bytes(policy):
        raise ValueError("request_policy_noncanonical")
    validate_request_policy_document(policy)
    if _sha256(bundle / "request-policy.json") != manifest["request_policy_sha256"]:
        raise ValueError("request_policy_hash")
    with zipfile.ZipFile(bundle / "app.pyz") as archive:
        for name in PACKAGED_MODULES:
            archive_name = f"dayz_mcp/{name}"
            if archive.read(archive_name) != (TOOLS_DIR / "dayz_mcp" / name).read_bytes():
                raise ValueError("app_module_drift")
    contract_path = bundle / "build-contract.json"
    contract = _closed_load(contract_path)
    if contract_path.read_bytes() != canonical_json_bytes(contract):
        raise ValueError("build_contract_noncanonical")
    bundled_sources = {
        name: (bundle / "src" / name).read_bytes()
        for name in ("app_main.py", "launcher.cpp")
    }
    if contract != _build_contract(bundled_sources):
        raise ValueError("build_contract_drift")
    if require_pe:
        pe = bundle / "dayz-test-launcher.exe"
        _pe_contract(pe)
        marker = b"DAYZ_MCP_MANIFEST_SHA256=" + hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper().encode("ascii")
        if pe.read_bytes().count(marker) != 1:
            raise ValueError("pe_manifest_marker")
    if require_receipt:
        verify_reproducibility_receipt(
            bundle,
            require_reproducible=True,
        )


def _prepare_staging(
    staging: Path,
    archive: Path,
    lock: dict[str, object],
    source_files: dict[str, bytes],
    *,
    policy: Path | None = None,
) -> None:
    staging.mkdir(parents=True)
    source = staging / "src"
    source.mkdir()
    for name, raw in sorted(source_files.items()):
        (source / name).write_bytes(raw)
    _extract_cpython(archive, staging / "runtime")
    _extract_psutil(lock, staging / "vendor")
    _build_app_pyz(staging / "app.pyz")
    host = load_launcher_policy_source(resolve_launcher_policy_path(policy))
    policy_bytes, runtime_bytes = _emit_policy_documents(host)
    (staging / "request-policy.json").write_bytes(policy_bytes)
    (staging / "worker-runtime.json").write_bytes(runtime_bytes)
    (staging / "build-contract.json").write_bytes(
        canonical_json_bytes(_build_contract(source_files))
    )
    manifest = _manifest(staging)
    (staging / "closure-manifest.json").write_bytes(canonical_json_bytes(manifest))
    _write_header(staging, manifest)
    _write_addon_roots_header(staging, host)
    _compile(staging, lock)
    (staging / "generated" / "closure_manifest.h").unlink()
    (staging / "generated" / "addon_roots.h").unlink()
    (staging / "generated").rmdir()
    verify_bundle(staging, require_receipt=False)


def _artifact_fingerprint(bundle: Path) -> dict[str, str]:
    return {
        "app_pyz_sha256": _sha256(bundle / "app.pyz"),
        "manifest_sha256": _sha256(bundle / "closure-manifest.json"),
        "pe_sha256": _sha256(bundle / "dayz-test-launcher.exe"),
        "request_policy_sha256": _sha256(bundle / "request-policy.json"),
    }


def verify_reproducibility_receipt(
    bundle: Path,
    *,
    require_reproducible: bool,
) -> None:
    receipt_path = bundle / "reproducibility.json"
    receipt = _closed_load(receipt_path)
    if receipt_path.read_bytes() != canonical_json_bytes(receipt):
        raise ValueError("reproducibility_noncanonical")
    if set(receipt) != {
        "build_contract_sha256",
        "builds",
        "format_version",
        "reproducible",
    } or receipt.get("format_version") != 2:
        raise ValueError("reproducibility_schema")
    if (
        not _valid_sha256(receipt.get("build_contract_sha256"))
        or receipt["build_contract_sha256"]
        != _sha256(bundle / "build-contract.json")
        or type(receipt.get("reproducible")) is not bool
        or type(receipt.get("builds")) is not list
    ):
        raise ValueError("reproducibility_contract")
    reproducible = receipt["reproducible"]
    builds = receipt["builds"]
    expected_modes = _REPRODUCIBILITY_MODES if reproducible else ("unverified-final",)
    if require_reproducible and reproducible is not True:
        raise ValueError("reproducibility_required")
    if len(builds) != len(expected_modes):
        raise ValueError("reproducibility_builds")
    actual = _artifact_fingerprint(bundle)
    for index, mode in enumerate(expected_modes):
        item = builds[index]
        if (
            type(item) is not dict
            or set(item) != _FINGERPRINT_KEYS | {"mode"}
            or item.get("mode") != mode
            or any(not _valid_sha256(item.get(key)) for key in _FINGERPRINT_KEYS)
            or {key: item[key] for key in _FINGERPRINT_KEYS} != actual
        ):
            raise ValueError("reproducibility_build")


def build(
    output: Path,
    *,
    offline: bool,
    verify_reproducible: bool,
    policy: Path | None = None,
) -> dict[str, str]:
    lock = _load_verified_lock()
    source_files = {
        name: (CANONICAL_BUNDLE / "src" / name).read_bytes()
        for name in ("app_main.py", "launcher.cpp")
    }
    fingerprints: list[dict[str, str]] = []
    archives: list[Path] = []
    if verify_reproducible:
        archives = [
            _acquire_cpython(lock, offline=offline),
            _acquire_cpython(lock, offline=offline),
            _acquire_cpython(lock, offline=True),
        ]
        with tempfile.TemporaryDirectory(prefix="dayz-mcp-repro-") as directory:
            base = Path(directory)
            for index, archive in enumerate(archives):
                staging = base / f"build-{index + 1}"
                _prepare_staging(staging, archive, lock, source_files, policy=policy)
                fingerprints.append(_artifact_fingerprint(staging))
            if fingerprints[0] != fingerprints[1] or fingerprints[0] != fingerprints[2]:
                raise ValueError("reproducible_build_mismatch")
    else:
        archives = [_acquire_cpython(lock, offline=offline)]
    with tempfile.TemporaryDirectory(prefix="dayz-mcp-final-") as directory:
        staging = Path(directory) / BUNDLE_ID
        _prepare_staging(staging, archives[-1], lock, source_files, policy=policy)
        final_fingerprint = _artifact_fingerprint(staging)
        if fingerprints and final_fingerprint != fingerprints[0]:
            raise ValueError("final_build_mismatch")
        receipt_builds = (
            [
                {"mode": mode, **fingerprint}
                for mode, fingerprint in zip(
                    _REPRODUCIBILITY_MODES,
                    fingerprints,
                    strict=True,
                )
            ]
            if verify_reproducible
            else [{"mode": "unverified-final", **final_fingerprint}]
        )
        receipt = {
            "build_contract_sha256": _sha256(staging / "build-contract.json"),
            "builds": receipt_builds,
            "format_version": 2,
            "reproducible": bool(verify_reproducible),
        }
        (staging / "reproducibility.json").write_bytes(canonical_json_bytes(receipt))
        verify_bundle(staging, require_receipt=False)
        verify_reproducibility_receipt(
            staging,
            require_reproducible=verify_reproducible,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        previous = output.with_name(output.name + ".previous")
        if previous.exists():
            shutil.rmtree(previous)
        if output.exists():
            os.replace(output, previous)
        try:
            os.replace(staging, output)
        except BaseException:
            if previous.exists() and not output.exists():
                os.replace(previous, output)
            raise
        if previous.exists():
            shutil.rmtree(previous)
    verify_bundle(output, require_receipt=False)
    verify_reproducibility_receipt(
        output,
        require_reproducible=verify_reproducible,
    )
    return _artifact_fingerprint(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the sealed dayz-test-v1 native launcher bundle")
    parser.add_argument("--output", type=Path, default=CANONICAL_BUNDLE)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--verify-reproducible", action="store_true")
    parser.add_argument("--policy", type=Path, default=None)
    options = parser.parse_args(argv)
    result = build(
        options.output.resolve(),
        offline=options.offline,
        verify_reproducible=options.verify_reproducible,
        policy=options.policy,
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
