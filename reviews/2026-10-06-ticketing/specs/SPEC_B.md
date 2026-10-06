## ITEM f4de

**Decision: implementation-ready specification for `player_kill`.** Add a server-authoritative, lease-gated kill by explicit UID. Keep `player_respawn` as the separate client respawn operation.

Repository paths below are relative to `C:\Users\guill\dzmcp_gauntlet\base`, the export declared to be `6671fd2`. Vanilla citations use:

- **V29:** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts`
- **V30:** `E:\DayZ-Exp-Extract\1.30.164014\exp\scripts\scripts`

API calls marked **[EXACT]** were read in those sources. Proposed contracts and implementation behavior are **[DESIGN]**.

**Verified evidence**

- Server player selection already exists: `addon/scripts/5_Mission/MCPBridge.c:3593-3631`. A supplied UID resolves through `FindHumanByUid`; failure returns `player_not_found`. Empty UID selects the first human, so the kill handler must explicitly prohibit it.
- The verified vanilla kill primitive is **[EXACT]** `player.SetHealth(0)`: server-side `EmoteManager.KillPlayer`, `V29/4_world/classes/emotemanager.c:826-831`. Its overload delegates to `SetHealth("", "", health)`: `V29/3_game/entities/object.c:1073-1075`.
- Vanilla `PlayerBase.EEKilled` performs character database death, corpse processing and respawn-mode synchronization: `V29/4_world/entities/manbase/playerbase.c:1183-1221`. Do not call `EEKilled` or fabricate `EEHitBy` manually.
- MCP godmode disables damage and periodically replenishes living players: `addon/scripts/4_World/MCP_PlayerCare.c:131-159,208-218`. `ReleaseBody` enables damage without changing the remembered identity preference: `:109-128`.
- Existing `player_respawn` mirrors the vanilla client respawn path and reports a request, without proving a replacement character: `addon/scripts/5_Mission/MCPClientBridge.c:1722-1766`.
- Business failures become public `ToolError`s: `tools/dayz_mcp/bridge_errors.py:508-531`.

**[DESIGN] Public and wire contract**

| Surface | Contract |
|---|---|
| MCP tool | `player_kill(uid: str, timeout_s: StrictFloat = 15.0)` |
| UID | Required, nonempty opaque `GetPlainId()` identity; no first-player fallback |
| Wire | Server command `player_kill`, arguments exactly `{"uid": "<identity>"}` |
| `MCPArgs` | Reuse `string uid`, currently at `MCPMessages.c:131` |
| Result | `player_kill: {uid, health_before, health_after, alive_before, alive_after, killed, godmode_policy_preserved}` |
| Success | Authoritative health is zero and the targeted body is no longer alive |
| Meaning | Death was applied to that body; client death-screen arrival and respawn remain separate acceptance checks |

Add the tool to `_CLOSED_SCHEMA_TOOLS`, beside the existing UID-sensitive tools at `tools/dayz_mcp/server.py:320-335`. Reject unknown public arguments so a mistyped UID cannot silently select another player.

**[DESIGN] Enforce implementation**

1. Add dispatch beside `player_heal`/`player_godmode`.
2. Validate arguments and nonempty UID before resolving anything.
3. Resolve the exact player; require its identity and verify it matches the requested UID.
4. Refuse an already-dead player before mutation.
5. Record health, alive state and damage permission.
6. Release body-level godmode using the existing `MCPGodmode.ReleaseBody` mechanism.
7. Invoke **[EXACT]** `player.SetHealth(0)`.
8. Read health and alive state back. Return success only when death is applied.
9. Preserve the identity’s remembered godmode preference. If the player remains alive, restore its previous damage permission and return `kill_not_applied`.

Do not automatically respawn. `player_respawn` acts on the local client; a kill addressed to another UID must not respawn the caller.

Errors: `bad_args`, `player_not_found`, `no_identity`, `player_dead`, `kill_not_applied`, plus existing lease, binding, readiness and timeout errors. A timeout means completion is unknown; query state before retrying.

**Capability and older-PBO protection**

- Add `player_kill` to `SERVER_COMMANDS`, its closed wire schema, `_BRIDGE_COMMAND_TOOLS["server"]`, and Enforce `SERVER_CAPABILITIES`.
- Add `player_kill: ("uid",)` to `SERVER_ARG_CONTRACT`. Recompute the hash using the existing canonicalizer at `tools/dayz_mcp/bridge_readiness.py:233-255`; update `MCPBridge.c:41`.
- Bump the synchronized bridge version once for the combined bridge release. Current version is `"10"` at `MCPMessages.c:1`.
- Add a daemon-side pre-enqueue gate for the new verbs requiring a fresh, accredited server capability announcement containing the command and matching argument hash. Repeat the check at delivery against the bound peer.
- Do not rely solely on the published `ready` field: `_enqueue_command` currently gates version, but does not enforce capability/hash readiness at `tools/dayz_mcp/loopback.py:2604-2617`.

**[DESIGN]** Missing capability must produce a named compatibility refusal, proposed `bridge_capability_missing`; wrong/missing hash produces `arg_contract_mismatch`. Neither may queue a command or consume an ID. An older PBO must never receive `player_kill` and answer `unknown_command`.

Lease authorization must remain before admission and commit. Existing authorization is at `tools/dayz_mcp/loopback.py:2413-2419`; commands absent from `READ_ONLY_COMMANDS` require a lease at `session_coordination.py:95-96`.

**Tests**

- Published closed schema: UID required; empty UID, misspelled fields and unknown arguments rejected.
- Exact wire routing and capability/tool/dispatch census parity.
- Old version; same-version PBO missing the verb; absent/wrong hash; stale announcement: all refuse with zero queue growth.
- No lease, expired lease, foreign run and retired binding: zero mutation.
- Target resolution: exact UID succeeds; unknown UID never falls back.
- Handler behavior: already dead; godmode on/off; kill not applied; timeout after dispatch.
- Source assertions support these checks but cannot prove Enforce compilation or death behavior.

**In-game acceptance**

On the connected test player, record UID and body identity. Test first with godmode off, then with godmode on:

1. `player_kill(uid)`; independently observe authoritative death and the client death screen.
2. Confirm an unrelated player remains alive, if a second controlled player is available.
3. Repeat kill before respawn: `player_dead`.
4. Invoke `player_respawn`; within 40 seconds confirm a **different living body** for the same UID.
5. Verify the remembered godmode preference survives respawn.

A request acknowledgement or a survivor creation log alone is insufficient.

## ITEM 120f

**Decision: implementation-ready specification for fail-closed `bot_start`/`bot_stop`. One script PBO can support both versions through compile-time exclusion.**

The 1.30 scripts were found and read. These API claims are source-verified; compilation and runtime behavior were not tested here.

**Verified evidence**

- V30 declares `PlayerBase.m_Bot` under `ROBOCLIENT`: `V30/4_World/Entities/ManBase/PlayerBase.c:237-239`.
- Construction in `Init` is likewise guarded: `:607-613`.
- Bot update is guarded: `:3513-3516`.
- `OnSpawnedFromConsole()` registers a server dummy with the mission scheduler and selects it; its bot construction is guarded: `:5281-5295`.
- `Bot.StartAction(int)` submits a start-debug event, or a stop event for `PLAYER_BOT_STOP_CURRENT`: `V30/4_World/Systems/Bot/Bot.c:205-215`.
- `Bot.ProcessEvent` returns whether the FSM accepted the event: `:383-393`. `StartAction` itself returns **void**.
- The installed debug-action transitions are at `:364-375`. Notably, `PLAYER_BOT_TEST_SWAP_C2H` is commented out and must not be advertised.
- Movement applies one-frame input overrides: `V30/4_World/Systems/Bot/Bot_MovementRandomizer.c:26-39`.
- V29 contains older debug-bot source too, but its player member/update use `DIAG_DEVELOPER`: `V29/4_world/entities/manbase/playerbase.c:238-240,3259-3262`. Symbol presence alone does not establish the requested 1.30 behavior.
- `world_spawn` stores created objects in `m_RuntimeObjects`: `addon/scripts/5_Mission/MCPBridge.c:632-651`. It does not call `OnSpawnedFromConsole` there.

**[DESIGN] Public and wire contract**

| Tool | Public arguments | Wire arguments |
|---|---|---|
| `bot_start` | `object_id: StrictInt`, `action: str`, `ttl_s: StrictFloat = 5.0`, `timeout_s = 15.0` | `object_id`, `action`, `bot_ttl_s` |
| `bot_stop` | `object_id: StrictInt`, `timeout_s = 15.0` | `object_id` |

Require positive object IDs and finite `0 < ttl_s <= 30`. Add `float bot_ttl_s` to `MCPArgs`; reuse its existing `object_id` and `action`.

Target only an object registered by this bridge’s `world_spawn`, castable to a living `PlayerBase`, with no connected `PlayerIdentity`. No UID fallback or connected-player control is included.

Allow only these exact action strings, mapped explicitly to their verified `EActions` constants:

- `PLAYER_BOT_RANDOMIZE_STANCE`
- `PLAYER_BOT_RANDOMIZE_MOVEMENT`
- `PLAYER_BOT_SPAM_USER_ACTIONS`
- `PLAYER_BOT_TEST_ATTACH_AND_DROP_CYCLE`
- `PLAYER_BOT_TEST_ITEM_MOVE_BACK_AND_FORTH`
- `PLAYER_BOT_TEST_SPAWN_OPEN`
- `PLAYER_BOT_TEST_SPAWN_OPEN_DESTROY`
- `PLAYER_BOT_TEST_SPAWN_OPEN_EAT`
- `PLAYER_BOT_TEST_SWAP_G2H`
- `PLAYER_BOT_TEST_SWAP_INTERNAL`

These transitions are verified at `Bot.c:365-375`; constants are at `V30/3_Game/Enums/EActions.c:95-107`. Reject raw integer IDs and `PLAYER_BOT_STOP_CURRENT` through `bot_start`.

**Compile-safe 1.29 behavior**

**[DESIGN]** Put all bot-dependent code behind nested compile-time guards:

- `DAYZ_1_30`
- `ROBOCLIENT`
- `INPUT_OVERRIDE`

The common adapter’s signatures use only cross-version types such as `PlayerBase`, strings, integers and floats. Its unguarded fallback returns `bot_unavailable`.

Every reference to `Bot`, `m_Bot`, bot events and movement-override symbols must be **inside** those guards—including fields, constructors, cleanup and tick maintenance. Do not define these engine macros in the addon.

On 1.29, the common dispatch handler validates the wire shape, then returns `bot_unavailable` **before any dummy initialization or mutation**. No runtime version check surrounds an otherwise compiled unknown symbol.

Historical module defines for 1.30 were recorded at `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\lanes\2026-09-26-roboclient-130\evidence_extracts.txt:4-7`. That is historical evidence, not verification of the currently running instance.

If the current 1.30 instance lacks any required macro, it also returns `bot_unavailable`. If actual compile gates invalidate the common-PBO approach, use a common cross-version stub plus a separately loaded 1.30 adapter addon, with distinct deployment identities. Do not substitute runtime exception handling.

**[DESIGN] Enforce behavior**

1. Resolve the registry object; reject missing, wrong-type, connected or dead targets.
2. On first successful initialization attempt, call **[EXACT]** `OnSpawnedFromConsole()` once for that object. Track initialization independently of whether the action transition succeeds.
3. Require non-null `m_Bot`.
4. Submit the same events used by vanilla through **[EXACT]** `Bot.ProcessEvent`, using `BotEventStartDebug` or `BotEventStop` (`BotEvents.c:17-18,29-36`).
5. Report `started:true` only if the FSM accepted the start transition. This still does not prove movement or inventory effects.
6. Track active controls by object and originating command/lease ownership. Reject a second start while active with `bot_busy`.
7. Stop on explicit stop, TTL expiration, object deletion, death, mission shutdown and owner cleanup. Owner cleanup is best effort; the server-side TTL is the independent bound.
8. `bot_stop` on an initialized, bridge-controlled idle dummy is idempotent: success with `stopped:false`.

Results:

- Start: `bot: {object_id, action, started, ttl_s}`.
- Stop: `bot: {object_id, stopped, released_by}`.

Errors: `bot_unavailable`, `object_not_found`, `not_dummy_player`, `player_dead`, `bot_busy`, `bot_action_rejected`, `bad_args`, and existing authority/compatibility errors.

Advertise both wire commands on **both** versions: on 1.29 the advertised contract is the working refusal stub. Conditional omission would make the capability census disagree and unnecessarily block unrelated tools.

Apply f4de’s closed schemas, daemon admission/delivery gates and lease policy. Add hash entries:

- `bot_start = action,bot_ttl_s,object_id`
- `bot_stop = object_id`

Recompute the combined hash once.

**Tests and acceptance**

Offline checks must cover unsupported compile branches, action allowlist, malformed arguments, older PBOs, registry/type/identity validation, duplicate start, rejected transition, idempotent stop and cleanup/TTL ownership. Preprocessor/source checks cannot replace actual module compilation.

In game:

- **1.29:** load the same candidate PBO; spawn a survivor dummy; both verbs return `bot_unavailable`; no movement, initialization side effects or script compile errors.
- **1.30:** spawn one dummy with `flags=1060|2048|8192`—decimal `11300`; record position and body identity.
- Start movement with a 10-second TTL. Over three seconds, independently observe displacement greater than 1 m.
- Stop; after a one-second settlement allowance, verify displacement stays below 0.2 m over the next two seconds.
- Repeat without explicit stop using a three-second TTL; verify automatic cessation.
- Start, then release the owning lease; verify cleanup, with TTL as the final bound.
- Delete the dummy and verify subsequent calls refuse.

Movement acceptance does not validate the other nine actions. Each advertised action needs an independent effect check; otherwise restrict the shipping allowlist to those actually validated.

## ITEM 9941

**Decision: investigation plan only. Source reading does not prove headless completion impossible.**

The first-pass assertion that progress requires a writable held `UAInput` is too strong.

**Verified mechanism**

- MCP invokes `PerformActionStart` once and immediately checks `GetRunningAction`: `addon/scripts/5_Mission/MCPClientBridge.c:3895-3915`.
- Vanilla’s public entry point exists outside the `BOT` guard: `V29/4_world/classes/useractionscomponent/actionmanagerclient.c:754-773`.
- Continuous input normally reads `LocalHold`/`LocalHoldBegin`: `actioninput.c:125-135`. `WasEnded()` returns `!m_Active`: `:109-111`.
- `ActionManagerClient.InputsUpdate` ends continuous input when `WasEnded()` is true **unless** `m_IgnoreAutoInputEnd` is set: `actionmanagerclient.c:282-301`.
- A public setter exists: **[EXACT]** `SetIgnoreAutomaticInputEnd(bool)`, `:1273-1277`.
- `CAContinuousTime.Execute` accumulates `GetDeltaT()` and completes through its normal component path: `actioncomponents/cacontinuoustime.c:29-54`. It does not directly poll `UAInput`.
- Animation callbacks drive `ActionContinuousBase.Do`: `actions/actioncontinuousbase.c:6-31`; `AnimatedActionBase.Do` checks continuation before progressing the component: `animatedactionbase.c:382-415`.
- Placement has additional client/server hologram and collision conditions: `actions/continuous/deployactions/actiondeployobject.c:35-70`.
- `ActionPlaceObject` uses `CAContinuousTime(UATimeSpent.DEFAULT_PLACE)` and reports `HasProgress=false`: `actions/continuous/actionplaceobject.c:1-20`. HUD progress absence therefore cannot prove stalled execution.

**Read before the experiment**

Finish tracing:

1. `ActionContinuousBaseCB` and `AnimatedActionBase` callback, interruption and finish paths.
2. `CAContinuousBase`, `CAContinuousTime` and one resource-consuming `CAContinuous*`.
3. `ActionInput`, including derived `WasEnded` implementations.
4. `ActionManagerClient.ActionStart`, `InputsUpdate`, `ProcessActionRequestEnd` and interruption cleanup.
5. `ActionManagerServer` handling of `INPUT_UDT_STANDARD_ACTION_START`, `...INPUT_END` and end requests.
6. `ActionDeployObject.SetupAction`, placement serialization and `OnEndServer`.
7. The actual SecretRock action and placement implementation before interpreting its persistence result.

**Ranked candidates and deciding experiments**

| Rank | Candidate | Smallest deciding experiment |
|---|---|---|
| 1 | Suppress automatic input-end through the public manager setter | Matched trials with the flag false/true, started once through the existing action path |
| 2 | Scoped action-input override supplying active/ended state | Only if trial 1 shows a remaining input-state dependency; override for one designated action/player |
| 3 | Server-dummy action path, preferably on 1.30 | Test a genuine continuous action on one dummy; distinguish dummy-only feasibility from connected-player support |
| Rejected as a current route | Set a raw `UAInput` value or send a key callback repeatedly | `V29/3_game/inputapi/uainput.c:48-55,78-86` exposes reads and enable/lock controls, not a local-value setter; key callbacks do not establish held native action input |
| Not a hold mechanism | Call finish/deploy callbacks directly | Bypasses elapsed progress and potentially collision, consumption and serialization |

**Experiment H1 — public manager setter**

Prepare a disposable probe that logs action ID, player/body identity, manager state, callback state, component progress, continuation failures, end-input requests and server completion. It must affect only the designated trial and restore the previous manager flag on every exit.

1. Use a vanilla kit on known clear ground, normal simulation time and gameplay camera.
2. Establish a valid hologram; record the kit and projection identity.
3. Control trial: flag false; start `ActionPlaceObject` once; observe for the action duration plus two seconds.
4. Fresh equivalent kit and projection: flag true; start once; use the same observation budget.
5. Record server completion, deployed entity and kit consumption.
6. Restore the flag; verify a subsequent ordinary action is unaffected.
7. Run an interruption trial: end before completion; verify no placement and no lingering ignore-input state.

**PASS:** the flag-true trial progresses naturally and produces authoritative placement while the control does not.  
**FAIL:** both cancel at the same identified input-dependent point.  
**INCONCLUSIVE:** invalid hologram, rejected start, frozen callback clock, missing server evidence or mismatched trials.

If both complete, the original failure was not isolated to absent hold input.

**Experiment H2 — scoped input state**

Run only if H1 identifies a remaining `ActionInput` dependency. Keep `Can`, callback timing, server continuation and serialization unchanged. Supply held state for a bounded interval, then release it.

**PASS:** natural progress and placement occur; early release cancels; later actions behave normally.  
**FAIL:** the identified end-input condition remains unchanged.  
**INCONCLUSIVE:** another condition fails first.

**Experiment H3 — dummy path**

On 1.30, use one initialized server dummy and a valid kit. Run the normal action-manager/callback path with bounded lifetime and server effect logging.

**PASS:** the dummy completes placement through normal conditions and consumption. This proves only a dummy route.  
**FAIL/INCONCLUSIVE:** classify the first failed gate; do not infer connected-client impossibility.

A hologram appearing despite `setup_failed` must be recorded independently. The immediate retained-action check at `MCPClientBridge.c:3906` does not prove that no side effect occurred.

Do not specify a production `hold_s` tool until one experiment establishes a controllable, cancellable mechanism.

## ITEM 7055

**Verdict: OPEN_CONFIRMED. The explicit client extension is blocked before reaching its implemented reattach path.**

**Evidence and root cause**

- `takeover_target_run_id` ignores only current ownership: `tools/dayz_mcp/process_lifecycle.py:1307-1328`.
- Ownership requires a nonempty `owner_session`: `:1203-1209`. A released ownerless run cannot satisfy it.
- The original launcher is separately recognized and allowed to adopt: `:1100-1128`.
- `_row_is_protected` consequently does not protect that launcher-owned row: `tools/dayz_mcp/box_occupancy.py:148-167`.
- Nevertheless, the public run tool returns takeover refusal before executing the extension: `tools/dayz_mcp/server.py:3913-3940`.
- With `takeover=true`, it stops the target first: `:3942-3956`.
- The extension implementation requires `RUNNING_IDLE`, checks project identity and evaluates client replacement: `dayz_test_tool.py:555-569,2027-2089`.
- The worker already adopts the supplied run and starts only its client role: `dayz_test_worker.py:762-775`.

A pure-function fixture executed here produced:

```text
launcher_recognized=True
may_adopt=True
protected=False
takeover_target="R"
error="takeover_required"
```

This establishes the classification defect without running DayZ.

The held-lease refusal is intentional: `_require_idle_session`, `dayz_test_tool.py:685-703`. Do not remove the release-first contract.

**[DESIGN] Implementation specification**

- Add an explicit client-extension admission path in `server.py`.
- Eligible request: `mode="client"`, supplied `run_id`, matching project, requested row `RUNNING_IDLE`, and caller recognized as that run’s original launcher.
- Exempt **only that requested row** from fresh-launch takeover classification.
- Apply the same distinction before queue/“own run” early refusals at `server.py:3842-3860`.
- Preserve all other blockers: another managed run, foreign DayZ, unknown scans, protected state and concurrent transitions.
- Proceed through existing extension validation, internal lease acquisition, adoption and client-replacement gates.
- Keep daemon authorization authoritative: launcher rights use the full session ID at `process_lifecycle.py:5682-5691`, and adoption protection at `:5694-5709`.
- Public prefix matching may select the path; it must never authorize a process change.
- Leave generic `takeover_target_run_id` behavior unchanged for fresh launches.
- A healthy polling client remains `client_already_polling`; do not make reattach unconditional.

**Contract after:** a released caller-launched server run with a confirmed-dead client gains a replacement client under the same `run_id`; server process identity and world state remain unchanged. No eviction occurs.

**Tests**

Exercise the actual public orchestration with fake transport/lifecycle boundaries:

- Released launcher’s explicit client extension reaches the extension path with zero stop calls.
- Identical fresh `mode="all"` request remains takeover-gated.
- Foreign launcher, prefix collision, daemon restart, wrong project and wrong run ID.
- `STARTING`, `STOPPING`, `UNRECONCILED`, unknown liveness and healthy client.
- Queue and immediate admission modes.
- Ownership/liveness change between classification, adoption and launch.
- Same run ID and server identity survive; only the client role is replaced.

**In-game acceptance**

Launch one owned tandem and place a server-side sentinel. Exit only the client through the game’s UI, discovering widget paths through `ui_tree`. Confirm the client process is gone while the server remains alive.

Release the lease, call `dayz_test_run(mode="client", run_id=R, ...)` with matching configuration, then reacquire. Require the same server PID/creation identity, unchanged sentinel and a new polling client. No `takeover=true`.

Python-only; no PBO. Reseal is required only if implementation extends into bundled worker/transaction modules.

## ITEM 9336

**Verdict: OPEN_CONFIRMED overall, with separate conclusions for its symptoms.**

| Symptom | Verdict |
|---|---|
| Caller’s released-run reattach blocked | **OPEN_CONFIRMED**, same defect as 7055 |
| InGameMenu present after launch | **NEEDS_REPRO** |
| Healthy graceful close times out | **NEEDS_REPRO** |
| Successful forced stop leaves the old run occupying the box | **LIKELY_FIXED**, requires current runtime confirmation |
| `settle_ticks=200` conflicts with bridge deadline | **OPEN_CONFIRMED** |

**Fresh evidence**

- Reattach: use 7055’s evidence and specification; no duplicate fix.
- Public camera/capture descriptions do not currently establish a post-launch menu-clearing requirement: `tools/dayz_mcp/server.py:5644-5654,6044-6073`.
- Camera settle is `ticks * 0.05`: `addon/scripts/5_Mission/MCPClientBridge.c:417,5801-5809`. Thus 200 means ten seconds.
- Its job deadline is five seconds: `:416,1793`. The runner times out unfinished jobs: `MCPJobRunner.c:159-163`.
- Successful lifecycle stop clears processes and commits retirement before success: `tools/dayz_mcp/process_lifecycle.py:4829-4881`. Retirement persists the manifest and invalidates box cache: `:2035-2055`.
- Graceful close requires new termination lines, successful reap and no missing/rotated RPT: `dayz_test_tool.py:3677-3684`. A client already gone without a termination line cannot satisfy that contract.

The deadline conflict does not, by itself, explain the ticket’s precise 40-second daemon timeout.

**[DESIGN] Camera deadline fix**

- Preserve the existing wire fields and result shape.
- Make the camera job deadline accommodate the effective settle duration plus five seconds of apply/report slack.
- Bound public and wire `settle_ticks` to `0..600`; zero retains the existing default-three-tick meaning.
- Validate before queuing or applying the camera. Values outside the bound return `bad_args`.
- Document `settle_ticks * 0.05` seconds and that the caller’s `timeout_s` must cover settle plus processing/transport.
- Keep timeout completion unknown: a camera may already have been applied.
- Update Python validation, `_camera_variant` at `loopback.py:641-653`, and Enforce validation together.

**Tests:** exercise runner transitions for ordinary settle, 200 ticks, boundary values, apply failure and an intentionally shorter caller timeout. Verify that a 200-tick job is not terminated at five seconds. Constant assertions alone are insufficient.

PBO rebuild required; no launcher reseal.

**Composable repros**

1. Immediately after readiness, inspect `ui_tree` before camera changes. If `InGameMenu` exists, close its observed Continue widget with `ui_click(mode="complete")`, then capture.
   - Menu already present: launch/UI behavior confirmed.
   - Menu absent until camera or focus manipulation: different trigger; keep open.
2. On a nearby baseline scene, compare `settle_ticks=3` and `200`, with `timeout_s=40`. Record bridge result, polling and actual camera readback.
3. At the end of a healthy run, perform normal `dayz_test_close`; record each role’s RPT, close order and lifecycle outcome.
4. If close degrades, release the lease and use `dayz_test_stop`. Immediately read `session_status` and attempt the next planned run.
   - Old run absent and next run admitted: delayed-retirement symptom likely resolved.
   - Old run still blocks: preserve exact generation, run state, stop result, scan/cache timestamps and audit; do not patch by declaring all `EXITED`/`STOPPING` rows free.
   - A different run acquired the box: contention, not the reported stale-own-run defect.

## ITEM 9efc+aecb

**Verdict: NEEDS_REPRO for client death attributed to `camera_set`; OPEN_CONFIRMED for the shared reattach defect and missing active-run exit detail.**

**Evidence**

- Camera dispatch validates and queues a job; the cited “queued” log precedes application: `addon/scripts/5_Mission/MCPClientBridge.c:1771-1800`.
- Application occurs later at `:4755-4768`.
- The look-at path creates and null-checks a static camera, then positions, aims and activates it: `:5380-5423`.
- Settling uses time rather than the native interpolation reads previously associated with render freezes: `:4771-4795`.
- These paths do not show a proven process-exit mechanism.
- Active lifecycle status projects stored run records: `tools/dayz_mcp/process_lifecycle.py:2082-2085,7011-7024`.
- The later client-death diagnosis is attached to **retired** runs at `dayz_test_tool.py:2299-2302`; its current implementation only recognizes `steam_bootstrap` or returns null: `client_steam_bootstrap.py:241-272`.
- The reaper retires a run only when all registered processes are dead: `process_lifecycle.py:6896-6907`. A live server therefore keeps the run active after client death.

Do not label these exits crashes without process-exit evidence supporting that distinction. Missing dumps establish neither a crash nor an orderly exit.

**[DESIGN] Minimal active-run diagnostics**

Add per-client-role diagnostics to active run status, without retiring or stopping the surviving server:

- `state`: `alive`, `dead`, `unknown`, or `not_started`.
- Registered process identity and observation timestamp.
- `first_observed_dead_at_utc`, explicitly an observation time.
- `exit_code` and exact `exit_time_utc`, nullable unless a retained process observation actually supplies them.

Use identity-aware lifecycle observation; do not attribute a reused PID to the old client. Preserve the previous client’s observation across reattach under its own process identity. Keep records bounded.

The existing launcher’s debug exit events concern the launcher (`native_launcher_backend.py:1548-1574`); they are not DayZ client exit codes. Do not substitute them.

Tests: server alive/client dead, unknown probe, PID reuse, reattach, late observations, daemon restart, bounded retention and proof that status collection makes no process-management calls. No PBO; native observation changes would require reseal.

**Shortest controlled camera repro**

Run paired 1.29 trials with identical mission, player state and candidate bridge:

1. Baseline without SecretRock mods.
2. Matching trial with `@SecretRock_RH` and `@SecretRock_RHTest`.
3. Follow the ticket’s short sequence where the fixture exists: door read → indoor teleport → open door 5 → door read → two raycasts.
4. Call exactly:

```text
camera_set(
  cam_mode="lookat",
  cam_pos=[13255.4,20,7148.3],
  look_at=[13252.8,19.9,7148],
  settle_ticks=10
)
```

5. Observe client process identity, exit evidence, bridge polling and logs for ten seconds; preserve results before recovery.
6. If alive, `restore_gameplay`; if dead, use the corrected explicit reattach path.
7. Separately test a distant camera around 6 km from the player for the Baltic symptom; do not combine that variable with the nearby SecretRock trial.

Interpretation:

- Both baseline and modded trials fail at the same boundary: common camera/engine candidate.
- Only modded trial fails: mod/scene interaction candidate, not proof of a particular mod defect.
- Only distant camera fails: streaming/distance candidate; investigate before imposing a distance guard.
- PID remains alive but polling/render stops: hang or degradation, not process death.
- Neither reproduces: retain NEEDS_REPRO with the tested configuration.

No coordinate clamp, automatic retry or claimed camera fix is justified yet.

## ITEM 449e

**Verdict: OPEN_CONFIRMED for radius-zero substitution and missing documentation; NEEDS_REPRO for universal sphere-centre semantics.**

**Evidence and root cause**

- Python accepts and forwards radius zero: `tools/dayz_mcp/server.py:4522-4538`. A pure wire-validation check here also accepted it.
- Enforce initializes effective radius to `0.05` and replaces it only when the requested radius is positive: `addon/scripts/5_Mission/MCPBridge.c:2494-2496,2523-2531`.
- `rvproxy` passes that radius to `RaycastRVParams`: `:2917-2923`.
- Returned position is copied unchanged from the engine result: `:2949-2959`.
- **The bullet branch ignores radius**, using `RayCastBullet`: `:2983-2999`.
- Vanilla calls the returned RV position a collision position, without establishing universal sphere-centre semantics: `V29/3_game/global/dayzphysics.c:103`.

**[DESIGN] Implementable documentation fix**

Update the `scene_raycast` description to state:

- With `method="rvproxy"`, requested `radius=0` currently uses an effective radius of `0.05 m`; positive values pass through.
- `pos` is the engine-returned position, without contact-point reconstruction.
- With `method="bullet"`, the implementation performs a raycast and does not use radius.
- The reported floor experiment suggests a sweep-centre offset for that geometry; do not promise that interpretation for all surfaces.

Preserve behavior and result shape. No PBO or reseal.

Do **not** implement the first-pass universal formula. For a downward ray, `centre - ray_direction * radius` moves upward—the wrong direction for the ticket’s floor. At normal incidence, the candidate along-ray correction is `centre + normalized(to-from) * radius`. It is not generally valid for oblique or edge contacts.

**Regression:** inspect published metadata, including the compact/full-description discovery path. Keep wording checks separate from behavioral verification.

**Composable geometry experiment**

Use one independently measured flat slab, normal simulation time and no moving geometry:

1. Downward RV casts at radii `0`, `0.01`, `0.05`.
2. Repeat upward against its underside.
3. Repeat one oblique cast.
4. Repeat matching bullet casts at radii `0` and `0.05`.
5. Record raw positions, normals, object/component identities and known plane height.

Expected code-level result: RV radius zero and `0.05` agree within tolerance; bullet results do not change because of radius.

A centre-height offset that tracks radius at normal incidence supports the reported interpretation for that fixture. Oblique/edge disagreement rules out a universal along-ray contact reconstruction. Engine measurements are required before adding `contact_pos`.

## ITEM d490

**Verdict: NEEDS_REPRO for the sleeping-display mechanism and pre-launch false admission. The current probe’s assurance gap is source-confirmed.**

**Evidence**

- Pre-run brightness uses `PIL.ImageGrab.grab()` of the desktop: `tools/mcp_capture.py:1004-1034`.
- Admission accepts a sufficiently non-black image: `:1125-1141`.
- This checks lock/brightness, not monitor power state or the future DayZ client surface.
- DayZ capture uses `PrintWindow`, with a screen-copy fallback: `tools/mcp-grab.ps1:127-144,322-344`.
- `execute_dayz_test_run` invokes desktop preflight before launch: `tools/dayz_mcp/dayz_test_tool.py:1967-1991`.

Those sources do not prove that monitor sleep caused the reported black frames. They also do not justify treating an unlocked bright desktop probe as a guarantee that later client captures work.

**Shortest composable repro**

Use the healthy baseline run before kill or camera-failure tests:

1. Awake-display control: record desktop-probe output, unlock state and a non-black DayZ capture.
2. Leave the same run, camera and scene unchanged. Let the operator-controlled display timeout put the display into power-save.
3. Record independently observed display state, idle age, timeout setting and timestamps.
4. Repeat the desktop probe and default/window capture.
5. Wake the display without restarting DayZ; repeat both captures.

Interpretation:

- Sleeping display + passing probe + black client captures, followed by recovery on wake: reproduces the admission gap and supports display-state involvement.
- Probe correctly rejects: no current false admission.
- Black persists after wake: investigate capture backend, minimization, rendering and surface selection.
- Actual display state cannot be established: INCONCLUSIVE; idle age exceeding a timeout is not proof.

Do not promise that `SetThreadExecutionState` wakes a sleeping display, or add synthetic input.

**[DESIGN] Implementable documentation clarification**

Clarify that desktop preflight checks current accessibility/brightness and does not keep the display awake or guarantee later client captures. No new power-state API or automatic power-policy change is ready from the present evidence.

The NavMeshGenerator computer-use app-discovery issue is **NOT_IN_MCP**: it is an application-catalog/installation concern, separate from these capture paths.

## ITEM a97e

**Verdict: NEEDS_REPRO. The current worker preserves the source path, but the Dokan/flag failure mechanism remains opaque.**

**Verified evidence**

- Request parsing supplies the policy’s default source when none is specified: `tools/dayz_mcp/dayz_test_request.py:475`.
- The worker forwards `payload["source"]` unchanged to the AddonBuilder broker: `tools/dayz_mcp/dayz_test_worker.py:724-745`.
- Native command construction uses `request.source`: `tools/native-launchers/dayz-test-v1/src/launcher.cpp:1032-1050`. No backing-path substitution occurs there.
- Path accreditation pins the source identity: `tools/dayz_mcp/request_path_authority.py:557-561`.
- Sealed roots validate both followed identity and final path: `:284-308`.
- Approved root-junction handling accepts the mount-point tag specifically; it is not permission to follow arbitrary reparse points: `:274-280`.
- The builder seals the declared source-root policy: `tools/build_native_launcher.py:575-577`.
- The known issue remains listed at `CHANGELOG.md:154`.

Thus the worker-side workaround is absent. That does not prove current AddonBuilder fails, nor that `resolved_path` maps this Dokan source to ordinary local NTFS.

**Reject the first-pass implementation as written**

Do not blindly replace every differing source with `resolved_path`:

- That field is a verified handle-final path, not a guaranteed Dokan-to-backing-directory mapping.
- A source may be a descendant of its sealed root; replacing it with the root loses the suffix.
- Public source containment is enforced at `dayz_test_request.py:378-381`.
- Rewritten paths must retain accreditation and identity checks, not bypass them.
- The ticket varied `-silent` and `-noLogs` together. It does not isolate which flag—or their combination—causes failure.

**Shortest deciding build experiment**

This is a build-only stage of the shared cycle; no game launch is needed.

1. Use the reported source and an ordinary local copy with identical relative paths, lengths and hashes. Record hydration/reparse attributes.
2. Use the same sealed toolchain, configuration, prefix and include behavior. Direct all outputs and temp data to separate scratch directories.
3. Run AddonBuilder against mirror and local source; capture argv, exit code, elapsed time and logs.
4. Run the corresponding binarize invocation on the mirror with:
   - neither flag;
   - `-silent` only;
   - `-noLogs` only;
   - both flags.
5. Compare artifact manifests, including scripts and required assets. Do not deploy the ticket’s small test PBOs.

Interpretation:

- Mirror fails, local succeeds: source-filesystem dependency confirmed.
- Exactly one flag variant fails: isolated flag association.
- Only both fail: combination dependency.
- Current admission rejects the source before AddonBuilder: path-policy issue, not the reported binarize failure.
- Local copy also fails: investigate content, dependency scope or toolchain.
- Both now pass: LIKELY_FIXED only for the measured environment/configuration.

If a backing path succeeds, the next spec should explicitly accredit that path and preserve descendant mapping. If only a materialized local copy succeeds, specify bounded staging with manifest/hash verification. Either worker/native build change requires launcher rebuild/reseal; no bridge PBO change. No implementation route is selected before this experiment.

## BATCHES

All rows are independent work scopes; the order minimizes deployment churn.

| Order | Batch | Scope | PBO | In-game/runtime evidence | Launcher reseal |
|---|---|---|---|---|---|
| 1 | Documentation | 449e method/radius semantics; d490 preflight limits | No | Geometry/display experiments remain separate | No |
| 2 | Explicit client extension | 7055 and reattach portions of 9336/9efc/aecb | No | Same server/world, replacement client | Only if bundled modules change |
| 3 | Active client diagnostics | 9efc/aecb status detail | No | Client exit with surviving server | If native/bundled observation changes |
| 4 | Player kill | f4de, schemas, compatibility gate and contract | Yes | Death, respawn and godmode-policy preservation | No |
| 5 | Bot adapter | 120f, guarded stub, actions and bounded cleanup | Yes | Compile/load on both versions; 1.30 effects | No |
| 6 | Camera timing | 9336 settle/deadline mismatch | Yes | 200-tick settle and timeout semantics | No |

Batches 4–6 can share one integrated candidate PBO, one synchronized bridge-version bump and one final server argument hash. They must retain separate acceptance results.

9941 has an investigation/probe batch, not a production hold implementation. Camera process exits, graceful-close failure, display power behavior and a97e require experiments before behavioral fixes.

For every deployment in the future cycle, record candidate and deployed PBO hashes, entry provenance, daemon generation and launcher bundle identity. Offline Python success does not prove Enforce compilation or in-game acceptance.

## IN-GAME CYCLE

One ordered evidence cycle, with managed run boundaries where version, mission or mods must change:

1. **Prepare offline**
   - Complete relevant positive/negative tests and build candidate/probe artifacts.
   - Record hashes and versions.
   - Run a97e’s scratch-output build matrix; select no production workaround prematurely.

2. **Admit the 1.29 baseline**
   - Follow the existing release → `dayz_test_run` → acquire cycle; launch manages its internal lease.
   - Record run, daemon, bundle, deployed PBO and server/client process identities.
   - Require accredited polling and the intended bridge contract.

3. **Inspect initial UI**
   - Record `ui_tree` before camera changes.
   - If the pause menu exists, complete its observed Continue action.
   - Capture an awake-display control.

4. **Geometry and nearby camera**
   - Run 449e’s flat-slab casts.
   - Run nearby camera settle at 3 and 200 ticks; use a sufficient caller timeout.
   - Restore gameplay and confirm client polling.

5. **Display experiment**
   - Run d490’s awake → independently confirmed asleep → awake sequence on the unchanged healthy scene.
   - Finish with a non-black capture or preserve the unresolved capture failure.

6. **Continuous-action investigation**
   - Run H1 matched control/ignore-input trials and early cancellation.
   - Run H2 only if H1 identifies a remaining input-state dependency.
   - Restore all probe state and independently verify subsequent actions.

7. **1.29 unsupported bot gate**
   - Spawn one dummy.
   - Confirm both bot verbs return `bot_unavailable` with no effects.
   - Remove the dummy.

8. **Player kill and respawn**
   - Run f4de’s godmode-off and godmode-on trials.
   - Confirm exact UID targeting, authoritative death, new living body and retained preference.

9. **Client-only recovery**
   - Establish a server sentinel.
   - Exit only the client through UI; confirm its process is gone.
   - Capture active-run diagnostics.
   - Release, explicitly reattach to the same run, reacquire and prove server/world preservation.

10. **Healthy baseline closure**
    - Perform normal close and collect per-role termination/reap evidence.
    - If degraded, use the managed stop path.
    - Immediately inspect status and admit the next planned run, checking 9336’s stale-occupancy claim.

11. **Camera-exit trials**
    - Run matched baseline/modded SecretRock trials with the exact nearby look-at request.
    - Test the distant-camera variable separately.
    - Preserve process and log evidence before reattach or managed cleanup.

12. **1.30 instance**
    - Identify and acquire the correct existing instance; do not evict another owner.
    - Verify its deployed candidate and actual module defines.
    - Run movement, explicit stop, TTL and owner-release bot acceptance.
    - Validate every other action retained in the shipping allowlist.
    - Run H3 only if needed.

13. **Close evidence**
    - Remove created fixtures and restore gameplay/probe state.
    - Release exclusive leases promptly.
    - Run `session_status` before handoff.
    - Record each check as PASS, FAIL or INCONCLUSIVE, including degraded closures and unrun branches.

## NOT VERIFIED

- No files were modified; no DayZ process, daemon or MCP tool was called. No commit, deployment, reseal or persistent handoff was created.
- The directory has no usable Git metadata; `6671fd2` is the supplied export identity.
- Verification here comprised source reading and pure-function checks, including the reattach classification failure and acceptance of radius-zero wire arguments. No complete test suite was run.
- Candidate Enforce compilation, native linking and runtime behavior remain unverified.
- The current 1.30 instance, its module defines and deployed PBO were not inspected through runtime tools. The located 1.30 APIs are source-verified, not ASSUMED; historical runtime evidence was labeled separately.
- SecretRock’s actual action implementation, its reported evidence artifacts and the Baltic fixtures were not validated here.
- Camera process-exit causation, display-sleep causation, universal RV contact semantics and binarize’s internal failure mechanism remain unresolved.
- Continuous-action completion is a viable investigation candidate, not an implemented or proven capability.

