## FINDINGS

Method: every writer/reader of the rotation state was traced in source; the regression suite `tests.test_storage_reset_visibility` passes 17/17 under the Windows project venv, and five executable scenarios (saved under `_scratch/`, deleted with this audit) were run against the real code. Snippets are literal.

### C1 — P1 — Cross-seal journal recovery publishes a marker for a seal whose engine never wrote the storage; the next same-seal launch then reuses a foreign-modset world

```python
tools/dayz_mcp/dayz_test_storage.py:493-500
    if not storage_present and backup_present:
        # The case the design names: killed between the rename and the seal.
        # The journal's new_seal is the authority; the old marker is not.
        # The engine will create the new tree on the next start.
        backup, marker_backup = _finish_rotation(mission, journal_path, document)
        return RotationResult(
            launch_allowed=True,
            storage_rotated=True,
```
and the caller returns without reclassifying for the current seal:
```python
tools/dayz_mcp/dayz_test_storage.py:639-644
    if active:
        recovered = _reconcile_journal(
            mission, active[0][len(JOURNAL_PREFIX) : -len(JOURNAL_SUFFIX)], seal
        )
        if recovered is not None:
            return recovered
```
Reasoning: in this branch `_reconcile_journal` never compares the journal's `new_seal` with the current launch's `seal`. `_finish_rotation` publishes the JOURNAL's seal as the mission marker, and `prepare_storage` returns directly, so a launch with a different modset proceeds without the `should_rotate` classification and the engine writes a new world under a marker naming the old journal's seal. `should_rotate` trusts any `present_valid` marker (`dayz_test_storage.py:222-223`), so the lie is never detected. Contrast: `_pending_completed_rotation` (`process_lifecycle.py:162-167`) does match `new_seal == seal`; the active-journal path does not. Executed scenario (scratch `audit_scenario4.py`): plant storage with an old-seal marker; build the exact post-crash state (journal `phase=storage_moved`, `new_seal=A`, tree renamed to backup); `prepare_storage(seal=B)` returns the recovered rotation and the marker on disk becomes A; simulate the B engine writing `storage_1`; then `prepare_storage(seal=A)` returns `decision=reuse, reason=seal_matches` — a seal-A engine is handed a world written by modset B. That is exactly the poisoning incident this module exists to prevent (fb-20260829-115147-4407), reached through the recovery path the design treats as first-class (`CrashBetweenEveryPairOfIoTest`). Failure scenario: daemon killed between the storage rename and the marker publish, next launch with a changed modset, later same-seal launch reuses the foreign world and reports `storage_rotated: false` (measured reuse) while the server bleeds `Scripted variables corrupted`. Fix direction: after `_finish_rotation`, fall through to classification for the current seal (publish its marker when storage is absent), and/or attribute the recovered result only when `new_seal == seal`.

### C2 — P2 — A rotation-refused launch publishes `storage_rotated: false` ("measured reuse") on the row, in the observation log, and in the public result

```python
tools/dayz_mcp/process_lifecycle.py:3024-3026
        self._record_storage_rotation(provisional, result)
        if not result.launch_allowed:
            return "storage_recovery_required"
```
with
```python
tools/dayz_mcp/process_lifecycle.py:3056-3058
        provisional.storage_rotated = False
        provisional.storage_backup = None
        provisional.storage_reset_notice = None
```
Reasoning: every `_blocked(...)` result (`dayz_test_storage.py:349-360`: journal_unreadable, journal_ambiguous, journal_state_impossible, backup_name_collision, mission_not_enumerable, ...) carries `storage_rotated=False`, and `_record_storage_rotation` runs before the `launch_allowed` check. The method's own contract and the tool's say false is "only the measured non-rotation" (`process_lifecycle.py:3040`, `dayz_test_tool.py:1544-1546`); a refused launch measured nothing. `_settle_failed_launch` clones the provisional into the EXITED row (`process_lifecycle.py:3398-3402`), `replace` logs the False into `storage_observations` (`process_lifecycle.py:1877-1878`), and the failed call's public result reads it back. Executed scenario (scratch `audit_scenarios.py`, Scenario 1): mission with one malformed active journal → `prepare_storage` returns blocked → `start_run` fails with `storage_recovery_required` → row `{'state': 'EXITED', 'storage_rotated': False}`, `storage_observations: [{..., 'storage_rotated': False}]`, public result `{'status': 'failed', 'error_code': 'storage_recovery_required', 'storage_rotated': False}`. An operator or automation reading "false = measured reuse" is told the world was kept when the launch never ran and the journal is unreadable. Fix direction: record nothing (unknown) when `not result.launch_allowed`, or make `_blocked` carry `None`.

### C3 — P3 — The durable observation log cannot bind an attempt: `_operation_matches` on it is vacuous

```python
tools/dayz_mcp/dayz_test_tool.py:1982-1984
    if len(found) != 1 or not _operation_matches(found[0], operation_id):
        return None, None, None
    return _decode_storage_observation(found[0])
```
Reasoning: `_note_storage_observation_locked` (`process_lifecycle.py:1826-1831`) persists only `run_id/storage_rotated/storage_backup/storage_reset_notice`; `"launch_operation_id" not in row` makes `_operation_matches` (`dayz_test_tool.py:1911-1917`) return True unconditionally. The orchestrator decision "a failed call is matched only by exact attempt identity" therefore holds only while the row is live; after EXITED-row pruning, any failed call whose terminal names that run id reads a rotation measured under a different operation. Executed: `_storage_observation_from_status` with a log row and a different operation id returns the True fact. Harm is bounded — the fact is about the run's world and run ids are uuid4 — hence P3.

### C4 — P3 — Over-bound or future-shaped `storage_observations` rejects the entire manifest (M1 judgment: reject-all is wrong for a diagnostic list)

```python
tools/dayz_mcp/process_lifecycle.py:231-232
    if not isinstance(value, list) or len(value) > _STORAGE_OBSERVATION_BOUND:
        raise ValueError("invalid_run_manifest")
```
Reasoning: the list is diagnostic; the run rows remain the source of truth and the same fact stays readable while a row exists. A future version with a larger bound, or a hand edit, makes `_load` raise, and daemon start then either restores an older checkpoint (losing newer rows, `daemon.py:452-478`) or fails with `lifecycle_manifest_checkpoint_missing`. The strictness extends to `notice != RESET_NOTICE` (`process_lifecycle.py:276-280`): any future notice string bricks the store. Executed: a `runs.json` with 33 valid observations → `RunManifestStore` raises `ValueError: invalid_run_manifest`. Judgment: truncate to the newest 32 or drop unknown-shaped entries instead of rejecting all; storage, backups and journals are untouched either way (downgrade compatibility M0 holds).

### C5 — P3 — `_valid_journal` accepts non-plain backup names; active-journal recovery joins them raw and renames the mission marker outside the mission

```python
tools/dayz_mcp/dayz_test_storage.py:406-409
        or not isinstance(document.get("storage_backup"), str)
        or not document["storage_backup"]
        or not isinstance(document.get("marker_backup"), str)
        or not document["marker_backup"]
```
and the raw join:
```python
tools/dayz_mcp/dayz_test_storage.py:445-446
    marker_backup = str(document["marker_backup"])
    marker_backup_path = ntpath.join(mission, marker_backup)
```
Reasoning: `_plain_storage_backup_name` exists and guards the completed-journal path (`process_lifecycle.py:168-172`) and the run-record path (`process_lifecycle.py:3047-3051`), but the active-journal recovery validates neither name. A corrupted or hand-edited journal with `marker_backup: "..\\escaped-marker.json"` makes `_rename_strict` move the mission's marker into the parent directory; a fresh valid marker is then republished inside, so the tree stays launchable. Executed: the escaped file appears outside the mission carrying the old marker's content, and the result's `storage_marker_backup` carries the traversal name into the audit row (`process_lifecycle.py:3086-3088`). No world bytes move (the storage backup name must still resolve for the branch to fire); P3 validation gap on persisted state.

## TRACE TABLE

| Writer | Readers | Verdict |
|---|---|---|
| `RunRecord.storage_rotated/backup/notice` — only `_record_storage_rotation` (`process_lifecycle.py:3052-3058`) via `_rotate_storage_for_launch` (3908-3916), persisted by `add`/`replace` (3758/3760/3916); carried by every settlement/recovery clone (`from_payload(asdict)`: 3398-3402, 3416-3419, 4017, 4364); the single constructor call (3735) leaves null | `status()`/`public_status()` per-run rows (`_projected_run` 2293-2296); tool live-row read (`_storage_observation_from_status` 1956-1973, including failed calls via the fetch at 1859-1864); `_persist_locked` asdict (1791); reload (`_load` → `from_payload`) | Sound except C2 (blocked → false) |
| `storage_observations` list — `_note_storage_observation_locked` (1819-1840) from `add`/`replace`/`_prune_exited_on_load` (1745-1746); persisted 1793-1794; validated on load (1769-1771) | `status()` 7278-7280 and `public_status()` 7303-7305; tool log path 1974-1984 | Bounded (32) and deduped; C3 (no operation id), C4 (reject-all) |
| `RotationResult` — `prepare_storage`/`rotate_storage`/`_reconcile_journal`/`_pending_completed_rotation` | `_record_storage_rotation`; `_audit_storage_rotation` (3060-3096, skipped for `pending_completed_rotation` replays, 3029-3030) | C1 (cross-seal), C2 |
| Active journal — written/advanced by `rotate_storage` (576-578), scanned by `_active_journals` (512-535), reconciled (abort→complete 489-492, finish 493-508) | launch blocking on unreadable/ambiguous/invalid names (630-637) | Fail-closed OK; never cleaned (design: v1 deletes nothing) |
| Completed journal — `_complete_journal` (427-431, 487, 491); read by `_pending_completed_rotation` (138-194: seal-matched, backup dir must exist) | retry recovery → run record (3017-3023) | Sound; two completed rotations to one seal → unknown (covered by suite) |
| Marker — published (339-342, 458, 664), read (308-327, 222-223, 485) | the rotation decision matrix | Trusted whenever `present_valid` — C1 makes it lie |
| Backups — rename only (577, 456), never deleted (design); existence checked at 476-477 and 169-172 | name carried onto the run record, audit row and public result | OK |
| Run rows — `add`/`replace`/`_persist_locked` (1787-1813, checkpoint rollback), prune on load (1729-1758, preprune backup 1693-1727), recovery (1979-2011, 4334-4380), reap/stop/close (7108-7151, 4838-5072, 6959-6969), `repair_manifest_recovery` (6979-7005) | status projections; tool exact-run read (failed calls included); `_run_unknown_to_store` (1797-1798 keeps attempt context) | Fields survive every transition (all rewrites are clones); C2 note above |
| Public result keys — `_compact_result` (1486-1488, 1548-1552; always present, backup/notice only when rotated is True) | MCP consumer | Matches the spec |

## NOT VERIFIED

- No daemon, DayZ process or MCP tool was started (boundary). All execution was unit-level: the regression suite (17/17 OK) and five scratch scenarios under the Windows project venv; the Linux REPL cannot import `dayz_mcp` (Windows-only imports), consistent with prior measurement.
- The shipped launcher bundle's reseal state (whether the sealed worker copy carries `attempt_run_id`) is a deployment fact not decidable from this tree; the older-worker → null paths are code-verified only.
- Concurrency: single-writer behaviour of `_operation_lock` around `runs.json` is reasoned, not race-executed; `atomic_write_bytes` and checkpoint internals are trusted as pre-existing.
- Pre-existing plumbing not re-audited: secure_launcher stdout demultiplexing, `window_close`, audit-writer internals, `runtime_state`.
- The txid corner where a same run_id+seal retried after two crash-aborted rotations would hit `backup_name_collision` is unreachable through the tool because a failed launch always mints a new run id; analysis only, not executed.
