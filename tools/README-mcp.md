# DayZ-MCP stdio wrapper

## Requirements

- Python 3.14 on the host. The fallback documented by the phase plan is `py -3.10`.
- Bridge tools require a running DayZ instance. Managed test instances can be queued and controlled directly with `dayz_test_run` and `dayz_test_stop`; existing external harnesses remain compatible.
- The bridge still authenticates with an API key in the query string because DayZ `RestContext.SetHeader()` cannot send arbitrary headers. Bind remains `127.0.0.1`.

## Install

From `tools/`:

```text
python install_mcp.py --pin-clis
python install_mcp.py
```

`--pin-clis` records native x64 `claude.exe` / `codex.exe` under `%LOCALAPPDATA%\DayZ_MCP\security\` (override with `DAYZ_MCP_SECURITY_DIR`). It is required before `python install_mcp.py --register`. The non-Python installer registration path does not need that pin.

The installer creates `.venv-mcp`, installs `mcp==1.27.2`, generates `.dayz_mcp.key` if missing, writes sample `dayz_mcp.json` files under `_mcp_config`, and prints both registration commands:

- Claude: `--client --client-platform claude`
- Codex: `--client --client-platform codex`

It mutates the Claude and Codex MCP registrations only when run with `--register`. Registration is remove-then-add and verifies both effective configurations use client mode, the expected platform, the same port/keyfile, and no `--embedded` flag.

To seed real DayZ profile/mission config, pass the directories explicitly:

```text
python install_mcp.py --server-profiles "C:\path\server_profiles" --client-profiles "C:\path\client_profiles" --mission-path "C:\path\mpmissions\dayzOffline.chernarusplus"
```

## Startup and health

1. Start a managed instance with `dayz_test_run`, or use an existing phase harness/local DayZ workflow with the same key/config.
2. Start a registered Claude/Codex MCP session. Registered sessions always use `--client`; the shared daemon owns the loopback port and starts lazily.
3. Call `bridge_status`, then run the read-only doctor when validating the host:

```text
.\.venv-mcp\Scripts\python.exe -m dayz_mcp.doctor --daemon-policy normal --json
.\.venv-mcp\Scripts\python.exe -m dayz_mcp.doctor --daemon-policy normal --json --require-clean
```

Doctor exit codes are `0` for clean or warning-only output, `1` for findings with severity `FAIL`, and `2` when no stable diagnosis can be produced. Externally opened retail DayZ is `RETAIL_MANUAL_CLOSE_REQUIRED`: warning/exit 0 normally and fail/exit 1 with `--require-clean`. Close retail through its UI and rerun doctor; the doctor never stops, adopts, reclaims, or assigns ownership to a process.

There is no embedded fallback for interactive agent sessions. If client/daemon discovery fails, diagnose it; do not start another bare or `--embedded` server on the shared port. Explicit embedded mode exists only for CI/backcompat harnesses:

```text
.\.venv-mcp\Scripts\python.exe -m dayz_mcp --embedded --keyfile .\.dayz_mcp.key --port 8765
```

## Session protocol

Mutating work uses the request-bound high-level queue by default:

1. `session_status()` first: `owner`/`queue`/`claimable` describe the **lease**. `box` describes the **game box** (managed `runs`, unmanaged `foreign` DayZDiag, `ports_in_use`, and the box wait FIFO). A free lease does not mean a free box.
2. `session_acquire_wait(purpose, max_wait_s)` remains queued until it returns an active lease or fails. It never returns `queued`.
3. Run only the required mutating operations. The lease/ticket TTL is 120 seconds; heartbeat is for active exclusive work only.
4. `session_release(lease_token)` as soon as exclusive work ends.
5. `session_status()` before handoff; confirm the caller has no lease/ticket and no pending commands. Report any degraded cleanup.

`session_acquire` and `session_wait` remain low-level compatibility tools. A caller that uses them owns its loop and must call `session_cancel(ticket)` when abandoning a queued ticket. The high-level call installs an operation tombstone on timeout, cancellation or transport failure, including when its first HTTP request completes late. The long wait is tied to the live MCP request/host; it is not a durable job and does not resume after host restart.

`install_mcp.py --pin-clis` then `install_mcp.py --register` also applies a seven-day host-side wait budget atomically to both user configs: Claude `timeout = 604800000` ms and Codex `tool_timeout_sec = 604800`. Queue cancellation remains explicit; this budget prevents the host from cutting off a healthy FIFO wait during normal long-running work. The writer holds both Windows files with `share=0`, verifies writes through the same handles and uses a restricted recovery journal; a busy or conflicting config fails closed without forcing a host shutdown. Existing host processes must be restarted or a new session opened before the new timeout is effective.

Read-only tools do not require a lease. Never use another session's token or terminate a process to advance the FIFO queue.

Requires a lease (these mutate the game): `world_spawn`, `object_delete`, `player_teleport`, `inventory_give`, `notify_players`, `vehicle_enter`, `vehicle_control`, `vehicle_release`, `vehicle_prepare_fixture`, `camera_set`, `world_time_set`, `world_weather_set`, `engine_set`, `object_anim` (write), `restore_gameplay`, `dayz_test_run`, `dayz_test_stop`. The last two acquire and release the lease internally.

No lease (read-only): `query_all_players`, `query_player_state`, `entities_query`, `surface_query`, `scene_raycast`, `object_inspect`, `telemetry_read`, `vehicle_telemetry`, `camera_get`, `logs_since`, `ui_tree`, `query_get_in_condition`, `bridge_status`, `session_status`, `capture_screenshot`, `pipeline_*`.

A mutation without a lease returns `lease_required`. Call `session_acquire_wait` first.

## Managed lifecycle and administration

Use `dayz_test_run(project, mode, ...)` for normal MCP launches. The call remains open while it waits in the FIFO and while the test runs; it acquires, heartbeats and releases the lease internally. Projects and paths come only from the sealed `dayz-test-v1` policy. Use `dayz_test_stop(run_id)` to queue adoption and shutdown of that exact managed run. Neither tool accepts or returns a lease token, executable, PID, argv or arbitrary path.

The box is a second resource. `wait_for_box_s=0` (default) fails immediately with `active_run_exists` if the box is occupied. `wait_for_box_s=<n>` waits up to n seconds (max 600, same order as `wait_for`), FIFO among waiters, without holding `tool_lock` and without requiring a lease heartbeat. A claimed head uses a 600 s claim TTL, not the 120 s waiter TTL; `dayz_test_run` heartbeats that claim until the launch returns, so a build longer than 120 s does not hand the box to the next waiter. While a session holds that claim, `dayz_test_run` from any other session is rejected (`active_run_exists`) even if no run is registered yet. On timeout the same `active_run_exists` is returned, enriched in the MCP tool (not on the sealed lifecycle wire) with `occupied_by_run_id`/`mod`/`label`/`age_s` when the occupant is a managed run, or `foreign=true` plus `port`/mods when it is not. The failed result keeps `run_id` null; the occupant's id is `occupied_by_run_id`. The stop recipe is only emitted when the caller owns the run or it is `RUNNING_IDLE`; otherwise retry with `wait_for_box_s=<n>` (and never terminate a foreign process). `box_queue_saturated` means the box wait FIFO is full — retry with `wait_for_box_s=<n>` after a waiter leaves.

Pick a free game port from `session_status.box.ports_in_use` and pass a different `port=` to `dayz_test_run`. There is no reservation registry.

The stable progress stages are `validating`, `queued`, `executing` and `finalizing`. A successful non-preflight run returns its `run_id`; preserve that identifier for the later stop call. `artifacts_paths` contains only approved profile directories.

`python -m dayz_mcp.lifecycle_cli` reads the caller's own identity and lease only from `DAYZ_MCP_CLIENT_ID_JSON` and `DAYZ_MCP_LEASE_TOKEN`; it reads the API key from `--keyfile`. Do not place any of those values in documentation, command history, or handoff text.

`python -m dayz_mcp.secure_launcher dayz-test-v1` remains the lower-level managed CLI route. It accepts only the launcher id in argv and one public request-v1 JSON document on stdin. It waits in the FIFO without a local deadline by default; `--max-wait-s` is an explicit cancellation budget. The launcher acquires the lease itself and injects identity/token only into the sealed lifecycle child environment, so the invoking shell never needs either value. Retail remains manual-only and Server remains gated by its probe.

Do not copy a token from an MCP result into a shell, argv, request file, log, or handoff. The registry entry is installed only after the native bundle, provenance closure, deny-launch suite and adversarial review gates pass.

## Native launcher host policy

The builder reads a host-only intent file. It never loads the published example.

1. Copy `tools/launcher-policy.example.json` to `%LOCALAPPDATA%\DayZ_MCP\launcher-policy.json` (create the directory if needed).
2. Replace `ExampleMod` and every path with trees that exist on this machine.
3. Set `allow_root_junction` to `true` only on a leaf that is a mount-point junction. Otherwise the builder raises `required_junction_missing`.
4. Each mission alias must live under one of that project's `mission_roots`.
5. `mods_root` must repeat one of the `mod_roots` paths.
6. On a machine other than the one that produced `tools/dependency-lock.json`,
   re-pin the toolchain section first: `python relock_toolchain.py` discovers the
   local MSVC and Windows SDK and rewrites only that section; the shipped
   artifact pins stay untouched.
7. Run the builder. A missing path stops the build; create the directory or fix the JSON.
8. Seed and install the registry:
   `python -m dayz_mcp.launcher_registry_update bootstrap`, then
   `python -m dayz_mcp.launcher_registry_update install-dayz-test-v1 --expected-sha256 <sha printed by bootstrap>`.

Locate the file with `--policy PATH`, then `DAYZ_MCP_LAUNCHER_POLICY`, then `%LOCALAPPDATA%\DayZ_MCP\launcher-policy.json`. An empty env value is an error, not a fallback. The published example is never selected.

Exceptional `python -m dayz_mcp.admin_cli` release/reconcile operations require a real interactive TTY, a non-empty reason, and exact typed confirmation. They are not MCP tools and are not a normal queue-bypass mechanism.

## Cookbook

Short call sequences for a cold consumer. Use `playbook_run(name, params)` for a named checklist; CLI runner usage is in [`playbooks/README.md`](../playbooks/README.md).

### Wait for a player

1. `session_status()` — confirm the lease is claimable (no blocking owner) and read `box` (occupied runs/foreign/ports) before launching.
2. `bridge_status()` — `version_state` is ok and `server_peer.last_poll_age_s` is small. The first call of a session may return `daemon_unavailable`; retry once. Box occupancy lives on `session_status`, not on the cheap `/status` health probe.
3. `wait_for(condition="log_matches", pattern="OnStoreLoad SUCCESS")` — player finished `OnStoreLoad`. Other Diag 1.29 needles that do appear: `"Create entity type 'SurvivorM_"` and `config loaded`. Never wait for `connected to server`; that string is not written.
4. `wait_for(condition="players_at_least", value=1)` — at least one connected player.
5. `query_all_players()` — `{ok: 1, players: [{uid, pos: [x, y, z], health (0..1), in_vehicle}]}`. An empty `players` list is success with zero players, not an error. `uid` is SteamID64 (`PlayerIdentity.GetPlainId()`).

`wait_for` on timeout still returns `ok: true` with `satisfied: false` and `timed_out: true`. Gate on `satisfied`, not `ok`. `timeout_s` is capped at 600. With the game off, the first probe aborts with `version_blocked` or `daemon_unavailable`.

`logs_since(marker=None, max_lines=200)` reads the active run's `script_*.log` and `.RPT` (server and client) and returns a `marker` for the next call. No lease. Player chat is not exposed: `wait_for` `log_matches` and `logs_since` read script/RPT only, where chat does not appear. With `-adminlog` the server writes a profiles `.ADM` file (`Chat("Name"(id=<hash>)): text`, plus Connect/Disconnect); no tool reads it — inspect `.ADM` by hand.

### Spawn safely

1. `session_acquire(purpose="spawn")` — keep `lease_token`; call `session_heartbeat(lease_token)` before the 120 s TTL. If the queue is held, use `session_acquire_wait(purpose="spawn", max_wait_s=...)` instead.
2. `playbook_run(name="place_safely", params={"x":..,"z":..})` ([`playbooks/place_safely.toml`](../playbooks/place_safely.toml); runner: [`playbooks/README.md`](../playbooks/README.md)). The playbook is DRAFT and `canopy_dy` is `uncalibrated`: an S2 canopy FAIL is downgraded to WARN (`uncalibrated_gate_downgraded`) and the verdict is `PASS_WITH_WARNINGS`. That does not certify a clear canopy or roof. Do not spawn unless S2 is a clean PASS. S4 checks only the vertical column (`entities_query` radius 5; WARN if `count_total > 25`) and players inside `clear_r`; it does not detect lateral solids. Manual substitute: `surface_query(x, z)`, then `scene_raycast` down the column, then `entities_query` plus `query_all_players`.
3. `world_spawn(type="CivilianSedan", pos=[x, surface_y, z], flags=0)` after a clean PASS, with `surface_y` from `surface_query`. `flags=0` uses `ECE_PLACE_ON_SURFACE`; `y=0` in `pos` means on the ground. Verified: `type="CivilianSedan"`, `pos=[7086, 0, 7726]` → `pos_real` y=`297.03`.
4. Living infected: `world_spawn(type="ZmbM_CitizenASkinny_Blue", pos=[x, surface_y, z], flags=3108)` (`ECE_PLACE_ON_SURFACE|ECE_INITAI|ECE_CREATEPHYSICS`). Without `ECE_INITAI` the infected has no AI.
5. `object_delete(object_id)` on the returned `object_id` when finished.
6. `session_release(lease_token)`.

### Report friction or a bug

These mailbox tools work with no game and no daemon.

1. `pipeline_inbox(limit=20)` — see whether the item is already filed. Optional `kind` filter; `include_resolved=false` by default.
2. `pipeline_feedback(kind="bug", title="...", body="...", project="")` — `kind` is `bug`, `request`, `finding`, or `tool_contribution`. `title` ≤ 120 characters; `body` ≤ 8000. Body template: `tool / args / error / repro`. Returns `id` `fb-YYYYMMDD-HHMMSS-hex4`. Over-length or an invalid `kind` fails with `bad_args` (the field is not named).
3. `pipeline_resolve(feedback_id, resolution)` to triage (append-only; nothing is deleted).

## Troubleshooting

- `bridge_status.server_peer.last_poll_age_s = null`: the server-side bridge has not polled; check server profiles and mission config.
- `bridge_status.client_peer.last_poll_age_s = null`: the client-side bridge has not polled; check `client_profiles\dayz_mcp.json`.
- `legacy_blocked`: `--require-version` is on while running the 4A bridge. Keep it off until the 4B PBO sends `ver=`.
- `version_mismatch`: bridge or game version does not match the MCP server config.
- `version_blocked` with the game off means no DayZ peers, not a protocol mismatch.
- `lease_required`: call `session_acquire_wait` before the mutating tool.
- Timeout errors include peer liveness so you can distinguish a quiet peer from a command-level failure.
- `CONFIG_EMBEDDED`/`CONFIG_MISMATCH`: repair both registrations with `python install_mcp.py --pin-clis` then `python install_mcp.py --register`; do not fall back to embedded.
- `PROCESS_UNREGISTERED`, `RUN_STALE`, or `RUN_IDENTITY_MISMATCH`: preserve the process and manifest for explicit lifecycle/admin review; doctor performs no cleanup.
- `PROCESS_SCAN_FAILED`: the process snapshot is unknown, so the result is fail-closed rather than clean.
- `PROCESS_SCAN_DECODE_FAILED`: process-scan output could not be decoded; fail-closed and distinct from a missing process.
- `RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED`: all ten `runs.json.bak-preprune*` slots are taken, so the load-time prune refuses to run and the manifest keeps growing. Retire the stale backups to restore pruning; the doctor still performs no cleanup.

`telemetry_read` is exposed as-is. Known residual backlog remains BUG-010/011/012: fixture line caps, radius/Inf hardening, and JSON-lines schema validation.
