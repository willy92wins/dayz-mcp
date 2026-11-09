"""ah-starting-cancel-20261109-1444: a stop must stay reachable in STARTING.

Offline only: no DayZ launch, no daemon, no PBO. The tool-level pre-check used
to answer run_not_active for a STARTING row before the stop's own lease was
asked for, so an owned launch could not be cancelled by the existing protocol.
These tests pin the accepted window, the explicit fail-closed answer, and the
ownership/rollback behaviour of the stop path that already exists.
"""
from __future__ import annotations

import sys
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
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)
from tests.lifecycle_helpers import stamp_launcher
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    IDENTITY_A,
    IDENTITY_B,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate

_PROFILES = r"C:\Tools\LFV_D2_Executor\_client\profiles"


def _starting_policy() -> dayz_test_request.RequestProjectPolicy:
    return _policy(
        mod="StorageMod",
        dev_root=r"C:\Tools\LFV_D2_Executor",
        default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
        default_base_mods=("@CF",),
    )


def _starting_row(owner: str | None = "A") -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": RUN_ID,
        "state": "STARTING",
        "mod": "@StorageMod",
        "profiles": _PROFILES,
        "launch_acknowledged": False,
    }
    if owner is not None:
        row["owner_session"] = owner
        row["owner_session_id"] = owner
    return row


class StartingStopReachabilityTest(unittest.IsolatedAsyncioTestCase):
    async def _stop(
        self, runtime: _Runtime, terminal: bytes, exit_code: int
    ) -> dict[str, object]:
        policy = _starting_policy()

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=(policy,)
            )
            self.assertIs(parsed.payload["kill"], True)
            self.assertEqual(parsed.payload["run_id"], RUN_ID)
            # The stop joins the box queue instead of failing locally: the
            # in-flight launch holds the lease, so the stop waits for it.
            self.assertIsNone(kwargs["max_wait_s"])
            kwargs["output_sink"]("stdout", terminal)
            return exit_code

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
            return await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

    def _owned_runtime(self) -> _Runtime:
        runtime = _Runtime({"runs": [_starting_row("A")]})
        runtime.identity = SimpleNamespace(session_id="A")
        return runtime

    async def test_starting_owned_run_is_stopped_not_refused(self) -> None:
        runtime = self._owned_runtime()
        terminal = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": RUN_ID,
            }
        )

        result = await self._stop(runtime, terminal, 0)

        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["stop_method"], "forced_kill")
        self.assertIs(result["exit_metrics_valid"], False)
        self.assertIsNone(result["error_code"])

    async def test_starting_stop_failure_is_explicit_not_silent_success(self) -> None:
        runtime = self._owned_runtime()
        terminal = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": "run_not_adoptable",
                "exit_code": 1,
                "ok": False,
                "run_id": None,
            }
        )

        result = await self._stop(runtime, terminal, 1)

        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["error_code"], "run_not_adoptable")
        self.assertIs(result["cleanup_degraded"], False)

    def test_owned_starting_row_is_attributed_to_its_session(self) -> None:
        runtime = self._owned_runtime()
        status = {"runs": [_starting_row("A")]}

        self.assertIs(
            dayz_test_tool._h14_owned_stop(runtime, status, RUN_ID), True
        )
        self.assertIs(
            dayz_test_tool._h14_owned_stop(
                runtime, {"runs": [_starting_row("B")]}, RUN_ID
            ),
            False,
        )
        # A STARTING row always has an owner while the launch holds the lease;
        # an ownerless STARTING is not adopted here (the daemon answers first).
        self.assertIs(
            dayz_test_tool._h14_owned_stop(
                runtime, {"runs": [_starting_row(None)]}, RUN_ID
            ),
            False,
        )

    def test_regression_the_settled_states_remain_stoppable(self) -> None:
        policies = _sealed(_starting_policy())
        for state in ("RUNNING", "RUNNING_IDLE", "UNRECONCILED"):
            with self.subTest(state=state):
                row = dict(_starting_row("A"), state=state)
                _, run = dayz_test_tool.resolve_stop_run(
                    {"runs": [row]}, policies, RUN_ID
                )
                self.assertEqual(run["state"], state)


class StartingCancelLifecycleTest(unittest.TestCase):
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
        self.sequence = 0

        def token_fn() -> str:
            self.sequence += 1
            return f"token-{self.sequence}"

        def id_fn() -> str:
            return f"lease-{self.sequence}"

        self.coordinator = SessionCoordinator(
            token_fn=token_fn, id_fn=id_fn, audit=self.audit
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.lease_a = acquired["lease_id"]
        self.store = RunManifestStore(self.paths)
        self.guard = FakeGuard()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=FakeLauncher(),
            id_fn=lambda: "run-1",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _add_run(
        self,
        records: list[object],
        *,
        state: str,
        owner: str | None = "A",
        lease: str | None = None,
    ) -> RunRecord:
        run = RunRecord(
            "run-existing",
            owner,
            lease,
            state,
            "same",
            "@SameMod",
            "profiles",
            "mission",
            list(records),
        )
        self.store.add(run)
        if owner == "A":
            stamp_launcher(self.lifecycle, run.run_id, IDENTITY_A)
        return run

    def test_starting_row_is_fail_closed_and_settles_to_a_clean_exit(self) -> None:
        record = process(7001)
        run = self._add_run(
            [record], state="STARTING", owner="A", lease=self.lease_a
        )
        self.guard.snapshots[record.pid] = snapshot(record)

        # The launch window is not adoptable and the stop is not RUNNING yet:
        # both answer explicitly instead of killing anything.
        adopt = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertEqual(adopt.get("error"), "run_not_adoptable")
        stop = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertEqual(stop.get("error"), "run_not_adopted")
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get(run.run_id).state, "STARTING")

        # The launch settles ownerless; the same session adopts and stops it.
        settled = self.store.get(run.run_id)
        settled.owner_session_id = None
        settled.owner_lease_id = None
        settled.state = "RUNNING_IDLE"
        self.store.replace(settled)

        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertIs(adopted.get("ok"), True, adopted)
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)
        self.assertIs(stopped.get("ok"), True, stopped)
        self.assertEqual(stopped.get("state"), "EXITED")
        stored = self.store.get(run.run_id)
        self.assertEqual(
            (
                stored.state,
                stored.owner_session_id,
                stored.owner_lease_id,
                stored.processes,
            ),
            ("EXITED", None, None, []),
        )
        self.assertEqual(
            [item.pid for item in self.guard.terminate_calls], [record.pid]
        )

    def test_foreign_owner_is_not_killed(self) -> None:
        record = process(7002)
        run = self._add_run(
            [record], state="RUNNING", owner="A", lease=self.lease_a
        )
        self.guard.snapshots[record.pid] = snapshot(record)
        self.coordinator.release(IDENTITY_A, self.token_a)
        status, acquired = self.coordinator.acquire(IDENTITY_B, "other")
        self.assertEqual(status, 200)

        result = self.lifecycle.stop_run(
            IDENTITY_B, acquired["lease_token"], run.run_id
        )

        self.assertEqual(result.get("error"), "run_not_adopted")
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.store.get(run.run_id).state, "RUNNING")
        self.assertEqual(self.store.get(run.run_id).owner_session_id, "A")

    def test_reused_pid_is_skipped_and_only_the_owned_pid_is_killed(self) -> None:
        reused, owned = process(7003), process(7004)
        run = self._add_run(
            [reused, owned], state="RUNNING", owner="A", lease=self.lease_a
        )
        self.guard.snapshots[reused.pid] = snapshot(reused) | {
            "creation_time_utc": "different"
        }
        self.guard.snapshots[owned.pid] = snapshot(owned)

        result = self.lifecycle.stop_run(IDENTITY_A, self.token_a, run.run_id)

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(result.get("state"), "EXITED")
        self.assertEqual(
            [item.pid for item in self.guard.terminate_calls], [owned.pid]
        )
        stored = self.store.get(run.run_id)
        self.assertEqual((stored.state, stored.processes), ("EXITED", []))


if __name__ == "__main__":
    unittest.main()
