"""Shared ExampleMod request/runtime policies for dayz-test worker tests.

Moved verbatim from test_dayz_test_worker.py so new tests stop importing
that test module (review 2026-09-25, T0; CI shard 1/4 on #365).

_Broker, _raw and run_worker (the body of DayzTestWorkerTests._run) moved
verbatim too, so test_starting_spawn_window stops importing the test module
(CrossTestImportRatchetTest, CI shard 1/4 on #368).
"""
from __future__ import annotations

import asyncio
import json

from dayz_mcp import (
    dayz_test_readiness,
    dayz_test_request,
    dayz_test_worker,
    native_broker_protocol,
)


POLICY = dayz_test_request.RequestProjectPolicy(
    mod="ExampleMod",
    dev_root=r"P:\ExampleMod_Suite",
    default_source=r"P:\ExampleMod",
    default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
    mission_roots=(r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions",),
    mod_roots=(r"P:\Mods",),
)
RUNTIME = dayz_test_worker.WorkerRuntimePolicy(
    dev_root=POLICY.dev_root,
    mod="ExampleMod",
    diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
    game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
    mission_aliases=(
        ("chernarus", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.chernarusplus"),
        ("livonia", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.enoch"),
        ("sakhal", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.sakhal"),
    ),
    mods_root=r"P:\Mods",
    build_temp_root=r"P:\temp",
    build_source_basename=None,
)


class _Broker:
    def __init__(self) -> None:
        self.requests: list[native_broker_protocol.BrokerRequest] = []
        self.current_run_id: str | None = None
        self.processes: list[dict[str, object]] = []
        self.fail_ack = False
        self.fail_client_start = False
        self.cancel_start = False
        self.cancel_ack = False
        self.fail_adopt = False
        self.fail_stop = False
        self.lose_first_new_start_response = False
        self.lose_adopt_response = False

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        self.requests.append(request)
        command = request.payload.get("command")
        if command == "status":
            return {
                "runs": [
                    {
                        "run_id": self.current_run_id,
                        "state": "RUNNING",
                        "processes": list(self.processes),
                    }
                ]
            }
        if command == "start" and self.cancel_start:
            self.cancel_start = False
            raise asyncio.CancelledError
        if command == "ack" and self.cancel_ack:
            self.cancel_ack = False
            raise asyncio.CancelledError
        if command == "ack" and self.fail_ack:
            raise RuntimeError("secret remote failure")
        if command == "adopt" and self.fail_adopt:
            return {"ok": False, "error": "run_not_adoptable"}
        if command == "adopt":
            self.current_run_id = request.payload["run_id"]
        if command == "adopt" and self.lose_adopt_response:
            self.lose_adopt_response = False
            return {"ok": False, "error": "broker_child_failed"}
        if command == "stop" and self.fail_stop:
            return {"ok": False, "error": "run_stop_failed"}
        if (
            command == "start"
            and self.lose_first_new_start_response
            and "new_run_id" in json.loads(request.stdin)
        ):
            self.lose_first_new_start_response = False
            return {"ok": False, "error": "broker_child_failed"}
        if command == "start" and self.fail_client_start:
            payload = json.loads(request.stdin)
            if payload.get("role") == "client":
                return {"ok": False}
        if command == "start":
            payload = json.loads(request.stdin)
            self.current_run_id = request.payload["run_id"]
            self.processes.append(
                {"pid": 100 + len(self.processes), "role": payload["role"]}
            )
        if command in {"start", "adopt"}:
            return {"ok": True, "state": "RUNNING", "run_id": request.payload["run_id"]}
        if command == "stop":
            return {"ok": True, "state": "EXITED", "run_id": request.payload["run_id"]}
        if command == "ack":
            return {"ok": True, "state": "RUNNING", "run_id": request.payload["run_id"]}
        if request.kind is native_broker_protocol.BrokerKind.ADDON_BUILDER:
            return {"ok": True, "exit_code": 0, "pbo_size": 8192}
        return {"ok": True}


def _raw(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "version": 1,
        "dev_root": POLICY.dev_root,
        "mod": POLICY.mod,
        "mode": "server",
    }
    value.update(overrides)
    return json.dumps(value).encode("utf-8")


def run_worker(
    raw: bytes,
    broker: _Broker,
    *,
    readiness_result: object | None = None,
    provide_readiness: bool = True,
    has_assets: bool = True,
) -> dayz_test_worker.WorkerResult:
    parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(POLICY,))
    ids = iter(
        (
            "12345678-1234-4234-8234-1234567890ab",
            "87654321-4321-4321-8321-ba0987654321",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
    )

    async def readiness(_run_id: str, _port: int, _timeout: int) -> object:
        if isinstance(readiness_result, BaseException):
            raise readiness_result
        if readiness_result is not None:
            return readiness_result
        return dayz_test_readiness.ReadinessResult(
            ready=True,
            error_code=None,
        )

    return asyncio.run(
        dayz_test_worker.execute_dayz_test_worker(
            parsed.canonical_bytes,
            request_sha256=parsed.sha256,
            request_policies=(POLICY,),
            runtime_policy=RUNTIME,
            broker=broker,
            id_fn=lambda: next(ids),
            readiness_probe=readiness if provide_readiness else None,
            has_binarizable_assets=lambda _source: has_assets,
        )
    )
