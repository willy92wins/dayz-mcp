from __future__ import annotations

from tests.steam_helpers import FakeSteamGate

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon, loopback, orphan_guard
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    IDENTITY_A,
    IDENTITY_B,
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
            steam_gate=FakeSteamGate(),
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


class ReapPastAnotherInstallTest(unittest.TestCase):
    """fb-20261103-194855-5394: the 1.30 daemon kept a run whose processes had
    exited until a DayZ of LIVE, launched by the other daemon from another
    install, exited too. start_run launches only game_path's DayZDiag_x64.exe,
    so a DayZ whose image is another file cannot be a survivor of a run here."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ Exp"
        self.other = self.root / "DayZ"
        for folder in (self.game, self.other):
            folder.mkdir()
            for name in ("DayZDiag_x64.exe", "DayZServer_x64.exe"):
                # Distinct bytes, so no two of them are one file.
                (folder / name).write_bytes(f"{folder.name}/{name}".encode())
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
        self.diag: list[dict[str, object]] = []
        self.images: dict[int, object] = {}
        self.store.add(
            RunRecord(
                "run-dead",
                None,
                None,
                "RUNNING_IDLE",
                "same",
                "@SameMod",
                "profiles",
                "mission",
                [process(152944, "server"), process(153208)],
            )
        )
        for pid in (152944, 153208):
            self.guard.snapshots[pid] = {"error": "process_not_found", "exit_code": 4}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _lifecycle(self, **probes: object) -> ProcessLifecycle:
        return ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": list(self.diag)},
            game_path=self.game,
            launcher=FakeLauncher(),
            id_fn=lambda: "run-1",
            **probes,
        )

    def _image_of(self, pid: int) -> object:
        image = self.images[pid]
        if isinstance(image, BaseException):
            raise image
        return image

    def _dayz(self, pid: int, image: object, name: str = "DayZDiag_x64.exe") -> None:
        self.diag.append({"pid": pid, "name": name})
        self.images[pid] = str(image) if isinstance(image, Path) else image

    def _assert_held(self, lifecycle: ProcessLifecycle) -> None:
        self.assertEqual(lifecycle.reap_dead_runs(), [])
        self.assertEqual(self.store.get("run-dead").state, "RUNNING_IDLE")

    def test_a_dayz_of_another_install_does_not_hold_the_reap(self) -> None:
        self._dayz(151392, self.other / "DayZDiag_x64.exe")
        self._dayz(146292, self.other / "DayZDiag_x64.exe")
        self._dayz(70001, self.game / "DayZServer_x64.exe", "DayZServer_x64.exe")
        lifecycle = self._lifecycle(image_path_of=self._image_of)

        self.assertEqual(lifecycle.reap_dead_runs(), ["run-dead"])

        self.assertEqual(self.store.get("run-dead").state, "EXITED")
        reaped = [event for event in self.audit.events if event.get("event") == "run_reaped"]
        self.assertEqual(len(reaped), 1)
        self.assertEqual(reaped[0]["reason"], "all_processes_gone_or_foreign")
        self.assertEqual(reaped[0]["gone_pids"], [152944, 153208])

    def test_the_agent_reap_retires_past_another_install(self) -> None:
        self._dayz(151392, self.other / "DayZDiag_x64.exe")
        lifecycle = self._lifecycle(image_path_of=self._image_of)

        result = lifecycle.reap_dead_run(IDENTITY_A, self.token_a, "run-dead")

        self.assertEqual(result, {"ok": True, "run_id": "run-dead", "state": "EXITED"})

    def test_a_dayz_of_this_install_still_holds_the_reap(self) -> None:
        self._dayz(151392, self.other / "DayZDiag_x64.exe")
        self._dayz(160000, self.game / "DayZDiag_x64.exe")
        self._assert_held(self._lifecycle(image_path_of=self._image_of))

    def test_this_install_under_another_name_still_holds_the_reap(self) -> None:
        # One file under two spellings, as a subst drive and its target are.
        alias = self.root / "alias.exe"
        os.link(self.game / "DayZDiag_x64.exe", alias)
        self._dayz(160000, alias)
        self._assert_held(self._lifecycle(image_path_of=self._image_of))

    def test_an_image_that_cannot_be_compared_still_holds_the_reap(self) -> None:
        self._dayz(160000, self.root / "removed" / "DayZDiag_x64.exe")
        self._assert_held(self._lifecycle(image_path_of=self._image_of))

    def test_an_unread_image_still_holds_the_reap(self) -> None:
        self._dayz(160000, None)
        self._assert_held(self._lifecycle(image_path_of=self._image_of))

    def test_an_image_probe_that_fails_still_holds_the_reap(self) -> None:
        self._dayz(160000, PermissionError(5, "Access is denied"))
        self._assert_held(self._lifecycle(image_path_of=self._image_of))

    def test_an_unknown_snapshot_still_holds_the_reap(self) -> None:
        lifecycle = self._lifecycle(image_path_of=self._image_of)
        lifecycle.diag_probe = lambda: {"known": False, "processes": []}
        self._assert_held(lifecycle)

    def test_without_an_image_probe_every_dayz_holds_the_reap(self) -> None:
        self._dayz(151392, self.other / "DayZDiag_x64.exe")
        self._assert_held(self._lifecycle())

    def test_the_reap_witness_names_only_a_dayz_of_this_install(self) -> None:
        self._dayz(151392, self.other / "DayZDiag_x64.exe")
        self._dayz(160000, self.game / "DayZDiag_x64.exe")
        lifecycle = self._lifecycle(image_path_of=self._image_of)

        self.assertEqual(
            lifecycle.launched_processes_still_alive("run-dead"), ("160000:diag",)
        )
        self.assertEqual(
            lifecycle.reap_rejection_witness("run-dead"), ("alive", ("160000:diag",))
        )

    def test_the_reap_witness_reads_another_install_as_clean(self) -> None:
        self._dayz(151392, self.other / "DayZDiag_x64.exe")
        lifecycle = self._lifecycle(image_path_of=self._image_of)

        self.assertEqual(lifecycle.launched_processes_still_alive("run-dead"), ())
        self.assertEqual(lifecycle.reap_rejection_witness("run-dead"), ("clean", ()))

    def test_the_daemon_lifecycle_reads_process_images(self) -> None:
        state = loopback.ServerState("key")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "appdata")}):
            daemon._activate_server_coordination(state, "new-generation")

        self.assertIs(state.lifecycle.image_path_of, orphan_guard.full_image_path_of)


if __name__ == "__main__":
    unittest.main()
