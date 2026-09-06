"""entities_query rows publish has_cargo as bool | None.

Run:
    cd tools && ./.venv-mcp/Scripts/python.exe -m unittest tests.test_entities_query_cargo -v

WHAT THE BRIDGE SENDS -- no game, no bridge, no lease.
MCPEntityHit declares `bool has_cargo` (addon/scripts/5_Mission/MCPMessages.c:360) and
DispatchEntitiesQuery fills it with HasCargoCapacity (MCPBridge.c:1422, defined :1441-1456
as EntityAI.Cast -> GetInventory() -> GetCargo() != null). Every test here replaces
runtime.call_bridge with a fake that returns literal row dicts, the same seam
tests/test_lote_v_products.py:64-80 already uses, so the rows below ARE the wire and
nothing about a live client is assumed.

TWO WIRE FACTS DRIVE THE NORMALISATION UNDER TEST:
  1. the bridge serialises an Enforce bool as int 0/1 -- server.py's wait_for_result says
     so about `ok`. A consumer testing `row["has_cargo"] is True` would read every
     container as false.
  2. a bridge that predates the field does not emit the key at all. Absent must read as
     "the bridge did not say" (null), never as "no cargo" (false).

CONTRACT UNDER TEST:
  result["entities"][i]["has_cargo"]   True | False | None, always present on a dict row
  True   the row's own object owns a cargo grid (CAPACITY, empty container included)
  False  the bridge said no: not an EntityAI, or an EntityAI without a cargo grid
  None   the bridge did not say: field absent, null, or a form this layer will not guess
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, build_app


def _content_json(result) -> dict:
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


# The two row forms, in the shape the bridge writes them.
# BRIDGE WITH THE FIELD: MCPEntityHit serialises has_cargo as an int 0/1.
ROW_NEW_CONTAINER = {
    "type": "SeaChest",
    "classname": "SeaChest",
    "has_cargo": 1,
    "pos": [100.0, 10.0, 200.0],
    "distance": 1.5,
}
ROW_NEW_PLAIN = {
    "type": "Land_Rock_1",
    "classname": "Building",
    "has_cargo": 0,
    "pos": [101.0, 10.0, 200.0],
    "distance": 2.5,
}
# BRIDGE WITHOUT THE FIELD: the key does not exist. Same row otherwise.
ROW_OLD = {
    "type": "SeaChest",
    "classname": "SeaChest",
    "pos": [100.0, 10.0, 200.0],
    "distance": 1.5,
}


def _bridge_returning(rows):
    async def fake_call(cmd, args, peer, timeout_s):
        if cmd == "entities_query":
            return {
                "ok": 1,
                "count_total": len(rows),
                "entities": [dict(row) for row in rows],
            }
        if cmd == "query_all_players":
            return {"ok": 1, "players": [{"uid": "1", "pos": [100.0, 10.0, 200.0]}]}
        raise AssertionError(cmd)

    return fake_call


class NormalizeEntitiesCargoTest(unittest.TestCase):
    """The unit under the tool: dict in, dict out, no I/O."""

    def test_bridge_integer_one_reads_true(self) -> None:
        out = server._normalize_entities_cargo({"ok": 1, "entities": [dict(ROW_NEW_CONTAINER)]})
        flag = out["entities"][0]["has_cargo"]
        self.assertIs(flag, True, "an int 1 off the wire must publish as the bool True")

    def test_bridge_integer_zero_reads_false(self) -> None:
        out = server._normalize_entities_cargo({"ok": 1, "entities": [dict(ROW_NEW_PLAIN)]})
        self.assertIs(out["entities"][0]["has_cargo"], False)

    def test_a_real_bool_survives_unchanged(self) -> None:
        rows = [{"type": "A", "has_cargo": True}, {"type": "B", "has_cargo": False}]
        out = server._normalize_entities_cargo({"ok": 1, "entities": rows})
        flags = [row["has_cargo"] for row in out["entities"]]
        self.assertIs(flags[0], True)
        self.assertIs(flags[1], False)

    def test_absent_field_publishes_null_not_false(self) -> None:
        # The point of the tolerance: an old bridge is silent, and silence is not a
        # denial. A false here would answer a question the bridge never was asked.
        out = server._normalize_entities_cargo({"ok": 1, "entities": [dict(ROW_OLD)]})
        row = out["entities"][0]
        self.assertIn("has_cargo", row)
        self.assertIsNone(row["has_cargo"])

    def test_explicit_null_stays_null(self) -> None:
        out = server._normalize_entities_cargo({"ok": 1, "entities": [{"has_cargo": None}]})
        self.assertIsNone(out["entities"][0]["has_cargo"])

    def test_a_form_this_layer_will_not_guess_reads_null(self) -> None:
        # Negative control for the coercion: only bool and int are read. A string, a
        # float or a container is not truthiness-tested into a verdict.
        for value in ("true", "1", 1.0, [], {}, "no"):
            with self.subTest(value=value):
                out = server._normalize_entities_cargo({"ok": 1, "entities": [{"has_cargo": value}]})
                self.assertIsNone(out["entities"][0]["has_cargo"])

    def test_other_keys_and_row_count_are_untouched(self) -> None:
        payload = {
            "ok": 1,
            "count_total": 2,
            "entities": [dict(ROW_NEW_CONTAINER), dict(ROW_OLD)],
        }
        out = server._normalize_entities_cargo(payload)
        self.assertEqual(out["count_total"], 2)
        self.assertEqual(len(out["entities"]), 2)
        self.assertEqual(out["entities"][0]["type"], "SeaChest")
        self.assertEqual(out["entities"][0]["pos"], [100.0, 10.0, 200.0])
        self.assertEqual(out["entities"][0]["distance"], 1.5)
        self.assertEqual(out["entities"][1]["classname"], "SeaChest")

    def test_missing_or_odd_shapes_never_raise(self) -> None:
        # Absence of the field is one thing; absence of the container is another.
        # Neither may cost the caller the answer it did get.
        self.assertEqual(server._normalize_entities_cargo({"ok": 1}), {"ok": 1})
        self.assertEqual(
            server._normalize_entities_cargo({"ok": 1, "entities": "nope"}),
            {"ok": 1, "entities": "nope"},
        )
        mixed = server._normalize_entities_cargo({"ok": 1, "entities": [None, 7, dict(ROW_OLD)]})
        self.assertEqual(mixed["entities"][:2], [None, 7])
        self.assertIsNone(mixed["entities"][2]["has_cargo"])
        self.assertEqual(server._normalize_entities_cargo("not a dict"), "not a dict")


class EntitiesQueryCargoWireTest(unittest.IsolatedAsyncioTestCase):
    """Through the public tool, with a bridge that states the field and one that does not."""

    def _app(self):
        return build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))

    async def test_new_bridge_rows_reach_the_caller_as_bools(self) -> None:
        app, runtime = self._app()
        rows = [ROW_NEW_CONTAINER, ROW_NEW_PLAIN]
        with patch.object(runtime, "call_bridge", _bridge_returning(rows)):
            result = _content_json(
                await app.call_tool(
                    "entities_query", {"pos": [100.0, 10.0, 200.0], "radius": 20.0}
                )
            )
        flags = [row["has_cargo"] for row in result["entities"]]
        # IDENTITY, not equality: 1 == True in Python, so assertEqual would go green
        # against the raw ints the bridge sends and certify a normalisation that never
        # ran. What the caller must receive is a JSON true, not a 1.
        self.assertIs(flags[0], True)
        self.assertIs(flags[1], False)
        # The field is additive: order, cardinality and the uncut count stand.
        self.assertEqual(
            [row["type"] for row in result["entities"]], ["SeaChest", "Land_Rock_1"]
        )
        self.assertEqual(result["count_total"], 2)
        self.assertEqual(result["reliability"], "player_in_bubble")

    async def test_old_bridge_rows_reach_the_caller_as_null(self) -> None:
        app, runtime = self._app()
        with patch.object(runtime, "call_bridge", _bridge_returning([ROW_OLD])):
            result = _content_json(
                await app.call_tool(
                    "entities_query", {"pos": [100.0, 10.0, 200.0], "radius": 20.0}
                )
            )
        row = result["entities"][0]
        self.assertIn("has_cargo", row)
        self.assertIsNone(row["has_cargo"])
        self.assertEqual(row["type"], "SeaChest")
        self.assertEqual(result["count_total"], 1)

    async def test_a_mixed_answer_keeps_the_three_verdicts_apart(self) -> None:
        # One response carrying all three: a container, a denial and a silence. The
        # positive sustains the negative -- all-false would be indistinguishable from
        # an old bridge whose key never travelled.
        app, runtime = self._app()
        rows = [ROW_NEW_CONTAINER, ROW_NEW_PLAIN, ROW_OLD]
        with patch.object(runtime, "call_bridge", _bridge_returning(rows)):
            result = _content_json(
                await app.call_tool(
                    "entities_query", {"pos": [100.0, 10.0, 200.0], "radius": 20.0}
                )
            )
        flags = [row["has_cargo"] for row in result["entities"]]
        self.assertIs(flags[0], True)
        self.assertIs(flags[1], False)
        self.assertIsNone(flags[2])


class EntitiesQueryDescriptionTest(unittest.IsolatedAsyncioTestCase):
    """The field travels on the wire; the published surface has to say so."""

    async def test_description_declares_has_cargo_as_capacity(self) -> None:
        app, _runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        desc = tools["entities_query"].description or ""
        lowered = desc.lower()
        self.assertIn("type, classname, has_cargo, pos, distance", desc)
        self.assertIn("capacity", lowered)
        # The distinguishing word must appear as a DENIAL, so a description that
        # promised occupancy could not pass by accident.
        self.assertIn("not occupancy", lowered)
        self.assertIn("empty", lowered)
        self.assertIn("proxy", lowered)
        # And the tolerance has to be stated, or a consumer reads null as false.
        self.assertIn("null", lowered)


if __name__ == "__main__":
    unittest.main()
