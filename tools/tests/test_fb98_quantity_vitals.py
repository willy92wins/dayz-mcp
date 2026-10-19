"""object_inspect reads an item's quantity, query_player_state water and energy (inbox fb98).

action_hold could not show what a drink or an eat consumed: on 1.29 a quantity
action counts a cycle only when the item runs out, and no verb read an item's
quantity or the player's water and energy without changing them. The quantity
read, FillInspectQuantity, is translated and run against fakes
(tests/enforce_subset_helpers.py); the dispatch branch, the messages and the
query fields are text contracts; the Python side is the published descriptions.
"""
from __future__ import annotations

import functools
import re
import unittest
from types import SimpleNamespace
from typing import Any

from dayz_mcp.effective_schema import resolve_effective_schemas
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import method_body, translate

SCRIPTS = addon_root() / "scripts" / "5_Mission"
BRIDGE = (SCRIPTS / "MCPBridge.c").read_text(encoding="utf-8")
MESSAGES = (SCRIPTS / "MCPMessages.c").read_text(encoding="utf-8")
FILL_QUANTITY = "protected void FillInspectQuantity(Object match, MCPObjectInspect inspect)"
DISPATCH_INSPECT = "protected bool DispatchObjectInspect(MCPCommand command, MCPResult result)"
BUILD_STATE = "protected MCPPlayerState BuildPlayerState()"
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)


class _Item:
    """An ItemBase that answers the three reads and refuses anything else."""

    def __init__(self, has: bool, quantity: float = 0.0, maximum: int = 0) -> None:
        self._has = has
        self._quantity = quantity
        self._maximum = maximum
        self.asked: list[str] = []

    def HasQuantity(self) -> bool:
        self.asked.append("HasQuantity")
        return self._has

    def GetQuantity(self) -> float:
        self.asked.append("GetQuantity")
        return self._quantity

    def GetQuantityMax(self) -> int:
        self.asked.append("GetQuantityMax")
        return self._maximum

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"{name} is not one of the reads")


class _ItemBase:
    @staticmethod
    def Cast(value: object) -> _Item | None:
        return value if isinstance(value, _Item) else None


def _inspected(match: object) -> SimpleNamespace:
    inspect = SimpleNamespace(has_quantity=False, quantity=0.0, quantity_max=0.0)
    fill = translate(BRIDGE, FILL_QUANTITY, {"ItemBase": _ItemBase}, class_types=("ItemBase",))
    fill(match, inspect)
    return inspect


@functools.lru_cache(maxsize=1)
def _published() -> dict[str, dict]:
    return resolve_effective_schemas()


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    body = method_body(source, f"class {class_name}")
    return [(item.group("type"), item.group("name")) for item in _MEMBER_RE.finditer(body)]


def _in_order(test: unittest.TestCase, body: str, *tokens: str) -> None:
    positions = []
    for token in tokens:
        test.assertEqual(body.count(token), 1, token)
        positions.append(body.index(token))
    test.assertEqual(positions, sorted(positions), tokens)


class InspectQuantityTest(unittest.TestCase):
    def test_an_item_with_a_quantity_is_read_and_not_changed(self) -> None:
        bottle = _Item(True, 350.0, 1000)
        inspect = _inspected(bottle)
        self.assertEqual((inspect.has_quantity, inspect.quantity, inspect.quantity_max), (True, 350.0, 1000))
        self.assertEqual(bottle.asked, ["HasQuantity", "GetQuantity", "GetQuantityMax"])

    def test_anything_else_stays_false_and_zero(self) -> None:
        empty = _Item(False, 350.0, 1000)
        for label, match in (("item without a quantity", empty), ("not an item", object())):
            with self.subTest(match=label):
                inspect = _inspected(match)
                self.assertEqual((inspect.has_quantity, inspect.quantity, inspect.quantity_max), (False, 0.0, 0.0))
        self.assertEqual(empty.asked, ["HasQuantity"])

    def test_quantity_is_its_own_want_and_not_a_memory_point(self) -> None:
        body = method_body(BRIDGE, DISPATCH_INSPECT)
        _in_order(
            self,
            body,
            'if (wantName == "bounding_center")',
            'else if (wantName == "quantity")',
            "FillInspectQuantity(match, inspect);",
            "MCPMemoryPoint point = new MCPMemoryPoint();",
        )

    def test_the_inspect_answer_carries_the_three_fields(self) -> None:
        self.assertEqual(
            _class_members(MESSAGES, "MCPObjectInspect"),
            [
                ("string", "type"),
                ("ref array<float>", "bounding_center"),
                ("bool", "has_bounding_center"),
                ("ref array<ref MCPMemoryPoint>", "memory_points"),
                ("bool", "has_quantity"),
                ("float", "quantity"),
                ("float", "quantity_max"),
            ],
        )
        self.assertIn("has_quantity = false;", method_body(MESSAGES, "void MCPObjectInspect()"))


class PlayerStateVitalsTest(unittest.TestCase):
    def test_the_player_state_reads_water_and_energy_as_player_heal(self) -> None:
        # Its members are checked with the rest of query_player_state in
        # tests/test_player_heal_godmode.py.
        _in_order(
            self,
            method_body(BRIDGE, BUILD_STATE),
            "state.godmode = MCPGodmode.IsOn(player);",
            "PlayerBase body = PlayerBase.Cast(player);",
            "if (body)",
            "FillPlayerVitals(body, vitals);",
            "state.water = vitals.water;",
            "state.energy = vitals.energy;",
            "return state;",
        )


class DescriptionsTest(unittest.TestCase):
    def test_each_tool_says_what_it_reads(self) -> None:
        for tool, sentence in (
            ("object_inspect", "want 'quantity' reads an item's quantity without changing it"),
            ("object_inspect", "an older PBO has no has_quantity"),
            ("query_player_state", "water and energy are the body's stats, 0 to 5000, as player_heal reads them"),
            (
                "action_hold",
                "On 1.29 a quantity action such as drink or eat counts a cycle only when the item runs out, "
                "so completed_cycles stays 0 while it consumes",
            ),
            ("action_hold", "read the item with object_inspect(want=['quantity'])"),
        ):
            with self.subTest(tool=tool, sentence=sentence):
                self.assertIn(sentence, _published()[tool]["description"])


if __name__ == "__main__":
    unittest.main()
