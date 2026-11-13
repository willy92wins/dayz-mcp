"""Lease work-idle clock and daemon idle_hold_probe (fb-20261112-103851-0dac)."""

from __future__ import annotations

import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon
from dayz_mcp.input_activity import InputSample
from dayz_mcp.loopback import ServerState
from dayz_mcp.process_lifecycle import (
    INPUT_SIGNAL_STALE_S,
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import (
    LEASE_WORK_IDLE_S,
    SESSION_TTL_S,
    SessionCoordinator,
)
from tests.lease_helpers import (
    AuditSink,
    CleanupSink,
    FakeClock,
    SequentialIds,
    _identity,
)
from tests.process_lifecycle_helpers import (
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate


GENERATION = "gen-work-idle"


class FakeIdleHoldProbe:
    def __init__(
        self,
        result: bool = True,
        *,
        side_effect=None,
    ) -> None:
        self.result = result
        self.side_effect = side_effect
        self.calls: list[tuple[str, str, float]] = []

    def __call__(self, session_id: str, lease_id: str, window_s: float) -> bool:
        self.calls.append((session_id, lease_id, window_s))
        if self.side_effect is not None:
            return bool(self.side_effect(session_id, lease_id, window_s))
        return self.result


def _make_coordinator(
    clock: FakeClock,
    *,
    audit: AuditSink | None = None,
    cleanup: CleanupSink | None = None,
    attached: bool = True,
    idle_hold_probe=None,
) -> tuple[SessionCoordinator, AuditSink, CleanupSink]:
    audit_sink = audit if audit is not None else AuditSink()
    cleanup_sink = cleanup if cleanup is not None else CleanupSink()
    coordinator = SessionCoordinator(
        time_fn=clock,
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("lease"),
        audit=audit_sink,
        cleanup=cleanup_sink,
        attached_run_probe=lambda _session, _lease: attached,
        idle_hold_probe=idle_hold_probe,
    )
    return coordinator, audit_sink, cleanup_sink


class LeaseWorkIdleCoordinatorTest(unittest.TestCase):
    def test_heartbeat_only_refuses_renewal_after_work_idle_and_expires(self) -> None:
        clock = FakeClock()
        probe = FakeIdleHoldProbe(result=True)
        coordinator, audit, cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        status, grant = coordinator.acquire(owner, "hold-box")
        self.assertEqual(status, 200)
        token = grant["lease_token"]
        lease_id = grant["lease_id"]

        for _ in range(44):
            clock.advance(40.0)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200)
            self.assertNotIn("renewal_refused", hb_body)
            self.assertNotIn("work_idle_s", hb_body)
            self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)
        self.assertEqual(clock.value, 1760.0)
        self.assertEqual(probe.calls, [])
        expires_before_refusal = coordinator._active.expires_at
        self.assertEqual(expires_before_refusal, 1760.0 + SESSION_TTL_S)

        clock.advance(40.0)
        self.assertEqual(clock.value, LEASE_WORK_IDLE_S)
        refused_status, refused_body = coordinator.heartbeat(owner, token)
        self.assertEqual(refused_status, 200)
        self.assertEqual(
            probe.calls, [(owner.session_id, lease_id, LEASE_WORK_IDLE_S)]
        )
        self.assertEqual(coordinator._active.expires_at, expires_before_refusal)
        self.assertEqual(refused_body["expires_in_s"], 80.0)
        self.assertEqual(refused_body["renewal_refused"], "work_idle")
        self.assertGreaterEqual(refused_body["work_idle_s"], LEASE_WORK_IDLE_S)
        self.assertEqual(refused_body["work_idle_s"], 1800.0)

        heartbeats = [
            event
            for event in audit.events
            if event.get("event") == "session_heartbeat"
        ]
        self.assertEqual(heartbeats[-1]["decision"], "work_idle")
        self.assertEqual(heartbeats[-1]["lease_id"], lease_id)

        clock.advance(SESSION_TTL_S)
        expired_status, expired_body = coordinator.heartbeat(owner, token)
        self.assertEqual(expired_status, 409)
        self.assertEqual(expired_body["error"], "lease_expired")
        self.assertEqual(
            cleanup.calls, [(owner.session_id, "lease_expired", False)]
        )
        self.assertIsNotNone(coordinator.status(owner)["grace"])

    def test_mutating_command_every_twenty_minutes_keeps_heartbeats_renewing_for_two_hours(
        self,
    ) -> None:
        clock = FakeClock()
        probe = FakeIdleHoldProbe(result=True)
        coordinator, _audit, cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        status, grant = coordinator.acquire(owner, "work")
        self.assertEqual(status, 200)
        token = grant["lease_token"]
        lease_id = grant["lease_id"]

        for step in range(1, 181):
            clock.advance(40.0)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200, f"step={step} t={clock.value}")
            self.assertNotIn("renewal_refused", hb_body)
            self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)
            if step % 30 == 0:
                decision = coordinator.authorize(owner, token, "world_spawn")
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.lease_id, lease_id)
                committed = coordinator.commit_authorization(
                    owner.session_id,
                    lease_id,
                    decision.reservation_id,
                    step,
                    "world_spawn",
                    {},
                )
                self.assertTrue(committed)
                coordinator.finish_operation(owner.session_id, step)

        self.assertEqual(clock.value, 7200.0)
        self.assertEqual(probe.calls, [])
        self.assertEqual(cleanup.calls, [])

    def test_owner_read_with_token_every_twenty_minutes_keeps_heartbeats_renewing_for_two_hours(
        self,
    ) -> None:
        clock = FakeClock()
        probe = FakeIdleHoldProbe(result=True)
        coordinator, _audit, cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        status, grant = coordinator.acquire(owner, "work")
        self.assertEqual(status, 200)
        token = grant["lease_token"]
        lease_id = grant["lease_id"]

        for step in range(1, 181):
            clock.advance(20.0)
            if step % 30 == 0:
                before_expiry = coordinator._active.expires_at
                decision = coordinator.authorize(owner, token, "camera_get")
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.lease_id, lease_id)
                self.assertEqual(coordinator._active.expires_at, before_expiry)
            clock.advance(20.0)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200, f"step={step} t={clock.value}")
            self.assertNotIn("renewal_refused", hb_body)
            self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)

        self.assertEqual(clock.value, 7200.0)
        self.assertEqual(probe.calls, [])
        self.assertEqual(cleanup.calls, [])

    def test_non_work_calls_and_unleased_reads_do_not_reset_work_clock(self) -> None:
        clock = FakeClock()
        probe = FakeIdleHoldProbe(result=True)
        coordinator, _audit, _cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        other = _identity("b")
        status, grant = coordinator.acquire(owner, "work")
        self.assertEqual(status, 200)
        token = grant["lease_token"]
        lease_id = grant["lease_id"]
        decision = coordinator.authorize(owner, token, "world_spawn")
        self.assertTrue(decision.allowed)

        for step in range(1, 45):
            clock.advance(40.0)
            self.assertEqual(coordinator.active_lease_id(owner, token), lease_id)
            self.assertEqual(coordinator.status(owner)["self"]["lease_id"], lease_id)
            self.assertTrue(coordinator.authorize(owner, None, "camera_get").allowed)
            self.assertTrue(coordinator.authorize(other, None, "camera_get").allowed)
            if step == 10:
                committed = coordinator.commit_authorization(
                    owner.session_id,
                    lease_id,
                    decision.reservation_id,
                    1,
                    "world_spawn",
                    {},
                )
                self.assertTrue(committed)
                coordinator.finish_operation(owner.session_id, 1)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200)
            self.assertNotIn("renewal_refused", hb_body)

        clock.advance(40.0)
        refused_status, refused_body = coordinator.heartbeat(owner, token)
        self.assertEqual(refused_status, 200)
        self.assertEqual(refused_body.get("renewal_refused"), "work_idle")

    def test_probe_returning_false_keeps_heartbeats_renewing_for_two_hours(
        self,
    ) -> None:
        clock = FakeClock()
        probe = FakeIdleHoldProbe(result=False)
        coordinator, audit, cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        status, grant = coordinator.acquire(owner, "work")
        self.assertEqual(status, 200)
        token = grant["lease_token"]

        for _ in range(180):
            clock.advance(40.0)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200)
            self.assertNotIn("renewal_refused", hb_body)
            self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)

        self.assertEqual(clock.value, 7200.0)
        self.assertEqual(len(probe.calls), 136)
        self.assertEqual(cleanup.calls, [])
        decisions = {
            event["decision"]
            for event in audit.events
            if event.get("event") == "session_heartbeat"
        }
        self.assertEqual(decisions, {"renewed"})

    def test_without_idle_hold_probe_behaves_as_today(self) -> None:
        clock = FakeClock()
        coordinator, _audit, cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=None
        )
        owner = _identity("a")
        status, grant = coordinator.acquire(owner, "work")
        self.assertEqual(status, 200)
        token = grant["lease_token"]

        for _ in range(180):
            clock.advance(40.0)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200)
            self.assertNotIn("renewal_refused", hb_body)
            self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)

        self.assertEqual(cleanup.calls, [])

    def test_probe_is_not_called_before_work_idle_threshold(self) -> None:
        clock = FakeClock()
        probe = FakeIdleHoldProbe(result=True)
        coordinator, _audit, _cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        token = coordinator.acquire(owner, "work")[1]["lease_token"]

        for _ in range(44):
            clock.advance(40.0)
            self.assertEqual(coordinator.heartbeat(owner, token)[0], 200)
        clock.advance(39.9)
        self.assertAlmostEqual(clock.value, LEASE_WORK_IDLE_S - 0.1)
        hb_status, hb_body = coordinator.heartbeat(owner, token)
        self.assertEqual(hb_status, 200)
        self.assertNotIn("renewal_refused", hb_body)
        self.assertEqual(probe.calls, [])

        clock.advance(0.1)
        refused_status, refused_body = coordinator.heartbeat(owner, token)
        self.assertEqual(refused_status, 200)
        self.assertEqual(len(probe.calls), 1)
        self.assertEqual(refused_body.get("renewal_refused"), "work_idle")

    def test_authorize_arriving_while_probe_runs_leaves_heartbeat_renewed(
        self,
    ) -> None:
        for command in ("world_spawn", "camera_get"):
            with self.subTest(command=command):
                clock = FakeClock()
                owner = _identity("a")
                holder: dict[str, SessionCoordinator] = {}
                token_box: dict[str, str] = {}
                authorized: list[bool] = []

                def concurrent_authorize(
                    _session_id: str, _lease_id: str, _window_s: float
                ) -> bool:
                    def run_authorize() -> None:
                        decision = holder["coordinator"].authorize(
                            owner, token_box["token"], command
                        )
                        authorized.append(decision.allowed)

                    worker = threading.Thread(target=run_authorize)
                    worker.start()
                    worker.join(timeout=2.0)
                    self.assertFalse(worker.is_alive())
                    return True

                probe = FakeIdleHoldProbe(side_effect=concurrent_authorize)
                coordinator, audit, _cleanup = _make_coordinator(
                    clock, attached=True, idle_hold_probe=probe
                )
                holder["coordinator"] = coordinator
                token = coordinator.acquire(owner, "work")[1]["lease_token"]
                token_box["token"] = token

                for _ in range(44):
                    clock.advance(40.0)
                    self.assertEqual(coordinator.heartbeat(owner, token)[0], 200)
                clock.advance(40.0)
                hb_status, hb_body = coordinator.heartbeat(owner, token)
                self.assertEqual(authorized, [True])
                self.assertEqual(len(probe.calls), 1)
                self.assertEqual(hb_status, 200)
                self.assertNotIn("renewal_refused", hb_body)
                self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)
                self.assertEqual(
                    coordinator._active.expires_at, clock.value + SESSION_TTL_S
                )
                heartbeats = [
                    event
                    for event in audit.events
                    if event.get("event") == "session_heartbeat"
                ]
                self.assertEqual(heartbeats[-1]["decision"], "renewed")

    def test_authorize_during_refusal_audit_window_leaves_heartbeat_renewed(
        self,
    ) -> None:
        clock = FakeClock()
        probe = FakeIdleHoldProbe(result=True)
        coordinator, audit, _cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        status, grant = coordinator.acquire(owner, "work")
        self.assertEqual(status, 200)
        token = grant["lease_token"]
        lease_id = grant["lease_id"]
        authorized: list[bool] = []
        triggered: list[bool] = []
        original_write_audit = coordinator._write_audit_locked

        def hooked_write_audit(event_name, **kwargs):
            ok = original_write_audit(event_name, **kwargs)
            if (
                ok
                and not triggered
                and event_name == "session_heartbeat"
                and kwargs.get("decision") == "work_idle"
            ):
                triggered.append(True)
                # The refusal audit write has retaken the coordinator
                # lock. Drop it so the read below lands in the window
                # between that write and the heartbeat's own recheck,
                # then take it back for the heartbeat to continue.
                coordinator._condition.release()
                try:
                    decision = coordinator.authorize(owner, token, "camera_get")
                    authorized.append(decision.allowed)
                finally:
                    coordinator._condition.acquire()
            return ok

        coordinator._write_audit_locked = hooked_write_audit
        try:
            for _ in range(16):
                clock.advance(110.0)
                hb_status, hb_body = coordinator.heartbeat(owner, token)
                self.assertEqual(hb_status, 200)
                self.assertNotIn("renewal_refused", hb_body)
                self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)
            self.assertEqual(clock.value, 1760.0)
            self.assertEqual(probe.calls, [])

            clock.advance(110.0)
            self.assertEqual(clock.value, 1870.0)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200)
            self.assertEqual(triggered, [True])
            self.assertEqual(authorized, [True])
            self.assertEqual(
                probe.calls, [(owner.session_id, lease_id, LEASE_WORK_IDLE_S)]
            )
            self.assertNotIn("renewal_refused", hb_body)
            self.assertNotIn("work_idle_s", hb_body)
            self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)
            self.assertEqual(
                coordinator._active.expires_at, clock.value + SESSION_TTL_S
            )
            heartbeats = [
                event
                for event in audit.events
                if event.get("event") == "session_heartbeat"
            ]
            self.assertEqual(heartbeats[-1]["decision"], "renewed")

            clock.advance(110.0)
            self.assertEqual(clock.value, 1980.0)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200)
            self.assertNotEqual(hb_body.get("error"), "lease_expired")
            self.assertNotIn("renewal_refused", hb_body)
            self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)
            self.assertEqual(
                coordinator._active.expires_at, clock.value + SESSION_TTL_S
            )
        finally:
            coordinator._write_audit_locked = original_write_audit

    def test_probe_runs_without_coordinator_lock_held(self) -> None:
        clock = FakeClock()
        owner = _identity("a")
        holder: dict[str, SessionCoordinator] = {}
        observations: list[tuple[bool, bool]] = []

        def check_lock(_session_id: str, _lease_id: str, _window_s: float) -> bool:
            condition = holder["coordinator"]._condition
            owned_on_probe_thread = condition._is_owned()  # type: ignore[attr-defined]
            other_acquired: list[bool] = []

            def try_from_other_thread() -> None:
                got = condition.acquire(blocking=False)
                other_acquired.append(got)
                if got:
                    condition.release()

            worker = threading.Thread(target=try_from_other_thread)
            worker.start()
            worker.join(timeout=2.0)
            self.assertFalse(worker.is_alive())
            observations.append((owned_on_probe_thread, other_acquired == [True]))
            return True

        probe = FakeIdleHoldProbe(side_effect=check_lock)
        coordinator, _audit, _cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        holder["coordinator"] = coordinator
        token = coordinator.acquire(owner, "work")[1]["lease_token"]

        for _ in range(44):
            clock.advance(40.0)
            self.assertEqual(coordinator.heartbeat(owner, token)[0], 200)
        clock.advance(40.0)
        hb_status, hb_body = coordinator.heartbeat(owner, token)
        self.assertEqual(hb_status, 200)
        self.assertEqual(hb_body.get("renewal_refused"), "work_idle")
        self.assertEqual(observations, [(False, True)])

    def test_probe_raising_leaves_heartbeat_renewed(self) -> None:
        clock = FakeClock()

        def raising_probe(_session_id: str, _lease_id: str, _window_s: float) -> bool:
            raise RuntimeError("probe failed")

        probe = FakeIdleHoldProbe(side_effect=raising_probe)
        coordinator, audit, _cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        token = coordinator.acquire(owner, "work")[1]["lease_token"]

        for _ in range(44):
            clock.advance(40.0)
            self.assertEqual(coordinator.heartbeat(owner, token)[0], 200)
        clock.advance(40.0)
        hb_status, hb_body = coordinator.heartbeat(owner, token)
        self.assertEqual(len(probe.calls), 1)
        self.assertEqual(hb_status, 200)
        self.assertNotIn("renewal_refused", hb_body)
        self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)
        self.assertEqual(
            coordinator._active.expires_at, clock.value + SESSION_TTL_S
        )
        heartbeats = [
            event
            for event in audit.events
            if event.get("event") == "session_heartbeat"
        ]
        self.assertEqual(heartbeats[-1]["decision"], "renewed")


class DaemonIdleHoldProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.state = ServerState("probe-test-key")
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("lease"),
            audit=AuditSink(),
            cleanup=CleanupSink(),
        )
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=lambda _event: True,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=FakeLauncher(),
            id_fn=lambda: "run-1",
            bindings=self.state,
            daemon_generation=GENERATION,
        )

    def _add_run(
        self,
        run_id: str = "run-1",
        *,
        owner_session_id: str | None = "session-a",
        owner_lease_id: str | None = "lease-1",
        state: str = "RUNNING",
        client_pid: int = 802,
        verified_guard: bool = True,
    ) -> RunRecord:
        client_proc = process(client_pid, role="client")
        if verified_guard:
            self.guard.snapshots[client_pid] = snapshot(client_proc)
        else:
            self.guard.snapshots.pop(client_pid, None)
        record = RunRecord(
            run_id=run_id,
            owner_session_id=owner_session_id,
            owner_lease_id=owner_lease_id,
            state=state,
            label="test",
            mod="@DayZ_MCP",
            profiles=str(self.root / "profiles"),
            mission=str(self.root / "mission"),
            processes=[client_proc],
            daemon_generation_at_launch=GENERATION,
        )
        self.store.add(record)
        return record

    def _bind(self, run_id: str = "run-1", pid: int = 801) -> None:
        self.state.install_bound_peer(
            instance=str(uuid.uuid4()),
            role="server",
            pid=pid,
            run_id=run_id,
        )

    def _credit_input(self, at: float, *, pid: int = 802) -> None:
        self.lifecycle.record_input_sample(
            InputSample(
                at=at - 0.25,
                ok=True,
                last_input_tick=1000,
                idle_ms=0,
                foreground_pid=pid,
            )
        )
        self.lifecycle.record_input_sample(
            InputSample(
                at=at,
                ok=True,
                last_input_tick=1100,
                idle_ms=0,
                foreground_pid=pid,
            )
        )

    def _feed_good_signal(
        self, since: float, *, until: float | None = None, tick: int = 1000
    ) -> None:
        """Good samples at most INPUT_SIGNAL_STALE_S apart from since to until
        (now by default). The tick never moves, so nobody is typing."""

        at = since
        while True:
            end = time.time() if until is None else until
            at = min(at, end)
            self.lifecycle.record_input_sample(
                InputSample(
                    at=at,
                    ok=True,
                    last_input_tick=tick,
                    idle_ms=0,
                    foreground_pid=None,
                )
            )
            if at >= end:
                return
            at += INPUT_SIGNAL_STALE_S

    def _signal_good(self):
        """Isolate a probe condition: the signal reads good for the whole window."""

        return mock.patch.object(
            self.lifecycle,
            "input_signal_good_since",
            return_value=time.time() - (LEASE_WORK_IDLE_S + 10.0),
        )

    def _hold_signal_good(self) -> None:
        patcher = self._signal_good()
        patcher.start()
        self.addCleanup(patcher.stop)

    def _probe(self) -> bool:
        return daemon.idle_hold_probe(
            self.state, self.lifecycle, "session-a", "lease-1", LEASE_WORK_IDLE_S
        )

    def test_true_with_owned_running_bound_run_and_no_recent_input(self) -> None:
        self._add_run("run-1", owner_session_id="session-a", owner_lease_id="lease-1")
        self._bind("run-1")
        self._feed_good_signal(time.time() - (LEASE_WORK_IDLE_S + 10.0))
        self.assertIsNone(self.lifecycle.human_input_at("run-1"))
        self.assertIsNone(self.lifecycle.uncertain_input_at("run-1"))
        self.assertTrue(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

    def test_true_with_only_old_input_and_a_good_signal(self) -> None:
        self._add_run("run-1", owner_session_id="session-a", owner_lease_id="lease-1")
        self._bind("run-1")
        old = time.time() - (LEASE_WORK_IDLE_S + 10.0)
        self._credit_input(old)
        self.assertEqual(self.lifecycle.human_input_at("run-1"), old)
        # The credit's last tick is 1100: the signal stays good, with no new input.
        self._feed_good_signal(old, tick=1100)
        self.assertEqual(self.lifecycle.human_input_at("run-1"), old)
        self.assertTrue(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

    def test_false_without_owned_running_run_or_with_other_lease(self) -> None:
        self._hold_signal_good()
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

        self._add_run(
            "run-other-lease",
            owner_session_id="session-a",
            owner_lease_id="lease-2",
        )
        self._bind("run-other-lease")
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

        self._add_run(
            "run-other-session",
            owner_session_id="session-b",
            owner_lease_id="lease-1",
            client_pid=803,
        )
        self._bind("run-other-session", pid=804)
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

        self._add_run(
            "run-idle",
            owner_session_id=None,
            owner_lease_id=None,
            state="RUNNING_IDLE",
            client_pid=805,
        )
        self._bind("run-idle", pid=806)
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

    def test_false_when_owned_run_is_not_bound(self) -> None:
        self._hold_signal_good()
        self._add_run("run-1", owner_session_id="session-a", owner_lease_id="lease-1")
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

        self._bind("run-1")
        self._add_run(
            "run-2",
            owner_session_id="session-a",
            owner_lease_id="lease-1",
            client_pid=804,
        )
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )
        self._bind("run-2", pid=805)
        self.assertTrue(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

    def test_false_with_human_input_inside_window(self) -> None:
        self._hold_signal_good()
        self._add_run(
            "run-1",
            owner_session_id="session-a",
            owner_lease_id="lease-1",
            verified_guard=True,
        )
        self._bind("run-1")
        recent = time.time() - 15.0
        self._credit_input(recent)
        self.assertEqual(self.lifecycle.human_input_at("run-1"), recent)
        self.assertIsNone(self.lifecycle.uncertain_input_at("run-1"))
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

    def test_false_with_uncertain_input_inside_window(self) -> None:
        self._hold_signal_good()
        self._add_run(
            "run-1",
            owner_session_id="session-a",
            owner_lease_id="lease-1",
            verified_guard=False,
        )
        self._bind("run-1")
        recent = time.time() - 15.0
        self._credit_input(recent)
        self.assertIsNone(self.lifecycle.human_input_at("run-1"))
        self.assertEqual(self.lifecycle.uncertain_input_at("run-1"), recent)
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                self.lifecycle,
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

    def test_false_on_missing_data_or_exception(self) -> None:
        self._add_run("run-1", owner_session_id="session-a", owner_lease_id="lease-1")
        self._bind("run-1")

        self.assertFalse(
            daemon.idle_hold_probe(
                None, self.lifecycle, "session-a", "lease-1", LEASE_WORK_IDLE_S
            )
        )
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state, None, "session-a", "lease-1", LEASE_WORK_IDLE_S
            )
        )
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                SimpleNamespace(manifest=None),
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )
        self.assertFalse(
            daemon.idle_hold_probe(
                self.state,
                SimpleNamespace(
                    manifest=self.store,
                    human_input_at=self.lifecycle.human_input_at,
                ),
                "session-a",
                "lease-1",
                LEASE_WORK_IDLE_S,
            )
        )

        def boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        def good(_now: float) -> float:
            return time.time() - (LEASE_WORK_IDLE_S + 10.0)

        def lifecycle_with(**overrides):
            fields = {
                "manifest": self.store,
                "human_input_at": self.lifecycle.human_input_at,
                "uncertain_input_at": self.lifecycle.uncertain_input_at,
                "input_signal_good_since": good,
            }
            fields.update(overrides)
            return SimpleNamespace(**fields)

        def probe(lifecycle, state=self.state) -> bool:
            return daemon.idle_hold_probe(
                state, lifecycle, "session-a", "lease-1", LEASE_WORK_IDLE_S
            )

        # The same shape with every collaborator working holds the box, so
        # each False below comes from the one piece that is missing or raises.
        self.assertTrue(probe(lifecycle_with()))
        self.assertFalse(
            probe(lifecycle_with(), state=SimpleNamespace(run_has_bound_binding=boom))
        )
        self.assertFalse(probe(lifecycle_with(manifest=SimpleNamespace(list_runs=boom))))
        self.assertFalse(probe(lifecycle_with(human_input_at=boom)))
        self.assertFalse(probe(lifecycle_with(uncertain_input_at=boom)))
        self.assertFalse(probe(lifecycle_with(input_signal_good_since=boom)))
        fields = vars(lifecycle_with())
        del fields["input_signal_good_since"]
        self.assertFalse(probe(SimpleNamespace(**fields)))
        for value in (None, True, float("nan"), "0", time.time() + 60.0):
            self.assertFalse(
                probe(lifecycle_with(input_signal_good_since=lambda _now, v=value: v)),
                value,
            )

    def test_false_without_input_sampling(self) -> None:
        self._add_run("run-1", owner_session_id="session-a", owner_lease_id="lease-1")
        self._bind("run-1")
        self.assertIsNone(self.lifecycle.input_signal_good_since(time.time()))
        self.assertFalse(self._probe())

    def test_false_when_the_signal_came_back_inside_the_window(self) -> None:
        self._add_run("run-1", owner_session_id="session-a", owner_lease_id="lease-1")
        self._bind("run-1")
        since = time.time() - (LEASE_WORK_IDLE_S - 60.0)
        self._feed_good_signal(since)
        self.assertEqual(self.lifecycle.input_signal_good_since(time.time()), since)
        self.assertFalse(self._probe())

    def test_false_when_the_signal_is_stale_now(self) -> None:
        self._add_run("run-1", owner_session_id="session-a", owner_lease_id="lease-1")
        self._bind("run-1")
        now = time.time()
        self._feed_good_signal(now - (LEASE_WORK_IDLE_S + 10.0), until=now - 10.0)
        self.assertIsNone(self.lifecycle.input_signal_good_since(time.time()))
        self.assertFalse(self._probe())

    def test_input_signal_good_since_reads_the_recovery_instant(self) -> None:
        # Explicit clocks: the accessor takes now from its caller.
        self.assertIsNone(self.lifecycle.input_signal_good_since(1000.0))
        for at in (1000.0, 1001.0):
            self.lifecycle.record_input_sample(InputSample(at=at, ok=False))
        self.assertIsNone(self.lifecycle.input_signal_good_since(1001.0))
        for at in (1002.0, 1004.0, 1006.0):
            self.lifecycle.record_input_sample(
                InputSample(
                    at=at, ok=True, last_input_tick=1000, idle_ms=0, foreground_pid=None
                )
            )
        self.assertEqual(self.lifecycle.input_signal_good_since(1006.0), 1002.0)
        self.assertEqual(
            self.lifecycle.input_signal_good_since(1006.0 + INPUT_SIGNAL_STALE_S), 1002.0
        )
        self.assertIsNone(self.lifecycle.input_signal_good_since(1008.5))
        # A gap longer than INPUT_SIGNAL_STALE_S restarts it at the sample after.
        self.lifecycle.record_input_sample(
            InputSample(
                at=1012.0, ok=True, last_input_tick=1000, idle_ms=0, foreground_pid=None
            )
        )
        self.assertEqual(self.lifecycle.input_signal_good_since(1012.5), 1012.0)

    def test_failed_input_samples_keep_heartbeats_renewing_past_the_window(self) -> None:
        clock = FakeClock()
        answers: list[bool] = []

        def probe(session_id: str, lease_id: str, window_s: float) -> bool:
            answer = daemon.idle_hold_probe(
                self.state, self.lifecycle, session_id, lease_id, window_s
            )
            answers.append(answer)
            return answer

        coordinator, _audit, cleanup = _make_coordinator(
            clock, attached=True, idle_hold_probe=probe
        )
        owner = _identity("a")
        status, grant = coordinator.acquire(owner, "hold-box")
        self.assertEqual(status, 200)
        token = grant["lease_token"]
        self._add_run(
            "run-1",
            owner_session_id=owner.session_id,
            owner_lease_id=grant["lease_id"],
        )
        self._bind("run-1")
        for _ in range(3):
            self.lifecycle.record_input_sample(InputSample(at=time.time(), ok=False))
        self.assertIsNone(self.lifecycle.input_signal_good_since(time.time()))
        self.assertIsNone(self.lifecycle.human_input_at("run-1"))

        for step in range(1, 181):
            clock.advance(40.0)
            hb_status, hb_body = coordinator.heartbeat(owner, token)
            self.assertEqual(hb_status, 200, f"step={step} t={clock.value}")
            self.assertNotIn("renewal_refused", hb_body)
            self.assertEqual(hb_body["expires_in_s"], SESSION_TTL_S)

        self.assertEqual(clock.value, 7200.0)
        # Every beat from 1800 s on asked the probe, and the probe never held.
        self.assertEqual(len(answers), 136)
        self.assertNotIn(True, answers)
        self.assertEqual(cleanup.calls, [])


if __name__ == "__main__":
    unittest.main()
