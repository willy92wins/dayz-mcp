# Prompt — Step 1: POC real, A1+A2+A3 · Codex sesión 5

```
===== PROMPT INICIO =====

Tarea: construir el POC real de DayZ-MCP (fase 0 completa) y probar **A1, A2 y A3**. Step 0 ya validó
TODO el plumbing del transporte (no lo rehagas): deploy PBO `-packonly`, compilación, RestApi
get-or-create server-side, config vía `$mission:`, GET/POST async, JsonSerializer `array<ref>`, lifetime
de RestCallback (3/3 callbacks), round-trip completo. Esta sesión cubre **únicamente A1+A2+A3** con la
seguridad fail-closed ya incorporada. **NO** hagas A4 (tests de seguridad), A5 (resiliencia), la capa MCP
stdio, ni ninguna otra tool (spawn/vehicle/scene/camera): solo `query_player_state`.

## Carga inicial obligatoria

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-06-poc-fase-0-roundtrip.md
   (spec vinculante: §3a bridge real, §3b server, §3c cliente A1-A3, §3d orchestrator, §4 schemas,
   §5 readiness, §7 mecánica A2, §11 APIs. El config ya está fijado a `$mission:` en §3a/§3d/§8.)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md (criterios A1, A2, A3).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (base PROBADA a EVOLUCIONAR — conserva get-or-create, dual-path `$mission:` config, lifetime de
   callbacks (ref list), clases JSON; reemplaza el gate one-shot de Step 0 por el lazo continuo de §3a).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server_step0.py
   (base del server a completar con los 5 endpoints + seguridad).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-step0.ps1
   (deploy/launch PROBADO a reusar; OJO su collector solo lee `.RPT` → el de run-poc.ps1 debe leer
   también `script_*.log`, que es donde DayZ escribió los marcadores).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DAYZ_INFRA.md (lanzar CLIENTE diag `-connect`, mission/spawn).
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md
   (GetPlayers/GetPosition/etc.; recuerda §9 item 10: un proto en el dump ≠ funciona en runtime — grepea el USO).

## Alcance acotado — 4 entregables (A1+A2+A3)

### 1. Server real `tools\mcp_server.py` (evoluciona mcp_server_step0.py) — §3b
Endpoints (stdlib only, ThreadingHTTPServer, cola+results+poll_delay one-shot+lock):
- `POST /enqueue {cmd,args?}` → whitelist `{query_player_state}` (else 400), asigna id, encola, t_enqueue → `{id}`.
- `GET /await?id=N` → `{status:done,result}` | `{status:pending}`.
- `GET /poll` → saca y vacía la cola; si hay comandos Y hay delay pendiente: sleep(delay) one-shot (fuera del lock) → `{commands}`.
- `POST /result {id,ok,state,tick_poll_sent,tick_poll_callback,tick_dispatch}` → guarda results[id]+t_result → `{ok}`.
- `POST /set_poll_delay {ms}` → fija delay one-shot para el siguiente /poll no-vacío → `{ok}`.
Seguridad (R6, fail-closed, INCLUIDA aunque A4 se difiera): bind **127.0.0.1** + assert server_address[0]=="127.0.0.1" o sys.exit(1); **key en query string** obligatoria en TODAS las rutas → 401 si falta/!=; `log_message` no-op o sin la URL (no volcar la key).

### 2. Bridge real `DayZ_MCP\scripts\5_Mission\MCPBridge.c` (evoluciona el de Step 0) — §3a
- Lazo continuo en OnTick: `m_Tick++`; si !configurado → TryInit (CONSERVA get-or-create + dual-path
  `$mission:` config + ref-list de callbacks); si configurado → cadencia (acumular dt, cada 1/pollHz=5Hz,
  **single-in-flight**) → marcar `m_TickPollSent=m_Tick` justo antes del `GET /poll` (async).
- OnPollSuccess: `m_TickPollCallback=m_Tick`; parsear batch; por comando → Dispatch.
- Dispatch `query_player_state`: BuildPlayerState = `GetGame().GetPlayers(players)` → `players[0].GetPosition()`
  → MCPResult{ id, ok, state:{name,pos:[x,y,z]}, tick_poll_sent, tick_poll_callback, tick_dispatch } → `POST /result`.
- Backoff expo 1→30s en OnError/OnTimeout. Quita el gate one-shot/GATE de Step 0 (era el smoke).
- **NO** `SetOption`/`ERESTOPTION` (no script-accesibles, settled). Solo async (`*_now` prohibido).

### 3. Cliente `tools\mcp_client.py` (stdlib) — §3c
- `enqueue(cmd)->id`; `await_result(id,timeout)->(result,rtt)` (poll /await ~50ms).
- **A1**: enqueue query_player_state → compara `state.pos` con `--spawn` (POC_SPAWN), error **< 0.5 m**.
- **A2** (no-bloqueo, §7): `POST /set_poll_delay {ms:600}` → enqueue → del resultado
  `ticks_in_flight = tick_poll_callback - tick_poll_sent`; **PASS si ≥ 5** (a 600ms incluso a 8fps son ~5;
  si bloqueara sería ~0). Reporta ticks_in_flight + fps implícito. Sanity: baseline sin delay.
- **A3**: encolar 2 query_player_state casi a la vez → ambas resuelven con su propio id + state coherente.
- Salida: `tools\poc-verdict.json` + PASS/FAIL por A1/A2/A3 + números (pos, error_m, ticks_in_flight, RTT min/med/max).

### 4. Orchestrator `tools\run-poc.ps1` (reusa el deploy/launch de run-step0.ps1) — §3d
- Reusa: keygen, **config escrito al dir de la mission** (`$mission:`), PBO `-packonly`, deploy `@DayZ_MCP`, launch server.
- NUEVO: mission con **spawn determinista** en una coord fija conocida `POC_SPAWN` (documenta el mecanismo:
  cfgplayerspawnpoints / init.c); pasa POC_SPAWN a mcp_client.py. Si pinear el spawn resulta inviable,
  fallback documentado: A1 = 2 lecturas consistentes + pos plausible (anótalo, no lo des por equivalente).
- NUEVO: lanzar **cliente diag** (`-connect=127.0.0.1 -filePatching ...`), esperar in-game (~50s; sondear
  A1 hasta pos válida con timeout), correr mcp_client.py (A1/A2/A3), recoger verdict, teardown.
- **Fix del collector**: leer marcadores en `.RPT` **y** `script_*.log` (Step 0 falló-en-falso por esto).

## Restricciones (vinculantes)

1. **Solo A1+A2+A3.** NO A4 (tests seguridad), NO A5 (resiliencia), NO capa MCP/FastMCP stdio, NO otras
   tools (spawn/vehicle/scene/camera/exec/telemetry). La seguridad fail-closed SÍ va incluida en el server.
2. **Solo `query_player_state`** como comando. La whitelist rechaza el resto.
3. NO rehagas lo que Step 0 dejó settled: deploy, compile, RestApi get-or-create, `$mission:`, `ERESTOPTION`.
   No re-investigues ni "mejores" eso. Conserva los bits probados del MCPBridge.c.
4. Python stdlib only (http.server, json, threading, time, secrets, argparse, sys, os, urllib). Enforce: async only.
5. R2: cada API que toques (GetPlayers/GetPosition/etc.) verifícala en el source; si algo da "Can't find
   variable"/no compila, NO improvises — cítalo y para (como con ERESTOPTION).
6. NO te auto-revises (R21 es sesión aparte). Construye, corre A1/A2/A3, reporta, para.
7. OneDrive: .py grandes con escritura atómica + verificar; .c igual. Rutas del host (no sandbox).

## Output (A/B/C/D)

A — Archivos creados/modificados (mcp_server.py, MCPBridge.c, mcp_client.py, run-poc.ps1, mission spawn) + PBO.
B — LITERAL: `poc-verdict.json`; Python stdout (hits); RPT/script_*.log excerpt con las trazas del bridge;
    por criterio: A1 (pos devuelta vs POC_SPAWN, error_m), A2 (ticks_in_flight + fps), A3 (2 ids). PASS/FAIL c/u.
C — Hallazgos (mecanismo de spawn usado; cualquier API que hubo que ajustar con path:line; si nada, "Sin hallazgos").
D — Handoff: estado; si A1+A2+A3 PASS → queda el cierre A4 (tests seguridad) + A5 (resiliencia) para de-riscar
    el resto; si algo FAIL → causa acotada + hipótesis. Deuda dejada.

===== PROMPT FIN =====
```

## Notas para Claude (receptor)

- Verificar A1 de verdad: que `state.pos` == POC_SPAWN (<0.5m), no solo "vino una pos". Confirmar el
  mecanismo de spawn que usó (si cayó al fallback de consistencia, A1 es más débil — anotarlo).
- A2: confirmar ticks_in_flight ≥ 5 con delay 600ms (la prueba real del no-bloqueo, R22-001).
- Si PASS: siguiente = cierre A4+A5. Luego fase 1 de la arquitectura (control: world_spawn/vehicle).
- Verificar que NO se coló scope (otras tools, MCP stdio) ni se re-rompió lo settled (grep MCPBridge.c: sin SetOption).
