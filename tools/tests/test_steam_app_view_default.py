"""The suite's Steam view: no session tool's observation reaches WMI or the process list.

tests/__init__.py replaces server.observe_app_steam_view with a view that
agrees. Without it, 27 tests in 8 modules built 72 real providers on the
observation thread (census on Linux, 2026-10-28: main 9713894, and the one
module 2e21068 adds, which built none).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import tests
from dayz_mcp import server
from dayz_mcp import steam_preflight as sp
from dayz_mcp.server import ServerConfig, build_app
from tests._tiers import slow_test
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json

_TOOLS = {
    "session_status": {},
    "session_acquire": {"purpose": "fixture"},
    "session_acquire_wait": {"purpose": "fixture"},
}
_OBSERVER = "dayz-mcp-steam-app-view"
_TOOLS_DIR = Path(__file__).resolve().parents[1]
_FIXTURE_SITE = Path(__file__).resolve().parent / "fixtures" / "dayz_mcp"
_PRINT_OBSERVER = (
    "from dayz_mcp import server; "
    "view = server.observe_app_steam_view; "
    "print(view.__module__ + '.' + view.__qualname__)"
)


class _RecordingProvider:
    """Stands in for WindowsSteamPreflightProvider: records each build and each read, and reads nothing."""

    built: list[dict[str, object]] = []
    reads: list[tuple[bool, str]] = []

    def __init__(self, **kwargs: object) -> None:
        self._real_registry = bool(kwargs.get("real_registry"))
        type(self).built.append(dict(kwargs))

    def __getattr__(self, name: str) -> object:
        type(self).reads.append((self._real_registry, name))
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
        self.enter(patch.object(sp, "WindowsSteamPreflightProvider", _RecordingProvider))

    def enter(self, patcher) -> None:
        patcher.start()
        self.addCleanup(patcher.stop)

    async def call(self, tool: str) -> dict:
        # The call observes from an expired cache, and the observation thread
        # it started has finished before anything is checked or restored.
        server._steam_app_view_cache["expires"] = 0.0
        _RecordingProvider.built = []
        _RecordingProvider.reads = []
        before = set(threading.enumerate())
        result = _content_json(await self.app.call_tool(tool, _TOOLS[tool]))
        for thread in set(threading.enumerate()) - before:
            if thread.name == _OBSERVER:
                thread.join(30.0)
                self.assertFalse(thread.is_alive(), f"{tool}: its observation is still running")
        self.assertGreater(
            server._steam_app_view_cache["expires"], 0.0, f"{tool} did not observe"
        )
        return result

    def test_the_suite_installs_a_view_that_agrees(self) -> None:
        self.assertIs(server.observe_app_steam_view, tests.no_steam_app_view)
        self.assertIsNot(server.observe_app_steam_view, sp.observe_app_steam_view)
        self.assertIsNone(tests.no_steam_app_view())

    async def test_each_session_tool_observes_and_builds_no_provider(self) -> None:
        for tool in _TOOLS:
            with self.subTest(tool=tool):
                result = await self.call(tool)
                self.assertEqual(_RecordingProvider.built, [])
                self.assertEqual(_RecordingProvider.reads, [])
                self.assertNotIn("steam_app_view", result)

    async def test_the_real_observation_reads_a_real_registry_provider_for_each_tool(self) -> None:
        # The negative control: with the real observation back, each call
        # builds a real-registry provider and reads it.
        with patch.object(server, "observe_app_steam_view", sp.observe_app_steam_view):
            for tool in _TOOLS:
                with self.subTest(tool=tool):
                    await self.call(tool)
                    self.assertIn({"real_registry": True}, _RecordingProvider.built)
                    self.assertTrue(
                        any(real for real, _name in _RecordingProvider.reads),
                        _RecordingProvider.reads,
                    )
        self.assertIs(server.observe_app_steam_view, tests.no_steam_app_view)


@slow_test
@unittest.skipUnless(sys.platform == "win32", "the child imports the server, which needs Windows")
class FixtureChildSteamViewTest(unittest.TestCase):
    """Children of the suite: the fixture site's sitecustomize imports this package first."""

    def run_child(
        self, code: str, pythonpath: Path, *, localappdata: bool = True
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as root:
            environment = os.environ.copy()
            environment.pop("DAYZ_MCP_FIXTURE_MODE", None)
            environment["PYTHONPATH"] = str(pythonpath)
            if localappdata:
                environment["LOCALAPPDATA"] = str(Path(root) / "local")
            else:
                environment.pop("LOCALAPPDATA", None)
            return subprocess.run(
                [sys.executable, "-c", code],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )

    def test_a_fixture_child_gets_the_suites_view(self) -> None:
        child = self.run_child(_PRINT_OBSERVER, _FIXTURE_SITE)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.strip(), "tests.no_steam_app_view", child.stderr)

    def test_a_child_without_the_fixture_keeps_the_real_observation(self) -> None:
        # The negative control: nothing imports the tests package there.
        child = self.run_child(_PRINT_OBSERVER, _TOOLS_DIR)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(
            child.stdout.strip(),
            "dayz_mcp.steam_preflight.observe_app_steam_view",
            child.stderr,
        )

    def test_the_modules_that_default_localappdata_load_without_it(self) -> None:
        child = self.run_child(
            "import tests.test_progressive_disclosure, tests.test_fn_p0_small_model_loops",
            _TOOLS_DIR,
            localappdata=False,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        # The negative control: in that environment the server alone does not import.
        bare = self.run_child("from dayz_mcp import server", _TOOLS_DIR, localappdata=False)
        self.assertNotEqual(bare.returncode, 0)
        self.assertIn("KeyError: 'LOCALAPPDATA'", bare.stderr)


if __name__ == "__main__":
    unittest.main()
