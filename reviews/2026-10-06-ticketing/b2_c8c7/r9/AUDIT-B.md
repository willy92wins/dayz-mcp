# Audit c8c7 — state machine and cross-actor correlation (auditor B)

## FINDINGS

### B1 — A blocked storage classification is persisted and published as a measured `storage_rotated=false` (P1)

`tools/dayz_mcp/process_lifecycle.py:3024-3026`
```python
        self._record_storage_rotation(provisional, result)
        if not result.launch_allowed:
            return "storage_recovery_required"
```
`tools/dayz_mcp/process_lifecycle.py:3055-3058`
```python
            return
        provisional.storage_rotated = False
        provisional.storage_backup = None
        provisional.storage_reset_notice = None
```
`tools/dayz_mcp/dayz_test_storage.py:349-352`
```python
def _blocked(reason: str, seal: str) -> RotationResult:
    return RotationResult(
        launch_allowed=False,
        storage_rotated=False,
```

Reasoning. `prepare_storage` returns `_blocked(...)` (`launch_allowed=False`, `storage_rotated=False`) whenever the storage state cannot be classified: `journal_unreadable`, `journal_state_impossible`, `journal_ambiguous`, `journal_name_invalid`, `mission_not_enumerable`, `backup_name_collision` (dayz_test_storage.py:464-509, :630-637). `_rotate_storage_for_launch` calls `_record_storage_rotation` BEFORE the `launch_allowed` check, and `_record_storage_rotation` writes `False` for any result whose `storage_rotated` is falsy — byte-identical to the measured-reuse result (dayz_test_storage.py:665-675). In the blocked case nothing was measured; the record's own contract says "False: this launch measured no rotation" (process_lifecycle.py:1543-1545) and the spec says "false only for a measured nonrotation". The asymmetry shows it is unintended: a raised `StorageError` returns `storage_rotate_failed` recording nothing (unknown, :3003-3004), while the strictly less informative blocked verdict records a definite false. `_settle_failed_launch` persists the EXITED row with that false (process_lifecycle.py:3397-3402), `_note_storage_observation_locked` (:1819-1840) copies it into the durable `storage_observations` list, and `_decode_storage_observation` (dayz_test_tool.py:1928-1929) publishes it as `false` on the failed call's public result. Verified mechanically: the verbatim `_record_storage_rotation` applied to `_blocked("journal_state_impossible", ...)` yields (False, None, None), identical to a measured reuse. In the `journal_state_impossible` blocked case where `storage_1` and its backup both exist (dayz_test_storage.py:509), the old world HAS been set aside by the earlier crashed attempt, so record and result assert "measured reuse" over a world that is already gone.

Failure scenario. Plant a mission with an active rotation journal in an impossible state (journal present; `storage_1` and its backup both present). Launch with a changed modset. `prepare_storage` blocks with `journal_state_impossible`; the launch is refused with `storage_recovery_required`, but the public result of the call says `storage_rotated: false`, and `runs.json` keeps an EXITED row plus a `storage_observations` entry with `storage_rotated=false` — a fabricated measurement of reuse on a state the code itself declares unmeasurable.

Severity. P1: the launch is refused, so no live run is misdescribed, but the durable run record, the observation log, and the public result all carry a false "measured reuse" where the truth is unknown.

### B2 — Reattach and stop republish the run's stored rotation on a call that cannot rotate (P3)

`tools/dayz_mcp/dayz_test_tool.py:1805-1810`
```python
    if terminal.attempt_run_id is not None:
        storage_run_id: str | None = terminal.attempt_run_id
        storage_operation = terminal.launch_operation_id
    elif terminal.ok:
        storage_run_id = terminal.run_id
        storage_operation = None
```
the code's own contract, `tools/dayz_mcp/dayz_test_tool.py:1544-1545`:
```python
        # null: this call did not observe a rotation (no status, another run,
        # a launch that does not create storage, a legacy row). false: the
```

Reasoning. For `mode=client`, a `mode=offline` that extends an existing run, and `dayz_test_stop`, the terminal is ok naming that run, so `storage_run_id` is the run and `_storage_observation_from_status` returns the rotation recorded when the run was CREATED (the operation check passes because `operation_id` is None). Those launches never rotate (`_rotation_applies`, process_lifecycle.py:2947-2957), so the result says `storage_rotated=true` on a call that cannot have reset anything — contradicting the null contract above, which lists "a launch that does not create storage" as a null case.

Failure scenario. A run was created with a rotation. A later `dayz_test_run mode=client run_id=<that run>` succeeds; its result carries `storage_rotated=true` and the creation's `storage_backup`. A consumer reading the call result concludes this attach reset the world.

Severity. P3: it is the same run's fact, not another run's, but the per-call semantics are wrong and easy to misread.

## STATE TABLE

Fields S/B/N = storage_rotated / storage_backup / storage_reset_notice. "kept" = carried unchanged from the previous row state.

| transition (set by) | S/B/N |
|---|---|
| absent -> STARTING, add before rotation (:3757-3758) | null,null,null |
| STARTING -> STARTING, post-rotation replace (:3912-3916) | true+backup+notice, or false (measured reuse; also the fabricated false of B1), or null,null,null (ambiguous pending journal, :3017-3021) |
| STARTING -> RUNNING (:4017-4021), launch-intent recovery (:4364-4366) | kept |
| STARTING -> EXITED, settle without previous (:3397-3402) | kept: true survives a failed launch; B1 false; unknown stays unknown |
| STARTING/STOPPING -> UNRECONCILED, settle (:3416-3417) or reload (:1994-1998) | kept |
| RUNNING -> STOPPING -> EXITED (:4915, :5103-5106) | kept |
| RUNNING -> RUNNING_IDLE, owner release (:1992, :6348, :6406, :6461, :7807) | kept |
| reapable -> EXITED, reap (:7140-7143) | kept |
| EXITED -> pruned on load (:1729-1758) | bool rows moved to `storage_observations` (null dropped), row deleted |
| extension, mode=client / offline with run_id (:3732-3760) | clone of existing, kept; never re-measured (`_rotation_applies` false, :2947-2957) |
| idempotent retry, same new_run_id+op over a RUNNING row (:3601-3635) | untouched |
| retry over an EXITED row | `launch_identity_conflict`; no fields written |

The only contradiction reachable is B1. No transition drops a measured true from a live row: every mutation goes through the record clone (`_clone`, :1784-1785), `stop`/`reap`/`ack`/`admin_reconcile` mutate state/owner/processes in place and replace, and reload keeps the fields (`from_payload` :1550-1577). True is written only from a measured rotation or a validated completed journal (`_pending_completed_rotation` :138-194 requires phase `marker_published`, matching seal, and an existing backup directory; more than one match yields unknown, never a guess).

## ANGLES NOT APPLICABLE

- Angle 2 (cross-actor correlation): no finding. Every worker failure path preserves attempt identity (`_failure_after_cleanup` :153-181, outer handlers :655-667 and :858-868, terminal writer app_main.py:55-79 and :341-372, cancellation included); the parser treats an old terminal without the fields as unknown (dayz_test_tool.py:685-696); `_storage_observation_from_status` (:1944-1984) matches the exact run id, refuses ambiguous rows, checks the operation when both sides name one, and uses the durable observation list only for the same run id. The `run_id=None` normalization at :1797-1798 runs first but leaves `attempt_run_id` intact. No combination attributing another run's rotation, or dropping this call's own, was constructable.
- Angle 4 (concurrency): no finding. Manifest reads clone under the store lock; writes are atomic and roll back both runs and observations on failure (:1851-1886); status reads take no lifecycle lock but correlate by exact run id, so another session's concurrent launch cannot be attributed to this call.
- Preflight: no finding. Its ok terminal names no run, so `storage_run_id` is None and the early refusals build `_compact_result` without the fields: null.

## NOT VERIFIED

- No daemon, DayZ process, or MCP tool was started (per boundaries). The end-to-end trace is from source reading. Baseline: `tests.test_storage_reset_visibility` run with the Windows venv — 17 tests pass; they do not cover the blocked-rotation path (B1) or the attach/stop republish (B2).
- `mode=all` failing in its client stage after the server stage was traced, not executed.
- The sealed bundle actually installed on disk was not inspected; only the sources under tools/.
- The absent-storage-without-journal launch (`should_rotate` -> seal_only, recorded false while the engine will create a fresh world) was treated as an honest measured nonrotation per the record comment; the spec leaves the H11 "new storage accredited" definition open, so it is not filed as a defect.
