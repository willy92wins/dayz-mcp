"""steam_update_pending: Steam has an update to apply to DayZ or DayZ Tools.

Steam records each installed app in ``<library>\\steamapps\\appmanifest_<appid>.acf``,
whose ``"StateFlags"`` is a bit field: 2 is UpdateRequired, 4 FullyInstalled, 64
AppRunning (Steam's app state bits, as the game-scanner crate's SteamAppState lists
them). A quiet install reads 4. The development PC read 6, an update to apply, for
DayZ (221100) on 2026-11-05 (HANDOFF.md) and for DayZServer (223350) on 2026-11-09,
and 4 for DayZ and DayZ Tools (830640) that day.

Applying such an update can replace DayZDiag_x64.exe or the DayZ Tools files the
sealed launchers pin by hash, and a launcher sealed before it then has to be
resealed. The manifests read are the ones of the layout the bundle is sealed with
(dayz_tools_paths.resolved_layout), in the steamapps folder above each install.

Advice, never a gate: a layout outside ``steamapps\\common``, a missing or unreadable
manifest, one without a numeric StateFlags, and any failure add nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from dayz_mcp.dayz_tools_paths import DayZLayout, resolved_layout

STEAM_UPDATE_PENDING = "steam_update_pending"
REMEDIATION_STEAM_UPDATE_PENDING = (
    "Steam has an update to apply to these apps, or is applying it. It can replace "
    "DayZDiag_x64.exe or the DayZ Tools files that the sealed launchers pin by hash; "
    "once it is in, a launcher sealed before it has to be resealed. Tell the operator."
)
UPDATE_REQUIRED = 2
FULLY_INSTALLED = 4
# (app id, name, DayZLayout field of its install folder).
APPS: tuple[tuple[int, str, str], ...] = (
    (221100, "DayZ", "dayz"),
    (830640, "DayZ Tools", "tools"),
)
_STATE_FLAGS = re.compile(r'"StateFlags"\s+"(\d+)"', re.IGNORECASE)


def update_pending(flags: int) -> bool:
    """An update is required, or the app is not fully installed. AppRunning and
    the other bits alone say nothing about an update."""
    return bool(flags & UPDATE_REQUIRED) or not flags & FULLY_INSTALLED


def manifest_path(install: Path, app_id: int) -> Path | None:
    common = install.parent
    steamapps = common.parent
    if common.name.casefold() != "common" or steamapps.name.casefold() != "steamapps":
        return None
    return steamapps / f"appmanifest_{app_id}.acf"


def read_state_flags(manifest: Path) -> int | None:
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _STATE_FLAGS.search(text)
    return int(match.group(1)) if match else None


def observe_steam_update_pending(
    layout: DayZLayout | None = None,
) -> dict[str, object] | None:
    """The steam_update_pending advisory, or None when no app has one."""
    try:
        selected = resolved_layout() if layout is None else layout
        apps: list[dict[str, object]] = []
        for app_id, name, field in APPS:
            manifest = manifest_path(getattr(selected, field), app_id)
            flags = None if manifest is None else read_state_flags(manifest)
            if flags is not None and update_pending(flags):
                apps.append(
                    {
                        "app_id": app_id,
                        "name": name,
                        "state_flags": flags,
                        "manifest": str(manifest),
                    }
                )
    except Exception:
        return None
    if not apps:
        return None
    return {
        "code": STEAM_UPDATE_PENDING,
        "apps": apps,
        "remediation": REMEDIATION_STEAM_UPDATE_PENDING,
    }
