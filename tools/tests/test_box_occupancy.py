from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server as server_module
from dayz_mcp.dayz_test_worker import _pre_admission_rejection
from dayz_mcp.process_lifecycle import occupancy_error_fields, parse_dayz_launch_argv
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
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
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
                "mode": "offline",
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
                        "mode": "offline",
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
                    "mode": "offline",
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
                        "mode": "offline",
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
                "mode": "offline",
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
                    {"project": "ExampleMod", "mode": "offline"},
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


if __name__ == "__main__":
    unittest.main()
