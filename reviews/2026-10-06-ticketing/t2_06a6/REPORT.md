## CHANGES

- `tools/dayz_mcp/server.py:build_app` — one session slot for the last `camera_set` (failed flag, reason, run/generation fingerprint). Updated under `runtime.tool_lock`, cleared at the start of `restore_gameplay` under that lock, dropped on capture when the observed run/generation fingerprint differs.
- `tools/dayz_mcp/server.py:camera_set` — records the bridge outcome inside the existing lock. A validation error before the lock is not an attempt. Context comes from a bounded lifecycle read that does not lazy-spawn; an unreadable read stores no context and is not treated as a run change.
- `tools/dayz_mcp/server.py:capture_screenshot` — lifecycle targeting, the grab, and the note share `tool_lock`. On success, one `camera_unverified` warning plus `camera_unverified_reason` when the remembered attempt failed (reason echoed from that attempt, including `lease_expired`) or `observe_caller_presence` is `absent` (`no_lease`). Unreadable presence sets `camera_unverified_reason=unknown` and does not warn. A held lease adds no verification claim. Image bytes and existing `warnings` stay. No heartbeat and no lazy-spawn on this path.
- `tools/dayz_mcp/lease_result_ttl.py:caller_presence` / `observe_caller_presence` — `held` / `absent` / `unknown`. Does not use `classify_status` (its `None` is both absence and "nothing to say"). Same `session_status` read as the TTL decorator, no spawn, no renew.
- `CHANGELOG.md` — one Fixed line under Unreleased (inbox 06a6).
- `tools/tests/test_lease_ttl_and_file_wait.py:test_584e_local_reads_show_declining_ttl` — the mock now returns four TTL samples because capture reads `session_status` once before the decorator. The published values still decline and the heartbeat count stays 0.

## TESTS ADDED

- `test_06a6_success_then_failed_camera_warns_on_capture` — fails on unmodified code: after a successful `camera_set` and a later `lease_expired` rejection, capture metadata has no `camera_unverified` warning, so the picture of pose A is returned with no mention of the failed attempt.
- `test_06a6_confirmed_absence_warns_without_saying_expired`
- `test_06a6_unreadable_observation_is_unknown_not_expired`
- `test_06a6_run_or_generation_change_drops_the_failed_attempt`
- `test_06a6_restore_gameplay_drops_the_failed_attempt`
- `test_06a6_capture_keeps_image_and_existing_warnings`
- `CallerPresenceTest.test_06a6_missing_status_is_unknown_and_idle_self_is_absent`

## GATE OUTPUT

```
--- tests.test_suite_structure (whole-suite ratchets): rc=0
--- tests.test_capture_camera_unverified: rc=0
Ran 13 tests in 1.549s

OK
--- tests.test_capture_camera_unverified on Python 3.11: rc=0 OK
--- fast tier: ran=5836 baseline_ran=5587 failures=0 new=0
GAUNTLET_GATE: PASS
```

## ROUND 3 FIXES

- F1 — `caller_presence` accepts only a string `self.state` before any set membership. A list, dict, number, `None`, or missing state is `unknown`. `observe_caller_presence` classifies inside its existing exception boundary, so a bad shape cannot discard the capture. Regression: `test_06a6_unreadable_self_state_keeps_the_image` (list, dict, and number). On the previous tree `{"self":{"state":[]}}` raised `TypeError: unhashable type: 'list'` and the JPEG was lost.
- F2 — `camera_set` marks the attempt successful only in the success path. `CancelledError` and any other `BaseException` that is not `Exception` are stored as `failed` with reason `cancelled` before the lifecycle read, and the cancellation still propagates. Regression: `test_06a6_cancelled_attempt_stays_unverified` (success, then `busy`, then a blocked attempt cancelled after dispatch, then capture). On the previous tree `finally` wrote `failed=False` because `CancelledError` skipped `except Exception`, so the capture dropped both the warning and `camera_unverified_reason`.

## ROUND 2 FIXES

- F1 — `capture_screenshot` reads lifecycle only through `_read_run_status_no_spawn` (`control._session_call` / `control.lifecycle_status`, bounded, no `_ensure_daemon`). It no longer calls `runtime.lifecycle_status`. `test_d05` feeds that same control call. New test: `test_06a6_unavailable_lifecycle_does_not_spawn`.
- F2 — `restore_gameplay` clears the camera attempt only after `call_bridge("restore_gameplay")` returns. A `busy` rejection leaves the attempt. New test: `test_06a6_rejected_restore_keeps_the_failed_attempt`.
- F3 — `caller_presence` treats only `self.state` `none` and `queued` as absence. Missing or unrecognized state is `unknown`. New test: `test_06a6_self_without_state_is_unknown`.
- F4 — the run fingerprint is `run_id` plus the two generation fields. Lifecycle `state` is not part of it. New test: `test_06a6_lifecycle_state_change_keeps_the_failed_attempt`.

## DEVIATIONS

- `product-spec.md` was not edited. The spec names no C*/H*/D* row.
- Round 1 cleared the camera attempt at the start of `restore_gameplay`. Round 2 clears it only after the restore bridge call returns. A probe failure after that return still drops the attempt, because the restore command was accepted.
- A failed `camera_set` whose error token is `lease_expired` is echoed as that reason. `expired` is not invented for an unreadable or absent read (`unknown` / `no_lease`).
- Context is invalidated only when both the stored fingerprint and the capture's lifecycle read are readable and differ. An unreadable read does not count as a change.

## NOT VERIFIED

- No in-game capture. No DayZ process and no daemon were started. The round-3 gate is the offline suite only (`GAUNTLET_GATE: PASS`, fast tier ran 5836).
- The image check is the simulated JPEG bytes round-tripping through `capture_screenshot`, not a window grab.
- The "fails on the previous tree" claim for the two new tests is from the reviewer's reproduction and the previous control flow (`state in` a set, `except Exception` only). This workspace already contained the fix when the tests were run, so they were not executed against an unmodified tree.
