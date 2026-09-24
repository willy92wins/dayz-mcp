# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Merged to `main` after [v1.2](https://github.com/willy92wins/dayz-mcp/releases/tag/v1.2) and not released yet. The deployed PBO matches `main` up to #43; the Enforce changes in #44 are not packed.

### Added

- `vehicle_trace` `mode=dump`: the bridge writes the canonical trace as JSONL in the profile folder (also on `stop`) instead of sending every sample over the wire; `MCP_BRIDGE_VERSION` stays `"10"` (#26).
- `vehicle_trace` samples carry `engine_rpm`, `rpm_idle`, `engine_ready` and `throttle_set`, and `classify_14de_throttle_sample` tells an engine-ready skip from setter lag; JSON `1`/`0` count as booleans (#36, #42).
- `dayz_test_run` results carry `caller_tool_registry_stale`; when it is true they also warn `tool_registry_stale_reopen_client` (#46).
- New runs record `daemon_generation_at_launch`; status, box and `dayz_test_stop` report `daemon_generation_current` and `generation_changed` (#47).
- `dayz_test_run` waits up to 30 s for an unlocked, non-black host desktop before launching a client; `session_locked` / `desktop_all_black` / `desktop_probe_timeout` / `desktop_probe_failed` abort the tandem so it does not burn runs only to return `frame_client_all_black`. Non-Windows `desktop_probe_unsupported` does not block (fb-20260918-134756-05a0, fb-20260918-134756-c0e5).

### Changed

- `dayz_test_run` refuses a closed Steam (no `steam.exe` running) with `error_code=steam_not_running` and a remediation that names the cause and the next step (start Steam and wait for its login, or retry with `auto_remediate_steam=true`), in validation, before any DayZ process starts. It used to be `steam_session_stale` with the generic `restart_steam_and_wait_for_active_process_match`. A running Steam with an invalid registration, and a host whose registry or process list cannot be read, still return `steam_session_stale`; the daemon-side gate keeps its codes (fb-20260924-011620-678b).
- `world_time_set` carries minute overflow into the hour and day, sets `ok:0` when a complete date echo mismatches, and returns `multiplier_applied=null` with `warnings=["multiplier_unconfirmed"]` because the engine exposes no multiplier getter (#35).
- `pipeline_feedback` publishes its title, body and project length limits in `inputSchema` (#32).
- `fixture_not_ready` from `vehicle_prepare_fixture` carries the observed fixture telemetry under `observed=` (#34).
- The `client_policy_untrusted_open_new_session` hint says when the client registered (#46).
- `capture_screenshot` returns `session_locked` on a locked Windows session instead of launching the grab and failing generically (#45).
- Enforce, not packed: automatic manual-gearbox upshifts wait a 0.3 s settle interval, and `vehicle_get_in_client` seats the client in the vehicle nearest to `pos` (#44).
- The `dayz_test_run` description states how long the call can block; the `session_release` description states that releasing never stops DayZ (#46, #47).

### Fixed

- Fresh-launch `bridge_status.ready` no longer reports `server_poll_stale` from a dead pre-launch peer's leftover poll ages; the verdict is `binding_not_ready` until this generation's first accredited poll, and `ready` publishes `stale_threshold_s` plus per-peer ages (fb-20260917-100411-5edf).
- `bridge_status` puts the `ready` object first in the payload; when `ready` is false that object names `next_step` (a public tool) before ages.
- User-facing fence and lease errors name only public MCP tools (`session_status`, `bridge_status`, `session_acquire_wait`) and carry `next_step=`; they no longer cite internal `lifecycle_status`.
- `session_release` of a token this client still held, answered `lease_invalid` after silent TTL, is `lease_expired` with `next_step=session_acquire_wait` (fb-20260917-100554-d0e0).
- `dayz_knowledge_find` / `show` no longer route to `dayz_knowledge_prepare` when `can_prepare` is false; the error is `knowledge_pack_missing` / `knowledge_pack_invalid` with the install command (fb-20260917-095637-8011).
- `dayz_knowledge_status` publishes `install_command` so a missing pack is installed without calling prepare.
- Untrusted clients keep H3 reads, `server_reload` and an owned `dayz_test_stop` when policy revalidation fails, including the stop of their own idle run; authority changes stay fail-closed (#30, #43).
- A bare `ClientRuntime` defaults to rejecting a stale policy (BUG-037) (#37).
- Native job cleanup no longer fails with `native_job_cleanup_incomplete:active_zero_wait_timed_out` when the second wait already ran and no handle remains open (#40, #41).
- `python -m dayz_mcp.doctor` parses supervised registrations and `--exec-audit-path` again, so it checks the daemon instead of reporting CONFIG_UNREADABLE. The audit path is now compared as part of the daemon policy (#48).
- `pack-addon.ps1` passes `-packonly` when the source has no `.p3d`/`.paa`/`.rvmat`, matching the test worker so AddonBuilder does not binarize against every `config.cpp` under `P:\` (fb-20260915-005408-bcd8).
- `capture_screenshot` launches the grab without an inherited `PSModulePath`, so a Git Bash-poisoned module path cannot hide PowerShell cmdlets (fb-20260915-011312-ba70).
- `vehicle_release` Abort autodumps a live trace before clearing it; stop the trace in a separate call, then release (fb-20260915-014739-7ad1).
- `player_teleport` refuses `occupant_client_seated` for a client-owned seated occupant so the server replica does not desync; one car per run, no get-out after `vehicle_get_in_client` (fb-20260915-014733-81f3).

### Security

- Strings from identities or persisted state that reach MCP payloads (the registration stamp, daemon generations, stop-envelope state and reason) are echoed only when they match a closed shape (#46, #47).

Test, documentation and proposal PRs (#24, #25, #27, #28, #29, #31, #33, #38, #39) are not listed.

## [1.2] - 2026-09-12

### Added

- 62 typed MCP tools across world, player, object, vehicle, camera, UI, telemetry, lifecycle, knowledge, and session coordination. Count pinned to the instantiated app (`tools/tests/test_install_mcp.py::PublicToolCountDocsTest`) and `README.md`. `exec_enforce` is extra and opt-in when an allowlist is configured.
- Engine-native, server-authoritative control and structured observation over a loopback HTTP push/pull bridge.
- Embedded, daemon, and client modes; one daemon can serve multiple agent sessions through FIFO leases.
- Managed lifecycle tools for building, launching, inspecting, and stopping DayZ test runs.
- Structured runtime diagnostics: field- and unit-aware `bad_args`, evidence-rich `wait_for` results, readiness causes from `bridge_status`, and the read-only `python -m dayz_mcp.doctor`.
- Vehicle entry, control, telemetry, and trace tools. Group G in-game drivability criteria in `product-spec.md` remain ❓; they are parked with the v1.2 pause, not an Unreleased promise.
- Camera capture, UI inspection and interaction, and live `.layout` reload tools.
- `dayz_mcp.effective_schema`: resolves the tool contract FastMCP publishes after `build_app`, aliases applied, and audits it against the prose each description promises.

### Changed

- `dayz_test_run` / `dayz_test_stop`: a failure inside the native launcher backend now names its bare code after the class, `dayz_test_failed:NativeLauncherBackendError:<code>`; host detail still stays in the local log (ficha ae65, step 1).
- `entities_query` keeps `entities: []` on an empty result: result pruning no longer drops the key its description promises (ficha 59d9).

### Security

- Loopback-only access with API-key authentication, process accreditation, FIFO leases, and a JSONL audit trail.
- No operating-system keystroke injection, OCR, paid-service dependency, usage analytics, or integrated knowledge database.
