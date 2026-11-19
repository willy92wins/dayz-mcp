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
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    IDENTITY_A,
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
        # Default foreign_ports is DayZ-related only; full table is foreign_ports_all.
        self.assertEqual(box["foreign_ports"], [2402])
        self.assertEqual(box["foreign_ports_all"], [53, 2402])
        self.assertEqual(
            box["foreign_ports_meta"],
            {
                "count": 1,
                "count_all": 2,
                "dayz_related": 1,
                "kind": "os_socket_table_ignored_for_occupancy",
            },
        )
        self.assertEqual(
            box["foreign_ports_meta"]["count"], len(box["foreign_ports"])
        )
        self.assertEqual(
            box["foreign_ports_meta"]["count_all"], len(box["foreign_ports_all"])
        )
        self.assertFalse(box["occupied"])

    def test_foreign_ports_default_is_dayz_related_only(self) -> None:
        self.port_holders = _holders(
            (53, 4032, "svchost.exe"),
            (2402, 5555, "python.exe"),
            (500, 6084, "ike.exe"),
        )
        box = self.lifecycle.box_occupancy()
        self.assertEqual(box["foreign_ports"], [2402])
        self.assertEqual(box["foreign_ports_all"], [53, 500, 2402])
        self.assertEqual(box["foreign_ports_meta"]["count"], 1)
        self.assertEqual(
            box["foreign_ports_meta"]["count_all"], len(box["foreign_ports_all"])
        )
        self.assertEqual(box["foreign_ports_meta"]["dayz_related"], 1)
        self.assertFalse(box["occupied"])
        self.assertTrue(box["port_scan_known"])

    def test_foreign_ports_dayz_image_outside_range_still_in_default_list(self) -> None:
        # DayZ image on a non-2302-2999 port still counts as dayz_related.
        self.port_holders = _holders(
            (53, 4032, "svchost.exe"),
            (1234, 888, "DayZServer_x64.exe"),
        )
        box = self.lifecycle.box_occupancy()
        self.assertEqual(box["foreign_ports"], [1234])
        self.assertEqual(box["foreign_ports_all"], [53, 1234])
        self.assertEqual(box["foreign_ports_meta"]["count"], 1)
        self.assertEqual(box["foreign_ports_meta"]["count_all"], 2)
        self.assertEqual(box["foreign_ports_meta"]["dayz_related"], 1)
        # DayZ image occupies the box even outside the game port band.
        self.assertTrue(box["occupied"])

    def test_foreign_ports_dayz_image_outside_range_annotates_coherent(self) -> None:
        # Capture → annotate: DayZServer_x64.exe:1234 must stay dayz_relevant
        # so meta dayz_relevant matches dayz_related (fb-1432 B1).
        from dayz_mcp import server

        self.port_holders = _holders(
            (53, 4032, "svchost.exe"),
            (1234, 888, "DayZServer_x64.exe"),
        )
        box = dict(self.lifecycle.box_occupancy())
        annotated = server._annotate_box_foreign_ports(box)
        self.assertEqual(
            annotated["foreign_ports"],
            [{"port": 1234, "dayz_relevant": True}],
        )
        self.assertEqual(
            annotated["foreign_ports_all"],
            [
                {"port": 53, "dayz_relevant": False},
                {"port": 1234, "dayz_relevant": True},
            ],
        )
        self.assertEqual(annotated["foreign_ports_meta"]["dayz_related"], 1)
        self.assertEqual(annotated["foreign_ports_meta"]["dayz_relevant"], 1)
        self.assertEqual(annotated["foreign_ports_meta"]["count"], 1)
        self.assertEqual(annotated["foreign_ports_meta"]["count_all"], 2)

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


# --- inbox d3b7: a crashed client's CrashReporter.exe keeps its UDP port -------------------------

class CrashReporterHeldPortD3b7Test(PortOccupancyTest):
    """fb-20261118-192120-d3b7: a dead client's CrashReporter.exe keeps its port.

    An unmanaged DayZ client crashes and its CrashReporter.exe child keeps the
    client's UDP socket. The socket table then names the dead client's pid with
    no image; the child probe row whose ppid is that pid is the only witness of
    who holds it. Same fixture as PortOccupancyTest, plus that probe. The box
    stays occupied (fail-closed) and names the reporter.
    """

    DEAD_CLIENT = 69416
    REPORTER = 37864
    PORT = 2316
    GONE = {"error": "process_not_found", "exit_code": 4}

    def reporter_row(
        self, ppid: int, *, pid: int | None = None, name: str = "CrashReporter.exe"
    ) -> dict[str, object]:
        return {"pid": self.REPORTER if pid is None else pid, "ppid": ppid, "name": name}

    def leave_reporter_holding_the_port(self) -> None:
        """The d3b7 host: client 69416 is gone, its CrashReporter.exe 37864 lives on."""
        from dayz_mcp.native_process_guard import identity_hashes

        reporter = self.game / "CrashReporter.exe"
        reporter.write_bytes(b"")
        self.port_holders = _holders((self.PORT, self.DEAD_CLIENT, None))
        self.guard.snapshots[self.DEAD_CLIENT] = dict(self.GONE)
        self.guard.snapshots[self.REPORTER] = {
            "pid": self.REPORTER,
            "creation_time_utc": "2026-07-15T00:01:00.0000000Z",
            "executable_sha256": identity_hashes(str(reporter), [str(reporter)])[
                "executable_sha256"
            ],
            "command_line_sha256": "c" * 64,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }
        self.lifecycle.child_probe = lambda: {
            "known": True,
            "processes": [self.reporter_row(self.DEAD_CLIENT)],
        }

    def assert_no_pid(self, published: object) -> None:
        """No pid or ppid, by name or by value, in what the box and its callers publish.

        The box publishes no pids (test_box_occupancy.py:275 and :166 of this file);
        naming CrashReporter.exe and the port is enough to close its window.
        """
        text = published if isinstance(published, str) else json.dumps(published)
        words = "".join(ch if ch.isalnum() else " " for ch in text.casefold()).split()
        self.assertNotIn("pid", words, text)
        self.assertNotIn("ppid", words, text)
        for pid in (self.DEAD_CLIENT, self.REPORTER):
            self.assertNotIn(str(pid), text)

    @staticmethod
    def names_crash_reporter(text: object) -> bool:
        """CrashReporter.exe, crash_reporter or Crash Reporter, in any case."""
        if not isinstance(text, str):
            return False
        return "crashreporter" in "".join(ch for ch in text.casefold() if ch.isalpha())

    def assert_attribution_unknown_and_blocked(self) -> None:
        """Every surface's answer for a nameless holder nobody vouches for."""
        self.lifecycle._invalidate_box_cache()
        box = self.lifecycle.box_occupancy()
        self.assertTrue(box["occupied"])
        self.assertFalse(box["port_scan_known"])
        self.assertEqual(box["port_scan_reason"], "port_attribution_unknown")
        self.assertEqual(box["foreign"], [])
        self.assertEqual(
            self.lifecycle._foreign_port_reason(set(), 2302), "port_attribution_unknown"
        )
        self.assertEqual(
            self.lifecycle._diag_snapshot_empty(), (False, "port_attribution_unknown")
        )
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))
        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.rejected_reasons()[-1], "port_attribution_unknown")
        self.assertEqual(self.launcher.calls, [])

    # --- positive: red on e36a19f by design -----------------------------------------------------

    def test_d3b7_box_names_the_crash_reporter_holding_a_dead_clients_port(self) -> None:
        # Guards the box: the nameless holder of 2316 is attributed to the live
        # CrashReporter.exe whose ppid it is; the box stays occupied, with a known
        # scan and one foreign row that names the image and the port, and the box
        # publishes no pid. RED on e36a19f by design: _collect_probes never reads
        # child_probe, so the scan is port_attribution_unknown, foreign empty.
        self.leave_reporter_holding_the_port()

        box = self.lifecycle.box_occupancy()

        # No pid anywhere in the box, the reporter's row included.
        self.assertNotIn("pid", json.dumps(box))
        self.assert_no_pid(box)
        self.assertTrue(box["occupied"])
        self.assertTrue(box["scan_known"])
        self.assertTrue(box["port_scan_known"], box["port_scan_reason"])
        self.assertIsNone(box["port_scan_reason"])
        self.assertEqual(box["runs"], [])
        self.assertEqual(len(box["foreign"]), 1, box["foreign"])
        row = box["foreign"][0]
        # The socket-table row shape.
        self.assertLessEqual({"port", "mods", "profiles", "image", "source"}, set(row), row)
        self.assertEqual(row["image"], "CrashReporter.exe")
        self.assertEqual(row["port"], self.PORT)
        self.assertEqual(row["mods"], [])
        self.assertIsNone(row["profiles"])

    def test_d3b7_session_status_blocked_on_names_the_crash_reporter(self) -> None:
        # Guards session_status: blocked_on names the Crash Reporter window and
        # its remedy instead of sending the reader to the socket table, and
        # available_for stays closed. RED on e36a19f by design: blocked_on is the
        # port_attribution_unknown "restore the daemon's view" text.
        from dayz_mcp import server

        self.leave_reporter_holding_the_port()
        # As server.session_status builds it, with no lease holder (the ticket).
        box = dict(self.lifecycle.box_occupancy())
        server._annotate_box_foreign_ports(box)
        box["available_for"] = server.box_available_for(box, None)
        text = server._session_status_blocked_on({"owner": None, "box": box}, None)

        self.assertEqual(box["available_for"], {"new_launch": False, "adopt": False})
        self.assertIsInstance(text, str)
        self.assertTrue(self.names_crash_reporter(text), text)
        self.assertIn("close", text.casefold())
        self.assertNotIn("restore the daemon's view", text)
        self.assertNotIn("port_attribution_unknown", text)
        self.assert_no_pid(text)

    def test_d3b7_start_is_refused_with_a_reason_that_names_the_reporter(self) -> None:
        # Guards start_run and _foreign_port_reason: the launch is still refused
        # and nothing is launched, but the reason names the reporter instead of
        # port_attribution_unknown, and the admission audit carries that reason.
        # RED on e36a19f by design: the reason is port_attribution_unknown (the
        # refusal itself already holds there).
        self.leave_reporter_holding_the_port()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))

        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.store.list_runs(), [])
        for requested_port in (2302, None):
            with self.subTest(requested_port=requested_port):
                reason = self.lifecycle._foreign_port_reason(set(), requested_port)
                self.assertIsInstance(reason, str)
                self.assertNotEqual(reason, "port_attribution_unknown")
                self.assertTrue(self.names_crash_reporter(reason), reason)
                self.assert_no_pid(reason)
        self.assertEqual(
            self.rejected_reasons()[-1], self.lifecycle._foreign_port_reason(set(), 2302)
        )

    def test_d3b7_admin_empty_gate_still_refuses_and_names_the_reporter(self) -> None:
        # Guards _diag_snapshot_empty (the admin "empty" reconcile gate): the same
        # holder still refuses, with a reason that names the reporter. RED on
        # e36a19f by design: the reason is port_attribution_unknown.
        self.leave_reporter_holding_the_port()

        empty, reason = self.lifecycle._diag_snapshot_empty()

        self.assertFalse(empty)
        self.assertNotEqual(reason, "port_attribution_unknown")
        self.assertTrue(self.names_crash_reporter(reason), reason)
        self.assert_no_pid(reason)

    # --- negative: green on e36a19f by design, and after the fix --------------------------------

    def test_d3b7_nameless_holder_without_a_crash_reporter_child_stays_unknown(self) -> None:
        # Guards "no CrashReporter": no child row, a child of another image, or no
        # child probe wired (an empty, known scan) names nothing, so the holder
        # stays port_attribution_unknown and blocks. Green on e36a19f by design;
        # must stay green after the fix.
        self.leave_reporter_holding_the_port()
        probes = {
            "no_child_rows": lambda: {"known": True, "processes": []},
            "child_of_another_image": lambda: {
                "known": True,
                "processes": [self.reporter_row(self.DEAD_CLIENT, name="WerFault.exe")],
            },
            "child_probe_not_wired": None,
        }
        for label, probe in probes.items():
            with self.subTest(label):
                self.lifecycle.child_probe = probe
                self.assert_attribution_unknown_and_blocked()

    def test_d3b7_crash_reporter_of_another_parent_leaves_the_holder_unknown(self) -> None:
        # Guards "CrashReporter of another ppid": a live CrashReporter.exe whose
        # ppid is not the holder's pid does not vouch for it. Green on e36a19f by
        # design; must stay green after the fix.
        self.leave_reporter_holding_the_port()
        self.lifecycle.child_probe = lambda: {
            "known": True,
            "processes": [self.reporter_row(51036)],
        }

        self.assert_attribution_unknown_and_blocked()

    def test_d3b7_reporter_of_one_nameless_holder_does_not_name_another(self) -> None:
        # Guards "another ppid" per holder: a reporter whose parent holds another
        # nameless port leaves 69416 unattributed, so the scan stays unknown and
        # 2316 gets no row. Which holder a refusal names first is not pinned.
        # Green on e36a19f by design; must stay green after the fix.
        self.leave_reporter_holding_the_port()
        self.port_holders = _holders((self.PORT, self.DEAD_CLIENT, None), (2402, 777, None))
        self.guard.snapshots[777] = dict(self.GONE)
        self.lifecycle.child_probe = lambda: {
            "known": True,
            "processes": [self.reporter_row(777)],
        }

        box = self.lifecycle.box_occupancy()

        self.assertTrue(box["occupied"])
        self.assertFalse(box["port_scan_known"])
        self.assertEqual(box["port_scan_reason"], "port_attribution_unknown")
        self.assertNotIn(self.PORT, [row.get("port") for row in box["foreign"]])
        self.assertFalse(self.lifecycle._diag_snapshot_empty()[0])
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request(port=2302))
        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.launcher.calls, [])

    def test_d3b7_unknown_child_scan_keeps_the_holder_unknown(self) -> None:
        # Guards "child probe unknown", as _crash_reporter_probe_rows models it: a
        # probe that raises, answers known=False, or lists a row it cannot vouch
        # for is no witness, even beside a matching row. Green on e36a19f by
        # design; must stay green after the fix.
        self.leave_reporter_holding_the_port()
        matching = self.reporter_row(self.DEAD_CLIENT)

        def toolhelp_fails() -> dict[str, object]:
            raise OSError("toolhelp snapshot failed")

        probes = {
            "probe_raises": toolhelp_fails,
            "known_false": lambda: {"known": False, "processes": [matching]},
            "malformed_row": lambda: {
                "known": True,
                "processes": [matching, {"pid": 37865, "name": "CrashReporter.exe"}],
            },
        }
        for label, probe in probes.items():
            with self.subTest(label):
                self.lifecycle.child_probe = probe
                self.assert_attribution_unknown_and_blocked()

    # --- managed run: green on e36a19f by design ------------------------------------------------

    def test_d3b7_managed_run_keeps_the_port_of_its_registered_reporter(self) -> None:
        # Guards the managed path (inbox 7417): the reaper registers the reporter
        # on the run, so the dead client's socket stays the run's: no foreign row,
        # the scan known, and the start gate sees no foreign holder. Green on
        # e36a19f by design; the run check must stay ahead of any crash-reporter
        # attribution.
        self.lifecycle.parent_anchor = (lambda pid: object(), lambda token: None)
        client = process(self.DEAD_CLIENT, "client")
        run = self.add_run(client)
        # Housekeeping anchors the live client before it crashes.
        self.guard.snapshots[client.pid] = snapshot(client)
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        self.leave_reporter_holding_the_port()

        self.assertEqual(self.lifecycle.reap_dead_runs(), [])

        stored = self.store.get(run.run_id)
        self.assertEqual(
            [(record.pid, record.role) for record in stored.processes],
            [(self.DEAD_CLIENT, "client"), (self.REPORTER, "crash_reporter")],
        )
        box = self.lifecycle.box_occupancy()
        self.assertTrue(box["occupied"])
        self.assertEqual([row["run_id"] for row in box["runs"]], [run.run_id])
        self.assertEqual(box["foreign"], [])
        self.assertTrue(box["port_scan_known"])
        self.assertIsNone(box["port_scan_reason"])
        self.assertEqual(box["ports_in_use"], [self.PORT])
        self.assertEqual(box["foreign_ports"], [])
        self.assertIsNone(
            self.lifecycle._foreign_port_reason({self.DEAD_CLIENT, self.REPORTER}, None)
        )


if __name__ == "__main__":
    unittest.main()
