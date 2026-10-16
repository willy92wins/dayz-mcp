"""steam_app_view_stale: this app's copy of Steam's ActiveProcess against the real one.

2026-10-16: an agent's own launch of DayZ, from a shell inside the Claude app,
read the app's private copy of ActiveProcess (a Steam gone for ten days) and
stopped at "Unable to locate a running instance of Steam", while dayz_test_run
kept launching. observe_app_steam_view names that state; session_acquire,
session_acquire_wait and session_status carry it as a warning and refuse nothing.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from dayz_mcp import steam_preflight as sp
from dayz_mcp.server import ServerConfig, build_app
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json

_LIVE_STEAM = 277748
_GONE_STEAM = 67468
_STEAM_EXE = r"C:\Program Files (x86)\Steam\steam.exe"
_PROCESSES = {_LIVE_STEAM: _STEAM_EXE, 4242: r"C:\Windows\notepad.exe"}


class _View:
    """One registry view of ActiveProcess over the host's process table."""

    def __init__(
        self,
        pid: object,
        *,
        processes: dict[int, str] = _PROCESSES,
        unavailable: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.pid = pid
        self.processes = processes
        self.real_registry_unavailable = unavailable
        self.error = error

    def read_active_process(self) -> sp.SteamActiveProcessSnapshot:
        if self.error is not None:
            raise self.error
        return sp.SteamActiveProcessSnapshot(pid=self.pid, active_user=1)

    def process_exists(self, pid: int) -> bool:
        return pid in self.processes

    def process_image_path(self, pid: int) -> str:
        return self.processes[pid]


class ObserveAppSteamViewTest(unittest.TestCase):
    def assert_named(self, app_pid: object) -> None:
        view = sp.observe_app_steam_view(_View(app_pid), _View(_LIVE_STEAM))
        self.assertEqual(
            view,
            {
                "code": sp.STEAM_APP_VIEW_STALE,
                "app_view_pid": app_pid,
                "real_pid": _LIVE_STEAM,
                "remediation": sp.REMEDIATION_STEAM_APP_VIEW_STALE,
            },
        )

    def test_a_copy_naming_a_steam_that_is_gone_is_named(self) -> None:
        self.assert_named(_GONE_STEAM)

    def test_a_copy_with_pid_zero_is_named(self) -> None:
        # 2026-09-23: pid 0 inside the Claude app, the live Steam outside.
        self.assert_named(0)

    def test_a_copy_whose_pid_now_belongs_to_another_program_is_named(self) -> None:
        self.assert_named(4242)

    def test_it_stays_quiet_when_a_dayz_from_this_app_would_find_steam(self) -> None:
        other_steam = {**_PROCESSES, 5150: _STEAM_EXE}
        cases = {
            "views_agree": (_View(_LIVE_STEAM), _View(_LIVE_STEAM)),
            "copy_names_a_live_steam": (
                _View(5150, processes=other_steam),
                _View(_LIVE_STEAM, processes=other_steam),
            ),
        }
        for name, (app, real) in cases.items():
            with self.subTest(name):
                self.assertIsNone(sp.observe_app_steam_view(app, real))

    def test_it_stays_quiet_when_there_is_nothing_sound_to_compare(self) -> None:
        cases = {
            # dayz_test_run names a Steam that is not running itself.
            "real_registers_no_live_steam": (_View(_GONE_STEAM), _View(_GONE_STEAM + 1)),
            "real_registers_pid_zero": (_View(_GONE_STEAM), _View(0)),
            # A real-registry read without WMI fell back to this process's own copy.
            "real_registry_unavailable": (
                _View(_GONE_STEAM),
                _View(_LIVE_STEAM, unavailable=sp.REAL_REGISTRY_PYWIN32_MISSING),
            ),
            "real_unreadable": (_View(_GONE_STEAM), _View(None, error=OSError(5, "denied"))),
            "copy_unreadable": (_View(None, error=FileNotFoundError(2, "gone")), _View(_LIVE_STEAM)),
            "copy_pid_not_an_int": (_View(str(_GONE_STEAM)), _View(_LIVE_STEAM)),
        }
        for name, (app, real) in cases.items():
            with self.subTest(name):
                self.assertIsNone(sp.observe_app_steam_view(app, real))

    def test_the_real_side_reads_through_wmi_and_the_app_side_through_winreg(self) -> None:
        made: list[bool] = []

        def provider(*, real_registry: bool = False) -> _View:
            made.append(real_registry)
            return _View(_LIVE_STEAM if real_registry else _GONE_STEAM)

        with patch.object(sp, "WindowsSteamPreflightProvider", side_effect=provider):
            view = sp.observe_app_steam_view()
        self.assertEqual(made, [True, False])
        self.assertEqual(view["app_view_pid"], _GONE_STEAM)


_ADVISORY = {
    "code": sp.STEAM_APP_VIEW_STALE,
    "app_view_pid": _GONE_STEAM,
    "real_pid": _LIVE_STEAM,
    "remediation": sp.REMEDIATION_STEAM_APP_VIEW_STALE,
}


class SessionToolsCarryTheAdvisoryTest(unittest.IsolatedAsyncioTestCase):
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
        cache = patch.dict(server._steam_app_view_cache, {"expires": 0.0, "value": None})
        cache.start()
        self.addCleanup(cache.stop)

    async def call(self, tool: str, arguments: dict, observed: object) -> tuple[dict, object]:
        runtime_result = {"status": "active", "warnings": ["already_here"]}
        with (
            patch.object(self.runtime, tool, new=AsyncMock(return_value=dict(runtime_result))),
            patch.object(server, "observe_app_steam_view", **observed) as observe,
        ):
            result = _content_json(await self.app.call_tool(tool, arguments))
        return result, observe

    async def test_each_session_tool_warns_and_keeps_its_answer(self) -> None:
        for tool, arguments in (
            ("session_status", {}),
            ("session_acquire", {"purpose": "fixture"}),
            ("session_acquire_wait", {"purpose": "fixture"}),
        ):
            with self.subTest(tool):
                server._steam_app_view_cache["expires"] = 0.0
                result, _observe = await self.call(
                    tool, arguments, {"return_value": dict(_ADVISORY)}
                )
                self.assertEqual(result["status"], "active")
                # session_acquire_wait may add tool_registry_stale_reopen_client in between.
                self.assertEqual(result["warnings"][0], "already_here")
                self.assertEqual(result["warnings"][-1], sp.STEAM_APP_VIEW_STALE)
                self.assertEqual(result["steam_app_view"], _ADVISORY)

    async def test_a_quiet_or_failed_observation_adds_nothing(self) -> None:
        for name, observed in (
            ("quiet", {"return_value": None}),
            ("failed", {"side_effect": RuntimeError("fixture observe")}),
        ):
            with self.subTest(name):
                server._steam_app_view_cache["expires"] = 0.0
                result, _observe = await self.call("session_status", {}, observed)
                self.assertEqual(result["status"], "active")
                self.assertNotIn("steam_app_view", result)
                self.assertNotIn(sp.STEAM_APP_VIEW_STALE, result["warnings"])

    async def test_one_observation_serves_the_calls_inside_its_ttl(self) -> None:
        observed = {"return_value": dict(_ADVISORY)}
        _first, observe = await self.call("session_status", {}, observed)
        observe.assert_called_once_with()
        second, observe = await self.call("session_status", {}, observed)
        observe.assert_not_called()
        self.assertEqual(second["steam_app_view"], _ADVISORY)
        server._steam_app_view_cache["expires"] = 0.0
        _third, observe = await self.call("session_status", {}, observed)
        observe.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
