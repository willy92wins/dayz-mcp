"""Unit tests for instance fencing.

Each test is written so it is RED against the pre-fence tree: either an
assertion on new behavior, or a reference to a symbol the old code lacks.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import os
import re
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tests._addon_paths import addon_root

from dayz_mcp import instance_fence as instance_fence_mod
from dayz_mcp import loopback
from dayz_mcp import process_lifecycle as process_lifecycle_mod
from dayz_mcp import server as server_mod
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION, version_state_for
from dayz_mcp.native_process_guard import NativeProcessGuard
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator


INST_C1 = "11111111-1111-4111-8111-111111111111"
INST_C2 = "22222222-2222-4222-8222-222222222222"
INST_S1 = "33333333-3333-4333-8333-333333333333"
INST_OFF = "44444444-4444-4444-8444-444444444444"
HASH_A = "a" * 64
HASH_B = "b" * 64
TELEPORT = {"pos": [1.0, 2.0, 3.0]}
LEGACY_UNBOUND_HINT = (
    "Peer poll lacks a valid inst=. Relaunch via dayz_test_run so start_run "
    "writes instance into $profile:dayz_mcp.json. Do not mutate a hand-launched game."
)
IDENTITY_A = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")
MOD_SCRIPTS = addon_root() / "scripts" / "5_Mission"
BRIDGES = (
    MOD_SCRIPTS / "MCPBridge.c",
    MOD_SCRIPTS / "MCPClientBridge.c",
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


def _record(pid: int, role: str) -> ProcessRecord:
    return ProcessRecord(
        pid,
        f"2026-08-18T00:00:{pid % 60:02d}.000000Z",
        HASH_A,
        HASH_B,
        role,
        identity_scheme="psutil-argv-v2",
    )


def _snapshot(record: ProcessRecord) -> dict[str, object]:
    return {
        "pid": record.pid,
        "creation_time_utc": record.creation_time_utc,
        "executable_sha256": record.executable_sha256,
        "command_line_sha256": record.command_line_sha256,
        "identity_scheme": record.identity_scheme,
        "identity_complete": True,
    }


def _bind(
    state: loopback.ServerState,
    instance: str,
    role: str,
    pid: int,
    run_id: str = "run-fence",
) -> None:
    state.install_bound_peer(
        instance=instance,
        role=role,
        pid=pid,
        run_id=run_id,
        creation_time_utc=f"2026-08-18T00:00:{pid % 60:02d}.000000Z",
    )


class RecordPollFenceTest(unittest.TestCase):
    def test_record_poll_without_instance_does_not_deliver_mutation(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_S1, "server", 1001)
        status, payload = state.enqueue_command("player_teleport", dict(TELEPORT))
        self.assertEqual(status, 200, payload)
        _status, poll = state.record_poll("server")
        self.assertEqual(_status, 200)
        self.assertNotIn(
            "player_teleport",
            [command.get("cmd") for command in poll["commands"]],
        )

    def test_record_poll_without_instance_still_delivers_read(self) -> None:
        state = loopback.ServerState("k")
        status, payload = state.enqueue_command("query_player_state", {})
        self.assertEqual(status, 200, payload)
        self.assertTrue(hasattr(state, "_legacy_queues"))
        queued = [command.get("cmd") for command in state._legacy_queues["server"]]
        self.assertIn("query_player_state", queued)
        _status, poll = state.record_poll("server")
        self.assertEqual([command["id"] for command in poll["commands"]], [payload["id"]])

    def test_bound_poll_receives_only_its_instance_queue(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_C2, "client", 2002, run_id="run-c2")
        status, sealed = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, sealed)
        _bind(state, INST_C1, "client", 2001, run_id="run-c1")
        _status, poll_c1 = state.record_poll(
            "client", instance=INST_C1, source_pid=2001
        )
        self.assertNotIn(sealed["id"], [command["id"] for command in poll_c1["commands"]])
        _status, poll_c2 = state.record_poll(
            "client", instance=INST_C2, source_pid=2002
        )
        self.assertEqual([command["id"] for command in poll_c2["commands"]], [sealed["id"]])

    def test_role_mismatch_does_not_drain_server_queue(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_S1, "server", 1001)
        status, payload = state.enqueue_command("query_player_state", {})
        self.assertEqual(status, 200, payload)
        _status, poll = state.record_poll(
            "client", instance=INST_S1, source_pid=1001
        )
        self.assertEqual(poll["commands"], [])
        remaining = [command.get("id") for command in state._bound_queues[INST_S1]]
        self.assertIn(payload["id"], remaining)
        _status, server_poll = state.record_poll(
            "server", instance=INST_S1, source_pid=1001
        )
        self.assertEqual(
            [command["id"] for command in server_poll["commands"]], [payload["id"]]
        )

    def test_offline_instance_may_poll_both_peers(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_OFF, "offline", 3003)
        server_status, server_cmd = state.enqueue_command("query_player_state", {})
        client_status, client_cmd = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual((server_status, client_status), (200, 200), (server_cmd, client_cmd))
        _status, server_poll = state.record_poll(
            "server", instance=INST_OFF, source_pid=3003
        )
        self.assertEqual(
            [command["id"] for command in server_poll["commands"]], [server_cmd["id"]]
        )
        _status, client_poll = state.record_poll(
            "client", instance=INST_OFF, source_pid=3003
        )
        self.assertEqual(
            [command["id"] for command in client_poll["commands"]], [client_cmd["id"]]
        )

    def test_reconnect_gap_does_not_flush_bound_queue(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _bind(state, INST_C1, "client", 2001)
        state.record_poll("client", instance=INST_C1, source_pid=2001)
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        clock.advance(loopback.PEER_RECONNECT_GAP_S + 1.0)
        _status, poll = state.record_poll(
            "client", instance=INST_C1, source_pid=2001
        )
        self.assertEqual([command["id"] for command in poll["commands"]], [payload["id"]])

    def test_reconnect_gap_still_flushes_legacy_queue(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        self.assertTrue(hasattr(state, "_legacy_queues"))
        state.record_poll("client")
        _status, payload = state.enqueue_command("camera_get", {}, peer="client")
        clock.advance(loopback.PEER_RECONNECT_GAP_S + 1.0)
        _status, poll = state.record_poll("client")
        self.assertEqual(poll["commands"], [])
        result = state.take_result(payload["id"])
        self.assertIsNotNone(result)
        self.assertEqual(result["error"], "peer_reconnect_flush")
        self.assertEqual(state._legacy_queues["client"], [])

    def test_command_ttl_still_expires_bound_command(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _bind(state, INST_C1, "client", 2001)
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        clock.advance(loopback.COMMAND_TTL_S + 1.0)
        _status, poll = state.record_poll(
            "client", instance=INST_C1, source_pid=2001
        )
        self.assertEqual(poll["commands"], [])
        result = state.take_result(payload["id"])
        self.assertIsNotNone(result)
        self.assertEqual(result["error"], "stale_discarded")

    def test_second_pid_same_instance_marks_ambiguous(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_C1, "client", 2001)
        status, payload = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, payload)
        _status, first = state.record_poll(
            "client", instance=INST_C1, source_pid=9999
        )
        self.assertEqual(first["commands"], [])
        _status, second = state.record_poll(
            "client", instance=INST_C1, source_pid=2001
        )
        self.assertEqual(second["commands"], [])
        blocked, body = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(blocked, 409)
        self.assertEqual(body["error"], "instance_ambiguous")

    def test_unattributed_poll_gets_zero_commands(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_C1, "client", 2001)
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        _status, poll = state.record_poll(
            "client", instance=INST_C1, source_pid=None
        )
        self.assertEqual(poll["commands"], [])
        remaining = [command.get("id") for command in state._bound_queues[INST_C1]]
        self.assertIn(payload["id"], remaining)


class EnqueueFenceTest(unittest.TestCase):
    def test_enqueue_mutation_without_binding_is_legacy_unbound(self) -> None:
        state = loopback.ServerState("k")
        status, payload = state.enqueue_command("player_teleport", dict(TELEPORT))
        self.assertEqual(status, 409)
        self.assertEqual(payload.get("error"), "legacy_unbound")
        self.assertEqual(payload.get("hint"), LEGACY_UNBOUND_HINT)
        self.assertEqual(state.status_snapshot()["peers"]["server"]["queue_depth"], 0)


class ResultFenceTest(unittest.TestCase):
    def test_store_result_wrong_instance_is_discarded_200(self) -> None:
        state = loopback.ServerState("k")
        events: list[dict] = []

        class _Writer:
            def write(self, event: dict) -> bool:
                events.append(event)
                return True

        state.audit_writer = _Writer()
        _bind(state, INST_C1, "client", 2001)
        status, payload = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, payload)
        result_status, result = state.store_result(
            {"id": payload["id"], "ok": 1, "value": 9},
            instance=INST_C2,
        )
        self.assertEqual(result_status, 200)
        self.assertTrue(result.get("discarded"))
        stored = state.take_result(payload["id"])
        self.assertTrue(stored is None or stored.get("ok") is not True)
        if stored is not None:
            self.assertNotEqual(stored.get("value"), 9)
        blob = json.dumps(events) + json.dumps(result)
        self.assertNotIn(INST_C1, blob)
        self.assertNotIn(INST_C2, blob)
        names = [event.get("event") for event in events]
        self.assertIn("late_result_fenced", names)


class EnforceBridgeFenceTest(unittest.TestCase):
    def test_reload_key_after_failure_does_not_assign_peer_instance(self) -> None:
        for bridge in BRIDGES:
            with self.subTest(bridge=bridge.name):
                source = bridge.read_text(encoding="utf-8")
                self.assertIn("protected string m_PeerInstance;", source)
                reload_body = _method_body(
                    source, "protected void ReloadKeyAfterFailure()"
                )
                self.assertNotIn("m_PeerInstance", reload_body)

    def test_try_init_copies_instance_once_in_both_bridges(self) -> None:
        for bridge in BRIDGES:
            with self.subTest(bridge=bridge.name):
                source = bridge.read_text(encoding="utf-8")
                try_init = _method_body(source, "protected void TryInit()")
                self.assertIn("m_PeerInstance", try_init)
                self.assertIn("cfg.instance", try_init)
                start = _method_body(source, "protected void StartPoll()")
                self.assertIn("&inst=", start)
                self.assertIn("EncodeQueryValue(m_PeerInstance)", start)
                post = _method_body(source, "protected void PostResult(MCPResult result)")
                self.assertIn("&inst=", post)

    def test_start_poll_omits_inst_when_peer_instance_empty(self) -> None:
        for bridge in BRIDGES:
            with self.subTest(bridge=bridge.name):
                start = _method_body(
                    bridge.read_text(encoding="utf-8"), "protected void StartPoll()"
                )
                self.assertIn('m_PeerInstance != ""', start)
                self.assertNotIn("&inst=\"\"", start)
                self.assertIn("m_PeerInstance", start)


class LifecycleFenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        (self.game / "DayZ_BE.exe").write_bytes(b"")
        (self.game / "DayZ_x64.exe").write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.events: list[dict[str, object]] = []
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=lambda event: self.events.append(event) or True,
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.state = loopback.ServerState("k")
        self.snapshots: dict[int, dict[str, object]] = {}
        self.launcher = _IncrementingLauncher()

        class _Guard:
            def __init__(self, snapshots: dict[int, dict[str, object]]) -> None:
                self.snapshots = snapshots

            def snapshot(self, pid: int) -> dict[str, object]:
                return dict(self.snapshots.get(pid, {"error": "identity_unavailable"}))

            def terminate(self, record: ProcessRecord) -> dict[str, object]:
                return {"terminated": True}

        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=lambda event: self.events.append(event) or True,
            guard=_Guard(self.snapshots),
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-fence-1",
            bindings=self.state,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _profile(self, name: str) -> Path:
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "dayz_mcp.json").write_text(
            json.dumps(
                {"url": "http://127.0.0.1:8765/", "key": "k", "pollHz": 5},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return directory

    def _request(self, role: str, profiles: Path, run_id: str | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": role,
            "window_style": "normal",
            "label": "fence",
            "mod": "@Mod",
            "profiles": str(profiles),
            "mission": "test",
        }
        if run_id is not None:
            payload["run_id"] = run_id
        return payload

    def _seed_next_pid(self, role: str) -> ProcessRecord:
        record = _record(self.launcher.next_pid, role)
        self.snapshots[record.pid] = _snapshot(record)
        return record

    def test_adopt_run_does_not_confirm_binding(self) -> None:
        record = _record(107, "client")
        self.store.add(
            RunRecord(
                "run-existing",
                None,
                None,
                "RUNNING_IDLE",
                "same",
                "@Mod",
                "profiles",
                "mission",
                [record],
            )
        )
        self.snapshots[record.pid] = _snapshot(record)
        result = self.lifecycle.adopt_run(IDENTITY_A, self.token, "run-existing")
        self.assertEqual(result.get("ok"), True, result)
        bound = [
            binding
            for binding in self.state._bindings.values()
            if binding.state == "BOUND"
        ]
        self.assertEqual(bound, [])

    def test_start_run_writes_instance_before_popen(self) -> None:
        profiles = self._profile("client_profiles")
        config_path = profiles / "dayz_mcp.json"
        self.launcher.config_path = config_path
        self._seed_next_pid("client")
        result = self.lifecycle.start_run(
            IDENTITY_A, self.token, self._request("client", profiles)
        )
        self.assertTrue(result.get("ok"), result)
        minted = self.launcher.instance_at_launch
        self.assertIsNotNone(minted)
        self.assertEqual(uuid.UUID(str(minted)).version, 4)
        on_disk = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["instance"], minted)
        self.assertGreaterEqual(len(self.launcher.calls), 1)

    def test_start_run_does_not_write_mission_config(self) -> None:
        profiles = self._profile("client_profiles")
        mission = self.root / "mpmissions" / "dayzOffline.chernarusplus"
        mission.mkdir(parents=True)
        mission_cfg = mission / "dayz_mcp.json"
        mission_cfg.write_text(
            json.dumps({"url": "http://127.0.0.1:1/", "key": "mission", "pollHz": 5}),
            encoding="utf-8",
        )
        before = mission_cfg.read_bytes()
        self._seed_next_pid("client")
        result = self.lifecycle.start_run(
            IDENTITY_A, self.token, self._request("client", profiles)
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(mission_cfg.read_bytes(), before)
        client_cfg = json.loads((profiles / "dayz_mcp.json").read_text(encoding="utf-8"))
        self.assertIn("instance", client_cfg)

    def test_client_relaunch_retires_old_instance_keeps_server(self) -> None:
        server_profiles = self._profile("server_profiles")
        client_profiles = self._profile("client_profiles")
        self._seed_next_pid("server")
        started = self.lifecycle.start_run(
            IDENTITY_A, self.token, self._request("server", server_profiles)
        )
        self.assertTrue(started.get("ok"), started)
        run_id = started["run_id"]
        server_instance = json.loads(
            (server_profiles / "dayz_mcp.json").read_text(encoding="utf-8")
        )["instance"]
        self._seed_next_pid("client")
        first_client = self.lifecycle.start_run(
            IDENTITY_A,
            self.token,
            self._request("client", client_profiles, run_id=run_id),
        )
        self.assertTrue(first_client.get("ok"), first_client)
        client_v1 = json.loads(
            (client_profiles / "dayz_mcp.json").read_text(encoding="utf-8")
        )["instance"]
        old_pid = self.launcher.calls[-1][3]
        self._seed_next_pid("client")
        second_client = self.lifecycle.start_run(
            IDENTITY_A,
            self.token,
            self._request("client", client_profiles, run_id=run_id),
        )
        self.assertTrue(second_client.get("ok"), second_client)
        client_v2 = json.loads(
            (client_profiles / "dayz_mcp.json").read_text(encoding="utf-8")
        )["instance"]
        self.assertNotEqual(client_v1, client_v2)
        status, queued = self.state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, queued)
        _status, stale = self.state.record_poll(
            "client", instance=client_v1, source_pid=old_pid
        )
        self.assertEqual(stale["commands"], [])
        server_binding = self.state._bindings[server_instance]
        self.assertEqual(server_binding.state, "BOUND")

    def _start_bound_client(self) -> tuple[str, str, ProcessRecord]:
        profiles = self._profile("client_profiles")
        record = self._seed_next_pid("client")
        result = self.lifecycle.start_run(
            IDENTITY_A, self.token, self._request("client", profiles)
        )
        self.assertTrue(result.get("ok"), result)
        run_id = str(result["run_id"])
        instance = json.loads(
            (profiles / "dayz_mcp.json").read_text(encoding="utf-8")
        )["instance"]
        self.assertEqual(self.state._bindings[instance].state, "BOUND")
        status, payload = self.state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, payload)
        self.assertGreater(len(self.state._bound_queues.get(instance, [])), 0)
        return run_id, instance, record

    def _assert_binding_retired(self, instance: str) -> None:
        self.assertNotIn(instance, self.state._bindings)
        self.assertIn(instance, self.state._retired_instances)
        self.assertEqual(self.state._bound_queues.get(instance, []), [])

    def _mark_unacknowledged(self, run_id: str) -> RunRecord:
        run = self.store.get(run_id)
        self.assertIsNotNone(run)
        assert run is not None
        run.launch_operation_id = (
            run.launch_operation_id or "22222222-2222-4222-8222-222222222222"
        )
        run.launch_request_sha256 = run.launch_request_sha256 or ("a" * 64)
        run.launch_acknowledged = False
        self.store.replace(run)
        return run

    def test_release_owner_retires_bindings(self) -> None:
        run_id, instance, _record = self._start_bound_client()
        run = self._mark_unacknowledged(run_id)
        disposition = self.lifecycle.begin_release_owner(
            str(run.owner_session_id), str(run.owner_lease_id)
        )
        self.assertTrue(disposition.terminal_event.wait(2.0))
        self.assertTrue(
            disposition.terminal_result.get("terminal_safe"),
            disposition.terminal_result,
        )
        self._assert_binding_retired(instance)

    def test_repair_recovery_fault_retires_bindings(self) -> None:
        run_id, instance, _record = self._start_bound_client()
        run = self._mark_unacknowledged(run_id)
        run_hash = hashlib.sha256(
            json.dumps(
                dataclasses.asdict(run),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        result = self.lifecycle.repair_recovery_fault(
            {
                "scope": "run",
                "run_id": run.run_id,
                "launch_operation_id": run.launch_operation_id,
                "run_record_sha256": run_hash,
            }
        )
        self.assertTrue(result.get("terminal_safe"), result)
        self._assert_binding_retired(instance)

    def test_repair_manifest_recovery_retires_bindings(self) -> None:
        run_id, instance, _record = self._start_bound_client()
        run = self._mark_unacknowledged(run_id)
        raw = (
            json.dumps(
                {"version": 1, "runs": [dataclasses.asdict(run)]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        result = self.lifecycle.repair_manifest_recovery(raw)
        self.assertTrue(result.get("terminal_safe"), result)
        self._assert_binding_retired(instance)

    def test_admin_reconcile_retires_bindings(self) -> None:
        run_id, instance, record = self._start_bound_client()
        run = self.store.get(run_id)
        self.assertIsNotNone(run)
        assert run is not None
        run.state = "RUNNING_IDLE"
        run.owner_session_id = None
        run.owner_lease_id = None
        self.store.replace(run)
        self.snapshots[record.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        result = self.lifecycle.admin_reconcile(run_id, record.pid, "incident")
        self.assertEqual(result.get("state"), "EXITED", result)
        self.assertTrue(result.get("reconciled"), result)
        self._assert_binding_retired(instance)


class ExitedBindingInvariantTest(unittest.TestCase):
    """Every `state = "EXITED"` path must retire bindings, except the declared list."""

    DECLARED_EXCEPTIONS = {
        "_settle_failed_launch": (
            "EXITED only when previous is None; start_run already called "
            "_retire_minted on that minted role"
        ),
    }

    def test_every_exited_path_retires_bindings(self) -> None:
        source_path = Path(process_lifecycle_mod.__file__).resolve()
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []
                self.exited_at: dict[str, list[int]] = {}
                self.retire_at: set[str] = set()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Assign(self, node: ast.Assign) -> None:
                if self.stack and _assign_sets_exited(node):
                    name = ".".join(self.stack)
                    self.exited_at.setdefault(name, []).append(node.lineno)
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                if self.stack and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "_retire_run_bindings":
                        self.retire_at.add(".".join(self.stack))
                self.generic_visit(node)

        walker = Visitor()
        walker.visit(tree)
        self.assertTrue(walker.exited_at, "parser found no state = EXITED assignments")
        self.assertIn("_settle_failed_launch", walker.exited_at)
        missing = []
        for func, lines in sorted(walker.exited_at.items()):
            if func in self.DECLARED_EXCEPTIONS:
                continue
            if func not in walker.retire_at:
                missing.append(f"{func} (lines {lines})")
        self.assertEqual(
            missing,
            [],
            "EXITED without _retire_run_bindings: " + "; ".join(missing),
        )


def _assign_sets_exited(node: ast.Assign) -> bool:
    if not any(
        isinstance(target, ast.Attribute) and target.attr == "state"
        for target in node.targets
    ):
        return False
    return _value_can_be_exited(node.value)


def _value_can_be_exited(value: ast.AST) -> bool:
    if isinstance(value, ast.Constant) and value.value == "EXITED":
        return True
    if isinstance(value, ast.IfExp):
        return _value_can_be_exited(value.body) or _value_can_be_exited(value.orelse)
    return False


class _IncrementingLauncher:
    def __init__(self) -> None:
        self.next_pid = 9001
        self.calls: list[tuple[list[str], str, str, int]] = []
        self.config_path: Path | None = None
        self.instance_at_launch: str | None = None

    def __call__(self, argv: list[str], cwd: str, window_style: str):
        if self.config_path is not None and self.config_path.is_file():
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.instance_at_launch = payload.get("instance")
        pid = self.next_pid
        self.next_pid += 1
        self.calls.append((list(argv), cwd, window_style, pid))
        return type("Launched", (), {"pid": pid})()


class StatusAndVersionFenceTest(unittest.TestCase):
    def test_status_hides_full_instance(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_C1, "client", 2001)
        snapshot = state.status_snapshot()
        blob = json.dumps(snapshot)
        self.assertNotIn(INST_C1, blob)
        prefix = snapshot["peers"]["client"].get("instance_prefix")
        self.assertEqual(prefix, uuid.UUID(INST_C1).hex[:8])
        self.assertEqual(len(str(prefix)), 8)
        self.assertNotRegex(blob, r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

    def test_ready_uses_bound_last_poll_not_legacy(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _bind(state, INST_S1, "server", 1001)
        _bind(state, INST_C1, "client", 2001)
        state.record_poll("server", "8~1.29.0", instance=INST_S1, source_pid=1001)
        state.record_poll("client", "8~1.29.0", instance=INST_C1, source_pid=2001)
        clock.advance(20.0)
        state.record_poll("server", "8~1.29.0")
        state.record_poll("client", "8~1.29.0")
        snapshot = state.status_snapshot()
        server_peer = snapshot["peers"]["server"]
        client_peer = snapshot["peers"]["client"]
        self.assertLess(server_peer["last_poll_age_s"], 1.0)
        self.assertGreaterEqual(server_peer["bound_last_poll_age_s"], 19.0)
        ready = server_mod.compute_bridge_ready(
            {
                "server_peer": {**server_peer, "version_state": "ok"},
                "client_peer": {**client_peer, "version_state": "ok"},
            }
        )
        self.assertFalse(ready["ready"])
        self.assertNotEqual(ready["reason"], "ready")

    def test_version_gate_unchanged_for_v8_without_inst(self) -> None:
        state, detail = version_state_for(
            "8~1.29.0",
            require_version=False,
            expected_game_version=None,
        )
        self.assertEqual(state, "ok")
        self.assertNotEqual(state, "version_mismatch")
        runtime = loopback.ServerState(
            "k",
            version_validator=lambda version: version_state_for(
                version,
                require_version=False,
                expected_game_version=None,
            )[0],
        )
        runtime.record_poll("server", "8~1.29.0")
        status, payload = runtime.enqueue_command("player_teleport", dict(TELEPORT))
        self.assertNotEqual(payload.get("error"), "version_blocked")
        self.assertEqual(status, 409)
        self.assertEqual(payload.get("error"), "legacy_unbound")

    def test_expected_bridge_version_stays_8(self) -> None:
        self.assertEqual(EXPECTED_BRIDGE_VERSION, "8")
        messages = (MOD_SCRIPTS / "MCPMessages.c").read_text(encoding="utf-8")
        self.assertIn('const string MCP_BRIDGE_VERSION = "8";', messages)
        self.assertNotIn('const string MCP_BRIDGE_VERSION = "9";', messages)
        match = re.search(r"class MCPConfig\s*\{([^}]*)\}", messages)
        self.assertIsNotNone(match)
        self.assertIn("string instance;", match.group(1))


class _FakeSock:
    def __init__(self, local: tuple[str, int], peer: tuple[str, int]) -> None:
        self._local = local
        self._peer = peer

    def getsockname(self) -> tuple[str, int]:
        return self._local

    def getpeername(self) -> tuple[str, int]:
        return self._peer


class _FakeConn:
    def __init__(
        self,
        laddr: tuple[str, int],
        raddr: tuple[str, int],
        pid: int,
        status: str,
    ) -> None:
        self.laddr = laddr
        self.raddr = raddr
        self.pid = pid
        self.status = status


def _established_status() -> str:
    try:
        import psutil
    except ImportError:
        return "ESTABLISHED"
    return getattr(psutil, "CONN_ESTABLISHED", "ESTABLISHED")


def _profile_dir(root: Path, name: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dayz_mcp.json").write_text(
        json.dumps(
            {"url": "http://127.0.0.1:8765/", "key": "k", "pollHz": 5},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return directory


class ProductionAttributionFenceTest(unittest.TestCase):
    """prepare+confirm + resolve_poll_pid path.

    Does not use install_bound_peer or _test_identity_override. The PID comes from
    connections_fn against THIS socket.
    """

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = loopback.ServerState("k")
        self.profiles = _profile_dir(self.root, "client_profiles")
        self.daemon_ep = ("127.0.0.1", 8765)
        self.sock_registered = _FakeSock(self.daemon_ep, ("127.0.0.1", 51001))
        self.sock_intruder = _FakeSock(self.daemon_ep, ("127.0.0.1", 51002))
        self.pid_registered = 61001
        self.pid_intruder = 61002
        self.record = _record(self.pid_registered, "client")
        status = _established_status()
        self.table = [
            _FakeConn(
                self.sock_registered.getpeername(),
                self.sock_registered.getsockname(),
                self.pid_registered,
                status,
            ),
            _FakeConn(
                self.sock_intruder.getpeername(),
                self.sock_intruder.getsockname(),
                self.pid_intruder,
                status,
            ),
        ]
        self.state._connections_fn = lambda: list(self.table)
        minted = self.state.prepare("run-attr", "client", str(self.profiles))
        self.instance = minted
        self.state.confirm(minted, self.record)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_resolve_poll_pid_does_not_reuse_cached_pid_across_sockets(self) -> None:
        first = self.state.resolve_poll_pid(self.instance, self.sock_registered)
        second = self.state.resolve_poll_pid(self.instance, self.sock_intruder)
        self.assertEqual(first, self.pid_registered)
        self.assertEqual(second, self.pid_intruder)
        self.assertNotEqual(first, second)

    def test_second_socket_same_instance_does_not_receive_mutation(self) -> None:
        status, payload = self.state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, payload)
        registered_pid = self.state.resolve_poll_pid(
            self.instance, self.sock_registered
        )
        _status, first = self.state.record_poll(
            "client",
            instance=self.instance,
            source_pid=registered_pid,
            source_creation_time=self.record.creation_time_utc,
        )
        self.assertEqual(
            [command["id"] for command in first["commands"]], [payload["id"]]
        )

        status, second_cmd = self.state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, second_cmd)
        intruder_pid = self.state.resolve_poll_pid(
            self.instance, self.sock_intruder
        )
        _status, second = self.state.record_poll(
            "client",
            instance=self.instance,
            source_pid=intruder_pid,
            source_creation_time=self.record.creation_time_utc,
        )
        self.assertEqual(intruder_pid, self.pid_intruder)
        self.assertEqual(second["commands"], [])
        self.assertEqual(self.state._bindings[self.instance].state, "AMBIGUOUS")
        blocked, body = self.state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(blocked, 409)
        self.assertEqual(body["error"], "instance_ambiguous")


class Round3FenceRegressionTest(unittest.TestCase):
    def test_creation_time_mismatch_marks_ambiguous(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_C1, "client", 2001)
        status, payload = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, payload)
        _status, poll = state.record_poll(
            "client",
            instance=INST_C1,
            source_pid=2001,
            source_creation_time="1999-01-01T00:00:00.000000Z",
        )
        self.assertEqual(poll["commands"], [])
        self.assertEqual(state._bindings[INST_C1].state, "AMBIGUOUS")

    def test_ready_false_when_binding_retired(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _bind(state, INST_S1, "server", 1001)
        _bind(state, INST_C1, "client", 2001)
        state.record_poll("server", "8~1.29.0", instance=INST_S1, source_pid=1001)
        state.record_poll("client", "8~1.29.0", instance=INST_C1, source_pid=2001)
        state.retire_run("run-fence", "stopped")
        state.record_poll("server", "8~1.29.0", instance=INST_S1, source_pid=1001)
        state.record_poll("client", "8~1.29.0", instance=INST_C1, source_pid=2001)
        snapshot = state.status_snapshot()
        ready = server_mod.compute_bridge_ready(
            {
                "server_peer": {**snapshot["peers"]["server"], "version_state": "ok"},
                "client_peer": {**snapshot["peers"]["client"], "version_state": "ok"},
            }
        )
        self.assertFalse(ready["ready"])
        self.assertNotEqual(ready["reason"], "ready")
        status, payload = state.enqueue_command("player_teleport", dict(TELEPORT))
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "binding_retired")
        self.assertNotIn("it will target the new instance", payload.get("hint", ""))

    def test_starting_does_not_enqueue_reads(self) -> None:
        state = loopback.ServerState("k")
        with TemporaryDirectory() as raw:
            profiles = _profile_dir(Path(raw), "p")
            minted = state.prepare("run-start", "server", str(profiles))
        self.assertEqual(state._bindings[minted].state, "STARTING")
        status, payload = state.enqueue_command("query_player_state", {})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "binding_not_ready")

    def test_confirm_without_pid_stays_starting(self) -> None:
        state = loopback.ServerState("k")
        with TemporaryDirectory() as raw:
            profiles = _profile_dir(Path(raw), "p")
            minted = state.prepare("run-start", "client", str(profiles))
        state.confirm(minted, object())
        self.assertEqual(state._bindings[minted].state, "STARTING")
        status, payload = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "binding_not_ready")

    def test_two_bound_peers_is_instance_peer_collision(self) -> None:
        state = loopback.ServerState("k")
        _bind(state, INST_C1, "client", 2001, run_id="run-a")
        _bind(state, INST_C2, "client", 2002, run_id="run-b")
        status, payload = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "instance_peer_collision")
        self.assertIn("hint", payload)
        self.assertNotEqual(payload["error"], "legacy_unbound")

    def test_retire_does_not_accumulate_bindings(self) -> None:
        state = loopback.ServerState("k")
        with TemporaryDirectory() as raw:
            profiles = _profile_dir(Path(raw), "p")
            for index in range(200):
                minted = state.prepare(f"run-{index}", "client", str(profiles))
                state.confirm(minted, _record(7000 + index, "client"))
                state.retire_role(f"run-{index}", "client", "stopped")
        active = [
            binding
            for binding in state._bindings.values()
            if binding.state != "RETIRED"
        ]
        self.assertEqual(active, [])
        self.assertLessEqual(len(state._bindings), 1)
        self.assertLessEqual(len(state._bound_queues), 1)
        limit = getattr(loopback, "RETIRED_INSTANCE_LIMIT", 0)
        self.assertGreater(limit, 0)
        self.assertLess(len(state._retired_instances), 200)
        self.assertLessEqual(len(state._retired_instances), limit)


class Round4FenceRegressionTest(unittest.TestCase):
    """Re-revision holes: supply, ready, retire cap, table cache, counters."""

    def _prepare_confirmed(
        self, pid: int = 77001, role: str = "client"
    ) -> tuple[loopback.ServerState, str, ProcessRecord]:
        state = loopback.ServerState("k")
        with TemporaryDirectory() as raw:
            profiles = _profile_dir(Path(raw), "p")
            minted = state.prepare("run-r4", role, str(profiles))
        record = _record(pid, role)
        state.confirm(minted, record)
        self.assertEqual(state._bindings[minted].state, "BOUND")
        self.assertEqual(state._bindings[minted].creation_time_utc, record.creation_time_utc)
        return state, minted, record

    def _enqueue_client_mutation(self, state: loopback.ServerState) -> dict:
        status, payload = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, payload)
        return payload

    def test_lookup_creation_time_via_lifecycle_like_daemon(self) -> None:
        """(b) Walk state.lifecycle.guard.snapshot as daemon.py:578 wires it."""
        state = loopback.ServerState("k")
        guard = NativeProcessGuard()
        state.lifecycle = SimpleNamespace(guard=guard)
        pid = os.getpid()
        got = state._lookup_creation_time(pid)
        snap = guard.snapshot(pid)
        self.assertIs(snap.get("identity_complete"), True, snap)
        self.assertIsInstance(got, str)
        self.assertTrue(got)
        self.assertEqual(got, snap["creation_time_utc"])
        self.assertRegex(got, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

    def test_missing_creation_time_without_lifecycle_does_not_deliver(self) -> None:
        state, minted, record = self._prepare_confirmed()
        self.assertIsNone(state.lifecycle)
        self.assertIsNone(state._creation_time_fn)
        payload = self._enqueue_client_mutation(state)
        _status, poll = state.record_poll(
            "client", instance=minted, source_pid=record.pid
        )
        self.assertEqual(poll["commands"], [])
        self.assertNotIn(payload["id"], [command.get("id") for command in poll["commands"]])
        self.assertEqual(state._bindings[minted].state, "creation_time_unreadable")

    def test_missing_creation_time_when_reader_raises_does_not_deliver(self) -> None:
        state, minted, record = self._prepare_confirmed()

        def boom(_pid: int) -> str | None:
            raise RuntimeError("creation_time_unavailable")

        state._creation_time_fn = boom
        payload = self._enqueue_client_mutation(state)
        _status, poll = state.record_poll(
            "client", instance=minted, source_pid=record.pid
        )
        self.assertEqual(poll["commands"], [])
        self.assertNotIn(payload["id"], [command.get("id") for command in poll["commands"]])
        self.assertEqual(state._bindings[minted].state, "creation_time_unreadable")

    def test_missing_creation_time_when_reader_returns_none_does_not_deliver(
        self,
    ) -> None:
        state, minted, record = self._prepare_confirmed()
        state._creation_time_fn = lambda _pid: None
        payload = self._enqueue_client_mutation(state)
        _status, poll = state.record_poll(
            "client", instance=minted, source_pid=record.pid
        )
        self.assertEqual(poll["commands"], [])
        self.assertNotIn(payload["id"], [command.get("id") for command in poll["commands"]])
        self.assertEqual(state._bindings[minted].state, "creation_time_unreadable")

    def test_creation_time_format_helper_locks_isoformat_microseconds_z(self) -> None:
        self.assertTrue(hasattr(instance_fence_mod, "format_creation_time_utc"))
        formatted = instance_fence_mod.format_creation_time_utc(1_700_000_000.125)
        self.assertEqual(formatted, "2023-11-14T22:13:20.125000Z")
        self.assertTrue(formatted.endswith("Z"))
        self.assertNotIn("+00:00", formatted)
        self.assertRegex(formatted, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
        snapshot_src = inspect.getsource(NativeProcessGuard._snapshot_process)
        self.assertIn('isoformat(timespec="microseconds")', snapshot_src)
        self.assertIn('"+00:00"', snapshot_src)
        self.assertIn('"Z"', snapshot_src)

    def test_confirm_and_lookup_share_creation_time_format(self) -> None:
        state = loopback.ServerState("k")
        guard = NativeProcessGuard()
        state.lifecycle = SimpleNamespace(guard=guard)
        pid = os.getpid()
        snap = guard.snapshot(pid)
        self.assertIs(snap.get("identity_complete"), True, snap)
        with TemporaryDirectory() as raw:
            minted = state.prepare(
                "run-r4-fmt", "client", str(_profile_dir(Path(raw), "p"))
            )
        state.confirm(
            minted,
            ProcessRecord(
                int(snap["pid"]),
                str(snap["creation_time_utc"]),
                str(snap["executable_sha256"]),
                str(snap["command_line_sha256"]),
                "client",
                identity_scheme=str(snap["identity_scheme"]),
            ),
        )
        stored = state._bindings[minted].creation_time_utc
        looked = state._lookup_creation_time(pid)
        self.assertEqual(stored, looked)
        self.assertEqual(stored, snap["creation_time_utc"])
        self.assertRegex(str(stored), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

    def test_legacy_unbound_is_not_ready_with_own_reason(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        state.record_poll("server", "8~1.29.0")
        snapshot = state.status_snapshot()
        self.assertEqual(snapshot["peers"]["server"]["binding_state"], "LEGACY_UNBOUND")
        ready = server_mod.compute_bridge_ready(
            {
                "server_peer": {**snapshot["peers"]["server"], "version_state": "ok"},
                "client_peer": {**snapshot["peers"]["client"], "version_state": "ok"},
            }
        )
        self.assertFalse(ready["ready"])
        self.assertEqual(ready["reason"], "legacy_unbound")
        self.assertIn("legacy_unbound", server_mod.READY_REASONS)
        self.assertNotEqual(ready["reason"], "no_run")
        self.assertNotEqual(ready["reason"], "server_poll_stale")
        status, payload = state.enqueue_command("player_teleport", dict(TELEPORT))
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "legacy_unbound")

    def test_never_polled_stays_no_run_not_legacy_unbound(self) -> None:
        state = loopback.ServerState("k")
        snapshot = state.status_snapshot()
        ready = server_mod.compute_bridge_ready(
            {
                "server_peer": {
                    **snapshot["peers"]["server"],
                    "version_state": "legacy",
                },
                "client_peer": {
                    **snapshot["peers"]["client"],
                    "version_state": "legacy",
                },
            }
        )
        self.assertFalse(ready["ready"])
        self.assertEqual(ready["reason"], "no_run")

    def test_tcp_table_snapshot_resolves_two_sockets_to_distinct_pids(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        with TemporaryDirectory() as raw:
            minted = state.prepare(
                "run-r4-tcp", "client", str(_profile_dir(Path(raw), "p"))
            )
        state.confirm(minted, _record(61001, "client"))
        status = _established_status()
        sock_a = _FakeSock(("127.0.0.1", 8765), ("127.0.0.1", 52001))
        sock_b = _FakeSock(("127.0.0.1", 8765), ("127.0.0.1", 52002))
        table = [
            _FakeConn(sock_a.getpeername(), sock_a.getsockname(), 61001, status),
            _FakeConn(sock_b.getpeername(), sock_b.getsockname(), 61002, status),
        ]
        fetches = {"n": 0}

        def connections_fn() -> list:
            fetches["n"] += 1
            return list(table)

        state._connections_fn = connections_fn
        first = state.resolve_poll_pid(minted, sock_a)
        second = state.resolve_poll_pid(minted, sock_b)
        self.assertEqual(first, 61001)
        self.assertEqual(second, 61002)
        self.assertNotEqual(first, second)
        self.assertEqual(fetches["n"], 2)

    def test_stale_tcp_snapshot_missing_socket_is_unattributed(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        with TemporaryDirectory() as raw:
            minted = state.prepare(
                "run-r4-stale", "client", str(_profile_dir(Path(raw), "p"))
            )
        record = _record(61001, "client")
        state.confirm(minted, record)
        status = _established_status()
        sock_reg = _FakeSock(("127.0.0.1", 8765), ("127.0.0.1", 53001))
        sock_new = _FakeSock(("127.0.0.1", 8765), ("127.0.0.1", 53002))
        live_table = [
            _FakeConn(sock_reg.getpeername(), sock_reg.getsockname(), 61001, status)
        ]

        def connections_fn() -> list:
            return list(live_table)

        state._connections_fn = connections_fn
        self.assertEqual(state.resolve_poll_pid(minted, sock_reg), 61001)
        live_table.append(
            _FakeConn(sock_new.getpeername(), sock_new.getsockname(), 61099, status)
        )
        self.assertEqual(state.resolve_poll_pid(minted, sock_new), 61099)

    def test_handle_poll_does_not_lookup_creation_time(self) -> None:
        source = inspect.getsource(loopback.Handler._handle_poll)
        self.assertNotIn("_lookup_creation_time", source)

    def test_record_poll_looks_up_creation_time_once(self) -> None:
        state, minted, record = self._prepare_confirmed()
        calls: list[int] = []

        def reader(pid: int) -> str:
            calls.append(pid)
            return record.creation_time_utc

        state._creation_time_fn = reader
        state.record_poll("client", instance=minted, source_pid=record.pid)
        self.assertEqual(calls, [record.pid])

    def test_status_snapshot_exposes_fence_counters(self) -> None:
        state = loopback.ServerState("k")
        status, payload = state.enqueue_command("player_teleport", dict(TELEPORT))
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "legacy_unbound")
        state.record_poll("server")
        snapshot = state.status_snapshot()
        fence = snapshot["fence"]
        rejects = fence["mutation_rejects_by_code"]
        self.assertEqual(rejects["legacy_unbound"], 1)
        for code in (
            "legacy_unbound",
            "instance_unknown",
            "instance_ambiguous",
            "instance_unattributed",
            "binding_retired",
            "instance_role_mismatch",
            "instance_peer_collision",
            "binding_not_ready",
        ):
            self.assertIn(code, rejects)
            self.assertIsInstance(rejects[code], int)
        self.assertEqual(fence["unaccredited_mutation_enqueues"], 0)
        polls = fence["unaccredited_polls_by_class"]
        self.assertGreaterEqual(polls.get("LEGACY_UNBOUND", 0), 1)
        blob = json.dumps(snapshot)
        self.assertNotRegex(
            blob,
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        )

    def test_accredited_mutation_does_not_increment_unaccredited_delivery(
        self,
    ) -> None:
        state, minted, record = self._prepare_confirmed()
        state._creation_time_fn = lambda _pid: record.creation_time_utc
        status, payload = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, payload)
        snapshot = state.status_snapshot()
        self.assertEqual(snapshot["fence"]["unaccredited_mutation_enqueues"], 0)


class Round5FenceRegressionTest(unittest.TestCase):
    """NUEVO-A/B cache phase bugs, MAYOR-1 residues, shortcut enclosure."""

    def _phased_state(self, role: str = "client"):
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        profiles = _profile_dir(root, role)
        minted = state.prepare("run-r5", role, str(profiles))
        record = _record(61001, role)
        state.confirm(minted, record)
        status = _established_status()
        live: list[_FakeConn] = []

        def connections_fn() -> list:
            return list(live)

        state._connections_fn = connections_fn
        return clock, state, minted, record, live, status, temporary

    def _new_sock_conn(
        self,
        live: list[_FakeConn],
        status: str,
        pid: int,
        port: int,
    ) -> tuple[_FakeSock, _FakeConn]:
        sock = _FakeSock(("127.0.0.1", 8765), ("127.0.0.1", port))
        conn = _FakeConn(sock.getpeername(), sock.getsockname(), pid, status)
        live.append(conn)
        return sock, conn

    def test_phased_same_run_client_never_unattributed(self) -> None:
        """(f) server+client, client 20 ms behind, 40 ticks, 0 unattributed."""
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        inst_s = state.prepare("run-r5", "server", str(_profile_dir(root, "s")))
        inst_c = state.prepare("run-r5", "client", str(_profile_dir(root, "c")))
        rec_s = _record(61001, "server")
        rec_c = _record(61002, "client")
        state.confirm(inst_s, rec_s)
        state.confirm(inst_c, rec_c)
        status = _established_status()
        live: list[_FakeConn] = []
        state._connections_fn = lambda: list(live)
        client_binds: list[str] = []
        port = 54000
        for tick in range(40):
            port += 1
            sock_s, conn_s = self._new_sock_conn(live, status, 61001, port)
            pid_s = state.resolve_poll_pid(inst_s, sock_s)
            state.record_poll(
                "server",
                instance=inst_s,
                source_pid=pid_s,
                source_creation_time=rec_s.creation_time_utc,
            )
            live.remove(conn_s)
            clock.advance(0.020)
            port += 1
            sock_c, conn_c = self._new_sock_conn(live, status, 61002, port)
            pid_c = state.resolve_poll_pid(inst_c, sock_c)
            _status, poll = state.record_poll(
                "client",
                instance=inst_c,
                source_pid=pid_c,
                source_creation_time=rec_c.creation_time_utc,
            )
            live.remove(conn_c)
            client_binds.append(str(poll.get("bind")))
            clock.advance(0.180)
        unattributed = sum(1 for label in client_binds if label == "instance_unattributed")
        self.assertEqual(unattributed, 0, client_binds[:8])
        self.assertEqual(state._bindings[inst_c].state, "BOUND")

    def test_phased_intruder_marks_ambiguous(self) -> None:
        """(d) registered + intruder 20 ms behind → AMBIGUOUS."""
        clock, state, minted, record, live, status, temporary = self._phased_state()
        self.addCleanup(temporary.cleanup)
        port = 55000
        for tick in range(40):
            port += 1
            sock_r, conn_r = self._new_sock_conn(live, status, 61001, port)
            pid_r = state.resolve_poll_pid(minted, sock_r)
            state.record_poll(
                "client",
                instance=minted,
                source_pid=pid_r,
                source_creation_time=record.creation_time_utc,
            )
            live.remove(conn_r)
            clock.advance(0.020)
            port += 1
            sock_i, conn_i = self._new_sock_conn(live, status, 61999, port)
            pid_i = state.resolve_poll_pid(minted, sock_i)
            state.record_poll(
                "client",
                instance=minted,
                source_pid=pid_i,
                source_creation_time=record.creation_time_utc,
            )
            live.remove(conn_i)
            clock.advance(0.180)
        self.assertEqual(state._bindings[minted].state, "AMBIGUOUS")

    def test_unread_creation_time_has_own_class_not_ambiguous(self) -> None:
        state = loopback.ServerState("k")
        with TemporaryDirectory() as raw:
            minted = state.prepare(
                "run-r5-unread", "client", str(_profile_dir(Path(raw), "p"))
            )
        record = _record(77001, "client")
        state.confirm(minted, record)
        status, queued = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(status, 200, queued)
        _status, poll = state.record_poll(
            "client", instance=minted, source_pid=record.pid
        )
        self.assertEqual(poll["commands"], [])
        self.assertEqual(poll.get("bind"), "creation_time_unreadable")
        self.assertNotEqual(state._bindings[minted].state, "AMBIGUOUS")
        self.assertEqual(state._bindings[minted].state, "creation_time_unreadable")
        blocked, body = state.enqueue_command(
            "camera_set", {"cam_mode": "orient"}, peer="client"
        )
        self.assertEqual(blocked, 409)
        self.assertEqual(body["error"], "creation_time_unreadable")
        hint = str(body.get("hint", ""))
        self.assertIn("creation time", hint.lower())
        self.assertNotIn("Two processes presented the same instance", hint)
        self.assertIn("creation_time_unreadable", server_mod.READY_REASONS)

    def test_normalize_creation_time_utc_rejects_extremes_without_raising(
        self,
    ) -> None:
        extremes = (
            "9999-12-31T23:59:59.999999Z",
            "1601-01-01T00:00:00.000000Z",
        )
        for stamp in extremes:
            with self.subTest(stamp=stamp):
                try:
                    got = instance_fence_mod.normalize_creation_time_utc(stamp)
                except Exception as exc:  # noqa: BLE001 — the bug is the raise
                    self.fail(f"normalize raised {type(exc).__name__}: {exc}")
                self.assertIsNone(got)

    def test_status_snapshot_names_unaccredited_enqueues_not_deliveries(
        self,
    ) -> None:
        state = loopback.ServerState("k")
        snapshot = state.status_snapshot()
        fence = snapshot["fence"]
        self.assertIn("unaccredited_mutation_enqueues", fence)
        self.assertNotIn("unaccredited_mutation_deliveries", fence)
        self.assertEqual(fence["unaccredited_mutation_enqueues"], 0)

    def test_test_identity_override_is_the_only_shortcut(self) -> None:
        state = loopback.ServerState("k")
        self.assertTrue(hasattr(state, "_test_identity_override"))
        self.assertIsNone(state._test_identity_override)
        self.assertFalse(hasattr(state, "_forced_poll_pids"))
        self.assertFalse(hasattr(state, "_forced_creation_times"))
        _bind(state, INST_C1, "client", 2001)
        self.assertIsNotNone(state._test_identity_override)
        self.assertEqual(state.resolve_poll_pid(INST_C1, object()), 2001)
        looked = state._lookup_creation_time(2001)
        self.assertEqual(looked, "2026-08-18T00:00:21.000000Z")


class MissingBridgeConfigRegressionTest(unittest.TestCase):
    """The fence turned `<profiles>\\dayz_mcp.json` into a launch
    precondition that nothing in the deploy path establishes.

    Every other test in this module builds the config through `_profile_dir`, so
    the absent branch shipped unexercised. From 2026-08-19T16:50Z every run of
    two projects died on it: prepare -> instance_config_missing ->
    `_settle_failed_launch` -> the worker's bare `_failed()` -> `worker_failed`.
    """

    def _empty_profiles(self) -> Path:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / "profiles"
        directory.mkdir(parents=True)
        return directory

    def test_absent_config_is_seeded_instead_of_refusing_the_launch(self) -> None:
        profiles = self._empty_profiles()
        state = loopback.ServerState("live-key", config_port=8765)
        minted = state.prepare("run-seed", "server", str(profiles))
        self.assertEqual(uuid.UUID(minted).version, 4)
        written = json.loads(
            (profiles / "dayz_mcp.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written["key"], "live-key")
        self.assertEqual(written["url"], "http://127.0.0.1:8765/")
        self.assertEqual(written["pollHz"], 5)
        self.assertEqual(written["instance"], minted)
        # Exactly the installer's shape plus the mint; the bridge reads no more.
        self.assertEqual(set(written), {"url", "key", "pollHz", "instance"})

    def test_seeding_never_creates_the_profiles_directory(self) -> None:
        """A wrong dev_root still fails closed instead of receiving the key."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        absent = Path(temporary.name) / "not-a-profiles-dir"
        state = loopback.ServerState("live-key", config_port=8765)
        with self.assertRaises(instance_fence_mod.BindingPrepareError) as raised:
            state.prepare("run-seed", "server", str(absent))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertFalse(absent.exists())

    def test_without_a_known_port_the_daemon_does_not_invent_a_url(self) -> None:
        profiles = self._empty_profiles()
        state = loopback.ServerState("live-key")
        with self.assertRaises(instance_fence_mod.BindingPrepareError) as raised:
            state.prepare("run-seed", "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertFalse((profiles / "dayz_mcp.json").exists())

    def test_a_boolean_is_not_a_port(self) -> None:
        """True passes isinstance(int) and would seed http://127.0.0.1:True/."""
        profiles = self._empty_profiles()
        state = loopback.ServerState("live-key", config_port=True)
        with self.assertRaises(instance_fence_mod.BindingPrepareError) as raised:
            state.prepare("run-seed", "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertFalse((profiles / "dayz_mcp.json").exists())

    def test_a_deployed_config_keeps_its_own_url_and_key(self) -> None:
        """Seeding fills a gap; it never restamps a config the installer wrote."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        profiles = _profile_dir(Path(temporary.name), "client")
        state = loopback.ServerState("a-different-key", config_port=9999)
        minted = state.prepare("run-keep", "client", str(profiles))
        written = json.loads(
            (profiles / "dayz_mcp.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written["key"], "k")
        self.assertEqual(written["url"], "http://127.0.0.1:8765/")
        self.assertEqual(written["instance"], minted)

    def test_the_launch_path_no_longer_reports_a_prepare_error(self) -> None:
        """`_prepare_instance` is what turns a refusal into a failed launch."""
        profiles = self._empty_profiles()
        state = loopback.ServerState("live-key", config_port=8765)
        minted, error = ProcessLifecycle._prepare_instance(
            SimpleNamespace(bindings=state), "run-x", "server", str(profiles), False
        )
        self.assertIsNone(error)
        self.assertEqual(uuid.UUID(minted).version, 4)

    def test_both_builders_hand_the_port_to_the_state(self) -> None:
        """DZ-R7: prepare() can only seed if its builders pass the port down."""
        from dayz_mcp import daemon as daemon_mod

        server = loopback.LoopbackServer(8899, "live-key")
        self.assertEqual(server.state.config_port, 8899)
        built = daemon_mod.build_server_state(
            SimpleNamespace(), "live-key", port=8899
        )
        self.assertEqual(built.config_port, 8899)
        # The production path passes no port kwarg at all, so the builder
        # must derive it from the config the daemon already binds with.
        derived = daemon_mod.build_server_state(
            SimpleNamespace(port=7777), "live-key"
        )
        self.assertEqual(derived.config_port, 7777)


if __name__ == "__main__":
    unittest.main()

