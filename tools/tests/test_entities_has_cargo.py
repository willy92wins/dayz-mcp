"""Offline structural gate for ``entities_query.has_cargo`` (ficha 40e4, module M03).

C3 asks for cargo *capacity*, not occupancy: an empty container answers
``true``, an EntityAI without a cargo grid answers ``false``, and an Object that
is not an EntityAI answers ``false`` instead of raising. The bridge computes the
flag in ``HasCargoCapacity`` and copies it onto every ``MCPEntityHit`` row inside
``DispatchEntitiesQuery`` before the row is collected, so ordering, ``limit``
and ``count_total`` are untouched.

Verified here, on comment-stripped source:

* the DTO ``MCPEntityHit`` declares ``bool has_cargo`` (M02 contract, read only);
* the helper casts to ``EntityAI``, keeps an explicit ``GameInventory`` guard
  before the single ``GetCargo()`` call and answers ``GetCargo() != null``;
* neither the helper nor the dispatcher consults item counts, ``HasAnyCargo``,
  classname comparisons, string lists, or the proxy cargo array;
* the radius/limit bounds, ``count_total`` and ``TakeNearestEntities`` stay as
  they were, and the sorter never reads ``has_cargo``.

Not verified here: the four in-game cases (Object that is not an EntityAI,
EntityAI without cargo, empty container, occupied container) and the survival
of ``"has_cargo": false`` on the wire. Both need a running server.
"""

from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"

DISPATCH_SIGNATURE = "protected bool DispatchEntitiesQuery(MCPCommand command, MCPResult result)"
HELPER_SIGNATURE = "protected bool HasCargoCapacity(Object found)"
SORTER_SIGNATURE = (
    "protected array<ref MCPEntityHit> TakeNearestEntities(array<ref MCPEntityHit> collected, int limit)"
)
LOOP_SIGNATURE = "while (i < m_ReadyObjects.Count())"

ROW_NEW = "MCPEntityHit entry = new MCPEntityHit();"
ROW_HAS_CARGO = "entry.has_cargo = HasCargoCapacity(found);"
ROW_INSERT = "collected.Insert(entry);"
COUNT_TOTAL = "result.count_total = collected.Count();"
ENTITIES_CUT = "result.entities = TakeNearestEntities(collected, limit);"
LIMIT_BOUND = "if (limit <= 0 || limit > 128)"
RADIUS_BOUND = "command.args.radius > 200.0"

HELPER_CAST = "EntityAI entity = EntityAI.Cast(found);"
HELPER_ENTITY_GUARD = "if (!entity)"
HELPER_INVENTORY = "GameInventory inventory = entity.GetInventory();"
HELPER_INVENTORY_GUARD = "if (!inventory)"
HELPER_ANSWER = "return inventory.GetCargo() != null;"

# Occupancy, classname and list idioms that would turn capacity into something else.
FORBIDDEN_IN_DISPATCH = (
    "has_cargo = true",
    "has_cargo = false",
    "HasAnyCargo",
    "GetItemCount",
    "m_ReadyProxyCargos.Get",
    "ClassName() ==",
    "GetType() ==",
    "array<string>",
)
FORBIDDEN_IN_HELPER = (
    "HasAnyCargo",
    "GetItemCount",
    "GetCargoFromIndex",
    "ClassName",
    "GetType",
    "array<string>",
    "m_ReadyProxyCargos",
    "return true;",
    '"',
)

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_HAS_CARGO_MEMBER = re.compile(r"\bbool\s+has_cargo\s*;")


def _without_comments(source: str) -> str:
    """Blank every comment, keeping strings verbatim and newlines in place."""

    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        if text.startswith('"'):
            return text
        return re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _block_at(text: str, pos: int) -> tuple[str, int]:
    pos = _skip_ws(text, pos)
    if pos >= len(text) or text[pos] != "{":
        raise AssertionError(f"expected a brace block at offset {pos}: {text[pos:pos + 40]!r}")
    depth = 0
    for index in range(pos, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[pos + 1 : index], index + 1
    raise AssertionError("unbalanced brace block")


def _body(clean: str, signature: str) -> str:
    count = clean.count(signature)
    if count != 1:
        raise AssertionError(f"{signature!r} occurs {count} times, expected exactly once")
    body, _ = _block_at(clean, clean.index(signature) + len(signature))
    return body


def _class_body(clean: str, class_name: str) -> str:
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", clean)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    body, _ = _block_at(clean, match.end())
    return body


def _once(test: unittest.TestCase, haystack: str, needle: str, where: str) -> int:
    test.assertEqual(haystack.count(needle), 1, f"{needle!r} must occur exactly once in {where}")
    return haystack.index(needle)


def _assert_dto(test: unittest.TestCase, messages: str) -> None:
    body = _class_body(_without_comments(messages), "MCPEntityHit")
    test.assertEqual(len(_HAS_CARGO_MEMBER.findall(body)), 1, "MCPEntityHit must declare bool has_cargo once")


def _assert_dispatch(test: unittest.TestCase, clean: str) -> None:
    dispatch = _body(clean, DISPATCH_SIGNATURE)
    for needle in FORBIDDEN_IN_DISPATCH:
        test.assertNotIn(needle, dispatch, f"{needle!r} must not appear in DispatchEntitiesQuery")
    test.assertEqual(dispatch.count("has_cargo"), 1, "has_cargo is written exactly once per row")

    loop = _body(dispatch, LOOP_SIGNATURE)
    new_at = _once(test, loop, ROW_NEW, "the collection loop")
    cargo_at = _once(test, loop, ROW_HAS_CARGO, "the collection loop")
    insert_at = _once(test, loop, ROW_INSERT, "the collection loop")
    test.assertLess(new_at, cargo_at, "has_cargo is written after the row is created")
    test.assertLess(cargo_at, insert_at, "has_cargo is written before the row is collected")

    radius_at = _once(test, dispatch, RADIUS_BOUND, "DispatchEntitiesQuery")
    limit_at = _once(test, dispatch, LIMIT_BOUND, "DispatchEntitiesQuery")
    count_at = _once(test, dispatch, COUNT_TOTAL, "DispatchEntitiesQuery")
    cut_at = _once(test, dispatch, ENTITIES_CUT, "DispatchEntitiesQuery")
    loop_at = dispatch.index(LOOP_SIGNATURE)
    test.assertLess(radius_at, limit_at)
    test.assertLess(limit_at, loop_at)
    test.assertLess(loop_at, count_at)
    test.assertLess(count_at, cut_at, "count_total is the uncut size, taken before the cut")


def _assert_helper(test: unittest.TestCase, clean: str) -> None:
    helper = _body(clean, HELPER_SIGNATURE)
    for needle in FORBIDDEN_IN_HELPER:
        test.assertNotIn(needle, helper, f"{needle!r} must not appear in HasCargoCapacity")

    cast_at = _once(test, helper, HELPER_CAST, "HasCargoCapacity")
    entity_guard_at = _once(test, helper, HELPER_ENTITY_GUARD, "HasCargoCapacity")
    inventory_at = _once(test, helper, HELPER_INVENTORY, "HasCargoCapacity")
    inventory_guard_at = _once(test, helper, HELPER_INVENTORY_GUARD, "HasCargoCapacity")
    answer_at = _once(test, helper, HELPER_ANSWER, "HasCargoCapacity")
    test.assertEqual(helper.count("GetCargo()"), 1, "GetCargo is called exactly once, after the guard")
    test.assertEqual(helper.count("GetInventory()"), 1, "GetInventory is called exactly once, on the cast entity")
    test.assertEqual(helper.count("return"), 3, "two guarded false returns and one capacity answer")
    test.assertLess(cast_at, entity_guard_at)
    test.assertLess(entity_guard_at, inventory_at)
    test.assertLess(inventory_at, inventory_guard_at)
    test.assertLess(inventory_guard_at, answer_at)

    entity_guard, _ = _block_at(helper, entity_guard_at + len(HELPER_ENTITY_GUARD))
    inventory_guard, _ = _block_at(helper, inventory_guard_at + len(HELPER_INVENTORY_GUARD))
    test.assertEqual(entity_guard.strip(), "return false;")
    test.assertEqual(inventory_guard.strip(), "return false;")


def _assert_sorter(test: unittest.TestCase, clean: str) -> None:
    sorter = _body(clean, SORTER_SIGNATURE)
    test.assertNotIn("has_cargo", sorter, "the nearest-first cut must not read has_cargo")


def _assert_contract(test: unittest.TestCase, bridge: str, messages: str) -> None:
    _assert_dto(test, messages)
    clean = _without_comments(bridge)
    _assert_dispatch(test, clean)
    _assert_helper(test, clean)
    _assert_sorter(test, clean)


def _known_good_messages() -> str:
    return '''class MCPEntityHit
{
	string type;
	string classname;
	bool has_cargo;
	ref array<float> pos;
	float distance;

	void MCPEntityHit()
	{
		pos = new array<float>();
	}
};
'''


def _known_good_bridge() -> str:
    return '''class MCPBridge
{
	protected ref array<Object> m_ReadyObjects;
	protected ref array<CargoBase> m_ReadyProxyCargos;

	// Raw nearby objects via GetObjectsAtPosition3D. No classname filter.
	// has_cargo reports cargo capacity (HasCargoCapacity), never occupancy.
	protected bool DispatchEntitiesQuery(MCPCommand command, MCPResult result)
	{
		MCPSpawnValidation validation = ValidatePositionArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		if (!command.args || command.args.radius <= 0.0 || !IsFiniteFloat(command.args.radius) || command.args.radius > 200.0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		int limit = command.args.limit;
		if (limit <= 0 || limit > 128)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(validation.pos, command.args.radius, m_ReadyObjects, m_ReadyProxyCargos);

		array<ref MCPEntityHit> collected = new array<ref MCPEntityHit>();
		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			if (found)
			{
				MCPEntityHit entry = new MCPEntityHit();
				vector foundPos = found.GetPosition();
				entry.type = found.GetType();
				entry.classname = found.ClassName();
				entry.has_cargo = HasCargoCapacity(found);
				VectorToArray(foundPos, entry.pos);
				entry.distance = vector.Distance(validation.pos, foundPos);
				collected.Insert(entry);
			}

			i = i + 1;
		}

		result.count_total = collected.Count();
		result.entities = TakeNearestEntities(collected, limit);
		result.ok = true;
		return true;
	}

	// C3: cargo capacity, not occupancy.
	protected bool HasCargoCapacity(Object found)
	{
		EntityAI entity = EntityAI.Cast(found);
		if (!entity)
		{
			return false;
		}

		GameInventory inventory = entity.GetInventory();
		if (!inventory)
		{
			return false;
		}

		return inventory.GetCargo() != null;
	}

	protected array<ref MCPEntityHit> TakeNearestEntities(array<ref MCPEntityHit> collected, int limit)
	{
		array<ref MCPEntityHit> nearest = new array<ref MCPEntityHit>();
		return nearest;
	}
};
'''


class EntitiesHasCargoStructuralTest(unittest.TestCase):
    def test_known_good_fixture_satisfies_the_contract(self) -> None:
        _assert_contract(self, _known_good_bridge(), _known_good_messages())

    def test_live_bridge_fills_has_cargo_as_capacity(self) -> None:
        _assert_contract(
            self,
            BRIDGE_PATH.read_text(encoding="utf-8"),
            MESSAGES_PATH.read_text(encoding="utf-8"),
        )

    def test_comment_mentions_of_forbidden_idioms_do_not_count(self) -> None:
        bridge = _known_good_bridge().replace(
            "\t\tEntityAI entity = EntityAI.Cast(found);",
            '\t\t// HasAnyCargo and GetItemCount measure occupancy; "WoodenCrate" lists are not consulted.\n'
            "\t\t/* GetType() == ClassName() == array<string> */\n"
            "\t\tEntityAI entity = EntityAI.Cast(found);",
            1,
        )
        _assert_contract(self, bridge, _known_good_messages())

    def test_bridge_mutants_are_rejected(self) -> None:
        bridge = _known_good_bridge()
        messages = _known_good_messages()
        row_line = "\t\t\t\tentry.has_cargo = HasCargoCapacity(found);\n"
        insert_line = "\t\t\t\tcollected.Insert(entry);\n"
        cast_block = (
            "\t\tEntityAI entity = EntityAI.Cast(found);\n"
            "\t\tif (!entity)\n"
            "\t\t{\n"
            "\t\t\treturn false;\n"
            "\t\t}\n"
            "\n"
            "\t\tGameInventory inventory = entity.GetInventory();\n"
        )
        inventory_guard = (
            "\t\tif (!inventory)\n"
            "\t\t{\n"
            "\t\t\treturn false;\n"
            "\t\t}\n"
            "\n"
        )
        answer_line = "\t\treturn inventory.GetCargo() != null;\n"
        count_line = "\t\tresult.count_total = collected.Count();\n"
        sorter_stub = "\t\tarray<ref MCPEntityHit> nearest = new array<ref MCPEntityHit>();\n"
        for needle in (row_line, insert_line, cast_block, inventory_guard, answer_line, count_line, sorter_stub):
            self.assertEqual(bridge.count(needle), 1, needle)
        mutants = {
            "row_never_assigned": bridge.replace(row_line, ""),
            "row_assigned_literal_true": bridge.replace(row_line, "\t\t\t\tentry.has_cargo = true;\n"),
            "row_assigned_literal_false": bridge.replace(row_line, "\t\t\t\tentry.has_cargo = false;\n"),
            "row_assigned_twice": bridge.replace(row_line, row_line + row_line),
            "row_assigned_after_insert": bridge.replace(row_line, "").replace(insert_line, insert_line + row_line),
            "row_assigned_outside_loop": bridge.replace(row_line, "").replace(count_line, row_line + count_line),
            "row_from_classname": bridge.replace(row_line, '\t\t\t\tentry.has_cargo = found.ClassName() == "SeaChest";\n'),
            "row_from_proxy_cargo": bridge.replace(row_line, "\t\t\t\tentry.has_cargo = m_ReadyProxyCargos.Get(i) != null;\n"),
            "helper_absent": bridge.replace(HELPER_SIGNATURE, "protected bool HasCargoSomething(Object found)"),
            "helper_uses_has_any_cargo": bridge.replace(answer_line, "\t\treturn entity.HasAnyCargo();\n"),
            "helper_counts_items": bridge.replace(answer_line, "\t\treturn inventory.GetCargo().GetItemCount() > 0;\n"),
            "helper_without_entity_cast": bridge.replace(cast_block, "\t\tGameInventory inventory = found.GetInventory();\n"),
            "helper_without_inventory_guard": bridge.replace(inventory_guard, ""),
            "helper_guard_after_get_cargo": bridge.replace(
                inventory_guard + answer_line,
                "\t\tCargoBase cargo = inventory.GetCargo();\n" + inventory_guard + "\t\treturn cargo != null;\n",
            ),
            "helper_guard_returns_true": bridge.replace(inventory_guard, inventory_guard.replace("return false;", "return true;")),
            "helper_classname_list": bridge.replace(answer_line, '\t\treturn found.GetType() == "SeaChest";\n'),
            "helper_proxy_cargo": bridge.replace(answer_line, "\t\treturn m_ReadyProxyCargos.Count() > 0;\n"),
            "helper_second_get_cargo": bridge.replace(answer_line, "\t\tinventory.GetCargo();\n" + answer_line),
            "count_total_dropped": bridge.replace(count_line, ""),
            "count_total_after_cut": bridge.replace(count_line, "").replace(
                "\t\tresult.entities = TakeNearestEntities(collected, limit);\n",
                "\t\tresult.entities = TakeNearestEntities(collected, limit);\n" + count_line,
            ),
            "limit_bound_changed": bridge.replace("limit > 128", "limit > 64"),
            "radius_bound_changed": bridge.replace("radius > 200.0", "radius > 100.0"),
            "sorter_reads_has_cargo": bridge.replace(
                sorter_stub, sorter_stub + "\t\tif (collected.Count() > 0 && collected.Get(0).has_cargo)\n\t\t{\n\t\t}\n"
            ),
        }
        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                self.assertNotEqual(mutant, bridge, "mutant did not change the fixture")
                with self.assertRaises(AssertionError):
                    _assert_contract(self, mutant, messages)

    def test_dto_mutants_are_rejected(self) -> None:
        bridge = _known_good_bridge()
        messages = _known_good_messages()
        mutants = {
            "dto_has_cargo_absent": messages.replace("\tbool has_cargo;\n", ""),
            "dto_has_cargo_wrong_type": messages.replace("bool has_cargo;", "string has_cargo;"),
            "dto_has_cargo_commented_out": messages.replace("\tbool has_cargo;", "\t// bool has_cargo;"),
            "dto_wrong_class": messages.replace("class MCPEntityHit", "class MCPOtherHit"),
        }
        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                self.assertNotEqual(mutant, messages, "mutant did not change the fixture")
                with self.assertRaises(AssertionError):
                    _assert_contract(self, bridge, mutant)


if __name__ == "__main__":
    unittest.main()
