from __future__ import annotations

import json
import hashlib
import math
import os
import secrets
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping

try:
    import msvcrt
except ImportError:  # pragma: no cover - P0.S is Windows-only.
    msvcrt = None  # type: ignore[assignment]


SECRET_KEYS = frozenset(
    {"key", "api_key", "keyfile", "lease_token", "password", "token"}
)
AUDIT_MAX_BYTES = 5 * 1024 * 1024
AUDIT_BACKUPS = 5

_REDACTED = "[REDACTED]"
_NON_PERSISTED_KEYS = frozenset(
    {"args", "cmdline", "command_line", "commandline", "request_args"}
)
_FAULT_STATES = frozenset({"armed", "fault", "completed", "repairing", "repaired"})
_FAULT_OPERATIONS = frozenset({"grant", "release"})
_FAULT_PHASES = frozenset(
    {
        "armed",
        "prepared",
        "committed",
        "published",
        "snapshot_persisted",
        "audit_failed",
        "snapshot_failed",
        "coordination_changed",
        "compensation_failed",
        "terminal_transition_failed",
        "release_audit_stalled",
        "repairing",
        "repaired",
    }
)
_FAULT_FAILURES = frozenset(
    {
        "audit_failed",
        "snapshot_failed",
        "coordination_changed",
        "compensation_failed",
        "terminal_transition_failed",
        "release_audit_stalled",
        "wal_completion_mismatch",
        "fault_marker_missing",
        "fault_store_unreadable",
        "repair_failed",
    }
)
_FAULT_REPAIR_PHASES = frozenset({"none", "compensation", "repair_event"})
_ARMED_PROGRESS_PHASES = ("armed", "prepared", "committed", "published")
_FAULT_FAILURES_BY_PHASE = {
    "audit_failed": frozenset(
        {"audit_failed", "fault_marker_missing", "fault_store_unreadable"}
    ),
    "snapshot_failed": frozenset(
        {"snapshot_failed", "wal_completion_mismatch", "repair_failed"}
    ),
    "coordination_changed": frozenset({"coordination_changed"}),
    "compensation_failed": frozenset({"compensation_failed"}),
    "terminal_transition_failed": frozenset({"terminal_transition_failed"}),
    "release_audit_stalled": frozenset({"release_audit_stalled"}),
}
_FAULT_KEYS = frozenset(
    {
        "format_version",
        "fault_id",
        "daemon_generation",
        "state",
        "operation",
        "phase",
        "lease_id",
        "ticket_id",
        "client",
        "reason",
        "armed_at_utc",
        "failure",
        "expected_snapshot_revision",
        "repair_phase",
    }
)
_FAULT_CLIENT_KEYS = frozenset(
    {"platform", "session", "started_at_utc", "task_label"}
)
_UNSET = object()
_COORDINATION_FAULT_PROCESS_LOCK = threading.Lock()


def _valid_coordination_fault_semantics(payload: Mapping[str, object]) -> bool:
    """Validate existing v1 fields as one closed WAL state tuple.

    Valid legacy v1 progress markers remain accepted; no key, enum, or on-disk
    format is added here.
    """

    state = payload.get("state")
    operation = payload.get("operation")
    phase = payload.get("phase")
    failure = payload.get("failure")
    repair_phase = payload.get("repair_phase")
    if state == "armed":
        if repair_phase != "none":
            return False
        if phase in _ARMED_PROGRESS_PHASES:
            return failure is None
        return (
            operation == "grant"
            and phase == "coordination_changed"
            and failure == "coordination_changed"
        )
    if state == "completed":
        return (
            phase == "snapshot_persisted"
            and repair_phase == "none"
            and (
                failure is None
                or (operation == "grant" and failure == "coordination_changed")
            )
        )
    if state == "fault":
        allowed_failures = _FAULT_FAILURES_BY_PHASE.get(str(phase))
        return allowed_failures is not None and failure in allowed_failures
    if state == "repairing":
        return (
            phase == "repairing"
            and failure in _FAULT_FAILURES
            and repair_phase in {"none", "compensation", "repair_event"}
        )
    if state == "repaired":
        return (
            phase == "repaired"
            and failure in _FAULT_FAILURES
            and repair_phase == "repair_event"
        )
    return False


def _valid_coordination_fault_transition(
    current: Mapping[str, object], updated: Mapping[str, object]
) -> bool:
    if current.get("state") != updated.get("state"):
        return True
    state = current.get("state")
    if state == "armed":
        current_phase = current.get("phase")
        updated_phase = updated.get("phase")
        if current_phase == "coordination_changed":
            return updated_phase == "coordination_changed"
        if updated_phase == "coordination_changed":
            return True
        try:
            return _ARMED_PROGRESS_PHASES.index(str(updated_phase)) >= (
                _ARMED_PROGRESS_PHASES.index(str(current_phase))
            )
        except ValueError:
            return False
    if state == "repairing":
        order = {"none": 0, "compensation": 1, "repair_event": 2}
        return order[str(updated.get("repair_phase"))] >= order[
            str(current.get("repair_phase"))
        ]
    if state in {"completed", "repaired"}:
        return all(
            current.get(field) == updated.get(field)
            for field in ("phase", "failure", "repair_phase")
        )
    return True


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    audit_dir: Path
    coordination_path: Path
    runs_path: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimePaths":
        values = os.environ if env is None else env
        base = values.get("LOCALAPPDATA", "")
        if not base:
            raise RuntimeError("localappdata_unavailable")
        root = Path(base) / "DayZ_MCP"
        return cls(root, root / "audit", root / "coordination.json", root / "runs.json")

    @property
    def coordination_fault_path(self) -> Path:
        return self.root / "coordination-fault.json"

    @property
    def lifecycle_recovery_faults_dir(self) -> Path:
        return self.root / "lifecycle-recovery-faults"

    @property
    def lifecycle_recovery_active_path(self) -> Path:
        return self.root / "lifecycle-recovery-active.json"

    @property
    def lifecycle_manifest_checkpoint_path(self) -> Path:
        return self.root / "lifecycle-manifest-checkpoint.json"


@dataclass(frozen=True)
class CoordinationStartupRecovery:
    audit_fault: dict[str, object] | None
    snapshot: dict[str, object] | None
    snapshot_status: str
    quarantined_snapshot_path: Path | None = None
    fault_materialized: bool = True

    @property
    def can_consume_snapshot(self) -> bool:
        return self.fault_materialized and self.snapshot_status in {
            "missing",
            "valid",
            "quarantined",
        }


@dataclass(frozen=True)
class _StartupSnapshotEvidence:
    snapshot: dict[str, object] | None
    status: str
    raw: bytes | None = None
    identity: tuple[int, int, int, int, int, int] | None = None


class JsonlAuditWriter:
    def __init__(
        self,
        paths: RuntimePaths,
        daemon_generation: str,
        utc_now_fn: Callable[[], str] | None = None,
    ) -> None:
        self._paths = paths
        self._daemon_generation = _require_generation(daemon_generation)
        self._utc_now_fn = utc_now_fn or _utc_now
        self._lock = threading.Lock()
        self.current_path = paths.audit_dir / "events.jsonl"

    def write(self, event: dict[str, object]) -> bool:
        payload = self._prepare_payload(event)
        with self._lock:
            self._append_payload_locked(payload)
        return True

    def write_once(self, event_id: str, event: dict[str, object]) -> bool:
        if not _bounded_text(event_id, 200):
            raise ValueError("invalid_audit_event_id")
        payload = self._prepare_payload(event, event_id=event_id)
        wanted_core = self._canonical_core(payload)
        with self._lock:
            paths = [self.current_path]
            paths.extend(
                Path(f"{self.current_path}.{index}")
                for index in range(1, AUDIT_BACKUPS + 1)
            )
            for path in paths:
                if not path.exists():
                    continue
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeError) as exc:
                    raise ValueError("invalid_audit_jsonl") from exc
                for line in lines:
                    try:
                        existing = json.loads(line)
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise ValueError("invalid_audit_jsonl") from exc
                    if not isinstance(existing, dict):
                        raise ValueError("invalid_audit_jsonl")
                    if existing.get("event_id") != event_id:
                        continue
                    if self._canonical_core(existing) != wanted_core:
                        raise ValueError("audit_event_id_conflict")
                    return True
            self._append_payload_locked(payload)
        return True

    def _prepare_payload(
        self, event: dict[str, object], *, event_id: str | None = None
    ) -> dict[str, object]:
        if not isinstance(event, dict):
            raise TypeError("event_must_be_dict")
        payload = _redact(event)
        if not isinstance(payload, dict):
            raise ValueError("invalid_audit_event")
        if event_id is not None:
            existing_id = payload.get("event_id")
            if existing_id not in (None, event_id):
                raise ValueError("audit_event_id_conflict")
            payload["event_id"] = event_id
        reason = payload.get("reason")
        duration = payload.get("duration_s")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration))
            or duration < 0.0
        ):
            raise ValueError("invalid_audit_event")
        payload.setdefault("decision", "")
        payload["timestamp_utc"] = self._utc_now_fn()
        payload["daemon_generation"] = self._daemon_generation
        return payload

    @staticmethod
    def _canonical_core(payload: dict[str, object]) -> str:
        core = {
            key: value
            for key, value in payload.items()
            if key not in {"timestamp_utc", "daemon_generation"}
        }
        return json.dumps(
            core,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _append_payload_locked(self, payload: dict[str, object]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        current_size = (
            self.current_path.stat().st_size if self.current_path.exists() else 0
        )
        if current_size + len(line.encode("utf-8")) > AUDIT_MAX_BYTES:
            self._rotate_locked()
        previous = ""
        if self.current_path.exists():
            previous = self.current_path.read_text(encoding="utf-8")
        _atomic_write_text(self.current_path, previous + line)

    def _rotate_locked(self) -> None:
        oldest = Path(f"{self.current_path}.{AUDIT_BACKUPS}")
        try:
            oldest.unlink()
        except FileNotFoundError:
            pass
        for index in range(AUDIT_BACKUPS - 1, 0, -1):
            source = Path(f"{self.current_path}.{index}")
            if source.exists():
                os.replace(source, Path(f"{self.current_path}.{index + 1}"))
        if self.current_path.exists():
            os.replace(self.current_path, Path(f"{self.current_path}.1"))


@contextmanager
def _coordination_fault_transaction(path: Path) -> Iterator[None]:
    """Serialize fault-marker CAS across store instances and daemon candidates."""

    if msvcrt is None:
        raise RuntimeError("coordination_fault_lock_unavailable")
    with _COORDINATION_FAULT_PROCESS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        _safe_directory(path.parent)
        lock_path = path.with_name(f".{path.name}.lock")
        if _path_lexists(lock_path):
            _safe_regular_lstat(lock_path)
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        acquired = False
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            opened = os.fstat(descriptor)
            if not _safe_regular_stat(opened):
                raise RuntimeError("coordination_fault_lock_unavailable")
            named = _safe_regular_lstat(lock_path)
            if _stat_identity(opened)[:2] != _stat_identity(named)[:2]:
                raise RuntimeError("coordination_fault_lock_unavailable")
            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            acquired = True
            named = _safe_regular_lstat(lock_path)
            if _stat_identity(opened)[:2] != _stat_identity(named)[:2]:
                raise RuntimeError("coordination_fault_lock_unavailable")
        except (OSError, RuntimeError) as exc:
            if descriptor is not None:
                os.close(descriptor)
            if isinstance(exc, RuntimeError) and str(exc) == (
                "coordination_fault_lock_unavailable"
            ):
                raise
            raise RuntimeError("coordination_fault_lock_unavailable") from exc
        try:
            yield
        finally:
            try:
                if acquired:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)


class CoordinationFaultStore:
    def __init__(self, paths: RuntimePaths) -> None:
        self.path = paths.coordination_fault_path
        self._lock = threading.Lock()

    @staticmethod
    def validate(payload: object) -> dict[str, object]:
        if not isinstance(payload, dict) or set(payload) != _FAULT_KEYS:
            raise ValueError("invalid_coordination_fault")
        client = payload.get("client")
        if not isinstance(client, dict) or set(client) != _FAULT_CLIENT_KEYS:
            raise ValueError("invalid_coordination_fault")
        text_fields = (
            (payload.get("fault_id"), 200),
            (payload.get("daemon_generation"), 200),
            (payload.get("lease_id"), 200),
            (payload.get("reason"), 160),
            (payload.get("armed_at_utc"), 64),
            (client.get("session"), 200),
            (client.get("started_at_utc"), 64),
        )
        ticket_id = payload.get("ticket_id")
        revision = payload.get("expected_snapshot_revision")
        failure = payload.get("failure")
        state = payload.get("state")
        operation = payload.get("operation")
        phase = payload.get("phase")
        repair_phase = payload.get("repair_phase")
        platform = client.get("platform")
        if (
            payload.get("format_version") != 1
            or any(not _bounded_text(value, limit) for value, limit in text_fields)
            or not isinstance(platform, str)
            or platform not in {"claude", "codex", "unknown"}
            or not isinstance(client.get("task_label"), str)
            or len(str(client.get("task_label"))) > 120
            or not isinstance(state, str)
            or state not in _FAULT_STATES
            or not isinstance(operation, str)
            or operation not in _FAULT_OPERATIONS
            or not isinstance(phase, str)
            or phase not in _FAULT_PHASES
            or not isinstance(repair_phase, str)
            or repair_phase not in _FAULT_REPAIR_PHASES
            or (ticket_id is not None and not _bounded_text(ticket_id, 200))
            or (
                failure is not None
                and (
                    not isinstance(failure, str) or failure not in _FAULT_FAILURES
                )
            )
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 0
            or not _valid_utc_timestamp(str(payload.get("armed_at_utc")))
            or not _valid_utc_timestamp(str(client.get("started_at_utc")))
            or not _valid_coordination_fault_semantics(payload)
        ):
            raise ValueError("invalid_coordination_fault")
        return json.loads(json.dumps(payload, ensure_ascii=False))

    def load_with_sha(self) -> tuple[dict[str, object] | None, str | None]:
        with self._lock:
            return self._read_locked()

    def load(self) -> dict[str, object] | None:
        return self.load_with_sha()[0]

    def arm(self, payload: dict[str, object]) -> str:
        marker = self.validate(payload)
        if marker["state"] != "armed":
            raise ValueError("invalid_coordination_fault_transition")
        with self._lock:
            with _coordination_fault_transaction(self.path):
                current, _ = self._read_locked()
                if current is not None:
                    raise RuntimeError("coordination_fault_exists")
                try:
                    return self._write_locked(marker, expected_sha256=None)
                except RuntimeError as exc:
                    if str(exc) in {"atomic_target_changed", "runtime_path_changed"}:
                        raise RuntimeError("coordination_fault_exists") from exc
                    raise

    def materialize_fault(self, payload: dict[str, object]) -> str:
        """Create a recovered repairable fault without a transient clean window."""

        marker = self.validate(payload)
        if marker["state"] != "fault":
            raise ValueError("invalid_coordination_fault_transition")
        with self._lock:
            with _coordination_fault_transaction(self.path):
                current, observed_sha = self._read_locked()
                if current is not None:
                    if current == marker and isinstance(observed_sha, str):
                        return observed_sha
                    raise RuntimeError("coordination_fault_exists")
                try:
                    return self._write_locked(marker, expected_sha256=None)
                except RuntimeError as exc:
                    if str(exc) not in {
                        "atomic_target_changed",
                        "runtime_path_changed",
                    }:
                        raise
                    current, observed_sha = self._read_locked()
                    if current == marker and isinstance(observed_sha, str):
                        return observed_sha
                    raise RuntimeError("coordination_fault_exists") from exc

    def transition(
        self,
        fault_id: str,
        expected_sha256: str,
        *,
        state: str,
        phase: str,
        failure: object = _UNSET,
        expected_snapshot_revision: object = _UNSET,
        repair_phase: object = _UNSET,
    ) -> str:
        with self._lock:
            with _coordination_fault_transaction(self.path):
                current, observed_sha = self._read_locked()
                if (
                    current is None
                    or current.get("fault_id") != fault_id
                    or observed_sha != expected_sha256
                ):
                    raise RuntimeError("coordination_fault_cas_conflict")
                allowed = {
                    "armed": {"armed", "fault", "completed"},
                    "fault": {"fault", "repairing"},
                    "repairing": {"repairing", "repaired"},
                    "completed": {"completed", "fault"},
                    "repaired": {"repaired", "fault"},
                }
                if state not in allowed[str(current["state"])]:
                    raise ValueError("invalid_coordination_fault_transition")
                updated = dict(current)
                updated["state"] = state
                updated["phase"] = phase
                if failure is not _UNSET:
                    updated["failure"] = failure
                if expected_snapshot_revision is not _UNSET:
                    updated["expected_snapshot_revision"] = expected_snapshot_revision
                if repair_phase is not _UNSET:
                    updated["repair_phase"] = repair_phase
                validated = self.validate(updated)
                if not _valid_coordination_fault_transition(current, validated):
                    raise ValueError("invalid_coordination_fault_transition")
                try:
                    return self._write_locked(
                        validated, expected_sha256=observed_sha
                    )
                except RuntimeError as exc:
                    if str(exc) in {"atomic_target_changed", "runtime_path_changed"}:
                        raise RuntimeError("coordination_fault_cas_conflict") from exc
                    raise

    def clear(self, fault_id: str, expected_sha256: str) -> bool:
        with self._lock:
            with _coordination_fault_transaction(self.path):
                current, observed_sha, observed_identity = (
                    self._read_locked_with_identity()
                )
                if (
                    current is None
                    or current.get("fault_id") != fault_id
                    or observed_sha != expected_sha256
                    or observed_identity is None
                ):
                    raise RuntimeError("coordination_fault_cas_conflict")
                if current.get("state") not in {"completed", "repaired"}:
                    raise ValueError("invalid_coordination_fault_transition")
                try:
                    _verify_expected_target(
                        self.path, observed_identity, observed_sha
                    )
                    self.path.unlink()
                except (FileNotFoundError, RuntimeError) as exc:
                    raise RuntimeError("coordination_fault_cas_conflict") from exc
                return True

    def _read_locked(self) -> tuple[dict[str, object] | None, str | None]:
        payload, observed_sha, _ = self._read_locked_with_identity()
        return payload, observed_sha

    def _read_locked_with_identity(
        self,
    ) -> tuple[
        dict[str, object] | None,
        str | None,
        tuple[int, int, int, int, int, int] | None,
    ]:
        try:
            raw, identity = _read_pinned_regular_file(self.path)
            payload = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return None, None, None
        except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_coordination_fault") from exc
        return self.validate(payload), _sha256(raw), identity

    def _write_locked(
        self, payload: dict[str, object], *, expected_sha256: str | None
    ) -> str:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        _atomic_write_text(self.path, text, expected_sha256=expected_sha256)
        return _sha256(text.encode("utf-8"))


_LIFECYCLE_FAULT_KEYS = frozenset(
    {
        "format_version",
        "fault_id",
        "scope",
        "reason",
        "manifest_sha256",
        "backup_receipt_sha256",
        "run_id",
        "launch_operation_id",
        "run_record_sha256",
        "armed_at_utc",
    }
)
_LIFECYCLE_EVENT_KEYS = frozenset(
    {
        "format_version",
        "fault_id",
        "sequence",
        "state",
        "previous_event_sha256",
        "event_at_utc",
        "expected_manifest_sha256",
        "evidence_sha256",
        "error_code",
    }
)
_LIFECYCLE_POINTER_KEYS = frozenset(
    {"format_version", "fault_id", "sequence", "head_event_sha256"}
)
_LIFECYCLE_ERROR_CODES = frozenset(
    {"manifest_drift", "identity_ambiguous", "cleanup_failed", "receipt_missing"}
)


class LifecycleRecoveryFaultStore:
    """Create-only lifecycle recovery evidence with one CAS active pointer."""

    def __init__(
        self,
        paths: RuntimePaths,
        *,
        fault_id_fn: Callable[[], str] | None = None,
        utc_now_fn: Callable[[], str] | None = None,
    ) -> None:
        self.paths = paths
        self._fault_id_fn = fault_id_fn or (lambda: str(uuid.uuid4()))
        self._utc_now_fn = utc_now_fn or _utc_now
        self._lock = threading.RLock()

    @staticmethod
    def _valid_hex64(value: object) -> bool:
        return bool(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdefABCDEF" for character in value)
        )

    @staticmethod
    def _valid_uuid4(value: object) -> bool:
        if not isinstance(value, str) or value != value.casefold():
            return False
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError):
            return False
        return parsed.version == 4 and str(parsed) == value

    @classmethod
    def validate_fault(cls, value: object) -> dict[str, object]:
        if not isinstance(value, dict) or set(value) != _LIFECYCLE_FAULT_KEYS:
            raise ValueError("invalid_lifecycle_recovery_fault")
        scope = value.get("scope")
        reason = value.get("reason")
        run_values = (
            value.get("run_id"),
            value.get("launch_operation_id"),
            value.get("run_record_sha256"),
        )
        if (
            value.get("format_version") != 1
            or not cls._valid_uuid4(value.get("fault_id"))
            or scope not in {"manifest", "run"}
            or reason
            not in {"manifest_corrupt", "identity_ambiguous", "cleanup_failed"}
            or not cls._valid_hex64(value.get("manifest_sha256"))
            or not cls._valid_hex64(value.get("backup_receipt_sha256"))
            or not isinstance(value.get("armed_at_utc"), str)
            or len(str(value.get("armed_at_utc"))) > 64
            or not _valid_utc_timestamp(str(value.get("armed_at_utc")))
        ):
            raise ValueError("invalid_lifecycle_recovery_fault")
        if scope == "manifest":
            if reason != "manifest_corrupt" or any(item is not None for item in run_values):
                raise ValueError("invalid_lifecycle_recovery_fault")
        elif (
            reason == "manifest_corrupt"
            or not isinstance(run_values[0], str)
            or not run_values[0]
            or not cls._valid_uuid4(run_values[1])
            or not cls._valid_hex64(run_values[2])
        ):
            raise ValueError("invalid_lifecycle_recovery_fault")
        return json.loads(json.dumps(value, ensure_ascii=False))

    @classmethod
    def validate_event(cls, value: object) -> dict[str, object]:
        if not isinstance(value, dict) or set(value) != _LIFECYCLE_EVENT_KEYS:
            raise ValueError("invalid_lifecycle_recovery_event")
        sequence = value.get("sequence")
        previous = value.get("previous_event_sha256")
        evidence = value.get("evidence_sha256")
        error = value.get("error_code")
        if (
            value.get("format_version") != 1
            or not cls._valid_uuid4(value.get("fault_id"))
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 0
            or value.get("state") not in {"armed", "repairing", "repaired"}
            or (sequence == 0) != (previous is None)
            or (previous is not None and not cls._valid_hex64(previous))
            or not isinstance(value.get("event_at_utc"), str)
            or len(str(value.get("event_at_utc"))) > 64
            or not _valid_utc_timestamp(str(value.get("event_at_utc")))
            or not cls._valid_hex64(value.get("expected_manifest_sha256"))
            or (evidence is not None and not cls._valid_hex64(evidence))
            or (error is not None and error not in _LIFECYCLE_ERROR_CODES)
        ):
            raise ValueError("invalid_lifecycle_recovery_event")
        return json.loads(json.dumps(value, ensure_ascii=False))

    @classmethod
    def validate_pointer(cls, value: object) -> dict[str, object]:
        if not isinstance(value, dict) or set(value) != _LIFECYCLE_POINTER_KEYS:
            raise ValueError("invalid_lifecycle_recovery_pointer")
        sequence = value.get("sequence")
        if (
            value.get("format_version") != 1
            or not cls._valid_uuid4(value.get("fault_id"))
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 0
            or not cls._valid_hex64(value.get("head_event_sha256"))
        ):
            raise ValueError("invalid_lifecycle_recovery_pointer")
        return json.loads(json.dumps(value, ensure_ascii=False))

    def arm(
        self,
        *,
        scope: str,
        reason: str,
        manifest_sha256: str,
        backup_receipt_sha256: str,
        run_id: str | None = None,
        launch_operation_id: str | None = None,
        run_record_sha256: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            current = self.load_active()
            expected_pointer_sha: str | None = None
            if current is not None:
                if current["event"].get("state") != "repaired":
                    raise RuntimeError("lifecycle_recovery_fault_active")
                expected_pointer_sha = str(current["pointer_sha256"])
            fault_id = self._fault_id_fn()
            now = self._utc_now_fn()
            fault = self.validate_fault(
                {
                    "format_version": 1,
                    "fault_id": fault_id,
                    "scope": scope,
                    "reason": reason,
                    "manifest_sha256": manifest_sha256,
                    "backup_receipt_sha256": backup_receipt_sha256,
                    "run_id": run_id,
                    "launch_operation_id": launch_operation_id,
                    "run_record_sha256": run_record_sha256,
                    "armed_at_utc": now,
                }
            )
            event = self.validate_event(
                {
                    "format_version": 1,
                    "fault_id": fault_id,
                    "sequence": 0,
                    "state": "armed",
                    "previous_event_sha256": None,
                    "event_at_utc": now,
                    "expected_manifest_sha256": manifest_sha256,
                    "evidence_sha256": None,
                    "error_code": None,
                }
            )
            fault_dir = self.paths.lifecycle_recovery_faults_dir / fault_id
            self._write_create_only_json(fault_dir / "fault.json", fault)
            event_path = fault_dir / "events" / "00000000.json"
            event_sha = self._write_create_only_json(event_path, event)
            pointer = self.validate_pointer(
                {
                    "format_version": 1,
                    "fault_id": fault_id,
                    "sequence": 0,
                    "head_event_sha256": event_sha,
                }
            )
            try:
                pointer_sha = self._write_pointer(
                    pointer, expected_sha256=expected_pointer_sha
                )
            except RuntimeError as exc:
                raise RuntimeError("lifecycle_recovery_cas_conflict") from exc
            return {
                "fault": fault,
                "event": event,
                "pointer": pointer,
                "pointer_sha256": pointer_sha,
            }

    def transition(
        self,
        fault_id: str,
        expected_head_sha256: str,
        *,
        state: str,
        expected_manifest_sha256: str,
        evidence_sha256: str | None = None,
        error_code: str | None = None,
    ) -> str:
        with self._lock:
            active = self.load_active()
            if (
                active is None
                or active["fault"].get("fault_id") != fault_id
                or active["pointer"].get("head_event_sha256")
                != expected_head_sha256
            ):
                raise RuntimeError("lifecycle_recovery_cas_conflict")
            previous_state = active["event"].get("state")
            if (previous_state, state) not in {
                ("armed", "repairing"),
                ("repairing", "armed"),
                ("repairing", "repaired"),
            }:
                raise ValueError("invalid_lifecycle_recovery_transition")
            if state == "repaired":
                if evidence_sha256 is None or not self._receipt_exists(
                    fault_id, evidence_sha256
                ):
                    raise ValueError("invalid_lifecycle_recovery_transition")
            elif evidence_sha256 is not None:
                raise ValueError("invalid_lifecycle_recovery_transition")
            sequence = int(active["pointer"]["sequence"]) + 1
            event = self.validate_event(
                {
                    "format_version": 1,
                    "fault_id": fault_id,
                    "sequence": sequence,
                    "state": state,
                    "previous_event_sha256": expected_head_sha256,
                    "event_at_utc": self._utc_now_fn(),
                    "expected_manifest_sha256": expected_manifest_sha256,
                    "evidence_sha256": evidence_sha256,
                    "error_code": error_code,
                }
            )
            event_path = (
                self.paths.lifecycle_recovery_faults_dir
                / fault_id
                / "events"
                / f"{sequence:08d}.json"
            )
            event_sha = self._write_create_only_json(event_path, event)
            pointer = self.validate_pointer(
                {
                    "format_version": 1,
                    "fault_id": fault_id,
                    "sequence": sequence,
                    "head_event_sha256": event_sha,
                }
            )
            try:
                self._write_pointer(
                    pointer, expected_sha256=str(active["pointer_sha256"])
                )
            except RuntimeError as exc:
                raise RuntimeError("lifecycle_recovery_cas_conflict") from exc
            return event_sha

    def create_receipt(
        self, fault_id: str, payload: dict[str, object]
    ) -> str:
        if not self._valid_uuid4(fault_id) or not isinstance(payload, dict):
            raise ValueError("invalid_lifecycle_recovery_receipt")
        text = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) + "\n"
        receipt_sha = _sha256(text.encode("utf-8"))
        path = (
            self.paths.lifecycle_recovery_faults_dir
            / fault_id
            / "receipts"
            / f"{receipt_sha}.json"
        )
        with self._lock:
            self._write_create_only_text(path, text)
        return receipt_sha

    def create_manifest_backup(self, raw: bytes) -> str:
        if not isinstance(raw, bytes):
            raise ValueError("invalid_lifecycle_manifest_backup")
        manifest_sha = _sha256(raw)
        receipt = {
            "format_version": 1,
            "manifest_sha256": manifest_sha,
            "backup_sha256": manifest_sha,
            "byte_length": len(raw),
        }
        receipt_text = json.dumps(
            receipt, ensure_ascii=False, separators=(",", ":")
        ) + "\n"
        receipt_sha = _sha256(receipt_text.encode("utf-8"))
        directory = (
            self.paths.lifecycle_recovery_faults_dir
            / "backups"
            / receipt_sha
        )
        with self._lock:
            try:
                self._write_create_only_bytes(directory / "manifest.bin", raw)
            except FileExistsError:
                try:
                    existing, _ = _read_pinned_regular_file(
                        directory / "manifest.bin"
                    )
                except (OSError, RuntimeError) as exc:
                    raise ValueError("invalid_lifecycle_manifest_backup") from exc
                if existing != raw:
                    raise ValueError("invalid_lifecycle_manifest_backup")
            try:
                self._write_create_only_text(directory / "receipt.json", receipt_text)
            except FileExistsError:
                try:
                    existing, _ = _read_pinned_regular_file(
                        directory / "receipt.json"
                    )
                except (OSError, RuntimeError) as exc:
                    raise ValueError("invalid_lifecycle_manifest_backup") from exc
                if existing != receipt_text.encode("utf-8"):
                    raise ValueError("invalid_lifecycle_manifest_backup")
        return receipt_sha

    def checkpoint_manifest(self, raw: bytes) -> str:
        receipt_sha = self.create_manifest_backup(raw)
        payload = {
            "format_version": 1,
            "manifest_sha256": _sha256(raw),
            "backup_receipt_sha256": receipt_sha,
        }
        atomic_write_json(self.paths.lifecycle_manifest_checkpoint_path, payload)
        return receipt_sha

    def load_manifest_checkpoint(self) -> tuple[bytes, str] | None:
        try:
            raw, _ = _read_pinned_regular_file(
                self.paths.lifecycle_manifest_checkpoint_path
            )
            payload = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_lifecycle_manifest_checkpoint") from exc
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {"format_version", "manifest_sha256", "backup_receipt_sha256"}
            or payload.get("format_version") != 1
            or not self._valid_hex64(payload.get("manifest_sha256"))
            or not self._valid_hex64(payload.get("backup_receipt_sha256"))
        ):
            raise ValueError("invalid_lifecycle_manifest_checkpoint")
        backup = self.read_manifest_backup(payload["backup_receipt_sha256"])
        if _sha256(backup) != payload["manifest_sha256"]:
            raise ValueError("invalid_lifecycle_manifest_checkpoint")
        return backup, payload["backup_receipt_sha256"]

    def read_manifest_backup(self, receipt_sha: str) -> bytes:
        if not self._valid_hex64(receipt_sha):
            raise ValueError("invalid_lifecycle_manifest_backup")
        directory = (
            self.paths.lifecycle_recovery_faults_dir
            / "backups"
            / receipt_sha
        )
        try:
            receipt_raw, _ = _read_pinned_regular_file(directory / "receipt.json")
            manifest_raw, _ = _read_pinned_regular_file(directory / "manifest.bin")
            receipt = json.loads(receipt_raw.decode("utf-8"))
        except (
            FileNotFoundError,
            OSError,
            RuntimeError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("invalid_lifecycle_manifest_backup") from exc
        if (
            _sha256(receipt_raw) != receipt_sha
            or not isinstance(receipt, dict)
            or set(receipt)
            != {"format_version", "manifest_sha256", "backup_sha256", "byte_length"}
            or receipt.get("format_version") != 1
            or receipt.get("manifest_sha256") != _sha256(manifest_raw)
            or receipt.get("backup_sha256") != _sha256(manifest_raw)
            or receipt.get("byte_length") != len(manifest_raw)
        ):
            raise ValueError("invalid_lifecycle_manifest_backup")
        return manifest_raw

    def load_active(self) -> dict[str, object] | None:
        pointer_path = self.paths.lifecycle_recovery_active_path
        try:
            pointer_raw, _ = _read_pinned_regular_file(pointer_path)
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError) as exc:
            raise ValueError("invalid_lifecycle_recovery_pointer") from exc
        try:
            pointer = self.validate_pointer(
                json.loads(pointer_raw.decode("utf-8"))
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_lifecycle_recovery_pointer") from exc
        pointer_sha = _sha256(pointer_raw)
        fault_id = str(pointer["fault_id"])
        fault_dir = self.paths.lifecycle_recovery_faults_dir / fault_id
        fault = self._read_json(
            fault_dir / "fault.json", self.validate_fault, "invalid_lifecycle_recovery_fault"
        )
        previous_sha: str | None = None
        head_event: dict[str, object] | None = None
        for sequence in range(int(pointer["sequence"]) + 1):
            path = fault_dir / "events" / f"{sequence:08d}.json"
            raw, event = self._read_json_with_raw(
                path, self.validate_event, "invalid_lifecycle_recovery_event"
            )
            event_sha = _sha256(raw)
            if (
                event.get("fault_id") != fault_id
                or event.get("sequence") != sequence
                or event.get("previous_event_sha256") != previous_sha
            ):
                raise ValueError("invalid_lifecycle_recovery_chain")
            previous_sha = event_sha
            head_event = event
        if previous_sha != pointer["head_event_sha256"] or head_event is None:
            raise ValueError("invalid_lifecycle_recovery_chain")
        return {
            "fault": fault,
            "event": head_event,
            "pointer": pointer,
            "pointer_sha256": pointer_sha,
        }

    def _receipt_exists(self, fault_id: str, receipt_sha: str) -> bool:
        if not self._valid_hex64(receipt_sha):
            return False
        path = (
            self.paths.lifecycle_recovery_faults_dir
            / fault_id
            / "receipts"
            / f"{receipt_sha}.json"
        )
        try:
            raw, _ = _read_pinned_regular_file(path)
        except (FileNotFoundError, OSError, RuntimeError):
            return False
        return _sha256(raw) == receipt_sha

    def _write_pointer(
        self, payload: dict[str, object], *, expected_sha256: str | None
    ) -> str:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        _atomic_write_text(
            self.paths.lifecycle_recovery_active_path,
            text,
            expected_sha256=expected_sha256,
        )
        return _sha256(text.encode("utf-8"))

    @staticmethod
    def _write_create_only_text(path: Path, text: str) -> None:
        LifecycleRecoveryFaultStore._write_create_only_bytes(
            path, text.encode("utf-8")
        )

    @staticmethod
    def _write_create_only_bytes(path: Path, raw: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _safe_directory(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise OSError("lifecycle_recovery_write_failed")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_create_only_json(
        self, path: Path, payload: dict[str, object]
    ) -> str:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._write_create_only_text(path, text)
        return _sha256(text.encode("utf-8"))

    @staticmethod
    def _read_json_with_raw(
        path: Path,
        validator: Callable[[object], dict[str, object]],
        error: str,
    ) -> tuple[bytes, dict[str, object]]:
        try:
            raw, _ = _read_pinned_regular_file(path)
            payload = json.loads(raw.decode("utf-8"))
            return raw, validator(payload)
        except (FileNotFoundError, OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(error) from exc

    @classmethod
    def _read_json(
        cls,
        path: Path,
        validator: Callable[[object], dict[str, object]],
        error: str,
    ) -> dict[str, object]:
        return cls._read_json_with_raw(path, validator, error)[1]


class CoordinationSnapshotStore:
    def __init__(self, paths: RuntimePaths, daemon_generation: str) -> None:
        self._daemon_generation = _require_generation(daemon_generation)
        self.coordination_path = paths.coordination_path
        self._lock = threading.Lock()
        self._last_revision: int | None = None

    def write_coordination(self, payload: dict[str, object]) -> bool:
        if not isinstance(payload, dict):
            raise TypeError("coordination_payload_must_be_dict")
        persisted = _coordination_payload(payload, self._daemon_generation)
        with self._lock:
            return self._write_coordination_locked(persisted)

    def persisted_revision(self) -> int | None:
        with self._lock:
            return self._last_revision

    def ensure_coordination(self, payload: dict[str, object]) -> bool:
        """Persist a WAL snapshot or accept an equal/newer durable snapshot."""

        if not isinstance(payload, dict):
            raise TypeError("coordination_payload_must_be_dict")
        persisted = _coordination_payload(payload, self._daemon_generation)
        revision = _coordination_revision(persisted)
        with self._lock:
            if self._last_revision is None:
                self._last_revision = self._disk_revision_locked()
            if revision <= self._last_revision:
                return True
            return self._write_coordination_locked(persisted)

    def consume_previous_generation(
        self,
        generation: str,
        *,
        previous_snapshot: object = _UNSET,
    ) -> dict[str, object]:
        current_generation = _require_generation(generation)
        if current_generation != self._daemon_generation:
            raise ValueError("daemon_generation_mismatch")

        with self._lock:
            if previous_snapshot is _UNSET:
                try:
                    raw, _ = _read_pinned_regular_file(self.coordination_path)
                except FileNotFoundError:
                    previous = None
                else:
                    previous = json.loads(raw.decode("utf-8"))
            else:
                previous = previous_snapshot

            if previous is None:
                initial = _coordination_payload(
                    {"revision": 0, "active": None, "queue": []},
                    self._daemon_generation,
                )
                self._last_revision = -1
                self._write_coordination_locked(initial)
                return {}

            if not isinstance(previous, dict):
                raise ValueError("invalid_coordination_snapshot")
            previous_generation = previous.get("daemon_generation")
            previous_revision = _coordination_revision(previous)
            if previous_generation == current_generation:
                self._last_revision = previous_revision
                return {}

            clients, lease_ids, ticket_ids = _restart_attribution(previous)
            cleared = _coordination_payload(
                {
                    "revision": 0,
                    "active": None,
                    "releasing": None,
                    "queue": [],
                    "previous_generation": previous_generation,
                },
                self._daemon_generation,
            )
            self._last_revision = -1
            self._write_coordination_locked(cleared)
            return {
                "event": "daemon_restart_invalidated",
                "previous_generation": previous_generation,
                "previous_revision": previous_revision,
                "daemon_generation": current_generation,
                "clients": clients,
                "lease_ids": lease_ids,
                "ticket_ids": ticket_ids,
                "decision": "invalidated",
                "reason": "daemon_restart",
                "duration_s": 0.0,
            }

    def _write_coordination_locked(self, persisted: dict[str, object]) -> bool:
        revision = _coordination_revision(persisted)
        if self._last_revision is None:
            self._last_revision = self._disk_revision_locked()
        if revision <= self._last_revision:
            return False
        text = json.dumps(persisted, ensure_ascii=False, separators=(",", ":")) + "\n"
        _atomic_write_text(self.coordination_path, text)
        self._last_revision = revision
        return True

    def _disk_revision_locked(self) -> int:
        try:
            raw, _ = _read_pinned_regular_file(self.coordination_path)
        except FileNotFoundError:
            return -1
        current = json.loads(raw.decode("utf-8"))
        if not isinstance(current, dict):
            raise ValueError("invalid_coordination_snapshot")
        if current.get("daemon_generation") != self._daemon_generation:
            return -1
        return _coordination_revision(current)


def _redact(value: object) -> object:
    if isinstance(value, dict):
        result: dict[object, object] = {}
        for key, item in value.items():
            normalized = key.casefold() if isinstance(key, str) else key
            if normalized in SECRET_KEYS or normalized in _NON_PERSISTED_KEYS:
                result[key] = _REDACTED
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _coordination_payload(
    payload: dict[str, object], daemon_generation: str
) -> dict[str, object]:
    persisted: dict[str, object] = {
        "daemon_generation": daemon_generation,
        "revision": _coordination_revision(payload),
    }
    for key in ("previous_generation", "captured_at_monotonic"):
        value = payload.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            persisted[key] = value
    persisted["active"] = _public_lease(payload.get("active"))
    persisted["releasing"] = _public_lease(payload.get("releasing"))
    persisted["granting"] = _public_lease(payload.get("granting"))
    persisted["handoff_pending"] = payload.get("handoff_pending") is True
    audit_fault = payload.get("audit_fault")
    persisted["audit_fault"] = (
        None
        if audit_fault is None
        else CoordinationFaultStore.validate(audit_fault)
    )
    queue = payload.get("queue")
    persisted["queue"] = (
        [_public_ticket(item) for item in queue if isinstance(item, dict)]
        if isinstance(queue, list)
        else []
    )
    return persisted


def _coordination_revision(payload: dict[str, object]) -> int:
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("invalid_coordination_revision")
    return revision


def _restart_attribution(
    snapshot: dict[str, object],
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    clients: list[dict[str, str]] = []
    lease_ids: list[str] = []
    ticket_ids: list[str] = []
    seen_clients: set[str] = set()

    def add_client(value: object) -> None:
        if isinstance(value, dict):
            session = value.get("session")
            if isinstance(session, str) and session not in seen_clients:
                seen_clients.add(session)
                clients.append({"session": session})

    for state_name in ("active", "releasing", "granting"):
        state = snapshot.get(state_name)
        if not isinstance(state, dict):
            continue
        add_client(state)
        lease_id = state.get("lease_id")
        if isinstance(lease_id, str) and lease_id not in lease_ids:
            lease_ids.append(lease_id)
        ticket_id = state.get("ticket")
        if isinstance(ticket_id, str) and ticket_id not in ticket_ids:
            ticket_ids.append(ticket_id)

    queue = snapshot.get("queue")
    if isinstance(queue, list):
        for ticket in queue:
            if not isinstance(ticket, dict):
                continue
            add_client(ticket)
            ticket_id = ticket.get("ticket")
            if isinstance(ticket_id, str) and ticket_id not in ticket_ids:
                ticket_ids.append(ticket_id)
    return clients, lease_ids, ticket_ids


def _public_lease(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return _copy_public_fields(
        value,
        (
            "lease_id",
            "session",
            "granted_at_monotonic",
            "expires_at_monotonic",
            "ticket",
        ),
    )


def _public_ticket(value: dict[object, object]) -> dict[str, object]:
    return _copy_public_fields(
        value,
        (
            "ticket",
            "session",
            "created_at_monotonic",
            "touched_at_monotonic",
        ),
    )


def _copy_public_fields(
    value: dict[object, object], fields: tuple[str, ...]
) -> dict[str, object]:
    result: dict[str, object] = {}
    for field in fields:
        item = value.get(field)
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            result[field] = item
    return result


def recover_coordination_fault(
    paths: RuntimePaths,
    store: CoordinationFaultStore,
    daemon_generation: str,
    *,
    utc_now_fn: Callable[[], str] | None = None,
) -> dict[str, object] | None:
    """Compatibility wrapper for callers that only need the recovered fault."""

    return recover_coordination_startup(
        paths,
        store,
        daemon_generation,
        utc_now_fn=utc_now_fn,
    ).audit_fault


def recover_coordination_startup(
    paths: RuntimePaths,
    store: CoordinationFaultStore,
    daemon_generation: str,
    *,
    utc_now_fn: Callable[[], str] | None = None,
) -> CoordinationStartupRecovery:
    """Load and classify startup evidence once, preserving corrupt snapshots."""

    generation = _require_generation(daemon_generation)
    now = (utc_now_fn or _utc_now)()
    evidence = _load_startup_snapshot_once(paths.coordination_path)
    snapshot = evidence.snapshot
    snapshot_status = evidence.status
    quarantined_path: Path | None = None

    def recovered(
        fault: dict[str, object] | None,
        *,
        fault_materialized: bool = True,
    ) -> CoordinationStartupRecovery:
        return CoordinationStartupRecovery(
            audit_fault=fault,
            snapshot=snapshot,
            snapshot_status=snapshot_status,
            quarantined_snapshot_path=quarantined_path,
            fault_materialized=fault_materialized,
        )

    def quarantine_invalid_snapshot() -> bool:
        nonlocal snapshot_status, quarantined_path
        if evidence.status != "invalid":
            return evidence.status in {"missing", "valid"}
        if evidence.raw is None or evidence.identity is None:
            snapshot_status = "unreadable"
            return False
        try:
            quarantined_path = _quarantine_coordination_snapshot(
                paths.coordination_path,
                evidence.raw,
                evidence.identity,
            )
        except (OSError, RuntimeError):
            quarantined_path = None
        if quarantined_path is None:
            snapshot_status = "quarantine_failed"
            return False
        snapshot_status = "quarantined"
        return True

    try:
        marker, marker_sha = store.load_with_sha()
    except ValueError:
        return recovered(
            _synthetic_fault(
                generation,
                now,
                failure="fault_store_unreadable",
                expected_revision=_snapshot_revision_or_zero(snapshot),
            ),
            fault_materialized=False,
        )

    if marker is None:
        if snapshot_status not in {"missing", "valid"}:
            fault = _synthetic_fault(
                generation,
                now,
                failure="snapshot_failed",
                expected_revision=0,
            )
            persisted, materialized = _materialize_startup_fault(store, fault)
            if materialized:
                quarantine_invalid_snapshot()
            return recovered(persisted, fault_materialized=materialized)
        snapshot_fault = snapshot.get("audit_fault") if snapshot is not None else None
        if snapshot_fault is None:
            return recovered(None)
        if not isinstance(snapshot_fault, dict):
            fault = _synthetic_fault(
                generation,
                now,
                failure="fault_marker_missing",
                expected_revision=_snapshot_revision_or_zero(snapshot),
            )
        else:
            fault = dict(snapshot_fault)
            fault["state"] = "fault"
            fault["phase"] = "audit_failed"
            fault["failure"] = "fault_marker_missing"
        persisted, materialized = _materialize_startup_fault(store, fault)
        return recovered(persisted, fault_materialized=materialized)

    assert marker_sha is not None
    quarantine_invalid_snapshot()
    state = marker["state"]
    if state == "armed":
        try:
            store.transition(
                str(marker["fault_id"]),
                marker_sha,
                state="fault",
                phase="audit_failed",
                failure="audit_failed",
            )
            return recovered(store.load())
        except (RuntimeError, ValueError, OSError):
            fault = dict(marker)
            fault["state"] = "fault"
            fault["phase"] = "terminal_transition_failed"
            fault["failure"] = "terminal_transition_failed"
            return recovered(fault)
    if state in {"fault", "repairing"}:
        return recovered(marker)
    if state == "completed":
        if _completed_postcondition_matches(snapshot, marker):
            try:
                store.clear(str(marker["fault_id"]), marker_sha)
                return recovered(None)
            except (RuntimeError, ValueError, OSError):
                return recovered(marker)
        failure = "wal_completion_mismatch"
    else:
        if _repaired_postcondition_matches(snapshot, marker):
            try:
                store.clear(str(marker["fault_id"]), marker_sha)
                return recovered(None)
            except (RuntimeError, ValueError, OSError):
                return recovered(marker)
        failure = "repair_failed"
    try:
        store.transition(
            str(marker["fault_id"]),
            marker_sha,
            state="fault",
            phase="snapshot_failed",
            failure=failure,
        )
        return recovered(store.load())
    except (RuntimeError, ValueError, OSError):
        fault = dict(marker)
        fault["state"] = "fault"
        fault["phase"] = "terminal_transition_failed"
        fault["failure"] = "terminal_transition_failed"
        return recovered(fault)


def _load_startup_snapshot_once(
    path: Path,
) -> _StartupSnapshotEvidence:
    try:
        loaded = _read_pinned_regular_file(path)
    except FileNotFoundError:
        return _StartupSnapshotEvidence(None, "missing")
    except RuntimeError as exc:
        status = str(exc)
        if status not in {"unsafe_runtime_path", "runtime_path_changed"}:
            status = "runtime_path_unreadable"
        return _StartupSnapshotEvidence(None, status)
    except OSError:
        return _StartupSnapshotEvidence(None, "runtime_path_unreadable")
    raw, identity = loaded
    try:
        candidate = json.loads(raw.decode("utf-8"))
        if not isinstance(candidate, dict):
            raise ValueError("invalid_coordination_snapshot")
        _coordination_revision(candidate)
        audit_fault = candidate.get("audit_fault")
        if audit_fault is not None:
            candidate["audit_fault"] = CoordinationFaultStore.validate(audit_fault)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return _StartupSnapshotEvidence(None, "invalid", raw, identity)
    return _StartupSnapshotEvidence(candidate, "valid", raw, identity)


def _quarantine_coordination_snapshot(
    path: Path,
    raw: bytes,
    identity: tuple[int, int, int, int, int, int],
) -> Path | None:
    try:
        current = _safe_regular_lstat(path)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    if _stat_identity(current) != identity:
        return None
    digest = _sha256(raw)
    base = path.with_name(f"{path.name}.corrupt.{digest}")
    candidate = base
    suffix = 0
    while _path_lexists(candidate):
        suffix += 1
        candidate = base.with_name(f"{base.name}.{suffix}")
        if suffix > 1000:
            return None
    try:
        os.replace(path, candidate)
    except OSError:
        return None
    try:
        quarantined_raw, quarantined_identity = _read_pinned_regular_file(candidate)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    if quarantined_identity != identity or quarantined_raw != raw:
        return None
    return candidate


def _materialize_startup_fault(
    store: CoordinationFaultStore, fault: dict[str, object]
) -> tuple[dict[str, object], bool]:
    try:
        store.materialize_fault(fault)
        persisted = store.load()
        if persisted == fault:
            return persisted, True
    except (OSError, RuntimeError, ValueError):
        pass
    return fault, False


def _completed_postcondition_matches(
    snapshot: dict[str, object] | None, marker: dict[str, object]
) -> bool:
    if (
        marker.get("state") != "completed"
        or not _valid_coordination_fault_semantics(marker)
        or snapshot is None
        or snapshot.get("revision") != marker.get("expected_snapshot_revision")
    ):
        return False
    if marker.get("operation") == "grant":
        if marker.get("failure") == "coordination_changed":
            return all(
                snapshot.get(field) is None
                for field in ("active", "releasing", "granting")
            )
        active = snapshot.get("active")
        return isinstance(active, dict) and active.get("lease_id") == marker.get(
            "lease_id"
        )
    return all(snapshot.get(field) is None for field in ("active", "releasing", "granting"))


def _repaired_postcondition_matches(
    snapshot: dict[str, object] | None, marker: dict[str, object]
) -> bool:
    return (
        marker.get("state") == "repaired"
        and _valid_coordination_fault_semantics(marker)
        and snapshot is not None
        and snapshot.get("revision") == marker.get("expected_snapshot_revision")
        and snapshot.get("audit_fault") is None
    )


def _snapshot_revision_or_zero(snapshot: dict[str, object] | None) -> int:
    if snapshot is None:
        return 0
    revision = snapshot.get("revision")
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else 0


def _synthetic_fault(
    generation: str,
    armed_at_utc: str,
    *,
    failure: str,
    expected_revision: int,
) -> dict[str, object]:
    return {
        "format_version": 1,
        "fault_id": f"synthetic-{failure}-{generation}"[:200],
        "daemon_generation": generation,
        "state": "fault",
        "operation": "grant",
        "phase": "snapshot_failed" if failure == "snapshot_failed" else "audit_failed",
        "lease_id": "unknown",
        "ticket_id": None,
        "client": {
            "platform": "unknown",
            "session": "daemon",
            "started_at_utc": armed_at_utc,
            "task_label": "startup recovery",
        },
        "reason": "startup_recovery",
        "armed_at_utc": armed_at_utc,
        "failure": failure,
        "expected_snapshot_revision": expected_revision,
        "repair_phase": "none",
    }


def _bounded_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and all(ord(character) >= 32 for character in value)
    )


def _valid_utc_timestamp(value: str) -> bool:
    if not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _path_lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _is_reparse(value: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(value, "st_file_attributes", 0) & reparse_flag)


def _safe_regular_stat(value: os.stat_result) -> bool:
    return stat.S_ISREG(value.st_mode) and not _is_reparse(value)


def _safe_regular_lstat(path: Path) -> os.stat_result:
    value = path.lstat()
    if not _safe_regular_stat(value):
        raise RuntimeError("unsafe_runtime_path")
    return value


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    # Windows can synthesize permission bits and creation-time metadata differently
    # for lstat() and fstat() over the same handle.  File ID + content metadata are
    # stable across both views and still detect replacement or in-place mutation.
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        0,
        0,
    )


def _read_pinned_regular_file(
    path: Path,
) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    before = _safe_regular_lstat(path)
    expected_identity = _stat_identity(before)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise RuntimeError("runtime_path_changed") from exc
    try:
        opened = os.fstat(descriptor)
        if not _safe_regular_stat(opened):
            raise RuntimeError("unsafe_runtime_path")
        if _stat_identity(opened) != expected_identity:
            raise RuntimeError("runtime_path_changed")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        try:
            current = _safe_regular_lstat(path)
        except FileNotFoundError as exc:
            raise RuntimeError("runtime_path_changed") from exc
        if (
            _stat_identity(after) != expected_identity
            or _stat_identity(current) != expected_identity
        ):
            raise RuntimeError("runtime_path_changed")
        return b"".join(chunks), expected_identity
    finally:
        os.close(descriptor)


def _safe_directory(path: Path) -> None:
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode) or _is_reparse(value):
        raise RuntimeError("unsafe_runtime_path")


def _target_identity(path: Path) -> tuple[int, int, int, int, int, int] | None:
    try:
        return _stat_identity(_safe_regular_lstat(path))
    except FileNotFoundError:
        return None


def _observe_expected_target(
    path: Path, expected_sha256: object
) -> tuple[int, int, int, int, int, int] | None:
    if expected_sha256 is _UNSET:
        return _target_identity(path)
    if expected_sha256 is None:
        if _target_identity(path) is not None:
            raise RuntimeError("atomic_target_changed")
        return None
    if not isinstance(expected_sha256, str):
        raise TypeError("invalid_expected_sha256")
    try:
        raw, identity = _read_pinned_regular_file(path)
    except FileNotFoundError as exc:
        raise RuntimeError("atomic_target_changed") from exc
    if _sha256(raw) != expected_sha256:
        raise RuntimeError("atomic_target_changed")
    return identity


def _verify_expected_target(
    path: Path,
    expected_identity: tuple[int, int, int, int, int, int] | None,
    expected_sha256: object,
) -> None:
    if _observe_expected_target(path, expected_sha256) != expected_identity:
        raise RuntimeError("atomic_target_changed")


def _unlink_if_same(
    path: Path, expected_identity: tuple[int, int, int, int, int, int] | None
) -> None:
    if expected_identity is None:
        return
    try:
        current = _safe_regular_lstat(path)
        current_identity = _stat_identity(current)
        if current_identity == expected_identity:
            path.unlink()
    except (FileNotFoundError, OSError, RuntimeError):
        pass


def _atomic_write_text(
    path: Path, text: str, *, expected_sha256: object = _UNSET
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_directory(path.parent)
    original_identity = _observe_expected_target(path, expected_sha256)
    encoded = text.encode("utf-8")
    descriptor: int | None = None
    temporary: Path | None = None
    temporary_identity: tuple[int, int, int, int, int, int] | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        for _ in range(16):
            candidate = path.with_name(f"{path.name}.tmp.{secrets.token_hex(12)}")
            try:
                descriptor = os.open(candidate, flags, 0o600)
            except FileExistsError:
                continue
            temporary = candidate
            break
        if descriptor is None or temporary is None:
            raise RuntimeError("atomic_temporary_unavailable")
        opened = os.fstat(descriptor)
        if not _safe_regular_stat(opened):
            raise RuntimeError("unsafe_runtime_path")
        temporary_identity = _stat_identity(opened)
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("atomic_write_failed")
            offset += written
        os.fsync(descriptor)
        persisted = os.fstat(descriptor)
        if not _safe_regular_stat(persisted) or persisted.st_size != len(encoded):
            raise RuntimeError("atomic_temporary_changed")
        temporary_identity = _stat_identity(persisted)
        os.close(descriptor)
        descriptor = None
        if _target_identity(temporary) != temporary_identity:
            raise RuntimeError("atomic_temporary_changed")
        _verify_expected_target(path, original_identity, expected_sha256)
        os.replace(temporary, path)
        temporary = None
        if _target_identity(path) != temporary_identity:
            raise RuntimeError("atomic_replace_identity_mismatch")
    except Exception:
        if descriptor is not None:
            try:
                opened = os.fstat(descriptor)
                if _safe_regular_stat(opened):
                    temporary_identity = _stat_identity(opened)
            finally:
                os.close(descriptor)
        if temporary is not None:
            _unlink_if_same(temporary, temporary_identity)
        raise


def atomic_write_json(path: Path, payload: object) -> None:
    """Persist one JSON document with the runtime store's atomic replace contract."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    _atomic_write_text(path, text)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace one UTF-8 runtime document without changing its bytes."""
    if not isinstance(payload, bytes):
        raise TypeError("runtime_payload_must_be_bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("invalid_runtime_utf8") from exc
    if text.encode("utf-8") != payload:
        raise ValueError("invalid_runtime_utf8")
    _atomic_write_text(path, text)


def _require_generation(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_daemon_generation")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
