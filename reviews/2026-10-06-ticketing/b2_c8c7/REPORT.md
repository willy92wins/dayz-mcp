# REPORT c8c7

## CHANGES

- `tools/dayz_mcp/process_lifecycle.py:_pending_completed_rotation` / `_rotate_storage_for_launch`: after `prepare_storage`, a validated completed journal whose `new_seal` matches and whose backup directory exists is applied when `storage_1` is absent. No second rename. A matching marker is not treated as proof the economy rebuilt the world. H11 accreditation is not defined here.
- `tools/dayz_mcp/process_lifecycle.py:RunRecord` / `_storage_rotation_from_payload`: optional `storage_rotated`, `storage_backup`, `storage_reset_notice`. Missing keys stay unknown (`null`). Present values are validated. `asdict` round-trips them through clone, settlement, and reload. Legacy rows still load.
- `tools/dayz_mcp/process_lifecycle.py:_rotate_storage_for_launch` / `_record_storage_rotation`: the measured result is copied onto the provisional run. A successful rotation is `manifest.replace`d before spawn. Failed-launch settlement clones that provisional, so a true reset stays true. A replay (`pending_completed_rotation`) is not audited again.
- `tools/dayz_mcp/process_lifecycle.py:_projected_run`: status publishes the dataclass fields, including the three rotation keys.
- `tools/dayz_mcp/dayz_test_tool.py:_storage_observation_from_status` / `_execute_request` / `_compact_result`: the public result always has `storage_rotated`, `storage_backup`, and `storage_reset_notice`. `null` is unknown; `false` is a measured non-rotation. On a failure the storage run id is the terminal's `attempt_run_id`, not a snapshot diff and not the cleanup `run_id`.
- `CHANGELOG.md`: one Unreleased fixed line.
- `tools/tests/test_dayz_test_tool.py`: four exact result-key sets now include the three fields.

## TESTS ADDED

Module `tools/tests/test_storage_reset_visibility.py`.

- `test_mismatch_rotation_is_on_the_run_and_survives_reload_and_ack` — fails on unmodified code because status and `runs.json` have no rotation fields.
- `test_public_result_reports_the_rotation_for_that_run_only` — fails because `_compact_result` omits the fields and another run's `false` would be indistinguishable.
- `test_spawn_failure_keeps_the_reset_and_a_retry_recovers_it` — fails because settlement does not keep the result and `prepare_storage` ignores completed journals.
- `test_failed_terminal_reads_the_rotation_before_run_id_normalization` — fails because a failed terminal never reads the rotation into the public result.
- `test_matching_seal_reuse_is_a_measured_false`
- `test_matching_seal_reuse_is_false_in_the_public_result`
- `test_legacy_record_without_rotation_keys_stays_unknown`
- `test_unavailable_status_yields_null_not_false`
- `test_reload_does_not_touch_rotation_journals`
- `test_unrelated_run_during_a_pre_run_failure_stays_null`
- `test_attempt_run_id_selects_this_rotation_among_concurrent_runs`
- `test_attempt_run_id_resolves_when_an_earlier_status_read_fails`
- `test_old_worker_terminal_without_attempt_run_id_is_null`

## GATE OUTPUT

```
--- tests.test_storage_reset_visibility: rc=0
Ran 15 tests in 0.747s

OK
--- fast tier: ran=5602 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

Round 3 gate, exit 0. `failures=2` are baseline failures (`new=0`). `ran=5602` is above the 5587 floor.

## DEVIATIONS

- The completed-journal recovery lives in `process_lifecycle._rotate_storage_for_launch`, not in `prepare_storage`. `tools/dayz_mcp/dayz_test_storage.py` is sealed by `tools/packaged-modules.lock.json`; changing it fails the fast tier unless the native launcher is resealed, and this batch forbids that reseal. `prepare_storage` still ignores completed journals. The launch path reads them and stamps the run.
- EXITED rows are still pruned when a manifest loads. The measured rotation is also stored in a bounded `storage_observations` list (32) in `runs.json` and on lifecycle status, so a reload still returns that run's reset by its original run id. The backup directory and journal bytes are left in place. A retry before the replacement `storage_1` exists still recovers the same fact from the completed journal.
- Replaying a completed journal does not append a second `lifecycle_storage_rotated` audit row. The run record is the transport. The audit log stays a record of moves, not of replays.
- This change does not define when a new `storage_1` counts as accredited.

## ROUND 2 FIXES

- F1: `dayz_test_tool._execute_request` records run ids before a creating launch. When the worker terminal has no run id and cleanup succeeded, the one run id that appeared during the attempt is the correlation. An unrelated run already present is ignored. `_storage_observation_from_status` reads that id.
- F2: `RunManifestStore` keeps a bounded `storage_observations` list (32) in `runs.json` and on lifecycle status. Pruning an `EXITED` row no longer drops that run's measured rotation. Lookup is by the original run id.
- F3: after rotation, a `manifest.replace` failure is settled as `manifest_failed` without indexing `_STORAGE_ROTATE_HINTS`. The settlement response is returned and the rotation stays on the run.

## ROUND 3 FIXES

- F1: Removed the global status-snapshot set difference. A run that this attempt did not name stays unknown: `storage_rotated`, `storage_backup`, and `storage_reset_notice` are null. `dayz_test_worker.DayzTestWorkerError` and `_failure_after_cleanup` now carry optional `attempt_run_id` (the run this attempt created or targeted) and `launch_operation_id` (when one was allocated) on failure whether or not cleanup succeeded. The existing `run_id` field is still set only when cleanup is degraded. `app_main._write_worker_terminal` writes those keys only when present. `parse_worker_terminal` accepts them only on a failure, as uuid4, and requires `attempt_run_id` when `launch_operation_id` is present. `_execute_request` matches `attempt_run_id` to the lifecycle row and checks `launch_operation_id` when that row carries one. A success terminal still uses its `run_id`. An older failure terminal with no `attempt_run_id` yields null. `tools/packaged-modules.lock.json` was regenerated with `tools/write_packaged_modules_lock.py` for the worker and `app_main` sources. The native launcher binary was not rebuilt. Regression coverage: an unrelated run during a pre-run `build_failed` stays null; this attempt's rotation is selected beside a concurrent run, with its own backup; a failed first status read still resolves that attempt; an old worker terminal without `attempt_run_id` stays null.

## NOT VERIFIED

- No in-game launch. The gate is the check that was run.
- Downgrade of a new `runs.json` by an old daemon was reasoned from `from_payload` ignoring unknown keys, not executed with an old interpreter.
