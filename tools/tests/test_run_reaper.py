from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.test_process_lifecycle import (
    IDENTITY_A,
    IDENTITY_B,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
)


class InstallRunReaperTest(unittest.TestCase):
    """The daemon-side periodic reaper thread (daemon.install_run_reaper): reaps
    first then waits (immediate first pass), stops promptly on the shared event, and
    survives a failing pass. It calls only ProcessLifecycle.reap_dead_runs — never
    terminate — so it is safe by construction."""

    def test_reaps_immediately_and_stops_on_event(self) -> None:
        stop = threading.Event()
        calls: list[int] = []
        logs: list[str] = []

        class FakeLifecycle:
            def reap_dead_runs(self_inner) -> list[str]:
                calls.append(1)
                stop.set()
                return ["run-x"]

        thread = daemon.install_run_reaper(
            FakeLifecycle(), interval_s=0.01, log=logs.append, stop=stop
        )
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(len(calls), 1)
        self.assertTrue(any("run-x" in message for message in logs))

    def test_a_failing_pass_does_not_kill_the_thread(self) -> None:
        stop = threading.Event()
        state = {"passes": 0}
        logs: list[str] = []

        class FakeLifecycle:
            def reap_dead_runs(self_inner) -> list[str]:
                state["passes"] += 1
                if state["passes"] == 1:
                    raise RuntimeError("boom")
                stop.set()
                return []

        thread = daemon.install_run_reaper(
            FakeLifecycle(), interval_s=0.01, log=logs.append, stop=stop
        )
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(state["passes"], 2)
        self.assertTrue(any("pass failed" in message for message in logs))


class RunReaperCoordinatorWakeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe", "DayZServer_x64.exe"):
            (self.game / name).write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=self.audit,
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(self, record, *, owner: str | None = "A", state: str = "RUNNING"):
        run = RunRecord(
            "run-existing",
            owner,
            "lease-A" if owner else None,
            state,
            "same",
            "@SameMod",
            "profiles",
            "mission",
            [record],
        )
        self.store.add(run)
        return run

    def _dead(self, pid: int) -> None:
        self.guard.snapshots[pid] = {"error": "process_not_found", "exit_code": 4}

    def test_note_run_reaped_wakes_condition_waiters(self) -> None:
        coordinator = SessionCoordinator()
        woke = threading.Event()
        entered = threading.Event()

        def waiter() -> None:
            with coordinator._condition:
                entered.set()
                coordinator._condition.wait(30.0)
            woke.set()

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(timeout=1.0))
        deadline = time.monotonic() + 1.0
        while thread.is_alive() and time.monotonic() < deadline:
            coordinator.note_run_reaped("sess-a")
            thread.join(timeout=0.05)
        self.assertFalse(thread.is_alive())
        self.assertTrue(woke.is_set())

    def test_reap_drops_box_claim_of_owner_session(self) -> None:
        claimed = self.coordinator.box_wait_touch(IDENTITY_A, claim=True)
        self.assertTrue(claimed.get("box_claimed"))
        self.assertTrue(self.coordinator.box_is_claimed())
        self.assertTrue(self.coordinator.box_blocks_start(IDENTITY_B))
        self.add_run(process(48976), owner="A", state="RUNNING")
        self._dead(48976)
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["run-existing"])
        self.assertFalse(self.coordinator.box_is_claimed())
        self.assertFalse(self.coordinator.box_blocks_start(IDENTITY_B))

    def test_reap_does_not_drop_active_lease(self) -> None:
        self.assertIsNotNone(self.coordinator.snapshot_payload()["active"])
        self.coordinator.note_run_reaped(IDENTITY_A.session_id)
        active = self.coordinator.snapshot_payload()["active"]
        self.assertIsNotNone(active)
        self.assertEqual(active["lease_id"], "lease-A")
        self.assertTrue(
            self.coordinator.authorize(IDENTITY_A, self.token_a, "world_spawn").allowed
        )

    def test_reap_wake_is_audited(self) -> None:
        before = len(self.audit.events)
        self.coordinator.note_run_reaped(IDENTITY_A.session_id)
        events = [event for event in self.audit.events[before:] if event.get("event") == "run_reaped_wake"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("decision"), "woke")
        self.assertEqual(events[0].get("owner_session"), IDENTITY_A.session_id)

    def test_reap_invalidates_box_occupancy_cache(self) -> None:
        self.add_run(process(48976), owner="A", state="RUNNING")
        before = self.lifecycle.box_occupancy()
        self.assertTrue(before["occupied"])
        self.assertEqual([run["run_id"] for run in before["runs"]], ["run-existing"])
        self.assertIsNotNone(self.lifecycle._box_cache)
        self._dead(48976)
        self.assertEqual(self.lifecycle.reap_dead_runs(), ["run-existing"])
        self.assertIsNone(self.lifecycle._box_cache)
        after = self.lifecycle.box_occupancy()
        self.assertFalse(after["occupied"])
        self.assertEqual(after["runs"], [])


if __name__ == "__main__":
    unittest.main()
