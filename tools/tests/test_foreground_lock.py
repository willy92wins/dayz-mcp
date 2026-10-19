"""A DayZ launch locks the foreground so DayZ does not take it (inbox fade, 75e7).

No test calls LockSetForegroundWindow: the lock and its timer are fakes.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dayz_mcp import daemon, foreground_lock, loopback
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
    def __init__(self, interval, function, args=()) -> None:
        self.interval = interval
        self.function = function
        self.args = args
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
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

    def __call__(self, code: int) -> bool:
        self.calls.append(code)
        return self.answer

    def timer(self, interval, function, args=()) -> _Timer:
        timer = _Timer(interval, function, args)
        self.timers.append(timer)
        return timer


class ForegroundLockTest(unittest.TestCase):
    def test_a_hold_locks_and_releases_hold_s_later(self) -> None:
        for answer in (True, False):
            with self.subTest(answer=answer):
                lock = _Lock(answer)
                held = ForegroundLock(lock, hold_s=30.0, timer=lock.timer)

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

    def test_without_the_call_there_is_nothing_to_hold(self) -> None:
        def no_timer(*_args, **_kwargs):
            raise AssertionError("no lock, no timer")

        self.assertIsNone(ForegroundLock(None, timer=no_timer).hold())
        with patch.object(foreground_lock.sys, "platform", "linux"):
            self.assertIsNone(foreground_lock.real_lock_fn())


class _HeldLock:
    def __init__(self, launcher: FakeLauncher, answer: bool | None) -> None:
        self.launcher = launcher
        self.answer = answer
        self.launches_before: list[int] = []

    def hold(self) -> bool | None:
        self.launches_before.append(len(self.launcher.calls))
        return self.answer


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
        self.audit = AuditSink()
        self.starts = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _start(self, answer: bool | None, window_style: str = "normal") -> _HeldLock:
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
        guard.snapshots[launcher.pid] = snapshot(process(launcher.pid))
        held = _HeldLock(launcher, answer)
        lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=coordinator,
            manifest=RunManifestStore(paths),
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
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(launcher.calls), 1)
        return held

    def _rows(self) -> list[dict[str, object]]:
        return [e for e in self.audit.events if e["event"] == "lifecycle_foreground_lock"]

    def test_a_normal_launch_takes_the_lock_before_dayz_starts(self) -> None:
        for answer, decision in ((True, "locked"), (False, "refused")):
            with self.subTest(answer=answer):
                held = self._start(answer)

                self.assertEqual(held.launches_before, [0])
                (row,) = self._rows()
                self.assertEqual(
                    (row["reason"], row["decision"], row["run_id"], row["role"]),
                    ("launch", decision, "run-1", "client"),
                )

    def test_no_lock_to_take_leaves_no_row(self) -> None:
        held = self._start(None)

        self.assertEqual(held.launches_before, [0])
        self.assertEqual(self._rows(), [])

    def test_a_hidden_launch_takes_no_lock(self) -> None:
        held = self._start(True, "hidden")

        self.assertEqual(held.launches_before, [])
        self.assertEqual(self._rows(), [])


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
