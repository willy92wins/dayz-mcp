"""54b8: a denied dayz_test_foreground says what it read right after the ask.

Inbox fb-20261122-132902-54b8 (LFSkateboard): dayz_test_foreground answered {"state": "denied", "granted": false,
"held_s": 0.0} twice, and nothing in the answer said why Windows refused. Windows grants SetForegroundWindow only in
the cases Microsoft documents; the foreground lock time-out (SystemParametersInfo SPI_GETFOREGROUNDLOCKTIMEOUT) and the
last input (GetLastInputInfo against GetTickCount) are what decide most refusals, and GetLastError says what the call
itself reported. On this PC the live lock time-out read 2147483647 ms on 2026-11-20 and 0 on 2026-11-23.

Contract pinned here, through the real foreground_run path with a fake Win32 layer:
- Win32ForegroundFns gains three optional reads: last_error, foreground_lock_timeout_ms and input_idle_s;
- last_error is read right after SetForegroundWindow, before any other Win32 call;
- a denied answer adds last_error, foreground_lock_timeout_ms, input_idle_s and minimized (IsIconic on the window);
  a read that fails, or that the layer does not have, gives None and the answer still comes;
- a foreground or lost answer is unchanged, and the lock and idle reads are not made on it;
- bind_real_win32 binds the three reads on Windows;
- the published description names the four fields and what to do (retry with the PC unused, or the person clicks the
  window).
"""

from __future__ import annotations

import dataclasses
import functools
import re
import sys
import unittest

from dayz_mcp import window_foreground
from dayz_mcp.effective_schema import resolve_effective_schemas
# Imported as a module so that unittest does not collect ForegroundRunTest's own tests here.
from tests import test_foreground_verb as _verb

BASE_KEYS = {"run_id", "state", "granted", "held_s"}
REASON_KEYS = {"last_error", "foreground_lock_timeout_ms", "input_idle_s", "minimized"}


class _ReasonDesktop(_verb._Desktop):
    """The foreground tests' fake desktop, plus the three new reads."""

    def __init__(self) -> None:
        super().__init__()
        self.values = {"last_error": 5, "foreground_lock_timeout_ms": 2147483647, "input_idle_s": 12.5}
        self.failing: set[str] = set()

    def foreground_fns(self) -> window_foreground.Win32ForegroundFns:
        base = super().foreground_fns()

        def read(name: str):
            def call():
                self.calls.append(name)
                if name in self.failing:
                    raise OSError("read failed: " + name)
                return self.values[name]
            return call

        return dataclasses.replace(
            base,
            last_error=read("last_error"),
            foreground_lock_timeout_ms=read("foreground_lock_timeout_ms"),
            input_idle_s=read("input_idle_s"),
        )


class DeniedReasonTest(unittest.TestCase):
    def setUp(self) -> None:
        # ForegroundRunTest's lifecycle, run records and windows, with this module's desktop.
        self.base = _verb.ForegroundRunTest("test_a_refused_ask_reads_denied")
        self.base.setUp()
        self.addCleanup(self.base.tearDown)
        self.desktop = _ReasonDesktop()
        self.desktop.add(410, 41, area=99999)
        self.desktop.add(420, 42, area=5000)
        self.desktop.add(421, 42, cls="ConsoleWindowClass", area=9000)
        self.base.lifecycle.window_fns = self.desktop.window_fns()
        self.base.lifecycle.foreground_fns = self.desktop.foreground_fns()
        self.desktop.operation_lock = self.base.lifecycle._operation_lock
        self.base.add_run()

    def deny(self) -> None:
        self.desktop.grant = False
        self.desktop.shows = False

    def test_a_denied_answer_says_what_it_read(self) -> None:
        self.deny()
        result = self.base.foreground(hold_s=1)
        self.assertEqual(set(result), BASE_KEYS | REASON_KEYS)
        self.assertEqual((result["run_id"], result["state"], result["granted"], result["held_s"]),
                         (_verb.RUN_ID, "denied", False, 0.0))
        self.assertEqual(result["last_error"], 5)
        self.assertEqual(result["foreground_lock_timeout_ms"], 2147483647)
        self.assertEqual(result["input_idle_s"], 12.5)
        self.assertIs(result["minimized"], False)

    def test_last_error_is_read_right_after_the_ask(self) -> None:
        self.deny()
        self.base.foreground(hold_s=1)
        at = self.desktop.calls.index("ask")
        self.assertEqual(self.desktop.calls[at + 1], "last_error", self.desktop.calls)

    def test_a_read_that_fails_gives_none_and_the_answer_still_comes(self) -> None:
        for name in ("last_error", "foreground_lock_timeout_ms", "input_idle_s"):
            with self.subTest(read=name):
                self.setUp()
                self.desktop.failing = {name}
                self.base.lifecycle.foreground_fns = self.desktop.foreground_fns()
                self.deny()
                result = self.base.foreground(hold_s=1)
                self.assertEqual(result["state"], "denied")
                self.assertIsNone(result[name])
                for other in REASON_KEYS - {name, "minimized"}:
                    self.assertIsNotNone(result[other], other)

    def test_a_layer_without_the_reads_gives_none(self) -> None:
        self.base.lifecycle.foreground_fns = _verb._Desktop.foreground_fns(self.desktop)
        self.deny()
        result = self.base.foreground(hold_s=1)
        self.assertEqual(result["state"], "denied")
        self.assertEqual((result["last_error"], result["foreground_lock_timeout_ms"], result["input_idle_s"]),
                         (None, None, None))
        self.assertIs(result["minimized"], False)

    def test_a_foreground_answer_is_unchanged_and_reads_neither_lock_nor_idle(self) -> None:
        result = self.base.foreground(hold_s=0)
        self.assertEqual(result, {"run_id": _verb.RUN_ID, "state": "foreground", "granted": True, "held_s": 0.0})
        self.assertNotIn("foreground_lock_timeout_ms", self.desktop.calls)
        self.assertNotIn("input_idle_s", self.desktop.calls)

    def test_a_lost_answer_is_unchanged(self) -> None:
        self.desktop.taken_after = 1
        result = self.base.foreground(hold_s=1)
        self.assertEqual(result["state"], "lost")
        self.assertEqual(set(result), BASE_KEYS)


@unittest.skipUnless(sys.platform == "win32", "binds user32 and kernel32")
class RealBindingTest(unittest.TestCase):
    def test_the_real_layer_reads_the_three_values(self) -> None:
        fns = window_foreground.bind_real_win32()
        lock = fns.foreground_lock_timeout_ms()
        idle = fns.input_idle_s()
        error = fns.last_error()
        self.assertIsInstance(lock, int)
        self.assertGreaterEqual(lock, 0)
        self.assertIsInstance(idle, float)
        self.assertGreaterEqual(idle, 0.0)
        self.assertIsInstance(error, int)
        self.assertGreaterEqual(error, 0)


@functools.lru_cache(maxsize=1)
def _description() -> str:
    return resolve_effective_schemas()["dayz_test_foreground"]["description"]


class DescriptionTest(unittest.TestCase):
    def test_the_description_names_the_four_fields(self) -> None:
        for name in sorted(REASON_KEYS):
            with self.subTest(field=name):
                self.assertIn(name, _description())

    def test_the_description_says_what_to_do_after_a_denial(self) -> None:
        text = _description()
        self.assertRegex(text, r"\bretry\b")
        self.assertRegex(text, r"\bclicks?\b")


if __name__ == "__main__":
    unittest.main()
