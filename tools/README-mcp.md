# DayZ-MCP stdio wrapper

## Requirements

- Python 3.14 on the host. The fallback documented by the phase plan is `py -3.10`.
- Bridge tools require a running DayZ instance. Managed test instances can be queued and controlled directly with `dayz_test_run` and `dayz_test_stop`; existing external harnesses remain compatible.
- The bridge still authenticates with an API key in the query string because DayZ `RestContext.SetHeader()` cannot send arbitrary headers. Bind remains `127.0.0.1`.

## Install

From `tools/`:

```text
python install_mcp.py
```

The installer creates `.venv-mcp`, installs `mcp==1.27.2`, generates `.dayz_mcp.key` if missing, writes sample `dayz_mcp.json` files under `_mcp_config`, and prints both registration commands:

- Claude: `--client --client-platform claude`
- Codex: `--client --client-platform codex`

It mutates the Claude and Codex MCP registrations only when run with `-Register`. Registration is remove-then-add and verifies both effective configurations use client mode, the expected platform, the same port/keyfile, and no `--embedded` flag.

To seed real DayZ profile/mission config, pass the directories explicitly:

```text
python install_mcp.py --server-profiles "C:\path\server_profiles" --client-profiles "C:\path\client_profiles" --mission-path "C:\path\mpmissions\dayzOffline.chernarusplus"
```

## Startup and health

1. Start a managed instance with `dayz_test_run`, or use an existing phase harness/local DayZ workflow with the same key/config.
2. Start a registered Claude/Codex MCP session. Registered sessions always use `--client`; the shared daemon owns the loopback port and starts lazily.
3. Call `bridge_status`, then run the read-only doctor when validating the host:

```text
.\.venv-mcp\Scripts\python.exe -m dayz_mcp.doctor --json
.\.venv-mcp\Scripts\python.exe -m dayz_mcp.doctor --json --require-clean
```

Doctor exit codes are `0` for clean or warning-only output, `1` for findings with severity `FAIL`, and `2` when no stable diagnosis can be produced. Externally opened retail DayZ is `RETAIL_MANUAL_CLOSE_REQUIRED`: warning/exit 0 normally and fail/exit 1 with `--require-clean`. Close retail through its UI and rerun doctor; the doctor never stops, adopts, reclaims, or assigns ownership to a process.

There is no embedded fallback for interactive agent sessions. If client/daemon discovery fails, diagnose it; do not start another bare or `--embedded` server on the shared port. Explicit embedded mode exists only for CI/backcompat harnesses:

```text
.\.venv-mcp\Scripts\python.exe -m dayz_mcp --embedded --keyfile .\.dayz_mcp.key --port 8765
```

## Session protocol

Mutating work uses the request-bound high-level queue by default:

1. `session_acquire_wait(purpose, max_wait_s)` remains queued until it returns an active lease or fails. It never returns `queued`.
2. Run only the required mutating operations. The lease/ticket TTL is 120 seconds; heartbeat is for active exclusive work only.
3. `session_release(lease_token)` as soon as exclusive work ends.
4. `session_status()` before handoff; confirm the caller has no lease/ticket and no pending commands. Report any degraded cleanup.

`session_acquire` and `session_wait` remain low-level compatibility tools. A caller that uses them owns its loop and must call `session_cancel(ticket)` when abandoning a queued ticket. The high-level call installs an operation tombstone on timeout, cancellation or transport failure, including when its first HTTP request completes late. The long wait is tied to the live MCP request/host; it is not a durable job and does not resume after host restart.

`install_mcp.py --register` also applies a seven-day host-side wait budget atomically to both user configs: Claude `timeout = 604800000` ms and Codex `tool_timeout_sec = 604800`. Queue cancellation remains explicit; this budget prevents the host from cutting off a healthy FIFO wait during normal long-running work. The writer holds both Windows files with `share=0`, verifies writes through the same handles and uses a restricted recovery journal; a busy or conflicting config fails closed without forcing a host shutdown. Existing host processes must be restarted or a new session opened before the new timeout is effective.

Read-only tools do not require a lease. Never use another session's token or terminate a process to advance the FIFO queue.

## Managed lifecycle and administration

Use `dayz_test_run(project, mode, ...)` for normal MCP launches. The call remains open while it waits in the FIFO and while the test runs; it acquires, heartbeats and releases the lease internally. Projects and paths come only from the sealed `dayz-test-v1` policy. Use `dayz_test_stop(run_id)` to queue adoption and shutdown of that exact managed run. Neither tool accepts or returns a lease token, executable, PID, argv or arbitrary path.

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
6. Run the builder. A missing path stops the build; create the directory or fix the JSON.
7. Install the bundle in `approved-launchers.json` as today.

Locate the file with `--policy PATH`, then `DAYZ_MCP_LAUNCHER_POLICY`, then `%LOCALAPPDATA%\DayZ_MCP\launcher-policy.json`. An empty env value is an error, not a fallback. The published example is never selected.

Exceptional `python -m dayz_mcp.admin_cli` release/reconcile operations require a real interactive TTY, a non-empty reason, and exact typed confirmation. They are not MCP tools and are not a normal queue-bypass mechanism.

## Troubleshooting

- `bridge_status.server_peer.last_poll_age_s = null`: the server-side bridge has not polled; check server profiles and mission config.
- `bridge_status.client_peer.last_poll_age_s = null`: the client-side bridge has not polled; check `client_profiles\dayz_mcp.json`.
- `legacy_blocked`: `--require-version` is on while running the 4A bridge. Keep it off until the 4B PBO sends `ver=`.
- `version_mismatch`: bridge or game version does not match the MCP server config.
- Timeout errors include peer liveness so you can distinguish a quiet peer from a command-level failure.
- `CONFIG_EMBEDDED`/`CONFIG_MISMATCH`: repair both registrations with `python install_mcp.py --register`; do not fall back to embedded.
- `PROCESS_UNREGISTERED`, `RUN_STALE`, or `RUN_IDENTITY_MISMATCH`: preserve the process and manifest for explicit lifecycle/admin review; doctor performs no cleanup.
- `PROCESS_SCAN_FAILED`: the process snapshot is unknown, so the result is fail-closed rather than clean.
- `RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED`: all ten `runs.json.bak-preprune*` slots are taken, so the load-time prune refuses to run and the manifest keeps growing. Retire the stale backups to restore pruning; the doctor still performs no cleanup.

`telemetry_read` is exposed as-is. Known residual backlog remains BUG-010/011/012: fixture line caps, radius/Inf hardening, and JSON-lines schema validation.
