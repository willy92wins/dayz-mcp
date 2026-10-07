from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

# Floor matches pyproject.toml requires-python; host_config imports tomllib.
MIN_PYTHON = (3, 11)
if sys.version_info < MIN_PYTHON:
    sys.stderr.write(
        f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required; "
        f"this interpreter is {sys.version_info[0]}.{sys.version_info[1]}\n"
    )
    raise SystemExit(2)

from dayz_mcp.host_config import (
    CLAUDE_TIMEOUT_MS,
    CODEX_TIMEOUT_SECONDS,
    apply_host_timeouts,
)
from dayz_mcp.knowledge_pack import KnowledgePackError, install_knowledge_pack


TOOLS_ROOT = Path(__file__).resolve().parent
_EXPECTED_ROLES = {"CLAUDE": "claude.exe", "CODEX": "codex.exe"}
_HEX_UPPER = frozenset("0123456789ABCDEF")
_PE_X64_MACHINE = 0x8664
_PE32_PLUS_MAGIC = 0x20B
_PSUTIL_WHEEL_BYTES = 137_737
_PSUTIL_WHEEL_SHA256 = "eb7e81434c8d223ec4a219b5fc1c47d0417b12be7ea866e24fb5ad6e84b3d988"
_ABSENT_PROBE_NAME = "p0s-absent-fixture-do-not-create"
_SECURITY_DIR_ENV = "DAYZ_MCP_SECURITY_DIR"
_PIN_RECIPE = "run: python install_mcp.py --pin-clis"
_CLI_MANIFEST_NAME = "installer-cli-manifest-v1.json"
_NOT_FOUND_FIXTURE_NAME = "installer-not-found-fixtures-v1.json"


class InstallerContractError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class InstallerExecutionError(RuntimeError):
    pass


class RegistrationTransactionError(InstallerExecutionError):
    pass


class RegistrationRollbackError(InstallerExecutionError):
    pass


class RegistrationCrash(BaseException):
    """Test-only abrupt stop. It skips the in-process rollback on purpose."""


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
    pin_clis: bool
    claude_exe: Path | None
    codex_exe: Path | None
    tools_root: Path
    skip_knowledge_pack: bool = False
    claude_no_progressive_disclosure: bool = False
    # Register under the MCP supervisor (server_reload, lease handoff across a
    # worker recycle). The box runs this way; --no-supervised opts out.
    supervised: bool = True
    # --register refuses to drop a flag the current registration has unless
    # this is set (fb-20260927-210146-0f68).
    allow_option_removal: bool = False
    instance_token: str | None = None
    game_path: str | None = None


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


def installer_security_dir() -> Path:
    override = os.environ.get(_SECURITY_DIR_ENV)
    if override is not None:
        text = override.strip()
        if not text:
            raise InstallerContractError("installer_cli_manifest_missing", _PIN_RECIPE)
        return Path(text)
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_appdata:
        raise InstallerContractError("installer_cli_manifest_missing", _PIN_RECIPE)
    return Path(local_appdata) / "DayZ_MCP" / "security"


def installer_cli_manifest_path() -> Path:
    return installer_security_dir() / _CLI_MANIFEST_NAME


def installer_not_found_fixtures_path() -> Path:
    return installer_security_dir() / _NOT_FOUND_FIXTURE_NAME


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
    if os.name == "nt":
        from dayz_mcp.pinned_keyfile import same_requested_path

        # collapse=False: main does not normpath, and GetLongPathNameW keeps
        # ".." and junctions. Collapsing either would accept what resolve()
        # rewrites. The raw compare still runs first.
        paths_match = same_requested_path(
            str(path), str(resolved), collapse=False
        )
    else:
        paths_match = os.path.normcase(str(path)) == os.path.normcase(str(resolved))
    if not paths_match:
        raise InstallerContractError("installer_cli_path_not_canonical")

    metadata = _native_regular_file(path)
    if metadata.st_size != expected_bytes:
        raise InstallerContractError("installer_cli_byte_drift", _PIN_RECIPE)
    _validate_x64_pe(path)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    except OSError as error:
        raise InstallerContractError("installer_cli_unreadable") from error
    if digest != expected_sha256:
        raise InstallerContractError("installer_cli_hash_drift", _PIN_RECIPE)

    return InstallerCliEntry(role, path, expected_bytes, expected_sha256)


def load_installer_cli_manifest(path: Path) -> InstallerCliManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InstallerContractError(
            "installer_cli_manifest_missing", _PIN_RECIPE
        ) from error
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
    except FileNotFoundError as error:
        raise InstallerContractError(
            "installer_cli_manifest_missing", _PIN_RECIPE
        ) from error
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
        raise InstallerContractError(
            "installer_not_found_manifest_binding_drift", _PIN_RECIPE
        )
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


def _atomic_write_json(path: Path, payload: object) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _retire_installer_not_found_fixture(path: Path) -> None:
    fixture = Path(path)
    if not fixture.is_file():
        return
    stale = fixture.with_name(fixture.name + ".stale")
    os.replace(fixture, stale)


def _resolve_pin_cli(role: str, explicit: Path | None) -> Path:
    expected_name = _EXPECTED_ROLES[role]
    flag = f"--{role.lower()}-exe"
    if explicit is not None:
        candidate = Path(explicit)
    else:
        found = shutil.which(role.lower())
        if not found:
            raise InstallerContractError(
                "installer_cli_missing",
                f"pass {flag} PATH; {_PIN_RECIPE}",
            )
        candidate = Path(found)
    if candidate.suffix.casefold() != ".exe":
        raise InstallerContractError(
            "installer_cli_not_native_exe",
            f"pass {flag} PATH to a native {expected_name}; {_PIN_RECIPE}",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise InstallerContractError(
            "installer_cli_missing",
            f"pass {flag} PATH; {_PIN_RECIPE}",
        ) from error
    _native_regular_file(resolved)
    _validate_x64_pe(resolved)
    if resolved.name.casefold() != expected_name:
        raise InstallerContractError("installer_cli_role_path_mismatch")
    return resolved


def pin_installer_clis(
    *,
    claude_exe: Path | None = None,
    codex_exe: Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> list[InstallerCliEntry]:
    resolved = {
        "CLAUDE": _resolve_pin_cli("CLAUDE", claude_exe),
        "CODEX": _resolve_pin_cli("CODEX", codex_exe),
    }
    entries = {
        role: build_installer_cli_entry_payload(role, path)
        for role, path in resolved.items()
    }
    manifest_payload = {
        "schema_version": 1,
        "kind": "dayz-mcp-installer-clis-v1",
        "entries": entries,
    }
    manifest_path = installer_cli_manifest_path()
    fixture_path = installer_not_found_fixtures_path()
    _retire_installer_not_found_fixture(fixture_path)
    _atomic_write_json(manifest_path, manifest_payload)
    manifest = load_installer_cli_manifest(manifest_path)
    fixture_entries: dict[str, dict[str, object]] = {}
    for role in ("CLAUDE", "CODEX"):
        arguments = ["mcp", "get", _ABSENT_PROBE_NAME]
        if role == "CODEX":
            arguments.append("--json")
        try:
            completed = invoke_manifest_cli(
                manifest.entries[role], arguments, runner
            )
        except subprocess.TimeoutExpired as error:
            raise InstallerContractError(
                "installer_cli_probe_timeout",
                f"{role}; {_PIN_RECIPE}",
            ) from error
        except OSError as error:
            raise InstallerContractError(
                f"installer_cli_launch_failed:{role}:{getattr(error, 'winerror', None)}"
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
            raise InstallerContractError(
                "invalid_installer_not_found_probe", _PIN_RECIPE
            )
        fixture_entries[role] = {
            "cli_sha256": manifest.entries[role].sha256,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    manifest_bytes = manifest_path.read_bytes()
    fixture_payload = {
        "schema_version": 1,
        "kind": "dayz-mcp-installer-not-found-fixtures-v1",
        "probe_name": _ABSENT_PROBE_NAME,
        "cli_manifest": {
            "bytes": len(manifest_bytes),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest().upper(),
        },
        "entries": fixture_entries,
    }
    _atomic_write_json(fixture_path, fixture_payload)
    load_installer_not_found_fixtures(fixture_path, manifest_path)
    return [manifest.entries[role] for role in ("CLAUDE", "CODEX")]


_VALUE_FLAGS = frozenset(
    {
        "-m",
        "--port",
        "--keyfile",
        "--expected-game-version",
        "--idle-timeout",
        "--exec-allowlist",
        "--exec-audit-path",
        "--client-platform",
        "--task-label",
        "--tool-pack",
        "--instance",
        "--game-path",
    }
)
_BOOLEAN_FLAGS = frozenset(
    {
        "--require-version",
        "--enable-exec-enforce",
        "--no-daemon-autospawn",
        "--no-progressive-disclosure",
        "--client",
        "--supervised",
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


def parse_codex_registration(
    text: str, *, server_name: str = "dayz-mcp"
) -> RegistrationSpec:
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
        payload.get("name") != server_name
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
        server_name: str = "dayz-mcp",
    ) -> None:
        if set(manifest.entries) != {"CLAUDE", "CODEX"} or set(not_found.entries) != {
            "CLAUDE",
            "CODEX",
        }:
            raise InstallerContractError("invalid_registration_provider_contract")
        if server_name != "dayz-mcp" and (
            not server_name.startswith("dayz-mcp-") or server_name == "dayz-mcp"
        ):
            raise InstallerContractError("invalid_registration_name")
        self.manifest = manifest
        self.not_found = not_found
        self.runner = runner
        self.server_name = server_name

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
        arguments = ["mcp", "get", self.server_name]
        if role == "CODEX":
            arguments.append("--json")
        completed = self._invoke(role, arguments)
        returncode, stdout, stderr = self._result_fields(completed)
        if returncode != 0:
            expected = self.not_found.entries[role]
            expected_stdout = expected.stdout
            expected_stderr = expected.stderr
            if self.server_name != "dayz-mcp":
                expected_stdout = expected_stdout.replace("dayz-mcp", self.server_name)
                expected_stderr = expected_stderr.replace("dayz-mcp", self.server_name)
            if (
                returncode == expected.returncode
                and stdout == expected_stdout
                and stderr == expected_stderr
            ):
                return None
            raise InstallerExecutionError("registration_probe_failed")
        if stderr:
            raise InstallerExecutionError("registration_probe_ambiguous")
        if role == "CLAUDE":
            return parse_claude_registration(stdout)
        return parse_codex_registration(stdout, server_name=self.server_name)

    def remove(self, role: str) -> None:
        arguments = ["mcp", "remove", self.server_name]
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
        arguments = ["mcp", "add", self.server_name]
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
    parser = argparse.ArgumentParser(
        description="Install and register DayZ MCP natively.",
        allow_abbrev=False,
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keyfile", default="")
    parser.add_argument("--server-profiles", default="")
    parser.add_argument("--client-profiles", default="")
    parser.add_argument("--mission-path", default="")
    parser.add_argument("--expected-game-version", default="")
    parser.add_argument("--idle-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--allow-legacy", action="store_true")
    parser.add_argument("--skip-knowledge-pack", action="store_true")
    parser.add_argument(
        "--claude-no-progressive-disclosure",
        action="store_true",
        help="Also register Claude Code with --no-progressive-disclosure. Claude already lists every tool from the start; the switch is still accepted.",
    )
    parser.add_argument(
        "--no-supervised",
        dest="supervised",
        action="store_false",
        help="Register without the MCP supervisor: no server_reload, and no lease handoff across a worker recycle.",
    )
    parser.add_argument(
        "--allow-option-removal",
        action="store_true",
        help="Let --register drop flags the current registrations have (for example --supervised).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--pin-clis", action="store_true")
    parser.add_argument("--claude-exe", default="")
    parser.add_argument("--codex-exe", default="")
    parser.add_argument("--instance", default="")
    parser.add_argument("--game-path", default="")
    return parser


def parse_args(
    argv: Sequence[str] | None = None,
    *,
    tools_root: Path = TOOLS_ROOT,
) -> InstallerOptions:
    parser = _build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    from dayz_mcp.server_cli import (
        InstanceSelectionError,
        reject_conflicting_environment,
        selector_from_parsed,
        validate_entry_selector,
    )

    try:
        instance_token, game_path = validate_entry_selector(raw)
    except InstanceSelectionError as error:
        parser.error(error.code)
    args = parser.parse_args(raw)
    try:
        consumed_token, consumed_game = selector_from_parsed(args.instance, args.game_path)
    except InstanceSelectionError as error:
        parser.error(error.code)
    if (consumed_token, consumed_game) != (instance_token, game_path):
        parser.error("duplicate_instance_flag")
    instance_token, game_path = consumed_token, consumed_game
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not math.isfinite(args.idle_timeout_seconds) or args.idle_timeout_seconds < 0:
        parser.error("--idle-timeout-seconds must be finite and non-negative")
    if (args.claude_exe or args.codex_exe) and not (args.pin_clis or args.register):
        parser.error("--claude-exe and --codex-exe require --pin-clis or --register")
    if args.register and bool(args.claude_exe) != bool(args.codex_exe):
        parser.error("registration_cli_pair_required")
    try:
        reject_conflicting_environment(instance_token, args.port, game_path)
    except InstanceSelectionError as error:
        parser.error(error.code)

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
        pin_clis=args.pin_clis,
        claude_exe=Path(args.claude_exe) if args.claude_exe else None,
        codex_exe=Path(args.codex_exe) if args.codex_exe else None,
        tools_root=canonical_tools,
        skip_knowledge_pack=args.skip_knowledge_pack,
        claude_no_progressive_disclosure=args.claude_no_progressive_disclosure,
        supervised=args.supervised,
        allow_option_removal=args.allow_option_removal,
        instance_token=instance_token,
        game_path=game_path,
    )


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else format(value, ".15g")


def build_client_args(options: InstallerOptions, platform: str) -> list[str]:
    if platform not in {"claude", "codex"}:
        raise InstallerContractError("invalid_client_platform")
    arguments = ["-m", "dayz_mcp", "--client"]
    if options.supervised:
        arguments.append("--supervised")
    arguments.extend(("--keyfile", str(options.keyfile), "--port", str(options.port)))
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
    if platform == "claude" and options.claude_no_progressive_disclosure:
        arguments.append("--no-progressive-disclosure")
    if options.instance_token:
        arguments.extend(("--instance", options.instance_token))
    if options.game_path:
        arguments.extend(("--game-path", options.game_path))
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


def _option_names(spec: RegistrationSpec) -> set[str]:
    # Every option token, known or not: a Codex registration is not checked
    # against the flag grammar, and an unknown flag there was added on purpose too.
    names: set[str] = set()
    skip_value = False
    for argument in spec.arguments:
        if skip_value:
            skip_value = False
        elif argument.startswith("-"):
            names.add(argument.split("=", 1)[0])
            skip_value = argument in _VALUE_FLAGS
    return names


_REGISTRATION_JOURNAL_SCHEMA = 1


def _default_registration_journal_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise InstallerContractError("registration_journal_unavailable")
    return Path(local) / "DayZ_MCP" / "registration-transaction"


def _validate_registration_server_name(server_name: str) -> str:
    # Same predicate as CliRegistrationProvider. The name is also a single
    # path segment: a slash or ".." would leave the journal root.
    if not isinstance(server_name, str) or server_name != "dayz-mcp" and (
        not server_name.startswith("dayz-mcp-") or server_name == "dayz-mcp"
    ):
        raise InstallerContractError("invalid_registration_name")
    if (
        server_name != Path(server_name).name
        or any(part in {"", ".", ".."} for part in server_name.split("-"))
    ):
        raise InstallerContractError("invalid_registration_name")
    return server_name


class _RegistrationLockToken:
    """Explicit owner of one held registration lock. Never an id()."""


class _HeldRegistrationLock:
    def __init__(self, descriptor: int, owner: _RegistrationLockToken) -> None:
        self._descriptor = descriptor
        self.owner = owner

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        import msvcrt

        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(descriptor)


def _registration_lock_path(journal_root: Path) -> Path:
    # Sibling of the journal directory, under that directory's parent:
    # <LOCALAPPDATA>\DayZ_MCP\registration-transaction.lock
    return journal_root.parent / f"{journal_root.name}.lock"


def _open_registration_lock(path: Path) -> int:
    """Open the lock without FILE_SHARE_DELETE.

    Sharing delete lets another process remove the held file and lock a new
    file of the same name. Read and write are shared so a waiter can open the
    same file and fail LK_NBLCK instead of failing the open.
    """
    import ctypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    handle = kernel.CreateFileW(
        str(path),
        0x80000000 | 0x40000000,
        0x1 | 0x2,
        None,
        4,
        0x80,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if not handle or handle == invalid:
        raise OSError(ctypes.get_last_error(), "registration_lock_open_failed")
    return msvcrt.open_osfhandle(handle, os.O_BINARY if hasattr(os, "O_BINARY") else 0)


def _acquire_registration_lock(journal_root: Path, timeout_s: float) -> _HeldRegistrationLock:
    # NaN and the infinities compare in a way that never reaches the deadline.
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s < 0
    ):
        raise InstallerContractError("invalid_registration_lock_timeout")
    path = _registration_lock_path(journal_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    import msvcrt

    owner = _RegistrationLockToken()
    deadline = time.monotonic() + float(timeout_s)
    while True:
        descriptor = _open_registration_lock(path)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(descriptor)
            if time.monotonic() >= deadline:
                raise RegistrationTransactionError("registration_busy")
            remaining = deadline - time.monotonic()
            time.sleep(min(0.02, max(0.0, remaining)))
            continue
        return _HeldRegistrationLock(descriptor, owner)


def _registration_spec_payload(spec: RegistrationSpec | None) -> object:
    if spec is None:
        return None
    return {"arguments": list(spec.arguments), "command": str(spec.command)}


def _registration_spec_from_payload(value: object) -> RegistrationSpec | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"arguments", "command"}
        or not isinstance(value.get("command"), str)
        or not value["command"]
        or not isinstance(value.get("arguments"), list)
        or any(not isinstance(item, str) for item in value["arguments"])
    ):
        raise RegistrationTransactionError("registration_journal_invalid")
    return RegistrationSpec(Path(value["command"]), tuple(value["arguments"]))


def _registration_publication_paths(journal: Path) -> tuple[Path, Path, Path]:
    return (
        journal / "manifest.json",
        journal / "manifest.next",
        journal / "manifest.staging",
    )


def _write_registration_manifest(journal: Path, manifest: dict[str, object]) -> None:
    from dayz_mcp.host_config import _mkdir_restricted, _write_private

    # The active names appear only after the bytes are complete. A kill during
    # the first write leaves manifest.staging, which is not a journal.
    _mkdir_restricted(journal)
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    published, pending, staging = _registration_publication_paths(journal)
    _write_private(staging, payload)
    os.replace(staging, pending)
    os.replace(pending, published)


def _cleanup_registration_journal(journal: Path) -> None:
    if journal.exists():
        shutil.rmtree(journal)


def _discard_incomplete_registration_publication(journal: Path) -> None:
    """Drop a first-write fragment. It never became a manifest and mutated nothing."""
    if not journal.exists():
        return
    published, pending, staging = _registration_publication_paths(journal)
    if published.exists():
        return
    for path in (pending, staging):
        if not path.is_file():
            continue
        try:
            parsed = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError):
            parsed = None
        if not isinstance(parsed, dict):
            path.unlink()


def _load_registration_manifest(
    journal: Path,
) -> tuple[
    dict[str, RegistrationSpec | None],
    bool,
    str,
    tuple[Path, Path] | None,
    Path | None,
] | None:
    published, pending, _staging = _registration_publication_paths(journal)
    only_next = False
    if published.is_file():
        source = published
    elif pending.is_file():
        source = pending
        only_next = True
    else:
        return None
    try:
        value = json.loads(source.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise RegistrationTransactionError("registration_journal_invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema") != _REGISTRATION_JOURNAL_SCHEMA
        or value.get("status") != "prepared"
        or value.get("server_name") in (None, "")
        or not isinstance(value.get("server_name"), str)
        or set(value) != {
            "host_configs",
            "host_journal",
            "previous",
            "schema",
            "server_name",
            "status",
        }
        or not isinstance(value.get("previous"), dict)
        or set(value["previous"]) != {"CLAUDE", "CODEX"}
    ):
        raise RegistrationTransactionError("registration_journal_invalid")
    raw_hosts = value.get("host_configs")
    raw_host_journal = value.get("host_journal")
    if raw_hosts is None:
        host_configs = None
    elif (
        isinstance(raw_hosts, list)
        and len(raw_hosts) == 2
        and all(isinstance(item, str) and item for item in raw_hosts)
    ):
        host_configs = (Path(raw_hosts[0]), Path(raw_hosts[1]))
    else:
        raise RegistrationTransactionError("registration_journal_invalid")
    if raw_host_journal is None:
        host_journal = None
    elif isinstance(raw_host_journal, str) and raw_host_journal:
        host_journal = Path(raw_host_journal)
    else:
        raise RegistrationTransactionError("registration_journal_invalid")
    previous = {
        role: _registration_spec_from_payload(value["previous"][role])
        for role in ("CLAUDE", "CODEX")
    }
    return previous, only_next, value["server_name"], host_configs, host_journal


def _restore_registered_roles(
    provider: RegistrationProvider,
    previous: dict[str, RegistrationSpec | None],
) -> None:
    for role in ("CLAUDE", "CODEX"):
        current = provider.get(role)
        target = previous[role]
        if current == target:
            continue
        if current is not None:
            provider.remove(role)
        if target is not None:
            provider.add(role, target)
        if provider.get(role) != target:
            raise RegistrationRollbackError("registration_rollback_verify_failed")


def _recover_pending_host_configs(
    host_configs: tuple[Path, Path] | None,
    host_journal_root: Path | None,
) -> None:
    if host_configs is None:
        return
    from dayz_mcp.host_config import _default_journal_root, _recover_if_needed

    host_journal = (
        Path(host_journal_root) if host_journal_root is not None else _default_journal_root()
    )
    _recover_if_needed(host_configs[0], host_configs[1], host_journal)


def _recover_registration_journal(
    provider: RegistrationProvider,
    journal: Path,
    loaded: tuple[
        dict[str, RegistrationSpec | None],
        bool,
        str,
        tuple[Path, Path] | None,
        Path | None,
    ],
) -> None:
    previous, only_next, _owner, _hosts, _host_journal = loaded
    if only_next and all(provider.get(role) == previous[role] for role in previous):
        _cleanup_registration_journal(journal)
        return
    _restore_registered_roles(provider, previous)
    _cleanup_registration_journal(journal)


def register_transaction(
    provider: RegistrationProvider,
    desired: dict[str, RegistrationSpec],
    *,
    host_configs: tuple[Path, Path] | None = None,
    allow_option_removal: bool = False,
    server_name: str = "dayz-mcp",
    journal_root: Path | None = None,
    host_journal_root: Path | None = None,
    fault_injector: Callable[[str], None] | None = None,
    lock_timeout_s: float = 120.0,
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
    selected_name = _validate_registration_server_name(server_name)
    # A relative root would lock and journal against the process cwd. Refuse
    # before any lock, journal or provider call.
    if journal_root is not None and not Path(journal_root).is_absolute():
        raise InstallerContractError("relative_journal_root")
    if host_journal_root is not None and not Path(host_journal_root).is_absolute():
        raise InstallerContractError("relative_host_journal_root")
    root = Path(journal_root) if journal_root is not None else _default_registration_journal_root()
    # One installer at a time, before discovery. The lock covers recovery,
    # publication, provider mutations, host timeouts and journal cleanup.
    held = _acquire_registration_lock(root, lock_timeout_s)
    try:
        _register_transaction_locked(
            provider,
            desired,
            host_configs=host_configs,
            allow_option_removal=allow_option_removal,
            server_name=selected_name,
            journal_root=root,
            host_journal_root=host_journal_root,
            fault_injector=fault_injector,
        )
    finally:
        held.release()


def _register_transaction_locked(
    provider: RegistrationProvider,
    desired: dict[str, RegistrationSpec],
    *,
    host_configs: tuple[Path, Path] | None,
    allow_option_removal: bool,
    server_name: str,
    journal_root: Path,
    host_journal_root: Path | None,
    fault_injector: Callable[[str], None] | None,
) -> None:
    roles = ("CLAUDE", "CODEX")
    journal = journal_root / server_name
    _discard_incomplete_registration_publication(journal)
    loaded = _load_registration_manifest(journal)
    recorded_hosts: tuple[Path, Path] | None = None
    recorded_host_journal: Path | None = None
    if loaded is not None:
        _previous, _only_next, owner, recorded_hosts, recorded_host_journal = loaded
        if owner != server_name:
            # Another instance owns this journal. Do not restore its specs here
            # and do not delete it.
            raise RegistrationTransactionError("registration_journal_owner_mismatch")
    # A torn timeout write is repaired before any provider read. The provider
    # parses those same host files.
    _recover_pending_host_configs(
        host_configs if host_configs is not None else recorded_hosts,
        host_journal_root if host_journal_root is not None else recorded_host_journal,
    )
    if loaded is not None:
        _recover_registration_journal(provider, journal, loaded)
    previous: dict[str, RegistrationSpec | None] = {}
    touched: set[str] = set()
    try:
        for role in roles:
            previous[role] = provider.get(role)
    except Exception as error:
        raise RegistrationTransactionError("registration_probe_failed") from error
    # Re-registering must not silently strip what someone added on purpose:
    # the live registrations carried --supervised that no installer emitted
    # (fb-20260927-210146-0f68). Nothing has been touched yet.
    dropped = {
        role: sorted(_option_names(previous[role]) - _option_names(desired[role]))
        for role in roles
        if previous[role] is not None
    }
    dropped = {role: flags for role, flags in dropped.items() if flags}
    if dropped and not allow_option_removal:
        detail = ";".join(f"{role}:{','.join(flags)}" for role, flags in sorted(dropped.items()))
        raise InstallerContractError(
            "registration_would_drop_options",
            f"{detail} (re-run with --allow-option-removal to drop them)",
        )
    try:
        _write_registration_manifest(
            journal,
            {
                "host_configs": (
                    None
                    if host_configs is None
                    else [str(host_configs[0]), str(host_configs[1])]
                ),
                "host_journal": (
                    None if host_journal_root is None else str(host_journal_root)
                ),
                "previous": {
                    role: _registration_spec_payload(previous[role]) for role in roles
                },
                "schema": _REGISTRATION_JOURNAL_SCHEMA,
                "server_name": server_name,
                "status": "prepared",
            },
        )
        for role in roles:
            if previous[role] is not None:
                provider.remove(role)
                touched.add(role)
        for role in roles:
            provider.add(role, desired[role])
            touched.add(role)
            if fault_injector is not None:
                fault_injector(f"after_add_{role}")
        for role in roles:
            if provider.get(role) != desired[role]:
                raise RegistrationTransactionError("registration_verify_mismatch")
        if host_configs is not None:
            timeout_kwargs: dict[str, object] = {}
            if server_name != "dayz-mcp":
                timeout_kwargs["server_name"] = server_name
            if host_journal_root is not None:
                timeout_kwargs["journal_root"] = Path(host_journal_root)
            if fault_injector is not None:
                timeout_kwargs["fault_injector"] = fault_injector
            apply_host_timeouts(*host_configs, **timeout_kwargs)
            for role in roles:
                if provider.get(role) != desired[role]:
                    raise RegistrationTransactionError("registration_verify_mismatch")
        _cleanup_registration_journal(journal)
    except RegistrationCrash:
        raise
    except Exception as error:
        try:
            _rollback_registrations(provider, previous, touched)
            _cleanup_registration_journal(journal)
        except Exception as rollback_error:
            raise RegistrationRollbackError("registration_rollback_failed") from rollback_error
        raise RegistrationTransactionError("registration_transaction_failed") from error


def run_runs_backup_gate(
    venv_python: Path,
    tools_root: Path,
    port: int,
    *,
    runner: CommandRunner = subprocess.run,
    instance_token: str | None = None,
    game_path: str | None = None,
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
    command = [
        str(python),
        "-I",
        "-B",
        str(script),
        "backup-runs-v1",
        "--port",
        str(port),
    ]
    if instance_token:
        command.extend(("--instance", instance_token))
    if game_path:
        command.extend(("--game-path", game_path))
    completed = runner(
        command,
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
    from dayz_mcp.server_cli import (
        InstanceSelectionError,
        bind_instance_context,
        reject_conflicting_environment,
    )

    try:
        bind_instance_context(
            options.instance_token, options.game_path, replace=True
        )
        reject_conflicting_environment(options.instance_token, options.port)
    except InstanceSelectionError as error:
        raise InstallerContractError(error.code) from error
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

    # install-mcp.ps1 passes the client executables it already resolved.
    # Pin them in this same process so --register still has one transactional
    # registration (lock, journal, recovery) and does not trust a .cmd shim.
    if options.claude_exe is not None and options.codex_exe is not None:
        pin_installer_clis(
            claude_exe=options.claude_exe,
            codex_exe=options.codex_exe,
            runner=runner,
        )
    manifest_path = installer_cli_manifest_path()
    fixture_path = installer_not_found_fixtures_path()
    manifest = load_installer_cli_manifest(manifest_path)
    not_found = load_installer_not_found_fixtures(fixture_path, manifest_path)
    from dayz_mcp.server_cli import registration_name

    provider = CliRegistrationProvider(
        manifest,
        not_found,
        runner=runner,
        server_name=registration_name(options.instance_token),
    )
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
        instance_token=options.instance_token,
        game_path=options.game_path,
    )
    transaction_kwargs = {
        "allow_option_removal": options.allow_option_removal,
        "host_configs": (
            Path.home() / ".claude.json",
            Path.home() / ".codex" / "config.toml",
        ),
    }
    selected_name = registration_name(options.instance_token)
    if selected_name != "dayz-mcp":
        transaction_kwargs["server_name"] = selected_name
    register_transaction(provider, desired, **transaction_kwargs)
    return {
        "status": "installed_and_registered",
        "registered": True,
        "venv_python": str(venv_python),
    }


def _public_installer_error_code(error: BaseException) -> str:
    """Identifier the installer prints. A path falls back to ``installer_failed``.

    ``OSError(errno, "registration_lock_open_failed")`` keeps the name in
    ``strerror``; ``str(error)`` wraps it with the errno and would otherwise
    collapse to ``installer_failed``.
    """
    code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_:-]+", code):
        return code
    strerror = getattr(error, "strerror", None)
    if isinstance(strerror, str) and re.fullmatch(r"[A-Za-z0-9_:-]+", strerror):
        return strerror
    text = str(error)
    if re.fullmatch(r"[A-Za-z0-9_:-]+", text):
        return text
    return "installer_failed"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        options = parse_args(argv)
        if options.pin_clis:
            entries = pin_installer_clis(
                claude_exe=options.claude_exe,
                codex_exe=options.codex_exe,
            )
            for entry in entries:
                print(f"{entry.role} {entry.path} {entry.bytes} {entry.sha256}")
            return 0
        result = run_installer(
            options,
            base_python=Path(sys.executable),
        )
        named = options.instance_token is not None
        pack_kwargs: dict[str, object] = {
            "sync": bool(options.register) and not named,
            "python_executable": Path(str(result["venv_python"])),
        }
        if named:
            pack_kwargs["owner"] = options.instance_token
        result["knowledge_pack"] = (
            {"status": "skipped"}
            if options.skip_knowledge_pack
            else install_knowledge_pack(**pack_kwargs)
        )
    except (
        InstallerContractError,
        InstallerExecutionError,
        KnowledgePackError,
        OSError,
        ValueError,
    ) as error:
        code = _public_installer_error_code(error)
        payload: dict[str, str] = {"status": "error", "error": code}
        remedy = getattr(error, "remedy", None)
        if isinstance(remedy, str) and remedy:
            payload["remedy"] = remedy
        detail = str(error)
        if detail and detail != code:
            payload["detail"] = detail
        print(
            json.dumps(payload, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
