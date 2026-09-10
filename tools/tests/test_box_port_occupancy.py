"""fb-20260904-114520-6927: the box is occupied by whoever holds a game port.

The name probe (``DayZDiag_x64.exe`` by image) read ports from argv, so a
server with another image, or one argv did not describe, held 2302 while the
box read as free and ``dayz_test_run`` launched on top of it. The socket table
is now a second witness: a DayZ image holding a UDP port is foreign even
without a run record, ports come from the OS, an unreadable table is an
occupied box, and ``start_run`` refuses to launch onto a socket we do not own.
"""
from __future__ import annotations

from tests.steam_helpers import FakeSteamGate

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
    _BoxProbes,
    _BoxSnapshot,
    _derive_box,
    _requested_port,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.test_box_occupancy import _argv_lookup
from tests.test_process_lifecycle import (
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)

DIAG_EMPTY = {"known": True, "processes": []}


def _holders(*rows: tuple[int, int | None, str | None]) -> dict[str, object]:
    return {
        "known": True,
        "holders": [{"port": port, "pid": pid, "name": name} for port, pid, name in rows],
    }


class PortOccupancyTest(unittest.TestCase):
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
        self.argv: dict[int, list[str]] = {}
        self.port_holders: dict[str, object] = _holders()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: DIAG_EMPTY,
            diag_probe=lambda: DIAG_EMPTY,
            port_probe=lambda: self.port_holders,
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            argv_of=_argv_lookup(self.argv),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, port: int | None = 2302) -> dict[str, object]:
        argv = [str(self.game / "DayZDiag_x64.exe"), "-mission=test"]
        if port is not None:
            argv.append(f"-port={port}")
        return {
            "argv": argv,
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
        }

    def add_run(self, record, *, owner: str | None = "A", state: str = "RUNNING") -> RunRecord:
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

    def rejected_reasons(self) -> list[object]:
        return [
            event.get("reason")
            for event in self.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]

    # --- box_occupancy -------------------------------------------------------

    def test_foreign_dayz_server_on_a_game_port_occupies_the_box(self) -> None:
        # The ficha: a 6-minute-old server, not ours, no run record, and the
        # name probe empty. Its image is not DayZDiag, so only the socket
        # table can see it.
        self.port_holders = _holders(
            (2302, 777, "DayZServer_x64.exe"), (2304, 777, "DayZServer_x64.exe")
        )

        box = self.lifecycle.box_occupancy()

        self.assertTrue(box["occupied"])
        self.assertEqual(box["runs"], [])
        self.assertEqual(
            box["foreign"],
            [
                {
                    "port": 2302,
                    "mods": [],
                    "profiles": None,
                    "image": "DayZServer_x64.exe",
                    "source": "port",
                }
            ],
        )
        self.assertEqual(box["ports_in_use"], [2302, 2304])
        self.assertTrue(box["scan_known"])
        self.assertTrue(box["port_scan_known"])
        self.assertNotIn("pid", json.dumps(box))

    def test_unrelated_services_never_touch_the_box(self) -> None:
        self.port_holders = _holders((53, 4032, "svchost.exe"), (500, 6084, "svchost.exe"))

        box = self.lifecycle.box_occupancy()

        self.assertFalse(box["occupied"])
        self.assertEqual(box["foreign"], [])
        self.assertEqual(box["ports_in_use"], [])

    def test_registered_run_ports_come_from_the_socket_table(self) -> None:
        # argv says nothing about ports; the OS does. A registered pid is never foreign.
        self.add_run(process(4242))
        self.argv[4242] = ["DayZDiag_x64.exe", "-server"]
        self.port_holders = _holders((2402, 4242, "DayZDiag_x64.exe"), (2404, 4242, "DayZDiag_x64.exe"))

        box = self.lifecycle.box_occupancy()

        self.assertTrue(box["occupied"])
        self.assertEqual([row["run_id"] for row in box["runs"]], ["run-existing"])
        self.assertEqual(box["foreign"], [])
        self.assertEqual(box["ports_in_use"], [2402, 2404])
        self.assertEqual(box["foreign_ports"], [])

    def test_diag_and_port_witnesses_of_one_process_make_one_row(self) -> None:
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": 909, "name": "DayZDiag_x64.exe"}],
        }
        self.argv[909] = ["DayZDiag_x64.exe", "-server", "-port=2402", "-mod=@Foreign"]
        self.port_holders = _holders((2402, 909, "DayZDiag_x64.exe"), (2404, 909, "DayZDiag_x64.exe"))

        box = self.lifecycle.box_occupancy()

        self.assertEqual(len(box["foreign"]), 1)
        self.assertEqual(box["foreign"][0]["port"], 2402)
        self.assertEqual(box["foreign"][0]["mods"], ["@Foreign"])
        self.assertNotIn("source", box["foreign"][0])
        self.assertEqual(box["ports_in_use"], [2402, 2404])

    def test_unreadable_socket_table_is_an_occupied_box(self) -> None:
        def boom() -> dict[str, object]:
            raise OSError("netstat unavailable")

        for probe in (boom, lambda: {"known": False, "holders": []}, lambda: {"known": True, "holders": "x"}):
            with self.subTest(probe):
                self.lifecycle.port_probe = probe
                self.lifecycle._invalidate_box_cache()
                box = self.lifecycle.box_occupancy()
                self.assertTrue(box["occupied"])
                self.assertTrue(box["scan_known"])
                self.assertFalse(box["port_scan_known"])
                self.assertEqual(box["foreign"], [])

    def test_no_port_probe_keeps_the_legacy_behaviour(self) -> None:
        self.lifecycle.port_probe = None

        box = self.lifecycle.box_occupancy()

        self.assertFalse(box["occupied"])
        self.assertTrue(box["port_scan_known"])
        self.assertEqual(box["foreign"], [])

    def test_derive_box_treats_an_unknown_port_scan_as_occupied(self) -> None:
        snapshot = _BoxSnapshot(
            clock=100.0, runs=(), activity={}, unknown=frozenset(), revision=1
        )
        probes = _BoxProbes(foreign=(), ports_in_use=(), scan_known=True, port_scan_known=False)
        box = _derive_box(snapshot, probes)
        self.assertTrue(box["occupied"])
        self.assertFalse(box["port_scan_known"])
        legacy = _BoxProbes(foreign=(), ports_in_use=(), scan_known=True)
        self.assertFalse(_derive_box(snapshot, legacy)["occupied"])

    # --- start_run -----------------------------------------------------------

    def test_start_refuses_the_requested_port_held_by_a_process_that_is_not_ours(self) -> None:
        # Any image: what matters is that we would bind on top of it.
        self.port_holders = _holders((2302, 31337, "python.exe"))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertEqual(result.get("error"), "active_run_exists")
        self.assertEqual(self.rejected_reasons()[-1], "port_in_use_foreign")
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.store.list_runs(), [])

    def test_start_refuses_while_a_foreign_dayz_image_holds_any_port(self) -> None:
        self.port_holders = _holders((2402, 777, "DayZServer_x64.exe"))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertEqual(result.get("error"), "active_run_exists")
        self.assertEqual(self.rejected_reasons()[-1], "port_in_use_foreign")
        self.assertEqual(self.launcher.calls, [])

    def test_start_refuses_an_unattributable_holder_of_the_requested_port(self) -> None:
        self.port_holders = _holders((2302, None, None))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertEqual(result.get("error"), "active_run_exists")
        self.assertEqual(self.rejected_reasons()[-1], "port_in_use_foreign")

    def test_start_proceeds_when_holders_are_unrelated(self) -> None:
        self.port_holders = _holders((53, 4032, "svchost.exe"), (2402, 5555, "python.exe"))

        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(self.launcher.calls), 1)
        self.assertEqual(self.rejected_reasons(), [])

    def test_start_uses_a_fresh_probe_not_the_box_cache(self) -> None:
        # A reader filled the 1.5 s cache while the port was free; the server
        # appeared right after. The launch must see it.
        self.assertFalse(self.lifecycle.box_occupancy()["occupied"])
        self.port_holders = _holders((2302, 777, "DayZServer_x64.exe"))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertEqual(result.get("error"), "active_run_exists")
        self.assertEqual(self.rejected_reasons()[-1], "port_in_use_foreign")

    def test_start_is_refused_when_the_socket_table_cannot_be_read(self) -> None:
        def boom() -> dict[str, object]:
            raise OSError("netstat unavailable")

        self.lifecycle.port_probe = boom

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertEqual(result.get("error"), "active_run_exists")
        self.assertEqual(self.rejected_reasons()[-1], "port_scan_unknown")
        self.assertEqual(self.launcher.calls, [])

    def test_start_without_a_port_probe_is_unchanged(self) -> None:
        self.lifecycle.port_probe = None

        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(self.launcher.calls), 1)

    def test_requested_port_parsing(self) -> None:
        self.assertEqual(_requested_port(["x.exe", "-port=2402"]), 2402)
        self.assertEqual(_requested_port(["x.exe", "-port", "2302"]), 2302)
        self.assertIsNone(_requested_port(["x.exe", "-mission=test"]))
        self.assertIsNone(_requested_port(None))


# --- round 2 (Codex review B-01, RACE H-01, ADMIN H-02/H-03/H-04, LOSS M-2/M-3) ------------------

class PortOccupancyRound2Test(PortOccupancyTest):
    """Same fixture; the cases the first audit round asked for."""

    def test_holder_appearing_before_the_launch_is_caught_by_the_pre_launch_read(self) -> None:
        # The admission probe saw a free table; the holder appears while the
        # instance is being prepared (audit, reserve, persist happen in between).
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        original_prepare = self.lifecycle._prepare_instance

        def prepare_then_holder(*args, **kwargs):
            self.port_holders = _holders((2302, 31337, "python.exe"))
            return original_prepare(*args, **kwargs)

        self.lifecycle._prepare_instance = prepare_then_holder

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.rejected_reasons()[-1], "port_in_use_foreign")
        stages = [
            event.get("stage")
            for event in self.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]
        self.assertIn("pre_launch", stages)
        # The provisional run does not survive as an active run.
        self.assertFalse(self.lifecycle.box_occupancy()["runs"])

    def test_pre_launch_rejection_publishes_an_audit_sink_failure(self) -> None:
        # REVIEW-R2 backlog: the refusal stands and the lost audit row is visible.
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        original_prepare = self.lifecycle._prepare_instance

        def prepare_then_holder(*args, **kwargs):
            self.port_holders = _holders((2302, 31337, "python.exe"))
            self.audit.fail_events.add("lifecycle_start_rejected")
            return original_prepare(*args, **kwargs)

        self.lifecycle._prepare_instance = prepare_then_holder

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertIn("audit_failed", result.get("cleanup_degraded", []), result)
        self.assertEqual(self.launcher.calls, [])
        self.assertFalse(self.lifecycle.box_occupancy()["runs"])

    def test_nameless_holder_is_attribution_unknown_and_blocks(self) -> None:
        # ADMIN H-04: a pid the process table could not name might be a DayZ
        # image on another port; nothing can clear it as harmless.
        self.port_holders = _holders((2402, 777, None))

        box = self.lifecycle.box_occupancy()
        self.assertTrue(box["occupied"])
        self.assertFalse(box["port_scan_known"])
        self.assertEqual(box["port_scan_reason"], "port_attribution_unknown")

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))
        self.assertEqual(result.get("error"), "active_run_exists")
        self.assertEqual(self.rejected_reasons()[-1], "port_attribution_unknown")
        self.assertEqual(self.launcher.calls, [])

    def test_named_non_dayz_holders_never_trip_attribution(self) -> None:
        self.port_holders = _holders((53, 4032, "svchost.exe"), (2402, 5555, "python.exe"))
        box = self.lifecycle.box_occupancy()
        self.assertFalse(box["occupied"])
        self.assertTrue(box["port_scan_known"])
        self.assertIsNone(box["port_scan_reason"])
        # A non-DayZ holder of a DayZ-range port is visible in ports_in_use so a
        # caller can pick another port, without occupying the box.
        self.assertEqual(box["ports_in_use"], [2402])
        # foreign_ports is the socket table minus managed runs, any image, any port.
        self.assertEqual(box["foreign_ports"], [53, 2402])

    def test_unknown_scan_carries_its_reason(self) -> None:
        self.lifecycle.port_probe = lambda: {"known": False, "holders": []}
        box = self.lifecycle.box_occupancy()
        self.assertFalse(box["port_scan_known"])
        self.assertEqual(box["port_scan_reason"], "port_scan_unknown")

    def test_wait_hint_names_an_unreadable_table(self) -> None:
        from dayz_mcp.process_lifecycle import occupancy_error_fields

        fields = occupancy_error_fields(
            {"occupied": True, "runs": [], "foreign": [], "ports_in_use": [], "port_scan_known": False}
        )
        self.assertIn("port_scan_unknown", fields["hint"])
        self.assertIn("waiting does not help", fields["hint"])

    def test_admin_empty_gate_needs_the_socket_table_too(self) -> None:
        # ADMIN H-02: "empty" must mean no DayZ image holds a UDP port either.
        self.port_holders = _holders((2402, 777, "DayZServer_x64.exe"))
        self.assertEqual(self.lifecycle._diag_snapshot_empty(), (False, "manual_cleanup_required"))
        self.lifecycle.port_probe = lambda: {"known": False, "holders": []}
        self.assertEqual(self.lifecycle._diag_snapshot_empty(), (False, "port_scan_unknown"))
        self.lifecycle.port_probe = lambda: _holders((2402, 777, None))
        self.assertEqual(self.lifecycle._diag_snapshot_empty(), (False, "port_attribution_unknown"))
        self.lifecycle.port_probe = lambda: _holders((53, 4032, "svchost.exe"))
        self.assertEqual(self.lifecycle._diag_snapshot_empty(), (True, ""))
        self.lifecycle.port_probe = None
        self.assertEqual(self.lifecycle._diag_snapshot_empty(), (True, ""))


if __name__ == "__main__":
    unittest.main()
