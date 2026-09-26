# Prompt arranque sesión — R21 estructural de Fase 4 (Claude/Cowork)

> Generado 2026-06-11. Patrón: next-session bootstrap. Pegar como primer mensaje de una sesión
> Cowork fresca cuando se quiera el cierre formal del código de Fase 4. Es OPCIONAL — el proyecto
> está entregado y PASS in-game; esto es el R21 estructural (doble revisión) que F4 no tuvo.

===== PROMPT INICIO =====

Sesión nueva. Tarea: **R21 estructural de Fase 4** de DayZ-MCP (doble revisión paralela e
independiente del código 4A+4B ya mergeado y PASS in-game). NO es implementación: es revisión.
El cwd suele ser el padre `C:\Users\guill\OneDrive\Documentos\DayZ Projects\` — el hook SessionStart
NO localiza el HANDOFF del subproyecto; léelo a mano (item 2).

## CARGA INICIAL MÍNIMA (solo esto)
1. C:\Users\guill\ObsidianVault\AI\00_System\workflow.md (pipeline: R21 estructural = paso 4, dos
   modalidades; doble revisión Claude+Codex independiente; sesiones correctivas X.5).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md (header LIVE-STATE:
   Fase 4 COMPLETA, proyecto cerrado, invariantes; el R21 estructural es el único item del flujo pendiente).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-10-fase4-mcp.md (contrato
   que el código implementa).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-10-fase4a-gate-ingame.md
   + …\2026-06-10-fase4b-gate-ingame.md (qué verificaron los gates + los 4 bugs ya cazados — no re-reportar).
5. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md (BUG-009..016 + GATE4B-LIM).

## OBJETIVO
Conducir la R21 estructural (doble revisión independiente) del diff de Fase 4:
- **Mitad Claude (tú)**: revisar el código F4 con la skill de revisión de código. Foco en lo que
  los gates NO cubren: concurrencia asyncio↔thread, lifecycle/locks, fail-closed del handshake y de
  `exec_enforce`, validadores de rango Enforce 4B, back-compat del shim, tests tautológicos/wire-type
  (cf. LL-139). Alcance del diff F4: ver el prompt Codex (item 6) — el MISMO scope.
- **Mitad Codex (en paralelo, independiente)**: pegar en Codex CLI
   `DayZ_MCP_dev/reviews/2026-06-11-prompt-r21-estructural-fase4-codex.md`. Codex escribe su review en
   `AI/10_Projects/DayZ_MCP/reviews/2026-06-11-r21-fase4-codex.md`.
- **Consolidar** ambas mitades: hallazgos por severidad → `reviews/codex-review-inbox.md`. P1 →
  sesión correctiva X.5 (scope: solo fixes). P2 → backlog. P3 → bug-ledger. La redundancia es por
  diseño (R21): cada modelo se le escapan cosas distintas.

## ALCANCE DEL DIFF F4 (lo que se revisa; NO el código de fases 0-3 cerrado)
- Python: `tools/dayz_mcp/loopback.py` · `server.py` · `__main__.py` · shim `tools/mcp_server.py` ·
  `install-mcp.ps1` · tests `test_loopback`/`test_mcp_tools`/`test_instance_lock`/`test_fase4b_*`.
- Enforce: `DayZ_MCP/scripts/5_Mission/MCPMessages.c` (const version + DTOs) + SOLO los bloques NUEVOS
  de `MCPBridge.c` (dispatch :372-382, los 3 handlers + validadores + `ver=`) y `MCPClientBridge.c` (`ver=`).

## REGLAS
- R2 cite-then-verify: cada hallazgo cita `path:line` real.
- NO redesign, NO tocar código en la revisión (es review; los fixes van a la X.5 si la hay).
- NO re-reportar los 4 bugs de los gates ni GATE4B-LIM (ya en bug-ledger).
- R11: conclusión arriba. Si la R21 sale clean (cero P1/P2), declararlo y cerrar el proyecto formalmente.

## PROHIBIDO
- Re-litigar las fases 0-3, las decisiones D-01..D-13 / G-1..G-7, o los gates ya PASS.
- Implementar nada en esta sesión salvo que la R21 dispare una X.5 explícita y el usuario la apruebe.

Cuando ambas mitades estén consolidadas, cerrar con post-session (handoff + estado).

===== PROMPT FIN =====
