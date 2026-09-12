"""W7a DZ-R9 offline for 0ab2 (state-machine, race, identity, data-loss).

In-game H8 is W7b / I3 — not this module.
"""

from __future__ import annotations

import inspect
import json
import sys
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
    occupancy_error_fields,
    takeover_target_run_id,
)
from dayz_mcp.runtime_state import (
    CoordinationSnapshotStore,
    RuntimePaths,
    _coordination_payload,
)
from dayz_mcp.server import TAKEOVER_REQUIRED, ServerConfig
from dayz_mcp import server as server_module
from dayz_mcp.session_coordination import (
    LEASE_GRACE_S,
    SESSION_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)
from tests.steam_helpers import FakeSteamGate
from tests.test_mcp_tools import _content_json
from tests.test_session_coordination import (
    AuditSink,
    CleanupSink,
    FakeClock,
    SequentialIds,
    _identity,
)
from tests.test_client_mode import _fixture_client_runtime
from tests.test_process_lifecycle import FakeGuard, FakeLauncher, process


TTL = SESSION_TTL_S
G = LEASE_GRACE_S
MID = TTL + (G / 2.0)


def _coord(clock: FakeClock, *, attached: bool, cleanup=None) -> SessionCoordinator:
    return SessionCoordinator(
        time_fn=clock,
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("id"),
        audit=AuditSink(),
        cleanup=cleanup or CleanupSink(),
        attached_run_probe=lambda _session, _lease: attached,
    )


@dataclass
class _AttachedRun:
    owner_session_id: str
    owner_lease_id: str
    state: str = "RUNNING"


class _DaemonShapedBox:
    """Same predicate as daemon.attached_run_probe + cleanup → RUNNING_IDLE."""

    def __init__(self) -> None:
        self.runs: list[_AttachedRun] = []
        self.probe_saw_released = False
        self.cleanup_calls = 0

    def probe(self, session_id: str, lease_id: str) -> bool:
        if any(run.state != "RUNNING" for run in self.runs):
            self.probe_saw_released = True
        for run in self.runs:
            if (
                run.owner_session_id == session_id
                and run.owner_lease_id == lease_id
                and run.state == "RUNNING"
            ):
                return True
        return False

    def cleanup(
        self, session_id: str, lease_id: str, _reason: str, _vehicle_active: bool
    ) -> dict[str, object]:
        self.cleanup_calls += 1
        for run in self.runs:
            if run.owner_session_id == session_id and run.owner_lease_id == lease_id:
                run.state = "RUNNING_IDLE"
                run.owner_session_id = None  # type: ignore[assignment]
                run.owner_lease_id = None  # type: ignore[assignment]
        return {"cancelled": 0, "runs_released": [session_id]}


class StateMachineR9Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")
        self.c = _identity("c")

    def test_grace_is_null_while_token_is_live(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(TTL - 0.001)
        self.assertIsNone(coordinator.status(self.a)["grace"])
        self.assertEqual(coordinator.acquire(self.a, "drive")[0], 200)

    def test_grace_arms_at_ttl_and_is_consumed_once(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        old = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(TTL)
        self.assertIsNotNone(coordinator.status(self.a)["grace"])
        first = coordinator.acquire(self.a, "drive")
        self.assertEqual(first[0], 200)
        self.assertNotEqual(first[1]["lease_token"], old)
        self.assertIsNone(coordinator.status(self.a)["grace"])
        clock.advance(1.0)
        second = coordinator.acquire(self.a, "drive")
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["lease_token"], first[1]["lease_token"])

    def test_voluntary_release_does_not_arm_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        released = coordinator.release(self.a, token)
        self.assertEqual(released[0], 200)
        self.assertIsNone(coordinator.status(self.a)["grace"])
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual((status, payload["status"]), (200, "active"))

    def test_third_identity_is_queued_during_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        self.assertEqual(coordinator.acquire(self.b, "drive")[0], 202)
        self.assertEqual(coordinator.acquire(self.c, "drive")[0], 202)
        self.assertEqual(coordinator.acquire(self.a, "drive")[0], 200)

    def test_grace_expires_exactly_at_until(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(TTL + G)
        self.assertIsNone(coordinator.status(self.a)["grace"])
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual((status, payload["status"]), (200, "active"))

    def test_release_active_probes_before_cleanup_in_source(self) -> None:
        source = inspect.getsource(SessionCoordinator._release_active_locked)
        probe_at = source.find("_probe_attached_run_locked")
        cleanup_at = source.find("self._cleanup(")
        self.assertGreater(probe_at, 0)
        self.assertGreater(cleanup_at, probe_at)


class RaceR9Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")

    def test_probe_sees_running_before_cleanup_mutates_idle(self) -> None:
        clock = FakeClock()
        box = _DaemonShapedBox()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=box.cleanup,
            attached_run_probe=box.probe,
        )
        payload = coordinator.acquire(self.a, "drive")[1]
        box.runs.append(
            _AttachedRun(
                owner_session_id=self.a.session_id,
                owner_lease_id=payload["lease_id"],
            )
        )
        clock.advance(MID)
        status, granted = coordinator.acquire(self.a, "drive")
        self.assertEqual((status, granted["status"]), (200, "active"))
        self.assertGreaterEqual(box.cleanup_calls, 1)
        self.assertFalse(box.probe_saw_released)
        self.assertEqual(box.runs[0].state, "RUNNING_IDLE")
        self.assertIsNone(box.runs[0].owner_session_id)

    def test_probe_negative_after_idle_transition(self) -> None:
        box = _DaemonShapedBox()
        box.runs.append(_AttachedRun("session-a", "lease-1"))
        self.assertTrue(box.probe("session-a", "lease-1"))
        box.cleanup("session-a", "lease-1", "lease_expired", False)
        self.assertFalse(box.probe("session-a", "lease-1"))

    def test_concurrent_former_and_stranger_acquire_one_active(self) -> None:
        for _ in range(8):
            clock = FakeClock()
            coordinator = _coord(clock, attached=True)
            coordinator.acquire(self.a, "drive")
            clock.advance(MID)
            coordinator.status(self.a)
            barrier = threading.Barrier(2)
            results: dict[str, tuple[int, dict]] = {}
            errors: dict[str, BaseException] = {}

            def worker(name: str, client: ClientIdentity) -> None:
                try:
                    barrier.wait(timeout=2.0)
                    results[name] = coordinator.acquire(client, "drive")
                except BaseException as exc:  # noqa: BLE001 — capture for the parent
                    errors[name] = exc

            threads = [
                threading.Thread(target=worker, args=("a", self.a)),
                threading.Thread(target=worker, args=("b", self.b)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5.0)
            self.assertEqual(errors, {})
            self.assertEqual(results["a"][0], 200)
            self.assertEqual(results["b"][0], 202)
            statuses = {results["a"][1]["status"], results["b"][1]["status"]}
            self.assertEqual(statuses, {"active", "queued"})
            self.assertEqual(results["a"][1]["status"], "active")

    def test_stranger_wait_loses_to_former_wait_claim(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        ticket_b = coordinator.acquire(self.b, "next")[1]["ticket"]
        clock.advance(119.0)
        self.assertEqual(coordinator.wait(self.b, ticket_b, 0.0)[0], 202)
        clock.advance(MID - 119.0)
        enq = coordinator.enqueue(self.a, "back", "operation-a")
        barrier = threading.Barrier(2)
        results: dict[str, tuple[int, dict]] = {}

        def wait_a() -> None:
            barrier.wait(timeout=2.0)
            results["a"] = coordinator.wait(self.a, enq[1]["ticket"], 0.0)

        def wait_b() -> None:
            barrier.wait(timeout=2.0)
            results["b"] = coordinator.wait(self.b, ticket_b, 0.0)

        threads = [
            threading.Thread(target=wait_a),
            threading.Thread(target=wait_b),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)
        self.assertEqual(results["a"][0], 200)
        self.assertEqual(results["b"][0], 202)


class IdentityR9Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")

    def test_grace_uses_full_client_identity_not_session_prefix(self) -> None:
        clock = FakeClock()
        former = _identity("a", session_id="session-abcdefghijklmnop")
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(former, "drive")
        clock.advance(MID)
        prefix = ClientIdentity.from_payload(
            {
                "platform": former.platform,
                "pid": former.pid,
                "ppid": former.ppid,
                "started_at_utc": former.started_at_utc,
                "session_id": former.session_id[:12],
                "task_label": former.task_label,
            }
        )
        self.assertNotEqual(prefix, former)
        status, payload = coordinator.acquire(prefix, "drive")
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(coordinator.acquire(former, "drive")[0], 200)

    def test_same_session_id_different_pid_is_identity_mismatch(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        spoof = ClientIdentity.from_payload(
            {
                "platform": self.a.platform,
                "pid": self.a.pid + 1,
                "ppid": self.a.ppid,
                "started_at_utc": self.a.started_at_utc,
                "session_id": self.a.session_id,
                "task_label": self.a.task_label,
            }
        )
        status, payload = coordinator.acquire(spoof, "drive")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "identity_mismatch")
        self.assertEqual(coordinator.acquire(self.a, "drive")[0], 200)

    def test_owner_prefix_twelve_matches_caller_full_session(self) -> None:
        session = "session-abcdefghijklmnop"
        box = {
            "runs": [
                {
                    "run_id": "abc",
                    "state": "RUNNING",
                    "owner_session": session[:12],
                }
            ]
        }
        self.assertIsNone(takeover_target_run_id(box, caller_session=session))
        fields = occupancy_error_fields(box, caller_session=session)
        self.assertIn("dayz_test_stop", str(fields.get("hint") or ""))

    def test_stranger_full_session_is_takeover_target(self) -> None:
        box = {
            "runs": [
                {
                    "run_id": "abc",
                    "state": "RUNNING",
                    "owner_session": "session-owner",
                }
            ]
        }
        self.assertEqual(
            takeover_target_run_id(box, caller_session="session-other"),
            "abc",
        )


class DataLossR9Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")

    def test_snapshot_omits_grace_and_pref_used(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        coordinator.status(self.a)
        snapshot = coordinator.snapshot_payload()
        self.assertNotIn("grace", snapshot)
        self.assertNotIn("pref_used", snapshot)
        self.assertIsNotNone(coordinator.status(self.a)["grace"])
        persisted = _coordination_payload(
            {
                **snapshot,
                "grace": {"remaining_s": 40, "pref_remaining": 1},
                "pref_used": {self.a.session_id: 1},
            },
            "gen-1",
        )
        self.assertNotIn("grace", persisted)
        self.assertNotIn("pref_used", persisted)

    def test_daemon_restart_invalidates_grace_stranger_wins(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        self.assertIsNotNone(coordinator.status(self.a)["grace"])
        restarted = _coord(FakeClock(), attached=True)
        self.assertIsNone(restarted.status(self.a)["grace"])
        status, payload = restarted.acquire(self.b, "drive")
        self.assertEqual((status, payload["status"]), (200, "active"))

    def test_generation_change_drops_injected_grace_from_disk(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = RuntimePaths.from_env({"LOCALAPPDATA": temp_dir})
            old = CoordinationSnapshotStore(paths, "old")
            old.write_coordination(
                {
                    "revision": 4,
                    "active": None,
                    "queue": [],
                    "grace": {
                        "remaining_s": 50.0,
                        "pref_remaining": 1,
                        "attached_required": True,
                    },
                }
            )
            new = CoordinationSnapshotStore(paths, "new")
            loaded = new.consume_previous_generation("new")
            self.assertEqual(loaded["event"], "daemon_restart_invalidated")
            text = new.coordination_path.read_text(encoding="utf-8")
            self.assertNotIn("grace", json.loads(text))
            self.assertNotIn("pref_remaining", text)

    def test_expiry_cleanup_idles_acknowledged_run_without_terminate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
            owner = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")
            coordinator = SessionCoordinator(
                token_fn=lambda: "token-A",
                id_fn=lambda: "lease-A",
                audit=AuditSink(),
            )
            self.assertEqual(coordinator.acquire(owner, "lifecycle")[0], 200)
            store = RunManifestStore(paths)
            guard = FakeGuard()
            record = process(4242)
            run = RunRecord(
                "run-existing",
                "A",
                "lease-A",
                "RUNNING",
                "same",
                "@SameMod",
                "profiles",
                "mission",
                [record],
            )
            store.add(run)
            lifecycle = ProcessLifecycle(
                steam_gate=FakeSteamGate(),
                coordinator=coordinator,
                manifest=store,
                audit=AuditSink(),
                guard=guard,
                retail_probe=lambda: {"known": True, "processes": []},
                diag_probe=lambda: {"known": True, "processes": []},
                game_path=game,
                launcher=FakeLauncher(),
                id_fn=lambda: "run-1",
            )
            disposition = lifecycle.begin_release_owner("A", "lease-A")
            self.assertTrue(disposition.terminal_event.wait(1.0))
            self.assertTrue(disposition.terminal_result["terminal_safe"])
            idle = store.get("run-existing")
            self.assertIsNotNone(idle)
            assert idle is not None
            self.assertEqual(idle.state, "RUNNING_IDLE")
            self.assertIsNone(idle.owner_session_id)
            self.assertEqual([proc.pid for proc in idle.processes], [4242])
            self.assertEqual(guard.terminate_calls, [])


class DayzTestRunIdentityR9Tests(unittest.IsolatedAsyncioTestCase):
    def _config(self) -> ServerConfig:
        return ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )

    def _box(self, *, state: str, owner: str | None, run_id: str) -> dict[str, object]:
        return {
            "occupied": True,
            "runs": [
                {
                    "run_id": run_id,
                    "mod": "@M",
                    "label": "lab",
                    "age_s": 12.0,
                    "state": state,
                    "owner_session": owner,
                }
            ],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
        }

    async def test_takeover_defaults_false_and_does_not_stop(self) -> None:
        run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        stop = AsyncMock(side_effect=AssertionError("must not stop"))
        runtime = _fixture_client_runtime(self._config())
        status = AsyncMock(
            return_value={
                "box": self._box(state="RUNNING", owner="other", run_id=run_id),
                "self": {"state": "none"},
            }
        )
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                new=execute,
            ),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_stop",
                new=stop,
            ),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server"},
                )
            )
        execute.assert_not_awaited()
        stop.assert_not_awaited()
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)

    async def test_owned_running_does_not_takeover_stop(self) -> None:
        run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        execute = AsyncMock(
            return_value={
                "status": "failed",
                "error_code": "active_run_exists",
                "project": "ExampleMod",
                "mode": "server",
                "run_id": None,
                "phase": "executing",
                "elapsed_s": 0.0,
                "artifacts_paths": [],
                "cleanup_degraded": False,
            }
        )
        stop = AsyncMock(side_effect=AssertionError("must not stop"))
        runtime = _fixture_client_runtime(self._config())
        caller = str(runtime.identity.session_id)
        status = AsyncMock(
            return_value={
                "box": self._box(state="RUNNING", owner=caller, run_id=run_id),
                "self": {"state": "none"},
            }
        )
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                new=execute,
            ),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_stop",
                new=stop,
            ),
            patch("psutil.Process") as process_cls,
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "takeover": True,
                    },
                )
            )
        stop.assert_not_awaited()
        execute.assert_awaited()
        process_cls.return_value.kill.assert_not_called()
        self.assertEqual(payload.get("error_code"), "active_run_exists")
        self.assertNotIn("evicted_run_id", payload)


if __name__ == "__main__":
    unittest.main()
