"""Contract tests for action_cursor and player_look_at (inbox 86a3).

These tests decide transport, schema and the public read. They do not claim
that the Enforce snapshot or the native aim override behaves this way in game.
"""

from __future__ import annotations

import asyncio
import math
import time
import unittest

from dayz_mcp.action_cursor import (
    SLOT_NAMES,
    CursorContractError,
    assemble_action_cursor,
    binding_fence,
    execute_action_cursor,
)
from dayz_mcp.look_at_control import (
    HOLD_TICKS,
    MAX_CONTROL_S,
    LookAtController,
    accept_bridge_look_at,
    admit_player_look_at,
    execute_player_look_at,
)
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from dayz_mcp.loopback import ServerState
from dayz_mcp.server import Runtime, ServerConfig
from dayz_mcp.loopback import CLIENT_COMMANDS, SERVER_COMMANDS, validate_command_args
from dayz_mcp.session_coordination import command_requires_lease
from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp.tool_args import _timeout

FENCE = ("run-a", "gen-1", "abcd1234", "abcd1234")


def _ref(classname: str = "Car", net: tuple[int, int] = (1, 2)) -> dict:
    return {
        "present": True,
        "classname": classname,
        "pos": [1.0, 2.0, 3.0],
        "net_low": net[0],
        "net_high": net[1],
    }


def _target(component: int = -1, ref: dict | None = None) -> dict:
    body = {
        "present": True,
        "object_ref": ref if ref is not None else _ref(),
        "parent_ref": {"present": False},
        "component_index": component,
        "cursor_pos": [0.0, 1.0, 0.0],
    }
    return body


def _slot(name: str, action: str, count: int, *, widget: bool) -> dict:
    row = {"slot": name, "action_class": action, "count": count}
    if widget:
        row["input_name"] = name
        row["widget"] = name
    return row


def _widget(name: str, *, exists: bool = True, visible: bool = True, hierarchy: bool = True) -> dict:
    if not exists:
        return {"name": name, "exists": False}
    return {
        "name": name,
        "exists": True,
        "path": "ActionTargetsCursorWidget/" + name,
        "widget_type": "ImageWidget",
        "visible": visible,
        "visible_hierarchy": hierarchy,
        "screen_x": 1.0,
        "screen_y": 2.0,
        "screen_w": 3.0,
        "screen_h": 4.0,
    }


def _body(**overrides) -> dict:
    names = (
        "root",
        "item",
        "item_desc",
        "item_flag_icon",
        "primary",
        "secondary",
        "continuous_primary",
        "continuous_secondary",
    )
    body = {
        "read_tick": 4,
        "cursor_update_tick": 4,
        "item_description_input": "Protected",
        "manager_target": _target(),
        "cursor_target": _target(),
        "display_object": _ref(),
        "manager_slots": [_slot(name, "ActionOpen", 1, widget=False) for name in SLOT_NAMES],
        "cursor_slots": [_slot(name, "ActionOpen", 1, widget=True) for name in SLOT_NAMES],
        "widgets": [_widget(name) for name in names],
    }
    body.update(overrides)
    return body


def _assemble(body: dict, rows: list | None = None, **kwargs):
    fence = kwargs.pop("fence", FENCE)
    return assemble_action_cursor(
        {"action_cursor": body},
        rows if rows is not None else [],
        fence_before=fence,
        fence_after=kwargs.pop("fence_after", fence),
        run_token_sent=kwargs.pop("sent", "run-a"),
        run_token_echo=kwargs.pop("echo", "run-a"),
        run_id="run-a",
        request_id="req-1",
        **kwargs,
    )


class RoutingTests(unittest.TestCase):
    def test_cursor_is_a_client_read_and_look_at_needs_a_lease_and_a_run(self) -> None:
        self.assertIn("action_cursor", CLIENT_COMMANDS)
        self.assertNotIn("action_cursor", SERVER_COMMANDS)
        self.assertIn("action_cursor_ids", SERVER_COMMANDS)
        self.assertFalse(command_requires_lease("action_cursor"))
        self.assertFalse(command_requires_lease("action_cursor_ids"))
        self.assertTrue(command_requires_lease("player_look_at"))
        self.assertEqual(admit_player_look_at(lease_token=None, run_id="run-a"), "lease_required")
        self.assertEqual(admit_player_look_at(lease_token="lease", run_id=None), "no_active_run")
        self.assertIsNone(admit_player_look_at(lease_token="lease", run_id="run-a"))


class IngressTests(unittest.TestCase):
    def test_cursor_rejects_a_target_selector_and_look_at_rejects_bad_vectors(self) -> None:
        ok, _error = validate_command_args("action_cursor", {"target": "Car"})
        self.assertFalse(ok)
        ok, _error = validate_command_args("player_look_at", {"pos": [1.0, float("nan"), 0.0]})
        self.assertFalse(ok)
        ok, _error = validate_command_args("player_look_at", {"pos": [1.0, 2.0]})
        self.assertFalse(ok)
        with self.assertRaises(ToolError):
            _timeout(0)
        with self.assertRaises(ToolError):
            _timeout(float("nan"))


class SlotAndComponentTests(unittest.TestCase):
    def test_slots_stay_named_component_minus_one_stays_and_visibility_is_hierarchy(self) -> None:
        body = _body()
        body["manager_slots"][0]["action_class"] = "ActionFirst"
        body["cursor_slots"][0]["action_class"] = "ActionSecond"
        body["cursor_target"]["component_index"] = -1
        body["widgets"][4]["visible"] = True
        body["widgets"][4]["visible_hierarchy"] = False
        public = _assemble(body)
        self.assertEqual(public["manager"]["selected_slots"]["primary"]["action_class"], "ActionFirst")
        self.assertEqual(public["cursor"]["slots"]["primary"]["action_class"], "ActionSecond")
        self.assertEqual(public["cursor"]["target"]["component_index"], -1)
        self.assertFalse(public["widgets"]["primary"]["visible_hierarchy"])
        self.assertTrue(public["widgets"]["primary"]["visible"])
        self.assertFalse(public["sample"]["coherent"])

    def test_missing_widget_is_absent_not_hidden(self) -> None:
        body = _body()
        body["widgets"][3] = _widget("item_flag_icon", exists=False)
        public = _assemble(body)
        icon = public["widgets"]["item_flag_icon"]
        self.assertFalse(icon["exists"])
        self.assertIsNone(icon["visible"])
        self.assertIsNone(icon["visible_hierarchy"])
        self.assertIsNone(icon["rect"])


class IdentityTests(unittest.TestCase):
    def test_object_id_follows_the_network_pair_of_this_run_only(self) -> None:
        body = _body()
        body["requested_classname"] = "NotTheObservedCar"
        rows = [
            {"net_low": 1, "net_high": 2, "object_id": 9, "registry_run": "run-a"},
            {"net_low": 1, "net_high": 2, "object_id": 9, "registry_run": "other"},
        ]
        with self.assertRaises(CursorContractError):
            _assemble(body, rows)
        rows = [{"net_low": 1, "net_high": 2, "object_id": 9, "registry_run": "other-run"}]
        public = _assemble(body, rows)
        self.assertIsNone(public["manager"]["target"]["object"]["object_id"])
        self.assertEqual(public["cursor"]["target"]["object"]["classname"], "Car")
        rows = [{"net_low": 1, "net_high": 2, "object_id": 9, "registry_run": "run-a"}]
        public = _assemble(body, rows)
        self.assertEqual(public["manager"]["target"]["object"]["object_id"], 9)
        self.assertIsNone(public["cursor"]["target"]["parent"])

    def test_classname_alone_does_not_assign_an_id(self) -> None:
        body = _body()
        rows = [{"net_low": 8, "net_high": 8, "object_id": 3, "classname": "Car", "registry_run": "run-a"}]
        public = _assemble(body, rows)
        self.assertIsNone(public["manager"]["target"]["object"]["object_id"])


class FreshnessTests(unittest.TestCase):
    def test_a_fresh_manager_against_an_old_cursor_is_not_coherent(self) -> None:
        body = _body(read_tick=5, cursor_update_tick=4)
        public = _assemble(body)
        self.assertFalse(public["sample"]["content_fresh"])
        self.assertFalse(public["sample"]["coherent"])
        self.assertTrue(public["ok"])

    def test_environmental_target_stays_present_with_a_null_target(self) -> None:
        empty = {
            "present": True,
            "object_ref": {"present": False},
            "parent_ref": {"present": False},
            "component_index": -1,
            "cursor_pos": [0.0, 0.0, 0.0],
        }
        public = _assemble(_body(manager_target=empty, cursor_target=empty, display_object={"present": False}))
        self.assertTrue(public["manager"]["action_target_present"])
        self.assertIsNone(public["manager"]["target"])
        self.assertTrue(public["sample"]["coherent"])


class NormalizationTests(unittest.TestCase):
    def test_a_dropped_payload_or_a_bridge_error_is_not_success(self) -> None:
        with self.assertRaises(CursorContractError) as caught:
            _assemble({})
        self.assertEqual(caught.exception.code, "bad_cursor_result")
        with self.assertRaises(CursorContractError) as caught:
            assemble_action_cursor(
                {"ok": False, "error": "no_player"},
                [],
                fence_before=FENCE,
                fence_after=FENCE,
                run_token_sent="",
                run_token_echo="",
                run_id="run-a",
                request_id="req-1",
            )
        self.assertEqual(caught.exception.code, "bad_cursor_result")

    def test_a_session_change_invalidates_the_snapshot(self) -> None:
        with self.assertRaises(CursorContractError) as caught:
            _assemble(_body(), fence_after=("run-b", "gen-1", "abcd1234", "abcd1234"))
        self.assertEqual(caught.exception.code, "snapshot_invalidated")


class LookAtControlTests(unittest.TestCase):
    def _obs(self, **extra) -> dict:
        obs = {
            "dt_s": 0.05,
            "lease_held": True,
            "pose_ok": True,
            "camera_dir": [0.0, 0.0, 1.0],
            "desired_dir": [0.0, 0.0, 1.0],
            "commanded_error_deg": 40.0,
        }
        obs.update(extra)
        return obs

    def test_convergence_uses_the_measured_direction_and_needs_the_hold(self) -> None:
        controller = LookAtController(budget_s=3.0)
        last = None
        for _ in range(6):
            last = controller.step(self._obs())
            self.assertIsNone(last["pulse"])
        self.assertTrue(last["converged"])
        self.assertGreaterEqual(last["stable_ticks"], 6)
        self.assertGreaterEqual(last["hold_ticks"], HOLD_TICKS)
        accept_bridge_look_at(
            {
                "converged": 1,
                "termination": "converged",
                "error_initial_deg": 0,
                "error_final_deg": last["error_final_deg"],
                "ticks": last["ticks"],
                "duration_s": last["duration_s"],
                "pose": "first_idle",
                "stable_ticks": last["stable_ticks"],
                "hold_ticks": last["hold_ticks"],
            }
        )
        with self.assertRaises(ValueError):
            accept_bridge_look_at(
                {
                    "converged": True,
                    "termination": "converged",
                    "error_initial_deg": 0.0,
                    "error_final_deg": 0.0,
                    "ticks": 3,
                    "duration_s": 0.1,
                    "pose": "first_idle",
                    "stable_ticks": 3,
                    "hold_ticks": 0,
                    "commanded_error_deg": 0.0,
                }
            )
        with self.assertRaises(ValueError):
            accept_bridge_look_at(
                {
                    "converged": True,
                    "termination": "converged",
                    "error_final_deg": 0.0,
                    "stable_ticks": 6,
                    "hold_ticks": 3,
                }
            )
        with self.assertRaises(ValueError):
            accept_bridge_look_at(
                {
                    "converged": 1,
                    "termination": "converged",
                    "error_initial_deg": -1,
                    "error_final_deg": -1,
                    "ticks": 6,
                    "duration_s": 1000,
                    "pose": "third_person",
                    "stable_ticks": 6,
                    "hold_ticks": 3,
                }
            )

    def test_cancel_and_deadline_release_and_do_not_keep_pulsing(self) -> None:
        controller = LookAtController(budget_s=0.1)
        turned = {
            "dt_s": 0.05,
            "lease_held": True,
            "pose_ok": True,
            "camera_dir": [1.0, 0.0, 0.0],
            "desired_dir": [0.0, 0.0, 1.0],
        }
        first = controller.step(turned)
        self.assertIsNotNone(first["pulse"])
        self.assertFalse(first["converged"])
        stopped = controller.step(self._obs(cancelled=True, camera_dir=[1.0, 0.0, 0.0], desired_dir=[0.0, 0.0, 1.0]))
        self.assertEqual(stopped["termination"], "cancelled")
        self.assertTrue(stopped["release_overrides"])
        again = controller.step(turned)
        self.assertIsNone(again["pulse"])
        late = LookAtController(budget_s=0.04)
        done = late.step({"dt_s": 0.05, "lease_held": True, "pose_ok": True, "camera_dir": [0.0, 0.0, 1.0], "desired_dir": [1.0, 0.0, 0.0]})
        self.assertEqual(done["termination"], "deadline")
        self.assertIsNone(late.step(turned)["pulse"])
        self.assertTrue(math.isfinite(first["pulse"][0]))


def _as_bridge_bools(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, list):
        return [_as_bridge_bools(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_bridge_bools(item) for key, item in value.items()}
    return value


def _status(run_id: str = "run-a", generation: str = "gen-1") -> dict:
    announced = {
        "state": "announced",
        "announced_commands": ["action_cursor", "player_look_at", "action_cursor_ids"],
    }
    return {
        "daemon_generation": generation,
        "client_peer": {
            "run_id": run_id,
            "instance_prefix": "abcd1234",
            "binding_token": "client-token",
            "version": "12~1.29",
            "capabilities": announced,
        },
        "server_peer": {
            "run_id": run_id,
            "instance_prefix": "abcd1234",
            "binding_token": "server-token",
            "version": "12~1.29",
            "capabilities": announced,
        },
    }


class _Clock:
    def __init__(self) -> None:
        self.calls = []

    async def bridge_status_payload(self, timeout_s: float | None = None) -> dict:
        self.calls.append(("status", timeout_s))
        await asyncio.sleep(0.03)
        return _status()

    async def call_bridge(self, command, args, peer, timeout_s, expected_fence=None):
        self.calls.append((command, args, peer, timeout_s))
        await asyncio.sleep(0)
        return {"ok": True, "action_cursor": _body(), "look_at": _look_report()}


def _look_report() -> dict:
    return {
        "converged": 1,
        "termination": "converged",
        "error_initial_deg": 4,
        "error_final_deg": 0,
        "ticks": 6,
        "duration_s": 0.3,
        "pose": "first_idle",
        "stable_ticks": 6,
        "hold_ticks": 3,
    }


class ProducerContractTests(unittest.TestCase):
    def test_integer_booleans_assemble_and_other_ints_do_not(self) -> None:
        assembled = _assemble(_as_bridge_bools(_body()))
        self.assertTrue(assembled["ok"])
        self.assertTrue(assembled["widgets"]["root"]["visible"])
        broken = _as_bridge_bools(_body())
        broken["widgets"][0]["visible"] = 2
        with self.assertRaises(CursorContractError) as caught:
            _assemble(broken)
        self.assertEqual(caught.exception.code, "bad_cursor_result")

    def test_a_missing_action_class_is_not_an_empty_selection(self) -> None:
        body = _body()
        for side in ("manager_slots", "cursor_slots"):
            for slot in body[side]:
                del slot["action_class"]
        with self.assertRaises(CursorContractError):
            _assemble(body)
        empty = _body()
        for side in ("manager_slots", "cursor_slots"):
            for slot in empty[side]:
                slot["action_class"] = ""
                slot["count"] = 0
        assembled = _assemble(empty)
        self.assertIsNone(assembled["manager"]["selected_slots"]["primary"]["action_class"])

    def test_retained_cursor_counts_beside_empty_actions_stay_observable(self) -> None:
        body = _body()
        for row in body["cursor_slots"]:
            row["action_class"] = ""
        body["widgets"][0]["visible"] = False
        body["widgets"][0]["visible_hierarchy"] = False
        assembled = _assemble(body)
        self.assertTrue(assembled["ok"])
        primary = assembled["cursor"]["slots"]["primary"]
        self.assertIsNone(primary["action_class"])
        self.assertEqual(primary["count"], 1)
        self.assertFalse(assembled["sample"]["coherent"])
        self.assertFalse(assembled["widgets"]["root"]["visible_hierarchy"])

    def test_a_replaced_client_in_the_same_run_is_not_the_same_observer(self) -> None:
        before = _status()
        after = _status()
        after["client_peer"]["binding_token"] = "ffff4321"
        peers = ("client", "server")
        self.assertNotEqual(binding_fence(before, peers), binding_fence(after, peers))
        with self.assertRaises(CursorContractError) as caught:
            _assemble(_body(), fence=binding_fence(before, peers), fence_after=binding_fence(after, peers))
        self.assertEqual(caught.exception.code, "snapshot_invalidated")
        assembled = _assemble(_body(), fence=binding_fence(before, peers), fence_after=binding_fence(before, peers))
        self.assertEqual(assembled["client_instance"], "client-token")
        self.assertEqual(assembled["run_id"], "run-a")
        self.assertNotEqual(assembled["client_instance"], before["client_peer"]["instance_prefix"])

    def test_missing_identity_is_not_an_empty_scene(self) -> None:
        body = _body()
        del body["manager_target"]["object_ref"]
        del body["cursor_target"]["object_ref"]
        del body["display_object"]
        with self.assertRaises(CursorContractError):
            _assemble(body)
        absent = _body(
            manager_target={
                "present": 0,
                "object_ref": {"present": 0},
                "parent_ref": {"present": 0},
            },
            cursor_target={
                "present": 0,
                "object_ref": {"present": 0},
                "parent_ref": {"present": 0},
            },
            display_object={"present": 0},
        )
        assembled = _assemble(absent)
        self.assertTrue(assembled["ok"])
        self.assertIsNone(assembled["manager"]["target"])
        self.assertFalse(assembled["manager"]["action_target_present"])

    def test_provenance_is_the_shared_run_not_the_prefix(self) -> None:
        status = _status()
        peers = ("client", "server")
        fence = binding_fence(status, peers)
        self.assertEqual(fence["generation"], "gen-1")
        self.assertEqual(fence["peers"]["client"]["run_id"], "run-a")
        self.assertEqual(fence["peers"]["client"]["binding_token"], "client-token")
        self.assertNotEqual(
            fence["peers"]["client"]["binding_token"],
            status["client_peer"]["instance_prefix"],
        )
        del status["client_peer"]["binding_token"]
        self.assertIsNone(binding_fence(status, peers))

    def test_cursor_deadline_covers_the_status_hops(self) -> None:
        clock = _Clock()

        async def run():
            await execute_action_cursor(clock, 0.01)

        with self.assertRaises(ToolError) as caught:
            asyncio.run(run())
        self.assertEqual(str(caught.exception), "timeout")
        self.assertLess(time.monotonic(), time.monotonic() + 1)


class LookAtModeTests(unittest.TestCase):
    def test_embedded_runtime_reaches_dispatch_and_client_mode_admits(self) -> None:
        embedded = Runtime(ServerConfig(key="embedded-look"))
        state = ServerState("k")
        embedded.loopback = type("Loop", (), {"state": state})()
        client_id = "11111111-1111-4111-8111-111111111111"
        server_id = "33333333-3333-4333-8333-333333333333"
        created = "2026-08-18T00:00:00.000000Z"
        state.install_bound_peer(instance=client_id, role="client", pid=11, run_id="r", creation_time_utc=created)
        state.install_bound_peer(instance=server_id, role="server", pid=12, run_id="r", creation_time_utc=created)
        state.record_poll(
            "client",
            version="12~1.29.0",
            instance=client_id,
            source_pid=11,
            source_creation_time=created,
            caps="action_cursor,player_look_at",
        )
        state.record_poll(
            "server",
            version="12~1.29.0",
            instance=server_id,
            source_pid=12,
            source_creation_time=created,
            caps="action_cursor_ids",
        )
        seen = {}

        async def wait_for_result(cmd, command_id, peer, timeout_s):
            seen["queued"] = command_id
            return {"ok": True, "look_at": _look_report()}

        embedded.wait_for_result = wait_for_result

        async def run_embedded():
            status = await embedded.bridge_status_payload()
            self.assertEqual(status["client_peer"]["run_id"], "r")
            self.assertEqual(status["server_peer"]["run_id"], "r")
            self.assertEqual(status["daemon_generation"], state.daemon_generation)
            self.assertTrue(status["daemon_generation"])
            return await execute_player_look_at(embedded, [0.0, 1.0, 10.0], 1.0)

        result = asyncio.run(run_embedded())
        self.assertEqual(result["run_id"], "r")
        self.assertEqual(result["generation"], state.daemon_generation)
        self.assertEqual(result["client_instance"], state.bound_instance_token("r", "client"))
        self.assertIn("|11|", result["client_instance"])
        self.assertIn("queued", seen)
        self.assertNotIn("_session_state_snapshot", dir(Runtime))

        client = Runtime(ServerConfig())

        async def status(timeout_s=None):
            return _status()

        async def call_bridge(command, args, peer, timeout_s, expected_fence=None):
            seen["args"] = args
            return {"ok": True, "look_at": _look_report()}

        client._session_state_snapshot = lambda: (None, None)
        client.bridge_status_payload = status
        client.call_bridge = call_bridge

        async def run_client():
            await execute_player_look_at(client, [0.0, 1.0, 10.0], 3.0)

        with self.assertRaises(ToolError) as caught:
            asyncio.run(run_client())
        self.assertEqual(str(caught.exception), "lease_required")

    def test_a_replaced_run_after_completion_is_not_success(self) -> None:
        runtime = Runtime(ServerConfig())
        calls = {"n": 0}

        async def status(timeout_s=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _status()
            return _status(run_id="run-b")

        async def call_bridge(command, args, peer, timeout_s, expected_fence=None):
            return {"ok": True, "look_at": _look_report()}

        runtime.bridge_status_payload = status
        runtime.call_bridge = call_bridge

        async def run():
            await execute_player_look_at(runtime, [0.0, 1.0, 10.0], 3.0)

        with self.assertRaises(ToolError) as caught:
            asyncio.run(run())
        self.assertEqual(str(caught.exception), "snapshot_invalidated")


def _bind_run(state: ServerState, client_id: str, client_pid: int) -> str:
    server_id = "33333333-3333-4333-8333-333333333333"
    created = "2026-08-18T00:00:00.000000Z"
    state.install_bound_peer(instance=client_id, role="client", pid=client_pid, run_id="r", creation_time_utc=created)
    if state.bound_instance_token("r", "server") is None:
        state.install_bound_peer(instance=server_id, role="server", pid=12, run_id="r", creation_time_utc=created)
    state.record_poll(
        "client",
        version="12~1.29.0",
        instance=client_id,
        source_pid=client_pid,
        source_creation_time=created,
        caps="action_cursor,player_look_at",
        ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
    )
    state.record_poll(
        "server",
        version="12~1.29.0",
        instance=server_id,
        source_pid=12,
        source_creation_time=created,
        caps="action_cursor_ids",
        ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
    )
    return created


class Round4RegressionTests(unittest.TestCase):
    def test_same_prefix_client_replacement_invalidates_cursor_and_look_at(self) -> None:
        first = "11111111-1111-4111-8111-111111111111"
        second = "11111111-2222-4222-8222-222222222222"
        self.assertEqual(first[:8], second[:8])

        async def replace_during_wait(runtime, state, command, payload):
            async def wait_for_result(cmd, command_id, peer, timeout_s):
                from dayz_mcp.instance_fence import BINDING_RETIRED

                for binding in state._bindings.values():
                    if binding.role == "client" and binding.state != BINDING_RETIRED:
                        binding.state = BINDING_RETIRED
                _bind_run(state, second, 22)
                if cmd == "action_cursor":
                    return {"ok": True, "action_cursor": _body()}
                if cmd == "action_cursor_ids":
                    return {"ok": True, "cursor_ids": {"object_ids": [0], "run_token": "r"}}
                return payload

            runtime.wait_for_result = wait_for_result
            with self.assertRaises(ToolError) as caught:
                if command == "action_cursor":
                    await execute_action_cursor(runtime, 2.0)
                else:
                    await execute_player_look_at(runtime, [0.0, 1.0, 10.0], 2.0)
            self.assertEqual(str(caught.exception), "snapshot_invalidated")
            self.assertNotEqual(
                state.bound_instance_token("r", "client"),
                f"{first}|",
            )

        async def run():
            for command, payload in (
                ("action_cursor", {"ok": True, "action_cursor": _body()}),
                ("player_look_at", {"ok": True, "look_at": _look_report()}),
            ):
                runtime = Runtime(ServerConfig(key="replace-fence"))
                state = ServerState("k")
                runtime.loopback = type("Loop", (), {"state": state})()
                _bind_run(state, first, 11)
                before = state.bound_instance_token("r", "client")
                self.assertIsNotNone(before)
                self.assertIn(first, before)
                await replace_during_wait(runtime, state, command, payload)
                after = state.bound_instance_token("r", "client")
                self.assertIn(second, after)
                self.assertNotEqual(before, after)

        asyncio.run(run())

    def test_cancelling_look_at_releases_the_admitted_command(self) -> None:
        runtime = Runtime(ServerConfig(key="look-cancel"))
        state = ServerState("k")
        runtime.loopback = type("Loop", (), {"state": state})()
        client_id = "11111111-1111-4111-8111-111111111111"
        created = _bind_run(state, client_id, 11)

        async def run():
            task = asyncio.create_task(execute_player_look_at(runtime, [0.0, 1.0, 10.0], 2.0))
            command_id = None
            for _ in range(100):
                for candidate, row in state._broker_rows.items():
                    if row.get("cmd") == "player_look_at":
                        command_id = candidate
                        break
                if command_id is not None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(command_id)
            status, polled = state.record_poll(
                "client",
                version="12~1.29.0",
                instance=client_id,
                source_pid=11,
                source_creation_time=created,
                caps="action_cursor,player_look_at",
            )
            self.assertEqual(status, 200)
            delivered = [item.get("cmd") for item in polled.get("commands", [])]
            self.assertIn("player_look_at", delivered)
            self.assertIsNone(state.take_result(command_id, remove=False))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertNotIn(command_id, state._broker_rows)
            queued = [
                item
                for item in state._bound_queues.get(client_id, [])
                if item.get("cmd") == "player_look_at_release"
            ]
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]["args"]["command_id"], command_id)

        asyncio.run(run())


class BrokerFenceTests(unittest.IsolatedAsyncioTestCase):
    """F6 and F11 through a real ClientRuntime and the broker HTTP handlers."""

    def setUp(self) -> None:
        from tests.broker_binding_helpers import BrokerFixture

        self._broker = BrokerFixture
        self.fixtures = []

    def tearDown(self) -> None:
        for fixture in self.fixtures:
            fixture.stop()

    def _open(self, **kwargs):
        # The shared fixture serves /status through the daemon's own provider.
        fixture = self._broker(self, **kwargs)
        self.fixtures.append(fixture)
        return fixture

    async def test_broker_client_replacement_invalidates_cursor_and_look_at(self) -> None:
        from dayz_mcp.instance_fence import BINDING_RETIRED
        from tests.fence_helpers import INST_CLIENT, INST_SERVER, PID_CLIENT, PID_SERVER
        from tests.broker_binding_helpers import ProbeLifecycle as _Lifecycle

        second = "11111111-2222-4222-8222-222222222222"
        created = "2026-08-18T00:00:00.000000Z"
        version = "12~1.29.0"

        def announce(state, client: str, pid: int) -> None:
            state.record_poll(
                "client",
                version=version,
                instance=client,
                source_pid=pid,
                source_creation_time=created,
                caps="action_cursor,player_look_at",
                ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
            )
            state.record_poll(
                "server",
                version=version,
                instance=INST_SERVER,
                source_pid=PID_SERVER,
                source_creation_time=created,
                caps="action_cursor_ids",
                ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
            )

        async def wait_cmd(state, cmd: str) -> int:
            for _ in range(200):
                for command_id, row in list(state._broker_rows.items()):
                    if row.get("cmd") == cmd and row.get("state") == "queued":
                        return command_id
                await asyncio.sleep(0.01)
            self.fail(f"{cmd} was not admitted")

        def replace_client(state) -> None:
            for binding in state._bindings.values():
                if binding.role == "client" and binding.state != BINDING_RETIRED:
                    binding.state = BINDING_RETIRED
            state.install_bound_peer(
                instance=second,
                role="client",
                pid=22,
                run_id="run-broker",
                creation_time_utc=created,
            )
            announce(state, second, 22)

        cursor = self._open()
        cursor.bind(INST_SERVER, "server", PID_SERVER, run_id="run-broker")
        cursor.bind(INST_CLIENT, "client", PID_CLIENT, run_id="run-broker")
        announce(cursor.state, INST_CLIENT, PID_CLIENT)
        cursor_task = asyncio.create_task(execute_action_cursor(cursor.runtime, 4.0))
        cursor_id = await wait_cmd(cursor.state, "action_cursor")
        cursor.state.record_poll(
            "client",
            version=version,
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            source_creation_time=created,
            caps="action_cursor,player_look_at",
            ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        replace_client(cursor.state)
        cursor.state.store_result(
            {"id": cursor_id, "ok": True, "action_cursor": _body()},
            instance=INST_CLIENT,
        )
        with self.assertRaises(ToolError) as cursor_error:
            await cursor_task
        self.assertEqual(str(cursor_error.exception), "snapshot_invalidated")

        look = self._open(coordination=True)
        look.state.lifecycle = _Lifecycle("run-broker")
        look.bind(INST_SERVER, "server", PID_SERVER, run_id="run-broker")
        look.bind(INST_CLIENT, "client", PID_CLIENT, run_id="run-broker")
        announce(look.state, INST_CLIENT, PID_CLIENT)
        identity = look.runtime.identity
        granted, grant = look.state.coordination.acquire(identity, "broker")
        self.assertEqual(granted, 200, grant)
        look.runtime._control.active_lease_token = grant["lease_token"]
        look_task = asyncio.create_task(
            execute_player_look_at(look.runtime, [0.0, 1.0, 10.0], 4.0)
        )
        look_id = await wait_cmd(look.state, "player_look_at")
        look.state.record_poll(
            "client",
            version=version,
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            source_creation_time=created,
            caps="action_cursor,player_look_at",
            ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        replace_client(look.state)
        look.state.store_result(
            {"id": look_id, "ok": True, "look_at": _look_report()},
            instance=INST_CLIENT,
        )
        with self.assertRaises(ToolError) as look_error:
            await look_task
        self.assertEqual(str(look_error.exception), "snapshot_invalidated")

    async def test_broker_cancel_queues_one_look_at_release(self) -> None:
        from tests.fence_helpers import INST_CLIENT, INST_SERVER, PID_CLIENT, PID_SERVER
        from tests.broker_binding_helpers import ProbeLifecycle as _Lifecycle

        created = "2026-08-18T00:00:00.000000Z"
        version = "12~1.29.0"
        fixture = self._open(coordination=True)
        fixture.state.lifecycle = _Lifecycle("run-broker")
        fixture.bind(INST_SERVER, "server", PID_SERVER, run_id="run-broker")
        fixture.bind(INST_CLIENT, "client", PID_CLIENT, run_id="run-broker")
        fixture.state.record_poll(
            "client",
            version=version,
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            source_creation_time=created,
            caps="player_look_at",
            ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        fixture.state.record_poll(
            "server",
            version=version,
            instance=INST_SERVER,
            source_pid=PID_SERVER,
            source_creation_time=created,
            caps="action_cursor_ids",
            ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        identity = fixture.runtime.identity
        granted, grant = fixture.state.coordination.acquire(identity, "broker")
        self.assertEqual(granted, 200, grant)
        fixture.runtime._control.active_lease_token = grant["lease_token"]
        task = asyncio.create_task(
            execute_player_look_at(fixture.runtime, [0.0, 1.0, 10.0], 4.0)
        )
        command_id = None
        for _ in range(200):
            for candidate, row in list(fixture.state._broker_rows.items()):
                if row.get("cmd") == "player_look_at":
                    command_id = candidate
                    break
            if command_id is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(command_id)
        status, polled = fixture.state.record_poll(
            "client",
            version=version,
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            source_creation_time=created,
            caps="player_look_at",
        )
        self.assertEqual(status, 200)
        delivered = [item.get("cmd") for item in polled.get("commands", [])]
        self.assertIn("player_look_at", delivered)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertNotIn(command_id, fixture.state._broker_rows)
        queued = [
            item
            for item in fixture.state._bound_queues.get(INST_CLIENT, [])
            if item.get("cmd") == "player_look_at_release"
            and item.get("args", {}).get("command_id") == command_id
        ]
        self.assertEqual(len(queued), 1)


if __name__ == "__main__":
    unittest.main()
