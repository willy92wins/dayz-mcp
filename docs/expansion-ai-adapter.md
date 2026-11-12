# Expansion AI adapter (@DayZ_MCP_ExpansionAI)

An optional second PBO that lets the MCP create Expansion AI groups on the
server. It ships in `addon_expansion_ai/` and packs as
`DayZ_MCP_ExpansionAI` (its `$PBOPREFIX$`).

## Build and load

- Build it with the same pack step as the base addon; it produces a separate
  PBO, never part of `DayZ_MCP`.
- Load it alongside `@DayZ_MCP` and DayZ Expansion AI. Its `CfgPatches`
  requires `DayZ_MCP` and `DayZExpansion_AI_Scripts`, so without Expansion
  the game fails closed at load instead of starting half-wired.
- The base PBO (`DayZ_MCP`, bridge v13) carries the five extension
  hooks of the adapter plan (§2.1): `ServerCapabilities`,
  `ExtensionPollParams`, `DispatchExtension`, and the two lease-close hooks
  the v13 close dispatch and observer call. It names no Expansion type and
  has no `#ifdef`, so a run without the adapter polls and dispatches
  byte-identically.

## What it does

- `ai_group_spawn` (tool, lease required) creates one `eAIGroup` of 1-8
  `eAISurvivor` members under a tool-generated `spawn_token`, applies the
  faction loadout, formation and one of HOLD / ONCE / LOOP / ROAM, and
  answers with what the server reads back (faction, formation, members).
  A refusal's message carries what the server filled before refusing:
  `field`, `allowed` (for an unknown faction or formation, the names the
  server knows) and `args_read` with `bad_args`, `living` and `limit`
  with `ai_limit_reached`, and `created`. Only names from a fixed list
  and checked names and numbers cross, so nothing else the caller sent
  is echoed.
- `ai_group_order` (tool, lease required) orders one group registered by
  `ai_group_spawn`, on the server. At least one of `behaviour`,
  `waypoints`, `formation` or `speed` must be given (none is `bad_args`
  and sends nothing).
- `ai_group_release` is internal: the broker queues it by `spawn_token`
  when a delivered spawn is abandoned (timeout or cancel). It is not a
  public tool and never runs from lease-end cleanup.
- The adapter keeps its own group registry on the server (groups are never
  deleted from it; a group is `active` or `releasing`). Members also enter
  the base `m_RuntimeObjects` and, with a `scene_id`, the scene registry,
  so `object_inspect`, `object_delete` and `scene_entities` work on them.

## Ordering a group (`ai_group_order`, 3ca5)

- An unknown `group_id`, or a group that is `releasing`, answers
  `not_found` in the adapter and changes nothing.
- The adapter validates every field before applying any: `behaviour`
  (HOLD, ONCE, LOOP, ROAM), `formation` (a name registered in
  `eAIFormation.s_FormationTypes` of the installed Expansion build, so a
  formation a newer build adds is accepted and `RANDOM` is not),
  `waypoints` (flat triples, 1 to 32 points) and `speed` (1 to 3, only
  beside `speed_set`). A bad formation changes nothing, not even the
  other fields of the call. A `bad_args` for one field names that
  `field` in its message and, for `behaviour` and `formation`, the
  `allowed` names; an order that reaches the adapter with no field to
  apply is a bare `bad_args`.
- The tool and the broker refuse an explicit empty `waypoints` list. The
  adapter reads an empty one as no waypoints, the form an absent key
  reaches it in (in-game round B), so a behaviour sent alone keeps the
  route.
- Only the fields present are applied. Waypoints replace the route
  (`ClearWaypoints`, backtracking off, one `AddWaypoint` per point, the
  current waypoint set to the first new point). `ROAM` roams to Expansion
  locations, not only to waypoints.
- Speed travels as the saved normal/threat preferences: the spawn seeds
  them from the member getters, an order's `speed` (walk/jog/sprint as
  1/2/3) replaces the normal preference, HOLD applies formation state
  NONE plus waypoint behaviour HALT and writes (STATIC, threat) per
  member; a repeated HOLD touches neither preference; a speed ordered
  in a HOLD is only saved; and leaving a HOLD restores (normal, threat).
- Changing only the formation keeps its scale and looseness.
- The reply is read after the change, inside `ai_group`: `group_id`,
  `behaviour` and `behaviour_expansion`, `formation`, `formation_state`,
  `waypoint_count`, `waypoint_index`, `in_combat` and, when the group is
  in combat, `combat_may_override: true` (the combat FSM may ignore the
  order), plus `roams_expansion_locations: true` when the behaviour is
  ROAM, and one member row per registered member with `object_id`,
  `alive`, `speed_limit` and `speed_limit_threat` (limits are 0 when
  the entity is gone). A `not_found`, and a
  `bad_args` with no field, carry no `ai_group`.

Offline coverage: `tools/tests/test_xai_group_order.py` (criteria O3 and
O4 for this verb, E5, and the mutants "not restoring the limit after a
HOLD" and "a HOLD stomping the threat limit", run on a mutated copy of
the adapter source).

## How the adapter announces itself (deviation from plan 2.2)

The adapter adds no verb names to `caps=`. With the names, the saturated
server poll measured 1037 characters, past the 1024 limit of #321 (1067
with order/state, 1079 with v13's `lease_close`). Instead its poll carries
`&xai=<16 hex>.<bitmask>`: the adapter contract hash plus one bit per verb,
in the append-only order fixed in `bridge_readiness.py`
(`EXPANSION_AI_VERB_BITS`: release, spawn, order, state). The broker
expands the mask to names before storing the census, so admission and the
capability comparator keep seeing names, and the saturated poll with the
full adapter (widest mask) and v13 measures 1020, four characters under
the limit. This deviates from plan 2.2 ("the
adapter adds its names to the census") and is provisional, pending
Guillermo's confirmation.

## The lease-close gate (active on bridge v13)

New adapter mutations are admitted only when every peer the run binds
announces real lease-close support (plan 5.1: server and client, or both
roles of an offline binding), with a current instance and generation, at
enqueue and again at delivery. On bridge v13 both peers announce
`lease_close` / `lease_close_client`, so `ai_group_spawn` and
`ai_group_order` are admitted and the adapter takes part in the lease
close: its groups are stopped by `LeaseCloseExtensionStop` and reported
as `ai_group:<spawn_token>` `residue` entries by
`LeaseCloseExtensionCensus` until they disappear.
The close removes the members and the group, nothing else. A member that
dies drops the item in its hands, usually the loadout's weapon (vanilla
`DayZPlayerImplement.DeathDropHandEntity`, called from `OnSimulationEnd`,
`scripts/4_world/entities/dayzplayerimplement.c:17` and `:635`); from
then on it is a world item of its own, with the corpse lifetime (`:646`).
The adapter cannot tell it apart from any other item, so neither
`ai_group_release` nor the close deletes it or counts it as residue: it
stays on the ground after a clean close.
When a bound peer's
census announces no close, the spawn and the order are refused with
`expansion_unavailable` / `lease_close_not_supported` before anything is
created or ordered (a real bridge v12 peer is already stopped earlier by
the version gate). Without the adapter at all, the refusal is
`expansion_unavailable` / `adapter_not_loaded`; with a stale adapter
contract hash, `adapter_contract_mismatch`.

## Licence

DayZ Expansion is CC BY-NC-ND 4.0. This repository contains no Expansion
code or files: the adapter only calls public Expansion AI methods and
names its classes, and has no `modded class` of any Expansion class.
Whether a commercial product may use Expansion is not decided here; the
requesting project (173a) states its final product will use its own AI.
