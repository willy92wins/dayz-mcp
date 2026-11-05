"""Production wiring of the lease-end close (§2.12-§2.14).

Stage 2a/2b drove the coordinator and broker with note_peer_bound called
by hand. Here nothing registers a peer manually: bindings become known
through confirm / install_bound_peer / the coordination setter, restart
debt is seeded from a manifest listing, and only the lifecycle clears it.
Criteria: K5, K13, K14 and the cleanup half of §2.14.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon, loopback, process_lifecycle
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.fence_helpers import (
    INST_CLIENT,
    INST_SERVER,
    PID_CLIENT,
    PID_SERVER,
    poll_census_query,
)
from tests.lease_helpers import AuditSink, FakeClock, SequentialIds, _identity

_CLOSE_NAMES = {"server": "lease_close", "client": "lease_close_client"}


def _identity_payload(client: ClientIdentity) -> dict[str, object]:
    return {
        "platform": client.platform,
        "pid": client.pid,
        "ppid": client.ppid,
        "started_at_utc": client.started_at_utc,
        "session_id": client.session_id,
        "task_label": client.task_label,
    }


def _floor(net_low, net_high, *, conn_gen, move_id, weapon_id):
    return {
        "net_low": net_low,
        "net_high": net_high,
        "conn_gen": conn_gen,
        "move_id": move_id,
        "weapon_id": weapon_id,
    }


class _WiringBase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.broker_clock = FakeClock()
        self.ids = SequentialIds("lease")
        self.tokens = SequentialIds("token")
        self.audit = AuditSink()
        self.a = _identity("a")
        self.b = _identity("b")
        self.instances = {"server": INST_SERVER, "client": INST_CLIENT}
        self.pids = {"server": PID_SERVER, "client": PID_CLIENT}
        self.poll_creation: dict[str, str] = {}

    def _coordinator(self) -> SessionCoordinator:
        return SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            cleanup=lambda *args: self.state.cleanup_owner(*args),
        )

    def _poll(self, peer: str) -> list[dict]:
        query = poll_census_query(peer)
        names = [n for n in query["caps"].split(",") if n]
        if _CLOSE_NAMES[peer] not in names:
            names.append(_CLOSE_NAMES[peer])
        query["caps"] = ",".join(names)
        status, body = self.state.record_poll(
            peer,
            f"{EXPECTED_BRIDGE_VERSION}~1.29",
            instance=self.instances[peer],
            source_pid=self.pids[peer],
            source_creation_time=self.poll_creation.get(peer),
            caps=query["caps"],
            ach=query.get("ach"),
        )
        self.assertEqual(status, 200, body)
        return list(body.get("commands") or [])

    @staticmethod
    def _cmds(commands: list[dict]) -> list[str]:
        return [str(c.get("cmd")) for c in commands]

    def _acquire(self, client: ClientIdentity) -> dict:
        status, lease = self.coordinator.acquire(client, "trace")
        self.assertEqual(status, 200, lease)
        return lease

    def _deliver_work(self, client: ClientIdentity, lease: dict) -> int:
        status, body = self.state.enqueue_command(
            "object_delete",
            {"object_id": 1},
            peer="server",
            identity_payload=_identity_payload(client),
            lease_token=lease["lease_token"],
            operation_timeout_s=30.0,
        )
        self.assertEqual(status, 200, body)
        command_id = int(body["id"])
        self.assertEqual([c["id"] for c in self._poll("server")], [command_id])
        return command_id

    def _release(self, client: ClientIdentity, lease: dict) -> None:
        status, body = self.coordinator.release(client, lease["lease_token"])
        self.assertEqual(status, 200, body)
        with self.coordinator._condition:
            self.assertTrue(
                self.coordinator._condition.wait_for(
                    lambda: not self.coordinator._handoff_pending, timeout=30.0
                ),
                "handoff_pending did not clear",
            )

    def _deliver_close(self, peer: str) -> dict:
        commands = self._poll(peer)
        self.assertEqual(self._cmds(commands), [_CLOSE_NAMES[peer]], commands)
        return commands[0]

    def _census(self, peer: str, close: dict) -> dict[str, object]:
        census: dict[str, object] = {
            "generation": close["args"]["close_generation"],
            "lease_id": close["args"]["close_lease_id"],
            "peer": peer,
            "state": "clean",
        }
        if peer == "client":
            census.update(
                entries=[{"name": "action", "state": "clean"}],
                move_floor=7,
                weapon_floor=4,
                net_low=10,
                net_high=20,
                conn_gen=1,
            )
        else:
            census.update(
                entries=[{"name": "bots", "state": "clean"}],
                floors=[_floor(10, 20, conn_gen=1, move_id=7, weapon_id=4)],
            )
        return census

    def _settle(self, peer: str, close: dict) -> None:
        status, body = self.state.store_result(
            {"id": close["id"], "ok": True, "lease_close": self._census(peer, close)},
            instance=self.instances[peer],
        )
        self.assertEqual(status, 200, body)
        self.assertNotIn("discarded", body)

    def _photo(self) -> dict:
        return self.coordinator.status(self.a).get("lease_end", {})


class LeaseCloseWiringTest(_WiringBase):
    """K5/K13/K14 wiring: peers known without a manual note_peer_bound."""

    def _build_attached_first(self, *, offline: bool = False) -> None:
        self.state = loopback.ServerState(
            "test-key", time_fn=self.broker_clock
        )
        self.coordinator = self._coordinator()
        self.state.coordination = self.coordinator
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        if offline:
            self.state.install_bound_peer(
                instance=INST_SERVER, role="offline", pid=PID_SERVER, run_id="test-run"
            )
            self.instances = {"server": INST_SERVER, "client": INST_SERVER}
            self.pids = {"server": PID_SERVER, "client": PID_SERVER}
        else:
            self.state.install_bound_peer(
                instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="test-run"
            )
            self.state.install_bound_peer(
                instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id="test-run"
            )
        for peer in ("server", "client"):
            self.assertEqual(self._poll(peer), [])

    def test_install_bound_peer_registers_peers_and_close_settles(self) -> None:
        self._build_attached_first()
        self.assertEqual(
            self.coordinator._peer_registry,
            {
                ("test-run", "server"): INST_SERVER,
                ("test-run", "client"): INST_CLIENT,
            },
        )
        lease = self._acquire(self.a)
        self._deliver_work(self.a, lease)
        self._release(self.a, lease)
        status, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 202, queued)
        # K5: the client close is published first, then the server close.
        client_close = self._deliver_close("client")
        self._settle("client", client_close)
        server_close = self._deliver_close("server")
        self._settle("server", server_close)
        status, grant = self.coordinator.wait(self.b, queued["ticket"], 30.0)
        self.assertEqual(status, 200, grant)
        settled = {str(i["close_id"]) for i in grant["lease_end"]["settled"]}
        self.assertEqual(settled, {str(client_close["id"]), str(server_close["id"])})
        self.assertNotIn("lease_end_unconfirmed", grant.get("cleanup_degraded", []))

    def test_offline_binding_registers_both_peers(self) -> None:
        self._build_attached_first(offline=True)
        self.assertEqual(
            self.coordinator._peer_registry,
            {
                ("test-run", "server"): INST_SERVER,
                ("test-run", "client"): INST_SERVER,
            },
        )

    def test_setter_replays_bindings_bound_before_attach(self) -> None:
        # The other construction order: bindings BOUND first, coordinator
        # attached afterwards (test_action_hold.py:418 shape).
        self.state = loopback.ServerState("test-key", time_fn=self.broker_clock)
        self.state.install_bound_peer(
            instance=INST_SERVER, role="offline", pid=PID_SERVER, run_id="test-run"
        )
        self.instances = {"server": INST_SERVER, "client": INST_SERVER}
        self.pids = {"server": PID_SERVER, "client": PID_SERVER}
        self.coordinator = self._coordinator()
        self.state.coordination = self.coordinator
        self.assertEqual(
            self.coordinator._peer_registry,
            {
                ("test-run", "server"): INST_SERVER,
                ("test-run", "client"): INST_SERVER,
            },
        )

    def test_confirm_registers_bound_peer(self) -> None:
        self.state = loopback.ServerState("test-key", time_fn=self.broker_clock)
        self.coordinator = self._coordinator()
        self.state.coordination = self.coordinator
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        record = SimpleNamespace(
            pid=PID_SERVER,
            creation_time_utc="2026-08-18T00:00:00.000000Z",
            role="server",
        )
        # A STARTING binding is what prepare() leaves; confirm() bounds it.
        from dayz_mcp.instance_fence import BINDING_STARTING, Binding

        self.state._bindings[INST_SERVER] = Binding(
            instance=INST_SERVER,
            run_id="test-run",
            role="server",
            epoch=1,
            pid=None,
            creation_time_utc=None,
            state=BINDING_STARTING,
        )
        self.state._role_index[("test-run", "server")] = INST_SERVER
        self.state._bound_queues.setdefault(INST_SERVER, [])
        self.poll_creation["server"] = "2026-08-18T00:00:00.000000Z"
        self.state.confirm(INST_SERVER, record)
        self.assertEqual(
            self.coordinator._peer_registry,
            {("test-run", "server"): INST_SERVER},
        )
        # End to end from the confirm-bound peer: lease with work, release,
        # client close first, then the server close, clean census, settled.
        self.state.install_bound_peer(
            instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id="test-run"
        )
        for peer in ("server", "client"):
            self.assertEqual(self._poll(peer), [])
        lease = self._acquire(self.a)
        self._deliver_work(self.a, lease)
        self._release(self.a, lease)
        status, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 202, queued)
        client_close = self._deliver_close("client")
        self._settle("client", client_close)
        server_close = self._deliver_close("server")
        self._settle("server", server_close)
        status, grant = self.coordinator.wait(self.b, queued["ticket"], 30.0)
        self.assertEqual(status, 200, grant)
        settled = {str(i["close_id"]) for i in grant["lease_end"]["settled"]}
        self.assertEqual(settled, {str(client_close["id"]), str(server_close["id"])})
        self.assertNotIn("lease_end_unconfirmed", grant.get("cleanup_degraded", []))

    def test_restart_debt_from_listing_blocks_until_processes_gone(self) -> None:
        # K13: seeded before attach, from a startup listing of live runs.
        self.state = loopback.ServerState("test-key", time_fn=self.broker_clock)
        self.coordinator = self._coordinator()
        run = SimpleNamespace(
            run_id="test-run",
            state="RUNNING",
            processes=[
                SimpleNamespace(role="server"),
                SimpleNamespace(role="client"),
            ],
        )
        done = SimpleNamespace(run_id="old-run", state="EXITED", processes=[])
        lifecycle = SimpleNamespace(
            manifest=SimpleNamespace(list_runs=lambda: [run, done])
        )
        loopback._seed_restart_debt_from_lifecycle(self.coordinator, lifecycle)
        self.assertIsNone(self.state.coordination)
        self.state.coordination = self.coordinator
        photo = self._photo()
        self.assertEqual(
            {row["peer"]: list(row["reasons"]) for row in photo["unconfirmed"]},
            {"server": ["daemon_restart"], "client": ["daemon_restart"]},
        )
        lease = self._acquire(self.a)
        self.assertIn("lease_end_unconfirmed", lease.get("cleanup_degraded", []))
        self._release(self.a, lease)
        self.coordinator.note_processes_gone("test-run", None)
        self.assertNotIn("unconfirmed", self._photo())
        lease = self._acquire(self.a)
        self.assertNotIn("lease_end_unconfirmed", lease.get("cleanup_degraded", []))

    def test_commit_retirement_clears_debt_only_after_persist(self) -> None:
        # K14: a failed manifest write calls nothing; a landed one clears.
        self.coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            cleanup=lambda *_args: {},
        )
        self.coordinator.seed_restart_debt([("test-run", None)])
        lifecycle = object.__new__(process_lifecycle.ProcessLifecycle)
        lifecycle.coordinator = self.coordinator
        # M5b: go through the real retire_run, so a note_processes_gone
        # smuggled into it (clearing debt before the persist) is caught
        # by the failing-write half below.
        retire_state = loopback.ServerState("test-key", time_fn=self.broker_clock)
        retire_state.coordination = self.coordinator
        lifecycle._retire_run_bindings = (
            lambda run_id, reason: retire_state.retire_run(run_id, reason)
        )
        lifecycle._release_parent_anchors_locked = lambda: None
        lifecycle._best_effort_post_persist_retirement = (
            lambda *_args: None
        )
        run = SimpleNamespace(run_id="test-run")

        def _failing_replace(_run):
            raise OSError("disk")

        lifecycle.manifest = SimpleNamespace(replace=_failing_replace)
        self.assertFalse(
            lifecycle._commit_retirement(run, "stop", "test", "decision")
        )
        photo = self.coordinator.status(self.a).get("lease_end", {})
        self.assertEqual(len(photo.get("unconfirmed", [])), 2)

        lifecycle.manifest = SimpleNamespace(replace=lambda _run: None)
        self.assertTrue(
            lifecycle._commit_retirement(run, "stop", "test", "decision")
        )
        photo = self.coordinator.status(self.a).get("lease_end", {})
        self.assertNotIn("unconfirmed", photo)

    def test_repair_paths_seed_kept_live_runs_before_lifting(self) -> None:
        # K13 (H4): both _repair_lifecycle_recovery_fault exits seed first.
        for repaired_first in (True, False):
            with self.subTest(repaired_first=repaired_first):
                clock = FakeClock()
                coordinator = SessionCoordinator(
                    time_fn=clock,
                    token_fn=SequentialIds("token"),
                    id_fn=SequentialIds("lease"),
                    audit=AuditSink(),
                    cleanup=lambda *_args: {},
                )
                fault = {
                    "fault": {"fault_id": "fault-1"},
                    "event": {"state": "repaired" if repaired_first else "armed"},
                    "pointer": {"head_event_sha256": "head-1"},
                }
                coordinator.arm_lifecycle_recovery_fault(fault)
                live = SimpleNamespace(
                    run_id="kept-run",
                    state="RUNNING",
                    processes=[
                        SimpleNamespace(role="server"),
                        SimpleNamespace(role="crash_reporter"),
                    ],
                )
                seen: list[bool] = []

                def _list_runs():
                    # M7: seeding must happen while the fault is still
                    # armed, i.e. before complete/clear lifts it.
                    seen.append(
                        coordinator._lifecycle_recovery_fault is not None
                    )
                    return [live]

                lifecycle = SimpleNamespace(
                    manifest=SimpleNamespace(list_runs=_list_runs),
                    repair_recovery_fault=lambda _fault: {
                        "terminal_safe": True,
                        "manifest_sha256": "sha-1",
                    },
                )
                store = SimpleNamespace(
                    load_active=lambda: fault,
                    transition=lambda *a, **k: "head-2",
                    create_receipt=lambda *a, **k: "receipt-1",
                )
                state = SimpleNamespace(
                    coordination=coordinator,
                    lifecycle_recovery_fault_store=store,
                    lifecycle=lifecycle,
                )
                status, body = loopback._repair_lifecycle_recovery_fault(
                    state, "fault-1", "head-1"
                )
                self.assertEqual((status, body.get("repaired")), (200, True))
                self.assertEqual(seen, [True])
                photo = coordinator.status(self.a).get("lease_end", {})
                self.assertEqual(
                    [
                        (r["peer"], list(r["reasons"]))
                        for r in photo.get("unconfirmed", [])
                    ],
                    [("server", ["daemon_restart"])],
                )

    def test_repair_seed_failure_returns_409_and_keeps_fault(self) -> None:
        # H1 fail-closed: a seeding failure must not lift the fault.
        for repaired_first in (True, False):
            with self.subTest(repaired_first=repaired_first):
                clock = FakeClock()
                coordinator = SessionCoordinator(
                    time_fn=clock,
                    token_fn=SequentialIds("token"),
                    id_fn=SequentialIds("lease"),
                    audit=AuditSink(),
                    cleanup=lambda *_args: {},
                )
                fault = {
                    "fault": {"fault_id": "fault-1"},
                    "event": {"state": "repaired" if repaired_first else "armed"},
                    "pointer": {"head_event_sha256": "head-1"},
                }
                coordinator.arm_lifecycle_recovery_fault(fault)

                def _boom():
                    raise OSError("disk")

                lifecycle = SimpleNamespace(
                    manifest=SimpleNamespace(list_runs=_boom),
                    repair_recovery_fault=lambda _fault: {
                        "terminal_safe": True,
                        "manifest_sha256": "sha-1",
                    },
                )
                store = SimpleNamespace(
                    load_active=lambda: fault,
                    transition=lambda *a, **k: "head-2",
                    create_receipt=lambda *a, **k: "receipt-1",
                )
                state = SimpleNamespace(
                    coordination=coordinator,
                    lifecycle_recovery_fault_store=store,
                    lifecycle=lifecycle,
                )
                status, body = loopback._repair_lifecycle_recovery_fault(
                    state, "fault-1", "head-1"
                )
                self.assertEqual(status, 409)
                self.assertEqual(body.get("error"), "lifecycle_recovery_seed_failed")
                self.assertIsNotNone(coordinator._lifecycle_recovery_fault)


class LeaseCloseCleanupWiringTest(_WiringBase):
    """§2.14: with a round open, no per-lease release commands are queued."""

    def setUp(self) -> None:
        super().setUp()
        self.state = loopback.ServerState("test-key", time_fn=self.broker_clock)
        self.coordinator = self._coordinator()
        self.state.coordination = self.coordinator
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        self.state.install_bound_peer(
            instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="test-run"
        )
        self.state.install_bound_peer(
            instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id="test-run"
        )
        for peer in ("server", "client"):
            self.assertEqual(self._poll(peer), [])

    def test_round_publishes_only_closes_no_vehicle_bot_or_cancel(self) -> None:
        lease = self._acquire(self.a)
        self._deliver_work(self.a, lease)
        # A delivered bot belongs to this lease; its stop is the close's job.
        status, bot = self.state.enqueue_command(
            "bot_start",
            {"object_id": 9, "action": "PLAYER_BOT_RANDOMIZE_MOVEMENT", "bot_ttl_s": 30.0},
            peer="server",
            identity_payload=_identity_payload(self.a),
            lease_token=lease["lease_token"],
            operation_timeout_s=30.0,
        )
        self.assertEqual(status, 200, bot)
        self.assertEqual(
            [c["id"] for c in self._poll("server")], [int(bot["id"])]
        )
        self._release(self.a, lease)
        # H5: with a round, _bot_by_lease must not leak the lease entry.
        self.assertEqual(self.state._bot_by_lease, {})
        client_close = self._deliver_close("client")
        self._settle("client", client_close)
        server_close = self._deliver_close("server")
        self._settle("server", server_close)
        for peer in ("server", "client"):
            queue = self.state._bound_queues[self.instances[peer]]
            names = self._cmds(queue)
            self.assertNotIn("bot_stop", names)
            self.assertNotIn("vehicle_release", names)
            self.assertNotIn("action_hold_cancel", names)


class DaemonRestartWiringTest(unittest.TestCase):
    """K13 at the daemon seam: a live run survives in the manifest listing."""

    def test_restart_debt_pairs_filters_non_peer_roles_and_exited(self) -> None:
        # M8/M10 at the helper: EXITED seeds nothing, roles are kept as-is,
        # and non-peer roles (crash_reporter) are dropped.
        live = SimpleNamespace(
            run_id="live",
            state="RUNNING",
            processes=[
                SimpleNamespace(role="server"),
                SimpleNamespace(role="crash_reporter"),
                SimpleNamespace(role="offline"),
            ],
        )
        done = SimpleNamespace(
            run_id="done",
            state="EXITED",
            processes=[SimpleNamespace(role="server")],
        )
        self.assertEqual(
            process_lifecycle.restart_debt_pairs([live, done]),
            [("live", "server"), ("live", "offline")],
        )

    def test_daemon_startup_seeds_restart_debt(self) -> None:
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary, patch.dict(
            os.environ, {"LOCALAPPDATA": temporary}
        ), patch.object(daemon, "_ensure_identity_migration", return_value=None):
            config = SimpleNamespace(
                enable_exec_enforce=False,
                exec_allowlist=None,
                require_version=False,
                expected_game_version=None,
                instance_token=None,
                live_profile=False,
                live_admin_enabled=False,
            )
            first = daemon.build_server_state(
                config, "k", daemon_generation="gen-1", activate_coordination=True
            )
            assert first.lifecycle is not None
            from dayz_mcp.process_lifecycle import ProcessRecord, RunRecord

            def _rec(pid, role):
                return ProcessRecord(
                    pid,
                    "2026-08-18T00:00:00.000000Z",
                    "a" * 64,
                    "b" * 64,
                    role,
                    identity_scheme="psutil-argv-v2",
                )

            # H1: crash_reporter owes no close and must not break startup.
            # M8/M10: EXITED runs seed nothing; offline seeds both peers.
            record = RunRecord(
                "restart-run",
                None,
                None,
                "RUNNING_IDLE",
                "wiring-fixture",
                "",
                "",
                "",
                [_rec(PID_SERVER, "server"), _rec(PID_CLIENT, "crash_reporter")],
            )
            first.lifecycle.manifest.add(record)
            first.lifecycle.manifest.add(
                RunRecord(
                    "offline-run",
                    None,
                    None,
                    "RUNNING_IDLE",
                    "wiring-fixture",
                    "",
                    "",
                    "",
                    [_rec(333, "offline")],
                )
            )
            first.lifecycle.manifest.add(
                RunRecord(
                    "old-run",
                    None,
                    None,
                    "EXITED",
                    "wiring-fixture",
                    "",
                    "",
                    "",
                    [],
                )
            )
            first.root_writer_lease.release()

            restarted = daemon.build_server_state(
                config, "k", daemon_generation="gen-2", activate_coordination=True
            )
            try:
                coordinator = restarted.coordination
                identity = _identity("a")
                photo = coordinator.status(identity).get("lease_end", {})
                self.assertEqual(
                    sorted(
                        (r["run_id"], r["peer"], tuple(r["reasons"]))
                        for r in photo.get("unconfirmed", [])
                    ),
                    [
                        ("offline-run", "client", ("daemon_restart",)),
                        ("offline-run", "server", ("daemon_restart",)),
                        ("restart-run", "server", ("daemon_restart",)),
                    ],
                )
                status, grant = coordinator.acquire(identity, "trace")
                self.assertEqual(status, 200, grant)
                self.assertIn(
                    "lease_end_unconfirmed", grant.get("cleanup_degraded", [])
                )
                coordinator.release(identity, grant["lease_token"])
                coordinator.note_processes_gone("restart-run", None)
                coordinator.note_processes_gone("offline-run", None)
                status, grant = coordinator.acquire(identity, "trace")
                self.assertEqual(status, 200, grant)
                self.assertNotIn(
                    "lease_end_unconfirmed", grant.get("cleanup_degraded", [])
                )
            finally:
                restarted.root_writer_lease.release()


if __name__ == "__main__":
    unittest.main()
