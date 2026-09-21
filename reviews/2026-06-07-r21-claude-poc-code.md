# R21 estructural (Claude) — código del POC fase 0 · DayZ-MCP

Review independiente (mitad Claude de la doble revisión R21; la mitad Codex corre en paralelo, ciega a
estos hallazgos — prompt `2026-06-07-prompt-codex-sesion-8-r21-poc-code.md`). Alcance: los 4 componentes
del POC. Todo citado `path:line`, leído host-direct esta sesión (2026-06-07). Severidad por el modelo X.5
del workflow (P1 bloquea construir encima · P2 deuda a la próxima sesión normal · P3 backlog).

**Veredicto: sin P1. La base que reusan las fases 1-3 (el transporte/bridge) está sana** — A1-A5 lo prueban
y la lectura lo confirma. 2 × P2 (uno en el bridge, uno en la integridad del verdict); resto P3.

## P2 — deuda a atender antes de apoyarse fuerte

### P2-1 · `TryInit` hace get-or-create de RestApi CADA tick hasta configurar  [verify]
`MCPBridge.c:49-53` llama `TryInit()` en cada `OnTick` mientras `!m_Configured`; `TryInit`
(`MCPBridge.c:74-77`) hace `GetRestApi(); if (!api) api = CreateRestApi();`. Si `GetRestApi()` devuelve
null durante N ticks (antes de que el engine cree el singleton), `CreateRestApi()` se invoca N veces.
- **Riesgo**: si `CreateRestApi()` NO es idempotente (crea instancia nueva cada vez en vez de devolver la
  existente), se crean múltiples RestApi en la ventana pre-config. El prior-art (`LFPG_BTCPriceFetcher.c:132-138`)
  lo llama UNA vez bajo `#ifdef SERVER`, no por-tick.
- **No verificado**: la semántica de `CreateRestApi()` (`..\scripts\3_game\http\restapi.c:181`) —
  idempotente vs always-new— NO está confirmada en source. La ventana es corta (config carga en los primeros
  ticks de mission-ready) y A1-A5 pasaron → empíricamente no rompió. Pero las fases 1-3 reusan este bridge.
- **Fix sugerido**: throttle del init (un intento cada ~1s con su propio backoff) o guard one-shot sobre el
  `CreateRestApi()`. Verificar primero la semántica de `CreateRestApi()` en source.

### P2-2 · `Update-PocVerdictTest` puede voltear `overall_pass` a true cuando el cliente A1-A4 crasheó
Cadena: si `run_verdict` lanza excepción, `mcp_client.py:288-292` escribe `{overall_pass:False, error:...}`
SIN clave `tests`. Luego `run-poc.ps1:363-366` (Update-PocVerdictTest) ve `tests` null → crea un `tests`
vacío, mete solo `A5`, y `run-poc.ps1:368-374` recomputa `overall_pass` SOLO desde los tests visibles (A5).
Si A5 pasa → `overall_pass=TRUE`, con A1-A4 ausentes y el `error` del crash todavía presente.
- **Mitigado parcialmente**: el GATE de consola (`run-poc.ps1:803-806`) exige `clientExitCode -eq 0` (sería 1)
  → `GATE=FAIL`. Pero el `poc-verdict.json` en disco queda con `overall_pass=true` engañoso, y el HANDOFF lo
  trata como "evidencia canónica".
- **Severidad**: integridad del harness de test, NO del transporte. No bloquea fase 1; sí puede engañar una
  lectura futura del verdict.
- **Fix sugerido**: `Update-PocVerdictTest` debe AND-ear el `overall_pass` previo del cliente (o exigir A1-A4
  presentes / propagar el `error` top-level a `overall_pass=false`), no recomputar desde un subconjunto de tests.

## P3 — backlog (no bloquea, registrar)
- **P3-1 · A2 fps son artefactos del diag server** (verdict `fps_in_flight≈7901`, `ticks_in_flight=4741`). La
  propiedad de no-bloqueo está probada (≥5), pero el fps absoluto NO representa el 60Hz de producción — documentar.
- **P3-2 · refs de callback huérfanas**: si un `RestCallback` nunca dispara, su `ref` queda en `m_CallbackRefs`
  para siempre (`MCPBridge.c:331-343` solo libera al disparar). Los POST de result son ilimitados
  (`MCPBridge.c:297-314`, un callback por comando) — escalabilidad fase 1+.
- **P3-3 · sin teardown**: el bridge no limpia `RestContext`/estado en fin de mission (sin `OnMissionFinish`).
- **P3-4 · A5 solo ejercita connection-refused** (OnError rápido, `error=7`), no el path de timeout (server
  colgado pero vivo → OnTimeout 10s). Modo de fallo distinto sin cubrir.
- **P3-5 · server Python: cola/results sin tope ni limpieza** (results dict crece; `/await` no purga). POC ok.
- **P3-6 · keyfiles persisten en el run dir** (`poc.key`, `dayz_mcp.json` con la key). Key efímera por-run y
  bound a 127.0.0.1 → riesgo bajo; limpiar en fase 4 (product-spec E2).
- **P3-7 · el orquestador no mata DayZDiag stale antes de lanzar** → riesgo de colisión de puerto (issue de
  entorno LFQuad ya anotado en HANDOFF). `run-poc.ps1` arranca server+client sin barrer procesos diag previos.
- **P3-8 · `MissionServer.c:7-11` llama `bridge.OnTick(0.0)` una vez en `OnMissionStart`** — no-op útil (config
  no lista ahí; solo logea "init pending"). Removible.
- **P3-9 · helpers PS de log-scraping muy duplicados** (`Get-CombinedEvidence`/`Get-McpMarkers`/
  `Get-PocBackoffEvidence`/`Get-PocCompileErrorEvidence`/`Get-LogFiles`/`Get-CurrentRunLogFiles`). Refactorable.
- **P3-10 · `mcp_server.py:229-231` el abort si no-loopback es inalcanzable** (bind hardcodeado a `127.0.0.1`,
  L228). Defensivo/dead-code, fail-closed por construcción — ok.

## Observaciones (no findings)
- **A1 `distance=0.0` exacto** porque la query precede al asentamiento físico del player; el fixture
  (`spawn_actual` printeado) y la lectura del bridge comparten origen por diseño (R22-004: la mission *setea*,
  el POC *lee*). Robusto: el umbral <0.5 m absorbe el drift (en A5 la Y ya había derivado a 8.029 vs 7.998 de A1).
- **Lifetime de callbacks SANO**: `MCPCallbacks.c:10-17` libera `this` de `m_CallbackRefs` ANTES de llamar al
  bridge, pero es seguro porque el engine retiene su propia ref durante el dispatch; y evita que el array crezca.
- **Thread-safety del server Python correcta**: estado bajo `self.state.lock`; el `sleep(delay)` de `/poll` queda
  fuera del lock (`mcp_server.py:159-160`) → no bloquea otros requests.

Status: **open**. Mitad Claude de R21 estructural. Falta la mitad de Codex (sesión 8) para consolidar por severidad.
