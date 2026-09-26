# Prompt Codex — sesión 16 — R22 plan-review del harness de fase 1

> Patrón: plan-review (R22). Generado por Claude 2026-06-07. Copiar de marker a marker.
> El usuario eligió "plan-review separado": Codex revisa ESTE plan del harness antes de implementarlo.

```
===== PROMPT INICIO =====

Tarea: R22 (doble revisión) del PLAN del harness de test in-game de la fase 1 de DayZ-MCP.
Revisas un plan de infraestructura de test; NO implementas nada, NO rediseñas, NO tocas el bridge
ni el código de fase 0. Salida = un archivo de review con veredicto + matriz de hallazgos.

CARGA INICIAL OBLIGATORIA (lee estos 7 archivos antes de nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-07-fase1-harness.md
   (EL PLAN A REVISAR — completo. Es infraestructura de test, no el bridge.)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (el PRODUCTOR: verifica que cada path:line y cada campo de MCPResult que el plan cita existe y
    se emite como dice — handlers world_spawn/vehicle_enter/drive_probe, ValidateSpawnArgs,
    EncodeNetworkMoveStrategy, constantes MAX_DISPATCH_PER_TICK/MAX_PENDING, bridge_queue_full.)
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (los DTO MCPArgs/MCPResult/MCPJob — shape exacto de args de entrada y campos de salida.)
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py
   (whitelist, MAX_QUEUE/429, set_poll_delay rango, manejo de args — el plan afirma qué errores
    son/ no son alcanzables vía HTTP; verifícalo.)
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py
   (lo que el plan EXTIENDE con el subcomando phase1; verifica que Client/await_result/
    set_poll_delay/enqueue existen como el plan asume y que añadir subparsers no rompe A1-A5.)
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-poc.ps1
   (el LAUNCH de fase 0 que run-fase1.ps1 clona; verifica que el clon propuesto es fiel —
    build PBO marker [MCP-POC], mission chernarus completa, init.c spawn fijo, server+client.)
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-07-fase1-control.md
   (el plan PADRE de fase 1, §"Test in-game agrupado (R5)": verifica que el harness cubre los
    escenarios que ese plan exige y respeta sus decisiones — B2 constante nombrada, B3 = probe.)

NO leas: el research consolidado, los R21 -claude/-codex separados, el product-spec completo,
ni el HANDOFF. El plan del harness y los 7 anclas de arriba bastan. NO abras run-step0.ps1 (su
marker [MCP-STEP0] está stale; el plan ya lo descarta).

DIMENSIONES DE LA REVISIÓN (ancla cada hallazgo a una sección §X del plan)

1) Consistencia interna: orden de pasos S0–S4, dependencias declaradas vs reales (B2 tras B1, B3
   tras B2), contradicciones entre secciones, referencias a archivos/líneas inexistentes.
2) Fidelidad del contrato (R2 cite-then-verify, PRIORITARIO): cada path:line y cada campo de
   MCPResult/MCPArgs que el plan cita, ¿existe y dice lo que el plan afirma? En particular:
   (a) ¿B1 emite type/found/pos_real? (b) ¿B2 emite seated/seat="driver"? (c) ¿B3 emite los 5
   campos del DATO? (d) ¿los errores listados como "alcanzables/no alcanzables" son correctos?
   (e) ¿es cierto que bad_args (args==null) NO es alcanzable vía el server HTTP?
3) Cobertura R5: ¿el harness cubre los 4 escenarios del plan padre §"Test in-game agrupado"
   (B1+negativos, B2, B3-probe, backpressure)? ¿Falta alguno o algún sub-caso?
4) Determinismo del backpressure (S4): ¿set_poll_delay(5000)+cebo+ráfaga realmente fuerza el 429
   server-side? ¿La verificación del cap ≤4/tick agrupando por tick_dispatch es válida (query_player_state
   es síncrono, no diferido)? ¿La señal bridge_queue_full prueba m_Pending acotado? ¿Hay race que
   produzca falso PASS o falso FAIL? Propón mitigación si la ves frágil.
5) Semántica del gate: ¿overall_pass EXCLUYE correctamente B3 (LL-116: B3 es DATO, no criterio)?
   ¿B3 se reporta sin pass/fail y con interpretación condicionada a vehicle_fixture_ready==true?
6) No-regresión fase 0: ¿la extensión de mcp_client.py (subparsers) y el nuevo run-fase1.ps1
   dejan intactos A1-A5 y run-poc.ps1? Cualquier toque a lo certificado es FAIL.
7) Escena determinista: ¿anclar el spawn del coche a query_player_state (S0) neutraliza el drift
   ~9 m? ¿offset 5 m + uso de pos_real son compatibles con el radio de búsqueda 4 de FindTransportNear?
8) Fallbacks y [verify]: ¿la fallback list de clases de coche es correcta (todas extends CarScript,
   spawneables)? ¿El manejo de OnDebugSpawn no-efectivo (fixture_ready=false → inconclusive) es
   correcto? ¿Algún [verify] del plan debería resolverse ANTES del run para no quemarlo?

CRITERIO DE SEVERIDAD (operativo, no estético)
- FAIL bloqueante: defecto que invalida el run si no se resuelve antes (contrato mal citado →
  el harness parsea un campo que no existe; método de backpressure que nunca dispara el 429; gate
  que cuenta B3; clase de coche citada que no existe; toque a A1-A5/run-poc.ps1).
- WARN mayor: retrabajo significativo (race de timing S4 no mitigado; criterio no verificable
  desde el verdict; cobertura R5 incompleta; [verify] crítico no señalado).
- NIT: mejora opcional (claridad, naming, ejemplo).

NO INTRODUZCAS DISEÑO NUEVO. Revisas el plan existente, no escribes un plan alternativo. Si crees
que el harness debe replantearse de raíz, dilo en el resumen ejecutivo, pero no redactes otro plan.

RESTRICCIONES (de tu AGENTS.md, aplican a esta sesión)
- R2 cite-then-verify obligatorio en cada hallazgo: cita §sección del plan + el path:line real del
  código si contradice una cita del plan. Sin cita, el hallazgo no es accionable.
- R22: review con veredicto, no rediseño. Severidad con el criterio de arriba (sin criterio,
  no infles severidades).
- NO ejecutes el harness, NO buildees PBO, NO lances DayZ (el plan aún no está implementado).
- Stack de referencia: cliente = Python stdlib only; orquestador = PowerShell 5.1. Si el plan
  propone una dep nueva, es WARN.

OUTPUT ESPERADO

Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-07-plan-review-harness-codex.md
(NO sobrescribas 2026-06-07-plan-review-codex.md, que es el de fase 1.)

Estructura del archivo:
### Resumen ejecutivo
- Veredicto: approve / approve with minor changes / reject.
- 3-5 líneas de justificación.
### Matriz de hallazgos
| ID | Sección plan | Severidad | Resumen | Resolución sugerida |
(IDs con prefijo HR22-NNN para no colisionar con los R22-NNN del plan de fase 1.)
### Hallazgos detallados
Por ID: §sección + cita del plan (1-3 líneas) + problema + propuesta + path:line del código si aplica.
### Cobertura
- Escenarios R5 cubiertos / no cubiertos.
- Citas del contrato verificadas / refutadas (lista con path:line).
### Próximo paso
- approve → implementación puede empezar; approve-with-minor → Claude aplica FAIL+WARN y reenvía;
  reject → nueva pasada de plan.

Cuando termines, añade los hallazgos (cross-link) a:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md
(append con timestamp y status=open).

Y en tu RESPUESTA en chat pega: el veredicto + la matriz de hallazgos completa (para que el usuario
me la traiga sin abrir el archivo).

===== PROMPT FIN =====
```

## Tras la review (receptor, Claude)
El usuario pega el veredicto + matriz. Claude verifica host-direct cada hallazgo HR22-NNN contra el
código real (Pattern 2 — no se fía del resumen de Codex), aplica FAIL+WARN al plan del harness, y si
queda approve, escribe el prompt de implementación (sesión 17).
