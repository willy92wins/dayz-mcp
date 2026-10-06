# gpt-6.1-sol review, b2_c8c7, round 4 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

Round-3 F1 is closed: correlation precedes `client_dead_after_ack`, and its regression passed in memory. Round-3 F2 is only partially closed. Three P2 issues remain; no P1 found.

### F1 — P2 — Degraded client failure still drops the allocated operation

**Locations:** `tools/dayz_mcp/dayz_test_worker.py:158`, `:606`, `:855`.

**Executable scenario:** Run `mode="all"`. Server creation and acknowledgement succeed with:

- Run: `12345678-1234-4234-8234-1234567890ab`
- Operation: `87654321-4321-4321-8321-ba0987654321`

Readiness succeeds. Client start returns `{"error":"steam_cleanup_degraded"}`.

The actual worker and terminal writer emit:

```json
{
  "attempt_run_id": "12345678-1234-4234-8234-1234567890ab",
  "cleanup_degraded": true,
  "error_code": "steam_cleanup_degraded",
  "exit_code": 2,
  "ok": false,
  "run_id": "12345678-1234-4234-8234-1234567890ab"
}
```

`launch_operation_id` is missing. `_failure_after_cleanup` calculates the inherited operation but returns the original degraded error before applying it. This bypasses operation comparison in the tool and leaves the allocated-operation requirement unmet.

**Suggested fix:** Preserve the degraded error’s cleanup semantics while enriching it with the retained attempt context before returning.

### F2 — P2 — Pending recovery can publish an older rotation’s backup

**Location:** `tools/dayz_mcp/process_lifecycle.py:170`.

**Executable state:** The mission has completed a historical B→A rotation, subsequently A→B, and finally another B→A rotation. Replacement `storage_1` is absent after the latest launch fails. Both B→A backups and their valid completed journals remain:

| Rotation | Transaction ID | Backup |
|---|---|---|
| Historical | `fcc0562339f8981e647102f5f70222b3` | `storage_1.modset-20260901-000000-bbbbbbbb` |
| Latest | `0caf8fe1f0b81be380759ed10e5fcc3b` | `storage_1.modset-20261006-000000-bbbbbbbb` |

Both journals match seal A. Recovery sorts transaction hashes and selects the historical journal.

A read-only probe through `_rotate_storage_for_launch` and actual `_execute_request` returned:

```json
{
  "storage_rotated": true,
  "storage_backup": "storage_1.modset-20260901-000000-bbbbbbbb",
  "storage_reset_notice": "mission_world_and_character_reset"
}
```

The pending reset belongs to the October backup. Transaction hashes do not encode chronology, so the public result can identify the wrong saved world.

**Suggested fix:** Correlate recovery with the authoritative pending transaction; treat unresolved ambiguity as unavailable rather than selecting by hash.

### F3 — P2 — The no-reseal delivery leaves failed-call visibility unfixed

**Locations:** `tools/build_native_launcher.py:344`, `tools/native-launchers/dayz-test-v1/src/launcher.cpp:1236`, `tools/dayz_mcp/dayz_test_tool.py:1805`.

The launcher executes its bundled `app.pyz`; regenerating `packaged-modules.lock.json` does not update that archive. This implementation depends on modified worker and terminal-writer sources.

**Executable scenario:** Retain the base worker, as required by the no-reseal premise. Launch `mode="all"` over mismatched storage, acknowledge the server, then return `readiness_timeout` and successfully clean up. The base worker supplies neither cleanup `run_id` nor `attempt_run_id`.

Running that worker source against the new tool, with the exact run’s persisted true rotation available, produced:

```json
{
  "status": "failed",
  "error_code": "readiness_timeout",
  "storage_rotated": null,
  "storage_backup": null,
  "storage_reset_notice": null
}
```

**Suggested fix:** Resolve the scope conflict explicitly: provide daemon/tool-only correlation compatible with the existing worker, or revise the constraint and rebuild/reseal the worker.

## SPEC COVERAGE

| Specification bullet | Result |
|---|---|
| Carry rotation into the provisional run before spawn | **done** |
| Validate and preserve metadata through success, settlement, cloning and reload; legacy unknown | **done** in source; retired observations are bounded to 32 |
| Publish both status projections and correlate by exact run ID | **done**, with incomplete operation transport in F1 |
| Always include the three fields; distinguish null from measured false | **done** |
| Preserve true facts on later failures and fetch failed-attempt status | **wrong** under the no-reseal delivery: F3 |
| Retain history after acknowledgement; recover pending notice on retry | **wrong** for repeated matching-seal journals: F2 |
| Avoid claiming acknowledgement/marker proves CE restoration | **done** |
| Legacy/downgrade compatibility; leave storage, backups and journals unchanged | **done** by source inspection; downgrade execution unverified |
| Requested regression scenarios | **done** as source tests; production packaging and identified edge cases remain uncovered |
| Leave H11 accreditation undefined | **done** |
| Daemon/tool scope without worker reseal | **wrong**: failed-call behavior depends on changed sealed-worker sources |

## GATE GAP

The gate can pass while testing updated source instead of the unchanged sealed worker. The new tests also miss degraded client rejection after server acknowledgement and multiple completed rotations to the same seal. Those gaps produced the failures above.

## PREMISE

The no-worker-reseal constraint conflicts with the chosen transport change. Updating the source lock does not resolve that conflict.

`REPORT.md` contains round-3 counts; I used the supplied round-4 gate as the orchestrator’s measured evidence.

## NOT VERIFIED

- Full focused suite locally: 16 tests could not create temporary directories under the read-only sandbox. The two worker-context tests passed.
- F1’s public-normalization regression passed separately without filesystem setup.
- No in-game execution, deployed launcher inspection, full-tier rerun, actual downgrade run, or mutation-test rerun.
- Counterexamples used actual functions with controlled in-memory inputs.
- No files modified; no commit or handoff file created.

