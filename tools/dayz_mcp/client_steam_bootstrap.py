"""Offline diagnosis when the client dies during Steam bootstrap (ficha 47c4).

This module must stay free of Win32 / FastMCP imports so the diagnosis can be
unit-tested on hosts that cannot bind kernel32.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
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
    except OSError:
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
# baseline taken before that launch is kept here, keyed by run_id, so the
# retired-run view can still name the death. In-process only: a restarted MCP
# process has no binding and publishes null, never a guess.
_BINDINGS_LIMIT = 32
_bindings: OrderedDict[str, list[ClientDumpBaseline | None]] = OrderedDict()
_bindings_lock = threading.Lock()


def bind_client_dumps(run_id: str, baseline: ClientDumpBaseline) -> None:
    """Bind a pre-launch baseline to run_id and close earlier bindings on its roots.

    Every earlier run whose client used one of these roots gets this baseline
    as its ceiling (if it has none yet): dumps that appear from now on belong
    to this launch, not to them.
    """
    shared = set(baseline.roots)
    with _bindings_lock:
        for other_id, entry in _bindings.items():
            if other_id == run_id or entry[1] is not None:
                continue
            if shared.intersection(entry[0].roots):
                entry[1] = baseline
        _bindings.pop(run_id, None)
        _bindings[run_id] = [baseline, None]
        while len(_bindings) > _BINDINGS_LIMIT:
            _bindings.popitem(last=False)


def diagnose_retired_client_death(run_id: object) -> str | None:
    """client_death_diagnosis for a retired run: steam_bootstrap or null.

    Only the newest client dump that appeared between this run's pre-launch
    baseline and the next launch on the same roots is read.
    """
    if not isinstance(run_id, str):
        return None
    with _bindings_lock:
        entry = _bindings.get(run_id)
        if entry is None:
            return None
        baseline, ceiling = entry[0], entry[1]
    if _new_dump_names_api_not_loaded(baseline, ceiling):
        return "steam_bootstrap"
    return None
