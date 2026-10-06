# REPORT — g3 launch contract (c561, 31d2, 2837)

## CHANGES
- `tools/dayz_mcp/dayz_test_attestation.py` (new packaged helper): closed parse of policy `attestation` (version 1, up to eight artifacts and eight initialization rules, `timeout_s` default 60 / max 300); plain-PBO entry names with compression/encoding as unverifiable; resolve exactly one deployed PBO in the effective mod directories and record SHA256; script-log and profile-file boundaries accept only complete lines after the captured offset; bounded scan; report and worker failure codes.
- `tools/dayz_mcp/dayz_test_request.py` `RequestProjectPolicy` / `parse_dayz_test_request`: optional attestation; version 1 keyset unchanged; version 2 adds strict bool `project_mod_override`; canonical v2 keysets added beside the existing v1 sets; `version` must be a real int (bool `True` stays `version_unsupported`); override rejects build/clean, a bridge-only candidate list, and a leaf that is the original project folder (`project_mod_override_conflicts_with_build`, `project_mod_override_requires_candidate`, `project_mod_override_includes_original`).
- `tools/dayz_mcp/dayz_test_worker.py` `_mods` / `_start_core` / `_modset_seal` / `assess_preflight` / `execute_dayz_test_worker`: override omits only `@<project>` and rejects a normalized path alias of that folder; storage seal uses `project_mod=None` for that role; preflight shares `assess_preflight` and starts no child; after a requested build, artifact checks run before start; initialization is inside the existing launch `try` so a new run is stopped and a reattach is not; `WorkerResult` and `DayzTestWorkerError` carry the report.
- `tools/dayz_mcp/dayz_test_tool.py` `build_run_request`, `_extra_mods_with_bridge_default`, `execute_dayz_test_run`, `_execute_preflight`, `parse_worker_terminal`, `_compact_result`: default calls stay request v1; override emits v2; bridge default uses the effective composition and does not copy `@DayZ_MCP` back when that folder is the excluded project; preflight returns before idle-session and does not call `_execute_request` or `execute_secure_launcher_request`; Steam is read-only for client-starting modes; result adds `preflight`, `box_busy`, `occupied_by_run_id`, drops `steam_session` from skipped checks, and adds `project_mod_replaced` plus `attestation` only when present. Legacy five-key terminals still parse.
- `tools/dayz_mcp/dayz_test_storage.py` `modset_roles`: implicit project role may be JSON null; marker schema unchanged.
- `tools/dayz_mcp/native_launcher_transaction.py` `effective_mod_entries`: same omission as worker `-mod`.
- `tools/dayz_mcp/native_bundle.py`: packages `dayz_test_attestation.py`; sealed policy may carry attestation; `VerifiedNativeBundle.validated_worker_runtime` reads the already parsed worker-runtime document.
- `tools/dayz_mcp/server.py` `dayz_test_run`: `project_mod_override`; preflight branch after argument validation and before box FIFO/takeover; description states that preflight success is not a free box or a future launch, that replacement is composition only, and that attestation is opt-in and is not feature acceptance.
- `tools/build_native_launcher.py` and `tools/native-launchers/dayz-test-v1/src/app_main.py`: same packaged module and policy reconstruction; worker terminal forwards the attestation object. C++ unchanged.
- `tools/packaged-modules.lock.json`: regenerated from the combined source set.
- `CHANGELOG.md` Unreleased: one short bullet. Kept under the PROJECT-MAP 44 KB rounding window.
- c8c7 rotation fields and dbe0 asset walk were left in place. Preflight does not traverse or rotate storage.

## TESTS ADDED
`tools/tests/test_launch_contract_c561_31d2_2837.py` (21 tests). On the unmodified tree they fail because no attestation report, busy-box success, or v2 override exists.

- `test_c561_required_script_entry_missing_fails_before_start`
- `test_c561_initialization_requires_post_launch_evidence`
- `test_c561_checks_deployed_candidate_artifact`
- `test_c561_failure_cleans_only_new_run` (new run is stopped; stop failure stays `cleanup_degraded`; client reattach does not stop)
- `test_c561_report_reaches_public_result` (success, failure, legacy five-key terminal)
- `test_c561_compressed_entry_is_unverifiable_and_disabled_policy_launches`
- `test_c561_unreadable_and_truncated_evidence_is_unverifiable`
- `test_31d2_foreign_box_does_not_block_valid_preflight`
- `test_31d2_preflight_takeover_never_evicts`
- `test_31d2_busy_box_does_not_mask_validation_failure`
- `test_31d2_preflight_checks_steam_without_repair`
- `test_31d2_preflight_with_held_lease_preserves_it`
- `test_31d2_host_and_worker_preflight_agree`
- `test_2837_override_excludes_original_from_all_role_argv`
- `test_2837_override_vpp_and_worker_lists_agree`
- `test_2837_override_changes_storage_seal_and_default_preserves_it`
- `test_2837_dayz_mcp_override_requires_effective_bridge`
- `test_2837_override_survives_witness_recomposition`
- `test_2837_old_canonical_v1_still_reparses`
- `test_2837_candidate_build_conflict_is_rejected_before_side_effects`
- negative override cases live in `test_2837_candidate_build_conflict_is_rejected_before_side_effects` and the bridge/v1 tests; invalid bools remain on the existing request suite (`bool_version` still `version_unsupported`).
- Round 4: `test_f13_replaced_root_fails_public_preflight` (previous tree accredits the recreated mod children and returns success; `_open_sealed_root` on the replaced root already raises). `test_f4_missing_build_source_basename_is_refused_by_preflight` (previous accessor treats the missing key as null and preflight succeeds). `test_f4_runtime_document_decisions_agree` feeds the same documents to the bundle accessor and `app_main._validated_worker_runtime`.

Pins updated, not deleted: `tools/tests/test_db05_preflight_diagnostics.py` `test_preflight_green_explicitly_discloses_steam_and_attach_checks` now expects `launch.await_count == 0` because preflight no longer starts the sealed worker. Earlier in this batch, Steam-skipped assertions in `test_dayz_test_tool.py`, `test_prerun_desktop_gate.py`, `test_steampost_readiness.py`, and the handoff test that needed a real launch (`preflight=False`) were updated the same way.

## GATE OUTPUT
Round 4, last lines of `gauntlet_gate.py tests.test_launch_contract_c561_31d2_2837`:

```
--- tests.test_suite_structure (whole-suite ratchets): rc=1
    (same offenders as the base tree; not blocking)
--- tests.test_launch_contract_c561_31d2_2837: rc=0
Ran 31 tests in 0.634s

OK
--- tests.test_launch_contract_c561_31d2_2837 on Python 3.11: rc=0 OK
--- fast tier: ran=5692 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

`tests.test_native_launcher_transaction` alone: `Ran 7 tests in 0.292s`, `OK`.

Round 3, last lines of `gauntlet_gate.py tests.test_launch_contract_c561_31d2_2837`:

```
--- tests.test_launch_contract_c561_31d2_2837: rc=0
Ran 28 tests in 0.470s

OK
--- tests.test_launch_contract_c561_31d2_2837 on Python 3.11: rc=0 OK
--- fast tier: ran=5689 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

Round 1, completed:

```
    (same offenders as the base tree; not blocking)
--- tests.test_launch_contract_c561_31d2_2837: rc=0
Ran 21 tests in 0.472s

OK
--- waiting for a free fast-tier slot (other batches are gating)
--- fast tier: ran=5682 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

Round 2: `gauntlet_gate.py` was started with `-u`. It printed the required module as `rc=0` (`Ran 21 tests in 0.425s`, `OK`) and the same module on Python 3.11 as `rc=0 OK`. The process was then killed by the session tool timeout while it held or waited for the fast-tier slot (another batch, `tests.test_lease_ttl_and_file_wait`, was gating). The gate buffers the discover summary until that step returns, so this round has no new `GAUNTLET_GATE:` line.

## ROUND 2 FIXES
- F1: `capture_log_boundaries` now records each existing script log and each declared profile filename as `(size, partial)`. `partial` is true when the offset cuts an unfinished line. `new_matching_line` drops that completing line and accepts only lines that begin and finish after the boundary. A profile file that already contains the marker is not a pass.
- F2: the worker captures each role immediately before that role's `_start`. In `mode="all"` the client boundary is taken after the server readiness wait, not before the server starts.
- F3: `accredit_request_paths` also pins the implicit `@<project>` directory unless `project_mod_override` is set. Host preflight calls that accreditation for a real `SealedRequestProjectPolicy` bundle and returns `invalid_dayz_test_path_authority` on failure. No lease is taken.
- F4: preflight requires `validated_worker_runtime`. A missing accessor or a raised validation error becomes `runtime_policy_invalid` instead of success. The branch that skipped the runtime and could omit a pending attestation report is gone. The test double `_Bundle` now implements the accessor.
- F5: the request parser no longer rejects an override because a listed leaf matches the project basename. `_reject_override_alias` still rejects a candidate whose normalized path is the original project directory.
- F6: artifact directories for `server` and `all` include `server_mods`, the same folders `-serverMod=` loads.
- F7: `_sample_box_occupancy` treats `STARTING`, `RUNNING`, `RUNNING_IDLE`, `STOPPING`, and `UNRECONCILED` as managed occupants, and treats a non-empty `foreign` list, `occupied: true`, or an unknown scan as busy. A single managed run id is returned. Unreadable status stays `null`.
- F8: hash and PBO parse use one bounded read. An unverifiable parse keeps that digest.
- F9: `unverifiable` outranks `failed` in a row and in `combine_status`, so a missing marker no longer hides an unreadable or truncated rule, and the worker returns `project_attestation_unverifiable` without waiting out the timeout.
- F10 and F11 are P3. They were not changed. Policy typing is still the previous closed parser, and `product-spec.md` stays outside the edit boundary.

## ROUND 3 FIXES
- Hang: `accredit_request_paths` no longer opens the implicit `@<project>` directory. That open ran on every sealed launch, raised `invalid_dayz_test_path_authority` when the folder was absent, and never started the consumer. `test_repeated_task_cancellation_cannot_skip_consumer_cleanup_or_release` then waited forever; `test_non_terminal_status_turns_success_into_cleanup_error` raised the path error instead of `session_close_degraded`. `tests.test_native_launcher_transaction` passes again. `test_regular_tree_is_pinned_until_context_exit` no longer expects a `project_mod` identity.
- F3: after the runtime is loaded, preflight resolves mission and effective mod directories with that runtime and accredits those absolute paths only (`accredit_exact_paths`). A relative name that exists under a different allowed root does not satisfy the selected `mods_root`. Regression: `test_f3_preflight_does_not_accredit_another_root`.
- F4: `runtime_policy_acceptable` is the worker's alias, absolute-path and build-source check. `VerifiedNativeBundle.validated_worker_runtime` and the test double both use it. Regression: `test_f4_runtime_accessor_uses_the_worker_rules` (`mission_aliases` `chernarus` → `relative-missing`).
- F7: `_sample_box_occupancy` reads `session_status()["box"]`, the snapshot with `foreign`, `scan_known` and `port_scan_known`. A known foreign DayZ process with no managed run is busy and `occupied_by_run_id` is null. `scan_known` is not true, or `port_scan_known` is not true, stays null. `/lifecycle/status` is not the source. `test_preflight_green_explicitly_discloses_steam_and_attach_checks` now pins `session_calls` instead of `lifecycle_calls`. Regression: `test_f7_foreign_process_and_unknown_scan`.
- F9: a later missing artifact does not replace an earlier `unverifiable` aggregate. Regression: `test_f9_unverifiable_artifact_precedes_a_later_miss`.
- F12: `reject_original_project_directory` compares normalized runtime paths and calls `_invalid("project_mod_override_includes_original")`. Preflight publishes `bad_dayz_test_request:project_mod_override_includes_original`. Regression: `test_f12_original_directory_keeps_the_request_reason`. The request vocabulary test matches the `_invalid` call again.
- F10: version must be a real `int`; entry names and `source` are typed before hashing or membership. Regression: `test_f10_policy_types_fail_closed`.
- F11: H11 and H13 now state preflight without the box (`box_busy` / `occupied_by_run_id`), `project_mod_override` / `project_mod_replaced`, and opt-in attestation. Regression: `test_f11_h11_and_h13_state_the_acceptance_contract`.
- `tools/packaged-modules.lock.json` regenerated for the changed sealed sources. The installed launcher binary was not resealed.

## ROUND 4 FIXES
- F13: `accredit_runtime_resolved_paths` opens `dev_root`, `default_source`, and every sealed root a runtime-resolved mod or mission path descends from with `_open_sealed_root` before any descendant open. Those handles stay open until the descendant checks finish. A replaced root fails as `invalid_dayz_test_path_authority`. Preflight uses this instead of two `accredit_exact_paths` calls. Regression: `test_f13_replaced_root_fails_public_preflight`.
- F4: `dayz_test_worker.worker_runtime_from_document` is the one closed parser (required keys including `build_source_basename`, unknown keys, semantic rules). `VerifiedNativeBundle.validated_worker_runtime` and `app_main._validated_worker_runtime` both call it. Public preflight still reports `runtime_policy_invalid`. Regression: `test_f4_missing_build_source_basename_is_refused_by_preflight` and `test_f4_runtime_document_decisions_agree`.
- `tools/packaged-modules.lock.json` regenerated. The installed launcher binary was not resealed.

## ROUND 5 FIXES
- Client reattach preflight (`mode="client"` with `run_id`) no longer fails when Steam is stopped or stale. `_execute_preflight` still calls `evaluate_steam_session` and still does not repair. A Steam error fails preflight only when `run_id` is absent, so `all` and `offline` keep the 31d2 refusal. The slow test `test_dayz_test_run_preflight_client_reattach_keeps_run_id` has no Steam double; on a machine whose Steam session is not the registered one the new gate returned `status=failed` (`steam_not_running` / `steam_session_stale`) and dropped the supplied run id. Reproduced by forcing `steam_not_running`: `AssertionError: 'failed' != 'succeeded'`. With a healthy local Steam session the same test already returned succeeded, which is why an isolated run on this machine did not show the CI failure until Steam was forced. Regression: `test_r5_client_reattach_preflight_survives_stopped_steam` (previous tree returns failed).
- `_execute_preflight` no longer does `loader = getattr(bundle, "validated_worker_runtime", None)` and then `loader(mod, dev_root)`. The runtime HTTP audit classifies that assignment-plus-two-argument call as `dynamic_http` (`dayz_mcp/dayz_test_tool.py`, function `_execute_preflight`). The accessor is now a direct attribute call; `AttributeError` and validation errors still become `runtime_policy_invalid`. No audit allow-list entry was added. Regression: `test_r5_preflight_loader_is_not_dynamic_http` (previous tree reports that finding).

Slow modules, `DAYZ_MCP_FAST_TESTS` unset, `DAYZ_MCP_FOREIGN_TOOLCHAIN=1`:

```
----------------------------------------------------------------------
Ran 113 tests in 25.660s

OK
```

`tests.test_mcp_tools` and `tests.test_security_runtime_audit` together. The reattach test and `test_productive_runtime_closure_has_no_unaccredited_http_path` both passed.

Round 5 gate, last lines:

```
--- tests.test_suite_structure (whole-suite ratchets): rc=1
    (same offenders as the base tree; not blocking)
--- tests.test_launch_contract_c561_31d2_2837: rc=0
Ran 33 tests in 5.042s

OK
--- tests.test_launch_contract_c561_31d2_2837 on Python 3.11: rc=0 OK
--- fast tier: ran=5694 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

## DEVIATIONS
H11 and H13 were updated in round 3, as that round required. The round-2 note that left them unchanged is historical. `tools/build_native_launcher.py`, `tools/native-launchers/dayz-test-v1/src/app_main.py` and `tools/packaged-modules.lock.json` were updated because packaging and the worker terminal have to carry attestation; the brief allows those sealed sources and the lock. The installed launcher binary was not resealed. A v2 document with `project_mod_override: false` is accepted and canonicalized as version 1. Cancellation of initialization is the existing `operation_cancelled` path inside the launch `try`; there is no separate cancellation test beyond the worker's existing cancel event. Host preflight does not accredit mod directories through `request_path_authority` when the bundle object is the test double; the worker validator checks the effective directories on the runtime policy. Round 5: a client-reattach preflight still observes Steam but does not fail the preflight on that observation. Modes that start a client without a run id still fail. The installed launcher was not resealed; this round did not change sealed worker sources.

## NOT VERIFIED
No DayZ, DayZDiag, DayZServer, daemon or `127.0.0.1:8765` call. No live reseal, no deployed-PBO hash on a real profile, no foreign-box occupancy on the shared machine, no in-game `-mod=` or storage-rotation check. Fast-tier baseline failures that the gate left as `new=0` were not re-diagnosed.
