"""The suite's Steam view: no test reaches WMI or the process list through a session tool.

tests/__init__.py replaces server.observe_app_steam_view with a view that
agrees. Without it, 27 tests in 8 modules built 72 real providers on the
observation thread (census on Linux, 2026-10-28: main 9713894, and the one
module 2e21068 adds, which built none).
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import AsyncMock, patch

import tests
from dayz_mcp import server
from dayz_mcp import steam_preflight as sp
from dayz_mcp.server import ServerConfig, build_app
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json

_TOOLS = {
    "session_status": {},
    "session_acquire": {"purpose": "fixture"},
    "session_acquire_wait": {"purpose": "fixture"},
}
_OBSERVER = "dayz-mcp-steam-app-view"


class _RecordingProvider:
    """Stands in for WindowsSteamPreflightProvider: records each build and reads nothing."""

    built: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        type(self).built.append(dict(kwargs))

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"a real provider read {name}")


class SuiteSteamViewTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="fixture",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)
        for tool in _TOOLS:
            answer = AsyncMock(return_value={"status": "active", "warnings": []})
            self.enter(patch.object(self.runtime, tool, new=answer))
        self.enter(
            patch.dict(
                server._steam_app_view_cache,
                {"expires": 0.0, "value": None, "pending": None, "started": 0.0},
            )
        )
        _RecordingProvider.built = []
        self.enter(patch.object(sp, "WindowsSteamPreflightProvider", _RecordingProvider))

    def enter(self, patcher) -> None:
        patcher.start()
        self.addCleanup(patcher.stop)

    async def call_each_tool(self) -> list[dict]:
        results = []
        for tool, arguments in _TOOLS.items():
            server._steam_app_view_cache["expires"] = 0.0
            results.append(_content_json(await self.app.call_tool(tool, arguments)))
            for thread in threading.enumerate():
                if thread.name == _OBSERVER:
                    thread.join(30.0)
        return results

    def test_the_suite_installs_a_view_that_agrees(self) -> None:
        self.assertIs(server.observe_app_steam_view, tests.no_steam_app_view)
        self.assertIsNot(server.observe_app_steam_view, sp.observe_app_steam_view)
        self.assertIsNone(tests.no_steam_app_view())

    async def test_the_session_tools_build_no_provider(self) -> None:
        results = await self.call_each_tool()
        self.assertEqual(_RecordingProvider.built, [])
        for result in results:
            self.assertNotIn("steam_app_view", result)
        # The observation ran and found nothing: it set a new expiry.
        self.assertGreater(server._steam_app_view_cache["expires"], 0.0)

    async def test_the_real_observation_builds_the_provider_that_reads_wmi(self) -> None:
        # The negative control: with the real observation back, the same calls
        # build a real-registry provider, so the recorder sees a real read.
        with patch.object(server, "observe_app_steam_view", sp.observe_app_steam_view):
            await self.call_each_tool()
        self.assertIn({"real_registry": True}, _RecordingProvider.built)
        self.assertIs(server.observe_app_steam_view, tests.no_steam_app_view)


if __name__ == "__main__":
    unittest.main()
