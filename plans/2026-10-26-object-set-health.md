# object_set_health: set one object's health and read it back (inbox c703)

Base: main `75b5d16`. Bridge and Python: a new server verb, so this PR changes the PBO and Guillermo merges it. Bridge version stays 12. No sealed module changes.

## The ticket (fb-20261026-150310-c703, LFQuad, request)

DayZ 1.30 replaces a car wheel that reaches ruined with its `_Ruined` class (`CarWheel.EEHealthLevelChanged`, `P:\scripts\4_world\entities\core\inherited\inventoryitem.c:220`; the names come from `CarWheelType.SetReplacementTypes`, `inventoryitemtype.c:17-46`). LFQuad added `LFQuad_Wheel_Front_Ruined` and `LFQuad_Wheel_Rear_Ruined`, and nothing in the MCP can bring an object to health 0 to test the swap: a grenade at 1.5 m took a loose wheel from 1.0 to 0.971. The ticket asks for `object_set_health(object_id | type+pos, health01, zone="")` on the server, calling `SetHealth01(zone, "Health", value)` (`object.c:1099-1103`), answering the health before and after and the health level. Empty zone is the global health. A mode that reports what occupies the slot a few ticks later is optional.

Acceptance (ticket): with `LFQuad_base` built by `OnDebugSpawn` on DayZDiag 1.30, `object_set_health` on a mounted wheel at 0 leaves `LFQuad_Wheel_Front_Ruined` in that slot (`telemetry_read` `object_at` of the vehicle, `attachment_items`) with no script errors. Control: the same on `CivilianSedan` gives `CivSedanWheel_Ruined`.

## Decisions

### D1. The tool

`object_set_health(health01, object_id=0, type="", pos=None, slot="", zone="", timeout_s=DEFAULT_TOOL_TIMEOUT_S)`, a lease tool (`LEASE_TOOL_LINE`), server peer.

- Target: `_object_target_args(type, pos, object_id)` (`tool_args.py:462`), the contract `object_inspect` and `object_anim` use: `object_id > 0`, or a classname and `pos`.
- `health01`: a real number, finite, in `[0, 1]`; a bool is refused. `bad_args` before any bridge call.
- `slot` and `zone`: strings. Each is sent only when it is not empty; an absent key reads `""` in the bridge.
- One `call_bridge("object_set_health", args, "server", ...)` under `runtime.tool_lock`.

Description (house style, sentences kept short): set one object's health on the server and read it back in the same tick. Target by object_id (world_spawn, object_resolve, inventory_give.object_id, inventory_attach.item_object_id) or by exact classname within 25 m of pos. With slot, the target is the item attached to that object in that inventory slot (telemetry_read object_at lists the slot names in declared_slots). health01 is a fraction of the zone's maximum Health; an empty zone is the global health. The reply, object_health, gives health01 and the health level (0 pristine to 4 ruined) read before and after the call. A player is refused: use player_heal or player_kill. An item the game replaces when it is ruined (a 1.30 car wheel becomes its _Ruined class) is replaced by the game, not by this tool: read the vehicle again with telemetry_read object_at to see what sits in the slot.

### D2. The bridge (`MCPBridge.c`, server)

`else if (command.cmd == "object_set_health") { postNow = DispatchObjectSetHealth(command, result); }` next to `object_resolve` (`MCPBridge.c:663-666`). The method answers in the dispatch tick and queues no job: in this bridge a job only waits and publishes, and the world changes in the dispatch tick.

In this order, locals declared at the top as the file does:

1. `command.args` null, or `health01` not finite or outside `[0, 1]`: `bad_args`.
2. `target = ResolveCommandObject(command.args, error)` (`MCPBridge.c:5062`), unchanged; null answers its error (`object_id_unknown`, `object_id_stale`, `object_not_found`, `ambiguous_object`, `bad_args`).
3. When `slot != ""`: the target must be an `EntityAI` with an inventory; `InventorySlots.GetSlotIdFromString(slot)` must be valid (`IsSlotIdValid`) and `HasAttachmentSlot` true, or `slot_not_found` (the checks `inventory_attach` makes, `MCPBridge.c:4050-4063`); `FindAttachment(slotId)` null is `slot_empty`; otherwise the attachment becomes the target.
4. A target that is a `Man` is `target_is_player`. Players have their own verbs, and `player_kill` keeps the godmode bookkeeping (`MCPBridge.c:3455-3460`).
5. `target.GetMaxHealth(zone, "Health") <= 0` is `no_health` with the type filled: no Health in that zone, or an unknown zone. Nothing changes.
6. Fill `MCPObjectHealth`: `type`, `zone`, `slot`, `health01_requested`, `health01_before = GetHealth01(zone, "Health")`, `health_level_before = GetHealthLevel(zone)`.
7. `target.SetHealth01(zone, "Health", health01)`: the only health write in the method.
8. If `target` reads null now: `target_gone`, with the before fields kept. Not expected, since ObjectDelete defers to the next frame (`MCPBridge.c:4247-4248`), but it keeps a null out of the reads.
9. `health01_after`, `health_level_after` read the same way.
10. `result.object_health`, `result.type`, `result.object_id = RuntimeObjectId(target)` (`MCPBridge.c:2618`; 0 for an attachment nobody registered).
11. `|health01_after - health01| > 0.001` is `health_not_applied` (ok false, report kept). Otherwise ok.

`SERVER_CAPABILITIES` (`MCPBridge.c:73`) gains `object_set_health` after `object_resolve`. `SERVER_ARG_CONTRACT_HASH` (`:78`) takes the new hash (D4).

### D3. The messages (`MCPMessages.c`)

- `MCPArgs` gains `float health01;` and `string zone;` (`slot`, `object_id`, `type` and `pos` exist).
- New `class MCPObjectHealth`, primitives only: `string type; string zone; string slot; float health01_requested; float health01_before; float health01_after; int health_level_before; int health_level_after;`.
- `MCPResult` gains `ref MCPObjectHealth object_health;`, null unless this verb filled it.

### D4. Wire, admission and contract (Python)

- `loopback.py`: `object_set_health` in `SERVER_COMMANDS` and in `_CAPABILITY_ADMISSION_COMMANDS` (an older PBO is never handed it); `_command_schema` with two variants, `required=("object_id", "health01")` and `required=("type", "pos", "health01")`, each with `optional=("slot", "zone")`. Validators: `object_id` an integer of at least 1, `type` a non-empty string, `pos` a real vector3, `health01` a finite real in `[0, 1]`, `slot` and `zone` non-empty strings.
- `bridge_readiness.py`: `SERVER_ARG_CONTRACT["object_set_health"] = ("health01", "object_id", "pos", "slot", "type", "zone")`, as `#265` did for its verbs; `_BRIDGE_COMMAND_TOOLS` maps it to the tool. The Enforce `SERVER_ARG_CONTRACT_HASH` is the new `server_arg_contract_hash()`.
- Not in `READ_ONLY_COMMANDS`, so it needs a lease (`session_coordination.py:21-32`: anything absent there is mutating), and not in the initial read catalog.
- `result_prune.py`: keep `object_health` for this verb, on ok and on `health_not_applied`.
- `bridge_errors.py`: `health_not_applied` carries `observed=health01_after=<v> health_level_after=<n>`, the way `view_not_applied` carries its read-back (`bridge_errors.py:461-475`); nothing else crosses.

### D5. Docs

README (98 to 99 tools, the tool in its domain list), CLAUDE.md (the two counts), `dayz-mcp-architecture.md` (two counts), `tools/README-mcp.md` if it lists tools, PROJECT-MAP, CHANGELOG under Added.

### Out of scope

The optional settle report (telemetry_read already lists the slot), health types other than `Health` (Blood, Shock), several objects in one call, and players.

## Before writing code

Check each engine call against `P:\scripts` (1.30) and note its `path:line` in the test module's docstring: `Object.SetHealth01`, `GetHealth01`, `GetMaxHealth`, `GetHealthLevel`; `InventorySlots.GetSlotIdFromString` and `IsSlotIdValid`; `GameInventory.HasAttachmentSlot` and `FindAttachment`; `IsMan` or the `Man` cast; and the 1.30 `CarWheel.EEHealthLevelChanged` the description relies on. If one does not exist or takes other arguments, stop and report it instead of working around it (R26).

## Viability criteria (R26)

New module `tools/tests/test_fb_c703_object_set_health.py`, built like `tools/tests/test_server_perf_nav_visibility.py` (ingress, admission, census, Enforce source, prune, tool). Each test names the case it fails on.

Python, positive:
- P1 the object_id path sends exactly `{"object_id": 7, "health01": 0.0}`; with `slot="Wheel_1_1"` and `zone="Engine"` it adds those two keys, and only then.
- P2 the type path sends exactly `{"type": "LFQuad_Wheel_Front", "pos": [x, y, z], "health01": 0.5}`.
- P3 a full census (capability and current hash) queues the wire args unchanged; the server peer gets it, the client peer never does.
- P4 the ok reply keeps `object_health` through the prune, and so does `health_not_applied`.

Python, negative (no bridge call, `bad_args` naming the field):
- N1 `health01` of -0.01, 1.01, NaN, inf, True, "0": each refused.
- N2 no `object_id` and no `type`; `object_id` -1 or True; `type` with whitespace; `pos` of two numbers.
- N3 the ingress refuses `health01` out of range, `object_id` 0, an unknown key, an empty `slot` or `zone`, and the client peer.
- N4 without a lease: `lease_required`. `command_requires_lease("object_set_health")` is True.
- N5 a census without the verb: `bridge_capability_missing` before an id; the previous contract hash: `arg_contract_mismatch`.
- N6 `health_not_applied` text carries `observed=health01_after=... health_level_after=...` and no caller input.

Census and contract:
- C1 `SERVER_CAPABILITIES`, `tools/tests/fixtures/bridge_capabilities_v1.json`, the dispatch chain and `_BRIDGE_COMMAND_TOOLS` each name the verb once.
- C2 the canonical contract in `test_arg_contract_hash.py` gains `object_set_health=health01,object_id,pos,slot,type,zone`; the Enforce constant equals the Python hash; `MCP_BRIDGE_VERSION` stays `"12"`.

Enforce source (read from `MCPBridge.c` and `MCPMessages.c`):
- E1 the dispatch branch calls `DispatchObjectSetHealth` and posts in the same tick: the method inserts no job and calls no `CallLater`.
- E2 the steps run in D2's order: the `health01` check before `ResolveCommandObject`; the slot checks give `slot_not_found` and `slot_empty`; `target_is_player` after the slot branch; `GetMaxHealth(zone, "Health")` before any write; both before reads before the write; the null guard and both after reads after it; the tolerance check last.
- E3 exactly one `SetHealth01(` in the method, with the `"Health"` literal; no `SetHealth(`, no `ObjectDelete`.
- E4 `MCPObjectHealth` holds primitives only, with D3's members; `MCPResult.object_health` and `MCPArgs.health01` and `zone` exist.
- E5 the method's locals are declared at its top.

Mutants, each in a copy of the tree and each turning at least one of these tests red: the write before the before reads; the after reads taken from the before values; no `GetMaxHealth` check; `"Blood"` for `"Health"`; no tolerance check; no `slot_empty` check; no `Man` refusal; the verb left out of `SERVER_CAPABILITIES`; the contract entry left out; the tool sending an empty `slot`.

Suite: the whole `tools/tests` tree fails the same test ids as main `75b5d16` and no others.

## In-game steps (final round)

1. The compile gate.
2. `world_spawn` `LFQuad_base` (the ticket's flags), `telemetry_read` `object_at` for its `declared_slots`, then `object_set_health(object_id=<car>, slot=<a front wheel slot>, health01=0)`: ok, `health_level_after` 4. One second later `telemetry_read` `object_at` lists `LFQuad_Wheel_Front_Ruined` among `attachment_items`; no script error in the RPT.
3. Control: the same on `CivilianSedan`, which gives `CivSedanWheel_Ruined`.
4. `health01=0.5` on a loose item: ok, `health01_after` 0.5.
5. Refusals: zone `Bogus` (`no_health`, or `health_not_applied` with the health unchanged), a player (`target_is_player`), an empty slot (`slot_empty`).
