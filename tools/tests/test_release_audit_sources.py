"""The releases of the other four sources leave one abandon_release line each.

Follow-up of PR 418, which audited every release decided for an abandoned
ai_group_spawn. The broker publishes a release from RELEASE_REGISTRY for any
delivered command it abandons (_abandon_row_locked): player_look_at,
action_hold, survivor_drive and camera_path_play too. On main only
ai_group_spawn builds the audit of that release
(_ABANDON_RELEASE_AUDIT_SOURCES), so the other four release on the deadline,
on the caller's /abandon or on an in-process abandon and write no line.

Each line goes through the daemon's JsonlAuditWriter, which refuses an event
without reason and duration_s, and is written after the broker lock is
released, even when a step after the decision fails. A command abandoned with
no release decided writes nothing.
"""
from __future__ import annotations

import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import loopback
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.instance_fence import instance_prefix
from dayz_mcp.runtime_state import JsonlAuditWriter, RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.fence_helpers import (
    INST_CLIENT,
    INST_SERVER,
    PID_CLIENT,
    PID_SERVER,
    bind_both_peers,
    bound_queue,
    poll_census_query,
)
from tests.lease_helpers import SequentialIds

# The three keys test_camera_path_focus_hud plays (_three_keys); built here, since
# a test module imports from no other test module (test_suite_structure).
CAMERA_KEYS = [
    {"t": t, "cam_pos": [t, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]} for t in (0, 3.0, 6.0)
]

OWNER = {
    "platform": "codex",
    "pid": 5151,
    "ppid": 10,
    "started_at_utc": "2026-11-17T16:00:00Z",
    "session_id": "release-audit",
    "task_label": "release audit",
}
WRITER_GENERATION = "gen-release-audit-sources"
RELEASE_EVENTS = ("abandon_release", "late_answer_abandon")

# (source command, peer, release command, wire arguments)
SOURCES = (
    ("player_look_at", "client", "player_look_at_release", {"pos": [0.0, 1.0, 10.0], "timeout_s": 4.0}),
    (
        "action_hold",
        "client",
        "action_hold_cancel",
        {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": 1.0},
    ),
    (
        "survivor_drive",
        "server",
        "survivor_drive_release",
        {"object_id": 7, "heading": 90.0, "heading_set": True, "speed": 1.0, "speed_set": True},
    ),
    (
        "camera_path_play",
        "client",
        "camera_path_release",
        {"mode": "play", "keys": CAMERA_KEYS, "easing": "linear", "path_id": "audit"},
    ),
)
HOLD = SOURCES[1]


class _LockCheckedWriter:
    """The daemon's JsonlAuditWriter, noting each write made under the broker lock."""

    def __init__(self, state: loopback.ServerState, inner: JsonlAuditWriter) -> None:
        self._state = state
        self._inner = inner
        self.under_lock: list[object] = []

    def write(self, event: dict) -> bool:
        if self._state._lock._is_owned():  # type: ignore[attr-defined]
            self.under_lock.append(event.get("event"))
        return self._inner.write(event)


class ReleaseAuditSourcesTest(unittest.TestCase):
    """abandon_release for player_look_at, action_hold, survivor_drive and
    camera_path_play: reason "deadline" when the reaper abandons the command at
    its operation deadline, otherwise the reason the abandoner gave (the
    caller's /abandon, or abandon_command in process), and "lease_end" for the
    cancel of a delivered hold that a lease end without a close round
    publishes; the decision release_queued with release_id, or
    cleanup_degraded with cause."""

    def setUp(self) -> None:
        self._writers: list[_LockCheckedWriter] = []

    # -- setup -------------------------------------------------------------

    def _state(self) -> tuple[loopback.ServerState, SessionCoordinator, list[float], str]:
        """Both peers bound with the current census, a real coordinator whose
        release runs this broker's cleanup_owner, and a held lease."""
        now = [1000.0]
        state = loopback.ServerState("test-key", time_fn=lambda: now[0])
        state.daemon_generation = "gen-release-audit"
        bind_both_peers(state)
        coordinator = SessionCoordinator(
            token_fn=SequentialIds("release-audit-token"),
            id_fn=SequentialIds("release-audit-lease"),
            audit=lambda _event: True,
            cleanup=state.cleanup_owner,
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        for peer in ("client", "server"):
            self._poll(state, peer)
        status, active = coordinator.acquire(ClientIdentity.from_payload(OWNER), OWNER["task_label"])
        self.assertEqual(status, 200, active)
        return state, coordinator, now, str(active["lease_token"])

    def _poll(self, state: loopback.ServerState, peer: str) -> list[dict]:
        instance, pid = (INST_SERVER, PID_SERVER) if peer == "server" else (INST_CLIENT, PID_CLIENT)
        status, polled = state.record_poll(
            peer,
            f"{EXPECTED_BRIDGE_VERSION}~1.29",
            instance=instance,
            source_pid=pid,
            **poll_census_query(peer),
        )
        self.assertEqual(status, 200, polled)
        return list(polled["commands"])

    def _enqueue(self, state: loopback.ServerState, token: str, source: tuple) -> dict:
        cmd, peer, _release, wire = source
        status, body = state.enqueue_command(
            cmd,
            json.loads(json.dumps(wire)),
            peer=peer,
            identity_payload=OWNER,
            lease_token=token,
            operation_timeout_s=5.0,
        )
        self.assertEqual(status, 200, body)
        return body

    def _delivered(self, state: loopback.ServerState, token: str, source: tuple) -> dict:
        """A command of source enqueued under the lease (deadline 5 s after
        enqueue) and handed to its peer by the next poll."""
        body = self._enqueue(state, token, source)
        self.assertEqual([c["id"] for c in self._poll(state, source[1])], [body["id"]])
        return body

    def _answer(self, state: loopback.ServerState, source: tuple, body: dict) -> dict:
        instance = INST_SERVER if source[1] == "server" else INST_CLIENT
        result: dict = {"id": body["id"], "ok": 1}
        if source[0] == "action_hold":
            result.update(hold_protocol=1, hold_id=body["hold_id"], cleanup_complete=1)
        status, stored = state.store_result(result, instance=instance)
        self.assertEqual(status, 200, stored)
        return stored

    def _request_abandon(
        self, state: loopback.ServerState, token: str, command_id: int, reason: str
    ) -> tuple[int, dict]:
        return state.request_abandon(
            identity_payload=OWNER,
            generation=state.daemon_generation,
            command_id=command_id,
            reason=reason,
            lease_token=token,
            lease_token_supplied=True,
        )

    def _queued_releases(self, state: loopback.ServerState, source: tuple) -> list[dict]:
        return [c for c in bound_queue(state, source[1]) if c.get("cmd") == source[2]]

    # -- audit file ----------------------------------------------------------

    def _real_audit(self, state: loopback.ServerState) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        paths = RuntimePaths(root, root / "audit", root / "coordination.json", root / "runs.json")
        writer = _LockCheckedWriter(state, JsonlAuditWriter(paths, WRITER_GENERATION))
        self._writers.append(writer)
        state.audit_writer = writer
        return root / "audit" / "events.jsonl"

    def _lines(self, audit_file: Path) -> list[dict]:
        for writer in self._writers:
            self.assertEqual(writer.under_lock, [], "an audit line was written under the broker lock")
        if not audit_file.exists():
            return []
        return [
            json.loads(line)
            for line in audit_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _release_lines(self, audit_file: Path) -> list[dict]:
        return [e for e in self._lines(audit_file) if e.get("event") in RELEASE_EVENTS]

    def _assert_line(
        self,
        line: dict,
        source: tuple,
        *,
        command_id: int,
        reason: str,
        decision: str,
        release_id: int | None = None,
        cause: str | None = None,
    ) -> None:
        instance = INST_SERVER if source[1] == "server" else INST_CLIENT
        self.assertEqual(line.get("event"), "abandon_release", line)
        self.assertEqual(line.get("command_id"), command_id, line)
        self.assertEqual(line.get("reason"), reason, line)
        self.assertIs(line.get("answered"), False, line)
        self.assertEqual((line.get("decision"), line.get("status")), (decision, decision), line)
        self.assertEqual(line.get("duration_s"), 0.0, line)
        self.assertEqual(line.get("expected_instance_prefix"), instance_prefix(instance), line)
        self.assertEqual(line.get("daemon_generation"), WRITER_GENERATION, line)
        if decision == "release_queued":
            self.assertIsInstance(release_id, int)
            self.assertEqual(line.get("release_id"), release_id, line)
            self.assertNotIn("cause", line)
        else:
            self.assertEqual(line.get("cause"), cause, line)
            self.assertNotIn("release_id", line)

    # -- releases decided --------------------------------------------------

    def test_the_deadline_release_is_audited(self) -> None:
        # The reaper at the operation deadline, before any answer. Mutants
        # killed: the source left out of the audit; no audit on the reaper's
        # abandon.
        for source in SOURCES:
            with self.subTest(source=source[0]):
                state, _coordinator, now, token = self._state()
                audit = self._real_audit(state)
                body = self._delivered(state, token, source)
                now[0] += 6.0
                state.status_snapshot()
                queued = self._queued_releases(state, source)
                self.assertEqual(len(queued), 1, bound_queue(state, source[1]))
                lines = self._release_lines(audit)
                self.assertEqual(len(lines), 1, self._lines(audit))
                self._assert_line(
                    lines[0], source, command_id=body["id"], reason="deadline",
                    decision="release_queued", release_id=queued[0]["id"],
                )
                # The answer that lands afterwards and the next reap add nothing.
                self.assertTrue(self._answer(state, source, body).get("discarded"))
                now[0] += 6.0
                state.status_snapshot()
                self.assertEqual(len(self._release_lines(audit)), 1, self._lines(audit))

    def test_the_callers_abandon_is_audited_with_its_reason(self) -> None:
        # HTTP /abandon, both reasons it admits. Mutant killed: no audit on
        # request_abandon.
        for source in SOURCES:
            for reason in ("cancelled", "tool_timeout"):
                with self.subTest(source=source[0], reason=reason):
                    state, _coordinator, now, token = self._state()
                    audit = self._real_audit(state)
                    body = self._delivered(state, token, source)
                    status, abandoned = self._request_abandon(state, token, body["id"], reason)
                    self.assertEqual((status, abandoned.get("status")), (200, "release_queued"), abandoned)
                    lines = self._release_lines(audit)
                    self.assertEqual(len(lines), 1, self._lines(audit))
                    self._assert_line(
                        lines[0], source, command_id=body["id"], reason=reason,
                        decision="release_queued", release_id=abandoned.get("release_id"),
                    )
                    # A second abandon and a reap past the deadline add nothing.
                    self.assertEqual(
                        self._request_abandon(state, token, body["id"], reason), (200, {"status": "noop"})
                    )
                    now[0] += 6.0
                    state.status_snapshot()
                    self.assertEqual(len(self._release_lines(audit)), 1, self._lines(audit))

    def test_an_in_process_abandon_is_audited_with_its_reason(self) -> None:
        # abandon_command without HTTP (the embedded server's cancelled tool).
        # Mutant killed: auditing only request_abandon.
        for source in SOURCES:
            with self.subTest(source=source[0]):
                state, _coordinator, _now, token = self._state()
                audit = self._real_audit(state)
                body = self._delivered(state, token, source)
                abandoned = state.abandon_command(body["id"], "tool_cancelled")
                self.assertEqual(abandoned.get("status"), "release_queued", abandoned)
                lines = self._release_lines(audit)
                self.assertEqual(len(lines), 1, self._lines(audit))
                self._assert_line(
                    lines[0], source, command_id=body["id"], reason="tool_cancelled",
                    decision="release_queued", release_id=abandoned.get("release_id"),
                )

    def test_a_release_that_cannot_be_queued_is_audited_with_its_cause(self) -> None:
        # Under retail quarantine, or with the peer's queue full, no release
        # is queued; the line says which (review r1 of PR 427: two causes),
        # whichever path decided it: the reaper at the deadline, the
        # caller's /abandon or an in-process abandon (review r2 of PR 427).
        # A second abandon and a reap add nothing. Mutants killed: auditing
        # only a queued release; one cause audited for every refusal; no line
        # for a refused release on one path.
        filler = {"server": "world_time_get", "client": "camera_get"}
        for source in SOURCES:
            for cause in ("quarantine", "queue_full"):
                for path in ("deadline", "http", "in_process"):
                    with self.subTest(source=source[0], cause=cause, path=path):
                        state, _coordinator, now, token = self._state()
                        audit = self._real_audit(state)
                        body = self._delivered(state, token, source)
                        if cause == "quarantine":
                            state.retail_probe = lambda: {"known": True, "processes": [{"name": "DayZ_x64.exe"}]}
                        else:
                            for _ in range(loopback.MAX_QUEUE):
                                status, queued = state.enqueue_command(
                                    filler[source[1]], {}, peer=source[1],
                                    identity_payload=OWNER, lease_token=token,
                                )
                                self.assertEqual(status, 200, queued)
                        refused = {"status": "cleanup_degraded", "cause": cause}
                        if path == "deadline":
                            now[0] += 6.0
                            state.status_snapshot()
                            reason = "deadline"
                        elif path == "http":
                            self.assertEqual(
                                self._request_abandon(state, token, body["id"], "cancelled"), (200, refused)
                            )
                            reason = "cancelled"
                        else:
                            self.assertEqual(state.abandon_command(body["id"], "cancelled"), refused)
                            reason = "cancelled"
                        self.assertEqual(self._queued_releases(state, source), [])
                        lines = self._release_lines(audit)
                        self.assertEqual(len(lines), 1, self._lines(audit))
                        self._assert_line(
                            lines[0], source, command_id=body["id"], reason=reason,
                            decision="cleanup_degraded", cause=cause,
                        )
                        self.assertEqual(state.abandon_command(body["id"], "cancelled"), {"status": "noop"})
                        now[0] += 6.0
                        state.status_snapshot()
                        self.assertEqual(len(self._release_lines(audit)), 1, self._lines(audit))

    def test_a_caller_holding_the_broker_lock_gets_no_audit_write_under_it(self) -> None:
        # The line waits for the next lock-free point. Mutant killed: the
        # write made under an outer acquisition of the broker lock.
        for source in SOURCES:
            with self.subTest(source=source[0]):
                state, _coordinator, _now, token = self._state()
                audit = self._real_audit(state)
                body = self._delivered(state, token, source)
                with state._lock:  # type: ignore[attr-defined]
                    abandoned = state.abandon_command(body["id"], "tool_cancelled")
                self.assertEqual(abandoned.get("status"), "release_queued", abandoned)
                self.assertEqual(self._release_lines(audit), [])
                state.status_snapshot()
                lines = self._release_lines(audit)
                self.assertEqual(len(lines), 1, self._lines(audit))
                self._assert_line(
                    lines[0], source, command_id=body["id"], reason="tool_cancelled",
                    decision="release_queued", release_id=abandoned.get("release_id"),
                )

    def test_a_failure_after_the_decision_keeps_the_line(self) -> None:
        # Review r3 of PR 427: once the release is decided under the lock, a
        # later failure (finish_operation_exact raising) must not lose its
        # line, whichever path decided it: the reaper at the deadline, the
        # caller's /abandon or an in-process abandon. Review r1 of PR 428:
        # whatever the error, and for a refused release too (retail
        # quarantine). The error still reaches the caller, as PR 418 pins for
        # the spawn, and a second abandon and a reap add nothing. Mutants
        # killed: these sources audited only after a flush that returned
        # normally, on every path or on one; the error swallowed; the line
        # written on one error type only; only a queued release audited after
        # a failure.
        reasons = {"deadline": "deadline", "http": "cancelled", "in_process": "tool_cancelled"}
        cases = itertools.product(
            SOURCES, reasons.items(), ("release_queued", "cleanup_degraded"), (RuntimeError, OSError)
        )
        for source, (path, reason), decision, error in cases:
            with self.subTest(source=source[0], path=path, decision=decision, error=error.__name__):
                state, coordinator, now, token = self._state()
                audit = self._real_audit(state)
                body = self._delivered(state, token, source)
                if decision == "cleanup_degraded":
                    state.retail_probe = lambda: {"known": True, "processes": [{"name": "DayZ_x64.exe"}]}
                with patch.object(coordinator, "finish_operation_exact", side_effect=error("injected")):
                    with self.assertRaises(error):
                        if path == "deadline":
                            now[0] += 6.0
                            state.status_snapshot()
                        elif path == "http":
                            self._request_abandon(state, token, body["id"], reason)
                        else:
                            state.abandon_command(body["id"], reason)
                queued = self._queued_releases(state, source)
                lines = self._release_lines(audit)
                self.assertEqual(len(lines), 1, self._lines(audit))
                if decision == "release_queued":
                    self.assertEqual(len(queued), 1, bound_queue(state, source[1]))
                    self._assert_line(
                        lines[0], source, command_id=body["id"], reason=reason,
                        decision=decision, release_id=queued[0]["id"],
                    )
                else:
                    self.assertEqual(queued, [])
                    self._assert_line(
                        lines[0], source, command_id=body["id"], reason=reason,
                        decision=decision, cause="quarantine",
                    )
                self.assertEqual(state.abandon_command(body["id"], "cancelled"), {"status": "noop"})
                now[0] += 6.0
                state.status_snapshot()
                self.assertEqual(len(self._release_lines(audit)), 1, self._lines(audit))

    def test_a_hold_cancelled_by_a_lease_end_without_a_round_is_audited_as_lease_end(self) -> None:
        # cleanup_owner's path without a lease-end close round
        # (loopback.cleanup_owner, "else" of has_round) abandons each
        # delivered hold and publishes its action_hold_cancel. With the peers
        # still bound, the coordinator opens a round once the lease delivered
        # work, so this test takes the round away (lease_end_round answers
        # None) to see the cancel queued; the next test takes the real way
        # there. The line names the lease end, not a cancel by the caller.
        # Mutants killed: the hold left out; the reason "cancelled".
        state, coordinator, _now, token = self._state()
        audit = self._real_audit(state)
        body = self._delivered(state, token, HOLD)
        coordinator.lease_end_round = lambda _session_id, _lease_id: None  # type: ignore[method-assign]
        status, released = coordinator.release(ClientIdentity.from_payload(OWNER), token, "owner_release")
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("hold_cancel_enqueued"), 1, released)
        queued = self._queued_releases(state, HOLD)
        self.assertEqual(len(queued), 1, bound_queue(state, "client"))
        lines = self._release_lines(audit)
        self.assertEqual(len(lines), 1, self._lines(audit))
        self._assert_line(
            lines[0], HOLD, command_id=body["id"], reason="lease_end",
            decision="release_queued", release_id=queued[0]["id"],
        )

    def test_a_hold_whose_run_retired_before_the_lease_end_is_audited_as_lease_end(self) -> None:
        # The real way to a lease end without a close round (review r1 of PR
        # 427): the run's processes are gone before the lease is released, so
        # the coordinator opens no round, and cleanup_owner abandons the
        # delivered hold. Its destination is gone, so the cancel cannot be
        # queued, and the line says so. Mutants killed: the hold left out; the
        # reason "cancelled".
        state, coordinator, _now, token = self._state()
        audit = self._real_audit(state)
        body = self._delivered(state, token, HOLD)
        state.retire_run("test-run", "processes_gone")
        coordinator.note_processes_gone("test-run", None)
        status, released = coordinator.release(ClientIdentity.from_payload(OWNER), token, "owner_release")
        self.assertEqual(status, 200, released)
        self.assertNotIn("hold_cancel_enqueued", released["cleanup"])
        self.assertEqual(self._queued_releases(state, HOLD), [])
        lines = self._release_lines(audit)
        self.assertEqual(len(lines), 1, self._lines(audit))
        self._assert_line(
            lines[0], HOLD, command_id=body["id"], reason="lease_end",
            decision="cleanup_degraded", cause="destination_changed",
        )

    # -- no release decided ------------------------------------------------

    def test_no_line_when_no_release_is_decided(self) -> None:
        # Guards (pass on main): abandoned before delivery (dropped); answered
        # on time, then abandoned and past the deadline (the row left with the
        # answer); answered at the deadline before any reap (a late answer is
        # discarded and these sources publish no release for it); a lease end
        # with its close round (the close, not a per-command release, stops
        # what runs). Mutant killed: a line written for an abandon that
        # decided no release.
        for source in SOURCES:
            with self.subTest(source=source[0], case="queued"):
                state, _coordinator, _now, token = self._state()
                audit = self._real_audit(state)
                body = self._enqueue(state, token, source)
                self.assertEqual(state.abandon_command(body["id"], "cancelled"), {"status": "dropped"})
                self.assertEqual(self._release_lines(audit), [])
            with self.subTest(source=source[0], case="answered_on_time"):
                state, _coordinator, now, token = self._state()
                audit = self._real_audit(state)
                body = self._delivered(state, token, source)
                self.assertNotIn("discarded", self._answer(state, source, body))
                self.assertEqual(
                    self._request_abandon(state, token, body["id"], "cancelled"), (200, {"status": "noop"})
                )
                now[0] += 6.0
                state.status_snapshot()
                self.assertEqual(self._queued_releases(state, source), [])
                self.assertEqual(self._release_lines(audit), [])
            with self.subTest(source=source[0], case="answered_late"):
                state, _coordinator, now, token = self._state()
                audit = self._real_audit(state)
                body = self._delivered(state, token, source)
                now[0] += 5.0
                self.assertTrue(self._answer(state, source, body).get("discarded"))
                state.status_snapshot()
                self.assertEqual(self._queued_releases(state, source), [])
                self.assertEqual(self._release_lines(audit), [])
            with self.subTest(source=source[0], case="lease_end_with_a_round"):
                state, coordinator, _now, token = self._state()
                audit = self._real_audit(state)
                self._delivered(state, token, source)
                status, released = coordinator.release(
                    ClientIdentity.from_payload(OWNER), token, "owner_release"
                )
                self.assertEqual(status, 200, released)
                self.assertEqual(self._queued_releases(state, source), [])
                self.assertEqual(self._release_lines(audit), [])


if __name__ == "__main__":
    unittest.main()
