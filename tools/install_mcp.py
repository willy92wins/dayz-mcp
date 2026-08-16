from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import stat
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from dayz_mcp.host_config import (
    CLAUDE_TIMEOUT_MS,
    CODEX_TIMEOUT_SECONDS,
    apply_host_timeouts,
)


TOOLS_ROOT = Path(__file__).resolve().parent
INSTALLER_CLI_MANIFEST = (
    TOOLS_ROOT.parent / "reports" / "security" / "installer-cli-manifest-v1.json"
)
_EXPECTED_ROLES = {"CLAUDE": "claude.exe", "CODEX": "codex.exe"}
_HEX_UPPER = frozenset("0123456789ABCDEF")
_PE_X64_MACHINE = 0x8664
_PE32_PLUS_MAGIC = 0x20B
_PSUTIL_WHEEL_BYTES = 137_737
_PSUTIL_WHEEL_SHA256 = "eb7e81434c8d223ec4a219b5fc1c47d0417b12be7ea866e24fb5ad6e84b3d988"


class InstallerContractError(RuntimeError):
    pass


class InstallerExecutionError(RuntimeError):
    pass


class RegistrationTransactionError(InstallerExecutionError):
    pass


class RegistrationRollbackError(InstallerExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class InstallerOptions:
    port: int
    keyfile: Path
    server_profiles: Path | None
    client_profiles: Path | None
    mission_path: Path | None
    expected_game_version: str
    idle_timeout_seconds: float
    allow_legacy: bool
    register: bool
    tools_root: Path


@dataclass(frozen=True, slots=True)
class InstallerCliEntry:
    role: str
    path: Path
    bytes: int
    sha256: str

    def revalidate(self) -> None:
        _validate_cli_entry(
            self.role,
            {
                "path": str(self.path),
                "bytes": self.bytes,
                "sha256": self.sha256,
            },
        )


@dataclass(frozen=True, slots=True)
class InstallerCliManifest:
    entries: dict[str, InstallerCliEntry]


@dataclass(frozen=True, slots=True)
class InstallerNotFoundEntry:
    cli_sha256: str
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class InstallerNotFoundFixtures:
    entries: dict[str, InstallerNotFoundEntry]


@dataclass(frozen=True, slots=True)
class RegistrationSpec:
    command: Path
    arguments: tuple[str, ...]


class RegistrationProvider(Protocol):
    def get(self, role: str) -> RegistrationSpec | None: ...

    def remove(self, role: str) -> None: ...

    def add(self, role: str, spec: RegistrationSpec) -> None: ...


CommandRunner = Callable[..., object]


def _native_regular_file(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise InstallerContractError("installer_cli_missing") from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or bool(file_attributes & reparse_flag)
    ):
        raise InstallerContractError("installer_cli_not_native_regular_file")
    return metadata


def _validate_x64_pe(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            dos_header = handle.read(64)
            if len(dos_header) != 64 or dos_header[:2] != b"MZ":
                raise InstallerContractError("installer_cli_not_pe")
            pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
            if pe_offset < 64:
                raise InstallerContractError("installer_cli_not_pe")
            handle.seek(pe_offset)
            pe_header = handle.read(26)
    except OSError as error:
        raise InstallerContractError("installer_cli_unreadable") from error

    if (
        len(pe_header) != 26
        or pe_header[:4] != b"PE\0\0"
        or struct.unpack_from("<H", pe_header, 4)[0] != _PE_X64_MACHINE
        or struct.unpack_from("<H", pe_header, 24)[0] != _PE32_PLUS_MAGIC
    ):
        raise InstallerContractError("installer_cli_not_native_x64_pe")


def _validate_cli_entry(role: str, value: object) -> InstallerCliEntry:
    if role not in _EXPECTED_ROLES or not isinstance(value, dict):
        raise InstallerContractError("invalid_installer_cli_manifest")
    if set(value) != {"path", "bytes", "sha256"}:
        raise InstallerContractError("invalid_installer_cli_manifest")

    raw_path = value.get("path")
    expected_bytes = value.get("bytes")
    expected_sha256 = value.get("sha256")
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes <= 0
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in _HEX_UPPER for character in expected_sha256)
    ):
        raise InstallerContractError("invalid_installer_cli_manifest")

    path = Path(raw_path)
    if not path.is_absolute() or path.suffix.casefold() != ".exe":
        raise InstallerContractError("installer_cli_path_not_absolute_exe")
    if path.name.casefold() != _EXPECTED_ROLES[role]:
        raise InstallerContractError("installer_cli_role_path_mismatch")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise InstallerContractError("installer_cli_missing") from error
    if os.path.normcase(str(path)) != os.path.normcase(str(resolved)):
        raise InstallerContractError("installer_cli_path_not_canonical")

    metadata = _native_regular_file(path)
    if metadata.st_size != expected_bytes:
        raise InstallerContractError("installer_cli_byte_drift")
    _validate_x64_pe(path)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    except OSError as error:
        raise InstallerContractError("installer_cli_unreadable") from error
    if digest != expected_sha256:
        raise InstallerContractError("installer_cli_hash_drift")

    return InstallerCliEntry(role, path, expected_bytes, expected_sha256)


def load_installer_cli_manifest(path: Path) -> InstallerCliManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallerContractError("invalid_installer_cli_manifest") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "kind", "entries"}
        or payload.get("schema_version") != 1
        or payload.get("kind") != "dayz-mcp-installer-clis-v1"
    ):
        raise InstallerContractError("invalid_installer_cli_manifest")
    entries = payload.get("entries")
    if not isinstance(entries, dict) or set(entries) != set(_EXPECTED_ROLES):
        raise InstallerContractError("invalid_installer_cli_manifest")
    return InstallerCliManifest(
        {role: _validate_cli_entry(role, entries[role]) for role in sorted(entries)}
    )


def build_installer_cli_entry_payload(role: str, path: Path) -> dict[str, object]:
    candidate = Path(path)
    try:
        payload = candidate.read_bytes()
    except OSError as error:
        raise InstallerContractError("installer_cli_unreadable") from error
    entry: dict[str, object] = {
        "path": str(candidate),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest().upper(),
    }
    _validate_cli_entry(role, entry)
    return entry


def load_installer_not_found_fixtures(
    fixture_path: Path,
    manifest_path: Path,
) -> InstallerNotFoundFixtures:
    manifest_file = Path(manifest_path)
    try:
        manifest_bytes = manifest_file.read_bytes()
        payload = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallerContractError("invalid_installer_not_found_fixture") from error
    manifest = load_installer_cli_manifest(manifest_file)
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {"schema_version", "kind", "probe_name", "cli_manifest", "entries"}
        or payload.get("schema_version") != 1
        or payload.get("kind") != "dayz-mcp-installer-not-found-fixtures-v1"
        or payload.get("probe_name") != "p0s-absent-fixture-do-not-create"
    ):
        raise InstallerContractError("invalid_installer_not_found_fixture")
    binding = payload.get("cli_manifest")
    if (
        not isinstance(binding, dict)
        or set(binding) != {"bytes", "sha256"}
        or binding.get("bytes") != len(manifest_bytes)
        or binding.get("sha256")
        != hashlib.sha256(manifest_bytes).hexdigest().upper()
    ):
        raise InstallerContractError("installer_not_found_manifest_binding_drift")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, dict) or set(raw_entries) != {"CLAUDE", "CODEX"}:
        raise InstallerContractError("invalid_installer_not_found_fixture")

    entries: dict[str, InstallerNotFoundEntry] = {}
    for role in ("CLAUDE", "CODEX"):
        value = raw_entries[role]
        if (
            not isinstance(value, dict)
            or set(value) != {"cli_sha256", "returncode", "stdout", "stderr"}
        ):
            raise InstallerContractError("invalid_installer_not_found_fixture")
        cli_hash = value.get("cli_sha256")
        returncode = value.get("returncode")
        stdout = value.get("stdout")
        stderr = value.get("stderr")
        if (
            cli_hash != manifest.entries[role].sha256
            or not isinstance(returncode, int)
            or isinstance(returncode, bool)
            or returncode == 0
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
            or len(stdout) > 4096
            or len(stderr) > 4096
            or "\0" in stdout
            or "\0" in stderr
        ):
            raise InstallerContractError("invalid_installer_not_found_fixture")
        entries[role] = InstallerNotFoundEntry(
            cli_sha256=cli_hash,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
    return InstallerNotFoundFixtures(entries)


_VALUE_FLAGS = frozenset(
    {
        "-m",
        "--port",
        "--keyfile",
        "--expected-game-version",
        "--idle-timeout",
        "--exec-allowlist",
        "--client-platform",
        "--task-label",
    }
)
_BOOLEAN_FLAGS = frozenset(
    {
        "--require-version",
        "--enable-exec-enforce",
        "--no-daemon-autospawn",
        "--client",
        "--daemon",
        "--embedded",
    }
)


def _parse_claude_arguments(value: str) -> tuple[str, ...]:
    matches = list(re.finditer(r"(?<!\S)(-{1,2}\S+)", value))
    if not matches or value[: matches[0].start()].strip():
        raise InstallerContractError("invalid_claude_registration_args")
    seen: set[str] = set()
    arguments: list[str] = []
    for index, match in enumerate(matches):
        option = match.group(1)
        if (
            "=" in option
            or option not in _VALUE_FLAGS | _BOOLEAN_FLAGS
            or option in seen
        ):
            raise InstallerContractError("invalid_claude_registration_args")
        seen.add(option)
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        trailing = value[value_start:value_end].strip()
        arguments.append(option)
        if option in _BOOLEAN_FLAGS:
            if trailing:
                raise InstallerContractError("invalid_claude_registration_args")
            continue
        if not trailing:
            raise InstallerContractError("invalid_claude_registration_args")
        if len(trailing) >= 2 and trailing[0] == trailing[-1] == '"':
            trailing = trailing[1:-1]
        if not trailing:
            raise InstallerContractError("invalid_claude_registration_args")
        arguments.append(trailing)
    return tuple(arguments)


def parse_claude_registration(text: str) -> RegistrationSpec:
    if not isinstance(text, str) or not text:
        raise InstallerContractError("invalid_claude_registration")
    required = {"Scope", "Type", "Command", "Args", "Environment"}
    relevant = required | {"Timeout"}
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        if name not in relevant:
            continue
        if name in fields:
            raise InstallerContractError("ambiguous_claude_registration")
        fields[name] = value.strip()
    if not required.issubset(fields) or not set(fields).issubset(relevant):
        raise InstallerContractError("invalid_claude_registration")
    if (
        fields["Scope"]
        not in {"User config", "User config (available in all your projects)"}
        or fields["Type"] != "stdio"
        or fields["Environment"]
        or fields.get("Timeout", f"{CLAUDE_TIMEOUT_MS}ms")
        != f"{CLAUDE_TIMEOUT_MS}ms"
    ):
        raise InstallerContractError("unsupported_claude_registration")
    command = Path(fields["Command"])
    if not command.is_absolute():
        raise InstallerContractError("invalid_claude_registration_command")
    return RegistrationSpec(command, _parse_claude_arguments(fields["Args"]))


def parse_codex_registration(text: str) -> RegistrationSpec:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise InstallerContractError("invalid_codex_registration") from error
    root_keys = {
        "name",
        "enabled",
        "disabled_reason",
        "startup_timeout_sec",
        "tool_timeout_sec",
        "enabled_tools",
        "disabled_tools",
        "transport",
    }
    if not isinstance(payload, dict) or set(payload) != root_keys:
        raise InstallerContractError("invalid_codex_registration")
    if (
        payload.get("name") != "dayz-mcp"
        or payload.get("enabled") is not True
        or payload.get("disabled_reason") is not None
        or payload.get("startup_timeout_sec") is not None
        or payload.get("tool_timeout_sec")
        not in (None, CODEX_TIMEOUT_SECONDS, float(CODEX_TIMEOUT_SECONDS))
        or payload.get("enabled_tools") not in (None, [])
        or payload.get("disabled_tools") not in (None, [])
    ):
        raise InstallerContractError("unsupported_codex_registration")
    transport = payload.get("transport")
    if (
        not isinstance(transport, dict)
        or set(transport) != {"type", "command", "args", "cwd", "env", "env_vars"}
        or transport.get("type") != "stdio"
        or transport.get("cwd") is not None
        or transport.get("env") not in (None, {})
        or transport.get("env_vars") not in (None, [])
    ):
        raise InstallerContractError("unsupported_codex_registration")
    command = transport.get("command")
    arguments = transport.get("args")
    if (
        not isinstance(command, str)
        or not Path(command).is_absolute()
        or not isinstance(arguments, list)
        or not arguments
        or any(not isinstance(argument, str) or not argument for argument in arguments)
    ):
        raise InstallerContractError("invalid_codex_registration")
    return RegistrationSpec(Path(command), tuple(arguments))


def invoke_manifest_cli(
    entry: InstallerCliEntry,
    arguments: Sequence[str],
    runner: CommandRunner = subprocess.run,
) -> object:
    if not arguments or any(not isinstance(argument, str) or not argument for argument in arguments):
        raise InstallerContractError("invalid_installer_cli_arguments")
    entry.revalidate()
    return runner(
        [str(entry.path), *arguments],
        shell=False,
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )


class CliRegistrationProvider:
    def __init__(
        self,
        manifest: InstallerCliManifest,
        not_found: InstallerNotFoundFixtures,
        *,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        if set(manifest.entries) != {"CLAUDE", "CODEX"} or set(not_found.entries) != {
            "CLAUDE",
            "CODEX",
        }:
            raise InstallerContractError("invalid_registration_provider_contract")
        self.manifest = manifest
        self.not_found = not_found
        self.runner = runner

    def _invoke(self, role: str, arguments: Sequence[str]) -> object:
        if role not in {"CLAUDE", "CODEX"}:
            raise InstallerContractError("invalid_registration_role")
        return invoke_manifest_cli(self.manifest.entries[role], arguments, self.runner)

    @staticmethod
    def _result_fields(completed: object) -> tuple[int, str, str]:
        returncode = getattr(completed, "returncode", None)
        stdout = getattr(completed, "stdout", None)
        stderr = getattr(completed, "stderr", None)
        if (
            not isinstance(returncode, int)
            or isinstance(returncode, bool)
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
        ):
            raise InstallerExecutionError("invalid_registration_cli_result")
        return returncode, stdout, stderr

    def get(self, role: str) -> RegistrationSpec | None:
        arguments = ["mcp", "get", "dayz-mcp"]
        if role == "CODEX":
            arguments.append("--json")
        completed = self._invoke(role, arguments)
        returncode, stdout, stderr = self._result_fields(completed)
        if returncode != 0:
            expected = self.not_found.entries[role]
            if (
                returncode == expected.returncode
                and stdout == expected.stdout
                and stderr == expected.stderr
            ):
                return None
            raise InstallerExecutionError("registration_probe_failed")
        if stderr:
            raise InstallerExecutionError("registration_probe_ambiguous")
        if role == "CLAUDE":
            return parse_claude_registration(stdout)
        return parse_codex_registration(stdout)

    def remove(self, role: str) -> None:
        arguments = ["mcp", "remove", "dayz-mcp"]
        if role == "CLAUDE":
            arguments.extend(("-s", "user"))
        completed = self._invoke(role, arguments)
        returncode, _stdout, _stderr = self._result_fields(completed)
        if returncode != 0:
            raise InstallerExecutionError("registration_remove_failed")

    def add(self, role: str, spec: RegistrationSpec) -> None:
        if (
            not isinstance(spec, RegistrationSpec)
            or not spec.command.is_absolute()
            or not spec.arguments
            or any(not isinstance(argument, str) or not argument for argument in spec.arguments)
        ):
            raise InstallerContractError("invalid_registration_spec")
        arguments = ["mcp", "add", "dayz-mcp"]
        if role == "CLAUDE":
            arguments.extend(("-s", "user"))
        arguments.extend(("--", str(spec.command), *spec.arguments))
        completed = self._invoke(role, arguments)
        returncode, _stdout, _stderr = self._result_fields(completed)
        if returncode != 0:
            raise InstallerExecutionError("registration_add_failed")


def _absolute_optional(value: str) -> Path | None:
    return Path(value).resolve() if value else None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install and register DayZ MCP natively.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keyfile", default="")
    parser.add_argument("--server-profiles", default="")
    parser.add_argument("--client-profiles", default="")
    parser.add_argument("--mission-path", default="")
    parser.add_argument("--expected-game-version", default="")
    parser.add_argument("--idle-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--allow-legacy", action="store_true")
    parser.add_argument("--register", action="store_true")
    return parser


def parse_args(
    argv: Sequence[str] | None = None,
    *,
    tools_root: Path = TOOLS_ROOT,
) -> InstallerOptions:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not math.isfinite(args.idle_timeout_seconds) or args.idle_timeout_seconds < 0:
        parser.error("--idle-timeout-seconds must be finite and non-negative")

    canonical_tools = Path(tools_root).resolve()
    keyfile = (
        Path(args.keyfile).resolve()
        if args.keyfile
        else (canonical_tools / ".dayz_mcp.key").resolve()
    )
    return InstallerOptions(
        port=args.port,
        keyfile=keyfile,
        server_profiles=_absolute_optional(args.server_profiles),
        client_profiles=_absolute_optional(args.client_profiles),
        mission_path=_absolute_optional(args.mission_path),
        expected_game_version=args.expected_game_version,
        idle_timeout_seconds=args.idle_timeout_seconds,
        allow_legacy=args.allow_legacy,
        register=args.register,
        tools_root=canonical_tools,
    )


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else format(value, ".15g")


def build_client_args(options: InstallerOptions, platform: str) -> list[str]:
    if platform not in {"claude", "codex"}:
        raise InstallerContractError("invalid_client_platform")
    arguments = [
        "-m",
        "dayz_mcp",
        "--client",
        "--keyfile",
        str(options.keyfile),
        "--port",
        str(options.port),
    ]
    if options.expected_game_version:
        arguments.extend(("--expected-game-version", options.expected_game_version))
    if not options.allow_legacy:
        arguments.append("--require-version")
    arguments.extend(
        (
            "--idle-timeout",
            _format_number(options.idle_timeout_seconds),
            "--client-platform",
            platform,
        )
    )
    return arguments


def _validate_python_executable(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.name.casefold() != "python.exe":
        raise InstallerContractError("python_path_not_exact")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise InstallerContractError("python_executable_missing") from error
    if os.path.normcase(str(candidate)) != os.path.normcase(str(resolved)):
        raise InstallerContractError("python_path_not_canonical")
    _native_regular_file(candidate)
    _validate_x64_pe(candidate)
    return candidate


def _verified_psutil_wheel(tools_root: Path) -> Path:
    vendor_root = tools_root / "vendor" / "psutil"
    manifest_path = vendor_root / "SHA256SUMS.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallerContractError("invalid_psutil_vendor_manifest") from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "files"}
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("files"), list)
    ):
        raise InstallerContractError("invalid_psutil_vendor_manifest")

    filename = "psutil-7.2.2-cp37-abi3-win_amd64.whl"
    entries = [
        entry
        for entry in manifest["files"]
        if isinstance(entry, dict) and entry.get("filename") == filename
    ]
    if len(entries) != 1 or set(entries[0]) != {"filename", "bytes", "sha256"}:
        raise InstallerContractError("invalid_psutil_vendor_manifest")
    entry = entries[0]
    expected_bytes = entry.get("bytes")
    expected_hash = entry.get("sha256")
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes <= 0
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
    ):
        raise InstallerContractError("invalid_psutil_vendor_manifest")
    if (
        expected_bytes != _PSUTIL_WHEEL_BYTES
        or expected_hash != _PSUTIL_WHEEL_SHA256
    ):
        raise InstallerContractError("psutil_vendor_contract_drift")

    wheel = vendor_root / filename
    try:
        payload = wheel.read_bytes()
    except OSError as error:
        raise InstallerContractError("psutil_vendor_wheel_missing") from error
    if len(payload) != expected_bytes:
        raise InstallerContractError("psutil_vendor_wheel_byte_drift")
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise InstallerContractError("psutil_vendor_wheel_hash_drift")
    return wheel


def _run_required(
    executable: Path,
    arguments: Sequence[str],
    runner: CommandRunner,
) -> None:
    canonical = _validate_python_executable(executable)
    completed = runner(
        [str(canonical), *arguments],
        shell=False,
        text=True,
        capture_output=True,
        timeout=300.0,
        check=False,
    )
    if getattr(completed, "returncode", None) != 0:
        raise InstallerExecutionError("required_command_failed")


def _load_or_create_key(
    path: Path,
    token_factory: Callable[[], str],
) -> str:
    if path.exists():
        try:
            value = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise InstallerExecutionError("keyfile_unreadable") from error
    else:
        value = token_factory()
        if not isinstance(value, str) or not value:
            raise InstallerExecutionError("key_generation_failed")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="ascii", newline="\n") as handle:
                handle.write(value + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, UnicodeError) as error:
            raise InstallerExecutionError("keyfile_write_failed") from error
    if not value:
        raise InstallerExecutionError("empty_keyfile")
    return value


def _write_config(path: Path, payload: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="ascii", newline="\n")
    except (OSError, UnicodeError) as error:
        raise InstallerExecutionError("config_write_failed") from error


def install_runtime(
    options: InstallerOptions,
    *,
    base_python: Path,
    runner: CommandRunner = subprocess.run,
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
) -> dict[str, object]:
    base = _validate_python_executable(Path(base_python))
    if base != Path(base_python):
        raise InstallerContractError("python_path_not_exact")
    tools_root = options.tools_root
    requirements = tools_root / "requirements-mcp.txt"
    pyproject = tools_root / "pyproject.toml"
    if not requirements.is_file() or not pyproject.is_file():
        raise InstallerContractError("installer_source_incomplete")
    wheel = _verified_psutil_wheel(tools_root)

    venv_root = tools_root / ".venv-mcp"
    venv_python = venv_root / "Scripts" / "python.exe"
    if not venv_root.exists():
        _run_required(base, ("-m", "venv", str(venv_root)), runner)
    _validate_python_executable(venv_python)

    _run_required(
        venv_python,
        ("-m", "pip", "install", "--no-index", "--no-deps", str(wheel)),
        runner,
    )
    _run_required(
        venv_python,
        ("-m", "pip", "install", "-r", str(requirements)),
        runner,
    )
    _run_required(
        venv_python,
        ("-m", "pip", "install", "-e", str(tools_root)),
        runner,
    )

    key = _load_or_create_key(options.keyfile, token_factory)
    config_payload = json.dumps(
        {
            "url": f"http://127.0.0.1:{options.port}/",
            "key": key,
            "pollHz": 5,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    config_paths = [
        tools_root / "_mcp_config" / "server_profiles" / "dayz_mcp.json",
        tools_root / "_mcp_config" / "client_profiles" / "dayz_mcp.json",
        tools_root
        / "_mcp_config"
        / "mpmissions"
        / "dayzOffline.chernarusplus"
        / "dayz_mcp.json",
    ]
    for optional_root in (
        options.server_profiles,
        options.client_profiles,
        options.mission_path,
    ):
        if optional_root is not None:
            config_paths.append(optional_root / "dayz_mcp.json")
    for config_path in config_paths:
        _write_config(config_path, config_payload)

    return {
        "status": "installed",
        "venv_python": str(venv_python),
        "keyfile": str(options.keyfile),
        "configs": [str(path) for path in config_paths],
    }


def _rollback_registrations(
    provider: RegistrationProvider,
    previous: dict[str, RegistrationSpec | None],
    touched: set[str],
) -> None:
    roles = ("CLAUDE", "CODEX")
    for role in roles:
        if role not in touched:
            continue
        current = provider.get(role)
        if current is not None:
            provider.remove(role)
        original = previous[role]
        if original is not None:
            provider.add(role, original)
    for role in roles:
        if provider.get(role) != previous[role]:
            raise RegistrationRollbackError("registration_rollback_verify_failed")


def register_transaction(
    provider: RegistrationProvider,
    desired: dict[str, RegistrationSpec],
    *,
    host_configs: tuple[Path, Path] | None = None,
) -> None:
    roles = ("CLAUDE", "CODEX")
    if set(desired) != set(roles) or any(
        not isinstance(desired[role], RegistrationSpec) for role in roles
    ):
        raise InstallerContractError("invalid_registration_contract")
    if host_configs is not None and (
        not isinstance(host_configs, tuple)
        or len(host_configs) != 2
        or any(not isinstance(path, Path) for path in host_configs)
    ):
        raise InstallerContractError("invalid_host_config_contract")

    previous: dict[str, RegistrationSpec | None] = {}
    touched: set[str] = set()
    try:
        for role in roles:
            previous[role] = provider.get(role)
        for role in roles:
            if previous[role] is not None:
                provider.remove(role)
                touched.add(role)
        for role in roles:
            provider.add(role, desired[role])
            touched.add(role)
        for role in roles:
            if provider.get(role) != desired[role]:
                raise RegistrationTransactionError("registration_verify_mismatch")
        if host_configs is not None:
            apply_host_timeouts(*host_configs)
            for role in roles:
                if provider.get(role) != desired[role]:
                    raise RegistrationTransactionError("registration_verify_mismatch")
    except Exception as error:
        if len(previous) != len(roles):
            raise RegistrationTransactionError("registration_probe_failed") from error
        try:
            _rollback_registrations(provider, previous, touched)
        except Exception as rollback_error:
            raise RegistrationRollbackError("registration_rollback_failed") from rollback_error
        raise RegistrationTransactionError("registration_transaction_failed") from error


def run_runs_backup_gate(
    venv_python: Path,
    tools_root: Path,
    port: int,
    *,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    python = _validate_python_executable(Path(venv_python))
    script = Path(tools_root) / "p0s_gate.py"
    try:
        metadata = script.lstat()
    except OSError as error:
        raise InstallerContractError("runs_backup_gate_missing") from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
    ):
        raise InstallerContractError("runs_backup_gate_invalid")
    completed = runner(
        [
            str(python),
            "-I",
            "-B",
            str(script),
            "backup-runs-v1",
            "--port",
            str(port),
        ],
        shell=False,
        text=True,
        capture_output=True,
        timeout=300.0,
        check=False,
    )
    returncode = getattr(completed, "returncode", None)
    stdout = getattr(completed, "stdout", None)
    stderr = getattr(completed, "stderr", None)
    if (
        returncode != 0
        or not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or stderr
        or len(stdout) > 1024
        or "\0" in stdout
    ):
        raise InstallerExecutionError("runs_backup_gate_failed")
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise InstallerExecutionError("runs_backup_gate_invalid_output") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"status", "source_absent"}
        or payload.get("status") != "verified"
        or not isinstance(payload.get("source_absent"), bool)
    ):
        raise InstallerExecutionError("runs_backup_gate_invalid_output")
    return payload


def run_installer(
    options: InstallerOptions,
    *,
    base_python: Path,
    runner: CommandRunner = subprocess.run,
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
) -> dict[str, object]:
    runtime = install_runtime(
        options,
        base_python=base_python,
        runner=runner,
        token_factory=token_factory,
    )
    venv_value = runtime.get("venv_python")
    if not isinstance(venv_value, str) or not venv_value:
        raise InstallerExecutionError("runtime_install_result_invalid")
    venv_python = Path(venv_value)
    if not options.register:
        return {
            "status": "installed",
            "registered": False,
            "venv_python": str(venv_python),
        }

    reports = options.tools_root.parent / "reports" / "security"
    manifest_path = reports / "installer-cli-manifest-v1.json"
    fixture_path = reports / "installer-not-found-fixtures-v1.json"
    manifest = load_installer_cli_manifest(manifest_path)
    not_found = load_installer_not_found_fixtures(fixture_path, manifest_path)
    provider = CliRegistrationProvider(manifest, not_found, runner=runner)
    desired = {
        "CLAUDE": RegistrationSpec(
            venv_python, tuple(build_client_args(options, "claude"))
        ),
        "CODEX": RegistrationSpec(
            venv_python, tuple(build_client_args(options, "codex"))
        ),
    }
    run_runs_backup_gate(
        venv_python,
        options.tools_root,
        options.port,
        runner=runner,
    )
    register_transaction(
        provider,
        desired,
        host_configs=(
            Path.home() / ".claude.json",
            Path.home() / ".codex" / "config.toml",
        ),
    )
    return {
        "status": "installed_and_registered",
        "registered": True,
        "venv_python": str(venv_python),
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run_installer(
            parse_args(argv),
            base_python=Path(sys.executable),
        )
    except (InstallerContractError, InstallerExecutionError, OSError, ValueError) as error:
        code = str(error)
        if not re.fullmatch(r"[A-Za-z0-9_:-]+", code):
            code = "installer_failed"
        print(
            json.dumps({"status": "error", "error": code}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
