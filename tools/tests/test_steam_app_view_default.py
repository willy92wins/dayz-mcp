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
    """Stands in for WindowsSteamPreflightProvider: records each build and each
    call of read_active_process, which the observation reads first, and reads
    nothing."""

    built: list[dict[str, object]] = []
    reads: list[str] = []

    def __init__(self, **kwargs: object) -> None:
        self._view = "real" if kwargs.get("real_registry") else "app"
        type(self).built.append(dict(kwargs))

    def read_active_process(self) -> object:
        type(self).reads.append(self._view)
        raise AssertionError(f"the {self._view} provider read ActiveProcess")


class _SessionToolsFixture(unittest.IsolatedAsyncioTestCase):
    """The three session tools through build_app in client mode, with the
    runtime's answers mocked and the recorder in place of the provider."""

    join_s = 30.0

    def setUp(self) -> None:
        self.observers: list[threading.Thread] = []
        self.joined: list[threading.Thread] = []
        self.patchers: list = []
        self.addCleanup(self.restore)
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
        self.patchers.append(patcher)

    def restore(self) -> None:
        # While an observation thread of the test runs, the patches stay:
        # restored, the provider it builds next would be the real one.
        if any(thread.is_alive() for thread in self.observers):
            raise AssertionError("an observation thread is still running, so the patches stay")
        while self.patchers:
            self.patchers.pop().stop()

    async def call(self, tool: str) -> dict:
        # Called outside any subtest, so a call that fails ends the test: no
        # other tool runs while its observation does. It observes from an
        # expired cache, and the observation threads it started are joined
        # even when it raises.
        server._steam_app_view_cache["expires"] = 0.0
        _RecordingProvider.built = []
        _RecordingProvider.reads = []
        before = set(threading.enumerate())
        try:
            result = _content_json(await self.app.call_tool(tool, _TOOLS[tool]))
        finally:
            started = [
                thread
                for thread in set(threading.enumerate()) - before
                if thread.name == _OBSERVER
            ]
            self.observers.extend(started)
            self.join_observers(started)
        self.assertFalse(
            any(thread.is_alive() for thread in started),
            f"{tool}: its observation is still running",
        )
        self.assertGreater(
            server._steam_app_view_cache["expires"], 0.0, f"{tool} did not observe"
        )
        return result

    def join_observers(self, threads: list[threading.Thread]) -> None:
        # Each join is listed, so a test can tell whether a call that raised
        # waited for its observation.
        for thread in threads:
            self.joined.append(thread)
            thread.join(self.join_s)


class SuiteSteamViewTest(_SessionToolsFixture):
    def test_the_suite_installs_a_view_that_agrees(self) -> None:
        self.assertIs(server.observe_app_steam_view, tests.no_steam_app_view)
        self.assertIsNot(server.observe_app_steam_view, sp.observe_app_steam_view)
        self.assertIsNone(tests.no_steam_app_view())

    async def test_each_session_tool_observes_and_builds_no_provider(self) -> None:
        for tool in _TOOLS:
            result = await self.call(tool)
            with self.subTest(tool=tool):
                self.assertEqual(_RecordingProvider.built, [])
                self.assertEqual(_RecordingProvider.reads, [])
                self.assertNotIn("steam_app_view", result)

    async def test_the_real_observation_reads_a_real_registry_provider_for_each_tool(self) -> None:
        # The negative control: with the real observation back, each call
        # builds a real-registry provider and calls its read_active_process.
        with patch.object(server, "observe_app_steam_view", sp.observe_app_steam_view):
            for tool in _TOOLS:
                await self.call(tool)
                with self.subTest(tool=tool):
                    self.assertIn({"real_registry": True}, _RecordingProvider.built)
                    self.assertIn("real", _RecordingProvider.reads)
        self.assertIs(server.observe_app_steam_view, tests.no_steam_app_view)


class StalledObservationTest(unittest.TestCase):
    """The cleanup that the fixture registers, run by unittest while an
    observation of the test still runs: it fails and keeps every patch the
    fixture started, and once the observation ends they go."""

    def run_a_stalled_call(self, *, raises: bool) -> None:
        # The view returns only once this test sets the event: no timeout of
        # its own ends the observation before the checks below.
        release = threading.Event()

        class StalledCall(_SessionToolsFixture):
            join_s = 0.0

            def setUp(self) -> None:
                super().setUp()
                self.enter(
                    patch.object(server, "observe_app_steam_view", lambda: release.wait() and None)
                )
                self.enter(patch.object(server, "_STEAM_APP_VIEW_WAIT_S", 0.0))

            async def test_call(self) -> None:
                if raises:
                    self.runtime.session_status.side_effect = RuntimeError("fixture")
                    with self.assertRaisesRegex(Exception, "fixture"):
                        await self.call("session_status")
                else:
                    with self.assertRaisesRegex(AssertionError, "still running"):
                        await self.call("session_status")

        inner = StalledCall("test_call")
        self.addCleanup(self.end, inner, release)
        result = unittest.TestResult()
        inner.run(result)
        # Its call failed as it should, and the cleanup its setUp registered
        # failed after it: the observation still ran.
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1, result.failures)
        self.assertIn("still running, so the patches stay", result.failures[0][1])
        self.assertEqual(len(inner.observers), 1)
        self.assertEqual(inner.joined, inner.observers, "the call did not join its observation")
        self.assertTrue(inner.observers[0].is_alive())
        self.assertIs(sp.WindowsSteamPreflightProvider, _RecordingProvider)
        self.end(inner, release)
        self.assertIsNot(sp.WindowsSteamPreflightProvider, _RecordingProvider)
        self.assertIs(server.observe_app_steam_view, tests.no_steam_app_view)

    @staticmethod
    def end(inner: _SessionToolsFixture, release: threading.Event) -> None:
        release.set()
        for thread in inner.observers:
            thread.join(30.0)
        inner.restore()

    def test_an_observation_still_running_fails_its_call_and_its_cleanup_keeps_the_patches(
        self,
    ) -> None:
        self.run_a_stalled_call(raises=False)

    def test_a_call_that_raises_joins_its_observation_and_its_cleanup_keeps_the_patches(
        self,
    ) -> None:
        self.run_a_stalled_call(raises=True)


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
