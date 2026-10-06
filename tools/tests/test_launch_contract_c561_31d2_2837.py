"""Launch-contract batch: attestation (c561), busy preflight (31d2), override (2837)."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import (
    dayz_test_attestation as attestation,
    dayz_test_request,
    dayz_test_storage,
    dayz_test_tool,
    dayz_test_worker,
    native_broker_protocol,
    native_launcher_transaction,
    request_path_authority,
    server as server_module,
)
from dayz_mcp.server import ServerConfig
from dayz_mcp import steam_preflight
from dayz_mcp.steam_preflight import SteamSessionResult
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)
from tests.mcp_helpers import _content_json


def _pbo(entries: dict[str, bytes], *, mime: int = 0) -> bytes:
    header = b""
    blobs = b""
    for name, payload in entries.items():
        header += name.encode("utf-8") + b"\x00"
        header += struct.pack("<5I", mime, len(payload), 0, 0, len(payload))
        blobs += payload
    header += b"\x00" + struct.pack("<5I", 0, 0, 0, 0, 0)
    body = header + blobs
    return body + b"\x00" + hashlib.sha1(body).digest()


def _policy_with(document: dict[str, object] | None, **kwargs: object):
    policy = _policy(**kwargs) if kwargs else _policy()
    parsed = None if document is None else attestation.parse_attestation(document)
    return dayz_test_request.RequestProjectPolicy(
        mod=policy.mod,
        dev_root=policy.dev_root,
        default_source=policy.default_source,
        default_base_mods=policy.default_base_mods,
        mission_roots=policy.mission_roots,
        mod_roots=policy.mod_roots,
        attestation=parsed,
    )


def _runtime(policy, mods_root: str, dev_root: str | None = None):
    root = dev_root or policy.dev_root
    mission = policy.mission_roots[0] + r"\dayzOffline.chernarusplus"
    return dayz_test_worker.WorkerRuntimePolicy(
        dev_root=root,
        mod=policy.mod,
        diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
        game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
        mission_aliases=(
            ("chernarus", mission if os.path.isabs(mission) else r"C:\missions\chernarus"),
            ("livonia", r"C:\missions\livonia"),
            ("sakhal", r"C:\missions\sakhal"),
        ),
        mods_root=mods_root,
        build_temp_root=os.path.join(mods_root, "_tmp"),
        build_source_basename=None,
    )


class _Broker:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.fail_stop = False
        self.cancel_on_start = False
        self.cancel_event: asyncio.Event | None = None

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        self.requests.append(request)
        command = request.payload.get("command")
        if command == "start" and self.cancel_on_start and self.cancel_event is not None:
            self.cancel_event.set()
        if command == "stop" and self.fail_stop:
            return {"ok": False, "error": "run_stop_failed"}
        if command == "stop":
            return {"ok": True, "state": "EXITED", "run_id": request.payload.get("run_id")}
        if command in {"start", "adopt", "ack"}:
            return {"ok": True, "state": "RUNNING", "run_id": request.payload.get("run_id")}
        return {"ok": True}


def _run(raw: bytes, policies, runtime, broker, cancel=None):
    return asyncio.run(
        dayz_test_worker.execute_dayz_test_worker(
            raw,
            request_sha256=hashlib.sha256(raw).hexdigest(),
            request_policies=policies,
            runtime_policy=runtime,
            broker=broker,
            id_fn=lambda: "12345678-1234-4234-8234-1234567890ab",
            readiness_probe=None,
            cancel_event=cancel,
        )
    )


def _request(policy, **overrides: object) -> bytes:
    document: dict[str, object] = {
        "dev_root": policy.dev_root,
        "mod": policy.mod,
        "mode": "server",
        "version": 1,
        "extra_mods": ["@DayZ_MCP"],
    }
    document.update(overrides)
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return dayz_test_request.parse_dayz_test_request(raw, policies=(policy,)).canonical_bytes


class AttestationTests(unittest.TestCase):
    def _tree(self, entries: dict[str, bytes]):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        mod = os.path.join(directory.name, "@Candidate")
        os.makedirs(os.path.join(mod, "Addons"))
        path = os.path.join(mod, "Addons", "Example.pbo")
        with open(path, "wb") as handle:
            handle.write(_pbo(entries))
        live = os.path.join(directory.name, "@ExampleMod", "Addons")
        os.makedirs(live)
        with open(os.path.join(live, "Example.pbo"), "wb") as handle:
            handle.write(_pbo({"scripts/3_Game/live.c": b"live"}))
        return directory.name

    def test_c561_required_script_entry_missing_fails_before_start(self) -> None:
        root = self._tree({"config.bin": b"cfg"})
        policy = _policy_with(
            {
                "version": 1,
                "artifacts": [
                    {"id": "addon", "pbo": r"Addons\Example.pbo", "entries": ["scripts/3_Game/need.c"]}
                ],
                "initialization": [],
                "timeout_s": 1,
            }
        )
        runtime = _runtime(policy, root, root)
        # The mission alias must be an absolute path the runtime accepts.
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=r"C:\dev",
            mod=policy.mod,
            diag_executable=runtime.diag_executable,
            game_directory=runtime.game_directory,
            mission_aliases=(
                ("chernarus", r"C:\missions\chernarus"),
                ("livonia", r"C:\missions\livonia"),
                ("sakhal", r"C:\missions\sakhal"),
            ),
            mods_root=root,
            build_temp_root=os.path.join(root, "_tmp"),
            build_source_basename=None,
        )
        policy = dayz_test_request.RequestProjectPolicy(
            mod=policy.mod,
            dev_root=r"C:\dev",
            default_source=policy.default_source,
            default_base_mods=(),
            mission_roots=(r"C:\missions",),
            mod_roots=(root,),
            attestation=policy.attestation,
        )
        raw = _request(policy, base_mods=["@Candidate"], extra_mods=["@DayZ_MCP"], version=2, project_mod_override=True)
        broker = _Broker()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            _run(raw, (policy,), runtime, broker)
        self.assertEqual(raised.exception.code, "project_attestation_artifact_missing")
        self.assertEqual(broker.requests, [])
        self.assertEqual(raised.exception.attestation["status"], "failed")

    def test_c561_initialization_requires_post_launch_evidence(self) -> None:
        profiles = tempfile.TemporaryDirectory()
        self.addCleanup(profiles.cleanup)
        log = os.path.join(profiles.name, "script.log")
        with open(log, "w", encoding="utf-8") as handle:
            handle.write("OLD MARKER\n")
        boundaries = attestation.capture_log_boundaries(profiles.name)
        self.assertEqual(
            attestation.new_matching_line(
                pattern="OLD MARKER",
                source="script_log",
                profiles=profiles.name,
                filename=None,
                boundaries=boundaries,
            ),
            "failed",
        )
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("NEW MARKER\n")
        self.assertEqual(
            attestation.new_matching_line(
                pattern="NEW MARKER",
                source="script_log",
                profiles=profiles.name,
                filename=None,
                boundaries=boundaries,
            ),
            "passed",
        )

    def test_c561_checks_deployed_candidate_artifact(self) -> None:
        root = self._tree({"scripts/3_Game/need.c": b"candidate"})
        directories = (os.path.join(root, "@Candidate"),)
        status, rows = attestation.verify_artifacts(
            attestation.parse_attestation(
                {
                    "version": 1,
                    "artifacts": [
                        {
                            "id": "addon",
                            "pbo": r"Addons\Example.pbo",
                            "entries": ["scripts/3_Game/need.c"],
                        }
                    ],
                    "initialization": [],
                }
            ),
            directories,
        )
        self.assertEqual(status, "passed")
        live = os.path.join(root, "@ExampleMod", "Addons", "Example.pbo")
        with open(live, "rb") as handle:
            live_hash = hashlib.sha256(handle.read()).hexdigest()
        self.assertNotEqual(rows[0]["sha256"], live_hash)

    def test_c561_failure_cleans_only_new_run(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        os.makedirs(os.path.join(root.name, "@Candidate", "Addons"))
        with open(os.path.join(root.name, "@Candidate", "Addons", "Example.pbo"), "wb") as handle:
            handle.write(_pbo({"config.bin": b"ok"}))
        policy = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"C:\dev",
            default_source=r"C:\src",
            default_base_mods=(),
            mission_roots=(r"C:\missions",),
            mod_roots=(root.name,),
            attestation=attestation.parse_attestation(
                {
                    "version": 1,
                    "artifacts": [
                        {"id": "addon", "pbo": r"Addons\Example.pbo", "entries": ["config.bin"]}
                    ],
                    "initialization": [
                        {
                            "id": "boot",
                            "role": "server",
                            "pattern": "BOOTED",
                            "source": "script_log",
                        }
                    ],
                    "timeout_s": 0.05,
                }
            ),
        )
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=r"C:\dev",
            mod="ExampleMod",
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(
                ("chernarus", r"C:\missions\chernarus"),
                ("livonia", r"C:\missions\livonia"),
                ("sakhal", r"C:\missions\sakhal"),
            ),
            mods_root=root.name,
            build_temp_root=os.path.join(root.name, "_tmp"),
            build_source_basename=None,
        )
        raw = _request(
            policy,
            base_mods=["@Candidate"],
            extra_mods=["@DayZ_MCP"],
            version=2,
            project_mod_override=True,
        )
        broker = _Broker()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            _run(raw, (policy,), runtime, broker)
        self.assertEqual(raised.exception.code, "project_attestation_initialization_missing")
        commands = [item.payload.get("command") for item in broker.requests]
        self.assertIn("stop", commands)
        broker.requests.clear()
        broker.fail_stop = True
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            _run(raw, (policy,), runtime, broker)
        self.assertTrue(raised.exception.cleanup_degraded)
        client = _request(
            policy,
            mode="client",
            run_id=RUN_ID,
            base_mods=["@Candidate"],
            extra_mods=["@DayZ_MCP"],
            version=2,
            project_mod_override=True,
        )
        # Reattach still preserves the server: initialization role client, no server stop
        # of a run this call did not create. Swap the requirement role via a client policy.
        client_policy = dayz_test_request.RequestProjectPolicy(
            mod=policy.mod,
            dev_root=policy.dev_root,
            default_source=policy.default_source,
            default_base_mods=(),
            mission_roots=policy.mission_roots,
            mod_roots=policy.mod_roots,
            attestation=attestation.parse_attestation(
                {
                    "version": 1,
                    "artifacts": [
                        {"id": "addon", "pbo": r"Addons\Example.pbo", "entries": ["config.bin"]}
                    ],
                    "initialization": [
                        {
                            "id": "boot",
                            "role": "client",
                            "pattern": "BOOTED",
                            "source": "script_log",
                        }
                    ],
                    "timeout_s": 0.05,
                }
            ),
        )
        client = _request(
            client_policy,
            mode="client",
            run_id=RUN_ID,
            base_mods=["@Candidate"],
            extra_mods=["@DayZ_MCP"],
            version=2,
            project_mod_override=True,
        )
        broker = _Broker()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            _run(client, (client_policy,), runtime, broker)
        self.assertNotIn("stop", [item.payload.get("command") for item in broker.requests])
        self.assertEqual(raised.exception.attempt_run_id, RUN_ID)

    def test_c561_report_reaches_public_result(self) -> None:
        report = {
            "artifacts": [{"id": "addon", "sha256": "a" * 64, "status": "passed"}],
            "initialization": [{"id": "boot", "status": "passed"}],
            "status": "passed",
        }
        stdout = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": RUN_ID,
                "attestation": report,
            }
        )
        parsed = dayz_test_tool.parse_worker_terminal(stdout, b"", 0)
        result = dayz_test_tool._compact_result(
            terminal=parsed,
            project="ExampleMod",
            mode="server",
            started_at=0.0,
            artifacts_paths=[],
        )
        self.assertEqual(result["attestation"]["status"], "passed")
        failed = _terminal(
            {
                "attempt_run_id": RUN_ID,
                "attestation": {
                    "artifacts": [{"id": "addon", "sha256": None, "status": "failed"}],
                    "initialization": [{"id": "boot", "status": "pending"}],
                    "status": "failed",
                },
                "cleanup_degraded": False,
                "error_code": "project_attestation_artifact_missing",
                "exit_code": 2,
                "ok": False,
                "run_id": None,
            }
        )
        parsed = dayz_test_tool.parse_worker_terminal(failed, b"", 2)
        projected = dayz_test_tool._compact_result(
            terminal=parsed,
            project="ExampleMod",
            mode="server",
            started_at=0.0,
            artifacts_paths=[],
        )
        self.assertEqual(projected["error_code"], "project_attestation_artifact_missing")
        self.assertEqual(projected["attestation"]["status"], "failed")
        legacy = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": None,
            }
        )
        self.assertIsNone(dayz_test_tool.parse_worker_terminal(legacy, b"", 0).attestation)
        extra = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": None,
                "unexpected": 1,
            }
        )
        with self.assertRaises(dayz_test_tool.DayzTestToolError):
            dayz_test_tool.parse_worker_terminal(extra, b"", 0)

    def test_c561_compressed_entry_is_unverifiable_and_disabled_policy_launches(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        os.makedirs(os.path.join(root.name, "@Candidate", "Addons"))
        with open(os.path.join(root.name, "@Candidate", "Addons", "Example.pbo"), "wb") as handle:
            handle.write(_pbo({"config.bin": b"x"}, mime=1))
        document = attestation.parse_attestation(
            {
                "version": 1,
                "artifacts": [
                    {"id": "addon", "pbo": r"Addons\Example.pbo", "entries": ["config.bin"]}
                ],
                "initialization": [],
            }
        )
        status, rows = attestation.verify_artifacts(document, (os.path.join(root.name, "@Candidate"),))
        self.assertEqual(status, "unverifiable")
        self.assertEqual(rows[0]["status"], "unverifiable")
        policy = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"C:\dev",
            default_source=r"C:\src",
            default_base_mods=(),
            mission_roots=(r"C:\missions",),
            mod_roots=(root.name,),
        )
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=r"C:\dev",
            mod="ExampleMod",
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(
                ("chernarus", r"C:\missions\chernarus"),
                ("livonia", r"C:\missions\livonia"),
                ("sakhal", r"C:\missions\sakhal"),
            ),
            mods_root=root.name,
            build_temp_root=os.path.join(root.name, "_tmp"),
            build_source_basename=None,
        )
        raw = _request(policy, extra_mods=["@DayZ_MCP"], mode="server")
        broker = _Broker()
        result = _run(raw, (policy,), runtime, broker)
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.attestation)
        self.assertTrue(broker.requests)

    def test_c561_unreadable_and_truncated_evidence_is_unverifiable(self) -> None:
        profiles = tempfile.TemporaryDirectory()
        self.addCleanup(profiles.cleanup)
        path = os.path.join(profiles.name, "script.log")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe not utf-8\n")
        with self.assertRaises(ValueError) as unread:
            attestation.new_matching_line(
                pattern="MARK",
                source="script_log",
                profiles=profiles.name,
                filename=None,
                boundaries={os.path.normcase(path): 0},
            )
        self.assertEqual(str(unread.exception), "unverifiable")
        huge_dir = tempfile.TemporaryDirectory()
        self.addCleanup(huge_dir.cleanup)
        huge = os.path.join(huge_dir.name, "big.log")
        with open(huge, "wb") as handle:
            handle.write(b"A" * (attestation._MAX_SCAN_BYTES + 8) + b"\n")
        with self.assertRaises(ValueError) as raised:
            attestation.new_matching_line(
                pattern="MARK",
                source="script_log",
                profiles=huge_dir.name,
                filename=None,
                boundaries={os.path.normcase(huge): 0},
            )
        self.assertEqual(str(raised.exception), "truncated")


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def _preflight(self, runtime, **kwargs):
        policy = _policy()
        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle", return_value=_Bundle(_sealed(policy))
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request",
            return_value=type("V", (), {"error_code": None, "missing": (), "warnings": (), "hint": ""})(),
        ), patch.object(
            dayz_test_tool.secure_launcher, "execute_secure_launcher_request", new=AsyncMock()
        ) as launch, patch.object(
            dayz_test_tool, "evaluate_steam_session",
            return_value=SteamSessionResult(None, 41, (41,), ""),
        ), patch.object(
            dayz_test_tool, "evaluate_prerun_desktop",
            return_value=type("D", (), {"error_code": None, "remediation": ""})(),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode=kwargs.pop("mode", "server"),
                extra_mods=["@DayZ_MCP"],
                preflight=True,
                **kwargs,
            )
        return result, launch

    async def test_31d2_foreign_box_does_not_block_valid_preflight(self) -> None:
        runtime = _Runtime({"runs": [{"run_id": RUN_ID, "state": "RUNNING", "mod": "@Other"}]})
        runtime.reconcile_calls = 0
        result, launch = await self._preflight(runtime)
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["box_busy"])
        self.assertEqual(result["occupied_by_run_id"], RUN_ID)
        self.assertTrue(result["preflight"])
        launch.assert_not_awaited()
        self.assertEqual(runtime.reconcile_calls, 0)

    async def test_31d2_preflight_takeover_never_evicts(self) -> None:
        from tests.client_helpers import _fixture_client_runtime

        config = ServerConfig(
            mode="client", key="k", port=1, client_platform="codex", log_sink=lambda _m: None
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(config)
        execute = AsyncMock(return_value={"status": "succeeded", "error_code": None, "preflight": True})
        stop = AsyncMock(side_effect=AssertionError("stop"))
        wait = AsyncMock(side_effect=AssertionError("wait"))
        with patch.object(server_module.dayz_test_tool, "execute_dayz_test_run", new=execute), patch.object(
            server_module.dayz_test_tool, "execute_dayz_test_stop", new=stop
        ), patch.object(server_module, "execute_wait_for_box", new=wait):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "preflight": True,
                        "takeover": True,
                        "on_busy": "queue",
                        "wait_for_box_s": 5,
                    },
                )
            )
        execute.assert_awaited()
        stop.assert_not_awaited()
        wait.assert_not_awaited()
        self.assertEqual(payload.get("status"), "succeeded")

    async def test_31d2_busy_box_does_not_mask_validation_failure(self) -> None:
        runtime = _Runtime({"runs": [{"run_id": RUN_ID, "state": "RUNNING"}]})
        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(_policy())),
        ):
            with self.assertRaises(dayz_test_tool.DayzTestToolError):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    mode="server",
                    preflight=True,
                    mission="not-a-mission",
                    extra_mods=["@DayZ_MCP"],
                )

    async def test_31d2_preflight_checks_steam_without_repair(self) -> None:
        runtime = _Runtime()
        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle", return_value=_Bundle(_sealed(_policy()))
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request",
            return_value=type("V", (), {"error_code": None, "missing": (), "warnings": (), "hint": ""})(),
        ), patch.object(
            dayz_test_tool, "evaluate_prerun_desktop",
            return_value=type("D", (), {"error_code": None, "remediation": ""})(),
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session",
            return_value=SteamSessionResult("steam_not_running", None, (), "start steam"),
        ), patch.object(
            dayz_test_tool.steam_preflight if False else __import__("dayz_mcp.steam_preflight", fromlist=["remediate_stale_steam_session"]),
            "remediate_stale_steam_session",
            side_effect=AssertionError("repair"),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                preflight=True,
                extra_mods=["@DayZ_MCP"],
                auto_remediate_steam=True,
            )
        self.assertEqual(result["error_code"], "steam_not_running")
        self.assertEqual(result["remediation"], "start steam")
        self.assertTrue(result["box_busy"] in {True, False, None})

    async def test_31d2_preflight_with_held_lease_preserves_it(self) -> None:
        runtime = _Runtime()
        runtime.active_lease_token = "held"
        result, _launch = await self._preflight(runtime)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(runtime.reconcile_calls, 0)
        self.assertEqual(runtime.active_lease_token, "held")

    async def test_31d2_host_and_worker_preflight_agree(self) -> None:
        policy = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"C:\dev",
            default_source=r"C:\src",
            default_base_mods=(),
            mission_roots=(r"C:\missions",),
            mod_roots=(r"C:\mods",),
        )
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=r"C:\dev",
            mod="ExampleMod",
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(
                ("chernarus", r"C:\missions\chernarus"),
                ("livonia", r"C:\missions\livonia"),
                ("sakhal", r"C:\missions\sakhal"),
            ),
            mods_root=r"C:\mods",
            build_temp_root=r"C:\tmp",
            build_source_basename="ExampleMod",
        )
        raw = _request(policy, mode="server", preflight=True, extra_mods=["@DayZ_MCP"], build=True, source=r"C:\src")
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as host:
            dayz_test_worker.assess_preflight(parsed.payload, runtime, None)
        broker = _Broker()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as worker:
            await dayz_test_worker.execute_dayz_test_worker(
                raw,
                request_sha256=hashlib.sha256(raw).hexdigest(),
                request_policies=(policy,),
                runtime_policy=runtime,
                broker=broker,
                id_fn=lambda: "12345678-1234-4234-8234-1234567890ab",
            )
        self.assertEqual(host.exception.code, worker.exception.code)
        self.assertEqual(broker.requests, [])


class OverrideTests(unittest.TestCase):
    def _runtime(self):
        return dayz_test_worker.WorkerRuntimePolicy(
            dev_root=r"C:\dev",
            mod="ExampleMod",
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(
                ("chernarus", r"C:\missions\chernarus"),
                ("livonia", r"C:\missions\livonia"),
                ("sakhal", r"C:\missions\sakhal"),
            ),
            mods_root=r"C:\mods",
            build_temp_root=r"C:\tmp",
            build_source_basename=None,
        )

    def _policy(self):
        return dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"C:\dev",
            default_source=r"C:\src",
            default_base_mods=("@CF",),
            mission_roots=(r"C:\missions",),
            mod_roots=(r"C:\mods",),
        )

    def test_2837_override_excludes_original_from_all_role_argv(self) -> None:
        policy = self._policy()
        runtime = self._runtime()
        raw = _request(
            policy,
            version=2,
            project_mod_override=True,
            base_mods=["@Candidate"],
            extra_mods=["@DayZ_MCP"],
            mode="server",
        )
        payload = json.loads(raw)
        for mode in ("server", "client", "offline"):
            body = dict(payload)
            body["mode"] = mode
            body["run_id"] = RUN_ID if mode == "client" else None
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            parsed = dayz_test_request.parse_dayz_test_request(encoded, policies=(policy,))
            core = dayz_test_worker._start_core(
                parsed.payload, runtime, role="server" if mode == "server" else ("client" if mode == "client" else "offline"), run_id=RUN_ID if mode == "client" else None
            )
            mod = next(item for item in core["argv"] if str(item).startswith("-mod="))
            self.assertIn(r"C:\mods\@Candidate", mod)
            self.assertNotIn("@ExampleMod", mod)
            self.assertEqual(mod.count("@Candidate"), 1)
            self.assertEqual(core["mod"], "@ExampleMod")

    def test_2837_override_vpp_and_worker_lists_agree(self) -> None:
        policy = self._policy()
        raw = _request(
            policy,
            version=2,
            project_mod_override=True,
            base_mods=["@Candidate"],
            extra_mods=["@DayZ_MCP"],
        )
        payload = json.loads(raw)
        expected = ("@Candidate", "@DayZ_MCP")
        self.assertEqual(native_launcher_transaction.effective_mod_entries(payload), expected)
        joined = dayz_test_worker._mods(payload, self._runtime())
        self.assertEqual(joined, r"C:\mods\@Candidate;C:\mods\@DayZ_MCP")

    def test_2837_override_changes_storage_seal_and_default_preserves_it(self) -> None:
        default = dayz_test_storage.modset_seal(
            dayz_test_storage.modset_roles(
                base_mods=["@CF"],
                project_mod="@ExampleMod",
                extra_mods=["@DayZ_MCP"],
                server_mods=[],
                mods_root=r"C:\mods",
            )
        )
        again = dayz_test_storage.modset_seal(
            dayz_test_storage.modset_roles(
                base_mods=["@CF"],
                project_mod="@ExampleMod",
                extra_mods=["@DayZ_MCP"],
                server_mods=[],
                mods_root=r"C:\mods",
            )
        )
        self.assertEqual(default, again)
        override = dayz_test_storage.modset_seal(
            dayz_test_storage.modset_roles(
                base_mods=["@Candidate"],
                project_mod=None,
                extra_mods=["@DayZ_MCP"],
                server_mods=[],
                mods_root=r"C:\mods",
            )
        )
        self.assertNotEqual(default, override)

    def test_2837_dayz_mcp_override_requires_effective_bridge(self) -> None:
        policy = dayz_test_request.RequestProjectPolicy(
            mod="DayZ_MCP",
            dev_root=r"C:\dev",
            default_source=r"C:\src",
            default_base_mods=(),
            mission_roots=(r"C:\missions",),
            mod_roots=(r"C:\mods",),
        )
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as raised:
            dayz_test_tool.build_run_request(
                _sealed(policy),
                project="DayZ_MCP",
                mode="server",
                extra_mods=["@Candidate"],
                project_mod_override=True,
            )
        self.assertIn("bridge_mod_missing", str(raised.exception))

    def test_2837_override_survives_witness_recomposition(self) -> None:
        policy = self._policy()
        first, _selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="client",
            run_id=RUN_ID,
            base_mods=["@Candidate"],
            extra_mods=["@DayZ_MCP"],
            project_mod_override=True,
        )
        second, _selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="client",
            run_id=RUN_ID,
            base_mods=["@Candidate"],
            extra_mods=["@DayZ_MCP"],
            project_mod_override=True,
            replace_if_not_polling_since=1_700_000_000_000,
        )
        self.assertTrue(json.loads(first)["project_mod_override"])
        self.assertTrue(json.loads(second)["project_mod_override"])
        self.assertEqual(json.loads(first)["version"], 2)
        self.assertEqual(json.loads(second)["version"], 2)

    def test_2837_old_canonical_v1_still_reparses(self) -> None:
        policy = self._policy()
        document = {
            "auto_remediate_steam": False,
            "build": False,
            "clean": False,
            "dev_root": policy.dev_root,
            "height": 1080,
            "kill": False,
            "mission": "chernarus",
            "mod": policy.mod,
            "mode": "server",
            "navmesh_data_server": False,
            "no_base_mods": False,
            "no_file_patching": False,
            "pack_only": False,
            "player_name": "Dev",
            "port": 2302,
            "preflight": False,
            "run_id": None,
            "server_wait_s": 60,
            "version": 1,
            "width": 1920,
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertEqual(parsed.payload["version"], 1)
        self.assertNotIn("project_mod_override", parsed.payload)
        self.assertIn("@ExampleMod", dayz_test_worker._mods(parsed.payload, self._runtime()))

    def test_2837_candidate_build_conflict_is_rejected_before_side_effects(self) -> None:
        policy = self._policy()
        raw = json.dumps(
            {
                "base_mods": ["@Candidate"],
                "build": True,
                "dev_root": policy.dev_root,
                "mod": policy.mod,
                "mode": "server",
                "project_mod_override": True,
                "version": 2,
            }
        ).encode()
        with self.assertRaises(ValueError) as raised:
            dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertIn("project_mod_override_conflicts_with_build", str(raised.exception))

    def test_2837_negative_override_cases(self) -> None:
        policy = self._policy()
        def parse(document: dict[str, object]):
            raw = json.dumps(document).encode()
            return dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))

        with self.assertRaises(ValueError) as raised:
            parse({
                "dev_root": policy.dev_root,
                "mod": policy.mod,
                "mode": "server",
                "project_mod_override": "yes",
                "version": 2,
            })
        self.assertIn("flag_not_boolean", str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            parse({
                "dev_root": policy.dev_root,
                "extra_mods": ["@DayZ_MCP"],
                "mod": policy.mod,
                "mode": "server",
                "project_mod_override": True,
                "version": 2,
            })
        self.assertIn("project_mod_override_requires_candidate", str(raised.exception))
        allowed = dayz_test_request.RequestProjectPolicy(
            mod=policy.mod,
            dev_root=policy.dev_root,
            default_source=policy.default_source,
            default_base_mods=policy.default_base_mods,
            mission_roots=policy.mission_roots,
            mod_roots=(r"C:\mods", r"C:\candidate"),
        )
        raw = json.dumps({
            "base_mods": [r"C:\candidate\@ExampleMod"],
            "dev_root": policy.dev_root,
            "extra_mods": ["@DayZ_MCP"],
            "mod": policy.mod,
            "mode": "server",
            "project_mod_override": True,
            "version": 2,
        }).encode()
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(allowed,))
        self.assertTrue(parsed.payload["project_mod_override"])
        dayz_test_worker._reject_override_alias(parsed.payload, self._runtime())
        same = dict(parsed.payload)
        same["base_mods"] = [r"C:\mods\@ExampleMod"]
        with self.assertRaises(ValueError) as raised:
            dayz_test_worker._reject_override_alias(same, self._runtime())
        self.assertIn("project_mod_override_includes_original", str(raised.exception))


class Round3Tests(unittest.IsolatedAsyncioTestCase):
    def test_f9_unverifiable_artifact_precedes_a_later_miss(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        encoded = _pbo({"config.bin": b"x"}, mime=1)
        with open(os.path.join(root.name, "encoded.pbo"), "wb") as handle:
            handle.write(encoded)
        document = attestation.parse_attestation({
            "version": 1,
            "artifacts": [
                {"id": "encoded", "pbo": "encoded.pbo", "entries": ["config.bin"]},
                {"id": "absent", "pbo": "absent.pbo", "entries": ["scripts\\a.c"]},
            ],
            "initialization": [],
        })
        status, rows = attestation.verify_artifacts(document, (root.name,))
        self.assertEqual([item["status"] for item in rows], ["unverifiable", "failed"])
        self.assertEqual(status, "unverifiable")

    def test_f10_policy_types_fail_closed(self) -> None:
        documents = (
            {"version": True, "artifacts": [], "initialization": []},
            {"version": 1.0, "artifacts": [], "initialization": []},
            {
                "version": 1,
                "artifacts": [{"id": "a", "pbo": "a.pbo", "entries": [{}]}],
                "initialization": [],
            },
            {
                "version": 1,
                "artifacts": [],
                "initialization": [
                    {"id": "b", "role": "server", "pattern": "MARK", "source": {}}
                ],
            },
        )
        for document in documents:
            with self.subTest(document=document):
                with self.assertRaises(attestation.AttestationPolicyError):
                    attestation.parse_attestation(document)

    def test_f4_runtime_accessor_uses_the_worker_rules(self) -> None:
        from dayz_mcp.native_bundle import VerifiedNativeBundle

        bundle = object.__new__(VerifiedNativeBundle)
        bundle.worker_runtime_document = {
            "format_version": 1,
            "projects": [
                {
                    "mod": "ExampleMod",
                    "dev_root": r"C:\dev",
                    "diag_executable": r"C:\DayZ\DayZDiag_x64.exe",
                    "game_directory": r"C:\DayZ",
                    "mission_aliases": {"chernarus": "relative-missing"},
                    "mods_root": r"C:\mods",
                    "build_temp_root": r"C:\tmp",
                }
            ],
        }
        with self.assertRaises(ValueError) as raised:
            bundle.validated_worker_runtime("ExampleMod", r"C:\dev")
        self.assertEqual(str(raised.exception), "invalid_native_launcher_bundle")

    def test_f11_h11_and_h13_state_the_acceptance_contract(self) -> None:
        spec = os.path.join(os.path.dirname(__file__), "..", "..", "product-spec.md")
        with open(spec, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        h11 = next(line for line in lines if line.startswith("| H11 |"))
        h13 = next(line for line in lines if line.startswith("| H13 |"))
        self.assertIn("box_busy", h11)
        self.assertIn("project_mod_override", h11)
        self.assertIn("attestation", h11)
        self.assertIn("box_busy", h13)
        self.assertIn("scan_known", h13)

    async def test_f7_foreign_process_and_unknown_scan(self) -> None:
        class Runtime:
            def __init__(self, box: dict[str, object]) -> None:
                self.box = box

            async def lifecycle_status(self) -> dict[str, object]:
                return {"runs": [], "scan_known": False}

            async def session_status(self) -> dict[str, object]:
                return {"box": self.box}

        busy, occupant = await dayz_test_tool._sample_box_occupancy(
            Runtime({
                "occupied": True,
                "runs": [],
                "foreign": [{"pid": 9, "image": "DayZDiag_x64.exe"}],
                "scan_known": True,
                "port_scan_known": True,
            })
        )
        self.assertTrue(busy)
        self.assertIsNone(occupant)
        unknown, occupant = await dayz_test_tool._sample_box_occupancy(
            Runtime({
                "occupied": True,
                "runs": [],
                "foreign": [],
                "scan_known": False,
                "port_scan_known": True,
            })
        )
        self.assertIsNone(unknown)
        self.assertIsNone(occupant)

    async def test_f3_preflight_does_not_accredit_another_root(self) -> None:
        tree = tempfile.TemporaryDirectory()
        self.addCleanup(tree.cleanup)
        root = tree.name
        dev = os.path.join(root, "dev")
        source = os.path.join(root, "src")
        missions = os.path.join(dev, "_server", "mpmissions")
        mods = os.path.join(root, "mods")
        other = os.path.join(root, "other")
        for path in (
            source,
            missions,
            os.path.join(mods, "@ExampleMod"),
            os.path.join(mods, "@DayZ_MCP"),
            os.path.join(other, "@Missing"),
        ):
            os.makedirs(path)
        policy = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=dev,
            default_source=source,
            default_base_mods=(),
            mission_roots=(missions,),
            mod_roots=(mods, other),
        )
        sealed = request_path_authority._seal_project_policy_for_test(policy)
        runtime_policy = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=dev,
            mod="ExampleMod",
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(
                ("chernarus", os.path.join(missions, "dayzOffline.chernarusplus")),
                ("livonia", r"C:\missions\livonia"),
                ("sakhal", r"C:\missions\sakhal"),
            ),
            mods_root=mods,
            build_temp_root=os.path.join(root, "tmp"),
            build_source_basename=None,
        )
        os.makedirs(runtime_policy.build_temp_root)

        class Bundle:
            sealed_policies = (sealed,)

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def validated_worker_runtime(self, mod: str, dev_root: str):
                if mod != "ExampleMod" or dev_root != dev:
                    raise ValueError("runtime_policy_invalid")
                if not dayz_test_worker.runtime_policy_acceptable(runtime_policy):
                    raise ValueError("runtime_policy_invalid")
                return runtime_policy

        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle", return_value=Bundle()
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request",
            return_value=type("V", (), {"error_code": None, "missing": (), "warnings": (), "hint": ""})(),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="server",
                preflight=True,
                base_mods=["@Missing"],
                extra_mods=["@DayZ_MCP"],
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "invalid_dayz_test_path_authority")

    async def test_f12_original_directory_keeps_the_request_reason(self) -> None:
        runtime = _Runtime()
        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle", return_value=_Bundle(_sealed(_policy()))
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request",
            return_value=type("V", (), {"error_code": None, "missing": (), "warnings": (), "hint": ""})(),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="server",
                preflight=True,
                project_mod_override=True,
                base_mods=[r"P:\Mods\@ExampleMod"],
                extra_mods=["@DayZ_MCP"],
            )
        self.assertEqual(
            result["error_code"],
            "bad_dayz_test_request:project_mod_override_includes_original",
        )


def _load_app_main():
    path = (
        Path(__file__).resolve().parents[1]
        / "native-launchers"
        / "dayz-test-v1"
        / "src"
        / "app_main.py"
    )
    spec = importlib.util.spec_from_file_location("dayz_test_v1_app_round4", path)
    if spec is None or spec.loader is None:
        raise AssertionError("app_main unavailable")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _closed_runtime_document() -> dict[str, object]:
    return {
        "format_version": 1,
        "projects": [
            {
                "build_source_basename": None,
                "build_temp_root": r"C:\tmp",
                "dev_root": r"C:\dev",
                "diag_executable": r"C:\DayZ\DayZDiag_x64.exe",
                "game_directory": r"C:\DayZ",
                "mission_aliases": {
                    "chernarus": r"C:\missions\chernarus",
                    "livonia": r"C:\missions\livonia",
                    "sakhal": r"C:\missions\sakhal",
                },
                "mod": "ExampleMod",
                "mods_root": r"C:\mods",
            }
        ],
    }


class Round4Tests(unittest.IsolatedAsyncioTestCase):
    def test_f4_runtime_document_decisions_agree(self) -> None:
        from dayz_mcp.native_bundle import VerifiedNativeBundle

        app = _load_app_main()
        missing = _closed_runtime_document()
        del missing["projects"][0]["build_source_basename"]
        unknown = _closed_runtime_document()
        unknown["projects"][0]["extra"] = True
        documents = (
            _closed_runtime_document(),
            missing,
            unknown,
        )

        def accessor(document: dict[str, object]) -> str:
            bundle = object.__new__(VerifiedNativeBundle)
            bundle.worker_runtime_document = document
            try:
                bundle.validated_worker_runtime("ExampleMod", r"C:\dev")
            except ValueError:
                return "refuse"
            return "accept"

        def bootstrap(document: dict[str, object]) -> str:
            try:
                app._validated_worker_runtime(document, "ExampleMod", r"C:\dev")
            except RuntimeError as error:
                self.assertEqual(str(error), "worker_runtime_invalid")
                return "refuse"
            return "accept"

        decisions = [(accessor(item), bootstrap(item)) for item in documents]
        self.assertEqual(decisions, [("accept", "accept"), ("refuse", "refuse"), ("refuse", "refuse")])

    async def test_f4_missing_build_source_basename_is_refused_by_preflight(self) -> None:
        from dayz_mcp.native_bundle import VerifiedNativeBundle

        document = _closed_runtime_document()
        del document["projects"][0]["build_source_basename"]
        bundle = object.__new__(VerifiedNativeBundle)
        bundle.worker_runtime_document = document
        bundle.sealed_policies = _sealed(_policy())
        bundle._streams = []
        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle", return_value=bundle
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request",
            return_value=type("V", (), {"error_code": None, "missing": (), "warnings": (), "hint": ""})(),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="server",
                preflight=True,
                base_mods=[],
                extra_mods=["@DayZ_MCP"],
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "runtime_policy_invalid")

    async def test_f13_replaced_root_fails_public_preflight(self) -> None:
        tree = tempfile.TemporaryDirectory()
        self.addCleanup(tree.cleanup)
        root = tree.name
        dev = os.path.join(root, "dev")
        source = os.path.join(root, "src")
        missions = os.path.join(dev, "_server", "mpmissions")
        mods = os.path.join(root, "mods")
        for path in (
            source,
            os.path.join(missions, "dayzOffline.chernarusplus"),
            os.path.join(mods, "@ExampleMod"),
            os.path.join(mods, "@DayZ_MCP"),
        ):
            os.makedirs(path)
        policy = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=dev,
            default_source=source,
            default_base_mods=(),
            mission_roots=(missions,),
            mod_roots=(mods,),
        )
        sealed = request_path_authority._seal_project_policy_for_test(policy)
        shutil.rmtree(mods)
        os.makedirs(os.path.join(mods, "@ExampleMod"))
        os.makedirs(os.path.join(mods, "@DayZ_MCP"))
        with self.assertRaises(ValueError) as probed:
            request_path_authority._open_sealed_root(sealed.mod_roots[0])
        self.assertEqual(str(probed.exception), "invalid_dayz_test_path_authority")
        runtime_policy = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=dev,
            mod="ExampleMod",
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(
                ("chernarus", os.path.join(missions, "dayzOffline.chernarusplus")),
                ("livonia", r"C:\missions\livonia"),
                ("sakhal", r"C:\missions\sakhal"),
            ),
            mods_root=mods,
            build_temp_root=os.path.join(root, "tmp"),
            build_source_basename=None,
        )
        os.makedirs(runtime_policy.build_temp_root)

        class Bundle:
            sealed_policies = (sealed,)

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def validated_worker_runtime(self, mod: str, dev_root: str):
                if mod != "ExampleMod" or dev_root != dev:
                    raise ValueError("runtime_policy_invalid")
                return runtime_policy

        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle", return_value=Bundle()
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request",
            return_value=type("V", (), {"error_code": None, "missing": (), "warnings": (), "hint": ""})(),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="server",
                preflight=True,
                base_mods=[],
                extra_mods=["@DayZ_MCP"],
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "invalid_dayz_test_path_authority")


class Round5RegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_r5_client_reattach_preflight_survives_stopped_steam(self) -> None:
        """Previous tree: client reattach preflight returns status failed on steam_not_running."""
        runtime = _Runtime()
        with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), patch.object(
            dayz_test_tool.secure_launcher, "load_verified_bundle", return_value=_Bundle(_sealed(_policy()))
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request",
            return_value=type("V", (), {"error_code": None, "missing": (), "warnings": (), "hint": ""})(),
        ), patch.object(
            dayz_test_tool, "evaluate_prerun_desktop",
            return_value=type("D", (), {"error_code": None, "remediation": ""})(),
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session",
            return_value=SteamSessionResult("steam_not_running", None, (), "start steam"),
        ), patch.object(
            steam_preflight,
            "remediate_stale_steam_session",
            side_effect=AssertionError("repair"),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                preflight=True,
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
                auto_remediate_steam=True,
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertTrue(result["preflight"])
        self.assertIsNone(result["error_code"])

    def test_r5_preflight_loader_is_not_dynamic_http(self) -> None:
        """Previous tree: getattr loader in _execute_preflight is a dynamic_http finding."""
        from dayz_mcp.security_runtime_audit import audit_runtime_http

        tools_dir = Path(__file__).resolve().parents[1]
        findings = [
            item
            for item in audit_runtime_http(tools_dir)
            if item.function == "_execute_preflight"
        ]
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
