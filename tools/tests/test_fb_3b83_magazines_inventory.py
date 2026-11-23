"""3b83: a magazine through world_spawn, inventory_give's ammo, and what inventory_move confirms.

Inbox fb-20261123-011026-3b83 (run 8ef97b93, a modded Mag_6L31_60Rnd on an AK74) found three gaps:
- world_spawn refused the magazine with unknown_type: the bridge checked CfgVehicles only (MCPBridge.c
  ValidateSpawnArgs), while vanilla creates CfgMagazines and CfgWeapons classes with CreateObjectEx. It now takes a
  classname of CfgVehicles, CfgWeapons or CfgMagazines (IsKnownInventoryClassname); a classname of none of them is
  still unknown_type, and the daemon's texts say the three tables.
- inventory_give had no way to fill a magazine. An optional ammo, an integer of 0 or more, travels with ammo_set
  true, as lifetime_s travels with lifetime_s_set (the bridge reads an absent key as 0 or false). A negative or
  non-integer ammo is refused before the bridge. The bridge refuses with bad_args, before creating anything, an ammo
  on a classname that is not a CfgMagazines class or above that magazine's capacity, and calls ServerSetAmmoCount
  right after creating it.
- inventory_move confirmed a to_inventory with the weapon still in the player's hands: the confirmation read only
  where the item was after the call, and the hands count for to_inventory. The job now keeps where the item was at
  dispatch; confirmed needs the item at the requested destination and no longer there; an item already at its
  destination at dispatch is answered with already_at_destination and moves nothing.

Pinned here: the tool and daemon layers, the published descriptions, and, read from the bridge's source, the order
of the checks that must come before anything is created or moved. What happens in the game is the gate's run.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.effective_schema import resolve_effective_schemas
from tests._addon_paths import addon_root

UID = "76561198000000001"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
GIVE = {"classname": "Mag_AK74_30Rnd", "dest": "inventory", "uid": UID}
GIVE_WIRE = {"classname": "Mag_AK74_30Rnd", "dest": "inventory", "uid": UID}
TABLES = "CfgVehicles, CfgWeapons or CfgMagazines"


def _error_text(exc: BaseException) -> str:
    text = str(exc)
    for attr in ("exceptions", "error"):
        nested = getattr(exc, attr, None)
        if nested:
            text += " " + str(nested)
    return text


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


class InventoryGiveAmmoToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _give(self, arguments: dict) -> AsyncMock:
        reply = {"ok": 1, "object_id": 7, "classname": arguments["classname"], "type": arguments["classname"]}
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=reply)) as call:
            await self.app.call_tool("inventory_give", arguments)
        return call

    async def test_without_ammo_no_ammo_key_travels(self) -> None:
        call = await self._give(dict(GIVE))
        call.assert_awaited_once()
        self.assertEqual("inventory_give", call.await_args.args[0])
        sent = call.await_args.args[1]
        self.assertNotIn("ammo", sent)
        self.assertNotIn("ammo_set", sent)

    async def test_an_ammo_travels_with_ammo_set(self) -> None:
        for ammo in (30, 0):
            with self.subTest(ammo=ammo):
                call = await self._give({**GIVE, "ammo": ammo})
                sent = call.await_args.args[1]
                self.assertEqual(ammo, sent["ammo"])
                self.assertIs(True, sent["ammo_set"])

    async def test_a_negative_or_non_integer_ammo_is_refused_before_the_bridge(self) -> None:
        for ammo in (-1, 30.5, "30", True):
            with self.subTest(ammo=ammo):
                with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
                    with self.assertRaises(Exception) as caught:
                        await self.app.call_tool("inventory_give", {**GIVE, "ammo": ammo})
                call.assert_not_awaited()
                self.assertIn("ammo", _error_text(caught.exception))

    def test_the_schema_publishes_ammo_as_an_optional_integer(self) -> None:
        params = resolve_effective_schemas()["inventory_give"]["params"]
        self.assertIn("ammo", params)
        self.assertIs(False, params["ammo"]["required"])
        self.assertIsNone(params["ammo"]["default"])
        self.assertIn("integer", str(params["ammo"]["type"]))
        self.assertNotIn("number", str(params["ammo"]["type"]))

    def test_the_description_says_what_ammo_does_and_when_it_is_refused(self) -> None:
        description = resolve_effective_schemas()["inventory_give"]["description"]
        for phrase in (
            "ammo (an integer, 0 or more)",
            "ServerSetAmmoCount",
            "object_inspect",
            "capacity",
            "returns bad_args and creates nothing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)


class InventoryGiveAmmoDaemonTest(unittest.TestCase):
    def test_the_wire_shapes_the_tool_sends_are_accepted(self) -> None:
        for args in (
            GIVE_WIRE,
            {**GIVE_WIRE, "ammo": 30, "ammo_set": True},
            {**GIVE_WIRE, "ammo": 0, "ammo_set": True},
        ):
            with self.subTest(args=args):
                ok, error = loopback.validate_command_args("inventory_give", args)
                self.assertTrue(ok, error)

    def test_ammo_and_ammo_set_travel_together_or_not_at_all(self) -> None:
        for args in (
            {**GIVE_WIRE, "ammo": 30},
            {**GIVE_WIRE, "ammo_set": True},
            {**GIVE_WIRE, "ammo": 30, "ammo_set": False},
            {**GIVE_WIRE, "ammo": -1, "ammo_set": True},
            {**GIVE_WIRE, "ammo": 30.5, "ammo_set": True},
            {**GIVE_WIRE, "ammo": "30", "ammo_set": True},
            {**GIVE_WIRE, "ammo": True, "ammo_set": True},
        ):
            with self.subTest(args=args):
                self.assertEqual((False, "bad_args"), loopback.validate_command_args("inventory_give", args))


class WorldSpawnClassnameTablesTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_type_names_the_three_tables(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        # The error the runtime raises for {ok: 0, error: unknown_type}, as test_v13_results_descriptions does.
        refusal = server._bridge_error({"ok": 0, "error": "unknown_type"}, "world_spawn")
        with patch.object(runtime, "call_bridge", new=AsyncMock(side_effect=refusal)):
            with self.assertRaises(Exception) as caught:
                await app.call_tool("world_spawn", {"type": "FX_NoSuchClass", "pos": [1.0, 0.0, 2.0]})
        text = _error_text(caught.exception)
        self.assertIn("unknown_type: type 'FX_NoSuchClass' is not a classname of " + TABLES, text)
        self.assertNotIn("is not a CfgVehicles classname", text)

    def test_the_description_names_the_three_tables(self) -> None:
        description = resolve_effective_schemas()["world_spawn"]["description"]
        self.assertIn("type is a classname of " + TABLES, description)
        self.assertNotIn("type is a CfgVehicles classname", description)

    def test_a_batch_item_type_names_the_three_tables(self) -> None:
        from dayz_mcp import tool_args

        with self.assertRaises(Exception) as caught:
            tool_args._spawn_batch_item_args(0, {"type": "", "pos": [1.0, 0.0, 2.0]})
        text = _error_text(caught.exception)
        self.assertIn("must be a classname of " + TABLES, text)
        self.assertNotIn("CfgVehicles classname", text)


class BridgeSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = BRIDGE_PATH.read_text(encoding="utf-8")
        cls.messages = MESSAGES_PATH.read_text(encoding="utf-8")

    def test_world_spawn_checks_the_three_tables(self) -> None:
        body = _method_body(self.bridge, "protected MCPSpawnValidation ValidateSpawnArgs(")
        self.assertIn("IsKnownInventoryClassname(args.type)", body)
        self.assertNotIn('"CfgVehicles "', body)

    def test_the_ammo_checks_come_before_anything_is_created(self) -> None:
        body = _method_body(self.bridge, "protected bool DispatchInventoryGive(")
        create = min(body.index("CreateInHands("), body.index("CreateInInventory("))
        for token in ("command.args.ammo_set", '"CfgMagazines ', 'result.error = "bad_args"'):
            with self.subTest(token=token):
                self.assertIn(token, body[:create])
        self.assertIn("ServerSetAmmoCount(", body[create:])
        self.assertNotIn("ServerSetAmmoCount(", body[:create])

    def test_the_args_class_carries_ammo_and_its_flag(self) -> None:
        self.assertIn("\tint ammo;\n", self.messages)
        self.assertIn("\tbool ammo_set;\n", self.messages)

    def test_a_move_already_at_its_destination_moves_nothing(self) -> None:
        body = _method_body(self.bridge, "protected bool DispatchInventoryMove(")
        refusal = body.index('"already_at_destination"')
        for call in ("MCPServerInventoryMove(", "MCPPerformClientInventoryMove(", "MCPSendInventoryMove("):
            with self.subTest(call=call):
                self.assertLess(refusal, body.index(call))

    def test_the_job_keeps_where_the_item_was(self) -> None:
        job = _method_body(self.messages, "class MCPJob")
        for field in ("move_origin_type", "move_origin_parent", "move_origin_slot"):
            with self.subTest(field=field):
                self.assertIn(field, job)
        body = _method_body(self.bridge, "protected bool DispatchInventoryMove(")
        perform = body.index("MCPServerInventoryMove(")
        self.assertIn("job.move_origin_type", body[:perform])


class InventoryMoveDescriptionTest(unittest.TestCase):
    def test_the_description_says_what_confirmed_needs_and_names_the_new_code(self) -> None:
        description = resolve_effective_schemas()["inventory_move"]["description"]
        for phrase in ("already_at_destination", "no longer where it was"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)
        self.assertNotIn("confirmed is true when the item lands in this player's hands.", description)


if __name__ == "__main__":
    unittest.main()
