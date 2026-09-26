# Prompt R22 — plan review Fase 4 (MCP stdio) — para Codex CLI

> Generado 2026-06-10 (sesión planning Fase 4). Patrón: plan-review (codex-handoff-template).
> Pegar tal cual en Codex CLI. El receptor (Claude) parsea el output contra este prompt.

===== PROMPT INICIO =====

Tarea: R22 (revisión de plan) del plan de Fase 4 (MCP stdio) de DayZ-MCP. SOLO review con
veredicto — NO implementación, NO redesign.

CARGA INICIAL OBLIGATORIA (en este orden)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-10-fase4-mcp.md
   (el plan a revisar — completo).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (contrato DPF — grupo E = E1-E4 es lo que la fase debe cumplir; §Changelog = alcance vivo).
3. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-10-fase4-mcp-stdio.md
   (research fase 0 de lo NUEVO: SDK mcp 1.27.2 smoke-verificado + firmas Enforce de 4B; el
   plan debe respetarlo).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\decisions\decision-log.md
   (D-01..D-13 — el plan no puede contradecir decisiones cerradas).
5. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md
   (APIs verificadas; el plan no debe usar APIs fuera de aquí + el research del punto 3).
6. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md
   (BUGs activos BUG-009..012: ¿el plan los considera o declara carril aparte?).
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py
   (loopback actual que el plan refactoriza a lib con back-compat dura — verifica que el
   contrato que el plan promete conservar es el real).

BOUNDARIES

- NO releas el research de Fase 3 ni los research -claude/-codex separados.
- NO leas las reviews in-game de D1/D2 (reviews/2026-06-10-fase3-d1-ingame.md, -d2-ingame.md).
- NO implementes nada; NO toques ningún archivo del repo. Tus únicos writes: el archivo de
  output del review + el append al inbox (ver OUTPUT).
- NO redesign: findings sobre el plan existente, no un plan alternativo. Si crees que debe
  replantearse entero, dilo en el resumen ejecutivo con argumento, sin escribir el plan B.
- Las 7 adjudicaciones del usuario (G-1..G-7, plan §1) y las invariantes cerradas (fases 0-3
  PASS in-game, D-09..D-13, CONFLICT-1/2) NO se re-litigan: verifica que el plan las RESPETA,
  no si son correctas.

CONTEXTO DE VERIFICACIÓN (R2)

- Las firmas del SDK mcp (FastMCP/Image/ToolError/CallToolResult, concurrencia start_soon,
  logging a stderr) están verificadas por smoke install real 2026-06-10 (research punto 3;
  el venv temporal ya fue borrado). No necesitas reinstalar para aceptarlas; si quieres
  re-verificar, crea tu propio venv temporal.
- Las citas Enforce del plan (world.c, weather.c, game.c, restapi.c) verifícalas contra el
  source local: C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\.
- mcp_capture.py y run-fase3.ps1 puedes LEERLOS para verificar las interfaces citadas
  (mcp_capture.py:279, run-fase3.ps1:256) — READ-ONLY para ti.

DIMENSIONES DE LA REVISIÓN

1) Consistencia interna: dependencias 4A→4B, contradicciones entre secciones, referencias a
   archivos/líneas inexistentes.
2) Consistencia con el research (punto 3): ¿el plan respeta los hallazgos (stdout prohibido
   en el proceso stdio, mutex E4 obligatorio, pin mcp==1.27.2, tolerancia legacy del
   handshake)?
3) Consistencia con decision-log + adjudicaciones G-1..G-7: ¿alguna sección las contradice?
4) Citas y APIs (R2): cada path:line del plan existe y dice lo que el plan afirma.
5) Trazado DPF (tu R29): ¿cada pieza traza a E1-E4? ¿Los drafts de Changelog (plan §9)
   cubren TODOS los desvíos del product-spec, sin dejar ninguno implícito?
6) Cobertura de BUGs activos (BUG-009..012): ¿tratados o declarados fuera con razón?
7) Gates verificables: ¿cada criterio de §8 es testable con fixture positivo Y negativo?
   ¿Falta algún negativo crítico (auth, args inválidos, peer caído, imagen sobre presupuesto)?
8) Riesgo técnico específico: el puente asyncio (FastMCP) ↔ thread (loopback) del §3.3 —
   busca huecos de diseño (deadlock, starvation, lifecycle del thread cuando stdio muere,
   cleanup en shutdown, señales en Windows). Es el [DESIGN] de más riesgo del plan.
9) Back-compat: ¿lo que el plan promete conservar (shim CLI --port/--keyfile, endpoints
   /enqueue /await /poll /result, run-fase3.ps1, tools/tests/) es suficiente y verificable?

CRITERIO DE SEVERIDAD

- FAIL bloqueante: invalida la implementación si no se resuelve antes (API inexistente,
  contradicción con decisión cerrada, dependencia circular 4A/4B, hueco de seguridad
  fail-open).
- WARN mayor: produce retrabajo significativo si se ignora (gate sin negativo, riesgo sin
  mitigación, back-compat incompleta).
- NIT: mejora opcional.

OUTPUT ESPERADO

Archivo único:
C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\reviews\2026-06-10-plan-review-fase4-codex.md

Estructura:

### Resumen ejecutivo
- Veredicto: approve / approve with minor changes / reject. + 3-5 líneas de justificación.

### Matriz de hallazgos
| ID | Sección plan | Severidad | Resumen | Resolución sugerida |
(IDs con prefijo R22-F4-001, R22-F4-002, …)

### Hallazgos detallados
Por ID: sección + cita exacta del plan (1-3 líneas), problema, propuesta de resolución,
referencia a decisión/research/bug si aplica.

### Cobertura
- BUGs cubiertos / NO cubiertos (cada NO cubierto = WARN o FAIL según gravedad).
- Decisiones (D-01..D-13, G-1..G-7) respetadas / desviadas (cada desvío: justificado o no).

### Próximo paso
- APPROVE → la implementación 4A puede empezar.
- APPROVE WITH MINOR → Claude aplica los FAIL+WARN y emite plan v2.
- REJECT → nueva pasada de planificación.

Al terminar, append al inbox con timestamp y status=open:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md

===== PROMPT FIN =====
