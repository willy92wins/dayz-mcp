<!-- CABECERA DE LA REVISIÓN 3 — leer antes que el cuerpo -->

# PR del transporte: el fin del lease espera al juego (diseño, revisión 3)

**Qué es esto.** Es la revisión 3 del diseño, sobre main `2e844c5` más #261 en `f51fc3e` (punta `d940503`). Responde a la revisión r2 de Codex Sol, RED con los hallazgos H1 a H13. El cuerpo, en inglés, sustituye entero al de la r2. Donde discrepen, manda esta cabecera.

**Decisión de Guillermo (2026-10-24 10:07Z):** «PR aparte». Lo revisa Codex Sol y lo mergea Guillermo, porque toca el PBO.

**La decisión: el diseño D** (§2 del cuerpo).
- Al acabar un lease con trabajo, cada peer conocido recibe un cierre interno: `lease_close` en el servidor y `lease_close_client` en el cliente.
- El cierre para en su dispatch todo lo que controla su bridge. Después contesta un censo del estado real, no de los jobs.
- Suelos de ids en `player_move` y en raise/fire. RPC de manos e inventario registrados por id hasta confirmación. Hold censo en los dos lados, sin interrupt tardío.
- Si una prueba tarda, un job observador espera hasta 25 s. El coordinator guarda una fila por (run, peer). El grant espera 40 s. Una fila sucia queda como deuda hasta un cierre limpio o la salida del proceso.
- FIFO en ambos bridges. Routing interno de los dos nombres de cierre, fuera de la whitelist. Versión del bridge 13.
- Un cierre ya entregado solo suelta copias y peticiones con id ≤ F o ≤ W. No hay valla de admisión por destino.
- `cleanup_owner` ya no encola `vehicle_release`, `bot_stop` ni `action_hold_cancel`. Los releases del abandono se quedan.

**Mapa H1–H13 de la r2 → secciones:**
- **H1** (arma): suelos W como F. §2.6, §2.7, §2.8, S4.
- **H2** (hold servidor): censo sin interrupt; fence con los dos lados. §2.7, §2.9, C11.
- **H3** (RPC manos/inventario): registro por id de petición, independiente del job. §2.7, S6.
- **H4** (arranque recuperado): siembra tras manifiesto y antes del fault; embedded fuera. §2.12, §7.
- **H5** (cierre entregado tarde): alcance por identidad; FIFO HTTP; no valla por destino. §2.5, §2.8, K8.
- **H6** (`lease_end_unconfirmed` sobrescrito): unir degradaciones en todos los payloads. §2.11, K21.
- **H7** (infected): deuda hasta salida del proceso; K27/G5. §2.7, S2, §6.
- **H8** (routing): tabla interna antes de `CLIENT_COMMANDS`. §2.16, K32.
- **H9** (hold tras reemplazo): fence por instancia; deuda `hold_server_unverified`. §2.9, K16.
- **H10** (pruebas de borrado): referencias al registro del bridge. §2.7, S5, K30.
- **H11** (muestreadores): abort en el cierre cliente. §2.6, C13, product-spec.md:122.
- **H12** (aserción spawn): cambiar `test_task9_spawn_phase_markers.py:220`. §5.4, K24.
- **H13** (recuperación en releasing): garantía solo tras `_releasing = None`. §2.13, K20.

**Condiciones de las preguntas (r2 §1) y citas del §4:** Q1 en §2.12; Q2 en §2.11 y §6 G6; Q7 en §2.10; Q11 en §2.6 y K31; Q13 en §2.1 y §2.5; Q15 en §2.3 y §2.8. Citas D:561 → §2.16; D:648-649 → §4 #266; D:501 y D:509-510 → §2.12 y §2.13; D:754-755 → §5.4; D:604 → S1 y §4; D:366-370 → §2.7; D:443-448 → §2.10.

**Parciales de la r1, cerrados aquí:** H1 r1 (canales que actuaban después) con H1–H3 r2. H2 r1 con H5 r2. H3 r1 con H3, H6 y H7 r2. H4 r1 con H4 r2. H5 r1 con H2 y H9 r2. H10 r1 con H13 r2. H12 r1 con el censo y G5.

**Fuera de alcance, con su seguimiento:**
- **H7 de la r1, P0 heredado.** PR de seguimiento: mutaciones de jobs al dispatch. Casos y criterios: cámara, get-in, y mantenimiento y timeout del hold. Compuerta: hasta que se mergee, ni el cuerpo ni el CHANGELOG dan por cumplida la regla de los jobs. §7.
- **Embedded (H4 r2):** no tiene coordinator (`server.py:990-1004`; `loopback.py:6808-6815` y `2030-2032`). §2.12 y §7.
- La sonda nativa de `infected_drive` sigue como seguimiento; la deuda no se borra al vaciar el registro.
- #263, #266 y #267: solo el contrato del §4.

**Orden de merge.** #261 ya está en main (`f51fc3e` desde `d940503`). #264, #265 y #262 entran antes, y este PR los cubre (§3). #263, #266 y #267 entran después. La versión 13 obliga a esos tres a rebasar y a construir el PBO nuevo. LIVE y T130 se resellan en la ronda final (#244).

**Los tests T1 a T5** del repro de la r1 siguen siendo K1, K2, K3, K22 y K23. Fallan en main.

**Decididas aquí (Claude, sobre lo que Grok dejó como pregunta):**
- El id de arma lleva su propio contador del cliente, siempre creciente, no el de `player_move`: F y W se informan por separado y compartirlo no aporta nada (§2.6).
- `weapon_floor` va por jugador con la misma identidad que `move_floor`: `floors[{net_low, net_high, move_id, weapon_id}]` (§2.16).
- El registro de RPC vive en Enforce, en el bridge que envía el RPC, por id de petición; Python solo ve las filas del censo (§2.7).
- Los 25 s son el tope del observador, no una prueba: lo que siga vivo queda como residuo `deletion_pending` y lo miran los cierres siguientes (H10). La duración real de `ObjectDelete` se mide en la ronda final.
- El PR de seguimiento de H7 r1 no bloquea el merge de este. Bloquea que el cuerpo o el CHANGELOG den por cumplida la regla de los jobs (la compuerta de arriba).
- El release del cliente ya lleva el id del movimiento, y el servidor solo suelta esa copia (4_World/MCP_PlayerMove.c:605-609, 794-798 y 935-952), así que H5 no añade nada ahí (§2.5).

**Preguntas para la revisión 4:**
1. ¿Basta leer `m_CurrentActionData` y `m_PendingAction` del ActionManagerServer del jugador (V130 actionmanagerserver.c:3-4 y actionmanagerbase.c:61) como predicado de residuo, o hace falta un helper propio en el mod?
2. ¿Qué RPC, además de `hands_take`, `hands_attach` e `inventory_move`, deben entrar en la tabla del §3 en este PR?
3. ¿Un stop de bot rechazado deja siempre residuo `bot_stop_rejected`, o hay un camino vanilla que igual llega a idle (V130 bot.c:331-341)?

---

# Lease-end close: design (revision 3)

**Base:** main `2e844c5`. Citation conventions:
- A bare `file:line` is at `2e844c5`. Python files are under `tools/dayz_mcp/` and tests under
  `tools/tests/`. Enforce files are under `addon/scripts/5_Mission/` unless written `4_World/…`.
- A PR head is written `#261@d940503 MCPBridge.c:607`. Heads: #261 `d940503` (already in main at
  `f51fc3e`), #262 `796f25d`, #263 `28075ac`, #264 `92336cb`, #265 `c4ba671`, #266 `b4f0611`,
  #267 `1dde8b9`. All of them are at bridge version "12".
- `V130/` is the vanilla 1.30 scripts tree. Vanilla is cited by path and line, never copied.
- **[DESIGN]** marks a choice this plan makes. **[UNVERIFIED]** marks a claim not checked by
  reading.

**Sealed files.** None is touched. The 21 files in `tools/packaged-modules.lock.json` are other
`tools/dayz_mcp/` modules (for example `server_cli.py`, `daemon_contract.py`) and the native
launcher. No file in §2.17 is among them.

## 0. Summary

r1 proposed a barrier that waits for jobs and a fixed list of controls. r2 kept FIFO, persistent
debt and the move floor, and left channels that can still act after a clean grant: weapons (H1),
server actions (H2), hands/inventory RPC (H3), a recovered lifecycle fault (H4), a delivered close
that runs late (H5), overwritten `lease_end_unconfirmed` (H6), infected debt cleared by emptying
the registry (H7), close routing (H8), holds after instance replace (H9), deletion jobs (H10),
samplers (H11), a pinned spawn assertion (H12) and recovery during releasing (H13).

Design D (revision 3) does four things:
1. **Stop, then prove.** At lease end every known peer gets one internal close. Its dispatch stops
   every controller its bridge owns (§3). Its answer is a census of the real state.
2. **Debt that persists.** The coordinator keeps one row per (run, peer). Only a clean close or the
   process exiting clears a row. Every grant while a row is owed says `lease_end_unconfirmed`.
   Degradations merge; they never overwrite the marker.
3. **Order and identity.** Both bridges dispatch HTTP in delivery order. A delivered close only
   releases move copies with id ≤ F and weapon requests with id ≤ W. Raise/fire carry a
   monotonically increasing client id. There is no per-destination admission fence for B.
4. **Census covers the leftover channels.** Weapon floors, server ActionManager without interrupt,
   RPC-in-flight by request id, deletion refs, sampler abort, infected debt until process exit.

## 1. Current mechanics at 2e844c5

### 1.1 Delivery and results
- `record_poll` (loopback.py:4429-4728) walks the destination queue in order. It holds back
  commands for the other peer (4613-4615), lease commands for an unaccredited peer (4624-4630) and
  `action_hold` on a hold-blocked run (4657-4663).
- A lease-requiring command passes only through one of three branches (4664-4692):
  - retail quarantine discards it (4672-4673);
  - owner-cleanup names and internal fire-and-forget rows pass (4674; `_cleanup_dispatch_locked`
    4263-4271);
  - everything else needs `claim_dispatch` (4678-4684; session_coordination.py:1499-1515).
- A pin mismatch discards the command as `destination_changed` (4697-4704). Then the row is marked
  `delivered` (4705-4707).
- Lease-requiring means "not in `READ_ONLY_COMMANDS`" (session_coordination.py:21-51, 99-100).
- `store_result` (loopback.py:4917-5041) applies the instance fence first (4932-4981):
  - retired instance: 409 `binding_retired`, except the hold command's own result (4935-4948);
  - another instance: 200 `discarded`, with an audit (4949-4966);
  - same instance under a newer station epoch: an audit only (4967-4981).
- After the fence: an unknown or past-deadline id is discarded (4982-4996), a fire-and-forget id
  is dropped (4997-5005), anything else is stored (5006-5017). The hold note runs in all three
  cases (5018-5020).

### 1.2 Abandon and owner attribution
- `_abandon_row_locked` (4162-4217) forgets a delivered row's tracking (4273-4279), fences a
  delivered hold (5230-5242) and publishes the registered release (`_publish_release_locked`
  4281-4343, registry 148-159).
- That release keeps the owner's attribution (4323-4326). `pending_for_owner` counts every
  `_command_owner` entry of the session (5114-5120), and status reports it as `pending_commands`
  (6075-6077).
- So `pending_commands == 0` right after an abandon holds only when no release was registered, as
  in T1 (H14). With a registered release it stays 1 until that release's result.

### 1.3 Lease end
- `release`, admin release and TTL expiry all end in `_release_active_locked`
  (session_coordination.py:2476-2649):
  - the WAL is armed (2480-2486), then `_active = None` and `_releasing = lease` (2490-2491);
  - the cleanup hook runs in a worker while the condition is released (2509-2548). It can be
    refused for saturation (2527-2530), fail to start (2549-2553), time out after 30 s (268,
    2555-2557) or raise (2558-2559);
  - then `_releasing = None` and `_handoff_pending = True` (2612-2616).
- The daemon's hook is `cleanup_begin` (daemon.py:89-120): `cleanup_owner` (97), then
  `begin_release_owner` (101).
- `cleanup_owner` (loopback.py:5280-5383):
  - checks validity and authority (5288-5302);
  - discards queued lease commands (5303-5305); internal releases survive (5090-5092);
  - closes queued holds (5307), and fences or abandons delivered ones (5308-5341);
  - returns early when no vehicle and no bot is active (5342-5345);
  - otherwise queues `vehicle_release` and `bot_stop` as internal fire-and-forget (5358-5382).
- The grant predicates read `_handoff_pending` and coordinator state only: preferential reacquire
  (495-506), acquire (515-522), wait (1146-1154), claim (2859-2870) and `claimable` (3429-3440).
  None of them reads anything about the game.
- `begin_release_owner` fences the run (`fence_runs`, loopback.py:2704-2720), which drains every
  queued command that is not exempt (2653-2678; exemption 2648-2651).

### 1.4 Bridges
- Server `OnTick` (MCPBridge.c:131-166) ticks bots (135), jobs (143) and `DrainPending` (144). It
  keeps one poll in flight (146) and starts none while more than 8 commands are pending (151;
  constants 3-9).
- `OnPollSuccess` dispatches the first 4 commands of a batch inline even when `m_Pending` is not
  empty (336-347). Only a `world_spawn` and what follows it in the same batch are deferred
  (325-339).
- `DrainPending` takes `Get(0)` and then `Remove(0)` (362-363). Client twin: `OnPollSuccess`
  848-897 (inline 887-890) and `DrainPending` 1019-1034 (`Remove(0)` at 1030).
- Vanilla `Remove(i)` moves the last element into the hole, and `RemoveOrdered(i)` keeps the order
  (V130/1_core/proto/enscript.c:458-470). Vanilla itself relies on `RemoveOrdered`
  (V130/3_game/entities/entityai.c:3265-3273). So a new batch overtakes pending commands, and a
  drain reverses part of the queue (T4, T5).
- **Where jobs end (H15).** The client runner posts a ready job as success or failure
  (MCPJobRunner.c:146-158) and an overdue one through `MCP_PostJobTimeout` (159-164; handlers
  MCPClientBridge.c:5896-6010). The server does the same in `ProcessJobs` (MCPBridge.c:5151-5202;
  `PostJobTimeout` 5394). A job's end proves only that its own check passed. It says nothing about
  state the job did not check.
- Results are async POSTs with no retry. `PostResult` logs `result posted id=…` as it sends
  (MCPBridge.c:5509, 5540; client MCPClientBridge.c:7273).
- §3 lists the controllers each bridge owns.

### 1.5 Holds
- A delivered hold fences its run until the hold's own protocol-1 result
  (`_fence_hold_open_locked`, loopback.py:5150-5163). A cancel acknowledgement never clears that
  fence (5244-5251). The hold note reconciles even a discarded result (5018-5020, 5264-5278).
- On the client, a hold observation says `cleanup_complete` only when the flag is restored and the
  action data is no longer local (4_World/MCP_ActionHold.c:400-404). `Take` clears the active
  state at once (433-449), so `IsActive() == false` proves nothing about the action manager.

### 1.6 Bindings, retirement, restart
- Bindings live in memory only (instance_fence.py:1-5). After a restart, a live game is
  `unbound_after_restart` (loopback.py:4482-4483). It gets no mutations until its run is
  relaunched (instance_fence.py:35-38).
- A new daemon generation invalidates every earlier lease (runtime_state.py:1624-1647). Nothing in
  the game is stopped.
- Retirement keeps the result fences of dispatched ids (loopback.py:2635-2636).
- Every lifecycle caller of `_commit_retirement` (process_lifecycle.py:2376-2388) sets the run
  `EXITED` first (5551, 7450, 7577, 7666, 7761, 8425-8426). Replace-role terminates the old
  process before it retires that role (4191-4205, 2286).

### 1.7 Recovery
- `_require_recovery_idle_status` (control_client.py:830-857) needs all of these: own state
  `none`, `pending_commands == 0`, no audit or lifecycle fault, an empty `cleanup_degraded`, and a
  generation.
- `_reconcile_idle_session_locked` (859-922) runs that check before and after the cancel.
  `session_acquire_wait` reconciles first (971). The lifecycle tools turn
  `session_transition_conflict` into their own errors (dayz_test_tool.py:816-846).
- Status `cleanup_degraded` holds only what that status call's own expiry produced
  (session_coordination.py:1931, 1999).

### 1.8 The race as tests

The r1 scratch repro (kept out of the repo) drove the real broker and coordinator in memory. It
was observed on main `f8cf3b4`. The mechanisms it hits are unchanged at `2e844c5` (§1.1-§1.4),
but it was not re-run there [UNVERIFIED].

| # | Steps | Observed on main | Expected with D |
|---|---|---|---|
| T1 | A acquires → `object_delete` delivered → `request_abandon(tool_timeout)` → A releases → B acquires | B got 200 at once | B gets 202 until both closes settle clean, then 200 |
| T2 | Same, without the abandon | B got 200 at once | Same as T1 |
| T3 | `vehicle_control` delivered and answered → release (`vehicle_release_enqueued: 1`, client queue `["vehicle_release"]`) → B | B got 200 with the release still queued | Client queue `["lease_close_client"]`; B gets 202 until it settles |
| T4 | Client `OnPollSuccess` and `DrainPending` on a 16-command batch | order `1,2,3,4,5,16,15,…,6` | order `1..16` |
| T5 | A batch of 12, one drain, then a batch of 2 | order `1,2,3,4,5,12,11,10,13,14,9,8,7,6` | order `1..14` |

## 2. Design D

### 2.1 Flow
1. A lease ends. If it delivered work, or a row is already owed, the coordinator opens a **round**:
   one row per known (run, peer). **Delivered work** is an accumulated mark of the lease, set at
   the effective delivery authorization under the same exclusion as `claim_dispatch`
   (session_coordination.py:1499-1515) and the broker's delivered transition (loopback.py:4705-4714).
   It survives the tool result and abandon (`finish_operation_exact` 1702-1720 drops
   `committed_commands`; the mark does not) [DESIGN, Q13].
2. The hook (`cleanup_owner`) publishes one close per row it can reach and reports each row.
3. Each bridge dispatches its close in FIFO order. The close stops everything, then answers a
   census, directly or through an observer job.
4. Each answer settles its row. The next grant waits until no row is outstanding, or until the
   round's bound. A row that did not settle clean stays owed.

### 2.2 Identity
- **Round:** an id the coordinator mints at each lease end that opens one [DESIGN].
- **Close identity:** daemon generation, round, ending lease id, instance, peer, the binding's
  epoch and the close id.
- **Args:** `close_generation` and `close_lease_id`; the server close also takes `close_floors`
  (move F and weapon W per player, §2.8).
- **Result:** the census echoes generation, lease id and peer. The client census also reports
  `move_floor` F and `weapon_floor` W.
- **Clean settle** needs all of these:
  - the close id is the row's current close;
  - the presented instance is the sealed one (the fence, loopback.py:4932-4966);
  - the echo matches exactly;
  - the binding's own epoch still equals the epoch sealed at publish (`_seal_command` 2954-2963).
    A different process presenting the same token bumps it (4746-4748, 4756-4758).

### 2.3 Rows and rounds (the debt)

Row states: PENDING (registered, waiting for the hook's report or for its publish), CLOSING (close
id), OWED (reasons, since) and clear (no row). A row is **outstanding** while it is PENDING or
CLOSING and its deadline has not passed.

| From | Event | To |
|---|---|---|
| none or OWED | lease end with delivered work or any OWED row; registered in `_release_active_locked` right after 2490-2491 | PENDING (round, deadline = `_time_fn()` + 40 s) for every known peer and every owed key |
| none | daemon start, live run (§2.12) | OWED (`daemon_restart`) |
| PENDING | hook report: close published | CLOSING (id) |
| PENDING | hook report: server close deferred (§2.4) | PENDING, published later |
| PENDING | hook report: destination unreachable (not BOUND, version-blocked, quarantine) | OWED (that reason) |
| PENDING | hook finished without naming the row | OWED (`close_not_reported`) |
| PENDING | hook refused for saturation, failed to start, or raised (2527-2530, 2549-2559) | OWED (`cleanup_worker_saturated` or `cleanup_failed`) at once |
| PENDING | hook timed out (2555-2557) | stays PENDING; a late report before the deadline applies |
| CLOSING | outcome clean | clear |
| CLOSING | outcome residue | OWED (its entries) |
| CLOSING | outcome rejected (`ok: false`, for example `client_not_in_game`) | OWED (`close_rejected:<error>`) |
| CLOSING | outcome discarded (`lease_close_stale`, `destination_changed`, `binding_retired`, stale expiry, `lease_close_superseded`, `retail_quarantine`) | OWED (`close_discarded:<reason>`) |
| PENDING or CLOSING | deadline passes | OWED (`close_timeout`), no longer outstanding |
| OWED, same round and close | late clean outcome | may clear only while identity still matches; a late result can arrive after the row is already OWED (Q15; D:263 allows it) and does not reopen a grant already issued |
| server row of a run, cleared or CLOSING | that run's client close reports a move floor above the server's (§2.8) | OWED (`player_move_floor_unapplied`) |
| server row of a run, cleared or CLOSING | client reports a weapon floor W above the server's | OWED (`weapon_floor_unapplied`) |
| any | outcome for a close that is not the row's current close | ignored and logged |
| any | `note_processes_gone(run, role)` | removed |

Rules:
- Nothing becomes outstanding after its deadline. A late hook report after it does not reopen the
  row.
- One round is current at a time. A new round supersedes the closes of older ones (§2.4).
- The client row of a run is still outstanding when its floor report arrives, so no grant can
  sit between a cleared server row and its floor check. **Q15:** D:272-273 of r2 said a late
  clean always still finds the client row outstanding. A late result can arrive with the row
  already OWED. K17 tests both.

### 2.4 Publishing a close
- `cleanup_owner` asks the coordinator for the round (`lease_end_round(session, lease)`). For each
  row it either publishes a close or reports the row as unreachable. It does this under the broker
  lock, with the authority recheck it already does (5358-5364), and ends the same lock section
  with `note_close_round(...)`.
- `_publish_close_locked` works like `_publish_release_locked` (4281-4343): full pin, seal,
  `_enqueued_at`. It differs in four ways [DESIGN]:
  - there is no `MAX_QUEUE` check (4296-4300 has one);
  - the id is not fire-and-forget, because its result is needed (4997-5005 would drop it);
  - there is no owner, so `pending_commands` never counts it (5114-5120);
  - it is fence-exempt: `_command_exempt_from_fence` (2648-2651) also accepts close ids. So the
    RUNNING_IDLE drain (2653-2678) and the fenced-poll filters (4561-4568, 4588-4597) keep it.
- **One queued close per (instance, peer).** A new publish first discards the older queued one as
  `lease_close_superseded`.
- **Unreachable destinations become debt at once:** a binding that is not BOUND, a binding whose
  last poll the version block refused (4546-4547) [DESIGN: the broker keeps that verdict per
  instance], and retail quarantine. No close is published for them.
- **Order per run.** The client close goes first. The server close is published when that run's
  client row resolves, or 10 s after the round opened, whichever comes first [DESIGN]. The broker
  checks this on every poll and result, with its own clock (`_now`, 2096-2097). A run with no
  client row gets its server close at once.

### 2.5 Delivery and settlement
- **Delivery gate.** The close names are lease-requiring, so they enter the lease branch
  (4664-4692), where quarantine discards them (4672-4673). A new branch before 4674 lets a close
  pass only while `lease_close_deliverable(round)` holds: no active lease, no grant in flight, and
  its round is current. Otherwise it is discarded as `lease_close_stale`. The pin check (4697-4704)
  still applies.
- **Identity of a delivered close (H5).** HTTP work on a bridge is FIFO, so B's HTTP work on that
  bridge runs after an older close already delivered there. Only other channels can overtake:
  `player_move` start by input (MCPClientBridge.c:2835-2844) and weapon raise/fire. The server
  close releases only the move copy whose id is ≤ F, and only weapon requests whose id is ≤ W.
  B's ids are greater. **Reject** a per-destination admission fence for new mutations while an
  older close is still executable: it would block B, and FIFO plus floors already cover HTTP and
  those two channels [DESIGN]. The client release already carries the move id: `ReleaseAll`
  stores the move's generation as the release id and `FlushServerRelease` writes it
  (4_World/MCP_PlayerMove.c:605-609, 794-798). The server ends only the copy with that id and
  notes `release_of_another_move` otherwise (`MCPMoveReadRelease`, 935-952). Nothing to add.
- **At delivery** the broker marks the close delivered and captures the hold records of that
  instance that are open or fenced (`_holds`, 5138-5173) [DESIGN].
- **Results.** The fence runs first (4932-4981). A close id then takes its own branch before the
  generic ones (4982): the broker parses the census into one outcome and drops the ledger entry.
  It stores nothing and answers 200. A malformed census is residue `census_malformed`.
- **Retirement, exactly (H13):**
  - a close still queued when its instance retires is discarded as `binding_retired`. Its fence
    goes with it (`_mark_discarded`, 4856), so a late result gets 200 `discarded` as an unknown id
    (4982-4996);
  - a delivered close keeps its fence (2635-2636), so a late result gets 409 `binding_retired`
    (4935-4948). The broker records `binding_retired` for it at retirement;
  - the own-hold exception (4942-4947) never applies to a close id.
- **Outcome flush.** Outcomes are collected under the broker lock and passed to the coordinator
  after the lock is released, as `finish_operation_exact` (5022-5036) and `_finish_operations`
  (4726) are.

### 2.6 The client close (`lease_close_client`)

The close passes the existing not-in-game gate first (MCPClientBridge.c:1098-1104). A client that
is not in game answers `client_not_in_game`, calls nothing in the engine, and its row becomes
OWED (`close_rejected:client_not_in_game`).

Stop steps, in order. This is the order of `Shutdown` (7344-7412) including the sampler aborts
and without the bridge teardown [DESIGN].
1. An open dialog ends as `lease_closed`. `FinishLeaseClosed` is a sibling of
   `FinishDisconnected` (MCPDialogController.c:306-317) that calls `TryFinish` (418-454).
   `ui_dialog.py` adds `lease_closed` to `TERMINAL_STATES` (17-19). product-spec.md:426-429
   moves from five terminal states to six. `TryFinish` still rewrites a deadline that has
   already passed as `timed_out` (MCPDialogController.c:428-432) [DESIGN, Q11].
2. The UI preview root is unlinked (as 7365-7369).
3. `MCPCarDrive.Clear()` (4_World/MCP_CarScript.c:47-63).
4. `MCPInputTriggerControl.ReleaseAll("lease_close")` (MCPClientBridge.c:337).
5. `MCPPlayerMoveControl.ReleaseAll("lease_close")` (4_World/MCP_PlayerMove.c:568-611). It sends
   the server release, or leaves it to the per-tick retry (605-610, 769-800; `OnTick` 611). It is
   not `ShutdownRelease` (619-624), which drops an unsent release.
6. `RestoreGameplay()` (MCPClientBridge.c:7009-7059): weapon, look-at, simulation unless a handoff
   runs, controls and HUD, focus.
7. `ReleaseCamera()` (7073-7108). An active owned camera starts the 10-tick handoff (430,
   7083-7089). Otherwise the cameras are switched off and deleted now.
8. `MCPHoldControl.Shutdown("lease_close")` (4_World/MCP_ActionHold.c:524-550), so the hold's own
   result names the close.
9. Sampler abort, as `Shutdown` does (7382-7384) and as `DispatchVehicleRelease` already does
   for vehicle trace (2623-2625): `MCPVehicleTrace.Abort("lease_close")`,
   `MCPAnimTimeline.Abort("lease_close")`, `MCPPlayerTrace.Abort("lease_close")`. This keeps
   product-spec.md:122: A's samples do not continue under B [DESIGN, H11].
10. `m_JobRunner.AbortAll(this)`, new in MCPJobRunner.c beside `Clear` (120-126). Every job posts
    through `MCP_PostJobTimeout` (MCPClientBridge.c:5948-6010), the path it already takes at its
    deadline:
    - a hold posts its own protocol-1 result (5994-5998, 4200-4220);
    - the dialog posts its terminal state (5955-5964);
    - a move or a key posts its release reply (6954-7007);
    - the others post a timeout (6000-6009).
11. Census. If it is clean, the close posts at once. Otherwise it adds the observer job.

**Observer.** Kind `lease_close`, id = the close id, deadline = runner time + 25 s [DESIGN]. Each
tick `MCP_ProcessJob` (4872) re-evaluates the census. The observer posts when the census is clean,
or at its deadline with what is left as residue (`observer_timeout`). After step 10 it is the only
job left, and it does not count itself. Commands delivered after the close keep dispatching while
it runs.

**Census entries:** one per client row of §3. The census also reports:
- `move_floor` = `MCPPlayerMoveControl.Generation()` (4_World/MCP_PlayerMove.c:115-118). It is at
  least every id `SendStart` ever wrote (752), because `Begin` adopts the id right after the send
  (MCPClientBridge.c:2835-2844; 4_World/MCP_PlayerMove.c:532) and ids only grow in the process
  (120-122);
- `weapon_floor` W: the same pattern for raise and fire. Each request carries a monotonically
  increasing client id from its own counter, not `player_move`'s, since F and W are reported
  apart; `Begin` of the weapon path adopts it after the send, as move does [DESIGN, H1];
- live RPC-in-flight entries as residue `rpc_in_flight:<kind>` (§2.7);
- the local player's network id (`GetNetworkID`, as 4_World/MCP_PlayerTrace.c:199).

### 2.7 The server close (`lease_close`)
1. `MCPBotControl.ShutdownAll(this)` (MCP_BotControl.c:100-123). Emptying `s_Running` and sending
   stop (270-279) does not prove the bot stopped: `SubmitStop` ignores its result (370-371).
   Vanilla idle is the transition in V130/4_world/systems/bot/bot.c:331-341, applied by the
   local HFSM step V130/3_game/systems/hfsmbase.c:544-568. A rejected stop is residue
   `bot_stop_rejected` [DESIGN, D:604].
2. **Infected (H7).** The bridge records each infected it overrides (MCPBridge.c:3157-3162) and
   forgets it on release (3151-3156) [DESIGN]. The close releases every recorded one with the
   same three calls. It reports `infected_drive` as `unverified` because the native lifetime
   cannot be read (V130/3_game/entities/dayzcreatureaiinputcontroller.c:3-9) [UNVERIFIED].
   **That unverified debt stays until the server process exits.** The close no longer clears it
   by emptying the registry. K27 and G5 forbid a clean grant on the next round from an empty
   registry. A native probe is follow-up and may shorten the debt.
3. **Each player** (`GetPlayers`, as `BuildAllPlayers` does, 5443-5455):
   - `MCPMoveCloseServer(floor)`, new in 4_World/MCP_PlayerMove.c beside `MCPMoveServerRelease`
     (1097-1120). It raises `m_MCPMoveLastId` (885) to the floor F carried for that player, then
     releases an active copy whose id is ≤ F. From then on a start at or below F is refused
     (1011-1022);
   - `MCPWeaponCloseServer(weapon_floor)`, new in 4_World/MCP_Weapon.c beside
     `MCPServerReleaseRaise` (945-959). The server readers, `MCPReadFireRequest` (723-737) and
     `MCPReadRaiseRequest` (739-760), store the last id per player and refuse an equal or lower
     one, as move does (4_World/MCP_PlayerMove.c:1011-1022). `MCPServerApplyFire` (963-998)
     applies only an accepted request. The
     close raises that floor to W, then clears pending requests with id ≤ W and releases an
     armed raise. Only after that;
   - **Hold, no interrupt (H2).** The census reads, without interrupting, whether that player
     (by network id named in the close floors) has a current action or a pending request on
     ActionManagerServer (V130/4_world/classes/useractionscomponent/actionmanagerserver.c:3-4,
     76-96; V130/4_world/classes/useractionscomponent/actionmanagerbase.c:61). If it does, that
     is residue and the row stays OWED until a later close reads none, or the process exits.
     It does not matter whether a hold started it. A captured hold whose player is not in the
     floors is residue `hold_server_unchecked`. **Why no interrupt:** a late interrupt can end
     B's action; the server does not tell A's action from B's
     (V130/4_world/classes/useractionscomponent/actionmanagerclient.c:1341-1349);
   - the floor's player is found by network id, as `DispatchActionCursorIds` already resolves
     client-reported ids on the server (MCPBridge.c:1898, 1936). Not found: residue
     `player_move_floor_unresolved` or `weapon_floor_unresolved`.
4. **Jobs.** Every `m_Jobs` entry (71) is posted through `PostJobTimeout` (5394) and removed, as at
   its deadline (5191-5197). The exception is a kind that only reads: `server_perf`
   (#265@c4ba671 MCPBridge.c:2971-2994) [DESIGN].
5. **RPC in flight (H3).** Each bridge keeps a register, in Enforce, by request id, of the RPCs it
   sends whose effect can arrive later: `hands_take` (MCPBridge.c:1726-1735; 4_World/MCP_Weapon.c:1252-1320),
   `hands_attach` (1861-1870) and `inventory_move` (897-901; 4_World/MCP_InventoryMove.c:314-340),
   plus any later row of §3. The register does not depend on the job: abort or timeout does not
   drop the entry. An entry leaves on confirmation, rejection, or process exit. Every census
   reports live entries as `rpc_in_flight:<kind>`.
6. **Deletion proofs (H10).** Before posting timeout on `loadout_clear` and `scene_clear`, the
   server close moves their pending refs into a bridge register. The census checks it on every
   evaluation. The observer waits up to 25 s. What is still live is residue `deletion_pending`.
   Later closes keep checking until the refs are gone or the process exits.
   (#261@d940503 MCPBridge.c:3515-3588, 4058-4073; #262@796f25d MCPBridge.c:1358-1410). Native
   `ObjectDelete` timing is measured in-game, separately.
7. `LeaseCloseExtensionStop()`, a new protected virtual, empty in the base (for #267, §4).
8. **Census**, plus `floors`: each player's `m_MCPMoveLastId` and weapon last id after step 3.
   An async proof goes to an observer job in `m_Jobs` (kind `lease_close`, deadline `m_ElapsedS`
   + 25 s). `IsJobReady` (5204) re-evaluates it, and `PostJobSuccess` (5325) or `PostJobTimeout`
   (5394) posts it. Every evaluation also calls `LeaseCloseExtensionCensus(census)`.

### 2.8 Across peers: the move floor and the weapon floor (H1, H5)
- The client close goes first, so the client stops sending before the server is closed. Its
  step 5 ends the local move and sends the release (with the move id), and its step 6 drops the
  client weapon override.
- The client census reports move floor F and weapon floor W (§2.6). The server close carries F
  and W for that player and reports both after applying them.
- The server row is clean only if its reported move floor is at least F and its reported weapon
  floor is at least W. Then every start the client ever sent, and every raise/fire at or below
  W, is refused from then on (4_World/MCP_PlayerMove.c:1011-1022; 4_World/MCP_Weapon.c:723-760,
  963-998), even one still in flight.
- If the server close went first (the 10 s rule) and the client then reports an F or W above the
  server's reported floor, the server row becomes OWED (`player_move_floor_unapplied` or
  `weapon_floor_unapplied`). That happens before any grant (§2.3).
- Raise and fire leave the out-of-scope list: they use the same id protocol as `player_move`.
- **H5 test:** the server close is delivered; the round times out; B gets a degraded grant and
  starts a move by input with id > F; the old close then runs; B's move continues.

### 2.9 Holds (H5)
- **Lease end.** `cleanup_owner` still forgets a delivered hold's row and fences the hold, as the
  abandon does today (`_abandon_row_locked` 4162-4217; `_settle_hold_for_command_locked`
  5230-5242). It no longer publishes `action_hold_cancel` and no longer adds
  `hold_cancel_unconfirmed` (today 5317-5341) [DESIGN: `abandon_command` gains a flag that
  suppresses the release]. A's `pending_commands` drops at once; today the cancel kept it at 1
  (4323-4326).
- **The close aborts the hold** (§2.6 steps 8-9). The hold's own result then clears the fence
  through the existing note, even though its row is gone (5018-5020, 5264-5278).
- **Census entry, client.** It is the test the next `action_hold` dispatch uses to refuse: the
  local action manager is busy, or the hold control is active (MCPClientBridge.c:3863, 4086;
  4_World/MCP_ActionHold.c:54-57, 720-733). It reads the action manager, not the job, so `Take`
  clearing the active state (433-449) cannot make it clean. That is only the client half.
- **Census entry, server (H2).** §2.7 step 3. The hold fence liquidates only when both sides are
  clean: client admission predicate and server ActionManager with no current or pending action.
- **Fence is per instance, not per run (H9).** When an instance exits or is replaced, its exact
  hold records become server-row debt `hold_server_unverified`. That debt lasts until a later
  server close reads that the player has no current or pending action (H2), or the server
  process exits. Holds of the new instance are not blocked by the old instance's records
  (loopback.py:2495-2507, 3711-3713, 5138-5173, 5259-5278).
- **Settlement.** A clean close on **both** sides closes, without fence, every hold the broker
  captured at that close's delivery (`_close_hold_locked(…, needs_fence=False)`, 5165-5173). That
  covers a lost hold result. A residue keeps the fence: the next hold on **that instance** gets
  409 `hold_unreconciled` (3711-3713), and the row is owed.
- **Either order works.** If the clean close settles first, the hold's result is a no-op
  (5270-5271). If the hold result closes with fence first (5275-5278), the clean close clears it.
- **Quarantine** keeps today's path: fence plus `hold_cancel_unconfirmed` (5308-5316).

### 2.10 FIFO in both bridges (H8)
- `OnPollSuccess` dispatches inline only when `m_Pending` is empty.
  - Server: the defer flag (MCPBridge.c:325) starts as `m_Pending.Count() > 0` instead of `false`.
    The rest of the loop (326-349) is unchanged.
  - Client: the same flag around the loop at 883-896.
- `DrainPending` uses `RemoveOrdered(0)` (server 363, client 1030).
- **`world_spawn` still never runs in a callback.** The callback never drains `m_Pending`, and
  inline dispatch still stops at the first `world_spawn` (330-339). In Codex's case, a deferred
  `world_spawn` at the head of `m_Pending` when the next callback arrives, the whole new batch now
  queues behind it.
- #262 keeps the same deferral (#262@796f25d MCPBridge.c:335-359). Its batch command creates and
  answers in its own dispatch (878-885), so nothing changes for it.
- **Occupancy.** A poll still accepts at most `MAX_DISPATCH_PER_TICK + MAX_PENDING` commands, the
  amount `MAX_POLL_RESULTS` reserves (MCPClientBridge.c:413-415; pinned by
  test_guards_bridge.py:176-184). A batch that arrives on a non-empty `m_Pending` loses its 4
  inline slots. With the reserved slot below, at most 5 more commands than today can be refused.
  The client refusal is `client_bridge_queue_full` (MCPClientBridge.c:1043-1046), not
  `bridge_queue_full`. `MAX_POLL_RESULTS` is a resource reserve (MCPBridge.c:8, 261), not a cap
  that clips the batch: callbacks walk `batch.commands.Count()` (MCPBridge.c:319-349;
  MCPClientBridge.c:877-895). Recalculate and test `MAX_QUEUE` plus the close [DESIGN, Q7].
  A refusal posts a result; nothing is dropped silently (MCPBridge.c:381-385).
- **Reserved slot (H6).** `QueuePendingOrFail` refuses an ordinary command from `MAX_PENDING - 1`
  on, and a close only at `MAX_PENDING` [DESIGN]. The broker queues one close per (instance, peer)
  at a time (§2.4), so one slot is enough while the bridge ticks.
- A close holds back nothing. During a round only closes and internal releases can follow it,
  because no lease is active.

### 2.11 Grant, wait and status (H3, H9)
- One helper, `_lease_end_blocks_locked()`, is true while any row is outstanding. It joins
  `_handoff_pending` at 495-502, 515-522, 1146-1154, 2859-2870 and 3429-3440.
- `wait` wakes at the earliest row deadline: a fourth term in `wait_for`
  (session_coordination.py:1239). At the deadline, the first coordinator call that looks turns the
  row into OWED (`close_timeout`). That happens in `_expire_due` (2425-2438), in memory only.
- **Grant payload** (`_active_payload_locked`, 4052-4063): `lease_end: {round, settled:
  [{run_id, peer, close_id}], unconfirmed: [{run_id, peer, reasons, since_s}]}`. A non-empty
  `unconfirmed` also adds `lease_end_unconfirmed` to the grant's `cleanup_degraded`.
  **Merge degradations on every payload path (H6),** including the direct `wait` assignments
  (session_coordination.py:1095, 1124-1125, 1173-1174 and 4141-4150). A later warning must not
  replace `lease_end_unconfirmed`. K21 tests the debt next to another warning.
- **Q2.** 40 s / 25 s / 10 s stay policy, not proof. Separate definitive residue from a still-
  pending proof: known residue answers at once; a camera handoff needs observation. Keep
  releasing, audit, and the post-TTL grace in product-spec.md:139.
- **Status** gains `lease_end: {outstanding, deadline_in_s, unconfirmed}`, outside
  `cleanup_degraded` (1999).
- `release` answers as today. The cleanup summary keys (2596-2607, and the watcher's 2675-2686)
  lose `vehicle_release_enqueued` and `bot_stop_enqueued` and gain `lease_close_enqueued`.
- **The 40 s bound is a policy, not a proof.** Bridge jobs run on an accumulated timeslice
  (MCPJobRunner.c:114-118), and the coordinator uses `_time_fn` (session_coordination.py:263).
  The numbers are set so that a normal clean round fits: the client close takes one poll plus at
  most 25 s of observer; the server close starts at most 10 s later and takes one poll plus at
  most 25 s. That is 35 s plus polls and round trips. Past the bound, a late clean result still
  clears its row (§2.3).

### 2.12 Known peers, retirement, restart (H4)
- **Registry.** The coordinator keeps `(run_id, peer) → instance`. The broker feeds it with
  `note_peer_bound` when a binding becomes BOUND: in `confirm` (loopback.py:2489) and in
  `install_bound_peer` (2529-2564). An offline binding covers both peers (`_peer_covers`
  2638-2639).
- A setter on `ServerState.coordination` replays the bindings already BOUND when a coordinator is
  attached (daemon.py:691; test_action_hold.py:418). **Q1:** it must tolerate both construction
  orders: today `self.coordination` is assigned before `_lock` and `_bindings` exist
  (loopback.py:2020, 2039-2045). It must not read attributes that are not there yet.
- **Only the lifecycle removes rows and entries**, through `note_processes_gone(run_id, role)`:
  - `_commit_retirement` calls it with role None after the manifest write succeeds
    (process_lifecycle.py:2383-2387). Its callers set the run `EXITED` first (§1.6), so all the
    run's processes are gone;
  - replace-role calls it for the replaced role after its write (4199-4205), before the new
    instance is minted (4223);
  - a failed write calls nothing, so the debt stays.
  - **Q1:** the exit names the real process and role (loopback.py:2638-2639;
    process_lifecycle.py:2376-2388, 4191-4224). Offline expands to both peers. It runs after
    persist. Removing a binding does not by itself prove the process exited.
- Nothing reads the liveness of a parent or a session (LL-156). A row is about game processes only.
- **Restart.** At start the daemon seeds OWED (`daemon_restart`) for each role of each live run in
  its startup listing (daemon.py:654; live means `_ACTIVE_STATES`, process_lifecycle.py:70; roles
  1611). This happens before the coordinator is attached (daemon.py:691).
  - That game is `unbound_after_restart` and takes no command until its run is relaunched (§1.6).
    So no close can clear this debt. The relaunch ends the old processes and clears it through
    `note_processes_gone`. Every grant before that says `lease_end_unconfirmed`.
  - **Recovered lifecycle fault (H4).** Restart debt is seeded after the manifest is
    validated or restored and before the fault is lifted, on both paths: the repair that
    completes at loopback.py:6554-6556, and `clear_lifecycle_recovery_fault` (6461-6463).
    It covers the live runs that repair keeps (process_lifecycle.py:7625-7631, 7677-7692).
  - **Embedded is out of scope (H4).** It has no coordinator: `server.py:990-1004` builds
    `LoopbackServer`; `loopback.py:6808-6815` builds `ServerState`; `loopback.py:2030-2032`
    says there is no daemon bootstrap. daemon.py:1642-1647 is daemon start, not embedded.

### 2.13 Recovery (H10, H14, H13)
- **No code changes in control_client.py.** The guarantee that recovery cannot see a round holds
  only **after** `_releasing` is cleared (session_coordination.py:2612). During releasing,
  recovery is still refused with `session_transition_conflict` (control_client.py:836-857;
  status publishes `releasing` at 1983-1988). Rows are registered after `_releasing` is set
  (2490-2491). K20 tests the two phases separately [DESIGN, H13].
- After release:
  - closes have no owner, so `pending_commands` never counts them (loopback.py:5114-5120);
  - the debt lives in `lease_end`, outside `cleanup_degraded`;
  - the ending lease is neither active nor releasing, so its owner's state is `none`
    (session_coordination.py:1977-1988).
- So recovery after release neither waits for a round nor fails on debt. A later acquire queues
  behind the round (202), as it queues behind `_handoff_pending` today.
- **Keeping debt out of recovery is deliberate [DESIGN].** A client whose recovery failed on debt
  could never acquire, and only a lease end retries a close (§2.3). That would be circular.
- **H14.** `pending_commands` is 0 after an abandon only when no release was registered (§1.2).
  K20 tests both cases. D improves one case: a delivered hold at lease end no longer leaves a
  cancel attributed to A (§2.9).

### 2.14 `cleanup_owner` after D
- **Keeps:** the validity and authority checks (5288-5302), the discard of queued lease commands
  (5303-5305), the queued-hold close (5307) and the quarantine branch (5308-5316).
- **Changes:** delivered holds are forgotten and fenced without a cancel (§2.9).
- **Drops:** the early return (5342-5345), `vehicle_release` (5365-5374) and the bot stops
  (5375-5382). `enqueue_bot_stops_for_lease` (3400) and the `_bot_by_lease` ledger (2078, 3748)
  lose their only caller and are deleted.
- **Adds:** the round, the closes and `note_close_round` (§2.4). It reports
  `lease_close_enqueued`.
- `vehicle_active` stays in the hook signature (daemon.py:89-97; session_coordination.py:2511-2516)
  but is no longer read.

### 2.15 Locks and clocks
- The order stays: lifecycle, then broker (`_lock`, an RLock, loopback.py:2039), then coordinator
  (`_condition`). The coordinator never calls the broker.
- Broker → coordinator calls made under the broker lock are memory-only and never reach
  `_expire_due`: `lease_end_round`, `note_close_round`, `lease_close_deliverable` and
  `note_peer_bound`. `claim_dispatch` already follows this rule (1499-1515).
- Outcomes reach the coordinator after the broker lock is released (§2.5). The lifecycle calls
  `note_processes_gone`, and daemon start calls the seeding.
- A close is published and its row turns CLOSING inside one broker-lock section. So no poll can
  deliver a close before its row says CLOSING.
- **Expiry on the poll path.** The reaper runs before the poll takes the broker lock
  (loopback.py:4452). The hook no longer waits for the game, so the poll answer that carries a
  close is not held.
- **Clocks.** Rows use `_time_fn` (session_coordination.py:263). The server-close delay uses the
  broker's `_now` (loopback.py:2096-2097). Observers use the bridge's accumulated timeslice. No
  relation between these clocks is assumed (§2.11).

### 2.16 Versioning and contracts (H15)
- `MCP_BRIDGE_VERSION` "13" (MCPMessages.c:1) and `EXPECTED_BRIDGE_VERSION` (core.py:41). A v12
  bridge is refused at ingress (loopback.py:3461-3469) and gets no delivery (4546-4547). There is
  no mixed mode.
- MCPMessages.c [DESIGN]:
  - `MCPArgs` gains `close_generation`, `close_lease_id` and `close_floors`;
  - `MCPResult` (1044) gains `ref MCPLeaseCloseCensus lease_close`, with `generation`,
    `lease_id`, `peer`, `state`, `entries[{name, state, reason}]`, `move_floor`, `weapon_floor`,
    `net_low`, `net_high` and `floors[{net_low, net_high, move_id, weapon_id}]`;
  - no constructor defaults (test_messages_contract.py:201-206).
- **The names stay internal.**
  - They are not in `SERVER_COMMANDS` or `CLIENT_COMMANDS` (loopback.py:65, 102-138), so
    `/enqueue` refuses them as unknown (whitelist 143).
  - `peer_for_command` (284-287) first consults an explicit internal table
    (`lease_close` → server, `lease_close_client` → client), then `CLIENT_COMMANDS`
    (4613-4615). The names stay out of the public whitelist. Tests cover both peers,
    including offline [DESIGN, H8]. r2 D:561 was false for the cited source.
  - The capability lists announce them (MCPBridge.c:38 as merged; MCPClientBridge.c:474).
    `_BRIDGE_COMMAND_TOOLS` maps both to None (bridge_readiness.py:274), and
    `fixtures/bridge_capabilities_v1.json` follows.
  - They are not in `SERVER_ARG_CONTRACT` (bridge_readiness.py:233-240), so the hash (252-260;
    MCPBridge.c:43) does not change.

### 2.17 Files
- **PBO:** MCPBridge.c, MCPClientBridge.c, MCPJobRunner.c, MCPDialogController.c, MCPMessages.c,
  4_World/MCP_PlayerMove.c, 4_World/MCP_Weapon.c and 4_World/MCP_ActionHold.c.
- **Python:** loopback.py, session_coordination.py, process_lifecycle.py, daemon.py, core.py,
  bridge_readiness.py, ui_dialog.py and product-spec.md (six dialog states; vehicle_trace
  cleanup unchanged in meaning).
- **Unchanged:** control_client.py, dayz_test_tool.py, server.py, MCP_BotControl.c and
  4_World/MCP_CarScript.c.
- **Tests:** two new files (§5) and the changes in §5.4.

## 3. Controllers per peer (main and the PRs merged before this one)

"Active when" is the census predicate. A row is clean when its predicate is false after the stop
step and any observer wait.

**Client**

| ID | Controller (started by) | Stop step | Active when | Proof of cleanup | Anchors |
|---|---|---|---|---|---|
| C1 | Vehicle drive (`vehicle_control`) | `MCPCarDrive.Clear()` | `s_Active` | brake re-armed, car dropped | 4_World/MCP_CarScript.c:3, 47-63 |
| C2 | Held key (`input_trigger`) | `MCPInputTriggerControl.ReleaseAll` | `IsHeld()` | key released | MCPClientBridge.c:151, 178, 337 |
| C3 | Local move (`player_move`) | `MCPPlayerMoveControl.ReleaseAll` | `IsActive()`, or `s_ServerReleasePending` | overrides off, server release sent | 4_World/MCP_PlayerMove.c:107-113, 568-611, 769-800 |
| C4 | Client weapon override (`weapon_*`) | `RestoreGameplay` → `MCPWeaponControl.ReleaseAll` | `IsBusy()` | raise and aim off | 4_World/MCP_Weapon.c:94, 212-230; MCPClientBridge.c:7013 |
| C5 | Look-at (`player_look_at`) | `RestoreGameplay` → `MCPLookAtControl.Release` | `IsActive()` | released | 4_World/MCP_LookAt.c:92, 107; MCPClientBridge.c:7014 |
| C6 | Camera (`camera_set`) | `ReleaseCamera()` | `m_ActiveCam` set, a handoff pending, or the free camera active | handoff finished after 10 ticks, static camera deleted | MCPClientBridge.c:430, 508, 7073-7108, 7147-7230 |
| C7 | Player simulation off (`camera_set` free, handoff) | `RestoreGameplay`; `FinishCameraHandoff` | `m_PlayerSimulationDisabled` | simulation on | 5754-5758, 7033-7040, 7219-7223 |
| C8 | Controls and HUD suppressed | `RestoreGameplay` | `m_ControlsSuppressed` | controls on, HUD shown | 6877, 7048-7056 |
| C9 | Dialog (`ui_dialog`) | `FinishLeaseClosed()` (new) | `m_Dialog.IsOpen()` | root hidden, input unlocked | MCPDialogController.c:125-128, 306-317, 418-454 |
| C10 | UI preview root | `Unlink()` | `m_UiPreviewRoot` set | unlinked | MCPClientBridge.c:523, 7365-7369 |
| C11 | Action hold (`action_hold`) | `MCPHoldControl.Shutdown`, then the job's own post | action manager busy, or hold control active (client half) | client predicate false **and** server ActionManager idle for that player | 4_World/MCP_ActionHold.c:54-57, 524-550, 720-733; MCPClientBridge.c:3863, 4086, 4200-4220; V130/4_world/classes/useractionscomponent/actionmanagerserver.c:3-4, 76-96 |
| C12 | Jobs, every kind, #264 `player_view_set` included | `AbortAll` through `MCP_PostJobTimeout` | runner count minus the observer | each kind's timeout post | MCPJobRunner.c:104-169; MCPClientBridge.c:5948-6010; #264@92336cb MCPClientBridge.c:1239-1241, 4921-4923, 6805 |
| C13 | Samplers: anim timeline, player trace, vehicle trace | abort as `Shutdown` / `DispatchVehicleRelease` | sampler still capturing | abort posted; A's samples do not continue | MCPClientBridge.c:2623-2625, 615, 619, 7382-7384; product-spec.md:122 |

**Server**

| ID | Controller (started by) | Stop step | Active when | Proof of cleanup | Anchors |
|---|---|---|---|---|---|
| S1 | Bots (`bot_start`) | `MCPBotControl.ShutdownAll` | `s_Running` not empty, or stop rejected | idle transition, not only empty registry; rejected stop is residue | MCP_BotControl.c:15, 100-123, 260-281, 370-371; V130/4_world/systems/bot/bot.c:331-341; V130/3_game/systems/hfsmbase.c:544-568 |
| S2 | Infected overrides (`infected_drive`) | release each recorded infected (new registry) | unverified debt until process exit | none provable by reading; empty registry is not clean | MCPBridge.c:3091-3173; V130/3_game/entities/dayzcreatureaiinputcontroller.c:3-9 |
| S3 | Move server copy (client `player_move`) | `MCPMoveCloseServer(floor)` (new); only copies with id ≤ F | `m_MCPMoveActive` set, or floor below the client's | copy released, older starts refused | 4_World/MCP_PlayerMove.c:872, 885, 935-952, 1011-1022, 1097-1120 |
| S4 | Weapon server copy (client `weapon_*`) | `MCPWeaponCloseServer(W)` (new); refuse id ≤ last; then clear ≤ W | armed raise, pending request, or floor below W | last id stored; older raise/fire refused | 4_World/MCP_Weapon.c:696-703, 723-760, 795-812, 945-959, 963-998 |
| S5 | Jobs: spawn, seat, `inventory_move`; deletion refs of #261 `loadout_clear`, #262 `scene_clear` | `PostJobTimeout`; pending refs moved to bridge register first | `m_Jobs` minus kept kinds and the observer; `deletion_pending` | timeout post plus register empty or residue | MCPBridge.c:71, 686-693, 872, 2116-2124, 5151-5202, 5394; #261@d940503 MCPBridge.c:3515-3588, 4058-4073; #262@796f25d MCPBridge.c:1112, 1203, 1358-1410 |
| S6 | RPC in flight: `hands_take`, `hands_attach`, `inventory_move` | none recalled; register by request id | register not empty | confirmation, rejection, or process exit | MCPBridge.c:897-901, 1726-1735, 1861-1870; 4_World/MCP_Weapon.c:1252-1320; 4_World/MCP_InventoryMove.c:314-340 |
| S7 | #265 `server_perf` | kept | not censused | it only reads | #265@c4ba671 MCPBridge.c:645-656, 2971-2994 |
| S8 | Extensions (#267) | `LeaseCloseExtensionStop()` | the entries `LeaseCloseExtensionCensus()` adds | set by the extension | §4 |
| S9 | Server action (hold and any other) | census only; no interrupt | current or pending ActionManagerServer, or `hold_server_unchecked` | later close reads idle, or process exit | V130/4_world/classes/useractionscomponent/actionmanagerserver.c:3-4, 76-96; V130/4_world/classes/useractionscomponent/actionmanagerbase.c:61 |

Not controllers: godmode, which is on by default by mission policy (4_World/MCP_PlayerCare.c:1-21),
and #262's `world_spawn_batch`, which creates and answers in its own dispatch (#262@796f25d
MCPBridge.c:878-885). Both are world state (§7).

## 4. Registration contract for #263, #266 and #267

These three PRs merge after this one and are not changed here. After their rebase, every lasting
effect they add follows four rules:
1. **Stop at the close.** Add a synchronous stop step to that peer's close (§2.6 or §2.7), at the
   place named below.
2. **Census the real state.** Add one census entry. Its predicate reads the effect itself, never
   the existence of a job.
3. **Async proofs go to the observer.** If the effect ends later (a deferred delete, an emote end),
   the observer re-evaluates the entry. Do not add another job.
4. **No lease-end release.** Do not queue a per-lease release in `cleanup_owner`, and keep no
   per-lease ledger for it. Releases for an abandon in mid-lease stay in `RELEASE_REGISTRY`; FIFO
   now runs them after their source.

**#263, camera path (`28075ac`).**
- Client stop: `StopCameraPath()` (MCPClientBridge.c:5270-5277; `MCPCameraPath.Abort`,
  MCP_CameraPath.c:298). It goes before `RestoreGameplay` and `ReleaseCamera`, as in its
  `restore_gameplay` (1131-1135). Stopping the path does not restore the camera by itself; steps 6
  and 7 of §2.6 do.
- Census: `m_CameraPath` (513) is not playing (`IsPlaying`, MCP_CameraPath.c:291).
- Drop at lease end: `enqueue_camera_path_releases_for_lease` (loopback.py:3508) and its calls in
  `cleanup_owner` (5408, 5485, 5510-5523). Keep its `RELEASE_REGISTRY` entry (164-168). `Shutdown`
  keeps its own stop (MCPClientBridge.c:7691).
- This also removes Y16 (old releases filling the queue): nothing is queued per play any more.

**#266, survivor drive and emotes (`b4f0611`).**
- Server stop: `MCPSurvivorDrive.ShutdownAll(this)` (MCP_SurvivorDrive.c:164-186). Census:
  `s_Driven` (32) is empty.
- Its maintainer ticks before `DrainPending` (MCPBridge.c:126-127, 136). So a close dispatched in
  one tick leaves no drive write after that tick.
- Client stop: `MCP_RequestEmoteEnd()` (4_World/MCP_Emote.c:23-31) for an emote that
  `actor_emote` started and that still plays. Census: `IsEmotePlaying`
  (V130/4_world/classes/emotemanager.c:1328-1330), limited to the MCP-started emote.
  `ProcessActorEmoteJob` today reads only `m_bEmoteIsPlaying` (#266@b4f0611
  MCPClientBridge.c:7064-7073); that is not both playing and pending (D:648-649).
- Drop at lease end: `enqueue_survivor_drive_releases_for_lease` (loopback.py:3549-3563) and its
  calls in `cleanup_owner` (5435, 5509, 5534, 5543-5551). Keep `RELEASE_REGISTRY` (162-169).

**#267, Expansion AI adapter (plan at `1dde8b9`).**
- The base bridge gets two empty protected virtuals beside the plan's three hooks (plan:39-42):
  `LeaseCloseExtensionStop()` and `LeaseCloseExtensionCensus(MCPLeaseCloseCensus census)`.
- The adapter's modded `MCPBridge` (plan:48) overrides them:
  - the stop starts the delete of every live group, as its `ai_group_release` dispatch does
    (plan:190-193);
  - the census adds one entry per group whose members or group still exist, the read-only check
    of plan:195-196.
- Its plan §5 changes:
  - no `ai_group_release` from `cleanup_owner` (plan:245-246);
  - `ai_group_release_unconfirmed` becomes a residue entry;
  - the reasoning about a 36 s barrier and `AI_RELEASE_CONFIRM_S` 40 s (plan:197, 249-252) gives
    way to the observer's 25 s cap. A delete still pending at the cap is residue, so it is debt,
    never a silent clean.

## 5. Test plan (R26)

### 5.1 Fixtures
- **Broker and coordinator, in memory:** the `_owned_state` wiring (test_action_hold.py:404-424:
  both peers bound, `SessionCoordinator(cleanup=state.cleanup_owner)`, attached at 418), with
  `FakeClock` (lease_helpers.py:20) and `SequentialIds` (31); `bind_both_peers`
  (fence_helpers.py:15-40) and `bound_queue` (210). `BrokerFixture` is not used: it opens a
  listener.
- **Bridges:** `translate` (enforce_subset_helpers.py:301-321) runs extracted Enforce bodies
  against doubles. Its limits are stated at 1-9: no natives, no compiler, no engine scheduling.
- **New files:** `tools/tests/test_lease_close_broker.py` (K1-K19, K21) and
  `tools/tests/test_lease_close_bridge.py` (K22-K30). K20 goes into the existing
  test_control_client.py and test_dayz_test_tool.py, K31 into the ui_dialog tests, and K32 into the
  contract tests.

### 5.2 Criteria

Each criterion has an offline positive (POS) and negative (NEG) fixture with its expected result.
"Red on main" marks the r1 repro cases, which must fail on main.

**Broker and coordinator** (`test_lease_close_broker.py`)

| K | Criterion | POS → expected | NEG → expected |
|---|---|---|---|
| K1 | T1, abandoned delivery (red on main) | as in T1 → B 202; both closes answered clean from their instances → B 200, `lease_end.settled` names both ids; the late `object_delete` result → `discarded` | server close answered from another instance → 200 `discarded`, row still CLOSING, B still 202 |
| K2 | T2, unanswered delivery (red on main) | same as K1 without the abandon | same as K1 |
| K3 | T3, lasting effect (Y14; red on main) | client queue exactly `["lease_close_client"]`, no `vehicle_release`; B 202 until settled | client residue `vehicle_drive` → B granted with `lease_end_unconfirmed` and that reason |
| K4 | Lease without work | no commands, or read-only ones only, and no debt → no close, B 200 at once | same lease with an owed row → closes published, B 202 |
| K5 | Every known peer | a server-only lease → a client close too, published first | client binding AMBIGUOUS (4746-4750) → client row OWED (`not_bound`), server close published at once |
| K6 | Offline binding | one offline binding → two closes on one instance queue; each poll takes only its own (4613-4615) | a server poll never returns `lease_close_client` |
| K7 | Identity | exact echo → clear | wrong generation, lease or peer → ignored, row CLOSING; another instance → `discarded`, row CLOSING; epoch bumped and re-BOUND → OWED (`binding_epoch_changed`) |
| K8 | Delivery gate (H5) | close queued, no lease → delivered; delivered close, round times out, B degraded grant, B starts move by input with id > F, old close then runs → B's move continues | deadline passes, B granted degraded, next poll of a still-queued close → `lease_close_stale`; round 1's queued close when round 2 publishes → `lease_close_superseded`; a per-destination admission fence for B is not added |
| K9 | Hook failures (H6) | normal hook → rows CLOSING | slots exhausted → OWED (`cleanup_worker_saturated`) at once; hook raises → OWED (`cleanup_failed`); hook returns `cleanup_fenced` → OWED (`close_not_reported`); hook past `cleanup_timeout_s` reports before the deadline → CLOSING, after it → stays OWED |
| K10 | Past `MAX_QUEUE` (H6) | client queue holds `MAX_QUEUE` internal releases → the close is still published and delivered | at that queue `_publish_release_locked` refuses a release with `queue_full` (4296-4300), which proves the queue was full |
| K11 | The waiter wakes (H9) | real clock, bound 0.2 s (constructor override): a thread in `wait` returns 200 with `lease_end_unconfirmed` within 0.35 s, with no other call | wake term removed (mutant) → it returns only at its own wait limit (1 s), so the POS assertion fails |
| K12 | A → B → C (H3) | A leaves residue → B degraded; B releases with no commands → a debt round → closes clean → C clean | B's round leaves residue again → C degraded too |
| K13 | Restart (H4) | seeded run → first grant degraded (`daemon_restart`); `note_processes_gone(run, None)` → next grant clean; recovered fault: seed after manifest restore and before lifting, including `clear_lifecycle_recovery_fault` (6461-6463) and complete (6554-6556), covering kept live runs (process_lifecycle.py:7625-7631, 7677-7692) | a lease end while the run is unbound → rows OWED (`not_bound`), debt stays; construction order of setter vs `_lock`/`_bindings` (2020, 2039-2045) both attach |
| K14 | Retirement and late results (H13) | queued close at `retire_role` → `binding_retired`, its late result → 200 `discarded`; delivered close → late result 409 `binding_retired`; the retired instance's own hold result → 200 and reconciles | the lifecycle write fails, `note_processes_gone` is not called → row stays OWED |
| K15 | Quarantine | quarantine at lease end → no close, rows OWED (`retail_quarantine`), grant degraded at once; hold path as today | quarantine after the close was queued → delivery discards it (4672-4673) → OWED |
| K16 | Holds (H2, H5, H9) | (a) delivered hold, both sides clean, hold result lost → next `action_hold` on that instance admitted; (b) clean close before the hold result → result is a no-op; (c) at lease end no `action_hold_cancel` is queued and A's `pending_commands` is 0; (f) instance replaced: old exact holds become `hold_server_unverified`, new instance holds admitted | (d) residue client or server action → next hold on that instance 409 `hold_unreconciled`, grant degraded; (e) a clean close from another instance → fence stays; (g) interrupt is not sent; a hold player missing from floors → `hold_server_unchecked` |
| K17 | Move and weapon floors (H1, H2, Q15) | client F=7 W=4 → server close carries both, reports ≥ → both rows clear; late clean while still CLOSING → clear | server first reports F=5 then client F=7 → OWED `player_move_floor_unapplied`; same for W → `weapon_floor_unapplied`; late result after row already OWED stays OWED until a later clean identity match; unresolved player → OWED |
| K18 | Order across peers | the server close is published only after the client close settles | client silent → server close published at + 10 s, not earlier |
| K19 | Interleavings and locks | (i) no poll between publish and report sees the close; (ii) expiry from `record_poll` → that same poll answer carries the client close; (iii) status and acquire during a blocked hook return; (iv) an outcome of round 1 flushed after round 2 opened → ignored | a mutant hook that waits for the game → the poll answer in (ii) comes only after the hook's timeout |
| K21 | Payloads (H6) | grant `lease_end.settled` names the close ids; during a round status shows `lease_end` and `cleanup_degraded == []` | with an owed row the grant has `lease_end_unconfirmed` merged with another warning (direct wait assigns at 1095, 1124-1125, 1173-1174, 4141-4150); status `cleanup_degraded` stays `[]` |

**Recovery** (test_control_client.py, test_dayz_test_tool.py)

| K | Criterion | POS → expected | NEG → expected |
|---|---|---|---|
| K20 | Consumers (H10, H13, H14) | after `_releasing` is None (2612): `_require_recovery_idle_status` accepts `lease_end.outstanding > 0` and owed debt; `reconcile_idle_session`, `session_acquire_wait` and `_require_idle_session` pass; T1 without a release has `pending_commands == 0` | during releasing (2490-2491, 1983-1988): recovery → `session_transition_conflict`; a mid-lease abandon whose release is still queued → `pending_commands == 1` → `session_transition_conflict`, as on main |

**Bridges** (`test_lease_close_bridge.py`)

| K | Criterion | POS → expected | NEG → expected |
|---|---|---|---|
| K22 | T4 (red on main) | a 16-command client batch → dispatch order `1..16` | the same body with `Remove(0)` → `1,2,3,4,5,16,15,…,6` |
| K23 | T5 (red on main) | a batch of 12, one drain, a batch of 2 → `1..14` | inline dispatch on a non-empty `m_Pending` → the new batch overtakes |
| K24 | `world_spawn` deferral (H8) | server: pending `C5..C12` with `world_spawn` at C9, one drain, a new batch → nothing runs in the callback; C9 runs only from `DrainPending`; ids in order | a mutant that drains in the callback → `world_spawn` runs in the callback (flag on the double) |
| K25 | Admission (H6, Q7) | `MAX_PENDING - 1` ordinary commands pending → an ordinary command gets `client_bridge_queue_full` on the client (`MCPClientBridge.c:1043-1046`) or `bridge_queue_full` on the server, a close is accepted; `MAX_QUEUE` plus one close still publishes | pending full → a second close gets the same full error; `MAX_POLL_RESULTS` does not clip the batch |
| K26 | Client census per controller (H1) | for each client row of §3: controller active → close → entry stopped, census clean (after the observer when async; the camera only after 10 ticks) | that row's stop step stubbed out → residue naming exactly that entry |
| K27 | Server census per controller (H1, H2, H3, H7) | for each server row: bots (empty registry is not enough; rejected stop → residue), infected (`unverified` until process exit, never clean next round from empty registry), move copy (F applied, copy id ≤ F released, later start with lower id refused), weapon copy (W applied, later raise/fire with lower id refused), jobs, RPC register, deletion register, server ActionManager idle | stop stubbed out → residue naming it; `rpc_in_flight:<kind>` with job gone; `deletion_pending`; G5-style empty infected registry still OWED |
| K28 | Observer | camera handoff → the close posts in the tick the handoff ends; later commands dispatch meanwhile | a stub that never clears → residue `observer_timeout` at 25 s of runner time, naming the entry |
| K29 | Client not in game | not in game → `client_not_in_game` from the gate (1098-1104), no stop step called | in game → the stop steps run |
| K30 | Jobs of the merged PRs (H10) | `loadout_clear` and `scene_clear` refs move to the bridge register before timeout post; observer waits ≤ 25 s; `player_view_set` aborted through timeout; `server_perf` survives | a mutant that drops the refs and calls the census clean; a mutant that aborts `server_perf` fails the keep assertion |

**Python contract**

| K | Criterion | POS → expected | NEG → expected |
|---|---|---|---|
| K31 | Dialog state (Q11) | a `ui_dialog` result with state `lease_closed` validates (ui_dialog.py:17-19, 296); product-spec.md:426-429 lists six states; `TryFinish` at the deadline still yields `timed_out` (MCPDialogController.c:428-432) | an unknown state is still rejected; a partial `values` payload is still rejected |
| K32 | Version and names (H8, H11, H15) | version "13" on both sides; the capability census lists the close names, mapped to None; they are absent from the public sets and `/enqueue` refuses them; arg-contract hash unchanged; `peer_for_command` table routes `lease_close` to server and `lease_close_client` to client, including offline (K6) | a v12 bridge is refused at ingress (3461-3469) and gets no delivery (4546-4547); names added to `CLIENT_COMMANDS` (mutant) |

### 5.2.1 Offline vs in-game (r2 §5, adopted)

| Criteria | Offline | In-game |
|---|---|---|
| K1–K4; G1 | Keep the in-memory broker/coordinator repros; check the accumulated delivery mark after result and abandon. | Prove X was **delivered**, not only queued before timeout. If not, G1 is inconclusive. Name the object and the grant's close ids. |
| K5–K7, K13–K14 | Both construction/attach orders; offline → two peers; role replace; persist fail; recovered fault that keeps other live runs. | Confirm surviving process identities and seed/clear of their debt. A binding change does not prove exit. |
| K8, K17–K19 | Interleavings with a close **delivered** before timeout and dispatch after the grant; results after 40 s; supersession; atomic floor check. | Delay the server close while the client sends a new move; the old close must not change it (H5). A Python queue test does not measure this. |
| K9–K12, K15, K21 | Combine each failure with other warnings and keep `lease_end_unconfirmed`. Cover late callback, hook that publishes, hook that never publishes. Full queue plus close. | Separate POST attempt, received result and settlement. Measure both clocks; a Python deadline does not prove 25 s of simulation. |
| K16, K26–K29; G2–G3 | Independent doubles of local and server state; interrupt not sent / in flight; replaced-instance hold; RPC with job gone; late weapon start; rejected bot stop. | Observe availability and rest on **both** sides. Camera and controls need readback; native channels need rejection of old activity. |
| K27, K30; G5 | Drop positives that treat the next round as clean because the registry/job was emptied. Keep refs and obligations until resolved. | Infected probe (follow-up) isolates override effect. Deletions are watched until gone; reading the native declaration is not enough. |
| K20 | Public consumers with a round during releasing — expected conflict — and after release — recovery allowed; abandon releases still attributed to the owner. | Distinguish session-transition end from pending physical cleanup. |
| K22–K25; G4 | Both bridges, exact order, old pending, spawn at head, new batch, queue pressure; update the `test_task9` literal and keep a FIFO-independent negative. | Keep the single-batch proof and filter expected ids. For the chosen one-shot commands, `result posted` order proves FIFO inside the tick. |
| K31–K32 | New enum, timeout exactly on the edge, unknown states refused, internal names in both routers, public whitelist closed, versions and integrated census. | Compile and deploy PBO v13, with identity/hash and capabilities of the run's peers. |
| G6 | Mechanical bounds; separate definitive residue from observed proof; keep grace, releasing and audit. | Measure idle, handoff, pending proof and degradation separately. 0.5/2 s targets depend on load and do not replace a close guarantee. |

### 5.3 Findings to criteria
r2 H1: K17, K27. r2 H2: K16, K27. r2 H3: K27. r2 H4: K13. r2 H5: K8, K17, G3. r2 H6: K21.
r2 H7: K27, G5. r2 H8: K32, K6. r2 H9: K16. r2 H10: K30. r2 H11: K26, C13. r2 H12: K24, §5.4.
r2 H13: K20. r1 H7: none here (§7). r1 H8: K24. r1 H9: K11. r1 H11: K32. r1 H13: K14.
r1 H14: K20, K16. r1 H15: K32 for the gates; K26 checks state, not jobs.

### 5.4 Existing tests
**Changed on purpose:**
- Version pins to "13": test_action_hold.py:106/108, test_batch6.py:35/41,
  test_fb_e1ae_input_trigger.py:407, test_instance_fence.py:942/944, test_player_heal_godmode.py:680,
  test_player_kill.py:313, test_player_move.py:651, test_player_trace.py:587,
  test_ui_click_complete.py:724, test_vehicle_trace_contract.py:137/139, test_world_time_get.py:265.
- test_messages_contract.py (H11): `EXPECTED_VERSION` (19), the known-good fixture (138) and the
  assertion (160) move to "13"; the `version_bumped` mutant (194) becomes 13 → 14, so the negative
  stays independent.
- test_task9_spawn_phase_markers.py (H12): the pinned loop text (158-184) changes with the defer
  flag. The literal at :220 (`bool deferFromWorldSpawn = false;`) changes with it. Keep a
  negative independent of FIFO and deferral (D:754-755).
- Lease-end markers that change meaning: test_action_hold.py:511-569 (525: the delivered cancel is
  gone), test_bot_verbs.py:187 and 249-251, test_client_process_gone.py:511,
  test_loopback.py:1048, 1119, 1138, test_process_lifecycle.py:1856, test_session_e2e.py:446,
  test_session_http_contract.py:727. They now assert the closes.
- Fake hooks that return the old keys (test_daemon.py:1895, 1908; test_loopback.py:590, 631) keep
  working, but the summary filter (2596-2607) no longer passes those keys on.
- Tests that expect an immediate grant after a lease that delivered a mutation now answer the
  closes first, with a helper in the in-memory fixtures [DESIGN].
- The capability fixture and census tests (fixtures/bridge_capabilities_v1.json,
  test_bridge_server_capabilities.py, test_bridge_client_capabilities.py) gain the two names.

**Stay green, by construction:**
- test_guards_bridge.py:176-184: no constant changes.
- test_fb_f47b_release_render.py:170-192 and test_vehicle_telemetry_contract.py:785-812: they pin
  job literals with `tick_dispatch`, and r2 adds no dispatch field.
- test_validate_command_args_table.py:2304-2305 and test_command_validation_coverage.py:228-229:
  the closes are not in the public command sets.
- test_action_hold.py:514 and 571-605 (quarantine keeps `hold_cancel_unconfirmed`) and
  test_retail_quarantine.py:189.
- Leases that deliver no mutation hand off as today.

## 6. In-game criteria (final round)

DayZDiag server and client with the v13 PBO, and MCP sessions A, B and C under the session
protocol: lease, lifecycle guard, no direct kills, `session_status` before each handoff.

**Evidence rules (H12):**
- **The causal event is B's own grant.** Its `lease_end.settled` lists the close ids (§2.11). It is
  built from settled rows, and the slot stays blocked while a row is outstanding, so it cannot come
  before the settlement. Log positions are never used as proof of cause.
- **Order on a bridge** is the order of its `result posted id=` lines (MCPBridge.c:5540;
  MCPClientBridge.c:7273). For one-shot commands `PostResult` writes them during the dispatch, so
  they give the exact sequence, also inside one tick.
- **A batch counts only if the bridge logged it as one poll:** `poll commands=N` (MCPBridge.c:322;
  client 880). Otherwise the run is inconclusive and is repeated.

**G1. Abandoned delivery (T1).**
- Send: A sets `/set_poll_delay {"ms": 4000}` (one-shot, at most 5000 ms, loopback.py:5412-5419).
  A sends `world_spawn` with a 1 s tool timeout, which returns `timeout waiting for world_spawn
  id=X`. A releases at once. B acquires.
- Pass: B gets 202 first. B's grant lists both closes as settled. The server log shows
  `result posted id=X` before the server close's line. B's first `entities_query` finds X's object.
  Release → grant takes at most 4 s + 2 s.

**G2. Lasting client effects (Y14, H1, H5).** Four runs, each ended by A's release:
- (a) `vehicle_control`, throttle 0.5, `hold_ttl_s` 20 → B's first `vehicle_telemetry` shows
  throttle 0;
- (b) `camera_set` free → B's first `camera_get` reads the player camera, and a `player_move` press
  by B moves the player (simulation is on);
- (c) `ui_dialog` open for 60 s → A's dialog result says `lease_closed`, and B's window grab shows no
  dialog;
- (d) a 10 s `action_hold`, released in the middle → B's own `action_hold` is admitted, with no
  `hold_unreconciled`.
- Pass, each run: B's grant lists the client close as settled. Release → grant takes at most 2 s,
  plus the observer's wait in (b) (the 10-tick handoff).

**G3. The player_move server copy (H2).**
- Send: A starts a 20 s `player_move` hold. A releases at once.
- Pass: the server log has the copy's release line (4_World/MCP_PlayerMove.c:1117-1119) before the
  server close's `result posted`. The client census `move_floor` equals the move id, and the server
  census floor is at least that. Two `query_player_state` calls by B, 1 s apart, give the same
  position within 0.05 m.

**G4. FIFO (H12).**
- Send: 16 `camera_get` through `/enqueue` within one poll interval; then 16 `query_player_state`.
- Pass: the bridge logged `poll commands=16` once, and its 16 `result posted id=` lines come in
  increasing id order. A split batch is inconclusive and is repeated.

**G5. Debt across leases (r2 H7; r1 H3).**
- Send: A runs `infected_drive` on a spawned infected and releases. B acquires, then releases
  without commands. C acquires.
- Pass: B's grant has `lease_end_unconfirmed`, with `infected_drive` unverified as the reason.
  C's grant **still** has that degradation. An empty registry after A's close is not a clean
  grant. The debt lasts until the server process exits.

**G7. Delivered close vs B's later move (r2 H5).**
- Send: delay the server close after delivery; A's round times out; B gets a degraded grant and
  starts a `player_move` by input with id > F; then the old close runs.
- Pass: B's move continues. The close log does not release B's id. A Python queue test is
  inconclusive for this row.

**G6. Latency budgets (H12),** from A's release reply to B's grant reply:
- no work (read-only commands only): no close in either log, at most 0.5 s;
- work with nothing left over (one `object_delete`): at most 2 s;
- work left over (G2 b): at most 2 s plus the handoff;
- residue (G5): a degraded grant within 2 s.
- The 40 s bound is measured offline only (K11). `/set_poll_delay` stops at 5 s, and making a peer
  stop polling would need a kill outside the lifecycle guard. Restart (K13) and retirement (K14)
  stay offline for the same reason.

## 7. What this does not cover
- `exec_enforce`: arbitrary code, whose effects the census cannot know.
- World state: spawns, deletes, teleports, godmode (on by default, 4_World/MCP_PlayerCare.c:1-21),
  time and weather, inventory and loadouts (#261), scenes (#262), the person-view flag (#264), HUD
  notifications, and seating in a vehicle. A request that can still apply after the grant is
  in the close (weapons, RPC, deletion refs), not in this exclusion.
- Sampler **data already captured**; the samplers themselves abort on the client close (§2.6).
- The native lifetime of infected overrides (§2.7). The unverified debt lasts until process
  exit. A native probe is follow-up.
- Native `ObjectDelete` termination latency (H10): measured in-game, separately from the
  register.
- **r1 H7, inherited P0.** Jobs that change the world (camera_set apply, MCPClientBridge.c:5067-5080;
  vehicle get-in, 5291-5295 and 5343-5354; timeout handlers, 5948-6010; hold maintenance inside
  its job, 4872-4924). Follow-up PR: move those mutations to dispatch. That PR states cases and
  criteria for camera, get-in, and hold maintenance/timeout. **Gate:** until that PR merges,
  neither this PR body nor the CHANGELOG treats the job rule as met.
- A peer that never answers: its debt lasts until its process exits or its role is replaced.
- Legacy (unaccredited) peers: no binding, so no row.
- **Embedded mode (H4).** No coordinator (`server.py:990-1004`; `loopback.py:6808-6815`,
  `2030-2032`). Restart-debt seeding does not apply there.
