"""inbox 3997 (fb-20261001-000925-3997): a launch that loses the box after its build.

2026-09-30 22:50:20Z, SimpleGroup, dayz_test_run(mode=all, build=true) with no
on_busy: the build took four minutes, and meanwhile a waiter of the box FIFO
claimed the box. The daemon refused the start (lifecycle_start_rejected
box_claimed, twice), the worker stopped a run that never existed (run_not_found),
and the call answered worker_failed, cleanup_degraded=true and a run_id that
dayz_test_stop could not find.

The cause was on the daemon side. Since #131 every lifecycle reply carries the
renewed lease_id, and the sealed worker republishes active_run_exists only when
the refusal is exactly {"error": ...}. The fixes, one per layer:

* the daemon: a start refused before admission is not stamped with lease_id;
* dayz_test_tool: a failed launch drops a run_id the run store does not list;
* dayz_test_run: the refusal names the box holder and the queue offer, and
  on_busy="queue" waits in the FIFO again and launches without rebuilding,
  within the wait budget.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import mcp_capture
from dayz_mcp import (
    box_occupancy,
    dayz_test_readiness,
    dayz_test_request,
    dayz_test_tool,
    dayz_test_worker,
    loopback,
    native_broker_protocol,
    native_launcher_transaction,
    server,
    steam_preflight,
)
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests._tiers import slow_test
from tests.client_helpers import _fixture_client_runtime
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)
from tests.lifecycle_helpers import (
    IDENTITY,
    IDENTITY_B,
    IDENTITY_PAYLOAD,
    Clock,
    LifecycleFixture,
    identity,
    record,
)
from tests.mcp_helpers import _content_json

_REAL_EXECUTE_WAIT_FOR_BOX = server.execute_wait_for_box
_OPERATION_ID = "87654321-4321-4321-8321-ba0987654321"
# The session that took the box in the incident, and the run it launched.
_TAKER = ClientIdentity(
    "claude",
    22,
    1,
    "2026-09-30T22:46:33Z",
    "a4317999-9c5d-4e1f-8a2b-0123456789ab",
    "fifo",
)
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


def _claim_box(coordinator: SessionCoordinator, client: ClientIdentity) -> None:
    joined = coordinator.box_wait_touch(client)
    claimed = coordinator.box_wait_touch(client, joined["box_ticket"], claim=True)
    if claimed.get("box_claimed") is not True:
        raise AssertionError(f"{client.session_id} did not claim the box")


# --- daemon: what the start route hands the sealed worker -------------------


def _lifecycle_reply(
    state: loopback.ServerState, action: str, body: dict[str, object]
) -> tuple[int, dict[str, object]]:
    """One /lifecycle/<action> through the daemon's real handler, no socket.

    The body crosses JSON both ways, as it does on the wire.
    """
    handler = loopback.Handler.__new__(loopback.Handler)
    handler.server = types.SimpleNamespace(state=state, log_sink=lambda _m: None)
    replies: list[tuple[int, dict[str, object]]] = []
    handler._read_json = lambda: json.loads(json.dumps(body))
    handler._json = lambda code, payload: replies.append(
        (code, json.loads(json.dumps(payload)))
    )
    handler._handle_lifecycle(action)
    if len(replies) != 1:
        raise AssertionError(f"{action} replied {len(replies)} times")
    return replies[0]


class _DaemonBroker:
    """The native broker the sealed worker talks to, over the real daemon.

    LIFECYCLE_CLI frames become the body app_main._lifecycle_main posts, and
    the reply body comes back whatever its HTTP status, as app_main returns it.
    ADDON_BUILDER answers a successful build: the incident's PBO was rebuilt.
    """

    def __init__(self, state: loopback.ServerState, token: str) -> None:
        self.state = state
        self.token = token
        self.commands: list[str] = []

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        if request.kind is native_broker_protocol.BrokerKind.ADDON_BUILDER:
            self.commands.append("build")
            return {"ok": True, "exit_code": 0, "pbo_size": 8192}
        command = str(request.payload["command"])
        self.commands.append(command)
        body: dict[str, object] = {
            "identity": IDENTITY_PAYLOAD,
            "lease_token": self.token,
        }
        if command == "start":
            body["request"] = json.loads(request.stdin)
        elif command in {"stop", "adopt", "reap"}:
            body["run_id"] = request.payload["run_id"]
        elif command == "ack":
            body["run_id"] = request.payload["run_id"]
            body["launch_operation_id"] = request.payload["launch_operation_id"]
        elif request.payload["run_id"] is not None:
            body["run_id"] = request.payload["run_id"]
        _status, reply = _lifecycle_reply(self.state, command, body)
        return reply


def _worker_runtime(game: Path) -> dayz_test_worker.WorkerRuntimePolicy:
    policy = _policy()
    missions = policy.dev_root + r"\_server\mpmissions"
    return dayz_test_worker.WorkerRuntimePolicy(
        dev_root=policy.dev_root,
        mod=policy.mod,
        diag_executable=str(game / "DayZDiag_x64.exe"),
        game_directory=str(game),
        mission_aliases=(
            ("chernarus", missions + r"\dayzOffline.chernarusplus"),
            ("livonia", missions + r"\dayzOffline.enoch"),
            ("sakhal", missions + r"\dayzOffline.sakhal"),
        ),
        mods_root=r"P:\Mods",
        build_temp_root=r"P:\temp",
        build_source_basename=None,
    )


async def _ready(_run_id: str, _port: int, _timeout_s: int) -> object:
    return dayz_test_readiness.ReadinessResult(ready=True, error_code=None)


class DaemonStartRefusalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = LifecycleFixture()
        self.addCleanup(self.fixture.close)
        self.state = loopback.ServerState("key", coordination=self.fixture.coordinator)
        self.state.lifecycle = self.fixture.lifecycle

    def _box_claim_rejections(self) -> list[object]:
        return [
            event.get("reason")
            for event in self.fixture.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]

    def test_a_start_refused_before_admission_is_the_bare_error(self) -> None:
        _claim_box(self.fixture.coordinator, IDENTITY_B)

        status, body = _lifecycle_reply(
            self.state,
            "start",
            {
                "identity": IDENTITY_PAYLOAD,
                "lease_token": self.fixture.token,
                "request": self.fixture.request(),
            },
        )

        # authorize renewed the lease, as it does for every mutation; the
        # refusal still reaches the worker in the one shape it classifies.
        self.assertEqual((status, body), (409, {"error": "active_run_exists"}))
        self.assertEqual(self._box_claim_rejections(), ["box_claimed"])

    def test_other_lifecycle_replies_keep_the_renewed_lease(self) -> None:
        status, body = _lifecycle_reply(
            self.state,
            "stop",
            {
                "identity": IDENTITY_PAYLOAD,
                "lease_token": self.fixture.token,
                "run_id": "missing",
            },
        )

        self.assertEqual(
            (status, body),
            (404, {"error": "run_not_found", "lease_id": self.fixture.lease_id}),
        )

    def test_a_start_that_registers_a_run_keeps_the_renewed_lease(self) -> None:
        launched = record(self.fixture.launcher.handle.pid)
        self.fixture.guard.snapshots[launched.pid] = identity(launched)

        status, body = _lifecycle_reply(
            self.state,
            "start",
            {
                "identity": IDENTITY_PAYLOAD,
                "lease_token": self.fixture.token,
                "request": self.fixture.request(),
            },
        )

        self.assertEqual((status, body.get("state")), (200, "RUNNING"))
        self.assertEqual(body.get("lease_id"), self.fixture.lease_id)

    def test_the_worker_answers_active_run_exists_after_its_build(self) -> None:
        """The incident end to end: real daemon refusal, real sealed worker."""
        _claim_box(self.fixture.coordinator, IDENTITY_B)
        broker = _DaemonBroker(self.state, self.fixture.token)
        policy = _policy()
        parsed = dayz_test_request.parse_dayz_test_request(
            json.dumps(
                {
                    "version": 1,
                    "dev_root": policy.dev_root,
                    "mod": policy.mod,
                    "mode": "all",
                    "build": True,
                    "extra_mods": ["@DayZ_MCP"],
                }
            ).encode("utf-8"),
            policies=(policy,),
        )
        ids = iter((RUN_ID, _OPERATION_ID))

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            asyncio.run(
                dayz_test_worker.execute_dayz_test_worker(
                    parsed.canonical_bytes,
                    request_sha256=parsed.sha256,
                    request_policies=(policy,),
                    runtime_policy=_worker_runtime(self.fixture.game),
                    broker=broker,
                    id_fn=lambda: next(ids),
                    readiness_probe=_ready,
                    has_binarizable_assets=lambda _source: True,
                )
            )

        self.assertEqual(raised.exception.code, "active_run_exists")
        self.assertIsNone(raised.exception.run_id)
        self.assertFalse(raised.exception.cleanup_degraded)
        # One start, no second start and no stop of a run that never existed.
        self.assertEqual(broker.commands, ["build", "start"])
        self.assertEqual(self._box_claim_rejections(), ["box_claimed"])
        self.assertEqual(self.fixture.lifecycle.status(IDENTITY)["runs"], [])


# --- dayz_test_tool: no run_id the store does not know ----------------------


class UnknownRunIdTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for target, value in (
            (
                "evaluate_steam_session",
                steam_preflight.SteamSessionResult(
                    error_code=None,
                    steam_registered_pid=1,
                    steam_live_pids=(1,),
                    remediation=steam_preflight.REMEDIATION,
                ),
            ),
            (
                "evaluate_prerun_desktop",
                mcp_capture.PrerunDesktopResult(
                    error_code=None,
                    desktop="unlocked",
                    mean_brightness=80.0,
                    nonblack_ratio=0.9,
                    waited_s=0.01,
                    remediation="",
                ),
            ),
            (
                "preflight_vpp_request",
                native_launcher_transaction.VppPreflightResult(
                    error_code=None,
                    missing=(),
                    warnings=(),
                    hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
                ),
            ),
        ):
            patcher = patch.object(dayz_test_tool, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _run(self, runtime: _Runtime) -> dict[str, object]:
        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            # The terminal of the incident: the worker could not confirm the
            # stop of the run it minted, so it names that run.
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": True,
                        "error_code": "worker_failed",
                        "exit_code": 2,
                        "ok": False,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 2

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(_policy())),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                build=True,
                extra_mods=["@DayZ_MCP"],
            )

    async def test_a_run_id_the_store_does_not_list_is_not_returned(self) -> None:
        result = await self._run(_Runtime(lifecycle={"runs": []}))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "worker_failed")
        self.assertIsNone(result["run_id"])
        # Nothing of that run exists to clean up, and the claim travels with
        # the id it is about.
        self.assertFalse(result["cleanup_degraded"])

    async def test_a_run_the_store_lists_keeps_its_id_and_cleanup_claim(self) -> None:
        listed = {"run_id": RUN_ID, "state": "UNRECONCILED", "processes": []}

        result = await self._run(_Runtime(lifecycle={"runs": [listed]}))

        self.assertEqual(result["run_id"], RUN_ID)
        self.assertTrue(result["cleanup_degraded"])

    async def test_an_unreadable_status_keeps_the_id(self) -> None:
        runtime = _Runtime()
        runtime.lifecycle_status = AsyncMock(side_effect=RuntimeError("down"))

        result = await self._run(runtime)

        self.assertEqual(result["run_id"], RUN_ID)
        self.assertTrue(result["cleanup_degraded"])


# --- dayz_test_run: the answer, and the wait on_busy=queue asked for ---------


def _refused() -> dict[str, object]:
    """dayz_test_tool's envelope for a launch the daemon refused."""
    return {
        "status": "failed",
        "project": "ExampleMod",
        "mode": "all",
        "run_id": None,
        "phase": "executing",
        "elapsed_s": 241.0,
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
        "elapsed_s": 30.0,
        "artifacts_paths": [],
        "error_code": None,
        "cleanup_degraded": False,
    }


class _Box:
    """A real SessionCoordinator box FIFO plus the occupancy the daemon reads."""

    def __init__(self) -> None:
        self.seq = 0
        self.runs: list[dict[str, object]] = []
        self.joins = 0
        self.busy_answers = 0
        self.done: list[object] = []
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
            if kwargs.get("done"):
                self.done.append(kwargs.get("ticket"))
            elif kwargs.get("wait") and not kwargs.get("ticket"):
                self.joins += 1
            touched = self.coordinator.box_wait_touch(
                caller,
                kwargs.get("ticket"),
                done=bool(kwargs.get("done")),
                claim=bool(kwargs.get("claim")),
            )
            snap = self.snapshot()
            polled = kwargs.get("wait") and not (kwargs.get("done") or kwargs.get("claim"))
            if polled and snap["box"]["occupied"]:
                self.busy_answers += 1
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


class BoxHolderSessionTest(unittest.TestCase):
    _CALLER = "c0ffee00-1111-4222-8333-444455556666"

    @staticmethod
    def _claimed(head: str, **extra: object) -> dict[str, object]:
        box: dict[str, object] = {
            "occupied": True,
            "runs": [],
            "foreign": [],
            "queue": [{"session": head[:12], "waiting_s": 1.0}],
            "claimed_s": 3.0,
        }
        box.update(extra)
        return box

    def _holder(self, box: object) -> object:
        return box_occupancy._box_holder_session(box, self._CALLER)

    def test_another_sessions_claim_names_it(self) -> None:
        self.assertEqual(
            self._holder(self._claimed(_TAKER.session_id)), "a4317999-9c5"
        )

    def test_the_callers_own_claim_names_nobody(self) -> None:
        # The answer is built before the caller leaves the FIFO.
        self.assertIsNone(self._holder(self._claimed(self._CALLER)))

    def test_a_run_comes_before_the_claim(self) -> None:
        box = self._claimed(self._CALLER, runs=[dict(_TAKER_RUN)])
        self.assertEqual(self._holder(box), "a4317999-9c5")

    def test_an_ownerless_run_names_its_launcher(self) -> None:
        run = dict(
            _TAKER_RUN,
            state="RUNNING_IDLE",
            owner_session=None,
            launched_by={"session": "b5528aaa-0d6"},
        )
        box = {"occupied": True, "runs": [run], "foreign": [], "queue": []}
        self.assertEqual(self._holder(box), "b5528aaa-0d6")

    def test_a_foreign_dayz_or_an_unreadable_box_names_nobody(self) -> None:
        foreign = {
            "occupied": True,
            "runs": [],
            "foreign": [{"port": 2302, "mods": ["@CF"]}],
            "queue": [],
        }
        self.assertIsNone(self._holder(foreign))
        self.assertIsNone(self._holder(None))


class BoxWasFreeForTest(unittest.TestCase):
    _CALLER = BoxHolderSessionTest._CALLER

    def _free(self, box: object) -> bool:
        return box_occupancy._box_was_free_for(box, self._CALLER)

    def test_a_free_box_and_the_callers_own_claim_are_free(self) -> None:
        self.assertTrue(self._free({"occupied": False, "runs": [], "foreign": []}))
        self.assertTrue(self._free(BoxHolderSessionTest._claimed(self._CALLER)))

    def test_anything_else_is_not(self) -> None:
        own = BoxHolderSessionTest._claimed
        for label, box in (
            ("another session's claim", own(_TAKER.session_id)),
            ("a run under the own claim", own(self._CALLER, runs=[dict(_TAKER_RUN)])),
            ("a foreign DayZ", own(self._CALLER, foreign=[{"port": 2302}])),
            ("unknown DayZ scan", own(self._CALLER, scan_known=False)),
            ("unknown port scan", own(self._CALLER, port_scan_known=False)),
            ("claimed without an age", own(self._CALLER, claimed_s=None)),
            ("no occupancy", {"runs": [], "foreign": []}),
            ("not a box", None),
        ):
            with self.subTest(label):
                self.assertFalse(self._free(box))


class BoxHeldByAnotherTest(unittest.TestCase):
    """The positive proof a refusal needs before it is called a lost box."""

    _CALLER = BoxHolderSessionTest._CALLER

    def _held(self, box: object) -> bool:
        return box_occupancy._box_held_by_another(box, self._CALLER)

    def test_another_run_claim_or_unmanaged_dayz_is_proof(self) -> None:
        claimed = BoxHolderSessionTest._claimed
        ownerless = dict(
            _TAKER_RUN,
            state="RUNNING_IDLE",
            owner_session=None,
            launched_by={"session": "b5528aaa-0d6"},
        )
        for label, box in (
            ("another session's run", {"runs": [dict(_TAKER_RUN)]}),
            ("an ownerless run another session launched", {"runs": [ownerless]}),
            ("another session's claim", claimed(_TAKER.session_id)),
            ("an unmanaged DayZ", {"runs": [], "foreign": [{"port": None}]}),
        ):
            with self.subTest(label):
                self.assertTrue(self._held(box))

    def test_nothing_that_holds_it_now_is_no_proof(self) -> None:
        claimed = BoxHolderSessionTest._claimed
        own_run = dict(_TAKER_RUN, owner_session=self._CALLER[:12])
        for label, box in (
            ("a free box", {"occupied": False, "runs": [], "foreign": [], "queue": []}),
            ("the caller's own claim", claimed(self._CALLER)),
            ("the caller's own run", {"runs": [own_run]}),
            (
                "a port held by a process that is not DayZ",
                {"occupied": False, "runs": [], "foreign": [], "foreign_ports_all": [2302]},
            ),
            ("an unreadable box", {"occupied": True, "runs": [], "foreign": [], "scan_known": False}),
            ("not a box", None),
        ):
            with self.subTest(label):
                self.assertFalse(self._held(box))


class BoxTakenDuringBuildTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._build()

    def _build(self, **runtime_kwargs: object) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config, **runtime_kwargs)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = server.build_app(config)
        self.box = _Box()
        self.box.bind(self.runtime)
        self.launches: list[dict[str, object]] = []
        self.caller = str(self.runtime.identity.session_id)

    def _take_box(self, *, launch: bool = True) -> None:
        # Another session claims the box while this call builds, and launches.
        _claim_box(self.box.coordinator, _TAKER)
        if launch:
            self.box.runs = [dict(_TAKER_RUN)]

    async def _call(self, execute, **arguments: object) -> dict[str, object]:
        async def recording(_client, **kwargs: object) -> dict[str, object]:
            self.launches.append(
                {key: kwargs[key] for key in ("build", "clean", "pack_only")}
            )
            return await execute(len(self.launches))

        with patch.object(
            server.dayz_test_tool, "execute_dayz_test_run", side_effect=recording
        ), _fast_box_wait():
            return _content_json(
                await self.app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "all", **arguments},
                )
            )

    def _queue_empty(self) -> bool:
        return self.box.coordinator.box_queue_public() == []

    async def test_without_on_busy_the_answer_names_the_holder_and_the_fifo(
        self,
    ) -> None:
        async def execute(attempt: int) -> dict[str, object]:
            self._take_box()
            return _refused()

        payload = await self._call(execute, build=True)

        self.assertEqual(self.launches, [{"build": True, "clean": False, "pack_only": False}])
        self.assertEqual(payload["status"], "failed")
        # The holder's run is RUNNING and owned by another session, and still
        # this is not takeover_required: that run won the box while this call
        # built.
        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertEqual(payload["reason"], "box_taken_during_build")
        self.assertIsNone(payload["run_id"])
        self.assertFalse(payload["cleanup_degraded"])
        self.assertEqual(payload["occupied_by_run_id"], _TAKER_RUN["run_id"])
        self.assertEqual(payload["occupied_by_session"], "a4317999-9c5")
        self.assertIn("build=false", payload["hint"])
        self.assertIn("on_busy='queue'", payload["hint"])
        offer = payload["queue_offer"]
        self.assertEqual(offer["waiters"], 1)
        self.assertEqual(offer["occupant"]["run_id"], _TAKER_RUN["run_id"])
        self.assertEqual(
            offer["retry"],
            {
                "tool": "dayz_test_run",
                "same_args": True,
                "on_busy": "queue",
                "build": False,
                "clean": False,
                "pack_only": False,
            },
        )
        self.assertNotIn("box_requeued", payload)

    async def test_a_claim_without_a_run_yet_is_named_by_its_session(self) -> None:
        # The incident's timing: the refusal is read before the claimant's
        # own launch registered its run.
        async def execute(attempt: int) -> dict[str, object]:
            self._take_box(launch=False)
            return _refused()

        payload = await self._call(execute, build=True)

        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertEqual(payload["reason"], "box_taken_during_build")
        self.assertIsNone(payload["occupied_by_run_id"])
        self.assertEqual(payload["occupied_by_session"], "a4317999-9c5")
        self.assertEqual(payload["queue_offer"]["waiters"], 1)

    async def test_without_a_build_the_reason_says_before_launch(self) -> None:
        async def execute(attempt: int) -> dict[str, object]:
            self._take_box()
            return _refused()

        payload = await self._call(execute)

        self.assertEqual(payload["reason"], "box_taken_before_launch")
        self.assertNotIn("build=false", payload["hint"])
        self.assertEqual(
            payload["queue_offer"]["retry"],
            {"tool": "dayz_test_run", "same_args": True, "on_busy": "queue"},
        )

    async def test_a_box_already_claimed_when_the_call_starts_keeps_its_answer(
        self,
    ) -> None:
        self._take_box(launch=False)

        async def execute(attempt: int) -> dict[str, object]:
            return _refused()

        payload = await self._call(execute, build=True)

        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertIsNone(payload["reason"])
        self.assertNotIn("occupied_by_session", payload)
        self.assertTrue(
            str(payload["hint"]).startswith("retry with wait_for_box_s=<n>")
        )

    @slow_test
    async def test_queue_waits_again_and_launches_without_rebuilding(self) -> None:
        refused = asyncio.Event()
        freed = asyncio.Event()
        busy_at_refusal: list[int] = []

        async def execute(attempt: int) -> dict[str, object]:
            if attempt == 1:
                self.box.runs = [dict(_TAKER_RUN)]
                busy_at_refusal.append(self.box.busy_answers)
                refused.set()
                return _refused()
            self.assertTrue(freed.is_set(), "launched before the box was free")
            return _launched()

        async def free_once_requeued() -> None:
            await refused.wait()
            # The re-join is answered with the taker's run still there.
            await _until(lambda: self.box.joins >= 2, message="never re-joined")
            self.box.runs = []
            freed.set()

        payload, _freed = await asyncio.gather(
            self._call(
                execute,
                build=True,
                clean=True,
                on_busy="queue",
                wait_for_box_s=5.0,
            ),
            free_once_requeued(),
        )

        self.assertEqual(
            self.launches,
            [
                {"build": True, "clean": True, "pack_only": False},
                {"build": False, "clean": False, "pack_only": False},
            ],
        )
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertIs(payload["box_requeued"], True)
        self.assertEqual(self.box.joins, 2)
        # The second FIFO wait saw the box busy before it could claim it.
        self.assertGreater(self.box.busy_answers, busy_at_refusal[0])
        self.assertEqual(len(self.box.done), 2)
        self.assertTrue(self._queue_empty())

    async def test_queue_does_not_wait_again_once_the_budget_is_spent(self) -> None:
        async def execute(attempt: int) -> dict[str, object]:
            await asyncio.sleep(0.02)  # the build outlasts wait_for_box_s
            self.box.runs = [dict(_TAKER_RUN)]
            return _refused()

        payload = await self._call(
            execute, build=True, on_busy="queue", wait_for_box_s=0.001
        )

        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.box.joins, 1)
        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertEqual(payload["reason"], "box_taken_during_build")
        self.assertNotIn("box_requeued", payload)
        self.assertTrue(self._queue_empty())

    async def test_a_refusal_no_holder_explains_stays_plain_and_is_not_requeued(
        self,
    ) -> None:
        # Refused while the box read after shows only this call's own claim:
        # whatever refused the launch (a port holder that has exited, say) is
        # not observable, so no lost box, no FIFO offer and no second launch,
        # although the wait budget is far from spent.
        async def execute(attempt: int) -> dict[str, object]:
            return _refused()

        payload = await self._call(
            execute, build=True, on_busy="queue", wait_for_box_s=5.0
        )

        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.box.joins, 1)
        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertIsNone(payload["reason"])
        self.assertIsNone(payload["queue_offer"])
        self.assertNotIn("occupied_by_session", payload)
        self.assertNotIn("box_requeued", payload)
        self.assertTrue(self._queue_empty())

    async def test_a_port_refusal_with_the_box_free_before_and_after_is_plain(
        self,
    ) -> None:
        """A process that is not DayZ holds the requested port, then exits.

        The daemon refuses that launch from a fresh socket probe
        (_foreign_port_reason), apart from box occupancy: the box reads free
        while the holder is there and after it has gone.
        """
        fixture = LifecycleFixture()
        self.addCleanup(fixture.close)
        state = loopback.ServerState("key", coordination=fixture.coordinator)
        state.lifecycle = fixture.lifecycle
        holder = {"port": 2302, "pid": 999998, "name": "python.exe"}
        request = fixture.request()
        request["argv"].append("-port=2302")
        with patch.object(
            fixture.lifecycle, "_port_holders", return_value=(None, [holder])
        ):
            before = fixture.lifecycle.box_occupancy(now=time.time())
            status, body = _lifecycle_reply(
                state,
                "start",
                {
                    "identity": IDENTITY_PAYLOAD,
                    "lease_token": fixture.token,
                    "request": request,
                },
            )
        with patch.object(fixture.lifecycle, "_port_holders", return_value=(None, [])):
            after = fixture.lifecycle.box_occupancy(now=time.time())

        self.assertEqual((status, body), (409, {"error": "active_run_exists"}))
        self.assertEqual(
            [
                event.get("reason")
                for event in fixture.audit.events
                if event.get("event") == "lifecycle_start_rejected"
            ],
            ["port_in_use_foreign"],
        )
        self.assertIs(before["occupied"], False)
        self.assertIs(after["occupied"], False)
        self.assertEqual(after["foreign_ports_all"], [])

        # The worker carries that exact body as active_run_exists (see
        # test_the_worker_answers_active_run_exists_after_its_build); here it
        # reaches dayz_test_run over a box that reads free before and after.
        async def execute(attempt: int) -> dict[str, object]:
            return _refused()

        for arguments in ({}, {"on_busy": "queue", "wait_for_box_s": 5.0}):
            with self.subTest(**arguments):
                self.launches.clear()
                payload = await self._call(execute, build=True, **arguments)

                self.assertEqual(len(self.launches), 1)
                self.assertEqual(payload["error_code"], "active_run_exists")
                self.assertIsNone(payload["reason"])
                self.assertIsNone(payload["queue_offer"])
                self.assertNotIn("occupied_by_session", payload)
                self.assertNotIn("box_requeued", payload)
                self.assertTrue(self._queue_empty())

    async def test_a_slow_release_does_not_stretch_the_wait_budget(self) -> None:
        clock = Clock()
        clock.now = 1000.0
        self._build(time_fn=clock)
        release = server._release_box_wait_ticket

        async def slow_release(client: object, ticket: object) -> None:
            # Leaving the FIFO outlasts what was left of wait_for_box_s, and
            # the run that took the box is gone by then.
            clock.advance(10.0)
            self.box.runs = []
            await release(client, ticket)

        async def execute(attempt: int) -> dict[str, object]:
            self.box.runs = [dict(_TAKER_RUN)]
            return _refused()

        with patch.object(
            server, "_release_box_wait_ticket", side_effect=slow_release
        ):
            payload = await self._call(
                execute, build=True, on_busy="queue", wait_for_box_s=5.0
            )

        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.box.joins, 1)
        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertEqual(payload["reason"], "box_taken_during_build")
        self.assertEqual(payload["occupied_by_session"], "a4317999-9c5")
        self.assertNotIn("box_requeued", payload)
        self.assertTrue(self._queue_empty())

    async def test_queue_without_contention_launches_once_with_its_build(self) -> None:
        async def execute(attempt: int) -> dict[str, object]:
            return _launched()

        payload = await self._call(
            execute, build=True, on_busy="queue", wait_for_box_s=5.0
        )

        self.assertEqual(
            self.launches, [{"build": True, "clean": False, "pack_only": False}]
        )
        self.assertEqual(payload["status"], "succeeded")
        self.assertNotIn("box_requeued", payload)
        self.assertEqual(self.box.joins, 1)
        self.assertTrue(self._queue_empty())

    @slow_test
    async def test_queue_that_times_out_again_answers_the_lost_box(self) -> None:
        async def execute(attempt: int) -> dict[str, object]:
            self.box.runs = [dict(_TAKER_RUN)]
            return _refused()

        payload = await self._call(
            execute, build=True, on_busy="queue", wait_for_box_s=0.3
        )

        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.box.joins, 2)
        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertEqual(payload["reason"], "box_taken_during_build")
        self.assertIs(payload["box_requeued"], True)
        self.assertIsNone(payload["run_id"])
        self.assertEqual(payload["occupied_by_session"], "a4317999-9c5")
        self.assertTrue(self._queue_empty())

    async def test_the_description_says_what_a_lost_box_does(self) -> None:
        description = self.app._tool_manager.get_tool("dayz_test_run").description
        for phrase in (
            "box_taken_during_build",
            "box_taken_before_launch",
            "occupied_by_session",
            "re-enters the FIFO once",
            "launches without rebuilding (box_requeued: true)",
            "the box read after the refusal showing who holds it",
            "nothing visible explains any more stays active_run_exists",
            "returns run_id null",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)


if __name__ == "__main__":
    unittest.main()
