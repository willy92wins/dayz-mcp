<!-- ARBITRAJE DEL ORQUESTADOR — leer antes que el cuerpo -->

# PR del transporte: el fin del lease espera al juego (diseño)

**Qué es esto.** El cuerpo, en inglés, lo escribió un worker de Claude el 2026-10-24 sobre main f8cf3b4. Parte del P0 que Codex Sol encontró en la r2 del #261 (Y10, un comando ya entregado que corre después de que el lease pase a otra sesión) y de los Y14 a Y16 de su r3 del #263. Esta cabecera es del orquestador: dice qué se decide y qué tiene que mirar la revisión. Donde discrepen, manda la cabecera.

**Decisión de Guillermo (2026-10-24 10:07Z):** «PR aparte». Lo revisa Codex Sol y lo mergea Guillermo, porque toca el PBO.

**Lo que se elige:** el diseño C del cuerpo (§3.C). Es FIFO en los dos bridges y una barrera por cada (instancia, peer) que el lease tocó. La entrega del lease siguiente queda cerrada hasta que cada barrera contesta, o hasta 40 s, que acaban en `lease_end_unconfirmed`.

**Lo que se añade al cuerpo:**
- **Y16 de #263** (64 releases viejos llenan la cola). Con FIFO y la barrera, el fin del lease de #263 puede mandar un solo stop que no nombre la ruta (§3.C, «What #263 and #266 could become»). Eso se hace en la ronda 6 de #263, no aquí: este PR no cambia #263 ni #266.
- **La versión 13 del bridge** obliga a traer este PR a cada PR del lote que toque el PBO (#261 a #267) y a construir el PBO nuevo. LIVE y T130 se resellan en la ronda final de todos modos (#244).
- **Los tests T1 a T5 del repro** (fuera del repo) pasan a ser tests del PR, que fallan en main y pasan con el arreglo.

**Preguntas para la revisión:**
1. ¿Hay algún camino en que B reciba el lease con un efecto de A aún activo sin que la entrega diga `lease_end_unconfirmed`?
2. ¿El `_lease_end_pending` sin bloqueo deja algún orden de locks o algún hueco de vida? La expiración corre en hilos de /poll.
3. FIFO: ¿`RemoveOrdered` existe en la vanilla 1.30 (`1_core/proto/enscript.c`) y conserva el orden? ¿El nuevo reparto de `OnPollSuccess` mantiene el aplazamiento de `world_spawn`?
4. ¿La lista de jobs y controles que espera la barrera (§1.7 y la enmienda 2) está completa en los dos peers?
5. ¿Son correctos los topes de 36 s y 40 s y la excepción de `ui_dialog`?
6. ¿`session_handoff_confirming`, y el conflicto ante `unconfirmed`, encajan con la recuperación de control_client?
7. ¿Algo de esto choca con los PR abiertos del lote, del #261 al #267?
8. ¿Hay una opción más simple que cierre lo mismo?

---

# Delivered-command race and lease-end confirmation: design

Base: main `f8cf3b4`. PR lines are marked: **#263** = `9060544`, cited from the commit;
**#266** = `fe16c68`.
All other `path:line` refer to `f8cf3b4`. Paths are repo-relative; `loopback.py`,
`session_coordination.py` etc. are under `tools/dayz_mcp/`, the `.c` files under
`addon/scripts/5_Mission/`.

**Sealed files.** No design here touches any of the 21 files in
`tools/packaged-modules.lock.json`. The recommended design (C) changes the PBO
(`MCPBridge.c`, `MCPClientBridge.c`, `MCPMessages.c`) and bumps the bridge version. On the Python
side it changes `loopback.py`, `session_coordination.py`, `control_client.py` and `core.py`.
None of these is in the lock, and neither are the `4_World` control classes whose static state
the barrier reads (for example `MCP_Weapon.c:66-89`).

## 0. Summary

One root, three findings. The handoff to the next lease completes on broker state alone, and the
game is never asked whether it has finished.

1. **Original gap.** An abandoned delivered mutation, or one that is only unanswered at lease end,
   can run in the game after B holds the lease (repro T1, T2).
2. **Y14 (#263 review).** Lease-end releases (`vehicle_release`, `bot_stop`, `action_hold_cancel`,
   #263 `camera_path_release`, #266 `survivor_drive_release`) are only queued. B is granted
   before any of them reaches the game (T3). Controls that have only a TTL (weapon override,
   input press, player move, up to 30 s) and open dialogs have no lease-end release at all.
3. **Order.** The bridges do not dispatch in delivery order, on either peer (T4, T5). Every
   "release after the command" scheme depends on that order, including #263's and #266's own
   ledgers.

**Recommendation: C.** Fix FIFO dispatch in both bridges. At lease end, queue one barrier per
touched peer instance, after the existing releases. A barrier answers once everything delivered
before it has run and nothing A started on that peer is still active. The handoff stays closed
until each barrier answers, or until a 40 s bound ends with an explicit `lease_end_unconfirmed`.
This closes 1 and 2 with one mechanism.

## 1. Current mechanics at f8cf3b4

**1.1 Enqueue with owner and lease.**
- `enqueue_command` (loopback.py:3054) authorizes lease-requiring commands (3114 →
  `authorize`, session_coordination.py:1333-1446).
- It commits them under the broker lock (loopback.py:3185-3195 → `commit_authorization`,
  session_coordination.py:1448-1497). The commit sets `committed_commands[id]` and, when the
  tool has a deadline, `operation_pins[id]` (1485-1487).
- `_publish_enqueue_locked` records:
  - `_command_owner[id] = (client, lease_id)` (loopback.py:3697);
  - the operation deadline (3734);
  - the instance seal (3740);
  - the broker row (3751 → 3796-3823): owner, lease proof, destination pin, `state: queued`, and
    the release plan from `RELEASE_REGISTRY` (147-158).
- Read-only commands need no lease (session_coordination.py:21-51, 99).
- Lifecycle operations commit **negative** ids to the same lease (process_lifecycle.py:2172,
  3181-3201).

**1.2 Delivery in the poll answer.** `record_poll` (loopback.py:4386-4685):
- It first runs the operation reaper (4409).
- It walks the destination queue in order. Two kinds of command are held back: commands for the
  other peer (4570), and `action_hold` while its run is hold-blocked (4614-4620).
- For lease-requiring commands:
  - under retail quarantine they are discarded (4628-4630);
  - owner-cleanup and internal fire-and-forget commands pass (`_cleanup_dispatch_locked`,
    4220-4228; `_OWNER_CLEANUP_COMMANDS`, 1042-1044);
  - all others need `claim_dispatch` (4635; session_coordination.py:1499-1515: the owner's lease
    is `_active` and the id is committed).
- The owner, lease and pin are stripped from the wire (4650-4653). The row is marked
  `delivered` (4662-4664). The one-shot `/set_poll_delay` sleeps after that mark (4673-4675,
  handler 6343-6344).
- The wire command is `{id, cmd, args}` (MCPMessages.c:244-259).

**1.3 Abandon of a delivered command (timeout or cancel).**
- The tool raises `timeout waiting for <cmd> id=<n>` (server.py:2476-2479) and runs a shielded
  2 s abandon (server.py:856-903).
- The abandon reaches the broker by one of three paths: `/abandon` (loopback.py:5807-5828) →
  `request_abandon` (4051-4092); the embedded `abandon_command` (4321-4362); or the reaper at the
  deadline (4364-4378, called from 4409, 5001, 5381).
- For a `delivered` row, `_abandon_row_locked` (4119-4174) does three things:
  - `_forget_command_tracking_locked` (4230-4236) drops the fence, enqueue time, deadline,
    owner, fire-and-forget mark and row;
  - a delivered hold is fenced (5187-5199);
  - the registered release is published (`_publish_release_locked` 4238-4300: pinned to the
    original destination, fire-and-forget, `internal: True`).
- `_flush_abandon_outcome` (4302-4319) then calls `finish_operation_exact`, which pops the id
  from `committed_commands` and `operation_pins` (session_coordination.py:1702-1722, at
  1715-1716).
- From then on, only the game knows the command:
  - `pending_for_owner` (loopback.py:5071-5077) and status `pending_commands` (6032-6034) read 0;
  - the command no longer pins the lease TTL (`effective_expiry`, session_coordination.py:233-234);
  - a late result is unknown, so it is discarded (loopback.py:4939-4953).

**1.4 Lease end: release, expiry, admin; fence and handoff.**
- `release` (session_coordination.py:1286-1315), `admin_release` (1833-1879) and TTL expiry
  (`_expire_due` 2425-2438) all run `_release_active_locked` (2476-2649). That function:
  - arms the release WAL (2480);
  - sets `_active = None` and `_releasing = lease`, and invalidates the token (2490-2495);
  - runs the cleanup hook in a worker while the condition is released (2509-2548);
  - may keep `_releasing` for a lifecycle `CleanupDisposition` (184-187). That wait, at 2565,
    holds the condition; the watcher is at 2573 and 2651-2713;
  - then sets `_releasing = None; _handoff_pending = True` (2612-2613) and writes the bounded
    audits (2626; `RELEASE_AUDIT_TIMEOUT_S` 0.05 s, :59). `_handoff_pending` clears once the
    audits are durable (2796, 3423-3427).
- There are two other handoff sites: the lifecycle-fault repair (2193-2194) and the
  fenced-cleanup watcher (2687-2688).
- A grant needs `slot_idle` (acquire 515-522, wait 1146-1154) and `claimable` (3429-3440).
  **None of these predicates looks at delivered commands or at queued releases.**
- The daemon's hook is `cleanup_begin` (daemon.py:89-120). It runs `cleanup_owner` (97) and then
  `begin_release_owner` (101).
- `cleanup_owner` (loopback.py:5237-5340):
  - checks cleanup authority (5250-5258; session_coordination.py:1549-1560);
  - discards only **queued** lease commands (`cancel_owner_pending` 5260 → 5027-5069);
  - closes queued holds (5264);
  - abandons delivered holds with `action_hold_cancel` and always adds `hold_cancel_unconfirmed`
    (5275-5298; under quarantine it fences them instead, 5265-5273);
  - returns early when no vehicle or bot is active (5299-5302);
  - otherwise enqueues `vehicle_release` and `bot_stop` as internal fire-and-forget commands
    (5315-5332), reported as `*_enqueued` with an **empty** `cleanup_degraded`.
- #263 adds `camera_path_release` per held play (#263 loopback.py:5452, 5490, 5498-5504). Its
  ledger is at 2106 and 3458-3527, noted at delivery (4814) and pruned on the result (5128).
- #266 adds `survivor_drive_release` per survivor (#266 loopback.py:5499, 5534, 5545-5551). Its
  ledger is at 2173 and 3527-3580, noted at delivery (4868).
- `begin_release_owner` (process_lifecycle.py:7361-7389) → `_quiesce_then_release_owner`
  (3704-3720) → `_transition_to_idle` (3670-3702). That fences the run (`fence_runs`,
  loopback.py:2661-2677), drains its queues except fire-and-forget (2605-2635) and persists
  RUNNING_IDLE. Nothing waits for any release's result.

**1.5 Quarantine and retirement.**
- Retail quarantine:
  - discards lease-requiring deliveries (loopback.py:4628-4630);
  - refuses lease-requiring releases (4250-4251);
  - makes `cleanup_owner` skip lease-end releases and report `*_unconfirmed` (5304-5310; #263
    5456-5467; #266 5503-5511).
- `_retire_instance_locked` (2560-2593) discards the instance queue and retires the binding. It
  keeps the result fences of dispatched ids (2592-2593), so a late result from a retired instance
  gets 409 `binding_retired` (4891-4906).
- Retirement follows process termination:
  - replace-role retires after terminating the superseded process (process_lifecycle.py:4180-4193,
    4223 → 2286);
  - the reaper retires only all-dead runs (7710-7724, 7788-7820), every 30 s (daemon.py:74,
    972-986).
- Precedent for "fence until the game confirms": a delivered hold blocks new holds on its run
  until that hold's own result (loopback.py:5107-5120, applied at 4614-4620, cleared by
  5201-5235).

**1.6 Result matching.** `store_result` (loopback.py:4874-4998):
- instance fence (4889-4938);
- unknown or past-deadline results are discarded (4939-4953);
- fire-and-forget results are dropped (4954-4962);
- anything else is stored (4963-4973), followed by `finish_operation_exact` for the owner lease
  (4979-4993). That call does nothing once the lease has left `_active`/`_releasing`
  (`_exact_lease_locked`, session_coordination.py:3854-3871).

**1.7 Bridge: OnPollSuccess and m_Pending.**
- Server `OnTick` (MCPBridge.c:119-154) does the following each tick:
  - runs jobs (131) and `DrainPending` (132);
  - keeps one poll in flight (134);
  - starts no poll while more than 8 commands are pending (139);
  - polls at 5 Hz by default (:80; the daemon writes `pollHz: 5`, loopback.py:2278).
- `OnPollSuccess` (277-338):
  - dispatches the first 4 commands **inline**, whether or not older commands are waiting in
    `m_Pending` (328-331);
  - sends `world_spawn` and the rest of its batch to `m_Pending` (313-327); everything else also
    goes there (332-335; full → `bridge_queue_full`, 357-376).
- `DrainPending` (340-355) takes `Get(0)`, then `Remove(0)` (350-351), 4 per tick.
- Client twin: `OnPollSuccess` 848-891 (inline 887); `DrainPending` 1019-1034 (`Remove(0)`
  1030); `MAX_PENDING` 16 (410); poll watchdog 30 s (489, 643-657).
- Results are async POSTs with no retry (MCPBridge.c:4570-4602; log `result posted id=…
  dispatch_tick=…` 4601; client 7273).
- A failed or timed-out poll drops its request identity, and a late answer is ignored
  (MCPBridge.c:383-394; MCPCallbacks.c:16-27).
- Jobs:
  - server spawn and seat jobs, `JOB_TIMEOUT_S` 5 s (MCPBridge.c:9, 668-680, 1205-1216,
    4241-4275);
  - client jobs (`m_JobRunner`, MCPClientBridge.c:515, ticked at 624-627) carry deadlines from
    their dispatch. The longest are `camera_set` (settle ≤ 600 × 0.05 s, + 5 s; 417, 1826,
    loopback.py:887), `player_move` (≤ 30 s + 5 s; 2855), `action_hold` (≤ 120 s + 8 s; 4109,
    loopback.py:549) and `ui_dialog` (≤ 240 s + 5 s; 3548, ui_dialog.py:29). Holds and dialogs
    answer only at their end (MCPMessages.c:1167-1205).
- Controls that outlive their answer:
  - client: weapon override, input key and player move are ticked before the jobs (602, 606,
    611; TTLs ≤ 30 s, loopback.py:640, 793, player_move.py:29-30). Vehicle control
    (`MCPCarDrive`, MCP_CarScript.c:1-8) is capped at 30 s (loopback.py:788);
  - server: bots tick at MCPBridge.c:123 (TTL ≤ 30 s, loopback.py:1013);
  - the samplers (`MCPAnimTimeline`, `MCPPlayerTrace`, MCPClientBridge.c:615, 619) only read.
- Outstanding work is already counted (MCPBridge.c:247-249; MCPClientBridge.c:767-786).
- **Order.** The #263 review reports that vanilla `array.Remove(i)` fills the hole with the last
  element (1_core/proto/enscript.c:458-463) and that `RemoveOrdered` keeps order. The vanilla
  source is not in this container. DayZ-Modding-Knowledge-Pack/skills/enforce-script-reference/SKILL.md:75
  records the same rule. Under that semantics, the real client bodies reproduce the reviewer's
  in-game order exactly (T4), and a new batch overtakes older pending commands (T5).

**1.8 What the bridge knows about leases.** Nothing:
- the wire is `{id, cmd, args}`;
- `Dispatch` has no authority check (MCPBridge.c:469; MCPClientBridge.c:1080);
- the bridge's only identity is `inst=` (MCPBridge.c:272).
- The bridge version is "12" (MCPMessages.c:1; core.py:41).

**1.9 control_client recovery.**
- `_require_recovery_idle_status` (control_client.py:830-857) accepts idle only when all of
  these hold: `self.state` is `none`, `pending_commands == 0`, there is no audit or lifecycle
  fault, and `cleanup_degraded` is empty (848-850). Otherwise it raises
  `session_transition_conflict` (854). `_reconcile_idle_session_locked` (859-922) runs this check
  before and after cancelling.
- After an abandon, `pending_commands` is already 0 (1.3).
- Main reports `hold_cancel_unconfirmed`, which counts as a conflict. It reports `vehicle_release`,
  `bot_stop` and #263/#266 releases as `*_enqueued` with an empty bag, which counts as clean.
  session_coordination.py:2637-2645 notes that control clients treat any non-empty
  `cleanup_degraded` as a conflict.

## 2. The race as offline tests

**Helpers that drive the real broker and coordinator in memory:**
- `tools/tests/fence_helpers.py`: `INST_*`, `PID_*`, `bind_both_peers` 15, `bound_queue` 210;
- `tools/tests/lease_helpers.py`: `FakeClock` 20, `SequentialIds` 31;
- `_owned_state` in `tools/tests/test_action_hold.py:404-424`, already on main: `bind_both_peers`
  plus `SessionCoordinator(cleanup=state.cleanup_owner)`, as the daemon wires it, and a granted
  lease. The **#266** lease probe (`tools/tests/test_survivor_drive.py:116-185`, `_leased_state`
  128-141) does the same;
- `tools/tests/enforce_subset_helpers.py` `translate` (301-322), which runs real Enforce bodies.

Avoid `BrokerFixture` (`tests/broker_binding_helpers.py`), which opens a real loopback listener.

**Scratch repro (outside every repo).** `repro_race.py`, sent with the review brief and kept
out of the repo. It ports those helpers to main, binds both peers with `install_bound_peer`, and
opens no listener and no process. Run it from a main checkout's `tools/` with its own folder on
`PYTHONPATH` (`python -m unittest repro_race`). Result on main: **5 of 5 red**.

| # | Steps | Red assert on main (observed) |
|---|---|---|
| T1 | A acquires → `object_delete` → server poll delivers it → `request_abandon(tool_timeout)` = `abandoned`, `pending_for_owner(A)` = 0 → A releases (`cleanup_degraded: []`) → B acquires | `B == 202` (got **200 active**). The late result is then `discarded` |
| T2 | Same, without the abandon | `B == 202` (got 200). The late result is stored for A after B's grant |
| T3 (Y14) | A: `vehicle_control` (throttle 0.5) delivered and answered → release reports `vehicle_release_enqueued: 1`, `cleanup_degraded: []`, client queue `["vehicle_release"]` → B acquires | `B == 202` (got 200 while the release is still queued) |
| T4 | Real client `OnPollSuccess` loop + `DrainPending` on a 16-command batch, `Remove` modelled as reported | order `1..16` (got `1,2,3,4,5,16,15,…,6`) |
| T5 | Batch of 12, one drain, then a batch of 2 (pending is 4, so a poll may start) | order `1..14` (got `1,2,3,4,5,12,11,10,13,14,9,8,7,6`) |

## 3. Designs

### A. Hold the handoff until each delivered mutation is answered or discarded ("delivery debts")

- **Broker:**
  - abandoning a delivered lease-requiring row keeps `_command_fence` and `_command_owner`, so a
    late answer is still discarded but now settles the debt (today 4151-4153, 4230-4236,
    4313-4319);
  - drops only the deadline, the pin and the row.
- **Coordinator:**
  - at the three handoff sites (2612, 2193, 2687), the broker-delivered ids still committed to
    the ending lease become debts. These are positive ids only; lifecycle commits are negative;
  - `slot_idle`, `wait` and `claimable` require no debts;
  - `finish_operation_exact` and `discard_committed` settle a debt by session, lease and id
    after the lease is gone;
  - retirement settles the debts of its instance.
- **Wire and bridge:** none.
- **Latency:** the next grant waits for the late command itself.
- **Never answers:**
  - a lost result POST or a lost poll answer leaves a debt (both are possible: 4570-4602 has no
    retry; 383-394 drops the request);
  - only retirement or a bound settles it. Retirement comes from the reaper after a crash, or
    from replace-role for a new instance;
  - past the bound, the race is back for that command.
- **#263, #266 and long jobs:**
  - does **not** close Y14: answered commands with lasting effects are not debts, and releases
    stay fire-and-forget;
  - making release results debts does not help without FIFO. On main a release can run before
    its still-pending source (T4, T5), and then the scoped releases do nothing. #263's release
    acts "only while the named play is the one playing" (#263 3505-3511); #266's drops a
    survivor's drive only "if that id set it" (#266 3552-3556).
- **Timed-out tool:** unchanged. **Sealed:** none.

### B. Execution authorization that the bridge revalidates

- **B1, authority epoch:**
  - every poll answer carries the current epoch, and every command carries the epoch it was
    authorized under (`MCPCommand`/`MCPCommandBatch`, MCPMessages.c:244-259);
  - `Dispatch`, `DrainPending` and job ticks refuse stale commands with `authority_revoked`;
  - version bump to 13.
- **Why B1 fails:**
  - the bridge learns about a revocation only from its next poll answer, so pending and running
    work of A continues after B's grant until then;
  - B1 orders A's leftovers before B's *bridge* commands, not before B's grant. B's window-grab
    capture and lifecycle actions do not go through the bridge;
  - lasting effects that are already running (camera path, drive) are not commands, so nothing
    revalidates them;
  - nothing confirms anything, so Y14 stays.
- **B2, TTL token:** this needs a shared clock. The bridge only has `m_ElapsedS` summed from
  timeslices (MCPBridge.c:122), and a poll answer can be held arbitrarily after delivery
  (loopback.py:4673-4675). A TTL therefore cannot bound start time in daemon time.
- **B3, two-phase (the bridge asks before executing):**
  - costs one round trip per mutation, at least one poll interval (200 ms at 5 Hz);
  - still needs A's wait for work it has already authorized.
- **Never answers:** under B1 nothing waits, so there is no liveness cost and no guarantee.
- **Timed-out tool:** unchanged. A revoked command answers late and is discarded.
- **Sealed:** none. Needs a PBO change.

### C. FIFO dispatch plus a lease-end barrier per touched peer, with a bounded handoff wait

This is the #263 reviewer's proposal, evaluated.

**Does it hold?** Yes, with four amendments:
1. **FIFO needs two bridge fixes.** `DrainPending` must remove in order (MCPBridge.c:351;
   MCPClientBridge.c:1030), and `OnPollSuccess` must not dispatch inline while `m_Pending` is
   non-empty (MCPBridge.c:328-331; MCPClientBridge.c:887-890). Broker delivery is already in
   queue order per destination, except a hold-blocked `action_hold` (loopback.py:4614-4620,
   4672). The lease's queued commands are discarded before the barrier is queued (5260), so
   "delivered before the barrier" means "queued before it".
2. **Dispatched is not done.** Jobs and controls started by earlier commands keep running
   (1.7). The barrier answers `ok` only when its peer has no open job and no active control; the
   samplers do not count. Its answer then proves two things: everything delivered before it has
   run, and everything those commands started has ended. It does not hold back later commands.
   Only lease-requiring commands start jobs or controls (no read-only command does,
   session_coordination.py:21-51), and none of B's can reach the game before the handoff.
3. **It confirms; it does not stop.** An effect with a lease-end release stops at once, because
   `cleanup_owner` queues that release before the barrier and, with amendment 1, it runs after
   its source. That covers vehicle control and bots (5315-5332), delivered holds (5288-5296),
   the #263 camera path (#263 5490) and the #266 drive (#266 5534). Anything else is waited out
   within its own cap (see **Bound**).
4. **One barrier per touched (instance, peer).** It is pinned and fenced like any command, so only
   that instance's `ok` counts (4889-4938). A not-ok answer such as `bridge_queue_full` (371) is
   not a confirmation. Retirement of that instance settles the barrier: the process is gone, and
   its late results are already refused (4891-4906).

**Changes.**
- **Bridge (PBO, both peers):**
  - FIFO as in amendment 1. Check `RemoveOrdered` in vanilla `enscript.c` first; a head index
    works without it.
  - One internal barrier command per peer, with no args. Routing is by name (loopback.py:283-286),
    so it takes two names, e.g. `lease_barrier` and `client_lease_barrier`.
    - On dispatch it is parked, not answered. Later commands keep dispatching.
    - Each tick, it answers `ok` once nothing in amendment 2 is active (server `m_Jobs`, bots;
      client `m_JobRunner` and the controls in 1.7).
    - 36 s after its dispatch it answers `ok:false error=jobs_open`, naming what is still
      active.
  - `MCP_BRIDGE_VERSION` "13" (MCPMessages.c:1, core.py:41); the capability lists
    (MCPBridge.c:38, MCPClientBridge.c:474) and the server arg-contract hash
    (bridge_readiness.py:260) follow. `MCPCommand` and `MCPResult` do not change. A v12 bridge is
    refused by the version gate (loopback.py:3250-3275), so there is no mixed mode.
  - Sizing: `OnPollSuccess` spends its budget of 4 on the head of `m_Pending` first, and on the
    batch only once `m_Pending` is empty; the rest is appended, with the `world_spawn` deferral
    kept. Peak occupancy is then at most today's (pending + batch − 4), so `MAX_PENDING` and
    `MAX_POLL_RESULTS` (MCPClientBridge.c:410, 413-415; pinned by tests/test_guards_bridge.py:181)
    stay as they are.
- **Wire:** one new command name per peer. The poll JSON does not change.
- **Broker (loopback.py):**
  - At delivery (4662-4664), remember which (instance, peer) pairs each lease's lease-requiring
    commands reached.
  - `cleanup_owner` queues one barrier per touched destination that is still bound:
    - last, after every release it queues, on every path past the authority check, including
      the early return at 5301-5302 (T1 and T2 take it);
    - under the broker lock, with the authority recheck of 5316-5321;
    - as fire-and-forget under an owner-cleanup name (1042-1044);
    - registered with the coordinator (broker → coordinator order, as at 5316-5320);
    - reported as `lease_end_barriers`.
  - The unconditional `hold_cancel_unconfirmed` for a delivered hold (5289-5293) goes away,
    because the barrier now confirms the cancel. Quarantine keeps it (5272).
  - Under quarantine it queues no barrier and, if a peer was touched, adds
    `lease_end_unconfirmed`.
  - The fire-and-forget branch of `store_result` (4954-4962) and the retirement/discard paths
    (2560-2593, 4794-4805) settle barriers after the broker lock: `confirmed`, `refused` or
    `retired`.
- **Coordinator (session_coordination.py):**
  - Barriers noted on the releasing lease move into `_lease_end_pending`, with a 40 s deadline, at
    the three handoff sites (2612, 2193, 2687). A barrier that has already answered by then is
    settled and does not move.
  - Every predicate that reads `_handoff_pending` (500, 520, 1151, 2864, 3417, 3434) also requires
    `_lease_end_pending` to be empty.
  - `_expire_due` (2425-2438) settles overdue entries as unconfirmed and writes an audit row.
  - Status (1929-2001) gains `lease_end: {state, pending, deadline_in_s}`, outside
    `cleanup_degraded`.
  - `release` still answers 200 `released: true`, plus `lease_end: confirming`.
- **Why non-blocking.** Waiting inside `cleanup_owner` would hold whichever thread triggered the
  release, and expiry runs on `/poll` handlers. `record_poll` → `reap_expired_commands` (4409) or
  `_finish_operations` (4683) → `finish_operation_exact` / `discard_committed` → `_expire_due`
  (session_coordination.py:1711, 1680). That would hold the poll answer that carries that peer's
  barrier. Reusing `CleanupDisposition` waits at 2565 with the condition held, which stalls
  status and the poll path's coordinator calls up to the 30 s cleanup timeout (268).
- **control_client:** `_require_recovery_idle_status` (830-857) treats `lease_end.state ==
  confirming` as a retriable wait, with a new code `session_handoff_confirming` bounded by
  `deadline_in_s`. It treats `unconfirmed` as a conflict.

**Costs.**
- **Release latency:**
  - `release` returns at once;
  - the next grant waits for one poll and the barrier's answer (≤ 200 ms at 5 Hz, plus RTT; both
    peers in parallel), plus whatever A left active on that peer: server jobs ≤ 5 s
    (MCPBridge.c:9), client jobs and controls ≤ 35 s (1.7);
  - an untouched peer costs nothing, and a lease that delivered no mutation hands off as today.
- **Bound:** the barrier's cap is 36 s from its dispatch; the coordinator's bound is 40 s from the
  handoff (the cap, plus delivery and RTT).
  - Everything cleanup does not stop ends within 35 s of its own dispatch (1.7), so it is waited
    out. `action_hold` (≤ 128 s) is cancelled before the barrier.
  - The one exception is `ui_dialog` (≤ 245 s), which has no lease-end release. A dialog that A
    left open ends as `lease_end_unconfirmed` (`jobs_open`). That is accurate: the dialog is still
    on screen when B is granted.
- **Client not polling (loading, hung, minimized):** costs something only if the client was
  touched. Then the bound runs out and the handoff is unconfirmed. FIFO still dispatches A's
  leftovers and releases before B's first command on that peer, but their effects can overlap.
  That is what `lease_end_unconfirmed` tells B.
- **Dead game:** the reaper retires an all-dead run within about 30 s. If that happens before
  the bound, the barrier settles as `retired`, with no degradation. A dead client whose server is
  still alive is not reaped (7710-7724): the bound runs out and the handoff is unconfirmed, which
  over-reports but is safe.
- **New peer instance:** replace-role retires the old binding after terminating it, which settles
  the barrier. The new instance never received A's commands.
- **Lost barrier answer or lost poll answer:** the bound runs out and the handoff is
  unconfirmed. One answer per peer matters, not one per command.
- **Daemon restart:** bindings are memory-only (instance_fence.py:1-5), and a new daemon
  generation invalidates every earlier lease (runtime_state.py:1624-1647,
  `daemon_restart_invalidated`). Pending barriers end with them, and the game's old `inst=` can
  no longer answer. As today, nothing confirms A's leftovers across a restart; that is out of
  scope.

**Recovery: confirming versus degraded.**
- **Confirming** is not a fault: `lease_end.state: confirming`, `claimable: false`,
  `cleanup_degraded: []`. Acquire answers 202 queued, as during `_handoff_pending`, and recovery
  waits.
- **Unconfirmed** (bound, refused, `jobs_open`, or quarantine) is a real degradation:
  - an audit row `session_lease_end_unconfirmed` (instance, peer, barrier id, reason);
  - `lease_end_unconfirmed` in the `cleanup_degraded` of the next grant;
  - `lease_end.state: unconfirmed` until that grant;
  - recovery treats it as a conflict, as it does `hold_cancel_unconfirmed` today.
- **Retired** means confirmed by absence: no degradation.

**#263, #266 and long jobs.**
- Their lease-end releases are queued before the barrier, so the barrier confirms them.
  `*_enqueued` keeps its meaning; `*_unconfirmed` stays for quarantine and enqueue failure.
- #263's path player and #266's drive join the amendment-2 list, so the barrier confirms that they
  stopped, not only that their release ran.
- Abandon-time releases (`RELEASE_REGISTRY`) now run after their source, which main does not
  guarantee (T4, T5). Those queued after the barrier still dispatch, because it holds nothing
  back, and they can end a job early.
- Long jobs are waited for within the cap; past it, the handoff is unconfirmed.
- Timed-out tool: unchanged. It still gets the `ToolError` timeout and the abandon outcome, and the
  late result is still discarded. Only the next lease waits.
- **What #263 and #266 could become** (a follow-up, not this PR):
  - With exclusivity plus confirmation, a lease-end stop need not name its play or drive. No
    other lease can have started one since the last confirmed handoff, and after an unconfirmed
    handoff the stop still runs before B's first command on that peer.
  - So #263's play ledger (2106, 3458-3527, 4814, 5128) and #266's drive ledger (2173,
    3527-3580, 4868) could become one unscoped stop at lease end, or the barrier could stop
    lasting effects itself.
  - The scoped `RELEASE_REGISTRY` entries stay for abandons in the middle of a lease.
  - Independently of this design, both ledgers assume execution in delivery order. #263 prunes
    plays below one that started (3468-3495); #266 keeps the latest delivered drive (3527-3547).
    That assumption is false on main (T4), so FIFO is a prerequisite for #263 and #266 anyway.

### Comparison

| | A debts | B1 epoch | C FIFO + barrier |
|---|---|---|---|
| Original gap | closed, except a lost answer past the bound | ordered before B's bridge commands only | closed (or explicitly unconfirmed) |
| Y14 | open | open | closed (or explicitly unconfirmed) |
| PBO / version bump | no | yes | yes |
| Normal handoff latency | the late command itself | none | about 1 poll, plus what A left active (≤ 35 s) |
| Liveness exposure | one answer per delivered command | none | one answer per touched peer, bounded |
| Sealed files | none | none | none |

### Recommendation

**C**, for five reasons:
1. It is the only design here that closes both gaps.
2. Its bridge change, FIFO, is needed anyway: #263's and #266's ledgers and main's abandon-time
   releases depend on it.
3. It needs one answer per touched peer, bounded and retirement-aware, so it has no per-command
   liveness hole.
4. The Python state is one pending set next to `_handoff_pending`, with no blocking and no lock
   inversion.
5. Tool semantics are unchanged, and no sealed file is touched.

A is simpler (Python only) but leaves Y14 open, and it still needs FIFO to close Y14. B cannot
close either gap.

## 4. Viability criteria for C

**4.1 Offline fixtures.** Use the `_owned_state` wiring (both peers bound) with `FakeClock` as
`time_fn`.

Positive:
- `test_abandoned_delivery_waits_for_the_barrier`: T1. Release gives `lease_end.state ==
  "confirming"` and `cleanup_degraded == []`; the server queue is exactly `[lease_barrier]`;
  B gets 202; `status.claimable is False`. After the barrier's `ok` from `INST_SERVER`, B gets
  200. The late X result is `discarded`.
- `test_unanswered_delivery_waits_for_the_barrier`: T2, with the same asserts.
- `test_lease_end_release_runs_before_the_barrier`: T3. The client queue is
  `["vehicle_release", "client_lease_barrier"]`; the release's result alone leaves B at 202. Run
  the same case on #263 (`camera_path_release`) and on #266 (`survivor_drive_release`).
- `test_a_delivered_hold_is_confirmed_by_the_barrier`: a delivered `action_hold`, then release.
  `cleanup_degraded == []` (no `hold_cancel_unconfirmed`); the client queue is
  `["action_hold_cancel", "client_lease_barrier"]`; B stays at 202 until the barrier's `ok`.
- `test_only_touched_peers_get_a_barrier`: a server-only lease leaves the client queue empty.
- `test_a_lease_without_mutations_hands_off_at_once`: with no commands, or with read-only
  commands only, there is no barrier and B gets 200 at once.
- `test_retirement_confirms_by_absence`: `retire_role(server)` while a barrier is pending gives B
  200 with no degradation. A late result from that instance gets 409 `binding_retired`.

Negative:
- `test_an_answer_from_another_instance_confirms_nothing`: B stays at 202.
- `test_a_failed_barrier_is_unconfirmed`: an `ok: 0` / `bridge_queue_full` answer grants B with
  `cleanup_degraded == ["lease_end_unconfirmed"]`, plus the audit row.
- `test_the_bound_completes_unconfirmed`: advancing the clock past 40 s lets the next coordinator
  call grant B unconfirmed. The barrier stays ahead of B's first command in that peer's queue.
- `test_quarantine_queues_no_barrier`: T1's delivery, then quarantine, then release. No barrier
  is queued, the degradations include `lease_end_unconfirmed`, and B is granted at once.
- `test_fenced_cleanup_queues_no_barrier`: `cleanup_owner` for a lease that is not releasing
  returns `cleanup_fenced` and queues no barrier.
- `test_expiry_on_the_poll_path_does_not_hold_the_poll`: a lease that expires through
  `record_poll` (4409, 4683) still gets its poll answer, with the barrier in it, without waiting.
- `test_recovery_waits_while_confirming` gives `session_handoff_confirming`;
  `test_recovery_conflicts_when_unconfirmed` gives `session_transition_conflict`.

Bridge (with `translate`, both bridges):
- `test_pending_drains_in_delivery_order` (T4 → `1..16`);
- `test_a_new_batch_never_overtakes_pending` (T5 → `1..14`);
- `test_world_spawn_deferral_stays_in_order`;
- `test_barrier_answers_only_when_idle`: with one job open, and separately with an active
  `MCPCarDrive` or held input key, the barrier does not answer while a later command is still
  dispatched. When the job or control ends, the barrier answers `ok`. An active sampler does not
  delay it;
- `test_barrier_cap_answers_jobs_open`: a `ui_dialog` job still open 36 s after the barrier's
  dispatch gives `ok: 0`, `error: "jobs_open"`, naming `ui_dialog`.

Source contracts: version "13" on both sides; the capability lists name the barriers; no tool
table (`server._BRIDGE_COMMAND_TOOLS`) exposes them.

**4.2 Existing tests that must keep passing.**

Unchanged:
- leases and handoff: `test_broker_binding_abandon.py` (whole module), `test_lease_queue_fifo.py`,
  `test_coordination_authority_fence.py` (984, 1033, 1087), `test_coordination_audit_faults.py`,
  `test_fb_00c4_audit_stage.py`, `test_session_status_blocked_on.py`,
  `test_pleno_lease_and_orphans.py`;
- quarantine and lifecycle: `test_retail_quarantine.py`, `test_loopback_authority_quarantine.py`,
  `test_process_lifecycle.py`, `test_process_lifecycle_authority.py`,
  `test_client_process_gone.py`, `test_idle_warden.py`, `test_daemon.py`;
- recovery: `test_control_client.py`, `test_dayz_test_tool.py`;
- bridge contracts: `test_guards_bridge.py`, `test_poll_watchdog_contract.py`,
  `test_fb_f47b_release_render.py`, `test_messages_contract.py`.

Leases that touch no peer must hand off exactly as today, so these stay green as they are. So do
`test_action_hold.py:511-569`, which filters the queue by name and builds a fresh state for the
second lease, and 571-605, which runs under quarantine, where no barrier is queued and
`hold_cancel_unconfirmed` stays.

Changed on purpose, with review:
- `test_task9_spawn_phase_markers.py:158-182/217-222`, which pins the `OnPollSuccess` loop text.
  Keep its "deferred FIFO" assertions and update the literal.
- Tests that assert the exact queue after a release, or an immediate next grant after a lease
  that delivered mutations: the `vehicle_release`/`bot_stop`/hold cases in `test_loopback.py`,
  `test_bot_verbs.py` and `test_action_hold.py`; #266 `test_survivor_drive.py:532-556` and 1489;
  #263's lease-end tests. These gain a trailing barrier and must answer it.

**4.3 In-game check (final round).** Use DayZDiag server and client with the v13 PBO and two MCP
sessions, A and B, under the session protocol: lease, lifecycle guard, no direct kills.

1. **Abandoned delivery.**
   - Send: A sets `/set_poll_delay {"ms": 4000}`, then sends `world_spawn` with a 1 s timeout
     (the tool returns `timeout waiting for world_spawn id=X`). A releases at once. B acquires.
   - Read:
     - A's release shows `lease_end: confirming`, and B gets 202 at first;
     - the server script log shows `result posted id=X` before `result posted id=Y` for the
       barrier, with Y > X;
     - the daemon log shows `RESULT id=Y`;
     - `events.jsonl` shows confirmation before B's `session_granted`.
   - Pass: B's grant comes after `RESULT id=Y`, and B's first `entities_query` finds the object.
2. **Y14.**
   - Send: A sends `vehicle_control` (throttle, `hold_ttl_s` 20), or on #263 a long
     `camera_path_play`. A releases.
   - Read: the client log shows the release's `result posted` before the client barrier's.
   - Pass: B is granted after that. B's first `vehicle_telemetry` shows throttle 0, or two
     `camera_get` readings 1 s apart are equal.
   - Variant with no release: A leaves a 10 s `player_move` hold. B is granted only after that
     job's `result posted` line, then the barrier's.
3. **Order.**
   - Send: 16 `camera_get` (read-only, client) through `/enqueue` within one 200 ms poll
     interval, then the same with `query_player_state` (server).
   - Pass: `dispatch_tick` never decreases with increasing id.
4. **Latency.**
   - Pass: release → B's grant takes ≤ 2 poll intervals plus RTT with a polling client, and a
     server-only lease queues no client barrier.

The not-answering and retirement paths stay offline (4.1). Forcing them in game would mean
killing a DayZ process outside the lifecycle guard, or getting a lease that the pending phase
withholds.
