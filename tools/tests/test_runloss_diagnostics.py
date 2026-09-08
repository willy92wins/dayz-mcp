"""Run-loss errors through real lifecycle/queue/runtime, with no host processes."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import loopback, server
from tests import test_instance_fence as fixtures
from tests.test_client_mode import _fixture_client_runtime


class RunlossRetirementTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fx = fixtures.LifecycleFenceTest()
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.run_id, self.instance, self.record = self.fx._start_bound_client()
        self.runtime = _fixture_client_runtime(
            server.ServerConfig(mode="client", key="fixture", port=12345,
                                auto_spawn_daemon=False, log_sink=lambda _m: None)
        )

    def reap(self):
        # The real reaper observes process identity loss. A kill is a test failure.
        self.fx.snapshots[self.record.pid] = {"error": "process_not_found", "exit_code": 4}
        with patch.object(self.fx.lifecycle.guard, "terminate",
                          side_effect=AssertionError("reaper must not terminate")):
            self.assertEqual(self.fx.lifecycle.reap_dead_runs(), [self.run_id])
        self.assertEqual(self.fx.store.get(self.run_id).state, "EXITED")
        diag = self.fx.lifecycle.public_status()["retired_run_diagnostics"]
        self.assertEqual(diag[0]["reason"], "all_processes_gone_or_foreign")

    def refusal(self, peer="client"):
        cmd, args = ("camera_set", {"cam_mode": "orient"}) if peer == "client" else (
            "player_teleport", {"pos": [1.0, 2.0, 3.0]})
        status, payload = self.fx.state.enqueue_command(cmd, args, peer=peer)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "binding_retired")
        return server._public_enqueue_error(payload)

    async def queued_error(self):
        state = self.fx.state
        # _start_bound_client already left a camera_set queued before retirement.
        def transport(method, path, _payload=None, query=None, *_args):
            self.assertEqual((method, path), ("GET", "/await"))
            result = state.take_result(int(query["id"]), remove=True)
            self.assertIsNotNone(result)
            return 200, {"status": "done", "result": result}
        self.runtime._call = transport
        with self.assertRaises(server.ToolError) as caught:
            await self.runtime._await_result("camera_set", 1, "client", 1.0)
        return str(caught.exception)

    async def test_reaped_owned_run_explains_process_loss_to_next_caller(self):
        owner_before = self.fx.coordinator.snapshot_payload()
        self.reap()
        self.assertEqual(self.fx.coordinator.snapshot_payload()["active"], owner_before["active"])
        message = self.refusal()
        self.assertTrue(message.startswith("binding_retired:"), message)
        self.assertIn("all_processes_gone_or_foreign", message)
        self.assertIn("no live owned process", message)
        self.assertIn("dayz_test_run", message)

    async def test_reaped_pending_command_delivers_the_same_cause_via_await(self):
        self.reap()
        message = await self.queued_error()
        self.assertIn("all_processes_gone_or_foreign", message)
        self.assertIn("no live owned process", message)

    async def test_role_replacement_is_not_reported_as_run_death(self):
        self.fx.state.retire_role(self.run_id, "client", "replace-role")
        self.assertEqual(self.fx.store.get(self.run_id).state, "RUNNING")
        message = self.refusal()
        self.assertIn("replace-role", message)
        self.assertIn("replacement instance", message)
        self.assertNotIn("no live owned process", message)

    async def test_failed_replacement_does_not_promise_a_successor(self):
        minted, error = self.fx.lifecycle._prepare_instance(
            self.run_id, "client", str(self.fx.root / "missing-profile"), True)
        self.assertIsNone(minted)
        self.assertEqual(error, "instance_config_missing")
        message = self.refusal()
        self.assertIn("replace-role", message)
        self.assertIn("lifecycle_status for a launch failure", message)

    async def test_pending_replacement_command_keeps_the_replacement_cause(self):
        self.fx.state.retire_role(self.run_id, "client", "replace-role")
        self.assertIn("replace-role", await self.queued_error())

    async def test_stopped_is_distinct_from_reaped(self):
        self.fx.state.retire_run(self.run_id, "stopped")
        message = self.refusal()
        self.assertIn("retirement_reason=stopped", message)
        self.assertNotIn("all_processes_gone_or_foreign", message)

    async def test_unknown_reason_does_not_invent_death_or_echo_host_text(self):
        self.fx.state.retire_run(self.run_id, "C:/private/failure.txt")
        message = self.refusal()
        self.assertIn("cause is unavailable", message)
        self.assertIn("lifecycle_status", message)
        self.assertNotIn("private", message)
        self.assertNotIn("no live owned process", message)

    async def test_latest_other_peer_retirement_does_not_overwrite_this_cause(self):
        self.reap()
        self.fx.state.install_bound_peer(instance=fixtures.INST_S1, role="server", pid=777, run_id="another-run")
        self.fx.state.retire_role("another-run", "server", "replace-role")
        self.assertIn("all_processes_gone_or_foreign", self.refusal("client"))
        self.assertIn("replace-role", self.refusal("server"))

    async def test_new_bound_target_does_not_inherit_older_dead_run_cause(self):
        self.reap()
        self.fx.state.install_bound_peer(instance=fixtures.INST_C2, role="client", pid=888, run_id="new-run")
        self.fx.state.lifecycle = SimpleNamespace(
            manifest=SimpleNamespace(get=lambda _run: SimpleNamespace(state="EXITED")))
        # No diagnostic for this new terminal row: do not reuse the old reaper.
        message = self.refusal()
        self.assertNotIn("all_processes_gone_or_foreign", message)
        self.assertNotIn("no live owned process", message)

    async def test_eviction_falls_back_without_borrowing_another_role_cause(self):
        self.reap()
        with patch.object(loopback, "RETIRED_INSTANCE_LIMIT", 1):
            self.fx.state.install_bound_peer(instance=fixtures.INST_S1, role="server", pid=777, run_id="another-run")
            self.fx.state.retire_role("another-run", "server", "replace-role")
        self.assertEqual(len(self.fx.state._retired_instances), 1)
        message = self.refusal("client")
        self.assertIn("cause is unavailable", message)
        self.assertNotIn("replace-role", message)
        self.assertNotIn("all_processes_gone_or_foreign", message)

    async def test_offline_retirement_explains_both_peers(self):
        state = loopback.ServerState("fixture")
        state.install_bound_peer(instance=fixtures.INST_OFF, role="offline", pid=900)
        state.retire_run("testrun", "reaped")
        for cmd, peer, args in (("camera_set", "client", {"cam_mode": "orient"}),
                                ("player_teleport", "server", {"pos": [1.0, 2.0, 3.0]})):
            status, payload = state.enqueue_command(cmd, args, peer=peer)
            self.assertEqual(status, 409)
            self.assertIn("all_processes_gone_or_foreign", server._public_enqueue_error(payload))


class RunlossWaitBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def probe_timeout(self, timeout_s):
        clock = SimpleNamespace(now=1000.0)
        monotonic = lambda: clock.now
        runtime = _fixture_client_runtime(
            server.ServerConfig(mode="client", key="fixture", port=12345,
                                auto_spawn_daemon=False, log_sink=lambda _m: None),
            time_fn=monotonic,
        )
        paths = []
        def transport(method, path, _payload=None, _query=None, *args):
            paths.append(path)
            if path == "/enqueue":
                return 200, {"id": 7}
            if path == "/await":
                clock.now += args[0]  # Real per-probe remaining budget.
                return 200, {"status": "pending"}
            if path == "/status":
                return 200, {"server_peer": {
                    "binding_state": "BOUND", "instance_prefix": None,
                    "last_poll_age_s": 140.4, "queue_depth": 1, "version_state": "ok"}}
            raise AssertionError(path)
        runtime._call = transport
        with patch.object(server, "time", SimpleNamespace(monotonic=monotonic)):
            with self.assertRaises(server.ToolError) as caught:
                await server.execute_wait_for(runtime, "players_at_least", value=1,
                                              timeout_s=timeout_s, poll_interval_s=6)
        self.assertEqual(paths.count("/enqueue"), 1)
        return str(caught.exception), clock.now - 1000.0

    async def test_fifteen_second_probe_does_not_claim_420_second_deadline(self):
        message, elapsed = await self.probe_timeout(420)
        self.assertEqual(elapsed, 15.0)
        self.assertIn("wait_for aborted", message)
        self.assertNotIn("wait_for timed out", message)
        self.assertIn("reason=probe_timeout", message)
        self.assertIn("elapsed_s=15.000", message)
        self.assertIn("timeout_s=420", message)
        self.assertIn("probe_timeout_s=15", message)

    async def test_probe_that_exhausts_global_budget_still_reports_timeout(self):
        message, elapsed = await self.probe_timeout(10)
        self.assertEqual(elapsed, 10.0)
        self.assertIn("wait_for timed out", message)
        self.assertIn("elapsed_s=10.000", message)
        self.assertIn("timeout_s=10", message)

    async def test_global_poll_age_is_labelled_as_station_snapshot(self):
        message, _elapsed = await self.probe_timeout(420)
        self.assertIn("station snapshot:", message)
        self.assertIn("last poll 140.4s ago", message)

    async def test_retryable_not_ready_still_retries_and_unknown_reason_aborts(self):
        runtime = SimpleNamespace(tool_lock=asyncio.Lock())
        replies = ["game_not_ready:reason=server_poll_stale", {"ok": 1, "players": [{}]}]
        async def call_bridge(*_args):
            result = replies.pop(0)
            if isinstance(result, str):
                raise server.ToolError(result)
            return result
        runtime.call_bridge = call_bridge
        result = await server.execute_wait_for(runtime, "players_at_least", value=1,
                                              timeout_s=2, poll_interval_s=0.5)
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["not_ready_probes"], 1)
        replies[:] = ["game_not_ready:reason=future_open_reason", {"ok": 1, "players": [{}]}]
        with self.assertRaisesRegex(server.ToolError, "future_open_reason"):
            await server.execute_wait_for(runtime, "players_at_least", value=1,
                                          timeout_s=2, poll_interval_s=0.5)
        self.assertEqual(len(replies), 1)


if __name__ == "__main__":
    unittest.main()
