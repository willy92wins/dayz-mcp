"""Keep full GC pauses short and install the fast-tier desktop-gate guard, a Steam view and a bridge PBO reader that read nothing, before any test module imports.

Full garbage collections are kept short first (tests/_gc_pauses.py): they
stop every thread of the process, and by the end of a shard they took up to
1 s, which a 0.2-0.5 s wall-clock gate charged to the code under test.

session_acquire, session_acquire_wait and session_status observe this app's
view of Steam (server.observe_app_steam_view), which on Windows reads WMI and
the process list. Every test gets a view that agrees instead; a test of the
observation patches its own. A child whose PYTHONPATH is the fixture site
(tests/fixtures/dayz_mcp) imports this package from its sitecustomize, so it
gets the same view.

dayz_test_run reads the arg contract out of the @DayZ_MCP PBO a server start
would load (native_launcher_transaction.read_bridge_pbos). The fixture policies
name P:\\Mods, where the deployed PBO lives on the development PC, so every test
gets a reader that reads nothing; a test of the gate patches its own.

The server import below runs before any test module's body, and inbox.py reads
LOCALAPPDATA on import. test_progressive_disclosure.py and
test_fn_p0_small_model_loops.py default it before they import the server, so
the same default comes first here.
"""

import os
import tempfile
from pathlib import Path

from tests._gc_pauses import install as _install_short_gc_pauses
from tests._tiers import install_fast_tier_desktop_gate_guard

# Full collections stop every thread of the test process; tests/_gc_pauses.py
# keeps them short so a wall-clock gate measures the code, not the collector.
_install_short_gc_pauses()
install_fast_tier_desktop_gate_guard()

os.environ.setdefault(
    "LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "dayz-mcp-localapp")
)

from dayz_mcp import native_launcher_transaction as _launcher_transaction  # noqa: E402
from dayz_mcp import server as _server  # noqa: E402


def no_steam_app_view() -> None:
    return None


_server.observe_app_steam_view = no_steam_app_view


def no_bridge_pbos(mod_path: str) -> list[bytes]:
    raise OSError("tests read no deployed PBO")


_launcher_transaction.read_bridge_pbos = no_bridge_pbos
