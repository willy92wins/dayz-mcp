"""survivor_drive (inbox c801): wire, admission and the server bridge contract."""

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
from tests.vehicle_eject_helpers import EjectGuardFake
from tests.bridge_client_capabilities_helpers import _method_body
from tests.enforce_subset_helpers import (
    EnforceString,
    EnforceVector,
    declared_fields,
    method_body,
    translate,
)
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue
from tests.lease_helpers import SequentialIds


COMMAND = "survivor_drive"
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
DRIVE = addon_root() / "scripts" / "5_Mission" / "MCP_SurvivorDrive.c"
MOVE = addon_root() / "scripts" / "4_World" / "MCP_PlayerMove.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
# The arg-contract hash of main before this verb: the PBO a user may still run.
PREVIOUS_ARG_CONTRACT_HASH = "4f90284258023d98"
DRIVE_WIRE = {"object_id": 7, "heading": 90.0, "heading_set": True, "speed": 1.0, "speed_set": True}
RELEASE_WIRE = {"object_id": 7, "mode": "release"}
# The broker's release of one drive: internal, sent by cleanup_owner and
# RELEASE_REGISTRY with the id of the drive command and its survivor's
# object_id.
DRIVE_RELEASE = "survivor_drive_release"
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


# Two owners in turn, as in Codex's lease probe: A drives and releases, then B
# acquires.
OWNER_A = {
    "platform": "codex",
    "pid": 201,
    "ppid": 10,
    "started_at_utc": "2026-07-14T20:00:00Z",
    "session_id": "drive-owner-a",
    "task_label": "survivor drive",
}
OWNER_B = {**OWNER_A, "pid": 202, "session_id": "drive-owner-b", "task_label": "next owner"}


def _leased_state() -> tuple[loopback.ServerState, SessionCoordinator]:
    """The bound server peer with the current census, and a real coordinator
    whose release runs this broker's cleanup_owner, as the daemon wires it."""
    state = _bound_state()
    coordinator = SessionCoordinator(
        token_fn=SequentialIds("drive-token"),
        id_fn=SequentialIds("drive-lease"),
        audit=lambda _event: True,
        cleanup=state.cleanup_owner,
    )
    state.coordination = coordinator
    state.retail_probe = lambda: {"known": True, "processes": []}
    _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
    return state, coordinator


def _acquire(coordinator: SessionCoordinator, owner: dict) -> str:
    status, active = coordinator.acquire(ClientIdentity.from_payload(owner), owner["task_label"])
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


def _delivered(state: loopback.ServerState, commands: list[str] | None = None) -> list[dict]:
    """What the next server poll hands the bridge (the full census unless
    `commands` is given)."""
    census = _full_commands() if commands is None else commands
    status, polled = _poll(state, census, EXPECTED_SERVER_ARG_CONTRACT_HASH)
    if status != 200:
        raise AssertionError(polled)
    return list(polled["commands"])


def _answered(state: loopback.ServerState, command_id: int, answer: dict | None = None) -> dict | None:
    """The bridge's answer to a delivered command, stored and then taken as
    the tool takes it."""
    body = {"ok": 1, "pos_real": [1.0, 2.0, 3.0]} if answer is None else dict(answer)
    state.store_result({"id": command_id, **body}, instance=INST_SERVER)
    return state.take_result(command_id, remove=True)


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

    def test_both_descriptions_say_a_driven_survivor_refuses_bot_start(self) -> None:
        self.assertIn(
            "While driven, the survivor refuses bot_start the same way (drive_busy) until mode='release'.",
            self.tool.description,
        )
        bot_start = self.app._tool_manager.get_tool("bot_start")
        self.assertIsNotNone(bot_start)
        for part in (
            "A survivor that survivor_drive walks is drive_busy and nothing changes: release "
            "the drive first with survivor_drive(mode='release').",
            "Errors: bad_args, bot_unavailable, drive_busy, object_not_found,",
        ):
            self.assertIn(part, bot_start.description)

    def test_the_drive_release_is_no_tool_and_the_description_says_the_lease_end_stops_a_drive(self) -> None:
        self.assertIsNone(self.app._tool_manager.get_tool(DRIVE_RELEASE))
        for part in (
            "The end of the lease that sent the drive ends it too: the broker queues its release "
            "(survivor_drive_release), which stops the drive only while that call's drive is the one set.",
            "A call that times out or is cancelled after the server received it still runs, and its "
            "release is queued only if its result is not stored yet and the queue admits the release: "
            "once the result is stored, nothing is queued and the drive keeps going until a "
            "mode='release' or the end of the lease.",
        ):
            self.assertIn(part, self.tool.description)
        self.assertNotIn("still runs, and its release is queued.", self.tool.description)

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

    def test_the_drive_release_is_an_internal_server_verb_with_its_own_contract(self) -> None:
        self.assertIn(DRIVE_RELEASE, loopback.SERVER_COMMANDS)
        self.assertNotIn(DRIVE_RELEASE, loopback.CLIENT_COMMANDS)
        self.assertIn(DRIVE_RELEASE, loopback._CAPABILITY_ADMISSION_COMMANDS)
        self.assertIn(DRIVE_RELEASE, loopback._OWNER_CLEANUP_COMMANDS)
        self.assertIn(DRIVE_RELEASE, server._BRIDGE_COMMAND_TOOLS["server"])
        self.assertIsNone(server._BRIDGE_COMMAND_TOOLS["server"][DRIVE_RELEASE])
        self.assertEqual(server.SERVER_ARG_CONTRACT[DRIVE_RELEASE], ("command_id", "object_id"))
        self.assertEqual(_schema_keys(DRIVE_RELEASE), {"command_id", "object_id"})
        plans = [entry for entry in loopback.RELEASE_REGISTRY if entry[0] == COMMAND]
        self.assertEqual([plan[1] for plan in plans], [DRIVE_RELEASE])
        # The release names the drive's id and the survivor of the drive's own
        # args.
        self.assertEqual(plans[0][2](41, dict(DRIVE_WIRE)), {"command_id": 41, "object_id": 7})
        self.assertEqual(
            plans[0][2](41, {**DRIVE_WIRE, "object_id": 9}), {"command_id": 41, "object_id": 9}
        )
        self.assertEqual(
            loopback.validate_command_args(DRIVE_RELEASE, {"command_id": 41, "object_id": 7}), (True, None)
        )
        for args in (
            {},
            {"command_id": 41},
            {"object_id": 7},
            {"command_id": 0, "object_id": 7},
            {"command_id": True, "object_id": 7},
            {"command_id": "41", "object_id": 7},
            {"command_id": 41, "object_id": 0},
            {"command_id": 41, "object_id": True},
            {"command_id": 41, "object_id": "7"},
            {"command_id": 41, "object_id": 7, "mode": "release"},
        ):
            with self.subTest(args=args):
                self.assertIs(loopback.validate_command_args(DRIVE_RELEASE, args)[0], False)

    def test_an_older_pbo_is_never_handed_the_drive_release(self) -> None:
        full = _full_commands()
        without_release = [name for name in full if name != DRIVE_RELEASE]
        previous = [name for name in full if name not in (COMMAND, DRIVE_RELEASE)]
        for name, commands, ach, error in (
            ("previous_pbo", previous, PREVIOUS_ARG_CONTRACT_HASH, "arg_contract_mismatch"),
            ("missing_release", without_release, EXPECTED_SERVER_ARG_CONTRACT_HASH, "bridge_capability_missing"),
            ("no_census", None, None, "bridge_capability_missing"),
        ):
            with self.subTest(name=name):
                state = _bound_state()
                if commands is not None:
                    _announce(state, commands, ach)
                before_id = state._next_id
                status, body = state.enqueue_command(
                    DRIVE_RELEASE, {"command_id": 5, "object_id": 7}, peer="server", internal=True
                )
                self.assertEqual((status, body), (409, {"error": error}))
                self.assertEqual(state._next_id, before_id)
                self.assertEqual(bound_queue(state, "server"), [])
        # Admitted under the current census, then dropped at delivery to a
        # peer that no longer advertises it.
        state = _bound_state()
        _announce(state, full, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command(
            DRIVE_RELEASE, {"command_id": 5, "object_id": 7}, peer="server", internal=True
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(_delivered(state, without_release), [])
        self.assertEqual(state.take_result(body["id"])["error"], "bridge_capability_missing")
        self.assertEqual(bound_queue(state, "server"), [])

    def test_abandoning_a_delivered_drive_queues_its_release(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, drive = state.enqueue_command(COMMAND, dict(DRIVE_WIRE), peer="server")
        self.assertEqual(status, 200, drive)
        self.assertEqual([command["id"] for command in _delivered(state)], [drive["id"]])
        abandoned = state.abandon_command(drive["id"], "tool_timeout")
        self.assertEqual(abandoned.get("status"), "release_queued", abandoned)
        self.assertEqual(
            _delivered(state),
            [
                {
                    "id": abandoned["release_id"],
                    "cmd": DRIVE_RELEASE,
                    "args": {"command_id": drive["id"], "object_id": 7},
                }
            ],
        )
        # Abandoned before delivery, it never runs: dropped, with no release.
        status, queued = state.enqueue_command(COMMAND, dict(DRIVE_WIRE), peer="server")
        self.assertEqual(status, 200, queued)
        self.assertEqual(state.abandon_command(queued["id"], "cancelled"), {"status": "dropped"})
        self.assertEqual(_delivered(state), [])

    def test_a_drive_abandoned_after_its_result_is_stored_goes_on_until_the_lease_ends(self) -> None:
        # The result is stored before the caller takes it: the abandon of a
        # timeout or a cancel, in process or over HTTP, finds nothing running
        # and queues nothing; the end of the lease still releases the drive.
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        drive = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([command["id"] for command in _delivered(state)], [drive])
        state.store_result({"id": drive, "ok": 1, "pos_real": [1.0, 2.0, 3.0]}, instance=INST_SERVER)
        self.assertEqual(state.abandon_command(drive, "tool_timeout"), {"status": "noop"})
        self.assertEqual(
            state.request_abandon(
                identity_payload=OWNER_A,
                generation=state.daemon_generation,
                command_id=drive,
                reason="cancelled",
                lease_token=token,
                lease_token_supplied=True,
            ),
            (200, {"status": "noop"}),
        )
        self.assertEqual(_delivered(state), [])
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"]["survivor_drive_release_enqueued"], 1)
        self.assertEqual(
            [(command["cmd"], command["args"]) for command in _delivered(state)],
            [(DRIVE_RELEASE, {"command_id": drive, "object_id": 7})],
        )

    def test_a_running_drive_abandoned_under_quarantine_queues_no_release(self) -> None:
        # Still running, but the queue does not admit the release: nothing is
        # queued, and the abandon says so.
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, drive = state.enqueue_command(COMMAND, dict(DRIVE_WIRE), peer="server")
        self.assertEqual(status, 200, drive)
        self.assertEqual([command["id"] for command in _delivered(state)], [drive["id"]])
        state.retail_probe = lambda: {"known": False}
        self.assertEqual(
            state.abandon_command(drive["id"], "cancelled"), {"status": "cleanup_degraded", "cause": "quarantine"}
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_the_end_of_a_lease_queues_the_release_of_its_latest_drive_of_each_survivor(self) -> None:
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        first = _send(state, OWNER_A, token, DRIVE_WIRE)
        other = _send(state, OWNER_A, token, {**DRIVE_WIRE, "object_id": 9})
        latest = _send(state, OWNER_A, token, {**DRIVE_WIRE, "heading": 180.0, "speed": 3.0})
        # A mode release names no drive: the broker keeps 9's drive id.
        stop = _send(state, OWNER_A, token, {"object_id": 9, "mode": "release"})
        self.assertEqual([command["id"] for command in _delivered(state)], [first, other, latest, stop])
        for command_id in (first, other, latest, stop):
            self.assertEqual(_answered(state, command_id)["ok"], 1)
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"]["survivor_drive_release_enqueued"], 2)
        self.assertEqual(
            [value for value in released["cleanup_degraded"] if value.startswith("survivor_drive")], []
        )
        self.assertEqual(
            [(command["cmd"], command["args"]) for command in _delivered(state)],
            [
                (DRIVE_RELEASE, {"command_id": latest, "object_id": 7}),
                (DRIVE_RELEASE, {"command_id": other, "object_id": 9}),
            ],
        )
        self.assertEqual(state._drive_by_lease, {})

    def test_a_clean_lease_end_reports_the_release_count_and_no_drive_degradation(self) -> None:
        # As bot_stop and camera_path_release: a release that went out is
        # counted and degrades nothing, so a control client sees no conflict;
        # survivor_drive_release_unconfirmed is for one that was not queued.
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        drive = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([command["id"] for command in _delivered(state)], [drive])
        _answered(state, drive)
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"]["survivor_drive_release_enqueued"], 1)
        self.assertEqual(released["cleanup"].get("cleanup_degraded", []), [])
        self.assertEqual(released["cleanup_degraded"], [])
        self.assertEqual(
            [(command["cmd"], command["args"]) for command in _delivered(state)],
            [(DRIVE_RELEASE, {"command_id": drive, "object_id": 7})],
        )

    def test_a_drive_the_queue_drops_at_the_lease_end_leaves_the_delivered_one_named(self) -> None:
        # The id is taken at delivery: the queued drive is discarded by the
        # release and never runs, so the release names the one before it.
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        delivered = _send(state, OWNER_A, token, DRIVE_WIRE)
        self.assertEqual([command["id"] for command in _delivered(state)], [delivered])
        _answered(state, delivered)
        _send(state, OWNER_A, token, {**DRIVE_WIRE, "heading": 180.0})
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"]["cancelled"], 1)
        self.assertEqual(released["cleanup"]["survivor_drive_release_enqueued"], 1)
        self.assertEqual(
            [(command["cmd"], command["args"]) for command in _delivered(state)],
            [(DRIVE_RELEASE, {"command_id": delivered, "object_id": 7})],
        )

    def test_a_release_the_bridge_is_not_handed_is_reported(self) -> None:
        for name in ("missing_release", "retail_quarantine"):
            with self.subTest(name=name):
                state, coordinator = _leased_state()
                token = _acquire(coordinator, OWNER_A)
                drive = _send(state, OWNER_A, token, DRIVE_WIRE)
                self.assertEqual([command["id"] for command in _delivered(state)], [drive])
                _answered(state, drive)
                if name == "missing_release":
                    _announce(
                        state,
                        [command for command in _full_commands() if command != DRIVE_RELEASE],
                        EXPECTED_SERVER_ARG_CONTRACT_HASH,
                    )
                else:
                    state.retail_probe = lambda: {"known": False}
                released = _release(coordinator, OWNER_A, token)
                self.assertEqual(bound_queue(state, "server"), [])
                if name == "missing_release":
                    # Not queued: unconfirmed, as camera_path_release says it.
                    self.assertEqual(released["cleanup"]["survivor_drive_release_enqueued"], 0)
                    self.assertIn("survivor_drive_release_unconfirmed", released["cleanup_degraded"])
                    self.assertNotIn("survivor_drive_release_failed", released["cleanup_degraded"])
                else:
                    self.assertNotIn("survivor_drive_release_enqueued", released["cleanup"])
                    self.assertIn("retail_quarantine", released["cleanup_degraded"])
                    self.assertIn("survivor_drive_release_unconfirmed", released["cleanup_degraded"])

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
            "MCPSurvivorDrive.Drive(survivor, objectId, command.args.heading, command.args.speed, command.id);",
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

    def test_the_drive_release_has_its_branch_and_drops_only_the_drive_its_id_set(self) -> None:
        dispatch = _method_body(self.bridge, "protected void Dispatch(MCPCommand command)")
        self.assertIn(
            'else if (command.cmd == "survivor_drive_release")\n\t\t{\n\t\t\t'
            "postNow = DispatchSurvivorDriveRelease(command, result);",
            dispatch,
        )
        body = _method_body(
            self.bridge, "protected bool DispatchSurvivorDriveRelease(MCPCommand command, MCPResult result)"
        )
        _in_order(
            self,
            body,
            "if (!command.args || command.args.command_id <= 0 || command.args.object_id <= 0)",
            'result.error = "bad_args";',
            "result.matched = MCPSurvivorDrive.ReleaseCommand(this, command.args.object_id, command.args.command_id);",
            "result.ok = true;",
        )
        self.assertEqual(body.count("MCPSurvivorDrive."), 1)
        release = _method_body(
            self.drive, "static bool ReleaseCommand(MCPBridge bridge, int objectId, int commandId)"
        )
        _in_order(
            self,
            release,
            "index = Find(objectId);",
            "if (!entry || entry.command_id != commandId)",
            "Release(bridge, objectId);",
            "return true;",
        )
        self.assertEqual(release.count("return false;"), 2)
        self.assertTrue(release.rstrip().endswith("return true;"), release)

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
        # scene_clear drops the drive of each entry it removes, the same way.
        clear = _method_body(
            self.bridge, "protected void DeleteSceneObjects(MCPJob job)"
        )
        _in_order(
            self,
            clear,
            "MCPSurvivorDrive.OnObjectGone(this, entry.object_id);",
            "m_RuntimeObjects.Remove(entry.object_id);",
            "GetGame().ObjectDelete(job.delete_targets.Get(index));",
        )
        shutdown = _method_body(self.bridge, "void Shutdown()")
        self.assertIn("MCPSurvivorDrive.ShutdownAll(this);", shutdown)

    def test_bot_start_refuses_a_driven_survivor_before_it_starts(self) -> None:
        body = _method_body(self.bridge, "protected bool DispatchBotStart(MCPCommand command, MCPResult result)")
        guard = "if (MCPSurvivorDrive.IsDriven(command.args.object_id))"
        _in_order(
            self,
            body,
            "if (!BotStartArgsOk(command))",
            'result.error = "bad_args";',
            "if (!MCPBotControl.Available())",
            'result.error = "bot_unavailable";',
            guard,
            'result.error = "drive_busy";',
            "botStarted = false;",
            "botError = MCPBotControl.Start(",
        )
        self.assertEqual(body.count(guard), 1)
        self.assertEqual(body.count("MCPSurvivorDrive."), 1)
        block = body[body.index(guard) :]
        block = block[: block.index("}") + 1]
        _in_order(self, block, "result.ok = false;", 'result.error = "drive_busy";', "return true;")
        driven = _method_body(self.drive, "static bool IsDriven(int objectId)")
        self.assertEqual(driven.strip(), "return Find(objectId) >= 0;")


DRIVE_METHODS = (
    "static bool IsDriveSpeed(float speed)",
    "static bool IsDriven(int objectId)",
    "static void Drive(PlayerBase survivor, int objectId, float headingDeg, float speed, int commandId)",
    "static void Release(MCPBridge bridge, int objectId)",
    "static bool ReleaseCommand(MCPBridge bridge, int objectId, int commandId)",
    "static void OnObjectGone(MCPBridge bridge, int objectId)",
    "static void Tick(MCPBridge bridge)",
    "static void ShutdownAll(MCPBridge bridge)",
    "protected static void Apply(PlayerBase survivor, MCPSurvivorDriveEntry entry)",
    "protected static void Disable(PlayerBase survivor)",
    "protected static int Find(int objectId)",
)
MOVE_HELPERS = (
    "static string SpeedName(float speed)",
    "static float CompassFromHeading(float headingRad)",
    "static float WrapDeg180(float angle)",
    "static void Enable(HumanInputController hic, float speed, float angleDeg)",
    "static void Disable(HumanInputController hic)",
)
# The translator has no statics and no `new`: the registry becomes G.s_Driven
# and the two constructors factory calls; nothing else changes.
_DRIVE_REWRITES = (
    (re.compile(r"(?<![\w.])s_Driven\b"), "G.s_Driven"),
    (re.compile(r"new array<ref MCPSurvivorDriveEntry>\(\)"), "NewArray()"),
    (re.compile(r"new MCPSurvivorDriveEntry\(\)"), "NewEntry()"),
)
_OVERRIDE = SimpleNamespace(DISABLED="DISABLED", ENABLED="ENABLED")


def _method_name(signature: str) -> str:
    return signature[: signature.index("(")].split()[-1]


class _Controller:
    """A survivor's server HumanInputController: GetHeadingAngle in radians
    and the two movement overrides, each the last (type, value) it was given,
    with every call in `calls`."""

    def __init__(self) -> None:
        self.heading_rad = 0.0
        self.speed: tuple[str, float] = (_OVERRIDE.DISABLED, 0.0)
        self.angle: tuple[str, float] = (_OVERRIDE.DISABLED, 0.0)
        self.calls: list[tuple[str, str, float]] = []

    def face(self, compass_deg: float) -> None:
        # CompassFromHeading is -heading * RAD2DEG.
        self.heading_rad = -math.radians(compass_deg)

    def GetHeadingAngle(self) -> float:
        return self.heading_rad

    def OverrideMovementSpeed(self, kind: str, value: float) -> None:
        self.calls.append(("speed", kind, value))
        self.speed = (kind, value)

    def OverrideMovementAngle(self, kind: str, value: float) -> None:
        self.calls.append(("angle", kind, value))
        self.angle = (kind, value)

    def driving(self) -> tuple[float, float] | None:
        """(speed, angle) while both overrides are ENABLED, None while both
        are DISABLED; one of each fails."""
        kinds = (self.speed[0], self.angle[0])
        if kinds == (_OVERRIDE.ENABLED, _OVERRIDE.ENABLED):
            return (self.speed[1], round(self.angle[1], 9))
        if kinds == (_OVERRIDE.DISABLED, _OVERRIDE.DISABLED):
            return None
        raise AssertionError(f"the overrides disagree: {self.speed} {self.angle}")


class _Vector:
    """A vector as GetPosition returns it: a copy of the survivor's position.
    The translator has no []: _HANDLER_REWRITES reads a component with
    Coordinate and writes one with SetCoordinate."""

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
    """A vector local as the translator copies it on assignment (165a): the
    copy of a _Vector, with the Coordinate and SetCoordinate that
    _HANDLER_REWRITES writes for its v[i]."""

    def __init__(self, value: object = (0.0, 0.0, 0.0)) -> None:
        super().__init__(value.xyz if isinstance(value, _Vector) else value)  # type: ignore[arg-type]

    def Coordinate(self, index: int) -> float:
        return self[index]

    def SetCoordinate(self, index: int, value: float) -> None:
        self[index] = value


class _Survivor:
    """A world_spawn survivor as the drive and its handler read it, facing
    north: no PlayerIdentity unless the test gives it one, alive, with its
    input controller, at `position`."""

    def __init__(self, position: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        self.controller = _Controller()
        self.alive = True
        self.identity: object | None = None
        self.has_controller = True
        self.position = position

    def GetInputController(self) -> _Controller | None:
        return self.controller if self.has_controller else None

    def IsAlive(self) -> bool:
        return self.alive

    def GetIdentity(self) -> object | None:
        return self.identity

    def GetPosition(self) -> _Vector:
        return _Vector(self.position)


class _Thing:
    """A registered object that is not a PlayerBase: it has no controller to
    write to."""


class _Array:
    """Enforce's array as the registry uses it: Remove(index) puts the last
    element in the hole (the order is not kept, as Tick's comment says), and
    an index outside the array fails."""

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
            raise AssertionError(f"index {index!r} outside an array of {len(self.items)}")


class _DriveModel:
    """MCPSurvivorDrive's methods (MCP_SurvivorDrive.c) and the
    MCPPlayerMoveControl helpers they call (MCP_PlayerMove.c), translated and
    run on one registry (s_Driven, here G.s_Driven) and a bridge whose
    RuntimeObjectById resolves the survivors the test spawns."""

    def __init__(self) -> None:
        drive = DRIVE.read_text(encoding="utf-8")
        move = MOVE.read_text(encoding="utf-8")
        self.G = SimpleNamespace(s_Driven=None)
        self.objects: dict[int, object] = {}
        self._next_command_id = 100
        self.bridge = SimpleNamespace(RuntimeObjectById=self.objects.get)
        helpers: dict[str, object] = {
            "Math": SimpleNamespace(RAD2DEG=180.0 / math.pi),
            "HumanInputControllerOverrideType": _OVERRIDE,
        }
        for signature in MOVE_HELPERS:
            translate(move, signature, helpers)
        entry = declared_fields(drive, "MCPSurvivorDriveEntry")
        self.namespace: dict[str, object] = {
            "G": self.G,
            "NewArray": _Array,
            "NewEntry": lambda: SimpleNamespace(**entry),
            "PlayerBase": SimpleNamespace(Cast=lambda item: item if isinstance(item, _Survivor) else None),
            "MCPPlayerMoveControl": SimpleNamespace(
                **{_method_name(signature): helpers[_method_name(signature)] for signature in MOVE_HELPERS}
            ),
        }
        for signature in DRIVE_METHODS:
            body = method_body(drive, signature)
            for pattern, replacement in _DRIVE_REWRITES:
                body = pattern.sub(replacement, body)
            translate(
                signature + "\n{" + body + "}\n",
                signature,
                self.namespace,
                class_types=("MCPSurvivorDriveEntry", "Object", "PlayerBase", "HumanInputController"),
            )

    def spawn(self, object_id: int, position: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> _Survivor:
        survivor = _Survivor(position)
        self.objects[object_id] = survivor
        return survivor

    def call(self, name: str, *args: object) -> object:
        return self.namespace[name](*args)

    def drive(self, object_id: int, heading: float, speed: float, command_id: int | None = None) -> None:
        """Drive as survivor_drive's command does; without command_id, each
        call is a new command id."""
        if command_id is None:
            self._next_command_id += 1
            command_id = self._next_command_id
        self.call("Drive", self.objects[object_id], object_id, heading, speed, command_id)

    def registry(self) -> list[tuple[int, float, float]]:
        if self.G.s_Driven is None:
            return []
        return [(entry.object_id, entry.heading_deg, entry.speed) for entry in self.G.s_Driven.items]

    def command_ids(self) -> dict[int, int]:
        """object_id -> the command id each driven entry keeps."""
        if self.G.s_Driven is None:
            return {}
        return {entry.object_id: entry.command_id for entry in self.G.s_Driven.items}

    def writes(self) -> int:
        """Every override write so far, on every survivor."""
        return sum(len(item.controller.calls) for item in self.objects.values() if isinstance(item, _Survivor))


# MCPBridge.c's two survivor_drive handlers, the args check and IsFiniteFloat
# they call, and RuntimeObjectById, through which the drive resolves its ids.
HANDLER_METHODS = (
    "protected bool IsFiniteFloat(float value)",
    "protected bool SurvivorDriveArgsOk(MCPCommand command)",
    "protected bool DispatchSurvivorDrive(MCPCommand command, MCPResult result)",
    "protected bool DispatchSurvivorDriveRelease(MCPCommand command, MCPResult result)",
    "Object RuntimeObjectById(int objectId)",
)
# The bridge methods that hold the drive's three hooks: OnTick ticks every
# drive, DispatchObjectDelete drops a deleted survivor's drive and Shutdown
# ends them all.
HOOK_METHODS = (
    "void OnTick(float timeslice)",
    "protected bool DispatchObjectDelete(MCPCommand command, MCPResult result)",
    "void Shutdown()",
)
# No `new`, no [], no bare member and no / either: the array becomes a factory
# call, a vector component a Coordinate read or a SetCoordinate write, a member
# m_X this.m_X (a field of the bridge double) and a / FloatQuotient; nothing
# else changes.
_HANDLER_REWRITES = (
    (re.compile(r"new array<float>\(\)"), "NewFloats()"),
    (re.compile(r"\bcurrent\[([012])\]\s*=\s*([^;]+);"), r"current.SetCoordinate(\1, \2);"),
    (re.compile(r"\bcurrent\[([012])\]"), r"current.Coordinate(\1)"),
    (re.compile(r"(?<![\w.])(m_\w+)"), r"this.\1"),
    (re.compile(r"([\w.]+) / ([\w.]+)"), r"FloatQuotient(\1, \2)"),
)
# Enforce's float.MAX (FLT_MAX), which IsFiniteFloat compares with.
_FLT_MAX = 3.4028234663852886e38
# The MCPArgs keys these handlers read, each with its type's zero: 0, 0.0,
# false or "". An absent JSON key leaves what MCPArgs() set (_arg_defaults).
_ARG_ZEROES: dict[str, object] = {
    "object_id": 0,
    "heading": 0.0,
    "heading_set": False,
    "speed": 0.0,
    "speed_set": False,
    "mode": "",
    "command_id": 0,
}


def _arg_defaults(messages: str) -> dict[str, object]:
    """What a key absent from the JSON leaves in MCPArgs: the value its
    constructor, MCPArgs() in MCPMessages.c, gives the field, else the zero of
    _ARG_ZEROES. It gives heading and speed MCP_ARG_FLOAT_UNSET, float.MAX;
    any other value it gives one of these keys fails instead of being
    guessed."""
    if re.search(r"(?m)^const float MCP_ARG_FLOAT_UNSET = float\.MAX;$", messages) is None:
        raise AssertionError("MCP_ARG_FLOAT_UNSET is not float.MAX")
    values = dict(_ARG_ZEROES)
    for name, value in re.findall(r"(\w+) = ([^;]+);", method_body(messages, "void MCPArgs()")):
        if name in values:
            if value != "MCP_ARG_FLOAT_UNSET":
                raise AssertionError(f"MCPArgs() sets {name} to {value}")
            values[name] = _FLT_MAX
    return values


def _float_quotient(dividend: float, divisor: float) -> float:
    """Enforce's / of two floats. An int division truncates, which is why the
    translator refuses /: an int here fails too."""
    if type(dividend) is not float or type(divisor) is not float:
        raise AssertionError(f"{dividend!r} / {divisor!r} is not a division of two floats")
    return dividend / divisor


class _Registry:
    """The bridge's m_RuntimeObjects: an id the test spawned, mapped to its
    object, or to None once the object is gone (object_id_stale). Remove
    forgets the id, as object_delete does."""

    def __init__(self, objects: dict[int, object]) -> None:
        self.objects = objects

    def Contains(self, object_id: int) -> bool:
        return object_id in self.objects

    def Get(self, object_id: int) -> object:
        if object_id not in self.objects:
            raise AssertionError(f"Get of unregistered id {object_id}")
        return self.objects[object_id]

    def Remove(self, object_id: int) -> None:
        self.objects.pop(object_id, None)


class _Result(SimpleNamespace):
    """A new MCPResult: the fields MCPMessages.c declares, with the values a
    new instance holds. Setting a field the class does not declare fails."""

    def __setattr__(self, name: str, value: object) -> None:
        if name not in self.__dict__:
            raise AssertionError(f"{name} is not a declared field")
        super().__setattr__(name, value)


class _HandlerModel(_DriveModel):
    """_DriveModel with MCPBridge.c's handlers (HANDLER_METHODS) and the
    methods that hold the drive's hooks (HOOK_METHODS) on top, run on the
    drive's own registry. `this`, the bridge the drive resolves survivors
    through, has the real RuntimeObjectById, m_RuntimeObjects holding what the
    test spawns, and the members OnTick and Shutdown read, as TryInit leaves
    them before the first poll. MCPBotControl.IsRunning answers for the ids in
    `bots`, and GetGame().ObjectDelete puts what it deletes in `deleted`."""

    def __init__(self) -> None:
        super().__init__()
        bridge = BRIDGE.read_text(encoding="utf-8")
        messages = MESSAGES.read_text(encoding="utf-8")
        bound = re.search(r"protected const float SURVIVOR_DRIVE_HEADING_ABS_MAX_DEG = ([0-9.]+);", bridge)
        if bound is None:
            raise AssertionError("SURVIVOR_DRIVE_HEADING_ABS_MAX_DEG not found")
        threshold = re.search(r"protected const int PENDING_POLL_THRESHOLD = ([0-9]+);", bridge)
        if threshold is None:
            raise AssertionError("PENDING_POLL_THRESHOLD not found")
        self.result_fields = declared_fields(messages, "MCPResult")
        self.arg_defaults = _arg_defaults(messages)
        self.bots: set[int] = set()
        self.deleted: list[object] = []
        game = SimpleNamespace(ObjectDelete=self.deleted.append)
        self.handlers: dict[str, object] = {
            "MCPVehicleEject": EjectGuardFake,
            "float": SimpleNamespace(MAX=_FLT_MAX),
            "SURVIVOR_DRIVE_HEADING_ABS_MAX_DEG": float(bound.group(1)),
            "PENDING_POLL_THRESHOLD": int(threshold.group(1)),
            "NewFloats": _Array,
            "FloatQuotient": _float_quotient,
            "_vector": _CopiedVector,
            "GetGame": lambda: game,
            "PlayerBase": self.namespace["PlayerBase"],
            # The bot's own hooks are called too; no bot moves here.
            "MCPBotControl": SimpleNamespace(
                IsRunning=lambda object_id: object_id in self.bots,
                Tick=lambda _bridge, _now_s: None,
                OnObjectGone=lambda _bridge, _object_id: None,
                ShutdownAll=lambda _bridge: None,
            ),
            # The rest of OnTick: the bridge's init, jobs, pending commands and poll.
            "TryInit": lambda: None,
            "ProcessJobs": lambda: None,
            "DrainPending": lambda: None,
            "StartPoll": lambda: None,
            "MCPSurvivorDrive": SimpleNamespace(
                **{
                    _method_name(signature): self.namespace[_method_name(signature)]
                    for signature in DRIVE_METHODS
                    if not signature.startswith("protected ")
                }
            ),
        }
        for signature in (*HANDLER_METHODS, *HOOK_METHODS):
            body = method_body(bridge, signature)
            for pattern, replacement in _HANDLER_REWRITES:
                body = pattern.sub(replacement, body)
            translate(
                signature + "\n{" + body + "}\n",
                signature,
                self.handlers,
                class_types=("Object", "PlayerBase", "vector", "MCPResultCallback", "MCPVehicleEject", "MCPJob"),
            )
        self.bridge = SimpleNamespace(
            RuntimeObjectById=self.handlers["RuntimeObjectById"],
            m_RuntimeObjects=_Registry(self.objects),
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
            m_Ctx=SimpleNamespace(reset=lambda: None),
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

    def dispatch(self, command_id: int, cmd: str, wire: dict | None) -> tuple[_Result, object]:
        """One wire command through its handler, as Dispatch routes it: the
        new MCPResult and the handler's postNow."""
        if wire is None:
            args = None
        else:
            unknown = set(wire) - set(self.arg_defaults)
            if unknown:
                raise AssertionError(f"keys these handlers do not read: {sorted(unknown)}")
            values = {**self.arg_defaults, **wire}
            values["mode"] = EnforceString.of(str(values["mode"]))
            args = SimpleNamespace(**values)
        command = SimpleNamespace(id=command_id, cmd=EnforceString.of(cmd), args=args)
        result = _Result(**self.result_fields)
        handler = {
            COMMAND: "DispatchSurvivorDrive",
            DRIVE_RELEASE: "DispatchSurvivorDriveRelease",
            "object_delete": "DispatchObjectDelete",
        }[cmd]
        post_now = self.handlers[handler](command, result)
        return result, post_now

    def tick(self, timeslice: float = 0.1) -> None:
        """One server bridge tick, MCPBridge.OnTick."""
        self.handlers["OnTick"](timeslice)

    def shutdown(self) -> None:
        """MCPBridge.Shutdown."""
        self.handlers["Shutdown"]()


class SurvivorDriveModelTest(unittest.TestCase):
    """The real Drive, Release, OnObjectGone, Tick and ShutdownAll and the
    player_move helpers they steer through, on recording controllers: after
    each step, the registry and both overrides."""

    def setUp(self) -> None:
        self.model = _DriveModel()

    def test_a_drive_applies_at_once_and_a_new_drive_replaces_it(self) -> None:
        survivor = self.model.spawn(7)
        self.model.drive(7, 90.0, 1.0)
        self.assertEqual(self.model.registry(), [(7, 90.0, 1.0)])
        self.assertEqual(survivor.controller.driving(), (1.0, 90.0))
        self.assertIs(self.model.call("IsDriven", 7), True)
        self.model.drive(7, 180.0, 3.0)
        self.assertEqual(self.model.registry(), [(7, 180.0, 3.0)])
        self.assertEqual(survivor.controller.driving(), (3.0, 180.0))

    def test_the_tick_steers_from_the_survivor_own_heading(self) -> None:
        survivor = self.model.spawn(7)
        self.model.drive(7, 90.0, 1.0)
        survivor.controller.face(90.0)
        self.model.call("Tick", self.model.bridge)
        self.assertEqual(self.model.registry(), [(7, 90.0, 1.0)])
        self.assertEqual(survivor.controller.driving(), (1.0, 0.0))
        # -170 wanted from compass 170 is -340, wrapped to 20.
        survivor.controller.face(170.0)
        self.model.drive(7, -170.0, 2.0)
        self.assertEqual(survivor.controller.driving(), (2.0, 20.0))
        survivor.controller.face(-100.0)
        self.model.call("Tick", self.model.bridge)
        self.assertEqual(self.model.registry(), [(7, -170.0, 2.0)])
        self.assertEqual(survivor.controller.driving(), (2.0, -70.0))

    def test_release_disables_the_overrides_and_the_next_tick_drives_nothing(self) -> None:
        survivor = self.model.spawn(7)
        self.model.drive(7, 90.0, 1.0)
        self.model.call("Release", self.model.bridge, 7)
        self.assertEqual(self.model.registry(), [])
        self.assertIs(self.model.call("IsDriven", 7), False)
        self.assertIsNone(survivor.controller.driving())
        self.assertEqual(
            survivor.controller.calls[-2:],
            [("speed", _OVERRIDE.DISABLED, 0.0), ("angle", _OVERRIDE.DISABLED, 0.0)],
        )
        calls = len(survivor.controller.calls)
        self.model.call("Tick", self.model.bridge)
        self.assertEqual(self.model.registry(), [])
        self.assertIsNone(survivor.controller.driving())
        self.model.call("Release", self.model.bridge, 7)
        self.assertEqual(len(survivor.controller.calls), calls)

    def test_a_release_of_a_survivor_that_is_not_driven_touches_no_controller(self) -> None:
        idle = self.model.spawn(8)
        walking = self.model.spawn(7)
        self.model.drive(7, 90.0, 1.0)
        calls = list(walking.controller.calls)
        self.model.call("Release", self.model.bridge, 8)
        self.assertEqual(idle.controller.calls, [])
        self.assertEqual(walking.controller.calls, calls)
        self.assertEqual(self.model.registry(), [(7, 90.0, 1.0)])

    def test_death_and_object_delete_end_the_drive_with_the_overrides_disabled(self) -> None:
        dying = self.model.spawn(7)
        deleted = self.model.spawn(8)
        self.model.drive(7, 90.0, 1.0)
        self.model.drive(8, 0.0, 2.0)
        dying.alive = False
        self.model.call("Tick", self.model.bridge)
        self.assertEqual(self.model.registry(), [(8, 0.0, 2.0)])
        self.assertIsNone(dying.controller.driving())
        self.assertEqual(deleted.controller.driving(), (2.0, 0.0))
        # object_delete calls OnObjectGone while the id still resolves, then
        # deletes the object (DispatchObjectDelete).
        self.model.call("OnObjectGone", self.model.bridge, 8)
        self.assertEqual(self.model.registry(), [])
        self.assertIsNone(deleted.controller.driving())

    def test_a_driven_id_that_no_longer_resolves_leaves_the_registry(self) -> None:
        self.model.spawn(9)
        self.model.drive(9, 90.0, 1.0)
        del self.model.objects[9]
        self.model.call("Tick", self.model.bridge)
        self.assertEqual(self.model.registry(), [])
        self.assertIs(self.model.call("IsDriven", 9), False)

    def test_the_tick_walks_backwards_so_two_removals_skip_no_entry(self) -> None:
        survivors = {object_id: self.model.spawn(object_id) for object_id in (7, 8, 9)}
        for object_id in survivors:
            self.model.drive(object_id, 90.0, 1.0)
        survivors[7].alive = False
        survivors[9].alive = False
        survivors[8].controller.face(90.0)
        self.model.call("Tick", self.model.bridge)
        self.assertEqual(self.model.registry(), [(8, 90.0, 1.0)])
        self.assertIsNone(survivors[7].controller.driving())
        self.assertIsNone(survivors[9].controller.driving())
        self.assertEqual(survivors[8].controller.driving(), (1.0, 0.0))

    def test_shutdown_disables_every_driven_survivor_and_empties_the_registry(self) -> None:
        first = self.model.spawn(7)
        second = self.model.spawn(8)
        self.model.drive(7, 90.0, 1.0)
        self.model.drive(8, 180.0, 3.0)
        self.model.call("ShutdownAll", self.model.bridge)
        self.assertEqual(self.model.registry(), [])
        self.assertIs(self.model.call("IsDriven", 7), False)
        self.assertIsNone(first.controller.driving())
        self.assertIsNone(second.controller.driving())
        calls = (len(first.controller.calls), len(second.controller.calls))
        self.model.call("Tick", self.model.bridge)
        self.assertEqual((len(first.controller.calls), len(second.controller.calls)), calls)

    def test_a_drive_release_names_one_drive_and_a_replace_moves_the_name_to_the_latest(self) -> None:
        survivor = self.model.spawn(7)
        self.model.drive(7, 90.0, 1.0, command_id=41)
        self.assertEqual(self.model.command_ids(), {7: 41})
        writes = self.model.writes()
        # Another id leaves the drive on.
        self.assertIs(self.model.call("ReleaseCommand", self.model.bridge, 7, 40), False)
        self.assertEqual(self.model.registry(), [(7, 90.0, 1.0)])
        self.assertEqual(survivor.controller.driving(), (1.0, 90.0))
        self.assertEqual(self.model.writes(), writes)
        # A replace keeps the latest id: the first is then a no-op.
        self.model.drive(7, 180.0, 3.0, command_id=43)
        self.assertEqual(self.model.command_ids(), {7: 43})
        writes = self.model.writes()
        self.assertIs(self.model.call("ReleaseCommand", self.model.bridge, 7, 41), False)
        self.assertEqual(survivor.controller.driving(), (3.0, 180.0))
        self.assertEqual(self.model.writes(), writes)
        self.assertIs(self.model.call("ReleaseCommand", self.model.bridge, 7, 43), True)
        self.assertEqual(self.model.registry(), [])
        self.assertIsNone(survivor.controller.driving())
        self.assertEqual(
            survivor.controller.calls[writes:],
            [("speed", _OVERRIDE.DISABLED, 0.0), ("angle", _OVERRIDE.DISABLED, 0.0)],
        )
        writes = self.model.writes()
        self.model.call("Tick", self.model.bridge)
        self.assertIs(self.model.call("ReleaseCommand", self.model.bridge, 7, 43), False)
        self.assertEqual(self.model.writes(), writes)

    def test_a_drive_release_stops_only_the_survivor_its_id_drives(self) -> None:
        first = self.model.spawn(7)
        second = self.model.spawn(9)
        self.model.drive(7, 90.0, 1.0, command_id=41)
        self.model.drive(9, 0.0, 2.0, command_id=42)
        self.assertIs(self.model.call("ReleaseCommand", self.model.bridge, 9, 42), True)
        self.assertEqual(self.model.command_ids(), {7: 41})
        self.assertIsNone(second.controller.driving())
        self.assertEqual(first.controller.driving(), (1.0, 90.0))
        self.assertIs(self.model.call("ReleaseCommand", self.model.bridge, 7, 41), True)
        self.assertEqual(self.model.registry(), [])
        self.assertIsNone(first.controller.driving())

    def test_each_refusal_of_the_drive_handler_changes_nothing(self) -> None:
        def stale(model: _HandlerModel) -> None:
            model.objects[7] = None

        def thing(model: _HandlerModel) -> None:
            model.objects[7] = _Thing()

        def identity(model: _HandlerModel) -> None:
            model.objects[7].identity = object()

        def dead(model: _HandlerModel) -> None:
            model.objects[7].alive = False

        def bot(model: _HandlerModel) -> None:
            model.bots.add(7)

        def no_controller(model: _HandlerModel) -> None:
            model.objects[7].has_controller = False

        def nothing(_model: _HandlerModel) -> None:
            return None

        cases = [
            ("bad_args", nothing, wire)
            for wire in (
                None,
                {**DRIVE_WIRE, "object_id": 0},
                {**DRIVE_WIRE, "speed": 1.5},
                {**DRIVE_WIRE, "speed": 4.0},
                {**DRIVE_WIRE, "heading": math.nan},
                {**DRIVE_WIRE, "heading": math.inf},
                {**DRIVE_WIRE, "heading": 360.5},
                {**DRIVE_WIRE, "heading": -361.0},
                {**DRIVE_WIRE, "heading_set": False},
                {**DRIVE_WIRE, "speed_set": False},
                # The flag without its heading: MCPArgs() left float.MAX there.
                {"object_id": 7, "heading_set": True, "speed": 1.0, "speed_set": True},
                {**DRIVE_WIRE, "mode": "stop"},
                {**RELEASE_WIRE, "heading": 90.0, "heading_set": True},
                {**RELEASE_WIRE, "speed": 1.0, "speed_set": True},
            )
        ]
        cases += [
            ("object_id_unknown", nothing, {**DRIVE_WIRE, "object_id": 8}),
            ("object_id_stale", stale, DRIVE_WIRE),
            ("bad_target", thing, DRIVE_WIRE),
            ("refused_player_controlled", identity, DRIVE_WIRE),
            ("player_dead", dead, DRIVE_WIRE),
            ("bot_busy", bot, DRIVE_WIRE),
            ("no_input_controller", no_controller, DRIVE_WIRE),
        ]
        self.assertEqual(len({error for error, _setup, _wire in cases}), 8)
        for error, setup, wire in cases:
            for driven in (False, True):
                with self.subTest(error=error, wire=wire, driven=driven):
                    model = _HandlerModel()
                    model.spawn(7, (11.0, 2.0, 33.0))
                    model.spawn(9)
                    if driven:
                        # A drive on 9 already runs: the refusal leaves it too.
                        model.drive(9, 0.0, 2.0, command_id=40)
                    before = (model.registry(), model.command_ids(), model.writes())
                    setup(model)
                    # The target refusals come before a release too.
                    shapes = [wire]
                    if error != "bad_args":
                        shapes.append({"object_id": wire["object_id"], "mode": "release"})
                    for shape in shapes:
                        result, post_now = model.dispatch(41, COMMAND, shape)
                        self.assertIs(post_now, True)
                        self.assertIs(result.ok, False)
                        self.assertEqual(result.error, EnforceString.of(error))
                        self.assertIsNone(result.pos_real)
                        self.assertEqual((model.registry(), model.command_ids(), model.writes()), before)
                    if not driven:
                        self.assertIsNone(model.G.s_Driven)

    def test_the_drive_handler_answers_the_position_with_its_three_coordinates(self) -> None:
        model = _HandlerModel()
        survivor = model.spawn(7, (11.0, 2.0, 33.0))
        result, post_now = model.dispatch(41, COMMAND, DRIVE_WIRE)
        self.assertIs(post_now, True)
        self.assertEqual((result.ok, result.error), (True, EnforceString(b"")))
        self.assertEqual(result.pos_real.items, [11.0, 2.0, 33.0])
        self.assertEqual(model.registry(), [(7, 90.0, 1.0)])
        self.assertEqual(model.command_ids(), {7: 41})
        self.assertEqual(survivor.controller.driving(), (1.0, 90.0))
        survivor.position = (-4.5, 7.25, 1980.0)
        result, _post_now = model.dispatch(42, COMMAND, RELEASE_WIRE)
        self.assertIs(result.ok, True)
        self.assertEqual(result.pos_real.items, [-4.5, 7.25, 1980.0])
        self.assertEqual(model.registry(), [])
        self.assertIsNone(survivor.controller.driving())
        self.assertEqual(survivor.position, (-4.5, 7.25, 1980.0))

    def test_the_release_handler_drops_only_the_drive_its_id_set(self) -> None:
        model = _HandlerModel()
        first = model.spawn(7)
        second = model.spawn(9)
        model.dispatch(41, COMMAND, DRIVE_WIRE)
        model.dispatch(42, COMMAND, {**DRIVE_WIRE, "object_id": 9, "heading": 0.0})
        writes = model.writes()
        for wire in (
            None,
            {},
            {"command_id": 0, "object_id": 9},
            {"command_id": -42, "object_id": 9},
            {"command_id": 42},
            {"command_id": 42, "object_id": 0},
            {"command_id": 42, "object_id": -9},
        ):
            with self.subTest(wire=wire):
                result, post_now = model.dispatch(50, DRIVE_RELEASE, wire)
                self.assertIs(post_now, True)
                self.assertEqual((result.ok, result.error, result.matched), (False, EnforceString.of("bad_args"), False))
                self.assertEqual((model.command_ids(), model.writes()), ({7: 41, 9: 42}, writes))
        result, _post_now = model.dispatch(51, DRIVE_RELEASE, {"command_id": 40, "object_id": 7})
        self.assertEqual((result.ok, result.matched), (True, False))
        self.assertEqual((model.command_ids(), model.writes()), ({7: 41, 9: 42}, writes))
        result, _post_now = model.dispatch(52, DRIVE_RELEASE, {"command_id": 42, "object_id": 9})
        self.assertEqual((result.ok, result.matched), (True, True))
        self.assertEqual(model.command_ids(), {7: 41})
        self.assertIsNone(second.controller.driving())
        self.assertEqual(first.controller.driving(), (1.0, 90.0))
        self.assertIsNone(result.pos_real)

    def test_one_command_id_on_two_survivors_a_release_stops_only_the_one_it_names(self) -> None:
        # Command ids restart with each daemon: after a restart a drive on 9
        # can carry the id that a drive on 7 got before it.
        model = _HandlerModel()
        first = model.spawn(7)
        second = model.spawn(9)
        model.dispatch(5, COMMAND, DRIVE_WIRE)
        model.dispatch(5, COMMAND, {**DRIVE_WIRE, "object_id": 9, "heading": 0.0})
        self.assertEqual(model.command_ids(), {7: 5, 9: 5})
        writes = len(first.controller.calls)
        result, post_now = model.dispatch(60, DRIVE_RELEASE, {"command_id": 5, "object_id": 9})
        self.assertIs(post_now, True)
        self.assertEqual((result.ok, result.matched), (True, True))
        self.assertEqual(model.command_ids(), {7: 5})
        self.assertIsNone(second.controller.driving())
        self.assertEqual(first.controller.driving(), (1.0, 90.0))
        self.assertEqual(len(first.controller.calls), writes)

    def test_a_release_with_the_right_id_and_another_survivor_matches_nothing(self) -> None:
        model = _HandlerModel()
        first = model.spawn(7)
        second = model.spawn(9)
        model.spawn(11)
        model.dispatch(41, COMMAND, DRIVE_WIRE)
        model.dispatch(42, COMMAND, {**DRIVE_WIRE, "object_id": 9, "heading": 0.0})
        writes = model.writes()
        # 41 drives 7 and 42 drives 9; 11 is not driven at all.
        for wire in (
            {"command_id": 41, "object_id": 9},
            {"command_id": 42, "object_id": 7},
            {"command_id": 41, "object_id": 11},
        ):
            with self.subTest(wire=wire):
                result, post_now = model.dispatch(61, DRIVE_RELEASE, wire)
                self.assertIs(post_now, True)
                self.assertEqual((result.ok, result.matched), (True, False))
                self.assertEqual((model.command_ids(), model.writes()), ({7: 41, 9: 42}, writes))
        self.assertEqual(first.controller.driving(), (1.0, 90.0))
        self.assertEqual(second.controller.driving(), (1.0, 0.0))

    def test_after_the_lease_ends_the_next_owner_inherits_no_drive(self) -> None:
        # Codex's probe, with the real coordinator, cleanup_owner and acquire
        # in memory and the translated bridge: A's drive is delivered and
        # answered, A releases, B acquires, and the bridge runs what it polls.
        model = _HandlerModel()
        survivor = model.spawn(7, (11.0, 2.0, 33.0))
        state, coordinator = _leased_state()
        token = _acquire(coordinator, OWNER_A)
        drive = _send(state, OWNER_A, token, DRIVE_WIRE)
        [command] = _delivered(state)
        self.assertEqual((command["id"], command["cmd"]), (drive, COMMAND))
        result, _post_now = model.dispatch(command["id"], command["cmd"], command["args"])
        self.assertIs(result.ok, True)
        self.assertEqual(
            _answered(state, drive, {"ok": 1, "pos_real": list(result.pos_real.items)})["pos_real"],
            [11.0, 2.0, 33.0],
        )
        self.assertEqual(bound_queue(state, "server"), [])
        self.assertEqual(survivor.controller.driving(), (1.0, 90.0))
        released = _release(coordinator, OWNER_A, token)
        self.assertEqual(released["cleanup"]["survivor_drive_release_enqueued"], 1)
        self.assertEqual(released["cleanup_degraded"], [])
        _acquire(coordinator, OWNER_B)
        [command] = _delivered(state)
        self.assertEqual(
            (command["cmd"], command["args"]), (DRIVE_RELEASE, {"command_id": drive, "object_id": 7})
        )
        result, _post_now = model.dispatch(command["id"], command["cmd"], command["args"])
        self.assertEqual((result.ok, result.matched), (True, True))
        self.assertIsNone(survivor.controller.driving())
        writes = model.writes()
        for _tick in range(3):
            model.call("Tick", model.bridge)
        self.assertEqual(model.writes(), writes)
        self.assertEqual(model.registry(), [])
        self.assertEqual(_delivered(state), [])

    def test_with_nothing_driven_the_hooks_change_nothing(self) -> None:
        idle = self.model.spawn(7)
        self.model.call("Tick", self.model.bridge)
        self.model.call("Release", self.model.bridge, 7)
        self.model.call("OnObjectGone", self.model.bridge, 7)
        self.model.call("ShutdownAll", self.model.bridge)
        self.assertEqual(idle.controller.calls, [])
        self.assertIsNone(self.model.G.s_Driven)

    def test_only_walk_jog_and_sprint_are_drive_speeds(self) -> None:
        for speed, expected in ((1.0, True), (2.0, True), (3.0, True), (0.0, False), (1.5, False), (4.0, False)):
            with self.subTest(speed=speed):
                self.assertIs(self.model.call("IsDriveSpeed", speed), expected)


class SurvivorDriveBridgeHookTest(unittest.TestCase):
    """MCPBridge.c's own OnTick, DispatchObjectDelete and Shutdown
    (HOOK_METHODS), run on the registry and the controllers of two drives the
    real handler set: the effect of each hook, where
    test_the_bridge_ticks_deletes_and_shuts_the_drive_down reads its text."""

    def setUp(self) -> None:
        self.model = _HandlerModel()
        self.first = self.model.spawn(7)
        self.second = self.model.spawn(9)
        self.model.dispatch(41, COMMAND, DRIVE_WIRE)
        self.model.dispatch(42, COMMAND, {**DRIVE_WIRE, "object_id": 9, "heading": 0.0, "speed": 3.0})
        self.assertEqual(self.model.command_ids(), {7: 41, 9: 42})
        self.assertEqual(self.first.controller.driving(), (1.0, 90.0))
        self.assertEqual(self.second.controller.driving(), (3.0, 0.0))

    def test_the_bridge_tick_steers_each_drive_and_drops_a_dead_one(self) -> None:
        self.first.controller.face(45.0)
        self.second.alive = False
        self.model.tick()
        self.assertEqual(self.first.controller.driving(), (1.0, 45.0))
        self.assertIsNone(self.second.controller.driving())
        self.assertEqual(self.model.command_ids(), {7: 41})

    def test_object_delete_drops_the_drive_while_the_id_still_resolves(self) -> None:
        calls = len(self.first.controller.calls)
        result, post_now = self.model.dispatch(50, "object_delete", {"object_id": 7})
        self.assertIs(post_now, True)
        self.assertEqual((result.ok, result.object_id, result.deleted), (True, 7, 1))
        self.assertEqual(self.model.deleted, [self.first])
        self.assertNotIn(7, self.model.objects)
        self.assertEqual(self.model.command_ids(), {9: 42})
        self.assertIs(self.model.call("IsDriven", 7), False)
        # The deleted survivor's own controller: DISABLED reached it before
        # the registry forgot the id.
        self.assertEqual(
            self.first.controller.calls[calls:],
            [("speed", _OVERRIDE.DISABLED, 0.0), ("angle", _OVERRIDE.DISABLED, 0.0)],
        )
        self.assertEqual(self.second.controller.driving(), (3.0, 0.0))

    def test_the_bridge_shutdown_ends_every_drive(self) -> None:
        self.model.shutdown()
        self.assertEqual(self.model.registry(), [])
        self.assertIsNone(self.first.controller.driving())
        self.assertIsNone(self.second.controller.driving())
        self.assertIs(self.model.call("IsDriven", 7), False)
        self.assertIs(self.model.call("IsDriven", 9), False)


if __name__ == "__main__":
    unittest.main()
