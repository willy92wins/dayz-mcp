from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import unittest

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


class _LostExistingStartBroker(_Broker):
    def __init__(self, *, materialized: bool = True) -> None:
        super().__init__()
        self.materialized = materialized
        self.run_id: str | None = None
        self.client_launched = False

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        command = request.payload.get("command")
        if command == "status":
            self.requests.append(request)
            processes: list[dict[str, object]] = [
                {"pid": 101, "role": "server"}
            ]
            if self.client_launched:
                processes.append({"pid": 202, "role": "client"})
            return {
                "runs": [
                    {
                        "run_id": self.run_id,
                        "state": "RUNNING",
                        "processes": processes,
                    }
                ]
            }
        if command == "start":
            payload = json.loads(request.stdin)
            if payload.get("role") == "server":
                self.run_id = request.payload["run_id"]
            elif payload.get("role") == "client":
                self.requests.append(request)
                self.client_launched = self.materialized
                return {"ok": False, "error": "broker_child_failed"}
        return await super().invoke(frame)


class _RejectingStartBroker(_Broker):
    def __init__(self, error: str, *, role: str | None = None) -> None:
        super().__init__()
        self.error = error
        self.role = role

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        if request.payload.get("command") == "start":
            payload = json.loads(request.stdin)
            if self.role is None or payload.get("role") == self.role:
                self.requests.append(request)
                return {"error": self.error}
        return await super().invoke(frame)


def _raw(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "version": 1,
        "dev_root": POLICY.dev_root,
        "mod": POLICY.mod,
        "mode": "server",
    }
    value.update(overrides)
    return json.dumps(value).encode("utf-8")


class DayzTestWorkerTests(unittest.TestCase):
    def _run(
        self,
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

    def test_server_start_uses_recoverable_ids_then_ack_without_secret_fields(self) -> None:
        broker = _Broker()
        result = self._run(_raw(), broker)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.run_id, "12345678-1234-4234-8234-1234567890ab")
        self.assertEqual(
            [(item.kind, item.payload.get("command")) for item in broker.requests],
            [
                (native_broker_protocol.BrokerKind.LIFECYCLE_CLI, "start"),
                (native_broker_protocol.BrokerKind.LIFECYCLE_CLI, "ack"),
            ],
        )
        start = json.loads(broker.requests[0].stdin)
        self.assertEqual(start["new_run_id"], result.run_id)
        self.assertEqual(start["launch_operation_id"], "87654321-4321-4321-8321-ba0987654321")
        core = dict(start)
        supplied_hash = core.pop("launch_request_sha256")
        expected_hash = hashlib.sha256(
            json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(supplied_hash, expected_hash)
        self.assertEqual(start["argv"][0], RUNTIME.diag_executable)
        serialized = b"".join(item.stdin for item in broker.requests)
        self.assertNotIn(b"lease", serialized.lower())
        self.assertNotIn(b"identity", serialized.lower())

    def test_new_start_retries_same_recoverable_request_after_lost_response(self) -> None:
        broker = _Broker()
        broker.lose_first_new_start_response = True

        result = self._run(_raw(), broker)

        self.assertEqual(result.run_id, "12345678-1234-4234-8234-1234567890ab")
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests],
            ["start", "start", "ack"],
        )
        self.assertEqual(broker.requests[0].stdin, broker.requests[1].stdin)

    def test_pre_admission_active_run_exists_is_single_attempt_without_cleanup(self) -> None:
        broker = _RejectingStartBroker("active_run_exists")
        broker.fail_stop = True

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._run(_raw(), broker)

        self.assertEqual(raised.exception.code, "active_run_exists")
        self.assertIsNone(raised.exception.run_id)
        self.assertFalse(raised.exception.cleanup_degraded)
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests],
            ["start"],
        )

    def test_unlisted_start_rejection_keeps_conservative_cleanup(self) -> None:
        broker = _RejectingStartBroker("retail_quarantine")
        broker.fail_stop = True

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._run(_raw(), broker)

        self.assertEqual(raised.exception.code, "worker_failed")
        self.assertEqual(
            raised.exception.run_id,
            "12345678-1234-4234-8234-1234567890ab",
        )
        self.assertTrue(raised.exception.cleanup_degraded)
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests],
            ["start", "start", "stop"],
        )

    def test_post_admission_rejection_keeps_created_run_cleanup(self) -> None:
        broker = _RejectingStartBroker("active_run_exists", role="client")
        broker.fail_stop = True

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._run(_raw(mode="all"), broker)

        self.assertEqual(raised.exception.code, "active_run_exists")
        self.assertEqual(
            raised.exception.run_id,
            "12345678-1234-4234-8234-1234567890ab",
        )
        self.assertTrue(raised.exception.cleanup_degraded)
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests],
            ["start", "ack", "start", "stop"],
        )

    def test_build_auto_packonly_then_all_starts_server_waits_and_extends_client(self) -> None:
        broker = _Broker()
        result = self._run(
            _raw(
                mode="all",
                build=True,
                clean=True,
                source=r"P:\ExampleMod\BuIlD_SrC",
            ),
            broker,
            has_assets=False,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            [(item.kind, item.payload.get("command")) for item in broker.requests],
            [
                (native_broker_protocol.BrokerKind.ADDON_BUILDER, None),
                (native_broker_protocol.BrokerKind.LIFECYCLE_CLI, "start"),
                (native_broker_protocol.BrokerKind.LIFECYCLE_CLI, "ack"),
                (native_broker_protocol.BrokerKind.LIFECYCLE_CLI, "start"),
            ],
        )
        addon = broker.requests[0].payload
        self.assertIs(addon["clear"], True)
        self.assertIs(addon["pack_only"], True)
        client = json.loads(broker.requests[-1].stdin)
        self.assertEqual(client["run_id"], result.run_id)
        self.assertNotIn("new_run_id", client)
        self.assertEqual(client["role"], "client")

    def test_build_without_required_basename_accepts_any_source(self) -> None:
        policy = dayz_test_request.RequestProjectPolicy(
            mod="Other_PC",
            dev_root=r"P:\Other_PC_Suite",
            default_source=r"P:\Other_PC",
            default_base_mods=POLICY.default_base_mods,
            mission_roots=POLICY.mission_roots,
            mod_roots=POLICY.mod_roots,
        )
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=policy.dev_root,
            mod=policy.mod,
            diag_executable=RUNTIME.diag_executable,
            game_directory=RUNTIME.game_directory,
            mission_aliases=RUNTIME.mission_aliases,
            mods_root=RUNTIME.mods_root,
            build_temp_root=RUNTIME.build_temp_root,
            build_source_basename=None,
        )
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(
                dev_root=policy.dev_root,
                mod=policy.mod,
                build=True,
                clean=True,
            ),
            policies=(policy,),
        )
        broker = _Broker()

        result = asyncio.run(
            dayz_test_worker.execute_dayz_test_worker(
                parsed.canonical_bytes,
                request_sha256=parsed.sha256,
                request_policies=(policy,),
                runtime_policy=runtime,
                broker=broker,
                has_binarizable_assets=lambda _source: False,
            )
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            broker.requests[0].kind,
            native_broker_protocol.BrokerKind.ADDON_BUILDER,
        )

    def test_required_basename_rejects_other_sources_before_asset_scan_or_broker(self) -> None:
        runtime = dataclasses.replace(RUNTIME, build_source_basename="build_src")
        for source in (
            r"P:\ExampleMod",
            r"P:\ExampleMod\evilbuild_src",
            r"P:\ExampleMod\build_src\child",
        ):
            with self.subTest(source=source):
                broker = _Broker()
                parsed = dayz_test_request.parse_dayz_test_request(
                    _raw(build=True, clean=True, source=source), policies=(POLICY,)
                )
                asset_scans: list[str] = []

                def record_asset_scan(scanned_source: str) -> bool:
                    asset_scans.append(scanned_source)
                    return True

                with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                    asyncio.run(
                        dayz_test_worker.execute_dayz_test_worker(
                            parsed.canonical_bytes,
                            request_sha256=parsed.sha256,
                            request_policies=(POLICY,),
                            runtime_policy=runtime,
                            broker=broker,
                            has_binarizable_assets=record_asset_scan,
                        )
                    )

                self.assertEqual(raised.exception.code, "build_source_unavailable")
                self.assertEqual(asset_scans, [])
                self.assertEqual(broker.requests, [])

    def test_required_basename_accepts_matching_source_case_insensitively(self) -> None:
        runtime = dataclasses.replace(RUNTIME, build_source_basename="build_src")
        for source in (r"P:\ExampleMod\build_src", r"P:\ExampleMod\BUILD_SRC"):
            with self.subTest(source=source):
                broker = _Broker()
                parsed = dayz_test_request.parse_dayz_test_request(
                    _raw(build=True, clean=True, source=source), policies=(POLICY,)
                )
                result = asyncio.run(
                    dayz_test_worker.execute_dayz_test_worker(
                        parsed.canonical_bytes,
                        request_sha256=parsed.sha256,
                        request_policies=(POLICY,),
                        runtime_policy=runtime,
                        broker=broker,
                        has_binarizable_assets=lambda _source: False,
                    )
                )
                self.assertEqual(result.exit_code, 0)
                self.assertEqual(
                    broker.requests[0].kind,
                    native_broker_protocol.BrokerKind.ADDON_BUILDER,
                )

    def test_runtime_with_invalid_build_source_basename_is_rejected(self) -> None:
        for invalid in ("", "build\\src", "build/src", ".."):
            with self.subTest(value=invalid):
                runtime = dataclasses.replace(RUNTIME, build_source_basename=invalid)
                parsed = dayz_test_request.parse_dayz_test_request(
                    _raw(preflight=True), policies=(POLICY,)
                )
                with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                    asyncio.run(
                        dayz_test_worker.execute_dayz_test_worker(
                            parsed.canonical_bytes,
                            request_sha256=parsed.sha256,
                            request_policies=(POLICY,),
                            runtime_policy=runtime,
                            broker=_Broker(),
                        )
                    )
                self.assertEqual(raised.exception.code, "runtime_policy_invalid")

    def test_preflight_rejects_alias_missing_from_project_runtime(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(mission="lfheli", preflight=True), policies=(POLICY,)
        )
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            asyncio.run(
                dayz_test_worker.execute_dayz_test_worker(
                    parsed.canonical_bytes,
                    request_sha256=parsed.sha256,
                    request_policies=(POLICY,),
                    runtime_policy=RUNTIME,
                    broker=_Broker(),
                )
            )
        self.assertEqual(raised.exception.code, "runtime_policy_invalid")

    def test_preflight_accepts_alias_present_in_project_runtime(self) -> None:
        runtime = dataclasses.replace(
            RUNTIME,
            mission_aliases=RUNTIME.mission_aliases
            + (
                (
                    "lfheli",
                    r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\LFHeli.chernarusplus",
                ),
            ),
        )
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(mission="lfheli", preflight=True), policies=(POLICY,)
        )
        broker = _Broker()
        result = asyncio.run(
            dayz_test_worker.execute_dayz_test_worker(
                parsed.canonical_bytes,
                request_sha256=parsed.sha256,
                request_policies=(POLICY,),
                runtime_policy=runtime,
                broker=broker,
            )
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(broker.requests, [])

    def test_lost_existing_start_response_recovers_only_from_one_role_pid(self) -> None:
        recovered = _LostExistingStartBroker()
        result = self._run(_raw(mode="all"), recovered)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            [item.payload.get("command") for item in recovered.requests],
            ["start", "ack", "start", "status"],
        )
        self.assertTrue(
            all(
                item.payload.get("run_id") == result.run_id
                for item in recovered.requests
                if item.payload.get("command") == "status"
            )
        )

        ambiguous = _LostExistingStartBroker(materialized=False)
        with self.assertRaisesRegex(dayz_test_worker.DayzTestWorkerError, "worker_failed"):
            self._run(_raw(mode="all"), ambiguous)
        self.assertEqual(
            [item.payload.get("command") for item in ambiguous.requests],
            ["start", "ack", "start", "status", "stop"],
        )

    def test_client_adopts_exact_run_then_extends_and_kill_only_stops_exact_run(self) -> None:
        run_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        broker = _Broker()
        result = self._run(_raw(mode="client", run_id=run_id), broker)
        self.assertEqual(result.run_id, run_id)
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests],
            ["adopt", "start"],
        )
        client = json.loads(broker.requests[-1].stdin)
        self.assertIn("-noPause", client["argv"])

        killer = _Broker()
        killed = self._run(_raw(mode="offline", kill=True, run_id=run_id), killer)
        self.assertEqual(killed.exit_code, 0)
        self.assertEqual(
            [item.payload.get("command") for item in killer.requests],
            ["adopt", "stop"],
        )
        self.assertTrue(
            all(item.payload["run_id"] == run_id for item in killer.requests)
        )

        lost_adopt = _Broker()
        lost_adopt.lose_adopt_response = True
        recovered = self._run(
            _raw(mode="offline", kill=True, run_id=run_id), lost_adopt
        )
        self.assertEqual(recovered.run_id, run_id)
        self.assertEqual(
            [item.payload.get("command") for item in lost_adopt.requests],
            ["adopt", "stop"],
        )

    def test_kill_rejection_is_closed_and_failed_stop_retains_run(self) -> None:
        run_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        not_adoptable = _Broker()
        not_adoptable.fail_adopt = True
        not_adoptable.fail_stop = True
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as rejected:
            self._run(
                _raw(mode="offline", kill=True, run_id=run_id), not_adoptable
            )
        self.assertEqual(rejected.exception.code, "run_not_adoptable")
        self.assertFalse(rejected.exception.cleanup_degraded)
        self.assertEqual(
            [item.payload.get("command") for item in not_adoptable.requests],
            ["adopt", "stop"],
        )

        failed_stop = _Broker()
        failed_stop.fail_stop = True
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as failed:
            self._run(_raw(mode="offline", kill=True, run_id=run_id), failed_stop)
        self.assertEqual(failed.exception.code, "run_stop_failed")
        self.assertTrue(failed.exception.cleanup_degraded)
        self.assertEqual(failed.exception.run_id, run_id)

    def test_preflight_has_zero_child_and_ack_failure_stops_only_new_unacked_run(self) -> None:
        preflight = _Broker()
        result = self._run(
            _raw(mode="all", preflight=True, build=True, clean=True), preflight
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(preflight.requests, [])

        failing = _Broker()
        failing.fail_ack = True
        with self.assertRaisesRegex(dayz_test_worker.DayzTestWorkerError, "worker_failed"):
            self._run(_raw(), failing)
        self.assertEqual(
            [item.payload.get("command") for item in failing.requests],
            ["start", "ack", "stop"],
        )
        self.assertEqual(
            failing.requests[-1].payload["run_id"],
            failing.requests[0].payload["run_id"],
        )

    def test_cancelled_new_start_or_ack_stops_the_preassigned_run_and_propagates(self) -> None:
        for cancellation_point, expected in (
            ("cancel_start", ["start", "stop"]),
            ("cancel_ack", ["start", "ack", "stop"]),
        ):
            with self.subTest(cancellation_point=cancellation_point):
                broker = _Broker()
                setattr(broker, cancellation_point, True)
                with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                    self._run(_raw(), broker)
                self.assertEqual(raised.exception.code, "operation_cancelled")
                self.assertFalse(raised.exception.cleanup_degraded)
                self.assertEqual(
                    [item.payload.get("command") for item in broker.requests],
                    expected,
                )
                self.assertEqual(
                    broker.requests[-1].payload.get("run_id"),
                    "12345678-1234-4234-8234-1234567890ab",
                )
    def test_all_failure_after_ack_stops_only_the_run_created_by_this_worker(self) -> None:
        readiness_failure = _Broker()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._run(
                _raw(mode="all"),
                readiness_failure,
                readiness_result=dayz_test_readiness.ReadinessResult(
                    ready=False,
                    error_code="readiness_udp_foreign_owner",
                ),
            )
        self.assertEqual(
            raised.exception.code,
            "readiness_udp_foreign_owner",
        )
        self.assertIsNone(raised.exception.run_id)
        self.assertFalse(raised.exception.cleanup_degraded)
        self.assertEqual(
            [item.payload.get("command") for item in readiness_failure.requests],
            ["start", "ack", "stop"],
        )

        client_failure = _Broker()
        client_failure.fail_client_start = True
        with self.assertRaisesRegex(dayz_test_worker.DayzTestWorkerError, "worker_failed"):
            self._run(_raw(mode="all"), client_failure)
        self.assertEqual(
            [item.payload.get("command") for item in client_failure.requests],
            ["start", "ack", "start", "stop"],
        )

    def test_readiness_codes_are_closed_and_legacy_fallback_remains(self) -> None:
        self.assertEqual(
            dayz_test_readiness.READINESS_ERROR_CODES
            & dayz_test_worker.WORKER_ERROR_CODES,
            dayz_test_readiness.READINESS_ERROR_CODES,
        )
        self.assertIn("readiness_failed", dayz_test_worker.WORKER_ERROR_CODES)

    def test_malformed_or_missing_readiness_result_uses_legacy_fallback(self) -> None:
        for label, result, provide in (
            ("missing", None, False),
            ("bool", True, True),
            ("object", object(), True),
        ):
            with self.subTest(label=label):
                broker = _Broker()
                with self.assertRaises(
                    dayz_test_worker.DayzTestWorkerError
                ) as raised:
                    self._run(
                        _raw(mode="all"),
                        broker,
                        readiness_result=result,
                        provide_readiness=provide,
                    )
                self.assertEqual(raised.exception.code, "readiness_failed")
                self.assertFalse(raised.exception.cleanup_degraded)
                self.assertIsNone(raised.exception.run_id)
                self.assertEqual(
                    [
                        item.payload.get("command")
                        for item in broker.requests
                    ],
                    ["start", "ack", "stop"],
                )

    def test_readiness_failure_with_failed_cleanup_retains_exact_new_run(self) -> None:
        broker = _Broker()
        broker.fail_stop = True

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._run(
                _raw(mode="all"),
                broker,
                readiness_result=dayz_test_readiness.ReadinessResult(
                    ready=False,
                    error_code="readiness_server_pid_dead",
                ),
            )

        self.assertEqual(raised.exception.code, "readiness_server_pid_dead")
        self.assertTrue(raised.exception.cleanup_degraded)
        self.assertEqual(
            raised.exception.run_id,
            "12345678-1234-4234-8234-1234567890ab",
        )
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests],
            ["start", "ack", "stop"],
        )

    def test_readiness_cancellation_stops_new_run_and_becomes_cancelled(self) -> None:
        broker = _Broker()

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._run(
                _raw(mode="all"),
                broker,
                readiness_result=asyncio.CancelledError(),
            )

        self.assertEqual(raised.exception.code, "operation_cancelled")
        self.assertFalse(raised.exception.cleanup_degraded)
        self.assertIsNone(raised.exception.run_id)
        self.assertEqual(
            [item.payload.get("command") for item in broker.requests],
            ["start", "ack", "stop"],
        )

    def test_failed_cleanup_retains_exact_new_run_and_marks_degraded(self) -> None:
        broker = _Broker()
        broker.fail_ack = True
        broker.fail_stop = True

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._run(_raw(), broker)

        self.assertEqual(raised.exception.code, "worker_failed")
        self.assertTrue(raised.exception.cleanup_degraded)
        self.assertEqual(
            raised.exception.run_id,
            "12345678-1234-4234-8234-1234567890ab",
        )

    def test_worker_repeats_request_hash_and_has_no_launch_surface(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(_raw(), policies=(POLICY,))
        with self.assertRaisesRegex(dayz_test_worker.DayzTestWorkerError, "request_integrity_failed"):
            asyncio.run(
                dayz_test_worker.execute_dayz_test_worker(
                    parsed.canonical_bytes,
                    request_sha256="0" * 64,
                    request_policies=(POLICY,),
                    runtime_policy=RUNTIME,
                    broker=_Broker(),
                )
            )
        source = inspect.getsource(dayz_test_worker)
        for forbidden in ("subprocess", "multiprocessing", "ctypes", "os.system", "os.spawn"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
