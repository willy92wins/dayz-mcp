"""HOLD on #485: pin after the grant, ticket in the start request."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp import native_launcher_transaction, server, shared_box_queue as queue
from dayz_mcp.server import ServerConfig
from tests.box_queue_helpers import (
    BoxQueueFixture,
    box_coordinator as _coordinator,
    box_entry as _entry,
    box_identity as _identity,
)
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json

_LEASE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class _GrantClient:
    """Control client that grants and pins through the real coordinator."""

    def __init__(self, coordinator, session) -> None:
        self.coordinator = coordinator
        self.session = session
        self.events: list[object] = []

    async def session_acquire_wait(
        self, purpose: str, max_wait_s: float | None = None, progress_cb=None
    ) -> dict[str, object]:
        del max_wait_s, progress_cb
        self.events.append("acquire")
        status, payload = self.coordinator.acquire(self.session, purpose)
        if status != 200:
            raise AssertionError(f"acquire failed: {status} {payload}")
        self.lease = payload
        return {
            "status": "active",
            "lease_token": payload["lease_token"],
            "lease_id": payload["lease_id"],
            "client_identity_json": json.dumps(self.session.to_payload()),
        }

    async def bind_next_start_ticket(
        self, ticket: str, *, operation_id: object = None
    ) -> dict[str, object]:
        self.events.append(("pin", ticket, operation_id))
        self.coordinator.bind_next_start_ticket(
            self.session, ticket, operation_id=operation_id
        )
        return {}

    async def session_heartbeat(self, lease_token: str) -> dict[str, object]:
        return {"ok": True, "lease_token": lease_token}

    async def session_release(self, lease_token: str) -> dict[str, object]:
        self.coordinator.release(self.session, lease_token)
        return {"released": True}

    async def session_status(self) -> dict[str, object]:
        return {"self": {"state": "none"}, "pending_commands": 0}


@contextmanager
def _transaction_stubs():
    parsed = SimpleNamespace(
        canonical_bytes=b"{}",
        sha256="a" * 64,
        payload={},
    )

    def parse(*_args, **_kwargs):
        return parsed

    with (
        patch.object(native_launcher_transaction.dayz_test_request, "parse_dayz_test_request", parse),
        patch.object(native_launcher_transaction, "enforce_vpp_preflight", lambda *_a, **_k: None),
        patch.object(
            native_launcher_transaction.request_path_authority,
            "accredit_request_paths",
            lambda *_a, **_k: nullcontext(),
        ),
        patch.object(
            native_launcher_transaction,
            "_accredit_runtime_paths",
            lambda *_a, **_k: nullcontext(),
        ),
    ):
        yield


class TransactionPinAfterGrantTest(BoxQueueFixture, unittest.IsolatedAsyncioTestCase):
    """Pin T after session_acquire_wait, through the real coordinator, before start_run."""

    async def _run_transaction(self, client, *, box_ticket: str | None, start):
        async def consumer(**_kwargs: object) -> int:
            client.events.append("start")
            start()
            return 0

        kwargs: dict[str, object] = {}
        if box_ticket is not None:
            kwargs["box_ticket"] = box_ticket
        with _transaction_stubs():
            return await native_launcher_transaction.execute_native_launcher_transaction(
                b"{}",
                sealed_policies=(),
                worker_runtime=lambda *_a, **_k: None,
                control_client=client,
                consumer=consumer,
                **kwargs,
            )

    async def test_new_session_pins_after_acquire_and_admits_before_start(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("new-session")
        claimed = coordinator.box_wait_touch(session, claim=True)
        ticket = claimed["box_ticket"]
        client = _GrantClient(coordinator, session)
        started: dict[str, object] = {}

        def start() -> None:
            started.update(
                lifecycle.start_run(
                    session, client.lease["lease_token"], self._request(game)
                )
            )

        await self._run_transaction(client, box_ticket=ticket, start=start)
        self.assertEqual(client.events[0], "acquire")
        self.assertEqual(client.events[1], ("pin", ticket, client.lease["lease_id"]))
        self.assertEqual(client.events[2], "start")
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)

    async def test_t2_retry_pins_the_new_grant_not_a_leftover_lease(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("retry-session")
        first = coordinator.box_wait_touch(session, claim=True)
        old_lease_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        # Leftover pin from an earlier attempt, keyed to a lease that is gone.
        coordinator.bind_next_start_ticket(
            session, first["box_ticket"], operation_id=old_lease_id
        )
        coordinator.box_wait_touch(session, first["box_ticket"], done=True)
        second = coordinator.box_wait_touch(session, claim=True)
        ticket = second["box_ticket"]
        self.assertNotEqual(ticket, first["box_ticket"])
        client = _GrantClient(coordinator, session)
        started: dict[str, object] = {}

        def start() -> None:
            started.update(
                lifecycle.start_run(
                    session, client.lease["lease_token"], self._request(game)
                )
            )

        await self._run_transaction(client, box_ticket=ticket, start=start)
        self.assertEqual(client.events[0], "acquire")
        self.assertEqual(client.events[1], ("pin", ticket, client.lease["lease_id"]))
        self.assertNotEqual(client.lease["lease_id"], old_lease_id)
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)

    async def test_failed_pin_is_not_swallowed(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        del lifecycle, launcher, game
        session = _identity("pin-fail-session")
        claimed = coordinator.box_wait_touch(session, claim=True)
        ticket = claimed["box_ticket"]
        client = _GrantClient(coordinator, session)

        async def boom(ticket: str, *, operation_id: object = None) -> dict[str, object]:
            client.events.append(("pin", ticket, operation_id))
            raise RuntimeError("pin exploded")

        client.bind_next_start_ticket = boom  # type: ignore[method-assign]

        with self.assertRaises(native_launcher_transaction.NativeLauncherTransactionError) as ctx:
            await self._run_transaction(client, box_ticket=ticket, start=lambda: None)
        self.assertEqual(ctx.exception.args[0], "box_ticket_pin_failed")
        self.assertEqual(client.events[0], "acquire")
        self.assertEqual(client.events[1], ("pin", ticket, client.lease["lease_id"]))
        self.assertNotIn("start", client.events)

    async def test_pending_start_ticket_on_the_client_is_pinned_after_grant(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("pending-session")
        claimed = coordinator.box_wait_touch(session, claim=True)
        ticket = claimed["box_ticket"]
        client = _GrantClient(coordinator, session)
        client.pending_start_ticket = ticket
        started: dict[str, object] = {}

        def start() -> None:
            started.update(
                lifecycle.start_run(
                    session, client.lease["lease_token"], self._request(game)
                )
            )

        await self._run_transaction(client, box_ticket=None, start=start)
        self.assertEqual(client.events[0], "acquire")
        self.assertEqual(client.events[1], ("pin", ticket, client.lease["lease_id"]))
        self.assertEqual(client.events[2], "start")
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)


class StartRequestTicketTest(BoxQueueFixture, unittest.TestCase):
    """box_ticket must travel in the lifecycle start request, not only on the pin."""

    def test_start_run_copies_the_pin_onto_the_start_request(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("pin-on-request")
        claimed = coordinator.box_wait_touch(session, claim=True)
        ticket = claimed["box_ticket"]
        status, lease = coordinator.acquire(session, "run")
        self.assertEqual(status, 200)
        coordinator.bind_next_start_ticket(
            session, ticket, operation_id=lease["lease_id"]
        )
        seen: list[object] = []
        real = coordinator.box_admit_start

        def wrapped(client, tkt, *, operation_id=None):
            seen.append(tkt)
            return real(client, tkt, operation_id=operation_id)

        coordinator.box_admit_start = wrapped  # type: ignore[method-assign]
        started = lifecycle.start_run(
            session, lease["lease_token"], self._request(game)
        )
        self.assertTrue(started.get("ok"), started)
        self.assertGreaterEqual(len(seen), 1)
        self.assertEqual(seen[0], ticket)
        self.assertTrue(all(item == ticket for item in seen), seen)
        self.assertEqual(len(launcher.calls), 1)

    def test_start_run_admits_a_request_ticket_without_a_pin(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("request-ticket")
        claimed = coordinator.box_wait_touch(session, claim=True)
        status, lease = coordinator.acquire(session, "run")
        self.assertEqual(status, 200)
        started = lifecycle.start_run(
            session,
            lease["lease_token"],
            self._request(game, box_ticket=claimed["box_ticket"]),
        )
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)


class PinClearedOnLeaseEndTest(BoxQueueFixture, unittest.TestCase):
    """Optional: a pin must not outlive its lease."""

    def test_pin_is_cleared_when_the_lease_ends(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("lease-end")
        claimed = coordinator.box_wait_touch(session, claim=True)
        status, lease = coordinator.acquire(session, "run")
        self.assertEqual(status, 200)
        coordinator.bind_next_start_ticket(
            session, claimed["box_ticket"], operation_id=lease["lease_id"]
        )
        coordinator.release(session, lease["lease_token"])
        self.assertIsNotNone(
            coordinator.box_admit_start(
                session, None, operation_id=lease["lease_id"]
            )
        )
        status, fresh = coordinator.acquire(session, "run")
        self.assertEqual(status, 200)
        refused = lifecycle.start_run(
            session, fresh["lease_token"], self._request(game)
        )
        self.assertFalse(refused.get("ok"), refused)
        self.assertEqual(len(launcher.calls), 0)


class SisterStartPinTest(BoxQueueFixture, unittest.TestCase):
    """A start in the same session must not consume another operation's pin."""

    def test_sister_start_does_not_consume_the_operation_pin(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("same-session")
        claimed = coordinator.box_wait_touch(session, claim=True)
        ticket = claimed["box_ticket"]
        status, lease = coordinator.acquire(session, "run")
        self.assertEqual(status, 200)
        coordinator.bind_next_start_ticket(
            session, ticket, operation_id=lease["lease_id"]
        )
        # A start bound to another operation must not consume this pin.
        self.assertIsNotNone(
            coordinator.box_admit_start(session, None, operation_id=_LEASE_B)
        )
        self.assertEqual(len(launcher.calls), 0)
        self.assertTrue(coordinator.box_is_claimed())
        owned = lifecycle.start_run(
            session, lease["lease_token"], self._request(game)
        )
        self.assertTrue(owned.get("ok"), owned)
        self.assertEqual(len(launcher.calls), 1)


class NumberOverflowTest(BoxQueueFixture, unittest.TestCase):
    """Optional: _number must not raise OverflowError on created_at 10**400."""

    def test_created_at_10e400_is_unreadable_not_overflow(self) -> None:
        row = _entry("t1", "claimed")
        row["created_at"] = 10**400
        path = self.root / "box-queue.json"
        path.write_text(json.dumps({"version": 1, "entries": [row]}), encoding="utf-8")
        try:
            parsed = queue.parse_document(path.read_text(encoding="utf-8"))
        except OverflowError as error:
            self.fail(f"parse_document raised OverflowError: {error}")
        self.assertIsNone(parsed)
        try:
            view = queue.read_view(now=1.0, root=self.root)
        except OverflowError as error:
            self.fail(f"read_view raised OverflowError: {error}")
        self.assertTrue(view.get("unreadable"))


class LifecycleStopAllowedTest(BoxQueueFixture, unittest.TestCase):
    """Optional: lifecycle_stop with a valid lease stays allowed."""

    def test_lifecycle_stop_with_a_valid_lease_is_allowed(self) -> None:
        row = _entry("t1", "claimed")
        row["kind"] = []
        path = self.root / "box-queue.json"
        path.write_text(json.dumps({"version": 1, "entries": [row]}), encoding="utf-8")
        coordinator = _coordinator([1.0])
        owner = _identity("sane-stop")
        status, lease = coordinator.acquire(owner, "run")
        self.assertEqual(status, 200)
        decision = coordinator.authorize(
            owner, lease["lease_token"], "lifecycle_stop"
        )
        self.assertTrue(decision.allowed)


class TakeoverUnreadableRereadTest(unittest.IsolatedAsyncioTestCase):
    """Optional: takeover fails closed if the queue re-read is unreadable."""

    async def test_takeover_fails_closed_when_reread_is_unreadable(self) -> None:
        from unittest.mock import AsyncMock

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        calls = {"status": 0}
        live = {
            "occupied": True,
            "runs": [
                {
                    "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "state": "RUNNING",
                    "owner_session": "other",
                }
            ],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
        }

        async def status() -> dict:
            calls["status"] += 1
            if calls["status"] == 1:
                return {"box": live, "self": {"state": "none"}}
            return {
                "box": {**live, "reason": "box_queue_unreadable"},
                "self": {"state": "none"},
            }

        stop = AsyncMock(return_value={"status": "succeeded", "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "error_code": None})
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = server.build_app(config)
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(server.dayz_test_tool, "execute_dayz_test_run", new=execute),
            patch.object(server.dayz_test_tool, "execute_dayz_test_stop", new=stop),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server", "takeover": True},
                )
            )
        stop.assert_not_awaited()
        execute.assert_not_awaited()
        self.assertEqual(payload.get("status"), "failed")
