"""Offline diagnosis when the client dies during Steam bootstrap (ficha 47c4).

This module must stay free of Win32 / FastMCP imports so the diagnosis can be
unit-tested on hosts that cannot bind kernel32.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_API_LOADED_NO_MARKER = b"[API loaded no]"
_MAX_MDMP_SCAN_BYTES = 2 * 1024 * 1024


def _has_steam_api_not_loaded(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(_MAX_MDMP_SCAN_BYTES)
            return _API_LOADED_NO_MARKER in chunk
    except OSError:
        return False


# The profile roots persist across runs and a crashing client leaves one dump per
# death: on 2026-09-24 27 of the 32 ErrorMessage_*.mdmp in the dev client profile
# carried the marker. A dump speaks for a run only if it was NOT in the client
# profile roots when that run's client was about to launch. The decision is a
# set difference over file identities, so a wall-clock jump cannot move a dump
# in or out of it, and a server-profile dump is never a candidate.
_DumpKey = tuple[str, int, int, int]


def _is_error_mdmp(name: str) -> bool:
    return name.startswith("ErrorMessage_") and name.endswith(".mdmp")


def _scan_error_mdmps(directory: str) -> dict[_DumpKey, Path] | None:
    """Identity -> path of every ErrorMessage_*.mdmp; None if the root is unreadable.

    A root that does not exist yet holds no dump: everything later found there
    is new. An identity is (name, file id, size, mtime_ns): a file rewritten in
    place under the same name is a different dump.
    """
    found: dict[_DumpKey, Path] = {}
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not _is_error_mdmp(entry.name):
                    continue
                try:
                    stat = os.stat(entry.path)
                except OSError:
                    continue
                key = (entry.name, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                found[key] = Path(entry.path)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # ValueError: a path the OS cannot even name (embedded NUL, 296b r4).
        return None
    return found


@dataclass(frozen=True)
class ClientDumpBaseline:
    """The dumps already in the client profile roots before a client launch."""

    roots: tuple[str, ...]
    before: tuple[frozenset[_DumpKey] | None, ...]


def snapshot_client_dumps(client_roots: Sequence[str | Path]) -> ClientDumpBaseline:
    roots = tuple(str(root) for root in client_roots)
    before: list[frozenset[_DumpKey] | None] = []
    for root in roots:
        scanned = _scan_error_mdmps(root)
        before.append(None if scanned is None else frozenset(scanned))
    return ClientDumpBaseline(roots=roots, before=tuple(before))


def _newest_new_dump(
    baseline: ClientDumpBaseline,
    ceiling: ClientDumpBaseline | None = None,
) -> Path | None:
    """The most recent client dump absent from the baseline, or None.

    ``ceiling`` is a later launch's snapshot of the same roots: a dump that
    first appeared after it belongs to that later launch, not to this run.
    A root unreadable at the baseline yields nothing: without the "before"
    set there is no way to tell this run's dump from an older one.
    """
    limits: dict[str, frozenset[_DumpKey] | None] = {}
    if ceiling is not None:
        limits = dict(zip(ceiling.roots, ceiling.before))
    newest: tuple[int, str] | None = None
    newest_path: Path | None = None
    for root, before in zip(baseline.roots, baseline.before):
        if before is None:
            continue
        current = _scan_error_mdmps(root)
        if current is None:
            continue
        limit = limits.get(root)
        for key, path in current.items():
            if key in before:
                continue
            if root in limits and (limit is None or key not in limit):
                continue
            rank = (key[3], key[0])
            if newest is None or rank > newest:
                newest = rank
                newest_path = path
    return newest_path


def _new_dump_names_api_not_loaded(
    baseline: ClientDumpBaseline, ceiling: ClientDumpBaseline | None = None
) -> bool:
    newest = _newest_new_dump(baseline, ceiling)
    return newest is not None and _has_steam_api_not_loaded(newest)


def diagnose_client_steam_bootstrap(
    *,
    error_code: object,
    client_alive: object,
    steam_startup: object,
    dump_baseline: ClientDumpBaseline | None = None,
) -> str | None:
    """Name a Steam-bootstrap death. Does not repair Steam (738a remains HOLD).

    When the client is dead after ack and Steam preparation passed on the
    limited old-process path without a startup marker, the death is
    steam_bootstrap.

    When the client is dead after ack and the newest client-profile
    ErrorMessage_*.mdmp that was not in ``dump_baseline`` (taken before the
    client launched) records '[API loaded no]', Steam bootstrap failed
    (ticket 296b). Without a baseline no dump is read: an older run's dump is
    not evidence.
    """
    if error_code != "client_dead_after_ack":
        return None
    if client_alive is not False:
        return None
    if steam_startup == "old_stable_unobserved":
        return "steam_bootstrap"
    if dump_baseline is not None and _new_dump_names_api_not_loaded(dump_baseline):
        return "steam_bootstrap"
    return None


# Late death (296b): dayz_test_run returned succeeded with the client alive and
# the client died about a second later; the daemon then reaped the run. The
# client profile is shared by every MCP process of a project, so the baseline
# and the "a later launch caps it" ceiling live in the daemon
# (client_dump_registry), not here. These helpers carry a baseline over the
# loopback wire and read back what the daemon holds for a retired run.
MAX_WIRE_ROOTS = 8
MAX_WIRE_DUMPS_PER_ROOT = 1024
_MAX_WIRE_ROOT_CHARS = 1024
_MAX_WIRE_NAME_CHARS = 255


def _is_wire_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_wire_root(value: object) -> bool:
    # 296b r4: no control character (NUL included) can name a Windows path;
    # os.scandir raises ValueError on NUL instead of OSError.
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_WIRE_ROOT_CHARS
        and not any(ord(char) < 32 for char in value)
    )


def baseline_to_wire(baseline: ClientDumpBaseline) -> dict[str, object] | None:
    """JSON form of a baseline, or None if it cannot travel intact.

    A root with more dumps than the wire carries travels as null: an
    unreadable "before" set, which never names a death.
    """
    if not baseline.roots or len(baseline.roots) > MAX_WIRE_ROOTS:
        return None
    before: list[object] = []
    for keys in baseline.before:
        if keys is None or len(keys) > MAX_WIRE_DUMPS_PER_ROOT:
            before.append(None)
            continue
        before.append([list(key) for key in sorted(keys)])
    return {"roots": list(baseline.roots), "before": before}


def _key_from_wire(value: object) -> _DumpKey | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    name, ino, size, mtime_ns = value
    if (
        not isinstance(name, str)
        or len(name) > _MAX_WIRE_NAME_CHARS
        or not _is_error_mdmp(name)
    ):
        return None
    if not all(_is_wire_int(number) for number in (ino, size, mtime_ns)):
        return None
    if ino < 0 or size < 0:
        return None
    return (name, ino, size, mtime_ns)


def baseline_from_wire(value: object) -> ClientDumpBaseline | None:
    """Strict inverse of baseline_to_wire; None for anything malformed."""
    if not isinstance(value, dict) or set(value) != {"roots", "before"}:
        return None
    roots, before = value["roots"], value["before"]
    if not isinstance(roots, list) or not isinstance(before, list):
        return None
    if not roots or len(roots) > MAX_WIRE_ROOTS or len(before) != len(roots):
        return None
    if not all(_is_wire_root(root) for root in roots):
        return None
    sets: list[frozenset[_DumpKey] | None] = []
    for keys in before:
        if keys is None:
            sets.append(None)
            continue
        if not isinstance(keys, list) or len(keys) > MAX_WIRE_DUMPS_PER_ROOT:
            return None
        parsed = [_key_from_wire(key) for key in keys]
        if any(key is None for key in parsed):
            return None
        sets.append(frozenset(parsed))  # type: ignore[arg-type]
    return ClientDumpBaseline(roots=tuple(roots), before=tuple(sets))


def diagnose_retired_client_death(
    run_id: object, bindings: object = None
) -> str | None:
    """client_death_diagnosis for a retired run: steam_bootstrap or null.

    ``bindings`` is the daemon's client_dump_bindings map (run_id ->
    {"baseline", "ceiling"}). The daemon lists a run only while it can prove
    which launch came next on its roots; any gap (no record, evicted, daemon
    restarted, malformed) is null. Only the newest client dump that appeared
    between this run's pre-launch baseline and the next launch on the same
    roots is read.
    """
    if not isinstance(run_id, str) or not isinstance(bindings, dict):
        return None
    entry = bindings.get(run_id)
    if not isinstance(entry, dict) or set(entry) != {"baseline", "ceiling"}:
        return None
    baseline = baseline_from_wire(entry["baseline"])
    if baseline is None:
        return None
    ceiling: ClientDumpBaseline | None = None
    if entry["ceiling"] is not None:
        ceiling = baseline_from_wire(entry["ceiling"])
        if ceiling is None:
            return None
    try:
        named = _new_dump_names_api_not_loaded(baseline, ceiling)
    except Exception:
        # 296b r4: one row's unreadable binding is that row's null, never an
        # error of session_status.
        return None
    return "steam_bootstrap" if named else None
