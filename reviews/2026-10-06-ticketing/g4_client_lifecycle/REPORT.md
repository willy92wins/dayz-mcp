## CHANGES

- `tools/dayz_mcp/server.py:_explicit_client_extension_row`, `_client_extension_blocked`, `_client_extension_exempt`: a `mode="client"` call with `run_id`, project `@{project}` on the row, `RUNNING_IDLE`, and `caller_launched_row` is an explicit client extension. Another active run, a non-empty `foreign` list, `scan_known`/`port_scan_known` false, or a non-list `foreign` blocks that exemption. `takeover_target_run_id` is unchanged. Only the requested row is dropped from the takeover target, so the call does not stop it.
- `tools/dayz_mcp/server.py:dayz_test_run`: the same exemption clears a queue `own_run`/`adopt` refusal and skips the FIFO for `on_busy="queue"`. `wait_for_box_s` on `on_busy="fail"` still uses the existing wait and does not peek the box first. The tool text says a polling client stays `client_already_polling` and that a public prefix only selects the path.
- `tools/dayz_mcp/process_lifecycle.py:_client_is_launcher` / `_adoption_protection`: unchanged. Full session id still authorises adopt and start.
- `tools/dayz_mcp/process_lifecycle.py:_projected_run`, `_client_role_diagnostics`, `_observe_client_record`: active and full lifecycle status rows gain `client_diagnostics`. Each client identity has `state` (`alive`, `dead`, `unknown`, `not_started`), registered identity, `observed_at_utc`, `first_observed_dead_at_utc`, and nullable `exit_code` / `exit_time_utc`. Those two are copied only from `process_exit_code` and `exit_time_utc` on a same-identity or `process_not_found` snapshot, never from the guard's `exit_code` and never from a reused pid. Previous identities stay under their own key, capped at 32. A quarantine or a new daemon process does not invent them. Status calls `guard.snapshot` for client roles only and does not terminate.
- `tools/dayz_mcp/server.py:camera_set`: `settle_ticks` must be an integer 0..600 before `call_bridge`. The description states `settle_ticks * 0.05` seconds, that 0 keeps three ticks, that `timeout_s` must cover settle plus processing and transport, and that a timeout does not prove the camera was not applied.
- `tools/dayz_mcp/loopback.py:_camera_variant`: wire `settle_ticks` is `_integer_in_range(minimum=0, maximum=600)`.
- `addon/scripts/5_Mission/MCPClientBridge.c:ValidateCameraArgs`: `settle_ticks` outside 0..600 is `bad_args` before the job is queued. `DispatchCameraSet` sets `deadline_s` to elapsed + `ResolveSettleSeconds` + `CAMERA_JOB_TIMEOUT_S` (5 s). `MCP_PostJobTimeout` still returns `error="timeout"`.
- `CHANGELOG.md`: one Unreleased fixed line. No existing test pin was rewritten. `MCP_BRIDGE_VERSION` stays `"10"`.

## TESTS ADDED

Module `tools/tests/test_client_lifecycle_7055_9336_9efc.py`.

- `test_released_launcher_client_extension_does_not_stop` fails on unmodified code: the public tool returns `takeover_required` and never calls `execute_dayz_test_run`. After the change it reaches the extension path, calls stop zero times, and returns the same `run_id` and server creation identity with a new client pid.
- `test_fresh_all_mode_stays_takeover_gated`
- `test_foreign_launcher_is_not_admitted`
- `test_wrong_project_and_wrong_run_stay_gated`
- `test_daemon_restart_forgets_the_launcher`
- `test_prefix_selects_the_path_and_does_not_authorise` (`_client_is_launcher` uses the full session id)
- `test_starting_stopping_unreconciled_do_not_replace`
- `test_unknown_liveness_and_healthy_client_do_not_replace` (healthy is `client_already_polling`)
- `test_queue_and_immediate_both_reach_extension` (`on_busy` `fail` with wait 0, and `queue`)
- `test_status_change_between_admission_and_extension_refuses`
- `test_another_run_foreign_and_unknown_scan_stay_blocked`
- `test_server_alive_client_dead_observes_without_managing_processes`
- `test_unknown_probe_pid_reuse_reattach_restart_and_bound`
- `test_not_started_when_the_run_has_no_client`
- `test_runner_keeps_a_200_tick_job_past_five_seconds` (apply failure and a 4 s caller timeout included)
- `test_wire_and_tool_reject_settle_ticks_outside_0_to_600`
- `test_tool_validates_before_enqueue_and_timeout_stays_unknown`

The extension cases call the real `require_extension_run` and `_decide_client_replacement` once the public tool has admitted the call. Transport and stop are fakes.

## GATE OUTPUT

```
    (same offenders as the base tree; not blocking)
--- tests.test_client_lifecycle_7055_9336_9efc: rc=0
Ran 22 tests in 3.017s

OK
--- tests.test_client_lifecycle_7055_9336_9efc on Python 3.11: rc=0 OK
--- fast tier: ran=5683 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

## DEVIATIONS

- The camera runner is not executed in Enforce. The test steps the same rule as `MCPJobRunner` (`process` finishes before the deadline check; timeout only when `elapsed > deadline`) using `CAMERA_SETTLE_STEP_S`, `CAMERA_JOB_TIMEOUT_S`, and `CAMERA_DEFAULT_SETTLE_TICKS` read from `MCPClientBridge.c`, and it requires the new deadline assignment in that file.
- No bridge capability bit and no version bump. `tools/tests/test_batch6.py` pins `MCP_BRIDGE_VERSION` `"10"`. An older PBO still cancels the camera job at 5 s. Python rejects `settle_ticks` outside 0..600 before enqueue and does not turn a timeout into proof that the camera was not applied.
- `exit_code` / `exit_time_utc` stay null unless the snapshot carries `process_exit_code` and `exit_time_utc`. The native guard's `exit_code` 0/3/4 is not a process exit. The shipping guard does not fill those two fields today.
- Round 2: `on_busy="fail"` with a positive `wait_for_box_s` skips the wait when the call is an eligible client extension. The pre-wait read is `session_box_status()` without joining the FIFO.
- c8c7 storage fields and the b4/b5 bridge contracts were not modified.

## ROUND 2 FIXES

- F1: `dayz_test_run` reads the box before `wait_for_box_s` in both `on_busy="fail"` and `on_busy="queue"`. An eligible client extension skips that wait. The fail-mode read is `session_box_status()` without joining the FIFO. `test_queue_and_immediate_both_reach_extension` now includes `on_busy="fail"` and `wait_for_box_s=0.02`.
- F2: client observations use `_client_observation_lock` for lookup, insert, prune, and the published copy. `guard.snapshot` stays outside that lock. `test_overlapping_status_reads_during_identity_replacement` reads status on two threads while the client identity is replaced.

## ROUND 3 FIXES

Reviewer finding F3 only. Round-1 F1 and F2 fixes are untouched.

- `tools/dayz_mcp/process_lifecycle.py:_observe_client_record`: the pre-probe snapshot of the
  stored observation is gone. The guard probe still runs outside `_client_observation_lock`; the
  probe row is merged against the LATEST stored observation, stored, and copied back inside one
  critical section, and that merged copy is what the status payload publishes.
- `tools/dayz_mcp/process_lifecycle.py:_remember_client_observation`: lookup, merge, store and the
  bound prune now share one critical section. It returns a detached copy of the merged row.
- `tools/dayz_mcp/process_lifecycle.py:_merge_client_observations` (new, pure): strength order
  `dead` > `alive` > `unknown`. A weaker or older observation never replaces a stronger stored one,
  `first_observed_dead_at_utc` stays the earliest stamp seen, `exit_code` / `exit_time_utc` survive
  once a snapshot has supplied them, and `observed_at_utc` never moves backwards. The former
  `_stored_client_observation` helper was removed; its locked lookup moved inside the same critical
  section (calling it under the lock it takes would deadlock).
- Regression tests in `tools/tests/test_client_lifecycle_7055_9336_9efc.py`, both run against the
  previous tree where each fails with `AssertionError: 'unknown' != 'dead'` (the stored row lost the
  published death), and both deterministic across repeated runs:
  - `test_overlapping_readers_cannot_erase_an_observed_death`: the reviewer's interleaving through
    the real `public_status()`. Reader A blocks inside its guard probe, reader B publishes the first
    death at stamp `00:00:01`, A resumes with `identity_unavailable`, reader C confirms at
    `00:00:02`. Asserts A's payload and the stored row keep `state=dead` and B's first-dead stamp,
    that C's confirm does not move the stamp, that the published row is a detached copy, and that
    status made no process-management call.
  - `test_late_unknown_merge_keeps_retained_exit_fields`: same interleaving with a death snapshot
    that carries `process_exit_code=1` and `exit_time_utc`; the late unknown merge keeps both fields
    in the payload, the stored row, and the later confirm.

The module now runs 20 tests; the round-1/2 list above names 17 of the 18 previous ones.

## ROUND 4 FIXES

Reviewer finding F3 only. F1, F2, the camera deadline, the client extension and the Enforce bridge are untouched.

- `tools/dayz_mcp/process_lifecycle.py:_observe_client_record`: the death and observation stamp is taken after `guard.snapshot` returns. It is no longer captured before the probe. Quarantine still stamps without calling the guard.
- `tools/dayz_mcp/process_lifecycle.py:_merge_client_observations`: recorded death evidence is write-once. An existing `first_observed_dead_at_utc`, `exit_code` or `exit_time_utc` is kept. An incoming observation fills only a field that is still missing. Conflicting exact exit values are ignored. `observed_at_utc` monotonicity and the round-3 lock (probe outside the lock, merge of the latest row under it, detached copy) stay as they were.
- Regressions in `tools/tests/test_client_lifecycle_7055_9336_9efc.py`, through real `public_status()` and `_BlockedProbe`. On the previous tree each fails: the first because `min(stamps)` moves the first-death time from `00:00:01.000Z` to `00:00:00.500Z`, the second because incoming `(2, 00:00:00.400Z)` replaces recorded `(1, 00:00:00.900Z)`.
  - `test_delayed_dead_observation_keeps_published_death_evidence`: A blocks inside the guard, B publishes dead with exit code 1 and `exit_time_utc` `00:00:00.900Z` at `00:00:01.000Z`, A resumes dead without exact exit fields, C confirms. B's first-death time and exit fields survive A and C.
  - `test_conflicting_older_exit_evidence_does_not_replace_recorded`: the same interleaving where A's resumed probe carries `process_exit_code=2` and `exit_time_utc` `00:00:00.400Z`. Recorded `(1, 00:00:00.900Z)` and B's first-death time stay.

## NOT VERIFIED

No DayZ process was started, stopped, or contacted. No PBO was built. The Enforce linter reported no new error (`errors=2`, `new=0`); it does not compile or run the script.

In-game cycle for the orchestrator:

7055. Launch one owned tandem and place a server-side sentinel. Exit only the client through the game UI, finding the widget with `ui_tree`. Confirm the client process is gone and the server is still alive. Release the lease. Call `dayz_test_run(mode="client", run_id=R, ...)` with the same configuration and no `takeover=true`, then reacquire. Require the same server PID and creation identity, the same sentinel, and a new polling client.

9336.

1. Right after readiness, read `ui_tree` before any camera change. If `InGameMenu` is present, `ui_click(mode="complete")` on the observed Continue widget, then capture. If the menu is absent until camera or focus changes, keep that symptom open.
2. On a nearby baseline scene, compare `settle_ticks=3` and `settle_ticks=200` with `timeout_s=40`. Record the bridge result, polling, and camera readback. A 200-tick job must not die at 5 s on a PBO built from this tree.
3. At the end of a healthy run, `dayz_test_close`. Record each role's RPT, close order, and lifecycle outcome.
4. If close degrades, release the lease and `dayz_test_stop`. Read `session_status` and try the next planned run. If the old run is gone and the next run is admitted, the delayed-retirement symptom is likely gone. If the old run still blocks, keep its generation, run state, stop result, scan/cache timestamps, and audit. Do not treat every `EXITED`/`STOPPING` row as free. If a different run holds the box, that is contention.

9efc/aecb. Paired 1.29 trials, same mission, player, and candidate bridge:

1. Baseline without SecretRock mods.
2. The same trial with `@SecretRock_RH` and `@SecretRock_RHTest`.
3. Where the fixture exists: door read, indoor teleport, open door 5, door read, two raycasts.
4. Exactly `camera_set(cam_mode="lookat", cam_pos=[13255.4, 20, 7148.3], look_at=[13252.8, 19.9, 7148], settle_ticks=10)`.
5. Watch client process identity, exit evidence, bridge polling, and logs for ten seconds. Keep that record before recovery.
6. If the client is alive, `restore_gameplay`. If it is dead, use the explicit reattach above (no `takeover=true`).
7. Separately, a camera about 6 km from the player for the Baltic symptom. Do not combine that with the SecretRock trial.

Interpretation: both trials fail at the same point means a shared camera/engine candidate; only the modded trial fails means a mod/scene candidate, not proof of one mod defect; only the distant camera fails means a streaming/distance candidate; a live PID with polling or render stopped is a hang, not a process exit; if nothing reproduces, keep NEEDS_REPRO with the configuration that was tested. No coordinate clamp, automatic retry, or claimed camera fix.

Also confirm `session_status` / lifecycle status shows `client_diagnostics` for a live server and a dead client, and that the server pid is unchanged.
