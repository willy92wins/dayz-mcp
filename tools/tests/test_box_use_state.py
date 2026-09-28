"""250f, plan v2.1 §3.2-§3.3: use_state in the box rows, through the lifecycle.

Observation only: these fields decide nothing yet. Clocks are real time plus
offsets passed to box_occupancy(now=...), because the ownerless stamp is taken
with time.time() at the transition.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.input_activity import InputSample
from dayz_mcp.process_lifecycle import (
    INPUT_SIGNAL_STALE_S,
    RUN_IDLE_CUT_S,
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.process_lifecycle_helpers import (
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate


GENERATION = "gen-now"
OTHER_WINDOW_PID = 31337


class FakeBindings:
    def __init__(self) -> None:
        self.bound: set[str] = set()

    def run_has_bound_binding(self, run_id: str) -> bool:
        return run_id in self.bound

    def fence_runs(self, run_ids: list[str]) -> None:
        return None

    def unfence_runs(self, run_ids: list[str]) -> None:
        return None


class BoxUseStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
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
        self.live: list[int] = []
        self.bindings = FakeBindings()
        self.lifecycle = self._lifecycle()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _lifecycle(self, *, with_bindings: bool = True) -> ProcessLifecycle:
        # A launch through fake bindings would need the whole instance fence;
        # the launch tests build the lifecycle without bindings, as the
        # existing start tests do.
        return ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {
                "known": True,
                "processes": [
                    {"pid": pid, "name": "DayZDiag_x64.exe"} for pid in self.live
                ],
            },
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            bindings=self.bindings if with_bindings else None,
            daemon_generation=GENERATION,
        )

    # -- helpers ---------------------------------------------------------------

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

    def release_to_idle(self, run_id: str = "run-x") -> float:
        """Adds an owned run and releases it: the ownerless clock starts now."""

        self.add_run(run_id, owner="A")
        before = time.time()
        self.assertEqual(self.lifecycle.release_owner("A", "lease-A"), [run_id])
        return before

    def good_signal(self, at: float, *, foreground: int = OTHER_WINDOW_PID, tick: int = 100) -> None:
        self.lifecycle.record_input_sample(
            InputSample(at=at, ok=True, last_input_tick=tick, idle_ms=0, foreground_pid=foreground)
        )

    def human_input(self, at: float, pid: int = 802) -> str | None:
        """Two samples with the run's window in front and the tick advancing."""

        self.good_signal(at - 0.25, foreground=pid, tick=1000)
        return self.lifecycle.record_input_sample(
            InputSample(at=at, ok=True, last_input_tick=1100, idle_ms=0, foreground_pid=pid)
        )

    def row(self, now: float, run_id: str = "run-x") -> dict[str, object]:
        box = self.lifecycle.box_occupancy(now=now)
        for item in box["runs"]:
            if item["run_id"] == run_id:
                return item
        self.fail(f"no row for {run_id}: {box['runs']}")

    # -- states ------------------------------------------------------------------

    def test_owned_run_is_agent(self) -> None:
        self.add_run(owner="A")
        row = self.row(time.time())
        self.assertEqual(row["use_state"], "agent")
        self.assertIsNone(row["idle_s"])

    def test_no_good_sample_reads_unknown_with_a_live_client(self) -> None:
        self.add_run()
        row = self.row(time.time())
        self.assertEqual(
            (row["use_state"], row["use_reason"]),
            ("unknown", "input_signal_unavailable"),
        )

    def test_stale_samples_read_unknown(self) -> None:
        self.add_run()
        now = time.time()
        self.good_signal(now - INPUT_SIGNAL_STALE_S - 0.5)
        self.assertEqual(self.row(now)["use_state"], "unknown")

    def test_failed_samples_never_refresh_the_signal(self) -> None:
        self.add_run()
        now = time.time()
        self.good_signal(now - INPUT_SIGNAL_STALE_S - 0.5)
        self.lifecycle.record_input_sample(InputSample(at=now, ok=False))
        self.assertEqual(self.row(now)["use_state"], "unknown")

    def test_input_in_the_run_window_reads_human(self) -> None:
        self.add_run()
        now = time.time()
        self.assertEqual(self.human_input(now - 1.0), "run-x")
        self.good_signal(now)
        row = self.row(now)
        self.assertEqual(row["use_state"], "human")
        self.assertAlmostEqual(row["human_input_age_s"], 1.0, places=2)

    def test_input_in_another_window_does_not_count(self) -> None:
        self.release_to_idle()
        now = time.time()
        self.good_signal(now - 0.25, tick=1000)
        self.assertIsNone(
            self.lifecycle.record_input_sample(
                InputSample(at=now, ok=True, last_input_tick=1100, idle_ms=0, foreground_pid=OTHER_WINDOW_PID)
            )
        )
        row = self.row(now)
        self.assertEqual(row["use_state"], "idle")
        self.assertIsNone(row["human_input_age_s"])

    def test_reused_pid_attributes_nothing(self) -> None:
        self.release_to_idle()
        # Same pid, another creation time: not the registered client any more.
        reused = dict(self.guard.snapshots[802])
        reused["creation_time_utc"] = "2026-07-15T09:09:09.0000000Z"
        self.guard.snapshots[802] = reused
        now = time.time()
        self.assertIsNone(self.human_input(now))

    def test_ownerless_clock_starts_at_the_transition(self) -> None:
        released = self.release_to_idle()
        self.good_signal(released + RUN_IDLE_CUT_S - 1.0)
        row = self.row(released + RUN_IDLE_CUT_S - 1.0)
        self.assertEqual(row["use_state"], "idle")
        self.assertLess(row["idle_s"], RUN_IDLE_CUT_S)

    def test_human_input_resets_the_idle_clock(self) -> None:
        released = self.release_to_idle()
        later = released + RUN_IDLE_CUT_S + 30.0
        self.human_input(later - 5.0)
        self.good_signal(later)
        row = self.row(later)
        self.assertEqual(row["use_state"], "human")
        self.assertLess(row["idle_s"], 6.0)

    def test_past_the_cut_with_a_bound_bridge_is_abandoned(self) -> None:
        released = self.release_to_idle()
        self.bindings.bound.add("run-x")
        now = released + RUN_IDLE_CUT_S + 1.0
        self.good_signal(now)
        row = self.row(now)
        self.assertEqual((row["use_state"], row["use_reason"]), ("abandoned", None))

    def test_past_the_cut_without_a_bound_bridge_waits(self) -> None:
        released = self.release_to_idle()
        now = released + RUN_IDLE_CUT_S + 1.0
        self.good_signal(now)
        row = self.row(now)
        self.assertEqual(
            (row["use_state"], row["use_reason"]), ("idle_waiting", "bridge_not_ready")
        )

    def test_a_run_of_another_generation_waits(self) -> None:
        self.add_run(generation="gen-before")
        self.bindings.bound.add("run-x")
        now = time.time() + RUN_IDLE_CUT_S + 1.0
        self.good_signal(now)
        row = self.row(now)
        self.assertEqual(
            (row["use_state"], row["use_reason"]), ("idle_waiting", "previous_generation")
        )

    def test_a_dead_client_past_the_cut_is_abandoned_without_signal(self) -> None:
        self.add_run(live=False)
        self.live.append(801)  # only the server is alive
        now = time.time() + RUN_IDLE_CUT_S + 1.0
        row = self.row(now)
        self.assertEqual((row["use_state"], row["use_reason"]), ("abandoned", "client_gone"))

    def test_a_server_alone_is_a_dead_client(self) -> None:
        self.add_run(processes=[process(801, role="server")])
        now = time.time() + RUN_IDLE_CUT_S + 1.0
        self.assertEqual(self.row(now)["use_state"], "abandoned")

    def test_unknown_scan_never_confirms_a_dead_client(self) -> None:
        self.add_run(live=False)
        self.lifecycle.diag_probe = lambda: {"known": False}
        now = time.time() + RUN_IDLE_CUT_S + 1.0
        self.assertEqual(self.row(now)["use_state"], "unknown")

    def test_two_active_runs_past_the_cut_wait(self) -> None:
        self.add_run("run-x")
        self.add_run(
            "run-y", processes=[process(901, role="server"), process(902, role="client")]
        )
        self.bindings.bound.update({"run-x", "run-y"})
        now = time.time() + RUN_IDLE_CUT_S + 1.0
        self.good_signal(now)
        row = self.row(now)
        self.assertEqual(
            (row["use_state"], row["use_reason"]), ("idle_waiting", "multiple_active_runs")
        )

    def test_restart_starts_every_clock_at_the_new_origin(self) -> None:
        released = self.release_to_idle()
        # An old clock in the lifecycle that is about to be replaced.
        self.lifecycle._ownerless_since[(GENERATION, "run-x")] = released - 1000.0
        restarted = self._lifecycle()
        self.lifecycle = restarted
        now = restarted._use_clock_origin + 10.0
        self.good_signal(now)
        row = self.row(now)
        self.assertAlmostEqual(row["idle_s"], 10.0, places=2)

    # -- launched_by ---------------------------------------------------------------

    def _start(self) -> dict[str, object]:
        self.lifecycle = self._lifecycle(with_bindings=False)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        return self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            {
                "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
                "cwd": str(self.game),
                "role": "client",
                "window_style": "normal",
                "label": "gate",
                "mod": "@Mod",
                "profiles": "profiles",
                "mission": "test",
            },
        )

    def test_the_session_that_creates_the_run_is_its_launcher(self) -> None:
        result = self._start()
        self.assertTrue(result.get("ok"), result)
        row = self.row(time.time(), result["run_id"])
        self.assertEqual(row["launched_by"], IDENTITY_A.public_payload())
        self.assertEqual(row["launched_by"]["session"], IDENTITY_A.session_id[:12])

    def test_a_restart_forgets_the_launcher(self) -> None:
        result = self._start()
        self.assertTrue(result.get("ok"), result)
        self.lifecycle = self._lifecycle(with_bindings=False)
        self.assertIsNone(self.row(time.time(), result["run_id"])["launched_by"])

    def test_a_retired_run_keeps_no_250f_state(self) -> None:
        result = self._start()
        run_id = result["run_id"]
        self.human_input(time.time(), pid=self.launcher.pid)
        self.lifecycle._seal_terminal(run_id, time.time())
        for table in (
            self.lifecycle._ownerless_since,
            self.lifecycle._human_input_at,
            self.lifecycle._launched_by,
        ):
            self.assertFalse([key for key in table if key[1] == run_id])


if __name__ == "__main__":
    unittest.main()
