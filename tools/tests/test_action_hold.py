"""action_hold: schema, dispatch, capability, observation, lease and cleanup.

Text asserts on Enforce are wiring only. They do not credit in-game behavior.
"""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.action_hold import (
    hold_command_args,
    normalize_hold_observation,
    parse_action_selector,
    validate_hold_budget,
)
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.result_prune import prune_unfilled_fields
from dayz_mcp.server import ServerConfig, ToolError, build_app
from dayz_mcp.session_coordination import (
    ClientIdentity,
    SessionCoordinator,
    command_requires_lease,
)
from tests._addon_paths import addon_root
from tests.fence_helpers import (
    INST_CLIENT,
    PID_CLIENT,
    accredited_poll,
    bind_both_peers,
    poll_census_query,
)
from tests.lease_helpers import SequentialIds


COMMAND = "action_hold"
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
HOLD_C = addon_root() / "scripts" / "4_World" / "MCP_ActionHold.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
IDENTITY = {
    "platform": "codex",
    "pid": 4242,
    "ppid": 10,
    "started_at_utc": "2026-07-14T20:00:01Z",
    "session_id": "session-hold",
    "task_label": "hold",
}


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    if message.startswith(wrapper):
        return message[len(wrapper) :]
    return message


def _status(commands: list[str] | None, *, state: str = "announced") -> dict:
    capabilities: dict = {"state": state}
    if commands is not None:
        capabilities["announced_commands"] = commands
    return {"client_peer": {"capabilities": capabilities}}


def _observation(**overrides: object) -> dict:
    body = {
        "ok": 1,
        "hold_protocol": 1,
        "hold_id": "minted",
        "started": 1,
        "end_state": "finished",
        "reason": "natural",
        "action_state": 4,
        "action_state_known": 1,
        "duration_s": 1.5,
        "completed_cycles": 0,
        "cycles_scope": "client_progress",
        "placed_known": 1,
        "already_placed": 0,
        "flag_restored": 1,
        "cleanup_complete": 1,
    }
    body.update(overrides)
    return body


class HoldSchemaTest(unittest.TestCase):
    def test_bridge_and_python_expect_version_12(self) -> None:
        self.assertEqual(EXPECTED_BRIDGE_VERSION, "12")
        text = MESSAGES.read_text(encoding="utf-8")
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', text)

    def test_budget_rejects_bool_nan_and_bad_margin(self) -> None:
        hold, timeout = validate_hold_budget(30, 45)
        self.assertEqual((hold, timeout), (30.0, 45.0))
        for hold_s, timeout_s in (
            (True, 45),
            (1, True),
            (float("nan"), 45),
            (1, float("inf")),
            (0, 45),
            (121, 200),
            (30, 44),
            (30, 301),
        ):
            with self.subTest(hold_s=hold_s, timeout_s=timeout_s):
                with self.assertRaises(ToolError):
                    validate_hold_budget(hold_s, timeout_s)

    def test_ingress_selector_is_closed_and_index_absence_is_not_zero(self) -> None:
        ok, _err = loopback.validate_command_args(
            COMMAND, {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": 1.0}
        )
        self.assertTrue(ok)
        refused = [
            {"action": "ActionDeployObject", "hold_timeout_s": 1.0},
            {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": True},
            {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": float("nan")},
            {"action": "ActionDeployObject", "selector": "elsewhere", "hold_timeout_s": 1.0},
            {
                "action": "ActionDeployObject",
                "selector": "door",
                "classname": "Land_Barn",
                "door_index": 0,
                "hold_timeout_s": 1.0,
            },
            {
                "action": "ActionDeployObject",
                "selector": "component",
                "classname": "Land_Barn",
                "component_index": 0,
                "cursor_pos": [1.0, 2.0, 3.0],
                "hold_timeout_s": 1.0,
            },
        ]
        for args in refused:
            with self.subTest(args=args):
                valid, error = loopback.validate_command_args(COMMAND, args)
                self.assertFalse(valid)
                self.assertEqual(error, "bad_args")
        door_ok, _ = loopback.validate_command_args(
            COMMAND,
            {
                "action": "ActionOpenDoors",
                "selector": "door",
                "classname": "Land_Barn",
                "door_index": 0,
                "door_index_set": True,
                "hold_timeout_s": 1.0,
            },
        )
        self.assertTrue(door_ok)

    def test_not_in_read_only(self) -> None:
        self.assertTrue(command_requires_lease(COMMAND))
        self.assertTrue(command_requires_lease("action_hold_cancel"))
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.READ_ONLY_COMMANDS) if hasattr(
            loopback, "READ_ONLY_COMMANDS"
        ) else None


class HoldDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_five_selectors_one_command_and_a_real_hands_item(self) -> None:
        app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        seen: list[dict] = []

        async def enqueue(args, timeout_s):
            seen.append(dict(args))
            return 7, "minted"

        async def await_hold(_command_id, minted, _timeout_s):
            self.assertEqual(minted, "minted")
            return _observation()

        cases = [
            ({"action": "ActionDeployObject"}, "world", None),
            ({"action": "ActionDeployObject", "classname": "FenceKit", "target": "hands"}, "hands", None),
            ({"action": "ActionBandageSelf", "target": "self"}, "self", None),
            (
                {
                    "action": "ActionOpenDoors",
                    "classname": "Land_Barn",
                    "door_index": 0,
                },
                "door",
                "door_index_set",
            ),
            (
                {
                    "action": "ActionWorldLiquidActionSwitch",
                    "classname": "Land_Misc_Well",
                    "component_index": 0,
                    "cursor_pos": [1.0, 2.0, 3.0],
                },
                "component",
                "component_index_set",
            ),
        ]
        with (
            patch.object(runtime, "bridge_status_payload", new=AsyncMock(return_value=_status([COMMAND]))),
            patch.object(runtime, "enqueue_action_hold", new=enqueue),
            patch.object(runtime, "await_action_hold", new=await_hold),
        ):
            for arguments, selector, flag in cases:
                with self.subTest(selector=selector):
                    before = len(seen)
                    await app.call_tool(COMMAND, {**arguments, "hold_timeout_s": 1.0, "timeout_s": 16.0})
                    self.assertEqual(len(seen), before + 1)
                    sent = seen[-1]
                    self.assertEqual(sent["selector"], selector)
                    self.assertEqual(
                        loopback.validate_command_args(COMMAND, sent)[0], True, sent
                    )
                    self.assertNotIn("cmd", sent)
                    if flag is not None:
                        self.assertIs(sent[flag], True)
                    else:
                        self.assertNotIn("door_index_set", sent)
                        self.assertNotIn("component_index_set", sent)
        bridge = BRIDGE.read_text(encoding="utf-8")
        body = _method_body(bridge, "protected bool DispatchActionUse(")
        self.assertIn("player.GetItemInHands()", body)
        self.assertIn('command.cmd == "action_hold"', body)
        self.assertIn("SetIgnoreAutomaticInputEnd", HOLD_C.read_text(encoding="utf-8"))
        # The shared starter sets the flag only after the hold arm, not for action_use.
        use_only = body.split("if (holdCommand)")[0]
        self.assertNotIn("SetIgnoreAutomaticInputEnd", use_only)

    async def test_action_use_still_dispatches_action_use(self) -> None:
        app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        call = AsyncMock(return_value={"ok": 1, "started": True})
        with patch.object(runtime, "call_bridge", new=call):
            await app.call_tool("action_use", {"action": "ActionOpenDoors", "timeout_s": 1.0})
        self.assertEqual(call.await_args.args[0], "action_use")
        self.assertNotIn("selector", call.await_args.args[1])
        self.assertNotIn("hold_timeout_s", call.await_args.args[1])


class HoldCapabilityTest(unittest.IsolatedAsyncioTestCase):
    async def _refused(self, status: dict) -> str:
        app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        enqueue = AsyncMock()
        with (
            patch.object(runtime, "bridge_status_payload", new=AsyncMock(return_value=status)),
            patch.object(runtime, "enqueue_action_hold", new=enqueue),
        ):
            with self.assertRaises(ToolError) as raised:
                await app.call_tool(
                    COMMAND,
                    {"action": "ActionDeployObject", "hold_timeout_s": 1.0, "timeout_s": 16.0},
                )
        enqueue.assert_not_awaited()
        return _tool_error_text(raised.exception)

    async def test_missing_malformed_and_exception_dispatch_nothing(self) -> None:
        for status in (
            _status(["action_use"]),
            _status(None),
            {"client_peer": {"capabilities": "nope"}},
            _status(["action_hold"], state="unknown"),
        ):
            with self.subTest(status=status):
                self.assertIn("hold_not_supported", await self._refused(status))

        app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        enqueue = AsyncMock()

        async def broken():
            raise RuntimeError("unreadable")

        with (
            patch.object(runtime, "bridge_status_payload", new=broken),
            patch.object(runtime, "enqueue_action_hold", new=enqueue),
        ):
            with self.assertRaises(ToolError) as raised:
                await app.call_tool(
                    COMMAND,
                    {"action": "ActionDeployObject", "hold_timeout_s": 1.0, "timeout_s": 16.0},
                )
        enqueue.assert_not_awaited()
        self.assertIn("hold_not_supported", _tool_error_text(raised.exception))

    async def test_incompatible_protocol_or_id_is_not_a_sustained_hold(self) -> None:
        app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        with (
            patch.object(runtime, "bridge_status_payload", new=AsyncMock(return_value=_status([COMMAND]))),
            patch.object(runtime, "enqueue_action_hold", new=AsyncMock(return_value=(3, "minted"))),
            patch.object(
                runtime,
                "await_action_hold",
                new=AsyncMock(side_effect=ToolError("hold_result_incompatible")),
            ),
        ):
            with self.assertRaises(ToolError) as raised:
                await app.call_tool(
                    COMMAND,
                    {"action": "ActionDeployObject", "hold_timeout_s": 1.0, "timeout_s": 16.0},
                )
        self.assertIn("hold_result_incompatible", _tool_error_text(raised.exception))
        with self.assertRaises(ToolError):
            normalize_hold_observation(_observation(hold_protocol=0), "minted")
        with self.assertRaises(ToolError):
            normalize_hold_observation(_observation(hold_id="other"), "minted")

    async def test_transport_timeout_is_unknown_not_end_state_timeout(self) -> None:
        app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        with (
            patch.object(runtime, "bridge_status_payload", new=AsyncMock(return_value=_status([COMMAND]))),
            patch.object(runtime, "enqueue_action_hold", new=AsyncMock(return_value=(3, "minted"))),
            patch.object(
                runtime,
                "await_action_hold",
                new=AsyncMock(side_effect=ToolError("timeout waiting for action_hold id=3")),
            ),
        ):
            with self.assertRaises(ToolError) as raised:
                await app.call_tool(
                    COMMAND,
                    {"action": "ActionDeployObject", "hold_timeout_s": 1.0, "timeout_s": 16.0},
                )
        text = _tool_error_text(raised.exception)
        self.assertIn("hold_result_unknown", text)
        self.assertNotIn("end_state", text)


class HoldObservationTest(unittest.TestCase):
    def test_zero_cycles_and_false_placement_survive(self) -> None:
        public = normalize_hold_observation(_observation(), "minted")
        self.assertEqual(public["completed_cycles"], 0)
        self.assertIs(public["already_placed"], False)
        self.assertEqual(public["end_state"], "finished")
        self.assertEqual(public["duration_s"], 1.5)
        self.assertEqual(public["action_state"], 4)
        self.assertIs(public["ok"], True)
        self.assertEqual(public["cycles_scope"], "client_progress")
        unknown = normalize_hold_observation(
            _observation(placed_known=0, action_state_known=0, end_state="unknown", reason="lost"),
            "minted",
        )
        self.assertNotIn("already_placed", unknown)
        self.assertIsNone(unknown["action_state"])

    def test_prune_keeps_falsy_hold_scalars_only_for_the_owner(self) -> None:
        wire = {
            "ok": 1,
            "completed_cycles": 0,
            "already_placed": False,
            "duration_s": 0.0,
            "cleanup_complete": False,
            "flag_restored": False,
        }
        kept = prune_unfilled_fields(COMMAND, wire)
        self.assertEqual(kept["completed_cycles"], 0)
        self.assertIs(kept["already_placed"], False)
        self.assertEqual(kept["duration_s"], 0.0)
        dropped = prune_unfilled_fields("action_use", wire)
        self.assertNotIn("completed_cycles", dropped)
        self.assertNotIn("already_placed", dropped)
        self.assertIn("action_hold", result_prune.OWNED_SCALAR_FIELDS["started"])
        self.assertIn("action_hold", result_prune.OWNED_SCALAR_FIELDS["distance"])


def _announce_hold(state: loopback.ServerState) -> None:
    query = poll_census_query("client")
    state.record_poll(
        "client",
        "12~1.29",
        instance=INST_CLIENT,
        source_pid=PID_CLIENT,
        caps=query["caps"],
    )


def _poll_hold(state: loopback.ServerState):
    """Deliver with the same client census admission just stored."""
    query = poll_census_query("client")
    return state.record_poll(
        "client",
        "12~1.29",
        instance=INST_CLIENT,
        source_pid=PID_CLIENT,
        caps=query["caps"],
    )


def _owned_state() -> tuple[loopback.ServerState, SessionCoordinator, str, ClientIdentity]:
    state = loopback.ServerState("test-key")
    bind_both_peers(state)
    client = ClientIdentity.from_payload(IDENTITY)

    def _cleanup(session_id: str, lease_id: str, reason: str, vehicle_active: bool):
        return state.cleanup_owner(session_id, lease_id, reason, vehicle_active)

    coordinator = SessionCoordinator(
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("lease"),
        audit=lambda _event: True,
        cleanup=_cleanup,
    )
    state.coordination = coordinator
    state.retail_probe = lambda: {"known": True, "processes": []}
    _announce_hold(state)
    status, active = coordinator.acquire(client, "hold")
    if status != 200:
        raise AssertionError(active)
    return state, coordinator, str(active["lease_token"]), client


class HoldLeaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.coordinator, self.token, self.client = _owned_state()
        self.args = {
            "action": "ActionDeployObject",
            "selector": "world",
            "hold_timeout_s": 1.0,
        }

    def _enqueue(self, **extra):
        return self.state.enqueue_command(
            COMMAND,
            self.args,
            peer="client",
            identity_payload=IDENTITY,
            lease_token=self.token,
            operation_timeout_s=30.0,
            **extra,
        )

    def test_without_lease_foreign_idle_fenced_and_quarantine(self) -> None:
        status, body = self.state.enqueue_command(
            COMMAND,
            self.args,
            peer="client",
            identity_payload=IDENTITY,
            operation_timeout_s=30.0,
        )
        self.assertEqual(body.get("error"), "lease_required")
        self.assertNotEqual(status, 200)
        self.assertEqual(self.state._holds, {})

        other = dict(IDENTITY)
        other["session_id"] = "session-other"
        other["pid"] = 4243
        status, _other_lease = self.coordinator.acquire(ClientIdentity.from_payload(other), "other")
        self.assertNotEqual(status, 200)

        status, body = self._enqueue()
        self.assertEqual(status, 200, body)
        self.state.fence_runs(["test-run"])
        status, body = self._enqueue()
        self.assertEqual(body.get("error"), "run_not_owned")

        self.state.unfence_runs(["test-run"])
        self.state.lifecycle.manifest.get = lambda _run_id: SimpleNamespace(state="RUNNING_IDLE")
        status, body = self._enqueue()
        self.assertEqual(body.get("error"), "run_not_owned")

        self.state.lifecycle.manifest.get = lambda _run_id: SimpleNamespace(state="RUNNING")
        self.state.retail_probe = lambda: {"known": True, "processes": [{"name": "DayZ_x64.exe"}]}
        status, body = self._enqueue()
        self.assertEqual(body.get("error"), "retail_quarantine")
        self.assertEqual(len(self.state._holds), 1)


class HoldCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.coordinator, self.token, self.client = _owned_state()

    def _arm(self) -> tuple[int, str]:
        status, body = self.state.enqueue_command(
            COMMAND,
            {
                "action": "ActionDeployObject",
                "selector": "hands",
                "target": "hands",
                "hold_timeout_s": 1.0,
            },
            peer="client",
            identity_payload=IDENTITY,
            lease_token=self.token,
            operation_timeout_s=30.0,
        )
        self.assertEqual(status, 200, body)
        return int(body["id"]), str(body["hold_id"])

    def _release(self) -> dict:
        status, body = self.coordinator.release(self.client, self.token, "owner_release")
        self.assertEqual(status, 200, body)
        cleanup = body.get("cleanup")
        self.assertIsInstance(cleanup, dict)
        return cleanup

    def test_undelivered_abandon_does_not_fence_and_delivered_cancel_is_named(self) -> None:
        _queued_id, queued_hold = self._arm()
        cleanup = self._release()
        self.assertNotIn("hold_cancel_enqueued", cleanup)
        self.assertNotIn("test-run", self.state._hold_blocked_runs)
        self.assertTrue(self.state._holds[queued_hold]["closed"])

        self.state, self.coordinator, self.token, self.client = _owned_state()
        delivered_id, delivered_hold = self._arm()
        _status, poll = _poll_hold(self.state)
        ids = [item["id"] for item in poll["commands"]]
        self.assertIn(delivered_id, ids)
        self.assertTrue(self.state._holds[delivered_hold]["delivered"])
        cleanup = self._release()
        self.assertEqual(cleanup.get("hold_cancel_enqueued"), 1)
        queued = [
            item
            for item in self.state._bound_queues[INST_CLIENT]
            if item["cmd"] == "action_hold_cancel"
        ]
        self.assertEqual(queued[-1]["args"]["hold_id"], delivered_hold)

    def test_late_cancel_does_not_end_a_later_hold(self) -> None:
        first_id, first_hold = self._arm()
        _poll_hold(self.state)
        self._release()
        cancel = [
            item
            for item in self.state._bound_queues[INST_CLIENT]
            if item["cmd"] == "action_hold_cancel" and item["args"]["hold_id"] == first_hold
        ][0]
        self.state.store_result(
            {
                "id": first_id,
                "ok": 1,
                "hold_protocol": 1,
                "hold_id": first_hold,
                "matched": 0,
                "cleanup_complete": 1,
                "end_state": "cancel",
                "reason": "interrupted",
            },
            instance=INST_CLIENT,
        )
        self.assertNotIn("test-run", self.state._hold_blocked_runs)
        self.state, self.coordinator, self.token, self.client = _owned_state()
        _second_id, second_hold = self._arm()
        self.state.store_result(
            {
                "id": cancel["id"],
                "ok": 1,
                "matched": 0,
                "hold_id": first_hold,
                "cleanup_complete": 0,
            },
            instance=INST_CLIENT,
        )
        self.assertNotIn("test-run", self.state._hold_blocked_runs)
        self.assertIs(self.state._holds[second_hold]["closed"], False)

    def test_unconfirmed_close_fences_new_holds(self) -> None:
        command_id, hold_id = self._arm()
        _poll_hold(self.state)
        self.state.retail_probe = lambda: {"known": True, "processes": [{"name": "DayZ_x64.exe"}]}
        status, released = self.coordinator.release(self.client, self.token, "owner_release")
        self.assertEqual(status, 200, released)
        degraded = list(released.get("cleanup_degraded") or [])
        inner = released.get("cleanup")
        if isinstance(inner, dict):
            degraded.extend(inner.get("cleanup_degraded") or [])
        self.assertIn("hold_cancel_unconfirmed", degraded)
        self.assertIn("test-run", self.state._hold_blocked_runs)
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        status, renewed = self.coordinator.acquire(self.client, "again")
        self.assertEqual(status, 200, renewed)
        status, body = self.state.enqueue_command(
            COMMAND,
            {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": 1.0},
            peer="client",
            identity_payload=IDENTITY,
            lease_token=renewed["lease_token"],
            operation_timeout_s=30.0,
        )
        self.assertEqual((status, body.get("error")), (409, "hold_unreconciled"))
        self.state.store_result(
            {
                "id": command_id,
                "ok": 1,
                "hold_protocol": 1,
                "hold_id": hold_id,
                "cleanup_complete": 1,
            },
            instance=INST_CLIENT,
        )
        self.assertNotIn("test-run", self.state._hold_blocked_runs)


class HoldRuntimeBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def test_in_process_runtime_mints_and_rejects_a_foreign_id(self) -> None:
        _app, runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        _announce_hold(state)
        runtime.loopback = SimpleNamespace(state=state)
        command_id, minted = await runtime.enqueue_action_hold(
            {
                "action": "ActionDeployObject",
                "selector": "world",
                "hold_timeout_s": 1.0,
            },
            30.0,
        )
        self.assertIsInstance(minted, str)
        self.assertNotEqual(minted, "")
        runtime.state.store_result(
            _observation(hold_id="someone-else", id=command_id),
            instance=INST_CLIENT,
        )
        with self.assertRaises(ToolError) as raised:
            await runtime.await_action_hold(command_id, minted, 1.0)
        self.assertIn("hold_result_incompatible", str(raised.exception))

    def test_selector_helper_matches_action_use_modes(self) -> None:
        parsed = parse_action_selector(
            "ActionDeployObject", "FenceKit", None, 5.0, "hands", None, None, None
        )
        self.assertEqual(parsed.command, "action_use_target")
        wire = hold_command_args(parsed, 1.0)
        self.assertEqual(wire["selector"], "hands")
        self.assertEqual(wire["classname"], "FenceKit")
        self.assertTrue(math.isfinite(wire["hold_timeout_s"]))


class HoldEnforceDrainContractTest(unittest.TestCase):
    def test_f6_send_keeps_drain_and_timeout_cause_survives_expiry(self) -> None:
        source = (addon_root() / "scripts" / "4_World" / "MCP_ActionHold.c").read_text(
            encoding="utf-8"
        )
        transmit = source.split("protected static bool TransmitInterrupt()", 1)[1]
        transmit = transmit.split("protected static void Publish()", 1)[0]
        self.assertNotIn("s_InterruptPending = false", transmit)
        self.assertIn("s_InterruptSent = true", transmit)
        maintain = source.split("static void Maintain()", 1)[1]
        pending = maintain.split("if (s_InterruptPending)", 1)[1]
        pending = pending.split("if (!GetGame()", 1)[0]
        self.assertIn("if (s_TimeoutCause)", pending)
        self.assertIn('s_EndState = "timeout"', pending)
        self.assertLess(
            pending.index("if (s_TimeoutCause)"),
            pending.index('s_EndState = "cancel"'),
        )


class HoldCancelSurvivesLeaseReleaseTest(unittest.TestCase):
    def test_lease_release_keeps_the_queued_hold_cancel(self) -> None:
        state, coordinator, token, client = _owned_state()
        status, admitted = state.enqueue_command(
            COMMAND,
            {
                "action": "ActionDeployObject",
                "selector": "hands",
                "target": "hands",
                "classname": "FenceKit",
                "hold_timeout_s": 120.0,
            },
            peer="client",
            identity_payload=IDENTITY,
            lease_token=token,
            operation_timeout_s=135.0,
        )
        self.assertEqual(status, 200, admitted)
        _poll_hold(state)
        abandoned = state.abandon_command(admitted["id"], "cancelled")
        self.assertEqual(abandoned.get("status"), "release_queued", abandoned)
        coordinator.release(client, token, "owner_release")
        _, polled = _poll_hold(state)
        self.assertEqual(
            [
                command["args"]["hold_id"]
                for command in polled["commands"]
                if command["cmd"] == "action_hold_cancel"
            ],
            [admitted["hold_id"]],
        )


if __name__ == "__main__":
    unittest.main()
