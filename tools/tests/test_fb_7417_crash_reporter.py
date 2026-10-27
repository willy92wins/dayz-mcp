from __future__ import annotations

import json
import sys
import unittest
import uuid
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
class FakeAnchors:
    opened: list[int] = field(default_factory=list)
    closed: list[object] = field(default_factory=list)
    tokens: dict[int, object] = field(default_factory=dict)
    refuse: set[int] = field(default_factory=set)

    def open(self, pid: int) -> object | None:
        self.opened.append(pid)
        if pid in self.refuse:
            return None
        token = object()
        self.tokens[pid] = token
        return token

    def close(self, token: object) -> None:
        self.closed.append(token)


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
        self.anchors = FakeAnchors()
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
            parent_anchor=(self.anchors.open, self.anchors.close),
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

    def anchor_alive(self, *records: ProcessRecord) -> None:
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
        self.lifecycle.reap_dead_runs()

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
        self.anchor_alive(server, client)
        closed_before_gone = list(self.anchors.closed)
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
        pins = [self.anchors.tokens[40784], self.anchors.tokens[51036]]
        self.assertEqual(self.anchors.closed, pins)
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
        self.assertEqual(closed_before_gone, [])
        parents = [self.anchors.tokens[server.pid], self.anchors.tokens[client.pid]]
        self.assertCountEqual(self.anchors.closed, pins + parents)

    def test_t3_negative_rows_are_not_registered(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
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
        self.anchor_alive(server)
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
        self.anchor_alive(server)
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
        self.anchor_alive(server)
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
            [(server.pid, "server"), (40784, "crash_reporter")],
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

    def test_t7_gone_parent_without_anchor_is_unverified(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.guard.snapshots[server.pid] = dict(GONE)
        self.guard.snapshots[40784] = self.reporter_snapshot(
            40784, "2026-07-15T00:01:00.0000000Z"
        )
        self.child_rows = [self.reporter_row(40784, server.pid)]
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stored = self.store.get(RUN_ID)
        self.assertNotIn("crash_reporter", [item.role for item in stored.processes])
        unverified = [
            event
            for event in self.audit.events
            if event.get("event") == "crash_reporter_parent_unverified"
        ]
        self.assertEqual(len(unverified), 1)
        self.assertEqual(unverified[0].get("pid"), 40784)
        self.assertEqual(unverified[0].get("ppid"), server.pid)
        self.assertEqual(self.guard.terminate_calls, [])
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "crash_reporter_parent_unverified")

    def test_t7_open_none_and_mismatch_leave_no_anchor(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchors.refuse.add(server.pid)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.lifecycle.reap_dead_runs()
        self.assertEqual(self.lifecycle._parent_anchors, {})
        self.anchors.refuse.clear()
        # Only the anchor's own snapshot sees another process on the pid; every
        # later read finds the parent gone, so the reaper keeps the run.
        answers = [
            {
                "pid": server.pid,
                "creation_time_utc": "2099-01-01T00:00:00.0000000Z",
                "executable_sha256": "e" * 64,
                "command_line_sha256": "f" * 64,
                "identity_scheme": "psutil-argv-v2",
                "identity_complete": True,
            }
        ]
        read = self.guard.snapshot

        def snapshot_once(pid: int) -> dict[str, object]:
            if pid == server.pid:
                return dict(answers.pop(0) if answers else GONE)
            return read(pid)

        self.guard.snapshots[40784] = self.reporter_snapshot(
            40784, "2026-07-15T00:01:00.0000000Z"
        )
        self.child_rows = [self.reporter_row(40784, server.pid)]
        with patch.object(self.guard, "snapshot", side_effect=snapshot_once):
            self.assertEqual(self.lifecycle.reap_dead_runs(), [])
            self.assertEqual(answers, [])
            self.assertEqual(self.lifecycle._parent_anchors, {})
            self.assertEqual(len(self.anchors.closed), 1)
            stored = self.store.get(RUN_ID)
            self.assertNotIn(
                "crash_reporter", [item.role for item in stored.processes]
            )
            stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "crash_reporter_parent_unverified")

    def test_t7_start_run_anchors_and_retirement_closes(self) -> None:
        launched = process(self.launcher.pid, "client")
        self.guard.snapshots[launched.pid] = snapshot(launched)
        request = {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "same",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
        }
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, request)
        self.assertTrue(started.get("ok"), started)
        run_id = str(started["run_id"])
        stored = self.store.get(run_id)
        key = (run_id, stored.processes[0].pid, stored.processes[0].creation_time_utc)
        self.assertIn(key, self.lifecycle._parent_anchors)
        self.assertIn(stored.processes[0].pid, self.anchors.opened)
        closed_before = list(self.anchors.closed)
        self.guard.snapshots[stored.processes[0].pid] = dict(GONE)
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run_id)
        self.assertEqual(stopped.get("ok"), True)
        self.assertEqual(stopped.get("state"), "EXITED")
        self.assertEqual(self.lifecycle._parent_anchors, {})
        self.assertGreater(len(self.anchors.closed), len(closed_before))

    def test_t7_live_matching_parent_needs_no_anchor(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchors.refuse.add(server.pid)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[40784] = self.reporter_snapshot(
            40784, "2026-07-15T00:01:00.0000000Z"
        )
        self.child_rows = [self.reporter_row(40784, server.pid)]
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stored = self.store.get(RUN_ID)
        self.assertEqual(
            [item.role for item in stored.processes if item.role == "crash_reporter"],
            ["crash_reporter"],
        )
        self.assertEqual(self.lifecycle._parent_anchors, {})
        self.assertIn(40784, self.anchors.opened)

    def test_t9_crash_reporter_is_not_a_parent(self) -> None:
        server = process(57116, "server")
        reporter = ProcessRecord(
            40784,
            "2026-07-15T00:01:00.0000000Z",
            self.reporter_hash,
            "c" * 64,
            "crash_reporter",
            identity_scheme="psutil-argv-v2",
        )
        self.add_run(server, reporter)
        self.anchor_alive(server)
        self.guard.snapshots[reporter.pid] = self.reporter_snapshot(
            reporter.pid, reporter.creation_time_utc
        )
        grandchild_created = "2026-07-15T00:02:00.0000000Z"
        self.guard.snapshots[51036] = self.reporter_snapshot(51036, grandchild_created)
        self.child_rows = [self.reporter_row(51036, reporter.pid)]
        self.guard.terminate_calls.clear()
        self.lifecycle.reap_dead_runs()
        stored = self.store.get(RUN_ID)
        self.assertEqual(
            [item.pid for item in stored.processes if item.role == "crash_reporter"],
            [40784],
        )
        self.assertEqual(self.guard.terminate_calls, [])

    def test_t10_warden_close_skips_reporter_windows(self) -> None:
        server = process(57116, "server")
        reporter = ProcessRecord(
            40784,
            "2026-07-15T00:01:00.0000000Z",
            self.reporter_hash,
            "c" * 64,
            "crash_reporter",
            identity_scheme="psutil-argv-v2",
        )
        self.add_run(server, reporter)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[reporter.pid] = self.reporter_snapshot(
            reporter.pid, reporter.creation_time_utc
        )
        win32 = FakeWin32()
        win32.windows = [
            (1, server.pid, True),
            (3, reporter.pid, True),
        ]
        self.lifecycle.window_fns = win32.as_fns()
        guard = {
            "operation": "close",
            "session_id": IDENTITY_A.session_id,
            "run_id": RUN_ID,
            "expected": tuple(),
            "allow_gone": False,
            "settings_path": self.root / "idle.json",
            "wall": 0.0,
        }
        run = self.store.get(RUN_ID)
        with patch.object(self.lifecycle, "_idle_switch_on", return_value=True), patch.object(
            self.lifecycle, "_idle_anchors_changed", return_value=False
        ), patch.object(
            self.lifecycle, "_idle_destruction_refusal", return_value=None
        ), patch.object(
            self.lifecycle, "_idle_effect_boundary", return_value=None
        ):
            self.lifecycle._close_with_idle_guard(run, RUN_ID, guard)
        posted_hwnds = [hwnd for hwnd, _msg, _w, _l in win32.posts]
        self.assertEqual(posted_hwnds, [1])

    def test_t12_incomplete_identity_after_parent_gates_is_unknown(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
        self.guard.snapshots[server.pid] = dict(GONE)
        child = self.reporter_snapshot(40784, "2026-07-15T00:01:00.0000000Z")
        child["identity_complete"] = False
        self.guard.snapshots[40784] = child
        self.child_rows = [self.reporter_row(40784, server.pid)]
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stored = self.store.get(RUN_ID)
        self.assertNotIn("crash_reporter", [item.role for item in stored.processes])
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "child_scan_unknown")

    def test_t12_second_scan_reason_is_partial_cleanup(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
        self.guard.snapshots[server.pid] = snapshot(server)
        scans = {"n": 0}

        def boom() -> dict[str, object]:
            scans["n"] += 1
            if scans["n"] < 2:
                return {"known": True, "processes": []}
            raise RuntimeError("toolhelp failed")

        self.lifecycle.child_probe = boom
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "partial_cleanup")
        self.assertEqual(stopped.get("reason"), "child_scan_unknown")
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertEqual([item.role for item in stored.processes], ["server"])

        run = self.store.get(RUN_ID)
        run.state = "RUNNING"
        run.owner_session_id = "A"
        run.owner_lease_id = "lease-A"
        run.processes = [server]
        self.store.replace(run)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[40784] = self.reporter_snapshot(
            40784, "2026-07-15T00:01:00.0000000Z"
        )
        scans["n"] = 0
        original = self.store.replace

        def fail_reporter_write(item: RunRecord) -> None:
            if any(record.role == "crash_reporter" for record in item.processes):
                raise OSError("disk full")
            original(item)

        def probe() -> dict[str, object]:
            scans["n"] += 1
            if scans["n"] < 2:
                return {"known": True, "processes": []}
            return {
                "known": True,
                "processes": [self.reporter_row(40784, server.pid)],
            }

        self.lifecycle.child_probe = probe
        with patch.object(self.store, "replace", fail_reporter_write):
            stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "partial_cleanup")
        self.assertEqual(stopped.get("reason"), "manifest_failed")
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertEqual([item.role for item in stored.processes], ["server"])

    def test_t12_unknown_classification_in_second_loop_keeps_reporter(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
        self.guard.snapshots[server.pid] = snapshot(server)
        created = "2026-07-15T00:01:00.0000000Z"
        self.guard.snapshots[40784] = self.reporter_snapshot(40784, created)
        scans = {"n": 0}
        classify_hits = {"n": 0}
        original_classify = self.lifecycle._classify_registered_process

        def classify(record: ProcessRecord) -> tuple[str, str]:
            kind, reason = original_classify(record)
            if record.pid == 40784:
                classify_hits["n"] += 1
                if classify_hits["n"] > 0 and kind == "owned":
                    return "unknown", "process_identity_mismatch"
            return kind, reason

        def probe() -> dict[str, object]:
            scans["n"] += 1
            if scans["n"] < 2:
                return {"known": True, "processes": []}
            return {
                "known": True,
                "processes": [self.reporter_row(40784, server.pid)],
            }

        self.lifecycle.child_probe = probe
        self.lifecycle._classify_registered_process = classify  # type: ignore[method-assign]
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "partial_cleanup")
        stored = self.store.get(RUN_ID)
        self.assertEqual(
            [(item.pid, item.role) for item in stored.processes],
            [(server.pid, "server"), (40784, "crash_reporter")],
        )

    def test_t12_second_scan_reason_keeps_the_reporter_it_registered(self) -> None:
        server = process(57116, "server")
        client = process(53904, "client")
        self.add_run(server, client)
        self.anchor_alive(server, client)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[client.pid] = snapshot(client)
        self.guard.snapshots[40784] = self.reporter_snapshot(
            40784, "2026-07-15T00:01:00.0000000Z"
        )
        incomplete = self.reporter_snapshot(51036, "2026-07-15T00:01:01.0000000Z")
        incomplete["identity_complete"] = False
        self.guard.snapshots[51036] = incomplete
        scans = {"n": 0}

        def probe() -> dict[str, object]:
            scans["n"] += 1
            if scans["n"] < 2:
                return {"known": True, "processes": []}
            return {
                "known": True,
                "processes": [
                    self.reporter_row(40784, server.pid),
                    self.reporter_row(51036, client.pid),
                ],
            }

        self.lifecycle.child_probe = probe
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "partial_cleanup")
        self.assertEqual(stopped.get("reason"), "child_scan_unknown")
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls],
            [server.pid, client.pid],
        )
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertEqual(
            [(item.pid, item.role) for item in stored.processes],
            [
                (server.pid, "server"),
                (client.pid, "client"),
                (40784, "crash_reporter"),
            ],
        )

    def test_t12_two_reporters_one_unknown_keep_both_and_parents(self) -> None:
        server = process(57116, "server")
        client = process(53904, "client")
        self.add_run(server, client)
        self.anchor_alive(server, client)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[client.pid] = snapshot(client)
        created_a = "2026-07-15T00:01:00.0000000Z"
        created_b = "2026-07-15T00:01:01.0000000Z"
        self.guard.snapshots[40784] = self.reporter_snapshot(40784, created_a)
        self.guard.snapshots[51036] = self.reporter_snapshot(51036, created_b)
        scans = {"n": 0}
        original_classify = self.lifecycle._classify_registered_process

        def classify(record: ProcessRecord) -> tuple[str, str]:
            if record.pid == 40784:
                kind, reason = original_classify(record)
                if kind == "owned":
                    return "unknown", "process_identity_mismatch"
                return kind, reason
            return original_classify(record)

        def probe() -> dict[str, object]:
            scans["n"] += 1
            if scans["n"] < 2:
                return {"known": True, "processes": []}
            return {
                "known": True,
                "processes": [
                    self.reporter_row(40784, server.pid),
                    self.reporter_row(51036, client.pid),
                ],
            }

        self.lifecycle.child_probe = probe
        self.lifecycle._classify_registered_process = classify  # type: ignore[method-assign]
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "partial_cleanup")
        # The unknown reporter stops the second loop before any kill in it.
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls],
            [server.pid, client.pid],
        )
        stored = self.store.get(RUN_ID)
        roles = [(item.pid, item.role) for item in stored.processes]
        self.assertEqual(
            roles,
            [
                (server.pid, "server"),
                (client.pid, "client"),
                (40784, "crash_reporter"),
                (51036, "crash_reporter"),
            ],
        )

    def test_t13_second_loop_handles_pid_and_creation_time(self) -> None:
        server = process(57116, "server")
        client = process(53904, "client")
        self.anchors.refuse.add(server.pid)
        self.add_run(server, client)
        self.anchor_alive(server, client)
        self.assertNotIn(
            (RUN_ID, server.pid, server.creation_time_utc),
            self.lifecycle._parent_anchors,
        )
        self.anchors.refuse.discard(server.pid)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[client.pid] = dict(GONE)
        reused = self.reporter_snapshot(server.pid, "2026-07-15T00:02:00.0000000Z")
        scans = {"n": 0}

        def probe() -> dict[str, object]:
            scans["n"] += 1
            if scans["n"] < 2:
                return {"known": True, "processes": []}
            self.guard.snapshots[server.pid] = reused
            return {
                "known": True,
                "processes": [self.reporter_row(server.pid, client.pid)],
            }

        self.lifecycle.child_probe = probe
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("ok"), True)
        self.assertEqual(stopped.get("state"), "EXITED")
        self.assertEqual(stopped.get("terminated"), 2)
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls],
            [server.pid, server.pid],
        )
        self.assertIn(server.pid, self.anchors.opened)

    def test_t14_second_loop_process_not_found_is_gone(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
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
            dict(GONE),
        ]
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("ok"), True)
        self.assertEqual(stopped.get("state"), "EXITED")
        self.assertEqual(stopped.get("terminated"), 1)
        self.assertEqual(stopped.get("stop_method"), "forced_kill")

    def test_t15_second_walk_ppid_change_and_pin_none(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchors.refuse.add(server.pid)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[40784] = self.reporter_snapshot(
            40784, "2026-07-15T00:01:00.0000000Z"
        )
        walks = {"n": 0}

        def probe() -> dict[str, object]:
            walks["n"] += 1
            if walks["n"] == 1:
                return {
                    "known": True,
                    "processes": [self.reporter_row(40784, server.pid)],
                }
            return {
                "known": True,
                "processes": [self.reporter_row(40784, 1)],
            }

        self.lifecycle.child_probe = probe
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stored = self.store.get(RUN_ID)
        self.assertNotIn("crash_reporter", [item.role for item in stored.processes])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertIn(40784, self.anchors.opened)
        self.assertIn(self.anchors.tokens[40784], self.anchors.closed)

        self.anchors.refuse.add(40784)
        walks["n"] = 0

        def same_walk() -> dict[str, object]:
            return {
                "known": True,
                "processes": [self.reporter_row(40784, server.pid)],
            }

        self.lifecycle.child_probe = same_walk
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "child_scan_unknown")

        self.anchors.refuse.discard(40784)
        walks["n"] = 0

        def missing_later() -> dict[str, object]:
            walks["n"] += 1
            if walks["n"] == 1:
                return {
                    "known": True,
                    "processes": [self.reporter_row(40784, server.pid)],
                }
            return {"known": True, "processes": []}

        self.lifecycle.child_probe = missing_later
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stored = self.store.get(RUN_ID)
        self.assertNotIn("crash_reporter", [item.role for item in stored.processes])
        self.assertNotEqual(stored.state, "EXITED")

    def test_t16_gate_zero_registered_reporter_survives_restart(self) -> None:
        server = process(57116, "server")
        reporter = ProcessRecord(
            40784,
            "2026-07-15T00:01:00.0000000Z",
            self.reporter_hash,
            "c" * 64,
            "crash_reporter",
            identity_scheme="psutil-argv-v2",
        )
        self.add_run(server, reporter)
        self.guard.snapshots[server.pid] = dict(GONE)
        self.guard.snapshots[reporter.pid] = self.reporter_snapshot(
            reporter.pid, reporter.creation_time_utc
        )
        self.guard.snapshots[51036] = self.reporter_snapshot(
            51036, "2026-07-15T00:01:02.0000000Z"
        )
        self.child_rows = [
            self.reporter_row(reporter.pid, server.pid),
            self.reporter_row(51036, server.pid),
        ]
        fresh = ProcessLifecycle(
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
            child_probe=lambda: {"known": True, "processes": list(self.child_rows)},
            parent_anchor=(self.anchors.open, self.anchors.close),
        )
        refused = fresh.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(refused.get("error"), "crash_reporter_parent_unverified")
        self.child_rows = [self.reporter_row(reporter.pid, server.pid)]
        adopted = fresh.adopt_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(adopted.get("ok"), True)
        self.guard.terminate_calls.clear()
        stopped = fresh.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("ok"), True)
        self.assertEqual(stopped.get("state"), "EXITED")
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls],
            [reporter.pid],
        )
        self.assertNotEqual(stopped.get("error"), "crash_reporter_parent_unverified")

        only = ProcessRecord(
            40784,
            "2026-07-15T00:01:00.0000000Z",
            self.reporter_hash,
            "c" * 64,
            "crash_reporter",
            identity_scheme="psutil-argv-v2",
        )
        idle = RunRecord(
            "run-idle-reporter",
            None,
            None,
            "UNRECONCILED",
            "same",
            "@SameMod",
            "profiles",
            "mission",
            [only],
        )
        self.store.add(idle)
        self.guard.snapshots[only.pid] = self.reporter_snapshot(
            only.pid, only.creation_time_utc
        )
        self.lifecycle.diag_probe = lambda: {"known": True, "processes": []}
        reconciled = self.lifecycle.admin_reconcile(
            "run-idle-reporter", only.pid, "incident"
        )
        self.assertEqual(reconciled.get("reconciled"), True)
        stored = self.store.get("run-idle-reporter")
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertEqual([item.role for item in stored.processes], ["crash_reporter"])

    def test_t17_unknown_parent_snapshot_is_parent_scan_unknown(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
        self.guard.snapshots[server.pid] = {"error": "identity_unavailable"}
        self.guard.snapshots[40784] = self.reporter_snapshot(
            40784, "2026-07-15T00:01:00.0000000Z"
        )
        self.child_rows = [self.reporter_row(40784, server.pid)]
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])
        stored = self.store.get(RUN_ID)
        self.assertNotEqual(stored.state, "EXITED")
        self.assertNotIn("crash_reporter", [item.role for item in stored.processes])
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "parent_scan_unknown")
        self.guard.snapshots[server.pid] = dict(GONE)
        self.assertEqual(self.lifecycle.reap_dead_runs(), [])

    def test_t18_quarantine_and_warden_partial_keep_parents(self) -> None:
        server = process(57116, "server")
        reporter = ProcessRecord(
            40784,
            "2026-07-15T00:01:00.0000000Z",
            self.reporter_hash,
            "c" * 64,
            "crash_reporter",
            identity_scheme="psutil-argv-v2",
        )
        self.add_run(server, reporter)
        self.anchor_alive(server)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[reporter.pid] = self.reporter_snapshot(
            reporter.pid, reporter.creation_time_utc
        )
        original = self.guard.terminate
        hits = {"n": 0}

        def terminate_then_quarantine(record: ProcessRecord) -> dict[str, object]:
            result = original(record)
            hits["n"] += 1
            if hits["n"] == 1:
                self.lifecycle.retail_probe = lambda: {
                    "known": True,
                    "processes": [{"pid": 1, "name": "DayZ_x64.exe"}],
                }
            return result

        self.guard.terminate = terminate_then_quarantine  # type: ignore[method-assign]
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "partial_cleanup")
        stored = self.store.get(RUN_ID)
        self.assertEqual(
            [(item.pid, item.role) for item in stored.processes],
            [(server.pid, "server"), (reporter.pid, "crash_reporter")],
        )

        self.lifecycle.retail_probe = lambda: {"known": True, "processes": []}
        run = self.store.get(RUN_ID)
        run.state = "RUNNING"
        run.owner_session_id = "A"
        run.owner_lease_id = "lease-A"
        run.processes = [server, reporter]
        self.store.replace(run)
        self.guard.terminate = original  # type: ignore[method-assign]
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[reporter.pid] = self.reporter_snapshot(
            reporter.pid, reporter.creation_time_utc
        )
        guard = {
            "operation": "stop",
            "session_id": IDENTITY_A.session_id,
            "run_id": RUN_ID,
            "expected": tuple(),
            "allow_gone": False,
            "settings_path": self.root / "idle.json",
            "wall": 0.0,
        }
        boundaries = {"n": 0}

        def boundary(*_args, **_kwargs):
            boundaries["n"] += 1
            if boundaries["n"] == 1:
                return None
            return ("operation", "warden_disabled")

        with patch.object(self.lifecycle, "_warden_idle_guard", return_value=guard), patch.object(
            self.lifecycle, "_idle_switch_on", return_value=True
        ), patch.object(
            self.lifecycle, "_idle_anchors_changed", return_value=False
        ), patch.object(
            self.lifecycle, "_idle_destruction_refusal", return_value=None
        ), patch.object(
            self.lifecycle, "_idle_effect_boundary", side_effect=boundary
        ):
            partial = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(partial.get("error"), "partial_stop")
        stored = self.store.get(RUN_ID)
        self.assertEqual(
            [(item.pid, item.role) for item in stored.processes],
            [(server.pid, "server"), (reporter.pid, "crash_reporter")],
        )

    def test_t19_warden_stop_scans_after_kills(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
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
        guard = {
            "operation": "stop",
            "session_id": IDENTITY_A.session_id,
            "run_id": RUN_ID,
            "expected": tuple(),
            "allow_gone": False,
            "settings_path": self.root / "idle.json",
            "wall": 0.0,
        }
        with patch.object(self.lifecycle, "_warden_idle_guard", return_value=guard), patch.object(
            self.lifecycle, "_idle_switch_on", return_value=True
        ), patch.object(
            self.lifecycle, "_idle_anchors_changed", return_value=False
        ), patch.object(
            self.lifecycle, "_idle_destruction_refusal", return_value=None
        ), patch.object(
            self.lifecycle, "_idle_effect_boundary", return_value=None
        ):
            stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("error"), "partial_cleanup")
        self.assertEqual(stopped.get("reason"), "crash_reporter_left")
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls],
            [server.pid],
        )
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertEqual(
            [(item.pid, item.role) for item in stored.processes],
            [(server.pid, "server"), (40784, "crash_reporter")],
        )

        run = self.store.get(RUN_ID)
        run.state = "RUNNING"
        run.owner_session_id = "A"
        run.owner_lease_id = "lease-A"
        run.processes = [server]
        self.store.replace(run)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.terminate_calls.clear()
        self.lifecycle.child_probe = lambda: {"known": True, "processes": []}
        with patch.object(self.lifecycle, "_warden_idle_guard", return_value=guard), patch.object(
            self.lifecycle, "_idle_switch_on", return_value=True
        ), patch.object(
            self.lifecycle, "_idle_anchors_changed", return_value=False
        ), patch.object(
            self.lifecycle, "_idle_destruction_refusal", return_value=None
        ), patch.object(
            self.lifecycle, "_idle_effect_boundary", return_value=None
        ):
            clean = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(clean.get("ok"), True)
        self.assertEqual(clean.get("state"), "EXITED")

    def test_t20_recover_launch_intent_anchors_the_record(self) -> None:
        profiles = self.root / "profiles"
        profiles.mkdir()
        instance = str(uuid.uuid4())
        (profiles / "dayz_mcp.json").write_text(
            json.dumps({"instance": instance}), encoding="utf-8"
        )
        exe = str(self.game / "DayZDiag_x64.exe")
        argv = [exe, f"-profiles={profiles}"]
        hashes = identity_hashes(exe, argv)
        created = "2026-07-15T00:00:10.0000000Z"
        pid = 9001
        self.guard.snapshots[pid] = {
            "pid": pid,
            "creation_time_utc": created,
            "executable_sha256": hashes["executable_sha256"],
            "command_line_sha256": hashes["command_line_sha256"],
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": pid, "name": "DayZDiag_x64.exe"}],
        }
        run = RunRecord(
            RUN_ID,
            "A",
            "lease-A",
            "STARTING",
            "same",
            "@SameMod",
            "profiles",
            "mission",
            [],
        )
        self.store.add(run)
        stamp_launcher(self.lifecycle, run.run_id, IDENTITY_A)
        intent_path = self.store.paths.runs_path.with_name("launch-intent.json")
        intent_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "run_id": RUN_ID,
                    "role": "client",
                    "instance": instance,
                    "profiles": str(profiles),
                    "command_line_sha256": hashes["command_line_sha256"],
                    "owner_session_id": "A",
                    "owner_lease_id": "lease-A",
                    "not_before_utc": created,
                }
            ),
            encoding="utf-8",
        )
        outcome = self.lifecycle.recover_launch_intent()
        self.assertEqual(outcome.get("launch_intent"), "recovered", outcome)
        stored = self.store.get(RUN_ID)
        key = (RUN_ID, stored.processes[0].pid, stored.processes[0].creation_time_utc)
        self.assertIn(key, self.lifecycle._parent_anchors)
        closed_before = list(self.anchors.closed)
        self.guard.snapshots[pid] = dict(GONE)
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(stopped.get("ok"), True)
        self.assertEqual(self.lifecycle._parent_anchors, {})
        self.assertGreater(len(self.anchors.closed), len(closed_before))

    def test_t21_gone_child_snapshot_is_skipped(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
        self.guard.snapshots[server.pid] = dict(GONE)
        self.guard.snapshots[40784] = dict(GONE)
        self.child_rows = [self.reporter_row(40784, server.pid)]
        reaped = self.lifecycle.reap_dead_runs()
        self.assertEqual(reaped, [RUN_ID])
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "EXITED")
        self.assertEqual(stored.processes, [])

    def test_t23_repair_manifest_recovery_releases_anchors(self) -> None:
        server = process(57116, "server")
        self.add_run(server)
        self.anchor_alive(server)
        self.assertTrue(self.lifecycle._parent_anchors)
        raw = b'{"version":1,"runs":[]}'
        result = self.lifecycle.repair_manifest_recovery(raw)
        self.assertEqual(result.get("terminal_safe"), True)
        self.assertEqual(self.lifecycle._parent_anchors, {})
        self.assertTrue(self.anchors.closed)


class DaemonWiring7417Test(unittest.TestCase):
    def test_daemon_wires_the_crash_reporter_probe(self) -> None:
        source = (_TOOLS_DIR / "dayz_mcp" / "daemon.py").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"child_probe=lambda:\s*orphan_guard\.snapshot_processes_with_parent\("
            r"\s*\[\"CrashReporter\.exe\"\]\s*\)",
        )
        self.assertIn("orphan_guard.open_process_anchor", source)
        self.assertIn("orphan_guard.close_process_anchor", source)


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

    def test_t6_open_and_close_process_anchor_use_fake_kernel32(self) -> None:
        class FakeK32:
            def __init__(self) -> None:
                self.opened: list[tuple[int, bool, int]] = []
                self.closed: list[object] = []

            def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
                self.opened.append((access, inherit, pid))
                return 42

            def CloseHandle(self, handle: object) -> bool:
                self.closed.append(handle)
                return True

        fake = FakeK32()
        with patch.object(orphan_guard, "_IS_WINDOWS", True), patch.object(
            orphan_guard, "_k32", fake
        ):
            token = orphan_guard.open_process_anchor(57116)
            self.assertEqual(token, 42)
            self.assertEqual(
                fake.opened,
                [
                    (
                        orphan_guard._SYNCHRONIZE
                        | orphan_guard._PROCESS_QUERY_LIMITED_INFORMATION,
                        False,
                        57116,
                    )
                ],
            )
            orphan_guard.close_process_anchor(token)
            self.assertEqual(fake.closed, [42])
