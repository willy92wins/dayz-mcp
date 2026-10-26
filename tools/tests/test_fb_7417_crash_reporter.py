from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.steam_helpers import FakeSteamGate


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import orphan_guard, window_close
from dayz_mcp.native_process_guard import identity_hashes
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from dayz_mcp.window_close import Win32WindowFns
from tests.lifecycle_helpers import stamp_launcher
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    IDENTITY_A,
    process,
    snapshot,
)


RUN_ID = "run-existing"
GONE = {"error": "process_not_found", "exit_code": 4}


@dataclass
class FakeWin32:
    windows: list[tuple[int, int, bool]] = field(default_factory=list)
    posts: list[tuple[int, int, int, int]] = field(default_factory=list)

    def as_fns(self) -> Win32WindowFns:
        def enum_windows(callback):
            for hwnd, _pid, _visible in list(self.windows):
                if not callback(hwnd):
                    return False
            return True

        def window_pid(hwnd: int) -> int:
            for handle, pid, _visible in self.windows:
                if handle == hwnd:
                    return pid
            return 0

        def is_visible(hwnd: int) -> bool:
            for handle, _pid, visible in self.windows:
                if handle == hwnd:
                    return visible
            return False

        def post_message(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
            self.posts.append((hwnd, msg, wparam, lparam))
            return True

        return Win32WindowFns(
            enum_windows=enum_windows,
            window_pid=window_pid,
            is_visible=is_visible,
            post_message=post_message,
        )


class CrashReporter7417Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in (
            "DayZDiag_x64.exe",
            "DayZ_BE.exe",
            "DayZ_x64.exe",
            "DayZServer_x64.exe",
            "CrashReporter.exe",
        ):
            (self.game / name).write_bytes(b"")
        reporter = str(self.game / "CrashReporter.exe")
        self.reporter_hash = identity_hashes(reporter, [reporter])["executable_sha256"]
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
        self.child_rows: list[dict[str, object]] = []
        self.child_probe = lambda: {"known": True, "processes": list(self.child_rows)}
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
            child_probe=self.child_probe,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(self, *records: ProcessRecord, owner: str | None = "A", state: str = "RUNNING") -> RunRecord:
        run = RunRecord(
            RUN_ID,
            owner,
            "lease-A" if owner else None,
            state,
            "same",
            "@SameMod",
            "profiles",
            "mission",
            list(records),
        )
        self.store.add(run)
        stamp_launcher(self.lifecycle, run.run_id, IDENTITY_A)
        return run

    def reporter_snapshot(self, pid: int, created: str) -> dict[str, object]:
        return {
            "pid": pid,
            "creation_time_utc": created,
            "executable_sha256": self.reporter_hash,
            "command_line_sha256": "c" * 64,
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }

    def reporter_row(self, pid: int, ppid: int, name: str = "CrashReporter.exe") -> dict[str, object]:
        return {"pid": pid, "ppid": ppid, "name": name}

    def test_t1_process_not_found_terminate_is_gone(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.terminate_results = [dict(GONE)]
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("state"), "EXITED")
        self.assertEqual(result.get("terminated"), 0)
        self.assertEqual(result.get("stop_method"), "no_live_owned")
        stored = self.store.get(RUN_ID)
        self.assertEqual((stored.state, stored.processes), ("EXITED", []))

    def test_t1_termination_unconfirmed_stays_partial(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.terminate_results = [
            {"terminated": False, "error": "termination_unconfirmed"}
        ]
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(result.get("error"), "partial_cleanup")
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "UNRECONCILED")

    def test_t2_incident_reapers_keep_reporters(self) -> None:
        server = process(57116, "server")
        client = process(53904, "client")
        self.add_run(server, client)
        self.guard.snapshots[server.pid] = dict(GONE)
        self.guard.snapshots[client.pid] = dict(GONE)
        rep_server_created = "2026-07-15T00:01:00.0000000Z"
        rep_client_created = "2026-07-15T00:01:01.0000000Z"
        self.guard.snapshots[40784] = self.reporter_snapshot(40784, rep_server_created)
        self.guard.snapshots[51036] = self.reporter_snapshot(51036, rep_client_created)
        self.child_rows = [
            self.reporter_row(40784, server.pid),
            self.reporter_row(51036, client.pid),
        ]
        writes_before = self.store.paths.runs_path.read_bytes()
        reaped = self.lifecycle.reap_dead_runs()
        self.assertEqual(reaped, [])
        stored = self.store.get(RUN_ID)
        reporters = [item for item in stored.processes if item.role == "crash_reporter"]
        self.assertEqual(sorted(item.pid for item in reporters), [40784, 51036])
        self.assertNotEqual(self.store.paths.runs_path.read_bytes(), writes_before)
        registered = [
            event
            for event in self.audit.events
            if event.get("event") == "crash_reporter_registered"
        ]
        self.assertEqual(len(registered), 2)
        self.assertEqual(
            sorted((event.get("pid"), event.get("ppid")) for event in registered),
            [(40784, server.pid), (51036, client.pid)],
        )
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(adopted.get("ok"), True)
        self.guard.terminate_calls.clear()
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("ok"), True)
        self.assertEqual(stopped.get("state"), "EXITED")
        self.assertEqual(stopped.get("terminated"), 2)
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls],
            [40784, 51036],
        )

    def test_t3_negative_rows_are_not_registered(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.guard.snapshots[server.pid] = dict(GONE)
        # The unknown_ppid row names pid 1; reading it as gone leaves the
        # ppid gate as the only one that refuses that row.
        self.guard.snapshots[1] = dict(GONE)
        later = "2026-07-15T00:01:00.0000000Z"
        earlier = "2026-07-15T00:00:00.0000000Z"
        cases = [
            ("other_name", self.reporter_row(40784, server.pid, "notepad.exe"), later, self.reporter_hash, True),
            ("unknown_ppid", self.reporter_row(40784, 1), later, self.reporter_hash, True),
            ("reused_parent", self.reporter_row(40784, server.pid), later, self.reporter_hash, True),
            ("other_hash", self.reporter_row(40784, server.pid), later, "d" * 64, True),
            ("before_parent", self.reporter_row(40784, server.pid), earlier, self.reporter_hash, True),
            ("incomplete", self.reporter_row(40784, server.pid), later, self.reporter_hash, False),
        ]
        for label, row, created, exe_hash, complete in cases:
            with self.subTest(label=label):
                run = self.store.get(RUN_ID)
                run.state = "RUNNING"
                run.owner_session_id = "A"
                run.owner_lease_id = "lease-A"
                run.processes = [server]
                self.store.replace(run)
                self.child_rows = [row]
                child_snap = self.reporter_snapshot(40784, created)
                child_snap["executable_sha256"] = exe_hash
                child_snap["identity_complete"] = complete
                self.guard.snapshots[40784] = child_snap
                if label == "reused_parent":
                    self.guard.snapshots[server.pid] = {
                        "pid": server.pid,
                        "creation_time_utc": "2099-01-01T00:00:00.0000000Z",
                        "executable_sha256": "e" * 64,
                        "command_line_sha256": "f" * 64,
                        "identity_scheme": "psutil-argv-v2",
                        "identity_complete": True,
                    }
                else:
                    self.guard.snapshots[server.pid] = dict(GONE)
                self.guard.terminate_calls.clear()
                self.lifecycle.reap_dead_runs()
                stored = self.store.get(RUN_ID)
                roles = [item.role for item in stored.processes]
                self.assertNotIn("crash_reporter", roles)
                self.assertEqual(self.guard.terminate_calls, [])

    def test_t3_unknown_probe_blocks_reap_and_stop(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.guard.snapshots[server.pid] = dict(GONE)

        def boom() -> dict[str, object]:
            raise RuntimeError("toolhelp failed")

        self.lifecycle.child_probe = boom
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stored = self.store.get(RUN_ID)
        self.assertNotEqual(stored.state, "EXITED")
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "child_scan_unknown")
        self.assertEqual(self.guard.terminate_calls, [])

        self.lifecycle.child_probe = lambda: {"known": False, "processes": []}
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "child_scan_unknown")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_t2_reporter_whose_record_is_not_written_blocks_the_reap(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.guard.snapshots[server.pid] = dict(GONE)
        self.guard.snapshots[40784] = self.reporter_snapshot(
            40784, "2026-07-15T00:01:00.0000000Z"
        )
        self.child_rows = [self.reporter_row(40784, server.pid)]
        replace = self.store.replace
        writes = {"n": 0}

        def first_write_fails(run: RunRecord) -> None:
            writes["n"] += 1
            if writes["n"] == 1:
                raise OSError("disk full")
            replace(run)

        with patch.object(self.store, "replace", first_write_fails):
            self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "RUNNING")
        self.assertEqual([item.role for item in stored.processes], ["server"])
        self.assertEqual(writes["n"], 1)

    def test_t4_reporter_born_during_stop_is_killed(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.guard.snapshots[server.pid] = snapshot(server)
        created = "2026-07-15T00:01:00.0000000Z"
        self.guard.snapshots[40784] = self.reporter_snapshot(40784, created)
        scans = {"n": 0}

        def probe() -> dict[str, object]:
            scans["n"] += 1
            if scans["n"] < 2:
                return {"known": True, "processes": []}
            return {
                "known": True,
                "processes": [self.reporter_row(40784, server.pid)],
            }

        self.lifecycle.child_probe = probe
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("ok"), True)
        self.assertEqual(stopped.get("state"), "EXITED")
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls],
            [server.pid, 40784],
        )
        self.assertEqual(stopped.get("terminated"), 2)

    def test_t4_late_reporter_whose_kill_fails_stays_on_the_run(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.guard.snapshots[server.pid] = snapshot(server)
        created = "2026-07-15T00:01:00.0000000Z"
        self.guard.snapshots[40784] = self.reporter_snapshot(40784, created)
        scans = {"n": 0}

        def probe() -> dict[str, object]:
            scans["n"] += 1
            if scans["n"] < 2:
                return {"known": True, "processes": []}
            return {
                "known": True,
                "processes": [self.reporter_row(40784, server.pid)],
            }

        self.lifecycle.child_probe = probe
        self.guard.terminate_results = [
            {"terminated": True},
            {"terminated": False, "error": "termination_unconfirmed"},
        ]
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "partial_cleanup")
        self.assertEqual(stopped.get("terminated"), 1)
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls],
            [server.pid, 40784],
        )
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertEqual(
            [(item.pid, item.role) for item in stored.processes],
            [(40784, "crash_reporter")],
        )

    def test_t5_close_skips_crash_reporter_windows(self) -> None:
        server = process(57116, "server")
        client = process(53904, "client")
        reporter = ProcessRecord(
            40784,
            "2026-07-15T00:01:00.0000000Z",
            self.reporter_hash,
            "c" * 64,
            "crash_reporter",
            identity_scheme="psutil-argv-v2",
        )
        self.add_run(server, client, reporter)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[client.pid] = snapshot(client)
        self.guard.snapshots[reporter.pid] = self.reporter_snapshot(
            reporter.pid, reporter.creation_time_utc
        )
        win32 = FakeWin32()
        win32.windows = [
            (1, server.pid, True),
            (2, client.pid, True),
            (3, reporter.pid, True),
        ]
        self.lifecycle.window_fns = win32.as_fns()
        result = self.lifecycle.close_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertNotIn("error", result)
        posted_hwnds = [hwnd for hwnd, msg, _w, _l in win32.posts]
        self.assertEqual(sorted(posted_hwnds), [1, 2])
        self.assertTrue(all(msg == window_close.WM_CLOSE for _h, msg, _w, _l in win32.posts))
        self.assertNotIn("crash_reporter", result)


class DaemonWiring7417Test(unittest.TestCase):
    def test_daemon_wires_the_crash_reporter_probe(self) -> None:
        source = (_TOOLS_DIR / "dayz_mcp" / "daemon.py").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"child_probe=lambda:\s*orphan_guard\.snapshot_processes_with_parent\("
            r"\s*\[\"CrashReporter\.exe\"\]\s*\)",
        )


class OrphanGuardParentSnapshot7417Test(unittest.TestCase):
    def test_t6_parent_snapshot_maps_fake_entries(self) -> None:
        entries = [
            (40784, 57116, "CrashReporter.exe"),
            (8, 1, "python.exe"),
            (51036, 53904, "CRASHREPORTER.EXE"),
        ]
        with patch.object(
            orphan_guard,
            "_snapshot_all_process_entries_with_parent",
            return_value=entries,
        ):
            result = orphan_guard.snapshot_processes_with_parent(["CrashReporter.exe"])
        self.assertEqual(
            result,
            {
                "known": True,
                "processes": [
                    {"pid": 40784, "ppid": 57116, "name": "CrashReporter.exe"},
                    {"pid": 51036, "ppid": 53904, "name": "CRASHREPORTER.EXE"},
                ],
            },
        )
        with patch.object(
            orphan_guard,
            "_snapshot_all_process_entries_with_parent",
            return_value=None,
        ):
            self.assertEqual(
                orphan_guard.snapshot_processes_with_parent(["CrashReporter.exe"]),
                {"known": False, "processes": []},
            )
        with patch.object(
            orphan_guard,
            "_snapshot_all_process_entries",
            return_value=[(7, "DAYZDIAG_X64.EXE"), (8, "python.exe")],
        ):
            self.assertEqual(
                orphan_guard.snapshot_processes_by_name(["DayZDiag_x64.exe"]),
                {
                    "known": True,
                    "processes": [{"pid": 7, "name": "DAYZDIAG_X64.EXE"}],
                },
            )
        with self.assertRaises(ValueError):
            orphan_guard.snapshot_processes_with_parent([""])
