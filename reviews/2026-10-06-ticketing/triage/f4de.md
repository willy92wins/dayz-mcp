# Triage f4de

Tickets: fb-20261002-165829-f4de

Title: No headless way to kill a living player: player_respawn is a no-op even with godmode off; add a kill option

First pass by GLM-5.3-Flash-EXL3 on the GX10 (API direct, grep excerpts of origin/main 6671fd2, no whole-tree access). Raw model output below, unedited.

<!-- ticket f4de | model GLM-5.3-Flash-EXL3 | finish stop | 326s | usage {"prompt_tokens": 43536, "completion_tokens": 3485, "total_tokens": 47021, "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 2096}} | grep ['TICKET', 'skip_clearance_check', 'player_respawn', 'log_matches', 'query_player_state', 'player_teleport', 'DayZDiag', 'player_godmode', 'restore_gameplay', 'minutes', 'living', 'documented', 'headless', 'damage'] -->

## VERDICT
OWNER_DECISION — the code confirms the gap (no kill verb, `player_respawn` takes no arguments and only drives the vanilla death-screen respawn), but the observed no-op is the documented contract, and adding `player_kill`/`force` is a new-feature product choice (new bridge verb + PBO), not a defect fix.

## EVIDENCE
- `tools/dayz_mcp/loopback.py:775` — `"player_respawn": _command_schema(_schema_variant())`: the ingress schema accepts **no fields at all**, so a `force`/`kill` argument cannot even reach the bridge today.
- `tools/dayz_mcp/loopback.py:93-123` — `player_respawn` is in `CLIENT_COMMANDS`; `SERVER_COMMANDS` (lines 63-92) contains `player_heal` and `player_godmode` but **no kill/damage verb** of any kind.
- `tools/dayz_mcp/bridge_readiness.py:312` — `"player_respawn": "player_respawn"` is mapped under the `client` peer only; no `player_kill` entry exists in `_BRIDGE_COMMAND_TOOLS` (lines 269-331), confirming the capability is absent on both peers.
- `tools/dayz_mcp/server.py:365-369` — `GODMODE_DEFAULT_LINE`: godmode is ON by default and `player_godmode(on=false)` restores vanilla damage, so the ticket's setup (godmode off, still no kill) is consistent with the shipped surface.
- `tools/dayz_mcp/server.py:4806-4807` — comment on `player_heal`'s origin: "the only remedy was player_respawn, which killed the character and moved it" — i.e. `player_respawn` is designed around an already-dead character, matching the ticket's "works only from the death screen".
- `tools/dayz_mcp/server.py:4749-4750` — `player_teleport` description: "y==0 snaps to SurfaceY"; a non-zero `y` (the ticket's 160) is teleported to literally, which explains the hovering body and why the fall-damage fallback failed.
- `CHANGELOG.md:13` — godmode choice "is kept for that player identity … across `player_respawn`", i.e. respawn is treated as the vanilla death/respawn flow, not a kill primitive.

## ROOT CAUSE
Not a regression: `player_respawn` is a client verb that triggers the vanilla death-screen respawn, which is a no-op while the character is alive, and the server verb set deliberately has no way to set a living player's health to 0 (the closest verb, `player_heal`, only heals — `server.py:4808-4820`). The ticket asks for the missing capability.

## PROPOSED FIX
Recommended (only if the owner approves the feature):
1. `addon/scripts/5_Mission/MCPBridge.c`: add a `player_kill` server verb that selects the player by `uid` (same selection as `player_teleport`/`player_heal`) and sets health to 0 through the vanilla damage juncture (e.g. the `EEHitBy` path or `SetHealth("", "Health", 0)`), so the death screen appears and the existing `player_respawn` flow then works; bump `SERVER_CAPABILITIES` and the arg-contract hash.
2. `tools/dayz_mcp/loopback.py`: add `"player_kill"` to `SERVER_COMMANDS` and a `_COMMAND_ARG_SCHEMAS` entry `required=("uid",)`-style variant mirroring `player_godmode` (lines 904-913).
3. `tools/dayz_mcp/bridge_readiness.py`: map `"player_kill": "player_kill"` under `server`.
4. `tools/dayz_mcp/server.py`: register a lease-gated `player_kill(uid, timeout_s)` tool next to `player_heal`.

Contract before: `player_respawn` on a living player answers `ok:1 requested:1` and nothing happens; the caller times out in `wait_for(log_matches "Create entity type 'Survivor")`. After: `player_kill` returns `{ok: true, player_kill: {uid, health_before, health_after: 0}}` (errors `no_player`, `player_dead`), after which `player_respawn` + `wait_for` succeeds within the 40 s budget.

Alternative (no code): document in the `player_respawn` description and knowledge pack that the only headless kill is infected damage, and close as wontfix. NOT SHOWN: the `player_kill` handler symbol in `MCPBridge.c` (file not excerpted).

## TEST
Unit test (fails before, passes after): in the server test suite, build the app and assert `"player_kill" in set(app tool names)` and `"player_kill" in loopback.SERVER_COMMANDS` and in `_BRIDGE_COMMAND_TOOLS["server"]`; before the fix all three fail. In-game check after implementation: godmode off → `player_kill()` → `wait_for(log_matches "Create entity type 'Survivor", 40)` satisfies (today it times out, per the ticket repro).

## SIZE / RISK
M — spans Enforce (new dispatch verb + capabilities), the daemon ingress schema, and a new MCP tool, plus a PBO rebuild. Main risk is **bridge-version compatibility**: an old PBO without the verb will surface as `unmapped_announced_commands`/capability mismatch until the arg-contract hash (`ach=`) and PBO are refreshed together; secondarily, a kill verb is destructive and must stay lease-gated like the other mutating verbs.

## NOT VERIFIED
- The client-side implementation of `player_respawn` in `addon/scripts/5_Mission/MCPClientBridge.c` (not excerpted) — I cannot confirm from code what it does on a living player, only that the ticket's observed no-op matches the documented contract.
- Whether the y=160 hover is an engine/physics artifact or a `player_teleport` defect: the `player_teleport` handler in `MCPBridge.c` is NOT SHOWN.
- The `dayz-mcp-verify` record of 2026-08-29 cited by the ticket.
- Whether godmode-off fall damage from a normal teleport (y=0 snap) would actually kill the player, which would offer a cheaper workaround than a new verb.