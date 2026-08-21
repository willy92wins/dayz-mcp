"""D05: capture_screenshot must aim at the active run's CLIENT window.

Two DayZ instances can be up at once, and picking a window by process name alone
photographs whichever one is largest. The agent then certifies a subject that
was never in the world it acted on.

The run record only carries the _server profiles dir, so the disambiguating
token has to be its _client sibling: that path is on the client's command line.
The recorded pid is the launcher's, which need not own the window
(mcp_capture.py:330, mcp-grab.ps1:28-30), so it rides along only as a
fallback.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server  # noqa: E402
from dayz_mcp.server import ServerConfig, build_app  # noqa: E402

from tests.test_client_mode import _fixture_client_runtime  # noqa: E402


class CaptureTargetsRunClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = self.enterContext(_temp_dir())
        root = Path(self._tmp)
        self.server_profiles = root / "_server" / "profiles"
        self.client_profiles = root / "_client" / "profiles"
        self.server_profiles.mkdir(parents=True)
        self.client_profiles.mkdir(parents=True)

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)

        self.captured: dict[str, object] = {}

        def _fake_capture(**kwargs):
            self.captured.update(kwargs)
            return {"isError": True, "error": "stubbed"}

        self._capture_patch = patch.object(
            server.mcp_capture, "capture_dual", side_effect=_fake_capture
        )
        self._capture_patch.start()
        self.addCleanup(self._capture_patch.stop)

    def _with_run(self, state: str) -> None:
        # The run records the SERVER dir only; the client sibling is derived.
        self.runtime.lifecycle_status = AsyncMock(
            return_value={
                "runs": [
                    {
                        "run_id": "run-a",
                        "state": state,
                        "profiles": str(self.server_profiles),
                        "processes": [
                            {"role": "server", "pid": 111},
                            {"role": "client", "pid": 222},
                        ],
                    }
                ]
            }
        )

    async def _call(self) -> None:
        with self.assertRaises(Exception):
            await self.app.call_tool("capture_screenshot", {})

    async def test_live_run_passes_the_client_sibling_as_cmdline_match(self) -> None:
        self._with_run("RUNNING")
        await self._call()
        self.assertEqual(str(self.client_profiles), self.captured.get("cmdline_match"))
        # Never the server dir: that token matches no client window at all.
        self.assertNotEqual(str(self.server_profiles), self.captured.get("cmdline_match"))
        # The launcher pid rides along only as a fallback.
        self.assertEqual(222, self.captured.get("client_pid"))

    async def test_dead_run_falls_back_to_todays_behaviour(self) -> None:
        # Negative control: an EXITED run has no window, so the tool must not
        # start aiming at a stale one -- it has to behave as it did before.
        self._with_run("EXITED")
        await self._call()
        self.assertEqual("", self.captured.get("cmdline_match"))
        self.assertEqual(0, self.captured.get("client_pid"))


def _temp_dir():
    import tempfile

    return tempfile.TemporaryDirectory()


if __name__ == "__main__":
    unittest.main()
