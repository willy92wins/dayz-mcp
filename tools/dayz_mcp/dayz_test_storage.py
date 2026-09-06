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
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


MARKER_SCHEMA_VERSION = 1
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
    return {
        "base_mods": list(normalize_modset(base_mods, mods_root=mods_root)),
        "project_mod": normalize_mod_path(project_mod, mods_root).casefold(),
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
    if not isinstance(project_mod, str) or not project_mod:
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


def _read_json(path: str) -> object | None:
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    if not raw or len(raw) > 65_536:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _write_json_atomic(path: str, document: object, *, replace: bool) -> None:
    """Temporary sibling, fsync, rename. Read back before returning.

    `replace=False` refuses an existing destination: a journal is created once
    and never overwritten. `replace=True` is only used for the marker, which is
    derived data and never the source of truth of anything a player owns.
    """
    payload = _canonical(document)
    # The temporary is NOT named after its destination. A hard kill between the
    # write and the rename leaves it on disk forever (v1 deletes nothing), and a
    # leftover called `storage_1.modset.rotation.<txid>.json.tmp-...` matches the
    # journal prefix without matching the journal grammar, which would block
    # every future launch of the mission. Its own prefix keeps it out of the scan.
    temporary = ntpath.join(
        ntpath.dirname(path),
        f"{STORAGE_NAME}.modset.tmp-{os.getpid()}-{next(_TEMPORARY_SEQUENCE)}",
    )
    with open(temporary, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if replace:
            os.replace(temporary, path)
        else:
            if os.path.exists(path):
                raise StorageError("destination_exists")
            os.rename(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    with open(path, "rb") as handle:
        if handle.read() != payload:
            raise StorageError("write_not_durable")


def _rename_strict(source: str, destination: str) -> None:
    """Rename to a destination that does not exist. Never overwrite, never delete."""
    if os.path.exists(destination):
        raise StorageError("destination_exists")
    os.rename(source, destination)


def read_marker(mission: str) -> MarkerRead:
    """Classify the sibling seal. Never repairs, never trusts a path it carries."""
    path = ntpath.join(_mission_dir(mission), MARKER_NAME)
    if not os.path.isfile(path):
        return MarkerRead(MARKER_ABSENT, None, None, False)
    document = _read_json(path)
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


def _publish_marker(mission: str, seal: str, project: str) -> None:
    _write_json_atomic(
        ntpath.join(mission, MARKER_NAME), _marker_document(seal, project), replace=True
    )


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
) -> dict[str, object]:
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "txid": txid,
        "phase": phase,
        "new_seal": new_seal,
        "old_seal": old_seal,
        "project": project,
        "storage_backup": storage_backup,
        "marker_backup": marker_backup,
    }


def _valid_journal(document: object, txid: str) -> dict[str, object] | None:
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schema_version",
            "txid",
            "phase",
            "new_seal",
            "old_seal",
            "project",
            "storage_backup",
            "marker_backup",
        }
        or document.get("schema_version") != MARKER_SCHEMA_VERSION
        or document.get("txid") != txid
        or document.get("phase") not in _PHASES
        or not isinstance(document.get("new_seal"), str)
        or _SEAL.fullmatch(str(document["new_seal"])) is None
        or not isinstance(document.get("project"), str)
        or not document["project"]
        or not isinstance(document.get("storage_backup"), str)
        or not document["storage_backup"]
        or not isinstance(document.get("marker_backup"), str)
        or not document["marker_backup"]
    ):
        return None
    old_seal = document.get("old_seal")
    if old_seal is not None and (
        not isinstance(old_seal, str) or _SEAL.fullmatch(old_seal) is None
    ):
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


def _finish_rotation(
    mission: str,
    journal_path: str,
    document: dict[str, object],
) -> tuple[str, str | None]:
    """Move the old marker aside if it is still there, then publish the new one.

    Idempotent on purpose: recovery re-enters here without knowing how far the
    crashed attempt got, and re-publishing an identical marker is a no-op.
    """
    marker_path = ntpath.join(mission, MARKER_NAME)
    marker_backup = str(document["marker_backup"])
    marker_backup_path = ntpath.join(mission, marker_backup)
    published_marker_backup: str | None = None
    # The backup is checked FIRST on purpose. Once it exists the old marker has
    # already been set aside, so whatever sits at marker_path is the new one --
    # published by an attempt that died before advancing the phase. Asking about
    # marker_path first would try to move the new marker onto the old backup and
    # abort the recovery of a tree that was already moved (crash at boundary 6).
    if os.path.exists(marker_backup_path):
        published_marker_backup = marker_backup
    elif os.path.exists(marker_path):
        _rename_strict(marker_path, marker_backup_path)
        published_marker_backup = marker_backup
    _publish_marker(mission, str(document["new_seal"]), str(document["project"]))
    _advance_phase(journal_path, document, PHASE_MARKER_PUBLISHED)
    _complete_journal(mission, str(document["txid"]))
    return str(document["storage_backup"]), published_marker_backup


def _reconcile_journal(mission: str, txid: str, seal: str) -> RotationResult | None:
    """Pre-pass. Returns a terminal result, or None when it is safe to classify.

    Driven by the physical state, not by the declared phase: a crash between the
    rename and the phase write leaves `prepared` on disk with the tree already
    moved, so believing the phase alone would rotate twice.
    """
    journal_path = ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_SUFFIX)
    document = _valid_journal(_read_json(journal_path), txid)
    if document is None:
        return _blocked("journal_unreadable", seal)
    storage_present = os.path.isdir(ntpath.join(mission, STORAGE_NAME))
    backup_path = ntpath.join(mission, str(document["storage_backup"]))
    backup_present = os.path.exists(backup_path)
    if document["phase"] == PHASE_MARKER_PUBLISHED:
        # Codex F-03. The phase is a CLAIM, not evidence. A journal that says
        # marker_published over a mission whose backup does not exist, or whose
        # marker is not the one this transaction published, describes a rotation
        # that did not happen -- and completing it would hand the engine the old
        # world under the new seal. The backup is the tell: this phase is only
        # reachable after the tree was renamed.
        if not backup_present or read_marker(mission).seal != str(document["new_seal"]):
            return _blocked("journal_state_impossible", seal)
        _complete_journal(mission, txid)
        return None
    if storage_present and not backup_present:
        # Nothing was moved. Abort the transaction and classify from scratch.
        _complete_journal(mission, txid)
        return None
    if not storage_present and backup_present:
        # The case the design names: killed between the rename and the seal.
        # The journal's new_seal is the authority; the old marker is not.
        # The engine will create the new tree on the next start.
        backup, marker_backup = _finish_rotation(mission, journal_path, document)
        return RotationResult(
            launch_allowed=True,
            storage_rotated=True,
            storage_backup=backup,
            storage_marker_backup=marker_backup,
            storage_seal=str(document["new_seal"])[:8],
            storage_recovery_required=False,
            storage_reset_notice=RESET_NOTICE,
            decision=DECISION_ROTATE,
            reason="recovered_storage_moved",
        )
    return _blocked("journal_state_impossible", seal)


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
    mission = _mission_dir(mission)
    _validate_transaction(seal, project, now, txid)
    old_seal8 = (
        marker.seal[:8]
        if marker.state == MARKER_PRESENT_VALID and isinstance(marker.seal, str)
        else LEGACY_SEAL8
    )
    backup = f"{STORAGE_NAME}.modset-{_backup_stamp(now)}-{old_seal8}"
    marker_backup = f"{backup}.marker.json"
    journal_path = ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_SUFFIX)
    reserved = (
        ntpath.join(mission, backup),
        ntpath.join(mission, marker_backup),
        journal_path,
        ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_COMPLETED_SUFFIX),
    )
    if any(os.path.exists(path) for path in reserved):
        # Nothing has been touched yet, and nothing will be.
        return _blocked("backup_name_collision", seal)
    document = _journal_document(
        txid=txid,
        phase=PHASE_PREPARED,
        new_seal=seal,
        old_seal=marker.seal if marker.state == MARKER_PRESENT_VALID else None,
        project=project,
        storage_backup=backup,
        marker_backup=marker_backup,
    )
    _write_json_atomic(journal_path, document, replace=False)
    _rename_strict(ntpath.join(mission, STORAGE_NAME), ntpath.join(mission, backup))
    document = _advance_phase(journal_path, document, PHASE_STORAGE_MOVED)
    stored_backup, published_marker_backup = _finish_rotation(
        mission, journal_path, document
    )
    return RotationResult(
        launch_allowed=True,
        storage_rotated=True,
        storage_backup=stored_backup,
        storage_marker_backup=published_marker_backup,
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
    if not os.path.isdir(mission):
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
            mission, active[0][len(JOURNAL_PREFIX) : -len(JOURNAL_SUFFIX)], seal
        )
        if recovered is not None:
            return recovered
        # The reconciled transaction already owns the journal names built from
        # `txid`, so a rotation decided after it gets its own derived id. Same
        # call, same input, deterministic: no clock and no randomness here.
        rotation_txid = derived_txid(txid)
    marker = read_marker(mission)
    storage_present = os.path.isdir(ntpath.join(mission, STORAGE_NAME))
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
