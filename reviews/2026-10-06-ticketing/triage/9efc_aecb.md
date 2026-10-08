# Triage 9efc+aecb

Tickets: fb-20261002-013119-9efc, fb-20261002-135957-aecb

Title: Client died silently after camera_set; reattach refused (takeover_required / session_transition_conflict) / camera_set killed the client again (2 of 2), no pause or lease expiry this time; reattach still takeover_required

First pass by GLM-5.3-Flash-EXL3 on the GX10 (API direct, grep excerpts of origin/main 6671fd2, no whole-tree access). Raw model output below, unedited.

<!-- ticket 9efc+aecb | model GLM-5.3-Flash-EXL3 | finish stop | 334s | usage {"prompt_tokens": 38348, "completion_tokens": 3807, "total_tokens": 42155, "prompt_tokens_details": {"cached_tokens": 384}, "completion_tokens_details": {"reasoning_tokens": 2061}} | grep ['ActionOpenDoors', 'TICKET', 'daemon_request_deadline_exceeded', 'skip_clearance_check', 'players_at_least', 'settle_ticks', 'dayz_test_close', 'takeover_required', 'scene_raycast', 'cam_pos', 'run_not_owned', 'session_transition_conflict', 'player_teleport', 'client_not_polling'] -->

## VERDICT
NEEDS_REPRO — the Python `camera_set` tool shown only validates arguments and forwards them to the client peer, so the code that could actually terminate the client (the Enforce-side `camera_set` handler in `MCPClientBridge.c`) and the reattach/takeover logic in `dayz_test_tool.py` are not in the excerpts, and the defect can neither be confirmed as still present nor located.

## EVIDENCE
- `tools/dayz_mcp/server.py:5655-5702` — the `camera_set` tool: builds args per `cam_mode`, validates vectors/fov, then `runtime.call_bridge("camera_set", args, "client", ...)`. Nothing here can kill a process; no guard or documentation of dangerous inputs (ask 3 not addressed here).
- `tools/dayz_mcp/server.py:5675-5676` — `lookat` mode sends `cam_pos` + `look_at` verbatim; no bounds check on the coordinates (the ticket's `cam_pos=[13255.4,20,7148.3]` passes through unvalidated beyond "3 finite reals").
- `tools/dayz_mcp/loopback.py:94` — `camera_set` is in `CLIENT_COMMANDS`, i.e. routed to the client peer whose process died; the daemon-side ingress schema for it is at `loopback.py:1413-1419` (`_camera_variant`), which validates shapes, not world-safe values.
- `tools/dayz_mcp/accredited_daemon_transport.py:287` and `:313` — `daemon_request_deadline_exceeded` is a client-side `TimeoutError` raised when the caller's deadline lapses before/inside the request; it is a symptom of the client not answering, not a cause of its death.
- `tools/dayz_mcp/accredited_daemon_transport.py:44-54` — `_transport_error` maps that `TimeoutError` to the code `daemon_request_deadline_exceeded`, confirming the error the ticket saw is a transport timeout.
- `tools/dayz_mcp/bridge_errors.py:243` — `session_transition_conflict` is listed in `_CONTROL_CLIENT_ERROR_CODES`; its raise site is NOT SHOWN.
- `tools/dayz_mcp/bridge_errors.py:610-612` — `run_not_owned` carries `next_step=session_acquire_wait`, matching ticket 1's lease-expiry/re-acquire path (context only).
- `CHANGELOG.md:127` — "camera_set restores gameplay when applying the camera fails after the controls were suppressed (#97)": a prior failure-path fix exists, but nothing shown covers process death or dead-client detection.
- `CHANGELOG.md:35` — `input_trigger` refuses keys that would trip DayZDiag's Alt+F4 exit check ("would_request_exit"), showing the harness already knows DayZDiag can exit the process on certain inputs — precedent for a camera_set-style guard, but no such guard is shown for camera inputs.
- Decisive code NOT SHOWN: `addon/scripts/5_Mission/MCPClientBridge.c` `camera_set` job handler (the queued job `kind=camera_set` in the ticket's log), `addon/scripts/5_Mission/MCPJobRunner.c` (deadline/deadline_s handling), `tools/dayz_mcp/dayz_test_tool.py` `dayz_test_run` takeover/reattach path (`takeover_required`), and the `session_transition_conflict` raise site in `session_coordination.py`/`control_client.py`.

## ROOT CAUSE
Unknown from the excerpts. The client script log ends after the job was queued (`kind=camera_set deadline_s=97.3057`) with no crash log and no `.mdmp`, so the death happens inside the mod's camera job or the engine/DayZDiag after dequeue — none of that code was shown. The `takeover_required` / `session_transition_conflict` refusals on reattach are separate lifecycle behaviors whose raise sites were also not shown.

## PROPOSED FIX
Two options, smallest first; both touch files not shown, so they are directional:

1. **Recommended — guard + report (matches asks 1 and 3).**
   - `addon/scripts/5_Mission/MCPClientBridge.c`, `camera_set` handler: before creating/posing the camera, validate `cam_pos`/`look_at` against the world (e.g. finite, within terrain bounds, y above surface floor) and wrap the camera application so any failure posts an error result (`camera_apply_failed`) instead of leaving the job unanswered; on failure also restore gameplay (extending the #97 behavior at `CHANGELOG.md:127`).
   - `tools/dayz_mcp/dayz_test_tool.py` / `runtime_state.py` (NOT SHOWN): have `session_status` poll the client PID and report `client_exit` (`code`, `time`, `no_crash_dump: true`) instead of leaving the run half-alive.
   - Contract before/after: before — `camera_set` hangs to `daemon_request_deadline_exceeded`, client gone, `session_status` shows a live run with `client_not_polling`; after — `camera_set` returns `{ok:false, error:"camera_apply_failed", ...}` and the client survives, or if the client still dies, `session_status` reports `client_exit` so the caller stops the run deliberately.

2. **Reattach without transition conflict (ask 2).** In `dayz_test_run`'s takeover path (NOT SHOWN), when `run_id` names a run owned by the calling session's lease and only the client peer is dead (server still polling), allow `mode=client` to relaunch the client into the same run instead of `takeover_required`/`session_transition_conflict`. Larger change; touches lease/fence invariants.

## TEST
In-game check (fails before, passes after fix 1): launch `mode=all` with `extra_mods=[@SecretRock_RH, @SecretRock_RHTest]`, acquire the lease, then call `camera_set(cam_mode="lookat", cam_pos=[13255.4,20,7148.3], look_at=[13252.8,19.9,7148], settle_ticks=10)`. Before the fix the client PID disappears and the call ends in `daemon_request_deadline_exceeded`; after the fix the call returns a structured error (or ok) and the client PID is still alive with `bridge_status` ready. A pure unit test is not possible for the death itself; a unit test for fix 2 (reattach accepted for own run with dead client peer) would need the `dayz_test_run` takeover function, NOT SHOWN.

## SIZE / RISK
M. Main risks: (a) the mod-side guard requires a rebuilt/redeployed PBO, so it must stay compatible with the bridge version/arg-contract hash handshake (`CHANGELOG.md:77`); (b) the reattach change (option 2) interacts with lease, fence and box-occupancy state and could open a race if another session claims the box between client death and reattach (exactly what the ticket's follow-up hit).

## NOT VERIFIED
- The `camera_set` job handler in `addon/scripts/5_Mission/MCPClientBridge.c` and the job runner's deadline handling in `MCPJobRunner.c` — whether any input or code path there can terminate the process.
- The `dayz_test_run` takeover/reattach logic and the `session_transition_conflict` raise site — whether re-attaching a client to one's own live run is already supported or refused by design.
- Whether `session_status` already reports client exit code/time somewhere (ask 1 may be stale).
- Whether the crash reproduces without `@SecretRock_RH` loaded (the filer explicitly did not test this), and whether the client exit is a DayZDiag-engine behavior rather than a mod defect.
- The actual range enforced by `_integer_in_range()` for `settle_ticks` (`loopback.py:647`) — whether `settle_ticks=10` is in range.