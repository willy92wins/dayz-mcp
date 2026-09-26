# Prompt Codex — sesión 19 — revertir instrumentación SEAT-DIAG + R21 del fix de deadlines

> Cierre técnico de fase 1 (B2 ya cerrado: PASS in-game). Dos tareas: (1) revertir el scaffolding TEST-ONLY,
> (2) R21-Codex (revisión a fondo) del fix de deadlines tick→tiempo de la sesión 18. La mitad R21-Claude
> corre en paralelo e independiente. Generado por Claude 2026-06-08. Copiar de marker a marker.

```
===== PROMPT INICIO =====

Tarea: cerrar técnicamente la fase 1 de DayZ-MCP. (1) REVERTIR la instrumentación diagnóstica TEST-ONLY
SEAT-DIAG (B2 ya pasó). (2) R21 a fondo del fix de deadlines tick→tiempo (sesión 18). NO cambies la lógica
del fix, NO toques A1-A5/server/run-poc.ps1, NO añadas features.

## Carga inicial obligatoria
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-08-fase1-b2-timeout-fix.md
   (qué se implementó en sesión 18; §2 = la instrumentación a revertir; §1 = el fix a revisar.)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (revertir bloques con anchor MCP-SEAT-DIAG; revisar la migración de deadlines a m_ElapsedS.)
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (revertir el campo TEST-ONLY last_diag_s; revisar MCPJob deadline_s/sample_start_s/sample_s_target.)

NO leas: research, product-spec, HANDOFF, run-poc.ps1 (no se toca), mcp_server.py (no se toca).

## Paso 1 — Revertir instrumentación SEAT-DIAG (TEST-ONLY)
- Quita TODOS los bloques marcados `// MCP-SEAT-DIAG TEST-ONLY — REVERTIR ANTES DE CERRAR B2` en MCPBridge.c
  y el campo `last_diag_s` en MCPMessages.c (y cualquier inicialización asociada).
- Verifica: grep `MCP-SEAT-DIAG` y `last_diag_s` → 0 ocurrencias. El bridge debe quedar igual que el fix de
  deadlines SIN la instrumentación. El PBO debe compilar.

## Paso 2 — R21-Codex del fix de deadlines (revisión a fondo, NO reimplementación)
Revisa el código del fix (plan §1) con criterio de severidad FAIL/WARN/NIT:
- **Correctitud de la migración:** ¿`m_ElapsedS += timeslice` se acumula en CADA OnTick (también pre-config)?
  ¿TODOS los deadlines/sample usan m_ElapsedS y segundos? grep R7: 0 ocurrencias de deadline_tick/
  prep_deadline_tick/sample_ticks.
- **Sample por tiempo:** ¿`sample_start_s` se fija al entrar a DRIVE y la condición de fin es
  `m_ElapsedS - sample_start_s >= sample_s_target`? ¿La validación de `duration` (segundos, 0..MAX_SAMPLE_S)
  es correcta sin Math.Round?
- **Edge cases:** timeslice=0 o muy grande; job creado antes de m_ElapsedS>0; drift de float en sesiones largas
  (¿relevante para un POC? marca como NIT si no).
- **No-regresión:** A1-A5 (query_player_state síncrono) no usan deadlines de job → confirma que siguen intactos.
  El wire format (MCPResult) no cambió (solo MCPJob, que no se serializa).
- **Honestidad del DATO B3:** ¿el probe mide lo que dice? (engine_on_server, speedo_max, pos_delta sobre la
  ventana de sampling real).

## Restricciones
- Enforce 1.29 sin ternario `?:`. NO toques mcp_server.py, A1-A5 de mcp_client.py, run-poc.ps1, run-step0.ps1.
- Paso 2 es REVISIÓN, no reimplementación: findings con severidad + path:line, NO reescribas el fix.
- NO re-corras el test in-game (B2/B3 ya validados); el compile post-revert basta como verificación de código.

## Output esperado (A/B/C/D)
### Bloque A — Archivos modificados por el revert (paths + tamaño).
### Bloque B — Verificación: grep MCP-SEAT-DIAG/last_diag_s = 0; grep R7 (deadline_tick/sample_ticks) = 0; compile OK.
### Bloque C — R21 del fix: matriz de hallazgos (ID, severidad, archivo:línea, resumen, resolución). Si limpio: "R21 sin hallazgos".
### Bloque D — Handoff: estado (fase 1 DONE?), pendiente (R21-Claude por consolidar), próximo (fase 2 Observación o fase client-peer).

===== PROMPT FIN =====
```

## Tras el revert+R21 (Claude)
Claude hace su mitad R21 independiente (lee el código del fix con la skill de revisión), consolida con la de
Codex, aplica findings FAIL/WARN. Si queda limpio → **fase 1 DONE** + handoff de sesión + lección LL (timeout
tick↔frecuencia) + decidir con el usuario el siguiente frente (fase 2 Observación vs fase client-peer para conducción).
