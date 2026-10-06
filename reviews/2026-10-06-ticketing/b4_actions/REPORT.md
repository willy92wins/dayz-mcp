## CHANGES

- `addon/scripts/5_Mission/MCPClientBridge.c` `ACTION_USE_DOOR_COMPONENT_CAP`: 512 → 2048. Comment states this covers a sole match at 876 and is not a component-count API. First-match scan and the `doorstwin` skip are unchanged.
- `MCPClientBridge.c` `CLIENT_POLL_CAPS` and `Dispatch`: new client command `action_use_component` calls `DispatchActionUse`. Caps stay sorted and split under the 200-character literal ceiling.
- `MCPClientBridge.c` `DispatchActionUse`: component mode echoes `component_index`, requires a classname and a finite `cursor_pos` (`ArrayToVector`), performs one `GetActionComponentNameList` lookup (return ≤ 0 → `component_not_found`; 1 or 2 kept, including a valid default with no selection name), and builds `ActionTarget` with the supplied cursor. No index scan.
- `addon/scripts/5_Mission/MCPMessages.c` `MCPArgs`: `component_index` and `cursor_pos`. `MCPResult` comment now says `door_index` is only `action_use_door` and `component_index` is both door and component commands. The existing phrase `the command name, not these fields, is` stays on one line.
- `tools/dayz_mcp/server.py` `action_use`: public `component_index` and `cursor_pos`. Both required, world-only, mutually exclusive with `door_index`, classname required, index in 0..2147483647. Routes to wire command `action_use_component`. Missing capability or a mismatched echo raises `component_not_supported` and does not call the bridge when the capability is absent. Instructions and the tool description name the new mode. Existing door routing and errors are unchanged.
- `tools/dayz_mcp/loopback.py`: `action_use_component` in `CLIENT_COMMANDS` and `_COMMAND_ARG_SCHEMAS` (required action, classname, component_index, cursor_pos; optional pos, radius). `action_use` still rejects a stray `component_index`.
- `tools/dayz_mcp/bridge_readiness.py` `_BRIDGE_COMMAND_TOOLS`: `action_use_component` → `action_use`.
- `tools/dayz_mcp/result_prune.py` `OWNED_SCALAR_FIELDS`: verified owners from the spec, plus `action_use_component` on `action`, `target`, `distance`, `started`, and `component_index`. `accepted`, `found`, and `object_id` stay unmanaged; comments name the untyped `result.accepted = job.weapon_action.accepted` write and the broad `PostJobSuccess` writes of `found` / `object_id`.
- `CHANGELOG.md`: one Fixed line under the existing Unreleased heading.
- `tools/tests/fixtures/bridge_capabilities_v1.json`: client map entry for the new command (the fixture must match `_BRIDGE_COMMAND_TOOLS`).

## TESTS ADDED

- `tools/tests/test_action_use_component.py`
  - `DoorComponentCapTest.test_scan_finds_876_a_low_index_and_no_match` — runs the first-match scan. A sole match at 876 is found with cap 2048 and missed with cap 512. Fails on unmodified code because the constant is 512, so the 876 case returns -1 (`door_component_not_found`).
  - `ActionUseComponentToolTest.test_index_zero_and_876_forward_on_the_component_command` — wire command, both indices, cursor forwarded.
  - `test_omitted_component_stays_on_action_use`
  - `test_bool_negative_cursor_target_and_door_are_refused`
  - `test_old_pbo_is_component_not_supported_without_a_call` — fails on unmodified code: there is no capability check, and the call either rejects the extra arguments or sends plain `action_use`.
  - `test_echo_mismatch_is_component_not_supported`
  - `test_bridge_component_not_found_is_not_rewritten`
  - `ActionUseComponentContractTest.test_command_is_advertised_on_the_client_wire_and_the_map`
  - `test_bridge_looks_up_one_component_and_uses_the_supplied_cursor`
- `tools/tests/test_result_prune.py`
  - `GenericScalarOwnershipTest.test_each_added_field_is_kept_for_owners_and_removed_from_nonowners` — fails on unmodified code: `prune_unfilled_fields("vehicle_telemetry", {clicked: 0, ...})` keeps `clicked`.
  - `test_residual_fields_survive_on_a_command_that_does_not_write_them`
  - `test_census_owners_match_the_table`
  - `PruneResultPathTest.test_embedded_wait_and_probe_drop_unrelated_click_defaults` — `Runtime.wait_for_result` and `probe_bridge_result`.
  - `test_client_wait_and_probe_drop_unrelated_click_defaults` — `ClientRuntime._await_result` and `probe_bridge_result`.

Pins updated because the new command and the new owners contradict them (stated here, not weakened away):

- `test_result_prune.py`: `phase` is no longer kept on `object_delete`; door/component owner assertions; `started`/`distance` follow the action owners; the vehicle-owner equality ignores non-vehicle fields.
- `test_owned_scalar_census.py`: the door-gate mutant now also names `action_use_component`. `test_every_owner_is_a_whitelisted_bridge_command` includes `EXEC_COMMANDS` because `exec_enforce` writes `sent` and is not in `SERVER_COMMANDS`.
- `test_validate_command_args_table.py`: cases for `action_use_component` (`test_every_schema_has_table_cases` requires them).
- `test_command_validation_coverage.py`: minimal valid args for the new command.
- `test_lote2_numeric_boundary.py`: census length 166 → 168 for `component_index` (int) and `cursor_pos` (vector).

## GATE OUTPUT

```
--- enforce linter: errors=2 baseline=2 new=0
--- tests.test_action_use_component: rc=0
Ran 10 tests in 0.946s

OK
--- tests.test_result_prune: rc=0
Ran 24 tests in 1.506s

OK
--- fast tier: ran=5603 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

## DEVIATIONS

- Invalid component lookup is `GetActionComponentNameList` return < 0 (`component_not_found`). Return 0 (valid default) and 1 (valid named) are kept (`object.c:197-198`). The selection centre is not read.
- A bool `component_index` is refused by the tool's `StrictInt` schema (`int_type`) before the function body. Negative index, missing pair, bad cursor, non-world target, and `door_index` together still raise `bad_args` in the function.
- `component_not_found` is raised by `call_bridge` on `ok` false, before the echo check, same as other action errors. The tool does not rewrite that exception.
- Line numbers in the brief for the residual writes do not match this snapshot. The writes are `MCPClientBridge.c` `result.accepted = job.weapon_action.accepted` and `MCPBridge.c` `PostJobSuccess` (`result.object_id = job.id`, `result.found = true`).

## NOT VERIFIED

- No PBO build and no in-game run (forbidden here). Orchestrator checks after a PBO rebuild, no launcher reseal:
  - 6d21: use the reported window door via `action_use(..., door_index=5)`. Confirm the result `component_index >= 512`. Read the door again with `object_doors` and confirm the state changed. Time a scan that still misses (component past 2048, or a door index with no component) and record that duration.
  - fde3: call `action_use` with `component_index` and `cursor_pos` on a non-door action that requires a real component, and confirm the effect. Call again with an index that object does not have and confirm `component_not_found`. Point an older PBO (no `action_use_component` in client caps) at the same tool and confirm `component_not_supported` with no enqueue.
- Enforce compile of `GetActionComponentNameList`'s int return was not run. The offline linter reports no new error (2 errors, same as origin/main).
- `accepted`, `found`, and `object_id` were not given owners, by spec. Their stale zeros still appear on unrelated successes.

## ROUND 2 FIXES

- **F1:** `DispatchActionUse` now rejects only `componentState < 0`. Return `0` (valid default component) and `1` (valid named component) continue into the existing action start. Comments cite `object.c:197-198`. Regression: `ActionUseComponentContractTest.test_lookup_keeps_default_zero_and_named_one_and_rejects_minus_one` (returns `-1`, `0`, and `1`) and the source no longer contains `componentState <= 0`.
