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
- `ai_group_order` (tool, lease required) orders one group registered by
  `ai_group_spawn`, on the server. At least one of `behaviour`,
  `waypoints`, `formation` or `speed` must be given (none is `bad_args`
  and sends nothing).
- `ai_group_state` (tool, no lease) reads one group by `group_id`, or
  every group registered under a `scene_id`, exactly one of the two.
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
  `waypoints` (flat triples, 1 to 32 points; an empty list is refused)
  and `speed` (1 to 3, only beside `speed_set`). A bad formation changes
  nothing, not even the other fields of the call.
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

## Reading a group (`ai_group_state`, 90f9)

Read-only tool `ai_group_state(group_id=None, scene_id=None, timeout_s)`.
Exactly one of the two ids must be given; both or neither is `bad_args`
(the tool refuses before sending, the loopback `_command_schema` admits
only one-key variants, and the bridge dispatch refuses again). It takes
no lease: `ai_group_state` is in `READ_ONLY_COMMANDS`, so the real
`authorize` passes it without a token, including after
`session_release`, while a mutating command in the same state answers
`lease_required`.

The broker admits the server verb by census, base `ach`, verb and `xai`
only (`expansion_unavailable` with `adapter_not_loaded` or
`adapter_contract_mismatch`); it is not a new mutation, so the
lease-close gate never applies and residues stay readable on a v12
census. The adapter announces the verb as bit3 of its `xai` bitmask
(see "How the adapter announces itself"); it adds no name to `caps=`.

The dispatch answers at once, with no job and no setter call. It never
reads through `eAITarget.GetThreat` or `GetPosition(false)`, which would
update state while reading.

With `group_id` the reply is `ai_group`; with `scene_id` it is
`ai_groups` with every group of that scene in registration order (a
scene with none answers an empty list). Per group: `group_id`,
`scene_id`, `faction`, `formation`, `formation_state`, `behaviour` (tool
vocabulary) and `behaviour_expansion` (waypoint-behaviour name),
`waypoint_index`, `waypoint_count`, `current_waypoint`, `in_combat`,
`alive` (members alive now), `members_total` and `group_present`
(whether `eAIGroup.GetGroupByID(id, false)` still returns the original
group). A releasing group with anything left also carries
`release: "pending"`, `confirmed: false` and the pending member ids in
`remaining_ids`. Getters of a group Expansion no longer returns are
never called: its Expansion-read fields stay unavailable (empty strings,
`waypoint_index: -1`, no current waypoint) while its registered members
are still read and `behaviour` answers from the adapter registry.
Behaviour and formation are read back through the same mapping the
spawn and order replies use (`AIFillBehaviour` and the enum constants),
so the three verbs answer alike for the same group; `ROAMING_LOCAL`
refines `ROAMING` when Expansion roams locally.

Per member: `object_id`, `alive`, `pos`, `health`, `fsm_state` and
`fsm_substate` (from `GetFSM()`/`GetState()`/`GetName()` and
`GetSubFSM()`; any null reference in that chain reads empty), `threat`
(`GetThreatToSelf(true)`), `fighting`, `speed_limit` and
`speed_limit_threat`, `emote_playing`, and `emote_pending` (a request in
either the Expansion layer, `m_Expansion_EmoteID`, or the vanilla layer,
`m_bEmoteIsRequestPending`). A live read refreshes the member's last
observation; a member whose entity is gone answers `gone: true` with
that last observation (`pos`, `health`, `alive`) and
`last_observation: true`. A member seen dead stays `alive: false`.

Errors: an unknown `group_id`, or the id of a releasing group that is
already fully gone (no member entity left and the original group no
longer returned), is `not_found`. A group with no living members
answers ok with `alive: 0`.

The state-only fields of the shared `MCPAIGroup` reply (`group_present`,
`alive`, `members_total`, `current_waypoint`, `scene_id`) are only
filled by `ai_group_state`; a spawn/order/release reply leaves them at
their defaults.

Offline coverage: `tools/tests/test_xai_group_state.py` (O4, E7, and the
O13 part that reads residues on a v12 census). In-game validation is
pending; this section describes the offline contract only.

As a server read it is also admitted under the live profile:
`LIVE_ALLOWED_COMMANDS` derives from `READ_ONLY_COMMANDS & SERVER_COMMANDS`
(`loopback.py`), and the adapter mutations stay outside it.

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
`LeaseCloseExtensionCensus` until they disappear. A peer still on v12
announces no close, so the spawn and the order are refused with
`expansion_unavailable` / `lease_close_not_supported` before anything is
created or ordered. Without the adapter at all, the refusal is
`expansion_unavailable` / `adapter_not_loaded`; with a stale adapter
contract hash, `adapter_contract_mismatch`.

## Licence

DayZ Expansion is CC BY-NC-ND 4.0. This repository contains no Expansion
code or files: the adapter only calls public Expansion AI methods and
names its classes, and has no `modded class` of any Expansion class.
Whether a commercial product may use Expansion is not decided here; the
requesting project (173a) states its final product will use its own AI.
