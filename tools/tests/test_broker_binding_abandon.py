"""Joint broker cases: opaque identity, fenced admission, id-directed abandon.

The fixture is a real ClientRuntime, its real ``_call``, the real accredited
transport (accreditation inputs point at this process), real HTTP sockets and
handlers, and a real ServerState plus coordinator. No DayZ process is started.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import unittest
import uuid
from types import SimpleNamespace

from dayz_mcp import core, loopback, server
from dayz_mcp.accredited_daemon_transport import AccreditedTransportError
from dayz_mcp.server import ServerConfig, ToolError
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.broker_binding_helpers import BrokerFixture, ProbeLifecycle, release_registry
from tests.client_helpers import _fixture_client_runtime
from tests.daemon_helpers import _http
from tests.fence_helpers import (
    INST_CLIENT,
    INST_SERVER,
    PID_CLIENT,
    PID_SERVER,
    poll_census_query,
)

_CTIME = "2026-08-18T00:00:00.000000Z"

# Names the original module used. The fixture lives in a helper so other tests
# can reuse it without a test-to-test import.
_Lifecycle = ProbeLifecycle
_release_registry = release_registry


class BrokerBindingAbandonTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fixtures: list[BrokerFixture] = []

    def tearDown(self) -> None:
        for fixture in self.fixtures:
            fixture.stop()

    def _open(self, **kwargs) -> BrokerFixture:
        fixture = BrokerFixture(self, **kwargs)
        self.fixtures.append(fixture)
        return fixture

    async def test_status_identity(self) -> None:
        fixture = self._open()
        server_token = fixture.bind(INST_SERVER, "server", PID_SERVER)
        raw = fixture.state.status_snapshot()
        rich = core.build_status(
            raw, require_version=False, expected_game_version=None
        )
        self.assertEqual(raw["peers"]["server"]["binding_token"], server_token)
        self.assertEqual(rich["server_peer"]["binding_token"], server_token)
        self.assertEqual(rich["server_peer"]["run_id"], "run-broker")
        self.assertIsNone(raw["peers"]["client"]["binding_token"])
        self.assertIsNone(rich["client_peer"]["binding_token"])
        self.assertNotIn("|", server_token.split("|")[0])
        self.assertGreater(server_token.count("|"), 2)
        status, payload = fixture.state.enqueue_command(
            "query_player_state", {}, peer="server"
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(fixture.state._legacy_queues["client"], [])
        fixture.bind(INST_CLIENT, "client", PID_CLIENT)
        # A prefix is not a token. Admission must not accept it.
        prefix = server_token.split("|")[0][:8]
        with self.assertRaises(ToolError) as raised:
            await fixture.runtime.call_bridge(
                "key_press",
                {"dik": 1},
                "client",
                1.0,
                expected_fence={
                    "generation": "broker-gen",
                    "peers": {
                        "server": {"run_id": "run-broker", "binding_token": prefix}
                    },
                },
            )
        self.assertIn("binding_changed", str(raised.exception))
        # Older daemon: the rich view has no token to preserve.
        older = core.build_status(
            {"peers": {"server": {"queue_depth": 0}, "client": {"queue_depth": 0}}, "results_pending": 0},
            require_version=False,
            expected_game_version=None,
        )
        self.assertIsNone(older["server_peer"]["binding_token"])
        self.assertIsNone(older["client_peer"]["run_id"])

    async def test_fence_from_production_status_admits(self) -> None:
        fixture = self._open()
        fixture.bind(INST_SERVER, "server", PID_SERVER)
        status = await fixture.runtime.bridge_status_payload()
        fence = {
            "generation": status["daemon_generation"],
            "peers": {
                "server": {
                    "run_id": status["server_peer"]["run_id"],
                    "binding_token": status["server_peer"]["binding_token"],
                }
            },
        }
        command_id = await fixture.runtime.enqueue_bridge(
            "query_player_state", {}, "server", 2.0, expected_fence=fence
        )
        self.assertEqual(
            [command["id"] for command in fixture.state._bound_queues[INST_SERVER]],
            [command_id],
        )

    async def test_admission_fence(self) -> None:
        changed = {"done": False}

        def shift(_run_id, _destination) -> None:
            binding = fixture.state._bindings[INST_CLIENT]
            fixture.state._station_epoch += 3
            binding.epoch = fixture.state._station_epoch
            changed["done"] = True

        fixture = self._open()
        fixture.state.lifecycle = _Lifecycle("run-broker", on_probe=shift)
        token = fixture.bind(INST_CLIENT, "client", PID_CLIENT)
        fence = {
            "generation": "broker-gen",
            "peers": {"client": {"run_id": "run-broker", "binding_token": token}},
        }
        with self.assertRaises(ToolError) as malformed:
            await fixture.runtime.call_bridge(
                "key_press",
                {"dik": 1},
                "client",
                1.0,
                expected_fence={"generation": "broker-gen", "peers": {}},
            )
        self.assertIn("bad_binding_fence", str(malformed.exception))
        with self.assertRaises(ToolError) as moved:
            await fixture.runtime.call_bridge(
                "key_press",
                {"dik": 1},
                "client",
                1.0,
                expected_fence=fence,
            )
        self.assertIn("binding_changed", str(moved.exception))
        self.assertTrue(changed["done"])
        queued = fixture.state._bound_queues.get(INST_CLIENT, [])
        self.assertEqual(queued, [])

    async def test_queued_authority(self) -> None:
        fixture = self._open(coordination=True)
        fixture.bind(INST_SERVER, "server", PID_SERVER)
        fixture.state.lifecycle = _Lifecycle("run-broker")
        identity = fixture.runtime.identity
        status, grant = fixture.state.coordination.acquire(identity, "broker")
        self.assertEqual(status, 200, grant)
        token = grant["lease_token"]
        fixture.runtime._control.active_lease_token = token
        first = await fixture.runtime.enqueue_bridge(
            "player_heal", {"full": True}, "server", 2.0
        )
        second = await fixture.runtime.enqueue_bridge(
            "player_heal", {"full": True}, "server", 2.0
        )
        wrong = fixture.http_abandon(
            identity.to_payload(), "broker-gen", first, "cancelled", token="other-lease-token"
        )
        self.assertEqual(wrong[0], 403, wrong[1])
        other = ClientIdentity(
            identity.platform,
            identity.pid,
            identity.ppid,
            identity.started_at_utc,
            str(uuid.uuid4()),
            "",
        )
        stranger = fixture.http_abandon(
            other.to_payload(), "broker-gen", first, "cancelled", token=token
        )
        self.assertEqual(stranger[0], 403, stranger[1])
        dropped = await fixture.runtime.abandon_bridge(first, "cancelled")
        self.assertEqual(dropped.get("status"), "dropped", dropped)
        again = await fixture.runtime.abandon_bridge(first, "cancelled")
        self.assertEqual(again.get("status"), "noop", again)
        ids = [command["id"] for command in fixture.state._bound_queues[INST_SERVER]]
        self.assertEqual(ids, [second])
        self.assertNotIn(first, ids)

    async def test_delivered_once(self) -> None:
        fixture = self._open(registry=_release_registry())
        fixture.bind(INST_CLIENT, "client", PID_CLIENT)
        command_id = await fixture.runtime.enqueue_bridge(
            "vehicle_telemetry", {}, "client", 2.0
        )
        _status, polled = fixture.state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        self.assertEqual([item["id"] for item in polled["commands"]], [command_id])
        barrier = threading.Barrier(2)
        results = []

        def abandon() -> None:
            barrier.wait(timeout=2)
            results.append(
                asyncio.run(fixture.runtime.abandon_bridge(command_id, "cancelled"))
            )

        threads = [threading.Thread(target=abandon), threading.Thread(target=abandon)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        statuses = sorted(item.get("status") for item in results)
        self.assertEqual(statuses, ["noop", "release_queued"])
        releases = [
            command
            for command in fixture.state._bound_queues.get(INST_CLIENT, [])
            if command.get("cmd") == "vehicle_release"
        ]
        self.assertEqual(len(releases), 1)
        late_status, late = fixture.state.store_result(
            {"id": command_id, "ok": 1}, instance=INST_CLIENT
        )
        self.assertEqual(late_status, 200)
        self.assertTrue(late.get("discarded"))
        self.assertIsNone(fixture.state.take_result(command_id, remove=True))
        # A terminal result is not a release.
        bare = self._open()
        bare.bind(INST_CLIENT, "client", PID_CLIENT)
        plain = await bare.runtime.enqueue_bridge(
            "vehicle_telemetry", {}, "client", 2.0
        )
        bare.state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        stored, body = bare.state.store_result(
            {"id": plain, "ok": 1, "cmd": "vehicle_telemetry"}, instance=INST_CLIENT
        )
        self.assertEqual(stored, 200, body)
        finished = await bare.runtime.abandon_bridge(plain, "cancelled")
        self.assertEqual(finished.get("status"), "noop", finished)
        self.assertEqual(bare.state._bound_queues.get(INST_CLIENT, []), [])

    async def test_original_destination(self) -> None:
        fixture = self._open(registry=_release_registry())
        token = fixture.bind(INST_CLIENT, "client", PID_CLIENT, run_id="run-a")
        command_id = await fixture.runtime.enqueue_bridge(
            "vehicle_telemetry", {}, "client", 2.0
        )
        fixture.state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        fixture.state.retire_run("run-a", "replace-role")
        replacement = "bbbbbbbb-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.assertEqual(replacement[:8], INST_CLIENT[:8])
        fixture.bind(replacement, "client", PID_CLIENT + 1, run_id="run-b")
        outcome = await fixture.runtime.abandon_bridge(command_id, "cancelled")
        self.assertEqual(outcome.get("status"), "cleanup_degraded", outcome)
        self.assertEqual(
            fixture.state._bound_queues.get(replacement, []),
            [],
        )
        self.assertNotEqual(token, fixture.state.bound_instance_token("run-b", "client"))

    async def test_cancel_cleanup(self) -> None:
        fixture = self._open()
        fixture.bind(INST_SERVER, "server", PID_SERVER)
        awaiting = threading.Event()
        real_call = fixture.runtime._call

        def note_await(method, path, *args, **kwargs):
            if path == "/await":
                awaiting.set()
            return real_call(method, path, *args, **kwargs)

        fixture.runtime._call = note_await
        task = asyncio.create_task(
            fixture.runtime.call_bridge("player_heal", {"full": True}, "server", 5.0)
        )
        self.assertTrue(await asyncio.to_thread(awaiting.wait, 5))
        self.assertTrue(fixture.state._bound_queues[INST_SERVER])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(fixture.state._bound_queues[INST_SERVER], [])
        blocked = self._open()
        held = threading.Event()
        release = threading.Event()

        def hold_lock() -> None:
            with blocked.state._lock:
                held.set()
                release.wait(timeout=8)

        worker = threading.Thread(target=hold_lock)
        worker.start()
        self.assertTrue(held.wait(timeout=2))
        # Enqueue before the lock is taken is impossible now; enqueue first.
        release.set()
        worker.join(timeout=2)
        blocked.bind(INST_SERVER, "server", PID_SERVER)
        queued = await blocked.runtime.enqueue_bridge(
            "player_heal", {"full": True}, "server", 2.0
        )
        self.assertIsInstance(queued, int)
        held.clear()
        release.clear()
        worker = threading.Thread(target=hold_lock)
        worker.start()
        self.assertTrue(held.wait(timeout=2))
        task = asyncio.create_task(
            blocked.runtime.await_enqueued("player_heal", queued, "server", 5.0)
        )
        await asyncio.sleep(0.05)
        started = time.monotonic()
        task.cancel()
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        elapsed = time.monotonic() - started
        release.set()
        worker.join(timeout=3)
        self.assertLess(elapsed, 2.5)

    def test_discovery_deadline_stays_a_budget_expiry(self) -> None:
        runtime = self._open().runtime

        def refused(*_args, **_kwargs):
            raise AccreditedTransportError(
                "daemon_request_deadline_exceeded",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        def discovery_past_deadline(_deadline=None):
            raise TimeoutError("daemon_request_deadline_exceeded")

        runtime._request_once = refused
        runtime._ensure_daemon = discovery_past_deadline
        with self.assertRaises(server._CallBudgetExpired):
            runtime._call("GET", "/await", None, {"id": "1", "remove": "1"}, 1.0)
        with self.assertRaises(ToolError):
            runtime._call("POST", "/enqueue", {}, None, 1.0)

    async def test_cancel_while_enqueue_in_flight(self) -> None:
        fixture = self._open()
        fixture.bind(INST_SERVER, "server", PID_SERVER)
        published = threading.Event()
        answer_held = threading.Event()
        real_call = fixture.runtime._call

        def answer_late(method, path, *args, **kwargs):
            answer = real_call(method, path, *args, **kwargs)
            if path == "/enqueue":
                published.set()
                answer_held.wait(timeout=5)
            return answer

        fixture.runtime._call = answer_late
        task = asyncio.create_task(
            fixture.runtime.call_bridge("player_heal", {"full": True}, "server", 5.0)
        )
        self.assertTrue(await asyncio.to_thread(published.wait, 5))
        self.assertTrue(fixture.state._bound_queues[INST_SERVER])
        task.cancel()
        await asyncio.sleep(0.05)
        answer_held.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(fixture.state._bound_queues[INST_SERVER], [])

    async def test_abandon_transport_stops_at_the_budget(self) -> None:
        runtime = self._open().runtime
        attempts: list[float] = []

        def refused(*_args, **_kwargs):
            attempts.append(time.monotonic())
            raise AccreditedTransportError(
                "daemon_unreachable",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        def slow_discovery(_deadline=None):
            time.sleep(0.3)
            return True

        runtime._request_once = refused
        runtime._ensure_daemon = slow_discovery
        loop = asyncio.get_running_loop()
        with self.assertRaises(ToolError):
            await runtime.abandon_bridge(7, "cancelled", budget_deadline=loop.time() + 0.2)
        # The discovery outlived the budget: no second /abandon is sent.
        self.assertEqual(len(attempts), 1)
        self.assertEqual(
            await runtime.abandon_bridge(7, "cancelled", budget_deadline=loop.time()),
            {"status": "cleanup_degraded", "cause": "budget_exhausted"},
        )
        self.assertEqual(len(attempts), 1)
        # A cleanup whose abandon starts only after its deadline (the event loop
        # was blocked in between) sends nothing.
        loop.call_soon(time.sleep, 0.3)
        await server._shielded_broker_cleanup(
            runtime, 7, "cancelled", budget_deadline=loop.time() + 0.2
        )
        self.assertEqual(len(attempts), 1)

    async def test_timeout_split(self) -> None:
        fixture = self._open()
        fixture.bind(INST_SERVER, "server", PID_SERVER)
        with self.assertRaises(ToolError) as timed:
            await fixture.runtime.call_bridge(
                "player_heal", {"full": True}, "server", 1.0
            )
        self.assertIn("timeout waiting", str(timed.exception))
        self.assertEqual(fixture.state._bound_queues[INST_SERVER], [])
        clock = {"now": 0.0}
        state = loopback.ServerState("k", time_fn=lambda: clock["now"])
        state.daemon_generation = "broker-gen"
        state.install_bound_peer(
            instance=INST_SERVER,
            role="server",
            pid=PID_SERVER,
            run_id="run-broker",
            creation_time_utc=_CTIME,
        )
        status, payload = state.enqueue_command(
            "player_heal", {"full": True}, peer="server", operation_timeout_s=1.0
        )
        self.assertEqual(status, 200, payload)
        clock["now"] = 5.0
        self.assertEqual(state.reap_expired_commands(), 1)
        self.assertEqual(state._bound_queues[INST_SERVER], [])
        self.assertEqual(state.reap_expired_commands(), 0)
        separate = self._open()
        separate.bind(INST_SERVER, "server", PID_SERVER)
        command_id = await separate.runtime.enqueue_bridge(
            "player_heal", {"full": True}, "server", 2.0
        )
        with self.assertRaises(ToolError) as waited:
            await separate.runtime.await_enqueued(
                "player_heal", command_id, "server", 0.4
            )
        self.assertIn("timeout waiting", str(waited.exception))
        self.assertEqual(separate.state._bound_queues[INST_SERVER], [])

    def test_release_builder_refusal_keeps_its_code(self) -> None:
        self.assertEqual(
            server._public_enqueue_error({"error": "bad_release_builder"}),
            "bad_release_builder",
        )

    async def test_release_composition(self) -> None:
        def builder(_command_id, _args):
            return {}

        with self.assertRaises(ValueError):
            loopback.ServerState(
                "k",
                release_registry=(("nope", "vehicle_release", builder),),
            )
        with self.assertRaises(ValueError):
            loopback.ServerState(
                "k",
                release_registry=(("query_player_state", "exec_enforce", builder),),
            )
        with self.assertRaises(ValueError):
            loopback.ServerState(
                "k",
                enable_exec_enforce=True,
                release_registry=(
                    (
                        "query_player_state",
                        "exec_enforce",
                        lambda command_id, args: {"expr": "not-allowed"},
                    ),
                ),
            )
        with self.assertRaises(ValueError):
            loopback.ServerState(
                "k",
                release_registry=(("key_press", "query_player_state", builder),),
            )
        with self.assertRaises(ValueError):
            loopback.ServerState(
                "k",
                release_registry=(("key_press", "key_press", builder),),
            )
        with self.assertRaises(ValueError):
            loopback.ServerState(
                "k",
                release_registry=(
                    ("vehicle_telemetry", "vehicle_release", builder),
                    ("vehicle_release", "key_press", builder),
                ),
            )
        state = loopback.ServerState("k", release_registry=_release_registry())
        self.assertNotIn("not_a_release_target", state.whitelisted_commands())
        self.assertIn("vehicle_release", state.whitelisted_commands())
        # Declared targets do not enlarge the catalog: exec stays out.
        self.assertNotIn("exec_enforce", state.whitelisted_commands())
        # The whitelist-union mutant adds declared target names to the catalog.
        state._release_target_names = frozenset({"exec_enforce"})
        self.assertNotIn("exec_enforce", state.whitelisted_commands())
        state._release_target_names = frozenset({"vehicle_release"})
        state.install_bound_peer(
            instance=INST_CLIENT,
            role="client",
            pid=PID_CLIENT,
            run_id="run-broker",
            creation_time_utc=_CTIME,
        )
        status, payload = state.enqueue_command(
            "vehicle_telemetry", {}, peer="client"
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(state._bound_queues[INST_CLIENT]), 1)
        bad = loopback.ServerState(
            "k",
            release_registry=(
                (
                    "vehicle_telemetry",
                    "vehicle_release",
                    lambda command_id, args: {"nope": True},
                ),
            ),
        )
        # Composition accepted the callable; admission rejects the built args.
        bad.install_bound_peer(
            instance=INST_CLIENT,
            role="client",
            pid=PID_CLIENT,
            run_id="run-broker",
            creation_time_utc=_CTIME,
        )
        refused, refused_body = bad.enqueue_command(
            "vehicle_telemetry", {}, peer="client"
        )
        self.assertEqual(refused, 400, refused_body)
        self.assertEqual(bad._bound_queues.get(INST_CLIENT, []), [])

    def test_legacy_release_is_unidentified(self) -> None:
        state = loopback.ServerState("k", release_registry=_release_registry())
        status, payload = state.enqueue_command(
            "vehicle_telemetry", {}, peer="client"
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(state._legacy_queues["client"][0]["_broker_pin"]["legacy"])
        polled, body = state.record_poll("client", None)
        self.assertEqual(polled, 200, body)
        self.assertEqual(body["commands"][0]["cmd"], "vehicle_telemetry")
        outcome = state.abandon_command(payload["id"], "cancelled")
        self.assertEqual(outcome.get("status"), "cleanup_degraded", outcome)
        self.assertEqual(outcome.get("cause"), "unidentified_destination")
        self.assertEqual(state._legacy_queues["client"], [])
        self.assertEqual(state._broker_rows, {})

    async def test_probe_drops_terminal_context(self) -> None:
        fixture = self._open()
        fixture.bind(INST_CLIENT, "client", PID_CLIENT)
        first = await fixture.runtime.enqueue_bridge(
            "vehicle_telemetry", {}, "client", 2.0
        )
        second = await fixture.runtime.enqueue_bridge(
            "vehicle_telemetry", {}, "client", 2.0
        )
        self.assertIn(first, fixture.runtime._broker_admits)
        fixture.state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        stored, body = fixture.state.store_result(
            {"id": first, "ok": 1, "cmd": "vehicle_telemetry"}, instance=INST_CLIENT
        )
        self.assertEqual(stored, 200, body)
        got = await fixture.runtime.probe_bridge_result(
            "vehicle_telemetry", first, "client"
        )
        self.assertEqual(got.get("ok"), 1)
        self.assertNotIn(first, fixture.runtime._broker_admits)
        fixture.state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        stored, body = fixture.state.store_result(
            {"id": second, "ok": 0, "error": "bridge_down"}, instance=INST_CLIENT
        )
        self.assertEqual(stored, 200, body)
        with self.assertRaises(ToolError):
            await fixture.runtime.probe_bridge_result(
                "vehicle_telemetry", second, "client"
            )
        self.assertEqual(fixture.runtime._broker_admits, {})

        state = loopback.ServerState("k", time_fn=time.monotonic)
        state.daemon_generation = "broker-gen"
        state.install_bound_peer(
            instance=INST_SERVER,
            role="server",
            pid=PID_SERVER,
            run_id="run-broker",
            creation_time_utc=_CTIME,
        )
        embedded = server.Runtime(
            ServerConfig(key="k", port=0, log_sink=lambda _message: None)
        )
        embedded.loopback = SimpleNamespace(state=state)
        left = await embedded.enqueue_bridge(
            "query_player_state", {}, "server", 2.0
        )
        right = await embedded.enqueue_bridge(
            "query_player_state", {}, "server", 2.0
        )
        state.record_poll(
            "server", None, instance=INST_SERVER, source_pid=PID_SERVER
        )
        state.store_result({"id": left, "ok": 1}, instance=INST_SERVER)
        state.store_result(
            {"id": right, "ok": 0, "error": "bridge_down"}, instance=INST_SERVER
        )
        await embedded.probe_bridge_result("query_player_state", left, "server")
        with self.assertRaises(ToolError):
            await embedded.probe_bridge_result(
                "query_player_state", right, "server"
            )
        self.assertEqual(getattr(embedded, "_broker_admits", {}), {})

    def test_unleased_admitter_can_abandon(self) -> None:
        fixture = self._open(coordination=True)
        fixture.bind(INST_SERVER, "server", PID_SERVER)
        owner = ClientIdentity(
            "codex", os.getpid(), os.getppid(), _CTIME, "session-owner", ""
        )
        status, payload = fixture.state.enqueue_command(
            "query_player_state",
            {},
            peer="server",
            identity_payload=owner.to_payload(),
        )
        self.assertEqual(status, 200, payload)
        row = fixture.state._broker_rows[payload["id"]]
        self.assertEqual(row["owner"].session_id, owner.session_id)
        other = ClientIdentity(
            "codex", os.getpid(), os.getppid(), _CTIME, "session-other", ""
        )
        forbidden, forbidden_body = fixture.state.request_abandon(
            identity_payload=other.to_payload(),
            generation="broker-gen",
            command_id=payload["id"],
            reason="cancelled",
        )
        self.assertEqual(forbidden, 403, forbidden_body)
        self.assertIn(payload["id"], fixture.state._broker_rows)
        allowed, allowed_body = fixture.state.request_abandon(
            identity_payload=owner.to_payload(),
            generation="broker-gen",
            command_id=payload["id"],
            reason="cancelled",
        )
        self.assertEqual(allowed, 200, allowed_body)
        self.assertEqual(allowed_body.get("status"), "dropped")
        self.assertNotIn(payload["id"], fixture.state._broker_rows)

    def test_lease_release_keeps_a_queued_release(self) -> None:
        state = loopback.ServerState(
            "k", release_registry=_release_registry(), time_fn=time.monotonic
        )
        state.coordination = SessionCoordinator(
            daemon_generation="broker-gen",
            cleanup=lambda session_id, lease_id, reason, vehicle_active: (
                state.cleanup_owner(session_id, lease_id, reason, vehicle_active)
            ),
        )
        state.daemon_generation = "broker-gen"
        state.retail_probe = lambda: {"known": True, "processes": []}
        state.install_bound_peer(
            instance=INST_CLIENT,
            role="client",
            pid=PID_CLIENT,
            run_id="run-broker",
            creation_time_utc=_CTIME,
        )
        owner = ClientIdentity(
            "codex", os.getpid(), os.getppid(), _CTIME, "session-owner", ""
        )
        acquired = state.coordination.acquire(owner, "trace")
        self.assertEqual(acquired[0], 200, acquired)
        token = acquired[1]["lease_token"]
        status, payload = state.enqueue_command(
            "vehicle_telemetry",
            {},
            peer="client",
            identity_payload=owner.to_payload(),
            lease_token=token,
        )
        self.assertEqual(status, 200, payload)
        state.record_poll("client", None, instance=INST_CLIENT, source_pid=PID_CLIENT)
        abandoned, abandoned_body = state.request_abandon(
            identity_payload=owner.to_payload(),
            generation="broker-gen",
            command_id=payload["id"],
            reason="cancelled",
            lease_token=token,
            lease_token_supplied=True,
        )
        self.assertEqual(abandoned_body.get("status"), "release_queued", abandoned_body)
        released = state.coordination.release(owner, token, "owner_release")
        self.assertEqual(released[0], 200, released)
        polled, body = state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        self.assertEqual(polled, 200, body)
        self.assertEqual(
            [command["id"] for command in body["commands"]],
            [abandoned_body["release_id"]],
        )

    def test_release_owner_cleared_on_terminal_result(self) -> None:
        state = loopback.ServerState(
            "k",
            coordination=SessionCoordinator(daemon_generation="broker-gen"),
            release_registry=_release_registry(),
            time_fn=time.monotonic,
        )
        state.daemon_generation = "broker-gen"
        state.retail_probe = lambda: {"known": True, "processes": []}
        state.install_bound_peer(
            instance=INST_CLIENT,
            role="client",
            pid=PID_CLIENT,
            run_id="run-broker",
            creation_time_utc=_CTIME,
        )
        owner = ClientIdentity(
            "codex", os.getpid(), os.getppid(), _CTIME, "session-owner", ""
        )
        acquired = state.coordination.acquire(owner, "trace")
        self.assertEqual(acquired[0], 200, acquired)
        token = acquired[1]["lease_token"]
        status, payload = state.enqueue_command(
            "vehicle_telemetry",
            {},
            peer="client",
            identity_payload=owner.to_payload(),
            lease_token=token,
        )
        self.assertEqual(status, 200, payload)
        polled, body = state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        self.assertEqual(polled, 200, body)
        self.assertEqual(body["commands"][0]["cmd"], "vehicle_telemetry")
        abandoned, abandoned_body = state.request_abandon(
            identity_payload=owner.to_payload(),
            generation="broker-gen",
            command_id=payload["id"],
            reason="cancelled",
            lease_token=token,
            lease_token_supplied=True,
        )
        self.assertEqual(abandoned, 200, abandoned_body)
        self.assertEqual(abandoned_body.get("status"), "release_queued")
        release_id = abandoned_body["release_id"]
        polled, body = state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        self.assertEqual(body["commands"][0]["id"], release_id)
        stored, stored_body = state.store_result(
            {"id": release_id, "ok": 1, "cmd": "vehicle_release"},
            instance=INST_CLIENT,
        )
        self.assertEqual(stored, 200, stored_body)
        self.assertNotIn(release_id, state._command_owner)
        self.assertEqual(state.pending_for_owner(owner.session_id), 0)

    async def test_embedded_abandon_reports_outcome(self) -> None:
        state = loopback.ServerState(
            "k", release_registry=_release_registry(), time_fn=time.monotonic
        )
        state.daemon_generation = "broker-gen"
        state.install_bound_peer(
            instance=INST_CLIENT,
            role="client",
            pid=PID_CLIENT,
            run_id="run-broker",
            creation_time_utc=_CTIME,
        )
        runtime = server.Runtime(
            ServerConfig(key="k", port=0, log_sink=lambda _message: None)
        )
        runtime.loopback = SimpleNamespace(state=state)
        command_id = await runtime.enqueue_bridge(
            "vehicle_telemetry", {}, "client", 2.0
        )
        state.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        published = await runtime.abandon_bridge(command_id, "cancelled")
        self.assertEqual(published.get("status"), "release_queued", published)
        self.assertIsInstance(published.get("release_id"), int)
        retired = loopback.ServerState(
            "k", release_registry=_release_registry(), time_fn=time.monotonic
        )
        retired.daemon_generation = "broker-gen"
        retired.install_bound_peer(
            instance=INST_CLIENT,
            role="client",
            pid=PID_CLIENT,
            run_id="run-broker",
            creation_time_utc=_CTIME,
        )
        runtime.loopback = SimpleNamespace(state=retired)
        command_id = await runtime.enqueue_bridge(
            "vehicle_telemetry", {}, "client", 2.0
        )
        retired.record_poll(
            "client", None, instance=INST_CLIENT, source_pid=PID_CLIENT
        )
        retired.retire_run("run-broker", "replace-role")
        degraded = await runtime.abandon_bridge(command_id, "cancelled")
        self.assertEqual(degraded.get("status"), "cleanup_degraded", degraded)
        self.assertTrue(degraded.get("cause"))

    def _hold_fixture(self) -> BrokerFixture:
        fixture = self._open(coordination=True)
        fixture.bind(INST_CLIENT, "client", PID_CLIENT)
        fixture.state.lifecycle = _Lifecycle("run-broker")
        identity = fixture.runtime.identity
        status, grant = fixture.state.coordination.acquire(identity, "broker-hold")
        self.assertEqual(status, 200, grant)
        fixture.runtime._control.active_lease_token = grant["lease_token"]
        return fixture

    async def _deliver_hold(self, fixture: BrokerFixture) -> tuple[int, str]:
        query = poll_census_query("client")
        fixture.state.record_poll(
            "client",
            "12~1.29",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=query["caps"],
        )
        command_id, minted = await fixture.runtime.enqueue_action_hold(
            {
                "action": "ActionDeployObject",
                "selector": "hands",
                "target": "hands",
                "classname": "FenceKit",
                "hold_timeout_s": 120.0,
            },
            135.0,
        )
        _status, polled = fixture.state.record_poll(
            "client",
            "12~1.29",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=query["caps"],
        )
        self.assertIn(command_id, [item["id"] for item in polled["commands"]])
        self.assertTrue(fixture.state._holds[minted]["delivered"])
        return command_id, minted

    async def test_f13_cancelled_await_queues_one_directed_cancel(self) -> None:
        fixture = self._hold_fixture()
        command_id, minted = await self._deliver_hold(fixture)
        task = asyncio.create_task(
            fixture.runtime.await_action_hold(command_id, minted, 120.0)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        cancels = [
            item
            for item in fixture.state._bound_queues[INST_CLIENT]
            if item.get("cmd") == "action_hold_cancel"
        ]
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0]["args"]["hold_id"], minted)

    async def test_f15_unknown_result_id_does_not_reconcile(self) -> None:
        fixture = self._hold_fixture()
        command_id, minted = await self._deliver_hold(fixture)
        abandoned = await fixture.runtime.abandon_bridge(command_id, "cancelled")
        self.assertEqual(abandoned.get("status"), "release_queued", abandoned)
        self.assertIn("run-broker", fixture.state._hold_blocked_runs)
        status, body = _http(
            f"http://127.0.0.1:{fixture.port}",
            "POST",
            "/result",
            "broker-key",
            payload={
                "id": 999999,
                "ok": 1,
                "hold_protocol": 0,
                "hold_id": minted,
                "cleanup_complete": 1,
                "end_state": "finished",
            },
            query={"inst": INST_CLIENT},
        )
        self.assertEqual(status, 200, body)
        self.assertIs(body.get("discarded"), True)
        self.assertIn("run-broker", fixture.state._hold_blocked_runs)
        status, refused = fixture.state.enqueue_command(
            "action_hold",
            {
                "action": "ActionDeployObject",
                "selector": "hands",
                "target": "hands",
                "classname": "FenceKit",
                "hold_timeout_s": 1.0,
            },
            peer="client",
            identity_payload=fixture.runtime.identity.to_payload(),
            lease_token=fixture.runtime._control.active_lease_token,
            operation_timeout_s=30.0,
        )
        self.assertEqual((status, refused.get("error")), (409, "hold_unreconciled"))


if __name__ == "__main__":
    unittest.main()
