"""A normal DayZ launch locks the foreground and unlocks it 30 s after the last one (inbox fade, 75e7).

No test calls LockSetForegroundWindow: the lock and its timer are fakes.
"""
from __future__ import annotations

import ctypes
import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

from dayz_mcp import box_admission, daemon, foreground_lock, loopback
from dayz_mcp.foreground_lock import LSFW_LOCK, LSFW_UNLOCK, ForegroundLock
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.fence_helpers import bind_both_peers
from tests.process_lifecycle_helpers import (
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate


class _Timer:
    def __init__(self, interval, function, args=(), startable: bool = True) -> None:
        self.interval = interval
        self.function = function
        self.args = args
        self.startable = startable
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        if not self.startable:
            raise RuntimeError("can't start new thread")
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self.function(*self.args)


class _Lock:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.calls: list[int] = []
        self.timers: list[_Timer] = []
        # "timer" or "start": the next release cannot be scheduled.
        self.fail: str | None = None
        # When set, how many launches each call came after.
        self.launcher: FakeLauncher | None = None
        self.launches_before: list[int] = []

    def __call__(self, code: int) -> bool:
        self.calls.append(code)
        if self.launcher is not None:
            self.launches_before.append(len(self.launcher.calls))
        return self.answer

    def timer(self, interval, function, args=()) -> _Timer:
        if self.fail == "timer":
            raise RuntimeError("can't start new thread")
        timer = _Timer(interval, function, args, startable=self.fail != "start")
        self.timers.append(timer)
        return timer


class ForegroundLockTest(unittest.TestCase):
    def test_a_hold_locks_and_releases_30_s_later(self) -> None:
        for answer in (True, False):
            with self.subTest(answer=answer):
                lock = _Lock(answer)
                held = ForegroundLock(lock, timer=lock.timer)

                self.assertIs(held.hold(), answer)
                self.assertEqual(lock.calls, [LSFW_LOCK])
                (timer,) = lock.timers
                self.assertEqual(timer.interval, 30.0)
                self.assertTrue(timer.daemon and timer.started)

                timer.fire()
                self.assertEqual(lock.calls, [LSFW_LOCK, LSFW_UNLOCK])

    def test_a_later_hold_moves_the_release(self) -> None:
        lock = _Lock()
        held = ForegroundLock(lock, timer=lock.timer)

        held.hold()
        held.hold()
        first, second = lock.timers
        self.assertTrue(first.cancelled)
        # A first timer that fired anyway, before the second hold cancelled it.
        first.fire()
        self.assertEqual(lock.calls, [LSFW_LOCK, LSFW_LOCK])
        second.fire()
        self.assertEqual(lock.calls, [LSFW_LOCK, LSFW_LOCK, LSFW_UNLOCK])

    def test_a_release_it_cannot_schedule_unlocks_at_once(self) -> None:
        for fail in ("timer", "start"):
            with self.subTest(fail=fail, hold="first"):
                lock = _Lock()
                lock.fail = fail
                held = ForegroundLock(lock, timer=lock.timer)

                with self.assertRaisesRegex(RuntimeError, "can't start new thread"):
                    held.hold()
                self.assertEqual(lock.calls, [LSFW_LOCK, LSFW_UNLOCK])
            with self.subTest(fail=fail, hold="later"):
                lock = _Lock()
                held = ForegroundLock(lock, timer=lock.timer)
                held.hold()
                earlier = lock.timers[0]
                lock.fail = fail

                with self.assertRaisesRegex(RuntimeError, "can't start new thread"):
                    held.hold()
                self.assertTrue(earlier.cancelled)
                self.assertEqual(lock.calls, [LSFW_LOCK, LSFW_LOCK, LSFW_UNLOCK])
                # The earlier release, had it fired anyway, has nothing left to do.
                earlier.fire()
                self.assertEqual(lock.calls, [LSFW_LOCK, LSFW_LOCK, LSFW_UNLOCK])

    def test_without_the_call_there_is_nothing_to_hold(self) -> None:
        def no_timer(*_args, **_kwargs):
            raise AssertionError("no lock, no timer")

        self.assertIsNone(ForegroundLock(None, timer=no_timer).hold())
        with patch.object(foreground_lock.sys, "platform", "linux"):
            self.assertIsNone(foreground_lock.real_lock_fn())

    def test_on_windows_it_binds_lock_set_foreground_window(self) -> None:
        class _Call:
            argtypes = None
            restype = None

            def __init__(self) -> None:
                self.codes: list[int] = []
                self.answer = 1

            def __call__(self, code: int) -> int:
                self.codes.append(code)
                return self.answer

        call = _Call()
        user32 = SimpleNamespace(LockSetForegroundWindow=call)
        with (
            patch.object(foreground_lock.sys, "platform", "win32"),
            patch("ctypes.WinDLL", create=True, return_value=user32) as windll,
        ):
            bound = foreground_lock.real_lock_fn()
        windll.assert_called_once_with("user32", use_last_error=True)
        self.assertEqual((call.argtypes, call.restype), ([ctypes.c_uint], ctypes.c_int))
        self.assertIs(bound(LSFW_UNLOCK), True)
        self.assertIs(bound(LSFW_LOCK), True)
        self.assertEqual(call.codes, [LSFW_UNLOCK, LSFW_LOCK])
        # The BOOL: zero is a refusal, any other value a success.
        for answer, expected in ((0, False), (7, True)):
            with self.subTest(answer=answer):
                call.answer = answer
                self.assertIs(bound(LSFW_LOCK), expected)
        for label, windll_answer in (
            ("no user32", {"side_effect": OSError("no user32")}),
            ("no call", {"return_value": SimpleNamespace()}),
        ):
            with self.subTest(binding=label):
                with (
                    patch.object(foreground_lock.sys, "platform", "win32"),
                    patch("ctypes.WinDLL", create=True, **windll_answer),
                ):
                    self.assertIsNone(foreground_lock.real_lock_fn())


class _HeldLock:
    def __init__(self, launcher: FakeLauncher, answer: bool | None | Exception) -> None:
        self.launcher = launcher
        self.answer = answer
        self.launches_before: list[int] = []

    def hold(self) -> bool | None:
        self.launches_before.append(len(self.launcher.calls))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Audit(AuditSink):
    """An AuditSink that can look around before each event."""

    def __init__(self) -> None:
        super().__init__()
        self.look: Callable[[str], None] | None = None

    def __call__(self, event: dict[str, object]) -> bool:
        if self.look is not None:
            self.look(str(event.get("event")))
        return super().__call__(event)


class LaunchTakesTheLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        patcher = patch.dict(
            os.environ, {"DAYZ_MCP_SHARED_ROOT": str(self.root / "shared-root")}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.audit = _Audit()
        self.starts = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _start(
        self,
        answer: bool | None | Exception,
        window_style: str = "normal",
        *,
        lock_for: Callable[[FakeLauncher], object] | None = None,
        identity: bool = True,
        fail: str | None = None,
    ) -> object:
        """Start a run. ``identity`` False leaves DayZ without one, and ``fail``
        "launcher" or "manifest" fails the launcher's answer or the write of
        RUNNING: each fails the launch after the launcher."""

        # A box of its own for each start, so a subtest finds no earlier run.
        self.starts += 1
        runtime = self.root / f"runtime-{self.starts}"
        paths = RuntimePaths(
            runtime, runtime / "audit", runtime / "coordination.json", runtime / "runs.json"
        )
        self.audit.events.clear()
        coordinator = SessionCoordinator(
            token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=self.audit
        )
        _status, acquired = coordinator.acquire(IDENTITY_A, "lifecycle")
        guard = FakeGuard()
        launcher = FakeLauncher()
        if fail == "launcher":
            launcher.pid = 0
        if identity:
            guard.snapshots[launcher.pid] = snapshot(process(launcher.pid))
        held = lock_for(launcher) if lock_for is not None else _HeldLock(launcher, answer)
        manifest = RunManifestStore(paths)
        if fail == "manifest":
            replace = manifest.replace

            def failing(run: object) -> None:
                if getattr(run, "state", None) == "RUNNING":
                    raise OSError("manifest write failed")
                replace(run)

            manifest.replace = failing
        self.lifecycle = lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=coordinator,
            manifest=manifest,
            audit=self.audit,
            guard=guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=launcher,
            id_fn=lambda: "run-1",
            foreground_lock=held,
        )
        result = lifecycle.start_run(
            IDENTITY_A,
            acquired["lease_token"],
            {
                "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
                "cwd": str(self.game),
                "role": "client",
                "window_style": window_style,
                "label": "gate",
                "mod": "@SameMod",
                "profiles": "profiles",
                "mission": "test",
            },
        )
        self.assertIs(bool(result.get("ok")), identity and fail is None, result)
        self.assertEqual(len(launcher.calls), 1)
        return held

    def _outcome_row(self) -> dict[str, object]:
        (row,) = [e for e in self.audit.events if e["event"] == "lifecycle_start_outcome"]
        return row

    def test_a_normal_launch_takes_the_lock_before_dayz_starts(self) -> None:
        for answer, decision in ((True, "locked"), (False, "refused")):
            with self.subTest(answer=answer):
                held = self._start(answer)

                self.assertEqual(held.launches_before, [0])
                row = self._outcome_row()
                self.assertEqual(
                    (row["decision"], row["run_id"], row["foreground_lock"]),
                    ("started", "run-1", decision),
                )

    def test_a_lock_that_raises_leaves_the_launch_going(self) -> None:
        # _start checks that the run started and DayZ was launched once.
        held = self._start(RuntimeError("can't start new thread"))

        self.assertEqual(held.launches_before, [0])
        row = self._outcome_row()
        self.assertEqual((row["decision"], row["foreground_lock"]), ("started", "failed"))

    def test_the_lock_writes_no_row_of_its_own(self) -> None:
        # Codex L6, L11: what Windows answered rides on the outcome row, so
        # the lock adds no audit write a busy writer could keep waiting,
        # before DayZ starts or once the launch is over.
        for identity in (True, False):
            self._start(None, identity=identity)
            without = [e["event"] for e in self.audit.events]
            self.assertEqual(without[-1], "lifecycle_start_outcome")
            for answer in (True, False, RuntimeError("can't start new thread")):
                with self.subTest(identity=identity, answer=answer):
                    self._start(answer, identity=identity)
                    self.assertEqual([e["event"] for e in self.audit.events], without)

    def test_a_launch_that_took_the_lock_returns_with_nothing_held(self) -> None:
        # Codex L12, L13: its command, its operation and the box admission,
        # however the launch ends once the lock is taken.
        def operation_lock_taken() -> bool:
            taken: list[bool] = []

            def probe() -> None:
                got = self.lifecycle._operation_lock.acquire(blocking=False)
                if got:
                    self.lifecycle._operation_lock.release()
                taken.append(not got)

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(5)
            return taken != [False]

        for answer in (True, RuntimeError("can't start new thread")):
            for identity, fail in (
                (True, None), (False, None), (True, "launcher"), (True, "manifest")
            ):
                with self.subTest(answer=answer, identity=identity, fail=fail):
                    self._start(answer, identity=identity, fail=fail)

                    active = self.lifecycle.coordinator._active
                    self.assertEqual((active.committed_commands, active.operation_pins), ({}, {}))
                    self.assertFalse(operation_lock_taken())
                    self.assertEqual(box_admission._holders, 0)

    def test_the_answer_is_written_once_the_launch_has_settled(self) -> None:
        # Codex L6: never before DayZ's start, nor while the run is STARTING;
        # and on every outcome a launch can have once the lock is taken.
        for launch, identity, fail, state in (
            ("started", True, None, "RUNNING"),
            ("no identity", False, None, "EXITED"),
            ("launcher", True, "launcher", "EXITED"),
            ("manifest", True, "manifest", "EXITED"),
        ):
            with self.subTest(launch=launch):
                seen: list[tuple[int, str]] = []

                def look(event: str) -> None:
                    if event == "lifecycle_start_outcome":
                        run = self.lifecycle.manifest.get("run-1")
                        seen.append((len(self.lifecycle.launcher.calls), run.state))

                self.audit.look = look

                self._start(True, identity=identity, fail=fail)

                self.assertEqual(seen, [(1, state)])
                self.assertEqual(self._outcome_row()["foreground_lock"], "locked")

    def test_the_lock_itself_under_a_launch(self) -> None:
        # Codex L8: ForegroundLock, its call and its timer faked, through a launch.
        for fail, decision, calls in (
            (None, "locked", [LSFW_LOCK]),
            ("timer", "failed", [LSFW_LOCK, LSFW_UNLOCK]),
            ("start", "failed", [LSFW_LOCK, LSFW_UNLOCK]),
        ):
            with self.subTest(fail=fail):
                lock = _Lock()
                lock.fail = fail

                def lock_for(launcher: FakeLauncher) -> ForegroundLock:
                    lock.launcher = launcher
                    return ForegroundLock(lock, timer=lock.timer)

                self._start(None, lock_for=lock_for)

                self.assertEqual(lock.calls, calls)
                self.assertEqual(lock.launches_before, [0] * len(calls))
                self.assertEqual(self._outcome_row()["foreground_lock"], decision)
                if fail is None:
                    (timer,) = lock.timers
                    self.assertEqual(timer.interval, 30.0)
                    timer.fire()
                    self.assertEqual(lock.calls, [LSFW_LOCK, LSFW_UNLOCK])

    def test_no_lock_to_take_leaves_the_outcome_as_it_was(self) -> None:
        held = self._start(None)

        self.assertEqual(held.launches_before, [0])
        self.assertNotIn("foreground_lock", self._outcome_row())

    def test_a_hidden_launch_takes_no_lock(self) -> None:
        held = self._start(True, "hidden")

        self.assertEqual(held.launches_before, [])
        self.assertNotIn("foreground_lock", self._outcome_row())


class DaemonWiringTest(unittest.TestCase):
    def test_the_daemon_lifecycle_holds_the_real_lock(self) -> None:
        def real() -> None:
            raise AssertionError("the test must not call the real lock")

        with TemporaryDirectory() as temporary:
            state = loopback.ServerState("key")
            bind_both_peers(state)
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": temporary}),
                patch.object(daemon, "real_lock_fn", return_value=real),
            ):
                daemon._activate_server_coordination(state, "new-generation")

        held = state.lifecycle.foreground_lock
        self.assertIsInstance(held, ForegroundLock)
        self.assertIs(held._lock_fn, real)


if __name__ == "__main__":
    unittest.main()
