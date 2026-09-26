# Prompt Codex — sesión 14 · R21 estructural (code review) fase 1 completa

> Patrón: R21 estructural (doble revisión independiente). Codex revisa el CÓDIGO de fase 1 (Paso 0 + 1a + 1b);
> NO rediseña. Paralelo e independiente al pase de receptor de Claude (NO se le pasan los hallazgos de Claude).
> Generado 2026-06-07. Tras esto: consolidar (Claude) → corrective pass → test in-game agrupado (R5).

```
===== PROMPT INICIO =====

Tarea: R21 estructural (revisión de código independiente) de la fase 1 (Control) de DayZ-MCP — el código
COMPLETO ya implementado (Paso 0 + 1a world_spawn + 1b vehicle_enter/vehicle_drive PROBE). Emites veredicto
+ matriz de hallazgos por severidad. NO rediseñas ni reescribes el código (findings, no rewrite).

## Carga inicial obligatoria (leer antes de nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-07-fase1-control.md
   (PLAN v2 — la spec que el código implementa; verifica que el código la cumple).
2. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-07-fase1-control.md
   (research consolidado: B3 client-auth, CONFLICT-2 predicado de asiento, EngineStart gated, LL-115).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (criterios B1/B2/B3 + cláusula de desafío).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md
   (invariantes: async only, Enforce 1.29 SIN ternario ?:, fail-closed, ADR P2-4 NO DestroyRestApi).

CÓDIGO A REVISAR:
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c (núcleo)
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c (DTOs)
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py (server)
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_mcp_server.py
Source vanilla para verificar firmas/semántica: C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\ .

## Dimensiones de la revisión
1) **Correctitud vs plan v2**: cada handler (world_spawn, vehicle_enter, vehicle_drive probe) hace lo que la
   spec dice; la máquina de estados del probe (PREP→IGNITE→DRIVE→SAMPLE→REPORT) es correcta.
2) **Seguridad fail-closed (R6)**: ¿algún side-effect (CreateObjectEx, StartCommand_Vehicle, EngineStart,
   SetThrottle, OnDebugSpawn) se ejecuta con datos de cliente sin validar? ¿la validación es genuinamente
   fail-closed (default-deny)? ¿algún comando puentea el gate?
3) **Validez del PROBE B3 (CRÍTICO — el DATO decide arquitectura)**: ¿un resultado `speedo_max≈0`/`pos_delta≈0`
   significa INEQUÍVOCAMENTE client-auth, o puede ser artefacto del fixture/secuencia (LL-115)? Traza el DATO de
   extremo a extremo: prep del coche, secuencia de arranque/throttle, muestreo, y la interpretación. ¿El probe
   sostiene las condiciones de conducción durante todo el muestreo? ¿`net_strategy` se mapea bien al enum real
   (pawn.c)? ¿`engine_on_server` se lee en un momento válido?
4) **Invariantes**: async only (sin `*_now`), SIN ternario `?:`, `query_player_state` sigue síncrono, NO
   `DestroyRestApi`, guard `GetRestApi()`-first intacto.
5) **Ciclo de vida de jobs**: leaks de refs (`subject`/`actor`), manejo de deadline, la iteración del map con
   borrado (¿correcta al remover durante el recorrido?), doble-post de result.
6) **Backpressure**: ¿la cola/`m_Pending` está realmente acotada? ¿coherencia `MAX_QUEUE` (server) vs
   `MAX_PENDING` (bridge)? ¿se puede perder/duplicar trabajo?
7) **APIs Enforce**: cada llamada nativa con firma/semántica real verificada en el source vanilla (no basta que
   compile; ¿se usa bien? p.ej. `SetThrottle` semántica, `OnDebugSpawn` efectos, `GetSeatAnimationType`).
8) **Concurrencia de estado**: el player solo puede estar en un vehículo — ¿seat + drive_probe interactúan bien?
   ¿dos comandos concurrentes corrompen el job map o el estado del player?

## Criterio de severidad
- FAIL bloqueante: bug que invalida un criterio (side-effect sin gate, DATO del probe contaminado/no
  interpretable, invariante rota, leak/corrupción de estado, API mal usada que no hace lo que el plan asume).
- WARN mayor: retrabajo o resultado ambiguo si se ignora (robustez del probe, backpressure con hueco, criterio
  no verificable limpio).
- NIT: mejora opcional.

NO rediseñes. Si crees que algo debe replantearse, dilo en el resumen, sin reescribir.

## Output esperado
Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-07-r21-fase1-codex.md
### Resumen ejecutivo — veredicto (approve / approve with minor / reject) + 3-5 líneas.
### Matriz de hallazgos — | ID | archivo:línea | Severidad | Resumen | Resolución sugerida | (IDs R21-001...).
### Hallazgos detallados — por ID: cita exacta (1-3 líneas con file:line), problema, resolución, ref a
    plan/research/product-spec si aplica.
### Cobertura — invariantes verificadas; validez del probe B3 (¿el DATO será limpio?); APIs verificadas vs no.
### Próximo paso — approve / approve-with-minor (Claude aplica antes del test in-game) / reject.

Cuando termines, cross-linkea en:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md
(append con fecha + status=open).

===== PROMPT FIN =====
```
