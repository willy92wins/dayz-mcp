===== PROMPT INICIO =====

Sesión nueva. **Fase 4 (MCP stdio / FastMCP)** del proyecto **DayZ-MCP** — la última fase: envolver el transporte + las 11 tools (ya probadas in-game) en un servidor MCP stdio real + seguridad endurecida. Esta sesión PLANIFICA la fase (Grill Modo B), NO la implementa.

Nota: el cwd suele ser el padre `C:\Users\guill\OneDrive\Documentos\DayZ Projects\`, donde el hook SessionStart NO localiza el HANDOFF del subproyecto — léelo a mano (item 2).

## CARGA INICIAL MÍNIMA (no abrir más cosas todavía)
Lee solo, en este orden:
1. `C:\Users\guill\ObsidianVault\AI\00_System\workflow.md` (pipeline: Grill Modo B, R22, receptor, roles Claude/Codex).
2. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md` (estado vivo — header `LIVE-STATE`: las 4 fases PASS in-game, deuda, invariantes cerradas).
3. `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-06-10-DayZ_MCP-fase3-complete.md` (sesión que cierra Fase 3 — sobre todo "Próximos pasos", "Deuda", "Bloqueos / notas").
4. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md` (contrato — **grupo E = Fase 4**: E1 tool surface vía MCP stdio, E2 seguridad endurecida, E3 packaging/install, E4 concurrencia).
5. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md` (§4 tool surface = 11 tools/6 dominios, §7 seguridad fail-closed, §8 plan por fases — Fase 4 = wrapper MCP stdio/FastMCP).
6. `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md` (APIs verificadas in-game — NO re-confabular firmas).
7. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py` (el loopback HTTP actual `/enqueue`/`/await`/`/poll`/`/result` que Fase 4 envuelve o reemplaza con MCP stdio).

NO releas los handoffs/reviews intermedios de D1/D2 (`reviews/2026-06-10-fase3-d1-ingame.md`, `-d2-ingame.md`) ni el research/plan de Fase 3, salvo que surja una contradicción específica que el HANDOFF no resuelva.

## OBJETIVO DE LA SESIÓN
Producir el **plan de Fase 4 (Grill Modo B)** en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\YYYY-MM-DD-fase4-mcp.md`, trazando cada pieza a E1-E4 del product-spec. Es PLANNING — la implementación va a Codex tras R22.

El plan DEBE resolver explícitamente estos prerrequisitos (Grill Modo B / cláusula de desafío) ANTES de la primera generación pesada:
(a) **FastMCP vs MCP stdio crudo (JSON-RPC stdlib)**: la arquitectura mencionó FastMCP, pero el server fase 0 es "stdlib-only" y FastMCP es dependencia externa. Decidir y bajar al Changelog si cambia el alcance.
(b) **El wrapper MCP, ¿reemplaza o envuelve el loopback HTTP `mcp_server.py`?** El agente MCP llama tools tipadas → el wrapper traduce a comandos del bridge (hoy: HTTP `/enqueue`+`/await` server-domain, y `/poll?peer=client` para Visual). Decidir topología (MCP stdio → HTTP loopback → bridge, o MCP stdio habla directo al bridge).
(c) **Seguridad endurecida E2**: snapshot de `ERPCs` + hash de versión DayZ validados en handshake; `exec_enforce` solo con allowlist (breakglass auditado). Alcance en Fase 4 vs diferido.
(d) **Deuda heredada** (scene-freeze server-side `SetTimeMultiplier`; migrar `MCPBridge.c`→base compartida `MCPJobRunner`, D-13): ¿se folda en Fase 4 o queda como tarea aparte?

## YA CERRADO en sesiones anteriores (NO rediscutir)
- Las **4 fases (transporte/control/observación/visual) PASS in-game**. SUP-1/SUP-2 (D-11 cámara `Camera` entity; D-12 transporte T-A poller cliente). Diseño D-13 (tools separadas, `SetOrientation`, `MCPJobRunner`). CONFLICT-1 (tokens) / CONFLICT-2 (captura síncrona).
- El **mecanismo** de cada una de las 11 tools ya está probado: control/observación engine-native vía el bridge server (`MissionServer`+`MCPBridge`); Visual = cámara client-side (`MCPClientBridge`) + captura host-side (`mcp_capture.py`). Fase 4 las EXPONE vía MCP, no las re-implementa.

## REGLAS QUE APLICAN
- **R24**: fase 0 (research) solo de lo NUEVO de Fase 4 (FastMCP/stdio idioms, MCP handshake/security). NO re-research de Fase 3.
- **R22**: cuando el plan esté escrito, Codex lo revisa (o review adversarial con subagentes) antes de cualquier línea de código. No improvisar implementación.
- **R2**: cualquier API/path/dependencia mencionada, verificada (grep/read/pip). Cite-then-verify durante la redacción.
- **R18**: ante ambigüedad de alcance/topología/dependencias, AskUserQuestion en vez de presuponer (los prereqs a-d son candidatos a AskUserQuestion al inicio).
- **R11**: conclusión arriba, sin floritura.

## ENTREGABLE
1. (si aplica) research fase 0 de lo NUEVO en `AI/10_Projects/DayZ_MCP/research/`.
2. Plan Fase 4 (Grill Modo B) en `DayZ_MCP_dev/plans/`.
3. Tras el plan: handoff R22 / review adversarial (paso 2).

## PROHIBIDO en esta sesión
- Implementar Fase 4 (es planning; Codex implementa tras R22).
- Re-litigar SUP-1/SUP-2, el research de Fase 3, o las 4 fases ya PASS.
- Tocar el Enforce o el Python de D1/D2 ya validados.

Cuando termines el plan, prepárate para el handoff a Codex (R22 o implementación). El usuario decide si Codex revisa en la misma sesión o en otra.

===== PROMPT FIN =====
