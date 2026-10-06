# gpt-6.1-sol review, b2_c8c7, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Successful cleanup hides a real reset.**  
`tools/dayz_mcp/dayz_test_tool.py:1748`, `:1762`, `:1821`.

Executable scenario: existing `storage_1` has seal B; launch with seal A rotates it; spawning fails; worker cleanup successfully stops the failed run. The real worker clears its run ID at `tools/dayz_mcp/dayz_test_worker.py:144`. The new extractor therefore receives `storage_run_id=None`, even when lifecycle status contains the exact failed row with `storage_rotated=true`.

An in-memory execution of `_start` confirmed calls `start → start → stop` and the resulting `run_id=None, cleanup_degraded=false`. Passing that valid terminal through `_execute_request`, with the true rotation available in status, produced:

```json
{
  "status": "failed",
  "run_id": null,
  "storage_rotated": null,
  "storage_backup": null,
  "storage_reset_notice": null
}
```

**Suggested fix:** provide exact attempt-to-run correlation for successfully cleaned failures, independently of the terminal’s cleanup run ID. Preserve unrelated-run isolation.

**F2 — P2 — Reload removes the failed attempt’s persisted observation.**  
`tools/dayz_mcp/process_lifecycle.py:1694`, `tools/tests/test_storage_reset_visibility.py:315`.

Executable scenario: mismatch rotation succeeds, spawning raises `OSError`, settlement persists the run as `EXITED` with `storage_rotated=true`; restart the daemon before retrieving its status. Manifest loading prunes that run. `/lifecycle/status` consequently has no matching row, and exact-run extraction returns `(None, None, None)`.

The actual prune function, exercised with an in-memory store containing this row, removed it. The new regression explicitly expects that removal. Recovering the notice onto a later retry does not preserve the original attempt’s observation across reload.

**Suggested fix:** retain a durable, bounded observation accessible by the original run ID after terminal-row pruning.

**F3 — P2 — Post-rotation persistence failure raises an undeclared exception.**  
`tools/dayz_mcp/process_lifecycle.py:3822`, `:3836`.

Executable scenario: rotation succeeds; the newly added `manifest.replace(provisional)` raises `OSError`; failed-launch settlement subsequently succeeds. The code sets `storage_error="manifest_failed"` and then indexes `_STORAGE_ROTATE_HINTS`, which contains only `storage_rotate_failed` and `storage_recovery_required` (`:1111`).

Executing the actual changed block with this failure injection produced:

```text
Settlement returns: manifest_failed rotated: True
Actual changed pre-spawn block raised: KeyError 'manifest_failed'
```

The caller loses the structured settlement response after storage has already rotated.

**Suggested fix:** handle `manifest_failed` separately or use a lookup that permits errors without storage hints.

## SPEC COVERAGE

| Specification bullet | Assessment |
|---|---|
| Daemon/tool change; no PBO or worker reseal | **done** — worker and storage module hashes match base and the lock file. |
| Carry rotation into provisional run before spawning | **done** — `process_lifecycle.py:2928`, `:3813`. |
| Validated optional metadata; preserve success, failure, cloning and reload; legacy unknown | **wrong** — validation/cloning implemented; failed rows disappear on reload, F2. |
| Publish lifecycle status and extract by exact run ID | **done** for available rows — both projections use `_projected_run`; extractor requires exactly one matching row. |
| Always include three fields; unavailable null; measured nonrotation false | **done** — `dayz_test_tool.py:1517` and `:1886`. |
| Preserve true facts on later failures; fetch before normalization | **wrong** — works for retained-ID failures, misses successful cleanup, F1; persistence failure also hits F3. |
| Retain history after acknowledgement; recover completed-journal notice before replacement storage exists | **done** for retained rows and matching journal retries; reload limitation remains F2. |
| Do not claim acknowledgement/marker proves CE restoration | **done** — no such accreditation claim introduced. |
| Legacy compatibility; downgrade leaves storage/backups/journals unchanged | **done by inspection** — old parser ignores added keys; downgrade execution unverified. |
| Required regressions | **wrong** — successful paths covered, but ordinary cleaned failure and original failed-run reload are not correctly covered. |
| Do not silently define H11 accreditation | **done** — remains explicitly deferred. |

## GATE GAP

The supplied gate passed with nine new tests and zero new fast-tier failures. It misses:

- Successful cleanup: the public failure helper forces `cleanup_degraded=not ok` at `test_storage_reset_visibility.py:202`, retaining an ID that ordinary cleaned failures lose.
- Failed-run reload visibility: the test expects the original record to disappear, then checks only a new retry.
- Failure of the added pre-spawn manifest write and the resulting missing hint key.

## PREMISE

The underlying c8c7 omission is confirmed. However, retaining the ID before tool normalization is insufficient: the sealed worker can discard it **before the tool receives the terminal**. The daemon/tool-only constraint requires another exact-attempt correlation mechanism; it does not excuse F1.

Completed-journal recovery in lifecycle code is a reasonable placement under the no-reseal constraint. It does not satisfy preservation of the original failed run across reload.

## NOT VERIFIED

- Did not rerun the filesystem-writing unittest suite in the read-only sandbox; gate results are orchestrator-supplied.
- Ran read-only, in-memory Python probes for F1–F3.
- No real DayZ launch, daemon restart, disk-failure experiment, downgrade execution, or CE restoration verification.
- No files modified.

