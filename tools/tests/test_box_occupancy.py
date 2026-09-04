from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server as server_module
from dayz_mcp.dayz_test_worker import _pre_admission_rejection
from dayz_mcp.process_lifecycle import (
    _BoxProbes,
    _BoxSnapshot,
    _derive_box,
    occupancy_error_fields,
    parse_dayz_launch_argv,
)
from dayz_mcp.server import BOX_WAIT_MAX_S, ServerConfig
from dayz_mcp.session_coordination import (
    BOX_CLAIM_TTL_S,
    MAX_SESSION_QUEUE,
    SESSION_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)
from tests.test_mcp_tools import _content_json
from tests.test_process_lifecycle import (
    IDENTITY_A,
    HASH_A,
    HASH_B,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
    _utc_epoch,
)
from dayz_mcp.runtime_state import JsonlAuditWriter, RuntimePaths
from dayz_mcp import loopback
from tests.fence_helpers import bind_both_peers
from tempfile import TemporaryDirectory


IDENTITY_WAITER = ClientIdentity(
    "claude", 33, 3, "2026-07-15T00:00:02Z", "waiter-session", "box"
)
IDENTITY_SECOND = ClientIdentity(
    "codex", 44, 4, "2026-07-15T00:00:03Z", "second-session", "box"
)


def _argv_lookup(mapping: dict[int, list[str]]):
    def reader(pid: int) -> list[str] | None:
        return mapping.get(pid)

    return reader


class ParseDayzLaunchArgvTest(unittest.TestCase):
    def test_extracts_port_mods_and_profiles(self) -> None:
        parsed = parse_dayz_launch_argv(
            [
                r"C:\DayZ\DayZDiag_x64.exe",
                "-server",
                "-port=2402",
                "-mod=@LFHeli;P:\\Mods\\@DayZ_MCP",
                r"-profiles=P:\proj\_server\profiles",
            ]
        )
        self.assertEqual(parsed["ports"], [2402])
        self.assertEqual(parsed["mods"], ["@LFHeli", "@DayZ_MCP"])
        self.assertEqual(parsed["profiles"], r"P:\proj\_server\profiles")

    def test_accepts_split_port_flag(self) -> None:
        parsed = parse_dayz_launch_argv(["DayZDiag_x64.exe", "-port", "2302"])
        self.assertEqual(parsed["ports"], [2302])


class BoxOccupancyTest(unittest.TestCase):
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
            argv_of=_argv_lookup(self.argv),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self) -> dict[str, object]:
        return {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
        }

    def add_run(self, record, *, owner: str | None = "A", state: str = "RUNNING"):
        from dayz_mcp.process_lifecycle import RunRecord

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

    def test_free_box_when_no_runs_and_empty_diag(self) -> None:
        box = self.lifecycle.box_occupancy()
        self.assertFalse(box["occupied"])
        self.assertEqual(box["runs"], [])
        self.assertEqual(box["foreign"], [])
        self.assertEqual(box["ports_in_use"], [])

    def test_managed_run_is_listed_with_owner_and_port(self) -> None:
        record = process(801, role="server")
        self.add_run(record)
        self.argv[801] = [
            "DayZDiag_x64.exe",
            "-server",
            "-port=2302",
            "-mod=@SameMod",
            "-profiles=profiles",
        ]

        box = self.lifecycle.box_occupancy()

        self.assertTrue(box["occupied"])
        self.assertEqual(len(box["runs"]), 1)
        run = box["runs"][0]
        self.assertEqual(run["run_id"], "run-existing")
        self.assertEqual(run["mod"], "@SameMod")
        self.assertEqual(run["label"], "same")
        self.assertEqual(run["owner_session"], "A")
        self.assertGreaterEqual(run["age_s"], 0.0)
        self.assertEqual(box["foreign"], [])
        self.assertEqual(box["ports_in_use"], [2302])

    def test_start_run_invalidates_empty_occupancy_cache(self) -> None:
        empty = self.lifecycle.box_occupancy()
        self.assertFalse(empty["occupied"])
        self.assertEqual(empty["runs"], [])
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        started = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.request()
        )
        self.assertTrue(started.get("ok"), started)
        cached = self.lifecycle.box_occupancy()
        self.assertTrue(cached["occupied"])
        self.assertTrue(cached["runs"])

    def test_starting_provisional_invalidates_empty_occupancy_cache(self) -> None:
        empty = self.lifecycle.box_occupancy()
        self.assertFalse(empty["occupied"])
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        puerta = threading.Event()
        dentro = threading.Event()
        original = self.lifecycle.launcher

        def _slow_launcher(*args, **kwargs):
            dentro.set()
            self.assertTrue(puerta.wait(5.0))
            return original(*args, **kwargs)

        self.lifecycle.launcher = _slow_launcher
        result: dict[str, object] = {}

        def _start() -> None:
            result["started"] = self.lifecycle.start_run(
                IDENTITY_A, self.token_a, self.request()
            )

        hilo = threading.Thread(target=_start, daemon=True)
        hilo.start()
        self.assertTrue(dentro.wait(5.0))
        starting = [
            run for run in self.store.list_runs() if run.state == "STARTING"
        ]
        self.assertTrue(starting)
        cached = self.lifecycle.box_occupancy()
        puerta.set()
        hilo.join(timeout=5)
        self.assertTrue(cached["occupied"], cached)
        self.assertEqual(cached["runs"][0]["state"], "STARTING")
        self.assertTrue(result.get("started", {}).get("ok"), result)

    def test_foreign_diag_is_listed_with_pid_port_and_mods(self) -> None:
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": 909, "name": "DayZDiag_x64.exe"}],
        }
        self.argv[909] = [
            "DayZDiag_x64.exe",
            "-server",
            "-port=2402",
            "-mod=@ForzaDayZ;@DayZ_MCP",
            r"-profiles=C:\foreign\profiles",
        ]

        box = self.lifecycle.box_occupancy()

        self.assertTrue(box["occupied"])
        self.assertEqual(box["runs"], [])
        self.assertEqual(
            box["foreign"],
            [
                {
                    "port": 2402,
                    "mods": ["@ForzaDayZ", "@DayZ_MCP"],
                    "profiles": "foreign",
                }
            ],
        )
        self.assertNotIn("pid", json.dumps(box))
        self.assertNotIn("C:\\", json.dumps(box))
        self.assertEqual(box["ports_in_use"], [2402])

    def test_unknown_diag_snapshot_is_occupied_fail_closed(self) -> None:
        self.lifecycle.diag_probe = None
        box = self.lifecycle.box_occupancy()
        self.assertTrue(box["occupied"])
        self.assertEqual(box["foreign"], [])

    def test_start_rejection_wire_is_only_error_for_managed_run(self) -> None:
        # RED if _start_rejection grows keys: sealed worker requires {error}.
        registered = process(810)
        self.add_run(registered)

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        public = {key: value for key, value in result.items() if key != "_http_status"}
        self.assertEqual(public, {"error": "active_run_exists"})
        self.assertEqual(_pre_admission_rejection(public), "active_run_exists")
        fields = occupancy_error_fields(
            self.lifecycle.box_occupancy(), caller_session="other"
        )
        self.assertEqual(fields["occupied_by_run_id"], "run-existing")
        self.assertEqual(fields["hint"], "retry with wait_for_box_s=<n>")

    def test_start_rejection_wire_is_only_error_for_foreign_diag(self) -> None:
        # RED if foreign occupancy is stuffed into the lifecycle start body.
        self.lifecycle.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": 777, "name": "DayZDiag_x64.exe"}],
        }
        self.argv[777] = ["DayZDiag_x64.exe", "-port=2502", "-mod=@Other"]

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        public = {key: value for key, value in result.items() if key != "_http_status"}
        self.assertEqual(public, {"error": "active_run_exists"})
        self.assertEqual(_pre_admission_rejection(public), "active_run_exists")
        fields = occupancy_error_fields(self.lifecycle.box_occupancy())
        self.assertTrue(fields["foreign"])
        self.assertNotIn("pid", fields)
        self.assertEqual(fields["port"], 2502)

    def test_box_occupancy_is_cached_briefly(self) -> None:
        # RED if every box_occupancy() call rescans processes.
        calls = {"n": 0}

        def probe() -> dict[str, object]:
            calls["n"] += 1
            return {"known": True, "processes": []}

        self.lifecycle.diag_probe = probe
        self.lifecycle.box_occupancy()
        self.lifecycle.box_occupancy()
        self.assertEqual(calls["n"], 1)

    def test_start_rejects_when_another_session_claimed_the_box(self) -> None:
        # RED if start_run ignores a live box claim from another session.
        joined = self.coordinator.box_wait_touch(IDENTITY_WAITER)
        self.coordinator.box_wait_touch(
            IDENTITY_WAITER, joined["box_ticket"], claim=True
        )
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        public = {key: value for key, value in result.items() if key != "_http_status"}
        self.assertEqual(public, {"error": "active_run_exists"})
        self.assertEqual(self.launcher.calls, [])

    def test_start_allows_the_claiming_session(self) -> None:
        # RED if the claim owner cannot launch after becoming head.
        joined = self.coordinator.box_wait_touch(IDENTITY_A)
        self.coordinator.box_wait_touch(IDENTITY_A, joined["box_ticket"], claim=True)
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        self.assertTrue(result.get("ok"))


class BoxHeadHelperTest(unittest.TestCase):
    def test_empty_queue_is_not_head(self) -> None:
        # RED if _box_head_is treats a missing queue as permission to launch.
        self.assertFalse(
            server_module._box_head_is({"queue": []}, "waiter-session")
        )
        self.assertFalse(server_module._box_head_is({"queue": None}, "waiter-session"))


class OccupancyErrorFieldsTest(unittest.TestCase):
    def test_managed_shape(self) -> None:
        fields = occupancy_error_fields(
            {
                "occupied": True,
                "runs": [
                    {
                        "run_id": "abc",
                        "mod": "@M",
                        "label": "lab",
                        "age_s": 12.5,
                    }
                ],
                "foreign": [],
            }
        )
        self.assertEqual(fields["occupied_by_run_id"], "abc")
        self.assertEqual(fields["mod"], "@M")
        self.assertEqual(fields["label"], "lab")
        self.assertEqual(fields["age_s"], 12.5)
        self.assertFalse(fields["foreign"])
        self.assertEqual(fields["hint"], "retry with wait_for_box_s=<n>")

    def test_stop_hint_only_for_owner_or_idle(self) -> None:
        # RED if a foreign-owned RUNNING run tells the caller to dayz_test_stop.
        foreign_owner = occupancy_error_fields(
            {
                "runs": [
                    {
                        "run_id": "abc",
                        "mod": "@M",
                        "label": "lab",
                        "age_s": 1.0,
                        "state": "RUNNING",
                        "owner_session": "other",
                    }
                ]
            },
            caller_session="me",
        )
        self.assertEqual(foreign_owner["hint"], "retry with wait_for_box_s=<n>")
        idle = occupancy_error_fields(
            {
                "runs": [
                    {
                        "run_id": "abc",
                        "mod": "@M",
                        "label": "lab",
                        "age_s": 1.0,
                        "state": "RUNNING_IDLE",
                        "owner_session": None,
                    }
                ]
            },
            caller_session="me",
        )
        self.assertEqual(idle["hint"], "stop it with dayz_test_stop(run_id=abc)")
        owner = occupancy_error_fields(
            {
                "runs": [
                    {
                        "run_id": "abc",
                        "mod": "@M",
                        "label": "lab",
                        "age_s": 1.0,
                        "state": "RUNNING",
                        "owner_session": "me",
                    }
                ]
            },
            caller_session="me",
        )
        self.assertEqual(owner["hint"], "stop it with dayz_test_stop(run_id=abc)")

    def test_foreign_shape(self) -> None:
        fields = occupancy_error_fields(
            {
                "occupied": True,
                "runs": [],
                "foreign": [{"port": 2302, "mods": ["@X"]}],
            }
        )
        self.assertIsNone(fields["occupied_by_run_id"])
        self.assertTrue(fields["foreign"])
        self.assertNotIn("pid", fields)
        self.assertEqual(fields["port"], 2302)
        self.assertEqual(fields["hint"], "retry with wait_for_box_s=<n>")

    def test_projects_activity_fields_from_run_row(self) -> None:
        fields = occupancy_error_fields(
            {
                "occupied": True,
                "runs": [
                    {
                        "run_id": "abc",
                        "mod": "@M",
                        "label": "lab",
                        "age_s": 12.5,
                        "activity_state": "stale",
                        "last_activity_age_s": 901.0,
                    }
                ],
                "foreign": [],
            }
        )
        self.assertEqual(fields["activity_state"], "stale")
        self.assertEqual(fields["last_activity_age_s"], 901.0)
        self.assertEqual(fields["occupied_by_run_id"], "abc")
        self.assertEqual(fields["age_s"], 12.5)
        self.assertEqual(fields["mod"], "@M")
        self.assertEqual(fields["label"], "lab")
        self.assertFalse(fields["foreign"])
        self.assertTrue(fields["hint"])

    def test_starting_and_stopping_do_not_order_dayz_test_stop(self) -> None:
        for state in ("STARTING", "STOPPING"):
            fields = occupancy_error_fields(
                {
                    "runs": [
                        {
                            "run_id": "abc",
                            "mod": "@M",
                            "label": "lab",
                            "age_s": 1.0,
                            "state": state,
                            "owner_session": "me",
                        }
                    ]
                },
                caller_session="me",
            )
            hint = str(fields.get("hint") or "")
            self.assertNotIn("dayz_test_stop", hint, state)

    def test_unreconciled_live_run_does_not_order_wait_for_box_s(self) -> None:
        owned = occupancy_error_fields(
            {
                "runs": [
                    {
                        "run_id": "abc",
                        "mod": "@M",
                        "label": "lab",
                        "age_s": 1.0,
                        "state": "UNRECONCILED",
                        "owner_session": "me",
                    }
                ]
            },
            caller_session="me",
        )
        foreign = occupancy_error_fields(
            {
                "runs": [
                    {
                        "run_id": "abc",
                        "mod": "@M",
                        "label": "lab",
                        "age_s": 1.0,
                        "state": "UNRECONCILED",
                        "owner_session": "other",
                    }
                ]
            },
            caller_session="me",
        )
        for fields in (owned, foreign):
            hint = str(fields.get("hint") or "")
            self.assertNotIn("wait_for_box_s", hint)
            self.assertNotIn("dayz_test_stop", hint)


CREATED_UTC = "2026-07-15T00:00:41.0000000Z"


def _activity_at(offset: float) -> float:
    base = _utc_epoch(CREATED_UTC)
    assert base is not None
    return base + offset


class RunCommandActivityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.paths = RuntimePaths(
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
        self.store = RunManifestStore(self.paths)
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
        self.lifecycle.daemon_generation = "gen-a"
        launched = ProcessRecord(
            9001,
            CREATED_UTC,
            HASH_A,
            HASH_B,
            "client",
            identity_scheme="psutil-argv-v2",
        )
        self.guard.snapshots[launched.pid] = snapshot(launched)
        started = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            {
                "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
                "cwd": str(self.game),
                "role": "client",
                "window_style": "normal",
                "label": "gate",
                "mod": "@SameMod",
                "profiles": "profiles",
                "mission": "test",
            },
        )
        self.assertTrue(started.get("ok"), started)
        self.run_id = started["run_id"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _row(self, now: float) -> dict:
        box = self.lifecycle.box_occupancy(now=now)
        runs = box.get("runs")
        self.assertIsInstance(runs, list)
        self.assertTrue(runs)
        row = runs[0]
        self.assertIsInstance(row, dict)
        return row

    def test_activity_state_is_recent_at_899s_and_stale_at_901s(self) -> None:
        recent = self._row(_activity_at(899.0))
        self.assertEqual(recent["activity_state"], "recent")
        self.assertIsInstance(recent["last_activity_age_s"], (int, float))
        self.assertNotIsInstance(recent["last_activity_age_s"], bool)
        stale = self._row(_activity_at(901.0))
        self.assertEqual(stale["activity_state"], "stale")
        self.assertGreater(stale["last_activity_age_s"], 900.0)

    def test_future_clock_is_unknown_with_null_age(self) -> None:
        row = self._row(_activity_at(-10.0))
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_legacy_run_without_activity_event_is_unknown(self) -> None:
        extra = process(802)
        self.store.add(
            RunRecord(
                "run-legacy",
                "A",
                "lease-A",
                "RUNNING",
                "legacy",
                "@Other",
                "profiles",
                "mission",
                [extra],
            )
        )
        box = self.lifecycle.box_occupancy(now=_activity_at(10.0))
        by_id = {item["run_id"]: item for item in box["runs"]}
        self.assertEqual(by_id["run-legacy"]["activity_state"], "unknown")
        self.assertIsNone(by_id["run-legacy"]["last_activity_age_s"])

    def test_occupancy_error_fields_project_activity_with_prior_values(self) -> None:
        box = self.lifecycle.box_occupancy(now=_activity_at(899.0))
        fields = occupancy_error_fields(box)
        self.assertEqual(fields["activity_state"], "recent")
        self.assertIsInstance(fields["last_activity_age_s"], (int, float))
        self.assertEqual(fields["occupied_by_run_id"], "run-1")
        self.assertEqual(fields["mod"], "@SameMod")
        self.assertEqual(fields["label"], "gate")
        self.assertIsInstance(fields["age_s"], (int, float))
        self.assertGreater(fields["age_s"], 0)
        self.assertFalse(fields["foreign"])
        self.assertTrue(fields["hint"])

    def test_old_owned_pid_still_occupies_when_activity_is_stale(self) -> None:
        box = self.lifecycle.box_occupancy(now=_activity_at(100000.0))
        self.assertTrue(box["occupied"])
        self.assertTrue(box["runs"])
        self.assertEqual(box["runs"][0]["activity_state"], "stale")

    def test_writer_fault_is_sticky_unknown_for_the_generation(self) -> None:
        self.audit.fail_events.add("run_command_activity")
        recorded = self.lifecycle.record_command_activity(
            self.run_id, now=_activity_at(10.0)
        )
        self.assertFalse(recorded)
        self.audit.fail_events.discard("run_command_activity")
        self.lifecycle.record_command_activity(self.run_id, now=_activity_at(20.0))
        row = self._row(_activity_at(901.0))
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_new_generation_clears_inherited_unknown(self) -> None:
        self.audit.fail_events.add("run_command_activity")
        recorded = self.lifecycle.record_command_activity(
            self.run_id, now=_activity_at(10.0)
        )
        self.assertFalse(recorded)
        self.audit.fail_events.discard("run_command_activity")
        self.lifecycle.daemon_generation = "gen-b"
        self.lifecycle.record_command_activity(self.run_id, now=_activity_at(50.0))
        row = self._row(_activity_at(60.0))
        self.assertEqual(row["activity_state"], "recent")
        self.assertAlmostEqual(row["last_activity_age_s"], 10.0, places=2)

    def test_command_resets_age_only_for_its_run(self) -> None:
        extra = ProcessRecord(
            9002,
            CREATED_UTC,
            HASH_A,
            HASH_B,
            "client",
            identity_scheme="psutil-argv-v2",
        )
        self.store.add(
            RunRecord(
                "run-2",
                "A",
                "lease-A",
                "RUNNING",
                "other",
                "@Other",
                "profiles",
                "mission",
                [extra],
            )
        )
        self.lifecycle.record_command_activity("run-2", now=_activity_at(0.0))
        self.lifecycle.record_command_activity(self.run_id, now=_activity_at(100.0))
        box = self.lifecycle.box_occupancy(now=_activity_at(100.0))
        by_id = {item["run_id"]: item for item in box["runs"]}
        self.assertEqual(by_id[self.run_id]["activity_state"], "recent")
        self.assertAlmostEqual(by_id[self.run_id]["last_activity_age_s"], 0.0, places=2)
        self.assertEqual(by_id["run-2"]["activity_state"], "recent")
        self.assertAlmostEqual(by_id["run-2"]["last_activity_age_s"], 100.0, places=2)
        later = self.lifecycle.box_occupancy(now=_activity_at(100.0 + 901.0))
        later_by_id = {item["run_id"]: item for item in later["runs"]}
        self.assertEqual(later_by_id[self.run_id]["activity_state"], "stale")
        self.assertEqual(later_by_id["run-2"]["activity_state"], "stale")
        self.lifecycle.record_command_activity(self.run_id, now=_activity_at(100.0 + 901.0))
        reset = self.lifecycle.box_occupancy(now=_activity_at(100.0 + 901.0))
        reset_by_id = {item["run_id"]: item for item in reset["runs"]}
        self.assertEqual(reset_by_id[self.run_id]["activity_state"], "recent")
        self.assertAlmostEqual(
            reset_by_id[self.run_id]["last_activity_age_s"], 0.0, places=2
        )
        self.assertEqual(reset_by_id["run-2"]["activity_state"], "stale")

    def test_corrupt_jsonl_does_not_change_activity(self) -> None:
        writer = JsonlAuditWriter(self.paths, "gen-json")
        life = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=writer.write,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            daemon_generation="gen-json",
        )
        life.record_command_activity(self.run_id, now=_activity_at(5.0))
        sano = life.box_occupancy(now=_activity_at(10.0))["runs"][0]
        writer.current_path.write_text("{not-json\n", encoding="utf-8")
        corrupto = life.box_occupancy(now=_activity_at(10.0))["runs"][0]
        self.assertEqual(sano["activity_state"], "recent")
        self.assertEqual(corrupto["activity_state"], sano["activity_state"])
        self.assertEqual(
            corrupto["last_activity_age_s"], sano["last_activity_age_s"]
        )

    def test_cleared_jsonl_does_not_change_recorded_activity(self) -> None:
        writer = JsonlAuditWriter(self.paths, "gen-json")
        life = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=writer.write,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            daemon_generation="gen-json",
        )
        life.record_command_activity(self.run_id, now=_activity_at(5.0))
        self.assertTrue(writer.current_path.is_file())
        sano = life.box_occupancy(now=_activity_at(10.0))["runs"][0]
        writer.current_path.write_text("", encoding="utf-8")
        borrado = life.box_occupancy(now=_activity_at(10.0))["runs"][0]
        self.assertEqual(sano["activity_state"], "recent")
        self.assertEqual(borrado["activity_state"], sano["activity_state"])
        self.assertEqual(
            borrado["last_activity_age_s"], sano["last_activity_age_s"]
        )

    def _bind_state(self, run_id: str | None = None, **kwargs) -> loopback.ServerState:
        state = loopback.ServerState("k", **kwargs)
        bind_both_peers(state, run_id=self.run_id if run_id is None else run_id)
        state.lifecycle = self.lifecycle
        self.lifecycle.bindings = state
        return state

    def _activity_events(self) -> list[dict]:
        return [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]

    def _add_unowned_run(self, run_id: str = "run-2", pid: int = 9002) -> None:
        extra = ProcessRecord(
            pid,
            CREATED_UTC,
            HASH_A,
            HASH_B,
            "server",
            identity_scheme="psutil-argv-v2",
        )
        self.store.add(
            RunRecord(
                run_id,
                None,
                None,
                "RUNNING_IDLE",
                "other",
                "@Other",
                "profiles",
                "mission",
                [extra],
            )
        )

    def test_accepted_enqueue_records_activity_on_both_branches(self) -> None:
        state = self._bind_state()
        before = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        after_normal = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        self.assertGreater(len(after_normal), len(before))
        self.assertEqual(after_normal[-1].get("run_id"), self.run_id)
        self.assertEqual(after_normal[-1].get("reason"), "enqueue_accepted")

        exec_state = self._bind_state(
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=lambda *_args: None,
        )
        before_exec = len(
            [
                event
                for event in self.audit.events
                if event.get("event") == "run_command_activity"
            ]
        )
        status, payload = exec_state.enqueue_command(
            "exec_enforce",
            {"expr": "probe()", "main_fn": "Main"},
            peer="server",
        )
        self.assertEqual(status, 200, payload)
        after_exec = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        self.assertGreater(len(after_exec), before_exec)
        self.assertEqual(after_exec[-1].get("run_id"), self.run_id)

    def test_rejected_enqueue_does_not_record_activity_on_either_branch(self) -> None:
        state = self._bind_state()
        before = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        status, payload = state.enqueue_command("not_a_command", {}, peer="client")
        self.assertEqual(status, 400, payload)
        status, payload = state.enqueue_command(
            "camera_get", {}, peer="server"
        )
        self.assertEqual(status, 400, payload)
        exec_state = self._bind_state(
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=lambda *_args: None,
        )
        status, payload = exec_state.enqueue_command(
            "exec_enforce",
            {"expr": "denied()", "main_fn": "Main"},
            peer="server",
        )
        self.assertEqual(status, 403, payload)
        after = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        self.assertEqual(len(after), len(before))

    def test_writer_fault_does_not_fail_accepted_enqueue(self) -> None:
        self.audit.fail_events.add("run_command_activity")
        state = self._bind_state()
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        self.assertIn("id", payload)

    def test_unowned_running_idle_command_records_activity(self) -> None:
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        states = [
            item.state
            for item in self.store.list_runs()
            if item.run_id == self.run_id
        ]
        self.assertEqual(states, ["RUNNING_IDLE"])
        self.lifecycle.record_box_command_activity(
            now=_activity_at(3000.0), owner_session="sesion-viva-B"
        )
        row = self._row(_activity_at(3010.0))
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_internal_enqueue_does_not_call_activity_recorder(self) -> None:
        class Spy:
            def __init__(self) -> None:
                self.calls = 0
                self.manifest = types.SimpleNamespace(
                    get=lambda _run_id: types.SimpleNamespace(state="RUNNING")
                )

            def record_box_command_activity(self, **_kw) -> None:
                self.calls += 1

        state = loopback.ServerState("k")
        bind_both_peers(state)
        spy = Spy()
        state.lifecycle = spy
        status, payload = state.enqueue_command(
            "vehicle_release", {}, peer="client", internal=True
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(spy.calls, 0)

    def test_internal_enqueue_does_not_write_run_command_activity(self) -> None:
        state = self._bind_state()
        before = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        status, payload = state.enqueue_command(
            "vehicle_release", {}, peer="client", internal=True
        )
        self.assertEqual(status, 200, payload)
        after = [
            event
            for event in self.audit.events
            if event.get("event") == "run_command_activity"
        ]
        self.assertEqual(len(after), len(before))
        row = self.lifecycle.box_occupancy()["runs"][0]
        self.assertEqual(row["activity_state"], "stale")

    def test_writer_fault_invalidates_cached_occupancy(self) -> None:
        seeded = self.lifecycle.box_occupancy()
        self.assertIn(seeded["runs"][0]["activity_state"], ("recent", "stale"))
        self.audit.fail_events.add("run_command_activity")
        recorded = self.lifecycle.record_command_activity(
            self.run_id, now=time.time()
        )
        self.assertFalse(recorded)
        cached = self.lifecycle.box_occupancy()
        self.assertEqual(cached["runs"][0]["activity_state"], "unknown")
        self.assertIsNone(cached["runs"][0]["last_activity_age_s"])

    def test_inherited_generation_is_unknown_until_new_activity(self) -> None:
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        self.lifecycle.daemon_generation = "gen-B"
        inherited = self._row(_activity_at(4000.0))
        self.assertEqual(inherited["activity_state"], "unknown")
        self.assertIsNone(inherited["last_activity_age_s"])
        self.lifecycle.record_command_activity(
            self.run_id, now=_activity_at(4100.0)
        )
        after = self._row(_activity_at(4110.0))
        self.assertEqual(after["activity_state"], "recent")
        self.assertAlmostEqual(after["last_activity_age_s"], 10.0, places=2)

    def test_box_activity_targets_only_the_owned_run_among_several(self) -> None:
        extra = ProcessRecord(
            9002,
            CREATED_UTC,
            HASH_A,
            HASH_B,
            "client",
            identity_scheme="psutil-argv-v2",
        )
        self.store.add(
            RunRecord(
                "run-2",
                "B",
                "lease-B",
                "RUNNING",
                "other",
                "@Other",
                "profiles",
                "mission",
                [extra],
            )
        )
        self.lifecycle.record_command_activity("run-2", now=_activity_at(0.0))
        self.lifecycle.record_command_activity(
            self.run_id, now=_activity_at(3000.0)
        )
        box = self.lifecycle.box_occupancy(now=_activity_at(3010.0))
        by_id = {item["run_id"]: item for item in box["runs"]}
        self.assertEqual(by_id[self.run_id]["activity_state"], "recent")
        self.assertAlmostEqual(
            by_id[self.run_id]["last_activity_age_s"], 10.0, places=2
        )
        self.assertEqual(by_id["run-2"]["activity_state"], "stale")

    def test_multiple_unowned_runs_do_not_all_receive_box_activity(self) -> None:
        extra = ProcessRecord(
            9002,
            CREATED_UTC,
            HASH_A,
            HASH_B,
            "client",
            identity_scheme="psutil-argv-v2",
        )
        self.store.add(
            RunRecord(
                "run-2",
                None,
                None,
                "RUNNING_IDLE",
                "other",
                "@Other",
                "profiles",
                "mission",
                [extra],
            )
        )
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        self.lifecycle.record_box_command_activity(
            now=_activity_at(3000.0), owner_session="sesion-viva-B"
        )
        box = self.lifecycle.box_occupancy(now=_activity_at(3010.0))
        by_id = {item["run_id"]: item for item in box["runs"]}
        self.assertEqual(by_id[self.run_id]["activity_state"], "unknown")
        self.assertEqual(by_id["run-2"]["activity_state"], "unknown")

    def test_accepted_enqueue_attributes_activity_to_binding_run_among_several(
        self,
    ) -> None:
        self._add_other_owned_run()
        state = self._bind_state(run_id="run-2")
        before = len(self._activity_events())
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        destinos = [event.get("run_id") for event in self._activity_events()[before:]]
        self.assertEqual(destinos, ["run-2"])
        box = self.lifecycle.box_occupancy(now=time.time())
        by_id = {item["run_id"]: item for item in box["runs"]}
        self.assertEqual(by_id["run-2"]["activity_state"], "recent")
        self.assertNotEqual(by_id[self.run_id]["activity_state"], "recent")

        exec_state = self._bind_state(
            run_id="run-2",
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=lambda *_args: None,
        )
        before_exec = len(self._activity_events())
        status, payload = exec_state.enqueue_command(
            "exec_enforce",
            {"expr": "probe()", "main_fn": "Main"},
            peer="server",
        )
        self.assertEqual(status, 200, payload)
        destinos_exec = [
            event.get("run_id") for event in self._activity_events()[before_exec:]
        ]
        self.assertEqual(destinos_exec, ["run-2"])

    def _add_other_owned_run(self, run_id: str = "run-2", pid: int = 9002) -> None:
        extra = ProcessRecord(
            pid,
            CREATED_UTC,
            HASH_A,
            HASH_B,
            "server",
            identity_scheme="psutil-argv-v2",
        )
        self.store.add(
            RunRecord(
                run_id,
                "B",
                "lease-B",
                "RUNNING",
                "other",
                "@Other",
                "profiles",
                "mission",
                [extra],
            )
        )

    def test_inherited_generation_becomes_recent_via_bound_enqueue(self) -> None:
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        self.lifecycle.daemon_generation = "gen-B"
        box = self.lifecycle.box_occupancy(now=_activity_at(4000.0))
        by_id = {item["run_id"]: item for item in box["runs"]}
        self.assertEqual(by_id[self.run_id]["activity_state"], "unknown")
        self.assertIsNone(by_id[self.run_id]["last_activity_age_s"])
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, self.run_id)
        self.assertTrue(adopted.get("ok"), adopted)
        self._add_unowned_run()
        state = self._bind_state(run_id=self.run_id)
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        after = {
            item["run_id"]: item
            for item in self.lifecycle.box_occupancy(now=time.time())["runs"]
        }
        self.assertEqual(after[self.run_id]["activity_state"], "recent")
        self.assertEqual(after["run-2"]["activity_state"], "unknown")

    def test_capture_start_activity_does_not_import_pre_daemon_stamp(self) -> None:
        viejo_utc = "2026-06-01T00:00:00.0000000Z"
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        self.lifecycle.daemon_generation = "gen-B"
        actual = [item for item in self.store.list_runs() if item.run_id == self.run_id][
            0
        ]
        actual.processes.append(
            ProcessRecord(
                9003,
                viejo_utc,
                HASH_A,
                HASH_B,
                "server",
                identity_scheme="psutil-argv-v2",
            )
        )
        self.lifecycle._capture_start_activity(actual)
        row = self._row(_activity_at(6000.0))
        edad = row.get("last_activity_age_s")
        viejo = _utc_epoch(viejo_utc)
        importado = (
            edad is not None
            and viejo is not None
            and abs((_activity_at(6000.0) - float(edad)) - viejo) < 2.0
        )
        self.assertFalse(importado, row)

    def test_slow_occupancy_reader_does_not_republish_after_invalidation(self) -> None:
        puerta = threading.Event()
        dentro = threading.Event()
        llamadas = {"n": 0}

        def _diag_lento():
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                dentro.set()
                puerta.wait(5.0)
            return {"known": True, "processes": []}

        self.lifecycle.box_occupancy(now=_activity_at(10.0))
        self.lifecycle.diag_probe = _diag_lento

        def _lector() -> None:
            self.lifecycle.box_occupancy()

        hilo = threading.Thread(target=_lector, daemon=True)
        hilo.start()
        self.assertTrue(dentro.wait(5.0))
        self.audit.fail_events.add("run_command_activity")
        self.lifecycle.record_command_activity(self.run_id, now=time.time())
        verdad = self.lifecycle.box_occupancy(now=time.time())["runs"][0][
            "activity_state"
        ]
        puerta.set()
        hilo.join(timeout=5)
        publicado = self.lifecycle.box_occupancy()["runs"][0]["activity_state"]
        self.assertEqual(verdad, "unknown")
        self.assertEqual(publicado, "unknown")

    def test_concurrent_occupancy_returns_one_coherent_snapshot(self) -> None:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien: dict[str, object] = {}

        def _argv_lento(pid: int) -> list[str]:
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return []

        self.lifecycle.argv_of = _argv_lento
        self.lifecycle.record_command_activity(self.run_id, now=time.time())
        self.lifecycle.box_occupancy()
        self.lifecycle.record_command_activity(self.run_id, now=time.time())
        devuelto: dict[str, object] = {}

        def _lector() -> None:
            devuelto["box"] = self.lifecycle.box_occupancy()

        hilo = threading.Thread(target=_lector, daemon=True)
        armado.set()
        hilo.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertEqual(quien.get("ident"), hilo.ident)
        self.assertNotIn("box", devuelto)
        parado = self.lifecycle.stop_run(IDENTITY_A, self.token_a, self.run_id)
        self.assertTrue(parado.get("ok"), parado)
        self.audit.fail_events.add("run_command_activity")
        self.lifecycle.record_command_activity(self.run_id, now=time.time())
        puerta.set()
        hilo.join(timeout=5)
        box = devuelto.get("box")
        self.assertIsInstance(box, dict)
        fila = next(
            (
                item
                for item in box["runs"]
                if isinstance(item, dict) and item.get("run_id") == self.run_id
            ),
            None,
        )
        if fila is not None:
            self.assertEqual(fila.get("state"), "RUNNING")
            self.assertEqual(fila.get("activity_state"), "recent")
        later = self.lifecycle.box_occupancy()
        self.assertFalse(
            any(item.get("run_id") == self.run_id for item in later["runs"])
        )

    def test_older_unbound_observation_does_not_erase_newer_stamp(self) -> None:
        t1 = time.time()
        self.lifecycle.record_command_activity(self.run_id, now=t1)
        seeded = self._row(t1 + 10.0)
        self.assertEqual(seeded["activity_state"], "recent")
        self.lifecycle.record_box_command_activity(now=t1 - 60.0)
        row = self._row(t1 + 10.0)
        self.assertEqual(row["activity_state"], "recent")

    def test_later_unbound_observation_still_publishes_unknown(self) -> None:
        t1 = time.time()
        self.lifecycle.record_command_activity(self.run_id, now=t1)
        seeded = self._row(t1 + 10.0)
        self.assertEqual(seeded["activity_state"], "recent")
        self.lifecycle.record_box_command_activity(now=t1 + 30.0)
        row = self._row(t1 + 40.0)
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_occupancy_rows_are_not_served_from_cache_after_direct_manifest_replace(
        self,
    ) -> None:
        before = self.lifecycle.box_occupancy()
        self.assertEqual(before["runs"][0]["state"], "RUNNING")
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        run.state = "UNRECONCILED"
        run.owner_session_id = None
        run.owner_lease_id = None
        self.store.replace(run)
        published = self.lifecycle.box_occupancy()
        self.assertEqual(published["runs"][0]["state"], "UNRECONCILED")

    def test_basal_does_not_land_at_or_before_tombstone(self) -> None:
        observation = _activity_at(5000.0)
        self.lifecycle.record_box_command_activity(now=observation)
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle._capture_start_activity(run)
        row = self._row(_activity_at(5010.0))
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_in_flight_writer_does_not_resurrect_stamp_after_unbound_observation(
        self,
    ) -> None:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien: dict[str, object] = {}
        audit_real = self.lifecycle.audit
        t_w = time.time()

        def _audit_lento(payload):
            if (
                armado.is_set()
                and not dentro.is_set()
                and isinstance(payload, dict)
                and payload.get("event") == "run_command_activity"
            ):
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return audit_real(payload)

        self.lifecycle.audit = _audit_lento
        returned: dict[str, object] = {}

        def _writer() -> None:
            returned["ok"] = self.lifecycle.record_command_activity(
                self.run_id, now=t_w
            )

        thread = threading.Thread(target=_writer, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertEqual(quien.get("ident"), thread.ident)
        self.lifecycle.record_box_command_activity(now=t_w + 5.0)
        puerta.set()
        thread.join(timeout=5)
        self.lifecycle.audit = audit_real
        self.assertIs(returned.get("ok"), True)
        row = self._row(t_w + 10.0)
        self.assertEqual(row["activity_state"], "unknown")
        self.assertIsNone(row["last_activity_age_s"])

    def test_probe_cache_is_not_reused_after_revision_bump_during_list_runs(
        self,
    ) -> None:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        probes = {"n": 0}

        def _diag_counted():
            probes["n"] += 1
            return {"known": True, "processes": []}

        self.lifecycle.diag_probe = _diag_counted
        list_runs_real = self.store.list_runs

        def _list_runs_slow():
            if armado.is_set() and not dentro.is_set():
                dentro.set()
                puerta.wait(5.0)
            return list_runs_real()

        self.store.list_runs = _list_runs_slow  # type: ignore[method-assign]
        self.lifecycle.record_command_activity(self.run_id, now=time.time())
        returned: dict[str, object] = {}

        def _reader() -> None:
            returned["box"] = self.lifecycle.box_occupancy()

        thread = threading.Thread(target=_reader, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.lifecycle.record_command_activity(self.run_id, now=time.time())
        puerta.set()
        thread.join(timeout=5)
        self.store.list_runs = list_runs_real  # type: ignore[method-assign]
        self.assertIn("box", returned)
        after_first = probes["n"]
        self.lifecycle.box_occupancy()
        self.assertGreater(probes["n"], after_first)

    def test_exec_enforce_on_idle_run_is_run_not_owned(self) -> None:
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        state = self._bind_state(
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=lambda *_args: None,
        )
        status, payload = state.enqueue_command(
            "exec_enforce",
            {"expr": "probe()", "main_fn": "Main"},
            peer="server",
        )
        self.assertNotEqual(status, 200, payload)
        self.assertEqual(payload.get("error"), "run_not_owned")

    def test_camera_get_on_idle_run_is_run_not_owned(self) -> None:
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        state = self._bind_state()
        before = len(self._activity_events())
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertNotEqual(status, 200, payload)
        self.assertEqual(payload.get("error"), "run_not_owned")
        self.assertEqual(len(self._activity_events()), before)

    def test_internal_command_on_idle_run_is_run_not_owned(self) -> None:
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        state = self._bind_state()
        before = len(self._activity_events())
        status, payload = state.enqueue_command(
            "vehicle_release", {}, peer="client", internal=True
        )
        self.assertNotEqual(status, 200, payload)
        self.assertEqual(payload.get("error"), "run_not_owned")
        self.assertEqual(len(self._activity_events()), before)

    def test_exec_enforce_losing_race_with_release_is_run_not_owned(self) -> None:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()

        def _exec_audit_lento(*_args: object) -> None:
            if armado.is_set() and not dentro.is_set():
                dentro.set()
                puerta.wait(5.0)

        state = self._bind_state(
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=_exec_audit_lento,
        )
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        returned: dict[str, object] = {}

        def _encolador() -> None:
            returned["r"] = state.enqueue_command(
                "exec_enforce",
                {"expr": "probe()", "main_fn": "Main"},
                peer="server",
            )

        thread = threading.Thread(target=_encolador, daemon=True)
        armado.set()
        thread.start()
        self.assertTrue(dentro.wait(5.0))
        self.assertNotIn("r", returned)
        self.lifecycle.release_owner(run.owner_session_id, run.owner_lease_id)
        puerta.set()
        thread.join(timeout=5)
        status, payload = returned.get("r", (None, None))
        self.assertNotEqual(status, 200, payload)
        self.assertEqual(payload.get("error"), "run_not_owned")

    def test_derive_box_is_pure_over_the_snapshot(self) -> None:
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        stamp = _activity_at(0.0)
        snapshot = _BoxSnapshot(
            clock=_activity_at(10.0),
            runs=(run,),
            activity={self.run_id: stamp},
            unknown=frozenset(),
            revision=1,
        )
        probes = _BoxProbes(foreign=(), ports_in_use=(), scan_known=True)
        first = _derive_box(snapshot, probes)
        self.audit.fail_events.add("run_command_activity")
        self.lifecycle.record_command_activity(self.run_id, now=time.time())
        second = _derive_box(snapshot, probes)
        self.assertEqual(first, second)
        self.assertEqual(first["runs"][0]["activity_state"], "recent")
        self.assertEqual(first["runs"][0]["state"], "RUNNING")
        live = self.lifecycle.box_occupancy(now=time.time())
        self.assertEqual(live["runs"][0]["activity_state"], "unknown")

    def test_compensating_snapshot_publishes_unknown(self) -> None:
        run = [item for item in self.store.list_runs() if item.run_id == self.run_id][0]
        stamp = _activity_at(0.0)
        snapshot = _BoxSnapshot(
            clock=_activity_at(10.0),
            runs=(run,),
            activity={self.run_id: stamp},
            unknown=frozenset(),
            revision=1,
            compensating=frozenset({self.run_id}),
        )
        probes = _BoxProbes(foreign=(), ports_in_use=(), scan_known=True)
        box = _derive_box(snapshot, probes)
        self.assertEqual(box["runs"][0]["activity_state"], "unknown")
        self.assertIsNone(box["runs"][0]["last_activity_age_s"])

    def test_unbound_enqueue_publishes_unknown_without_crediting(self) -> None:
        self._add_unowned_run()
        state = loopback.ServerState("k")
        state.lifecycle = self.lifecycle
        before = len(self._activity_events())
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(self._activity_events()), before)
        box = self.lifecycle.box_occupancy(now=_activity_at(2000.0))
        by_id = {item["run_id"]: item for item in box["runs"]}
        self.assertEqual(by_id[self.run_id]["activity_state"], "unknown")
        self.assertIsNone(by_id[self.run_id]["last_activity_age_s"])
        self.assertEqual(by_id["run-2"]["activity_state"], "unknown")
        self.assertIsNone(by_id["run-2"]["last_activity_age_s"])

    def test_late_activity_write_does_not_regress_freshness(self) -> None:
        self.lifecycle.record_command_activity(self.run_id, now=_activity_at(2000.0))
        self.lifecycle.record_command_activity(self.run_id, now=_activity_at(1000.0))
        row = self._row(_activity_at(2100.0))
        self.assertEqual(row["activity_state"], "recent")
        self.assertAlmostEqual(row["last_activity_age_s"], 100.0, places=0)

    def test_stop_run_invalidates_occupancy_cache(self) -> None:
        before = self.lifecycle.box_occupancy()
        self.assertTrue(before["occupied"])
        self.assertTrue(before["runs"])
        parado = self.lifecycle.stop_run(IDENTITY_A, self.token_a, self.run_id)
        self.assertTrue(parado.get("ok"), parado)
        cached = self.lifecycle.box_occupancy()
        self.assertFalse(cached["occupied"])
        self.assertEqual(cached["runs"], [])


class JsonlActivityLookupTest(unittest.TestCase):
    """Activity is daemon memory; the audit is a trace, not the authority."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.paths.audit_dir.mkdir(parents=True, exist_ok=True)
        self.writer = JsonlAuditWriter(self.paths, "gen-A")
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=self.writer.write,
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.store = RunManifestStore(self.paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.writer.write,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            daemon_generation="gen-A",
        )
        launched = ProcessRecord(
            9001,
            CREATED_UTC,
            HASH_A,
            HASH_B,
            "client",
            identity_scheme="psutil-argv-v2",
        )
        self.guard.snapshots[launched.pid] = snapshot(launched)
        started = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            {
                "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
                "cwd": str(self.game),
                "role": "client",
                "window_style": "normal",
                "label": "gate",
                "mod": "@SameMod",
                "profiles": "profiles",
                "mission": "test",
            },
        )
        self.assertTrue(started.get("ok"), started)
        self.run_id = started["run_id"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _events(self) -> list[dict]:
        raw = self.writer.current_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in raw if line.strip()]

    def _start_epoch(self) -> float:
        starts = [
            event
            for event in self._events()
            if event.get("event") == "lifecycle_start_outcome"
            and event.get("decision") == "started"
            and event.get("state") == "RUNNING"
        ]
        self.assertTrue(starts)
        epoch = _utc_epoch(starts[-1].get("timestamp_utc"))
        self.assertIsNotNone(epoch)
        return float(epoch)

    def _row(self, now: float) -> dict:
        box = self.lifecycle.box_occupancy(now=now)
        runs = box.get("runs")
        self.assertIsInstance(runs, list)
        self.assertTrue(runs)
        row = runs[0]
        self.assertIsInstance(row, dict)
        return row

    def test_occupancy_lookup_does_not_write_audit(self) -> None:
        before = self.writer.current_path.read_bytes()
        box = self.lifecycle.box_occupancy(now=self._start_epoch() + 300.0)
        occupancy_error_fields(box)
        after = self.writer.current_path.read_bytes()
        self.assertEqual(before, after)

    def test_accredited_start_is_basal_without_commands(self) -> None:
        recent = self._row(_activity_at(899.0))
        self.assertEqual(recent["activity_state"], "recent")
        self.assertIsInstance(recent["last_activity_age_s"], (int, float))
        self.assertNotIsInstance(recent["last_activity_age_s"], bool)
        stale = self._row(_activity_at(901.0))
        self.assertEqual(stale["activity_state"], "stale")
        self.assertGreater(stale["last_activity_age_s"], 900.0)

    def test_forged_jsonl_activity_does_not_change_state(self) -> None:
        before = self._row(_activity_at(10.0))
        with self.writer.current_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event": "run_command_activity",
                        "run_id": self.run_id,
                        "reason": "enqueue_accepted",
                        "decision": "recorded",
                        "duration_s": 0.0,
                        "activity_epoch": _activity_at(800.0),
                        "timestamp_utc": "2026-07-15T00:20:00.0000000Z",
                    }
                )
                + "\n"
            )
        after = self._row(_activity_at(10.0))
        self.assertEqual(before["activity_state"], "recent")
        self.assertEqual(after["activity_state"], before["activity_state"])
        self.assertEqual(after["last_activity_age_s"], before["last_activity_age_s"])

    def test_cleared_jsonl_keeps_basal_activity(self) -> None:
        before = self._row(_activity_at(10.0))
        self.assertTrue(self.writer.current_path.is_file())
        self.writer.current_path.write_text("", encoding="utf-8")
        after = self._row(_activity_at(10.0))
        self.assertEqual(before["activity_state"], "recent")
        self.assertEqual(after["activity_state"], before["activity_state"])
        self.assertEqual(after["last_activity_age_s"], before["last_activity_age_s"])


class BoxWaitQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 0.0
        self.seq = 0

        def next_id() -> str:
            self.seq += 1
            return f"ticket-{self.seq}"

        self.coordinator = SessionCoordinator(
            time_fn=lambda: self.now,
            id_fn=next_id,
            token_fn=lambda: "unused",
        )

    def test_fifo_order_and_public_queue(self) -> None:
        first = self.coordinator.box_wait_touch(IDENTITY_WAITER)
        second = self.coordinator.box_wait_touch(IDENTITY_SECOND)
        self.assertEqual(first["box_position"], 1)
        self.assertEqual(second["box_position"], 2)
        queue = self.coordinator.box_queue_public()
        self.assertEqual(
            [item["session"] for item in queue],
            ["waiter-sessi", "second-sessi"],
        )
        self.assertEqual(queue[0]["waiting_s"], 0.0)
        self.now = 5.0
        self.assertEqual(self.coordinator.box_queue_public()[0]["waiting_s"], 5.0)

    def test_ttl_expires_stale_waiter(self) -> None:
        self.coordinator.box_wait_touch(IDENTITY_WAITER)
        self.now = SESSION_TTL_S
        self.assertEqual(self.coordinator.box_queue_public(), [])
        again = self.coordinator.box_wait_touch(
            IDENTITY_WAITER, "ticket-1"
        )
        self.assertEqual(again.get("box_wait_error"), "box_wait_cancelled")

    def test_done_tombstone_blocks_same_ticket(self) -> None:
        joined = self.coordinator.box_wait_touch(IDENTITY_WAITER)
        ticket = joined["box_ticket"]
        self.coordinator.box_wait_touch(IDENTITY_WAITER, ticket, done=True)
        self.assertEqual(self.coordinator.box_queue_public(), [])
        late = self.coordinator.box_wait_touch(IDENTITY_WAITER, ticket)
        self.assertEqual(late.get("box_wait_error"), "box_wait_cancelled")

    def test_claimed_head_survives_waiter_ttl(self) -> None:
        # RED if a claimed head is evicted at SESSION_TTL_S (120s) mid-launch.
        self.coordinator.box_wait_touch(IDENTITY_WAITER)
        self.coordinator.box_wait_touch(IDENTITY_SECOND)
        claimed = self.coordinator.box_wait_touch(
            IDENTITY_WAITER, "ticket-1", claim=True
        )
        self.assertTrue(claimed.get("box_claimed"))
        self.now = SESSION_TTL_S
        queue = self.coordinator.box_queue_public()
        self.assertEqual([item["session"] for item in queue], ["waiter-sessi"])
        self.assertTrue(self.coordinator.box_is_claimed())
        self.now = BOX_CLAIM_TTL_S
        self.assertEqual(self.coordinator.box_queue_public(), [])
        self.assertFalse(self.coordinator.box_is_claimed())

    def test_claim_only_head(self) -> None:
        self.coordinator.box_wait_touch(IDENTITY_WAITER)
        self.coordinator.box_wait_touch(IDENTITY_SECOND)
        second = self.coordinator.box_wait_touch(
            IDENTITY_SECOND, "ticket-2", claim=True
        )
        self.assertFalse(second.get("box_claimed"))
        self.assertFalse(self.coordinator.box_is_claimed())
        first = self.coordinator.box_wait_touch(
            IDENTITY_WAITER, "ticket-1", claim=True
        )
        self.assertTrue(first.get("box_claimed"))
        self.assertTrue(self.coordinator.box_is_claimed())

    def test_queue_cap_matches_lease_fifo(self) -> None:
        for index in range(MAX_SESSION_QUEUE):
            client = ClientIdentity(
                "codex",
                100 + index,
                1,
                "2026-07-15T00:00:00Z",
                f"session-{index:04d}",
                "box",
            )
            result = self.coordinator.box_wait_touch(client)
            self.assertIsNone(result.get("box_wait_error"))
        overflow = self.coordinator.box_wait_touch(IDENTITY_SECOND)
        self.assertEqual(overflow.get("box_wait_error"), "queue_full")


class FakeBoxClient:
    def __init__(self, statuses: list[dict[str, object]]) -> None:
        self.tool_lock = asyncio.Lock()
        self.identity = IDENTITY_WAITER
        self.statuses = list(statuses)
        self.calls: list[dict[str, object]] = []
        self.lock_held_during_sleep = False

    async def session_box_status(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if not self.statuses:
            raise AssertionError("no remaining box statuses")
        return dict(self.statuses.pop(0))


class ExecuteWaitForBoxTest(unittest.IsolatedAsyncioTestCase):
    async def test_waits_until_free_then_claims(self) -> None:
        occupied = {
            "box": {
                "occupied": True,
                "runs": [
                    {
                        "run_id": "run-1",
                        "mod": "@M",
                        "label": "lab",
                        "age_s": 3.0,
                    }
                ],
                "foreign": [],
                "ports_in_use": [2302],
                "queue": [{"session": "waiter-sessi", "waiting_s": 0.0}],
            },
            "box_ticket": "t1",
        }
        free = {
            "box": {
                "occupied": False,
                "runs": [],
                "foreign": [],
                "ports_in_use": [],
                "queue": [{"session": "waiter-sessi", "waiting_s": 1.0}],
            },
            "box_ticket": "t1",
        }
        claimed = dict(free)
        client = FakeBoxClient([occupied, free, claimed])
        clock = {"now": 0.0}

        async def sleeper(delay: float) -> None:
            client.lock_held_during_sleep = (
                client.lock_held_during_sleep or client.tool_lock.locked()
            )
            clock["now"] += delay

        result = await server_module.execute_wait_for_box(
            client,
            5.0,
            sleep_fn=sleeper,
            time_fn=lambda: clock["now"],
            poll_interval_s=0.05,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["ticket"], "t1")
        self.assertFalse(client.lock_held_during_sleep)
        self.assertEqual(client.calls[-1].get("claim"), True)

    async def test_timeout_returns_occupied_box(self) -> None:
        occupied = {
            "box": {
                "occupied": True,
                "runs": [],
                "foreign": [{"pid": 5, "port": 2402, "mods": ["@X"]}],
                "ports_in_use": [2402],
                "queue": [{"session": "waiter-sessi", "waiting_s": 0.0}],
            },
            "box_ticket": "t2",
        }
        client = FakeBoxClient([occupied, occupied, occupied])
        clock = {"now": 0.0}

        async def sleeper(delay: float) -> None:
            client.lock_held_during_sleep = (
                client.lock_held_during_sleep or client.tool_lock.locked()
            )
            clock["now"] += delay

        result = await server_module.execute_wait_for_box(
            client,
            0.1,
            sleep_fn=sleeper,
            time_fn=lambda: clock["now"],
            poll_interval_s=0.05,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["box"]["occupied"])
        self.assertFalse(client.lock_held_during_sleep)

    async def test_dead_ticket_rejoins_instead_of_looping(self) -> None:
        # RED if a box_ticket_invalid response is resent forever.
        occupied = {
            "box": {
                "occupied": True,
                "runs": [],
                "foreign": [],
                "ports_in_use": [],
                "queue": [{"session": "waiter-sessi", "waiting_s": 0.0}],
            },
            "box_ticket": "dead",
        }
        invalid = {
            "box": occupied["box"],
            "box_ticket": None,
            "box_wait_error": "box_ticket_invalid",
        }
        free = {
            "box": {
                "occupied": False,
                "runs": [],
                "foreign": [],
                "ports_in_use": [],
                "queue": [{"session": "waiter-sessi", "waiting_s": 1.0}],
            },
            "box_ticket": "fresh",
        }
        client = FakeBoxClient([occupied, invalid, free, free])
        clock = {"now": 0.0}

        async def sleeper(delay: float) -> None:
            clock["now"] += delay

        result = await server_module.execute_wait_for_box(
            client,
            5.0,
            sleep_fn=sleeper,
            time_fn=lambda: clock["now"],
            poll_interval_s=0.05,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["ticket"], "fresh")
        self.assertIsNone(client.calls[2].get("ticket"))

    async def test_heartbeat_survives_transport_blip(self) -> None:
        # RED if the first session_box_status exception kills the heartbeat.
        calls = {"n": 0}

        class _Client:
            async def session_box_status(self, **_kwargs: object) -> dict[str, object]:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("blip")
                return {}

        async def sleeper(_delay: float) -> None:
            if calls["n"] >= 2:
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await server_module._heartbeat_box_claim(
                _Client(), "t1", sleep_fn=sleeper
            )
        self.assertGreaterEqual(calls["n"], 2)


class DayzTestRunWaitForBoxTest(unittest.IsolatedAsyncioTestCase):
    async def test_wait_then_launch_when_box_already_free(self) -> None:
        # RED if wait_for_box_s>0 never reaches execute after a free box.
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        public = runtime.identity.session_id[:12]

        async def box_status(**kwargs: object) -> dict[str, object]:
            if kwargs.get("done"):
                return {
                    "box_ticket": None,
                    "box": {
                        "occupied": False,
                        "runs": [],
                        "foreign": [],
                        "ports_in_use": [],
                        "queue": [],
                    },
                }
            return {
                "box_ticket": "t1",
                "box": {
                    "occupied": False,
                    "runs": [],
                    "foreign": [],
                    "ports_in_use": [],
                    "queue": [{"session": public, "waiting_s": 0.0}],
                },
            }

        async def execute_run(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "status": "succeeded",
                "project": "ExampleMod",
                "mode": "server",
                "run_id": "12345678-1234-4234-8234-1234567890ab",
                "phase": "completed",
                "elapsed_s": 0.1,
                "artifacts_paths": [],
                "error_code": None,
                "cleanup_degraded": False,
            }

        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(config)
        with (
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                side_effect=execute_run,
            ),
            patch.object(runtime, "session_box_status", side_effect=box_status),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "wait_for_box_s": 30.0,
                    },
                )
            )
        self.assertEqual(payload.get("status"), "succeeded")

    async def test_wait_for_box_s_above_cap_is_bad_args(self) -> None:
        # RED if wait_for_box_s>600 is clamped silently instead of bad_args.
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(config)
        with self.assertRaises(Exception) as err:
            await app.call_tool(
                "dayz_test_run",
                {
                    "project": "ExampleMod",
                    "mode": "server",
                    "wait_for_box_s": BOX_WAIT_MAX_S + 1,
                },
            )
        self.assertIn("bad_args", str(err.exception))
        self.assertIn("wait_for_box_s", str(err.exception))

    async def test_dayz_test_run_description_names_wait_for_box_cap(self) -> None:
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(config)
        tools = {tool.name: tool for tool in app._tool_manager.list_tools()}
        description = tools["dayz_test_run"].description or ""
        self.assertIn("wait_for_box_s", description)
        self.assertIn(f"{BOX_WAIT_MAX_S:g}", description)

    async def test_timeout_returns_enriched_active_run_exists(self) -> None:
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        box = {
            "occupied": True,
            "runs": [
                {
                    "run_id": "run-live",
                    "mod": "@LFHeli",
                    "label": "heli",
                    "age_s": 44.0,
                }
            ],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
        }

        async def wait_box(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"ok": False, "ticket": "box-ticket", "box": box}

        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(config)
        with (
            patch.object(
                server_module.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            patch.object(
                server_module, "execute_wait_for_box", side_effect=wait_box
            ),
            patch.object(
                runtime, "session_box_status", new=AsyncMock(return_value={})
            ),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "wait_for_box_s": 5.0,
                    },
                )
            )
        execute.assert_not_awaited()
        self.assertEqual(payload.get("error_code"), "active_run_exists")
        self.assertIsNone(payload.get("run_id"))
        self.assertEqual(payload.get("occupied_by_run_id"), "run-live")
        self.assertEqual(payload.get("mod"), "@LFHeli")
        self.assertEqual(payload.get("label"), "heli")
        self.assertFalse(payload.get("foreign"))
        self.assertEqual(payload.get("hint"), "retry with wait_for_box_s=<n>")

    async def test_zero_wait_enriches_execute_active_run_exists(self) -> None:
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)

        async def execute_run(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "status": "failed",
                "project": "ExampleMod",
                "mode": "server",
                "run_id": None,
                "phase": "executing",
                "elapsed_s": 0.2,
                "artifacts_paths": [],
                "error_code": "active_run_exists",
                "cleanup_degraded": False,
                "server_alive": None,
                "client_alive": None,
            }

        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(config)
        with (
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                side_effect=execute_run,
            ),
            patch.object(
                runtime,
                "session_status",
                new=AsyncMock(
                    return_value={
                        "box": {
                            "occupied": True,
                            "runs": [],
                            "foreign": [
                                {"port": 2502, "mods": ["@Other"]}
                            ],
                            "ports_in_use": [2502],
                            "queue": [],
                        }
                    }
                ),
            ),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server"},
                )
            )
        self.assertEqual(payload.get("error_code"), "active_run_exists")
        self.assertIsNone(payload.get("run_id"))
        self.assertTrue(payload.get("foreign"))
        self.assertNotIn("pid", payload)
        self.assertEqual(payload.get("port"), 2502)
        self.assertEqual(payload.get("hint"), "retry with wait_for_box_s=<n>")


class LoopbackBoxStatusTest(unittest.TestCase):
    def test_session_status_includes_box_when_lifecycle_missing(self) -> None:
        from dayz_mcp import loopback

        state = loopback.ServerState("k")
        box = loopback._box_payload(state)
        self.assertTrue(box["occupied"])
        self.assertEqual(box["runs"], [])
        self.assertEqual(box["foreign"], [])
        self.assertEqual(box["ports_in_use"], [])
        self.assertEqual(box["queue"], [])


class BoxOccupancyGenerationProjectionTest(unittest.TestCase):
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
        self.generation = "gen-box-h"
        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-box",
            daemon_generation=self.generation,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_box_occupancy_projects_the_same_generation_fields_as_status(self) -> None:
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        started = self.lifecycle.start_run(
            IDENTITY_A,
            self.token_a,
            {
                "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
                "cwd": str(self.game),
                "role": "client",
                "window_style": "normal",
                "label": "gate",
                "mod": "@SameMod",
                "profiles": "profiles",
                "mission": "test",
            },
        )
        self.assertTrue(started.get("ok"), started)
        run_id = started["run_id"]
        box_row = next(
            item
            for item in self.lifecycle.box_occupancy()["runs"]
            if item["run_id"] == run_id
        )
        status_row = next(
            item
            for item in self.lifecycle.status(IDENTITY_A)["runs"]
            if item["run_id"] == run_id
        )
        for field in (
            "daemon_generation_at_launch",
            "daemon_generation_current",
            "generation_changed",
        ):
            self.assertIn(field, box_row)
            self.assertEqual(box_row[field], status_row[field])
        self.assertEqual(box_row["daemon_generation_at_launch"], self.generation)
        self.assertIs(box_row["generation_changed"], False)


if __name__ == "__main__":
    unittest.main()
