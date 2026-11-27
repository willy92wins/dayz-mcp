"""Install the fast-tier desktop-gate guard, a Steam view, a Steam manifest observation and a bridge PBO reader that read nothing, before any test module imports.

session_acquire, session_acquire_wait and session_status observe this app's
view of Steam (server.observe_app_steam_view), which on Windows reads WMI and
the process list. Every test gets a view that agrees instead; a test of the
observation patches its own. The same tools read Steam's appmanifest for DayZ
and DayZ Tools (server.observe_steam_update_pending); every test gets an
observation that reads no manifest. A child whose PYTHONPATH is the fixture site
(tests/fixtures/dayz_mcp) imports this package from its sitecustomize, so it
gets the same view.

dayz_test_run reads the arg contract out of the @DayZ_MCP PBO a server start
would load (native_launcher_transaction.read_bridge_pbos). The fixture policies
name P:\\Mods, where the deployed PBO lives on the development PC, so every test
gets a reader that reads nothing; a test of the gate patches its own.

The daemon reads the DayZ Studio (M1) reservation of the box from a file on
the development PC (box_reservation.read_box_reservation), which M1 creates
and removes while this suite may run. Every test gets a reader that finds no
reservation; a test of the reservation patches its own.

The server import below runs before any test module's body, and inbox.py reads
LOCALAPPDATA on import. test_progressive_disclosure.py and
test_fn_p0_small_model_loops.py default it before they import the server, so
the same default comes first here.
"""

import functools
import itertools
import os
import tempfile
from pathlib import Path

from tests._tiers import install_fast_tier_desktop_gate_guard

install_fast_tier_desktop_gate_guard()

os.environ.setdefault(
    "LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "dayz-mcp-localapp")
)

# The sealed server_cli.shared_root uses the real profile unless this override
# is set. A test that needs the variable absent clears it in its own setUp.
_suite_shared_root = (
    Path(tempfile.gettempdir()) / "dayz-mcp-test-shared" / f"suite-{os.getpid()}"
)
if not os.environ.get("DAYZ_MCP_SHARED_ROOT", "").strip():
    _suite_shared_root.mkdir(parents=True, exist_ok=True)
    os.environ["DAYZ_MCP_SHARED_ROOT"] = str(_suite_shared_root)
_suite_shared_root_value = os.environ["DAYZ_MCP_SHARED_ROOT"]

if os.name != "nt":
    # The Linux implementations, as python -m dayz_mcp installs them first. Without
    # them the imports below stop at ctypes.WinDLL.
    from dayz_mcp import linux_compat  # noqa: E402

    linux_compat.install()

from dayz_mcp import box_reservation as _box_reservation  # noqa: E402
from dayz_mcp import native_launcher_transaction as _launcher_transaction  # noqa: E402
from dayz_mcp import server as _server  # noqa: E402


def no_steam_app_view() -> None:
    return None


_server.observe_app_steam_view = no_steam_app_view


def no_steam_update_pending() -> None:
    return None


_server.observe_steam_update_pending = no_steam_update_pending


def no_bridge_pbos(mod_path: str) -> list[bytes]:
    raise OSError("tests read no deployed PBO")


_launcher_transaction.read_bridge_pbos = no_bridge_pbos


def no_box_reservation() -> None:
    return None


_box_reservation.read_box_reservation = no_box_reservation


def _isolate_box_queue_per_coordinator() -> None:
    """One shared-queue directory per coordinator unless the test set one.

    The CI runner is unittest and every daemon in that process would otherwise
    share a single ``box-queue.json``. A test that sets ``DAYZ_MCP_SHARED_ROOT``
    before constructing the coordinator keeps that directory.
    """

    from dayz_mcp.session_coordination import SessionCoordinator

    roots = itertools.count(1)
    original = SessionCoordinator.__init__

    # wraps keeps inspect.signature(SessionCoordinator) on the real __init__.
    @functools.wraps(original)
    def _init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        current = os.environ.get("DAYZ_MCP_SHARED_ROOT", "").strip()
        # The suite default stays per coordinator. A test that picks its own
        # directory wants every coordinator in the process to share it.
        if current and os.path.normcase(current) != os.path.normcase(
            _suite_shared_root_value
        ):
            self._box_root = None
            return
        root = (
            Path(tempfile.gettempdir())
            / "dayz-mcp-test-shared"
            / f"{os.getpid()}-{next(roots)}"
        )
        root.mkdir(parents=True, exist_ok=True)
        self._box_root = root

    SessionCoordinator.__init__ = _init


_isolate_box_queue_per_coordinator()
