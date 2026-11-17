"""The launch focus guard records why it left a steal alone (5c8e follow-up).

Oracle frozen before the implementation (rev. 2, after the review of 5cf693e).
In the desktop run of #417 two steals were classed user_switched and left
alone, and nobody could tell why. The guard keeps its decision exactly as in
#417 (the window under the cursor read when the steal is detected), and it
now records the evidence: the window under the cursor when a mouse button was
pressed, whether that press is trustworthy (released since, the longest gap
between polls since, whether the person window changed since) and what a rule
based on the press would have decided. It keeps a bounded log,
FocusGuard.evidence(), written as `focus_evidence` in the guard's audit row
next to the unchanged `focus` counters: one entry per arming (the time since
the launcher call, what was in front) and one per steal. Windows appear only
as categories. No test touches the desktop or starts a thread.
"""
from __future__ import annotations

import json
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import launch_focus
from dayz_mcp.launch_focus import FocusGuard
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import JsonlAuditWriter, RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.launch_focus_helpers import (
    CLIENT,
    CLIENT_PID,
    OTHER,
    PERSON,
    SERVER_PID,
    FakeClock,
    FakeDesktop,
)
from tests.process_lifecycle_helpers import (
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
START = 1000.0  # FakeClock's first reading: the guard's first arming in these tests

ARM_KEYS = {"kind", "at_s", "arm_delay_s", "front_at_arm"}
STEAL_KEYS = {
    "kind",
    "at_s",
    "outcome",
    "input",
    "key_age_s",
    "mouse_age_s",
    "click_age_s",
    "click_window",
    "click_held",
    "click_person_changed",
    "poll_gap_s",
    "detect_window",
    "press_rule_switched",
}
CATEGORIES = {"run", "person", "other", "none"}
OUTCOMES = {
    "pending",
    "user_switched",
    "no_previous",
    "away",
    "max_returns",
    "unverified",
    "returned",
    "elsewhere",
    "failed",
}
NO_PRESS = {
    "click_age_s": None,
    "click_window": None,
    "click_held": None,
    "click_person_changed": None,
    "poll_gap_s": None,
}


def steals(guard: FocusGuard) -> list[dict]:
    return [entry for entry in guard.evidence()["entries"] if entry["kind"] == "steal"]


def _broken_cursor() -> int:
    raise OSError("no cursor")


class _HookClock(FakeClock):
    """A clock whose next reading first runs `hook` and then returns the time read
    before it: another thread acting between a caller's clock read and its lock."""

    def __init__(self) -> None:
        super().__init__()
        self.hook = None

    def __call__(self) -> float:
        value = self.now
        hook, self.hook = self.hook, None
        if hook is not None:
            hook()
        return value


class _GuardCase(unittest.TestCase):
    def setUp(self) -> None:
        self.desk = FakeDesktop()
        self.clock = FakeClock()

    def guard(self, **kwargs) -> FocusGuard:
        fns = kwargs.pop("fns", None) or self.desk.fns()
        clock = kwargs.pop("clock", None) or self.clock
        return FocusGuard(fns=fns, clock=clock, sleep=clock.sleep, **kwargs)

    def tick(self, guard: FocusGuard, seconds: float = 0.025) -> None:
        self.clock.now += seconds
        guard.step()

    def detect(self, guard: FocusGuard) -> float:
        """One poll that sees a steal; returns the time of that poll."""
        self.clock.now += 0.025
        at = self.clock.now
        guard.step()
        return at

    def assert_entry(self, entry: dict, expected: dict) -> None:
        self.assertEqual(set(entry), set(expected))
        for key, want in expected.items():
            got = entry[key]
            if isinstance(want, float):
                self.assertIsInstance(got, (int, float), key)
                self.assertNotIsInstance(got, bool, key)
                self.assertAlmostEqual(got, want, delta=0.011, msg=key)
                self.assertEqual(got, round(got, 2), key)
            else:
                self.assertEqual(got, want, key)


class DecisionUnchangedTest(_GuardCase):
    """The decision stays the rule of #417; the press only goes to the log."""

    def test_a_click_in_the_persons_window_just_before_dayz_covers_it_is_logged(
        self,
    ) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)  # the person's window is in front
        self.desk.mouse = True  # the person clicks in their own window...
        self.desk.cursor = PERSON
        self.tick(guard)
        pressed = self.clock.now
        self.desk.mouse = False
        self.tick(guard)
        self.tick(guard, 0.2)
        self.desk.steal(CLIENT)  # ...and DayZ comes up under the cursor
        self.desk.cursor = CLIENT
        at = self.detect(guard)
        # As in #417: the cursor is over DayZ now, so the steal is left alone.
        self.assertEqual(self.desk.calls, [])
        summary = guard.summary()
        self.assertEqual((summary["user_switched"], summary["returned"]), (1, 0))
        (entry,) = steals(guard)
        self.assert_entry(
            entry,
            {
                "kind": "steal",
                "at_s": at - START,
                "outcome": "user_switched",
                "input": "mouse",
                "key_age_s": None,
                "mouse_age_s": at - pressed,
                "click_age_s": at - pressed,
                "click_window": "person",
                "click_held": False,
                "click_person_changed": False,
                "poll_gap_s": 0.2,
                "detect_window": "run",
                "press_rule_switched": False,
            },
        )

    def test_the_cursor_is_read_at_the_press_not_while_the_button_is_held(
        self,
    ) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True  # pressed in the person's window and held
        self.desk.cursor = PERSON
        self.tick(guard)
        pressed = self.clock.now
        self.desk.cursor = CLIENT  # DayZ comes up under the held button
        self.tick(guard)
        self.desk.steal(CLIENT)
        at = self.detect(guard)
        self.assertEqual(self.desk.calls, [])
        (entry,) = steals(guard)
        self.assertEqual(
            (
                entry["outcome"],
                entry["input"],
                entry["click_window"],
                entry["click_held"],
                entry["detect_window"],
                entry["press_rule_switched"],
            ),
            ("user_switched", "mouse", "person", True, "run", False),
        )
        # The button is still down at the steal: the mouse was seen just now,
        # the press it belongs to earlier.
        self.assertAlmostEqual(entry["mouse_age_s"], 0.0, delta=0.011)
        self.assertAlmostEqual(entry["click_age_s"], at - pressed, delta=0.011)
        self.assertAlmostEqual(entry["poll_gap_s"], 0.025, delta=0.011)

    def test_each_new_press_reads_the_cursor_again(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True  # a press on DayZ...
        self.desk.cursor = CLIENT
        self.tick(guard)
        self.desk.mouse = False
        self.tick(guard)
        self.desk.mouse = True  # ...then one in the person's window
        self.desk.cursor = PERSON
        self.tick(guard)
        self.desk.mouse = False
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.desk.cursor = CLIENT
        self.detect(guard)
        (entry,) = steals(guard)
        self.assertEqual(
            (entry["click_window"], entry["click_held"], entry["press_rule_switched"]),
            ("person", False, False),
        )

    def test_a_button_already_down_at_the_first_poll_counts_as_a_press(self) -> None:
        self.desk.mouse = True
        self.desk.cursor = PERSON
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)  # first poll: the button is down over the person's window
        self.desk.cursor = CLIENT
        self.desk.steal(CLIENT)
        self.detect(guard)
        self.assertEqual(self.desk.calls, [])
        (entry,) = steals(guard)
        self.assertEqual(
            (entry["click_window"], entry["click_held"], entry["press_rule_switched"]),
            ("person", True, False),
        )

    def test_a_press_on_a_dayz_window_is_logged_as_run(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True  # the person clicks DayZ
        self.desk.cursor = CLIENT
        self.tick(guard)
        self.desk.mouse = False
        self.desk.steal(CLIENT)
        self.detect(guard)
        self.assertEqual(self.desk.calls, [])
        (entry,) = steals(guard)
        self.assertEqual(
            (
                entry["outcome"],
                entry["click_window"],
                entry["detect_window"],
                entry["press_rule_switched"],
            ),
            ("user_switched", "run", "run", True),
        )

    def test_a_press_on_another_window_is_logged_as_other(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True  # e.g. the taskbar
        self.desk.cursor = OTHER
        self.tick(guard)
        self.desk.mouse = False
        self.desk.steal(CLIENT)
        self.desk.cursor = CLIENT
        self.detect(guard)
        self.assertEqual(self.desk.calls, [])
        (entry,) = steals(guard)
        self.assertEqual(
            (entry["click_window"], entry["detect_window"], entry["press_rule_switched"]),
            ("other", "run", True),
        )

    def test_a_click_that_changed_the_person_window_is_flagged(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)  # the person works in PERSON
        self.desk.mouse = True  # and clicks OTHER (an application, or a selector)
        self.desk.cursor = OTHER
        self.tick(guard)
        self.desk.mouse = False
        self.desk.steal(OTHER)
        self.tick(guard)  # OTHER is now the person window
        self.desk.steal(CLIENT)  # DayZ comes up under the cursor
        self.desk.cursor = CLIENT
        self.detect(guard)
        self.assertEqual(self.desk.calls, [])
        (entry,) = steals(guard)
        self.assertEqual(
            (
                entry["outcome"],
                entry["click_window"],
                entry["click_person_changed"],
                entry["press_rule_switched"],
            ),
            ("user_switched", "person", True, False),
        )

    def test_a_gap_in_polling_after_a_press_is_logged(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True  # pressed in the person's window and, as far as polls show, held
        self.desk.cursor = PERSON
        self.tick(guard)
        slow = []

        def slow_give_back(hwnd: int) -> None:
            if not slow:
                slow.append(hwnd)
                self.clock.now += 0.3  # no poll while the give-back runs

        self.desk.on_minimize = slow_give_back
        self.desk.steal(CLIENT)  # the cursor is still over PERSON: given back
        self.detect(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT])
        # During the give-back the person released and pressed DayZ: no poll saw it.
        self.desk.steal(CLIENT)
        self.desk.cursor = CLIENT
        self.detect(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT])  # left alone, as in #417
        first, second = steals(guard)
        self.assertEqual(first["outcome"], "returned")
        self.assertEqual(
            (
                second["outcome"],
                second["click_window"],
                second["click_held"],
                second["press_rule_switched"],
            ),
            ("user_switched", "person", True, False),
        )
        self.assertGreaterEqual(second["poll_gap_s"], 0.3)
        self.assertEqual(second["poll_gap_s"], round(second["poll_gap_s"], 2))


class StealLogTest(_GuardCase):
    def test_a_switch_key_is_logged_as_key_input(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.keys = True  # Alt+Tab
        self.tick(guard)
        key_at = self.clock.now
        self.desk.keys = False
        self.tick(guard, 0.1)
        self.desk.steal(CLIENT)
        at = self.detect(guard)
        self.assertEqual(self.desk.calls, [])
        (entry,) = steals(guard)
        self.assert_entry(
            entry,
            {
                "kind": "steal",
                "at_s": at - START,
                "outcome": "user_switched",
                "input": "key",
                "key_age_s": at - key_at,
                "mouse_age_s": None,
                **NO_PRESS,
                "detect_window": "person",
                "press_rule_switched": True,
            },
        )

    def test_a_switch_key_decides_before_a_click_and_is_the_logged_input(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.keys = True  # Alt held while clicking in the person's window
        self.desk.mouse = True
        self.desk.cursor = PERSON
        self.tick(guard)
        self.desk.keys = False
        self.desk.mouse = False
        self.desk.steal(CLIENT)
        self.detect(guard)
        self.assertEqual(self.desk.calls, [])
        (entry,) = steals(guard)
        self.assertEqual(
            (
                entry["outcome"],
                entry["input"],
                entry["click_window"],
                entry["press_rule_switched"],
            ),
            ("user_switched", "key", "person", True),
        )

    def test_a_steal_without_recent_input_is_given_back_and_logged_as_none(
        self,
    ) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.desk.cursor = CLIENT
        at = self.detect(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT])
        (entry,) = steals(guard)
        self.assert_entry(
            entry,
            {
                "kind": "steal",
                "at_s": at - START,
                "outcome": "returned",
                "input": "none",
                "key_age_s": None,
                "mouse_age_s": None,
                **NO_PRESS,
                "detect_window": "run",
                "press_rule_switched": False,
            },
        )

    def test_input_older_than_intent_s_is_logged_with_its_age_but_does_not_count(
        self,
    ) -> None:
        guard = self.guard(intent_s=0.75)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True  # an old click on DayZ
        self.desk.cursor = CLIENT
        self.tick(guard)
        pressed = self.clock.now
        self.desk.mouse = False
        self.tick(guard, 0.5)
        self.tick(guard, 0.5)
        self.desk.steal(CLIENT)
        at = self.detect(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT])
        (entry,) = steals(guard)
        self.assert_entry(
            entry,
            {
                "kind": "steal",
                "at_s": at - START,
                "outcome": "returned",
                "input": "none",
                "key_age_s": None,
                "mouse_age_s": at - pressed,
                "click_age_s": at - pressed,
                "click_window": "run",
                "click_held": False,
                "click_person_changed": False,
                "poll_gap_s": 0.5,
                "detect_window": "run",
                "press_rule_switched": False,
            },
        )

    def test_a_steal_reads_pending_while_its_give_back_runs(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        seen = []
        self.desk.on_minimize = lambda hwnd: seen.append(steals(guard)[-1]["outcome"])
        self.desk.steal(CLIENT)
        self.detect(guard)
        self.assertEqual(seen, ["pending"])
        self.assertEqual(steals(guard)[-1]["outcome"], "returned")

    def test_a_steal_with_no_window_to_give_back_to_is_logged(self) -> None:
        self.desk.front = CLIENT  # DayZ is in front at the arming, nothing known before it
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        summary = guard.summary()
        self.assertEqual(summary["no_previous"], 1)
        (entry,) = steals(guard)
        self.assertEqual(entry["outcome"], "no_previous")
        # No person window is known, so the window under the cursor is "other".
        self.assertEqual(entry["detect_window"], "other")

    def test_a_steal_while_the_person_is_away_is_logged(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.idle = 500.0
        self.desk.steal(CLIENT)
        self.detect(guard)
        (entry,) = steals(guard)
        self.assertEqual(entry["outcome"], "away")

    def test_a_steal_by_an_unverified_process_is_logged(self) -> None:
        guard = self.guard(verify=lambda pid: False)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.detect(guard)
        (entry,) = steals(guard)
        self.assertEqual(entry["outcome"], "unverified")

    def test_the_attempt_limit_is_the_outcome_of_the_steal_it_stops(self) -> None:
        guard = self.guard(max_returns=1)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.detect(guard)
        self.tick(guard)  # the person's window is back in front
        self.desk.steal(CLIENT)
        self.detect(guard)
        summary = guard.summary()
        self.assertEqual(summary["disarmed"], "max_returns")
        self.assertEqual(
            [entry["outcome"] for entry in steals(guard)], ["returned", "max_returns"]
        )

    def test_a_give_back_that_lands_elsewhere_is_logged(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.z = [OTHER, PERSON]  # Windows will activate another application
        self.desk.steal(CLIENT)
        self.detect(guard)
        (entry,) = steals(guard)
        self.assertEqual(entry["outcome"], "elsewhere")

    def test_a_give_back_that_leaves_dayz_in_front_is_logged_as_failed(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.activates_next = False  # the minimize activates nothing
        self.desk.steal(CLIENT)
        self.detect(guard)
        summary = guard.summary()
        self.assertEqual(summary["failed"], 1)
        (entry,) = steals(guard)
        self.assertEqual(entry["outcome"], "failed")


class ArmLogTest(_GuardCase):
    def test_each_arming_logs_its_delay_and_what_was_in_front(self) -> None:
        self.desk.front = CLIENT  # DayZ took the front before the guard was armed
        guard = self.guard()
        guard.add_pids([CLIENT_PID], foreground_before=PERSON, arm_delay_s=4.123)
        self.clock.now += 10.0
        self.desk.front = PERSON
        self.assertTrue(
            guard.extend([SERVER_PID], foreground_before=PERSON, arm_delay_s=0.5)
        )
        self.clock.now += 1.0
        self.desk.front = OTHER
        self.assertTrue(guard.extend([SERVER_PID], foreground_before=PERSON))
        self.clock.now += 1.0
        self.desk.front = 0
        self.assertTrue(guard.extend([SERVER_PID]))
        log = guard.evidence()["entries"]
        self.assertEqual(len(log), 4)
        self.assert_entry(
            log[0],
            {"kind": "arm", "at_s": 0.0, "arm_delay_s": 4.12, "front_at_arm": "run"},
        )
        self.assert_entry(
            log[1],
            {"kind": "arm", "at_s": 10.0, "arm_delay_s": 0.5, "front_at_arm": "person"},
        )
        self.assert_entry(
            log[2],
            {"kind": "arm", "at_s": 11.0, "arm_delay_s": None, "front_at_arm": "other"},
        )
        self.assert_entry(
            log[3],
            {"kind": "arm", "at_s": 12.0, "arm_delay_s": None, "front_at_arm": "none"},
        )

    def test_an_extend_refused_by_a_disarmed_guard_logs_nothing(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID], arm_delay_s=1.0)
        guard.disarm("foreground_verb")
        self.assertFalse(guard.extend([SERVER_PID], arm_delay_s=2.0))
        self.assertEqual(len(guard.evidence()["entries"]), 1)

    def test_arm_and_steal_entries_share_one_log_in_order(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID], foreground_before=PERSON, arm_delay_s=2.0)
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.detect(guard)
        self.tick(guard)
        self.assertTrue(guard.extend([SERVER_PID], arm_delay_s=3.0))
        log = guard.evidence()["entries"]
        self.assertEqual([entry["kind"] for entry in log], ["arm", "steal", "arm"])
        self.assertLessEqual(log[0]["at_s"], log[1]["at_s"])
        self.assertLessEqual(log[1]["at_s"], log[2]["at_s"])

    def test_times_never_run_backwards_when_a_steal_lands_during_an_arming(
        self,
    ) -> None:
        clock = _HookClock()
        self.clock = clock
        guard = self.guard(clock=clock)
        guard.add_pids([CLIENT_PID], foreground_before=PERSON)
        self.tick(guard)
        clock.now = START + 0.1

        def steal_meanwhile() -> None:
            clock.now = START + 0.2
            self.desk.steal(CLIENT)
            guard.step()  # another thread polls between the arming's clock read and its lock

        clock.hook = steal_meanwhile
        self.assertTrue(guard.extend([SERVER_PID], arm_delay_s=1.0))
        log = guard.evidence()["entries"]
        self.assertEqual([entry["kind"] for entry in log], ["arm", "steal", "arm"])
        times = [entry["at_s"] for entry in log]
        self.assertEqual(times, sorted(times))


class LogBoundAndShapeTest(_GuardCase):
    def test_the_log_keeps_sixteen_entries_and_counts_the_rest(self) -> None:
        self.assertEqual(launch_focus.LOG_MAX, 16)
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        for _ in range(20):
            self.desk.steal(PERSON)  # the person's window is in front again
            self.tick(guard)
            self.desk.keys = True  # Alt+Tab to DayZ
            self.desk.steal(CLIENT)
            self.tick(guard)
            self.desk.keys = False
        summary = guard.summary()
        self.assertEqual((summary["steals"], summary["user_switched"]), (20, 20))
        evidence = guard.evidence()
        self.assertEqual(set(evidence), {"entries", "dropped"})
        self.assertEqual(len(evidence["entries"]), 16)
        self.assertEqual(evidence["dropped"], 5)
        self.assertEqual(evidence["entries"][0]["kind"], "arm")

    def test_a_fresh_guard_has_an_empty_log(self) -> None:
        self.assertEqual(self.guard().evidence(), {"entries": [], "dropped": 0})

    def test_entries_hold_only_categories_numbers_and_flags(self) -> None:
        guard = self.guard(max_returns=1)
        guard.add_pids([CLIENT_PID], foreground_before=PERSON, arm_delay_s=3.0)
        self.tick(guard)
        self.desk.mouse = True
        self.desk.cursor = OTHER
        self.tick(guard)
        self.desk.mouse = False
        self.desk.steal(CLIENT)
        self.detect(guard)  # user_switched
        self.desk.steal(PERSON)
        self.tick(guard)
        self.tick(guard, 1.0)
        self.desk.steal(CLIENT)
        self.detect(guard)  # returned
        evidence = guard.evidence()
        self.assertEqual(json.loads(json.dumps(evidence)), evidence)
        self.assertEqual(len(evidence["entries"]), 3)
        for entry in evidence["entries"]:
            if entry["kind"] == "arm":
                self.assertEqual(set(entry), ARM_KEYS)
                self.assertIn(entry["front_at_arm"], CATEGORIES)
                continue
            self.assertEqual(entry["kind"], "steal")
            self.assertEqual(set(entry), STEAL_KEYS)
            self.assertIn(entry["outcome"], OUTCOMES)
            self.assertIn(entry["input"], {"key", "mouse", "none"})
            self.assertIn(entry["click_window"], CATEGORIES | {None})
            self.assertIn(entry["detect_window"], CATEGORIES)
            self.assertIn(entry["click_held"], {True, False, None})
            self.assertIn(entry["click_person_changed"], {True, False, None})
            self.assertIsInstance(entry["press_rule_switched"], bool)
            for key in ("at_s", "key_age_s", "mouse_age_s", "click_age_s", "poll_gap_s"):
                if entry[key] is not None:
                    self.assertIsInstance(entry[key], (int, float), key)
                    self.assertNotIsInstance(entry[key], bool, key)
        self.assertIsInstance(evidence["dropped"], int)
        self.assertNotIsInstance(evidence["dropped"], bool)

    def test_evidence_hands_out_a_copy_of_the_log(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID], arm_delay_s=1.0)
        first = guard.evidence()
        first["entries"][0]["front_at_arm"] = "changed"
        first["entries"].append({"kind": "steal"})
        second = guard.evidence()
        self.assertEqual(len(second["entries"]), 1)
        self.assertNotEqual(second["entries"][0]["front_at_arm"], "changed")

    def test_the_summary_keeps_its_counters_only(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID], arm_delay_s=1.0)
        self.assertNotIn("entries", guard.summary())
        self.assertNotIn("log", guard.summary())


class CursorFailureTest(_GuardCase):
    def test_a_cursor_read_that_raises_counts_as_no_window(self) -> None:
        guard = self.guard(fns=replace(self.desk.fns(), cursor_window=_broken_cursor))
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.mouse = True
        self.tick(guard)  # the press: the cursor read raises
        self.desk.mouse = False
        self.desk.steal(CLIENT)
        self.detect(guard)  # must not raise
        summary = guard.summary()
        self.assertEqual(summary["user_switched"], 1)
        (entry,) = steals(guard)
        self.assertEqual((entry["click_window"], entry["detect_window"]), ("none", "none"))

    def test_a_cursor_read_that_raises_does_not_stop_a_give_back(self) -> None:
        guard = self.guard(fns=replace(self.desk.fns(), cursor_window=_broken_cursor))
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.detect(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT])
        (entry,) = steals(guard)
        self.assertEqual((entry["outcome"], entry["detect_window"]), ("returned", "none"))


class _BridgeProbe:
    """The bridge row a start reads: a client that has not polled for a long time."""

    def status_snapshot(self, now: object = None) -> dict[str, object]:
        return {
            "peers": {
                "client": {
                    "last_poll_age_s": 999.0,
                    "bound_last_poll_age_s": None,
                    "binding_state": None,
                }
            }
        }


class _SlowLauncher(FakeLauncher):
    """A launcher call that takes `seconds` on the focus clock."""

    def __init__(self, clock: FakeClock, seconds: float) -> None:
        super().__init__()
        self.clock = clock
        self.seconds = seconds

    def __call__(self, argv: list[str], cwd: str, window_style: str):
        self.clock.now += self.seconds
        return super().__call__(argv, cwd, window_style)


class ArmDelayWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._saved_shared_root = os.environ.get("DAYZ_MCP_SHARED_ROOT")
        os.environ["DAYZ_MCP_SHARED_ROOT"] = str(self.root / "shared-root")
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in (
            "DayZDiag_x64.exe",
            "DayZ_BE.exe",
            "DayZ_x64.exe",
            "DayZServer_x64.exe",
        ):
            (self.game / name).write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
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
        self.clock = FakeClock()
        self.launcher = _SlowLauncher(self.clock, 4.0)
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
        )
        self.lifecycle.bridge_probe = _BridgeProbe().status_snapshot
        self.desk = FakeDesktop()
        self.starts: list[object] = []
        self.lifecycle.focus_fns = self.desk.fns()
        self.lifecycle.focus_start = self.starts.append
        self.lifecycle.focus_clock = self.clock

    def tearDown(self) -> None:
        if self._saved_shared_root is None:
            os.environ.pop("DAYZ_MCP_SHARED_ROOT", None)
        else:
            os.environ["DAYZ_MCP_SHARED_ROOT"] = self._saved_shared_root
        self.temporary.cleanup()

    def request(self) -> dict[str, object]:
        exe = self.game / "DayZDiag_x64.exe"
        return {
            "argv": [str(exe), "-mission=test"],
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
            "replace_if_not_polling_since": int(time.time() * 1000),
        }

    def start(self) -> FocusGuard:
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        result = self.lifecycle.start_run(IDENTITY_A, self.token, self.request())
        self.assertNotIn("error", result)
        return self.lifecycle._focus_guards["run-1"]

    def test_the_arm_entry_carries_the_time_from_the_launcher_call_to_the_arming(
        self,
    ) -> None:
        guard = self.start()
        (entry,) = guard.evidence()["entries"]
        self.assertEqual(set(entry), ARM_KEYS)
        self.assertEqual(entry["kind"], "arm")
        self.assertEqual(entry["at_s"], 0.0)
        self.assertEqual(entry["arm_delay_s"], 4.0)
        # The person's window was in front before the launch and still is.
        self.assertEqual(entry["front_at_arm"], "person")

    def test_a_recorded_launch_time_is_used_once(self) -> None:
        guard = self.start()
        self.lifecycle._arm_launch_focus(IDENTITY_A, "run-1", 42)  # no new launch
        log = guard.evidence()["entries"]
        self.assertEqual([entry["kind"] for entry in log], ["arm", "arm"])
        self.assertEqual(log[0]["arm_delay_s"], 4.0)
        self.assertIsNone(log[1]["arm_delay_s"])

    def test_an_arming_without_a_launch_has_no_delay(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, "run-x", 41)
        guard = self.lifecycle._focus_guards["run-x"]
        (entry,) = guard.evidence()["entries"]
        self.assertIsNone(entry["arm_delay_s"])

    def test_a_failed_clock_read_forgets_an_earlier_launch_of_the_run(self) -> None:
        self.lifecycle._remember_focus_launch("run-x")  # a launch at 1000 that never armed

        def broken_clock() -> float:
            raise OSError("no clock")

        self.clock.now = START + 10.0
        self.lifecycle.focus_clock = broken_clock
        self.lifecycle._remember_focus_launch("run-x")  # the next launch: its read fails
        self.lifecycle.focus_clock = self.clock
        self.clock.now = START + 14.0
        self.lifecycle._arm_launch_focus(IDENTITY_A, "run-x", 41)
        guard = self.lifecycle._focus_guards["run-x"]
        (entry,) = guard.evidence()["entries"]
        self.assertIsNone(entry["arm_delay_s"])

    def test_the_guard_row_carries_the_evidence(self) -> None:
        guard = self.start()
        guard.disarm("foreground_verb")
        self.starts[0]()
        (row,) = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_focus_guard"
        ]
        self.assertEqual(row["focus"], guard.summary())
        self.assertEqual(row["focus_evidence"], guard.evidence())
        self.assertEqual(row["focus_evidence"]["entries"][0]["arm_delay_s"], 4.0)


class AuditWriterTest(_GuardCase):
    def test_the_log_survives_the_audit_writer_unredacted(self) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID], foreground_before=PERSON, arm_delay_s=2.5)
        self.tick(guard)
        self.desk.keys = True
        self.desk.steal(CLIENT)
        self.detect(guard)
        summary = guard.summary()
        evidence = guard.evidence()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "runtime" / "audit").mkdir(parents=True)
            paths = RuntimePaths(
                root / "runtime",
                root / "runtime" / "audit",
                root / "runtime" / "coordination.json",
                root / "runtime" / "runs.json",
            )
            writer = JsonlAuditWriter(paths, "gen-intent")
            writer.write(
                {
                    "event": "lifecycle_focus_guard",
                    "reason": "window_elapsed",
                    "duration_s": 0.0,
                    "decision": "watched",
                    "run_id": "run-1",
                    "focus": summary,
                    "focus_evidence": evidence,
                }
            )
            text = writer.current_path.read_text(encoding="utf-8")
        (line,) = text.splitlines()
        self.assertNotIn("[REDACTED]", line)
        row = json.loads(line)
        self.assertEqual(row["focus_evidence"], evidence)
        self.assertEqual(
            [entry["kind"] for entry in row["focus_evidence"]["entries"]],
            ["arm", "steal"],
        )


class DocsTest(unittest.TestCase):
    def test_the_changelog_names_the_new_row_fields(self) -> None:
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        for word in (
            "focus_evidence",
            "click_window",
            "click_held",
            "click_person_changed",
            "poll_gap_s",
            "detect_window",
            "press_rule_switched",
            "arm_delay_s",
            "front_at_arm",
        ):
            self.assertIn(word, text, word)


if __name__ == "__main__":
    unittest.main()
