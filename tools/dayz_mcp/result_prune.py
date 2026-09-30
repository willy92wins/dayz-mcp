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
   The test is whether the consumer reads the container as the answer.
   `query_all_players` has no scalar beside `players`: prune its array and
   the answer is gone with it. `entities_query` does report `count_total`,
   but its description promises `entities: []` and an agent indexes by
   that key (ficha 59d9), so the empty list stays too.

Rule 1 has one exception it does not cover: a scalar with ONE known owner.
`door_index` and `component_index` are filled only by `action_use_door`, yet
the flat class sends them on every verb, so a plain `action_use` reported
`component_index: 0` although its target used -1 (#156 R1 F2). Outside their
owner their 0 is always the unassigned default, never an answer, so they are
dropped there; for the owner they are kept whatever the value, because door 0
and component 0 are real. Such fields live in OWNED_SCALAR_FIELDS.

This is an observable contract change for a published server: see the changelog.
"""
from __future__ import annotations

from typing import Any


# The `ref` members of MCPResult, in declaration order (MCPMessages.c MCPResult).
# Scalars (y, phase, classname, source, ...) are never pruned — 0/"" is real data —
# except the owned scalars of OWNED_SCALAR_FIELDS on a command that does not own them.
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
    "inventory_attach",
    "inspect",
    "entities",
    "ui",
    "ui_request",
    "dialog",
    "weapon_state",
    # Nested probe (ficha 4f50) lives inside this object. prune does not
    # recurse, so the nested object stays with or without fields.
    "input_describe",
    "building_doors",
    "weapon_action",
    "timeline",
    "vehicle_door",
    # world_spawn fills it only when the command carried lifetime_s.
    "lifetime",
    # input_trigger fills it, also on its refusals (e1ae part 2).
    "input_trigger",
    # world_time_get fills it with the World.GetDate read.
    "world_time",
)

# (command, field) pairs where an EMPTY container is the real answer.
SEMANTIC_EMPTY_FIELDS = frozenset(
    {
        # Verified in-game 2026-07-29: empty array is success, not absence.
        ("query_all_players", "players"),
        # ui_dialog answers inside `dialog`; an empty object there is a bridge
        # defect the caller must see, not noise to hide.
        ("ui_dialog", "dialog"),
        # entities_query publishes "Absent entities travel as []" (server.py)
        # and its consumer indexes result["entities"]: the empty list is the
        # answer, not noise. Ficha 59d9 (2026-09-04): the key vanished on
        # every empty query and the description described what never came.
        ("entities_query", "entities"),
    }
)

# Scalar field -> the one bridge command that fills it. The key is dropped from
# every other command's result and kept, even at 0, for its owner. The owner is
# the command name call_bridge sends: action_use with door_index goes out as
# action_use_door (server.py action_use), and that name reaches this module.
OWNED_SCALAR_FIELDS = {
    "door_index": "action_use_door",
    "component_index": "action_use_door",
}


def _is_empty_container(value: Any) -> bool:
    # `null` for a ref member means the same as {} / []: the verb never filled it.
    # The bridge does not emit null for a filled ref (players null arrives as []).
    if value is None:
        return True
    return (isinstance(value, (list, dict))) and not value


def _owned_by_another_command(command: str, key: str) -> bool:
    owner = OWNED_SCALAR_FIELDS.get(key)
    return owner is not None and owner != command


def prune_unfilled_fields(command: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return `result` without the reference fields this verb never filled.

    Non-empty values, scalars and unknown keys are returned untouched, so a verb
    gaining a field later needs no change here. The exception is an owned scalar
    (OWNED_SCALAR_FIELDS), which only its owning command keeps.
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
        and not _owned_by_another_command(command, key)
    }
    return pruned
