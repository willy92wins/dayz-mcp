# Architecture decisions: the invariants

The rules below hold DayZ-MCP together. Each one has a reason, a place in the code that enforces it, and the ID of the decision that set it (`D-NN`). The full decision log, with the day-to-day operational decisions, is the maintainer's and is not in this repository.

If you are about to change one of these rules, you are changing the design, not fixing a bug. Say so in the pull request.

[`dayz-mcp-architecture.md`](dayz-mcp-architecture.md) is the original design, kept as history. Where it disagrees with this page, this page is current.

## Authority and transport

1. **The game is the authority, and the server side of it by default.** State and positions are read in `MissionServer`. The one exception is driving: a car under `NetworkMoveStrategy.PHYSICS` only moves from its owning client, so `vehicle_control` runs on the client peer.
   - *Why:* engine-native reads and writes cannot be spoofed by the agent. `ActionStartEngine` returns early on the server under PHYSICS (the B3 probe in the README).
   - *Decisions:* D-01 and D-06.

2. **The mod pulls; Python is a passive endpoint.** The bridge polls the daemon over HTTP from `MCPBridge.OnTick`, which `MissionServer.OnUpdate` drives, with one poll in flight (`m_PollInFlight`). It posts results back. The key travels in the query string because `RestContext.SetHeader` only sets `Content-Type`, so the endpoint binds to 127.0.0.1 and never logs URLs.
   - *Where:* `addon/scripts/5_Mission/MCPBridge.c`, `MCPClientBridge.c` and `tools/dayz_mcp/loopback.py`.
   - *Decisions:* D-01, D-02 and D-12 (the client peer has its own `RestContext`).

3. **One daemon owns the port; every agent session is a client.** A standalone daemon talks to the game, and sessions start in `--client` mode and spawn it lazily if it is missing. A daemon is reclaimed by health, not by parentage. `--embedded` is a fallback.
   - *Decisions:* D-14.

4. **Every request is accredited against the socket's owner.** The registrations of both hosts must agree on port, keyfile, launch, argv, cwd and auto-spawn. A client verifies that the process on the other end of the socket is the daemon it expects before it sends anything secret.
   - *Where:* `tools/dayz_mcp/host_config.py`, `accredited_daemon_transport.py` and `pinned_keyfile.py`.
   - *Decisions:* D-21 and D-30 (the live credential can be reloaded, but not repointed).

## Coordination

5. **One box, one lease.** Reads need no lease. Mutations and lifecycle calls need the `dayz-box` lease, handed out FIFO with a 120 s TTL that every authorized command and heartbeat renews. Cleanup is scoped to the owner and never kills DayZ or the daemon.
   - *Where:* `tools/dayz_mcp/session_coordination.py`.
   - *Decisions:* D-15, D-19 (only a live `session_wait` can claim the head of the queue) and D-20 (a token is published only after its write-ahead log entry is durable).

6. **Only the sealed native launcher starts DayZ.** `dayz_test_run` and `dayz_test_stop` are the agent's only lifecycle interface. The launcher embeds CPython pinned by bytes and SHA-256, takes its request on stdin and keeps the lease token internal. Its policy is external, so it can be published.
   - *Where:* `tools/native-launchers/dayz-test-v1/`, `tools/build_native_launcher.py`, and `tools/packaged-modules.lock.json`, which pins the Python modules sealed inside it.
   - *Consequence:* changing one of `PACKAGED_MODULES` means rebuilding and re-registering the launcher before the live tree moves past that change.
   - *Decisions:* D-23, D-25, D-27, D-28 and D-35.

7. **Lifecycle fails closed.** Retail DayZ is manual-only. An external retail process, or a snapshot of processes that cannot be read, quarantines mutations instead of guessing. Runs whose processes are all confirmed dead are reaped to `EXITED`.
   - *Where:* `tools/dayz_mcp/process_lifecycle.py`.
   - *Decisions:* D-16 and D-18.

## Truthfulness

8. **A request is not retried once any of its bytes may have been sent.** If the client cannot tell whether the daemon received a request, it reports `daemon_response_ambiguous` instead of sending it again. Repeating a spawn or a lifecycle call can leave the world wrong.
   - *Where:* `ClientRuntime._call` in `tools/dayz_mcp/server.py`.

9. **Sent is not verified, and missing is not zero.** A tool reports what it observed. `wait_for(entity_state)` never reads absent data as `false` or `0`. `world_time_set` returns `multiplier_unconfirmed` because the engine has no getter for the multiplier.
   - *Where:* `_entity_wait_observation` and `world_time_set` in `tools/dayz_mcp/server.py`.
   - *Decisions:* D-68.

## Contracts

10. **The deployed PBO and the Python move together, and the bridge proves which PBO it is.** The bridge announces its version, its command census and an argument-contract hash (`ach`). The Python side fails closed on a mismatch. A mismatch blocks world reads with `game_not_ready:reason=arg_contract_mismatch` rather than let an old addon answer a new request. The live tree only advances in a controlled promotion that deploys the PBO built from the same commit.
    - *Where:* `compute_bridge_ready` and `SERVER_ARG_CONTRACT` in `tools/dayz_mcp/server.py`, and `tools/pack-addon.ps1`.
    - *Decisions:* D-31 and D-76.

11. **Every verb has an ingress schema.** The daemon validates each command's arguments against `_COMMAND_ARG_SCHEMAS` in `tools/dayz_mcp/loopback.py`, whoever sent it. Extra keys, missing keys and wrong types are `bad_args`, and a verb without a schema is refused. Each schema accepts exactly what its tool sends, and `tests/test_ingress_schema_coherence.py` checks that.

12. **Playbooks are drafts until a frozen registry certifies them.** `playbook_run` reports `certified: false` with `certified_reason: no_frozen_registry`. A passing draft playbook is evidence, not a regression test.
    - *Where:* `tools/dayz_mcp/playbook_tool.py`.
