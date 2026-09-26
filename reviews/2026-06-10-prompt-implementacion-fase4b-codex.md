# Prompt implementación Fase 4B (handlers Enforce + version-report) — para Codex CLI

> Generado 2026-06-10 tras GATE 4A PASS. Patrón: implementation-handoff.
> Scope: SOLO tramo 4B del plan v2 (source-only; el rebuild del PBO y el gate in-game los
> conduce Claude después).

===== PROMPT INICIO =====

Tarea: implementar el **tramo 4B** del plan Fase 4 de DayZ-MCP — los 3 handlers Enforce
nuevos (`world_time_set`, `world_weather_set`, `exec_enforce`) + el version-report `ver=`
en ambos pollers + sus 3 tools MCP y el gating de exec. Esta sesión es **source-only**:
NO empaques el PBO, NO lances el juego, NO corras gates in-game (los conduce Claude).
El tramo 4A está COMPLETO y gateado in-game — no lo retoques.

## Carga inicial obligatoria

Lee estos archivos antes de tocar nada:

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-10-fase4-mcp.md
   (plan v2 — VINCULANTE; tu scope = §4 entero + §5.3 + las tools 4B de §2; el gate §8-4B
   es lo que Claude verificará después: tu código debe hacerlo posible).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-10-fase4a-gate-ingame.md
   (trail del gate 4A — constraints heredados: bridge-version EXACTA, notas operativas).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (server peer: dispatch :334-364 cuyo patrón else-if/DTO/result siguen los 3 handlers
   nuevos; y la construcción de la URL del /poll donde va `&ver=`).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c
   (client peer: su poller también añade `&ver=` — mismo cambio mínimo).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\
   (server.py: registrar las 3 tools — el `version_state`/`--require-version`/
   `EXPECTED_BRIDGE_VERSION="4"` YA están implementados y gateados, NO los reescribas;
   loopback.py: whitelist a extender + hook `ver=` ya existente).
6. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-10-fase4-mcp-stdio.md
   (firmas Enforce 4B verificadas host-direct: SetDate/GetDate, Weather/WeatherPhenomenon,
   GetVersion, ExecuteEnforceScript — con path:line).
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md
   (convenciones: async-only, fail-closed, Enforce 1.29 SIN ternario `?:`, key en query).

NO releas: research/plan de fases 0-3, reviews in-game de D1/D2, el architecture doc, ni
el prompt/trail de 4A más allá del punto 2.

## Alcance acotado — plan v2 §4 + §5.3

### Paso 1 — Enforce: 3 handlers nuevos en MCPBridge.c (server peer)

Siguen EXACTAMENTE el patrón del dispatch existente (else-if + DTO + result JSON). Los 8
handlers existentes NO se tocan.

**`world_time_set`** (plan §4.1): args `{year, month, day, hour, minute, time_multiplier?}`.
Validación fail-closed de rangos ANTES de aplicar (month 1-12, day 1-31, hour 0-23,
minute 0-59; `time_multiplier` solo si viene: 0..64 o -1, `world.c:15-19`). Aplica
`World.SetDate(y,m,d,h,min)` (`world.c:51`) y opcionalmente `SetTimeMultiplier`. Result:
read-after-write con `World.GetDate(out …)` (`world.c:33`) → `{applied:{year,month,day,hour,minute}}`.

**`world_weather_set`** (plan §4.2): args `{overcast?, rain?, fog?, time?, min_duration?}`
(cada fenómeno opcional, 0..1; time/min_duration ≥0, default 0). Fuera de rango o ningún
fenómeno presente → error fail-closed sin aplicar nada. Aplica `GetGame().GetWeather()`
(`game.c:1349`) → `GetOvercast()/GetRain()/GetFog()` (`weather.c:183/186/189`) →
`WeatherPhenomenon.Set(forecast, time, minDuration)` (`weather.c:49`). Result:
`{applied:{overcast_actual, rain_actual, fog_actual}}` vía `GetActual()` (`weather.c:38`)
+ los `GetForecast()` (`weather.c:41`) como `forecast` (el gate acepta forecast como
confirmación si `GetActual` converge lento).

**`exec_enforce`** (plan §4.3): args `{expr, main_fn?}`. Mecanismo
`ExecuteEnforceScript(string expr, string mainFnName)` (`game.c:776`). Contrato honesto:
result `{sent: true}` — el output del script-console es DIAG-gated y NO se promete. La
ALLOWLIST vive en el lado Python (§5.3, paso 3) — el handler Enforce ejecuta lo que le
llega porque el server MCP ya filtró; aún así el handler rechaza `expr` vacía.

### Paso 2 — Enforce: version-report `ver=` en AMBOS pollers (plan §4.4)

- Constante de mod compartida `MCP_BRIDGE_VERSION = "4"` — **EXACTAMENTE "4"**: debe casar
  con `EXPECTED_BRIDGE_VERSION` (`tools/dayz_mcp/server.py:20`, gateado en 4A). Ubícala en
  el archivo común de DTOs existente (verifica el nombre real — hay un MCPMessages.c; cita
  `path:line` en el bloque C).
- Ambos pollers añaden `&ver=<MCP_BRIDGE_VERSION>~<game_version>` a la URL de su `/poll`,
  con `g_Game.GetVersion(out string version)` (`game.c:944`). Cambio MÍNIMO: solo la
  construcción de la URL; cachea la string de versión (no llames GetVersion cada poll).
  OJO encoding: la versión del juego contiene puntos (p.ej. "1.29.x") — sin espacios; si
  detectas caracteres problemáticos para query-string, sanea (sustituye espacio por `_`).

### Paso 3 — Python: tools 4B + gating exec (plan §2, §3.3, §5.3)

- `loopback.py`: whitelist + `world_time_set`, `world_weather_set` SIEMPRE; `exec_enforce`
  SOLO cuando el server lo habilita (doble gate — pasa el flag desde la config del server
  al construir el estado; comandos fuera → `400 not_whitelisted` como hoy).
- `server.py`: registra `world_time_set` y `world_weather_set` (mismo patrón que las tools
  4A: validación local de args ANTES de encolar — defensa en profundidad, como hace
  `scene_raycast` —, `runtime.tool_lock`, `call_bridge(..., "server", timeout)`).
- `exec_enforce`: SOLO se registra con `--enable-exec-enforce` (sin flag NO aparece en
  `tools/list` — ya hay precedente de construcción condicional en el app factory).
  `--exec-allowlist <path>`: JSON con lista de expresiones EXACTAS (match de string
  completo; SIN regex, SIN prefijos). Audit JSONL en
  `tools/_audit/exec_enforce.jsonl` (crea el dir): una línea por invocación allowed Y
  denied — `{ts_utc, expr, verdict, command_id?}`. Denied → `ToolError` + línea audit.
- CLI: añade `--enable-exec-enforce` y `--exec-allowlist` a `parse_args`
  (`--require-version`/`--expected-game-version` YA existen — no los toques).

### Paso 4 — unittests nuevos (sin tocar los existentes; los 19 actuales siguen PASS)

Archivos nuevos en `tools/tests/`:
- whitelist extendida: los 2 cmds nuevos aceptados; `exec_enforce` 400 con gating OFF y
  aceptado con ON; `evil` sigue 400.
- `ver=` end-to-end loopback: `/poll?...&ver=4~1.29.x` → `status_snapshot` captura la
  versión por peer (la validación version_state ya está testeada — no la dupliques).
- tools 4B con peer fake (patrón de `test_mcp_tools.py`): happy path de las 2 world;
  negativos de rangos (`month=13`, `hour=24`, `time_multiplier=65`, `overcast=-0.1`,
  `rain=1.5`, payload sin fenómenos) → `ToolError` SIN encolar nada (assert cola vacía).
- exec gating: sin flag → tool ausente de `tools/list`; con flag + expr fuera de allowlist
  → `ToolError` + línea audit `denied`; expr en allowlist → encola + línea audit `allowed`.

Comando esperado literal:
`"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -m unittest discover -s "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests"`
→ TODOS verdes (19 existentes + nuevos). Pega el output real en el bloque B. Verifica
además que los 11 stdlib siguen pasando con el python del SISTEMA (sin mcp):
`python -m unittest tests.test_loopback tests.test_mcp_server tests.test_mcp_capture` desde `tools\`.

## Restricciones críticas (vinculantes toda la sesión)

1. **PROHIBIDO tocar**: los 8 handlers existentes del dispatch (la ÚNICA línea compartida
   que cambias es la construcción de la URL del poller), `MCPJobRunner.c`,
   `MissionServer.c`/`MissionGameplay.c` salvo que el poller viva ahí (cita por qué),
   `mcp_capture.py`, `mcp_client.py`, `run-*.ps1`, `gate4a_mcp_client.py`, tests
   existentes, `requirements-mcp.txt`, `install-mcp.ps1`, `product-spec.md`, `HANDOFF.md`.
2. **Enforce 1.29**: SIN ternario `?:`; condiciones booleanas en una línea (R11g);
   comentarios estilo header vanilla (R6 del proyecto), en inglés.
3. **NADA de migración a `MCPJobRunner`** (G-4): los handlers nuevos siguen el patrón
   actual de MCPBridge.c aunque te tiente unificar. Si ves deuda, bloque C.
4. **NADA de `vehicle_drive` ni `session_*`** (G-5/G-6) — ni "de paso".
5. **Resultado de exec_enforce**: NO intentes capturar el output del script-console
   (DIAG-gated) ni añadir plumbing para ello. `{sent:true}` y punto.
6. **Python**: `loopback.py` sigue stdlib-only; `mcp` solo en `server.py`/`__main__.py`;
   ni una dependencia nueva; stdout del modo MCP intocable (logs → stderr).
7. **Sin instrumentación debug no pedida** (flags DebugForce*, branches test-only):
   requiere pregunta explícita previa al usuario.
8. **NO improvises fuera del plan**: si algo no encaja, interpretación conservadora +
   bloque C con `plan path:línea`.
9. **R21 NO se hace en esta sesión**: implementa, testea en Python, para. El rebuild del
   PBO + gate 4B in-game los conduce Claude.

## Output esperado al cerrar (bloques canónicos)

- **A** — archivos creados/modificados (paths absolutos).
- **B** — output REAL de los dos comandos de tests (venv completo + stdlib con python del
  sistema), sin parafrasear.
- **C** — findings sobre el plan + citas de dónde derivaste cada contrato
  (dispatch/poller/DTOs con `path:line`).
- **D** — handoff para el gate 4B (qué rebuild necesita, qué configs nuevas espera el
  server MCP, cualquier supuesto que el gate deba verificar).

===== PROMPT FIN =====
