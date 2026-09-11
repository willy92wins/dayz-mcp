"""Offline diagnosis when the client dies during Steam bootstrap (ficha 47c4).

This module must stay free of Win32 / FastMCP imports so the diagnosis can be
unit-tested on hosts that cannot bind kernel32.
"""

from __future__ import annotations


def diagnose_client_steam_bootstrap(
    *,
    error_code: object,
    client_alive: object,
    steam_startup: object,
) -> str | None:
    """Name a Steam-bootstrap death. Does not repair Steam (738a remains HOLD).

    When the client is dead after ack and Steam preparation passed on the
    limited old-process path without a startup marker, the death is
    steam_bootstrap.
    """
    if error_code != "client_dead_after_ack":
        return None
    if client_alive is not False:
        return None
    if steam_startup == "old_stable_unobserved":
        return "steam_bootstrap"
    return None
