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
- The base PBO (`DayZ_MCP`, bridge v12) only carries the five extension
  hooks of the adapter plan (§2.1): `ServerCapabilities`,
  `ExtensionPollParams`, `DispatchExtension`, and the two lease-close hooks
  nothing calls yet. It names no Expansion type and has no `#ifdef`, so a
  run without the adapter polls and dispatches byte-identically.

## What it does

- `ai_group_spawn` (tool, lease required) creates one `eAIGroup` of 1-8
  `eAISurvivor` members under a caller-generated `spawn_token`, applies the
  faction loadout, formation and one of HOLD / ONCE / LOOP / ROAM, and
  answers with what the server reads back (faction, formation, members).
- `ai_group_release` is internal: the broker queues it by `spawn_token`
  when a delivered spawn is abandoned (timeout or cancel). It is not a
  public tool and never runs from lease-end cleanup.
- The adapter keeps its own group registry on the server (groups are never
  deleted from it; a group is `active` or `releasing`). Members also enter
  the base `m_RuntimeObjects` and, with a `scene_id`, the scene registry,
  so `object_inspect`, `object_delete` and `scene_entities` work on them.

## The lease-close gate (mutations are inert today)

New adapter mutations are admitted only when the run's peers announce real
lease-close support. Bridge v12 does not announce `lease_close`, so today
`ai_group_spawn` is refused with `expansion_unavailable` /
`lease_close_not_supported` before anything is created. That is the
designed inert behaviour, not a fault: the v13 lease-close activation (its
census, observer and floors) is a separate change, and only then do spawns
go through. Without the adapter at all, the refusal is
`expansion_unavailable` / `adapter_not_loaded`; with a stale adapter
contract hash, `adapter_contract_mismatch`.

## Licence

DayZ Expansion is CC BY-NC-ND 4.0. This repository contains no Expansion
code or files: the adapter only calls public Expansion AI methods and
names its classes, and has no `modded class` of any Expansion class.
Whether a commercial product may use Expansion is not decided here; the
requesting project (173a) states its final product will use its own AI.
