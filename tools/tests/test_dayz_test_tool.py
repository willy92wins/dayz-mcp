from __future__ import annotations

import json
import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import dayz_test_request, dayz_test_worker
from dayz_mcp import dayz_test_tool


RUN_ID = "12345678-1234-4234-8234-1234567890ab"


def _policy(
    *,
    mod: str = "ExampleMod",
    dev_root: str = r"P:\ExampleMod_Suite",
    default_source: str = r"P:\ExampleMod",
    default_base_mods: tuple[str, ...] = ("@CF", "@Dabs Framework"),
) -> dayz_test_request.RequestProjectPolicy:
    return dayz_test_request.RequestProjectPolicy(
        mod=mod,
        dev_root=dev_root,
        default_source=default_source,
        default_base_mods=default_base_mods,
        mission_roots=(dev_root + r"\_server\mpmissions",),
        mod_roots=(r"P:\Mods",),
    )


def _sealed(*policies: dayz_test_request.RequestProjectPolicy) -> tuple[object, ...]:
    return tuple(types.SimpleNamespace(policy=policy) for policy in policies)


def _terminal(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class DayzTestToolRequestTest(unittest.TestCase):
    def test_build_run_request_uses_sealed_project_and_policy_defaults(self) -> None:
        policy = _policy()

        raw, selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="offline",
            mission="chernarus",
        )

        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertIs(selected, policy)
        self.assertEqual(parsed.payload["dev_root"], policy.dev_root)
        self.assertEqual(parsed.payload["source"], policy.default_source)
        self.assertEqual(parsed.payload["base_mods"], list(policy.default_base_mods))
        self.assertEqual(parsed.payload["mode"], "offline")

    def test_build_run_request_rejects_unknown_project_and_public_paths(self) -> None:
        sealed = _sealed(_policy())
        invalid = (
            ({"project": "Unknown", "mode": "offline"}, "bad_project"),
            (
                {
                    "project": "ExampleMod",
                    "mode": "offline",
                    "mission": r"P:\missions\custom.ChernarusPlus",
                },
                "bad_mission",
            ),
            (
                {
                    "project": "ExampleMod",
                    "mode": "offline",
                    "extra_mods": [r"C:\Users\Public\@Outside"],
                },
                "bad_mod",
            ),
            (
                {
                    "project": "ExampleMod",
                    "mode": "offline",
                    "server_mods": [r"folder\@Server"],
                },
                "bad_mod",
            ),
            (
                {
                    "project": "ExampleMod",
                    "mode": "offline",
                    "base_mods": [".."],
                },
                "bad_mod",
            ),
        )
        for arguments, code in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(dayz_test_tool.DayzTestToolError, code):
                    dayz_test_tool.build_run_request(sealed, **arguments)

    def test_build_run_request_delegates_cross_field_validation(self) -> None:
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "bad_dayz_test_request"
        ):
            dayz_test_tool.build_run_request(
                _sealed(_policy()),
                project="ExampleMod",
                mode="client",
            )

    def test_extension_run_must_be_idle_and_match_selected_project(self) -> None:
        policy = _policy()
        valid = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "RUNNING_IDLE",
                    "mod": "@ExampleMod",
                    "profiles": r"P:\ExampleMod_Suite\_client\profiles",
                }
            ]
        }
        run = dayz_test_tool.require_extension_run(valid, policy, RUN_ID)
        self.assertEqual(run["run_id"], RUN_ID)

        for status, code in (
            ({"runs": []}, "run_not_found"),
            (
                {"runs": [{**valid["runs"][0], "state": "RUNNING"}]},
                "run_not_extensible",
            ),
            (
                {"runs": [{**valid["runs"][0], "mod": "@StorageMod"}]},
                "run_project_mismatch",
            ),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(dayz_test_tool.DayzTestToolError, code):
                    dayz_test_tool.require_extension_run(status, policy, RUN_ID)

    def test_stop_resolves_project_only_from_exact_manifest_run(self) -> None:
        utopia = _policy()
        lfv = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        status = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "RUNNING_IDLE",
                    "mod": "@StorageMod",
                    "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
                }
            ]
        }

        selected, run = dayz_test_tool.resolve_stop_run(
            status, _sealed(utopia, lfv), RUN_ID
        )

        self.assertIs(selected, lfv)
        self.assertEqual(run["run_id"], RUN_ID)


class DayzTestTerminalTest(unittest.TestCase):
    def test_parse_success_terminal(self) -> None:
        raw = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": RUN_ID,
            }
        )

        result = dayz_test_tool.parse_worker_terminal(raw, b"", 0)

        self.assertTrue(result.ok)
        self.assertEqual(result.run_id, RUN_ID)
        self.assertIsNone(result.error_code)

    def test_parse_failure_terminal_with_degraded_cleanup(self) -> None:
        raw = _terminal(
            {
                "cleanup_degraded": True,
                "error_code": "readiness_failed",
                "exit_code": 2,
                "ok": False,
                "run_id": RUN_ID,
            }
        )

        result = dayz_test_tool.parse_worker_terminal(raw, b"", 2)

        self.assertFalse(result.ok)
        self.assertTrue(result.cleanup_degraded)
        self.assertEqual(result.run_id, RUN_ID)

    def test_typed_readiness_codes_keep_five_key_terminal_contract(self) -> None:
        readiness_codes = sorted(
            code
            for code in dayz_test_worker.WORKER_ERROR_CODES
            if code.startswith("readiness_") and code != "readiness_failed"
        )
        self.assertEqual(len(readiness_codes), 8)
        for code in readiness_codes:
            with self.subTest(code=code):
                raw = _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": code,
                        "exit_code": 2,
                        "ok": False,
                        "run_id": None,
                    }
                )
                payload = json.loads(raw)
                self.assertEqual(
                    set(payload),
                    {
                        "cleanup_degraded",
                        "error_code",
                        "exit_code",
                        "ok",
                        "run_id",
                    },
                )
                result = dayz_test_tool.parse_worker_terminal(raw, b"", 2)
                self.assertEqual(result.error_code, code)

    def test_terminal_rejects_non_closed_or_inconsistent_payloads(self) -> None:
        valid = {
            "cleanup_degraded": False,
            "error_code": None,
            "exit_code": 0,
            "ok": True,
            "run_id": RUN_ID,
        }
        cases = {
            "stderr": (_terminal(valid), b"unexpected", 0),
            "extra": (_terminal({**valid, "secret": "x"}), b"", 0),
            "noncanonical": (json.dumps(valid, indent=2).encode(), b"", 0),
            "exit_mismatch": (_terminal(valid), b"", 1),
            "success_error": (
                _terminal({**valid, "error_code": "worker_failed"}),
                b"",
                0,
            ),
            "unknown_error": (
                _terminal(
                    {
                        **valid,
                        "ok": False,
                        "exit_code": 2,
                        "error_code": "raw_exception_text",
                        "run_id": None,
                    }
                ),
                b"",
                2,
            ),
            "degraded_without_run": (
                _terminal(
                    {
                        **valid,
                        "ok": False,
                        "exit_code": 2,
                        "error_code": "worker_failed",
                        "cleanup_degraded": True,
                        "run_id": None,
                    }
                ),
                b"",
                2,
            ),
            "failure_run_without_degradation": (
                _terminal(
                    {
                        **valid,
                        "ok": False,
                        "exit_code": 2,
                        "error_code": "worker_failed",
                    }
                ),
                b"",
                2,
            ),
            "oversize": (b"{" + b" " * 4096 + b"}", b"", 2),
            "duplicate": (
                b'{"cleanup_degraded":false,"error_code":null,"exit_code":0,'
                b'"ok":true,"ok":true,"run_id":null}',
                b"",
                0,
            ),
        }
        for label, arguments in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    dayz_test_tool.DayzTestToolError, "terminal_invalid"
                ):
                    dayz_test_tool.parse_worker_terminal(*arguments)

    def test_success_run_id_matches_operation_context(self) -> None:
        success_without_run = dayz_test_tool.WorkerTerminal(False, None, 0, True, None)
        success_with_run = dayz_test_tool.WorkerTerminal(
            False, None, 0, True, RUN_ID
        )

        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "terminal_invalid"
        ):
            dayz_test_tool._validate_terminal_context(
                success_without_run, preflight=False, expected_run_id=None
            )
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "terminal_invalid"
        ):
            dayz_test_tool._validate_terminal_context(
                success_with_run, preflight=True, expected_run_id=None
            )
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "terminal_invalid"
        ):
            dayz_test_tool._validate_terminal_context(
                success_with_run,
                preflight=False,
                expected_run_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            )

    def test_stop_artifact_must_be_derived_from_sealed_project(self) -> None:
        policy = _policy()
        self.assertEqual(
            dayz_test_tool._stop_artifacts(
                policy,
                {"profiles": r"P:\ExampleMod_Suite\_client\profiles"},
            ),
            [r"P:\ExampleMod_Suite\_client\profiles"],
        )
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "lifecycle_status_invalid"
        ):
            dayz_test_tool._stop_artifacts(
                policy, {"profiles": r"P:\Unapproved\profiles"}
            )


class _Opened:
    def __init__(self) -> None:
        self.validated = False

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def validate_native_pe(self) -> None:
        self.validated = True


class _Bundle:
    def __init__(self, sealed_policies: tuple[object, ...]) -> None:
        self.sealed_policies = sealed_policies

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Runtime:
    def __init__(self, lifecycle: dict[str, object] | None = None) -> None:
        self.active_lease_token = None
        self.active_ticket = None
        self.active_operation_id = None
        self.daemon_policy = object()
        self.lifecycle = lifecycle or {"runs": []}
        self.lifecycle_calls = 0
        self.reconcile_calls = 0

    async def lifecycle_status(self) -> dict[str, object]:
        self.lifecycle_calls += 1
        return self.lifecycle

    async def reconcile_idle_session(self) -> dict[str, object]:
        self.reconcile_calls += 1
        return {"reconciled": False}


class DayzTestExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_reports_progress_and_returns_compact_terminal_result(self) -> None:
        policy = _policy()
        opened = _Opened()
        bundle = _Bundle(_sealed(policy))
        runtime = _Runtime()
        progress: list[tuple[str, str | None]] = []

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=(policy,)
            )
            self.assertEqual(parsed.payload["mode"], "offline")
            self.assertIs(kwargs["daemon_policy"], runtime.daemon_policy)
            await kwargs["queue_progress_cb"](0.0, None, "En cola (posición 2)")
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        async def report(stage: str, message: str | None) -> None:
            progress.append((stage, message))

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=opened
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=bundle,
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="offline",
                progress_cb=report,
            )

        self.assertTrue(opened.validated)
        self.assertEqual(
            [stage for stage, _message in progress],
            ["validating", "queued", "executing", "finalizing"],
        )
        self.assertEqual(
            set(result),
            {
                "status",
                "project",
                "mode",
                "run_id",
                "phase",
                "elapsed_s",
                "artifacts_paths",
                "error_code",
                "cleanup_degraded",
            },
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["phase"], "completed")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(
            result["artifacts_paths"],
            [r"P:\ExampleMod_Suite\_client\profiles"],
        )

    async def test_typed_readiness_failure_uses_existing_nine_key_result(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        readiness_code = "readiness_udp_foreign_owner"

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": readiness_code,
                        "exit_code": 2,
                        "ok": False,
                        "run_id": None,
                    }
                ),
            )
            return 2

        with patch.object(
            dayz_test_tool,
            "open_approved_launcher",
            return_value=_Opened(),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
            )

        self.assertEqual(
            set(result),
            {
                "status",
                "project",
                "mode",
                "run_id",
                "phase",
                "elapsed_s",
                "artifacts_paths",
                "error_code",
                "cleanup_degraded",
            },
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], readiness_code)

    async def test_run_rejects_busy_session_before_opening_launcher(self) -> None:
        runtime = _Runtime()
        runtime.active_ticket = "ticket"
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaisesRegex(
                dayz_test_tool.DayzTestToolError, "session_busy"
            ):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="offline"
                )
        opened.assert_not_called()
        self.assertEqual(runtime.reconcile_calls, 1)

    async def test_idle_guard_recovers_stale_local_operation_before_launch(
        self,
    ) -> None:
        runtime = _Runtime()
        runtime.active_lease_token = "stale-local-token"
        runtime.active_operation_id = "11111111-1111-4111-8111-111111111111"

        async def reconcile() -> dict[str, object]:
            runtime.reconcile_calls += 1
            runtime.active_lease_token = None
            runtime.active_operation_id = None
            return {"reconciled": True}

        runtime.reconcile_idle_session = reconcile  # type: ignore[method-assign]

        await dayz_test_tool._require_idle_session(runtime)

        self.assertEqual(runtime.reconcile_calls, 1)
        self.assertIsNone(runtime.active_lease_token)
        self.assertIsNone(runtime.active_operation_id)

    async def test_run_id_is_bound_before_secure_launch(self) -> None:
        policy = _policy()
        runtime = _Runtime(
            {
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "state": "RUNNING_IDLE",
                        "mod": "@StorageMod",
                    }
                ]
            }
        )
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=AsyncMock(),
        ) as launch:
            with self.assertRaisesRegex(
                dayz_test_tool.DayzTestToolError, "run_project_mismatch"
            ):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    mode="offline",
                    run_id=RUN_ID,
                )
        launch.assert_not_awaited()

    async def test_stop_derives_policy_and_builds_adopt_then_stop_request(self) -> None:
        lfv = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        run = {
            "run_id": RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
        }
        runtime = _Runtime({"runs": [run]})

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=(lfv,)
            )
            self.assertIs(parsed.payload["kill"], True)
            self.assertEqual(parsed.payload["run_id"], RUN_ID)
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(lfv)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["project"], "StorageMod")
        self.assertEqual(result["mode"], "stop")
        self.assertEqual(result["artifacts_paths"], [run["profiles"]])

    async def test_stop_reconciles_lost_terminal_response_from_exact_exited_run(self) -> None:
        lfv = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        active = {
            "run_id": RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
        }
        exited = {**active, "state": "EXITED"}
        runtime = _Runtime()
        runtime.lifecycle_status = AsyncMock(
            side_effect=({"runs": [active]}, {"runs": [exited]})
        )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": True,
                        "error_code": "run_stop_failed",
                        "exit_code": 2,
                        "ok": False,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 2

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(lfv)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["phase"], "completed")
        self.assertIsNone(result["error_code"])
        self.assertFalse(result["cleanup_degraded"])


if __name__ == "__main__":
    unittest.main()
