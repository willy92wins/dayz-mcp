from __future__ import annotations

import dataclasses
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon, dayz_test_tool, server
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import SessionCoordinator
from tests._source_snapshot import source_of
from tests.steam_helpers import FakeSteamGate
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
)
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    IDENTITY_A,
    process,
    snapshot,
)


_RELEASE_SENTENCE = (
    "Runs listed in cleanup.runs_released stay alive as ownerless RUNNING_IDLE; "
    "releasing never stops DayZ; use dayz_test_stop to stop a run."
)
_UNKNOWN_RUN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_LAUNCH_GENERATION = "gen-7ef2-launch"
_LATER_GENERATION = "gen-7ef2-later"


class Fb7ef2LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.paths = RuntimePaths(
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
        self.token_a = acquired["lease_token"]
        self.store = RunManifestStore(self.paths)
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
            id_fn=lambda: "run-7ef2",
            daemon_generation=_LAUNCH_GENERATION,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _request(self) -> dict[str, object]:
        return {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
        }

    def _start(self) -> str:
        launched = process(self.launcher.pid)
        self.guard.snapshots[launched.pid] = snapshot(launched)
        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request())
        self.assertTrue(result.get("ok"), result)
        return str(result["run_id"])

    def test_fb_7ef2_generation_recorded_at_launch(self) -> None:
        source = source_of(daemon._activate_server_coordination)
        lifecycle_call = source[source.index("ProcessLifecycle") :]
        self.assertIn("daemon_generation=daemon_generation", lifecycle_call)
        forwarded: list[object] = []
        original = daemon.ProcessLifecycle

        def wrapping(*args: object, **kwargs: object) -> ProcessLifecycle:
            forwarded.append(kwargs.get("daemon_generation"))
            return original(*args, **kwargs)

        with TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}), patch.object(
                daemon, "_ensure_identity_migration", return_value=None
            ), patch.object(daemon, "ProcessLifecycle", side_effect=wrapping):
                state = daemon.build_server_state(
                    ServerConfig(
                        mode="daemon",
                        key="k",
                        port=0,
                        log_sink=lambda _m: None,
                    ),
                    "k",
                    daemon_generation=_LAUNCH_GENERATION,
                    activate_coordination=True,
                )
            self.assertIsNotNone(state.lifecycle)
            self.assertEqual(state.lifecycle.daemon_generation, _LAUNCH_GENERATION)
            self.assertEqual(forwarded, [_LAUNCH_GENERATION])
            state.root_writer_lease.release()
        run_id = self._start()
        stored = self.lifecycle.manifest.get(run_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.daemon_generation_at_launch, _LAUNCH_GENERATION)
        row = next(
            item
            for item in self.lifecycle.status(IDENTITY_A)["runs"]
            if item["run_id"] == run_id
        )
        self.assertEqual(row["daemon_generation_at_launch"], _LAUNCH_GENERATION)
        self.assertEqual(row["daemon_generation_current"], _LAUNCH_GENERATION)
        self.assertIs(row["generation_changed"], False)

    def test_fb_7ef2_projection_current_and_changed(self) -> None:
        run_id = self._start()
        self.lifecycle.daemon_generation = _LATER_GENERATION
        row = next(
            item
            for item in self.lifecycle.status(IDENTITY_A)["runs"]
            if item["run_id"] == run_id
        )
        self.assertEqual(row["daemon_generation_at_launch"], _LAUNCH_GENERATION)
        self.assertEqual(row["daemon_generation_current"], _LATER_GENERATION)
        self.assertIs(row["generation_changed"], True)
        box_row = next(
            item
            for item in self.lifecycle.box_occupancy()["runs"]
            if item["run_id"] == run_id
        )
        self.assertEqual(box_row["daemon_generation_at_launch"], _LAUNCH_GENERATION)
        self.assertEqual(box_row["daemon_generation_current"], _LATER_GENERATION)
        self.assertIs(box_row["generation_changed"], True)

    def test_fb_7ef2_legacy_row_without_the_field_loads(self) -> None:
        record = process(9102)
        payload = {
            "version": 1,
            "runs": [
                {
                    "run_id": "legacy-disk",
                    "owner_session_id": "A",
                    "owner_lease_id": "lease-A",
                    "state": "RUNNING",
                    "label": "gate",
                    "mod": "@SameMod",
                    "profiles": "profiles",
                    "mission": "mission",
                    "processes": [dataclasses.asdict(record)],
                    "launch_operation_id": None,
                    "launch_request_sha256": None,
                    "launch_acknowledged": True,
                }
            ],
        }
        self.assertNotIn("daemon_generation_at_launch", payload["runs"][0])
        self.paths.runs_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.runs_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        store = RunManifestStore(self.paths)
        loaded = store.get("legacy-disk")
        self.assertIsNotNone(loaded)
        self.assertIsNone(getattr(loaded, "daemon_generation_at_launch", "missing"))
        with_null = dict(payload["runs"][0], daemon_generation_at_launch=None)
        from_null = RunRecord.from_payload(with_null)
        self.assertIsNone(from_null.daemon_generation_at_launch)

    def test_fb_7ef2_projection_reports_a_non_token_launch_generation_as_unknown(
        self,
    ) -> None:
        from types import SimpleNamespace

        from dayz_mcp import process_lifecycle

        for value in (r"C:\Users\x\run.log", "old gen\nnext line", ""):
            with self.subTest(value=value):
                projected = process_lifecycle._generation_projection(
                    SimpleNamespace(daemon_generation_at_launch=value),
                    _LATER_GENERATION,
                )
                self.assertEqual(
                    projected,
                    {
                        "daemon_generation_at_launch": None,
                        "daemon_generation_current": _LATER_GENERATION,
                        "generation_changed": None,
                    },
                )
        minted = "55a1e4f1" * 4
        projected = process_lifecycle._generation_projection(
            SimpleNamespace(daemon_generation_at_launch=minted), minted
        )
        self.assertEqual(projected["daemon_generation_at_launch"], minted)
        self.assertIs(projected["generation_changed"], False)

    def test_fb_7ef2_session_release_description_contains_the_sentence(self) -> None:
        source = source_of(server.build_app)
        self.assertIn(_RELEASE_SENTENCE, source)


class Fb7ef2StopTests(unittest.IsolatedAsyncioTestCase):
    async def _stop(self, lifecycle: dict[str, object]):
        runtime = _Runtime(lifecycle)
        policy = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ):
            return await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

    async def test_fb_7ef2_stop_on_inactive_row_answers_run_not_active(self) -> None:
        row = {
            "run_id": RUN_ID,
            "state": "EXITED",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
            "launch_acknowledged": True,
            "daemon_generation_at_launch": "old-gen",
            "daemon_generation_current": "new-gen",
            "generation_changed": True,
            "reason": "reaped",
        }
        result = await self._stop({"runs": [row]})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["error_code"], "run_not_active")
        self.assertEqual(result["state"], "EXITED")
        self.assertEqual(result["reason"], "reaped")
        self.assertEqual(result["daemon_generation_at_launch"], "old-gen")
        self.assertEqual(result["daemon_generation_current"], "new-gen")
        self.assertIs(result["generation_changed"], True)

    async def test_fb_7ef2_stop_on_an_unknown_id_raises_run_not_found(self) -> None:
        runtime = _Runtime({"runs": [], "retired_run_diagnostics": []})
        policy = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ):
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_stop(
                    runtime, _UNKNOWN_RUN_ID
                )
        self.assertEqual(caught.exception.code, "run_not_found")

    async def test_fb_7ef2_stop_envelope_omits_non_token_reason(self) -> None:
        base = {
            "run_id": RUN_ID,
            "state": "EXITED",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
            "launch_acknowledged": True,
            "daemon_generation_at_launch": "old-gen",
            "daemon_generation_current": "new-gen",
            "generation_changed": True,
        }
        for reason in (r"C:\Users\x\run.log", "processes gone"):
            with self.subTest(reason=reason):
                result = await self._stop({"runs": [dict(base, reason=reason)]})
                self.assertEqual(result["error_code"], "run_not_active")
                self.assertNotIn("reason", result)
                self.assertEqual(result["state"], "EXITED")
        kept = await self._stop(
            {"runs": [dict(base, reason="all_processes_gone_or_foreign")]}
        )
        self.assertEqual(kept["error_code"], "run_not_active")
        self.assertEqual(kept["reason"], "all_processes_gone_or_foreign")

    async def test_fb_7ef2_stop_never_publishes_a_non_token_generation(self) -> None:
        base = {
            "run_id": RUN_ID,
            "state": "EXITED",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
            "launch_acknowledged": True,
            "daemon_generation_at_launch": "old-gen",
            "daemon_generation_current": "new-gen",
            "generation_changed": True,
        }
        leak = r"C:\Users\x\run.log"
        for field, value in (
            ("daemon_generation_at_launch", leak),
            ("daemon_generation_current", leak),
            ("daemon_generation_at_launch", "old gen\nnext line"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    await self._stop({"runs": [dict(base, **{field: value})]})
                self.assertEqual(caught.exception.code, "run_not_active")
                self.assertNotIn(value, str(caught.exception))

        diagnostic = {
            "run_id": RUN_ID,
            "event": "run_reaped",
            "reason": "all_processes_gone_or_foreign",
            "decision": "reaped",
            "state": "EXITED",
            "daemon_generation_at_launch": leak,
            "daemon_generation_current": "new-gen",
            "generation_changed": True,
        }
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            await self._stop({"runs": [], "retired_run_diagnostics": [diagnostic]})
        self.assertEqual(caught.exception.code, "run_not_found")
        self.assertNotIn(leak, str(caught.exception))

        minted = {
            "daemon_generation_at_launch": "55a1e4f1" * 4,
            "daemon_generation_current": "eca81d26" * 4,
        }
        result = await self._stop({"runs": [dict(base, **minted)]})
        self.assertEqual(result["error_code"], "run_not_active")
        self.assertEqual(result["daemon_generation_at_launch"], "55a1e4f1" * 4)
        self.assertEqual(result["daemon_generation_current"], "eca81d26" * 4)
