"""Second round of review fixes for the launch focus guard (inbox 5c8e, review of f64cc94).

Oracle frozen before the fix round. The desktop is tests.launch_focus_helpers.FakeDesktop;
the only threads are the two of the barrier test, which never sleep.
"""
from __future__ import annotations

import inspect
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import launch_focus, window_close, window_foreground
from dayz_mcp.launch_focus import FocusFns, FocusGuard
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.launch_focus_helpers import (
    CLIENT,
    CLIENT_PID,
    PERSON,
    SERVER,
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

RUN_ID = "run-focus"


def _with_window_pid(fns: FocusFns, window_pid) -> FocusFns:
    return FocusFns(
        foreground=fns.foreground,
        window_pid=window_pid,
        is_window=fns.is_window,
        is_iconic=fns.is_iconic,
        minimize=fns.minimize,
        show_no_activate=fns.show_no_activate,
        idle_s=fns.idle_s,
        mouse_down=fns.mouse_down,
        switch_keys_down=fns.switch_keys_down,
        cursor_window=fns.cursor_window,
    )


class AdmissionTest(unittest.TestCase):
    """A disarm that returns before the minimize is admitted means no minimize."""

    def test_a_disarm_after_verify_and_before_the_minimize_stops_it(self) -> None:
        desk = FakeDesktop()
        clock = FakeClock()
        state = {"verified": False, "guard": None}

        def verify(pid: int) -> bool:
            state["verified"] = True
            return pid == CLIENT_PID

        def window_pid(hwnd: int) -> int:
            # The owner read the give-back makes after verify: another thread disarms here.
            if state["verified"] and hwnd == CLIENT and state["guard"].armed:
                state["guard"].disarm("take")
            return desk.window_pid(hwnd)

        guard = FocusGuard(
            fns=_with_window_pid(desk.fns(), window_pid),
            clock=clock,
            sleep=clock.sleep,
            verify=verify,
        )
        state["guard"] = guard
        guard.add_pids([CLIENT_PID])
        clock.now += 0.025
        guard.step()
        desk.steal(CLIENT)
        clock.now += 0.025
        guard.step()
        self.assertTrue(state["verified"])
        self.assertEqual(desk.minimizes(), [])
        self.assertEqual(guard.summary()["disarmed"], "take")

    def test_a_disarm_from_another_thread_during_the_owner_read_stops_the_minimize(
        self,
    ) -> None:
        desk = FakeDesktop()
        clock = FakeClock()
        at_read = threading.Barrier(2, timeout=5)
        resume = threading.Barrier(2, timeout=5)
        state = {"verified": False, "held": False}

        def verify(pid: int) -> bool:
            state["verified"] = True
            return True

        def window_pid(hwnd: int) -> int:
            if state["verified"] and hwnd == CLIENT and not state["held"]:
                state["held"] = True
                at_read.wait()
                resume.wait()
            return desk.window_pid(hwnd)

        guard = FocusGuard(
            fns=_with_window_pid(desk.fns(), window_pid),
            clock=clock,
            sleep=clock.sleep,
            verify=verify,
        )
        guard.add_pids([CLIENT_PID])
        clock.now += 0.025
        guard.step()
        desk.steal(CLIENT)
        clock.now += 0.025
        worker = threading.Thread(target=guard.step, name="focus-oracle-step")
        worker.start()
        at_read.wait()
        guard.disarm("take")  # returns while the guard is still inside the owner read
        resume.wait()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(desk.minimizes(), [])


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
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.desk = FakeDesktop()
        self.desk.idle = 1e9  # nobody at the PC: steals are counted as away, nothing is minimized
        self.now = [5000.0]
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
            focus_fns=self.desk.fns(),
        )
        self.lifecycle.focus_clock = lambda: self.now[0]
        self.starts: list[object] = []
        self.lifecycle.focus_start = self.starts.append

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(self) -> None:
        records = (process(SERVER_PID, role="server"), process(CLIENT_PID))
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
        self.store.add(
            RunRecord(
                RUN_ID,
                "A",
                "lease-A",
                "RUNNING",
                "same",
                "@SameMod",
                "profiles",
                "mission",
                list(records),
            )
        )


class SeedingTest(_LifecycleCase):
    def test_a_new_guard_watches_every_live_game_process_of_the_run(self) -> None:
        self.add_run()
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID, PERSON)
        guard = self.lifecycle._focus_guards[RUN_ID]
        self.desk.front = PERSON
        guard.step()
        self.desk.steal(SERVER)  # the server, launched earlier, activates
        guard.step()
        summary = guard.summary()
        self.assertEqual((summary["steals"], summary["away"]), (1, 1))


class RunModeTest(_LifecycleCase):
    def test_a_run_keeps_its_mode_only_for_the_launch_ttl(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, SERVER_PID)
        self.assertNotIn(RUN_ID, self.lifecycle._focus_guards)
        self.now[0] += 901.0  # a later relaunch of the same run whose update failed
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID)
        self.assertIn(RUN_ID, self.lifecycle._focus_guards)

    def test_a_start_attempt_uses_up_the_mode_before_the_launch(self) -> None:
        source = inspect.getsource(ProcessLifecycle._start_run_reserved_holding)
        attempt = source.index("self._launch_focus_for_run(client, run_id)")
        launch = source.index("self.launcher(")
        self.assertLess(attempt, launch)
        # A start that fails after that line leaves no pending mode for the next run.
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        self.assertEqual(self.lifecycle._launch_focus_for_run(IDENTITY_A, "run-failed"), "take")
        self.lifecycle._arm_launch_focus(IDENTITY_A, "run-next", CLIENT_PID)
        self.assertIn("run-next", self.lifecycle._focus_guards)


class ForegroundBarrierTest(_LifecycleCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_run()
        self.asks: list[int] = []

        def enum_windows(callback) -> bool:
            for hwnd in (SERVER, CLIENT):
                callback(hwnd)
            return True

        def ask(hwnd: int) -> bool:
            self.asks.append(hwnd)
            return True

        self.lifecycle.window_fns = window_close.Win32WindowFns(
            enum_windows=enum_windows,
            window_pid=lambda hwnd: {SERVER: SERVER_PID, CLIENT: CLIENT_PID}.get(hwnd, 0),
            is_visible=lambda hwnd: True,
            post_message=lambda *args: True,
        )
        self.lifecycle.foreground_fns = window_foreground.Win32ForegroundFns(
            class_name=lambda hwnd: "DayZ",
            area=lambda hwnd: 5000,
            is_iconic=lambda hwnd: False,
            set_foreground=ask,
            foreground=lambda: CLIENT if self.asks else PERSON,
        )

    def test_disarm_reports_whether_the_guard_settled(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID)
        guard = self.lifecycle._focus_guards[RUN_ID]
        self.assertIs(self.lifecycle._disarm_launch_focus(RUN_ID, "foreground_verb", wait_s=0.1), True)
        guard.wait_idle = lambda timeout_s: False  # a give-back that does not settle
        self.assertIs(self.lifecycle._disarm_launch_focus(RUN_ID, "foreground_verb", wait_s=0.1), False)
        self.assertIs(self.lifecycle._disarm_launch_focus("no-such-run", "x", wait_s=0.1), True)

    def test_the_verb_refuses_while_a_give_back_has_not_settled(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID)
        guard = self.lifecycle._focus_guards[RUN_ID]
        guard.wait_idle = lambda timeout_s: False
        result = self.lifecycle.foreground_run(IDENTITY_A, self.token, RUN_ID, 0.0)
        self.assertEqual(
            (result.get("error"), result.get("_http_status")), ("focus_guard_busy", 409), result
        )
        self.assertEqual(self.asks, [])
        self.assertFalse(guard.armed)

    def test_the_verb_asks_once_the_guard_settled(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID)
        result = self.lifecycle.foreground_run(IDENTITY_A, self.token, RUN_ID, 0.0)
        self.assertEqual(result.get("state"), "foreground", result)
        self.assertEqual(self.asks, [CLIENT])


if __name__ == "__main__":
    unittest.main()
