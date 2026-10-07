## ITEM 97de+0cb3

### Owner decisions (2026-10-06, binding)
1. Each instance runs from its own tools tree (its own copy of the code and venv), as the 1.30 copy at
   C:\temp\DayZ_MCP_130 does today. Shared-tree support is out of scope.
2. Both daemon installations (the default one and the 1.30 one) will be upgraded to this code before
   the concurrent-launch acceptance.
3. The default instance stays the one for the stable (retail) DayZ from now on, and token `130` the one
   for DayZ 1.30 Experimental, keeping its existing store `%LOCALAPPDATA%\DayZ_MCP_130` and receipt.
   Inbox history stays on the default instance. Renaming an instance is out of scope.

### Implementation specification (gpt-6.1-sol, after reviewing the Grok design; binding)
The following is **[DESIGN]**, grounded in the source anchors above. It is a corrected implementation specification, not an applied change.

1. **Define one validated selector and preserve default compatibility.**
   - Add optional instance/game-path fields to `ServerConfig`, installer options, and the relevant CLI entry points.
   - Retain the proposed token grammar and reserve `default`.
   - Reject duplicate, malformed, and glued instance flags consistently at startup and during scanning.
   - Omission remains the default instance. Preserve existing paths, registration key, keyfile default, daemon argv ordering, receipt schemas/content, and sealed default executable.
   - Resolve instance context once. An inherited named environment value must not make an omitted-token server write some files to a named root and others to the default root.
   - Prefer omitting registration `env`; publish validated context inside clients/daemons for ordinary child processes.

2. **Complete spawn and authority propagation.**
   - Update `server_cli.py`, `server.py`, `daemon_contract.py`, `daemon.py`, `host_config.py`, both normal-policy implementations, `secure_launcher.py`, `doctor.py`, and `stdio_bridge.py`.
   - Append named flags after the **entire existing argv**, preserving optional exec flags.
   - Build the WMI command from accredited provenance. Carry both token and explicit game path.
   - Bind registration name to argv token. Compare both hosts’ instance, game path, port and keyfile.
   - Capture that same selector in every policy revalidation closure.
   - Preserve accreditation before sending credentials or HTTP bytes.

3. **Enforce one writer per root. [DATA-CRITICAL]**
   - Extend root exclusion to the writer’s lifetime, covering daemon and other runtime-state activation paths.
   - Reject a second writer using the same token with a different port, key, or installation.
   - Keep existing paths and data formats; do not migrate the default store.

4. **Correct migration classification. [DATA-CRITICAL]**
   - Preserve current valid-client exclusion.
   - Parse the actual Python target and server/bootstrap invocation before classifying instance.
   - Supported same-root writers block; positively accredited different-root writers do not.
   - Unknown legacy, malformed, or unsupported writers block unsettled migrations.
   - Preserve native identity, PID-reuse and launch-ancestor checks. Reconcile `allowed_seen` with any changed classification.
   - Apply the selector to **every** quiescence check, including recovery and post-copy checks.
   - Preserve the validated settled-receipt shortcut.
   - Add explicit instance selection to `p0s_gate.py`; reject conflicting environment/port configuration before writes.
   - Retain `130`’s existing receipt only if it validates unchanged at the same absolute paths. Do not delete artifacts to force startup.

5. **Separate instance resources; retain genuinely shared recovery authority.**
   - Update runtime paths, inbox, knowledge, frame state, named capture outputs and named exec audit defaults.
   - Preserve each helper’s existing default fallback behaviour.
   - Keep host-config recovery shared because it recovers shared files.
   - Parameterize installer probes, parsers, commands, timeout writers and rollback targets by registration name. Named operations must never remove `dayz-mcp`.
   - Review both installers’ exact not-found handling when introducing dynamic names.
   - Namespace named `_mcp_config` templates.
   - Require separate profile locations and validate bridge URL/key before launch.
   - Give global skills one explicit ownership policy; separate pack directories alone do not provide version-isolated global skills.

6. **Serialize physical-box admission. [DATA-CRITICAL]**
   - Add a shared interlock for supported instances.
   - Under it, recheck occupancy and identities before profile preparation, storage rotation, Steam mutation and spawn.
   - Retain exclusion until the launched process is verifiably observable, or cleanup/recovery has established the outcome.
   - A daemon death must release the OS lock; unresolved launches remain protected through existing intent recovery and foreign detection.
   - Do not merge instance-local session leases or authorize cross-instance stopping.
   - Serialize shared build targets/temp paths independently: builds occur before lifecycle launch admission.

7. **Deploy with aligned sealed policy.**
   - Rebuild/reseal the named launcher against its selected game executable, directories and packaged behaviour; update approved-launcher evidence.
   - If adding packaged modules, update builder/runtime closure inventories without silently invalidating the existing default bundle.
   - Prove the unchanged default executable remains accepted and operational.
   - **No PBO rebuild is required** for this corrected scope. The existing addon reads its endpoint from configuration. Verify deployed bridge compatibility during acceptance.

Required regressions:

| Gate | Failure before / required result after |
|---|---|
| Default compatibility | Golden default argv across optional exec/version settings; unchanged default registration payload, paths and receipt acceptance. |
| Fresh lazy startup | Live same-instance client does not block its daemon’s first migration. |
| Client/ancestor topology | A’s stdio children do not block B; allowed identities still detect drift and PID reuse. |
| Legacy/spoofed invocation | Patched no-flag writer under another directory/port, unsupported bootstrap and invalid tails cannot bypass migration. |
| WMI propagation | Captured WMI command contains token/game path; missing client environment changes neither. |
| Provenance | Named/default siblings, mismatched token/game path, environment conflict and revalidation after config replacement. |
| Root writer exclusion | Two installations, same token, different ports: only one activates stores; loser changes no manifest. |
| Migration recovery | Existing/absent source; interruption at every publication boundary; foreign-root bytes unchanged; orphan backup remains fail-closed. |
| Shared host recovery | Interrupted A transaction followed by B cannot bypass recovery or overwrite sibling registration changes. |
| Box admission | Barrier-controlled concurrent launch: only one reaches destructive preparation/spawn, including pre-bind and Steam windows. |
| Profile routing | Existing A config cannot be reused by B; matching named config reaches B’s bridge. |
| Capture isolation | Fixed-clock simultaneous saves return distinct named/default paths. |
| Sealed deployment | Named Experimental policy and unchanged default bundle independently pass authority/closure checks. |

Live acceptance must include the simultaneous-launch negative case, not just launching against an already occupied box. Run it later under the session protocol, with lifecycle-guard cleanup and final status from both instances.

### The reviewer's findings on the first design, which the implementation must not reintroduce
**F01 — P1 — The proposed classifier reintroduces a first-start deadlock.**  
Anchors: `DESIGN.md:130`, `DESIGN.md:153`; [identity_migration.py:469](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/identity_migration.py:469), [server.py:1613](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/server.py:1613).

The current scanner excludes a **valid client-mode invocation**: `_dayz_mcp_tail_is_writer` returns false for `mode == "client"`. I executed the current parser/classifier in memory: a valid unflagged client returned `False`, while a daemon returned `True`. The design’s description of every client being a global writer is therefore stale against this snapshot.

**Failure scenario:** register a fresh named instance with `--client --instance 130`; its client lazily spawns the daemon through WMI and remains alive awaiting startup. Under the proposed “every same-root PID” rule, that client blocks the daemon’s unsettled migration. It is neither the migrating process nor its accredited venv launch ancestor.

**Correction:** classify process role before instance. Preserve exclusion of valid supported clients, including supervised clients and their workers. Malformed or unrecognised invocations remain conservative blockers.

---

**F02 — P1 — A startup election does not enforce one writer per state root. [DATA-CRITICAL]**  
Anchors: `DESIGN.md:185`; [daemon.py:1528](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/daemon.py:1528), [daemon.py:1626](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/daemon.py:1626).

The root election ends after startup, before the daemon’s serving lifetime. Healthy-daemon discovery examines the requested port. The installer rejects port 8765 for named instances but does not establish an immutable token-to-port binding across installations.

**Failure scenario:** with a settled `130` receipt, start one daemon using `--instance 130 --port 8775`, then another installation using `--instance 130 --port 8785`. The second port has no healthy listener, the root startup lock is available, and the receipt skips migration scanning. Both daemons can activate independent in-memory coordination over the same `runs.json` and `coordination.json`. One daemon’s persistence can overwrite the other’s state.

**Correction:** enforce lifetime exclusion for each state root, irrespective of port, keyfile, or code tree. Cover every mode that activates runtime-state writers. The loser must fail without activating coordination or altering manifests.

---

**F03 — P1 — The legacy exception does not prove migration quiescence. [DATA-CRITICAL]**  
Anchors: `DESIGN.md:124`, `DESIGN.md:146`, `DESIGN.md:181`; [identity_migration.py:765](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/identity_migration.py:765); `_instance_evidence/dayz-mcp-130.patch`.

An omitted flag cannot establish default-root ownership for an unknown older or patched installation. Port and directory-name matches provide additional reasons to **block**, not evidence that every other unflagged process is safe to ignore.

**Failure scenario:** place the supplied patched source in `C:\fixture\exp-copy`, preserving its hardcoded `DayZ_MCP_130` root, and run its daemon on 8785. Start the supported `130` migration on 8775. Its argv contains neither `DayZ_MCP_130` nor port 8775, so the proposed exception permits a live writer to the migrated store.

A token in argv likewise proves only what the invocation claims. Unknown code must not gain accreditation merely by appending another instance’s flag.

**Correction:** unknown legacy writers remain blockers for unsettled migrations. Exemption requires positive evidence that the installation implements the supported root-selection contract. Explicitly distinguish supported code from the patched legacy tree. Preserve native identity validation for allowed PIDs; do not reduce it to token equality.

---

**F04 — P1 — Registration-name changes do not complete the provenance chain.**  
Anchors: `DESIGN.md:82`, `DESIGN.md:110`, `DESIGN.md:220`; [host_config.py:71](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/host_config.py:71), [host_config.py:233](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/host_config.py:233), [host_config.py:390](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/host_config.py:390), [normal_daemon_policy.py:158](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/normal_daemon_policy.py:158).

Missing changes include:

- Strict registration option inventories.
- `_ClientRegistration` fields and equality.
- Reconstruction of the daemon config from registrations.
- Both normal-policy loaders and their revalidation closures.
- Binding the selected registration name to its argv token.
- Client comparison of requested instance/game path with accredited provenance.

The optional registration environment also conflicts with the existing schema: nonempty Claude `env` is rejected. I reproduced `daemon_provenance_conflict` for both the new flag and the proposed environment entry.

**Failure scenario:** implement the listed name lookups and CLI flag, then register `dayz-mcp-130`. Strict option scanning rejects `--instance` before daemon discovery. Updating only the initial lookup leaves policy revalidation resolving `dayz-mcp`.

**Correction:** thread the selector through the entire authority chain. Matching Claude/Codex entries must also match the requested instance, canonical keyfile, port, and game path.

---

**F05 — P1 — Foreign-process detection is not an atomic physical-box interlock. [DATA-CRITICAL]**  
Anchors: `DESIGN.md:159–167`; [process_lifecycle.py:1857](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/process_lifecycle.py:1857), [process_lifecycle.py:3610](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/process_lifecycle.py:3610), [process_lifecycle.py:3645](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/process_lifecycle.py:3645), [steam_prepare_supervisor.py:131](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/steam_prepare_supervisor.py:131).

The lifecycle and Steam locks are process-local. The source expressly acknowledges the window between its final port observation and DayZ binding.

**Failure scenario:** A and B each obtain their instance-local lease. Synchronise their final probes while the box is empty, then release both. Both can prepare profiles, rotate shared mission storage, and spawn before either game becomes an observable foreign holder. Different UDP ports do not remove this race.

Steam remediation is on the launch path and has a mutation callback; I found that protection. It still cannot serialize two separate daemons admitting work against an empty box.

**Correction:** retain independent leases, but add a shared admission interlock covering supported launchers’ box checks and destructive preparation through verifiable process publication. Serialize shared Steam mutations too. This need not be a machine-wide session lease or a shared crash-recovery store.

The observed refusal against an **already running** foreign game proves only that sequential case.

---

**F06 — P1 — Existing profile configuration can route the new game to the wrong daemon.**  
Anchors: `DESIGN.md:165`; [loopback.py:1668](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/loopback.py:1668), [loopback.py:1708](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/loopback.py:1708), [dayz_test_worker.py:256](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/dayz_test_worker.py:256).

Profiles derive from the project’s sealed `dev_root`, not the daemon state root. `prepare` seeds URL/key only when the file is absent; an existing file receives a new launch UUID while retaining its old endpoint and key.

**Failure scenario:** A leaves `<dev_root>\_server\profiles\dayz_mcp.json` pointing to 8765. Stop A’s game and launch the same project through B on 8775. B publishes its UUID into that file but preserves A’s URL/key. Its game polls A and B cannot establish its expected bridge binding.

I reproduced that preservation using the actual `prepare` method with an in-memory filesystem. This does **not** prove that A accepts B’s commands—the UUID fence may reject them—but it does prove broken routing.

**Correction:** separate named-instance profiles and bridge templates, and verify endpoint/key consistency before launch. Do not silently rewrite another instance’s profile.

---

**F07 — P2 — Per-instance journals are unsafe for recovery of shared host configuration files. [DATA-CRITICAL]**  
Anchors: `DESIGN.md:83`; [host_config.py:1091](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/host_config.py:1091), [host_config.py:1141](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/host_config.py:1141), [host_config.py:1217](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/host_config.py:1217).

These transactions store and recover **whole Claude/Codex files**, which remain shared between instances.

**Failure scenario:** interrupt A after writing Claude but before Codex. B consults only its new journal, updates its own registrations/timeouts, and commits. A later resumes recovery and sees bytes outside its recorded original/target states, producing `registration_recovery_conflict`. File-handle exclusion during a live transaction does not resolve an abandoned transaction in another journal.

**Correction:** retain one shared recovery authority for the shared config pair, preferably the existing journal path. Recover or refuse an incomplete transaction before any instance starts a new one.

---

**F08 — P2 — The documented receipt recovery and rename procedures are incomplete. [DATA-CRITICAL]**  
Anchors: `DESIGN.md:179`, `DESIGN.md:181`; [identity_migration.py:958](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/identity_migration.py:958), [identity_migration.py:1182](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/identity_migration.py:1182).

**Failure scenarios:**

1. Delete only a settled receipt after a questionable migration, leaving `runs.pre-v2.json`. Recovery rejects the orphaned backup as `incomplete_runs_backup_artifacts`. I reproduced this with the actual recovery function and mocked existence checks.
2. Interrupt after receipt publication but before marker cleanup; rename the root and rewrite only receipt paths. The remaining transaction marker still embeds four old absolute paths and fails validation.

**Correction:** remove the receipt-only deletion advice. Preserve suspect artifacts and require a backed-up, explicit recovery procedure. Defer root relocation; use the original token or a fresh independent store until relocation has a complete transactional specification.

---

**F09 — P2 — The reseal explanation identifies the wrong packaged code and misses game-policy alignment.**  
Anchors: `DESIGN.md:13`, `DESIGN.md:222`; [build_native_launcher.py:56](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/build_native_launcher.py:56), [build_native_launcher.py:1050](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/build_native_launcher.py:1050), [dayz_test_worker.py:263](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/dayz_test_worker.py:263), [process_lifecycle.py:2680](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/dayz_mcp/process_lifecycle.py:2680).

`resolve_launcher_policy_path` and `_cache_path` are builder functions, not packaged launcher modules. Conversely, `host_config.py`, `server_cli.py`, `daemon_contract.py`, and `normal_daemon_policy.py` are packaged.

**Failure scenario:** configure B’s daemon with the Experimental `--game-path` but reuse a retail sealed policy. The worker still supplies the retail `diag_executable`; B’s lifecycle rejects it as `executable_not_allowed`. Passing `--policy` to a builder does not alter an already sealed executable.

**Correction:** align named daemon game path with sealed `diag_executable`/`game_directory`, and reseal the named bundle with the required packaged changes. Compatibility of the unchanged default executable must be tested separately.

---

**F10 — P2 — Full-resolution capture files still collide.**  
Anchors: `DESIGN.md:32`, `DESIGN.md:220`; [mcp_capture.py:1454](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/mcp_capture.py:1454), [mcp_capture.py:1464](C:/Users/guill/dzmcp_gauntlet/d97de/ws/tools/mcp_capture.py:1464).

Splitting the frame-state sidecar does not split the default output directory. Filenames contain only a timestamp through milliseconds and saves overwrite that path.

**Failure scenario:** two clients save captures in the same millisecond with no explicit output override. Both return the same filename; one image replaces the other. A fixed-clock in-memory execution reproduced identical paths for two different instance tokens.

**Correction:** give named instances separate default capture output directories, preserving the default instance’s current directory and naming contract.

### Instance-fixed places the first design missed
| Place | Verified anchor | Required treatment |
|---|---|---|
| Normal policy loading and revalidation, including the separate packaged implementation | `daemon_policy.py:319`, `normal_daemon_policy.py:158` | Carry and retain the selected instance. |
| Standalone secure-launcher policy lookup | `secure_launcher.py:197` | Select the intended instance explicitly. |
| Strict provenance option sets, registration records and reconstructed config | `host_config.py:54`, `:71`, `:390` | Include and compare instance/game path. |
| Doctor’s client/daemon option inventories and policy comparison | `doctor.py:44`, `:70`, `:381` | Changing probe names alone is insufficient. |
| Programmatic stdio-client construction | `stdio_bridge.py:60` | Forward instance/game path into installer options and client argv. |
| Installer bridge templates | `install_mcp.py:1037` | `_mcp_config` is shared by instances using one tools tree. |
| Project profiles, `serverDZ.cfg`, mission persistence and build outputs/temp | `dayz_test_worker.py:256`, `:266`, `:744`; `process_lifecycle.py:3645` | Separate or serialize according to resource ownership. |
| Full-resolution capture directory and filenames | `mcp_capture.py:1460`, `:1469` | Separate named defaults. |
| Default exec audit file | `daemon.py:342` | `tools/_audit/exec_enforce.jsonl` remains shared in one tree. |
| Global installed skills and ownership manifests | `knowledge_pack.py:58`, `:62`, `:181` | Packs may be separate while `%USERPROFILE%\.agents\skills` remains shared. A named install can skip existing, differently versioned skills. |
| Builder and runtime archive-member inventories | `build_native_launcher.py:56`, `native_bundle.py:67` | Keep closure validation consistent if adding a helper module. |
| Steam helper’s process-local exclusion | `steam_prepare_supervisor.py:131` | It provides no cross-instance exclusion. |

The dependency cache is verified by size/hash, but is **filename-keyed**, not content-addressed as claimed. Both builders also use the same `.partial` filename (`build_native_launcher.py:251–259`); concurrent builds need serialization or separate temporary files.

The production job objects I found are anonymous. No production named mutex, named pipe, or scheduled-task registration was found in the inspected source. This does not establish the machine’s live task inventory.
