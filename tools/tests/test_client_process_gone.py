"""d17c-a: client-peer admission refuses at once on a known-dead client.

Before enqueueing a client-peer command the daemon classifies the exact
destination's registered client through the lifecycle's full native identity
check (``classify_registered_client_liveness``) and answers
``client_process_gone`` before publication, so a dead client fails in one
call instead of after the whole enqueue timeout. Unknown liveness keeps
today's admission path: doubt is never a death.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from dayz_mcp import bridge_errors, loopback, server
from dayz_mcp.session_coordination import SessionCoordinator
from dayz_mcp.process_lifecycle import RunRecord
from tests.fence_helpers import INST_CLIENT, INST_SERVER, accredited_poll, PID_CLIENT, PID_SERVER
from tests.lifecycle_helpers import (
    IDENTITY,
    LifecycleFixtureContext,
    identity as guard_identity,
    record as process_record,
    stamp_launcher,
)

RUN_ID = "run-d17ca"
INST_CLIENT_2 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
BOUND_PIDS = {"server": PID_SERVER, "client": PID_CLIENT}
BOUND_INSTANCES = {"server": INST_SERVER, "client": INST_CLIENT}
# A complete native snapshot that answers process_not_found (exit_code 4).
GONE = {"error": "process_not_found", "exit_code": 4}
# A guard that reads nothing answers unknown, never a death.
UNAVAILABLE = {"error": "identity_unavailable", "exit_code": 3}
IDENTITY_PAYLOAD = IDENTITY.to_payload()

# One valid payload per client command (d17c-a census). The census test
# validates each pair against loopback.validate_command_args before enqueueing.
_CENSUS_ARGS: dict[str, dict[str, object]] = {
    "camera_set": {
        "cam_mode": "orient",
        "cam_pos": [0.0, 2.0, 0.0],
        "cam_orientation": [90.0, 0.0, 0.0],
        "fov": 0.0,
        "settle_ticks": 3,
    },
    "camera_get": {},
    "restore_gameplay": {},
    "key_press": {"dik": 1},
    "input_describe": {"name": "UAMoveForward"},
    "input_trigger": {
        "trigger_kind": "key",
        "trigger_edge": "click",
        "trigger_entry": "game",
        "dik": 1,
    },
    "player_move": {"mode": "release"},
    "player_respawn": {},
    "player_trace": {
        "mode": "start",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "cursor": 0,
        "limit": 64,
        "sample_hz": 20,
        "max_samples": 4096,
    },
    "vehicle_get_in_client": {"pos": [0.0, 0.0, 0.0]},
    "engine_set": {"mode": "start"},
    "vehicle_control": {
        "throttle": 0.0,
        "steer": 0.0,
        "brake": 0.0,
        "handbrake": 0.0,
        "hold_ttl_s": 0.0,
    },
    "vehicle_telemetry": {},
    "vehicle_trace": {
        "mode": "start",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "cursor": 0,
        "limit": 64,
        "sample_hz": 20,
        "max_samples": 8192,
    },
    "vehicle_release": {},
    "ui_tree": {},
    "ui_set_text": {"path": "Root.Label", "text": "Ready"},
    "ui_click": {"path": "Root.Button"},
    "ui_reload_layout": {"path": "layout.gui"},
    "ui_focus": {"path": "Root.EditBox"},
    "ui_dialog": {"kind": "acknowledge", "title": "Notice", "message": "Ready"},
    "action_use": {"action": "open"},
    "action_use_door": {
        "action": "ActionOpenDoors",
        "classname": "Land_House_2W03",
        "door_index": 0,
    },
    "action_use_target": {"action": "open", "target": "hands"},
    "anim_timeline": {
        "mode": "start",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "cursor": 0,
        "limit": 64,
        "sample_hz": 20,
        "max_samples": 4096,
        "sources": [],
    },
    "weapon_aim": {"dx": 0.0, "dy": 0.0},
    "weapon_fire": {},
    "weapon_raise": {"raised": True, "hold_ttl_s": 3.0},
    "weapon_sights": {"mode": "ironsights"},
}


def _gate_state(
    *,
    roles: tuple[str, ...] = ("server", "client"),
    run_state: str = "RUNNING",
) -> tuple[object, LifecycleFixtureContext, list[object]]:
    """A ServerState wired to a real lifecycle with bound peers.

    The registered run is RUNNING (RUNNING_IDLE for the precedence test) with
    one process record per bound role; the caller seeds the guard snapshots
    that decide each classification.
    """
    fixture = LifecycleFixtureContext()
    state = loopback.ServerState("k")
    state.lifecycle = fixture.lifecycle
    records = []
    for role in roles:
        record = process_record(BOUND_PIDS[role], role=role)
        state.install_bound_peer(
            instance=BOUND_INSTANCES[role],
            role=role,
            pid=record.pid,
            run_id=RUN_ID,
            # Production confirm copies the record identity onto the binding.
            creation_time_utc=record.creation_time_utc,
        )
        records.append(record)
    owned = run_state == "RUNNING"
    run = RunRecord(
        RUN_ID,
        IDENTITY.session_id if owned else None,
        fixture.lease_id if owned else None,
        run_state,
        "gate",
        "",
        "",
        "",
        records,
    )
    fixture.store.add(run)
    stamp_launcher(fixture.lifecycle, RUN_ID, IDENTITY)
    return state, fixture, records


class DeadClientRefusalTest(unittest.TestCase):
    """The named refusal, and nothing published with it."""

    def test_d17ca_dead_client_refused_before_command_publication(self) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        client_record = records[1]
        fixture.guard.snapshots[client_record.pid] = dict(GONE)
        queue = state._bound_queues[INST_CLIENT]
        next_id = state._next_id
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(
            (status, payload.get("error")), (409, "client_process_gone")
        )
        self.assertEqual(payload.get("run_id"), RUN_ID)
        hint = payload.get("hint")
        self.assertIsInstance(hint, str)
        self.assertIn("session_status", hint)
        self.assertLessEqual(len(hint), 240)
        # Nothing was published: queue, ownership and the id counter are
        # exactly what they were, and no client poll can pick the command up.
        self.assertEqual(queue, [])
        self.assertEqual(state._next_id, next_id)
        self.assertEqual(state._command_owner, {})
        _, poll = accredited_poll(state, "client")
        self.assertEqual(poll["commands"], [])
        # The gate probed the registered identity and ran no process operation.
        self.assertIn(client_record.pid, fixture.guard.snapshot_calls)
        self.assertEqual(fixture.guard.terminate_calls, [])
        self.assertEqual(fixture.launcher.calls, [])
        self.assertEqual(fixture.audit.fail_events, set())

    def test_d17ca_refusal_aborts_the_lease_reservation(self) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[records[1].pid] = dict(GONE)
        coordinator = SessionCoordinator(
            token_fn=lambda: "token-gone",
            id_fn=lambda: "lease-gone",
            audit=lambda _event: True,
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        status, active = coordinator.acquire(IDENTITY, "gate")
        self.assertEqual(status, 200)
        token = active["lease_token"]
        status, payload = state.enqueue_command(
            "player_move",
            {"mode": "release"},
            peer="client",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=token,
            operation_timeout_s=300.0,
        )
        self.assertEqual(
            (status, payload.get("error")), (409, "client_process_gone")
        )
        # The reservation was aborted, no command owns the lease mapping and
        # the queue is still empty.
        self.assertEqual(state._command_owner, {})
        self.assertEqual(coordinator._active.pending_authorizations, [])  # type: ignore[attr-defined]
        self.assertEqual(state.pending_for_owner(IDENTITY.session_id), 0)
        self.assertEqual(state._bound_queues[INST_CLIENT], [])


class ClientVerbCensusTest(unittest.IsolatedAsyncioTestCase):
    """Every client verb is admitted through the dead-process gate."""

    def test_d17ca_all_client_verbs_use_dead_process_gate(self) -> None:
        for cmd, args in sorted(_CENSUS_ARGS.items()):
            with self.subTest(command=cmd):
                ok, err = loopback.validate_command_args(cmd, args)
                self.assertTrue(ok, f"{cmd}: {err}")
                state, fixture, records = _gate_state()
                self.addCleanup(fixture.close)
                fixture.guard.snapshots[records[1].pid] = dict(GONE)
                next_id = state._next_id
                status, payload = state.enqueue_command(cmd, args, peer="client")
                self.assertEqual(
                    (status, payload.get("error")), (409, "client_process_gone")
                )
                self.assertEqual(payload.get("run_id"), RUN_ID)
                self.assertEqual(state._bound_queues[INST_CLIENT], [])
                self.assertEqual(state._next_id, next_id)

    async def test_d17ca_ui_dialog_enqueue_probe_path_refuses_at_once(self) -> None:
        # execute_ui_dialog enqueues first and probes the result afterwards:
        # the named refusal must come from the enqueue, before any probe.
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[records[1].pid] = dict(GONE)
        runtime = server.Runtime(server.ServerConfig(key="k"))
        runtime.loopback = SimpleNamespace(state=state)
        with self.assertRaises(server.ToolError) as caught:
            await server.execute_ui_dialog(runtime, "acknowledge", "Notice", "Ready")
        message = str(caught.exception)
        self.assertTrue(message.startswith("client_process_gone"), message)
        self.assertIn(loopback._CLIENT_PROCESS_GONE_HINT, message)
        self.assertEqual(state._bound_queues[INST_CLIENT], [])
        self.assertEqual(state._next_id, 1)


class UnknownClientAdmissionTest(unittest.TestCase):
    """Doubt keeps today's admission path."""

    def test_d17ca_unknown_client_identity_allows_existing_admission(self) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[records[1].pid] = dict(UNAVAILABLE)
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        self.assertEqual(
            [c["cmd"] for c in state._bound_queues[INST_CLIENT]], ["camera_get"]
        )

    def test_d17ca_failing_guard_observation_passes_through(self) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)

        def boom(pid: int) -> dict[str, object]:
            raise RuntimeError("guard exploded")

        fixture.guard.snapshot = boom  # type: ignore[method-assign]
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        self.assertEqual(
            [c["cmd"] for c in state._bound_queues[INST_CLIENT]], ["camera_get"]
        )

    def test_d17ca_lifecycle_without_the_helper_keeps_today_s_admission(self) -> None:
        # An older lifecycle object without the classify helper is unknown,
        # which leaves the existing enqueue and timeout behaviour alone.
        state = loopback.ServerState("k")
        state.install_bound_peer(
            instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id=RUN_ID
        )
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)

    def test_d17ca_run_without_client_record_does_not_guess_death(self) -> None:
        # A registered run with no client/offline record has no client
        # process to classify; the gate stays open instead of guessing.
        state, fixture, _records = _gate_state(roles=("server",))
        self.addCleanup(fixture.close)
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)


class ReusedPidTest(unittest.TestCase):
    def test_d17ca_reused_pid_is_not_a_live_client(self) -> None:
        # A complete identity that matches nothing registered is foreign: the
        # recycled pid does not keep the old client alive.
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        reused = dict(guard_identity(records[1]))
        reused["creation_time_utc"] = "1999-01-01T00:00:00.000000Z"
        fixture.guard.snapshots[records[1].pid] = reused
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(
            (status, payload.get("error")), (409, "client_process_gone")
        )


class BindingChangeTest(unittest.TestCase):
    def test_d17ca_binding_change_discards_death_observation(self) -> None:
        # The destination that changed while the probe ran cannot inherit the
        # death verdict: the command follows the normal path on the new
        # binding instead of being refused.
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[records[1].pid] = dict(GONE)
        observed: list[bool] = []

        def rebind_mid_probe(run_id: str, destination=None) -> str:
            observed.append(state._lock.locked())
            state.retire_role(run_id, "client", "rebound")
            state.install_bound_peer(
                instance=INST_CLIENT_2,
                role="client",
                pid=records[1].pid,
                run_id=run_id,
                creation_time_utc=records[1].creation_time_utc,
            )
            return "dead"

        fixture.lifecycle.classify_registered_client_liveness = rebind_mid_probe  # type: ignore[method-assign]
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        self.assertEqual(observed, [False])
        self.assertEqual(
            [c["cmd"] for c in state._bound_queues[INST_CLIENT_2]], ["camera_get"]
        )
        # The retired destination kept nothing: its queue went with it.
        self.assertNotIn(INST_CLIENT, state._bound_queues)

    def test_d17ca_probe_never_holds_the_lifecycle_operation_lock(self) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        lifecycle = fixture.lifecycle
        seen: list[bool] = []
        original = lifecycle.classify_registered_client_liveness

        def watch(run_id: str, destination=None) -> str:
            seen.append(lifecycle._operation_lock.locked())
            return original(run_id)

        lifecycle.classify_registered_client_liveness = watch  # type: ignore[method-assign]
        status, _payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200)
        self.assertEqual(seen, [False])


class ReplacementRaceTest(unittest.TestCase):
    """F1: a live replacement never inherits the superseded client's death.

    Production reattach confirms the station binding before its manifest
    record is published. Admission can pin the replacement inside that
    window, while the registered snapshot still lists only the old client.
    A death computed from that snapshot belongs to the old identity: the
    helper only reads it as evidence when the snapshot observed the pinned
    destination.
    """

    def test_d17ca_confirmed_replacement_before_published_record_admits(
        self,
    ) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        # The superseded client has a complete, mismatching native identity.
        reused = dict(guard_identity(records[1]))
        reused["creation_time_utc"] = "1999-01-01T00:00:00.000000Z"
        fixture.guard.snapshots[records[1].pid] = reused
        # The reattach confirmed the replacement binding; its record is not
        # published yet, so the manifest still lists only the old client.
        replacement = process_record(9102, role="client")
        state.retire_role(RUN_ID, "client", "reattach")
        state.install_bound_peer(
            instance=INST_CLIENT_2,
            role="client",
            pid=replacement.pid,
            run_id=RUN_ID,
            creation_time_utc=replacement.creation_time_utc,
        )
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        self.assertEqual(
            [c["cmd"] for c in state._bound_queues[INST_CLIENT_2]],
            ["camera_get"],
        )
        # The probe read the registered snapshot, never the pinned
        # replacement that was absent from it.
        self.assertEqual(fixture.guard.snapshot_calls, [records[1].pid])

    def test_d17ca_replacement_publishing_mid_probe_discards_stale_death(
        self,
    ) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[records[1].pid] = dict(GONE)
        replacement = process_record(9102, role="client")
        fixture.guard.snapshots[replacement.pid] = guard_identity(replacement)
        # The destination binding is already the replacement client when
        # admission pins it; the binding stays unchanged for the whole call.
        state.retire_role(RUN_ID, "client", "reattach")
        state.install_bound_peer(
            instance=INST_CLIENT_2,
            role="client",
            pid=replacement.pid,
            run_id=RUN_ID,
            creation_time_utc=replacement.creation_time_utc,
        )
        published: list[bool] = []
        original_snapshot = fixture.guard.snapshot

        def snapshot_during_publish(pid: int) -> dict[str, object]:
            result = original_snapshot(pid)
            if pid == records[1].pid and not published:
                # Reattachment completes while the probe holds the clone:
                # the replacement's record reaches the manifest here.
                published.append(True)
                run = fixture.store.get(RUN_ID)
                run.processes.append(replacement)
                fixture.store.replace(run)
            return result

        fixture.guard.snapshot = snapshot_during_publish  # type: ignore[method-assign]
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        self.assertEqual(published, [True])
        self.assertEqual(
            [c["cmd"] for c in state._bound_queues[INST_CLIENT_2]],
            ["camera_get"],
        )
        # With the replacement published, the run's registered liveness
        # reads alive, not the inherited death of the superseded client.
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness(RUN_ID),
            "alive",
        )


class ErrorPrecedenceTest(unittest.TestCase):
    """Fence errors stay first where there is no eligible exact destination."""

    def test_d17ca_ownership_error_precedes_the_gate(self) -> None:
        state, fixture, _records = _gate_state(run_state="RUNNING_IDLE")
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[PID_CLIENT] = dict(GONE)
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual((status, payload.get("error")), (409, "run_not_owned"))
        self.assertEqual(state._bound_queues[INST_CLIENT], [])

    def test_d17ca_retired_binding_error_precedes_the_gate(self) -> None:
        state, fixture, _records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[PID_CLIENT] = dict(GONE)
        state.retire_role(RUN_ID, "client", "test-retire")
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(
            (status, payload.get("error")), (409, "binding_retired")
        )

    def test_d17ca_internal_cleanup_enqueue_skips_the_gate(self) -> None:
        # H5: the daemon's own fire-and-forget vehicle_release keeps working
        # when the client is already gone.
        state, fixture, _records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[PID_CLIENT] = dict(GONE)
        cleanup = state.cleanup_owner(
            "owner-gone", "lease-gone", "owner_release", True
        )
        self.assertEqual(cleanup["vehicle_release_enqueued"], 1)


class ControlsTest(unittest.TestCase):
    """Alive clients and server peers are not gated."""

    def test_d17ca_alive_client_still_admits(self) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[records[1].pid] = guard_identity(records[1])
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        self.assertEqual(
            [c["cmd"] for c in state._bound_queues[INST_CLIENT]], ["camera_get"]
        )

    def test_d17ca_dead_server_does_not_gate_server_peer(self) -> None:
        state, fixture, records = _gate_state()
        self.addCleanup(fixture.close)
        fixture.guard.snapshots[records[0].pid] = dict(GONE)
        fixture.guard.snapshots[records[1].pid] = dict(GONE)
        status, payload = state.enqueue_command(
            "query_all_players", {}, peer="server"
        )
        self.assertEqual(status, 200, payload)
        # ...while the same dead box still refuses the client verb.
        gone, refused = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual((gone, refused.get("error")), (409, "client_process_gone"))


class HelperContractTest(unittest.TestCase):
    """classify_registered_client_liveness answers exactly four verdicts."""

    def _fixture_with(
        self, records: list[object], guard_seeds: dict[int, dict[str, object]]
    ) -> LifecycleFixtureContext:
        fixture = LifecycleFixtureContext()
        self.addCleanup(fixture.close)
        if records:
            run = RunRecord(
                RUN_ID,
                IDENTITY.session_id,
                fixture.lease_id,
                "RUNNING",
                "gate",
                "",
                "",
                "",
                records,
            )
            fixture.store.add(run)
        fixture.guard.snapshots.update(guard_seeds)
        return fixture

    def test_d17ca_helper_alive_when_any_record_matches(self) -> None:
        client = process_record(PID_CLIENT, role="client")
        offline = process_record(PID_SERVER, role="offline")
        fixture = self._fixture_with(
            [client, offline],
            {PID_CLIENT: guard_identity(client), PID_SERVER: dict(GONE)},
        )
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness(RUN_ID), "alive"
        )

    def test_d17ca_helper_dead_when_all_records_are_gone_or_foreign(self) -> None:
        client = process_record(PID_CLIENT, role="client")
        offline = process_record(PID_SERVER, role="offline")
        foreign = dict(guard_identity(offline))
        foreign["executable_sha256"] = "c" * 64
        fixture = self._fixture_with(
            [client, offline],
            {PID_CLIENT: dict(GONE), PID_SERVER: foreign},
        )
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness(RUN_ID), "dead"
        )
        self.assertEqual(fixture.guard.terminate_calls, [])

    def test_d17ca_helper_unknown_when_any_record_is_unreadable(self) -> None:
        client = process_record(PID_CLIENT, role="client")
        offline = process_record(PID_SERVER, role="offline")
        fixture = self._fixture_with(
            [client, offline],
            {PID_CLIENT: dict(UNAVAILABLE), PID_SERVER: dict(GONE)},
        )
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness(RUN_ID),
            "unknown",
        )

    def test_d17ca_helper_none_without_player_records(self) -> None:
        server_record = process_record(PID_SERVER, role="server")
        fixture = self._fixture_with(
            [server_record], {PID_SERVER: guard_identity(server_record)}
        )
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness(RUN_ID), "none"
        )

    def test_d17ca_helper_death_requires_the_pinned_identity_observed(self) -> None:
        client = process_record(PID_CLIENT, role="client")
        fixture = self._fixture_with([client], {PID_CLIENT: dict(GONE)})
        classify = fixture.lifecycle.classify_registered_client_liveness
        # The pinned destination was among the observed records: the death
        # is evidence about exactly that identity.
        self.assertEqual(
            classify(RUN_ID, destination=(client.pid, client.creation_time_utc)),
            "dead",
        )
        # A destination the snapshot never observed stays doubt, never a
        # death inherited from the records that were there.
        self.assertEqual(
            classify(RUN_ID, destination=(9102, "2026-08-18T00:00:00.000000Z")),
            "unknown",
        )

    def test_d17ca_helper_unknown_for_missing_run(self) -> None:
        fixture = self._fixture_with([], {})
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness("no-such-run"),
            "unknown",
        )

    def test_d17ca_helper_unknown_for_bad_run_id(self) -> None:
        fixture = self._fixture_with([], {})
        classify = fixture.lifecycle.classify_registered_client_liveness
        for run_id in (None, "", 5, b"run"):
            self.assertEqual(classify(run_id), "unknown")

    def test_d17ca_helper_unknown_when_manifest_read_fails(self) -> None:
        fixture = self._fixture_with([], {})

        def boom(run_id: str) -> object:
            raise RuntimeError("disk")

        fixture.store.get = boom  # type: ignore[method-assign]
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness(RUN_ID),
            "unknown",
        )

    def test_d17ca_helper_unknown_for_unreadable_run_shape(self) -> None:
        fixture = self._fixture_with([], {})
        fixture.lifecycle.manifest = SimpleNamespace(  # type: ignore[attr-defined]
            get=lambda run_id: SimpleNamespace(state="RUNNING", processes=None)
        )
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness(RUN_ID),
            "unknown",
        )
        fixture.lifecycle.manifest = SimpleNamespace(  # type: ignore[attr-defined]
            get=lambda run_id: SimpleNamespace(
                state="RUNNING",
                processes=[SimpleNamespace(role="client", pid=PID_CLIENT)],
            )
        )
        self.assertEqual(
            fixture.lifecycle.classify_registered_client_liveness(RUN_ID),
            "unknown",
        )


class PublicTokenTest(unittest.TestCase):
    def test_d17ca_public_error_keeps_named_token(self) -> None:
        self.assertIn("client_process_gone", server._REMOTE_ERROR_CODES)
        self.assertNotIn(
            "client_process_gone", bridge_errors._PUBLISHED_NOT_READY_CODES
        )
        hint = loopback._CLIENT_PROCESS_GONE_HINT
        text = server._public_enqueue_error(
            {"error": "client_process_gone", "run_id": RUN_ID, "hint": hint}
        )
        self.assertTrue(text.startswith("client_process_gone"), text)
        self.assertIn(hint, text)
        self.assertNotIn("game_not_ready", text)
        self.assertNotIn("remote_error", text)
        # A hint that cannot travel leaves the bare named token.
        self.assertEqual(
            server._public_enqueue_error(
                {"error": "client_process_gone", "hint": "a" * 241}
            ),
            "client_process_gone",
        )
        self.assertEqual(
            server._public_enqueue_error({"error": "client_process_gone"}),
            "client_process_gone",
        )


class HttpRefusalTest(unittest.TestCase):
    """The HTTP refusal carries 409, the token and the run."""

    def setUp(self) -> None:
        self.state, self.fixture, records = _gate_state()
        self.addCleanup(self.fixture.close)
        self.fixture.guard.snapshots[records[1].pid] = dict(GONE)
        self.httpd = loopback.create_http_server(
            0, self.state, log_sink=lambda _message: None
        )
        self.addCleanup(self.httpd.server_close)
        import threading
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        self.addCleanup(self.thread.join, 2.0)
        self.addCleanup(self.httpd.shutdown)
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, dict]:
        import json
        import urllib.error
        import urllib.request
        import urllib.parse

        url = self.base + path + "?key=k"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0) as response:
                return int(response.status), json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                return int(exc.code), json.loads(exc.read().decode())
            finally:
                exc.close()

    def test_d17ca_http_refusal_carries_409_and_run_id(self) -> None:
        status, body = self.request(
            "POST",
            "/enqueue",
            {"cmd": "camera_get", "args": {}, "peer": "client"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(body.get("error"), "client_process_gone")
        self.assertEqual(body.get("run_id"), RUN_ID)
        self.assertIn("session_status", body.get("hint", ""))
