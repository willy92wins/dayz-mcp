from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from dayz_mcp import control_client, dayz_test_tool, server, session_handoff
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.test_control_client import _clean_session_status, _policy


class ReloadLeaseRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.keyfile = self.root / "daemon.key"
        self.keyfile.write_text("test-key\n", encoding="utf-8")
        self.carrier = self.root / "handoff.json"
        self.identity = ClientIdentity(
            platform="codex", pid=123, ppid=45,
            started_at_utc="2026-09-25T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab", task_label="reload",
        )
        self.coordinator = SessionCoordinator(
            audit=lambda _: None, cleanup=lambda *_: {}, daemon_generation="test-generation",
        )
        code, self.lease = self.coordinator.acquire(self.identity, "before reload")
        self.assertEqual(code, 200)

    def replacement(self):
        session_handoff.write_handoff(
            self.carrier, identity=self.identity, lease_token=self.lease["lease_token"],
            lease_id=self.lease["lease_id"], generation=1,
        )
        policy = _policy(self.keyfile)
        provenance = SimpleNamespace(
            port=policy.port, argv=policy.argv, keyfile=policy.keyfile,
            cwd=policy.cwd, native_executable=policy.native_executable,
            launch_executable=policy.native_executable, auto_spawn_daemon=False,
        )
        with (
            patch.dict(os.environ, {session_handoff.HANDOFF_ENV: str(self.carrier)}),
            patch.object(server, "load_normal_daemon_policy", return_value=policy),
            patch.object(server.host_config, "resolve_daemon_provenance", return_value=provenance),
            patch.object(server.host_config, "_local_launch_executable", return_value=policy.native_executable),
            patch.object(server.host_config, "_local_native_executable", return_value=policy.native_executable),
        ):
            runtime = server.ClientRuntime(server.ServerConfig(
                mode="client", port=policy.port, keyfile=str(self.keyfile),
                auto_spawn_daemon=False, log_sink=lambda _: None,
            ))
        self.assertEqual(runtime.identity, self.identity)
        self.assertEqual(runtime.active_lease_token, self.lease["lease_token"])
        self.assertIsNone(runtime.active_operation_id)
        # A heartbeat may re-mirror the carried token before reconciliation.
        runtime._control._announce_lease()
        self.assertTrue(self.carrier.exists())
        return runtime

    async def remote(self, path, payload=None, **kwargs):
        payload = payload or {}
        if path == "/session/status":
            return {
                **self.coordinator.status(self.identity), "pending_commands": 0,
                "daemon_generation": "test-generation",
            }
        if path == "/session/acquire":
            code, result = self.coordinator.acquire(self.identity, **payload)
        elif path == "/session/enqueue":
            code, result = self.coordinator.enqueue(self.identity, **payload)
        elif path == "/session/wait":
            code, result = self.coordinator.wait(self.identity, payload["ticket"], payload["timeout_s"])
        else:
            self.fail(f"unexpected mutation during recovery: {path}")
        self.assertEqual(code, 202 if path == "/session/enqueue" else 200, result)
        return result

    def release_old(self):
        code, _ = self.coordinator.release(self.identity, self.lease["lease_token"])
        self.assertEqual(code, 200)

    async def test_replacement_reaches_run_launcher_gate_then_acquires_without_reconnect(self):
        runtime = self.replacement()
        self.release_old()
        class ReachedLauncher(Exception):
            pass
        remote = AsyncMock(side_effect=self.remote)
        with (
            patch.object(runtime._control, "_session_call", remote),
            patch.object(dayz_test_tool, "open_approved_launcher", side_effect=ReachedLauncher) as launcher,
        ):
            with self.assertRaises(ReachedLauncher):
                await dayz_test_tool.execute_dayz_test_run(runtime, project="Example", mode="all", preflight=True)
            launcher.assert_called_once_with("dayz-test-v1")
            self.assertEqual(runtime._control.state, "CLOSED")
            self.assertIsNone(runtime._control.active_lease_id)
            self.assertFalse(self.carrier.exists())
            self.assertEqual([call.args[0] for call in remote.await_args_list], ["/session/status"] * 2)
            acquired = await runtime.session_acquire_wait("after reload", max_wait_s=1)
        self.assertEqual(acquired["status"], "active")
        self.assertTrue(self.coordinator.authorize(self.identity, acquired["lease_token"], "world_spawn").allowed)
        self.assertNotEqual(acquired["lease_token"], self.lease["lease_token"])
        self.assertTrue(self.carrier.exists())

    async def test_direct_acquire_recovers_both_new_and_active_local_state(self):
        self.release_old()
        for state in ("NEW", "ACTIVE"):
            with self.subTest(state=state):
                runtime = self.replacement()
                runtime._control.state = state
                with patch.object(runtime._control, "_session_call", side_effect=self.remote):
                    acquired = await runtime.session_acquire("after reload")
                self.assertEqual(acquired["status"], "active")
                self.coordinator.release(self.identity, acquired["lease_token"])

    async def test_live_inherited_lease_is_preserved_and_still_authorized(self):
        runtime = self.replacement()
        with patch.object(runtime._control, "_session_call", side_effect=self.remote):
            with self.assertRaises(server.ToolError):
                await runtime.reconcile_idle_session()
        self.assertEqual(runtime.active_lease_token, self.lease["lease_token"])
        self.assertTrue(self.carrier.exists())
        self.assertTrue(self.coordinator.authorize(self.identity, runtime.active_lease_token, "world_spawn").allowed)

    async def test_foreign_owner_is_not_released_while_local_ghost_is_cleared(self):
        runtime = self.replacement()
        self.release_old()
        foreign = ClientIdentity(
            platform="codex", pid=124, ppid=45,
            started_at_utc="2026-09-25T00:00:00Z",
            session_id="22345678-1234-4234-8234-1234567890ab", task_label="other",
        )
        code, lease = self.coordinator.acquire(foreign, "other owner")
        self.assertEqual(code, 200)
        with patch.object(runtime._control, "_session_call", side_effect=self.remote):
            self.assertEqual(await runtime.reconcile_idle_session(), {"reconciled": True})
        self.assertFalse(self.carrier.exists())
        self.assertTrue(self.coordinator.authorize(foreign, lease["lease_token"], "world_spawn").allowed)

    async def test_unprovable_idle_never_clears_inherited_state_or_carrier(self):
        clean = _clean_session_status()
        bad = [
            {}, {**clean, "self": {"state": "active"}},
            {**clean, "self": {"state": "queued", "ticket": "t"}},
            {**clean, "pending_commands": 1}, {**clean, "pending_commands": False},
            {**clean, "audit_fault": {"fault_id": "x"}},
            {**clean, "lifecycle_recovery_fault": {"fault_id": "x"}},
            {**clean, "cleanup_degraded": ["release_failed"]},
            {**clean, "daemon_generation": ""},
            {**clean, "self": {"state": "none", "lease_id": "old"}},
        ]
        for key in ("audit_fault", "lifecycle_recovery_fault", "cleanup_degraded", "pending_commands"):
            incomplete = copy.deepcopy(clean)
            del incomplete[key]
            bad.append(incomplete)
        for status in bad:
            for final in (False, True):
                with self.subTest(status=status, final=final):
                    runtime = self.replacement()
                    replies = [clean, status] if final else [status]
                    call = AsyncMock(side_effect=replies)
                    with patch.object(runtime._control, "_session_call", call):
                        with self.assertRaises(control_client.ControlClientError):
                            await runtime._control.reconcile_idle_session()
                    self.assertEqual(runtime.active_lease_token, self.lease["lease_token"])
                    self.assertTrue(self.carrier.exists())
                    self.assertTrue(all(c.args[0] == "/session/status" for c in call.await_args_list))

    async def test_transport_failure_and_concurrent_state_change_preserve_local_authority(self):
        for kind in ("transport", "lease_id", "operation"):
            with self.subTest(kind=kind):
                runtime = self.replacement()
                listener = Mock()
                runtime._control.on_lease_change = listener
                calls = 0
                async def status(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        if kind == "transport":
                            raise control_client.ControlClientError(
                                "daemon_unavailable", request_stage="pre_request", http_bytes_sent=0,
                            )
                        if kind == "lease_id":
                            runtime._control.active_lease_id = "changed"
                        else:
                            runtime._control.active_operation_id = "changed"
                    return _clean_session_status()
                with patch.object(runtime._control, "_session_call", side_effect=status):
                    with self.assertRaises(control_client.ControlClientError):
                        await runtime._control.reconcile_idle_session()
                self.assertEqual(runtime.active_lease_token, self.lease["lease_token"])
                listener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
