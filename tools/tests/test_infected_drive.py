"""infected_drive (inbox 7c86): server tick hold and lease close."""

from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.session_coordination import (
    READ_ONLY_COMMANDS,
    ClientIdentity,
    SessionCoordinator,
    command_requires_lease,
)
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import _method_body
from tests.enforce_subset_helpers import (
    EnforceString,
    EnforceVector,
    class_members,
    declared_fields,
    method_body,
    translate,
)
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue
from tests.lease_helpers import SequentialIds


COMMAND = "infected_drive"
DRIVE_RELEASE = "infected_drive_release"
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
DRIVE = addon_root() / "scripts" / "5_Mission" / "MCP_InfectedDrive.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
INFECTED_TYPE = "ZmbM_CitizenASkinny_Blue"
POS = [4200.0, 338.66, 10610.0]
DRIVE_WIRE = {
    "type": INFECTED_TYPE,
    "pos": list(POS),
    "heading": 90.0,
    "heading_set": True,
    "speed": 2.5,
    "speed_set": True,
}
RELEASE_WIRE = {"type": INFECTED_TYPE, "pos": list(POS), "mode": "release"}


def _tool_error_text(exc: BaseException) -> str:
    text = str(exc)
    nested = getattr(exc, "exceptions", None)
    if nested:
        text = text + " " + str(nested)
    return text


def _strip_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


def _in_order(test: unittest.TestCase, body: str, *tokens: str) -> None:
    positions = []
    for token in tokens:
        test.assertIn(token, body)
        positions.append(body.index(token))
    test.assertEqual(positions, sorted(positions), tokens)


def _bound_state(**kwargs: object) -> loopback.ServerState:
    state = loopback.ServerState(key="k", **kwargs)  # type: ignore[arg-type]
    state.daemon_generation = "gen-infected-drive"
    state.install_bound_peer(
        instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="drive-run"
    )
    return state


def _full_commands() -> list[str]:
    return sorted(server._BRIDGE_COMMAND_TOOLS["server"])


def _announce(
    state: loopback.ServerState,
    commands: list[str],
    ach: str | None,
    generation: str | None = None,
) -> None:
    state._peer_caps["server"] = {
        "generation": state.daemon_generation if generation is None else generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _poll(state: loopback.ServerState, commands: list[str], ach: str):
    return state.record_poll(
        "server",
        f"{EXPECTED_BRIDGE_VERSION}~1.30.0",
        instance=INST_SERVER,
        source_pid=PID_SERVER,
        caps=",".join(commands),
        ach=ach,
    )


OWNER_A = {
    "platform": "codex",
    "pid": 301,
    "ppid": 10,
    "started_at_utc": "2026-11-12T08:00:00Z",
    "session_id": "infected-drive-owner-a",
    "task_label": "infected drive",
}
OWNER_B = {
    **OWNER_A,
    "pid": 302,
    "session_id": "infected-drive-owner-b",
    "task_label": "next owner",
}


def _leased_state() -> tuple[loopback.ServerState, SessionCoordinator]:
    state = _bound_state()
    coordinator = SessionCoordinator(
        token_fn=SequentialIds("infected-token"),
        id_fn=SequentialIds("infected-lease"),
        audit=lambda _event: True,
        cleanup=state.cleanup_owner,
    )
    state.coordination = coordinator
    state.retail_probe = lambda: {"known": True, "processes": []}
    _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
    return state, coordinator


def _acquire(coordinator: SessionCoordinator, owner: dict) -> str:
    status, active = coordinator.acquire(
        ClientIdentity.from_payload(owner), owner["task_label"]
    )
    if status != 200 or active.get("status") != "active":
        raise AssertionError(active)
    return str(active["lease_token"])


def _release(coordinator: SessionCoordinator, owner: dict, token: str) -> dict:
    status, released = coordinator.release(ClientIdentity.from_payload(owner), token)
    if status != 200 or released.get("released") is not True:
        raise AssertionError(released)
    return released


def _send(state: loopback.ServerState, owner: dict, token: str, wire: dict) -> int:
    status, body = state.enqueue_command(
        COMMAND, dict(wire), peer="server", identity_payload=owner, lease_token=token
    )
    if status != 200:
        raise AssertionError(body)
    return int(body["id"])


def _delivered(
    state: loopback.ServerState, commands: list[str] | None = None
) -> list[dict]:
    census = _full_commands() if commands is None else commands
    status, polled = _poll(state, census, EXPECTED_SERVER_ARG_CONTRACT_HASH)
    if status != 200:
        raise AssertionError(polled)
    return list(polled["commands"])


def _answered(
    state: loopback.ServerState, command_id: int, answer: dict | None = None
) -> dict | None:
    body = (
        {"ok": 1, "pos_real": list(POS)} if answer is None else dict(answer)
    )
    state.store_result({"id": command_id, **body}, instance=INST_SERVER)
    return state.take_result(command_id, remove=True)


def _lease_close(state: loopback.ServerState) -> dict:
    """The one command a lease end now sends the server for its drives:
    the aggregated close whose dispatch stops every drive."""
    [close] = _delivered(state)
    if close["cmd"] != "lease_close":
        raise AssertionError(close)
    if close["args"].get("close_lease_id") != "infected-lease-1":
        raise AssertionError(close)
    if close["args"].get("close_generation") != "gen-infected-drive":
        raise AssertionError(close)
    return close


class InfectedDriveToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tool = self.app._tool_manager.get_tool(COMMAND)

    async def _forwarded(self, arguments: dict) -> tuple:
        with patch.object(
            self.runtime,
            "call_bridge",
            new=AsyncMock(
                return_value={
                    "ok": 1,
                    "pos_real": list(POS),
                }
            ),
        ) as call:
            await self.app.call_tool(COMMAND, arguments)
        call.assert_awaited_once()
        return call.await_args.args

    async def _refused(self, arguments: dict) -> str:
        with patch.object(
            self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})
        ) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(COMMAND, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    def test_public_tool_and_no_public_release_tool(self) -> None:
        self.assertIsNotNone(self.tool)
        self.assertIsNone(self.app._tool_manager.get_tool(DRIVE_RELEASE))
        parameters = self.tool.parameters
        self.assertEqual(
            set(parameters["properties"]),
            {"type", "pos", "heading", "speed", "mode", "timeout_s"},
        )
        self.assertEqual(parameters.get("required"), ["type", "pos"])
        self.assertTrue(command_requires_lease(COMMAND))
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(self.tool.description.startswith(server.LEASE_TOOL_LINE))
        self.assertIn(
            "The bridge re-applies the drive on every server tick until "
            "mode='release', the infected's death or deletion, or the end of "
            "the lease that sent it; to release it, pos is the infected's "
            "current position (the infected is looked up by type within 25 m of pos).",
            self.tool.description,
        )
        for stale in (
            "may need reapplying each tick",
            "so re-send the drive every second",
            "pos_real of the last reply",
        ):
            self.assertNotIn(stale, self.tool.description)

    async def test_drive_and_release_reach_the_server_with_presence_flags(self) -> None:
        args = await self._forwarded(
            {"type": INFECTED_TYPE, "pos": list(POS), "heading": 90.0, "speed": 2.5}
        )
        self.assertEqual(args, (COMMAND, DRIVE_WIRE, "server", 15.0))
        self.assertEqual(loopback.validate_command_args(COMMAND, args[1]), (True, None))

        rel = await self._forwarded(
            {"type": INFECTED_TYPE, "pos": list(POS), "mode": "release", "timeout_s": 4.0}
        )
        self.assertEqual(rel, (COMMAND, RELEASE_WIRE, "server", 4.0))
        self.assertEqual(loopback.validate_command_args(COMMAND, rel[1]), (True, None))

    async def test_bad_args_never_reach_the_bridge(self) -> None:
        for arguments, part in (
            (
                {"type": "", "pos": list(POS), "heading": 90.0, "speed": 1.0},
                "type",
            ),
            (
                {"type": INFECTED_TYPE, "pos": list(POS), "mode": "stop"},
                "be 'release' or omitted",
            ),
            (
                {"type": INFECTED_TYPE, "pos": list(POS), "mode": "release", "heading": 90.0},
                "be omitted when mode is 'release'",
            ),
            (
                {"type": INFECTED_TYPE, "pos": list(POS), "mode": "release", "speed": 1.0},
                "be omitted when mode is 'release'",
            ),
            (
                {"type": INFECTED_TYPE, "pos": list(POS), "heading": 90.0},
                "be provided when mode is omitted",
            ),
            (
                {"type": INFECTED_TYPE, "pos": list(POS), "speed": 1.0},
                "be provided when mode is omitted",
            ),
        ):
            with self.subTest(arguments=arguments):
                self.assertIn(part, await self._refused(arguments))


class InfectedDriveBrokerTest(unittest.TestCase):
    def test_a_drive_the_queue_drops_at_the_lease_end_is_cancelled_not_released(self) -> None:
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        delivered = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([cmd["id"] for cmd in _delivered(state)], [delivered])
        _answered(state, delivered)
        _send(state, OWNER_A, token, {**DRIVE_WIRE, "heading": 180.0})
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"]["cancelled"], 1)
        self.assertEqual(released["cleanup"].get("lease_close_enqueued"), 1)
        self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
        _lease_close(state)
        self.assertEqual(_delivered(state), [])

    def test_the_end_of_a_lease_queues_one_close_and_no_infected_drive_release(self) -> None:
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        first = _send(state, OWNER_A, token, DRIVE_WIRE)
        second = _send(
            state, OWNER_A, token, {**DRIVE_WIRE, "heading": 180.0, "speed": 3.0}
        )
        stop = _send(state, OWNER_A, token, RELEASE_WIRE)
        self.assertEqual([cmd["id"] for cmd in _delivered(state)], [first, second, stop])
        for command_id in (first, second, stop):
            _answered(state, command_id)
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"].get("lease_close_enqueued"), 1)
        self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
        self.assertNotIn("infected_drive_release_unconfirmed", released["cleanup"])
        self.assertNotIn("infected_drive_release_unconfirmed", released["cleanup_degraded"])
        self.assertEqual(
            [value for value in released["cleanup_degraded"] if value.startswith("infected_drive")], []
        )
        _lease_close(state)
        self.assertEqual(_delivered(state), [])
        self.assertEqual(bound_queue(state, "server"), [])

    def test_a_clean_lease_end_reports_the_close_count_and_no_drive_degradation(self) -> None:
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        drive = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([cmd["id"] for cmd in _delivered(state)], [drive])
        _answered(state, drive)
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"].get("lease_close_enqueued"), 1)
        self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
        self.assertEqual(released["cleanup"].get("cleanup_degraded", []), [])
        self.assertEqual(released["cleanup_degraded"], [])
        _lease_close(state)
        self.assertEqual(_delivered(state), [])

    def test_a_mode_release_drive_at_the_lease_end_queues_only_the_close(self) -> None:
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        drive = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([cmd["id"] for cmd in _delivered(state)], [drive])
        _answered(state, drive)
        stop = _send(state, OWNER_A, token, RELEASE_WIRE)
        self.assertEqual([cmd["id"] for cmd in _delivered(state)], [stop])
        _answered(state, stop)
        released = _release(coordinator, OWNER_A, token)
        self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
        self.assertEqual(released["cleanup_degraded"], [])
        _lease_close(state)
        self.assertEqual(_delivered(state), [])

    def test_an_ok_false_drive_at_the_lease_end_queues_only_the_close(self) -> None:
        for ok_value in (0, False):
            with self.subTest(ok_value=ok_value):
                state, coordinator = _leased_state()
                token = _acquire(coordinator, OWNER_A)
                drive = _send(state, OWNER_A, token, DRIVE_WIRE)
                self.assertEqual([cmd["id"] for cmd in _delivered(state)], [drive])
                _answered(
                    state,
                    drive,
                    {"ok": ok_value, "error": "object_not_found"},
                )
                released = _release(coordinator, OWNER_A, token)
                self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
                self.assertEqual(released["cleanup_degraded"], [])
                _lease_close(state)
                self.assertEqual(_delivered(state), [])

    def test_the_broker_keeps_no_per_lease_infected_drive_ledger(self) -> None:
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        drive = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([cmd["id"] for cmd in _delivered(state)], [drive])
        _answered(state, drive)
        self.assertFalse(hasattr(state, "_infected_drive_by_lease"))
        released = _release(coordinator, OWNER_A, token)
        self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
        _lease_close(state)
        self.assertEqual(_delivered(state), [])

    def test_a_close_the_bridge_is_not_handed_and_quarantine_are_reported(self) -> None:
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        drive = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([cmd["id"] for cmd in _delivered(state)], [drive])
        _answered(state, drive)
        state.retail_probe = lambda: {"known": False}
        released = _release(coordinator, OWNER_A, token)
        self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
        self.assertNotIn("infected_drive_release_unconfirmed", released["cleanup_degraded"])
        self.assertEqual(released["cleanup"].get("lease_close_enqueued"), 0)
        self.assertIn("retail_quarantine", released["cleanup_degraded"])
        self.assertEqual(_delivered(state), [])

    def test_a_lease_end_after_the_run_ended_queues_and_degrades_nothing(self) -> None:
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        drive = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([cmd["id"] for cmd in _delivered(state)], [drive])
        _answered(state, drive)
        state.retire_run("drive-run", "stopped")
        released = _release(coordinator, OWNER_A, token)
        self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
        self.assertEqual(released["cleanup_degraded"], [])
        self.assertEqual(bound_queue(state, "server"), [])
        self.assertEqual(state._fire_and_forget_ids, set())


class InfectedDriveEnforceSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge_raw = BRIDGE.read_text(encoding="utf-8")
        cls.drive_raw = DRIVE.read_text(encoding="utf-8")
        cls.bridge = _strip_comments(cls.bridge_raw)
        cls.drive = _strip_comments(cls.drive_raw)

    def test_entry_holds_plain_infected_reference_and_drive_parameters(self) -> None:
        self.assertEqual(
            class_members(self.drive_raw, "MCPInfectedDriveEntry"),
            [
                ("DayZInfected", "infected"),
                ("float", "heading_rad"),
                ("float", "speed"),
            ],
        )
        # Only MCPBridge.c, MCPClientBridge.c and MCPMessages.c may name MCPResult.
        self.assertNotIn("MCPResult", self.drive)

    def test_apply_and_disable_set_all_three_overrides_in_vanilla_order(self) -> None:
        apply = _method_body(
            self.drive,
            "protected static void Apply(DayZInfected infected, MCPInfectedDriveEntry entry)",
        )
        _in_order(
            self,
            apply,
            "controller = infected.GetInputController();",
            "controller.OverrideTurnSpeed(true, Math.PI2);",
            "controller.OverrideMovementSpeed(true, entry.speed);",
            "controller.OverrideHeading(true, entry.heading_rad);",
        )
        disable = _method_body(
            self.drive, "protected static void Disable(DayZInfected infected)"
        )
        _in_order(
            self,
            disable,
            "controller = infected.GetInputController();",
            "controller.OverrideMovementSpeed(false, 0.0);",
            "controller.OverrideHeading(false, 0.0);",
            "controller.OverrideTurnSpeed(false, 0.0);",
        )

    def test_the_tick_reapplies_all_three_overrides_and_drops_dead_or_null_entries_backwards(
        self,
    ) -> None:
        tick = _method_body(self.drive, "static void Tick()")
        _in_order(
            self,
            tick,
            "index = s_Driven.Count() - 1;",
            "while (index >= 0)",
            "if (!entry || !infected || !infected.IsAlive())",
            "s_Driven.Remove(index);",
            "Disable(infected);",
            "Apply(infected, entry);",
            "index = index - 1;",
        )
        shutdown = _method_body(self.drive, "static void ShutdownAll()")
        _in_order(self, shutdown, "Disable(entry.infected);", "s_Driven.Clear();")

    def test_bridge_hooks_and_dispatch_wire_infected_drive(self) -> None:
        self.assertIn(
            f'SERVER_ARG_CONTRACT_HASH = "{EXPECTED_SERVER_ARG_CONTRACT_HASH}";',
            self.bridge,
        )

        on_tick = _method_body(self.bridge, "void OnTick(float timeslice)")
        _in_order(
            self,
            on_tick,
            "MCPBotControl.Tick(this, m_ElapsedS);",
            "MCPSurvivorDrive.Tick(this);",
            "MCPInfectedDrive.Tick();",
        )

        shutdown = _method_body(self.bridge, "void Shutdown()")
        _in_order(
            self,
            shutdown,
            "MCPBotControl.ShutdownAll(this);",
            "MCPSurvivorDrive.ShutdownAll(this);",
            "MCPInfectedDrive.ShutdownAll();",
        )

        dispatch = _method_body(self.bridge, "protected void Dispatch(MCPCommand command)")
        _in_order(
            self,
            dispatch,
            'else if (command.cmd == "infected_drive")',
            'else if (command.cmd == "survivor_drive")',
            'else if (command.cmd == "survivor_drive_release")',
            'else if (command.cmd == "entities_query")',
        )

        drive_body = _method_body(
            self.bridge,
            "protected bool DispatchInfectedDrive(MCPCommand command, MCPResult result)",
        )
        _in_order(
            self,
            drive_body,
            "MCPInfectedDrive.Release(infected);",
            "controller.OverrideMovementSpeed(false, 0.0);",
            "controller.OverrideHeading(false, 0.0);",
            "controller.OverrideTurnSpeed(false, 0.0);",
            "MCPInfectedDrive.Drive(infected, command.args.heading * Math.DEG2RAD, command.args.speed);",
            "result.ok = true;",
        )

    def test_no_infected_drive_release_command_because_the_poll_url_has_no_room(self) -> None:
        # No infected_drive_release anywhere: any new server command name
        # pushes the saturated server poll URL with the Expansion AI adapter
        # past the engine's 1024-character limit (ENGINE_URL_MAX).
        self.assertNotIn(DRIVE_RELEASE, loopback.WHITELISTED_COMMANDS)
        self.assertNotIn(DRIVE_RELEASE, loopback.SERVER_COMMANDS)
        self.assertNotIn(DRIVE_RELEASE, self.bridge)
        self.assertNotIn(DRIVE_RELEASE, server.SERVER_ARG_CONTRACT)
        self.assertEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, "3b800b7c7ee0674c")

    def test_the_lease_close_shuts_the_registry_down_before_the_infected_record(self) -> None:
        close = _method_body(
            self.bridge,
            "protected bool DispatchLeaseClose(MCPCommand command, MCPResult result)",
        )
        _in_order(
            self,
            close,
            "MCPSurvivorDrive.ShutdownAll(this);",
            "MCPInfectedDrive.ShutdownAll();",
            "m_InfectedOverridden",
        )
        shutdown = _method_body(self.bridge, "void Shutdown()")
        self.assertIn("MCPInfectedDrive.ShutdownAll();", shutdown)


DRIVE_METHODS = (
    "static void Drive(DayZInfected infected, float headingRad, float speed)",
    "static void Release(DayZInfected infected)",
    "static void Tick()",
    "static void ShutdownAll()",
    "protected static void Apply(DayZInfected infected, MCPInfectedDriveEntry entry)",
    "protected static void Disable(DayZInfected infected)",
    "protected static int Find(DayZInfected infected)",
)
_DRIVE_REWRITES = (
    (re.compile(r"(?<![\w.])s_Driven\b"), "G.s_Driven"),
    (re.compile(r"new array<ref MCPInfectedDriveEntry>\(\)"), "NewArray()"),
    (re.compile(r"new MCPInfectedDriveEntry\(\)"), "NewEntry()"),
)


def _method_name(signature: str) -> str:
    return signature[: signature.index("(")].split()[-1]


class _InfectedController:
    """An infected's server DayZInfectedInputController: the three AI overrides
    (turn, speed, heading), each the last (state, value) it was given, with
    every call recorded in `calls`."""

    def __init__(self) -> None:
        self.turn: tuple[bool, float] = (False, 0.0)
        self.speed: tuple[bool, float] = (False, 0.0)
        self.heading: tuple[bool, float] = (False, 0.0)
        self.calls: list[tuple[str, bool, float]] = []

    def OverrideTurnSpeed(self, state: bool, value: float) -> None:
        if type(state) is not bool:
            raise AssertionError(f"OverrideTurnSpeed state {state!r} is not bool")
        self.calls.append(("turn", state, float(value)))
        self.turn = (state, float(value))

    def OverrideMovementSpeed(self, state: bool, value: float) -> None:
        if type(state) is not bool:
            raise AssertionError(f"OverrideMovementSpeed state {state!r} is not bool")
        self.calls.append(("speed", state, float(value)))
        self.speed = (state, float(value))

    def OverrideHeading(self, state: bool, value: float) -> None:
        if type(state) is not bool:
            raise AssertionError(f"OverrideHeading state {state!r} is not bool")
        self.calls.append(("heading", state, float(value)))
        self.heading = (state, float(value))

    def reset_by_ai(self) -> None:
        """Simulate the vanilla infected AI overwriting the controller between frames."""
        self.turn = (False, 0.0)
        self.speed = (False, 0.0)
        self.heading = (False, 0.0)
        self.calls.clear()

    def driving(self) -> tuple[float, float, float] | None:
        """(turn_speed, speed, heading_rad) while all three overrides are ON,
        None while all three are OFF; a mixed state fails."""
        states = (self.turn[0], self.speed[0], self.heading[0])
        if states == (True, True, True):
            return (round(self.turn[1], 9), self.speed[1], round(self.heading[1], 9))
        if states == (False, False, False):
            return None
        raise AssertionError(
            f"the overrides disagree: turn={self.turn} speed={self.speed} heading={self.heading}"
        )


class _Vector:
    def __init__(self, xyz: tuple[float, float, float]) -> None:
        self.xyz = list(xyz)

    def Coordinate(self, index: int) -> float:
        self._check(index)
        return self.xyz[index]

    def SetCoordinate(self, index: int, value: float) -> None:
        self._check(index)
        self.xyz[index] = value

    def _check(self, index: int) -> None:
        if type(index) is not int or not 0 <= index < 3:
            raise AssertionError(f"vector index {index!r}")


class _CopiedVector(EnforceVector):
    def __init__(self, value: object = (0.0, 0.0, 0.0)) -> None:
        super().__init__(value.xyz if isinstance(value, _Vector) else value)  # type: ignore[arg-type]

    def Coordinate(self, index: int) -> float:
        return self[index]

    def SetCoordinate(self, index: int, value: float) -> None:
        self[index] = value


class _Infected:
    def __init__(
        self,
        type_name: str = INFECTED_TYPE,
        position: tuple[float, float, float] = tuple(POS),  # type: ignore[arg-type]
    ) -> None:
        self.type_name = type_name
        self.position = position
        self.alive = True
        self.has_controller = True
        self.controller = _InfectedController()

    def GetInputController(self) -> _InfectedController | None:
        return self.controller if self.has_controller else None

    def IsAlive(self) -> bool:
        return self.alive

    def GetPosition(self) -> _Vector:
        return _Vector(self.position)


class _Thing:
    """A non-infected world object matched by FindUniqueObjectNearType."""

    def __init__(
        self,
        type_name: str,
        position: tuple[float, float, float] = tuple(POS),  # type: ignore[arg-type]
    ) -> None:
        self.type_name = type_name
        self.position = position


class _Array:
    """Enforce's array as the registry uses it: Remove(index) moves the last
    element into the removed slot."""

    def __init__(self) -> None:
        self.items: list[object] = []

    def Count(self) -> int:
        return len(self.items)

    def Get(self, index: int) -> object:
        self._check(index)
        return self.items[index]

    def Insert(self, item: object) -> int:
        self.items.append(item)
        return len(self.items) - 1

    def Remove(self, index: int) -> None:
        self._check(index)
        self.items[index] = self.items[-1]
        self.items.pop()

    def Clear(self) -> None:
        self.items.clear()

    def _check(self, index: int) -> None:
        if type(index) is not int or not 0 <= index < len(self.items):
            raise AssertionError(
                f"index {index!r} outside an array of {len(self.items)}"
            )


class _OverriddenArray(_Array):
    """array<DayZInfected> as DispatchInfectedDrive uses m_InfectedOverridden:
    Find and RemoveItem by identity, Insert as _Array."""

    def Find(self, value: object) -> int:
        for index, item in enumerate(self.items):
            if item is value:
                return index
        return -1

    def RemoveItem(self, value: object) -> None:
        index = self.Find(value)
        if index >= 0:
            del self.items[index]


class _DriveModel:
    """MCPInfectedDrive's methods (MCP_InfectedDrive.c), translated and run on
    one registry (s_Driven, here G.s_Driven)."""

    def __init__(self) -> None:
        drive = DRIVE.read_text(encoding="utf-8")
        self.G = SimpleNamespace(s_Driven=None)
        self.world: list[object] = []
        entry = declared_fields(drive, "MCPInfectedDriveEntry")
        self.namespace: dict[str, object] = {
            "G": self.G,
            "NewArray": _Array,
            "NewEntry": lambda: SimpleNamespace(**entry),
            "DayZInfected": SimpleNamespace(
                Cast=lambda item: item if isinstance(item, _Infected) else None
            ),
            "Math": SimpleNamespace(PI2=2.0 * math.pi, DEG2RAD=math.pi / 180.0),
        }
        for signature in DRIVE_METHODS:
            body = method_body(drive, signature)
            for pattern, replacement in _DRIVE_REWRITES:
                body = pattern.sub(replacement, body)
            translate(
                signature + "\n{" + body + "}\n",
                signature,
                self.namespace,
                class_types=(
                    "MCPInfectedDriveEntry",
                    "DayZInfected",
                    "DayZInfectedInputController",
                ),
            )

    def spawn(
        self,
        position: tuple[float, float, float] = tuple(POS),  # type: ignore[arg-type]
        type_name: str = INFECTED_TYPE,
    ) -> _Infected:
        infected = _Infected(type_name=type_name, position=position)
        self.world.append(infected)
        return infected

    def delete_entity(self, entity: object) -> None:
        """Simulate engine deletion: remove from world and null plain DayZInfected
        references held in MCPInfectedDriveEntry (enentity.c:164)."""
        if entity in self.world:
            self.world.remove(entity)
        if self.G.s_Driven is not None:
            for entry in self.G.s_Driven.items:
                if entry.infected is entity:
                    entry.infected = None

    def call(self, name: str, *args: object) -> object:
        return self.namespace[name](*args)

    def drive(
        self,
        infected: _Infected,
        heading_deg: float,
        speed: float,
    ) -> None:
        self.call("Drive", infected, math.radians(heading_deg), speed)

    def registry(self) -> list[tuple[object, float, float]]:
        if self.G.s_Driven is None:
            return []
        return [
            (
                entry.infected,
                round(entry.heading_rad, 9),
                entry.speed,
            )
            for entry in self.G.s_Driven.items
        ]

    def writes(self) -> int:
        return sum(
            len(item.controller.calls)
            for item in self.world
            if isinstance(item, _Infected)
        )


HANDLER_METHODS = (
    "protected bool IsFiniteFloat(float value)",
    "protected bool DispatchInfectedDrive(MCPCommand command, MCPResult result)",
)
HOOK_METHODS = (
    "void OnTick(float timeslice)",
    "void Shutdown()",
)
_HANDLER_REWRITES = (
    (re.compile(r"new array<float>\(\)"), "NewFloats()"),
    (
        re.compile(r"\bcurrent\[([012])\]\s*=\s*([^;]+);"),
        r"current.SetCoordinate(\1, \2);",
    ),
    (re.compile(r"\bcurrent\[([012])\]"), r"current.Coordinate(\1)"),
    (re.compile(r"(?<![\w.])(m_\w+)"), r"this.\1"),
    (re.compile(r"([\w.]+) / ([\w.]+)"), r"FloatQuotient(\1, \2)"),
)
_FLT_MAX = 3.4028234663852886e38
_ARG_ZEROES: dict[str, object] = {
    "type": "",
    "pos": (),
    "heading": 0.0,
    "heading_set": False,
    "speed": 0.0,
    "speed_set": False,
    "mode": "",
    "command_id": 0,
}


def _arg_defaults(messages: str) -> dict[str, object]:
    if (
        re.search(r"(?m)^const float MCP_ARG_FLOAT_UNSET = float\.MAX;$", messages)
        is None
    ):
        raise AssertionError("MCP_ARG_FLOAT_UNSET is not float.MAX")
    values = dict(_ARG_ZEROES)
    for name, value in re.findall(
        r"(\w+) = ([^;]+);", method_body(messages, "void MCPArgs()")
    ):
        if name in values:
            if value == "MCP_ARG_FLOAT_UNSET":
                values[name] = _FLT_MAX
            elif name == "pos" and value == "new array<float>()":
                values[name] = ()
            else:
                raise AssertionError(f"MCPArgs() sets {name} to {value}")
    return values


def _float_quotient(dividend: float, divisor: float) -> float:
    if type(dividend) is not float or type(divisor) is not float:
        raise AssertionError(
            f"{dividend!r} / {divisor!r} is not a division of two floats"
        )
    return dividend / divisor


class _Result(SimpleNamespace):
    def __setattr__(self, name: str, value: object) -> None:
        if name not in self.__dict__:
            raise AssertionError(f"{name} is not a declared field")
        super().__setattr__(name, value)


class _HandlerModel(_DriveModel):
    """_DriveModel with MCPBridge.c's DispatchInfectedDrive, OnTick and
    Shutdown translated on top."""

    def __init__(self) -> None:
        super().__init__()
        bridge = BRIDGE.read_text(encoding="utf-8")
        messages = MESSAGES.read_text(encoding="utf-8")
        lookup = re.search(
            r"protected const float OBJECT_LOOKUP_RADIUS = ([0-9.]+);", bridge
        )
        if lookup is None:
            raise AssertionError("OBJECT_LOOKUP_RADIUS not found")
        threshold = re.search(
            r"protected const int PENDING_POLL_THRESHOLD = ([0-9]+);", bridge
        )
        if threshold is None:
            raise AssertionError("PENDING_POLL_THRESHOLD not found")
        self.lookup_radius = float(lookup.group(1))
        self.result_fields = declared_fields(messages, "MCPResult")
        self.arg_defaults = _arg_defaults(messages)
        self.handlers: dict[str, object] = {
            "float": SimpleNamespace(MAX=_FLT_MAX),
            "OBJECT_LOOKUP_RADIUS": self.lookup_radius,
            "PENDING_POLL_THRESHOLD": int(threshold.group(1)),
            "NewFloats": _Array,
            "FloatQuotient": _float_quotient,
            "_vector": _CopiedVector,
            "Vector": lambda x, y, z: _Vector((x, y, z)),
            "FindUniqueObjectNearType": self._find_unique_object_near_type,
            "DayZInfected": self.namespace["DayZInfected"],
            "Math": self.namespace["Math"],
            "MCPResultCallback": SimpleNamespace(Cast=lambda item: item),
            "MCPBotControl": SimpleNamespace(
                Tick=lambda _bridge, _now_s: None,
                ShutdownAll=lambda _bridge: None,
            ),
            "MCPSurvivorDrive": SimpleNamespace(
                Tick=lambda _bridge: None,
                ShutdownAll=lambda _bridge: None,
            ),
            "MCPInfectedDrive": SimpleNamespace(
                **{
                    _method_name(signature): self.namespace[_method_name(signature)]
                    for signature in DRIVE_METHODS
                    if not signature.startswith("protected ")
                }
            ),
            "TryInit": lambda: None,
            "ProcessJobs": lambda: None,
            "DrainPending": lambda: None,
            "StartPoll": lambda: None,
        }
        for signature in (*HANDLER_METHODS, *HOOK_METHODS):
            body = method_body(bridge, signature)
            for pattern, replacement in _HANDLER_REWRITES:
                body = pattern.sub(replacement, body)
            translate(
                signature + "\n{" + body + "}\n",
                signature,
                self.handlers,
                class_types=(
                    "Object",
                    "DayZInfected",
                    "DayZInfectedInputController",
                    "vector",
                    "MCPResultCallback",
                ),
            )
        self.bridge = SimpleNamespace(
            m_Tick=0,
            m_ElapsedS=0.0,
            m_SincePollS=0.0,
            m_Configured=True,
            m_PollInFlight=False,
            m_RepollNow=False,
            m_Accum=0.0,
            m_PollHz=5.0,
            m_Backoff=0.0,
            m_PollCallback=None,
            m_PollVersion=EnforceString(b""),
            m_Players=None,
            m_Ctx=SimpleNamespace(reset=lambda: None),
            m_InfectedOverridden=_OverriddenArray(),
            **{
                name: _Array()
                for name in (
                    "m_Pending",
                    "m_CallbackRefs",
                    "m_ResultCallbackPool",
                    "m_Jobs",
                    "m_ReadyObjects",
                    "m_ReadyProxyCargos",
                    "m_TelemetryFixtureLinePool",
                )
            },
        )
        self.handlers["this"] = self.bridge

    def _find_unique_object_near_type(
        self,
        type_name: EnforceString,
        center: _Vector,
        radius: float,
        error: EnforceString,
    ) -> object | None:
        if radius != self.lookup_radius:
            raise AssertionError(
                f"expected lookup radius {self.lookup_radius}, got {radius}"
            )
        wanted = type_name.text
        matches = [
            obj
            for obj in self.world
            if getattr(obj, "type_name", None) == wanted
            and math.dist(center.xyz, getattr(obj, "position")) <= radius
        ]
        if not matches:
            error.data = b"object_not_found"
            return None
        if len(matches) > 1:
            error.data = b"object_ambiguous"
            return None
        return matches[0]

    def dispatch(
        self, command_id: int, cmd: str, wire: dict | None
    ) -> tuple[_Result, object]:
        if wire is None:
            args = None
        else:
            unknown = set(wire) - set(self.arg_defaults)
            if unknown:
                raise AssertionError(
                    f"keys these handlers do not read: {sorted(unknown)}"
                )
            values = {**self.arg_defaults, **wire}
            values["type"] = EnforceString.of(str(values["type"]))
            values["mode"] = EnforceString.of(str(values["mode"]))
            raw_pos = values["pos"]
            if raw_pos is not None:
                pos_array = _Array()
                for item in raw_pos:
                    pos_array.Insert(float(item))
                values["pos"] = pos_array
            args = SimpleNamespace(**values)
        command = SimpleNamespace(id=command_id, cmd=EnforceString.of(cmd), args=args)
        fresh_fields = {
            name: EnforceString(val.data) if isinstance(val, EnforceString) else val
            for name, val in self.result_fields.items()
        }
        result = _Result(**fresh_fields)
        handler = {
            COMMAND: "DispatchInfectedDrive",
        }[cmd]
        post_now = self.handlers[handler](command, result)
        return result, post_now

    def tick(self, timeslice: float = 0.1) -> None:
        self.handlers["OnTick"](timeslice)

    def shutdown(self) -> None:
        self.handlers["Shutdown"]()


class InfectedDriveModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = _DriveModel()

    def test_a_drive_registers_and_a_second_drive_replaces_it(self) -> None:
        infected = self.model.spawn()
        self.model.drive(infected, 90.0, 2.5)
        self.assertEqual(
            self.model.registry(),
            [(infected, round(math.radians(90.0), 9), 2.5)],
        )
        self.assertEqual(
            infected.controller.driving(),
            (round(2.0 * math.pi, 9), 2.5, round(math.radians(90.0), 9)),
        )
        self.assertEqual(
            infected.controller.calls,
            [
                ("turn", True, 2.0 * math.pi),
                ("speed", True, 2.5),
                ("heading", True, math.radians(90.0)),
            ],
        )

        self.model.drive(infected, 180.0, 3.0)
        self.assertEqual(
            self.model.registry(),
            [(infected, round(math.radians(180.0), 9), 3.0)],
        )
        self.assertEqual(
            infected.controller.driving(),
            (round(2.0 * math.pi, 9), 3.0, round(math.radians(180.0), 9)),
        )

    def test_the_tick_reapplies_all_three_overrides_after_ai_reset(self) -> None:
        infected = self.model.spawn()
        self.model.drive(infected, 90.0, 2.5)
        infected.controller.reset_by_ai()
        self.assertIsNone(infected.controller.driving())

        self.model.call("Tick")
        self.assertEqual(
            self.model.registry(),
            [(infected, round(math.radians(90.0), 9), 2.5)],
        )
        self.assertEqual(
            infected.controller.driving(),
            (round(2.0 * math.pi, 9), 2.5, round(math.radians(90.0), 9)),
        )
        self.assertEqual(
            infected.controller.calls,
            [
                ("turn", True, 2.0 * math.pi),
                ("speed", True, 2.5),
                ("heading", True, math.radians(90.0)),
            ],
        )

    def test_mode_release_removes_entry_and_disables_even_without_entry(self) -> None:
        model = _HandlerModel()
        infected = model.spawn()
        result_drive, post_now = model.dispatch(41, COMMAND, DRIVE_WIRE)
        self.assertIs(post_now, True)
        self.assertIs(result_drive.ok, True)
        self.assertEqual(result_drive.pos_real.items, list(POS))
        self.assertEqual(result_drive.type, EnforceString.of(INFECTED_TYPE))
        self.assertIs(result_drive.found, True)
        self.assertEqual(model.bridge.m_InfectedOverridden.items, [infected])

        # A second drive on the same infected records it only once.
        result_second, _post_now = model.dispatch(44, COMMAND, DRIVE_WIRE)
        self.assertIs(result_second.ok, True)
        self.assertEqual(model.bridge.m_InfectedOverridden.items, [infected])

        infected.position = (4205.0, 338.66, 10610.0)
        result_rel, _post_now = model.dispatch(
            42,
            COMMAND,
            {"type": INFECTED_TYPE, "pos": [4205.0, 338.66, 10610.0], "mode": "release"},
        )
        self.assertIs(result_rel.ok, True)
        self.assertEqual(model.registry(), [])
        self.assertEqual(model.bridge.m_InfectedOverridden.items, [])
        self.assertIsNone(infected.controller.driving())
        self.assertEqual(
            infected.controller.calls[-3:],
            [
                ("speed", False, 0.0),
                ("heading", False, 0.0),
                ("turn", False, 0.0),
            ],
        )

        # Releasing an infected that has no entry still disables all three overrides.
        infected.controller.calls.clear()
        result_idle, _post_now = model.dispatch(
            43,
            COMMAND,
            {"type": INFECTED_TYPE, "pos": [4205.0, 338.66, 10610.0], "mode": "release"},
        )
        self.assertIs(result_idle.ok, True)
        self.assertEqual(
            infected.controller.calls,
            [
                ("speed", False, 0.0),
                ("heading", False, 0.0),
                ("turn", False, 0.0),
            ],
        )

    def test_death_deletion_and_shutdown_remove_entries_and_disable_existing_infected(
        self,
    ) -> None:
        dying = self.model.spawn((4200.0, 338.66, 10610.0))
        vanishing = self.model.spawn((4250.0, 338.66, 10610.0))
        staying = self.model.spawn((4300.0, 338.66, 10610.0))
        self.model.drive(dying, 0.0, 1.0)
        self.model.drive(vanishing, 90.0, 2.0)
        self.model.drive(staying, 180.0, 3.0)

        dying.alive = False
        vanishing_calls_before = list(vanishing.controller.calls)
        self.model.delete_entity(vanishing)
        staying.controller.reset_by_ai()

        self.model.call("Tick")
        self.assertEqual(
            self.model.registry(),
            [(staying, round(math.radians(180.0), 9), 3.0)],
        )
        self.assertIsNone(dying.controller.driving())
        self.assertEqual(vanishing.controller.calls, vanishing_calls_before)
        self.assertEqual(
            staying.controller.driving(),
            (round(2.0 * math.pi, 9), 3.0, round(math.radians(180.0), 9)),
        )

        self.model.call("ShutdownAll")
        self.assertEqual(self.model.registry(), [])
        self.assertIsNone(staying.controller.driving())
        calls_after_shutdown = len(staying.controller.calls)
        self.model.call("Tick")
        self.assertEqual(len(staying.controller.calls), calls_after_shutdown)

    def test_the_tick_walks_backwards_so_two_removals_skip_no_entry(self) -> None:
        first = self.model.spawn((4200.0, 338.66, 10610.0))
        middle = self.model.spawn((4250.0, 338.66, 10610.0))
        last = self.model.spawn((4300.0, 338.66, 10610.0))
        self.model.drive(first, 0.0, 1.0)
        self.model.drive(middle, 90.0, 2.5)
        self.model.drive(last, 180.0, 3.0)

        first.alive = False
        last.alive = False
        middle.controller.reset_by_ai()
        self.model.call("Tick")

        self.assertEqual(
            self.model.registry(),
            [(middle, round(math.radians(90.0), 9), 2.5)],
        )
        self.assertIsNone(first.controller.driving())
        self.assertIsNone(last.controller.driving())
        self.assertEqual(
            middle.controller.driving(),
            (round(2.0 * math.pi, 9), 2.5, round(math.radians(90.0), 9)),
        )

    def test_each_refusal_of_dispatch_infected_drive_changes_nothing(self) -> None:
        model = _HandlerModel()
        driven = model.spawn((4300.0, 338.66, 10610.0))
        model.dispatch(
            40, COMMAND, {**DRIVE_WIRE, "pos": [4300.0, 338.66, 10610.0]}
        )
        model.world.append(_Thing("Land_Shed_M1", (4400.0, 338.66, 10610.0)))
        no_ctrl = model.spawn((4500.0, 338.66, 10610.0))
        no_ctrl.has_controller = False
        model.spawn((4600.0, 338.66, 10610.0))
        model.spawn((4605.0, 338.66, 10610.0))

        before = (model.registry(), model.writes())
        for error, wire in (
            ("bad_args", None),
            ("bad_args", {**DRIVE_WIRE, "type": ""}),
            ("bad_args", {**DRIVE_WIRE, "pos": None}),
            ("bad_args", {**DRIVE_WIRE, "pos": [1.0, 2.0]}),
            ("bad_args", {**DRIVE_WIRE, "pos": [math.nan, 0.0, 0.0]}),
            ("bad_args", {**DRIVE_WIRE, "heading_set": False}),
            ("bad_args", {**DRIVE_WIRE, "speed_set": False}),
            ("bad_args", {**DRIVE_WIRE, "heading": math.inf}),
            ("bad_args", {**DRIVE_WIRE, "speed": math.nan}),
            ("object_not_found", {**DRIVE_WIRE, "pos": [1000.0, 0.0, 1000.0]}),
            ("object_ambiguous", {**DRIVE_WIRE, "pos": [4602.0, 338.66, 10610.0]}),
            (
                "not_infected",
                {
                    **DRIVE_WIRE,
                    "type": "Land_Shed_M1",
                    "pos": [4400.0, 338.66, 10610.0],
                },
            ),
            (
                "no_input_controller",
                {**DRIVE_WIRE, "pos": [4500.0, 338.66, 10610.0]},
            ),
        ):
            with self.subTest(error=error, wire=wire):
                result, post_now = model.dispatch(41, COMMAND, wire)
                self.assertIs(post_now, True)
                self.assertIs(result.ok, False)
                self.assertEqual(result.error, EnforceString.of(error))
                self.assertEqual((model.registry(), model.writes()), before)
        self.assertIsNotNone(driven.controller.driving())

    def test_after_the_lease_ends_the_next_owner_inherits_no_drive(self) -> None:
        model = _HandlerModel()
        infected = model.spawn()
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)

        first = _send(state, OWNER_A, token, DRIVE_WIRE)
        second = _send(
            state, OWNER_A, token, {**DRIVE_WIRE, "heading": 180.0, "speed": 3.0}
        )
        delivered = _delivered(state)
        self.assertEqual([cmd["id"] for cmd in delivered], [first, second])

        for cmd in delivered:
            res, _ = model.dispatch(cmd["id"], cmd["cmd"], cmd["args"])
            self.assertIs(res.ok, True)
            _answered(
                state,
                cmd["id"],
                {
                    "ok": 1,
                    "pos_real": list(res.pos_real.items),
                },
            )

        self.assertEqual(
            infected.controller.driving(),
            (round(2.0 * math.pi, 9), 3.0, round(math.radians(180.0), 9)),
        )
        # Two drives on the same infected record it once in m_InfectedOverridden.
        self.assertEqual(model.bridge.m_InfectedOverridden.items, [infected])
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"].get("lease_close_enqueued"), 1)
        self.assertNotIn("infected_drive_release_enqueued", released["cleanup"])
        self.assertEqual(released["cleanup_degraded"], [])

        _acquire(coordinator, OWNER_B)
        _lease_close(state)
        # DispatchLeaseClose calls MCPInfectedDrive.ShutdownAll (the source
        # test above pins that order), which ends every drive.
        model.call("ShutdownAll")
        self.assertIsNone(infected.controller.driving())

        writes = model.writes()
        for _ in range(3):
            model.tick()
        self.assertEqual(model.writes(), writes)
        self.assertEqual(model.registry(), [])


class InfectedDriveBridgeHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = _HandlerModel()
        self.first = self.model.spawn((4200.0, 338.66, 10610.0))
        self.second = self.model.spawn((4260.0, 338.66, 10610.0))
        self.model.dispatch(41, COMMAND, DRIVE_WIRE)
        self.model.dispatch(
            42,
            COMMAND,
            {
                **DRIVE_WIRE,
                "pos": [4260.0, 338.66, 10610.0],
                "heading": 0.0,
                "speed": 3.0,
            },
        )

    def test_the_bridge_tick_reapplies_live_drives_and_drops_dead_or_deleted_ones(
        self,
    ) -> None:
        self.first.controller.reset_by_ai()
        self.second.alive = False
        self.model.tick()
        self.assertEqual(
            self.first.controller.driving(),
            (round(2.0 * math.pi, 9), 2.5, round(math.radians(90.0), 9)),
        )
        self.assertEqual(
            self.first.controller.calls,
            [
                ("turn", True, 2.0 * math.pi),
                ("speed", True, 2.5),
                ("heading", True, math.radians(90.0)),
            ],
        )
        self.assertIsNone(self.second.controller.driving())
        self.assertEqual(
            self.model.registry(),
            [(self.first, round(math.radians(90.0), 9), 2.5)],
        )

    def test_the_bridge_shutdown_ends_every_drive(self) -> None:
        self.model.shutdown()
        self.assertEqual(self.model.registry(), [])
        self.assertIsNone(self.first.controller.driving())
        self.assertIsNone(self.second.controller.driving())


if __name__ == "__main__":
    unittest.main()
