from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dayz_mcp import accredited_daemon_transport as transport
from dayz_mcp import daemon_policy, orphan_guard
from dayz_mcp import pinned_keyfile
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy
from dayz_mcp.native_process_guard import NativeProcessGuard
from dayz_mcp.process_lifecycle import (
    ProcessRecord,
    RunManifestStore,
)
from dayz_mcp.runtime_state import CoordinationFaultStore, RuntimePaths
from dayz_mcp.session_coordination import SESSION_TTL_S


_RETAIL_NAMES = ["DayZ_BE.exe", "DayZ_x64.exe"]
_MANAGED_NAMES = ["DayZDiag_x64.exe", "DayZServer_x64.exe"]
_BLIND_KILL = re.compile(r"\bStop-Process\b|\btaskkill(?:\.exe)?\b|\.Kill\s*\(", re.I)
_VALUE_OPTIONS = frozenset(
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
_BOOLEAN_OPTIONS = frozenset(
    {
        "--require-version",
        "--enable-exec-enforce",
        "--no-daemon-autospawn",
        "--client",
        "--daemon",
        "--embedded",
    }
)
_DAEMON_VALUE_OPTIONS = frozenset(
    {
        "-m",
        "--port",
        "--keyfile",
        "--expected-game-version",
        "--idle-timeout",
        "--exec-allowlist",
    }
)
_DAEMON_BOOLEAN_OPTIONS = frozenset(
    {"--require-version", "--enable-exec-enforce", "--daemon"}
)
_CREDENTIAL_RECOVERY_TTL_S = 300.0
_CREDENTIAL_RECOVERY_COUNT_MAX = 2_147_483_647
_CONFIG_PROBE_FAILED_MESSAGE = (
    "The CLI probe failed; this does not prove the registration is invalid. "
    "Read the platform config file directly before changing anything; "
    "do not re-register."
)


CommandSource = Callable[[], tuple[int, str]]
ListenerSource = Callable[[int], int | None]
ProcessArgvSource = Callable[[int], list[str] | None]
DaemonStatusSource = Callable[[int, str], dict[str, object]]
ProcessSnapshotSource = Callable[[list[str]], dict[str, object]]
ProcessIdentitySource = Callable[[int], dict[str, object]]


@dataclass(frozen=True)
class DoctorSources:
    claude_config: CommandSource
    codex_config: CommandSource
    listener_pid: ListenerSource
    process_argv: ProcessArgvSource
    daemon_status: DaemonStatusSource
    process_snapshot: ProcessSnapshotSource
    process_identity: ProcessIdentitySource
    runtime_paths: RuntimePaths
    scan_roots: tuple[Path, ...]
    expected_command: str


@dataclass(frozen=True)
class _DaemonPolicy:
    expected_game_version: str | None
    require_version: bool
    idle_timeout: float
    enable_exec_enforce: bool
    exec_allowlist: str | None


@dataclass(frozen=True)
class _Registration:
    client: bool
    daemon: bool
    embedded: bool
    platform: str | None
    port: int
    keyfile: str
    policy: _DaemonPolicy


class _ConfigProbeFailed(ValueError):
    pass


class _DaemonStatusError(RuntimeError):
    _CODES = frozenset(
        {"daemon_credential_desynchronized", "status_unavailable"}
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("invalid_daemon_status_error")
        self.code = code
        super().__init__(code)


def _run_command(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return result.returncode, result.stdout


def _read_daemon_status(
    policy: AccreditedDaemonPolicy,
) -> dict[str, object]:
    if type(policy) is not AccreditedDaemonPolicy:
        raise ValueError("invalid_daemon_policy")
    policy.revalidate()
    key = pinned_keyfile.read_pinned_keyfile(policy.keyfile)
    policy.revalidate()
    status, body = transport.verified_daemon_http_request(
        host=policy.host,
        port=policy.port,
        key=key,
        method="GET",
        path="/status",
        query={},
        body=None,
        headers={},
        deadline=time.monotonic() + 3.0,
        expected_executable=policy.native_executable,
        expected_argv=list(policy.argv),
        expected_cwd=policy.cwd,
        max_response_bytes=transport.MAX_AUTHENTICATED_RESPONSE_BYTES,
    )
    if status == 401:
        raise _DaemonStatusError("daemon_credential_desynchronized")
    if status != 200:
        raise _DaemonStatusError("status_unavailable")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_status")
    return payload


def default_sources(policy: AccreditedDaemonPolicy) -> DoctorSources:
    if type(policy) is not AccreditedDaemonPolicy:
        raise ValueError("invalid_daemon_policy")
    codex = shutil.which("codex.cmd") or "codex.cmd"
    guard = NativeProcessGuard()

    def daemon_status(port: int, keyfile: str) -> dict[str, object]:
        policy.revalidate()
        if port != policy.port or not _same_path(keyfile, policy.keyfile):
            raise ValueError("daemon_policy_drift")
        return _read_daemon_status(policy)

    return DoctorSources(
        claude_config=lambda: _run_command(["claude", "mcp", "get", "dayz-mcp"]),
        codex_config=lambda: _run_command(
            [codex, "mcp", "get", "dayz-mcp", "--json"]
        ),
        listener_pid=orphan_guard.listener_pid_for_port,
        process_argv=orphan_guard.command_argv_of,
        daemon_status=daemon_status,
        process_snapshot=orphan_guard.snapshot_processes_by_name,
        process_identity=guard.snapshot,
        runtime_paths=RuntimePaths.from_env(),
        scan_roots=(Path(__file__).resolve().parents[3],),
        expected_command=sys.executable,
    )


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_exact_argv_options(
    args: list[str],
    *,
    value_options: frozenset[str] = _VALUE_OPTIONS,
    boolean_options: frozenset[str] = _BOOLEAN_OPTIONS,
) -> dict[str, str | None]:
    parsed: dict[str, str | None] = {}
    index = 0
    while index < len(args):
        option = args[index]
        if (
            not isinstance(option, str)
            or "=" in option
            or option not in value_options | boolean_options
            or option in parsed
        ):
            raise ValueError("config_unreadable")
        if option in boolean_options:
            parsed[option] = None
            index += 1
            continue
        value_index = index + 1
        if value_index >= len(args):
            raise ValueError("config_unreadable")
        value = args[value_index]
        if not isinstance(value, str) or not value or value.startswith("-"):
            raise ValueError("config_unreadable")
        parsed[option] = _strip_outer_quotes(value)
        index += 2
    return parsed


def _parse_exact_text_options(text: str) -> dict[str, str | None]:
    matches = list(re.finditer(r"(?<!\S)(-{1,2}\S+)", text))
    if not matches or text[: matches[0].start()].strip():
        raise ValueError("config_unreadable")
    parsed: dict[str, str | None] = {}
    allowed = _VALUE_OPTIONS | _BOOLEAN_OPTIONS
    for index, match in enumerate(matches):
        option = match.group(1)
        if "=" in option or option not in allowed or option in parsed:
            raise ValueError("config_unreadable")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        trailing = text[match.end() : end].strip()
        if option in _BOOLEAN_OPTIONS:
            if trailing:
                raise ValueError("config_unreadable")
            parsed[option] = None
        else:
            if not trailing:
                raise ValueError("config_unreadable")
            parsed[option] = _strip_outer_quotes(trailing)
    return parsed


def _text_fields(text: str, name: str) -> list[str]:
    pattern = re.compile(
        r"^\s*" + re.escape(name) + r":\s*(.*?)\s*$", re.MULTILINE | re.IGNORECASE
    )
    return [match.group(1).strip() for match in pattern.finditer(text)]


def _same_path(first: str, second: str) -> bool:
    return os.path.normcase(os.path.normpath(first)) == os.path.normcase(
        os.path.normpath(second)
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("config_unreadable")
        result[key] = value
    return result


def _policy_from_options(options: dict[str, str | None]) -> _DaemonPolicy:
    raw_idle_timeout = options.get("--idle-timeout", "1800.0")
    try:
        idle_timeout = float(raw_idle_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("config_unreadable") from exc
    if not math.isfinite(idle_timeout):
        raise ValueError("config_unreadable")
    allowlist = options.get("--exec-allowlist")
    if allowlist is not None:
        allowlist = os.path.normcase(os.path.normpath(allowlist))
    return _DaemonPolicy(
        expected_game_version=options.get("--expected-game-version"),
        require_version="--require-version" in options,
        idle_timeout=idle_timeout,
        enable_exec_enforce="--enable-exec-enforce" in options,
        exec_allowlist=allowlist,
    )


def _parse_registration(
    platform: str, result: tuple[int, str], expected_command: str
) -> _Registration:
    returncode, output = result
    if returncode != 0:
        raise _ConfigProbeFailed("config_probe_failed")
    if not isinstance(output, str):
        raise ValueError("config_unreadable")
    if platform == "claude":
        types = _text_fields(output, "Type")
        commands = _text_fields(output, "Command")
        arguments = _text_fields(output, "Args")
        if (
            types != ["stdio"]
            or len(commands) != 1
            or not _same_path(commands[0], expected_command)
            or len(arguments) != 1
        ):
            raise ValueError("config_unreadable")
        options = _parse_exact_text_options(arguments[0])
        if options.get("-m") != "dayz_mcp":
            raise ValueError("config_unreadable")
        raw_port = options.get("--port")
        keyfile = options.get("--keyfile")
        platform_arg = options.get("--client-platform")
        client = "--client" in options
        daemon = "--daemon" in options
        embedded = "--embedded" in options
    else:
        payload = json.loads(output, object_pairs_hook=_unique_json_object)
        transport = payload.get("transport") if isinstance(payload, dict) else None
        args = transport.get("args") if isinstance(transport, dict) else None
        if (
            not isinstance(transport, dict)
            or transport.get("type") != "stdio"
            or not isinstance(transport.get("command"), str)
            or not _same_path(str(transport.get("command")), expected_command)
            or not isinstance(args, list)
            or any(not isinstance(item, str) for item in args)
        ):
            raise ValueError("config_unreadable")
        options = _parse_exact_argv_options(args)
        if options.get("-m") != "dayz_mcp":
            raise ValueError("config_unreadable")
        raw_port = options.get("--port")
        keyfile = options.get("--keyfile")
        platform_arg = options.get("--client-platform")
        client = "--client" in options
        daemon = "--daemon" in options
        embedded = "--embedded" in options
    try:
        port = int(raw_port) if raw_port is not None else 0
    except (TypeError, ValueError) as exc:
        raise ValueError("config_unreadable") from exc
    if not (1 <= port <= 65535) or not isinstance(keyfile, str) or not keyfile:
        raise ValueError("config_unreadable")
    return _Registration(
        client,
        daemon,
        embedded,
        platform_arg,
        port,
        keyfile,
        _policy_from_options(options),
    )


def _finding(code: str, *, severity: str = "FAIL", **details: object) -> dict[str, object]:
    return {"code": code, "severity": severity, **details}


def _safe_snapshot(
    source: ProcessSnapshotSource,
    names: list[str],
    scope: str,
    findings: list[dict[str, object]],
) -> list[dict[str, object]] | None:
    try:
        payload = source(list(names))
    except Exception:
        payload = {"known": False, "processes": []}
    if not isinstance(payload, dict) or payload.get("known") is not True:
        findings.append(_finding("PROCESS_SCAN_FAILED", scope=scope))
        return None
    raw = payload.get("processes")
    if not isinstance(raw, list):
        findings.append(_finding("PROCESS_SCAN_FAILED", scope=scope))
        return None
    processes: list[dict[str, object]] = []
    for value in raw:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("pid"), int)
            or isinstance(value.get("pid"), bool)
            or int(value["pid"]) <= 0
            or not isinstance(value.get("name"), str)
        ):
            findings.append(_finding("PROCESS_SCAN_FAILED", scope=scope))
            return None
        processes.append({"pid": int(value["pid"]), "name": str(value["name"])})
    return sorted(processes, key=lambda item: (int(item["pid"]), str(item["name"])))


def _check_coordination(
    status: dict[str, object], findings: list[dict[str, object]], port: int
) -> None:
    generation = status.get("daemon_generation")
    if not isinstance(generation, str) or not generation.strip():
        findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port))
        return
    coordination = status.get("coordination")
    if not isinstance(coordination, dict):
        findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port))
        return

    def number(value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0.0
        )

    def nonempty(value: object) -> bool:
        return isinstance(value, str) and bool(value)

    def valid_lease(value: object) -> bool:
        if value is None:
            return True
        if not isinstance(value, dict):
            return False
        granted = value.get("granted_at_monotonic")
        expires = value.get("expires_at_monotonic")
        return (
            nonempty(value.get("lease_id"))
            and nonempty(value.get("session"))
            and number(granted)
            and number(expires)
            and number(captured)
            and float(granted) <= float(captured)
            and float(granted) <= float(expires)
            and (
                "ticket" not in value or nonempty(value.get("ticket"))
            )
        )

    required = {
        "revision",
        "captured_at_monotonic",
        "active",
        "releasing",
        "granting",
        "handoff_pending",
        "claimable",
        "audit_fault",
        "operation_tombstones",
        "queue",
        "cleanup_workers",
    }
    captured = coordination.get("captured_at_monotonic")
    revision = coordination.get("revision")
    queue = coordination.get("queue")
    workers = coordination.get("cleanup_workers")
    tombstones = coordination.get("operation_tombstones")
    valid_tombstones = isinstance(tombstones, dict)
    if valid_tombstones:
        tombstone_count = tombstones.get("count")
        tombstone_capacity = tombstones.get("capacity")
        tombstone_saturated = tombstones.get("saturated")
        valid_tombstones = (
            isinstance(tombstone_count, int)
            and not isinstance(tombstone_count, bool)
            and tombstone_count >= 0
            and isinstance(tombstone_capacity, int)
            and not isinstance(tombstone_capacity, bool)
            and tombstone_capacity > 0
            and tombstone_count <= tombstone_capacity
            and isinstance(tombstone_saturated, bool)
            and tombstone_saturated == (tombstone_count >= tombstone_capacity)
        )
    valid_workers = isinstance(workers, dict)
    if valid_workers:
        capacity = workers.get("capacity")
        active_workers = workers.get("active")
        saturated = workers.get("saturated")
        valid_workers = (
            isinstance(capacity, int)
            and not isinstance(capacity, bool)
            and capacity > 0
            and isinstance(active_workers, int)
            and not isinstance(active_workers, bool)
            and 0 <= active_workers <= capacity
            and isinstance(saturated, int)
            and not isinstance(saturated, bool)
            and saturated >= 0
        )
    valid_tickets = isinstance(queue, list)
    ticket_ids: set[str] = set()
    if valid_tickets:
        for ticket in queue:
            if not isinstance(ticket, dict):
                valid_tickets = False
                break
            created = ticket.get("created_at_monotonic")
            touched = ticket.get("touched_at_monotonic")
            if not (
                nonempty(ticket.get("ticket"))
                and nonempty(ticket.get("session"))
                and number(created)
                and number(touched)
                and number(captured)
                and float(created) <= float(touched) <= float(captured)
            ):
                valid_tickets = False
                break
            ticket_id = str(ticket["ticket"])
            if ticket_id in ticket_ids:
                valid_tickets = False
                break
            ticket_ids.add(ticket_id)
    live_lease_states = sum(
        coordination.get(field) is not None
        for field in ("active", "releasing", "granting")
    )
    if (
        not required.issubset(coordination)
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or not number(captured)
        or not isinstance(coordination.get("handoff_pending"), bool)
        or not isinstance(coordination.get("claimable"), bool)
        or not all(
            valid_lease(coordination.get(field))
            for field in ("active", "releasing", "granting")
        )
        or live_lease_states > 1
        or not valid_tickets
        or not valid_workers
        or not valid_tombstones
    ):
        findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port))
        return
    audit_fault = coordination.get("audit_fault")
    if audit_fault is not None:
        try:
            validated_fault = CoordinationFaultStore.validate(audit_fault)
        except ValueError:
            findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port))
            return
        if coordination.get("claimable") is not False:
            findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port))
            return
        findings.append(
            _finding(
                "COORDINATION_AUDIT_FAULT",
                port=port,
                fault_id=validated_fault["fault_id"],
                operation=validated_fault["operation"],
                phase=validated_fault["phase"],
            )
        )
    if tombstones["saturated"]:
        findings.append(
            _finding(
                "OPERATION_TOMBSTONES_SATURATED",
                port=port,
                count=tombstones["count"],
                capacity=tombstones["capacity"],
            )
        )
    for field in ("active", "releasing", "granting"):
        lease = coordination.get(field)
        if lease is None:
            continue
        expiry = lease.get("expires_at_monotonic") if isinstance(lease, dict) else None
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
            findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port))
        elif float(expiry) <= float(captured):
            findings.append(
                _finding("LEASE_STALE", lease_state=field, port=port)
            )
    for ticket in queue:
        touched = ticket["touched_at_monotonic"]
        if float(touched) + SESSION_TTL_S <= float(captured):
            findings.append(_finding("TICKET_STALE", port=port))


def _check_credential_recovery(
    status: dict[str, object],
    findings: list[dict[str, object]],
    port: int,
) -> None:
    if "credential_recovery" not in status:
        return
    recovery = status.get("credential_recovery")
    required = {
        "recovered_count",
        "recent",
        "last_recovered_age_s",
    }
    if not isinstance(recovery, dict) or set(recovery) != required:
        findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port))
        return
    recovered_count = recovery.get("recovered_count")
    recent = recovery.get("recent")
    age = recovery.get("last_recovered_age_s")
    valid_count = (
        isinstance(recovered_count, int)
        and not isinstance(recovered_count, bool)
        and 0 <= recovered_count <= _CREDENTIAL_RECOVERY_COUNT_MAX
    )
    valid_recent = isinstance(recent, bool)
    valid_age = (
        isinstance(age, (int, float))
        and not isinstance(age, bool)
        and math.isfinite(float(age))
        and 0.0 <= float(age) <= _CREDENTIAL_RECOVERY_TTL_S
    )
    if (
        not valid_count
        or not valid_recent
        or (recent is True and (recovered_count == 0 or not valid_age))
        or (recent is False and age is not None)
    ):
        findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port))
        return
    if recent:
        findings.append(
            _finding(
                "STALE_CLIENT_CREDENTIAL_RECOVERED",
                severity="INFO",
                port=port,
                recovered_count=recovered_count,
                last_recovered_age_s=age,
            )
        )


def _valid_daemon_listener_argv(
    argv: list[str],
    expected_command: str,
    registration: _Registration,
) -> bool:
    try:
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(value, str) or not value for value in argv)
            or not _same_path(argv[0], expected_command)
        ):
            return False
        options = _parse_exact_argv_options(
            argv[1:],
            value_options=_DAEMON_VALUE_OPTIONS,
            boolean_options=_DAEMON_BOOLEAN_OPTIONS,
        )
        raw_port = options.get("--port")
        if (
            options.get("-m") != "dayz_mcp"
            or "--daemon" not in options
            or options.get("--keyfile") is None
            or options.get("--idle-timeout") is None
            or int(raw_port) != registration.port
            or not _same_path(str(options["--keyfile"]), registration.keyfile)
        ):
            return False
        return _policy_from_options(options) == registration.policy
    except (TypeError, ValueError):
        return False


def _identity_matches(record: ProcessRecord, actual: dict[str, object]) -> bool:
    return (
        actual.get("identity_complete") is True
        and actual.get("identity_scheme") == record.identity_scheme
        and actual.get("pid") == record.pid
        and actual.get("creation_time_utc") == record.creation_time_utc
        and str(actual.get("executable_sha256", "")).casefold()
        == record.executable_sha256.casefold()
        and str(actual.get("command_line_sha256", "")).casefold()
        == record.command_line_sha256.casefold()
    )


def _preprune_backup_slots_exhausted(runs_path: Path) -> bool:
    """True when all 10 pre-prune backup slots exist (prune is fail-closed silent)."""

    base_name = runs_path.name + ".bak-preprune"
    slot_names = [base_name] + [f"{base_name}.{index}" for index in range(2, 11)]
    return all(runs_path.with_name(name).exists() for name in slot_names)


def _check_runs(
    sources: DoctorSources,
    observed: list[dict[str, object]] | None,
    findings: list[dict[str, object]],
) -> None:
    try:
        runs = RunManifestStore(sources.runtime_paths, read_only=True).list_runs()
    except (OSError, TypeError, ValueError):
        try:
            raw = json.loads(sources.runtime_paths.runs_path.read_text(encoding="utf-8"))
            raw_runs = raw.get("runs") if isinstance(raw, dict) else None
            if isinstance(raw_runs, list):
                for item in raw_runs:
                    if (
                        isinstance(item, dict)
                        and item.get("state") == "RUNNING"
                        and item.get("processes") == []
                        and isinstance(item.get("run_id"), str)
                        and item.get("run_id")
                    ):
                        findings.append(
                            _finding("RUN_STALE", run_id=str(item["run_id"]))
                        )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        findings.append(_finding("RUN_MANIFEST_UNREADABLE"))
        return
    if _preprune_backup_slots_exhausted(sources.runtime_paths.runs_path):
        findings.append(
            _finding(
                "RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED",
                slots=10,
                path=str(
                    sources.runtime_paths.runs_path.with_name(
                        sources.runtime_paths.runs_path.name + ".bak-preprune"
                    )
                ),
            )
        )
    if observed is None:
        return
    observed_pids = {int(item["pid"]) for item in observed}
    records: dict[int, tuple[str, ProcessRecord]] = {}
    duplicate_runs: dict[int, set[str]] = {}
    for run in runs:
        if run.state == "EXITED":
            continue
        if run.state in {"STARTING", "STOPPING", "UNRECONCILED"}:
            findings.append(_finding("RUN_STALE", run_id=run.run_id))
        for record in run.processes:
            if record.pid in records:
                duplicate_runs.setdefault(record.pid, {records[record.pid][0]}).add(
                    run.run_id
                )
                continue
            records[record.pid] = (run.run_id, record)

    for pid, run_ids in sorted(duplicate_runs.items()):
        findings.append(
            _finding(
                "RUN_IDENTITY_MISMATCH", pid=pid, run_ids=sorted(run_ids)
            )
        )

    accounted: set[int] = set()
    for pid, (run_id, record) in sorted(records.items()):
        if record.identity_scheme == "legacy-wmi-v1":
            findings.append(
                _finding(
                    "LEGACY_PROCESS_IDENTITY_SCHEME",
                    pid=pid,
                    run_id=run_id,
                )
            )
            if pid not in observed_pids:
                findings.append(_finding("RUN_STALE", pid=pid, run_id=run_id))
            accounted.add(pid)
            continue
        if pid not in observed_pids:
            findings.append(_finding("RUN_STALE", pid=pid, run_id=run_id))
            accounted.add(pid)
            continue
        try:
            actual = sources.process_identity(pid)
        except Exception:
            actual = {"error": "guard_unavailable", "exit_code": 3}
        if (
            isinstance(actual, dict)
            and actual.get("error") == "process_not_found"
            and actual.get("exit_code") == 4
        ):
            findings.append(_finding("RUN_STALE", pid=pid, run_id=run_id))
        elif not isinstance(actual, dict) or actual.get("identity_complete") is not True:
            findings.append(
                _finding("PROCESS_SCAN_FAILED", pid=pid, run_id=run_id, scope="identity")
            )
        elif not _identity_matches(record, actual):
            findings.append(
                _finding("RUN_IDENTITY_MISMATCH", pid=pid, run_id=run_id)
            )
        accounted.add(pid)

    for item in observed:
        pid = int(item["pid"])
        if pid not in records and pid not in accounted:
            findings.append(
                _finding("PROCESS_UNREGISTERED", pid=pid, name=str(item["name"]))
            )


def _check_launchers(
    roots: tuple[Path, ...], findings: list[dict[str, object]]
) -> None:
    paths: set[Path] = set()
    for root in roots:
        try:
            paths.update(Path(root).rglob("dayz-test.ps1"))
        except OSError:
            findings.append(_finding("LAUNCHER_SCAN_FAILED", path=str(Path(root))))
    for path in sorted(paths, key=lambda item: str(item).casefold()):
        if any(part.casefold() == "_backups" for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            findings.append(_finding("LAUNCHER_SCAN_FAILED", path=str(path.resolve())))
            continue
        for line_number, line in enumerate(lines, start=1):
            if _BLIND_KILL.search(line):
                findings.append(
                    _finding(
                        "LEGACY_BLIND_KILL",
                        path=str(path.resolve()),
                        line=line_number,
                    )
                )


def _diagnose(sources: DoctorSources, *, require_clean: bool) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    registrations: dict[str, _Registration] = {}
    for platform, source in (
        ("claude", sources.claude_config),
        ("codex", sources.codex_config),
    ):
        try:
            registration = _parse_registration(
                platform, source(), sources.expected_command
            )
        except _ConfigProbeFailed:
            findings.append(
                _finding(
                    "CONFIG_PROBE_FAILED",
                    platform=platform,
                    message=_CONFIG_PROBE_FAILED_MESSAGE,
                )
            )
            continue
        except (json.JSONDecodeError, TypeError, ValueError):
            findings.append(_finding("CONFIG_UNREADABLE", platform=platform))
            continue
        registrations[platform] = registration
        if not registration.client or registration.daemon or registration.embedded:
            findings.append(_finding("CONFIG_EMBEDDED", platform=platform))
        if registration.platform != platform:
            findings.append(_finding("CONFIG_MISMATCH", platform=platform))

    if len(registrations) == 2:
        claude = registrations["claude"]
        codex = registrations["codex"]
        same_keyfile = os.path.normcase(os.path.normpath(claude.keyfile)) == os.path.normcase(
            os.path.normpath(codex.keyfile)
        )
        if (
            claude.port != codex.port
            or not same_keyfile
            or claude.policy != codex.policy
        ):
            findings.append(_finding("CONFIG_MISMATCH", platform="shared"))

    listeners: dict[int, tuple[int, _Registration]] = {}
    checked_ports: set[int] = set()
    for registration in registrations.values():
        if registration.port in checked_ports:
            continue
        checked_ports.add(registration.port)
        try:
            pid = sources.listener_pid(registration.port)
        except Exception:
            findings.append(
                _finding("PROCESS_SCAN_FAILED", scope="listener", port=registration.port)
            )
            continue
        if pid is None:
            findings.append(
                _finding(
                    "DAEMON_STATUS_UNREADABLE",
                    scope="listener",
                    port=registration.port,
                )
            )
        else:
            listeners[registration.port] = (int(pid), registration)

    listener_pids = sorted({pid for pid, _ in listeners.values()})
    if len(listener_pids) > 1:
        findings.append(_finding("MULTIPLE_LISTENERS", pids=listener_pids))
    for port, (pid, registration) in sorted(listeners.items()):
        try:
            argv = sources.process_argv(pid)
        except Exception:
            argv = None
        if (
            not isinstance(argv, list)
            or not _valid_daemon_listener_argv(
                argv, sources.expected_command, registration
            )
        ):
            findings.append(
                _finding("PROCESS_SCAN_FAILED", scope="listener", port=port, pid=pid)
            )
            continue
        try:
            status = sources.daemon_status(port, registration.keyfile)
        except _DaemonStatusError as error:
            if error.code == "daemon_credential_desynchronized":
                findings.append(
                    _finding(
                        "DAEMON_CREDENTIAL_DESYNCHRONIZED",
                        port=port,
                        pid=pid,
                    )
                )
            else:
                findings.append(
                    _finding("DAEMON_STATUS_UNREADABLE", port=port, pid=pid)
                )
            continue
        except Exception:
            findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port, pid=pid))
            continue
        if not isinstance(status, dict):
            findings.append(_finding("DAEMON_STATUS_UNREADABLE", port=port, pid=pid))
            continue
        _check_coordination(status, findings, port)
        _check_credential_recovery(status, findings, port)

    retail = _safe_snapshot(
        sources.process_snapshot, _RETAIL_NAMES, "retail", findings
    )
    managed = _safe_snapshot(
        sources.process_snapshot, _MANAGED_NAMES, "managed", findings
    )
    if retail:
        findings.append(
            _finding(
                "RETAIL_MANUAL_CLOSE_REQUIRED",
                severity="FAIL" if require_clean else "WARN",
                processes=retail,
            )
        )
    _check_runs(sources, managed, findings)
    _check_launchers(sources.scan_roots, findings)

    findings.sort(
        key=lambda item: (
            str(item.get("code", "")),
            str(item.get("path", "")),
            int(item.get("pid", 0)),
            str(item.get("platform", "")),
        )
    )
    fail = sum(item.get("severity") == "FAIL" for item in findings)
    warn = sum(item.get("severity") == "WARN" for item in findings)
    return {
        "ok": fail == 0,
        "findings": findings,
        "summary": {"fail": fail, "warn": warn},
    }


def execute(
    sources: DoctorSources | None = None,
    *,
    require_clean: bool = False,
    policy: AccreditedDaemonPolicy | None = None,
) -> tuple[dict[str, object], int]:
    try:
        if sources is None:
            if type(policy) is not AccreditedDaemonPolicy:
                raise ValueError("missing_daemon_policy")
            sources = default_sources(policy)
        payload = _diagnose(sources, require_clean=require_clean)
    except Exception:
        return (
            {
                "ok": False,
                "error": "diagnostic_failure",
                "findings": [],
                "summary": {"fail": 0, "warn": 0},
            },
            2,
        )
    return payload, 0 if payload["ok"] else 1


def render_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def render_human(payload: dict[str, object]) -> str:
    lines = ["DayZ-MCP doctor: " + ("OK" if payload.get("ok") else "FINDINGS")]
    for finding in payload.get("findings", []):
        if isinstance(finding, dict):
            line = f"[{finding.get('severity')}] {finding.get('code')}"
            if finding.get("code") == "CONFIG_PROBE_FAILED":
                platform = finding.get("platform")
                if isinstance(platform, str) and platform:
                    line += f" ({platform})"
                message = finding.get("message")
                if isinstance(message, str) and message:
                    line += f": {message}"
            lines.append(line)
    if payload.get("error"):
        lines.append("[ERROR] diagnostic_failure")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only DayZ-MCP session doctor")
    parser.add_argument(
        "--daemon-policy", choices=("normal", "bootstrap"), required=True
    )
    parser.add_argument("--json", action="store_true", help="emit stable JSON")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="treat externally opened retail DayZ as a failure",
    )
    args = parser.parse_args(argv)
    try:
        policy = daemon_policy.load_daemon_policy(args.daemon_policy)
        payload, exit_code = execute(
            require_clean=args.require_clean, policy=policy
        )
    except Exception:
        payload, exit_code = (
            {
                "ok": False,
                "error": "diagnostic_failure",
                "findings": [],
                "summary": {"fail": 0, "warn": 0},
            },
            2,
        )
    print(render_json(payload) if args.json else render_human(payload))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
