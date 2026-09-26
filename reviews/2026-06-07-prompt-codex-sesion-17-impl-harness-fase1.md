# Prompt Codex — sesión 17 — implementación del harness de fase 1 + run in-game (R5)

> Patrón: implementation-handoff (post-R22, plan v2 approve-with-minor). Generado por Claude 2026-06-07.
> Copiar de marker a marker.

```
===== PROMPT INICIO =====

Tarea: implementar el harness de test in-game de la fase 1 de DayZ-MCP y CORRERLO una vez. Esta
sesión cubre **únicamente** (1) extender `mcp_client.py` con un modo `phase1`, (2) crear
`run-fase1.ps1`, (3) ejecutar `run-fase1.ps1` y devolver el verdict + el DATO del probe B3. NO
implementes nada del bridge, NO toques el código de fase 0 (A1-A5), NO hagas R21/cleanup. El plan v2
ya pasó R22 (approve-with-minor); sus 4 fixes HR22-001..004 YA están incorporados al plan v2.

## Carga inicial obligatoria (lee antes de tocar nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-07-fase1-harness.md
   (EL PLAN v2 — vinculante, con changelog v2 al inicio. Implementa exactamente esto.)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-07-plan-review-harness-codex.md
   (tu propia R22; los 4 HR22 ya están aplicados en el plan v2 — referencia de por qué.)
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py
   (lo que EXTIENDES; el modo POC sin subcomando debe quedar intacto — HR22-003.)
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-poc.ps1
   (el LAUNCH a clonar fielmente: build, mission, init.c spawn, server+client, probe de readiness.)
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py
   (contrato server: whitelist, MAX_QUEUE/429, args no-dict/400, set_poll_delay 0..5000. NO lo toques.)
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (contrato PRODUCTOR: shape de MCPResult y errores por comando. SOLO lectura, NO lo toques.)
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (DTOs MCPArgs/MCPResult. SOLO lectura, NO lo toques.)

NO leas: el research consolidado, los R21 -claude/-codex, el product-spec, el HANDOFF. El plan v2
destila el contrato. NO abras run-step0.ps1.

## Alcance acotado — pasos 1-3

### Paso 1 — `mcp_client.py`: modo `phase1` (extensión ADITIVA)
Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py

- **Retrocompat (HR22-003, crítico):** añade `--mode {poc,phase1}` con **default `poc`**. La invocación
  SIN `--mode` (la de run-poc.ps1:674: `--port --keyfile --spawn --output --timeout`) debe ejecutar
  el camino POC actual byte-idéntico en comportamiento. No reorganices `run_verdict` ni A1-A5.
- **Métodos nuevos en `Client`:** `enqueue_cmd(cmd, args:dict)->int` (camino feliz 200→id) y
  `enqueue_cmd_status(cmd, args)->{status,id,error}` **S4-safe** (captura `urllib.error.HTTPError`
  para 429/400; excepción solo para HTTP inesperado; preserva ids OK). Ver plan v2 §Pieza 1.
- **`run_phase1(args)`:** secuencia S0→S4 del plan v2 (§"Secuencia del cliente phase1"):
  S0 query_player_state→pos; S1 world_spawn con `selected_car_class` (lista de intentos + fallback) y
  aceptación `result.type==selected_car_class`; S1-neg (unknown_type, bad_pos×2, bad_flags OBLIGATORIO,
  args-no-dict→HTTP 400); S2 vehicle_enter con `car_pos_real`; S3 vehicle_drive PROBE (DATO, timeout
  ≥40s); S4 backpressure (S4a 429+S4c bridge_queue_full vía set_poll_delay(5000); S4b cap≤4/tick
  agrupando por `tick_dispatch`).
- Escribe `tools/fase1-verdict.json` con la estructura del plan v2 §"fase1-verdict.json".
  **`overall_pass` = B1 ∧ B1_neg ∧ B2 ∧ S4. B3 NO entra** (es DATO; LL-116). Mensajes de error
  esperados EXACTOS como en el contrato (unknown_type/bad_pos/bad_flags/bad_args).

### Paso 2 — `run-fase1.ps1` (NUEVO, clon enfocado de run-poc.ps1)
Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-fase1.ps1

- COPIA las funciones de launch de run-poc.ps1 (Read-SharedText, New-*Token, Find-NewestFile,
  Resolve-AddonBuilderPath, Resolve-ServerMissionPath, Ensure-DayZWorkDrive, Invoke-DayZMcpPboBuild,
  writers de init.c/serverDZ.cfg/dayz_mcp.json, probe de readiness). Mismo build PBO `-packonly` con
  verificación de marker `[MCP-POC]`, misma mission chernarus completa + init.c con spawn fijo.
- **SIN el bloque A5** (no stop/restart del server). Tras player-ready, corre
  `python mcp_client.py --mode phase1 --port <p> --keyfile <k> --output <fase1-verdict.json> --timeout <t>`.
- `GATE=PASS` = `clientExit==0 && fase1-verdict.json overall_pass==true`. Imprime bloques claros:
  ADDONBUILDER / PYTHON STDOUT / MCP MARKERS [MCP-POC] / FASE1 VERDICT / **B3 DECISION DATA** / RPT paths.
- Param `-DayZPort 2402` por defecto (heads-up: si hay otra instancia DayZDiag corriendo —p.ej. LFQuad—,
  detenla antes para un run concluyente).

### Paso 3 — ejecutar el run in-game (R5, un solo build/launch)
- Ejecuta `run-fase1.ps1`. Es el único gate pendiente de la fase 1.
- Recolecta: `fase1-verdict.json` completo, el bloque B3 DECISION DATA, GATE, RPT + script*.log, markers.

## Verificación offline previa al run (stdlib)
- `python -m py_compile mcp_client.py` OK.
- `python -m unittest discover tools\tests` → los tests existentes del server SIGUEN verdes (no-regresión).
- Retrocompat: `python mcp_client.py --help` muestra `--mode` con default poc; el parse de la línea POC
  (sin --mode) no rompe.

## Restricciones críticas (vinculantes toda la sesión)
1. **Stack:** cliente = Python **stdlib only** (urllib/json/argparse/statistics/math/time — los que ya usa).
   Orquestador = **PowerShell 5.1**. Cualquier dep nueva es regresión.
2. **NO toques:** `MCPBridge.c`, `MCPMessages.c`, `mcp_server.py`, la lógica A1-A5 de `mcp_client.py`,
   `run-poc.ps1`, `run-step0.ps1`. Solo: extensión aditiva de `mcp_client.py` + nuevo `run-fase1.ps1`
   + output `fase1-verdict.json`.
3. **B3 es DATO, NO pass/fail (LL-116):** `overall_pass` excluye B3; si `vehicle_fixture_ready==false`
   el probe es `inconclusive`, NO fail. No "arregles" speedo≈0 como si fuera un bug del harness.
4. **Tentación de factorizar (resiste):** el plan v2 manda COPIAR las funciones de launch, NO extraer un
   common dot-source — eso tocaría run-poc.ps1 probado (R3). Si crees que factorizar es neto, anótalo en
   Bloque C, NO lo hagas.
5. **Tentación de "mejorar de paso" (resiste):** no refactorices A1-A5 ni arregles el marker stale de
   run-step0.ps1; está fuera de scope.
6. **NO improvises fuera del plan (R22-corto):** si algo del plan v2 no encaja (p.ej. set_poll_delay no
   fuerza el 429 de forma fiable por timing), NO rediseñes: reintenta la ventana 1-2 veces (ya previsto
   en S4), aplica interpretación conservadora, y anota el problema en Bloque C con path:line del plan.
7. **NO R21/self-review del harness en esta sesión.** Implementa, verifica offline, corre el run, reporta.
   La doble revisión del código del harness, si hace falta, es sesión aparte.

## Output esperado al cerrar (pega en chat + deja el verdict en disco)

### Bloque A — Archivos creados/modificados
Paths absolutos + tamaño aprox (run-fase1.ps1 nuevo; mcp_client.py extendido; fase1-verdict.json generado).

### Bloque B — Resultado de verificación
- Salida literal de py_compile + `unittest discover tools\tests` (no-regresión server).
- `GATE=PASS/FAIL` del run + **`fase1-verdict.json` LITERAL completo** (no parafrasear).
- El bloque **B3 DECISION DATA** {vehicle_fixture_ready, engine_on_server, speedo_max, pos_delta,
  net_strategy, interpretation} y los markers `[MCP-POC]` relevantes.

### Bloque C — Hallazgos durante implementación
Lo que no encajó del plan v2 (con path:line), interpretación aplicada, sugerencia. Si nada: "Sin hallazgos".

### Bloque D — Handoff para sesión siguiente
Estado al cierre (B1/B2/negativos/S4 PASS? B3 DATO recolectado?), próximo paso (Claude analiza el DATO
host-direct y decide B3: server/client-peer/diferir), deuda dejada.

===== PROMPT FIN =====
```

## Tras el run (receptor, Claude)
El usuario pega el verdict + B3 DECISION DATA. Claude verifica host-direct (Pattern 2): lee
`fase1-verdict.json` y los markers `[MCP-POC]` en el RPT real, confirma B1/B2/negativos/S4 y la
interpretación del DATO B3 (sin fiarse del resumen). Decide B3 con el usuario y cierra la fase 1.
