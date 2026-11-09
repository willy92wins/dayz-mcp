"""B2 rest (fb-20261028-131048-091a): the bridge acts on the player uid names.

vehicle_enter seats the player its uid names and answers player_not_found
for an empty or unknown uid; its seat job reads only that player, so a job
whose player is gone ends at its deadline instead of reading the first human.
A server inventory_move whose item or target hangs from a player answers
uid_required without a uid instead of resolving the first human.

The bodies are the real ones of MCPBridge.c, translated and run on fakes
(enforce_subset_helpers) with their Log lines dropped. The inventory_move
player gate is run on its own: its ResolvePlayer call, whose error is an out
parameter, becomes a call to a recording fake.
"""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    fake_class,
    method_body,
    translate,
)

_MISSION = addon_root() / "scripts" / "5_Mission"
BRIDGE = (_MISSION / "MCPBridge.c").read_text(encoding="utf-8")
MESSAGES = (_MISSION / "MCPMessages.c").read_text(encoding="utf-8")
UID_A = "76561198000000001"
UID_B = "76561198000000002"
DRIVER = 0

FIRST_HUMAN = "protected Human GetFirstHuman()"
HUMAN_BY_UID = "protected Human FindHumanByUid(string uid)"
VEHICLE_ENTER = "protected bool DispatchVehicleEnter(MCPCommand command, MCPResult result)"
SEAT_READY = "protected bool IsSeatReady(MCPJob job)"
NEEDS_PLAYER = "protected bool MCPInventoryMoveNeedsPlayer(string op)"
INVENTORY_MOVE = "protected bool DispatchInventoryMove(MCPCommand command, MCPResult result)"
PLAYER_GATE = 'if (authority == "client" || MCPInventoryMoveNeedsPlayer(op) || rootedOnPlayer)'
GATE_SIGNATURE = (
    "protected bool PlayerGate(MCPCommand command, MCPResult result, "
    "string authority, string op, bool rootedOnPlayer)"
)
RESOLVE_CALL = "player = ResolvePlayer(command.args, playerError);"


def _s(text: str) -> EnforceString:
    return EnforceString.of(text)


def _without_logs(signature: str) -> str:
    body = re.sub(r"\bLog\([^;\n]*\);", "", method_body(BRIDGE, signature))
    return signature + "\n{" + body + "}\n"


class _Identity:
    def __init__(self, uid: str) -> None:
        self.uid = _s(uid)

    def GetPlainId(self) -> EnforceString:
        return self.uid


class _VehicleCommand:
    def __init__(self, transport: object, getting_in: bool) -> None:
        self.transport = transport
        self.getting_in = getting_in
        self.seat = DRIVER

    def SetVehicleType(self, _anim: object) -> None:
        pass

    def GetTransport(self) -> object:
        return self.transport

    def IsGettingIn(self) -> bool:
        return self.getting_in

    def GetVehicleSeat(self) -> int:
        return self.seat


class _Human:
    def __init__(self, uid: str) -> None:
        self.identity = _Identity(uid)
        self.command: _VehicleCommand | None = None
        self.started: list[object] = []

    def GetIdentity(self) -> _Identity:
        return self.identity

    def GetCommand_Vehicle(self) -> _VehicleCommand | None:
        return self.command

    def StartCommand_Vehicle(self, vehicle: object, _crew: int, _anim: int) -> _VehicleCommand:
        self.started.append(vehicle)
        self.command = _VehicleCommand(vehicle, getting_in=True)
        return self.command


class _Vehicle:
    def GetSeatAnimationType(self, _crew: int) -> int:
        return 1

    def GetAnimInstance(self) -> int:
        return 0


class _Bridge:
    """GetFirstHuman, FindHumanByUid, DispatchVehicleEnter and IsSeatReady as
    they are, on the players GetGame().GetPlayers lists, first human first."""

    def __init__(self, players: list[_Human]) -> None:
        self.players = players
        self.vehicle = _Vehicle()
        self.jobs = EnforceMap()
        game = SimpleNamespace(GetPlayers=self._get_players)
        self.ns: dict = {
            "_S": EnforceString,
            "m_Players": EnforceArray(),
            "m_Jobs": self.jobs,
            "m_ElapsedS": 10.0,
            "JOB_TIMEOUT_S": 5.0,
            "GetGame": lambda: game,
            "Human": SimpleNamespace(Cast=lambda man: man if isinstance(man, _Human) else None),
            "ValidatePositionArgs": lambda args: SimpleNamespace(ok=True, error=_s(""), pos=args.pos),
            "FindTransportNear": lambda _pos: self.vehicle,
            "HasActiveJobFor": lambda _human, _vehicle: False,
            "DayZPlayerConstants": SimpleNamespace(VEHICLESEAT_DRIVER=DRIVER),
        }
        fake_class(MESSAGES, "MCPJob", self.ns)
        for signature in (FIRST_HUMAN, HUMAN_BY_UID, VEHICLE_ENTER, SEAT_READY):
            translate(
                _without_logs(signature),
                signature,
                self.ns,
                class_types=(
                    "Man",
                    "PlayerIdentity",
                    "Human",
                    "MCPSpawnValidation",
                    "Transport",
                    "HumanCommandVehicle",
                    "MCPJob",
                ),
                members=("m_Players",),
            )

    def _get_players(self, out: EnforceArray) -> None:
        for player in self.players:
            out.Insert(player)

    def enter(self, uid: str) -> tuple[bool, SimpleNamespace]:
        command = SimpleNamespace(id=7, args=SimpleNamespace(pos=[1.0, 2.0, 3.0], uid=_s(uid)))
        result = SimpleNamespace(
            ok=True, error=_s(""), tick_poll_sent=0, tick_poll_callback=0, tick_dispatch=0
        )
        return self.ns["DispatchVehicleEnter"](command, result), result

    def seat_job(self, actor: _Human | None) -> object:
        job = self.ns["MCPJob"]()
        job.subject = self.vehicle
        job.actor = actor
        return job


class VehicleEnterUidTest(unittest.TestCase):
    def test_the_uid_names_the_player_seated(self) -> None:
        for uid, seated in ((UID_B, 1), (UID_A, 0)):
            with self.subTest(uid=uid):
                players = [_Human(UID_A), _Human(UID_B)]
                bridge = _Bridge(players)
                done, result = bridge.enter(uid)
                self.assertIs(done, False)
                self.assertEqual(players[seated].started, [bridge.vehicle])
                self.assertEqual(players[1 - seated].started, [])
                self.assertEqual(bridge.jobs.Count(), 1)
                self.assertIs(bridge.jobs.GetElement(0).actor, players[seated])

    def test_an_empty_or_unknown_uid_seats_nobody(self) -> None:
        for uid in ("", "76561198000000009"):
            with self.subTest(uid=uid):
                players = [_Human(UID_A), _Human(UID_B)]
                bridge = _Bridge(players)
                done, result = bridge.enter(uid)
                self.assertIs(done, True)
                self.assertEqual((result.ok, result.error.text), (False, "player_not_found"))
                self.assertEqual([player.started for player in players], [[], []])
                self.assertEqual(bridge.jobs.Count(), 0)

    def test_the_seat_job_reads_only_its_player(self) -> None:
        first = _Human(UID_A)
        second = _Human(UID_B)
        bridge = _Bridge([first, second])
        first.command = _VehicleCommand(bridge.vehicle, getting_in=False)
        # Its player gone, the job does not read the first human in the seat.
        gone = bridge.seat_job(None)
        self.assertIs(bridge.ns["IsSeatReady"](gone), False)
        self.assertIsNone(gone.actor)
        second.command = _VehicleCommand(bridge.vehicle, getting_in=True)
        own = bridge.seat_job(second)
        self.assertIs(bridge.ns["IsSeatReady"](own), False)
        second.command.getting_in = False
        self.assertIs(bridge.ns["IsSeatReady"](own), True)


def _player_gate_source() -> str:
    """The inventory_move player gate, run as a method of its own."""
    body = method_body(BRIDGE, INVENTORY_MOVE)
    if body.count(PLAYER_GATE) != 1:
        raise AssertionError("the inventory_move player gate is not found exactly once")
    start = body.index(PLAYER_GATE)
    depth = 0
    for index in range(body.index("{", start), len(body)):
        depth += (body[index] == "{") - (body[index] == "}")
        if depth == 0:
            gate = body[start : index + 1]
            break
    else:
        raise AssertionError("unterminated player gate")
    if gate.count(RESOLVE_CALL) != 1 or body.count("ResolvePlayer(") != 1:
        raise AssertionError("the gate is not the one ResolvePlayer call of the method")
    gate = gate.replace(
        RESOLVE_CALL,
        "player = ResolvePlayerFake(command.args); playerError = LastResolveError();",
    )
    return (
        GATE_SIGNATURE
        + "\n{\nPlayerBase player = null;\n"
        + gate
        + "\nreturn false;\n}\n"
    )


class InventoryMovePlayerGateTest(unittest.TestCase):
    def gate(self, authority: str, op: str, rooted: bool, uid: str, found: bool = True):
        resolved: list[object] = []
        player = object()

        def resolve(args: object) -> object | None:
            resolved.append(args)
            return player if found else None

        ns: dict = {
            "_S": EnforceString,
            "ResolvePlayerFake": resolve,
            "LastResolveError": lambda: _s("") if found else _s("player_not_found"),
        }
        translate(_without_logs(NEEDS_PLAYER), NEEDS_PLAYER, ns)
        translate(_player_gate_source(), GATE_SIGNATURE, ns, class_types=("PlayerBase",))
        args = SimpleNamespace(uid=_s(uid))
        result = SimpleNamespace(ok=True, error=_s(""))
        done = ns["PlayerGate"](
            SimpleNamespace(args=args), result, _s(authority), _s(op), rooted
        )
        return done, result.error.text, resolved

    def test_a_move_that_needs_a_player_and_has_no_uid_is_refused(self) -> None:
        for authority, op, rooted in (
            ("server", "to_cargo", True),
            ("server", "to_hands", False),
            ("client", "to_cargo", False),
        ):
            with self.subTest(authority=authority, op=op, rooted=rooted):
                self.assertEqual(
                    self.gate(authority, op, rooted, ""), (True, "uid_required", [])
                )

    def test_a_uid_still_resolves_its_player(self) -> None:
        done, error, resolved = self.gate("server", "to_cargo", True, UID_B)
        self.assertEqual((done, error, len(resolved)), (False, "", 1))
        self.assertEqual(resolved[0].uid.text, UID_B)
        self.assertEqual(
            self.gate("client", "to_hands", False, UID_B, found=False)[:2],
            (True, "player_not_found"),
        )

    def test_a_world_to_world_server_move_names_no_player(self) -> None:
        self.assertEqual(self.gate("server", "to_cargo", False, ""), (False, "", []))


if __name__ == "__main__":
    unittest.main()
