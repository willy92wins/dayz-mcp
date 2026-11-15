"""Fixes after the review of the launch focus guard (inbox 5c8e, review of fbdd84a).

Oracle frozen before the fix round. No test touches the desktop or starts a
thread: the desktop is tests.launch_focus_helpers.FakeDesktop, the thread
starter is captured, and the launcher is a fake.
"""
from __future__ import annotations

import inspect
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from dayz_mcp import daemon, launch_focus, window_close, window_foreground
from dayz_mcp.launch_focus import FocusGuard
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.launch_focus_helpers import (
    CLIENT,
    CLIENT_PID,
    OTHER_PID,
    PERSON,
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


class GuardFixesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.desk = FakeDesktop()
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
        }
        options.update(overrides)
        return FocusGuard(**options)

    def tick(self, guard: FocusGuard, seconds: float = 0.025) -> None:
        self.clock.now += seconds
        guard.step()

    def test_a_reactivation_right_after_a_successful_give_back_is_a_new_steal(
        self,
    ) -> None:
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.front, PERSON)
        # DayZ activates itself again before any read sees PERSON in front.
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT, CLIENT])
        self.assertEqual(self.desk.front, PERSON)
        summary = guard.summary()
        self.assertEqual((summary["steals"], summary["returned"]), (2, 2))

    def test_a_window_that_activates_again_when_shown_is_tried_up_to_the_cap(
        self,
    ) -> None:
        self.desk.restore_activates = True
        guard = self.guard(max_returns=3)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        for _ in range(10):
            self.tick(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT, CLIENT, CLIENT])
        summary = guard.summary()
        self.assertEqual(summary["failed"], 3)
        self.assertEqual(summary["disarmed"], "max_returns")

    def test_the_latch_holds_while_the_front_never_left_the_run(self) -> None:
        self.desk.activates_next = False
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        for _ in range(10):
            self.tick(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT])

    def test_a_handle_that_changes_owner_before_the_minimize_is_not_minimized(
        self,
    ) -> None:
        def verify(pid: int) -> bool:
            # Accepted, then the handle is reused by another process before the minimize.
            self.desk.pids[CLIENT] = OTHER_PID
            return pid == CLIENT_PID

        guard = self.guard(verify=verify)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.minimizes(), [])
        self.assertEqual(self.desk.shows(), [])

    def test_a_handle_that_changes_owner_before_a_retry_is_not_shown_again(
        self,
    ) -> None:
        self.desk.restores = False  # the first show does not bring it back

        def recycle(_hwnd: int) -> None:
            self.desk.pids[CLIENT] = OTHER_PID

        self.desk.on_show = recycle
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.shows(), [CLIENT])
        self.assertEqual(self.desk.owners_at_show, [CLIENT_PID])

    def test_a_rejected_show_is_retried_even_when_the_window_looks_restored(
        self,
    ) -> None:
        # The minimize is still queued when the show is rejected: the window is
        # not iconic yet, and it would be once the minimize lands.
        self.desk.minimize_pending = True
        self.desk.activates_next = True
        self.desk.show_answers = [False, True]
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.shows(), [CLIENT, CLIENT])
        self.assertEqual(guard.summary()["restore_failed"], 0)

    def test_two_rejected_shows_are_reported_and_disarm(self) -> None:
        self.desk.minimize_pending = True
        self.desk.show_answers = [False, False]
        guard = self.guard()
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.shows(), [CLIENT, CLIENT])
        summary = guard.summary()
        self.assertEqual(summary["restore_failed"], 1)
        self.assertEqual(summary["disarmed"], "restore_failed")
        self.assertTrue(guard.wait_idle(0))


class _LifecycleCase(unittest.TestCase):
    focus_fns_in_constructor = True

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
        self.desk.idle = 1e9  # nobody at the PC: no test here minimizes anything
        options = dict(
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
        )
        if self.focus_fns_in_constructor:
            options["focus_fns"] = self.desk.fns()
        self.lifecycle = ProcessLifecycle(**options)
        self.starts: list[object] = []
        self.lifecycle.focus_start = self.starts.append

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(self, run_id: str = RUN_ID) -> None:
        records = (process(41, role="server"), process(42))
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
        self.store.add(
            RunRecord(
                run_id,
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


class DisabledByDefaultTest(_LifecycleCase):
    focus_fns_in_constructor = False

    def test_without_focus_fns_the_lifecycle_never_binds_user32(self) -> None:
        real = Mock(side_effect=AssertionError("real_win32 must not be called"))
        with patch.object(launch_focus, "real_win32", real):
            self.assertIsNone(self.lifecycle.focus_fns)
            self.assertIsNone(self.lifecycle._focus_fns())
            self.assertEqual(self.lifecycle._focus_foreground(), 0)
            self.assertEqual(
                self.lifecycle.set_launch_focus(IDENTITY_A, "keep"),
                {"error": "focus_unsupported", "_http_status": 409},
            )
            self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41, PERSON)
        real.assert_not_called()
        self.assertNotIn(RUN_ID, self.lifecycle._focus_guards)
        self.assertEqual(self.starts, [])


class EnabledByTheDaemonTest(_LifecycleCase):
    def test_the_constructor_takes_focus_fns(self) -> None:
        self.assertIsNotNone(self.lifecycle.focus_fns)
        self.assertEqual(
            self.lifecycle.set_launch_focus(IDENTITY_A, "keep"),
            {"ok": True, "focus": "keep"},
        )
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41, PERSON)
        self.assertIn(RUN_ID, self.lifecycle._focus_guards)

    def test_stop_launch_focus_disarms_every_guard(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, "run-a", 41)
        self.lifecycle._arm_launch_focus(IDENTITY_A, "run-b", 42)
        guards = [self.lifecycle._focus_guards[r] for r in ("run-a", "run-b")]
        self.lifecycle.stop_launch_focus("daemon_stopping")
        for guard in guards:
            self.assertFalse(guard.armed)
            self.assertEqual(guard.summary()["disarmed"], "daemon_stopping")
        self.assertEqual(
            inspect.signature(ProcessLifecycle.stop_launch_focus)
            .parameters["reason"]
            .default,
            "daemon_stopping",
        )

    def test_the_daemon_builds_the_real_binding_on_windows_only(self) -> None:
        sentinel = object()
        with patch.object(launch_focus, "real_win32", return_value=sentinel) as real:
            got = daemon._launch_focus_fns()
        if os.name == "nt":
            self.assertIs(got, sentinel)
            real.assert_called_once_with()
            with patch.object(launch_focus, "real_win32", side_effect=OSError("no user32")):
                self.assertIsNone(daemon._launch_focus_fns())
        else:
            self.assertIsNone(got)
            real.assert_not_called()

    def test_the_daemon_passes_it_to_the_lifecycle_and_stops_the_guards(self) -> None:
        source = inspect.getsource(daemon)
        self.assertIn("focus_fns=_launch_focus_fns()", source)
        self.assertIn("stop_launch_focus(", source)

    def test_foreground_run_disarms_even_when_it_answers_minimized(self) -> None:
        self.add_run()
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42)
        guard = self.lifecycle._focus_guards[RUN_ID]

        def enum_windows(callback) -> bool:
            for hwnd in (410, 420):
                callback(hwnd)
            return True

        self.lifecycle.window_fns = window_close.Win32WindowFns(
            enum_windows=enum_windows,
            window_pid=lambda hwnd: {410: 41, 420: 42}.get(hwnd, 0),
            is_visible=lambda hwnd: True,
            post_message=lambda *args: True,
        )
        self.lifecycle.foreground_fns = window_foreground.Win32ForegroundFns(
            class_name=lambda hwnd: "DayZ",
            area=lambda hwnd: 5000,
            is_iconic=lambda hwnd: True,  # the guard has the client minimized
            set_foreground=lambda hwnd: True,
            foreground=lambda: 7,
        )
        result = self.lifecycle.foreground_run(IDENTITY_A, self.token, RUN_ID, 0.0)
        self.assertEqual(result.get("state"), "minimized", result)
        self.assertFalse(guard.armed)
        self.assertEqual(guard.summary()["disarmed"], "foreground_verb")

    def test_a_mode_is_used_by_the_first_start_and_stays_with_its_run(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)  # the server
        self.assertNotIn(RUN_ID, self.lifecycle._focus_guards)
        # The start used it: a later launch of another run no longer sees it.
        self.assertEqual(self.lifecycle.launch_focus_mode(IDENTITY_A.session_id), "keep")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42)  # the client, same run
        self.assertNotIn(RUN_ID, self.lifecycle._focus_guards)
        self.assertEqual(self.starts, [])

    def test_a_new_run_whose_mode_could_not_be_set_gets_keep(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        self.lifecycle._arm_launch_focus(IDENTITY_A, "run-earlier", 41)
        # The next dayz_test_run's set failed: its new run must not inherit take.
        self.lifecycle._arm_launch_focus(IDENTITY_A, "run-next", 42)
        self.assertIn("run-next", self.lifecycle._focus_guards)
        self.assertNotIn("run-earlier", self.lifecycle._focus_guards)

    def test_a_fresh_mode_for_a_run_replaces_the_one_it_kept(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
        self.lifecycle.set_launch_focus(IDENTITY_A, "keep")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42)  # client relaunch
        self.assertIn(RUN_ID, self.lifecycle._focus_guards)


if __name__ == "__main__":
    unittest.main()
