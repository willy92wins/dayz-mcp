# R9 audit fixes for PR #205 (c8c7, storage reset visibility)

Written by the orchestrator from a DZ-R9 rigorous data audit: two angle auditors, an independent
verifier from another model family that re-opened every cited line (snippet and inference both
confirmed for F1, F2 and F3), and an orchestrator re-read of each citation against this tree.

## ITEM c8c7-r9

The workspace is PR #205 (c8c7) rebased on main. Fix exactly the three defects below; keep every
other behaviour of the PR. `tools/dayz_mcp/dayz_test_storage.py` and `tools/dayz_mcp/dayz_test_worker.py`
are pinned by `tools/packaged-modules.lock.json` (sealed launcher bundle): none of the fixes needs them,
do not edit them.

### F1. A refused storage classification stays unknown instead of becoming a measured `false`

- Code today: `_rotate_storage_for_launch` (`tools/dayz_mcp/process_lifecycle.py:3024-3026`) calls
  `self._record_storage_rotation(provisional, result)` BEFORE `if not result.launch_allowed: return
  "storage_recovery_required"`. `_record_storage_rotation` (`:3055-3058`) writes `storage_rotated=False`,
  `storage_backup=None`, `storage_reset_notice=None` for any result that did not rotate, the same value a
  measured reuse produces. `dayz_test_storage._blocked` (`tools/dayz_mcp/dayz_test_storage.py:349-352`)
  returns `launch_allowed=False, storage_rotated=False` for every state the storage subsystem cannot
  classify (journal_unreadable, journal_state_impossible, journal_ambiguous, journal_name_invalid,
  mission_not_enumerable, backup_name_collision). The settled EXITED row, the durable
  `storage_observations` list and the failed call's public result then all say "measured reuse" where
  nothing was measured (in the impossible-state case the old world may already be set aside).
- Required: when `prepare_storage` refuses the launch (`launch_allowed` false), record nothing on the
  provisional run. Its three storage fields stay null (unknown), so the settled row is null, no entry
  enters `storage_observations` (`_note_storage_observation_locked` already skips non-bool values), and
  the failed call's public result carries `storage_rotated: null`. The refusal code
  `storage_recovery_required` does not change. This matches the raised-`StorageError` path, which already
  records nothing (`:3003-3004`).
- Tests (each must FAIL on the unmodified tree): a launch refused by a real blocked classification (for
  example a planted active rotation journal in an impossible state, `storage_1` and its backup both
  present) settles an EXITED row with null storage fields and adds no observation; the public result of
  that failed `dayz_test_run` call has null `storage_rotated`, `storage_backup`, `storage_reset_notice`.
  Drive it through the lifecycle and the tool path the existing module uses, not by calling
  `_record_storage_rotation` directly.

### F2. A malformed `storage_observations` list degrades per entry instead of invalidating `runs.json`

- Code today: `_storage_observations_from_payload` (`tools/dayz_mcp/process_lifecycle.py:228-262`) raises
  `invalid_run_manifest` for a non-list value, more than `_STORAGE_OBSERVATION_BOUND` (32, `:1158`)
  entries, an entry that is not a dict or whose key set is not exactly the four keys, a duplicate run id,
  or an invalid value. The manifest load then fails as a whole, so the daemon refuses to start or enters
  manifest recovery, over a diagnostic list. The rest of the manifest reads known keys and ignores unknown
  ones (`RunRecord.from_payload`), so the exact-key-set check also makes this version unable to read a
  list written by a later version that adds a key per entry.
- Required: the list is advisory and its loader never raises.
  - A missing or non-list value loads as an empty list.
  - Each entry is validated on its own and dropped when it is not a dict, lacks one of the four keys, has
    a run id that is not a non-empty string, has a `storage_rotated` that is not a bool, or fails
    `_validate_storage_rotation`. Extra keys are ignored and not copied.
  - A run id that appears more than once is dropped entirely (ambiguous means unknown, never a guess).
  - Then keep the newest `_STORAGE_OBSERVATION_BOUND` entries; the writer appends at the end and trims
    the front (`:1832-1840`), so the newest are the last ones.
  - The run rows' own storage fields (`_storage_rotation_from_payload`, `:206-225`) keep their current
    strict validation. Do not change it.
- Tests (each must FAIL on the unmodified tree): 33 valid entries load as the newest 32; one malformed
  entry among valid ones drops only that entry; a duplicated run id is dropped, both copies; an entry
  with an extra key loads without that key; a non-list value loads as empty. In every case the runs of
  the same `runs.json` load and stay visible.

### F3. A call on an existing run reports null, as the contract comment says

- Code today: the comment at `tools/dayz_mcp/dayz_test_tool.py:1544-1545` makes the three fields
  per call: null for "a launch that does not create storage". In `_execute_request`
  (`tools/dayz_mcp/dayz_test_tool.py:1805-1813`) an ok terminal sets `storage_run_id = terminal.run_id`
  with no operation check. So `dayz_test_run mode=client run_id=X`, `mode=offline` with `run_id`, and
  `dayz_test_stop` (all pass `expected_run_id=run_id`, `:2316` and `:2601`) republish the rotation
  recorded when run X was created. Those calls never rotate: `_rotation_applies`
  (`tools/dayz_mcp/process_lifecycle.py:2947-2957`) allows rotation only for a launch that creates its
  run, role server or offline.
- Required: when `expected_run_id is not None`, the three fields are null on every terminal, ok or
  failed. Creating launches (`expected_run_id is None`) keep today's behaviour exactly: an ok terminal
  reports the run's stored rotation; a failure terminal with `attempt_run_id` reports that attempt's
  rotation with the operation check.
- Tests: a creating launch whose run recorded a rotation still reports true with its backup (must pass
  before and after); a `mode=client` reattach of that run reports null; `dayz_test_stop` of that run
  reports null (these two must FAIL on the unmodified tree).

### Out of scope (backlog; do not change)

- `_persist_locked` rollback when both the checkpoint and the rollback write fail (`:1803-1813`).
- `_pending_completed_rotation` skipping an unreadable completed journal (`:138-194`).
- Everything else in the PR, the sealed launcher sources (`tools/native-launchers/`) and the two pinned
  modules named above. CHANGELOG: at most one short line, under the existing c8c7 entry's subsection.
