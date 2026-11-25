"""Installed-wheel setup: find the local game, check the halves, register."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import install_mcp
from dayz_mcp import dayz_tools_paths
from dayz_mcp.install_layout import installed_venv_root
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server_cli import (
    InstanceSelectionError,
    bind_instance_context,
    registration_name,
    reject_conflicting_environment,
)

WORKSHOP_APP_ID = "221100"
WORKSHOP_ITEM_ID = ""

_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[{}]')


def _wheel_version_path() -> Path:
    return Path(__file__).resolve().parent / "VERSION.json"


def workshop_item_root(
    registry: Callable[[str, str, str], str | None] | None = None,
) -> Path | None:
    if WORKSHOP_ITEM_ID == "":
        return None
    reader = dayz_tools_paths.read_registry_string if registry is None else registry
    steam_root: Path | None = None
    for hive, subkey, value in dayz_tools_paths.REGISTRY_STEAM_KEYS:
        raw = reader(hive, subkey, value)
        if not raw:
            continue
        candidate = Path(raw)
        try:
            has_folders = (candidate / "steamapps" / "libraryfolders.vdf").is_file()
        except OSError:
            has_folders = False
        if has_folders:
            steam_root = candidate
            break
    if steam_root is None:
        return None
    library_root = _read_libraryfolders(steam_root / "steamapps" / "libraryfolders.vdf")
    if library_root is None:
        return None
    return (
        library_root
        / "steamapps"
        / "workshop"
        / "content"
        / WORKSHOP_APP_ID
        / WORKSHOP_ITEM_ID
    )


def _emit(payload: dict[str, object]) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("ok") is True else 2


def _reject(missing: list[dict[str, object]]) -> int:
    return _emit({"error": missing[0], "missing": missing, "ok": False})


def _unquote(token: str) -> str:
    inner = token[1:-1]
    return inner.replace("\\\\", "\\").replace('\\"', '"')


def _parse_vdf_block(tokens: list[str], index: int) -> tuple[dict[str, object], int]:
    result: dict[str, object] = {}
    while index < len(tokens):
        token = tokens[index]
        if token == "}":
            return result, index + 1
        if token == "{" or not token.startswith('"'):
            raise ValueError("invalid_vdf")
        key = _unquote(token)
        index += 1
        if index >= len(tokens):
            raise ValueError("invalid_vdf")
        if tokens[index] == "{":
            value, index = _parse_vdf_block(tokens, index + 1)
            result[key] = value
        elif tokens[index].startswith('"'):
            result[key] = _unquote(tokens[index])
            index += 1
        else:
            raise ValueError("invalid_vdf")
    raise ValueError("invalid_vdf")


def _read_libraryfolders(path: Path) -> Path | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    tokens = _TOKEN_RE.findall(text)
    # Anything outside quoted strings and braces is another format.
    if _TOKEN_RE.sub("", text).strip():
        return None
    try:
        if not tokens or not tokens[0].startswith('"'):
            return None
        root_key = _unquote(tokens[0])
        if root_key.casefold() != "libraryfolders" or len(tokens) < 2 or tokens[1] != "{":
            return None
        libraries, index = _parse_vdf_block(tokens, 2)
        if index != len(tokens):
            return None
    except (ValueError, IndexError):
        return None
    for library in libraries.values():
        if not isinstance(library, dict):
            continue
        library_path: object = None
        apps: object = None
        for key, value in library.items():
            if key.casefold() == "path":
                library_path = value
            elif key.casefold() == "apps":
                apps = value
        if isinstance(library_path, str) and isinstance(apps, dict) and WORKSHOP_APP_ID in apps:
            return Path(library_path)
    return None


def _version_key(version: str) -> tuple[int, ...] | None:
    parts = version.split(".")
    if not parts or any(not part.isdecimal() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _compare_versions(item_version: str, wheel_version: str) -> str:
    item_key = _version_key(item_version)
    wheel_key = _version_key(wheel_version)
    if item_key is None or wheel_key is None:
        return "differs"
    size = max(len(item_key), len(wheel_key))
    item_padded = item_key + (0,) * (size - len(item_key))
    wheel_padded = wheel_key + (0,) * (size - len(wheel_key))
    if item_padded > wheel_padded:
        return "item_ahead"
    if item_padded < wheel_padded:
        return "item_behind"
    return "differs"


def _read_version_json(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("version"), str) or not isinstance(payload.get("pbo_sha256"), str):
        return None
    return payload


def _first_file_on_path(name: str) -> Path | None:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        candidate = Path(entry) / name
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def find_native_cli(role: str) -> Path | None:
    """First native x64 ``ROLE.exe`` on the PATH or behind its ``ROLE.cmd`` shim."""
    expected = f"{role}.exe"
    candidates: list[Path] = []
    found_exe = _first_file_on_path(expected)
    if found_exe is not None:
        candidates.append(found_exe)
    found_cmd = _first_file_on_path(f"{role}.cmd")
    if found_cmd is not None:
        shim = found_cmd
        candidates.append(shim.parent / expected)
        try:
            shim_text = shim.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            shim_text = ""
        pattern = re.compile(
            r"[A-Za-z]:\\[^\"\r\n|<>]*\\" + re.escape(role) + r"\.exe",
            re.IGNORECASE,
        )
        for match in pattern.finditer(shim_text):
            candidates.append(Path(match.group(0)))
    for candidate in candidates:
        if candidate.name.casefold() != expected.casefold():
            continue
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        try:
            install_mcp._validate_x64_pe(candidate)
        except (install_mcp.InstallerContractError, OSError, ValueError):
            continue
        return candidate.resolve()
    return None


def _resolve_cli(role: str, explicit: str | None) -> Path | None:
    if explicit is None:
        return find_native_cli(role)
    candidate = Path(explicit)
    if candidate.name.casefold() != f"{role}.exe":
        return None
    try:
        if not candidate.is_file():
            return None
        install_mcp._validate_x64_pe(candidate)
    except (install_mcp.InstallerContractError, OSError, ValueError):
        return None
    return candidate.resolve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dayz-mcp setup", allow_abbrev=False)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--instance", default=None)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--steam", default=None)
    parser.add_argument("--dayz", default=None)
    parser.add_argument("--item", default=None)
    parser.add_argument("--server-profiles", default=None)
    parser.add_argument("--client-profiles", default=None)
    parser.add_argument("--claude-exe", default=None)
    parser.add_argument("--codex-exe", default=None)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., object] = subprocess.run,
    registry: Callable[[str, str, str], str | None] | None = None,
    token_factory: Callable[[], str] | None = None,
) -> int:
    args = _build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    registry_reader = dayz_tools_paths.read_registry_string if registry is None else registry
    key_factory: Callable[[], str] = (
        (lambda: secrets.token_urlsafe(32)) if token_factory is None else token_factory
    )
    port = args.port
    token = args.instance

    # Phase 1: check everything, write nothing.
    package_init = Path(__file__).resolve().parent / "__init__.py"
    root = installed_venv_root(package_init)
    if root is None:
        return _reject(
            [
                {
                    "code": "not_installed_from_wheel",
                    "remedy": "run setup from the venv that has the dayz-mcp wheel installed; from a clone, follow QUICKSTART.md",
                }
            ]
        )
    venv_python = root / "Scripts" / "python.exe"

    if not 1 <= port <= 65535:
        return _reject(
            [
                {
                    "code": "port_out_of_range",
                    "remedy": "pass --port between 1 and 65535",
                }
            ]
        )

    try:
        bind_instance_context(token, None, replace=True)
        reject_conflicting_environment(token, port)
    except InstanceSelectionError as error:
        return _reject([{"code": error.code}])

    state_root = RuntimePaths.for_token(token).root
    missing: list[dict[str, object]] = []

    steam_root: Path | None = None
    library_root: Path | None = None
    needs_steam = args.dayz is None or args.item is None
    if needs_steam:
        if args.steam is not None:
            steam_root = Path(args.steam)
        else:
            for hive, subkey, value in dayz_tools_paths.REGISTRY_STEAM_KEYS:
                raw = registry_reader(hive, subkey, value)
                if not raw:
                    continue
                candidate = Path(raw)
                try:
                    has_folders = (candidate / "steamapps" / "libraryfolders.vdf").is_file()
                except OSError:
                    has_folders = False
                if has_folders:
                    steam_root = candidate
                    break
        if steam_root is None:
            missing.append(
                {
                    "code": "steam_missing",
                    "remedy": "install Steam, or pass --steam PATH",
                }
            )
        else:
            library_root = _read_libraryfolders(
                steam_root / "steamapps" / "libraryfolders.vdf"
            )

    dayz_root: Path | None = None
    if args.dayz is not None:
        dayz_root = Path(args.dayz)
    elif library_root is not None:
        dayz_root = library_root / "steamapps" / "common" / "DayZ"
    if args.dayz is not None or library_root is not None:
        try:
            dayz_ok = dayz_root is not None and (dayz_root / "DayZ_x64.exe").is_file()
        except OSError:
            dayz_ok = False
        if not dayz_ok:
            missing.append(
                {
                    "code": "dayz_missing",
                    "remedy": "install DayZ from Steam and start it once, or pass --dayz PATH",
                }
            )
    elif steam_root is not None:
        # Steam was found but no library holds the game: the default DayZ
        # location cannot be derived, so DayZ counts as missing.
        missing.append(
            {
                "code": "dayz_missing",
                "remedy": "install DayZ from Steam and start it once, or pass --dayz PATH",
            }
        )

    item_root: Path | None = None
    item_ok = False
    if args.item is not None:
        item_root = Path(args.item)
    elif library_root is not None and WORKSHOP_ITEM_ID != "":
        item_root = (
            library_root
            / "steamapps"
            / "workshop"
            / "content"
            / WORKSHOP_APP_ID
            / WORKSHOP_ITEM_ID
        )
    if args.item is not None or library_root is not None:
        try:
            item_ok = item_root is not None and (
                item_root / "Addons" / "DayZ_MCP.pbo"
            ).is_file()
        except OSError:
            item_ok = False
        if not item_ok:
            problem: dict[str, object] = {
                "code": "workshop_item_missing",
                "remedy": "subscribe to the DayZ-MCP item in the Steam Workshop, let Steam download it, then run dayz-mcp setup --json again; or pass --item PATH",
            }
            if item_root is not None:
                problem["path"] = str(item_root)
            if WORKSHOP_ITEM_ID != "":
                problem["url"] = (
                    "https://steamcommunity.com/sharedfiles/filedetails/?id="
                    + WORKSHOP_ITEM_ID
                )
            missing.append(problem)
    elif steam_root is not None:
        problem = {
            "code": "workshop_item_missing",
            "remedy": "subscribe to the DayZ-MCP item in the Steam Workshop, let Steam download it, then run dayz-mcp setup --json again; or pass --item PATH",
        }
        if WORKSHOP_ITEM_ID != "":
            problem["url"] = (
                "https://steamcommunity.com/sharedfiles/filedetails/?id="
                + WORKSHOP_ITEM_ID
            )
        missing.append(problem)

    server_profiles = (
        Path(args.server_profiles)
        if args.server_profiles is not None
        else state_root / "profiles" / "server"
    )
    client_profiles = (
        Path(args.client_profiles)
        if args.client_profiles is not None
        else state_root / "profiles" / "client"
    )
    if args.server_profiles is not None:
        try:
            server_ok = server_profiles.is_dir()
        except OSError:
            server_ok = False
        if not server_ok:
            missing.append(
                {
                    "code": "profiles_missing",
                    "path": str(server_profiles),
                    "remedy": "create that folder or leave the option out",
                    "which": "server",
                }
            )
    if args.client_profiles is not None:
        try:
            client_ok = client_profiles.is_dir()
        except OSError:
            client_ok = False
        if not client_ok:
            missing.append(
                {
                    "code": "profiles_missing",
                    "path": str(client_profiles),
                    "remedy": "create that folder or leave the option out",
                    "which": "client",
                }
            )

    wheel_version_path = _wheel_version_path()
    wheel_version: dict[str, object] | None = None
    try:
        wheel_exists = wheel_version_path.is_file()
    except OSError:
        wheel_exists = False
    if not wheel_exists:
        missing.append(
            {
                "code": "version_json_missing",
                "path": str(wheel_version_path),
                "remedy": "reinstall the dayz-mcp wheel of this release, then run dayz-mcp setup --json again",
                "which": "wheel",
            }
        )
    else:
        wheel_version = _read_version_json(wheel_version_path)
        if wheel_version is None:
            missing.append(
                {
                    "code": "version_json_invalid",
                    "path": str(wheel_version_path),
                    "remedy": "reinstall the dayz-mcp wheel of this release, then run dayz-mcp setup --json again",
                    "which": "wheel",
                }
            )

    item_version: dict[str, object] | None = None
    if item_root is not None:
        item_version_path = item_root / "VERSION.json"
        try:
            item_exists = item_version_path.is_file()
        except OSError:
            item_exists = False
        if not item_exists:
            missing.append(
                {
                    "code": "version_json_missing",
                    "path": str(item_version_path),
                    "remedy": "let Steam download the DayZ-MCP Workshop item of this release, then run dayz-mcp setup --json again",
                    "which": "item",
                }
            )
        else:
            item_version = _read_version_json(item_version_path)
            if item_version is None:
                missing.append(
                    {
                        "code": "version_json_invalid",
                        "path": str(item_version_path),
                        "remedy": "let Steam download the DayZ-MCP Workshop item of this release, then run dayz-mcp setup --json again",
                        "which": "item",
                    }
                )

    if wheel_version is not None and item_version is not None and item_root is not None:
        pbo_path = item_root / "Addons" / "DayZ_MCP.pbo"
        try:
            pbo_sha256 = hashlib.sha256(pbo_path.read_bytes()).hexdigest()
            pbo_readable = True
        except OSError:
            pbo_readable = False
            pbo_sha256 = ""
        if not pbo_readable:
            if item_ok:
                missing.append(
                    {
                        "code": "workshop_item_unreadable",
                        "path": str(pbo_path),
                        "remedy": "close whatever holds DayZ_MCP.pbo open (Steam may still be updating the item), then run dayz-mcp setup --json again",
                    }
                )
        elif pbo_sha256 != wheel_version.get("pbo_sha256"):
            item_text = str(item_version.get("version"))
            wheel_text = str(wheel_version.get("version"))
            direction = _compare_versions(item_text, wheel_text)
            if direction == "item_ahead":
                remedy = (
                    f"install the dayz-mcp wheel of release {item_text}, "
                    "then run dayz-mcp setup --json again"
                )
            elif direction == "item_behind":
                remedy = (
                    "let Steam update the DayZ-MCP Workshop item, "
                    "then run dayz-mcp setup --json again"
                )
            else:
                remedy = (
                    "install the dayz-mcp wheel and the Workshop item of the same release, "
                    "then run dayz-mcp setup --json again"
                )
            missing.append(
                {
                    "code": "halves_mismatch",
                    "direction": direction,
                    "item_version": item_text,
                    "remedy": remedy,
                    "wheel_version": wheel_text,
                }
            )

    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        expected_home = Path.home() / ".codex"
        if os.path.normcase(os.path.abspath(codex_home)) != os.path.normcase(
            os.path.abspath(expected_home)
        ):
            missing.append(
                {
                    "code": "codex_home_unsupported",
                    "path": codex_home,
                    "remedy": "unset CODEX_HOME or set it to %USERPROFILE%\\.codex: the daemon reads only that Codex config",
                }
            )

    claude_exe = _resolve_cli("claude", args.claude_exe)
    if claude_exe is None:
        missing.append(
            {
                "code": "installer_cli_missing",
                "remedy": "install the native claude CLI, or pass --claude-exe PATH",
                "role": "claude",
            }
        )
    codex_exe = _resolve_cli("codex", args.codex_exe)
    if codex_exe is None:
        missing.append(
            {
                "code": "installer_cli_missing",
                "remedy": "install the native codex CLI, or pass --codex-exe PATH",
                "role": "codex",
            }
        )

    if missing:
        return _reject(missing)

    # Phase 2: pin, key, configs, registration.
    assert item_root is not None and wheel_version is not None
    assert claude_exe is not None and codex_exe is not None
    keyfile = state_root / "dayz_mcp.key"
    try:
        install_mcp.pin_installer_clis(
            claude_exe=claude_exe, codex_exe=codex_exe, runner=runner
        )
        key = install_mcp._load_or_create_key(keyfile, key_factory)
        payload_text = install_mcp.config_payload(port, key)
        install_mcp._write_config(server_profiles / "dayz_mcp.json", payload_text)
        install_mcp._write_config(client_profiles / "dayz_mcp.json", payload_text)
        options = install_mcp.InstallerOptions(
            port=port,
            keyfile=keyfile,
            server_profiles=server_profiles,
            client_profiles=client_profiles,
            mission_path=None,
            expected_game_version="",
            idle_timeout_seconds=1800.0,
            allow_legacy=False,
            register=True,
            pin_clis=False,
            claude_exe=None,
            codex_exe=None,
            tools_root=Path(install_mcp.__file__).resolve().parent,
            skip_knowledge_pack=True,
            instance_token=token,
        )
        install_mcp.register_clients(options, venv_python, runner=runner)
    except (
        install_mcp.InstallerContractError,
        install_mcp.InstallerExecutionError,
        OSError,
        ValueError,
    ) as error:
        problem: dict[str, object] = {
            "code": install_mcp._public_installer_error_code(error)
        }
        remedy = getattr(error, "remedy", None)
        if isinstance(remedy, str) and remedy:
            problem["remedy"] = remedy
        return _emit({"error": problem, "missing": [], "ok": False})

    return _emit(
        {
            "client_profiles_arg": f"-profiles={client_profiles}",
            "item": str(item_root),
            "keyfile": str(keyfile),
            "missing": [],
            "mod_arg": f"-mod={item_root}",
            "ok": True,
            "port": port,
            "python": str(venv_python),
            "registered": ["claude", "codex"],
            "server_name": registration_name(token),
            "server_profiles_arg": f"-profiles={server_profiles}",
            "version": str(wheel_version.get("version")),
        }
    )
