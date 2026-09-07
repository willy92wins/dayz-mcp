from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon, loopback
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from _session_coordination.process_guard_gate import validate_result_shape
from tests.fence_helpers import accredited_poll, bind_both_peers


IDENTITY = ClientIdentity(
    "codex", 11, 1, "2026-07-15T00:00:00Z", "same-session", "task7-red"
)
IDENTITY_PAYLOAD = IDENTITY.to_payload()
HASH_A = "a" * 64
HASH_B = "b" * 64


class ProcessGuardGateContractTest(unittest.TestCase):
    def test_durable_gate_result_shape_is_regression_checked_without_running_processes(self) -> None:
        missing = {
            "terminated": False,
            "error": "invalid_expected_identity",
            "exit_code": 3,
            "pid": None,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": False,
        }
        mismatch = {
            "terminated": False,
            "error": "process_identity_mismatch",
            "exit_code": 4,
            "pid": 102,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }
        payload = {
            "schema_version": 1,
            "gate": "task7_process_guard_registered_vs_foreign",
            "passed": True,
            "steps": {
                "registered_snapshot": {
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "foreign_snapshot": {
                    "pid": 102,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "missing_field_rejections": {
                    field: dict(missing)
                    for field in (
                        "pid",
                        "creation_time_utc",
                        "executable_sha256",
                        "command_line_sha256",
                    )
                },
                "forged_identity_rejections": {
                    field: dict(mismatch)
                    for field in (
                        "pid",
                        "creation_time_utc",
                        "executable_sha256",
                        "command_line_sha256",
                    )
                },
                "registered_termination": {
                    "terminated": True,
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
                "terminated_identity_recheck": {
                    "terminated": False,
                    "error": "process_not_found",
                    "pid": 101,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": False,
                    "exit_code": 4,
                },
                "foreign_alive_after_rejections": True,
                "foreign_exact_cleanup": {
                    "terminated": True,
                    "pid": 102,
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                    "exit_code": 0,
                },
            },
            "children": [
                {"slot": "registered", "pid": 101, "final_state": "exited"},
                {"slot": "foreign", "pid": 102, "final_state": "exited"},
            ],
            "errors": [],
        }
        self.assertEqual(validate_result_shape(payload), [])
        payload["steps"]["forged_identity_rejections"]["command_line_sha256"] = {
            "terminated": True,
            "exit_code": 0,
        }
        self.assertIn("forged_identity_contract", validate_result_shape(payload))

    def test_guard_source_closes_process_objects_and_gate_has_no_generic_kill_primitive(self) -> None:
        guard_source = (_TOOLS_DIR / "process-guard.ps1").read_text(encoding="utf-8")
        gate_source = (
            _TOOLS_DIR / "_session_coordination" / "process_guard_gate.py"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(guard_source.count("$proc.Dispose()"), 2)
        self.assertNotIn("taskkill", gate_source.casefold())
        self.assertNotIn("stop-process", gate_source.casefold())
        self.assertNotIn(".kill(", gate_source.casefold())


class Sequence:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.fail_events: set[str] = set()
        self.on_event = None

    def __call__(self, event: dict[str, object]) -> bool:
        self.events.append(dict(event))
        if self.on_event is not None:
            self.on_event(event)
        return event.get("event") not in self.fail_events


class Handle:
    def __init__(self, pid: int = 7001, *, confirmed_exit: bool = True) -> None:
        self.pid = pid
        self.confirmed_exit = confirmed_exit
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1

    def wait(self, timeout: float):
        if not self.confirmed_exit:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def poll(self):
        return 0 if self.confirmed_exit else None


class Launcher:
    def __init__(self, handle: Handle | None = None) -> None:
        self.handle = handle or Handle()
        self.calls: list[tuple[list[str], str, str]] = []

    def __call__(self, argv: list[str], cwd: str, window_style: str) -> Handle:
        self.calls.append((list(argv), cwd, window_style))
        return self.handle


class Guard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict[str, object]] = {}
        self.snapshot_calls: list[int] = []
        self.terminate_calls: list[ProcessRecord] = []
        self.terminate_results: list[dict[str, object]] = []

    def snapshot(self, pid: int) -> dict[str, object]:
        self.snapshot_calls.append(pid)
        return dict(self.snapshots.get(pid, {"error": "identity_unavailable", "exit_code": 3}))

    def terminate(self, record: ProcessRecord) -> dict[str, object]:
        self.terminate_calls.append(record)
        if self.terminate_results:
            return self.terminate_results.pop(0)
        return {"terminated": True, "exit_code": 0}


def record(pid: int, role: str = "client") -> ProcessRecord:
    return ProcessRecord(
        pid,
        f"2026-07-15T00:00:{pid % 60:02d}.0000000Z",
        HASH_A,
        HASH_B,
        role,
        identity_scheme="psutil-argv-v2",
    )


def identity(record_value: ProcessRecord) -> dict[str, object]:
    return {
        "pid": record_value.pid,
        "creation_time_utc": record_value.creation_time_utc,
        "executable_sha256": record_value.executable_sha256,
        "command_line_sha256": record_value.command_line_sha256,
        "identity_scheme": record_value.identity_scheme,
        "identity_complete": True,
    }


class LifecycleFixture:
    def __init__(self, *, confirmed_exit: bool = True) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe"):
            (self.game / name).write_bytes(b"")
        self.paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = Audit()
        self.coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=self.audit,
        )
        status, acquired = self.coordinator.acquire(IDENTITY, "task7")
        assert status == 200
        self.token = acquired["lease_token"]
        self.lease_id = acquired["lease_id"]
        self.store = RunManifestStore(self.paths)
        self.guard = Guard()
        self.launcher = Launcher(Handle(confirmed_exit=confirmed_exit))
        self.probe = {"known": True, "processes": []}
        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: self.probe,
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=Sequence("run"),
        )
        # fb-20260904-200816-79e2: superseding a live client needs the witness
        # of the extension gate AND a bridge row that still agrees when
        # start_run revalidates it. The daemon always wires this probe
        # (daemon.py:545). The default row is a client that has not polled for
        # a long time -- the state the gate acts on -- so the settlement tests
        # written before the witness keep measuring what they measured. The
        # refusal branches have their own tests in test_lifecycle_reconcile.
        self.peer_row: dict[str, object] = {
            "last_poll_age_s": 999.0,
            "bound_last_poll_age_s": None,
            "binding_state": None,
        }
        self.lifecycle.bridge_probe = lambda now=None: {
            "peers": {"client": dict(self.peer_row)}
        }
        # start_run ages the witness against time.time()
        # (process_lifecycle.py:1902-1903). time.time() is not monotonic: a 1ms
        # step back is already replace_witness_stale, and 60s of delay trips
        # the published bound. Pin both sides of that comparison so a loaded
        # machine still reaches settlement instead of the gate.
        self._witness_now = time.time()
        lifecycle = self.lifecycle

        def _frozen_witness(parsed, *, now):
            now = self._witness_now
            return ProcessLifecycle._replacement_witness_error(
                lifecycle, parsed, now=now
            )

        lifecycle._replacement_witness_error = _frozen_witness  # type: ignore[method-assign]

    def close(self) -> None:
        self.temporary.cleanup()

    def request(self, *, run_id: str | None = None) -> dict[str, object]:
        value: dict[str, object] = {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "red",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
        }
        if run_id is not None:
            value["run_id"] = run_id
            # fb-20260904-200816-79e2: dayz_test_worker._start_core stamps the
            # witness on exactly this shape and no other -- a client relaunch
            # over an existing run (dayz_test_worker.py:305-323). A request
            # without it is refused with replace_witness_missing before a
            # process is touched, so a settlement test would never reach the
            # settlement it measures.
            value["replace_if_not_polling_since"] = int(self._witness_now * 1000)
        return value

    def add_run(
        self,
        process: ProcessRecord,
        *,
        run_id: str = "existing",
        state: str = "RUNNING",
        owned: bool = True,
    ) -> RunRecord:
        run = RunRecord(
            run_id,
            IDENTITY.session_id if owned else None,
            self.lease_id if owned else None,
            state,
            "red",
            "@SameMod",
            "profiles",
            "mission",
            [process],
        )
        self.store.add(run)
        return run

    def release_and_reacquire(self) -> tuple[str, str]:
        status, _ = self.coordinator.release(IDENTITY, self.token)
        assert status == 200
        status, acquired = self.coordinator.acquire(IDENTITY, "replacement")
        assert status == 200
        return acquired["lease_token"], acquired["lease_id"]


class LifecycleFixtureContext(LifecycleFixture):
    def __enter__(self) -> "LifecycleFixtureContext":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class ExactLeaseBarrierTest(unittest.TestCase):
    def test_authorization_decision_carries_exact_lease_and_reservation_ids(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("id"), audit=lambda _event: True
        )
        _, acquired = coordinator.acquire(IDENTITY, "exact")
        decision = coordinator.authorize(
            IDENTITY, acquired["lease_token"], "world_time_set"
        )
        self.assertEqual(decision.lease_id, acquired["lease_id"])
        self.assertIsInstance(decision.reservation_id, str)
        self.assertTrue(decision.reservation_id)

    def _run_stale_lifecycle(self, action):
        fixture = LifecycleFixture()
        entered = threading.Event()
        resume = threading.Event()
        original = fixture.lifecycle._authorize

        def paused(client, token, command):
            decision = original(client, token, command)
            entered.set()
            self.assertTrue(resume.wait(2.0))
            return decision

        fixture.lifecycle._authorize = paused  # type: ignore[method-assign]
        results: list[dict[str, object]] = []
        worker = threading.Thread(target=lambda: results.append(action(fixture)))
        worker.start()
        self.assertTrue(entered.wait(1.0), "authorization barrier not reached")
        _, replacement_lease = fixture.release_and_reacquire()
        resume.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        return fixture, results[0], replacement_lease

    def test_stale_l1_start_cannot_launch_or_persist_under_l2(self) -> None:
        fixture, result, replacement_lease = self._run_stale_lifecycle(
            lambda item: item.lifecycle.start_run(IDENTITY, item.token, item.request())
        )
        try:
            self.assertNotIn("ok", result)
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.store.list_runs(), [])
            self.assertNotEqual(fixture.lease_id, replacement_lease)
        finally:
            fixture.close()

    def test_stale_l1_adopt_cannot_snapshot_or_persist_under_l2(self) -> None:
        holder: dict[str, ProcessRecord] = {}

        def action(fixture: LifecycleFixture):
            process = record(7101)
            holder["process"] = process
            fixture.add_run(process, state="RUNNING_IDLE", owned=False)
            fixture.guard.snapshots[process.pid] = identity(process)
            return fixture.lifecycle.adopt_run(IDENTITY, fixture.token, "existing")

        fixture, result, _ = self._run_stale_lifecycle(action)
        try:
            self.assertNotIn("ok", result)
            self.assertEqual(fixture.guard.snapshot_calls, [])
            run = fixture.store.get("existing")
            self.assertEqual((run.state, run.owner_session_id, run.owner_lease_id), ("RUNNING_IDLE", None, None))
        finally:
            fixture.close()

    def test_stale_l1_stop_cannot_snapshot_or_terminate_under_l2(self) -> None:
        def action(fixture: LifecycleFixture):
            process = record(7102)
            fixture.add_run(process, state="RUNNING", owned=True)
            fixture.guard.snapshots[process.pid] = identity(process)
            return fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")

        fixture, result, _ = self._run_stale_lifecycle(action)
        try:
            self.assertNotIn("ok", result)
            self.assertEqual(fixture.guard.snapshot_calls, [])
            self.assertEqual(fixture.guard.terminate_calls, [])
            run = fixture.store.get("existing")
            self.assertEqual(
                (run.state, run.owner_session_id, run.owner_lease_id),
                ("RUNNING", IDENTITY.session_id, fixture.lease_id),
            )
        finally:
            fixture.close()

    def test_stale_l1_enqueue_cannot_be_delivered_after_same_identity_gets_l2(self) -> None:
        state = loopback.ServerState("key")
        bind_both_peers(state)
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda _event: True,
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        _, first = coordinator.acquire(IDENTITY, "first")
        entered = threading.Event()
        resume = threading.Event()
        original = coordinator.authorize

        def paused(*args, **kwargs):
            decision = original(*args, **kwargs)
            entered.set()
            self.assertTrue(resume.wait(2.0))
            return decision

        coordinator.authorize = paused  # type: ignore[method-assign]
        results: list[tuple[int, dict]] = []
        worker = threading.Thread(
            target=lambda: results.append(
                state.enqueue_command(
                    "world_time_set",
                    {},
                    "server",
                    identity_payload=IDENTITY_PAYLOAD,
                    lease_token=first["lease_token"],
                )
            )
        )
        worker.start()
        self.assertTrue(entered.wait(1.0))
        self.assertEqual(coordinator.release(IDENTITY, first["lease_token"])[0], 200)
        self.assertEqual(coordinator.acquire(IDENTITY, "second")[0], 200)
        resume.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertNotEqual(results[0][0], 200)
        self.assertEqual(accredited_poll(state, "server")[1]["commands"], [])


class DispatchAuthorityAndQuarantineTest(unittest.TestCase):
    def _state(self, *, clock: Clock | None = None):
        state = loopback.ServerState("key", time_fn=clock)
        bind_both_peers(state)
        events: list[dict[str, object]] = []
        coordinator = SessionCoordinator(
            time_fn=clock or __import__("time").monotonic,
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=lambda event: events.append(dict(event)) or True,
            cleanup=lambda session, lease, reason, active: state.cleanup_owner(
                session, lease, reason, active
            ),
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        _, acquired = coordinator.acquire(IDENTITY, "dispatch")
        return state, coordinator, acquired, events

    def test_committed_unpolled_mutation_does_not_prevent_expiry_or_dispatch(self) -> None:
        clock = Clock()
        state, coordinator, acquired, _ = self._state(clock=clock)
        status, queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
            operation_timeout_s=0.0,
        )
        self.assertEqual(status, 200)
        clock.advance(121.0)
        with patch.object(loopback, "COMMAND_TTL_S", 1000.0):
            polled = accredited_poll(state, "server")[1]
        self.assertEqual(polled["commands"], [])
        self.assertEqual(coordinator.status(IDENTITY)["self"]["state"], "none")
        self.assertIn(
            state.take_result(queued["id"])["error"],
            {"lease_expired", "authority_expired", "owner_release", "lease_inactive"},
        )

    def test_retail_appearing_before_poll_discards_mutation_but_delivers_read(self) -> None:
        state, _coordinator, acquired, events = self._state()
        mutation = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )[1]
        read = state.enqueue_command(
            "query_player_state", {}, "server", identity_payload=IDENTITY_PAYLOAD
        )[1]
        state.retail_probe = lambda: {
            "known": True,
            "processes": [{"pid": 42, "name": "DayZ_x64.exe"}],
        }
        commands = accredited_poll(state, "server")[1]["commands"]
        self.assertEqual([item["id"] for item in commands], [read["id"]])
        self.assertEqual(state.take_result(mutation["id"])["error"], "retail_quarantine")
        self.assertIn("session_rejected", [event.get("event") for event in events])

    def test_unknown_snapshot_before_poll_discards_mutation(self) -> None:
        state, _coordinator, acquired, _events = self._state()
        queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )[1]
        state.retail_probe = lambda: {"known": False, "processes": []}
        self.assertEqual(accredited_poll(state, "server")[1]["commands"], [])
        self.assertEqual(state.take_result(queued["id"])["error"], "retail_quarantine")

    def test_missing_probe_with_coordination_is_unknown_and_fail_closed(self) -> None:
        state, _coordinator, acquired, _events = self._state()
        state.retail_probe = None
        status, result = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )
        self.assertEqual((status, result["error"]), (409, "retail_quarantine"))
        self.assertEqual(accredited_poll(state, "server")[1]["commands"], [])

    def test_dispatch_claim_wins_before_release_and_delivers_exactly_once(self) -> None:
        state, coordinator, acquired, _events = self._state()
        queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )[1]
        claimed = threading.Event()
        resume = threading.Event()
        original = coordinator.claim_dispatch

        def paused(*args, **kwargs):
            result = original(*args, **kwargs)
            claimed.set()
            self.assertTrue(resume.wait(2.0))
            return result

        coordinator.claim_dispatch = paused  # type: ignore[method-assign]
        poll_result: list[tuple[int, dict]] = []
        release_result: list[tuple[int, dict]] = []
        poll = threading.Thread(target=lambda: poll_result.append(accredited_poll(state, "server")))
        poll.start()
        self.assertTrue(claimed.wait(1.0))
        release = threading.Thread(
            target=lambda: release_result.append(
                coordinator.release(IDENTITY, acquired["lease_token"])
            )
        )
        release.start()
        resume.set()
        poll.join(2.0)
        release.join(2.0)
        self.assertFalse(poll.is_alive() or release.is_alive())
        self.assertEqual([item["id"] for item in poll_result[0][1]["commands"]], [queued["id"]])
        self.assertEqual(release_result[0][1]["cleanup"]["cancelled"], 0)

    def test_release_wins_before_dispatch_claim_and_delivers_nothing(self) -> None:
        state, coordinator, acquired, _events = self._state()
        queued = state.enqueue_command(
            "world_time_set",
            {},
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=acquired["lease_token"],
        )[1]
        before_claim = threading.Event()
        resume = threading.Event()
        original = coordinator.claim_dispatch

        def paused(*args, **kwargs):
            before_claim.set()
            self.assertTrue(resume.wait(2.0))
            return original(*args, **kwargs)

        coordinator.claim_dispatch = paused  # type: ignore[method-assign]
        poll_result: list[tuple[int, dict]] = []
        release_result: list[tuple[int, dict]] = []
        poll = threading.Thread(target=lambda: poll_result.append(accredited_poll(state, "server")))
        poll.start()
        self.assertTrue(before_claim.wait(1.0))
        release = threading.Thread(
            target=lambda: release_result.append(
                coordinator.release(IDENTITY, acquired["lease_token"])
            )
        )
        release.start()
        deadline = __import__("time").monotonic() + 1.0
        while coordinator.snapshot_payload()["active"] is not None:
            if __import__("time").monotonic() >= deadline:
                self.fail("release did not invalidate before claim")
        resume.set()
        poll.join(2.0)
        release.join(2.0)
        self.assertFalse(poll.is_alive() or release.is_alive())
        self.assertEqual(poll_result[0][1]["commands"], [])
        self.assertEqual(state.take_result(queued["id"])["error"], "lease_inactive")


class LifecyclePostAuditQuarantineTest(unittest.TestCase):
    def _switch_probe_on(self, fixture: LifecycleFixture, event_name: str) -> None:
        def change(event):
            if event.get("event") == event_name:
                fixture.probe = {
                    "known": True,
                    "processes": [{"pid": 77, "name": "DayZ_x64.exe"}],
                }

        fixture.audit.on_event = change

    def test_retail_appearing_during_start_audit_blocks_popen_and_manifest(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[expected.pid] = identity(expected)
            self._switch_probe_on(fixture, "lifecycle_start")
            result = fixture.lifecycle.start_run(IDENTITY, fixture.token, fixture.request())
            self.assertEqual(result["error"], "retail_quarantine")
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.store.list_runs(), [])
        finally:
            fixture.close()

    def test_retail_appearing_during_stop_audit_blocks_manifest_and_guard(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(7201)
            fixture.add_run(expected)
            fixture.guard.snapshots[expected.pid] = identity(expected)
            self._switch_probe_on(fixture, "lifecycle_stop")
            result = fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(result["error"], "retail_quarantine")
            self.assertEqual(fixture.guard.terminate_calls, [])
            self.assertEqual(fixture.store.get("existing").state, "RUNNING")
        finally:
            fixture.close()

    def test_retail_appearing_during_adopt_audit_blocks_manifest(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(7202)
            fixture.add_run(expected, state="RUNNING_IDLE", owned=False)
            fixture.guard.snapshots[expected.pid] = identity(expected)
            self._switch_probe_on(fixture, "lifecycle_adopt")
            result = fixture.lifecycle.adopt_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(result["error"], "retail_quarantine")
            self.assertEqual(fixture.store.get("existing").state, "RUNNING_IDLE")
        finally:
            fixture.close()

    def test_retail_appearing_during_admin_audit_blocks_manifest(self) -> None:
        fixture = LifecycleFixture()
        try:
            expected = record(7203)
            fixture.add_run(expected, state="UNRECONCILED")
            fixture.guard.snapshots[expected.pid] = identity(expected)
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [
                    {"pid": expected.pid, "name": "DayZDiag_x64.exe"}
                ],
            }
            self._switch_probe_on(fixture, "admin_reconcile")
            result = fixture.lifecycle.admin_reconcile("existing", expected.pid, "incident")
            self.assertEqual(result["error"], "retail_quarantine")
            self.assertEqual(fixture.store.get("existing").state, "UNRECONCILED")
        finally:
            fixture.close()


class RunStateRecoveryTest(unittest.TestCase):
    def test_supplied_unknown_or_exited_run_id_never_launches(self) -> None:
        for mode in ("unknown", "exited"):
            with self.subTest(mode=mode):
                fixture = LifecycleFixture()
                try:
                    supplied = "missing"
                    if mode == "exited":
                        supplied = "exited"
                        fixture.store.add(
                            RunRecord(
                                supplied,
                                None,
                                None,
                                "EXITED",
                                "red",
                                "@SameMod",
                                "profiles",
                                "mission",
                                [],
                            )
                        )
                    expected = record(fixture.launcher.handle.pid)
                    fixture.guard.snapshots[expected.pid] = identity(expected)
                    result = fixture.lifecycle.start_run(
                        IDENTITY, fixture.token, fixture.request(run_id=supplied)
                    )
                    self.assertNotIn("ok", result)
                    self.assertEqual(fixture.launcher.calls, [])
                finally:
                    fixture.close()

    def test_confirmed_handle_exit_after_snapshot_failure_persists_exited(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=True)
        try:
            result = fixture.lifecycle.start_run(IDENTITY, fixture.token, fixture.request())
            self.assertIn(result["error"], {"identity_unavailable", "manual_cleanup_required"})
            run = fixture.store.list_runs()[0]
            self.assertEqual((run.state, run.owner_session_id, run.owner_lease_id), ("EXITED", None, None))
        finally:
            fixture.close()

    def test_unconfirmed_handle_exit_requires_manual_cleanup(self) -> None:
        fixture = LifecycleFixture(confirmed_exit=False)
        try:
            result = fixture.lifecycle.start_run(IDENTITY, fixture.token, fixture.request())
            self.assertEqual(result["error"], "manual_cleanup_required")
            run = fixture.store.list_runs()[0]
            self.assertEqual((run.state, run.processes), ("UNRECONCILED", []))
        finally:
            fixture.close()

    def test_partial_stop_persists_only_survivors(self) -> None:
        fixture = LifecycleFixture()
        try:
            first, second = record(7302), record(7303)
            run = fixture.add_run(first)
            run.processes.append(second)
            fixture.store.replace(run)
            fixture.guard.snapshots = {first.pid: identity(first), second.pid: identity(second)}
            fixture.guard.terminate_results = [
                {"terminated": True, "exit_code": 0},
                {"terminated": False, "error": "process_identity_mismatch", "exit_code": 4},
            ]
            result = fixture.lifecycle.stop_run(IDENTITY, fixture.token, "existing")
            self.assertEqual(result["error"], "partial_cleanup")
            self.assertEqual(fixture.store.get("existing").processes, [second])
        finally:
            fixture.close()

    def test_reconcile_prunes_only_process_not_found_and_keeps_exact_survivor(self) -> None:
        fixture = LifecycleFixture()
        try:
            gone, survivor = record(7304), record(7305)
            run = fixture.add_run(gone, state="UNRECONCILED")
            run.processes.append(survivor)
            fixture.store.replace(run)
            fixture.guard.snapshots[gone.pid] = {"error": "process_not_found", "exit_code": 4}
            fixture.guard.snapshots[survivor.pid] = identity(survivor)
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [
                    {"pid": survivor.pid, "name": "DayZDiag_x64.exe"}
                ],
            }
            result = fixture.lifecycle.admin_reconcile("existing", survivor.pid, "incident")
            self.assertEqual(result["state"], "RUNNING_IDLE")
            self.assertEqual(fixture.store.get("existing").processes, [survivor])
        finally:
            fixture.close()

    def test_reconcile_all_registered_processes_not_found_exits_run(self) -> None:
        fixture = LifecycleFixture()
        try:
            gone = record(7307)
            fixture.add_run(gone, state="UNRECONCILED")
            fixture.guard.snapshots[gone.pid] = {
                "error": "process_not_found",
                "exit_code": 4,
            }
            result = fixture.lifecycle.admin_reconcile("existing", gone.pid, "incident")
            self.assertEqual(result["state"], "EXITED")
            run = fixture.store.get("existing")
            self.assertEqual((run.state, run.processes), ("EXITED", []))
        finally:
            fixture.close()

    def test_empty_unreconciled_requires_explicit_empty_mode_and_known_zero_diag(self) -> None:
        fixture = LifecycleFixture()
        try:
            empty = RunRecord(
                "empty",
                IDENTITY.session_id,
                fixture.lease_id,
                "UNRECONCILED",
                "red",
                "@SameMod",
                "profiles",
                "mission",
                [],
            )
            fixture.store.add(empty)
            fixture.lifecycle.diag_probe = lambda: {"known": True, "processes": []}
            result = fixture.lifecycle.admin_reconcile(
                "empty", None, "confirmed empty", empty=True
            )
            self.assertEqual(result["state"], "EXITED")
            run = fixture.store.get("empty")
            self.assertEqual((run.state, run.owner_session_id, run.owner_lease_id), ("EXITED", None, None))
        finally:
            fixture.close()

    def test_empty_reconcile_unknown_or_present_diag_preserves_bytes(self) -> None:
        for probe in (
            {"known": False, "processes": []},
            {"known": True, "processes": [{"pid": 88, "name": "DayZDiag_x64.exe"}]},
        ):
            with self.subTest(probe=probe), LifecycleFixtureContext() as fixture:
                empty = RunRecord(
                    "empty",
                    IDENTITY.session_id,
                    fixture.lease_id,
                    "UNRECONCILED",
                    "red",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [],
                )
                fixture.store.add(empty)
                before = fixture.paths.runs_path.read_bytes()
                fixture.lifecycle.diag_probe = lambda probe=probe: probe
                result = fixture.lifecycle.admin_reconcile(
                    "empty", None, "confirmed empty", empty=True
                )
                self.assertNotIn("reconciled", result)
                self.assertEqual(fixture.paths.runs_path.read_bytes(), before)

    def test_reconcile_unknown_or_mismatch_preserves_manifest_bytes(self) -> None:
        for response in (
            {"error": "guard_unavailable", "exit_code": 3},
            {"error": "process_not_found", "exit_code": 3},
            identity(record(7306)) | {"creation_time_utc": "different"},
        ):
            with self.subTest(response=response.get("error", "mismatch")):
                fixture = LifecycleFixture()
                try:
                    expected = record(7306)
                    fixture.add_run(expected, state="UNRECONCILED")
                    before = fixture.paths.runs_path.read_bytes()
                    fixture.guard.snapshots[expected.pid] = response
                    result = fixture.lifecycle.admin_reconcile("existing", expected.pid, "incident")
                    self.assertNotIn("reconciled", result)
                    self.assertEqual(fixture.paths.runs_path.read_bytes(), before)
                finally:
                    fixture.close()


class RestartAndManifestTest(unittest.TestCase):
    def test_activation_releases_previous_generation_running_owner_without_guard(self) -> None:
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            store.add(
                RunRecord(
                    "persisted",
                    "old-session",
                    "old-lease",
                    "RUNNING",
                    "red",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [record(7401)],
                )
            )
            state = loopback.ServerState("key")
            bind_both_peers(state)
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                daemon._activate_server_coordination(state, "new-generation")
            run = state.lifecycle.manifest.get("persisted")
            self.assertEqual((run.state, run.owner_session_id, run.owner_lease_id), ("RUNNING_IDLE", None, None))

    def test_restart_release_audit_failure_aborts_activation_without_manifest_change(self) -> None:
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            store.add(
                RunRecord(
                    "persisted",
                    "old-session",
                    "old-lease",
                    "RUNNING",
                    "red",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [record(7403)],
                )
            )
            before = paths.runs_path.read_bytes()
            state = loopback.ServerState("key")
            bind_both_peers(state)
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": temporary}),
                patch.object(daemon.JsonlAuditWriter, "write", return_value=False),
                self.assertRaisesRegex(RuntimeError, "restart.*audit"),
            ):
                daemon._activate_server_coordination(state, "new-generation")
            self.assertEqual(paths.runs_path.read_bytes(), before)

    def test_corrupt_version_duplicate_and_invalid_strong_record_are_rejected(self) -> None:
        valid = {
            "run_id": "r",
            "owner_session_id": None,
            "owner_lease_id": None,
            "state": "EXITED",
            "label": "red",
            "mod": "@M",
            "profiles": "p",
            "mission": "m",
            "processes": [],
        }
        payloads: list[object] = [
            "{",
            {"version": 2, "runs": []},
            {"version": 1, "runs": [valid, valid]},
            {
                "version": 1,
                "runs": [valid | {"processes": [{
                    "pid": 1,
                    "creation_time_utc": "now",
                    "executable_sha256": "short",
                    "command_line_sha256": HASH_B,
                    "role": "client",
                }]}],
            },
        ]
        for payload in payloads:
            with self.subTest(payload=type(payload).__name__), TemporaryDirectory() as temporary:
                paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
                paths.runs_path.parent.mkdir(parents=True)
                if isinstance(payload, str):
                    paths.runs_path.write_text(payload, encoding="utf-8")
                else:
                    paths.runs_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "invalid_run_manifest"):
                    RunManifestStore(paths)

    def test_add_replace_and_release_owner_roll_back_memory_and_disk(self) -> None:
        with TemporaryDirectory() as temporary:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temporary})
            store = RunManifestStore(paths)
            original = RunRecord(
                "original", "owner", "lease", "RUNNING", "red", "@M", "p", "m", [record(7402)]
            )
            store.add(original)
            before_bytes = paths.runs_path.read_bytes()
            before_runs = store.list_runs()
            with patch.object(store, "_persist_locked", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store.add(RunRecord("new", None, None, "EXITED", "", "", "", "", []))
                replacement = store.get("original")
                replacement.label = "changed"
                with self.assertRaises(OSError):
                    store.replace(replacement)
                with self.assertRaises(OSError):
                    store.release_owner("owner", "lease")
            self.assertEqual(store.list_runs(), before_runs)
            self.assertEqual(paths.runs_path.read_bytes(), before_bytes)


class ReleaseAuditTest(unittest.TestCase):
    def test_release_finished_persists_deduplicated_cleanup_degraded(self) -> None:
        audit = Audit()
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {
                "cancelled": 1,
                "cleanup_degraded": ["retail_quarantine", "retail_quarantine"],
            },
        )
        _, acquired = coordinator.acquire(IDENTITY, "release")
        status, result = coordinator.release(IDENTITY, acquired["lease_token"])
        self.assertEqual(status, 200)
        self.assertEqual(result["cleanup_degraded"], ["retail_quarantine"])
        finished = [event for event in audit.events if event.get("event") == "session_release_finished"][-1]
        self.assertEqual(finished["cleanup_degraded"], ["retail_quarantine"])

    def test_release_finished_audit_failure_remains_surfaced_after_cleanup(self) -> None:
        audit = Audit()
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=audit,
            cleanup=lambda *_args: {"cancelled": 0},
        )
        _, acquired = coordinator.acquire(IDENTITY, "release")
        audit.fail_events.add("session_release_finished")
        status, result = coordinator.release(IDENTITY, acquired["lease_token"])
        self.assertEqual((status, result["released"]), (200, True))
        self.assertIn("audit_failed", result["cleanup_degraded"])


def http_post(base: str, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    url = base + path + "?" + urllib.parse.urlencode({"key": "key"})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        finally:
            exc.close()


class RealLifecycleHttpQuarantineTest(unittest.TestCase):
    def test_real_lifecycle_quarantine_blocks_mutations_but_status_and_release_work(self) -> None:
        fixture = LifecycleFixture()
        state = loopback.ServerState("key", coordination=fixture.coordinator)
        bind_both_peers(state)
        state.lifecycle = fixture.lifecycle
        state.retail_probe = lambda: {"known": False, "processes": []}
        fixture.probe = {"known": False, "processes": []}
        server = loopback.create_http_server(
            0, state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base = f"http://{host}:{port}"
        try:
            requests = (
                ("/lifecycle/start", {"identity": IDENTITY_PAYLOAD, "lease_token": fixture.token, "request": fixture.request()}),
                ("/lifecycle/stop", {"identity": IDENTITY_PAYLOAD, "lease_token": fixture.token, "run_id": "missing"}),
                ("/lifecycle/adopt", {"identity": IDENTITY_PAYLOAD, "lease_token": fixture.token, "run_id": "missing"}),
            )
            for path, payload in requests:
                with self.subTest(path=path):
                    status, result = http_post(base, path, payload)
                    self.assertEqual((status, result["error"]), (409, "retail_quarantine"))
            status, result = http_post(base, "/lifecycle/status", {"identity": IDENTITY_PAYLOAD})
            self.assertEqual((status, result["retail_quarantine"]), (200, True))
            status, result = http_post(
                base,
                "/admin/reconcile",
                {"run_id": "missing", "pid": 9, "reason": "incident", "confirmation": "FORCE missing 9"},
            )
            self.assertEqual((status, result["error"]), (409, "retail_quarantine"))
            status, result = http_post(
                base,
                "/admin/release",
                {
                    "lease_id": fixture.lease_id,
                    "reason": "incident",
                    "confirmation": f"FORCE {fixture.lease_id}",
                },
            )
            self.assertEqual((status, result["released"]), (200, True))
            self.assertEqual(fixture.launcher.calls, [])
            self.assertEqual(fixture.guard.snapshot_calls, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2.0)
            fixture.close()

    def test_real_lifecycle_clean_routes_cover_start_stop_adopt_reconcile_and_empty(self) -> None:
        fixture = LifecycleFixture()
        state = loopback.ServerState("key", coordination=fixture.coordinator)
        bind_both_peers(state)
        state.lifecycle = fixture.lifecycle
        state.retail_probe = lambda: {"known": True, "processes": []}
        fixture.probe = {"known": True, "processes": []}
        server = loopback.create_http_server(
            0, state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base = f"http://{host}:{port}"
        try:
            launched = record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[launched.pid] = identity(launched)
            status, started = http_post(
                base,
                "/lifecycle/start",
                {
                    "identity": IDENTITY_PAYLOAD,
                    "lease_token": fixture.token,
                    "request": fixture.request(),
                },
            )
            self.assertEqual((status, started["state"]), (200, "RUNNING"))
            status, state_payload = http_post(
                base, "/lifecycle/status", {"identity": IDENTITY_PAYLOAD}
            )
            self.assertEqual((status, state_payload["retail_quarantine"]), (200, False))
            status, stopped = http_post(
                base,
                "/lifecycle/stop",
                {
                    "identity": IDENTITY_PAYLOAD,
                    "lease_token": fixture.token,
                    "run_id": started["run_id"],
                },
            )
            self.assertEqual((status, stopped["state"]), (200, "EXITED"))

            idle_process = record(7601)
            fixture.add_run(
                idle_process,
                run_id="idle",
                state="RUNNING_IDLE",
                owned=False,
            )
            fixture.guard.snapshots[idle_process.pid] = identity(idle_process)
            status, adopted = http_post(
                base,
                "/lifecycle/adopt",
                {
                    "identity": IDENTITY_PAYLOAD,
                    "lease_token": fixture.token,
                    "run_id": "idle",
                },
            )
            self.assertEqual((status, adopted["state"]), (200, "RUNNING"))

            survivor = record(7602)
            fixture.add_run(survivor, run_id="repair", state="UNRECONCILED")
            fixture.guard.snapshots[survivor.pid] = identity(survivor)
            fixture.lifecycle.diag_probe = lambda: {
                "known": True,
                "processes": [
                    {"pid": survivor.pid, "name": "DayZDiag_x64.exe"}
                ],
            }
            status, reconciled = http_post(
                base,
                "/admin/reconcile",
                {
                    "run_id": "repair",
                    "pid": survivor.pid,
                    "reason": "repair",
                    "confirmation": f"FORCE repair {survivor.pid}",
                },
            )
            self.assertEqual((status, reconciled["state"]), (200, "RUNNING_IDLE"))

            fixture.store.add(
                RunRecord(
                    "empty-http",
                    IDENTITY.session_id,
                    fixture.lease_id,
                    "UNRECONCILED",
                    "red",
                    "@SameMod",
                    "profiles",
                    "mission",
                    [],
                )
            )
            fixture.lifecycle.diag_probe = lambda: {"known": True, "processes": []}
            status, emptied = http_post(
                base,
                "/admin/reconcile",
                {
                    "run_id": "empty-http",
                    "empty": True,
                    "reason": "confirmed empty",
                    "confirmation": "FORCE empty-http EMPTY",
                },
            )
            self.assertEqual((status, emptied["state"]), (200, "EXITED"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2.0)
            fixture.close()


class DaemonReleaseWiringTest(unittest.TestCase):
    def test_owner_admin_and_expiry_release_runs_idle_without_guard_in_clean_or_quarantine(self) -> None:
        for trigger in ("owner", "admin", "expiry"):
            for quarantined in (False, True):
                with self.subTest(trigger=trigger, quarantined=quarantined), TemporaryDirectory() as temporary:
                    config = ServerConfig(
                        mode="daemon",
                        key="key",
                        port=0,
                        log_sink=lambda _message: None,
                    )
                    with patch.dict(os.environ, {"LOCALAPPDATA": temporary}), patch.object(
                        daemon, "_ensure_identity_migration", return_value=None
                    ):
                        state = daemon.build_server_state(
                            config,
                            "key",
                            daemon_generation=f"{trigger}-{quarantined}",
                            activate_coordination=True,
                        )
                    probe = (
                        {"known": False, "processes": []}
                        if quarantined
                        else {"known": True, "processes": []}
                    )
                    state.retail_probe = lambda probe=probe: probe
                    state.lifecycle.retail_probe = lambda probe=probe: probe
                    guard = Guard()
                    state.lifecycle.guard = guard
                    status, acquired = state.coordination.acquire(IDENTITY, trigger)
                    self.assertEqual(status, 200)
                    state.lifecycle.manifest.add(
                        RunRecord(
                            "managed",
                            IDENTITY.session_id,
                            acquired["lease_id"],
                            "RUNNING",
                            "red",
                            "@SameMod",
                            "profiles",
                            "mission",
                            [record(7701)],
                        )
                    )
                    with state.coordination._condition:
                        state.coordination._active.vehicle_active = True
                    if trigger == "owner":
                        _, response = state.coordination.release(
                            IDENTITY, acquired["lease_token"]
                        )
                    elif trigger == "admin":
                        _, response = state.coordination.admin_release(
                            acquired["lease_id"], "incident"
                        )
                    else:
                        with state.coordination._condition:
                            state.coordination._active.expires_at = -1.0
                        response = state.coordination.status(IDENTITY)
                    idle = state.lifecycle.manifest.get("managed")
                    self.assertEqual(
                        (idle.state, idle.owner_session_id, idle.owner_lease_id),
                        ("RUNNING_IDLE", None, None),
                    )
                    self.assertEqual(guard.snapshot_calls, [])
                    self.assertEqual(guard.terminate_calls, [])
                    degraded = response.get("cleanup_degraded", [])
                    self.assertEqual(
                        "retail_quarantine" in degraded,
                        quarantined,
                    )
                    with state.coordination._condition:
                        terminalized = state.coordination._condition.wait_for(
                            lambda: (
                                not state.coordination._handoff_pending
                                and state.coordination._cleanup_worker_active == 0
                            ),
                            timeout=2.0,
                        )
                    self.assertTrue(terminalized)
                    # _persist_snapshot_locked releases the condition lock while
                    # writing coordination.json (atomic .tmp). handoff_pending can
                    # therefore clear mid-write; join release-audit/cleanup workers
                    # so TemporaryDirectory does not hit WinError 32/145 on the tmp.
                    # If workers outlive the deadline, fail explicitly — never let
                    # tempfile.WinError 145 be the first signal.
                    _wait_for_dayz_mcp_background_workers(timeout_s=2.0)


_DAYZ_MCP_WORKER_PREFIXES = (
    "dayz-mcp-release-audit-",
    "dayz-mcp-cleanup-",
    "dayz-mcp-fenced-cleanup-",
)


def _wait_for_dayz_mcp_background_workers(
    *,
    timeout_s: float = 2.0,
    enumerate_fn=threading.enumerate,
    monotonic_fn=time.monotonic,
) -> None:
    """Join dayz-mcp background workers or fail with their names.

    Raises AssertionError if any matching thread is still alive after timeout_s.
    """
    deadline = monotonic_fn() + timeout_s
    while True:
        workers = [
            thread
            for thread in enumerate_fn()
            if thread.name.startswith(_DAYZ_MCP_WORKER_PREFIXES)
        ]
        if not workers:
            return
        if monotonic_fn() >= deadline:
            names = ", ".join(sorted({thread.name for thread in workers}))
            raise AssertionError(
                f"los workers {names} no terminaron en {timeout_s} s"
            )
        for thread in workers:
            thread.join(timeout=0.05)


class DayzMcpWorkerWaitTest(unittest.TestCase):
    def test_wait_fails_explicitly_when_workers_outlive_deadline(self) -> None:
        class _StuckThread:
            name = "dayz-mcp-release-audit-deadbeef"

            def join(self, timeout: float | None = None) -> None:
                return None

        stuck = _StuckThread()
        ticks = {"n": 0}

        def mono() -> float:
            # 0.0 -> deadline=2.0; 0.5 -> still waiting; 2.0 -> fail
            ticks["n"] += 1
            return {1: 0.0, 2: 0.5, 3: 2.0}.get(ticks["n"], 2.0)

        with self.assertRaisesRegex(
            AssertionError,
            r"los workers dayz-mcp-release-audit-deadbeef no terminaron en 2\.0 s",
        ):
            _wait_for_dayz_mcp_background_workers(
                timeout_s=2.0,
                enumerate_fn=lambda: [stuck],
                monotonic_fn=mono,
            )

    def test_wait_returns_when_no_workers(self) -> None:
        _wait_for_dayz_mcp_background_workers(
            timeout_s=2.0,
            enumerate_fn=lambda: [],
            monotonic_fn=lambda: 0.0,
        )


if __name__ == "__main__":
    unittest.main()
