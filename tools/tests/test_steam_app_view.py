"""steam_app_view_stale: this app's copy of Steam's ActiveProcess against the real one.

2026-10-16: an agent's own launch of DayZ, from a shell inside the Claude app,
read the app's private copy of ActiveProcess (a Steam gone for ten days) and
stopped at "Unable to locate a running instance of Steam", while dayz_test_run
kept launching. observe_app_steam_view names that state; session_acquire,
session_acquire_wait and session_status carry it as a warning and refuse nothing.
The observation reads WMI, which has no timeout, so the tools wait for it a
bounded time, outside the tool lock, and never run two at once (Codex review
of #239).
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import unittest
from contextlib import nullcontext
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from dayz_mcp import steam_preflight as sp
from dayz_mcp.server import ServerConfig, build_app
from tests._tiers import slow_test
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json

_LIVE_STEAM = 277748
_GONE_STEAM = 67468
_STEAM_EXE = r"C:\Program Files (x86)\Steam\steam.exe"
_PROCESSES = {_LIVE_STEAM: _STEAM_EXE, 4242: r"C:\Windows\notepad.exe"}


class _View:
    """One registry view of ActiveProcess over the host's process list.

    ``pid`` is what every read returns, or a list read in order (the key
    changing between reads); an exception in its place is raised.
    """

    def __init__(
        self,
        pid: object,
        *,
        processes: dict[int, str] | BaseException = _PROCESSES,
        unavailable: str | None = None,
    ) -> None:
        self.reads = list(pid) if isinstance(pid, list) else None
        self.pid = pid
        self.processes = processes
        self.real_registry_unavailable = unavailable

    def read_active_process(self) -> sp.SteamActiveProcessSnapshot:
        pid = self.reads.pop(0) if self.reads is not None else self.pid
        if isinstance(pid, BaseException):
            raise pid
        return sp.SteamActiveProcessSnapshot(pid=pid, active_user=1)

    def steam_process_pids(self) -> tuple[int, ...]:
        if isinstance(self.processes, BaseException):
            raise self.processes
        return tuple(
            pid
            for pid, image in self.processes.items()
            if image.rsplit("\\", 1)[-1].casefold() == "steam.exe"
        )


class ObserveAppSteamViewTest(unittest.TestCase):
    def test_a_copy_that_does_not_register_the_running_steam_is_named(self) -> None:
        cases = {
            "pid_of_a_steam_that_is_gone": (_GONE_STEAM, _GONE_STEAM),
            # 2026-09-23: pid 0 inside the Claude app, the live Steam outside.
            "pid_zero": (0, 0),
            "pid_now_another_program": (4242, 4242),
            # A tombstone in the copy hides the real key (LL-516).
            "key_absent": (FileNotFoundError(2, "absent"), None),
            "pid_written_as_text": (str(_LIVE_STEAM), None),
            # Not truncated to a DWORD, so not the live Steam's pid.
            "pid_wider_than_a_dword": (2**32 + _LIVE_STEAM, 2**32 + _LIVE_STEAM),
        }
        for name, (copy, app_view_pid) in cases.items():
            with self.subTest(name):
                self.assertEqual(
                    sp.observe_app_steam_view(_View(copy), _View(_LIVE_STEAM)),
                    {
                        "code": sp.STEAM_APP_VIEW_STALE,
                        "app_view_pid": app_view_pid,
                        "real_pid": _LIVE_STEAM,
                        "remediation": sp.REMEDIATION_STEAM_APP_VIEW_STALE,
                    },
                )

    def test_it_stays_quiet_when_a_dayz_from_this_app_would_find_steam(self) -> None:
        other_steam = {**_PROCESSES, 5150: _STEAM_EXE}
        cases = {
            "views_agree": (_View(_LIVE_STEAM), _View(_LIVE_STEAM)),
            "copy_names_another_running_steam": (
                _View(5150, processes=other_steam),
                _View(_LIVE_STEAM, processes=other_steam),
            ),
        }
        for name, (app, real) in cases.items():
            with self.subTest(name):
                self.assertIsNone(sp.observe_app_steam_view(app, real))

    def test_it_stays_quiet_when_there_is_nothing_sound_to_compare(self) -> None:
        denied = OSError(5, "denied")
        cases = {
            # dayz_test_run names a Steam that is not running itself.
            "real_registers_no_running_steam": (_View(_GONE_STEAM), _View(_GONE_STEAM + 1)),
            "real_registers_pid_zero": (_View(_GONE_STEAM), _View(0)),
            "real_key_absent": (_View(_GONE_STEAM), _View(FileNotFoundError(2, "absent"))),
            "real_key_moving": (_View(_GONE_STEAM), _View([_LIVE_STEAM, 0])),
            # A real-registry read without WMI fell back to this process's own copy.
            "real_registry_unavailable": (
                _View(_GONE_STEAM),
                _View(_LIVE_STEAM, unavailable=sp.REAL_REGISTRY_PYWIN32_MISSING),
            ),
            "real_unreadable": (_View(_GONE_STEAM), _View(denied)),
            "copy_unreadable": (_View(PermissionError(5, "denied")), _View(_LIVE_STEAM)),
            # Steam restarting writes 0, then its new pid, to the one key both
            # views read when there is no copy.
            "copy_changed_meanwhile": (_View([0, _LIVE_STEAM]), _View(_LIVE_STEAM)),
            "process_list_unreadable": (
                _View(_GONE_STEAM, processes=denied),
                _View(_LIVE_STEAM, processes=denied),
            ),
        }
        for name, (app, real) in cases.items():
            with self.subTest(name):
                self.assertIsNone(sp.observe_app_steam_view(app, real))


class _FakeWinreg:
    """winreg as this process sees it: ActiveProcess holds ``pid``, or is absent."""

    HKEY_CURRENT_USER = object()

    def __init__(self, pid: int | None) -> None:
        self.pid = pid

    def OpenKey(self, _root, _path):  # noqa: N802
        if self.pid is None:
            raise FileNotFoundError(2, "absent")
        return nullcontext(self)

    def QueryValueEx(self, _key, name):  # noqa: N802
        return (self.pid if name == "pid" else 1), 4


class DefaultProvidersTest(unittest.TestCase):
    """observe_app_steam_view() reads the real side through WMI and its own through winreg."""

    def observe(self, wmi: dict, copy_pid: int | None) -> dict | None:
        with (
            patch.object(sp.wmi_host, "read_hkcu_dword", **wmi),
            patch.dict(sys.modules, {"winreg": _FakeWinreg(copy_pid)}),
            patch.object(
                sp.WindowsSteamPreflightProvider,
                "steam_process_pids",
                return_value=(_LIVE_STEAM,),
            ),
        ):
            return sp.observe_app_steam_view()

    def test_wmi_reads_the_running_steam_and_winreg_the_copy(self) -> None:
        for copy_pid, app_view_pid in ((_GONE_STEAM, _GONE_STEAM), (None, None)):
            with self.subTest(copy_pid=copy_pid):
                view = self.observe({"return_value": _LIVE_STEAM}, copy_pid)
                self.assertEqual(
                    (view["app_view_pid"], view["real_pid"]), (app_view_pid, _LIVE_STEAM)
                )

    def test_a_real_read_that_fell_back_to_winreg_compares_nothing(self) -> None:
        cases = {
            "pywin32_missing": {"side_effect": ImportError("pythoncom")},
            "wmi_error": {"side_effect": RuntimeError("com_error")},
            # Not in the real HKCU: the provider reads winreg, this very copy.
            "real_value_absent": {"return_value": None},
        }
        for name, wmi in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.observe(wmi, _GONE_STEAM))


_ADVISORY = {
    "code": sp.STEAM_APP_VIEW_STALE,
    "app_view_pid": _GONE_STEAM,
    "real_pid": _LIVE_STEAM,
    "remediation": sp.REMEDIATION_STEAM_APP_VIEW_STALE,
}
_TOOLS = {
    "session_status": {},
    "session_acquire": {"purpose": "fixture"},
    "session_acquire_wait": {"purpose": "fixture"},
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
        for tool in _TOOLS:
            answer = AsyncMock(
                side_effect=lambda *_a, **_k: {"status": "active", "warnings": ["already_here"]}
            )
            self.enter(patch.object(self.runtime, tool, new=answer))
        self.enter(
            patch.dict(
                server._steam_app_view_cache,
                {"expires": 0.0, "value": None, "pending": None, "started": 0.0},
            )
        )

    def enter(self, patcher) -> None:
        patcher.start()
        self.addCleanup(patcher.stop)

    async def call(self, tool: str) -> dict:
        return _content_json(await self.app.call_tool(tool, _TOOLS[tool]))

    def observed(self, **behaviour):
        return patch.object(server, "observe_app_steam_view", **behaviour)

    def expire(self) -> None:
        server._steam_app_view_cache["expires"] = 0.0

    async def test_each_session_tool_warns_and_keeps_its_answer(self) -> None:
        for tool in _TOOLS:
            with self.subTest(tool), self.observed(return_value=dict(_ADVISORY)):
                self.expire()
                result = await self.call(tool)
                self.assertEqual(result["status"], "active")
                # session_acquire_wait may add tool_registry_stale_reopen_client in between.
                self.assertEqual(result["warnings"][0], "already_here")
                self.assertEqual(result["warnings"][-1], sp.STEAM_APP_VIEW_STALE)
                self.assertEqual(result["steam_app_view"], _ADVISORY)

    async def test_the_observation_runs_outside_the_tool_lock(self) -> None:
        held: list[bool] = []

        def observe() -> dict:
            held.append(self.runtime.tool_lock.locked())
            return dict(_ADVISORY)

        for tool in _TOOLS:
            with self.subTest(tool), self.observed(side_effect=observe):
                held.clear()
                self.expire()
                await self.call(tool)
                self.assertEqual(held, [False])

    async def test_a_quiet_or_failed_observation_adds_nothing_and_is_kept(self) -> None:
        for name, behaviour in (
            ("quiet", {"return_value": None}),
            ("failed", {"side_effect": RuntimeError("fixture observe")}),
        ):
            with self.subTest(name), self.observed(**behaviour) as observe:
                self.expire()
                for _call in range(2):
                    result = await self.call("session_status")
                    self.assertEqual(result["status"], "active")
                    self.assertNotIn("steam_app_view", result)
                    self.assertNotIn(sp.STEAM_APP_VIEW_STALE, result["warnings"])
                observe.assert_called_once_with()

    async def test_one_observation_serves_the_calls_inside_its_ttl(self) -> None:
        with self.observed(return_value=dict(_ADVISORY)) as observe:
            await self.call("session_status")
            second = await self.call("session_status")
            self.assertEqual(second["steam_app_view"], _ADVISORY)
            observe.assert_called_once_with()
            self.expire()
            await self.call("session_status")
            self.assertEqual(observe.call_count, 2)

    @slow_test
    async def test_a_hung_observation_holds_no_tool_and_is_not_started_again(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def hung() -> dict:
            release.wait(5)
            return dict(_ADVISORY)

        with (
            patch.object(server, "_STEAM_APP_VIEW_WAIT_S", 0.2),
            self.observed(side_effect=hung) as observe,
        ):
            began = time.monotonic()
            first = await self.call("session_status")
            self.assertLess(time.monotonic() - began, 2.0)
            second = await self.call("session_acquire")
            for result in (first, second):
                self.assertEqual(result["status"], "active")
                self.assertNotIn("steam_app_view", result)
            observe.assert_called_once_with()
            # When the query comes back, its answer serves the next calls.
            pending = server._steam_app_view_cache["pending"]
            release.set()
            await asyncio.wait_for(asyncio.wrap_future(pending), 5)
            third = await self.call("session_status")
            self.assertEqual(third["steam_app_view"], _ADVISORY)
            observe.assert_called_once_with()

    @slow_test
    async def test_concurrent_calls_share_one_observation(self) -> None:
        go = threading.Event()
        self.addCleanup(go.set)

        def slow() -> dict:
            go.wait(5)
            return dict(_ADVISORY)

        with (
            patch.object(server, "_STEAM_APP_VIEW_WAIT_S", 10.0),
            self.observed(side_effect=slow) as observe,
        ):
            calls = [asyncio.ensure_future(self.call(tool)) for tool in _TOOLS]
            deadline = time.monotonic() + 5
            while observe.call_count == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            go.set()
            results = await asyncio.gather(*calls)
        observe.assert_called_once_with()
        for result in results:
            self.assertEqual(result["steam_app_view"], _ADVISORY)


if __name__ == "__main__":
    unittest.main()
