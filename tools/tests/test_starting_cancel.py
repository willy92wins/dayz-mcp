"""ah-starting-cancel-20261109-1444: cancel an owned launch in STARTING.

Offline only: no DayZ launch, no daemon, no network, no PBO. The daemon's
start_run owns ``_operation_lock`` for the whole launch window. A concurrent
same-session stop must not wait for that lock and then kill a run that has
already become RUNNING; it signals the in-flight launch through the existing
lifecycle protocol, the launch terminates exactly its owned process and
settles the row terminal, and the stop answers from that durable settlement
or refuses with a named fail-closed code.

The classes below are the GATES contract names; each one drives product code
(ProcessLifecycle, execute_dayz_test_run/stop) with fake host primitives.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_request, dayz_test_tool
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _policy,
    _sealed,
    _terminal,
)
from tests.lifecycle_helpers import stamp_launcher
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    IDENTITY_A,
    IDENTITY_B,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate

_OP_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"


class _BlockingLauncher:
    """A launcher that holds the launch window open until the test releases it."""

    def __init__(self, pid: int = 91001, *, confirmed_exit: bool = True) -> None:
        self.pid = pid
        self.confirmed_exit = confirmed_exit
        self.entered = threading.Event()
        self.release = threading.Event()
        self.terminate_calls = 0

    def __call__(self, argv: list[str], cwd: str, window_style: str):
        self.entered.set()
        if not self.release.wait(5.0):
            raise TimeoutError("launcher held too long")
        return self

    def terminate(self) -> None:
        self.terminate_calls += 1

    def wait(self, timeout: float) -> int:
        if not self.confirmed_exit:
            raise TimeoutError("still running")
        return 0

    def poll(self):
        return 0 if self.confirmed_exit else None


class _Bindings:
    """The one binding method a cancelled launch touches."""

    def __init__(self) -> None:
        self.minted = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.retired: list[tuple[str, str, str]] = []

    def prepare(self, run_id: str, role: str, profiles: str) -> str:
        return self.minted

    def retire_role(self, run_id: str, role: str, reason: str) -> None:
        self.retired.append((run_id, role, reason))

    def retire_run(self, run_id: str, reason: str) -> None:
        return None


def _fresh_request(game: Path, mission: Path) -> dict[str, object]:
    request: dict[str, object] = {
        "argv": [str(game / "DayZDiag_x64.exe"), "-mission=" + str(mission)],
        "cwd": str(game),
        "role": "offline",
        "window_style": "hidden",
        "label": "red",
        "mod": "@StorageMod",
        "profiles": "profiles",
        "mission": str(mission),
        "storage_seal": "a" * 64,
        "new_run_id": RUN_ID,
        "launch_operation_id": _OP_ID,
    }
    canonical = json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    request["launch_request_sha256"] = hashlib.sha256(canonical).hexdigest()
    return request


class _Fixture:
    def __init__(
        self,
        *,
        confirmed_exit: bool = True,
        bindings: object | None = None,
    ) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.mission = self.root / "mpmissions" / "dayzOffline.chernarusplus"
        self.mission.mkdir(parents=True)
        self.paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.sequence = 0

        def token_fn() -> str:
            self.sequence += 1
            return f"token-{self.sequence}"

        def id_fn() -> str:
            return f"lease-{self.sequence}"

        self.coordinator = SessionCoordinator(
            token_fn=token_fn, id_fn=id_fn, audit=self.audit
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "cancel")
        assert status == 200
        self.token = acquired["lease_token"]
        self.lease_id = acquired["lease_id"]
        self.store = RunManifestStore(self.paths)
        self.guard = FakeGuard()
        self.launcher = _BlockingLauncher(confirmed_exit=confirmed_exit)
        self.bindings = bindings
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
            id_fn=lambda: "run-1",
            bindings=bindings,
        )
        self.lifecycle._starting_cancel_settle_s = 1.0
        self.request = _fresh_request(self.game, self.mission)
        self.guard.snapshots[self.launcher.pid] = snapshot(
            process(self.launcher.pid, role="offline")
        )

    def close(self) -> None:
        self.temporary.cleanup()

    def add_run(
        self,
        records: list[object],
        *,
        run_id: str = "run-existing",
        state: str = "RUNNING",
        owner: str | None = IDENTITY_A.session_id,
        lease: str | None = None,
    ) -> RunRecord:
        run = RunRecord(
            run_id,
            owner,
            lease if lease is not None else (self.lease_id if owner else None),
            state,
            "same",
            "@StorageMod",
            "profiles",
            "mission",
            list(records),
        )
        self.store.add(run)
        # The run was launched by A even when it is ownerless now: the adopt
        # gate uses that attribution to tell an idle run from a stranger's.
        stamp_launcher(self.lifecycle, run.run_id, IDENTITY_A)
        return run

    def wait_for_cancel_request(self, timeout: float = 2.0) -> bool:
        """Wait until the stop has signalled the in-flight launch.

        The launcher is released only after this, so the test cannot race the
        stop: the launch is guaranteed to be inside its STARTING window when
        the cancel arrives, and the stop is guaranteed to have reached the
        daemon's cancel branch before the launch can settle.
        """
        lookup = getattr(self.lifecycle, "_launch_cancel_for", None)
        if lookup is None:
            # BASE has no launch-cancel registry: there is nothing to wait for,
            # and the release below lets the launch settle so the feature
            # assertions can fail for the real reason.
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cancel = lookup(RUN_ID)
            if cancel is not None and cancel.requested.is_set():
                return True
            time.sleep(0.005)
        return False

    def start_in_thread(self) -> threading.Thread:
        self.start_result: dict[str, object] = {}

        def run() -> None:
            self.start_result["value"] = self.lifecycle.start_run(
                IDENTITY_A, self.token, self.request
            )

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        assert self.launcher.entered.wait(2.0), "launch never reached the launcher"
        return thread

    def stop_in_thread(
        self,
        client: object = IDENTITY_A,
        token: str | None = None,
    ) -> threading.Thread:
        self.stop_result: dict[str, object] = {}
        chosen_token = self.token if token is None else token

        def run() -> None:
            self.stop_result["value"] = self.lifecycle.stop_run(
                client, chosen_token, RUN_ID
            )

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread


class _StartingCancelTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _Fixture()
        self.addCleanup(self.fixture.close)

    def _join(self, thread: threading.Thread, timeout: float = 3.0) -> None:
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), "thread deadlocked")

    def _assert_cancelled(self) -> None:
        stored = self.fixture.store.get(RUN_ID)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(
            (
                stored.state,
                stored.owner_session_id,
                stored.owner_lease_id,
                stored.processes,
            ),
            ("EXITED", None, None, []),
        )


class StartingWindowStopTest(_StartingCancelTestCase):
    def test_concurrent_same_session_stop_cancels_the_launch(self) -> None:
        start_thread = self.fixture.start_in_thread()
        stop_thread = self.fixture.stop_in_thread()
        self.assertTrue(self.fixture.wait_for_cancel_request())
        self.fixture.launcher.release.set()
        self._join(stop_thread)
        self._join(start_thread)

        stop = self.fixture.stop_result["value"]
        self.assertIs(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("state"), "EXITED")
        self.assertEqual(stop.get("terminated"), 1)
        self.assertEqual(stop.get("stop_method"), "cancelled_during_start")
        self.assertEqual(self.fixture.launcher.terminate_calls, 1)

        started = self.fixture.start_result["value"]
        self.assertIsNot(started.get("ok"), True, started)
        self.assertEqual(started.get("error"), "launch_cancelled")
        self.assertEqual(started.get("state"), "EXITED")
        self._assert_cancelled()

    def test_stop_that_cannot_reach_the_launch_refuses_by_name(self) -> None:
        self.fixture.lifecycle._starting_cancel_settle_s = 0.05
        start_thread = self.fixture.start_in_thread()
        stop_thread = self.fixture.stop_in_thread()
        self._join(stop_thread)

        stop = self.fixture.stop_result["value"]
        self.assertIsNot(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("error"), "launch_cancel_pending")
        self.assertEqual(stop.get("state"), "STARTING")
        self.assertEqual(self.fixture.launcher.terminate_calls, 0)

        # The request stays pending: the launch still settles cancelled when
        # it reaches the boundary, never a silent success for the stop.
        self.fixture.launcher.release.set()
        self._join(start_thread)
        started = self.fixture.start_result["value"]
        self.assertEqual(started.get("error"), "launch_cancelled")
        self._assert_cancelled()


class SameSessionStopLeaseTest(unittest.TestCase):
    def test_same_session_with_its_lease_uses_the_inflight_lease(self) -> None:
        from tests.dayz_test_tool_helpers import _Runtime

        runtime = _Runtime({"runs": []})
        runtime.identity = SimpleNamespace(session_id=IDENTITY_A.session_id)
        runtime.active_lease_token = "token-1"
        runtime.active_lease_id = "lease-1"
        runtime.lifecycle = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "STARTING",
                    "mod": "@StorageMod",
                    "owner_session_id": IDENTITY_A.session_id,
                    "owner_lease_id": "lease-1",
                }
            ]
        }
        calls: list[str] = []

        async def lifecycle_stop(run_id: str) -> dict[str, object]:
            calls.append(run_id)
            return {
                "ok": True,
                "run_id": run_id,
                "state": "EXITED",
                "terminated": 1,
                "stop_method": "cancelled_during_start",
                "exit_metrics_valid": False,
            }

        runtime.lifecycle_stop = lifecycle_stop

        result = asyncio.run(
            dayz_test_tool.execute_dayz_test_stop(
                runtime, RUN_ID, direct_inflight=True
            )
        )

        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["state"], "EXITED")
        self.assertEqual(result["terminated"], 1)
        self.assertEqual(calls, [RUN_ID])
        # The direct route never reconciled the session or acquired a second
        # lease: the in-flight launch's own lease authorized the stop.
        self.assertEqual(runtime.reconcile_calls, 0)
        self.assertEqual(runtime.active_lease_token, "token-1")

    def test_foreign_session_without_the_lease_is_rejected(self) -> None:
        fixture = _Fixture()
        self.addCleanup(fixture.close)
        record = process(7300, role="client")
        run = fixture.add_run([record], run_id="run-foreign", state="RUNNING")
        fixture.guard.snapshots[record.pid] = snapshot(record)
        # A releases; B is now the only session with a lease and still may not
        # end a run A owns.
        status, _ = fixture.coordinator.release(IDENTITY_A, fixture.token)
        self.assertEqual(status, 200)
        status, acquired = fixture.coordinator.acquire(IDENTITY_B, "other")
        self.assertEqual(status, 200)
        stop = fixture.lifecycle.stop_run(
            IDENTITY_B, acquired["lease_token"], run.run_id
        )
        self.assertEqual(stop.get("error"), "run_not_adopted", stop)
        self.assertEqual(fixture.guard.terminate_calls, [])
        self.assertEqual(fixture.store.get(run.run_id).state, "RUNNING")


class StopOwnershipAttributionTest(_StartingCancelTestCase):
    def test_foreign_owner_does_not_kill_the_run(self) -> None:
        record = process(7301, role="client")
        run = self.fixture.add_run(
            [record], run_id="run-foreign-owner", state="RUNNING"
        )
        self.fixture.guard.snapshots[record.pid] = snapshot(record)
        status, _ = self.fixture.coordinator.release(IDENTITY_A, self.fixture.token)
        self.assertEqual(status, 200)
        status, acquired = self.fixture.coordinator.acquire(IDENTITY_B, "other")
        self.assertEqual(status, 200)
        stop = self.fixture.lifecycle.stop_run(
            IDENTITY_B, acquired["lease_token"], run.run_id
        )
        self.assertEqual(stop.get("error"), "run_not_adopted", stop)
        self.assertEqual(self.fixture.guard.terminate_calls, [])
        self.assertEqual(self.fixture.store.get(run.run_id).state, "RUNNING")

    def test_reused_pid_is_skipped_and_only_the_owned_pid_is_killed(self) -> None:
        reused, owned = process(7003), process(7004)
        run = self.fixture.add_run([reused, owned], run_id="run-existing")
        self.fixture.guard.snapshots[reused.pid] = snapshot(reused) | {
            "creation_time_utc": "different"
        }
        self.fixture.guard.snapshots[owned.pid] = snapshot(owned)

        result = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, run.run_id
        )

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(result.get("state"), "EXITED")
        self.assertEqual(
            [item.pid for item in self.fixture.guard.terminate_calls], [owned.pid]
        )
        stored = self.fixture.store.get(run.run_id)
        self.assertEqual((stored.state, stored.processes), ("EXITED", []))

    def test_unattributed_process_is_not_killed(self) -> None:
        record = process(7005)
        run = self.fixture.add_run([record], run_id="run-unattributed")
        self.fixture.guard.snapshots[record.pid] = {
            "error": "guard_unavailable",
            "exit_code": 3,
        }

        result = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, run.run_id
        )

        self.assertIsNot(result.get("ok"), True, result)
        self.assertEqual(result.get("error"), "guard_unavailable")
        self.assertEqual(result.get("state"), "UNRECONCILED")
        self.assertEqual(self.fixture.guard.terminate_calls, [])
        stored = self.fixture.store.get(run.run_id)
        self.assertEqual((stored.state, stored.owner_session_id), ("UNRECONCILED", None))


class StartingCancelRollbackTest(_StartingCancelTestCase):
    def test_cancel_leaves_a_terminal_row_and_retires_the_binding(self) -> None:
        bindings = _Bindings()
        fixture = _Fixture(bindings=bindings)
        self.addCleanup(fixture.close)
        start_thread = fixture.start_in_thread()
        stop_thread = fixture.stop_in_thread()
        self.assertTrue(fixture.wait_for_cancel_request())
        fixture.launcher.release.set()
        stop_thread.join(3.0)
        start_thread.join(3.0)
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(start_thread.is_alive())

        stored = fixture.store.get(RUN_ID)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(
            (
                stored.state,
                stored.owner_session_id,
                stored.owner_lease_id,
                stored.processes,
            ),
            ("EXITED", None, None, []),
        )
        self.assertIn(
            (RUN_ID, "offline", "launch_cancelled"),
            bindings.retired,
        )
        self.assertFalse((fixture.root / "runtime" / "launch-intent.json").exists())

    def test_degraded_cancel_is_explicit_and_ownerless(self) -> None:
        fixture = _Fixture(confirmed_exit=False)
        self.addCleanup(fixture.close)
        start_thread = fixture.start_in_thread()
        stop_thread = fixture.stop_in_thread()
        self.assertTrue(fixture.wait_for_cancel_request())
        fixture.launcher.release.set()
        stop_thread.join(3.0)
        start_thread.join(3.0)
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(start_thread.is_alive())

        stop = fixture.stop_result["value"]
        self.assertIsNot(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("error"), "launch_cancel_degraded")
        self.assertEqual(stop.get("state"), "UNRECONCILED")
        stored = fixture.store.get(RUN_ID)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertIsNone(stored.owner_session_id)
        self.assertEqual([item.pid for item in stored.processes], [fixture.launcher.pid])


class StopCompatStatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _Fixture()
        self.addCleanup(self.fixture.close)

    def _adopt_then_stop(self, state: str) -> None:
        record = process(7100, role="client")
        run = self.fixture.add_run(
            [record],
            run_id=f"run-{state.lower()}",
            state=state,
            owner=None,
        )
        self.fixture.guard.snapshots[record.pid] = snapshot(record)
        adopted = self.fixture.lifecycle.adopt_run(
            IDENTITY_A, self.fixture.token, run.run_id
        )
        self.assertIs(adopted.get("ok"), True, adopted)
        stopped = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, run.run_id
        )
        self.assertIs(stopped.get("ok"), True, stopped)
        self.assertEqual(stopped.get("state"), "EXITED")
        stored = self.fixture.store.get(run.run_id)
        self.assertEqual((stored.state, stored.processes), ("EXITED", []))
        self.assertEqual(
            [item.pid for item in self.fixture.guard.terminate_calls], [record.pid]
        )

    def test_running_is_still_stoppable(self) -> None:
        record = process(7101, role="client")
        run = self.fixture.add_run(
            [record], run_id="run-running", state="RUNNING"
        )
        self.fixture.guard.snapshots[record.pid] = snapshot(record)
        stopped = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, run.run_id
        )
        self.assertIs(stopped.get("ok"), True, stopped)
        self.assertEqual(stopped.get("state"), "EXITED")
        self.assertEqual(
            [item.pid for item in self.fixture.guard.terminate_calls], [record.pid]
        )

    def test_running_idle_is_still_stoppable_after_adopt(self) -> None:
        self._adopt_then_stop("RUNNING_IDLE")

    def test_unreconciled_is_still_stoppable_after_adopt(self) -> None:
        self._adopt_then_stop("UNRECONCILED")


class StopErrorExplicitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _Fixture()
        self.addCleanup(self.fixture.close)

    def test_unknown_run_is_a_named_failure(self) -> None:
        result = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, "unknown-run"
        )
        self.assertIsNot(result.get("ok"), True, result)
        self.assertEqual(result.get("error"), "run_not_found")

    def test_invalid_lease_is_a_named_failure(self) -> None:
        missing = self.fixture.lifecycle.stop_run(IDENTITY_A, None, RUN_ID)
        self.assertIsNot(missing.get("ok"), True, missing)
        self.assertEqual(missing.get("error"), "lease_required")
        unknown = self.fixture.lifecycle.stop_run(
            IDENTITY_A, "not-a-token", RUN_ID
        )
        self.assertIsNot(unknown.get("ok"), True, unknown)
        self.assertEqual(unknown.get("error"), "lease_invalid")

    def test_quarantine_is_a_named_failure(self) -> None:
        record = process(7200)
        run = self.fixture.add_run([record], run_id="run-quarantine")
        self.fixture.guard.snapshots[record.pid] = snapshot(record)
        self.fixture.lifecycle.retail_probe = lambda: {
            "known": True,
            "processes": [{"pid": record.pid, "name": "DayZ_x64.exe"}],
        }
        result = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, run.run_id
        )
        self.assertIsNot(result.get("ok"), True, result)
        self.assertEqual(result.get("error"), "retail_quarantine")
        self.assertEqual(self.fixture.guard.terminate_calls, [])

    def test_manifest_failure_is_named(self) -> None:
        record = process(7201)
        run = self.fixture.add_run([record], run_id="run-manifest")
        self.fixture.guard.snapshots[record.pid] = snapshot(record)

        def fail_replace(_run: RunRecord) -> None:
            raise OSError("disk")

        with patch.object(
            self.fixture.store, "replace", side_effect=fail_replace
        ):
            result = self.fixture.lifecycle.stop_run(
                IDENTITY_A, self.fixture.token, run.run_id
            )
        self.assertIsNot(result.get("ok"), True, result)
        self.assertEqual(result.get("error"), "manifest_failed")
        self.assertEqual(self.fixture.guard.terminate_calls, [])

    def test_launch_completing_in_a_race_is_stopped_normally(self) -> None:
        start_thread = self.fixture.start_in_thread()
        # Release the launch before the stop reaches it: the launch wins and
        # leaves RUNNING. The stop must then take the ordinary path and kill
        # exactly the owned process, never report a silent success.
        self.fixture.launcher.release.set()
        self.fixture.guard.snapshots[self.fixture.launcher.pid] = snapshot(
            process(self.fixture.launcher.pid, role="offline")
        )
        start_thread.join(3.0)
        self.assertFalse(start_thread.is_alive())
        self.assertIs(self.fixture.start_result["value"].get("ok"), True)
        stopped = self.fixture.lifecycle.stop_run(
            IDENTITY_A, self.fixture.token, RUN_ID
        )
        self.assertIs(stopped.get("ok"), True, stopped)
        self.assertEqual(stopped.get("state"), "EXITED")
        # The launch won the window, so the ordinary path terminates the
        # registered process through the guard, not the Popen handle.
        self.assertEqual(
            [item.pid for item in self.fixture.guard.terminate_calls],
            [self.fixture.launcher.pid],
        )


class ToolSurfaceStartingStopTest(unittest.IsolatedAsyncioTestCase):
    """dayz_test_run -> dayz_test_stop, both through the product adapters."""

    async def test_tool_surface_reaches_the_cancel_during_the_window(self) -> None:
        fixture = _Fixture()
        self.addCleanup(fixture.close)
        policy = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )

        class _ToolRuntime:
            def __init__(self) -> None:
                self.identity = IDENTITY_A
                self.active_lease_token = None
                self.active_lease_id = None
                self.active_ticket = None
                self.active_operation_id = None
                self.daemon_policy = object()
                self.reconcile_calls = 0
                self.bridge_payload = {"ready": {"ready": True, "reason": "ready"}}

            async def lifecycle_status(self) -> dict[str, object]:
                return await asyncio.to_thread(
                    fixture.lifecycle.status, IDENTITY_A
                )

            async def lifecycle_stop(self, run_id: str) -> dict[str, object]:
                token = self.active_lease_token
                assert isinstance(token, str) and token
                return await asyncio.to_thread(
                    fixture.lifecycle.stop_run, IDENTITY_A, token, run_id
                )

            async def bridge_status_payload(self) -> dict[str, object]:
                return self.bridge_payload

            async def reconcile_idle_session(self) -> dict[str, object]:
                self.reconcile_calls += 1
                return {"reconciled": False}

            async def lifecycle_close(self, run_id: str) -> dict[str, object]:
                return {"ok": True, "run_id": run_id, "state": "EXITED"}

            async def lifecycle_reap(self, run_id: str) -> dict[str, object]:
                return {"ok": True, "run_id": run_id, "state": "EXITED"}

        runtime = _ToolRuntime()

        async def acquire() -> None:
            status, acquired = fixture.coordinator.acquire(IDENTITY_A, "dayz-test")
            assert status == 200
            runtime.active_lease_token = acquired["lease_token"]
            runtime.active_lease_id = acquired["lease_id"]

        async def fake_execute(raw_request: bytes, **kwargs: object) -> int:
            started = kwargs.get("execution_started_cb")
            if started is not None:
                await started()
            await acquire()
            token = runtime.active_lease_token
            assert isinstance(token, str)
            result = await asyncio.to_thread(
                fixture.lifecycle.start_run,
                IDENTITY_A,
                token,
                fixture.request,
            )
            sink = kwargs["output_sink"]
            if result.get("ok") is True:
                sink(
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
            degraded = bool(result.get("cleanup_degraded"))
            sink(
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": degraded,
                        "error_code": result.get("error") or "worker_failed",
                        "exit_code": 1,
                        "ok": False,
                        "run_id": RUN_ID if degraded else None,
                    }
                ),
            )
            return 1

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=fake_execute,
        ), patch.object(
            dayz_test_tool, "preflight_bridge_contract_request", return_value=()
        ), patch.object(
            dayz_test_tool,
            "preflight_vpp_request",
            return_value=SimpleNamespace(error_code=None, missing=[], warnings=[]),
        ):
            run_task = asyncio.create_task(
                dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="StorageMod",
                    mode="server",
                )
            )
            await asyncio.to_thread(fixture.launcher.entered.wait, 2.0)
            stop_task = asyncio.create_task(
                dayz_test_tool.execute_dayz_test_stop(
                    runtime, RUN_ID, direct_inflight=True
                )
            )
            self.assertTrue(
                await asyncio.to_thread(fixture.wait_for_cancel_request, 2.0)
            )
            fixture.launcher.release.set()
            stop = await asyncio.wait_for(stop_task, timeout=3.0)
            run_result = await asyncio.wait_for(run_task, timeout=3.0)

        self.assertEqual(stop["status"], "succeeded", stop)
        self.assertEqual(stop["run_id"], RUN_ID)
        self.assertEqual(stop["state"], "EXITED")
        self.assertEqual(stop["terminated"], 1)
        self.assertEqual(run_result["status"], "failed", run_result)
        self.assertEqual(run_result["error_code"], "launch_cancelled")
        self.assertEqual(runtime.reconcile_calls, 0)
        stored = fixture.store.get(RUN_ID)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(
            (
                stored.state,
                stored.owner_session_id,
                stored.owner_lease_id,
                stored.processes,
            ),
            ("EXITED", None, None, []),
        )
        self.assertEqual(fixture.launcher.terminate_calls, 1)


if __name__ == "__main__":
    unittest.main()
