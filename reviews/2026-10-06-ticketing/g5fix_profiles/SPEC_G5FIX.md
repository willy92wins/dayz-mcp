# Named-instance profile folders (follow-up to g5 #216; inbox fb-20261007-135819-39b8)

## ITEM g5fix

### Owner decision (2026-10-07, binding)
Merge g5 (#216, done) and fix the named-instance profile folders in this follow-up batch, with its own in-game
check afterwards (run by the orchestrator, not by you).

### Implementation specification (gpt-6.1-sol, binding)
**All changes below are [DESIGN], binding requirements for the follow-up batch. Source anchors above describe existing behavior.**

### 1. Decision and scope

Create `profiles-<token>` **during admitted launch preparation**, immediately before bridge-config seeding in `ServerState.prepare`.

Do not make installer execution or manual folder creation prerequisites. Do not recursively provision project roots.

Scope: named profile selection, provisioning, affected readers, regression tests, packaging seals and documentation. No bridge protocol, mission-storage format, lifecycle ownership or shutdown-algorithm redesign.

### 2. One explicit profile-name contract

Add a small pure helper in the already packaged `tools/dayz_mcp/server_cli.py`:

- Omitted token returns exactly `profiles`.
- A valid token returns exactly `profiles-<token>`.
- Reuse the existing token validator at `server_cli.py:33`; malformed tokens fail.
- The helper accepts its token explicitly.

Use it in `dayz_test_worker._start_core`, attestation path construction, launch artifact/client-root construction, VPP preflight and preparation’s expected-name check.

**Token authority:**

- Sealed worker and VPP preflight: canonical request `instance_token`.
- Daemon-side preparation: its bound instance token.
- Daemon readers: bound context, with the recorded run folder validated against it.
- Never infer the selection from an inherited environment variable, loose prefix matching, newest directory or existing config contents.

The worker already receives the token through the accredited request (`dayz_test_tool.py:482`).

### 3. Provisioning — DATA-CRITICAL

In `tools/dayz_mcp/loopback.py`, `ServerState.prepare`:

1. Preserve absolute-path and owner-name rejection.
2. For a named instance, require the appropriate existing role parent: `_server` for server; `_client` for client/offline. Require its existing project root.
3. Validate the expected leaf and usable seeding credentials before creating anything.
4. If absent, create **only that leaf**, without creating ancestors.
5. An existing ordinary directory is reusable. A file, invalid path or redirection escaping the intended role root fails closed.
6. A concurrent creation is accepted only after rechecking that the result is the expected directory.
7. Continue through existing atomic config seeding, endpoint/key validation, launch-UUID write and reread.
8. Filesystem/provisioning failures return the existing typed `instance_config_missing`; preserve `instance_profile_owner_mismatch` and `instance_endpoint_mismatch`.
9. Do not spawn after preparation failure.

Preserve `_seed_bridge_config`’s requirement that its parent already exists. The preceding named preparation step supplies that parent.

**No migration or copying:** do not copy legacy keys, VPP permissions, logs, dumps or player-profile files into named folders. Do not overwrite an existing foreign endpoint/key. Do not remove an existing profile directory during cleanup.

A misspelled nonexistent `dev_root`, or a project lacking its role root, must fail without creating directories or receiving credentials. The selected sealed project remains the upstream project authority; directory existence alone does not establish project approval.

### 4. Recorded-run resolution — DATA-CRITICAL

Update these `dayz_test_tool.py` functions together:

- `_artifact_paths`
- `_client_profile_roots`
- `_stop_artifacts`
- `_artifact_candidates`
- `_profiles_match_policy`
- `_close_role_folder`
- Their launch, replacement, stop and close callers.

Separate two cases:

- **Before a run exists:** construct paths from sealed project/mode policy and the explicit token.
- **For an existing run:** validate its complete recorded `profiles` against the approved project and selected instance, then retain that leaf for all role paths.

A shared run-aware resolver may be introduced locally in `dayz_test_tool.py`. It must reject wrong project roots, wrong tokens, legacy anchors in a named context, relative paths and traversal. Validate the anchor **before** branching on the requested role.

Retain mode authority for role roots (`dayz_test_modes.py:102`) and `_start_role_roots`; retain existing stop artifact expansion when the server anchor remains after the client has retired.

### 5. Repair the readers

**`log_tail.py` and `launch_logs.py`:**

- Extend profile admission to the exact selected leaf.
- Preserve existing structural checks.
- Derive siblings with the validated recorded leaf.
- Do not search multiple tokens or fall back to legacy profiles.

**`server.py`:**

- Apply that resolution to `logs_since`, `_wait_for_script_log_paths` and `_profiles_dir_for_role`.
- Preserve cursor encoding, scan limits, launch-time filtering, deadlines, file containment and lease behavior.
- Preserve `log_matches`’ aggregate behavior. `role` continues selecting only `file_matches`.
- Preserve the existing client/offline eligibility rules.

The shared log resolver also feeds capture-window disambiguation (`server.py:7504`); verify that named client paths reach that consumer.

**Close:**

- Use the validated run-derived folders to initialize RPT watches.
- Keep termination freshness, rotation handling, retirement requirements and client-then-server order unchanged.
- Ensure logout boundary capture and subsequent reads use the named server directory.
- Do not obtain a green close result by weakening missing-RPT, rotation or retirement conditions.

**Attestation:**

- Extend `profile_directory` and `initialization_rows` to receive the explicit token.
- Thread the same payload token through `_capture_role_boundary` and every worker call to `initialization_rows`.
- Preserve missing-directory boundaries as empty and unreadable evidence as unverifiable.
- Preserve post-boundary complete-line requirements.

**VPP:**

- Update `vpp_preflight_paths` using the canonical request token.
- Keep `serverDZ.cfg` unchanged.
- Retain read-only preflight; create no administrative files.

**Client diagnosis:**

- Supply the correct named roots to stalled-start checks and dump baselines.
- Preserve the existing baseline/ceiling readers; they already follow their supplied roots correctly.

### 6. Compatibility and deliverables

**Default instance:** profile names, argv path strings, artifact ordering and existing profile contents must stay byte-identical. Preserve the existing per-launch UUID rewrite and serialization behavior. Missing default `profiles` folders must continue failing as before; this batch does not automatically provision them.

**Launcher reseal: required.** The planned changes touch sealed `server_cli.py`, `dayz_test_worker.py` and `dayz_test_attestation.py`, listed in `tools/packaged-modules.lock.json:7`, `:12` and `:20`. Refresh their reviewed hashes and rebuild/reseal the launcher and associated bundle through the existing packaging workflow. Verify import closure and builder/verifier module-set parity. The proposed helper needs no new packaged module.

**PBO: no change required.** No Enforce or bridge-protocol change is specified.

**README: required.** Correct `README.md:247` to distinguish:

- Existing project/role roots.
- Legacy default profile prerequisites.
- Automatic named-leaf provisioning.
- Rejection of existing endpoint/key conflicts.
- Operator provisioning of named VPP administrative state when needed.

Document named artifact locations and the existing `log_matches`/`file_matches` role distinction in `tools/README-mcp.md`.

**Persistence:** retain the run-manifest and launch-intent formats. No profile migration, mission-storage rotation change or automatic rollback deletion.

### 7. Regression acceptance

Use disposable fixtures, mocked lifecycle/process operations and independently specified expected paths. Comparing two consumers of the same new helper is insufficient.

| Test group | Required assertion |
|---|---|
| Fresh named preparation | Existing project/role roots, absent named leaf: preparation creates that leaf, seeds the correct endpoint/key and completes before mocked spawn. Fails on the base. |
| Fail-closed preparation | Missing project/role root, file at leaf, wrong leaf/token, escaping redirection, denied creation and existing foreign endpoint: reject without credential leakage or ancestor creation. |
| Mode/path parity | Token `130`: server, all, client extension and offline deliver the expected `-profiles` values to the launch consumer, record the corresponding anchor and report matching artifacts. |
| Default compatibility | Fixed inputs produce unchanged default argv/path strings, ordering and config bytes apart from the established UUID mutation. Default missing-folder behavior remains unchanged. |
| Log admission/siblings | Named server/client RPT and script logs are read; legacy and other-token sentinel lines are excluded. Test both anchor directions. |
| `logs_since` / `log_matches` | Exercise cursors, launch scan and lookback. Fresh named evidence works; foreign/legacy evidence cannot satisfy. `log_matches` retains aggregate behavior for each supplied `role`. |
| `file_matches` | Test server, client and offline, including server-anchored client reads. Named evidence satisfies; matching legacy or other-token evidence does not. Invalid anchors open no file. |
| Close / logout — DATA-CRITICAL | Named connection evidence creates the expected player wait. A linked fresh logout completes it; absence reaches the existing warning/deadline behavior. Fresh termination lines plus confirmed reaping permit valid graceful metrics. |
| RPT rotation | Named role rotation is detected and invalidates exit metrics; legacy/other-token rotation is ignored. Old termination lines remain insufficient. |
| Stop artifacts | Named runs resolve successfully; server anchors still include client artifacts after client retirement. |
| Client diagnosis | Named header-only RPTs and bounded fresh dumps are considered; legacy/server/other-token dumps and evidence beyond the next-launch ceiling are excluded. |
| Attestation | Cover server, client and offline requirements, both script and profile-file sources. Fresh named evidence passes; legacy post-boundary matches and named pre-boundary matches do not. |
| VPP | Named-only administrative files remove the appropriate absence warnings; legacy-only files do not. Server config location stays unchanged. |
| Existing correct consumers | Named launch-intent recovery uses its recorded config; foreign labelling returns `profiles-130` without an absolute path. |
| Packaging | Reviewed lock hashes, packaged import closure, module-set parity and resealed-bundle verification pass. |

Extend the existing worker, instance-identity, log-tail, wait, orderly-close, VPP and client-dump test suites. Add assertions at the public result boundary for `artifacts_paths`, close metrics and attestation reports.

The batch passes only when the named positive cases and isolation negatives pass, default compatibility remains intact, and the resealed launcher is verified. A subsequent owner-authorized in-game run must independently confirm both role paths, logout observation and fresh termination evidence.

### The reviewer's findings and classification, which the implementation must close (source anchors are on
### origin/main bbda2e9; this workspace is main 17145fb, which only adds batch E in loopback.py/server.py/bridge)
The failure scenarios below are regression recipes. No DayZ, daemon, or MCP tool was started during this review.

### F1 — P1: a fresh named instance cannot provision its launch profiles

**Evidence:** `tools/dayz_mcp/dayz_test_worker.py:454` selects `profiles-<token>` for server, client and offline launches. `tools/dayz_mcp/loopback.py:1799` requires that exact basename, while `_seed_bridge_config` at `:1770` refuses a missing directory. `process_lifecycle.py:2259` forwards preparation failures; `:2262` also maps unexpected exceptions to `instance_config_missing`.

**Failure scenario:** provide an otherwise valid project with existing `_server` and `_client` roots but no `profiles-130`; call `dayz_test_run(project="DayZ_MCP", mode="all")` through instance `130`. Preparation fails before spawn. Creating the two empty leaves allows preparation to proceed.

The copied evidence agrees:

- `_g5fix_evidence/g5.20-130-launch.txt:13`: `instance_config_missing`.
- `_g5fix_evidence/g5.21-130-launch-retry.txt:3`: launch succeeds.
- `_g5fix_evidence/runs-130-after-close.json:1`: the successful run records `_server\profiles-130`.

### F2 — P1, DATA-CRITICAL: close cannot observe named RPTs and skips the logout wait

**Evidence:** `tools/dayz_mcp/dayz_test_tool.py:3284` validates the recorded folder against legacy artifact candidates; `:3307` independently constructs a legacy role folder. At `:4129`, a named run fails validation, so no role watches are created. Consequently `:4158` supplies no server RPT, and `_wait_for_connected_logouts` at `:3834` returns `([], 0.0, False)` immediately.

**Failure scenario:** close a named all-mode run whose named server logs contain an active player connection. Even if both named RPTs append termination lines, the tool reports `role_without_rpt`; it also closes the server without observing the player’s linked logout completion.

This bypasses an existing persistence safeguard. **Actual data loss was not demonstrated.**

The observed result is `_g5fix_evidence/g5.40-130-close.txt:2`. The server termination line is present at `_g5fix_evidence/profile_dirs.txt:21`.

RPT rotation detection is likewise bypassed because it requires an initialized watch. The rotation algorithm itself follows `watch.path.parent` correctly at `dayz_test_tool.py:4226`.

### F3 — P1: `file_matches` can read the legacy client profile of a named run

**Evidence:** `tools/dayz_mcp/server.py:2741` obtains the folder through the legacy `_close_role_folder`. The recorded-folder check at `:2750` compares paths only when their role parents match.

**Failure scenario:** use a named all-mode run recorded under `_server\profiles-130`. Put a matching line only in `_client\profiles\probe.txt`; leave `_client\profiles-130\probe.txt` unmatched. Call `wait_for(condition="file_matches", role="client", profile_file="probe.txt", pattern="READY")`. Resolution selects the legacy client folder, allowing wrong-instance evidence to satisfy the wait.

For that server-anchored run:

- `role="server"` fails with `profile_unresolved`.
- `role="client"` resolves to legacy `_client\profiles`.
- An offline run anchored under `_client\profiles-130`, queried with `role="offline"`, fails with `profile_unresolved`.

Isolated execution of the source resolver reproduced all three outcomes.

### F4 — P2: `logs_since` and `log_matches` reject named profiles; sibling discovery retains the wrong leaf

**Evidence:** `tools/dayz_mcp/log_tail.py:204` admits only `profiles`. `server.py:5409` therefore rejects a named `logs_since` request with `bad_profiles`. `_wait_for_script_log_paths` at `server.py:2408` obtains no allowed folders and reports `no_active_run`.

Separately, `tools/dayz_mcp/launch_logs.py:29` and `:31` construct the opposite role using literal `profiles`.

**Failure scenario:** write fresh server and client log lines under `profiles-130`, then call `logs_since(run_id=R)` or `wait_for(condition="log_matches", pattern="READY")`. Neither reads the named logs. Fixing only the allowlist would then allow sibling discovery to add the **legacy** opposite role and miss the named sibling.

**Contract clarification:** `log_matches` scans both role folders. Its `role` argument is not used for selection (`server.py:3487`, `:3614`). `role` selects a folder for `file_matches` (`server.py:3382`). This batch should preserve that distinction.

### F5 — P2: initialization attestation reads and measures boundaries in legacy profiles

**Evidence:** `tools/dayz_mcp/dayz_test_attestation.py:313` builds the legacy folder. The worker uses it for pre-launch boundaries at `dayz_test_worker.py:604`; `initialization_rows` uses it again at `dayz_test_attestation.py:477`.

**Failure scenario:** enable a client initialization requirement. A fresh matching line written only in `_client\profiles-130` fails attestation. Conversely, an independently appended matching line in legacy `_client\profiles`, after its captured boundary, can satisfy the named launch despite no matching named-instance initialization evidence.

Both boundary capture and subsequent reads must change together.

### F6 — P2: reported artifacts and client diagnosis point to legacy folders

**Evidence:** `tools/dayz_mcp/dayz_test_tool.py:800` reports legacy artifact paths; `:816` selects legacy client roots for stalled-start analysis and dump baselines. Consumers include `:2716`, `:2781` and the baseline capture at `:1774`. `_stop_artifacts` at `:1638` rejects a named recorded folder against those legacy candidates.

**Failure scenario:** launch instance `130`; artifacts are reported under `profiles` although the process writes under `profiles-130`. A header-only client RPT or new `ErrorMessage_*.mdmp` in the named client directory is missed. A normal `dayz_test_stop` reaching `_stop_artifacts` rejects the named anchor with `lifecycle_status_invalid`.

The dump readers themselves correctly follow supplied baseline roots. Missing directories already produce an empty baseline, whereas unreadable directories remain unknown (`client_steam_bootstrap.py:59`). No dump-parser redesign is needed.

### F7 — P2: VPP preflight examines another profile’s administrative state

**Evidence:** `tools/dayz_mcp/native_launcher_transaction.py:158` defines the legacy leaf, used by `vpp_preflight_paths` at `:291`. Superadmin and credential reads at `:593` and `:598` consequently target legacy profiles.

**Failure scenario:** request VPPAdminTools on instance `130`, with administrative files present only under `_server\profiles-130`. Preflight incorrectly warns that they are absent. Files present only in legacy profiles suppress those warnings even though the launched server does not use them.

`serverDZ.cfg` correctly remains directly under `_server`; only profile-dependent VPP paths change.

### Classification of every cited location

All paths below are relative to `tools/dayz_mcp/`.

| Location | Classification and required treatment |
|---|---|
| `dayz_test_attestation.py:313` | **Change:** instance-aware name from the accredited request token; use it for both boundaries and initialization reads. |
| `dayz_test_tool.py:800` | **Change:** instance-aware name for planned launch artifacts. For existing runs, validate and follow their recorded anchor. |
| `dayz_test_tool.py:816` | **Change:** named client/offline roots for client diagnosis and dump baselines. Existing-run decisions must agree with the validated recorded anchor. |
| `dayz_test_tool.py:3307` | **Change:** resolve each role from the validated run anchor and sealed role-root policy, retaining its profile leaf. |
| `launch_logs.py:29`, `:31` | **Change:** preserve the recorded leaf when deriving the opposite role; never substitute legacy `profiles`. |
| `log_tail.py:204` | **Change:** recognize the exact selected instance’s leaf, retaining absolute-path, traversal and role-parent checks. |
| `native_launcher_transaction.py:158` | **Change:** VPP profile paths must use the canonical request’s instance-aware leaf. |
| `process_lifecycle.py:342` | **Correct:** public display formatting, not directory resolution. Legacy `profiles` displays its parent; named profiles display `profiles-130`. Retain this redaction behavior. |
| `dayz_test_tool.py:1626` | **Correct source, defective validation:** keep `run["profiles"]`; replace legacy candidates and preserve server-anchor artifact expansion after client retirement. |
| `dayz_test_tool.py:3281` | **Correct source, defective candidates:** validate the full recorded path against the approved project and selected instance. |
| `launch_logs.py:137` | **Correct source:** recorded folders are authoritative inputs; repair the allowlist and sibling derivation around them. |
| `server.py:2746` | **Insufficient check:** validate the recorded anchor independently of the requested role, then derive the requested role. The same-parent-only comparison permits F3. |
| `server.py:5404` | **Correct source:** retain recorded candidates; repair admission and sibling discovery. |
| `process_lifecycle.py:4423–4444` | **Correct:** records the parsed launch profile after checking equality with `-profiles`. This is the launch request, not a reconstructed legacy path. |
| `process_lifecycle.py:4500–4542` | **Correct:** validates the persisted intent and reads `dayz_mcp.json` from its recorded profile. Preserve recovery behavior. |
| `process_lifecycle.py:7881` | **Correct:** labels a foreign process from its actual argv through the redacting formatter. Do not impose the local instance’s name on foreign processes. |

Per-role reader coverage:

| Consumer | Required named-run behavior |
|---|---|
| `logs_since` | Recorded anchor plus opposite role with the same leaf. Preserve launch filtering and cursors. |
| `log_matches` | Same named server/client log set, including launch, lookback and marker modes. Preserve its existing aggregate behavior. |
| `file_matches`, server/client/offline | Validate the complete anchor first; resolve the approved requested role. Offline uses the client root and existing role-eligibility rules. |
| Close RPTs and logout reads | Initialize every launched role’s watch in its named folder; obtain connection/logout evidence from the named server directory. |
| RPT rotation | Continue monitoring the initialized watch’s directory; rotation in legacy or another instance must have no effect. |
| Client stalled-start and death diagnosis | Supply the named client root; preserve baseline, next-launch ceiling and unknown-evidence rules. |
| Foreign-process labelling | Continue using actual foreign argv and publishing only the existing basename label. |
