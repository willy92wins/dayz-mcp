# Plan R26 — `0ab2` gracia de lease + no segar run

Ficha `fb-20260909-213002-0ab2`. Spec: **H4** (FIFO + TTL 120 s; esta ficha lo enmienda), **H5** (expiry no mata DayZ), **H6** (lifecycle solo sobre runs registrados), **H11**/**H13** (ocupación nombrada).

Lane **única**: mecanismo citado a `path:line`. Grill del dueño **aceptado 2026-09-12**. Contrato **[EXACT]** abajo.

**Estado:** implementado en código (`DayZ_MCP_dev` `main`). Unittest del plan exit 0. **W7a R9 offline** en `tests.test_0ab2_r9` (no finge H8). H8 in-game sigue leftover (W7b).

---

## Contrato sellado (grill)

| Param | [EXACT] |
|---|---|
| Mecanismo A | **S1** — token muere a 120 s; preferencia de **reacquire** tras `release_owner`; run pasa a `RUNNING_IDLE` (cleanup actual) |
| `grace_s` | **90** |
| `attached_run_required` | **true** — snapshot **antes** de `release_owner` |
| `max_pref_renewals` | **1** cuando la cola no está vacía (`M=1`); sin cola, sin tope |
| B | **sí**, código `takeover_required`, flag `takeover=false` por defecto |
| §3 `activity_extends_ttl` | **fuera** (R20) |

Constantes junto a `SESSION_TTL_S`:

```
LEASE_GRACE_S = 90.0
MAX_PREF_RENEWALS = 1
```

`TTL = 120`, `G = 90`, `M = 1`. Reloj del coordinador (`FakeClock` / `time_fn`). Identidades `A` (titular a `t=TTL`) y `B`/`C` (extraños). Igualdad de `ClientIdentity` = la de acquire idempotente (`client ==`).

---

## Qué arregla

Incidente 2026-09-09 (sesión `835e80d8`, Arma2Quad): TTL 120 s caducó en huecos humanos. Otra sesión adquirió el lease y lanzó su par; el run `636e7213` desapareció. H4 daba el hueco al primero que llegaba.

| | Pedido | Este cambio |
|---|---|---|
| **A** | Tras TTL, solo el titular anterior readquiere; el resto FIFO | S1, ventana 90 s |
| **B** | `dayz_test_run` no lanza sobre un run ajeno/`RUNNING_IDLE` ownerless sin `takeover=true` | `takeover_required` |
| **§3** | Alargar el mismo token con actividad | **No** |

---

## Hechos verificados (2026-09-12, `main` @ `80ed00b`)

- `SESSION_TTL_S = 120.0` — `tools/dayz_mcp/session_coordination.py:15`
- `_expire_due` libera a `now >= effective_expiry()` con `lease_expired` — `:2093-2101`
- `acquire` con `_active is None` y cola vacía concede al instante — `:406-420` (N1 hoy)
- Token a 120 s / 121 s → `lease_expired`; a 119 s válido — `test_session_coordination.py:416-425`
- Heartbeat renueva el **mismo** token — `:1120-1123`. Entre tools no hay heartbeat (`server.py:3697-3698`)
- `session_acquire_wait` usa `enqueue` (siempre ticket) + `wait`, no el 200 inmediato de `acquire` — `control_client.py:747-756`, `test_session_acquire_wait.py:31-40`
- Cleanup → `begin_release_owner` — `daemon.py:78-109`
- `release_owner`: `RUNNING` del dueño → `RUNNING_IDLE` ownerless — `process_lifecycle.py:1055-1078`
- `_start_run_reserved` rechaza cualquier `_ACTIVE_STATES` con `active_run_exists` — `:2288-2349`
- `_caller_owns_run`: `RUNNING_IDLE` → True para **cualquier** caller — `:550-552` (N5 hoy)
- `takeover` no existe (grep 0)

---

## Fuera de alcance (R25 / R20)

- `546d`, lote `2edd-1`/`dae1-1`, PARO `3fc1`/`1025`
- Enforce, PBO, `_APP_PACKAGED_MODULES`, resellado
- Matar PIDs foreign/gone (D-49 / reaper)
- Overlay `a429` / mapa HTTP `fence.mutation_rejects_by_code`
- `pipeline_resolve` de fichas
- In-game / R9 en el turno de code (leftover de la sesión de día)
- §3 auto-renew del mismo token
- Persistir gracia en WAL/snapshot (memoria del daemon; restart invalida leases por H5)

---

## Schema [EXACT]

### A — coordinación (S1)

Estado interno (no HTTP `/status` salvo que `coordinator.status` ya viaje por `/session/status`):

```
_expiry_grace: null | {
  former: ClientIdentity,          # identidad titular a t=TTL
  until: float,                   # time_fn == TTL_expiry + 90
  attached_at_expiry: bool       # snapshot RUNNING owned, antes de release_owner
}
_pref_used: map session_id -> int   # grants preferentes ya consumidos CON cola
```

Probe inyectable `attached_run_probe(session_id, lease_id) -> bool`. Default (None): **False** (sin gracia; tests H4 actuales intactos). El daemon la cablea a un `RUNNING` con `owner_session_id`+`owner_lease_id` coincidentes, **antes** de `begin_release_owner`.

`authorize` / `heartbeat` del token viejo: **sin cambio**. A `t>=120` → `lease_expired` / `lease_invalid`. La gracia **no** revalida el token.

`acquire` / `wait` (S1):

| Condición | Resultado |
|---|---|
| Token viejo, `authorize`/`heartbeat` | `lease_expired` / `lease_invalid` (H2/H4 120 s) |
| `now < until` y caller **es** former y `attached_at_expiry` | **200 active**, **nuevo** `lease_token`; se consume la gracia; si había otros en cola, `_pref_used[A] += 1` |
| `now < until` y caller **no** es former | `acquire` → **202 queued** (FIFO). `wait` **no** hace claim de cabeza. `session_acquire_wait` no devuelve `queued` al caller MCP (H9): espera o `session_wait_timeout` |
| Gracia vencida, o probe false a `t=TTL`, o cola no vacía y `_pref_used[A] >= 1` | H4 actual: primer `acquire` libre o cabeza FIFO con `session_wait` vivo |

Former vía `session_acquire_wait`: `enqueue` sigue devolviendo 202+ticket (contrato H9). `wait` puede **promover** el ticket de A a cabeza durante la gracia (aunque B esté delante) y entonces claim. Extraños no claim mientras `now < until`.

Enmienda H4 [EXACT], una frase en `product-spec.md`:

> Cola FIFO estricta salvo ventana de gracia post-TTL de 90 s en la que solo la identidad titular a t=TTL puede acquire/wait-claim si a t=TTL tenía un RUNNING propio (snapshot antes de release_owner); el token sigue inválido a 120 s; extraños solo promocionan con session_wait vivo después de la gracia; como máximo 1 reacquire preferente consecutivo cuando la cola no está vacía.

`coordinator.status` / MCP `session_status` (no se inventa en HTTP `/status` del loopback de juego):

```
grace: null | {
  remaining_s: float,        # until - now, >= 0
  pref_remaining: int,       # max(0, 1 - _pref_used[former])
  attached_required: true
}
```

`null` cuando no hay gracia viva.

### B — `dayz_test_run`

```
takeover: bool = false   # StrictBool; ausente = false
```

Lifecycle `_start_run_reserved` **sigue** devolviendo `active_run_exists`. El MCP **nombra** `takeover_required` cuando el box tiene un run `RUNNING` o `RUNNING_IDLE` que el caller **no** posee (`_caller_owns_run` sin el atajo idle).

- `takeover is false` + run ajeno o idle ownerless → **no lanza**; `error_code=takeover_required`; `occupied_by_run_id`, dueño redactado como hoy, `age_s`; hint de takeover (no `dayz_test_stop`)
- `takeover is true` + ese bloqueo → `dayz_test_stop` del `occupied_by_run_id` (lifecycle, H6) y luego el launch; éxito incluye `evicted_run_id`; no `Process.kill` de PIDs que no están en el manifiesto
- Run **propio** (`_caller_owns_run`) → sigue `active_run_exists` + hint de stop
- Puerto foreign / `box_claimed` / sin run gestionado → `active_run_exists` (sin reciclar el código)

`_caller_owns_run`: `RUNNING_IDLE` **ya no** es True para un extraño. Solo `owner_session` coincidente (session_id o prefijo de 12).

---

## Exit codes [EXACT]

| Código | HTTP / MCP | Cuándo |
|---|---|---|
| `lease_expired` | authorize 409 | token a `t>=120`; **igual** con gracia viva |
| `lease_invalid` | 403 | token/identidad; igual |
| `queued` | acquire/enqueue/wait 202 | extraño en gracia; o FIFO normal. **No** sale por `session_acquire_wait` (H9) |
| `active_run_exists` | dayz_test_run failed | run propio, puerto foreign, box claimed, ocupación sin run `RUNNING`/`RUNNING_IDLE` ajeno |
| `takeover_required` | dayz_test_run failed | `takeover is false` y hay `RUNNING`/`RUNNING_IDLE` no poseído |
| unittest | process exit **0** | comando abajo |

Unittest: `tools/.venv-mcp/Scripts/python.exe`, cwd `DayZ_MCP_dev/tools`. Reloj inyectable. Los tests `:416-435` (119/120/121) siguen verdes.

```
.\.venv-mcp\Scripts\python.exe -B -m unittest tests.test_session_coordination tests.test_0ab2_grace tests.test_session_status_blocked_on tests.test_box_occupancy.OccupancyErrorFieldsTest tests.test_0ab2_r9 -v
```

(más el módulo de takeover en el mismo `test_0ab2_grace` o tests de occupancy/dayz_test_run tocados). Exit **0**.

---

## Fixtures [EXACT]

Símbolos: `TTL=120`, `G=90`, `M=1`. `A` titular, `B` extraño, `C` tercera sesión. Probe `True` salvo P4. Un test = un veredicto. Sin DayZ.

### Positivos

| ID | Given | When | Then |
|---|---|---|---|
| P1 | A activo; probe True a `t=TTL`; S1 | `t=165` (`TTL+G/2`): A `acquire` | 200 `active`, **nuevo** token ≠ viejo; `grace` pasa a `null` |
| P1w | Igual; B puede estar o no en cola | `t=165`: A `enqueue`+`wait(0)` | 200 `active` (promoción si B era cabeza) |
| P2 | Igual | `t=165`: B `acquire` | 202 `queued`; no 200 |
| P3 | B en cola (`session_wait`/ticket vivo); `M=1` | A consume P1 a `t=165`; `t=285` (`+TTL`) A `acquire` otra vez | A **no** 200 preferente; B `wait(0)` → 200 |
| P4 | Probe **False** (A sin `RUNNING` a `t=TTL`) | `t=120.001`: B `acquire` (cola vacía) | 200 a B; A no privilegio; `grace is null` |
| P5 | Box con `RUNNING` de A (`owner_session` ≠ C); C llama `dayz_test_run`, `takeover=false` | tool | `status=failed`, `error_code=takeover_required`, `occupied_by_run_id` del run de A; execute **no** lanza |
| P6 | Igual, `takeover=true` | `dayz_test_run` | lanza (execute_run awaited); payload `evicted_run_id` del run de A; `Process.kill` **no** se llama (stop mockeado / no pids foreign) |

H9: P2 vía `session_acquire_wait` no puede devolver `status=queued` al MCP; el HTTP `wait(0)` sí 202. El test de coordinador cubre 202; el de MCP no se corre in-game aquí.

### Negativos

| ID | Given | When | Then |
|---|---|---|---|
| N1 | A expiró; gracia viva; probe True | B `acquire` a `t=165` | **No** 200 (hoy `:406-420` concedería) |
| N2 | Token de A a `t=120` | `authorize` / `heartbeat` | **No** 200; `lease_expired` (gracia ≠ alargar token) |
| N3 | `t=210.001` (`TTL+G+ε`), gracia muerta, cola vacía, probe True en el expiry | B `acquire` | 200 a B |
| N4 | Cola; A ya usó `M=1` (tras P3) | A `acquire` en la siguiente ventana | **No** 200 preferente |
| N5 | `RUNNING_IDLE` ownerless | `occupancy_error_fields(..., caller_session=extraño)` | `_caller_owns_run` no es True; hint **no** es `dayz_test_stop` |
| N6 | Idle ownerless, C, `takeover=false` | `dayz_test_run` | **No** lanza; `takeover_required` (no “para con stop”) |
| P1-neg | P1 | token viejo de A a `t=165` | `authorize` no es True (`lease_expired`) |

### INCONCLUSO (LL-017)

| ID | Precondición | Si falla |
|---|---|---|
| I1 | `.venv-mcp\Scripts\python.exe` | No correr; no PASS |
| I2 | Harness `FakeClock` de `test_session_coordination.py` | Skip; no PASS |
| I3 / I-game | Peers / juego / Steam | **No** en este turno. Sesión de día / H8 |

### No-regresión

`test_session_coordination.py:416-435` exit 0. Probe default False: expiry + B `acquire` sigue 200 (H4).

---

## Sesión de día + DZ-R9 (leftover; no este turno)

1. Unittest exit 0 (comando arriba) **antes** de R9. **W7a (offline):** `tests.test_0ab2_r9` cubre state-machine, race, identity y data-loss (probe antes de cleanup; gracia no en snapshot; restart invalida). No finge H8.
2. **DZ-R9** (`rigorous-data-audit`) **antes** de release-safe. Ángulos: **state-machine**, **race**, **admin/identity**; **data-loss** (B=sí). CRITICAL con repro ejecutable, tope 2 rondas.
3. In-game H8-shaped (**W7b**, no esta PR): dos clientes MCP; A deja tools >120 s con run vivo; B `session_acquire_wait`; A vuelve dentro de 90 s y readquiere; B sigue FIFO. C con lease no lanza encima sin `takeover=true`. H5: procesos de A vivos tras expiry. Reloj de pared, caja exclusiva, de día, no under the map.
4. Owner/integrador: HANDOFF LIVE-STATE y H4. Este worker no reescribe LIVE-STATE.

Invariante que R9 cita: “el token muere a 120 s; A puede acquire/wait-claim hasta `until=TTL+90` si había RUNNING propio; nadie más 200 en esa ventana; con cola, como máximo un reacquire preferente.”

---

## Call-sites (R7)

Invariant A (S1): token muere a 120 s; identidad titular puede acquire/wait-claim hasta `until`; nadie más 200.

Grep: `SESSION_TTL_S`, `_expire_due`, `_new_lease_locked`, `session_acquire_wait`, `enqueue`, `lease_expired`, `_caller_owns_run`, `active_run_exists`, `renewal is internal`, H4. Opuesto: `release` vs expiry, cola Insert/Remove, `adopt` (`loopback.py:3180`) vs `dayz_test_run`.

`_caller_owns_run` es el mismo predicado que el overlay adopt de `a429`. Idle ownerless: adopt (lease) ≠ stop (hint).

---

## Criterios verificables (R26.1)

1. P1 ejecutable: `G=90`, S1, probe True, `t=165`.
2. N1 Then estable: B no 200 a `t=165`.
3. N2 estable: token no revalidado.
4. I3 no se corre en el turno de code.
5. R9 ángulos nombrados; invariante numérica = fila grill.

---

## Implementado en código

Grill cerrado. `session_coordination.py` + probe en `daemon.py` + occupancy / `dayz_test_run` + una frase H4 + tests. Unittest del plan exit 0. R9/H8 leftover. R20: nada más. No `546d`. No lote. No reabrir PARO.
