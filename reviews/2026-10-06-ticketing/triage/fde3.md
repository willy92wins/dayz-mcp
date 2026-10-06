# Triage fde3

Tickets: fb-20261002-013119-fde3

Title: action_use: synthetic target (component -1) cannot trigger component-gated building actions

First pass by GLM-5.3-Flash-EXL3 on the GX10 (API direct, grep excerpts of origin/main 6671fd2, no whole-tree access). Raw model output below, unedited.

<!-- ticket fde3 | model GLM-5.3-Flash-EXL3 | finish stop | 263s | usage {"prompt_tokens": 43362, "completion_tokens": 3475, "total_tokens": 46837, "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 1945}} | grep ['ActionCondition', 'ActionOpenDoors', 'componentIndex', 'GetDoorIndex', 'GetActionComponentNameList', 'TICKET', 'door_index', 'action_use', 'executed', 'geometry', 'checking', 'synthetic', 'actions', 'points'] -->

## VERDICT
OPEN_CONFIRMED — current main still builds the synthetic world `ActionTarget` with component -1 and the object's origin as cursor point for every non-door `action_use`, and neither the tool nor the bridge accepts a component index or selection name, so component-gated `ActionCondition`s can never pass.

## EVIDENCE
- `tools/dayz_mcp/server.py:6742-6750` — the `action_use` signature accepts only `action, classname, pos, radius, target, door_index, timeout_s`; there is no `component_index` or selection parameter.
- `tools/dayz_mcp/server.py:6723-6724` — the tool's own description states "Without door_index the synthetic target uses component=-1", i.e. the limitation is the shipped behavior for plain world targets.
- `tools/dayz_mcp/server.py:6712-6719` — `door_index` is documented only for "one door of a Building"; named non-door view-geometry components (the ticket's case) have no path.
- `addon/scripts/5_Mission/MCPClientBridge.c:3861` — the non-door branch: `actionTarget = new ActionTarget(targetObj, null, -1, cursorHitPos, 0);` — component is hard-coded -1.
- `addon/scripts/5_Mission/MCPClientBridge.c:3783` — `vector cursorHitPos = targetObj.GetPosition();` — the cursor point is the object position, not a point on the gated component (the comment at 3777-3782 explains `CCTCursor.Can` measures distance from this point).
- `addon/scripts/5_Mission/MCPClientBridge.c:3808-3856` — the `action_use_door` branch shows the working pattern the plain path lacks: scan `GetDoorIndex`, resolve a selection via `GetActionComponentNameList`, compute `ModelToWorld(GetSelectionPositionMS(...))`, and build the `ActionTarget` with the real component (3856) — this machinery exists only behind `command.cmd == "action_use_door"`.
- `tools/dayz_mcp/loopback.py:1211-1222` — the `action_use` command schema has no component/selection field either, so the daemon would reject one before it reached the bridge.
- `tools/dayz_mcp/bridge_readiness.py:301-303` — `action_use`, `action_use_door`, `action_use_target` all map to the single `action_use` tool; no component-mode command exists in the census (`MCPClientBridge.c:469` `CLIENT_POLL_CAPS` likewise).

## ROOT CAUSE
For a world target without `door_index`, `MCPClientBridge.c` constructs `ActionTarget(targetObj, null, -1, targetObj.GetPosition(), 0)`. Any `ActionCondition` that reads the target component (`GetDoorIndex`, `GetActionComponentNameList`) or measures reach from the cursor hit position against a component point evaluates false, so `ActionBase.Can` is false, `PerformActionStart` is never called, and the tool reports `condition_failed` (or silently no action) — exactly what the ticket observed on the custom helipad building.

## PROPOSED FIX
**Recommended (smallest):** generalize the existing door-path machinery to an explicit component index.
1. `tools/dayz_mcp/server.py` (`action_use`): add `component_index: StrictInt | None = None`; validate `component_index >= 0`, mutually exclusive with `door_index`; forward it in the bridge args; echo it in the result.
2. `tools/dayz_mcp/loopback.py` (`action_use` schema, ~1211): add optional `component_index` validator `_integer_in_range(minimum=0, maximum=511)`.
3. `addon/scripts/5_Mission/MCPClientBridge.c` (world-target branch, ~3859-3862): when `command.args.component_index` is present and >= 0, mirror the door branch: call `GetActionComponentNameList(idx, names)`; empty → `result.error = "component_not_found"`; else take the first selection name, `cursorHitPos = ModelToWorld(GetSelectionPositionMS(name))`, build `ActionTarget(targetObj, null, idx, cursorHitPos, 0)`, set `result.component_index = idx`.
4. Bump the argument-contract hash inputs (the `ach=` gate, `MCPClientBridge.c:469` census unchanged if the command name stays `action_use`) and update the tool description.

Caller contract: before — `action_use` on a component-gated action returns `condition_failed`/no dispatch; after — `action_use(..., component_index=N)` dispatches when the condition passes, echoes `component_index: N`, and returns `component_not_found` for an index with no selection names.

**Option 2:** accept a `selection` name string instead and resolve the index server-side by scanning `GetActionComponentNameList` over components (like the door scan at 3810-3818). More ergonomic but a larger scan and a new command name; not recommended for the minimal fix.

## TEST
Unit test (fails before, passes after): in the loopback schema tests, assert that `action_use` with `{"action": "ActionCustomButton", "classname": "Land_LF_RocaHeli", "component_index": 2}` validates and forwards `component_index` to the bridge args — before the fix the field is an unexpected argument (rejected by `loopback.py:1211-1222`); after, it passes. In-game check: spawn the custom building, call `action_use` with the mod's component-gated action and the resolved `component_index`; before the fix the result is `condition_failed`/no effect, after, the action's effect is observable (e.g. via the mod's state or `anim_timeline`).

## SIZE / RISK
M — touches the Python tool signature, the daemon schema, and the Enforce bridge. Main risk is bridge/tools version skew: the new field changes the argument contract, so an old PBO with new tools (or vice versa) must fail closed via the `ach=` mismatch rather than silently ignoring the component; secondarily, a wrong cursor point for the component can still fail reach checks, so the selection-position computation needs in-game validation.

## NOT VERIFIED
- The remainder of the `action_use` dispatch in `MCPClientBridge.c` after line 3872 (how `condition_failed` is produced, the `PerformActionStart` call) — NOT SHOWN.
- Whether the tool-level `pos` argument is used anywhere in the bridge target construction — NOT SHOWN.
- The custom SecretRock action's actual `ActionCondition` and whether `Land_LF_RocaHeli`'s button component exposes selection names via `GetActionComponentNameList` — needs the in-game repro.
- Whether any building in scope has more than `ACTION_USE_DOOR_COMPONENT_CAP` (512, `MCPClientBridge.c:450`) components, which would bound a selection-scan variant of the fix.