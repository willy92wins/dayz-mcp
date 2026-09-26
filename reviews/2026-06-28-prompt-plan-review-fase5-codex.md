# Prompt R22 (plan-review Codex) — Fase 5 drivability autónoma

> Generado 2026-06-28 (Claude builder). Pegar de marcador a marcador en la terminal de Codex CLI.
> Patrón: plan-review (R22). Output canónico: `reviews\2026-06-28-plan-review-fase5-codex.md`.

```
===== PROMPT INICIO =====

Tarea: R22 (doble revisión) del plan de Fase 5 de DayZ-MCP ("drivability autónoma + diagnóstico
get-in"). Revisas el plan ANTES de implementar. NO implementes nada, NO reescribas el plan: review
con veredicto + matriz de hallazgos. R22 de tu AGENTS.md aplica.

CARGA INICIAL OBLIGATORIA (rutas absolutas; léelas antes de tocar nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-28-fase5-drivability-autonoma.md
   (el plan a revisar — completo, incluida la tabla de citas §11 y los gates).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md
   (diseño base: 3 actores CONTROL/CAPTURE/host, tool surface, §2 APIs de vehículo verificadas;
   el plan extiende esto — verifica coherencia).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-10-fase4-mcp.md
   (decisión G-5 línea 43: vehicle_drive NO se expone porque throttle server-side no mueve
   PHYSICS = "B3 client-auth, fase futura". El plan F5 ES esa fase — verifica que no contradice
   las decisiones G-1..G-7 ni el broker D-14).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\decisions\decision-log.md
   (ADRs/D-NN del proyecto, incl. broker/daemon D-14 y B3; verifica que el plan los respeta).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (contrato de "terminado"; el plan propone un criterio E5 nuevo en §10 — verifica el trazado
   DPF y que nada del plan contradiga el spec vigente).
6. El bridge Enforce que el plan modifica, en
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\ :
   MCPBridge.c (server), MCPClientBridge.c (cliente), MCPMessages.c (DTOs).
   (Verifica las citas §0/§11 del plan: dispatch else-if, drive_probe, DTOs reutilizados.)
7. Para la dimensión de citas R2, usa la tabla §11 del plan como índice y SPOT-CHECKEA las
   firmas vanilla bajo C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\ :
   vehicles\car.c, entities\vehicles\carscript.c (esp. IsServerOrOwner ~:3220),
   vehicles\transport.c, classes\useractionscomponent\actions\interact\actiongetintransport.c,
   entities\object.c, entities\pawn.c.

NO leas los reviews de fases anteriores ni los handoffs de sesión salvo que necesites verificar
un contrato concreto que el plan cite. NO abras el código Python del server (dayz_mcp\) salvo
para verificar un contrato que el plan afirme.

DIMENSIONES DE LA REVISIÓN

Las 8 genéricas (consistencia interna; vs architecture/decisiones/product-spec; APIs y citas R2;
cobertura de BUGs activos —BUG-009 autoconexión cliente, BUG-010/011/012 telemetry—; patrones;
testing con fixture +/-; orden de implementación). Además, ESPECÍFICAS de este plan:

A) **Hipótesis central (§2)**: ¿`carscript.c` IsServerOrOwner (~:3220) realmente respalda que
   conducir desde el peer CLIENTE (owner) mueva un coche NetworkMoveStrategy.PHYSICS? ¿El plan la
   trata correctamente como HIPÓTESIS no verificada gateada tras S0, o la da por hecha en algún
   punto? Señala si el gate S0 (STOP si pos_delta≈0, no construir Tramo A) es sólido.
B) **Owner real del peer MCP (§2, riesgo #1)**: ¿hay evidencia en el bridge/architecture de que
   el cliente que conduce la MCP es el OWNER del coche tras `vehicle_enter` (que se ejecuta en el
   SERVER)? Si el ownership no se transfiere al cliente MCP, S0 fallaría por diseño — ¿el plan lo
   contempla?
C) **Motor de control sostenido (§3.1)**: reaplicar throttle/steer cada OnTick + interacción con
   `SuppressGameplay`/HID. ¿Riesgo de que el held-state pise input o reaplique a coche equivocado?
   ¿El auto-clear cubre los casos?
D) **Cambios de DTO (§3.2)**: el rename `engine_on_server`→`engine_on` y su impacto R7 (todos los
   call-sites). ¿El plan lo gestiona o lo deja ambiguo?
E) **query_get_in_condition (§4)**: ¿la decomposición de los 7 gates refleja FIELMENTE
   ActionGetInTransport.ActionCondition (actiongetintransport.c:26-80)? ¿La ambigüedad
   "component dado vs iterar crew" está bien resuelta? ¿El fixture de regresión SUB_BRZ (gate B)
   es válido?
F) **Modularidad vs broker (D-14)**: los verbos de control van al peer cliente; ¿son compatibles
   con el modo broker/daemon (--client) sin pelear por el puerto, igual que camera_*?

CRITERIO DE SEVERIDAD

- FAIL bloqueante: invalida la implementación si no se resuelve antes (API inexistente, cita
  errónea, contradicción con ADR/architecture/product-spec, hipótesis central insostenible no
  gateada, dependencia circular).
- WARN mayor: retrabajo significativo si se ignora (testing insuficiente, gate ambiguo, DTO sin
  estrategia de migración, cobertura de BUG ausente).
- NIT: mejora opcional (claridad, redacción).

NO INTRODUZCAS HALLAZGOS NUEVOS DE DISEÑO. Si crees que el plan completo debe replantearse (p.ej.
si la hipótesis owner es insostenible), dilo en el resumen ejecutivo, pero NO escribas un plan
alternativo. Cada hallazgo cita la sección del plan (ej. "§3.1" o "§11 fila car.c").

OUTPUT ESPERADO

Archivo único:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-28-plan-review-fase5-codex.md

Estructura:

### Resumen ejecutivo
- Veredicto: approve / approve with minor changes / reject.
- 3-5 líneas de justificación (incl. tu lectura de si la hipótesis central está bien gateada).

### Matriz de hallazgos
| ID | Sección plan | Severidad | Resumen | Resolución sugerida |
|---|---|---|---|---|
| R22F5-001 | §2 | FAIL | ... | ... |

### Hallazgos detallados
Por cada ID: sección, cita exacta del plan (1-3 líneas), problema, propuesta, ref a
architecture/decisión/citas si aplica.

### Cobertura
- BUGs activos del ledger (009/010/011/012): cubiertos / no cubiertos (cada no-cubierto = WARN o FAIL).
- Decisiones G-1..G-7 / D-14 / B3: respetadas / desviadas (cada desvío justificado o no).
- Citas §11 spot-checkeadas: cuáles confirmaste, cuáles divergen.

### Próximo paso
- approve → implementación de S0 puede empezar.
- approve with minor → Claude aplica FAIL+WARN y reenvía.
- reject → nueva pasada de planificación.

Al terminar, append con timestamp + status=open al canal de revisión (créalo si no existe):
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md

===== PROMPT FIN =====
```
