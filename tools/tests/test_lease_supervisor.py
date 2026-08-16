from __future__ import annotations

import asyncio
import importlib
import unittest


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _StepWaiter:
    def __init__(self, clock: _Clock, steps: list[float]) -> None:
        self.clock = clock
        self.steps = steps
        self.delays: list[float] = []

    async def __call__(self, stop: asyncio.Event, delay: float) -> bool:
        self.delays.append(delay)
        if self.steps:
            self.clock.now = self.steps.pop(0)
            await asyncio.sleep(0)
            return False
        await stop.wait()
        return True


class _Client:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.heartbeats: list[tuple[str, float]] = []
        self.heartbeat_called = asyncio.Event()
        self.release_calls: list[str] = []
        self.release_started = asyncio.Event()
        self.allow_release = asyncio.Event()
        self.status = {
            "self": {"state": "none", "position": None},
            "pending_commands": 0,
        }
        self.heartbeat_error: BaseException | None = None
        self.release_error: BaseException | None = None

    async def session_heartbeat(self, lease_token: str):
        self.heartbeats.append((lease_token, self.clock()))
        self.heartbeat_called.set()
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return {
            "status": "active",
            "lease_token": lease_token,
            "lease_id": "lease-one",
            "expires_in_s": 120.0,
        }

    async def session_release(self, lease_token: str):
        self.release_calls.append(lease_token)
        self.release_started.set()
        await self.allow_release.wait()
        if self.release_error is not None:
            raise self.release_error
        return {"status": "released"}

    async def session_status(self):
        return self.status


class LeaseHeartbeatSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_schedule_sends_at_45_not_44(self) -> None:
        module = importlib.import_module("dayz_mcp.lease_supervisor")
        clock = _Clock()
        waiter = _StepWaiter(clock, [44.0, 45.0])
        client = _Client(clock)
        supervisor = module.LeaseHeartbeatSupervisor(
            client,
            lease_token="secret-token",
            lease_id="lease-one",
            monotonic=clock,
            wait_fn=waiter,
        )

        supervisor.start()
        await asyncio.wait_for(client.heartbeat_called.wait(), timeout=0.5)
        self.assertEqual(client.heartbeats, [("secret-token", 45.0)])
        self.assertAlmostEqual(waiter.delays[0], 45.0)
        self.assertAlmostEqual(waiter.delays[1], 1.0)
        supervisor.ensure_healthy()
        await supervisor.stop()

    async def test_missing_a_whole_fixed_slot_fails_without_heartbeat(self) -> None:
        module = importlib.import_module("dayz_mcp.lease_supervisor")
        clock = _Clock()
        waiter = _StepWaiter(clock, [90.0])
        client = _Client(clock)
        supervisor = module.LeaseHeartbeatSupervisor(
            client,
            lease_token="secret-token",
            lease_id="lease-one",
            monotonic=clock,
            wait_fn=waiter,
        )

        supervisor.start()
        failure = await asyncio.wait_for(supervisor.wait_failed(), timeout=0.5)
        self.assertEqual(failure.code, "lease_heartbeat_tardy")
        self.assertEqual(client.heartbeats, [])
        with self.assertRaisesRegex(
            module.LeaseHeartbeatError, "lease_heartbeat_tardy"
        ):
            supervisor.ensure_healthy()
        await supervisor.stop()

    async def test_rejected_heartbeat_marks_supervisor_failed(self) -> None:
        module = importlib.import_module("dayz_mcp.lease_supervisor")
        clock = _Clock()
        waiter = _StepWaiter(clock, [45.0])
        client = _Client(clock)
        client.heartbeat_error = RuntimeError("remote detail must not escape")
        supervisor = module.LeaseHeartbeatSupervisor(
            client,
            lease_token="secret-token",
            lease_id="lease-one",
            monotonic=clock,
            wait_fn=waiter,
        )

        supervisor.start()
        failure = await asyncio.wait_for(supervisor.wait_failed(), timeout=0.5)
        self.assertEqual(failure.code, "lease_heartbeat_failed")
        self.assertNotIn("remote detail", str(failure))
        await supervisor.stop()

    async def test_stop_before_due_sends_no_heartbeat(self) -> None:
        module = importlib.import_module("dayz_mcp.lease_supervisor")
        clock = _Clock()
        waiter = _StepWaiter(clock, [])
        client = _Client(clock)
        supervisor = module.LeaseHeartbeatSupervisor(
            client,
            lease_token="secret-token",
            lease_id="lease-one",
            monotonic=clock,
            wait_fn=waiter,
        )
        supervisor.start()
        await supervisor.stop()
        self.assertEqual(client.heartbeats, [])

    async def test_protected_release_finishes_before_delivering_cancellation(self) -> None:
        module = importlib.import_module("dayz_mcp.lease_supervisor")
        client = _Client(_Clock())
        task = asyncio.create_task(
            module.protected_release_and_verify(client, "secret-token")
        )
        await client.release_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        client.allow_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(client.release_calls, ["secret-token"])


class TerminalSessionStatusTests(unittest.TestCase):
    def test_clean_terminal_status_is_accepted(self) -> None:
        module = importlib.import_module("dayz_mcp.lease_supervisor")
        status = {
            "self": {"state": "none", "position": None},
            "pending_commands": 0,
        }
        module.validate_terminal_session_status(status)

    def test_owned_or_malformed_terminal_state_fails_closed(self) -> None:
        module = importlib.import_module("dayz_mcp.lease_supervisor")
        invalid = (
            {"self": {"state": "active"}, "pending_commands": 0},
            {"self": {"state": "releasing"}, "pending_commands": 0},
            {
                "self": {"state": "queued", "ticket": "ticket-one"},
                "pending_commands": 0,
            },
            {
                "self": {"state": "none", "ticket": "hidden-ticket"},
                "pending_commands": 0,
            },
            {"self": {"state": "none"}, "pending_commands": 1},
            {"self": {"state": "none"}, "pending_commands": False},
            {"self": {"state": "none"}},
            {"pending_commands": 0},
        )
        for status in invalid:
            with self.subTest(status=status), self.assertRaisesRegex(
                module.LeaseCleanupError, "session_close_degraded"
            ):
                module.validate_terminal_session_status(status)


if __name__ == "__main__":
    unittest.main()
