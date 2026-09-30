"""Every audit_failed names the coordinator step that failed (fb-20260821-162508-00c4, part a).

session_acquire_wait used to answer a bare audit_failed, so the caller could not
tell a ledger write from a fault-marker (WAL) step, a snapshot, a revoked grant
or a failure an earlier call left latched. Each coordinator test below injects
one stage the way the neighbouring coordination tests do (AuditSink.fail_events,
fault-store callbacks, the release-audit slots, a replaced audit gate) and reads
audit_stage back; without the change the key is absent and every test fails.
The carrier tests follow the same value through loopback, the lifecycle, the
control client and the public tool text, and check that nothing outside the
closed vocabulary is echoed.
"""

from __future__ import annotations

import ast
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server, session_coordination as coordination_module
from dayz_mcp.control_client import ControlClientError
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import (
    AUDIT_STAGES,
    SessionCoordinator,
    public_audit_stage,
)
from tests.client_helpers import _fixture_client_runtime
from tests.fence_helpers import bind_both_peers
from tests.lease_helpers import (
    AuditSink,
    CleanupSink,
    FakeClock,
    SequentialIds,
    _identity,
)
from tests.lifecycle_helpers import IDENTITY, IDENTITY_PAYLOAD, LifecycleFixtureContext
from tests.session_http_helpers import SessionLoopback


_SPAWN_ARGS = {"type": "X", "pos": [1, 2, 3], "flags": 0, "rotation": 0}


class _Wal:
    """Coordination fault-store callbacks with one failure switch per step."""

    def __init__(self) -> None:
        self.fail_arm: set[str] = set()
        self.fail_transition = False
        self.fail_persist = False
        self.fail_clear = False
        self._serial = 0

    def _sha(self) -> str:
        self._serial += 1
        return f"sha-{self._serial}"

    def arm(self, marker: dict[str, object]) -> str:
        if marker.get("operation") in self.fail_arm:
            raise OSError("coordination fault lock unavailable")
        return self._sha()

    def transition(
        self, _fault_id: str, _expected_sha256: str, **_changes: object
    ) -> object:
        if self.fail_transition:
            return False
        return self._sha()

    def persist(self, _snapshot: dict[str, object]) -> bool:
        return not self.fail_persist

    def clear(self, _fault_id: str, _expected_sha256: str) -> bool:
        return not self.fail_clear


def _coordinator(
    audit: object | None = None,
    *,
    wal: _Wal | None = None,
    clock: FakeClock | None = None,
) -> SessionCoordinator:
    callbacks: dict[str, object] = {}
    if wal is not None:
        callbacks = {
            "fault_arm": wal.arm,
            "fault_transition": wal.transition,
            "fault_clear": wal.clear,
            "persist_snapshot": wal.persist,
        }
    return SessionCoordinator(
        time_fn=clock or FakeClock(),
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("id"),
        fault_id_fn=SequentialIds("fault"),
        audit=audit if audit is not None else AuditSink(),
        cleanup=CleanupSink(),
        **callbacks,
    )


def _wait_handoff(coordinator: SessionCoordinator) -> None:
    # release() returns after at most RELEASE_AUDIT_TIMEOUT_S while the handoff
    # fence can still be up; the FIFO tests wait for it the same way.
    with coordinator._condition:
        if not coordinator._condition.wait_for(
            lambda: not coordinator._handoff_pending, timeout=1.0
        ):
            raise AssertionError("handoff fence still up")


def _stage(result: tuple[int, dict]) -> tuple[int, object, object]:
    status, payload = result
    return status, payload.get("error"), payload.get("audit_stage")


class AuditStageVocabularyTest(unittest.TestCase):
    def test_only_closed_identifier_shaped_values_pass(self) -> None:
        self.assertTrue(AUDIT_STAGES)
        for stage in AUDIT_STAGES:
            with self.subTest(stage=stage):
                self.assertTrue(server._is_safe_error_token(stage), stage)
                self.assertEqual(public_audit_stage(stage), stage)
        for value in (
            None,
            "",
            "WRITE",
            "write ",
            "C:\\Users\\someone\\audit",
            7,
            True,
            ["write"],
            {"write": 1},
        ):
            with self.subTest(value=value):
                self.assertIsNone(public_audit_stage(value))


class AuditStageCensusTest(unittest.TestCase):
    """Every audit_failed result of the coordinator names a stage from the set."""

    def setUp(self) -> None:
        source = Path(coordination_module.__file__).read_text(encoding="utf-8")
        self.tree = ast.parse(source)

    def _emitted_stages(self) -> set[str]:
        stages = set(coordination_module._WAL_OUTCOME_STAGES.values())
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(
                    func, "id", ""
                )
                if name in {"_audit_failed_payload", "failed"} and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        stages.add(first.value)
                for keyword in node.keywords:
                    if keyword.arg == "audit_stage" and isinstance(
                        keyword.value, ast.Constant
                    ):
                        stages.add(keyword.value.value)
            if isinstance(node, ast.FunctionDef) and node.name in {
                "_queued_grant_audit_stage_locked",
                "_write_initial_grant_audits_locked",
            }:
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Return)
                        and isinstance(inner.value, ast.Constant)
                        and isinstance(inner.value.value, str)
                    ):
                        stages.add(inner.value.value)
        return stages - {"ok", "busy"}

    def test_every_emitted_stage_is_in_the_set_and_every_member_is_emitted(self) -> None:
        emitted = self._emitted_stages()
        self.assertEqual(sorted(emitted - AUDIT_STAGES), [])
        self.assertEqual(sorted(AUDIT_STAGES - emitted), [])

    def test_only_the_helper_builds_the_audit_failed_body(self) -> None:
        owners: list[str] = []
        for function in ast.walk(self.tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Dict) and any(
                    isinstance(key, ast.Constant)
                    and key.value == "error"
                    and isinstance(value, ast.Constant)
                    and value.value == "audit_failed"
                    for key, value in zip(node.keys, node.values)
                ):
                    owners.append(function.name)
        self.assertEqual(owners, ["_audit_failed_payload"])

    def test_every_audit_failed_decision_names_its_stage(self) -> None:
        decisions = 0
        for node in ast.walk(self.tree):
            if not (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "AuthorizationDecision"
            ):
                continue
            error = node.args[2] if len(node.args) > 2 else None
            if not (isinstance(error, ast.Constant) and error.value == "audit_failed"):
                continue
            decisions += 1
            self.assertIn(
                "audit_stage",
                {keyword.arg for keyword in node.keywords},
                ast.dump(node),
            )
        # authorize, reject_reservation and reject_authorization: the census is
        # not allowed to pass by finding nothing.
        self.assertGreaterEqual(decisions, 3)


class CoordinatorAuditStageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = _identity("a")
        self.waiter = _identity("b")

    def _fifo_claim(self, wal: _Wal) -> tuple[SessionCoordinator, str]:
        """An owner releases cleanly; the waiter is left at the head, unclaimed."""
        coordinator = _coordinator(wal=wal)
        token = coordinator.acquire(self.owner, "drive")[1]["lease_token"]
        ticket = coordinator.acquire(self.waiter, "camera")[1]["ticket"]
        self.assertEqual(coordinator.release(self.owner, token)[0], 200)
        _wait_handoff(coordinator)
        return coordinator, ticket

    def test_write_names_this_requests_own_ledger_event(self) -> None:
        audit = AuditSink()
        coordinator = _coordinator(audit)
        token = coordinator.acquire(self.owner, "drive")[1]["lease_token"]

        audit.fail_events = {"session_queued"}
        self.assertEqual(
            _stage(coordinator.enqueue(self.waiter, "camera", "op-b")),
            (503, "audit_failed", "write"),
        )

        audit.fail_events = set()
        ticket = coordinator.enqueue(self.waiter, "camera", "op-b")[1]["ticket"]
        audit.fail_events = {"session_wait"}
        self.assertEqual(
            _stage(coordinator.wait(self.waiter, ticket, 0.0)),
            (503, "audit_failed", "write"),
        )

        audit.fail_events = {"session_heartbeat"}
        self.assertEqual(
            _stage(coordinator.heartbeat(self.owner, token)),
            (503, "audit_failed", "write"),
        )

    def test_write_travels_on_authorization_decisions(self) -> None:
        audit = AuditSink()
        coordinator = _coordinator(audit)
        token = coordinator.acquire(self.owner, "drive")[1]["lease_token"]

        audit.fail_events = {"session_authorized"}
        refused = coordinator.authorize(self.owner, token, "world_spawn")
        self.assertEqual(
            (refused.allowed, refused.error, refused.audit_stage),
            (False, "audit_failed", "write"),
        )

        audit.fail_events = set()
        allowed = coordinator.authorize(self.owner, token, "world_spawn")
        self.assertTrue(allowed.allowed)
        audit.fail_events = {"session_rejected"}
        rejected = coordinator.reject_reservation(
            self.owner.session_id,
            allowed.lease_id,
            allowed.reservation_id,
            "retail_quarantine",
        )
        self.assertEqual(
            (rejected.error, rejected.audit_stage), ("audit_failed", "write")
        )

        audit.fail_events = set()
        self.assertTrue(coordinator.authorize(self.owner, token, "world_spawn").allowed)
        audit.fail_events = {"session_rejected"}
        rejected = coordinator.reject_authorization(
            self.owner.session_id, "world_spawn", "retail_quarantine"
        )
        self.assertEqual(
            (rejected.error, rejected.audit_stage), ("audit_failed", "write")
        )

        # Any other refusal carries no stage.
        audit.fail_events = set()
        stolen = coordinator.authorize(self.waiter, token, "world_spawn")
        self.assertEqual((stolen.error, stolen.audit_stage), ("lease_invalid", None))

    def test_gate_names_an_audit_gate_that_cannot_be_taken(self) -> None:
        coordinator, ticket = self._fifo_claim(_Wal())

        class _BrokenGate:
            def acquire(self, blocking: bool = True) -> bool:
                raise RuntimeError("audit gate unavailable")

            def release(self) -> None:
                raise AssertionError("a gate that was never taken is released")

        coordinator._audit_gate = _BrokenGate()
        self.assertEqual(
            _stage(coordinator.wait(self.waiter, ticket, 0.0)),
            (503, "audit_failed", "gate"),
        )

    def test_wal_arm_names_a_fault_marker_that_cannot_be_armed(self) -> None:
        with self.subTest(path="immediate_grant"):
            wal = _Wal()
            wal.fail_arm = {"grant"}
            coordinator = _coordinator(wal=wal)
            self.assertEqual(
                _stage(coordinator.acquire(self.owner, "drive")),
                (503, "audit_failed", "wal_arm"),
            )

        with self.subTest(path="fifo_claim"):
            wal = _Wal()
            coordinator, ticket = self._fifo_claim(wal)
            wal.fail_arm = {"grant"}
            self.assertEqual(
                _stage(coordinator.wait(self.waiter, ticket, 0.0)),
                (503, "audit_failed", "wal_arm"),
            )

        with self.subTest(path="release"):
            wal = _Wal()
            coordinator = _coordinator(wal=wal)
            token = coordinator.acquire(self.owner, "drive")[1]["lease_token"]
            wal.fail_arm = {"release"}
            self.assertEqual(
                _stage(coordinator.release(self.owner, token)),
                (503, "audit_failed", "wal_arm"),
            )
            self.assertEqual(coordinator.status(self.owner)["self"]["state"], "active")

        with self.subTest(path="admin_release"):
            wal = _Wal()
            coordinator = _coordinator(wal=wal)
            lease_id = coordinator.acquire(self.owner, "drive")[1]["lease_id"]
            wal.fail_arm = {"release"}
            self.assertEqual(
                _stage(coordinator.admin_release(lease_id, "operator")),
                (503, "audit_failed", "wal_arm"),
            )

    def _assert_publication_step(self, switch: str, stage: str) -> None:
        with self.subTest(path="immediate_grant"):
            wal = _Wal()
            setattr(wal, switch, True)
            coordinator = _coordinator(wal=wal)
            result = coordinator.acquire(self.owner, "drive")
            self.assertEqual(_stage(result), (503, "audit_failed", stage))
            self.assertNotIn("lease_token", result[1])

        with self.subTest(path="fifo_claim"):
            wal = _Wal()
            coordinator, ticket = self._fifo_claim(wal)
            setattr(wal, switch, True)
            result = coordinator.wait(self.waiter, ticket, 0.0)
            self.assertEqual(_stage(result), (503, "audit_failed", stage))
            self.assertNotIn("lease_token", result[1])

    def test_wal_transition_names_a_marker_that_cannot_advance(self) -> None:
        self._assert_publication_step("fail_transition", "wal_transition")

    def test_snapshot_names_a_coordination_snapshot_that_cannot_persist(self) -> None:
        self._assert_publication_step("fail_persist", "snapshot")

    def test_wal_clear_names_a_marker_that_cannot_be_cleared(self) -> None:
        self._assert_publication_step("fail_clear", "wal_clear")

    def test_compensation_names_a_grant_that_cannot_be_revoked(self) -> None:
        coordinator: SessionCoordinator

        def audit(event: dict[str, object]) -> bool:
            if event.get("event") == "session_granted":
                # The cancel lands while the grant is being audited, so the
                # grant has to be revoked; that revocation is what fails.
                coordinator.cancel_operation(self.owner, "op-a")
            return event.get("event") != "session_grant_revoked"

        coordinator = _coordinator(audit)
        result = coordinator.acquire(self.owner, "drive", "op-a")
        self.assertEqual(_stage(result), (503, "audit_failed", "compensation"))
        self.assertNotIn("lease_token", result[1])

    def test_reservation_names_a_same_client_queue_request_still_in_flight(self) -> None:
        entered = threading.Event()
        resume = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            client = event.get("client") or {}
            if (
                event.get("event") == "session_acquire"
                and client.get("session") == self.waiter.session_id[:12]
            ):
                entered.set()
                resume.wait(5.0)
            return True

        coordinator = _coordinator(audit)
        coordinator.acquire(self.owner, "drive")
        first: list[tuple[int, dict]] = []
        thread = threading.Thread(
            target=lambda: first.append(coordinator.acquire(self.waiter, "camera"))
        )
        thread.start()
        try:
            self.assertTrue(entered.wait(1.0))
            retry = coordinator.acquire(self.waiter, "camera")
        finally:
            resume.set()
            thread.join(2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(_stage(retry), (503, "audit_failed", "reservation"))
        self.assertEqual(first[0][0], 202)

    def test_expiry_names_a_ticket_expiry_this_call_failed_to_audit(self) -> None:
        clock = FakeClock()
        audit = AuditSink()
        coordinator = _coordinator(audit, clock=clock)
        token = coordinator.acquire(self.owner, "drive")[1]["lease_token"]
        coordinator.acquire(self.waiter, "camera")
        clock.advance(1.0)
        late = _identity("c")
        ticket = coordinator.acquire(late, "weather")[1]["ticket"]
        self.assertEqual(coordinator.release(self.owner, token)[0], 200)
        _wait_handoff(coordinator)
        # The first ticket is past its TTL, the late one is not.
        clock.advance(119.5)
        audit.fail_events = {"ticket_cancelled"}
        self.assertEqual(
            _stage(coordinator.wait(late, ticket, 0.0)),
            (503, "audit_failed", "expiry"),
        )

    def test_fault_latched_names_a_fault_latched_while_the_waiter_waits(self) -> None:
        failing = threading.Event()
        waiting = threading.Event()

        def audit(event: dict[str, object]) -> bool:
            return not (
                failing.is_set() and event.get("event") == "session_release_started"
            )

        class _WaitProbe(threading.Condition):
            def wait(self, timeout: float | None = None) -> bool:
                if threading.current_thread().name == "audit-stage-waiter":
                    waiting.set()
                return super().wait(timeout)

        coordinator = _coordinator(audit, wal=_Wal())
        coordinator._condition = _WaitProbe()
        token = coordinator.acquire(self.owner, "drive")[1]["lease_token"]
        ticket = coordinator.acquire(self.waiter, "camera")[1]["ticket"]
        results: list[tuple[int, dict]] = []
        thread = threading.Thread(
            name="audit-stage-waiter",
            target=lambda: results.append(coordinator.wait(self.waiter, ticket, 5.0)),
        )
        thread.start()
        try:
            self.assertTrue(waiting.wait(2.0))
            failing.set()
            # The release ledger batch fails and latches a coordination audit
            # fault while the waiter is parked inside wait().
            self.assertEqual(coordinator.release(self.owner, token)[0], 200)
        finally:
            thread.join(5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(_stage(results[0]), (503, "audit_failed", "fault_latched"))
        self.assertIsNotNone(coordinator.status(self.waiter)["audit_fault"])

    def test_grant_latched_names_an_earlier_grant_audit_failure(self) -> None:
        audit = AuditSink()
        coordinator = _coordinator(audit)
        token = coordinator.acquire(self.owner, "drive")[1]["lease_token"]
        ticket = coordinator.acquire(self.waiter, "camera")[1]["ticket"]
        self.assertEqual(coordinator.release(self.owner, token)[0], 200)
        _wait_handoff(coordinator)
        audit.fail_events = {"session_granted", "session_grant_revoked"}
        # The claim's own grant write fails, and so does its revocation...
        self.assertEqual(
            _stage(coordinator.wait(self.waiter, ticket, 0.0)),
            (503, "audit_failed", "write"),
        )
        # ...which leaves the queue fenced for the next call.
        audit.fail_events = set()
        self.assertEqual(
            _stage(coordinator.wait(self.waiter, ticket, 0.0)),
            (503, "audit_failed", "grant_latched"),
        )

    def test_handoff_latched_names_an_earlier_release_handoff_failure(self) -> None:
        coordinator = _coordinator()
        token = coordinator.acquire(self.owner, "drive")[1]["lease_token"]
        ticket = coordinator.acquire(self.waiter, "camera")[1]["ticket"]
        slots = coordinator._release_audit_worker_slots
        held = 0
        while slots.acquire(blocking=False):
            held += 1
        try:
            # No release-audit worker can start: the handoff is not audited.
            released = coordinator.release(self.owner, token)
        finally:
            for _ in range(held):
                slots.release()
        self.assertEqual(released[0], 200)
        self.assertIsNone(coordinator.status(self.waiter)["audit_fault"])
        self.assertEqual(
            _stage(coordinator.wait(self.waiter, ticket, 0.0)),
            (503, "audit_failed", "handoff_latched"),
        )


class LoopbackCarrierAuditStageTest(unittest.TestCase):
    def _state(self, audit: AuditSink) -> tuple[loopback.ServerState, str]:
        state = loopback.ServerState("key")
        bind_both_peers(state)
        coordinator = _coordinator(audit)
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        token = coordinator.acquire(IDENTITY, "drive")[1]["lease_token"]
        return state, token

    def _enqueue(self, state: loopback.ServerState, token: str) -> tuple[int, dict]:
        return state.enqueue_command(
            "world_spawn",
            dict(_SPAWN_ARGS),
            "server",
            identity_payload=IDENTITY_PAYLOAD,
            lease_token=token,
        )

    def test_an_authorize_refusal_keeps_its_stage(self) -> None:
        audit = AuditSink()
        state, token = self._state(audit)
        audit.fail_events = {"session_authorized"}
        self.assertEqual(
            _stage(self._enqueue(state, token)), (503, "audit_failed", "write")
        )

    def test_a_retail_rejection_keeps_its_stage(self) -> None:
        audit = AuditSink()
        state, token = self._state(audit)
        state.retail_probe = lambda: {"known": True, "processes": [{"pid": 4242}]}
        audit.fail_events = {"session_rejected"}
        status, payload = self._enqueue(state, token)
        self.assertEqual(_stage((status, payload)), (503, "audit_failed", "write"))
        self.assertEqual(payload.get("reason"), "retail_present")


class LifecycleCarrierAuditStageTest(unittest.TestCase):
    def test_lifecycle_refusals_keep_the_coordinator_stage(self) -> None:
        # session_authorized fails inside _authorize; session_rejected inside
        # _reject_reserved, after the missing run is refused.
        for failing in ("session_authorized", "session_rejected"):
            with self.subTest(failing=failing), LifecycleFixtureContext() as fixture:
                fixture.audit.fail_events.add(failing)
                result = fixture.lifecycle.reap_dead_run(
                    IDENTITY, fixture.token, "missing-run"
                )
                self.assertEqual(
                    (
                        result.get("error"),
                        result.get("audit_stage"),
                        result.get("_http_status"),
                    ),
                    ("audit_failed", "write", 503),
                )


def _client_runtime() -> server.ClientRuntime:
    return _fixture_client_runtime(
        ServerConfig(
            mode="client",
            key="test-key",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
    )


class ControlClientAuditStageTest(unittest.TestCase):
    def _raised(
        self, runtime: server.ClientRuntime, status: int, document: dict[str, object]
    ) -> ControlClientError:
        control = runtime._control

        def request_with_refresh(**_kwargs: object) -> tuple[int, bytes]:
            return status, json.dumps(document).encode("utf-8")

        control._credential_provider.request_with_refresh = request_with_refresh
        with patch.object(type(control.policy), "revalidate", return_value=None):
            with self.assertRaises(ControlClientError) as raised:
                control._request_once("/session/wait", {"ticket": "t"}, 5.0)
        return raised.exception

    def test_only_a_closed_stage_of_an_audit_failed_is_kept(self) -> None:
        runtime = _client_runtime()
        kept = self._raised(
            runtime, 503, {"error": "audit_failed", "audit_stage": "wal_arm"}
        )
        self.assertEqual((kept.code, kept.audit_stage), ("audit_failed", "wal_arm"))
        self.assertEqual(str(kept), "audit_failed")
        for bad in ("C:\\Users\\someone\\audit", "WRITE", "", 7, None, ["write"]):
            with self.subTest(bad=bad):
                dropped = self._raised(
                    runtime, 503, {"error": "audit_failed", "audit_stage": bad}
                )
                self.assertIsNone(dropped.audit_stage)
        other = self._raised(
            runtime, 409, {"error": "lease_invalid", "audit_stage": "write"}
        )
        self.assertIsNone(other.audit_stage)


class PublicAuditStageTextTest(unittest.IsolatedAsyncioTestCase):
    def test_an_enqueue_refusal_names_the_stage_and_the_next_step(self) -> None:
        self.assertEqual(
            server._public_enqueue_error(
                {"error": "audit_failed", "audit_stage": "write"}
            ),
            "audit_failed; audit_stage=write; next_step=session_status",
        )
        # No known stage (the exec_enforce audit, an older daemon, a forged
        # value): the text stays what it was.
        for payload in (
            {"error": "audit_failed"},
            {"error": "audit_failed", "audit_stage": "C:\\Users\\someone"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(server._public_enqueue_error(payload), "audit_failed")

    async def test_a_mutation_refusal_reaches_the_caller_with_its_stage(self) -> None:
        runtime = _client_runtime()
        runtime._call = lambda *_a, **_k: (
            503,
            {"error": "audit_failed", "audit_stage": "write"},
        )
        with self.assertRaises(server.ToolError) as raised:
            await runtime.call_bridge("world_spawn", dict(_SPAWN_ARGS), "server", 1.0)
        self.assertEqual(
            str(raised.exception),
            "audit_failed; audit_stage=write; next_step=session_status",
        )

    async def _acquire_wait_error(
        self, enqueue: tuple[int, dict], wait: tuple[int, dict] | None = None
    ) -> str:
        runtime = _client_runtime()

        def request_with_refresh(
            *,
            method: str,
            path: str,
            query: dict[str, str],
            body: bytes | None,
            headers: dict[str, str],
            deadline: float,
        ) -> tuple[int, bytes]:
            payload = json.loads((body or b"{}").decode("utf-8"))
            if path == "/session/enqueue":
                status, document = enqueue
                if status == 202:
                    document = dict(document, operation_id=payload.get("operation_id"))
                return status, json.dumps(document).encode("utf-8")
            if path == "/session/wait" and wait is not None:
                return wait[0], json.dumps(wait[1]).encode("utf-8")
            if path == "/session/cancel-operation":
                return 200, json.dumps(
                    {"cancelled": True, "operation_id": payload.get("operation_id")}
                ).encode("utf-8")
            raise AssertionError(path)

        runtime._control._credential_provider.request_with_refresh = (
            request_with_refresh
        )
        with patch.object(
            type(runtime._control.policy), "revalidate", return_value=None
        ):
            with self.assertRaises(server.ToolError) as raised:
                await runtime.session_acquire_wait("build", 1.0)
        return str(raised.exception)

    async def test_session_acquire_wait_names_the_stage_of_a_refused_enqueue(self) -> None:
        message = await self._acquire_wait_error(
            (503, {"error": "audit_failed", "audit_stage": "write"})
        )
        self.assertEqual(
            message, "audit_failed; audit_stage=write; next_step=session_status"
        )

    async def test_session_acquire_wait_names_the_stage_of_a_refused_wait(self) -> None:
        message = await self._acquire_wait_error(
            (202, {"status": "queued", "ticket": "ticket-a", "position": 1}),
            (503, {"error": "audit_failed", "audit_stage": "handoff_latched"}),
        )
        self.assertEqual(
            message,
            "audit_failed; audit_stage=handoff_latched; next_step=session_status",
        )

    async def test_session_acquire_wait_echoes_no_unknown_stage(self) -> None:
        message = await self._acquire_wait_error(
            (503, {"error": "audit_failed", "audit_stage": "C:\\Users\\someone"})
        )
        self.assertEqual(message, "audit_failed")


class AuditStageHttpWireTest(SessionLoopback, unittest.TestCase):
    def test_the_daemon_puts_the_stage_on_the_wire(self) -> None:
        self.audit_fails = True
        self.assertEqual(
            _stage(
                self.request(
                    "/session/acquire",
                    {"identity": IDENTITY_PAYLOAD, "purpose": "denied"},
                )
            ),
            (503, "audit_failed", "write"),
        )

        self.audit_fails = False
        status, acquired = self.request(
            "/session/acquire", {"identity": IDENTITY_PAYLOAD, "purpose": "drive"}
        )
        self.assertEqual(status, 200)
        self.audit_fails = True
        self.assertEqual(
            _stage(
                self.request(
                    "/enqueue",
                    {
                        "identity": IDENTITY_PAYLOAD,
                        "lease_token": acquired["lease_token"],
                        "cmd": "world_spawn",
                        "args": dict(_SPAWN_ARGS),
                        "peer": "server",
                    },
                )
            ),
            (503, "audit_failed", "write"),
        )


if __name__ == "__main__":
    unittest.main()
