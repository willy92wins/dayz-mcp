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
- `ai_emote` is not a tool: `actor_emote(object_id=...)` sends it to the
  server with a lease (see "Playing an emote on a member" below).
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
`lease_required`. In a run launched by `dayz_test_run`, after
`session_release` leaves the run idle (`RUNNING_IDLE`), `ai_group_state`
answers `run_not_owned` like any command until a lease adopts the run
(then it reads again without a token: `loopback.py`
`_enqueue_run_rejection` lets a `RUNNING` run through); `session_status`
(`lease_end.unconfirmed`) only says the close owes the release of some
group (`ai_group_release_unconfirmed`), not which ones nor in what
state; the groups' state is read before the release. Outside a managed
run, the read still needs no lease.

The broker admits the server verb by census, base `ach`, verb and `xai`
only (`expansion_unavailable` with `adapter_not_loaded` or
`adapter_contract_mismatch`); it is not a new mutation, so the
lease-close gate never applies and residues stay readable on a census
that announces no close support
(a real bridge v12 peer is stopped earlier by the version gate). The
adapter announces the verb as bit3 of its `xai` bitmask (see "How the
adapter announces itself"); it adds no name to `caps=`.

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

Offline coverage: `tools/tests/test_xai_group_state.py` (O4, E7, the O13
part that reads residues when the census announces no close support, and
the real bridge v12 poll stopped by the version gate). In-game
validation is pending; this section describes the offline contract only.

## Playing an emote on a member (`ai_emote`, 36fa NPC half)

`actor_emote(emote, wait_s=10.0, object_id=None, timeout_s)` is the one
tool. Without `object_id` it is the client verb of 36fa, unchanged. With
`object_id` (a member of an `ai_group_spawn` group) the broker sends
`ai_emote` to the server with a lease; the emote catalog and the
`wait_s` bounds (0.5 to 30 s) are the client verb's, and the states gain
`cancelled`. Every server answer also carries `emote_pending`; on an
error answer it travels in the message text as `emote_pending=true` or
`emote_pending=false`, since only that text crosses to the caller.

Three layers are kept apart: the Expansion request
(`m_Expansion_EmoteID`), the vanilla request
(`m_bEmoteIsRequestPending`) and playback (`m_bEmoteIsPlaying`). The
dispatch, in this order, is the only place the verb writes:

1. `object_id` not a living member of an `active` group: `bad_target`.
2. Another emote job already watching that member (an `end` included):
   `emote_rejected`.
3. An Expansion request pending, or `IsEmotePlaying()` true (either
   layer): `emote_rejected`. Nothing is sounding when the job starts,
   so what it sees afterwards is its own.
4. Fighting (`m_eAI_IsFightingFSM`), group in combat, or
   `GetThreatToSelf(true) > 0.2`: `emote_rejected`, no write.
5. One write: `Expansion_SetEmote(id, true, wait_s * 1000)`. With
   `autoCancel=true` Expansion consumes the request in its own attempt
   even if nothing plays, and schedules the cancel of static poses
   `autoCancelDelay` ms after that attempt; temporary poses are not
   cancelled. `Expansion_PlayEmote` is never called (it would skip the
   FSM guard).

The job in `m_Jobs` only reads the member's `EmoteManager` and Expansion
request, and writes only its own fields. `started` is the first tick
with `m_bEmoteIsPlaying` true and `Expansion_GetCurrentGesture()` equal
to the requested id; `finished` follows a seen start and stop. Both
carry `started_after_s` / `finished_after_s` from the request and
`emote_pending` read on that tick, so a `finished` with
`emote_pending: true` warns the request was never consumed and the
gesture can play again. At `wait_s`: `started` if it started and has
not stopped, else `emote_timed_out` with `emote_pending` read when that
answer is published (the lease close publishes it with no tick at all). A member that
dies or whose entity disappears answers `emote_interrupted`; that end
never proves the lease close (the close deletes the member, and its
census — not this job — is the proof).

With `emote="end"`, after steps 1-2: if it plays, the dispatch clears
any Expansion request with `Expansion_SetEmote(0)`, requests
`ServerRequestEmoteCancel()`, and a job watches to `finished` (or
`emote_timed_out` at `wait_s`). If only an Expansion request is
pending, `Expansion_SetEmote(0)` clears it and the dispatch answers
`emote_state: "cancelled"` with `emote_pending` read after the write.
If only a vanilla request is in flight, `emote_rejected` with
`emote_pending: true` and no write; the `end` can be repeated. With
nothing, `no_emote_playing`. A request that stays pending is cleared by
an `end` or by the lease-close member delete.

The verb is bit 4 of the `&xai` mask (append-only: release, spawn,
order, state, emote; the widened mask `31` is the same two characters
as `15`, so the saturated poll stays at its measured size). As a new
mutation it sits behind the v13 lease-close gate: without real close
support on every bound peer the broker answers
`expansion_unavailable` / `lease_close_not_supported`.

Offline coverage: `tools/tests/test_xai_emote.py` (O8, E8; mutants: the
job calling `Expansion_SetEmote`, and a `started` accepting the gesture
already sounding with the same id). In-game validation is pending.

As a server read it is also admitted under the live profile:
`LIVE_ALLOWED_COMMANDS` derives from `READ_ONLY_COMMANDS & SERVER_COMMANDS`
(`loopback.py`), and the adapter mutations stay outside it.

## How the adapter announces itself (deviation from plan 2.2)

The adapter adds no verb names to `caps=`. With the names, the saturated
server poll measured 1037 characters, past the 1024 limit of #321 (1067
with order/state, 1079 with v13's `lease_close`). Instead its poll carries
`&xai=<16 hex>.<bitmask>`: the adapter contract hash plus one bit per verb,
in the append-only order fixed in `bridge_readiness.py`
(`EXPANSION_AI_VERB_BITS`: release, spawn, order, state, emote). The broker
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
`lease_close` / `lease_close_client`, so `ai_group_spawn`,
`ai_group_order` and `ai_emote` are admitted and the adapter takes part in the lease
close: its groups are stopped by `LeaseCloseExtensionStop` and reported
as `ai_group:<spawn_token>` `residue` entries by
`LeaseCloseExtensionCensus` until they disappear. When a bound peer's
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
