"""inbox 6310: human_input counts on the lease holder's heartbeat and /await.

The 250f sampler already stamps the latest input per run. These tests pin the
cumulative counts and the report block that heartbeat and POST /await answers
carry for the lease holder, using fake InputSamples, FakeGuard, and an
in-process loopback. They do not call a real Windows API or start a real
process.
"""

from __future__ import annotations

import http.client
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, process_lifecycle
from dayz_mcp.input_activity import InputSample
from dayz_mcp.process_lifecycle import (
    INPUT_SIGNAL_STALE_S,
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.fence_helpers import INST_SERVER
from tests.lease_helpers import FakeClock
from tests.mcp_helpers import _content_json
from tests.process_lifecycle_helpers import (
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.session_http_helpers import SessionLoopback, _http
from tests.steam_helpers import FakeSteamGate


GENERATION = "gen-6310"
CLIENT_PID = 802
OTHER_PID = 31337
# The run SessionLoopback binds both peers to (fence_helpers.bind_both_peers).
BOUND_RUN = "test-run"
IDENTITY_PAYLOAD = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-14T10:00:00Z",
    "session_id": "A",
    "task_label": "test",
}
STRANGER_PAYLOAD = dict(IDENTITY_PAYLOAD, session_id="B", pid=12)
BLOCK_KEYS = {"signal", "runs"}
ROW_KEYS = {"run_id", "count", "uncertain_count", "last_age_s"}
UNAVAILABLE = {"signal": "unavailable", "runs": []}


class FakeBindings:
    def __init__(self) -> None:
        self.bound: set[str] = set()

    def run_has_bound_binding(self, run_id: str) -> bool:
        return run_id in self.bound

    def fence_runs(self, run_ids: list[str]) -> None:
        return None

    def unfence_runs(self, run_ids: list[str]) -> None:
        return None


class ArmedLock:
    """Stands in for _activity_lock. The first acquire after arm() runs a hook."""

    def __init__(self, lock) -> None:
        self._lock = lock
        self._hook = None

    def arm(self, hook) -> None:
        self._hook = hook

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        hook, self._hook = self._hook, None
        if hook is not None:
            hook()
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "ArmedLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class ContentionProbe:
    """Stands in for the manifest lock. The first acquire after arm() first
    tries without waiting, and records whether another thread held the lock."""

    def __init__(self, lock) -> None:
        self._lock = lock
        self._armed = False
        self.tried = threading.Event()
        self.was_held: bool | None = None

    def arm(self) -> None:
        self._armed = True

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if self._armed:
            self._armed = False
            free = self._lock.acquire(False)
            self.was_held = not free
            self.tried.set()
            if free:
                return True
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "ContentionProbe":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class RecordingLock:
    """Stands in for a lock and records how each acquire asked to wait."""

    def __init__(self, lock, name: str, calls: list) -> None:
        self._lock = lock
        self._name = name
        self._calls = calls

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        self._calls.append((self._name, blocking, timeout))
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "RecordingLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class CountingWorkers(threading.BoundedSemaphore):
    """`_http_workers` that can wait until every slot taken is back.

    The server gives a slot back when its worker ends, after the answer.
    """

    def __init__(self, value: int) -> None:
        super().__init__(value)
        self._idle = threading.Condition()
        self._taken = 0

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        taken = super().acquire(blocking, timeout)
        if taken:
            with self._idle:
                self._taken += 1
        return taken

    def release(self, n: int = 1) -> None:
        super().release(n)
        with self._idle:
            self._taken -= n
            self._idle.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: self._taken == 0, timeout)


class HumanInputHolderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        paths = RuntimePaths(
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
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.live: list[int] = []
        self.bindings = FakeBindings()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {
                "known": True,
                "processes": [
                    {"pid": pid, "name": "DayZDiag_x64.exe"} for pid in self.live
                ],
            },
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            bindings=self.bindings,
            daemon_generation=GENERATION,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(
        self,
        run_id: str = "run-x",
        *,
        owner: str | None = None,
        lease_id: str | None = None,
        processes=None,
        generation: str | None = GENERATION,
        live: bool = True,
    ) -> RunRecord:
        records = processes if processes is not None else [
            process(801, role="server"),
            process(CLIENT_PID, role="client"),
        ]
        lease = lease_id if owner else None
        if owner and lease is None:
            lease = "lease-A"
        run = RunRecord(
            run_id,
            owner,
            lease,
            "RUNNING" if owner else "RUNNING_IDLE",
            "label",
            "@Mod",
            "profiles",
            "mission",
            list(records),
            daemon_generation_at_launch=generation,
        )
        self.store.add(run)
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
            if live:
                self.live.append(record.pid)
        return run

    def sample(
        self,
        at: float,
        *,
        tick: int,
        foreground: int,
        idle_ms: int = 0,
        ok: bool = True,
    ) -> list[str]:
        return self.lifecycle.record_input_sample(
            InputSample(
                at=at,
                ok=ok,
                last_input_tick=tick if ok else None,
                idle_ms=idle_ms if ok else None,
                foreground_pid=foreground if ok else None,
            )
        )

    def credit_three_verified(self, start: float = 1000.0) -> float:
        self.sample(start, tick=100, foreground=CLIENT_PID)
        self.sample(start + 1.0, tick=200, foreground=CLIENT_PID)
        self.sample(start + 2.0, tick=300, foreground=CLIENT_PID)
        last_at = start + 3.0
        self.sample(last_at, tick=400, foreground=CLIENT_PID)
        return last_at

    def assert_block_shape(self, block: object) -> dict:
        self.assertIsInstance(block, dict)
        self.assertEqual(set(block), BLOCK_KEYS)
        self.assertIsInstance(block["runs"], list)
        return block

    def test_t1_counters_credit_stamp_updates_only(self) -> None:
        self.add_run(owner="A")
        last_at = self.credit_three_verified()
        clock = last_at + 0.5
        block = self.lifecycle.human_input_block("A", "lease-A", now=clock)
        block = self.assert_block_shape(block)
        self.assertEqual(len(block["runs"]), 1)
        row = block["runs"][0]
        self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual(row["run_id"], "run-x")
        self.assertEqual(row["count"], 3)
        self.assertEqual(row["uncertain_count"], 0)
        self.assertEqual(row["last_age_s"], round(clock - last_at, 3))
        # A stamp later than the clock has no age, as in human_input_age_s.
        early = self.lifecycle.human_input_block("A", "lease-A", now=last_at - 1.0)
        self.assertIsNone(early["runs"][0]["last_age_s"])
        self.assertEqual(early["runs"][0]["count"], 3)

        # A tick that did not advance, then new input with another pid in front
        # at both ends of the step: the attributor credits the run in front at
        # either end (input_activity.py:196-208), so the focus leaves first.
        self.sample(clock, tick=400, foreground=CLIENT_PID)
        self.sample(clock + 0.1, tick=400, foreground=OTHER_PID)
        self.sample(clock + 0.2, tick=500, foreground=OTHER_PID)
        again = self.lifecycle.human_input_block("A", "lease-A", now=clock + 0.3)
        again = self.assert_block_shape(again)
        self.assertEqual(len(again["runs"]), 1)
        self.assertEqual(again["runs"][0]["count"], 3)
        self.assertEqual(again["runs"][0]["uncertain_count"], 0)

        del self.guard.snapshots[CLIENT_PID]
        self.sample(clock + 0.4, tick=600, foreground=CLIENT_PID)
        uncertain = self.lifecycle.human_input_block("A", "lease-A", now=clock + 0.5)
        uncertain = self.assert_block_shape(uncertain)
        self.assertEqual(len(uncertain["runs"]), 1)
        self.assertEqual(uncertain["runs"][0]["count"], 3)
        self.assertEqual(uncertain["runs"][0]["uncertain_count"], 1)

        self.guard.snapshots[CLIENT_PID] = snapshot(process(CLIENT_PID, role="client"))
        self.sample(
            clock + 0.6, tick=700, foreground=CLIENT_PID, idle_ms=20000
        )
        stale_epoch = self.lifecycle.human_input_block("A", "lease-A", now=clock + 0.7)
        stale_epoch = self.assert_block_shape(stale_epoch)
        self.assertEqual(len(stale_epoch["runs"]), 1)
        self.assertEqual(stale_epoch["runs"][0]["count"], 3)
        self.assertEqual(stale_epoch["runs"][0]["uncertain_count"], 1)

        self.lifecycle._seal_terminal("run-x", clock + 1.0)
        self.store.replace(
            RunRecord(
                "run-x",
                None,
                None,
                "EXITED",
                "label",
                "@Mod",
                "profiles",
                "mission",
                [],
            )
        )
        retired = self.lifecycle.human_input_block("A", "lease-A", now=clock + 1.0)
        retired = self.assert_block_shape(retired)
        self.assertEqual(retired["runs"], [])
        self.assertFalse(
            [key for key in self.lifecycle._human_input_count if key[1] == "run-x"]
        )
        self.assertFalse(
            [key for key in self.lifecycle._uncertain_input_count if key[1] == "run-x"]
        )

    def test_t1_equal_epoch_counts_once(self) -> None:
        # Both later samples put the input at 1001 (at minus idle): the second
        # does not move the stamp, so it does not count either.
        self.add_run(owner="A")
        self.sample(1000.0, tick=100, foreground=CLIENT_PID)
        self.sample(1001.0, tick=200, foreground=CLIENT_PID)
        self.sample(1002.0, tick=300, foreground=CLIENT_PID, idle_ms=1000)
        block = self.lifecycle.human_input_block("A", "lease-A", now=1002.5)
        block = self.assert_block_shape(block)
        self.assertEqual(
            [(row["count"], row["last_age_s"]) for row in block["runs"]],
            [(1, 1.5)],
        )

    def test_t2_block_filters_owner_and_signal(self) -> None:
        self.add_run(owner="A")
        last_at = self.credit_three_verified()
        clock = last_at + 1.0
        owned = self.lifecycle.human_input_block("A", "lease-A", now=clock)
        owned = self.assert_block_shape(owned)
        self.assertEqual(owned["signal"], "ok")
        self.assertEqual(len(owned["runs"]), 1)
        self.assertEqual(owned["runs"][0]["run_id"], "run-x")
        self.assertEqual(owned["runs"][0]["count"], 3)
        # ok from 0 s to INPUT_SIGNAL_STALE_S after the last good sample, both
        # ends included; a clock before that sample is not ok.
        for offset, expected in (
            (-0.001, "unavailable"),
            (0.0, "ok"),
            (INPUT_SIGNAL_STALE_S, "ok"),
            (INPUT_SIGNAL_STALE_S + 0.001, "unavailable"),
        ):
            with self.subTest(offset=offset):
                edge = self.lifecycle.human_input_block(
                    "A", "lease-A", now=last_at + offset
                )
                self.assertEqual(edge["signal"], expected)

        self.assertEqual(
            self.lifecycle.human_input_block("A", "lease-B", now=clock)["runs"],
            [],
        )
        self.assertEqual(
            self.lifecycle.human_input_block("B", "lease-A", now=clock)["runs"],
            [],
        )
        self.assertEqual(
            self.lifecycle.human_input_block("", "lease-A", now=clock)["runs"],
            [],
        )
        self.assertEqual(
            self.lifecycle.human_input_block("A", "", now=clock)["runs"],
            [],
        )

        self.add_run("run-idle")
        idle_block = self.lifecycle.human_input_block("A", "lease-A", now=clock)
        idle_block = self.assert_block_shape(idle_block)
        self.assertEqual([row["run_id"] for row in idle_block["runs"]], ["run-x"])

        self.add_run(
            "run-a",
            owner="A",
            processes=[process(901, role="server"), process(902, role="client")],
        )
        both = self.lifecycle.human_input_block("A", "lease-A", now=clock)
        self.assertEqual(
            [(row["run_id"], row["count"], row["last_age_s"]) for row in both["runs"]],
            [("run-a", 0, None), ("run-x", 3, round(clock - last_at, 3))],
        )

        fresh = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            daemon_generation=GENERATION,
        )
        none = fresh.human_input_block("A", "lease-A", now=time.time())
        none = self.assert_block_shape(none)
        self.assertEqual(none["signal"], "unavailable")

        stale_at = last_at + INPUT_SIGNAL_STALE_S + 1.0
        stale = self.lifecycle.human_input_block("A", "lease-A", now=stale_at)
        stale = self.assert_block_shape(stale)
        self.assertEqual(stale["signal"], "unavailable")

        failed_clock = last_at + 3.0
        self.lifecycle.record_input_sample(InputSample(at=failed_clock, ok=False))
        failed = self.lifecycle.human_input_block("A", "lease-A", now=failed_clock)
        failed = self.assert_block_shape(failed)
        self.assertEqual(failed["signal"], "unavailable")

    def test_t2_clock_is_read_with_both_locks_held(self) -> None:
        # The last good sample is at 1000, and the production clock reads
        # 1001.99 until the report takes _activity_lock. Read there, the
        # sample is 1.99 s old and the signal ok; if the clock moves to
        # 1002.03 while the lock is taken, a reading made with both locks
        # held finds it 2.03 s old.
        self.add_run(owner="A")
        self.sample(1000.0, tick=100, foreground=CLIENT_PID)
        clock = [1001.99]
        armed = ArmedLock(self.lifecycle._activity_lock)
        self.lifecycle._activity_lock = armed  # type: ignore[assignment]
        with mock.patch.object(process_lifecycle.time, "time", lambda: clock[0]):
            steady = self.lifecycle.human_input_block("A", "lease-A")
            armed.arm(lambda: clock.__setitem__(0, 1002.03))
            crossed = self.lifecycle.human_input_block("A", "lease-A")
        self.assertEqual(steady["signal"], "ok")
        self.assertEqual(crossed["signal"], "unavailable")
        self.assertEqual([row["run_id"] for row in crossed["runs"]], ["run-x"])

    def test_t9_the_report_never_waits_for_a_lock(self) -> None:
        # Deterministic, unlike the busy-lock HTTP test: each lock the report
        # takes is asked with blocking=False, so no wait budget is spent.
        self.add_run(owner="A")
        last_at = self.credit_three_verified()
        calls: list[tuple[str, bool, float]] = []
        self.store._lock = RecordingLock(self.store._lock, "manifest", calls)
        self.lifecycle._activity_lock = RecordingLock(  # type: ignore[assignment]
            self.lifecycle._activity_lock, "activity", calls
        )
        block = self.lifecycle.human_input_block("A", "lease-A", now=last_at + 0.5)
        self.assertEqual([row["count"] for row in block["runs"]], [3])
        self.assertEqual(calls, [("manifest", False, -1), ("activity", False, -1)])

        condition = self.coordinator._condition
        self.coordinator._condition = RecordingLock(condition, "coordinator", calls)
        try:
            lease_id = self.coordinator.active_lease_id(
                IDENTITY_A, self.token_a, blocking=False
            )
        finally:
            self.coordinator._condition = condition
        self.assertEqual(lease_id, "lease-A")
        self.assertEqual(calls[-1], ("coordinator", False, -1))

        # Another thread holding a lock gives the fallback, without waiting.
        for name, lock in (
            ("manifest", self.store._lock),
            ("activity", self.lifecycle._activity_lock),
        ):
            with self.subTest(lock=name):
                held = threading.Event()
                done = threading.Event()

                def hold(lock=lock, held=held, done=done) -> None:
                    lock.acquire()
                    try:
                        held.set()
                        done.wait(30.0)
                    finally:
                        lock.release()

                holder = threading.Thread(target=hold, daemon=True)
                holder.start()
                self.assertTrue(held.wait(30.0))
                try:
                    busy = self.lifecycle.human_input_block(
                        "A", "lease-A", now=last_at + 0.5
                    )
                finally:
                    done.set()
                    holder.join(30.0)
                self.assertFalse(holder.is_alive())
                self.assertEqual(busy, UNAVAILABLE)

    def test_t8_an_expired_lease_names_no_holder(self) -> None:
        clock = FakeClock()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=lambda: "token-E",
            id_fn=lambda: "lease-E",
            audit=AuditSink(),
        )
        status, acquired = coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        token = acquired["lease_token"]
        lease = coordinator._active
        expires_at = lease.effective_expiry()
        clock.value = expires_at - 0.001
        self.assertEqual(coordinator.active_lease_id(IDENTITY_A, token), "lease-E")
        clock.value = expires_at
        self.assertIsNone(coordinator.active_lease_id(IDENTITY_A, token))
        self.assertIsNone(
            coordinator.active_lease_id(IDENTITY_A, token, blocking=False)
        )
        # The check reads the clock only: the lease is neither renewed nor
        # ended, which _expire_due does on the next call that runs it.
        self.assertIs(coordinator._active, lease)
        self.assertEqual(lease.effective_expiry(), expires_at)

    def test_t8_a_pin_past_ttl_still_names_the_lease(self) -> None:
        # EX1: active_lease_id compares against effective_expiry, not expires_at.
        clock = FakeClock()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=lambda: "token-E",
            id_fn=lambda: "lease-E",
            audit=AuditSink(),
        )
        status, acquired = coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        token = acquired["lease_token"]
        c0 = clock.value
        coordinator.authorize(
            IDENTITY_A, token, "notify_players", operation_timeout_s=180
        )
        lease = coordinator._active
        self.assertEqual(lease.expires_at, c0 + 120.0)
        pending = lease.pending_authorizations
        self.assertEqual(len(pending), 1, f"pending authorizations: {pending!r}")
        self.assertEqual(pending[0].deadline, c0 + 180.0)
        clock.value = c0 + 130
        self.assertEqual(coordinator.active_lease_id(IDENTITY_A, token), "lease-E")
        self.assertEqual(
            coordinator.active_lease_id(IDENTITY_A, token, blocking=False),
            "lease-E",
        )
        clock.value = c0 + 180
        self.assertIsNone(coordinator.active_lease_id(IDENTITY_A, token))
        clock.value = c0 + 180.5
        self.assertIsNone(
            coordinator.active_lease_id(IDENTITY_A, token, blocking=False)
        )
        self.assertIs(coordinator._active, lease)

    def test_t9_owner_and_counts_come_from_one_snapshot(self) -> None:
        self.add_run(owner="A")
        last_at = self.credit_three_verified()
        armed = ArmedLock(self.lifecycle._activity_lock)
        self.lifecycle._activity_lock = armed  # type: ignore[assignment]
        probe = ContentionProbe(self.store._lock)
        self.store._lock = probe

        def retire() -> None:
            # The order of _commit_retirement: persist EXITED, then drop the
            # run's counters.
            self.store.replace(
                RunRecord(
                    "run-x",
                    None,
                    None,
                    "EXITED",
                    "label",
                    "@Mod",
                    "profiles",
                    "mission",
                    [],
                )
            )
            self.lifecycle._seal_terminal("run-x", last_at + 1.0)

        worker = threading.Thread(target=retire, daemon=True)

        def race() -> None:
            # Runs when the block asks for the counters. The retirement's
            # first try at the manifest lock must find it held: one that
            # could land here would publish the RUNNING run with its
            # counters gone. The wait is only a bound on a hung worker.
            probe.arm()
            worker.start()
            self.assertTrue(probe.tried.wait(30.0), "the worker never asked")

        armed.arm(race)
        block = self.lifecycle.human_input_block("A", "lease-A", now=last_at + 0.5)
        worker.join(30.0)
        self.assertFalse(worker.is_alive())
        self.assertIs(probe.was_held, True)
        self.assertEqual(
            [(row["run_id"], row["count"]) for row in block["runs"]],
            [("run-x", 3)],
        )
        after = self.lifecycle.human_input_block("A", "lease-A", now=last_at + 1.5)
        self.assertEqual(after["runs"], [])


class HumanInputHttpTest(SessionLoopback, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.logs: list[str] = []
        self.httpd.log_sink = self.logs.append
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.manifest = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.live: list[int] = []
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.manifest,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {
                "known": True,
                "processes": [
                    {"pid": pid, "name": "DayZDiag_x64.exe"} for pid in self.live
                ],
            },
            game_path=self.game,
            launcher=self.launcher,
            daemon_generation=GENERATION,
        )
        self.state.lifecycle = self.lifecycle

    def tearDown(self) -> None:
        super().tearDown()
        self.temporary.cleanup()

    def acquire(self) -> tuple[str, dict]:
        status, body = self.request(
            "/session/acquire",
            {"identity": IDENTITY_PAYLOAD, "purpose": "test"},
        )
        self.assertEqual(status, 200)
        return body["lease_token"], body

    def add_owned_run(self, lease_id: str) -> None:
        records = [
            process(801, role="server"),
            process(CLIENT_PID, role="client"),
        ]
        run = RunRecord(
            BOUND_RUN,
            "A",
            lease_id,
            "RUNNING",
            "label",
            "@Mod",
            "profiles",
            "mission",
            list(records),
            daemon_generation_at_launch=GENERATION,
        )
        self.manifest.add(run)
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
            self.live.append(record.pid)

    def credit_one(self, at: float = 1000.0) -> None:
        self.lifecycle.record_input_sample(
            InputSample(
                at=at,
                ok=True,
                last_input_tick=100,
                idle_ms=0,
                foreground_pid=CLIENT_PID,
            )
        )
        self.lifecycle.record_input_sample(
            InputSample(
                at=at + 1.0,
                ok=True,
                last_input_tick=200,
                idle_ms=0,
                foreground_pid=CLIENT_PID,
            )
        )

    def test_t3_heartbeat_carries_block_only_on_200(self) -> None:
        token, acquired = self.acquire()
        lease_id = acquired["lease_id"]
        self.add_owned_run(lease_id)
        self.credit_one()
        status, body = self.request(
            "/session/heartbeat",
            {"identity": IDENTITY_PAYLOAD, "lease_token": token},
        )
        self.assertEqual(status, 200)
        self.assertIn("human_input", body)
        block = body["human_input"]
        self.assertEqual(set(block), BLOCK_KEYS)
        self.assertEqual(len(block["runs"]), 1)
        self.assertEqual(block["runs"][0]["run_id"], BOUND_RUN)
        self.assertEqual(block["runs"][0]["count"], 1)

        status, err = self.request(
            "/session/heartbeat",
            {"identity": IDENTITY_PAYLOAD, "lease_token": "not-this-lease"},
        )
        self.assertNotEqual(status, 200)
        self.assertNotIn("human_input", err)

        self.state.lifecycle = None
        status, plain = self.request(
            "/session/heartbeat",
            {"identity": IDENTITY_PAYLOAD, "lease_token": token},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("human_input", plain)

    def enqueue_done(self, token: str | None = None) -> int:
        """query_player_state, enqueued under the lease when token is given, done."""
        payload: dict[str, object] = {
            "identity": IDENTITY_PAYLOAD,
            "cmd": "query_player_state",
            "args": {},
            "peer": "server",
        }
        if token is not None:
            payload["lease_token"] = token
        status, enqueued = self.request("/enqueue", payload)
        self.assertEqual(status, 200, enqueued)
        command_id = enqueued["id"]
        wire = {
            "id": command_id,
            "ok": 1,
            "cmd": "query_player_state",
            "state": {"pos": [1.0, 2.0, 3.0]},
        }
        stored_status, stored = self.state.store_result(wire, instance=INST_SERVER)
        self.assertEqual(stored_status, 200, stored)
        self.assertNotIn("discarded", stored)
        return command_id

    def post_await(
        self,
        command_id: int,
        token: str | None,
        *,
        identity: dict = IDENTITY_PAYLOAD,
        remove: str = "1",
        timeout: float = 2.0,
    ) -> tuple[int, dict]:
        payload: dict[str, object] = {"identity": identity}
        if token is not None:
            payload["lease_token"] = token
        return _http(
            self.base,
            "POST",
            "/await",
            self.key,
            payload,
            {"id": str(command_id), "remove": remove},
            timeout=timeout,
        )

    def _join_human_input_diagnostics(self) -> None:
        living = [
            thread
            for thread in threading.enumerate()
            if thread.name == "mcp-human-input-diagnostic"
        ]
        for thread in living:
            thread.join(30.0)
            self.assertFalse(
                thread.is_alive(),
                f"{thread.name} still running",
            )

    def test_t4_await_adds_block_only_for_owned_done(self) -> None:
        token, acquired = self.acquire()
        lease_id = acquired["lease_id"]
        self.add_owned_run(lease_id)
        self.credit_one()
        command_id = self.enqueue_done(token)
        # GET /await names no caller, so it never carries the block.
        status, plain = _http(
            self.base,
            "GET",
            "/await",
            self.key,
            query={"id": str(command_id), "remove": "0"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(plain), {"status", "result"})
        status, done = self.post_await(command_id, token)
        self.assertEqual(status, 200)
        self.assertEqual(set(done), {"status", "result", "human_input"})
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"]["ok"], 1)
        self.assertEqual(done["result"]["state"]["pos"], [1.0, 2.0, 3.0])
        self.assertNotIn("human_input", done["result"])
        block = done["human_input"]
        self.assertEqual(set(block), BLOCK_KEYS)
        self.assertEqual(len(block["runs"]), 1)
        self.assertEqual(block["runs"][0]["count"], 1)

        foreign_id = self.enqueue_done()
        status, no_owner = self.post_await(foreign_id, token)
        self.assertEqual(status, 200)
        self.assertEqual(set(no_owner), {"status", "result"})
        self.assertEqual(no_owner["status"], "done")

        status, pending_enq = self.request(
            "/enqueue",
            {
                "identity": IDENTITY_PAYLOAD,
                "lease_token": token,
                "cmd": "query_player_state",
                "args": {},
                "peer": "server",
            },
        )
        self.assertEqual(status, 200)
        status, pending = self.post_await(pending_enq["id"], token)
        self.assertEqual(status, 200)
        self.assertEqual(pending, {"status": "pending"})
        for identity in (
            {},
            dict(IDENTITY_PAYLOAD, platform=[]),
            dict(IDENTITY_PAYLOAD, platform={}),
        ):
            with self.subTest(identity=identity):
                status, refused = self.answered(
                    lambda identity=identity: _http(
                        self.base,
                        "POST",
                        "/await",
                        self.key,
                        {"identity": identity},
                        {"id": str(pending_enq["id"]), "remove": "1"},
                    )
                )
                self.assertEqual(
                    (status, refused), (400, {"error": "invalid_identity"})
                )

    def test_t8_only_the_live_holder_gets_the_block(self) -> None:
        token, acquired = self.acquire()
        self.add_owned_run(acquired["lease_id"])
        self.credit_one()
        command_id = self.enqueue_done(token)
        for identity, presented in (
            (STRANGER_PAYLOAD, None),
            (STRANGER_PAYLOAD, token),
            (IDENTITY_PAYLOAD, None),
            (IDENTITY_PAYLOAD, "not-this-lease"),
        ):
            with self.subTest(session=identity["session_id"], token=presented):
                status, body = self.post_await(
                    command_id, presented, identity=identity, remove="0"
                )
                self.assertEqual(status, 200)
                self.assertEqual(body["status"], "done")
                self.assertNotIn("human_input", body)
        # The holder gets it on every read that keeps the result.
        for _ in range(2):
            status, body = self.post_await(command_id, token, remove="0")
            self.assertEqual(status, 200)
            self.assertIn("human_input", body)
            self.assertEqual(body["human_input"]["runs"][0]["count"], 1)

        status, released = self.request(
            "/session/release",
            {"identity": IDENTITY_PAYLOAD, "lease_token": token},
        )
        self.assertEqual(status, 200, released)
        status, body = self.post_await(command_id, token, remove="0")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "done")
        self.assertNotIn("human_input", body)
        # The next lease's token does not name the lease the command came with.
        next_token, _acquired = self.acquire()
        status, body = self.post_await(command_id, next_token)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "done")
        self.assertNotIn("human_input", body)

    def test_t8_an_expired_holder_gets_no_block(self) -> None:
        token, acquired = self.acquire()
        self.add_owned_run(acquired["lease_id"])
        self.credit_one()
        command_id = self.enqueue_done(token)
        status, body = self.post_await(command_id, token, remove="0")
        self.assertEqual(status, 200)
        self.assertIn("human_input", body)
        lease = self.coordinator._active
        expiry = lease.effective_expiry()
        # Past its expiry, before any call that runs _expire_due: the lease
        # is still active in memory, and still gets no block.
        self.coordinator._time_fn = lambda: expiry + 1.0
        status, body = self.post_await(command_id, token, remove="0")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "done")
        self.assertNotIn("human_input", body)
        self.assertIs(self.coordinator._active, lease)

    def test_t9_a_busy_lock_does_not_delay_the_answer(self) -> None:
        token, acquired = self.acquire()
        self.add_owned_run(acquired["lease_id"])
        self.credit_one()
        beat = {"identity": IDENTITY_PAYLOAD, "lease_token": token}
        # This thread holds the lock for the whole request, so a report that
        # waited for it without a bound would answer after the request's
        # timeout, which answered() fails. That a bounded wait is not asked
        # for either is pinned by test_t9_the_report_never_waits_for_a_lock.
        for name, lock in (
            ("manifest", self.manifest._lock),
            ("activity", self.lifecycle._activity_lock),
        ):
            with self.subTest(lock=name):
                command_id = self.enqueue_done(token)
                with lock:
                    beat_status, beat_body = self.answered(
                        lambda: _http(
                            self.base,
                            "POST",
                            "/session/heartbeat",
                            self.key,
                            beat,
                            timeout=30.0,
                        )
                    )
                    done_status, done = self.answered(
                        lambda: self.post_await(
                            command_id, token, timeout=30.0
                        )
                    )
                self.assertEqual(beat_status, 200)
                self.assertEqual(beat_body.get("human_input"), UNAVAILABLE)
                self.assertEqual(done_status, 200)
                self.assertEqual(done["status"], "done")
                self.assertEqual(done["result"]["ok"], 1)
                self.assertEqual(done.get("human_input"), UNAVAILABLE)

        # The coordinator lock: the lease check does not wait for it, so no
        # block. remove=0, because consuming a leased result waits on that
        # lock in main too (finish_operation_exact).
        asked: list[dict] = []
        check = self.coordinator.active_lease_id

        def spy(*args, **kwargs):
            asked.append(dict(kwargs))
            return check(*args, **kwargs)

        self.coordinator.active_lease_id = spy  # type: ignore[method-assign]
        command_id = self.enqueue_done(token)
        with self.coordinator._condition:
            status, done = self.answered(
                lambda: self.post_await(
                    command_id, token, remove="0", timeout=30.0
                )
            )
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "done")
        self.assertNotIn("human_input", done)
        status, done = self.post_await(command_id, token)
        self.assertEqual(status, 200)
        self.assertIn("human_input", done)
        self.assertEqual(done["human_input"]["runs"][0]["count"], 1)
        self.assertEqual(asked, [{"blocking": False}, {"blocking": False}])

    def answered(self, call):
        # M9: an exception that escapes the handler drops the connection, and a
        # handler stuck on a lock lets the request time out. Both fail the test
        # as an assertion, not an error.
        try:
            return call()
        except (OSError, http.client.HTTPException) as exc:
            self.fail(f"the daemon did not answer: {exc!r}")

    def test_t7_block_exception_does_not_fail_the_call(self) -> None:
        token, acquired = self.acquire()
        lease_id = acquired["lease_id"]
        self.add_owned_run(lease_id)

        def boom(*_args, **_kwargs):
            raise RuntimeError("inbox-6310-boom")

        self.lifecycle.human_input_block = boom  # type: ignore[method-assign]
        # The failure line is written after the answer, so the test waits
        # for it instead of reading the log as the answer arrives.
        failure_logged = threading.Event()

        def sink(message: str) -> None:
            self.logs.append(message)
            if "human_input_block failed" in message:
                failure_logged.set()

        self.httpd.log_sink = sink
        self.logs.clear()
        status, beat = self.answered(
            lambda: self.request(
                "/session/heartbeat",
                {"identity": IDENTITY_PAYLOAD, "lease_token": token},
            )
        )
        self.assertEqual(status, 200)
        self.assertIn("human_input", beat)
        self.assertEqual(beat["human_input"], UNAVAILABLE)
        self.assertTrue(failure_logged.wait(30.0))
        # The sink sets the event before its thread frees the slot.
        self._join_human_input_diagnostics()
        self.assertEqual(
            sum(1 for line in self.logs if "human_input_block failed" in line),
            1,
        )

        command_id = self.enqueue_done(token)
        self.logs.clear()
        failure_logged.clear()
        status, done = self.answered(lambda: self.post_await(command_id, token))
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"]["state"]["pos"], [1.0, 2.0, 3.0])
        self.assertEqual(done.get("human_input"), UNAVAILABLE)
        self.assertTrue(failure_logged.wait(30.0))
        self._join_human_input_diagnostics()
        self.assertEqual(
            sum(1 for line in self.logs if "human_input_block failed" in line),
            1,
        )

        # A sink that blocks on the failure line, as a write to a stderr pipe
        # nobody reads can, then one that raises on it: both answers still
        # come, the consumed result whole, since the line goes after them.
        release = threading.Event()
        stuck = threading.Event()
        raised = threading.Event()

        def stuck_sink(message: str) -> None:
            if "human_input_block failed" in message:
                stuck.set()
                release.wait(60.0)

        def broken_sink(message: str) -> None:
            if "human_input_block failed" in message:
                raised.set()
                raise OSError("inbox-6310-sink")

        # A line written before the answer would hold it for the sink's 60 s,
        # far past these 10 s (M28, M29).
        def heartbeat() -> tuple[int, dict]:
            return _http(
                self.base,
                "POST",
                "/session/heartbeat",
                self.key,
                {"identity": IDENTITY_PAYLOAD, "lease_token": token},
                timeout=10.0,
            )

        def await_done() -> tuple[int, dict]:
            return self.post_await(command_id, token, timeout=10.0)

        # The diagnostic thread swallows a sink raise; this still keeps a
        # leaked handler traceback out of the test output.
        self.httpd.handle_error = lambda _request, _address: None  # type: ignore[method-assign]
        for log_sink, logged in ((stuck_sink, stuck), (broken_sink, raised)):
            self.httpd.log_sink = log_sink
            for call in (heartbeat, await_done):
                with self.subTest(sink=log_sink.__name__, route=call.__name__):
                    if call is await_done:
                        command_id = self.enqueue_done(token)
                    release.clear()
                    logged.clear()
                    try:
                        status, answer = self.answered(call)
                    finally:
                        release.set()
                    self.assertEqual(status, 200)
                    self.assertEqual(answer.get("human_input"), UNAVAILABLE)
                    if call is await_done:
                        self.assertEqual(answer["status"], "done")
                        self.assertEqual(
                            answer["result"]["state"]["pos"], [1.0, 2.0, 3.0]
                        )
                    self.assertTrue(logged.wait(30.0))
                    self._join_human_input_diagnostics()

    def test_t7b_failure_line_does_not_hold_a_worker(self) -> None:
        # Before the first request: a worker releases the semaphore it finds
        # when it ends, so a swap after a request could take that release.
        workers = CountingWorkers(3)
        self.httpd._http_workers = workers
        token, acquired = self.acquire()
        lease_id = acquired["lease_id"]
        self.add_owned_run(lease_id)

        def boom(*_args, **_kwargs):
            raise RuntimeError("inbox-6310-boom")

        self.lifecycle.human_input_block = boom  # type: ignore[method-assign]
        command_id = self.enqueue_done(token)

        def heartbeat() -> tuple[int, dict]:
            return _http(
                self.base,
                "POST",
                "/session/heartbeat",
                self.key,
                {"identity": IDENTITY_PAYLOAD, "lease_token": token},
                timeout=30.0,
            )

        def await_kept() -> tuple[int, dict]:
            return self.post_await(
                command_id, token, remove="0", timeout=30.0
            )

        def status_get() -> tuple[int, dict]:
            return _http(self.base, "GET", "/status", self.key, timeout=30.0)

        def failing(call) -> None:
            status, body = self.answered(call)
            self.assertEqual(status, 200)
            self.assertEqual(body.get("human_input"), UNAVAILABLE)
            # An answer can arrive before its worker ends; once every slot
            # is back, each handler has also started its diagnostic.
            self.assertTrue(workers.wait_idle(30.0), "an HTTP worker is busy")

        for route, call in (("heartbeat", heartbeat), ("await", await_kept)):
            with self.subTest(route=route):
                entered = 0
                threads: list[str] = []
                waits: list[bool] = []
                saw = threading.Event()
                again = threading.Event()
                release = threading.Event()

                def held_sink(message: str) -> None:
                    nonlocal entered
                    if "human_input_block failed" not in message:
                        return
                    entered += 1
                    threads.append(threading.current_thread().name)
                    saw.set()
                    if entered > 1:
                        again.set()
                    waits.append(release.wait(30.0))

                self.httpd.log_sink = held_sink
                try:
                    for _index in range(4):
                        failing(call)
                    status, _health = self.answered(status_get)
                    self.assertEqual(status, 200)
                    self.assertTrue(workers.wait_idle(30.0), "an HTTP worker is busy")
                    self.assertTrue(saw.wait(30.0))
                    # Every worker is back while the sink still holds the
                    # line, on the diagnostic thread.
                    self.assertEqual(entered, 1)
                    self.assertEqual(threads, ["mcp-human-input-diagnostic"])
                    self.assertEqual(waits, [])
                finally:
                    release.set()
                self._join_human_input_diagnostics()
                self.assertEqual(waits, [True], "the sink gave up on its own")
                failing(call)
                self.assertTrue(again.wait(30.0))
                self._join_human_input_diagnostics()
                self.assertEqual(entered, 2)
                self.assertEqual(threads, ["mcp-human-input-diagnostic"] * 2)

                written = threading.Event()

                def broken_sink(message: str) -> None:
                    if "human_input_block failed" in message:
                        written.set()
                        raise OSError("inbox-6310-sink")

                self.httpd.log_sink = broken_sink
                self.httpd.handle_error = (  # type: ignore[method-assign]
                    lambda _request, _address: None
                )
                written.clear()
                failing(call)
                self.assertTrue(written.wait(30.0))
                self._join_human_input_diagnostics()
                written.clear()
                failing(call)
                self.assertTrue(written.wait(30.0))
                self._join_human_input_diagnostics()

    def test_t7c_failed_start_frees_the_diagnostic_slot(self) -> None:
        lines: list[str] = []
        written = threading.Event()

        def sink(message: str) -> None:
            lines.append(message)
            written.set()

        self.httpd.log_sink = sink
        slot = self.httpd._human_input_diagnostic
        handler = types.SimpleNamespace(server=self.httpd)
        real_thread = threading.Thread
        refused: list[str] = []

        class FirstStartFails(real_thread):
            def start(self) -> None:
                if self.name == "mcp-human-input-diagnostic" and not refused:
                    refused.append(self.name)
                    raise RuntimeError("inbox-6310-start")
                super().start()

        with mock.patch.object(loopback.threading, "Thread", FirstStartFails):
            loopback.Handler._log_human_input_block_failed(handler)
            self.assertEqual(refused, ["mcp-human-input-diagnostic"])
            self.assertTrue(
                slot.acquire(blocking=False), "the failed start kept the slot"
            )
            slot.release()
            loopback.Handler._log_human_input_block_failed(handler)
            self.assertTrue(written.wait(30.0))
        self._join_human_input_diagnostics()
        self.assertEqual(lines, ["human_input_block failed"])


class HumanInputClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_t5_await_result_copies_block_after_prune(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from dayz_mcp.server import ClientRuntime

        runtime = object.__new__(ClientRuntime)
        runtime._time_fn = time.monotonic
        runtime.identity = ClientIdentity.from_payload(IDENTITY_PAYLOAD)
        # Commands 1, 3 and 4 were admitted under a lease; command 2 was not.
        runtime._broker_admits = {
            command_id: {"generation": "", "lease_token": "token-A"}
            for command_id in (1, 3, 4)
        }
        holder = {
            "identity": runtime.identity.to_payload(),
            "lease_token": "token-A",
        }
        block = {
            "signal": "ok",
            "runs": [
                {
                    "run_id": "run-x",
                    "count": 3,
                    "uncertain_count": 0,
                    "last_age_s": 1.0,
                }
            ],
        }
        wire = {"ok": 1, "clicked": 0, "handler": "", "user_id": 0, "y": 12.5}
        calls: list[tuple] = []

        def _call_with_block(method, path, payload=None, query=None, *_args):
            calls.append((method, path, payload, query))
            return 200, {
                "status": "done",
                "result": dict(wire),
                "human_input": block,
            }

        runtime._call = _call_with_block
        waited = await runtime._await_result("surface_query", 1, "server", 1.0)
        self.assertEqual(
            calls, [("POST", "/await", holder, {"id": "1", "remove": "1"})]
        )
        self.assertEqual(waited["y"], 12.5)
        self.assertNotIn("clicked", waited)
        self.assertIn("human_input", waited)
        self.assertEqual(waited["human_input"], block)

        def _call_plain(method, path, payload=None, query=None, *_args):
            calls.append((method, path, payload, query))
            return 200, {"status": "done", "result": dict(wire)}

        calls.clear()
        runtime._call = _call_plain
        plain = await runtime._await_result("surface_query", 2, "server", 1.0)
        self.assertEqual(calls, [("GET", "/await", None, {"id": "2", "remove": "1"})])
        self.assertNotIn("human_input", plain)
        self.assertEqual(plain["y"], 12.5)

        def _call_error(*_args, **_kwargs):
            return 200, {
                "status": "done",
                "result": {"ok": 0, "error": "boom"},
                "human_input": block,
            }

        runtime._call = _call_error
        with self.assertRaises(ToolError):
            await runtime._await_result("surface_query", 3, "server", 1.0)

        # A daemon from before this change answers POST /await with 404: the
        # client reads the same command with GET instead.
        def _call_old_daemon(method, path, payload=None, query=None, *_args):
            calls.append((method, path, payload, query))
            if method == "POST":
                return 404, {"error": "not_found"}
            return 200, {"status": "done", "result": dict(wire)}

        calls.clear()
        runtime._call = _call_old_daemon
        try:
            old = await runtime._await_result("surface_query", 4, "server", 1.0)
        except ToolError as exc:
            self.fail(f"the 404 did not fall back to GET: {exc!r}")
        self.assertEqual(
            calls,
            [
                ("POST", "/await", holder, {"id": "4", "remove": "1"}),
                ("GET", "/await", None, {"id": "4", "remove": "1"}),
            ],
        )
        self.assertEqual(old["y"], 12.5)
        self.assertNotIn("human_input", old)

    async def test_t6_tools_return_call_bridge_row(self) -> None:
        from dayz_mcp.server import ServerConfig, build_app

        app, runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        runtime.bridge_status_payload = mock.AsyncMock(
            return_value={"client_peer": {"last_poll_age_s": 0.1}}
        )
        block = {
            "signal": "ok",
            "runs": [
                {
                    "run_id": "run-x",
                    "count": 2,
                    "uncertain_count": 1,
                    "last_age_s": 0.25,
                }
            ],
        }

        block_entities = {
            "signal": "ok",
            "runs": [
                {
                    "run_id": "run-eq",
                    "count": 1,
                    "uncertain_count": 0,
                    "last_age_s": 0.1,
                }
            ],
        }
        block_players = {
            "signal": "ok",
            "runs": [
                {
                    "run_id": "run-players",
                    "count": 9,
                    "uncertain_count": 0,
                    "last_age_s": 0.2,
                }
            ],
        }
        block_notify = {
            "signal": "ok",
            "runs": [
                {
                    "run_id": "run-notify",
                    "count": 4,
                    "uncertain_count": 0,
                    "last_age_s": 0.3,
                }
            ],
        }
        blocks = {
            "query_player_state": block,
            "object_delete": block,
            "ui_focus": block,
            "entities_query": block_entities,
            "query_all_players": block_players,
            "notify_players": block_notify,
        }

        async def fake_call_bridge(cmd, args, peer, timeout_s, expected_fence=None):
            if cmd == "vehicle_telemetry":
                return {"ok": 1, "seated": False}
            row = {"ok": 1, "cmd": cmd, "human_input": blocks[cmd]}
            if cmd == "object_delete":
                row["deleted"] = 1
            if cmd == "entities_query":
                row["entities"] = []
            if cmd == "query_all_players":
                row["players"] = [{"id": 1, "pos": [0.0, 0.0, 0.0]}]
            if cmd == "notify_players":
                row["sent"] = 1
            return row

        runtime.call_bridge = fake_call_bridge
        player = _content_json(
            await app.call_tool("query_player_state", {"timeout_s": 1.0})
        )
        self.assertIn("human_input", player)
        self.assertEqual(player["human_input"], block)
        deleted = _content_json(
            await app.call_tool(
                "object_delete", {"object_id": 7, "timeout_s": 1.0}
            )
        )
        self.assertEqual(
            deleted,
            {"ok": 1, "cmd": "object_delete", "human_input": block, "deleted": 1},
        )
        focused = _content_json(
            await app.call_tool("ui_focus", {"path": "root", "timeout_s": 1.0})
        )
        self.assertEqual(focused.get("human_input"), block)
        queried = _content_json(
            await app.call_tool(
                "entities_query",
                {"pos": [0.0, 0.0, 0.0], "radius": 10.0, "timeout_s": 1.0},
            )
        )
        self.assertEqual(queried.get("human_input"), block_entities)
        notified = _content_json(
            await app.call_tool(
                "notify_players",
                {"show_time": 1.0, "title": "n", "timeout_s": 1.0},
            )
        )
        self.assertEqual(notified.get("human_input"), block_notify)
        waited = _content_json(
            await app.call_tool(
                "wait_for",
                {
                    "condition": "players_at_least",
                    "value": 0,
                    "timeout_s": 1.0,
                },
            )
        )
        self.assertNotIn("human_input", waited)


if __name__ == "__main__":
    unittest.main()
