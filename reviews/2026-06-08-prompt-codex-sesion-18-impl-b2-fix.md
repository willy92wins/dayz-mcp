# Prompt Codex — sesión 18 — fix B2 (deadlines por tiempo) + instrumentación + re-run

> Patrón: implementation-handoff con R22-corta embebida (Codex critica el plan en el paso 0). R21 del
> código va en sesión aparte tras el run. Generado por Claude 2026-06-08. Copiar de marker a marker.

```
===== PROMPT INICIO =====

Tarea: arreglar el timeout de B2 (vehicle_enter) en el bridge de DayZ-MCP convirtiendo los deadlines de
ticks a TIEMPO de pared, añadir instrumentación diagnóstica TEMPORAL (autorizada) al job de seat, ajustar
el harness, y RE-CORRER el test in-game. Esta sesión cubre **únicamente** el plan del fix v1. NO rediseñes
el probe B3, NO toques el server Python ni A1-A5, NO reviertas la instrumentación (se revierte al cerrar B2).

## Paso 0 — R22-corta del plan (ANTES de codear)
Lee el plan y critícalo: ¿están TODOS los call-sites de deadline_tick (R7)? ¿las firmas que usa la
instrumentación existen (HumanCommandVehicle.IsGettingIn/GetVehicleSeat, vector.Distance)? ¿la unidad de
timeslice es segundos? Si encuentras un FAIL bloqueante, PÁRATE y repórtalo en Bloque C sin implementar.
Si solo hay WARN/NIT, anótalos y procede.

## Carga inicial obligatoria (lee antes de tocar nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-08-fase1-b2-timeout-fix.md
   (EL PLAN v1 — vinculante. Implementa exactamente esto. Tiene la tabla de call-sites §1.4 y la evidencia.)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (lo que EDITAS: m_ElapsedS, constantes en s, migrar deadlines, instrumentación SEAT-DIAG.)
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (lo que EDITAS: MCPJob deadline_tick/sample_ticks → float deadline_s/sample_start_s/sample_s_target.)
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py
   (EDITAS SOLO el modo phase1: offset coche 5→2 m, B3 duration en segundos 2.0. NO toques A1-A5.)
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-fase1.ps1
   (lo CORRES; verifica que vuelca los markers [MCP-POC] incl. SEAT-DIAG. -DayZPort 2402 default.)
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_fase1\run_20260608_002308\server_profiles\script_2026-06-08_00-23-15.log
   (contexto: el run fallido — `job queued id=16 kind=seat` → timeout; gap set_poll_delay confirma ~6000 Hz.)

NO leas: research, R21 -claude/-codex, product-spec, HANDOFF, run-poc.ps1 (no se toca), run-step0.ps1.

## Alcance acotado — plan v1 partes 1-3

### Paso 1 — Deadlines por tiempo (MCPBridge.c + MCPMessages.c), plan §1
- `m_ElapsedS` (float, init 0) acumulado en OnTick: `m_ElapsedS = m_ElapsedS + timeslice;` (1ª línea tras m_Tick++).
- Constantes ticks→segundos (plan §1.2): JOB_TIMEOUT_S=5.0, DRIVE_PROBE_PREP_TIMEOUT_S=5.0,
  DRIVE_PROBE_TIMEOUT_S=12.0, DRIVE_PROBE_DEFAULT_SAMPLE_S=2.0, DRIVE_PROBE_MAX_SAMPLE_S=5.0.
- MCPJob: deadline_tick→deadline_s, prep_deadline_tick→prep_deadline_s, sample_ticks/sample_ticks_target→
  sample_start_s/sample_s_target (todos float). (MCPJob NO se serializa → no toca el wire.)
- Migra los 8 call-sites de la tabla §1.4. duration del probe validado en SEGUNDOS (0..DRIVE_PROBE_MAX_SAMPLE_S),
  sin Math.Round. Sample: `if (m_ElapsedS - job.sample_start_s >= job.sample_s_target)`.
- **R7:** al terminar, grep en MCPBridge.c+MCPMessages.c → 0 ocurrencias de deadline_tick/prep_deadline_tick/
  sample_ticks. Los tick_poll_sent/callback/dispatch (en MCPResult) SE QUEDAN (diagnóstico, no deadlines).

### Paso 2 — Instrumentación SEAT-DIAG TEMPORAL (plan §2, AUTORIZADA por el usuario 2026-06-08)
- Anchor OBLIGATORIO en cada bloque: `// MCP-SEAT-DIAG TEST-ONLY — REVERTIR ANTES DE CERRAR B2`.
- En ProcessJobs, para job.kind=="seat", throttle 0.5 s (campo TEST-ONLY `float last_diag_s`): loggear
  `MCP-SEAT-DIAG id=<> getting_in=<> seat=<> dist=<> elapsed_s=<>` con if/else (Enforce SIN ternario ?:).
- NO la reviertas en esta sesión (se revierte al cerrar B2).

### Paso 3 — Harness (mcp_client.py modo phase1), plan §3
- Offset del coche 5.0 → 2.0 m. B3 vehicle_drive `duration` = 2.0 (SEGUNDOS). Timeouts cliente B2≥15s, B3≥30s.
- NO toques A1-A5 ni el resto del modo phase1.

### Paso 4 — Verificación offline + RE-RUN in-game
- Offline: `python -m py_compile mcp_client.py`; grep R7 limpio; (PBO compila en el build del run).
- Corre `run-fase1.ps1`. Recolecta fase1-verdict.json + GATE + markers [MCP-POC] (incl. SEAT-DIAG) + RPT.

## Restricciones críticas (vinculantes)
1. **Enforce 1.29 SIN ternario `?:`** (mordió en fase 0) → if/else en la instrumentación. Cliente=Python stdlib;
   orquestador=PowerShell 5.1.
2. **NO toques:** mcp_server.py, la lógica A1-A5 de mcp_client.py, run-poc.ps1, run-step0.ps1. EDITAS:
   MCPBridge.c, MCPMessages.c, mcp_client.py (solo offset+duration del modo phase1).
3. **R7 obligatorio:** migra TODOS los call-sites; grep 0 deadline_tick/sample_ticks al cerrar.
4. **Instrumentación TEST-ONLY con anchor; NO revertir ahora.** Está autorizada; no preguntes de nuevo.
5. **B3 es DATO, NO pass/fail (LL-116):** overall_pass excluye B3; fixture_ready=false → inconclusive.
6. **NO improvises fuera del plan:** si un call-site/firma no encaja, R22-corta (Bloque C), interpretación
   conservadora, NO rediseñes el probe.
7. **NO R21/self-review del código en esta sesión** (va aparte tras el run). Implementa, compila, corre, reporta.
8. Entorno: -DayZPort 2402; si hay otra instancia DayZDiag (p.ej. LFQuad) corriendo, deténla antes del run.

## Output esperado (pega en chat + deja artefactos en disco)
### Bloque A — Archivos modificados (paths + tamaño): MCPBridge.c, MCPMessages.c, mcp_client.py; verdict generado.
### Bloque B — Verificación:
- py_compile + grep R7 (0 deadline_tick/sample_ticks) literal.
- GATE=PASS/FAIL del run + **fase1-verdict.json LITERAL** + bloque **B3 DECISION DATA** (si B2 pasó).
- Si B2 falló: las líneas **MCP-SEAT-DIAG** del log (getting_in/seat/dist/elapsed_s).
### Bloque C — Hallazgos (R22-corta del paso 0 + lo que no encajó del plan, con path:line). Si nada: "Sin hallazgos".
### Bloque D — Handoff: estado (B2 pasó? B3 DATO recolectado? o SEAT-DIAG dice qué falló), próximo paso
  (Claude analiza host-direct → decide B3 / o diagnostica B2 con el SEAT-DIAG), recordatorio: revertir
  la instrumentación al cerrar B2 + R21 del código pendiente.

===== PROMPT FIN =====
```

## Tras el run (receptor, Claude)
Claude verifica host-direct: lee fase1-verdict.json + markers [MCP-POC]/SEAT-DIAG en el RPT real, confirma
B2 (seated/driver) y el grep R7. Si B2 pasa y B3 trae DATO → decide B3 con el usuario. Si B2 falla → el
SEAT-DIAG dice si es proximidad u otra causa. Pendiente al cerrar B2: revertir instrumentación + R21 del código.
