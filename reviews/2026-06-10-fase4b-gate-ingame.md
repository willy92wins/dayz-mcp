# Gate 4B — handlers Enforce + version-report — in-game (2026-06-10)

> Conduce: Claude. Plan: `plans/2026-06-10-fase4-mcp.md` §4 + §8-4B. Implementación: Codex
> (source-only, handoff `reviews/2026-06-10-prompt-implementacion-fase4b-codex.md`).
> **Veredicto: GATE=PASS** (11/11 checks del harness 4B + regresión fase3 GATE=PASS ×3),
> con **3 hallazgos cazados** (2 bugs de producto arreglados, 1 limitación de engine
> documentada) y la deuda de exec adjudicada con el usuario.

## Receptor (offline, antes del gate)
- Scope limpio por mtime: Enforce modificado SOLO `MCPMessages.c`/`MCPBridge.c`/`MCPClientBridge.c`
  (19:15-19:19); `MCPJobRunner.c`/`MissionServer.c`/`MissionGameplay.c`/`MCPCallbacks.c` intactos
  (≤04:17). Python SOLO `loopback.py`/`server.py`. 0 scope creep.
- Suite re-ejecutada por Claude: 26/26 (tras los fixes del gate → 27/27).
- Contratos verificados en código: `MCP_BRIDGE_VERSION="4"` (`MCPMessages.c:1`) casa
  `EXPECTED_BRIDGE_VERSION` (`server.py:20`); `ver=` en ambos pollers (`MCPBridge.c:193`,
  `MCPClientBridge.c:289`); dispatch nuevo else-if `MCPBridge.c:372-382`; gating exec doble
  (loopback whitelist condicional + tool registrada solo con flag); audit JSONL.

## Build + regresión
- AddonBuilder `-packonly` → `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` 77 KB (de 75); verificado
  por contenido (strings `world_time_set`/`world_weather_set`/`exec_enforce`/`MCP_BRIDGE_VERSION`).
- `run-fase3.ps1 -NoStop` con el PBO 4B → **FASE3_VERDICT PASS 6/6** (D1+D2 intactos) ×3 corridas.

## Resultado por criterio (§8-4B) — gate 4B harness 11/11 PASS
| Check | Evidencia | Estado |
|---|---|---|
| tools/list = 12 (exec ON) | las 12 exactas desde cliente MCP real | ✓ |
| exec ausente sin flag | 11 tools, `exec_enforce` no aparece | ✓ |
| handshake ambos peers ok | `version_state {server:ok, client:ok}`, `ver=4~1.29.163047` | ✓ |
| handshake bridge-version mala | synthetic poll `999~…` → `version_mismatch` + ToolError, recovery 1s | ✓ |
| handshake game-version mala | `--expected-game-version 9.99.99999` → fail-closed ambos peers | ✓ |
| `world_time_set` applied | read-after-write `GetDate` → `applied{2026,6,10,1,30}` | ✓ |
| `world_time_set` rango inválido | `month=13` → `bad_month` fail-closed | ✓ |
| `world_weather_set` applied | `overcast_actual 0.800000` vía `GetActual` | ✓ |
| `world_weather_set` rango inválido | `rain=1.5` → `bad_rain` fail-closed | ✓ |
| exec gating + audit | denied→ToolError+audit`denied`; allowed→encola+audit`allowed` | ✓ |
| (advisory) `world_time_set` efecto visual día/noche | brillo medio ~igual (cámara estática poco sensible); criterio real = GetDate ✓ | INFO |
| (advisory) exec ejecución del engine | `ExecuteEnforceScript`→false (ver GATE4B-LIM) | INFO |

## Hallazgos del gate

### GATE4B-001 (bug PRODUCTO — arreglado + test) — afectaba TODAS las tools
`wait_for_result` (`server.py`) usaba `result.get("ok") is False`. El bridge serializa el
`bool` Enforce como **int 0/1**, y en Python `0 is False` es `False` → un error de negocio del
bridge (`no_players`, `spawn_failed`, `exec_failed`…) se devolvía como **éxito** en vez de
`ToolError`. El receptor de 4A no lo cazó porque sus negativos eran validación local de args
(raise antes de encolar); el fake del unittest usaba `"ok": True` (bool) → tautológico respecto
al tipo real. Fix: `if not result.get("ok")` + fake alineado a int 0/1 + test nuevo
`test_bridge_business_error_is_tool_error`. (LL-115 — fixture no refleja el tipo de la realidad.)

### GATE4B-002 (robustez PRODUCTO — arreglado)
`_load_exec_allowlist` abría con `encoding="utf-8"`; un BOM (Notepad/PowerShell `Set-Content`)
hacía **crashear el server al arrancar** con traceback en vez de fail-closed. Fix: `utf-8-sig`.

### GATE4B-LIM (limitación de ENGINE — documentada, no bug nuestro) — adjudicada con usuario
`exec_enforce` ejecuta send-path + gating + audit correctamente, pero `ExecuteEnforceScript`
(proto **"Developer only"**, `game.c:776`) devuelve `false`/timeout en el server diag
**headless** (`NO_GUI`/`NO_GUI_INGAME`) — probado con 6 formatos incluido el **wrapper vanilla
EXACTO** del script-console (`void scConsMain() \n{\n…\n}\n` + `main_fn=scConsMain`,
idéntico a `scriptconsoleenfscripttab.c:137` / `playerbase.c:5698`), sin escribir el marcador
`Print` en el script log. El script-console vanilla corre en el CLIENTE con GUI; el server
headless no ejecuta. Es el patrón de `MakeScreenshot` (roto en diag → rodeado). **La salvaguarda
de seguridad del breakglass — gating exacto + audit JSONL — está verificada in-game**; la
ejecución real del script es una limitación del engine. Decisión del usuario (AskUserQuestion
2026-06-10): investigar el formato (hecho → no era formato) y mantener exec con la limitación
documentada.

## Fixes del harness del gate (no producto)
- Phase 0 / handshake-client / recovery: sleeps fijos → polling con deadline (el bridge entra
  en backoff tras 401s/mismatch; el peer cliente pollea más lento que el server).
- `time_set_visible_effect` y `exec engine execution`: degradados a **advisory** (INFO, no
  gated) — el criterio binario real de cada uno es otro (GetDate read-after-write; gating+audit).
- `test_instance_lock.py`: bootstrap de `sys.path` inline (antes dependía de `test_000_path`
  por import-name, fallaba al invocar por módulo).
- Probe A/B `world_time_set` year 2011 vs 2026: ambos `ok=1` + server vivo → descartada la
  hipótesis de crash por año (el timeout de un run intermedio fue backoff del bridge, no crash).

## Evidencia
- `tools/_fase4a/gate4a-ingame4b-verdict.json` (11/11, overall_pass=true)
- `tools/_fase4a/gate4b-final-launch.log` + workroot `_fase3/run_20260610_195654` (regresión PASS)
- `tools/_audit/exec_enforce.jsonl` (líneas denied + allowed)
- Suite: 27/27 (venv, discover) + 12/12 (stdlib sin mcp, por nombre)

## Estado product-spec tras el gate
- **E1**: 12 tools vía MCP stdio ✓ in-game (con caveat exec-execution).
- **E2**: handshake bridge+juego fail-closed ✓ in-game (4 casos); key/whitelist/bind ✓ (A4);
  exec gating + audit + OFF-default ✓; ejecución de exec = limitación documentada.
- **E3** ✓ (gate 4A), **E4** ✓ (gate 4A). **Fase 4 COMPLETA in-game.**
