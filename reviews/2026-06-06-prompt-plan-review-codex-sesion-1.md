# Prompt — R22 plan-review POC fase 0 (DayZ-MCP) · Codex sesión 1

Copiar de marcador a marcador en el CLI de Codex.

```
===== PROMPT INICIO =====

Tarea: R22 (doble revisión) del plan de la **fase 0 (POC)** del proyecto DayZ-MCP. Revisas el
plan ANTES de implementar. Devuelves una review con veredicto + matriz de hallazgos. NO reescribes
el plan ni implementas nada en esta sesión.

## Carga inicial obligatoria (lee antes de tocar nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-06-poc-fase-0-roundtrip.md
   (el plan a revisar — completo, incluida la §11 de verificación de APIs).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (la Definición de Producto Final; verifica que cada parte del POC trace a un criterio A1-A5).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md
   (diseño del que sale el plan; verifica que el plan lo respeta. OJO: el plan corrige a propósito
   el §7 del doc —la key va en query string, no en header Authorization—; eso es intencional).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md
   (índice de APIs Enforce con path:line; úsalo como pista, NO como verdad — verifica en el source).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\MCPTest\config.cpp
   (el CfgMods/missionScriptModule real sobre el que el plan modela el config.cpp del bridge).

Para R2 (cite-then-verify): el **source vanilla** está bajo
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\  (1_core, 3_game, 4_world, 5_mission).
   Cada path:line de la §11 del plan se verifica AHÍ, abriendo el archivo en la línea citada.

NO hay research consolidado aparte, ni ADRs, ni bug-ledger todavía (proyecto recién scaffoldeado):
el architecture doc ES el research consolidado. No busques esos archivos.

## Dimensiones de la revisión

1) **Consistencia interna**: numeración de pasos, dependencias declaradas vs implícitas,
   contradicciones entre secciones, referencias a §/archivos que no existen.
2) **Trace al product-spec (gate DPF)**: ¿cada componente del POC (3a-3d) y cada paso (§9) traza a
   un criterio A1-A5? ¿Algún criterio A1-A5 queda sin cubrir por el plan?
3) **Consistencia con el architecture doc**: ¿el plan respeta el diseño (3 actores, transporte
   pull async, server-authoritative)? Desvíos justificados (p.ej. el §7 header→query string) OK;
   desvíos NO justificados = hallazgo.
4) **APIs y citas (R2)**: cada path:line de la §11 del plan, ¿existe en el source vanilla con esa
   firma? En particular verifica: restapi.c GET/POST/SetHeader/SetOption + ERESTOPTION_*;
   missionserver.c OnUpdate/TickScheduler; game.c GetPlayers; object.c GetPosition; gameplay.c
   JsonSerializer; jsonfileloader.c JsonLoadFile. Reporta cualquier firma que no case.
5) **Correctitud del lazo async (lo crítico del POC)**: ¿el pseudocódigo del MCPBridge garantiza
   no-bloqueo (solo GET/POST async, nunca *_now en el tick)? ¿El single-in-flight + backoff están
   bien planteados? ¿El lifetime de RestCallback (Managed/GC) está cubierto o es un agujero real?
6) **Seguridad fail-closed (R6)**: ¿bind 127.0.0.1 + assert, key 401, whitelist, no-logging de URL
   son suficientes y consistentes? ¿La key-en-query-string tiene mitigación adecuada para un POC?
7) **Prueba de no-bloqueo (A2)**: ¿el método tick-counter + base_rate/load_rate evidencia
   realmente el no-bloqueo, o tiene un hueco lógico (p.ej. el tick avanza igual aunque REST
   bloquee otra cosa)? ¿El umbral 0.8·base_rate es defendible?
8) **Gate de descubrimiento (Step 0)**: ¿está bien que Step 0 sea gate antes de 3a-3d? ¿La tabla
   de riesgos §8 cubre los supuestos server-side reales? ¿El fallback client-first es viable sin
   rehacer el transporte?
9) **Testing y "done"**: ¿cada criterio A1-A5 tiene una comprobación concreta y testable en §3c/§10?
   ¿Falta algún fixture/medida (p.ej. caso negativo de seguridad, RPT limpio)?
10) **Orden de implementación (§9)**: ¿las dependencias permiten el orden propuesto? ¿Hay ciclos?

## Criterio de severidad

- **FAIL (bloqueante)**: invalida la implementación si no se resuelve antes (API inexistente o
  firma incorrecta, no-bloqueo no garantizado por el diseño, agujero de seguridad, contradicción
  interna que impide implementar, criterio A1-A5 imposible de cumplir con el plan).
- **WARN (mayor)**: retrabajo significativo si se ignora (cobertura de test insuficiente, riesgo
  no listado, decisión de diseño frágil pero no fatal, lifetime de callback no resuelto).
- **NIT**: mejora opcional (claridad, naming, ejemplo extra).

## Restricciones críticas

1. **NO redesign**. Revisas el plan existente; no escribes un plan alternativo. Si crees que hay que
   replantearlo entero, dilo en el resumen ejecutivo en 3-5 líneas, sin redactar el plan nuevo.
2. **NO implementes** nada (ni el mod, ni el Python, ni el ps1). Esta sesión es solo review.
3. **R2 / R22** de tu AGENTS.md aplican explícitamente. Cada hallazgo cita la §/línea del plan
   (p.ej. "§3a MCPBridge.OnTick" o "§11 fila RestApi"). Sin cita, el hallazgo no es accionable.
4. **Severidad operativa**, con el criterio de arriba. No infles severidades.
5. Recuerda que el plan corrige el §7 del architecture doc a propósito (key en query string). Eso
   NO es un hallazgo; el hallazgo sería si la corrección estuviera mal aplicada.

## Output esperado

Archivo único:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-06-plan-review-codex.md

Estructura:

### Resumen ejecutivo
- Veredicto: approve / approve with minor changes / reject.
- 3-5 líneas de justificación.

### Matriz de hallazgos
| ID | Sección plan | Severidad | Resumen | Resolución sugerida |
|---|---|---|---|---|
| R22-001 | §3a | FAIL | <one-liner> | <one-liner> |

### Hallazgos detallados
Por cada ID: sección + cita exacta del plan (1-3 líneas), problema, propuesta de resolución, y la
referencia al architecture doc / source vanilla / product-spec si aplica.

### Cobertura
- Criterios A1-A5 cubiertos por el plan: lista.
- Criterios A1-A5 NO cubiertos o débiles: lista (cada uno = WARN o FAIL).
- APIs de §11 verificadas OK: lista. APIs con firma incorrecta/inexistente: lista (cada una = FAIL).

### Próximo paso
- approve → implementación (Codex) puede empezar por Step 0.
- approve with minor → Claude aplica FAIL+WARN, re-envía.
- reject → nueva pasada de plan.

Al terminar, añade una fila al inbox con timestamp y status=open:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md

===== PROMPT FIN =====
```

## Notas para Claude (receptor, no para Codex)

Cuando Codex devuelva, parsear (skill `codex-handoff-template` §receptor):
- Verificar que `2026-06-06-plan-review-codex.md` existe (Read host-direct).
- Cada FAIL/WARN → aplicar al plan (v2 con changelog post-R22) o rechazar con el usuario.
- Re-verificar cualquier path:line que Codex marque como incorrecto, contra el source (no fiarse).
