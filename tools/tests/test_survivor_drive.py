"""survivor_drive (inbox c801): wire, admission and the server bridge contract."""

from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path
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
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue


COMMAND = "survivor_drive"
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
DRIVE = addon_root() / "scripts" / "5_Mission" / "MCP_SurvivorDrive.c"
# The arg-contract hash of main before this verb: the PBO a user may still run.
PREVIOUS_ARG_CONTRACT_HASH = "4f90284258023d98"
DRIVE_WIRE = {"object_id": 7, "heading": 90.0, "heading_set": True, "speed": 1.0, "speed_set": True}
RELEASE_WIRE = {"object_id": 7, "mode": "release"}
HEADING_RULE = "be a finite number of compass degrees within [-360, 360]"


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
    state.daemon_generation = "gen-drive"
    state.install_bound_peer(instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="drive-run")
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
        f"{EXPECTED_BRIDGE_VERSION}~1.29.0",
        instance=INST_SERVER,
        source_pid=PID_SERVER,
        caps=",".join(commands),
        ach=ach,
    )


def _schema_keys(command: str) -> set[str]:
    variants, _delegated = loopback._COMMAND_ARG_SCHEMAS[command]
    keys: set[str] = set()
    for required, optional, _validators in variants:
        keys |= required | optional
    return keys


class SurvivorDriveToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tool = self.app._tool_manager.get_tool(COMMAND)

    async def _forwarded(self, arguments: dict) -> tuple:
        with patch.object(
            self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1, "pos_real": [1.0, 2.0, 3.0]})
        ) as call:
            await self.app.call_tool(COMMAND, arguments)
        call.assert_awaited_once()
        return call.await_args.args

    async def _refused(self, arguments: dict) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(COMMAND, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    def test_closed_schema_with_object_id_required(self) -> None:
        self.assertIsNotNone(self.tool)
        parameters = self.tool.parameters
        self.assertEqual(
            set(parameters["properties"]), {"object_id", "heading", "speed", "mode", "timeout_s"}
        )
        self.assertEqual(parameters.get("required"), ["object_id"])
        self.assertIs(parameters.get("additionalProperties"), False)
        self.assertIn(COMMAND, server._CLOSED_SCHEMA_TOOLS)

    def test_lease_verb_and_not_read_only(self) -> None:
        self.assertTrue(command_requires_lease(COMMAND))
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(self.tool.description.startswith(server.LEASE_TOOL_LINE))
        for code in (
            "bad_args",
            "object_id_unknown",
            "object_id_stale",
            "bad_target",
            "refused_player_controlled",
            "player_dead",
            "bot_busy",
            "no_input_controller",
            "bridge_capability_missing",
            "arg_contract_mismatch",
        ):
            self.assertIn(code, self.tool.description)

    async def test_each_gait_and_release_reach_the_server_as_the_ingress_takes_them(self) -> None:
        for arguments, wire in (
            (
                {"object_id": 7, "heading": 90, "speed": "walk"},
                {"object_id": 7, "heading": 90.0, "heading_set": True, "speed": 1.0, "speed_set": True},
            ),
            (
                {"object_id": 7, "heading": -45.5, "speed": "jog"},
                {"object_id": 7, "heading": -45.5, "heading_set": True, "speed": 2.0, "speed_set": True},
            ),
            (
                {"object_id": 9, "heading": 360.0, "speed": "sprint"},
                {"object_id": 9, "heading": 360.0, "heading_set": True, "speed": 3.0, "speed_set": True},
            ),
            (
                {"object_id": 9, "heading": -360, "speed": "walk"},
                {"object_id": 9, "heading": -360.0, "heading_set": True, "speed": 1.0, "speed_set": True},
            ),
            ({"object_id": 7, "mode": "release"}, {"object_id": 7, "mode": "release"}),
        ):
            with self.subTest(arguments=arguments):
                args = await self._forwarded(arguments)
                self.assertEqual(args, (COMMAND, wire, "server", 15.0))
                if "heading" in wire:
                    self.assertIsInstance(args[1]["heading"], float)
                self.assertEqual(loopback.validate_command_args(COMMAND, args[1]), (True, None))
        args = await self._forwarded({"object_id": 7, "mode": "release", "timeout_s": 4.0})
        self.assertEqual(args[3], 4.0)

    async def test_bad_args_never_reach_the_bridge(self) -> None:
        nan = math.nan
        for arguments, part in (
            (
                {"object_id": 7, "mode": "release", "heading": 90.0},
                "bad_args: heading 90.0 must be omitted when mode is 'release'",
            ),
            (
                {"object_id": 7, "mode": "release", "speed": "walk"},
                "bad_args: speed 'walk' must be omitted when mode is 'release'",
            ),
            (
                {"object_id": 7, "mode": "release", "heading": 90.0, "speed": "walk"},
                "must be omitted when mode is 'release'",
            ),
            ({"object_id": 7, "heading": 90.0}, "bad_args: speed None must be provided when mode is omitted"),
            ({"object_id": 7, "speed": "walk"}, "bad_args: heading None must be provided when mode is omitted"),
            ({"object_id": 7}, "bad_args: heading None must be provided when mode is omitted"),
            ({"object_id": 7, "heading": 90.0, "speed": "run"}, "be one of 'walk', 'jog' or 'sprint'"),
            ({"object_id": 7, "heading": 90.0, "speed": "Walk"}, "be one of 'walk', 'jog' or 'sprint'"),
            ({"object_id": 7, "heading": 90.0, "speed": "1"}, "be one of 'walk', 'jog' or 'sprint'"),
            ({"object_id": 7, "heading": 90.0, "speed": ""}, "be one of 'walk', 'jog' or 'sprint'"),
            ({"object_id": 7, "heading": nan, "speed": "walk"}, HEADING_RULE),
            ({"object_id": 7, "heading": math.inf, "speed": "walk"}, HEADING_RULE),
            ({"object_id": 7, "heading": -math.inf, "speed": "walk"}, HEADING_RULE),
            ({"object_id": 7, "heading": 360.5, "speed": "walk"}, HEADING_RULE),
            ({"object_id": 7, "heading": -361, "speed": "walk"}, HEADING_RULE),
            ({"object_id": 0, "mode": "release"}, "bad_args: object_id 0 must be a positive int"),
            ({"object_id": -3, "heading": 90.0, "speed": "walk"}, "bad_args: object_id -3 must be a positive int"),
            ({"object_id": 7, "mode": "stop"}, "bad_args: mode 'stop' must be 'release' or omitted"),
            ({"object_id": 7, "mode": ""}, "bad_args: mode '' must be 'release' or omitted"),
            ({"object_id": 7, "mode": "release", "pos": [0.0, 0.0, 0.0]}, "unexpected arguments: pos"),
            (
                {"object_id": 7, "heading": 90.0, "speed": "walk", "type": "SurvivorM_Mirek"},
                "unexpected arguments: type",
            ),
            ({"object_id": 7, "heading": 90.0, "speed": 1}, "speed"),
            ({"object_id": 7, "heading": "90", "speed": "walk"}, "heading"),
            ({"object_id": 7, "heading": True, "speed": "walk"}, "heading"),
            ({"object_id": True, "mode": "release"}, "object_id"),
            ({"object_id": "7", "mode": "release"}, "object_id"),
            ({"heading": 90.0, "speed": "walk"}, "object_id"),
        ):
            with self.subTest(arguments=arguments):
                self.assertIn(part, await self._refused(arguments))

    async def test_the_answer_is_the_bridge_reply(self) -> None:
        with patch.object(
            self.runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "pos_real": [1.0, 2.0, 3.0]}),
        ):
            result = await self.app.call_tool(COMMAND, {"object_id": 7, "mode": "release"})
        self.assertIn("'pos_real': [1.0, 2.0, 3.0]", str(result))


class SurvivorDriveAdmissionTest(unittest.TestCase):
    def _refused(self, state: loopback.ServerState, args: dict | None = None, **kwargs: object):
        before_id = state._next_id
        status, body = state.enqueue_command(
            COMMAND, dict(DRIVE_WIRE) if args is None else args, peer="server", **kwargs
        )
        self.assertNotEqual(status, 200)
        self.assertEqual(state._next_id, before_id)
        self.assertEqual(bound_queue(state, "server"), [])
        return status, body

    def test_lists_name_the_server_verb(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertIn(COMMAND, loopback._CAPABILITY_ADMISSION_COMMANDS)
        self.assertNotIn(COMMAND, loopback._CLIENT_ANNOUNCED_COMMANDS)
        self.assertEqual(server._BRIDGE_COMMAND_TOOLS["server"][COMMAND], COMMAND)
        self.assertNotIn(COMMAND, server._BRIDGE_COMMAND_TOOLS["client"])

    def test_the_arg_contract_is_the_ingress_schema(self) -> None:
        contract = server.SERVER_ARG_CONTRACT[COMMAND]
        self.assertEqual(contract, ("heading", "heading_set", "mode", "object_id", "speed", "speed_set"))
        self.assertEqual(list(contract), sorted(contract))
        self.assertEqual(set(contract), _schema_keys(COMMAND))
        self.assertNotEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, PREVIOUS_ARG_CONTRACT_HASH)

    def test_an_older_or_absent_census_queues_nothing(self) -> None:
        full = _full_commands()
        without = [name for name in full if name != COMMAND]
        for name, commands, ach, generation, error in (
            ("previous_pbo", without, PREVIOUS_ARG_CONTRACT_HASH, None, "arg_contract_mismatch"),
            ("missing_verb", without, EXPECTED_SERVER_ARG_CONTRACT_HASH, None, "bridge_capability_missing"),
            ("absent_hash", full, None, None, "arg_contract_mismatch"),
            ("wrong_hash", full, "0" * 16, None, "arg_contract_mismatch"),
            ("stale", full, EXPECTED_SERVER_ARG_CONTRACT_HASH, "gen-old", "bridge_capability_missing"),
            ("no_census", None, None, None, "bridge_capability_missing"),
        ):
            for wire in (DRIVE_WIRE, RELEASE_WIRE):
                with self.subTest(name=name, wire=wire):
                    state = _bound_state()
                    if commands is not None:
                        _announce(state, commands, ach, generation)
                    _status, body = self._refused(state, dict(wire))
                    self.assertEqual(body["error"], error)

    def test_a_lease_is_required(self) -> None:
        coordinator = SessionCoordinator(
            token_fn=lambda: "drive-token",
            id_fn=lambda: "drive-lease",
            audit=lambda _event: True,
            cleanup=lambda *_args: None,
        )
        state = _bound_state(coordination=coordinator)
        state.retail_probe = lambda: {"known": True, "processes": []}
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        identity = {
            "platform": "codex",
            "pid": 101,
            "ppid": 10,
            "started_at_utc": "2026-07-14T20:00:00Z",
            "session_id": "drive-owner",
            "task_label": "survivor drive",
        }
        status, _body = self._refused(state, identity_payload=identity)
        self.assertEqual(status, 423)
        acquired, active = coordinator.acquire(ClientIdentity.from_payload(identity), "drive")
        self.assertEqual(acquired, 200, active)
        status, body = state.enqueue_command(
            COMMAND,
            dict(DRIVE_WIRE),
            peer="server",
            identity_payload=identity,
            lease_token=active["lease_token"],
        )
        self.assertEqual(status, 200, body)

    def test_the_current_census_admits_and_delivers_both_shapes(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, drive = state.enqueue_command(COMMAND, dict(DRIVE_WIRE), peer="server")
        self.assertEqual(status, 200, drive)
        status, release = state.enqueue_command(COMMAND, dict(RELEASE_WIRE), peer="server")
        self.assertEqual(status, 200, release)
        status, bad = state.enqueue_command(
            COMMAND, {"object_id": 7, "mode": "release", "heading": 1.0, "heading_set": True}, peer="server"
        )
        self.assertEqual((status, bad), (400, {"error": "bad_args"}))
        status, polled = _poll(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual(
            polled["commands"],
            [
                {"id": drive["id"], "cmd": COMMAND, "args": DRIVE_WIRE},
                {"id": release["id"], "cmd": COMMAND, "args": RELEASE_WIRE},
            ],
        )

    def test_delivery_drops_it_when_the_bound_peer_no_longer_advertises_it(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command(COMMAND, dict(DRIVE_WIRE), peer="server")
        self.assertEqual(status, 200, body)
        reduced = [name for name in _full_commands() if name != COMMAND]
        status, polled = _poll(state, reduced, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual(polled["commands"], [])
        self.assertEqual(state.take_result(body["id"])["error"], "bridge_capability_missing")
        self.assertEqual(bound_queue(state, "server"), [])


class SurvivorDriveEnforceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = _strip_comments(BRIDGE.read_text(encoding="utf-8"))
        cls.drive = _strip_comments(DRIVE.read_text(encoding="utf-8"))

    def test_heading_bound_matches_the_ingress(self) -> None:
        match = re.search(
            r"protected const float SURVIVOR_DRIVE_HEADING_ABS_MAX_DEG = ([0-9.]+);", self.bridge
        )
        self.assertIsNotNone(match)
        self.assertEqual(float(match.group(1)), loopback.SURVIVOR_DRIVE_HEADING_ABS_MAX_DEG)
        self.assertEqual(loopback.SURVIVOR_DRIVE_HEADING_ABS_MAX_DEG, 360.0)

    def test_dispatch_refuses_everything_before_it_drives(self) -> None:
        dispatch = _method_body(self.bridge, "protected void Dispatch(MCPCommand command)")
        self.assertIn(
            'else if (command.cmd == "survivor_drive")\n\t\t{\n\t\t\tpostNow = DispatchSurvivorDrive(command, result);',
            dispatch,
        )
        body = _method_body(
            self.bridge, "protected bool DispatchSurvivorDrive(MCPCommand command, MCPResult result)"
        )
        _in_order(
            self,
            body,
            "if (!SurvivorDriveArgsOk(command))",
            'result.error = "bad_args";',
            "if (!m_RuntimeObjects || !m_RuntimeObjects.Contains(objectId))",
            'result.error = "object_id_unknown";',
            "registered = m_RuntimeObjects.Get(objectId);",
            'result.error = "object_id_stale";',
            "survivor = PlayerBase.Cast(registered);",
            'result.error = "bad_target";',
            "if (survivor.GetIdentity())",
            'result.error = "refused_player_controlled";',
            "if (!survivor.IsAlive())",
            'result.error = "player_dead";',
            "if (MCPBotControl.IsRunning(objectId))",
            'result.error = "bot_busy";',
            "if (!survivor.GetInputController())",
            'result.error = "no_input_controller";',
            'if (command.args.mode == "release")',
            "MCPSurvivorDrive.Release(this, objectId);",
            "MCPSurvivorDrive.Drive(survivor, objectId, command.args.heading, command.args.speed);",
            "current = survivor.GetPosition();",
            "result.pos_real.Insert(current[2]);",
            "result.ok = true;",
        )
        self.assertEqual(body.count("MCPSurvivorDrive."), 2)
        self.assertEqual(body.count("result.ok = false;"), 8)
        self.assertNotIn("GetGame().GetPlayer", body)

    def test_args_check_is_the_ingress_rule_again(self) -> None:
        body = _method_body(self.bridge, "protected bool SurvivorDriveArgsOk(MCPCommand command)")
        _in_order(
            self,
            body,
            "if (!command.args || command.args.object_id <= 0)",
            'if (command.args.mode == "release")',
            "if (command.args.heading_set || command.args.speed_set)",
            'if (command.args.mode != "")',
            "if (!command.args.heading_set || !command.args.speed_set)",
            "if (!IsFiniteFloat(heading))",
            "if (heading < -SURVIVOR_DRIVE_HEADING_ABS_MAX_DEG || heading > SURVIVOR_DRIVE_HEADING_ABS_MAX_DEG)",
            "return MCPSurvivorDrive.IsDriveSpeed(command.args.speed);",
        )
        speed = _method_body(self.drive, "static bool IsDriveSpeed(float speed)")
        self.assertIn('return MCPPlayerMoveControl.SpeedName(speed) != "";', speed)
        # The server bridge names no player_move code (test_player_move's census).
        self.assertNotIn("MCPPlayerMove", BRIDGE.read_text(encoding="utf-8"))

    def test_the_drive_steers_through_the_player_move_helpers(self) -> None:
        apply = _method_body(
            self.drive, "protected static void Apply(PlayerBase survivor, MCPSurvivorDriveEntry entry)"
        )
        _in_order(
            self,
            apply,
            "hic = survivor.GetInputController();",
            "compass = MCPPlayerMoveControl.CompassFromHeading(hic.GetHeadingAngle());",
            "angle = MCPPlayerMoveControl.WrapDeg180(entry.heading_deg - compass);",
            "MCPPlayerMoveControl.Enable(hic, entry.speed, angle);",
        )
        disable = _method_body(self.drive, "protected static void Disable(PlayerBase survivor)")
        self.assertIn("MCPPlayerMoveControl.Disable(hic);", disable)
        for absent in ("OverrideMovementSpeed(", "OverrideMovementAngle(", "MCPResult", "GetGame().GetPlayer"):
            self.assertNotIn(absent, self.drive)

    def test_release_disables_only_a_driven_id(self) -> None:
        release = _method_body(self.drive, "static void Release(MCPBridge bridge, int objectId)")
        _in_order(
            self,
            release,
            "index = Find(objectId);",
            "if (index < 0)\n\t\t{\n\t\t\treturn;\n\t\t}",
            "s_Driven.Remove(index);",
            "Disable(PlayerBase.Cast(target));",
        )
        gone = _method_body(self.drive, "static void OnObjectGone(MCPBridge bridge, int objectId)")
        self.assertIn("Release(bridge, objectId);", gone)

    def test_the_tick_reapplies_and_drops_the_dead_walking_backwards(self) -> None:
        tick = _method_body(self.drive, "static void Tick(MCPBridge bridge)")
        _in_order(
            self,
            tick,
            "index = s_Driven.Count() - 1;",
            "while (index >= 0)",
            "target = bridge.RuntimeObjectById(entry.object_id);",
            "if (!entry || !survivor || !survivor.IsAlive())",
            "s_Driven.Remove(index);",
            "Disable(survivor);",
            "Apply(survivor, entry);",
            "index = index - 1;",
        )
        shutdown = _method_body(self.drive, "static void ShutdownAll(MCPBridge bridge)")
        _in_order(self, shutdown, "Disable(PlayerBase.Cast(target));", "s_Driven.Clear();")

    def test_the_bridge_ticks_deletes_and_shuts_the_drive_down(self) -> None:
        on_tick = _method_body(self.bridge, "void OnTick(float timeslice)")
        _in_order(self, on_tick, "MCPBotControl.Tick(this, m_ElapsedS);", "MCPSurvivorDrive.Tick(this);")
        delete = _method_body(
            self.bridge, "protected bool DispatchObjectDelete(MCPCommand command, MCPResult result)"
        )
        _in_order(
            self,
            delete,
            "MCPSurvivorDrive.OnObjectGone(this, objectId);",
            "m_RuntimeObjects.Remove(objectId);",
            "GetGame().ObjectDelete(target);",
        )
        shutdown = _method_body(self.bridge, "void Shutdown()")
        self.assertIn("MCPSurvivorDrive.ShutdownAll(this);", shutdown)


if __name__ == "__main__":
    unittest.main()
