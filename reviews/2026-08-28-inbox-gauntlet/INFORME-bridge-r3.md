# INFORME — leak MCPClientPollCallback (fb-20260828-125854-964f) ronda 3

## CAUSA

Sin cambio. La causa más defendible **sin motor** es **(a) retención engine-side de cada `RestCallback` pasado a `RestContext.GET`**, combinada con **una instancia script nueva por poll**.

Rastro:

- Vanilla: `P:\scripts\3_game\http\restapi.c:103` `proto native int GET(RestCallback cb, string request)`. El ejemplo del proto (`:44-46`) construye un `RestCallback` por petición. No hay API script para soltar esa retención; `reset()` (`:133`) cancela *todas* las peticiones del contexto. Un segundo contexto de poll es inalcanzable (`PollContextUrl` devuelve la misma URL).
- Código medido (ficha, ~80 min): `new MCPClientPollCallback` por poll. ~2.5 GET/s × 4800 s ≈ 12000, contra **11798 leaked**.
- Polls verdes ⇒ `OnSuccess` corrió. Refuta **(b)** (Remove no ejecutándose).
- **(c)** (Abandon) es residual O(watchdog), no 2.5/s.

`MCPClientResultCallback` (17x) y `Print("[MCP-CLIENT] " + message)` (`:3865`) no se tocan.

## FIX

Ronda 2 retiraba a `-1` para siempre. Un GET que termina en error/timeout **sin** `OnSuccess` posterior nunca libera el slot. 8 fallos terminales (daemon caído) agotan el techo; al volver el daemon no hay idle ni `new`.

Cuarentena temporal (dirección cerrada; no hay cancelación por-request):

1. Reloj: `m_ElapsedS` se incrementa con el `timeslice` de `OnTick` (`:301`). Misma fuente que `m_PollInFlightS` / `m_Accum`. No se añade `GetTickTime` al retiro (esa API sigue solo en el log rate-limited del techo, preexistente ronda 2).
2. `Retire()` (`:38-46`) pone generación `-1` y estampa `m_RetiredAt = GetPollElapsedS()`. Sin bridge, `m_RetiredAt` queda `-1` (no madura: fail-closed).
3. `AcquireIdlePollCallback` (`:531-586`): primero un slot `generacion == 0`; si no hay, un retirado con `m_RetiredAt >= 0` y `now - m_RetiredAt >= POLL_CALLBACK_QUARANTINE_S` (60.0, `:213`), y lo resetea a idle (`SetGeneration(0)`, `:576`) **antes** de devolverlo.
4. Techo 8 intacto (`ReplenishPollCallback` `:502-505` en numeración previa; `Count() >= POLL_CALLBACK_POOL_MAX`). Techo + todos en cuarentena (`edad < 60`): `AcquireIdle` null, `Replenish` null, `StartPoll` → `OnPollFail("poll_callback_pool_exhausted")` (`backoff` + `m_Accum = 0`) y log rate-limited 5 s. Sin stall silencioso, sin log por tick.
5. A los 60 s el slot más viejo madura. El siguiente `StartPoll` (cadencia `interval + backoff`, backoff máximo 30 s) lo readquiere **sin** `new`. El poleo retoma. No hay cancelación por-request ni segundo `RestContext`.

Justificación de 60 s: `restapi.c:53` documenta retries de `OnError` como mecanismo **inmediato** del request en curso. No hay evidencia de eventos diferidos a decenas de segundos. La ventana F1 residual es “retry retenido > 60 s”, sin evidencia de que exista — mismo rango de residuo que success→error de ronda 2.

## LEAK RESULTANTE

| Escenario (80 min, poll_hz=5, ~2.5 GET/s) | Instancias `MCPClientPollCallback` retenidas |
|---|---|
| Antes (ficha, `new` por poll) | **11798** |
| Error ~0 (polls verdes) | **2** (pool inicial) |
| N fallos terminales sin `OnSuccess` | crece con replenish, techo **8**; tras 60 s los retirados vuelven a idle y el conteo de *vivos creados* sigue ≤ 8 |
| Peor caso (techo) | **8 vivos** (no crece con el tiempo de outage) |

O(polls) → O(errores+abandonos) acotado por **8 vivos**. ~0–decenas frente a 11798.

## PRUEBA DE NO-REGRESIÓN

### (1) Retry-error doble del mismo GET — con cuarentena

Estado: A bound `G`, GET outstanding.

1. Primer `OnError` (`:68-81`): `IsCurrentPollGeneration(G)` true → `Retire()` (`:77`): A queda `-1`, `m_RetiredAt = m_ElapsedS` (t0). `OnPollFail`.
2. `AcquireIdle`: A no es 0; `age = now - t0` es milisegundos ≪ 60 → **no** lo toma (`:574`).
3. Retry `OnError` (segundos, `restapi.c:53`): generación sigue `-1`. `IsCurrentPollGeneration(-1)` false (`:704-708`; rechaza `<= 0`). `Retire()` re-estampa. **No** despacha. **No** idlea.
4. A no se re-adquiere. Un poll nuevo usa otro slot. El retry no puede ver una generación nueva en A: `SetGeneration` solo corre tras adquirir un idle (`StartPoll`).

### (2) Error seguido de success final del mismo GET

Sin cambio de ronda 2: `OnError` retira A; `OnSuccess` de A ve `-1`, no despacha, idlea en `:65`. No pisa el poll vivo (otro slot).

### (3) Abandono por watchdog con evento tardío

`AbandonInFlightPoll` `Retire()` + bump. Evento tardío en `-1` no despacha. `OnSuccess` puede idlear antes de 60 s. `OnError`/`OnTimeout` mantienen cuarentena.

### (4) Pool agotado → reposición → techo → recuperación

Sin idle → `Replenish` (`new` si `Count() < 8`). Al techo: `OnPollFail` + backoff, log 5 s. Recuperación: `OnSuccess` (idle inmediato) **o** cuarentena madura (caso 5).

### (5) Daemon caído 10 min → techo → daemon vuelve → poleo retoma

1. Timeouts/errores terminales continuos: cada GET retira su slot (`OnTimeout`/`OnError` → `Retire()`). Sin idle, `StartPoll` repone hasta 8. Ninguno emite `OnSuccess`.
2. Al techo, los 8 están `-1` con `m_RetiredAt` reciente. `AcquireIdle` no encuentra 0 ni maduros. `Replenish` null. `OnPollFail("poll_callback_pool_exhausted")`: in-flight false, accum 0, backoff. Log extra como mucho cada 5 s. El tick sigue sumando `m_ElapsedS`.
3. Daemon vuelve (minuto 10). El slot más viejo tiene edad ≫ 60. Primer `StartPoll` tras madurar: segunda pasada de `AcquireIdle` (`:562-584`) ve `age >= 60`, `SetGeneration(0)`, lo devuelve. `StartPoll` hace `SetGeneration(m_PollGeneration+1)` y `GET`. **Sin** `new`. El poleo retoma.
4. Si el techo se alcanza con todos aún `< 60` s: se permanece en backoff hasta que el más viejo cumple 60 s; entonces el mismo camino de (3) aplica. No hay bloqueo permanente.

## GATES

Ronda 3, `MCPClientBridge.c`:

| Gate | Antes (ronda 2) | Después (ronda 3) |
|---|---|---|
| `{` | 571 | 579 |
| `}` | 571 | 579 |
| Balance | 571=571 | 579=579 |

Cómo: `python` `t.count('{')` / `t.count('}')`.

`new MCPClientPollCallback` — **2 hits**, sin cambios de sitio respecto a ronda 2:

| Línea | Función | Por qué no es per-poll caliente |
|---|---|---|
| `:504` | `EnsurePollCallbackPool` | Constructor y `TryInit`. Rellena hasta 2. |
| `:526` | `ReplenishPollCallback` | Solo si `AcquireIdle` null (0 idle y 0 cuarentenas maduras). Camino de error/timeout/abandon/techo, no el 5 Hz verde. |

Cuarentena (greps):

- `POLL_CALLBACK_QUARANTINE_S` `:213` (const 60.0) y uso `:574`
- `m_RetiredAt` / `GetRetiredAt` `:15`, `:48`, `:570`
- `Retire()` estampa `GetPollElapsedS()` `:44`
- `m_ElapsedS = m_ElapsedS + timeslice` en `OnTick` `:301`

`StartPoll` y el 5 Hz de `OnTick` no contienen `new MCPClientPollCallback`.

## LO QUE NO PUDE VERIFICAR

- Compilación Enforce / módulo Mission en DayZDiag.
- Leak re-medido in-game. Esperado con error~0: **2** instancias; con outage largo: **≤ 8**.
- Residuo F1: un `OnError` retry **más de 60 s** después del primer error, sobre un slot ya readquirido y con generación nueva, despacharía como poll vivo. El proto solo documenta retries inmediatos (`restapi.c:53`). Sin evidencia de eventos diferidos a decenas de segundos. Declarado, no cerrado.
- Residuo ronda 2: `OnError` después de `OnSuccess` del mismo GET.
- `MCPClientResultCallback` 17x: no implementado.
