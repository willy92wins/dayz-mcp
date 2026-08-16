from __future__ import annotations

import unittest
import threading
from pathlib import Path

from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.test_session_coordination import AuditSink, FakeClock, SequentialIds, _identity


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRODUCT_SPEC = _REPO_ROOT / "product-spec.md"


def parse_dpf_table(markdown: str, heading: str) -> dict[str, dict[str, str]]:
    """Return the named DPF table as structured criterion records."""

    lines = markdown.splitlines()
    try:
        start = lines.index(heading)
    except ValueError as error:
        raise AssertionError(f"missing DPF heading: {heading}") from error

    header_index = start + 1
    while header_index < len(lines) and not lines[header_index].startswith("| # |"):
        header_index += 1
    if header_index == len(lines):
        raise AssertionError(f"missing DPF table after: {heading}")

    rows: dict[str, dict[str, str]] = {}
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise AssertionError(f"malformed DPF row: {line}")
        key, criterion, verification, state = cells
        if key in rows:
            raise AssertionError(f"duplicate DPF criterion: {key}")
        rows[key] = {
            "criterion": criterion,
            "verification": verification,
            "state": state,
        }
    return rows


def extract_markdown_segment(markdown: str, start_marker: str, end_marker: str) -> str:
    """Extract one exact Markdown block delimited by structural markers."""

    start = markdown.find(start_marker)
    if start < 0:
        raise AssertionError(f"missing start marker: {start_marker}")
    end = markdown.find(end_marker, start)
    if end < 0:
        raise AssertionError(f"missing end marker: {end_marker}")
    return markdown[start:end].strip()


_H8_HISTORY = """**Excepción de ejecución aprobada 2026-07-16:** mientras no haya créditos Claude, el usuario
autoriza sustituir el reparto 2+2 de H8 por **4 sesiones Codex fresh**. Para acreditar el gate
funcional deben conservar cuatro `session_id` y PID distintos, una misma `daemon_generation`,
la secuencia completa y el cierre limpio. La evidencia se etiqueta `4-Codex`; esta excepción no
convierte en verificación in-game la mitad Claude de H1.

Estado de cierre 2026-07-16: H8 funcional pasó en una ejecución real de 4 sesiones Codex fresh,
con una generación compartida, FIFO/TTL/lifecycle completos y cierre limpio. El doctor final quedó
sin findings y no quedaron procesos DayZ ni UDP 2302. Evidencia:
`reviews/2026-07-16-h8-real-4-codex.json` (SHA256
`E49A4A224EE782C99C86C5D71F8DEE8EB502213B94683D619AF3BF2F26CD873A`). La configuración
efectiva mixta 2 Claude + 2 Codex de H1 sigue sin verificación in-game."""


class Bug046DpfContractTests(unittest.TestCase):
    def test_h4_h9_contract_and_h8_history_are_structurally_preserved(self) -> None:
        markdown = _PRODUCT_SPEC.read_text(encoding="utf-8")
        rows = parse_dpf_table(markdown, "### H — Coordinación segura de sesiones de agentes")

        self.assertEqual(
            rows["H4"],
            {
                "criterion": "Cola FIFO estricta, `acquire` idempotente, TTL exacto 120 s y promoción sólo con `session_wait` vivo",
                "verification": "A→B→C, ticket duplicado no duplica posición, reloj inyectable 119/120/121 s; release/expiry sin `session_wait` vivo no conceden leases",
                "state": "✓ offline",
            },
        )
        self.assertEqual(
            rows["H9"],
            {
                "criterion": "Adquisición en espera request-bound y liveness de cola",
                "verification": "`session_acquire_wait` request-bound nunca devuelve `queued`; timeout/cancel no deja ticket/lease oculto; sólo `session_wait` vivo promueve cabeza; release/expiry no hacen grant ciego; launcher nativo/neutral respecto al consumidor, registrado (path+SHA, sin identidad/token), no autoriza PowerShell ni `.ps1`; gate local `fifo_grants_without_live_wait=0`; gate real 2 Claude+2 Codex abierto hasta proveniencia externa",
                "state": "[verify] offline ✓; falta gate real 2 Claude + 2 Codex",
            },
        )
        self.assertEqual(
            rows["H8"],
            {
                "criterion": "Gate combinado real sobre una caja y un juego",
                "verification": "2 Claude + 2 Codex: lecturas paralelas, mutaciones FIFO, release, expiry por caída, adopción/reemplazo seguro y estado final limpio",
                "state": "✓ in-game — sustitución 4-Codex aprobada",
            },
        )
        self.assertEqual(
            extract_markdown_segment(
                markdown,
                "**Excepción de ejecución aprobada 2026-07-16:**",
                "Aceptación detallada:",
            ),
            _H8_HISTORY,
        )

    def test_parse_dpf_table_rejects_duplicate_ids(self) -> None:
        duplicate_table = "\n".join(
            (
                "### Test",
                "| # | Criterio | Cómo se verifica | Estado |",
                "|---|---|---|---|",
                "| H4 | first | check | ❌ |",
                "| H4 | second | check | ❌ |",
            )
        )
        with self.assertRaisesRegex(AssertionError, "^duplicate DPF criterion: H4$"):
            parse_dpf_table(duplicate_table, "### Test")


class Bug046QueueLivenessRedTests(unittest.TestCase):
    def test_v1_release_without_live_wait_keeps_fifo_head_queued_and_grants_nothing(self) -> None:
        clock = FakeClock()
        audit = AuditSink()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b, c = _identity("a"), _identity("b"), _identity("c")

        active = coordinator.acquire(a, "drive")
        b_ticket = coordinator.acquire(b, "camera")
        c_ticket = coordinator.acquire(c, "weather")
        self.assertEqual(
            (
                active[0],
                active[1]["status"],
                b_ticket[0],
                b_ticket[1]["status"],
                b_ticket[1]["position"],
                c_ticket[0],
                c_ticket[1]["status"],
                c_ticket[1]["position"],
            ),
            (200, "active", 202, "queued", 1, 202, "queued", 2),
        )
        grants_before_release = len(
            [event for event in audit.events if event["event"] == "session_granted"]
        )

        self.assertEqual(coordinator.release(a, active[1]["lease_token"])[0], 200)
        snapshot = coordinator.snapshot_payload()
        observed = {
            "active": snapshot["active"],
            "b_position": coordinator.status(b)["self"]["position"],
            "new_grants": len(
                [event for event in audit.events if event["event"] == "session_granted"]
            )
            - grants_before_release,
        }
        self.assertEqual(
            observed,
            {"active": None, "b_position": 1, "new_grants": 0},
            "V1 requires release to leave the FIFO head queued until a live session_wait promotes it",
        )
        self.assertEqual(b_ticket[1]["position"], 1)

    def test_v2_live_head_wait_claims_once_and_leaves_next_ticket_at_position_one(self) -> None:
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b, c = _identity("a"), _identity("b"), _identity("c")
        active = coordinator.acquire(a, "drive")[1]
        b_ticket = coordinator.acquire(b, "camera")[1]
        coordinator.acquire(c, "weather")

        coordinator.release(a, active["lease_token"])
        claimed = coordinator.wait(b, b_ticket["ticket"], 0.0)
        repeated = coordinator.wait(b, b_ticket["ticket"], 0.0)

        fifo_prepared = [
            event
            for event in events
            if event.get("event") == "session_grant_prepared"
            and event.get("reason") == "fifo_head"
        ]
        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        self.assertEqual((claimed[0], claimed[1].get("status")), (200, "active"))
        self.assertEqual(repeated[1]["lease_token"], claimed[1]["lease_token"])
        self.assertEqual(len(fifo_prepared), 1)
        self.assertEqual(len(fifo_commits), 1)
        self.assertLess(events.index(fifo_prepared[0]), events.index(fifo_commits[0]))
        self.assertEqual(coordinator.status(c)["self"]["position"], 1)
        self.assertNotIn(b_ticket["ticket"], [item["ticket"] for item in coordinator.status(c)["queue"]])

    def test_v3_expired_head_never_becomes_lease_and_next_client_claims_only_from_live_wait(self) -> None:
        clock = FakeClock()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b, c = _identity("a"), _identity("b"), _identity("c")
        active = coordinator.acquire(a, "drive")[1]
        b_ticket = coordinator.acquire(b, "camera")[1]
        c_ticket = coordinator.acquire(c, "weather")[1]
        coordinator.release(a, active["lease_token"])

        clock.advance(120.0)
        claimed = coordinator.wait(c, c_ticket["ticket"], 0.0)
        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        cancelled = [
            event
            for event in events
            if event.get("event") == "ticket_cancelled"
        ]

        self.assertEqual((claimed[0], claimed[1].get("status")), (200, "active"))
        self.assertEqual(len(fifo_commits), 1)
        self.assertEqual(fifo_commits[0]["ticket"], c_ticket["ticket"])
        self.assertEqual([event["ticket"] for event in cancelled], [b_ticket["ticket"]])
        self.assertFalse(
            any(event.get("ticket") == b_ticket["ticket"] for event in fifo_commits)
        )

    def test_v4_two_concurrent_waits_publish_at_most_one_fifo_lease_and_commit(self) -> None:
        prepared_entered = threading.Event()
        allow_prepared = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_grant_prepared"
                and event.get("reason") == "fifo_head"
            ):
                prepared_entered.set()
                allow_prepared.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])
        results: list[tuple[int, dict]] = []
        first = threading.Thread(
            target=lambda: results.append(coordinator.wait(b, ticket["ticket"], 0.0))
        )
        second = threading.Thread(
            target=lambda: results.append(coordinator.wait(b, ticket["ticket"], 0.0))
        )

        first.start()
        self.assertTrue(prepared_entered.wait(1.0))
        second.start()
        second.join(1.0)
        self.assertFalse(second.is_alive())
        allow_prepared.set()
        first.join(2.0)
        self.assertFalse(first.is_alive())

        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        active_results = [body for status, body in results if status == 200]
        self.assertEqual(sorted(status for status, _body in results), [200, 202])
        self.assertEqual(len(active_results), 1)
        self.assertEqual(len({body["lease_token"] for body in active_results}), 1)
        self.assertEqual(len(fifo_commits), 1)
        self.assertEqual(
            coordinator.snapshot_payload()["active"]["lease_id"],
            fifo_commits[0]["lease_id"],
        )

    def test_v5_prepared_audit_false_or_exception_keeps_exact_head_without_token(self) -> None:
        for failure_mode in ("false", "exception"):
            with self.subTest(failure_mode=failure_mode):
                events: list[dict[str, object]] = []

                def audit(event: dict[str, object]) -> bool:
                    events.append(dict(event))
                    if (
                        event.get("event") == "session_grant_prepared"
                        and event.get("reason") == "fifo_head"
                    ):
                        if failure_mode == "exception":
                            raise OSError("prepared unavailable")
                        return False
                    return True

                coordinator = SessionCoordinator(
                    token_fn=SequentialIds("secret-token"),
                    id_fn=SequentialIds("id"),
                    audit=audit,
                )
                a, b = _identity("a"), _identity("b")
                active = coordinator.acquire(a, "drive")[1]
                ticket = coordinator.acquire(b, "camera")[1]
                coordinator.release(a, active["lease_token"])

                result = coordinator.wait(b, ticket["ticket"], 0.0)
                snapshot = coordinator.snapshot_payload()
                fifo_commits = [
                    event
                    for event in events
                    if event.get("event") == "session_granted"
                    and event.get("reason") == "fifo_head"
                ]
                self.assertEqual((result[0], result[1].get("error")), (503, "audit_failed"))
                self.assertIsNone(snapshot["active"])
                self.assertIsNone(snapshot["granting"])
                self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])
                self.assertEqual(fifo_commits, [])
                self.assertNotIn("secret-token", repr((events, result, snapshot)))

    def test_v5_busy_audit_gate_returns_queued_progress_without_audit_failure(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=lambda _event: True,
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])

        self.assertTrue(coordinator._audit_gate.acquire(blocking=False))
        busy_results: list[tuple[int, dict]] = []
        busy_thread = threading.Thread(
            target=lambda: busy_results.append(
                coordinator.wait(b, ticket["ticket"], 0.0)
            )
        )
        try:
            busy_thread.start()
            busy_thread.join(0.20)
            bounded = not busy_thread.is_alive()
        finally:
            coordinator._audit_gate.release()
            busy_thread.join(1.0)
        self.assertTrue(bounded, "claim_wait_blocked_behind_busy_audit_gate")
        busy = busy_results[0]
        claimed = coordinator.wait(b, ticket["ticket"], 0.0)

        self.assertEqual((busy[0], busy[1]["status"]), (202, "queued"))
        self.assertNotIn("cleanup_degraded", busy[1])
        self.assertNotIn("lease_token", busy[1])
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))

    def test_v5b_commit_failure_or_uncertain_append_is_compensated_without_hidden_active(self) -> None:
        for failure_mode, expected_commits in (("false", 0), ("append_then_raise", 1)):
            with self.subTest(failure_mode=failure_mode):
                events: list[dict[str, object]] = []

                def audit(event: dict[str, object]) -> bool:
                    is_commit = (
                        event.get("event") == "session_granted"
                        and event.get("reason") == "fifo_head"
                    )
                    if is_commit and failure_mode == "false":
                        return False
                    events.append(dict(event))
                    if is_commit and failure_mode == "append_then_raise":
                        raise OSError("append outcome uncertain")
                    return True

                coordinator = SessionCoordinator(
                    token_fn=SequentialIds("secret-token"),
                    id_fn=SequentialIds("id"),
                    audit=audit,
                )
                a, b = _identity("a"), _identity("b")
                active = coordinator.acquire(a, "drive")[1]
                ticket = coordinator.acquire(b, "camera")[1]
                coordinator.release(a, active["lease_token"])

                result = coordinator.wait(b, ticket["ticket"], 0.0)
                snapshot = coordinator.snapshot_payload()
                commits = [
                    event
                    for event in events
                    if event.get("event") == "session_granted"
                    and event.get("reason") == "fifo_head"
                ]
                revoked = [
                    event
                    for event in events
                    if event.get("event") == "session_grant_revoked"
                ]
                self.assertEqual((result[0], result[1].get("error")), (503, "audit_failed"))
                self.assertEqual(len(commits), expected_commits)
                self.assertEqual(len(revoked), 1)
                self.assertIsNone(snapshot["active"])
                self.assertIsNone(snapshot["granting"])
                self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])
                self.assertNotIn("secret-token", repr((events, result, snapshot)))

    def test_v5e_nonconfirmable_fifo_compensation_latches_after_last_ticket_expires(self) -> None:
        clock = FakeClock()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                raise OSError("commit append outcome uncertain")
            if event.get("event") == "session_grant_revoked":
                raise OSError("revocation append outcome uncertain")
            return True

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("secret-token"),
            id_fn=SequentialIds("id"),
            audit=audit,
        )
        a, b, c = _identity("a"), _identity("b"), _identity("c")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])

        failed_claim = coordinator.wait(b, ticket["ticket"], 0.0)
        clock.advance(120.0)
        expired_status = coordinator.status(b)
        retried_acquire = coordinator.acquire(c, "inspect")
        snapshot = coordinator.snapshot_payload()

        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        revocations = [
            event
            for event in events
            if event.get("event") == "session_grant_revoked"
        ]
        self.assertEqual(
            (failed_claim[0], failed_claim[1].get("error")),
            (503, "audit_failed"),
        )
        self.assertEqual(len(fifo_commits), 1)
        self.assertEqual(len(revocations), 1)
        self.assertEqual(expired_status["queue"], [])
        self.assertEqual(
            (retried_acquire[0], retried_acquire[1].get("error")),
            (503, "audit_failed"),
        )
        self.assertFalse(snapshot["claimable"])
        self.assertIsNone(snapshot["active"])
        self.assertIsNone(snapshot["granting"])
        self.assertNotIn("secret-token", repr((failed_claim, retried_acquire, snapshot)))

    def test_v5c_coordination_change_after_prepared_never_writes_authoritative_commit(self) -> None:
        events: list[dict[str, object]] = []
        coordinator: SessionCoordinator

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if event.get("event") == "session_grant_prepared":
                with coordinator._condition:
                    coordinator._handoff_pending = True
                    coordinator._bump_revision_locked()
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"), id_fn=SequentialIds("id"), audit=audit
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])

        result = coordinator.wait(b, ticket["ticket"], 0.0)
        fifo_commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        self.assertEqual((result[0], result[1].get("error")), (409, "coordination_changed"))
        self.assertEqual(fifo_commits, [])
        self.assertIsNone(coordinator.snapshot_payload()["active"])
        self.assertEqual(coordinator.snapshot_payload()["queue"][0]["ticket"], ticket["ticket"])

    def test_v5c_coordination_change_after_commit_writes_one_revocation_and_publishes_nothing(self) -> None:
        events: list[dict[str, object]] = []
        coordinator: SessionCoordinator

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if (
                event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ):
                with coordinator._condition:
                    coordinator._handoff_pending = True
                    coordinator._bump_revision_locked()
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"), id_fn=SequentialIds("id"), audit=audit
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        coordinator.release(a, active["lease_token"])

        result = coordinator.wait(b, ticket["ticket"], 0.0)
        commits = [
            event
            for event in events
            if event.get("event") == "session_granted"
            and event.get("reason") == "fifo_head"
        ]
        revoked = [event for event in events if event.get("event") == "session_grant_revoked"]
        snapshot = coordinator.snapshot_payload()
        self.assertEqual((result[0], result[1].get("error")), (409, "coordination_changed"))
        self.assertEqual(len(commits), 1)
        self.assertEqual(len(revoked), 1)
        self.assertEqual(commits[0]["lease_id"], revoked[0]["lease_id"])
        self.assertIsNone(snapshot["active"])
        self.assertIsNone(snapshot["granting"])
        self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])

    def test_v20_stalled_release_audit_keeps_fence_until_late_success_then_live_wait_claims(self) -> None:
        release_started = threading.Event()
        allow_release_audit = threading.Event()
        release_returned = threading.Event()
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            if event.get("event") == "session_release_started":
                release_started.set()
                allow_release_audit.wait(2.0)
            return True

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
            cleanup=lambda *_args: {},
            cleanup_timeout_s=0.02,
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]
        release_result: list[tuple[int, dict]] = []
        release_thread = threading.Thread(
            target=lambda: (
                release_result.append(coordinator.release(a, active["lease_token"])),
                release_returned.set(),
            )
        )
        release_thread.start()
        self.assertTrue(release_started.wait(1.0))
        self.assertTrue(release_returned.wait(1.0))

        during = coordinator.wait(b, ticket["ticket"], 0.0)
        during_snapshot = coordinator.snapshot_payload()
        self.assertEqual((during[0], during[1].get("status")), (202, "queued"))
        self.assertTrue(during_snapshot["handoff_pending"])
        self.assertIsNone(during_snapshot["active"])
        self.assertFalse(
            any(event.get("event") == "session_grant_prepared" for event in events)
        )

        allow_release_audit.set()
        with coordinator._condition:
            terminalized = coordinator._condition.wait_for(
                lambda: not coordinator._handoff_pending, timeout=1.0
            )
        release_thread.join(1.0)
        self.assertTrue(terminalized)
        claimed = coordinator.wait(b, ticket["ticket"], 0.0)
        names = [str(event.get("event")) for event in events]
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))
        self.assertLess(names.index("session_release_finished"), names.index("session_grant_prepared"))

    def test_v20_failed_release_terminal_audit_remains_visible_and_blocks_live_wait(self) -> None:
        events: list[dict[str, object]] = []

        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return event.get("event") != "session_release_finished"

        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=audit,
            cleanup=lambda *_args: {},
        )
        a, b = _identity("a"), _identity("b")
        active = coordinator.acquire(a, "drive")[1]
        ticket = coordinator.acquire(b, "camera")[1]

        release = coordinator.release(a, active["lease_token"])
        blocked = coordinator.wait(b, ticket["ticket"], 0.0)
        snapshot = coordinator.snapshot_payload()

        self.assertIn("audit_failed", release[1]["cleanup_degraded"])
        self.assertEqual((blocked[0], blocked[1].get("error")), (503, "audit_failed"))
        self.assertTrue(snapshot["handoff_pending"])
        self.assertIsNone(snapshot["active"])
        self.assertEqual(snapshot["queue"][0]["ticket"], ticket["ticket"])
        self.assertFalse(
            any(
                event.get("event") in {"session_grant_prepared", "session_granted"}
                and event.get("reason") == "fifo_head"
                for event in events
            )
        )


class _BlockingWalBoundary:
    """Deterministic WAL callbacks for grant-fence concurrency tests."""

    def __init__(self, boundary: str | None = None) -> None:
        self.boundary = boundary
        self.entered = threading.Event()
        self.resume = threading.Event()
        self.events: list[dict[str, object]] = []
        self.snapshots: list[dict[str, object]] = []
        self._blocked = False
        self._sha_index = 0

    def _next_sha(self) -> str:
        self._sha_index += 1
        return f"sha-{self._sha_index}"

    def _block_once(self, boundary: str) -> None:
        if self.boundary != boundary or self._blocked:
            return
        self._blocked = True
        self.entered.set()
        if not self.resume.wait(2.0):
            raise TimeoutError(f"boundary_not_released:{boundary}")

    def arm(self, marker: dict[str, object]) -> str:
        self.events.append({"callback": "arm", **dict(marker)})
        return self._next_sha()

    def transition(
        self, _fault_id: str, _expected_sha256: str, **kwargs: object
    ) -> str:
        if kwargs.get("state") == "completed":
            boundary = "completed_transition"
        else:
            boundary = "revision_transition"
        self.events.append({"callback": boundary, **kwargs})
        self._block_once(boundary)
        return self._next_sha()

    def persist(self, snapshot: dict[str, object]) -> bool:
        self.snapshots.append(snapshot)
        self._block_once("snapshot")
        return True

    def clear(self, _fault_id: str, _expected_sha256: str) -> bool:
        self.events.append({"callback": "clear"})
        self._block_once("clear")
        return True


class Bug046GrantAuthorityFenceTests(unittest.TestCase):
    _BOUNDARIES = (
        "revision_transition",
        "snapshot",
        "completed_transition",
        "clear",
    )

    @staticmethod
    def _coordinator(
        wal: _BlockingWalBoundary,
        events: list[dict[str, object]],
    ) -> SessionCoordinator:
        def audit(event: dict[str, object]) -> bool:
            events.append(dict(event))
            return True

        return SessionCoordinator(
            token_fn=SequentialIds("secret-token"),
            id_fn=SequentialIds("lease"),
            audit=audit,
            fault_id_fn=SequentialIds("fault"),
            fault_arm=wal.arm,
            fault_transition=wal.transition,
            fault_clear=wal.clear,
            persist_snapshot=wal.persist,
        )

    def test_admin_release_never_confirms_a_provisional_grant_at_any_wal_boundary(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal = _BlockingWalBoundary(boundary)
                events: list[dict[str, object]] = []
                coordinator = self._coordinator(wal, events)
                client = _identity("owner")
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.acquire(client, "authority-fence", "operation-owner")
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                during = coordinator.snapshot_payload()
                self.assertIsNone(during["active"])
                self.assertIsNotNone(during["granting"])
                self.assertNotIn("secret-token", repr(during))
                status = coordinator.status(client)
                read = coordinator.authorize(client, None, "telemetry_read")
                self.assertIsNone(status["owner"])
                self.assertIsNone(read.owner_session_id)
                try:
                    released = coordinator.admin_release(
                        str(during["granting"]["lease_id"]), "operator"
                    )
                finally:
                    wal.resume.set()
                    grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertEqual(
                    (released[0], released[1].get("error")),
                    (409, "session_granting"),
                )
                self.assertEqual((result[0][0], result[0][1]["status"]), (200, "active"))
                self.assertFalse(
                    any(event.get("event") == "admin_release" for event in events)
                )

    def test_revision_or_queue_drift_at_any_wal_boundary_revokes_without_token(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal = _BlockingWalBoundary(boundary)
                events: list[dict[str, object]] = []
                coordinator = self._coordinator(wal, events)
                owner, waiter = _identity("owner"), _identity("waiter")
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.acquire(owner, "authority-fence", "operation-owner")
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                queued = coordinator.acquire(waiter, "queued-drift", "operation-waiter")
                self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
                wal.resume.set()
                grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertEqual(
                    (result[0][0], result[0][1].get("error")),
                    (409, "coordination_changed"),
                )
                settled = coordinator.snapshot_payload()
                self.assertIsNone(settled["active"])
                self.assertIsNone(settled["granting"])
                self.assertEqual(settled["queue"][0]["session"], waiter.session_id[:12])
                self.assertNotIn("secret-token", repr((result, settled)))
                revoked = [
                    event
                    for event in events
                    if event.get("event") == "session_grant_revoked"
                ]
                self.assertEqual(len(revoked), 1, events)
                self.assertTrue(
                    any(
                        event.get("failure") == "coordination_changed"
                        for event in wal.events
                        if event.get("callback")
                        in {"revision_transition", "completed_transition"}
                    ),
                    wal.events,
                )

    def test_cancel_at_any_wal_boundary_is_revisioned_and_durably_revoked(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal = _BlockingWalBoundary(boundary)
                events: list[dict[str, object]] = []
                coordinator = self._coordinator(wal, events)
                owner = _identity("owner")
                operation_id = "operation-owner"
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.acquire(
                            owner, "authority-fence", operation_id
                        )
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                revision_before_cancel = coordinator.snapshot_payload()["revision"]
                cancelled = coordinator.cancel_operation(owner, operation_id)
                revision_after_cancel = coordinator.snapshot_payload()["revision"]
                wal.resume.set()
                grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertGreater(revision_after_cancel, revision_before_cancel)
                self.assertIn(cancelled[0], {200, 202})
                self.assertEqual(
                    (result[0][0], result[0][1].get("error")),
                    (409, "operation_cancelled"),
                )
                settled = coordinator.snapshot_payload()
                self.assertIsNone(settled["active"])
                self.assertIsNone(settled["granting"])
                self.assertNotIn("secret-token", repr((result, settled)))
                revoked = [
                    event
                    for event in events
                    if event.get("event") == "session_grant_revoked"
                ]
                self.assertEqual(len(revoked), 1, events)
                self.assertTrue(
                    any(
                        event.get("failure") == "coordination_changed"
                        for event in wal.events
                        if event.get("callback")
                        in {"revision_transition", "completed_transition"}
                    ),
                    wal.events,
                )

    def test_pending_wal_is_not_claimable_while_next_acquire_queues(self) -> None:
        wal = _BlockingWalBoundary()
        events: list[dict[str, object]] = []
        coordinator = self._coordinator(wal, events)
        with coordinator._condition:
            coordinator._wal_marker = {
                "fault_id": "fault-open",
                "state": "armed",
                "phase": "coordination_changed",
            }
            coordinator._wal_sha256 = "sha-open"

        self.assertFalse(coordinator.status(_identity("observer"))["claimable"])
        queued = coordinator.acquire(
            _identity("next"), "wait-for-compensation", "operation-next"
        )
        self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
        self.assertFalse(
            any(
                event.get("event") in {"session_grant_prepared", "session_granted"}
                for event in events
            )
        )

    def _fifo_fixture(
        self, boundary: str
    ) -> tuple[
        _BlockingWalBoundary,
        list[dict[str, object]],
        SessionCoordinator,
        ClientIdentity,
        dict,
    ]:
        wal = _BlockingWalBoundary()
        events: list[dict[str, object]] = []
        coordinator = self._coordinator(wal, events)
        owner, waiter = _identity("owner"), _identity("waiter")
        active = coordinator.acquire(owner, "owner", "operation-owner")[1]
        ticket = coordinator.acquire(waiter, "waiter", "operation-waiter")[1]
        self.assertEqual(coordinator.release(owner, active["lease_token"])[0], 200)
        with coordinator._condition:
            self.assertTrue(
                coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=1.0
                )
            )
        wal.boundary = boundary
        wal.entered.clear()
        wal.resume.clear()
        wal._blocked = False
        return wal, events, coordinator, waiter, ticket

    def test_fifo_admin_release_rejects_each_provisional_wal_boundary(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal, events, coordinator, waiter, ticket = self._fifo_fixture(boundary)
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.wait(waiter, ticket["ticket"], 0.0)
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                granting = coordinator.snapshot_payload()["granting"]
                try:
                    released = coordinator.admin_release(
                        str(granting["lease_id"]), "operator"
                    )
                finally:
                    wal.resume.set()
                    grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertEqual(
                    (released[0], released[1].get("error")),
                    (409, "session_granting"),
                )
                self.assertEqual((result[0][0], result[0][1]["status"]), (200, "active"))
                self.assertFalse(
                    any(event.get("event") == "admin_release" for event in events)
                )

    def test_duplicate_wait_during_each_wal_boundary_does_not_revoke_grant(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal, _events, coordinator, waiter, ticket = self._fifo_fixture(boundary)
                first_result: list[tuple[int, dict]] = []
                first_wait = threading.Thread(
                    target=lambda: first_result.append(
                        coordinator.wait(waiter, ticket["ticket"], 0.0)
                    )
                )
                first_wait.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)

                revision_before_duplicate = coordinator.snapshot_payload()["revision"]
                duplicate = coordinator.wait(waiter, ticket["ticket"], 0.0)
                revision_after_duplicate = coordinator.snapshot_payload()["revision"]
                wal.resume.set()
                first_wait.join(2.0)

                self.assertFalse(first_wait.is_alive(), boundary)
                self.assertEqual(
                    (duplicate[0], duplicate[1]["status"]),
                    (202, "queued"),
                )
                self.assertEqual(revision_after_duplicate, revision_before_duplicate)
                self.assertEqual(
                    (first_result[0][0], first_result[0][1]["status"]),
                    (200, "active"),
                )

    def test_fifo_queue_drift_at_each_wal_boundary_requeues_exact_head(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal, events, coordinator, waiter, ticket = self._fifo_fixture(boundary)
                next_waiter = _identity("next")
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.wait(waiter, ticket["ticket"], 0.0)
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                queued_results: list[tuple[int, dict]] = []
                queue_thread = threading.Thread(
                    target=lambda: queued_results.append(
                        coordinator.acquire(
                            next_waiter, "next", "operation-next"
                        )
                    )
                )
                queue_thread.start()
                with coordinator._condition:
                    self.assertTrue(
                        coordinator._condition.wait_for(
                            lambda: bool(coordinator._queue_reservations),
                            timeout=1.0,
                        )
                    )
                wal.resume.set()
                grant_thread.join(2.0)
                queue_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertFalse(queue_thread.is_alive(), boundary)
                queued = queued_results[0]
                self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
                self.assertEqual(
                    (result[0][0], result[0][1].get("error")),
                    (409, "coordination_changed"),
                )
                settled = coordinator.snapshot_payload()
                self.assertIsNone(settled["active"])
                self.assertIsNone(settled["granting"])
                self.assertEqual(
                    [item["ticket"] for item in settled["queue"]],
                    [ticket["ticket"], queued[1]["ticket"]],
                )
                self.assertEqual(
                    len(
                        [
                            event
                            for event in events
                            if event.get("event") == "session_grant_revoked"
                            and event.get("reason") == "coordination_changed"
                        ]
                    ),
                    1,
                )

    def test_fifo_cancel_at_each_wal_boundary_never_requeues_or_returns_token(self) -> None:
        for boundary in self._BOUNDARIES:
            with self.subTest(boundary=boundary):
                wal, events, coordinator, waiter, ticket = self._fifo_fixture(boundary)
                result: list[tuple[int, dict]] = []
                grant_thread = threading.Thread(
                    target=lambda: result.append(
                        coordinator.wait(waiter, ticket["ticket"], 0.0)
                    )
                )
                grant_thread.start()
                self.assertTrue(wal.entered.wait(1.0), boundary)
                cancelled = coordinator.cancel_operation(
                    waiter, "operation-waiter"
                )
                wal.resume.set()
                grant_thread.join(2.0)

                self.assertFalse(grant_thread.is_alive(), boundary)
                self.assertIn(cancelled[0], {200, 202})
                self.assertEqual(
                    (result[0][0], result[0][1].get("error")),
                    (409, "operation_cancelled"),
                )
                settled = coordinator.snapshot_payload()
                self.assertIsNone(settled["active"])
                self.assertIsNone(settled["granting"])
                self.assertEqual(settled["queue"], [])
                self.assertNotIn("secret-token", repr((result, settled)))
                self.assertEqual(
                    len(
                        [
                            event
                            for event in events
                            if event.get("event") == "session_grant_revoked"
                            and event.get("reason") == "operation_cancelled"
                        ]
                    ),
                    1,
                )

    def test_tombstone_create_and_refresh_each_advance_revision(self) -> None:
        coordinator = SessionCoordinator()
        client = _identity("owner")
        revision_0 = coordinator.snapshot_payload()["revision"]
        first = coordinator.cancel_operation(client, "operation-owner")
        revision_1 = coordinator.snapshot_payload()["revision"]
        second = coordinator.cancel_operation(client, "operation-owner")
        revision_2 = coordinator.snapshot_payload()["revision"]

        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 200)
        self.assertGreater(revision_1, revision_0)
        self.assertGreater(revision_2, revision_1)

    def test_snapshots_never_mix_active_and_granting_and_fifo_clear_pending_has_no_token(self) -> None:
        class FailThirdClear(_BlockingWalBoundary):
            def __init__(self) -> None:
                super().__init__()
                self.clear_count = 0

            def clear(self, _fault_id: str, _expected_sha256: str) -> bool:
                self.clear_count += 1
                return self.clear_count < 3

        wal = FailThirdClear()
        events: list[dict[str, object]] = []
        coordinator = self._coordinator(wal, events)
        owner, waiter = _identity("owner"), _identity("waiter")
        active = coordinator.acquire(owner, "owner", "operation-owner")[1]
        ticket = coordinator.acquire(waiter, "waiter", "operation-waiter")[1]
        released = coordinator.release(owner, active["lease_token"])
        self.assertEqual(released[0], 200)
        with coordinator._condition:
            self.assertTrue(
                coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=1.0
                )
            )

        claimed = coordinator.wait(waiter, ticket["ticket"], 0.0)
        public_snapshot = coordinator.snapshot_payload()
        all_snapshots = [*wal.snapshots, public_snapshot]

        self.assertEqual((claimed[0], claimed[1].get("error")), (503, "audit_failed"))
        self.assertTrue(
            all(not (snapshot["active"] and snapshot["granting"]) for snapshot in all_snapshots)
        )
        self.assertIsNone(public_snapshot["active"])
        self.assertNotIn("secret-token", repr((claimed, all_snapshots)))
