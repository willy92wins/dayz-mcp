from __future__ import annotations

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
    }
    empty.update(filled)
    return {"ok": 1, "cmd": "fixture", **empty}


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
        self.assertEqual(SEMANTIC_EMPTY_FIELDS, frozenset({("query_all_players", "players")}))

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

    def test_unknown_keys_and_non_dict_results_pass_through(self) -> None:
        result = _wire_result()
        result["future_field"] = []
        pruned = prune_unfilled_fields("world_spawn", result)
        self.assertEqual(pruned["future_field"], [])
        self.assertEqual(result_prune.prune_unfilled_fields("x", None), None)
        self.assertEqual(result_prune.prune_unfilled_fields("x", []), [])

    def test_prunable_fields_match_the_bridge_result_class(self) -> None:
        # Anchored to MCPMessages.c:280-289. If the Enforce class gains a ref
        # member, this fails and forces a decision instead of silent drift.
        self.assertEqual(
            PRUNABLE_FIELDS,
            (
                "state",
                "players",
                "raycast",
                "telemetry",
                "camera",
                "applied",
                "get_in",
                "trace",
                "pos_real",
                "normal",
                "inspect",
            ),
        )


if __name__ == "__main__":
    unittest.main()
