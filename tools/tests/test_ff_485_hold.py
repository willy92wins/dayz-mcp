"""HOLD on #485: queue-retry T2 pin and same-session sister start."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp import server, shared_box_queue as queue
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import SessionCoordinator
from tests.box_queue_helpers import (
    BoxQueueFixture,
    box_coordinator as _coordinator,
    box_entry as _entry,
    box_identity as _identity,
)
from tests.client_helpers import _fixture_client_runtime
from tests.dayz_test_tool_helpers import RUN_ID
from tests.mcp_helpers import _content_json

_REAL_EXECUTE_WAIT_FOR_BOX = server.execute_wait_for_box
_TAKER_RUN = {
    "run_id": "db23a1df-a49c-4407-a4e4-eb62b895b342",
    "mod": "@DayZ_MCP",
    "label": "@DayZ_MCP server",
    "age_s": 0.5,
    "owner_session": "a4317999-9c5",
    "state": "RUNNING",
    "activity_state": "active",
    "last_activity_age_s": 0.0,
}
_LEASE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _refused() -> dict[str, object]:
    return {
        "status": "failed",
        "project": "ExampleMod",
        "mode": "all",
        "run_id": None,
        "phase": "executing",
        "elapsed_s": 1.0,
        "artifacts_paths": [],
        "error_code": "active_run_exists",
        "cleanup_degraded": False,
        "server_alive": None,
        "client_alive": None,
        "reason": None,
    }


def _launched() -> dict[str, object]:
    return {
        "status": "succeeded",
        "project": "ExampleMod",
        "mode": "all",
        "run_id": RUN_ID,
        "phase": "completed",
        "elapsed_s": 1.0,
        "artifacts_paths": [],
        "error_code": None,
        "cleanup_degraded": False,
    }


class _Box:
    def __init__(self) -> None:
        self.seq = 0
        self.runs: list[dict[str, object]] = []
        self.joins = 0
        self.coordinator = SessionCoordinator(
            time_fn=lambda: 0.0,
            id_fn=self._next_id,
            token_fn=lambda: "unused",
        )

    def _next_id(self) -> str:
        self.seq += 1
        return f"ticket-{self.seq}"

    def snapshot(self) -> dict[str, object]:
        claim = self.coordinator.box_claim_public()
        box: dict[str, object] = {
            "occupied": bool(self.runs) or bool(claim.get("claimed")),
            "runs": list(self.runs),
            "foreign": [],
            "ports_in_use": [2302] if self.runs else [],
            "queue": self.coordinator.box_queue_public(),
            "scan_known": True,
            "port_scan_known": True,
            "foreign_ports": [],
        }
        if claim.get("claimed_s") is not None:
            box["claimed_s"] = claim["claimed_s"]
        return {"box": box}

    def bind(self, runtime: object) -> None:
        caller = runtime.identity  # type: ignore[attr-defined]

        async def session_status() -> dict[str, object]:
            await asyncio.sleep(0)
            return self.snapshot()

        async def session_box_status(**kwargs: object) -> dict[str, object]:
            await asyncio.sleep(0)
            if kwargs.get("wait") and not kwargs.get("ticket"):
                self.joins += 1
            touched = self.coordinator.box_wait_touch(
                caller,
                kwargs.get("ticket"),
                done=bool(kwargs.get("done")),
                claim=bool(kwargs.get("claim")),
            )
            snap = self.snapshot()
            snap["box_ticket"] = touched.get("box_ticket")
            if "box_claimed" in touched:
                snap["box_claimed"] = touched["box_claimed"]
            if touched.get("box_wait_error"):
                snap["box_wait_error"] = touched["box_wait_error"]
            return snap

        runtime.session_status = session_status  # type: ignore[attr-defined]
        runtime.session_box_status = session_box_status  # type: ignore[attr-defined]


@contextmanager
def _fast_box_wait():
    async def wrapped(client, wait_s, **kwargs):
        kwargs.setdefault("poll_interval_s", 0.05)
        return await _REAL_EXECUTE_WAIT_FOR_BOX(client, wait_s, **kwargs)

    with patch.object(server, "execute_wait_for_box", wrapped):
        yield


async def _until(predicate, *, timeout_s: float = 2.0, message: str = "condition") -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


class QueueRetryPinsT2Test(unittest.IsolatedAsyncioTestCase):
    """on_busy='queue' retry must pin T2 under tool_lock and pass it to execute."""

    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key)
            for key in ("DAYZ_MCP_SHARED_ROOT", "DAYZ_MCP_FAST_TESTS")
        }
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "shared"
        self.root.mkdir()
        os.environ["DAYZ_MCP_SHARED_ROOT"] = str(self.root)
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = server.build_app(config)
        self.box = _Box()
        self.box.bind(self.runtime)
        self.launches: list[dict[str, object]] = []
        self.pins: list[dict[str, object]] = []

        async def bind(ticket: str) -> dict[str, object]:
            self.pins.append(
                {"ticket": ticket, "lock_held": self.runtime.tool_lock.locked()}
            )
            return {}

        self.runtime.bind_next_start_ticket = bind  # type: ignore[method-assign]

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    async def test_queue_retry_passes_t2_into_execute_under_tool_lock(self) -> None:
        refused = asyncio.Event()
        freed = asyncio.Event()

        async def execute(_client, **kwargs: object) -> dict[str, object]:
            self.launches.append(
                {
                    "build": kwargs["build"],
                    "box_ticket": kwargs.get("box_ticket"),
                }
            )
            if len(self.launches) == 1:
                self.box.runs = [dict(_TAKER_RUN)]
                refused.set()
                return _refused()
            self.assertTrue(freed.is_set(), "launched before the box was free")
            return _launched()

        async def free_once_requeued() -> None:
            await refused.wait()
            await _until(lambda: self.box.joins >= 2, message="never re-joined")
            self.box.runs = []
            freed.set()

        with patch.object(
            server.dayz_test_tool, "execute_dayz_test_run", side_effect=execute
        ), _fast_box_wait():
            payload, _freed = await asyncio.gather(
                self._call_run(),
                free_once_requeued(),
            )

        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(len(self.launches), 2)
        self.assertEqual(self.launches[0]["build"], True)
        self.assertEqual(self.launches[1]["build"], False)
        self.assertEqual(self.launches[1]["box_ticket"], "ticket-2")
        retry_pins = [pin for pin in self.pins if pin["ticket"] == "ticket-2"]
        self.assertEqual(len(retry_pins), 1)
        self.assertTrue(retry_pins[0]["lock_held"])

    async def _call_run(self) -> dict[str, object]:
        return _content_json(
            await self.app.call_tool(
                "dayz_test_run",
                {
                    "project": "ExampleMod",
                    "mode": "all",
                    "build": True,
                    "on_busy": "queue",
                    "wait_for_box_s": 5.0,
                },
            )
        )


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
