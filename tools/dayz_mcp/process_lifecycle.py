from __future__ import annotations

# Every transition into RUNNING_IDLE goes through the fence; the poll
# re-validates after re-acquiring the lock.

import dataclasses
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, TypeVar

from dayz_mcp.instance_fence import BindingPrepareError
from dayz_mcp.runtime_state import RuntimePaths, atomic_write_bytes
from dayz_mcp.session_coordination import (
    AuthorizationDecision,
    CleanupDisposition,
    ClientIdentity,
    SessionCoordinator,
)


RUN_STATES = frozenset(
    {"STARTING", "RUNNING", "RUNNING_IDLE", "STOPPING", "EXITED", "UNRECONCILED"}
)
_ACTIVE_STATES = RUN_STATES - {"EXITED"}
# States a dead-run reaper may retire: they hold ProcessRecords but no in-flight
# lifecycle operation. STARTING/STOPPING (operation in progress under the lock) are
# left to recover_after_restart; EXITED is terminal.
_REAPABLE_STATES = frozenset({"RUNNING", "RUNNING_IDLE", "UNRECONCILED"})
_HEX = frozenset("0123456789abcdef")
_IDENTITY_SCHEMES = frozenset({"legacy-wmi-v1", "psutil-argv-v2"})
_BOX_MOD_CAP = 12
_BOX_OCCUPANCY_CACHE_S = 1.5
# fb-20260904-114520-6927: images whose bound UDP ports make the box occupied
# even without a run record. Mirrors orphan_guard so a probe fed from another
# source is classified the same way.
_DAYZ_IMAGE_NAMES = frozenset(
    {"dayzdiag_x64.exe", "dayzserver_x64.exe", "dayz_x64.exe", "dayz_be.exe"}
)
# The band this project launches in: 2302 by default, alternates at +100 steps
# (2402, 2502, ...) and the query/steam neighbours of each. A holder of one of
# these is reported in ports_in_use whatever its image, so a caller can pick
# another port; only DayZ images occupy the box.
_DAYZ_PORT_RANGE = range(2302, 3000)
_PORT_SCAN_UNKNOWN_HINT = (
    "port_scan_unknown: the daemon could not read the host UDP socket table "
    "(psutil/netstat); waiting does not help, restore that first"
)
_ACTIVITY_STALE_S = 900.0
_ACTIVE_RUN_STOP_HINT = "stop it with dayz_test_stop(run_id={run_id})"
_ACTIVE_RUN_WAIT_HINT = "retry with wait_for_box_s=<n>"


def _valid_uuid4(value: object) -> bool:
    if not isinstance(value, str) or value != value.casefold():
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _valid_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and value == value.casefold()
        and all(character in _HEX for character in value)
    )


def _default_argv_of(pid: int) -> list[str] | None:
    try:
        from dayz_mcp.native_process_snapshot import command_argv_of
    except Exception:
        return None
    return command_argv_of(pid)


def _parse_port_token(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _public_profiles_label(value: object) -> str | None:
    """Publish a directory basename, never a host-absolute path."""

    if not isinstance(value, str) or not value.strip():
        return None
    path = value.replace("/", "\\").rstrip("\\")
    parts = [part for part in path.split("\\") if part]
    if not parts:
        return None
    if parts[-1].casefold() == "profiles" and len(parts) >= 2:
        return parts[-2]
    return parts[-1]


def _summarize_mod_tokens(raw: list[str]) -> list[str]:
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            continue
        name = item.replace("/", "\\").rstrip("\\")
        base = name.rsplit("\\", 1)[-1]
        if not base or base in names:
            continue
        names.append(base)
        if len(names) >= _BOX_MOD_CAP:
            break
    return names


def parse_dayz_launch_argv(argv: object) -> dict[str, object]:
    """Extract public -port/-mod/-profiles tokens from a DayZDiag argv."""

    ports: list[int] = []
    mods: list[str] = []
    profiles: str | None = None
    if not isinstance(argv, list):
        return {"ports": ports, "mods": mods, "profiles": profiles}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if not isinstance(argument, str) or not argument:
            index += 1
            continue
        if argument.startswith("-port="):
            port = _parse_port_token(argument[6:])
            if port is not None and port not in ports:
                ports.append(port)
        elif argument == "-port" and index + 1 < len(argv):
            port = _parse_port_token(argv[index + 1])
            if port is not None and port not in ports:
                ports.append(port)
            index += 1
        elif argument.startswith("-mod=") or argument.startswith("-serverMod="):
            payload = argument.split("=", 1)[1]
            mods.extend(part for part in payload.split(";") if part)
        elif argument.startswith("-profiles="):
            profiles = argument[10:] or None
        index += 1
    return {
        "ports": ports,
        "mods": _summarize_mod_tokens(mods),
        "profiles": profiles,
    }


def _is_dayz_image(name: object) -> bool:
    return isinstance(name, str) and name.casefold() in _DAYZ_IMAGE_NAMES


def _requested_port(argv: object) -> int | None:
    """The -port this launch asked for, or None when argv names none."""
    ports = parse_dayz_launch_argv(argv).get("ports")
    if isinstance(ports, list) and ports and isinstance(ports[0], int):
        return ports[0]
    return None


def _utc_epoch(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        digits = ""
        suffix = rest
        for offset, char in enumerate(rest):
            if char.isdigit():
                digits += char
                continue
            suffix = rest[offset:]
            break
        else:
            suffix = ""
        text = f"{head}.{(digits + '000000')[:6]}{suffix}"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _run_age_s(run: RunRecord, now: float) -> float:
    stamps = [
        stamp
        for stamp in (_utc_epoch(record.creation_time_utc) for record in run.processes)
        if stamp is not None
    ]
    if not stamps:
        return 0.0
    return max(0.0, now - min(stamps))


def empty_box(*, occupied: bool) -> dict[str, object]:
    return {
        "occupied": occupied,
        "runs": [],
        "foreign": [],
        "ports_in_use": [],
        "queue": [],
    }


def _copy_box(payload: dict[str, object]) -> dict[str, object]:
    runs = payload.get("runs")
    foreign = payload.get("foreign")
    ports = payload.get("ports_in_use")
    queue = payload.get("queue")
    return {
        "occupied": bool(payload.get("occupied")),
        "runs": [dict(item) for item in runs] if isinstance(runs, list) else [],
        "foreign": [dict(item) for item in foreign] if isinstance(foreign, list) else [],
        "ports_in_use": list(ports) if isinstance(ports, list) else [],
        "queue": list(queue) if isinstance(queue, list) else [],
        # Additive. Missing/unknown → False (fail-closed). Distinguishes a
        # clean empty foreign list from a scan that never ran.
        "scan_known": payload.get("scan_known") is True,
    }


@dataclass(frozen=True)
class _BoxSnapshot:
    clock: float
    runs: tuple[RunRecord, ...]
    activity: Mapping[str, float]
    unknown: frozenset[str]
    revision: int
    daemon_generation: str = ""
    compensating: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class _BoxProbes:
    foreign: tuple[dict[str, object], ...]
    ports_in_use: tuple[int, ...]
    scan_known: bool
    port_scan_known: bool = True
    port_scan_reason: str | None = None
    foreign_ports: tuple[int, ...] = ()


def _activity_from_snapshot(
    snapshot: _BoxSnapshot, run_id: str
) -> tuple[str, float | None]:
    if run_id in snapshot.unknown or run_id in snapshot.compensating:
        return "unknown", None
    stamp = snapshot.activity.get(run_id)
    if stamp is None:
        return "unknown", None
    if stamp > snapshot.clock:
        return "unknown", None
    age = snapshot.clock - stamp
    if age <= _ACTIVITY_STALE_S:
        return "recent", round(age, 3)
    return "stale", round(age, 3)


def _generation_projection(run: RunRecord, current: str) -> dict[str, object]:
    launch = getattr(run, "daemon_generation_at_launch", None)
    if not isinstance(launch, str) or not launch:
        launch = None
    current_value = current if isinstance(current, str) else ""
    changed = None if launch is None else launch != current_value
    return {
        "daemon_generation_at_launch": launch,
        "daemon_generation_current": current_value,
        "generation_changed": changed,
    }


_DIAGNOSTIC_PUBLIC_KEYS = (
    "run_id",
    "daemon_generation_at_launch",
    "daemon_generation_current",
    "generation_changed",
    "event",
    "reason",
    "decision",
    "state",
)
_BINDING_REASON_BY_EVENT = {
    "lifecycle_stop_outcome": "stopped",
    "lifecycle_owner_released": "released",
    "lifecycle_recovery_repair": "recovery_repaired",
    "lifecycle_manifest_recovery": "manifest_repaired",
    "run_reaped": "reaped",
    "admin_reconcile": "admin_reconciled",
}
_RUN_PROCESSES_GONE_HINT = (
    "This run has no live owned process. Reap or recover the run "
    "(reap_dead_run / reap_dead_runs / admin reconcile) so the durable "
    "EXITED state converges; adopt_run does not restore a dead run."
)
_ADOPT_NOT_DISPATCHABLE_HINT = (
    "Instance bindings do not survive a daemon restart. This run admits "
    "stop_run and relaunch; mutations cannot dispatch until then."
)
# fb-20260904-025733-d60f: states an ownerless run may be adopted from. A stop
# that degraded leaves the run UNRECONCILED with no owner and, when the guard
# could not end it, with a live process of its own: stop_run demands RUNNING
# with an owner, reap_dead_run answers process_alive while the process lives,
# and admin_reconcile is a TTY-confirmed admin path, so the box stayed occupied
# with no verb a session could use. Adopting it re-enters the machine that
# already exists: UNRECONCILED -> adopt -> RUNNING -> stop -> EXITED. The
# safety discriminator does not move: still a lease, still no record the guard
# cannot vouch for, still at least one owned process.
_ADOPTABLE_STATES = frozenset({"RUNNING_IDLE", "UNRECONCILED"})
# fb-20260904-025027-8f76 (part c): a process that has already exited can
# outlive its own row in the host UDP table. start_run reads that table again
# right before the launcher, and a pid that is no longer a registered process
# reads there as a foreign holder, so retiring the record while the socket
# lives would leave the role dead AND unlaunched - worse than the state it
# started from. The wait for the release is bounded by contract, never by an
# open sleep: at most _ROLE_RELEASE_TRIES probes spaced
# _ROLE_RELEASE_INTERVAL_S apart, then port_still_held and nothing is retired.
_ROLE_RELEASE_TRIES = 10
_ROLE_RELEASE_INTERVAL_S = 0.25
_PORT_STILL_HELD_HINT = (
    "port_still_held: the superseded process ended but the host UDP socket "
    "table still lists its pid. Nothing was retired and nothing was launched; "
    "repeat the same call once the socket is released."
)
# fb-20260904-200816-79e2 / H-A2-2. The extension gate decides in the MCP server
# process and the kill happens here, after composing the sealed request, opening
# the launcher, starting app.pyz and two broker round trips. A4 measured that
# window on the durable audit: n=26, min 0,47 s, median 7,00 s, max 29,16 s --
# the same order as PEER_STALE_S. The bound below is twice the measured maximum,
# so a legitimate call never trips it while a request replayed minutes later does.
_REPLACE_WITNESS_MAX_AGE_S = 60.0
# One-sided tolerance for a witness stamped a hair in the future by clock skew
# between the two processes. Anything beyond it is not skew.
_REPLACE_WITNESS_SKEW_S = 2.0
_REPLACE_WITNESS_HINTS = {
    "replace_witness_missing": (
        "replace_witness_missing: superseding a live client needs the witness "
        "of the gate that authorised it, carried in the sealed request. Nothing "
        "was terminated and nothing was launched. A launcher bundle older than "
        "this daemon does not send it: rebuild and reinstall app.pyz."
    ),
    "replace_witness_stale": (
        "replace_witness_stale: the gate read the bridge too long ago for its "
        "verdict to still stand. Nothing was terminated and nothing was "
        "launched; repeat the same call."
    ),
    "client_polling_since_decision": (
        "client_polling_since_decision: the client polled the bridge again "
        "after the gate decided it had stopped. Nothing was terminated and "
        "nothing was launched: the client is alive and serving."
    ),
    "bridge_state_unreadable": (
        "bridge_state_unreadable: the bridge state carries no usable evidence "
        "about this client, and no evidence does not authorise ending a live "
        "process. Nothing was terminated and nothing was launched."
    ),
}
_STATUS_SNAPSHOT_TRIES = 3
_RECOVERY_REPAIR_STATES = frozenset(
    {"STARTING", "RUNNING", "RUNNING_IDLE", "STOPPING", "UNRECONCILED"}
)


def _derive_box(snapshot: _BoxSnapshot, probes: _BoxProbes) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    for run in snapshot.runs:
        if run.state not in _ACTIVE_STATES:
            continue
        owner = run.owner_session_id
        activity_state, last_activity_age_s = _activity_from_snapshot(
            snapshot, run.run_id
        )
        row: dict[str, object] = {
            "run_id": run.run_id,
            "mod": run.mod,
            "label": run.label,
            "age_s": round(_run_age_s(run, snapshot.clock), 3),
            "owner_session": owner[:12] if isinstance(owner, str) and owner else None,
            "state": run.state,
            "activity_state": activity_state,
            "last_activity_age_s": last_activity_age_s,
        }
        row.update(_generation_projection(run, snapshot.daemon_generation))
        runs.append(row)
    scans_known = probes.scan_known and probes.port_scan_known
    occupied = True if not scans_known else bool(runs or probes.foreign)
    return {
        "occupied": occupied,
        "runs": runs,
        "foreign": [dict(item) for item in probes.foreign],
        "ports_in_use": list(probes.ports_in_use),
        "queue": [],
        "scan_known": probes.scan_known,
        "port_scan_known": probes.port_scan_known,
        "port_scan_reason": probes.port_scan_reason,
        # Ports held by processes that are not managed runs, whatever their
        # image: the launch diagnosis names the requested port from here.
        "foreign_ports": list(probes.foreign_ports),
    }


def _wait_hint(box: object) -> str:
    if not isinstance(box, dict):
        return _ACTIVE_RUN_WAIT_HINT
    if box.get("port_scan_known") is False:
        return _PORT_SCAN_UNKNOWN_HINT
    claimed_s = box.get("claimed_s")
    if (
        isinstance(claimed_s, (int, float))
        and not isinstance(claimed_s, bool)
        and claimed_s >= 0
    ):
        return f"retry with wait_for_box_s=<n> (claimed for {float(claimed_s):.0f}s)"
    return _ACTIVE_RUN_WAIT_HINT


def _caller_owns_run(item: dict[str, object], caller_session: str | None) -> bool:
    if item.get("state") == "RUNNING_IDLE":
        return True
    owner = item.get("owner_session")
    if not isinstance(owner, str) or not owner:
        return False
    if not isinstance(caller_session, str) or not caller_session:
        return False
    return owner in {caller_session, caller_session[:12]}


def occupancy_error_fields(
    box: object, *, caller_session: str | None = None
) -> dict[str, object]:
    """Public active_run_exists extras derived from a box snapshot."""

    payload: dict[str, object] = {
        "occupied_by_run_id": None,
        "mod": "",
        "label": "",
        "age_s": 0.0,
        "foreign": False,
        "hint": _wait_hint(box),
    }
    if not isinstance(box, dict):
        payload["foreign"] = True
        return payload
    runs = box.get("runs")
    if isinstance(runs, list):
        for item in runs:
            if not isinstance(item, dict):
                continue
            run_id = item.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                continue
            payload["occupied_by_run_id"] = run_id
            payload["mod"] = item.get("mod") if isinstance(item.get("mod"), str) else ""
            payload["label"] = (
                item.get("label") if isinstance(item.get("label"), str) else ""
            )
            age = item.get("age_s")
            payload["age_s"] = (
                float(age)
                if isinstance(age, (int, float)) and not isinstance(age, bool)
                else 0.0
            )
            payload["activity_state"] = item.get("activity_state")
            payload["last_activity_age_s"] = item.get("last_activity_age_s")
            payload["foreign"] = False
            state = item.get("state")
            if state in {"STARTING", "STOPPING"}:
                payload["hint"] = _wait_hint(box)
            elif state == "UNRECONCILED":
                payload["hint"] = (
                    "this run is unreconciled; waiting will not free the box"
                )
            elif _caller_owns_run(item, caller_session) and state in {
                "RUNNING",
                "RUNNING_IDLE",
            }:
                payload["hint"] = _ACTIVE_RUN_STOP_HINT.format(run_id=run_id)
            else:
                payload["hint"] = _wait_hint(box)
            return payload
    foreign = box.get("foreign")
    if isinstance(foreign, list):
        for item in foreign:
            if not isinstance(item, dict):
                continue
            mods = item.get("mods")
            port = item.get("port")
            has_port = (
                isinstance(port, int)
                and not isinstance(port, bool)
                and 1 <= port <= 65535
            )
            has_mods = isinstance(mods, list) and any(
                isinstance(part, str) and part for part in mods
            )
            if not has_port and not has_mods:
                continue
            if has_mods:
                payload["mod"] = ";".join(
                    part for part in mods if isinstance(part, str) and part
                )
            payload["foreign"] = True
            payload["port"] = port if has_port else None
            payload["hint"] = _wait_hint(box)
            return payload
    if box.get("occupied") is True:
        payload["foreign"] = True
    return payload


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    creation_time_utc: str
    executable_sha256: str
    command_line_sha256: str
    role: str
    identity_scheme: str = "legacy-wmi-v1"

    @classmethod
    def from_payload(cls, value: object) -> "ProcessRecord":
        if not isinstance(value, dict):
            raise ValueError("invalid_process_record")
        record = cls(
            value.get("pid"),
            value.get("creation_time_utc"),
            value.get("executable_sha256"),
            value.get("command_line_sha256"),
            value.get("role"),
            value.get("identity_scheme", "legacy-wmi-v1"),
        )
        record.validate()
        return record

    def validate(self) -> None:
        if not isinstance(self.pid, int) or isinstance(self.pid, bool) or self.pid <= 0:
            raise ValueError("invalid_process_record")
        if not isinstance(self.creation_time_utc, str) or not self.creation_time_utc:
            raise ValueError("invalid_process_record")
        for value in (self.executable_sha256, self.command_line_sha256):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in _HEX for char in value.casefold())
            ):
                raise ValueError("invalid_process_record")
        if not isinstance(self.role, str) or not self.role:
            raise ValueError("invalid_process_record")
        if self.identity_scheme not in _IDENTITY_SCHEMES:
            raise ValueError("invalid_process_record")


@dataclass
class RunRecord:
    run_id: str
    owner_session_id: str | None
    owner_lease_id: str | None
    state: str
    label: str
    mod: str
    profiles: str
    mission: str
    processes: list[ProcessRecord]
    launch_operation_id: str | None = None
    launch_request_sha256: str | None = None
    launch_acknowledged: bool = True
    daemon_generation_at_launch: str | None = None

    @classmethod
    def from_payload(cls, value: object) -> "RunRecord":
        if not isinstance(value, dict):
            raise ValueError("invalid_run_record")
        raw_processes = value.get("processes")
        if not isinstance(raw_processes, list):
            raise ValueError("invalid_run_record")
        run = cls(
            value.get("run_id"),
            value.get("owner_session_id"),
            value.get("owner_lease_id"),
            value.get("state"),
            value.get("label"),
            value.get("mod"),
            value.get("profiles"),
            value.get("mission"),
            [ProcessRecord.from_payload(item) for item in raw_processes],
            value.get("launch_operation_id"),
            value.get("launch_request_sha256"),
            value.get("launch_acknowledged", True),
            value.get("daemon_generation_at_launch"),
        )
        run.validate()
        return run

    def validate(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("invalid_run_record")
        if self.state not in RUN_STATES:
            raise ValueError("invalid_run_record")
        if (self.owner_session_id is None) != (self.owner_lease_id is None):
            raise ValueError("invalid_run_record")
        if self.owner_session_id is not None and (
            not self.owner_session_id or not self.owner_lease_id
        ):
            raise ValueError("invalid_run_record")
        if self.state == "EXITED" and (
            self.owner_session_id is not None or self.processes
        ):
            raise ValueError("invalid_run_record")
        if self.state == "RUNNING" and (
            self.owner_session_id is None or not self.processes
        ):
            raise ValueError("invalid_run_record")
        if self.state == "RUNNING_IDLE" and (
            self.owner_session_id is not None or not self.processes
        ):
            raise ValueError("invalid_run_record")
        for value in (self.owner_session_id, self.owner_lease_id):
            if value is not None and not isinstance(value, str):
                raise ValueError("invalid_run_record")
        for value in (self.label, self.mod, self.profiles, self.mission):
            if not isinstance(value, str):
                raise ValueError("invalid_run_record")
        for record in self.processes:
            record.validate()
        if (self.launch_operation_id is None) != (
            self.launch_request_sha256 is None
        ):
            raise ValueError("invalid_run_record")
        if self.launch_operation_id is None:
            if self.launch_acknowledged is not True:
                raise ValueError("invalid_run_record")
        elif (
            not _valid_uuid4(self.launch_operation_id)
            or not _valid_sha256(self.launch_request_sha256)
            or not isinstance(self.launch_acknowledged, bool)
        ):
            raise ValueError("invalid_run_record")
        if self.daemon_generation_at_launch is not None and not isinstance(
            self.daemon_generation_at_launch, str
        ):
            raise ValueError("invalid_run_record")


@dataclass(frozen=True)
class RetiredRunDiagnostic:
    run_id: str
    daemon_generation_at_launch: str | None
    daemon_generation_current: str
    generation_changed: bool | None
    event: str
    reason: str
    decision: str
    state: str
    launch_operation_id: str | None = None


class RunManifestStore:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        checkpoint: Callable[[bytes], object] | None = None,
        read_only: bool = False,
    ) -> None:
        self.paths = paths
        self._lock = threading.RLock()
        self._runs: dict[str, RunRecord] = {}
        self._legacy_gate_complete = False
        self._checkpoint = checkpoint
        self._read_only = bool(read_only)
        self._load()
        if self._read_only:
            return
        pruned = self._prune_exited_on_load()
        if self._checkpoint is not None and not pruned:
            raw = (
                self.paths.runs_path.read_bytes()
                if self.paths.runs_path.exists()
                else b'{"version":1,"runs":[]}\n'
            )
            self._checkpoint(raw)

    @classmethod
    def empty_for_recovery(
        cls,
        paths: RuntimePaths,
        *,
        checkpoint: Callable[[bytes], object] | None = None,
    ) -> "RunManifestStore":
        store = cls.__new__(cls)
        store.paths = paths
        store._lock = threading.RLock()
        store._runs = {}
        store._legacy_gate_complete = True
        store._checkpoint = checkpoint
        store._read_only = False
        return store

    def _require_writable(self) -> None:
        if self._read_only:
            raise RuntimeError("run_manifest_store_read_only")

    def _create_preprune_backup(self, raw: bytes) -> bool:
        """Write a unique pre-prune backup; never overwrite an existing one.

        Slot names: runs.json.bak-preprune, then .bak-preprune.2 .. .10.
        Returns False (fail-closed, no prune) if every slot is occupied or I/O fails.
        """
        base_name = self.paths.runs_path.name + ".bak-preprune"
        slot_names = [base_name] + [f"{base_name}.{index}" for index in range(2, 11)]
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        for slot_name in slot_names:
            backup_path = self.paths.runs_path.with_name(slot_name)
            try:
                descriptor = os.open(backup_path, flags, 0o600)
            except FileExistsError:
                continue
            except OSError:
                return False
            try:
                with os.fdopen(descriptor, "wb") as backup:
                    if backup.write(raw) != len(raw):
                        raise OSError("preprune_backup_short_write")
                    backup.flush()
                    os.fsync(backup.fileno())
            except OSError:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                try:
                    backup_path.unlink()
                except OSError:
                    pass
                return False
            return True
        return False

    def _prune_exited_on_load(self) -> bool:
        retired = {
            run_id: run
            for run_id, run in self._runs.items()
            if run.state == "EXITED"
        }
        if not retired:
            return False
        try:
            original_raw = self.paths.runs_path.read_bytes()
        except OSError:
            return False
        if not self._create_preprune_backup(original_raw):
            return False
        previous = self._runs
        self._runs = {
            run_id: run
            for run_id, run in self._runs.items()
            if run.state != "EXITED"
        }
        try:
            self._persist_locked()
        except Exception:
            self._runs = previous
            raise
        return True

    def _load(self) -> None:
        if not self.paths.runs_path.exists():
            return
        try:
            payload = json.loads(self.paths.runs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_run_manifest") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("invalid_run_manifest")
        runs = payload.get("runs")
        if not isinstance(runs, list):
            raise ValueError("invalid_run_manifest")
        try:
            for item in runs:
                run = RunRecord.from_payload(item)
                if run.run_id in self._runs:
                    raise ValueError("invalid_run_manifest")
                self._runs[run.run_id] = run
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_run_manifest") from exc

    def _clone(self, run: RunRecord) -> RunRecord:
        return RunRecord.from_payload(dataclasses.asdict(run))

    def _persist_locked(self) -> None:
        self._require_writable()
        payload = {
            "version": 1,
            "runs": [dataclasses.asdict(self._runs[key]) for key in sorted(self._runs)],
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ) + b"\n"
        previous = (
            self.paths.runs_path.read_bytes()
            if self.paths.runs_path.exists()
            else b'{"version":1,"runs":[]}\n'
        )
        atomic_write_bytes(self.paths.runs_path, raw)
        if self._checkpoint is not None:
            try:
                self._checkpoint(raw)
            except Exception:
                atomic_write_bytes(self.paths.runs_path, previous)
                try:
                    self._checkpoint(previous)
                except Exception:
                    pass
                raise

    def list_runs(self) -> list[RunRecord]:
        with self._lock:
            return [self._clone(self._runs[key]) for key in sorted(self._runs)]

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
            return self._clone(run) if run is not None else None

    def add(self, run: RunRecord) -> None:
        self._require_writable()
        run.validate()
        with self._lock:
            if run.run_id in self._runs:
                raise ValueError("run_exists")
            self._runs[run.run_id] = self._clone(run)
            try:
                self._persist_locked()
            except Exception:
                self._runs.pop(run.run_id, None)
                raise
            if self._active_legacy(run):
                self._legacy_gate_complete = False

    def replace(self, run: RunRecord) -> None:
        self._require_writable()
        run.validate()
        with self._lock:
            if run.run_id not in self._runs:
                raise ValueError("run_not_found")
            previous = self._runs[run.run_id]
            self._runs[run.run_id] = self._clone(run)
            try:
                self._persist_locked()
            except Exception:
                self._runs[run.run_id] = previous
                raise
            if self._active_legacy(run):
                self._legacy_gate_complete = False

    @staticmethod
    def _active_legacy(run: RunRecord) -> bool:
        return run.state in _ACTIVE_STATES and any(
            record.identity_scheme == "legacy-wmi-v1" for record in run.processes
        )

    def quarantine_legacy_active(
        self, audit: Callable[[dict[str, object]], object]
    ) -> list[str]:
        """Audit and durably quarantine active records that predate identity v2."""

        self._require_writable()
        if not callable(audit):
            raise TypeError("audit_callback_required")
        with self._lock:
            if self._legacy_gate_complete:
                return []
            targets = [
                self._runs[run_id]
                for run_id in sorted(self._runs)
                if self._active_legacy(self._runs[run_id])
            ]
            if not targets:
                self._legacy_gate_complete = True
                return []
            for run in targets:
                event = {
                    "event": "legacy_identity_quarantined",
                    "reason": "legacy_process_identity_scheme",
                    "duration_s": 0.0,
                    "decision": "manual_reconciliation_required",
                    "run_id": run.run_id,
                    "previous_state": run.state,
                    "process_count": len(run.processes),
                }
                try:
                    audited = audit(event)
                except Exception as error:
                    raise RuntimeError("legacy_identity_audit_failed") from error
                if audited is False:
                    raise RuntimeError("legacy_identity_audit_failed")

            previous = dict(self._runs)
            changed: list[str] = []
            for current in targets:
                if (
                    current.state == "UNRECONCILED"
                    and current.owner_session_id is None
                    and current.owner_lease_id is None
                ):
                    continue
                run = self._clone(current)
                run.state = "UNRECONCILED"
                run.owner_session_id = None
                run.owner_lease_id = None
                self._runs[run.run_id] = run
                changed.append(run.run_id)
            try:
                if changed:
                    self._persist_locked()
            except Exception:
                self._runs = previous
                raise
            self._legacy_gate_complete = True
            return changed

    def release_owner(self, session_id: str, lease_id: str) -> list[str]:
        self._require_writable()
        with self._lock:
            changed: list[str] = []
            previous = dict(self._runs)
            for run_id, current in list(self._runs.items()):
                if (
                    current.owner_session_id == session_id
                    and current.owner_lease_id == lease_id
                    and current.state == "RUNNING"
                ):
                    run = self._clone(current)
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    run.state = "RUNNING_IDLE"
                    self._runs[run_id] = run
                    changed.append(run_id)
            if changed:
                try:
                    self._persist_locked()
                except Exception:
                    self._runs = previous
                    raise
            return changed

    def release_all_running_owners(self) -> list[str]:
        """Atomically orphan every persisted RUNNING run after daemon restart."""

        self._require_writable()
        with self._lock:
            changed: list[str] = []
            previous = dict(self._runs)
            for run_id, current in list(self._runs.items()):
                if current.state == "RUNNING" and current.owner_session_id is not None:
                    run = self._clone(current)
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    run.state = "RUNNING_IDLE"
                    self._runs[run_id] = run
                    changed.append(run_id)
            if changed:
                try:
                    self._persist_locked()
                except Exception:
                    self._runs = previous
                    raise
            return changed

    def recover_after_restart(self) -> dict[str, list[str]]:
        """Atomically release stable owners and quarantine interrupted mutations."""

        self._require_writable()
        with self._lock:
            released: list[str] = []
            unreconciled: list[str] = []
            previous = dict(self._runs)
            for run_id, current in list(self._runs.items()):
                run = self._clone(current)
                if run.state == "RUNNING" and run.owner_session_id is not None:
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    run.state = "RUNNING_IDLE"
                    released.append(run_id)
                elif run.state in {"STARTING", "STOPPING"}:
                    run.state = "UNRECONCILED"
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    unreconciled.append(run_id)
                else:
                    continue
                self._runs[run_id] = run
            if released or unreconciled:
                try:
                    self._persist_locked()
                except Exception:
                    self._runs = previous
                    raise
            return {
                "released": sorted(released),
                "unreconciled": sorted(unreconciled),
            }


AuditCallback = Callable[[dict[str, object]], object]
RetailProbe = Callable[[], dict[str, object]]
Launcher = Callable[[list[str], str, str], object]
RecoveryFaultArm = Callable[[RunRecord, str], object]
_T = TypeVar("_T")


class ProcessLifecycle:
    """Every transition into RUNNING_IDLE goes through the fence; the poll re-validates after re-acquiring the lock."""

    def __init__(
        self,
        *,
        coordinator: SessionCoordinator,
        manifest: RunManifestStore,
        audit: AuditCallback,
        guard: object,
        retail_probe: RetailProbe | None,
        game_path: Path,
        diag_probe: RetailProbe | None = None,
        port_probe: RetailProbe | None = None,
        launcher: Launcher | None = None,
        id_fn: Callable[[], str] | None = None,
        recovery_fault_arm: RecoveryFaultArm | None = None,
        argv_of: Callable[[int], list[str] | None] | None = None,
        bindings: object | None = None,
        # 79e2: reads the bridge peer rows to revalidate a client replacement at
        # the instant of the kill. Read-only; None means "no answer", which is a
        # refusal, never a licence.
        bridge_probe: object | None = None,
        daemon_generation: str | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.manifest = manifest
        self.audit = audit
        self.guard = guard
        self.retail_probe = retail_probe
        self.diag_probe = diag_probe
        self.port_probe = port_probe
        self.game_path = Path(game_path).resolve()
        self.launcher = launcher or self._launch
        self.id_fn = id_fn or (lambda: uuid.uuid4().hex)
        self.recovery_fault_arm = recovery_fault_arm
        self.argv_of = argv_of or _default_argv_of
        self.bindings = bindings
        self.bridge_probe = bridge_probe
        self.daemon_generation = (
            daemon_generation if isinstance(daemon_generation, str) else ""
        )
        self._box_cache: tuple[float, int, _BoxProbes] | None = None
        self._box_revision = 0
        self._operation_lock = threading.RLock()
        self._command_id = -1
        self._last_start_error: str | None = None
        self._activity_lock = threading.Lock()
        self._last_activity: dict[tuple[str, str], float] = {}
        self._activity_unknown: set[tuple[str, str]] = set()
        self._activity_tombstone: dict[tuple[str, str], float] = {}
        self._compensating_runs: set[tuple[str, str]] = set()
        # Retired-run diagnostics live in daemon memory and start empty after a
        # restart; the audit file is never read to reconstruct them.
        self._retired_diagnostics: deque[RetiredRunDiagnostic] = deque(maxlen=32)
        # Bound of the socket-release confirmation (P-L2.c). Instance state so a
        # test can shorten it without patching the module.
        self._role_release_tries = _ROLE_RELEASE_TRIES
        self._role_release_interval_s = _ROLE_RELEASE_INTERVAL_S

    def _prepare_instance(
        self,
        run_id: str,
        role: str,
        profiles_dir: str,
        replacing_role: bool,
    ) -> tuple[str | None, str | None]:
        bindings = self.bindings
        if bindings is None:
            return None, None
        try:
            if replacing_role:
                bindings.retire_role(run_id, role, "replace-role")
            minted = bindings.prepare(run_id, role, profiles_dir)
        except BindingPrepareError as exc:
            return None, exc.code
        except Exception:
            return None, "instance_config_missing"
        if not isinstance(minted, str) or not minted:
            return None, "instance_config_missing"
        return minted, None

    def _confirm_instance(self, minted: str | None, record: ProcessRecord) -> None:
        bindings = self.bindings
        if bindings is None or minted is None:
            return
        bindings.confirm(minted, record)

    def _retire_minted(
        self, run_id: str, role: str, minted: str | None, reason: str
    ) -> None:
        bindings = self.bindings
        if bindings is None or minted is None:
            return
        bindings.retire_role(run_id, role, reason)

    def _retire_run_bindings(self, run_id: str, reason: str) -> None:
        bindings = self.bindings
        if bindings is None:
            return
        bindings.retire_run(run_id, reason)

    def _adopt_dispatchable(self, run_id: str) -> bool:
        # Caller holds _operation_lock. ServerState.run_has_bound_binding
        # takes ServerState._lock; never the inverse.
        bindings = self.bindings
        if bindings is None:
            return False
        checker = getattr(bindings, "run_has_bound_binding", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(run_id))
        except Exception:
            return False

    def _current_generation(self) -> str:
        return self.daemon_generation if isinstance(self.daemon_generation, str) else ""

    def _retire_run_diagnostic(
        self,
        run: RunRecord,
        event: str,
        reason: str,
        decision: str,
        state: str = "EXITED",
    ) -> None:
        key = (run.run_id, run.launch_operation_id)
        fields = _generation_projection(run, self._current_generation())
        entry = RetiredRunDiagnostic(
            run_id=run.run_id,
            daemon_generation_at_launch=fields["daemon_generation_at_launch"],
            daemon_generation_current=fields["daemon_generation_current"],
            generation_changed=fields["generation_changed"],
            event=event,
            reason=reason,
            decision=decision,
            state=state,
            launch_operation_id=run.launch_operation_id,
        )
        with self._activity_lock:
            for existing in self._retired_diagnostics:
                if (existing.run_id, existing.launch_operation_id) == key:
                    return
            self._retired_diagnostics.appendleft(entry)

    def _commit_retirement(
        self, run: RunRecord, event: str, reason: str, decision: str
    ) -> bool:
        # Binding retirement precedes the durable transition; the diagnostic
        # follows a successful persist. stop_run may already have retired.
        binding_reason = _BINDING_REASON_BY_EVENT.get(event, reason)
        self._retire_run_bindings(run.run_id, binding_reason)
        try:
            self.manifest.replace(run)
        except Exception:
            return False
        self._best_effort_post_persist_retirement(run, event, reason, decision)
        return True

    def _best_effort_post_persist_retirement(
        self, run: RunRecord, event: str, reason: str, decision: str
    ) -> None:
        try:
            self._invalidate_box_cache()
        except Exception:
            self._note_post_persist_fault(run.run_id, "invalidate_box_cache")
        try:
            self._retire_run_diagnostic(run, event, reason, decision, "EXITED")
        except Exception:
            self._note_post_persist_fault(run.run_id, "diagnostic")
        try:
            self._seal_terminal(run.run_id, time.time())
        except Exception:
            self._note_post_persist_fault(run.run_id, "seal_terminal")
            try:
                self._unfence_runs([run.run_id])
            except Exception:
                pass

    def _note_post_persist_fault(self, run_id: str, reason: str) -> None:
        try:
            self._audit(
                "lifecycle_terminal_post_persist",
                None,
                reason,
                "degraded",
                run_id=run_id,
            )
        except Exception:
            pass

    def _projected_run(self, run: RunRecord) -> dict[str, object]:
        row = dataclasses.asdict(run)
        row.update(_generation_projection(run, self._current_generation()))
        return row

    def _publish_retired_diagnostics(self) -> list[dict[str, object]]:
        with self._activity_lock:
            entries = list(self._retired_diagnostics)
        published: list[dict[str, object]] = []
        for item in entries:
            raw = dataclasses.asdict(item)
            published.append({key: raw[key] for key in _DIAGNOSTIC_PUBLIC_KEYS})
        return published

    def _status_snapshot(
        self,
    ) -> tuple[list[RunRecord], list[dict[str, object]]]:
        # Never takes _operation_lock: /status is the health discriminator and
        # must not wait out a stop. Revision stamp + bounded retry, then drop
        # a diagnostic whose run still has a non-terminal row in this payload.
        runs: list[RunRecord] = []
        diagnostics: list[dict[str, object]] = []
        for _ in range(_STATUS_SNAPSHOT_TRIES):
            with self._activity_lock:
                before = self._box_revision
            runs = list(self.manifest.list_runs())
            diagnostics = self._publish_retired_diagnostics()
            with self._activity_lock:
                after = self._box_revision
            if before == after:
                break
        live = {run.run_id for run in runs if run.state != "EXITED"}
        diagnostics = [
            item
            for item in diagnostics
            if item.get("run_id") not in live
        ]
        return runs, diagnostics

    def _launch(self, argv: list[str], cwd: str, window_style: str) -> object:
        kwargs: dict[str, object] = {"cwd": cwd, "close_fds": True}
        if os.name == "nt" and window_style == "hidden":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return subprocess.Popen(argv, **kwargs)

    @staticmethod
    def _error(error: str, status: int) -> dict[str, object]:
        return {"error": error, "_http_status": status}

    def _audit(self, event: str, client: ClientIdentity | None, reason: str, decision: str, **extra: object) -> bool:
        payload: dict[str, object] = {
            "event": event,
            "reason": reason,
            "duration_s": 0.0,
            "decision": decision,
        }
        if client is not None:
            payload["client"] = client.public_payload()
        payload.update(extra)
        try:
            return self.audit(payload) is not False
        except Exception:
            return False

    def _activity_key(self, run_id: str) -> tuple[str, str]:
        generation = self.daemon_generation if isinstance(self.daemon_generation, str) else ""
        return (generation, run_id)

    def _bump_box_revision_locked(self) -> None:
        # Caller holds _activity_lock. start/stop/reap take _operation_lock
        # then _activity_lock; reverse order would deadlock.
        self._box_revision += 1
        self._box_cache = None

    def _invalidate_box_cache(self) -> None:
        with self._activity_lock:
            self._bump_box_revision_locked()

    def _capture_start_activity(
        self, run: RunRecord, *, launched: ProcessRecord | None = None
    ) -> None:
        records = [launched] if launched is not None else list(run.processes)
        stamps = [
            stamp
            for stamp in (
                _utc_epoch(record.creation_time_utc) for record in records
            )
            if stamp is not None
        ]
        key = self._activity_key(run.run_id)
        with self._activity_lock:
            if stamps and key not in self._activity_unknown:
                if launched is not None or key not in self._last_activity:
                    # A process launched by this daemon is a stamp of its own:
                    # max with any earlier seal (extension), never import a
                    # pre-daemon basal when this daemon has not sealed yet.
                    self._seal_activity_locked(key, max(stamps))
            self._bump_box_revision_locked()

    def record_command_activity(self, run_id: str, *, now: float | None = None) -> bool:
        if not isinstance(run_id, str) or not run_id:
            return False
        epoch = time.time() if now is None else float(now)
        return self._write_command_activity(run_id, epoch, reason="enqueue_accepted")

    def record_box_command_activity(
        self,
        *,
        now: float | None = None,
        owner_session: str | None = None,
        run_id: str | None = None,
    ) -> None:
        epoch = time.time() if now is None else float(now)
        if isinstance(run_id, str) and run_id:
            try:
                self.record_command_activity(run_id, now=epoch)
            except Exception:
                pass
            return
        # No accredited binding. Do not credit by owner or by cardinality:
        # either false-attributes freshness or leaves a false stale on a live
        # session. Forget stamps so occupancy publishes unknown.
        _ = owner_session
        try:
            runs = list(self.manifest.list_runs())
        except Exception:
            return
        active = [run for run in runs if run.state in _ACTIVE_STATES]
        with self._activity_lock:
            for run in active:
                key = self._activity_key(run.run_id)
                current = self._last_activity.get(key)
                if current is not None and current <= epoch:
                    self._last_activity.pop(key, None)
                self._raise_activity_tombstone_locked(key, epoch)
            self._bump_box_revision_locked()

    def tombstone_run_activity(self, run_id: str, *, now: float | None = None) -> None:
        if not isinstance(run_id, str) or not run_id:
            return
        epoch = time.time() if now is None else float(now)
        key = self._activity_key(run_id)
        with self._activity_lock:
            self._raise_activity_tombstone_locked(key, epoch)
            current = self._last_activity.get(key)
            if current is not None and current <= epoch:
                self._last_activity.pop(key, None)
            self._bump_box_revision_locked()

    def _forget_run_residues(self, run_id: str) -> None:
        self._seal_terminal(run_id, time.time())

    def _seal_terminal(self, run_id: str, at: float) -> None:
        # Drop data, unknown, and compensation; keep (or recreate) a frontier
        # later than any stamp that was acceptable at retirement so a writer
        # that accepted before this seal cannot reappear after it.
        epoch = float(at)
        with self._activity_lock:
            keys = [
                key
                for key in (
                    set(self._last_activity)
                    | set(self._activity_tombstone)
                    | set(self._activity_unknown)
                    | set(self._compensating_runs)
                )
                if key[1] == run_id
            ]
            current = self._activity_key(run_id)
            if current not in keys:
                keys.append(current)
            frontier = epoch
            for key in keys:
                stamp = self._last_activity.get(key)
                if stamp is not None and stamp > frontier:
                    frontier = stamp
                tomb = self._activity_tombstone.get(key)
                if tomb is not None and tomb > frontier:
                    frontier = tomb
            for key in keys:
                self._last_activity.pop(key, None)
                self._activity_unknown.discard(key)
                self._compensating_runs.discard(key)
                self._raise_activity_tombstone_locked(key, frontier)
            self._bump_box_revision_locked()
        bindings = self.bindings
        unfence = getattr(bindings, "unfence_runs", None)
        if callable(unfence):
            try:
                unfence([run_id])
            except Exception:
                pass

    def _raise_activity_tombstone_locked(
        self, key: tuple[str, str], epoch: float
    ) -> None:
        current = self._activity_tombstone.get(key)
        if current is None or epoch > current:
            self._activity_tombstone[key] = epoch

    def _seal_activity_locked(self, key: tuple[str, str], epoch: float) -> None:
        # Unique stamp writer. Sticky unknown still wins over everything.
        # A compensating run discards credit and basal until the rollback
        # tombstone is the frontier.
        if key in self._compensating_runs:
            return
        tombstone = self._activity_tombstone.get(key)
        if tombstone is not None and epoch <= tombstone:
            return
        current = self._last_activity.get(key)
        if current is None or epoch >= current:
            self._last_activity[key] = epoch

    def _write_command_activity(self, run_id: str, epoch: float, *, reason: str) -> bool:
        key = self._activity_key(run_id)
        ok = self._audit(
            "run_command_activity",
            None,
            reason,
            "recorded",
            run_id=run_id,
            activity_epoch=epoch,
        )
        with self._activity_lock:
            if not ok:
                tombstone = self._activity_tombstone.get(key)
                if tombstone is None or epoch > tombstone:
                    self._activity_unknown.add(key)
            elif key not in self._activity_unknown:
                self._seal_activity_locked(key, epoch)
            self._bump_box_revision_locked()
        return ok

    def _activity_for_run_id(
        self, run_id: str, clock: float
    ) -> tuple[str, float | None]:
        key = self._activity_key(run_id)
        with self._activity_lock:
            if key in self._activity_unknown or key in self._compensating_runs:
                return "unknown", None
            stamp = self._last_activity.get(key)
            tombstone = self._activity_tombstone.get(key)
        if stamp is None:
            return "unknown", None
        if tombstone is not None and stamp <= tombstone:
            return "unknown", None
        if stamp > clock:
            return "unknown", None
        age = clock - stamp
        if age <= _ACTIVITY_STALE_S:
            return "recent", round(age, 3)
        return "stale", round(age, 3)

    def _run_activity(self, run: RunRecord, clock: float) -> tuple[str, float | None]:
        return self._activity_for_run_id(run.run_id, clock)

    def _forget_attempt_activity(
        self,
        run_id: str,
        attempt_started_at: float,
        *,
        rolled_back_at: float,
    ) -> None:
        key = self._activity_key(run_id)
        with self._activity_lock:
            current = self._last_activity.get(key)
            if (
                current is not None
                and attempt_started_at <= current <= rolled_back_at
            ):
                self._last_activity.pop(key, None)
            self._raise_activity_tombstone_locked(key, rolled_back_at)
            self._bump_box_revision_locked()

    @contextmanager
    def _compensating(self, run_id: str):
        key = self._activity_key(run_id)
        with self._activity_lock:
            self._compensating_runs.add(key)
        try:
            yield
        finally:
            with self._activity_lock:
                self._compensating_runs.discard(key)

    def _legacy_identity_error(self) -> dict[str, object] | None:
        try:
            self.manifest.quarantine_legacy_active(self.audit)
        except Exception:
            return self._error("legacy_identity_transition_failed", 503)
        return None

    def _require_legacy_identity_safe(self) -> None:
        if self._legacy_identity_error() is not None:
            raise RuntimeError("legacy_identity_transition_failed")

    def _quarantined(self) -> bool:
        if self.retail_probe is None:
            return True
        try:
            result = self.retail_probe()
        except Exception:
            return True
        return (
            not isinstance(result, dict)
            or result.get("known") is not True
            or not isinstance(result.get("processes"), list)
            or bool(result.get("processes"))
        )

    def _authorize(self, client: ClientIdentity, token: str | None, command: str):
        decision = self.coordinator.authorize(client, token, command, 15.0)
        if not decision.allowed:
            return decision, self._error(decision.error, decision.http_status)
        return decision, None

    @staticmethod
    def _authority(
        decision: AuthorizationDecision,
    ) -> tuple[str, str, str] | None:
        if (
            decision.owner_session_id is None
            or decision.lease_id is None
            or decision.reservation_id is None
        ):
            return None
        return (
            decision.owner_session_id,
            decision.lease_id,
            decision.reservation_id,
        )

    def _reject_reserved(
        self,
        authority: tuple[str, str, str],
        command: str,
        reason: str,
        status: int = 409,
    ) -> dict[str, object]:
        decision = self.coordinator.reject_reservation(
            authority[0], authority[1], authority[2], reason, status
        )
        result = self._error(decision.error, decision.http_status)
        if decision.cleanup_degraded:
            result["cleanup_degraded"] = list(decision.cleanup_degraded)
        return result

    def _commit_reserved(
        self, authority: tuple[str, str, str], command: str
    ) -> int | None:
        command_id = self._command_id
        self._command_id -= 1
        if not self.coordinator.commit_authorization(
            authority[0],
            authority[1],
            authority[2],
            command_id,
            command,
            {},
        ):
            return None
        return command_id

    def _finish_committed(
        self, authority: tuple[str, str, str], command_id: int
    ) -> None:
        self.coordinator.finish_operation_exact(
            authority[0], authority[1], command_id
        )

    def _reservation_active(
        self, authority: tuple[str, str, str], command: str
    ) -> bool:
        return self.coordinator.reservation_active(
            authority[0], authority[1], authority[2], command
        )

    def _canonical_error(self, executable: Path) -> str | None:
        canonical = executable.resolve()
        diag = (self.game_path / "DayZDiag_x64.exe").resolve()
        retail = {
            os.path.normcase(str((self.game_path / "DayZ_BE.exe").resolve())),
            os.path.normcase(str((self.game_path / "DayZ_x64.exe").resolve())),
        }
        normalized = os.path.normcase(str(canonical))
        if normalized == os.path.normcase(str(diag)):
            return None
        if normalized in retail:
            return "retail_manual_lifecycle_required"
        return "executable_not_allowed"

    def _client_peer_row(self) -> dict[str, object] | None:
        """The bridge row for the client peer, or None when there is no answer.

        The probe is the loopback ServerState's own status_snapshot: the daemon
        already owns that object (daemon.py passes it as ``bindings``), so the
        state the MCP server reads over HTTP as /status is readable here with no
        network hop. It travels as its OWN parameter and not through
        ``bindings`` because this is a read of the bridge, not a binding
        mutation, and an object that answers one must not have to answer both.

        The lock order is the established one: _prepare_instance already calls
        ``bindings.prepare`` under _operation_lock, and ServerState never takes
        _operation_lock while holding its own lock (loopback.py:1250).

        No probe, an unreadable one, or a snapshot without a client row are all
        the same answer: None. The caller turns that into a refusal, never into
        a licence to kill.
        """
        probe = self.bridge_probe
        if not callable(probe):
            return None
        try:
            snapshot = probe()
        except Exception:
            return None
        if not isinstance(snapshot, dict):
            return None
        peers = snapshot.get("peers")
        if not isinstance(peers, dict):
            return None
        row = peers.get("client")
        return row if isinstance(row, dict) else None

    def _replacement_witness_error(
        self, parsed: dict[str, object], *, now: float
    ) -> str | None:
        """Revalidate here the verdict the extension gate reached back there.

        H-A2-2 / ficha 79e2: the gate reads the bridge once, in another process,
        and this is where a live DayZ dies. Nothing revalidated in between, so a
        client that resumed polling inside the window was killed and the answer
        said client_not_polling -- a fact already false at the moment of the kill.

        The witness is the instant the gate READ the bridge, and it is compared
        by ELAPSED TIME, never by absolute clocks: the bridge stamps its polls
        with time.monotonic (loopback.py:939), so an epoch read here and a
        monotonic reading there do not live on the same axis. "The client polled
        after the decision" is therefore "its most recent poll is younger than
        the age of the decision".

        Deliberately stricter than server._peer_is_live: it takes the MOST
        RECENT of the two ages the row carries instead of choosing one by
        binding state. Any evidence of a poll after the decision blocks the
        kill, and refusing costs one repeated call while being wrong the other
        way costs a live session.
        """
        witness = parsed.get("replace_if_not_polling_since")
        if type(witness) is not int or witness <= 0:
            return "replace_witness_missing"
        decision_age_s = now - witness / 1000.0
        if (
            decision_age_s < -_REPLACE_WITNESS_SKEW_S
            or decision_age_s > _REPLACE_WITNESS_MAX_AGE_S
        ):
            return "replace_witness_stale"
        peer = self._client_peer_row()
        if peer is None:
            return "bridge_state_unreadable"
        ages = [
            value
            for value in (
                peer.get("last_poll_age_s"),
                peer.get("bound_last_poll_age_s"),
            )
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if not ages:
            # A row with no usable age is not an answer, and no answer must not
            # authorise a kill (the same reading as _peer_row_is_usable).
            return "bridge_state_unreadable"
        if min(ages) < max(0.0, decision_age_s):
            return "client_polling_since_decision"
        return None

    def _parse_start_request(self, request: object) -> tuple[dict[str, object] | None, str | None]:
        if not isinstance(request, dict):
            return None, "invalid_start_request"
        argv = request.get("argv")
        cwd = request.get("cwd")
        role = request.get("role")
        style = request.get("window_style")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
            or not isinstance(cwd, str)
            or not Path(cwd).is_absolute()
            or not isinstance(role, str)
            or not role
            or style not in {"normal", "hidden"}
        ):
            return None, "invalid_start_request"
        result = dict(request)
        for field in ("label", "mod", "profiles", "mission"):
            value = result.get(field, "")
            if not isinstance(value, str):
                return None, "invalid_start_request"
            result[field] = value
        launch_fields = (
            result.get("new_run_id"),
            result.get("launch_operation_id"),
            result.get("launch_request_sha256"),
        )
        if any(value is not None for value in launch_fields):
            new_run_id, operation_id, request_sha256 = launch_fields
            if (
                result.get("run_id") is not None
                or not _valid_uuid4(new_run_id)
                or not _valid_uuid4(operation_id)
                or new_run_id == operation_id
                or not _valid_sha256(request_sha256)
            ):
                return None, "invalid_launch_identity"
            canonical_request = {
                key: value
                for key, value in request.items()
                if key != "launch_request_sha256"
            }
            canonical = json.dumps(
                canonical_request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if hashlib.sha256(canonical).hexdigest() != request_sha256:
                return None, "launch_request_hash_mismatch"
        return result, None

    def _start_rejection(
        self,
        client: ClientIdentity,
        authority: tuple[str, str, str],
        reason: str,
        *,
        audit_reason: str | None = None,
    ) -> dict[str, object]:
        if not self._audit(
            "lifecycle_start_rejected",
            client,
            audit_reason or reason,
            "rejected",
        ):
            self.coordinator.reject_reservation(
                authority[0],
                authority[1],
                authority[2],
                reason,
            )
            return self._error("audit_failed", 503)
        return self._reject_reserved(authority, "lifecycle_start", reason)

    @staticmethod
    def _add_degradation(result: dict[str, object], value: str) -> None:
        current = result.get("cleanup_degraded")
        degraded = list(current) if isinstance(current, list) else []
        if value not in degraded:
            degraded.append(value)
        result["cleanup_degraded"] = degraded

    def _terminal_outcome(
        self,
        result: dict[str, object],
        event: str,
        client: ClientIdentity,
        *,
        reason: str,
        decision: str,
        run_id: str,
        state: str,
        terminated: int | None = None,
    ) -> dict[str, object]:
        fields: dict[str, object] = {"run_id": run_id, "state": state}
        if terminated is not None:
            fields["terminated"] = terminated
        if not self._audit(event, client, reason, decision, **fields):
            self._add_degradation(result, "audit_failed")
        return result

    def _persist_failed_launch_target(
        self,
        target: RunRecord,
        provisional: RunRecord,
        attempt_started_at: float | None,
    ) -> bool:
        persistence_failed = False
        try:
            self.manifest.replace(target)
        except Exception:
            persistence_failed = True
        else:
            self._invalidate_box_cache()
            if target.state == "EXITED":
                self._seal_terminal(provisional.run_id, time.time())
        if attempt_started_at is not None:
            self._forget_attempt_activity(
                provisional.run_id,
                attempt_started_at,
                rolled_back_at=time.time(),
            )
        return persistence_failed

    def _drain_pending_for_runs(self, run_ids: list[str]) -> None:
        bindings = self.bindings
        drain = getattr(bindings, "drain_pending_for_run", None)
        if not callable(drain):
            return
        for run_id in run_ids:
            try:
                drain(run_id)
            except Exception:
                pass

    def _fence_runs(self, run_ids: list[str]) -> None:
        if not run_ids:
            return
        bindings = self.bindings
        fence = getattr(bindings, "fence_runs", None)
        if callable(fence):
            fence(run_ids)
            return
        self._drain_pending_for_runs(run_ids)

    def _unfence_runs(self, run_ids: list[str]) -> None:
        bindings = self.bindings
        unfence = getattr(bindings, "unfence_runs", None)
        if not callable(unfence):
            return
        try:
            unfence(run_ids)
        except Exception:
            pass

    def _transition_to_idle(self, runs: list[str], persist: Callable[[], _T]) -> _T:
        """Fence + drain, persist, then confirm; unfence if persist fails.

        Every transition into RUNNING_IDLE goes through this method.
        """

        if runs:
            try:
                self._fence_runs(runs)
            except Exception:
                self._unfence_runs(runs)
                raise
        try:
            result = persist()
        except Exception:
            if runs:
                self._unfence_runs(runs)
            raise
        leftover: list[str] = []
        for run_id in runs:
            current = self.manifest.get(run_id)
            if current is None or current.state != "RUNNING_IDLE":
                leftover.append(run_id)
        if leftover:
            self._unfence_runs(leftover)
        return result

    def _quiesce_then_release_owner(self, session_id: str, lease_id: str) -> list[str]:
        """Quiesce runs → persist → confirm or revert.

        Every transition into RUNNING_IDLE goes through the fence.
        """

        candidates = [
            run.run_id
            for run in self.manifest.list_runs()
            if run.owner_session_id == session_id
            and run.owner_lease_id == lease_id
            and run.state == "RUNNING"
        ]
        return self._transition_to_idle(
            candidates,
            lambda: self.manifest.release_owner(session_id, lease_id),
        )

    def _settle_failed_launch(
        self,
        *,
        client: ClientIdentity,
        previous: RunRecord | None,
        provisional: RunRecord,
        launched: object | None,
        record: ProcessRecord | None,
        confirmed_error: str,
        manifest_failure: bool = False,
        attempt_started_at: float | None = None,
    ) -> dict[str, object]:
        """Leave every failed Popen attempt in a durable, reconcilable state."""

        self._last_start_error = confirmed_error
        confirmed_closed = launched is None or self._terminate_open_handle(launched)
        persistence_failed = False
        with self._compensating(provisional.run_id):
            if confirmed_closed:
                if previous is not None:
                    target = RunRecord.from_payload(dataclasses.asdict(previous))
                else:
                    target = RunRecord.from_payload(dataclasses.asdict(provisional))
                    target.state = "EXITED"
                    target.owner_session_id = None
                    target.owner_lease_id = None
                    target.processes = []
                persistence_failed = self._persist_failed_launch_target(
                    target, provisional, attempt_started_at
                )
                durable = self.manifest.get(provisional.run_id)
                state = durable.state if durable is not None else "STARTING"
                if persistence_failed:
                    result = self._error("manual_cleanup_required", 409)
                else:
                    result = self._error(
                        confirmed_error,
                        503 if confirmed_error == "lifecycle_start_failed" else 409,
                    )
            else:
                target = RunRecord.from_payload(dataclasses.asdict(provisional))
                target.state = "UNRECONCILED"
                if record is not None and record not in target.processes:
                    target.processes.append(record)
                persistence_failed = self._persist_failed_launch_target(
                    target, provisional, attempt_started_at
                )
                durable = self.manifest.get(provisional.run_id)
                state = durable.state if durable is not None else "STARTING"
                result = self._error("manual_cleanup_required", 409)

        result["run_id"] = provisional.run_id
        result["state"] = state
        if manifest_failure or persistence_failed:
            self._add_degradation(result, "manifest_failed")
        return self._terminal_outcome(
            result,
            "lifecycle_start_outcome",
            client,
            reason=str(result["error"]),
            decision=str(result["error"]),
            run_id=provisional.run_id,
            state=state,
        )

    def start_run(self, client: ClientIdentity, token: str | None, request: object) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_start"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        with self._operation_lock:
            if not self._reservation_active(authority, command):
                return self._error("lease_invalid", 409)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            parsed, request_error = self._parse_start_request(request)
            if request_error is not None or parsed is None:
                return self._start_rejection(
                    client, authority, request_error or "invalid_start_request"
                )
            executable = Path(parsed["argv"][0])
            canonical_error = self._canonical_error(executable)
            if canonical_error is not None:
                return self._start_rejection(client, authority, canonical_error)
            canonical_argv = list(parsed["argv"])
            canonical_argv[0] = str(executable.resolve())
            parsed["argv"] = canonical_argv

            supplied_run_id = parsed.get("run_id")
            new_run_id = parsed.get("new_run_id")
            launch_operation_id = parsed.get("launch_operation_id")
            launch_request_sha256 = parsed.get("launch_request_sha256")
            active = [run for run in self.manifest.list_runs() if run.state in _ACTIVE_STATES]
            existing: RunRecord | None = None
            if supplied_run_id is not None:
                if not isinstance(supplied_run_id, str) or not supplied_run_id:
                    return self._start_rejection(client, authority, "invalid_run_id")
                existing = self.manifest.get(supplied_run_id)
                if existing is None:
                    return self._start_rejection(client, authority, "run_not_found")
                if existing.state != "RUNNING":
                    return self._start_rejection(
                        client, authority, "run_not_extensible"
                    )
                if (
                    existing.owner_session_id != client.session_id
                    or existing.owner_lease_id != authority[1]
                ):
                    return self._start_rejection(client, authority, "run_not_adopted")
                if any(run.run_id != existing.run_id for run in active):
                    return self._start_rejection(
                        client, authority, "active_run_exists"
                    )
            elif isinstance(new_run_id, str):
                existing = self.manifest.get(new_run_id)
                if existing is not None:
                    if (
                        existing.owner_session_id != client.session_id
                        or existing.owner_lease_id != authority[1]
                        or existing.launch_operation_id != launch_operation_id
                        or existing.launch_request_sha256 != launch_request_sha256
                        or existing.state != "RUNNING"
                    ):
                        return self._start_rejection(
                            client, authority, "launch_identity_conflict"
                        )
                    if not self._audit(
                        "lifecycle_start",
                        client,
                        "idempotent_retry",
                        "reused",
                        run_id=existing.run_id,
                    ):
                        self.coordinator.reject_reservation(
                            authority[0], authority[1], authority[2], "audit_failed"
                        )
                        return self._error("audit_failed", 503)
                    command_id = self._commit_reserved(authority, command)
                    if command_id is None:
                        return self._error("lease_invalid", 409)
                    try:
                        return {
                            "ok": True,
                            "run_id": existing.run_id,
                            "state": existing.state,
                        }
                    finally:
                        self._finish_committed(authority, command_id)
                if active:
                    return self._start_rejection(
                        client, authority, "active_run_exists"
                    )
            elif active:
                return self._start_rejection(client, authority, "active_run_exists")
            blocker = getattr(self.coordinator, "box_blocks_start", None)
            if callable(blocker) and blocker(client):
                return self._start_rejection(
                    client,
                    authority,
                    "active_run_exists",
                    audit_reason="box_claimed",
                )
            registered_pids = {
                record.pid for run in active for record in run.processes
            }
            foreign_diag_reason = self._foreign_diag_reason(registered_pids)
            if foreign_diag_reason is not None:
                return self._start_rejection(
                    client,
                    authority,
                    "active_run_exists",
                    audit_reason=foreign_diag_reason,
                )
            requested_port = _requested_port(parsed["argv"])
            foreign_port_reason = self._foreign_port_reason(
                registered_pids, requested_port
            )
            if foreign_port_reason is not None:
                return self._start_rejection(
                    client,
                    authority,
                    "active_run_exists",
                    audit_reason=foreign_port_reason,
                )
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            if not self._audit("lifecycle_start", client, "lease_valid", "allowed"):
                self.coordinator.reject_reservation(
                    authority[0], authority[1], authority[2], "audit_failed"
                )
                return self._error("audit_failed", 503)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")

            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)

            run_id = (
                existing.run_id
                if existing is not None
                else str(new_run_id) if isinstance(new_run_id, str) else self.id_fn()
            )
            previous = (
                RunRecord.from_payload(dataclasses.asdict(existing))
                if existing is not None
                else None
            )
            provisional = (
                RunRecord.from_payload(dataclasses.asdict(existing))
                if existing is not None
                else RunRecord(
                    run_id,
                    client.session_id,
                    authority[1],
                    "STARTING",
                    str(parsed["label"]),
                    str(parsed["mod"]),
                    str(parsed["profiles"]),
                    str(parsed["mission"]),
                    [],
                    str(launch_operation_id)
                    if isinstance(launch_operation_id, str)
                    else None,
                    str(launch_request_sha256)
                    if isinstance(launch_request_sha256, str)
                    else None,
                    False if isinstance(launch_operation_id, str) else True,
                    self._current_generation() or None,
                )
            )
            provisional.state = "STARTING"
            try:
                if existing is None:
                    self.manifest.add(provisional)
                else:
                    self.manifest.replace(provisional)
            except Exception:
                self._finish_committed(authority, command_id)
                result = self._error("manifest_failed", 503)
                return self._terminal_outcome(
                    result,
                    "lifecycle_start_outcome",
                    client,
                    reason="manifest_failed",
                    decision="manifest_failed",
                    run_id=run_id,
                    state=existing.state if existing is not None else "ABSENT",
                )
            self._invalidate_box_cache()

            launched: object | None = None
            record: ProcessRecord | None = None
            minted: str | None = None
            launch_role = str(parsed["role"])
            attempt_started_at = time.time()
            try:
                if self._quarantined():
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=None,
                        record=None,
                        confirmed_error="retail_quarantine",
                        attempt_started_at=attempt_started_at,
                    )
                if (
                    existing is not None
                    and previous is not None
                    # A1-F1/A1-F2: only the role the extension gate of
                    # dayz_test_tool covers. That gate reads the client record
                    # and nothing else, so running the replacement for another
                    # role would end a live process no gate ever looked at, and
                    # publish no_client_to_replace while doing it. Measured on
                    # role=offline; restores the HEAD behaviour for it.
                    and launch_role == "client"
                ):
                    # P-L2 / P-L2.c: the role this launch claims is freed
                    # before the instance is prepared, so a refusal here never
                    # leaves the superseded process alive with its binding
                    # already retired.
                    # 79e2: revalidated HERE, against the bridge state this
                    # process owns, before a single process is touched.
                    replaced_pids: tuple[int, ...] = ()
                    replace_error = self._replacement_witness_error(
                        parsed, now=time.time()
                    )
                    if replace_error is None:
                        replaced_pids, replace_error = self._replace_role_processes(
                            provisional, launch_role, client=client
                        )
                    if replace_error is None and replaced_pids:
                        # Durable before the launch: a crash between here and
                        # the launcher leaves STARTING without the superseded
                        # record, which recover_after_restart turns into
                        # UNRECONCILED and the adopt + stop pair closes.
                        try:
                            self.manifest.replace(provisional)
                        except Exception:
                            replace_error = "manifest_failed"
                        else:
                            self._invalidate_box_cache()
                            previous.processes = list(provisional.processes)
                    if replace_error is not None:
                        settled = self._settle_failed_launch(
                            client=client,
                            previous=previous,
                            provisional=provisional,
                            launched=None,
                            record=None,
                            confirmed_error=replace_error,
                            manifest_failure=replace_error == "manifest_failed",
                            attempt_started_at=attempt_started_at,
                        )
                        if replace_error == "port_still_held":
                            settled["hint"] = _PORT_STILL_HELD_HINT
                        elif replace_error in _REPLACE_WITNESS_HINTS:
                            settled["hint"] = _REPLACE_WITNESS_HINTS[replace_error]
                        return settled
                minted, prepare_error = self._prepare_instance(
                    run_id, launch_role, str(parsed["profiles"]), existing is not None
                )
                if prepare_error is not None:
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=None,
                        record=None,
                        confirmed_error=prepare_error,
                        attempt_started_at=attempt_started_at,
                    )
                # Last look at the socket table before the bind. Audit, reserve
                # and persistence sat between the admission probe and here; a
                # server that appeared meanwhile must not be launched over. The
                # window that remains is between this read and DayZ's own bind.
                pre_launch_reason = self._foreign_port_reason(
                    registered_pids, requested_port
                )
                if pre_launch_reason is not None:
                    audited = self._audit(
                        "lifecycle_start_rejected",
                        client,
                        pre_launch_reason,
                        "rejected",
                        run_id=run_id,
                        stage="pre_launch",
                    )
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    settled = self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=None,
                        record=None,
                        confirmed_error="active_run_exists",
                        attempt_started_at=attempt_started_at,
                    )
                    if not audited:
                        # The refusal stands; the missing audit row is published
                        # the way _start_rejection publishes it, not swallowed.
                        self._add_degradation(settled, "audit_failed")
                    return settled
                try:
                    launched = self.launcher(
                        list(parsed["argv"]),
                        str(parsed["cwd"]),
                        str(parsed["window_style"]),
                    )
                    pid = getattr(launched, "pid", None)
                    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                        raise OSError("invalid_launcher_pid")
                except Exception:
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=launched,
                        record=None,
                        confirmed_error="lifecycle_start_failed",
                        attempt_started_at=attempt_started_at,
                    )

                try:
                    actual = self.guard.snapshot(pid)
                except Exception:
                    actual = {"error": "guard_unavailable", "exit_code": 3}
                record = self._record_from_snapshot(actual, launch_role)
                if record is None:
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=launched,
                        record=None,
                        confirmed_error="identity_unavailable",
                        attempt_started_at=attempt_started_at,
                    )
                self._confirm_instance(minted, record)

                completed = RunRecord.from_payload(dataclasses.asdict(provisional))
                completed.processes.append(record)
                completed.state = "RUNNING"
                try:
                    self.manifest.replace(completed)
                except Exception:
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=launched,
                        record=record,
                        confirmed_error="lifecycle_start_failed",
                        manifest_failure=True,
                        attempt_started_at=attempt_started_at,
                    )
                self._capture_start_activity(completed, launched=record)

                result: dict[str, object] = {
                    "ok": True,
                    "run_id": run_id,
                    "state": "RUNNING",
                }
                self._last_start_error = None
                return self._terminal_outcome(
                    result,
                    "lifecycle_start_outcome",
                    client,
                    reason="started",
                    decision="started",
                    run_id=run_id,
                    state="RUNNING",
                )
            finally:
                self._finish_committed(authority, command_id)

    def ack_run(
        self,
        client: ClientIdentity,
        token: str | None,
        run_id: object,
        launch_operation_id: object,
    ) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_ack"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        with self._operation_lock:
            if not self._reservation_active(authority, command):
                return self._error("lease_invalid", 409)
            if not isinstance(run_id, str) or not isinstance(
                launch_operation_id, str
            ):
                return self._reject_reserved(
                    authority, command, "launch_identity_conflict"
                )
            run = self.manifest.get(run_id)
            if (
                run is None
                or run.state != "RUNNING"
                or run.owner_session_id != client.session_id
                or run.owner_lease_id != authority[1]
                or run.launch_operation_id != launch_operation_id
            ):
                return self._reject_reserved(
                    authority, command, "launch_identity_conflict"
                )
            if not self._audit(
                "lifecycle_ack",
                client,
                "launch_identity_match",
                "acknowledged",
                run_id=run_id,
            ):
                self.coordinator.reject_reservation(
                    authority[0], authority[1], authority[2], "audit_failed"
                )
                return self._error("audit_failed", 503)
            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)
            try:
                if not run.launch_acknowledged:
                    run.launch_acknowledged = True
                    try:
                        self.manifest.replace(run)
                    except Exception:
                        return self._error("manifest_failed", 503)
                return {"ok": True, "run_id": run_id, "state": run.state}
            finally:
                self._finish_committed(authority, command_id)

    @staticmethod
    def _terminate_open_handle(launched: object) -> bool:
        try:
            launched.terminate()
        except Exception:
            poll = getattr(launched, "poll", None)
            if callable(poll):
                try:
                    return poll() is not None
                except Exception:
                    return False
            return False
        try:
            launched.wait(timeout=5.0)
            return True
        except Exception:
            poll = getattr(launched, "poll", None)
            if callable(poll):
                try:
                    return poll() is not None
                except Exception:
                    return False
            return False

    @staticmethod
    def _record_from_snapshot(value: object, role: str) -> ProcessRecord | None:
        if (
            not isinstance(value, dict)
            or value.get("identity_complete") is not True
            or value.get("identity_scheme") != "psutil-argv-v2"
        ):
            return None
        try:
            record = ProcessRecord(
                value.get("pid"),
                value.get("creation_time_utc"),
                str(value.get("executable_sha256", "")).casefold(),
                str(value.get("command_line_sha256", "")).casefold(),
                role,
                identity_scheme="psutil-argv-v2",
            )
            record.validate()
            return record
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _identity_matches(record: ProcessRecord, actual: object) -> bool:
        if not isinstance(actual, dict) or actual.get("identity_complete") is not True:
            return False
        return (
            actual.get("identity_scheme") == record.identity_scheme
            and actual.get("pid") == record.pid
            and actual.get("creation_time_utc") == record.creation_time_utc
            and str(actual.get("executable_sha256", "")).casefold() == record.executable_sha256.casefold()
            and str(actual.get("command_line_sha256", "")).casefold() == record.command_line_sha256.casefold()
        )

    def _classify_registered_process(self, record: ProcessRecord) -> tuple[str, str]:
        """Classify a registered PID as owned, gone, foreign, or unknown.

        Absent (process_not_found) and a complete identity that does not match
        are both "no longer ours". Unknown snapshots stay fail-closed. A foreign
        PID must never be terminated.
        """
        try:
            actual = self.guard.snapshot(record.pid)
        except Exception:
            return "unknown", "guard_unavailable"
        if not isinstance(actual, dict):
            return "unknown", "process_identity_mismatch"
        if actual.get("error") == "process_not_found" and actual.get("exit_code") == 4:
            return "gone", "process_not_found"
        if actual.get("exit_code") == 3:
            return "unknown", "guard_unavailable"
        if self._identity_matches(record, actual):
            return "owned", ""
        if actual.get("identity_complete") is True:
            return "foreign", "process_identity_mismatch"
        return "unknown", "process_identity_mismatch"

    def _partition_registered_processes(
        self, processes: list[ProcessRecord]
    ) -> tuple[dict[str, list[int]], str | None]:
        buckets: dict[str, list[int]] = {
            "owned": [],
            "gone": [],
            "foreign": [],
            "unknown": [],
        }
        unknown_reason: str | None = None
        for record in processes:
            kind, reason = self._classify_registered_process(record)
            buckets[kind].append(record.pid)
            if kind == "unknown" and unknown_reason is None:
                unknown_reason = reason
        return buckets, unknown_reason

    def _ports_released(self, pids: set[int]) -> str | None:
        """None when no UDP holder carries one of these pids (P-L2.c).

        Fail-closed twice over: a table this daemon cannot read is never
        "released", and a pid still listed answers port_still_held instead of
        letting the caller retire a record that the pre-launch probe of
        start_run would then read as a foreign holder. A lifecycle wired
        without a port probe has no table to contradict, and _port_holders
        answers an empty known scan for it.

        A3-F4: a bare pid is not an identity, and this runs right after the
        guard confirmed the exit, which is the instant that pid stopped being
        one. The OS is free to hand it to anything, so only a holder that could
        plausibly be the game counts: a DayZ image on any port, or any image on
        a port of the band this project launches in. A recycled pid holding an
        unrelated socket no longer blocks the relaunch of a client it never was.
        """
        if not pids:
            return None
        tries = max(1, int(self._role_release_tries))
        reason = "port_scan_unknown"
        for attempt in range(tries):
            scan_reason, holders = self._port_holders()
            if scan_reason is None and holders is not None:
                held = any(
                    isinstance(holder.get("pid"), int)
                    and holder["pid"] in pids
                    and (
                        _is_dayz_image(holder.get("name"))
                        or holder["port"] in _DAYZ_PORT_RANGE
                    )
                    for holder in holders
                )
                if not held:
                    return None
                reason = "port_still_held"
            else:
                reason = scan_reason or "port_scan_unknown"
            if attempt + 1 < tries:
                time.sleep(self._role_release_interval_s)
        return reason

    def _replace_role_processes(
        self, run: RunRecord, role: str, *, client: ClientIdentity
    ) -> tuple[list[int], str | None]:
        """Free a role on a run being extended, before a new process takes it.

        fb-20260904-025027-8f76 (part c): start_run appended the relaunched
        client next to the hung one, and RunRecord.validate does not bound a
        run to one process per role, so the row carried two client records and
        the liveness projection reported the last of them. The role's instance
        binding is already retired on this same path (_prepare_instance with
        replacing_role), so the superseded process is cut off from the bridge
        either way: what was missing was ending it and dropping its record.

        Order: terminate -> confirm -> retire. The process is confirmed by the
        guard, which waits for the exit before answering terminated
        (native_process_guard.py: process.wait after kill); the socket is
        confirmed by _ports_released. The owned/foreign/unknown split is
        stop_run's and is not relaxed: only a process this lifecycle can vouch
        for is terminated, and a record it cannot classify is left exactly
        where it is - a duplicate record is preferable to ending a process that
        may not be ours.

        Answers (retired_pids, error). On an error the run keeps every record;
        the terminations already confirmed are reported so the caller can
        settle the launch on the truth.
        """
        role_records = [record for record in run.processes if record.role == role]
        if not role_records:
            return [], None
        owned: list[ProcessRecord] = []
        gone: list[ProcessRecord] = []
        for record in role_records:
            kind, _reason = self._classify_registered_process(record)
            if kind == "owned":
                owned.append(record)
            elif kind == "gone":
                gone.append(record)
        retiring = owned + gone
        if not retiring:
            return [], None
        if len(retiring) >= len(run.processes):
            # RunRecord.validate demands processes on a RUNNING run, so an
            # emptied row would make the rollback target of
            # _settle_failed_launch unpersistable. Refuse before terminating.
            return [], "run_would_be_empty"
        if not self._audit(
            "lifecycle_role_replaced",
            client,
            "role_superseded",
            "allowed",
            run_id=run.run_id,
            role=role,
            owned_pids=[record.pid for record in owned],
            gone_pids=[record.pid for record in gone],
        ):
            return [], "audit_failed"
        terminated: list[int] = []
        for record in owned:
            try:
                guard_result = self.guard.terminate(record)
            except Exception:
                guard_result = {
                    "error": "guard_unavailable",
                    "exit_code": 3,
                    "terminated": False,
                }
            if (
                not isinstance(guard_result, dict)
                or guard_result.get("terminated") is not True
            ):
                return terminated, (
                    str(guard_result.get("error", "termination_failed"))
                    if isinstance(guard_result, dict)
                    else "termination_failed"
                )
            terminated.append(record.pid)
        release_reason = self._ports_released(
            {record.pid for record in retiring}
        )
        if release_reason is not None:
            return terminated, release_reason
        retired = {(record.pid, record.role) for record in retiring}
        run.processes = [
            record
            for record in run.processes
            if (record.pid, record.role) not in retired
        ]
        return [record.pid for record in retiring], None

    def stop_run(self, client: ClientIdentity, token: str | None, run_id: object) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_stop"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        with self._operation_lock:
            if not self._reservation_active(authority, command):
                return self._error("lease_invalid", 409)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            if not isinstance(run_id, str) or not run_id:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            run = self.manifest.get(run_id)
            if run is None:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            never_started = (
                run.state == "EXITED"
                and run.launch_acknowledged is False
                and not run.processes
                and run.owner_session_id is None
                and run.owner_lease_id is None
            )
            if not never_started and (
                run.owner_session_id != client.session_id
                or run.owner_lease_id != authority[1]
                or run.state != "RUNNING"
            ):
                return self._reject_reserved(authority, command, "run_not_adopted")
            owned: list[ProcessRecord] = []
            gone_pids: list[int] = []
            foreign_pids: list[int] = []
            for record in run.processes:
                kind, reason = self._classify_registered_process(record)
                if kind == "owned":
                    owned.append(record)
                    continue
                if kind == "gone":
                    gone_pids.append(record.pid)
                    continue
                if kind == "foreign":
                    foreign_pids.append(record.pid)
                    continue
                run.owner_session_id = None
                run.owner_lease_id = None
                run.state = "UNRECONCILED"
                self._retire_run_bindings(run.run_id, "stopped")
                try:
                    self.manifest.replace(run)
                except Exception:
                    result = self._error("manifest_failed", 503)
                    degraded = ["manifest_failed"]
                    degraded.extend(
                        self.coordinator.abort_reservation(
                            authority[0],
                            authority[1],
                            authority[2],
                            "manifest_failed",
                        )
                    )
                    result["cleanup_degraded"] = list(
                        dict.fromkeys(degraded)
                    )
                    return self._terminal_outcome(
                        result,
                        "lifecycle_stop_outcome",
                        client,
                        reason="manifest_failed",
                        decision="manual_cleanup_required",
                        run_id=run.run_id,
                        state="RUNNING",
                        terminated=0,
                    )
                self._invalidate_box_cache()
                result = self._reject_reserved(
                    authority,
                    command,
                    reason,
                    503 if reason == "guard_unavailable" else 409,
                )
                result["run_id"] = run.run_id
                result["state"] = "UNRECONCILED"
                return self._terminal_outcome(
                    result,
                    "lifecycle_stop_outcome",
                    client,
                    reason=reason,
                    decision="manual_cleanup_required",
                    run_id=run.run_id,
                    state="UNRECONCILED",
                    terminated=0,
                )
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            if not self._audit(
                "lifecycle_stop",
                client,
                "identity_match",
                "allowed",
                run_id=run_id,
                owned_pids=[record.pid for record in owned],
                gone_pids=list(gone_pids),
                foreign_pids=list(foreign_pids),
            ):
                self.coordinator.reject_reservation(
                    authority[0], authority[1], authority[2], "audit_failed"
                )
                return self._error("audit_failed", 503)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)
            try:
                self._retire_run_bindings(run_id, "stopped")
                run.state = "STOPPING"
                try:
                    self.manifest.replace(run)
                except Exception:
                    result = self._error("manifest_failed", 503)
                    self._add_degradation(result, "manifest_failed")
                    return self._terminal_outcome(
                        result,
                        "lifecycle_stop_outcome",
                        client,
                        reason="manifest_failed",
                        decision="manual_cleanup_required",
                        run_id=run_id,
                        state="RUNNING",
                        terminated=0,
                    )
                self._invalidate_box_cache()
                terminated = 0
                survivors = list(owned)
                for record in list(owned):
                    if self._quarantined():
                        run.owner_session_id = None
                        run.owner_lease_id = None
                        run.state = "UNRECONCILED"
                        run.processes = list(survivors)
                        degraded: list[str] = []
                        try:
                            self.manifest.replace(run)
                        except Exception:
                            degraded.append("manifest_failed")
                        else:
                            self._invalidate_box_cache()
                        result: dict[str, object] = {
                            "error": "partial_cleanup",
                            "reason": "retail_quarantine",
                            "terminated": terminated,
                            "run_id": run_id,
                            "_http_status": 409,
                        }
                        if degraded:
                            result["cleanup_degraded"] = degraded
                        return self._terminal_outcome(
                            result,
                            "lifecycle_stop_outcome",
                            client,
                            reason="retail_quarantine",
                            decision="partial_cleanup",
                            run_id=run_id,
                            state="UNRECONCILED",
                            terminated=terminated,
                        )
                    try:
                        guard_result = self.guard.terminate(record)
                    except Exception:
                        guard_result = {
                            "error": "guard_unavailable",
                            "exit_code": 3,
                            "terminated": False,
                        }
                    if (
                        not isinstance(guard_result, dict)
                        or guard_result.get("terminated") is not True
                    ):
                        run.owner_session_id = None
                        run.owner_lease_id = None
                        run.state = "UNRECONCILED"
                        run.processes = list(survivors)
                        degraded = []
                        try:
                            self.manifest.replace(run)
                        except Exception:
                            degraded.append("manifest_failed")
                        else:
                            self._invalidate_box_cache()
                        response: dict[str, object] = {
                            "error": "partial_cleanup",
                            "terminated": terminated,
                            "run_id": run_id,
                            "_http_status": 409,
                        }
                        if degraded:
                            response["cleanup_degraded"] = degraded
                        return self._terminal_outcome(
                            response,
                            "lifecycle_stop_outcome",
                            client,
                            reason=(
                                str(guard_result.get("error", "termination_failed"))
                                if isinstance(guard_result, dict)
                                else "termination_failed"
                            ),
                            decision="partial_cleanup",
                            run_id=run_id,
                            state="UNRECONCILED",
                            terminated=terminated,
                        )
                    terminated += 1
                    survivors.remove(record)
                run.state = "EXITED"
                run.owner_session_id = None
                run.owner_lease_id = None
                run.processes = []
                if not self._commit_retirement(
                    run,
                    "lifecycle_stop_outcome",
                    "stopped",
                    "stopped",
                ):
                    result = {
                        "error": "partial_cleanup",
                        "reason": "manifest_failed",
                        "terminated": terminated,
                        "run_id": run_id,
                        "cleanup_degraded": ["manifest_failed"],
                        "_http_status": 503,
                    }
                    return self._terminal_outcome(
                        result,
                        "lifecycle_stop_outcome",
                        client,
                        reason="manifest_failed",
                        decision="partial_cleanup",
                        run_id=run_id,
                        state="STOPPING",
                        terminated=terminated,
                    )
                result = {
                    "ok": True,
                    "run_id": run_id,
                    "state": "EXITED",
                    "terminated": terminated,
                }
                return self._terminal_outcome(
                    result,
                    "lifecycle_stop_outcome",
                    client,
                    reason="stopped",
                    decision="stopped",
                    run_id=run_id,
                    state="EXITED",
                    terminated=terminated,
                )
            finally:
                self._finish_committed(authority, command_id)

    def adopt_run(self, client: ClientIdentity, token: str | None, run_id: object) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_adopt"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        committed = False
        try:
            with self._operation_lock:
                if not self._reservation_active(authority, command):
                    return self._error("lease_invalid", 409)
                if self._quarantined():
                    return self._reject_reserved(authority, command, "retail_quarantine")
                if not isinstance(run_id, str) or not run_id:
                    return self._reject_reserved(authority, command, "run_not_found", 404)
                run = self.manifest.get(run_id)
                if run is None:
                    return self._reject_reserved(authority, command, "run_not_found", 404)
                if (
                    run.state == "RUNNING"
                    and run.owner_session_id == client.session_id
                    and run.owner_lease_id == authority[1]
                ):
                    # P-J2: same owner + same lease is a no-op success. dayz_test_worker
                    # adopts explicitly after the grant already adopted.
                    if not self._audit(
                        "lifecycle_adopt", client, "identity_match", "allowed", run_id=run_id
                    ):
                        self.coordinator.reject_reservation(
                            authority[0], authority[1], authority[2], "audit_failed"
                        )
                        return self._error("audit_failed", 503)
                    command_id = self._commit_reserved(authority, command)
                    if command_id is None:
                        return self._error("lease_invalid", 409)
                    committed = True
                    self._finish_committed(authority, command_id)
                    dispatchable = self._adopt_dispatchable(run_id)
                    payload: dict[str, object] = {
                        "ok": True,
                        "run_id": run_id,
                        "state": "RUNNING",
                        "dispatchable": dispatchable,
                    }
                    if not dispatchable:
                        payload["hint"] = _ADOPT_NOT_DISPATCHABLE_HINT
                    return payload
                if (
                    run.state not in _ADOPTABLE_STATES
                    or run.owner_session_id is not None
                ):
                    return self._reject_reserved(authority, command, "run_not_adoptable")
                if any(
                    other.state in _ACTIVE_STATES and other.run_id != run_id
                    for other in self.manifest.list_runs()
                ):
                    return self._reject_reserved(authority, command, "active_run_exists")
                buckets, unknown_reason = self._partition_registered_processes(run.processes)
                if buckets["unknown"]:
                    reason = unknown_reason or "process_identity_mismatch"
                    return self._reject_reserved(
                        authority,
                        command,
                        reason,
                        503 if reason == "guard_unavailable" else 409,
                    )
                if not buckets["owned"]:
                    result = self._reject_reserved(
                        authority, command, "run_processes_gone"
                    )
                    result["hint"] = _RUN_PROCESSES_GONE_HINT
                    return result
                if self._quarantined():
                    return self._reject_reserved(authority, command, "retail_quarantine")
                adopt_reason = (
                    "unreconciled_adopt"
                    if run.state == "UNRECONCILED"
                    else "identity_match"
                )
                if not self._audit("lifecycle_adopt", client, adopt_reason, "allowed", run_id=run_id):
                    self.coordinator.reject_reservation(
                        authority[0], authority[1], authority[2], "audit_failed"
                    )
                    return self._error("audit_failed", 503)
                if self._quarantined():
                    return self._reject_reserved(authority, command, "retail_quarantine")
                command_id = self._commit_reserved(authority, command)
                if command_id is None:
                    return self._error("lease_invalid", 409)
                committed = True
                run.owner_session_id = client.session_id
                run.owner_lease_id = authority[1]
                run.state = "RUNNING"
                try:
                    self.manifest.replace(run)
                except Exception:
                    self._finish_committed(authority, command_id)
                    return self._error("manifest_failed", 503)
                self._invalidate_box_cache()
                self._unfence_runs([run_id])
                self._finish_committed(authority, command_id)
                dispatchable = self._adopt_dispatchable(run_id)
                payload = {
                    "ok": True,
                    "run_id": run_id,
                    "state": "RUNNING",
                    "dispatchable": dispatchable,
                }
                if not dispatchable:
                    payload["hint"] = _ADOPT_NOT_DISPATCHABLE_HINT
                return payload
        except Exception:
            # Unexpected exit after _authorize opened a reservation and before
            # commit: abort (not reject). abort_reservation is the coordinator
            # contract for an operation that could not complete; reject is the
            # fallback close. Compensator degradations are not swallowed (F-04);
            # same collection pattern as stop_run's manifest.replace failure.
            result = self._error("adopt_failed", 503)
            if not committed:
                degraded: list[str] = []
                try:
                    degraded.extend(
                        self.coordinator.abort_reservation(
                            authority[0],
                            authority[1],
                            authority[2],
                            "adopt_failed",
                        )
                    )
                except Exception:
                    try:
                        rejected = self.coordinator.reject_reservation(
                            authority[0],
                            authority[1],
                            authority[2],
                            "adopt_failed",
                        )
                        extra = getattr(rejected, "cleanup_degraded", ())
                        if extra:
                            degraded.extend(extra)
                    except Exception:
                        degraded.append("reservation_abort_failed")
                if degraded:
                    result["cleanup_degraded"] = list(dict.fromkeys(degraded))
            return result

    def release_owner(self, session_id: str, lease_id: str) -> list[str]:
        with self._operation_lock:
            self._require_legacy_identity_safe()
            changed = self._quiesce_then_release_owner(session_id, lease_id)
            if changed:
                self._invalidate_box_cache()
            return changed

    def release_all_running_owners(self) -> list[str]:
        with self._operation_lock:
            self._require_legacy_identity_safe()
            candidates = [
                run.run_id
                for run in self.manifest.list_runs()
                if run.state == "RUNNING" and run.owner_session_id is not None
            ]
            changed = self._transition_to_idle(
                candidates, self.manifest.release_all_running_owners
            )
            if changed:
                self._invalidate_box_cache()
            return changed

    def begin_release_owner(
        self, session_id: str, lease_id: str
    ) -> CleanupDisposition:
        terminal = threading.Event()
        result: dict[str, object] = {}
        with self._operation_lock:
            self._require_legacy_identity_safe()
            unacknowledged = [
                run
                for run in self.manifest.list_runs()
                if run.owner_session_id == session_id
                and run.owner_lease_id == lease_id
                and run.launch_operation_id is not None
                and not run.launch_acknowledged
                and run.state in _ACTIVE_STATES
            ]
            if not unacknowledged:
                released = self._quiesce_then_release_owner(session_id, lease_id)
                if released:
                    self._invalidate_box_cache()
                result.update(
                    {
                        "terminal_safe": True,
                        "runs_released": released,
                    }
                )
                terminal.set()
                return CleanupDisposition(False, terminal, result)

        def cleanup() -> None:
            released: list[str] = []
            try:
                with self._operation_lock:
                    for original in unacknowledged:
                        current = self.manifest.get(original.run_id)
                        if (
                            current is None
                            or current.owner_session_id != session_id
                            or current.owner_lease_id != lease_id
                            or current.launch_operation_id
                            != original.launch_operation_id
                            or current.launch_request_sha256
                            != original.launch_request_sha256
                            or current.launch_acknowledged
                        ):
                            self._arm_cleanup_fault(
                                current or original, "identity_ambiguous"
                            )
                            result.update(
                                {
                                    "terminal_safe": False,
                                    "error": "identity_ambiguous",
                                }
                            )
                            return
                        for record in current.processes:
                            try:
                                actual = self.guard.snapshot(record.pid)
                            except Exception:
                                actual = {"error": "guard_unavailable"}
                            if not self._identity_matches(record, actual):
                                self._persist_unreconciled_and_arm(
                                    current, "identity_ambiguous"
                                )
                                result.update(
                                    {
                                        "terminal_safe": False,
                                        "error": "identity_ambiguous",
                                    }
                                )
                                return
                            try:
                                terminated = self.guard.terminate(record)
                            except Exception:
                                terminated = {"terminated": False}
                            if (
                                not isinstance(terminated, dict)
                                or terminated.get("terminated") is not True
                            ):
                                self._persist_unreconciled_and_arm(
                                    current, "cleanup_failed"
                                )
                                result.update(
                                    {
                                        "terminal_safe": False,
                                        "error": "cleanup_failed",
                                    }
                                )
                                return
                        current.state = "EXITED"
                        current.owner_session_id = None
                        current.owner_lease_id = None
                        current.processes = []
                        if not self._commit_retirement(
                            current,
                            "lifecycle_owner_released",
                            "released",
                            "released",
                        ):
                            result.update(
                                {
                                    "terminal_safe": False,
                                    "error": "cleanup_failed",
                                }
                            )
                            return
                        released.append(current.run_id)
                    extra = self._quiesce_then_release_owner(session_id, lease_id)
                    if extra:
                        self._invalidate_box_cache()
                    released.extend(extra)
                    result.update(
                        {
                            "terminal_safe": True,
                            "runs_released": sorted(set(released)),
                        }
                    )
            except Exception:
                result.update(
                    {"terminal_safe": False, "error": "cleanup_failed"}
                )
            finally:
                terminal.set()

        threading.Thread(
            target=cleanup,
            name=f"dayz-mcp-run-cleanup-{lease_id[:12]}",
            daemon=True,
        ).start()
        return CleanupDisposition(True, terminal, result)

    def _persist_unreconciled_and_arm(self, current: RunRecord, reason: str) -> None:
        durable = RunRecord.from_payload(dataclasses.asdict(current))
        current.state = "UNRECONCILED"
        current.owner_session_id = None
        current.owner_lease_id = None
        self._retire_run_bindings(current.run_id, "released")
        try:
            self.manifest.replace(current)
        except Exception:
            self._arm_cleanup_fault(durable, reason)
            return
        self._invalidate_box_cache()
        self._arm_cleanup_fault(current, reason)

    def _arm_cleanup_fault(self, run: RunRecord, reason: str) -> None:
        callback = self.recovery_fault_arm
        if callback is None:
            return
        try:
            callback(RunRecord.from_payload(dataclasses.asdict(run)), reason)
        except Exception:
            return

    def repair_recovery_fault(
        self, fault: dict[str, object]
    ) -> dict[str, object]:
        if not isinstance(fault, dict) or fault.get("scope") != "run":
            return {"terminal_safe": False, "error": "manifest_repair_required"}
        run_id = fault.get("run_id")
        operation_id = fault.get("launch_operation_id")
        expected_hash = fault.get("run_record_sha256")
        with self._operation_lock:
            if not isinstance(run_id, str):
                return {"terminal_safe": False, "error": "identity_ambiguous"}
            run = self.manifest.get(run_id)
            if (
                run is None
                or run.launch_operation_id != operation_id
                or run.launch_acknowledged
                or run.state not in _RECOVERY_REPAIR_STATES
            ):
                return {"terminal_safe": False, "error": "identity_ambiguous"}
            observed_hash = hashlib.sha256(
                json.dumps(
                    dataclasses.asdict(run),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if observed_hash != expected_hash:
                return {"terminal_safe": False, "error": "manifest_drift"}
            if not self._audit(
                "lifecycle_recovery_repair",
                None,
                "confirmed_repair",
                "allowed",
                run_id=run_id,
            ):
                return {"terminal_safe": False, "error": "audit_failed"}
            for record in run.processes:
                try:
                    actual = self.guard.snapshot(record.pid)
                except Exception:
                    actual = {"error": "guard_unavailable", "exit_code": 3}
                if (
                    isinstance(actual, dict)
                    and actual.get("error") == "process_not_found"
                    and actual.get("exit_code") == 4
                ):
                    continue
                if not self._identity_matches(record, actual):
                    return {
                        "terminal_safe": False,
                        "error": "identity_ambiguous",
                    }
                try:
                    terminated = self.guard.terminate(record)
                except Exception:
                    terminated = {"terminated": False}
                if (
                    not isinstance(terminated, dict)
                    or terminated.get("terminated") is not True
                ):
                    return {"terminal_safe": False, "error": "cleanup_failed"}
            run.state = "EXITED"
            run.owner_session_id = None
            run.owner_lease_id = None
            run.processes = []
            if not self._commit_retirement(
                run,
                "lifecycle_recovery_repair",
                "confirmed_repair",
                "repaired",
            ):
                return {"terminal_safe": False, "error": "manifest_drift"}
            return {
                "terminal_safe": True,
                "run_id": run_id,
                "state": "EXITED",
                "manifest_sha256": hashlib.sha256(
                    self.manifest.paths.runs_path.read_bytes()
                ).hexdigest(),
            }

    def repair_manifest_recovery(self, raw: bytes) -> dict[str, object]:
        if not isinstance(raw, bytes):
            return {"terminal_safe": False, "error": "manifest_drift"}
        try:
            payload = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("version") != 1
                or not isinstance(payload.get("runs"), list)
            ):
                raise ValueError("invalid_run_manifest")
            parsed = [RunRecord.from_payload(item) for item in payload["runs"]]
            if len({run.run_id for run in parsed}) != len(parsed):
                raise ValueError("invalid_run_manifest")
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return {"terminal_safe": False, "error": "manifest_drift"}

        with self._operation_lock:
            try:
                atomic_write_bytes(self.manifest.paths.runs_path, raw)
                restored = RunManifestStore(
                    self.manifest.paths,
                    checkpoint=self.manifest._checkpoint,
                )
            except Exception:
                return {"terminal_safe": False, "error": "manifest_drift"}
            self.manifest = restored
            self._invalidate_box_cache()
            for run in restored.list_runs():
                if (
                    run.launch_operation_id is None
                    or run.launch_acknowledged
                    or run.state == "EXITED"
                ):
                    continue
                if self._quarantined():
                    return self._manifest_repair_failure("identity_ambiguous")
                if not self._audit(
                    "lifecycle_manifest_recovery",
                    None,
                    "backup_restored",
                    "reconcile_unacknowledged",
                    run_id=run.run_id,
                ):
                    return self._manifest_repair_failure("cleanup_failed")
                for record in run.processes:
                    try:
                        actual = self.guard.snapshot(record.pid)
                    except Exception:
                        actual = {"error": "guard_unavailable", "exit_code": 3}
                    if (
                        isinstance(actual, dict)
                        and actual.get("error") == "process_not_found"
                        and actual.get("exit_code") == 4
                    ):
                        continue
                    if not self._identity_matches(record, actual):
                        return self._manifest_repair_failure(
                            "identity_ambiguous"
                        )
                    try:
                        terminated = self.guard.terminate(record)
                    except Exception:
                        terminated = {"terminated": False}
                    if (
                        not isinstance(terminated, dict)
                        or terminated.get("terminated") is not True
                    ):
                        return self._manifest_repair_failure("cleanup_failed")
                run.state = "EXITED"
                run.owner_session_id = None
                run.owner_lease_id = None
                run.processes = []
                if not self._commit_retirement(
                    run,
                    "lifecycle_manifest_recovery",
                    "backup_restored",
                    "repaired",
                ):
                    return self._manifest_repair_failure("manifest_drift")
            try:
                candidates = [
                    run.run_id
                    for run in restored.list_runs()
                    if run.state == "RUNNING" and run.owner_session_id is not None
                ]
                self._transition_to_idle(
                    candidates, restored.recover_after_restart
                )
            except Exception:
                return self._manifest_repair_failure("manifest_drift")
            return {
                "terminal_safe": True,
                "manifest_sha256": hashlib.sha256(
                    restored.paths.runs_path.read_bytes()
                ).hexdigest(),
            }

    def _manifest_repair_failure(self, error: str) -> dict[str, object]:
        try:
            manifest_sha256 = hashlib.sha256(
                self.manifest.paths.runs_path.read_bytes()
            ).hexdigest()
        except Exception:
            manifest_sha256 = None
        result: dict[str, object] = {
            "terminal_safe": False,
            "error": error,
        }
        if manifest_sha256 is not None:
            result["manifest_sha256"] = manifest_sha256
        return result

    def _run_all_dead(self, run: RunRecord) -> tuple[bool, str]:
        """True if every registered process is gone or foreign and the diag
        snapshot is known-clean. A still-owned survivor, an unknown snapshot,
        or an unexpected DayZ returns (False, reason). Identity mismatch is
        treated as foreign (not ours) and does not block reap. Never a basis
        to terminate."""
        buckets, unknown_reason = self._partition_registered_processes(run.processes)
        if buckets["owned"]:
            return False, "process_alive"
        if buckets["unknown"]:
            return False, unknown_reason or "process_identity_mismatch"
        diag_ok, diag_reason = self._diag_snapshot_registered(set())
        if not diag_ok:
            return False, diag_reason
        return True, ""

    def _reap_run_locked(self, run: RunRecord, *, client: ClientIdentity | None) -> str:
        """Audit-before-act retire of a confirmed-dead run to EXITED. Caller holds
        _operation_lock and has already proven _run_all_dead. Terminates nothing —
        there is no live process by construction. Returns "" on success, or the exact
        failure cause ("audit_failed" / "manifest_failed") so the agent path reports the
        real reason instead of conflating a disk failure with an audit failure. On a
        persist failure the manifest rolls back in-memory; the next pass converges."""
        buckets, _unknown_reason = self._partition_registered_processes(run.processes)
        if not self._audit(
            "run_reaped",
            client,
            "all_processes_gone_or_foreign",
            "reaped",
            run_id=run.run_id,
            dead_pids=[record.pid for record in run.processes],
            owned_pids=list(buckets["owned"]),
            gone_pids=list(buckets["gone"]),
            foreign_pids=list(buckets["foreign"]),
        ):
            return "audit_failed"
        owner_session_id = run.owner_session_id
        run.owner_session_id = None
        run.owner_lease_id = None
        run.processes = []
        run.state = "EXITED"
        if not self._commit_retirement(
            run,
            "run_reaped",
            "all_processes_gone_or_foreign",
            "reaped",
        ):
            return "manifest_failed"
        self.coordinator.note_run_reaped(owner_session_id)
        return ""

    def _reap_dead_runs_locked(self) -> list[str]:
        """Caller holds _operation_lock. Safe under retail quarantine: never
        terminates, only retires runs whose owned processes are already gone."""
        reaped: list[str] = []
        for run in self.manifest.list_runs():
            if run.state not in _REAPABLE_STATES:
                continue
            all_dead, _reason = self._run_all_dead(run)
            if not all_dead:
                continue
            if not self._reap_run_locked(run, client=None):
                reaped.append(run.run_id)
        return reaped

    def reap_dead_runs(self) -> list[str]:
        """Daemon housekeeping: retire every reapable run whose processes are all
        gone or foreign, so a crashed DayZ never permanently blocks the box (the run
        would otherwise linger active and fail every start/adopt/stop until a manual
        admin reconcile). Safe by construction — reaps only zero-owned-process runs
        and never calls terminate. Runs under retail quarantine; that pass is
        audited so a later ghost can be told apart from a skipped reaper."""
        with self._operation_lock:
            self._require_legacy_identity_safe()
            if self._quarantined():
                self._audit(
                    "reap_under_quarantine",
                    None,
                    "retail_quarantine",
                    "continued",
                )
            return self._reap_dead_runs_locked()

    def reap_dead_run(
        self, client: ClientIdentity, token: str | None, run_id: object
    ) -> dict[str, object]:
        """Agent-callable, non-TTY recovery restricted to the all-dead-or-foreign
        case. Lease-gated like other lifecycle ops. Rejects (run_not_reapable)
        any run with a still-owned process, an unavailable guard or an ambiguous
        diag — those keep the TTY-gated admin_reconcile path. Terminates nothing."""
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_reap"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        with self._operation_lock:
            if not self._reservation_active(authority, command):
                return self._error("lease_invalid", 409)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            if not isinstance(run_id, str) or not run_id:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            run = self.manifest.get(run_id)
            if run is None:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            if run.state not in _REAPABLE_STATES:
                return self._reject_reserved(authority, command, "run_not_reapable")
            all_dead, _reason = self._run_all_dead(run)
            if not all_dead:
                return self._reject_reserved(authority, command, "run_not_reapable")
            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)
            try:
                outcome = self._reap_run_locked(run, client=client)
                if outcome:
                    return self._error(outcome, 503)
                return {"ok": True, "run_id": run_id, "state": "EXITED"}
            finally:
                self._finish_committed(authority, command_id)

    def status(self, client: ClientIdentity) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        runs, diagnostics = self._status_snapshot()
        payload: dict[str, object] = {
            "runs": [self._projected_run(run) for run in runs],
            "retail_quarantine": self._quarantined(),
            "client": client.public_payload(),
            "retired_run_diagnostics": diagnostics,
        }
        if self._last_start_error is not None:
            payload["last_start_error"] = self._last_start_error
        return payload

    def public_status(self) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        runs, diagnostics = self._status_snapshot()
        # Keep every non-terminal state: admin recovery needs STARTING and STOPPING.
        active_runs = [run for run in runs if run.state in _ACTIVE_STATES]
        return {
            "runs": [self._projected_run(run) for run in active_runs],
            "runs_retired": len(runs) - len(active_runs),
            "retail_quarantine": self._quarantined(),
            "retired_run_diagnostics": diagnostics,
        }

    def _diag_snapshot(
        self,
    ) -> tuple[str | None, list[dict[str, object]] | None]:
        if self.diag_probe is None:
            return "diag_snapshot_unknown", None
        try:
            result = self.diag_probe()
        except Exception:
            return "diag_snapshot_unknown", None
        if not isinstance(result, dict) or result.get("known") is not True:
            return "diag_snapshot_unknown", None
        processes = result.get("processes")
        if not isinstance(processes, list):
            return "diag_snapshot_unknown", None
        observed: list[dict[str, object]] = []
        for process in processes:
            if not isinstance(process, dict):
                return "diag_snapshot_unknown", None
            pid = process.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                return "diag_snapshot_unknown", None
            observed.append(process)
        return None, observed

    def _port_holders(
        self,
    ) -> tuple[str | None, list[dict[str, object]] | None]:
        """Bound UDP ports with holder pid and image, or why the scan is unknown.

        No probe wired means the feature is absent (an empty, known scan), not
        an unknown one: daemons and fixtures built without it keep their
        behaviour. A wired probe that fails, or answers with a shape this code
        cannot vouch for, is unknown: launching on that would be a guess.
        """
        if self.port_probe is None:
            return None, []
        try:
            result = self.port_probe()
        except Exception:
            return "port_scan_unknown", None
        if not isinstance(result, dict) or result.get("known") is not True:
            return "port_scan_unknown", None
        holders = result.get("holders")
        if not isinstance(holders, list):
            return "port_scan_unknown", None
        observed: list[dict[str, object]] = []
        for holder in holders:
            if not isinstance(holder, dict):
                return "port_scan_unknown", None
            port = holder.get("port")
            if (
                not isinstance(port, int)
                or isinstance(port, bool)
                or not 1 <= port <= 65535
            ):
                return "port_scan_unknown", None
            pid = holder.get("pid")
            if pid is not None and (
                not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            ):
                return "port_scan_unknown", None
            name = holder.get("name")
            observed.append(
                {
                    "port": port,
                    "pid": pid,
                    "name": name if isinstance(name, str) else None,
                }
            )
        return None, observed

    def _foreign_port_reason(
        self, registered_pids: set[int], requested_port: int | None
    ) -> str | None:
        """Refuse to launch on top of a socket this lifecycle does not own.

        A holder is foreign when its pid is not a process of an active run (an
        unattributable pid counts as foreign). It blocks the launch when it is
        a DayZ image on any port, or any image on the port this launch asked
        for. Always a fresh probe: the 1.5 s box cache serves readers, and a
        server that appeared a second ago is exactly the case this guards
        (fb-20260904-114520-6927: a 6-minute-old foreign server on 2302 was
        launched over because the name probe did not list it).
        """
        reason, holders = self._port_holders()
        if reason is not None or holders is None:
            return reason or "port_scan_unknown"
        unattributed = False
        for holder in holders:
            pid = holder.get("pid")
            if isinstance(pid, int) and pid in registered_pids:
                continue
            name = holder.get("name")
            if _is_dayz_image(name) or (
                requested_port is not None and holder["port"] == requested_port
            ):
                return "port_in_use_foreign"
            if pid is None or name is None:
                unattributed = True
        # A holder nobody can be named for might be a DayZ image on another
        # port: the launch is refused until the table can be attributed.
        return "port_attribution_unknown" if unattributed else None

    def box_occupancy(self, *, now: float | None = None) -> dict[str, object]:
        """The box is derived from one snapshot; live state is never re-read after it.

        Observe managed runs plus live DayZDiag not owned by this lifecycle.
        Reuses ``diag_probe`` (the same scan ``start_run`` uses to reject a
        foreign diag). Argv decode is per-PID enrichment, not a second scan.
        An unknown snapshot is occupied: launching would be a guess.
        ``scan_known`` is False on that path (empty ``foreign`` is not proof
        of an empty box). Probe results (``foreign``, ``ports_in_use``,
        ``scan_known``) may be reused for 1.5 s; rows are not.
        """

        clock = time.time() if now is None else float(now)
        snapshot = self._take_box_snapshot(clock)
        probes = self._probes_for_snapshot(snapshot, use_cache=now is None)
        return _derive_box(snapshot, probes)

    def _take_box_snapshot(self, clock: float) -> _BoxSnapshot:
        # The seal must be a lower bound of the data it certifies.
        with self._activity_lock:
            revision = self._box_revision
        runs = tuple(self.manifest.list_runs())
        with self._activity_lock:
            generation = (
                self.daemon_generation
                if isinstance(self.daemon_generation, str)
                else ""
            )
            activity = {
                run_id: stamp
                for (gen, run_id), stamp in self._last_activity.items()
                if gen == generation
                and stamp
                > self._activity_tombstone.get((gen, run_id), stamp - 1.0)
            }
            unknown = frozenset(
                run_id
                for (gen, run_id) in self._activity_unknown
                if gen == generation
            )
            compensating = frozenset(
                run_id
                for (gen, run_id) in self._compensating_runs
                if gen == generation
            )
        return _BoxSnapshot(
            clock=clock,
            runs=runs,
            activity=MappingProxyType(activity),
            unknown=unknown,
            revision=revision,
            daemon_generation=generation,
            compensating=compensating,
        )

    def _collect_probes(self, snapshot: _BoxSnapshot) -> _BoxProbes:
        active = [run for run in snapshot.runs if run.state in _ACTIVE_STATES]
        registered_pids = {
            record.pid for run in active for record in run.processes
        }
        ports: list[int] = []

        def _remember_port(port: int) -> None:
            if port not in ports:
                ports.append(port)

        def _absorb_argv(pid: int) -> dict[str, object]:
            try:
                argv = self.argv_of(pid)
            except Exception:
                argv = None
            parsed = parse_dayz_launch_argv(argv or [])
            for port in parsed["ports"]:
                if isinstance(port, int):
                    _remember_port(port)
            return parsed

        for run in active:
            for record in run.processes:
                _absorb_argv(record.pid)

        reason, observed = self._diag_snapshot()
        if reason is not None or observed is None:
            return _BoxProbes(
                foreign=(),
                ports_in_use=tuple(ports),
                scan_known=False,
            )
        foreign: list[dict[str, object]] = []
        diag_foreign_pids: set[int] = set()
        for process in observed:
            pid = int(process["pid"])
            parsed = _absorb_argv(pid)
            if pid in registered_pids:
                continue
            diag_foreign_pids.add(pid)
            parsed_ports = parsed.get("ports")
            port = (
                parsed_ports[0]
                if isinstance(parsed_ports, list) and parsed_ports
                else None
            )
            foreign.append(
                {
                    "port": port if isinstance(port, int) else None,
                    "mods": list(parsed.get("mods") or []),
                    "profiles": _public_profiles_label(parsed.get("profiles")),
                }
            )
        # fb-20260904-114520-6927: the socket table is the second witness. A
        # DayZ image holding a UDP port is foreign even when the name probe
        # never saw it (other image, argv without -port); its ports and the
        # ports of registered processes come from the OS, not from argv.
        # Unrelated services (DNS, IKE) never touch the box.
        port_reason, holders = self._port_holders()
        if port_reason is not None or holders is None:
            return _BoxProbes(
                foreign=tuple(foreign),
                ports_in_use=tuple(ports),
                scan_known=True,
                port_scan_known=False,
                port_scan_reason=port_reason or "port_scan_unknown",
            )
        rows_by_pid: dict[int, dict[str, object]] = {}
        unattributed = False
        foreign_ports: list[int] = []
        for holder in holders:
            pid = holder.get("pid")
            port = holder["port"]
            name = holder.get("name")
            registered = isinstance(pid, int) and pid in registered_pids
            dayz = _is_dayz_image(name)
            if registered or dayz or int(port) in _DAYZ_PORT_RANGE:
                _remember_port(int(port))
            if registered:
                continue
            if int(port) not in foreign_ports:
                foreign_ports.append(int(port))
            if pid is None or name is None:
                # A socket nobody can be named for cannot be cleared as
                # not-DayZ: the scan is unknown, the box occupied.
                unattributed = True
                continue
            if not dayz or pid in diag_foreign_pids:
                continue
            row = rows_by_pid.get(int(pid))
            if row is None:
                row = {
                    "port": int(port),
                    "mods": [],
                    "profiles": None,
                    "image": name,
                    "source": "port",
                }
                rows_by_pid[int(pid)] = row
                foreign.append(row)
            elif int(port) < int(row["port"]):
                row["port"] = int(port)
        if unattributed:
            return _BoxProbes(
                foreign=tuple(foreign),
                ports_in_use=tuple(ports),
                scan_known=True,
                port_scan_known=False,
                port_scan_reason="port_attribution_unknown",
                foreign_ports=tuple(sorted(foreign_ports)),
            )
        return _BoxProbes(
            foreign=tuple(foreign),
            ports_in_use=tuple(ports),
            scan_known=True,
            port_scan_known=True,
            foreign_ports=tuple(sorted(foreign_ports)),
        )

    def _probes_for_snapshot(
        self, snapshot: _BoxSnapshot, *, use_cache: bool
    ) -> _BoxProbes:
        if use_cache:
            with self._activity_lock:
                cached = self._box_cache
            if cached is not None:
                cached_at, cached_revision, cached_probes = cached
                if (
                    cached_revision == snapshot.revision
                    and time.monotonic() - cached_at < _BOX_OCCUPANCY_CACHE_S
                ):
                    return cached_probes
        probes = self._collect_probes(snapshot)
        if use_cache:
            with self._activity_lock:
                if self._box_revision == snapshot.revision:
                    self._box_cache = (time.monotonic(), snapshot.revision, probes)
        return probes

    def _reconcile_survivors(
        self, processes: list[ProcessRecord]
    ) -> tuple[list[ProcessRecord], str | None, int]:
        survivors: list[ProcessRecord] = []
        for record in processes:
            try:
                actual = self.guard.snapshot(record.pid)
            except Exception:
                actual = {"error": "guard_unavailable", "exit_code": 3}
            if (
                isinstance(actual, dict)
                and actual.get("error") == "process_not_found"
                and actual.get("exit_code") == 4
            ):
                continue
            if self._identity_matches(record, actual):
                survivors.append(record)
                continue
            if isinstance(actual, dict) and actual.get("exit_code") == 3:
                return [], "guard_unavailable", 503
            return [], "process_identity_mismatch", 409
        return survivors, None, 200

    def _diag_snapshot_registered(
        self, registered_pids: set[int]
    ) -> tuple[bool, str]:
        if self.diag_probe is None:
            return False, "diag_snapshot_unknown"
        try:
            result = self.diag_probe()
        except Exception:
            return False, "diag_snapshot_unknown"
        if not isinstance(result, dict) or result.get("known") is not True:
            return False, "diag_snapshot_unknown"
        processes = result.get("processes")
        if not isinstance(processes, list):
            return False, "diag_snapshot_unknown"
        observed: set[int] = set()
        for process in processes:
            if not isinstance(process, dict):
                return False, "diag_snapshot_unknown"
            pid = process.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                return False, "diag_snapshot_unknown"
            observed.add(pid)
        if observed != registered_pids:
            return False, "manual_cleanup_required"
        return True, ""

    def _diag_snapshot_empty(self) -> tuple[bool, str]:
        if self.diag_probe is None:
            return False, "diag_snapshot_unknown"
        try:
            result = self.diag_probe()
        except Exception:
            return False, "diag_snapshot_unknown"
        if not isinstance(result, dict) or result.get("known") is not True:
            return False, "diag_snapshot_unknown"
        processes = result.get("processes")
        if not isinstance(processes, list):
            return False, "diag_snapshot_unknown"
        if processes:
            return False, "manual_cleanup_required"
        # fb-20260904-114520-6927: "empty" also means no DayZ image holds a UDP
        # port. An unreadable table cannot certify emptiness.
        port_reason, holders = self._port_holders()
        if port_reason is not None or holders is None:
            return False, port_reason or "port_scan_unknown"
        for holder in holders:
            if _is_dayz_image(holder.get("name")):
                return False, "manual_cleanup_required"
            if holder.get("pid") is None or holder.get("name") is None:
                return False, "port_attribution_unknown"
        return True, ""

    def _foreign_diag_reason(self, registered_pids: set[int]) -> str | None:
        reason, processes = self._diag_snapshot()
        if reason is not None or processes is None:
            return reason or "diag_snapshot_unknown"
        observed = {int(process["pid"]) for process in processes}
        # Registered PIDs absent from the snapshot are pending reap, not foreign.
        if observed - registered_pids:
            return "foreign_diag_process"
        return None

    def admin_reconcile(
        self,
        run_id: object,
        pid: object,
        reason: str,
        *,
        empty: bool = False,
    ) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        if self._quarantined():
            return self._error("retail_quarantine", 409)
        if not isinstance(reason, str) or not reason.strip():
            return self._error("invalid_reason", 400)
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(empty, bool)
            or (
                empty
                and pid is not None
            )
            or (
                not empty
                and (
                    not isinstance(pid, int)
                    or isinstance(pid, bool)
                    or pid <= 0
                )
            )
        ):
            return self._error("invalid_reconcile_request", 400)
        with self._operation_lock:
            run = self.manifest.get(run_id)
            if run is None:
                return self._error("run_not_found", 404)
            idle_ownerless = (
                run.state == "RUNNING_IDLE"
                and run.owner_session_id is None
                and run.owner_lease_id is None
            )
            if run.state not in {"UNRECONCILED", "STARTING", "STOPPING"} and not idle_ownerless:
                return self._error("run_not_reconcilable", 409)
            if empty:
                if run.processes:
                    return self._error("invalid_reconcile_request", 400)
                empty_ok, empty_error = self._diag_snapshot_empty()
                if not empty_ok:
                    return self._error(empty_error, 409)
                survivors: list[ProcessRecord] = []
            else:
                selected = next(
                    (record for record in run.processes if record.pid == pid), None
                )
                if selected is None:
                    return self._error("process_not_registered", 404)
                survivors, survivor_error, survivor_status = self._reconcile_survivors(
                    run.processes
                )
                if survivor_error is not None:
                    return self._error(survivor_error, survivor_status)
                diag_ok, diag_error = self._diag_snapshot_registered(
                    {record.pid for record in survivors}
                )
                if not diag_ok:
                    return self._error(diag_error, 409)
            if self._quarantined():
                return self._error("retail_quarantine", 409)
            if not self._audit(
                "admin_reconcile",
                None,
                reason.strip(),
                "confirmed",
                run_id=run_id,
                pid=pid,
                empty=empty,
            ):
                return self._error("audit_failed", 503)
            if self._quarantined():
                return self._error("retail_quarantine", 409)
            if empty:
                empty_ok, empty_error = self._diag_snapshot_empty()
                if not empty_ok:
                    return self._error(empty_error, 409)
            else:
                survivors, survivor_error, survivor_status = self._reconcile_survivors(
                    survivors
                )
                if survivor_error is not None:
                    return self._error(survivor_error, survivor_status)
                diag_ok, diag_error = self._diag_snapshot_registered(
                    {record.pid for record in survivors}
                )
                if not diag_ok:
                    return self._error(diag_error, 409)
            run.owner_session_id = None
            run.owner_lease_id = None
            run.processes = survivors
            if (
                run.state in {"STARTING", "STOPPING"}
                and survivors
                and run.launch_operation_id is not None
            ):
                run.launch_acknowledged = True
            run.state = "RUNNING_IDLE" if survivors else "EXITED"
            if run.state == "EXITED":
                if not self._commit_retirement(
                    run,
                    "admin_reconcile",
                    "admin_reconciled",
                    "confirmed",
                ):
                    return self._error("manifest_failed", 503)
            else:
                try:
                    self._transition_to_idle(
                        [run.run_id],
                        lambda: self.manifest.replace(run),
                    )
                except Exception:
                    return self._error("manifest_failed", 503)
                self._invalidate_box_cache()
            result: dict[str, object] = {
                "reconciled": True,
                "run_id": run_id,
                "state": run.state,
            }
            if empty:
                result["empty"] = True
            else:
                result["pid"] = pid
            return result
