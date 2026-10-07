VERDICT: FINDINGS

## MECHANICAL

**1. Path-naming matrix**

| Consumer | Folder derivation | Result and evidence |
|---|---|---|
| Canonical leaf helper | Explicit token → `profiles` or `profiles-<token>` | Single naming contract; validates token. `tools/dayz_mcp/server_cli.py:46` |
| Worker server/client/offline argv and recorded launch path | Helper with accredited request token | Both role folders share the selected leaf. `tools/dayz_mcp/dayz_test_worker.py:476`, `tools/dayz_mcp/dayz_test_worker.py:540` |
| Preparation and bridge config | Helper with daemon instance token; config beneath supplied folder | Expected basename checked before provisioning. `tools/dayz_mcp/loopback.py:1874`, `tools/dayz_mcp/loopback.py:1880` |
| Planned artifacts | Helper through `_planned_profile_leaf`, plus mode artifact roots | Instance-aware construction. `tools/dayz_mcp/dayz_test_tool.py:797`, `tools/dayz_mcp/dayz_test_tool.py:808`, `tools/dayz_mcp/dayz_test_tool.py:860` |
| Client/offline diagnosis and dump roots | Planned helper, or validated recorded anchor for an existing run | No legacy fallback for an invalid supplied anchor. `tools/dayz_mcp/dayz_test_tool.py:877`, `tools/dayz_mcp/dayz_test_tool.py:884` |
| Stop artifacts | Validated recorded anchor → mode artifact roots | Server anchor still expands to client artifacts after client retirement. `tools/dayz_mcp/dayz_test_tool.py:1699`, `tools/dayz_mcp/dayz_test_tool.py:1726` |
| Close admission and role folders | Validated recorded anchor → approved role root | Validation precedes role-folder construction. `tools/dayz_mcp/dayz_test_tool.py:3371`, `tools/dayz_mcp/dayz_test_tool.py:3400`, `tools/dayz_mcp/dayz_test_tool.py:3409` |
| Profile recognition | Helper with bound context | Exact selected leaf; absolute path, traversal and role-parent checks retained. `tools/dayz_mcp/log_tail.py:201`, `tools/dayz_mcp/log_tail.py:206` |
| Opposite-role log discovery | Recorded leaf retained when constructing sibling | No independently selected profile basename. `tools/dayz_mcp/launch_logs.py:28`, `tools/dayz_mcp/launch_logs.py:30`, `tools/dayz_mcp/launch_logs.py:32` |
| RPT/script-log enumeration | Supplied admitted folder | Enumerates `.rpt`/`.log` beneath that folder. `tools/dayz_mcp/log_tail.py:215`, `tools/dayz_mcp/log_tail.py:220` |
| Close RPT, logout observation and rotation | Validated role folder; subsequent reads retain watch path | Initial RPT selection, logout boundary and rotation follow the named folder. `tools/dayz_mcp/dayz_test_tool.py:4239`, `tools/dayz_mcp/dayz_test_tool.py:4260`, `tools/dayz_mcp/dayz_test_tool.py:4328` |
| `file_matches` | Shared validated role resolver | Does not reconstruct legacy folders. `tools/dayz_mcp/server.py:2741`, `tools/dayz_mcp/server.py:3148` |
| Attestation boundaries | Helper with request token | Named root supplied to boundary capture. `tools/dayz_mcp/dayz_test_worker.py:622`, `tools/dayz_mcp/dayz_test_attestation.py:315` |
| Attestation script/profile-file reads | Same helper and token | Both sources remain beneath the selected root. `tools/dayz_mcp/dayz_test_attestation.py:480`, `tools/dayz_mcp/dayz_test_attestation.py:404`, `tools/dayz_mcp/dayz_test_attestation.py:433` |
| Dump baseline and later diagnosis | Supplied client roots retained in baseline | Readers do not select another profile leaf. `tools/dayz_mcp/client_steam_bootstrap.py:75`, `tools/dayz_mcp/client_steam_bootstrap.py:100` |
| VPP administrative files | Helper with canonical request token | Permissions, superadmins and credentials use selected server profiles; `serverDZ.cfg` stays under `_server`. `tools/dayz_mcp/native_launcher_transaction.py:293`, `tools/dayz_mcp/native_launcher_transaction.py:297`, `tools/dayz_mcp/native_launcher_transaction.py:299` |
| Launch RPT watcher and console capture | Exact `-profiles` value from launch argv | Retains supplied folder; no basename reconstruction. `tools/dayz_mcp/process_lifecycle.py:416`, `tools/dayz_mcp/process_lifecycle.py:2715`, `tools/dayz_mcp/process_lifecycle.py:549` |
| Launch-intent config recovery | Recorded intent anchor | Reads `dayz_mcp.json` beneath that anchor. `tools/dayz_mcp/process_lifecycle.py:4542` |
| Public profile label | Literal recognition of default basename; otherwise last component | Display-only; does not resolve a directory. `tools/dayz_mcp/process_lifecycle.py:333`, `tools/dayz_mcp/process_lifecycle.py:342` |
| Installer templates/operator destinations | Template literals or explicitly supplied roots | Does not select a running instance’s role folder. `tools/install_mcp.py:1096`, `tools/install_mcp.py:1105`, `tools/install-mcp.ps1:999` |
| H8 retail negative fixtures | Fixed legacy literal | Explicit rejection-test input, not a named-run consumer. `tools/_session_coordination/h8_distributed_codex_gate.py:1168`, `tools/_session_coordination/h8_real_codex_gate.py:1028` |
| Project-map log anchors | Independently hardcoded legacy folders | **M1:** both anchors bypass the naming contract. `tools/gen-project-map.ps1:130`, `tools/gen-project-map.ps1:131` |

**2. Existing-run entry points**

| Caller | Recorded-anchor validation before folder use | Result and evidence |
|---|---|---|
| Client extension admission | `_validated_recorded_leaf` | Invalid anchor rejects extension. `tools/dayz_mcp/dayz_test_tool.py:604`, `tools/dayz_mcp/dayz_test_tool.py:2729` |
| Client replacement/stalled-start diagnosis | `_client_profile_roots(..., run)` validates anchor | Root resolved before RPT diagnosis. `tools/dayz_mcp/dayz_test_tool.py:2788`, `tools/dayz_mcp/dayz_test_tool.py:878` |
| Extension dump baseline | Explicit validation, then run-aware client roots | Invalid recorded anchor fails before request execution. `tools/dayz_mcp/dayz_test_tool.py:2861`, `tools/dayz_mcp/dayz_test_tool.py:2866` |
| Stop artifact resolution | `_stop_artifacts` validates anchor | Evaluated before `_execute_request` runs. Missing anchor returns no artifacts. `tools/dayz_mcp/dayz_test_tool.py:1695`, `tools/dayz_mcp/dayz_test_tool.py:1699`, `tools/dayz_mcp/dayz_test_tool.py:3162` |
| Close RPT/logout resolution | `_profiles_match_policy`, then `_close_role_folder` | Validation precedes file enumeration. Rejected anchors produce missing-RPT handling; existing lifecycle close behavior remains. `tools/dayz_mcp/dayz_test_tool.py:4231`, `tools/dayz_mcp/dayz_test_tool.py:4239`, `tools/dayz_mcp/dayz_test_tool.py:4253` |
| `file_matches`, initial resolution | `_profiles_dir_for_role` → `_close_role_folder(..., run)` | Validates before profile-file access. `tools/dayz_mcp/server.py:2741`, `tools/dayz_mcp/server.py:3148` |
| `file_matches`, refreshed/final resolution | Same resolver | Anchor and resulting root rechecked during waiting and before success. `tools/dayz_mcp/server.py:3220`, `tools/dayz_mcp/server.py:3281` |
| `logs_since` | `_profile_dirs_from_runs` → sealed-project anchor validation | Rejected rows contribute no folders. `tools/dayz_mcp/server.py:5402`, `tools/dayz_mcp/launch_logs.py:162` |
| `log_matches` | Same shared log resolver | Validation precedes log discovery; aggregate role behavior retained. `tools/dayz_mcp/server.py:2408`, `tools/dayz_mcp/server.py:2418` |
| Capture-window profile selection | Same shared log resolver | Client command-line discriminator comes from admitted folders. `tools/dayz_mcp/server.py:7559`, `tools/dayz_mcp/server.py:7561` |
| Log sibling expansion | Called after recorded-anchor validation | Retains validated leaf; no alternate production caller found. `tools/dayz_mcp/launch_logs.py:162`, `tools/dayz_mcp/launch_logs.py:165` |
| Launch-intent recovery | Separate intent identity validation, not the existing-run resolver | Checks absolute recorded path, command-line identity and config UUID; constructs no alternative leaf. `tools/dayz_mcp/process_lifecycle.py:4507`, `tools/dayz_mcp/process_lifecycle.py:4598`, `tools/dayz_mcp/process_lifecycle.py:4601` |

**3. Cleanup symmetry**

| Lifecycle event | Profile leaf / `dayz_mcp.json` | Per-launch UUID/binding | Evidence |
|---|---|---|---|
| Named preparation | Creates only absent leaf under existing roots; seeds absent config | Mints, writes and rereads UUID; installs starting binding | `tools/dayz_mcp/loopback.py:1824`, `tools/dayz_mcp/loopback.py:1848`, `tools/dayz_mcp/loopback.py:1900`, `tools/dayz_mcp/loopback.py:1908`, `tools/dayz_mcp/loopback.py:1921` |
| Successful launch | Retained | Starting binding confirmed against process identity | `tools/dayz_mcp/process_lifecycle.py:2284`, `tools/dayz_mcp/loopback.py:1950` |
| Preparation refusal | Existing contents retained; no rollback deletion of a newly created leaf/config | No spawn after preparation error; binding installation follows successful UUID reread | `tools/dayz_mcp/process_lifecycle.py:4174`, `tools/dayz_mcp/loopback.py:1917`, `tools/dayz_mcp/loopback.py:1921` |
| Refusal/failure after minting | Leaf and config retained | Minted binding retired on launch-failure branches | `tools/dayz_mcp/process_lifecycle.py:4200`, `tools/dayz_mcp/process_lifecycle.py:4248`, `tools/dayz_mcp/process_lifecycle.py:4334` |
| Successful stop/retirement | Leaf, config and disk UUID retained | Role index, binding, queues and capabilities retired | `tools/dayz_mcp/process_lifecycle.py:5483`, `tools/dayz_mcp/process_lifecycle.py:2354`, `tools/dayz_mcp/loopback.py:1976`, `tools/dayz_mcp/loopback.py:2100` |
| Process death/recovery retirement | Persistent profile contents retained | Recovery retirement reaches shared binding retirement | `tools/dayz_mcp/process_lifecycle.py:7330`, `tools/dayz_mcp/process_lifecycle.py:2354` |
| Daemon crash | Disk leaf/config/UUID survive; no new provisioning rollback | RAM binding does not survive process death; launch-intent recovery reads recorded config | `tools/dayz_mcp/loopback.py:1921`, `tools/dayz_mcp/process_lifecycle.py:4536`, `tools/dayz_mcp/process_lifecycle.py:4542` |
| Next launch/replacement | Reuses leaf; validates existing endpoint/key; rewrites config UUID | Replacement retires prior role; fresh UUID installed | `tools/dayz_mcp/loopback.py:1886`, `tools/dayz_mcp/loopback.py:1896`, `tools/dayz_mcp/loopback.py:1911`, `tools/dayz_mcp/process_lifecycle.py:2258` |
| Atomic-write temporary files | Normal completion/error path removes its identified temporary | Hard interruption can bypass `finally`; crash scavenging was not established by this walk | `tools/dayz_mcp/runtime_state.py:2333`, `tools/dayz_mcp/runtime_state.py:2356`, `tools/dayz_mcp/runtime_state.py:2365` |
| Retention contract | Profile deletion is explicitly prohibited; retained config is rewritten next launch | No cleanup mismatch found against that contract | `_audit_context/SPEC_G5FIX.md:59`, `tools/dayz_mcp/loopback.py:1911` |

**4. Token authority**

| Token consumer/source | Authority | Result and evidence |
|---|---|---|
| `profile_leaf_name` argument | Explicit caller input | Pure validation/formatting; no ambient selection. `tools/dayz_mcp/server_cli.py:46`, `tools/dayz_mcp/server_cli.py:53` |
| Daemon planned paths and recorded-anchor validation | Published bound context | `_planned_profile_leaf` reads `bound_instance_token`. `tools/dayz_mcp/server_cli.py:66`, `tools/dayz_mcp/dayz_test_tool.py:797` |
| Log-folder admission | Published bound context | Same selected leaf as daemon artifact resolution. `tools/dayz_mcp/log_tail.py:206` |
| Preparation | Daemon state token copied from selected configuration | No token inferred from folder/config contents. `tools/dayz_mcp/daemon.py:397`, `tools/dayz_mcp/loopback.py:1875` |
| Worker launch and attestation | Canonical accredited request payload | Worker verifies canonical bytes/hash before using payload token; all four initialization call sites pass it. `tools/dayz_mcp/dayz_test_worker.py:1055`, `tools/dayz_mcp/dayz_test_worker.py:453`, `tools/dayz_mcp/dayz_test_worker.py:662`, `tools/dayz_mcp/dayz_test_worker.py:695`, `tools/dayz_mcp/dayz_test_worker.py:732`, `tools/dayz_mcp/dayz_test_worker.py:1189` |
| VPP preflight | Canonical parsed request payload | Both preflight routes parse the request before consuming its token. `tools/dayz_mcp/native_launcher_transaction.py:292`, `tools/dayz_mcp/native_launcher_transaction.py:655`, `tools/dayz_mcp/native_launcher_transaction.py:697` |
| Request-token producer | Published bound context | Copies selected token into the request. `tools/dayz_mcp/dayz_test_tool.py:482`, `tools/dayz_mcp/dayz_test_tool.py:485` |
| Recorded profile basename | Validated evidence, not independent token authority | Compared against bound selection before retaining leaf. `tools/dayz_mcp/dayz_test_tool.py:830`, `tools/dayz_mcp/dayz_test_tool.py:833` |

## FINDINGS

| ID | Severity | Location | Finding and executable scenario |
|---|---|---|---|
| M1 | P3 | `tools/gen-project-map.ps1:130`, `tools/gen-project-map.ps1:131`; generated output at `PROJECT-MAP.md:19`, `PROJECT-MAP.md:20` | **Documentation consumer independently constructs legacy profile folders.** In a disposable repository copy, create `_server/profiles-130` and `_client/profiles-130`, leaving legacy leaves absent. Run `powershell -NoProfile -ExecutionPolicy Bypass -File tools/gen-project-map.ps1`. The generated generic server/client log anchors still point to absent `profiles` folders. This is pre-existing, outside the changed runtime functions, and documentation-only; it meets check 1’s explicit independent-construction criterion. Scenario supplied, not executed. |

No additional findings from the existing-run entry-point, cleanup-symmetry or token-authority walks.

## NOT VERIFIED

- No tests, executable scenarios, DayZ, daemons or MCP tools were run.
- No filesystem mutations or `%LOCALAPPDATA%` access occurred.
- Launcher reseal, packaged execution, deployment and in-game behavior remain unverified.
- Filesystem races, crash injection and hard-crash temporary-file recovery were not exercised.
- Historical backups/review snapshots were excluded from active-consumer classification. This is a mechanical pre-check result, not a release approval.