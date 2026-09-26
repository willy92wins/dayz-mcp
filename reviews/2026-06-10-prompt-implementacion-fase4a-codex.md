# Prompt implementación Fase 4A (wrapper MCP stdio) — para Codex CLI

> Generado 2026-06-10 tras aplicar la R22 (plan v2 aprobado). Patrón: implementation-handoff.
> Scope: SOLO tramo 4A del plan v2. El tramo 4B (Enforce) va en handoff aparte.

===== PROMPT INICIO =====

Tarea: implementar el **tramo 4A** del plan Fase 4 de DayZ-MCP — wrapper MCP stdio
(package `dayz_mcp/`) + shim back-compat + packaging. Esta sesión cubre **únicamente 4A
(Python-only, CERO rebuild del PBO)**. El tramo 4B (handlers Enforce `world_time_set` /
`world_weather_set` / `exec_enforce` + version-report en los pollers) queda para una sesión
posterior: NO lo implementes aquí ni siquiera parcialmente, ni en Python ni en Enforce.

## Carga inicial obligatoria

Lee estos archivos antes de tocar nada:

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-10-fase4-mcp.md
   (plan **v2** con la R22 aplicada — VINCULANTE entero; tu scope = §3 + §6 + §7 + gate §8-4A).
2. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-10-fase4-mcp-stdio.md
   (firmas reales del SDK mcp 1.27.2 verificadas por smoke install + implicaciones 1-5).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py
   (el loopback a EXTRAER a lib — extracción mecánica, no reescritura).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_capture.py
   (READ-ONLY: la interfaz que `capture_screenshot` importa — `capture_screenshot` :279,
   `image_content_from_png_bytes` :166).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\
   (unittests existentes: deben seguir PASS SIN tocarse).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md
   (convenciones del proyecto: async-only, fail-closed, key en query).

Fuentes de CONTRATO (read-only, consultar solo al derivar los schemas de las tools):
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
  (dispatch :334-364 — args/result reales de los 6 cmds server).
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c
  (dispatch :423-427 — camera_set/camera_get).
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py
  (cómo el harness construye args de cada cmd).

NO releas el research de Fase 3, ni los reviews in-game de D1/D2, ni el architecture doc.

## Alcance acotado — pasos del plan v2 §3 + §6

### Paso 1 — `tools/dayz_mcp/loopback.py` (lib extraída)

Extracción mecánica de `ServerState`/`Handler`/colas desde `mcp_server.py`. Diff funcional
~cero SALVO los 4 cambios del plan §3.2:
1. Log sink parametrizado (los `print()` de :155/:192/:218/:239/:264 → callable/logger
   inyectable; el shim mantiene stdout, el modo MCP manda a stderr).
2. `last_poll_at[peer]` (monotonic) en cada `/poll` → alimenta `bridge_status`.
3. Hook de lectura `ver=` del query de `/poll`: si viene, se guarda por peer (la VALIDACIÓN
   vive en el server MCP; en 4A ningún poll real la manda → estado `legacy`).
4. API encapsulada de `ServerState`: métodos sync breves con lock interno
   (`enqueue_command`, `take_result`, `status_snapshot`, `record_poll`). Nadie fuera de la
   clase toca los dicts.

**CONSTRAINT de imports**: `loopback.py` es **stdlib-only** (el harness debe poder correr
sin el venv). SOLO `dayz_mcp/server.py`/`__main__.py` importan `mcp`.

### Paso 2 — `tools/mcp_server.py` (shim back-compat)

Conserva nombre, CLI exacto (`--port`, `--keyfile`) y comportamiento HTTP-only con los
**5 endpoints** (`/enqueue /await /poll /result /set_poll_delay`) — `run-fase3.ps1:256` lo
lanza por path y la suite de backpressure usa `/set_poll_delay` (plan §3.1, R22-F4-005).
Importa `dayz_mcp.loopback`; logs por stdout como hoy.

### Paso 3 — `tools/dayz_mcp/server.py` + `__main__.py` (el server MCP)

Según plan §3.3 (vinculante). Resumen de piezas — el detalle manda el plan:
- `FastMCP(name="dayz-mcp", instructions=…)`; `mcp.run(transport='stdio')`; logs SOLO stderr.
- Lifespan con wrapper `LoopbackServer.start()/stop()` (stop = `shutdown()` +
  `server_close()` + `join(timeout)` + pendientes cancelados). Bind ocupado → exit
  fail-closed con mensaje claro.
- Las **9 tools 4A** (plan §2): `query_player_state`, `world_spawn`, `vehicle_enter`,
  `scene_raycast`, `telemetry_read` (server peer), `camera_set`, `camera_get` (client peer),
  `capture_screenshot` (import de `mcp_capture`, sin subprocess), `bridge_status`
  (host-only). Schemas de las que bridgean DayZ = 1:1 del cmd bridge (cita `path:line` de
  donde derivas cada campo en el bloque C del handoff).
- Puente tool→bridge: encolar in-process vía la API de `ServerState` + espera con polling
  async (timeout por-tool 15 s default) → timeout = `ToolError` con liveness del peer en el
  mensaje.
- Mutex global `asyncio.Lock` (todas las tools DayZ-touching; `bridge_status` exenta).
  Nunca sostener un lock a través de un `await` de espera.
- `version_state` por peer (`ok`/`legacy`/`legacy_blocked`/`version_mismatch`, plan §5.2):
  la mecánica Python COMPLETA se implementa y unit-testea AQUÍ (simulando polls con/sin
  `ver=`); en runtime 4A solo se verá `legacy` (el bridge aún no manda `ver=` — eso es 4B).
  CLI: `--expected-game-version`, `--require-version` (default OFF), constante
  `EXPECTED_BRIDGE_VERSION`.
- Errores de negocio → `ToolError` (isError), nunca excepción de protocolo.

### Paso 4 — packaging (plan §6)

- `tools/requirements-mcp.txt` → `mcp==1.27.2` (pin; NO subir de versión).
- `tools/install-mcp.ps1`: crea `.venv-mcp` (Python 3.14 host), pip install del
  requirements, genera keyfile si falta REUSANDO el patrón de sembrado del harness (extrae
  de `run-fase3.ps1`/`mcp_client.py` cómo se siembra la config `$mission:dayz_mcp/` +
  `client_profiles`; cítalo, no lo re-inventes), e IMPRIME el comando `claude mcp add` y el
  bloque `.mcp.json` equivalente. **Default = imprimir; registra SOLO con `-Register`**
  (no mutar config del usuario sin pedirlo).
- README corto en `tools/` (plan §6: requisitos, orden de arranque, troubleshooting con
  `bridge_status`, caveat key-en-query, riesgo residual BUG-010/011/012 de `telemetry_read`).

### Paso 5 — unittests nuevos (sin tocar los existentes)

Archivos nuevos en `tools/tests/`:
- `test_loopback.py`: los 5 endpoints (incl. `/set_poll_delay`: válido aplica una vez;
  `ms<0`/`>5000` → `400 bad_ms_range`), `last_poll_at`, hook `ver=`, API `ServerState`.
- `test_mcp_tools.py`: tools contra un peer FAKE (thread que hace `/poll`+`/result` como
  haría el bridge): happy path de 2-3 tools, negativos de args (R22-F4-003: `world_spawn`
  sin `type`, `camera_set` matriz inválida → `isError` limpio), timeout con peer mudo →
  `ToolError` con liveness, mutex (2 calls concurrentes serializadas), `version_state`
  (legacy / legacy_blocked con `--require-version` / mismatch de bridge y de game / ok),
  shutdown limpio (puerto reusable tras stop()).

Comando esperado literal: `python -m unittest discover -s "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests"`
→ TODOS verdes (existentes + nuevos). Pega el output real en el bloque B.

## Restricciones críticas (vinculantes toda la sesión)

1. **PROHIBIDO tocar**: `mcp_capture.py`, `mcp_client.py`, `run-*.ps1`, los tests
   existentes, TODO `DayZ_MCP\` (Enforce/config), `product-spec.md`, `HANDOFF.md`.
2. **Imports**: `loopback.py` y el shim = stdlib-only. `mcp` solo en `server.py`/`__main__.py`.
   Ninguna otra dependencia nueva.
3. **stdout es del protocolo**: en modo MCP ni un `print()` a stdout. El shim HTTP-only
   mantiene su stdout actual.
4. **Tentación anticipada — resiste**: al extraer el loopback vas a querer "mejorarlo"
   (renombrar, tipar, reestructurar el Handler) o "dejar preparado" el enforcement 4B en el
   bridge. NO: extracción mecánica + los 4 cambios listados, y el Enforce ni se abre para
   editar. Si ves deuda real, anótala en el bloque C.
5. **NO improvises fuera del plan**: si algo del plan no encaja al implementarlo, no lo
   "arregles" sobre la marcha — interpretación conservadora + anótalo en el bloque C con
   `plan path:línea`.
6. **Sin instrumentación debug no pedida** (flags `DebugForce*`, branches test-only):
   requiere pregunta explícita previa al usuario; en su ausencia, no va.
7. **R21 NO se hace en esta sesión**: implementa, testea, para. La doble revisión es sesión
   aparte.

## Output esperado al cerrar (bloques canónicos)

- **A** — archivos creados/modificados (paths absolutos).
- **B** — output REAL del comando de tests (no parafraseado).
- **C** — findings sobre el plan durante implementación (cada uno con `plan path:línea`).
- **D** — handoff para la siguiente sesión (estado, qué falta para el gate 4A).

===== PROMPT FIN =====
