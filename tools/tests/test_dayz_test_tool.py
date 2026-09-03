from __future__ import annotations

import json
import asyncio
import inspect
import os
import re
import types
import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import dayz_test_request, dayz_test_worker
from dayz_mcp import dayz_test_tool
from dayz_mcp import server
from dayz_mcp import steam_preflight
from dayz_mcp.control_client import ControlClientError


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


def _list_projects_path(payload: bytes | BaseException) -> type:
    """Path stand-in so list_project_names never leaves the fixture."""

    class _PolicyPath:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def resolve(self) -> _PolicyPath:
            return self

        @property
        def parents(self) -> tuple[_PolicyPath, _PolicyPath]:
            return (self, self)

        def __truediv__(self, _other: object) -> _PolicyPath:
            return self

        def read_bytes(self) -> bytes:
            if isinstance(payload, BaseException):
                raise payload
            return payload

    return _PolicyPath


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
            extra_mods=["@DayZ_MCP"],
        )

        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertIs(selected, policy)
        self.assertEqual(parsed.payload["dev_root"], policy.dev_root)
        self.assertEqual(parsed.payload["source"], policy.default_source)
        self.assertEqual(parsed.payload["base_mods"], list(policy.default_base_mods))
        self.assertEqual(parsed.payload["mode"], "offline")

    def test_build_run_request_requires_bridge_in_effective_mods(self) -> None:
        policy = _policy()
        sealed = _sealed(policy)

        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                sealed,
                project="ExampleMod",
                mode="offline",
            )
        self.assertEqual(
            caught.exception.code,
            "bridge_mod_missing: add extra_mods=['@DayZ_MCP']",
        )

        for excluded_field in ("base_mods", "server_mods"):
            with self.subTest(excluded_field=excluded_field):
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    dayz_test_tool.build_run_request(
                        sealed,
                        project="ExampleMod",
                        mode="offline",
                        **{excluded_field: ["@DayZ_MCP"]},
                    )
                self.assertEqual(
                    caught.exception.code,
                    "bridge_mod_missing: add extra_mods=['@DayZ_MCP']",
                )

        raw, selected = dayz_test_tool.build_run_request(
            sealed,
            project="ExampleMod",
            mode="offline",
            extra_mods=["@DayZ_MCP"],
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertIs(selected, policy)
        self.assertEqual(parsed.payload["extra_mods"], ["@DayZ_MCP"])

        bridge_policy = _policy(
            mod="DayZ_MCP",
            dev_root=r"P:\DayZ_MCP_dev",
            default_source=r"P:\DayZ_MCP",
        )
        raw, selected = dayz_test_tool.build_run_request(
            _sealed(bridge_policy),
            project="DayZ_MCP",
            mode="offline",
        )
        parsed = dayz_test_request.parse_dayz_test_request(
            raw, policies=(bridge_policy,)
        )
        self.assertIs(selected, bridge_policy)
        self.assertEqual(parsed.payload["mod"], "DayZ_MCP")
        self.assertEqual(parsed.payload["extra_mods"], [])

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
                "bad_dayz_test_request",
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

    def test_build_run_request_accepts_absolute_mission_inside_roots(self) -> None:
        policy = _policy()
        inside = r"P:\ExampleMod_Suite\_server\mpmissions\custom.ChernarusPlus"
        raw, selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="offline",
            mission=inside,
            extra_mods=["@DayZ_MCP"],
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertIs(selected, policy)
        self.assertEqual(parsed.payload["mission"], inside)

    def test_build_run_request_delegates_cross_field_validation(self) -> None:
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "bad_dayz_test_request"
        ):
            dayz_test_tool.build_run_request(
                _sealed(_policy()),
                project="ExampleMod",
                mode="client",
            )

    def test_build_run_request_names_invalid_mode_and_expected_values(self) -> None:
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(_policy()),
                project="ExampleMod",
                mode="not-a-mode",
                extra_mods=["@DayZ_MCP"],
            )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected server|all|client",
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

    def test_stop_resolves_never_started_unacked_exited_run(self) -> None:
        policy = _policy()
        status = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "EXITED",
                    "mod": "@ExampleMod",
                    "profiles": r"P:\ExampleMod_Suite\_client\profiles",
                    "launch_acknowledged": False,
                }
            ]
        }

        selected, run = dayz_test_tool.resolve_stop_run(
            status, _sealed(policy), RUN_ID
        )

        self.assertIs(selected, policy)
        self.assertEqual(run["run_id"], RUN_ID)

    def test_stop_rejects_acked_exited_run(self) -> None:
        status = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "EXITED",
                    "mod": "@ExampleMod",
                    "profiles": r"P:\ExampleMod_Suite\_client\profiles",
                    "launch_acknowledged": True,
                }
            ]
        }
        with self.assertRaisesRegex(dayz_test_tool.DayzTestToolError, "run_not_active"):
            dayz_test_tool.resolve_stop_run(status, _sealed(_policy()), RUN_ID)


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
        # M19: the bridge snapshot the readiness projection reads. Counted so a
        # test can say how many times it was consulted, and on which rows.
        self.bridge_payload: object = {"ready": {"ready": True, "reason": "ready"}}
        self.bridge_calls = 0
        self.bridge_raises = False

    async def bridge_status_payload(self) -> dict[str, object]:
        self.bridge_calls += 1
        if self.bridge_raises:
            raise RuntimeError("snapshot unavailable")
        return self.bridge_payload  # type: ignore[return-value]

    async def lifecycle_status(self) -> dict[str, object]:
        self.lifecycle_calls += 1
        return self.lifecycle

    async def reconcile_idle_session(self) -> dict[str, object]:
        self.reconcile_calls += 1
        return {"reconciled": False}


class DayzTestExecutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        patcher = patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            return_value=steam_preflight.SteamSessionResult(
                error_code=None,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation=steam_preflight.REMEDIATION,
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_run_rejects_missing_bridge_before_secure_launch(self) -> None:
        policy = _policy()
        runtime = _Runtime()

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
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    mode="all",
                )

        launch.assert_not_awaited()
        self.assertEqual(
            caught.exception.code,
            "bridge_mod_missing: add extra_mods=['@DayZ_MCP']",
        )

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
            self.assertEqual(parsed.payload["mode"], "all")
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
                mode="all",
                extra_mods=["@DayZ_MCP"],
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
                "server_alive",
                "client_alive",
                # M19: the readiness triple is always present, null included. A
                # key that appears only on some rows is a key no consumer can
                # branch on.
                "process_alive",
                "bridge_ready",
                "reason",
                "steam_registered_pid",
                "steam_live_pids",
                "remediation",
            },
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["phase"], "completed")
        self.assertEqual(result["run_id"], RUN_ID)
        # M19 call discipline: mode=all, not preflight, terminal ok -> the
        # bridge snapshot is consulted exactly once and the triple is filled
        # from it, never from the PID.
        self.assertEqual(runtime.bridge_calls, 1)
        self.assertIs(result["bridge_ready"], True)
        self.assertEqual(result["reason"], "ready")
        self.assertEqual(
            result["artifacts_paths"],
            [
                r"P:\ExampleMod_Suite\_server\profiles",
                r"P:\ExampleMod_Suite\_client\profiles",
            ],
        )

    async def test_server_mode_never_consults_the_bridge_snapshot(self) -> None:
        """Zero snapshot reads outside client|all.

        The ficha allows exactly one read for a successful non-preflight
        client|all and zero anywhere else. Without this case a build that asks
        on every row stays green, and then the triple no longer means "the
        client's bridge" -- it means whatever the daemon happened to answer.
        Found by a mutant that widened the predicate to every row and survived.
        """

        policy = _policy()
        opened = _Opened()
        bundle = _Bundle(_sealed(policy))
        runtime = _Runtime()

        async def launch(raw_request: bytes, **kwargs: object) -> int:
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
                mode="server",
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(runtime.bridge_calls, 0)
        self.assertIsNone(result["bridge_ready"])
        self.assertIsNone(result["reason"])

    async def test_typed_readiness_failure_uses_the_same_envelope(self) -> None:
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
                extra_mods=["@DayZ_MCP"],
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
                "server_alive",
                "client_alive",
                # M19: the readiness triple is always present, null included. A
                # key that appears only on some rows is a key no consumer can
                # branch on.
                "process_alive",
                "bridge_ready",
                "reason",
                "steam_registered_pid",
                "steam_live_pids",
                "remediation",
            },
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], readiness_code)

    async def test_steam_session_stale_returns_typed_failure_before_launch(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        stale = steam_preflight.SteamSessionResult(
            error_code=steam_preflight.STEAM_SESSION_STALE,
            steam_registered_pid=4321,
            steam_live_pids=(1, 2, 3, 4, 5, 6, 7, 8, 9),
            remediation="restart Steam",
        )
        launch = AsyncMock()

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session", return_value=stale
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                preflight=False,
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["run_id"])
        self.assertEqual(result["phase"], "validating")
        self.assertEqual(result["artifacts_paths"], [])
        self.assertEqual(result["error_code"], steam_preflight.STEAM_SESSION_STALE)
        self.assertEqual(result["steam_registered_pid"], 4321)
        self.assertEqual(result["steam_live_pids"], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(result["remediation"], "restart Steam")
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
                "server_alive",
                "client_alive",
                "process_alive",
                "bridge_ready",
                "reason",
                "steam_registered_pid",
                "steam_live_pids",
                "remediation",
            },
        )

    async def test_steam_evaluator_exception_returns_typed_stale_envelope(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        launch = AsyncMock()

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            side_effect=RuntimeError("sonda P2-F"),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                preflight=False,
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["run_id"])
        self.assertEqual(result["phase"], "validating")
        self.assertEqual(result["artifacts_paths"], [])
        self.assertEqual(result["error_code"], steam_preflight.STEAM_SESSION_STALE)
        self.assertIsNone(result["steam_registered_pid"])
        self.assertEqual(result["steam_live_pids"], [])
        self.assertEqual(result["remediation"], steam_preflight.REMEDIATION)
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
                "server_alive",
                "client_alive",
                "process_alive",
                "bridge_ready",
                "reason",
                "steam_registered_pid",
                "steam_live_pids",
                "remediation",
            },
        )

    async def test_steam_evaluator_keyboardinterrupt_propagates(self) -> None:
        policy = _policy()
        runtime = _Runtime()

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
        ), patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    mode="client",
                    preflight=False,
                    run_id=RUN_ID,
                    extra_mods=["@DayZ_MCP"],
                )

    async def test_steam_session_is_not_consulted_for_preflight_or_server(self) -> None:
        policy = _policy()
        calls: list[int] = []

        def _stale_provider(*_args: object, **_kwargs: object) -> object:
            calls.append(1)
            return steam_preflight.SteamSessionResult(
                error_code=steam_preflight.STEAM_SESSION_STALE,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation="restart Steam",
            )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            parsed = json.loads(_raw_request.decode("utf-8"))
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": None if parsed.get("preflight") else RUN_ID,
                    }
                ),
            )
            return 0

        # preflight=True with mode="client" requires a request run_id
        # (client_requires_run_id) but then expected_run_id is set and the
        # terminal of a preflight run has run_id=None, which
        # _validate_terminal_context rejects (dayz_test_tool.py:527-530).
        # mode="all" is in {client, all}, so it is still the case that WOULD
        # consult Steam; preflight is what has to suppress it.
        for label, arguments in (
            ("preflight", {"mode": "all", "preflight": True}),
            ("server", {"mode": "server", "preflight": False}),
        ):
            calls.clear()
            runtime = _Runtime()
            with self.subTest(label=label), patch.object(
                dayz_test_tool, "open_approved_launcher", return_value=_Opened()
            ), patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                return_value=_Bundle(_sealed(policy)),
            ), patch.object(
                dayz_test_tool.secure_launcher,
                "execute_secure_launcher_request",
                side_effect=launch,
            ), patch.object(
                dayz_test_tool, "evaluate_steam_session", side_effect=_stale_provider
            ):
                result = await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    extra_mods=["@DayZ_MCP"],
                    **arguments,
                )
                self.assertEqual(calls, [])
                self.assertEqual(result["status"], "succeeded")

    async def test_run_fails_when_client_pid_is_already_dead(self) -> None:
        policy = _policy()
        runtime = _Runtime(
            lifecycle={
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "processes": [
                            {"role": "server", "pid": os.getpid()},
                            {"role": "client", "pid": 999_999_999},
                        ],
                    }
                ]
            }
        )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
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
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertEqual(result["client_alive"], False)
        self.assertEqual(result["server_alive"], True)

    def test_list_project_names_returns_mod_fields_only(self) -> None:
        policy = {
            "projects": [
                {
                    "mod": "FixtureListedMod",
                    "name": "ShouldNotSurface",
                    "path": "ignored-extra",
                },
                "skip-non-dict",
                {"name": "Nameless"},
                {"mod": ""},
                {"mod": 3},
                None,
                {"mod": "FixtureSecondMod"},
            ]
        }

        class _PolicyPath:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def resolve(self) -> _PolicyPath:
                return self

            @property
            def parents(self) -> tuple[_PolicyPath, _PolicyPath]:
                return (self, self)

            def __truediv__(self, _other: object) -> _PolicyPath:
                return self

            def read_bytes(self) -> bytes:
                return json.dumps(policy).encode("utf-8")

        # list_project_names() locates the policy through Path(__file__);
        # replace Path so the read never leaves this fixture.
        with patch.object(dayz_test_tool, "Path", _PolicyPath):
            payload = dayz_test_tool.list_project_names()

        self.assertEqual(
            payload,
            {
                "projects": [
                    {"name": "FixtureListedMod"},
                    {"name": "FixtureSecondMod"},
                ]
            },
        )
        self.assertTrue(all(set(item) == {"name"} for item in payload["projects"]))

    def test_list_project_names_missing_policy_is_not_invalid(self) -> None:
        """A clone has no sealed policy; that is missing, not corrupt.

        FileNotFoundError is an OSError. If the read except swallows OSError
        as launcher_policy_invalid, this assertion goes red.
        """
        with patch.object(
            dayz_test_tool, "Path", _list_projects_path(FileNotFoundError())
        ):
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                dayz_test_tool.list_project_names()
        self.assertEqual(caught.exception.code, "launcher_policy_missing")

    def test_list_project_names_unreadable_policy_stays_invalid(self) -> None:
        cases: tuple[tuple[str, bytes | BaseException], ...] = (
            ("broken-json", b"{not json"),
            ("non-object", b"[1]"),
            ("undecodable", b"\x80"),
            ("permission", PermissionError()),
        )
        for label, payload in cases:
            with self.subTest(label=label):
                with patch.object(
                    dayz_test_tool, "Path", _list_projects_path(payload)
                ):
                    with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                        dayz_test_tool.list_project_names()
                self.assertEqual(caught.exception.code, "launcher_policy_invalid")

    async def test_run_rejects_busy_session_before_opening_launcher(self) -> None:
        runtime = _Runtime()
        runtime.active_ticket = "ticket"
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaisesRegex(
                dayz_test_tool.DayzTestToolError, "session_busy"
            ):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="all"
                )
        opened.assert_not_called()
        self.assertEqual(runtime.reconcile_calls, 1)

    async def test_run_rejects_public_offline_mode_with_expected_enum(self) -> None:
        runtime = _Runtime()
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="offline"
                )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected server|all|client",
        )
        opened.assert_not_called()

    async def test_run_names_held_session_lease_on_transition_conflict(self) -> None:
        runtime = _Runtime()
        runtime.active_lease_token = "held-lease"
        runtime.active_operation_id = "11111111-1111-4111-8111-111111111111"

        async def reconcile() -> dict[str, object]:
            runtime.reconcile_calls += 1
            raise ControlClientError(
                "session_transition_conflict",
                request_stage="post_request",
                http_bytes_sent=1,
            )

        runtime.reconcile_idle_session = reconcile  # type: ignore[method-assign]
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="all"
                )
        self.assertEqual(
            caught.exception.code,
            "session_transition_conflict: release your session lease first - "
            "dayz_test_run manages its own lease internally",
        )
        opened.assert_not_called()
        self.assertEqual(runtime.reconcile_calls, 1)

    async def test_run_transition_conflict_without_local_lease_stays_neutral(
        self,
    ) -> None:
        runtime = _Runtime()
        runtime.active_ticket = "queued-ticket"

        async def reconcile() -> dict[str, object]:
            runtime.reconcile_calls += 1
            raise ControlClientError(
                "session_transition_conflict",
                request_stage="post_request",
                http_bytes_sent=1,
            )

        runtime.reconcile_idle_session = reconcile  # type: ignore[method-assign]
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="all"
                )
        self.assertEqual(
            caught.exception.code,
            "session_transition_conflict: a session transition is in flight",
        )
        self.assertNotIn("release your session lease first", caught.exception.code)
        opened.assert_not_called()
        self.assertEqual(runtime.reconcile_calls, 1)

    async def test_stop_names_held_session_lease_without_naming_run(self) -> None:
        runtime = _Runtime()
        runtime.active_lease_token = "held-lease"
        runtime.active_operation_id = "11111111-1111-4111-8111-111111111111"

        async def reconcile() -> dict[str, object]:
            runtime.reconcile_calls += 1
            raise ControlClientError(
                "session_transition_conflict",
                request_stage="post_request",
                http_bytes_sent=1,
            )

        runtime.reconcile_idle_session = reconcile  # type: ignore[method-assign]
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)
        self.assertEqual(
            caught.exception.code,
            "session_transition_conflict: release your session lease first - "
            "dayz_test_stop manages its own lease internally",
        )
        self.assertNotIn("dayz_test_run manages", caught.exception.code)
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

        await dayz_test_tool._require_idle_session(runtime, tool="dayz_test_run")

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
                    mode="client",
                    run_id=RUN_ID,
                    extra_mods=["@DayZ_MCP"],
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

    async def test_worker_failed_carries_lifecycle_start_error(self) -> None:
        policy = _policy()
        runtime = _Runtime(
            lifecycle={"runs": [], "last_start_error": "instance_config_missing"}
        )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": True,
                        "error_code": "worker_failed",
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
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "instance_config_missing")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertGreaterEqual(runtime.lifecycle_calls, 1)

    async def test_stdout_overflow_sentinel_is_rejected_by_real_parser(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        seen_len: list[int] = []
        real_parse = dayz_test_tool.parse_worker_terminal
        valid = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": RUN_ID,
            }
        )
        # Compact five-key terminals stay well under the 4096 cap; 4097
        # is overflow, not a valid payload the parser could accept.
        self.assertLess(len(valid), 4096)
        self.assertEqual(len(b"{" + (b" " * 4094) + b"}"), 4096)

        def parse_and_record(
            stdout: bytes, stderr: bytes, process_exit_code: int
        ) -> dayz_test_tool.WorkerTerminal:
            seen_len.append(len(stdout))
            self.assertEqual(stderr, b"")
            self.assertEqual(process_exit_code, 0)
            return real_parse(stdout, stderr, process_exit_code)

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"]("stdout", b"{" + (b" " * 4094) + b"}")
            kwargs["output_sink"]("stdout", b"X")
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ), patch.object(
            dayz_test_tool, "parse_worker_terminal", side_effect=parse_and_record
        ):
            with self.assertRaisesRegex(
                dayz_test_tool.DayzTestToolError, "terminal_invalid"
            ):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    mode="all",
                    extra_mods=["@DayZ_MCP"],
                )

        self.assertEqual(seen_len, [4097])


if __name__ == "__main__":
    unittest.main()


# --- M19: the single readiness contract (ficha 21/a396 point 5) -------------
#
# The incident this comes from is a launch called ready because a PID existed.
# So the contract has two axes that never feed each other, and a third field
# that says WHY the bridge axis holds what it holds. The table below is written
# out rather than generated: deriving the expectations from the projection would
# make it agree with itself.
#
# label, client_alive, bridge snapshot, expected (process_alive, bridge_ready, reason)
_PROJECTION_CASES = (
    (
        "alive and polling",
        True,
        {"ready": {"ready": True, "reason": "ready"}},
        (True, True, "ready"),
    ),
    (
        "alive but not polling",
        True,
        {"ready": {"ready": False, "reason": "client_not_polling"}},
        (True, False, "client_not_polling"),
    ),
    (
        # The two axes have to be able to disagree, or one of them is decorative.
        "process gone, snapshot still says ready",
        False,
        {"ready": {"ready": True, "reason": "ready"}},
        (False, True, "ready"),
    ),
    ("no snapshot at all", True, None, (True, None, None)),
    ("snapshot is not a mapping", True, ["ready"], (True, None, None)),
    ("ready object is not a mapping", True, {"ready": "yes"}, (True, None, None)),
    (
        # An unfamiliar reason is transported, not swallowed: the reason space
        # is the server's to grow, and dropping one would report "unknown" for
        # an answer that exists.
        "reason this module has never seen",
        True,
        {"ready": {"ready": True, "reason": "some_new_reason"}},
        (True, True, "some_new_reason"),
    ),
    (
        "reason present but empty",
        True,
        {"ready": {"ready": True, "reason": ""}},
        (True, None, None),
    ),
    (
        "flag is not a boolean",
        True,
        {"ready": {"ready": 1, "reason": "ready"}},
        (True, None, None),
    ),
    ("liveness unknown too", None, None, (None, None, None)),
)


class LaunchReadinessProjectionTest(unittest.TestCase):
    def test_projection_matrix(self) -> None:
        for label, alive, snapshot, expected in _PROJECTION_CASES:
            with self.subTest(label):
                projection = dayz_test_tool._project_launch_readiness(alive, snapshot)
                self.assertEqual(
                    (
                        projection.process_alive,
                        projection.bridge_ready,
                        projection.reason,
                    ),
                    expected,
                )

    def test_the_bridge_axis_is_never_promoted_from_the_process_axis(self) -> None:
        # The whole point: a live process with no readable snapshot stays
        # unknown on the bridge axis. If this ever returns True, the defect the
        # contract exists to expose is back.
        projection = dayz_test_tool._project_launch_readiness(True, None)
        self.assertIs(projection.process_alive, True)
        self.assertIsNone(projection.bridge_ready)
        self.assertIsNone(projection.reason)

    def test_every_reason_the_server_can_emit_survives_the_projection(self) -> None:
        # Derived from the server, not from prose. The first version of this
        # contract whitelisted six reasons taken from the tool description; the
        # server also emits "legacy_unbound" AND every value of
        # _FENCE_BLOCK_READY, so the whitelist would have reported "unknown" for
        # real answers. The reason space is open: what must hold is that nothing
        # the server can say gets dropped on the way through.
        source = inspect.getsource(server.compute_bridge_ready)
        emitted = set(re.findall(r'"reason":\s*"([a-z_]+)"', source))
        emitted |= {
            value
            for value in server._FENCE_BLOCK_READY.values()
            if isinstance(value, str) and value
        }
        self.assertGreater(len(emitted), 6, "no se leyo el espacio real de razones")
        for reason in sorted(emitted):
            with self.subTest(reason):
                projection = dayz_test_tool._project_launch_readiness(
                    True, {"ready": {"ready": False, "reason": reason}}
                )
                self.assertIs(projection.bridge_ready, False)
                self.assertEqual(projection.reason, reason)

    def test_a_reason_that_is_not_a_usable_string_is_no_answer(self) -> None:
        for bad in (None, "", 7, ["ready"], {}):
            with self.subTest(repr(bad)):
                projection = dayz_test_tool._project_launch_readiness(
                    True, {"ready": {"ready": False, "reason": bad}}
                )
                self.assertIsNone(projection.bridge_ready)
                self.assertIsNone(projection.reason)
