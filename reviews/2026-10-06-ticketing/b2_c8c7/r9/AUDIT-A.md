# Data audit A — c8c7 persistence, recovery paths, input bounds

Scope: `tools/dayz_mcp/process_lifecycle.py`, `tools/dayz_mcp/dayz_test_storage.py`,
`tools/dayz_mcp/runtime_state.py`, `tools/dayz_mcp/dayz_test_tool.py`, `tools/dayz_mcp/daemon.py`.
Verified by execution with the project venv: `_record_storage_rotation` on a blocked result,
`_pending_completed_rotation` (single + ambiguous), `_storage_observations_from_payload` bounds,
`RunRecord.from_payload` hostile fields, a two-failure `_persist_locked` repro, and the pure
tool-side decoders. `tests.test_storage_reset_visibility` (17 tests) passes.

## FINDINGS

### A1 — A refused storage classification is persisted as a measured `storage_rotated: false` (P2)

`_rotate_storage_for_launch` records the result on the provisional run BEFORE checking whether
prepare_storage allowed the launch. Every `_blocked(...)` result (dayz_test_storage.py:349-352)
carries `storage_rotated=False`, so a launch refused by the storage subsystem — `journal_unreadable`,
`journal_ambiguous`, `journal_name_invalid`, `journal_state_impossible`, `backup_name_collision`,
`mission_not_enumerable` — gets a *measured non-rotation* written onto its run record:

```python
# tools/dayz_mcp/process_lifecycle.py:3024-3026
        self._record_storage_rotation(provisional, result)
        if not result.launch_allowed:
            return "storage_recovery_required"
```
```python
# tools/dayz_mcp/process_lifecycle.py:3056-3058
        provisional.storage_rotated = False
        provisional.storage_backup = None
        provisional.storage_reset_notice = None
```
```python
# tools/dayz_test_storage.py:349-352
    return RotationResult(
        launch_allowed=False,
        storage_rotated=False,
```

For a creating launch `previous is None` (process_lifecycle.py:3727-3731), so `_settle_failed_launch`
persists the provisional as an EXITED row with `storage_rotated=False`
(process_lifecycle.py:3397-3404); the sealed worker's pre-admission rejection carries
`attempt_run_id` (dayz_test_worker.py:616-621), so `_execute_request` fetches status and
`_decode_storage_observation` (dayz_test_tool.py:1928-1929) publishes `false`.

Executable scenario: a rotation crashes after `_rename_strict(storage_1, backup)` and its active
journal is then unreadable (>64 KiB, truncated, bad JSON). Next launch: `prepare_storage` returns
`_blocked("journal_unreadable")`; the call fails with `storage_recovery_required` yet the public
result says `storage_rotated: false` — "measured reuse" per the spec — while the world may actually
be set aside mid-transaction (reset state unknown, which the spec reserves `null` for). The caller is
told the wrong thing about whether the mission was reset. Severity stays P2 only because the same
response carries `storage_recovery_required`; the flag itself is a false measurement. Same wrong
`false` for the allowed-but-unreadable-completed-journal case (`_pending_completed_rotation`
process_lifecycle.py:158-161 skips an unreadable completed journal, the launch proceeds via
`seal_only`, and the run measures `false` while an earlier reset is only provable from the skipped
journal) — there the per-run semantics make it defensible; in the blocked case there was no
measurement at all.

### A2 — `_persist_locked` rollback can leave memory and disk disagreeing (P3)

If the checkpoint fails after the new bytes landed, the rollback rewrites `previous`; if that rewrite
also fails, the raised error makes `add`/`replace`/`_prune_exited_on_load` restore memory while the
disk keeps the *new* content:

```python
# tools/dayz_mcp/process_lifecycle.py:1803-1813
        atomic_write_bytes(self.paths.runs_path, raw)
        if self._checkpoint is not None:
            try:
                self._checkpoint(raw)
            except Exception:
                atomic_write_bytes(self.paths.runs_path, previous)
                try:
                    self._checkpoint(previous)
                except Exception:
                    pass
                raise
```

Repro executed: checkpoint raises from its 2nd call, `atomic_write_bytes` raises on the rollback
call; `store.add(run)` raises, `store._runs` is empty, and `runs.json` on disk contains the run row
(memory=old, disk=new). Bounded: it needs two independent I/O failures; disk wins on restart (an
EXITED row is then pruned/observed normally); the launch path re-attempts persistence via
`_settle_failed_launch`, and a persistent failure surfaces as `manual_cleanup_required`. Smell, not a
record-lost path. The same block also re-checkpoints `previous` after rolling back, which is correct.

### M1 severity — argue P1, not P2 (evidence, not a re-finding)

`invalid_run_manifest` from `_storage_observations_from_payload` (33+ entries or a non-bool
`storage_rotated` — both reproduced; duplicate run id and extra/missing keys hit the same reject-all
branch) rejects the whole manifest, and
the daemon then starts with an empty manifest plus an armed recovery fault:

```python
# tools/dayz_mcp/daemon.py:475-478
        manifest = RunManifestStore.empty_for_recovery(
            paths,
            checkpoint=lifecycle_recovery_store.checkpoint_manifest,
        )
```

Consequence: every run row is discarded from the live view — RUNNING runs become unmanaged
(`dayz_test_stop` answers `run_not_found`, `active_run_exists` admissions cannot see them), all
`storage_observations` are dropped from status, and manual recovery is required. A diagnostic-only
list this version could safely truncate (or clamp to 32) therefore triggers a P1 recovery, not
degraded behaviour. Journals and backups are untouched, so journal-side retry recovery still works.

## CRASH MATRIX

State after "prepare_storage renamed storage_1" (journal `.json`, phase `prepared`/`storage_moved`),
crash before completion:
- Next creating launch runs `_reconcile_journal` against physical state. Tree moved (storage absent,
  backup present) → `_finish_rotation` → recovered result `storage_rotated=True` recorded on the
  *launching* run — correct: the world was set aside. Nothing moved → journal aborted, fresh
  classification. Physically impossible states → blocked launch (A1 applies).

Journal completed, crash before `manifest.replace(provisional)` (process_lifecycle.py:3916):
- Disk: STARTING row without storage fields (added at :3758), completed journal, backup, no
  `storage_1`. Next start: `recover_after_restart` → UNRECONCILED, fields `None` → status `null`
  (unknown, no wrong report was ever delivered). Retry: storage absent + marker matches →
  `seal_only`, then `_pending_completed_rotation` → `true`. Idempotent across repeated retries.

Journal completed + provisional persisted, crash before spawn:
- Next start: UNRECONCILED row keeps `storage_rotated=true`. Retry reports `true` again (journal
  replay is read-only; it never moves or deletes). If the engine already created `storage_1`, the
  retry measures `false` and the old row keeps its `true`. Idempotent.

Spawn done, crash before settlement replace (:4021):
- Same as above; `attempt_run_id` correlation is unreachable (the call died with the daemon), the row
  survives and a later stop/adopt keeps the fields into the observation list on prune.

Settlement persist fails once (checkpoint hiccup, rollback OK):
- Caller gets `manual_cleanup_required`/`manifest_failed`; the EXITED row with the rotation may be
  absent from `runs.json`; the tool result for that call is `null` (no row, journal untouched); a
  retry recovers `true` from the journal. No reset is lost.

Double failure (checkpoint + rollback fail): see A2; transient memory/disk split only.

Runs.json unreadable / observation list hostile: M1 — empty manifest + recovery fault; journals and
rotation backups are not read or modified by that path, so a later retry still recovers `true`.

Clock/mtime: nothing in the new state uses mtime or wall time as truth. `txid` is derived from
`run_id+seal` (process_lifecycle.py:2999-3001), so a retry names the same journal; `_backup_stamp`
uses the clock only as a name label with an existence-collision check (dayz_test_storage.py:564-566);
ambiguity of two completed journals for one seal degrades to `null`, never a guess (verified).

## ANGLES NOT APPLICABLE

- Angle 3 found no new crash-or-accept-wrong-value beyond M1: the tool-side decoders tolerate hostile
  input (verified: string `rotated`, path separators in backup, wrong notice, duplicate rows, missing
  keys → `null`), `_read_json` caps journals at 64 KiB and fails closed, and `_valid_journal` is
  strict; over-permissive journal `storage_backup` names are neutralized downstream by
  `_plain_storage_backup_name` (recorded as `null`, accepted only as evidence the launch may proceed).
- No finding on write ordering between `runs.json` and rotation journals: they are independent
  stores; the journal is self-recovering and the run record is derived from it on retry.

## NOT VERIFIED

- No daemon, DayZ, or MCP tool was started; the A1 public-result chain (worker terminal →
  `_execute_request` fetch → `_compact_result`) is code-traced with each link unit-verified, but the
  full end-to-end response was not produced live.
- `tests.test_storage_reset_visibility` exists only in the audited workspace (the OneDrive tree
  lacks it); its 17 tests pass, but no other suite was run.
- Windows-specific behaviour of `_atomic_write_text` (sharing-violation retry) and checkpoint
  pruning under real I/O faults was not measured.
- Concurrent multi-session status/replace interleavings were reviewed by reading, not by execution.
