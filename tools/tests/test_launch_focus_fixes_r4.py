"""Fourth round of review fixes for the launch focus guard (inbox 5c8e, review of 39b7281).

Oracle frozen before the fix round. A minimize that is never seen keeps the guard
busy until it lands or its window is gone (the fence); every foreground read of a
give-back can end the steal; a replaced guard that is still busy stays in the
run's barrier. No test touches the desktop or starts a thread.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp.launch_focus import FocusFns, FocusGuard
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.launch_focus_helpers import (
    CLIENT,
    CLIENT_PID,
    OTHER_PID,
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
)
from tests.steam_helpers import FakeSteamGate

RUN_ID = "run-focus"


class _QueuedDesktop(FakeDesktop):
    """FakeDesktop whose minimize and show requests wait in the window's queue
    (ShowWindowAsync only posts them) until the window's thread runs."""

    def __init__(self) -> None:
        super().__init__()
        self.queued = False
        self.queue: list[tuple[str, int]] = []

    def minimize(self, hwnd: int) -> bool:
        if not self.queued:
            return super().minimize(hwnd)
        self.calls.append(("minimize", hwnd))
        self.queue.append(("minimize", hwnd))
        return True

    def show_no_activate(self, hwnd: int) -> bool:
        if not self.queued:
            return super().show_no_activate(hwnd)
        self.calls.append(("show_no_activate", hwnd))
        self.queue.append(("show", hwnd))
        return True

    def drain(self) -> None:
        for name, hwnd in self.queue:
            if name == "minimize":
                self._apply_minimize(hwnd)
            else:
                self.minimized.discard(hwnd)
        self.queue = []


class _GuardCase(unittest.TestCase):
    def setUp(self) -> None:
        self.desk = _QueuedDesktop()
        self.clock = FakeClock()

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
            "unresolved_s": 10.0,
        }
        options.update(overrides)
        return FocusGuard(**options)

    def tick(self, guard: FocusGuard, seconds: float = 0.025) -> None:
        self.clock.now += seconds
        guard.step()

    def fenced(self) -> FocusGuard:
        """A give-back whose minimize is still queued when unresolved_s runs out."""
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.queued = True
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.queue, [("minimize", CLIENT)])
        self.assertFalse(guard.wait_idle(0))
        return guard


class FenceTest(_GuardCase):
    def test_the_fence_lifts_when_the_window_is_gone(self) -> None:
        guard = self.fenced()
        del self.desk.pids[CLIENT]  # the client exited: its queue went with it
        self.tick(guard)
        self.assertTrue(guard.wait_idle(0))
        self.assertEqual(self.desk.shows(), [])

    def test_the_fence_lifts_when_the_handle_belongs_to_another_process(self) -> None:
        guard = self.fenced()
        self.desk.pids[CLIENT] = OTHER_PID  # the handle was reused: never show it
        self.desk.minimized.add(CLIENT)
        self.tick(guard)
        self.assertTrue(guard.wait_idle(0))
        self.assertEqual(self.desk.shows(), [])

    def test_a_disarm_never_lifts_the_fence(self) -> None:
        guard = self.fenced()
        guard.disarm("foreground_verb")
        self.tick(guard)
        self.assertFalse(guard.wait_idle(0))
        self.assertEqual(self.desk.queue, [("minimize", CLIENT)])

    def test_run_services_the_fence_after_the_disarm_and_returns_once_it_lifts(self) -> None:
        hung_until = self.clock.now + 15.0

        def sleep(seconds: float) -> None:
            self.clock.sleep(seconds)
            if len(self.clock.slept) > 20000:
                raise AssertionError("run() kept looping after the fence lifted")
            if self.clock.now >= hung_until:
                self.desk.drain()  # the DayZ thread runs again

        guard = self.guard(sleep=sleep)
        guard.add_pids([CLIENT_PID], foreground_before=PERSON)
        self.desk.queued = True
        self.desk.steal(CLIENT)
        summary = guard.run(live_pids=lambda: {CLIENT_PID})
        self.assertGreaterEqual(self.clock.now, hung_until)
        self.assertEqual((summary["restore_failed"], summary["disarmed"]), (1, "restore_failed"))
        self.assertTrue(guard.wait_idle(0))
        self.assertEqual(self.desk.queue, [])
        self.assertEqual(self.desk.minimized, set())
        self.assertEqual(self.desk.shows(), [CLIENT])
        self.assertEqual(self.desk.front, PERSON)


class LatchTest(unittest.TestCase):
    def test_a_minimize_that_lands_between_the_foreground_and_iconic_reads_ends_the_steal(
        self,
    ) -> None:
        desk = FakeDesktop()
        clock = FakeClock()
        desk.minimize_pending = True
        desk.restore_activates = True  # DayZ activates itself again when it is shown
        posted: list[float] = []
        desk.on_minimize = lambda hwnd: posted.append(clock.now)
        fns = desk.fns()

        def is_iconic(hwnd: int) -> bool:
            # The DayZ thread runs the minimize right after the guard read the foreground.
            if desk._pending and clock.now - posted[-1] >= 1.0:
                desk.deliver_pending()
            return fns.is_iconic(hwnd)

        guard = FocusGuard(
            fns=FocusFns(
                foreground=fns.foreground,
                window_pid=fns.window_pid,
                is_window=fns.is_window,
                is_iconic=is_iconic,
                minimize=fns.minimize,
                show_no_activate=fns.show_no_activate,
                idle_s=fns.idle_s,
                mouse_down=fns.mouse_down,
                switch_keys_down=fns.switch_keys_down,
                cursor_window=fns.cursor_window,
            ),
            clock=clock,
            sleep=clock.sleep,
            settle_s=1.0,
            max_returns=3,
        )
        guard.add_pids([CLIENT_PID])
        clock.now += 0.025
        guard.step()
        desk.steal(CLIENT)
        for _ in range(6):
            clock.now += 0.025
            guard.step()
        # Each minimize did take the front away, so each reactivation is a new steal up to the cap.
        self.assertEqual(desk.minimizes(), [CLIENT, CLIENT, CLIENT])
        summary = guard.summary()
        self.assertEqual((summary["failed"], summary["restore_failed"]), (3, 0))
        self.assertEqual(summary["disarmed"], "max_returns")
        self.assertTrue(guard.wait_idle(0))


class _LifecycleCase(unittest.TestCase):
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
        self.store = RunManifestStore(paths)
        self.desk = FakeDesktop()
        self.desk.idle = 1e9
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=FakeGuard(),
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game,
            launcher=FakeLauncher(),
            id_fn=lambda: "run-1",
            focus_fns=self.desk.fns(),
        )
        self.starts: list[object] = []
        self.lifecycle.focus_start = self.starts.append

    def tearDown(self) -> None:
        self.temporary.cleanup()


class ReplacedGuardBarrierTest(_LifecycleCase):
    def test_a_replaced_guard_that_is_still_busy_stays_in_the_runs_barrier(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, SERVER_PID)
        first = self.lifecycle._focus_guards[RUN_ID]
        first.wait_idle = lambda timeout_s: False  # its give-back has not completed
        self.assertIs(
            self.lifecycle._disarm_launch_focus(RUN_ID, "foreground_verb", wait_s=0.05), False
        )
        # Another keep launch of the run while that give-back is still in flight.
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID, PERSON)
        second = self.lifecycle._focus_guards[RUN_ID]
        self.assertIsNot(second, first)
        self.assertIs(
            self.lifecycle._disarm_launch_focus(RUN_ID, "foreground_verb", wait_s=0.05), False
        )
        first.wait_idle = lambda timeout_s: True  # the old give-back completed
        self.assertIs(
            self.lifecycle._disarm_launch_focus(RUN_ID, "foreground_verb", wait_s=0.05), True
        )

    def test_a_replaced_guard_that_is_idle_does_not_hold_the_barrier(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, SERVER_PID)
        first = self.lifecycle._focus_guards[RUN_ID]
        first.disarm("window_elapsed")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID, PERSON)
        self.assertIsNot(self.lifecycle._focus_guards[RUN_ID], first)
        self.assertIs(
            self.lifecycle._disarm_launch_focus(RUN_ID, "foreground_verb", wait_s=0.05), True
        )


if __name__ == "__main__":
    unittest.main()
