"""54b8: a denied dayz_test_foreground says what it read right after the ask.

Inbox fb-20261122-132902-54b8 (LFSkateboard): dayz_test_foreground answered {"state": "denied", "granted": false,
"held_s": 0.0} twice, and nothing in the answer said why Windows refused. Windows grants SetForegroundWindow only in
the cases Microsoft documents; the foreground lock time-out (SystemParametersInfo SPI_GETFOREGROUNDLOCKTIMEOUT) and the
last input (GetLastInputInfo against GetTickCount) are what decide most refusals, and GetLastError says what the call
itself reported. On this PC the live lock time-out read 2147483647 ms on 2026-11-20 and 0 on 2026-11-23.

Contract pinned here, through the real foreground_run path with a fake Win32 layer:
- Win32ForegroundFns gains three optional reads: last_error, foreground_lock_timeout_ms and input_idle_s;
- last_error is read right after SetForegroundWindow, before any other call on the layer, a foreground read included
  (a GetForegroundWindow call in between would overwrite the thread's last error);
- a denied answer adds last_error, foreground_lock_timeout_ms, input_idle_s and minimized (IsIconic on the window);
  a read that fails, or that the layer does not have, gives None and the answer still comes;
- a foreground or lost answer is unchanged, and the lock and idle reads are not made on it;
- bind_real_win32 binds the three reads on Windows;
- the published description names the four fields and what to do (retry with the PC unused, or the person clicks the
  window).

The module builds its own lifecycle and fake desktop from the shared helper modules: the suite does not let one test
module import another (test_suite_structure).
"""

from __future__ import annotations

import dataclasses
import functools
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import window_close, window_foreground
from dayz_mcp.effective_schema import resolve_effective_schemas
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.process_lifecycle_helpers import IDENTITY_A, AuditSink, FakeGuard, FakeLauncher, process, snapshot
from tests.steam_helpers import FakeSteamGate

RUN_ID = "run-front"
OTHER = 7
CLIENT_WINDOW = 420
BASE_KEYS = {"run_id", "state", "granted", "held_s"}
REASON_KEYS = {"last_error", "foreground_lock_timeout_ms", "input_idle_s", "minimized"}


class _Desktop:
    """Top-level windows, the foreground window and every call the verb makes on the layer, in order."""

    def __init__(self) -> None:
        self.windows = {
            410: {"pid": 41, "cls": "DayZ", "area": 99999, "iconic": False},
            CLIENT_WINDOW: {"pid": 42, "cls": "DayZ", "area": 5000, "iconic": False},
            421: {"pid": 42, "cls": "ConsoleWindowClass", "area": 9000, "iconic": False},
        }
        self.front = OTHER
        self.grant = True
        self.shows = True
        self.taken_after: int | None = None
        self.reads = 0
        self.calls: list[str] = []
        self.values = {"last_error": 5, "foreground_lock_timeout_ms": 2147483647, "input_idle_s": 12.5}
        self.failing: set[str] = set()

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
            is_visible=lambda hwnd: hwnd in self.windows,
            post_message=post_message,
        )

    def foreground_fns(self, *, with_reads: bool = True) -> window_foreground.Win32ForegroundFns:
        def is_iconic(hwnd: int) -> bool:
            self.calls.append("iconic")
            return bool(self.windows[hwnd]["iconic"])

        def set_foreground(hwnd: int) -> bool:
            self.calls.append("ask")
            if self.shows:
                self.front = hwnd
            return self.grant

        def foreground() -> int:
            self.calls.append("front")
            self.reads += 1
            if self.taken_after is not None and self.reads > self.taken_after:
                self.front = OTHER
            return self.front

        base = window_foreground.Win32ForegroundFns(
            class_name=lambda hwnd: str(self.windows[hwnd]["cls"]),
            area=lambda hwnd: int(self.windows[hwnd]["area"]),
            is_iconic=is_iconic,
            set_foreground=set_foreground,
            foreground=foreground,
        )
        if not with_reads:
            return base

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
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        game = root / "DayZ"
        game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe", "DayZServer_x64.exe"):
            (game / name).write_bytes(b"")
        paths = RuntimePaths(root / "runtime", root / "runtime" / "audit", root / "runtime" / "coordination.json",
                             root / "runtime" / "runs.json")
        audit = AuditSink()
        self.coordinator = SessionCoordinator(token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=audit)
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token = acquired["lease_token"]
        store = RunManifestStore(paths)
        guard = FakeGuard()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=store,
            audit=audit,
            guard=guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game,
            launcher=FakeLauncher(),
            id_fn=lambda: "run-1",
        )
        self.desktop = _Desktop()
        self.lifecycle.window_fns = self.desktop.window_fns()
        self.lifecycle.foreground_fns = self.desktop.foreground_fns()
        records = (process(41, role="server"), process(42))
        for record in records:
            guard.snapshots[record.pid] = snapshot(record)
        store.add(RunRecord(RUN_ID, "A", "lease-A", "RUNNING", "same", "@SameMod", "profiles", "mission",
                            list(records)))

    def foreground(self, hold_s: float) -> dict[str, object]:
        return self.lifecycle.foreground_run(IDENTITY_A, self.token, RUN_ID, hold_s)

    def deny(self) -> None:
        self.desktop.grant = False
        self.desktop.shows = False

    def test_a_denied_answer_says_what_it_read(self) -> None:
        self.deny()
        result = self.foreground(1)
        self.assertEqual(set(result), BASE_KEYS | REASON_KEYS)
        self.assertEqual((result["run_id"], result["state"], result["granted"], result["held_s"]),
                         (RUN_ID, "denied", False, 0.0))
        self.assertEqual(result["last_error"], 5)
        self.assertEqual(result["foreground_lock_timeout_ms"], 2147483647)
        self.assertEqual(result["input_idle_s"], 12.5)
        self.assertIs(result["minimized"], False)

    def test_last_error_is_read_right_after_the_ask(self) -> None:
        self.deny()
        self.foreground(1)
        at = self.desktop.calls.index("ask")
        self.assertEqual(self.desktop.calls[at + 1], "last_error", self.desktop.calls)

    def test_a_read_that_fails_gives_none_and_the_answer_still_comes(self) -> None:
        for name in ("last_error", "foreground_lock_timeout_ms", "input_idle_s"):
            with self.subTest(read=name):
                self.desktop = _Desktop()
                self.desktop.failing = {name}
                self.lifecycle.window_fns = self.desktop.window_fns()
                self.lifecycle.foreground_fns = self.desktop.foreground_fns()
                self.deny()
                result = self.foreground(1)
                self.assertEqual(result["state"], "denied")
                self.assertIsNone(result[name])
                for other in REASON_KEYS - {name, "minimized"}:
                    self.assertIsNotNone(result[other], other)

    def test_a_layer_without_the_reads_gives_none(self) -> None:
        self.lifecycle.foreground_fns = self.desktop.foreground_fns(with_reads=False)
        self.deny()
        result = self.foreground(1)
        self.assertEqual(result["state"], "denied")
        self.assertEqual((result["last_error"], result["foreground_lock_timeout_ms"], result["input_idle_s"]),
                         (None, None, None))
        self.assertIs(result["minimized"], False)

    def test_a_foreground_answer_is_unchanged_and_reads_neither_lock_nor_idle(self) -> None:
        result = self.foreground(0)
        self.assertEqual(result, {"run_id": RUN_ID, "state": "foreground", "granted": True, "held_s": 0.0})
        self.assertNotIn("foreground_lock_timeout_ms", self.desktop.calls)
        self.assertNotIn("input_idle_s", self.desktop.calls)

    def test_a_lost_answer_is_unchanged(self) -> None:
        self.desktop.taken_after = 1
        result = self.foreground(1)
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
