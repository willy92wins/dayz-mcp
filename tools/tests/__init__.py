"""Install the fast-tier desktop-gate guard, and a Steam view that reads nothing, before any test module imports.

session_acquire, session_acquire_wait and session_status observe this app's
view of Steam (server.observe_app_steam_view), which on Windows reads WMI and
the process list. Every test gets a view that agrees instead; a test of the
observation patches its own.
"""

from tests._tiers import install_fast_tier_desktop_gate_guard

install_fast_tier_desktop_gate_guard()

from dayz_mcp import server as _server  # noqa: E402


def no_steam_app_view() -> None:
    return None


_server.observe_app_steam_view = no_steam_app_view
