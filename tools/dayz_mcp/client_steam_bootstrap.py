"""Offline diagnosis when the client dies during Steam bootstrap (ficha 47c4).

This module must stay free of Win32 / FastMCP imports so the diagnosis can be
unit-tested on hosts that cannot bind kernel32.
"""

from __future__ import annotations

import os
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
# carried the marker. Only a dump written since this call started can speak for
# this run's death; the slack absorbs filesystem timestamp granularity.
_MDMP_MTIME_SLACK_S = 2.0


def _is_error_mdmp(name: str) -> bool:
    return name.startswith("ErrorMessage_") and name.endswith(".mdmp")


def _find_newest_error_mdmp(directory: Path, not_before: float) -> Path | None:
    try:
        newest_path: Path | None = None
        newest_mtime = not_before - _MDMP_MTIME_SLACK_S
        with os.scandir(directory) as entries:
            for entry in entries:
                if _is_error_mdmp(entry.name):
                    try:
                        stat = entry.stat()
                        if stat.st_mtime >= newest_mtime:
                            newest_mtime = stat.st_mtime
                            newest_path = Path(entry.path)
                    except OSError:
                        continue
        return newest_path
    except OSError:
        return None


def diagnose_client_steam_bootstrap(
    *,
    error_code: object,
    client_alive: object,
    steam_startup: object,
    artifacts_paths: Sequence[str | Path] | None = None,
    not_before: float | None = None,
) -> str | None:
    """Name a Steam-bootstrap death. Does not repair Steam (738a remains HOLD).

    When the client is dead after ack and Steam preparation passed on the
    limited old-process path without a startup marker, the death is
    steam_bootstrap.

    When the client is dead after ack and the newest ErrorMessage_*.mdmp
    written since ``not_before`` (epoch seconds, the call's start) records
    '[API loaded no]', Steam bootstrap failed (ticket 296b). Without
    ``not_before`` no dump is read: an older run's dump is not evidence.
    """
    if error_code != "client_dead_after_ack":
        return None
    if client_alive is not False:
        return None
    if steam_startup == "old_stable_unobserved":
        return "steam_bootstrap"

    if artifacts_paths and not_before is not None:
        for raw_path in artifacts_paths:
            p = Path(raw_path)
            if p.is_dir():
                newest_mdmp = _find_newest_error_mdmp(p, not_before)
                if newest_mdmp and _has_steam_api_not_loaded(newest_mdmp):
                    return "steam_bootstrap"
    return None
