"""Drop reference fields the bridge never filled in (F2.2).

MCPResult (`DayZ_MCP\\scripts\\5_Mission\\MCPMessages.c:275-310`) is ONE flat
class shared by every verb, so each dispatch fills only its own members and the
Enforce serializer emits the rest as `[]` / `{}`. The consumer therefore cannot
tell "this verb reports zero players" from "this verb does not report players at
all" -- the exact ambiguity recorded against GameMaster in the 2026-07-29 handoff.

Two deliberate limits:

1. Only the `ref` members are prunable. A `bool false`, an `int 0` or an empty
   `string` is INDISTINGUISHABLE from "never assigned", so pruning scalars would
   silently delete real answers (`deleted:0`, `found:false`, `seated:false`).
2. An empty container is not always noise. `query_all_players` returning
   `players: []` is a verified success (in-game 2026-07-29) and must survive.
   Such pairs live in SEMANTIC_EMPTY_FIELDS and are never pruned.
   The test is whether the container is the verb's ONLY answer.
   `entities_query` reports `count_total` beside `entities`, and an int
   survives pruning, so an empty result still says so. `query_all_players`
   has no such scalar: prune its array and the answer is gone with it.

This is an observable contract change for a published server: see the changelog.
"""
from __future__ import annotations

from typing import Any


# The `ref` members of MCPResult, in declaration order (MCPMessages.c MCPResult).
# Scalars (y, phase, classname, source, ...) are never pruned — 0/"" is real data.
PRUNABLE_FIELDS = (
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
    "entities",
    "ui",
    "dialog",
)

# (command, field) pairs where an EMPTY container is the real answer.
SEMANTIC_EMPTY_FIELDS = frozenset(
    {
        # Verified in-game 2026-07-29: empty array is success, not absence.
        ("query_all_players", "players"),
        # ui_dialog answers inside `dialog`; an empty object there is a bridge
        # defect the caller must see, not noise to hide.
        ("ui_dialog", "dialog"),
    }
)


def _is_empty_container(value: Any) -> bool:
    # `null` for a ref member means the same as {} / []: the verb never filled it.
    # The bridge does not emit null for a filled ref (players null arrives as []).
    if value is None:
        return True
    return (isinstance(value, (list, dict))) and not value


def prune_unfilled_fields(command: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return `result` without the reference fields this verb never filled.

    Non-empty values, scalars and unknown keys are returned untouched, so a verb
    gaining a field later needs no change here.
    """
    if not isinstance(result, dict):
        return result
    pruned = {
        key: value
        for key, value in result.items()
        if not (
            key in PRUNABLE_FIELDS
            and _is_empty_container(value)
            and (command, key) not in SEMANTIC_EMPTY_FIELDS
        )
    }
    return pruned
