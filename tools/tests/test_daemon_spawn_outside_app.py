"""The daemon is spawned through WMI, outside the client's app container (2026-09-23).

Claude Desktop and Codex are MSIX apps whose children inherit the app's registry
virtualization; a daemon started with Popen inherited it and read a stale private
copy of Steam's ActiveProcess key (daemon._spawn_outside_app). These tests pin the
WMI branch and its fallback to the unchanged Popen branches without starting any
real process: the COM call (_wmi_create_process) and subprocess.Popen are patched.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from dayz_mcp import daemon, server, wmi_host
from dayz_mcp import steam_preflight as sp

_WINDOWS_ONLY = "the WMI branch exists on Windows only"
_ARGV = ["C:/Program Files/venv dir/Scripts/python.exe", "-m", "dayz_mcp", "--daemon", "--port", "8765"]


class _FakePopen:
    def __init__(self, pid: int) -> None:
        self.pid = pid


@unittest.skipUnless(sys.platform == "win32", _WINDOWS_ONLY)
class SpawnOutsideAppTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(daemon.DAEMON_SPAWN_WMI_ENV, None)

    def test_wmi_branch_returns_the_created_pid_without_popen(self) -> None:
        with patch.object(daemon, "_wmi_create_process", return_value=(0, 4321)) as wmi, patch.object(
            daemon.subprocess, "Popen", side_effect=AssertionError("Popen must not run")
        ):
            pid = daemon.spawn_detached(list(_ARGV), cwd="work dir", outside_app=True)

        self.assertEqual(pid, 4321)
        wmi.assert_called_once_with(subprocess.list2cmdline(_ARGV), "work dir")

    def test_no_cwd_is_passed_as_none(self) -> None:
        with patch.object(daemon, "_wmi_create_process", return_value=(0, 7)) as wmi:
            self.assertEqual(daemon.spawn_detached(list(_ARGV), cwd=None, outside_app=True), 7)
        wmi.assert_called_once_with(subprocess.list2cmdline(_ARGV), None)

    def _assert_falls_back(self, **wmi_behaviour) -> None:
        logs: list[str] = []
        with patch.object(daemon, "_wmi_create_process", **wmi_behaviour), patch.object(
            daemon.subprocess, "Popen", return_value=_FakePopen(99)
        ) as popen:
            pid = daemon.spawn_detached(list(_ARGV), log=logs.append, cwd=None, outside_app=True)
        self.assertEqual(pid, 99)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(
            int(popen.call_args.kwargs["creationflags"]),
            daemon._CREATE_NO_WINDOW | daemon._CREATE_NEW_PROCESS_GROUP | daemon._CREATE_BREAKAWAY_FROM_JOB,
        )
        self.assertTrue(any("WMI launch" in line for line in logs), logs)

    def test_wmi_error_code_falls_back_to_popen(self) -> None:
        self._assert_falls_back(return_value=(21, 0))

    def test_zero_pid_falls_back_to_popen(self) -> None:
        self._assert_falls_back(return_value=(0, 0))

    def test_missing_pywin32_falls_back_to_popen(self) -> None:
        self._assert_falls_back(side_effect=ImportError("No module named 'win32com'"))

    def test_com_failure_falls_back_to_popen(self) -> None:
        self._assert_falls_back(side_effect=RuntimeError("com_error -2147217405"))

    def test_kill_switch_skips_wmi(self) -> None:
        os.environ[daemon.DAEMON_SPAWN_WMI_ENV] = "0"
        with patch.object(
            daemon, "_wmi_create_process", side_effect=AssertionError("WMI must not run")
        ), patch.object(daemon.subprocess, "Popen", return_value=_FakePopen(5)):
            self.assertEqual(daemon.spawn_detached(list(_ARGV), cwd=None, outside_app=True), 5)

    def test_default_is_the_plain_popen_spawn(self) -> None:
        with patch.object(
            daemon, "_wmi_create_process", side_effect=AssertionError("WMI must not run")
        ), patch.object(daemon.subprocess, "Popen", return_value=_FakePopen(6)):
            self.assertEqual(daemon.spawn_detached(list(_ARGV), cwd=None), 6)

    def test_exec_create_builds_a_hidden_new_group_create_call(self) -> None:
        """wmi_host._exec_create against a fake COM surface: the in-parameters it sets."""
        recorded: dict[str, object] = {}

        class _Prop:
            def __init__(self, owner: dict, name: str) -> None:
                self._owner, self._name = owner, name

            @property
            def Value(self):  # noqa: N802 - COM casing
                return self._owner.get(self._name)

            @Value.setter
            def Value(self, value):  # noqa: N802
                self._owner[self._name] = value

        class _Props:
            def __init__(self, owner: dict) -> None:
                self._owner = owner

            def Item(self, name: str) -> _Prop:  # noqa: N802
                return _Prop(self._owner, name)

        class _Instance:
            def __init__(self, owner: dict) -> None:
                self.Properties_ = _Props(owner)

        startup_values: dict[str, object] = {}
        param_values: dict[str, object] = {}

        class _Method:
            class InParameters:  # noqa: N801
                @staticmethod
                def SpawnInstance_():  # noqa: N802
                    return _Instance(param_values)

        class _Methods:
            @staticmethod
            def Item(_name: str):  # noqa: N802
                return _Method

        class _ProcessClass:
            Methods_ = _Methods

            @staticmethod
            def ExecMethod_(name: str, params):  # noqa: N802
                recorded["method"] = name
                return _Instance({"ReturnValue": 0, "ProcessId": 3141})

        class _StartupClass:
            @staticmethod
            def SpawnInstance_():  # noqa: N802
                return _Instance(startup_values)

        class _Wmi:
            @staticmethod
            def Get(name: str):  # noqa: N802
                return _StartupClass if name == "Win32_ProcessStartup" else _ProcessClass

        class _Client:
            @staticmethod
            def GetObject(moniker: str):  # noqa: N802
                recorded["moniker"] = moniker
                return _Wmi

        result = wmi_host._exec_create(_Client, "cmd line", "cwd dir")
        self.assertEqual(result, (0, 3141))
        self.assertEqual(recorded["method"], "Create")
        self.assertIn("root\\cimv2", recorded["moniker"])
        self.assertEqual(startup_values, {"ShowWindow": 0, "CreateFlags": daemon._CREATE_NEW_PROCESS_GROUP})
        self.assertEqual(param_values["CommandLine"], "cmd line")
        self.assertEqual(param_values["CurrentDirectory"], "cwd dir")
        self.assertIsInstance(param_values["ProcessStartupInformation"], _Instance)


class _FakeWinreg:
    """winreg double: ActiveProcess holds the stale private copy (pid 0) seen inside the app."""

    HKEY_CURRENT_USER = object()

    def __init__(self, pid: int = 0, active_user: int = 0) -> None:
        self.values = {"pid": pid, "ActiveUser": active_user}
        self.opened = 0

    def OpenKey(self, _root, _path):  # noqa: N802
        self.opened += 1
        registry = self

        class _Key:
            def __enter__(self):
                return registry

            def __exit__(self, *exc):
                return False

        return _Key()

    def QueryValueEx(self, _key, name):  # noqa: N802
        return self.values[name], 4


class RealRegistryProviderTest(unittest.TestCase):
    def _read(self, provider, winreg_double):
        with patch.dict(sys.modules, {"winreg": winreg_double}):
            return provider.read_active_process()

    def test_real_registry_reads_through_wmi_and_skips_winreg(self) -> None:
        stale = _FakeWinreg(0, 0)
        values = {"pid": 15544, "ActiveUser": 35309983}
        with patch.object(sp.wmi_host, "read_hkcu_dword", side_effect=lambda _k, name: values[name]) as wmi:
            snap = self._read(sp.WindowsSteamPreflightProvider(real_registry=True), stale)
        self.assertEqual((snap.pid, snap.active_user), (15544, 35309983))
        self.assertEqual(wmi.call_count, 2)
        self.assertEqual(stale.opened, 0)

    def test_wmi_failure_falls_back_to_winreg(self) -> None:
        with patch.object(sp.wmi_host, "read_hkcu_dword", side_effect=RuntimeError("com_error")):
            snap = self._read(sp.WindowsSteamPreflightProvider(real_registry=True), _FakeWinreg(77, 5))
        self.assertEqual((snap.pid, snap.active_user), (77, 5))

    def test_value_missing_in_wmi_falls_back_to_winreg(self) -> None:
        with patch.object(sp.wmi_host, "read_hkcu_dword", return_value=None):
            snap = self._read(sp.WindowsSteamPreflightProvider(real_registry=True), _FakeWinreg(78, 6))
        self.assertEqual((snap.pid, snap.active_user), (78, 6))

    def test_default_provider_keeps_winreg(self) -> None:
        with patch.object(sp.wmi_host, "read_hkcu_dword", side_effect=AssertionError("WMI must not run")):
            snap = self._read(sp.WindowsSteamPreflightProvider(), _FakeWinreg(79, 7))
        self.assertEqual((snap.pid, snap.active_user), (79, 7))

    def test_production_defaults_ask_for_the_real_registry(self) -> None:
        seen: list[dict] = []

        class _Provider:
            def __init__(self, **kwargs):
                seen.append(kwargs)

            def read_active_process(self):
                return sp.SteamActiveProcessSnapshot(pid=0, active_user=0)

            def steam_process_pids(self):
                return ()

        with patch.object(sp, "WindowsSteamPreflightProvider", _Provider):
            result = sp.evaluate_steam_session()
        # An empty process list is steam_not_running since fb-20260924-011620-678b.
        self.assertEqual(result.error_code, sp.STEAM_NOT_RUNNING)
        self.assertTrue(seen and all(kw == {"real_registry": True} for kw in seen), seen)


class ClientSpawnAsksForOutsideAppTest(unittest.TestCase):
    def test_default_spawn_passes_outside_app(self) -> None:
        class _FakeClient:
            _daemon_argv = tuple(_ARGV)
            _daemon_cwd = "cwd"

            @staticmethod
            def _log(_line: str) -> None:
                return None

        with patch.object(daemon, "spawn_detached", return_value=123) as spawn:
            pid = server.ClientRuntime._default_spawn(_FakeClient())
        self.assertEqual(pid, 123)
        self.assertEqual(spawn.call_args.args[0], list(_ARGV))
        self.assertEqual(spawn.call_args.kwargs["cwd"], "cwd")
        self.assertIs(spawn.call_args.kwargs["outside_app"], True)


if __name__ == "__main__":
    unittest.main()
