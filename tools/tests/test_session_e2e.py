from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import control_client, daemon, loopback, server
from dayz_mcp.server import ServerConfig, ToolError
from _broker import e2e_daemon as broker_e2e
from _session_coordination import e2e_agent_sessions as binary_e2e
from tests.test_daemon import _free_port, _http
from tests.test_client_mode import _fixture_client_runtime
from tests.fence_helpers import INST_CLIENT, INST_SERVER, bind_both_peers


class IntegrationDaemon:
    """Real HTTP daemon wiring with an isolated durable runtime directory."""

    def __init__(self, runtime_root: str, key: str, port: int = 0) -> None:
        self.key = key
        self.runtime_root = runtime_root
        self.config = ServerConfig(
            mode="daemon", key=key, port=port, log_sink=lambda _message: None
        )
        with patch.dict(os.environ, {"LOCALAPPDATA": runtime_root}), patch.object(
            daemon.orphan_guard,
            "snapshot_retail_processes",
            return_value={"known": True, "processes": []},
        ), patch.object(daemon, "_ensure_identity_migration", return_value=None):
            self.state = daemon.build_server_state(
                self.config, key, activate_coordination=True
            )
        bind_both_peers(self.state)
        # P-I10: no owner is written here. Tests that dispatch acquire a
        # real lease and call adopt_run (see SessionE2ETest.adopt_run).
        self.httpd = loopback.create_http_server(
            port,
            self.state,
            log_sink=lambda _message: None,
            reclaim_orphans=False,
            status_provider=daemon.make_status_provider(self.config, self.state),
        )
        self.port = int(self.httpd.server_address[1])
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        try:
            self.httpd.serve_forever(poll_interval=0.01)
        except Exception:
            pass

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)


class GamePeer:
    """Fake DayZ peer that records only public command ids/names, never args."""

    def __init__(self, base: str, key: str, peer: str, *, respond: bool = True) -> None:
        self.base = base
        self.key = key
        self.peer = peer
        self.respond = respond
        self.commands_seen: list[dict[str, object]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=2.0)

    def command_names(self) -> list[str]:
        with self._lock:
            return [str(item["cmd"]) for item in self.commands_seen]

    def respond_all(self) -> None:
        with self._lock:
            pending = [item for item in self.commands_seen if not item["responded"]]
            for item in pending:
                item["responded"] = True
        for item in pending:
            _http(
                self.base,
                "POST",
                "/result",
                self.key,
                payload={"id": item["id"], "ok": 1, "cmd": item["cmd"]},
                query={
                    "inst": INST_SERVER if self.peer == "server" else INST_CLIENT
                },
            )

    def _poll_once(self) -> None:
        _status, body = _http(
            self.base,
            "GET",
            "/poll",
            self.key,
            query={
                "peer": self.peer,
                "inst": INST_SERVER if self.peer == "server" else INST_CLIENT,
            },
        )
        new_items: list[dict[str, object]] = []
        with self._lock:
            for command in body.get("commands", []):
                item = {
                    "id": int(command["id"]),
                    "cmd": str(command["cmd"]),
                    "responded": False,
                }
                self.commands_seen.append(item)
                new_items.append(item)
        if self.respond:
            inst = INST_SERVER if self.peer == "server" else INST_CLIENT
            for item in new_items:
                _http(
                    self.base,
                    "POST",
                    "/result",
                    self.key,
                    payload={"id": item["id"], "ok": 1, "cmd": item["cmd"]},
                    query={"inst": inst},
                )
                with self._lock:
                    item["responded"] = True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                pass
            time.sleep(0.01)


class SessionE2ETest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.runtime_root = TemporaryDirectory()
        self.key = "integration-key"
        self.daemon = IntegrationDaemon(self.runtime_root.name, self.key)
        self.daemon.start()
        self.peers: list[GamePeer] = []

    def tearDown(self) -> None:
        for peer in self.peers:
            peer.shutdown()
        self.daemon.stop()
        deadline = time.monotonic() + 2.0
        while True:
            try:
                self.runtime_root.cleanup()
                break
            except OSError as error:
                if error.winerror not in {32, 145} or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def client(self, platform: str) -> server.ClientRuntime:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key=self.key,
                port=self.daemon.port,
                client_platform=platform,
                log_sink=lambda _message: None,
            )
        )
        request = lambda method, path, payload=None, query=None, timeout=5.0: _http(
            self.daemon.base,
            method,
            path,
            self.key,
            payload=payload,
            query=query,
            timeout=timeout,
        )
        runtime._request_once = request

        def control_request(path, payload, timeout_s):
            status, response = request("POST", path, payload, None, timeout_s)
            if status not in (200, 202):
                raise control_client.ControlClientError(
                    server._remote_error_code(response),
                    request_stage="post_request",
                    http_bytes_sent=1,
                )
            return response

        runtime._control._request_once = control_request
        return runtime

    def peer(self, peer: str, *, respond: bool = True) -> GamePeer:
        game = GamePeer(self.daemon.base, self.key, peer, respond=respond)
        game.start()
        self.peers.append(game)
        return game

    async def adopt_run(
        self,
        runtime: server.ClientRuntime,
        lease_token: str,
        run_id: str = "test-run",
    ) -> dict:
        result = await runtime._control_with_lazy_spawn(
            runtime._control._session_call,
            "/lifecycle/adopt",
            {"lease_token": lease_token, "run_id": run_id},
        )
        self.assertEqual(result.get("ok"), True, result)
        self.assertIs(result.get("dispatchable"), True, result)
        return result

    async def wait_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition_not_reached")

    async def wait_pending_commands(
        self, runtime: server.ClientRuntime, expected: int, timeout: float = 2.0
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (await runtime.session_status()).get("pending_commands") == expected:
                return
            await asyncio.sleep(0.01)
        self.fail("pending_command_count_not_reached")

    async def test_parallel_reads_and_mutations_are_fifo_non_interleaved(self) -> None:
        runtime_a = self.client("codex")
        runtime_b = self.client("claude")
        server_peer = self.peer("server")
        self.peer("client")

        acquired_a = await runtime_a.session_acquire("A")
        await self.adopt_run(runtime_a, acquired_a["lease_token"])
        read_a, read_b = await asyncio.gather(
            runtime_a.call_bridge("query_player_state", {}, "server", 2.0),
            runtime_b.call_bridge("query_player_state", {}, "server", 2.0),
        )
        self.assertTrue(read_a["ok"])
        self.assertTrue(read_b["ok"])

        before_reject = list(server_peer.command_names())
        with self.assertRaisesRegex(ToolError, "lease_required"):
            await runtime_b.call_bridge("world_spawn", {}, "server", 2.0)
        await asyncio.sleep(0.05)
        self.assertEqual(server_peer.command_names(), before_reject)

        queued_b = await runtime_b.session_acquire("B")
        self.assertEqual((queued_b["status"], queued_b["position"]), ("queued", 1))
        await runtime_a.call_bridge("world_spawn", {}, "server", 2.0)
        await runtime_a.call_bridge("world_time_set", {}, "server", 2.0)
        await runtime_a.session_release(acquired_a["lease_token"])

        acquired_b = await runtime_b.session_wait(queued_b["ticket"], 1.0)
        self.assertEqual(acquired_b["status"], "active")
        await self.adopt_run(runtime_b, acquired_b["lease_token"])
        await runtime_b.call_bridge("world_weather_set", {}, "server", 2.0)
        await runtime_b.session_release(acquired_b["lease_token"])

        mutations = [
            name
            for name in server_peer.command_names()
            if name != "query_player_state"
        ]
        self.assertEqual(
            mutations, ["world_spawn", "world_time_set", "world_weather_set"]
        )

    async def test_abandoned_head_is_never_blind_granted_and_next_live_wait_claims(self) -> None:
        runtime_a = self.client("codex")
        runtime_b = self.client("claude")
        runtime_c = self.client("codex")

        acquired_a = await runtime_a.session_acquire("owner-a")
        queued_b = await runtime_b.session_acquire("abandoned-b")
        self.assertEqual((queued_b["status"], queued_b["position"]), ("queued", 1))

        waiting_c = asyncio.create_task(
            runtime_c.session_acquire_wait("live-c", 5.0)
        )
        await self.wait_until(
            lambda: len(self.daemon.state.coordination.status(runtime_a.identity)["queue"])
            == 2
        )

        await runtime_a.session_release(acquired_a["lease_token"])
        after_release = await runtime_a.session_status()
        self.assertIsNone(after_release["owner"])
        self.assertEqual(
            [item["client"]["task_label"] for item in after_release["queue"]],
            ["", ""],
        )
        self.assertFalse(waiting_c.done())

        cancelled_b = await runtime_b.session_cancel(queued_b["ticket"])
        self.assertTrue(cancelled_b["cancelled"])
        acquired_c = await asyncio.wait_for(waiting_c, timeout=3.0)
        self.assertEqual(acquired_c["status"], "active")
        self.assertEqual((await runtime_c.session_status())["self"]["state"], "active")
        await runtime_c.session_release(acquired_c["lease_token"])
        final = await runtime_c.session_status()
        self.assertIsNone(final["owner"])
        self.assertEqual(final["queue"], [])
        audit_path = Path(self.runtime_root.name) / "DayZ_MCP" / "audit" / "events.jsonl"
        events = [json.loads(line) for line in audit_path.read_text().splitlines()]
        fifo_grants = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        self.assertEqual(len(fifo_grants), 1)
        self.assertEqual(fifo_grants[0].get("source"), "live_wait")
        self.assertNotEqual(
            fifo_grants[0]["client"]["session"],
            runtime_b.identity.public_payload()["session"],
        )

    async def test_release_cancels_only_owner_queued_commands(self) -> None:
        runtime_a = self.client("codex")
        runtime_b = self.client("claude")
        acquired_a = await runtime_a.session_acquire("A")
        await self.adopt_run(runtime_a, acquired_a["lease_token"])

        owned_mutation = asyncio.create_task(
            runtime_a.call_bridge("world_spawn", {}, "server", 3.0)
        )
        foreign_read = asyncio.create_task(
            runtime_b.call_bridge("query_player_state", {}, "server", 3.0)
        )
        await self.wait_pending_commands(runtime_a, 1)

        released = await runtime_a.session_release(acquired_a["lease_token"])
        self.assertEqual(released["cleanup"]["cancelled"], 1)
        with self.assertRaisesRegex(ToolError, "owner_release"):
            await owned_mutation

        server_peer = self.peer("server")
        with self.assertRaisesRegex(ToolError, "run_not_owned"):
            await foreign_read
        self.assertEqual(server_peer.command_names(), [])

    async def test_delivered_command_stays_pending_and_is_never_replayed(self) -> None:
        runtime_a = self.client("codex")
        server_peer = self.peer("server", respond=False)
        acquired_a = await runtime_a.session_acquire("A")
        await self.adopt_run(runtime_a, acquired_a["lease_token"])
        delivered = asyncio.create_task(
            runtime_a.call_bridge("world_spawn", {}, "server", 5.0)
        )
        await self.wait_until(lambda: server_peer.command_names() == ["world_spawn"])

        released = await runtime_a.session_release(acquired_a["lease_token"])
        self.assertEqual(released["cleanup"]["cancelled"], 0)
        await asyncio.sleep(0.1)
        self.assertFalse(delivered.done())
        self.assertEqual(server_peer.command_names(), ["world_spawn"])

        delivered.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await delivered

    async def test_vehicle_release_cleanup_is_enqueued_exactly_once(self) -> None:
        runtime_a = self.client("codex")
        client_peer = self.peer("client")
        acquired_a = await runtime_a.session_acquire("drive")
        await self.adopt_run(runtime_a, acquired_a["lease_token"])
        await runtime_a.call_bridge(
            "vehicle_control", {"throttle": 1.0}, "client", 2.0
        )

        released = await runtime_a.session_release(acquired_a["lease_token"])
        self.assertEqual(released["cleanup"]["vehicle_release_enqueued"], 1)
        self.assertEqual(client_peer.command_names(), ["vehicle_control"])
        await asyncio.sleep(0.1)
        self.assertEqual(client_peer.command_names(), ["vehicle_control"])

    async def test_pin_is_capped_at_300_and_result_clears_it(self) -> None:
        runtime_a = self.client("codex")
        server_peer = self.peer("server", respond=False)
        acquired_a = await runtime_a.session_acquire("long mutation")
        await self.adopt_run(runtime_a, acquired_a["lease_token"])
        mutation = asyncio.create_task(
            runtime_a.call_bridge("world_spawn", {}, "server", 400.0)
        )
        await self.wait_until(lambda: server_peer.command_names() == ["world_spawn"])

        pinned = await runtime_a.session_status()
        self.assertGreater(pinned["owner"]["expires_in_s"], 299.0)
        self.assertLessEqual(pinned["owner"]["expires_in_s"], 300.0)

        server_peer.respond_all()
        self.assertTrue((await mutation)["ok"])
        unpinned = await runtime_a.session_status()
        self.assertGreater(unpinned["owner"]["expires_in_s"], 115.0)
        self.assertLessEqual(unpinned["owner"]["expires_in_s"], 120.0)
        await runtime_a.session_release(acquired_a["lease_token"])

    async def test_restart_invalidates_old_lease_and_ticket_without_secret_leak(self) -> None:
        runtime_a = self.client("codex")
        runtime_b = self.client("claude")
        acquired_a = await runtime_a.session_acquire("A")
        queued_b = await runtime_b.session_acquire("B")
        old_token = acquired_a["lease_token"]
        old_ticket = queued_b["ticket"]

        port = self.daemon.port
        self.daemon.stop()
        self.daemon = IntegrationDaemon(self.runtime_root.name, self.key, port)
        self.daemon.start()

        with self.assertRaisesRegex(ToolError, "lease_invalid"):
            await runtime_a.session_heartbeat(old_token)
        with self.assertRaisesRegex(ToolError, "ticket_invalid"):
            await runtime_b.session_wait(old_ticket, 0.0)

        status = await runtime_a.session_status()
        audit_path = Path(self.runtime_root.name) / "DayZ_MCP" / "audit" / "events.jsonl"
        audit_text = audit_path.read_text(encoding="utf-8")
        audit_events = [json.loads(line) for line in audit_text.splitlines()]
        self.assertIn(
            "daemon_restart_invalidated",
            [event.get("event") for event in audit_events],
        )
        wire = json.dumps({"status": status, "audit": audit_events})
        self.assertNotIn(old_token, wire)
        self.assertNotIn('"lease_token"', wire)
        self.assertNotIn('"pid"', wire)
        self.assertNotIn('"ppid"', wire)
        self.assertNotIn('"request_args"', wire)

    def test_broker_binary_harness_uses_client_runtime_and_natural_daemon_exit(self) -> None:
        harness = (_TOOLS_DIR / "_broker" / "e2e_daemon.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ClientRuntime", harness)
        self.assertIn('mode="client"', harness)
        self.assertNotIn('"/enqueue"', harness)
        self.assertNotIn("kill_pid", harness)
        self.assertNotIn('"--embedded"', harness)


class BinaryGateHelperTest(unittest.TestCase):
    def test_generation_gates_reject_none_empty_and_false_equality(self) -> None:
        self.assertTrue(binary_e2e._same_nonempty_generation("g", "g", "g"))
        for values in ((None, None), ("", ""), ("g", "g", None), ("g", "h")):
            with self.subTest(values=values):
                self.assertFalse(binary_e2e._same_nonempty_generation(*values))

        self.assertTrue(binary_e2e._changed_nonempty_generation("old", "new"))
        for old, new in ((None, "new"), ("old", None), ("", "new"), ("same", "same")):
            with self.subTest(old=old, new=new):
                self.assertFalse(binary_e2e._changed_nonempty_generation(old, new))

    def test_cleanup_and_overall_reject_crash_listener_or_live_worker(self) -> None:
        self.assertTrue(binary_e2e._cleanup_pass(0, True, True, True))
        for values in (
            (1, True, True, True),
            (0, False, True, True),
            (0, True, False, True),
            (0, True, True, False),
            (None, True, True, True),
        ):
            with self.subTest(values=values):
                self.assertFalse(binary_e2e._cleanup_pass(*values))

        gates = {name: True for name in binary_e2e.GATE_NAMES}
        self.assertTrue(binary_e2e._overall_pass(gates, cleanup_ok=True))
        self.assertFalse(binary_e2e._overall_pass(gates, cleanup_ok=False))
        gates["P7_no_secrets_and_clean_status"] = False
        self.assertFalse(binary_e2e._overall_pass(gates, cleanup_ok=True))

    def test_secret_scan_parses_json_jsonl_nested_fields_and_transient_values(self) -> None:
        documents = binary_e2e._parse_json_documents(
            [
                '{"safe":1}\n{"nested":{"lease_token":"redacted"}}\n',
                '{"evidence":{"value":"prefix-owner-secret-suffix"}}',
            ]
        )
        value_found, forbidden_found = binary_e2e._scan_documents(
            documents, ["owner-secret"]
        )
        self.assertTrue(value_found)
        self.assertTrue(forbidden_found)

        safe_documents = binary_e2e._parse_json_documents(
            ['{"lease_ids":["public-id"],"duration_s":120.0}']
        )
        self.assertEqual(
            binary_e2e._scan_documents(safe_documents, ["owner-secret"]),
            (False, False),
        )

    def test_p5_deadline_never_starts_a_wait_after_hard_timeout(self) -> None:
        self.assertEqual(binary_e2e._next_wait_timeout(0.0, 149.5), 0.5)
        self.assertEqual(binary_e2e._next_wait_timeout(0.0, 119.0), 30.0)
        self.assertIsNone(binary_e2e._next_wait_timeout(0.0, 150.0))
        self.assertIsNone(binary_e2e._next_wait_timeout(10.0, 160.1))
        self.assertTrue(binary_e2e._p5_deadline_pass(True, 150.0))
        self.assertFalse(binary_e2e._p5_deadline_pass(True, 150.0001))
        self.assertFalse(binary_e2e._p5_deadline_pass(False, 1.0))

    def test_broker_cleanup_requires_zero_exit_and_free_port(self) -> None:
        self.assertTrue(broker_e2e.clean_exit(0, True))
        self.assertFalse(broker_e2e.clean_exit(1, True))
        self.assertFalse(broker_e2e.clean_exit(None, True))
        self.assertFalse(broker_e2e.clean_exit(0, False))


if __name__ == "__main__":
    unittest.main()
