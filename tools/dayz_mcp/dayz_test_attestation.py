"""Opt-in project attestation of deployed PBO entries and fresh init evidence.

Disabled when project policy has no ``attestation`` member. A passing report
proves only the declared entries and post-boundary lines. It does not prove
feature acceptance. Plain PBOs only: compressed or encoded entries are
unverifiable, never "absent".
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
from dataclasses import dataclass

import ntpath


_MAX_REQUIREMENTS = 8
_MAX_ENTRIES = 32
_MAX_ID = 64
_MAX_PATTERN = 200
_MAX_PBO_BYTES = 8_000_000
_MAX_SCAN_BYTES = 262_144
_MAX_TIMEOUT_S = 300.0
_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
_ROLE = re.compile(r"[a-z0-9_]{1,32}")
_RECORD = struct.Struct("<5I")
_TRAILER_SIZE = 21
_MIME_PROPERTIES = 0x56657273
_APPLICABLE_ROLES = frozenset({"server", "client", "offline"})
_ARTIFACT_KEYS = frozenset({"entries", "id", "pbo"})
_INIT_SCRIPT_KEYS = frozenset({"id", "pattern", "role", "source"})
_INIT_FILE_KEYS = frozenset({"filename", "id", "pattern", "role", "source"})
_REPORT_STATUSES = frozenset({"passed", "failed", "unverifiable", "pending"})


class AttestationPolicyError(ValueError):
    """The policy member is not the closed attestation document."""


@dataclass(frozen=True, slots=True)
class ArtifactRequirement:
    id: str
    pbo: str
    entries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InitializationRequirement:
    id: str
    role: str
    pattern: str
    source: str
    filename: str | None


@dataclass(frozen=True, slots=True)
class ProjectAttestation:
    version: int
    artifacts: tuple[ArtifactRequirement, ...]
    initialization: tuple[InitializationRequirement, ...]
    timeout_s: float


def _relative_path(value: object, *, suffix: str | None = None) -> str:
    if type(value) is not str or not value or len(value) > 240:
        raise AttestationPolicyError("attestation_invalid")
    if ntpath.isabs(value) or ":" in value or value.startswith(("/", "\\")):
        raise AttestationPolicyError("attestation_invalid")
    parts = value.replace("/", "\\").split("\\")
    if any(part in {"", ".", ".."} for part in parts):
        raise AttestationPolicyError("attestation_invalid")
    if suffix is not None and not value.casefold().endswith(suffix):
        raise AttestationPolicyError("attestation_invalid")
    return "\\".join(parts)


def parse_attestation(value: object) -> ProjectAttestation:
    """Closed validation. Unknown keys, duplicates and traversal fail."""
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise AttestationPolicyError("attestation_invalid")
    if (
        set(value) - {"timeout_s"} != {"artifacts", "initialization", "version"}
        or type(value.get("version")) is not int
        or value.get("version") != 1
    ):
        raise AttestationPolicyError("attestation_invalid")
    timeout = value.get("timeout_s", 60)
    if (
        type(timeout) not in {int, float}
        or type(timeout) is bool
        or timeout != timeout
        or timeout in {float("inf"), float("-inf")}
        or not 0 < float(timeout) <= _MAX_TIMEOUT_S
    ):
        raise AttestationPolicyError("attestation_invalid")
    artifacts_raw = value.get("artifacts")
    init_raw = value.get("initialization")
    if (
        type(artifacts_raw) is not list
        or type(init_raw) is not list
        or len(artifacts_raw) > _MAX_REQUIREMENTS
        or len(init_raw) > _MAX_REQUIREMENTS
    ):
        raise AttestationPolicyError("attestation_invalid")
    artifacts: list[ArtifactRequirement] = []
    seen: set[str] = set()
    for item in artifacts_raw:
        if type(item) is not dict or set(item) != _ARTIFACT_KEYS:
            raise AttestationPolicyError("attestation_invalid")
        ident = item.get("id")
        if type(ident) is not str or _ID.fullmatch(ident) is None or ident in seen:
            raise AttestationPolicyError("attestation_invalid")
        seen.add(ident)
        entries = item.get("entries")
        if (
            type(entries) is not list
            or not 1 <= len(entries) <= _MAX_ENTRIES
            or any(type(entry) is not str for entry in entries)
            or len(set(entries)) != len(entries)
        ):
            raise AttestationPolicyError("attestation_invalid")
        names = tuple(_relative_path(entry) for entry in entries)
        artifacts.append(
            ArtifactRequirement(
                id=ident,
                pbo=_relative_path(item.get("pbo"), suffix=".pbo"),
                entries=names,
            )
        )
    initialization: list[InitializationRequirement] = []
    seen.clear()
    for item in init_raw:
        if type(item) is not dict or set(item) not in {
            _INIT_SCRIPT_KEYS,
            _INIT_FILE_KEYS,
        }:
            raise AttestationPolicyError("attestation_invalid")
        ident = item.get("id")
        role = item.get("role")
        pattern = item.get("pattern")
        source = item.get("source")
        if (
            type(ident) is not str
            or _ID.fullmatch(ident) is None
            or ident in seen
            or type(role) is not str
            or _ROLE.fullmatch(role) is None
            or type(pattern) is not str
            or not 1 <= len(pattern) <= _MAX_PATTERN
            or type(source) is not str
            or source not in {"script_log", "profile_file"}
        ):
            raise AttestationPolicyError("attestation_invalid")
        filename = None
        if source == "profile_file":
            if "filename" not in item:
                raise AttestationPolicyError("attestation_invalid")
            filename = _relative_path(item.get("filename"))
        elif "filename" in item:
            raise AttestationPolicyError("attestation_invalid")
        seen.add(ident)
        initialization.append(
            InitializationRequirement(
                id=ident,
                role=role,
                pattern=pattern,
                source=source,
                filename=filename,
            )
        )
    return ProjectAttestation(
        version=1,
        artifacts=tuple(artifacts),
        initialization=tuple(initialization),
        timeout_s=float(timeout),
    )


def roles_for_mode(mode: object) -> frozenset[str]:
    if mode == "server":
        return frozenset({"server"})
    if mode == "client":
        return frozenset({"client"})
    if mode == "offline":
        return frozenset({"offline"})
    if mode == "all":
        return frozenset({"server", "client"})
    return frozenset()


def _read_cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0 or end - pos > 260:
        raise ValueError("unverifiable")
    return data[pos:end].decode("utf-8"), end + 1


def plain_pbo_entry_names(data: bytes) -> set[str]:
    """Names of plain entries. Compressed or truncated input is unverifiable."""
    if type(data) is not bytes or not data or len(data) > _MAX_PBO_BYTES:
        raise ValueError("unverifiable")
    pos = 0
    names: list[str] = []
    sizes: list[int] = []
    first = True
    while True:
        if pos >= len(data):
            raise ValueError("unverifiable")
        name, pos = _read_cstr(data, pos)
        if pos + _RECORD.size > len(data):
            raise ValueError("unverifiable")
        mime, original, _reserved, _stamp, size = _RECORD.unpack_from(data, pos)
        pos += _RECORD.size
        if name == "" and first and mime == _MIME_PROPERTIES:
            while True:
                key, pos = _read_cstr(data, pos)
                if key == "":
                    break
                _value, pos = _read_cstr(data, pos)
            first = False
            continue
        first = False
        if name == "":
            break
        if mime != 0 or original not in (0, size) or size < 0:
            raise ValueError("unverifiable")
        names.append(name.replace("/", "\\"))
        sizes.append(size)
    data_end = pos + sum(sizes)
    if data_end > len(data) or len(data) - data_end != _TRAILER_SIZE:
        raise ValueError("unverifiable")
    if data[data_end] != 0 or hashlib.sha1(data[:data_end]).digest() != data[data_end + 1 :]:
        raise ValueError("unverifiable")
    return set(names)


def _read_bounded(path: str) -> tuple[bytes, str | None]:
    """One read. The digest covers exactly those bytes when the file fits."""
    with open(path, "rb") as handle:
        payload = handle.read(_MAX_PBO_BYTES + 1)
        extra = handle.read(1)
    if extra or len(payload) > _MAX_PBO_BYTES:
        raise ValueError("unverifiable")
    return payload, hashlib.sha256(payload).hexdigest()


def resolve_deployed_pbo(relative: str, directories: tuple[str, ...]) -> str:
    """Exactly one match inside the effective mod directories."""
    matches: list[str] = []
    leaf = relative.replace("/", "\\")
    for directory in directories:
        candidate = ntpath.normpath(ntpath.join(directory, leaf))
        parent = ntpath.normcase(ntpath.normpath(directory))
        folded = ntpath.normcase(candidate)
        if not folded.startswith(parent + "\\") and folded != parent:
            continue
        try:
            if os.path.isfile(candidate):
                matches.append(candidate)
        except OSError as error:
            raise ValueError("unverifiable") from error
    if len(matches) != 1:
        raise FileNotFoundError(relative)
    return matches[0]


def verify_artifacts(
    attestation: ProjectAttestation,
    directories: tuple[str, ...],
    *,
    pending: bool = False,
) -> tuple[str, list[dict[str, object]]]:
    """Return (status, rows). ``pending`` skips disk reads (a future build)."""
    rows: list[dict[str, object]] = []
    status = "pending" if pending else "passed"
    for requirement in attestation.artifacts:
        if pending:
            rows.append(
                {"id": requirement.id, "sha256": None, "status": "pending"}
            )
            continue
        digest: str | None = None
        try:
            path = resolve_deployed_pbo(requirement.pbo, directories)
            payload, digest = _read_bounded(path)
            names = plain_pbo_entry_names(payload)
        except FileNotFoundError:
            rows.append({"id": requirement.id, "sha256": None, "status": "failed"})
            if status != "unverifiable":
                status = "failed"
            continue
        except (OSError, UnicodeDecodeError, ValueError):
            rows.append(
                {"id": requirement.id, "sha256": digest, "status": "unverifiable"}
            )
            status = "unverifiable"
            continue
        required = {entry.replace("/", "\\") for entry in requirement.entries}
        if not required <= names:
            rows.append({"id": requirement.id, "sha256": digest, "status": "failed"})
            if status != "unverifiable":
                status = "failed"
            continue
        rows.append({"id": requirement.id, "sha256": digest, "status": "passed"})
    return status, rows


def profile_directory(dev_root: str, role: str) -> str:
    root = "_server" if role == "server" else "_client"
    return ntpath.join(dev_root, root, "profiles")


def _boundary_mark(path: str) -> tuple[int, bool]:
    """Size, and whether that offset cuts through an unfinished line."""
    size = os.path.getsize(path)
    if size <= 0:
        return 0, False
    with open(path, "rb") as handle:
        handle.seek(size - 1)
        last = handle.read(1)
    return size, last not in {b"\n", b"\r"}


def capture_log_boundaries(
    profiles: str, filenames: tuple[str, ...] = ()
) -> dict[str, tuple[int, bool]]:
    """Existing script logs and declared profile files.

    The value is ``(size, partial)``. ``partial`` is true when the captured
    offset is not a line boundary, so the next complete line still belongs to
    text that was already on disk. A missing directory is an empty map.
    """
    boundaries: dict[str, tuple[int, bool]] = {}
    try:
        names = os.listdir(profiles)
    except FileNotFoundError:
        names = []
    except OSError as error:
        raise ValueError("unverifiable") from error
    paths = [
        ntpath.join(profiles, name)
        for name in names
        if name.casefold().endswith(".log") and not name.casefold().startswith("crash")
    ]
    for filename in filenames:
        paths.append(ntpath.normpath(ntpath.join(profiles, filename)))
    for path in paths:
        try:
            if os.path.isfile(path):
                boundaries[ntpath.normcase(path)] = _boundary_mark(path)
        except OSError as error:
            raise ValueError("unverifiable") from error
    return boundaries


def _complete_lines(blob: bytes, *, drop_first: bool) -> list[str]:
    if len(blob) > _MAX_SCAN_BYTES:
        raise ValueError("truncated")
    text = blob.decode("utf-8")
    if text.endswith(("\n", "\r")):
        lines = text.splitlines()
    else:
        lines = text.splitlines()
        if lines:
            lines.pop()
    if drop_first and lines:
        lines = lines[1:]
    return lines


def _stored_boundary(
    boundaries: dict[str, tuple[int, bool] | int], folded: str
) -> tuple[int, bool]:
    raw = boundaries.get(folded)
    if raw is None:
        return 0, False
    if type(raw) is int:
        return raw, False
    if (
        type(raw) is tuple
        and len(raw) == 2
        and type(raw[0]) is int
        and type(raw[1]) is bool
    ):
        return raw
    raise ValueError("unverifiable")


def new_matching_line(
    *,
    pattern: str,
    source: str,
    profiles: str,
    filename: str | None,
    boundaries: dict[str, tuple[int, bool] | int],
) -> str:
    """``passed``, ``failed`` (no match yet) or raises ValueError unverifiable/truncated."""
    if source == "profile_file":
        path = ntpath.normpath(ntpath.join(profiles, filename or ""))
        folded = ntpath.normcase(path)
        try:
            size = os.path.getsize(path)
            offset, partial = _stored_boundary(boundaries, folded)
            if size < offset:
                raise ValueError("unverifiable")
            with open(path, "rb") as handle:
                handle.seek(offset)
                blob = handle.read(_MAX_SCAN_BYTES + 1)
        except FileNotFoundError:
            return "failed"
        except OSError as error:
            raise ValueError("unverifiable") from error
        try:
            lines = _complete_lines(blob, drop_first=partial)
        except UnicodeDecodeError as error:
            raise ValueError("unverifiable") from error
        return "passed" if any(pattern in line for line in lines) else "failed"
    try:
        names = os.listdir(profiles)
    except FileNotFoundError:
        return "failed"
    except OSError as error:
        raise ValueError("unverifiable") from error
    saw_file = False
    for name in names:
        if not name.casefold().endswith(".log") or name.casefold().startswith("crash"):
            continue
        path = ntpath.join(profiles, name)
        folded = ntpath.normcase(path)
        try:
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            offset, partial = _stored_boundary(boundaries, folded)
            if folded not in boundaries:
                offset, partial = 0, False
            if size < offset:
                raise ValueError("unverifiable")
            with open(path, "rb") as handle:
                handle.seek(offset)
                blob = handle.read(_MAX_SCAN_BYTES + 1)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError("unverifiable") from error
        saw_file = True
        try:
            lines = _complete_lines(blob, drop_first=partial)
        except UnicodeDecodeError as error:
            raise ValueError("unverifiable") from error
        if any(pattern in line for line in lines):
            return "passed"
    return "failed" if saw_file or True else "failed"


def initialization_rows(
    attestation: ProjectAttestation,
    *,
    mode: str,
    dev_root: str,
    boundaries_by_role: dict[str, dict[str, tuple[int, bool] | int]],
    pending: bool,
) -> tuple[str, list[dict[str, str]]]:
    applicable = roles_for_mode(mode)
    rows: list[dict[str, str]] = []
    status = "pending" if pending else "passed"
    for requirement in attestation.initialization:
        if requirement.role not in applicable:
            rows.append({"id": requirement.id, "status": "not_applicable"})
            continue
        if pending:
            rows.append({"id": requirement.id, "status": "pending"})
            continue
        profiles = profile_directory(dev_root, requirement.role)
        try:
            outcome = new_matching_line(
                pattern=requirement.pattern,
                source=requirement.source,
                profiles=profiles,
                filename=requirement.filename,
                boundaries=boundaries_by_role.get(requirement.role, {}),
            )
        except ValueError as error:
            outcome = "unverifiable" if str(error) in {"unverifiable", "truncated"} else "unverifiable"
        rows.append({"id": requirement.id, "status": outcome})
        if outcome == "unverifiable":
            status = "unverifiable"
        elif outcome == "failed" and status != "unverifiable":
            status = "failed"
    return status, rows


def combine_status(artifact_status: str, initialization_status: str) -> str:
    if "unverifiable" in {artifact_status, initialization_status}:
        return "unverifiable"
    if "failed" in {artifact_status, initialization_status}:
        return "failed"
    if "pending" in {artifact_status, initialization_status}:
        return "pending"
    return "passed"


def failure_code(status: str, *, initialization_failed: bool) -> str | None:
    if status == "passed" or status == "pending":
        return None
    if status == "unverifiable":
        return "project_attestation_unverifiable"
    if initialization_failed:
        return "project_attestation_initialization_missing"
    return "project_attestation_artifact_missing"


def report(
    attestation: ProjectAttestation,
    *,
    artifact_status: str,
    artifacts: list[dict[str, object]],
    initialization_status: str,
    initialization: list[dict[str, str]],
) -> dict[str, object]:
    status = combine_status(artifact_status, initialization_status)
    if status not in _REPORT_STATUSES:
        status = "unverifiable"
    return {
        "artifacts": artifacts,
        "initialization": initialization,
        "status": status,
    }


def validate_report(value: object) -> dict[str, object]:
    """The terminal's bounded report. Rejects raw logs and extra keys."""
    if (
        type(value) is not dict
        or set(value) != {"artifacts", "initialization", "status"}
        or value.get("status") not in _REPORT_STATUSES
    ):
        raise ValueError("attestation_invalid")
    artifacts = value.get("artifacts")
    initialization = value.get("initialization")
    if (
        type(artifacts) is not list
        or type(initialization) is not list
        or len(artifacts) > _MAX_REQUIREMENTS
        or len(initialization) > _MAX_REQUIREMENTS
    ):
        raise ValueError("attestation_invalid")
    seen: set[str] = set()
    clean_artifacts: list[dict[str, object]] = []
    for item in artifacts:
        if type(item) is not dict or set(item) != {"id", "sha256", "status"}:
            raise ValueError("attestation_invalid")
        ident = item.get("id")
        digest = item.get("sha256")
        row_status = item.get("status")
        if (
            type(ident) is not str
            or ident in seen
            or len(ident) > _MAX_ID
            or row_status not in _REPORT_STATUSES
            or not (digest is None or (type(digest) is str and len(digest) == 64))
        ):
            raise ValueError("attestation_invalid")
        seen.add(ident)
        clean_artifacts.append(
            {"id": ident, "sha256": digest, "status": row_status}
        )
    seen.clear()
    clean_init: list[dict[str, str]] = []
    allowed = _REPORT_STATUSES | {"not_applicable"}
    for item in initialization:
        if type(item) is not dict or set(item) != {"id", "status"}:
            raise ValueError("attestation_invalid")
        ident = item.get("id")
        row_status = item.get("status")
        if (
            type(ident) is not str
            or ident in seen
            or len(ident) > _MAX_ID
            or type(row_status) is not str
            or row_status not in allowed
        ):
            raise ValueError("attestation_invalid")
        seen.add(ident)
        clean_init.append({"id": ident, "status": row_status})
    return {
        "artifacts": clean_artifacts,
        "initialization": clean_init,
        "status": value["status"],
    }
