from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

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


def _wait_hint(box: object) -> str:
    if not isinstance(box, dict):
        return _ACTIVE_RUN_WAIT_HINT
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
            payload["foreign"] = False
            payload["hint"] = (
                _ACTIVE_RUN_STOP_HINT.format(run_id=run_id)
                if _caller_owns_run(item, caller_session)
                else _wait_hint(box)
            )
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


class ProcessLifecycle:
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
        launcher: Launcher | None = None,
        id_fn: Callable[[], str] | None = None,
        recovery_fault_arm: RecoveryFaultArm | None = None,
        argv_of: Callable[[int], list[str] | None] | None = None,
        bindings: object | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.manifest = manifest
        self.audit = audit
        self.guard = guard
        self.retail_probe = retail_probe
        self.diag_probe = diag_probe
        self.game_path = Path(game_path).resolve()
        self.launcher = launcher or self._launch
        self.id_fn = id_fn or (lambda: uuid.uuid4().hex)
        self.recovery_fault_arm = recovery_fault_arm
        self.argv_of = argv_of or _default_argv_of
        self.bindings = bindings
        self._box_cache: tuple[float, dict[str, object]] | None = None
        self._operation_lock = threading.RLock()
        self._command_id = -1
        self._last_start_error: str | None = None

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
    ) -> dict[str, object]:
        """Leave every failed Popen attempt in a durable, reconcilable state."""

        self._last_start_error = confirmed_error
        confirmed_closed = launched is None or self._terminate_open_handle(launched)
        persistence_failed = False
        if confirmed_closed:
            if previous is not None:
                target = RunRecord.from_payload(dataclasses.asdict(previous))
            else:
                target = RunRecord.from_payload(dataclasses.asdict(provisional))
                target.state = "EXITED"
                target.owner_session_id = None
                target.owner_lease_id = None
                target.processes = []
            try:
                self.manifest.replace(target)
            except Exception:
                persistence_failed = True
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
            try:
                self.manifest.replace(target)
            except Exception:
                persistence_failed = True
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

            launched: object | None = None
            record: ProcessRecord | None = None
            minted: str | None = None
            launch_role = str(parsed["role"])
            try:
                if self._quarantined():
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=None,
                        record=None,
                        confirmed_error="retail_quarantine",
                    )
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
                    )
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
                    )

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
                try:
                    self.manifest.replace(run)
                except Exception:
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
                self._retire_run_bindings(run_id, "stopped")
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
            if run.state != "RUNNING_IDLE" or run.owner_session_id is not None:
                return self._reject_reserved(authority, command, "run_not_adoptable")
            if any(
                other.state in _ACTIVE_STATES and other.run_id != run_id
                for other in self.manifest.list_runs()
            ):
                return self._reject_reserved(authority, command, "active_run_exists")
            for record in run.processes:
                kind, reason = self._classify_registered_process(record)
                if kind == "unknown":
                    return self._reject_reserved(
                        authority,
                        command,
                        reason,
                        503 if reason == "guard_unavailable" else 409,
                    )
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            if not self._audit("lifecycle_adopt", client, "identity_match", "allowed", run_id=run_id):
                self.coordinator.reject_reservation(
                    authority[0], authority[1], authority[2], "audit_failed"
                )
                return self._error("audit_failed", 503)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)
            run.owner_session_id = client.session_id
            run.owner_lease_id = authority[1]
            run.state = "RUNNING"
            try:
                self.manifest.replace(run)
            except Exception:
                self._finish_committed(authority, command_id)
                return self._error("manifest_failed", 503)
            self._finish_committed(authority, command_id)
            return {"ok": True, "run_id": run_id, "state": "RUNNING"}

    def release_owner(self, session_id: str, lease_id: str) -> list[str]:
        with self._operation_lock:
            self._require_legacy_identity_safe()
            return self.manifest.release_owner(session_id, lease_id)

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
                result.update(
                    {
                        "terminal_safe": True,
                        "runs_released": self.manifest.release_owner(
                            session_id, lease_id
                        ),
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
                                current.state = "UNRECONCILED"
                                current.owner_session_id = None
                                current.owner_lease_id = None
                                try:
                                    self.manifest.replace(current)
                                except Exception:
                                    pass
                                self._arm_cleanup_fault(
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
                                current.state = "UNRECONCILED"
                                current.owner_session_id = None
                                current.owner_lease_id = None
                                try:
                                    self.manifest.replace(current)
                                except Exception:
                                    pass
                                self._arm_cleanup_fault(current, "cleanup_failed")
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
                        self.manifest.replace(current)
                        released.append(current.run_id)
                        self._retire_run_bindings(current.run_id, "released")
                    released.extend(
                        self.manifest.release_owner(session_id, lease_id)
                    )
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
            try:
                self.manifest.replace(run)
            except Exception:
                return {"terminal_safe": False, "error": "manifest_drift"}
            self._retire_run_bindings(run_id, "recovery_repaired")
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
                try:
                    restored.replace(run)
                except Exception:
                    return self._manifest_repair_failure("manifest_drift")
                self._retire_run_bindings(run.run_id, "manifest_repaired")
            try:
                restored.recover_after_restart()
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
        try:
            self.manifest.replace(run)
        except Exception:
            return "manifest_failed"
        self._retire_run_bindings(run.run_id, "reaped")
        self._box_cache = None
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
        payload: dict[str, object] = {
            "runs": [dataclasses.asdict(run) for run in self.manifest.list_runs()],
            "retail_quarantine": self._quarantined(),
            "client": client.public_payload(),
        }
        if self._last_start_error is not None:
            payload["last_start_error"] = self._last_start_error
        return payload

    def public_status(self) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        runs = self.manifest.list_runs()
        # Keep every non-terminal state: admin recovery needs STARTING and STOPPING.
        active_runs = [run for run in runs if run.state in _ACTIVE_STATES]
        return {
            "runs": [dataclasses.asdict(run) for run in active_runs],
            "runs_retired": len(runs) - len(active_runs),
            "retail_quarantine": self._quarantined(),
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

    def box_occupancy(self, *, now: float | None = None) -> dict[str, object]:
        """Observe managed runs plus live DayZDiag not owned by this lifecycle.

        Reuses ``diag_probe`` (the same scan ``start_run`` uses to reject a
        foreign diag). Argv decode is per-PID enrichment, not a second scan.
        An unknown snapshot is occupied: launching would be a guess.
        ``scan_known`` is False on that path (empty ``foreign`` is not proof
        of an empty box).
        """

        if now is None and self._box_cache is not None:
            cached_at, cached = self._box_cache
            if time.monotonic() - cached_at < _BOX_OCCUPANCY_CACHE_S:
                return _copy_box(cached)
        snapshot = self._box_occupancy_uncached(
            time.time() if now is None else float(now)
        )
        if now is None:
            self._box_cache = (time.monotonic(), snapshot)
        return _copy_box(snapshot)

    def _box_occupancy_uncached(self, clock: float) -> dict[str, object]:
        active = [
            run for run in self.manifest.list_runs() if run.state in _ACTIVE_STATES
        ]
        registered_pids = {
            record.pid for run in active for record in run.processes
        }
        runs: list[dict[str, object]] = []
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
            owner = run.owner_session_id
            for record in run.processes:
                _absorb_argv(record.pid)
            runs.append(
                {
                    "run_id": run.run_id,
                    "mod": run.mod,
                    "label": run.label,
                    "age_s": round(_run_age_s(run, clock), 3),
                    "owner_session": owner[:12] if isinstance(owner, str) and owner else None,
                    "state": run.state,
                }
            )

        reason, observed = self._diag_snapshot()
        foreign: list[dict[str, object]] = []
        if reason is not None or observed is None:
            return {
                "occupied": True,
                "runs": runs,
                "foreign": foreign,
                "ports_in_use": ports,
                "queue": [],
                "scan_known": False,
            }
        for process in observed:
            pid = int(process["pid"])
            parsed = _absorb_argv(pid)
            if pid in registered_pids:
                continue
            parsed_ports = parsed.get("ports")
            port = parsed_ports[0] if isinstance(parsed_ports, list) and parsed_ports else None
            foreign.append(
                {
                    "port": port if isinstance(port, int) else None,
                    "mods": list(parsed.get("mods") or []),
                    "profiles": _public_profiles_label(parsed.get("profiles")),
                }
            )
        return {
            "occupied": bool(runs or foreign),
            "runs": runs,
            "foreign": foreign,
            "ports_in_use": ports,
            "queue": [],
            "scan_known": True,
        }

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
            run.state = "RUNNING_IDLE" if survivors else "EXITED"
            try:
                self.manifest.replace(run)
            except Exception:
                return self._error("manifest_failed", 503)
            if run.state == "EXITED":
                self._retire_run_bindings(run.run_id, "admin_reconciled")
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
