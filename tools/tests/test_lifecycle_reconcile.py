from __future__ import annotations

from tests.steam_helpers import FakeSteamGate

import os
import sys
import time
import hashlib
import json
import unittest
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from mcp.server.fastmcp.exceptions import ToolError

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_tool, process_lifecycle
from dayz_mcp import native_launcher_transaction
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
    _RUN_PROCESSES_GONE_HINT,
)
from dayz_mcp.runtime_state import JsonlAuditWriter
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.test_dayz_test_tool import (
    RUN_ID as TOOL_RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)
from tests.test_process_lifecycle import (
    HASH_A,
    HASH_B,
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)

RUN_ID = "run-existing"
LAUNCH_PID = 9001


def gone(pid: int) -> dict[str, object]:
    """What the guard answers for a pid that is no longer there."""
    return {"error": "process_not_found", "exit_code": 4, "pid": pid}


def foreign(record: ProcessRecord) -> dict[str, object]:
    """A complete identity that does not match the record: not ours any more."""
    return dict(snapshot(record), creation_time_utc="2001-01-01T00:00:00.0000000Z")


def holders(*rows: tuple[int, int | None, str | None]) -> dict[str, object]:
    return {
        "known": True,
        "holders": [
            {"port": port, "pid": pid, "name": name} for port, pid, name in rows
        ],
    }


class FakeBridgeBindings:
    """The loopback ServerState the daemon always wires (daemon.py:541).

    fb-20260904-200816-79e2: superseding a live client now needs the witness of
    the gate that authorised it AND a bridge row that still agrees at T1. The
    default row here is a client that has not polled for a long time -- the very
    state the extension gate acts on -- so the tests written before the witness
    keep measuring what they measured. The refusal branches have their own tests.
    """

    def __init__(
        self,
        last_poll_age_s: object = 999.0,
        bound_last_poll_age_s: object = None,
        binding_state: object = None,
        raise_on_read: bool = False,
    ) -> None:
        self.last_poll_age_s = last_poll_age_s
        self.bound_last_poll_age_s = bound_last_poll_age_s
        self.binding_state = binding_state
        self.raise_on_read = raise_on_read
        self.reads = 0

    def status_snapshot(self, now: object = None) -> dict[str, object]:
        self.reads += 1
        if self.raise_on_read:
            raise RuntimeError("bridge unavailable")
        return {
            "peers": {
                "client": {
                    "last_poll_age_s": self.last_poll_age_s,
                    "bound_last_poll_age_s": self.bound_last_poll_age_s,
                    "binding_state": self.binding_state,
                }
            }
        }


class LifecycleReconcileTest(unittest.TestCase):
    """P-L1 (fb-20260904-025733-d60f) and P-L2/P-L2.c (fb-20260904-025027-8f76 c).

    P-L1: an UNRECONCILED run with no owner and a live owned process is
    adoptable, so dayz_test_stop has a supported route back to EXITED without a
    session killing processes by hand.
    P-L2: relaunching a role on a live run replaces its ProcessRecord instead of
    adding a second one, and the confirmation of the old process includes its
    UDP socket (P-L2.c), because start_run probes that table again just before
    the launcher.
    """

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in (
            "DayZDiag_x64.exe",
            "DayZ_BE.exe",
            "DayZ_x64.exe",
            "DayZServer_x64.exe",
        ):
            (self.game / name).write_bytes(b"")
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
        self.launcher = FakeLauncher(LAUNCH_PID)
        self.port_table: dict[str, object] = holders()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            port_probe=lambda: self.port_table,
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
        )
        # The bound of the socket-release confirmation is contract, not a sleep:
        # one probe here so a red check costs no wall clock.
        self.lifecycle._role_release_tries = 1
        self.lifecycle._role_release_interval_s = 0.0
        self.bridge = FakeBridgeBindings()
        self.lifecycle.bridge_probe = self.bridge.status_snapshot

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # ------------------------------------------------------------------ helpers
    def install_run(
        self,
        records: list[ProcessRecord],
        *,
        state: str = "UNRECONCILED",
        owner: str | None = None,
    ) -> RunRecord:
        run = RunRecord(
            RUN_ID,
            owner,
            "lease-A" if owner else None,
            state,
            "same",
            "@SameMod",
            "profiles",
            "mission",
            list(records),
        )
        self.store.add(run)
        return run

    def owned(self, pid: int, role: str) -> ProcessRecord:
        record = process(pid, role)
        self.guard.snapshots[pid] = snapshot(record)
        return record

    def request(self, role: str = "client") -> dict[str, object]:
        return {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": role,
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
            "run_id": RUN_ID,
            # 79e2: the witness of the gate, stamped now, so the revalidation
            # inside start_run has something to compare against.
            "replace_if_not_polling_since": int(time.time() * 1000),
        }

    def arm_launch(self, role: str = "client") -> ProcessRecord:
        launched = process(LAUNCH_PID, role)
        self.guard.snapshots[LAUNCH_PID] = snapshot(launched)
        return launched

    def roles(self) -> list[str]:
        return [record.role for record in self.store.get(RUN_ID).processes]

    def pids(self) -> list[int]:
        return [record.pid for record in self.store.get(RUN_ID).processes]

    def adopt_events(self) -> list[dict[str, object]]:
        return [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_adopt"
        ]

    # ============================================================ P-L1 (d60f)
    def test_unreconciled_with_live_owned_process_is_adopted(self) -> None:
        self.install_run([self.owned(701, "server"), self.owned(702, "client")])

        result = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(result.get("state"), "RUNNING")
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "RUNNING")
        self.assertEqual(stored.owner_session_id, "A")
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.adopt_events()[-1].get("reason"), "unreconciled_adopt")

    def test_adopting_an_idle_run_keeps_its_own_audit_reason(self) -> None:
        """Positive control for the branch above: the pre-existing edge is intact."""
        self.install_run([self.owned(703, "server")], state="RUNNING_IDLE")

        result = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.adopt_events()[-1].get("reason"), "identity_match")

    def test_unreconciled_adopt_still_requires_a_lease(self) -> None:
        self.install_run([self.owned(704, "server")])

        result = self.lifecycle.adopt_run(IDENTITY_A, None, RUN_ID)

        self.assertIsNot(result.get("ok"), True, result)
        stored = self.store.get(RUN_ID)
        self.assertEqual((stored.state, stored.owner_session_id), ("UNRECONCILED", None))

    def test_unreconciled_without_live_processes_says_processes_gone(self) -> None:
        record = process(705, "server")
        self.guard.snapshots[705] = gone(705)
        self.install_run([record])

        subject = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertEqual(subject.get("error"), "run_processes_gone", subject)
        self.assertEqual(subject.get("hint"), _RUN_PROCESSES_GONE_HINT)
        stored = self.store.get(RUN_ID)
        self.assertEqual((stored.state, stored.owner_session_id), ("UNRECONCILED", None))

    def test_idle_run_without_live_processes_says_processes_gone(self) -> None:
        """Positive control: the same construction already answered this on RUNNING_IDLE."""
        record = process(706, "server")
        self.guard.snapshots[706] = gone(706)
        self.install_run([record], state="RUNNING_IDLE")

        control = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertEqual(control.get("error"), "run_processes_gone", control)

    def test_unreconciled_with_an_unvouched_record_stays_closed(self) -> None:
        """Negative control: opening the state did not relax the identity gate."""
        self.install_run([process(707, "server")])  # no snapshot: unknown

        result = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertIsNot(result.get("ok"), True, result)
        self.assertEqual(self.guard.terminate_calls, [])
        stored = self.store.get(RUN_ID)
        self.assertEqual((stored.state, stored.owner_session_id), ("UNRECONCILED", None))

    def test_adopt_then_stop_ends_the_run_and_frees_the_box(self) -> None:
        server = self.owned(708, "server")
        client = self.owned(709, "client")
        self.install_run([server, client])

        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)
        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertIs(adopted.get("ok"), True, adopted)
        self.assertIs(stopped.get("ok"), True, stopped)
        stored = self.store.get(RUN_ID)
        self.assertEqual((stored.state, stored.processes), ("EXITED", []))
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls], [708, 709]
        )
        box = self.lifecycle.box_occupancy()
        self.assertIs(box["occupied"], False, box)
        self.assertEqual(box["ports_in_use"], [])

    def test_adopting_an_unreconciled_run_twice_is_idempotent(self) -> None:
        self.install_run([self.owned(710, "server")])

        first = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)
        second = self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertEqual(first, second)
        stored = self.store.get(RUN_ID)
        self.assertEqual((stored.state, stored.owner_session_id), ("RUNNING", "A"))

    def test_a_second_stop_after_the_run_ended_is_refused_not_repeated(self) -> None:
        self.install_run([self.owned(711, "server")])
        self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)
        self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)

        again = self.lifecycle.stop_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertIsNot(again.get("ok"), True, again)
        self.assertEqual(len(self.guard.terminate_calls), 1)
        self.assertEqual(self.store.get(RUN_ID).state, "EXITED")

    def test_a_restart_between_adopt_and_stop_leaves_the_run_readoptable(self) -> None:
        self.install_run([self.owned(712, "server")])
        self.lifecycle.adopt_run(IDENTITY_A, self.token_a, RUN_ID)

        recovered = RunManifestStore(self.paths)
        recovered.recover_after_restart()

        self.assertEqual(recovered.get(RUN_ID).state, "RUNNING_IDLE")
        self.assertIsNone(recovered.get(RUN_ID).owner_session_id)

    def test_dayz_test_stop_accepts_unreconciled_and_still_refuses_the_rest(self) -> None:
        policies = _sealed_policies()

        for state in ("RUNNING", "RUNNING_IDLE", "UNRECONCILED"):
            with self.subTest(state=state):
                policy, run = dayz_test_tool.resolve_stop_run(
                    _status_row(state), policies, _UUID
                )
                self.assertEqual(run["state"], state)
                self.assertEqual(policy.mod, "SameMod")

        for state in ("EXITED", "STARTING", "STOPPING"):
            with self.subTest(state=state):
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    dayz_test_tool.resolve_stop_run(_status_row(state), policies, _UUID)
                self.assertEqual(caught.exception.code, "run_not_active")

    # ====================================================== P-L2 / P-L2.c (8f76c)
    def test_relaunching_the_client_replaces_its_record(self) -> None:
        server = self.owned(720, "server")
        hung = self.owned(721, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.pids(), [720, LAUNCH_PID])
        self.assertEqual(self.roles(), ["server", "client"])
        self.assertEqual([record.pid for record in self.guard.terminate_calls], [721])
        replaced = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_role_replaced"
        ]
        self.assertEqual(len(replaced), 1, replaced)
        self.assertEqual(replaced[0].get("role"), "client")
        self.assertEqual(replaced[0].get("owned_pids"), [721])
        self.assertEqual(replaced[0].get("gone_pids"), [])

    # -- fb-20260906-185909-df53 -------------------------------------------
    def test_a_client_over_a_run_with_no_client_needs_no_witness(self) -> None:
        """mode=all: the client stage extends the run its own server stage made.

        `existing` is not None there, but the run carries no client process, so
        _replace_role_processes would return ([], None) without touching a
        thing (:2718) and the worker stamps no witness
        (dayz_test_worker.py:317-323). Demanding it refused the launch of a
        client that would never have superseded anything.
        """
        server = self.owned(770, "server")
        self.install_run([server], state="RUNNING", owner="A")
        self.arm_launch()
        request = self.request()
        request.pop("replace_if_not_polling_since")

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, request)

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.pids(), [770, LAUNCH_PID])
        self.assertEqual(self.roles(), ["server", "client"])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(
            [
                event
                for event in self.audit.events
                if event.get("event") == "lifecycle_role_replaced"
            ],
            [],
        )

    # -- fb-20260904-200816-79e2 / H-A2-2 -----------------------------------
    # The gate that authorises superseding a client decides in the MCP server
    # process; the kill happens here, after composing the sealed request,
    # opening the launcher, starting app.pyz and two broker round trips. A4
    # measured that window over the durable audit: n=26, min 0,47 s, median
    # 7,00 s, max 29,16 s -- the same order as PEER_STALE_S = 15 s.

    def _replacement(self, **overrides: object) -> dict[str, object]:
        server = self.owned(760, "server")
        hung = self.owned(761, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        request = self.request()
        request.update(overrides)
        return self.lifecycle.start_run(IDENTITY_A, self.token_a, request)

    def _assert_nothing_was_touched(self, result: dict[str, object], code: str) -> None:
        self.assertEqual(result.get("error"), code, result)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.pids(), [760, 761])
        self.assertIn(code, str(result.get("hint")))

    def test_a_replacement_without_the_gate_witness_is_refused(self) -> None:
        """Fail-closed: no witness, no kill.

        This is also what a launcher bundle older than this daemon sends, and
        what lifecycle_cli start sends when it bypasses the gate (A2-G3).
        """
        result = self._replacement(replace_if_not_polling_since=None)
        self._assert_nothing_was_touched(result, "replace_witness_missing")

    def test_a_witness_that_is_not_an_integer_is_no_witness(self) -> None:
        for value in ("1756000000000", 0, -1, 1.5, True):
            with self.subTest(value=value):
                self.setUp()
                result = self._replacement(replace_if_not_polling_since=value)
                self._assert_nothing_was_touched(result, "replace_witness_missing")

    def test_a_decision_older_than_the_bound_no_longer_authorises_a_kill(self) -> None:
        stale = int((time.time() - 120.0) * 1000)
        result = self._replacement(replace_if_not_polling_since=stale)
        self._assert_nothing_was_touched(result, "replace_witness_stale")

    def test_a_witness_stamped_in_the_future_is_refused(self) -> None:
        future = int((time.time() + 3600.0) * 1000)
        result = self._replacement(replace_if_not_polling_since=future)
        self._assert_nothing_was_touched(result, "replace_witness_stale")

    def test_a_client_that_polled_after_the_decision_is_not_killed(self) -> None:
        """A2-G2, closed here. The repro: the bridge says stale at T0, the
        client polls again before start_run, and it used to die anyway with a
        response that declared client_not_polling -- already false at the kill.
        """
        self.bridge.last_poll_age_s = 0.2
        result = self._replacement(
            replace_if_not_polling_since=int((time.time() - 10.0) * 1000)
        )
        self._assert_nothing_was_touched(result, "client_polling_since_decision")

    def test_the_bound_age_counts_too_when_the_peer_is_bound(self) -> None:
        # The most recent evidence of a poll wins, whichever key carries it:
        # deliberately stricter than server._peer_is_live, which picks one by
        # binding state.
        self.bridge.last_poll_age_s = 999.0
        self.bridge.bound_last_poll_age_s = 0.5
        self.bridge.binding_state = "BOUND"
        result = self._replacement(
            replace_if_not_polling_since=int((time.time() - 10.0) * 1000)
        )
        self._assert_nothing_was_touched(result, "client_polling_since_decision")

    def test_a_bridge_that_cannot_be_read_never_authorises_a_kill(self) -> None:
        self.bridge.raise_on_read = True
        result = self._replacement()
        self._assert_nothing_was_touched(result, "bridge_state_unreadable")

    def test_a_client_that_never_polled_is_still_replaceable(self) -> None:
        """Both ages null is the row of a peer that never polled this generation.

        That is evidence that it did not poll after the decision either, and it
        is the hung client the replacement exists for (ficha 8f76c). Reading it
        as silence would refuse exactly the case the feature was built to fix.
        """
        self.bridge.last_poll_age_s = None
        self.bridge.bound_last_poll_age_s = None
        result = self._replacement(
            replace_if_not_polling_since=int((time.time() - 5.0) * 1000)
        )
        self.assertIs(result.get("ok"), True, result)
        self.assertEqual([record.pid for record in self.guard.terminate_calls], [761])

    def test_a_row_that_is_not_the_shape_the_bridge_publishes_is_no_answer(self) -> None:
        class _MalformedBridge:
            reads = 0

            def status_snapshot(self, now: object = None) -> dict[str, object]:
                return {"peers": {"client": {"binding_state": "BOUND"}}}

        self.lifecycle.bridge_probe = _MalformedBridge().status_snapshot
        result = self._replacement()
        self._assert_nothing_was_touched(result, "bridge_state_unreadable")

    def test_f05_a_witness_a_second_in_the_future_does_not_authorise_a_kill(self) -> None:
        """Codex F-05. The dangerous window was inside the old skew tolerance.

        A witness stamped 1 s ahead made a poll 0.1 s old look older than the
        decision once the negative age was clamped to zero, and the live client
        died. +3600 s was already refused; this is the case that was not.
        """
        self.bridge.last_poll_age_s = 0.1
        result = self._replacement(
            replace_if_not_polling_since=int((time.time() + 1.0) * 1000)
        )
        self._assert_nothing_was_touched(result, "replace_witness_stale")

    def test_f04_a_partially_unreadable_row_is_no_answer(self) -> None:
        """Codex F-04. One field that parses does not make the row readable.

        NaN matters on its own: every comparison against it is False, so a NaN
        that survived the filter would have read as "did not poll".
        """
        for label, first, second in (
            ("string", 999.0, "0.2"),
            ("nan", 999.0, float("nan")),
            ("inf", 999.0, float("inf")),
            ("negative", 999.0, -1.0),
            ("bool", 999.0, True),
        ):
            with self.subTest(label=label):
                self.setUp()
                self.bridge.last_poll_age_s = first
                self.bridge.bound_last_poll_age_s = second
                result = self._replacement(
                    replace_if_not_polling_since=int((time.time() - 5.0) * 1000)
                )
                self._assert_nothing_was_touched(result, "bridge_state_unreadable")

    def test_a_non_numeric_age_is_no_answer(self) -> None:
        self.bridge.last_poll_age_s = "0.2"
        result = self._replacement()
        self._assert_nothing_was_touched(result, "bridge_state_unreadable")

    def test_no_bridge_probe_at_all_is_the_same_refusal(self) -> None:
        self.lifecycle.bridge_probe = None
        result = self._replacement()
        self._assert_nothing_was_touched(result, "bridge_state_unreadable")

    def test_positive_control_a_client_that_stopped_polling_is_replaced(self) -> None:
        """The other half: without it every assertion above would pass over a
        gate that refuses everything.
        """
        self.bridge.last_poll_age_s = 999.0
        result = self._replacement(
            replace_if_not_polling_since=int((time.time() - 5.0) * 1000)
        )
        self.assertIs(result.get("ok"), True, result)
        self.assertEqual([record.pid for record in self.guard.terminate_calls], [761])
        self.assertGreaterEqual(self.bridge.reads, 1)

    def test_the_witness_is_read_before_anything_is_terminated(self) -> None:
        self.bridge.last_poll_age_s = 0.1
        result = self._replacement(
            replace_if_not_polling_since=int((time.time() - 10.0) * 1000)
        )
        self.assertEqual(result.get("error"), "client_polling_since_decision")
        self.assertEqual(self.bridge.reads, 1)
        self.assertEqual(self.guard.terminate_calls, [])

    # -- M15 / Codex F-01: the rotation is a pre-spawn step of start_run ----
    """The rotation used to live in the worker, before the first lifecycle

    It used to live in the worker, before the first lifecycle call, so a launch
    the daemon was about to refuse with active_run_exists had already renamed
    the storage of the run that WAS alive. These tests fix the order: nothing
    the daemon refuses may have touched the mission, and nothing it admits may
    reach the launcher without the storage being right.
    """

    STORAGE_SEAL_A = "a" * 64
    STORAGE_SEAL_B = "b" * 64

    def mission(self) -> Path:
        """The mission directory, seeded ONCE.

        It used to re-create storage_1 whenever it was missing, so merely
        reading the sibling list after a rotation put the tree back and every
        later assertion measured a mission that had healed itself.
        """
        directory = Path(self.temporary.name) / "mpmissions" / "dayzOffline"
        if not directory.exists():
            tree = directory / "storage_1"
            (tree / "players").mkdir(parents=True)
            (tree / "data.bin").write_bytes(b"world-and-characters")
            (tree / "players" / "p1.bin").write_bytes(b"survivor")
        return directory

    def backup_trees(self) -> list[str]:
        """Only the trees set aside: not their markers, not the journals."""
        return sorted(
            entry.name
            for entry in self.mission().iterdir()
            if entry.is_dir() and entry.name.startswith("storage_1.modset-")
        )

    def digest(self) -> str:
        entries = []
        for current, dirnames, filenames in os.walk(self.mission()):
            dirnames.sort()
            entries.append("D " + str(Path(current).relative_to(self.mission())))
            for name in sorted(filenames):
                payload = (Path(current) / name).read_bytes()
                entries.append(
                    "F " + name + " " + hashlib.sha256(payload).hexdigest()
                )
        return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()

    def siblings(self) -> list[str]:
        return sorted(
            entry.name
            for entry in self.mission().iterdir()
            if entry.name.startswith("storage_1")
        )

    def server_request(self, seal: str | None = STORAGE_SEAL_A) -> dict[str, object]:
        payload: dict[str, object] = {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "server",
            "window_style": "normal",
            "label": "gate",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": str(self.mission()),
        }
        if seal is not None:
            payload["storage_seal"] = seal
        return payload

    def test_a_launch_the_daemon_refuses_has_not_rotated_anything(self) -> None:
        """(a) The ordering itself, asserted on the file system.

        A run is already active, so this new launch dies with active_run_exists.
        The mission must be byte-identical: same tree, same siblings, no marker.
        """
        self.install_run([self.owned(730, "server")], state="RUNNING", owner="A")
        self.mission()
        before_digest = self.digest()
        before_siblings = self.siblings()

        result = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.server_request()
        )

        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.digest(), before_digest)
        self.assertEqual(self.siblings(), before_siblings)
        self.assertFalse((self.mission() / "storage_1.modset.json").exists())

    def test_b_an_admitted_launch_rotates_and_then_spawns(self) -> None:
        """(b) The positive control. Without it (a) would pass over a gate that
        never rotates at all, which is the mutation that matters most here."""
        self.arm_launch("server")
        self.mission()

        result = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.server_request()
        )

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(len(self.launcher.calls), 1)
        marker = self.mission() / "storage_1.modset.json"
        self.assertTrue(marker.is_file())
        self.assertEqual(
            json.loads(marker.read_text(encoding="utf-8"))["seal"], self.STORAGE_SEAL_A
        )
        # Legacy tree with no marker: fail-closed, so it was set aside.
        moved = [name for name in self.siblings() if name.startswith("storage_1.modset-")]
        self.assertEqual(len(moved), 1, self.siblings())
        rows = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_storage_rotated"
        ]
        self.assertEqual(len(rows), 1, rows)

    def test_the_same_seal_twice_does_not_rotate_again(self) -> None:
        """Idempotence, measured where it lives.

        The first version of this test called setUp() between the two launches,
        which moved to a FRESH mission and then compared sibling names: it
        passed just as happily over a gate that rotates every single time. The
        delta review named it. Two calls of the rotation step against the SAME
        mission is the property, and the assertion that kills that mutant is
        that the second adds no sibling and leaves the marker byte-identical.
        """
        mission = self.mission()
        request = self.server_request()

        first = self.lifecycle._rotate_storage_for_launch(request, "run-one")
        self.assertIsNone(first)
        after_first = self.siblings()
        marker = mission / "storage_1.modset.json"
        sealed = marker.read_bytes()
        self.assertEqual(
            len([name for name in after_first if name.startswith("storage_1.modset-")]),
            1,
            after_first,
        )

        second = self.lifecycle._rotate_storage_for_launch(request, "run-two")

        self.assertIsNone(second)
        self.assertEqual(self.siblings(), after_first)
        self.assertEqual(marker.read_bytes(), sealed)
        rotations = [
            event
            for event in self.audit.events
            if event.get("event") == "lifecycle_storage_rotated"
        ]
        self.assertEqual(len(rotations), 1, rotations)

    def test_a_different_seal_rotates_a_second_time(self) -> None:
        """Positive control for the one above: the gate is not simply inert."""
        request = self.server_request()
        self.assertIsNone(self.lifecycle._rotate_storage_for_launch(request, "run-one"))
        after_first = self.backup_trees()
        # The engine would create the new tree on start; nothing did here, and
        # a second seal over an ABSENT storage only re-seals. Recreate it so the
        # control measures a real second rotation.
        (self.mission() / "storage_1").mkdir()
        (self.mission() / "storage_1" / "data.bin").write_bytes(b"second-world")

        other = dict(request)
        other["storage_seal"] = self.STORAGE_SEAL_B
        self.assertIsNone(self.lifecycle._rotate_storage_for_launch(other, "run-two"))

        self.assertEqual(len(self.backup_trees()), len(after_first) + 1, self.backup_trees())

    def test_c_a_journal_that_cannot_be_reconciled_refuses_the_launch(self) -> None:
        """(c) Recovery: an ambiguous transaction blocks and spawns nothing."""
        self.arm_launch("server")
        mission = self.mission()
        for txid in ("1" * 32, "2" * 32):
            (mission / ("storage_1.modset.rotation." + txid + ".json")).write_text(
                "{}", encoding="utf-8"
            )
        before = self.digest()

        result = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.server_request()
        )

        self.assertEqual(result.get("error"), "storage_recovery_required", result)
        self.assertIn("storage_1.modset.rotation", str(result.get("hint")))
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.digest(), before)

    def test_a_launch_that_must_rotate_without_its_seal_is_refused(self) -> None:
        """Fail-closed against a launcher bundle older than this daemon."""
        self.arm_launch("server")
        self.mission()
        before = self.digest()

        result = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.server_request(seal=None)
        )

        self.assertEqual(result.get("error"), "storage_rotate_failed", result)
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.digest(), before)

    def test_a_client_relaunch_never_rotates(self) -> None:
        """Negative control: attaching to a run the engine already serves."""
        server = self.owned(740, "server")
        hung = self.owned(741, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        self.mission()
        before = self.digest()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.digest(), before)
        self.assertFalse((self.mission() / "storage_1.modset.json").exists())

    def test_relaunching_a_role_the_run_does_not_hold_changes_nothing(self) -> None:
        """The mode=all client leg and any first launch of a role: no replacement."""
        server = self.owned(722, "server")
        self.install_run([server], state="RUNNING", owner="A")
        self.arm_launch()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.pids(), [722, LAUNCH_PID])
        self.assertEqual(self.guard.terminate_calls, [])

    def test_a_dead_role_record_is_retired_without_a_termination(self) -> None:
        server = self.owned(723, "server")
        dead = process(724, "client")
        self.guard.snapshots[724] = gone(724)
        self.install_run([server, dead], state="RUNNING", owner="A")
        self.arm_launch()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.pids(), [723, LAUNCH_PID])
        self.assertEqual(self.guard.terminate_calls, [])

    def test_a_role_record_that_is_not_ours_is_never_terminated(self) -> None:
        """Negative control. Positive control: the owned case above does terminate."""
        server = self.owned(725, "server")
        stranger = process(726, "client")
        self.guard.snapshots[726] = foreign(stranger)
        self.install_run([server, stranger], state="RUNNING", owner="A")
        self.arm_launch()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertIn(726, self.pids())

    def test_the_socket_still_held_blocks_the_retirement_and_the_launch(self) -> None:
        server = self.owned(727, "server")
        hung = self.owned(728, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        self.port_table = holders((2302, 728, "DayZDiag_x64.exe"))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "port_still_held", result)
        self.assertEqual(
            result.get("hint"), process_lifecycle._PORT_STILL_HELD_HINT
        )
        self.assertEqual(self.launcher.calls, [])
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "RUNNING")
        self.assertEqual(sorted(record.pid for record in stored.processes), [727, 728])

    def test_the_socket_released_lets_the_same_call_through(self) -> None:
        """Positive control of the check above: the only difference is the table."""
        server = self.owned(729, "server")
        hung = self.owned(730, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        self.port_table = holders((2302, 44444, "svchost.exe"))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.pids(), [729, LAUNCH_PID])
        self.assertEqual([record.pid for record in self.guard.terminate_calls], [730])

    def test_an_unreadable_socket_table_refuses_the_launch_at_admission(self) -> None:
        """Measured: the lote D admission gate fires first and nothing is retired.

        _foreign_port_reason runs before any of this (the admission probe of
        start_run) and answers port_scan_unknown, which start_run publishes as
        active_run_exists. The replacement is never reached, so the fail-closed
        of P-L2.c is not what is being observed here - the check below is.
        """
        server = self.owned(731, "server")
        hung = self.owned(732, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        self.port_table = {"known": False}

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(sorted(self.pids()), [731, 732])

    def test_a_table_that_goes_unreadable_refuses_the_retirement(self) -> None:
        """The table is readable at admission and unreadable at the confirmation."""
        server = self.owned(741, "server")
        hung = self.owned(742, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        reads: list[int] = []

        def flaky() -> dict[str, object]:
            reads.append(1)
            # Admission runs both before and after Steam preparation. Isolate
            # the unreadable table at the post-termination confirmation here.
            return holders() if len(reads) <= 2 else {"known": False}

        self.lifecycle.port_probe = flaky

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "port_scan_unknown", result)
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(
            [record.pid for record in self.guard.terminate_calls], [742]
        )
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "RUNNING")
        self.assertEqual(sorted(record.pid for record in stored.processes), [741, 742])

    def test_a_run_whose_only_process_is_the_role_is_not_emptied(self) -> None:
        hung = self.owned(733, "client")
        self.install_run([hung], state="RUNNING", owner="A")
        self.arm_launch()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "run_would_be_empty", result)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.launcher.calls, [])
        stored = self.store.get(RUN_ID)
        self.assertEqual((stored.state, [r.pid for r in stored.processes]), ("RUNNING", [733]))

    def test_a_failed_termination_leaves_the_run_reconcilable(self) -> None:
        server = self.owned(734, "server")
        hung = self.owned(735, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        self.guard.terminate_results = [
            {"terminated": False, "error": "termination_unavailable", "exit_code": 3}
        ]

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIn("error", result)
        self.assertEqual(self.launcher.calls, [])
        stored = self.store.get(RUN_ID)
        self.assertEqual(sorted(record.pid for record in stored.processes), [734, 735])

    def test_a_crash_after_the_retirement_recovers_through_adopt_and_stop(self) -> None:
        """The durable row between the retirement and the launch is STARTING.

        A restart turns it into UNRECONCILED (RunManifestStore.recover_after_restart),
        which is exactly the state P-L1 opened: adopt, then stop.
        """
        server = self.owned(736, "server")
        hung = self.owned(737, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        seen: list[tuple[str, list[int]]] = []
        original = self.store.replace

        def spy(run: RunRecord) -> None:
            original(run)
            seen.append((run.state, [record.pid for record in run.processes]))
            if run.state == "STARTING" and 737 not in [
                record.pid for record in run.processes
            ]:
                raise KeyboardInterrupt("crash between the retirement and the launcher")

        self.store.replace = spy  # type: ignore[method-assign]
        with self.assertRaises(KeyboardInterrupt):
            self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        self.store.replace = original  # type: ignore[method-assign]

        self.assertIn(("STARTING", [736]), seen)
        recovered = RunManifestStore(self.paths)
        recovered.recover_after_restart()
        self.assertEqual(recovered.get(RUN_ID).state, "UNRECONCILED")
        self.assertEqual([r.pid for r in recovered.get(RUN_ID).processes], [736])

        reborn = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=recovered,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            port_probe=lambda: self.port_table,
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-2",
        )
        adopted = reborn.adopt_run(IDENTITY_A, self.token_a, RUN_ID)
        stopped = reborn.stop_run(IDENTITY_A, self.token_a, RUN_ID)

        self.assertIs(adopted.get("ok"), True, adopted)
        self.assertIs(stopped.get("ok"), True, stopped)
        self.assertEqual(recovered.get(RUN_ID).state, "EXITED")

    # ------------------------------------------------- ronda 2: A3-F4 y A1-F1/F2
    def test_the_socket_witness_ignores_a_recycled_pid_on_a_foreign_socket(self) -> None:
        """A3-F4: un pid pelado no es una identidad, y el testigo corre justo
        despues del instante en que ese pid dejo de serlo. Solo cuenta un holder
        que podria ser el juego: imagen DayZ, o cualquier imagen en la banda de
        puertos del proyecto."""
        server = self.owned(750, "server")
        hung = self.owned(751, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        # El SO reasigno el pid del cliente muerto a un servicio ajeno con un
        # socket que nada tiene que ver con DayZ (mDNS, 5353).
        self.port_table = holders((5353, 751, "svchost.exe"))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.pids(), [750, LAUNCH_PID])

    def test_the_socket_witness_still_blocks_a_dayz_image(self) -> None:
        """Control positivo del check anterior: solo cambia el nombre de imagen."""
        server = self.owned(752, "server")
        hung = self.owned(753, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        self.port_table = holders((5353, 753, "DayZDiag_x64.exe"))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "port_still_held", result)
        self.assertEqual(self.launcher.calls, [])

    def test_the_socket_witness_still_blocks_the_game_port_band(self) -> None:
        """Segundo control positivo: imagen ajena, pero en la banda del juego."""
        server = self.owned(754, "server")
        hung = self.owned(755, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()
        self.port_table = holders((2302, 755, "svchost.exe"))

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "port_still_held", result)

    def test_the_replacement_only_runs_for_the_role_the_gate_covers(self) -> None:
        """A1-F2: la puerta de extension lee el record de rol client y nada mas,
        asi que reemplazar otro rol mataria un proceso que ninguna puerta miro."""
        server = self.owned(760, "server")
        offline = self.owned(761, "offline")
        self.install_run([server, offline], state="RUNNING", owner="A")
        self.arm_launch("offline")

        result = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.request("offline")
        )

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(sorted(self.pids()), sorted([760, 761, LAUNCH_PID]))

    def test_a_single_process_run_of_another_role_still_relaunches(self) -> None:
        """A1-F1: run_would_be_empty dejaba sin relanzar un run de un solo
        proceso offline, que HEAD si relanzaba. Regresion cerrada."""
        offline = self.owned(762, "offline")
        self.install_run([offline], state="RUNNING", owner="A")
        self.arm_launch("offline")

        result = self.lifecycle.start_run(
            IDENTITY_A, self.token_a, self.request("offline")
        )

        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(len(self.launcher.calls), 1)

    def test_the_client_role_still_refuses_to_empty_its_run(self) -> None:
        """Control positivo de los dos anteriores: para el rol que la puerta SI
        cubre, run_would_be_empty sigue en pie."""
        hung = self.owned(763, "client")
        self.install_run([hung], state="RUNNING", owner="A")
        self.arm_launch()

        result = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertEqual(result.get("error"), "run_would_be_empty", result)

    def test_replacing_the_role_twice_converges_on_one_record(self) -> None:
        server = self.owned(738, "server")
        hung = self.owned(739, "client")
        self.install_run([server, hung], state="RUNNING", owner="A")
        self.arm_launch()

        first = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())
        # The relaunched client is now the record to supersede; the guard vouches
        # for it because arm_launch registered its identity.
        second = self.lifecycle.start_run(IDENTITY_A, self.token_a, self.request())

        self.assertIs(first.get("ok"), True, first)
        self.assertIs(second.get("ok"), True, second)
        self.assertEqual(self.roles(), ["server", "client"])
        self.assertEqual(self.pids(), [738, LAUNCH_PID])

    def test_a_rotation_row_reaches_the_real_audit_writer(self) -> None:
        """The durable jsonl, not AuditSink.events: that fake is how 7a6b hid."""
        self.paths.audit_dir.mkdir(parents=True, exist_ok=True)
        writer = JsonlAuditWriter(self.paths, "generation-rotation")
        self.lifecycle.audit = writer.write
        self.mission()

        error = self.lifecycle._rotate_storage_for_launch(
            self.server_request(), "run-real-audit"
        )

        self.assertIsNone(error)
        rows = [
            json.loads(line)
            for line in writer.current_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        rotated = [
            row for row in rows if row.get("event") == "lifecycle_storage_rotated"
        ]
        self.assertEqual(len(rotated), 1, rows)
        self.assertEqual(rotated[0].get("run_id"), "run-real-audit")

    def test_a_dropped_audit_row_is_counted_where_it_can_be_seen(self) -> None:
        def boom(_event: dict[str, object]) -> bool:
            raise ValueError("invalid_audit_event")

        self.lifecycle.audit = boom
        self.mission()
        error = self.lifecycle._rotate_storage_for_launch(
            self.server_request(), "run-dropped-audit"
        )
        self.assertIsNone(error)

        public = self.lifecycle.public_status()
        dropped = public.get("audit_rows_dropped")
        self.assertIsInstance(dropped, int)
        self.assertGreaterEqual(dropped, 1)

        from dayz_mcp import daemon
        from tests.test_daemon import _config

        snapshot = {
            "peers": {
                "server": {"last_poll_age_s": None, "queue_depth": 0, "version": None},
                "client": {"last_poll_age_s": None, "queue_depth": 0, "version": None},
            },
            "results_pending": 0,
        }
        state = SimpleNamespace(
            daemon_generation="generation-a",
            coordination=SimpleNamespace(snapshot_payload=lambda: {"revision": 7}),
            lifecycle=self.lifecycle,
            status_snapshot=lambda: snapshot,
        )
        payload = daemon.make_status_provider(_config(), state)()
        self.assertIn("audit_row_dropped", payload.get("warnings", []))
        self.assertGreaterEqual(payload.get("audit_rows_dropped", 0), 1)

    def test_the_witness_hint_names_the_bundle_and_daemon_versions(self) -> None:
        result = self._replacement(replace_if_not_polling_since=None)
        self._assert_nothing_was_touched(result, "replace_witness_missing")
        hint = str(result.get("hint"))
        daemon_sha = hashlib.sha256(
            Path(process_lifecycle.__file__).with_name("dayz_test_request.py").read_bytes()
        ).hexdigest()
        manifest_path = (
            Path(process_lifecycle.__file__).resolve().parents[1]
            / "native-launchers"
            / "dayz-test-v1"
            / "closure-manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn(str(manifest["bundle_id"]), hint)
        # Normalised: the manifest stores it upper-case, the hint prints one
        # spelling. Asserting the raw spelling is what forced a redundant
        # second copy of the same hash into the message.
        self.assertIn(str(manifest["dayz_test_request_sha256"]).casefold(), hint)
        self.assertIn(daemon_sha, hint)
        self.assertIn("orphan", hint.casefold())
        rebuild_at = hint.casefold().find("rebuild")
        self.assertGreaterEqual(rebuild_at, 0)
        self.assertIn("only", hint[rebuild_at:].casefold())

    def test_the_witness_hint_is_computed_when_it_is_read(self) -> None:
        distinctive = "c" * 64
        with patch.object(
            process_lifecycle, "_request_parser_sha256", return_value=distinctive
        ):
            result = self._replacement(replace_if_not_polling_since=None)
        hint = str(result.get("hint"))
        self.assertIn(distinctive, hint)

    def test_the_witness_hint_names_the_approved_launcher_manifest(self) -> None:
        distinctive_id = "approved-only-bundle-9f3c"
        distinctive_sha = "ab" * 32
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "closure-manifest.json").write_text(
                json.dumps(
                    {
                        "bundle_id": distinctive_id,
                        "dayz_test_request_sha256": distinctive_sha,
                    }
                ),
                encoding="utf-8",
            )

            class _Approved:
                def __init__(self) -> None:
                    self.root = root

                def __enter__(self) -> "_Approved":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

            with patch(
                "dayz_mcp.launcher_registry.open_approved_launcher",
                return_value=_Approved(),
            ):
                result = self._replacement(replace_if_not_polling_since=None)
            hint = str(result.get("hint"))
        self.assertIn(distinctive_id, hint)
        self.assertIn(str(root), hint)
        self.assertIn("approved", hint.casefold())

    def test_both_request_parser_hashes_are_printed_in_the_same_case(self) -> None:
        result = self._replacement(replace_if_not_polling_since=None)
        hint = str(result.get("hint"))
        marker = "dayz_test_request_sha256="
        found: list[str] = []
        cursor = 0
        while True:
            at = hint.find(marker, cursor)
            if at < 0:
                break
            digest = []
            for char in hint[at + len(marker) :]:
                if char in "0123456789abcdefABCDEF":
                    digest.append(char)
                else:
                    break
            if digest:
                found.append("".join(digest))
            cursor = at + len(marker)
        self.assertGreaterEqual(len(found), 2, hint)
        for digest in found:
            self.assertEqual(digest, digest.casefold(), digest)

    def test_a_dropped_row_from_any_silent_audit_path_is_counted(self) -> None:
        def boom(_event: dict[str, object]) -> bool:
            raise ValueError("invalid_audit_event")

        self.lifecycle.audit = boom
        before = int(self.lifecycle.public_status().get("audit_rows_dropped") or 0)
        self.lifecycle._note_post_persist_fault("run-silent-post", "diagnostic")
        after_post = int(self.lifecycle.public_status().get("audit_rows_dropped") or 0)
        self.assertGreater(after_post, before)
        self.lifecycle.retail_probe = lambda: {
            "known": True,
            "processes": [{"pid": 1, "name": "DayZ_x64.exe"}],
        }
        self.lifecycle.reap_dead_runs()
        after_reap = int(self.lifecycle.public_status().get("audit_rows_dropped") or 0)
        self.assertGreater(after_reap, after_post)

    def test_the_dropped_row_counter_survives_the_legacy_identity_path(self) -> None:
        self.lifecycle._note_audit_row_dropped()
        self.lifecycle._note_audit_row_dropped()

        def explode(_audit: object) -> list[str]:
            raise RuntimeError("legacy_identity_transition_failed")

        self.lifecycle.manifest.quarantine_legacy_active = explode
        payload = self.lifecycle.public_status()
        self.assertEqual(payload.get("error"), "legacy_identity_transition_failed")
        self.assertIn("audit_rows_dropped", payload)
        self.assertEqual(payload.get("audit_rows_dropped"), 2)


_UUID = "11111111-1111-4111-8111-111111111111"


def _status_row(state: str) -> dict[str, object]:
    return {
        "runs": [
            {
                "run_id": _UUID,
                "state": state,
                "mod": "@SameMod",
                "profiles": r"P:\SameMod\_client\profiles",
                "launch_acknowledged": True,
            }
        ]
    }


def _sealed_policies() -> tuple[object, ...]:
    from dayz_mcp import dayz_test_request

    class _Sealed:
        policy = dayz_test_request.RequestProjectPolicy(
            "SameMod", r"P:\SameMod", "source", (), (), ()
        )

    return (_Sealed(),)


_POLLING_PEER = {"last_poll_age_s": 0.2, "version_state": "ok"}
_STALE_PEER = {"last_poll_age_s": 41.0, "version_state": "ok"}
_AMBIGUOUS_PEER = {"binding_state": "AMBIGUOUS", "last_poll_age_s": 0.2}
_RAISES = object()
# Defined up here, not with the fixture helpers below: a default argument is
# evaluated when the class body runs, which is before those helpers exist.
_UNSET = object()
# Read defensively so the module still IMPORTS against the pre-ronda-2
# product: a red calibration has to run test by test, not die on import.
_BUDGET_ENV = getattr(
    dayz_test_tool, "_CLIENT_START_BUDGET_ENV", "DAYZ_MCP_CLIENT_START_BUDGET_S"
)


def _record(*, present=True, alive=True, age_s=900.0, pid=4002):
    return dayz_test_tool.ClientRecordProjection(present, alive, age_s, pid)


def _decide(record, payload):
    """Call the gate projection through whichever signature the product has.

    The pre-ronda-2 build took a bool; calling it with a record would raise
    AttributeError deep inside and hide which branch the check was measuring.
    """
    return dayz_test_tool._decide_client_replacement(record, payload)


class ClientReplacementDecisionTest(unittest.TestCase):
    """Ronda 2, mitad pura: que rama elige la puerta de extension, y por que.

    El relanzamiento de fb-20260904-025027-8f76 (parte c) se pide «cuando el
    puente ve client_not_polling». Ronda 2 anadio las tres distinciones que los
    auditores R9 midieron que faltaban: un cliente que aun ARRANCA no es uno
    colgado (A4-H1), un cliente cuyo pid esta confirmado MUERTO es reemplazable
    diga lo que diga su fila de peer (A2-H1), y un snapshot ilegible ya NO
    autoriza matar (A3-F1).
    """

    def test_every_branch_of_the_decision(self) -> None:
        cases = [
            # (etiqueta, record, snapshot, replace, motivo, edad_peer)
            ("payload ilegible: fail-closed antes que nada",
             dayz_test_tool.ClientRecordProjection(False, None, None, None, valid=False),
             {"client_peer": _POLLING_PEER}, False, "lifecycle_status_invalid", None),
            ("peer sin edad utilizable: fail-closed", _record(),
             {"client_peer": {}}, False, "bridge_status_unknown", None),
            ("no hay cliente registrado", _record(present=False, age_s=None, pid=None),
             {"client_peer": _POLLING_PEER}, True, "no_client_to_replace", None),
            ("el pid del cliente esta muerto", _record(alive=False, age_s=10.0),
             {"client_peer": _POLLING_PEER}, True, "client_process_dead", None),
            ("sin snapshot: fail-closed", _record(), None,
             False, "bridge_status_unknown", None),
            ("snapshot sin fila del peer: fail-closed", _record(),
             {"ready": {"ready": True, "reason": "ready"}},
             False, "bridge_status_unknown", None),
            ("el cliente sondea", _record(), {"client_peer": _POLLING_PEER},
             False, "client_polling", 0.2),
            ("el cliente aun arranca", _record(age_s=30.0), {"client_peer": _STALE_PEER},
             False, "client_still_starting", 41.0),
            ("edad del registro ilegible: fail-closed", _record(age_s=None),
             {"client_peer": _STALE_PEER}, False, "client_record_age_unknown", 41.0),
            ("el cliente esta colgado", _record(), {"client_peer": _STALE_PEER},
             True, "client_not_polling", 41.0),
            ("sondea pero sin acreditar", _record(), {"client_peer": _AMBIGUOUS_PEER},
             True, "client_not_accredited", 0.2),
            ("peer BOUND y vivo", _record(),
             {"client_peer": {"binding_state": "BOUND",
                              "bound_last_poll_age_s": 0.3,
                              "last_poll_age_s": 0.3}},
             False, "client_polling", 0.3),
        ]
        for label, record, payload, replace, reason, age in cases:
            with self.subTest(label):
                decision = dayz_test_tool._decide_client_replacement(record, payload)
                self.assertEqual(
                    (decision.replace, decision.reason, decision.last_poll_age_s),
                    (replace, reason, age),
                )

    def test_a_stale_server_does_not_condemn_a_polling_client(self) -> None:
        """Control positivo del motivo por el que NO se usa ready.reason.

        compute_bridge_ready comprueba el servidor antes que el cliente
        (server.py:413 devuelve server_poll_stale y server.py:415
        client_not_polling), asi que este snapshot dice ready=False con motivo
        del SERVIDOR mientras el cliente sondea. Un gate construido sobre
        ready.reason mataria un cliente sano justo aqui.
        """
        payload = {
            "client_peer": _POLLING_PEER,
            "server_peer": {"last_poll_age_s": 900.0, "version_state": "ok"},
            "ready": {"ready": False, "reason": "server_poll_stale"},
        }
        decision = dayz_test_tool._decide_client_replacement(_record(), payload)
        self.assertFalse(decision.replace)
        self.assertEqual(decision.reason, "client_polling")

    def test_the_startup_budget_is_the_only_difference(self) -> None:
        """Control positivo de client_still_starting: solo cambia la edad.

        A5-N3: las dos edades son LITERALES, no derivadas de
        `_client_start_budget_s()`. Un control cuyo input sale del tratamiento
        que prueba no puede detectar un cambio del tratamiento: con el
        presupuesto puesto a 0 por mutacion, `budget - 1.0` daba -1.0, que
        sigue siendo menor que 0, y el check sobrevivia verde mientras el
        producto estaba roto.
        """
        joven = dayz_test_tool._decide_client_replacement(
            _record(age_s=30.0), {"client_peer": _STALE_PEER}
        )
        viejo = dayz_test_tool._decide_client_replacement(
            _record(age_s=3600.0), {"client_peer": _STALE_PEER}
        )
        self.assertEqual((joven.replace, joven.reason), (False, "client_still_starting"))
        self.assertEqual((viejo.replace, viejo.reason), (True, "client_not_polling"))

    def test_the_budget_default_covers_the_measured_startup_window(self) -> None:
        """El default es un SUPUESTO medido, no un numero elegido a ojo.

        Medido 2026-09-04 sobre los 120 RPT de cliente de `_client/profiles`:
        de los 102 que llegan a CreateMission(), p50=39,3 s, p90=58,7 s,
        p95=81,4 s, p99=187,7 s y max=344,6 s desde la cabecera del RPT hasta la
        mision. El default tiene que cubrir ese maximo observado.
        """
        self.assertGreaterEqual(dayz_test_tool._CLIENT_START_BUDGET_S, 344.61)

    def test_the_budget_is_overridable_and_a_bad_override_is_ignored(self) -> None:
        with patch.dict(os.environ, {_BUDGET_ENV: "5"}):
            self.assertEqual(dayz_test_tool._client_start_budget_s(), 5.0)
            decision = dayz_test_tool._decide_client_replacement(
                _record(age_s=30.0), {"client_peer": _STALE_PEER}
            )
            self.assertEqual((decision.replace, decision.reason), (True, "client_not_polling"))
        for bad in ("", "abc", "-1", "nan", "99999"):
            with self.subTest(bad), patch.dict(os.environ, {_BUDGET_ENV: bad}):
                self.assertEqual(
                    dayz_test_tool._client_start_budget_s(),
                    dayz_test_tool._CLIENT_START_BUDGET_S,
                )

    def test_the_budget_travels_as_a_call_parameter(self) -> None:
        """The only door to the budget was the environment of the process that
        serves the tools, and whoever drives this MCP does not launch that
        process. A gate that needs a human to relaunch the server is a gate an
        agent cannot run -- which is the whole point of the product.
        """
        self.assertEqual(dayz_test_tool._client_start_budget_s(5.0), 5.0)
        decision = dayz_test_tool._decide_client_replacement(
            _record(age_s=30.0), {"client_peer": _STALE_PEER}, budget_s=5.0
        )
        self.assertEqual(
            (decision.replace, decision.reason), (True, "client_not_polling")
        )

    def test_the_call_parameter_wins_over_the_environment(self) -> None:
        with patch.dict(os.environ, {_BUDGET_ENV: "3000"}):
            self.assertEqual(dayz_test_tool._client_start_budget_s(5.0), 5.0)
            decision = dayz_test_tool._decide_client_replacement(
                _record(age_s=30.0), {"client_peer": _STALE_PEER}, budget_s=5.0
            )
            self.assertTrue(decision.replace)

    def test_no_parameter_falls_back_to_the_environment_then_the_default(self) -> None:
        with patch.dict(os.environ, {_BUDGET_ENV: "7"}):
            self.assertEqual(dayz_test_tool._client_start_budget_s(None), 7.0)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                dayz_test_tool._client_start_budget_s(None),
                dayz_test_tool._CLIENT_START_BUDGET_S,
            )

    def test_a_bad_call_parameter_is_rejected_not_ignored(self) -> None:
        """Deliberately asymmetric with the env var, which ignores garbage.

        The operator who exports an env var never sees the response; the caller
        who passes a parameter does. Silently ignoring their override would be
        a mute failure, and it would answer client_still_starting for six
        minutes while the caller believed it had asked for five seconds.
        """
        for bad in (-1.0, 3600.5, float("nan"), "5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dayz_test_tool._client_start_budget_s(bad)

    def test_the_record_projection_does_not_confuse_absent_with_unknown(self) -> None:
        """A4-H2: un _pid_alive que no sabe responder NO es «no hay cliente»."""
        status = _extension_status(with_client=True)
        with patch.object(dayz_test_tool, "_pid_alive", return_value=None):
            record = dayz_test_tool._client_record_from_status(status, TOOL_RUN_ID)
        self.assertTrue(record.present)
        self.assertIsNone(record.alive)
        self.assertIsNotNone(record.age_s)
        with patch.object(dayz_test_tool, "_pid_alive", return_value=None):
            empty = dayz_test_tool._client_record_from_status(
                _extension_status(with_client=False), TOOL_RUN_ID
            )
        self.assertFalse(empty.present)


class ClientReplacementGateTest(unittest.IsolatedAsyncioTestCase):
    """Ronda 2, extremo a extremo: la peticion sellada sale o no sale."""

    def setUp(self) -> None:
        from dayz_mcp import steam_preflight

        steam = patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            return_value=steam_preflight.SteamSessionResult(
                error_code=None,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation=steam_preflight.REMEDIATION,
            ),
        )
        steam.start()
        self.addCleanup(steam.stop)
        # ficha df93: the admin-tools preflight verifies the tools a request
        # asks for and warns when it asks for none. These fixtures are policies
        # and stubs, not a server workspace, so the gate is neutralised here
        # exactly as the Steam one above is; its oracle is tests/test_vpp_preflight.py.
        vpp_patcher = patch.object(
            dayz_test_tool,
            "preflight_vpp_request",
            return_value=native_launcher_transaction.VppPreflightResult(
                error_code=None,
                missing=(),
                warnings=(),
                hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
            ),
        )
        vpp_patcher.start()
        self.addCleanup(vpp_patcher.stop)
        # The fake pids of the fixture must not be looked up on this host: a pid
        # that happens to exist would flip client_dead_after_ack and make the
        # outcome depend on the machine. A test that needs another answer sets
        # self.pid_alive.
        self.pid_alive: bool | None = True
        alive = patch.object(
            dayz_test_tool, "_pid_alive", side_effect=lambda _pid: self.pid_alive
        )
        alive.start()
        self.addCleanup(alive.stop)

    async def run_extension(
        self,
        *,
        with_client: bool = True,
        bridge: object = None,
        mode: str = "client",
        run_id: str | None = TOOL_RUN_ID,
        client_age_s: float = 3600.0,
        relaunch: bool = True,
        processes_override: object = _UNSET,
        client_start_budget_s: float | None = None,
        bridge_error: BaseException | None = None,
    ):
        policy = _policy()
        before = _extension_status(
            with_client=with_client,
            client_age_s=client_age_s,
            processes_override=processes_override,
        )
        # The row AFTER the call: the replacement retires the old client record
        # either way, and only a successful launch adds a new one. relaunch=False
        # is the shape of port_still_held -- the client is gone and nothing took
        # its place.
        after = _extension_status(
            with_client=False,
            client_age_s=client_age_s,
            with_new_client=relaunch,
        )
        runtime = _Runtime(before)
        seen: list[int] = []

        async def lifecycle_status() -> dict[str, object]:
            seen.append(1)
            runtime.lifecycle_calls += 1
            return before if len(seen) == 1 else after

        runtime.lifecycle_status = lifecycle_status  # type: ignore[method-assign]
        if bridge_error is not None:
            async def boom() -> dict[str, object]:
                runtime.bridge_calls += 1
                raise bridge_error

            runtime.bridge_status_payload = boom  # type: ignore[method-assign]
        elif bridge is _RAISES:
            runtime.bridge_raises = True
        else:
            runtime.bridge_payload = bridge
        sent: list[bytes] = []

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            sent.append(raw_request)
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": TOOL_RUN_ID,
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
                mode=mode,
                run_id=run_id,
                extra_mods=["@DayZ_MCP"],
                client_start_budget_s=client_start_budget_s,
            )
        return result, sent

    # ---------------------------------------------------------------- refusals
    async def test_a_polling_client_is_not_replaced_and_the_request_is_not_sent(
        self,
    ) -> None:
        result, sent = await self.run_extension(bridge={"client_peer": _POLLING_PEER})

        self.assertEqual(sent, [], "la peticion sellada NO debe salir")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "client_already_polling")
        self.assertEqual(result["client_replace_reason"], "client_polling")
        self.assertEqual(result["client_terminated"], 0)
        self.assertIs(result["client_relaunched"], False)
        self.assertEqual(result["client_last_poll_age_s"], 0.2)
        self.assertEqual(result["phase"], "validating")
        self.assertIn("dayz_test_stop", str(result["remediation"]))

    async def test_the_budget_override_crosses_the_executor(self) -> None:
        """The ONLY control that spans the executor-to-decision hop.

        The cross-family review removed ``budget_s=budget_s`` from the internal
        call by AST and every one of the new tests stayed green: they measured
        either the bare helper or the wrapper with the executor spied out, so
        nothing watched the hop between them. This is the same fixture as
        ``test_a_starting_client_is_not_replaced`` -- a 30 s client that has not
        polled -- with the override that makes 30 s old rather than starting.
        """
        default, sent_default = await self.run_extension(
            bridge={"client_peer": _STALE_PEER}, client_age_s=30.0
        )
        self.assertEqual(default["error_code"], "client_still_starting")
        self.assertEqual(sent_default, [], "sin override no debe salir la peticion")

        overridden, sent_over = await self.run_extension(
            bridge={"client_peer": _STALE_PEER},
            client_age_s=30.0,
            client_start_budget_s=5.0,
        )
        self.assertNotEqual(overridden["error_code"], "client_still_starting")
        self.assertNotEqual(sent_over, [], "con override de 5 s la peticion SALE")

    async def test_a_starting_client_is_not_replaced(self) -> None:
        """A4-H1: el que aun no ha sondeado NUNCA ha sondeado, no es un colgado."""
        result, sent = await self.run_extension(
            bridge={"client_peer": _STALE_PEER}, client_age_s=30.0
        )

        self.assertEqual(sent, [])
        self.assertEqual(result["error_code"], "client_still_starting")
        self.assertEqual(result["client_replace_reason"], "client_still_starting")
        self.assertEqual(result["client_terminated"], 0)
        self.assertIs(result["client_relaunched"], False)
        self.assertLess(float(result["client_record_age_s"]), 60.0)
        self.assertIn(_BUDGET_ENV, str(result["remediation"]))

    async def test_a_client_past_the_budget_is_replaced(self) -> None:
        """Control positivo del check anterior: solo cambia la edad del registro."""
        result, sent = await self.run_extension(
            bridge={"client_peer": _STALE_PEER}, client_age_s=3600.0
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["client_replace_reason"], "client_not_polling")

    async def test_an_unreadable_record_age_publishes_its_own_code(self) -> None:
        """A5-N2: con el puente LEGIBLE, decir error_code=bridge_status_unknown
        es la misma contradiccion que client_not_accredited vino a quitar. El
        motivo y el codigo salen de la misma decision."""
        result, sent = await self.run_extension(
            bridge={"client_peer": _STALE_PEER}, client_age_s=None
        )

        self.assertEqual(sent, [])
        self.assertEqual(result["client_replace_reason"], "client_record_age_unknown")
        self.assertEqual(result["error_code"], "client_record_age_unknown")
        self.assertIsNone(result["client_record_age_s"])
        self.assertIn("creation_time_utc", str(result["remediation"]))

    async def test_an_unreadable_bridge_keeps_its_own_code(self) -> None:
        """Control positivo del check anterior: la rama del puente ilegible SI
        publica bridge_status_unknown, asi que el codigo no es constante."""
        result, sent = await self.run_extension(bridge=_RAISES)

        self.assertEqual(sent, [])
        self.assertEqual(result["error_code"], "bridge_status_unknown")

    async def test_an_unreadable_bridge_refuses_and_says_what_to_do(self) -> None:
        """A3-F1: la rama DESCONOCIDO era fail-open hacia matar. Ya no."""
        result, sent = await self.run_extension(bridge=_RAISES)

        self.assertEqual(sent, [])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "bridge_status_unknown")
        self.assertEqual(result["client_replace_reason"], "bridge_status_unknown")
        self.assertEqual(result["client_terminated"], 0)
        self.assertIn("Retry", str(result["remediation"]))

    async def test_an_unreadable_bridge_publishes_the_exception_kind(self) -> None:
        """The mute except used to refuse without saying why the snapshot failed."""
        result, sent = await self.run_extension(bridge_error=TimeoutError())

        self.assertEqual(sent, [])
        self.assertEqual(result["error_code"], "bridge_status_unknown")
        self.assertEqual(result["client_replace_reason"], "bridge_status_unknown")
        self.assertEqual(result["bridge_status_cause"], "TimeoutError")

    async def test_distinct_unreadability_kinds_stay_distinct(self) -> None:
        """A discriminator that is the same for every exception discriminates nothing."""
        timeout, _ = await self.run_extension(bridge_error=TimeoutError())
        http, _ = await self.run_extension(
            bridge_error=HTTPError(
                "http://127.0.0.1/status",
                503,
                "Unavailable",
                EmailMessage(),
                None,
            )
        )
        refused, _ = await self.run_extension(
            bridge_error=OSError(111, "Connection refused")
        )

        self.assertEqual(timeout["error_code"], "bridge_status_unknown")
        self.assertEqual(http["error_code"], "bridge_status_unknown")
        self.assertEqual(refused["error_code"], "bridge_status_unknown")
        self.assertEqual(timeout["bridge_status_cause"], "TimeoutError")
        self.assertEqual(http["bridge_status_cause"], "HTTPError:503")
        self.assertEqual(refused["bridge_status_cause"], "OSError:111")
        self.assertEqual(
            len({
                timeout["bridge_status_cause"],
                http["bridge_status_cause"],
                refused["bridge_status_cause"],
            }),
            3,
        )

    async def test_a_call_shaped_toolerror_keeps_its_token(self) -> None:
        """ClientRuntime._call raises ToolError(token), not the OS exception.

        The positives above inject HTTPError and TimeoutError by replacing
        bridge_status_payload, so they never take the token branch. Deleting
        `return f"{name}:{token}"` left those 3/3 green while the form _call
        actually raises -- ToolError("daemon_unavailable") -- published as
        "ToolError". That token is a communication failure, not a crash, and
        it is not a retry signal: timeout and a refused connection collapse
        onto it in _call.
        """
        result, sent = await self.run_extension(
            bridge_error=ToolError("daemon_unavailable")
        )

        self.assertEqual(sent, [])
        self.assertEqual(result["error_code"], "bridge_status_unknown")
        self.assertEqual(result["client_replace_reason"], "bridge_status_unknown")
        self.assertEqual(result["client_terminated"], 0)
        self.assertEqual(
            result["bridge_status_cause"], "ToolError:daemon_unavailable"
        )

    async def test_a_readable_bridge_does_not_publish_a_cause(self) -> None:
        """A successful snapshot must keep today's shape: no sibling field at all."""
        polling, sent_polling = await self.run_extension(
            bridge={"client_peer": _POLLING_PEER}
        )
        self.assertEqual(sent_polling, [])
        self.assertEqual(polling["error_code"], "client_already_polling")
        self.assertNotIn("bridge_status_cause", polling)

        unreadable, sent_unreadable = await self.run_extension(
            bridge={"ready": {"ready": True, "reason": "ready"}}
        )
        self.assertEqual(sent_unreadable, [])
        self.assertEqual(unreadable["error_code"], "bridge_status_unknown")
        self.assertNotIn("bridge_status_cause", unreadable)

    async def test_a_snapshot_without_the_peer_row_also_refuses(self) -> None:
        result, sent = await self.run_extension(
            bridge={"ready": {"ready": True, "reason": "ready"}}
        )

        self.assertEqual(sent, [])
        self.assertEqual(result["error_code"], "bridge_status_unknown")

    async def test_an_unknown_pid_liveness_no_longer_opens_the_gate(self) -> None:
        """A4-H2: presencia se decide por el RECORD, no por su liveness."""
        self.pid_alive = None
        result, sent = await self.run_extension(bridge={"client_peer": _POLLING_PEER})

        self.assertEqual(sent, [])
        self.assertEqual(result["error_code"], "client_already_polling")
        self.assertEqual(result["client_replace_reason"], "client_polling")

    async def test_a_malformed_process_list_refuses_instead_of_authorising(self) -> None:
        """Codex C-01: una fila con `processes` malformado NO es «no hay cliente».

        El lifecycle, al otro lado del cable, ve su manifiesto integro, clasifica
        el cliente como `owned` y lo termina; asi que leer la forma rota como
        ausencia le entrega a la rama destructiva una licencia. Codex lo midio
        con un cliente hijo real: guard_terminate=[26420], old_client_alive=False.
        """
        for label, override in (
            ("processes es un dict", {"malformed": True}),
            ("processes es un string", "server,client"),
            ("una fila no es dict", [{"pid": 4001, "role": "server"}, "client"]),
            ("una fila sin role", [{"pid": 4001}]),
            ("una fila con pid invalido", [{"pid": 0, "role": "client"}]),
        ):
            with self.subTest(label):
                result, sent = await self.run_extension(
                    bridge={"client_peer": _POLLING_PEER}, processes_override=override
                )
                self.assertEqual(sent, [], "la peticion sellada NO debe salir")
                self.assertEqual(result["error_code"], "lifecycle_status_invalid")
                self.assertEqual(
                    result["client_replace_reason"], "lifecycle_status_invalid"
                )
                self.assertIsNone(result["client_terminated"])
                self.assertIsNone(result["client_relaunched"])

    async def test_a_well_formed_empty_process_list_is_still_an_absence(self) -> None:
        """Control positivo de C-01: una lista VALIDA y vacia si es una medida."""
        result, sent = await self.run_extension(
            bridge={"client_peer": _POLLING_PEER}, processes_override=[]
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(result["client_replace_reason"], "no_client_to_replace")

    async def test_a_peer_row_without_any_usable_age_refuses(self) -> None:
        """Codex C-02: ser un dict no es ser legible.

        _peer_is_live contesta False a una fila VACIA igual que a un peer que
        dejo de sondear, asi que con el record por encima del presupuesto la fila
        vacia caia en client_not_polling y mataba. Codex lo midio:
        terminate=[36700], vivo_despues=False.
        """
        for label, peer in (
            ("peer vacio", {}),
            ("BOUND sin ninguna edad", {"binding_state": "BOUND"}),
            ("edad no numerica", {"last_poll_age_s": "0.2"}),
            ("edad NaN", {"last_poll_age_s": float("nan")}),
            ("edad negativa", {"last_poll_age_s": -3.0}),
        ):
            with self.subTest(label):
                result, sent = await self.run_extension(bridge={"client_peer": peer})
                self.assertEqual(sent, [], "la peticion sellada NO debe salir")
                self.assertEqual(result["error_code"], "bridge_status_unknown")
                self.assertEqual(
                    result["client_replace_reason"], "bridge_status_unknown"
                )

    async def test_a_peer_row_with_one_usable_age_is_still_readable(self) -> None:
        """Control positivo de C-02: el arreglo NO cierra las filas parciales que
        SI traen evidencia. Un peer BOUND sin `bound_last_poll_age_s` pero con
        una edad cruda fresca sigue dando client_not_accredited y SI sale, que es
        lo que A5-6 midio y lo que BAJO 7 dejo abierto a proposito."""
        result, sent = await self.run_extension(
            bridge={"client_peer": {"binding_state": "BOUND", "last_poll_age_s": 0.2}}
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(result["client_replace_reason"], "client_not_accredited")

    # ---------------------------------------------------------------- replacements
    async def test_a_dead_client_is_replaced_even_with_a_fresh_peer(self) -> None:
        """A2-H1: un pid confirmado muerto no es un cliente sano que proteger."""
        self.pid_alive = False
        result, sent = await self.run_extension(bridge={"client_peer": _POLLING_PEER})

        self.assertEqual(len(sent), 1)
        self.assertEqual(result["client_replace_reason"], "client_process_dead")

    async def test_a_live_client_with_a_fresh_peer_is_the_negative_control(self) -> None:
        """El mismo peer fresco con el pid VIVO no reemplaza: la unica diferencia
        entre este check y el anterior es la respuesta de _pid_alive."""
        self.pid_alive = True
        result, sent = await self.run_extension(bridge={"client_peer": _POLLING_PEER})

        self.assertEqual(sent, [])
        self.assertEqual(result["client_replace_reason"], "client_polling")

    async def test_a_hung_client_is_replaced(self) -> None:
        result, sent = await self.run_extension(bridge={"client_peer": _STALE_PEER})

        self.assertEqual(len(sent), 1)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["client_replace_reason"], "client_not_polling")
        self.assertEqual(result["client_last_poll_age_s"], 41.0)

    async def test_a_run_without_a_client_declares_nothing_to_replace(self) -> None:
        result, sent = await self.run_extension(with_client=False, relaunch=True)

        self.assertEqual(len(sent), 1)
        self.assertEqual(result["client_replace_reason"], "no_client_to_replace")

    # ---------------------------------------------------------------- the truth
    async def test_the_result_measures_the_rows_not_the_worker_verdict(self) -> None:
        """A3-F2/F3: client_terminated y client_relaunched salen de las filas."""
        self.pid_alive = None  # el pid viejo no responde -> no se cuenta

        result, sent = await self.run_extension(bridge={"client_peer": _STALE_PEER})
        self.assertEqual(len(sent), 1)
        self.assertEqual(result["client_terminated"], 0)
        self.assertIs(result["client_relaunched"], True)

    async def test_a_terminated_client_that_is_not_relaunched_is_sayable(self) -> None:
        """La forma de port_still_held: el cliente muere y no se relanza.

        El worker sellado colapsa ese motivo a worker_failed
        (dayz_test_worker: port_still_held no esta en WORKER_ERROR_CODES), asi
        que el motivo NO viaja; lo que si viaja, y es lo que el operador
        necesita, son las dos cuentas medidas sobre la fila.
        """
        self.pid_alive = False
        result, sent = await self.run_extension(
            bridge={"client_peer": _STALE_PEER}, relaunch=False
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(result["client_terminated"], 1)
        self.assertIs(result["client_relaunched"], False)

    async def test_the_five_keys_are_always_published(self) -> None:
        """Negativo de forma: una llamada que no extiende nada los trae en null."""
        result, sent = await self.run_extension(
            mode="all", run_id=None, bridge={"client_peer": _POLLING_PEER}
        )

        self.assertEqual(len(sent), 1)
        for key in (
            "client_terminated",
            "client_relaunched",
            "client_replace_reason",
            "client_last_poll_age_s",
            "client_record_age_s",
        ):
            self.assertIn(key, result)
            self.assertIsNone(result[key])


_CLIENT_PID = 4002
_NEW_CLIENT_PID = 4003


def _extension_status(
    *,
    with_client: bool = True,
    client_age_s: float | None = 3600.0,
    client_pid: int = _CLIENT_PID,
    with_new_client: bool = False,
    processes_override: object = _UNSET,
) -> dict[str, object]:
    """A /lifecycle/status row shaped the way the daemon really publishes it.

    Faithful on creation_time_utc: _projected_run is dataclasses.asdict over a
    ProcessRecord, and ProcessRecord.validate rejects an empty one, so every
    process row carries it in production. A fixture that omits it sends the gate
    down the client_record_age_unknown branch and measures the wrong thing.
    """
    # client_age_s=None publishes the row the auditors' fixtures publish: no
    # creation_time_utc at all, which is what the fail-closed branch answers to.
    born = (
        None
        if client_age_s is None
        else time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - client_age_s)
        )
        + ".000000Z"
    )
    server_row: dict[str, object] = {"pid": 4001, "role": "server"}
    client_row: dict[str, object] = {"pid": client_pid, "role": "client"}
    if born is not None:
        server_row["creation_time_utc"] = born
        client_row["creation_time_utc"] = born
    processes: list[dict[str, object]] = [server_row]
    if with_client:
        processes.append(client_row)
    if with_new_client:
        processes.append(
            {
                "pid": _NEW_CLIENT_PID,
                "role": "client",
                "creation_time_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.gmtime()
                )
                + ".000000Z",
            }
        )
    return {
        "runs": [
            {
                "run_id": TOOL_RUN_ID,
                "state": "RUNNING_IDLE",
                "mod": "@ExampleMod",
                "profiles": r"P:\ExampleMod_Suite\_client\profiles",
                "launch_acknowledged": True,
                "processes": (
                    processes if processes_override is _UNSET else processes_override
                ),
            }
        ]
    }


if __name__ == "__main__":
    unittest.main()
