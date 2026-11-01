"""Client-owned seated occupant predicate for player_teleport.

vehicle_get_in_client transfers ownership to the client. A server
SetTransform of that transport desyncs the owning client
(fb-20260915-014733-81f3). Authority-owned seating still moves the
transport; an unreadable telemetry payload is not treated as seated.
"""

from __future__ import annotations


def _wire_bool(value: object) -> bool | None:
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    return None


def occupant_client_seated(telemetry: object) -> bool:
    """True when the client owns a seated occupant (vehicle_get_in_client)."""
    if not isinstance(telemetry, dict):
        return False
    if _wire_bool(telemetry.get("seated")) is not True:
        return False
    return _wire_bool(telemetry.get("is_authority_owner")) is not True


def occupant_client_seated_strict(telemetry: object) -> bool | None:
    """Deletion precheck: None means the seat could not be verified.

    Enforce bools also arrive as integer 0/1 (world_results.py:133-135).
    Keep the permissive teleport predicate unchanged; deletion must not
    mistake malformed telemetry for an empty seat. Authority may be
    omitted only when a successful read confirms seated=false.
    """
    if not isinstance(telemetry, dict):
        return None
    for field in ("ok", "seated"):
        value = telemetry.get(field)
        if type(value) not in (bool, int) or value not in (0, 1):
            return None
    if not telemetry["ok"]:
        return None
    authority = telemetry.get("is_authority_owner")
    if telemetry["seated"] or "is_authority_owner" in telemetry:
        if type(authority) not in (bool, int) or authority not in (0, 1):
            return None
    return bool(telemetry["seated"] and not authority)
