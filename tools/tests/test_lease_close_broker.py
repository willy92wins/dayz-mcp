"""Lease-end close criteria of stages 2a and 2b.

Stage 2a (K4, K9, K11-K13, K17, K19, K21) drives SessionCoordinator as the
broker does, through the memory-only API: the coordinator rows, rounds,
grant fence and payloads. Stage 2b (K1, K2, K4-K10, K14, K15, K18, K19,
K32) drives a real loopback ServerState, which publishes and settles the
closes through cleanup_owner, its polls and its results. The daemon and
the bridges are not under test here.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, session_coordination
from dayz_mcp.session_coordination import (
    LEASE_END_BOUND_S,
    SESSION_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)
from tests.fence_helpers import (
    INST_CLIENT,
    INST_SERVER,
    PID_CLIENT,
    PID_SERVER,
    bind_both_peers,
    poll_census_query,
)
from tests.lease_helpers import MID, AuditSink, FakeClock, SequentialIds, _identity


def _floor(
    net_low: int,
    net_high: int,
    *,
    conn_gen: object,
    move_id: int,
    weapon_id: int,
) -> dict[str, object]:
    return {
        "net_low": net_low,
        "net_high": net_high,
        "conn_gen": conn_gen,
        "move_id": move_id,
        "weapon_id": weapon_id,
    }


class _DeadlineCrossingClock:
    """FakeClock until armed; then k reads return D - 0.001, later D + 0.001."""

    def __init__(self) -> None:
        self.value = 0.0
        self._armed = False
        self._k = 0
        self._seen = 0
        self._deadline = 0.0

    def __call__(self) -> float:
        if not self._armed:
            return self.value
        self._seen += 1
        if self._seen <= self._k:
            return self._deadline - 0.001
        return self._deadline + 0.001

    def advance(self, seconds: float) -> None:
        self.value += seconds

    def arm(self, k: int, deadline: float) -> None:
        self._armed = True
        self._k = k
        self._seen = 0
        self._deadline = deadline


class _TickingClock:
    """FakeClock while step is 0; after that, every read moves it on by step."""

    def __init__(self) -> None:
        self.value = 0.0
        self.step = 0.0

    def __call__(self) -> float:
        self.value += self.step
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _RecordingCondition(threading.Condition):
    """A Condition that records each wait's timeout and the thread that waited.

    With a cap set for a thread, a longer wait by that thread fails at once
    instead of waiting it out.
    """

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[tuple[str, float | None]] = []
        self.caps: dict[str, float] = {}
        self._seen = threading.Condition(threading.Lock())

    def wait(self, timeout: float | None = None) -> bool:
        name = threading.current_thread().name
        with self._seen:
            self.waits.append((name, timeout))
            self._seen.notify_all()
        cap = self.caps.get(name)
        if cap is not None and (timeout is None or timeout > cap):
            raise AssertionError(f"{name} waited {timeout} s, more than {cap} s")
        return super().wait(timeout)

    def waits_of(self, name: str) -> list[float | None]:
        with self._seen:
            return [timeout for who, timeout in self.waits if who == name]

    def await_waits(self, name: str, count: int, timeout: float) -> bool:
        with self._seen:
            return self._seen.wait_for(
                lambda: sum(1 for who, _ in self.waits if who == name) >= count,
                timeout,
            )


class LeaseCloseBrokerStage2aTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.ids = SequentialIds("id")
        self.tokens = SequentialIds("token")
        self.audit = AuditSink()
        self.cleanup_fn = lambda *_args: {}
        self.a = _identity("a")
        self.b = _identity("b")
        self.c = _identity("c")
        self.coordinator = self._make_coordinator()

    def _make_coordinator(self, **overrides: object) -> SessionCoordinator:
        kwargs: dict[str, object] = {
            "time_fn": self.clock,
            "token_fn": self.tokens,
            "id_fn": self.ids,
            "audit": self.audit,
            "cleanup": lambda *args: self.cleanup_fn(*args),
        }
        kwargs.update(overrides)
        return SessionCoordinator(**kwargs)

    def _join(self, thread: threading.Thread, timeout: float = 30.0) -> None:
        thread.join(timeout=timeout)
        self.assertFalse(thread.is_alive(), f"{thread.name} did not finish")

    def _wait_handoff(self) -> None:
        with self.coordinator._condition:
            self.assertTrue(
                self.coordinator._condition.wait_for(
                    lambda: not self.coordinator._handoff_pending,
                    timeout=30.0,
                ),
                "handoff_pending did not clear",
            )

    def _bind(self, run_id: str = "run-1") -> None:
        self.coordinator.note_peer_bound(run_id, "server", f"{run_id}-server")
        self.coordinator.note_peer_bound(run_id, "client", f"{run_id}-client")

    def _commit_work(
        self,
        client: ClientIdentity,
        command: str = "vehicle_trace",
        command_id: int = 1,
    ) -> dict[str, object]:
        status, payload = self.coordinator.acquire(client, "trace")
        self.assertEqual(status, 200, "acquire did not grant")
        decision = self.coordinator.authorize(
            client, payload["lease_token"], command
        )
        self.assertTrue(decision.allowed, "authorize refused")
        self.assertTrue(
            self.coordinator.commit_authorization(
                client.session_id,
                decision.lease_id,
                decision.reservation_id,
                command_id,
                command,
                {"mode": "start"},
            )
        )
        claimed = self.coordinator.claim_dispatch(
            client.session_id, decision.lease_id, command_id, command
        )
        self.assertTrue(claimed, "claim_dispatch did not mark work")
        payload["lease_id"] = decision.lease_id
        return payload

    def _publishing_hook(
        self,
        *,
        action: str = "published",
        reason: str = "not_bound",
        carried: list[dict[str, object]] | None = None,
        close_prefix: str = "close",
    ):
        seq = SequentialIds(close_prefix)

        def cleanup(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            if info is None:
                return {}
            reports = []
            for row in info["rows"]:
                close_id = seq()
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": action,
                        "close_id": close_id,
                        "reason": reason,
                        "carried_floors": list(carried or []),
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        return cleanup

    def _close_outcome(
        self,
        round_id: int,
        run_id: str,
        peer: str,
        close_id: str,
        kind: str,
        *,
        reasons: list[str] | None = None,
        error: str = "",
        reason: str = "",
        floor: dict[str, object] | None = None,
        reported_floors: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "round": round_id,
            "run_id": run_id,
            "peer": peer,
            "close_id": close_id,
            "kind": kind,
            "reasons": list(reasons or []),
            "error": error,
            "reason": reason,
            "floor": floor,
            "reported_floors": list(reported_floors or []),
        }

    def _closing_ids(self, session_id: str, lease_id: str) -> dict[tuple[str, str], str]:
        info = self.coordinator.lease_end_round(session_id, lease_id)
        self.assertIsNotNone(info, "expected an open round")
        assert info is not None
        rows = info["rows"]
        self.assertGreaterEqual(len(rows), 1, "round had no pending rows")
        reports = []
        ids: dict[tuple[str, str], str] = {}
        seq = SequentialIds("close")
        for row in rows:
            close_id = seq()
            ids[(row["run_id"], row["peer"])] = close_id
            reports.append(
                {
                    "run_id": row["run_id"],
                    "peer": row["peer"],
                    "action": "published",
                    "close_id": close_id,
                    "reason": "",
                    "carried_floors": [],
                }
            )
        accepted = self.coordinator.note_close_round(info["round"], reports)
        self.assertEqual(len(accepted), len(rows), "not every pending row was accepted")
        return ids

    def _published_round(
        self,
        carried_server: list[dict[str, object]],
        *,
        peers: tuple[str, ...] = ("server", "client"),
    ) -> tuple[int, dict[str, str]]:
        """Bind run-1's peers, end a lease that delivered work and publish
        every row of its round; the server close carries carried_server."""
        for peer in peers:
            self.coordinator.note_peer_bound("run-1", peer, f"run-1-{peer}")
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"pub-{row['peer']}"
                captured[row["peer"]] = close_id
                carried = carried_server if row["peer"] == "server" else []
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": [dict(item) for item in carried],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        round_id = int(captured.pop("round"))
        return round_id, {peer: str(close) for peer, close in captured.items()}

    def _owed(self) -> list[tuple[str, list[str]]]:
        body = self.coordinator.status(self.a).get("lease_end", {"unconfirmed": []})
        return [(row["peer"], row["reasons"]) for row in body["unconfirmed"]]

    def _cleanup_thread(self, lease_id: str) -> threading.Thread:
        name = f"dayz-mcp-cleanup-{lease_id[:12]}"
        matches = [thread for thread in threading.enumerate() if thread.name == name]
        self.assertEqual(len(matches), 1, f"expected one thread named {name}")
        return matches[0]

    def test_k4_no_work_no_debt_no_round(self) -> None:
        self._bind()
        status, lease = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200)
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        self.assertIsNone(
            self.coordinator.lease_end_round(
                self.a.session_id, lease["lease_id"]
            )
        )
        status_b, grant = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.assertNotIn("lease_end", grant)

    def test_k4_owed_debt_opens_round_and_blocks(self) -> None:
        self._bind()
        self.coordinator.seed_restart_debt([("run-1", None)])
        status, lease = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200)
        self.cleanup_fn = self._publishing_hook()
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        info = self.coordinator.lease_end_round(
            self.a.session_id, lease["lease_id"]
        )
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(len(info["rows"]), 0, "published rows stay closing")
        status_b, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 202)
        self.assertEqual(queued["status"], "queued")

    def test_k4_delivered_work_opens_round(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        self.cleanup_fn = self._publishing_hook()
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        info = self.coordinator.lease_end_round(
            self.a.session_id, lease["lease_id"]
        )
        self.assertIsNotNone(info, "delivered work must open a round")
        status_b, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 202)

    def test_k4_finish_exact_before_release_keeps_delivered_work(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        self.coordinator.finish_operation_exact(
            self.a.session_id,
            str(lease["lease_id"]),
            1,
            command_succeeded=True,
        )
        self.cleanup_fn = self._publishing_hook()
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        info = self.coordinator.lease_end_round(
            self.a.session_id, str(lease["lease_id"])
        )
        self.assertIsNotNone(info, "claimed work still opens a round after finish")
        status_b, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 202)

        self.coordinator = self._make_coordinator()
        self._bind()
        status, granted = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200)
        decision = self.coordinator.authorize(
            self.a, granted["lease_token"], "vehicle_trace"
        )
        self.assertTrue(decision.allowed, "authorize refused")
        self.assertTrue(
            self.coordinator.commit_authorization(
                self.a.session_id,
                decision.lease_id,
                decision.reservation_id,
                1,
                "vehicle_trace",
                {"mode": "start"},
            )
        )
        self.coordinator.finish_operation_exact(
            self.a.session_id,
            decision.lease_id,
            1,
            command_succeeded=True,
        )
        self.cleanup_fn = self._publishing_hook()
        self.coordinator.release(self.a, granted["lease_token"])
        self._wait_handoff()
        self.assertIsNone(
            self.coordinator.lease_end_round(
                self.a.session_id, decision.lease_id
            )
        )
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.assertNotIn("lease_end", grant_b)

    def test_k9_hook_publish_rows_closing(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        self.cleanup_fn = self._publishing_hook()
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        info = self.coordinator.lease_end_round(
            self.a.session_id, lease["lease_id"]
        )
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(len(info["rows"]), 0, "published rows are no longer pending")
        body = self.coordinator.status(self.b)
        self.assertIn("lease_end", body)
        self.assertEqual(body["lease_end"]["outstanding"], 2)

    def test_k9_saturation_marks_owed_at_once(self) -> None:
        blocked = threading.Event()
        self.addCleanup(blocked.set)
        started = threading.Event()

        def blocking(*_args: object) -> dict:
            started.set()
            blocked.wait(30.0)
            return {}

        self.coordinator = self._make_coordinator(
            cleanup=blocking, cleanup_timeout_s=0.05
        )
        for index in range(4):
            status, lease = self.coordinator.acquire(self.a, f"fill-{index}")
            self.assertEqual(status, 200)
            released = self.coordinator.release(self.a, lease["lease_token"])[1]
            self.assertIn("cleanup_timeout", released["cleanup_degraded"])
        self._bind()
        work = self._commit_work(self.b)
        released = self.coordinator.release(self.b, work["lease_token"])[1]
        self.assertIn("cleanup_worker_saturated", released["cleanup_degraded"])
        body = self.coordinator.status(self.b)
        unconfirmed = body["lease_end"]["unconfirmed"]
        self.assertEqual(len(unconfirmed), 2, "both peers must be owed")
        for row in unconfirmed:
            self.assertEqual(row["reasons"], ["cleanup_worker_saturated"])

    def test_k9_hook_raises_cleanup_failed(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)

        def boom(*_args: object) -> dict:
            raise RuntimeError("cleanup exploded")

        self.cleanup_fn = boom
        released = self.coordinator.release(self.a, lease["lease_token"])[1]
        self.assertIn("cleanup_failed", released["cleanup_degraded"])
        unconfirmed = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertEqual(len(unconfirmed), 2, "both pending rows become owed")
        for row in unconfirmed:
            self.assertEqual(row["reasons"], ["cleanup_failed"])

    def test_k9_unreported_close_not_reported(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        self.cleanup_fn = lambda *_args: {"cleanup_degraded": ["cleanup_fenced"]}
        self.coordinator.release(self.a, lease["lease_token"])
        unconfirmed = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertEqual(len(unconfirmed), 2, "unreported pending rows become owed")
        for row in unconfirmed:
            self.assertEqual(row["reasons"], ["close_not_reported"])

    def test_k9_timeout_report_before_deadline_closing(self) -> None:
        go = threading.Event()
        self.addCleanup(go.set)
        reported = threading.Event()
        self.addCleanup(reported.set)

        def late_report(session_id: str, lease_id: str, *_rest: object) -> dict:
            go.wait(30.0)
            info = self.coordinator.lease_end_round(session_id, lease_id)
            if info is not None:
                reports = [
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": f"late-{row['peer']}",
                        "reason": "",
                        "carried_floors": [],
                    }
                    for row in info["rows"]
                ]
                self.coordinator.note_close_round(info["round"], reports)
            reported.set()
            return {}

        self.coordinator = self._make_coordinator(cleanup_timeout_s=0.03)
        self.cleanup_fn = late_report
        self._bind()
        lease = self._commit_work(self.a)
        released = self.coordinator.release(self.a, lease["lease_token"])[1]
        self.assertIn("cleanup_timeout", released["cleanup_degraded"])
        thread = self._cleanup_thread(str(lease["lease_id"]))
        go.set()
        self.assertTrue(reported.wait(30.0), "late report did not run")
        self._join(thread)
        info = self.coordinator.lease_end_round(
            self.a.session_id, lease["lease_id"]
        )
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(len(info["rows"]), 0, "late report before deadline is closing")
        self.assertEqual(
            self.coordinator.status(self.a)["lease_end"]["outstanding"], 2
        )

    def test_k9_report_after_deadline_rejected(self) -> None:
        release_hook = threading.Event()
        self.addCleanup(release_hook.set)
        self.coordinator = self._make_coordinator(
            lease_end_bound_s=0.05, cleanup_timeout_s=0.03
        )
        self.cleanup_fn = lambda *_args: (release_hook.wait(30.0), {})[1]
        self._bind()
        lease = self._commit_work(self.a)
        self.coordinator.release(self.a, lease["lease_token"])
        self.clock.advance(0.06)
        self.coordinator.status(self.a)
        info = self.coordinator.lease_end_round(
            self.a.session_id, lease["lease_id"]
        )
        self.assertIsNotNone(info)
        assert info is not None
        reports = [
            {
                "run_id": "run-1",
                "peer": "server",
                "action": "published",
                "close_id": "too-late",
                "reason": "",
                "carried_floors": [],
            }
        ]
        accepted = self.coordinator.note_close_round(info["round"], reports)
        self.assertEqual(len(accepted), 0, "late report after deadline is not accepted")
        unconfirmed = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertGreaterEqual(len(unconfirmed), 1, "owed rows remain")
        self.assertTrue(
            any("close_timeout" in row["reasons"] for row in unconfirmed)
        )

    def test_k9_late_hook_return_owes_pending(self) -> None:
        failed_reasons = ["cleanup_failed"]
        for name, boom in (("return", False), ("raise", True)):
            with self.subTest(name):
                go = threading.Event()
                self.addCleanup(go.set)
                self.clock = FakeClock()
                self.ids = SequentialIds("id")
                self.tokens = SequentialIds("token")
                self.coordinator = self._make_coordinator(cleanup_timeout_s=0.03)

                def hook(*_args: object) -> dict:
                    go.wait(30.0)
                    if boom:
                        raise RuntimeError("late cleanup")
                    return {}

                self.cleanup_fn = hook
                self._bind()
                lease = self._commit_work(self.a)
                released = self.coordinator.release(
                    self.a, lease["lease_token"]
                )[1]
                self.assertIn("cleanup_timeout", released["cleanup_degraded"])
                thread = self._cleanup_thread(str(lease["lease_id"]))
                info = self.coordinator.lease_end_round(
                    self.a.session_id, str(lease["lease_id"])
                )
                self.assertIsNotNone(info)
                assert info is not None
                self.assertEqual(len(info["rows"]), 2, "rows stay pending")
                self.assertEqual(
                    self.coordinator.status(self.a)["lease_end"]["outstanding"],
                    2,
                )
                go.set()
                self._join(thread)
                self._wait_handoff()
                owed = self._owed()
                self.assertEqual(len(owed), 2, "both rows become owed")
                reason = failed_reasons if boom else ["close_not_reported"]
                for _peer, reasons in owed:
                    self.assertEqual(reasons, reason)
                status_b, grant_b = self.coordinator.acquire(self.b, "trace")
                self.assertEqual(status_b, 200)
                self.assertIn(
                    "lease_end_unconfirmed", grant_b.get("cleanup_degraded", [])
                )

    def test_k9_late_hook_raise_after_deferred_server(self) -> None:
        go = threading.Event()
        reported = threading.Event()
        self.addCleanup(go.set)
        self.coordinator = self._make_coordinator(cleanup_timeout_s=0.03)

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            reports = []
            for row in info["rows"]:
                action = "deferred" if row["peer"] == "server" else "published"
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": action,
                        "close_id": f"def-{row['peer']}",
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            reported.set()
            go.wait(30.0)
            raise RuntimeError("late cleanup")

        self.cleanup_fn = hook
        self._bind()
        lease = self._commit_work(self.a)
        released = self.coordinator.release(self.a, lease["lease_token"])[1]
        self.assertIn("cleanup_timeout", released["cleanup_degraded"])
        self.assertTrue(reported.wait(30.0), "hook did not report")
        thread = self._cleanup_thread(str(lease["lease_id"]))
        go.set()
        self._join(thread)
        self._wait_handoff()
        owed = self._owed()
        self.assertEqual(len(owed), 1, "only the deferred server is owed")
        self.assertEqual(owed[0], ("server", ["cleanup_failed"]))
        body = self.coordinator.status(self.a)["lease_end"]
        self.assertEqual(body["outstanding"], 1, "client row stays closing")

    def test_k9_late_hook_does_not_touch_other_round(self) -> None:
        go1 = threading.Event()
        go2 = threading.Event()
        reported_a = threading.Event()
        started_b = threading.Event()
        self.addCleanup(go1.set)
        self.addCleanup(go2.set)
        self.coordinator = self._make_coordinator(cleanup_timeout_s=0.03)

        def hook_a(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            reports = []
            for row in info["rows"]:
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": f"r1-{row['peer']}",
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            reported_a.set()
            go1.wait(30.0)
            raise RuntimeError("late round 1")

        def hook_b(*_args: object) -> dict:
            started_b.set()
            go2.wait(30.0)
            return {}

        self.cleanup_fn = hook_a
        self._bind()
        lease_a = self._commit_work(self.a)
        released_a = self.coordinator.release(
            self.a, lease_a["lease_token"]
        )[1]
        self.assertIn("cleanup_timeout", released_a["cleanup_degraded"])
        self.assertTrue(reported_a.wait(30.0), "round 1 did not publish")
        thread_a = self._cleanup_thread(str(lease_a["lease_id"]))
        info_a = self.coordinator.lease_end_round(
            self.a.session_id, str(lease_a["lease_id"])
        )
        self.assertIsNotNone(info_a)
        assert info_a is not None
        round_a = int(info_a["round"])
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_a, "run-1", "client", "r1-client", "clean"
                ),
                self._close_outcome(
                    round_a, "run-1", "server", "r1-server", "clean"
                ),
            ]
        )
        self.cleanup_fn = hook_b
        self._wait_handoff()
        lease_b = self._commit_work(self.b)
        released_b = self.coordinator.release(
            self.b, lease_b["lease_token"]
        )[1]
        self.assertIn("cleanup_timeout", released_b["cleanup_degraded"])
        self.assertTrue(started_b.wait(30.0), "round 2 hook did not start")
        thread_b = self._cleanup_thread(str(lease_b["lease_id"]))
        info_b = self.coordinator.lease_end_round(
            self.b.session_id, str(lease_b["lease_id"])
        )
        self.assertIsNotNone(info_b)
        assert info_b is not None
        self.assertEqual(len(info_b["rows"]), 2, "round 2 still pending")
        self.assertEqual(int(info_b["round"]), 2)
        go1.set()
        self._join(thread_a)
        after_go1 = self.coordinator.lease_end_round(
            self.b.session_id, str(lease_b["lease_id"])
        )
        self.assertIsNotNone(after_go1)
        assert after_go1 is not None
        self.assertEqual(
            len(after_go1["rows"]), 2, "late round 1 must not owe round 2"
        )
        go2.set()
        self._join(thread_b)
        self._wait_handoff()
        owed = self._owed()
        self.assertEqual(len(owed), 2, "round 2 becomes owed after its hook")
        for _peer, reasons in owed:
            self.assertEqual(reasons, ["close_not_reported"])

    def test_k9_deferred_then_published(self) -> None:
        reported = threading.Event()

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            reports = []
            for row in info["rows"]:
                action = "deferred" if row["peer"] == "server" else "published"
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": action,
                        "close_id": f"pub-{row['peer']}",
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            reported.set()
            return {}

        self.cleanup_fn = hook
        self._bind()
        lease = self._commit_work(self.a)
        self.coordinator.release(self.a, lease["lease_token"])
        self.assertTrue(reported.wait(30.0), "hook did not report")
        self._wait_handoff()
        info = self.coordinator.lease_end_round(
            self.a.session_id, str(lease["lease_id"])
        )
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(
            len(info["rows"]), 0, "reported deferred server is not listed"
        )
        self.assertEqual(
            self.coordinator.status(self.a)["lease_end"]["outstanding"], 2
        )
        accepted = self.coordinator.note_close_published(
            int(info["round"]), "run-1", "server", "later-server", []
        )
        self.assertTrue(accepted)
        after = self.coordinator.lease_end_round(
            self.a.session_id, str(lease["lease_id"])
        )
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(len(after["rows"]), 0, "published server is no longer listed")
        self.assertEqual(
            self.coordinator.status(self.a)["lease_end"]["outstanding"], 2
        )
        round_id = int(info["round"])
        cleaned = self.coordinator.note_close_outcomes(
            [self._close_outcome(round_id, "run-1", "client", "pub-client", "clean")]
        )
        self.assertEqual(cleaned, ["applied"])
        stale = self.coordinator.note_close_outcomes(
            [self._close_outcome(round_id, "run-1", "server", "pub-server", "clean")]
        )
        self.assertEqual(stale, ["ignored"])
        self.assertEqual(
            self.coordinator.status(self.a)["lease_end"]["outstanding"], 1
        )
        fresh = self.coordinator.note_close_outcomes(
            [self._close_outcome(round_id, "run-1", "server", "later-server", "clean")]
        )
        self.assertEqual(fresh, ["applied"])
        self.assertNotIn("lease_end", self.coordinator.status(self.a))
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.assertIn(
            {"run_id": "run-1", "peer": "server", "close_id": "later-server"},
            grant_b["lease_end"]["settled"],
        )

        resume = threading.Event()
        self.addCleanup(resume.set)
        entered = threading.Event()
        self.coordinator = self._make_coordinator(cleanup_timeout_s=0.03)

        def silent(session_id: str, lease_id: str, *_rest: object) -> dict:
            entered.set()
            resume.wait(30.0)
            return {}

        self.cleanup_fn = silent
        self._bind()
        lease_n = self._commit_work(self.a)
        released = self.coordinator.release(
            self.a, lease_n["lease_token"]
        )[1]
        self.assertIn("cleanup_timeout", released["cleanup_degraded"])
        self.assertTrue(entered.wait(30.0), "silent hook did not start")
        thread = self._cleanup_thread(str(lease_n["lease_id"]))
        info_n = self.coordinator.lease_end_round(
            self.a.session_id, str(lease_n["lease_id"])
        )
        self.assertIsNotNone(info_n)
        assert info_n is not None
        refused = self.coordinator.note_close_published(
            int(info_n["round"]), "run-1", "server", "too-soon", []
        )
        self.assertFalse(refused)
        still = self.coordinator.lease_end_round(
            self.a.session_id, str(lease_n["lease_id"])
        )
        self.assertIsNotNone(still)
        assert still is not None
        self.assertEqual(len(still["rows"]), 2, "unreported row stays pending")
        resume.set()
        self._join(thread)

    def test_k9_deferred_publication_requires_its_carried_floor(self) -> None:
        # The client's clean comes while the server row is deferred, so the
        # coordinator keeps no floor from it; the server's publication
        # carries that floor. A server clean below it leaves the row owed,
        # and one that reaches it clears the row.
        client_floor = _floor(10, 20, conn_gen="g", move_id=7, weapon_id=4)
        for server_move, clears in ((5, False), (7, True)):
            with self.subTest(server_move_id=server_move):
                self.clock = FakeClock()
                self.ids = SequentialIds("id")
                self.tokens = SequentialIds("token")
                self.coordinator = self._make_coordinator()
                reported = threading.Event()

                def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
                    info = self.coordinator.lease_end_round(session_id, lease_id)
                    assert info is not None
                    reports = []
                    for row in info["rows"]:
                        server = row["peer"] == "server"
                        reports.append(
                            {
                                "run_id": row["run_id"],
                                "peer": row["peer"],
                                "action": "deferred" if server else "published",
                                "close_id": f"pub-{row['peer']}",
                                "reason": "",
                                "carried_floors": [],
                            }
                        )
                    self.coordinator.note_close_round(info["round"], reports)
                    reported.set()
                    return {}

                self.cleanup_fn = hook
                self._bind()
                lease = self._commit_work(self.a)
                self.coordinator.release(self.a, lease["lease_token"])
                self.assertTrue(reported.wait(30.0), "hook did not report")
                self._wait_handoff()
                info = self.coordinator.lease_end_round(
                    self.a.session_id, str(lease["lease_id"])
                )
                self.assertIsNotNone(info)
                assert info is not None
                round_id = int(info["round"])
                client = self.coordinator.note_close_outcomes(
                    [
                        self._close_outcome(
                            round_id,
                            "run-1",
                            "client",
                            "pub-client",
                            "clean",
                            floor=dict(client_floor),
                        )
                    ]
                )
                self.assertEqual(client, ["applied"])
                self.assertTrue(
                    self.coordinator.note_close_published(
                        round_id, "run-1", "server", "later-server", [client_floor]
                    )
                )
                server = self.coordinator.note_close_outcomes(
                    [
                        self._close_outcome(
                            round_id,
                            "run-1",
                            "server",
                            "later-server",
                            "clean",
                            reported_floors=[
                                _floor(
                                    10,
                                    20,
                                    conn_gen="g",
                                    move_id=server_move,
                                    weapon_id=4,
                                )
                            ],
                        )
                    ]
                )
                self.assertEqual(server, ["applied"])
                if clears:
                    self.assertNotIn("lease_end", self.coordinator.status(self.a))
                else:
                    self.assertEqual(
                        self._owed(), [("server", ["player_move_floor_unapplied"])]
                    )

    def test_k11_wait_wakes_at_bound(self) -> None:
        # The hook never reports, so the rows stay pending. On the fake
        # clock B's acquire queues whatever the scheduling, and its wait
        # first waits no longer than the rows' term. Once the clock passes
        # the deadline, that wait ends on its own timeout, with no notify,
        # and the next pass grants with the rows owed.
        blocked = threading.Event()
        self.addCleanup(blocked.set)

        def hang(*_args: object) -> dict:
            blocked.wait(30.0)
            return {}

        bound = 0.5
        self.cleanup_fn = hang
        self.coordinator = self._make_coordinator(
            cleanup_timeout_s=0.05, lease_end_bound_s=bound
        )
        recorder = _RecordingCondition()
        self.coordinator._condition = recorder
        self._bind()
        lease = self._commit_work(self.a)
        self.coordinator.release(self.a, lease["lease_token"])
        status_b, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 202)
        result: list[tuple[int, dict]] = []

        def waiter() -> None:
            result.append(self.coordinator.wait(self.b, queued["ticket"], 30.0))

        thread = threading.Thread(target=waiter, name="k11-wait")
        thread.start()
        self.assertTrue(
            recorder.await_waits("k11-wait", 1, 30.0), "the wait did not wait"
        )
        self.assertLessEqual(recorder.waits_of("k11-wait")[0], bound + 1e-6)
        self.clock.advance(bound + 0.001)
        self._join(thread, timeout=40.0)
        for timeout in recorder.waits_of("k11-wait"):
            self.assertLessEqual(timeout, bound + 1e-6)
        self.assertEqual(len(result), 1, "wait returned once")
        status, payload = result[0]
        self.assertEqual(status, 200)
        self.assertIn("lease_end_unconfirmed", payload.get("cleanup_degraded", []))

    def test_k11_deadline_crosses_inside_call(self) -> None:
        granted = 0
        for k in range(1, 41):
            with self.subTest(acquire_k=k):
                clock = _DeadlineCrossingClock()
                self.clock = clock
                self.ids = SequentialIds("id")
                self.tokens = SequentialIds("token")
                self.coordinator = self._make_coordinator(time_fn=clock)
                self._bind()
                lease = self._commit_work(self.a)
                self.cleanup_fn = self._publishing_hook()
                self.coordinator.release(self.a, lease["lease_token"])
                matches = [
                    thread
                    for thread in threading.enumerate()
                    if thread.name
                    == f"dayz-mcp-cleanup-{str(lease['lease_id'])[:12]}"
                ]
                if matches:
                    self.assertEqual(
                        len(matches), 1, "expected one cleanup thread"
                    )
                    self._join(matches[0])
                self._wait_handoff()
                deadline = clock.value + LEASE_END_BOUND_S
                clock.arm(k, deadline)
                status_b, grant_b = self.coordinator.acquire(self.b, "trace")
                if status_b == 200:
                    granted += 1
                    self.assertIn(
                        "lease_end_unconfirmed",
                        grant_b.get("cleanup_degraded", []),
                    )
                    unconfirmed = grant_b["lease_end"]["unconfirmed"]
                    self.assertEqual(
                        len(unconfirmed), 2, "both rows become close_timeout"
                    )
                    for row in unconfirmed:
                        self.assertEqual(row["reasons"], ["close_timeout"])
                else:
                    self.assertEqual(status_b, 202)
        self.assertGreaterEqual(granted, 1, "at least one k granted 200")

        for k in range(1, 41):
            with self.subTest(wait_k=k):
                clock = _DeadlineCrossingClock()
                self.clock = clock
                self.ids = SequentialIds("id")
                self.tokens = SequentialIds("token")
                self.coordinator = self._make_coordinator(time_fn=clock)
                recorder = _RecordingCondition()
                self.coordinator._condition = recorder
                self._bind()
                lease = self._commit_work(self.a)
                self.cleanup_fn = self._publishing_hook()
                self.coordinator.release(self.a, lease["lease_token"])
                matches = [
                    thread
                    for thread in threading.enumerate()
                    if thread.name
                    == f"dayz-mcp-cleanup-{str(lease['lease_id'])[:12]}"
                ]
                if matches:
                    self.assertEqual(
                        len(matches), 1, "expected one cleanup thread"
                    )
                    self._join(matches[0])
                self._wait_handoff()
                status_b, queued = self.coordinator.acquire(self.b, "trace")
                self.assertEqual(status_b, 202)
                deadline = clock.value + LEASE_END_BOUND_S
                clock.arm(k, deadline)
                # The call's own limit is 30 s; a wait longer than 0.01 s,
                # such as one without the rows' term, fails at once.
                me = threading.current_thread().name
                recorder.caps[me] = 0.01
                try:
                    wait_status, wait_payload = self.coordinator.wait(
                        self.b, queued["ticket"], 30.0
                    )
                finally:
                    recorder.caps.pop(me, None)
                self.assertEqual(wait_status, 200)
                self.assertIn(
                    "lease_end_unconfirmed",
                    wait_payload.get("cleanup_degraded", []),
                )

    def test_k11_status_owes_rows_past_their_deadline(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        self.cleanup_fn = self._publishing_hook()
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        # With a recovery fault armed, status does not reach the grant fence.
        self.coordinator.arm_lifecycle_recovery_fault(
            {"fault_id": "fault-k11", "state": "armed"}
        )
        self.assertEqual(
            self.coordinator.status(self.a)["lease_end"]["outstanding"], 2
        )
        self.assertEqual(self._owed(), [])
        self.clock.advance(LEASE_END_BOUND_S)
        self.assertEqual(
            self._owed(),
            [("client", ["close_timeout"]), ("server", ["close_timeout"])],
        )
        self.assertEqual(
            self.coordinator.status(self.a)["lease_end"]["outstanding"], 0
        )

    def test_k11_status_photo_holds_when_the_deadline_crosses(self) -> None:
        # Status expires the rows and builds its lease_end fields and
        # claimable from one clock reading, so a deadline that crosses inside
        # it never shows a row that is neither outstanding nor owed, nor a
        # slot kept from being claimable by rows already owed, whether or not
        # a recovery fault keeps status off the grant fence.
        seen: set[tuple[bool, int]] = set()
        for fault in (True, False):
            for k in range(1, 41):
                with self.subTest(fault=fault, status_k=k):
                    clock = _DeadlineCrossingClock()
                    self.clock = clock
                    self.ids = SequentialIds("id")
                    self.tokens = SequentialIds("token")
                    self.coordinator = self._make_coordinator(time_fn=clock)
                    self._bind()
                    lease = self._commit_work(self.a)
                    self.cleanup_fn = self._publishing_hook()
                    self.coordinator.release(self.a, lease["lease_token"])
                    matches = [
                        thread
                        for thread in threading.enumerate()
                        if thread.name
                        == f"dayz-mcp-cleanup-{str(lease['lease_id'])[:12]}"
                    ]
                    if matches:
                        self.assertEqual(
                            len(matches), 1, "expected one cleanup thread"
                        )
                        self._join(matches[0])
                    self._wait_handoff()
                    if fault:
                        self.coordinator.arm_lifecycle_recovery_fault(
                            {"fault_id": f"fault-k11-{k}", "state": "armed"}
                        )
                    clock.arm(k, clock.value + LEASE_END_BOUND_S)
                    status = self.coordinator.status(self.a)
                    body = status["lease_end"]
                    outstanding = body["outstanding"]
                    unconfirmed = body["unconfirmed"]
                    self.assertEqual(outstanding + len(unconfirmed), 2, body)
                    self.assertEqual(
                        body["deadline_in_s"] is None, outstanding == 0, body
                    )
                    self.assertEqual(
                        status["claimable"], not fault and outstanding == 0, status
                    )
                    if outstanding:
                        self.assertGreater(body["deadline_in_s"], 0.0, body)
                    for row in unconfirmed:
                        self.assertEqual(row["reasons"], ["close_timeout"])
                    seen.add((fault, outstanding))
        for fault in (True, False):
            self.assertIn((fault, 2), seen, "no k read the clock before D")
            self.assertIn((fault, 0), seen, "no k read the clock after D")

    def test_k11_status_photo_ages_a_prior_debt_when_the_deadline_crosses(self) -> None:
        # The server row is owed since t=0 and the client row is CLOSING, due
        # at D, so the server row's age dates the photo's reading. While the
        # client row is outstanding, its term plus that age is the bound and
        # the slot is not claimable; once it is owed, the age is the bound at
        # least and the slot is claimable. A claimable read from a later
        # reading than the photo's shows the client row owed with the age
        # short of the bound.
        seen: set[int] = set()
        for k in range(0, 41):
            with self.subTest(status_k=k):
                clock = _DeadlineCrossingClock()
                self.clock = clock
                self.ids = SequentialIds("id")
                self.tokens = SequentialIds("token")
                self.coordinator = self._make_coordinator(time_fn=clock)
                carried = _floor(10, 20, conn_gen="g", move_id=7, weapon_id=4)
                round_id, ids = self._published_round([carried])
                lease_id = str(self.coordinator._lease_end_round.lease_id)
                for thread in threading.enumerate():
                    if thread.name == f"dayz-mcp-cleanup-{lease_id[:12]}":
                        self._join(thread)
                low = self.coordinator.note_close_outcomes(
                    [
                        self._close_outcome(
                            round_id,
                            "run-1",
                            "server",
                            ids["server"],
                            "clean",
                            reported_floors=[
                                _floor(10, 20, conn_gen="g", move_id=5, weapon_id=4)
                            ],
                        )
                    ]
                )
                self.assertEqual(low, ["applied"])
                clock.arm(k, clock.value + LEASE_END_BOUND_S)
                status = self.coordinator.status(self.a)
                body = status["lease_end"]
                rows = {row["peer"]: row for row in body["unconfirmed"]}
                age = rows["server"]["since_s"]
                if body["outstanding"]:
                    self.assertEqual(body["outstanding"], 1, body)
                    self.assertEqual(sorted(rows), ["server"], body)
                    self.assertFalse(status["claimable"], status)
                    self.assertAlmostEqual(
                        body["deadline_in_s"] + age, LEASE_END_BOUND_S, places=9, msg=body
                    )
                else:
                    self.assertEqual(sorted(rows), ["client", "server"], body)
                    self.assertIsNone(body["deadline_in_s"], body)
                    self.assertTrue(status["claimable"], status)
                    self.assertGreaterEqual(age, LEASE_END_BOUND_S, body)
                seen.add(body["outstanding"])
        self.assertEqual(seen, {0, 1}, "the deadline did not cross on both sides of the reading")

    def test_k11_preferential_reacquire_wakes_at_the_rows_deadline(self) -> None:
        # A's lease expires and its cleanup runs long. A's preferential
        # reacquire waits no longer than the rows' term, and a wakeup after
        # the deadline turns the rows OWED instead of waiting zero.
        go = threading.Event()
        self.addCleanup(go.set)
        published = threading.Event()
        publish = self._publishing_hook()

        def hook(session_id: str, lease_id: str, *rest: object) -> dict:
            result = publish(session_id, lease_id, *rest)
            published.set()
            go.wait(30.0)
            return result

        self.coordinator = self._make_coordinator(
            attached_run_probe=lambda *_args: True, lease_end_bound_s=5.0
        )
        recorder = _RecordingCondition()
        self.coordinator._condition = recorder
        self.cleanup_fn = hook
        self._bind()
        self._commit_work(self.a)
        self.clock.advance(MID)
        expiry = threading.Thread(
            target=self.coordinator.status, args=(self.b,), name="k11-expiry"
        )
        expiry.start()
        self.assertTrue(published.wait(30.0), "the expiry cleanup did not publish")
        result: list[tuple[int, dict]] = []

        def reacquire() -> None:
            result.append(self.coordinator.acquire(self.a, "trace"))

        preferred = threading.Thread(target=reacquire, name="k11-preferred")
        preferred.start()
        self.assertTrue(recorder.await_waits("k11-preferred", 1, 30.0))
        self.assertLessEqual(recorder.waits_of("k11-preferred")[0], 5.0 + 1e-6)
        self.clock.advance(5.0 + 0.001)
        with recorder:
            recorder.notify_all()
        self.assertTrue(
            recorder.await_waits("k11-preferred", 2, 30.0),
            "the preferential acquire did not wait again",
        )
        go.set()
        self._join(expiry, timeout=40.0)
        self._join(preferred, timeout=40.0)
        status, payload = result[0]
        self.assertEqual(status, 200, payload)
        self.assertIn("lease_end_unconfirmed", payload.get("cleanup_degraded", []))
        for timeout in recorder.waits_of("k11-preferred"):
            self.assertGreater(timeout, 0.0)

    def _preferential_crossing(self, *, out_of_time: bool) -> list[bool]:
        # After A's cleanup and the handoff, only closing rows keep A's
        # preferential reacquire waiting, and their deadline passes right
        # after the loop's fence saw them outstanding. The loop's own limit
        # reads the coordinator's time.monotonic, which here is a clock
        # apart from the rows' that only the test moves: it keeps still, so
        # no pause of the driver or of the reacquire runs it out, and with
        # out_of_time it runs out at that crossing. Returns what each
        # expiry by the loop's body returned after the crossing.
        go = threading.Event()
        self.addCleanup(go.set)
        published = threading.Event()
        publish = self._publishing_hook()

        def hook(session_id: str, lease_id: str, *rest: object) -> dict:
            result = publish(session_id, lease_id, *rest)
            published.set()
            go.wait(30.0)
            return result

        self.coordinator = self._make_coordinator(
            attached_run_probe=lambda *_args: True, lease_end_bound_s=5.0
        )
        recorder = _RecordingCondition()
        self.coordinator._condition = recorder
        budget = FakeClock()
        held = patch.object(
            session_coordination, "time", SimpleNamespace(monotonic=budget)
        )
        held.start()
        self.addCleanup(held.stop)
        # A reacquire still in the loop when the test ends runs out of time
        # at its next look.
        self.addCleanup(budget.advance, 3600.0)
        self.cleanup_fn = hook
        self._bind()
        self._commit_work(self.a)
        self.clock.advance(MID)
        deadline = self.clock.value + 5.0
        crossed = threading.Event()
        waits_at_cross: list[int] = []
        fence = self.coordinator._lease_end_blocks_locked

        def fence_then_cross(now: float | None = None) -> bool:
            blocks = fence() if now is None else fence(now)
            if (
                blocks
                and threading.current_thread().name == "k11-preferred"
                and not crossed.is_set()
            ):
                self.clock.value = deadline + 0.001
                if out_of_time:
                    budget.advance(3600.0)
                # Cleanup and handoff are over, so these rows were all the
                # loop had left to wait for: any later wait fails at once.
                waits_at_cross.append(len(recorder.waits_of("k11-preferred")))
                recorder.caps["k11-preferred"] = -1.0
                crossed.set()
            return blocks

        self.coordinator._lease_end_blocks_locked = fence_then_cross
        expire = self.coordinator._expire_rows_locked
        body_expiries: list[bool] = []

        def expire_and_note(now: float | None = None) -> bool:
            expired = expire(now)
            # The loop's body is the one place acquire itself calls this;
            # the fence, _expire_due and the round's API call it from their
            # own frames.
            if (
                crossed.is_set()
                and threading.current_thread().name == "k11-preferred"
                and sys._getframe(1).f_code.co_name == "acquire"
            ):
                body_expiries.append(expired)
            return expired

        self.coordinator._expire_rows_locked = expire_and_note
        expiry = threading.Thread(
            target=self.coordinator.status, args=(self.b,), name="k11-expiry"
        )
        expiry.start()
        self.assertTrue(published.wait(30.0), "the expiry cleanup did not publish")
        result: list[tuple[int, dict]] = []

        def reacquire() -> None:
            result.append(self.coordinator.acquire(self.a, "trace"))

        preferred = threading.Thread(target=reacquire, name="k11-preferred")
        preferred.start()
        self.assertTrue(recorder.await_waits("k11-preferred", 1, 30.0))
        go.set()
        self._join(expiry, timeout=40.0)
        self._join(preferred, timeout=40.0)
        self.assertTrue(crossed.is_set(), "the fence never saw the rows outstanding")
        self.assertEqual(
            recorder.waits_of("k11-preferred")[waits_at_cross[0]:],
            [],
            "the preferential reacquire waited after its rows expired",
        )
        status, payload = result[0]
        self.assertEqual(status, 200, payload)
        self.assertIn("lease_end_unconfirmed", payload.get("cleanup_degraded", []))
        for timeout in recorder.waits_of("k11-preferred"):
            self.assertLessEqual(timeout, 5.0 + 1e-6)
        return body_expiries

    def test_k11_preferential_reacquire_decides_again_when_the_rows_expire(
        self,
    ) -> None:
        # With its own limit left, the loop's body expires the rows and the
        # loop decides again, instead of waiting out that limit.
        self.assertEqual(
            self._preferential_crossing(out_of_time=False),
            [True],
            "the loop's body did not expire the rows after the crossing",
        )

    def test_k11_preferential_reacquire_out_of_time_at_the_crossing(self) -> None:
        # The loop's own limit runs out at the crossing: the loop leaves
        # before its body, and the admission's fence expires the rows.
        self.assertEqual(
            self._preferential_crossing(out_of_time=True),
            [],
            "the loop's body ran after its own limit ran out",
        )

    def test_k11_queued_wait_expires_rows_during_a_long_cleanup(self) -> None:
        # While A's cleanup still runs, B's wait does not reach the grant
        # fence: _expire_due turns the rows past their deadline OWED, so
        # the next wait is not zero.
        go = threading.Event()
        self.addCleanup(go.set)
        published = threading.Event()
        publish = self._publishing_hook()

        def hook(session_id: str, lease_id: str, *rest: object) -> dict:
            result = publish(session_id, lease_id, *rest)
            published.set()
            go.wait(30.0)
            return result

        self.coordinator = self._make_coordinator(lease_end_bound_s=5.0)
        recorder = _RecordingCondition()
        self.coordinator._condition = recorder
        self.cleanup_fn = hook
        self._bind()
        lease = self._commit_work(self.a)
        releaser = threading.Thread(
            target=self.coordinator.release,
            args=(self.a, lease["lease_token"]),
            name="k11-release",
        )
        releaser.start()
        self.assertTrue(published.wait(30.0), "the release cleanup did not publish")
        status_b, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 202)
        result: list[tuple[int, dict]] = []

        def waiter() -> None:
            result.append(self.coordinator.wait(self.b, queued["ticket"], 30.0))

        thread = threading.Thread(target=waiter, name="k11-queued")
        thread.start()
        self.assertTrue(recorder.await_waits("k11-queued", 1, 30.0))
        self.assertLessEqual(recorder.waits_of("k11-queued")[0], 5.0 + 1e-6)
        self.clock.advance(5.0 + 0.001)
        with recorder:
            recorder.notify_all()
        self.assertTrue(
            recorder.await_waits("k11-queued", 2, 30.0),
            "the queued wait did not wait again",
        )
        go.set()
        self._join(releaser, timeout=40.0)
        self._join(thread, timeout=40.0)
        status, payload = result[0]
        self.assertEqual(status, 200, payload)
        self.assertIn("lease_end_unconfirmed", payload.get("cleanup_degraded", []))
        for timeout in recorder.waits_of("k11-queued"):
            self.assertGreater(timeout, 0.0)

    def test_k12_residue_then_clean_debt_round(self) -> None:
        self._bind()
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            if info is None:
                return {}
            captured["round"] = info["round"]
            captured["ids"] = {}
            reports = []
            for row in info["rows"]:
                close_id = f"{lease_id[:6]}-{row['peer']}"
                captured["ids"][(row["run_id"], row["peer"])] = close_id
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        lease_a = self._commit_work(self.a)
        self.coordinator.release(self.a, lease_a["lease_token"])
        self._wait_handoff()
        round_a = int(captured["round"])
        ids_a = dict(captured["ids"])
        self.assertEqual(len(ids_a), 2, "A published both peers")
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_a, run_id, peer, close_id, "residue",
                    reasons=["vehicle_drive"],
                )
                for (run_id, peer), close_id in ids_a.items()
            ]
        )
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.assertIn("lease_end_unconfirmed", grant_b["cleanup_degraded"])
        reasons = [
            item
            for row in grant_b["lease_end"]["unconfirmed"]
            for item in row["reasons"]
        ]
        self.assertIn("vehicle_drive", reasons)
        self.coordinator.release(self.b, grant_b["lease_token"])
        self._wait_handoff()
        self.assertIsNotNone(
            self.coordinator.lease_end_round(
                self.b.session_id, grant_b["lease_id"]
            ),
            "owed debt must open B's round",
        )
        round_b = int(captured["round"])
        ids_b = dict(captured["ids"])
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(round_b, run_id, peer, close_id, "clean")
                for (run_id, peer), close_id in ids_b.items()
            ]
        )
        status_c, grant_c = self.coordinator.acquire(self.c, "trace")
        self.assertEqual(status_c, 200)
        self.assertIn("lease_end", grant_c)
        self.assertEqual(len(grant_c["lease_end"]["unconfirmed"]), 0, "C must be clean")
        self.assertGreaterEqual(len(grant_c["lease_end"]["settled"]), 1)
        self.assertNotIn("cleanup_degraded", grant_c)

    def test_k12_residue_again_degrades_c(self) -> None:
        self._bind()
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            if info is None:
                return {}
            captured["round"] = info["round"]
            captured["ids"] = {}
            reports = []
            for row in info["rows"]:
                close_id = f"{lease_id[:6]}-{row['peer']}"
                captured["ids"][(row["run_id"], row["peer"])] = close_id
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        lease_a = self._commit_work(self.a)
        self.coordinator.release(self.a, lease_a["lease_token"])
        self._wait_handoff()
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    int(captured["round"]), run_id, peer, close_id, "residue",
                    reasons=["stuck"],
                )
                for (run_id, peer), close_id in dict(captured["ids"]).items()
            ]
        )
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.coordinator.release(self.b, grant_b["lease_token"])
        self._wait_handoff()
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    int(captured["round"]), run_id, peer, close_id, "residue",
                    reasons=["stuck-again"],
                )
                for (run_id, peer), close_id in dict(captured["ids"]).items()
            ]
        )
        status_c, grant_c = self.coordinator.acquire(self.c, "trace")
        self.assertEqual(status_c, 200)
        self.assertIn("lease_end_unconfirmed", grant_c.get("cleanup_degraded", []))

    def test_k13_restart_debt_then_gone(self) -> None:
        self.coordinator.seed_restart_debt([("run-1", None)])
        status, grant = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200)
        self.assertIn("lease_end_unconfirmed", grant["cleanup_degraded"])
        reasons = [
            item
            for row in grant["lease_end"]["unconfirmed"]
            for item in row["reasons"]
        ]
        self.assertIn("daemon_restart", reasons)
        self.coordinator.note_processes_gone("run-1", None)
        self.coordinator.release(self.a, grant["lease_token"])
        self._wait_handoff()
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.assertNotIn("lease_end", grant_b)

    def test_k13_unbound_not_bound_keeps_debt(self) -> None:
        self.coordinator.seed_restart_debt([("run-1", None)])
        lease = self._commit_work(self.a)
        self.cleanup_fn = self._publishing_hook(
            action="unreachable", reason="not_bound"
        )
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        unconfirmed = self.coordinator.status(self.b)["lease_end"]["unconfirmed"]
        self.assertGreaterEqual(len(unconfirmed), 1, "unbound close stays owed")
        self.assertTrue(
            any("not_bound" in row["reasons"] for row in unconfirmed)
        )
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.assertIn("lease_end_unconfirmed", grant_b["cleanup_degraded"])

    def test_k17_matching_floors_clear(self) -> None:
        self._bind()
        floor = _floor(10, 20, conn_gen="g", move_id=7, weapon_id=4)
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"k17-{row['peer']}"
                captured[row["peer"]] = close_id
                carried = [dict(floor)] if row["peer"] == "server" else []
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": carried,
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        round_id = int(captured["round"])
        results = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id, "run-1", "client", str(captured["client"]), "clean",
                    floor=floor,
                ),
                self._close_outcome(
                    round_id, "run-1", "server", str(captured["server"]), "clean",
                    reported_floors=[
                        _floor(10, 20, conn_gen="g", move_id=7, weapon_id=4)
                    ],
                ),
            ]
        )
        self.assertEqual(results, ["applied", "applied"])
        body = self.coordinator.status(self.b)
        self.assertNotIn("lease_end", body)
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        settled = grant_b["lease_end"]["settled"]
        self.assertEqual(len(settled), 2, "both closes settle")
        named = {(item["peer"], item["close_id"]) for item in settled}
        self.assertIn(("client", captured["client"]), named)
        self.assertIn(("server", captured["server"]), named)

    def test_k17_late_clean_still_closing(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"late-{row['peer']}"
                captured[row["peer"]] = close_id
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        round_id = int(captured["round"])
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id, "run-1", "client", str(captured["client"]), "clean"
                )
            ]
        )
        self.assertEqual(
            self.coordinator.status(self.a)["lease_end"]["outstanding"], 1
        )
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id, "run-1", "server", str(captured["server"]), "clean"
                )
            ]
        )
        self.assertNotIn("lease_end", self.coordinator.status(self.a))

    def test_k17_late_clean_after_timeout_clears(self) -> None:
        self.coordinator = self._make_coordinator(lease_end_bound_s=1.0)
        self._bind()
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"to-{row['peer']}"
                captured[row["peer"]] = close_id
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        self.clock.advance(1.1)
        self.coordinator.status(self.a)
        unconfirmed = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertTrue(
            any("close_timeout" in row["reasons"] for row in unconfirmed)
        )
        results = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    int(captured["round"]),
                    "run-1",
                    "client",
                    str(captured["client"]),
                    "clean",
                ),
                self._close_outcome(
                    int(captured["round"]),
                    "run-1",
                    "server",
                    str(captured["server"]),
                    "clean",
                ),
            ]
        )
        self.assertEqual(results, ["applied", "applied"])
        self.assertNotIn("lease_end", self.coordinator.status(self.a))

    def test_k17_floor_negatives(self) -> None:
        self._bind()
        client_floor = _floor(10, 20, conn_gen="g", move_id=7, weapon_id=4)
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"neg-{row['peer']}"
                captured[row["peer"]] = close_id
                carried = (
                    [_floor(10, 20, conn_gen="g", move_id=5, weapon_id=4)]
                    if row["peer"] == "server"
                    else []
                )
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": carried,
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        round_id = int(captured["round"])
        server_close = str(captured["server"])
        client_close = str(captured["client"])
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    server_close,
                    "clean",
                    reported_floors=[
                        _floor(10, 20, conn_gen="g", move_id=5, weapon_id=4)
                    ],
                )
            ]
        )
        status_mid, grant_mid = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_mid, 202)
        # client F=7 after server settled at 5
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "client",
                    client_close,
                    "clean",
                    floor=client_floor,
                )
            ]
        )
        unconfirmed = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertEqual(len(unconfirmed), 1, "server row returns owed")
        self.assertEqual(unconfirmed[0]["peer"], "server")
        self.assertIn("player_move_floor_unapplied", unconfirmed[0]["reasons"])
        ignored = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(99, "run-1", "server", server_close, "clean"),
                self._close_outcome(
                    round_id, "run-1", "server", "other-close", "clean"
                ),
            ]
        )
        self.assertEqual(ignored, ["ignored", "ignored"])
        still = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertEqual(len(still), 1, "ignored outcomes must not change the row")
        # The row the client's floor brought back needs that floor: a clean
        # of the same close at move_id 5 leaves it, one at 7 clears it.
        low = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    server_close,
                    "clean",
                    reported_floors=[
                        _floor(10, 20, conn_gen="g", move_id=5, weapon_id=4)
                    ],
                )
            ]
        )
        self.assertEqual(low, ["applied"])
        self.assertEqual(
            self._owed(), [("server", ["player_move_floor_unapplied"])]
        )
        high = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    server_close,
                    "clean",
                    reported_floors=[dict(client_floor)],
                )
            ]
        )
        self.assertEqual(high, ["applied"])
        self.assertNotIn("lease_end", self.coordinator.status(self.a))

    def test_k17_closing_client_floor_blocks_low_late_clean(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"cl-{row['peer']}"
                captured[row["peer"]] = close_id
                carried = (
                    [_floor(10, 20, conn_gen="g", move_id=0, weapon_id=0)]
                    if row["peer"] == "server"
                    else []
                )
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": carried,
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        round_id = int(captured["round"])
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "client",
                    str(captured["client"]),
                    "clean",
                    floor=_floor(10, 20, conn_gen="g", move_id=7, weapon_id=0),
                )
            ]
        )
        unconfirmed = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertEqual(len(unconfirmed), 1, "closing server becomes owed")
        self.assertIn("player_move_floor_unapplied", unconfirmed[0]["reasons"])
        low = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    str(captured["server"]),
                    "clean",
                    reported_floors=[
                        _floor(10, 20, conn_gen="g", move_id=5, weapon_id=0)
                    ],
                )
            ]
        )
        self.assertEqual(low, ["applied"])
        still = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertEqual(len(still), 1, "move_id 5 must not clear required 7")
        high = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    str(captured["server"]),
                    "clean",
                    reported_floors=[
                        _floor(10, 20, conn_gen="g", move_id=7, weapon_id=0)
                    ],
                )
            ]
        )
        self.assertEqual(high, ["applied"])
        self.assertNotIn("lease_end", self.coordinator.status(self.a))

    def test_k17_conn_gen_and_missing_player(self) -> None:
        carried = _floor(10, 20, conn_gen="g", move_id=7, weapon_id=4)
        no_gen = _floor(10, 20, conn_gen=None, move_id=7, weapon_id=4)
        conn_gen = ["player_move_conn_gen_unapplied", "weapon_conn_gen_unapplied"]
        floors = ["player_move_floor_unapplied", "weapon_floor_unapplied"]
        cases = (
            (
                "reported_other_gen",
                carried,
                [_floor(10, 20, conn_gen="other", move_id=7, weapon_id=4)],
                conn_gen,
            ),
            ("carried_no_gen", no_gen, [dict(no_gen)], conn_gen),
            (
                "player_missing",
                carried,
                [_floor(30, 40, conn_gen="g", move_id=7, weapon_id=4)],
                floors,
            ),
        )
        for name, carried_floor, reported, expected in cases:
            with self.subTest(name):
                # Each case starts from its own CLOSING server row.
                self.coordinator = self._make_coordinator()
                round_id, ids = self._published_round([carried_floor])
                results = self.coordinator.note_close_outcomes(
                    [
                        self._close_outcome(
                            round_id,
                            "run-1",
                            "server",
                            ids["server"],
                            "clean",
                            reported_floors=reported,
                        )
                    ]
                )
                self.assertEqual(results, ["applied"])
                self.assertEqual(self._owed(), [("server", expected)])
        # Decision 11: a clean of that same close that fails the floors
        # again leaves the OWED row as it was; one that passes clears it.
        again = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    ids["server"],
                    "clean",
                    reported_floors=[
                        _floor(10, 20, conn_gen="other", move_id=7, weapon_id=4)
                    ],
                )
            ]
        )
        self.assertEqual(again, ["applied"])
        self.assertEqual(self._owed(), [("server", floors)])
        passing = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    ids["server"],
                    "clean",
                    reported_floors=[dict(carried)],
                )
            ]
        )
        self.assertEqual(passing, ["applied"])
        self.assertEqual(self._owed(), [])

    def test_k17_client_floor_without_server_row(self) -> None:
        # A run whose round has no server row: the client's floor brings
        # none back (decision 11 checks only a server row cleared here).
        round_id, ids = self._published_round([], peers=("client",))
        results = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "client",
                    ids["client"],
                    "clean",
                    floor=_floor(10, 20, conn_gen="g", move_id=7, weapon_id=4),
                )
            ]
        )
        self.assertEqual(results, ["applied"])
        self.assertNotIn("lease_end", self.coordinator.status(self.a))

    def test_k17_gone_server_floor_is_not_checked(self) -> None:
        reported = _floor(10, 20, conn_gen="g", move_id=5, weapon_id=4)
        round_id, ids = self._published_round([reported])
        cleared = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    ids["server"],
                    "clean",
                    reported_floors=[dict(reported)],
                )
            ]
        )
        self.assertEqual(cleared, ["applied"])
        self.coordinator.note_processes_gone("run-1", "server")
        results = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "client",
                    ids["client"],
                    "clean",
                    floor=_floor(10, 20, conn_gen="g", move_id=7, weapon_id=4),
                )
            ]
        )
        self.assertEqual(results, ["applied"])
        self.assertNotIn("lease_end", self.coordinator.status(self.a))

    def test_k17_late_owed_server_clean_keeps_its_floors(self) -> None:
        # A late clean of an owed server row stores the floors it reported:
        # a client floor at them brings no row back, one above them does.
        floor = _floor(10, 20, conn_gen="g", move_id=7, weapon_id=4)
        cases = (
            ("at_floor", dict(floor), []),
            (
                "above_floor",
                _floor(10, 20, conn_gen="g", move_id=8, weapon_id=4),
                [("server", ["player_move_floor_unapplied"])],
            ),
        )
        for name, client_floor, expected in cases:
            with self.subTest(name):
                self.coordinator = self._make_coordinator()
                round_id, ids = self._published_round([floor])
                self.clock.advance(41.0)
                self.assertEqual(
                    self._owed(),
                    [("client", ["close_timeout"]), ("server", ["close_timeout"])],
                )
                server = self.coordinator.note_close_outcomes(
                    [
                        self._close_outcome(
                            round_id,
                            "run-1",
                            "server",
                            ids["server"],
                            "clean",
                            reported_floors=[dict(floor)],
                        )
                    ]
                )
                self.assertEqual(server, ["applied"])
                self.assertEqual(self._owed(), [("client", ["close_timeout"])])
                client = self.coordinator.note_close_outcomes(
                    [
                        self._close_outcome(
                            round_id,
                            "run-1",
                            "client",
                            ids["client"],
                            "clean",
                            floor=client_floor,
                        )
                    ]
                )
                self.assertEqual(client, ["applied"])
                if expected:
                    self.assertEqual(self._owed(), expected)
                else:
                    self.assertNotIn("lease_end", self.coordinator.status(self.a))

    def test_k17_weapon_floor_unapplied(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"w-{row['peer']}"
                captured[row["peer"]] = close_id
                carried = (
                    [_floor(10, 20, conn_gen="g", move_id=7, weapon_id=1)]
                    if row["peer"] == "server"
                    else []
                )
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": carried,
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        round_id = int(captured["round"])
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    str(captured["server"]),
                    "clean",
                    reported_floors=[
                        _floor(10, 20, conn_gen="g", move_id=7, weapon_id=1)
                    ],
                ),
                self._close_outcome(
                    round_id,
                    "run-1",
                    "client",
                    str(captured["client"]),
                    "clean",
                    floor=_floor(10, 20, conn_gen="g", move_id=7, weapon_id=4),
                ),
            ]
        )
        unconfirmed = self.coordinator.status(self.a)["lease_end"]["unconfirmed"]
        self.assertEqual(len(unconfirmed), 1, "server row returns owed on W")
        self.assertIn("weapon_floor_unapplied", unconfirmed[0]["reasons"])

    def test_k17_issued_grant_not_rewritten(self) -> None:
        self._bind()
        self.coordinator.seed_restart_debt([("run-1", None)])
        status, grant = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200)
        before = grant["lease_end"]
        self.coordinator.note_processes_gone("run-1", None)
        status2, again = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status2, 200)
        self.assertEqual(again["lease_end"], before)

    def test_k17_client_other_conn_gen_while_server_closing(self) -> None:
        conn_reasons = [
            "player_move_conn_gen_unapplied",
            "weapon_conn_gen_unapplied",
        ]
        carried = _floor(10, 20, conn_gen=1, move_id=7, weapon_id=4)
        client_other = _floor(10, 20, conn_gen=2, move_id=7, weapon_id=4)
        orders = (
            ("client_then_server", ("client", "server")),
            ("server_then_client", ("server", "client")),
        )
        for name, order in orders:
            with self.subTest(name):
                self.clock = FakeClock()
                self.ids = SequentialIds("id")
                self.tokens = SequentialIds("token")
                self.coordinator = self._make_coordinator()
                round_id, ids = self._published_round([carried])
                outcomes = []
                for peer in order:
                    if peer == "client":
                        outcomes.append(
                            self._close_outcome(
                                round_id,
                                "run-1",
                                "client",
                                ids["client"],
                                "clean",
                                floor=client_other,
                            )
                        )
                    else:
                        outcomes.append(
                            self._close_outcome(
                                round_id,
                                "run-1",
                                "server",
                                ids["server"],
                                "clean",
                                reported_floors=[dict(carried)],
                            )
                        )
                results = self.coordinator.note_close_outcomes(outcomes)
                self.assertEqual(len(results), 2, "both outcomes applied")
                self.assertEqual(results, ["applied", "applied"])
                owed = self._owed()
                self.assertEqual(len(owed), 1, "server stays owed")
                self.assertEqual(owed[0], ("server", conn_reasons))
                status_b, grant_b = self.coordinator.acquire(self.b, "trace")
                self.assertEqual(status_b, 200)
                self.assertIn(
                    "lease_end_unconfirmed", grant_b.get("cleanup_degraded", [])
                )
                named = [
                    row["peer"]
                    for row in grant_b["lease_end"]["unconfirmed"]
                ]
                self.assertEqual(named, ["server"])

        self.clock = FakeClock()
        self.ids = SequentialIds("id")
        self.tokens = SequentialIds("token")
        self.coordinator = self._make_coordinator()
        round_id, ids = self._published_round([carried])
        matching = _floor(10, 20, conn_gen=1, move_id=7, weapon_id=4)
        results = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "client",
                    ids["client"],
                    "clean",
                    floor=matching,
                ),
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    ids["server"],
                    "clean",
                    reported_floors=[dict(carried)],
                ),
            ]
        )
        self.assertEqual(results, ["applied", "applied"])
        self.assertEqual(self._owed(), [])
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.assertNotIn(
            "lease_end_unconfirmed", grant_b.get("cleanup_degraded", [])
        )

    def test_k17_client_floor_without_carried_while_server_closing(self) -> None:
        # The server close carried no floor: the client's floor, ids 0
        # included, becomes the server row's requirement, so the server's
        # clean must reach it, generation included, in either order.
        conn_reasons = [
            "player_move_conn_gen_unapplied",
            "weapon_conn_gen_unapplied",
        ]
        floor_reasons = ["player_move_floor_unapplied", "weapon_floor_unapplied"]
        cases = (
            (
                "other_gen",
                _floor(10, 20, conn_gen=2, move_id=0, weapon_id=0),
                [_floor(10, 20, conn_gen=1, move_id=0, weapon_id=0)],
                conn_reasons,
            ),
            (
                "player_missing",
                _floor(10, 20, conn_gen=1, move_id=0, weapon_id=0),
                [_floor(30, 40, conn_gen=1, move_id=0, weapon_id=0)],
                floor_reasons,
            ),
            (
                "same_gen",
                _floor(10, 20, conn_gen=1, move_id=0, weapon_id=0),
                [_floor(10, 20, conn_gen=1, move_id=0, weapon_id=0)],
                None,
            ),
        )
        for name, client_floor, reported, expected in cases:
            for order in (("client", "server"), ("server", "client")):
                with self.subTest(name, order="_then_".join(order)):
                    self.clock = FakeClock()
                    self.ids = SequentialIds("id")
                    self.tokens = SequentialIds("token")
                    self.coordinator = self._make_coordinator()
                    round_id, ids = self._published_round([])
                    outcomes = {
                        "client": self._close_outcome(
                            round_id,
                            "run-1",
                            "client",
                            ids["client"],
                            "clean",
                            floor=client_floor,
                        ),
                        "server": self._close_outcome(
                            round_id,
                            "run-1",
                            "server",
                            ids["server"],
                            "clean",
                            reported_floors=reported,
                        ),
                    }
                    results = self.coordinator.note_close_outcomes(
                        [outcomes[peer] for peer in order]
                    )
                    self.assertEqual(results, ["applied", "applied"])
                    status_b, grant_b = self.coordinator.acquire(self.b, "trace")
                    self.assertEqual(status_b, 200)
                    degraded = grant_b.get("cleanup_degraded", [])
                    if expected is None:
                        self.assertEqual(self._owed(), [])
                        self.assertNotIn("lease_end_unconfirmed", degraded)
                    else:
                        self.assertEqual(self._owed(), [("server", expected)])
                        self.assertIn("lease_end_unconfirmed", degraded)
                        named = [
                            row["peer"]
                            for row in grant_b["lease_end"]["unconfirmed"]
                        ]
                        self.assertEqual(named, ["server"])

    def test_k19_status_and_acquire_during_blocked_hook(self) -> None:
        entered = threading.Event()
        resume = threading.Event()
        self.addCleanup(resume.set)

        def hang(*_args: object) -> dict:
            entered.set()
            resume.wait(30.0)
            return {}

        self.coordinator = self._make_coordinator(cleanup=hang, cleanup_timeout_s=30.0)
        self._bind()
        lease = self._commit_work(self.a)
        releaser_done: list[object] = []

        def releaser() -> None:
            releaser_done.append(
                self.coordinator.release(self.a, lease["lease_token"])
            )

        release_thread = threading.Thread(target=releaser, name="k19-release")
        release_thread.start()
        self.assertTrue(entered.wait(30.0), "hook did not start")
        started = time.monotonic()
        status_body = self.coordinator.status(self.b)
        acquire_status, _queued = self.coordinator.acquire(self.b, "trace")
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 10.0, f"status/acquire blocked for {elapsed:.3f}s")
        self.assertIn("lease_end", status_body)
        self.assertIn(acquire_status, (200, 202))
        resume.set()
        self._join(release_thread)

    def test_k19_stale_round_outcome_ignored(self) -> None:
        self._bind()
        self.cleanup_fn = self._publishing_hook(close_prefix="r1")
        lease_a = self._commit_work(self.a)
        self.coordinator.release(self.a, lease_a["lease_token"])
        self._wait_handoff()
        info1 = self.coordinator.lease_end_round(
            self.a.session_id, lease_a["lease_id"]
        )
        self.assertIsNotNone(info1)
        assert info1 is not None
        round1 = info1["round"]
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(round1, "run-1", "client", "r1-1", "residue", reasons=["old"]),
                self._close_outcome(round1, "run-1", "server", "r1-2", "residue", reasons=["old"]),
                self._close_outcome(round1, "run-1", "client", "r1-2", "residue", reasons=["old"]),
                self._close_outcome(round1, "run-1", "server", "r1-1", "residue", reasons=["old"]),
            ]
        )
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.cleanup_fn = self._publishing_hook(close_prefix="r2")
        self.coordinator.release(self.b, grant_b["lease_token"])
        self._wait_handoff()
        info2 = self.coordinator.lease_end_round(
            self.b.session_id, grant_b["lease_id"]
        )
        self.assertIsNotNone(info2)
        assert info2 is not None
        before = self.coordinator.status(self.c).get("lease_end")
        ignored = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(round1, "run-1", "client", "r1-1", "clean"),
            ]
        )
        self.assertEqual(ignored, ["ignored"])
        after = self.coordinator.status(self.c).get("lease_end")
        self.assertEqual(after, before)

    def test_k21_settled_names_close_ids_and_status_keeps_degraded_empty(self) -> None:
        self._bind()
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"set-{row['peer']}"
                captured[row["peer"]] = close_id
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        lease = self._commit_work(self.a)
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        during = self.coordinator.status(self.b)
        self.assertIn("lease_end", during)
        self.assertEqual(during["cleanup_degraded"], [])
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    int(captured["round"]),
                    "run-1",
                    "client",
                    str(captured["client"]),
                    "clean",
                ),
                self._close_outcome(
                    int(captured["round"]),
                    "run-1",
                    "server",
                    str(captured["server"]),
                    "clean",
                ),
            ]
        )
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        settled_ids = {item["close_id"] for item in grant_b["lease_end"]["settled"]}
        self.assertEqual(settled_ids, {captured["client"], captured["server"]})

    def test_k21_status_ages_owed_rows_from_its_reading(self) -> None:
        # The server row is owed since t=0 and the client row is still
        # closing, due at the bound. Status reads the clock once for its
        # photo, so since_s and deadline_in_s add up to the bound even
        # when every read moves the clock on.
        clock = _TickingClock()
        self.clock = clock
        self.coordinator = self._make_coordinator(time_fn=clock)
        carried = _floor(10, 20, conn_gen="g", move_id=7, weapon_id=4)
        round_id, ids = self._published_round([carried])
        low = self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    round_id,
                    "run-1",
                    "server",
                    ids["server"],
                    "clean",
                    reported_floors=[
                        _floor(10, 20, conn_gen="g", move_id=5, weapon_id=4)
                    ],
                )
            ]
        )
        self.assertEqual(low, ["applied"])
        clock.step = 0.25
        body = self.coordinator.status(self.a)["lease_end"]
        self.assertEqual(body["outstanding"], 1, body)
        self.assertEqual(len(body["unconfirmed"]), 1, body)
        row = body["unconfirmed"][0]
        self.assertEqual(row["peer"], "server", body)
        self.assertGreater(row["since_s"], 0.0, body)
        self.assertEqual(
            body["deadline_in_s"] + row["since_s"], LEASE_END_BOUND_S, body
        )

    def test_k21_merge_unconfirmed_with_cleanup_failed_on_acquire(self) -> None:
        def boom(*_args: object) -> dict:
            raise RuntimeError("hook fail")

        self.cleanup_fn = boom
        self._bind()
        lease = self._commit_work(self.a)
        self.clock.advance(120.0)
        status, grant = self.coordinator.acquire(self.b, "trace")
        if status == 202:
            self._wait_handoff()
            status, grant = self.coordinator.wait(self.b, grant["ticket"], 30.0)
        self.assertEqual(status, 200)
        degraded = grant.get("cleanup_degraded", [])
        self.assertGreaterEqual(len(degraded), 2, "both warnings must be present")
        self.assertEqual(degraded[0], "lease_end_unconfirmed")
        self.assertIn("cleanup_failed", degraded)
        self.assertEqual(self.coordinator.status(self.b)["cleanup_degraded"], [])

    def test_k21_merge_unconfirmed_with_cleanup_failed_on_wait(self) -> None:
        def boom(*_args: object) -> dict:
            raise RuntimeError("hook fail")

        self.cleanup_fn = boom
        self._bind()
        lease = self._commit_work(self.a)
        self.clock.advance(10.0)
        status_b, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 202)
        self.clock.advance(110.5)
        status, grant = self.coordinator.wait(self.b, queued["ticket"], 30.0)
        self.assertEqual(status, 200)
        degraded = grant.get("cleanup_degraded", [])
        self.assertGreaterEqual(len(degraded), 2, "wait merge needs both warnings")
        self.assertEqual(degraded[0], "lease_end_unconfirmed")
        self.assertIn("cleanup_failed", degraded)
        self.assertEqual(self.coordinator.status(self.b)["cleanup_degraded"], [])

    def test_inert_empty_registry_delivered_work_no_round(self) -> None:
        lease = self._commit_work(self.a)
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        self.assertIsNone(
            self.coordinator.lease_end_round(
                self.a.session_id, lease["lease_id"]
            )
        )
        status_b, grant = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        self.assertNotIn("lease_end", grant)
        self.assertNotIn("lease_end", self.coordinator.status(self.b))

    def test_mw1_pending_blocks_until_settled(self) -> None:
        self._bind()
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"mw1-{row['peer']}"
                captured[row["peer"]] = close_id
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        lease = self._commit_work(self.a)
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        status_b, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 202)
        result: list[tuple[int, dict]] = []

        def waiter() -> None:
            result.append(self.coordinator.wait(self.b, queued["ticket"], 30.0))

        thread = threading.Thread(target=waiter, name="mw1-wait")
        thread.start()
        self.coordinator.note_close_outcomes(
            [
                self._close_outcome(
                    int(captured["round"]),
                    "run-1",
                    "client",
                    str(captured["client"]),
                    "clean",
                ),
                self._close_outcome(
                    int(captured["round"]),
                    "run-1",
                    "server",
                    str(captured["server"]),
                    "clean",
                ),
            ]
        )
        self._join(thread)
        self.assertEqual(len(result), 1, "wait returned")
        self.assertEqual(result[0][0], 200)

    def test_mw4_reacquire_merges_existing_unconfirmed(self) -> None:
        self.audit.fail_events.add("ticket_cancelled")
        self.coordinator.seed_restart_debt([("run-1", None)])
        status, grant = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200)
        self.assertIn("lease_end_unconfirmed", grant["cleanup_degraded"])
        queued_status, _queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(queued_status, 202)
        self.clock.advance(1.0)
        self.coordinator.heartbeat(self.a, grant["lease_token"])
        self.clock.advance(119.0)
        status2, again = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status2, 200)
        degraded = again.get("cleanup_degraded", [])
        self.assertIn("lease_end_unconfirmed", degraded)
        self.assertEqual(degraded[0], "lease_end_unconfirmed")
        self.assertIn("audit_failed", degraded)

    def test_k21_operation_id_retry_merges_degraded(self) -> None:
        for name in ("acquire", "enqueue"):
            with self.subTest(name):
                self.clock = FakeClock()
                self.ids = SequentialIds("id")
                self.tokens = SequentialIds("token")
                self.audit = AuditSink()
                self.audit.fail_events.add("ticket_cancelled")
                self.coordinator = self._make_coordinator()
                self.coordinator.seed_restart_debt([("run-1", None)])
                status, grant = self.coordinator.acquire(
                    self.a, "trace", operation_id="op-a"
                )
                self.assertEqual(status, 200)
                self.assertIn(
                    "lease_end_unconfirmed", grant["cleanup_degraded"]
                )
                queued_status, _queued = self.coordinator.acquire(
                    self.b, "trace"
                )
                self.assertEqual(queued_status, 202)
                self.clock.advance(1.0)
                self.coordinator.heartbeat(self.a, grant["lease_token"])
                self.clock.advance(119.0)
                if name == "acquire":
                    status2, again = self.coordinator.acquire(
                        self.a, "trace", operation_id="op-a"
                    )
                else:
                    status2, again = self.coordinator.enqueue(
                        self.a, "trace", "op-a"
                    )
                self.assertEqual(status2, 200)
                degraded = again.get("cleanup_degraded", [])
                self.assertIn("lease_end_unconfirmed", degraded)
                self.assertEqual(degraded[0], "lease_end_unconfirmed")
                self.assertIn("audit_failed", degraded)

    def test_mw9_grant_photo_stable_after_late_clean(self) -> None:
        self._bind()
        self.coordinator.seed_restart_debt([("run-1", None)])
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {}

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            if info is None:
                return {}
            captured["round"] = info["round"]
            reports = []
            for row in info["rows"]:
                close_id = f"photo-{row['peer']}"
                captured[row["peer"]] = close_id
                reports.append(
                    {
                        "run_id": row["run_id"],
                        "peer": row["peer"],
                        "action": "published",
                        "close_id": close_id,
                        "reason": "",
                        "carried_floors": [],
                    }
                )
            self.coordinator.note_close_round(info["round"], reports)
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        self.clock.advance(40.0)
        self.coordinator.status(self.a)
        status_b, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status_b, 200)
        before = grant_b["lease_end"]
        if captured.get("round") is not None:
            self.coordinator.note_close_outcomes(
                [
                    self._close_outcome(
                        int(captured["round"]),
                        "run-1",
                        "client",
                        str(captured.get("client", "")),
                        "clean",
                    ),
                    self._close_outcome(
                        int(captured["round"]),
                        "run-1",
                        "server",
                        str(captured.get("server", "")),
                        "clean",
                    ),
                ]
            )
        status2, again = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status2, 200)
        self.assertEqual(again["lease_end"], before)

    def test_reports_are_checked_before_any_row_changes(self) -> None:
        self._bind()
        lease = self._commit_work(self.a)
        captured: dict[str, object] = {"errors": []}
        good = {
            "run_id": "run-1",
            "peer": "client",
            "action": "published",
            "close_id": "chk-client",
            "reason": "",
            "carried_floors": [],
        }

        def hook(session_id: str, lease_id: str, *_rest: object) -> dict:
            info = self.coordinator.lease_end_round(session_id, lease_id)
            assert info is not None
            for bad in (
                {"peer": "observer"},
                {"run_id": ""},
                {"peer": "server", "close_id": ""},
            ):
                try:
                    self.coordinator.note_close_round(
                        info["round"], [dict(good), {**good, **bad}]
                    )
                except ValueError as exc:
                    captured["errors"].append(str(exc))
            after = self.coordinator.lease_end_round(session_id, lease_id)
            assert after is not None
            captured["pending"] = [row["peer"] for row in after["rows"]]
            return {}

        self.cleanup_fn = hook
        self.coordinator.release(self.a, lease["lease_token"])
        self._wait_handoff()
        self.assertEqual(
            captured["errors"],
            ["invalid_peer", "invalid_run_id", "invalid_close_id"],
        )
        self.assertEqual(captured["pending"], ["client", "server"])
        with self.assertRaisesRegex(ValueError, "invalid_close_id"):
            self.coordinator.note_close_published(1, "run-1", "server", "", [])

    def test_restart_pairs_are_checked_before_any_row(self) -> None:
        for pairs, error in (
            ([("run-1", None), ("", None)], "invalid_run_id"),
            ([("run-1", None), ("run-2", "observer")], "invalid_role"),
        ):
            with self.subTest(error):
                with self.assertRaisesRegex(ValueError, error):
                    self.coordinator.seed_restart_debt(pairs)
                self.assertNotIn("lease_end", self.coordinator.status(self.a))


def _identity_payload(client: ClientIdentity) -> dict[str, object]:
    return {
        "platform": client.platform,
        "pid": client.pid,
        "ppid": client.ppid,
        "started_at_utc": client.started_at_utc,
        "session_id": client.session_id,
        "task_label": client.task_label,
    }


_CLOSE_NAMES = {"server": "lease_close", "client": "lease_close_client"}
_DROP = object()


class LeaseCloseBrokerStage2bTest(unittest.TestCase):
    """Stage 2b broker criteria (K1, K2, K4-K10, K14, K15, K18, K19, K32).

    A real ServerState publishes and settles the closes through
    cleanup_owner, its polls and its results, against a SessionCoordinator
    on a fake clock of its own. A census the broker is meant to read answers
    a close a poll delivered; the negatives of an undelivered or retired
    close send theirs without one.
    """

    def setUp(self) -> None:
        self.clock = FakeClock()
        self.broker_clock = FakeClock()
        self.ids = SequentialIds("lease")
        self.tokens = SequentialIds("token")
        self.audit = AuditSink()
        self.a = _identity("a")
        self.b = _identity("b")
        self._build()

    def _build(self, *, offline: bool = False, version_validator=None) -> None:
        self.state = loopback.ServerState(
            "test-key",
            version_validator=version_validator,
            time_fn=self.broker_clock,
        )
        if offline:
            self.state.install_bound_peer(
                instance=INST_SERVER, role="offline", pid=PID_SERVER, run_id="test-run"
            )
            self.instances = {"server": INST_SERVER, "client": INST_SERVER}
            self.pids = {"server": PID_SERVER, "client": PID_SERVER}
        else:
            bind_both_peers(self.state)
            self.instances = {"server": INST_SERVER, "client": INST_CLIENT}
            self.pids = {"server": PID_SERVER, "client": PID_CLIENT}
        self.coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=self.tokens,
            id_fn=self.ids,
            audit=self.audit,
            cleanup=lambda *args: self.state.cleanup_owner(*args),
        )
        self.state.coordination = self.coordinator
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        for peer in ("server", "client"):
            self.coordinator.note_peer_bound("test-run", peer, self.instances[peer])
            self.assertEqual(self._poll(peer), [])

    def _poll(
        self, peer: str, *, pid: int | None = None, version: str = "12~1.29"
    ) -> list[dict]:
        query = poll_census_query(peer)
        status, body = self.state.record_poll(
            peer,
            version,
            instance=self.instances[peer],
            source_pid=self.pids[peer] if pid is None else pid,
            caps=query["caps"],
            ach=query.get("ach"),
        )
        self.assertEqual(status, 200, body)
        return list(body.get("commands") or [])

    @staticmethod
    def _cmds(commands: list[dict]) -> list[str]:
        return [str(command.get("cmd")) for command in commands]

    def _queued(self, peer: str) -> list[str]:
        queue = self.state._bound_queues.get(self.instances[peer], [])
        return [
            name
            for name in self._cmds(queue)
            if loopback.peer_for_command(name) == peer
        ]

    def _acquire(self, client: ClientIdentity) -> dict:
        status, lease = self.coordinator.acquire(client, "trace")
        self.assertEqual(status, 200, lease)
        return lease

    def _enqueue(
        self,
        client: ClientIdentity,
        lease: dict,
        cmd: str,
        args: dict[str, object],
        peer: str,
    ) -> tuple[int, dict]:
        return self.state.enqueue_command(
            cmd,
            args,
            peer=peer,
            identity_payload=_identity_payload(client),
            lease_token=lease["lease_token"],
            operation_timeout_s=30.0,
        )

    def _deliver_work(self, client: ClientIdentity, lease: dict) -> int:
        status, body = self._enqueue(
            client, lease, "object_delete", {"object_id": 1}, "server"
        )
        self.assertEqual(status, 200, body)
        command_id = int(body["id"])
        self.assertEqual([item["id"] for item in self._poll("server")], [command_id])
        return command_id

    def _work(self) -> tuple[dict, int]:
        lease = self._acquire(self.a)
        return lease, self._deliver_work(self.a, lease)

    def _release(self, client: ClientIdentity, lease: dict) -> None:
        status, body = self.coordinator.release(client, lease["lease_token"])
        self.assertEqual(status, 200, body)
        with self.coordinator._condition:
            self.assertTrue(
                self.coordinator._condition.wait_for(
                    lambda: not self.coordinator._handoff_pending,
                    timeout=30.0,
                ),
                "handoff_pending did not clear",
            )

    def _ended_lease(self) -> tuple[dict, int]:
        lease, command_id = self._work()
        self._release(self.a, lease)
        return lease, command_id

    def _deliver_close(self, peer: str) -> dict:
        commands = self._poll(peer)
        self.assertEqual(self._cmds(commands), [_CLOSE_NAMES[peer]], commands)
        return commands[0]

    def _census(self, peer: str, close: dict, /, **fields: object) -> dict[str, object]:
        census: dict[str, object] = {
            "generation": close["args"]["close_generation"],
            "lease_id": close["args"]["close_lease_id"],
            "peer": peer,
            "state": "clean",
        }
        if peer == "client":
            census.update(
                entries=[{"name": "action", "state": "clean"}],
                move_floor=7,
                weapon_floor=4,
                net_low=10,
                net_high=20,
                conn_gen=1,
            )
        else:
            census.update(
                entries=[{"name": "bots", "state": "clean"}],
                floors=[_floor(10, 20, conn_gen=1, move_id=7, weapon_id=4)],
            )
        census.update(fields)
        return {key: value for key, value in census.items() if value is not _DROP}

    def _settle(
        self, peer: str, close: dict, /, *, instance: str | None = None, **fields: object
    ) -> tuple[int, dict]:
        return self.state.store_result(
            {"id": close["id"], "ok": True, "lease_close": self._census(peer, close, **fields)},
            instance=instance or self.instances[peer],
        )

    def _accepted(self, result: tuple[int, dict]) -> None:
        status, body = result
        self.assertEqual(status, 200, body)
        self.assertNotIn("discarded", body)

    def _discarded(self, result: tuple[int, dict]) -> None:
        status, body = result
        self.assertEqual((status, body.get("discarded")), (200, True), body)

    def _photo(self) -> dict:
        return self.coordinator.status(self.a).get("lease_end", {})

    def _owed(self) -> dict[str, list[str]]:
        return {
            row["peer"]: list(row["reasons"])
            for row in self._photo().get("unconfirmed", [])
        }

    def _settled_ids(self, grant: dict) -> set[str]:
        return {str(item["close_id"]) for item in grant["lease_end"]["settled"]}

    def _record_outcomes(self) -> list[tuple[str, str, str]]:
        seen: list[tuple[str, str, str]] = []
        note = self.coordinator.note_close_outcomes

        def recording(outcomes: list[dict[str, object]]) -> list[str]:
            statuses = note(outcomes)
            for outcome, status in zip(outcomes, statuses):
                seen.append((str(outcome["kind"]), str(outcome["reason"]), status))
            return statuses

        self.coordinator.note_close_outcomes = recording
        return seen

    def _degraded_grant(self) -> dict:
        """B queues behind A's round, then gets the lease once the rows time out."""
        status, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 202, queued)
        self.clock.advance(LEASE_END_BOUND_S)
        status, grant = self.coordinator.wait(self.b, queued["ticket"], 30.0)
        self.assertEqual(status, 200, grant)
        self.assertIn("lease_end_unconfirmed", grant["cleanup_degraded"])
        return grant

    def test_k1_abandoned_delivery_waits_for_both_closes(self) -> None:
        lease, command_id = self._work()
        self.state.abandon_command(command_id, "tool_timeout")
        self._release(self.a, lease)
        status, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 202, queued)
        client_close = self._deliver_close("client")
        self._accepted(self._settle("client", client_close))
        server_close = self._deliver_close("server")
        self._accepted(self._settle("server", server_close))
        status, grant = self.coordinator.wait(self.b, queued["ticket"], 30.0)
        self.assertEqual(status, 200, grant)
        self.assertEqual(
            self._settled_ids(grant), {str(client_close["id"]), str(server_close["id"])}
        )
        self.assertNotIn("lease_end_unconfirmed", grant.get("cleanup_degraded", []))
        self._discarded(
            self.state.store_result({"id": command_id, "ok": True}, instance=INST_SERVER)
        )

    def test_k1_server_census_from_another_instance_is_discarded(self) -> None:
        lease, command_id = self._work()
        self.state.abandon_command(command_id, "tool_timeout")
        self._release(self.a, lease)
        status, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 202, queued)
        self._accepted(self._settle("client", self._deliver_close("client")))
        server_close = self._deliver_close("server")
        self._discarded(self._settle("server", server_close, instance=INST_CLIENT))
        self.assertEqual((self._photo()["outstanding"], self._owed()), (1, {}))
        self.assertEqual(self.coordinator.acquire(self.b, "trace")[0], 202)
        self._accepted(self._settle("server", server_close))
        status, grant = self.coordinator.wait(self.b, queued["ticket"], 30.0)
        self.assertEqual(status, 200, grant)
        self.assertNotIn("lease_end_unconfirmed", grant.get("cleanup_degraded", []))

    def test_k2_unanswered_delivery_waits_for_both_closes(self) -> None:
        lease, command_id = self._ended_lease()
        status, queued = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 202, queued)
        client_close = self._deliver_close("client")
        self._accepted(self._settle("client", client_close))
        server_close = self._deliver_close("server")
        self._accepted(self._settle("server", server_close))
        status, grant = self.coordinator.wait(self.b, queued["ticket"], 30.0)
        self.assertEqual(status, 200, grant)
        self.assertEqual(
            self._settled_ids(grant), {str(client_close["id"]), str(server_close["id"])}
        )
        # The result arrives once the 30 s its tool waited are over.
        self.broker_clock.advance(30.0)
        self._discarded(
            self.state.store_result({"id": command_id, "ok": True}, instance=INST_SERVER)
        )

    def test_k4_lease_without_work_publishes_no_close(self) -> None:
        for read in (False, True):
            with self.subTest(read=read):
                lease = self._acquire(self.a)
                if read:
                    status, body = self._enqueue(
                        self.a, lease, "query_player_state", {}, "server"
                    )
                    self.assertEqual(status, 200, body)
                    self.assertEqual(
                        self._cmds(self._poll("server")), ["query_player_state"]
                    )
                self._release(self.a, lease)
                self.assertEqual((self._queued("server"), self._queued("client")), ([], []))
                grant = self._acquire(self.b)
                self.assertNotIn("lease_end", grant)
                self._release(self.b, grant)

    def test_k4_owed_row_opens_a_round_without_work(self) -> None:
        self.coordinator.seed_restart_debt([("test-run", None)])
        lease = self._acquire(self.a)
        self._release(self.a, lease)
        self.assertEqual(self._queued("client"), ["lease_close_client"])
        self.assertEqual(self.coordinator.acquire(self.b, "trace")[0], 202)

    def test_k5_server_only_lease_closes_the_client_first(self) -> None:
        self._ended_lease()
        self.assertEqual(self._queued("client"), ["lease_close_client"])
        self.assertEqual(self._queued("server"), [])
        self._accepted(self._settle("client", self._deliver_close("client")))
        self.assertEqual(self._queued("server"), ["lease_close"])

    def test_k5_ambiguous_client_owes_and_the_server_close_goes_at_once(self) -> None:
        lease, _command_id = self._work()
        self.assertEqual(self._poll("client", pid=PID_CLIENT + 1), [])
        self._release(self.a, lease)
        self.assertEqual(self._owed(), {"client": ["not_bound"]})
        self.assertEqual(self._queued("client"), [])
        self.assertEqual(self._queued("server"), ["lease_close"])

    def test_k6_offline_binding_hands_each_close_to_its_own_poll(self) -> None:
        self._build(offline=True)
        self._ended_lease()
        self.assertEqual(self._queued("client"), ["lease_close_client"])
        self.assertEqual(self._poll("server"), [])
        self.broker_clock.value = 10.0
        self.assertEqual(self._cmds(self._poll("server")), ["lease_close"])
        self.assertEqual(self._cmds(self._poll("client")), ["lease_close_client"])

    def test_k7_census_with_another_identity_is_ignored(self) -> None:
        for field, value in (
            ("generation", "another-generation"),
            ("lease_id", "another-lease"),
            ("peer", "server"),
        ):
            with self.subTest(field=field):
                self._build()
                self._ended_lease()
                close = self._deliver_close("client")
                ignored = self.state._lease_close_ignored
                self._accepted(self._settle("client", close, **{field: value}))
                self.assertEqual(self.state._lease_close_ignored, ignored + 1)
                self.assertEqual((self._photo()["outstanding"], self._owed()), (2, {}))
                self.assertEqual(self._queued("server"), [])
                # That result used up the close, so its exact echo is late.
                self._discarded(self._settle("client", close))
                self.assertEqual((self._photo()["outstanding"], self._owed()), (2, {}))

    def test_k7_census_from_another_instance_is_discarded(self) -> None:
        self._ended_lease()
        close = self._deliver_close("client")
        self._discarded(self._settle("client", close, instance=INST_SERVER))
        self.assertEqual((self._photo()["outstanding"], self._owed()), (2, {}))
        self._accepted(self._settle("client", close))
        self.assertEqual(self._queued("server"), ["lease_close"])

    def test_k7_census_after_the_epoch_changed_owes_the_row(self) -> None:
        self._ended_lease()
        close = self._deliver_close("client")
        self.state.install_bound_peer(
            instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id="test-run"
        )
        self._accepted(self._settle("client", close))
        self.assertEqual(self._owed(), {"client": ["binding_epoch_changed"]})

    def test_k8_queued_close_is_stale_once_b_holds_the_lease(self) -> None:
        self._ended_lease()
        grant = self._degraded_grant()
        # No admission fence: B's command goes in behind A's queued close.
        status, body = self._enqueue(self.b, grant, "key_press", {"dik": 1}, "client")
        self.assertEqual(status, 200, body)
        outcomes = self._record_outcomes()
        self.assertEqual(self._cmds(self._poll("client")), ["key_press"])
        self.assertEqual(self._queued("client"), [])
        self.assertEqual(outcomes, [("discarded", "lease_close_stale", "ignored")])
        self.assertEqual(
            self._owed(), {"client": ["close_timeout"], "server": ["close_timeout"]}
        )

    def test_k8_next_round_supersedes_a_queued_close(self) -> None:
        self._ended_lease()
        first = self.state._bound_queues[INST_CLIENT][0]["id"]
        grant = self._degraded_grant()
        self._deliver_work(self.b, grant)
        outcomes = self._record_outcomes()
        self._release(self.b, grant)
        queue = self.state._bound_queues[INST_CLIENT]
        self.assertEqual(self._cmds(queue), ["lease_close_client"])
        self.assertNotEqual(queue[0]["id"], first)
        self.assertEqual(outcomes, [("discarded", "lease_close_superseded", "ignored")])

    def test_k9_server_close_carries_the_client_floor(self) -> None:
        for reported, owed in ((7, {}), (5, {"server": ["player_move_floor_unapplied"]})):
            with self.subTest(reported=reported):
                self._build()
                self._ended_lease()
                self._accepted(self._settle("client", self._deliver_close("client")))
                server_close = self._deliver_close("server")
                self.assertEqual(
                    server_close["args"]["close_floors"],
                    [_floor(10, 20, conn_gen=1, move_id=7, weapon_id=4)],
                )
                floors = [_floor(10, 20, conn_gen=1, move_id=reported, weapon_id=4)]
                self._accepted(self._settle("server", server_close, floors=floors))
                self.assertEqual(self._owed(), owed)
                grant = self._acquire(self.b)
                self.assertEqual(
                    str(server_close["id"]) in self._settled_ids(grant), not owed
                )
                self._release(self.b, grant)

    def test_k9_client_census_without_conn_gen_owes_and_carries_no_floor(self) -> None:
        self._ended_lease()
        self._accepted(
            self._settle("client", self._deliver_close("client"), conn_gen=_DROP)
        )
        self.assertEqual(self._owed(), {"client": ["conn_gen_unknown"]})
        self.assertEqual(self._deliver_close("server")["args"]["close_floors"], [])

    def test_k10_close_is_published_past_max_queue(self) -> None:
        lease, _command_id = self._work()
        row = {"pin": self.state._destination_pin_locked("client", INST_CLIENT)}
        with self.state._lock:
            published = [
                self.state._publish_release_locked(dict(row), ("vehicle_release", {}), None)
                for _ in range(loopback.MAX_QUEUE)
            ]
            refused = self.state._publish_release_locked(
                dict(row), ("vehicle_release", {}), None
            )
        self.assertTrue(all(ok for ok, _reason, _id in published))
        self.assertEqual(refused, (False, "queue_full", None))
        self._release(self.a, lease)
        commands = self._poll("client")
        self.assertEqual(
            self._cmds(commands),
            ["vehicle_release"] * loopback.MAX_QUEUE + ["lease_close_client"],
        )

    def test_k14_retired_client_owes_its_close(self) -> None:
        for delivered in (False, True):
            with self.subTest(delivered=delivered):
                self._build()
                self._ended_lease()
                if delivered:
                    close = self._deliver_close("client")
                else:
                    close = self.state._bound_queues[INST_CLIENT][0]
                self.state.retire_role("test-run", "client", "replace-role")
                self.assertEqual(
                    self._owed(), {"client": ["close_discarded:binding_retired"]}
                )
                result = self._settle("client", close)
                if delivered:
                    self.assertEqual(result, (409, {"error": "binding_retired"}))
                else:
                    self._discarded(result)

    def test_k15_quarantine_at_lease_end_owes_every_row_at_once(self) -> None:
        lease, _command_id = self._work()
        self.state.retail_probe = lambda: {"known": True, "processes": [{"pid": 7}]}
        self._release(self.a, lease)
        self.assertEqual((self._queued("server"), self._queued("client")), ([], []))
        self.assertEqual(
            self._owed(),
            {"server": ["retail_quarantine"], "client": ["retail_quarantine"]},
        )
        grant = self._acquire(self.b)
        self.assertIn("lease_end_unconfirmed", grant["cleanup_degraded"])

    def test_k15_quarantine_after_publishing_discards_the_close(self) -> None:
        self._ended_lease()
        self.state.retail_probe = lambda: {"known": True, "processes": [{"pid": 7}]}
        self.assertEqual(self._poll("client"), [])
        self.assertEqual(self._queued("client"), [])
        self.assertEqual(
            self._owed(), {"client": ["close_discarded:retail_quarantine"]}
        )

    def test_k15_quarantine_while_the_server_close_waits_owes_it(self) -> None:
        for path in ("client outcome", "ten seconds"):
            with self.subTest(path=path):
                self._build()
                self._ended_lease()
                close = self._deliver_close("client")
                self.state.retail_probe = lambda: {
                    "known": True,
                    "processes": [{"pid": 7}],
                }
                if path == "client outcome":
                    self._accepted(self._settle("client", close))
                else:
                    self.broker_clock.value = 10.0
                    self.assertEqual(self._poll("server"), [])
                self.assertEqual(self._queued("server"), [])
                self.assertEqual(self._owed(), {"server": ["retail_quarantine"]})

    def test_k18_client_outcome_publishes_the_server_close(self) -> None:
        self._ended_lease()
        close = self._deliver_close("client")
        self.assertEqual(self._queued("server"), [])
        self._accepted(self._settle("client", close))
        self.assertEqual(self._queued("server"), ["lease_close"])

    def test_k18_silent_client_lets_the_server_close_go_at_ten_seconds(self) -> None:
        self._ended_lease()
        self._deliver_close("client")
        # The lease ended at 0 s on the broker's clock.
        self.broker_clock.value = 9.9
        self.assertEqual(self._poll("server"), [])
        self.broker_clock.value = 10.0
        self.assertEqual(self._cmds(self._poll("server")), ["lease_close"])

    def test_k18_ten_seconds_run_from_the_round_not_from_the_hook(self) -> None:
        # The round opens at 50 s on the coordinator's clock and 100 s on the
        # broker's, and the hook runs 8 s later on both.
        self.clock.value = 50.0
        self.broker_clock.value = 100.0
        cleanup_owner = self.state.cleanup_owner

        def late_hook(*args: object) -> object:
            self.clock.advance(8.0)
            self.broker_clock.advance(8.0)
            return cleanup_owner(*args)

        self.coordinator._cleanup = late_hook
        self._ended_lease()
        self._deliver_close("client")
        self.broker_clock.value = 109.9
        self.assertEqual(self._poll("server"), [])
        self.broker_clock.value = 110.0
        self.assertEqual(self._cmds(self._poll("server")), ["lease_close"])

    def test_k18_client_result_past_ten_seconds_gives_its_floor(self) -> None:
        # Nothing reaches the broker between the ten seconds and the client's
        # result, so the server close goes with that result and carries the
        # floor it brings.
        self._ended_lease()
        close = self._deliver_close("client")
        self.broker_clock.value = 10.0
        self._accepted(self._settle("client", close))
        self.assertEqual(
            self._deliver_close("server")["args"]["close_floors"],
            [_floor(10, 20, conn_gen=1, move_id=7, weapon_id=4)],
        )

    def test_k18_discarded_client_close_lets_the_server_close_go(self) -> None:
        self._ended_lease()
        self.state.retire_role("test-run", "client", "replace-role")
        self.assertEqual(self._queued("server"), [])
        self.assertEqual(self._deliver_close("server")["args"]["close_floors"], [])

    def test_k19_rows_are_reported_before_their_closes_are_queued(self) -> None:
        lease, _command_id = self._work()
        note = self.coordinator.note_close_round
        queued_at_report: list[list[str]] = []

        def report(round_id: int, reports: list[dict[str, object]]) -> set:
            queued_at_report.append(self._queued("client") + self._queued("server"))
            return note(round_id, [item for item in reports if item["peer"] != "client"])

        self.coordinator.note_close_round = report
        self._release(self.a, lease)
        self.assertEqual(queued_at_report, [[]])
        self.assertEqual(self._queued("client"), [])
        self.assertEqual(self._owed(), {"client": ["close_not_reported"]})

    def test_k19_expiry_inside_a_poll_delivers_the_close_in_that_answer(self) -> None:
        lease, _command_id = self._work()
        status, body = self._enqueue(self.a, lease, "key_press", {"dik": 1}, "client")
        self.assertEqual(status, 200, body)
        status, body = self.state.enqueue_command(
            "camera_get", {}, peer="client", internal=True
        )
        self.assertEqual(status, 200, body)
        self.clock.advance(SESSION_TTL_S + 1.0)
        self.assertEqual(
            self._cmds(self._poll("client")), ["camera_get", "lease_close_client"]
        )
        self.assertEqual(self._poll("client"), [])

    def test_k19_poll_that_runs_the_expiry_answers_before_the_game_does(self) -> None:
        lease, _command_id = self._work()
        status, body = self._enqueue(self.a, lease, "key_press", {"dik": 1}, "client")
        self.assertEqual(status, 200, body)
        self.clock.advance(SESSION_TTL_S + 1.0)
        query = poll_census_query("client")
        answers: list[tuple[int, dict]] = []
        answered = threading.Event()

        def poll() -> None:
            answers.append(
                self.state.record_poll(
                    "client",
                    "12~1.29",
                    instance=INST_CLIENT,
                    source_pid=PID_CLIENT,
                    caps=query["caps"],
                    ach=query.get("ach"),
                )
            )
            answered.set()

        thread = threading.Thread(target=poll, name="k19-poll")
        thread.start()
        try:
            # The game answers the close only once the poll has answered, so
            # a hook that waited for the game would hold this answer.
            self.assertTrue(answered.wait(1.0), "the poll waited for the game")
        finally:
            thread.join(30.0)
        status, body = answers[0]
        self.assertEqual(status, 200, body)
        commands = list(body["commands"])
        self.assertEqual(self._cmds(commands), ["lease_close_client"])
        self._accepted(self._settle("client", commands[0]))
        self.assertEqual(self._queued("server"), ["lease_close"])

    def test_k32_close_names_are_internal_only(self) -> None:
        lease = self._acquire(self.a)
        for peer, name in _CLOSE_NAMES.items():
            with self.subTest(name=name):
                self.assertEqual(loopback.peer_for_command(name), peer)
                self.assertNotIn(name, loopback.WHITELISTED_COMMANDS)
                args = {"close_generation": "g", "close_lease_id": "l"}
                for internal in (False, True):
                    status, body = self.state.enqueue_command(
                        name,
                        args,
                        peer=peer,
                        identity_payload=None if internal else _identity_payload(self.a),
                        lease_token=None if internal else lease["lease_token"],
                        operation_timeout_s=30.0,
                        internal=internal,
                    )
                    self.assertEqual((status, body.get("error")), (400, "not_whitelisted"))
        self.assertEqual((self._queued("server"), self._queued("client")), ([], []))

    def test_run_fence_keeps_the_close_and_drains_the_rest(self) -> None:
        lease, _command_id = self._work()
        status, body = self.state.enqueue_command(
            "camera_get", {}, peer="client", internal=True
        )
        self.assertEqual(status, 200, body)
        self._release(self.a, lease)
        self.state.lifecycle.manifest.get = lambda _run_id: SimpleNamespace(
            state="RUNNING_IDLE"
        )
        self.assertEqual(self._cmds(self._poll("client")), ["lease_close_client"])
        self.assertEqual(self.state._results[int(body["id"])]["error"], "run_not_owned")
        self.assertEqual(self._queued("client"), [])

    def test_version_blocked_client_owes_until_a_passing_poll(self) -> None:
        self._build(
            version_validator=lambda version: (
                "version_mismatch" if version == "11~1.29" else "ok"
            )
        )
        lease, _command_id = self._work()
        self.assertEqual(self._poll("client", version="11~1.29"), [])
        self._release(self.a, lease)
        self.assertEqual(self._owed(), {"client": ["version_blocked"]})
        self.assertEqual(self._queued("client"), [])
        self._accepted(self._settle("server", self._deliver_close("server")))
        self.assertEqual(self._poll("client"), [])
        grant = self._acquire(self.b)
        self._release(self.b, grant)
        self.assertEqual(self._queued("client"), ["lease_close_client"])

    def test_poll_that_delivers_nothing_settles_the_close_its_sweep_discarded(
        self,
    ) -> None:
        for answer in ("version blocked", "no queue"):
            with self.subTest(answer=answer):
                self._build(
                    version_validator=lambda version: (
                        "version_mismatch" if version == "11~1.29" else "ok"
                    )
                )
                self._ended_lease()
                # Only the broker's clock passes the queued close's 30 s.
                self.broker_clock.advance(loopback.COMMAND_TTL_S + 1.0)
                if answer == "version blocked":
                    self.assertEqual(self._poll("client", version="11~1.29"), [])
                else:
                    self.assertEqual(self._poll("client", pid=PID_CLIENT + 1), [])
                self.assertEqual(self._queued("client"), [])
                self.assertEqual(
                    self._owed(), {"client": ["close_discarded:stale_discarded"]}
                )

    def test_poll_that_delivers_nothing_finishes_what_its_sweep_discarded(
        self,
    ) -> None:
        # On main these two answers left before the discard reached the
        # coordinator.
        for answer in ("version blocked", "no queue"):
            with self.subTest(answer=answer):
                self._build(
                    version_validator=lambda version: (
                        "version_mismatch" if version == "11~1.29" else "ok"
                    )
                )
                self.audit.events.clear()
                lease = self._acquire(self.a)
                # Its tool limit outlasts the queue's 30 s, so the poll's
                # sweep discards it, not the reaper.
                status, body = self.state.enqueue_command(
                    "key_press",
                    {"dik": 1},
                    peer="client",
                    identity_payload=_identity_payload(self.a),
                    lease_token=lease["lease_token"],
                    operation_timeout_s=60.0,
                )
                self.assertEqual(status, 200, body)
                self.broker_clock.advance(loopback.COMMAND_TTL_S + 1.0)
                if answer == "version blocked":
                    self.assertEqual(self._poll("client", version="11~1.29"), [])
                else:
                    self.assertEqual(self._poll("client", pid=PID_CLIENT + 1), [])
                self.assertEqual(
                    [
                        event.get("command_id")
                        for event in self.audit.events
                        if event.get("decision") == "dispatch_discarded"
                    ],
                    [body["id"]],
                )

    def test_incomplete_pin_is_unreachable(self) -> None:
        lease, _command_id = self._work()
        self.state._bindings[INST_CLIENT].creation_time_utc = None
        self._release(self.a, lease)
        self.assertEqual(self._owed(), {"client": ["not_bound"]})
        self.assertEqual(self._queued("client"), [])

    def test_unreachable_server_at_its_deferred_publish_owes_its_row(self) -> None:
        self._ended_lease()
        close = self._deliver_close("client")
        self.assertEqual(self._poll("server", pid=PID_SERVER + 1), [])
        self._accepted(self._settle("client", close))
        self.assertEqual(self._owed(), {"server": ["not_bound"]})
        self.assertEqual(self._queued("server"), [])

    def test_note_close_unreachable_owes_only_a_deferred_row(self) -> None:
        lease, _command_id = self._ended_lease()
        info = self.coordinator.lease_end_round(self.a.session_id, lease["lease_id"])
        self.assertIsNotNone(info)
        assert info is not None
        round_id = int(info["round"])
        unreachable = self.coordinator.note_close_unreachable
        self.assertFalse(unreachable(round_id, "test-run", "client", "not_bound"))
        self.assertFalse(unreachable(round_id + 1, "test-run", "server", "not_bound"))
        self.assertTrue(unreachable(round_id, "test-run", "server", "not_bound"))
        self.assertFalse(unreachable(round_id, "test-run", "server", "version_blocked"))
        self.assertEqual(self._owed(), {"server": ["not_bound"]})

    def test_census_that_is_not_clean_owes_the_row(self) -> None:
        residue = [
            {"name": "one", "state": "active", "reason": "held"},
            {"name": "two", "state": "active", "reason": "held"},
            {"name": "vehicle_drive", "state": "active"},
        ]
        cases = (
            ("rejected", False, "client_not_in_game", None, ["close_rejected:client_not_in_game"]),
            ("failed", False, None, None, ["close_rejected:close_failed"]),
            ("no census", True, None, None, ["census_malformed"]),
            (
                "clean with an active entry",
                True,
                None,
                {"entries": [{"name": "action", "state": "active"}]},
                ["census_malformed"],
            ),
            ("residue", True, None, {"state": "residue", "entries": residue}, ["held", "vehicle_drive"]),
            ("bool move floor", True, None, {"move_floor": True}, ["census_malformed"]),
            ("bool conn_gen", True, None, {"conn_gen": True}, ["census_malformed"]),
        )
        # A residue still gives the coordinator its floor, which the server
        # close does not carry, so the server row owes it.
        server_owed = {"residue": ["player_move_floor_unapplied", "weapon_floor_unapplied"]}
        for name, ok, error, census, owed in cases:
            with self.subTest(name):
                self._build()
                self._ended_lease()
                close = self._deliver_close("client")
                body: dict[str, object] = {"id": close["id"], "ok": ok}
                if error is not None:
                    body["error"] = error
                if census is not None:
                    body["lease_close"] = self._census("client", close, **census)
                self._accepted(self.state.store_result(body, instance=INST_CLIENT))
                expected = {"client": owed}
                if name in server_owed:
                    expected["server"] = server_owed[name]
                self.assertEqual(self._owed(), expected)
                # The server close goes, but only a clean census carries a floor.
                self.assertEqual(self._deliver_close("server")["args"]["close_floors"], [])

    def test_bool_in_a_server_census_floor_is_malformed(self) -> None:
        self._ended_lease()
        self._accepted(self._settle("client", self._deliver_close("client")))
        floors = [_floor(10, 20, conn_gen=1, move_id=True, weapon_id=4)]
        self._accepted(self._settle("server", self._deliver_close("server"), floors=floors))
        self.assertEqual(self._owed(), {"server": ["census_malformed"]})

    def test_close_result_with_its_id_as_a_string_is_read(self) -> None:
        self._ended_lease()
        close = self._deliver_close("client")
        census = self._census("client", close)
        self._accepted(
            self.state.store_result(
                {"id": str(close["id"]), "ok": True, "lease_close": census},
                instance=INST_CLIENT,
            )
        )
        self.assertEqual(self._owed(), {})
        self.assertEqual(
            self._deliver_close("server")["args"]["close_floors"],
            [_floor(10, 20, conn_gen=1, move_id=7, weapon_id=4)],
        )

    def test_result_of_an_undelivered_close_is_ignored(self) -> None:
        self._ended_lease()
        queued = self.state._bound_queues[INST_CLIENT][0]
        ignored = self.state._lease_close_ignored
        self._discarded(self._settle("client", queued))
        self.assertEqual(self.state._lease_close_ignored, ignored + 1)
        self.assertEqual((self._photo()["outstanding"], self._owed()), (2, {}))
        close = self._deliver_close("client")
        self.assertEqual(close["id"], queued["id"])
        self._accepted(self._settle("client", close))
        self.assertEqual(self._queued("server"), ["lease_close"])
