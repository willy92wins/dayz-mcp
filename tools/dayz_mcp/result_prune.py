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

Rule 1 has one exception it does not cover: a scalar with known owners.
`door_index` and `component_index` are filled only by `action_use_door`, yet
the flat class sends them on every verb, so a plain `action_use` reported
`component_index: 0` although its target used -1 (#156 R1 F2). Outside their
owners their 0 is always the unassigned default, never an answer, so they are
dropped there; for an owner they are kept whatever the value, because door 0
and component 0 are real. Such fields live in OWNED_SCALAR_FIELDS.

The vehicle scalars are the same case (fb-20260823-130809-a412): `vehicle_control`
answered `seated=0, gear=0, is_owner=0` while the car it drove was in gear 2 and
owned, because it fills only `engine_on_server`. Each vehicle scalar is kept for
the commands whose dispatch writes it and dropped from every other result.
tests/test_owned_scalar_census.py reads both bridges and fails when a command
writes an owned field without being one of its owners, or is listed without
writing it.

`pos_delta` is declared in MCPResult and assigned by no command, so it is always
the unassigned 0.0 (fb-20260823-141958-dde3) and is dropped from every result:
NEVER_FILLED_SCALAR_FIELDS.

This is an observable contract change for a published server: see the changelog.
"""
from __future__ import annotations

from typing import Any


# The `ref` members of MCPResult, in declaration order (MCPMessages.c MCPResult).
# Scalars (y, phase, classname, source, ...) are never pruned — 0/"" is real data —
# except the owned scalars of OWNED_SCALAR_FIELDS on a command that does not own
# them and the never-filled scalars of NEVER_FILLED_SCALAR_FIELDS.
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

# Scalar field -> the bridge commands whose dispatch writes it. The key is dropped
# from every other command's result and kept, even at 0, for its owners. An owner
# is the command name call_bridge sends: action_use with door_index goes out as
# action_use_door (server.py action_use), and that name reaches this module.
# The sets come from the assignments in MCPBridge.c and MCPClientBridge.c; a
# field whose owners could not be established exactly is left out, because
# dropping a real answer is worse than a stale 0.
OWNED_SCALAR_FIELDS: dict[str, frozenset[str]] = {
    "door_index": frozenset({"action_use_door"}),
    "component_index": frozenset({"action_use_door"}),
    # vehicle_enter answers from its seat job (PostSeatSuccess), vehicle_get_in_client
    # from its vehicle_get_in job (MCP_PostJobSuccess).
    "seated": frozenset({"vehicle_enter", "vehicle_get_in_client", "vehicle_telemetry"}),
    "seat": frozenset({"vehicle_enter", "vehicle_get_in_client", "vehicle_telemetry"}),
    "vehicle_fixture_ready": frozenset({"vehicle_prepare_fixture", "vehicle_get_in_client"}),
    "engine_on_server": frozenset({"engine_set", "vehicle_control", "vehicle_telemetry"}),
    "speedo_max": frozenset({"vehicle_telemetry"}),
    "gear": frozenset({"vehicle_telemetry"}),
    "net_strategy": frozenset({"vehicle_get_in_client", "vehicle_telemetry"}),
    "is_owner": frozenset({"vehicle_get_in_client", "vehicle_telemetry"}),
    "is_authority_owner": frozenset({"vehicle_get_in_client", "vehicle_telemetry"}),
    "owner_identity": frozenset({"vehicle_get_in_client", "vehicle_telemetry"}),
    "net_id_low": frozenset({"vehicle_get_in_client", "vehicle_telemetry"}),
    "net_id_high": frozenset({"vehicle_get_in_client", "vehicle_telemetry"}),
}

# Scalars MCPResult declares and no command assigns (dde3). Every result carries
# the unassigned default, which is never an answer, so the key is always dropped.
# A command that starts writing one moves it to OWNED_SCALAR_FIELDS.
NEVER_FILLED_SCALAR_FIELDS = frozenset({"pos_delta"})


def _is_empty_container(value: Any) -> bool:
    # `null` for a ref member means the same as {} / []: the verb never filled it.
    # The bridge does not emit null for a filled ref (players null arrives as []).
    if value is None:
        return True
    return (isinstance(value, (list, dict))) and not value


def _owned_by_another_command(command: str, key: str) -> bool:
    owners = OWNED_SCALAR_FIELDS.get(key)
    return owners is not None and command not in owners


def prune_unfilled_fields(command: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return `result` without the reference fields this verb never filled.

    Non-empty values, scalars and unknown keys are returned untouched, so a verb
    gaining a field later needs no change here. The exceptions are an owned
    scalar (OWNED_SCALAR_FIELDS), which only its owning commands keep, and a
    never-filled scalar (NEVER_FILLED_SCALAR_FIELDS), which no command keeps.
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
        and key not in NEVER_FILLED_SCALAR_FIELDS
    }
    return pruned
