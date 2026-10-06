# gpt-6.1-sol review, b4_actions, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Valid default components are rejected.**  
`addon/scripts/5_Mission/MCPClientBridge.c:3895`

The new branch rejects `componentState <= 0`. The actual API documents:

- `-1`: not found.
- `0`: valid default component.
- `1`: valid named component.

Evidence: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\3_game\entities\object.c:197-198`. Vanilla also handles return `0` explicitly at `scripts\5_mission\gui\actiontargetscursor.c:518`.

**Executable failure scenario:** configure a world object `ComponentProbeObject` whose component `0` is valid but unnamed, so its lookup returns `0`. Register `ActionComponentProbe` with otherwise satisfied conditions. With the capability advertised, call:

```json
{
  "action": "ActionComponentProbe",
  "classname": "ComponentProbeObject",
  "component_index": 0,
  "cursor_pos": [10.0, 1.5, 20.0]
}
```

The bridge returns `ok=false, error="component_not_found"` before checking or starting the action. Expected: retain the valid default component and proceed through the existing action lifecycle.

**Suggested fix:** reject negative lookup results, retain `0` and `1`, correct the return-value comments and `REPORT.md:60`, and add a regression covering lookup returns `-1`, `0`, and `1`.

## SPEC COVERAGE

### ITEM 6d21

| Specification bullet | Status |
|---|---|
| Cap 2048 and updated comment | **done** |
| Preserve first match and `doorstwin` exclusion | **done** |
| Preserve Python routing, wire validation, errors and echoes | **done** |
| Offline scan regression: sole match 876, low match, no match | **done** — Python scan representation, beyond a constant assertion |
| In-game window operation, component ≥512, independent door-state read, unsuccessful-scan timing | **missing** — deferred to the later game cycle |

### ITEM fde3

| Specification bullet | Status |
|---|---|
| Public parameters; paired, world-only, mutually exclusive with door mode | **done** |
| Distinct command with required action, classname, component and finite cursor | **done** |
| Nonnegative Enforce-range indices, including >511; one lookup | **done** |
| Dispatch, args, capability, whitelist/schema and readiness mapping | **done** |
| Reject invalid lookup while retaining valid default components | **wrong** — F1 |
| Use supplied cursor instead of a selection centre | **done** |
| Fail closed without capability; echo verification; preserve action errors | **done** |
| Component and action-result pruning ownership | **done** |
| Regressions for 0/876, missing pair, bool/negative index, invalid cursor, incompatible target and old PBO | **done** — mocked Python routing; default-component lookup semantics remain uncovered |
| In-game non-door effect and invalid-index refusal | **missing** — deferred |

### ITEM 7f27

| Specification bullet | Status |
|---|---|
| All specified owner sets | **done** |
| Preserve `accepted`, `found`, `object_id`, metadata and unknown fields | **done** |
| Preserve owner defaults; remove nonowner fields regardless of value | **done** |
| Comments, changelog and unresolved-field documentation | **done** |
| Table-driven additions, census and embedded/client result paths | **done** |
| Combined component-command ownership and source review | **done** |

## GATE GAP

F1 passes the gate because the component tests mock `call_bridge` or inspect source strings. They never exercise the engine lookup’s valid-default return value.

The gate also cannot establish Enforce compilation, actual `ActionTarget` behavior, action effects, door-state changes or native scan duration. The door regression executes a Python representation of the scan.

I reran five relevant modules using the project venv: **130 tests passed**. The initial default-Python attempt lacked `anyio`; that was an environment failure.

## PREMISE

The specification correctly requires retaining valid default components. The implementer’s API interpretation is incorrect: `REPORT.md:60` claims return values `0/1/2`, whereas the verified declaration specifies `-1/0/1`.

The supplied gate establishes no new measured offline failures, not complete acceptance. The deferred in-game cycle is an explicit remaining gate.

## NOT VERIFIED

- Enforce compilation, PBO build or deployed artifact.
- In-game component effects, invalid-index refusal or door acceptance.
- A real older PBO; refusal was verified through mocked capabilities and source.
- Full fast-tier rerun; its result is orchestrator-supplied.

No definite static compile blocker was identified. No files were modified; no commit was made.