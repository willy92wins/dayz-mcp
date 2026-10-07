"""Rotate `<mission>\\storage_1` only when the mod set that wrote it changed.

Ficha fb-20260829-115147-4407 and its annex fb-20260904-144835-01ae. A server
started over a `storage_1` written by another mod set does not die: it bleeds
persistence exceptions and never reaches the poll loop, so the bridge answers
`game_not_ready:reason=server_poll_stale` (server.py:413) and the symptom names
the bridge while the cause is on disk.

Design: reviews/2026-09-04-reserva/R1-storage-4407-diseno.md, option A (M15-min).
Four invariants this module never breaks:

* the seal is a SIBLING of `storage_1`, never a file inside it -- the engine owns
  what lives inside and prunes it, and a marker inside would travel with the
  tree when it is set aside;
* rotation is a RENAME to a name that does not exist yet. v1 deletes nothing:
  pruning the siblings is a separate admin verb with its own ficha;
* the journal is written and fsynced BEFORE the first mutation, and recovery
  reconciles against the PHYSICAL state, never against the declared phase alone;
* fail-closed. An absent or unreadable seal rotates; an ambiguous journal blocks
  the launch instead of guessing.

DIAGNOSTIC RULE (annex 01ae). Faced with `server_poll_stale` while the server
process is alive -- and above all right after a mod set change -- read the
server RPT before blaming the bridge, and count BOTH signatures in
``STORAGE_POISON_RPT_SIGNATURES``. Measured on the 19 server starts of
2026-09-04: the dominant case (16:26:48) has `Failed to read modstorage` = 0 and
`Scripted variables corrupted` = 43197, so a rule that greps only the first
signature would not have seen the incident that motivated the annex.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import ntpath
import os
import re
import stat
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


MARKER_SCHEMA_VERSION = 1
JOURNAL_SCHEMA_LEGACY = 1
JOURNAL_SCHEMA_VERSION = 2
MARKER_ALGORITHM = "sha256"
STORAGE_NAME = "storage_1"
MARKER_NAME = "storage_1.modset.json"

JOURNAL_PREFIX = "storage_1.modset.rotation."
JOURNAL_SUFFIX = ".json"
JOURNAL_COMPLETED_SUFFIX = ".completed.json"
_JOURNAL_ACTIVE = re.compile(r"^storage_1\.modset\.rotation\.[0-9a-f]{32}\.json$")
_TXID = re.compile(r"^[0-9a-f]{32}$")
_SEAL = re.compile(r"^[0-9a-f]{64}$")

PHASE_PREPARED = "prepared"
PHASE_STORAGE_MOVED = "storage_moved"
PHASE_MARKER_PUBLISHED = "marker_published"
_PHASES = frozenset({PHASE_PREPARED, PHASE_STORAGE_MOVED, PHASE_MARKER_PUBLISHED})

MARKER_ABSENT = "absent"
MARKER_PRESENT_VALID = "present_valid"
MARKER_PRESENT_INVALID = "present_invalid"

DECISION_SEAL_ONLY = "seal_only"
DECISION_REUSE = "reuse"
DECISION_ROTATE = "rotate"

RESET_NOTICE = "mission_world_and_character_reset"
LEGACY_SEAL8 = "legacy"

# Annex 01ae: BOTH literals, not one. See the module docstring.
STORAGE_POISON_RPT_SIGNATURES = (
    "Failed to read modstorage",
    "Scripted variables corrupted",
)

MODSET_ROLES = ("base_mods", "project_mod", "extra_mods", "server_mods")


_TEMPORARY_SEQUENCE = itertools.count()


class StorageError(ValueError):
    """A malformed argument. Never raised for a state found on disk."""


@dataclass(frozen=True, slots=True)
class MarkerRead:
    state: str
    seal: str | None
    project: str | None
    present: bool


@dataclass(frozen=True, slots=True)
class Decision:
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class RotationResult:
    launch_allowed: bool
    storage_rotated: bool
    storage_backup: str | None
    storage_marker_backup: str | None
    storage_seal: str
    storage_recovery_required: bool
    storage_reset_notice: str | None
    decision: str
    reason: str


# --------------------------------------------------------------------------
# Pure: normalisation and seal
# --------------------------------------------------------------------------


def normalize_mod_path(value: object, mods_root: object) -> str:
    """The single implementation of the `-mod=` entry rule.

    dayz_test_worker._mod_path delegates here on purpose: a mirror of the rule
    is not the rule, and ficha 9d46 is what a mirrored rule costs. The argv and
    the seal must never be able to disagree about which folder a mod entry names.
    """
    if not isinstance(mods_root, str) or not mods_root:
        raise StorageError("invalid_mods_root")
    if not isinstance(value, str) or not value:
        raise StorageError("invalid_mod_entry")
    if ntpath.isabs(value):
        return ntpath.normpath(value)
    return ntpath.join(mods_root, value)


def normalize_modset(values: Sequence[object], *, mods_root: str) -> tuple[str, ...]:
    """Absolute, normalised, casefolded paths, in the order they were given.

    Order is preserved inside the role: two mod sets with the same members in a
    different load order are not interchangeable for the engine, so a global
    sort would seal them as the same game. Casefold because the Windows file
    system is case-insensitive: `P:\\Mods\\@CF` and `p:\\mods\\@cf` are one folder.
    """
    if not isinstance(values, (list, tuple)):
        raise StorageError("invalid_modset")
    return tuple(
        normalize_mod_path(value, mods_root).casefold() for value in values
    )


def modset_roles(
    *,
    base_mods: Sequence[object],
    project_mod: object,
    extra_mods: Sequence[object],
    server_mods: Sequence[object],
    mods_root: str,
) -> dict[str, object]:
    """The four roles, normalised, ready for ``modset_seal``.

    `-mod=` and `-serverMod=` are two different argv (dayz_test_worker.py:235
    and :240-245) and BOTH load mods into the server, so they are sealed as
    separate roles: merging them would let a change confined to `server_mods`
    leave the seal unmoved.
    """
    if project_mod is None:
        sealed_project: str | None = None
    else:
        sealed_project = normalize_mod_path(project_mod, mods_root).casefold()
    return {
        "base_mods": list(normalize_modset(base_mods, mods_root=mods_root)),
        "project_mod": sealed_project,
        "extra_mods": list(normalize_modset(extra_mods, mods_root=mods_root)),
        "server_mods": list(normalize_modset(server_mods, mods_root=mods_root)),
    }


def modset_seal(roles: Mapping[str, object]) -> str:
    """sha256 of the canonical JSON of the four roles. Never of the joined string.

    The `;` separator does not enter the seal, so a mod whose name contains a
    `;` cannot forge a collision.
    """
    if not isinstance(roles, Mapping) or set(roles) != set(MODSET_ROLES):
        raise StorageError("invalid_modset_roles")
    project_mod = roles["project_mod"]
    # null is the absent implicit project role (project_mod_override). A string
    # role keeps the historical hash input.
    if project_mod is not None and (
        not isinstance(project_mod, str) or not project_mod
    ):
        raise StorageError("invalid_modset_roles")
    sealed_roles: dict[str, object] = {"project_mod": project_mod}
    for name in ("base_mods", "extra_mods", "server_mods"):
        value = roles[name]
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise StorageError("invalid_modset_roles")
        sealed_roles[name] = list(value)
    document = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "algorithm": MARKER_ALGORITHM,
        "roles": sealed_roles,
    }
    return hashlib.sha256(_canonical(document)).hexdigest()


def should_rotate(
    *, seal: str, marker: MarkerRead, storage_present: bool
) -> Decision:
    """Pure. The six rows of the design matrix, fail-closed on the unknown.

    An absent or unreadable marker over a present tree ROTATES: without a seal
    the tree cannot be PROVEN compatible, and being wrong by rotating costs one
    test world that a rename can bring back, while being wrong by launching
    costs a bleeding server, a blocked box and a diagnosis that accuses the
    bridge.
    """
    if not isinstance(seal, str) or _SEAL.fullmatch(seal) is None:
        raise StorageError("invalid_seal")
    if not isinstance(marker, MarkerRead) or type(storage_present) is not bool:
        raise StorageError("invalid_decision_input")
    if not storage_present:
        return Decision(DECISION_SEAL_ONLY, "storage_absent")
    if marker.state == MARKER_ABSENT:
        return Decision(DECISION_ROTATE, "marker_absent")
    if marker.state == MARKER_PRESENT_INVALID:
        return Decision(DECISION_ROTATE, "marker_unreadable")
    if marker.seal == seal:
        return Decision(DECISION_REUSE, "seal_matches")
    return Decision(DECISION_ROTATE, "seal_changed")


# --------------------------------------------------------------------------
# Disk: marker, journal, rotation
# --------------------------------------------------------------------------


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _mission_dir(mission: object) -> str:
    if not isinstance(mission, str) or not mission:
        raise StorageError("invalid_mission")
    return mission


def _win32_file_identity(name: str) -> str:
    """Path-segment identity used by Win32: case, trailing dots, trailing spaces."""
    return name.rstrip(" .").casefold()


def _plain_artifact_name(value: object) -> bool:
    """D5, the same predicate process_lifecycle uses for a backup name.

    Applied before any join. A name that is not a single path segment can alias
    an authoritative artifact or escape the mission directory.
    """
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and ":" not in value
    )


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _parse_json(raw: bytes) -> object | None:
    if not raw or len(raw) > 65_536:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _read_json(path: str) -> object | None:
    try:
        raw = _read_bytes(path)
    except OSError:
        return None
    return _parse_json(raw)


def _write_all(handle: object, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = handle.write(view)  # type: ignore[attr-defined]
        if not isinstance(written, int) or written <= 0:
            raise StorageError("short_write")
        view = view[written:]


def _exclusive_temporary(directory: str) -> str:
    """Create an empty sibling temporary. Never truncate a name that exists."""
    while True:
        temporary = ntpath.join(
            directory,
            f"{STORAGE_NAME}.modset.tmp-{os.getpid()}-{next(_TEMPORARY_SEQUENCE)}",
        )
        try:
            handle = open(temporary, "xb")
        except FileExistsError:
            continue
        handle.close()
        return temporary


def _write_json_atomic(path: str, document: object, *, replace: bool) -> None:
    """Temporary sibling, fsync, publish, read back.

    `replace=False` refuses an existing destination: a journal is created once
    and never overwritten. `replace=True` publishes the marker or a later phase.
    The temporary is not named after its destination, so a leftover cannot match
    the journal grammar. A publish that fails unlinks that temporary only.
    """
    payload = _canonical(document)
    temporary = _exclusive_temporary(ntpath.dirname(path))
    try:
        with open(temporary, "wb") as handle:
            _write_all(handle, payload)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            if _entry_kind(path) != "absent":
                raise StorageError("destination_exists")
            os.rename(temporary, path)
    except Exception:
        try:
            if _entry_kind(temporary) != "absent":
                os.unlink(temporary)
        except OSError:
            pass
        raise
    with open(path, "rb") as handle:
        if handle.read() != payload:
            raise StorageError("write_not_durable")


def _rename_strict(source: str, destination: str) -> None:
    """Rename to a destination that does not exist. Never overwrite, never delete."""
    if _entry_kind(destination) != "absent":
        raise StorageError("destination_exists")
    os.rename(source, destination)


def read_marker(mission: str) -> MarkerRead:
    """Classify the sibling seal. Never repairs, never trusts a path it carries."""
    path = ntpath.join(_mission_dir(mission), MARKER_NAME)
    if _entry_kind(path) != "file":
        return MarkerRead(MARKER_ABSENT, None, None, False)
    document = _parse_json(_read_bytes(path))
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "algorithm", "seal", "project"}
        or document.get("schema_version") != MARKER_SCHEMA_VERSION
        or document.get("algorithm") != MARKER_ALGORITHM
        or not isinstance(document.get("seal"), str)
        or _SEAL.fullmatch(str(document.get("seal"))) is None
        or not isinstance(document.get("project"), str)
        or not document["project"]
    ):
        return MarkerRead(MARKER_PRESENT_INVALID, None, None, True)
    return MarkerRead(
        MARKER_PRESENT_VALID, str(document["seal"]), str(document["project"]), True
    )


def _marker_document(seal: str, project: str) -> dict[str, object]:
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "algorithm": MARKER_ALGORITHM,
        "seal": seal,
        "project": project,
    }


def _backup_stamp(now: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def _blocked(reason: str, seal: str) -> RotationResult:
    return RotationResult(
        launch_allowed=False,
        storage_rotated=False,
        storage_backup=None,
        storage_marker_backup=None,
        storage_seal=seal[:8],
        storage_recovery_required=True,
        storage_reset_notice=None,
        decision="blocked",
        reason=reason,
    )


def _journal_document(
    *,
    txid: str,
    phase: str,
    new_seal: str,
    old_seal: str | None,
    project: str,
    storage_backup: str,
    marker_backup: str,
    old_marker_state: str,
    old_marker_sha256: str | None,
) -> dict[str, object]:
    return {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "txid": txid,
        "phase": phase,
        "new_seal": new_seal,
        "old_seal": old_seal,
        "project": project,
        "storage_backup": storage_backup,
        "marker_backup": marker_backup,
        "old_marker_state": old_marker_state,
        "old_marker_sha256": old_marker_sha256,
    }


def _valid_journal(document: object, txid: str) -> dict[str, object] | None:
    if not isinstance(document, dict):
        return None
    version = document.get("schema_version")
    legacy = version == JOURNAL_SCHEMA_LEGACY
    current = version == JOURNAL_SCHEMA_VERSION
    if legacy == current:
        return None
    expected = {
        "schema_version",
        "txid",
        "phase",
        "new_seal",
        "old_seal",
        "project",
        "storage_backup",
        "marker_backup",
    }
    if current:
        expected |= {"old_marker_state", "old_marker_sha256"}
    if set(document) != expected:
        return None
    phase = document.get("phase")
    new_seal = document.get("new_seal")
    project = document.get("project")
    if (
        document.get("txid") != txid
        or not isinstance(phase, str)
        or phase not in _PHASES
        or not isinstance(new_seal, str)
        or _SEAL.fullmatch(new_seal) is None
        or not isinstance(project, str)
        or not 1 <= len(project) <= 64
        or not _plain_artifact_name(document.get("storage_backup"))
        or not _plain_artifact_name(document.get("marker_backup"))
    ):
        return None
    old_seal = document.get("old_seal")
    if old_seal is not None and (
        not isinstance(old_seal, str) or _SEAL.fullmatch(old_seal) is None
    ):
        return None
    backup = str(document["storage_backup"])
    marker_backup = str(document["marker_backup"])
    journal_name = JOURNAL_PREFIX + txid + JOURNAL_SUFFIX
    completed_name = JOURNAL_PREFIX + txid + JOURNAL_COMPLETED_SUFFIX
    reserved = (
        STORAGE_NAME,
        MARKER_NAME,
        backup,
        marker_backup,
        journal_name,
        completed_name,
    )
    # Windows names one file when segments differ only by case or by trailing
    # spaces and periods (`storage_1.` is `storage_1`). Refuse before any join
    # that would rename a marker onto the world.
    if len({_win32_file_identity(name) for name in reserved}) != len(reserved):
        return None
    if not current:
        return document
    state = document.get("old_marker_state")
    digest = document.get("old_marker_sha256")
    if state == MARKER_ABSENT:
        if digest is not None or old_seal is not None:
            return None
    elif isinstance(state, str) and state in {MARKER_PRESENT_VALID, MARKER_PRESENT_INVALID}:
        if (
            not isinstance(digest, str)
            or _SEAL.fullmatch(digest) is None
        ):
            return None
        if state == MARKER_PRESENT_VALID and old_seal is None:
            return None
        if state == MARKER_PRESENT_INVALID and old_seal is not None:
            return None
    else:
        return None
    return document


def _advance_phase(path: str, document: dict[str, object], phase: str) -> dict[str, object]:
    updated = dict(document)
    updated["phase"] = phase
    _write_json_atomic(path, updated, replace=True)
    return updated


def _complete_journal(mission: str, txid: str) -> None:
    _rename_strict(
        ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_SUFFIX),
        ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_COMPLETED_SUFFIX),
    )


def _entry_kind(path: str) -> str:
    """`absent`, `dir`, `file`, `link` or `other`. An OSError is not absence."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return "absent"
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _classify_marker_bytes(raw: bytes) -> tuple[str, str | None]:
    document = _parse_json(raw)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "algorithm", "seal", "project"}
        or document.get("schema_version") != MARKER_SCHEMA_VERSION
        or document.get("algorithm") != MARKER_ALGORITHM
        or not isinstance(document.get("seal"), str)
        or _SEAL.fullmatch(document["seal"]) is None
        or not isinstance(document.get("project"), str)
        or not document["project"]
    ):
        return MARKER_PRESENT_INVALID, None
    return MARKER_PRESENT_VALID, str(document["seal"])


def _canonical_marker(seal: str, project: str) -> bytes:
    return _canonical(_marker_document(seal, project))


def _publish_payload(path: str, payload: bytes, *, replace: bool) -> None:
    temporary = _exclusive_temporary(ntpath.dirname(path))
    try:
        with open(temporary, "wb") as handle:
            _write_all(handle, payload)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            if _entry_kind(path) != "absent":
                raise StorageError("destination_exists")
            os.rename(temporary, path)
    except Exception:
        try:
            if _entry_kind(temporary) != "absent":
                os.unlink(temporary)
        except OSError:
            pass
        raise
    if _read_bytes(path) != payload:
        raise StorageError("write_not_durable")


def _publish_marker(mission: str, seal: str, project: str) -> None:
    path = ntpath.join(mission, MARKER_NAME)
    payload = _canonical_marker(seal, project)
    if _entry_kind(path) == "file" and _read_bytes(path) == payload:
        return
    _publish_payload(path, payload, replace=True)


def _marker_is_n(raw: bytes | None, document: dict[str, object]) -> bool:
    if raw is None:
        return False
    return raw == _canonical_marker(str(document["new_seal"]), str(document["project"]))


def _legacy_is_n(raw: bytes | None, document: dict[str, object]) -> bool:
    """Schema 1 published the seal, not a byte-identical canonical document.

    Main treated a valid marker carrying ``new_seal`` as the publication.
    Schema 2 keeps the exact canonical bytes in ``_marker_is_n``.
    """
    if raw is None:
        return False
    state, seal = _classify_marker_bytes(raw)
    if state != MARKER_PRESENT_VALID or seal != document.get("new_seal"):
        return False
    parsed = _parse_json(raw)
    return isinstance(parsed, dict) and parsed.get("project") == document.get("project")


def _legacy_seal_bytes(raw: bytes | None, old_seal: str) -> bool:
    if raw is None:
        return False
    state, seal = _classify_marker_bytes(raw)
    return state == MARKER_PRESENT_VALID and seal == old_seal


def _select_action(
    document: dict[str, object],
    *,
    w: str,
    d: str,
    m_kind: str,
    m_bytes: bytes | None,
    k_kind: str,
    k_bytes: bytes | None,
    completed: bool,
) -> str:
    """One admitted continuation, or `refuse`. No mutation."""
    if completed:
        return "refuse"
    if w not in {"absent", "dir"} or d not in {"absent", "dir"}:
        return "refuse"
    if w == "dir" and d == "dir":
        return "refuse"
    if w == "absent" and d == "absent":
        return "refuse"
    phase = str(document["phase"])
    if phase in {PHASE_STORAGE_MOVED, PHASE_MARKER_PUBLISHED} and d != "dir":
        return "refuse"
    schema2 = document.get("schema_version") == JOURNAL_SCHEMA_VERSION
    if schema2:
        return _select_schema2(
            document, w=w, d=d, m_kind=m_kind, m_bytes=m_bytes,
            k_kind=k_kind, k_bytes=k_bytes,
        )
    return _select_legacy(
        document, w=w, d=d, m_kind=m_kind, m_bytes=m_bytes,
        k_kind=k_kind, k_bytes=k_bytes,
    )


def _original_matches(
    document: dict[str, object], kind: str, raw: bytes | None
) -> bool:
    state = document.get("old_marker_state")
    if state == MARKER_ABSENT:
        return kind == "absent"
    if kind != "file" or raw is None:
        return False
    return _sha256(raw) == document.get("old_marker_sha256")


def _preservation_held(
    document: dict[str, object], k_kind: str, k_bytes: bytes | None
) -> bool:
    state = document.get("old_marker_state")
    if state == MARKER_ABSENT:
        return k_kind == "absent"
    return _original_matches(document, k_kind, k_bytes)


def _select_schema2(
    document: dict[str, object],
    *,
    w: str,
    d: str,
    m_kind: str,
    m_bytes: bytes | None,
    k_kind: str,
    k_bytes: bytes | None,
) -> str:
    phase = str(document["phase"])
    is_n = _marker_is_n(m_bytes, document)
    if phase == PHASE_PREPARED:
        if k_kind != "absent" or not _original_matches(document, m_kind, m_bytes):
            return "refuse"
        if w == "dir" and d == "absent":
            return "abort"
        if w == "absent" and d == "dir":
            return "advance_s"
        return "refuse"
    if phase == PHASE_STORAGE_MOVED:
        if document.get("old_marker_state") == MARKER_ABSENT:
            if k_kind != "absent":
                return "refuse"
            if m_kind == "absent":
                return "publish_n"
            if is_n:
                return "advance_q"
            return "refuse"
        if k_kind == "absent":
            if _original_matches(document, m_kind, m_bytes):
                return "preserve"
            return "refuse"
        if not _preservation_held(document, k_kind, k_bytes):
            return "refuse"
        if m_kind == "absent":
            return "publish_n"
        if is_n:
            return "advance_q"
        return "refuse"
    if phase == PHASE_MARKER_PUBLISHED:
        if not is_n or not _preservation_held(document, k_kind, k_bytes):
            return "refuse"
        return "complete"
    return "refuse"


def _legacy_opaque(kind: str) -> bool:
    return kind in {"file", "dir", "link", "other"}


def _select_legacy(
    document: dict[str, object],
    *,
    w: str,
    d: str,
    m_kind: str,
    m_bytes: bytes | None,
    k_kind: str,
    k_bytes: bytes | None,
) -> str:
    phase = str(document["phase"])
    old = document.get("old_seal")
    known = isinstance(old, str)
    is_n = _legacy_is_n(m_bytes, document)
    if phase == PHASE_PREPARED and w == "dir" and d == "absent":
        # An intact world is only the abort of a prepared journal. K authenticates
        # an old marker after the world has moved; it does not make a new canonical
        # marker compatible with the tree that is still in place. Preserved-K
        # exceptions stay in the moved-world branches below.
        if k_kind != "absent":
            return "refuse"
        if known and not _legacy_seal_bytes(m_bytes, old):
            return "refuse"
        return "abort"
    if d != "dir" or w != "absent":
        return "refuse"
    if known:
        return _select_legacy_known(
            document, old=old, phase=phase, m_kind=m_kind, m_bytes=m_bytes,
            k_kind=k_kind, k_bytes=k_bytes, is_n=is_n,
        )
    return _select_legacy_unknown(
        phase=phase, m_kind=m_kind, k_kind=k_kind, is_n=is_n,
    )


def _select_legacy_unknown(
    *,
    phase: str,
    m_kind: str,
    k_kind: str,
    is_n: bool,
) -> str:
    if phase == PHASE_MARKER_PUBLISHED:
        if not is_n:
            return "refuse"
        return "complete"
    if phase not in {PHASE_PREPARED, PHASE_STORAGE_MOVED}:
        return "refuse"
    if phase == PHASE_PREPARED:
        if k_kind != "absent" and not (m_kind == "absent" or is_n):
            return "refuse"
        return "advance_s"
    if k_kind == "absent" and _legacy_opaque(m_kind):
        return "preserve"
    if k_kind != "absent" and not (m_kind == "absent" or is_n):
        return "refuse"
    if not is_n:
        return "publish_n"
    return "advance_q"


def _select_legacy_known(
    document: dict[str, object],
    *,
    old: str,
    phase: str,
    m_kind: str,
    m_bytes: bytes | None,
    k_kind: str,
    k_bytes: bytes | None,
    is_n: bool,
) -> str:
    del document
    pending = _legacy_seal_bytes(m_bytes, old)
    k_ok = k_kind == "file" and _legacy_seal_bytes(k_bytes, old)
    if phase == PHASE_MARKER_PUBLISHED:
        if not k_ok or not is_n:
            return "refuse"
        return "complete"
    if phase not in {PHASE_PREPARED, PHASE_STORAGE_MOVED}:
        return "refuse"
    if phase == PHASE_PREPARED:
        if k_kind == "absent" and pending and not is_n:
            return "advance_s"
        if k_kind == "absent" and pending and is_n:
            return "advance_s"
        if k_ok and (m_kind == "absent" or is_n):
            return "advance_s"
        return "refuse"
    if k_kind == "absent" and pending:
        return "preserve"
    if k_ok and m_kind == "absent":
        return "publish_n"
    if k_ok and is_n:
        return "advance_q"
    return "refuse"


def _observe_pair(
    mission: str, document: dict[str, object]
) -> tuple[str, str, str, bytes | None, str, bytes | None, bool]:
    storage = ntpath.join(mission, STORAGE_NAME)
    backup = ntpath.join(mission, str(document["storage_backup"]))
    marker = ntpath.join(mission, MARKER_NAME)
    preserved = ntpath.join(mission, str(document["marker_backup"]))
    completed = ntpath.join(
        mission,
        JOURNAL_PREFIX + str(document["txid"]) + JOURNAL_COMPLETED_SUFFIX,
    )
    w = _entry_kind(storage)
    d = _entry_kind(backup)
    m_kind = _entry_kind(marker)
    k_kind = _entry_kind(preserved)
    c_kind = _entry_kind(completed)
    if c_kind not in {"absent", "file"}:
        raise StorageError("completed_journal_type")
    m_bytes = _read_bytes(marker) if m_kind == "file" else None
    k_bytes = _read_bytes(preserved) if k_kind == "file" else None
    return w, d, m_kind, m_bytes, k_kind, k_bytes, c_kind == "file"


def _apply_action(
    mission: str,
    journal_path: str,
    document: dict[str, object],
    action: str,
) -> dict[str, object]:
    if action == "abort":
        _complete_journal(mission, str(document["txid"]))
        return document
    if action == "advance_s":
        return _advance_phase(journal_path, document, PHASE_STORAGE_MOVED)
    if action == "preserve":
        _rename_strict(
            ntpath.join(mission, MARKER_NAME),
            ntpath.join(mission, str(document["marker_backup"])),
        )
        return document
    if action == "publish_n":
        _publish_marker(
            mission, str(document["new_seal"]), str(document["project"])
        )
        return document
    if action == "advance_q":
        return _advance_phase(journal_path, document, PHASE_MARKER_PUBLISHED)
    if action == "complete":
        _complete_journal(mission, str(document["txid"]))
        return document
    raise StorageError("unknown_recovery_action")


def _finish_recorded(
    mission: str,
    journal_path: str,
    document: dict[str, object],
    call_seal: str,
    project: str,
) -> RotationResult | None:
    """Walk the admitted continuation until the journal is complete, then reseal.

    An OSError keeps whatever the walk already published and returns a blocked
    result. It does not roll the transaction back.
    """
    try:
        current = document
        for _step in range(8):
            observed = _observe_pair(mission, current)
            action = _select_action(
                current,
                w=observed[0],
                d=observed[1],
                m_kind=observed[2],
                m_bytes=observed[3],
                k_kind=observed[4],
                k_bytes=observed[5],
                completed=observed[6],
            )
            if action == "refuse":
                return _blocked("journal_state_impossible", call_seal)
            if action == "abort":
                _apply_action(mission, journal_path, current, action)
                return None
            if action == "complete":
                _apply_action(mission, journal_path, current, action)
                break
            current = _apply_action(mission, journal_path, current, action)
        else:
            return _blocked("recovery_finish_failed", call_seal)
        if _entry_kind(ntpath.join(mission, STORAGE_NAME)) != "absent":
            return _blocked("recovery_marker_mismatch", call_seal)
        marker_path = ntpath.join(mission, MARKER_NAME)
        published = _read_bytes(marker_path) if _entry_kind(marker_path) == "file" else None
        legacy = document.get("schema_version") == JOURNAL_SCHEMA_LEGACY
        if legacy:
            published_ok = _legacy_is_n(published, document)
        else:
            published_ok = published == _canonical_marker(
                str(document["new_seal"]), str(document["project"])
            )
        if not published_ok:
            return _blocked("recovery_marker_mismatch", call_seal)
        if call_seal != str(document["new_seal"]):
            try:
                _publish_marker(mission, call_seal, project)
            except OSError:
                return _blocked("recovery_seal_publish_failed", call_seal)
            except StorageError:
                return _blocked("recovery_seal_publish_failed", call_seal)
            expected_x = _canonical_marker(call_seal, project)
            if _entry_kind(marker_path) != "file" or _read_bytes(marker_path) != expected_x:
                return _blocked("recovery_marker_mismatch", call_seal)
        # Same seal: N stays the journal's project. A different caller project
        # is not a reseal and is not a mismatch. The rotation result stands.
        preserved = document["marker_backup"]
        preserved_path = ntpath.join(mission, str(preserved))
        preserved_kind = _entry_kind(preserved_path)
        return RotationResult(
            launch_allowed=True,
            storage_rotated=True,
            storage_backup=str(document["storage_backup"]),
            storage_marker_backup=(
                str(preserved) if preserved_kind != "absent" else None
            ),
            storage_seal=call_seal[:8],
            storage_recovery_required=False,
            storage_reset_notice=RESET_NOTICE,
            decision=DECISION_ROTATE,
            reason="recovered_storage_moved",
        )
    except (OSError, StorageError):
        return _blocked("recovery_finish_failed", call_seal)


def _reconcile_journal(
    mission: str, txid: str, seal: str, project: str
) -> RotationResult | None:
    """Pre-pass. Returns a terminal result, or None when it is safe to classify.

    Driven by the physical state, not by the declared phase: a crash between the
    rename and the phase write leaves `prepared` on disk with the tree already
    moved, so believing the phase alone would rotate twice.
    """
    journal_path = ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_SUFFIX)
    try:
        raw = _read_bytes(journal_path)
    except OSError:
        return _blocked("recovery_observation_failed", seal)
    document = _valid_journal(_parse_json(raw), txid)
    if document is None:
        return _blocked("journal_unreadable", seal)
    return _finish_recorded(mission, journal_path, document, seal, project)


def _active_journals(mission: str) -> tuple[list[str], bool] | None:
    """The active journals, or None when the directory could not be enumerated.

    Codex F-06. Returning an empty list for an OSError made "I could not look"
    indistinguishable from "there is nothing there", and the caller turned that
    into permission to launch over a mission with a transaction possibly in
    flight. An observation that failed is not an absence.
    """
    try:
        entries = os.listdir(mission)
    except OSError:
        return None
    active: list[str] = []
    malformed = False
    for name in entries:
        if not name.startswith(JOURNAL_PREFIX):
            continue
        if name.endswith(JOURNAL_COMPLETED_SUFFIX):
            continue
        if _JOURNAL_ACTIVE.fullmatch(name) is None:
            malformed = True
            continue
        active.append(name)
    return sorted(active), malformed


def rotate_storage(
    mission: str,
    *,
    seal: str,
    project: str,
    now: float,
    txid: str,
    marker: MarkerRead,
) -> RotationResult:
    """The only function with effects. Reserves every name before moving anything."""
    del marker
    mission = _mission_dir(mission)
    _validate_transaction(seal, project, now, txid)
    storage_path = ntpath.join(mission, STORAGE_NAME)
    marker_path = ntpath.join(mission, MARKER_NAME)
    try:
        if _entry_kind(storage_path) != "dir":
            return _blocked("storage_not_a_directory", seal)
        marker_kind = _entry_kind(marker_path)
        if marker_kind not in {"absent", "file"}:
            return _blocked("marker_type_unsupported", seal)
        old_state = MARKER_ABSENT
        old_seal: str | None = None
        old_hash: str | None = None
        if marker_kind == "file":
            captured = _read_bytes(marker_path)
            old_state, old_seal = _classify_marker_bytes(captured)
            old_hash = _sha256(captured)
    except OSError:
        return _blocked("recovery_observation_failed", seal)
    old_seal8 = old_seal[:8] if isinstance(old_seal, str) else LEGACY_SEAL8
    backup = f"{STORAGE_NAME}.modset-{_backup_stamp(now)}-{old_seal8}"
    marker_backup = f"{backup}.marker.json"
    journal_path = ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_SUFFIX)
    reserved = (
        ntpath.join(mission, backup),
        ntpath.join(mission, marker_backup),
        journal_path,
        ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_COMPLETED_SUFFIX),
    )
    if any(_entry_kind(path) != "absent" for path in reserved):
        return _blocked("backup_name_collision", seal)
    document = _journal_document(
        txid=txid,
        phase=PHASE_PREPARED,
        new_seal=seal,
        old_seal=old_seal,
        project=project,
        storage_backup=backup,
        marker_backup=marker_backup,
        old_marker_state=old_state,
        old_marker_sha256=old_hash,
    )
    _write_json_atomic(journal_path, document, replace=False)
    _rename_strict(storage_path, ntpath.join(mission, backup))
    document = _advance_phase(journal_path, document, PHASE_STORAGE_MOVED)
    finished = _finish_recorded(mission, journal_path, document, seal, project)
    if finished is None or not finished.launch_allowed:
        return finished if finished is not None else _blocked(
            "journal_state_impossible", seal
        )
    return RotationResult(
        launch_allowed=True,
        storage_rotated=True,
        storage_backup=finished.storage_backup,
        storage_marker_backup=finished.storage_marker_backup,
        storage_seal=seal[:8],
        storage_recovery_required=False,
        storage_reset_notice=RESET_NOTICE,
        decision=DECISION_ROTATE,
        reason="seal_changed",
    )


def derived_txid(txid: str) -> str:
    """A second transaction id for the same call, derived, never random."""
    if not isinstance(txid, str) or _TXID.fullmatch(txid) is None:
        raise StorageError("invalid_txid")
    return hashlib.sha256(f"{txid}:retry".encode("ascii")).hexdigest()[:32]


def _validate_transaction(seal: object, project: object, now: object, txid: object) -> None:
    if not isinstance(seal, str) or _SEAL.fullmatch(seal) is None:
        raise StorageError("invalid_seal")
    if not isinstance(project, str) or not 1 <= len(project) <= 64:
        raise StorageError("invalid_project")
    if type(now) is not float and type(now) is not int:
        raise StorageError("invalid_now")
    if not isinstance(txid, str) or _TXID.fullmatch(txid) is None:
        raise StorageError("invalid_txid")


def prepare_storage(
    mission: str,
    *,
    seal: str,
    project: str,
    now: float,
    txid: str,
) -> RotationResult:
    """Single entry point for the launcher: recover, classify, then act.

    Called from dayz_test_worker before the first broker frame of a launch that
    starts a server, so a refusal here has never created a process.
    """
    mission = _mission_dir(mission)
    _validate_transaction(seal, project, now, txid)
    if _entry_kind(mission) != "dir":
        return _blocked("mission_not_a_directory", seal)
    scan = _active_journals(mission)
    if scan is None:
        return _blocked("mission_not_enumerable", seal)
    active, malformed = scan
    if malformed:
        return _blocked("journal_name_invalid", seal)
    if len(active) > 1:
        return _blocked("journal_ambiguous", seal)
    rotation_txid = txid
    if active:
        recovered = _reconcile_journal(
            mission,
            active[0][len(JOURNAL_PREFIX) : -len(JOURNAL_SUFFIX)],
            seal,
            project,
        )
        if recovered is not None:
            return recovered
        # The reconciled transaction already owns the journal names built from
        # `txid`, so a rotation decided after it gets its own derived id. Same
        # call, same input, deterministic: no clock and no randomness here.
        rotation_txid = derived_txid(txid)
    try:
        marker = read_marker(mission)
        storage_present = _entry_kind(ntpath.join(mission, STORAGE_NAME)) == "dir"
    except OSError:
        return _blocked("recovery_observation_failed", seal)
    decision = should_rotate(
        seal=seal, marker=marker, storage_present=storage_present
    )
    if decision.action == DECISION_ROTATE:
        return rotate_storage(
            mission,
            seal=seal,
            project=project,
            now=now,
            txid=rotation_txid,
            marker=marker,
        )
    if decision.action == DECISION_SEAL_ONLY:
        _publish_marker(mission, seal, project)
    return RotationResult(
        launch_allowed=True,
        storage_rotated=False,
        storage_backup=None,
        storage_marker_backup=None,
        storage_seal=seal[:8],
        storage_recovery_required=False,
        storage_reset_notice=None,
        decision=decision.action,
        reason=decision.reason,
    )
