## ITEM c561

1. **Decision restated**

   Project attestation is opt-in through project policy: ordinary `dayz_test_run` remains a launch tool, while a policy-enabled run fails unless its declared artifacts and initialization evidence are present.

   Artifact presence, initialization evidence, and feature acceptance must remain separate claims. Neither an arbitrary project-name substring nor a nonzero `.c` count proves initialization. The existing PBO reader also rejects compressed entries; it cannot silently become a universal archive verifier.

2. **Evidence**

   Paths and line numbers below refer to the supplied `6671fd2` export. New interfaces and behavior in these specifications are **[DESIGN]**, not existing APIs.

   - Launch composition: `tools/dayz_mcp/dayz_test_worker.py:239` (`_mods`) and `:249` (`_start_core`). The project mod is an unconditional entry at `:242`.
   - Actual launch/readiness: `dayz_test_worker.py:766` starts server mode; `:788` starts all mode. All-mode readiness at `:795` calls the owned-UDP probe, whose acceptance at `dayz_test_readiness.py:141`–`:154` proves process/port ownership, not project initialization.
   - Failure cleanup: `dayz_test_worker.py:813`–`:821` stops only a newly created run and preserves degraded-cleanup reporting.
   - Public result assembly: `tools/dayz_mcp/dayz_test_tool.py:1636` (`_execute_request`), `:1710` (terminal parsing), `:1774` (post-launch projection), and `:1800` (compact result).
   - Policy surfaces that must agree:
     - `dayz_test_request.py:243` (`RequestProjectPolicy`) and `:259` (`_validate_policies`);
     - `tools/build_native_launcher.py:433`, `:512`, `:568`, `:707`;
     - `native_bundle.py:1312` (`_parse_policy`);
     - `tools/native-launchers/dayz-test-v1/src/app_main.py:161`–`:174` (worker-side policy reconstruction).
   - Worker terminal producers and consumer: `app_main.py:55`, `:265`, `:351`; `dayz_test_tool.py:106`, `:119`, `:621`–`:675`. The terminal currently has exactly five keys.
   - Sealing: `native_bundle.py:61` pins worker/request sources; `:67` enumerates packaged modules. The builder separately packages them at `tools/build_native_launcher.py:56`.
   - PBO limitation: `tools/dev/pbo_provenance.py:81` implements `read_pbo`; `:108`–`:112` rejects compressed/encoded entries.
   - Log boundaries/read limits: `launch_logs.py:90`, `:203`, `:254`; `log_tail.py:134` handles bounded reads, replacement, truncation, and partial lines.
   - Relevant contract: `product-spec.md:146` H11 and `:148` H13. The earlier review’s distinction is explicit in `SOL_REVIEW.md:124`–`:143`.

3. **Spec**

   **Policy and files**

   Add an optional `attestation` member to project policy, defaulting to absent/disabled. Implement its closed validation in `dayz_test_request.py`, carry it through the builder, sealed policy loader, and `app_main.py`, and add a packaged `dayz_test_attestation.py` helper.

   Minimal policy shape:

   - `version: 1`;
   - `artifacts`: up to eight requirements, each with a unique `id`, a relative `pbo` path, and explicitly required archive-entry names;
   - `initialization`: up to eight requirements, each with a unique `id`, a role, a nonempty literal `pattern`, and either `source="script_log"` or `source="profile_file"` with a validated relative filename;
   - `timeout_s`: finite, positive, at most 300 seconds; default 60.

   Reject unknown keys, duplicate IDs, traversal, absolute artifact/profile paths, and invalid types. Roles determine which initialization rules apply to the processes started by that request. Report other roles as `not_applicable`, without claiming they initialized.

   **Execution**

   - Disabled policy preserves existing launch behavior and terminal shape.
   - After any requested build, check the **deployed artifact that appears in the effective mod set**, before starting DayZ. Resolve each declared relative PBO against that set; zero or multiple matches fails closed. This also supports a candidate override without inspecting the excluded live project folder.
   - Verify exact required entries, and record the deployed PBO SHA256. Do not infer a log prefix or impose a global “must contain scripts” rule.
   - Initially support the plain PBO format verified by the existing reader. Unsupported compression/encoding is **unverifiable**, never “entries absent” or PASS. Keep parsing and archive size bounded.
   - Capture initialization-file boundaries immediately before starting each applicable role. Accept only complete matching lines written after that boundary. For script logs, track newly created launch logs as well as appends; do not select old logs merely because they are newest.
   - Poll initialization under the existing admitted launcher transaction, while its heartbeat supervisor still owns the lease.
   - Match absence at the deadline fails attestation. Unreadable evidence or exceeded scan limits also fail, with a distinct unverifiable reason.
   - Place initialization checks inside the worker’s existing launch `try` block, so a failure cleans up a newly created run through the lifecycle broker. For reattach, preserve the existing server; report the affected supplied run rather than stopping the whole run.

   **Result and errors**

   Add a bounded `attestation` report to enabled-policy results:

   - `status`: `passed`, `failed`, `unverifiable`, or `pending`;
   - `artifacts`: requirement IDs, status, and SHA256 where measured;
   - `initialization`: requirement IDs and status;
   - no raw logs or unbounded entry lists.

   New worker error codes:

   - `project_attestation_artifact_missing`;
   - `project_attestation_initialization_missing`;
   - `project_attestation_unverifiable`.

   Preserve launch errors and `cleanup_degraded` independently. A preflight may validate the policy and installed artifacts, but reports initialization as `pending`; it cannot attest an initialization it did not run. If preflight includes a future build, report artifact validation as pending for that build.

   Extend `WorkerResult`, `DayzTestWorkerError`, `_failure_after_cleanup`, terminal emission, `WorkerTerminal`, terminal parsing, and compact projection to carry the report. Accept the legacy five-key terminal and one explicitly defined extended shape; reject arbitrary additional keys. Keep the complete terminal within the existing **4096-byte** limit. C++ forwards terminal bytes without interpreting the JSON keys (`launcher.cpp:1394`, `:1542`), so no C++ protocol change is needed.

   Update the tool description, H11/H13, and changelog: launch success has no project-attestation meaning unless policy enables it; attestation success does not prove feature acceptance.

   **Compatibility**

   - Existing projects and callers remain opt-out.
   - New Python server accepts old policy documents and terminals as disabled attestation.
   - Enabling policy requires a **launcher reseal**, including the new packaged helper and changed policy/worker/bootstrap sources.
   - Old servers cannot consume the enabled extended terminal safely; deploy the Python server and resealed launcher together. Never downgrade an enabled requirement to disabled because an older component lacks support.
   - No PBO bridge change or DayZ persistence-format migration. Existing storage rotation/recovery remains authoritative.

4. **Tests**

   Proposed regression tests; none were executed in this read-only task.

   - `test_c561_required_script_entry_missing_fails_before_start`: a plain PBO containing only `config.bin` lacks a policy-required `.c` entry; no lifecycle start occurs.
   - `test_c561_initialization_requires_post_launch_evidence`: an old matching line fails; a matching line appended after the captured boundary passes.
   - `test_c561_checks_deployed_candidate_artifact`: source-tree/live-folder evidence cannot satisfy a requirement for the effective candidate PBO.
   - `test_c561_failure_cleans_only_new_run`: missing initialization stops the newly created run; reattach failure preserves its server; cleanup failure remains visible.
   - `test_c561_report_reaches_public_result`: enabled-policy success/failure retains the validated report through terminal parsing and compact projection.

   These feature assertions fail on the base because no policy attestation is evaluated or reported. Add controls for disabled/config-only projects, unsupported PBO encoding, unreadable logs, scan truncation, cancellation, and legacy terminals.

   **Later live check:** use a disposable project/profile, verify deployed hashes, and run both a valid script-bearing build and an intentionally incomplete artifact. Confirm fresh initialization evidence, failure cleanup, and final session state.

5. **Size / risk**

   **L — lifecycle-critical.** Main risk: a false attestation from the wrong artifact or stale initialization evidence; secondary risk is moving failure handling outside the owned transaction.

## ITEM 584e+f0e3

1. **Decision restated**

   While the calling session holds an active lease, every tool result exposes `lease_ttl_s`; pure reads do not renew it.

   **This is a behavior change, not merely additive reporting:** owner bridge reads currently renew the lease. The binding decision requires removing that renewal. `file_matches` is the separately authorized exception.

2. **Evidence**

   - Read classification: `tools/dayz_mcp/session_coordination.py:21`–`:46`; `command_requires_lease` at `:95`.
   - Existing owner-read renewal: `session_coordination.py:1330`–`:1357`, particularly the expiry update and renewal stamp at `:1345`–`:1349`.
   - Mutation renewal: `session_coordination.py:1373`–`:1425`; heartbeat remains the explicit renewal operation at `:1245`.
   - Authorization callers: `loopback.py:2416`; lifecycle wrapper `process_lifecycle.py:2604`, used at `:3183`, `:4119`, `:4497`, `:4929`, `:6277`, `:6943`.
   - Both client enqueue paths attach the token and consume renewal stamps: `server.py:1845`–`:1909` and `:1961`–`:2007`; carrier refresh is `:1282`.
   - Authoritative TTL already exists: `session_coordination.py:4058`–`:4064` publishes `owner.expires_in_s`; `:2309` includes the operation pin. `status()` identifies the caller’s own active lease at `:1965`.
   - Opposite operations: `server.py:1506` heartbeat, `:1513` release, `:1524` status and token disowning.
   - All-result hook precedent: `server_freshness.py:220`–`:274`, covering SDK errors and image results. Hook installation/order: `server.py:7115`–`:7118`.
   - Pruning preserves unknown keys: `result_prune.py:158`–`:178`; an added TTL does **not** require a new keep-list entry.
   - Supervisor-generated tool result: `mcp_supervisor.py:251`–`:304`; its heartbeat currently returns only `"answered"` at `:317`–`:344`.
   - Existing contrary test: `tools/tests/test_lease_queue_fifo.py:628`–`:643` explicitly expects owner reads to renew.
   - Contrary descriptions/changelog: `server.py:3179`–`:3189`, `:6885`–`:6887`; `CHANGELOG.md:86`. Contract anchors: H2/H3/H4/H7 at `product-spec.md:137`–`:142`.

3. **Spec**

   **Renewal semantics**

   In `SessionCoordinator.authorize`, retain owner-read identification/auditing and existing routing authority, but remove the read branch’s expiry update and `_note_renewed_lease`. Preserve lease-free reads, including expired/invalid optional-token handling. Mutations and explicit heartbeats retain their current renewal behavior.

   Update `_lease_renewal_contract`, `wait_for` descriptions, H3/H4, and changelog. Player/entity probes remain reads and stop renewing merely because they carry an owner token. `file_matches` renews explicitly through heartbeat.

   **Universal result annotation**

   Add a result decorator at the final MCP `CallToolResult` boundary, installed after catalog re-registration and before the freshness wrapper. It must cover successful results, SDK/tool errors, images, lists, aliases, and compact/full tool catalogs.

   Contract:

   - Active caller lease, successfully observed: finite nonnegative `lease_ttl_s`, in seconds.
   - No active caller lease: field absent.
   - Caller has a local lease but authoritative observation fails: `lease_ttl_s: null`, with `lease_ttl_status: "unknown"`.
   - TTL is advisory at response emission, includes the existing operation pin, and conveys no authorization.

   Publish it in `_meta` and a short visible text block for every result. For ordinary dictionary results, also add the top-level field to structured JSON and its corresponding JSON text representation. Preserve image bytes, original error text, `isError`, and other metadata.

   Obtain TTL through the existing **non-launching** `ControlClient.session_status()` path (`control_client.py:400`), under a short bounded observation budget. Match the authoritative caller `self.state`/lease ID and owner lease ID, and recheck the local lease ID after the observation. Do not use another session’s owner TTL or a stored token as proof of ownership. Never subtract monotonic timestamps from different processes.

   Do not acquire, heartbeat, reconcile, or lazily spawn a daemon solely to decorate a local tool result. Reuse an already available valid status observation where possible.

   **Supervisor surface**

   `server_reload` and supervisor-synthesized tool errors must satisfy the same result contract. Its existing `"answered"` heartbeat receipt is insufficient to infer TTL. The supervisor implementation must obtain a bounded status result from the appropriate worker generation and apply the same annotation rules; this must be integrated with the 734c owner.

   **Compatibility**

   - Older clients may ignore additive fields.
   - Existing daemons already publish the TTL source, so annotation can work with them; however, **nonrenewing owner reads require the updated daemon**. Mixed deployment must not claim the new renewal semantics.
   - No launcher reseal, PBO change, or persistent-format change.
   - Release results carry no TTL once the lease is released; acquire results describe the newly held lease.

4. **Tests**

   - `test_584e_all_tool_results_carry_owned_ttl`: protocol-level dictionary, image, list, alias, and error results expose the same owned TTL.
   - `test_584e_owner_bridge_read_does_not_extend_expiry`: read at second 119; mutation at/after original expiry is refused unless an explicit heartbeat intervenes.
   - `test_584e_local_reads_show_declining_ttl`: repeated logs/capture reads show decreasing TTL without heartbeat/acquire calls.
   - `test_584e_foreign_or_replaced_lease_ttl_is_not_leaked`: concurrent release/reacquire or another owner cannot decorate a result with the wrong lease.
   - `test_584e_server_reload_result_reports_ttl`: exercise the supervisor response, not just the worker wrapper.

   Replace the contrary renewal expectation in `test_read_without_token_does_not_renew_but_valid_token_does`; retain its lease-free-read controls. Add unknown-observation, expired lease, operation pin, release, and freshness/catalog coexistence cases.

   **Later live check:** hold/adopt a lease, perform host and bridge reads across the TTL boundary, observe the countdown and expiry, then verify explicit heartbeat and `file_matches` renewals.

5. **Size / risk**

   **M — lifecycle/recovery-critical.** Main risk: reporting another lease’s TTL or accidentally renewing during observation; changing owner-read renewal also affects lease carriers and expiry timing.

## ITEM 31d2

1. **Decision restated**

   `dayz_test_run(preflight=true)` validates everything that does not require box ownership, including Steam and the sealed launcher, and succeeds despite another session’s occupancy with `box_busy: true` and `occupied_by_run_id`.

   The current implementation both skips Steam validation and eventually acquires a lease. Bypassing only the initial occupancy refusal does not satisfy this decision.

2. **Evidence**

   - MCP admission occurs before execution: `server.py:3841`–`:3897` handles the box FIFO; `:3913`–`:3956` can refuse or execute takeover.
   - Adapter: `dayz_test_tool.py:1854` (`execute_dayz_test_run`); `:1898` declares Steam/extension omissions; `:1905` requires an idle session.
   - Steam is deliberately excluded at `dayz_test_tool.py:1992`.
   - Sealed execution still runs for preflight: `dayz_test_tool.py:2157`, `_execute_request` at `:1636`, `secure_launcher.py:78` and `:161`.
   - Transaction parses, checks VPP, accredits paths, then acquires a lease at `native_launcher_transaction.py:694`–`:712`.
   - Mod-directory accreditation: `request_path_authority.py:477`–`:494`, `:575`–`:580`. It currently covers explicit lists, not the implicit project-mod entry.
   - Worker preflight only resolves the mission and returns: `dayz_test_worker.py:717`–`:722`.
   - Verified bundle validates worker-runtime bytes but does not expose parsed runtime policy: `native_bundle.py:1521`–`:1530`; `VerifiedNativeBundle` at `:1002`.
   - Real Steam gate: `steam_preflight.py:412`–`:437`, with `steam_not_running` and `steam_session_stale`.
   - Opposite operations must remain outside preflight: worker build at `:724`, start at `:766`, stop at `:681`; MCP takeover at `server.py:3944`.
   - Existing contrary tests: `test_db05_preflight_diagnostics.py:72`–`:81`; `test_dayz_test_tool.py:1547`; `test_steampost_readiness.py:434`.
   - Required documentation: H11/H13 at `product-spec.md:146`, `:148`.

3. **Spec**

   Add an early dedicated preflight branch after argument validation and before box FIFO, takeover, or `_require_idle_session`. It must work when the caller already holds a lease without releasing or renewing it.

   Add a pure preflight validator shared with the worker’s preflight branch. Expose validated worker-runtime policy from the already verified bundle; do not reopen an unverified runtime JSON file or assume an existing `bundle.worker_runtime` attribute.

   Validate:

   - the existing closed request grammar and mode/run-ID matrix;
   - sealed launcher, bundle, policy, source pins, and path-root identities;
   - actual effective mod directories, including the implicit project mod or its override;
   - mission aliases against the selected runtime and absolute missions through existing accreditation;
   - applicable source/build-policy constraints without scanning/building assets;
   - VPP request requirements;
   - read-only Steam gate for client-starting modes.

   Preserve the existing desktop check where applicable. `auto_remediate_steam=true` does **not** authorize repair during preflight: a failed Steam observation returns its normal failure and remediation fields.

   The MCP preflight branch must not call `_execute_request` or `execute_secure_launcher_request`: those enter lease acquisition. It may verify the sealed executable without launching it.

   Public result:

   - retain the compact success/failure envelope;
   - add `preflight: true`;
   - `box_busy`: `true`, `false`, or `null` when occupancy could not be observed;
   - `occupied_by_run_id`: exact managed occupant when known, otherwise `null`;
   - preserve a supplied client-reattach `run_id`; mint no run ID;
   - retain `preflight_skipped_checks`, but remove `steam_session`. Continue listing extension/replacement checks omitted because they require live-run admission, and explicitly disclose process launch/readiness and pending initialization evidence.

   Occupancy is advisory, sampled separately from validation. `box_busy=true` cannot replace a bad request, missing mod, stale launcher, or Steam failure with success. Preflight ignores queue/takeover execution options after validating their types; it never queues or evicts.

   Update description, H11/H13, and changelog: preflight success proves neither a free box nor future launch/readiness success.

   **Compatibility**

   Older callers receive additive fields. Legacy preflight callers that relied on skipped Steam checks now receive the real Steam refusal. The shared worker-validator/source changes require a **launcher reseal**. No daemon/PBO protocol or persistence-format change; preflight never rotates storage.

4. **Tests**

   - `test_31d2_foreign_box_does_not_block_valid_preflight`: succeeds with the occupant ID; zero acquire/wait/adopt/stop/start/build calls.
   - `test_31d2_preflight_takeover_never_evicts`: `takeover=true`, queue mode, and positive wait budget still perform validation only.
   - `test_31d2_busy_box_does_not_mask_validation_failure`: missing mod, invalid mission, or stale source seal remains a failure.
   - `test_31d2_preflight_checks_steam_without_repair`: stale/stopped Steam fails even with remediation enabled; no repair occurs.
   - `test_31d2_preflight_with_held_lease_preserves_it`: no idle reconciliation, release, or heartbeat.
   - `test_31d2_host_and_worker_preflight_agree`: compare outcomes against independently constructed request/path/runtime fixtures.

   Update tests that currently assert Steam is not consulted. Retain worker preflight’s zero-child test and add no-storage-write assertions.

   **Later live check:** while an authorized foreign run occupies the box, perform valid and invalid preflights; verify occupant continuity and unchanged queue/lease state.

5. **Size / risk**

   **M — lifecycle-critical.** Main risk: an overlooked route still queues, adopts, repairs Steam, or executes takeover.

## ITEM 983a

1. **Decision restated**

   Successful `camera_set` echoes the applied nonzero FOV as `fov_applied`, returns `null` for zero, and documents the supplied contextual measurement without calling `GetCurrentFOV`.

2. **Evidence**

   - Python validation and call: `tools/dayz_mcp/server.py:5655`–`:5702`; validated radians are stored at `:5699`.
   - Static/matrix/orient/lookat application: `addon/scripts/5_Mission/MCPClientBridge.c:5352`–`:5423`, especially `SetFOV` at `:5417`.
   - Free-camera application: `MCPClientBridge.c:5426`–`:5467`, especially `:5461`.
   - No-getter rationale: `MCPClientBridge.c:4773`–`:4776`.
   - Opposite/read/restoration surface: `server.py:5704`–`:5725` (`camera_get`), `:5727` onward (`restore_gameplay`).
   - Shared nested camera result: `MCPClientBridge.c:6015`–`:6069`; it does not observe FOV.
   - Pruning preserves added Python keys: `result_prune.py:158`.
   - Acceptance anchor: D1 at `product-spec.md:77`; retain client lifecycle/error behavior under H11/H13.

3. **Spec**

   In the Python `camera_set` tool, await the existing bridge result and add the **top-level** `fov_applied` field only to successful results:

   - positive validated FOV → that value, in radians;
   - zero → JSON `null`.

   Do not alter the request schema, nested legacy `camera.fov`, bridge argument hash, or `camera_get`. Do not cache this as a future `camera_get` measurement. A failed apply, timeout, or illegible camera observation must not report an applied FOV.

   Describe it as the value submitted to the successful setter path, **not a native readback or a guarantee of observed optical projection**.

   Add this contextual description: approximately **68.5° vertical at 1920×1080**, measured in run `8834df0e` on **2026-10-04**. Attribute it to the owner-provided measurement. Zero leaves the engine/current FOV unchanged; especially for the singleton free camera, it does not guarantee resetting to that measured default.

   Update D1 and changelog. No new error codes.

   **Compatibility:** additive Python result field; old clients ignore it. Existing daemon/PBO wire remains unchanged. **No reseal**, PBO rebuild, or persistence change.

4. **Tests**

   In `tools/tests/test_mcp_tools.py`, add:

   - `test_983a_camera_set_echoes_positive_fov`: parameterize orient, lookat/alias, matrix, and free; result equals the validated radians.
   - `test_983a_zero_fov_is_explicit_null`: check key presence and JSON null.
   - `test_983a_failed_camera_set_has_no_applied_claim`: bridge failure preserves its error.
   - `test_983a_description_scopes_measured_default`: includes units, resolution, run, date, and no-readback qualification.

   Positive/null assertions fail on the base because the field is absent. Retain `test_camera_native_crash.py` guards against the native getter.

   **Later in-game check:** set positive and zero FOV, confirm replies and a live render, then restore gameplay. Recalibrating the 68.5° measurement is not required to verify this echo.

5. **Size / risk**

   **S.** Main risk: presenting an echoed setter value or contextual calibration as measured current FOV.

## ITEM d17c-a

1. **Decision restated**

   Before enqueueing a client-peer command, refuse immediately with `client_process_gone` when its exact run’s client is known dead; unknown liveness permits the existing path.

2. **Evidence**

   - Central admission paths: `loopback.py:2377` (`enqueue_command`) and `:2578` (`_enqueue_command`); peer validation at `:2598`, target fence at `:2636`, publication at `:2655`.
   - Exact run/binding routing and existing refusal precedence: `loopback.py:2088` (`_enqueue_run_rejection`), `:2145` (`_enqueue_fence_target`).
   - Existing tri-state client projection: `process_lifecycle.py:626`–`:641`. It relies on a PID census and is insufficient by itself for identity-sensitive admission.
   - Stronger reusable classification: `process_lifecycle.py:4237`–`:4258`, `_classify_registered_process`; native snapshot semantics at `native_process_guard.py:150`–`:159`.
   - Registered identity fields: `process_lifecycle.py:1333`–`:1339`; role records at `:1384`.
   - Remote-code translation: `bridge_errors.py:55`–`:109`, `:274`, `:556`.
   - Both synchronous-call and enqueue/probe consumers: `server.py:736`, `:794`, `:1845`, `:1961`. A daemon-side gate covers all of them.
   - Client command census: `loopback.py:93`–`:123`.
   - Production client call sites in `server.py`: `:2753`; `:4785`; `:5162`, `:5194`, `:5222`, `:5249`; `:5702`, `:5725`, `:5749`, `:5751`; `:5811`, `:5827`, `:5896`; `:6022`, `:6040`; `:6206`, `:6230`, `:6290`, `:6295`, `:6330`, `:6351`; `:6398`, `:6484`; `:6523`, `:6548`, `:6607`, `:6643`, `:6672`; `:6817`, `:6826`, `:6839`.
   - Opposite operations remain the lifecycle’s close/stop/reap paths; this gate must invoke none.
   - Contract anchors: H6/H11/H13 at `product-spec.md:141`, `:146`, `:148`.

3. **Spec**

   Add a read-only lifecycle helper that classifies the client/offline records of an exact registered run using `_classify_registered_process`:

   - any matching live record → `alive`;
   - all relevant records gone or positively identified as foreign/reused → `dead`;
   - otherwise → `unknown`;
   - an exact registered run with no client/offline record has no client process;
   - unavailable lifecycle, unreadable/missing run, or ambiguous target → `unknown`, not a guessed death.

   Gate centrally in daemon admission for `peer=="client"`. Validate arguments, peer, authorization, and run ownership as today. Resolve the same exact destination that enqueue would use. Preserve existing collision, ownership, retired-binding, and unavailable-state errors where there is no eligible exact destination.

   Perform native process probing **outside** `ServerState._lock`; capture the destination identity/epoch, probe, then revalidate it before publication. A changed destination cannot inherit the earlier death verdict. Do not acquire lifecycle operation locks under the loopback lock.

   For a known-dead eligible destination, return HTTP 409:

   - `error: "client_process_gone"`;
   - `run_id`;
   - a bounded hint to inspect `session_status` and use the normal client-reattach path.

   Add the token to `_REMOTE_ERROR_CODES`. Preserve it in `ToolError` translation; do not convert it into `game_not_ready:reason=client_not_polling` or `remote_error`.

   This is process absence/identity loss, not a crash diagnosis. Poll staleness, old screenshots, and `use_state="human"` cannot prove death. Do not modify queues, terminate, reap, relaunch, or change ownership as a side effect.

   Update H6/H13 and changelog; document the common client-peer refusal once. `capture_screenshot` is a host capture tool, not a client-peer enqueue, and retains its own window errors.

   **Compatibility:** new daemons protect old clients too, although an old client may display `remote_error`. New clients against old daemons retain the existing timeout behavior. No wire argument/PBO change, reseal, or manifest migration.

4. **Tests**

   - `test_d17ca_dead_client_refused_before_command_publication`: registered absent client; immediate token; unchanged queue, command ownership, and next command ID.
   - `test_d17ca_all_client_verbs_use_dead_process_gate`: parameterize the command census with valid arguments, including `ui_dialog`’s enqueue/probe path.
   - `test_d17ca_unknown_client_identity_allows_existing_admission`: inaccessible/failed guard observation passes through.
   - `test_d17ca_reused_pid_is_not_a_live_client`: complete mismatching identity does not keep the old client alive.
   - `test_d17ca_binding_change_discards_death_observation`: race destination replacement against the probe.
   - `test_d17ca_public_error_keeps_named_token`: HTTP refusal survives client translation.

   Include alive-client and server-peer controls, ownership/error precedence, and no-process-operation assertions.

   **Later live check:** after owner-authorized client closure, invoke `camera_set` while the registered run still exists; confirm immediate refusal. A live client loading/not polling must retain its prior behavior.

5. **Size / risk**

   **M — lifecycle-critical.** Main risk: attributing a PID or stale binding to the wrong run, or reversing the lifecycle/loopback lock order.

## ITEM d17c-b

1. **Decision restated**

   Add `wait_for(condition="file_matches")` for a file relative to the selected role’s `$profile` directory, renewing the calling session’s lease on every poll.

2. **Evidence**

   - Condition census and validation: `server.py:354`–`:359`, `:2468`–`:2478`.
   - Existing polling implementation: `server.py:2440` (`execute_wait_for`), deadline/lock handling at `:2511`, `:2593`, and sleep outside the lock at `:2695`.
   - Existing log-only discovery: `server.py:2227`–`:2247`; substring matching at `:2672`–`:2683`.
   - Existing caller-visible timeout envelope: `server.py:2303`–`:2329`.
   - Typed tool signature/delegation: `server.py:6891`–`:6918`; description currently says log matches does not renew at `:6887`.
   - Bounded cursor reader: `log_tail.py:134`–`:208`; launch-log helpers at `launch_logs.py:254`.
   - Profile path validation is only syntactic at `log_tail.py:98`–`:118`; it does not secure arbitrary relative files against reparse/ADS escapes.
   - Role-root policy helpers: `dayz_test_tool.py:2603`, `:2624`, `:2668`; actual launch profiles at `dayz_test_worker.py:255`–`:259`, `:282`, `:298`, `:311`.
   - Explicit renewal API: `server.py:1506`; release/status at `:1513`, `:1524`.
   - Sole production `execute_wait_for` caller: `server.py:6907`. Playbooks invoke registered tools rather than a separate implementation.
   - Contract anchors: C4 at `product-spec.md:68`, H2/H3/H7 at `:137`–`:142`.

3. **Spec**

   Extend the condition set, typed enum, and `execute_wait_for` with:

   - `profile_file: str | None = None`;
   - `role: Literal["server","client","offline"] = "server"`.

   For `file_matches`, require a nonempty pattern, relative file path, active lease, and one exact owned/adopted active run. Resolve the role’s profiles directory from verified project/mode policy and the registered run. Do not select the globally newest run, infer a sibling profile, or accept a caller-supplied absolute profile root.

   Accept a bounded relative path under that role’s profile. Reject absolute/drive-relative/UNC paths, traversal, ADS, device paths, and reparse escapes. Verify containment against the actual opened file; lexical `join`/prefix checks are insufficient. A nonexistent file may be awaited, but validate the existing parent chain and recheck the eventual opened file.

   Reuse bounded complete-line, substring, cursor, and scan-report semantics:

   - no regex;
   - `lookback_lines` remains the existing bounded heuristic;
   - `lookback_from="launch"` must be rejected for `file_matches`, because an arbitrary reused profile file lacks a verified launch boundary;
   - a supplied marker must identify only the selected file;
   - return a marker for this file so callers can establish a boundary with `lookback_lines=0`;
   - document that default lookback can match preexisting content. Reliable consumer gates use a boundary marker or a run-specific pattern.

   At each poll—including a poll where the file is missing—heartbeat the exact held lease before reading. Revalidate the lease/run identity; do not acquire or adopt implicitly. Heartbeat failure aborts with the existing named lease error. Never continue a renewing wait as an unowned read.

   Bound heartbeat, path resolution, and file reading by the original deadline. Keep sleeps outside `tool_lock`, offload blocking file work, and leave no detached polling task after cancellation. A poll interval longer than the renewable lease window must not silently promise continuous ownership.

   Results retain `ok`, `satisfied`, `timed_out`, `condition`, `observed`, `probes`, `elapsed_s`, and `scanned`; add role, relative filename, and cursor. File absence/no match times out with `satisfied=false`; unreadability must remain visible in scan diagnostics.

   Keep other wait conditions unchanged except for the separate nonrenewing-read decision. Update C4/H3, tool description, and changelog: `file_matches` is a lease-renewing operation despite reading a file.

   **Compatibility:** optional arguments and enum extension; old callers unaffected. Older servers reject the new condition. No daemon command, PBO change, reseal, or persistent-format change.

4. **Tests**

   - `test_d17cb_profile_file_matches_and_heartbeats_each_poll`: append a complete matching line after two polls; assert role/path and heartbeat count.
   - `test_d17cb_missing_file_still_renews`: create the file later; every preceding poll renews.
   - `test_d17cb_requires_owned_run_and_lease`: reject unowned/no-lease waits before file access.
   - `test_d17cb_rejects_profile_escape`: traversal, absolute/ADS/device paths, junction/symlink escape, and forged marker cannot read outside the profile.
   - `test_d17cb_lease_loss_aborts_without_reacquire`: expire/replace the lease during polling.
   - `test_d17cb_marker_excludes_old_result`: stale matching text cannot satisfy a marker-scoped wait.

   Also cover role isolation, truncation/replacement, partial lines, scan caps, cancellation, deadline exhaustion, and lock availability to a concurrent tool.

   **Later in-game check:** a disposable mission writes a delayed profile-file marker during a wait longer than 120 seconds. Verify fresh match, continuing lease ownership, release, and final `session_status`.

5. **Size / risk**

   **M — lifecycle-critical.** Main risks: arbitrary host-file access, matching a stale mission result, and renewing the wrong lease.

## ITEM 2837

1. **Decision restated**

   Add explicit `project_mod_override` so a caller-declared candidate in `base_mods`/`extra_mods` replaces the implicit project folder in `-mod`, with `project_mod_replaced: true`.

   A Boolean flag cannot identify which dependency is the candidate or prove its PBO prefix. Treat it as the caller’s explicit replacement declaration; do not invent candidate detection.

2. **Evidence**

   - Closed request/version/canonical compatibility: `dayz_test_request.py:14`, `:62`, `:328`, `:342`, `:424`, `:452`.
   - Public request construction: `dayz_test_tool.py:369`–`:462`; bridge default/gate at `:411` and `:501`–`:508`.
   - All production `build_run_request` calls: `dayz_test_tool.py:1931`, `:2144` (replacement-witness recomposition), `:2429` (stop).
   - Public run forwarding: `server.py:3769`–`:3793`.
   - Actual implicit append: `dayz_test_worker.py:239`–`:245`; consumed by `_start_core` at `:260` for server/client/offline argv.
   - Parallel composition used by VPP validation: `native_launcher_transaction.py:250`–`:265`, called at `:549`.
   - Storage composition: `dayz_test_worker.py:343`–`:364`; `dayz_test_storage.py:151`–`:198`.
   - Storage currently requires a nonempty project-role string at `dayz_test_storage.py:182`. Persisted markers store only the seal/project, not those role inputs (`:308`–`:320`).
   - Logical project identity/build target: `dayz_test_worker.py:318`–`:320`, `:742`–`:745`; extension identity check at `dayz_test_tool.py:567`.
   - Parser consumers: `dayz_test_tool.py:462`; `native_launcher_transaction.py:652`, `:694`; `dayz_test_worker.py:664`; `app_main.py:216`.
   - Seals and packaged sources: `native_bundle.py:61`, `:67`; builder packaging at `tools/build_native_launcher.py:56`.
   - Contract anchors: H9/H11/H13 at `product-spec.md:144`, `:146`, `:148`.

3. **Spec**

   Add `project_mod_override: StrictBool = False` to the public tool and corresponding adapter/request-construction parameters.

   **Versioning**

   - Retain existing version-1 parsing and canonical keysets unchanged.
   - Ordinary/default-false MCP calls emit the existing v1 request.
   - Override calls emit request version 2 with a strictly Boolean override field.
   - The new parser accepts explicit closed v1/v2 forms. V1 cannot carry the new key.
   - Preserve reparsing of historical canonical v1 documents, including their implicit source.

   **Composition**

   With override false, retain byte-for-byte existing effective composition and storage fingerprint.

   With override true:

   - require an explicit nonempty caller `base_mods` or `extra_mods` list containing something other than the bridge;
   - omit only the implicit `@<project>` entry;
   - retain validated caller ordering and existing `server_mods` semantics;
   - reject explicitly reintroducing the original project directory through any list, comparing actual normalized runtime paths;
   - reject `build`/`clean`: their current target is the live project folder, not the candidate. This flag changes launch selection, not build/deploy destination.

   Update both `_mods` and `effective_mod_entries`; preflight and VPP must inspect the same effective directories that will reach argv.

   Fix bridge presence/defaulting against the **effective** composition. Overriding the `DayZ_MCP` project no longer makes its excluded original folder satisfy bridge presence. Preserve the existing explicit-extra bridge rule and `extra_mods_defaulted` reporting.

   **Storage and identity**

   Extend `modset_roles`/`modset_seal` to represent the absent implicit project role as `null`, while retaining the candidate paths in their original base/extra roles. This changes only the hash input for overrides; persisted marker/journal schemas remain unchanged.

   Entering or leaving override produces the existing recoverable storage rotation on a new server/offline launch. Preflight and client reattach never rotate. Switching bytes under the same candidate path remains outside the path-based seal’s assurance.

   Keep manifest `mod`, labels, stop-policy lookup, and extension matching tied to the logical project. The candidate folder must not become an unregistered project identity.

   Add `project_mod_replaced: true` to results for a valid override request, including preflight. It describes the effective request composition; it does not prove the candidate initialized. Use c561 when that proof is required.

   New closed request reasons:

   - `project_mod_override_requires_candidate`;
   - `project_mod_override_conflicts_with_build`;
   - `project_mod_override_includes_original`.

   Preserve the established public `bad_dayz_test_request:<reason>` translation.

   Update tool description, H11/H13, and changelog. Do not add automatic PBO-prefix collision detection.

   **Compatibility**

   - Default clients and stored v1 requests retain their existing behavior.
   - Old parsers reject v2 before launch.
   - Requires a **launcher reseal** for changed request/worker/storage/bootstrap sources.
   - Existing daemons can consume the resulting approved argv and opaque storage seal; no new Enforce arguments/PBO required.
   - Rollback to a normal launch sees a seal mismatch and uses existing backup/rotation behavior. Do not reuse candidate-world storage by relabeling its marker.

4. **Tests**

   - `test_2837_override_excludes_original_from_all_role_argv`: candidate appears once in actual generated server/client argv; original is absent.
   - `test_2837_override_vpp_and_worker_lists_agree`: independently expected candidate list matches both consumers.
   - `test_2837_override_changes_storage_seal_and_default_preserves_it`: v1/default seal is unchanged; entering/leaving override differs.
   - `test_2837_dayz_mcp_override_requires_effective_bridge`: excluded original cannot satisfy bridge presence.
   - `test_2837_override_survives_witness_recomposition`: client replacement preserves the override through the second request build.
   - `test_2837_old_canonical_v1_still_reparses`: historical keysets remain valid.
   - `test_2837_candidate_build_conflict_is_rejected_before_side_effects`.

   Add negative tests for invalid Boolean values, absent/bridge-only candidate declaration, original-folder aliases, invalid paths, and unchanged stop/reattach logical identity.

   **Later in-game check:** verify candidate/live hashes, launch with override, inspect server and client RPT `-mod`, confirm only the candidate project artifact is selected, and check recoverable storage rotation/reporting when toggling the flag.

5. **Size / risk**

   **M — persistence/lifecycle-critical.** Main risk: argv, bridge/VPP validation, and storage seal disagreeing about the loaded mod set.

## BATCHES

Use qualified nested tool functions as ownership units. No function is split between these batches; coordinate shared-file edits through the integrator.

| Order | Items | Function ownership | Reseal | Later live check |
|---|---|---|---|---|
| 1 | **c561, 31d2, 2837** | Launch/request/policy/terminal functions; `build_app.dayz_test_run`; worker execution/composition; VPP effective list; storage-seal input helpers | **Yes, once after integration** | Attestation positive/negative; busy preflight; candidate argv and storage rotation |
| 2 | **584e+f0e3, d17c-b** | `SessionCoordinator.authorize`; TTL decorator; supervisor TTL integration; `_lease_renewal_contract`; `execute_wait_for`; `build_app.wait_for`; factory hook-install statements | No additional reseal | Read expiry, long file wait, supervisor result |
| 3 | **d17c-a** | Loopback enqueue/fence integration, new exact-run liveness helper, remote-error translation | No | Known-dead refusal and live/loading control |
| 4 | **983a** | `build_app.camera_set` and its description | No | Echo and live-render smoke |

Batch 1 keeps its three items together because they share `execute_dayz_test_run`, worker execution, policy/request parsing, and effective-mod composition. Batch 2 keeps TTL and file waiting together because their renewal descriptions and tests must agree.

If the scheduler treats the entire enclosing `build_app` as one function rather than its nested tools, **combine batches 1, 2, and 4** for that file. Do not run concurrent writers over the factory.

## CONFLICTS WITH IN-FLIGHT WORK

| In-flight change | Overlap and integration requirement |
|---|---|
| **734c — `mcp_supervisor`** | Direct overlap with universal TTL reporting for `server_reload` and supervisor-generated errors. Let its owner integrate the TTL contract into the revised request/generation handling. Do not preserve the current `"answered"` receipt as proof of successful renewal. |
| **c8c7 — storage rotation/run result** | Direct shared launch/result surfaces with batch 1. Preserve its run-record fields and rotation notice when projecting attestation, preflight, and replacement results. Override must use its existing recoverable rotation/reporting path. |
| **dbe0 — `pack_only` traversal** | Shared worker/build surfaces and launcher reseal. Attestation runs after the completed build; preflight does not perform asset traversal. Preserve the new traversal implementation. |
| **6d21+fde3+7f27 — action mode/pruning ownership** | d17c-a covers those client commands centrally. Do not alter their arguments or dispatch. TTL/FOV are Python additions after bridge pruning; do not extend generic scalar ownership for them. |
| **aa01 — inventory attachment item ID** | No direct function overlap. Universal TTL decoration must preserve its new result fields. |
| **bb6d — test** | No production change required here. Preserve its independent test expectation; do not reuse that regression as proof of any new feature. |
| **Docs — capture/teleport/key/spawn descriptions** | Avoid rewriting those descriptions. Universal lease behavior belongs in the shared renewal contract. Client-process refusal applies centrally to `key_press`; screenshot window errors remain separate. |

Reseal from the **combined reviewed source set**, including in-flight sealed-module changes. A reseal from an earlier isolated batch would reinstate stale source pins.

## NOT VERIFIED

- No files were modified; no tests were added or executed; no DayZ process, daemon, or MCP tool was contacted.
- Named regression tests are proposed tests with source-established baseline failure expectations, not measured red/green results.
- Live launcher registration, installed source pins, effective policy, deployed PBOs, profiles, and process topology were not inspected. The injected October 1 live state does not establish this export’s runtime state.
- The cited SimpleGroup artifacts/logs, Baltic FOV calibration, and process-disappearance observations were not independently reproduced.
- Actual in-flight diffs were unavailable; conflict analysis uses the supplied work list and current source anchors.
- New policy/terminal compatibility, Windows file-containment defenses, race behavior, and combined reseal must be demonstrated by implementation tests.
- Prior memory was used only as a caution about false PASS claims; implementation anchors above were reopened in this export.

