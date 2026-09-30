from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, result_prune
from dayz_mcp.result_prune import (
    PRUNABLE_FIELDS,
    SEMANTIC_EMPTY_FIELDS,
    prune_unfilled_fields,
)
from tests._addon_paths import addon_root


# A bridge answer as the wire actually carries it: MCPResult is one flat class,
# so every ref member is present even when the verb filled none of them.
def _wire_result(**filled: object) -> dict[str, object]:
    empty: dict[str, object] = {
        "state": {},
        "players": [],
        "raycast": {},
        "telemetry": {},
        "camera": {},
        "applied": {},
        "get_in": {},
        "trace": {},
        "pos_real": [],
        "dialog": {},
        "entities": [],
        "input_describe": {},
        "building_doors": {},
    }
    empty.update(filled)
    return {"ok": 1, "cmd": "fixture", **empty}


# Other classes in MCPMessages.c also declare `ref` members. Only MCPResult
# is the prune contract, so the scan stops at that class's closing brace.
def _mcp_result_ref_members(source: str) -> tuple[str, ...]:
    start = source.index("class MCPResult")
    brace = source.index("{", start)
    depth = 0
    body = ""
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                body = source[brace + 1 : index]
                break
    else:
        raise AssertionError("unterminated class MCPResult")

    names: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        comment = line.find("//")
        if comment >= 0:
            line = line[:comment].rstrip()
        if not line.startswith("ref "):
            continue
        match = re.search(r"(\w+)\s*;\s*$", line)
        if match:
            names.append(match.group(1))
    return tuple(names)


class ResultPruneTest(unittest.TestCase):
    def test_unfilled_reference_fields_are_dropped(self) -> None:
        pruned = prune_unfilled_fields("world_spawn", _wire_result())

        for field in PRUNABLE_FIELDS:
            self.assertNotIn(field, pruned, field)
        self.assertEqual(pruned["ok"], 1)

    def test_filled_fields_survive_for_every_prunable_field(self) -> None:
        # Positive control per field: pruning must never eat a real answer.
        for field in PRUNABLE_FIELDS:
            with self.subTest(field=field):
                value = [1.0] if field in {"pos_real", "players"} else {"a": 1}
                pruned = prune_unfilled_fields("any_command", _wire_result(**{field: value}))
                self.assertEqual(pruned[field], value)
                for other in PRUNABLE_FIELDS:
                    if other != field:
                        self.assertNotIn(other, pruned, other)

    def test_query_all_players_keeps_its_empty_array(self) -> None:
        # The reinforced negative control: empty players IS the success answer
        # for this verb, verified in-game 2026-07-29. Pruning it is a regression.
        pruned = prune_unfilled_fields("query_all_players", _wire_result())

        self.assertIn("players", pruned)
        self.assertEqual(pruned["players"], [])
        # ... while its other unfilled fields still go.
        self.assertNotIn("raycast", pruned)
        self.assertNotIn("telemetry", pruned)

    def test_semantic_exception_is_scoped_to_its_own_command(self) -> None:
        # Same field, different verb -> pruned. Otherwise the exception would be
        # a blanket opt-out and the ambiguity would survive everywhere else.
        pruned = prune_unfilled_fields("query_player_state", _wire_result())
        self.assertNotIn("players", pruned)
        self.assertEqual(
            SEMANTIC_EMPTY_FIELDS,
            frozenset(
                {
                    ("query_all_players", "players"),
                    ("ui_dialog", "dialog"),
                    ("entities_query", "entities"),
                }
            ),
        )

    def test_entities_query_keeps_its_empty_list(self) -> None:
        # Ficha 59d9: the published description says "Absent entities travel
        # as []", and pruning the key made result["entities"] a KeyError on
        # every empty query. count_total survives either way; the list is the
        # contract the description names, and only for this verb.
        pruned = prune_unfilled_fields(
            "entities_query", _wire_result(count_total=0)
        )

        self.assertEqual(pruned["entities"], [])
        self.assertEqual(pruned["count_total"], 0)
        self.assertNotIn("players", pruned)
        self.assertNotIn(
            "entities", prune_unfilled_fields("object_inspect", _wire_result())
        )

    def test_scalars_are_never_pruned_even_when_falsy(self) -> None:
        # False/0/"" are indistinguishable from "never assigned", so they are
        # real answers that must survive: deleted:0 and found:false are results.
        result = _wire_result()
        result.update(
            {"deleted": 0, "found": False, "seated": False, "seat": "", "gear": 0}
        )

        pruned = prune_unfilled_fields("object_delete", result)

        for field in ("deleted", "found", "seated", "seat", "gear"):
            self.assertIn(field, pruned, field)

    def test_every_public_verb_keeps_ok_and_loses_only_empty_refs(self) -> None:
        # H9: one assertion per public verb, driven off the real whitelist so a
        # newly added verb cannot silently skip this contract.
        verbs = loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS
        self.assertGreaterEqual(len(verbs), 20)
        for verb in sorted(verbs):
            with self.subTest(verb=verb):
                pruned = prune_unfilled_fields(verb, _wire_result())
                self.assertEqual(pruned["ok"], 1)
                self.assertEqual(pruned["cmd"], "fixture")
                expected = {
                    field
                    for field in PRUNABLE_FIELDS
                    if (verb, field) in SEMANTIC_EMPTY_FIELDS
                }
                self.assertEqual(
                    {field for field in PRUNABLE_FIELDS if field in pruned}, expected
                )

    def test_door_scalars_are_dropped_from_every_command_but_action_use_door(self) -> None:
        # #156 R1 F2: the flat class sends door_index:0 and component_index:0 on
        # every verb, so a plain action_use read component_index 0 although its
        # target used -1. Driven off the real whitelist like the test above.
        verbs = (loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS) - {
            "action_use_door"
        }
        self.assertIn("action_use", verbs)
        for verb in sorted(verbs):
            with self.subTest(verb=verb):
                pruned = prune_unfilled_fields(
                    verb,
                    _wire_result(
                        door_index=0, component_index=0, started=False, distance=0.0
                    ),
                )
                self.assertNotIn("door_index", pruned)
                self.assertNotIn("component_index", pruned)
                # Every other scalar keeps rule 1.
                self.assertIs(pruned["started"], False)
                self.assertEqual(pruned["distance"], 0.0)

    def test_action_use_door_keeps_door_zero_and_component_zero(self) -> None:
        # Door 0 and component 0 are real, and the action_use tool compares the
        # echoed door_index with the request: pruning it would turn a real door 0
        # into door_not_supported.
        self.assertIn("action_use_door", loopback.CLIENT_COMMANDS)
        for door, component in ((0, 0), (3, 17)):
            with self.subTest(door=door, component=component):
                pruned = prune_unfilled_fields(
                    "action_use_door",
                    _wire_result(door_index=door, component_index=component),
                )
                self.assertEqual(pruned["door_index"], door)
                self.assertEqual(pruned["component_index"], component)
        self.assertEqual(
            result_prune.OWNED_SCALAR_FIELDS,
            {"door_index": "action_use_door", "component_index": "action_use_door"},
        )

    def test_empty_dialog_is_pruned_except_on_ui_dialog(self) -> None:
        empty_object = prune_unfilled_fields(
            "world_spawn", {**_wire_result(), "dialog": {}}
        )
        self.assertNotIn("dialog", empty_object)
        empty_none = prune_unfilled_fields(
            "query_player_state", {**_wire_result(), "dialog": None}
        )
        self.assertNotIn("dialog", empty_none)
        kept_empty = prune_unfilled_fields(
            "ui_dialog", {**_wire_result(), "dialog": {}}
        )
        self.assertEqual(kept_empty["dialog"], {})
        filled = {"state": "completed", "elapsed_s": 1.0}
        kept_filled = prune_unfilled_fields(
            "ui_dialog", {**_wire_result(), "dialog": filled}
        )
        self.assertEqual(kept_filled["dialog"], filled)

    def test_unknown_keys_and_non_dict_results_pass_through(self) -> None:
        result = _wire_result()
        result["future_field"] = []
        pruned = prune_unfilled_fields("world_spawn", result)
        self.assertEqual(pruned["future_field"], [])
        self.assertEqual(result_prune.prune_unfilled_fields("x", None), None)
        self.assertEqual(result_prune.prune_unfilled_fields("x", []), [])

    def test_prunable_fields_match_the_bridge_result_class(self) -> None:
        # Expectation comes from MCPResult, not from a second Python tuple.
        # That is what fails when the Enforce class gains a `ref` member and
        # PRUNABLE_FIELDS is left behind.
        messages = (
            addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
        ).read_text(encoding="utf-8")
        self.assertEqual(PRUNABLE_FIELDS, _mcp_result_ref_members(messages))


if __name__ == "__main__":
    unittest.main()
