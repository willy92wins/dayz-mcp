## CHANGES

- `tools/dayz_mcp/server_cli.py`: owns the token grammar (`default` reserved), one bound selector, state-root and registration names, and `reject_conflicting_environment`. `--instance` and `--game-path` are optional. Glued and duplicate flags are invalid in the silent scanner and in `parse_args`. An inherited environment value is never a selector; if `DAYZ_MCP_INSTANCE`, `DAYZ_MCP_PORT`, or `DAYZ_MCP_GAME_PATH` is set it must match the bound selector or startup fails before writes. Omission stays the default instance.
- `tools/dayz_mcp/instance_context.py` (new, unpackaged): re-exports the selector, publishes `ROOT_SELECTION_CONTRACT_ID = "97de-v1"`, and holds `RootWriterLease`. The lease locks byte 1 of the existing `<root>/.daemon-startup.lock` for the writer lifetime (byte 0 remains the startup election). A second writer of the same root fails with `DAEMON_STARTUP_CONTENDED` and does not activate coordination or write manifests.
- `tools/dayz_mcp/server.py` `ServerConfig` / `parse_args` / `ClientRuntime.__init__`: bind the selector once. The default registration calls `resolve_daemon_provenance()` with no keyword arguments. A named registration passes `server_name`. Provenance instance token and game path must match the config.
- `tools/dayz_mcp/daemon_contract.py` `build_daemon_argv`: append `--instance` then `--game-path` only when set, after the existing argv. Omission leaves the default argv unchanged.
- `tools/dayz_mcp/daemon.py` `run_daemon`: reject conflicting environment, acquire the root writer lease, then migrate and serve. `RuntimePaths.for_token` selects the store. Named exec audit defaults to `exec_enforce-<token>.jsonl`; the default path is unchanged. `read_key(_required_keyfile(config))` remains inside `run_daemon`.
- `tools/dayz_mcp/runtime_state.py` `RuntimePaths.for_token`: `None` is `from_env()`; a token uses `DayZ_MCP_<token>`. The default store is not migrated.
- `tools/dayz_mcp/identity_migration.py`: classify role before instance. A valid client, including a supervised client, is not a writer. A writer is exempt only when the package beside the Python target contains the contract line and its state root differs. Unknown, bootstrap, malformed, and unaccredited tails block an unsettled migration. `state_token` is passed to every `_assert_quiescent` call, including recovery and post-copy. Allowed identities that are no longer blockers are still snapshot-matched and counted in `allowed_seen`. The settled-receipt shortcut is unchanged.
- `tools/dayz_mcp/host_config.py`: optional `--instance` / `--game-path` in the strict inventory; the registration name must equal `registration_name(namespace.instance)`; both hosts compare instance, game path, port, and keyfile. Provenance carries both fields. The default name is omitted from internal parser kwargs so existing default call replacements stay valid; a named registration still passes `server_name`. The shared host-config journal path is unchanged. Claude `env` is not added.
- `tools/dayz_mcp/daemon_policy.py` and `normal_daemon_policy.py` `load_normal_daemon_policy`: the initial load and the revalidation closure keep the selected registration. Default still calls `resolve_daemon_provenance()` with no kwargs.
- `tools/dayz_mcp/doctor.py`: the same options and policy fields; probes use `registration_name(current_instance_token())` and `RuntimePaths.for_token`.
- `tools/dayz_mcp/stdio_bridge.py` `official_client_argv`: appends the selector after the installer argv. `InstallerOptions` itself was not extended (that type lives outside the edit set).
- `tools/dayz_mcp/box_admission.py` (new) and `process_lifecycle.py` `_start_run_reserved`: shared OS lock at `%LOCALAPPDATA%\DayZ_MCP_shared\box-admission.lock` around profile preparation, storage rotation, Steam mutation, and spawn. Same-process threads share the hold so the existing Steam gate still returns `steam_prepare_busy`. A second process gets `box_admission_busy`. Process death closes the handle. Instance-local session leases are unchanged.
- `tools/dayz_mcp/process_lifecycle.py` `_prepare_instance`: after `prepare`, a real port and key must match `http://127.0.0.1:{port}/` and the key, or the launch returns `instance_endpoint_mismatch`. `loopback.prepare` still does not rewrite an existing URL or key.
- `tools/dayz_mcp/dayz_test_worker.py`: a bound token uses `profiles-<token>` under the same dev root. The default folder stays `profiles`.
- `tools/dayz_mcp/inbox.py`, `knowledge.py`, `knowledge_pack.py`: named roots for inbox, knowledge, and the pack directory. `FEEDBACK_PATH` is unchanged for the default. `GLOBAL_SKILLS_OWNER` is `default`; `sync_skills` for any other owner raises `global_skills_not_owned`.
- `tools/tests/test_install_mcp.py`: pin update. The installer copies cannot gain `--instance` / `--game-path` because `tools/install_mcp.py` and `tools/install-mcp.ps1` are outside the edit set. `installer_selector_omission` records that those copies omit the two flags, and the doctor comparison allows them as extras. Existing assertions were not deleted.
- `tools/packaged-modules.lock.json`: regenerated after the packaged-module edits (`server_cli.py`, `host_config.py`, `daemon_contract.py`, `normal_daemon_policy.py`, `dayz_test_worker.py`). This file is outside the requested edit set; leaving it stale fails the packaged-lock test.
- `CHANGELOG.md`: one Unreleased line.

## TESTS ADDED

Module `tools/tests/test_instance_identity_97de.py` (9 tests, all passing under the gate).

- `test_f01_valid_client_does_not_block_same_root_migration` — a valid unflagged or flagged client must not be a migration writer. A scanner that treats every same-root PID as a writer fails this.
- `test_f02_second_writer_same_token_different_port_does_not_activate` — the second lease of the same root is refused and does not create `runs.json`.
- `test_f03_unaccredited_flagged_writer_still_blocks` — `--instance` on a tree with no contract marker still blocks.
- `test_f03_accredited_other_root_does_not_block` — an accredited different root is not a blocker.
- `test_f04_registration_name_must_match_argv_token` — `dayz-mcp` must not accept an `--instance 130` registration.
- `test_default_daemon_argv_omits_instance_flags` — golden default argv; named flags come after it.
- `test_glued_and_reserved_tokens_are_rejected`
- `test_malformed_tail_blocks`
- `test_omission_namespace_has_no_token`

## GATE OUTPUT

```
--- tests.test_suite_structure (whole-suite ratchets): rc=1
    (same offenders as the base tree; not blocking)
--- tests.test_instance_identity_97de: rc=0
Ran 53 tests in 3.783s

OK
--- tests.test_instance_identity_97de on Python 3.11: rc=0 OK
--- fast tier: ran=5715 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

## ROUND 2 FIXES

- F1: `accredited_package_dir` resolves one package. An explicit `__main__.py` is that package only; the working directory cannot supply the contract marker for a different script.
- F2: `build_server_state(..., activate_coordination=True)` acquires `RootWriterLease` before migration or coordination. A refused lease raises `state_root_writer_busy` and does not activate stores. Same-process holders share the OS lock so a later activation in that process still works; another process does not.
- F3: `_lifecycle_game_path` passes `config.game_path` into `ProcessLifecycle`. The env var and the retail directory remain the fallback.
- F4: the native launcher copies `--instance` and `--game-path` from the accredited policy into the private worker environment. The worker selects `profiles-<token>` from that variable. `prepare` refuses a named instance whose profile directory or endpoint/key does not match, before it writes a UUID.
- F5: the quiescence check after `_recover_backup_transaction` receives `state_token`.
- F6: `install_mcp.py`, `install-mcp.ps1`, `secure_launcher.py` and `stdio_bridge.py` accept `--instance` and `--game-path`. Registration commands use `dayz-mcp` or `dayz-mcp-<token>`. A named remove never targets `dayz-mcp`.
- F7: `p0s_gate.py backup-runs-v1` takes the selector, rejects a conflicting environment, and backs up `RuntimePaths.for_token`.
- F8: addon builds take `shared_build_lock` on the target and temp paths before the broker call. The native builder holds a download lock and writes a per-process `.partial` file.
- F9: `resolve_capture_dir` and `frame_state_path` use the bound token. Omission keeps `dayz_mcp_captures` and `DayZ_MCP`.
- F10: `install_knowledge_pack` passes the bound owner into `sync_skills`. A token other than the default owner raises `global_skills_not_owned` before copying skills.
- Shared locks read `DAYZ_MCP_SHARED_ROOT`. Under `DAYZ_MCP_FAST_TESTS`, the real `%LOCALAPPDATA%\DayZ_MCP_shared` is refused.

## ROUND 3 FIXES

- F10: `shared_root()` treats a normal-tier `python -m unittest` process like the fast tier: a missing override uses a per-process temp directory, and the real `%LOCALAPPDATA%\DayZ_MCP_shared` is refused. `ProcessLifecycleTest` and `LifecycleFixture` also set `DAYZ_MCP_SHARED_ROOT` inside the fixture. `test_normal_tier_start_stays_inside_the_fixture` unsets `DAYZ_MCP_FAST_TESTS`, runs `start_run`, and checks that `box-admission.lock` is under the fixture and that `DayZ_MCP` / `DayZ_MCP_shared` were not created under `%LOCALAPPDATA%` or the home directory.
- F1: `_interpreter_target` is the invocation parser. A script is the first non-option argument; `-m` resolves the module from the process cwd and then a venv `Scripts` layout. `--keyfile` and other server values are not targets. The reviewer argv is `test_f1_keyfile_argument_does_not_accredit_the_package`.
- F2: `RootWriterLease` records an owner id. A second lease in the same process is admitted only for that same owner object. `build_server_state(..., activate_coordination=True)` passes the new `ServerState`. The reviewer call is `test_f2_independent_state_in_the_same_process_is_refused`.
- F6: `shared_build_lock` takes one lock per normalized path, sorted, released in reverse. `test_f6_overlapping_resources_share_a_lock` covers a shared target and a shared temp directory.
- F5: `build_run_request` puts `shared_lock_root` (and `instance_token` when a selector is already bound) in the hashed request. The worker validates that root and does not need `LOCALAPPDATA`. The profile token is read from the request. `launcher.cpp` no longer scans policy JSON with `ExtractJsonStringAfter`. `test_f5_worker_lock_root_comes_from_the_request` uses the minimal environment keys.
- F9: no compatibility shim. The default sealed bundle needs the coordinated reseal with PR #205 (c8c7). Default argv, paths, registration and receipts are unchanged. See the CHANGELOG line.
- F3: `parse_codex_registration`, `build_claude_target`, `build_codex_target` and `apply_host_timeouts` take the registration name. The default name is still omitted from the timeout call so existing default mocks stay valid. The shared journal path is unchanged.
- F4: `run_installer` binds the selector and rejects a conflicting environment before `install_runtime` writes. `run_runs_backup_gate` appends `--instance` and `--game-path` when they are set.
- F7: the PowerShell knowledge child receives `--instance` for a named install and does not pass `--sync`. `install_knowledge_pack` raises `global_skills_not_owned` if a non-default owner asks to sync.
- F8: `parse_args` rejects glued and duplicate selector flags before it parses, via `reject_glued_selector_flags`. The PowerShell script rejects `--`, a leading or trailing hyphen, and `default` before any later work.

## ROUND 4 FIXES

- F1: `_interpreter_target` records whether `-m` lookup is established. `-I` and `-P` (and any interpreter flag the parser does not recognize) leave it unestablished, and `accredited_package_dir` then refuses the cwd package. A `PYTHONSAFEPATH` value in another process cannot be read portably, so a writer that sets it without `-P` can still be matched to the cwd package. Regression: `test_f1_isolated_module_flag_does_not_accredit_cwd` (the reviewer's `-I -m dayz_mcp --daemon --keyfile ...` argv blocks migration of `130`).
- F7: `install_mcp.main` installs a named instance's knowledge pack with `sync=False` and `owner` set to the token. The default install still calls `install_knowledge_pack` without `owner`, so the existing default mock keeps its signature. Regression: `test_f7_named_python_install_does_not_sync_global_skills`.
- F8: `validate_entry_selector` is the shared check (case-sensitive token, glued and duplicate flags, absolute game path). `install_mcp.parse_args`, `p0s_gate.main` and `secure_launcher.main` call it before side effects, and they reject an inherited instance context that disagrees. `install-mcp.ps1` applies the same rules, rejects unconsumed selector arguments such as `-Instance=130` and `--instance=130`, and `-ValidateOnly` exits 0 or 1 before any install work. Regressions: one test per reviewer row, plus relative game path and inherited context; PowerShell runs this tree's script with `-ValidateOnly`.
- F10: `LifecycleFixture` always sets `DAYZ_MCP_SHARED_ROOT` to `<fixture>/shared-root`, saves the previous value, and restores it from `close()` and from constructor failure. Regressions: `test_f10_fixtures_keep_separate_shared_roots` and `test_f10_inherited_shared_root_is_replaced_and_restored`.

## ROUND 5 FIXES

- F8: `install_mcp._build_parser`, `p0s_gate._build_parser` (parent and `backup-runs-v1`), `secure_launcher._parser`, and both `server_cli` parsers are built with `allow_abbrev=False`. After parse, `selector_from_parsed` validates the stored `--instance` and `--game-path` and must match the argv scan; a disagreement is `duplicate_instance_flag` and nothing is installed, backed up, or launched. `install-mcp.ps1` matches the token with `[regex]::IsMatch(..., '\A[a-z0-9](?:[a-z0-9-]{0,31})?\z')` (no `IgnoreCase`, so a trailing LF fails) and accepts a game path only when it is a drive-rooted `X:\...` path or a UNC/device path starting with `\\`, which is `os.path.isabs` on this interpreter. Regressions, one per reviewer row: `test_f8_install_rejects_abbreviated_instance`, `test_f8_backup_rejects_abbreviated_instance`, `test_f8_secure_launcher_rejects_abbreviated_instance`, `test_f8_abbreviated_duplicate_does_not_select_the_long_flag`, `test_f8_powershell_rejects_drive_relative_game_path`, `test_f8_powershell_rejects_root_relative_game_path`, `test_f8_powershell_rejects_token_with_trailing_newline`. On the previous tree `--inst` was an abbreviation, so the installer returned a default token, the backup returned 0, and the launcher ran.
- F1: `_migration_disposition` returns `blocker` when the interpreter scan stops on an unrecognized short option such as `-z` and the argv still contains `-m dayz_mcp`, `--daemon`, or a `dayz_mcp` path component. Long options stay on the existing non-target path (`--help`, `--version`, `--unknown`), which `test_cpython_target_parser_handles_compact_long_and_terminal_options` already requires. Regression: `test_f1_unknown_interpreter_flag_keeps_daemon_evidence` (`python.exe -z -m dayz_mcp --daemon ...` is a blocker and `scan_dayz_mcp_processes` returns that pid). On the previous tree that argv was `absent` and the scan returned `()`.
- Pin update: `test_f8_glued_and_duplicate_selectors_are_rejected` now looks for `Contains('--')` because the token check no longer uses `-match '--'`. `tools/packaged-modules.lock.json` pins `server_cli.py` as `D905486AB3ABEC37C1E6F55743B1B579550691AAE044AB52C38AF7E422A9E4D2`. `tools/dependency-lock.json` and `tools/tests/test_dependency_lock.py` pin `secure_launcher.py` at size 11581 / sha256 `3BDF14502F02C5F8FBCDF5DD0EE8F6F1E4193E86CC6C7FF0A29DF0F2D516E492`.

## DEVIATIONS

- `loopback.prepare` still updates the UUID of a default-instance profile whose URL or key differ. That keeps `test_a_deployed_config_keeps_its_own_url_and_key`. A named instance refuses before the write.
- The writer lock remains byte 1 of `.daemon-startup.lock`.
- `tools/packaged-modules.lock.json` was regenerated. `tools/dependency-lock.json` and the pin in `tools/tests/test_dependency_lock.py` now hash the updated `secure_launcher.py`.
- Round 4 updated those same lock pins again (`server_cli.py` in the packaged lock, `secure_launcher.py` size 11034 / sha256 `5A5625197D50D1FDD43C996748C2A74B068DA114903FA8A98524EA28F630034B`). The launcher was not rebuilt.
- `PYTHONSAFEPATH` in a foreign process is not visible. A legacy `-m` writer that depends on it without passing `-P` can still be accredited from the cwd package.
- The Unreleased changelog line stays short so `CHANGELOG.md` remains inside the PROJECT-MAP 44 KB claim (46078 bytes).
- `tools/tests/test_install_mcp.py` no longer requires the PowerShell remove block to be the single unbranched `dayz-mcp` command. The default branch still contains that command. A named registration removes only its own name.
- `test_corrupt_manifest_arms_repair_fence_and_restart_does_not_mutate_it` releases each lifetime lease before the next `build_server_state` activation. A second state object is an independent writer. The corrupt-manifest assertions are unchanged.
- The sealed executable on disk was not rebuilt. PR #205 (c8c7) already changes pinned packaged modules, and this round changes more of them (`dayz_test_worker.py`, `dayz_test_request.py`, `server_cli.py`, `launcher.cpp`). The default bundle must be resealed in that same coordinated release. There is no shim that accepts the previous source pins. Default argv, paths, registration and receipts are unchanged.
- The private-worker environment no longer carries `DAYZ_MCP_INSTANCE` or `DAYZ_MCP_GAME_PATH`. The worker reads the instance token and the shared lock root from the hashed request. The game path stays on the runtime policy; the worker never read it from the environment.
- No live simultaneous DayZ launch was run.
- Round 5 does not treat an unrecognized long option (`--unknown`, `--help`, `--version`) as a migration blocker. `test_cpython_target_parser_handles_compact_long_and_terminal_options` requires those argv to scan as absent. The new blocker is an unrecognized short option (`-z`) that still carries daemon evidence.
- Round 5 updated the packaged-module pin for `server_cli.py` and the dependency-lock pin for `secure_launcher.py`. The sealed executable was not rebuilt. `CHANGELOG.md` stayed at 46078 bytes so the PROJECT-MAP 44 KB claim still rounds.

## NOT VERIFIED

- Live two-instance launch on the physical box.
- A rebuilt sealed launcher binary. The C++ source now copies the selector; the existing executable bytes were not relinked.
- Installer registration against live Claude and Codex CLIs.
