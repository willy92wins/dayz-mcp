# Prompt R21 estructural Fase 4 — mitad Codex (review a fondo) — para Codex CLI

> Generado 2026-06-11. Patrón: code-review post-implementación (NO plan-review).
> Es la mitad Codex de la doble revisión R21 estructural; Claude corre su mitad en paralelo
> con la skill de revisión. Pegar tal cual en Codex CLI.

===== PROMPT INICIO =====

Tarea: R21 estructural (revisión de código a fondo) del **código de Fase 4** de DayZ-MCP
(wrapper MCP stdio 4A + handlers Enforce 4B), ya mergeado y PASS in-game. Detector principal
de bugs. SOLO review con hallazgos por severidad — NO refactor, NO reescritura, NO tocar código.

CARGA INICIAL OBLIGATORIA

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-10-fase4-mcp.md
   (plan v2 — el contrato que el código implementa; §2 surface, §3 wrapper, §4 handlers, §5 seguridad).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-10-fase4a-gate-ingame.md
   + C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-10-fase4b-gate-ingame.md
   (qué ya se verificó en los gates + los 4 bugs cazados GATE4A/4B-001/002 — NO re-reportar lo ya arreglado).
3. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md
   (BUG-009..016 + la limitación GATE4B-LIM; lo ya conocido no es hallazgo nuevo).

CÓDIGO A REVISAR (el diff de Fase 4; léelo entero)
- Python: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py,
  …\dayz_mcp\server.py, …\dayz_mcp\__main__.py, …\dayz_mcp\__init__.py, el shim …\tools\mcp_server.py,
  …\tools\install-mcp.ps1, y los tests …\tools\tests\test_loopback.py / test_mcp_tools.py /
  test_instance_lock.py / test_fase4b_loopback.py / test_fase4b_tools.py.
- Enforce: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
  (constante `MCP_BRIDGE_VERSION` + DTOs `MCPApplied`/sentinels), y SOLO los bloques NUEVOS de
  MCPBridge.c (dispatch :372-382, `DispatchWorldTimeSet`/`DispatchWorldWeatherSet`/`DispatchExecEnforce`,
  validadores de rango, `GetPollVersion`/`ver=` :193/:1047-1053) y MCPClientBridge.c (`ver=` :289/:948-954).

BOUNDARIES
- NO revises el código de fases 0-3 ya cerrado (los 8 handlers existentes de MCPBridge.c,
  MCPJobRunner.c, MCPClientBridge camera/captura, mcp_capture.py) SALVO la línea del `ver=` que F4
  añadió a los pollers. Si un bug vive ahí y F4 lo agrava, repórtalo; si es preexistente y ortogonal, nótalo aparte.
- NO toques ningún archivo. Tu único write: el archivo de review (ver OUTPUT).
- NO re-reportes los 4 bugs ya arreglados por los gates ni la limitación GATE4B-LIM (están en el bug-ledger).
- NO redesign: hallazgos sobre el código existente, no un diseño alternativo.

DIMENSIONES (foco en lo que los gates NO cubren — casos límite, no el happy path)
1) Concurrencia/async: el puente FastMCP asyncio ↔ loopback thread (`LoopbackServer`, `ServerState`
   con lock interno); ¿alguna ruta async toca los dicts de estado sin pasar por los métodos sync?
   ¿se sostiene algún lock a través de un `await`? ¿el mutex global cubre todas las tools DayZ-touching?
2) Lifecycle: `start()/stop()` (shutdown+close+join+cancel pendientes); ¿fugas de thread/socket si
   stdio muere a media tool-call? ¿el lock de instancia es robusto a carreras de arranque?
3) Fail-closed: handshake `version_state` (4 estados) — ¿algún camino deja pasar un peer sin versión
   con `--require-version` ON? ¿`exec_enforce` allowlist es match exacto sin bypass (prefijo/regex/normalización)?
   ¿el audit registra TODA invocación (allowed Y denied) sin perder ninguna por excepción?
4) Enforce 4B: validadores de rango de `world_time_set`/`world_weather_set` — ¿fail-closed real (rechazan
   ANTES de aplicar)? ¿los sentinels de float-opcional distinguen "omitido" de 0/-1 sin colisión? ¿`ver=`
   se construye sin inyección/caracteres que rompan el query-string? ¿`GetVersion` cacheado, no por-tick?
5) Back-compat: ¿el shim conserva EXACTO los 5 endpoints + CLI? ¿el refactor del loopback cambió alguna
   semántica observable del harness (poll/result/set_poll_delay)?
6) Tests: ¿algún test es tautológico o usa fixture del tipo equivocado (cf. LL-139/GATE4B-001)? ¿faltan
   negativos críticos? ¿la cobertura afirma algo que el fixture no ejerce?

CRITERIO DE SEVERIDAD
- FAIL/P1: corruption, fail-open de seguridad, crash, data-loss, o bug que invalida una tool.
- WARN/P2: degradación, deuda que causará retrabajo, cobertura insuficiente de un caso real.
- NIT/P3: claridad, naming, micro-mejora.

OUTPUT ESPERADO
Archivo único:
C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\reviews\2026-06-11-r21-fase4-codex.md
Estructura: resumen ejecutivo (veredicto clean / needs-fix) · matriz de hallazgos (ID R21-F4-NNN,
sección/archivo:línea, severidad, resumen, resolución sugerida) · hallazgos detallados con cita exacta
(1-3 líneas) · cobertura (qué revisaste, qué dejaste fuera y por qué). Al terminar, append al inbox
con timestamp y status=open: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md

===== PROMPT FIN =====
