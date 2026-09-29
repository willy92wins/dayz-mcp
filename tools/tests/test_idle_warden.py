"""250f PR 3: the idle warden, shipped disabled.

Fake clocks, a fake bridge and a fake process guard. No real process is started.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_tool
from dayz_mcp.idle_warden import (
    EXIT_WAIT_S,
    WARNING_TEXT,
    IdleWarden,
    idle_warden_identity,
    install_idle_warden,
    read_idle_warden_enabled,
)
from dayz_mcp.input_activity import InputSample
from dayz_mcp.loopback import ServerState
from dayz_mcp.process_lifecycle import (
    RUN_IDLE_CUT_S,
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
    idle_destruction_guard,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import (
    MAX_OPERATION_TOMBSTONES,
    SESSION_TTL_S,
    CleanupDisposition,
    ClientIdentity,
    SessionCoordinator,
)
from dayz_mcp.window_close import Win32WindowFns
from tests.process_lifecycle_helpers import (
    IDENTITY_A,
    IDENTITY_B,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate


GENERATION = "gen-now"
OTHER_WINDOW_PID = 31337
LAUNCHER = ClientIdentity(
    "codex", 7, 1, "2026-07-15T00:00:02Z", "launcher-session", "launch"
)
STRANGER = ClientIdentity(
    "claude", 3, 1, "2026-07-15T00:00:03Z", "stranger", "fill"
)


class FakeBindings:
    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.retired: list[tuple[str, str]] = []

    def run_has_bound_binding(self, run_id: str) -> bool:
        return run_id in self.bound

    def fence_runs(self, run_ids: list[str]) -> None:
        return None

    def unfence_runs(self, run_ids: list[str]) -> None:
        return None

    def retire_run(self, run_id: str, reason: str) -> None:
        self.retired.append((run_id, reason))
        self.bound.discard(run_id)


class FakeWindows:
    def __init__(self) -> None:
        self.posted: list[tuple[int, int]] = []
        self.hwnd_of = {801: 1801, 802: 1802}

    def bind(self) -> Win32WindowFns:
        owner = self

        def enum_windows(callback):
            for hwnd in owner.hwnd_of.values():
                if callback(hwnd) is False:
                    return False
            return True

        def window_pid(hwnd: int) -> int:
            for pid, handle in owner.hwnd_of.items():
                if handle == hwnd:
                    return pid
            return 0

        def is_visible(_hwnd: int) -> bool:
            return True

        def post_message(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
            owner.posted.append((int(hwnd), int(msg)))
            return True

        return Win32WindowFns(
            enum_windows=enum_windows,
            window_pid=window_pid,
            is_visible=is_visible,
            post_message=post_message,
        )


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.scripts: dict[str, dict[str, object] | None] = {}
        self.enqueue_status = 200
        self.token: str | None = "instance-1|1|9|2026-09-29T00:00:00Z"
        self.abandoned: list[tuple[int, str]] = []
        self._seq = 0
        self._cmd: dict[int, str] = {}

    def bound_instance_token(self, run_id: str, role: str = "server") -> str | None:
        _ = (run_id, role)
        return self.token

    def enqueue_command(
        self,
        cmd: str,
        args: dict,
        peer: str | None = None,
        *,
        identity_payload: object = None,
        lease_token: str | None = None,
        operation_timeout_s: float = 0.0,
        internal: bool = False,
    ) -> tuple[int, dict]:
        self.calls.append(
            {
                "cmd": cmd,
                "args": dict(args),
                "peer": peer,
                "identity": identity_payload,
                "lease_token": lease_token,
                "operation_timeout_s": operation_timeout_s,
                "internal": internal,
            }
        )
        if self.enqueue_status != 200:
            return self.enqueue_status, {"error": "bridge_down"}
        self._seq += 1
        self._cmd[self._seq] = cmd
        return 200, {"id": self._seq, "peer": peer, "cmd": cmd}

    def take_result(self, command_id: int, remove: bool = False) -> dict | None:
        _ = remove
        cmd = self._cmd.get(command_id)
        if cmd in self.scripts:
            value = self.scripts[cmd]
            return None if value is None else dict(value)
        if cmd == "query_all_players":
            return {
                "ok": True,
                "players": [
                    {"uid": "76561198000000001"},
                    {"uid": "76561198000000002"},
                ],
            }
        if cmd == "notify_players":
            return {"ok": 1}
        return None

    def abandon_command(self, command_id: int, reason: str) -> None:
        self.abandoned.append((command_id, reason))


class SettingsReaderTest(unittest.TestCase):
    def test_only_exact_enabled_true_is_on(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            missing = root / "missing.json"
            self.assertFalse(read_idle_warden_enabled(missing))
            directory = root / "nested"
            directory.mkdir()
            self.assertFalse(read_idle_warden_enabled(directory))
            broken = root / "broken.json"
            broken.write_text("{", encoding="utf-8")
            self.assertFalse(read_idle_warden_enabled(broken))
            bom = root / "bom.json"
            bom.write_bytes(b'\xef\xbb\xbf{"enabled": true}\n')
            self.assertFalse(read_idle_warden_enabled(bom))
            for text in (
                '{"enabled": "true"}',
                '{"enabled": 1}',
                '{"enabled": false}',
                "{}",
                '{"enabled": true, "extra": 1}',
            ):
                path = root / "off.json"
                path.write_text(text, encoding="utf-8")
                self.assertFalse(read_idle_warden_enabled(path), text)
            on = root / "on.json"
            on.write_text('{"enabled": true}\n', encoding="utf-8")
            self.assertTrue(read_idle_warden_enabled(on))


class IdleWardenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.settings = self.paths.root / "idle-warden.json"
        self.windows = FakeWindows()
        self.holder: dict[str, ProcessLifecycle | None] = {"lifecycle": None}
        self._ids = 0
        self.clock = [0.0]
        self.rebuild()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _token(self) -> str:
        self._ids += 1
        return f"token-{self._ids}"

    def _lease(self) -> str:
        self._ids += 1
        return f"lease-{self._ids}"

    def _cleanup(self, session_id: str, lease_id: str, reason: str, vehicle: bool):
        _ = (reason, vehicle)
        lifecycle = self.holder.get("lifecycle")
        if lifecycle is None:
            return {}
        return lifecycle.begin_release_owner(session_id, lease_id)

    def rebuild(self, **coordinator_kwargs: object) -> None:
        # A new runs.json each time: the cases that rebuild are independent,
        # and RunManifestStore reloads whatever is already on that path.
        self._ids += 1
        slot = self.root / f"rt-{self._ids}"
        self.paths = RuntimePaths(
            slot,
            slot / "audit",
            slot / "coordination.json",
            slot / "runs.json",
        )
        self.settings = slot / "idle-warden.json"
        audit = coordinator_kwargs.pop("audit", None)
        self.audit = audit if isinstance(audit, AuditSink) else AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=self._token,
            id_fn=self._lease,
            audit=self.audit,
            cleanup=self._cleanup,
            **coordinator_kwargs,
        )
        self.store = RunManifestStore(self.paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.live: list[int] = []
        self.retail_processes: list[dict[str, object]] = []
        self.diag_known = True
        self.bindings = FakeBindings()
        self.bridge = FakeBridge()
        self.phases: list = []
        self.mono = 0.0
        self.wall_value = 0.0
        self.windows.posted.clear()
        self.lifecycle = self._lifecycle()
        self.holder["lifecycle"] = self.lifecycle
        self.lifecycle.window_fns = self.windows.bind()

    def _lifecycle(self) -> ProcessLifecycle:
        return ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {
                "known": True,
                "processes": list(self.retail_processes),
            },
            diag_probe=lambda: (
                {"known": False}
                if not self.diag_known
                else {
                    "known": True,
                    "processes": [
                        {"pid": pid, "name": "DayZDiag_x64.exe"} for pid in self.live
                    ],
                }
            ),
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            bindings=self.bindings,
            daemon_generation=GENERATION,
        )

    def add_run(
        self,
        run_id: str = "run-x",
        *,
        owner: str | None = None,
        processes=None,
        generation: str | None = GENERATION,
        live: bool = True,
    ) -> RunRecord:
        records = processes if processes is not None else [
            process(801, role="server"),
            process(802, role="client"),
        ]
        run = RunRecord(
            run_id,
            owner,
            "lease-A" if owner else None,
            "RUNNING" if owner else "RUNNING_IDLE",
            "label",
            "@Mod",
            "profiles",
            "mission",
            list(records),
            daemon_generation_at_launch=generation,
        )
        self.store.add(run)
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
            if live:
                self.live.append(record.pid)
        return run

    def good_signal(
        self, at: float, *, foreground: int = OTHER_WINDOW_PID, tick: int = 100
    ) -> None:
        self.lifecycle.record_input_sample(
            InputSample(
                at=at,
                ok=True,
                last_input_tick=tick,
                idle_ms=0,
                foreground_pid=foreground,
            )
        )

    def healthy_signal(self, start: float, until: float, step: float = 1.5) -> None:
        at = start
        while at < until:
            self.good_signal(at)
            at += step
        self.good_signal(until)

    def human_input(self, at: float, pid: int = 802) -> list[str]:
        self.good_signal(at - 0.25, foreground=pid, tick=1000)
        return self.lifecycle.record_input_sample(
            InputSample(
                at=at, ok=True, last_input_tick=1100, idle_ms=0, foreground_pid=pid
            )
        )

    def row(self, now: float, run_id: str = "run-x") -> dict[str, object]:
        box = self.lifecycle.box_occupancy(now=now)
        for item in box["runs"]:
            if item["run_id"] == run_id:
                return item
        self.fail(f"no row for {run_id}: {box['runs']}")

    def abandon(
        self,
        *,
        dead: bool = False,
        generation: str | None = GENERATION,
        run_id: str = "run-x",
        signal: bool = True,
    ) -> float:
        self.add_run(run_id, owner="A", generation=generation, live=not dead)
        self.assertEqual(self.lifecycle.release_owner("A", "lease-A"), [run_id])
        # adopt_run classifies with its own clock (time.time). Backdate the
        # ownerless stamp so that clock and the warden's wall agree on the cut.
        stamp = time.time() - (RUN_IDLE_CUT_S + 1.0)
        self.lifecycle.restore_ownerless_since(run_id, stamp)
        self.wall_value = stamp + RUN_IDLE_CUT_S + 1.0
        if not dead and signal:
            self.bindings.bound.add(run_id)
            self.healthy_signal(float(stamp), self.wall_value)
        return self.wall_value

    def enable(self) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text('{"enabled": true}\n', encoding="utf-8")

    def advance(self, seconds: float) -> None:
        self.mono += float(seconds)

    def quiet_sample(self) -> InputSample:
        return InputSample(
            at=self.wall_value,
            ok=True,
            last_input_tick=100,
            idle_ms=0,
            foreground_pid=OTHER_WINDOW_PID,
        )

    def human_sample(self) -> InputSample:
        return InputSample(
            at=self.wall_value,
            ok=True,
            last_input_tick=1100,
            idle_ms=0,
            foreground_pid=802,
        )

    def _phase(self, name: str, warden: IdleWarden) -> None:
        for hook in list(self.phases):
            hook(name, warden)

    def make_warden(self, **kwargs: object) -> IdleWarden:
        options: dict[str, object] = dict(
            lifecycle=self.lifecycle,
            coordinator=self.coordinator,
            bridge=self.bridge,
            settings_path=self.settings,
            generation=GENERATION,
            wall=lambda: self.wall_value,
            monotonic=lambda: self.mono,
            sleep=self.advance,
            wait_slice_s=0.0,
            poll_s=60.0,
            sample_input=self.quiet_sample,
            phase=self._phase,
        )
        options.update(kwargs)
        return IdleWarden(**options)

    def drive(self, **kwargs: object) -> str:
        self.enable()
        return self.make_warden(**kwargs).run_once()

    def kill_all(self) -> None:
        for pid in list(self.guard.snapshots):
            self.guard.snapshots[pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
        self.live.clear()

    def mark_guard_gone(self) -> None:
        """Guard says the launched PIDs are gone. The diag scan is left as it is."""

        for pid in list(self.guard.snapshots):
            self.guard.snapshots[pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }

    def make_foreign(self, pid: int) -> None:
        current = dict(self.guard.snapshots[pid])
        current["executable_sha256"] = "c" * 64
        current["identity_complete"] = True
        self.guard.snapshots[pid] = current

    def on_exit(self, name: str, _warden: IdleWarden) -> None:
        if name == "waiting_exit":
            self.kill_all()

    def commands(self) -> list[object]:
        return [call["cmd"] for call in self.bridge.calls]

    def assert_abandoned(self, run_id: str = "run-x", reason: str | None = None) -> None:
        row = self.row(self.wall_value, run_id)
        self.assertEqual(row["use_state"], "abandoned")
        if reason is not None:
            self.assertEqual(row["use_reason"], reason)

    def assert_retired(self, decision: str, run_id: str = "run-x") -> None:
        matched = [
            event
            for event in self.audit.events
            if event.get("event") == "idle_timeout" and event.get("decision") == decision
        ]
        self.assertTrue(matched, self.audit.events)
        self.assertTrue(all(event.get("reason") == "idle_timeout" for event in matched))
        self.assertTrue(all(event.get("run_id") == run_id for event in matched))
        raw = self.lifecycle.public_status()["retired_run_diagnostics"]
        self.assertTrue(
            any(
                item.get("run_id") == run_id
                and item.get("event") == "idle_timeout"
                and item.get("reason") == "idle_timeout"
                and item.get("decision") == decision
                for item in raw
            ),
            raw,
        )
        published = dayz_test_tool._runs_retired_recently(raw)
        self.assertIsNotNone(published)
        self.assertTrue(
            any(
                item.get("run_id") == run_id
                and item.get("reason") == "idle_timeout"
                and item.get("decision") == decision
                for item in published
            ),
            published,
        )

    def assert_not_retired(self, run_id: str = "run-x") -> None:
        raw = self.lifecycle.public_status()["retired_run_diagnostics"]
        self.assertFalse(any(item.get("run_id") == run_id for item in raw), raw)
        failed = [
            event
            for event in self.audit.events
            if event.get("event") == "idle_timeout" and event.get("decision") == "failed"
        ]
        self.assertTrue(failed, self.audit.events)
        self.assertTrue(all(event.get("reason") == "idle_timeout" for event in failed))

    def test_identity_is_the_internal_client(self) -> None:
        client = idle_warden_identity(
            GENERATION, pid=4, ppid=5, started_at_utc="2026-09-29T00:00:00Z"
        )
        self.assertEqual(client.platform, "unknown")
        self.assertEqual(client.session_id, f"idle-warden-{GENERATION}")
        self.assertEqual(client.task_label, "idle_timeout")
        self.assertEqual(client.public_payload()["session"], "idle-warden-")

    def test_disabled_has_no_side_effect(self) -> None:
        self.clock = [0.0]
        self.rebuild(time_fn=lambda: self.clock[0])
        status, _body = self.coordinator.acquire(IDENTITY_A, "hold")
        self.assertEqual(status, 200)
        self.abandon()
        self.assert_abandoned()
        self.lifecycle.remember_launcher("run-x", LAUNCHER)
        queued, _ticket = self.coordinator.enqueue(LAUNCHER, "launch", "op-launch")
        self.assertEqual(queued, 202)
        # enqueue itself expires due leases. Advance only after the ticket is
        # in, so a disabled pass is the only thing that could drop either one.
        self.clock[0] = SESSION_TTL_S + 5
        warden = self.make_warden()
        self.assertEqual(warden.run_once(), "disabled")
        self.assertIsNotNone(self.coordinator._active)
        self.assertIsNone(self.lifecycle.launcher_request_at("run-x"))
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.windows.posted, [])
        run = self.store.get("run-x")
        self.assertEqual(run.state, "RUNNING_IDLE")
        self.assertIsNone(run.owner_session_id)
        self.assertEqual(
            self.coordinator.queued_session_ids(), (LAUNCHER.session_id,)
        )
        self.assertIsNone(warden.token)
        self.assertIsNone(warden.ticket_id)

    def test_enabled_cycle_expires_a_due_lease(self) -> None:
        self.clock = [0.0]
        self.rebuild(time_fn=lambda: self.clock[0])
        status, _body = self.coordinator.acquire(IDENTITY_A, "hold")
        self.assertEqual(status, 200)
        self.clock[0] = SESSION_TTL_S + 5
        self.assertEqual(self.drive(), "no_candidate")
        self.assertIsNone(self.coordinator._active)

    def test_live_orderly_warns_then_closes(self) -> None:
        self.abandon()
        self.assert_abandoned()
        seen: dict[str, object] = {}

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "warning":
                seen["use"] = self.row(self.wall_value)["use_state"]
            self.on_exit(name, _warden)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "orderly")
        self.assertEqual(seen["use"], "closing")
        self.assertEqual(
            self.commands(),
            ["query_all_players", "notify_players", "notify_players"],
        )
        self.assertTrue(all(call["internal"] is False for call in self.bridge.calls))
        self.assertTrue(all(call["peer"] == "server" for call in self.bridge.calls))
        for call in self.bridge.calls[1:]:
            self.assertEqual(call["args"]["title"], WARNING_TEXT)
            self.assertEqual(call["args"]["show_time"], 60)
        self.assertEqual(
            [call["args"]["uid"] for call in self.bridge.calls[1:]],
            ["76561198000000001", "76561198000000002"],
        )
        identity = self.bridge.calls[0]["identity"]
        self.assertEqual(identity["session_id"], f"idle-warden-{GENERATION}")
        self.assertIsInstance(self.bridge.calls[0]["lease_token"], str)
        self.assertTrue(self.windows.posted)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get("run-x").state, "EXITED")
        self.assert_retired("orderly")
        self.assertIsNone(self.coordinator._active)

    def test_dead_client_closes_without_warning_or_closing(self) -> None:
        self.abandon(dead=True)
        self.assert_abandoned(reason="client_gone")
        seen: dict[str, object] = {}

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "revalidate_close":
                seen["use"] = self.row(self.wall_value)["use_state"]
            self.on_exit(name, _warden)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "orderly")
        self.assertNotEqual(seen.get("use"), "closing")
        self.assertEqual(self.bridge.calls, [])
        self.assertTrue(self.windows.posted)
        self.assert_retired("orderly")

    def test_fallback_stop_when_processes_survive_the_wait(self) -> None:
        self.abandon()
        self.assertEqual(self.drive(), "fallback_stop")
        self.assertTrue(self.windows.posted)
        self.assertTrue(self.guard.terminate_calls)
        self.assertEqual(self.store.get("run-x").state, "EXITED")
        self.assert_retired("fallback_stop")
        self.assertIsNone(self.coordinator._active)

    def _fail_detail(self) -> str:
        failed = [
            event
            for event in self.audit.events
            if event.get("event") == "idle_timeout" and event.get("decision") == "failed"
        ]
        self.assertEqual(len(failed), 1, self.audit.events)
        detail = failed[0].get("detail")
        self.assertIsInstance(detail, str)
        assert isinstance(detail, str)
        return detail

    def test_role_exits_fifteen_seconds_after_close_is_orderly(self) -> None:
        """A role the diag scan still sees for 15 s after WM_CLOSE is orderly.

        The guard reports the pid gone on the first exit poll. Reap stays
        ``run_not_reapable`` until the scan drops the pid.
        """

        self.abandon()
        started: dict[str, float] = {}

        def hook(name: str, _warden: IdleWarden) -> None:
            if name != "waiting_exit":
                return
            started.setdefault("at", self.mono)
            self.mark_guard_gone()
            if self.mono - started["at"] >= 15.0:
                self.live.clear()

        self.phases.append(hook)
        self.assertEqual(self.drive(poll_s=5.0), "orderly")
        self.assertIn("at", started)
        waited = self.mono - started["at"]
        self.assertGreaterEqual(waited, 10.0)
        self.assertLessEqual(waited, 20.0)
        self.assertTrue(self.windows.posted)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get("run-x").state, "EXITED")
        self.assert_retired("orderly")
        self.assertIsNone(self.coordinator._active)

    def test_role_alive_through_the_exit_budget_names_processes(self) -> None:
        """The diag scan still sees both roles when EXIT_WAIT_S runs out."""

        self.abandon()
        started: dict[str, float] = {}

        def hook(name: str, _warden: IdleWarden) -> None:
            if name != "waiting_exit":
                return
            started.setdefault("at", self.mono)
            self.mark_guard_gone()

        self.phases.append(hook)
        self.assertEqual(self.drive(), "failed")
        self.assertIn("at", started)
        waited = self.mono - started["at"]
        self.assertGreaterEqual(waited, EXIT_WAIT_S)
        self.assertLess(waited, EXIT_WAIT_S + 1.0)
        self.assertEqual(
            self._fail_detail(),
            "reap_failed; processes=801:server,802:client",
        )
        self.assertTrue(self.windows.posted)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertNotEqual(self.store.get("run-x").state, "EXITED")
        self.assert_not_retired()
        self.assertIsNone(self.coordinator._active)

    def test_reap_not_reapable_then_retry_is_orderly(self) -> None:
        self.abandon()
        original = self.lifecycle.reap_dead_run
        calls: list[int] = []

        def wrapped(client, token, run_id):
            calls.append(1)
            if len(calls) >= 2:
                self.live.clear()
            return original(client, token, run_id)

        self.lifecycle.reap_dead_run = wrapped

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "waiting_exit":
                self.mark_guard_gone()

        self.phases.append(hook)
        try:
            self.assertEqual(self.drive(), "orderly")
        finally:
            self.lifecycle.reap_dead_run = original
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get("run-x").state, "EXITED")
        self.assert_retired("orderly")
        self.assertIsNone(self.coordinator._active)

    def test_mixed_identity_before_close_closes_nothing(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "revalidate_close":
                self.make_foreign(802)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "failed")
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.guard.terminate_calls, [])
        run = self.store.get("run-x")
        self.assertNotEqual(run.state, "EXITED")
        self.assertEqual(len(run.processes), 2)
        self.assert_not_retired()

    def test_scan_unknown_before_stop_does_not_stop(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "waiting_exit":
                self.diag_known = False

        self.phases.append(hook)
        self.assertEqual(self.drive(), "failed")
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertNotEqual(self.store.get("run-x").state, "EXITED")
        self.assert_not_retired()

    def test_identity_change_before_stop_does_not_stop(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "waiting_exit":
                self.make_foreign(802)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "failed")
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertNotEqual(self.store.get("run-x").state, "EXITED")
        self.assert_not_retired()

    def test_scan_change_during_countdown_zeros_the_clock(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "countdown":
                self.diag_known = False

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.diag_known = True
        self.assertEqual(self.row(self.wall_value)["use_state"], "idle")

    def test_process_change_during_countdown_zeros_the_clock(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "countdown":
                self.make_foreign(802)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.guard.snapshots[802] = snapshot(process(802, role="client"))
        self.assertEqual(self.row(self.wall_value)["use_state"], "idle")

    def test_quarantine_during_countdown_zeros_the_clock(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "countdown":
                self.retail_processes.append({"pid": 9, "name": "DayZ_x64.exe"})

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.retail_processes.clear()
        self.assertEqual(self.row(self.wall_value)["use_state"], "idle")

    def test_input_during_the_queue_cancels(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "queued":
                self.human_input(self.wall_value)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "cancelled")
        run = self.store.get("run-x")
        self.assertEqual(run.state, "RUNNING_IDLE")
        self.assertIsNone(run.owner_session_id)
        self.assertEqual(self.row(self.wall_value)["use_state"], "human")
        self.assertIsNone(self.coordinator._active)
        self.assertEqual(self.windows.posted, [])

    def test_input_during_the_warning_cancels(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "warning":
                self.human_input(self.wall_value)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertNotIn("notify_players", self.commands())
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.row(self.wall_value)["use_state"], "human")

    def test_input_during_countdown_cancels(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "countdown":
                self.human_input(self.wall_value)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.row(self.wall_value)["use_state"], "human")

    def test_input_just_after_the_last_sample_cancels_before_close(self) -> None:
        self.abandon()

        def hook(name: str, warden: IdleWarden) -> None:
            if name == "revalidate_close":
                warden.sample_input = self.human_sample

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.row(self.wall_value)["use_state"], "human")

    def test_launcher_ticket_before_the_grant_is_not_a_candidate(self) -> None:
        self.abandon()
        self.assert_abandoned()
        self.lifecycle.remember_launcher("run-x", LAUNCHER)
        status, _body = self.coordinator.enqueue(LAUNCHER, "launch", "op-launch")
        self.assertEqual(status, 202)
        self.assertEqual(self.drive(), "no_candidate")
        self.assertEqual(
            self.coordinator.queued_session_ids(), (LAUNCHER.session_id,)
        )
        self.assertEqual(self.row(self.wall_value)["use_state"], "idle")
        self.assertEqual(self.bridge.calls, [])

    def test_launcher_lifecycle_call_restarts_the_clock(self) -> None:
        self.add_run(owner="A")
        self.lifecycle.release_owner("A", "lease-A")
        past = time.time() - (RUN_IDLE_CUT_S + 1)
        self.lifecycle.restore_ownerless_since("run-x", past)
        self.bindings.bound.add("run-x")
        self.healthy_signal(past, time.time())
        now = time.time()
        self.healthy_signal(now - 1.0, now)
        self.lifecycle.remember_launcher("run-x", LAUNCHER)
        self.assertEqual(self.row(time.time())["use_state"], "abandoned")
        result = self.lifecycle.adopt_run(LAUNCHER, None, "run-x")
        self.assertEqual(result.get("error"), "lease_required")
        self.assertIsNotNone(self.lifecycle.launcher_request_at("run-x"))
        self.assertNotEqual(self.row(time.time())["use_state"], "abandoned")

    def test_launcher_during_the_warning_cancels(self) -> None:
        self.abandon()
        self.lifecycle.remember_launcher("run-x", LAUNCHER)

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "warning":
                self.assertTrue(
                    self.lifecycle.note_launcher_request(
                        LAUNCHER, "run-x", when=self.wall_value
                    )
                )

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertNotIn("notify_players", self.commands())
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.row(self.wall_value)["use_state"], "idle")

    def test_launcher_during_countdown_cancels(self) -> None:
        self.abandon()
        self.lifecycle.remember_launcher("run-x", LAUNCHER)

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "countdown":
                self.lifecycle.note_launcher_request(
                    LAUNCHER, "run-x", when=self.wall_value
                )

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.row(self.wall_value)["use_state"], "idle")

    def test_launcher_just_before_wm_close_cancels(self) -> None:
        self.abandon()
        self.lifecycle.remember_launcher("run-x", LAUNCHER)

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "revalidate_close":
                self.lifecycle.note_launcher_request(
                    LAUNCHER, "run-x", when=self.wall_value
                )

        self.phases.append(hook)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])

    def test_another_session_ahead_in_the_queue_blocks_the_warden(self) -> None:
        self.abandon(dead=True)
        status, _body = self.coordinator.enqueue(IDENTITY_B, "work", "op-b")
        self.assertEqual(status, 202)
        self.phases.append(self.on_exit)
        self.enable()
        warden = self.make_warden()
        self.assertEqual(warden.run_once(), "queued")
        self.assertIsNone(self.store.get("run-x").owner_session_id)
        self.assertIn(warden.client.session_id, self.coordinator.queued_session_ids())
        self.assertEqual(self.coordinator.cancel_operation(IDENTITY_B, "op-b")[0], 200)
        self.assertEqual(warden.run_once(), "orderly")
        self.assertEqual(self.bridge.calls, [])
        self.assert_retired("orderly")

    def test_late_cancel_while_queued_does_not_adopt(self) -> None:
        self.abandon()

        def hook(name: str, warden: IdleWarden) -> None:
            if name == "queued":
                status, _body = self.coordinator.cancel_operation(
                    warden.client, warden.operation_id or ""
                )
                self.assertEqual(status, 200)

        self.phases.append(hook)
        # Cancel removes the ticket. The following wait sees ticket_invalid,
        # which the warden reports as wait_failed and does not adopt on.
        self.assertEqual(self.drive(), "wait_failed")
        self.assertIsNone(self.coordinator._active)
        self.assertIsNone(self.store.get("run-x").owner_session_id)
        self.assertEqual(self.windows.posted, [])

    def test_cancel_during_grant_audit_publishes_no_lease(self) -> None:
        self.abandon(dead=True)
        sink = self.audit
        holder: dict[str, object] = {"warden": None}

        def wrapped(event: dict[str, object]) -> bool:
            ok = sink(event)
            warden = holder["warden"]
            if (
                event.get("event") == "session_grant_prepared"
                and isinstance(warden, IdleWarden)
            ):
                # The grant holds the audit gate across this callback. Cancelling
                # on this thread deadlocks on that gate; another thread tombstones
                # the operation, then blocks until the grant releases the gate.
                op = warden.operation_id or ""
                key = (warden.client, op)
                worker = threading.Thread(
                    target=lambda: self.coordinator.cancel_operation(
                        warden.client, op
                    ),
                    daemon=True,
                )
                holder["worker"] = worker
                worker.start()
                deadline = time.monotonic() + 2.0
                while key not in self.coordinator._operation_tombstones:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.005)
            return ok

        self.coordinator._audit = wrapped
        self.enable()
        warden = self.make_warden()
        holder["warden"] = warden
        self.assertEqual(warden.run_once(), "cancelled")
        worker = holder.get("worker")
        if isinstance(worker, threading.Thread):
            worker.join(2.0)
            self.assertFalse(worker.is_alive())
        self.assertIsNone(self.coordinator._active)
        self.assertIsNone(self.store.get("run-x").owner_session_id)

    def test_tombstone_saturation_refuses_the_enqueue(self) -> None:
        for index in range(MAX_OPERATION_TOMBSTONES):
            status, body = self.coordinator.cancel_operation(STRANGER, f"never-{index}")
            self.assertEqual(status, 200, body)
        self.abandon()
        self.assertEqual(self.drive(), "enqueue_failed")
        self.assertIsNone(self.store.get("run-x").owner_session_id)
        self.assertEqual(self.bridge.calls, [])

    def test_wal_failure_does_not_adopt(self) -> None:
        def boom(_marker: dict) -> str:
            raise RuntimeError("wal down")

        self.rebuild(
            fault_arm=boom,
            fault_transition=lambda _marker: "a" * 64,
            fault_clear=lambda _marker: True,
            persist_snapshot=lambda _snapshot: None,
        )
        self.abandon(dead=True)
        self.assertEqual(self.drive(), "wait_failed")
        run = self.store.get("run-x")
        self.assertEqual(run.state, "RUNNING_IDLE")
        self.assertIsNone(run.owner_session_id)
        self.assertEqual(self.windows.posted, [])

    def test_grant_audit_failure_does_not_adopt(self) -> None:
        self.audit.fail_events.add("session_grant_prepared")
        self.abandon(dead=True)
        self.assertEqual(self.drive(), "wait_failed")
        self.assertIsNone(self.store.get("run-x").owner_session_id)
        self.assertEqual(self.windows.posted, [])

    def test_queue_audit_failure_does_not_adopt(self) -> None:
        self.audit.fail_events.add("session_queued")
        self.abandon(dead=True)
        self.assertEqual(self.drive(), "enqueue_failed")
        self.assertIsNone(self.store.get("run-x").owner_session_id)

    def test_cleanup_saturation_releases_without_closing(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "countdown":
                for _index in range(4):
                    self.assertTrue(
                        self.coordinator._cleanup_worker_slots.acquire(blocking=False)
                    )
                self.human_input(self.wall_value)

        self.phases.append(hook)
        try:
            self.assertEqual(self.drive(), "countdown_aborted")
        finally:
            for _index in range(4):
                self.coordinator._cleanup_worker_slots.release()
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get("run-x").state, "RUNNING_IDLE")

    def test_ticket_ttl_does_not_adopt(self) -> None:
        self.clock = [0.0]
        self.rebuild(time_fn=lambda: self.clock[0])
        self.abandon(dead=True)

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "queued":
                self.clock[0] += SESSION_TTL_S + 1

        self.phases.append(hook)
        self.assertEqual(self.drive(), "wait_failed")
        self.assertIsNone(self.store.get("run-x").owner_session_id)
        self.assertEqual(self.windows.posted, [])

    def test_grace_and_pref_renewal_then_the_warden_closes(self) -> None:
        self.clock = [0.0]
        self.rebuild(
            time_fn=lambda: self.clock[0],
            attached_run_probe=lambda _session, _lease: True,
        )
        status, _body = self.coordinator.acquire(IDENTITY_A, "owner")
        self.assertEqual(status, 200)
        self.abandon(dead=True)
        self.phases.append(self.on_exit)
        self.enable()
        warden = self.make_warden()
        self.clock[0] = 100
        self.assertEqual(warden.run_once(), "queued")
        self.assertIsNone(self.store.get("run-x").owner_session_id)
        self.clock[0] = 130
        self.assertEqual(warden.run_once(), "queued")
        self.assertEqual(self.store.get("run-x").state, "RUNNING_IDLE")
        renewed, _renewed_body = self.coordinator.acquire(IDENTITY_A, "owner")
        self.assertEqual(renewed, 200)
        self.assertEqual(self.coordinator._pref_used.get(IDENTITY_A.session_id), 1)
        self.clock[0] = 200
        self.assertEqual(warden.run_once(), "queued")
        self.clock[0] = 260
        jumped, jumped_body = self.coordinator.acquire(IDENTITY_A, "again")
        self.assertEqual(jumped, 202, jumped_body)
        self.assertEqual(warden.run_once(), "orderly")
        self.assertEqual(self.bridge.calls, [])
        self.assert_retired("orderly")

    def _assert_warning_failed(self, **kwargs: object) -> None:
        self.abandon()
        self.assert_abandoned()
        scripts = kwargs.get("scripts")
        if isinstance(scripts, dict):
            self.bridge.scripts.update(scripts)
        status = kwargs.get("enqueue_status")
        if isinstance(status, int):
            self.bridge.enqueue_status = status
        extra: dict[str, object] = {}
        if kwargs.get("timeout"):
            extra["bridge_timeout_s"] = 0.0
        self.assertEqual(self.drive(**extra), "warning_failed")
        row = self.row(self.wall_value)
        self.assertEqual(
            (row["use_state"], row["use_reason"]),
            ("idle_waiting", "warning_failed"),
        )
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.guard.terminate_calls, [])

    def test_warning_query_error_blocks_for_five_minutes(self) -> None:
        self._assert_warning_failed(scripts={"query_all_players": {"error": "bridge_down"}})
        end = self.wall_value + 301
        self.healthy_signal(self.wall_value, end)
        self.assertEqual(self.row(end)["use_state"], "abandoned")

    def test_warning_query_transport_failure(self) -> None:
        self._assert_warning_failed(enqueue_status=500)

    def test_warning_no_players(self) -> None:
        self._assert_warning_failed(
            scripts={"query_all_players": {"ok": True, "players": []}}
        )

    def test_warning_notify_not_ok(self) -> None:
        self._assert_warning_failed(
            scripts={"notify_players": {"ok": False, "error": "player_not_found"}}
        )
        self.assertIn("notify_players", self.commands())

    def test_warning_query_timeout(self) -> None:
        self._assert_warning_failed(scripts={"query_all_players": None}, timeout=True)
        self.assertTrue(self.bridge.abandoned)

    def test_binding_change_between_query_and_notify(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "before_notify":
                self.bridge.token = "other-binding"

        self.phases.append(hook)
        self.assertEqual(self.drive(), "warning_failed")
        self.assertEqual(self.commands(), ["query_all_players"])
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(
            self.row(self.wall_value)["use_reason"], "warning_failed"
        )

    def test_generation_change_between_query_and_notify(self) -> None:
        self.abandon()

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "before_notify":
                run = self.store.get("run-x")
                run.daemon_generation_at_launch = "gen-other"
                self.store.replace(run)

        self.phases.append(hook)
        self.assertEqual(self.drive(), "warning_failed")
        self.assertEqual(self.commands(), ["query_all_players"])
        self.assertEqual(self.windows.posted, [])

    def test_several_active_runs_are_not_a_candidate(self) -> None:
        for run_id in ("run-a", "run-b"):
            self.add_run(run_id)
            self.lifecycle.restore_ownerless_since(run_id, 10_000.0)
            self.bindings.bound.add(run_id)
        self.healthy_signal(10_000.0, 10_601.0)
        self.wall_value = 10_601.0
        box = self.lifecycle.box_occupancy(now=self.wall_value)
        self.assertEqual(len(box["runs"]), 2)
        self.assertTrue(
            all(item["use_reason"] == "multiple_active_runs" for item in box["runs"])
        )
        self.assertEqual(self.drive(), "no_candidate")
        self.assertEqual(self.coordinator.queued_session_ids(), ())

    def test_unknown_scan_is_not_a_candidate(self) -> None:
        self.abandon()
        self.diag_known = False
        self.assertEqual(self.drive(), "no_candidate")
        self.assertEqual(self.coordinator.queued_session_ids(), ())

    def test_retail_quarantine_is_not_a_candidate_even_if_abandoned(self) -> None:
        self.abandon()
        self.retail_processes.append({"pid": 9, "name": "DayZ_x64.exe"})
        self.assertEqual(self.row(self.wall_value)["use_state"], "abandoned")
        self.assertEqual(self.drive(), "no_candidate")
        self.assertEqual(self.coordinator.queued_session_ids(), ())
        self.assertEqual(self.bridge.calls, [])

    def test_owned_human_idle_unknown_and_unreconciled_are_left_alone(self) -> None:
        self.add_run(owner="A")
        self.assertEqual(self.row(time.time())["use_state"], "agent")
        self.assertEqual(self.drive(), "no_candidate")
        self.assertEqual(self.coordinator.queued_session_ids(), ())

        self.rebuild()
        self.add_run()
        self.lifecycle.release_owner("A", "lease-A") if False else None
        self.bindings.bound.add("run-x")
        now = time.time()
        self.lifecycle.restore_ownerless_since("run-x", now)
        self.healthy_signal(now, now)
        self.human_input(now)
        self.wall_value = now
        self.assertEqual(self.row(now)["use_state"], "human")
        self.assertEqual(self.drive(), "no_candidate")

        self.rebuild()
        self.add_run()
        now = time.time()
        self.lifecycle.restore_ownerless_since("run-x", now)
        self.bindings.bound.add("run-x")
        self.healthy_signal(now, now)
        self.wall_value = now
        self.assertEqual(self.row(now)["use_state"], "idle")
        self.assertEqual(self.drive(), "no_candidate")

        self.rebuild()
        self.add_run()
        self.wall_value = time.time()
        self.assertEqual(self.row(self.wall_value)["use_state"], "unknown")
        self.assertEqual(self.drive(), "no_candidate")

        self.rebuild()
        self.add_run()
        run = self.store.get("run-x")
        run.state = "UNRECONCILED"
        self.store.replace(run)
        self.assertEqual(self.drive(), "no_candidate")
        self.assertEqual(self.coordinator.queued_session_ids(), ())

    def test_restart_during_countdown_discards_it(self) -> None:
        self.add_run(owner="A", live=True)
        self.lifecycle.mark_closing("run-x")
        self.assertEqual(self.row(time.time())["use_state"], "closing")
        self.store.recover_after_restart()
        self.bindings = FakeBindings()
        self.lifecycle = self._lifecycle()
        self.holder["lifecycle"] = self.lifecycle
        self.lifecycle.window_fns = self.windows.bind()
        text = self.paths.runs_path.read_text(encoding="utf-8")
        self.assertNotIn("closing", text)
        self.assertNotIn("idle-warden", text)
        row = self.row(time.time())
        self.assertNotEqual(row["use_state"], "closing")
        self.assertEqual(row["use_state"], "unknown")
        self.assertEqual(self.drive(), "no_candidate")
        self.assertEqual(self.bridge.calls, [])

    def test_restart_mid_stop_is_unreconciled(self) -> None:
        self.add_run(owner="A")
        run = self.store.get("run-x")
        run.state = "STOPPING"
        self.store.replace(run)
        recovered = self.store.recover_after_restart()
        self.assertIn("run-x", recovered["unreconciled"])
        self.bindings = FakeBindings()
        self.lifecycle = self._lifecycle()
        self.holder["lifecycle"] = self.lifecycle
        self.assertEqual(self.store.get("run-x").state, "UNRECONCILED")
        self.assertEqual(self.drive(), "no_candidate")

    def test_dead_client_after_restart_waits_until_600(self) -> None:
        self.add_run(generation="gen-before", live=False)
        # The restarted clock has no ownerless stamp. Put its 600 s point at
        # the real now: adopt_run classifies with time.time(), not the warden wall.
        now = time.time()
        self.lifecycle._use_clock_origin = now - 600.0
        origin = self.lifecycle._use_clock_origin
        self.assertIsNone(self.lifecycle.ownerless_since("run-x"))
        low = origin + 599
        high = origin + 600
        if self.row(high)["use_state"] != "abandoned":
            high = origin + 600.05
        self.assertEqual(self.row(low)["use_state"], "idle")
        self.assertEqual(self.row(high)["use_reason"], "client_gone")
        self.wall_value = low
        self.enable()
        warden = self.make_warden()
        self.assertEqual(warden.run_once(), "no_candidate")
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.coordinator.queued_session_ids(), ())
        self.wall_value = high
        self.phases.append(self.on_exit)
        self.assertEqual(warden.run_once(), "orderly")
        self.assertEqual(self.bridge.calls, [])
        self.assert_retired("orderly")

    def test_closing_beats_agent(self) -> None:
        self.add_run(owner="A")
        self.lifecycle.mark_closing("run-x")
        self.assertEqual(self.row(time.time())["use_state"], "closing")

    def test_queued_session_ids_do_not_expire(self) -> None:
        self.clock = [0.0]
        self.rebuild(time_fn=lambda: self.clock[0])
        status, _body = self.coordinator.enqueue(IDENTITY_A, "work", "op-keep")
        self.assertEqual(status, 202)
        self.clock[0] = SESSION_TTL_S + 5
        self.assertEqual(
            self.coordinator.queued_session_ids(), (IDENTITY_A.session_id,)
        )
        self.coordinator.expire_due()
        self.assertEqual(self.coordinator.queued_session_ids(), ())

    def test_input_inside_the_adoption_write_is_not_adopted(self) -> None:
        # Human input during the owner replace invalidates the stranger adopt.
        # The run goes back to ownerless RUNNING_IDLE. The warden must not warn
        # or close, and the unconfirmed marker must not stay behind.
        self.abandon()
        self.assert_abandoned()
        original = self.store.replace
        injected = False

        def replace_with_input(run):
            nonlocal injected
            if not injected:
                injected = True
                self.human_input(time.time())
            return original(run)

        self.store.replace = replace_with_input
        self.enable()
        warden = self.make_warden()
        try:
            result = warden.run_once()
        finally:
            self.store.replace = original
        self.assertEqual(result, "not_adopted")
        self.assertTrue(injected)
        stored = self.store.get("run-x")
        self.assertIsNotNone(stored)
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(stored.owner_lease_id)
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.windows.posted, [])
        self.assertIsNone(self.coordinator._active)
        self.assertIsNone(warden.token)
        self.assertEqual(self.lifecycle._warning_blocked_until, {})
        marker = self.lifecycle._adoption_marker_path()
        self.assertIsNotNone(marker)
        self.assertFalse(marker.exists())
        settled = json.loads(
            self.lifecycle._adoption_settled_path().read_text(encoding="utf-8")
        )
        self.assertTrue(settled["settled"])
        self.assertTrue(settled["reverted"])
        self.assertEqual(settled["invalidated_by"], "human")
        self.assertEqual(settled["owner_session_id"], warden.client.session_id)
        self.assertEqual(self.row(time.time())["use_state"], "human")

    def test_switch_off_during_countdown_and_before_stop_does_not_close(self) -> None:
        for phase_name in ("countdown", "revalidate_stop"):
            with self.subTest(phase=phase_name):
                self.rebuild()
                self.abandon()

                def hook(name: str, _warden: IdleWarden, expected: str = phase_name) -> None:
                    if name == expected:
                        self.settings.write_text(
                            '{"enabled": false}\n', encoding="utf-8"
                        )

                self.phases.append(hook)
                result = self.drive()
                self.assertEqual(result, "disabled")
                self.assertEqual(self.guard.terminate_calls, [])
                self.assertNotEqual(self.store.get("run-x").state, "EXITED")
                self.assertIsNone(self.coordinator._active)
                if phase_name == "countdown":
                    self.assertEqual(self.windows.posted, [])

    def test_launcher_enqueue_during_warning_countdown_and_close_cancels(self) -> None:
        for phase_name in ("warning", "countdown", "revalidate_close", "close_audit"):
            with self.subTest(phase=phase_name):
                self.rebuild()
                self.abandon()
                self.lifecycle.remember_launcher("run-x", LAUNCHER)
                seen: dict[str, object] = {}

                def enqueue_launcher(tag: str = phase_name) -> None:
                    status, body = self.coordinator.enqueue(
                        LAUNCHER, "launch", f"real-launcher-{tag}"
                    )
                    seen["status"] = status
                    seen["ticket"] = body.get("ticket") if isinstance(body, dict) else None

                if phase_name == "close_audit":
                    original_audit = self.lifecycle._audit

                    def audit(event, client, reason, decision, **extra):
                        if event == "lifecycle_close" and "status" not in seen:
                            enqueue_launcher()
                        return original_audit(event, client, reason, decision, **extra)

                    self.lifecycle._audit = audit
                else:

                    def hook(name: str, _warden: IdleWarden, expected: str = phase_name) -> None:
                        if name == expected:
                            enqueue_launcher(expected)

                    self.phases.append(hook)
                result = self.drive()
                self.assertEqual(seen.get("status"), 202)
                self.assertIsInstance(seen.get("ticket"), str)
                self.assertEqual(result, "countdown_aborted")
                self.assertIsNotNone(self.lifecycle.launcher_request_at("run-x"))
                self.assertEqual(self.windows.posted, [])
                self.assertEqual(self.guard.terminate_calls, [])
                self.assertIsNone(self.coordinator._active)
                self.assertEqual(self.row(self.wall_value)["use_state"], "idle")
                self.assertEqual(
                    self.coordinator.queued_session_ids(), (LAUNCHER.session_id,)
                )

    def test_input_during_close_audit_does_not_post(self) -> None:
        self.abandon()
        original = self.lifecycle._audit
        credited: list[str] = []

        def audit(event, client, reason, decision, **extra):
            if event == "lifecycle_close" and not credited:
                credited.extend(self.human_input(time.time()))
            return original(event, client, reason, decision, **extra)

        self.lifecycle._audit = audit
        self.phases.append(self.on_exit)
        result = self.drive()
        self.assertEqual(credited, ["run-x"])
        self.assertEqual(result, "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertIsNone(self.coordinator._active)
        stored = self.store.get("run-x")
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertIsNone(stored.owner_session_id)
        self.assertEqual(self.row(time.time())["use_state"], "human")

    def test_identity_change_inside_close_run_posts_nothing(self) -> None:
        for where in ("before_call", "during_audit"):
            with self.subTest(where=where):
                self.rebuild()
                self.abandon()
                if where == "before_call":
                    original_close = self.lifecycle.close_run

                    def close_after_pid_reuse(client, token, run_id):
                        self.make_foreign(801)
                        return original_close(client, token, run_id)

                    self.lifecycle.close_run = close_after_pid_reuse
                else:
                    original_audit = self.lifecycle._audit

                    def audit(event, client, reason, decision, **extra):
                        if event == "lifecycle_close":
                            self.make_foreign(801)
                        return original_audit(event, client, reason, decision, **extra)

                    self.lifecycle._audit = audit
                result = self.drive()
                self.assertEqual(result, "failed")
                self.assertEqual(self.windows.posted, [])
                self.assertEqual(self.guard.terminate_calls, [])
                stored = self.store.get("run-x")
                self.assertEqual(stored.state, "RUNNING_IDLE")
                self.assertEqual(len(stored.processes), 2)
                self.assertIsNone(stored.owner_session_id)
                self.assertIsNone(self.coordinator._active)

    def test_identity_change_inside_stop_run_kills_nothing(self) -> None:
        for where in ("before_call", "after_stopping"):
            with self.subTest(where=where):
                self.rebuild()
                self.abandon()
                if where == "before_call":
                    original_stop = self.lifecycle.stop_run

                    def stop_after_pid_reuse(client, token, run_id):
                        self.make_foreign(801)
                        return original_stop(client, token, run_id)

                    self.lifecycle.stop_run = stop_after_pid_reuse
                else:
                    original_replace = self.store.replace
                    flipped = {"done": False}

                    def replace(run):
                        result = original_replace(run)
                        if not flipped["done"] and getattr(run, "state", None) == "STOPPING":
                            flipped["done"] = True
                            self.make_foreign(801)
                        return result

                    self.store.replace = replace
                result = self.drive()
                if where == "after_stopping":
                    self.assertTrue(flipped["done"])
                self.assertEqual(result, "failed")
                self.assertEqual(self.guard.terminate_calls, [])
                stored = self.store.get("run-x")
                self.assertEqual(stored.state, "RUNNING_IDLE")
                self.assertIsNone(stored.owner_session_id)
                self.assertEqual(len(stored.processes), 2)
                self.assertIsNone(self.coordinator._active)

    def test_exit_does_not_leave_the_adoption_marker(self) -> None:
        # This cycle did not adopt. A pre-existing marker stays for startup recovery.
        self.abandon()
        warden = self.make_warden()
        marker = self.lifecycle._adoption_marker_path()
        self.assertIsNotNone(marker)
        planted = json.dumps(
            {
                "version": 1,
                "run_id": "run-x",
                "owner_session_id": warden.client.session_id,
                "owner_lease_id": "lease-leak",
                "token": 1,
                "invalidated": False,
                "invalidated_by": None,
            }
        )
        marker.write_text(planted, encoding="utf-8")
        self.assertEqual(warden.run_once(), "disabled")
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), planted)
        settled = self.lifecycle._adoption_settled_path()
        self.assertIsNotNone(settled)
        self.assertFalse(settled.exists())
        self.assertFalse(self.settings.exists())
        stored = self.store.get("run-x")
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(self.coordinator._active)
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.windows.posted, [])

    def test_release_failure_after_orderly_close_stays_pending(self) -> None:
        self.abandon()
        original = self.coordinator._arm_wal_locked

        def arm(*args, **kwargs):
            if kwargs.get("operation") == "release":
                return False
            return original(*args, **kwargs)

        def hook(name: str, _warden: IdleWarden) -> None:
            if name == "revalidate_close":
                self.coordinator._arm_wal_locked = arm
            self.on_exit(name, _warden)

        self.phases.append(hook)
        self.enable()
        warden = self.make_warden()
        try:
            result = warden.run_once()
        finally:
            self.coordinator._arm_wal_locked = original
        self.assertEqual(result, "release_pending")
        self.assertEqual(self.store.get("run-x").state, "EXITED")
        self.assertIsNotNone(self.coordinator._active)
        self.assertIsNotNone(warden.token)
        self.assertEqual(warden.run_once(), "released")
        self.assertIsNone(self.coordinator._active)
        self.assertIsNone(warden.token)

    def test_install_thread_stops(self) -> None:
        stop = threading.Event()
        stop.set()
        thread = install_idle_warden(
            self.lifecycle,
            self.coordinator,
            self.bridge,
            self.settings,
            generation=GENERATION,
            stop=stop,
            interval_s=30,
        )
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(thread.name, "dayz-mcp-idle-warden")
        self.assertTrue(thread.daemon)

    def test_install_retries_release_while_stop_is_set(self) -> None:
        self.abandon()
        self.enable()
        stop = threading.Event()
        original = self.coordinator._arm_wal_locked
        calls = {"release": 0}

        def arm(*args, **kwargs):
            if kwargs.get("operation") == "release":
                calls["release"] += 1
                if calls["release"] == 1:
                    stop.set()
                    return False
            return original(*args, **kwargs)

        self.coordinator._arm_wal_locked = arm
        thread = install_idle_warden(
            self.lifecycle,
            self.coordinator,
            self.bridge,
            self.settings,
            generation=GENERATION,
            stop=stop,
            interval_s=0.05,
            sample_input=self.quiet_sample,
            wall=lambda: self.wall_value,
            monotonic=lambda: self.mono,
            sleep=self.advance,
        )
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(calls["release"], 2)
        self.assertIsNone(self.coordinator._active)

    def _post_pid_hook(self, action) -> None:
        """Run ``action`` on the PID read inside the first ``post_wm_close``."""

        original = self.lifecycle.window_fns
        reads = {"n": 0}

        def window_pid(hwnd: int) -> int:
            reads["n"] += 1
            # Two processes, two windows listed, then the first post's PID read.
            if reads["n"] == 5:
                self.assertEqual(self.windows.posted, [])
                action()
            return original.window_pid(hwnd)

        self.lifecycle.window_fns = Win32WindowFns(
            original.enum_windows,
            window_pid,
            original.is_visible,
            original.post_message,
        )

    def test_switch_off_during_the_post_pid_read_posts_nothing(self) -> None:
        self.abandon()
        self._post_pid_hook(
            lambda: self.settings.write_text(
                '{"enabled": false}\n', encoding="utf-8"
            )
        )
        self.phases.append(self.on_exit)
        self.assertEqual(self.drive(), "disabled")
        self.assertEqual(self.windows.posted, [])
        self.assertNotEqual(self.store.get("run-x").state, "EXITED")
        self.assertIsNone(self.coordinator._active)

    def test_launcher_enqueue_during_the_post_pid_read_posts_nothing(self) -> None:
        self.abandon()
        self.lifecycle.remember_launcher("run-x", LAUNCHER)

        def enqueue() -> None:
            status, _body = self.coordinator.enqueue(
                LAUNCHER, "launch", "during-post-pid"
            )
            self.assertEqual(status, 202)

        self._post_pid_hook(enqueue)
        self.phases.append(self.on_exit)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.assertNotEqual(self.store.get("run-x").state, "EXITED")
        self.assertIsNone(self.coordinator._active)
        self.assertIn(
            LAUNCHER.session_id, self.coordinator.queued_session_ids()
        )

    def test_human_input_during_the_post_pid_read_posts_nothing(self) -> None:
        self.abandon()

        def credit() -> None:
            credited = self.human_input(time.time())
            self.assertEqual(credited, ["run-x"])

        self._post_pid_hook(credit)
        self.phases.append(self.on_exit)
        self.assertEqual(self.drive(), "countdown_aborted")
        self.assertEqual(self.windows.posted, [])
        self.assertNotEqual(self.store.get("run-x").state, "EXITED")
        self.assertIsNone(self.coordinator._active)

    def test_foreign_identity_during_the_post_pid_read_posts_nothing(self) -> None:
        self.abandon()
        self._post_pid_hook(lambda: self.make_foreign(801))
        self.phases.append(self.on_exit)
        self.assertEqual(self.drive(), "failed")
        self.assertEqual(self.windows.posted, [])
        self.assertEqual(self.guard.terminate_calls, [])
        stored = self.store.get("run-x")
        self.assertNotEqual(stored.state, "EXITED")
        self.assertEqual(len(stored.processes), 2)
        self.assertIsNone(self.coordinator._active)

    def test_identity_change_between_posts_is_partial_close(self) -> None:
        self.abandon()
        original = self.lifecycle.window_fns

        def post_message(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
            posted = original.post_message(hwnd, msg, wparam, lparam)
            if hwnd == 1802:
                self.make_foreign(801)
            return posted

        self.lifecycle.window_fns = Win32WindowFns(
            original.enum_windows,
            original.window_pid,
            original.is_visible,
            post_message,
        )
        result = self.drive()
        self.assertEqual(result, "partial_close")
        self.assertEqual(self.windows.posted, [(1802, 16)])
        self.assertEqual(self.guard.terminate_calls, [])
        detail = [
            event.get("detail")
            for event in self.audit.events
            if event.get("event") == "idle_timeout"
            and event.get("decision") == "partial_close"
        ]
        self.assertTrue(detail, self.audit.events)
        self.assertIn("acted_pids=[802]", detail[-1])
        self.assertIn("untouched_pids=[801]", detail[-1])
        self.assertIsNone(self.coordinator._active)
        stored = self.store.get("run-x")
        self.assertNotEqual(stored.state, "EXITED")
        self.assertIsNone(stored.owner_session_id)

    def test_identity_change_between_terminates_is_partial_stop(self) -> None:
        self.abandon()
        original = self.guard.terminate

        def terminate(record):
            if record.pid == 801:
                result = original(record)
                self.make_foreign(802)
                return result
            if record.pid == 802:
                return {"error": "process_identity_mismatch", "terminated": False}
            return original(record)

        self.guard.terminate = terminate
        result = self.drive()
        self.assertEqual(result, "partial_stop")
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls], [801]
        )
        detail = [
            event.get("detail")
            for event in self.audit.events
            if event.get("event") == "idle_timeout"
            and event.get("decision") == "partial_stop"
        ]
        self.assertTrue(detail, self.audit.events)
        self.assertIn("acted_pids=[801]", detail[-1])
        self.assertIn("untouched_pids=[802]", detail[-1])
        stored = self.store.get("run-x")
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertEqual([record.pid for record in stored.processes], [802])
        self.assertIsNone(stored.owner_session_id)
        self.assertIsNone(self.coordinator._active)

    def test_release_exception_keeps_the_lease(self) -> None:
        self.abandon()
        self.phases.append(self.on_exit)

        def unavailable(*_args, **_kwargs):
            raise OSError("injected release transport failure")

        self.coordinator.release = unavailable
        warden = self.make_warden()
        self.enable()
        result = warden.run_once()
        self.assertEqual(result, "release_pending")
        self.assertEqual(self.store.get("run-x").state, "EXITED")
        self.assertIsNotNone(self.coordinator._active)
        self.assertIsNotNone(warden.token)
        self.assertIsNotNone(warden.lease_id)

    def test_cancel_exception_keeps_the_ticket(self) -> None:
        self.abandon()
        status, _body = self.coordinator.acquire(IDENTITY_A, "hold")
        self.assertEqual(status, 200)

        def unavailable(*_args, **_kwargs):
            raise OSError("injected cancellation failure")

        def disable_at_queue(name: str, _warden: IdleWarden) -> None:
            if name == "queued":
                self.settings.write_text('{"enabled": false}\n', encoding="utf-8")

        self.coordinator.cancel_operation = unavailable
        self.phases.append(disable_at_queue)
        warden = self.make_warden()
        self.enable()
        result = warden.run_once()
        self.assertEqual(result, "release_pending")
        self.assertIsNotNone(warden.ticket_id)
        self.assertIn(
            warden.client.session_id, self.coordinator.queued_session_ids()
        )

    def test_idle_guard_is_bound_to_session_operation_and_run(self) -> None:
        self.abandon()
        warden = self.make_warden()
        client = warden.client
        with idle_destruction_guard(
            {
                "operation": "close",
                "session_id": client.session_id,
                "run_id": "run-x",
            }
        ):
            self.assertIsNotNone(
                self.lifecycle._warden_idle_guard(client, "close", "run-x")
            )
            self.assertIsNone(
                self.lifecycle._warden_idle_guard(client, "close", "run-y")
            )
            self.assertIsNone(
                self.lifecycle._warden_idle_guard(client, "stop", "run-x")
            )
        with idle_destruction_guard(
            {"operation": "close", "session_id": client.session_id}
        ):
            self.assertIsNone(
                self.lifecycle._warden_idle_guard(client, "close", "run-x")
            )

    def _defer_cleanup(self) -> threading.Event:
        gate = threading.Event()
        self.coordinator._cleanup_timeout_s = 0.01

        def cleanup(*_args: object) -> CleanupDisposition:
            return CleanupDisposition(
                True,
                gate,
                {"terminal_safe": True, "runs_released": ["run-x"]},
            )

        self.coordinator._cleanup = cleanup
        return gate

    def _arm_release_fails_once(self):
        original = self.coordinator._arm_wal_locked

        def arm(*args, **kwargs):
            if kwargs.get("operation") == "release":
                return False
            return original(*args, **kwargs)

        self.coordinator._arm_wal_locked = arm
        return original

    def test_release_202_drops_the_token_without_a_second_release(self) -> None:
        self.abandon()
        self.phases.append(self.on_exit)
        gate = self._defer_cleanup()
        calls = {"release": 0}
        real_release = self.coordinator.release

        def counting_release(client, token, reason="owner_release"):
            calls["release"] += 1
            return real_release(client, token, reason)

        self.coordinator.release = counting_release
        self.enable()
        warden = self.make_warden()
        try:
            self.assertEqual(warden.run_once(), "release_cleanup_pending")
            self.assertEqual(warden.run_once(), "release_cleanup_pending")
            self.assertIsNone(warden.token)
            self.assertIsNone(warden.lease_id)
            self.assertTrue(warden.cleanup_pending_lease_id)
            self.assertIsNone(self.coordinator._active)
            self.assertIsNotNone(self.coordinator._releasing)
            self.assertEqual(calls["release"], 1)
        finally:
            gate.set()

    def test_cleanup_pending_blocks_adoption_until_it_finishes(self) -> None:
        self.abandon()
        self.phases.append(self.on_exit)
        gate = self._defer_cleanup()
        self.enable()
        warden = self.make_warden()
        try:
            self.assertEqual(warden.run_once(), "release_cleanup_pending")
            self.windows.hwnd_of[803] = 1803
            self.windows.hwnd_of[804] = 1804
            self.add_run(
                "run-y",
                owner="B",
                processes=[
                    process(803, role="server"),
                    process(804, role="client"),
                ],
            )
            self.assertEqual(self.lifecycle.release_owner("B", "lease-A"), ["run-y"])
            stamp = time.time() - (RUN_IDLE_CUT_S + 1.0)
            self.lifecycle.restore_ownerless_since("run-y", stamp)
            self.wall_value = stamp + RUN_IDLE_CUT_S + 1.0
            self.bindings.bound.add("run-y")
            self.healthy_signal(float(stamp), self.wall_value)
            self.assertEqual(warden.run_once(), "release_cleanup_pending")
            stored = self.store.get("run-y")
            self.assertIsNone(stored.owner_session_id)
            self.assertIsNone(warden.ticket_id)
            gate.set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                snapshot_payload = self.coordinator.snapshot_payload()
                if (
                    snapshot_payload.get("releasing") is None
                    and snapshot_payload.get("handoff_pending") is False
                    and snapshot_payload.get("claimable") is True
                ):
                    break
                time.sleep(0.01)
            else:
                self.fail(
                    f"cleanup did not finish: {self.coordinator.snapshot_payload()}"
                )
            seen = {"owned": False}

            def watch(name: str, owner: IdleWarden) -> None:
                self.on_exit(name, owner)
                run = self.store.get("run-y")
                if (
                    run is not None
                    and run.owner_session_id == owner.client.session_id
                ):
                    seen["owned"] = True

            self.phases.append(watch)
            result = warden.run_once()
            self.assertIsNone(warden.cleanup_pending_lease_id)
            self.assertNotEqual(result, "release_cleanup_pending")
            self.assertNotEqual(result, "release_pending")
            self.assertTrue(seen["owned"], result)
        finally:
            gate.set()

    def test_stop_exits_while_cleanup_is_pending(self) -> None:
        self.abandon()
        gate = self._defer_cleanup()
        self.enable()
        stop = threading.Event()
        created: dict[str, IdleWarden] = {}
        original_init = IdleWarden.__init__

        def remember(warden: IdleWarden, *args, **kwargs) -> None:
            original_init(warden, *args, **kwargs)
            created["warden"] = warden

        IdleWarden.__init__ = remember
        try:
            def cleanup(*_args: object) -> CleanupDisposition:
                stop.set()
                return CleanupDisposition(
                    True,
                    gate,
                    {"terminal_safe": True, "runs_released": ["run-x"]},
                )

            self.coordinator._cleanup = cleanup
            thread = install_idle_warden(
                self.lifecycle,
                self.coordinator,
                self.bridge,
                self.settings,
                generation=GENERATION,
                stop=stop,
                interval_s=0.05,
                sample_input=self.quiet_sample,
                wall=lambda: self.wall_value,
                monotonic=lambda: self.mono,
                sleep=self.advance,
            )
            thread.join(5)
            self.assertFalse(thread.is_alive())
            warden = created["warden"]
            self.assertIsNone(warden.token)
            self.assertIsNone(warden.ticket_id)
            self.assertTrue(warden.cleanup_pending_lease_id)
            self.assertIsNotNone(self.coordinator._releasing)
            self.assertFalse(gate.is_set())
        finally:
            IdleWarden.__init__ = original_init
            gate.set()

    def test_lease_invalid_on_release_drops_the_token(self) -> None:
        self.abandon()
        self.phases.append(self.on_exit)
        original = self._arm_release_fails_once()
        self.enable()
        warden = self.make_warden()
        self.assertEqual(warden.run_once(), "release_pending")
        self.assertIsNotNone(warden.token)
        self.coordinator._arm_wal_locked = original
        status, body = self.coordinator.release(
            warden.client, warden.token, "owner_release"
        )
        self.assertEqual(status, 200, body)
        calls = {"release": 0}
        real_release = self.coordinator.release

        def counting_release(client, token, reason="owner_release"):
            calls["release"] += 1
            return real_release(client, token, reason)

        self.coordinator.release = counting_release
        self.assertEqual(warden.run_once(), "release_lost")
        self.assertIsNone(warden.token)
        self.assertIsNone(warden.lease_id)
        self.assertEqual(calls["release"], 1)
        self.assertEqual(body.get("released"), True)
        self.assertEqual(warden.run_once(), "no_candidate")
        self.assertEqual(calls["release"], 1)
        self.assertIsNone(self.coordinator._active)

    def test_lease_expired_on_release_drops_the_token(self) -> None:
        self.abandon()
        self.phases.append(self.on_exit)
        original = self._arm_release_fails_once()
        self.enable()
        warden = self.make_warden()
        self.assertEqual(warden.run_once(), "release_pending")
        self.coordinator._arm_wal_locked = original
        started = self.coordinator._time_fn()
        self.coordinator._time_fn = lambda: started + SESSION_TTL_S + 5.0
        calls = {"release": 0}
        real_release = self.coordinator.release

        def counting_release(client, token, reason="owner_release"):
            calls["release"] += 1
            status, body = real_release(client, token, reason)
            self.assertEqual(status, 409, body)
            self.assertEqual(body.get("error"), "lease_expired")
            return status, body

        self.coordinator.release = counting_release
        self.assertEqual(warden.run_once(), "release_lost")
        self.assertIsNone(warden.token)
        self.assertIsNone(warden.lease_id)
        self.assertEqual(calls["release"], 1)
        self.assertIsNone(self.coordinator._active)
        self.assertEqual(warden.run_once(), "no_candidate")
        self.assertEqual(calls["release"], 1)

    def test_release_exception_retries_while_the_token_is_held(self) -> None:
        self.abandon()
        self.phases.append(self.on_exit)
        real_release = self.coordinator.release
        calls = {"release": 0}

        def flaky(client, token, reason="owner_release"):
            calls["release"] += 1
            if calls["release"] == 1:
                raise OSError("injected release transport failure")
            return real_release(client, token, reason)

        self.coordinator.release = flaky
        self.enable()
        warden = self.make_warden()
        self.assertEqual(warden.run_once(), "release_pending")
        self.assertIsNotNone(warden.token)
        self.assertIsNotNone(self.coordinator._active)
        self.assertEqual(warden.run_once(), "released")
        self.assertIsNone(warden.token)
        self.assertIsNone(self.coordinator._active)
        self.assertEqual(calls["release"], 2)


class BoundInstanceTokenTest(unittest.TestCase):
    def test_bound_server_pin_and_missing_peer(self) -> None:
        state = ServerState("bound-token-test")
        instance = str(uuid.uuid4())
        state.install_bound_peer(
            instance=instance, role="server", pid=9, run_id="run-x"
        )
        token = state.bound_instance_token("run-x", "server")
        self.assertEqual(token, f"{instance}|1|9|2026-08-18T00:00:00.000000Z")
        self.assertIsNone(state.bound_instance_token("missing", "server"))
        self.assertIsNone(state.bound_instance_token("run-x", "client"))
