"""dayz_test_foreground asks Windows once to bring a run's DayZ client forward (inbox 8b1c).

No test calls SetForegroundWindow, GetForegroundWindow or
LockSetForegroundWindow: the windows, the foreground and the lock are fakes.
"""
from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import json
import threading
import unittest
from ctypes import wintypes
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import control_client, server, window_close, window_foreground
from dayz_mcp.foreground_lock import LSFW_LOCK, LSFW_UNLOCK, ForegroundLock
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
    adoption_revert_pending_fields,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import LEASE_TOOL_LINE, ServerConfig, build_app
from dayz_mcp.session_coordination import SessionCoordinator
from tests.client_helpers import _fixture_client_runtime
from tests.lifecycle_helpers import IDENTITY_PAYLOAD
from tests.mcp_helpers import _content_json
from tests.process_lifecycle_helpers import (
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.session_http_helpers import SessionLoopback
from tests.steam_helpers import FakeSteamGate


RUN_ID = "run-front"
# The window the user works in.
_OTHER = 7
_HELD = object()


class _Desktop:
    """Top-level windows and the foreground window, as the verb reads them."""

    def __init__(self) -> None:
        self.windows: dict[int, dict[str, object]] = {}
        self.front = _OTHER
        # What SetForegroundWindow answers, and whether the window comes forward.
        self.grant: object = True
        self.shows = True
        # How many foreground reads find the asked window before another takes it back.
        self.taken_after: int | None = None
        # A pid the window passes to between the listing and the ask.
        self.passes_to: int | None = None
        self.reads = 0
        self.calls: list[str] = []
        self.asks: list[int] = []
        # The lifecycle's operation lock, and whether another thread found it taken.
        self.operation_lock: object | None = None
        self.taken_at_ask: list[bool] = []
        self.taken_at_read: list[bool] = []

    def add(
        self,
        hwnd: int,
        pid: int,
        *,
        cls: str = "DayZ",
        area: int = 1000,
        visible: bool = True,
        iconic: bool = False,
    ) -> None:
        self.windows[hwnd] = {
            "pid": pid,
            "cls": cls,
            "area": area,
            "visible": visible,
            "iconic": iconic,
        }

    def _taken(self) -> bool:
        lock = self.operation_lock
        if lock is None:
            return False
        acquired: list[bool] = []

        def probe() -> None:
            got = lock.acquire(blocking=False)
            if got:
                lock.release()
            acquired.append(got)

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(5)
        return acquired == [False]

    def window_fns(self) -> window_close.Win32WindowFns:
        def enum_windows(callback) -> bool:
            for hwnd in list(self.windows):
                if not callback(hwnd):
                    return False
            return True

        def post_message(*_args) -> bool:
            raise AssertionError("the verb posted a window message")

        return window_close.Win32WindowFns(
            enum_windows=enum_windows,
            window_pid=lambda hwnd: int(self.windows.get(hwnd, {}).get("pid", 0)),
            is_visible=lambda hwnd: bool(self.windows.get(hwnd, {}).get("visible")),
            post_message=post_message,
        )

    def foreground_fns(self) -> window_foreground.Win32ForegroundFns:
        def is_iconic(hwnd: int) -> bool:
            self.calls.append("iconic")
            if self.passes_to is not None:
                self.windows[hwnd]["pid"] = self.passes_to
            return bool(self.windows[hwnd]["iconic"])

        def set_foreground(hwnd: int) -> bool:
            self.calls.append("ask")
            self.asks.append(hwnd)
            self.taken_at_ask.append(self._taken())
            if self.shows:
                self.front = hwnd
            return self.grant  # type: ignore[return-value]

        def foreground() -> int:
            self.reads += 1
            self.taken_at_read.append(self._taken())
            if self.taken_after is not None and self.reads > self.taken_after:
                self.front = _OTHER
            return self.front

        return window_foreground.Win32ForegroundFns(
            class_name=lambda hwnd: str(self.windows[hwnd]["cls"]),
            area=lambda hwnd: int(self.windows[hwnd]["area"]),
            is_iconic=is_iconic,
            set_foreground=set_foreground,
            foreground=foreground,
        )


def _fronts(*answers: int) -> window_foreground.Win32ForegroundFns:
    """GetForegroundWindow answers ``answers`` in turn, then the last one."""

    left = list(answers)

    def foreground() -> int:
        return left.pop(0) if len(left) > 1 else left[0]

    def unused(*_args):
        raise AssertionError("watch only reads the foreground window")

    return window_foreground.Win32ForegroundFns(
        class_name=unused,
        area=unused,
        is_iconic=unused,
        set_foreground=unused,
        foreground=foreground,
    )


def _slow_reads(
    fns: window_foreground.Win32ForegroundFns, clock: _Clock, read_s: float
) -> window_foreground.Win32ForegroundFns:
    """``fns`` whose reads of the foreground window each take ``read_s``."""

    def foreground() -> int:
        answer = fns.foreground()
        clock.now += read_s
        return answer

    return dataclasses.replace(fns, foreground=foreground)


class _Clock:
    """A clock whose sleeps end ``late_s`` late, and whose first readings are
    ``times``, as a thread that stalls between them sees it."""

    def __init__(self, late_s: float = 0.0, times: tuple[float, ...] = ()) -> None:
        self.now = 100.0
        self.late_s = late_s
        self.times = list(times)
        self.slept: list[float] = []

    def __call__(self) -> float:
        if self.times:
            self.now = self.times.pop(0)
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            # As time.sleep does (Codex F6).
            raise ValueError("sleep length must be non-negative")
        self.slept.append(seconds)
        self.now += seconds + self.late_s


class WindowForegroundTest(unittest.TestCase):
    def setUp(self) -> None:
        self.desktop = _Desktop()
        self.windows = self.desktop.window_fns()
        self.fns = self.desktop.foreground_fns()

    def client_window(self, pid: int) -> int | None:
        return window_foreground.client_window(pid, windows=self.windows, fns=self.fns)

    def ask(self, hwnd: int, pid: int) -> bool | None:
        return window_foreground.set_foreground(
            hwnd, pid, windows=self.windows, fns=self.fns
        )

    def test_the_client_window_is_the_largest_visible_dayz_window_of_the_pid(
        self,
    ) -> None:
        self.desktop.add(10, 42, area=100)
        self.desktop.add(11, 42, area=5000)
        self.desktop.add(12, 42, cls="ConsoleWindowClass", area=9000)
        self.desktop.add(13, 42, area=8000, visible=False)
        self.desktop.add(14, 41, area=7000)
        self.assertEqual(self.client_window(42), 11)
        self.assertEqual(self.client_window(41), 14)
        self.assertIsNone(self.client_window(43))
        for hwnd in (10, 11):
            self.desktop.windows[hwnd]["area"] = 0
        self.assertIsNone(self.client_window(42))

    def test_it_asks_only_while_the_window_is_still_the_pids(self) -> None:
        self.desktop.add(11, 42)
        self.assertIs(self.ask(11, 42), True)
        self.desktop.grant = 0
        self.assertIs(self.ask(11, 42), False)
        self.assertEqual(self.desktop.asks, [11, 11])
        self.desktop.windows[11]["pid"] = 43
        self.assertIsNone(self.ask(11, 42))
        del self.desktop.windows[11]
        self.assertIsNone(self.ask(11, 42))
        self.assertEqual(self.desktop.asks, [11, 11])

    def watch(self, fns, hold_s: float, clock: _Clock | None = None) -> tuple[str, float]:
        self.clock = clock if clock is not None else _Clock()
        return window_foreground.watch(
            11, hold_s, fns=fns, clock=self.clock, sleep=self.clock.sleep
        )

    def test_a_window_that_holds_the_front_reads_foreground(self) -> None:
        self.assertEqual(self.watch(_fronts(11), 1.0), ("foreground", 1.0))
        self.assertEqual(self.clock.slept, [0.25] * 4)
        self.assertEqual(self.watch(_fronts(11), 0.0), ("foreground", 0.0))
        self.assertEqual(self.clock.slept, [])

    def test_the_answer_may_take_half_a_second_to_show(self) -> None:
        # Read at 0, 0.25 and 0.5 s: shown at 0.5 s, then held from there.
        self.assertEqual(
            self.watch(_fronts(_OTHER, _OTHER, 11), 0.5), ("foreground", 0.5)
        )
        self.assertEqual(self.clock.slept, [0.25] * 4)
        self.assertEqual(
            self.watch(_fronts(_OTHER, _OTHER, _OTHER, 11), 3.0), ("denied", 0.0)
        )
        self.assertEqual(self.clock.slept, [0.25, 0.25])
        self.assertEqual(self.watch(_fronts(_OTHER), 0.0), ("denied", 0.0))

    def test_a_window_another_takes_back_reads_lost_with_how_long_it_held(
        self,
    ) -> None:
        # Read at 0, 0.25 and 0.5 s, then another window at 0.75 s.
        self.assertEqual(
            self.watch(_fronts(11, 11, 11, _OTHER), 3.0), ("lost", 0.5)
        )
        self.assertEqual(self.clock.slept, [0.25] * 3)

    def test_the_last_read_is_made_when_hold_s_is_up(self) -> None:
        # Codex F1: a window taken back after hold_s is not lost.
        self.assertEqual(
            self.watch(_fronts(11, 11, 11, _OTHER), 0.3), ("foreground", 0.3)
        )
        self.assertEqual([round(s, 6) for s in self.clock.slept], [0.25, 0.05])
        self.assertEqual(self.watch(_fronts(11, 11, _OTHER), 0.3), ("lost", 0.25))

    def test_a_late_thread_reads_late_and_counts_only_what_the_reads_saw(
        self,
    ) -> None:
        # Codex F1: each sleep ends 20 ms late and each read takes 10 ms. The
        # window shows at 100.01, and is read at 100.28 and at 100.33, after
        # hold_s.
        clock = _Clock(late_s=0.02)
        fns = _slow_reads(_fronts(11), clock, 0.01)
        self.assertEqual(self.watch(fns, 0.3, clock), ("foreground", 0.32))
        clock = _Clock(late_s=0.02)
        fns = _slow_reads(_fronts(11, 11, _OTHER), clock, 0.01)
        self.assertEqual(self.watch(fns, 0.3, clock), ("lost", 0.27))

    def test_a_stalled_thread_still_reads_once_hold_s_is_up(self) -> None:
        # Codex F1: shown at 100.0, and the thread runs again at 100.31.
        stalled = (100.0, 100.0, 100.31)
        self.assertEqual(
            self.watch(_fronts(11, _OTHER), 0.3, _Clock(times=stalled)), ("lost", 0.0)
        )
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(
            self.watch(_fronts(11), 0.3, _Clock(times=stalled)), ("foreground", 0.31)
        )

    def test_the_sleep_is_never_negative_when_the_clock_passes_hold_s(self) -> None:
        # Codex F6: read at 100.299, 1 ms before hold_s is up, and at 100.301.
        clock = _Clock(times=(100.0, 100.0, 100.299, 100.301))
        self.assertEqual(self.watch(_fronts(11), 0.3, clock), ("foreground", 0.3))
        self.assertEqual([round(s, 6) for s in clock.slept], [0.001])

    def test_on_windows_it_binds_user32_with_handles_for_arguments(self) -> None:
        class _Call:
            def __init__(self, answer) -> None:
                self.argtypes = None
                self.restype = None
                self.answer = answer
                self.args: list[tuple] = []

            def __call__(self, *args):
                self.args.append(args)
                return self.answer(*args) if callable(self.answer) else self.answer

        def class_name(_hwnd, buffer, _size) -> int:
            buffer.value = "DayZ"
            return 4

        def rect(left: int, top: int, right: int, bottom: int):
            def fill(_hwnd, ref) -> int:
                ref._obj.left, ref._obj.top = left, top
                ref._obj.right, ref._obj.bottom = right, bottom
                return 1

            return fill

        user32 = SimpleNamespace(
            GetClassNameW=_Call(class_name),
            GetWindowRect=_Call(rect(10, 20, 110, 70)),
            IsIconic=_Call(1),
            SetForegroundWindow=_Call(7),
            GetForegroundWindow=_Call(0x51),
        )
        with patch("ctypes.WinDLL", create=True, return_value=user32) as windll:
            bound = window_foreground.bind_real_win32()
        windll.assert_called_once_with("user32", use_last_error=True)
        hwnd = wintypes.HWND
        self.assertEqual(
            {
                name: (getattr(user32, name).argtypes, getattr(user32, name).restype)
                for name in vars(user32)
            },
            {
                "GetClassNameW": ([hwnd, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                "GetWindowRect": ([hwnd, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
                "IsIconic": ([hwnd], wintypes.BOOL),
                "SetForegroundWindow": ([hwnd], wintypes.BOOL),
                "GetForegroundWindow": ([], hwnd),
            },
        )
        self.assertEqual(bound.class_name(5), "DayZ")
        self.assertEqual(bound.area(5), 100 * 50)
        self.assertIs(bound.is_iconic(5), True)
        self.assertIs(bound.set_foreground(5), True)
        self.assertEqual(user32.SetForegroundWindow.args, [(5,)])
        self.assertEqual(bound.foreground(), 0x51)
        # A zero BOOL is a refusal, and a NULL handle (None) no window.
        user32.GetClassNameW.answer = 0
        user32.GetWindowRect.answer = 0
        user32.IsIconic.answer = 0
        user32.SetForegroundWindow.answer = 0
        user32.GetForegroundWindow.answer = None
        self.assertEqual(bound.class_name(5), "")
        self.assertEqual(bound.area(5), 0)
        self.assertIs(bound.is_iconic(5), False)
        self.assertIs(bound.set_foreground(5), False)
        self.assertEqual(bound.foreground(), 0)
        user32.GetWindowRect.answer = rect(110, 20, 10, 70)
        self.assertEqual(bound.area(5), 0)


class _Timer:
    def __init__(self, interval, function, args=()) -> None:
        self.interval = interval
        self.function = function
        self.args = args
        self.daemon = False
        self.cancelled = False

    def start(self) -> None:
        return None

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self.function(*self.args)


class _LockCall:
    def __init__(self) -> None:
        self.codes: list[int] = []
        self.timers: list[_Timer] = []

    def __call__(self, code: int) -> bool:
        self.codes.append(code)
        return True

    def timer(self, interval, function, args=()) -> _Timer:
        timer = _Timer(interval, function, args)
        self.timers.append(timer)
        return timer


class ForegroundLockReleaseTest(unittest.TestCase):
    def test_release_unlocks_now_and_drops_the_pending_release(self) -> None:
        call = _LockCall()
        held = ForegroundLock(call, timer=call.timer)
        held.hold()
        held.release()
        self.assertEqual(call.codes, [LSFW_LOCK, LSFW_UNLOCK])
        (timer,) = call.timers
        self.assertTrue(timer.cancelled)
        # A release timer that fired anyway finds nothing left to unlock.
        timer.fire()
        self.assertEqual(call.codes, [LSFW_LOCK, LSFW_UNLOCK])
        # A later launch schedules its own release again.
        held.hold()
        call.timers[-1].fire()
        self.assertEqual(call.codes, [LSFW_LOCK, LSFW_UNLOCK, LSFW_LOCK, LSFW_UNLOCK])

    def test_release_unlocks_once_without_a_hold_and_nothing_without_the_call(
        self,
    ) -> None:
        call = _LockCall()
        ForegroundLock(call, timer=call.timer).release()
        self.assertEqual(call.codes, [LSFW_UNLOCK])
        self.assertEqual(call.timers, [])
        self.assertIsNone(ForegroundLock(None, timer=call.timer).release())


class _Lock:
    """The daemon's foreground lock: release() is all the verb may call."""

    def __init__(self, desktop: _Desktop) -> None:
        self.desktop = desktop

    def release(self) -> None:
        self.desktop.calls.append("unlock")

    def hold(self) -> bool:
        raise AssertionError("the verb took the lock")


class ForegroundRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        game = root / "DayZ"
        game.mkdir()
        for name in (
            "DayZDiag_x64.exe",
            "DayZ_BE.exe",
            "DayZ_x64.exe",
            "DayZServer_x64.exe",
        ):
            (game / name).write_bytes(b"")
        paths = RuntimePaths(
            root / "runtime",
            root / "runtime" / "audit",
            root / "runtime" / "coordination.json",
            root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=self.audit
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.desktop = _Desktop()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game,
            launcher=FakeLauncher(),
            id_fn=lambda: "run-1",
            foreground_lock=_Lock(self.desktop),
        )
        self.lifecycle.window_fns = self.desktop.window_fns()
        self.lifecycle.foreground_fns = self.desktop.foreground_fns()
        self.desktop.operation_lock = self.lifecycle._operation_lock
        self.server = process(41, role="server")
        self.client = process(42)
        self.desktop.add(410, 41, area=99999)
        self.desktop.add(420, 42, area=5000)
        self.desktop.add(421, 42, cls="ConsoleWindowClass", area=9000)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(
        self,
        *records,
        run_id: str = RUN_ID,
        owner: str | None = "A",
        lease: str | None = "lease-A",
        state: str = "RUNNING",
    ) -> None:
        records = records or (self.server, self.client)
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
        self.store.add(
            RunRecord(
                run_id,
                owner,
                lease,
                state,
                "same",
                "@SameMod",
                "profiles",
                "mission",
                list(records),
            )
        )

    def foreground(
        self, hold_s: object = 0.0, run_id: object = RUN_ID, token: object = _HELD
    ) -> dict[str, object]:
        return self.lifecycle.foreground_run(
            IDENTITY_A, self.token if token is _HELD else token, run_id, hold_s
        )

    def assertRefused(
        self, result: dict[str, object], error: str, status: int
    ) -> None:
        self.assertEqual((result.get("error"), result.get("_http_status")), (error, status))

    def rows(self) -> list[dict[str, object]]:
        return [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_foreground"
        ]

    def in_flight(self) -> list[object]:
        active = self.coordinator._active
        if active is None:
            return []
        return [
            *active.pending_authorizations,
            *active.committed_commands,
            *active.operation_pins,
        ]

    def test_it_asks_once_for_the_largest_dayz_window_of_the_client(self) -> None:
        self.add_run()
        result = self.foreground()
        self.assertEqual(
            result,
            {"run_id": RUN_ID, "state": "foreground", "granted": True, "held_s": 0.0},
        )
        self.assertEqual(self.desktop.asks, [420])
        # The client's identity is read again right before the ask, the lock
        # is let go before it, and the ask runs under the operation lock while
        # the reads of the foreground after it do not.
        self.assertEqual(self.guard.snapshot_calls, [42, 42])
        self.assertEqual(self.desktop.calls, ["iconic", "unlock", "ask"])
        self.assertEqual(self.desktop.taken_at_ask, [True])
        self.assertEqual(self.desktop.taken_at_read, [False])
        (row,) = self.rows()
        self.assertEqual(
            (row["reason"], row["decision"], row["run_id"], row["pid"]),
            ("identity_match", "allowed", RUN_ID, 42),
        )
        self.assertEqual(self.in_flight(), [])

    def test_a_window_another_takes_back_reads_lost(self) -> None:
        self.add_run()
        self.desktop.taken_after = 2
        result = self.foreground(hold_s=3)
        self.assertEqual((result["state"], result["granted"]), ("lost", True))
        self.assertGreater(result["held_s"], 0.0)
        self.assertLess(result["held_s"], 3.0)
        self.assertEqual(self.desktop.asks, [420])

    def test_a_refused_ask_reads_denied(self) -> None:
        self.add_run()
        self.desktop.grant = False
        self.desktop.shows = False
        result = self.foreground(hold_s=3)
        self.assertEqual(
            result,
            {"run_id": RUN_ID, "state": "denied", "granted": False, "held_s": 0.0},
        )
        self.assertEqual(self.desktop.asks, [420])

    def test_hold_s_is_a_number_from_0_to_10(self) -> None:
        self.add_run()
        before = list(self.audit.events)
        for bad in (True, None, "3", [], float("nan"), float("inf"), -0.01, 10.01):
            with self.subTest(hold_s=bad):
                self.assertRefused(self.foreground(hold_s=bad), "bad_args", 400)
        self.assertEqual(self.audit.events, before)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.in_flight(), [])
        # 10 s is the most it reads; here another window takes the front back.
        self.desktop.taken_after = 1
        self.assertEqual(self.foreground(hold_s=10)["state"], "lost")

    def test_without_the_lease_it_asks_nothing(self) -> None:
        self.add_run()
        self.assertRefused(self.foreground(token=None), "lease_required", 423)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.rows(), [])

    def test_an_unknown_run_is_run_not_found(self) -> None:
        self.add_run()
        for run_id in ("run-other", "", None, 7):
            with self.subTest(run_id=run_id):
                self.assertRefused(self.foreground(run_id=run_id), "run_not_found", 404)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.in_flight(), [])

    def test_a_run_this_lease_does_not_own_running_is_run_not_adopted(self) -> None:
        cases = {
            "run-b": {"owner": "B"},
            "run-old-lease": {"lease": "lease-old"},
            "run-idle": {"owner": None, "lease": None, "state": "RUNNING_IDLE"},
            "run-starting": {"state": "STARTING"},
        }
        for run_id, fields in cases.items():
            self.add_run(run_id=run_id, **fields)
            with self.subTest(run_id=run_id):
                self.assertRefused(
                    self.foreground(run_id=run_id), "run_not_adopted", 409
                )
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.in_flight(), [])

    def test_a_run_whose_adoption_revert_is_pending_is_protected(self) -> None:
        self.add_run()
        with patch.object(
            self.lifecycle,
            "_refuse_unconfirmed_owner",
            return_value=adoption_revert_pending_fields(),
        ):
            result = self.foreground()
        self.assertRefused(result, "run_protected", 409)
        self.assertEqual(
            {key: result[key] for key in adoption_revert_pending_fields()},
            adoption_revert_pending_fields(),
        )
        self.assertEqual(self.desktop.calls, [])

    def test_a_retail_dayz_blocks_it_up_to_the_audit_row(self) -> None:
        self.add_run()
        clean = {"known": True, "processes": []}
        retail = {"known": True, "processes": [{"pid": 5}]}
        for answers in ([retail], [clean, retail]):
            with self.subTest(answers=len(answers)):
                left = list(answers)
                self.lifecycle.retail_probe = lambda: left.pop(0) if len(left) > 1 else left[0]
                self.assertRefused(self.foreground(), "retail_quarantine", 409)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.in_flight(), [])

    def test_a_client_that_is_gone_or_another_process_is_client_process_gone(
        self,
    ) -> None:
        self.add_run()
        foreign = dict(snapshot(self.client), creation_time_utc="2099-01-01T00:00:00.0000000Z")
        for label, seen in (
            ("gone", {"error": "process_not_found", "exit_code": 4}),
            ("foreign", foreign),
        ):
            with self.subTest(client=label):
                self.guard.snapshots[42] = seen
                self.assertRefused(self.foreground(), "client_process_gone", 409)
        with self.subTest(client="none"):
            self.add_run(self.server, run_id="run-server-only")
            self.assertRefused(
                self.foreground(run_id="run-server-only"), "client_process_gone", 409
            )
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.in_flight(), [])

    def test_a_client_it_cannot_tell_apart_is_refused_with_the_guards_reason(
        self,
    ) -> None:
        self.add_run()
        incomplete = dict(
            snapshot(self.client),
            creation_time_utc="2099-01-01T00:00:00.0000000Z",
            identity_complete=False,
        )
        for label, seen, error, status in (
            ("guard down", {"error": "guard", "exit_code": 3}, "guard_unavailable", 503),
            ("incomplete", incomplete, "process_identity_mismatch", 409),
        ):
            with self.subTest(client=label):
                self.guard.snapshots[42] = seen
                self.assertRefused(self.foreground(), error, status)
        with self.subTest(client="guard raises"):
            with patch.object(self.guard, "snapshot", side_effect=RuntimeError("guard")):
                self.assertRefused(self.foreground(), "guard_unavailable", 503)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.in_flight(), [])

    def test_a_client_without_a_visible_dayz_window_is_window_not_found(self) -> None:
        self.add_run()
        self.desktop.windows[420]["visible"] = False
        self.assertRefused(self.foreground(), "window_not_found", 404)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.in_flight(), [])

    def test_a_window_that_passes_to_another_process_is_not_asked_for(self) -> None:
        self.add_run()
        self.desktop.passes_to = 43
        self.assertRefused(self.foreground(), "window_not_found", 404)
        self.assertEqual(self.desktop.calls, ["iconic", "unlock"])
        self.assertEqual(self.in_flight(), [])

    def test_a_client_gone_or_unclear_after_the_audit_row_is_not_asked_for(
        self,
    ) -> None:
        # The identity is read again once the command is committed (Codex F4).
        self.add_run()
        incomplete = dict(
            snapshot(self.client),
            creation_time_utc="2099-01-01T00:00:00.0000000Z",
            identity_complete=False,
        )

        def guard_raises(_pid: int) -> dict[str, object]:
            raise RuntimeError("guard")

        for label, seen, error, status in (
            ("gone", {"error": "process_not_found", "exit_code": 4}, "client_process_gone", 409),
            ("guard down", {"error": "guard", "exit_code": 3}, "guard_unavailable", 503),
            ("incomplete", incomplete, "process_identity_mismatch", 409),
            ("guard raises", None, "guard_unavailable", 503),
        ):

            def audit(event: dict[str, object], seen=seen) -> bool:
                if event.get("event") == "lifecycle_foreground":
                    if seen is None:
                        self.guard.snapshot = guard_raises
                    else:
                        self.guard.snapshots[42] = seen
                return self.audit(event)

            with self.subTest(client=label):
                self.guard.snapshots[42] = snapshot(self.client)
                self.lifecycle.audit = audit
                try:
                    self.assertRefused(self.foreground(), error, status)
                finally:
                    vars(self.guard).pop("snapshot", None)
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.in_flight(), [])

    def test_a_minimized_window_is_not_asked_for(self) -> None:
        self.add_run()
        self.desktop.windows[420]["iconic"] = True
        self.assertEqual(
            self.foreground(hold_s=3),
            {"run_id": RUN_ID, "state": "minimized", "granted": None, "held_s": 0.0},
        )
        self.assertEqual(self.desktop.calls, ["iconic"])
        self.assertEqual(self.desktop.reads, 0)
        self.assertEqual(self.in_flight(), [])

    def test_an_audit_row_it_cannot_write_asks_nothing(self) -> None:
        self.add_run()
        self.audit.fail_events.add("lifecycle_foreground")
        self.assertRefused(self.foreground(), "audit_failed", 503)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.in_flight(), [])

    def test_an_error_before_the_commit_drops_the_reservation(self) -> None:
        self.add_run()

        def enumeration_fails():
            return patch.object(
                window_foreground, "client_window", side_effect=RuntimeError("enum")
            )

        def fails(name: str):
            return patch.object(self.coordinator, name, side_effect=RuntimeError(name))

        with enumeration_fails():
            result = self.foreground()
        self.assertRefused(result, "foreground_failed", 503)
        self.assertNotIn("cleanup_degraded", result)
        self.assertEqual(self.in_flight(), [])
        # Codex F5: when the abort fails too, rejecting the reservation drops it.
        with enumeration_fails(), fails("abort_reservation"):
            result = self.foreground()
        self.assertRefused(result, "foreground_failed", 503)
        self.assertNotIn("cleanup_degraded", result)
        self.assertEqual(self.in_flight(), [])
        with enumeration_fails(), fails("abort_reservation"), fails("reject_reservation"):
            result = self.foreground()
        self.assertRefused(result, "foreground_failed", 503)
        self.assertEqual(result["cleanup_degraded"], ["reservation_abort_failed"])
        self.assertEqual(self.desktop.calls, [])

    def test_an_error_after_the_commit_finishes_the_command(self) -> None:
        self.add_run()

        def raises(_hwnd: int) -> bool:
            raise RuntimeError("SetForegroundWindow")

        self.lifecycle.foreground_fns = dataclasses.replace(
            self.desktop.foreground_fns(), set_foreground=raises
        )
        self.assertRefused(self.foreground(), "foreground_failed", 503)
        self.assertEqual(self.desktop.calls, ["iconic", "unlock"])
        self.assertEqual(self.in_flight(), [])
        self.lifecycle.foreground_fns = self.desktop.foreground_fns()
        self.assertEqual(self.foreground()["state"], "foreground")


class ForegroundRouteTest(SessionLoopback, unittest.TestCase):
    def test_the_route_hands_run_id_and_hold_s_to_the_lifecycle(self) -> None:
        seen: list[tuple[object, ...]] = []
        answers = [
            {"run_id": "run-1", "state": "foreground", "granted": True, "held_s": 2.5},
            {"error": "window_not_found", "_http_status": 404},
        ]

        class Lifecycle:
            def foreground_run(self, client, token, run_id, hold_s):
                seen.append((client.session_id, token, run_id, hold_s))
                return answers.pop(0)

        self.state.lifecycle = Lifecycle()
        payload = {
            "identity": IDENTITY_PAYLOAD,
            "lease_token": "token-1",
            "run_id": "run-1",
            "hold_s": 2.5,
        }
        self.assertEqual(
            self.request("/lifecycle/foreground", payload),
            (200, {"run_id": "run-1", "state": "foreground", "granted": True, "held_s": 2.5}),
        )
        status, body = self.request("/lifecycle/foreground", payload)
        self.assertEqual((status, body["error"]), (404, "window_not_found"))
        self.assertEqual(seen, [("same-session", "token-1", "run-1", 2.5)] * 2)


class ForegroundToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_it_is_registered_for_client_mode_with_the_lease_line(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        tools = {tool.name: tool for tool in app._tool_manager.list_tools()}
        description = tools["dayz_test_foreground"].description or ""
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description[:80])
        for words in (
            "SetForegroundWindow and nothing else",
            "(0 to 10)",
            "every 0.25 s",
            "later when the thread wakes late",
            "held_s is the time from the first to the last read",
            "a change between two reads is not seen",
            "never asks twice",
            "state minimized, granted null",
            "capture_screenshot stays passive",
        ):
            self.assertIn(words, description)
        with self.assertRaises(Exception) as raised:
            await app.call_tool("dayz_test_foreground", {"run_id": "run-1"})
        self.assertIn("session_tools_require_client_mode", str(raised.exception))


class ForegroundClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(self.config)
        self.control = self.runtime._control

    @contextlib.contextmanager
    def answer(self, status: int, document: dict[str, object]):
        def transport(**_kwargs):
            return status, json.dumps(document).encode("utf-8")

        # The fixture's daemon is not this machine's: revalidating its policy
        # would refuse before the request is sent, as in test_fb_00c4_audit_stage.
        with patch.object(type(self.control.policy), "revalidate", return_value=None):
            with patch.object(
                self.control._credential_provider,
                "request_with_refresh",
                side_effect=transport,
            ):
                yield

    async def test_the_client_posts_the_run_hold_s_and_lease_and_waits_for_the_hold(
        self,
    ) -> None:
        self.control.active_lease_token = "token-1"
        seen: list[tuple[object, ...]] = []

        async def session_call(path, payload, *, timeout_s=None):
            seen.append((path, payload, timeout_s))
            return {"state": "foreground"}

        with patch.object(self.control, "_session_call", new=session_call):
            result = await self.control.lifecycle_foreground("run-1", 2.5)
        self.assertEqual(result, {"state": "foreground"})
        self.assertEqual(
            seen,
            [
                (
                    "/lifecycle/foreground",
                    {"run_id": "run-1", "hold_s": 2.5, "lease_token": "token-1"},
                    self.control.timeout_s + 10.5,
                )
            ],
        )

    async def test_its_error_document_keeps_a_verified_lease_id(self) -> None:
        with self.answer(404, {"error": "window_not_found", "lease_id": "lease-1"}):
            with self.assertRaises(control_client.ControlClientError) as raised:
                self.control._request_once(
                    "/lifecycle/foreground", {"run_id": "run-1"}, 5.0
                )
        self.assertEqual(raised.exception.code, "window_not_found")
        self.assertIsInstance(raised.exception.body, control_client.VerifiedErrorBody)
        self.assertEqual(raised.exception.body.lease_id, "lease-1")

    async def test_a_stale_lease_answer_clears_the_token_it_used(self) -> None:
        self.control.active_lease_token = "token-1"
        self.assertEqual(
            self.runtime._lifecycle_held_token(self.control.lifecycle_foreground),
            (True, "token-1"),
        )
        with self.answer(409, {"error": "lease_invalid"}):
            with self.assertRaises(server.ToolError):
                await self.runtime.lifecycle_foreground("run-1", 0.0)
        self.assertIsNone(self.control.active_lease_token)

    async def test_the_runtime_drops_the_lease_id_and_names_each_refusal(self) -> None:
        async def answered(run_id, hold_s):
            return {
                "run_id": run_id,
                "state": "lost",
                "granted": True,
                "held_s": 1.25,
                "lease_id": "someone-elses-lease",
            }

        with patch.object(self.control, "lifecycle_foreground", new=answered):
            self.assertEqual(
                await self.runtime.lifecycle_foreground("run-1", 2.5),
                {"run_id": "run-1", "state": "lost", "granted": True, "held_s": 1.25},
            )
        for code in (
            "run_not_found",
            "run_not_adopted",
            "run_protected",
            "client_process_gone",
            "window_not_found",
            "foreground_failed",
        ):
            async def refused(run_id, hold_s, code=code):
                raise control_client.ControlClientError(
                    code, request_stage="post_request", http_bytes_sent=1
                )

            with self.subTest(code=code):
                with patch.object(self.control, "lifecycle_foreground", new=refused):
                    with self.assertRaises(server.ToolError) as raised:
                        await self.runtime.lifecycle_foreground("run-1", 0.0)
                self.assertEqual(str(raised.exception), code)

    async def test_the_tool_calls_the_runtime_under_its_tool_lock(self) -> None:
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            app, _built = build_app(self.config)
        seen: list[tuple[object, ...]] = []

        async def foreground(run_id, hold_s):
            seen.append((run_id, hold_s, self.runtime.tool_lock.locked()))
            return {"run_id": run_id, "state": "lost", "granted": True, "held_s": 1.25}

        with patch.object(self.runtime, "lifecycle_foreground", new=foreground):
            result = _content_json(
                await app.call_tool(
                    "dayz_test_foreground", {"run_id": "run-1", "hold_s": 2.5}
                )
            )
            await app.call_tool("dayz_test_foreground", {"run_id": "run-1"})
        self.assertEqual(
            result, {"run_id": "run-1", "state": "lost", "granted": True, "held_s": 1.25}
        )
        self.assertEqual(seen, [("run-1", 2.5, True), ("run-1", 3, True)])


if __name__ == "__main__":
    unittest.main()
