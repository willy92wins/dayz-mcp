# R22 plan-review — Fase 5 drivability autónoma (Codex)

> Recibido 2026-06-28. Codex devolvió la review inline (no escribió este archivo); persistido
> por el receptor (Claude). Preflight Codex: solo lectura; plan + architecture + F4 + decision-log
> + product-spec + bridge Enforce + spot-check vanilla. No corrió tests (review de plan).

## Resumen ejecutivo

**Veredicto: NEEDS-WORK** (no implementar Fase 5 todavía). S0 bien planteado como gate de
hipótesis, pero 3 bloqueos de plan: (1) DPF se quiere cerrar después de implementar;
(2) `query_get_in_condition` puede dar falsos verdes sin `componentIndex`; (3) el held-state de
conducción no tiene release/deadman cerrado.

Respuestas a las dimensiones dirigidas:
- **A/B (hipótesis owner)**: `IsServerOrOwner()` respalda la hipótesis COMO hipótesis, no como
  certeza. El plan la gatea bien con S0, pero S0 debería instrumentar ownership directo. NO hay
  evidencia en el bridge de que `vehicle_enter` server transfiera ownership al cliente MCP — S0 lo
  cubre conductualmente, no diagnósticamente.
- **C/D/E/F**: Tramo A necesita release/deadman + identidad de coche; el rename DTO debe cerrarse;
  `query_get_in_condition` no es fiel sin componente; broker D-14 es compatible con peer cliente +
  lock, PERO no con held-state indefinido sin lease/TTL.

## Matriz de hallazgos

| ID | Sev | Hallazgo | Evidencia | Fix sugerido | Receptor |
|---|---|---|---|---|---|
| F5-001 | P1 | Gate DPF no cerrado antes de implementar (E5 al cierre) | plan:65, plan:277, product-spec:47 | Adjudicar antes: reabrir B3 o añadir criterio en product-spec antes de S0 | **ACCEPT** — adjudicado: añadir grupo G (E5) |
| F5-002 | P1 | `query_get_in_condition` no replica el radial sin `componentIndex` (vanilla decide crew desde el componente del cursor) | actiongetintransport.c:50, plan:168 | `component` obligatorio para available/first_block exactos; sin él, diagnóstico parcial, no PASS | **ACCEPT** |
| F5-003 | P1 | Held-state menciona `vehicle_release` pero no está en surface ni gates; sin TTL/deadman | plan:123, plan:109, daemon.py:9 | Añadir `vehicle_release` + max-hold/deadman gateado, o `vehicle_control` bounded por duración | **ACCEPT** |
| F5-004 | P2 | Re-resolver el coche cada tick no evita aplicar control a otro coche si el player cambia de vehículo | plan:124, human.c:694 | Fijar identidad del vehículo al activar + auto-clear si cambia | **ACCEPT** |
| F5-005 | P2 | Rename `engine_on_server` queda como decisión de Codex, no plan cerrado | plan:135, MCPMessages.c:214 | Cerrar en plan: default no rename en F5, documentar alias; rename en plan separado | **ACCEPT** |
| F5-006 | P2 | S0 sólido como STOP/PASS pero no recoge evidencia directa de ownership | carscript.c:3222, pawn.c:193, MCPBridge.c:464 | S0 reporta `IsOwner()`/`GetOwnerIdentity()`/net id además de pos_delta/net_strategy | **ACCEPT** — APIs confirmadas (pawn.c:194/209, object.c:815) |
| F5-007 | P2 | Negativos fail-closed incompletos para floats nuevos; BUG-011 ya documenta NaN/Inf | plan:240, bug-ledger:18, MCPClientBridge.c:915 | Validación finita/rango para throttle/steer/brake/handbrake + fixtures NaN/Inf | **ACCEPT** |
| F5-008 | P2 | Fixture SUB_BRZ subespecificado: CarScript pelado puede fallar antes en componentNN, no en CrewCanGetThrough | transport.c:493, actiongetintransport.c:50, plan:244 | Definir fixture con componente válido + asiento mapeado; solo entonces esperar crew_can_get_through | **ACCEPT** |
| F5-009 | P2 | La escalera usa cámara antes de get-in/drive, pero `camera_set` suprime controles y freecam deshabilita simulación | plan:212, MCPClientBridge.c:562, MCPClientBridge.c:627 | Gatear restore/neutralización de cámara antes de get-in/drive, o prohibir freecam en esa escalera | **ACCEPT** |

## Receptor (Claude, 2026-06-28)

Los 9 hallazgos verificados contra plan + código; **9/9 válidos, 0 rechazados**. F5-001 adjudicado
con el usuario (AskUserQuestion): añadir grupo **G** (drivability fase 5) al product-spec antes de
S0. APIs de F5-006 confirmadas en source (`IsOwner` pawn.c:194, `GetOwnerIdentity` pawn.c:209,
`GetNetworkID` object.c:815). Todos se aplican al plan **v2**; round-2 = verificación de cierre.
