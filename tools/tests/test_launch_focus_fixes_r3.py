"""Third round of review fixes for the launch focus guard (inbox 5c8e, review of 3b6e0a7).

Oracle frozen before the fix round. QueuedDesktop models what ShowWindowAsync
does: it only posts the request, and the window's thread applies the posted
requests later, in order. No test touches the desktop or starts a thread.
"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import control_client, launch_focus, server
from dayz_mcp.launch_focus import FocusGuard
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.client_helpers import _fixture_client_runtime
from tests.launch_focus_helpers import (
    CLIENT,
    CLIENT_PID,
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


class QueuedDesktop(FakeDesktop):
    """FakeDesktop whose minimize and show requests wait in the window's queue."""

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
        """The window's thread runs: the posted requests apply in order."""
        for name, hwnd in self.queue:
            if name == "minimize":
                self._apply_minimize(hwnd)
            else:
                self.minimized.discard(hwnd)
        self.queue = []


class CompletionBarrierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.desk = QueuedDesktop()
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

    def test_the_unresolved_bound_is_a_keyword_whose_default_is_the_constant(self) -> None:
        self.assertEqual(launch_focus.UNRESOLVED_S, 10.0)
        param = inspect.signature(FocusGuard).parameters["unresolved_s"]
        self.assertEqual(param.default, launch_focus.UNRESOLVED_S)
        self.assertEqual(param.kind, inspect.Parameter.KEYWORD_ONLY)

    # Re-frozen after the review of 1d07355: the give-back runs in phases. The restore is
    # posted only once the minimize is SEEN (the window is iconic), and the guard is busy
    # until the restore is seen too. The three tests below replace the ones that assumed
    # both requests could be queued before the minimize took effect.

    def drain_on_sleep(self, guard_box: list, react_after_show: bool = False):
        record: dict[str, list] = {"queues": [], "busy": []}

        def sleep(seconds: float) -> None:
            self.clock.sleep(seconds)
            if self.desk.queue:
                record["queues"].append(list(self.desk.queue))
                record["busy"].append(not guard_box[0].wait_idle(0))
                had_show = any(name == "show" for name, _ in self.desk.queue)
                self.desk.drain()  # the DayZ thread runs what was posted, in order
                if react_after_show and had_show:
                    self.desk.steal(CLIENT)  # DayZ activates itself again

        return sleep, record

    def test_the_restore_is_posted_only_once_the_minimize_is_seen(self) -> None:
        box: list = []
        sleep, record = self.drain_on_sleep(box)
        guard = self.guard(sleep=sleep)
        box.append(guard)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.queued = True
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(
            record["queues"], [[("minimize", CLIENT)], [("show", CLIENT)]]
        )
        self.assertEqual(record["busy"], [True, True])
        self.assertEqual(self.desk.queue, [])
        self.assertEqual(self.desk.minimized, set())
        self.assertEqual(self.desk.front, PERSON)
        self.assertTrue(guard.wait_idle(0))
        summary = guard.summary()
        self.assertEqual((summary["returned"], summary["restore_failed"]), (1, 0))

    def test_a_minimize_never_seen_within_unresolved_s_keeps_the_guard_busy_until_it_lands(
        self,
    ) -> None:
        # Re-frozen after the review of 39b7281 (this test replaces "a minimize never seen gets
        # its restore after unresolved_s"): a restore posted behind a minimize that has not run
        # cannot be seen to complete, and the queued minimize could still undo the foreground
        # verb. So no restore is posted for it, and the guard stays busy (the verb answers
        # focus_guard_busy) until the minimize is seen, or the window is gone. Time alone never
        # lifts it.
        guard = self.guard(unresolved_s=10.0)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.queued = True  # the DayZ thread does not run during this give-back
        self.desk.steal(CLIENT)
        start = self.clock.now + 0.025
        self.tick(guard)
        self.assertGreaterEqual(self.clock.now - start, 10.0 - 1e-6)
        self.assertEqual(self.desk.queue, [("minimize", CLIENT)])
        summary = guard.summary()
        self.assertEqual(summary["restore_failed"], 1)
        self.assertEqual(summary["disarmed"], "restore_failed")
        self.assertFalse(guard.wait_idle(0))
        for _ in range(2):
            self.tick(guard, 3600.0)  # an hour, twice: time alone never lifts it
            self.assertFalse(guard.wait_idle(0))
        self.assertEqual(self.desk.queue, [("minimize", CLIENT)])
        self.desk.drain()  # the late minimize lands: DayZ is minimized, the person's window active
        self.assertEqual(self.desk.front, PERSON)
        self.tick(guard)
        self.assertEqual(self.desk.queue, [("show", CLIENT)])
        self.assertFalse(guard.wait_idle(0))
        self.desk.drain()
        self.tick(guard)
        self.assertTrue(guard.wait_idle(0))
        self.assertEqual(self.desk.minimized, set())
        self.assertEqual(self.desk.front, PERSON)
        self.assertEqual(guard.summary()["restore_failed"], 1)  # counted once

    def test_a_disarm_during_the_phases_still_restores_what_was_minimized(self) -> None:
        box: list = []
        record: dict[str, list] = {"queues": []}

        def sleep(seconds: float) -> None:
            self.clock.sleep(seconds)
            if self.desk.queue:
                record["queues"].append(list(self.desk.queue))
                box[0].disarm("foreground_verb")  # the verb arrives mid give-back
                self.desk.drain()

        guard = self.guard(sleep=sleep)
        box.append(guard)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.queued = True
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT])
        self.assertEqual(self.desk.shows(), [CLIENT])
        self.assertEqual(self.desk.minimized, set())
        self.assertEqual(self.desk.queue, [])
        self.assertEqual(guard.summary()["disarmed"], "foreground_verb")
        self.assertTrue(guard.wait_idle(0))

    def test_a_reactivation_after_the_restore_is_a_new_steal_up_to_the_cap(self) -> None:
        box: list = []
        sleep, _record = self.drain_on_sleep(box, react_after_show=True)
        guard = self.guard(sleep=sleep, max_returns=3)
        box.append(guard)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.queued = True
        self.desk.steal(CLIENT)
        for _ in range(6):
            self.tick(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT, CLIENT, CLIENT])
        summary = guard.summary()
        self.assertEqual(summary["failed"], 3)
        self.assertEqual(summary["restore_failed"], 0)
        self.assertEqual(summary["disarmed"], "max_returns")
        self.assertTrue(guard.wait_idle(0))

    def test_extend_adds_pids_and_restarts_the_watch_while_armed(self) -> None:
        guard = self.guard(watch_s=10.0)
        guard.add_pids([SERVER_PID])
        self.tick(guard, 8.0)
        self.assertIs(guard.extend([CLIENT_PID], foreground_before=PERSON), True)
        self.tick(guard, 5.0)
        self.assertTrue(guard.armed)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(guard.summary()["returned"], 1)

    def test_extend_refuses_a_guard_that_already_ended(self) -> None:
        guard = self.guard(watch_s=10.0)
        guard.add_pids([SERVER_PID])
        self.tick(guard, 11.0)
        self.assertFalse(guard.armed)
        self.assertIs(guard.extend([CLIENT_PID]), False)
        self.assertFalse(guard.armed)
        self.assertEqual(guard.summary()["disarmed"], "window_elapsed")

    def test_a_new_launch_resets_the_give_back_allowance(self) -> None:
        guard = self.guard(max_returns=1)
        guard.add_pids([CLIENT_PID])
        self.tick(guard)
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.desk.steal(PERSON)
        self.tick(guard)
        self.assertIs(guard.extend([CLIENT_PID]), True)  # a relaunch of the client
        self.desk.steal(CLIENT)
        self.tick(guard)
        self.assertEqual(self.desk.minimizes(), [CLIENT, CLIENT])
        self.assertEqual(guard.summary()["returned"], 2)
        self.assertTrue(guard.armed)


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


class RearmTest(_LifecycleCase):
    def test_a_guard_that_ends_while_a_launch_extends_it_is_replaced(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, SERVER_PID)
        first = self.lifecycle._focus_guards[RUN_ID]
        first.extend = lambda *args, **kwargs: False  # it expired first: the race was lost
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID)
        second = self.lifecycle._focus_guards[RUN_ID]
        self.assertIsNot(second, first)
        self.assertTrue(second.armed)
        self.assertEqual(len(self.starts), 2)

    def test_a_guard_still_armed_is_extended_not_replaced(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, SERVER_PID)
        first = self.lifecycle._focus_guards[RUN_ID]
        calls: list[tuple] = []
        original = first.extend

        def extend(pids, **kwargs):
            calls.append((sorted(pids), kwargs))
            return original(pids, **kwargs)

        first.extend = extend
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID, PERSON)
        self.assertIs(self.lifecycle._focus_guards[RUN_ID], first)
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(len(calls), 1)
        self.assertIn(CLIENT_PID, calls[0][0])
        self.assertEqual(calls[0][1].get("foreground_before"), PERSON)


class LeaseScopeTest(_LifecycleCase):
    # Re-frozen after the review of 1d07355: the mode is bound to the launch lease when it is
    # SET (/lifecycle/focus carries the lease token, start_run binds the token to its lease id),
    # so a start with a lease never sees a mode another launch set.

    def test_a_run_mode_is_reused_only_by_starts_of_the_same_lease(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take", "token-1")
        self.lifecycle._focus_bind_lease("token-1", "lease-1")
        self.assertEqual(self.lifecycle._launch_focus_for_run(IDENTITY_A, RUN_ID, "lease-1"), "take")
        # The client start of the same launch (same lease) keeps it.
        self.assertEqual(self.lifecycle._launch_focus_for_run(IDENTITY_A, RUN_ID, "lease-1"), "take")
        # A later launch of the same run whose update failed has another lease.
        self.assertEqual(self.lifecycle._launch_focus_for_run(IDENTITY_A, RUN_ID, "lease-2"), "keep")

    def test_arming_uses_the_mode_decided_at_the_start_attempt(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take", "token-1")
        self.lifecycle._focus_bind_lease("token-1", "lease-1")
        self.lifecycle._launch_focus_for_run(IDENTITY_A, RUN_ID, "lease-1")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, SERVER_PID)
        self.assertNotIn(RUN_ID, self.lifecycle._focus_guards)
        self.lifecycle._launch_focus_for_run(IDENTITY_A, RUN_ID, "lease-2")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, CLIENT_PID)
        self.assertIn(RUN_ID, self.lifecycle._focus_guards)

    def test_a_mode_set_without_a_lease_never_reaches_a_start_with_a_lease(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        self.assertEqual(self.lifecycle._launch_focus_for_run(IDENTITY_A, RUN_ID, "lease-1"), "keep")

    def test_a_mode_whose_launch_never_started_does_not_reach_a_later_launch(self) -> None:
        # Launch 1 set take with its token but was refused before any start bound it;
        # launch 2's update failed: its start binds only its own token.
        self.lifecycle.set_launch_focus(IDENTITY_A, "take", "token-1")
        self.lifecycle._focus_bind_lease("token-2", "lease-2")
        self.assertEqual(self.lifecycle._launch_focus_for_run(IDENTITY_A, RUN_ID, "lease-2"), "keep")

    def test_start_run_binds_the_callers_lease_token_to_its_lease_before_starting(self) -> None:
        source = inspect.getsource(ProcessLifecycle.start_run)
        bind = source.index("self._focus_bind_lease(token, authority[1])")
        reserved = source.index("self._start_run_reserved(")
        self.assertLess(bind, reserved)


class BusyCodeTest(unittest.IsolatedAsyncioTestCase):
    async def test_focus_guard_busy_reaches_the_caller_by_name(self) -> None:
        config = server.ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)

        async def refused(run_id, hold_s):
            raise control_client.ControlClientError(
                "focus_guard_busy", request_stage="post_request", http_bytes_sent=1
            )

        runtime._control.lifecycle_foreground = refused
        with self.assertRaises(server.ToolError) as raised:
            await runtime.lifecycle_foreground("run-1", 0.0)
        self.assertEqual(str(raised.exception), "focus_guard_busy")


if __name__ == "__main__":
    unittest.main()
