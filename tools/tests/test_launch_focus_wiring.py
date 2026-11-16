"""dayz_test_run(focus=...) reaches the daemon, which guards the launch's foreground (inbox 5c8e).

Oracle frozen before the wiring round. No test touches the desktop or starts a
thread or a process: the guard's Win32 calls are fakes, the thread starter is
captured, and the launcher and the secure launcher are fakes.
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import mcp_capture
from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import (
    control_client,
    dayz_test_request,
    dayz_test_tool,
    launch_focus,
    loopback,
    native_launcher_transaction,
    server,
    steam_preflight,
    window_close,
    window_foreground,
)
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.client_helpers import _fixture_client_runtime
from tests.dayz_test_tool_helpers import (
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)
from tests.lifecycle_helpers import IDENTITY_PAYLOAD
from tests.process_lifecycle_helpers import (
    IDENTITY_A,
    IDENTITY_B,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.session_http_helpers import SessionLoopback
from tests.steam_helpers import FakeSteamGate

RUN_ID = "run-focus"
TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent

PERSON = 7  # the window a person works in
SERVER_WINDOW = 410
CLIENT_WINDOW = 420


class _Front:
    """The foreground as the guard reads it. By default nobody is at the PC
    (idle 1e9 s), so a steal is counted as away and nothing is minimized. A
    minimize hands the front back to PERSON at once, so no test waits."""

    def __init__(self) -> None:
        self.front = PERSON
        self.idle = 1e9
        self.pids = {PERSON: 900, SERVER_WINDOW: 41, CLIENT_WINDOW: 42}
        self.calls: list[tuple[str, int]] = []

    def fns(self) -> launch_focus.FocusFns:
        def minimize(hwnd: int) -> bool:
            self.calls.append(("minimize", hwnd))
            if self.front == hwnd:
                self.front = PERSON
            return True

        def show(hwnd: int) -> bool:
            self.calls.append(("show_no_activate", hwnd))
            return True

        return launch_focus.FocusFns(
            foreground=lambda: self.front,
            window_pid=lambda hwnd: self.pids.get(hwnd, 0),
            is_window=lambda hwnd: hwnd in self.pids,
            is_iconic=lambda hwnd: False,
            minimize=minimize,
            show_no_activate=show,
            idle_s=lambda: self.idle,
            mouse_down=lambda: False,
            switch_keys_down=lambda: False,
            cursor_window=lambda: PERSON,
        )


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
        )
        self.front = _Front()
        self.starts: list[object] = []
        self.lifecycle.focus_fns = self.front.fns()
        self.lifecycle.focus_start = self.starts.append

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(self, state: str = "RUNNING") -> None:
        records = (process(41, role="server"), process(42))
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
        self.store.add(
            RunRecord(
                RUN_ID,
                "A",
                "lease-A",
                state,
                "same",
                "@SameMod",
                "profiles",
                "mission",
                list(records),
            )
        )

    def guard_rows(self) -> list[dict[str, object]]:
        return [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_focus_guard"
        ]


class LaunchFocusPreferenceTest(_LifecycleCase):
    def test_each_session_keeps_its_own_mode_and_keep_is_the_default(self) -> None:
        self.assertEqual(self.lifecycle.launch_focus_mode(IDENTITY_A.session_id), "keep")
        self.assertEqual(
            self.lifecycle.set_launch_focus(IDENTITY_A, "take"),
            {"ok": True, "focus": "take"},
        )
        self.assertEqual(self.lifecycle.launch_focus_mode(IDENTITY_A.session_id), "take")
        self.assertEqual(self.lifecycle.launch_focus_mode(IDENTITY_B.session_id), "keep")
        self.assertEqual(
            self.lifecycle.set_launch_focus(IDENTITY_A, "keep"),
            {"ok": True, "focus": "keep"},
        )
        self.assertEqual(self.lifecycle.launch_focus_mode(IDENTITY_A.session_id), "keep")

    def test_an_unknown_mode_is_invalid_focus_and_changes_nothing(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        for bad in (None, "", "steal", "KEEP", 1, True, ["take"]):
            with self.subTest(bad=bad):
                self.assertEqual(
                    self.lifecycle.set_launch_focus(IDENTITY_A, bad),
                    {"error": "invalid_focus", "_http_status": 400},
                )
        self.assertEqual(self.lifecycle.launch_focus_mode(IDENTITY_A.session_id), "take")

    def test_a_mode_older_than_the_launch_ttl_falls_back_to_keep(self) -> None:
        now = [5000.0]
        self.lifecycle.focus_clock = lambda: now[0]
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        now[0] += 899.0
        self.assertEqual(self.lifecycle.launch_focus_mode(IDENTITY_A.session_id), "take")
        now[0] += 2.0
        self.assertEqual(self.lifecycle.launch_focus_mode(IDENTITY_A.session_id), "keep")

    def test_a_daemon_without_the_win32_reads_answers_focus_unsupported(self) -> None:
        self.lifecycle.focus_fns = None
        with patch.object(launch_focus, "real_win32", side_effect=OSError("no user32")):
            self.assertEqual(
                self.lifecycle.set_launch_focus(IDENTITY_A, "take"),
                {"error": "focus_unsupported", "_http_status": 409},
            )
            self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
            self.assertEqual(self.lifecycle._focus_foreground(), 0)
        self.assertNotIn(RUN_ID, self.lifecycle._focus_guards)
        self.assertEqual(self.starts, [])

    def test_the_oldest_of_many_sessions_falls_back_to_keep(self) -> None:
        identities = [
            ClientIdentity("codex", 100 + i, 1, "2026-07-15T00:00:00Z", f"S{i}", "x")
            for i in range(70)
        ]
        for identity in identities:
            self.lifecycle.set_launch_focus(identity, "take")
        self.assertEqual(self.lifecycle.launch_focus_mode("S0"), "keep")
        self.assertEqual(self.lifecycle.launch_focus_mode("S69"), "take")


class LaunchFocusGuardWiringTest(_LifecycleCase):
    def test_keep_arms_one_guard_per_run_and_a_later_launch_adds_its_pid(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
        guard = self.lifecycle._focus_guards[RUN_ID]
        self.assertIsInstance(guard, launch_focus.FocusGuard)
        self.assertTrue(guard.armed)
        self.assertEqual(len(self.starts), 1)
        self.assertTrue(callable(self.starts[0]))
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42)
        self.assertIs(self.lifecycle._focus_guards[RUN_ID], guard)
        self.assertEqual(len(self.starts), 1)
        # Both pids are the run's: a window of each that takes the front is a steal.
        for window in (SERVER_WINDOW, CLIENT_WINDOW):
            self.front.front = PERSON
            guard.step()
            self.front.front = window
            guard.step()
        summary = guard.summary()
        self.assertEqual((summary["steals"], summary["away"]), (2, 2))
        self.assertEqual(self.front.calls, [])

    def test_take_arms_nothing(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
        self.assertNotIn(RUN_ID, self.lifecycle._focus_guards)
        self.assertEqual(self.starts, [])

    def test_take_ends_the_guard_an_earlier_launch_of_the_run_armed(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
        guard = self.lifecycle._focus_guards[RUN_ID]
        self.lifecycle.set_launch_focus(IDENTITY_A, "take")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42)
        self.assertFalse(guard.armed)
        self.assertEqual(guard.summary()["disarmed"], "take")
        self.assertEqual(len(self.starts), 1)

    def test_a_steal_before_the_guard_was_armed_goes_back_to_the_window_before_the_launch(
        self,
    ) -> None:
        self.add_run()
        self.front.idle = 1.0
        self.front.front = CLIENT_WINDOW  # DayZ took the front before the arm
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42, PERSON)
        guard = self.lifecycle._focus_guards[RUN_ID]
        guard.step()
        self.assertEqual(
            self.front.calls,
            [("minimize", CLIENT_WINDOW), ("show_no_activate", CLIENT_WINDOW)],
        )
        self.assertEqual(self.front.front, PERSON)
        self.assertEqual(guard.summary()["returned"], 1)

    def test_the_guard_touches_only_a_process_the_run_still_owns(self) -> None:
        self.front.idle = 1.0
        self.front.front = CLIENT_WINDOW
        # No run in the manifest: the client's identity cannot be confirmed.
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42, PERSON)
        guard = self.lifecycle._focus_guards[RUN_ID]
        guard.step()
        self.assertEqual(self.front.calls, [])
        self.assertEqual(guard.summary()["unverified"], 1)

    def test_the_foreground_before_a_launch_is_read_through_the_seam(self) -> None:
        self.front.front = PERSON
        self.assertEqual(self.lifecycle._focus_foreground(), PERSON)

    def test_the_mode_of_the_launching_session_is_the_one_used(self) -> None:
        self.lifecycle.set_launch_focus(IDENTITY_B, "take")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
        self.assertIn(RUN_ID, self.lifecycle._focus_guards)

    def test_arming_never_raises_into_the_launch(self) -> None:
        def broken(_target: object) -> None:
            raise RuntimeError("no thread")

        self.lifecycle.focus_start = broken
        self.assertIsNone(self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41))

    def test_disarm_reaches_the_runs_guard_and_ignores_an_unknown_run(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
        guard = self.lifecycle._focus_guards[RUN_ID]
        self.lifecycle._disarm_launch_focus(RUN_ID, "foreground_verb")
        self.assertFalse(guard.armed)
        self.assertEqual(guard.summary()["disarmed"], "foreground_verb")
        self.lifecycle._disarm_launch_focus("no-such-run", "foreground_verb")

    def test_the_guard_thread_writes_one_row_and_forgets_its_guard(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
        guard = self.lifecycle._focus_guards[RUN_ID]
        guard.disarm("foreground_verb")
        self.starts[0]()
        (row,) = self.guard_rows()
        self.assertEqual(row["reason"], "foreground_verb")
        self.assertEqual(row["decision"], "watched")
        self.assertEqual(row["run_id"], RUN_ID)
        self.assertEqual(row["focus"], guard.summary())
        self.assertEqual(row["client"], IDENTITY_A.public_payload())
        self.assertNotIn(RUN_ID, self.lifecycle._focus_guards)

    def test_a_launch_after_the_guard_ended_arms_a_new_one(self) -> None:
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 41)
        first = self.lifecycle._focus_guards[RUN_ID]
        first.disarm("foreground_verb")
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42)
        second = self.lifecycle._focus_guards[RUN_ID]
        self.assertIsNot(second, first)
        self.assertTrue(second.armed)
        self.assertEqual(len(self.starts), 2)
        # The first guard's thread ends after the second was armed: it must not
        # forget the second one.
        self.starts[0]()
        self.assertIs(self.lifecycle._focus_guards[RUN_ID], second)
        self.assertEqual(len(self.guard_rows()), 1)

    def test_the_guard_watches_the_runs_game_processes_while_the_run_is_active(
        self,
    ) -> None:
        self.assertEqual(self.lifecycle._focus_live_pids(RUN_ID), set())
        records = (process(41, role="server"), process(42), process(43, role="crash_reporter"))
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
        self.assertEqual(self.lifecycle._focus_live_pids(RUN_ID), {41, 42})

    def test_a_pid_is_owned_only_while_its_identity_matches_the_run(self) -> None:
        self.add_run()
        self.assertTrue(self.lifecycle._focus_pid_owned(RUN_ID, 42))
        self.assertTrue(self.lifecycle._focus_pid_owned(RUN_ID, 41))
        self.assertFalse(self.lifecycle._focus_pid_owned(RUN_ID, 44))
        self.assertFalse(self.lifecycle._focus_pid_owned("no-such-run", 42))
        self.guard.snapshots[42] = {"error": "process_not_found"}
        self.assertFalse(self.lifecycle._focus_pid_owned(RUN_ID, 42))

    def test_the_success_path_of_a_start_arms_the_guard_after_the_launch(self) -> None:
        source = inspect.getsource(ProcessLifecycle._start_run_reserved_holding)
        before = source.index("focus_before = self._focus_foreground()")
        launch = source.index("self.launcher(")
        arm = source.index("self._arm_launch_focus(client, run_id, pid, focus_before)")
        started = source.index('reason="started"')
        self.assertLess(before, launch)
        self.assertLess(launch, arm)
        self.assertLess(arm, started)
        self.assertEqual(source.count("self._arm_launch_focus("), 1)


class ForegroundVerbDisarmsTest(_LifecycleCase):
    def test_foreground_run_disarms_the_guard_before_it_asks(self) -> None:
        self.add_run()
        self.lifecycle._arm_launch_focus(IDENTITY_A, RUN_ID, 42)
        guard = self.lifecycle._focus_guards[RUN_ID]
        state = {"front": PERSON}
        armed_at_ask: list[bool] = []

        def enum_windows(callback) -> bool:
            for hwnd in (SERVER_WINDOW, CLIENT_WINDOW):
                callback(hwnd)
            return True

        def ask(hwnd: int) -> bool:
            armed_at_ask.append(guard.armed)
            state["front"] = hwnd
            return True

        self.lifecycle.window_fns = window_close.Win32WindowFns(
            enum_windows=enum_windows,
            window_pid=lambda hwnd: {SERVER_WINDOW: 41, CLIENT_WINDOW: 42}.get(hwnd, 0),
            is_visible=lambda hwnd: True,
            post_message=lambda *args: True,
        )
        self.lifecycle.foreground_fns = window_foreground.Win32ForegroundFns(
            class_name=lambda hwnd: "DayZ",
            area=lambda hwnd: 5000,
            is_iconic=lambda hwnd: False,
            set_foreground=ask,
            foreground=lambda: state["front"],
        )
        result = self.lifecycle.foreground_run(IDENTITY_A, self.token, RUN_ID, 0.0)
        self.assertEqual(result.get("state"), "foreground", result)
        self.assertEqual(armed_at_ask, [False])
        self.assertEqual(guard.summary()["disarmed"], "foreground_verb")


class LaunchFocusRouteTest(SessionLoopback, unittest.TestCase):
    def test_the_route_hands_the_mode_to_the_lifecycle(self) -> None:
        self.assertEqual(loopback.LIFECYCLE_ROUTES["/lifecycle/focus"], "focus")
        seen: list[tuple[object, ...]] = []
        answers = [
            {"ok": True, "focus": "take"},
            {"error": "invalid_focus", "_http_status": 400},
        ]

        # Re-frozen after the review of 1d07355: the route also hands over the lease token,
        # which binds the mode to the launch's lease.
        class Lifecycle:
            def set_launch_focus(self, client, mode, token=None):
                seen.append((client.session_id, mode, token))
                return answers.pop(0)

        self.state.lifecycle = Lifecycle()
        self.assertEqual(
            self.request(
                "/lifecycle/focus",
                {"identity": IDENTITY_PAYLOAD, "mode": "take", "lease_token": "token-1"},
            ),
            (200, {"ok": True, "focus": "take"}),
        )
        status, body = self.request(
            "/lifecycle/focus", {"identity": IDENTITY_PAYLOAD, "mode": "steal"}
        )
        self.assertEqual((status, body["error"]), (400, "invalid_focus"))
        self.assertEqual(
            seen, [("same-session", "take", "token-1"), ("same-session", "steal", None)]
        )


class LaunchFocusClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.config = server.ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(self.config)
        self.control = self.runtime._control

    async def test_the_client_posts_the_mode_with_the_launch_lease_it_holds(self) -> None:
        # Re-frozen after the review of 1d07355: when the launch leaves the queue the client
        # holds the launch lease (_remember_acquire_or_wait), and the mode is bound to it.
        seen: list[tuple[object, ...]] = []

        async def session_call(path, payload, *, timeout_s=None):
            seen.append((path, payload))
            return {"ok": True, "focus": "take"}

        with patch.object(self.control, "_session_call", new=session_call):
            self.control.active_lease_token = "token-1"
            result = await self.control.lifecycle_focus("take")
            self.control.active_lease_token = None
            await self.control.lifecycle_focus("keep")
        self.assertEqual(result, {"ok": True, "focus": "take"})
        self.assertEqual(
            seen,
            [
                ("/lifecycle/focus", {"mode": "take", "lease_token": "token-1"}),
                ("/lifecycle/focus", {"mode": "keep"}),
            ],
        )

    async def test_the_runtime_hands_the_mode_to_its_control_client(self) -> None:
        forwarded = AsyncMock(return_value={"ok": True, "focus": "keep"})
        with patch.object(self.runtime, "_control_with_lazy_spawn", new=forwarded):
            result = await self.runtime.lifecycle_focus("keep")
        self.assertEqual(result, {"ok": True, "focus": "keep"})
        (call,) = forwarded.await_args_list
        method, mode = call.args
        self.assertIs(
            getattr(method, "__func__", None), control_client.ControlClient.lifecycle_focus
        )
        self.assertEqual(mode, "keep")


class LaunchFocusToolTest(unittest.IsolatedAsyncioTestCase):
    def app(self):
        config = server.ServerConfig(
            mode="client",
            key="test-key",
            port=12345,
            client_platform="codex",
            log_sink=lambda _: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _ = server.build_app(config)
        return app, runtime

    async def test_the_parameter_defaults_to_keep_and_an_unknown_mode_is_refused_first(
        self,
    ) -> None:
        app, runtime = self.app()
        tool = app._tool_manager.get_tool("dayz_test_run")
        self.assertEqual(tool.parameters["properties"]["focus"]["default"], "keep")
        with patch.object(runtime, "session_status", new=AsyncMock()) as status:
            with self.assertRaisesRegex(ToolError, "invalid_focus"):
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "all", "focus": "steal"},
                )
            status.assert_not_awaited()

    async def test_the_description_names_both_modes_and_the_foreground_verb(self) -> None:
        app, _runtime = self.app()
        description = app._tool_manager.get_tool("dayz_test_run").description or ""
        for words in ('focus="keep"', 'focus="take"', "dayz_test_foreground", "capture_screenshot"):
            self.assertIn(words, description)


class _FocusRuntime(_Runtime):
    def __init__(self, lifecycle: dict, events: list, answer: object) -> None:
        super().__init__(lifecycle)
        self.events = events
        self.answer = answer

    async def lifecycle_focus(self, mode: str) -> dict:
        self.events.append(("focus", mode))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer  # type: ignore[return-value]


class LaunchFocusEndToEndTest(unittest.IsolatedAsyncioTestCase):
    RUN_ID = "12345678-1234-4234-8234-1234567890ab"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "_client" / "profiles").mkdir(parents=True)
        (self.root / "_server" / "profiles").mkdir(parents=True)
        patchers = [
            patch.object(
                dayz_test_tool,
                "evaluate_steam_session",
                return_value=steam_preflight.SteamSessionResult(
                    error_code=None,
                    steam_registered_pid=1,
                    steam_live_pids=(1,),
                    remediation=steam_preflight.REMEDIATION,
                ),
            ),
            patch.object(
                dayz_test_tool,
                "preflight_vpp_request",
                return_value=native_launcher_transaction.VppPreflightResult(
                    error_code=None,
                    missing=(),
                    warnings=(),
                    hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
                ),
            ),
            patch.object(
                dayz_test_tool,
                "evaluate_prerun_desktop",
                return_value=mcp_capture.PrerunDesktopResult(
                    error_code=None,
                    desktop="unlocked",
                    mean_brightness=80.0,
                    nonblack_ratio=0.9,
                    waited_s=0.01,
                    remediation="",
                ),
            ),
        ]
        # The fake server writes no RPT: do not wait the advisory boot report's 60 s.
        patchers.append(patch.object(dayz_test_tool, "_SERVER_BOOT_MAX_WAIT_S", 0.0))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _run_all(self, runtime: _Runtime, events: list, **kwargs) -> dict:
        policy = _policy(dev_root=str(self.root))

        async def launch(raw_request: bytes, **launch_kwargs: object) -> int:
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=(policy,)
            )
            # The sealed request never carries the focus mode.
            self.assertNotIn("focus", parsed.payload)
            # secure_launcher: the queue grants the launch, then the worker's
            # /lifecycle/start reaches the daemon.
            await launch_kwargs["execution_started_cb"]()  # type: ignore[operator]
            events.append(("start",))
            launch_kwargs["output_sink"](  # type: ignore[operator]
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": self.RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ), patch.object(dayz_test_tool, "_pid_alive", return_value=True):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
                **kwargs,
            )

    def lifecycle(self) -> dict:
        return {
            "runs": [
                {
                    "run_id": self.RUN_ID,
                    "processes": [
                        {"role": "server", "pid": 11},
                        {"role": "client", "pid": 22},
                    ],
                }
            ]
        }

    async def test_the_mode_reaches_the_daemon_before_the_launcher_runs(self) -> None:
        events: list = []
        runtime = _FocusRuntime(self.lifecycle(), events, {"ok": True, "focus": "take"})
        result = await self._run_all(runtime, events, focus="take")
        self.assertEqual(events, [("focus", "take"), ("start",)])
        self.assertEqual(result["focus"], {"mode": "take", "set": True})

    async def test_keep_is_sent_by_default(self) -> None:
        events: list = []
        runtime = _FocusRuntime(self.lifecycle(), events, {"ok": True, "focus": "keep"})
        result = await self._run_all(runtime, events)
        self.assertEqual(events, [("focus", "keep"), ("start",)])
        self.assertEqual(result["focus"], {"mode": "keep", "set": True})

    async def test_a_daemon_that_cannot_take_the_mode_still_launches(self) -> None:
        for answer in (RuntimeError("daemon down"), {"error": "invalid_focus"}, None):
            with self.subTest(answer=answer):
                events: list = []
                runtime = _FocusRuntime(self.lifecycle(), events, answer)
                result = await self._run_all(runtime, events, focus="take")
                self.assertEqual(events, [("focus", "take"), ("start",)])
                self.assertEqual(result["focus"], {"mode": "take", "set": False})

    async def test_a_runtime_without_the_call_still_launches(self) -> None:
        events: list = []
        result = await self._run_all(_Runtime(self.lifecycle()), events)
        self.assertEqual(events, [("start",)])
        self.assertEqual(result["focus"], {"mode": "keep", "set": False})


class LaunchFocusDocsTest(unittest.TestCase):
    def test_the_changelog_and_the_tool_readme_name_it(self) -> None:
        changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("5c8e", changelog)
        readme = (TOOLS / "README-mcp.md").read_text(encoding="utf-8")
        self.assertIn('focus="take"', readme)


if __name__ == "__main__":
    unittest.main()
