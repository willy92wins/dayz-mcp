from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Sequence


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from install_mcp import (
    INSTALLER_CLI_MANIFEST,
    InstallerContractError,
    build_installer_cli_entry_payload,
    invoke_manifest_cli,
    load_installer_cli_manifest,
)


class _Guid(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )


class _WintrustFileInfo(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.POINTER(_Guid)),
    )


class _WintrustData(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", wintypes.LPVOID),
        ("pSIPClientData", wintypes.LPVOID),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.POINTER(_WintrustFileInfo)),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", wintypes.LPVOID),
    )


_WINTRUST_ACTION_GENERIC_VERIFY_V2 = _Guid(
    0x00AAC56B,
    0xCD44,
    0x11D0,
    (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
)
_WTD_UI_NONE = 2
_WTD_REVOKE_NONE = 0
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_VERIFY = 1
_WTD_STATEACTION_CLOSE = 2
_WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000
_ABSENT_PROBE_NAME = "p0s-absent-fixture-do-not-create"
INSTALLER_NOT_FOUND_FIXTURES = (
    TOOLS_DIR.parent
    / "reports"
    / "security"
    / "installer-not-found-fixtures-v1.json"
)


def authenticode_valid(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
    except (AttributeError, OSError):
        return False
    verify = wintrust.WinVerifyTrust
    verify.argtypes = [wintypes.HWND, ctypes.POINTER(_Guid), wintypes.LPVOID]
    verify.restype = wintypes.LONG

    file_info = _WintrustFileInfo(
        ctypes.sizeof(_WintrustFileInfo),
        str(Path(path)),
        None,
        None,
    )
    trust_data = _WintrustData(
        ctypes.sizeof(_WintrustData),
        None,
        None,
        _WTD_UI_NONE,
        _WTD_REVOKE_NONE,
        _WTD_CHOICE_FILE,
        ctypes.pointer(file_info),
        _WTD_STATEACTION_VERIFY,
        None,
        None,
        _WTD_CACHE_ONLY_URL_RETRIEVAL,
        0,
        None,
    )
    status = verify(
        wintypes.HWND(-1),
        ctypes.byref(_WINTRUST_ACTION_GENERIC_VERIFY_V2),
        ctypes.byref(trust_data),
    )
    trust_data.dwStateAction = _WTD_STATEACTION_CLOSE
    verify(
        wintypes.HWND(-1),
        ctypes.byref(_WINTRUST_ACTION_GENERIC_VERIFY_V2),
        ctypes.byref(trust_data),
    )
    return status == 0


def host_cli_provenance(role: str, path: Path) -> bool:
    try:
        candidate = Path(path).resolve(strict=True)
    except OSError:
        return False
    normalized = os.path.normcase(str(candidate))
    if role == "CLAUDE":
        expected = (Path.home() / ".local" / "bin" / "claude.exe").resolve()
        return normalized == os.path.normcase(str(expected))
    if role != "CODEX":
        return False

    appdata = os.environ.get("APPDATA")
    if not appdata:
        return False
    expected = (
        Path(appdata)
        / "npm"
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    ).resolve()
    return normalized == os.path.normcase(str(expected))


def create_installer_cli_manifest(
    output_path: Path,
    cli_paths: dict[str, Path],
    *,
    provenance_checker: Callable[[str, Path], bool] = host_cli_provenance,
    signature_checker: Callable[[Path], bool] = authenticode_valid,
) -> Path:
    output = Path(output_path)
    if output.exists():
        raise FileExistsError("installer CLI manifest is create-only")
    if set(cli_paths) != {"CLAUDE", "CODEX"}:
        raise InstallerContractError("invalid_installer_cli_roles")

    entries: dict[str, dict[str, object]] = {}
    for role in ("CLAUDE", "CODEX"):
        path = Path(cli_paths[role])
        if not provenance_checker(role, path):
            raise InstallerContractError("installer_cli_provenance_rejected")
        if not signature_checker(path):
            raise InstallerContractError("installer_cli_signature_rejected")
        entries[role] = build_installer_cli_entry_payload(role, path)

    payload = {
        "schema_version": 1,
        "kind": "dayz-mcp-installer-clis-v1",
        "entries": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        created = True
        load_installer_cli_manifest(output)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return output


def capture_installer_not_found_fixtures(
    output_path: Path,
    *,
    manifest_path: Path = INSTALLER_CLI_MANIFEST,
    runner: Callable[..., object] = subprocess.run,
) -> Path:
    output = Path(output_path)
    if output.exists():
        raise FileExistsError("installer not-found fixture is create-only")
    manifest_file = Path(manifest_path)
    manifest_bytes = manifest_file.read_bytes()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest().upper()
    manifest = load_installer_cli_manifest(manifest_file)

    entries: dict[str, dict[str, object]] = {}
    for role in ("CLAUDE", "CODEX"):
        arguments = ["mcp", "get", _ABSENT_PROBE_NAME]
        if role == "CODEX":
            arguments.append("--json")
        try:
            completed = invoke_manifest_cli(manifest.entries[role], arguments, runner)
        except OSError as error:
            winerror = getattr(error, "winerror", None)
            raise InstallerContractError(
                f"installer_cli_launch_failed:{role}:{winerror}"
            ) from error
        returncode = getattr(completed, "returncode", None)
        stdout = getattr(completed, "stdout", None)
        stderr = getattr(completed, "stderr", None)
        if (
            not isinstance(returncode, int)
            or isinstance(returncode, bool)
            or returncode == 0
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
            or len(stdout) > 4096
            or len(stderr) > 4096
            or "\0" in stdout
            or "\0" in stderr
        ):
            raise InstallerContractError("invalid_installer_not_found_probe")
        entries[role] = {
            "cli_sha256": manifest.entries[role].sha256,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    if manifest_file.read_bytes() != manifest_bytes:
        raise InstallerContractError("installer_cli_manifest_drift")
    payload = {
        "schema_version": 1,
        "kind": "dayz-mcp-installer-not-found-fixtures-v1",
        "probe_name": _ABSENT_PROBE_NAME,
        "cli_manifest": {
            "bytes": len(manifest_bytes),
            "sha256": manifest_hash,
        },
        "entries": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        created = True
        persisted = json.loads(output.read_text(encoding="utf-8"))
        if persisted != payload:
            raise InstallerContractError("installer_not_found_fixture_write_drift")
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DayZ MCP P0.S security gates")
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze-installer-clis")
    freeze.add_argument("--claude-exe", type=Path, required=True)
    freeze.add_argument("--codex-exe", type=Path, required=True)
    subparsers.add_parser("probe-installer-not-found")
    backup = subparsers.add_parser("backup-runs-v1")
    backup.add_argument("--port", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "freeze-installer-clis":
        created = create_installer_cli_manifest(
            INSTALLER_CLI_MANIFEST,
            {"CLAUDE": args.claude_exe, "CODEX": args.codex_exe},
        )
        print(json.dumps({"status": "created", "path": str(created)}))
        return 0
    if args.command == "probe-installer-not-found":
        created = capture_installer_not_found_fixtures(INSTALLER_NOT_FOUND_FIXTURES)
        print(json.dumps({"status": "created", "path": str(created)}))
        return 0
    if args.command == "backup-runs-v1":
        from dayz_mcp.identity_migration import ensure_runs_v1_backup
        from dayz_mcp.runtime_state import RuntimePaths

        receipt = ensure_runs_v1_backup(RuntimePaths.from_env(), args.port)
        print(
            json.dumps(
                {
                    "status": "verified",
                    "source_absent": receipt["source_absent"],
                },
                sort_keys=True,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
