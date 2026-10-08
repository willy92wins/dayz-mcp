# gpt-6.1-sol review, g5, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

All paths below refer to this tree. Reproductions used real functions with filesystem writes or process launches mocked where necessary.

**F1 — P1 — Migration accredits a package that is not the executed target.**

Anchors: [instance_context.py:86](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/instance_context.py:86), [identity_migration.py:726](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/identity_migration.py:726).

`accredited_package_dir` falls back to `candidates.append(Path(cwd) / "dayz_mcp")`, even for an explicit absolute script whose own package lacks accreditation.

**Scenario:** run the legacy writer that hardcodes `DayZ_MCP_130` as:

```text
C:\Python314\python.exe C:\fixture\legacy\dayz_mcp\__main__.py --daemon --keyfile C:\keys\K
```

Set its working directory to this tree’s `tools`. Scan for migration of token `130`. **Observed:** disposition is `not_writer`, and scanner output is `()`. The unknown same-root writer is ignored because an unrelated working-directory package supplies the marker.

**Fix:** bind accreditation to the actual Python target and its supported import resolution; unrelated candidate directories must not establish root ownership.

**F2 — P1 — Runtime-state activation bypasses lifetime root exclusion.**

Anchor: [daemon.py:392](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/daemon.py:392).

`build_server_state(..., activate_coordination=True)` calls migration and `_activate_server_coordination` without acquiring or requiring `RootWriterLease`.

**Scenario:** installation A serves token `130` on 8775 with a settled receipt and holds the writer lease. From installation B’s approved interpreter, call:

```python
daemon.build_server_state(
    config_for_token_130_port_8785,
    key,
    activate_coordination=True,
)
```

The settled receipt permits migration verification; activation then opens the same coordination and manifest stores. **Observed with activation mocked:** activation executes even when `RootWriterLease.try_acquire` would return false; that method is never called.

**Fix:** enforce lifetime ownership at every runtime-state activation entry point, including programmatic activation.

**F3 — P2 — `--game-path` does not configure lifecycle executable authority.**

Anchors: [daemon.py:595](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/daemon.py:595), [process_lifecycle.py:2909](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/process_lifecycle.py:2909).

Lifecycle construction still uses `os.environ.get("DAYZ_GAME_PATH", retail_default)`. It never consumes `config.game_path`.

**Scenario:** launch the named daemon with `--instance 130 --game-path "C:\Program Files (x86)\Steam\steamapps\common\DayZ Experimental"` and no `DAYZ_GAME_PATH`. Submit a correctly sealed Experimental `DayZDiag_x64.exe` launch. **Observed executable validation:** `executable_not_allowed`, because lifecycle authority still points at retail.

**Fix:** pass the validated bound game directory into lifecycle construction, preserving the existing default fallback.

**F4 — P2 — The packaged worker selects default profiles and modifies them before rejecting routing.**

Anchors: [dayz_test_worker.py:296](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/dayz_test_worker.py:296), [launcher.cpp:600](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/native-launchers/dayz-test-v1/src/launcher.cpp:600), [process_lifecycle.py:2173](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/process_lifecycle.py:2173), [loopback.py:1739](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/loopback.py:1739).

Profile selection uses `payload.get("instance_token", current_instance_token())`. The canonical request rejects `instance_token`, and the private worker receives neither selector argv nor selector context. Its argv is simply the packaged application.

**Scenario:** invoke a rebuilt named launcher for a project whose default `_server\profiles\dayz_mcp.json` contains A’s endpoint/key. **Observed worker output:** `_server\profiles`, rather than `_server\profiles-130`. B calls `prepare`, which writes a new UUID into A’s file, then returns `instance_endpoint_mismatch`. The in-memory reproduction confirmed the UUID changed before refusal.

**Fix:** carry accredited selection into the private worker; validate profile ownership and endpoint/key before modifying its configuration.

**F5 — P2 — Recovery’s second quiescence check loses the selected instance.**

Anchor: [identity_migration.py:1485](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/identity_migration.py:1485).

The `_assert_quiescent` call after `_recover_backup_transaction` omits `state_token`.

**Scenario:** recover an interrupted `130` migration while a supported default daemon is alive. The first check correctly exempts that different-root daemon; recovery executes; the second check classifies against the default root and rejects it. **Observed scanner selectors:** `['130', None]`.

**Fix:** forward `state_token` to this recovery check too.

**F6 — P2 — Required installation and launch entry points cannot select a named instance.**

Anchors: [install_mcp.py:764](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/install_mcp.py:764), [install_mcp.py:734](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/install_mcp.py:734), [install-mcp.ps1:743](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/install-mcp.ps1:743), [secure_launcher.py:249](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/secure_launcher.py:249), [stdio_bridge.py:430](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/stdio_bridge.py:430).

Installer options omit the selector; registration commands remain hardcoded to `dayz-mcp`. Secure-launcher and stdio-bridge CLI parsers also omit it.

**Scenarios:**

- `python -B install_mcp.py --instance 130 --port 8775 --register` exits **2** at argument parsing.
- `python -B -m dayz_mcp.secure_launcher DayZ130 --instance 130` exits **2**.
- The PowerShell installer still emits `mcp add dayz-mcp`, so it cannot install the sibling registration.

The first two parser failures were reproduced.

**Fix:** complete selector support and dynamic registration handling across both installers and relevant CLIs; retain default payload compatibility.

**F7 — P2 — `p0s_gate.py` can operate on the wrong store without rejecting conflicting context.**

Anchor: [p0s_gate.py:318](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/p0s_gate.py:318).

It still calls `ensure_runs_v1_backup(RuntimePaths.from_env(), args.port)` without a selector or environment-conflict validation.

**Scenario:**

```powershell
$env:DAYZ_MCP_INSTANCE = '130'
python -B p0s_gate.py backup-runs-v1 --port 8775
```

With an unsettled default store and no process blockers, this operates on `%LOCALAPPDATA%\DayZ_MCP`, despite the conflicting named environment. **Observed with backup publication mocked:** the selected root was `DayZ_MCP`, selector kwargs were empty, and the command reported `status: verified`. Adding `--instance 130` instead exits **2**.

**Fix:** add explicit selection and reject conflicting environment/port configuration before writes.

**F8 — P1 — Shared build targets remain outside cross-instance exclusion.**

Anchors: [dayz_test_worker.py:780](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/dayz_test_worker.py:780), [process_lifecycle.py:3558](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/process_lifecycle.py:3558), [build_native_launcher.py:258](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/build_native_launcher.py:258).

The box interlock starts at lifecycle admission, after worker builds. Build requests retain common target/temp paths. Native launcher builders also retain one filename-keyed `.partial` cache path.

**Scenario:** A and B concurrently build `ExampleMod` with `clean=True` and the same sealed `mods_root`/`build_temp_root`. A barrier reproduction admitted **both** build requests with:

```text
target = P:\Mods\@ExampleMod\Addons
temp   = P:\temp\ExampleMod
clear  = True
```

Both consumers can clear/write those shared locations before either acquires box admission. Separately, two uncached native-builder downloads race on the same `.partial` file.

**Fix:** serialize shared build resources independently of lifecycle launch admission.

**F9 — P2 — Named captures and frame state remain shared.**

Anchors: [mcp_capture.py:561](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/mcp_capture.py:561), [mcp_capture.py:1460](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/mcp_capture.py:1460), [mcp_capture.py:1469](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/mcp_capture.py:1469).

Neither default path resolver uses instance selection.

**Scenario:** default and `130` save different images at the same fixed millisecond, with no output override. **Observed using the extracted real functions and mocked image writes:** both return `dayz_mcp_captures\capture_20261006_120000_125.jpg`; both frame-state paths are `DayZ_MCP\capture-frame-state.json`. The second image overwrites the first.

**Fix:** separate named output and frame-state defaults while preserving default-instance fallbacks.

**F10 — P2 — Global-skills ownership is not enforced for named installs.**

Anchors: [knowledge_pack.py:190](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/knowledge_pack.py:190), [knowledge_pack.py:273](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/knowledge_pack.py:273).

`sync_skills` defaults to `owner="default"`; `install_knowledge_pack` never passes the bound owner.

**Scenario:** bind token `130`, then call `install_knowledge_pack(sync=True)` with a named pack containing a globally absent skill. **Observed:** the installer calls `sync_skills` without ownership, and the real synchronization function copies the skill into `%USERPROFILE%\.agents\skills` instead of refusing. Its ownership manifest belongs to the named store.

**Fix:** enforce ownership from validated instance context at the installation boundary.

## SPEC COVERAGE

“Done” below means source-level implementation, unless stated otherwise; it does not claim live acceptance.

| Specification bullet | Status | Evidence / gap |
|---|---|---|
| 1 — Optional config, installer and CLI fields | **wrong** | Server fields exist; installer and required CLI fields are missing, F6/F7. |
| 1 — Grammar and reserved `default` | **done** | Central validation and negative tests. |
| 1 — Duplicate, malformed and glued flags | **done** | Server startup and silent parser reject them. |
| 1 — Default paths, registration, keyfile, argv and receipt compatibility | **done / missing** | Existing defaults retained; deployed executable and receipt acceptance remain unverified. |
| 1 — Resolve context once; prevent inherited-env root splitting | **done** | Bound server context and daemon conflict check exist. |
| 1 — Omit registration env; publish context for children | **wrong** | Registration env is omitted; child publication is incomplete, F4. |
| 2 — Update every named propagation surface | **wrong** | Secure-launcher and CLI gaps, F6. |
| 2 — Append flags after complete existing argv | **done** | `daemon_contract.py:51`. |
| 2 — Accredited WMI argv carries token/game path | **done** | Reconstructed provenance carries both into daemon argv. Captured WMI acceptance not run. |
| 2 — Bind registration name; compare hosts’ selector/path/port/key | **done** | Registration records and equality include both fields. |
| 2 — Retain selector in revalidation closures | **done** | Both normal-policy loaders capture the selected registration. |
| 2 — Accreditation before credentials/HTTP | **done** | Existing accredited transport ordering retained. |
| 3 — Lifetime exclusion across activation paths | **wrong** | Daemon wrapper holds it; activation helper bypasses it, F2. |
| 3 — Reject same-root rival regardless of port/key/install | **wrong** | Enforced only through `run_daemon`, F2. |
| 3 — Preserve formats/default store | **done** | No default-store relocation or format migration introduced. |
| 4 — Preserve valid-client exclusion | **done** | Selected regression passed. |
| 4 — Parse actual Python target before classification | **wrong** | Accreditation can come from an unrelated candidate, F1. |
| 4 — Block same-root/unknown writers; exempt accredited siblings | **wrong** | Unknown writer exemption reproduced, F1. |
| 4 — Preserve native identity/PID/ancestor checks; reconcile `allowed_seen` | **done** | Existing checks retained; exempt allowed identities remain checked. |
| 4 — Selector at every quiescence check | **wrong** | Recovery call misses it, F5. |
| 4 — Preserve settled-receipt shortcut | **done** | Shortcut retained. |
| 4 — Explicit `p0s_gate` selection and conflict rejection | **missing** | F7. |
| 4 — Validate existing `130` receipt unchanged | **missing** | Actual receipt and absolute paths not tested. |
| 5 — Runtime/inbox/knowledge/frame/capture/audit isolation | **wrong** | Runtime, inbox, knowledge and audit changes exist; frame/capture isolation is missing, F9. |
| 5 — Preserve helper default fallbacks | **done** | Inspected default fallbacks retained. |
| 5 — Shared host-config recovery authority | **done** | Existing shared journal path retained. |
| 5 — Parameterize installer probes, writes, rollback and not-found parsing | **missing** | Both installers remain default-specific, F6. |
| 5 — Namespace named `_mcp_config` templates | **missing** | Existing template paths retained. |
| 5 — Separate profiles and validate routing before launch | **wrong** | Worker loses selection; validation follows modification, F4. |
| 5 — Explicit global-skills ownership | **wrong** | Declared ownership is bypassed by caller defaults, F10. |
| 6 — Shared physical-box interlock | **done** | OS interlock wraps lifecycle admission. |
| 6 — Recheck before profile/storage/Steam/spawn effects | **done** | Existing checks execute inside the wrapper. |
| 6 — Hold through observable process or settlement | **done** | Wrapper spans spawn, identity snapshot and settlement; unresolved/live cases untested. |
| 6 — Death release; intent recovery and foreign detection | **done / missing** | OS-handle lifetime and existing recovery remain; cross-instance crash acceptance missing. |
| 6 — Independent leases and stopping authority | **done** | No shared session lease or cross-instance stop authority added. |
| 6 — Independent build-resource serialization | **missing** | F8. |
| 7 — Reseal named Experimental launcher and approved evidence | **missing** | Report explicitly defers it. |
| 7 — Aligned packaged closure inventories | **done** | Selector lives in already packaged `server_cli`; lock updated. |
| 7 — Unchanged default executable accepted and operational | **missing** | No deployed-bundle acceptance evidence. |
| 7 — No PBO rebuild; deployed bridge compatibility | **done / missing** | No addon change; deployed compatibility unverified. |

Required regression coverage is incomplete:

- **Covered narrowly:** valid-client exclusion, one default argv case, token grammar, registration-name binding, and unaccredited flagged-writer blocking.
- **Insufficient or contradicted:** actual-target accreditation, all activation paths, recovery selector propagation, packaged profile routing, capture isolation and skills ownership.
- **Missing acceptance:** cross-process writer contention, concurrent box admission including Steam/pre-bind windows, shared-host interrupted recovery, full migration interruption matrix, aligned sealed bundles, and live simultaneous-launch negative case.

## GATE GAP

The gate proves no additional failures in the selected suite. It does not prove this specification.

The new writer test exercises two lease objects and checks that `runs.json` remains absent; it never activates competing runtime states. The accreditation positive test creates only a marker file, so it does not prove which code Python executes. No new test crosses the packaged worker boundary or controls concurrent build/launch admission.

Additionally, [test_install_mcp.py:1980](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/tests/test_install_mcp.py:1980) explicitly exempts the missing selector flags from parser-copy consistency checks. This allows a required omission to pass the gate.

## PREMISE

The binding specification requires surfaces that `REPORT.md` declares outside the implementer’s edit set. Those restrictions are incompatible with completing this batch; they do not establish an approved scope reduction.

The reported `GAUNTLET_GATE: PASS` is therefore a regression result, not specification acceptance. The injected October 1 release state also does not establish the state of this modified batch.

## NOT VERIFIED

- I ran **7 selected new tests: all passed** under system Python 3.14 using `-B`.
- I did not run the two temporary-file tests or the full suite in this read-only sandbox. This tree has no project venv, and system Python lacks Pillow.
- Behavioral reproductions mocked writes/process launches; capture functions were extracted from the real source.
- No daemon, DayZ, Steam, installer transaction or build process was launched.
- Actual `130` receipt validity, deployed launcher hashes/closure, unchanged default bundle operation, bridge compatibility and simultaneous-launch acceptance remain unverified.
- No files were modified, no commit was created, and no review/handoff artifact was written.