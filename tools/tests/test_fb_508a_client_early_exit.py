from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import unittest
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from tests.steam_helpers import FakeSteamGate

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_tool, loopback, native_launcher_transaction
from dayz_mcp import orphan_guard, steam_preflight
import mcp_capture
from dayz_mcp.native_process_guard import NativeProcessGuard
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
    _redact_diagnostic_text,
    _utc_epoch,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from dayz_mcp.window_close import Win32WindowFns
from tests._tiers import slow_test
from tests.dayz_test_tool_helpers import (
    RUN_ID as TOOL_RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)
from tests.lifecycle_helpers import (
    HASH_A,
    HASH_B,
    IDENTITY as LIFECYCLE_IDENTITY,
    LifecycleFixture,
    Sequence,
    identity as lifecycle_identity,
    record as lifecycle_record,
    stamp_launcher,
)
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    IDENTITY_A,
    snapshot,
)

RUN_ID = "run-508a"
CREATED = "2026-07-15T00:00:00.000000Z"
EXITED = "2026-07-15T00:00:23.000000Z"
GONE = {"error": "process_not_found", "exit_code": 4}
INST = "11111111-1111-4111-8111-111111111111"


def _record(pid: int, role: str = "client", created: str = CREATED) -> ProcessRecord:
    return ProcessRecord(
        pid, created, HASH_A, HASH_B, role, identity_scheme="psutil-argv-v2"
    )


class _Life:
    def __init__(self, **kwargs: object) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        game = root / "DayZ"
        game.mkdir()
        paths = RuntimePaths(
            root / "runtime",
            root / "runtime" / "audit",
            root / "runtime" / "coordination.json",
            root / "runtime" / "runs.json",
        )
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=SessionCoordinator(
                token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=AuditSink()
            ),
            manifest=RunManifestStore(paths),
            audit=AuditSink(),
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game,
            launcher=self.launcher,
            daemon_generation="gen-now",
            **kwargs,  # type: ignore[arg-type]
        )
        self.store = self.lifecycle.manifest

    def close(self) -> None:
        self.temporary.cleanup()

    def add_run(self, *records: ProcessRecord) -> RunRecord:
        run = RunRecord(
            RUN_ID, None, None, "RUNNING_IDLE", "label", "@ExampleMod",
            "profiles", "mission", list(records),
            daemon_generation_at_launch="gen-now",
        )
        self.store.add(run)
        return run

    def diagnostics(self, run: RunRecord) -> list[dict[str, object]]:
        return self.lifecycle._client_role_diagnostics(run)


def _dead_life(anchor_exit=None, **kwargs: object):
    life = _Life(anchor_exit=anchor_exit, **kwargs)
    client = _record(802)
    run = life.add_run(client)
    life.guard.snapshots[client.pid] = dict(GONE)
    return life, run, client


class OrphanGuardExit508aTest(unittest.TestCase):
    def _fake(self, *, wait: int = 0, code: int = 7, fail_code: bool = False):
        ticks = (
            int(
                (
                    datetime(2026, 7, 15, 0, 0, 23, 123456, tzinfo=timezone.utc)
                    - datetime(1970, 1, 1, tzinfo=timezone.utc)
                )
                / timedelta(microseconds=1)
            )
            * 10
            + 116444736000000000
        )

        class FakeK32:
            def WaitForSingleObject(self, _token: object, _timeout: int) -> int:
                return wait

            def GetExitCodeProcess(self, _token: object, ref: object) -> bool:
                if fail_code:
                    return False
                ctypes.cast(ref, ctypes.POINTER(wintypes.DWORD)).contents.value = code
                return True

            def GetProcessTimes(self, _t, _c, exited, _k, _u) -> bool:
                target = ctypes.cast(
                    exited, ctypes.POINTER(wintypes.FILETIME)
                ).contents
                target.dwLowDateTime = ticks & 0xFFFFFFFF
                target.dwHighDateTime = ticks >> 32
                return True

        return FakeK32()

    def test_read_process_exit_variants(self) -> None:
        with patch.object(orphan_guard, "_IS_WINDOWS", True), patch.object(
            orphan_guard, "_k32", self._fake(wait=0x102)
        ):
            self.assertIsNone(orphan_guard.read_process_exit(object()))
        with patch.object(orphan_guard, "_IS_WINDOWS", True), patch.object(
            orphan_guard, "_k32", self._fake()
        ):
            self.assertEqual(
                orphan_guard.read_process_exit(object()),
                {
                    "exit_code": 7,
                    "exit_time_utc": "2026-07-15T00:00:23.123456Z",
                },
            )
        with patch.object(orphan_guard, "_IS_WINDOWS", True), patch.object(
            orphan_guard, "_k32", self._fake(fail_code=True)
        ):
            self.assertIsNone(orphan_guard.read_process_exit(object()))
        with patch.object(orphan_guard, "_IS_WINDOWS", True), patch.object(
            orphan_guard, "_k32", self._fake()
        ):
            self.assertIsNone(orphan_guard.read_process_exit(None))
        with patch.object(orphan_guard, "_IS_WINDOWS", False):
            self.assertIsNone(orphan_guard.read_process_exit(object()))

    @slow_test
    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_real_child_exit_while_anchor_open(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read(1); raise SystemExit(7)"],
            stdin=subprocess.PIPE,
        )
        token = None
        try:
            token = orphan_guard.open_process_anchor(proc.pid)
            self.assertIsNotNone(token)
            creation = NativeProcessGuard().snapshot(proc.pid)
            created = creation.get("creation_time_utc")
            self.assertIsInstance(created, str)
            assert proc.stdin is not None
            proc.stdin.close()
            proc.wait(timeout=10)
            info = orphan_guard.read_process_exit(token)
            self.assertIsNotNone(info)
            self.assertEqual(info["exit_code"], 7)  # type: ignore[index]
            self.assertIsNotNone(info["exit_time_utc"])  # type: ignore[index]
            exit_epoch = _utc_epoch(info["exit_time_utc"])  # type: ignore[index]
            created_epoch = _utc_epoch(created)
            self.assertIsNotNone(exit_epoch)
            self.assertIsNotNone(created_epoch)
            assert exit_epoch is not None and created_epoch is not None
            self.assertGreaterEqual(exit_epoch, created_epoch)
            snap = NativeProcessGuard().snapshot(proc.pid)
            self.assertEqual(snap.get("error"), "process_not_found")
        finally:
            if token is not None:
                orphan_guard.close_process_anchor(token)
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
            if proc.poll() is None:
                proc.kill()


class LifecycleDiagnosis508aTest(unittest.TestCase):
    def test_anchor_exit_gives_spontaneous(self) -> None:
        token = object()
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 3, "exit_time_utc": EXITED}
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = token
            rows = life.diagnostics(run)
            self.assertEqual(len(life.guard.snapshot_calls), 1)
            row = rows[0]
            self.assertEqual(row["exit_code"], 3)
            self.assertEqual(row["exit_time_utc"], EXITED)
            diag = row["exit_diagnosis"]
            self.assertEqual(diag["classification"], "spontaneous")  # type: ignore[index]
            self.assertEqual(diag["lifetime_s"], 23.0)  # type: ignore[index]
            self.assertEqual(diag["close_posted"], False)  # type: ignore[index]
            self.assertEqual(diag["lifecycle_actions"], [])  # type: ignore[index]
        finally:
            life.close()

    def test_no_anchor_no_guard_exit_gives_unknown(self) -> None:
        life, run, _client = _dead_life()
        try:
            row = life.diagnostics(run)[0]
            self.assertIsNone(row["exit_code"])
            self.assertEqual(row["exit_diagnosis"]["classification"], "unknown")  # type: ignore[index]
        finally:
            life.close()

    def test_published_row_derives_from_locked_copy(self) -> None:
        life, run, client = _dead_life()
        try:
            row = life.diagnostics(run)[0]
            self.assertIsNone(row["exit_code"])
            self.assertEqual(row["exit_diagnosis"]["classification"], "unknown")  # type: ignore[index]
            key = life.lifecycle._client_action_key(RUN_ID, client)

            class _FillingFallback(dict[str, object]):
                def __contains__(self, item: object) -> bool:
                    stored = life.lifecycle._client_role_observations[key]
                    stored["exit_code"] = 7
                    stored["exit_time_utc"] = EXITED
                    return dict.__contains__(self, item)

            published = life.lifecycle._published_client_row(
                key, _FillingFallback()
            )
            self.assertIsNone(published["exit_code"])
            self.assertEqual(
                published["exit_diagnosis"]["classification"], "unknown"  # type: ignore[index]
            )
            later = life.lifecycle._published_client_row(key, {})
            self.assertEqual(later["exit_code"], 7)
            self.assertEqual(
                later["exit_diagnosis"]["classification"], "spontaneous"  # type: ignore[index]
            )
        finally:
            life.close()

    def test_reused_pid_no_anchor_unknown(self) -> None:
        life = _Life()
        try:
            client = _record(802)
            other = _record(802, created="2026-07-15T00:09:00.000000Z")
            run = life.add_run(client)
            life.guard.snapshots[client.pid] = snapshot(other)
            row = life.diagnostics(run)[0]
            self.assertEqual(row["state"], "dead")
            self.assertIsNone(row["exit_code"])
            self.assertEqual(row["exit_diagnosis"]["classification"], "unknown")  # type: ignore[index]
        finally:
            life.close()

    def test_wm_close_before_exit_is_mcp_ordered(self) -> None:
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": EXITED}
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            life.lifecycle._observation_now = lambda: "2026-07-15T00:00:10.000Z"  # type: ignore[method-assign]
            entry = life.lifecycle._begin_client_action(
                RUN_ID, client, "wm_close", "close_run"
            )
            life.lifecycle._end_client_action(RUN_ID, client, entry, "posted")
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "mcp_ordered")  # type: ignore[index]
            self.assertTrue(diag["close_posted"])  # type: ignore[index]
        finally:
            life.close()

    def test_terminate_after_exit_stays_spontaneous(self) -> None:
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": EXITED}
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            life.lifecycle._observation_now = lambda: "2026-07-15T00:00:30.000Z"  # type: ignore[method-assign]
            entry = life.lifecycle._begin_client_action(
                RUN_ID, client, "terminate", "stop"
            )
            life.lifecycle._end_client_action(RUN_ID, client, entry, "terminated")
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "spontaneous")  # type: ignore[index]
        finally:
            life.close()

    def test_parsed_not_string_time_comparison(self) -> None:
        exit_at = "2026-07-15T00:00:00.100500Z"
        created = "2026-07-15T00:00:00.000000Z"
        life = _Life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": exit_at}
        )
        try:
            client = _record(802, created=created)
            run = life.add_run(client)
            life.guard.snapshots[client.pid] = dict(GONE)
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, created)] = object()
            life.lifecycle._observation_now = lambda: "2026-07-15T00:00:00.100Z"  # type: ignore[method-assign]
            entry = life.lifecycle._begin_client_action(
                RUN_ID, client, "wm_close", "close_run"
            )
            life.lifecycle._end_client_action(RUN_ID, client, entry, "posted")
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "mcp_ordered")  # type: ignore[index]
        finally:
            life.close()

    def test_alive_has_no_diagnosis(self) -> None:
        life = _Life()
        try:
            client = _record(802)
            run = life.add_run(client)
            life.guard.snapshots[client.pid] = snapshot(client)
            row = life.diagnostics(run)[0]
            self.assertEqual(row["state"], "alive")
            self.assertIsNone(row["exit_diagnosis"])
        finally:
            life.close()

    def test_in_flight_action_counts_then_failed_is_spontaneous(self) -> None:
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": EXITED}
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            life.lifecycle._observation_now = lambda: "2026-07-15T00:00:10.000Z"  # type: ignore[method-assign]
            entry = life.lifecycle._begin_client_action(
                RUN_ID, client, "wm_close", "close_run"
            )
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "mcp_ordered")  # type: ignore[index]
            life.lifecycle._end_client_action(RUN_ID, client, entry, "failed")
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "spontaneous")  # type: ignore[index]
        finally:
            life.close()

    def test_pending_unknown_and_rpt_on_third_pending(self) -> None:
        life, run, client = _dead_life(anchor_exit=lambda _t: None)
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            key = (life.lifecycle._current_generation(), RUN_ID, client.pid, CREATED)
            life.lifecycle._client_launch_output_dirs[key] = "no-such-folder-508a"
            scans: list[str] = []
            original = life.lifecycle._client_rpt_evidence

            def counting_scan(folder: str, row: dict[str, object]):
                scans.append(folder)
                return original(folder, row)

            life.lifecycle._client_rpt_evidence = counting_scan  # type: ignore[method-assign]
            for _ in range(2):
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                self.assertEqual(diag["classification"], "unknown")  # type: ignore[index]
            self.assertEqual(scans, [])
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "unknown")  # type: ignore[index]
            self.assertEqual(len(scans), 1)
            self.assertIsNone(life.diagnostics(run)[0]["exit_code"])
            self.assertEqual(len(scans), 1)
        finally:
            life.close()

    def test_pending_then_exited(self) -> None:
        answers: list[object] = [None, {"exit_code": 5, "exit_time_utc": EXITED}]
        life, run, client = _dead_life(
            anchor_exit=lambda _t: answers.pop(0)  # type: ignore[arg-type]
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            first = life.diagnostics(run)[0]
            self.assertEqual(first["exit_diagnosis"]["classification"], "unknown")  # type: ignore[index]
            row = life.diagnostics(run)[0]
            self.assertEqual(row["exit_code"], 5)
            self.assertEqual(row["exit_diagnosis"]["classification"], "spontaneous")  # type: ignore[index]
        finally:
            life.close()

    def test_exit_code_first_time_later_gives_lifetime(self) -> None:
        answers: list[object] = [
            {"exit_code": 7, "exit_time_utc": None},
            {"exit_code": 7, "exit_time_utc": EXITED},
        ]
        life, run, client = _dead_life(
            anchor_exit=lambda _t: answers.pop(0)  # type: ignore[arg-type]
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            first = life.diagnostics(run)[0]
            self.assertEqual(first["exit_code"], 7)
            self.assertIsNone(first["exit_diagnosis"]["lifetime_s"])  # type: ignore[index]
            second = life.diagnostics(run)[0]
            self.assertEqual(second["exit_diagnosis"]["lifetime_s"], 23.0)  # type: ignore[index]
        finally:
            life.close()

    def _held_reader_interleaving(self, *, with_action: bool, check_published: bool):
        """B reads anchor 7, is held before its merge; A (none) merges first."""
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 7, "exit_time_utc": EXITED}
        )
        published: dict[str, object] = {}
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            if with_action:
                life.lifecycle._observation_now = lambda: "2026-07-15T00:00:10.000Z"  # type: ignore[method-assign]
                entry = life.lifecycle._begin_client_action(
                    RUN_ID, client, "wm_close", "close_run"
                )
                life.lifecycle._end_client_action(RUN_ID, client, entry, "posted")
            original = life.lifecycle._remember_client_observation
            state: dict[str, object] = {"held": False}

            def holding(key, observation):  # type: ignore[no-untyped-def]
                if not state["held"]:
                    state["held"] = True
                    # Anchor released while B is held; A now reads none.
                    life.lifecycle._parent_anchors.pop(
                        (RUN_ID, client.pid, CREATED), None
                    )
                    a_row = life.diagnostics(run)[0]
                    published["row"] = a_row
                    if check_published:
                        self.assertIsNone(a_row["exit_code"])
                        self.assertEqual(
                            a_row["exit_diagnosis"]["classification"], "unknown"  # type: ignore[index]
                        )
                return original(key, observation)

            life.lifecycle._remember_client_observation = holding  # type: ignore[method-assign]
            try:
                b_row = life.diagnostics(run)[0]
            finally:
                life.lifecycle._remember_client_observation = original  # type: ignore[method-assign]
            return life, run, client, b_row, published.get("row")
        except Exception:
            life.close()
            raise

    def test_interleaving_r2_held_reader_then_none_reader(self) -> None:
        life, run, _client, b_row, _a = self._held_reader_interleaving(
            with_action=False, check_published=False
        )
        try:
            self.assertEqual(b_row["exit_code"], 7)
            self.assertEqual(
                b_row["exit_diagnosis"]["classification"], "spontaneous"  # type: ignore[index]
            )
            row = life.diagnostics(run)[0]
            self.assertEqual(row["exit_code"], 7)
            self.assertEqual(
                row["exit_diagnosis"]["classification"], "spontaneous"  # type: ignore[index]
            )
            self.assertNotIn("exit_evidence", row)
            stored = next(iter(life.lifecycle._client_role_observations.values()))
            self.assertNotIn("exit_diagnosis", stored)
        finally:
            life.close()

    def test_interleaving_r3_published_unknown_then_held_reader(self) -> None:
        life, run, _client, _b, _a = self._held_reader_interleaving(
            with_action=True, check_published=True
        )
        try:
            row = life.diagnostics(run)[0]
            self.assertEqual(row["exit_code"], 7)
            self.assertEqual(
                row["exit_diagnosis"]["classification"], "mcp_ordered"  # type: ignore[index]
            )
        finally:
            life.close()

    def test_late_identity_unavailable_then_exited_is_spontaneous(self) -> None:
        anchor_answer: dict[str, object] = {"value": None}
        life, run, client = _dead_life(
            anchor_exit=lambda _t: anchor_answer["value"]  # type: ignore[arg-type]
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            first = life.diagnostics(run)[0]
            self.assertEqual(first["exit_diagnosis"]["classification"], "unknown")  # type: ignore[index]
            life.guard.snapshots[client.pid] = {
                "error": "identity_unavailable",
                "exit_code": 3,
            }
            late = life.diagnostics(run)[0]
            self.assertEqual(late["state"], "dead")
            self.assertEqual(late["exit_diagnosis"]["classification"], "unknown")  # type: ignore[index]
            anchor_answer["value"] = {"exit_code": 7, "exit_time_utc": EXITED}
            life.guard.snapshots[client.pid] = dict(GONE)
            row = life.diagnostics(run)[0]
            self.assertEqual(row["exit_code"], 7)
            self.assertEqual(
                row["exit_diagnosis"]["classification"], "spontaneous"  # type: ignore[index]
            )
        finally:
            life.close()

    def test_retained_dead_row_collects_anchor_after_record_leaves(self) -> None:
        anchor_answer: dict[str, object] = {"value": None}
        life, run, client = _dead_life(
            anchor_exit=lambda _t: anchor_answer["value"]  # type: ignore[arg-type]
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            life.lifecycle._observation_now = lambda: "2026-07-15T00:00:10.000Z"  # type: ignore[method-assign]
            entry = life.lifecycle._begin_client_action(
                RUN_ID, client, "terminate", "stop"
            )
            first = life.diagnostics(run)[0]
            self.assertEqual(first["exit_diagnosis"]["classification"], "unknown")  # type: ignore[index]
            life.lifecycle._end_client_action(RUN_ID, client, entry, "terminated")
            anchor_answer["value"] = {"exit_code": 7, "exit_time_utc": EXITED}
            run.processes = []
            calls_before = len(life.guard.snapshot_calls)
            row = life.diagnostics(run)[0]
            self.assertEqual(len(life.guard.snapshot_calls), calls_before)
            self.assertIs(row["current"], False)
            self.assertEqual(row["exit_code"], 7)
            self.assertEqual(row["exit_time_utc"], EXITED)
            self.assertEqual(
                row["exit_diagnosis"]["classification"], "mcp_ordered"  # type: ignore[index]
            )
        finally:
            life.close()

    def test_bridge_polled_fake_variants(self) -> None:
        for answer, expected in ((True, True), (False, False), (None, None), ("yes", None)):
            life, run, _c = _dead_life()
            try:
                life.lifecycle.bindings = SimpleNamespace(
                    process_polled=lambda *_a, value=answer: value
                )
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                self.assertEqual(diag["bridge_polled"], expected)  # type: ignore[index]
            finally:
                life.close()
        life, run, _c = _dead_life()
        try:
            def boom(*_a: object) -> bool:
                raise RuntimeError("boom")

            life.lifecycle.bindings = SimpleNamespace(process_polled=boom)
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertIsNone(diag["bridge_polled"])  # type: ignore[index]
        finally:
            life.close()

    def test_bridge_first_bool_kept_and_none_reasked(self) -> None:
        life, run, _c = _dead_life()
        try:
            answers: list[object] = [True, False]
            calls: list[int] = []

            def checker(*_a: object):
                calls.append(1)
                return answers.pop(0)

            life.lifecycle.bindings = SimpleNamespace(process_polled=checker)
            self.assertTrue(life.diagnostics(run)[0]["exit_diagnosis"]["bridge_polled"])  # type: ignore[index]
            self.assertTrue(life.diagnostics(run)[0]["exit_diagnosis"]["bridge_polled"])  # type: ignore[index]
            self.assertEqual(len(calls), 1)
        finally:
            life.close()
        life, run, _c = _dead_life()
        try:
            answers = [None, False]
            calls = []

            def checker2(*_a: object):
                calls.append(1)
                return answers.pop(0)

            life.lifecycle.bindings = SimpleNamespace(process_polled=checker2)
            self.assertIsNone(life.diagnostics(run)[0]["exit_diagnosis"]["bridge_polled"])  # type: ignore[index]
            self.assertFalse(life.diagnostics(run)[0]["exit_diagnosis"]["bridge_polled"])  # type: ignore[index]
            self.assertEqual(len(calls), 2)
        finally:
            life.close()

    def test_action_table_keeps_stored_row_identity_over_2x_bound(self) -> None:
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": EXITED}
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            life.lifecycle._observation_now = lambda: "2026-07-15T00:00:10.000Z"  # type: ignore[method-assign]
            entry = life.lifecycle._begin_client_action(
                RUN_ID, client, "wm_close", "close_run"
            )
            life.lifecycle._end_client_action(RUN_ID, client, entry, "posted")
            key = life.lifecycle._client_action_key(RUN_ID, client)
            self.assertEqual(
                life.diagnostics(run)[0]["exit_diagnosis"]["classification"],  # type: ignore[index]
                "mcp_ordered",
            )
            run.processes = []
            life.store.replace(run)
            for index in range(64):
                rec = _record(
                    1000 + index,
                    created=f"2026-07-15T00:01:{index:02d}.000000Z",
                )
                life.lifecycle._begin_client_action(RUN_ID, rec, "terminate", "stop")
            self.assertIn(key, life.lifecycle._client_actions)
            self.assertLessEqual(len(life.lifecycle._client_actions), 64)
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "mcp_ordered")  # type: ignore[index]
        finally:
            life.close()

    def test_observation_drop_drops_actions_folder_and_deferral(self) -> None:
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": EXITED}
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            life.lifecycle._begin_client_action(RUN_ID, client, "terminate", "stop")
            life.diagnostics(run)
            key = life.lifecycle._client_action_key(RUN_ID, client)
            life.lifecycle._client_launch_output_dirs[key] = "some-folder"
            life.lifecycle._client_exit_deferrals[key] = 2
            run.processes = []
            life.store.replace(run)
            generation = life.lifecycle._current_generation()
            for index in range(40):
                other_key = (
                    generation,
                    RUN_ID,
                    2000 + index,
                    f"2026-07-15T00:02:{index:02d}.000000Z",
                )
                life.lifecycle._remember_client_observation(
                    other_key,
                    {
                        "role": "client",
                        "state": "alive",
                        "pid": 2000 + index,
                        "creation_time_utc": other_key[3],
                        "observed_at_utc": "2026-07-15T00:03:00.000Z",
                        "first_observed_dead_at_utc": None,
                        "exit_code": None,
                        "exit_time_utc": None,
                        "current": True,
                    },
                )
            self.assertNotIn(key, life.lifecycle._client_role_observations)
            self.assertNotIn(key, life.lifecycle._client_actions)
            self.assertNotIn(key, life.lifecycle._client_launch_output_dirs)
            self.assertNotIn(key, life.lifecycle._client_exit_deferrals)
        finally:
            life.close()

    def test_ninth_action_evicts_oldest_noncounting(self) -> None:
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": EXITED}
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            life.lifecycle._observation_now = lambda: "2026-07-15T00:00:10.000Z"  # type: ignore[method-assign]
            for _ in range(8):
                entry = life.lifecycle._begin_client_action(
                    RUN_ID, client, "wm_close", "close_run"
                )
                life.lifecycle._end_client_action(
                    RUN_ID, client, entry, "not_posted"
                )
            entry = life.lifecycle._begin_client_action(
                RUN_ID, client, "terminate", "stop"
            )
            self.assertIsNotNone(entry)
            life.lifecycle._end_client_action(RUN_ID, client, entry, "terminated")
            key = life.lifecycle._client_action_key(RUN_ID, client)
            self.assertEqual(len(life.lifecycle._client_actions[key]), 8)
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "mcp_ordered")  # type: ignore[index]
        finally:
            life.close()

    def test_ninth_counting_action_not_stored(self) -> None:
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": EXITED}
        )
        try:
            life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
            life.lifecycle._observation_now = lambda: "2026-07-15T00:00:10.000Z"  # type: ignore[method-assign]
            for _ in range(8):
                entry = life.lifecycle._begin_client_action(
                    RUN_ID, client, "wm_close", "close_run"
                )
                life.lifecycle._end_client_action(RUN_ID, client, entry, "posted")
            ninth = life.lifecycle._begin_client_action(
                RUN_ID, client, "terminate", "stop"
            )
            self.assertIsNone(ninth)
            key = life.lifecycle._client_action_key(RUN_ID, client)
            self.assertEqual(len(life.lifecycle._client_actions[key]), 8)
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "mcp_ordered")  # type: ignore[index]
            self.assertTrue(
                all(a["source"] == "close_run" for a in diag["lifecycle_actions"])  # type: ignore[index]
            )
        finally:
            life.close()


class RptAndLock508aTest(unittest.TestCase):
    def _rpt_life(self, folder: Path, times: dict[str, tuple[float, float]], calls: list[str]):
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 0, "exit_time_utc": EXITED}
        )
        life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
        key = (life.lifecycle._current_generation(), RUN_ID, client.pid, CREATED)
        life.lifecycle._client_launch_output_dirs[key] = str(folder)

        def reader(path: str):
            calls.append(path)
            return times[Path(path).name]

        life.lifecycle._rpt_file_times = reader  # type: ignore[method-assign]
        return life, run, client

    def test_rpt_clean_and_one_attempt(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.rpt").write_text(
                "line one\n--- Termination successfully completed ---\n",
                encoding="utf-8",
            )
            created = _utc_epoch(CREATED)
            exited = _utc_epoch(EXITED)
            assert created is not None and exited is not None
            calls: list[str] = []
            life, run, _c = self._rpt_life(
                folder, {"a.rpt": (created + 1, exited)}, calls
            )
            try:
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                self.assertTrue(diag["rpt_clean_termination"])  # type: ignore[index]
                self.assertTrue(diag["rpt_path"].endswith("a.rpt"))  # type: ignore[index]
                self.assertEqual(diag["rpt_tail"][-1], "--- Termination successfully completed ---")  # type: ignore[index]
                first_calls = len(calls)
                diag2 = life.diagnostics(run)[0]["exit_diagnosis"]
                self.assertEqual(diag2, diag)
                self.assertEqual(len(calls), first_calls)
                self.assertEqual(len(life.guard.snapshot_calls), 2)
            finally:
                life.close()

    def test_rpt_truncated_no_and_newer_older_not_chosen(self) -> None:
        created = _utc_epoch(CREATED)
        exited = _utc_epoch(EXITED)
        assert created is not None and exited is not None
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.rpt").write_text("boom\npartial", encoding="utf-8")
            calls: list[str] = []
            life, run, _c = self._rpt_life(folder, {"a.rpt": (created + 1, exited)}, calls)
            try:
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                self.assertFalse(diag["rpt_clean_termination"])  # type: ignore[index]
            finally:
                life.close()
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            calls = []
            life, run, _c = self._rpt_life(folder, {}, calls)
            try:
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                self.assertIsNone(diag["rpt_path"])  # type: ignore[index]
                self.assertEqual(diag["rpt_tail"], [])  # type: ignore[index]
            finally:
                life.close()
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "new.rpt").write_text("x\n", encoding="utf-8")
            (folder / "old.rpt").write_text("y\n", encoding="utf-8")
            calls = []
            life, run, _c = self._rpt_life(
                folder,
                {
                    "new.rpt": (created + 1, exited + 10),
                    "old.rpt": (created - 10, exited),
                },
                calls,
            )
            try:
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                self.assertIsNone(diag["rpt_path"])  # type: ignore[index]
            finally:
                life.close()

    def test_rpt_bounds_and_redaction(self) -> None:
        created = _utc_epoch(CREATED)
        exited = _utc_epoch(EXITED)
        assert created is not None and exited is not None
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            long_line = "x" * 300
            body = ("pad\n" * 5000) + "\n".join(
                [f"line {i} {long_line}" for i in range(10)]
            )
            (folder / "a.rpt").write_bytes(body.encode("utf-8"))
            calls: list[str] = []
            life, run, _c = self._rpt_life(folder, {"a.rpt": (created + 1, exited)}, calls)
            try:
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                tail = diag["rpt_tail"]  # type: ignore[index]
                self.assertLessEqual(len(tail), 6)
                self.assertTrue(all(len(line) <= 200 for line in tail))
            finally:
                life.close()
        samples = [
            (r"C:\Users\alice\file", r"C:\Users\<user>\file"),
            (r"C:\Users\Alice Smith\log.rpt", r"C:\Users\<user>\log.rpt"),
            ("a@example.com", "<email>"),
            ("123e4567-e89b-12d3-a456-426614174000", "<uuid>"),
            ("76561198000000000", "<steamid>"),
            ("ABCDEFGHIJKLMNOPQRSTUV==", "<id>"),
            ("abcdefabcdefabcdefabcdef12", "<hex>"),
            ("abcdefghijklmnopqrstuvwxyz0123456789ABCD", "<token>"),
            ("127.0.0.1:2302", "<ip>"),
        ]
        for raw, expected in samples:
            self.assertEqual(_redact_diagnostic_text(raw), expected, raw)

    def test_rpt_over_16384_early_and_partial_dropped_and_bang_cut(self) -> None:
        created = _utc_epoch(CREATED)
        exited = _utc_epoch(EXITED)
        assert created is not None and exited is not None
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            final = ["end one", "!" * 201, "end three", "--- Termination successfully completed ---"]
            final_text = "\n".join(final) + "\n"
            partial_line = "partial fragment here\n"
            filler_len = (
                16384 - len(partial_line.encode("utf-8")) - 1
                - len(final_text.encode("utf-8"))
            )
            body = (
                "EARLY_UNIQUE_LINE\n" + partial_line
                + ("y" * filler_len) + "\n" + final_text
            )
            raw = body.encode("utf-8")
            self.assertGreater(len(raw), 16384)
            self.assertTrue(
                raw[len(raw) - 16384:].startswith(b"partial fragment here")
            )
            (folder / "a.rpt").write_bytes(raw)
            calls: list[str] = []
            life, run, _c = self._rpt_life(folder, {"a.rpt": (created + 1, exited)}, calls)
            try:
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                tail = diag["rpt_tail"]  # type: ignore[index]
                self.assertEqual(
                    tail,
                    [
                        "<token>",
                        "end one",
                        "!" * 200,
                        "end three",
                        "--- Termination successfully completed ---",
                    ],
                )
                self.assertTrue(all("EARLY_UNIQUE_LINE" not in line for line in tail))
                self.assertTrue(all("partial fragment here" not in line for line in tail))
                self.assertTrue(all(len(line) <= 200 for line in tail))
            finally:
                life.close()

    def test_rpt_miss_is_one_attempt_counting_scan(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            calls: list[str] = []
            life, run, _c = self._rpt_life(folder, {}, calls)
            try:
                scans: list[str] = []
                original = life.lifecycle._client_rpt_evidence

                def counting_scan(f: str, row: dict[str, object]):
                    scans.append(f)
                    return original(f, row)

                life.lifecycle._client_rpt_evidence = counting_scan  # type: ignore[method-assign]
                diag = life.diagnostics(run)[0]["exit_diagnosis"]
                self.assertIsNone(diag["rpt_path"])  # type: ignore[index]
                self.assertEqual(len(scans), 1)
                life.diagnostics(run)
                self.assertEqual(len(scans), 1)
            finally:
                life.close()

    def test_rpt_two_collectors_race_one_scan(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.rpt").write_text("hello\n", encoding="utf-8")
            created = _utc_epoch(CREATED)
            exited = _utc_epoch(EXITED)
            assert created is not None and exited is not None
            calls: list[str] = []
            life, run, _c = self._rpt_life(
                folder, {"a.rpt": (created + 1, exited)}, calls
            )
            try:
                entered = threading.Event()
                release = threading.Event()
                scans: list[str] = []
                original = life.lifecycle._client_rpt_evidence

                def blocking_scan(f: str, row: dict[str, object]):
                    scans.append(f)
                    entered.set()
                    release.wait(timeout=5)
                    return original(f, row)

                life.lifecycle._client_rpt_evidence = blocking_scan  # type: ignore[method-assign]
                errors: list[BaseException] = []

                def first() -> None:
                    try:
                        life.diagnostics(run)
                    except BaseException as exc:  # noqa: BLE001
                        errors.append(exc)

                thread = threading.Thread(target=first)
                thread.start()
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    second = life.diagnostics(run)[0]
                    self.assertEqual(second["state"], "dead")
                    self.assertEqual(len(scans), 1)
                finally:
                    release.set()
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(scans), 1)
            finally:
                life.close()

    def test_behavioural_launch_records_folder_and_rpt(self) -> None:
        fixture = LifecycleFixture()
        try:
            profiles = Path(fixture.temporary.name) / "client-profiles"
            profiles.mkdir()
            launched_record = lifecycle_record(fixture.launcher.handle.pid)
            fixture.guard.snapshots[fixture.launcher.handle.pid] = lifecycle_identity(
                launched_record
            )
            request = fixture.request()
            request["argv"] = [
                *request["argv"],  # type: ignore[list-item]
                f"-profiles={profiles}",
            ]
            result = fixture.lifecycle.start_run(
                LIFECYCLE_IDENTITY, fixture.token, request
            )
            self.assertTrue(result.get("ok") or result.get("run_id"), result)
            run_id = str(result.get("run_id") or "run-1")
            run = fixture.store.get(run_id)
            self.assertIsNotNone(run)
            assert run is not None
            client_records = [r for r in run.processes if r.role == "client"]
            self.assertTrue(client_records, run)
            record = client_records[0]
            exit_at = "2026-07-15T00:01:23.000000Z"
            fixture.lifecycle.anchor_exit = lambda _t: {  # type: ignore[method-assign]
                "exit_code": 7,
                "exit_time_utc": exit_at,
            }
            fixture.lifecycle._parent_anchors[
                (run_id, record.pid, record.creation_time_utc)
            ] = object()
            (profiles / "client.rpt").write_text(
                "boot\n--- Termination successfully completed ---\n",
                encoding="utf-8",
            )
            created_epoch = _utc_epoch(record.creation_time_utc)
            exit_epoch = _utc_epoch(exit_at)
            assert created_epoch is not None and exit_epoch is not None
            fixture.lifecycle._rpt_file_times = (  # type: ignore[method-assign]
                lambda _p: (created_epoch + 1, exit_epoch)
            )
            fixture.guard.snapshots[record.pid] = dict(GONE)
            rows = fixture.lifecycle._client_role_diagnostics(run)
            dead = [r for r in rows if r.get("pid") == record.pid]
            self.assertTrue(dead, rows)
            diag = dead[0]["exit_diagnosis"]
            self.assertIsNotNone(diag)
            self.assertTrue(diag["rpt_path"].endswith("client.rpt"))  # type: ignore[index]
            self.assertTrue(diag["rpt_clean_termination"])  # type: ignore[index]
        finally:
            fixture.close()

    def test_anchor_lock_covers_accesses(self) -> None:
        life = _Life()
        try:
            client = _record(802)
            run = life.add_run(client)
            life.guard.snapshots[client.pid] = snapshot(client)
            token = object()
            life.lifecycle.parent_anchor = (lambda _pid: token, lambda _t: None)
            records: list[tuple[str, bool]] = []
            lifecycle = life.lifecycle

            class RecDict(dict):
                def get(self, *a, **k):  # type: ignore[no-untyped-def]
                    records.append(("get", lifecycle._anchor_lock.locked()))
                    return super().get(*a, **k)

                def __setitem__(self, k, v):  # type: ignore[no-untyped-def]
                    records.append(("set", lifecycle._anchor_lock.locked()))
                    return super().__setitem__(k, v)

                def pop(self, *a, **k):  # type: ignore[no-untyped-def]
                    records.append(("pop", lifecycle._anchor_lock.locked()))
                    return super().pop(*a, **k)

                def __contains__(self, k):  # type: ignore[no-untyped-def]
                    records.append(("contains", lifecycle._anchor_lock.locked()))
                    return super().__contains__(k)

            rec = RecDict()
            lifecycle._parent_anchors = rec  # type: ignore[assignment]
            lifecycle.anchor_exit = lambda _t: {"exit_code": 1, "exit_time_utc": EXITED}
            lifecycle._anchor_parents_locked(run)
            self.assertTrue(records and all(flag for _op, flag in records), records)
            records.clear()
            kind, _info = lifecycle._read_anchor_exit((RUN_ID, client.pid, CREATED))
            self.assertEqual(kind, "exited")
            self.assertTrue(records and all(flag for _op, flag in records), records)
            records.clear()
            run.state = "EXITED"
            run.processes = []
            life.store.replace(run)
            lifecycle._release_parent_anchors_locked()
            self.assertTrue(
                any(op == "pop" and flag for op, flag in records), records
            )
            self.assertTrue(all(flag for _op, flag in records), records)
        finally:
            life.close()


class ServerStatePolled508aTest(unittest.TestCase):
    def test_process_polled(self) -> None:
        state = loopback.ServerState("k")
        created = "2026-08-18T00:00:01.000000Z"
        state.install_bound_peer(
            instance=INST, role="client", pid=2001, run_id="run-x",
            creation_time_utc=created,
        )
        self.assertFalse(state.process_polled("run-x", 2001, created))
        self.assertIsNone(state.process_polled("run-x", 9999, created))
        self.assertIsNone(state.process_polled("nope", 2001, created))
        state.record_poll(
            "client", instance=INST, source_pid=2001,
            source_creation_time="1999-01-01T00:00:00.000000Z",
        )
        self.assertFalse(state.process_polled("run-x", 2001, created))
        state2 = loopback.ServerState("k")
        state2.install_bound_peer(
            instance=INST, role="client", pid=2001, run_id="run-x",
            creation_time_utc=created,
        )
        state2.record_poll("client", instance=INST, source_pid=2001)
        self.assertTrue(state2.process_polled("run-x", 2001, created))
        empty = loopback.ServerState("k")
        self.assertIsNone(empty.process_polled("run-x", 2001, created))


class _FakeWin32:
    def __init__(self) -> None:
        self.windows: list[tuple[int, int, bool]] = []
        self.posts: list[tuple[int, int, int, int]] = []

    def as_fns(self) -> Win32WindowFns:
        def enum_windows(callback):  # type: ignore[no-untyped-def]
            for hwnd, _pid, _vis in list(self.windows):
                if not callback(hwnd):
                    return False
            return True

        def window_pid(hwnd: int) -> int:
            return next((pid for h, pid, _v in self.windows if h == hwnd), 0)

        def is_visible(hwnd: int) -> bool:
            return next((vis for h, _p, vis in self.windows if h == hwnd), False)

        def post_message(hwnd: int, msg: int, w: int, l: int) -> bool:
            self.posts.append((hwnd, msg, w, l))
            return True

        return Win32WindowFns(
            enum_windows=enum_windows, window_pid=window_pid,
            is_visible=is_visible, post_message=post_message,
        )


class RealAction508aTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.game = root / "DayZ"
        self.game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe", "DayZServer_x64.exe"):
            (self.game / name).write_bytes(b"")
        paths = RuntimePaths(
            root / "runtime", root / "runtime" / "audit",
            root / "runtime" / "coordination.json", root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=self.audit
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.win32 = _FakeWin32()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(), coordinator=self.coordinator,
            manifest=self.store, audit=self.audit, guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game, launcher=FakeLauncher(), id_fn=lambda: "run-1",
        )
        self.lifecycle.window_fns = self.win32.as_fns()  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(self, *records: ProcessRecord) -> RunRecord:
        run = RunRecord(
            RUN_ID, "A", "lease-A", "RUNNING", "same", "@SameMod",
            "profiles", "mission", list(records),
        )
        self.store.add(run)
        stamp_launcher(self.lifecycle, run.run_id, IDENTITY_A)
        return run

    def _actions(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for items in self.lifecycle._client_actions.values():
            out.extend(items)
        return out

    def test_close_run_records_client_only(self) -> None:
        client = _record(41, "client")
        server = _record(42, "server")
        self.add_run(server, client)
        self.guard.snapshots[client.pid] = snapshot(client)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.win32.windows = [(100, server.pid, True), (101, client.pid, True)]
        result = self.lifecycle.close_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertEqual(result.get("run_id"), RUN_ID)
        actions = self._actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "wm_close")
        self.assertEqual(actions[0]["source"], "close_run")
        self.assertEqual(actions[0]["outcome"], "posted")

    def test_stop_records_terminate(self) -> None:
        client = _record(41, "client")
        server = _record(42, "server")
        self.add_run(server, client)
        self.guard.snapshots[client.pid] = snapshot(client)
        self.guard.snapshots[server.pid] = snapshot(server)
        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        self.assertTrue(result.get("ok") in (True, None) or result.get("state") == "EXITED", result)
        actions = [
            a for a in self._actions() if a.get("action") == "terminate"
        ]
        self.assertTrue(
            any(a.get("source") == "stop" and a.get("outcome") == "terminated" for a in actions),
            actions,
        )

    def test_stop_process_not_found_is_failed_not_counting(self) -> None:
        client = _record(41, "client")
        server = _record(42, "server")
        self.add_run(server, client)
        self.guard.snapshots[client.pid] = snapshot(client)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.terminate_results = [dict(GONE), dict(GONE), dict(GONE), dict(GONE)]
        self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)
        actions = [a for a in self._actions() if a.get("action") == "terminate"]
        self.assertTrue(actions)
        self.assertTrue(all(a.get("outcome") == "failed" for a in actions), actions)


class _FakeLaunched:
    def __init__(self, *, terminate_raises: bool = False, wait_raises: bool = False, poll_value: object = 0) -> None:
        self.terminate_raises = terminate_raises
        self.wait_raises = wait_raises
        self.poll_value = poll_value
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_raises:
            raise RuntimeError("terminate failed")

    def wait(self, timeout: float = 0):  # type: ignore[no-untyped-def]
        if self.wait_raises:
            raise RuntimeError("wait failed")
        return 0

    def poll(self):  # type: ignore[no-untyped-def]
        return self.poll_value


class CancelAction508aTest(unittest.TestCase):
    def _cancel_life(self):
        life, run, client = _dead_life(
            anchor_exit=lambda _t: {"exit_code": 1, "exit_time_utc": EXITED}
        )
        life.lifecycle._parent_anchors[(RUN_ID, client.pid, CREATED)] = object()
        life.lifecycle._observation_now = lambda: "2026-07-15T00:00:10.000Z"  # type: ignore[method-assign]
        return life, run, client

    def _cancel_outcome(self, life) -> object:  # type: ignore[no-untyped-def]
        actions = [
            a
            for items in life.lifecycle._client_actions.values()
            for a in items
            if a.get("source") == "cancel"
        ]
        self.assertEqual(len(actions), 1, actions)
        return actions[0].get("outcome")

    def test_cancel_wait_ok_is_terminated(self) -> None:
        life, _run, client = self._cancel_life()
        try:
            result = life.lifecycle._terminate_open_handle(
                _FakeLaunched(), run_id=RUN_ID, record=client
            )
            self.assertTrue(result)
            self.assertEqual(self._cancel_outcome(life), "terminated")
        finally:
            life.close()

    def test_cancel_wait_raises_poll_exited_is_terminated_and_true(self) -> None:
        life, _run, client = self._cancel_life()
        try:
            result = life.lifecycle._terminate_open_handle(
                _FakeLaunched(wait_raises=True, poll_value=1),
                run_id=RUN_ID,
                record=client,
            )
            self.assertTrue(result)
            self.assertEqual(self._cancel_outcome(life), "terminated")
        finally:
            life.close()

    def test_cancel_wait_raises_poll_running_is_unconfirmed(self) -> None:
        life, _run, client = self._cancel_life()
        try:
            result = life.lifecycle._terminate_open_handle(
                _FakeLaunched(wait_raises=True, poll_value=None),
                run_id=RUN_ID,
                record=client,
            )
            self.assertFalse(result)
            self.assertEqual(self._cancel_outcome(life), "unconfirmed")
        finally:
            life.close()

    def test_cancel_terminate_raises_is_failed(self) -> None:
        life, _run, client = self._cancel_life()
        try:
            life.lifecycle._terminate_open_handle(
                _FakeLaunched(terminate_raises=True, poll_value=1),
                run_id=RUN_ID,
                record=client,
            )
            self.assertEqual(self._cancel_outcome(life), "failed")
        finally:
            life.close()

    def test_cancel_action_before_exit_reads_mcp_ordered(self) -> None:
        life, run, client = self._cancel_life()
        try:
            life.lifecycle._terminate_open_handle(
                _FakeLaunched(), run_id=RUN_ID, record=client
            )
            diag = life.diagnostics(run)[0]["exit_diagnosis"]
            self.assertEqual(diag["classification"], "mcp_ordered")  # type: ignore[index]
            self.assertTrue(
                any(a.get("source") == "cancel" for a in diag["lifecycle_actions"])  # type: ignore[index]
            )
        finally:
            life.close()

    def test_settle_failed_launch_records_cancel(self) -> None:
        life = _Life()
        try:
            client = _record(802)
            run = life.add_run(client)
            result = life.lifecycle._settle_failed_launch(
                client=LIFECYCLE_IDENTITY,
                previous=None,
                provisional=run,
                launched=_FakeLaunched(),
                record=client,
                confirmed_error="launch_cancelled",
            )
            self.assertIsInstance(result, dict)
            self.assertEqual(self._cancel_outcome(life), "terminated")
        finally:
            life.close()


class Wiring508aTest(unittest.TestCase):
    def test_daemon_wiring_and_changelog(self) -> None:
        daemon = (_TOOLS_DIR / "dayz_mcp" / "daemon.py").read_text(encoding="utf-8")
        self.assertIn("anchor_exit=orphan_guard.read_process_exit", daemon)
        changelog = (_TOOLS_DIR.parent / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("fb-20261112-194514-508a", changelog)
        self.assertIn("client_exit", changelog)


class _SeqRuntime(_Runtime):
    def __init__(
        self,
        statuses: list[dict[str, object]],
        events: list[str] | None = None,
    ) -> None:
        super().__init__(lifecycle=statuses[0] if statuses else {"runs": []})
        self._statuses = list(statuses)
        self._events = events

    async def lifecycle_status(self) -> dict[str, object]:
        self.lifecycle_calls += 1
        if self._events is not None:
            self._events.append("status")
        if self._statuses:
            return self._statuses.pop(0)
        return self.lifecycle  # type: ignore[return-value]


def _status(client_pid: object, *, diag: object = "spontaneous") -> dict[str, object]:
    processes: list[dict[str, object]] = [{"role": "server", "pid": os.getpid()}]
    if client_pid is not None:
        processes.append({"role": "client", "pid": client_pid})  # type: ignore[dict-item]
    row: dict[str, object] = {
        "run_id": TOOL_RUN_ID,
        "processes": processes,
        "state": "RUNNING",
        "mod": "@ExampleMod",
        "profiles": r"P:\ExampleMod_Suite\_server\profiles",
    }
    if isinstance(client_pid, int):
        diagnosis = None
        if diag == "spontaneous":
            diagnosis = {
                "classification": "spontaneous",
                "lifetime_s": 23.0,
                "close_posted": False,
                "lifecycle_actions": [],
                "bridge_polled": False,
                "rpt_path": None,
                "rpt_clean_termination": None,
                "rpt_tail": [],
            }
        elif isinstance(diag, dict):
            diagnosis = diag
        row["client_diagnostics"] = [
            {
                "current": True,
                "pid": client_pid,
                "state": "dead" if client_pid == 999_999_999 else "alive",
                "exit_code": 3 if client_pid == 999_999_999 else None,
                "exit_time_utc": EXITED if client_pid == 999_999_999 else None,
                "exit_diagnosis": diagnosis,
            }
        ]
    return {"runs": [row]}


class DayzTestRun508aTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for target, value in (
            (
                "evaluate_steam_session",
                steam_preflight.SteamSessionResult(
                    error_code=None, steam_registered_pid=1,
                    steam_live_pids=(1,), remediation=steam_preflight.REMEDIATION,
                ),
            ),
            (
                "preflight_vpp_request",
                native_launcher_transaction.VppPreflightResult(
                    error_code=None, missing=(), warnings=(),
                    hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
                ),
            ),
        ):
            patcher = patch.object(dayz_test_tool, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        desktop = patch.object(
            dayz_test_tool, "evaluate_prerun_desktop",
            return_value=mcp_capture.PrerunDesktopResult(
                error_code=None, desktop="unlocked", mean_brightness=80.0,
                nonblack_ratio=0.9, waited_s=0.01, remediation="",
            ),
        )
        desktop.start()
        self.addCleanup(desktop.stop)

    async def _run(self, runtime: _Runtime, mode: str = "all", run_id: str | None = None):
        policy = _policy()

        async def launch(_raw: bytes, **kwargs: object) -> int:
            runtime.lifecycle_calls = 0
            await kwargs["execution_started_cb"]()  # type: ignore[misc]
            kwargs["output_sink"](  # type: ignore[misc]
                "stdout",
                _terminal({
                    "cleanup_degraded": False, "error_code": None,
                    "exit_code": 0, "ok": True, "run_id": TOOL_RUN_ID,
                }),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher, "execute_secure_launcher_request",
            side_effect=launch,
        ):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime, project="ExampleMod", mode=mode, run_id=run_id,
                extra_mods=["@DayZ_MCP"],
            )

    async def test_dead_at_first_read(self) -> None:
        runtime = _SeqRuntime([_status(999_999_999)])
        result = await self._run(runtime)
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertEqual(result["client_exit"]["pid"], 999_999_999)  # type: ignore[index]
        self.assertEqual(result["client_exit"]["classification"], "spontaneous")  # type: ignore[index]
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_dead_after_boot_wait(self) -> None:
        runtime = _SeqRuntime([_status(os.getpid()), _status(999_999_999)])
        result = await self._run(runtime)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertIsNotNone(result["client_exit"])
        self.assertEqual(runtime.bridge_calls, 1)
        self.assertEqual(runtime.lifecycle_calls, 2)

    async def test_alive_at_both(self) -> None:
        runtime = _SeqRuntime([_status(os.getpid()), _status(os.getpid())])
        result = await self._run(runtime)
        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["client_exit"])

    async def test_dead_row_without_diagnosis_unknown(self) -> None:
        runtime = _SeqRuntime([_status(999_999_999, diag=None)])
        result = await self._run(runtime)
        self.assertEqual(result["client_exit"]["classification"], "unknown")  # type: ignore[index]
        self.assertEqual(result["client_exit"]["exit_code"], 3)  # type: ignore[index]
        self.assertEqual(result["client_exit"]["lifecycle_actions"], [])  # type: ignore[index]

    async def test_client_mode_no_reread(self) -> None:
        extension = _status(999_999_999)
        extension["runs"][0]["state"] = "RUNNING_IDLE"  # type: ignore[index]
        runtime = _SeqRuntime(
            [extension, _status(os.getpid()), _status(999_999_999)]
        )
        result = await self._run(runtime, mode="client", run_id=TOOL_RUN_ID)
        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["client_exit"])
        self.assertEqual(runtime.lifecycle_calls, 1)

    async def test_post_boot_reread_after_boot_collection(self) -> None:
        events: list[str] = []
        runtime = _SeqRuntime(
            [_status(os.getpid()), _status(os.getpid())], events=events
        )

        async def fake_boot(**_kwargs: object) -> None:
            events.append("boot")
            return None

        with patch.object(
            dayz_test_tool, "_collect_server_boot", new=fake_boot
        ):
            result = await self._run(runtime, mode="all")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(events, ["status", "boot", "status"])

    async def test_none_then_alive_recomputes_readiness(self) -> None:
        runtime = _SeqRuntime([_status(None), _status(os.getpid())])
        result = await self._run(runtime)
        self.assertEqual(result["status"], "succeeded")
        self.assertIs(result["client_alive"], True)
        self.assertEqual(result["process_alive"], result["client_alive"])
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_none_then_dead_after_boot(self) -> None:
        first = _status(None)
        runtime = _SeqRuntime([first, _status(999_999_999)])
        result = await self._run(runtime)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertIsNotNone(result["client_exit"])
        self.assertEqual(runtime.bridge_calls, 1)


class StopReply508aTest(unittest.IsolatedAsyncioTestCase):
    def test_direct_stop_envelope_has_no_client_exit(self) -> None:
        envelope = dayz_test_tool._direct_stop_envelope(
            TOOL_RUN_ID, {"ok": True}, {"mod": "@X"}, 0.0
        )
        self.assertNotIn("client_exit", envelope)

    async def test_stop_through_execute_request_has_no_client_exit(self) -> None:
        run = {
            "run_id": TOOL_RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@ExampleMod",
            "profiles": r"P:\ExampleMod_Suite\_client\profiles",
        }
        runtime = _Runtime({"runs": [run]})

        async def launch(_raw: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()  # type: ignore[misc]
            kwargs["output_sink"](  # type: ignore[misc]
                "stdout",
                _terminal({
                    "cleanup_degraded": False, "error_code": None,
                    "exit_code": 0, "ok": True, "run_id": TOOL_RUN_ID,
                }),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle",
            return_value=_Bundle(_sealed(_policy())),
        ), patch.object(
            dayz_test_tool.secure_launcher, "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(
                runtime, TOOL_RUN_ID
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "succeeded")
        self.assertNotIn("client_exit", result)


class ClientExitProjection508aTest(unittest.TestCase):
    def test_projection_typechecks(self) -> None:
        status = _status(999_999_999, diag={
            "classification": "bogus", "lifetime_s": "x", "close_posted": 1,
            "lifecycle_actions": [{"action": "wm_close", "source": "close_run", "at_utc": "t", "outcome": "posted", "extra": 1}],
            "bridge_polled": True, "rpt_path": "p", "rpt_clean_termination": True,
            "rpt_tail": ["a" * 300],
        })
        out = dayz_test_tool._client_exit_from_status(status, TOOL_RUN_ID)
        self.assertIsNotNone(out)
        self.assertEqual(out["classification"], "unknown")  # type: ignore[index]
        self.assertIsNone(out["lifetime_s"])  # type: ignore[index]
        self.assertEqual(len(out["rpt_tail"][0]), 200)  # type: ignore[index]
        self.assertNotIn("extra", out["lifecycle_actions"][0])  # type: ignore[index]

    def test_projection_malformed_containers_never_raise(self) -> None:
        def fresh() -> dict[str, object]:
            return _status(999_999_999)

        def run_of(status: dict[str, object]) -> dict[str, object]:
            return status["runs"][0]  # type: ignore[index]

        def diag_row(status: dict[str, object]) -> dict[str, object]:
            return run_of(status)["client_diagnostics"][0]  # type: ignore[index]

        none_cases: list[dict[str, object]] = []
        none_cases.append({"runs": 1})
        none_cases.append({"runs": [[]]})
        status = fresh()
        run_of(status)["processes"] = 1
        none_cases.append(status)
        status = fresh()
        run_of(status)["processes"] = [{"role": "client", "pid": True}]
        none_cases.append(status)
        status = fresh()
        run_of(status)["client_diagnostics"] = 1
        none_cases.append(status)
        status = fresh()
        run_of(status)["client_diagnostics"] = ["row-as-str"]
        none_cases.append(status)
        for status in none_cases:
            self.assertIsNone(
                dayz_test_tool._client_exit_from_status(status, TOOL_RUN_ID),
                status,
            )
        status = fresh()
        diag_row(status)["exit_diagnosis"] = []
        out = dayz_test_tool._client_exit_from_status(status, TOOL_RUN_ID)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["classification"], "unknown")
        self.assertEqual(out["lifecycle_actions"], [])
        self.assertEqual(out["rpt_tail"], [])
        self.assertIsNone(out["lifetime_s"])
        status = fresh()
        diag_row(status)["exit_diagnosis"] = {
            "classification": "spontaneous",
            "lifetime_s": 1.0,
            "close_posted": False,
            "lifecycle_actions": 1,
            "bridge_polled": False,
            "rpt_path": None,
            "rpt_clean_termination": None,
            "rpt_tail": 1,
        }
        out = dayz_test_tool._client_exit_from_status(status, TOOL_RUN_ID)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["lifecycle_actions"], [])
        self.assertEqual(out["rpt_tail"], [])

    def test_projection_list_classification_is_unknown(self) -> None:
        status = _status(999_999_999, diag={"classification": []})
        out = dayz_test_tool._client_exit_from_status(status, TOOL_RUN_ID)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["classification"], "unknown")
