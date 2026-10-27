"""inbox 6310: human_input counts on the lease holder's heartbeat and /await.

The 250f sampler already stamps the latest input per run. These tests pin the
cumulative counts and the report block that heartbeat and done /await answers
carry, using fake InputSamples, FakeGuard, and an in-process loopback. They
do not call a real Windows API or start a real process.
"""

from __future__ import annotations

import http.client
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
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.fence_helpers import INST_SERVER
from tests.mcp_helpers import _content_json
from tests.process_lifecycle_helpers import (
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.session_http_helpers import SessionLoopback, _http
from tests.steam_helpers import FakeSteamGate


GENERATION = "gen-6310"
CLIENT_PID = 802
OTHER_PID = 31337
# The run SessionLoopback binds both peers to (fence_helpers.bind_both_peers).
BOUND_RUN = "test-run"
IDENTITY_PAYLOAD = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-14T10:00:00Z",
    "session_id": "A",
    "task_label": "test",
}
BLOCK_KEYS = {"signal", "runs"}
ROW_KEYS = {"run_id", "count", "uncertain_count", "last_age_s"}


class FakeBindings:
    def __init__(self) -> None:
        self.bound: set[str] = set()

    def run_has_bound_binding(self, run_id: str) -> bool:
        return run_id in self.bound

    def fence_runs(self, run_ids: list[str]) -> None:
        return None

    def unfence_runs(self, run_ids: list[str]) -> None:
        return None


class HumanInputHolderTest(unittest.TestCase):
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
        self.lifecycle = ProcessLifecycle(
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
            bindings=self.bindings,
            daemon_generation=GENERATION,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(
        self,
        run_id: str = "run-x",
        *,
        owner: str | None = None,
        lease_id: str | None = None,
        processes=None,
        generation: str | None = GENERATION,
        live: bool = True,
    ) -> RunRecord:
        records = processes if processes is not None else [
            process(801, role="server"),
            process(CLIENT_PID, role="client"),
        ]
        lease = lease_id if owner else None
        if owner and lease is None:
            lease = "lease-A"
        run = RunRecord(
            run_id,
            owner,
            lease,
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

    def sample(
        self,
        at: float,
        *,
        tick: int,
        foreground: int,
        idle_ms: int = 0,
        ok: bool = True,
    ) -> list[str]:
        return self.lifecycle.record_input_sample(
            InputSample(
                at=at,
                ok=ok,
                last_input_tick=tick if ok else None,
                idle_ms=idle_ms if ok else None,
                foreground_pid=foreground if ok else None,
            )
        )

    def credit_three_verified(self, start: float = 1000.0) -> float:
        self.sample(start, tick=100, foreground=CLIENT_PID)
        self.sample(start + 1.0, tick=200, foreground=CLIENT_PID)
        self.sample(start + 2.0, tick=300, foreground=CLIENT_PID)
        last_at = start + 3.0
        self.sample(last_at, tick=400, foreground=CLIENT_PID)
        return last_at

    def assert_block_shape(self, block: object) -> dict:
        self.assertIsInstance(block, dict)
        self.assertEqual(set(block), BLOCK_KEYS)
        self.assertIsInstance(block["runs"], list)
        return block

    def test_t1_counters_credit_stamp_updates_only(self) -> None:
        self.add_run(owner="A")
        last_at = self.credit_three_verified()
        clock = last_at + 0.5
        block = self.lifecycle.human_input_block("A", "lease-A", now=clock)
        block = self.assert_block_shape(block)
        self.assertEqual(len(block["runs"]), 1)
        row = block["runs"][0]
        self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual(row["run_id"], "run-x")
        self.assertEqual(row["count"], 3)
        self.assertEqual(row["uncertain_count"], 0)
        self.assertEqual(row["last_age_s"], round(clock - last_at, 3))
        # A stamp later than the clock has no age, as in human_input_age_s.
        early = self.lifecycle.human_input_block("A", "lease-A", now=last_at - 1.0)
        self.assertIsNone(early["runs"][0]["last_age_s"])
        self.assertEqual(early["runs"][0]["count"], 3)

        # A tick that did not advance, then new input with another pid in front
        # at both ends of the step: the attributor credits the run in front at
        # either end (input_activity.py:196-208), so the focus leaves first.
        self.sample(clock, tick=400, foreground=CLIENT_PID)
        self.sample(clock + 0.1, tick=400, foreground=OTHER_PID)
        self.sample(clock + 0.2, tick=500, foreground=OTHER_PID)
        again = self.lifecycle.human_input_block("A", "lease-A", now=clock + 0.3)
        again = self.assert_block_shape(again)
        self.assertEqual(len(again["runs"]), 1)
        self.assertEqual(again["runs"][0]["count"], 3)
        self.assertEqual(again["runs"][0]["uncertain_count"], 0)

        del self.guard.snapshots[CLIENT_PID]
        self.sample(clock + 0.4, tick=600, foreground=CLIENT_PID)
        uncertain = self.lifecycle.human_input_block("A", "lease-A", now=clock + 0.5)
        uncertain = self.assert_block_shape(uncertain)
        self.assertEqual(len(uncertain["runs"]), 1)
        self.assertEqual(uncertain["runs"][0]["count"], 3)
        self.assertEqual(uncertain["runs"][0]["uncertain_count"], 1)

        self.guard.snapshots[CLIENT_PID] = snapshot(process(CLIENT_PID, role="client"))
        self.sample(
            clock + 0.6, tick=700, foreground=CLIENT_PID, idle_ms=20000
        )
        stale_epoch = self.lifecycle.human_input_block("A", "lease-A", now=clock + 0.7)
        stale_epoch = self.assert_block_shape(stale_epoch)
        self.assertEqual(len(stale_epoch["runs"]), 1)
        self.assertEqual(stale_epoch["runs"][0]["count"], 3)
        self.assertEqual(stale_epoch["runs"][0]["uncertain_count"], 1)

        self.lifecycle._seal_terminal("run-x", clock + 1.0)
        self.store.replace(
            RunRecord(
                "run-x",
                None,
                None,
                "EXITED",
                "label",
                "@Mod",
                "profiles",
                "mission",
                [],
            )
        )
        retired = self.lifecycle.human_input_block("A", "lease-A", now=clock + 1.0)
        retired = self.assert_block_shape(retired)
        self.assertEqual(retired["runs"], [])
        self.assertFalse(
            [key for key in self.lifecycle._human_input_count if key[1] == "run-x"]
        )
        self.assertFalse(
            [key for key in self.lifecycle._uncertain_input_count if key[1] == "run-x"]
        )

    def test_t2_block_filters_owner_and_signal(self) -> None:
        self.add_run(owner="A")
        last_at = self.credit_three_verified()
        clock = last_at + 1.0
        owned = self.lifecycle.human_input_block("A", "lease-A", now=clock)
        owned = self.assert_block_shape(owned)
        self.assertEqual(owned["signal"], "ok")
        self.assertEqual(len(owned["runs"]), 1)
        self.assertEqual(owned["runs"][0]["run_id"], "run-x")
        self.assertEqual(owned["runs"][0]["count"], 3)

        self.assertEqual(
            self.lifecycle.human_input_block("A", "lease-B", now=clock)["runs"],
            [],
        )
        self.assertEqual(
            self.lifecycle.human_input_block("B", "lease-A", now=clock)["runs"],
            [],
        )
        self.assertEqual(
            self.lifecycle.human_input_block("", "lease-A", now=clock)["runs"],
            [],
        )
        self.assertEqual(
            self.lifecycle.human_input_block("A", "", now=clock)["runs"],
            [],
        )

        self.add_run("run-idle")
        idle_block = self.lifecycle.human_input_block("A", "lease-A", now=clock)
        idle_block = self.assert_block_shape(idle_block)
        self.assertEqual([row["run_id"] for row in idle_block["runs"]], ["run-x"])

        self.add_run(
            "run-a",
            owner="A",
            processes=[process(901, role="server"), process(902, role="client")],
        )
        both = self.lifecycle.human_input_block("A", "lease-A", now=clock)
        self.assertEqual(
            [(row["run_id"], row["count"], row["last_age_s"]) for row in both["runs"]],
            [("run-a", 0, None), ("run-x", 3, round(clock - last_at, 3))],
        )

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
            daemon_generation=GENERATION,
        )
        none = fresh.human_input_block("A", "lease-A", now=time.time())
        none = self.assert_block_shape(none)
        self.assertEqual(none["signal"], "unavailable")

        stale_at = last_at + INPUT_SIGNAL_STALE_S + 1.0
        stale = self.lifecycle.human_input_block("A", "lease-A", now=stale_at)
        stale = self.assert_block_shape(stale)
        self.assertEqual(stale["signal"], "unavailable")

        failed_clock = last_at + 3.0
        self.lifecycle.record_input_sample(InputSample(at=failed_clock, ok=False))
        failed = self.lifecycle.human_input_block("A", "lease-A", now=failed_clock)
        failed = self.assert_block_shape(failed)
        self.assertEqual(failed["signal"], "unavailable")


class HumanInputHttpTest(SessionLoopback, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.logs: list[str] = []
        self.httpd.log_sink = self.logs.append
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
        self.manifest = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.live: list[int] = []
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.manifest,
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
            daemon_generation=GENERATION,
        )
        self.state.lifecycle = self.lifecycle

    def tearDown(self) -> None:
        super().tearDown()
        self.temporary.cleanup()

    def acquire(self) -> tuple[str, dict]:
        status, body = self.request(
            "/session/acquire",
            {"identity": IDENTITY_PAYLOAD, "purpose": "test"},
        )
        self.assertEqual(status, 200)
        return body["lease_token"], body

    def add_owned_run(self, lease_id: str) -> None:
        records = [
            process(801, role="server"),
            process(CLIENT_PID, role="client"),
        ]
        run = RunRecord(
            BOUND_RUN,
            "A",
            lease_id,
            "RUNNING",
            "label",
            "@Mod",
            "profiles",
            "mission",
            list(records),
            daemon_generation_at_launch=GENERATION,
        )
        self.manifest.add(run)
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
            self.live.append(record.pid)

    def credit_one(self, at: float = 1000.0) -> None:
        self.lifecycle.record_input_sample(
            InputSample(
                at=at,
                ok=True,
                last_input_tick=100,
                idle_ms=0,
                foreground_pid=CLIENT_PID,
            )
        )
        self.lifecycle.record_input_sample(
            InputSample(
                at=at + 1.0,
                ok=True,
                last_input_tick=200,
                idle_ms=0,
                foreground_pid=CLIENT_PID,
            )
        )

    def test_t3_heartbeat_carries_block_only_on_200(self) -> None:
        token, acquired = self.acquire()
        lease_id = acquired["lease_id"]
        self.add_owned_run(lease_id)
        self.credit_one()
        status, body = self.request(
            "/session/heartbeat",
            {"identity": IDENTITY_PAYLOAD, "lease_token": token},
        )
        self.assertEqual(status, 200)
        self.assertIn("human_input", body)
        block = body["human_input"]
        self.assertEqual(set(block), BLOCK_KEYS)
        self.assertEqual(len(block["runs"]), 1)
        self.assertEqual(block["runs"][0]["run_id"], BOUND_RUN)
        self.assertEqual(block["runs"][0]["count"], 1)

        status, err = self.request(
            "/session/heartbeat",
            {"identity": IDENTITY_PAYLOAD, "lease_token": "not-this-lease"},
        )
        self.assertNotEqual(status, 200)
        self.assertNotIn("human_input", err)

        self.state.lifecycle = None
        status, plain = self.request(
            "/session/heartbeat",
            {"identity": IDENTITY_PAYLOAD, "lease_token": token},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("human_input", plain)

    def test_t4_await_adds_block_only_for_owned_done(self) -> None:
        token, acquired = self.acquire()
        lease_id = acquired["lease_id"]
        self.add_owned_run(lease_id)
        self.credit_one()
        status, enqueued = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_PAYLOAD,
                "lease_token": token,
                "cmd": "query_player_state",
                "args": {},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)
        command_id = enqueued["id"]
        wire = {"id": command_id, "ok": 1, "cmd": "query_player_state", "state": {"pos": [1.0, 2.0, 3.0]}}
        stored_status, stored = self.state.store_result(wire, instance=INST_SERVER)
        self.assertEqual(stored_status, 200, stored)
        self.assertNotIn("discarded", stored)
        status, done = _http(
            self.base,
            "GET",
            "/await",
            self.key,
            query={"id": str(command_id), "remove": "1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(done), {"status", "result", "human_input"})
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"]["ok"], 1)
        self.assertEqual(done["result"]["state"]["pos"], [1.0, 2.0, 3.0])
        self.assertNotIn("human_input", done["result"])
        block = done["human_input"]
        self.assertEqual(set(block), BLOCK_KEYS)
        self.assertEqual(len(block["runs"]), 1)
        self.assertEqual(block["runs"][0]["count"], 1)

        status, foreign = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_PAYLOAD,
                "cmd": "query_player_state",
                "args": {},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)
        foreign_id = foreign["id"]
        self.state.store_result(
            {"id": foreign_id, "ok": 1, "cmd": "query_player_state"},
            instance=INST_SERVER,
        )
        status, no_owner = _http(
            self.base,
            "GET",
            "/await",
            self.key,
            query={"id": str(foreign_id), "remove": "1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(no_owner), {"status", "result"})
        self.assertEqual(no_owner["status"], "done")
        self.assertNotIn("human_input", no_owner)

        status, pending_enq = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_PAYLOAD,
                "lease_token": token,
                "cmd": "query_player_state",
                "args": {},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)
        status, pending = _http(
            self.base,
            "GET",
            "/await",
            self.key,
            query={"id": str(pending_enq["id"]), "remove": "1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(pending, {"status": "pending"})

    def answered(self, call):
        # M9: an exception that escapes the handler drops the connection. That
        # is the failure T7 is for, so it fails as an assertion, not an error.
        try:
            return call()
        except (OSError, http.client.HTTPException) as exc:
            self.fail(f"the daemon did not answer: {exc!r}")

    def test_t7_block_exception_does_not_fail_the_call(self) -> None:
        token, acquired = self.acquire()
        lease_id = acquired["lease_id"]
        self.add_owned_run(lease_id)

        def boom(*_args, **_kwargs):
            raise RuntimeError("inbox-6310-boom")

        self.lifecycle.human_input_block = boom  # type: ignore[method-assign]
        self.logs.clear()
        status, beat = self.answered(
            lambda: self.request(
                "/session/heartbeat",
                {"identity": IDENTITY_PAYLOAD, "lease_token": token},
            )
        )
        self.assertEqual(status, 200)
        self.assertIn("human_input", beat)
        self.assertEqual(set(beat["human_input"]), BLOCK_KEYS)
        self.assertEqual(beat["human_input"]["signal"], "unavailable")
        self.assertEqual(beat["human_input"]["runs"], [])
        self.assertEqual(
            sum(1 for line in self.logs if "human_input_block failed" in line),
            1,
        )

        status, enqueued = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_PAYLOAD,
                "lease_token": token,
                "cmd": "query_player_state",
                "args": {},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)
        self.state.store_result(
            {"id": enqueued["id"], "ok": 1, "cmd": "query_player_state"},
            instance=INST_SERVER,
        )
        self.logs.clear()
        status, done = self.answered(
            lambda: _http(
                self.base,
                "GET",
                "/await",
                self.key,
                query={"id": str(enqueued["id"]), "remove": "1"},
            )
        )
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "done")
        self.assertIn("human_input", done)
        self.assertEqual(done["human_input"]["signal"], "unavailable")
        self.assertEqual(done["human_input"]["runs"], [])
        self.assertEqual(
            sum(1 for line in self.logs if "human_input_block failed" in line),
            1,
        )


class HumanInputClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_t5_await_result_copies_block_after_prune(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from dayz_mcp.server import ClientRuntime

        runtime = object.__new__(ClientRuntime)
        runtime._time_fn = time.monotonic
        block = {
            "signal": "ok",
            "runs": [
                {
                    "run_id": "run-x",
                    "count": 3,
                    "uncertain_count": 0,
                    "last_age_s": 1.0,
                }
            ],
        }
        wire = {"ok": 1, "clicked": 0, "handler": "", "user_id": 0, "y": 12.5}

        def _call_with_block(*_args, **_kwargs):
            return 200, {
                "status": "done",
                "result": dict(wire),
                "human_input": block,
            }

        runtime._call = _call_with_block
        waited = await runtime._await_result("surface_query", 1, "server", 1.0)
        self.assertEqual(waited["y"], 12.5)
        self.assertNotIn("clicked", waited)
        self.assertIn("human_input", waited)
        self.assertEqual(waited["human_input"], block)

        def _call_plain(*_args, **_kwargs):
            return 200, {"status": "done", "result": dict(wire)}

        runtime._call = _call_plain
        plain = await runtime._await_result("surface_query", 2, "server", 1.0)
        self.assertNotIn("human_input", plain)
        self.assertEqual(plain["y"], 12.5)

        def _call_error(*_args, **_kwargs):
            return 200, {
                "status": "done",
                "result": {"ok": 0, "error": "boom"},
                "human_input": block,
            }

        runtime._call = _call_error
        with self.assertRaises(ToolError):
            await runtime._await_result("surface_query", 3, "server", 1.0)

    async def test_t6_tools_return_call_bridge_row(self) -> None:
        from dayz_mcp.server import ServerConfig, build_app

        app, runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        block = {
            "signal": "ok",
            "runs": [
                {
                    "run_id": "run-x",
                    "count": 2,
                    "uncertain_count": 1,
                    "last_age_s": 0.25,
                }
            ],
        }

        async def fake_call_bridge(cmd, args, peer, timeout_s, expected_fence=None):
            row = {"ok": 1, "cmd": cmd, "human_input": block}
            if cmd == "object_delete":
                row["deleted"] = 1
            return row

        runtime.call_bridge = fake_call_bridge
        player = _content_json(
            await app.call_tool("query_player_state", {"timeout_s": 1.0})
        )
        self.assertIn("human_input", player)
        self.assertEqual(player["human_input"], block)
        deleted = _content_json(
            await app.call_tool(
                "object_delete", {"object_id": 7, "timeout_s": 1.0}
            )
        )
        self.assertEqual(deleted["human_input"], block)
        focused = _content_json(
            await app.call_tool("ui_focus", {"path": "root", "timeout_s": 1.0})
        )
        self.assertEqual(focused["human_input"], block)


if __name__ == "__main__":
    unittest.main()
