"""Lease handoff carriers and lease recovery across a server reload.

Moved verbatim from test_session_handoff.py, test_reload_lease_recovery.py
(review 2026-09-25, W4d step 3).
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import control_client, dayz_test_tool, server, session_handoff
from dayz_mcp.session_coordination import (
    ClientIdentity,
    SESSION_TTL_S,
    SessionCoordinator,
)
from dayz_mcp.session_handoff import (
    carrier_path,
    clear_handoff,
    consume_handoff,
    HANDOFF_ENV,
    HANDOFF_VERSION,
    MAX_HANDOFF_AGE_S,
    write_handoff,
)
from tests.test_control_client import _clean_session_status, _policy
from tests._tiers import slow_test


# --- helpers from test_session_handoff.py ---
def _identity(pid: int = 1000, session_id: str = "s" * 32) -> ClientIdentity:
    return ClientIdentity(
        platform="claude",
        pid=pid,
        ppid=pid - 100,
        started_at_utc="2026-09-10T12:00:00Z",
        session_id=session_id,
        task_label="handoff test",
    )


# --- from test_session_handoff.py ---


class HandoffRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-test-"))
        self.path = self.dir / "session-handoff.json"
        self.identity = _identity()

    def _write(self, **overrides: object) -> None:
        payload: dict[str, object] = {
            "identity": self.identity,
            "lease_token": "token-abc",
            "lease_id": "lease-1",
            "generation": 3,
        }
        payload.update(overrides)
        write_handoff(self.path, now=lambda: 1000.0, **payload)  # type: ignore[arg-type]

    def test_round_trip_carries_both_secrets_and_the_whole_identity(self) -> None:
        self._write()
        carried = consume_handoff(self.path, now=lambda: 1000.0)
        assert carried is not None
        self.assertEqual(carried.identity, self.identity)
        self.assertEqual(carried.lease_token, "token-abc")
        self.assertEqual(carried.lease_id, "lease-1")
        self.assertEqual(carried.generation, 3)

    def test_the_identity_that_comes_back_is_not_the_object_that_went_in(self) -> None:
        # Guards the tautology the rehearsal caught: equality has to survive the
        # serialisation, not just hold because it is the same Python object.
        self._write()
        carried = consume_handoff(self.path, now=lambda: 1000.0)
        assert carried is not None
        self.assertIsNot(carried.identity, self.identity)
        self.assertEqual(carried.identity, self.identity)

    def test_consuming_removes_the_carrier_so_it_cannot_be_replayed(self) -> None:
        self._write()
        self.assertTrue(self.path.exists())
        self.assertIsNotNone(consume_handoff(self.path, now=lambda: 1000.0))
        self.assertFalse(self.path.exists())
        self.assertIsNone(consume_handoff(self.path, now=lambda: 1000.0))

    def test_a_malformed_carrier_is_removed_too_not_retried_forever(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(consume_handoff(self.path))
        self.assertFalse(self.path.exists())

    def test_missing_carrier_is_not_an_error(self) -> None:
        self.assertIsNone(consume_handoff(self.dir / "absent.json"))

    def test_clear_removes_it_and_tolerates_absence(self) -> None:
        self._write()
        clear_handoff(self.path)
        self.assertFalse(self.path.exists())
        clear_handoff(self.path)

    def test_supervisor_identity_survives_clearing_the_carrier(self) -> None:
        identity_path = session_handoff.supervisor_identity_path(self.path)
        self.assertTrue(
            session_handoff.store_supervisor_identity_if_absent(
                identity_path, self.identity
            )
        )
        self.assertFalse(
            session_handoff.store_supervisor_identity_if_absent(
                identity_path, self.identity
            )
        )
        loaded = session_handoff.load_supervisor_identity(identity_path)
        self.assertIsNotNone(loaded)
        self.assertIsNot(loaded, self.identity)
        self.assertEqual(loaded, self.identity)
        self._write()
        clear_handoff(self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(
            session_handoff.load_supervisor_identity(identity_path), self.identity
        )
        session_handoff.clear_supervisor_identity(identity_path)
        self.assertIsNone(session_handoff.load_supervisor_identity(identity_path))
        session_handoff.clear_supervisor_identity(identity_path)

    def test_supervisor_identity_refuses_a_carrier_document(self) -> None:
        identity_path = session_handoff.supervisor_identity_path(self.path)
        self._write()
        identity_path.write_bytes(self.path.read_bytes())
        self.assertIsNone(session_handoff.load_supervisor_identity(identity_path))
        self.assertTrue(identity_path.is_file())

    def test_supervisor_identity_refuses_a_reused_pid(self) -> None:
        identity_path = session_handoff.supervisor_identity_path(self.path)
        self.assertTrue(
            session_handoff.store_supervisor_identity_if_absent(
                identity_path, self.identity
            )
        )
        stamp = session_handoff.supervisor_process_stamp()
        self.assertIsNotNone(stamp)
        document = json.loads(identity_path.read_text(encoding="utf-8"))
        assert stamp is not None
        self.assertEqual(document["supervisor_pid"], stamp[0])
        self.assertEqual(document["supervisor_creation_time_utc"], stamp[1])
        document["supervisor_creation_time_utc"] = "2000-01-01T00:00:00.000000Z"
        identity_path.write_text(json.dumps(document), encoding="utf-8")
        self.assertIsNone(session_handoff.load_supervisor_identity(identity_path))
        self.assertTrue(identity_path.is_file())
        self.assertEqual(
            json.loads(identity_path.read_text(encoding="utf-8"))["supervisor_pid"],
            stamp[0],
        )

    def test_supervisor_identity_refuses_a_copy_into_another_directory(self) -> None:
        identity_path = session_handoff.supervisor_identity_path(self.path)
        self.assertTrue(
            session_handoff.store_supervisor_identity_if_absent(
                identity_path, self.identity
            )
        )
        other = self.dir / "other-supervisor" / "supervisor-identity.json"
        other.parent.mkdir()
        other.write_bytes(identity_path.read_bytes())
        self.assertIsNone(session_handoff.load_supervisor_identity(other))
        self.assertEqual(other.read_bytes(), identity_path.read_bytes())


class HandoffRefusalTest(unittest.TestCase):
    """Every refusal returns None -- start leaseless -- and never raises."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-refuse-"))
        self.path = self.dir / "session-handoff.json"
        self.good = {
            "version": HANDOFF_VERSION,
            "identity": _identity().to_payload(),
            "lease_token": "token-abc",
            "lease_id": "lease-1",
            "generation": 3,
            "written_at": 1000.0,
        }

    def _put(self, document: object) -> None:
        self.path.write_text(json.dumps(document), encoding="utf-8")

    def _refused(self, **overrides: object) -> None:
        document = dict(self.good)
        document.update(overrides)
        self._put(document)
        self.assertIsNone(consume_handoff(self.path, now=lambda: 1000.0))

    def test_a_good_document_is_the_control_and_is_accepted(self) -> None:
        self._put(self.good)
        self.assertIsNotNone(consume_handoff(self.path, now=lambda: 1000.0))

    def test_wrong_version(self) -> None:
        self._refused(version=HANDOFF_VERSION + 1)

    def test_pid_that_arrived_as_text(self) -> None:
        # What an env var would hand over. from_payload rejects it; so do we.
        identity = _identity().to_payload()
        identity["pid"] = str(identity["pid"])
        self._refused(identity=identity)

    def test_identity_missing_a_field(self) -> None:
        identity = _identity().to_payload()
        del identity["ppid"]
        self._refused(identity=identity)

    def test_empty_or_absurd_secrets(self) -> None:
        self._refused(lease_token="")
        self._refused(lease_id="")
        self._refused(lease_token="x" * 5000)

    def test_generation_that_is_a_bool_or_negative(self) -> None:
        self._refused(generation=True)
        self._refused(generation=-1)

    def test_a_carrier_older_than_the_lease_it_names(self) -> None:
        self._put(self.good)
        stale = 1000.0 + MAX_HANDOFF_AGE_S + 1.0
        self.assertIsNone(consume_handoff(self.path, now=lambda: stale))

    def test_a_carrier_from_the_future_beyond_skew(self) -> None:
        self._put(self.good)
        self.assertIsNone(consume_handoff(self.path, now=lambda: 900.0))

    def test_a_carrier_right_at_the_edge_still_works(self) -> None:
        # The symmetric check: narrowing the window must not refuse a legitimate one.
        self._put(self.good)
        edge = 1000.0 + MAX_HANDOFF_AGE_S - 0.001
        self.assertIsNotNone(consume_handoff(self.path, now=lambda: edge))

    def test_a_document_that_is_not_an_object(self) -> None:
        self._put(["not", "a", "mapping"])
        self.assertIsNone(consume_handoff(self.path))


class HandoffWriterContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-write-"))
        self.path = self.dir / "session-handoff.json"

    def test_writer_refuses_a_caller_mistake_instead_of_persisting_it(self) -> None:
        with self.assertRaises(TypeError):
            write_handoff(
                self.path, identity="not-an-identity", lease_token="t",  # type: ignore[arg-type]
                lease_id="l", generation=0,
            )
        with self.assertRaises(ValueError):
            write_handoff(
                self.path, identity=_identity(), lease_token="", lease_id="l", generation=0
            )
        with self.assertRaises(ValueError):
            write_handoff(
                self.path, identity=_identity(), lease_token="t", lease_id="l",
                generation=True,  # type: ignore[arg-type]
            )
        self.assertFalse(self.path.exists())
        self.assertFalse((self.dir / "session-handoff.json.tmp").exists())

    def test_rewriting_replaces_rather_than_appends(self) -> None:
        write_handoff(
            self.path, identity=_identity(), lease_token="first", lease_id="l1", generation=1
        )
        write_handoff(
            self.path, identity=_identity(), lease_token="second", lease_id="l2", generation=2
        )
        carried = consume_handoff(self.path)
        assert carried is not None
        self.assertEqual(carried.lease_token, "second")
        self.assertFalse((self.dir / "session-handoff.json.tmp").exists())

    def test_the_token_is_not_in_the_env_var_name_or_the_path(self) -> None:
        # The rehearsal's rule: only the PATH travels. This box's process list is shared.
        path = carrier_path(self.dir)
        write_handoff(
            path, identity=_identity(), lease_token="s3cret-token", lease_id="l", generation=0
        )
        self.assertNotIn("s3cret-token", str(path))
        self.assertNotIn("s3cret-token", HANDOFF_ENV)

    def test_carrier_path_defaults_to_a_private_directory(self) -> None:
        path = carrier_path()
        self.assertTrue(path.parent.is_dir())
        self.assertEqual(path.name, "session-handoff.json")


class HandoffAgainstTheRealCoordinatorTest(unittest.TestCase):
    """The carrier is only worth anything if the coordinator accepts what comes out.

    This is the rehearsal's group C and F, now against the shipped carrier instead of a
    hand-built dict: acquire as one worker, hand over, and mutate as the next one.
    """

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-coord-"))
        self.path = self.dir / "session-handoff.json"
        self.clock = 0.0
        self.coordinator = SessionCoordinator(
            time_fn=lambda: self.clock,
            token_fn=lambda: "token-1",
            id_fn=lambda: "lease-1",
            audit=lambda _event: None,
            cleanup=lambda *_args: {},
        )

    def test_the_next_worker_keeps_the_lease_and_the_lease_id(self) -> None:
        old = _identity(pid=1000)
        status, active = self.coordinator.acquire(old, "before recycle")
        self.assertEqual(status, 200)
        self.assertTrue(self.coordinator.authorize(old, active["lease_token"], "world_spawn").allowed)

        write_handoff(
            self.path,
            identity=old,
            lease_token=active["lease_token"],
            lease_id=active["lease_id"],
            generation=1,
            now=lambda: 1000.0,
        )
        # The recycle itself: measured at 2.24 s by the spike, against a 120 s TTL.
        self.clock += 2.24
        carried = consume_handoff(self.path, now=lambda: 1002.24)
        assert carried is not None

        decision = self.coordinator.authorize(
            carried.identity, carried.lease_token, "world_spawn"
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.lease_id, active["lease_id"])
        self.assertEqual(self.coordinator.release(carried.identity, carried.lease_token)[0], 200)

    def test_a_worker_that_starts_without_the_carrier_cannot_touch_the_lease(self) -> None:
        old = _identity(pid=1000)
        _, active = self.coordinator.acquire(old, "before recycle")
        fresh = _identity(pid=2000, session_id="d" * 32)
        decision = self.coordinator.authorize(fresh, active["lease_token"], "world_spawn")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error, "lease_invalid")

    def test_an_overrun_recycle_does_not_get_the_box_back(self) -> None:
        # The rehearsal's E4, now end to end through the carrier: past the TTL the lease
        # is gone, and the carried token must not smuggle it back.
        old = _identity(pid=1000)
        _, active = self.coordinator.acquire(old, "before recycle")
        write_handoff(
            self.path, identity=old, lease_token=active["lease_token"],
            lease_id=active["lease_id"], generation=1, now=lambda: 1000.0,
        )
        self.clock += SESSION_TTL_S + 1.0
        carried = consume_handoff(self.path, now=lambda: 1000.5)
        assert carried is not None
        self.assertFalse(
            self.coordinator.authorize(carried.identity, carried.lease_token, "world_spawn").allowed
        )


# --- from test_reload_lease_recovery.py ---


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

    @slow_test
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


# --- fb-20260927-205523-1e06: the carrier follows heartbeats, and only them ---


class CarrierFollowsLeaseRenewalTests(unittest.IsolatedAsyncioTestCase):
    """A heartbeat the daemon accepted refreshes the carrier; nothing else does.

    The carrier used to be written only when the lease changed, so a recycle more
    than MAX_HANDOFF_AGE_S after the acquire lost a lease the session had kept
    alive by heartbeating: 236 s after the acquire on 2026-09-27. Review R1 of #119
    showed why accepted commands cannot refresh it: the daemon authorizes reads
    with no valid lease. One clock drives the coordinator and the worker, and
    another stamps and reads the carrier.
    """

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
            session_id="12345678-1234-4234-8234-1234567890ab", task_label="renewal",
        )
        self.clock = 0.0
        self.wall = 1000.0
        self.coordinator = SessionCoordinator(
            time_fn=lambda: self.clock,
            audit=lambda _: None, cleanup=lambda *_: {}, daemon_generation="test-generation",
        )
        code, self.lease = self.coordinator.acquire(self.identity, "before renewal")
        self.assertEqual(code, 200)
        self.token = self.lease["lease_token"]
        self.writes = 0
        real_write, real_consume = write_handoff, consume_handoff

        def stamped_write(*args, **kwargs):
            self.writes += 1
            return real_write(*args, **{**kwargs, "now": lambda: self.wall})

        def stamped_consume(path, **kwargs):
            return real_consume(path, **{**kwargs, "now": lambda: self.wall})

        for name, fake in (("write_handoff", stamped_write), ("consume_handoff", stamped_consume)):
            patcher = patch.object(session_handoff, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def worker(self):
        """A worker that inherited the lease through a carrier, as after a recycle."""
        session_handoff.write_handoff(
            self.carrier, identity=self.identity, lease_token=self.token,
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
            runtime = server.ClientRuntime(
                server.ServerConfig(
                    mode="client", port=policy.port, keyfile=str(self.keyfile),
                    auto_spawn_daemon=False, log_sink=lambda _: None,
                ),
                time_fn=lambda: self.clock,
            )
        self.assertEqual(runtime.active_lease_token, self.token)
        runtime._control._announce_lease()
        self.assertTrue(self.carrier.exists())
        return runtime

    def written_at(self) -> float:
        return json.loads(self.carrier.read_text(encoding="utf-8"))["written_at"]

    def advance(self, seconds: float) -> None:
        self.clock += seconds
        self.wall += seconds

    async def daemon(self, path, payload=None, **_kwargs):
        payload = payload or {}
        if path == "/session/heartbeat":
            code, result = self.coordinator.heartbeat(self.identity, payload["lease_token"])
        elif path == "/session/release":
            code, result = self.coordinator.release(self.identity, payload["lease_token"])
        else:
            self.fail(f"unexpected control call: {path}")
        if code != 200:
            raise control_client.ControlClientError(
                str(result.get("error")), request_stage="post_request", http_bytes_sent=1,
            )
        return result

    def enqueue_through_the_coordinator(self, method, path, payload, *_args):
        self.assertEqual((method, path), ("POST", "/enqueue"))
        decision = self.coordinator.authorize(
            self.identity, payload.get("lease_token"), payload["cmd"]
        )
        if not decision.allowed:
            return decision.http_status, {"error": decision.error}
        return 200, {"id": 7}

    async def send(self, runtime, command, args, peer):
        runtime._call = self.enqueue_through_the_coordinator
        with (
            patch.object(runtime, "_await_result", AsyncMock(return_value={"ok": 1})),
            patch.object(runtime, "bridge_status_payload", AsyncMock(side_effect=RuntimeError("no bridge"))),
            patch.object(server, "_with_bridge_success_hints", side_effect=lambda _r, _c, result: result),
        ):
            return await runtime.call_bridge(command, args, peer, 2.0)

    def recycle_keeps_the_lease(self) -> bool:
        carried = session_handoff.consume_handoff(self.carrier)
        if carried is None:
            return False
        return self.coordinator.authorize(
            carried.identity, carried.lease_token, "world_spawn"
        ).allowed

    async def test_a_heartbeat_carries_the_lease_past_the_acquire_ttl(self):
        runtime = self.worker()
        self.advance(100.0)
        with patch.object(runtime._control, "_session_call", side_effect=self.daemon):
            await runtime.session_heartbeat(self.token)
        self.assertEqual(self.written_at(), 1100.0)
        # 130 s after the acquire, 30 s after the heartbeat: the lease is alive, and
        # the replacement must get it.
        self.advance(30.0)
        self.assertTrue(self.recycle_keeps_the_lease())

    async def test_an_accepted_read_with_a_dead_token_does_not_reseal_the_carrier(self):
        # Review R1 of #119, F1: past the TTL the lease is gone, yet the daemon
        # still accepts a read that carries the dead token. That must not stamp
        # the token into a fresh carrier.
        runtime = self.worker()
        self.advance(SESSION_TTL_S + 10.0)
        before = self.writes
        await self.send(runtime, "camera_get", {}, "client")
        self.assertEqual(self.writes, before)
        self.assertFalse(self.recycle_keeps_the_lease())

    async def test_only_a_heartbeat_refreshes(self):
        # An accepted mutation renews the lease in the daemon, but its answer does
        # not say which lease it renewed, so it does not refresh the carrier.
        runtime = self.worker()
        self.advance(100.0)
        before = self.writes
        await self.send(
            runtime,
            "world_spawn",
            {"type": "X", "pos": [1, 2, 3], "flags": 0, "rotation": 0},
            "server",
        )
        runtime._refresh_lease_carrier("some-other-token")
        self.assertEqual(self.writes, before)

    async def test_without_a_heartbeat_the_old_carrier_still_expires(self):
        # Control: nothing renews the lease, so neither the carrier nor the lease
        # survives past the TTL.
        self.worker()
        self.advance(SESSION_TTL_S + 10.0)
        self.assertFalse(self.recycle_keeps_the_lease())

    async def test_heartbeat_refresh_writes_at_most_once_per_interval(self):
        runtime = self.worker()
        after_announce = self.writes
        with patch.object(runtime._control, "_session_call", side_effect=self.daemon):
            self.advance(1.0)
            await runtime.session_heartbeat(self.token)
            self.assertEqual(self.writes, after_announce)
            self.advance(server._CARRIER_REFRESH_S)
            await runtime.session_heartbeat(self.token)
            self.assertEqual(self.writes, after_announce + 1)

    async def test_a_release_clears_the_carrier(self):
        runtime = self.worker()
        with patch.object(runtime._control, "_session_call", side_effect=self.daemon):
            await runtime.session_release(self.token)
        self.assertFalse(self.carrier.exists())

    async def test_an_invalidated_lease_clears_the_carrier(self):
        runtime = self.worker()
        self.advance(SESSION_TTL_S + 10.0)
        with patch.object(runtime._control, "_session_call", side_effect=self.daemon):
            with self.assertRaises(Exception):
                await runtime.session_heartbeat(self.token)
        self.assertIsNone(runtime.active_lease_token)
        self.assertFalse(self.carrier.exists())

    async def test_a_release_racing_a_heartbeat_leaves_no_carrier(self):
        # The heartbeat succeeds in the daemon, then the lease goes away locally
        # before the refresh runs, as a concurrent release or invalidation would
        # do. The refresh re-reads the lease under the carrier lock and writes
        # nothing, so the clear stands.
        runtime = self.worker()
        self.advance(100.0)

        async def heartbeat_then_lose_the_lease(path, payload=None, **kwargs):
            result = await self.daemon(path, payload, **kwargs)
            runtime._control._clear_matching_lease(self.token)
            return result

        before = self.writes
        with patch.object(runtime._control, "_session_call", side_effect=heartbeat_then_lose_the_lease):
            await runtime.session_heartbeat(self.token)
        self.assertEqual(self.writes, before)
        self.assertFalse(self.carrier.exists())


if __name__ == "__main__":
    unittest.main()
