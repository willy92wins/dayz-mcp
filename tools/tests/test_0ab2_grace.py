"""fb-20260909-213002-0ab2: lease grace S1 + takeover_required.

Fixtures from docs/plan-0ab2.md (EXACT).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.process_lifecycle import occupancy_error_fields, takeover_target_run_id
from dayz_mcp.server import ServerConfig, TAKEOVER_REQUIRED
from dayz_mcp import server as server_module
from dayz_mcp.session_coordination import (
    LEASE_GRACE_S,
    MAX_PREF_RENEWALS,
    SESSION_TTL_S,
    SessionCoordinator,
)
from tests.test_mcp_tools import _content_json
from tests.test_session_coordination import (
    AuditSink,
    CleanupSink,
    FakeClock,
    SequentialIds,
    _identity,
)


TTL = SESSION_TTL_S
G = LEASE_GRACE_S
MID = TTL + (G / 2.0)
AFTER = TTL + G + 0.001


def _coord(clock: FakeClock, *, attached: bool) -> SessionCoordinator:
    return SessionCoordinator(
        time_fn=clock,
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("id"),
        audit=AuditSink(),
        cleanup=CleanupSink(),
        attached_run_probe=lambda _session, _lease: attached,
    )


class GraceS1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _identity("a")
        self.b = _identity("b")
        self.assertEqual(LEASE_GRACE_S, 90.0)
        self.assertEqual(MAX_PREF_RENEWALS, 1)

    def test_p1_former_reacquires_at_ttl_plus_half_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        old = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(MID)
        status, payload = coordinator.acquire(self.a, "drive")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")
        self.assertNotEqual(payload["lease_token"], old)
        self.assertIsNone(coordinator.status(self.a)["grace"])

    def test_p1_neg_old_token_stays_expired(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        old = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(MID)
        coordinator.acquire(self.a, "drive")
        decision = coordinator.authorize(self.a, old, "world_spawn")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error, "lease_expired")

    def test_p1w_enqueue_wait_promotes_former(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        queued = coordinator.acquire(self.b, "next")
        self.assertEqual(queued[0], 202)
        clock.advance(119.0)
        self.assertEqual(
            coordinator.wait(self.b, queued[1]["ticket"], 0.0)[0],
            202,
        )
        clock.advance(MID - 119.0)
        enq = coordinator.enqueue(self.a, "back", "operation-a")
        self.assertEqual(enq[0], 202)
        claimed = coordinator.wait(self.a, enq[1]["ticket"], 0.0)
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))

    def test_p2_n1_stranger_is_queued_during_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "queued")
        self.assertNotEqual(payload.get("status"), "active")

    def test_p3_n4_one_pref_renewal_with_queue(self) -> None:
        self.assertEqual(MAX_PREF_RENEWALS, 1)
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        ticket = coordinator.acquire(self.b, "next")[1]["ticket"]
        clock.advance(119.0)
        self.assertEqual(coordinator.wait(self.b, ticket, 0.0)[0], 202)
        clock.advance(MID - 119.0)
        blocked = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((blocked[0], blocked[1]["status"]), (202, "queued"))
        first = coordinator.acquire(self.a, "drive")
        self.assertEqual(first[0], 200)
        queued = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
        clock.advance(TTL - 1.0)
        queued = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((queued[0], queued[1]["status"]), (202, "queued"))
        clock.advance(1.0)
        second = coordinator.acquire(self.a, "again")
        self.assertNotEqual(second[0], 200)
        claimed = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual((claimed[0], claimed[1]["status"]), (200, "active"))

    def test_p4_no_attached_run_no_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=False)
        coordinator.acquire(self.a, "drive")
        clock.advance(TTL + 0.001)
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")
        self.assertIsNone(coordinator.status(self.a)["grace"])

    def test_n2_authorize_at_ttl_is_lease_expired(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        token = coordinator.acquire(self.a, "drive")[1]["lease_token"]
        clock.advance(TTL)
        decision = coordinator.authorize(self.a, token, "world_spawn")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error, "lease_expired")
        beat = coordinator.heartbeat(self.a, token)
        self.assertNotEqual(beat[0], 200)

    def test_n3_stranger_wins_after_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(AFTER)
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")

    def test_grace_status_shape_during_window(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        clock.advance(MID)
        grace = coordinator.status(self.a)["grace"]
        self.assertIsInstance(grace, dict)
        self.assertEqual(grace["attached_required"], True)
        self.assertEqual(grace["pref_remaining"], 1)
        self.assertAlmostEqual(grace["remaining_s"], G / 2.0, places=3)

    def test_stranger_wait_does_not_claim_during_grace(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(self.a, "drive")
        ticket = coordinator.acquire(self.b, "next")[1]["ticket"]
        clock.advance(119.0)
        self.assertEqual(coordinator.wait(self.b, ticket, 0.0)[0], 202)
        clock.advance(MID - 119.0)
        waited = coordinator.wait(self.b, ticket, 0.0)
        self.assertEqual(waited[0], 202)
        self.assertEqual(waited[1]["status"], "queued")

    def test_default_probe_keeps_h4_stranger_grant(self) -> None:
        clock = FakeClock()
        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("id"),
            audit=AuditSink(),
            cleanup=CleanupSink(),
        )
        coordinator.acquire(self.a, "drive")
        clock.advance(TTL + 0.001)
        status, payload = coordinator.acquire(self.b, "drive")
        self.assertEqual((status, payload["status"]), (200, "active"))
        self.assertIsNone(coordinator.status(self.a)["grace"])


class TakeoverBTests(unittest.TestCase):
    def test_n5_ownerless_idle_is_not_owned(self) -> None:
        box = {
            "runs": [
                {
                    "run_id": "abc",
                    "mod": "@M",
                    "label": "lab",
                    "age_s": 1.0,
                    "state": "RUNNING_IDLE",
                    "owner_session": None,
                }
            ]
        }
        fields = occupancy_error_fields(box, caller_session="me")
        hint = str(fields.get("hint") or "")
        self.assertIn("takeover=true", hint)
        self.assertNotIn("stop it with dayz_test_stop", hint)
        self.assertEqual(takeover_target_run_id(box, caller_session="me"), "abc")

    def test_owned_running_is_not_a_takeover_target(self) -> None:
        box = {
            "runs": [
                {
                    "run_id": "abc",
                    "state": "RUNNING",
                    "owner_session": "me",
                }
            ]
        }
        self.assertIsNone(takeover_target_run_id(box, caller_session="me"))


class DayzTestRunTakeoverTests(unittest.IsolatedAsyncioTestCase):
    def _config(self) -> ServerConfig:
        return ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )

    def _foreign_box(self, *, state: str, owner: str | None) -> dict[str, object]:
        return {
            "occupied": True,
            "runs": [
                {
                    "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "mod": "@M",
                    "label": "lab",
                    "age_s": 12.0,
                    "state": state,
                    "owner_session": owner,
                }
            ],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
        }

    async def _call(
        self,
        *,
        box: dict[str, object],
        takeover: bool,
        execute: object | None = None,
        stop: object | None = None,
    ) -> dict:
        from tests.test_client_mode import _fixture_client_runtime

        runtime = _fixture_client_runtime(self._config())
        status = AsyncMock(return_value={"box": box, "self": {"state": "none"}})
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        args = {
            "project": "ExampleMod",
            "mode": "server",
            "takeover": takeover,
        }
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                new=execute
                or AsyncMock(side_effect=AssertionError("must not launch")),
            ),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_stop",
                new=stop or AsyncMock(side_effect=AssertionError("must not stop")),
            ),
        ):
            return _content_json(await app.call_tool("dayz_test_run", args))

    async def test_p5_foreign_running_is_takeover_required(self) -> None:
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        payload = await self._call(
            box=self._foreign_box(state="RUNNING", owner="other"),
            takeover=False,
            execute=execute,
        )
        execute.assert_not_awaited()
        self.assertEqual(payload.get("status"), "failed")
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)
        self.assertEqual(
            payload.get("occupied_by_run_id"),
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        hint = str(payload.get("hint") or "")
        self.assertIn("takeover=true", hint)
        self.assertNotIn("stop it with dayz_test_stop", hint)

    async def test_n6_ownerless_idle_is_takeover_required(self) -> None:
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        payload = await self._call(
            box=self._foreign_box(state="RUNNING_IDLE", owner=None),
            takeover=False,
            execute=execute,
        )
        execute.assert_not_awaited()
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)
        hint = str(payload.get("hint") or "")
        self.assertIn("takeover=true", hint)
        self.assertNotIn("stop it with dayz_test_stop", hint)

    async def test_p6_takeover_stops_then_launches(self) -> None:
        run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        stop = AsyncMock(
            return_value={
                "status": "succeeded",
                "run_id": run_id,
                "error_code": None,
            }
        )
        execute = AsyncMock(
            return_value={
                "status": "succeeded",
                "project": "ExampleMod",
                "mode": "server",
                "run_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "phase": "completed",
                "elapsed_s": 0.1,
                "artifacts_paths": [],
                "error_code": None,
                "cleanup_degraded": False,
            }
        )
        with patch("psutil.Process") as process_cls:
            kill = process_cls.return_value.kill
            payload = await self._call(
                box=self._foreign_box(state="RUNNING", owner="other"),
                takeover=True,
                execute=execute,
                stop=stop,
            )
        stop.assert_awaited()
        execute.assert_awaited()
        self.assertEqual(payload.get("status"), "succeeded")
        self.assertEqual(payload.get("evicted_run_id"), run_id)
        kill.assert_not_called()

    async def test_takeover_is_published_on_the_tool(self) -> None:
        app, _ = server_module.build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        props = app._tool_manager.get_tool("dayz_test_run").parameters["properties"]
        self.assertIn("takeover", props)
        self.assertTrue((props["takeover"].get("description") or "").strip())


if __name__ == "__main__":
    unittest.main()
