"""Box FIFO offer: on_busy=queue, queue_offer, and session_status position."""

from __future__ import annotations

import asyncio
import copy
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import anyio

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, TAKEOVER_REQUIRED
from dayz_mcp.session_coordination import (
    MAX_OPERATION_TOMBSTONES,
    SESSION_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json
from tests._tiers import slow_test

_REAL_EXECUTE_WAIT_FOR_BOX = server.execute_wait_for_box

_LAUNCH_OK = {
    "status": "succeeded",
    "project": "ExampleMod",
    "mode": "server",
    "run_id": "12345678-1234-4234-8234-1234567890ab",
    "phase": "completed",
    "elapsed_s": 0.1,
    "artifacts_paths": [],
    "error_code": None,
    "cleanup_degraded": False,
}

_ACTIVE_RUN_FAILED = {
    "status": "failed",
    "project": "ExampleMod",
    "mode": "server",
    "run_id": None,
    "phase": "executing",
    "elapsed_s": 0.2,
    "artifacts_paths": [],
    "error_code": "active_run_exists",
    "cleanup_degraded": False,
    "server_alive": None,
    "client_alive": None,
}


def _run_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": "runOther01",
        "mod": "@CF",
        "label": "lab",
        "age_s": 12.0,
        "owner_session": "otherSess012",
        "state": "RUNNING",
        "activity_state": "active",
        "last_activity_age_s": 0.0,
    }
    row.update(overrides)
    return row


def _busy_box(*, runs=None, foreign=None, queue=None, **extra) -> dict[str, object]:
    box: dict[str, object] = {
        "occupied": True,
        "runs": [_run_row()] if runs is None else list(runs),
        "foreign": [] if foreign is None else list(foreign),
        "ports_in_use": [2302],
        "queue": [] if queue is None else list(queue),
        "scan_known": True,
        "port_scan_known": True,
    }
    box.update(extra)
    return box


def _config() -> ServerConfig:
    return ServerConfig(
        mode="client",
        key="k",
        port=12345,
        client_platform="codex",
        log_sink=lambda _message: None,
    )


def _build(runtime=None):
    config = _config()
    if runtime is None:
        runtime = _fixture_client_runtime(config)
    with patch.object(server, "ClientRuntime", return_value=runtime):
        app, _built = server.build_app(config)
    return runtime, app


@contextmanager
def _fast_box_wait():
    async def wrapped(client, wait_s, **kwargs):
        kwargs.setdefault("poll_interval_s", 0.05)
        return await _REAL_EXECUTE_WAIT_FOR_BOX(client, wait_s, **kwargs)

    with patch.object(server, "execute_wait_for_box", wrapped):
        yield


async def _until(predicate, *, timeout_s: float = 2.0, message: str = "condition") -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


def _fail_product(test: unittest.TestCase, exc: BaseException) -> None:
    if isinstance(exc, AssertionError):
        raise exc
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise exc
    test.fail(f"product raised {type(exc).__name__}: {exc}")


def _block_later_box_waits(runtime, entered: asyncio.Event) -> None:
    inner = runtime.session_box_status
    polls = {"n": 0}

    async def session_box_status(**kwargs: object) -> dict[str, object]:
        if kwargs.get("done") or kwargs.get("claim"):
            return await inner(**kwargs)
        result = await inner(**kwargs)
        if kwargs.get("wait"):
            polls["n"] += 1
            if polls["n"] >= 2:
                entered.set()
                await asyncio.Event().wait()
        return result

    runtime.session_box_status = session_box_status


async def _cancel_scope_after(event: asyncio.Event, scope: anyio.CancelScope) -> None:
    await event.wait()
    scope.cancel()


def _hang_first_box_join(runtime, entered: asyncio.Event) -> None:
    inner = runtime.session_box_status

    async def session_box_status(**kwargs: object) -> dict[str, object]:
        if kwargs.get("done") or kwargs.get("claim"):
            return await inner(**kwargs)
        # Enqueue first, like the daemon, then stay in-flight so the
        # caller never learns the ticket id.
        result = await inner(**kwargs)
        if kwargs.get("wait"):
            entered.set()
            await asyncio.Event().wait()
        return result

    runtime.session_box_status = session_box_status


def _delay_join_so_done_can_win(runtime, entered: asyncio.Event) -> None:
    inner = runtime.session_box_status
    pending: list[asyncio.Task[object]] = []

    async def session_box_status(**kwargs: object) -> dict[str, object]:
        if kwargs.get("done") or kwargs.get("claim"):
            return await inner(**kwargs)

        async def apply_join() -> dict[str, object]:
            await asyncio.sleep(0.05)
            return await inner(**kwargs)

        apply_task = asyncio.create_task(apply_join())
        pending.append(apply_task)
        entered.set()
        return await asyncio.shield(apply_task)

    runtime.session_box_status = session_box_status


class _BoxHarness:
    """Shared occupancy plus the real request-bound box FIFO."""

    def __init__(
        self, *, occupied: bool = True, runs=None, foreign=None, foreign_ports=None
    ) -> None:
        self.now = 0.0
        self.seq = 0
        self.occupied = occupied
        self.runs = list(runs or [])
        self.foreign = list(foreign or [])
        self.foreign_ports = list(foreign_ports or [])
        self.box_calls: list[dict[str, object]] = []
        self.done: list[object] = []
        self.coordinator = SessionCoordinator(
            time_fn=lambda: self.now,
            id_fn=self._next_id,
            token_fn=lambda: "unused",
        )

    def _next_id(self) -> str:
        self.seq += 1
        return f"ticket-{self.seq}"

    def snapshot(self) -> dict[str, object]:
        claimed = self.coordinator.box_is_claimed()
        occupancy: dict[str, object] = {
            "occupied": bool(self.occupied or claimed),
            "runs": list(self.runs) if self.occupied else [],
            "foreign": list(self.foreign) if self.occupied else [],
            "ports_in_use": [2302] if self.occupied else [],
            "queue": self.coordinator.box_queue_public(),
            "scan_known": True,
            "port_scan_known": True,
            "foreign_ports": list(self.foreign_ports),
        }
        claim = self.coordinator.box_claim_public()
        if claim.get("claimed_s") is not None:
            occupancy["claimed_s"] = claim["claimed_s"]
        return {"box": occupancy}

    def handle_box(self, identity, **kwargs: object) -> dict[str, object]:
        self.box_calls.append(dict(kwargs))
        if kwargs.get("done"):
            self.done.append(kwargs.get("ticket"))
        if not (
            kwargs.get("wait")
            or kwargs.get("done")
            or kwargs.get("claim")
            or kwargs.get("ticket")
        ):
            # Like the daemon's /session/status: without a box flag or a ticket
            # the read reports the box and does not join the FIFO.
            return self.snapshot()
        result = self.coordinator.box_wait_touch(
            identity,
            kwargs.get("ticket"),
            done=bool(kwargs.get("done")),
            claim=bool(kwargs.get("claim")),
        )
        snap = self.snapshot()
        snap["box_ticket"] = result.get("box_ticket")
        if "box_claimed" in result:
            snap["box_claimed"] = result["box_claimed"]
        if result.get("box_wait_error"):
            snap["box_wait_error"] = result["box_wait_error"]
        return snap

    def bind(self, runtime) -> None:
        identity = runtime.identity

        async def session_status() -> dict[str, object]:
            await asyncio.sleep(0)
            return self.snapshot()

        async def session_box_status(**kwargs: object) -> dict[str, object]:
            # The real client suspends in asyncio.to_thread; without a
            # checkpoint here a cancelled CancelScope never re-cancels.
            await asyncio.sleep(0)
            return self.handle_box(identity, **kwargs)

        runtime.session_status = session_status
        runtime.session_box_status = session_box_status

    def public_in_queue(self, session_id: str) -> bool:
        token = session_id[:12]
        return any(
            item.get("session") == token
            for item in self.coordinator.box_queue_public()
        )


def _status_payload(*, occupied: bool, queue=None, runs=None) -> dict[str, object]:
    return {
        "owner": None,
        "queue": [],
        "self": {"state": "none", "position": None},
        "claimable": True,
        "audit_fault": None,
        "lifecycle_recovery_fault": None,
        "operation_tombstones": {
            "count": 0,
            "capacity": 4096,
            "saturated": False,
        },
        "cleanup_degraded": [],
        "daemon_generation": "generation-a",
        "pending_commands": 0,
        "box": {
            "occupied": occupied,
            "runs": [] if runs is None else list(runs),
            "foreign": [],
            "ports_in_use": [2302] if occupied else [],
            "queue": [] if queue is None else list(queue),
            "scan_known": True,
            "port_scan_known": True,
        },
    }


class Fb2223BoxQueueOfferTest(unittest.IsolatedAsyncioTestCase):
    async def test_fb_2223_fail_busy_run_carries_offer_without_fifo(self) -> None:
        runtime, app = _build()
        box = _busy_box(
            queue=[{"session": "waiter-sessi", "waiting_s": 1.0}],
        )
        box_status = AsyncMock(return_value={})
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with (
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            patch.object(
                runtime, "session_status", new=AsyncMock(return_value={"box": box})
            ),
            patch.object(runtime, "session_box_status", new=box_status),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "fail",
                    },
                )
            )
        execute.assert_not_awaited()
        box_status.assert_not_awaited()
        offer = payload.get("queue_offer")
        self.assertIsInstance(offer, dict)
        self.assertEqual(offer["position_if_joined"], offer["waiters"] + 1)
        self.assertEqual(offer["waiters"], 1)
        self.assertEqual(offer["occupant"]["run_id"], "runOther01")

    async def test_fb_2223_foreign_occupant_mods_are_whitelisted(self) -> None:
        box = _busy_box(
            runs=[],
            foreign=[
                {
                    "port": 2302,
                    "mods": ["@CF", r"C:\Mods\@Evil", "a\nb", "@Ok Name", " @Pad"],
                    "profiles": r"P:\secret",
                    "image": "DayZDiag_x64.exe",
                    "source": "netstat",
                    "pid": 4321,
                }
            ],
        )
        offer = server._box_queue_offer(box, caller_session="caller-session")
        self.assertIsInstance(offer, dict)
        occupant = offer["occupant"]
        self.assertEqual(
            set(occupant),
            {"run_id", "state", "foreign", "port", "mods"},
        )
        self.assertEqual(occupant["mods"], ["@CF", "@Ok Name"])
        self.assertEqual(occupant["port"], 2302)
        self.assertIs(occupant["foreign"], True)

        runtime, app = _build()
        box_status = AsyncMock(return_value={})

        async def execute_run(*_args: object, **_kwargs: object) -> dict[str, object]:
            return dict(_ACTIVE_RUN_FAILED)

        with (
            patch.object(
                server.dayz_test_tool,
                "execute_dayz_test_run",
                side_effect=execute_run,
            ),
            patch.object(
                runtime, "session_status", new=AsyncMock(return_value={"box": box})
            ),
            patch.object(runtime, "session_box_status", new=box_status),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server"},
                )
            )
        box_status.assert_not_awaited()
        wired = payload["queue_offer"]["occupant"]
        self.assertEqual(set(wired), {"run_id", "state", "foreign", "port", "mods"})
        self.assertEqual(wired["mods"], ["@CF", "@Ok Name"])
        self.assertEqual(wired["port"], 2302)
        self.assertIs(wired["foreign"], True)

    @slow_test
    async def test_fb_2223_queue_launches_once_when_box_frees(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        launches: list[str] = []

        async def execute_run(*_args: object, **_kwargs: object) -> dict[str, object]:
            launches.append("run")
            return dict(_LAUNCH_OK)

        async def free_when_queued() -> None:
            await _until(
                lambda: harness.coordinator.box_queue_public(),
                message="waiter never joined",
            )
            harness.occupied = False
            harness.runs = []

        with (
            patch.object(
                server.dayz_test_tool,
                "execute_dayz_test_run",
                side_effect=execute_run,
            ),
            _fast_box_wait(),
        ):
            payload, _freed = await asyncio.gather(
                app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "queue",
                        "wait_for_box_s": 5.0,
                    },
                ),
                free_when_queued(),
            )
        result = _content_json(payload)
        self.assertEqual(result.get("status"), "succeeded")
        self.assertEqual(launches, ["run"])
        self.assertEqual(len(harness.done), 1)
        self.assertEqual(harness.coordinator.box_queue_public(), [])

    @slow_test
    async def test_fb_2223_queue_fifo_b_launches_before_c(self) -> None:
        config = _config()
        runtime_b = _fixture_client_runtime(config)
        runtime_c = _fixture_client_runtime(config)
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime_b)
        harness.bind(runtime_c)
        with patch.object(server, "ClientRuntime", return_value=runtime_b):
            app_b, _built_b = server.build_app(config)
        with patch.object(server, "ClientRuntime", return_value=runtime_c):
            app_c, _built_c = server.build_app(config)
        launches: list[str] = []

        async def execute_run(client, **_kwargs: object) -> dict[str, object]:
            launches.append(client.identity.session_id)
            return dict(_LAUNCH_OK)

        with (
            patch.object(
                server.dayz_test_tool,
                "execute_dayz_test_run",
                side_effect=execute_run,
            ),
            _fast_box_wait(),
        ):
            task_b = asyncio.create_task(
                app_b.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "queue",
                        "wait_for_box_s": 5.0,
                    },
                )
            )
            await _until(
                lambda: harness.public_in_queue(runtime_b.identity.session_id),
                message="session B never joined",
            )
            task_c = asyncio.create_task(
                app_c.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "queue",
                        "wait_for_box_s": 5.0,
                    },
                )
            )
            await _until(
                lambda: len(harness.coordinator.box_queue_public()) >= 2,
                message="session C never joined",
            )
            harness.occupied = False
            harness.runs = []
            await asyncio.gather(task_b, task_c)
        self.assertEqual(
            launches,
            [runtime_b.identity.session_id, runtime_c.identity.session_id],
        )

    async def test_fb_2223_queue_cancel_leaves_ticket_and_empty_fifo(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with (
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            _fast_box_wait(),
        ):
            task = asyncio.create_task(
                app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "queue",
                        "wait_for_box_s": 30.0,
                    },
                )
            )
            await _until(
                lambda: harness.coordinator.box_queue_public(),
                message="waiter never joined",
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        execute.assert_not_awaited()
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertTrue(harness.done)

    async def test_fb_2223_queue_adoptable_idle_fails_at_once_without_fifo(self) -> None:
        runtime, app = _build()
        box = _busy_box(
            runs=[
                _run_row(
                    state="RUNNING_IDLE",
                    owner_session=None,
                    last_activity_age_s=1.0,
                    age_s=1.0,
                    use_state="abandoned",
                )
            ],
        )
        box_status = AsyncMock(return_value={})
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with (
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            patch.object(
                runtime, "session_status", new=AsyncMock(return_value={"box": box})
            ),
            patch.object(runtime, "session_box_status", new=box_status),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "queue",
                    },
                )
            )
        execute.assert_not_awaited()
        box_status.assert_not_awaited()
        self.assertEqual(payload.get("reason"), "adopt")
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)
        self.assertIsNone(payload.get("queue_offer"))

    @slow_test
    async def test_fb_2223_queue_becomes_adoptable_while_waiting(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))

        async def become_adoptable() -> None:
            await _until(
                lambda: harness.coordinator.box_queue_public(),
                message="waiter never joined",
            )
            harness.runs = [
                _run_row(
                    state="RUNNING_IDLE",
                    owner_session=None,
                    last_activity_age_s=1.0,
                    age_s=1.0,
                    use_state="abandoned",
                )
            ]

        with (
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            _fast_box_wait(),
        ):
            payload, _changed = await asyncio.gather(
                app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "queue",
                        "wait_for_box_s": 5.0,
                    },
                ),
                become_adoptable(),
            )
        result = _content_json(payload)
        execute.assert_not_awaited()
        self.assertEqual(result.get("reason"), "adopt")
        self.assertEqual(result.get("error_code"), TAKEOVER_REQUIRED)
        self.assertIsNone(result.get("queue_offer"))
        self.assertTrue(harness.done)
        self.assertEqual(harness.coordinator.box_queue_public(), [])

    @slow_test
    async def test_fb_2223_queue_timeout_with_short_wait_carries_offer(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with (
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            _fast_box_wait(),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "queue",
                        "wait_for_box_s": 0.2,
                    },
                )
            )
        execute.assert_not_awaited()
        self.assertIsNotNone(payload.get("queue_offer"))
        self.assertEqual(
            payload["queue_offer"]["retry"],
            {"tool": "dayz_test_run", "on_busy": "queue", "same_args": True},
        )

    @slow_test
    async def test_fb_2223_on_busy_rejects_later_empty_and_uppercase(self) -> None:
        runtime, app = _build()
        status = AsyncMock(return_value={"box": _busy_box()})
        box_status = AsyncMock(return_value={})
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with (
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            patch.object(runtime, "session_status", new=status),
            patch.object(runtime, "session_box_status", new=box_status),
        ):
            for value in ("later", "", "QUEUE"):
                with self.subTest(on_busy=value):
                    status.reset_mock()
                    box_status.reset_mock()
                    with self.assertRaises(Exception) as err:
                        await app.call_tool(
                            "dayz_test_run",
                            {
                                "project": "ExampleMod",
                                "mode": "server",
                                "on_busy": value,
                            },
                        )
                    self.assertIn("bad_args", str(err.exception))
                    status.assert_not_awaited()
                    box_status.assert_not_awaited()
        execute.assert_not_awaited()

    async def test_fb_2223_session_status_position_offer_and_blocked_on(self) -> None:
        runtime, app = _build()
        public = runtime.identity.session_id[:12]

        async def call_status(payload: dict[str, object]) -> dict:
            status = AsyncMock(return_value=copy.deepcopy(payload))
            with patch.object(runtime, "session_status", new=status):
                return _content_json(await app.call_tool("session_status", {}))

        holding = await call_status(
            _status_payload(
                occupied=True,
                runs=[_run_row()],
                queue=[
                    {"session": public, "waiting_s": 3.0},
                    {"session": "otherWaiter1", "waiting_s": 1.0},
                ],
            )
        )
        self.assertEqual(holding["box"]["queue_position"], 1)
        self.assertIsNone(holding["box"]["queue_offer"])

        watching = await call_status(
            _status_payload(occupied=True, runs=[_run_row()], queue=[])
        )
        self.assertIsNone(watching["box"]["queue_position"])
        self.assertIsInstance(watching["box"]["queue_offer"], dict)
        self.assertIn('on_busy="queue"', watching["blocked_on"])

        free = await call_status(_status_payload(occupied=False))
        self.assertIsNone(free["box"]["queue_position"])
        self.assertIsNone(free["box"]["queue_offer"])
        self.assertIsNone(free["blocked_on"])

    @slow_test
    async def test_2223_r2_anyio_cancel_while_waiting_leaves_no_ticket(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        entered = asyncio.Event()
        _block_later_box_waits(runtime, entered)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                with anyio.CancelScope() as scope:
                    tripper = asyncio.create_task(
                        _cancel_scope_after(entered, scope)
                    )
                    try:
                        await app.call_tool(
                            "dayz_test_run",
                            {
                                "project": "ExampleMod",
                                "mode": "server",
                                "on_busy": "queue",
                                "wait_for_box_s": 30.0,
                            },
                        )
                    finally:
                        tripper.cancel()
                        try:
                            await tripper
                        except (asyncio.CancelledError, Exception):
                            pass
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_not_awaited()
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertTrue(harness.done)

    @slow_test
    async def test_2223_r2_anyio_cancel_while_launching_leaves_no_ticket_or_claim(
        self,
    ) -> None:
        config = _config()
        runtime_a = _fixture_client_runtime(config)
        runtime_b = _fixture_client_runtime(config)
        harness = _BoxHarness(occupied=False, runs=[])
        harness.bind(runtime_a)
        harness.bind(runtime_b)
        with patch.object(server, "ClientRuntime", return_value=runtime_a):
            app_a, _built_a = server.build_app(config)
        with patch.object(server, "ClientRuntime", return_value=runtime_b):
            app_b, _built_b = server.build_app(config)
        entered = asyncio.Event()

        async def execute_run(*_args: object, **_kwargs: object) -> dict[str, object]:
            entered.set()
            await asyncio.Event().wait()
            return dict(_LAUNCH_OK)

        try:
            with (
                patch.object(
                    server.dayz_test_tool,
                    "execute_dayz_test_run",
                    side_effect=execute_run,
                ),
                _fast_box_wait(),
            ):
                with anyio.CancelScope() as scope:
                    tripper = asyncio.create_task(
                        _cancel_scope_after(entered, scope)
                    )
                    try:
                        await app_a.call_tool(
                            "dayz_test_run",
                            {
                                "project": "ExampleMod",
                                "mode": "server",
                                "on_busy": "queue",
                                "wait_for_box_s": 5.0,
                            },
                        )
                    finally:
                        tripper.cancel()
                        try:
                            await tripper
                        except (asyncio.CancelledError, Exception):
                            pass
        except BaseException as exc:
            _fail_product(self, exc)
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertFalse(harness.coordinator.box_is_claimed())
        self.assertTrue(harness.done)

        async def execute_second(
            *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            return dict(_LAUNCH_OK)

        try:
            with (
                patch.object(
                    server.dayz_test_tool,
                    "execute_dayz_test_run",
                    side_effect=execute_second,
                ),
                _fast_box_wait(),
            ):
                payload = _content_json(
                    await app_b.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 1.0,
                        },
                    )
                )
        except BaseException as exc:
            _fail_product(self, exc)
        self.assertEqual(payload.get("status"), "succeeded")

    @slow_test
    async def test_2223_r2_wait_for_box_s_cancel_leaves_no_ticket(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        entered = asyncio.Event()
        _block_later_box_waits(runtime, entered)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                with anyio.CancelScope() as scope:
                    tripper = asyncio.create_task(
                        _cancel_scope_after(entered, scope)
                    )
                    try:
                        await app.call_tool(
                            "dayz_test_run",
                            {
                                "project": "ExampleMod",
                                "mode": "server",
                                "wait_for_box_s": 30.0,
                            },
                        )
                    finally:
                        tripper.cancel()
                        try:
                            await tripper
                        except (asyncio.CancelledError, Exception):
                            pass
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_not_awaited()
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertTrue(harness.done)

    @slow_test
    async def test_2223_r2_second_waiter_stays_behind_a_fresh_launch(self) -> None:
        config = _config()
        runtime_b = _fixture_client_runtime(config)
        runtime_c = _fixture_client_runtime(config)
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime_b)
        harness.bind(runtime_c)
        with patch.object(server, "ClientRuntime", return_value=runtime_b):
            app_b, _built_b = server.build_app(config)
        with patch.object(server, "ClientRuntime", return_value=runtime_c):
            app_c, _built_c = server.build_app(config)
        launches: list[str] = []

        async def execute_run(client, **_kwargs: object) -> dict[str, object]:
            launches.append(client.identity.session_id)
            if client.identity.session_id == runtime_b.identity.session_id:
                harness.occupied = True
                harness.runs = [
                    _run_row(
                        run_id="runB",
                        state="RUNNING_IDLE",
                        owner_session=None,
                        last_activity_age_s=0.5,
                        age_s=1.0,
                    )
                ]
            return dict(_LAUNCH_OK)

        try:
            with (
                patch.object(
                    server.dayz_test_tool,
                    "execute_dayz_test_run",
                    side_effect=execute_run,
                ),
                _fast_box_wait(),
            ):
                task_b = asyncio.create_task(
                    app_b.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 10.0,
                        },
                    )
                )
                await _until(
                    lambda: harness.public_in_queue(runtime_b.identity.session_id),
                    message="session B never joined",
                )
                task_c = asyncio.create_task(
                    app_c.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 10.0,
                        },
                    )
                )
                await _until(
                    lambda: len(harness.coordinator.box_queue_public()) >= 2,
                    message="session C never joined",
                )
                harness.occupied = False
                harness.runs = []
                await task_b
                await asyncio.sleep(0.2)
                self.assertEqual(
                    launches, [runtime_b.identity.session_id]
                )
                self.assertTrue(
                    harness.public_in_queue(runtime_c.identity.session_id)
                )
                harness.occupied = False
                harness.runs = []
                payload_c = _content_json(await task_c)
        except BaseException as exc:
            _fail_product(self, exc)
        self.assertEqual(payload_c.get("status"), "succeeded")
        self.assertEqual(
            launches,
            [runtime_b.identity.session_id, runtime_c.identity.session_id],
        )

    async def test_2223_r2_own_run_ends_the_wait(self) -> None:
        runtime, app = _build()
        box = _busy_box(
            runs=[
                _run_row(
                    owner_session=runtime.identity.session_id[:12],
                    state="RUNNING",
                )
            ],
        )
        box_status = AsyncMock(return_value={})
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                patch.object(
                    runtime,
                    "session_status",
                    new=AsyncMock(return_value={"box": box}),
                ),
                patch.object(runtime, "session_box_status", new=box_status),
            ):
                payload = _content_json(
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 0.3,
                        },
                    )
                )
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_not_awaited()
        box_status.assert_not_awaited()
        self.assertEqual(payload.get("reason"), "own_run")
        offer = payload.get("queue_offer")
        self.assertIsInstance(offer, dict)
        retry = offer.get("retry")
        self.assertIsInstance(retry, dict)
        self.assertNotEqual(retry.get("on_busy"), "queue")

    async def test_2223_r2_port_in_use_foreign_after_a_wait_has_no_offer(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()], foreign_ports=[2302])
        harness.bind(runtime)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                payload = _content_json(
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 5.0,
                            "port": 2302,
                        },
                    )
                )
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_not_awaited()
        self.assertEqual(payload.get("reason"), "port_in_use_foreign")
        self.assertIsNone(payload.get("queue_offer"))
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertFalse(harness.done)

    async def test_2223_r2_non_string_queue_session_does_not_break_status(self) -> None:
        runtime, app = _build()
        public = runtime.identity.session_id[:12]
        status = AsyncMock(
            return_value=_status_payload(
                occupied=True,
                runs=[_run_row()],
                queue=[
                    {"session": {"nested": True}, "waiting_s": 2.0},
                    {"session": ["x"], "waiting_s": 1.0},
                    {"session": public, "waiting_s": 0.5},
                ],
            )
        )
        try:
            with patch.object(runtime, "session_status", new=status):
                result = _content_json(await app.call_tool("session_status", {}))
        except BaseException as exc:
            _fail_product(self, exc)
        self.assertEqual(result["box"]["queue_position"], 3)

    async def test_fb_2223_stale_ownerless_idle_ends_queue_with_adopt(self) -> None:
        # Age alone no longer adopts. use_state idle at the old 120 s mark
        # stays protected; a short wait times out with an offer.
        runtime, app = _build()
        harness = _BoxHarness(
            runs=[
                _run_row(
                    state="RUNNING_IDLE",
                    owner_session=None,
                    last_activity_age_s=SESSION_TTL_S,
                    age_s=SESSION_TTL_S,
                    use_state="idle",
                    idle_s=120.0,
                )
            ]
        )
        harness.bind(runtime)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with (
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            _fast_box_wait(),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": "queue",
                        "wait_for_box_s": 0.2,
                    },
                )
            )
        execute.assert_not_awaited()
        self.assertNotEqual(payload.get("reason"), "adopt")
        self.assertEqual(payload.get("error_code"), "run_protected")
        self.assertIsNotNone(payload.get("queue_offer"))
        self.assertNotIn("takeover=true", str(payload.get("hint") or ""))

    @slow_test
    async def test_2223_r3_cancel_before_the_ticket_id_leaves_no_ticket(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        entered = asyncio.Event()
        _hang_first_box_join(runtime, entered)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                with anyio.CancelScope() as scope:
                    tripper = asyncio.create_task(
                        _cancel_scope_after(entered, scope)
                    )
                    try:
                        await app.call_tool(
                            "dayz_test_run",
                            {
                                "project": "ExampleMod",
                                "mode": "server",
                                "on_busy": "queue",
                                "wait_for_box_s": 30.0,
                            },
                        )
                    finally:
                        tripper.cancel()
                        try:
                            await tripper
                        except (asyncio.CancelledError, Exception):
                            pass
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_not_awaited()
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertIn(None, harness.done)

    @slow_test
    async def test_2223_r3_release_does_not_wait_for_the_tool_lock(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        lock_held = asyncio.Event()
        holder: asyncio.Task[None] | None = None
        execute = AsyncMock(side_effect=AssertionError("must not launch"))

        async def hold_lock() -> None:
            async with runtime.tool_lock:
                lock_held.set()
                await asyncio.sleep(8.0)

        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                with anyio.CancelScope() as scope:
                    async def trip() -> None:
                        nonlocal holder
                        await _until(
                            lambda: harness.coordinator.box_queue_public(),
                            message="waiter never joined",
                        )
                        claimed = harness.coordinator.box_wait_touch(
                            runtime.identity, "ticket-1", claim=True
                        )
                        if claimed.get("box_claimed") is not True:
                            raise AssertionError("head did not take the claim")
                        holder = asyncio.create_task(hold_lock())
                        await lock_held.wait()
                        scope.cancel()

                    tripper = asyncio.create_task(trip())
                    try:
                        await app.call_tool(
                            "dayz_test_run",
                            {
                                "project": "ExampleMod",
                                "mode": "server",
                                "on_busy": "queue",
                                "wait_for_box_s": 30.0,
                            },
                        )
                    finally:
                        tripper.cancel()
                        try:
                            await tripper
                        except (asyncio.CancelledError, Exception):
                            pass
        except BaseException as exc:
            _fail_product(self, exc)
        finally:
            if holder is not None:
                holder.cancel()
                try:
                    await holder
                except (asyncio.CancelledError, Exception):
                    pass
        execute.assert_not_awaited()
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertFalse(harness.coordinator.box_is_claimed())
        self.assertTrue(harness.done)

    async def test_2223_r3_sibling_release_keeps_the_head_claim(self) -> None:
        config = _config()
        runtime = _fixture_client_runtime(config)
        runtime_c = _fixture_client_runtime(config)
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = server.build_app(config)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        cancel_b = asyncio.Event()
        task_a: asyncio.Task[object] | None = None

        async def run_b() -> None:
            with anyio.CancelScope() as scope:
                async def trip() -> None:
                    await cancel_b.wait()
                    scope.cancel()

                tripper = asyncio.create_task(trip())
                try:
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 30.0,
                        },
                    )
                finally:
                    tripper.cancel()
                    try:
                        await tripper
                    except (asyncio.CancelledError, Exception):
                        pass

        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                task_a = asyncio.create_task(
                    app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 30.0,
                        },
                    )
                )
                await _until(
                    lambda: harness.public_in_queue(runtime.identity.session_id),
                    message="head never joined",
                )
                task_b = asyncio.create_task(run_b())
                await _until(
                    lambda: len(harness.coordinator.box_queue_public()) >= 2,
                    message="sibling never joined",
                )
                claimed = harness.coordinator.box_wait_touch(
                    runtime.identity, "ticket-1", claim=True
                )
                self.assertTrue(claimed.get("box_claimed"))
                cancel_b.set()
                try:
                    await task_b
                except BaseException as exc:
                    _fail_product(self, exc)
                self.assertTrue(harness.coordinator.box_is_claimed())
                self.assertTrue(
                    harness.coordinator.box_blocks_start(runtime_c.identity)
                )
                self.assertTrue(
                    harness.public_in_queue(runtime.identity.session_id)
                )
        except BaseException as exc:
            _fail_product(self, exc)
        finally:
            if task_a is not None:
                task_a.cancel()
                try:
                    await task_a
                except (asyncio.CancelledError, Exception):
                    pass

    @slow_test
    async def test_2223_r3_own_run_matches_any_run_in_the_snapshot(self) -> None:
        runtime, app = _build()
        box = _busy_box(
            runs=[
                _run_row(),
                _run_row(
                    run_id="runMine01",
                    owner_session=runtime.identity.session_id[:12],
                    state="RUNNING",
                ),
            ],
        )
        box_status = AsyncMock(return_value={})
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                patch.object(
                    runtime,
                    "session_status",
                    new=AsyncMock(return_value={"box": box}),
                ),
                patch.object(runtime, "session_box_status", new=box_status),
            ):
                payload = _content_json(
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 0.3,
                        },
                    )
                )
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_not_awaited()
        box_status.assert_not_awaited()
        self.assertEqual(payload.get("reason"), "own_run")

    async def test_2223_release_reaches_the_daemon_when_the_lock_is_free(self) -> None:
        runtime, _app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        joined = harness.coordinator.box_wait_touch(runtime.identity)
        ticket = joined.get("box_ticket")
        self.assertIsInstance(ticket, str)
        self.assertTrue(ticket)
        self.assertTrue(harness.public_in_queue(runtime.identity.session_id))
        await server._release_box_wait_ticket(runtime, ticket)
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertEqual(harness.done, [ticket])

    async def test_2223_r3b_test_run_without_a_ticket_releases_nothing(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            sibling = harness.coordinator.box_wait_touch(runtime.identity)
            for index in range(MAX_OPERATION_TOMBSTONES):
                other = ClientIdentity(
                    "codex",
                    200 + index,
                    1,
                    "2026-07-15T00:00:00Z",
                    f"other-session-{index:04d}",
                    "box",
                )
                joined = harness.coordinator.box_wait_touch(other)
                harness.coordinator.box_wait_touch(
                    other, joined.get("box_ticket"), done=True
                )
        except Exception as exc:
            _fail_product(self, exc)
        self.assertEqual(sibling.get("box_position"), 1)
        self.assertEqual(len(harness.coordinator.box_queue_public()), 1)
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                payload = _content_json(
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 5.0,
                        },
                    )
                )
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_not_awaited()
        self.assertEqual(len(harness.coordinator.box_queue_public()), 1)
        self.assertTrue(harness.public_in_queue(runtime.identity.session_id))
        self.assertEqual(payload.get("error_code"), "box_queue_saturated")

    @slow_test
    async def test_2223_r4_release_beats_the_join_and_still_leaves_no_ticket(
        self,
    ) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()])
        harness.bind(runtime)
        entered = asyncio.Event()
        _delay_join_so_done_can_win(runtime, entered)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                with anyio.CancelScope() as scope:
                    tripper = asyncio.create_task(
                        _cancel_scope_after(entered, scope)
                    )
                    try:
                        await app.call_tool(
                            "dayz_test_run",
                            {
                                "project": "ExampleMod",
                                "mode": "server",
                                "on_busy": "queue",
                                "wait_for_box_s": 30.0,
                            },
                        )
                    finally:
                        tripper.cancel()
                        try:
                            await tripper
                        except (asyncio.CancelledError, Exception):
                            pass
        except BaseException as exc:
            _fail_product(self, exc)
        await asyncio.sleep(0.2)
        execute.assert_not_awaited()
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertTrue(
            any(isinstance(item, str) and item for item in harness.done)
        )

    @slow_test
    async def test_2223_r4_sibling_without_the_claim_does_not_launch(self) -> None:
        runtime_a, app_a = _build()
        runtime_b = _fixture_client_runtime(_config())
        runtime_b.identity = runtime_a.identity
        _, app_b = _build(runtime=runtime_b)

        class _StillFree(_BoxHarness):
            def snapshot(self) -> dict[str, object]:
                data = super().snapshot()
                box = dict(data["box"])
                box["occupied"] = False
                box["runs"] = []
                box["ports_in_use"] = []
                out = dict(data)
                out["box"] = box
                return out

        harness = _StillFree(occupied=False, runs=[])
        harness.bind(runtime_a)
        harness.bind(runtime_b)
        launched = asyncio.Event()
        release_launch = asyncio.Event()
        wait_results: list[dict[str, object]] = []

        async def execute_run(*_args: object, **_kwargs: object) -> dict[str, object]:
            launched.set()
            await release_launch.wait()
            return dict(_LAUNCH_OK)

        async def wrapped(client, wait_s, **kwargs):
            kwargs.setdefault("poll_interval_s", 0.05)
            result = await _REAL_EXECUTE_WAIT_FOR_BOX(client, wait_s, **kwargs)
            wait_results.append(result)
            return result

        execute = AsyncMock(side_effect=execute_run)
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                patch.object(server, "execute_wait_for_box", wrapped),
            ):
                task_a = asyncio.create_task(
                    app_a.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 1.0,
                        },
                    )
                )
                task_b = asyncio.create_task(
                    app_b.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 1.0,
                        },
                    )
                )
                await asyncio.wait_for(launched.wait(), 2.0)
                done, _pending = await asyncio.wait(
                    {task_a, task_b},
                    timeout=1.5,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                self.assertEqual(execute.await_count, 1)
                self.assertEqual(sum(1 for item in wait_results if item.get("ok")), 1)
                self.assertEqual(len(done), 1)
                sibling_payload = _content_json(next(iter(done)).result())
                self.assertNotEqual(sibling_payload.get("status"), "succeeded")
                release_launch.set()
                first, second = await asyncio.gather(task_a, task_b)
            payload_a = _content_json(first)
            payload_b = _content_json(second)
        except BaseException as exc:
            _fail_product(self, exc)
        self.assertEqual(execute.await_count, 1)
        succeeded = [
            payload
            for payload in (payload_a, payload_b)
            if payload.get("status") == "succeeded"
        ]
        self.assertEqual(len(succeeded), 1)

    async def test_2223_r4_queue_wait_ends_on_a_foreign_port(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(runs=[_run_row()], foreign_ports=[2302])
        harness.bind(runtime)
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                payload = _content_json(
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 5.0,
                            "port": 2302,
                        },
                    )
                )
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_not_awaited()
        self.assertEqual(payload.get("reason"), "port_in_use_foreign")
        self.assertEqual(harness.coordinator.box_queue_public(), [])
        self.assertFalse(harness.done)

    async def test_head_claim_returns_ok_and_launches(self) -> None:
        runtime, app = _build()
        harness = _BoxHarness(occupied=False, runs=[])
        harness.bind(runtime)
        execute = AsyncMock(return_value=dict(_LAUNCH_OK))
        try:
            with (
                patch.object(
                    server.dayz_test_tool, "execute_dayz_test_run", execute
                ),
                _fast_box_wait(),
            ):
                payload = _content_json(
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                            "wait_for_box_s": 5.0,
                        },
                    )
                )
        except BaseException as exc:
            _fail_product(self, exc)
        execute.assert_awaited()
        self.assertEqual(payload.get("status"), "succeeded")
        self.assertEqual(payload.get("run_id"), _LAUNCH_OK["run_id"])


if __name__ == "__main__":
    unittest.main()
