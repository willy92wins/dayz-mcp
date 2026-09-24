"""fb-20260924-011620-678b: a launch with Steam closed stops before DayZ and says so.

Incident (2026-09-24 01:02-01:13 UTC): dayz_test_run was refused with
steam_session_stale while steam.exe was alive, and a later launch passed the
Steam gate but the client stopped at DayZ's own "Steam not running" error. The
gate had judged one copy of HKCU\\Software\\Valve\\Steam\\ActiveProcess (the real
one, read through WMI by uncommitted working-tree code) while the client, a
Popen child of a daemon running inside the Claude app's registry
virtualization, read another (the app's private copy, pid 0).

Two contracts are pinned here:

* Steam closed is a named refusal. No steam.exe process at all is
  ``steam_not_running`` with a remediation that names the cause and the next
  step, raised by the stdio gate before the sealed launcher starts anything.
  Steam alive with an invalid registration stays ``steam_session_stale``;
  an unreadable host stays ``steam_session_stale`` too (unknown is not "closed").
* The daemon-side gate (final_check, and the helper's provider) reads the
  registry through the process's own view, the copy a Popen-launched DayZ
  client inherits. Judging any other copy lets the gate and the client disagree.
"""
from __future__ import annotations

from contextlib import contextmanager
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import mcp_capture
from dayz_mcp import dayz_test_tool as tool
from dayz_mcp import steam_launch_guard, steam_prepare_helper
from dayz_mcp import steam_preflight as steam
from dayz_mcp.steam_preflight import evaluate_steam_session
from tests import test_dayz_test_tool as fixtures
from tests.test_steam_preflight import (
    FakeSteamProvider,
    _MutableSteamProvider,
    _STEAM_EXE,
    active_process,
)


STEAM_NOT_RUNNING = "steam_not_running"


class SteamNotRunningEvaluationTests(unittest.TestCase):
    """evaluate_steam_session names a closed Steam; other refusals keep their token."""

    def assert_not_running(self, result: object, registered_pid: object) -> None:
        self.assertEqual(result.error_code, STEAM_NOT_RUNNING)
        self.assertEqual(result.steam_registered_pid, registered_pid)
        self.assertEqual(result.steam_live_pids, ())
        # The next step, in words an agent can act on: what is wrong and what to do.
        self.assertIn("Steam is not running", result.remediation)
        self.assertIn("Start Steam", result.remediation)
        self.assertIn("auto_remediate_steam=true", result.remediation)

    def test_clean_exit_registration_and_no_steam_process_is_steam_not_running(self) -> None:
        # Steam writes pid=0 and ActiveUser=0 when it exits; no steam.exe is left.
        provider = FakeSteamProvider([active_process(0, 0), active_process(0, 0)])

        self.assert_not_running(evaluate_steam_session(provider), None)

    def test_registration_left_by_a_dead_steam_is_steam_not_running(self) -> None:
        # A crash leaves the old pid behind; that process no longer exists.
        provider = FakeSteamProvider([active_process(41, 7), active_process(41, 7)])

        self.assert_not_running(evaluate_steam_session(provider), 41)

    def test_registered_pid_reused_by_another_program_is_steam_not_running(self) -> None:
        provider = FakeSteamProvider(
            [active_process(41, 7), active_process(41, 7)],
            existing={41},
            images={41: r"C:\Windows\notepad.exe"},
        )

        self.assert_not_running(evaluate_steam_session(provider), 41)

    def test_negative_live_registered_steam_still_passes(self) -> None:
        provider = FakeSteamProvider(
            [active_process(41, 7), active_process(41, 7)],
            existing={41},
            images={41: _STEAM_EXE},
            steam_pids=(41,),
        )

        result = evaluate_steam_session(provider)

        self.assertIsNone(result.error_code)
        self.assertEqual(result.remediation, steam.REMEDIATION)

    def test_negative_live_steam_with_invalid_registration_stays_stale(self) -> None:
        # The incident's shape at 01:02 UTC: steam.exe 37580 alive, the copy the
        # gate read held pid=0/ActiveUser=0. Steam IS running: not steam_not_running.
        provider = FakeSteamProvider(
            [active_process(0, 0), active_process(0, 0)],
            existing={37580},
            images={37580: _STEAM_EXE},
            steam_pids=(37580,),
        )

        result = evaluate_steam_session(provider)

        self.assertEqual(result.error_code, steam.STEAM_SESSION_STALE)
        self.assertEqual(result.steam_live_pids, (37580,))
        self.assertEqual(result.remediation, steam.REMEDIATION)

    def test_negative_unreadable_host_is_not_reported_as_closed(self) -> None:
        cases = {
            "process_list_unreadable": FakeSteamProvider(
                [active_process(0, 0), active_process(0, 0)],
                steam_pids=RuntimeError("snapshot failed"),
            ),
            "registry_unreadable": FakeSteamProvider([PermissionError("denied")]),
            "registry_changing": FakeSteamProvider(
                [active_process(41, 7), active_process(42, 7)]
            ),
        }
        for name, provider in cases.items():
            with self.subTest(case=name):
                result = evaluate_steam_session(provider)
                self.assertEqual(result.error_code, steam.STEAM_SESSION_STALE)
                self.assertEqual(result.remediation, steam.REMEDIATION)

    def test_public_result_contract_is_unchanged(self) -> None:
        provider = FakeSteamProvider([active_process(0, 0), active_process(0, 0)])

        result = evaluate_steam_session(provider)

        self.assertIsInstance(result, steam.SteamSessionResult)
        self.assertEqual(
            list(result.__dataclass_fields__),
            ["error_code", "steam_registered_pid", "steam_live_pids", "remediation"],
        )


class SteamNotRunningToolGateTests(unittest.IsolatedAsyncioTestCase):
    """dayz_test_run refuses a closed Steam in validation, before any DayZ process."""

    def setUp(self) -> None:
        self.policy = fixtures._policy()
        self.provider = _MutableSteamProvider()
        self.runtime = fixtures._Runtime({"runs": [{
            "run_id": fixtures.RUN_ID, "state": "RUNNING_IDLE",
            "mod": "@ExampleMod", "processes": [],
        }]})
        self.launch = AsyncMock(side_effect=self._launch)
        self._patch(tool, "evaluate_steam_session",
                    side_effect=lambda: steam.evaluate_steam_session(self.provider))
        self.remediate = self._patch(steam, "remediate_stale_steam_session")
        self._patch(tool, "open_approved_launcher", return_value=fixtures._Opened())
        self._patch(tool.secure_launcher, "load_verified_bundle",
                    return_value=fixtures._Bundle(fixtures._sealed(self.policy)))
        self._patch(tool, "preflight_vpp_request", return_value=SimpleNamespace(
            error_code=None, missing=(), warnings=(), hint=""))
        self._patch(tool.secure_launcher, "execute_secure_launcher_request", new=self.launch)
        self._patch(tool, "evaluate_prerun_desktop", return_value=mcp_capture.PrerunDesktopResult(
            error_code=None, desktop="unlocked", mean_brightness=80.0,
            nonblack_ratio=0.9, waited_s=0.01, remediation=""))

    def _patch(self, owner, name, **kwargs):
        patcher = patch.object(owner, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    async def _launch(self, raw_request, **kwargs):
        await kwargs["execution_started_cb"]()
        kwargs["output_sink"]("stdout", fixtures._terminal({
            "cleanup_degraded": False, "error_code": None,
            "exit_code": 0, "ok": True, "run_id": fixtures.RUN_ID,
        }))
        return 0

    def _close_steam(self) -> None:
        self.provider.shut_down()
        self.provider.pid = 0
        self.provider.active_user = 0

    async def run_tool(self, *, mode: str = "client", **kwargs):
        if mode == "client":
            kwargs.setdefault("run_id", fixtures.RUN_ID)
        return await tool.execute_dayz_test_run(
            self.runtime, project="ExampleMod", mode=mode,
            extra_mods=["@DayZ_MCP"], **kwargs)

    async def test_closed_steam_is_refused_before_any_dayz_process_with_cause_and_next_step(self):
        self._close_steam()
        for mode in ("all", "client"):
            with self.subTest(mode=mode):
                result = await self.run_tool(mode=mode)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["phase"], "validating")
                self.assertEqual(result["error_code"], STEAM_NOT_RUNNING)
                self.assertIsNone(result["run_id"])
                self.assertEqual(result["artifacts_paths"], [])
                self.assertEqual(result["steam_live_pids"], [])
                self.assertIn("Steam is not running", result["remediation"])
                self.assertIn("auto_remediate_steam=true", result["remediation"])
        # Neither the server nor the client was started: the sealed launcher
        # never ran, and Steam was not touched.
        self.launch.assert_not_awaited()
        self.remediate.assert_not_called()

    async def test_negative_steam_running_launch_proceeds(self):
        result = await self.run_tool()

        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["error_code"])
        self.launch.assert_awaited_once()

    async def test_negative_live_steam_with_invalid_registration_keeps_stale_token(self):
        self.provider.pid = 0
        self.provider.active_user = 0

        result = await self.run_tool()

        self.assertEqual(result["error_code"], steam.STEAM_SESSION_STALE)
        self.assertEqual(result["remediation"], steam.REMEDIATION)
        self.assertEqual(result["steam_live_pids"], [41])
        self.launch.assert_not_awaited()

    async def test_negative_opt_in_leaves_a_closed_steam_to_the_daemon(self):
        # auto_remediate_steam=true is the owner's opt-in: the stdio gate does not
        # refuse, the request reaches the sealed launcher, and the daemon's consent
        # path (remediate_stale_steam_session) is the one that may start Steam.
        self._close_steam()

        result = await self.run_tool(auto_remediate_steam=True)

        self.assertNotEqual(result["error_code"], STEAM_NOT_RUNNING)
        self.launch.assert_awaited_once()
        self.remediate.assert_not_called()  # never from stdio


class _InheritedRegistryView:
    """Stands in for ``winreg``: the HKCU copy this process and its children read."""

    HKEY_CURRENT_USER = 0x80000001

    def __init__(self, pid: int, active_user: int) -> None:
        self.values = {"pid": pid, "ActiveUser": active_user}
        self.queried: list[str] = []

    @contextmanager
    def OpenKey(self, root, sub_key, *args):
        yield sub_key

    def QueryValueEx(self, key, name):
        self.queried.append(name)
        return self.values[name], 4  # REG_DWORD


_LIVE_STEAM = 4242
_LIVE_TICKS = 134346854556188144  # a FILETIME after 1970, like any real process


def _host_with_live_steam():
    """Process-side reads of the Windows provider, without touching the host."""
    return patch.multiple(
        steam.WindowsSteamPreflightProvider,
        steam_process_pids=lambda self: (_LIVE_STEAM,),
        process_exists=lambda self, pid: pid == _LIVE_STEAM,
        process_image_path=lambda self, pid: _STEAM_EXE,
        process_creation_ticks=lambda self, pid: _LIVE_TICKS,
    )


class DaemonGateRegistryViewTests(unittest.TestCase):
    """The admission gate judges the registry copy the DayZ client will read."""

    def _prepared(self) -> steam_launch_guard.Preparation:
        return steam_launch_guard.Preparation(
            identity=steam_launch_guard.SteamIdentity(_LIVE_STEAM, _LIVE_TICKS),
            startup="observed",
        )

    def test_final_check_default_provider_reads_the_inherited_view(self) -> None:
        view = _InheritedRegistryView(_LIVE_STEAM, 7)
        with patch.dict(sys.modules, {"winreg": view}), _host_with_live_steam():
            admitted = steam_launch_guard.final_check(self._prepared())

        self.assertTrue(admitted)
        self.assertIn("pid", view.queried)
        self.assertIn("ActiveUser", view.queried)

    def test_final_check_refuses_when_the_inherited_copy_does_not_name_steam(self) -> None:
        # 01:04 UTC shape: steam.exe alive and registered in some other copy, while
        # the copy a Popen-launched client inherits held pid=0/ActiveUser=0. The
        # gate must refuse here; the client would stop at "Steam not running".
        view = _InheritedRegistryView(0, 0)
        with patch.dict(sys.modules, {"winreg": view}), _host_with_live_steam():
            admitted = steam_launch_guard.final_check(self._prepared())

        self.assertFalse(admitted)
        self.assertIn("pid", view.queried)

    def test_helper_provider_reads_the_inherited_view(self) -> None:
        channel = SimpleNamespace(cancel=threading.Event())
        host = steam_prepare_helper.GuardedHost(channel, time.monotonic() + 60.0, False)
        view = _InheritedRegistryView(_LIVE_STEAM, 7)
        with patch.dict(sys.modules, {"winreg": view}), _host_with_live_steam():
            snapshot = host.provider.read_active_process()

        self.assertEqual(snapshot, steam.SteamActiveProcessSnapshot(_LIVE_STEAM, 7))
        self.assertEqual(view.queried, ["pid", "ActiveUser"])


if __name__ == "__main__":
    unittest.main()
