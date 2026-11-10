"""Remote client-only launch without a local run_id (F3 r2 blocker).

The request parser from #365 admits ``mode=client`` without ``run_id`` when
``connect_host`` is not loopback (a laptop client joining a server on another
machine). Before this fix the worker still refused that request with a bare
``worker_failed`` before calling the broker, and ``assess_preflight`` returned
a clean preflight for it. These tests drive the real worker with a fake broker
and, for admission, a real ``ProcessLifecycle`` with fake launcher and guard.
No DayZ process starts.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import dayz_test_request, dayz_test_worker, native_broker_protocol
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.dayz_test_worker_helpers import POLICY, RUNTIME
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    IDENTITY_A,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate


REMOTE_HOST = "192.168.1.34"
RUN_ID = "12345678-1234-4234-8234-1234567890ab"
OPERATION_ID = "87654321-4321-4321-8321-ba0987654321"


def _raw(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "version": 3,
        "dev_root": POLICY.dev_root,
        "mod": POLICY.mod,
        "mode": "client",
        "connect_host": REMOTE_HOST,
    }
    value.update(overrides)
    return json.dumps(value).encode("utf-8")


def _ids():
    values = iter(
        (
            RUN_ID,
            OPERATION_ID,
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
    )
    return lambda: next(values)


class _FakeBroker:
    """Answers lifecycle frames like the daemon would for a fresh client run."""

    def __init__(self, *, fail_ack: bool = False) -> None:
        self.requests: list[native_broker_protocol.BrokerRequest] = []
        self.fail_ack = fail_ack

    def commands(self) -> list[str]:
        return [str(item.payload.get("command")) for item in self.requests]

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        self.requests.append(request)
        command = request.payload.get("command")
        run_id = request.payload.get("run_id")
        if command == "ack" and self.fail_ack:
            return {"ok": False, "error": "launch_identity_conflict"}
        if command in {"start", "ack"}:
            return {"ok": True, "state": "RUNNING", "run_id": run_id}
        if command == "stop":
            return {"ok": True, "state": "EXITED", "run_id": run_id}
        return {"ok": False, "error": "unexpected_command"}


class _LifecycleBroker:
    """Routes the worker's lifecycle frames into a real ProcessLifecycle."""

    def __init__(self, lifecycle: ProcessLifecycle, token: str) -> None:
        self.lifecycle = lifecycle
        self.token = token
        self.requests: list[native_broker_protocol.BrokerRequest] = []

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        self.requests.append(request)
        command = request.payload.get("command")
        run_id = request.payload.get("run_id")
        if command == "start":
            return self.lifecycle.start_run(
                IDENTITY_A, self.token, json.loads(request.stdin.decode("utf-8"))
            )
        if command == "ack":
            return self.lifecycle.ack_run(
                IDENTITY_A,
                self.token,
                run_id,
                request.payload.get("launch_operation_id"),
            )
        if command == "stop":
            return self.lifecycle.stop_run(IDENTITY_A, self.token, run_id)
        if command == "adopt":
            return self.lifecycle.adopt_run(IDENTITY_A, self.token, run_id)
        return {"ok": False, "error": "unexpected_command"}


def _execute(
    raw: bytes,
    broker: object,
    runtime: dayz_test_worker.WorkerRuntimePolicy = RUNTIME,
) -> dayz_test_worker.WorkerResult:
    parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(POLICY,))
    return asyncio.run(
        dayz_test_worker.execute_dayz_test_worker(
            parsed.canonical_bytes,
            request_sha256=parsed.sha256,
            request_policies=(POLICY,),
            runtime_policy=runtime,
            broker=broker,
            id_fn=_ids(),
        )
    )


class RemoteClientWorkerLaunchTest(unittest.TestCase):
    def test_remote_client_without_run_id_creates_client_run(self) -> None:
        broker = _FakeBroker()

        result = _execute(_raw(), broker)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.run_id, RUN_ID)
        self.assertEqual(broker.commands(), ["start", "ack"])
        start = json.loads(broker.requests[0].stdin)
        self.assertEqual(start["role"], "client")
        self.assertEqual(start["new_run_id"], RUN_ID)
        self.assertEqual(start["launch_operation_id"], OPERATION_ID)
        self.assertNotIn("run_id", start)
        # A client attaches to the remote server's world: no local storage seal.
        self.assertNotIn("storage_seal", start)
        self.assertIn("-connect=" + REMOTE_HOST, start["argv"])
        core = dict(start)
        supplied = core.pop("launch_request_sha256")
        self.assertEqual(
            supplied,
            hashlib.sha256(
                json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )
        ack = broker.requests[1].payload
        self.assertEqual(ack["run_id"], RUN_ID)
        self.assertEqual(ack["launch_operation_id"], OPERATION_ID)

    def test_remote_client_failed_ack_stops_the_created_run(self) -> None:
        broker = _FakeBroker(fail_ack=True)

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            _execute(_raw(), broker)

        self.assertEqual(broker.commands(), ["start", "ack", "stop"])
        self.assertEqual(broker.requests[2].payload["run_id"], RUN_ID)
        self.assertEqual(raised.exception.attempt_run_id, RUN_ID)
        self.assertEqual(raised.exception.launch_operation_id, OPERATION_ID)
        self.assertFalse(raised.exception.cleanup_degraded)

    def test_loopback_client_without_run_id_never_reaches_the_broker(self) -> None:
        for overrides in (
            {"connect_host": "127.0.0.1"},
            {"connect_host": "localhost"},
            {"connect_host": "::1"},
        ):
            with self.subTest(**overrides):
                raw = _raw(**overrides)
                broker = _FakeBroker()
                with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                    asyncio.run(
                        dayz_test_worker.execute_dayz_test_worker(
                            raw,
                            request_sha256=hashlib.sha256(raw).hexdigest(),
                            request_policies=(POLICY,),
                            runtime_policy=RUNTIME,
                            broker=broker,
                            id_fn=_ids(),
                        )
                    )
                self.assertEqual(raised.exception.code, "request_integrity_failed")
                self.assertEqual(broker.requests, [])


class RemoteClientLifecycleAdmissionTest(unittest.TestCase):
    """Point 2: the daemon admits start role=client + new_run_id, empty store."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._saved_shared_root = os.environ.get("DAYZ_MCP_SHARED_ROOT")
        os.environ["DAYZ_MCP_SHARED_ROOT"] = str(self.root / "shared-root")
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe", "DayZServer_x64.exe"):
            (self.game / name).write_bytes(b"")
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
        self.token = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "unused-run-id",
        )
        self.runtime = dataclasses.replace(
            RUNTIME,
            diag_executable=str(self.game / "DayZDiag_x64.exe"),
            game_directory=str(self.game),
        )

    def tearDown(self) -> None:
        if self._saved_shared_root is None:
            os.environ.pop("DAYZ_MCP_SHARED_ROOT", None)
        else:
            os.environ["DAYZ_MCP_SHARED_ROOT"] = self._saved_shared_root
        self.temporary.cleanup()

    def test_remote_client_run_is_admitted_acknowledged_and_stoppable(self) -> None:
        expected = process(self.launcher.pid, "client")
        self.guard.snapshots[self.launcher.pid] = snapshot(expected)
        self.assertEqual(self.store.list_runs(), [])
        broker = _LifecycleBroker(self.lifecycle, self.token)

        result = _execute(_raw(), broker, self.runtime)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.run_id, RUN_ID)
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests], ["start", "ack"]
        )
        self.assertEqual(len(self.launcher.calls), 1)
        self.assertIn("-connect=" + REMOTE_HOST, self.launcher.calls[0][0])
        stored = self.store.get(RUN_ID)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.state, "RUNNING")
        self.assertEqual([record.role for record in stored.processes], ["client"])
        self.assertEqual(stored.launch_operation_id, OPERATION_ID)
        self.assertTrue(stored.launch_acknowledged)

        # created_run_id is the run a later stop targets.
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token, RUN_ID)
        self.assertTrue(stopped.get("ok"), stopped)
        self.assertEqual(stopped.get("run_id"), RUN_ID)


class RemoteClientPreflightCoherenceTest(unittest.TestCase):
    """Point 3: assess_preflight gives the same verdict as the launch."""

    def test_preflight_and_launch_agree_for_remote_client(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(_raw(), policies=(POLICY,))
        host = dayz_test_worker.assess_preflight(parsed.payload, RUNTIME, None)
        self.assertIsNone(host)

        preflight_broker = _FakeBroker()
        dry = _execute(_raw(preflight=True), preflight_broker)
        self.assertEqual(dry.exit_code, 0)
        self.assertEqual(preflight_broker.requests, [])

        launch = _execute(_raw(), _FakeBroker())
        self.assertEqual(launch.exit_code, 0)
        self.assertEqual(launch.run_id, RUN_ID)

    def test_preflight_rejects_loopback_client_without_run_like_the_launch(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(_raw(), policies=(POLICY,))
        for variant in ("127.0.0.1", "localhost", "[::1]", None):
            with self.subTest(connect_host=variant):
                payload = dict(parsed.payload)
                if variant is None:
                    payload.pop("connect_host", None)
                else:
                    payload["connect_host"] = variant
                with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                    dayz_test_worker.assess_preflight(payload, RUNTIME, None)
                self.assertEqual(raised.exception.code, "worker_failed")

    def test_preflight_keeps_client_with_local_run(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(connect_host="127.0.0.1", run_id=RUN_ID), policies=(POLICY,)
        )
        self.assertIsNone(
            dayz_test_worker.assess_preflight(parsed.payload, RUNTIME, None)
        )


if __name__ == "__main__":
    unittest.main()
