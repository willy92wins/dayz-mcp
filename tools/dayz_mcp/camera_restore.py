"""Pure camera_get observer used by restore_gameplay.

No daemon, Win32, or MCP imports: the trichotomy has to stay testable
offline (decision 6). Liberation is a positive view=player reading.
"""
from __future__ import annotations

from typing import Any

RESTORE_CAMERA_PROBE_CMD = "camera_get"
RESTORE_NOT_VERIFIED = ("controls", "hud", "simulation")
RESTORE_CAMERA_VIEW_PLAYER = "player"
RESTORE_CAMERA_VIEW_SCRIPTED = "scripted"
RESTORE_CAMERA_VIEW_VEHICLE = "vehicle"


def restore_camera_verdict(probe: dict[str, Any]) -> tuple[str, str]:
    """Classify a camera_get result as released | still_active | unverified.

    Liberation is a positive ``view=player`` (or the legacy
    ``error=player_camera_active`` wire), or a readable ``view=vehicle``
    while still seated. A missing scripted camera, an empty view, or
    ``ok=0`` is ``unverified``; absence is never success.
    """
    camera = probe.get("camera") if isinstance(probe, dict) else None
    if not isinstance(camera, dict):
        return "unverified", "camera_get returned no camera block"
    view = camera.get("view")
    if camera.get("ok") and view == RESTORE_CAMERA_VIEW_VEHICLE:
        return "released", ""
    if camera.get("ok") and (
        view == RESTORE_CAMERA_VIEW_SCRIPTED or camera.get("viewport_moved")
    ):
        return "still_active", str(camera.get("error") or view or "")
    if not camera.get("ok"):
        reason = str(camera.get("error") or "camera_not_readable")
        return "unverified", f"camera_get could not read the camera ({reason})"
    if view == RESTORE_CAMERA_VIEW_PLAYER or camera.get("error") == "player_camera_active":
        return "released", ""
    return (
        "unverified",
        "camera_get reported neither view=player nor a scripted camera",
    )
