"""dayz_test_run reports the session's idle time and the display timeout (d490).

fb-20261002-043444-d490: every capture of an unattended run read
frame_client_all_black. The user had been idle past the 300 s display
timeout, the session was not locked, and the desktop gate let the run start.
The two numbers are a report: they refuse nothing and keep nothing awake.

No test calls a Win32 function: the Windows read runs on fake bindings, and
the dayz_test_run wiring patches evaluate_display_idle.
"""
from __future__ import annotations

import ctypes
import unittest
import uuid
from pathlib import PureWindowsPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import mcp_capture
from dayz_mcp import dayz_test_tool, server_cli, steam_preflight
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)

# The GUIDs Microsoft documents for the display-off timeout (powercfg VIDEOIDLE).
VIDEO_SUBGROUP = "7516b95f-f776-4464-8c53-06167f40cc99"
VIDEO_POWERDOWN_TIMEOUT = "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e"
SCHEME = uuid.UUID("381b4222-f694-41f0-9685-ff5bb260df2e")
AC_TIMEOUT_S = 300
DC_TIMEOUT_S = 120


class _Call:
    """One bound Win32 function: records its arguments, answers ``answer``."""

    def __init__(self, name: str, log: list[str], answer) -> None:
        self.argtypes = None
        self.restype = None
        self.name = name
        self.log = log
        self.answer = answer
        self.args: list[tuple] = []

    def __call__(self, *args):
        self.log.append(self.name)
        self.args.append(args)
        return self.answer(*args) if callable(self.answer) else self.answer


def _raises(*_args):
    raise OSError("the call raised")


def _value(seconds: int):
    """A PowerRead*ValueIndex that answers ``seconds``."""

    def read(_root, _scheme, _subgroup, _setting, ref) -> int:
        ref._obj.value = seconds
        return 0

    return read


class _Host:
    """user32, kernel32 and powrprof as the read binds them, no Win32 behind.

    The last input was at tick 0xFFFFFF00 and the tick count is now 1000, so
    the idle time crosses the 32-bit wrap: 1.256 s.
    """

    def __init__(self, line_status: int = 1) -> None:
        self.log: list[str] = []
        self.sizes: list[tuple[int, int]] = []
        self.line_status = line_status
        self.scheme = (ctypes.c_ubyte * 16).from_buffer_copy(SCHEME.bytes_le)

        def call(name: str, answer) -> _Call:
            return _Call(name, self.log, answer)

        self.user32 = SimpleNamespace(
            GetLastInputInfo=call("GetLastInputInfo", self._last_input),
        )
        self.kernel32 = SimpleNamespace(
            GetTickCount=call("GetTickCount", 1000),
            GetSystemPowerStatus=call("GetSystemPowerStatus", self._power_status),
            LocalFree=call("LocalFree", None),
        )
        self.powrprof = SimpleNamespace(
            PowerGetActiveScheme=call("PowerGetActiveScheme", self._active_scheme),
            PowerReadACValueIndex=call("PowerReadACValueIndex", _value(AC_TIMEOUT_S)),
            PowerReadDCValueIndex=call("PowerReadDCValueIndex", _value(DC_TIMEOUT_S)),
        )

    def _last_input(self, ref) -> int:
        self.sizes.append((ref._obj.cbSize, ctypes.sizeof(ref._obj)))
        ref._obj.dwTime = 0xFFFFFF00
        return 1

    def _power_status(self, ref) -> int:
        ref._obj.ACLineStatus = self.line_status
        return 1

    def _active_scheme(self, _root, ref) -> int:
        ref._obj.contents = self.scheme
        return 0


class ReadDisplayIdleTest(unittest.TestCase):
    def read(self, host: _Host) -> mcp_capture.DisplayIdle:
        libraries = {
            "user32": host.user32,
            "kernel32": host.kernel32,
            "powrprof": host.powrprof,
        }
        with patch.object(mcp_capture.sys, "platform", "win32"), patch(
            "ctypes.WinDLL",
            create=True,
            side_effect=lambda name, **_kwargs: libraries[name],
        ) as windll:
            result = mcp_capture.read_display_idle()
        self.assertEqual(
            {made.args for made in windll.call_args_list},
            {("user32",), ("kernel32",), ("powrprof",)},
        )
        for made in windll.call_args_list:
            self.assertEqual(made.kwargs, {"use_last_error": True})
        return result

    def assert_timeout_read_with(self, host: _Host, read: _Call) -> None:
        ((root, scheme, subgroup, setting, _value_ref),) = read.args
        self.assertIsNone(root)
        self.assertEqual(
            ctypes.addressof(scheme.contents), ctypes.addressof(host.scheme)
        )
        self.assertEqual(
            str(uuid.UUID(bytes_le=bytes(subgroup._obj))), VIDEO_SUBGROUP
        )
        self.assertEqual(
            str(uuid.UUID(bytes_le=bytes(setting._obj))), VIDEO_POWERDOWN_TIMEOUT
        )
        # The scheme PowerGetActiveScheme allocated is freed once, after the read.
        ((freed,),) = host.kernel32.LocalFree.args
        self.assertEqual(
            ctypes.addressof(freed.contents), ctypes.addressof(host.scheme)
        )
        self.assertEqual(
            host.log,
            [
                "GetLastInputInfo",
                "GetTickCount",
                "GetSystemPowerStatus",
                "PowerGetActiveScheme",
                read.name,
                "LocalFree",
            ],
        )

    def test_off_windows_both_are_none_and_nothing_is_bound(self) -> None:
        with patch.object(mcp_capture.sys, "platform", "linux"), patch(
            "ctypes.WinDLL", create=True
        ) as windll:
            self.assertEqual(
                mcp_capture.read_display_idle(), mcp_capture.DisplayIdle(None, None)
            )
        windll.assert_not_called()

    def test_idle_s_counts_32_bit_ticks_across_the_wrap(self) -> None:
        self.assertEqual(mcp_capture._idle_s(595_500, 1_500), 594.0)
        self.assertEqual(mcp_capture._idle_s(1000, 0xFFFFFF00), 1.256)
        self.assertEqual(mcp_capture._idle_s(1_234_567, 1), 1234.566)

    def test_on_ac_or_unknown_power_it_reads_the_ac_timeout(self) -> None:
        for line_status in (1, 255):
            with self.subTest(ACLineStatus=line_status):
                host = _Host(line_status)
                self.assertEqual(
                    self.read(host), mcp_capture.DisplayIdle(1.256, AC_TIMEOUT_S)
                )
                self.assert_timeout_read_with(
                    host, host.powrprof.PowerReadACValueIndex
                )
                self.assertEqual(host.powrprof.PowerReadDCValueIndex.args, [])
                # A LASTINPUTINFO whose cbSize is set.
                self.assertEqual(host.sizes, [(8, 8)])

    def test_on_battery_it_reads_the_dc_timeout(self) -> None:
        host = _Host(line_status=0)
        self.assertEqual(
            self.read(host), mcp_capture.DisplayIdle(1.256, DC_TIMEOUT_S)
        )
        self.assert_timeout_read_with(host, host.powrprof.PowerReadDCValueIndex)
        self.assertEqual(host.powrprof.PowerReadACValueIndex.args, [])

    def test_a_failed_power_status_call_reads_the_ac_timeout(self) -> None:
        # The structure a failed call leaves behind reads ACLineStatus 0.
        host = _Host(line_status=0)
        host.kernel32.GetSystemPowerStatus.answer = 0
        self.assertEqual(
            self.read(host), mcp_capture.DisplayIdle(1.256, AC_TIMEOUT_S)
        )
        self.assert_timeout_read_with(host, host.powrprof.PowerReadACValueIndex)

    def test_a_timeout_of_zero_is_never_not_unread(self) -> None:
        host = _Host()
        host.powrprof.PowerReadACValueIndex.answer = _value(0)
        self.assertEqual(self.read(host), mcp_capture.DisplayIdle(1.256, 0))

    def test_a_failed_input_read_leaves_the_timeout_as_read(self) -> None:
        for label, answer in (("returns 0", 0), ("raises", _raises)):
            with self.subTest(label):
                host = _Host()
                host.user32.GetLastInputInfo.answer = answer
                self.assertEqual(
                    self.read(host), mcp_capture.DisplayIdle(None, AC_TIMEOUT_S)
                )
                self.assertEqual(host.kernel32.GetTickCount.args, [])

    def test_a_failed_timeout_read_leaves_the_idle_time_as_read(self) -> None:
        for label, name, answer, freed in (
            ("no active scheme", "PowerGetActiveScheme", 5, 0),
            ("scheme raises", "PowerGetActiveScheme", _raises, 0),
            ("read error", "PowerReadACValueIndex", 2, 1),
            ("read raises", "PowerReadACValueIndex", _raises, 1),
        ):
            with self.subTest(label):
                host = _Host()
                getattr(host.powrprof, name).answer = answer
                self.assertEqual(
                    self.read(host), mcp_capture.DisplayIdle(1.256, None)
                )
                # A scheme Windows allocated is freed; one it never gave is not.
                self.assertEqual(len(host.kernel32.LocalFree.args), freed)

    def test_every_function_is_bound_with_argtypes_and_restype(self) -> None:
        host = _Host()
        self.read(host)
        bound = {
            name: (function.argtypes, function.restype)
            for library in (host.user32, host.kernel32, host.powrprof)
            for name, function in vars(library).items()
        }
        info_p = bound["GetLastInputInfo"][0][0]
        status_p = bound["GetSystemPowerStatus"][0][0]
        guid_p = ctypes.POINTER(ctypes.c_ubyte * 16)
        dword_p = ctypes.POINTER(ctypes.c_uint)
        read = ([ctypes.c_void_p, guid_p, guid_p, guid_p, dword_p], ctypes.c_uint)
        self.assertEqual(
            bound,
            {
                "GetLastInputInfo": ([info_p], ctypes.c_int),
                "GetTickCount": ([], ctypes.c_uint),
                "GetSystemPowerStatus": ([status_p], ctypes.c_int),
                "LocalFree": ([ctypes.c_void_p], ctypes.c_void_p),
                "PowerGetActiveScheme": (
                    [ctypes.c_void_p, ctypes.POINTER(guid_p)],
                    ctypes.c_uint,
                ),
                "PowerReadACValueIndex": read,
                "PowerReadDCValueIndex": read,
            },
        )
        # Pointers to LASTINPUTINFO and SYSTEM_POWER_STATUS.
        self.assertIs(info_p, ctypes.POINTER(info_p._type_))
        self.assertEqual(
            info_p._type_._fields_,
            [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)],
        )
        self.assertIs(status_p, ctypes.POINTER(status_p._type_))
        self.assertEqual(
            status_p._type_._fields_,
            [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_uint),
                ("BatteryFullLifeTime", ctypes.c_uint),
            ],
        )
        self.assertEqual(ctypes.sizeof(status_p._type_), 12)

    def test_evaluate_display_idle_is_the_capture_read(self) -> None:
        read = mcp_capture.DisplayIdle(594.0, AC_TIMEOUT_S)
        with patch.object(
            mcp_capture, "read_display_idle", return_value=read
        ) as call:
            self.assertIs(dayz_test_tool.evaluate_display_idle(), read)
        call.assert_called_once_with()


_IDLE = mcp_capture.DisplayIdle(594.0, AC_TIMEOUT_S)


def _desktop(error_code: str | None = None) -> mcp_capture.PrerunDesktopResult:
    black = error_code is not None
    return mcp_capture.PrerunDesktopResult(
        error_code=error_code,
        desktop="unlocked",
        mean_brightness=0.0 if black else 80.0,
        nonblack_ratio=0.0 if black else 0.9,
        waited_s=0.01,
        remediation=mcp_capture.REMEDIATION_DESKTOP_ALL_BLACK if black else "",
    )


def _steam(error_code: str | None = None) -> steam_preflight.SteamSessionResult:
    return steam_preflight.SteamSessionResult(
        error_code=error_code,
        steam_registered_pid=1,
        steam_live_pids=(1,),
        remediation="ok" if error_code is None else steam_preflight.REMEDIATION,
    )


class DisplayIdleWiringTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.launch = AsyncMock(side_effect=self._launch)
        self.desktop = Mock(return_value=_desktop())
        self.display = Mock(return_value=_IDLE)
        self.steam = Mock(return_value=_steam())
        bundle = _Bundle(_sealed(_policy()))
        for owner, name, value in (
            (dayz_test_tool, "open_approved_launcher", Mock(return_value=_Opened())),
            (
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                Mock(return_value=bundle),
            ),
            (
                dayz_test_tool.secure_launcher,
                "execute_secure_launcher_request",
                self.launch,
            ),
            (dayz_test_tool, "evaluate_prerun_desktop", self.desktop),
            (dayz_test_tool, "evaluate_display_idle", self.display),
            (dayz_test_tool, "evaluate_steam_session", self.steam),
            # The sealed request takes a Windows lock root, which the test
            # temp root is not on a POSIX host. A pure path opens nothing.
            (
                server_cli,
                "shared_root",
                Mock(return_value=PureWindowsPath(r"C:\d490")),
            ),
        ):
            patcher = patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _launch(self, _raw_request: bytes, **kwargs: object) -> int:
        await kwargs["execution_started_cb"]()
        kwargs["output_sink"](
            "stdout",
            _terminal(
                {
                    "cleanup_degraded": False,
                    "error_code": None,
                    "exit_code": 0,
                    "ok": True,
                    "run_id": RUN_ID,
                }
            ),
        )
        return 0

    async def run_tool(self, mode: str, **kwargs: object) -> dict[str, object]:
        return await dayz_test_tool.execute_dayz_test_run(
            _Runtime(),
            project="ExampleMod",
            mode=mode,
            extra_mods=["@DayZ_MCP"],
            **kwargs,
        )

    def assertIdle(self, result: dict[str, object], expected: tuple) -> None:
        self.assertEqual(
            (result["session_idle_s"], result["display_timeout_s"]), expected
        )

    async def test_a_client_starting_launch_answers_what_was_read(self) -> None:
        result = await self.run_tool("all")
        self.assertEqual(
            (result["status"], result["run_id"]), ("succeeded", RUN_ID)
        )
        self.assertIdle(result, (594.0, AC_TIMEOUT_S))
        self.desktop.assert_called_once_with()
        self.display.assert_called_once_with()
        self.launch.assert_awaited_once()

    async def test_a_client_mode_preflight_answers_what_was_read(self) -> None:
        for mode, run_id in (("all", None), ("client", RUN_ID)):
            with self.subTest(mode=mode):
                self.display.reset_mock()
                result = await self.run_tool(mode, preflight=True, run_id=run_id)
                self.assertEqual(
                    (result["status"], result["preflight"]), ("succeeded", True)
                )
                self.assertIdle(result, (594.0, AC_TIMEOUT_S))
                self.display.assert_called_once_with()
        self.launch.assert_not_awaited()

    async def test_a_server_only_run_reads_nothing_and_answers_null(self) -> None:
        for preflight in (False, True):
            with self.subTest(preflight=preflight):
                result = await self.run_tool("server", preflight=preflight)
                self.assertEqual(result["status"], "succeeded")
                self.assertIdle(result, (None, None))
        self.desktop.assert_not_called()
        self.display.assert_not_called()

    async def test_a_desktop_gate_refusal_reads_nothing_and_answers_null(
        self,
    ) -> None:
        self.desktop.return_value = _desktop(mcp_capture.DESKTOP_ALL_BLACK)
        for preflight in (False, True):
            with self.subTest(preflight=preflight):
                result = await self.run_tool("all", preflight=preflight)
                self.assertEqual(result["error_code"], mcp_capture.DESKTOP_ALL_BLACK)
                self.assertIdle(result, (None, None))
        self.display.assert_not_called()
        self.launch.assert_not_awaited()

    async def test_a_refusal_after_the_gate_answers_null(self) -> None:
        self.steam.return_value = _steam(steam_preflight.STEAM_SESSION_STALE)
        for preflight in (False, True):
            with self.subTest(preflight=preflight):
                result = await self.run_tool("all", preflight=preflight)
                self.assertEqual(
                    result["error_code"], steam_preflight.STEAM_SESSION_STALE
                )
                self.assertIdle(result, (None, None))
        self.assertEqual(self.display.call_count, 2)
        self.launch.assert_not_awaited()

    def test_every_compact_result_has_both_keys(self) -> None:
        for ok in (True, False):
            with self.subTest(ok=ok):
                terminal = dayz_test_tool.WorkerTerminal(
                    cleanup_degraded=False,
                    error_code=None if ok else "readiness_timeout",
                    exit_code=0 if ok else 2,
                    ok=ok,
                    run_id=RUN_ID if ok else None,
                )
                common = dict(
                    terminal=terminal,
                    project="ExampleMod",
                    mode="all",
                    started_at=0.0,
                    artifacts_paths=[],
                )
                self.assertIdle(dayz_test_tool._compact_result(**common), (None, None))


if __name__ == "__main__":
    unittest.main()
