# Prompt — Implementación POC fase 0, Step 0 (gate) · Codex sesión 1

Copiar de marcador a marcador en el CLI de Codex.

```
===== PROMPT INICIO =====

Tarea: implementar el **Step 0 (gate de descubrimiento)** del POC fase 0 de DayZ-MCP — un smoke
mínimo end-to-end que prueba que el transporte server-side funciona. Esta sesión cubre **únicamente
el Step 0 del plan v2 §9**. NO implementes los Steps 1-4 (servidor completo, MCPBridge completo,
cliente A1-A5, orquestador completo, seguridad endurecida) ni siquiera parcialmente.

## Carga inicial obligatoria (lee antes de tocar nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-06-poc-fase-0-roundtrip.md
   (plan v2 — el spec vinculante. Foco: §3a MCPBridge/TryInit, §3b servidor, §9 Step 0, §11 APIs.
   El changelog post-R22 §12 forma parte del plan).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (criterio A4 fail-closed: incluso el Step 0 bindea 127.0.0.1 + key; el resto de seguridad es Step 1).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md
   (índice de APIs Enforce; pista, NO verdad — verifica firmas en el source, ver abajo).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\MCPTest\config.cpp
   y C:\Users\guill\OneDrive\Documentos\DayZ Projects\MCPTest\scripts\5_Mission\MCPTest_MissionGameplay.c
   (patrón VALIDADO de CfgMods missionScriptModule + modded mission por filepatching — el mod nuevo
   lo replica, pero con modded MissionServer en vez de MissionGameplay).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DAYZ_INFRA.md
   (lanzamiento diag, serverDZ.cfg allowFilePatching, junction P:\Mods, deploy del mod).
6. C:\Users\guill\MCPTest\mcp-shot-test.ps1
   (referencia VALIDADA de lanzamiento autónomo del diag desde PowerShell — adáptala para el SERVER).

Para R2 (cite-then-verify): el source vanilla está bajo
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\  — verifica ahí cada firma que uses
   (restapi.c, missionserver.c, game.c, object.c, gameplay.c, jsonfileloader.c).
NO abras el architecture doc ni otros mods salvo para verificar un contrato puntual; si lo haces,
justifícalo en el handoff.

## Alcance acotado — SOLO Step 0 (plan v2 §9)

Objetivo del gate: probar in-game (en el **server diag, sin cliente** — MissionServer tickea sin
players) que (a) RestApi está disponible server-side vía get-or-create, (b) el mod carga/filepatchea
en el server, (c) un GET async alcanza 127.0.0.1, (d) JsonSerializer deserializa un batch de 2
comandos, (e) varias callbacks async disparan fiables (lifetime de RestCallback), (f) el `$profile:`
del server resuelve y el config se lee, (g) un POST /result llega.

### Entregables

1. **Mod `@DayZ_MCP`** en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\`:
   - `$PBOPREFIX$` → `DayZ_MCP`
   - `config.cpp` → CfgPatches + CfgMods missionScriptModule (modelado sobre MCPTest/config.cpp;
     `files[]={"DayZ_MCP/scripts/5_Mission"}`).
   - `scripts/5_Mission/`:
     - `MissionServer.c` → `modded class MissionServer`, hook `OnUpdate` (super + bridge.OnTick), per §3a.
     - `MCPBridge.c` → versión **Step 0** del bridge: en el primer tick configurado corre la secuencia
       de gate UNA vez (guard con bool). Reusa `TryInit` de §3a (get-or-create RestApi + JsonLoadFile
       de `$profile:dayz_mcp.json` + SetOption timeouts + GetRestContext + SetHeader). Secuencia:
       lanzar **3** `GET /poll?key=…` async; en cada `OnSuccess` parsear el batch de 2 comandos y
       contar callback; tras el primer parse OK, lanzar **1** `POST /result?key=…` con un payload
       mínimo. Emitir los marcadores de log de abajo. NO implementes cadencia de 5Hz, backoff,
       dispatch real de query_player_state, ni la medida in-flight de A2 (eso es Step 1-3).
     - `MCPMessages.c` → solo `MCPConfig`, `MCPCommand`, `MCPCommandBatch` (de §3a). (MCPResult/
       MCPPlayerState completos son Step 2 — para el /result de Step 0 basta un string JSON a mano.)
     - `MCPCallbacks.c` → `MCPPollCallback`/`MCPResultCallback : RestCallback` (de §3a). Asegura el
       lifetime: guarda una `ref` viva a cada callback en una lista del bridge hasta que dispare.
   - **Marcadores de log obligatorios** (Print con prefijo literal, para verdict greppable en RPT):
     `[MCP-STEP0] restapi=OK` · `[MCP-STEP0] config url=<u> pollHz=<n> keylen=<k>` (NUNCA el valor de
     la key) · `[MCP-STEP0] poll.OnSuccess size=<n>` · `[MCP-STEP0] parsed cmds=<n> ids=<...>` ·
     `[MCP-STEP0] callbacks=<n>/3` · `[MCP-STEP0] result.posted` · `[MCP-STEP0] result.OnSuccess` ·
     `[MCP-STEP0] GATE=PASS` (o `GATE=FAIL reason=<...>`).

2. **Servidor Step 0** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server_step0.py`:
   - `http.server.ThreadingHTTPServer`, **stdlib only**. Bind **127.0.0.1** + assert `server_address[0]=="127.0.0.1"`
     o `sys.exit(1)`. Key en query string (`?key=`); si falta/!=: 401.
   - `GET /poll` → devuelve un batch FIJO de 2 comandos: `{"commands":[{"id":1,"cmd":"query_player_state"},{"id":2,"cmd":"query_player_state"}]}`.
   - `POST /result` → loguea a stdout lo recibido (sin la URL/key) y responde `{"ok":true}`.
   - NO implementes /enqueue, /await, /set_poll_delay, whitelist completa ni delay (eso es Step 1).
   - `--port` (def 8765) `--keyfile`. Loguea cada hit (método+ruta SIN query string).

3. **Orquestador** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-step0.ps1`:
   - Genera key (`token_urlsafe`-equivalente), escribe keyfile (para Python) + `dayz_mcp.json`
     (`{url,key,pollHz}`) en el **profile dir del server diag** (el `-profiles=` que uses).
   - Arranca `mcp_server_step0.py` (background).
   - Despliega `@DayZ_MCP` por **filepatching** (scripts-only, sin re-empaquetar) y lanza el **diag
     SERVER** con `@DayZ_MCP`, `allowFilePatching=1` (adapta de DAYZ_INFRA.md + mcp-shot-test.ps1).
     NO hace falta cliente para el Step 0.
   - Espera ~60-90s, recoge: stdout del Python + el RPT del server (grep de `[MCP-STEP0]`).
   - Rutas del HOST (no del sandbox); descubre/parametriza, no hardcodees mounts.

### Criterio de "gate PASS" (lo que esta sesión debe demostrar)

- Python stdout: ≥1 hit a `/poll` (key OK) y ≥1 `POST /result`.
- RPT del mod: `restapi=OK`, `config ...` con campos reales, `poll.OnSuccess`, `parsed cmds=2`,
  `callbacks=3/3`, `result.posted` + `result.OnSuccess`, y `GATE=PASS`.
- Si algún sub-check falla irreparablemente (p.ej. RestApi null incluso tras CreateRestApi) →
  `GATE=FAIL reason=...`, documenta en el handoff y para. Eso dispara el fallback client-first del
  plan §8 (NO lo implementes tú; solo repórtalo).

## Restricciones críticas (vinculantes toda la sesión)

1. **Python: solo stdlib**. Permitido: `http.server`, `json`, `threading`, `time`, `secrets`,
   `argparse`, `sys`, `os`, `urllib.parse`. Cualquier otro import (requests, flask, fastmcp) es regresión.
2. **Enforce: solo async**. `RestContext.GET/POST` con callback. `GET_now/POST_now` PROHIBIDOS.
   Init de RestApi por **get-or-create** (`GetRestApi()`→si null `CreateRestApi()`→si null FAIL), como
   el prior-art `LFPowerGrid\scripts\4_World\LFPG_BTCPriceFetcher.c:132-138` (#ifdef SERVER).
   `SetHeader` solo Content-Type. Key en query string. NUNCA loguees el valor de la key.
3. **Reglas Enforce de tu skill** (enforce-script-reference): respeta 11b (Print(string.Format(LOG_TAG+))),
   11g (&&/|| en una línea), 8 (no `new` en ticks periódicos). El gate corre UNA vez (guard), no por tick.
4. **NO scope creep**. Te tentará construir el MCPBridge completo (cadencia, backoff, dispatch real,
   medida in-flight A2) o el servidor completo (/enqueue, /await, /set_poll_delay, whitelist). RESISTE:
   esto es solo el smoke del gate. Lo demás es Step 1-3 en sesiones siguientes.
5. **NO toques** el plan, product-spec, architecture doc, harness-apis, ni otros mods (LFQuad, etc.).
   NO `git init`. Crea archivos normales en disco.
6. **NO improvises fuera del plan**. Si algo del plan no encaja (firma que no existe, $profile: que no
   resuelve), no improvises: anótalo en el handoff con la cita del plan, aplica lo conservador, marca
   para revisión. NO te auto-revises (R21 es sesión separada): termina el Step 0 y para.
7. **OneDrive**: el repo está en OneDrive. Para .py grandes usa escritura atómica (no Edits parciales
   encadenados); verifica el archivo tras escribir. (El mod .c/.cpp igual.)

## Output esperado al cerrar la sesión

### Bloque A — Archivos creados/modificados
Lista con paths absolutos y tamaño aprox.

### Bloque B — Resultado del gate (verificación)
Pega LITERAL: el stdout del `mcp_server_step0.py` (hits) + las líneas `[MCP-STEP0]` del RPT del
server + el veredicto `GATE=PASS/FAIL`. No parafrasees.

### Bloque C — Hallazgos durante la implementación
Qué no encajó del plan, qué firma hubo que ajustar (con path:line del source), decisiones
conservadoras tomadas. Si no hay: "Sin hallazgos."

### Bloque D — Handoff para la sesión siguiente
Estado al cierre · si GATE=PASS, qué Step sigue (Step 1: servidor completo + seguridad) · infra ya
lista reutilizable (orquestador, config $profile:) · deuda dejada.

===== PROMPT FIN =====
```

## Notas para Claude (receptor, no para Codex)

- Verificar Bloque A con Read host-direct (Codex a veces declara archivos que no escribió).
- Bloque B: confirmar que las líneas `[MCP-STEP0]` parecen RPT real, no parafraseado (pre-output P2).
- Si `GATE=PASS` → siguiente prompt = implementation Step 1 (servidor completo + seguridad). Si
  `GATE=FAIL` → evaluar el fallback client-first (plan §8) antes de seguir.
