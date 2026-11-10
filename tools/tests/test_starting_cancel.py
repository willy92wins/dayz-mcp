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
import contextlib
import hashlib
import io
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import (
    control_client,
    dayz_test_tool,
    host_config,
    loopback,
    native_broker_protocol,
    native_launcher_backend,
    process_lifecycle,
    request_path_authority,
    server,
)
from dayz_mcp.dayz_test_request import RequestProjectPolicy
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import SessionCoordinator
from tests.daemon_helpers import _http
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _terminal,
)
from tests.lifecycle_helpers import stamp_launcher
from tests.mcp_helpers import _content_json
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

    def __init__(
        self,
        pid: int = 91001,
        *,
        confirmed_exit: bool = True,
        wait_delay: float = 0.0,
    ) -> None:
        self.pid = pid
        self.confirmed_exit = confirmed_exit
        self.wait_delay = wait_delay
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
        if self.wait_delay:
            time.sleep(self.wait_delay)
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


def _extension_request(request: dict[str, object]) -> dict[str, object]:
    """The same launch, but extending a known run instead of creating one."""
    extension = dict(request)
    extension.pop("new_run_id", None)
    extension.pop("launch_operation_id", None)
    extension.pop("launch_request_sha256", None)
    extension["run_id"] = RUN_ID
    # An extension carries no launch identity: the run already exists.
    return extension


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

    def test_bounds_are_ordered_client_settle_kill(self) -> None:
        # B2: the named answers only exist if the client outlives the daemon's
        # settle wait and the settle wait outlives the kill confirmation.
        self.assertGreater(
            process_lifecycle._STARTING_CANCEL_SETTLE_S,
            process_lifecycle._STARTING_CANCEL_KILL_WAIT_S,
        )
        self.assertGreater(
            control_client.LIFECYCLE_STOP_TIMEOUT_S,
            process_lifecycle._STARTING_CANCEL_SETTLE_S,
        )

    def test_settle_wait_covers_a_slow_kill_confirmation(self) -> None:
        fixture = _Fixture()
        self.addCleanup(fixture.close)
        fixture.lifecycle._starting_cancel_kill_s = 0.2
        fixture.lifecycle._starting_cancel_settle_s = 1.0
        fixture.launcher.wait_delay = 0.2
        start_thread = fixture.start_in_thread()
        stop_thread = fixture.stop_in_thread()
        self.assertTrue(fixture.wait_for_cancel_request())
        fixture.launcher.release.set()
        stop_thread.join(3.0)
        start_thread.join(3.0)
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(start_thread.is_alive())
        stop = fixture.stop_result["value"]
        # A kill that takes 0.2s is inside the 1.0s settle bound: the stop
        # answers the effective cancel, not launch_cancel_pending.
        self.assertIs(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("stop_method"), "cancelled_during_start")


class SameSessionStopLeaseTest(_StartingCancelTestCase):
    def test_same_session_with_its_lease_uses_the_inflight_lease(self) -> None:
        # The stop is authorized by the real coordinator with the lease the
        # launch itself holds; no fake runtime and no test-set direct flag.
        start_thread = self.fixture.start_in_thread()
        stop_thread = self.fixture.stop_in_thread()
        self.assertTrue(self.fixture.wait_for_cancel_request())
        self.fixture.launcher.release.set()
        self._join(stop_thread)
        self._join(start_thread)

        stop = self.fixture.stop_result["value"]
        self.assertIs(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("stop_method"), "cancelled_during_start")
        self.assertEqual(stop.get("terminated"), 1)
        self.assertEqual(self.fixture.launcher.terminate_calls, 1)
        # The launch's own lease authorized it: the same token the fixture
        # acquired, not a second one.
        self.assertEqual(self.fixture.coordinator.status(IDENTITY_A)["self"]["state"], "active")

    def test_foreign_session_without_the_lease_is_rejected(self) -> None:
        fixture = self.fixture
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

    def test_extension_cancel_restores_and_stops_the_previous_run(self) -> None:
        # S2: the launch extends a RUNNING run. Cancelling the extension
        # restores that row, and the same stop then ends it through the
        # ordinary kill path. The answer is forced_kill, not a silent
        # cancelled_during_start that would leave the server alive.
        fixture = _Fixture()
        self.addCleanup(fixture.close)
        previous_record = process(7400, role="server")
        previous = fixture.add_run(
            [previous_record], run_id=RUN_ID, state="RUNNING"
        )
        fixture.guard.snapshots[previous_record.pid] = snapshot(previous_record)
        fixture.guard.snapshots[fixture.launcher.pid] = snapshot(
            process(fixture.launcher.pid, role="client")
        )
        fixture.request = _extension_request(fixture.request)
        fixture.request["role"] = "client"

        start_thread = fixture.start_in_thread()
        stop_thread = fixture.stop_in_thread()
        self.assertTrue(fixture.wait_for_cancel_request())
        fixture.launcher.release.set()
        stop_thread.join(3.0)
        start_thread.join(3.0)
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(start_thread.is_alive())

        stop = fixture.stop_result["value"]
        self.assertIs(stop.get("ok"), True, stop)
        self.assertEqual(stop.get("state"), "EXITED")
        self.assertEqual(stop.get("stop_method"), "forced_kill")
        self.assertEqual(
            [item.pid for item in fixture.guard.terminate_calls],
            [previous_record.pid],
        )
        stored = fixture.store.get(RUN_ID)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(
            (stored.state, stored.owner_session_id, stored.processes),
            ("EXITED", None, []),
        )


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


def _surface_policy(root: Path) -> RequestProjectPolicy:
    """A project whose accredited roots exist under the fixture's temp dir."""
    dev = root / "dev"
    (dev / "_server" / "mpmissions").mkdir(parents=True)
    source = dev / "staged-source" / "StorageMod"
    source.mkdir(parents=True)
    mods = root / "mods"
    (mods / "@CF").mkdir(parents=True)
    # build_run_request adds the bridge mod as a default extra mod.
    (mods / "@DayZ_MCP").mkdir(parents=True)
    return RequestProjectPolicy(
        mod="StorageMod",
        dev_root=str(dev),
        default_source=str(source),
        default_base_mods=("@CF",),
        mission_roots=(str(dev / "_server" / "mpmissions"),),
        mod_roots=(str(mods),),
    )


class _LoopbackSurface:
    """The real public surface: FastMCP tools -> ClientRuntime -> ControlClient
    -> loopback /lifecycle routes, with the lease from the real transaction.

    Only the host primitives are faked: the launcher handle, the process guard
    and Steam. The worker process is the native launcher's transport, replaced
    by a fake that speaks the same broker frame and calls /lifecycle/start (or
    adopt+stop) over the loopback; it never calls start_run/stop_run in-process.
    """

    def __init__(self, fixture: _Fixture) -> None:
        self.fixture = fixture
        self.key = "surface-key"
        self.keyfile = fixture.root / "daemon.key"
        self.keyfile.write_text(self.key, encoding="utf-8")
        self.state = loopback.ServerState(self.key)
        self.state.coordination = fixture.coordinator
        self.state.lifecycle = fixture.lifecycle
        self.httpd = loopback.create_http_server(
            0, self.state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        self.port = int(self.httpd.server_address[1])
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        self.policy = _surface_policy(fixture.root / "surface")
        self.sealed = request_path_authority._seal_project_policy_for_test(
            self.policy
        )
        self.launcher_path = str(Path(sys.executable).resolve())
        self.config = ServerConfig(
            mode="client",
            key=self.key,
            keyfile=str(self.keyfile),
            port=self.port,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        self.provenance = host_config.DaemonProvenance(
            launch_executable=self.launcher_path,
            native_executable=self.launcher_path,
            argv=tuple(
                server.daemon.build_daemon_argv(
                    self.config, python=self.launcher_path
                )
            ),
            cwd=server.daemon.daemon_runtime_cwd(),
            port=self.port,
            keyfile=str(self.keyfile),
            auto_spawn_daemon=False,
        )
        self._stack = contextlib.ExitStack()
        self.app = None
        self.runtime = None

    def _build_runtime(self) -> server.ClientRuntime:
        with (
            patch.object(
                host_config,
                "resolve_daemon_provenance",
                return_value=self.provenance,
            ),
            patch.object(
                host_config, "_local_launch_executable", return_value=self.launcher_path
            ),
            patch.object(
                host_config, "_local_native_executable", return_value=self.launcher_path
            ),
        ):
            return server.ClientRuntime(self.config)

    def _transport(self, **kwargs: object) -> tuple[int, bytes]:
        host = str(kwargs["host"])
        port = int(kwargs["port"])
        key = str(kwargs["key"])
        path = str(kwargs["path"])
        method = str(kwargs["method"])
        query = dict(kwargs.get("query") or {})
        query["key"] = key
        url = f"http://{host}:{port}{path}?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            data=kwargs.get("body"),
            headers=dict(kwargs.get("headers") or {}),
            method=method,
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                with urllib.request.urlopen(request, timeout=30.0) as response:
                    return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            try:
                return int(exc.code), exc.read()
            finally:
                exc.close()

    async def _not_stale(self, *_args: object) -> bool:
        return False

    def _post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        _status, body = _http(
            self.base, "POST", path, self.key, payload=payload, timeout=30.0
        )
        return body

    async def _launch(
        self,
        opened_launcher: object,
        *,
        verified_bundle: object,
        canonical_request: bytes,
        lease_token: str,
        identity_json: str,
        daemon_policy_json: str,
        cancel_event: object,
        output_sink: object = None,
        **_kwargs: object,
    ) -> int:
        del opened_launcher, verified_bundle, daemon_policy_json, cancel_event
        frame = native_broker_protocol.decode_request(canonical_request)
        request = json.loads(frame.stdin.decode("utf-8"))
        identity = json.loads(identity_json)
        if request.get("kill") is True:
            run_id = request.get("run_id")
            await asyncio.to_thread(
                self._post,
                "/lifecycle/adopt",
                {
                    "identity": identity,
                    "lease_token": lease_token,
                    "run_id": run_id,
                },
            )
            result = await asyncio.to_thread(
                self._post,
                "/lifecycle/stop",
                {
                    "identity": identity,
                    "lease_token": lease_token,
                    "run_id": run_id,
                },
            )
        else:
            if request.get("run_id") is None:
                request["role"] = (
                    "server" if request.get("mode") in {"server", "all"} else "offline"
                )
                request["mod"] = "@" + str(request.get("mod", ""))
                request["label"] = str(request["mod"]) + " " + request["role"]
                request["window_style"] = "normal"
                request["cwd"] = str(self.fixture.game)
                request["argv"] = list(self.fixture.request["argv"])
                request["profiles"] = str(self.fixture.game / "profiles")
                request["mission"] = str(self.fixture.mission)
                request["auto_remediate_steam"] = False
                # The worker seals the mod set before hashing the request.
                request["storage_seal"] = self.fixture.request["storage_seal"]
                request["new_run_id"] = RUN_ID
                request["launch_operation_id"] = _OP_ID
                request["launch_request_sha256"] = hashlib.sha256(
                    json.dumps(
                        {
                            key: value
                            for key, value in request.items()
                            if key != "launch_request_sha256"
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            result = await asyncio.to_thread(
                self._post,
                "/lifecycle/start",
                {
                    "identity": identity,
                    "lease_token": lease_token,
                    "request": request,
                },
            )
        ok = result.get("ok") is True
        raw_error = result.get("error")
        if ok:
            cleanup_degraded = False
            error_code = None
            terminal_run_id = result.get("run_id")
        elif raw_error in {
            "launch_cancelled",
            "launch_cancel_pending",
            "launch_cancel_degraded",
        }:
            cleanup_degraded = bool(result.get("cleanup_degraded"))
            error_code = raw_error
            terminal_run_id = (
                result.get("run_id") if cleanup_degraded else None
            )
        else:
            # The worker republishes only its declared codes; the daemon's
            # manual_cleanup_required is the degraded cancel of this launch.
            cleanup_degraded = True
            error_code = "launch_cancel_degraded"
            terminal_run_id = result.get("run_id")
        if callable(output_sink):
            output_sink(
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": cleanup_degraded,
                        "error_code": error_code,
                        "exit_code": 0 if ok else 1,
                        "ok": ok,
                        "run_id": terminal_run_id,
                    }
                ),
            )
        return 0 if ok else 1

    def start(self) -> "_LoopbackSurface":
        status, _ = self.fixture.coordinator.release(
            IDENTITY_A, self.fixture.token
        )
        assert status == 200
        stack = self._stack
        stack.enter_context(
            patch.object(
                host_config,
                "resolve_daemon_provenance",
                return_value=self.provenance,
            )
        )
        stack.enter_context(
            patch.object(
                host_config, "_local_launch_executable", return_value=self.launcher_path
            )
        )
        stack.enter_context(
            patch.object(
                host_config, "_local_native_executable", return_value=self.launcher_path
            )
        )
        stack.enter_context(
            patch.object(
                server.orphan_guard,
                "verified_daemon_http_request",
                side_effect=self._transport,
            )
        )
        stack.enter_context(
            patch.object(
                server, "_observe_caller_tool_registry_stale", new=self._not_stale
            )
        )
        stack.enter_context(
            patch.object(
                dayz_test_tool, "open_approved_launcher", return_value=_Opened()
            )
        )
        stack.enter_context(
            patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                return_value=_Bundle((self.sealed,)),
            )
        )
        stack.enter_context(
            patch.object(
                dayz_test_tool, "preflight_bridge_contract_request", return_value=()
            )
        )
        stack.enter_context(
            patch.object(
                dayz_test_tool,
                "preflight_vpp_request",
                return_value=SimpleNamespace(error_code=None, missing=[], warnings=[]),
            )
        )
        stack.enter_context(
            patch.object(
                native_launcher_backend,
                "launch_registered_native",
                side_effect=self._launch,
            )
        )
        self.runtime = self._build_runtime()
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = server.build_app(self.config)
        return self

    def close(self) -> None:
        self._stack.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)


class ToolSurfaceStartingStopTest(unittest.IsolatedAsyncioTestCase):
    """dayz_test_run -> dayz_test_stop through the real public surface."""

    def _surface(self, *, confirmed_exit: bool = True) -> tuple[_Fixture, _LoopbackSurface]:
        fixture = _Fixture(confirmed_exit=confirmed_exit)
        self.addCleanup(fixture.close)
        surface = _LoopbackSurface(fixture)
        self.addCleanup(surface.close)
        surface.start()
        return fixture, surface

    async def test_public_tool_cancels_a_fresh_launch_without_a_run_id(self) -> None:
        fixture, surface = self._surface()
        run_task = asyncio.create_task(
            surface.app.call_tool(
                "dayz_test_run", {"project": "StorageMod", "mode": "server"}
            )
        )
        self.assertTrue(await asyncio.to_thread(fixture.launcher.entered.wait, 5.0))
        # The caller never learned the worker-minted id: the public tool names
        # the one STARTING row of this session and lease (B3).
        stop_task = asyncio.create_task(
            surface.app.call_tool("dayz_test_stop", {})
        )
        self.assertTrue(
            await asyncio.to_thread(fixture.wait_for_cancel_request, 5.0)
        )
        fixture.launcher.release.set()
        stop = _content_json(await asyncio.wait_for(stop_task, timeout=10.0))
        run_result = _content_json(await asyncio.wait_for(run_task, timeout=10.0))

        self.assertEqual(stop["status"], "succeeded", stop)
        self.assertEqual(stop["run_id"], RUN_ID)
        self.assertEqual(stop["state"], "EXITED")
        self.assertEqual(stop["terminated"], 1)
        self.assertEqual(stop["stop_method"], "cancelled_during_start")
        self.assertEqual(run_result["status"], "failed", run_result)
        self.assertEqual(run_result["error_code"], "launch_cancelled")
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

    async def test_public_tool_reports_launch_cancel_pending(self) -> None:
        fixture, surface = self._surface()
        fixture.lifecycle._starting_cancel_settle_s = 0.05
        run_task = asyncio.create_task(
            surface.app.call_tool(
                "dayz_test_run", {"project": "StorageMod", "mode": "server"}
            )
        )
        self.assertTrue(await asyncio.to_thread(fixture.launcher.entered.wait, 5.0))
        stop = _content_json(
            await asyncio.wait_for(
                surface.app.call_tool("dayz_test_stop", {}), timeout=10.0
            )
        )
        self.assertEqual(stop["status"], "failed", stop)
        self.assertEqual(stop["error_code"], "launch_cancel_pending")
        self.assertEqual(stop["state"], "STARTING")
        self.assertEqual(fixture.launcher.terminate_calls, 0)
        fixture.launcher.release.set()
        run_result = _content_json(await asyncio.wait_for(run_task, timeout=10.0))
        self.assertEqual(run_result["error_code"], "launch_cancelled")

    async def test_public_tool_reports_launch_cancel_degraded(self) -> None:
        fixture, surface = self._surface(confirmed_exit=False)
        run_task = asyncio.create_task(
            surface.app.call_tool(
                "dayz_test_run", {"project": "StorageMod", "mode": "server"}
            )
        )
        self.assertTrue(await asyncio.to_thread(fixture.launcher.entered.wait, 5.0))
        stop_task = asyncio.create_task(
            surface.app.call_tool("dayz_test_stop", {})
        )
        self.assertTrue(
            await asyncio.to_thread(fixture.wait_for_cancel_request, 5.0)
        )
        fixture.launcher.release.set()
        stop = _content_json(await asyncio.wait_for(stop_task, timeout=10.0))
        await asyncio.wait_for(run_task, timeout=10.0)
        self.assertEqual(stop["status"], "failed", stop)
        self.assertEqual(stop["error_code"], "launch_cancel_degraded")
        self.assertEqual(stop["state"], "UNRECONCILED")
        stored = fixture.store.get(RUN_ID)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.state, "UNRECONCILED")
        self.assertIsNone(stored.owner_session_id)
        self.assertEqual([item.pid for item in stored.processes], [fixture.launcher.pid])

    async def test_public_stop_refuses_a_held_session_lease(self) -> None:
        # A session that acquired its own lease is busy: the public tool names
        # the refusal instead of silently waiting for the run or the lock.
        fixture, surface = self._surface()
        acquired = await surface.runtime.session_acquire_wait("surface")
        self.assertEqual(acquired["status"], "active", acquired)
        record = process(7301, role="client")
        run = RunRecord(
            RUN_ID,
            surface.runtime.identity.session_id,
            acquired["lease_id"],
            "RUNNING",
            "same",
            "@StorageMod",
            str(Path(surface.policy.dev_root) / "_client" / "profiles"),
            "mission",
            [record],
        )
        fixture.store.add(run)
        fixture.guard.snapshots[record.pid] = snapshot(record)
        with self.assertRaises(Exception) as caught:
            await asyncio.wait_for(
                surface.app.call_tool("dayz_test_stop", {"run_id": RUN_ID}),
                timeout=10.0,
            )
        self.assertIn(
            "release your session lease first", str(caught.exception)
        )

    async def test_public_stop_of_a_running_idle_run_uses_the_locked_path(self) -> None:
        fixture, surface = self._surface()
        record = process(7300, role="client")
        run = RunRecord(
            RUN_ID,
            None,
            None,
            "RUNNING_IDLE",
            "same",
            "@StorageMod",
            str(Path(surface.policy.dev_root) / "_client" / "profiles"),
            "mission",
            [record],
        )
        fixture.store.add(run)
        stamp_launcher(fixture.lifecycle, RUN_ID, surface.runtime.identity)
        fixture.guard.snapshots[record.pid] = snapshot(record)
        stop = _content_json(
            await asyncio.wait_for(
                surface.app.call_tool("dayz_test_stop", {"run_id": RUN_ID}),
                timeout=10.0,
            )
        )
        self.assertEqual(stop["status"], "succeeded", stop)
        self.assertEqual(stop["run_id"], RUN_ID)
        self.assertEqual(
            [item.pid for item in fixture.guard.terminate_calls], [record.pid]
        )
        stored = fixture.store.get(RUN_ID)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual((stored.state, stored.processes), ("EXITED", []))


if __name__ == "__main__":
    unittest.main()
