# R21 estructural — CONSOLIDADO (Claude + Codex) · POC fase 0 DayZ-MCP

Doble revisión independiente del código del POC (Claude con skill + Codex a fondo), reconciliada por el
receptor (Claude) con verificación host-direct. Fuentes: `2026-06-07-r21-claude-poc-code.md` (mitad Claude) +
review de Codex sesión 8 (pegada por el usuario 2026-06-07). Severidad X.5.

**Veredicto: el transporte/bridge está genuinamente de-riscado (5 propiedades verificadas host-direct), pero
el harness de aceptación tiene 2 P1 de integridad — uno disparó en el run canónico. → X.5 correctivo (scope B)
antes de fase 1.**

## Verificación host-direct del receptor (confirmado, no parafraseado)
- **A1 disparó tautológico en `run_045213`**: marker presente (`script_…04-52-21.log:33: spawn_actual=6063.02
  7.99834 1931.91`) pero verdict `target==pos==6063.0185546875` exacto → el fixture vino de `playerReadyPos`
  (lectura del bridge), no del marker. Root cause: race de flush del log (`Get-SpawnActual` lee disco antes de
  que la línea se vuelque → null → fallback `run-poc.ps1:633-636`).
- **Propiedad real de A1 OK** (comparación que el harness omitió, hecha host-direct): bridge
  `6063.0185546875/7.99833869934/1931.9072265625` vs marker independiente `6063.02/7.99834/1931.91` =
  **0.0031 m ≪ 0.5 m**. El bridge lee la pos autoritativa bien; lo roto es el TEST.
- A2-A5 genuinamente PASS (verificados en run + log real: ticks_in_flight, ids 9/10, 401/401/401+400, backoff+recovery).

## Hallazgos consolidados (severidad reconciliada)

### P1 — bloquean; van al X.5
- **P1-1 · A1 test tautológico** — `run-poc.ps1:633-636` cae a `playerReadyPos` (lectura del bridge) cuando el
  marker no está flusheado → A1 compara bridge-vs-bridge → distance 0 trivial. **Disparó** en el run canónico.
  (Codex P1-b; la mitad Claude lo subestimó como observación.) **Fix**: quitar el fallback; tras player-ready
  reintentar `Get-SpawnActual` hasta que el marker flushee (timeout ~15s) y FALLAR el gate si nunca aparece. El
  marker truncado (6063.02) vs bridge = 0.0031 m < 0.5 m → pasa legítimo; no tocar precisión ni umbral.
- **P1-2 · verdict `overall_pass` falseable** — `mcp_client.py:288-292` (excepción → `{overall_pass:false}` sin
  `tests`) + `run-poc.ps1:363-374` (recomputa desde tests visibles) → si el cliente A1-A4 crashea y A5 pasa,
  `poc-verdict.json` queda `overall_pass=true`. Latente (no disparó en run_045213). (Codex P1-a = mi P2-2.)
  **Fix**: `Update-PocVerdictTest` solo pone `overall_pass=true` con A1-A5 TODOS presentes y pass, sin `error`
  top-level y `clientExitCode==0`; AND-ear con el `overall_pass` previo del cliente.

### P2 bridge — en el X.5 (scope B: los baratos)
- **P2-1 · malformed-JSON sin backoff** — `MCPBridge.c:160-167`: `m_Backoff=0` antes de chequear el parse; un
  200+JSON inválido resetea backoff y vuelve a 5Hz → log spam. **Fix**: mover `m_Backoff=0` a DESPUÉS de parse
  OK; parse-fail/null-commands → backoff rate-limited (sin romper el happy path).
- **P2-2 · sin teardown del bridge** — no limpia `RestContext`/refs en fin de mission (`MissionServer.c` sin
  destructor; `m_CallbackRefs` solo libera al disparar). **Fix**: `Shutdown()` en MCPBridge (clear refs, null
  ctx) desde el destructor de la modded `MissionServer`. **[verify R2]** las APIs que Codex sugiere
  (`RestContext.reset()`, `DestroyRestApi()`, destructor `missionserver.c:78`) antes de usarlas.

### P2 diferidos a fase 1 (scope B: NO en el X.5)
- **P2-3 · backpressure** — cola/batch/results sin tope ni budget/tick (`mcp_server.py:22-23,151`; `MCPBridge.c:179`
  despacha todo el batch en un callback). Pertenece al diseño de fase 1 (comandos con side-effects).
- **P2-4 · `TryInit` get-or-create de RestApi por-tick** `[verify]` (`MCPBridge.c:49-53,74-77`): si `CreateRestApi()`
  no es idempotente, crea múltiples instancias en la ventana pre-config. Verificar semántica de `CreateRestApi()`
  (`restapi.c:181`) y añadir throttle de init en fase 1 si aplica. (mi P2-1; Codex no lo tocó.)

### P3 — backlog (bug-ledger)
A2 baseline-fps no fiable; refs de callback huérfanas; A5 solo cubre refused (no timeout); keyfiles persisten;
no barre DayZDiag stale; `OnTick(0.0)` en OnMissionStart removible; helpers PS duplicados; abort no-loopback
inalcanzable. Detalle en `2026-06-07-r21-claude-poc-code.md`.

## Falsos positivos (acuerdo Claude + Codex)
modded MissionServer en layer correcto; key-en-query es invariante cerrada (no bug); at-most-once OK para POC
read-only; caminos de backoff refused/timeout existen.

## Re-certificación (gate de cierre del X.5)
Tras los fixes, 1 run del POC debe mostrar: **A1 distance ≈ 0.003 m (NO 0.000 exacto — un 0 exacto = el fallback
sigue firing)** y `target` del verdict = `6063.02…` (marker), NO `6063.0185546875` (bridge); A2-A5 PASS;
`overall_pass=true` legítimo con A1-A5 presentes; lógica del verdict-flip demostrada (cliente con error → no true).

Status: **open → X.5 (sesión 9)**. Scope B confirmado con usuario 2026-06-07.
Lección de proceso: la doble revisión independiente cumplió su función — Codex cazó el A1 tautológico que la
pasada Claude dio por bueno (asumí el marker sin verificar la procedencia del fixture).
