<!-- CABECERA DE LA REVISIÓN 8 — leer antes que el cuerpo -->

# PR del transporte: el fin del lease espera al juego (diseño, revisión 8)

**Qué es esto.** Es la revisión 8 del diseño, sobre `ce3601e` (la base del código sigue siendo main `75b5d16`). Responde a la revisión r7 de Codex Sol, RED con los hallazgos H1 a H4 (P1 en H1 y H2, P3 en H3 y H4). El cuerpo, en inglés, cambia solo lo que esos H piden. Donde discrepen, manda esta cabecera.

**Decisión de Guillermo (2026-10-24 10:07Z):** «PR aparte». Lo revisa Codex Sol y lo mergea Guillermo, porque toca el PBO.

**La decisión: el diseño D** (§2 del cuerpo).
- Al acabar un lease con trabajo, hay una obligación por peer conocido. Se publica `lease_close` o `lease_close_client` a cada destino alcanzable; un destino inaccesible queda OWED sin comando (§2.4).
- El cierre para en su dispatch todo lo que controla su bridge. Después contesta un censo del estado real, no de los jobs.
- Suelos de ids en `player_move` y en raise/fire, ligados a la conexión de origen. Cada mensaje de esos canales que el lector compara por id (start, release, raise, fire) lleva `conn_gen`. RPC de inventario y de manos: la fila se liquida solo con un rechazo validado, con el id de la petición, antes de emitir; un readback de cantidad o de ubicación no borra nada. Esas filas y `notify_players` viven en el registro del servidor hasta un rechazo pre-emit o la salida del **proceso servidor**. Hold: helper de current/pending más deuda por tipo (START/interrupt/END).
- Si una prueba tarda, un job observador espera hasta 25 s. El coordinator guarda una fila por (run, peer). El grant espera 40 s. Una fila sucia queda como deuda hasta un cierre limpio o la salida del proceso.
- FIFO en ambos bridges. Routing interno de los dos nombres de cierre, fuera de la whitelist. Versión del bridge 13. Reserva de cada peer: 65 resultados (64 ordinarios, admitidos o rechazados, más el cierre); umbral de admisión 63 de 128.
- Un cierre ya entregado solo suelta copias y peticiones con id ≤ F o ≤ W **de esa conexión**. Un release de otra generación no suelta el move ni sube el suelo. No hay valla de admisión por destino.
- `cleanup_owner` ya no encola `vehicle_release`, `bot_stop` ni `action_hold_cancel`. Los releases del abandono se quedan. Las filas se registran en el coordinator **antes** del hook.

**Mapa H1–H4 de la r7 → secciones:**
- **H1** (contrato público de `conn_gen_unknown`, P1): la migración cubre cada verbo cuyo camino cliente puede devolver el rechazo (`player_move`, `weapon_raise`, `weapon_fire`): SendStart / FlushServerRelease / DispatchPlayerMove, SendRaiseRequest / SendFireRequest y sus llamadores, BRIDGE_CODES, el extractor con SEND_START, las tres descripciones, `server.py` fuera de Unchanged, y el positivo más el pin de fuente. §2.5, §2.17, §5.4.
- **H2** (resultado del hold, P1): el paso 8 dice lo que hace `MCPHoldControl.Shutdown` sin cambiarlo: `cancel`/`shutdown` solo si aún no hay terminal ni timeout; un hold ya publicado conserva su observación. K16 mide los dos casos y el mutante que reescribe el terminal. §2.6 paso 8, K16, G2(d).
- **H3** (prefijo de los pins, P3): los tres pins de conjuntos llevan commit y ruta completa. §2.6 paso 4.
- **H4** (redacción de consumidores, P3): ninguna rama distingue `shutdown` del cierre de un lease; otras causas sí ramifican. El resumen nombra revisión 8. §2.6, §0.
- El mapa H1–H5 de la r6 está en `ce3601e`.

**Condiciones de las preguntas (r2 §1) y citas del §4:** Q1 en §2.12; Q2 en §2.11 y §6 G6; Q7 en §2.10; Q11 en §2.6 y K31; Q13 en §2.1 y §2.5; Q15 en §2.3 y §2.8. Citas de la r2 (sus líneas): D:561 → §2.16; D:648-649 → §4 #266; D:501 y D:509-510 → §2.12 y §2.13; D:754-755 → §5.4; D:604 → S1 y §4; D:366-370 → §2.7; D:443-448 → §2.10.

**Parciales de la r1, cerrados aquí:** H2 r1 con H5 r2. H4 r1 con H4 r2. H5 r1 con H2 y H9 r2. H10 r1 con H13 r2. H12 r1 con el censo y G5. **Siguen abiertos:** los canales de acciones (H1 r4) y de RPC de inventario/manos (H2 r4); H1 r1 y H3 r1 no se dan por cerrados.

**Fuera de alcance, con su seguimiento:**
- **H7 de la r1, P0 heredado.** PR de seguimiento: mutaciones de jobs al dispatch. Casos y criterios: cámara, get-in, y mantenimiento y timeout del hold. Compuerta: hasta que se mergee, ni el cuerpo ni el CHANGELOG dan por cumplida la regla de los jobs. §7.
- **Embedded (H4 r2):** no tiene coordinator (`server.py:990-1004`; `loopback.py:6808-6815` y `2030-2032`). §2.12 y §7.
- La sonda nativa de `infected_drive` sigue como seguimiento; la deuda no se borra al vaciar el registro.
- #263, #266 y #267: solo el contrato del §4.

**Orden de merge.** #261, #262, #264 y #265 ya están en main `75b5d16`. Este PR los cubre (§3). #263, #266 y #267 entran después. La versión 13 obliga a esos tres a rebasar y a construir el PBO nuevo. LIVE y T130 se resellan en la ronda final (#244).

**Los tests T1 a T5** del repro de la r1 siguen siendo K1, K2, K3, K22 y K23. Fallan en main según §1.8, que conserva [UNVERIFIED] sobre la repetición del repro.

**Decididas aquí (Claude, sobre lo que Grok dejó como pregunta):**
- El id de arma lleva su propio contador del cliente, siempre creciente, no el de `player_move`: F y W se informan por separado y compartirlo no aporta nada (§2.6).
- `weapon_floor` va por jugador con la misma identidad que `move_floor`, más el `conn_gen` que el cliente nombra (el del cuerpo, recibido por RPC): `floors[{net_low, net_high, conn_gen, move_id, weapon_id}]`. El servidor aplica F/W solo si ese `conn_gen` es el actual del cuerpo; si el cliente no lo nombra, la fila queda OWED (§2.5, §2.8, §2.16).
- El registro de RPC vive en Enforce, en el bridge que envía el RPC, por id de petición; Python solo ve las filas del censo. Inventario y manos: un acuse de recepción o de emisión no liquida; un rechazo validado antes de emitir sí. Esas filas del servidor no las liquida la salida del cliente (§2.7).
- Los 25 s son el tope del observador de pruebas async (borrado, censo sucio), no una liquidación de inventario. Lo que siga vivo queda como residuo `deletion_pending` y lo miran los cierres siguientes. La duración real de `ObjectDelete` se mide en la ronda final.
- El PR de seguimiento de H7 r1 no bloquea el merge de este. Bloquea que el cuerpo o el CHANGELOG den por cumplida la regla de los jobs (la compuerta de arriba).
- El release del cliente ya lleva hoy el id del movimiento (4_World/MCP_PlayerMove.c:605-609, 794-798 y 935-952); `conn_gen` es [DESIGN]: con él, un release de otra generación no suelta la copia ni sube el suelo (§2.5).

**Respuestas a las preguntas de la r7 (para la r8):**
1. La instrumentación del cuerpo cliente (constructor, destructor, selección y recepción de `MCP_RPC_CONN_GEN`) observa el lifetime en las corridas concretas. No acredita por sí sola G7 ni una garantía universal; G7 lo dice (r7 §6; G7).
2. Ningún consumidor distingue `shutdown` del cierre de un lease (r7 §2; MCPClientBridge.c:4929-4947 y :4953-4969; 4_World/MCP_PlayerMove.c:585-588). La segunda pregunta queda cerrada.

**Preguntas para la revisión 9:**
Ninguna: las dos de la r7 quedan respondidas; no aparece otra frontera abierta en las fuentes leídas.

---

# Lease-end close: design (revision 8)

**Base:** main `75b5d16`. Citation conventions:
- A bare `file:line` is at `2e844c5`. Python files are under `tools/dayz_mcp/` and tests under
  `tools/tests/`. Enforce files are under `addon/scripts/5_Mission/` unless written `4_World/…`.
- Code that #261, #262, #264 and #265 merged into main is written
  `75b5d16 MCPBridge.c:4300` (read from `repo\`). Use that form everywhere those four appear.
- Still-unmerged heads keep the r3 form: `#263@28075ac`, `#266@b4f0611`, `#267@1dde8b9`.
  Do not add new citations to those heads. They remain at bridge version "12".
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

Design D (revision 8) does four things:
1. **Stop, then prove.** At lease end every known peer has one close obligation. A reachable
   destination gets one internal close; an unreachable one is OWED with no command (§2.4).
   Its dispatch stops every controller its bridge owns (§3). Its answer is a census of the
   real state.
2. **Debt that persists.** The coordinator keeps one row per (run, peer). Only a clean close or the
   process exiting clears a row. Every grant while a row is owed says `lease_end_unconfirmed`.
   Degradations merge; they never overwrite the marker.
3. **Order and identity.** Both bridges dispatch HTTP in delivery order. A delivered close
   names the `conn_gen` of the client census that produced F and W. It releases move
   copies with id ≤ F and weapon requests with id ≤ W **only when that `conn_gen` is
   the body's current connection**, whether or not that connection has accepted a
   start. A close that cannot name the connection leaves the row OWED. Raise/fire
   carry a monotonically increasing client id. There is no per-destination admission
   fence for B.
4. **Census covers the leftover channels.** Weapon floors, ActionManager helper plus
   in-flight START/interrupt/END debt (no premature confirm), RPC-in-flight including
   `notify_players` (inventory/hands: validated pre-emit refuse only; those
   server-register rows last until the **server process** exits), deletion
   refs from dispatch, sampler abort, infected debt until process exit.

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
2. Rows already exist in `_release_active_locked` (session_coordination.py:2490-2491)
   before the hook runs. The hook (`cleanup_owner`) publishes one close per row it can
   reach and reports each row. Saturation refuses the hook (2527-2530) after those
   rows exist, so the hook is not the registrar (r3 H10) [DESIGN].
3. Each bridge dispatches its close in FIFO order. The close stops everything, then answers a
   census, directly or through an observer job.
4. Each answer settles its row. The next grant waits until no row is outstanding, or until the
   round's bound. A row that did not settle clean stays owed.

### 2.2 Identity
- **Round:** an id the coordinator mints at each lease end that opens one [DESIGN].
- **Close identity:** daemon generation, round, ending lease id, instance, peer, the binding's
  epoch and the close id.
- **Args:** `close_generation` and `close_lease_id`; the server close also takes `close_floors`
  (move F, weapon W and the client-named `conn_gen` per player, §2.8).
- **Result:** the census echoes generation, lease id and peer. The client census also reports
  `move_floor` F, `weapon_floor` W and `conn_gen` (the body's generation last received
  on the owner client, §2.5). Missing `conn_gen` is not a clean client census.
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
| PENDING | hook timed out (2555-2557) | stays PENDING; a late report before the deadline applies; when the hook later returns or raises, that round's PENDING rows (never another round's) become owed as in the two rows above |
| CLOSING | outcome clean | clear |
| CLOSING | outcome residue | OWED (its entries) |
| CLOSING | outcome rejected (`ok: false`, for example `client_not_in_game`) | OWED (`close_rejected:<error>`) |
| CLOSING | outcome discarded (`lease_close_stale`, `destination_changed`, `binding_retired`, stale expiry, `lease_close_superseded`, `retail_quarantine`) | OWED (`close_discarded:<reason>`) |
| PENDING or CLOSING | deadline passes | OWED (`close_timeout`), no longer outstanding |
| OWED, same round and close | late clean outcome whose close identity still matches, including F/W/`conn_gen` checked atomically | clear that row; a grant already issued is never rewritten (r3 H11) |
| server row of a run, cleared or CLOSING | that run's client close reports a move floor above the server's (§2.8) | OWED (`player_move_floor_unapplied`) |
| server row of a run, cleared or CLOSING | client reports a weapon floor W above the server's | OWED (`weapon_floor_unapplied`) |
| any | outcome for a close that is not the row's current close | ignored and logged |
| any | `note_processes_gone(run, role)` | removed |

Rules:
- Nothing becomes outstanding after its deadline. A late hook report after it does not reopen the
  row.
- One round is current at a time. A new round supersedes the closes of older ones (§2.4).
- The client row of a run is still outstanding when its floor report arrives, so no grant can
  sit between a cleared server row and its floor check. **Q15 / r3 H11:** that "outstanding"
  clause is the punctual result of the floor-report race **before** the late-clean exception.
  A late clean that is still the current close, with identity and F/W/`conn_gen` matching
  under one broker lock, **may clear** an OWED row. A superseded, discarded, or
  identity-mismatch result does not. K17 states that single expectation. A grant already
  issued is never rewritten.

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
  close releases only the move copy whose id is ≤ F, and only weapon requests whose id is ≤ W,
  **and only when the close's `conn_gen` equals the body's current `m_MCPConnGen`**.
  That match is the gate: it does not wait for an accepted start on this connection.
  A close with no `conn_gen` does not raise floors or release copies, and its row is
  OWED (`player_move_conn_gen_unapplied` / `weapon_conn_gen_unapplied`), never a
  clean no-op. A close that names a `conn_gen` that is not the body's current one
  also does not raise floors or release copies (K8 reconnect skip); if that close
  is still the row's current close, the row is OWED, not a clean skip of F/W.
  **Reject** a per-destination admission fence for new mutations while an
  older close is still executable: it would block B [DESIGN]. The client release already carries the move id: `ReleaseAll`
  stores the move's generation as the release id and `FlushServerRelease` writes it
  (4_World/MCP_PlayerMove.c:605-609, 794-798). The server ends only the copy with that id and
  notes `release_of_another_move` otherwise (`MCPMoveReadRelease`, 935-952).
  **[DESIGN, r5 H4, H5]** that release, and every other client→server message on
  the move and weapon channels whose server reader compares a numeric id, also
  carries `conn_gen`. A release of another generation does not end B's copy and
  does not raise `m_MCPMoveLastId`.
  **Connection generation (r3 H5, r4 H5).** `OnReconnect` already releases the old copy and
  resets `m_MCPMoveLastId` (4_World/MCP_PlayerMove.c:880-885, 1143-1148). A **new** client
  process counts ids from 1 again. The **same** process keeps `s_Gen`: `NextMoveId()` is
  `s_Gen+1` (4_World/MCP_PlayerMove.c:120-125), `Begin` adopts it (:530-532), and the
  reconnect reset is the server member, not the client static (:1143-1148). After F=100
  the next id is 101, so a close of ids ≤100 does not reach it. That is why this plan
  does **not** discard a per-instance id as universally broken (the r4 commit message
  and Grok r4 `choice.rejected.b` said the opposite; that reason is false).
  **Alternatives, compared by distribution, id domain, and behaviour before the first
  start** [DESIGN]:
  - Per-instance / process id: domain is the client process. Same-process reconnect
    keeps the counter (good against the F=100 hole). A new process starts at 1, so a
    close of the previous process still needs a connection name. It does not name the
    body's connection before the first start of a new process.
  - `PlayerIdentity.GetPlayerId()`: session id reused after disconnect
    (V130/3_game/gameplay.c:371-372). `GetId()` names the player, not the connection
    (:367-368). Not a connection id.
  - Server `m_MCPConnGen` on the body: one integer per connection of that body,
    assigned at the mission events that already have `(identity, player)`, reported
    by the client census, compared on the close. Works with lastId still 0. **Kept.**
  [DESIGN] the body keeps `m_MCPConnGen`. **Where and when the RPC goes (r4 H5, H9).**
  The value is assigned, and the RPC is sent, from the **mission** events that already
  carry `(identity, player)`, not from `GetIdentity()` inside `PlayerBase.OnReconnect`
  (that native is V130/3_game/entities/man.c:21; vanilla `OnReconnect` only resets
  construction data, V130/4_world/entities/manbase/playerbase.c:7638-7645):
  - first connect: after vanilla in `MissionServer.InvokeOnConnect` (addon
    5_Mission/MissionServer.c:41-45; vanilla calls it at
    `V130/5_mission/mission/missionserver.c:336` for a new body and at `:357` for a
    loaded one; `MCPGodmode.OnBodyReady` already runs there,
    4_World/MCP_PlayerCare.c:88-105). Set gen to 1, then send;
  - reconnect: override `MissionServer.OnClientReconnectEvent` after
    `player.OnReconnect()` (`V130/5_mission/mission/missionserver.c:380-392`,
    :633-637). Increment gen on the body (same reset of lastId as today,
    4_World/MCP_PlayerMove.c:1143-1148), then send.
  Send uses the event's `identity` (null-checked, as 4_World/MCP_Weapon.c:1046-1050).
  The helper that writes and `Send`s lives on the **body** and takes that identity
  explicitly: `rpc.Send(this, MCP_RPC_CONN_GEN, true, identity)` [DESIGN]
  (`ScriptRPC.Send` is `V130/3_game/gameplay.c:117`). From `MissionServer`, the
  helper is invoked on `player`; a `Send(this, …)` on the mission would target the
  mission. The client stores the last value received **on the PlayerBase that
  received that RPC** [DESIGN] and reports it on its census. A missing value is
  OWED; `RegisterNetSyncVariableInt`
  (V130/3_game/entities/entityai.c:2850) does not remove that rule.
  **Messages carry `conn_gen` (r5 H4, H5) [DESIGN].** Every client→server
  message on the move and weapon channels whose server reader compares a numeric
  id writes `conn_gen` next to that id: move start (`SendStart`,
  4_World/MCP_PlayerMove.c:750-761), move release (`FlushServerRelease`, :794-798;
  reader `MCPMoveReadRequest` :901-931, then `MCPMoveReadRelease` :935-952),
  fire (`SendFireRequest`, 4_World/MCP_Weapon.c:653-656; reader
  `MCPReadFireRequest` :723-737) and raise (`SendRaiseRequest`, :681-685; reader
  `MCPReadRaiseRequest` :739-760). The design already gives raise/fire their own
  monotonically increasing client id (§2.6); `conn_gen` travels with that id.
  Symmetric serialization: the server reads the same fields in the same order
  the client wrote them. A message with no generation, or with a generation
  other than the body's `m_MCPConnGen`, is refused; a release of another
  generation does not free B's copy and does not raise the floor.
  **Admission while the client learns the generation [DESIGN].** The known
  generation lives on the client-side body that received `MCP_RPC_CONN_GEN`
  (the same object the helper targeted). `SendStart` / `FlushServerRelease` /
  `SendFireRequest` / `SendRaiseRequest` read that member on `GetGame().GetPlayer()` cast to `PlayerBase` (it returns a `DayZPlayer`, V130/3_game/global/game.c:944; 75b5d16 4_World/MCP_PlayerMove.c:689 and 4_World/MCP_Weapon.c:363 already cast it).
  If it is unset, the **client** refuses the command with `conn_gen_unknown`
  [DESIGN] and does not write the input. The public migration of that refusal (r7 H1) is the whole client path that can return it, not only `player_move`:
  - **player_move.** `SendStart` (75b5d16 4_World/MCP_PlayerMove.c:725-763) checks generation right after the three `unavailable` exits (:728-739) and before `FlushServerRelease` (:740): with no known generation it returns `conn_gen_unknown`, so a start with a pending release and no generation does not answer `input_busy` (:741-744). `FlushServerRelease` also does not send without a generation and keeps the pending (this paragraph). In `DispatchPlayerMove`, as today's `input_busy` (MCPClientBridge.c:2836-2844): `if (serverRequest == "conn_gen_unknown")` with `result.ok = false; result.error = "conn_gen_unknown"; return true;` before `Begin`, with the literal written, not the variable.
  - **weapon_raise and weapon_fire.** `SendRaiseRequest` and `SendFireRequest` (4_World/MCP_Weapon.c:634-680) also read the generation; without it they return `conn_gen_unknown` after the `IsClient` exit and before `CanStoreInputUserData`. Their callers already propagate the value by variable: MCPClientBridge.c:2147-2152 (`result.error = requestError`) and 4_World/MCP_Weapon.c:617-621 (`s_FireReason = refusal`). No other client message that writes generation reaches a tool: `FlushServerRelease` returns nothing (75b5d16 4_World/MCP_PlayerMove.c:769), and the client census travels in `lease_close_client`, which stays out of the public whitelist (§2.16).
  - **Release with no known generation** is not sent: the local overrides still go off, `ReleaseAll` (4_World/MCP_PlayerMove.c:575-609) leaves `s_ServerReleasePending` set, and the retry (:769-800) sends it once the generation is known; `conn_gen_unknown` never counts as a sent release or a clean close.
  If a message still arrives without a matching generation, the **server** refuses it (`conn_gen_mismatch` [DESIGN])
  without raising lastId or releasing a copy. V130 `PlayerBase.OnReconnect`
  (V130/4_world/entities/manbase/playerbase.c:7638-7645) is called by the server (V130/5_mission/mission/missionserver.c:633-639; the method itself has no guard) and
  only resets construction data; it does not say whether the client PlayerBase
  is a new object after reconnect: that cannot be concluded from the sources (r6 §7.1) and is measured in game (G7); until then the server fails closed.
  **[UNVERIFIED] transport hypothesis (r4 H5, kept as context only):** gen 1
  sends id=100; after reconnect a valid gen-2 close applies F=1; an old request
  without generation arrives later and the reader compared only the number
  (4_World/MCP_PlayerMove.c:1011-1022; 4_World/MCP_Weapon.c:723-760). V130 does
  not show whether a connection change drops that old input. This revision
  does **not** wait on G7 to label the messages: G7 cannot close the hypothesis
  by observing nothing (r5 H4), and labelling only START would leave the
  release uncovered (r5 H5). The paragraph no longer loads the design.
  `MCPMoveCloseServer` / `MCPWeaponCloseServer` raise the floor and release copies
  **only while the close's `conn_gen` equals `m_MCPConnGen`**, including when
  nothing has been accepted yet (`m_MCPMoveLastId` still 0 after reconnect). An
  old delivered close with F=100 of a previous generation is then a no-op on
  B's id=1 **and** does not raise B's floor, even if B already accepted a start
  on the new connection. Same connection: F/W still rise and in-flight ids ≤ F
  are refused, including B's own F=1 with start id=1 still in flight. Census
  `floors` echo the body's `m_MCPConnGen`; the **client** census is what names
  the generation the close carries. K8 covers both reconnect holes, not only
  `id > F`.
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
   lists five terminal states today (`completed` · `cancelled` · `timed_out` ·
   `disconnected` · `rejected`); this PR migrates that list to six by adding
   `lease_closed`. `TryFinish` still rewrites a deadline that has already passed as
   `timed_out` (MCPDialogController.c:428-432) [DESIGN, Q11].
2. The UI preview root is unlinked (as 7365-7369).
3. `MCPCarDrive.Clear()` (4_World/MCP_CarScript.c:47-63).
4. `MCPInputTriggerControl.ReleaseAll("shutdown")` (MCPClientBridge.c:337). Steps 4, 5, 8 and 9 pass `"shutdown"`, as `Shutdown` does (MCPClientBridge.c:7382-7405; 4_World/MCP_PlayerMove.c:621), not a new cause (r6 H1): the closed sets are 75b5d16 bridge_errors.py:406-408 and 443-445, player_move.py:47-49 and player_trace.py:45-55 (checked at :122), and 75b5d16 tools/tests/test_player_move.py:53, 75b5d16 tools/tests/test_fb_e1ae_input_trigger.py:82 and 75b5d16 tools/tests/test_player_trace.py:1457 (2e844c5 tools/tests/test_player_trace.py:1440) pin them. No branch distinguishes `shutdown` from a lease-end close; other causes do branch (MCPClientBridge.c:4929-4947 and :4953-4969; 4_World/MCP_PlayerMove.c:585-588). No enum, description or test of those cause sets changes; B learns of the close from the grant's `lease_end` (K21).
5. `MCPPlayerMoveControl.ReleaseAll("shutdown")` (4_World/MCP_PlayerMove.c:568-611). It sends
   the server release, or leaves it to the per-tick retry (605-610, 769-800; `OnTick` 611). It is
   not `ShutdownRelease` (619-624), which drops an unsent release.
6. `RestoreGameplay()` (MCPClientBridge.c:7009-7059): weapon, look-at, simulation unless a handoff
   runs, controls and HUD, focus.
7. `ReleaseCamera()` (7073-7108). An active owned camera starts the 10-tick handoff (430,
   7083-7089). Otherwise the cameras are switched off and deleted now.
8. `MCPHoldControl.Shutdown("shutdown")` (4_World/MCP_ActionHold.c:524-550). `Shutdown` is unchanged (R25). It sets `cancel` with cause `shutdown` only if the hold still has no terminal and no timeout cause (:531-535). A hold that already had a terminal or a published observation keeps it (`finished`/`natural` at :239-243; `Publish` and `Take` at :368-386 and :414-448), and the post takes that observation (MCPClientBridge.c:4207-4219).
9. Sampler abort, as `Shutdown` does (7382-7384) and as `DispatchVehicleRelease` already does
   for vehicle trace (2623-2625): `MCPVehicleTrace.Abort("shutdown")`,
   `MCPAnimTimeline.Abort("shutdown")`, `MCPPlayerTrace.Abort("shutdown")`. This keeps
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
  apart. Today `BeginFire` runs before the send (MCPClientBridge.c:2256-2276; 4_World/MCP_Weapon.c:395-399, 597-626), so W must cover every id written, in that deferred path too [DESIGN, H1];
- live RPC-in-flight entries as residue `rpc_in_flight:<kind>` (§2.7);
- the local player's network id (`GetNetworkID`, as 4_World/MCP_PlayerTrace.c:199);
- `conn_gen`: the last body generation the owner client received on the conn-gen RPC
  and stored on that PlayerBase (§2.5). The close carries **this** value as the identity of F and W. A client
  census without it is not clean (row OWED). The server census still echoes the
  body's `m_MCPConnGen` after step 3 so K8 can compare. Start, release, raise and
  fire of this connection name that same value.

### 2.7 The server close (`lease_close`)
1. `MCPBotControl.ShutdownAll(this)` (MCP_BotControl.c:100-123). Emptying `s_Running` and sending
   stop (270-279) does not prove the bot stopped: `SubmitStop` ignores its result (370-371).
   Vanilla idle is the transition in V130/4_world/systems/bot/bot.c:331-341, applied by the
   local HFSM step V130/3_game/systems/hfsmbase.c:544-568. **r3 H8:** `ProcessEvent` can
   return false from idle with no pending work (V130/4_world/systems/bot/bot.c:383-394);
   completions run before the event (V130/3_game/systems/hfsmbase.c:393-395). Keep the
   object until a helper reads the real FSM state [DESIGN]. Rejected stop **and** state
   not idle / not readable → residue `bot_stop_rejected` or `bot_state_unverified`.
   Idle confirmed after stop (or already idle) may clear that entry. Emptying `s_Running`
   alone never clears. MCP_BotControl.c is in the changed-files list.
2. **Infected (H7).** The bridge records each infected it overrides (MCPBridge.c:3157-3162) and
   forgets it on release (3151-3156) [DESIGN]. The close releases every recorded one with the
   same three calls. It reports `infected_drive` as `unverified` because the native lifetime
   cannot be read (V130/3_game/entities/dayzcreatureaiinputcontroller.c:3-9) [UNVERIFIED].
   **That unverified debt stays until the server process exits.** The close no longer clears it
   by emptying the registry. K27 and G5 forbid a clean grant on the next round from an empty
   registry. A native probe is follow-up and may shorten the debt.
3. **Each player** (`GetPlayers`, as `BuildAllPlayers` does, 5443-5455):
   - `MCPMoveCloseServer(floor, conn_gen)`, new in 4_World/MCP_PlayerMove.c beside `MCPMoveServerRelease`
     (1097-1120). If the close's `conn_gen` equals `m_MCPConnGen` (§2.5), it raises `m_MCPMoveLastId`
     (885) to F, then releases an active copy whose id is ≤ F, **even when lastId is still 0**.
     From then on a start at or below F is refused (1011-1022). A mismatch or a missing
     `conn_gen` does not change lastId or the copy, and is residue
     `player_move_conn_gen_unapplied`, not a clean no-op. Start and release
     readers (`MCPMoveReadRequest` :901-931, `MCPMoveReadRelease` :935-952)
     refuse a message with no `conn_gen` or another generation; that release
     does not free the copy or raise lastId;
   - `MCPWeaponCloseServer(weapon_floor, conn_gen)`, new in 4_World/MCP_Weapon.c beside
     `MCPServerReleaseRaise` (945-959). The server readers, `MCPReadFireRequest` (723-737) and
     `MCPReadRaiseRequest` (739-760), store the last id per player and refuse an equal or lower
     one, as move does (4_World/MCP_PlayerMove.c:1011-1022). `MCPServerApplyFire` (963-998)
     applies only an accepted request. The
     close raises that floor to W, then clears pending requests with id ≤ W and releases an
     armed raise, **only on the same `conn_gen` match**, including when nothing has been
     accepted yet. Mismatch or missing `conn_gen` is `weapon_conn_gen_unapplied`.
     Raise and fire readers refuse a message with no `conn_gen` or another
     generation. Only after that;
   - **Hold, no interrupt (H2, r3 H1).** `m_PendingAction` and `m_CurrentActionData` are
     `protected` (V130/4_world/classes/useractionscomponent/actionmanagerserver.c:3;
     V130/4_world/classes/useractionscomponent/actionmanagerbase.c:61). The census uses a
     4_World helper that reads them without mutating, matching the client helpers
     (4_World/MCP_ActionHold.c:720-743) [DESIGN]. That snapshot is **received** state only:
     START is installed at V130 actionmanagerserver.c:40-61; interrupt at :76-96; Update may
     later Start it (:305-318). A client can finish locally while START is still in
     flight (V130 actionmanagerclient.c:687-714; ActionDropItem UseAcknowledgment false,
     V130/.../actiondropitem.c:74-76). `Shutdown` still sends interrupt
     (4_World/MCP_ActionHold.c:538-546; V130 actionmanagerclient.c:1341-1349). That
     interrupt can still arrive after a **degraded** grant: the server close does not
     send a new one, and this plan does not claim that nothing interrupts the server
     late. `ScriptInputUserData.Send()` is `void` (V130/3_game/gameplay.c:120-131).
     There is no consultable "no unacked send" in V130. **Causal debt, by type
     [DESIGN, r4 H1]:**
     - **START with vanilla ack** (`UseAcknowledgment()`,
       V130/4_world/classes/useractionscomponent/actionmanagerclient.c:695-700 writes
       `m_PendingActionAcknowledgmentID`; `ctx.Send()` is at `:708`, outside
       that write range; server reads it at
       V130/4_world/classes/useractionscomponent/actionmanagerserver.c:63-69; accept juncture
       `DayZPlayerSyncJunctures.SendActionAcknowledgment(..., true)` at
       V130/4_world/classes/useractionscomponent/actionmanagerserver.c:183-187;
       server rejects also at `:124-126`, `:203-206`, `:250`, `:277-279`; client
       matches id at V130/4_world/classes/useractionscomponent/actionmanagerbase.c:158-181).
       **Ack-id capture [DESIGN, r5 §3].** The active MCP request id is known
       before entering the send. The client extension records the association
       in that same call, then checks that the ack id advanced. The key is
       (manager, body); the integer is not process-global. On receive, capture
       must survive `OnSyncJuncture` consuming the context
       (V130/4_world/entities/manbase/playerbase.c:8502-8505); if the pair
       cannot be named, the row is debt. Accept/reject liquidates **only** that
       START's input row, never the hold fence. The matching
       `SJ_ACTION_ACK_ACCEPT` / `SJ_ACTION_ACK_REJECT` is that liquidation.
     - **START without ack** (`UseAcknowledgment()` false, e.g. ActionDropItem
       V130/.../actiondropitem.c:74-76; client starts locally at
       V130/4_world/classes/useractionscomponent/actionmanagerclient.c:710-714): debt until
       the **client process** exits.
     - **interrupt** (V130/4_world/classes/useractionscomponent/actionmanagerclient.c:1341-1349;
       server V130/4_world/classes/useractionscomponent/actionmanagerserver.c:76-96) and
       **END** (`INPUT_UDT_STANDARD_ACTION_INPUT_END` / `END_REQUEST` other than interrupt,
       V130/4_world/classes/useractionscomponent/actionmanagerserver.c:76-109): debt
       until the client process exits. No premature confirm.
     After the client close, residue is `action_input_in_flight` until that
     type's rule fires, or the process exits. The helper alone is
     never a clean grant. Offline: `PerformActionStart` uses `m_PendingActionData` on
     ActionManagerClient (V130 actionmanagerclient.c:804-814); K6 reads that manager, not a
     cast to Server. A captured hold whose player is not in the floors is residue
     `hold_server_unchecked`. **Why no interrupt from the server close:** a late
     interrupt can end B's action; the server does not tell A's action from B's
     (V130/4_world/classes/useractionscomponent/actionmanagerclient.c:1341-1349);
   - the floor's player is found by network id, as `DispatchActionCursorIds` already resolves
     client-reported ids on the server (MCPBridge.c:1898, 1936). Not found: residue
     `player_move_floor_unresolved` or `weapon_floor_unresolved`.
4. **Jobs.** Every `m_Jobs` entry (71) is posted through `PostJobTimeout` (5394) and removed, as at
   its deadline (5191-5197). The exception is a kind that only reads: `server_perf`
   (75b5d16 MCPBridge.c:5276-5300, 6967-6969, 7141-7144) [DESIGN].
5. **RPC in flight (H3, r3 H6).** Each bridge keeps a register, in Enforce, by request id,
   of the RPCs it sends whose effect can arrive later. Insert the row **before**
   `rpc.Send`, because a listen-server refusal can return before Send returns
   (MCPBridge.c:896-901). Kinds in this PR:
   - `hands_take` (MCPBridge.c:1726-1735; send 4_World/MCP_Weapon.c:1067; apply
     :1252-1320, `input_busy` drop :1280-1283);
   - `hands_attach` (MCPBridge.c:1861-1870; send 4_World/MCP_Weapon.c:1456;
     `input_busy` drop :1509-1512, not the generic attach logger at :1460-1463);
   - `inventory_move` (MCPBridge.c:897-901; apply 4_World/MCP_InventoryMove.c:314-340;
     refuse RPC :101-114; server consumer today looks up the **job**
     75b5d16 MCPBridge.c:1660-1683 / MCPBridge.c:1594-1612);
   - `notify_players` (75b5d16 MCPBridge.c:2778-2807, Send at :2804;
     V130/3_game/client/notifications/notificationsystem.c:141-150;
     V130/3_game/dayzgame.c:3192-3204).
   **Terminal protocol [DESIGN, r4 H2, H7].** Request id = the command id. Insert
   before `rpc.Send`. Duplicate or foreign ids are ignored. Abort/timeout of the
   job does not drop the register. The job-ready readback
   (`MCPInventoryMoveReady`, 75b5d16 MCPBridge.c:1405-1419; publication
   :7135-7138) **answers the tool only** (the tool result stays with its job).
   It does **not** delete the register row. It never attributes a quantity or
   location change to an id.
   **Inventory `authority=client`:** the row leaves only on a client refuse
   RPC that names that request id, validated (lookup 75b5d16 MCPBridge.c:1683;
   target/actor/sender :1689-1697) **and** sent before the client emitted
   vanilla manipulation. If the client already called that manipulation, or
   there is no refuse, the row is debt until the **server process** exits.
   Replacing only the client leaves this debt. Another player's client exit
   does not touch it. Cost: grants stay degraded until that server process
   exits.
   Example that must keep the debt (K27): combine X→T (`authority=client`)
   still in flight, another combine Y→T (`authority=server`) raises T,
   `MCPInventoryMoveReady` of X is true (:1413-1417) and must not clear X.
   A job that times out before the close, then the RPC applies with no
   refuse, is the same debt (K27, K30). No observer is required to liquidate
   inventory rows.
   **Hands:** a new client→server refuse/confirm RPC (today there is only a
   local log). A receipt or predictive-emit confirm does **not** liquidate.
   A validated refuse **before** any vanilla emit (`input_busy` take
   :1280-1283, attach :1509-1512) does. Success, or refuse after emit, is
   debt until the **server process** exits. A hands location readback is not
   causal (same class of false clean as inventory).
   **`notify_players`:** no ack in vanilla; `rpc_in_flight:notify_players`
   until the **server process** exits. HUD already shown stays world state (§7). Abort/timeout of the job does
   not drop the register. 4_World/MCP_InventoryMove.c is a changed file.
6. **Deletion proofs (H10, r3 H4).** The bridge register of pending refs is filled
   **at dispatch**, when the job is created: `loadout_clear` copies `job.loadout_cleared`
   (75b5d16 MCPBridge.c:4300-4311, ObjectDelete :4486); `scene_clear` copies
   `job.scene_pending` (:2185-2196, ObjectDelete :2195). Every terminal path of the
   runner that removes the job (timeout :6936-6941; `PostJobTimeout` loadout
   :7308-7312 with `cleanup_complete=false` via :4805-4815; scene :7294-7299 and
   :2334-2364) **transfers remaining live refs into that register first**. The
   server close no longer waits to see the job. The census checks the register.
   The observer waits up to 25 s. What is still live is residue `deletion_pending`.
   Later closes keep checking until the refs are gone or the process exits.
   Native `ObjectDelete` timing is measured in-game, separately.
7. `LeaseCloseExtensionStop()`, a new protected virtual, empty in the base (for #267, §4).
8. **Census**, plus `floors`: each player's `m_MCPConnGen`, `m_MCPMoveLastId` and weapon last id after step 3.
   An async proof goes to an observer job in `m_Jobs` (kind `lease_close`, deadline `m_ElapsedS`
   + 25 s). `IsJobReady` (5204) re-evaluates it, and `PostJobSuccess` (5325) or `PostJobTimeout`
   (5394) posts it. Every evaluation also calls `LeaseCloseExtensionCensus(census)`.

### 2.8 Across peers: the move floor and the weapon floor (H1, H5)
- The client close goes first, so the client stops sending before the server is closed. Its
  step 5 ends the local move and sends the release (with the move id **and** `conn_gen`), and its step 6 drops the
  client weapon override.
- The client census reports move floor F, weapon floor W and `conn_gen` (§2.6). The server
  close carries F, W and **that client-named** `conn_gen` for that player and reports them after applying.
- The server row is clean only if the close's `conn_gen` equals the body's `m_MCPConnGen`
  (whether or not a start has been accepted) **and** its reported move floor is at least F
  and its reported weapon floor is at least W. Then every start the client ever sent **on
  that connection**, and every raise/fire at or below W, is refused from then on
  (4_World/MCP_PlayerMove.c:1011-1022; 4_World/MCP_Weapon.c:723-760, 963-998), even one
  still in flight. A close of a previous `conn_gen` is a no-op on the new connection's
  floors and copies, so B's move and weapon state stay as they are; the row of that
  stale close is OWED while it is the row's current close (§2.5), and so is the row of a
  close that names no generation.
- If the server close went first (the 10 s rule) and the client then reports an F or W above the
  server's reported floor, the server row becomes OWED (`player_move_floor_unapplied` or
  `weapon_floor_unapplied`). That happens before any grant (§2.3).
- Raise and fire leave the out-of-scope list: they use the same id protocol as `player_move`.
- **H5 tests:** (a) same connection: the server close is delivered; the round times out; B
  gets a degraded grant and starts a move by input with id > F; the old close then runs;
  B's move continues. (b) replace/reconnect: body kept, `OnReconnect` resets last id
  (4_World/MCP_PlayerMove.c:1140-1147); B starts at id=1 with a new `conn_gen`; the old
  delivered close with F=100 of the previous `conn_gen` does not release or raise the
  new copy, **even if B's first start on the new connection was already accepted**.
  (c) fresh connection, nothing accepted yet: B's close names the new `conn_gen` with
  F=1 while start id=1 is still in flight; after the close, that id=1 is refused.

### 2.9 Holds (H5)
- **Lease end.** `cleanup_owner` still forgets a delivered hold's row and fences the hold, as the
  abandon does today (`_abandon_row_locked` 4162-4217; `_settle_hold_for_command_locked`
  5230-5242). It no longer publishes `action_hold_cancel` and no longer adds
  `hold_cancel_unconfirmed` (today 5317-5341) [DESIGN: `abandon_command` gains a flag that
  suppresses the release]. A's `pending_commands` drops at once; today the cancel kept it at 1
  (4323-4326).
- **The close aborts the hold** (§2.6 steps 8-9). The hold's own result does **not** by itself
  clear the instance fence (r3 H2). Today `_note_hold_result_locked` with
  `cleanup_complete=true` calls `_close_hold_locked(..., needs_fence=False)`
  (loopback.py:5272-5273) from the client half only. [DESIGN] that note may mark the
  client half done; the fence stays until the server helper is idle and in-flight
  action input is settled by the per-type rules of §2.7 (no "unacked send" path).
- **Census entry, client.** It is the test the next `action_hold` dispatch uses to refuse: the
  local action manager is busy, or the hold control is active (MCPClientBridge.c:3863, 4086;
  4_World/MCP_ActionHold.c:54-57, 720-733). It reads the action manager, not the job, so `Take`
  clearing the active state (433-449) cannot make it clean. That is only the client half.
- **Census entry, server (H2).** §2.7 step 3. The hold fence liquidates only when both sides are
  clean: client admission predicate, server helper idle, and in-flight input
  settled per §2.7 type rules (START-with-ack only via the recorded vanilla
  juncture, which never liquidates the hold fence; START without ack, interrupt,
  END: client-process exit). An interrupt left by Shutdown
  (4_World/MCP_ActionHold.c:538-546) is deliberate debt on **that** instance
  while the sending client process lives: B's `action_hold` there gets 409
  `hold_unreconciled` and a degraded grant; B's client close is not settled.
  After replace-role (§2.12, D:812-821) the new instance's holds are admitted,
  because the old instance's records do not block it. There is no new causal
  terminal for interrupt (R25): V130 has none.
- **Fence is per instance, not per run (H9).** When an instance exits or is replaced, its exact
  hold records become server-row debt `hold_server_unverified`. That debt lasts until a later
  server close reads that the player has no current or pending action (H2), or the server
  process exits. Holds of the new instance are not blocked by the old instance's records
  (loopback.py:2495-2507, 3711-3713, 5138-5173, 5259-5278).
- **Settlement.** A clean close on **both** sides closes, without fence, every hold the broker
  captured at that close's delivery (`_close_hold_locked(…, needs_fence=False)`, 5165-5173). That
  covers a lost hold result. A residue keeps the fence: the next hold on **that instance** gets
  409 `hold_unreconciled` (3711-3713), and the row is owed.
- **Either order (r3 H2).** If the **both-sides-clean** close settles first, the hold's own
  result is a no-op (5270-5271). If the hold result arrives first with
  `cleanup_complete=true`, it records the client half and **leaves the fence** until the
  server close is both-sides clean. K16 NEG: own result first, server helper still busy
  → fence stays, next hold 409, grant degraded. The existing note is not sufficient.
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
- #262's merged deferral is 75b5d16 MCPBridge.c:367-388. Its batch command creates and
  answers in its own dispatch (75b5d16 MCPBridge.c:1832-1929), so nothing changes for it.
- **Occupancy.** A poll still accepts at most `MAX_DISPATCH_PER_TICK + MAX_PENDING` commands.
  Client: that sum is `MAX_POLL_RESULTS` 20 (75b5d16 MCPClientBridge.c:409-415). Server:
  `MAX_POLL_RESULTS` 64 (75b5d16 MCPBridge.c:6-8). A batch that arrives on a non-empty
  `m_Pending` loses its 4 inline slots. The client refusal is `client_bridge_queue_full`
  (75b5d16 MCPClientBridge.c:1043-1046), which also POSTs (client `PostResult`
  :7428-7435). Server refusals POST too (`PostResult` 75b5d16 MCPBridge.c:7451-7458).
  `MAX_POLL_RESULTS` is a resource reserve, not a cap that clips the batch: callbacks
  walk `batch.commands.Count()` (MCPBridge.c:319-349; MCPClientBridge.c:877-895).
- **Callback budget (r4 H3).** Each peer's reservation is the maximum POSTs one
  admitted batch can produce on that peer: accepted results, **refusals**, and the
  close [DESIGN]. Server: `StartPoll` admits when
  `m_CallbackRefs.Count() + m_Pending.Count() + m_Jobs.Count() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS`
  is false (75b5d16 MCPBridge.c:6-8, :303-305). **Both bridges reserve 65
  results:** 64 ordinary (accepted or refused) plus the close. Admission
  threshold: outstanding 63 of 128 may admit the poll; 64 must not
  (63 prior + 65 new = 128). A poll of 64 plus one close is 65 POSTs.
  [DESIGN] the server reserves `MAX_POLL_RESULTS + 1` (=65). Client:
  `OutstandingWork() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS`
  (75b5d16 MCPClientBridge.c:767-799). Reserving `20 + 1` does not cover a
  producer-sized batch: daemon `MAX_QUEUE` is 64
  (75b5d16 test_guards_bridge.py:80-83); extras refuse and POST. [DESIGN] the
  client reserve is **65**, the same producer max (admitted + refusals +
  close), not `MAX_POLL_RESULTS + 1`. K25 uses that real producer (64 + close),
  not 20.
- **Stage 1 implementation note.** Client `MAX_POLL_RESULTS` is 64
  (MCPClientBridge.c:415) so StartPoll
  `OutstandingWork() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS - 1`
  (MCPClientBridge.c:798) reserves 65, matching the server
  (MCPBridge.c:8, MCPBridge.c:307). The plan's remark that the client reserve
  is not `MAX_POLL_RESULTS + 1` named the previous 20; after stage 1 both
  peers subtract `MAX_POLL_RESULTS + 1` from `MAX_CALLBACK_REFS`.
  K24 also polls `[A, world_spawn, B]` with nothing pending: A runs in the
  callback, and `world_spawn` and B wait for `DrainPending`.
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

- **Stage 2a implementation note.** The coordinator half of §2.3, §2.8, §2.11 and §2.12 is in
  `tools/dayz_mcp/session_coordination.py`, in memory only (no snapshot, WAL or audit line). The
  broker, `cleanup_owner`, the daemon, the lifecycle, the bridges, version 13 and the docs come
  in later stages. The criteria are `tools/tests/test_lease_close_broker.py`. The line numbers
  below are round 6's.
  - State: `LEASE_END_BOUND_S` (16), `_Lease.delivered_work` and `lease_end` (233-234), the row
    and the round (250-274), the bound's check (353-360) and the coordinator's fields (394-397).
    `claim_dispatch` marks delivered work (1613).
  - The API is 1616-1822. Each method, on entering its locked section, turns expired rows into
    OWED (`_expire_rows_locked`, 4560-4570), and so does `_expire_due` (2735).
  - The round opens right after `_releasing` is set (2802; 4572-4613). A saturated hook owes the
    round's pending rows at once (2859), one that raises owes the reported ones too (2890), and
    one that returns owes the rows it did not name (2892).
  - `_lease_end_blocks_locked` (4532-4542) joins the grant checks at 555, 583, 1222, 3197 and
    3770. `wait` wakes at the earliest deadline (1317-1319), and so does the preferential
    reacquire (561-567). `_new_lease_locked` takes the grant's photo (3463);
    `_grant_payload_locked` (4504-4514) copies it at 512, 792, 3438, 3686 and 4154, and
    `_merge_cleanup_degraded` (4516-4530) keeps the debt's warning on every payload path. Status
    adds `lease_end` (2309; 4689-4708) and computes it and `claimable` (2301) from one clock
    reading (2296).
  - Outcomes go through `_apply_close_outcome_locked` (4710-4733) and its helpers (to 4894); the
    floors are compared by `_floor_gap` (4913-4932).
  - Restart debt takes the registry's instance for its run and peer, or "" when none is bound
    (1663). No peer is bound yet at daemon start (§2.12), and the stage 2b broker reports such a
    row unreachable.
  - Only a server close may be deferred (§2.3). A client `deferred` report is not accepted, so
    that row ends `close_not_reported` when the hook returns.
  - A late clean of an OWED server row whose floors fall short answers `applied` and changes
    nothing: it is the row's current close, so it is not `ignored`, and the row keeps its
    reasons. One whose floors pass clears the row and stores them.
  - The ids in every report and restart pair are checked before any row changes, so a bad id
    raises with nothing applied, and a published close needs a `close_id` (1650-1654, 1717-1723,
    1776).
  - After a cleared server row, a client floor is compared only with the floors that row's clean
    stored in this round (4821-4894). A run with no server row brings none back, and neither does
    one `note_processes_gone` removed, which also drops its stored floors (1639-1643). The row a
    client floor brings back requires that floor, as a CLOSING one does.
  - r1 H1 to H5 are the findings of Codex Sol's first review of stage 2a, which round 2 fixed;
    r2 H1 to H5 are those of its second review, which round 3 fixed; r3 H1 to H7 are those of its
    third review, which round 4 fixed; r4 H1 and H2 are those of its fourth review, which round 5
    fixed; r5 H1 is that of its fifth review, which round 6 fixes.
  - r1 H1. A late hook finish stores `round_id` at 2803-2807; `invoke_cleanup`'s `finally`
    (2836-2852) sets `finished` and, if `abandoned`, owes that round (`cleanup_failed` with
    `include_reported` if it raised, else `close_not_reported`). The timeout branch sets
    `abandoned` when `finished` is unset (2884-2887). `_owe_round_pending_locked` takes
    `round_id` (4615-4634).
  - r1 H2. When the CLOSING row carries a floor for that player, a client floor of another
    `conn_gen` owes the two conn_gen reasons, in `_floor_gap`'s order, without comparing ids
    (4863-4892).
  - r1 H3. For the grant and `wait`: `_expire_rows_locked` accepts `now` (4560-4562).
    `_lease_end_blocks_locked` expires with that same reading (4532-4535).
    `_lease_end_wait_remaining_locked` returns 0.0 when a pending or closing row is already due
    (4544-4555).
  - r1 H4. `acquire` (433-434) and `enqueue` (908-909) merge `degraded` onto an `operation_id`
    retry.
  - r1 H5. Tests pin deferred-then-published and `finish_operation_exact` leaving
    `delivered_work`.
  - r2 H1. When the CLOSING row carries no floor for that player, it now keeps the client's floor
    as a requirement, ids 0 included (4888-4889), and the server clean compares its floors with
    it (`_server_clean_reasons_locked`, 4779-4786): another `conn_gen` owes the two conn_gen
    reasons, and a player missing from the server census owes the two floor reasons. When the
    server reports first, the row the client floor brings back already owed the same reasons.
  - r2 H2. K9's deferred-then-published test now applies cleans after the publication: one with
    the deferred report's close id is `ignored`, one with the published id clears the row, and the
    next grant's `settled` names that id.
  - r2 H3. Status expires the rows with one clock reading and computes `outstanding`,
    `deadline_in_s` and `unconfirmed` from it (4696-4708), so a deadline that crosses inside
    status cannot show a row as neither outstanding nor owed. `_lease_end_wait_remaining_locked`
    and `_unconfirmed_list_locked` take `now` (4544-4548, 4652-4656). Status no longer needs
    `_expire_due`'s expiry. A queued `wait` during a long cleanup still does (2735), because its
    loop skips the fence while `_releasing` is set (1218-1222); a test pins it. Round 3 said
    status read the clock once; `claimable` still came from an earlier reading (r3 H4).
  - r2 H4. The preferential reacquire's body expires the rows and caps its wait with the rows'
    term from one reading (561-567). Round 3 said "as `wait` does", which was wrong: `wait` takes
    its term from a reading of its own (1317) and goes round again when a row is already due
    (1320-1321).
  - r2 H5. The tests order their phases with events and with the waits a recording condition
    sees, not with sleeps. Round 3 said every time bound left was 10 s or more; K11 still gave
    the wait of its crossing test a limit of 1 s (r3 H2).
  - r3 H1 (P1). K11's waiter could pass without waiting: B's 202 came before the real 0.2 s
    deadline, but the waiter could start after it and be granted at once, so a recorder with no
    wait passed, as did the mutant without the rows' term. The test now runs on the fake clock
    with a bound of 0.5 s: B queues whatever the scheduling, the test waits for the waiter's
    first recorded wait (no longer than 0.5 s), and only then moves the clock past the deadline,
    with no notify; the wait returns 200 with `lease_end_unconfirmed`.
  - r3 H2 (P1). The crossing test's wait now has the call's limit of 30 s, and the recorder fails
    a wait longer than 0.01 s at once, so the mutant without the rows' term fails in seconds
    instead of waiting out 30 s per subtest. The only real-time assertion left in the module is
    K19's 10 s, against a hook that would hold status and acquire for 30 s. The bounds of the K11
    tests run on the fake clock, and the 0.01 s and 0.5 s caps check the timeouts the recorder
    sees, not elapsed time. The hook timeouts of 0.03 s and 0.05 s are real, and a slow runner
    only makes the release return later. Every other real-time number ends a stuck run: the 30 s
    waits for events and for recorded waits, and the 30 s and 40 s joins.
  - r3 H3 (P2). When the preferential loop's body expires rows, it goes round again (562-564)
    instead of waiting: those rows may have been all it waited for. A test crosses the deadline
    between the loop's fence and its body, and the reacquire gets 200 with
    `lease_end_unconfirmed` with no wait longer than the rows' term; on `b95b158` its recorded
    wait was its own limit, 30.05 s. Round 4's test still let the reacquire wait as long as the
    rows' term after the crossing (r4 H2).
  - r3 H4 (P2). Status passes its reading to `claimable` too (2296, 2301;
    `_claimable_locked(now)`, 3764-3776, and `_lease_end_blocks_locked(now)`), so a deadline that
    crosses inside status can no longer give `claimable` false with nothing outstanding. The
    status photo test checks `claimable` against the photo, with and without a recovery fault.
    Round 4 said a mutant in which `claimable` reads the clock again survived and was
    equivalent; it is not (r4 H1).
  - r3 H5 (P2). With the fake clock, a pause before B's acquire no longer uses up its deadline
    (r3 H1).
  - r3 H6 (P2). K9 now publishes the deferred server close with the client's floor F=7 as its
    carried floor: a server clean reporting F=5 leaves the row OWED
    (`player_move_floor_unapplied`), one reporting F=7 clears it, and a publication that drops
    its carried floors (mutant) fails.
  - r3 H7 (P2). K21 checks the ages: with the server row owed since 0, the client row CLOSING,
    and a clock that moves 0.25 s on every read, the owed row's `since_s` plus `deadline_in_s` is
    the bound, which `unconfirmed` aged from another reading (mutant) fails.
  - r4 H1 (P1). A `claimable` that reads the clock again is not equivalent. With the server row
    owed since t=0 and the client row CLOSING, due at D, a status whose photo reads the clock just
    before D and whose `claimable` reads it after D shows the slot claimable and both rows owed,
    with the server row's age short of the bound: the rows' states come from one reading and
    their ages from another. A test with that debt puts the crossing at each point among
    status's clock reads in turn (k from 0 to 40) and checks one photo: while the client row is
    outstanding, the slot is not claimable and the row's `deadline_in_s` plus the server row's
    `since_s` is the bound; once both rows are owed, the slot is claimable and that `since_s` is
    the bound at least. Both forms of the mutant fail it: `_claimable_locked` reading the clock
    itself, and status passing it a reading of its own.
  - r4 H2 (P2). The fence of the preferential loop runs only once no lease is active and the
    cleanup, the grant in flight, the WAL marker and the handoff are all clear (550-555), so when
    the crossing test moves the clock there, the rows were all the loop had left to wait for.
    Round 4's test still accepted a wait up to the rows' term after that: a loop that expires the
    rows and then waits out the bound (mutant) passed, and so did `b95b158` when the driver paused
    long enough before letting the cleanup finish. Round 5's recorder fails any later wait of the
    reacquire at once, and the test checks that none was recorded after the crossing; a run in
    which the loop's body never ran after the crossing still passed (r5 H1).
  - r5 H1 (P1). The preferential loop's own limit runs on the real clock (546-548:
    `cleanup_timeout_s` plus `RELEASE_AUDIT_TIMEOUT_S`, 30.05 s in these tests), and the rows'
    deadline on the fake one. When the driver lets the cleanup finish just before the hook's own
    30 s and the reacquire runs again past that limit, the fence still sees the rows outstanding
    and the test crosses the deadline there, but the limit's check breaks before the body
    (558-560); the admission's fence (583) then expires the rows, and the grant is 200 with
    `lease_end_unconfirmed`. Round 5's test passed that run with `b95b158`'s body or the mutant,
    neither of which ran, so K11's "whatever the driver's timing" did not hold. The test now
    records the expiries of the loop's body (562, the one call of `_expire_rows_locked` in
    `acquire` itself) and requires one after the crossing that turned rows OWED, so that run
    fails instead of passing. A limit check that breaks before the body once the rows are all
    the loop waits for (mutant) stands for that run: alone, with `b95b158`'s body or with the
    body that waits out the bound, it passes round 5's test and fails round 6's.
  - r6 H1 (P2). Round 6's test could fail with the coordinator as it is: if the driver paused
    before it let the cleanup finish, or the reacquire paused after the crossing, until the
    loop's own limit ran out, the loop left before its body, the admission's fence granted as it
    should, and the test failed for want of the body's expiry. The limit reads the coordinator's
    `time.monotonic` (546-548, 558), so the crossing now runs twice with that module's `time`
    replaced by a namespace whose `monotonic` is a fake clock apart from the rows' one, which
    only the test moves. Held still, no pause runs the limit out, and the first test always
    reaches the body. The second moves it an hour on at the crossing, so the loop leaves before
    its body (558-560) and the admission's fence (583) expires the rows; it checks the 200 with
    `lease_end_unconfirmed`, no wait after the crossing and no expiry by the body. A loop with
    no limit check (mutant) fails the second, and the limit check that breaks before the body
    (mutant), alone or with either body, still fails the first. The fixture's events and joins
    keep their finite real-time limits, and its cleanup moves the clock an hour on, so a
    reacquire still in the loop then leaves at its next look.

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
- **No code changes in control_client.py.** Status still shows a round as `lease_end` (§2.11), but recovery's idle check (control_client.py:830-857) does not read that field (r6 H5), so recovery ignores the round; that holds
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
  `lease_close_enqueued`. It does not create the rows; `_release_active_locked` already
  did (§2.1).
- `vehicle_active` stays in the hook signature (daemon.py:89-97; session_coordination.py:2511-2516)
  but is no longer read.
- **product-spec.md H5 (r3 H12).** Line 140 still says release/expiry attempt
  `vehicle_release` without retail quarantine. This PR replaces that attempt with the
  aggregated close. Update H5 and its evidence: cancel undelivered commands, publish
  one close per known peer (or OWED if unreachable), keep quarantine fail-closed
  (`retail_quarantine`, no mutation), do not kill DayZ/daemon, do not revive commands
  on reconnect. `vehicle_release` remains the mid-lease abandon path in
  `RELEASE_REGISTRY`. Dialog states migrate from the five in
  product-spec.md:426-429 to six (`lease_closed` added, §2.6).

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
    `conn_gen` (client census: last received body generation), `net_low`, `net_high` and
    `floors[{net_low, net_high, conn_gen, move_id, weapon_id}]`;
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
  MCP_BotControl.c, addon/scripts/5_Mission/MissionServer.c (2e844c5 :41-45
  `InvokeOnConnect`; [DESIGN] `OnClientReconnectEvent` after vanilla
  `player.OnReconnect()`, V130/5_mission/mission/missionserver.c:633-637; the
  body's helper sends with the event identity, target = that player),
  4_World/MCP_PlayerMove.c, 4_World/MCP_Weapon.c,
  4_World/MCP_ActionHold.c and 4_World/MCP_InventoryMove.c.
- **Python:** loopback.py, session_coordination.py, process_lifecycle.py, daemon.py, core.py,
  bridge_readiness.py, ui_dialog.py, server.py (`conn_gen_unknown` in the
  `player_move`, `weapon_raise` and `weapon_fire` descriptions, 75b5d16
  tools/dayz_mcp/server.py:8864-8869, :7478 and :7546) and product-spec.md (six dialog states; H5
  release/expiry uses the aggregated close, r3 H12; vehicle_trace cleanup unchanged
  in meaning).
- **Unchanged:** control_client.py, dayz_test_tool.py and
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
| C3 | Local move (`player_move`) | `MCPPlayerMoveControl.ReleaseAll` | `IsActive()`, or `s_ServerReleasePending` | overrides off, server release sent with move id **and** `conn_gen` | 4_World/MCP_PlayerMove.c:107-113, 568-611, 769-800, 794-798 |
| C4 | Client weapon override (`weapon_*`) | `RestoreGameplay` → `MCPWeaponControl.ReleaseAll` | `IsBusy()` | raise and aim off | 4_World/MCP_Weapon.c:94, 212-230; MCPClientBridge.c:7013 |
| C5 | Look-at (`player_look_at`) | `RestoreGameplay` → `MCPLookAtControl.Release` | `IsActive()` | released | 4_World/MCP_LookAt.c:92, 107; MCPClientBridge.c:7014 |
| C6 | Camera (`camera_set`) | `ReleaseCamera()` | `m_ActiveCam` set, a handoff pending, or the free camera active | handoff finished after 10 ticks, static camera deleted | MCPClientBridge.c:430, 508, 7073-7108, 7147-7230 |
| C7 | Player simulation off (`camera_set` free, handoff) | `RestoreGameplay`; `FinishCameraHandoff` | `m_PlayerSimulationDisabled` | simulation on | 5754-5758, 7033-7040, 7219-7223 |
| C8 | Controls and HUD suppressed | `RestoreGameplay` | `m_ControlsSuppressed` | controls on, HUD shown | 6877, 7048-7056 |
| C9 | Dialog (`ui_dialog`) | `FinishLeaseClosed()` (new) | `m_Dialog.IsOpen()` | root hidden, input unlocked | MCPDialogController.c:125-128, 306-317, 418-454 |
| C10 | UI preview root | `Unlink()` | `m_UiPreviewRoot` set | unlinked | MCPClientBridge.c:523, 7365-7369 |
| C11 | Action hold (`action_hold`) | `MCPHoldControl.Shutdown`, then the job's own post | action manager busy, hold control active, or `action_input_in_flight` | client predicate false **and** server helper idle **and** in-flight START/interrupt/END settled per §2.7 type rules (START ack liquidates only that input row, never the hold fence; no unacked-send shortcut) | 4_World/MCP_ActionHold.c:54-57, 524-550, 720-743; MCPClientBridge.c:3863, 4086, 4200-4220; V130/4_world/classes/useractionscomponent/actionmanagerserver.c:3, 40-61, 76-96, 124-126, 183-187, 203-206, 250, 277-279; V130/4_world/classes/useractionscomponent/actionmanagerclient.c:687-714, 1341-1349; V130/4_world/classes/useractionscomponent/actionmanagerbase.c:158-181 |
| C12 | Jobs, every kind, #264 `player_view_set` included | `AbortAll` through `MCP_PostJobTimeout` | runner count minus the observer | each kind's timeout post | MCPJobRunner.c:104-169; MCPClientBridge.c:5948-6010; 75b5d16 MCPClientBridge.c:1239, 4935, 6802 |
| C13 | Samplers: anim timeline, player trace, vehicle trace | abort as `Shutdown` / `DispatchVehicleRelease` | sampler still capturing | abort posted; A's samples do not continue | MCPClientBridge.c:2623-2625, 615, 619, 7382-7384; product-spec.md:122 |

**Server**

| ID | Controller (started by) | Stop step | Active when | Proof of cleanup | Anchors |
|---|---|---|---|---|---|
| S1 | Bots (`bot_start`) | `MCPBotControl.ShutdownAll`; keep object until FSM readback | `s_Running` not empty, or helper not idle / unread | idle FSM state, not only empty registry; reject+not-idle is residue; idle after reject may clear | MCP_BotControl.c:15, 100-123, 260-281, 360-375; V130/4_world/systems/bot/bot.c:331-341, 383-394; V130/3_game/systems/hfsmbase.c:393-395, 544-568 |
| S2 | Infected overrides (`infected_drive`) | release each recorded infected (new registry) | unverified debt until process exit | none provable by reading; empty registry is not clean | MCPBridge.c:3091-3173; V130/3_game/entities/dayzcreatureaiinputcontroller.c:3-9 |
| S3 | Move server copy (client `player_move`) | `MCPMoveCloseServer(floor, conn_gen)` (new); only if close `conn_gen` equals `m_MCPConnGen` (accepted or not), copies with id ≤ F | `m_MCPMoveActive` set, or floor below the client's on this connection | copy released, older starts refused; other-gen close is a no-op; missing gen is OWED; start/release without matching `conn_gen` refused, other-gen release does not free the copy or raise lastId | 4_World/MCP_PlayerMove.c:872, 885, 935-952, 1011-1022, 1097-1120, 1140-1147 |
| S4 | Weapon server copy (client `weapon_*`) | `MCPWeaponCloseServer(W, conn_gen)` (new); same `conn_gen` match, including last id 0; then refuse id ≤ last; then clear ≤ W | armed raise, pending request, or floor below W | last id stored; older raise/fire refused; missing gen is OWED; raise/fire without matching `conn_gen` refused | 4_World/MCP_Weapon.c:696-703, 723-760, 795-812, 945-959, 963-998 |
| S5 | Jobs: spawn, seat, `inventory_move`; deletion refs of `loadout_clear`, `scene_clear` | register refs at dispatch; every timeout path transfers them before dropping the job | `m_Jobs` minus kept kinds and the observer; `deletion_pending` | timeout post plus register empty or residue | MCPBridge.c:71; 75b5d16 MCPBridge.c:4300-4311, 4486, 4805-4815, 6936-6941, 7308-7312; :2168-2196, 2312-2328, 2334-2364, 7294-7299 |
| S6 | RPC in flight: `hands_take`, `hands_attach`, `inventory_move`, `notify_players` | none recalled; register by request id before send | register not empty | inventory/hands: validated pre-emit refuse with that id, or **server-process** exit; job-ready never clears; notify until **server-process** exit; client-only replace leaves the debt | MCPBridge.c:896-901, 1726-1735, 1861-1870; 75b5d16 MCPBridge.c:1405-1419, 1660-1683, 2778-2807; 4_World/MCP_Weapon.c:1067, 1252-1320, 1280-1283, 1456, 1509-1512; 4_World/MCP_InventoryMove.c:101-114, 314-340; V130/3_game/client/notifications/notificationsystem.c:141-150 |
| S7 | `server_perf` | kept | not censused | it only reads | 75b5d16 MCPBridge.c:5276-5300, 6967-6969, 7141-7144 |
| S8 | Extensions (#267) | `LeaseCloseExtensionStop()` | the entries `LeaseCloseExtensionCensus()` adds | set by the extension | §4 |
| S9 | Server action (hold and any other) | census only; no new interrupt from the server close (a late interrupt can still arrive under a degraded grant) | helper current/pending, `action_input_in_flight`, or `hold_server_unchecked` | later close: helper idle **and** per-type §2.7 settlement (START ack = that input row only), or client-process exit for interrupt/END/unacked START; no unacked-send shortcut | 4_World/MCP_ActionHold.c:538-546, 720-743; V130/4_world/classes/useractionscomponent/actionmanagerserver.c:3, 40-61, 76-109, 124-126, 183-187, 203-206, 250, 277-279, 305-318; V130/4_world/classes/useractionscomponent/actionmanagerbase.c:61, 158-181; V130/4_world/classes/useractionscomponent/actionmanagerclient.c:687-714, 1341-1349 |

Not controllers: godmode, which is on by default by mission policy (4_World/MCP_PlayerCare.c:1-21),
and #262's `world_spawn_batch`, which creates and answers in its own dispatch
(75b5d16 MCPBridge.c:1832-1929). Both are world state (§7). HUD already shown by
`notify_players` is world state; an unacked send of that RPC is S6.

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
  MCPClientBridge.c:7064-7073); that is not both playing and pending (r2 D:648-649).
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
| K6 | Offline binding | one offline binding → two closes on one instance queue; each poll takes only its own (4613-4615); census reads ActionManagerClient (`m_PendingActionData`, V130 actionmanagerclient.c:804-814), not a cast to Server | a server poll never returns `lease_close_client` |
| K7 | Identity | exact echo → clear | wrong generation, lease or peer → ignored, row CLOSING; another instance → `discarded`, row CLOSING; epoch bumped and re-BOUND → OWED (`binding_epoch_changed`) |
| K8 | Delivery gate (H5) | close queued, no lease → delivered; delivered close, round times out, B degraded grant, B starts move by input with id > F **same `conn_gen`**, old close then runs → B's move continues; reconnect: body kept, new `conn_gen`, B id=1, old close F=100 of previous `conn_gen` does not apply **even after B's first start on the new connection is accepted** (B's move continues, new floor unchanged); fresh connection, nothing accepted: B's close names the new `conn_gen` with F=1 while start id=1 is in flight → that id=1 is refused | deadline passes, B granted degraded, next poll of a still-queued close → `lease_close_stale`; round 1's queued close when round 2 publishes → `lease_close_superseded`; a per-destination admission fence for B is not added; close with no `conn_gen` → OWED, not a clean no-op; start or release with no `conn_gen` or another generation → refused; a release of another generation does not free B's copy or raise lastId |
| K9 | Hook failures (H6) | normal hook → rows CLOSING; a deferred server close published later → a clean with the published close id clears its row, and the next grant's `settled` names that id; published with the client's floor F=7 as its carried floor → a server clean reporting F=7 clears its row | a clean with the deferred report's close id → `ignored`; a server clean of that publication reporting F=5 → OWED (`player_move_floor_unapplied`), and a publication that drops its carried floors (mutant) clears it instead; slots exhausted → OWED (`cleanup_worker_saturated`) at once; hook raises → OWED (`cleanup_failed`); hook returns `cleanup_fenced` → OWED (`close_not_reported`); hook past `cleanup_timeout_s` reports before the deadline → CLOSING, after it → stays OWED; a late hook return owes that round's PENDING (`close_not_reported`); a late raise owes them (`cleanup_failed`, including reported); neither touches another round's PENDING |
| K10 | Past `MAX_QUEUE` (H6) | client queue holds `MAX_QUEUE` internal releases → the close is still published and delivered | at that queue `_publish_release_locked` refuses a release with `queue_full` (4296-4300), which proves the queue was full |
| K11 | The waiter wakes (H9) | fake clock, bound 0.5 s (constructor override): B queues (202), and a thread in `wait` with its own limit of 30 s first waits no longer than 0.5 s; the test waits for that first recorded wait, then moves the clock past the deadline with no other call and no notify, and the wait returns 200 with `lease_end_unconfirmed`, none of its recorded waits longer than 0.5 s; a deadline that crosses inside acquire or wait turns expired outstanding rows OWED (`close_timeout`), and wait, with its own limit of 30 s, returns 200 with `lease_end_unconfirmed`, a recorded wait longer than 0.01 s failing at once; status lists a row past its deadline as OWED (`close_timeout`), also with a recovery fault armed; a deadline that crosses inside status, with or without the fault, gives one photo: `outstanding` plus the `unconfirmed` rows is 2, `deadline_in_s` is null exactly when `outstanding` is 0, positive otherwise, and `claimable` is true exactly when there is no fault and `outstanding` is 0; with the server row owed since 0 and the client row CLOSING, a deadline that crosses at any point among status's clock reads also gives one photo: while the client row is outstanding, `claimable` is false and its `deadline_in_s` plus the server row's `since_s` is the bound, and once it is owed, `claimable` is true and that `since_s` is the bound at least; during a long cleanup, the preferential reacquire and a queued `wait` first wait no longer than the rows' term, never wait zero, and get 200 with `lease_end_unconfirmed`; when the deadline passes after the preferential loop's fence and before its body expires the rows, with the loop's own limit left (that limit reads the coordinator's `time.monotonic`, which these two tests replace with a clock apart from the rows' that only the test moves), the body expires them and the loop decides again, getting 200 with `lease_end_unconfirmed` with no wait after the crossing; and when that limit runs out at the crossing, the loop leaves before its body and the admission's fence expires the rows, so the grant is 200 with `lease_end_unconfirmed` too, with no wait after the crossing and no expiry by the body | wake term removed (mutant) → its first recorded wait is its own limit, so the POS assertion fails; the preferential loop without its expiry, or without both its expiry and the term, and `_expire_due` without its expiry (mutants) fail the long-cleanup cases; a preferential loop that expires the rows and waits on (`b95b158`, and a mutant), or that expires them and then waits out the bound (mutant), fails the crossing with the limit left, its body waiting after the crossing; so does a limit check that breaks before the body once the rows are all the loop waits for (mutant), alone or with either of those bodies, since no body then runs; a loop with no limit check (mutant) fails the crossing that runs the limit out, its body expiring the rows; status that reads the clock again for `lease_end` (mutant) fails the photo, and a `claimable` that reads the clock again (two mutants) fails the photo with the server row's debt |
| K12 | A → B → C (H3) | A leaves residue → B degraded; B releases with no commands → a debt round → closes clean → C clean | B's round leaves residue again → C degraded too |
| K13 | Restart (H4) | seeded run → first grant degraded (`daemon_restart`); `note_processes_gone(run, None)` → next grant clean; recovered fault: seed after manifest restore and before lifting, including `clear_lifecycle_recovery_fault` (6461-6463) and complete (6554-6556), covering kept live runs (process_lifecycle.py:7625-7631, 7677-7692) | a lease end while the run is unbound → rows OWED (`not_bound`), debt stays; construction order of setter vs `_lock`/`_bindings` (2020, 2039-2045) both attach |
| K14 | Retirement and late results (H13) | queued close at `retire_role` → `binding_retired`, its late result → 200 `discarded`; delivered close → late result 409 `binding_retired`; the retired instance's own hold result → 200 and reconciles | the lifecycle write fails, `note_processes_gone` is not called → row stays OWED |
| K15 | Quarantine | quarantine at lease end → no close, rows OWED (`retail_quarantine`), grant degraded at once; hold path as today | quarantine after the close was queued → delivery discards it (4672-4673) → OWED |
| K16 | Holds (H2, H5, H9, r4 H1, r7 H2) | (a) delivered hold, both sides clean, hold result lost → next `action_hold` on that instance admitted; (b) both-sides-clean close before the hold result → result is a no-op; (c) at lease end no `action_hold_cancel` is queued and A's `pending_commands` is 0; (f) instance replaced: old exact holds become `hold_server_unverified`, new instance holds admitted; (j) START with vanilla ack whose recorded id matches `SJ_ACTION_ACK_*` → **that input row** may clear, never the hold fence; **(k)** active hold with no terminal: close → `Shutdown` ends it, result `cancel`/`shutdown`; **(l)** hold with `finished`/`natural` (or `timeout`/`timeout`, `rejected`/`server_rejected`) published and not yet taken when close enters: result stays that observation | (d) residue client or server action → next hold on that instance 409 `hold_unreconciled`, grant degraded; (e) a clean close from another instance → fence stays; (g) interrupt is not sent from the **server** close; a late interrupt can still arrive under a degraded grant; a hold player missing from floors → `hold_server_unchecked`; (h) own result `cleanup_complete=true` first while server helper still busy → fence stays; (i) START without ack, interrupt, or END in flight, both helpers idle → `action_input_in_flight`, debt until client process exit; no "unacked send" shortcut; **(m)** a mutant in which `Shutdown` rewrites an existing terminal must fail |
| K17 | Move and weapon floors (H1, H2, Q15, r3 H11) | client F=7 W=4 matching named `conn_gen` (equals body's `m_MCPConnGen`, even if lastId is 0) → server close carries both, reports ≥ → both rows clear; late clean while still CLOSING → clear; late clean after OWED whose identity (close id, instance, `conn_gen`, F/W) still matches, atomic → **clears**; with no carried floor, a client floor with ids 0 and the server's `conn_gen` → both rows clear | server first reports F=5 then client F=7 → OWED `player_move_floor_unapplied`; same for W → `weapon_floor_unapplied`; missing or other `conn_gen` on the close → OWED `player_move_conn_gen_unapplied` / `weapon_conn_gen_unapplied`; client floor of another `conn_gen` while the server is CLOSING, in either order → those two conn_gen reasons; with no carried floor for that player, in either order, a client floor with ids 0 of another `conn_gen` → those two conn_gen reasons, and one for a player the server census lacks → `player_move_floor_unapplied` and `weapon_floor_unapplied`; start/raise/fire/release with no generation or another generation → refused; other-gen release does not free B's copy or raise lastId; superseded or identity-mismatch late result stays OWED; unresolved player → OWED; issued grant is never rewritten |
| K18 | Order across peers | the server close is published only after the client close settles | client silent → server close published at + 10 s, not earlier |
| K19 | Interleavings and locks | (i) no poll between publish and report sees the close; (ii) expiry from `record_poll` → that same poll answer carries the client close; (iii) status and acquire during a blocked hook return; (iv) an outcome of round 1 flushed after round 2 opened → ignored | a mutant hook that waits for the game → the poll answer in (ii) comes only after the hook's timeout; start/release with no `conn_gen` or another generation → refused (same identity negatives as K8/K17) |
| K21 | Payloads (H6) | grant `lease_end.settled` names the close ids; during a round status shows `lease_end` and `cleanup_degraded == []`; with the server row owed since 0, the client row CLOSING, and a clock that moves 0.25 s on every read, status's `since_s` for the owed row plus its `deadline_in_s` is the bound | with an owed row the grant has `lease_end_unconfirmed` merged with another warning (on main `75b5d16`, `wait` assigns the list itself at 1082, 1134, 1152, 1183, 1205 and 1238, and `_payload_with_degradation` at 4158); an `operation_id` retry of `acquire` and of `enqueue` merges the same way; status `cleanup_degraded` stays `[]`; `unconfirmed` aged from another clock reading than the photo's (mutant) → the sum is not the bound |

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
| K25 | Admission (H6, Q7, r4 H3) | `MAX_PENDING - 1` ordinary commands pending → an ordinary command gets `client_bridge_queue_full` on the client (75b5d16 MCPClientBridge.c:1043-1046, POST :7428-7435) or `bridge_queue_full` on the server, a close is accepted; `MAX_QUEUE` plus one close still publishes; **both peers: 64 ordinary (accepted or refused) + close = 65**; server 63 live callbacks + a 64-command poll + close = 128 (`StartPoll` 75b5d16 MCPBridge.c:303-305, 6-8, 7451-7458; r6 H3); outstanding 63 admits, 64 does not; **client** at that same 65 reserve, not 20 | pending full → a second close gets the same full error; `MAX_POLL_RESULTS` does not clip the batch; server 64 callbacks + 65 owed POSTs refused; client reserve of 20+1 that admits a 64+close batch and overruns 128 is refused |
| K26 | Client census per controller (H1, r5 H2) | for each client row of §3 whose stop leaves the controller clean: controller active → close → entry stopped, census clean (after the observer when async; the camera only after 10 ticks) | that row's stop step stubbed out → residue naming exactly that entry; **deliberate debt is not this POS:** C11 with interrupt, END, or START without a named ack in flight (`action_input_in_flight`) stays residue until the type rule of §2.7; a stop that sent interrupt (4_World/MCP_ActionHold.c:538-546) is that debt, not a failed stop |
| K27 | Server census per controller (H1, H2, H3, H7, r4 H2) | for each server row: bots (empty registry is not enough; rejected stop **and** not idle → residue; rejected stop **and** idle → may clear), infected (`unverified` until process exit, never clean next round from empty registry), move copy (F applied only with matching `conn_gen`), weapon copy, jobs, RPC register including `notify_players` and refuse with job gone, deletion register filled at dispatch, server helper idle and in-flight input per §2.7 | stop stubbed out → residue naming it; `rpc_in_flight:<kind>` with job gone; `rpc_in_flight:notify_players`; `deletion_pending` after normal timeout; G5-style empty infected registry still OWED; **combine X→T client + Y→T server:** `MCPInventoryMoveReady` of X true and the register of X still owed; job timeout then RPC applies with no refuse → debt until **server process** exit; client-only replace leaves S6; another player's client exit does not; a quantity/location readback never clears a row |
| K28 | Observer | camera handoff → the close posts in the tick the handoff ends; later commands dispatch meanwhile | a stub that never clears → residue `observer_timeout` at 25 s of runner time, naming the entry |
| K29 | Client not in game | not in game → `client_not_in_game` from the gate (1098-1104), no stop step called | in game → the stop steps run |
| K30 | Jobs of the merged PRs (H10, r3 H4, r4 H7) | `loadout_clear` and `scene_clear` refs enter the bridge register **at dispatch**; a **normal** job timeout before the close still leaves them there (75b5d16 MCPBridge.c:6936-6941, 7308-7312); observer waits ≤ 25 s; `player_view_set` aborted through timeout; `server_perf` survives; inventory job timeout before close does not liquidate the RPC row | a mutant that drops the refs on timeout and calls the census clean; a mutant that aborts `server_perf` fails the keep assertion; inventory job gone + later RPC with no refuse still owed until **server process** exit |

**Python contract**

| K | Criterion | POS → expected | NEG → expected |
|---|---|---|---|
| K31 | Dialog state (Q11) | a `ui_dialog` result with state `lease_closed` validates (ui_dialog.py:17-19, 296); product-spec.md:426-429 migrates from five states to six; `TryFinish` at the deadline still yields `timed_out` (MCPDialogController.c:428-432) | an unknown state is still rejected; a partial `values` payload is still rejected |
| K32 | Version and names (H8, H11, H15) | version "13" on both sides; the capability census lists the close names, mapped to None; they are absent from the public sets and `/enqueue` refuses them; arg-contract hash unchanged; `peer_for_command` table routes `lease_close` to server and `lease_close_client` to client, including offline (K6) | a v12 bridge is refused at ingress (3461-3469) and gets no delivery (4546-4547); names added to `CLIENT_COMMANDS` (mutant) |

### 5.2.1 Offline vs in-game (r2 §5, adopted)

| Criteria | Offline | In-game |
|---|---|---|
| K1–K4; G1 | Keep the in-memory broker/coordinator repros; check the accumulated delivery mark after result and abandon. | Prove X was **delivered**, not only queued before timeout. If not, G1 is inconclusive. Name the object and the grant's close ids. |
| K5–K7, K13–K14 | Both construction/attach orders; offline → two peers; role replace; persist fail; recovered fault that keeps other live runs. | Confirm surviving process identities and seed/clear of their debt. A binding change does not prove exit. |
| K8, K17–K19 | Interleavings with a close **delivered** before timeout and dispatch after the grant; results after 40 s; supersession; atomic floor check; reconnect `conn_gen` match (old F after B's first accepted start; F=1 in flight on a fresh connection); missing `conn_gen` is OWED; start/release/raise/fire with no generation or another generation refused; other-gen release does not free B's copy. | Delay **only** that delivered close's DrainPending (G7 hook); B's input still ticks. Inject a previous-generation start **and** release at the server reader; they are refused. A Python queue test does not measure this. `/set_poll_delay` is 5 s (loopback.py:5412-5419) and is not this instrument. |
| K9–K12, K15, K21 | Combine each failure with other warnings and keep `lease_end_unconfirmed`. Cover late callback, hook that publishes, hook that never publishes. Full queue plus close. | Separate POST attempt, received result and settlement. Measure both clocks; a Python deadline does not prove 25 s of simulation. |
| K16, K26–K29; G2–G3 | Independent doubles of local and server state; START/interrupt/END in flight; own result before server helper; replaced-instance hold; close on an active hold vs a published-and-untaken observation (K16(k)(l)(m)); RPC with job gone; notify register; late weapon start; rejected bot stop with idle vs not-idle. | Observe availability and rest on **both** sides. Camera and controls need readback; native channels need rejection of old activity. |
| K27, K30; G5 | Drop positives that treat the next round as clean because the registry/job was emptied. Keep refs and obligations until resolved. Combine X/Y and job-timeout-then-RPC stay owed. | Infected probe (follow-up) isolates override effect. Deletions are watched until gone; reading the native declaration is not enough. |
| K20 | Public consumers with a round during releasing — expected conflict — and after release — recovery allowed; abandon releases still attributed to the owner. | Distinguish session-transition end from pending physical cleanup. |
| K22–K25; G4 | Both bridges, exact order, old pending, spawn at head, new batch, queue pressure; server 64 callbacks + close in the 128 budget; **client** admission edge with refusals; update the `test_task9` literal and keep a FIFO-independent negative. | Keep the single-batch proof and filter expected ids. For the chosen one-shot commands, `result posted` order proves FIFO inside the tick. |
| K31–K32 | New enum, timeout exactly on the edge, unknown states refused, internal names in both routers, public whitelist closed, versions and integrated census. | Compile and deploy PBO v13, with identity/hash and capabilities of the run's peers. |
| G6 | Mechanical bounds; separate definitive residue from observed proof; keep grace, releasing and audit. | Measure idle, handoff, pending proof and degradation separately. 0.5/2 s targets depend on load and do not replace a close guarantee. |

### 5.3 Findings to criteria
r7 H1: §2.5, §2.17, §5.4. r7 H2: §2.6 step 8, K16(k)(l)(m), G2(d). r7 H3: §2.6 step 4. r7 H4: §2.6 step 4, §0.
r5 H1: §5.4. r5 H2: G2, K26, §2.9. r5 H3: S6, K27, K30, §2.7. r5 H4: §2.5, K8, K17–K19, G7. r5 H5: §2.5, C3, S3, G7. r5 H6: cabecera, §2.7, §2.10, K16, K25, §5.4.
r4 H1: K16, K27. r4 H2: K27. r4 H3: K25. r4 H4: §5.4. r4 H5: K8, K17, G7, §2.5. r4 H6: cabecera, §2.4, S9.
r4 H7: K27, K30. r4 H8: G7. r4 H9: §2.17. r4 H10: §2.3, §2.6, §2.14, G6.
r3 H1: K16, K27. r3 H2: K16. r3 H3: K27. r3 H4: K30. r3 H5: K8, K17, G7. r3 H6: K27.
r3 H7: K25. r3 H8: K27, S1. r3 H9: G7. r3 H10: §2.1. r3 H11: K17. r3 H12: §2.14.
r2 H1: K17, K27. r2 H2: K16, K27. r2 H3: K27. r2 H4: K13. r2 H5: K8, K17, G3. r2 H6: K21.
r2 H7: K27, G5. r2 H8: K32, K6. r2 H9: K16. r2 H10: K30. r2 H11: K26, C13. r2 H12: K24, §5.4.
r2 H13: K20. r1 H7: none here (§7). r1 H8: K24. r1 H9: K11. r1 H11: K32. r1 H13: K14.
r1 H14: K20, K16. r1 H15: K32 for the gates; K26 checks state, not jobs.

### 5.4 Existing tests
**Changed on purpose:**
- Version pins to "13" (each read; Python constant, MCPMessages.c text, or both):
  test_action_hold.py:106 (`EXPECTED_BRIDGE_VERSION` "12") and :108 (`MCP_BRIDGE_VERSION = "12"`);
  test_batch6.py:35 (`core.EXPECTED_BRIDGE_VERSION`) and :41 (`MCP_BRIDGE_VERSION`);
  test_fb_e1ae_input_trigger.py:407 (`MCP_BRIDGE_VERSION`);
  test_instance_fence.py:942 (`EXPECTED_BRIDGE_VERSION`) and :944 (`MCP_BRIDGE_VERSION`);
  75b5d16 tools/tests/test_loadout.py:1702 (`MCP_BRIDGE_VERSION`) and :1703 (`EXPECTED_BRIDGE_VERSION`);
  test_player_heal_godmode.py:680 (`MCP_BRIDGE_VERSION`);
  test_player_kill.py:313 (`MCP_BRIDGE_VERSION`);
  test_player_move.py:651 (`MCP_BRIDGE_VERSION`);
  test_player_trace.py:587 (`MCP_BRIDGE_VERSION`);
  75b5d16 tools/tests/test_player_view_set.py:865 (`MCP_BRIDGE_VERSION`);
  75b5d16 tools/tests/test_scene_verbs.py:757 (`MCP_BRIDGE_VERSION`);
  75b5d16 tools/tests/test_server_perf_nav_visibility.py:423 (`MCP_BRIDGE_VERSION`);
  test_ui_click_complete.py:724 (`MCP_BRIDGE_VERSION`);
  test_vehicle_trace_contract.py:137 (`server.EXPECTED_BRIDGE_VERSION`) and :139 (`MCP_BRIDGE_VERSION`);
  test_world_time_get.py:265 (`MCP_BRIDGE_VERSION`).
- test_messages_contract.py (H11): `EXPECTED_VERSION` (19), the known-good fixture (138) and the
  assertion (160) move to "13"; the `version_bumped` mutant (194) becomes 13 → 14, so the negative
  stays independent of the number. A v12 peer is still refused (K32).
- test_task9_spawn_phase_markers.py (H12): the pinned loop text (158-184) changes with the defer
  flag. The literal at :220 (`bool deferFromWorldSpawn = false;`) changes with it. Keep a
  negative independent of FIFO and deferral (r2 D:754-755).
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
- 75b5d16 tools/tests/test_player_move.py:1158-1165: pins every `m_MCPMoveLastId`
  write (`["moveId", "request.move_id", "0"]`) and the whole `OnReconnect` body
  (`super.OnReconnect(); MCPMoveServerRelease("reconnect"); m_MCPMoveLastId = 0;`).
  Conn-gen assignment lives on MissionServer and does **not** by itself
  require changing the PlayerBase.OnReconnect body this pin names.
  :2213-2218 pins the **vanilla** `OnClientReconnectEvent` flat body
  (`if (player) { player.OnReconnect(); }`); an addon override does not
  change that vanilla pin. :1377-1378 (model `OnReconnect` order).
  Adding `conn_gen` to start/release also changes the write/read order
  pins in this file: SendStart writes at :1009-1011
  (`MCP_INPUT_UDT_PLAYER_MOVE`, `true`, `request.move_id`);
  FlushServerRelease at :1071-1073 and :928 (`s_ServerReleaseId`);
  `MCPMoveReadRequest` at :1038-1042 (`isStart`, `moveId`). `_in_order` (:248-253) checks presence and relative order only, so a field slipped in between would pass: the new pins compare the exact field sequence. :1391-1395 and :1408-1416 expect the release `[[udt, False, 5]]`, and the body double at :506-515 has no generation: the double gains a generation G and those expectations include it (r6 H2). With no generation known the release path sends nothing, keeps `s_ServerReleasePending`, turns the overrides off, and the retry sends the release once G is known: a positive with a valid G, and negatives for an unknown and a mismatched G and for the field order.
- 75b5d16 tools/tests/test_guards_bridge.py:85-91 (server `StartPoll` guard
  expression and `MAX_CALLBACK_REFS`/`MAX_POLL_RESULTS` 128/64 at :75-77) and
  :187-192 (client `OutstandingWork() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS`,
  constants 128/20 at :176-178). H3 changes the reservation; these assertions
  change. :80-83 pins daemon `MAX_QUEUE` 64.
- 75b5d16 tools/tests/test_inventory_move_contract.py:147-149 (`MCPInventoryMoveReady`
  body conditions) and :157-164 (ready before client refusal in dispatch /
  `ProcessJobs`, order of those two checks). That is **not** a causality test
  and does not pin the whole ready body. The register no longer clears on that
  ready path.
- 75b5d16 tools/tests/test_fb_0063_weapon_actions.py:398-413 pins
  `MCPReadFireRequest` / `MCPReadRaiseRequest` and the write order of
  `SendRaiseRequest` (`raised` then `ttlS`). Adding `conn_gen` (and the
  weapon request id) changes those read/write literals; they check presence and relative order only, and the new pins compare the exact sequence.
- 75b5d16 tools/tests/test_resultleak_pool.py:360 (server `StartPoll` expression).
- 75b5d16 tools/tests/test_leak_callback_drain.py:26-29, :170-172 (admission
  model from `MAX_CALLBACK_REFS` / `MAX_POLL_RESULTS`).
- 75b5d16 tools/tests/test_player_heal_godmode.py:1321-1326 (InvokeOnConnect
  order: super then `MCPGodmode.OnBodyReady`) and :1865-1872 (vanilla connect
  events). MissionServer conn-gen send after vanilla must keep that order or
  update the pin.
- 75b5d16 tools/tests/test_player_move.py:54-60 `BRIDGE_CODES` gains
  `conn_gen_unknown`. The extractor `_bridge_codes` (:308-319) gains `SEND_START`
  (:136) in the return sweep, discarding exactly `sent` and `unavailable`, the
  two statuses that are not errors (75b5d16 4_World/MCP_PlayerMove.c:730-762), so
  the oracle sees the code even when it travels by variable. The `player_move`
  (75b5d16 tools/dayz_mcp/server.py:8864-8869), `weapon_raise` (:7478) and
  `weapon_fire` (:7546) descriptions name `conn_gen_unknown`; `server.py` leaves
  Unchanged for that bounded change. The description test
  (75b5d16 tools/tests/test_player_move.py:1982-1987) covers it with no extra
  assertion. 75b5d16 tools/tests/test_fb_0063_weapon_actions.py:363 and :520-532,
  which pin `input_busy`, gain the new literal.
- Offline positive and independent source pin (r7 H1): a no-game test with the
  body double lacking generation walks `DispatchPlayerMove` and gets
  `conn_gen_unknown` with no `Begin`, no written message and no overrides; a
  source pin, apart from the extractor, that in `DispatchPlayerMove` the
  literal branch sits before `Begin`. A mutant without that branch, or that
  leaves it after `Begin`, fails both. For raise and fire, the same positive
  (no message, no override) and the pin of the literal.
- Independent negatives (not the pins above): conn_gen missing/mismatch (K8/K17);
  start/release/raise/fire of another generation refused; other-gen release
  does not free B's copy; floors applied with lastId still 0; client admission
  overruns when refusals are counted; combine X/Y keeps the X row (K27); a v12
  peer is still refused (K32); `version_bumped` stays independent of the number.

**Stay green, by construction:**
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
- (d) a 10 s `action_hold`, released in the middle → Shutdown sends interrupt
  (4_World/MCP_ActionHold.c:538-546); that interrupt is debt while the sending
  client process lives. This is K16(k): the hold was active with no terminal,
  so the posted result is `cancel`/`shutdown`. B's `action_hold` on **that** instance gets 409
  `hold_unreconciled`, B's grant is degraded, and B's client close is **not**
  settled. After replace-role, B's hold on the **new** instance is admitted.
- Pass, (a)–(c): B's grant lists the client close as settled. Release → grant
  takes at most 2 s, plus the observer's wait in (b) (the 10-tick handoff).
  Pass, (d): the run shows that Shutdown sent the interrupt (4_World/MCP_ActionHold.c:539-546: it needs the current hold and a free channel), else the row is INCONCLUSO; 409 and degraded grant while the old client lives; hold admitted
  on the new instance after replace-role. The Pass line does **not** require
  B's client close settled in (d).

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

**G7. Delivered close vs B's later move (r2 H5, r3 H9, r4 H8).**
- Instrument [DESIGN]: force or intercept the already delivered server close into
  `m_Pending` so `DrainPending` (MCPBridge.c:352-365) holds it. A lone close can
  dispatch **inline** from the poll callback (MCPBridge.c:325-347) and never hit
  that hold; the recipe must not allow that path. Jobs, bots, input and other
  pending commands still tick; only that id is skipped until the test releases it.
  `/set_poll_delay` (loopback.py:5412-5419) caps at 5 s and delays a poll, not this
  dispatch; it is not the instrument.
- Send: deliver the server close, hold its DrainPending > 40 s; A's round times out; B gets a
  degraded grant and starts a `player_move` by input with id > F (same `conn_gen`); then
  release the hold so the old close runs. Second run: reconnect, new `conn_gen`, B's first
  start accepted, **then** the old close F=100 of the previous gen runs. Third run:
  reconnect, nothing accepted yet (`m_MCPMoveLastId` credited 0 **before** the close,
  4_World/MCP_PlayerMove.c:1143-1148), B's close with F=1 of the new `conn_gen` while
  start id=1 is in flight. The third run **controls start and release**: a normal
  client release also raises the floor (4_World/MCP_PlayerMove.c:605-609, :935-952)
  and must not be the cause of lastId rising. Mutant: the close omits F; that run
  **must fail**.
- Pass: first two runs, B's move continues and the new floor is unchanged by the old
  close. Third run, start id=1 is refused after the close, lastId was 0 before it.
  The close log does not release B's id in the first two. Without the pending hold
  the row is INCONCLUSO. A Python queue test is inconclusive.
- **Previous-generation identity (r5 H4, H5; r6 H4), not a close-by-absence:** inject
  at the **server reader** messages valid in every field but `conn_gen`, which is the previous one:
  a start with an id above lastId, a release with B's move id and one with a higher id, and raise/fire with ids above W. Pass: all refused with `conn_gen_mismatch`; the releases
  do not free B's copy and do not raise lastId; a mutant without the generation compare **must fail**. Absence of traffic is
  INCONCLUSO, never a closed hypothesis. In-game positive after reconnect:
  the client learns the new generation (RPC on the body) and then a start
  with that `conn_gen` is accepted and the player moves.
- **Client-body probe (r7 §6), observation only.** The client logs the `PlayerBase`
  constructor, destructor and selection (the surfaces r7 §6 lists) and the reception of
  `MCP_RPC_CONN_GEN`, with object identity, process, and the generation before and after
  the RPC, in a same-process reconnect and in a client replacement. It observes the
  lifetime in those runs; it does not accredit G7 by itself, nor any universal guarantee.
  G7 passes only when the run also correlates the server body's current generation, the
  RPC's target body with the locally selected one, the generation that start, release,
  raise, fire and the census actually write, and the exact refusal of the previous value
  with the current one accepted (the injection and the in-game positive above).

**G6. Latency budgets (H12),** from A's release reply to B's grant reply:
- no work (read-only commands only): no close in either log, at most 0.5 s;
- work with nothing left over (one `object_delete`): at most 2 s;
- work left over (G2 b): at most 2 s plus the handoff;
- residue (G5): a degraded grant within 2 s.
- The 40 s bound is measured offline in K11 **and** in G7 (delivered close held in
  DrainPending past 40 s, then B's degraded grant). `/set_poll_delay` stops at 5 s, and making a peer
  stop polling would need a kill outside the lifecycle guard. Restart (K13) and retirement (K14)
  stay offline for the same reason.

## 7. What this does not cover
- `exec_enforce`: arbitrary code, whose effects the census cannot know.
- World state: spawns, deletes, teleports, godmode (on by default, 4_World/MCP_PlayerCare.c:1-21),
  time and weather, inventory and loadouts (#261), scenes (#262), the person-view flag (#264), HUD
  notifications **already applied**, and seating in a vehicle. A request that can still apply
  after the grant is in the close (weapons, RPC including unacked `notify_players`, deletion
  refs), not in this exclusion.
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
