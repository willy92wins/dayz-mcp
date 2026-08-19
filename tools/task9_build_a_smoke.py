from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import importlib
import json
import math
import ntpath
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import uuid
from dataclasses import dataclass
from ctypes import wintypes
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from PIL import Image


EXIT_OK = 0
EXIT_STOP = 2
EXIT_USAGE = 64
VERDICT_OWNER = "task9_build_a_smoke"
VERDICT_SCHEMA = "task9-build-a-smoke/v1"
OBJECT_TYPE = "MERCEDES_AMGLF"
SPAWN_FLAGS = 1028
HOLD_SCHEDULER_JITTER_SECONDS = 0.100
HOLD_TIMING_ROUNDING_TOLERANCE_SECONDS = 0.002
CLIENT_PEER_SETTLE_TIMEOUT_SECONDS = 60.0
CLIENT_PEER_SETTLE_SAMPLES = 5
CLIENT_PEER_SETTLE_INTERVAL_SECONDS = 0.5
CLIENT_PEER_SETTLE_MAX_POLL_AGE_SECONDS = 1.0
CAMERA_POSITION_TOLERANCE_METERS = 0.05
SPAWN_OPERATION_TIMEOUT_SECONDS = 30.0
SPAWN_OBSERVATION_MAX_SAMPLES = 64
TASK9_CONTINUE_CLIENT_GEOMETRIES = frozenset({(1920, 1080), (1280, 720)})
TASK9_CONTINUE_CLICK_X_FRACTION = 1620 / 1920
TASK9_CONTINUE_CLICK_Y_FRACTION = 904 / 1080
TASK9_CONTINUE_CAPTURE_TIMEOUT_S = 30.0
TASK9_CONTINUE_CAPTURE_INTERVAL_S = 2.0
TASK9_FOREGROUND_CONFIRM_SAMPLES = 10
TASK9_FOREGROUND_CONFIRM_INTERVAL_S = 0.05

DEFAULT_TOOLS_DIR = pathlib.Path(
    r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools"
)
DEFAULT_KEYFILE = DEFAULT_TOOLS_DIR / ".dayz_mcp.key"
DEFAULT_DAEMON_EXECUTABLE = DEFAULT_TOOLS_DIR / ".venv-mcp" / "Scripts" / "python.exe"
DEFAULT_DAYZ_EXECUTABLE = pathlib.Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe"
)
DEFAULT_HOST = pathlib.Path(
    r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\MERCEDES_AMGLF\data\mercedes_amglf.p3d"
)
DEFAULT_PBO = pathlib.Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop\@MERCEDES_AMGLF\Addons\MERCEDES_AMGLF.pbo"
)
DEFAULT_AUDIT = pathlib.Path(
    r"C:\Users\guill\AppData\Local\Temp\amglf-proxy-contract\build-a-audit\report.json"
)
DEFAULT_BACKUP_MANIFEST = pathlib.Path(
    r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\MERCEDES_AMGLF_dev\_backups\proxy-contract-build-a-20260711-193843\backup-manifest.json"
)
DEFAULT_SERVER_PROFILES = pathlib.Path(
    r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\MERCEDES_AMGLF_dev\_server\profiles"
)
DEFAULT_CLIENT_PROFILES = pathlib.Path(
    r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\MERCEDES_AMGLF_dev\_client\profiles"
)
DEFAULT_ACTIVE_MISSION_INIT = DEFAULT_SERVER_PROFILES.parent / (
    "mpmissions/dayzOffline.chernarusplus/init.c"
)
DEFAULT_STOCK_MISSION_INIT = pathlib.Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.chernarusplus\init.c"
)

DEFAULT_EXPECTED_HASHES = {
    "host_p3d": "F6E4C898B3E0E765A77CDFB9BF8F57AACF76A7B2822BCDAB679997E911F8CFDA",
    "pbo": "6DB34BDCA308F68829B1E758688EFAB380AA68094957E101676AF3C14C589911",
}

VIEW_SPECS: dict[str, dict[str, Any]] = {
    "front": {"offset": [0.0, 2.0, 7.0], "visual_gate": "BASELINE_EXTERIOR_REVIEW"},
    "rear": {"offset": [0.0, 2.0, -7.0], "visual_gate": "BASELINE_EXTERIOR_REVIEW"},
    "left": {"offset": [-7.0, 2.0, 0.0], "visual_gate": "BASELINE_EXTERIOR_REVIEW"},
    "right": {"offset": [7.0, 2.0, 0.0], "visual_gate": "BASELINE_EXTERIOR_REVIEW"},
    "front_left": {"offset": [-5.0, 2.0, 5.0], "visual_gate": "BASELINE_EXTERIOR_REVIEW"},
    "front_right": {"offset": [5.0, 2.0, 5.0], "visual_gate": "BASELINE_EXTERIOR_REVIEW"},
    "rear_left": {"offset": [-5.0, 2.0, -5.0], "visual_gate": "BASELINE_EXTERIOR_REVIEW"},
    "rear_right": {"offset": [5.0, 2.0, -5.0], "visual_gate": "BASELINE_EXTERIOR_REVIEW"},
}
BUILD_ID = "BASELINE_ROLLBACK_6DB34BDC"
TELEMETRY_RADIUS = 100.0
WHEEL_TYPE = "MERCEDES_AMGLF_Wheel"
VISUAL_REVIEW_CONTRACT = {
    "exterior_matrix": "ISOLATED_EQUIPPED_BASELINE",
    "interior_validation": "OUT_OF_SCOPE",
    "auto_pass_forbidden": True,
}


class DiscoveryError(RuntimeError):
    pass


class StopRun(RuntimeError):
    pass


class UsageError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    name: str
    command_line: str


@dataclass(frozen=True)
class PortBinding:
    protocol: str
    local_port: int
    pid: int


@dataclass(frozen=True)
class OwnershipSnapshot:
    processes: tuple[ProcessRecord, ...]
    ports: tuple[PortBinding, ...]


@dataclass(frozen=True)
class OwnershipResult:
    ok: bool
    reason: str
    server_pid: int = 0
    client_pid: int = 0
    daemon_pid: int = 0
    server_cmdline: str = ""
    client_cmdline: str = ""


@dataclass(frozen=True)
class ArtifactSpec:
    path: pathlib.Path
    expected_sha256: str


@dataclass
class SmokeConfig:
    output_dir: pathlib.Path
    artifacts: dict[str, ArtifactSpec]
    tools_dir: pathlib.Path = DEFAULT_TOOLS_DIR
    keyfile: pathlib.Path = DEFAULT_KEYFILE
    server_profiles: pathlib.Path = DEFAULT_SERVER_PROFILES
    client_profiles: pathlib.Path = DEFAULT_CLIENT_PROFILES
    active_mission_init: pathlib.Path = DEFAULT_ACTIVE_MISSION_INIT
    stock_mission_init: pathlib.Path = DEFAULT_STOCK_MISSION_INIT
    ready_timeout: float = 90.0
    hold_seconds: float = 30.0
    hold_interval: float = 1.0

    @property
    def verdict_path(self) -> pathlib.Path:
        return self.output_dir / "task-9-build-a-smoke-verdict.json"

    @property
    def evidence_dir(self) -> pathlib.Path:
        return self.output_dir / "evidence"


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    payload: dict[str, Any]


class OwnershipProvider(Protocol):
    def snapshot(self) -> OwnershipSnapshot:
        ...


class Runtime(Protocol):
    def client_factory(self) -> object:
        ...

    def query_player_state(
        self, client: object, timeout_s: float
    ) -> dict[str, Any]:
        ...

    def wait_for_readiness(
        self,
        client: object,
        camera_position: list[float],
        look_at: list[float],
        client_pid: int,
        client_cmdline_match: str,
        timeout_s: float,
    ) -> dict[str, Any]:
        ...

    def inspect_frontend_overlay(
        self,
        client_pid: int,
        client_cmdline_match: str,
        evidence_filename: str = "task9-continue-preaction.png",
    ) -> dict[str, Any]:
        ...

    def activate_frontend_window(
        self, client_pid: int, inspection: dict[str, Any]
    ) -> dict[str, Any]:
        ...

    def resume_frontend_overlay(
        self, client_pid: int, inspection: dict[str, Any]
    ) -> dict[str, Any]:
        ...

    def raycast(self, client: object, start: list[float], end: list[float]) -> dict[str, Any]:
        ...

    def spawn(
        self, client: object, object_type: str, position: list[float], flags: int
    ) -> dict[str, Any]:
        ...

    def telemetry_object_at(
        self, client: object, position: list[float], radius: float
    ) -> dict[str, Any]:
        ...

    def prepare_vehicle_fixture(
        self, client: object, position: list[float], radius: float
    ) -> dict[str, Any]:
        ...

    def wait_for_client_peer_settlement(
        self, client: object, timeout_s: float
    ) -> dict[str, Any]:
        ...

    def set_camera(
        self,
        client: object,
        view: str,
        camera_position: list[float],
        look_at: list[float],
    ) -> dict[str, Any]:
        ...

    def capture(
        self,
        view: str,
        destination: pathlib.Path,
        client_pid: int,
        client_cmdline_match: str,
    ) -> dict[str, Any]:
        ...

    def cleanup(self, client: object, object_id: int) -> dict[str, Any]:
        ...

    def collect_logs(self) -> list[dict[str, Any]]:
        ...

    def monotonic(self) -> float:
        ...

    def sleep(self, seconds: float) -> None:
        ...


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _snapshot_log_records(
    config: SmokeConfig, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    trusted_roots = {
        "server": config.server_profiles.resolve(),
        "client": config.client_profiles.resolve(),
    }
    snapshot_root = (config.evidence_dir / "logs").resolve()
    snapshots: list[dict[str, Any]] = []
    seen_source_paths: set[pathlib.Path] = set()
    for index, record in enumerate(records):
        source = record.get("source")
        profile_value = record.get("profile_path")
        path_value = record.get("path")
        if (
            source not in trusted_roots
            or not isinstance(profile_value, str)
            or not profile_value
            or not isinstance(path_value, str)
            or not path_value
        ):
            raise StopRun("log_evidence_invalid")
        profile_path = pathlib.Path(profile_value).resolve()
        source_path = pathlib.Path(path_value).resolve()
        if (
            profile_path != trusted_roots[source]
            or not source_path.is_file()
            or not source_path.is_relative_to(profile_path)
            or source_path in seen_source_paths
        ):
            raise StopRun(f"log_source_invalid:{source}")
        seen_source_paths.add(source_path)
        snapshot_path = snapshot_root / source / f"{index:02d}-{source_path.name}"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(source_path.read_bytes())
        snapshots.append(
            {
                "source": source,
                "profile_path": str(profile_path),
                "live_path": str(source_path),
                "path": str(snapshot_path),
                "sha256": sha256_file(snapshot_path),
            }
        )
    return snapshots


def _is_jpeg_file(path: pathlib.Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(3)
            stream.seek(-2, os.SEEK_END)
            trailer = stream.read(2)
        if header != b"\xff\xd8\xff" or trailer != b"\xff\xd9":
            return False
        with Image.open(path, formats=("JPEG",)) as image:
            width, height = image.size
            if image.format != "JPEG" or width <= 0 or height <= 0:
                return False
            image.verify()
    except (OSError, ValueError):
        return False
    return True


def atomic_write_json(destination: pathlib.Path, payload: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _stop_payload(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "owner": VERDICT_OWNER,
        "schema": VERDICT_SCHEMA,
        "result": "STOP",
        "model_verdict": "NOT_EVALUATED",
        "collection_status": "STOPPED",
        "stop_reason": reason,
        **extra,
    }


def _publish_stop(config: SmokeConfig, reason: str, **extra: Any) -> RunOutcome:
    payload = _stop_payload(reason, **extra)
    if not config.verdict_path.exists():
        atomic_write_json(config.verdict_path, payload)
    return RunOutcome(EXIT_STOP, payload)


def _publish_incident(config: SmokeConfig, payload: dict[str, Any]) -> pathlib.Path:
    token = uuid.uuid4().hex
    incident = config.output_dir.with_name(
        f"{config.output_dir.name}.incident-{token}.json"
    )
    temporary = config.output_dir.with_name(
        f".{config.output_dir.name}.incident-{token}.tmp"
    )
    incident_payload = {**payload, "incident_path": str(incident.resolve())}
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(incident_payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, incident)
    finally:
        if temporary.exists():
            temporary.unlink()
    return incident


def _is_dayz_process(process: ProcessRecord) -> bool:
    name = process.name.casefold()
    command = process.command_line.casefold()
    return name in {"dayzdiag_x64.exe", "dayzserver_x64.exe", "dayz_x64.exe"} or bool(
        re.search(r"(?:^|[\\/\s\"])(?:dayzdiag|dayzserver|dayz)_x64\.exe(?:\"|\s|$)", command)
    )


def _has_switch(command: str, switch: str) -> bool:
    return bool(re.search(rf"(?:^|\s){re.escape(switch)}(?:\s|$)", command, re.IGNORECASE))


def _fallback_windows_argv(command: str) -> list[str]:
    arguments: list[str] = []
    current: list[str] = []
    quoted = False
    for character in command.strip():
        if character == '"':
            quoted = not quoted
        elif character.isspace() and not quoted:
            if current:
                arguments.append("".join(current))
                current = []
        else:
            current.append(character)
    if quoted:
        raise ValueError("unclosed_command_line_quote")
    if current:
        arguments.append("".join(current))
    if not arguments:
        raise ValueError("empty_command_line")
    return arguments


def _windows_argv(command: str, *, allow_non_windows_test_fallback: bool = False) -> list[str]:
    if os.name != "nt":
        if not allow_non_windows_test_fallback:
            raise OSError("CommandLineToArgvW_unavailable")
        return _fallback_windows_argv(command)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    argument_count = ctypes.c_int()
    pointer = shell32.CommandLineToArgvW(command, ctypes.byref(argument_count))
    if not pointer:
        raise OSError(ctypes.get_last_error(), "CommandLineToArgvW_failed")
    try:
        arguments = [pointer[index] for index in range(argument_count.value)]
    finally:
        free_result = kernel32.LocalFree(pointer)
    if free_result:
        raise OSError(ctypes.get_last_error(), "LocalFree_failed")
    if not arguments:
        raise ValueError("empty_command_line")
    return arguments


def _parsed_options(command: str) -> tuple[str, dict[str, str | None]]:
    arguments = _windows_argv(command)
    options: dict[str, str | None] = {}
    index = 1
    while index < len(arguments):
        token = arguments[index]
        if not token.startswith("-"):
            raise ValueError(f"orphan_argument:{token}")
        if "=" in token:
            name, value = token.split("=", 1)
        else:
            name = token
            value = None
            if index + 1 < len(arguments) and not arguments[index + 1].startswith("-"):
                index += 1
                value = arguments[index]
        key = name.casefold()
        if key in options:
            raise ValueError(f"duplicate_argument:{name}")
        options[key] = value
        index += 1
    return arguments[0], options


def _normalized_windows_path(value: str | None) -> str:
    if not value:
        return ""
    if os.name == "nt":
        value = str(pathlib.Path(value).resolve(strict=False))
    return ntpath.normcase(ntpath.normpath(value))


def _mods(value: str | None) -> set[str]:
    if value is None:
        return set()
    return {
        ntpath.basename(ntpath.normpath(component.strip())).casefold()
        for component in value.split(";")
        if component.strip()
    }


def validate_ownership(snapshot: OwnershipSnapshot) -> OwnershipResult:
    dayz = [process for process in snapshot.processes if _is_dayz_process(process)]
    if len(dayz) != 2:
        return OwnershipResult(False, f"foreign_dayz_or_pair_count:{len(dayz)}")

    try:
        parsed = {process.pid: _parsed_options(process.command_line) for process in dayz}
    except ValueError as exc:
        return OwnershipResult(False, f"dayz_command_parse_error:{exc}")
    servers = [process for process in dayz if "-server" in parsed[process.pid][1]]
    clients = [process for process in dayz if "-server" not in parsed[process.pid][1]]
    if len(servers) != 1 or len(clients) != 1:
        return OwnershipResult(False, "mercedes_pair_role_mismatch")
    server = servers[0]
    client = clients[0]

    if server.name.casefold() != "dayzdiag_x64.exe" or client.name.casefold() != "dayzdiag_x64.exe":
        return OwnershipResult(False, "dayz_process_name_mismatch")
    if any(
        _normalized_windows_path(parsed[process.pid][0])
        != _normalized_windows_path(str(DEFAULT_DAYZ_EXECUTABLE))
        for process in (server, client)
    ):
        return OwnershipResult(False, "dayz_executable_path_mismatch")
    server_options = parsed[server.pid][1]
    client_options = parsed[client.pid][1]
    if "-connect" in server_options or "-server" in client_options:
        return OwnershipResult(False, "dayz_role_contradiction")
    common_required = {"@mercedes_amglf", "@dayz_mcp"}
    if not common_required.issubset(_mods(server_options.get("-mod"))):
        return OwnershipResult(False, "server_mod_mismatch")
    if not common_required.issubset(_mods(client_options.get("-mod"))):
        return OwnershipResult(False, "client_mod_mismatch")
    if _normalized_windows_path(server_options.get("-profiles")) != _normalized_windows_path(
        str(DEFAULT_SERVER_PROFILES)
    ):
        return OwnershipResult(False, "server_profile_mismatch")
    if _normalized_windows_path(client_options.get("-profiles")) != _normalized_windows_path(
        str(DEFAULT_CLIENT_PROFILES)
    ):
        return OwnershipResult(False, "client_profile_mismatch")
    if server_options.get("-port") != "2302" or server_options.get("-server") is not None:
        return OwnershipResult(False, "server_endpoint_mismatch")
    if client_options.get("-connect") != "127.0.0.1" or client_options.get("-port") != "2302":
        return OwnershipResult(False, "client_endpoint_mismatch")

    udp = [
        binding
        for binding in snapshot.ports
        if binding.protocol.upper() == "UDP" and binding.local_port == 2302
    ]
    if len(udp) != 1 or udp[0].pid != server.pid:
        return OwnershipResult(False, "udp_2302_owner_mismatch")

    tcp = [
        binding
        for binding in snapshot.ports
        if binding.protocol.upper() == "TCP" and binding.local_port == 8765
    ]
    if len(tcp) != 1:
        return OwnershipResult(False, "daemon_tcp_8765_owner_mismatch")
    daemon = next((process for process in snapshot.processes if process.pid == tcp[0].pid), None)
    if daemon is None:
        return OwnershipResult(False, "daemon_process_missing")
    try:
        daemon_executable, daemon_options = _parsed_options(daemon.command_line)
    except ValueError as exc:
        return OwnershipResult(False, f"daemon_command_parse_error:{exc}")
    if (
        _normalized_windows_path(daemon_executable)
        != _normalized_windows_path(str(DEFAULT_DAEMON_EXECUTABLE))
        or daemon_options.get("-m") != "dayz_mcp"
        or "--daemon" not in daemon_options
        or daemon_options.get("--daemon") is not None
        or daemon_options.get("--port") != "8765"
        or _normalized_windows_path(daemon_options.get("--keyfile"))
        != _normalized_windows_path(str(DEFAULT_KEYFILE))
        or "--require-version" not in daemon_options
        or daemon_options.get("--require-version") is not None
    ):
        return OwnershipResult(False, "daemon_command_mismatch")

    return OwnershipResult(
        True,
        "owned",
        server_pid=server.pid,
        client_pid=client.pid,
        daemon_pid=daemon.pid,
        server_cmdline=server.command_line,
        client_cmdline=client.command_line,
    )


def _as_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise DiscoveryError(f"{label}_not_list")
    return value


def parse_windows_snapshot(raw: str) -> OwnershipSnapshot:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"snapshot_json_error:{exc}") from exc
    if not isinstance(payload, dict):
        raise DiscoveryError("snapshot_not_object")
    processes_raw = _as_list(payload.get("processes"), "processes")
    ports_raw = _as_list(payload.get("ports"), "ports")
    try:
        processes = tuple(
            ProcessRecord(
                pid=int(item.get("pid", item.get("ProcessId"))),
                name=str(item.get("name", item.get("Name")) or ""),
                command_line=str(item.get("command_line", item.get("CommandLine")) or ""),
            )
            for item in processes_raw
            if isinstance(item, dict)
        )
        ports = tuple(
            PortBinding(
                protocol=str(item.get("protocol", item.get("Protocol")) or ""),
                local_port=int(item.get("local_port", item.get("LocalPort"))),
                pid=int(item.get("pid", item.get("OwningProcess"))),
            )
            for item in ports_raw
            if isinstance(item, dict)
        )
    except (TypeError, ValueError) as exc:
        raise DiscoveryError(f"snapshot_field_error:{exc}") from exc
    if len(processes) != len(processes_raw) or len(ports) != len(ports_raw):
        raise DiscoveryError("snapshot_entry_not_object")
    return OwnershipSnapshot(processes=processes, ports=ports)


class WindowsOwnershipProvider:
    _SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$processes = @(Get-CimInstance Win32_Process | ForEach-Object {
    [pscustomobject]@{ pid=[int]$_.ProcessId; name=[string]$_.Name; command_line=[string]$_.CommandLine }
})
$ports = @()
$ports += @(Get-NetUDPEndpoint | ForEach-Object {
    [pscustomobject]@{ protocol='UDP'; local_port=[int]$_.LocalPort; pid=[int]$_.OwningProcess }
})
$ports += @(Get-NetTCPConnection -State Listen | ForEach-Object {
    [pscustomobject]@{ protocol='TCP'; local_port=[int]$_.LocalPort; pid=[int]$_.OwningProcess }
})
[pscustomobject]@{ processes=$processes; ports=$ports } | ConvertTo-Json -Depth 4 -Compress
"""

    def snapshot(self) -> OwnershipSnapshot:
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", self._SCRIPT],
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
                timeout=20.0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DiscoveryError(f"ownership_provider_error:{exc}") from exc
        if completed.returncode != 0:
            raise DiscoveryError(
                f"ownership_provider_exit:{completed.returncode}:{completed.stderr.strip()[:300]}"
            )
        return parse_windows_snapshot(completed.stdout)


def _validate_neutral_mission(active: pathlib.Path, stock: pathlib.Path) -> None:
    if not active.is_file():
        raise StopRun("active_mission_init_missing")
    if not stock.is_file():
        raise StopRun("stock_mission_init_missing")
    active_bytes = active.read_bytes()
    if any(
        token in active_bytes
        for token in (
            b'CreateObjectEx("MERCEDES_AMGLF"',
            b'CreateObjectEx("CivilianSedan"',
        )
    ):
        raise StopRun("active_mission_init_autospawn")
    if active_bytes != stock.read_bytes():
        raise StopRun("active_mission_init_not_stock")


def _artifact_observation(artifacts: dict[str, ArtifactSpec]) -> dict[str, dict[str, str]]:
    observed: dict[str, dict[str, str]] = {}
    for name, spec in artifacts.items():
        if not spec.path.is_file():
            raise StopRun(f"artifact_missing:{name}")
        actual = sha256_file(spec.path)
        expected = spec.expected_sha256.upper()
        if actual != expected:
            raise StopRun(f"hash_drift:{name}:expected={expected}:actual={actual}")
        observed[name] = {
            "path": str(spec.path.resolve()),
            "sha256": actual,
            "expected_sha256": expected,
        }
    return observed


def _snapshot_artifact_records(
    config: SmokeConfig, observations: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    snapshot_root = (config.evidence_dir / "artifacts").resolve()
    snapshots: dict[str, dict[str, str]] = {}
    for name in sorted(observations):
        if not name or pathlib.Path(name).name != name:
            raise StopRun(f"artifact_name_invalid:{name}")
        observation = observations[name]
        live_path = pathlib.Path(observation["path"]).resolve()
        recorded = observation["sha256"]
        expected = observation["expected_sha256"]
        if not live_path.is_file() or sha256_file(live_path) != recorded or recorded != expected:
            raise StopRun(f"artifact_source_drift:{name}")
        snapshot_path = snapshot_root / name / live_path.name
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        if snapshot_path.exists():
            raise StopRun(f"artifact_snapshot_exists:{name}")
        snapshot_path.write_bytes(live_path.read_bytes())
        if sha256_file(snapshot_path) != recorded:
            raise StopRun(f"artifact_snapshot_hash_drift:{name}")
        snapshots[name] = {
            "live_path": str(live_path),
            "path": str(snapshot_path),
            "sha256": recorded,
            "expected_sha256": expected,
        }
    return snapshots


def _same_owners(expected: OwnershipResult, current: OwnershipResult) -> bool:
    return current.ok and (
        current.server_pid,
        current.client_pid,
        current.daemon_pid,
    ) == (expected.server_pid, expected.client_pid, expected.daemon_pid)


def _snapshot_summary(snapshot: OwnershipSnapshot) -> dict[str, Any]:
    return {
        "processes": [
            {"pid": process.pid, "name": process.name, "command_line": process.command_line}
            for process in snapshot.processes
        ],
        "ports": [
            {"protocol": binding.protocol, "local_port": binding.local_port, "pid": binding.pid}
            for binding in snapshot.ports
            if binding.local_port in (2302, 8765)
        ],
    }


def _vec3(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise StopRun(f"{label}_missing")
    if any(isinstance(component, bool) for component in value):
        raise StopRun(f"{label}_invalid")
    try:
        converted = [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError) as exc:
        raise StopRun(f"{label}_invalid") from exc
    if not all(math.isfinite(component) for component in converted):
        raise StopRun(f"{label}_invalid")
    return converted


def _add(left: list[float], right: list[float]) -> list[float]:
    return [left[index] + right[index] for index in range(3)]


def _dayz_true(value: object) -> bool:
    return value is True or (type(value) is int and value == 1)


def _dayz_false(value: object) -> bool:
    return value is False or (type(value) is int and value == 0)


def _camera_contract_failure(
    result: object, expected_position: list[float]
) -> str:
    if not isinstance(result, dict) or not _dayz_true(result.get("ok")):
        return "result_not_ok"
    camera = result.get("camera")
    if not isinstance(camera, dict):
        return "camera_missing"
    if not _dayz_true(camera.get("ok")):
        return "camera_not_ok"
    if not _dayz_true(camera.get("viewport_moved")):
        return "viewport_not_moved"
    if camera.get("applied_mode") != "lookat":
        return "mode_mismatch"
    try:
        observed_position = _vec3(camera.get("pos"), "camera_position")
    except StopRun:
        return "position_invalid"
    if math.dist(observed_position, expected_position) > CAMERA_POSITION_TOLERANCE_METERS:
        return "position_mismatch"
    return ""


def _telemetry(result: object, label: str) -> dict[str, Any]:
    if not isinstance(result, dict) or not _dayz_true(result.get("ok")):
        error = (result.get("error") or "not_ok") if isinstance(result, dict) else "invalid_payload"
        raise StopRun(f"{label}_failed:{error}")
    telemetry = result.get("telemetry")
    if not isinstance(telemetry, dict):
        raise StopRun(f"{label}_telemetry_invalid")
    return telemetry


def _require_absent(result: object, label: str) -> dict[str, Any]:
    telemetry = _telemetry(result, label)
    if not _dayz_false(telemetry.get("found")):
        raise StopRun(f"{label}_not_absent")
    return telemetry


def _require_unique_vehicle(result: object) -> dict[str, Any]:
    telemetry = _telemetry(result, "post_spawn_unique")
    if (
        not _dayz_true(telemetry.get("found"))
        or telemetry.get("type") != OBJECT_TYPE
        or telemetry.get("class_name") != OBJECT_TYPE
    ):
        raise StopRun("post_spawn_unique_identity_invalid")
    return telemetry


def _require_fixture(result: object) -> dict[str, Any]:
    if (
        not isinstance(result, dict)
        or not _dayz_true(result.get("ok"))
        or not _dayz_true(result.get("vehicle_fixture_ready"))
    ):
        error = (result.get("error") or "not_ready") if isinstance(result, dict) else "invalid_payload"
        raise StopRun(f"vehicle_fixture_failed:{error}")
    telemetry = result.get("telemetry")
    if not isinstance(telemetry, dict):
        raise StopRun("vehicle_fixture_telemetry_invalid")
    wheel_count = telemetry.get("wheel_count")
    attachment_count = telemetry.get("attachment_count")
    items = telemetry.get("items")
    if type(wheel_count) is not int or wheel_count != 4:
        raise StopRun("vehicle_fixture_wheel_count_invalid")
    if type(attachment_count) is not int or attachment_count < 4:
        raise StopRun("vehicle_fixture_attachment_count_invalid")
    if not isinstance(items, list) or items.count(WHEEL_TYPE) != 4:
        raise StopRun("vehicle_fixture_wheel_items_invalid")
    return telemetry


def _raycast_position(result: dict[str, Any]) -> list[float]:
    raycast = result.get("raycast") if isinstance(result.get("raycast"), dict) else {}
    ok = result.get("ok")
    hit = raycast.get("hit")
    ok_is_true = ok is True or (type(ok) is int and ok == 1)
    hit_is_true = hit is True or (type(hit) is int and hit == 1)
    if not ok_is_true or not hit_is_true:
        raise StopRun("raycast_no_hit")
    object_type = str(raycast.get("object_type") or "")
    parent_type = str(raycast.get("parent_type") or "")
    occupied = raycast.get("occupied") is True or bool(parent_type)
    if object_type and not object_type.casefold().startswith(("cp_", "land_")):
        occupied = True
    if occupied:
        raise StopRun(f"raycast_blocked:{object_type or parent_type or 'occupied'}")
    return _vec3(raycast.get("pos"), "raycast_position")


def _wait_for_peer_settlement(
    client: object,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    *,
    peer_name: str,
    timeout_s: float,
) -> dict[str, Any]:
    started = monotonic()
    deadline = started + timeout_s
    observations: list[dict[str, Any]] = []
    stable_samples = 0
    recovery_observed = False
    daemon_generation = ""
    last_error = ""

    while monotonic() < deadline:
        observed_at = monotonic()
        status: dict[str, Any] = {}
        try:
            raw_status = client.request_json("GET", "/status")  # type: ignore[attr-defined]
            if type(raw_status) is dict:
                status = raw_status
            else:
                last_error = "status_payload_invalid"
        except Exception as exc:
            last_error = f"status_request_{type(exc).__name__}"

        peer_key = f"{peer_name}_peer"
        peer = status.get(peer_key) if type(status.get(peer_key)) is dict else {}
        poll_age = peer.get("last_poll_age_s")
        queue_depth = peer.get("queue_depth")
        version_state = peer.get("version_state")
        current_generation = status.get("daemon_generation")
        generation_valid = type(current_generation) is str and bool(current_generation)
        if generation_valid and not daemon_generation:
            daemon_generation = current_generation
        generation_stable = generation_valid and current_generation == daemon_generation
        poll_age_valid = (
            type(poll_age) in (int, float)
            and math.isfinite(float(poll_age))
            and 0.0 <= float(poll_age) <= CLIENT_PEER_SETTLE_MAX_POLL_AGE_SECONDS
        )
        queue_clean = type(queue_depth) is int and queue_depth == 0
        version_ok = version_state == "ok"
        healthy = generation_stable and poll_age_valid and queue_clean and version_ok

        if healthy:
            stable_samples += 1
            last_error = ""
        else:
            stable_samples = 0
            recovery_observed = True
            if not last_error:
                if not generation_stable:
                    last_error = "daemon_generation_unstable"
                elif not poll_age_valid:
                    last_error = "client_poll_stale"
                elif not queue_clean:
                    last_error = "client_queue_not_empty"
                else:
                    last_error = "client_version_not_ok"

        observations.append(
            {
                "elapsed_s": round(max(0.0, observed_at - started), 3),
                "last_poll_age_s": poll_age,
                "queue_depth": queue_depth,
                "version_state": version_state,
                "daemon_generation": current_generation,
                "healthy": healthy,
                "error": last_error,
            }
        )
        if stable_samples >= CLIENT_PEER_SETTLE_SAMPLES:
            return {
                "ready": True,
                "timeout_s": timeout_s,
                "samples_required": CLIENT_PEER_SETTLE_SAMPLES,
                "samples_observed": len(observations),
                "stable_samples": stable_samples,
                "interval_s": CLIENT_PEER_SETTLE_INTERVAL_SECONDS,
                "max_poll_age_s": CLIENT_PEER_SETTLE_MAX_POLL_AGE_SECONDS,
                "last_poll_age_s": poll_age,
                "daemon_generation": daemon_generation,
                "recovery_observed": recovery_observed,
                "observations": observations,
            }

        remaining = deadline - monotonic()
        if remaining <= 0.0:
            break
        sleep(min(CLIENT_PEER_SETTLE_INTERVAL_SECONDS, remaining))

    return {
        "ready": False,
        "error": f"{peer_name}_peer_settlement_timeout",
        "last_error": last_error,
        "timeout_s": timeout_s,
        "samples_required": CLIENT_PEER_SETTLE_SAMPLES,
        "samples_observed": len(observations),
        "stable_samples": stable_samples,
        "interval_s": CLIENT_PEER_SETTLE_INTERVAL_SECONDS,
        "max_poll_age_s": CLIENT_PEER_SETTLE_MAX_POLL_AGE_SECONDS,
        "last_poll_age_s": observations[-1].get("last_poll_age_s") if observations else None,
        "daemon_generation": daemon_generation,
        "recovery_observed": recovery_observed,
        "observations": observations,
    }


def _wait_for_client_peer_settlement(
    client: object,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    *,
    timeout_s: float,
) -> dict[str, Any]:
    return _wait_for_peer_settlement(
        client,
        monotonic,
        sleep,
        peer_name="client",
        timeout_s=timeout_s,
    )


def _wait_for_server_peer_settlement(
    client: object,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    *,
    timeout_s: float,
) -> dict[str, Any]:
    return _wait_for_peer_settlement(
        client,
        monotonic,
        sleep,
        peer_name="server",
        timeout_s=timeout_s,
    )


def _capture_views(
    runtime: Runtime,
    client: object,
    config: SmokeConfig,
    ownership: OwnershipResult,
    position: list[float],
    evidence: dict[str, dict[str, Any]],
    camera_results: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    look_at = _add(position, [0.0, 1.0, 0.0])
    hashes_by_view: dict[str, str] = {}
    for view, specification in VIEW_SPECS.items():
        camera_position = _add(position, specification["offset"])
        camera_result = runtime.set_camera(client, view, camera_position, look_at)
        camera_results[view] = camera_result
        camera_failure = _camera_contract_failure(camera_result, camera_position)
        if camera_failure == "result_not_ok":
            raise StopRun(f"camera_failed:{view}")
        if camera_failure:
            raise StopRun(f"camera_contract_failed:{view}:{camera_failure}")
        destination = config.evidence_dir / f"{view}.jpg"
        capture = runtime.capture(
            view,
            destination,
            ownership.client_pid,
            ownership.client_cmdline,
        )
        if capture.get("isError") is True or not destination.is_file():
            raise StopRun(f"capture_failed:{view}:{capture.get('error', 'missing_fullres')}")
        if not _is_jpeg_file(destination):
            raise StopRun(f"capture_format_invalid:{view}:expected_jpeg")
        capture_sha256 = sha256_file(destination)
        for prior_view, prior_sha256 in hashes_by_view.items():
            if capture_sha256 == prior_sha256:
                raise StopRun(f"capture_stale:{view}:matches:{prior_view}")
        hashes_by_view[view] = capture_sha256
        evidence[view] = {
            "path": str(destination.resolve()),
            "sha256": capture_sha256,
            "native_width": (capture.get("meta") or {}).get("native_width"),
            "native_height": (capture.get("meta") or {}).get("native_height"),
            "format": "JPEG",
            "visual_gate": specification["visual_gate"],
        }
    return evidence


def _hold_owned_processes(
    config: SmokeConfig,
    provider: OwnershipProvider,
    runtime: Runtime,
    ownership: OwnershipResult,
    evidence: dict[str, Any],
) -> OwnershipSnapshot:
    if (
        not isinstance(config.hold_interval, (int, float))
        or isinstance(config.hold_interval, bool)
        or not math.isfinite(config.hold_interval)
        or config.hold_interval <= 0.0
    ):
        raise StopRun("hold_sample_interval_invalid")
    started = runtime.monotonic()
    samples: list[dict[str, Any]] = []
    evidence.update(
        {
            "clock": "monotonic",
            "required_seconds": config.hold_seconds,
            "sample_interval_seconds": config.hold_interval,
            "elapsed_seconds": 0.0,
            "observed_span_seconds": 0.0,
            "samples": samples,
        }
    )
    last_snapshot: OwnershipSnapshot | None = None
    first_sample_elapsed: float | None = None
    while True:
        snapshot_started_elapsed = runtime.monotonic() - started
        last_snapshot = provider.snapshot()
        elapsed = runtime.monotonic() - started
        snapshot_duration = elapsed - snapshot_started_elapsed
        current = validate_ownership(last_snapshot)
        if first_sample_elapsed is None:
            first_sample_elapsed = elapsed
        samples.append(
            {
                "snapshot_started_elapsed": round(snapshot_started_elapsed, 3),
                "snapshot_duration_seconds": round(snapshot_duration, 3),
                "elapsed_seconds": round(elapsed, 3),
                "ok": _same_owners(ownership, current),
                "reason": current.reason,
                "server_pid": current.server_pid,
                "client_pid": current.client_pid,
                "daemon_pid": current.daemon_pid,
            }
        )
        evidence["elapsed_seconds"] = round(elapsed, 3)
        observed_span = elapsed - first_sample_elapsed
        evidence["observed_span_seconds"] = round(observed_span, 3)
        evidence["last_snapshot"] = _snapshot_summary(last_snapshot)
        if not _same_owners(ownership, current):
            raise StopRun(f"owned_process_loss:{current.reason}")
        if observed_span >= config.hold_seconds:
            break
        runtime.sleep(min(config.hold_interval, config.hold_seconds - observed_span))
    evidence["elapsed_seconds"] = round(runtime.monotonic() - started, 3)
    return last_snapshot


def _wait_for_player_state(
    runtime: Runtime,
    client: object,
    started: float,
    timeout_s: float,
) -> dict[str, Any]:
    if (
        not isinstance(timeout_s, (int, float))
        or isinstance(timeout_s, bool)
        or not math.isfinite(timeout_s)
        or timeout_s <= 0.0
    ):
        raise StopRun("ready_timeout_invalid")
    last_transient_error = "peer_reconnect_flush"
    while True:
        remaining = timeout_s - (runtime.monotonic() - started)
        if remaining <= 0.0:
            raise StopRun(f"player_state_settlement_timeout:{last_transient_error}")
        player = runtime.query_player_state(client, remaining)
        ok = player.get("ok")
        if ok is True or (type(ok) is int and ok == 1):
            return player
        raw_error = player.get("error")
        if type(raw_error) is str and raw_error in {
            "peer_reconnect_flush",
            "no_players",
        }:
            transient_error = raw_error
        elif (
            player.get("ok") is False
            and raw_error == "version_blocked"
            and player.get("state") == "legacy_blocked"
        ):
            transient_error = "version_blocked:legacy_blocked"
        else:
            error = str(raw_error or "not_ok")
            raise StopRun(f"player_state_failed:{error}")
        last_transient_error = transient_error
        remaining = timeout_s - (runtime.monotonic() - started)
        if remaining <= 0.0:
            raise StopRun(f"player_state_settlement_timeout:{last_transient_error}")
        runtime.sleep(min(1.0, remaining))


def collect(
    config: SmokeConfig,
    provider: OwnershipProvider,
    runtime_factory: Callable[[SmokeConfig], Runtime],
) -> RunOutcome:
    if config.output_dir.exists():
        payload = _stop_payload("existing_output_not_owned_or_not_absent")
        try:
            incident = _publish_incident(config, payload)
            payload["incident_path"] = str(incident.resolve())
        except Exception as exc:
            payload["incident_error"] = f"{type(exc).__name__}:{exc}"
        return RunOutcome(EXIT_STOP, payload)

    try:
        _validate_neutral_mission(config.active_mission_init, config.stock_mission_init)
        artifacts_before = _artifact_observation(config.artifacts)
        initial_snapshot = provider.snapshot()
        ownership = validate_ownership(initial_snapshot)
        if not ownership.ok:
            return _publish_stop(
                config,
                ownership.reason,
                initial_snapshot=_snapshot_summary(initial_snapshot),
            )
    except Exception as exc:
        return _publish_stop(config, f"preflight_error:{exc}")

    runtime: Runtime
    client: object
    readiness: dict[str, Any] | None = None
    frontend_resume: dict[str, Any] | None = None
    late_frontend_resume: dict[str, Any] | None = None
    try:
        runtime = runtime_factory(config)
        client = runtime.client_factory()
        ready_started = runtime.monotonic()
        player = _wait_for_player_state(
            runtime, client, ready_started, config.ready_timeout
        )
        player_state = player.get("state")
        player_position = _vec3(
            player_state.get("pos")
            if isinstance(player_state, Mapping)
            else player.get("pos"),
            "player_position",
        )
        inspection = runtime.inspect_frontend_overlay(
            ownership.client_pid, ownership.client_cmdline
        )
        inspection_valid = (
            type(inspection) is dict
            and inspection.get("ok") is True
            and type(inspection.get("detected")) is bool
            and type(inspection.get("reason")) is str
        )
        inspection_deferred = (
            type(inspection) is dict
            and inspection.get("ok") is False
            and inspection.get("detected") is False
            and inspection.get("reason") == "continue_client_stats_invalid"
        )
        if not inspection_valid and not inspection_deferred:
            reason = (
                inspection.get("reason")
                if type(inspection) is dict and type(inspection.get("reason")) is str
                else "invalid_inspection_payload"
            )
            frontend_resume = {
                "inspection": inspection,
                "action": {"ok": False, "attempted": False, "reason": reason},
            }
            raise StopRun(f"frontend_inspection_failed:{reason}")
        if inspection_deferred:
            frontend_resume = {
                "inspection": inspection,
                "action": {
                    "ok": False,
                    "attempted": False,
                    "reason": "foreground_activation_not_attempted",
                },
            }
            frontend_snapshot = provider.snapshot()
            frontend_ownership = validate_ownership(frontend_snapshot)
            frontend_resume["ownership_snapshot"] = _snapshot_summary(
                frontend_snapshot
            )
            if not _same_owners(ownership, frontend_ownership):
                raise StopRun(
                    "ownership_changed_before_frontend_activation:"
                    f"{frontend_ownership.reason}"
                )
            action = runtime.activate_frontend_window(
                ownership.client_pid, inspection
            )
            frontend_resume["action"] = action
            if not (
                type(action) is dict
                and action.get("ok") is True
                and action.get("attempted") is True
                and type(action.get("reason")) is str
            ):
                reason = (
                    action.get("reason")
                    if type(action) is dict and type(action.get("reason")) is str
                    else "invalid_action_payload"
                )
                raise StopRun(f"frontend_activation_failed:{reason}")
        else:
            frontend_resume = {
                "inspection": inspection,
                "action": {
                    "ok": True,
                    "attempted": False,
                    "reason": "continue_overlay_absent",
                },
            }
        if inspection_valid and inspection["detected"]:
            frontend_snapshot = provider.snapshot()
            frontend_ownership = validate_ownership(frontend_snapshot)
            frontend_resume["ownership_snapshot"] = _snapshot_summary(frontend_snapshot)
            if not _same_owners(ownership, frontend_ownership):
                raise StopRun(
                    "ownership_changed_before_frontend_resume:"
                    f"{frontend_ownership.reason}"
                )
            action = runtime.resume_frontend_overlay(
                ownership.client_pid, inspection
            )
            frontend_resume["action"] = action
            if not (
                type(action) is dict
                and action.get("ok") is True
                and action.get("attempted") is True
                and type(action.get("reason")) is str
            ):
                reason = (
                    action.get("reason")
                    if type(action) is dict and type(action.get("reason")) is str
                    else "invalid_action_payload"
                )
                raise StopRun(f"frontend_resume_failed:{reason}")
        readiness_timeout = config.ready_timeout - (runtime.monotonic() - ready_started)
        if readiness_timeout <= 0.0:
            raise StopRun("readiness_timeout_exhausted")
        ready_camera = _add(player_position, [3.0, 2.0, 3.0])
        readiness = runtime.wait_for_readiness(
            client,
            ready_camera,
            player_position,
            ownership.client_pid,
            ownership.client_cmdline,
            readiness_timeout,
        )
        if readiness.get("inworld") is not True:
            late_inspection = runtime.inspect_frontend_overlay(
                ownership.client_pid,
                ownership.client_cmdline,
                "task9-continue-late.png",
            )
            if not (
                type(late_inspection) is dict
                and late_inspection.get("ok") is True
                and type(late_inspection.get("detected")) is bool
                and type(late_inspection.get("reason")) is str
            ):
                reason = (
                    late_inspection.get("reason")
                    if type(late_inspection) is dict
                    and type(late_inspection.get("reason")) is str
                    else "invalid_inspection_payload"
                )
                late_frontend_resume = {
                    "inspection": late_inspection,
                    "action": {"ok": False, "attempted": False, "reason": reason},
                }
                raise StopRun(f"late_frontend_inspection_failed:{reason}")
            late_frontend_resume = {
                "inspection": late_inspection,
                "action": {
                    "ok": True,
                    "attempted": False,
                    "reason": "continue_overlay_absent",
                },
            }
            if late_inspection["detected"]:
                late_frontend_snapshot = provider.snapshot()
                late_frontend_ownership = validate_ownership(late_frontend_snapshot)
                late_frontend_resume["ownership_snapshot"] = _snapshot_summary(
                    late_frontend_snapshot
                )
                if not _same_owners(ownership, late_frontend_ownership):
                    raise StopRun(
                        "ownership_changed_before_late_frontend_resume:"
                        f"{late_frontend_ownership.reason}"
                    )
                late_action = runtime.resume_frontend_overlay(
                    ownership.client_pid, late_inspection
                )
                late_frontend_resume["action"] = late_action
                if not (
                    type(late_action) is dict
                    and late_action.get("ok") is True
                    and late_action.get("attempted") is True
                    and type(late_action.get("reason")) is str
                ):
                    reason = (
                        late_action.get("reason")
                        if type(late_action) is dict
                        and type(late_action.get("reason")) is str
                        else "invalid_action_payload"
                    )
                    raise StopRun(f"late_frontend_resume_failed:{reason}")
                readiness_before_resume = readiness
                readiness = runtime.wait_for_readiness(
                    client,
                    ready_camera,
                    player_position,
                    ownership.client_pid,
                    ownership.client_cmdline,
                    config.ready_timeout,
                )
                late_frontend_resume["readiness_before_resume"] = (
                    readiness_before_resume
                )
                late_frontend_resume["readiness_after_resume"] = readiness
        if readiness.get("inworld") is not True:
            raise StopRun("readiness_not_inworld")

        pre_spawn_telemetry = _require_absent(
            runtime.telemetry_object_at(client, player_position, TELEMETRY_RADIUS),
            "pre_spawn_isolation",
        )

        second_snapshot = provider.snapshot()
        second_ownership = validate_ownership(second_snapshot)
        if not _same_owners(ownership, second_ownership):
            raise StopRun(f"ownership_changed_before_spawn:{second_ownership.reason}")

        candidate = _add(player_position, [4.0, 0.0, 4.0])
        raycast_result = runtime.raycast(
            client,
            _add(candidate, [0.0, 5.0, 0.0]),
            _add(candidate, [0.0, -5.0, 0.0]),
        )
        spawn_position = _raycast_position(raycast_result)
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        readiness_evidence = {"readiness": readiness} if readiness is not None else {}
        frontend_evidence = (
            {"frontend_resume": frontend_resume}
            if frontend_resume is not None
            else {}
        )
        late_frontend_evidence = (
            {"late_frontend_resume": late_frontend_resume}
            if late_frontend_resume is not None
            else {}
        )
        return _publish_stop(
            config,
            f"runtime_before_spawn_stop:{exc}",
            **readiness_evidence,
            **frontend_evidence,
            **late_frontend_evidence,
        )

    operation_error = ""
    views: dict[str, dict[str, Any]] = {}
    camera_results: dict[str, dict[str, Any]] = {}
    hold: dict[str, Any] = {}
    final_snapshot: OwnershipSnapshot = second_snapshot
    artifacts_after: dict[str, dict[str, str]] = {}
    artifact_evidence: dict[str, dict[str, str]] = {}
    logs: list[dict[str, Any]] = []
    cleanup_ok = False
    cleanup_attempted = False
    cleanup_outcome = "NOT_ATTEMPTED_NO_OBJECT_ID"
    possible_orphan = False
    cleanup_response: dict[str, Any] = {}
    post_delete_telemetry: dict[str, Any] = {}
    object_id = 0
    recorded_position = spawn_position
    spawn_invoked = False
    spawn_result: dict[str, Any] = {}
    spawn_protocol: dict[str, Any] = {}
    spawn_rejected = False
    unique_vehicle: dict[str, Any] = {}
    fixture: dict[str, Any] = {}
    client_settlement: dict[str, Any] = {}
    try:
        spawn_invoked = True
        raw_spawn_result = runtime.spawn(client, OBJECT_TYPE, spawn_position, SPAWN_FLAGS)
        if not isinstance(raw_spawn_result, dict):
            raise StopRun("spawn_payload_invalid")
        spawn_result = raw_spawn_result
        raw_spawn_protocol = spawn_result.get("command_observation")
        if type(raw_spawn_protocol) is dict:
            spawn_protocol = raw_spawn_protocol
        protocol_command_id = spawn_protocol.get("command_id")
        command_was_enqueued = (
            type(protocol_command_id) is int and protocol_command_id > 0
        )
        spawn_rejected = _dayz_false(spawn_result.get("ok")) and not command_was_enqueued
        spawn_ok = spawn_result.get("ok")
        candidate_object_id = spawn_result.get("object_id")
        if isinstance(candidate_object_id, int) and not isinstance(candidate_object_id, bool):
            object_id = candidate_object_id
        if not (spawn_ok is True or (type(spawn_ok) is int and spawn_ok == 1)) or object_id <= 0:
            raise StopRun(f"spawn_failed:{spawn_result.get('error', 'invalid_object_id')}")
        recorded_position = _vec3(spawn_result.get("pos", spawn_position), "spawn_position")
        unique_telemetry = _require_unique_vehicle(
            runtime.telemetry_object_at(client, recorded_position, TELEMETRY_RADIUS)
        )
        unique_vehicle = {
            "verified": True,
            "type": unique_telemetry["type"],
            "class_name": unique_telemetry["class_name"],
            "radius": TELEMETRY_RADIUS,
        }
        fixture_telemetry = _require_fixture(
            runtime.prepare_vehicle_fixture(client, recorded_position, TELEMETRY_RADIUS)
        )
        fixture = {
            "vehicle_fixture_ready": True,
            "wheel_count": fixture_telemetry["wheel_count"],
            "attachment_count": fixture_telemetry["attachment_count"],
            "wheel_item_count": fixture_telemetry["items"].count(WHEEL_TYPE),
            "items": list(fixture_telemetry["items"]),
        }
        raw_client_settlement = runtime.wait_for_client_peer_settlement(
            client, CLIENT_PEER_SETTLE_TIMEOUT_SECONDS
        )
        if type(raw_client_settlement) is not dict:
            raise StopRun("client_peer_settlement_payload_invalid")
        client_settlement = raw_client_settlement
        if client_settlement.get("ready") is not True:
            settlement_error = str(
                client_settlement.get("error") or "client_peer_not_ready"
            )
            raise StopRun(f"client_peer_settlement_failed:{settlement_error}")
        _capture_views(
            runtime,
            client,
            config,
            ownership,
            recorded_position,
            views,
            camera_results,
        )
        final_snapshot = _hold_owned_processes(config, provider, runtime, ownership, hold)
        artifacts_after = _artifact_observation(config.artifacts)
        if artifacts_after != artifacts_before:
            raise StopRun("artifact_hash_set_changed")
        logs = _snapshot_log_records(config, runtime.collect_logs())
        artifact_evidence = _snapshot_artifact_records(config, artifacts_after)
    except Exception as exc:
        operation_error = str(exc)
    finally:
        if object_id > 0:
            cleanup_attempted = True
            try:
                raw_cleanup = runtime.cleanup(client, object_id)
                if isinstance(raw_cleanup, dict):
                    cleanup_response = raw_cleanup
                else:
                    cleanup_response = {"invalid_payload": raw_cleanup}
                deleted = cleanup_response.get("deleted")
                delete_confirmed = (
                    _dayz_true(cleanup_response.get("ok"))
                    and type(deleted) is int
                    and deleted == 1
                )
                post_delete_telemetry = _require_absent(
                    runtime.telemetry_object_at(client, recorded_position, TELEMETRY_RADIUS),
                    "post_delete",
                )
                cleanup_ok = delete_confirmed
                cleanup_outcome = "DELETED_AND_ABSENT" if cleanup_ok else "DELETE_UNCONFIRMED"
            except Exception as exc:  # cleanup is fail-closed even for an unexpected provider exception
                operation_error = operation_error or f"cleanup_exception:{type(exc).__name__}:{exc}"
                cleanup_ok = False
                cleanup_outcome = "DELETE_EXCEPTION"
            possible_orphan = not cleanup_ok
        else:
            cleanup_ok = False
            if spawn_invoked and spawn_rejected:
                cleanup_outcome = "NOT_REQUIRED_SPAWN_REJECTED"
                possible_orphan = False
            elif spawn_invoked:
                cleanup_outcome = "NOT_ATTEMPTED_NO_OBJECT_ID"
                possible_orphan = True
    if object_id > 0 and not cleanup_ok:
        operation_error = operation_error or "cleanup_failed"

    if operation_error and not logs:
        try:
            logs = _snapshot_log_records(config, runtime.collect_logs())
        except Exception:
            logs = []
    hold_final_summary = hold.get("last_snapshot") or _snapshot_summary(final_snapshot)
    publish_final_summary: dict[str, Any]
    try:
        publish_final_snapshot = provider.snapshot()
        publish_final_summary = _snapshot_summary(publish_final_snapshot)
        publish_ownership = validate_ownership(publish_final_snapshot)
        if not _same_owners(ownership, publish_ownership):
            operation_error = operation_error or (
                f"ownership_changed_before_publication:{publish_ownership.reason}"
            )
    except Exception as exc:
        publish_final_summary = {"error": f"{type(exc).__name__}:{exc}"}
        operation_error = operation_error or f"publish_snapshot_error:{type(exc).__name__}:{exc}"

    cleanup_evidence = {
        "attempted": cleanup_attempted,
        "ok": cleanup_ok,
        "outcome": cleanup_outcome,
        "object_id": object_id,
        "possible_orphan": possible_orphan,
    }
    if cleanup_attempted:
        cleanup_evidence.update(
            {
                "response": cleanup_response,
                "deleted": cleanup_response.get("deleted"),
                "post_delete_absent": _dayz_false(post_delete_telemetry.get("found")),
            }
        )
    spawn_evidence = {"object_id": object_id, "position": recorded_position, "flags": SPAWN_FLAGS}
    if spawn_protocol:
        spawn_evidence.update(
            {
                "command_id": spawn_protocol.get("command_id"),
                "classification": spawn_protocol.get("classification"),
                "server_settlement": spawn_protocol.get("server_settlement"),
                "command_observations": spawn_protocol.get("observations"),
            }
        )
        if spawn_protocol.get("observation_error"):
            spawn_evidence["observation_error"] = spawn_protocol.get(
                "observation_error"
            )
    if operation_error:
        late_frontend_evidence = (
            {"late_frontend_resume": late_frontend_resume}
            if late_frontend_resume is not None
            else {}
        )
        return _publish_stop(
            config,
            operation_error,
            spawn=spawn_evidence,
            cleanup=cleanup_evidence,
            automatic_retry_blocked=possible_orphan,
            readiness=readiness,
            frontend_resume=frontend_resume,
            build_id=BUILD_ID,
            isolation={
                "pre_spawn_absent": _dayz_false(pre_spawn_telemetry.get("found")),
                "position": player_position,
                "radius": TELEMETRY_RADIUS,
            },
            unique_vehicle=unique_vehicle,
            fixture=fixture,
            client_settlement=client_settlement,
            raycast=raycast_result,
            evidence={
                "views": views,
                "camera_results": camera_results,
                "logs": logs,
            },
            hold=hold,
            artifacts=artifact_evidence or artifacts_after or artifacts_before,
            ownership={
                "server_pid": ownership.server_pid,
                "client_pid": ownership.client_pid,
                "daemon_pid": ownership.daemon_pid,
                "initial_snapshot": _snapshot_summary(initial_snapshot),
                "pre_spawn_snapshot": _snapshot_summary(second_snapshot),
                "hold_final_snapshot": hold_final_summary,
                "publish_final_snapshot": publish_final_summary,
            },
            visual_review_contract=dict(VISUAL_REVIEW_CONTRACT),
            **late_frontend_evidence,
        )

    payload = {
        "owner": VERDICT_OWNER,
        "schema": VERDICT_SCHEMA,
        "build_id": BUILD_ID,
        "result": "STOP",
        "model_verdict": "NOT_EVALUATED",
        "collection_status": "COMPLETE",
        "stop_reason": "PENDING_EXPLICIT_VISUAL_REVIEW",
        "spawn": spawn_evidence,
        "readiness": readiness,
        "frontend_resume": frontend_resume,
        "isolation": {
            "pre_spawn_absent": _dayz_false(pre_spawn_telemetry.get("found")),
            "position": player_position,
            "radius": TELEMETRY_RADIUS,
        },
        "unique_vehicle": unique_vehicle,
        "fixture": fixture,
        "client_settlement": client_settlement,
        "raycast": raycast_result,
        "evidence": {
            "views": views,
            "camera_results": camera_results,
            "logs": logs,
        },
        "hold": hold,
        "cleanup": cleanup_evidence,
        "automatic_retry_blocked": False,
        "artifacts": artifact_evidence,
        "ownership": {
            "server_pid": ownership.server_pid,
            "client_pid": ownership.client_pid,
            "daemon_pid": ownership.daemon_pid,
            "initial_snapshot": _snapshot_summary(initial_snapshot),
            "pre_spawn_snapshot": _snapshot_summary(second_snapshot),
            "hold_final_snapshot": hold_final_summary,
            "publish_final_snapshot": publish_final_summary,
        },
        "visual_review_contract": dict(VISUAL_REVIEW_CONTRACT),
    }
    if late_frontend_resume is not None:
        payload["late_frontend_resume"] = late_frontend_resume
    atomic_write_json(config.verdict_path, payload)
    return RunOutcome(EXIT_OK, payload)


def _trusted_log_roots(
    override: Mapping[str, pathlib.Path] | None = None,
) -> dict[str, pathlib.Path]:
    roots: Mapping[str, pathlib.Path] = (
        override
        if override is not None
        else {"server": DEFAULT_SERVER_PROFILES, "client": DEFAULT_CLIENT_PROFILES}
    )
    if set(roots) != {"server", "client"}:
        raise ValueError("trusted_log_roots_must_be_server_and_client")
    return {source: pathlib.Path(path).resolve() for source, path in roots.items()}


def _validate_finalizable(
    payload: dict[str, Any],
    verdict_path: pathlib.Path,
    trusted_log_roots: Mapping[str, pathlib.Path] | None = None,
) -> str:
    resolved_log_roots = _trusted_log_roots(trusted_log_roots)
    resolved_log_snapshot_root = (verdict_path.parent / "evidence" / "logs").resolve()
    resolved_artifact_snapshot_root = (verdict_path.parent / "evidence" / "artifacts").resolve()
    if payload.get("owner") != VERDICT_OWNER or payload.get("schema") != VERDICT_SCHEMA:
        return "verdict_not_owned"
    if payload.get("build_id") != BUILD_ID:
        return "build_id_invariant_failed"
    if (
        payload.get("result") != "STOP"
        or payload.get("model_verdict") != "NOT_EVALUATED"
        or payload.get("collection_status") != "COMPLETE"
    ):
        return "verdict_state_not_finalizable"
    readiness = payload.get("readiness")
    if not isinstance(readiness, dict) or readiness.get("inworld") is not True:
        return "readiness_invariant_failed"
    raycast = payload.get("raycast")
    if not isinstance(raycast, dict):
        return "raycast_invariant_failed"
    try:
        raycast_position = _raycast_position(raycast)
    except StopRun:
        return "raycast_invariant_failed"
    spawn = payload.get("spawn")
    if not isinstance(spawn, dict):
        return "spawn_invariant_failed"
    object_id = spawn.get("object_id")
    if (
        not isinstance(object_id, int)
        or isinstance(object_id, bool)
        or object_id <= 0
        or spawn.get("flags") != SPAWN_FLAGS
    ):
        return "spawn_invariant_failed"
    try:
        if _vec3(spawn.get("position"), "spawn_position") != raycast_position:
            return "spawn_raycast_position_mismatch"
    except StopRun:
        return "spawn_invariant_failed"
    isolation = payload.get("isolation")
    if (
        not isinstance(isolation, dict)
        or isolation.get("pre_spawn_absent") is not True
        or isolation.get("radius") != TELEMETRY_RADIUS
    ):
        return "isolation_invariant_failed"
    try:
        _vec3(isolation.get("position"), "isolation_position")
    except StopRun:
        return "isolation_invariant_failed"
    unique_vehicle = payload.get("unique_vehicle")
    if (
        not isinstance(unique_vehicle, dict)
        or unique_vehicle.get("verified") is not True
        or unique_vehicle.get("type") != OBJECT_TYPE
        or unique_vehicle.get("class_name") != OBJECT_TYPE
        or unique_vehicle.get("radius") != TELEMETRY_RADIUS
    ):
        return "unique_vehicle_invariant_failed"
    fixture = payload.get("fixture")
    if (
        not isinstance(fixture, dict)
        or fixture.get("vehicle_fixture_ready") is not True
        or type(fixture.get("wheel_count")) is not int
        or fixture.get("wheel_count") != 4
        or type(fixture.get("attachment_count")) is not int
        or fixture.get("attachment_count") < 4
        or type(fixture.get("wheel_item_count")) is not int
        or fixture.get("wheel_item_count") != 4
        or not isinstance(fixture.get("items"), list)
        or fixture["items"].count(WHEEL_TYPE) != 4
    ):
        return "fixture_invariant_failed"
    hold = payload.get("hold")
    if not isinstance(hold, dict) or hold.get("clock") != "monotonic":
        return "hold_invariant_failed"
    required_seconds = hold.get("required_seconds")
    elapsed_seconds = hold.get("elapsed_seconds")
    sample_interval_seconds = hold.get("sample_interval_seconds")
    observed_span_seconds = hold.get("observed_span_seconds")
    if (
        not isinstance(required_seconds, (int, float))
        or isinstance(required_seconds, bool)
        or not math.isfinite(required_seconds)
        or required_seconds < 30.0
        or not isinstance(elapsed_seconds, (int, float))
        or isinstance(elapsed_seconds, bool)
        or not math.isfinite(elapsed_seconds)
        or elapsed_seconds < required_seconds
        or not isinstance(sample_interval_seconds, (int, float))
        or isinstance(sample_interval_seconds, bool)
        or not math.isfinite(sample_interval_seconds)
        or sample_interval_seconds <= 0.0
        or not isinstance(observed_span_seconds, (int, float))
        or isinstance(observed_span_seconds, bool)
        or not math.isfinite(observed_span_seconds)
        or observed_span_seconds < required_seconds
    ):
        return "hold_duration_invariant_failed"
    samples = hold.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        return "hold_samples_invariant_failed"
    cleanup = payload.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("attempted") is not True
        or cleanup.get("ok") is not True
        or cleanup.get("outcome") != "DELETED_AND_ABSENT"
        or type(cleanup.get("deleted")) is not int
        or cleanup.get("deleted") != 1
        or cleanup.get("post_delete_absent") is not True
        or not isinstance(cleanup.get("response"), dict)
        or not _dayz_true(cleanup["response"].get("ok"))
        or type(cleanup["response"].get("deleted")) is not int
        or cleanup["response"].get("deleted") != 1
        or cleanup.get("possible_orphan") is not False
        or cleanup.get("object_id") != object_id
        or payload.get("automatic_retry_blocked") is not False
        or payload.get("automatic_retry_blocked") is not cleanup.get("possible_orphan")
    ):
        return "cleanup_invariant_failed"
    ownership = payload.get("ownership")
    if not isinstance(ownership, dict):
        return "ownership_invariant_failed"
    expected_pids = (
        ownership.get("server_pid"),
        ownership.get("client_pid"),
        ownership.get("daemon_pid"),
    )
    if any(not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 for pid in expected_pids):
        return "ownership_pid_invariant_failed"
    sample_times: list[float] = []
    snapshot_starts: list[float] = []
    snapshot_durations: list[float] = []
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("ok") is not True:
            return "hold_sample_not_owned"
        sample_time = sample.get("elapsed_seconds")
        snapshot_started = sample.get("snapshot_started_elapsed")
        snapshot_duration = sample.get("snapshot_duration_seconds")
        if (
            not isinstance(sample_time, (int, float))
            or isinstance(sample_time, bool)
            or not math.isfinite(sample_time)
            or sample_time < 0.0
            or not isinstance(snapshot_started, (int, float))
            or isinstance(snapshot_started, bool)
            or not math.isfinite(snapshot_started)
            or snapshot_started < 0.0
            or not isinstance(snapshot_duration, (int, float))
            or isinstance(snapshot_duration, bool)
            or not math.isfinite(snapshot_duration)
            or snapshot_duration < 0.0
            or snapshot_duration > sample_time
            or abs((sample_time - snapshot_started) - snapshot_duration)
            > HOLD_TIMING_ROUNDING_TOLERANCE_SECONDS
        ):
            return "hold_sample_time_invalid"
        if sample_times and sample_time < sample_times[-1]:
            return "hold_sample_time_decreasing"
        sample_times.append(float(sample_time))
        snapshot_starts.append(float(snapshot_started))
        snapshot_durations.append(float(snapshot_duration))
        if (
            sample.get("server_pid"),
            sample.get("client_pid"),
            sample.get("daemon_pid"),
        ) != expected_pids:
            return "hold_sample_pid_mismatch"
    calculated_observed_span = sample_times[-1] - sample_times[0]
    if (
        snapshot_starts[0]
        > HOLD_SCHEDULER_JITTER_SECONDS + HOLD_TIMING_ROUNDING_TOLERANCE_SECONDS
        or calculated_observed_span < required_seconds
        or abs(float(observed_span_seconds) - calculated_observed_span)
        > HOLD_TIMING_ROUNDING_TOLERANCE_SECONDS
        or sample_times[-1] > elapsed_seconds
    ):
        return "hold_sample_boundary_invalid"
    for index, (earlier, later) in enumerate(zip(sample_times, sample_times[1:]), start=1):
        gap = later - earlier
        maximum_gap = (
            float(sample_interval_seconds)
            + snapshot_durations[index]
            + HOLD_SCHEDULER_JITTER_SECONDS
            + HOLD_TIMING_ROUNDING_TOLERANCE_SECONDS
        )
        if gap <= 0.0 or gap > maximum_gap:
            return "hold_sample_cadence_invalid"
    for name in (
        "initial_snapshot",
        "pre_spawn_snapshot",
        "hold_final_snapshot",
        "publish_final_snapshot",
    ):
        summary = ownership.get(name)
        if not isinstance(summary, dict):
            return f"ownership_snapshot_missing:{name}"
        try:
            snapshot = OwnershipSnapshot(
                processes=tuple(
                    ProcessRecord(
                        pid=int(process["pid"]),
                        name=str(process["name"]),
                        command_line=str(process["command_line"]),
                    )
                    for process in summary["processes"]
                ),
                ports=tuple(
                    PortBinding(
                        protocol=str(binding["protocol"]),
                        local_port=int(binding["local_port"]),
                        pid=int(binding["pid"]),
                    )
                    for binding in summary["ports"]
                ),
            )
        except (KeyError, TypeError, ValueError):
            return f"ownership_snapshot_invalid:{name}"
        observed = validate_ownership(snapshot)
        if not observed.ok or (
            observed.server_pid,
            observed.client_pid,
            observed.daemon_pid,
        ) != expected_pids:
            return f"ownership_snapshot_mismatch:{name}"
    if payload.get("visual_review_contract") != VISUAL_REVIEW_CONTRACT:
        return "visual_review_contract_invariant_failed"
    evidence_section = payload.get("evidence")
    if not isinstance(evidence_section, dict):
        return "evidence_invariant_failed"
    views = evidence_section.get("views") or {}
    if not isinstance(views, dict) or set(views) != set(VIEW_SPECS):
        return "eight_view_evidence_incomplete"
    camera_results = evidence_section.get("camera_results") or {}
    if not isinstance(camera_results, dict) or set(camera_results) != set(VIEW_SPECS):
        return "camera_evidence_incomplete"
    try:
        spawn_position = _vec3((payload.get("spawn") or {}).get("position"), "spawn_position")
    except StopRun:
        return "camera_evidence_spawn_position_invalid"
    observed_view_hashes: set[str] = set()
    for view, specification in VIEW_SPECS.items():
        evidence = views.get(view)
        if not isinstance(evidence, dict):
            return f"eight_view_evidence_invalid:{view}"
        camera_failure = _camera_contract_failure(
            camera_results.get(view),
            _add(spawn_position, specification["offset"]),
        )
        if camera_failure:
            return f"camera_evidence_invalid:{view}:{camera_failure}"
        path = pathlib.Path(str(evidence.get("path") or ""))
        if (
            evidence.get("format") != "JPEG"
            or path.suffix.casefold() not in {".jpg", ".jpeg"}
            or not path.is_file()
            or not _is_jpeg_file(path)
            or sha256_file(path) != evidence.get("sha256")
        ):
            return f"view_hash_drift:{view}"
        if evidence.get("visual_gate") != specification["visual_gate"]:
            return f"view_gate_mismatch:{view}"
        recorded_view_hash = str(evidence.get("sha256") or "")
        if recorded_view_hash in observed_view_hashes:
            return f"view_hash_duplicate:{view}"
        observed_view_hashes.add(recorded_view_hash)
    logs = evidence_section.get("logs")
    if not isinstance(logs, list) or not logs:
        return "log_evidence_incomplete"
    expected_log_sources = {"server", "client"}
    observed_log_sources: set[str] = set()
    observed_log_paths: set[pathlib.Path] = set()
    for record in logs:
        if not isinstance(record, dict):
            return "log_evidence_invalid"
        source = record.get("source")
        profile_value = record.get("profile_path")
        live_path_value = record.get("live_path")
        path_value = record.get("path")
        recorded_hash = record.get("sha256")
        if (
            source not in expected_log_sources
            or not isinstance(profile_value, str)
            or not profile_value
            or not isinstance(live_path_value, str)
            or not live_path_value
            or not isinstance(path_value, str)
            or not path_value
            or not isinstance(recorded_hash, str)
        ):
            return "log_evidence_invalid"
        profile_path = pathlib.Path(profile_value).resolve()
        live_path = pathlib.Path(live_path_value).resolve()
        log_path = pathlib.Path(path_value).resolve()
        if log_path in observed_log_paths:
            return "log_snapshot_path_reused"
        if (
            profile_path != resolved_log_roots[source]
            or not profile_path.is_dir()
            or not live_path.is_file()
            or not live_path.is_relative_to(profile_path)
            or not log_path.is_file()
            or not log_path.is_relative_to(resolved_log_snapshot_root)
            or sha256_file(log_path) != recorded_hash
        ):
            return f"log_hash_drift:{source}"
        observed_log_paths.add(log_path)
        observed_log_sources.add(source)
    if observed_log_sources != expected_log_sources:
        return "log_role_evidence_incomplete"
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"host_p3d", "pbo"}:
        return "artifact_evidence_incomplete"
    observed_artifact_paths: set[pathlib.Path] = set()
    for name, evidence in artifacts.items():
        if not isinstance(evidence, dict):
            return f"artifact_evidence_invalid:{name}"
        path_value = evidence.get("path")
        live_path_value = evidence.get("live_path")
        expected = evidence.get("expected_sha256")
        recorded = evidence.get("sha256")
        if (
            not isinstance(path_value, str)
            or not path_value
            or not isinstance(live_path_value, str)
            or not live_path_value
            or not isinstance(expected, str)
            or not isinstance(recorded, str)
        ):
            return f"artifact_evidence_invalid:{name}"
        path = pathlib.Path(path_value).resolve()
        if path in observed_artifact_paths:
            return "artifact_snapshot_path_reused"
        if (
            not path.is_file()
            or not path.is_relative_to(resolved_artifact_snapshot_root / name)
            or recorded != expected
            or sha256_file(path) != recorded
        ):
            return f"artifact_hash_drift:{name}"
        observed_artifact_paths.add(path)
    return ""


def finalize(
    verdict_path: pathlib.Path,
    decision: str,
    trusted_log_roots: Mapping[str, pathlib.Path] | None = None,
) -> RunOutcome:
    decision = decision.upper()
    if decision not in {"PASS", "FALSIFIED"}:
        return RunOutcome(EXIT_USAGE, _stop_payload("decision_must_be_PASS_or_FALSIFIED"))
    try:
        payload = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return RunOutcome(EXIT_STOP, _stop_payload(f"malformed_verdict:{exc}"))
    if not isinstance(payload, dict):
        return RunOutcome(EXIT_STOP, _stop_payload("malformed_verdict:not_object"))
    try:
        error = _validate_finalizable(payload, verdict_path, trusted_log_roots)
    except (OSError, ValueError, TypeError) as exc:
        error = f"finalize_validation_error:{exc}"
    if error:
        return RunOutcome(EXIT_STOP, _stop_payload(error))
    payload["result"] = decision
    payload["model_verdict"] = decision
    payload["collection_status"] = "FINALIZED"
    payload["stop_reason"] = None
    payload["finalization"] = {
        "explicit": True,
        "decision": decision,
        "eight_views_verified": True,
        "hashes_unchanged": True,
    }
    if decision == "FALSIFIED":
        payload["rollback"] = {
            "decision": "SELECTED",
            "action": "CONTROLLER_MUST_APPLY_DOCUMENTED_ROLLBACK",
            "files_modified": False,
        }
    else:
        payload["rollback"] = {"decision": "NOT_SELECTED", "files_modified": False}
    atomic_write_json(verdict_path, payload)
    return RunOutcome(EXIT_OK, payload)


def _task9_menu_red_line_present(image: Image.Image) -> bool | None:
    x0 = int(image.width * 0.72)
    x1 = int(image.width * 0.96)
    y0 = int(image.height * 0.70)
    y1 = int(image.height * 0.90)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None

    roi = image.convert("RGB").crop((x0, y0, x1, y1))
    try:
        pixels = roi.load()
        minimum_run = math.ceil(roi.width * 0.80)
        previous_row_matches = False
        for y in range(roi.height):
            longest_run = 0
            current_run = 0
            for x in range(roi.width):
                red, green, blue = pixels[x, y]
                if red >= 180 and green <= 45 and blue <= 45:
                    current_run += 1
                    longest_run = max(longest_run, current_run)
                else:
                    current_run = 0
            row_matches = longest_run >= minimum_run
            if previous_row_matches and row_matches:
                return True
            previous_row_matches = row_matches
        return False
    finally:
        roi.close()


def _inspect_task9_continue_overlay(
    path: pathlib.Path,
    capture: object,
    client_pid: int,
    mcp_capture: object,
    expected_method: str = "foreground",
) -> dict[str, Any]:
    invalid = {"ok": False, "detected": False}
    if type(capture) is not dict or capture.get("ok") is not True:
        return {**invalid, "reason": "continue_capture_failed"}
    if capture.get("method") != expected_method:
        return {**invalid, "reason": f"continue_capture_not_{expected_method}"}

    window = capture.get("window")
    client = capture.get("client")
    client_stats = capture.get("clientStats")
    if not (
        type(window) is dict
        and set(window) == {"pid", "class", "title", "left", "top", "width", "height"}
        and all(type(window[key]) is int for key in ("pid", "left", "top", "width", "height"))
        and window["pid"] == client_pid
        and window["class"] == "DayZ"
        and window["title"] == "DayZ"
        and window["width"] > 0
        and window["height"] > 0
    ):
        return {**invalid, "reason": "continue_window_identity_invalid"}
    if not (
        type(client) is dict
        and set(client) == {"left", "top", "width", "height"}
        and all(type(client[key]) is int for key in client)
        and client["left"] >= 0
        and client["top"] >= 0
        and (client["width"], client["height"])
        in TASK9_CONTINUE_CLIENT_GEOMETRIES
        and client["left"] + client["width"] <= window["width"]
        and client["top"] + client["height"] <= window["height"]
    ):
        return {**invalid, "reason": "continue_client_geometry_invalid"}
    if not (
        type(client_stats) is dict
        and set(client_stats) == {"meanBrightness", "nonBlackRatio"}
        and type(client_stats["meanBrightness"]) in {int, float}
        and type(client_stats["nonBlackRatio"]) in {int, float}
        and math.isfinite(client_stats["meanBrightness"])
        and math.isfinite(client_stats["nonBlackRatio"])
    ):
        return {**invalid, "reason": "continue_client_stats_invalid"}
    if not (
        client_stats["meanBrightness"] > 1.0
        and client_stats["nonBlackRatio"] > 0.10
    ):
        return {
            **invalid,
            "reason": "continue_client_stats_invalid",
            "client_pid": client_pid,
            "window": dict(window),
        }

    recorded_hash = capture.get("sha256")
    if type(recorded_hash) is not str or re.fullmatch(r"[0-9A-Fa-f]{64}", recorded_hash) is None:
        return {**invalid, "reason": "continue_capture_hash_invalid"}
    try:
        resolved_path = path.resolve(strict=True)
        if not resolved_path.is_file() or sha256_file(resolved_path) != recorded_hash.upper():
            return {**invalid, "reason": "continue_capture_hash_mismatch"}
        image = mcp_capture.load_rgb(str(resolved_path))
    except (OSError, RuntimeError):
        return {**invalid, "reason": "continue_capture_unreadable"}

    try:
        if image.size != (window["width"], window["height"]):
            return {**invalid, "reason": "continue_capture_geometry_mismatch"}
        red_line = _task9_menu_red_line_present(image)
        if red_line is None:
            return {**invalid, "reason": "continue_overlay_unclassifiable"}
        if red_line is False:
            return {"ok": True, "detected": False, "reason": "continue_overlay_absent"}

        button_box = (
            round(image.width * (1430 / 1942)),
            round(image.height * (904 / 1136)),
            round(image.width * (1832 / 1942)),
            round(image.height * (996 / 1136)),
        )
        label_box = (
            round(image.width * (1470 / 1942)),
            round(image.height * (925 / 1136)),
            round(image.width * (1797 / 1942)),
            round(image.height * (982 / 1136)),
        )
        button = image.crop(button_box).convert("RGB")
        label = image.crop(label_box).convert("RGB")
        try:
            button_pixels = button.load()
            label_pixels = label.load()
            button_count = button.width * button.height
            label_count = label.width * label.height
            button_dark = sum(
                1
                for y in range(button.height)
                for x in range(button.width)
                if max(button_pixels[x, y]) <= 85
            )
            button_white = sum(
                1
                for y in range(button.height)
                for x in range(button.width)
                if min(button_pixels[x, y]) >= 180
                and max(button_pixels[x, y]) - min(button_pixels[x, y]) <= 30
            )
            label_white = sum(
                1
                for y in range(label.height)
                for x in range(label.width)
                if min(label_pixels[x, y]) >= 180
                and max(label_pixels[x, y]) - min(label_pixels[x, y]) <= 30
            )
        finally:
            button.close()
            label.close()
    finally:
        image.close()

    button_dark_ratio = button_dark / button_count
    button_white_ratio = button_white / button_count
    label_white_ratio = label_white / label_count
    if not (
        button_dark_ratio >= 0.80
        and 0.04 <= button_white_ratio <= 0.25
        and 0.08 <= label_white_ratio <= 0.45
    ):
        return {
            "ok": False,
            "detected": True,
            "reason": "continue_overlay_signature_partial",
            "button_dark_ratio": button_dark_ratio,
            "button_white_ratio": button_white_ratio,
            "label_white_ratio": label_white_ratio,
        }

    click_window_x = client["left"] + round(
        client["width"] * TASK9_CONTINUE_CLICK_X_FRACTION
    )
    click_window_y = client["top"] + round(
        client["height"] * TASK9_CONTINUE_CLICK_Y_FRACTION
    )
    return {
        "ok": True,
        "detected": True,
        "reason": "continue_overlay_exact",
        "client_pid": client_pid,
        "window": dict(window),
        "path": str(resolved_path),
        "sha256": recorded_hash.upper(),
        "click_screen": [
            window["left"] + click_window_x,
            window["top"] + click_window_y,
        ],
        "button_dark_ratio": button_dark_ratio,
        "button_white_ratio": button_white_ratio,
        "label_white_ratio": label_white_ratio,
    }


def _run_task9_per_monitor_dpi(
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    if os.name != "nt":
        return operation()

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous_context = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-3))
    if not previous_context:
        return {
            "ok": False,
            "attempted": False,
            "reason": "dpi_context_unavailable",
        }

    result: dict[str, Any] | None = None
    restore_ok = False
    try:
        result = operation()
    finally:
        restore_ok = bool(
            user32.SetThreadDpiAwarenessContext(previous_context)
        )

    if not restore_ok:
        payload: dict[str, Any] = {
            "ok": False,
            "attempted": bool(result and result.get("attempted") is True),
            "reason": "dpi_context_restore_failed",
        }
        if result and type(result.get("reason")) is str:
            payload["operation_reason"] = result["reason"]
        return payload
    if result is None:
        return {
            "ok": False,
            "attempted": False,
            "reason": "dpi_operation_result_missing",
        }
    return result


def _confirm_task9_owned_foreground(
    user32: Any,
    target_hwnd: int,
    client_pid: int,
    expected_rect: tuple[int, int, int, int],
    window_pid: Callable[[int], tuple[int, int]],
    window_rect: Callable[[int], tuple[int, int, int, int] | None],
) -> dict[str, Any]:
    observed_pid = 0
    for sample in range(TASK9_FOREGROUND_CONFIRM_SAMPLES):
        observed_hwnd = int(user32.GetForegroundWindow() or 0)
        _, observed_pid = window_pid(observed_hwnd)
        if (
            observed_hwnd == target_hwnd
            and observed_pid == client_pid
            and window_rect(target_hwnd) == expected_rect
        ):
            return {
                "ok": True,
                "reason": "foreground_confirmed",
                "foreground_pid": observed_pid,
            }
        if (
            observed_hwnd != 0
            or window_rect(target_hwnd) != expected_rect
            or sample == TASK9_FOREGROUND_CONFIRM_SAMPLES - 1
        ):
            return {
                "ok": False,
                "reason": "foreground_activation_not_confirmed",
                "foreground_pid": observed_pid,
            }
        time.sleep(TASK9_FOREGROUND_CONFIRM_INTERVAL_S)
    raise AssertionError("foreground confirmation loop exhausted unexpectedly")


def _activate_task9_owned_window_in_current_dpi_context(
    client_pid: int,
    expected_window: dict[str, Any],
    toggle_presentation: bool = False,
) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "ok": False,
            "attempted": False,
            "reason": "win32_activation_unavailable",
        }
    if not (
        type(client_pid) is int
        and client_pid > 0
        and type(toggle_presentation) is bool
        and type(expected_window) is dict
        and set(expected_window)
        == {"pid", "class", "title", "left", "top", "width", "height"}
        and all(
            type(expected_window[key]) is int
            for key in ("pid", "left", "top", "width", "height")
        )
        and type(expected_window["class"]) is str
        and type(expected_window["title"]) is str
        and expected_window["pid"] == client_pid
        and expected_window["class"] == "DayZ"
        and expected_window["title"] == "DayZ"
        and expected_window["width"] > 0
        and expected_window["height"] > 0
    ):
        return {
            "ok": False,
            "attempted": False,
            "reason": "activation_arguments_invalid",
        }

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    window_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [window_enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.BOOL,
    ]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    if toggle_presentation:
        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class KeyboardInputUnion(ctypes.Union):
            _fields_ = [("mi", MouseInput), ("ki", KeyboardInput)]

        class KeyboardInputEvent(ctypes.Structure):
            _anonymous_ = ("data",)
            _fields_ = [
                ("type", wintypes.DWORD),
                ("data", KeyboardInputUnion),
            ]

        user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(KeyboardInputEvent),
            ctypes.c_int,
        ]
        user32.SendInput.restype = wintypes.UINT

    def window_pid(hwnd: int) -> tuple[int, int]:
        if not hwnd:
            return 0, 0
        pid = wintypes.DWORD()
        thread_id = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))
        if thread_id == 0:
            return 0, 0
        return thread_id, int(pid.value)

    def window_text(hwnd: int, getter: object) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        if getter(hwnd, buffer, len(buffer)) <= 0:  # type: ignore[operator]
            return ""
        return str(buffer.value)

    def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (
            int(rect.left),
            int(rect.top),
            int(rect.right - rect.left),
            int(rect.bottom - rect.top),
        )

    expected_rect = (
        expected_window["left"],
        expected_window["top"],
        expected_window["width"],
        expected_window["height"],
    )
    matches: list[int] = []

    @window_enum_proc
    def enum_window(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        _, pid = window_pid(hwnd)
        if pid != client_pid:
            return True
        if window_text(hwnd, user32.GetClassNameW) != expected_window["class"]:
            return True
        if window_text(hwnd, user32.GetWindowTextW) != expected_window["title"]:
            return True
        if window_rect(hwnd) != expected_rect:
            return True
        matches.append(int(hwnd))
        return True

    if not user32.EnumWindows(enum_window, 0):
        return {
            "ok": False,
            "attempted": False,
            "reason": "owned_window_enumeration_failed",
        }
    if len(matches) == 0:
        return {
            "ok": False,
            "attempted": False,
            "reason": "owned_window_match_missing",
        }
    if len(matches) != 1:
        return {
            "ok": False,
            "attempted": False,
            "reason": "owned_window_match_ambiguous",
            "match_count": len(matches),
        }

    target_hwnd = matches[0]
    foreground_hwnd = int(user32.GetForegroundWindow() or 0)
    foreground_thread, _ = window_pid(foreground_hwnd)
    current_thread = int(kernel32.GetCurrentThreadId())
    attached = False
    if foreground_thread and foreground_thread != current_thread:
        if not user32.AttachThreadInput(current_thread, foreground_thread, True):
            return {
                "ok": False,
                "attempted": False,
                "reason": "foreground_attach_failed",
            }
        attached = True

    activation_ok = False
    activated_rect: tuple[int, int, int, int] | None = None
    detach_ok = True
    try:
        # The DayZ client can restore a persisted window placement outside the
        # visible desktop while still reporting a valid foreground HWND.  Move
        # the exact owned HWND to the primary origin without changing its
        # validated size, sending input, or touching any other process.
        user32.ShowWindow(target_hwnd, 9)
        refresh_ok = bool(
            user32.SetWindowPos(
                target_hwnd,
                0,
                0,
                0,
                max(1, expected_rect[2] - 1),
                max(1, expected_rect[3] - 1),
                0x0004 | 0x0040,
            )
        )
        position_ok = bool(
            user32.SetWindowPos(
                target_hwnd,
                0,
                0,
                0,
                expected_rect[2],
                expected_rect[3],
                0x0004 | 0x0040,
            )
        )
        top_ok = bool(user32.BringWindowToTop(target_hwnd))
        foreground_ok = bool(user32.SetForegroundWindow(target_hwnd))
        activated_rect = window_rect(target_hwnd)
        activation_ok = (
            refresh_ok
            and position_ok
            and top_ok
            and foreground_ok
            and activated_rect is not None
            and activated_rect == (0, 0, expected_rect[2], expected_rect[3])
        )
    finally:
        if attached:
            detach_ok = bool(
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            )

    if not activation_ok:
        return {
            "ok": False,
            "attempted": True,
            "reason": "foreground_activation_failed",
        }
    if not detach_ok:
        return {
            "ok": False,
            "attempted": True,
            "reason": "foreground_detach_failed",
        }

    confirmation = _confirm_task9_owned_foreground(
        user32,
        target_hwnd,
        client_pid,
        activated_rect,
        window_pid,
        window_rect,
    )
    if confirmation["ok"] is not True:
        return {
            "ok": False,
            "attempted": True,
            "reason": confirmation["reason"],
            "foreground_pid": confirmation["foreground_pid"],
        }

    presentation_toggle: dict[str, Any] | None = None
    if toggle_presentation:
        observed_hwnd = int(user32.GetForegroundWindow() or 0)
        _, observed_pid = window_pid(observed_hwnd)
        if observed_hwnd != target_hwnd or observed_pid != client_pid:
            return {
                "ok": False,
                "attempted": False,
                "reason": "foreground_window_changed_before_presentation_toggle",
                "foreground_pid": observed_pid,
            }
        if window_rect(target_hwnd) != activated_rect:
            return {
                "ok": False,
                "attempted": False,
                "reason": "owned_window_geometry_changed_before_presentation_toggle",
                "foreground_pid": observed_pid,
            }

        key_events = (KeyboardInputEvent * 4)()
        for index, (virtual_key, flags) in enumerate(
            ((0x12, 0), (0x0D, 0), (0x0D, 0x0002), (0x12, 0x0002))
        ):
            key_events[index].type = 1
            key_events[index].ki = KeyboardInput(virtual_key, 0, flags, 0, 0)

        sent = -1
        release_sent = 0
        try:
            sent = int(
                user32.SendInput(
                    4, key_events, ctypes.sizeof(KeyboardInputEvent)
                )
            )
        finally:
            if sent != 4:
                release_events = (KeyboardInputEvent * 2)()
                release_events[0].type = 1
                release_events[0].ki = KeyboardInput(0x0D, 0, 0x0002, 0, 0)
                release_events[1].type = 1
                release_events[1].ki = KeyboardInput(0x12, 0, 0x0002, 0, 0)
                release_sent = int(
                    user32.SendInput(
                        2, release_events, ctypes.sizeof(KeyboardInputEvent)
                    )
                )
        if sent != 4:
            return {
                "ok": False,
                "attempted": True,
                "reason": "presentation_toggle_input_partial",
                "foreground_pid": observed_pid,
                "events_sent": sent,
                "release_events_sent": release_sent,
            }
        time.sleep(0.15)
        post_hwnd = int(user32.GetForegroundWindow() or 0)
        _, post_pid = window_pid(post_hwnd)
        if post_hwnd != target_hwnd or post_pid != client_pid:
            return {
                "ok": False,
                "attempted": True,
                "reason": "foreground_window_changed_after_presentation_toggle",
                "foreground_pid": post_pid,
                "events_sent": sent,
            }
        presentation_toggle = {
            "events_sent": sent,
            "keys": ["ALT_DOWN", "ENTER_DOWN", "ENTER_UP", "ALT_UP"],
        }

    result = {
        "ok": True,
        "attempted": True,
        "reason": (
            "foreground_repositioned_and_presentation_toggled"
            if toggle_presentation
            else "foreground_repositioned"
        ),
        "foreground_pid": confirmation["foreground_pid"],
        "original_window_rect": list(expected_rect),
        "activated_window_rect": list(activated_rect),
    }
    if presentation_toggle is not None:
        result["presentation_toggle"] = presentation_toggle
    return result


def _activate_task9_owned_window(
    client_pid: int,
    expected_window: dict[str, Any],
    toggle_presentation: bool = False,
) -> dict[str, Any]:
    return _run_task9_per_monitor_dpi(
        lambda: _activate_task9_owned_window_in_current_dpi_context(
            client_pid, expected_window, toggle_presentation
        )
    )


def _send_task9_owned_click_in_current_dpi_context(
    client_pid: int,
    screen_x: int,
    screen_y: int,
    expected_window: dict[str, Any],
) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "attempted": False, "reason": "win32_input_unavailable"}
    if not (
        type(client_pid) is int
        and client_pid > 0
        and type(screen_x) is int
        and type(screen_y) is int
        and type(expected_window) is dict
        and set(expected_window)
        == {"pid", "class", "title", "left", "top", "width", "height"}
        and all(
            type(expected_window[key]) is int
            for key in ("pid", "left", "top", "width", "height")
        )
        and type(expected_window["class"]) is str
        and type(expected_window["title"]) is str
        and expected_window["pid"] == client_pid
        and expected_window["class"] == "DayZ"
        and expected_window["title"] == "DayZ"
        and expected_window["width"] > 0
        and expected_window["height"] > 0
    ):
        return {"ok": False, "attempted": False, "reason": "click_arguments_invalid"}

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class InputUnion(ctypes.Union):
        _fields_ = [("mi", MouseInput)]

    class Input(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("type", wintypes.DWORD), ("data", InputUnion)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    window_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [window_enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT

    def window_pid(hwnd: int) -> tuple[int, int]:
        if not hwnd:
            return 0, 0
        pid = wintypes.DWORD()
        thread_id = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))
        if thread_id == 0:
            return 0, 0
        return thread_id, int(pid.value)

    def window_text(hwnd: int, getter: object) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        if getter(hwnd, buffer, len(buffer)) <= 0:  # type: ignore[operator]
            return ""
        return str(buffer.value)

    def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (
            int(rect.left),
            int(rect.top),
            int(rect.right - rect.left),
            int(rect.bottom - rect.top),
        )

    expected_rect = (
        expected_window["left"],
        expected_window["top"],
        expected_window["width"],
        expected_window["height"],
    )
    matches: list[int] = []

    @window_enum_proc
    def enum_window(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        _, pid = window_pid(hwnd)
        if pid != client_pid:
            return True
        if window_text(hwnd, user32.GetClassNameW) != expected_window["class"]:
            return True
        if window_text(hwnd, user32.GetWindowTextW) != expected_window["title"]:
            return True
        if window_rect(hwnd) != expected_rect:
            return True
        matches.append(int(hwnd))
        return True

    if not user32.EnumWindows(enum_window, 0):
        return {
            "ok": False,
            "attempted": False,
            "reason": "owned_window_enumeration_failed",
        }
    if len(matches) == 0:
        return {
            "ok": False,
            "attempted": False,
            "reason": "owned_window_match_missing",
        }
    if len(matches) != 1:
        return {
            "ok": False,
            "attempted": False,
            "reason": "owned_window_match_ambiguous",
            "match_count": len(matches),
        }

    target_hwnd = matches[0]
    foreground_hwnd = int(user32.GetForegroundWindow() or 0)
    foreground_thread, _ = window_pid(foreground_hwnd)
    current_thread = int(kernel32.GetCurrentThreadId())
    attached = False
    if foreground_thread and foreground_thread != current_thread:
        if not user32.AttachThreadInput(current_thread, foreground_thread, True):
            return {
                "ok": False,
                "attempted": False,
                "reason": "foreground_attach_failed",
            }
        attached = True

    activation_ok = False
    detach_ok = True
    try:
        user32.ShowWindow(target_hwnd, 9)
        top_ok = bool(user32.BringWindowToTop(target_hwnd))
        foreground_ok = bool(user32.SetForegroundWindow(target_hwnd))
        activation_ok = top_ok and foreground_ok
    finally:
        if attached:
            detach_ok = bool(
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            )

    if not activation_ok:
        return {
            "ok": False,
            "attempted": False,
            "reason": "foreground_activation_failed",
        }
    if not detach_ok:
        return {
            "ok": False,
            "attempted": False,
            "reason": "foreground_detach_failed",
        }

    confirmation = _confirm_task9_owned_foreground(
        user32,
        target_hwnd,
        client_pid,
        expected_rect,
        window_pid,
        window_rect,
    )
    if confirmation["ok"] is not True:
        return {
            "ok": False,
            "attempted": False,
            "reason": confirmation["reason"],
            "foreground_pid": confirmation["foreground_pid"],
        }

    original = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(original)):
        return {"ok": False, "attempted": False, "reason": "cursor_position_unavailable"}
    if not user32.SetCursorPos(screen_x, screen_y):
        return {"ok": False, "attempted": False, "reason": "cursor_move_failed"}

    restore_ok = False
    sent = 0
    try:
        observed_hwnd = int(user32.GetForegroundWindow() or 0)
        _, observed_pid = window_pid(observed_hwnd)
        if observed_hwnd != target_hwnd or observed_pid != client_pid:
            return {
                "ok": False,
                "attempted": False,
                "reason": "foreground_window_changed_before_input",
                "foreground_pid": observed_pid,
            }
        if window_rect(target_hwnd) != expected_rect:
            return {
                "ok": False,
                "attempted": False,
                "reason": "owned_window_geometry_changed_before_input",
                "foreground_pid": observed_pid,
            }
        inputs = (Input * 2)()
        inputs[0].type = 0
        inputs[0].mi = MouseInput(0, 0, 0, 0x0002, 0, 0)
        inputs[1].type = 0
        inputs[1].mi = MouseInput(0, 0, 0, 0x0004, 0, 0)
        sent = int(user32.SendInput(2, inputs, ctypes.sizeof(Input)))
        time.sleep(0.15)
    finally:
        restore_ok = bool(user32.SetCursorPos(original.x, original.y))

    if sent != 2:
        return {
            "ok": False,
            "attempted": True,
            "reason": "send_input_partial",
            "foreground_pid": observed_pid,
            "events_sent": sent,
            "cursor_restored": restore_ok,
        }
    if not restore_ok:
        return {
            "ok": False,
            "attempted": True,
            "reason": "cursor_restore_failed_after_click",
            "foreground_pid": observed_pid,
            "events_sent": sent,
            "cursor_restored": False,
        }
    return {
        "ok": True,
        "attempted": True,
        "reason": "continue_clicked",
        "foreground_pid": observed_pid,
        "events_sent": sent,
        "cursor_restored": True,
    }


def _send_task9_owned_click(
    client_pid: int,
    screen_x: int,
    screen_y: int,
    expected_window: dict[str, Any],
) -> dict[str, Any]:
    return _run_task9_per_monitor_dpi(
        lambda: _send_task9_owned_click_in_current_dpi_context(
            client_pid, screen_x, screen_y, expected_window
        )
    )


def _recover_task9_bright_readiness(
    readiness: object, mcp_capture: object
) -> object:
    if type(readiness) is not dict or readiness.get("inworld") is not False:
        return readiness

    thresholds = readiness.get("thresholds")
    if type(thresholds) is not dict or set(thresholds) != {
        "min_settle_s",
        "stability_max",
        "stable_samples",
        "menu_max_mean",
        "nonblack_min",
    }:
        return readiness
    if not (
        type(thresholds["min_settle_s"]) is float
        and thresholds["min_settle_s"] == 20.0
        and type(thresholds["stability_max"]) is float
        and thresholds["stability_max"] == 0.02
        and type(thresholds["stable_samples"]) is int
        and thresholds["stable_samples"] == 2
        and type(thresholds["menu_max_mean"]) is float
        and thresholds["menu_max_mean"] == 86.0
        and type(thresholds["nonblack_min"]) is float
        and thresholds["nonblack_min"] == 0.10
    ):
        return readiness

    elapsed_s = readiness.get("elapsed_s")
    last_mean = readiness.get("last_mean")
    last_nonblack = readiness.get("last_nonblack")
    if not (
        type(elapsed_s) is float
        and math.isfinite(elapsed_s)
        and elapsed_s >= 20.0
        and type(last_mean) is float
        and math.isfinite(last_mean)
        and last_mean > 86.0
        and type(last_nonblack) is float
        and math.isfinite(last_nonblack)
        and last_nonblack >= 0.10
    ):
        return readiness

    deltas = readiness.get("inter_sample_deltas")
    if type(deltas) is not list or len(deltas) < 2:
        return readiness
    if not all(
        type(delta) is float and math.isfinite(delta) and delta >= 0.0
        for delta in deltas
    ):
        return readiness
    if not all(delta < 0.02 for delta in deltas[-2:]):
        return readiness

    last_burst = readiness.get("last_burst")
    if type(last_burst) is not dict or last_burst.get("ok") is not True:
        return readiness
    last_path = last_burst.get("last_path")
    grabs = last_burst.get("grabs")
    if type(grabs) is not list or not grabs:
        return readiness
    successful_grabs: list[dict[str, Any]] = []
    for grab in grabs:
        if type(grab) is not dict or type(grab.get("ok")) is not bool:
            return readiness
        if grab["ok"]:
            successful_grabs.append(grab)
    if not successful_grabs:
        return readiness
    selected_grab = successful_grabs[-1]
    client = selected_grab.get("client")
    client_stats = selected_grab.get("clientStats")
    if not (
        type(client) is dict
        and set(client) == {"left", "top", "width", "height"}
        and all(type(client[key]) is int for key in client)
        and client["left"] >= 0
        and client["top"] >= 0
        and client["width"] > 0
        and client["height"] > 0
        and type(client_stats) is dict
        and set(client_stats) == {"meanBrightness", "nonBlackRatio"}
    ):
        return readiness
    client_mean = client_stats["meanBrightness"]
    client_nonblack = client_stats["nonBlackRatio"]
    if not (
        type(client_mean) in {int, float}
        and math.isfinite(client_mean)
        and type(client_nonblack) in {int, float}
        and math.isfinite(client_nonblack)
    ):
        return readiness
    if client_mean <= 1.0 and client_nonblack <= 0.01:
        return readiness
    recorded_hash = selected_grab.get("sha256")
    if type(recorded_hash) is not str:
        return readiness
    temp_root_raw = os.environ.get("TEMP")
    if type(last_path) is not str or type(temp_root_raw) is not str or not temp_root_raw:
        return readiness
    try:
        temp_root = pathlib.Path(temp_root_raw).resolve(strict=True)
        resolved_path = pathlib.Path(last_path).resolve(strict=True)
    except (OSError, RuntimeError):
        return readiness
    if not resolved_path.is_file() or resolved_path.parent.parent != temp_root:
        return readiness
    if re.fullmatch(r"phase3_ready_[A-Za-z0-9_]+", resolved_path.parent.name) is None:
        return readiness
    if re.fullmatch(r"ready_[0-9]{2}_[0-9]{2}\.png", resolved_path.name) is None:
        return readiness

    try:
        if sha256_file(resolved_path) != recorded_hash:
            return readiness
        image = mcp_capture.load_rgb(str(resolved_path))
        try:
            if (
                client["left"] + client["width"] > image.width
                or client["top"] + client["height"] > image.height
            ):
                return readiness
            client_image = image.crop(
                (
                    client["left"],
                    client["top"],
                    client["left"] + client["width"],
                    client["top"] + client["height"],
                )
            )
            try:
                actual_client_stats = mcp_capture.image_stats_from_image(client_image)
                actual_client_mean = actual_client_stats.get("meanBrightness")
                actual_client_nonblack = actual_client_stats.get("nonBlackRatio")
            finally:
                client_image.close()
            if not (
                type(actual_client_mean) is float
                and math.isfinite(actual_client_mean)
                and type(actual_client_nonblack) is float
                and math.isfinite(actual_client_nonblack)
            ):
                return readiness
            if actual_client_mean <= 1.0 and actual_client_nonblack <= 0.01:
                return readiness
            stats = mcp_capture.image_stats_from_image(image)
            actual_mean = stats.get("meanBrightness")
            actual_nonblack = stats.get("nonBlackRatio")
            menu_red_line = _task9_menu_red_line_present(image)
        finally:
            image.close()
    except Exception:
        return readiness
    if not (
        type(actual_mean) is float
        and math.isfinite(actual_mean)
        and round(actual_mean, 1) == last_mean
        and type(actual_nonblack) is float
        and math.isfinite(actual_nonblack)
        and round(actual_nonblack, 4) == last_nonblack
        and menu_red_line is False
    ):
        return readiness

    recovered = dict(readiness)
    recovered["inworld"] = True
    recovered["task9_readiness_recovery"] = {
        "applied": True,
        "reason": "bright_settled_frame_without_menu_red_line",
        "last_path": str(resolved_path),
        "last_mean": last_mean,
        "last_nonblack": last_nonblack,
        "menu_red_line_present": False,
    }
    return recovered


class DefaultRuntime:
    def __init__(
        self,
        config: SmokeConfig,
        *,
        broker_identity_json: str | None = None,
        lease_token: str | None = None,
    ) -> None:
        if (broker_identity_json is None) != (lease_token is None):
            raise ValueError("incomplete_broker_session")
        self.config = config
        self.broker_identity_json = broker_identity_json
        self.lease_token = lease_token
        tools = str(config.tools_dir)
        if tools not in sys.path:
            sys.path.insert(0, tools)
        self.mcp_client = importlib.import_module("mcp_client")
        self.mcp_capture = importlib.import_module("mcp_capture")

    def client_factory(self) -> object:
        key = self.config.keyfile.read_text(encoding="utf-8").strip()
        if not key:
            raise StopRun("empty_keyfile")
        identity_json = (
            self.broker_identity_json
            if self.broker_identity_json is not None
            else os.environ.get("DAYZ_MCP_CLIENT_ID_JSON", "")
        ).strip()
        lease_token = (
            self.lease_token
            if self.lease_token is not None
            else os.environ.get("DAYZ_MCP_LEASE_TOKEN", "")
        ).strip()
        if not identity_json or not lease_token:
            raise StopRun("broker_session_environment_missing")
        try:
            identity = json.loads(identity_json)
        except json.JSONDecodeError as exc:
            raise StopRun("broker_identity_json_invalid") from exc
        if not isinstance(identity, dict):
            raise StopRun("broker_identity_json_invalid")
        return self.mcp_client.Client(
            port=8765,
            key=key,
            timeout_s=30.0,
            identity=identity,
            lease_token=lease_token,
        )


    def query_player_state(
        self, client: object, timeout_s: float
    ) -> dict[str, Any]:
        try:
            _, result = self.mcp_client.run_result(
                client, "query_player_state", {}, timeout_s=timeout_s, peer="server"
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                raise
            try:
                body = exc.read()
            except OSError:
                exc.close()
                raise exc
            exc.close()
            if type(body) is not bytes:
                raise exc
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise exc
            if (
                type(payload) is not dict
                or payload.get("error") != "version_blocked"
                or payload.get("state") != "legacy_blocked"
                or not {"error", "state"}
                <= set(payload)
                <= {"error", "state", "expected", "got", "detail"}
            ):
                raise
            return {
                "ok": False,
                "error": "version_blocked",
                "state": "legacy_blocked",
            }
        return result

    def telemetry_object_at(
        self, client: object, position: list[float], radius: float
    ) -> dict[str, Any]:
        _, result = self.mcp_client.run_result(
            client,
            "telemetry_read",
            {
                "mode": "object_at",
                "type": OBJECT_TYPE,
                "pos": position,
                "radius": radius,
            },
            timeout_s=30.0,
            peer="server",
        )
        return result

    def prepare_vehicle_fixture(
        self, client: object, position: list[float], radius: float
    ) -> dict[str, Any]:
        _, result = self.mcp_client.run_result(
            client,
            "vehicle_prepare_fixture",
            {
                "mode": "object_at",
                "type": OBJECT_TYPE,
                "pos": position,
                "radius": radius,
            },
            timeout_s=30.0,
            peer="server",
        )
        return result

    def wait_for_client_peer_settlement(
        self, client: object, timeout_s: float
    ) -> dict[str, Any]:
        return _wait_for_client_peer_settlement(
            client,
            self.monotonic,
            self.sleep,
            timeout_s=timeout_s,
        )

    def inspect_frontend_overlay(
        self,
        client_pid: int,
        client_cmdline_match: str,
        evidence_filename: str = "task9-continue-preaction.png",
    ) -> dict[str, Any]:
        self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
        base_name = pathlib.Path(evidence_filename)
        started = self.monotonic()
        attempts = 0
        printwindow_attempts = 0
        while True:
            if attempts == 0:
                path = self.config.evidence_dir / base_name
            else:
                path = self.config.evidence_dir / (
                    f"{base_name.stem}-retry-{attempts:02d}{base_name.suffix}"
                )
            attempts += 1
            capture = self.mcp_capture.grab_window_to_file(
                str(path),
                process_name="DayZDiag_x64",
                method="foreground",
                client_pid=client_pid,
                cmdline_match=client_cmdline_match,
            )
            inspection = dict(
                _inspect_task9_continue_overlay(
                    path, capture, client_pid, self.mcp_capture
                )
            )
            elapsed = max(0.0, self.monotonic() - started)
            inspection["capture_attempts"] = attempts
            inspection["printwindow_attempts"] = printwindow_attempts
            inspection["capture_elapsed_s"] = round(elapsed, 3)
            if inspection.get("reason") != "continue_client_stats_invalid":
                inspection["capture_channel"] = "foreground"
                return inspection

            if attempts == 1:
                printwindow_path = self.config.evidence_dir / (
                    f"{base_name.stem}-printwindow{base_name.suffix}"
                )
            else:
                printwindow_path = self.config.evidence_dir / (
                    f"{base_name.stem}-printwindow-retry-{attempts - 1:02d}"
                    f"{base_name.suffix}"
                )
            printwindow_attempts += 1
            printwindow_capture = self.mcp_capture.grab_window_to_file(
                str(printwindow_path),
                process_name="DayZDiag_x64",
                method="printwindow",
                client_pid=client_pid,
                cmdline_match=client_cmdline_match,
            )
            printwindow_inspection = dict(
                _inspect_task9_continue_overlay(
                    printwindow_path,
                    printwindow_capture,
                    client_pid,
                    self.mcp_capture,
                    expected_method="printwindow",
                )
            )
            elapsed = max(0.0, self.monotonic() - started)
            if printwindow_inspection.get("reason") == "continue_overlay_exact":
                return {
                    "ok": False,
                    "detected": True,
                    "reason": "continue_overlay_foreground_unverified",
                    "capture_channel": "printwindow",
                    "capture_attempts": attempts,
                    "printwindow_attempts": printwindow_attempts,
                    "capture_elapsed_s": round(elapsed, 3),
                    "foreground_reason": "continue_client_stats_invalid",
                    "printwindow_path": printwindow_inspection.get("path"),
                    "printwindow_sha256": printwindow_inspection.get("sha256"),
                }
            if printwindow_inspection.get("reason") != "continue_client_stats_invalid":
                printwindow_inspection["capture_channel"] = "printwindow"
                printwindow_inspection["capture_attempts"] = attempts
                printwindow_inspection["printwindow_attempts"] = printwindow_attempts
                printwindow_inspection["capture_elapsed_s"] = round(elapsed, 3)
                printwindow_inspection["foreground_reason"] = (
                    "continue_client_stats_invalid"
                )
                return printwindow_inspection

            inspection["printwindow_attempts"] = printwindow_attempts
            inspection["printwindow_reason"] = "continue_client_stats_invalid"
            inspection["capture_channel"] = "foreground"
            inspection["capture_elapsed_s"] = round(elapsed, 3)
            remaining = TASK9_CONTINUE_CAPTURE_TIMEOUT_S - elapsed
            if remaining <= 0.0:
                return inspection
            self.sleep(min(TASK9_CONTINUE_CAPTURE_INTERVAL_S, remaining))

    def activate_frontend_window(
        self, client_pid: int, inspection: dict[str, Any]
    ) -> dict[str, Any]:
        window = inspection.get("window")
        if not (
            inspection.get("ok") is False
            and inspection.get("detected") is False
            and inspection.get("reason") == "continue_client_stats_invalid"
            and inspection.get("client_pid") == client_pid
            and type(window) is dict
            and set(window)
            == {"pid", "class", "title", "left", "top", "width", "height"}
            and all(
                type(window[key]) is int
                for key in ("pid", "left", "top", "width", "height")
            )
            and type(window["class"]) is str
            and type(window["title"]) is str
            and window["pid"] == client_pid
            and window["class"] == "DayZ"
            and window["title"] == "DayZ"
            and window["width"] > 0
            and window["height"] > 0
        ):
            return {
                "ok": False,
                "attempted": False,
                "reason": "black_inspection_not_authoritative",
            }
        return _activate_task9_owned_window(
            client_pid, window, toggle_presentation=True
        )

    def resume_frontend_overlay(
        self, client_pid: int, inspection: dict[str, Any]
    ) -> dict[str, Any]:
        click = inspection.get("click_screen")
        window = inspection.get("window")
        if not (
            inspection.get("ok") is True
            and inspection.get("detected") is True
            and inspection.get("reason") == "continue_overlay_exact"
            and inspection.get("client_pid") == client_pid
            and type(click) is list
            and len(click) == 2
            and all(type(value) is int for value in click)
            and type(window) is dict
            and set(window)
            == {"pid", "class", "title", "left", "top", "width", "height"}
            and all(
                type(window[key]) is int
                for key in ("pid", "left", "top", "width", "height")
            )
            and type(window["class"]) is str
            and type(window["title"]) is str
            and window["pid"] == client_pid
            and window["class"] == "DayZ"
            and window["title"] == "DayZ"
            and window["width"] > 0
            and window["height"] > 0
        ):
            return {
                "ok": False,
                "attempted": False,
                "reason": "continue_inspection_not_authoritative",
            }
        return _send_task9_owned_click(client_pid, click[0], click[1], window)

    def wait_for_readiness(
        self,
        client: object,
        camera_position: list[float],
        look_at: list[float],
        client_pid: int,
        client_cmdline_match: str,
        timeout_s: float,
    ) -> dict[str, Any]:
        args = argparse.Namespace(timeout=30.0, client_cmdline_match=client_cmdline_match)
        readiness = self.mcp_client.wait_for_inworld_render(
            client, camera_position, look_at, args, client_pid, timeout_s
        )
        return _recover_task9_bright_readiness(readiness, self.mcp_capture)

    def raycast(self, client: object, start: list[float], end: list[float]) -> dict[str, Any]:
        _, result = self.mcp_client.run_result(
            client,
            "scene_raycast",
            {
                "from": start,
                "to": end,
                "method": "rvproxy",
                "ignore": "player",
                "radius": 0.05,
                "intersect": "view",
            },
            timeout_s=30.0,
            peer="server",
        )
        return result

    def spawn(
        self, client: object, object_type: str, position: list[float], flags: int
    ) -> dict[str, Any]:
        server_settlement = _wait_for_server_peer_settlement(
            client,
            self.monotonic,
            self.sleep,
            timeout_s=CLIENT_PEER_SETTLE_TIMEOUT_SECONDS,
        )
        protocol: dict[str, Any] = {
            "command_id": None,
            "classification": "server_peer_settlement_failed",
            "operation_timeout_s": SPAWN_OPERATION_TIMEOUT_SECONDS,
            "server_settlement": server_settlement,
            "observations": [],
        }
        if server_settlement.get("ready") is not True:
            return {
                "ok": False,
                "error": "server_peer_settlement_failed",
                "command_observation": protocol,
            }

        command_id = client.enqueue_cmd(  # type: ignore[attr-defined]
            "world_spawn",
            {"type": object_type, "pos": position, "flags": flags},
            "server",
            operation_timeout_s=SPAWN_OPERATION_TIMEOUT_SECONDS,
        )
        if type(command_id) is not int or command_id <= 0:
            raise StopRun("spawn_command_id_invalid")
        protocol["command_id"] = command_id

        expected_generation = server_settlement.get("daemon_generation")
        started = self.monotonic()
        deadline = started + SPAWN_OPERATION_TIMEOUT_SECONDS
        observations: list[dict[str, Any]] = []
        last_observation_error = ""

        while (
            self.monotonic() < deadline
            and len(observations) < SPAWN_OBSERVATION_MAX_SAMPLES
        ):
            observed_at = self.monotonic()
            await_payload: dict[str, Any] = {}
            await_error = ""
            try:
                raw_await = client.request_json(  # type: ignore[attr-defined]
                    "GET",
                    "/await",
                    query={"id": command_id, "remove": 1},
                )
                if type(raw_await) is dict:
                    await_payload = raw_await
                else:
                    await_error = "await_payload_invalid"
            except Exception as exc:
                await_error = f"await_request_{type(exc).__name__}"

            status: dict[str, Any] = {}
            status_error = ""
            try:
                raw_status = client.request_json(  # type: ignore[attr-defined]
                    "GET", "/status"
                )
                if type(raw_status) is dict:
                    status = raw_status
                else:
                    status_error = "status_payload_invalid"
            except Exception as exc:
                status_error = f"status_request_{type(exc).__name__}"

            peer = (
                status.get("server_peer")
                if type(status.get("server_peer")) is dict
                else {}
            )
            current_generation = status.get("daemon_generation")
            poll_age = peer.get("last_poll_age_s")
            queue_depth = peer.get("queue_depth")
            version_state = peer.get("version_state")
            generation_stable = (
                type(expected_generation) is str
                and bool(expected_generation)
                and current_generation == expected_generation
            )
            poll_fresh = (
                type(poll_age) in (int, float)
                and math.isfinite(float(poll_age))
                and 0.0
                <= float(poll_age)
                <= CLIENT_PEER_SETTLE_MAX_POLL_AGE_SECONDS
            )
            queue_valid = type(queue_depth) is int and queue_depth >= 0
            version_ok = version_state == "ok"
            if not status_error:
                if not peer:
                    status_error = "server_peer_payload_invalid"
                elif not generation_stable:
                    status_error = "daemon_generation_drift"
                elif not queue_valid:
                    status_error = "server_queue_depth_invalid"
                elif not version_ok:
                    status_error = "server_version_not_ok"
                elif not poll_fresh:
                    status_error = "server_poll_stale"

            await_status = await_payload.get("status")
            observation = {
                "elapsed_s": round(max(0.0, observed_at - started), 3),
                "await_status": await_status,
                "last_poll_age_s": poll_age,
                "queue_depth": queue_depth,
                "version_state": version_state,
                "daemon_generation": current_generation,
            }
            observations.append(observation)
            protocol["observations"] = observations
            last_observation_error = await_error or status_error
            if last_observation_error:
                protocol["observation_error"] = last_observation_error

            if await_status == "done":
                raw_result = await_payload.get("result")
                if type(raw_result) is not dict:
                    protocol["classification"] = "spawn_observation_incomplete"
                    return {
                        "ok": False,
                        "error": "spawn_observation_incomplete",
                        "command_observation": protocol,
                    }
                result = dict(raw_result)
                if await_error or status_error:
                    protocol["classification"] = "spawn_observation_incomplete"
                    result["ok"] = False
                    result["error"] = "spawn_observation_incomplete"
                    result["command_observation"] = protocol
                    return result
                protocol["classification"] = "completed"
                result["command_observation"] = protocol
                return result

            if await_error or await_status != "pending":
                protocol["classification"] = "spawn_observation_incomplete"
                return {
                    "ok": False,
                    "error": "spawn_observation_incomplete",
                    "command_observation": protocol,
                }
            if status_error and status_error != "server_poll_stale":
                protocol["classification"] = "spawn_observation_incomplete"
                return {
                    "ok": False,
                    "error": "spawn_observation_incomplete",
                    "command_observation": protocol,
                }

            remaining = deadline - self.monotonic()
            if remaining <= 0.0:
                break
            self.sleep(min(CLIENT_PEER_SETTLE_INTERVAL_SECONDS, remaining))

        queue_depths = [
            observation.get("queue_depth")
            for observation in observations
            if type(observation.get("queue_depth")) is int
        ]
        classification = "spawn_observation_incomplete"
        transitioned_to_zero = (
            1 in queue_depths and 0 in queue_depths[queue_depths.index(1) + 1 :]
        )
        if queue_depths and all(depth == 1 for depth in queue_depths):
            classification = "spawn_not_dispatched_before_deadline"
        elif transitioned_to_zero and last_observation_error == "server_poll_stale":
            classification = "spawn_post_dispatch_peer_stalled"
        elif (
            transitioned_to_zero
            and observations
            and observations[-1].get("queue_depth") == 0
            and not last_observation_error
        ):
            classification = "spawn_result_missing_with_live_peer"
        protocol["classification"] = classification
        return {
            "ok": False,
            "error": classification,
            "command_observation": protocol,
        }

    def set_camera(
        self,
        client: object,
        view: str,
        camera_position: list[float],
        look_at: list[float],
    ) -> dict[str, Any]:
        args = argparse.Namespace(timeout=30.0)
        return self.mcp_client.camera_lookat_command(
            client, camera_position, look_at, args, timeout_s=30.0
        )

    def capture(
        self,
        view: str,
        destination: pathlib.Path,
        client_pid: int,
        client_cmdline_match: str,
    ) -> dict[str, Any]:
        result = self.mcp_capture.capture_dual(
            scale="small",
            client_pid=client_pid,
            cmdline_match=client_cmdline_match,
            save_fullres=True,
            save_dir=str(destination.parent),
        )
        source = result.get("fullres_path") if isinstance(result, dict) else None
        if result.get("isError") is True or not source:
            return result
        source_path = pathlib.Path(source)
        if source_path.resolve() != destination.resolve():
            os.replace(source_path, destination)
        return {"fullres_path": str(destination), "meta": result.get("meta") or {}}

    def cleanup(self, client: object, object_id: int) -> dict[str, Any]:
        _, result = self.mcp_client.run_result(
            client,
            "object_delete",
            {"object_id": object_id},
            timeout_s=30.0,
            peer="server",
        )
        return result

    def collect_logs(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for source, root in (
            ("server", self.config.server_profiles),
            ("client", self.config.client_profiles),
        ):
            if not root.is_dir():
                continue
            candidates = sorted(
                (path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() in {".rpt", ".log"}),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for path in candidates[:4]:
                records.append(
                    {
                        "source": source,
                        "profile_path": str(root.resolve()),
                        "path": str(path.resolve()),
                        "sha256": sha256_file(path),
                    }
                )
        return records

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _sha256_argument(value: str) -> str:
    normalized = value.upper()
    if re.fullmatch(r"[0-9A-F]{64}", normalized) is None:
        raise argparse.ArgumentTypeError("must be exactly 64 hexadecimal characters")
    return normalized


async def _collect_with_self_lease_async(
    config: SmokeConfig,
    provider: OwnershipProvider,
    *,
    max_wait_s: float,
) -> RunOutcome:
    tools = str(config.tools_dir)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    control_module = importlib.import_module("dayz_mcp.control_client")
    policy_module = importlib.import_module("dayz_mcp.normal_daemon_policy")
    supervisor_module = importlib.import_module("dayz_mcp.lease_supervisor")
    identity = control_module.ControlIdentity(
        platform="codex",
        pid=os.getpid(),
        ppid=os.getppid(),
        started_at_utc=datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        session_id=str(uuid.uuid4()),
        task_label="MercedesAMGLF Task 9 evidence",
    )
    control_client = control_module.ControlClient(
        policy=policy_module.load_normal_daemon_policy(),
        identity=identity,
    )
    grant = await control_client.session_acquire_wait(
        "MercedesAMGLF Task 9 evidence",
        max_wait_s=max_wait_s,
    )
    if not isinstance(grant, dict):
        raise StopRun("broker_session_grant_invalid")
    lease_token = grant.get("lease_token")
    lease_id = grant.get("lease_id")
    identity_json = grant.get("client_identity_json")
    if (
        grant.get("status") != "active"
        or not isinstance(lease_token, str)
        or not lease_token
        or not isinstance(lease_id, str)
        or not lease_id
        or not isinstance(identity_json, str)
        or not identity_json
    ):
        raise StopRun("broker_session_grant_invalid")
    supervisor = supervisor_module.LeaseHeartbeatSupervisor(
        control_client,
        lease_token=lease_token,
        lease_id=lease_id,
    )
    supervisor.start()
    primary: BaseException | None = None
    outcome: RunOutcome | None = None
    cleanup_error: BaseException | None = None
    try:
        outcome = await asyncio.to_thread(
            collect,
            config,
            provider,
            lambda current: DefaultRuntime(
                current,
                broker_identity_json=identity_json,
                lease_token=lease_token,
            ),
        )
        supervisor.ensure_healthy()
    except BaseException as exc:
        primary = exc
    finally:
        try:
            await supervisor.stop()
        except BaseException as exc:
            cleanup_error = exc
        try:
            await supervisor_module.protected_release_and_verify(
                control_client, lease_token
            )
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
    if primary is not None:
        if cleanup_error is not None:
            primary.add_note(
                f"lease cleanup degraded: {type(cleanup_error).__name__}"
            )
        raise primary
    if cleanup_error is not None:
        raise cleanup_error
    if outcome is None:
        raise StopRun("broker_collect_outcome_missing")
    return outcome


def collect_with_self_lease(
    config: SmokeConfig,
    provider: OwnershipProvider,
    *,
    max_wait_s: float,
) -> RunOutcome:
    try:
        return asyncio.run(
            _collect_with_self_lease_async(
                config,
                provider,
                max_wait_s=max_wait_s,
            )
        )
    except Exception as exc:
        return RunOutcome(
            EXIT_STOP,
            {
                "result": "STOP",
                "stop_reason": f"broker_session_failed:{type(exc).__name__}",
                "automatic_retry_blocked": True,
            },
        )


def _default_artifacts(
    *,
    pbo: pathlib.Path = DEFAULT_PBO,
    pbo_sha256: str = DEFAULT_EXPECTED_HASHES["pbo"],
) -> dict[str, ArtifactSpec]:
    return {
        "host_p3d": ArtifactSpec(DEFAULT_HOST, DEFAULT_EXPECTED_HASHES["host_p3d"]),
        "pbo": ArtifactSpec(pbo, pbo_sha256),
    }


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Task 9 Mercedes isolated baseline smoke evidence driver")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    collect_parser.add_argument("--ready-timeout", type=float, default=90.0)
    collect_parser.add_argument("--self-lease", action="store_true")
    collect_parser.add_argument("--lease-wait-seconds", type=float, default=300.0)
    collect_parser.add_argument("--pbo", type=pathlib.Path, default=DEFAULT_PBO)
    collect_parser.add_argument(
        "--pbo-sha256",
        type=_sha256_argument,
        default=DEFAULT_EXPECTED_HASHES["pbo"],
    )
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--verdict", type=pathlib.Path, required=True)
    finalize_parser.add_argument("--decision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return EXIT_USAGE
    try:
        args = _parser().parse_args(arguments)
    except (UsageError, SystemExit):
        return EXIT_USAGE
    if args.command == "finalize":
        outcome = finalize(args.verdict, args.decision)
    else:
        config = SmokeConfig(
            output_dir=args.output_dir,
            artifacts=_default_artifacts(pbo=args.pbo, pbo_sha256=args.pbo_sha256),
            ready_timeout=args.ready_timeout,
        )
        if args.self_lease:
            if (
                isinstance(args.lease_wait_seconds, bool)
                or not math.isfinite(args.lease_wait_seconds)
                or args.lease_wait_seconds <= 0.0
            ):
                return EXIT_USAGE
            outcome = collect_with_self_lease(
                config,
                WindowsOwnershipProvider(),
                max_wait_s=args.lease_wait_seconds,
            )
        else:
            outcome = collect(config, WindowsOwnershipProvider(), DefaultRuntime)
    print(json.dumps(outcome.payload, sort_keys=True))
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
