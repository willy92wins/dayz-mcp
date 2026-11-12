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
- `ai_group_release` is internal: the broker queues it by `spawn_token`
  when a delivered spawn is abandoned (timeout or cancel). It is not a
  public tool and never runs from lease-end cleanup.
- The adapter keeps its own group registry on the server (groups are never
  deleted from it; a group is `active` or `releasing`). Members also enter
  the base `m_RuntimeObjects` and, with a `scene_id`, the scene registry,
  so `object_inspect`, `object_delete` and `scene_entities` work on them.

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
`lease_close` / `lease_close_client`, so `ai_group_spawn` is admitted and
the adapter takes part in the lease close: its groups are stopped by
`LeaseCloseExtensionStop` and reported as `ai_group:<spawn_token>`
`residue` entries by `LeaseCloseExtensionCensus` until they disappear.
The close removes the members and the group, nothing else. A member that
dies drops the item in its hands, usually the loadout's weapon (vanilla
`DayZPlayerImplement.DeathDropHandEntity`, called from `OnSimulationEnd`,
`scripts/4_world/entities/dayzplayerimplement.c:17` and `:635`); from
then on it is a world item of its own, with the corpse lifetime (`:646`).
The adapter cannot tell it apart from any other item, so neither
`ai_group_release` nor the close deletes it or counts it as residue: it
stays on the ground after a clean close.
When a bound peer's census announces no close, the spawn is refused with
`expansion_unavailable` / `lease_close_not_supported` before anything is
created (a real bridge v12 peer is already stopped earlier by the version
gate). Without the adapter at all, the refusal is
`expansion_unavailable` / `adapter_not_loaded`; with a stale adapter
contract hash, `adapter_contract_mismatch`.

## Licence

DayZ Expansion is CC BY-NC-ND 4.0. This repository contains no Expansion
code or files: the adapter only calls public Expansion AI methods and
names its classes, and has no `modded class` of any Expansion class.
Whether a commercial product may use Expansion is not decided here; the
requesting project (173a) states its final product will use its own AI.
