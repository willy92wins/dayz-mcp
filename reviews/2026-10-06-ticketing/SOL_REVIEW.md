# gpt-6.1-sol review of the triage (2026-10-06)

codex exec, read-only, whole tree of origin/main `6671fd2`; 262k tokens. Unedited.

Review outcome: proceed with the corrected implementation batches below. Do not implement the proposed automatic teleport movement, instance-scoped migration bypass, generic mod-log warning, or CE-signature tuple change as written.

Evidence refers to the supplied checkout, declared to be `6671fd2`. Specifications below describe proposed behavior; they are not implemented changes. Bridge failures normally become public `ToolError`s through `tools/dayz_mcp/bridge_errors.py:508-531`, rather than returning the raw Enforce `ok:false` object.

## ITEM 734c

1. **Verdict check — CONFIRMED, P1.**  
   `tools/dayz_mcp/mcp_supervisor.py:216-231` removes requests only upon responses and merely logs EOF. Admission ignores failed writes at `:449-457`; `_drain` waits solely on `inflight` at `:307-315`.

   I reproduced this without filesystem changes: a worker with empty stdout, `poll() == None`, and writable stdin retained requests 5 and 6, returned `(False, [5, 6])` from `_drain`, and emitted zero host-response bytes.

2. **Fix check — incomplete and protocol-unsafe as proposed.**  
   Clearing existing requests at EOF does not prevent admission **after** EOF when the launcher still accepts writes. `_reply` produces a `tools/call` result, so it cannot correctly answer failed `initialize`, `tools/list`, or other JSON-RPC methods.

   Also trace `_heartbeat` at `:317-344` and `_replay_handshake` at `:365-391`: removing their reserved IDs must not be mistaken for a successful response. Do not remove requests merely because the host sends cancellation; cancellation does not prove that execution stopped.

3. **Executable failure scenario.**  
   Admit `tools/call` ID 5; close worker stdout while the launcher remains alive; admit ID 6 through still-writable stdin; request `server_reload`. Today, neither call receives a response and reload returns `drain_timeout`.

4. **Decision — IMPLEMENT_WITH_CHANGES.**

5. **Implementer spec — M; Python only, no PBO/reseal.**

   - Change `_Generation`, `_pump_worker`, admission, `_send_worker` failure handling, `_heartbeat`, and `_replay_handshake`.
   - Record terminal transport failure under `_state`. Atomically detach pending requests and reject subsequent admission to that generation.
   - Answer failed `tools/call`s with `isError:true` and payload `error:"worker_died"`, `generation`, and completion explicitly unknown. For other request methods, use a JSON-RPC error response, proposed code `-32603`, with the same diagnostic data.
   - Emit exactly one terminal response per host request. Handle EOF/write-failure races and generation boundaries.
   - Reserved requests receive internal failure outcomes; EOF is not heartbeat/replay success.
   - Explicit reload may retire the failed generation and replay initialization into a replacement. Retain `drain_timeout` for a live worker whose stdout remains open and whose calls cannot drain.
   - Do not automatically retry interrupted tools: their effects may already have reached the daemon.
   - **Regression:** extend `test_mcp_supervisor.py` with launcher-alive EOF, admission after EOF, broken-write/response races, non-tool requests, replacement EOF during replay, and an ordinary live-worker drain timeout. The first scenario fails today as reproduced above.

## ITEM dbe0

1. **Verdict check — CONFIRMED vulnerability; reported timing not reproduced.**  
   Both `tools/dayz_mcp/pack_only.py:16-22` and `tools/dayz_mcp/dayz_test_worker.py:630-639` recursively enumerate without checking junctions. The worker invokes the predicate before sending the build frame at `:735-746`.

2. **Fix check — direction correct; test and integration need changes.**  
   A `.p3d` encountered early can short-circuit `any()` before recursion, so the proposed positive fixture is not a reliable regression. The shared wrapper chain is `has_binarizable_assets` → `should_pack_only` → `addon_builder_packonly_args` at `pack_only.py:16-35`.

   The current native launcher receives `pack_only` from the worker; it does not call that Python helper. `tools/pack-addon.ps1:498` also has a separate predicate, so delegation does not establish universal scanner parity.

3. **Executable failure scenario — P2.**  
   A source containing a self-junction and no qualifying assets forces recursive enumeration instead of an immediate negative answer. Whether it terminates by path limits or suppresses traversal errors depends on the interpreter; do not promise a particular exception.

4. **Decision — IMPLEMENT_WITH_CHANGES.**

5. **Implementer spec — S; sealed worker change requires reseal, no PBO.**

   - Implement the bounded traversal in `pack_only.has_binarizable_assets`; delegate `_default_has_assets` to it.
   - Inspect entries without following links. Do not descend into mount-point junctions or symbolic links; do not classify every reparse point as a link, because that would exclude OneDrive placeholders.
   - Preserve suffix matching and explicit `pack_only=True` short-circuiting. Preserve the worker’s `build_source_unavailable` mapping for surfaced `OSError`s.
   - This predicate is not build-source authorization; retain any subsequent source-link rejection.
   - **Regression:** Windows self-junction with no assets must return false with bounded traversal; a separate fixture with a normal `.p3d` must return true. Add an outside-link case and a placeholder-tag classification case. Use a bounded test process for the pre-fix cyclic traversal.
   - Reconcile this helper with #195 before resealing; that draft is not present here.

## ITEM 4d7e

1. **Verdict check — CONFIRMED, P3 documentation defect.**  
   `tools/mcp_capture.py:1547-1558` computes the effective crop, and `:1600-1603` saves that cropped image. The public description at `tools/dayz_mcp/server.py:6049-6051` says “native-resolution frame” without explaining this.

2. **Fix check — correct and minimal.**  
   Preserve `fullres_path`. Describe the saved image as the native-resolution **effective surface**, after client-area selection and cropping, before inline downscaling.

   Description compaction at `tools/dayz_mcp/tool_catalog.py:111-144` can omit a late sentence; check the full published description and its discovery path.

3. **Executable failure scenario.**  
   Request `crop="0.2,0.0,0.8,1.0", save_fullres=True`; the saved file represents the crop. Applying whole-window coordinates to it selects the wrong region.

4. **Decision — IMPLEMENT.**

5. **Implementer spec — S; documentation only, no PBO/reseal.**

   - Edit the `capture_screenshot` description in `server.py`.
   - State that `fullres_path` contains `effective_surface`, whose dimensions—not whole-window dimensions—apply to the file.
   - No result, filename, or error-code changes.
   - **Regression:** inspect published metadata through `effective_schema.resolve_effective_schemas`, defined at `tools/dayz_mcp/effective_schema.py:29-38`, and assert the crop/save relationship is explicit. Keep behavioral crop/save checks separate from wording checks.

## ITEM 6d21

1. **Verdict check — CONFIRMED conditional failure, P2.**  
   The cap is 512 at `addon/scripts/5_Mission/MCPClientBridge.c:450`. The scan at `:3808-3824` cannot find a door whose only matching component is 876.

2. **Fix check — correct for the reported building, not a universal component-count solution.**  
   Raising the cap to 2048 covers the reported indices. Preserve first-match selection and the `doorstwin` exclusion at `:3827-3855`.

   No geometry component-count API was established. The local vanilla `Object` API exposes component lookup methods, but that does not establish a count method.

3. **Executable failure scenario.**  
   For a building where `GetDoorIndex(876) == 5` and indices 0–511 do not match, `action_use(..., door_index=5)` returns `door_component_not_found` at `:3823`, despite a valid door index.

4. **Decision — IMPLEMENT.**

5. **Implementer spec — S; PBO rebuild and in-game check required, no launcher reseal.**

   - Set `ACTION_USE_DOOR_COMPONENT_CAP` to 2048 and update its comment.
   - Preserve Python routing at `tools/dayz_mcp/server.py:6806-6823`, wire validation at `loopback.py:1237-1248`, and existing errors/echoes.
   - **Offline regression:** exercise the existing scan representation with a sole match at 876, an ordinary low-index match, and no match. A constant assertion alone is insufficient.
   - **In-game acceptance:** operate the reported window door, confirm `component_index >= 512`, then independently read changed door state with `object_doors`. Measure the unsuccessful-scan duration.

## ITEM bb6d

1. **Verdict check — PARTIAL; the claimed production timing cause is wrong.**  
   Production `_DEBUG_DRAIN_SECONDS` is 5 seconds. The cited test explicitly changes it to **0.0** at `tools/tests/test_native_launcher_backend.py:1680-1682`.

   `_PublicRequestWriter` starts a real thread at `tools/dayz_mcp/native_launcher_backend.py:978-985`; two zero-time joins at `:1350-1354` can fail before that thread completes.

2. **Fix check — do not widen the shared drain constant.**  
   That constant governs several cleanup/watchdog paths, not just the writer. Preserve the zero-drain condition that this test uses to examine missing completion notifications. A fake that succeeds only when given a larger timeout merely tests its own timeout rule.

3. **Executable failure scenario — P2 test defect.**  
   Run the test’s CREATE/EXIT event sequence while preventing the writer thread from completing until after both zero-time joins. It raises `native_launcher_request_writer_stuck` instead of returning root exit 0. This mechanism is source-confirmed; the historical loaded run was not reproduced.

4. **Decision — IMPLEMENT_WITH_CHANGES.**

5. **Implementer spec — S; test only, no PBO/reseal.**

   - Make request-writer completion deterministic in this ownership test, using a synchronous writer double or explicit completion coordination.
   - Keep `_DEBUG_DRAIN_SECONDS=0.0` for the missing-zero-notification scenario.
   - Retain real writer cancellation/timeout coverage in the dedicated writer scenarios; do not replace all writers across the test class.
   - No production behavior or caller contract changes.
   - **Regression:** delay scheduling deterministically; the ownership test must still return 0 and verify handle cleanup without depending on the writer thread winning a scheduling race.

## ITEM c561

1. **Verdict check — PARTIAL.**  
   Missing mod-under-test attestation is confirmed. Calling the current result a promised mod-initialization PASS is stronger than the implementation.

   Server mode starts the process at `tools/dayz_mcp/dayz_test_worker.py:766-770`. The all-mode readiness check at `:793-805` uses owned UDP readiness; `dayz_test_readiness.py:141-154` checks process/port ownership. Neither proves project scripts ran. `dayz_test_tool._compact_result` at `:1460-1510` reports launch status and readiness, not project initialization.

2. **Fix check — neither proposal is ready as written.**  
   A guessed project log prefix makes quiet mods false failures and unrelated matching logs false passes. Zero `.c` entries is legitimate for config-only mods and does not prove how scripts were resolved under filepatching.

   `tools/native-launchers/dayz-test-v1/src/launcher.cpp:1032-1050` supplies no include-list argument. Also, `tools/dev/pbo_provenance.py:108-112` rejects compressed entries; its parser cannot simply become a universal PBO gate.

3. **Executable failure scenario — P2 assurance gap.**  
   Install a config-only artifact where a script-bearing project build was expected; start server mode. The worker can acknowledge a running server and the public result can say `status:"succeeded"` while no project script executes. The SimpleGroup artifact itself was not inspected.

4. **Decision — OWNER.**  
   Choose whether `dayz_test_run` remains a launch tool or gains an explicit project-attestation contract; recommend opt-in required artifacts and initialization evidence declared by project policy.

5. **Implementer spec — not authorized yet.**  
   The selected contract must distinguish artifact presence, script execution, and feature acceptance; include-list enforcement and deployed-artifact identity belong in that design.

## ITEM c879+769c

1. **Verdict check — PARTIAL.**  
   The server performs a position assignment at `addon/scripts/5_Mission/MCPBridge.c:1416-1424`; Python returns that result at `tools/dayz_mcp/server.py:4804`. There is no client wake step.

   Source alone does not prove the physics mechanism or that it occurs after every teleport.

2. **Fix check — reject the proposed automatic movement.**  
   There is no zero-speed movement mode: `tools/dayz_mcp/player_move.py:40` maps movement speeds to 1, 2, and 3. A 0.3-second walk changes position. `player_move` targets the local client, whereas teleport can select another player by UID. The proposal also supplies an unverified direction shape.

3. **Executable failure scenario — P3 here; runtime reproduction required.**  
   The ticket’s state is an unfocused client, teleport above a moving platform, then no movement input. The reported wrong observation is constant height with no floor linking. That observation was not recreated in this review.

4. **Decision — IMPLEMENT_WITH_CHANGES: documentation only.**

5. **Implementer spec — S; no PBO/reseal.**

   - Add a qualified caveat to `player_teleport`: position assignment does not attest client physics settlement or moving-floor attachment.
   - Document the reported workaround `player_move(speed="walk", phase="hold", hold_s=1)` as actual movement, restricted to the intended local player.
   - Preserve teleport parameters and results. Add no automatic `settle` operation.
   - **Regression:** published description distinguishes assigned position from settled physics and describes movement honestly.
   - An automatic settle feature remains deferred until an in-game experiment verifies a non-displacing mechanism and correct player targeting.

## ITEM fde3

1. **Verdict check — CONFIRMED limitation, P2.**  
   Non-door world targets use component `-1` at `addon/scripts/5_Mission/MCPClientBridge.c:3859-3862`. Failed `action.Can` becomes `condition_failed` at `:3895-3899`.

   I also executed wire validation: an `action_use` request containing `component_index:2` returns `(False, "bad_args")`.

2. **Fix check — substantial corrections required.**  
   The 511 maximum reproduces the high-component limitation. An optional integer cannot distinguish omission from component zero in Enforce: `MCPMessages.c:43-47` documents this.

   The proposed client argument-hash bump does not exist: `tools/dayz_mcp/bridge_readiness.py:227-235` explicitly says the client has no `ach` gate.

   Empty selection names do not prove an invalid component: vanilla `Object.GetActionComponentNameList` documents separate invalid/default/named return values at `scripts/3_game/entities/object.c:197-198`.

3. **Executable failure scenario.**  
   A custom action requires a real component index; the caller selects its world object through plain `action_use`. The bridge supplies `-1`, so its condition rejects. Trying to supply the index currently fails ingress validation.

4. **Decision — IMPLEMENT_WITH_CHANGES.**

5. **Implementer spec — M; PBO rebuild and in-game check required, no launcher reseal.**

   - Add public `component_index` and `cursor_pos` parameters to `action_use`. Component mode requires both; it is world-only and mutually exclusive with `door_index`.
   - Use a distinct wire command, proposed `action_use_component`, with required action, classname, component index, and finite world-space cursor position. Do not infer mode from an optional scalar.
   - Accept nonnegative Enforce-range integer indices, including values above 511. Perform one component lookup, not an index scan.
   - Add dispatch, `MCPArgs` fields, client capability advertisement, `loopback.CLIENT_COMMANDS`/schema, and the mapping in `bridge_readiness._BRIDGE_COMMAND_TOOLS`.
   - Reject invalid component lookup with `component_not_found`; retain valid default components. Use the supplied cursor position instead of guessing the first selection’s center.
   - Python must fail closed with proposed `component_not_supported` when the capability is absent. Echo and verify the selected component. Preserve existing action-condition/start errors.
   - Update `result_prune` ownership for `component_index` and action-result fields. This call site is missing from the first proposal.
   - **Regression:** index 0 and index 876 forward correctly; absence, bool, negative index, invalid cursor vector, incompatible target, and old PBO are refused. In-game, verify a non-door component-gated effect and an invalid-index refusal.

## ITEM aa01

1. **Verdict check — CONFIRMED removal gap; proposed result interpretation is wrong.**  
   `inventory_attach` creates the item at `addon/scripts/5_Mission/MCPBridge.c:1963` or `:1986`, but does not register it. Its top-level `object_id` at `:2004-2006` identifies the **destination owner**.

   `object_delete` only resolves registered objects at `:670-681`; an unknown positive ID returns success with `deleted:0`.

2. **Fix check — preserve owner identity; no special deletion API is justified.**  
   Replacing top-level `object_id` with the child ID breaks its meaning.

   Existing `GetGame().ObjectDelete` already deletes registered objects. Vanilla also deletes a player attachment using this API at `scripts/3_game/gameplay.c:1054-1065`. The proposed `DeleteAttachment` mechanism is unnecessary.

   Registry consumers include `hands_take`, `RuntimeObjectId`, and `ResolveCommandObject`; registration makes the new item available to all of them.

3. **Executable failure scenario — P2.**  
   Attach garment A to Armband by type/position; the reply supplies no child ID. Trying garment B hits `slot_occupied` at `:1956-1960`. Deleting the echoed owner ID would delete the survivor when that owner was registered.

4. **Decision — IMPLEMENT_WITH_CHANGES.**

5. **Implementer spec — M; PBO rebuild and in-game check required, no launcher reseal.**

   - After successful attachment/cargo postconditions, register `attachedItem` using the attachment command’s unique `command.id`, following the registry convention at `MCPBridge.c:651`.
   - Extend `MCPInventoryAttachReceipt`, currently only destination/slot at `MCPMessages.c:549-553`, with proposed `item_object_id`.
   - Preserve top-level owner `object_id`. Return the child ID inside `inventory_attach.item_object_id`.
   - Leave `DispatchObjectDelete` unchanged. Document same-run identity and the distinction between owner and item.
   - Update registry-origin wording in `object_inspect`, `hands_take`, and related tools where it claims IDs only originate from `world_spawn`.
   - **Offline regression:** source/contract checks verify registration of the created item and distinct owner/child fields.
   - **Decisive gate:** attach A, inspect the child, delete its ID, confirm the owner survives and the slot is free, then attach B. Repeat for cargo and repeated deletion.

## ITEM 97de+0cb3

1. **Verdict check — CONFIRMED behavior; PARTIAL bug classification.**  
   `identity_migration.scan_dayz_mcp_processes` adds every matching process outside the explicit allowed identities at `:781-786`. `_assert_quiescent` refuses blockers at `:887-897`. A settled receipt returns before that gate at `:1341-1350`.

   Supported instance identity is absent: `runtime_state.py:208` fixes the state directory and `host_config.py:348-350` fixes the registration name.

2. **Fix check — reject root inference as a minimal fix.**  
   Port identity does not establish state-root ownership. The current processes do not publish an accredited root discriminator, and WMI daemon startup does not inherit the client environment (`daemon.py:1739-1743`).

   Keeping unknown processes as blockers would still block the reported patched clones. Removing unknown blockers would weaken migration safety. Both daemon startup and `tools/p0s_gate.py:318` call the migration entry point.

3. **Executable failure scenario — P3 product limitation.**  
   An unsettled root A, no listener on A’s port, and one unrelated `python -m dayz_mcp` process cause `dayz_mcp_process_present`. The scan has no root argument with which to distinguish B.

4. **Decision — OWNER.**  
   Decide whether independently configured instances are supported; if yes, establish explicit state-root/provenance identity across launcher, daemon, clients, registration, and migration before narrowing quiescence.

5. **Implementer spec — not authorized yet.**  
   The external process-cleanup workaround is not evidence that instance isolation has been implemented here.

## ITEM 7f27

1. **Verdict check — CONFIRMED, P2 contract defect.**  
   The ownership table at `tools/dayz_mcp/result_prune.py:120-137` leaves the reported generic fields unmanaged. I executed `prune_unfilled_fields("vehicle_telemetry", ...)`: `clicked:0`, empty `handler`, and `user_id:0` survived.

2. **Fix check — extend only proven ownership.**  
   I ran the existing census separately for all 21 reported fields. Eighteen resolved; three did not:

   - `accepted`: untyped write at `MCPClientBridge.c:4731`.
   - `found` and `object_id`: broadly attributed writes in `MCPBridge.PostJobSuccess` at `:3881-3883`.

   Do not invert the whole result contract or guess those three owner sets.

   Pruning runs through all four result paths at `server.py:788`, `:818`, `:1951`, and `:2036`. Error conversion occurs before pruning, so error payload policy is separate.

3. **Executable failure scenario.**  
   A successful vehicle telemetry result contains unrelated UI defaults; a consumer sees `clicked:0` even though that command never attempted a click. This was reproduced directly.

4. **Decision — IMPLEMENT_WITH_CHANGES.**

5. **Implementer spec — M; Python only, no PBO/reseal.**

   Add these verified owner sets:

   | Fields | Owners |
   |---|---|
   | `clicked`, `handler`, `user_id` | `ui_click` |
   | `delivered`, `dik` | `key_press` |
   | `requested` | `player_respawn` |
   | `action`, `target`, `distance`, `started` | `action_use`, `action_use_door`, `action_use_target` |
   | `confirmed` | `hands_take`, `weapon_state` |
   | `sent` | `exec_enforce`, `notify_players` |
   | `deleted` | `object_delete` |
   | `y` | `surface_query` |
   | `phase`, `source` | `object_anim` |
   | `deferred` | `inventory_give` |
   | `count_total` | `entities_query` |

   - Preserve `accepted`, `found`, `object_id`, common metadata, and unknown fields.
   - Keep zero/false/empty values for owners; remove these keys from nonowners regardless of value.
   - Update comments and changelog. Document the three deliberately unresolved residual fields.
   - **Regression:** table-driven owner/nonowner cases for all additions, plus census verification and embedded/client result-path coverage.
   - If shipped with fde3, add `action_use_component` to the action-field owners and `component_index` owners. Review that combined source snapshot.

## ITEM c8c7

1. **Verdict check — CONFIRMED, P2.**  
   Rotation data exists in `RotationResult` at `tools/dayz_mcp/dayz_test_storage.py:103-112`, but `process_lifecycle.py:2793-2795` only audits it. Public result assembly at `dayz_test_tool.py:1460-1510` has no storage fields.

2. **Fix check — status transport is sound; a transient dictionary is insufficient.**  
   A daemon restart loses an in-memory map. Returning false after a failed post-rotation launch can hide a real reset. The tool currently skips its normal status fetch on many failed calls at `dayz_test_tool.py:1765-1769`.

   Trace the single rotation call at `process_lifecycle.py:3646`, failed-launch settlement, manifest cloning, both status projections, and final tool normalization. Do not depend on best-effort audit output.

3. **Executable failure scenario.**  
   Launch with a changed modset over existing storage. `prepare_storage` renames it and returns `mission_world_and_character_reset` at `dayz_test_storage.py:577-590`. The launch can succeed while its public result contains no reset information.

4. **Decision — IMPLEMENT_WITH_CHANGES.**

5. **Implementer spec — M; daemon/tool change, no PBO or worker reseal.**

   - Carry the rotation result from `_rotate_storage_for_launch` into the provisional run before spawning.
   - Persist optional, validated rotation metadata on `RunRecord`; preserve it through success, failed-launch settlement, cloning, and reload. Legacy records default to unknown.
   - Publish it in lifecycle status and extract it by exact run ID in `_execute_request`.
   - Always include `storage_rotated`, `storage_backup`, and `storage_reset_notice` in `_compact_result`. Use `null` for unavailable/not-applicable observations, false only for a measured nonrotation.
   - Preserve true rotation facts on later launch failures; read status for failed attempts too, before terminal normalization can discard their run ID.
   - Retain the historical reset fact after launch acknowledgement. For retries before replacement storage exists, recover the pending notice from the validated completed rotation journal matching the mission/seal; `prepare_storage` currently ignores completed journals at `:529-530`.
   - Do not describe process acknowledgement or a matching marker as proof that CE restored world contents.
   - Persistence compatibility: old records remain readable; downgrade may discard added diagnostic metadata, but must leave storage, backups, and rotation journals unchanged.
   - **Regression:** mismatch rotation through public tool output; subsequent spawn failure; daemon reload; retry before new storage exists; ordinary matching-seal reuse; unrelated-run isolation; unavailable status yielding null.
   - The full H11 meaning of “new storage accredited” still needs an explicit acceptance definition; this implementation must not silently invent it.

## ITEM bf5c+8cf9

1. **Verdict check — PARTIAL.**  
   Unisolated build inputs are confirmed at `tools/dayz_mcp/dayz_test_worker.py:739-745` and native `BuildAddonCommand` at `launcher.cpp:1042-1050`.

   AddonBuilder’s derived `-addon` scope is an external behavior reported by the tickets; this review did not observe a real child command line or reproduce poisoning.

2. **Fix check — not ready for implementation from this export.**  
   A junction can be canonicalized back to the original source, defeating isolation. A copied source can alter dependency/path resolution. Native command composition—not `native_launcher_backend.py`—owns AddonBuilder arguments.

   The current command contains no `-noLogs`; adding/removing it here cannot fix the proposed logging issue. The proposed test could pass while AddonBuilder still chooses a different effective addon root.

3. **Executable failure scenario — P3 until tool behavior is reproduced.**  
   Source A contains binarizable assets; a broken sibling config B lies under the parent AddonBuilder actually supplies as `-addon`. Reported behavior is binarize exit 1 and worker `build_failed` at `:755`. The effective parent was not measured here.

4. **Decision — DEFER.**  
   Obtain the frozen #195 diff/approved plan and its actual AddonBuilder command-line evidence before authorizing another implementation of the same build boundary.

5. **Implementer spec — pending that evidence.**  
   Acceptance must prove isolation under both source-parent and temp-root poisoning, preserve required dependencies, preserve include-list behavior, and expose bounded diagnostics belonging to the current build. Any worker/native change requires reseal; it does not inherently require an in-game cycle.

## ITEM 1d31

1. **Verdict check — PARTIAL: the alternate route exists; exact menu behavior remains unverified.**  
   `key_press` is already documented as mission-only at `tools/dayz_mcp/server.py:5815-5818` and calls only mission `OnKeyPress` at `MCPClientBridge.c:1381`.

   `input_trigger` documents the game route at `server.py:5834-5839`; the bridge invokes game press/release at `MCPClientBridge.c:283-287`.

2. **Fix check — documentation cross-link is appropriate.**  
   Do not claim `key_press` sends key-up. The proposed `key=1` example is also wrong: the public argument is `dik`.

   Do not add a camera-distance guard or classify the accompanying client death as a proven engine crash; that mechanism was not isolated.

3. **Executable failure scenario — P3 for the specific menu claim.**  
   With InGameMenu open, `key_press(dik=1)` reports delivery of a mission callback. That result does not attest menu closure. The reported unchanged menu was not reproduced.

4. **Decision — IMPLEMENT_WITH_CHANGES: documentation steering.**

5. **Implementer spec — S; no PBO/reseal.**

   - Add to `key_press`: use `input_trigger(kind="key", dik=1, entry="game", phase="click")` for game-level key handlers.
   - Explain that callback delivery is not confirmation of the intended UI effect; verify with `ui_tree`.
   - Preserve results and input semantics.
   - **Regression:** published description includes the correct route and parameter names.
   - **Runtime closure:** verify the actual pause menu closes through that route. Keep the camera observation deferred within this item.

## ITEM 6084

1. **Verdict check — CONFIRMED source-level fix; current deployment unverified.**  
   The public launch check calls `evaluate_steam_session()` at `tools/dayz_mcp/dayz_test_tool.py:1994`. That function selects `WindowsSteamPreflightProvider(real_registry=True)` at `steam_preflight.py:446-448`, which reads through WMI at `:168-181`.

   The client asks for outside-app daemon spawning at `server.py:1613-1620`; Windows attempts WMI at `daemon.py:1836-1839`.

2. **Fix check — close; no additional mechanism required.**  
   Daemon-side providers intentionally use the registry view inherited by their children (`steam_preflight.py:134-137`). Changing every provider to WMI is not the correct invariant.

   WMI failure can fall back to Popen; the implementation does not guarantee that every deployment always escaped virtualization.

3. **Executable failure scenario today.**  
   No deterministic defect was established on the normal WMI path. The ticket’s manual app-child launch bypasses this mechanism. Fallback and embedded execution remain separate conditions to verify.

4. **Decision — CLOSE as addressed in the supported WMI daemon path.**  
   File the call chain above as resolution evidence, with the deployment/fallback limitation.

5. **Implementer spec — none.**  
   Do not synchronize the app’s private registry copy as an additional fix.

## ITEM 52c3

1. **Verdict check — PARTIAL.**  
   The reported messages concern CE storage loading, but their origin does not prove MCP, shutdown handling, filesystem access, or cloud synchronization cannot contribute.

   The delayed-boot mechanism remains a hypothesis.

2. **Fix check — WRONG as an executable diagnostic proposal.**  
   `STORAGE_POISON_RPT_SIGNATURES` at `tools/dayz_mcp/dayz_test_storage.py:73-76` has no runtime consumer. The repository search found only its declaration/documentation and the tuple assertion in `tools/tests/test_dayz_test_storage.py:661`.

   Adding literals changes no launch result or audit. Bare `valid:NO` also needs context to distinguish rejected expected storage from an ordinary initialization.

3. **Executable failure scenario — P3 here.**  
   The proposed 45-minute delayed restart is a reproduction procedure, not an isolated source-confirmed cause. Neither the retained bytes nor delayed engine reads were observed in this review.

4. **Decision — DEFER investigation; do not close as definitively outside MCP.**  
   Compare immediate and delayed restarts with controlled storage hashes, host reads, shutdown evidence, and fresh RPTs before choosing a mitigation.

5. **Implementer spec — none yet.**  
   Any future diagnostic needs a real bounded scanner, current-run attribution, an expected-existing-storage condition, and explicit public propagation. A tuple/string test is insufficient.

## ITEM 0eb8

1. **Verdict check — PARTIAL.**  
   Flag handling is confirmed: `tools/dayz_mcp/core.py:331-357` accepts the supplied mask, and `addon/scripts/5_Mission/MCPBridge.c:3381-3389` preserves it for `CreateObjectEx` at `:632`.

   Vanilla-class reproduction does not prove the cause is engine cleanup or absence of AI. No isolated death mechanism was established.

2. **Fix check — reject the causal and survival claims.**  
   “Static props” and “add ECE_INITAI if the fixture must stay alive” are stronger than the evidence. AI activation also changes fixture behavior.

   The existing help already gives living-infected flags 3108 at `tools/dayz_mcp/server.py:3348-3349`.

3. **Executable failure scenario — P3 here.**  
   Spawn the reported infected with flags 8389668 and measure health after approximately 12 minutes. Death and its absence of log output were reported, not reproduced.

4. **Decision — IMPLEMENT_WITH_CHANGES: qualified documentation only.**

5. **Implementer spec — S; no PBO/reseal.**

   - Update `WORLD_SPAWN_FLAGS_LINE` to state that spawning an infected without AI initialization does not establish a durable living visual fixture.
   - Attribute the delayed-health-zero observation as reported; state that the cause is unverified.
   - Recommend checking health immediately before a visual judgment. Mention the living-infected recipe without guaranteeing survival or automatically changing flags.
   - Preserve accepted masks and result/error contracts.
   - **Regression:** published documentation includes the qualification and observation check, without asserting a proven death mechanism.
   - Cause investigation requires controlled AI/no-AI runs with damage and lifecycle evidence.

## BATCHES

These batches have separate production-function ownership. Shared changelog edits should be consolidated by the integrator.

| Order | Batch | Items | Deployment / review gate |
|---|---|---|---|
| 1 | Supervisor terminal transport handling | 734c | Python; protocol/race tests |
| 2 | Storage reset visibility and retained evidence | c8c7 | Python; durable-state and public-result tests |
| 3 | Junction-safe asset predicate | dbe0 | Worker reseal; Windows traversal gate; reconcile #195 |
| 4 | Action targeting and scalar ownership | 6d21, fde3, 7f27 | **PBO and in-game cycle**; combined census snapshot |
| 5 | Created inventory-item identity | aa01 | **PBO and same in-game cycle**; attachment/cargo removal gate |
| 6 | Deterministic ownership-test fixture | bb6d | Test only |
| 7 | Published contract clarification | 4d7e, c879+769c, 1d31, 0eb8 | Documentation; metadata checks |

Batch 4 groups the overlapping `DispatchActionUse` changes and ownership-table updates. Its 7f27 Python slice can be reviewed independently, but the final census must include the new component command.

Build the PBO once after batches 4 and 5 are accepted. Verify the deployed artifact before the shared cycle. That cycle must independently check high-index doors, non-door component effects, owner survival after child deletion, cargo removal, and unchanged ordinary action behavior.

c561 and 97de+0cb3 require owner decisions. bf5c+8cf9 and 52c3 require additional evidence. 6084 requires no implementation.

## PREMISE

- **Launch success is being promoted into feature acceptance.** A server process, owned UDP socket, bridge readiness, deployed project scripts, and project initialization are different assertions. c561 exposes that distinction.
- **Several tickets contain observations plus unproven causes.** Teleport hover, infected death, delayed CE rejection, and client death cannot be converted into engine facts from this checkout.
- **The build tickets share an artifact boundary.** Asset traversal, addon-root scope, include lists, and deployed contents must be reconciled; fixing each independently can still produce a vacuous green gate.
- **Optional Enforce scalars need presence semantics.** Component zero is real, and absent JSON integers arrive as zero. The existing command-mode convention should govern fde3.
- **The storage seal is not an artifact-content seal.** `dayz_test_storage.py:174-198` fingerprints mod roles/identifiers, not PBO bytes. Do not replace it with a content hash casually: doing so could rotate worlds on ordinary rebuilds.
- **Some useful outputs are deliberately unknown.** Null status, unresolved scalar ownership, and interrupted-call completion must remain distinguishable from false, zero, and success.
- **The supplied release state is not current deployment proof.** Source-level fixes and proposed reseals must be verified against the artifact actually used by clients.

## GATE GAPS

For the first three implementation batches:

1. **734c:** an EOF fake can miss inherited handles that keep stdout open after interpreter death, blocking stdin writes, and transport-failure/response races. Include an actual supervised interpreter-death test in the writable integration environment; retain the live-hang refusal case.

2. **c8c7:** a test that injects `RotationResult` directly into `_compact_result` can pass while real start settlement loses it, a daemon reload drops it, or failed-terminal normalization removes its run ID. Exercise rotation through lifecycle persistence, failure settlement, reload, and the public tool response.

3. **dbe0:** a nearby `.p3d` can make both implementations pass without visiting the junction. Mocked reparse tags can also miss real Windows junction behavior or incorrectly exclude OneDrive files. Use a no-assets self-junction and a genuine Windows traversal gate.

For the bridge batches, source assertions do not prove Enforce compilation, action effects, inventory replication, or slot release. Those require the shared in-game cycle.

## NOT VERIFIED

- **Tests:** `python -B -m unittest tests.test_mcp_supervisor tests.test_result_prune tests.test_owned_scalar_census` passed **69 tests**.
- A broader selected run attempted **63 tests and reported 51 errors**. Native fixtures could not create temporary directories under the read-only sandbox; server-importing modules failed because the available Python lacked `anyio`. This was not a usable product-regression verdict. The full suite was not run.
- No tree, shared memory, inbox, handoff, or runtime state was modified; no commit was made. This response is the review handoff.
- No DayZ processes were launched, stopped, or mutated. No lease or lifecycle operation was needed for the read-only review.
- No PBO build, launcher reseal, deployed hash verification, or in-game acceptance was performed.
- External ticket artifacts—SimpleGroup PBO/logs, SecretRock component measurements/traces, delayed CE snapshots, historical load logs, and infected captures—were not independently inspected.
- #195’s frozen diff and approved build-isolation plan are absent from this export.
- The export’s declared Git provenance was supplied by the brief; this review anchored conclusions to file contents rather than independently attesting `origin/main`.
- Vanilla API evidence used the local tree under `C:/Users/guill/OneDrive/Documentos/DayZ Projects/scripts/`; declaration and script usage do not establish native behavior in every DayZ build.
- Automatic non-displacing teleport settlement, the exact pause-menu ESC result, infected death causation, delayed CE rejection causation, and the effective AddonBuilder `-addon` root remain unverified.
- Full H11 clearance of a pending reset notice requires an explicit definition of “new storage accredited.” Neither launch acknowledgement nor bridge readiness proves restored CE contents.
- Prior memory was used only for review discipline; all reported code findings were re-anchored to this checkout.

