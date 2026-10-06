"""Public visibility of a mission storage rotation (c8c7).

The rotation is produced by prepare_storage inside a real launch, stored on
the run, and read back through lifecycle status and _execute_request. Nothing
here passes a RotationResult into _compact_result.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_storage, dayz_test_tool
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    IDENTITY_A,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate


RUN_ROTATED = "11111111-1111-4111-8111-111111111111"
RUN_REUSE = "22222222-2222-4222-8222-222222222222"
RUN_RETRY = "33333333-3333-4333-8333-333333333333"
RUN_OTHER = "44444444-4444-4444-8444-444444444444"
OP_ROTATED = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SEAL_A = "a" * 64
SEAL_B = "b" * 64


def _observation(run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "storage_rotated": True,
        "storage_backup": "storage_1.modset-" + run_id[:8],
        "storage_reset_notice": dayz_test_storage.RESET_NOTICE,
    }


def _terminal_bytes(
    *,
    ok: bool,
    run_id: str | None,
    error_code: str | None,
    exit_code: int,
    cleanup_degraded: bool,
    attempt_run_id: str | None = None,
    launch_operation_id: str | None = None,
) -> bytes:
    payload: dict[str, object] = {
        "cleanup_degraded": cleanup_degraded,
        "error_code": error_code,
        "exit_code": exit_code,
        "ok": ok,
        "run_id": run_id,
    }
    if attempt_run_id is not None:
        payload["attempt_run_id"] = attempt_run_id
    if launch_operation_id is not None:
        payload["launch_operation_id"] = launch_operation_id
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class _StatusRuntime:
    def __init__(self, status: Callable[[], object]) -> None:
        self._status = status
        self.status_calls = 0
        self.active_lease_token = None
        self.active_ticket = None
        self.active_operation_id = None
        self.daemon_policy = None

    async def lifecycle_status(self) -> object:
        self.status_calls += 1
        return self._status()

    async def reconcile_idle_session(self) -> dict[str, object]:
        return {}

    async def bridge_status_payload(self) -> dict[str, object]:
        return {"ready": True}

    async def lifecycle_close(self, run_id: str) -> dict[str, object]:
        return {}

    async def lifecycle_reap(self, run_id: str) -> dict[str, object]:
        return {}


class StorageResetVisibilityTest(unittest.IsolatedAsyncioTestCase):
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
        self.paths.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=self.audit,
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.ids = iter((RUN_ROTATED, RUN_REUSE, RUN_RETRY))
        self.lifecycle = self._lifecycle(RunManifestStore(self.paths))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _lifecycle(self, store: RunManifestStore) -> ProcessLifecycle:
        return ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: next(self.ids),
        )

    def _mission_dir(self) -> Path:
        return self.root / "mpmissions" / "dayzOffline"

    def _plant_storage(self, *, marker_seal: str | None) -> Path:
        directory = self._mission_dir()
        tree = directory / "storage_1"
        (tree / "players").mkdir(parents=True)
        (tree / "data.bin").write_bytes(b"world-and-characters")
        if marker_seal is not None:
            (directory / "storage_1.modset.json").write_text(
                json.dumps(
                    {
                        "schema_version": dayz_test_storage.MARKER_SCHEMA_VERSION,
                        "algorithm": dayz_test_storage.MARKER_ALGORITHM,
                        "seal": marker_seal,
                        "project": "SameMod",
                    }
                ),
                encoding="utf-8",
            )
        return directory

    def _request(self, seal: str) -> dict[str, object]:
        return {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "server",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": str(self._mission_dir()),
            "storage_seal": seal,
        }

    def _arm(self) -> None:
        launched = process(self.launcher.pid, "server")
        self.guard.snapshots[self.launcher.pid] = snapshot(launched)

    def _row(self, run_id: str) -> dict[str, object]:
        payload = self.lifecycle.status(IDENTITY_A)
        matches = [
            item
            for item in payload["runs"]
            if isinstance(item, dict) and item.get("run_id") == run_id
        ]
        self.assertEqual(len(matches), 1, payload)
        return matches[0]

    def _backups(self) -> list[str]:
        mission = self._mission_dir()
        return sorted(
            entry.name
            for entry in mission.iterdir()
            if entry.is_dir() and entry.name.startswith("storage_1.modset-")
        )

    async def _public(
        self,
        status: Callable[[], object],
        *,
        ok: bool,
        run_id: str | None,
        cleanup_degraded: bool | None = None,
        attempt_run_id: str | None = None,
        launch_operation_id: str | None = None,
        error_code: str | None = None,
        public_mode: str = "server",
        expected_run_id: str | None = None,
    ) -> dict[str, object]:
        runtime = _StatusRuntime(status)
        if cleanup_degraded is None:
            cleanup_degraded = not ok
        if error_code is None and not ok:
            error_code = "build_failed"
        body = _terminal_bytes(
            ok=ok,
            run_id=run_id,
            error_code=error_code,
            exit_code=0 if ok else 1,
            cleanup_degraded=cleanup_degraded,
            attempt_run_id=attempt_run_id,
            launch_operation_id=launch_operation_id,
        )

        async def execute(_raw: object, **kwargs: object) -> int:
            sink = kwargs["output_sink"]
            sink("stdout", body)
            sink("stderr", b"")
            return 0 if ok else 1

        with patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=execute,
        ):
            result = await dayz_test_tool._execute_request(
                runtime,
                opened_launcher=None,
                verified_bundle=None,
                raw_request=b"{}",
                policy=type("Policy", (), {"mod": "SameMod"})(),
                public_mode=public_mode,
                artifacts_paths=[],
                started_at=0.0,
                preflight=False,
                expected_run_id=expected_run_id,
                progress_cb=None,
            )
        # A failure that never names a run does not read status. Reading it
        # would be how another session's run gets attributed to this attempt.
        if ok or run_id is not None or attempt_run_id is not None:
            self.assertGreaterEqual(runtime.status_calls, 1)
        return result

    def test_mismatch_rotation_is_on_the_run_and_survives_reload_and_ack(self) -> None:
        self._arm()
        self._plant_storage(marker_seal=SEAL_B)
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        self.assertIs(started.get("ok"), True, started)
        run_id = str(started["run_id"])
        row = self._row(run_id)
        self.assertIs(row["storage_rotated"], True)
        self.assertEqual(row["storage_reset_notice"], dayz_test_storage.RESET_NOTICE)
        self.assertEqual(row["storage_backup"], self._backups()[0])
        durable = json.loads(self.paths.runs_path.read_text(encoding="utf-8"))
        stored = next(item for item in durable["runs"] if item["run_id"] == run_id)
        self.assertIs(stored["storage_rotated"], True)
        self.assertEqual(stored["storage_backup"], row["storage_backup"])
        # Acknowledgement must not clear the historical reset.
        run = self.lifecycle.manifest.get(run_id)
        assert run is not None
        self.assertIs(run.launch_acknowledged, True)
        reloaded = RunManifestStore(self.paths)
        kept = reloaded.get(run_id)
        assert kept is not None
        self.assertIs(kept.storage_rotated, True)
        self.assertEqual(kept.storage_backup, row["storage_backup"])
        self.assertEqual(kept.storage_reset_notice, dayz_test_storage.RESET_NOTICE)
        other = RunRecord(
            RUN_OTHER, None, None, "EXITED", "other", "@SameMod", "profiles", "mission", []
        )
        self.assertIsNone(other.storage_rotated)

    async def test_public_result_reports_the_rotation_for_that_run_only(self) -> None:
        self._arm()
        self._plant_storage(marker_seal=SEAL_B)
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        self.assertIs(started.get("ok"), True, started)
        run_id = str(started["run_id"])

        def status() -> dict[str, object]:
            payload = self.lifecycle.status(IDENTITY_A)
            runs = list(payload["runs"])
            runs.append(
                {
                    "run_id": RUN_OTHER,
                    "state": "EXITED",
                    "storage_rotated": False,
                    "storage_backup": None,
                    "storage_reset_notice": None,
                }
            )
            return {**payload, "runs": runs}

        shown = await self._public(status, ok=True, run_id=run_id)
        self.assertIs(shown["storage_rotated"], True)
        self.assertEqual(shown["storage_reset_notice"], dayz_test_storage.RESET_NOTICE)
        self.assertEqual(shown["storage_backup"], self._row(run_id)["storage_backup"])
        other = await self._public(status, ok=True, run_id=RUN_OTHER)
        self.assertIs(other["storage_rotated"], False)
        self.assertIsNone(other["storage_backup"])
        self.assertIsNone(other["storage_reset_notice"])

    def test_spawn_failure_keeps_the_reset_and_a_retry_recovers_it(self) -> None:
        self._plant_storage(marker_seal=SEAL_B)

        def explode(_argv: list[str], _cwd: str, _style: str) -> object:
            raise OSError("spawn failed")

        self.lifecycle.launcher = explode
        failed = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        self.assertEqual(failed.get("error"), "lifecycle_start_failed", failed)
        run_id = str(failed["run_id"])
        row = self._row(run_id)
        self.assertEqual(row["state"], "EXITED")
        self.assertIs(row["storage_rotated"], True)
        backup = str(row["storage_backup"])
        self.assertEqual(self._backups(), [backup])
        journals_before = sorted(
            path.name
            for path in self._mission_dir().iterdir()
            if "rotation" in path.name
        )
        # Reload drops the EXITED row. The observation for that run id stays,
        # and so do the journal and the backup.
        reloaded_store = RunManifestStore(self.paths)
        self.assertIsNone(reloaded_store.get(run_id))
        kept = [
            item
            for item in reloaded_store.storage_observations()
            if item.get("run_id") == run_id
        ]
        self.assertEqual(len(kept), 1, kept)
        self.assertIs(kept[0]["storage_rotated"], True)
        self.assertEqual(kept[0]["storage_backup"], backup)
        self.assertEqual(
            kept[0]["storage_reset_notice"], dayz_test_storage.RESET_NOTICE
        )
        journals_after = sorted(
            path.name
            for path in self._mission_dir().iterdir()
            if "rotation" in path.name
        )
        self.assertEqual(journals_after, journals_before)
        self.assertEqual(self._backups(), [backup])
        self.launcher = FakeLauncher()
        self._arm()
        self.lifecycle = self._lifecycle(reloaded_store)
        retried = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self._request(SEAL_A)
        )
        self.assertIs(retried.get("ok"), True, retried)
        retry_row = self._row(str(retried["run_id"]))
        self.assertIs(retry_row["storage_rotated"], True)
        self.assertEqual(retry_row["storage_backup"], backup)
        self.assertEqual(
            retry_row["storage_reset_notice"], dayz_test_storage.RESET_NOTICE
        )
        self.assertEqual(self._backups(), [backup])

    async def test_failed_terminal_reads_the_rotation_before_run_id_normalization(
        self,
    ) -> None:
        self._plant_storage(marker_seal=SEAL_B)

        def explode(_argv: list[str], _cwd: str, _style: str) -> object:
            raise OSError("spawn failed")

        self.lifecycle.launcher = explode
        failed = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        run_id = str(failed["run_id"])
        result = await self._public(
            lambda: self.lifecycle.status(IDENTITY_A),
            ok=False,
            run_id=run_id,
            attempt_run_id=run_id,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["run_id"], run_id)
        self.assertIs(result["storage_rotated"], True)
        self.assertEqual(
            result["storage_reset_notice"], dayz_test_storage.RESET_NOTICE
        )

    def test_matching_seal_reuse_is_a_measured_false(self) -> None:
        self._arm()
        self._plant_storage(marker_seal=SEAL_A)
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        self.assertIs(started.get("ok"), True, started)
        row = self._row(str(started["run_id"]))
        self.assertIs(row["storage_rotated"], False)
        self.assertIsNone(row["storage_backup"])
        self.assertIsNone(row["storage_reset_notice"])
        self.assertEqual(self._backups(), [])

    async def test_matching_seal_reuse_is_false_in_the_public_result(self) -> None:
        self._arm()
        self._plant_storage(marker_seal=SEAL_A)
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        run_id = str(started["run_id"])
        result = await self._public(
            lambda: self.lifecycle.status(IDENTITY_A), ok=True, run_id=run_id
        )
        self.assertIs(result["storage_rotated"], False)
        self.assertIsNone(result["storage_backup"])
        self.assertIsNone(result["storage_reset_notice"])

    def test_legacy_record_without_rotation_keys_stays_unknown(self) -> None:
        record = RunRecord(
            RUN_OTHER,
            "A",
            "lease-A",
            "RUNNING",
            "old",
            "@SameMod",
            "profiles",
            "mission",
            [process(9100, "server")],
        )
        payload = dataclasses.asdict(record)
        for key in ("storage_rotated", "storage_backup", "storage_reset_notice"):
            payload.pop(key, None)
        self.paths.runs_path.write_text(
            json.dumps({"version": 1, "runs": [payload]}), encoding="utf-8"
        )
        store = RunManifestStore(self.paths)
        loaded = store.get(RUN_OTHER)
        assert loaded is not None
        self.assertIsNone(loaded.storage_rotated)
        self.assertIsNone(loaded.storage_backup)
        self.assertIsNone(loaded.storage_reset_notice)

    async def test_unrelated_run_during_a_pre_run_failure_stays_null(self) -> None:
        unrelated = {
            "run_id": RUN_OTHER,
            "state": "EXITED",
            "launch_operation_id": OP_ROTATED,
            "storage_rotated": True,
            "storage_backup": "storage_1.modset-unrelated",
            "storage_reset_notice": dayz_test_storage.RESET_NOTICE,
        }
        result = await self._public(
            lambda: {"runs": [unrelated]},
            ok=False,
            run_id=None,
            cleanup_degraded=False,
            error_code="build_failed",
        )
        self.assertIsNone(result["run_id"])
        self.assertIsNone(result["storage_rotated"])
        self.assertIsNone(result["storage_backup"])
        self.assertIsNone(result["storage_reset_notice"])

    async def test_attempt_run_id_selects_this_rotation_among_concurrent_runs(self) -> None:
        self._plant_storage(marker_seal=SEAL_B)

        def explode(_argv: list[str], _cwd: str, _style: str) -> object:
            raise OSError("spawn failed")

        self.lifecycle.launcher = explode
        failed = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self._request(SEAL_A)
        )
        run_id = str(failed["run_id"])
        backup = str(self._row(run_id)["storage_backup"])
        unrelated = {
            "run_id": RUN_OTHER,
            "state": "EXITED",
            "storage_rotated": True,
            "storage_backup": "storage_1.modset-unrelated",
            "storage_reset_notice": dayz_test_storage.RESET_NOTICE,
        }

        def status() -> dict[str, object]:
            payload = self.lifecycle.status(IDENTITY_A)
            return {**payload, "runs": [*payload["runs"], unrelated]}

        result = await self._public(
            status,
            ok=False,
            run_id=None,
            cleanup_degraded=False,
            error_code="worker_failed",
            attempt_run_id=run_id,
        )
        self.assertIsNone(result["run_id"])
        self.assertIs(result["storage_rotated"], True)
        self.assertEqual(result["storage_backup"], backup)
        self.assertEqual(
            result["storage_reset_notice"], dayz_test_storage.RESET_NOTICE
        )

    async def test_attempt_run_id_resolves_when_an_earlier_status_read_fails(self) -> None:
        self._plant_storage(marker_seal=SEAL_B)

        def explode(_argv: list[str], _cwd: str, _style: str) -> object:
            raise OSError("spawn failed")

        self.lifecycle.launcher = explode
        failed = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self._request(SEAL_A)
        )
        run_id = str(failed["run_id"])
        calls = {"n": 0}

        def status() -> dict[str, object]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("status down")
            return self.lifecycle.status(IDENTITY_A)

        result = await self._public(
            status,
            ok=False,
            run_id=None,
            cleanup_degraded=False,
            error_code="worker_failed",
            attempt_run_id=run_id,
        )
        self.assertIs(result["storage_rotated"], True)
        self.assertEqual(
            result["storage_backup"], self._row(run_id)["storage_backup"]
        )

    def test_two_completed_rotations_to_the_same_seal_leave_the_retry_unknown(self) -> None:
        from dayz_mcp import process_lifecycle

        mission = self._plant_storage(marker_seal=SEAL_B)
        rotations = []
        for seal, now, txid in (
            (SEAL_A, 1_000_000.0, "1" * 32),
            (SEAL_B, 2_000_000.0, "2" * 32),
            (SEAL_A, 3_000_000.0, "3" * 32),
        ):
            if not (mission / "storage_1").exists():
                self._plant_storage(marker_seal=None)
            rotations.append(
                dayz_test_storage.prepare_storage(
                    str(mission), seal=seal, project="SameMod", now=now, txid=txid
                )
            )
        self.assertTrue(all(item.storage_rotated for item in rotations))
        self.assertFalse((mission / "storage_1").exists())
        # B->A twice: two completed journals for seal A, two backups on disk.
        self.assertIs(
            process_lifecycle._pending_completed_rotation(str(mission), SEAL_A),
            process_lifecycle._AMBIGUOUS_PENDING_ROTATION,
        )

        def explode(_argv: list[str], _cwd: str, _style: str) -> object:
            raise OSError("spawn failed")

        self.lifecycle.launcher = explode
        failed = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        row = self._row(str(failed["run_id"]))
        # Unknown, neither a guessed backup nor a measured non-rotation.
        self.assertIsNone(row.get("storage_rotated"))
        self.assertIsNone(row.get("storage_backup"))
        self.assertIsNone(row.get("storage_reset_notice"))

    async def test_client_dead_after_ack_keeps_this_runs_storage(self) -> None:
        # A successful terminal names its run; the projection that turns the
        # launch into client_dead_after_ack must not drop that correlation.
        row = {
            "run_id": RUN_ROTATED,
            "state": "RUNNING",
            "storage_rotated": True,
            "storage_backup": "storage_1.modset-own",
            "storage_reset_notice": dayz_test_storage.RESET_NOTICE,
            "processes": [{"role": "server", "pid": 9100}, {"role": "client", "pid": 9200}],
        }
        with patch.object(dayz_test_tool, "_pid_alive", side_effect=lambda pid: pid == 9100):
            result = await self._public(
                lambda: {"runs": [row]},
                ok=True,
                run_id=RUN_ROTATED,
                public_mode="all",
            )
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertEqual(result["run_id"], RUN_ROTATED)
        self.assertIs(result["storage_rotated"], True)
        self.assertEqual(result["storage_backup"], "storage_1.modset-own")
        self.assertEqual(
            result["storage_reset_notice"], dayz_test_storage.RESET_NOTICE
        )

    async def test_old_worker_terminal_without_attempt_run_id_is_null(self) -> None:
        unrelated = {
            "run_id": RUN_OTHER,
            "state": "EXITED",
            "storage_rotated": True,
            "storage_backup": "storage_1.modset-unrelated",
            "storage_reset_notice": dayz_test_storage.RESET_NOTICE,
        }
        result = await self._public(
            lambda: {"runs": [unrelated]},
            ok=False,
            run_id=None,
            cleanup_degraded=False,
            error_code="worker_failed",
        )
        self.assertIsNone(result["storage_rotated"])
        self.assertIsNone(result["storage_backup"])
        self.assertIsNone(result["storage_reset_notice"])

    def test_post_rotation_manifest_failure_still_settles(self) -> None:
        self._plant_storage(marker_seal=SEAL_B)
        real = self.lifecycle.manifest.replace
        calls = {"n": 0}

        def replace(run: RunRecord) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk")
            real(run)

        self.lifecycle.manifest.replace = replace  # type: ignore[method-assign]
        failed = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self._request(SEAL_A)
        )
        self.assertEqual(failed.get("error"), "manifest_failed", failed)
        self.assertNotIn("hint", failed)
        run_id = str(failed["run_id"])
        row = self._row(run_id)
        self.assertIs(row["storage_rotated"], True)
        self.assertEqual(
            row["storage_reset_notice"], dayz_test_storage.RESET_NOTICE
        )

    async def test_reloaded_observation_is_visible_without_the_exited_row(self) -> None:
        self._plant_storage(marker_seal=SEAL_B)

        def explode(_argv: list[str], _cwd: str, _style: str) -> object:
            raise OSError("spawn failed")

        self.lifecycle.launcher = explode
        failed = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self._request(SEAL_A)
        )
        run_id = str(failed["run_id"])
        backup = str(self._row(run_id)["storage_backup"])
        reloaded = self._lifecycle(RunManifestStore(self.paths))
        payload = reloaded.status(IDENTITY_A)
        self.assertFalse(
            any(
                isinstance(item, dict) and item.get("run_id") == run_id
                for item in payload["runs"]
            )
        )
        result = await self._public(
            lambda: reloaded.status(IDENTITY_A),
            ok=False,
            run_id=run_id,
            attempt_run_id=run_id,
        )
        self.assertIs(result["storage_rotated"], True)
        self.assertEqual(result["storage_backup"], backup)
        self.assertEqual(
            result["storage_reset_notice"], dayz_test_storage.RESET_NOTICE
        )

    async def test_unavailable_status_yields_null_not_false(self) -> None:
        def status() -> object:
            raise RuntimeError("status down")

        result = await self._public(status, ok=False, run_id=RUN_ROTATED)
        self.assertIsNone(result["storage_rotated"])
        self.assertIsNone(result["storage_backup"])
        self.assertIsNone(result["storage_reset_notice"])

    def test_reload_does_not_touch_rotation_journals(self) -> None:
        digest_before = self._journal_digest_or_empty()
        self._arm()
        self._plant_storage(marker_seal=SEAL_B)
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        self.assertIs(started.get("ok"), True, started)
        digest_rotated = self._journal_digest_or_empty()
        self.assertNotEqual(digest_rotated, digest_before)
        RunManifestStore(self.paths)
        self.assertEqual(self._journal_digest_or_empty(), digest_rotated)

    def _plant_impossible_rotation_state(self) -> None:
        """A journal in a physical state no rotation sequence produces.

        The journal is valid, its phase says the tree was already moved, and
        both `storage_1` and the backup are present: prepare_storage refuses
        the launch with journal_state_impossible instead of classifying.
        """
        mission = self._plant_storage(marker_seal=None)
        backup = "storage_1.modset-impossible"
        (mission / ("storage_1.modset.rotation." + "c" * 32 + ".json")).write_text(
            json.dumps(
                {
                    "schema_version": dayz_test_storage.MARKER_SCHEMA_VERSION,
                    "txid": "c" * 32,
                    "phase": dayz_test_storage.PHASE_STORAGE_MOVED,
                    "new_seal": SEAL_A,
                    "old_seal": None,
                    "project": "SameMod",
                    "storage_backup": backup,
                    "marker_backup": backup + ".marker.json",
                }
            ),
            encoding="utf-8",
        )
        tree = mission / backup
        (tree / "players").mkdir(parents=True)
        (tree / "data.bin").write_bytes(b"old-world")

    def _refused_launch(self) -> str:
        failed = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self._request(SEAL_A)
        )
        self.assertEqual(failed.get("error"), "storage_recovery_required", failed)
        return str(failed["run_id"])

    def test_refused_classification_settles_null_not_a_measured_false(self) -> None:
        self._plant_impossible_rotation_state()
        run_id = self._refused_launch()
        row = self._row(run_id)
        self.assertEqual(row["state"], "EXITED")
        self.assertIsNone(row["storage_rotated"])
        self.assertIsNone(row["storage_backup"])
        self.assertIsNone(row["storage_reset_notice"])
        # The durable list keeps unknown off it, on load as anywhere else.
        reloaded = RunManifestStore(self.paths)
        self.assertFalse(
            [
                item
                for item in reloaded.storage_observations()
                if item.get("run_id") == run_id
            ]
        )

    async def test_refused_classification_public_result_stays_null(self) -> None:
        self._plant_impossible_rotation_state()
        run_id = self._refused_launch()
        result = await self._public(
            lambda: self.lifecycle.status(IDENTITY_A),
            ok=False,
            run_id=None,
            cleanup_degraded=False,
            error_code="storage_recovery_required",
            attempt_run_id=run_id,
        )
        self.assertEqual(result["error_code"], "storage_recovery_required")
        self.assertIsNone(result["storage_rotated"])
        self.assertIsNone(result["storage_backup"])
        self.assertIsNone(result["storage_reset_notice"])

    def _visible_run_payload(self) -> dict[str, object]:
        record = RunRecord(
            RUN_OTHER,
            "A",
            "lease-A",
            "RUNNING",
            "other",
            "@SameMod",
            "profiles",
            "mission",
            [process(9100, "server")],
        )
        return dataclasses.asdict(record)

    def _write_manifest(
        self, runs: list[dict[str, object]], observations: object
    ) -> None:
        self.paths.runs_path.write_text(
            json.dumps({"version": 1, "runs": runs, "storage_observations": observations}),
            encoding="utf-8",
        )

    def test_more_than_the_bound_keeps_the_newest_entries(self) -> None:
        entries = [
            _observation("11111111-1111-4111-8111-" + f"{index:012d}")
            for index in range(33)
        ]
        self._write_manifest([self._visible_run_payload()], entries)
        store = RunManifestStore(self.paths)
        self.assertIsNotNone(store.get(RUN_OTHER))
        loaded = store.storage_observations()
        self.assertEqual(len(loaded), 32)
        self.assertEqual(loaded[0]["run_id"], entries[1]["run_id"])
        self.assertEqual(loaded[-1]["run_id"], entries[-1]["run_id"])

    def test_a_malformed_entry_drops_only_itself(self) -> None:
        good = _observation(RUN_ROTATED)
        malformed = {
            "run_id": RUN_RETRY,
            "storage_rotated": "yes",
            "storage_backup": None,
            "storage_reset_notice": None,
        }
        self._write_manifest(
            [self._visible_run_payload()], [good, malformed, _observation(RUN_REUSE)]
        )
        store = RunManifestStore(self.paths)
        self.assertIsNotNone(store.get(RUN_OTHER))
        loaded = store.storage_observations()
        self.assertEqual([item["run_id"] for item in loaded], [RUN_ROTATED, RUN_REUSE])

    def test_broken_entries_are_dropped_one_by_one(self) -> None:
        good = _observation(RUN_ROTATED)
        not_a_dict = ["nope"]
        missing_key = {
            "run_id": RUN_RETRY,
            "storage_rotated": False,
            "storage_backup": None,
        }
        bad_backup = {
            "run_id": RUN_OTHER,
            "storage_rotated": True,
            "storage_backup": "with/slash",
            "storage_reset_notice": dayz_test_storage.RESET_NOTICE,
        }
        self._write_manifest(
            [self._visible_run_payload()],
            [not_a_dict, good, missing_key, bad_backup],
        )
        store = RunManifestStore(self.paths)
        self.assertIsNotNone(store.get(RUN_OTHER))
        loaded = store.storage_observations()
        self.assertEqual([item["run_id"] for item in loaded], [RUN_ROTATED])

    def test_a_malformed_twin_drops_the_run_id_before_validation(self) -> None:
        """Repeated ids are counted on the original list, before validation.

        A valid observation plus a malformed twin of the same run id loads as
        no entry for that id, in either order, and a twin that is only
        {"run_id": <same>} does the same. The runs of that runs.json stay
        visible through RunManifestStore._load.
        """
        good = _observation(RUN_ROTATED)
        malformed = dict(good, storage_rotated="broken")
        id_only = {"run_id": RUN_ROTATED}
        cases = (
            [good, malformed],
            [malformed, good],
            [good, id_only],
            [id_only, good],
        )
        for observations in cases:
            self._write_manifest(
                [self._visible_run_payload()],
                [*observations, _observation(RUN_REUSE)],
            )
            # Construction is the _load path the daemon uses for runs.json.
            store = RunManifestStore(self.paths)
            self.assertIsNotNone(store.get(RUN_OTHER))
            loaded = store.storage_observations()
            self.assertEqual(
                [item["run_id"] for item in loaded],
                [RUN_REUSE],
                observations,
            )

    def test_a_duplicated_run_id_is_dropped_entirely(self) -> None:
        self._write_manifest(
            [self._visible_run_payload()],
            [
                _observation(RUN_ROTATED),
                _observation(RUN_REUSE),
                _observation(RUN_ROTATED),
            ],
        )
        store = RunManifestStore(self.paths)
        self.assertIsNotNone(store.get(RUN_OTHER))
        loaded = store.storage_observations()
        self.assertEqual([item["run_id"] for item in loaded], [RUN_REUSE])

    def test_an_extra_key_is_ignored_and_not_copied(self) -> None:
        entry = dict(_observation(RUN_ROTATED), future_field=42)
        self._write_manifest([self._visible_run_payload()], [entry])
        store = RunManifestStore(self.paths)
        self.assertIsNotNone(store.get(RUN_OTHER))
        loaded = store.storage_observations()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(
            set(loaded[0]),
            {"run_id", "storage_rotated", "storage_backup", "storage_reset_notice"},
        )

    def test_a_non_list_value_loads_as_empty(self) -> None:
        for value in ("nope", None, {"run_id": RUN_ROTATED}):
            self._write_manifest([self._visible_run_payload()], value)
            store = RunManifestStore(self.paths)
            self.assertIsNotNone(store.get(RUN_OTHER))
            self.assertEqual(store.storage_observations(), [])

    async def test_creating_launch_still_reports_its_rotation_and_backup(self) -> None:
        self._arm()
        self._plant_storage(marker_seal=SEAL_B)
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        self.assertIs(started.get("ok"), True, started)
        run_id = str(started["run_id"])
        backup = str(self._row(run_id)["storage_backup"])
        result = await self._public(
            lambda: self.lifecycle.status(IDENTITY_A),
            ok=True,
            run_id=run_id,
            expected_run_id=None,
        )
        self.assertIs(result["storage_rotated"], True)
        self.assertEqual(result["storage_backup"], backup)
        self.assertEqual(
            result["storage_reset_notice"], dayz_test_storage.RESET_NOTICE
        )

    async def test_a_reattach_of_an_existing_run_reports_null(self) -> None:
        self._arm()
        self._plant_storage(marker_seal=SEAL_B)
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        self.assertIs(started.get("ok"), True, started)
        run_id = str(started["run_id"])
        result = await self._public(
            lambda: self.lifecycle.status(IDENTITY_A),
            ok=True,
            run_id=run_id,
            expected_run_id=run_id,
            public_mode="client",
        )
        self.assertIsNone(result["storage_rotated"])
        self.assertIsNone(result["storage_backup"])
        self.assertIsNone(result["storage_reset_notice"])

    async def test_a_stop_of_an_existing_run_reports_null(self) -> None:
        self._arm()
        self._plant_storage(marker_seal=SEAL_B)
        started = self.lifecycle.start_run(IDENTITY_A, self.token_a, self._request(SEAL_A))
        self.assertIs(started.get("ok"), True, started)
        run_id = str(started["run_id"])
        result = await self._public(
            lambda: self.lifecycle.status(IDENTITY_A),
            ok=True,
            run_id=run_id,
            expected_run_id=run_id,
            public_mode="stop",
        )
        self.assertIsNone(result["storage_rotated"])
        self.assertIsNone(result["storage_backup"])
        self.assertIsNone(result["storage_reset_notice"])

    def _journal_digest_or_empty(self) -> str:
        mission = self.root / "mpmissions" / "dayzOffline"
        if not mission.exists():
            return ""
        rows = []
        for path in sorted(mission.iterdir()):
            if "rotation" not in path.name and not path.name.startswith("storage_1"):
                continue
            if path.is_file():
                rows.append(path.name + ":" + hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                rows.append(path.name + ":dir")
        return "\n".join(rows)

