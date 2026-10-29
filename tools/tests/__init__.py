"""Install the fast-tier desktop-gate guard, and a Steam view that reads nothing, before any test module imports.

session_acquire, session_acquire_wait and session_status observe this app's
view of Steam (server.observe_app_steam_view), which on Windows reads WMI and
the process list. Every test gets a view that agrees instead; a test of the
observation patches its own. A child whose PYTHONPATH is the fixture site
(tests/fixtures/dayz_mcp) imports this package from its sitecustomize, so it
gets the same view.

The server import below runs before any test module's body, and inbox.py reads
LOCALAPPDATA on import. test_progressive_disclosure.py and
test_fn_p0_small_model_loops.py default it before they import the server, so
the same default comes first here.
"""

import os
import tempfile
from pathlib import Path

from tests._tiers import install_fast_tier_desktop_gate_guard

install_fast_tier_desktop_gate_guard()

os.environ.setdefault(
    "LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "dayz-mcp-localapp")
)

from dayz_mcp import server as _server  # noqa: E402


def no_steam_app_view() -> None:
    return None


_server.observe_app_steam_view = no_steam_app_view
