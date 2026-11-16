"""launch_focus takes the foreground away from a managed DayZ launch that grabbed it (inbox 5c8e).

DayZ activates its own window after it starts, whatever show state it was given
(#59, #168 reverted by #180), and Windows refused the daemon's foreground lock at
every launch (#253, removed by #361). The guard watches the foreground after a
launch and, when a window of the run takes it from a person working in another
window, minimizes that window: Windows activates the next window in the Z order,
best effort the one the person was using. Every window it minimized is shown
again without activation, and the guard checks that it came back.

No test touches the desktop: the windows, the foreground, the cursor, the input
clock and the keys are fakes, and the user32 binding is read through a fake WinDLL.
"""
from __future__ import annotations

import ast
import ctypes
import inspect
import unittest
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import launch_focus
from dayz_mcp.launch_focus import FocusFns, FocusGuard

PERSON = 7  # the window a person works in
OTHER = 8  # a window of another application
CLIENT = 101  # the run's DayZ client window
SERVER = 202  # the run's DayZ server window
PERSON_PID = 900
OTHER_PID = 901
CLIENT_PID = 5001
SERVER_PID = 5002

SUMMARY_KEYS = {
    "mode",
    "steals",
    "returned",
    "elsewhere",
    "failed",
    "user_switched",
    "away",
    "no_previous",
    "unverified",
    "restore_failed",
    "first_steal_s",
    "disarmed",
}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += max(0.0, float(seconds))


class _Desktop:
    """Top-level windows, the foreground, the cursor, and what Windows does on
    SW_MINIMIZE and SW_SHOWNOACTIVATE."""

    def __init__(self) -> None:
        self.pids = {
            PERSON: PERSON_PID,
            OTHER: OTHER_PID,
            CLIENT: CLIENT_PID,
            SERVER: SERVER_PID,
        }
        self.front = PERSON
        # Z order, top first: a minimized front window hands the activation to
        # the first window below it that is not minimized.
        self.z = [PERSON, OTHER]
        self.minimized: set[int] = set()
        self.calls: list[tuple[str, int]] = []
        self.idle = 1.0
        self.mouse = False
        self.keys = False
        self.cursor = PERSON
        # False: SW_MINIMIZE leaves the foreground where it was.
        self.activates_next = True
        # Reads of the foreground before a minimize takes effect.
        self.minimize_delay_reads = 0
        self._pending: list[int] = []
        self._pending_reads = 0
        # False: the window refuses to be restored (WM_QUERYOPEN).
        self.restores = True
        self.restore_activates = False
        self.on_minimize = None
        self.minimize_raises = False
        self.show_raises = False

    def steal(self, hwnd: int) -> None:
        """A window activates itself (a DayZ window), or a person brings it forward."""
        self.front = hwnd
        if hwnd in self.z:
            self.z.remove(hwnd)
        self.z.insert(0, hwnd)

    def _apply_minimize(self, hwnd: int) -> None:
        self.minimized.add(hwnd)
        if self.front == hwnd and self.activates_next:
            below = [w for w in self.z if w != hwnd and w not in self.minimized]
            self.front = below[0] if below else 0

    def foreground(self) -> int:
        if self._pending:
            self._pending_reads += 1
            if self._pending_reads > self.minimize_delay_reads:
                for hwnd in self._pending:
                    self._apply_minimize(hwnd)
                self._pending = []
        return self.front

    def window_pid(self, hwnd: int) -> int:
        return self.pids.get(hwnd, 0)

    def is_window(self, hwnd: int) -> bool:
        return hwnd in self.pids

    def is_iconic(self, hwnd: int) -> bool:
        return hwnd in self.minimized

    def minimize(self, hwnd: int) -> bool:
        self.calls.append(("minimize", hwnd))
        if self.minimize_raises:
            raise OSError("ShowWindowAsync failed")
        if self.on_minimize is not None:
            self.on_minimize(hwnd)
        if self.minimize_delay_reads:
            self._pending.append(hwnd)
            self._pending_reads = 0
        else:
            self._apply_minimize(hwnd)
        return True

    def show_no_activate(self, hwnd: int) -> bool:
        self.calls.append(("show_no_activate", hwnd))
        if self.show_raises:
            raise OSError("ShowWindowAsync failed")
        if self.restores:
            self.minimized.discard(hwnd)
            if self.restore_activates:
                self.steal(hwnd)
        return True

    def idle_s(self) -> float:
        return self.idle

    def mouse_down(self) -> bool:
        return self.mouse

    def switch_keys_down(self) -> bool:
        return self.keys

    def cursor_window(self) -> int:
        return self.cursor

    def fns(self) -> FocusFns:
        return FocusFns(
            foreground=self.foreground,
            window_pid=self.window_pid,
            is_window=self.is_window,
            is_iconic=self.is_iconic,
            minimize=self.minimize,
            show_no_activate=self.show_no_activate,
            idle_s=self.idle_s,
            mouse_down=self.mouse_down,
            switch_keys_down=self.switch_keys_down,
            cursor_window=self.cursor_window,
        )


def _minimizes(desk: _Desktop) -> list[int]:
    return [hwnd for name, hwnd in desk.calls if name == "minimize"]


def _restores(desk: _Desktop) -> list[int]:
    return [hwnd for name, hwnd in desk.calls if name == "show_no_activate"]


class FocusGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.desk = _Desktop()
        self.clock = _Clock()

    def guard(self, **overrides) -> FocusGuard:
        options = {
            "fns": self.desk.fns(),
            "clock": self.clock,
            "sleep": self.clock.sleep,
            "watch_s": 300.0,
            "poll_s": 0.025,
            "present_s": 200.0,
            "intent_s": 0.75,
            "max_returns": 3,
            "settle_s": 1.0,
        }
        options.update(overrides)
        return FocusGuard(**options)

    def tick(self, guard: FocusGuard, seconds: float = 0.025) -> None:
        self.clock.now += seconds
        guard.step()

    def steal_and_tick(self, guard: FocusGuard, hwnd: int = CLIENT) -> None:
        self.desk.steal(hwnd)
        self.tick(guard)

    # -- constants and construction ------------------------------------------

    def test_the_defaults_and_the_modes(self) -> None:
        self.assertEqual(launch_focus.FOCUS_MODES, ("keep", "take"))
        self.assertEqual(launch_focus.DEFAULT_FOCUS, "keep")
        self.assertEqual(launch_focus.WATCH_S, 300.0)
        self.assertEqual(launch_focus.POLL_S, 0.025)
        self.assertEqual(launch_focus.PRESENT_S, 200.0)
        self.assertEqual(launch_focus.INTENT_S, 0.75)
        self.assertEqual(launch_focus.MAX_RETURNS, 3)
        self.assertEqual(launch_focus.SETTLE_S, 1.0)
        self.assertEqual(launch_focus.SW_MINIMIZE, 6)
        self.assertEqual(launch_focus.SW_SHOWNOACTIVATE, 4)
        self.assertEqual(launch_focus.GA_ROOT, 2)
        # Left, right, middle, X1 and X2 mouse buttons; Alt and both Windows keys.
        self.assertEqual(launch_focus.MOUSE_BUTTONS, (0x01, 0x02, 0x04, 0x05, 0x06))
        self.assertEqual(launch_focus.SWITCH_KEYS, (0x12, 0x5B, 0x5C))

    def test_the_guard_takes_keywords_whose_defaults_are_the_module_constants(
        self,
    ) -> None:
        guard = FocusGuard(fns=self.desk.fns())
        self.assertEqual(guard.summary()["mode"], "keep")
        params = inspect.signature(FocusGuard).parameters
        self.assertIsNone(params["verify"].default)
        self.assertEqual(params["watch_s"].default, launch_focus.WATCH_S)
        self.assertEqual(params["poll_s"].default, launch_focus.POLL_S)
        self.assertEqual(params["present_s"].default, launch_focus.PRESENT_S)
        self.assertEqual(params["intent_s"].default, launch_focus.INTENT_S)
        self.assertEqual(params["max_returns"].default, launch_focus.MAX_RETURNS)
        self.assertEqual(params["settle_s"].default, launch_focus.SETTLE_S)
        for name in params:
            self.assertEqual(params[name].kind, inspect.Parameter.KEYWORD_ONLY, name)

    # -- the give-back ----------------------------------------------------------

    def test_a_window_of_the_run_that_takes_the_front_from_a_person_is_minimized_and_shown(
        self,
    ) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(
            self.desk.calls, [("minimize", CLIENT), ("show_no_activate", CLIENT)]
        )
        self.assertEqual(self.desk.front, PERSON)
        self.assertEqual(self.desk.minimized, set())
        summary = guard.summary()
        self.assertEqual(set(summary), SUMMARY_KEYS)
        self.assertEqual(summary["mode"], "keep")
        self.assertEqual(summary["steals"], 1)
        self.assertEqual(summary["returned"], 1)
        for key in (
            "elsewhere",
            "failed",
            "user_switched",
            "away",
            "no_previous",
            "unverified",
            "restore_failed",
        ):
            self.assertEqual(summary[key], 0, key)
        self.assertIsNone(summary["disarmed"])
        self.assertTrue(guard.armed)

    def test_first_steal_s_is_the_time_from_the_first_launch_to_the_steal(
        self,
    ) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.assertIsNone(guard.summary()["first_steal_s"])
        for _ in range(4):
            self.tick(guard, 0.5)
        self.desk.steal(CLIENT)
        self.tick(guard, 0.5)
        self.assertEqual(guard.summary()["first_steal_s"], 2.5)

    def test_a_minimize_that_takes_effect_late_is_waited_for(self) -> None:
        self.desk.minimize_delay_reads = 4
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.front, PERSON)
        self.assertEqual(guard.summary()["returned"], 1)
        self.assertEqual(_restores(self.desk), [CLIENT])

    def test_the_window_is_shown_again_even_when_the_front_does_not_move(
        self,
    ) -> None:
        self.desk.activates_next = False
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(_minimizes(self.desk), [CLIENT])
        self.assertEqual(_restores(self.desk), [CLIENT])
        self.assertEqual(self.desk.minimized, set())
        summary = guard.summary()
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["returned"], 0)

    def test_it_waits_at_most_settle_s_for_the_front_to_move(self) -> None:
        self.desk.activates_next = False
        guard = self.guard(settle_s=1.0, poll_s=0.025)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        start = self.clock.now + 0.025
        self.tick(guard)
        waited = self.clock.now - start
        self.assertGreaterEqual(waited, 1.0 - 1e-6)
        # Settling the minimize and then the restore each take at most settle_s.
        self.assertLess(waited, 2.0 + 0.05 + 1e-6)
        self.assertTrue(all(0 < s <= 0.025 + 1e-9 for s in self.clock.slept))

    def test_a_front_that_lands_on_another_application_reads_elsewhere(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(OTHER)  # an application the person did not use...
        self.desk.steal(CLIENT)  # ...stays between the client and PERSON
        self.desk.z = [CLIENT, OTHER, PERSON]
        self.desk.front = CLIENT
        self.tick(guard)
        self.assertEqual(self.desk.front, OTHER)
        summary = guard.summary()
        self.assertEqual(summary["elsewhere"], 1)
        self.assertEqual(summary["returned"], 0)

    def test_one_steal_is_one_give_back_while_the_run_window_keeps_the_front(
        self,
    ) -> None:
        self.desk.activates_next = False
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        for _ in range(5):
            self.tick(guard)
        self.assertEqual(_minimizes(self.desk), [CLIENT])
        self.assertEqual(guard.summary()["steals"], 1)

    def test_a_window_that_activates_again_when_shown_reads_failed(self) -> None:
        self.desk.restore_activates = True
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.front, CLIENT)
        summary = guard.summary()
        self.assertEqual((summary["failed"], summary["returned"]), (1, 0))

    def test_a_second_run_window_that_takes_the_front_is_minimized_too(
        self,
    ) -> None:
        guard = self.guard()
        guard.add_pids([SERVER_PID, CLIENT_PID])
        self.tick(guard)
        self.desk.steal(SERVER)
        self.desk.steal(CLIENT)  # z: CLIENT, SERVER, PERSON, OTHER
        self.tick(guard)
        self.assertEqual(_minimizes(self.desk), [CLIENT, SERVER])
        restores = _restores(self.desk)
        self.assertEqual(sorted(restores), sorted([CLIENT, SERVER]))
        last_minimize = max(
            index for index, call in enumerate(self.desk.calls) if call[0] == "minimize"
        )
        first_restore = min(
            index
            for index, call in enumerate(self.desk.calls)
            if call[0] == "show_no_activate"
        )
        self.assertLess(last_minimize, first_restore)
        self.assertEqual(self.desk.front, PERSON)
        self.assertEqual(self.desk.minimized, set())
        summary = guard.summary()
        self.assertEqual(summary["steals"], 1)
        self.assertEqual(summary["returned"], 1)

    # -- restoration ------------------------------------------------------------

    def test_a_window_that_stays_minimized_is_shown_once_more_then_reported(
        self,
    ) -> None:
        self.desk.restores = False
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(_restores(self.desk), [CLIENT, CLIENT])
        summary = guard.summary()
        self.assertEqual(summary["restore_failed"], 1)
        self.assertEqual(summary["disarmed"], "restore_failed")
        self.assertFalse(guard.armed)

    def test_a_window_that_belongs_to_another_process_when_shown_is_left_alone(
        self,
    ) -> None:
        def recycle(_hwnd: int) -> None:
            # The client window is destroyed and its handle reused by OTHER's process.
            self.desk.pids[CLIENT] = OTHER_PID

        self.desk.on_minimize = recycle
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(_restores(self.desk), [])
        self.assertEqual(guard.summary()["restore_failed"], 0)

    def test_a_minimize_that_raises_still_settles_and_shows_nothing(self) -> None:
        self.desk.minimize_raises = True
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(_restores(self.desk), [])
        summary = guard.summary()
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(guard.armed)
        self.assertTrue(guard.wait_idle(0))

    def test_a_show_that_raises_is_retried_once_then_reported(self) -> None:
        self.desk.show_raises = True
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(_restores(self.desk), [CLIENT, CLIENT])
        self.assertEqual(guard.summary()["restore_failed"], 1)
        self.assertEqual(guard.summary()["disarmed"], "restore_failed")

    # -- who may be touched -------------------------------------------------------

    def test_a_window_of_another_process_is_never_touched(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard, OTHER)
        self.steal_and_tick(guard, SERVER)  # not a pid of this guard
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["steals"], 0)

    def test_a_process_verify_refuses_is_not_touched_and_leaves_the_watch(
        self,
    ) -> None:
        asked: list[int] = []

        def verify(pid: int) -> bool:
            asked.append(pid)
            return False

        guard = self.guard(verify=verify)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(asked, [CLIENT_PID])
        self.assertEqual(guard.summary()["unverified"], 1)
        # The pid left the watch: its next steal is not a steal of this run.
        self.steal_and_tick(guard, PERSON)
        self.steal_and_tick(guard)
        self.assertEqual(guard.summary()["steals"], 1)

    def test_a_verify_that_raises_counts_as_a_refusal(self) -> None:
        def verify(_pid: int) -> bool:
            raise OSError("identity unavailable")

        guard = self.guard(verify=verify)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["unverified"], 1)

    def test_a_second_window_whose_process_verify_refuses_is_not_minimized(
        self,
    ) -> None:
        guard = self.guard(verify=lambda pid: pid == CLIENT_PID)
        guard.add_pids([SERVER_PID, CLIENT_PID])
        self.tick(guard)
        self.desk.steal(SERVER)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(_minimizes(self.desk), [CLIENT])

    # -- intent and presence --------------------------------------------------------

    def test_a_click_on_the_dayz_window_is_left_to_the_person(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True
        self.desk.cursor = CLIENT
        self.tick(guard)
        self.desk.mouse = False
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        summary = guard.summary()
        self.assertEqual(summary["user_switched"], 1)
        self.assertIsNone(summary["disarmed"])
        self.assertTrue(guard.armed)

    def test_a_click_in_the_persons_own_window_does_not_count_as_a_switch(
        self,
    ) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True  # the person clicks in their own window...
        self.desk.cursor = PERSON
        self.tick(guard)
        self.desk.mouse = False
        self.clock.now += 0.4
        self.steal_and_tick(guard)  # ...and DayZ activates 0.4 s later
        self.assertEqual(guard.summary()["returned"], 1)
        self.assertEqual(guard.summary()["user_switched"], 0)

    def test_alt_or_a_windows_key_down_counts_wherever_the_cursor_is(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.keys = True  # Alt+Tab
        self.desk.cursor = PERSON
        self.tick(guard)
        self.desk.keys = False
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["user_switched"], 1)

    def test_keys_down_at_the_read_that_shows_the_steal_count(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.keys = True
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["user_switched"], 1)

    def test_input_seen_longer_than_intent_s_ago_does_not_count(self) -> None:
        guard = self.guard(intent_s=0.75)
        guard.add_pids([CLIENT_PID])
        self.desk.keys = True
        self.desk.mouse = True
        self.desk.cursor = CLIENT
        self.tick(guard)
        self.desk.keys = False
        self.desk.mouse = False
        self.tick(guard, 0.5)
        self.tick(guard, 0.5)
        self.steal_and_tick(guard)
        self.assertEqual(guard.summary()["returned"], 1)
        self.assertEqual(guard.summary()["user_switched"], 0)

    def test_after_a_switch_to_dayz_a_later_steal_is_still_given_back(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.keys = True
        self.steal_and_tick(guard)  # the person switches to DayZ
        self.desk.keys = False
        self.clock.now += 5.0
        self.steal_and_tick(guard, PERSON)  # and back to their window
        self.clock.now += 5.0
        self.steal_and_tick(guard)  # DayZ activates itself again
        summary = guard.summary()
        self.assertEqual((summary["user_switched"], summary["returned"]), (1, 1))

    def test_nobody_at_the_pc_leaves_the_front_to_dayz_and_keeps_watching(
        self,
    ) -> None:
        guard = self.guard(present_s=200.0)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.idle = 201.0
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["away"], 1)
        self.assertTrue(guard.armed)
        self.desk.idle = 0.2
        self.steal_and_tick(guard, PERSON)
        self.steal_and_tick(guard)
        summary = guard.summary()
        self.assertEqual(summary["returned"], 1)
        self.assertEqual(summary["steals"], 2)

    # -- the previous window -----------------------------------------------------

    def test_a_steal_with_no_window_to_give_back_to_is_counted_and_left(
        self,
    ) -> None:
        self.desk.front = 0
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["no_previous"], 1)

    def test_a_previous_window_that_is_gone_is_not_given_back_to(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        del self.desk.pids[PERSON]
        self.desk.z.remove(PERSON)
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["no_previous"], 1)

    def test_the_window_a_person_moves_to_is_the_one_given_back_to(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.steal_and_tick(guard, OTHER)
        self.steal_and_tick(guard)  # z: CLIENT, OTHER, PERSON
        self.assertEqual(self.desk.front, OTHER)
        self.assertEqual(guard.summary()["returned"], 1)

    def test_a_steal_before_the_guard_knew_the_pid_goes_back_to_the_window_before_the_launch(
        self,
    ) -> None:
        guard = self.guard()
        self.desk.steal(CLIENT)  # DayZ activates before add_pids
        self.tick(guard)
        guard.add_pids([CLIENT_PID], foreground_before=PERSON)
        self.tick(guard)
        self.assertEqual(self.desk.front, PERSON)
        self.assertEqual(guard.summary()["returned"], 1)

    def test_a_window_seen_before_its_pid_joined_is_never_the_one_given_back_to(
        self,
    ) -> None:
        guard = self.guard()
        self.tick(guard)
        self.steal_and_tick(guard)  # the client activates before add_pids
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["no_previous"], 1)

    def test_a_foreground_before_that_is_a_run_window_is_ignored(self) -> None:
        guard = self.guard()
        guard.add_pids([SERVER_PID])
        self.tick(guard)
        guard.add_pids([CLIENT_PID], foreground_before=SERVER)
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.front, PERSON)
        self.assertEqual(guard.summary()["returned"], 1)

    # -- limits and ends -------------------------------------------------------------

    def test_every_attempt_counts_toward_max_returns_then_it_disarms(self) -> None:
        self.desk.activates_next = False  # every attempt fails
        guard = self.guard(max_returns=2)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        for _ in range(2):
            self.steal_and_tick(guard)
            self.steal_and_tick(guard, PERSON)  # the person clicks back
        self.assertEqual(guard.summary()["failed"], 2)
        self.steal_and_tick(guard)
        self.assertEqual(len(_minimizes(self.desk)), 2)
        summary = guard.summary()
        self.assertEqual(summary["steals"], 3)
        self.assertEqual(summary["disarmed"], "max_returns")
        self.assertEqual(self.desk.front, CLIENT)

    def test_the_watch_ends_watch_s_after_the_last_launch(self) -> None:
        guard = self.guard(watch_s=10.0)
        guard.add_pids([SERVER_PID])
        self.tick(guard, 8.0)
        guard.add_pids([CLIENT_PID])  # the client launch restarts the watch
        self.tick(guard, 5.0)
        self.steal_and_tick(guard)
        self.assertEqual(guard.summary()["returned"], 1)
        self.tick(guard, 10.0)
        self.steal_and_tick(guard)
        self.assertEqual(len(_minimizes(self.desk)), 1)
        self.assertEqual(guard.summary()["disarmed"], "window_elapsed")
        self.assertFalse(guard.armed)

    def test_disarm_stops_it_and_the_first_reason_stays(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        guard.disarm("foreground_verb")
        guard.disarm("run_ended")
        self.steal_and_tick(guard)
        self.assertEqual(self.desk.calls, [])
        self.assertEqual(guard.summary()["disarmed"], "foreground_verb")
        self.assertFalse(guard.armed)

    def test_a_disarm_during_a_give_back_stops_further_minimizes_and_still_shows(
        self,
    ) -> None:
        guard = self.guard()
        idle_during: list[bool] = []

        def disarm_now(_hwnd: int) -> None:
            idle_during.append(guard.wait_idle(0))
            guard.disarm("foreground_verb")

        self.desk.on_minimize = disarm_now
        guard.add_pids([SERVER_PID, CLIENT_PID])
        self.tick(guard)
        self.desk.steal(SERVER)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(_minimizes(self.desk), [CLIENT])
        self.assertEqual(_restores(self.desk), [CLIENT])
        self.assertEqual(self.desk.minimized, set())
        self.assertEqual(idle_during, [False])
        self.assertTrue(guard.wait_idle(0))
        self.assertEqual(guard.summary()["disarmed"], "foreground_verb")

    def test_wait_idle_is_true_when_no_give_back_runs(self) -> None:
        guard = self.guard()
        self.assertTrue(guard.wait_idle(0))
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.assertTrue(guard.wait_idle(0))

    # -- run() --------------------------------------------------------------------

    def test_run_watches_until_disarmed_and_returns_the_summary(self) -> None:
        guard = self.guard(watch_s=1.0, poll_s=0.025)
        guard.add_pids([CLIENT_PID])
        summary = guard.run()
        self.assertEqual(summary["disarmed"], "window_elapsed")
        self.assertEqual(summary, guard.summary())
        self.assertTrue(self.clock.slept)
        self.assertTrue(all(0 < s <= 0.025 + 1e-9 for s in self.clock.slept))
        self.assertLessEqual(len(self.clock.slept), 1.0 / 0.025 + 2)

    def test_run_gives_back_a_steal_made_while_it_runs(self) -> None:
        def sleep_and_steal(seconds: float) -> None:
            self.clock.sleep(seconds)
            if len(self.clock.slept) == 3:
                self.desk.steal(CLIENT)

        guard = self.guard(watch_s=1.0, poll_s=0.025, sleep=sleep_and_steal)
        guard.add_pids([CLIENT_PID])
        summary = guard.run()
        self.assertEqual(summary["returned"], 1)
        self.assertEqual(self.desk.front, PERSON)
        self.assertEqual(summary["disarmed"], "window_elapsed")

    def test_run_keeps_only_the_pids_the_run_still_has_and_ends_with_none(
        self,
    ) -> None:
        answers = [{CLIENT_PID}, set()]
        asked: list[float] = []

        def live_pids() -> set[int]:
            asked.append(self.clock.now)
            return answers.pop(0)

        def sleep_and_steal(seconds: float) -> None:
            self.clock.sleep(seconds)
            # After the first check dropped SERVER_PID, the server steals: not a run window now.
            if asked and len(asked) == 1 and self.desk.front != SERVER:
                self.desk.steal(SERVER)

        guard = self.guard(watch_s=300.0, poll_s=0.025, sleep=sleep_and_steal)
        guard.add_pids([SERVER_PID, CLIENT_PID])
        summary = guard.run(live_pids=live_pids, check_every_s=1.0)
        self.assertEqual(summary["disarmed"], "run_ended")
        self.assertEqual(len(asked), 2)
        gap = asked[1] - asked[0]
        self.assertGreaterEqual(gap, 1.0 - 1e-6)
        self.assertLess(gap, 1.0 + 0.025 + 1e-6)
        self.assertEqual(summary["steals"], 0)
        self.assertEqual(self.desk.calls, [])

    def test_run_stops_when_live_pids_cannot_tell(self) -> None:
        def live_pids() -> set[int]:
            raise OSError("manifest unreadable")

        guard = self.guard(watch_s=300.0, poll_s=0.025)
        guard.add_pids([CLIENT_PID])
        summary = guard.run(live_pids=live_pids, check_every_s=1.0)
        self.assertEqual(summary["disarmed"], "alive_unknown")
        self.assertLess(self.clock.now - 1000.0, 1.1)

    def test_a_pid_added_while_live_pids_reads_is_kept(self) -> None:
        stolen = {"done": False}

        def sleep_and_steal(seconds: float) -> None:
            self.clock.sleep(seconds)
            if self.clock.now - 1000.0 > 1.5 and not stolen["done"]:
                stolen["done"] = True
                self.desk.steal(CLIENT)

        guard = self.guard(watch_s=2.5, poll_s=0.025, sleep=sleep_and_steal)
        guard.add_pids([SERVER_PID])
        reads = {"n": 0}

        def live_pids() -> set[int]:
            reads["n"] += 1
            if reads["n"] == 1:
                # The client launches while the manifest is read: this read misses it.
                guard.add_pids([CLIENT_PID])
                return {SERVER_PID}
            return {SERVER_PID, CLIENT_PID}

        summary = guard.run(live_pids=live_pids, check_every_s=1.0)
        self.assertEqual(summary["steals"], 1)
        self.assertEqual(summary["returned"], 1)
        self.assertEqual(summary["disarmed"], "window_elapsed")

    # -- what it never does --------------------------------------------------------

    def test_the_guard_never_forces_the_foreground_or_injects_input(self) -> None:
        tree = ast.parse(inspect.getsource(launch_focus))
        used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        used |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for name in (
            "SetForegroundWindow",
            "AttachThreadInput",
            "SendInput",
            "keybd_event",
            "mouse_event",
            "LockSetForegroundWindow",
            "AllowSetForegroundWindow",
            "SwitchToThisWindow",
            "BringWindowToTop",
            "SetWindowsHookExW",
            "SetWindowsHookExA",
            "SetWindowPos",
        ):
            self.assertNotIn(name, used)


class _Call:
    def __init__(self, answer) -> None:
        self.argtypes = None
        self.restype = None
        self.answer = answer
        self.args: list[tuple] = []

    def __call__(self, *args):
        self.args.append(args)
        return self.answer(*args) if callable(self.answer) else self.answer


class RealBindingTest(unittest.TestCase):
    def test_it_binds_user32_and_kernel32_with_handles_for_arguments(self) -> None:
        def thread_pid(_hwnd, ref) -> int:
            ref._obj.value = CLIENT_PID
            return 77

        last_input: dict[str, int] = {"tick": 1000, "ok": 1}

        def fill_last_input(ref) -> int:
            self.assertEqual(ref._obj.cbSize, ctypes.sizeof(launch_focus.LASTINPUTINFO))
            ref._obj.dwTime = last_input["tick"]
            return last_input["ok"]

        state: dict[int, int] = {0x12: -32768}

        def key_state(vk: int) -> int:
            return state.get(vk, 0)

        cursor = {"ok": 1}

        def cursor_pos(ref) -> int:
            ref._obj.x, ref._obj.y = 40, 50
            return cursor["ok"]

        def from_point(point) -> int:
            self.assertEqual((point.x, point.y), (40, 50))
            return 0x77

        user32 = SimpleNamespace(
            GetForegroundWindow=_Call(0x51),
            GetWindowThreadProcessId=_Call(thread_pid),
            IsWindow=_Call(1),
            IsIconic=_Call(1),
            ShowWindowAsync=_Call(1),
            GetLastInputInfo=_Call(fill_last_input),
            GetAsyncKeyState=_Call(key_state),
            GetCursorPos=_Call(cursor_pos),
            WindowFromPoint=_Call(from_point),
            GetAncestor=_Call(0x70),
        )
        kernel32 = SimpleNamespace(GetTickCount=_Call(3500))
        libraries = {"user32": user32, "kernel32": kernel32}

        def windll(name, use_last_error=False):
            self.assertTrue(use_last_error)
            return libraries[name]

        with patch("ctypes.WinDLL", create=True, side_effect=windll):
            bound = launch_focus.bind_real_win32()
        hwnd = wintypes.HWND
        self.assertEqual(
            {
                name: (getattr(user32, name).argtypes, getattr(user32, name).restype)
                for name in vars(user32)
            },
            {
                "GetForegroundWindow": ([], hwnd),
                "GetWindowThreadProcessId": (
                    [hwnd, ctypes.POINTER(wintypes.DWORD)],
                    wintypes.DWORD,
                ),
                "IsWindow": ([hwnd], wintypes.BOOL),
                "IsIconic": ([hwnd], wintypes.BOOL),
                "ShowWindowAsync": ([hwnd, ctypes.c_int], wintypes.BOOL),
                "GetLastInputInfo": (
                    [ctypes.POINTER(launch_focus.LASTINPUTINFO)],
                    wintypes.BOOL,
                ),
                "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_short),
                "GetCursorPos": ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
                "WindowFromPoint": ([wintypes.POINT], hwnd),
                "GetAncestor": ([hwnd, wintypes.UINT], hwnd),
            },
        )
        self.assertEqual(
            (kernel32.GetTickCount.argtypes, kernel32.GetTickCount.restype),
            ([], wintypes.DWORD),
        )
        self.assertIsInstance(bound, FocusFns)
        self.assertEqual(bound.foreground(), 0x51)
        self.assertEqual(bound.window_pid(5), CLIENT_PID)
        self.assertIs(bound.is_window(5), True)
        self.assertIs(bound.is_iconic(5), True)
        self.assertIs(bound.minimize(5), True)
        self.assertIs(bound.show_no_activate(6), True)
        self.assertEqual(
            user32.ShowWindowAsync.args,
            [(5, launch_focus.SW_MINIMIZE), (6, launch_focus.SW_SHOWNOACTIVATE)],
        )
        self.assertEqual(bound.idle_s(), 2.5)
        # Alt is down (high bit); no mouse button is.
        self.assertIs(bound.switch_keys_down(), True)
        self.assertIs(bound.mouse_down(), False)
        # Only the high bit means down: 1 is "pressed since the last read", up now.
        state.clear()
        state.update({0x01: 1, 0x12: 1, 0x5B: 1})
        self.assertIs(bound.mouse_down(), False)
        self.assertIs(bound.switch_keys_down(), False)
        state[0x02] = -32767
        self.assertIs(bound.mouse_down(), True)
        asked = {args[0] for args in user32.GetAsyncKeyState.args}
        self.assertEqual(asked, set(launch_focus.MOUSE_BUTTONS) | set(launch_focus.SWITCH_KEYS))
        # The root window under the cursor.
        self.assertEqual(bound.cursor_window(), 0x70)
        self.assertEqual(user32.GetAncestor.args[-1], (0x77, launch_focus.GA_ROOT))
        cursor["ok"] = 0
        self.assertEqual(bound.cursor_window(), 0)
        cursor["ok"] = 1
        user32.WindowFromPoint.answer = None
        self.assertEqual(bound.cursor_window(), 0)
        # The tick counter wraps every 49.7 days.
        kernel32.GetTickCount.answer = 5
        last_input["tick"] = 0xFFFFFFFF - 994
        self.assertEqual(bound.idle_s(), 1.0)
        # A read that fails counts as a person at the PC; a NULL handle as no window.
        last_input["ok"] = 0
        self.assertEqual(bound.idle_s(), 0.0)
        user32.GetForegroundWindow.answer = None
        self.assertEqual(bound.foreground(), 0)
        user32.IsWindow.answer = 0
        self.assertIs(bound.is_window(5), False)
        user32.IsIconic.answer = 0
        self.assertIs(bound.is_iconic(5), False)
        user32.ShowWindowAsync.answer = 0
        self.assertIs(bound.minimize(5), False)

    def test_real_win32_binds_once(self) -> None:
        sentinel = object()
        with patch.object(launch_focus, "_REAL", None), patch.object(
            launch_focus, "bind_real_win32", return_value=sentinel
        ) as bind:
            self.assertIs(launch_focus.real_win32(), sentinel)
            self.assertIs(launch_focus.real_win32(), sentinel)
        bind.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
