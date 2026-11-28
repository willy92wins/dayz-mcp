---
name: dayz-test-run
description: "Use when starting, reattaching, stopping, or closing a DayZ test run on the shared DayZ test box, managing session leases, or handling a busy box and FIFO queue."
---

# DayZ Test Run Protocol

Follow this sequence whenever you launch, adopt, stop, or hand off a DayZ test run on the shared box. Read references/box-queue.md before retrying a busy box or interpreting `queue_offer` refusals.

## Standard Cycle

1. Check `session_status` first to inspect `box.occupied`, active runs, `available_for`, and `blocked_on`.
2. Release any held lease with `session_release` before calling `dayz_test_run`; lease ownership during launch is internal to `dayz_test_run`.
3. Call `dayz_test_run(project, mode, ...)` with `on_busy="queue"` when the box may be busy.
   - `mode="server"` or `mode="all"` must omit `run_id`.
   - `mode="client"` requires `run_id` to reattach only the client without rebooting the server (unless `connect_host` is a non-loopback host).
   - `build=false` never builds even with `clean=true`; `pack_only=true` requires `build=true`.
   - `preflight=true` validates the request without taking the box; success is advisory and does not reserve the box.
4. Adopt the ownerless `RUNNING_IDLE` run with `session_acquire_wait` (this grants your lease and reports `adopted_run`; with several idle runs it reports `multiple_idle_runs` and adopts none).
5. Verify `bridge_status` reports `ready: true` before mutating tools. If not ready, read `reason`: `no_run`, `binding_not_ready`, `server_poll_stale`, or `version_mismatch`. For `mode="all"`, wait for a player with `wait_for(condition="players_at_least", value=1)`.
6. Keep the lease alive across long pauses (TTL is 120 s; heartbeat every 45 s with `session_heartbeat`). Mutating bridge calls, `dayz_test_run`, `dayz_test_stop`, `session_heartbeat`, and `wait_for(file_matches)` renew; pure reads and `session_status` do not.
7. Shut down cleanly when done:
   - For orderly teardown and valid RPT exit metrics, call `dayz_test_close` while still holding the lease. If the reply carries `stop_required=true`, release the lease and fall back to `dayz_test_stop`.
   - For forced termination (or cancelling a `STARTING` launch), call `session_release` if holding a lease, then `dayz_test_stop(run_id)`. `dayz_test_stop` kills processes directly (`exit_metrics_valid` is false).
8. Release the lease with `session_release` as soon as exclusive work ends. Releasing leaves a live run as `RUNNING_IDLE`; it never stops DayZ.
9. Run `session_status` before handoff and document any degraded close (`cleanup_degraded` or remaining runs).

## Hard Rules

- Acquire a lease before mutating the world or managing owned runs, and release it immediately when the exclusive sequence ends.
- Never kill DayZ processes directly from a shell; always use the lifecycle guard (`dayz_test_close` / `dayz_test_stop`).
- Never close or kill a foreign run, never delete `m1.espera`, and never ask the user whether to close someone else's run.
- A running retail client (`DayZ_x64.exe` / `DayZ_BE.exe`) blocks launches with `retail_quarantine`; close retail DayZ first.
- When the box is occupied, wait in the FIFO via `dayz_test_run(..., on_busy="queue")` instead of polling for gaps; follow the retry classification and 1800 s monotonic deadline in references/box-queue.md.
