# Architecture decisions: the invariants

The rules below hold DayZ-MCP together. Each one has a reason, a place in the code that enforces it, and the ID of the decision that set it (`D-NN`). The full decision log, with the day-to-day operational decisions, is the maintainer's and is not in this repository.

If you are about to change one of these rules, you are changing the design, not fixing a bug. Say so in the pull request.

[`dayz-mcp-architecture.md`](dayz-mcp-architecture.md) is the original design, kept as history. Where it disagrees with this page, this page is current.

## Authority and transport

1. **The server is the authority by default.** Unless a verb is routed to the client, world state is read and written on the server peer, in `MissionServer`.
   - The client peer does what only the player's own client can do, and reads what only it sees:
     - the camera, the UI and input (`camera_*`, `ui_*`, `key_press`, `restore_gameplay`);
     - player actions, started the way the player would start them (`action_use`, `action_use_target`, `player_respawn`);
     - the owner's side of a vehicle: get-in, engine, `vehicle_control` and its release, plus the owner-side readings `vehicle_telemetry` and `vehicle_trace`.
   - Driving is the one piece of world state that moves from the client. A car under `NetworkMoveStrategy.PHYSICS` only moves from its owning client, so its position and speed are read there too.
   - *Why:* engine-native reads and writes cannot be spoofed by the agent. `ActionStartEngine` returns early on the server under PHYSICS; this is the B3 probe in the README.
   - *Where:* `SERVER_COMMANDS` and `CLIENT_COMMANDS` in `tools/dayz_mcp/loopback.py`, which route each verb to its peer.
   - *Decisions:* D-01, D-06 and D-12.

2. **The mod pulls; Python is a passive endpoint.**
   - The bridge polls the daemon over HTTP from `MCPBridge.OnTick`, which `MissionServer.OnUpdate` drives, with one poll in flight (`m_PollInFlight`). It posts results back.
   - The key travels in the query string because `RestContext.SetHeader` only sets `Content-Type`. So the endpoint binds to 127.0.0.1 and never logs URLs.
   - *Where:* `addon/scripts/5_Mission/MCPBridge.c`, `MCPClientBridge.c` and `tools/dayz_mcp/loopback.py`.
   - *Decisions:* D-01, D-02 and D-12 (the client peer has its own `RestContext`).

3. **One daemon owns the port; every agent session is a client.**
   - A standalone daemon talks to the game. Sessions start in `--client` mode and spawn it lazily if it is missing.
   - A daemon is reclaimed by health, not by parentage. `--embedded` is a fallback.
   - *Decisions:* D-14.

4. **Every request is accredited against the socket's owner.**
   - The registrations of both hosts must agree on port, keyfile, launch, argv, cwd and auto-spawn.
   - A client verifies that the process on the other end of the socket is the daemon it expects before it sends anything secret.
   - *Where:* `tools/dayz_mcp/host_config.py`, `accredited_daemon_transport.py` and `pinned_keyfile.py`.
   - *Decisions:* D-21 and D-30 (the live credential can be reloaded, but not repointed).

## Coordination

5. **One box, one lease.**
   - Reads need no lease.
   - Mutations and lifecycle calls need the `dayz-box` lease. It has a 120 s TTL that every heartbeat and every command authorized under it renew.
   - The queue is FIFO with one exception. A holder whose lease expired while its run was still attached gets a 90 s grace (`LEASE_GRACE_S`) to take it back ahead of the queue. Strangers are held off meanwhile, and with a queue waiting it can do so only once in a row (`MAX_PREF_RENEWALS`).
   - Cleanup is scoped to the owner and never kills DayZ or the daemon. This sentence is lease cleanup.
   - The one exception is the idle warden (`tools/dayz_mcp/idle_warden.py`). It ships off. Only `%LOCALAPPDATA%\DayZ_MCP\idle-warden.json` as the JSON object `{"enabled": true}` turns it on; a missing, unreadable or any other file leaves it off. Turning it off before `WM_CLOSE` releases what it holds and closes nothing. After `WM_CLOSE` has been sent, turning it off does not undo that close, and it prevents the fallback stop. When on, it takes the `dayz-box` lease through the same queue and may close one abandoned run: ownerless `RUNNING_IDLE`, no human input for `RUN_IDLE_CUT_S` (600 s), `use_state` `abandoned`, and the only active run, with a known scan and no retail quarantine. The run is a candidate from that cut. The warning is sent only after this warden holds the lease, so a queue ahead of it delays the warning. A live client is then closed no earlier than `COUNTDOWN_S` (60 s) after the last confirmed `notify_players` (`WARNING_SHOW_S` 60), and only with no reaction in that wait. The close is `WM_CLOSE` first (`close_run`). It then waits up to `EXIT_WAIT_S` (45 s) for every launched role to leave the process-guard snapshot and reaps; a `run_not_reapable` whose next read is known and empty is one more reap, still inside that budget. If a launched role is still alive, the fallback stop happens only after a second revalidation (scan, retail quarantine, generation, full identity of each survivor, and the lease). When that check passes, a lifecycle-guard stop by identity follows (`stop_run`). When it does not, the result is `failed` and there is no stop. A confirmed-dead client (`client_gone`) skips the warning. It never closes a run with an agent owner, a human playing, an unknown scan or retail quarantine.
   - *Where:* `tools/dayz_mcp/session_coordination.py` for the lease; `tools/dayz_mcp/idle_warden.py` for the exception. Use state is `_use_projection` in `tools/dayz_mcp/process_lifecycle.py`; the input signal is `tools/dayz_mcp/input_activity.py`.
   - *Decisions:*
     - D-15;
     - D-19: only a live `session_wait` can claim the head of the queue;
     - D-20: a token is published only after its write-ahead log entry is durable;
     - D-81: activity that resets the 10-minute cut is a leased agent, or keyboard or mouse while a window of the run is in front. Warn at 10:00 and close at 11:00. Those are the owner's words, and they are the measured outcome when the lease queue is empty (pass B round 1), not a deadline. The run is a candidate from 600 s; the warning follows the lease; the close is no earlier than 60 s after the last confirmed warning. With no way to warn, a live client is not cut. While someone is playing, only the session that launched the run may adopt it or stop it.
     - D-82: before that cut, only the launching session may adopt an ownerless `RUNNING_IDLE` run. An ownerless `UNRECONCILED` run has no `use_state` and keeps its recovery adoption, so another session may adopt it. Once `use_state` is `abandoned`, any session may adopt that `RUNNING_IDLE` run, or the warden may close it.

6. **Only the sealed native launcher starts DayZ.**
   - `dayz_test_run`, `dayz_test_stop` and `dayz_test_close` (the graceful close) are the agent's lifecycle interface.
   - The launcher embeds CPython pinned by bytes and SHA-256, takes its request on stdin and keeps the lease token internal.
   - Its policy is external, so it can be published.
   - *Where:*
     - `tools/native-launchers/dayz-test-v1/`;
     - `tools/build_native_launcher.py`;
     - `tools/packaged-modules.lock.json`, which pins the Python modules sealed inside it.
   - *Consequence:* changing one of `PACKAGED_MODULES` means rebuilding and re-registering the launcher before the live tree moves past that change.
   - *Decisions:* D-23, D-25, D-27, D-28 and D-35.

7. **Lifecycle fails closed.**
   - Retail DayZ is manual-only.
   - An external retail process, or a process snapshot that cannot be read, quarantines mutations instead of guessing.
   - Runs whose processes are all confirmed dead are reaped to `EXITED`.
   - *Where:* `tools/dayz_mcp/process_lifecycle.py`.
   - *Decisions:* D-16 and D-18.

## Truthfulness

8. **A request whose outcome is ambiguous is not sent again.**
   - If the client cannot tell whether the daemon received a request, it reports `daemon_response_ambiguous` instead of resending it. Repeating a spawn or a lifecycle call can leave the world wrong.
   - The one deliberate resend is authentication. After a 401 from an accredited daemon, the client reloads the credential and sends the request again, because the daemon refused it before running it.
   - *Where:*
     - `ClientRuntime._call` in `tools/dayz_mcp/server.py`;
     - the 401 path in `tools/dayz_mcp/daemon_credential.py`.

9. **Sent is not verified, and missing is not zero.** A tool reports what it observed.
   - `wait_for(entity_state)` never reads absent data as `false` or `0`.
   - `world_time_set` returns `multiplier_unconfirmed`, because the engine has no getter for the multiplier.
   - *Where:* `_entity_wait_observation` and `world_time_set` in `tools/dayz_mcp/server.py`.
   - *Decisions:* D-68.

## Contracts

10. **The Python side checks the bridge's protocol, and the PBO ships with the Python that expects it.**
    - The bridge announces its version, its command census and an argument-contract hash (`ach`). Today the hash covers one command's keys, the shape of `vehicle_prepare_fixture` in `SERVER_ARG_CONTRACT`.
    - On the server peer, a missing or wrong `ach` makes `ready` false with `arg_contract_mismatch`, and that blocks world reads.
    - A census that differs while the `ach` matches keeps `ready` true.
    - None of these identify the PBO's bytes or the commit it was built from. That the deployed PBO matches the Python is an operational rule, kept by promoting both together: the live tree only advances in a controlled promotion that installs the PBO built from the same commit.
    - *Where:* `compute_bridge_ready` and `SERVER_ARG_CONTRACT` in `tools/dayz_mcp/server.py`, and `tools/pack-addon.ps1`.
    - *Decisions:* D-31 and D-76.

11. **Every verb has an ingress schema.**
    - The daemon validates each command's arguments against `_COMMAND_ARG_SCHEMAS` in `tools/dayz_mcp/loopback.py`, whoever sent it.
    - Extra keys, missing keys and wrong types are `bad_args`, and a verb without a schema is refused.
    - Each schema is written to accept what its tool sends. Two tests check it:
      - `tools/tests/test_ingress_schema_coherence.py`, for the sixteen verbs that had no schema before, over sampled argument shapes and two internal senders;
      - `tools/tests/test_validate_command_args_table.py`, which pins accepted and refused payloads for every schema.

12. **Playbooks are drafts until a frozen registry certifies them.**
    - `playbook_run` reports `certified: false` with `certified_reason: no_frozen_registry`.
    - A passing draft playbook is evidence, not a regression test.
    - *Where:* `tools/dayz_mcp/playbook_tool.py`.
